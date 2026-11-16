#!/usr/bin/env python3
"""Host-native request selection: classifier only; host owns task execution.

Adapters apply model/effort to an already-authorized request in the SAME host.
This does not enable the separate isolated delegate.py subscription transport.
"""
from __future__ import annotations

import argparse
import copy
import json
import sys

import adaptive_execution as A
import ledger as L
import route_request as R

FAMILIES = {'codex': 'openai', 'orca': 'openai', 'claude-code': 'claude'}


def host_settings(cfg):
    routing = cfg.get('automatic_routing') if isinstance(cfg, dict) else None
    value = routing.get('host_routing') if isinstance(routing, dict) else None
    if not isinstance(value, dict):
        return None
    enabled, models = value.get('enabled_hosts'), value.get('models')
    if not isinstance(enabled, list) or not all(isinstance(h, str) and h in FAMILIES for h in enabled):
        return None
    if not isinstance(models, dict) or not all(isinstance(m, list) and all(isinstance(x, str) and x for x in m) for m in models.values()):
        return None
    return value


def decide(payload, *, execute=False, load_config=L.load_routing):
    if not isinstance(payload, dict):
        raise ValueError('Object payload required')
    host, request = payload.get('host'), payload.get('request')
    context = payload.get('context', '')
    if not isinstance(host, str) or host not in FAMILIES or not isinstance(request, str) or not request.strip() or not isinstance(context, str):
        raise ValueError('Known host and nonempty request required')
    cfg = copy.deepcopy(load_config())
    settings = host_settings(cfg)
    if not settings or host not in settings['enabled_hosts']:
        return {'status': 'disabled', 'reason': 'Native host routing not enabled for this project/host'}
    if len((request + context).encode('utf-8')) > 24000:
        return {'status': 'needs_review', 'reason': 'Routing synopsis exceeds 24000 bytes; no hidden truncation'}
    # Configuring a provider alone is not consent to send native host input.
    classifier = (cfg.get('automatic_routing') or {}).get('classifier')
    if classifier != 'local-d1' and not (classifier in {'provider', 'cascade'} and settings.get('api_classifier_opt_in') is True):
        return {'status': 'unavailable', 'reason': 'Native API classification requires explicit api_classifier_opt_in'}
    timeout = settings.get('timeout_seconds', 120)
    if type(timeout) is not int or not 1 <= timeout <= 240:
        return {'status': 'unavailable', 'reason': 'Invalid host routing timeout'}
    compact = settings.get('compact_questions', True)
    if type(compact) is not bool:
        return {'status': 'unavailable', 'reason': 'compact_questions must be boolean'}
    class ConfigurationChanged(ValueError):
        pass

    def checked_config():
        fresh = load_config()
        if fresh != cfg:
            raise ConfigurationChanged()
        if classifier == 'cascade':
            fresh = copy.deepcopy(fresh)
            fresh['automatic_routing']['bootstrap_host'] = 'claude' if host == 'claude-code' else 'codex'
        return fresh

    try:
        result = R.classify_request(request, context, execute=execute, load_config=checked_config, timeout=timeout,
                                    question_scope='native' if compact else 'all')
    except ConfigurationChanged:
        return {'status': 'unavailable', 'reason': 'Configuration changed before classification'}
    # Native hosts retain their tools. The text-only delegate's capability gate
    # must not veto a confident model/effort selection here. Keep the raw receipt
    # and unchanged .75 thresholds; never accept a failed/invalid transport.
    native_labels = None
    if (result.get('route') or {}).get('result') == 'succeeded' and result.get('response'):
        qs = R.questions(cfg)
        native_labels = R.classified(result['response'], {k: qs[k] for k in ('tier', 'effort')}, .75)
        if native_labels:
            result = {**result, 'status': 'accepted', 'labels': native_labels,
                      'capability_gate': 'not_applicable_native_host_retains_tools'}
    report = {'status': result['status'], 'host': host, 'classification': result, 'classifier': classifier,
              'question_scope': 'native' if compact else 'all',
              'execution': 'host_owned', 'account_switch': False}
    if result['status'] != 'accepted':
        return report
    fresh = load_config()
    if fresh != cfg:
        return {**report, 'status': 'unavailable', 'reason': 'Configuration changed before host override'}
    labels = result['labels']
    tier = labels['tier']
    if tier not in R.TASKS:
        return {**report, 'status': 'needs_review', 'reason': 'No model for this tier'}
    family = FAMILIES[host]
    model = L.model_for(cfg, family, tier)
    ladder = L.tier_efforts(cfg, tier, family, model)
    effort = ladder[-1 if labels['effort'] == 'deep' else 0] if ladder else None
    allowlist = (settings.get('models') or {}).get(host, [])
    if not isinstance(model, str) or model not in allowlist or effort in {'max', 'ultra'}:
        return {**report, 'status': 'needs_review', 'reason': 'Model/effort not approved for native host routing'}
    if tier == 'T4':
        if not A.t4_available(cfg):
            return {**report, 'status': 'needs_review', 'reason': 'T4 standing approval/quota unavailable'}
    return {**report, 'status': 'selected', 'tier': tier, 'model': model, 'effort': effort,
            'reason': 'Decision classifier selected native host request; host retains credentials, tools and permissions'}


def authorize(selection, *, load_config=L.load_routing):
    """Recheck a selection at the adapter's actual dispatch boundary.

    This does not authorize tools or billing. Native transport/session still owns
    those gates. T4 attempts consume the same approved quota as delegate.py:
    one Codex turn/spawn or one Claude turn.step. Host-internal tool loops and
    transport retries are not individually metered by the Codex turn boundary.
    """
    cfg = load_config()
    options = host_settings(cfg)
    denied = {'status': 'needs_review', 'reason': 'Host override no longer approved'}
    if not isinstance(selection, dict) or not options:
        return denied
    host = selection.get('host')
    classifier = selection.get('classifier')
    if classifier is not None:
        if classifier != (cfg.get('automatic_routing') or {}).get('classifier'):
            return denied
        if classifier in {'provider', 'cascade'} and options.get('api_classifier_opt_in') is not True:
            return denied
    tier, model, effort = (selection.get(k) for k in ('tier', 'model', 'effort'))
    if not isinstance(host, str) or not isinstance(tier, str) or host not in FAMILIES or host not in options['enabled_hosts'] or tier not in R.TASKS:
        return denied
    if model != L.model_for(cfg, FAMILIES[host], tier) or model not in (options.get('models') or {}).get(host, []):
        return denied
    ladder = L.tier_efforts(cfg, tier, FAMILIES[host], model)
    if not (effort is None or isinstance(effort, str)) or effort not in (ladder or [None]) or effort in {'max', 'ultra'}:
        return denied
    report = {'status': 'authorized', 'host': host, 'tier': tier, 'model': model, 'effort': effort}
    if tier == 'T4':
        try:
            report['t4_reservation'] = A.reserve_t4(cfg)
        except (ValueError, OSError):
            return {**denied, 'reason': 'T4 approval/quota unavailable; do not dispatch'}
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execute', action='store_true', help='Configured decision classifier only; host executes its own request')
    parser.add_argument('--authorize', action='store_true', help='Recheck selection before each host model request')
    args = parser.parse_args(argv)
    try:
        raw = sys.stdin.buffer.read(65537)
        if len(raw) > 65536:
            raise ValueError('Host event exceeds 65536 bytes')
        report = authorize(json.loads(raw)) if args.authorize else decide(json.loads(raw), execute=args.execute)
    except (ValueError, OSError, TypeError, KeyError) as exc:
        report = {'status': 'unavailable', 'reason': type(exc).__name__}
    print(json.dumps(report, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    import sys as _sys
    for _stream in (_sys.stdin, _sys.stdout, _sys.stderr):  # Windows: pipes/console default to cp1251
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    raise SystemExit(main())

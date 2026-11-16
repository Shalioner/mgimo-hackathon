"""One low-effort classifier on the current native ChatGPT account, no task execution."""
from __future__ import annotations

import json

import decide as C
import native_codex as N
import route_request as R


def _configuration_unchanged(load_config, cfg):
    """A failed reload revokes acceptance without discarding completed work."""
    try:
        return load_config() == cfg
    except (ValueError, OSError, TypeError, KeyError):
        return False


def classify(request, context, *, cfg, load_config, timeout, question_scope, host, binary):
    """R-compatible result. Host/model permission and fresh auth stay fail-closed."""
    route = {"result": "not_started", "started": False, "purpose": "routing_classifier",
             "transport": "native-subscription", "requested": {"host": host, "effort": "low"}}
    unavailable = {"status": "unavailable", "labels": None, "route": route}
    if host not in ("codex", "orca"):
        return {**unavailable, "reason": "Claude subscription classifier requires a verified logged-in subscription adapter"}
    if question_scope not in ("all", "native"):
        return {**unavailable, "reason": "Unknown classifier question scope"}
    if not _configuration_unchanged(load_config, cfg):
        return {**unavailable, "reason": "Classifier configuration changed"}
    selection = N.plan("T1", cfg, effort="low")
    if selection["status"] != "selected":
        return {**unavailable, "reason": selection.get("reason", "T1 classifier is not approved")}
    route["requested"].update(model=selection["model"])
    qs = R.questions(cfg)
    if question_scope == "native":
        qs = {key: qs[key] for key in ("tier", "effort")}
    state = "REQUEST (DATA):\n" + request + "\nCONTEXT (DATA):\n" + context
    prompt = C.EMULATION_PROMPT.replace("{questions}", json.dumps(qs, ensure_ascii=False)).replace("{state}", state)
    # Native transport disables tools/hooks/agents and checks account/read before inference.
    receipt, text = N.execute(binary, prompt, selection, load_config=load_config,
                              timeout=timeout, require_subscription=True)
    route.update(result=receipt["status"], started=receipt.get("transmitted_model") is not None,
                 native_receipt=receipt)
    report = {"status": "needs_review", "labels": None, "route": route,
              "method": "labels_with_quotes_not_calibrated_probabilities"}
    if receipt["status"] != "succeeded" or not _configuration_unchanged(load_config, cfg):
        return {**report, "reason": "Subscription classifier unavailable or configuration changed"}
    raw = C.parse_answers(text).get("answers", {})
    if not all(isinstance(raw.get(key), dict) and "type" not in raw[key]
               and type(raw[key].get("sure")) is bool for key in qs):
        return {**report, "reason": "Invalid subscription classifier labels"}
    response = {"answers": C.from_labels(raw, qs, state)}
    labels = R.classified(response, qs, .85)
    return {**report, "status": "accepted" if labels else "needs_review", "labels": labels,
            "response": response, "threshold": .85,
            "reason": None if labels else "Uncertain subscription classifier; no inferred default"}


def infer_questions(state, questions, *, cfg, load_config, execute=False, timeout=240):
    """Generic label-with-evidence fallback. Scores are policy, not probabilities.

    In particular, an unevidenced `no` can never delete exported tool context.
    """
    import delegate as D
    options = cfg.get('automatic_routing') or {}
    host = options.get('bootstrap_host', 'codex')
    native = options.get('subscription_classifier') or {}
    report = {'status': 'unavailable', 'route': None,
              'method': 'labels_with_quotes_not_calibrated_probabilities'}
    if not _configuration_unchanged(load_config, cfg):
        return {**report, 'reason': 'Classifier configuration changed'}
    if (cfg.get('execution_policy') or {}).get('mode') != 'subscription_only':
        return {**report, 'reason': 'Generic subscription classifier requires subscription_only policy'}
    prompt = C.EMULATION_PROMPT.replace('{questions}', json.dumps(questions, ensure_ascii=False)).replace('{state}', state)
    prompt += '\nFor this safety-sensitive task, yes AND no require a supporting quote from STATE. Do not infer no from missing evidence.'
    if host in ('codex', 'orca') and native.get('enabled') is True:
        selection = N.plan('T1', cfg, effort='low')
        if selection['status'] != 'selected':
            return {**report, 'reason': selection.get('reason', 'T1 classifier not approved')}
        route = {'status': 'executable', 'result': 'not_started', 'started': False,
                 'transport': 'native-subscription', 'requested': {'host': host, 'model': selection['model'], 'effort': 'low'}}
        if not execute:
            return {**report, 'status': 'pending', 'route': route}
        if not _configuration_unchanged(load_config, cfg):
            return {**report, 'route': route, 'reason': 'Classifier configuration changed'}
        receipt, text = N.execute(native.get('codex_binary'), prompt, selection,
                                 load_config=load_config, timeout=timeout, require_subscription=True)
        route.update(result=receipt['status'], started=receipt.get('transmitted_model') is not None,
                     native_receipt=receipt)
    else:
        route = R.route_plan(cfg, host, 'T1', prompt, bootstrap=True)
        if route['status'] != 'executable' or not execute:
            return {**report, 'route': route, 'status': 'pending' if route['status'] == 'executable' else 'unavailable'}
        if not _configuration_unchanged(load_config, cfg):
            return {**report, 'route': route, 'reason': 'Classifier configuration changed'}
        route, text, _ = D.execute_route(cfg, host, route['requested']['model'], prompt,
                                         route['requested']['effort'], timeout, route, revalidate=lambda: _configuration_unchanged(load_config, cfg))
    route['method'] = report['method']
    report.update(status='needs_review', route=route)
    if route.get('result') != 'succeeded' or not _configuration_unchanged(load_config, cfg):
        return {**report, 'reason': 'Subscription classifier unavailable or configuration changed'}
    raw = C.parse_answers(text or '').get('answers', {})
    if set(raw) != set(questions):
        return {**report, 'reason': 'Missing or extra classifier labels'}
    answers = {}
    for key, question in questions.items():
        label = raw[key]
        if (not isinstance(label, dict) or 'type' in label
                or not isinstance(label.get('quote'), str) or not C.quote_ok(label['quote'], state)):
            return {**report, 'reason': 'Classifier label lacks quoted evidence'}
        if question['type'] == 'noul':
            if label.get('verdict') not in ('yes', 'no'):
                return {**report, 'reason': 'Uncertain classifier label'}
            answers[key] = {'type': 'noul', 'noul': .95 if label['verdict'] == 'yes' else .05,
                            'quote_ok': True}
        else:
            keys = list(question['criteria'])
            choice = label.get('choice')
            if not isinstance(choice, str) or choice not in keys or label.get('sure') is not True:
                return {**report, 'reason': 'Uncertain classifier label'}
            score = .9 if len(keys) > 1 else 1.
            answers[key] = {'type': 'choice', 'choice': choice, 'confidence': score, 'quote_ok': True,
                            'probabilities': {k: score if k == choice else (1-score)/(len(keys)-1) for k in keys}}
    return {**report, 'status': 'succeeded',
            'response': {'answers': answers, 'method': report['method']}}

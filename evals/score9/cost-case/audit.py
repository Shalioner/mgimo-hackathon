#!/usr/bin/env python3
"""Offline audit of one bounded product result. No model, network or user logs."""
import argparse
import copy
import hashlib
import json
import re
import fnmatch
from datetime import datetime
from decimal import Decimal
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
BASELINE = '0888a8e5d92c2e62593bf5dccf9ee1da65ea1fe1'
CASE = 'projects/ryadom/'
SECTIONS = ['## Граница результата', '## Где расходуются ресурсы',
            '## Что решает руководитель', '## Ограничения', '## Воспроизведение']


def extract(root, snapshot):
    sources = {}
    for rel, pin in snapshot['sources'].items():
        data = (root / rel).read_bytes()
        if hashlib.sha256(data).hexdigest() != pin['sha256']:
            raise ValueError('source hash mismatch: ' + rel)
        sources[rel] = data.decode('utf-8')
    ids = [r[3] for r in snapshot['records']]
    if len(set(ids)) != len(ids):
        raise ValueError('duplicate stream')
    if set(ids) != {a['thread_id'] for a in snapshot['attribution']}:
        raise ValueError('missing attribution')
    if len({a['cwd_sha256'] for a in snapshot['attribution']}) != 1:
        raise ValueError('mixed workspaces')
    prices = yaml.safe_load(sources['knowledge/prices.yaml'])['models']
    # Only the two observed model IDs; historical prices come from this baseline.
    rows = []
    for model in sorted({r[1] for r in snapshot['records']}):
        price = next(p for p in prices if fnmatch.fnmatch(model, p['match']))
        selected = [r for r in snapshot['records'] if r[1] == model]
        usage = {k: sum(r[2][k] for r in selected) for k in ['input', 'cached', 'output', 'calls']}
        amount = sum(Decimal(usage[k]) * Decimal(str(price[k])) for k in ['input', 'cached', 'output']) / 1000000
        rows.append({'model': model, **usage, 'usd': str(amount)})
    recorded = sources[CASE + 'state/cost_report.md']
    total = sum(Decimal(r['usd']) for r in rows)
    if f'${total:.4f}' not in recorded:
        raise ValueError('recorded total differs from token calculation')
    # Compare all model counters to the previously committed report, not only its total.
    for row in rows:
        line = next(l for l in recorded.splitlines() if '| ' + row['model'] + ' |' in l)
        cells = [c.strip() for c in line.strip('|').split('|')]
        if [int(c.replace(' ', '')) for c in cells[2:6]] != [row[k] for k in ['calls', 'input', 'cached', 'output']]:
            raise ValueError('recorded counters differ: ' + row['model'])
        if Decimal(cells[6]) != Decimal(row['usd']).quantize(Decimal('.0001')):
            raise ValueError('recorded model cost differs: ' + row['model'])
    included = [r for r in snapshot['records'] if r[3] not in snapshot['excluded_threads']]
    result_rows = []
    for row in rows:
        usage = {k: sum(r[2][k] for r in included if r[1] == row['model']) for k in ['input', 'cached', 'output', 'calls']}
        price = next(p for p in prices if fnmatch.fnmatch(row['model'], p['match']))
        amount = sum(Decimal(usage[k]) * Decimal(str(price[k])) for k in ['input', 'cached', 'output']) / 1000000
        result_rows.append({'model': row['model'], **usage, 'usd': str(amount)})
    excluded_usd = Decimal(0)
    for env, model, usage, tid in snapshot['records']:
        if tid in snapshot['excluded_threads']:
            price = next(p for p in prices if fnmatch.fnmatch(model, p['match']))
            excluded_usd += sum(Decimal(usage[k]) * Decimal(str(price[k])) for k in ['input', 'cached', 'output']) / 1000000
    log = sources[CASE + 'state/run_log.md']
    stamps = sorted(set(re.findall(r'2026-11-10T\d\d:\d\d:\d\d\+03:00', log)))
    start = stamps[0]
    end = '2026-11-10T16:27:58+03:00'
    if end not in stamps:
        raise ValueError('missing completion record')
    decision = yaml.safe_load(sources[CASE + 'state/ledger/decisions.yaml'])[0]
    experiments = yaml.safe_load(sources[CASE + 'state/ledger/experiments.yaml'])
    source_objects = yaml.safe_load(sources[CASE + 'state/ledger/sources.yaml'])
    hours = {'EXP-001': 2, 'EXP-002': 4, 'EXP-003': 4, 'EXP-004': 10, 'EXP-005': 4}
    for exp in experiments:
        if 'result' in exp:
            raise ValueError('experiment now has results: ' + exp['id'])
        if not re.search(r'(?:До|предел) ' + str(hours[exp['id']]) + r' человеко-час(?:ов|а)' , exp['duration'], re.I):
            raise ValueError('experiment planned hours changed: ' + exp['id'])
    # Stable locators for every source, with quote and line, kept separate from interpretation.
    anchors = {
        'cost': (CASE + 'state/cost_report.md', '**Итого'),
        'search': (CASE + 'state/run_log.md', 'Учёт веб-бюджета'),
        'retry': (CASE + 'state/run_log.md', '42/43'),
        'change': (CASE + 'state/run_log.md', '22 прежних объекта'),
        'completion': (CASE + 'state/run_log.md', end),
        'later_work': (CASE + 'state/run_log.md', '2026-11-17'),
        'choice': (CASE + 'state/ledger/decisions.yaml', 'chosen:'),
        'approval': (CASE + 'state/ledger/decisions.yaml', 'Часы перераспредели'),
        'plan': (CASE + 'outputs/08_decision_log.md', '| Доступы,'),
        'human': (CASE + 'outputs/09_run_report.md', '3 обязательных checkpoints'),
    }
    references = {}
    for name, (rel, needle) in anchors.items():
        number, quote = next((n, line) for n, line in enumerate(sources[rel].splitlines(), 1) if needle in line)
        references[name] = {'path': rel, 'line': number, 'quote': quote}
    return {
        'baseline': snapshot['baseline'], 'result': 'Рядом: пакет ALT-006 после CHG-001; срез 2026-11-10',
        'window_models': rows, 'result_models': result_rows, 'window_api_equivalent_usd': str(total.quantize(Decimal('.0001'))),
        'api_equivalent_usd': str((total-excluded_usd).quantize(Decimal('.0001'))),
        'included_streams': len(included), 'excluded_api_equivalent_usd': str(excluded_usd.quantize(Decimal('.0001'))),
        'exclusion': snapshot['exclusion_evidence'],
        'cash_payment_usd': None, 'active_seconds': None,
        'calendar': {'start': start, 'end': end, 'seconds': int((datetime.fromisoformat(end)-datetime.fromisoformat(start)).total_seconds())},
        'initial_calendar_seconds': int((datetime.fromisoformat('2026-11-10T15:56:21+03:00')-datetime.fromisoformat(start)).total_seconds()),
        'search': {'calls': 2, 'queries': 4, 'failed_opens': 3, 'external_sources_read': 6, 'cash_payment_usd': None},
        'sources_in_window': sum(s['retrieved'] == '2026-11-10' for s in source_objects),
        'later_sources_excluded': [s['id'] for s in source_objects if s['retrieved'] > '2026-11-10'],
        'human': {'initial_checkpoints': ['C1', 'C2', 'C3'], 'revision_approval': True, 'active_seconds': None},
        'plan': {'status': 'approved_limit_not_actual', 'hours_by_experiment': hours, 'total_hours': sum(hours.values()), 'new_cash_limit': 0},
        'chosen': decision['chosen'], 'decided_by': decision['decided_by'],
        'references': references,
    }


def validate(evidence, expected):
    errors = []
    # Unknowns are a requirement, not an invitation to fill them with zero.
    for key in ['cash_payment_usd', 'active_seconds']:
        if evidence.get(key) is not None:
            errors.append(key + ' must remain unknown')
    for key in expected:
        if evidence.get(key) != expected[key]:
            errors.append('evidence differs from extracted source: ' + key)
    return errors


def controls(expected):
    mutations = {
        'payment_as_api': lambda x: x.update(cash_payment_usd=3.2351),
        'elapsed_as_active': lambda x: x.update(active_seconds=x['calendar']['seconds']),
        'budget_as_actual': lambda x: x['plan'].update(status='actual'),
        'omit_revision_help': lambda x: x['human'].update(revision_approval=False),
        'invent_search_free': lambda x: x['search'].update(cash_payment_usd=0),
        'omit_cached_tokens': lambda x: x['window_models'][0].update(cached=0),
    }
    results = {}
    for name, mutate in mutations.items():
        candidate = copy.deepcopy(expected)
        mutate(candidate)
        errors = validate(candidate, expected)
        results[name] = {'rejected': bool(errors), 'errors': errors}
    return results


def broken_links(memo, path):
    errors = []
    for target in re.findall(r'\]\(([^)#]+)(?:#[^)]+)?\)', memo):
        if '://' not in target and not (path.parent / target).exists():
            errors.append('broken memo link: ' + target)
    return errors


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True, help='unmodified baseline archive')
    parser.add_argument('--memo', type=Path, default=HERE / 'NOTE.md')
    parser.add_argument('--write-evidence', action='store_true')
    parser.add_argument('--controls', action='store_true')
    args = parser.parse_args()
    snapshot = json.loads((HERE / 'usage-snapshot.json').read_text())
    if snapshot['baseline'] != BASELINE:
        raise ValueError('wrong baseline')
    expected = extract(args.root, snapshot)
    if args.write_evidence:
        (HERE / 'evidence.json').write_text(json.dumps(expected, ensure_ascii=False, indent=2) + '\n')
    if args.controls:
        result = controls(expected)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if all(r['rejected'] for r in result.values()) else 1
    evidence_path = HERE / 'evidence.json'
    errors = validate(json.loads(evidence_path.read_text()), expected) if evidence_path.exists() else ['missing evidence.json']
    memo = args.memo.read_text() if args.memo.exists() else ''
    errors.extend(broken_links(memo, args.memo))
    coverage = {section: section in memo for section in SECTIONS}
    for section, present in coverage.items():
        if not present:
            errors.append('missing memo section: ' + section)
    # A few central values must be visible to the decision maker, not only in JSON.
    for token in ['$3.0331', '$3.2351', '127 мин 23 с', '24', 'активное время', 'API-эквивалент', 'EXP-001', '16:27:58']:
        if token not in memo:
            errors.append('missing visible boundary/value: ' + token)
    print(json.dumps({'baseline': BASELINE, 'coverage': coverage, 'errors': errors, 'passed': not errors}, ensure_ascii=False, indent=2))
    return int(bool(errors))


if __name__ == '__main__':
    raise SystemExit(main())

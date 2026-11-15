#!/usr/bin/env python3
"""Offline aggregation, frozen-price API-equivalent and blinded review package."""
import argparse
from collections import Counter
import json
from pathlib import Path
import random
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT/'tools'))
import cost


def parse(text):
    if not isinstance(text, str):
        return None
    text = text.strip()
    if text.startswith('```'):
        text = '\n'.join(text.splitlines()[1:-1])
    try:
        obj = json.loads(text)
        return obj if isinstance(obj, dict) else None
    except ValueError:
        return None


def matches(actual, expected):
    if isinstance(expected, bool):
        return actual is expected
    if type(expected) in (int, float):
        return type(actual) in (int, float) and abs(actual - expected) <= .01
    if isinstance(expected, list):
        return isinstance(actual, list) and sorted(actual) == sorted(expected)
    return actual == expected


def analyze(folder):
    summary = json.loads((folder/'summary.json').read_text())
    manifest = json.loads((folder/'manifest.json').read_text())
    cases = {c['id']: c for c in manifest['cases']}
    prices = cost.load_prices(folder/'prices.yaml')
    arms, blind, mapping = {}, [], {}
    for arm in ('routed', 'manual'):
        rows = [r for r in summary['rows'] if r['arm'] == arm]
        result = {'tasks': len(rows), 'status': dict(Counter(r['status'] for r in rows)),
                  'input_tokens': 0, 'cached_input_tokens': 0, 'output_tokens': 0,
                  'native_calls': 0, 'api_equivalent_usd': 0, 'unknown_pricing_calls': 0,
                  'accounting_complete': all(r['accounting_complete'] for r in rows),
                  'mechanical_all_fields_pass_delivered': 0, 'mechanical_checks': [],
                  'd1_requests': 0, 'd1_input_tokens_known': 0, 'd1_unknown_usage_requests': 0,
                  'd1_timing': [], 'task_model_changes': 0, 'task_effort_only_changes': 0,
                  'sequences': {}}
        for row in rows:
            for call in row['calls']:
                if call['model'] is None:
                    continue
                result['native_calls'] += 1
                usage = call['usage']
                if usage is None:
                    result['unknown_pricing_calls'] += 1
                    continue
                result['input_tokens'] += usage['inputTokens']
                result['cached_input_tokens'] += usage['cachedInputTokens']
                result['output_tokens'] += usage['outputTokens']
                u = {'input': usage['inputTokens'] - usage['cachedInputTokens'],
                     'cached': usage['cachedInputTokens'], 'output': usage['outputTokens']}
                price = cost.usd(u, cost.price_for(call['model'], prices))
                if price is None or usage.get('cacheWriteInputTokens', 0):
                    result['unknown_pricing_calls'] += 1
                else:
                    result['api_equivalent_usd'] += price
            if row.get('d1_status'):
                result['d1_requests'] += 1
                if row.get('d1_usage'):
                    result['d1_input_tokens_known'] += row['d1_usage']['input_tokens']
                else:
                    result['d1_unknown_usage_requests'] += 1
                result['d1_timing'].append({'batch': row['batch'], 'case': row['case'], 'step': row['step'],
                    'status': row['d1_status'], **(row.get('d1_runtime') or {})})
            sequence_key = row['batch'] + '/' + row['case']
            result['sequences'].setdefault(sequence_key, []).extend(
                [{'step': row['step'], **s} for s in row['task_sequence']])
            step = cases[row['case']]['steps'][row['step']-1]
            answer = parse(row['answer'])
            facts = answer.get('facts', {}) if answer else {}
            if not isinstance(facts, dict):
                facts = {}
            checks = {k: matches(facts.get(k), v) for k, v in step['expectedfacts'].items()}
            if row['case'] == 'C5' and row['step'] == 1 and facts.get('P1') == 'contradicted':
                checks['P1'] = True  # preregistered acceptable alternative
            passed = bool(answer) and all(checks.values())
            result['mechanical_all_fields_pass_delivered'] += int(passed)
            result['mechanical_checks'].append({'batch': row['batch'], 'case': row['case'],
                'step': row['step'], 'delivered': row['answer'] is not None, 'checks': checks,
                'json_valid': answer is not None, 'all_fields_pass': passed})
            if row['answer'] or row.get('candidate'):
                blind.append({'request': step['request'], 'context': step['context'],
                              'answer': row['answer'] or row.get('candidate'),
                              'expectedfacts': step['expectedfacts'], 'blind_criteria': step.get('blind_criteria', []),
                              'prohibitedclaims': step.get('prohibitedclaims', []),
                              '_mapping': {'batch': row['batch'], 'case': row['case'], 'step': row['step'],
                                           'delivered': row['answer'] is not None}})
        for sequence in result['sequences'].values():
            for a, b in zip(sequence, sequence[1:]):
                if a['model'] != b['model']:
                    result['task_model_changes'] += 1
                elif a['effort'] != b['effort']:
                    result['task_effort_only_changes'] += 1
        result['api_equivalent_usd'] = round(result['api_equivalent_usd'], 8)
        arms[arm] = result
    random.Random(15112026).shuffle(blind)
    for i, item in enumerate(blind):
        ident = f'B{i+1:03}'
        mapping[ident] = item.pop('_mapping')
        item['id'] = ident
    return {'arms': arms, 'limitations': 'Mechanical fields are not semantic quality. API-equivalent is not subscription charges. No cost saving claim when delivered quality/coverage differs. D1 electricity unmeasured.'}, blind, mapping


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('folder', type=Path)
    args = parser.parse_args()
    metrics, blind, mapping = analyze(args.folder)
    for name, value in [('metrics.json', metrics), ('blind-review.json', blind), ('blind-map.json', mapping)]:
        with (args.folder/name).open('x') as file:
            json.dump(value, file, ensure_ascii=False, indent=2)
    print(json.dumps({arm: {k:v for k,v in x.items() if k not in ('sequences','mechanical_checks','d1_timing')}
                      for arm,x in metrics['arms'].items()}, ensure_ascii=False, indent=2))

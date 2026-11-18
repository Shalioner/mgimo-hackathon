#!/usr/bin/env python3
"""Offline corpus check/scorer; does not call or simulate a semantic judge."""
import argparse
import json
from pathlib import Path

LABELS = {'ok', 'unsupported', 'overstated', 'wrong-ref', 'not_distinct', 'inconsistent'}


def load_cases(path):
    cases = json.loads(Path(path).read_text(encoding='utf-8'))['cases']
    if not cases or len({c['id'] for c in cases}) != len(cases):
        raise ValueError('Nonempty corpus with unique IDs required')
    for c in cases:
        if c['expected'] not in LABELS or any(not isinstance(c[k], str) or not c[k].strip()
                for k in ('id', 'axis', 'source', 'text', 'rationale')):
            raise ValueError('Invalid case')
    for axis in {c['axis'] for c in cases}:
        labels = {c['expected'] for c in cases if c['axis'] == axis}
        if 'ok' not in labels or len(labels) < 2:
            raise ValueError('Each axis requires a negative and a control')
    return cases


def score(cases, answers):
    ids = {c['id'] for c in cases}
    if not isinstance(answers, dict) or set(answers) != ids:
        raise ValueError('Exactly one answer per case required; no missing/extra IDs')
    if any(label not in LABELS for label in answers.values() if isinstance(label, str)) or any(
            not isinstance(label, str) for label in answers.values()):
        raise ValueError('Unknown label')
    axes = {}
    for c in cases:
        row = axes.setdefault(c['axis'], {'correct': 0, 'total': 0, 'missed_defects': [], 'false_alarms': []})
        row['total'] += 1
        row['correct'] += answers[c['id']] == c['expected']
        if c['expected'] != 'ok' and answers[c['id']] == 'ok':
            row['missed_defects'].append(c['id'])
        if c['expected'] == 'ok' and answers[c['id']] != 'ok':
            row['false_alarms'].append(c['id'])
    return {'cases': len(cases), 'correct': sum(r['correct'] for r in axes.values()), 'axes': axes}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cases', type=Path, default=Path(__file__).with_name('cases.json'))
    parser.add_argument('--answers', type=Path)
    args = parser.parse_args()
    try:
        cases = load_cases(args.cases)
        result = score(cases, json.loads(args.answers.read_text())) if args.answers else {
            'status': 'corpus_validated_only', 'cases': len(cases),
            'axes': sorted({c['axis'] for c in cases}), 'model_evaluated': False}
    except (ValueError, OSError, KeyError, TypeError) as exc:
        parser.error(str(exc))
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()

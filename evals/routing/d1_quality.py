#!/usr/bin/env python3
"""Local classifier-only diagnostic. No task executor, Luna, or paid API calls."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import random
import sys
import threading
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tools'))
import host_routing as H
import ledger as L
import route_request as R
import d1_control


def write(path, data):
    with Path(path).open('x', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2, allow_nan=False)


def summarize(cases, rows):
    by_id = {r['id']: r for r in rows}
    evaluated = []
    for case in cases:
        row = by_id.get(case['id'], {})
        report = row.get('report', {})
        classification = report.get('classification', {})
        response = classification.get('response') or {}
        answers = response.get('answers', {})
        transport_ok = (classification.get('route') or {}).get('result') == 'succeeded'
        matches = {k: answers.get(k, {}).get('choice') in case[k]
                   for k in ('tier', 'effort', 'capability')}
        confident = transport_ok and all(
            answers.get(k, {}).get('confidence', 0) >= .75 for k in ('tier', 'effort'))
        evaluated.append({'id': case['id'], 'status': report.get('status', 'missing'),
            'transport_ok': transport_ok, 'matches': matches,
            'route_correct': transport_ok and matches['tier'] and matches['effort'],
            'confident_tier_effort': confident, 'selected': report.get('status') == 'selected',
            'choices': {k: {'choice': v.get('choice'), 'confidence': v.get('confidence')}
                        for k, v in answers.items()},
            'below_threshold': [k for k in ('tier', 'effort')
                                if answers.get(k, {}).get('confidence', 0) < .75],
            'model': report.get('model'), 'model_effort': report.get('effort'),
            'reason': report.get('reason') or classification.get('reason'),
            'runtime': classification.get('runtime'), 'wall_seconds': row.get('wall_seconds')})
    return {'tasks': len(cases), 'transport_success': sum(r['transport_ok'] for r in evaluated),
        'top1_route_matches': sum(r['route_correct'] for r in evaluated),
        'field_matches': {k: sum(r['transport_ok'] and r['matches'][k] for r in evaluated)
                          for k in ('tier', 'effort', 'capability')},
        'confident_routes': sum(r['confident_tier_effort'] for r in evaluated),
        'confident_route_errors': sum(r['confident_tier_effort'] and not r['route_correct'] for r in evaluated),
        'selected_routes': sum(r['selected'] for r in evaluated),
        'selected_route_errors': sum(r['selected'] and not r['route_correct'] for r in evaluated),
        'correct_but_uncertain': sum(r['route_correct'] and not r['confident_tier_effort'] for r in evaluated),
        'rows': evaluated,
        'limitation': 'Agreement with preregistered engineering labels, not measured minimum model capability. One diagnostic round; no threshold tuning.'}


def saved_rows(folder, cases):
    rows = []
    for case in cases:
        path = folder/(case['id']+'.json')
        if not path.exists():
            continue
        try:
            row = json.loads(path.read_text())
            if row.get('id') != case['id']:
                raise ValueError('Receipt ID mismatch')
            rows.append(row)
        except (ValueError, AttributeError):
            rows.append({'id': case['id'], 'report': {'status': 'corrupt_receipt'}})
    return rows


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--cases', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--execute', action='store_true')
    args = p.parse_args()
    cases = json.loads(args.cases.read_text())['cases']
    cfg = L.load_routing()
    cfg['automatic_routing'].setdefault('local_d1', {})['reuse'] = False
    # Preserve the historical three-question quality protocol.
    cfg['automatic_routing'].setdefault('host_routing', {})['compact_questions'] = False
    qs = R.questions(cfg)
    assert len({c['id'] for c in cases}) == len(cases)
    for c in cases:
        for k in ('tier', 'effort', 'capability'):
            assert c[k] and set(c[k]) <= set(qs[k]['criteria'])
    if not args.execute:
        print(json.dumps({'status': 'preflight', 'cases': len(cases), 'model_calls': 0}))
        return
    assert cfg['automatic_routing']['classifier'] == 'local-d1'
    args.output.mkdir(parents=True, exist_ok=False)
    order = list(cases)
    random.Random(20261115).shuffle(order)
    source_files = [Path(__file__), ROOT/'tools/host_routing.py', ROOT/'tools/route_request.py',
                    ROOT/'tools/decide.py', ROOT/'tools/d1_service.py', ROOT/'tools/d1_engine.py']
    write(args.output/'manifest.json', {'started_utc': datetime.now(timezone.utc).isoformat(),
        'cases': cases, 'order': [c['id'] for c in order], 'questions': qs,
        'threshold': .75, 'parallel_clients': 5, 'rounds': 1, 'decision_reuse': False,
        'cases_sha256': hashlib.sha256(args.cases.read_bytes()).hexdigest(),
        'code_sha256': {str(f.relative_to(ROOT)): hashlib.sha256(f.read_bytes()).hexdigest() for f in source_files},
        'service_before': d1_control.status(), 'task_execution': False, 'cloud_calls': 0})
    for f in source_files:
        dest = args.output/'code_snapshot'/f.relative_to(ROOT)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(f.read_bytes())
    rows = []
    try:
        for start in range(0, len(order), 5):
            batch = order[start:start+5]
            barrier = threading.Barrier(len(batch))
            def run(case):
                barrier.wait(timeout=10)
                began = time.monotonic()
                try:
                    report = H.decide({'host': 'codex', 'request': case['request'],
                                       'context': case['context']}, execute=True, load_config=lambda: cfg)
                except Exception as exc:
                    report = {'status': 'error', 'error_type': type(exc).__name__}
                row = {'id': case['id'], 'report': report, 'wall_seconds': time.monotonic()-began}
                write(args.output/(case['id']+'.json'), row)
                return row
            with ThreadPoolExecutor(max_workers=5) as pool:
                rows.extend(pool.map(run, batch))
            print('Completed '+str(len(rows))+'/'+str(len(cases)), flush=True)
    finally:
        write(args.output/'summary.json', summarize(cases, saved_rows(args.output, cases)))
        write(args.output/'service-after.json', d1_control.status())


if __name__ == '__main__':
    main()

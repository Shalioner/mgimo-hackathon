"""Uncached paired native-routing probe: 20 distinct synthetic tasks, five callers.

Full three questions vs experimental tier/effort only. Does not enable the latter.
Alternates arm order by block; two burst blocks and two staggered-arrival blocks.
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import argparse
import hashlib
import json
from pathlib import Path
import statistics
import sys
import threading
import time
import uuid

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT/'tools'))
import d1_service as S
import ledger as L
import route_request as R


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    status = S.request(S.DEFAULT_SOCKET, {'op': 'status'})
    if status.get('active_id') or status.get('queue_depth'):
        raise RuntimeError('Idle service required')
    cases = json.loads(Path(__file__).with_name('d1_quality_cases.json').read_text())['cases']
    qs = R.questions(L.load_routing())
    native = {k: qs[k] for k in ('tier', 'effort')}
    args.output.mkdir(parents=True, exist_ok=False)
    def write(name, data):
        (args.output/name).write_text(json.dumps(data, ensure_ascii=False, indent=2))
    files = [Path(__file__), ROOT/'tools/d1_engine.py', ROOT/'tools/d1_service.py']
    write('manifest.json', {'started_utc': datetime.now(timezone.utc).isoformat(),
          'before': status, 'reuse': False, 'questions': qs, 'cases': cases,
          'files': {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in files}})
    for p in files:
        dest = args.output/'code'/p.relative_to(ROOT)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(p.read_bytes())
    def call(case, questions, barrier=None, delay=0):
        if barrier:
            barrier.wait(timeout=10)
        if delay:
            time.sleep(delay)
        start = time.monotonic()
        state = json.dumps({'request': case['request'], 'context': case['context']}, ensure_ascii=False)
        ident = uuid.uuid4().hex
        prepared = time.monotonic()
        result = S.request(S.DEFAULT_SOCKET, {'op': 'infer', 'id': ident, 'state': state,
                           'questions': questions, 'reuse': False, 'timeout': 120}, timeout=125)
        if result.get('id') != ident or result.get('status') != 'succeeded':
            raise RuntimeError(f'Invalid response: {result}')
        if not result['runtime'].get('engine_timing'):
            raise RuntimeError('Restart service to enable engine timing')
        return {'case': case['id'], 'seconds': time.monotonic()-start,
                'preparation_seconds': prepared-start, 'result': result}
    write('warmup.json', call(cases[0], qs))
    rows = []
    for block in range(4):
        chunk = cases[block*5:block*5+5]
        mode = 'burst' if block < 2 else 'staggered'
        arms = [('full', qs), ('native_two', native)]
        if block % 2:
            arms.reverse()
        for arm, questions in arms:
            barrier = threading.Barrier(5)
            def run(item):
                i, case = item
                row = call(case, questions, barrier, [0, .15, .4, .8, 1.4][i] if mode == 'staggered' else 0)
                row.update(arm=arm, arrival=mode, block=block)
                write(f'{arm}-{case["id"]}.json', row)
                return row
            with ThreadPoolExecutor(max_workers=5) as pool:
                batch = list(pool.map(run, enumerate(chunk)))
            rows.extend(batch)
            print(json.dumps({'block': block, 'arm': arm, 'arrival': mode,
                              'median_seconds': statistics.median(r['seconds'] for r in batch)}), flush=True)
    summary = {}
    for arm in ('full', 'native_two'):
        group = [r for r in rows if r['arm'] == arm]
        metrics = {}
        for name in ('queue_wait_seconds', 'worker_setup_seconds', 'worker_inference_seconds', 'server_total_seconds'):
            metrics[name] = statistics.median(r['result']['runtime']['timing'][name] for r in group)
        for name in ('preparation_seconds', 'model_seconds', 'validation_cleanup_seconds'):
            metrics['engine_'+name] = statistics.median(r['result']['runtime']['engine_timing'][name] for r in group)
        metrics['transport_residual_seconds'] = statistics.median(
            r['result']['runtime']['timing']['client_roundtrip_seconds']
            - r['result']['runtime']['timing']['queue_wait_seconds']
            - r['result']['runtime']['timing']['worker_setup_seconds']
            - r['result']['runtime']['timing']['worker_inference_seconds'] for r in group)
        correct = selected = selected_errors = tokens = 0
        for row in group:
            case = next(c for c in cases if c['id'] == row['case'])
            response = row['result']['response']
            good = all(response['answers'][k]['choice'] in case[k] for k in native)
            accepted = R.classified(response, native, .75) is not None
            correct += good
            selected += accepted
            selected_errors += accepted and not good
            tokens += response['usage']['input_tokens']
        summary[arm] = {'cases': len(group), 'correct': correct, 'selected': selected,
                        'selected_errors': selected_errors, 'input_tokens': tokens, 'median': metrics}
    changes = []
    for case in cases:
        a, b = [next(r for r in rows if r['case'] == case['id'] and r['arm'] == arm) for arm in ('full','native_two')]
        if any(a['result']['response']['answers'][k]['choice'] != b['result']['response']['answers'][k]['choice'] for k in native):
            changes.append(case['id'])
    summary['changed_choices'] = changes
    summary['limitations'] = 'One paired exploratory run, 20 engineering-reference tasks, no p95 or end-to-end business metric.'
    write('summary.json', summary)
    print(json.dumps(summary, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()

"""Local service-only A/B probe; synthetic cases, no executor or paid API.

Run after restarting the reviewed service: .venv/bin/python
evals/routing/d1_reuse_benchmark.py --output <new-directory>
Baseline bypasses reuse, candidate enables it. Not an end-to-end agent eval.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import statistics
import sys
import threading
import time
import uuid

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tools'))
import d1_service as S
import route_request as R


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    status = S.request(S.DEFAULT_SOCKET, {'op': 'status'})
    if ('reuse' not in status or status['active_id'] or status['queue_depth']
            or status['reuse']['entries']):
        raise RuntimeError('Updated idle service with empty cache required; restart before the experiment')
    cases = json.loads((Path(__file__).with_name('d1_quality_cases.json')).read_text(encoding="utf-8"))['cases']
    questions = R.questions()
    unique = [cases[i] for i in (0, 6, 12, 15, 19)]
    duplicate = [cases[4]] * 5
    manifest = {'service_before': status, 'scope': 'local classification transport only',
                'questions': questions, 'unique_cases': unique, 'duplicate_case': cases[4],
                'files': {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
                          for p in [ROOT/'tools/d1_service.py', ROOT/'tools/d1_engine.py', Path(__file__)]}}
    (args.output/'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    def call(case, reuse, barrier=None):
        before = time.monotonic()
        payload = {'op': 'infer', 'id': uuid.uuid4().hex, 'timeout': 120, 'reuse': reuse,
                   'state': json.dumps({'request': case['request'], 'context': case['context']}, ensure_ascii=False),
                   'questions': questions}
        preparation = time.monotonic()-before
        if barrier:
            barrier.wait(timeout=10)
        start = time.monotonic()
        result = S.request(S.DEFAULT_SOCKET, payload, timeout=125)
        if result.get('id') != payload['id'] or result.get('status') != 'succeeded':
            raise RuntimeError(f'Unsuccessful or mismatched reply: {result}')
        return {'case': case['id'], 'request_id': payload['id'], 'seconds': time.monotonic()-start,
                'preparation_seconds': preparation, 'result': result}

    warmup = call(cases[1], False)
    (args.output/'warmup.json').write_text(json.dumps(warmup, ensure_ascii=False, indent=2), encoding="utf-8")
    summaries = []
    answers = {}
    for name, workload, reuse in [
        ('baseline_unique', unique, False), ('reuse_unique', unique, True),
        ('baseline_duplicates', duplicate, False), ('reuse_duplicates', duplicate, True),
        ('baseline_repeat', unique, False), ('reuse_repeat', unique, True),
    ]:
        barrier = threading.Barrier(5)
        status = S.request(S.DEFAULT_SOCKET, {'op': 'status'})
        samples = []
        stop = threading.Event()
        def sample():
            while not stop.is_set():
                samples.append(S.sample_memory(status.get('worker_pid')))
                stop.wait(0.5)
        sampler = threading.Thread(target=sample, daemon=True)
        sampler.start()
        start = time.monotonic()
        try:
            with ThreadPoolExecutor(max_workers=5) as pool:
                rows = list(pool.map(lambda c: call(c, reuse, barrier), workload))
            elapsed = time.monotonic()-start
        finally:
            stop.set()
            sampler.join(timeout=10)
        (args.output/f'{name}.json').write_text(json.dumps({'rows': rows, 'memory_samples': samples}, ensure_ascii=False, indent=2), encoding="utf-8")
        for row in rows:
            answer = row['result']['response']['answers']
            if row['case'] in answers and answers[row['case']] != answer:
                raise RuntimeError('Answer changed across identical requests; inspect raw evidence')
            answers[row['case']] = answer
        times = [r['seconds'] for r in rows]
        summary = {'name': name, 'requests': 5, 'wall_seconds': elapsed,
                   'median_seconds': statistics.median(times), 'max_seconds': max(times),
                   'inference_calls': sum(r['result']['runtime']['inference_calls'] for r in rows),
                   'input_tokens': sum(r['result']['response']['usage']['input_tokens'] for r in rows),
                   'rss_kib_max_observed': max((s['rss_kib'] or 0 for s in samples), default=None),
                   'swap_mib_max_observed': max((s['swap_used_mib'] or 0 for s in samples), default=None)}
        summaries.append(summary)
        print(json.dumps(summary), flush=True)
    (args.output/'summary.json').write_text(json.dumps({'phases': summaries,
        'answer_equality': True, 'limitations': 'Five clients per phase, one run, synthetic reuse rate; no p95 or business-cost claim.'}, indent=2), encoding="utf-8")


if __name__ == '__main__':
    main()

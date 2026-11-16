#!/usr/bin/env python3
"""Four classifier arms; five independent two-step agents, one shared D1 service.

No model call without --execute. Native subscription only, no credentials copied.
Raw receipts are saved before aggregation; failed attempts remain in denominators.
"""
from __future__ import annotations

import argparse
import copy
from datetime import datetime, timezone
import hashlib
import json
import multiprocessing as mp
from pathlib import Path
import signal
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tools'))
import adaptive_execution as A
import document_flow as F
import ledger as L
import d1_control
import host_routing as H


def write(path, value):
    with Path(path).open('x', encoding='utf-8') as file:
        json.dump(value, file, ensure_ascii=False, indent=2, allow_nan=False)
        file.write('\n')


def configuration():
    cfg = copy.deepcopy(L.load_routing())
    # Identical preregistered pilot limits in all arms, not a persistent edit.
    cfg['automatic_routing']['quality'] = {'enabled': True, 'max_attempts': 2}
    cfg['automatic_routing']['host_routing']['timeout_seconds'] = 120
    return cfg


def agent(case, arm, barrier, folder, binary):
    # Each process is one independent case-agent. Turns get fresh ephemeral
    # native threads and fixed source inputs; no cross-arm response contamination.
    for index, step in enumerate(case['steps']):
        barrier.wait(timeout=600)
        started = time.monotonic()
        counter = [0]
        original_execute, original_decide = F.native_execute, H.decide
        def recorded_execute(binary, prompt, route, load_config, timeout):
            counter[0] += 1
            prefix = folder / f"{case['id']}-step{index + 1}-call{counter[0]:02}"
            write(str(prefix) + '-start.json', {'plan': route, 'prompt_sha256': hashlib.sha256(prompt.encode()).hexdigest(),
                                               'started_utc': datetime.now(timezone.utc).isoformat()})
            result = original_execute(binary, prompt, route, load_config, timeout)
            write(str(prefix) + '-end.json', {'route': result[0], 'text': result[1]})
            return result
        def recorded_decide(*args, **kwargs):
            result = original_decide(*args, **kwargs)
            write(folder / f"{case['id']}-step{index + 1}-d1.json", result)
            return result
        F.native_execute, H.decide = recorded_execute, recorded_decide
        row = {'case': case['id'], 'step': index + 1, 'arm': arm,
               'started_utc': datetime.now(timezone.utc).isoformat(),
               'agent_pid': __import__('os').getpid(), 'input': step}
        try:
            result = F.native_synthesis(step['request'], step['context'],
                step.get('routing_synopsis', step['context']), binary, configuration, 120,
                routing_mode=arm, baseline=step['baseline'])
            row['report'] = result
        except Exception as exc:
            row['report'] = {'status': 'error', 'error_type': type(exc).__name__}
        finally:
            F.native_execute, H.decide = original_execute, original_decide
        row['elapsed_seconds'] = round(time.monotonic() - started, 6)
        write(folder / f"{case['id']}-step{index + 1}.json", row)


def native_receipts(value):
    """Pick receipt objects once, not duplicated usage arrays in adaptive traces."""
    if isinstance(value, dict):
        if 'transmitted_model' in value and 'usage_events' in value:
            yield value
            return
        for item in value.values():
            yield from native_receipts(item)
    elif isinstance(value, list):
        for item in value:
            yield from native_receipts(item)


def summarize(folder):
    rows = []
    manifest = json.loads((folder / 'manifest.json').read_text())
    expected = [(label, case['id'], step) for label in manifest['order']
                for case in manifest['cases'] for step in (1, 2)]
    for label, case, step in expected:
        path = folder / label / f'{case}-step{step}.json'
        raw = (json.loads(path.read_text()) if path.exists() else
               {'case': case, 'step': step, 'arm': label.split('-', 1)[1],
                'report': {'status': 'missing_result'}, 'elapsed_seconds': None})
        report = raw['report']
        prefix = f'{case}-step{step}-call'
        ends = sorted(path.parent.glob(prefix + '*-end.json'))
        receipts = [(receipt, json.loads(end.with_name(end.name.replace('-end.json', '-start.json')).read_text())['plan'].get('purpose', 'unknown'))
                    for end in ends for receipt in native_receipts(json.loads(end.read_text()))]
        totals = {'input': 0, 'cached': 0, 'output': 0, 'reasoning_output': 0}
        missing = len(list(path.parent.glob(prefix + '*-start.json'))) - len(ends)
        calls = []
        for receipt, purpose in receipts:
            usage = receipt['usage_events']
            u = usage[-1]['params']['tokenUsage']['total'] if usage else None
            if u:
                totals['input'] += u['inputTokens']
                totals['cached'] += u['cachedInputTokens']
                totals['output'] += u['outputTokens']
                totals['reasoning_output'] += u['reasoningOutputTokens']
            elif receipt.get('transmitted_model'):
                missing += 1
            calls.append({'purpose': purpose, 'model': receipt['transmitted_model'], 'effort': receipt['transmitted_effort'],
                          'status': receipt['status'], 'usage': u,
                          'seconds': receipt['elapsed_seconds'], 'observed_model': None})
        classification = report.get('classification', {}).get('classification', {})
        d1_file = path.parent / f'{case}-step{step}-d1.json'
        if not classification and d1_file.exists():
            classification = json.loads(d1_file.read_text()).get('classification', {})
        attempts = report.get('result', {}).get('attempts', [])
        rows.append({'batch': path.parent.name, 'case': raw['case'], 'step': raw['step'],
                     'arm': raw['arm'], 'status': report['status'], 'wall_seconds': raw['elapsed_seconds'],
                     'tokens': totals, 'missing_usage_calls': missing, 'calls': calls,
                     'accounting_complete': missing == 0 and report['status'] not in ('error', 'missing_result'),
                     'd1_runtime': classification.get('runtime'),
                     'd1_usage': (classification.get('response') or {}).get('usage'),
                     'd1_status': (classification.get('route') or {}).get('result'),
                     'task_sequence': [{'model': a['route']['native_receipt']['transmitted_model'],
                                        'effort': a['route']['native_receipt']['transmitted_effort'],
                                        'quality': a.get('quality', {}).get('verdict'),
                                        'reason': a.get('escalation_reason')} for a in attempts
                                       if (a.get('route', {}).get('native_receipt') or {}).get('transmitted_model')],
                     'answer': report.get('result', {}).get('text'),
                     'candidate': report.get('result', {}).get('candidate')})
    return {'rows': rows, 'note': 'Partial token totals if missing_usage_calls > 0. Reasoning is included in output, never added twice. Quality acceptance is a model label, not an independent benchmark verdict.'}


def run_batch(cases, arm, folder, binary):
    ctx = mp.get_context('spawn')
    barrier = ctx.Barrier(5)
    folder.mkdir()
    before = d1_control.status()
    started = time.monotonic()
    processes = [ctx.Process(target=agent, args=(case, arm, barrier, folder, binary)) for case in cases]
    try:
        for process in processes:
            process.start()
        deadline = time.monotonic() + 1200
        while any(p.is_alive() for p in processes):
            if time.monotonic() > deadline:
                raise TimeoutError('Batch deadline')
            for process in processes:
                process.join(timeout=.1)
    finally:
        for process in processes:
            if process.is_alive():
                process.terminate()
        for process in processes:
            process.join(timeout=10)
            if process.is_alive():
                process.kill()
                process.join()
        write(folder / 'batch.json', {'arm': arm, 'seconds': round(time.monotonic() - started, 6),
                                    'exitcodes': [p.exitcode for p in processes],
                                    'service_before': before, 'service_after': d1_control.status()})


def main():
    def terminate(_signum, _frame):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, terminate)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cases', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--binary', required=True)
    parser.add_argument('--execute', action='store_true')
    parser.add_argument('--rounds', type=int, choices=(1, 2), default=1)
    args = parser.parse_args()
    cases = json.loads(args.cases.read_text())['cases']
    if len(cases) != 5 or len({c['id'] for c in cases}) != 5 or any(len(c['steps']) != 2 for c in cases):
        parser.error('Exactly five unique cases, two steps each')
    cfg = configuration()
    for case in cases:
        for step in case['steps']:
            plan = F.native_plan(cfg, **step['baseline'])
            if plan['status'] != 'executable':
                parser.error('Manual baseline unavailable')
    if not args.execute:
        print(json.dumps({'status': 'preflight_only', 'cases': len(cases), 'rounds': args.rounds,
                          'arms': ['d1', 'd1_luna', 'luna', 'none'],
                          'tasks_per_arm': 10 * args.rounds, 'model_calls': 0}))
        return
    arms = ['d1', 'd1_luna', 'luna', 'none']
    order = [f'r{n+1}-{arm}' for n in range(args.rounds)
             for arm in (arms if n % 2 == 0 else list(reversed(arms)))]
    args.output.mkdir(parents=True, exist_ok=False)
    price_bytes = (ROOT/'knowledge/prices.yaml').read_bytes()
    (args.output/'prices.yaml').write_bytes(price_bytes)
    write(args.output / 'manifest.json', {
        'prices_sha256': hashlib.sha256(price_bytes).hexdigest(),
        'started_utc': datetime.now(timezone.utc).isoformat(), 'cases_sha256': hashlib.sha256(args.cases.read_bytes()).hexdigest(),
        'code_sha256': {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
                        for p in [Path(__file__), ROOT/'tools/d1_service.py', ROOT/'tools/d1_engine.py', ROOT/'tools/native_codex.py', ROOT/'tools/document_flow.py', ROOT/'tools/adaptive_execution.py', ROOT/'tools/calculation_checks.py']},
        'order': order, 'rounds': args.rounds, 'classifier_only_ablation': True,
        'quality_max_attempts': 2, 'd1_deadline_seconds': 120, 'native_deadline_seconds': 120,
        't4': 'denied', 'billing': 'native subscription; token-based API-equivalent separate from actual charges',
        'cases': cases})
    try:
        for label in order:
            arm = label.split('-', 1)[1]
            print('START ' + label, flush=True)
            run_batch(cases, arm, args.output / label, args.binary)
            print('DONE ' + label, flush=True)
    finally:
        write(args.output / 'summary.json', summarize(args.output))


if __name__ == '__main__':
    main()

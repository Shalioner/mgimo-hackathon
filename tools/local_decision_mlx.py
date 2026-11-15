#!/usr/bin/env python3
"""Pinned official d1-2B MLX 8bit System One lab; offline inference, no fallback."""
from __future__ import annotations

import argparse
import contextlib
import json
from pathlib import Path
import sys
import time

import local_decision as base

REPO = 'LiquidAI/d1-2B-MLX-8bit'
REVISION = '3247d0483b11e5da31fa10e7979c23252fbe3580'
WEIGHTS = base.LAB / 'mlx-weights' / REVISION
MANIFEST = Path(__file__).with_name('local_decision_mlx_manifest.json')


def phase(name, mx=None):
    """Flush before each expensive stage so a killed child leaves diagnostic evidence."""
    import datetime
    import os
    import resource
    import subprocess
    rss, rss_error = '', None
    try:
        rss = subprocess.run(['/bin/ps', '-o', 'rss=', '-p', str(os.getpid())],
                             capture_output=True, text=True, timeout=5).stdout.strip()
    except (OSError, subprocess.SubprocessError) as exc:
        rss_error = type(exc).__name__  # sandbox may deny ps; diagnostics must not abort inference
    record = {'phase': name, 'timestamp': datetime.datetime.now(datetime.timezone.utc).isoformat(),
              'monotonic_seconds': time.monotonic(), 'rss_kib': int(rss) if rss.isdigit() else None,
              'max_rss_bytes': resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
              'rss_probe_error': rss_error}
    if mx is not None:
        record.update(metal_active_bytes=mx.get_active_memory(), metal_peak_bytes=mx.get_peak_memory())
    print('D1_PHASE ' + json.dumps(record), file=sys.stderr, flush=True)


def worker(payload):
    import resource
    import importlib.metadata
    directory = Path(payload['weights'])
    phase('hash_start')
    base.verify_weights(directory, MANIFEST)
    phase('hash_done')
    phase('import_start')
    from PIL import Image
    import mlx.core as mx
    from mlx_vlm import load, utils
    phase('import_done', mx)
    base.require(importlib.metadata.version('mlx-vlm') == '0.7.11', 'Reviewed mlx-vlm 0.7.11 required')
    base.require(mx.metal.is_available(), 'Metal unavailable; no CPU/cloud fallback')
    start = time.monotonic()
    phase('load_start', mx)
    original_processor_loader = utils.load_processor
    def traced_processor(*args, **kwargs):
        phase('load_weights_done', mx)
        phase('processor_start', mx)
        result = original_processor_loader(*args, **kwargs)
        phase('processor_done', mx)
        return result
    utils.load_processor = traced_processor
    try:
        model, processor = load(str(directory), revision=REVISION, lazy=False, strict=True,
                                local_files_only=True, trust_remote_code=False)
    finally:
        utils.load_processor = original_processor_loader
    mx.synchronize()
    phase('load_done', mx)
    load_seconds = time.monotonic() - start
    base.require(model.__class__.__module__.endswith('d1_lfm3_vl.d1'), 'Expected custom d1 System One model')
    base.require(model.engine.calibration is not None, 'Pinned calibration not attached')
    loaded_bytes = mx.get_active_memory()
    print(f'Loaded d1 MLX: active={loaded_bytes}, peak={mx.get_peak_memory()}', file=sys.stderr, flush=True)
    # Cap the actual packed row, including templates and image placeholders, before forward.
    original_hidden = model.d1_hidden
    def bounded_hidden(ids, *args, **kwargs):
        base.require(len(ids) <= 2048, 'Packed input exceeds 2048-token lab limit; no truncation')
        return original_hidden(ids, *args, **kwargs)
    model.d1_hidden = bounded_hidden
    results = []
    for case in payload['cases']:
        base.validate_request(case['state'], case['questions'])
        encoded = processor.tokenizer.encode(json.dumps([case['state'], case['questions']], ensure_ascii=False))
        base.require(len(encoded) <= 1024, 'Text exceeds 1024-token lab limit; no truncation')
        media, image_info = None, None
        if case.get('image'):
            path = Path(case['image'])
            base.require(path.is_file() and path.stat().st_size <= 8*1024*1024, 'Local image <=8 MiB required')
            with Image.open(path) as source:
                base.require(source.width*source.height <= 16_000_000, 'Image exceeds 16M pixels')
                image = source.convert('RGB')
            original = image.size
            image.thumbnail((384, 384))
            media, image_info = [image], {'original_size': original, 'inference_size': image.size}
        began = time.monotonic()
        phase(case['id'] + ':forward_start', mx)
        response = model.system_one(case['state'], case['questions'], media=media, score_readout='median')
        mx.synchronize()
        phase(case['id'] + ':forward_done', mx)
        base.validate_result(response, case['questions'])
        results.append({'id': case['id'], 'response': response, 'seconds': round(time.monotonic()-began, 3),
                        'image': image_info, 'mlx_active_bytes': mx.get_active_memory(),
                        'mlx_peak_bytes': mx.get_peak_memory(),
                        'max_rss_bytes': resource.getrusage(resource.RUSAGE_SELF).ru_maxrss})
        print(f"Completed {case['id']}: {json.dumps(results[-1], ensure_ascii=False)}", file=sys.stderr, flush=True)
        mx.clear_cache()
    return {'status': 'succeeded', 'repo': REPO, 'revision': REVISION, 'device': 'metal',
            'quantization': '8bit affine group64 (language), FP16 vision/projector/norms',
            'calibration': 'pinned calibration.json', 'load_seconds': round(load_seconds, 3),
            'loaded_active_bytes': loaded_bytes, 'results': results}


def smoke_cases():
    # Exactly one text request, then image only after its validated successful response.
    from PIL import Image, ImageDraw
    picture = base.LAB / 'synthetic-red-square.png'
    picture.parent.mkdir(parents=True, exist_ok=True)
    image = Image.new('RGB', (256, 256), 'white')
    ImageDraw.Draw(image).rectangle((32, 32, 224, 224), fill='red')
    image.save(picture)
    root = Path(__file__).resolve().parents[1]
    return [
        {'id': 'routing_T3', 'state': 'Сравни три стратегии запуска продукта и разреши противоречия источников.',
         'questions': base.read_questions(root/'knowledge/decisions/route_tier.yaml')},
        {'id': 'image_red_square', 'state': 'Какого цвета квадрат?', 'image': str(picture),
         'questions': base.read_questions(root/'docs/examples/d1-image-questions.yaml')},
    ]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['download', 'run', 'smoke', '_worker'])
    parser.add_argument('--weights', type=Path, default=WEIGHTS)
    parser.add_argument('--questions', type=Path)
    inputs = parser.add_mutually_exclusive_group()
    inputs.add_argument('--state')
    inputs.add_argument('--state-file', type=Path)
    inputs.add_argument('--state-json')
    parser.add_argument('--image', type=Path)
    parser.add_argument('--timeout', type=int, default=240)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == '_worker':
            with contextlib.redirect_stdout(sys.stderr):
                report = worker(json.load(sys.stdin))
        elif args.command == 'download':
            report = base.download(args.weights, manifest_path=MANIFEST, repo=REPO, revision=REVISION)
        else:
            if args.command == 'smoke':
                cases = smoke_cases()
            else:
                base.require(args.questions is not None, '--questions required')
                state = args.state
                if args.state_file:
                    base.require(args.state_file.stat().st_size <= base.MAX_BYTES, 'State file exceeds limit')
                    state = args.state_file.read_text(encoding='utf-8')
                elif args.state_json is not None:
                    state = json.loads(args.state_json)
                base.require(state is not None or args.image is not None, 'Provide state or image')
                cases = [{'id': 'request', 'state': state, 'questions': base.read_questions(args.questions),
                          'image': str(args.image.resolve()) if args.image else None}]
            report = base.execute(cases, args.weights, args.timeout, worker_script=__file__, preflight_samples=3)
            report['phases'] = [json.loads(line[len('D1_PHASE '):]) for line in report.get('worker_stderr', '').splitlines() if line.startswith('D1_PHASE ')]
            report['last_phase'] = report['phases'][-1]['phase'] if report['phases'] else 'preflight_or_child_start'
    except (ValueError, OSError, ImportError, RuntimeError) as exc:
        report = {'status': 'blocked', 'reason': f'{type(exc).__name__}: {exc}', 'fallback': False}
    output = json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False)
    if args.output:
        args.output.write_text(output+'\n', encoding='utf-8')
    print(output)
    return 0 if report['status'] in {'succeeded', 'downloaded'} else 3


if __name__ == '__main__':
    raise SystemExit(main())

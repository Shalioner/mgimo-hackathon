"""Persistent pinned MLX System One engine; imported only by the isolated worker."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import time

import local_decision as base
import local_decision_mlx as pinned

MAX_STATE_BYTES = 32768
MAX_FRAME_BYTES = 65536
MAX_TEXT_TOKENS = 4096
MAX_PACKED_TOKENS = 8192


def validate_input(state, questions):
    base.require(isinstance(state, str) and bool(state.strip()), 'Nonempty text state required')
    base.require(len(json.dumps(state, ensure_ascii=False).encode('utf-8')) <= MAX_STATE_BYTES,
                 'State exceeds 32768 bytes; no truncation')
    # Reuse the reviewed question schema without the laboratory's state-size cap.
    base.validate_request('', questions)


class Engine:
    def __init__(self, weights=None, lock_file=None):
        import fcntl
        import importlib.metadata
        self.weights = Path(weights) if weights is not None else pinned.WEIGHTS
        self.model_lock = Path(lock_file or base.LAB / 'inference.lock').open('a', encoding="utf-8")
        try:
            fcntl.flock(self.model_lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError('Another local D1 worker owns the model lock')
        import mlx.core as mx
        from mlx_vlm import load
        base.require(importlib.metadata.version('mlx-vlm') == '0.7.11', 'Reviewed mlx-vlm 0.7.11 required')
        base.require(mx.metal.is_available(), 'Metal unavailable; no fallback')
        base.verify_weights(self.weights, pinned.MANIFEST)
        self.mx = mx
        self.model, self.processor = load(str(self.weights), revision=pinned.REVISION,
            lazy=False, strict=True, local_files_only=True, trust_remote_code=False)
        mx.synchronize()
        base.require(self.model.__class__.__module__.endswith('d1_lfm3_vl.d1'), 'Unexpected model architecture')
        base.require(self.model.engine.calibration is not None, 'Pinned calibration missing')
        original = self.model.d1_hidden
        def bounded(ids, *args, **kwargs):
            base.require(len(ids) <= MAX_PACKED_TOKENS, 'Packed input exceeds 8192 tokens; no truncation')
            return original(ids, *args, **kwargs)
        self.model.d1_hidden = bounded

    def infer(self, state, questions):
        started = time.monotonic()
        validate_input(state, questions)
        encoded = self.processor.tokenizer.encode(json.dumps([state, questions], ensure_ascii=False))
        base.require(len(encoded) <= MAX_TEXT_TOKENS, 'Input exceeds 4096 tokens; no truncation')
        prepared = time.monotonic()
        response = self.model.system_one(state, questions, score_readout='median')
        self.mx.synchronize()
        computed = time.monotonic()
        base.validate_result(response, questions)
        self.mx.clear_cache()
        self.last_timing = {'preparation_seconds': prepared-started,
                            'model_seconds': computed-prepared,
                            'validation_cleanup_seconds': time.monotonic()-computed}
        return response


def worker(argv=None):
    import contextlib
    import os
    import threading
    import time
    parent = os.getppid()
    def parent_watch():
        while True:
            time.sleep(1)
            if os.getppid() != parent:
                os._exit(0)
    threading.Thread(target=parent_watch, name='d1-parent-watch', daemon=True).start()
    parser = argparse.ArgumentParser()
    parser.add_argument('--weights', required=True, type=Path)
    parser.add_argument('--lock-file', required=True, type=Path)
    args = parser.parse_args(argv)
    # Imported libraries must not contaminate the NDJSON protocol or log sources.
    try:
        with open('/dev/null', 'w', encoding="utf-8") as quiet, contextlib.redirect_stdout(quiet), contextlib.redirect_stderr(quiet):
            engine = Engine(weights=args.weights, lock_file=args.lock_file)
    except Exception as exc:
        known = ('Missing regular pinned file:', 'Pinned SHA256 mismatch:', 'Unreviewed file in weights',
                 'Another local D1', 'Reviewed mlx-vlm', 'Metal unavailable', 'Unexpected model architecture',
                 'Pinned calibration')
        reason = type(exc).__name__
        if isinstance(exc, ValueError) and str(exc).startswith(known):
            reason += ': ' + str(exc)
        print(json.dumps({'event': 'failed', 'phase': 'startup', 'reason': reason}), flush=True)
        return
    print(json.dumps({'event': 'ready', 'model': pinned.REPO, 'revision': pinned.REVISION}), flush=True)
    while True:
        line = sys.stdin.buffer.readline(MAX_FRAME_BYTES + 1)
        if not line:
            return
        if len(line) > MAX_FRAME_BYTES or not line.endswith(b'\n'):
            return
        message = json.loads(line)
        if message.get('op') == 'stop':
            return
        started = time.monotonic()
        try:
            with open('/dev/null', 'w', encoding="utf-8") as quiet, contextlib.redirect_stdout(quiet), contextlib.redirect_stderr(quiet):
                answer = engine.infer(message['state'], message['questions'])
            result = {'event': 'result', 'id': message['id'], 'response': answer,
                      'engine_timing': getattr(engine, 'last_timing', {}),
                      'inference_seconds': round(time.monotonic()-started, 6)}
        except ValueError as exc:
            # Input/shape validators never interpolate source content.
            result = {'event': 'rejected', 'id': message.get('id'), 'reason': str(exc)}
        except Exception as exc:
            result = {'event': 'failed', 'id': message.get('id'), 'reason': type(exc).__name__}
        print(json.dumps(result, ensure_ascii=False, allow_nan=False), flush=True)


if __name__ == '__main__':
    for _stream in (sys.stdin, sys.stdout, sys.stderr):  # Windows: pipes/console default to cp1251
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    worker()

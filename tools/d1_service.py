#!/usr/bin/env python3
"""Private UNIX-socket D1 service with one warm isolated MLX worker and bounded queue."""
from __future__ import annotations

import argparse
from collections import OrderedDict
import copy
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import queue
import signal
import socket
import socketserver
import stat
import subprocess
import sys
import threading
import time
import uuid

import d1_engine as engine
import local_decision as base
import local_decision_mlx as pinned

DEFAULT_SOCKET = Path.home() / '.cache/strategist/d1-service/service.sock'
PYTHON = base.LAB / 'mlx-venv/bin/python'
MAX_REPLY_BYTES = 131072


class RequestCancelled(Exception):
    """Internal cooperative cancellation; never treated as a worker crash."""



def private_directory(path, create=False):
    path = Path(path)
    if create:
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or path.is_symlink() or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise ValueError('Socket directory must be owned by current user with mode 0700')


def request(socket_path, payload, timeout=240):
    """One request, no retries. The caller owns the request ID across uncertain results."""
    started = time.monotonic()
    path = Path(socket_path)
    private_directory(path.parent)
    info = path.lstat()
    if not stat.S_ISSOCK(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise ValueError('Expected private owned UNIX socket')
    data = (json.dumps(payload, ensure_ascii=False, allow_nan=False) + '\n').encode('utf-8')
    if len(data) > engine.MAX_FRAME_BYTES:
        raise ValueError('Request exceeds 65536 bytes')
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.settimeout(timeout)
        client.connect(str(path))
        client.sendall(data)
        with client.makefile('rb') as stream:
            raw = stream.readline(MAX_REPLY_BYTES + 1)
    if not raw.endswith(b'\n') or len(raw) > MAX_REPLY_BYTES:
        raise ValueError('Invalid service response frame')
    result = json.loads(raw)
    if type(result) is not dict:
        raise ValueError('Invalid service response')
    if payload.get('op') == 'infer':
        result.setdefault('runtime', {}).setdefault('timing', {})['client_roundtrip_seconds'] = round(time.monotonic()-started, 6)
    return result


def sample_memory(pid=None):
    result = base.memory()
    result['rss_kib'] = None
    if pid:
        raw = subprocess.run(['/bin/ps', '-o', 'rss=', '-p', str(pid)], capture_output=True,
                             text=True, timeout=3, encoding="utf-8").stdout.strip()
        result['rss_kib'] = int(raw) if raw.isdigit() else None
    return result


def memory_problem(sample, baseline=None):
    pressure, swap = sample.get('pressure_level'), sample.get('swap_used_mib')
    if pressure is None or swap is None:
        return 'Memory telemetry unavailable'
    if pressure >= 4:
        return 'Critical system memory pressure'
    if swap > 8192:
        return 'Total system swap exceeds 8 GiB'
    if sample.get('rss_kib') is not None and sample['rss_kib'] > 6*1024*1024:
        return 'Worker RSS exceeds 6 GiB'
    if baseline is not None and swap - baseline > 3072:
        return 'System swap growth exceeds 3 GiB during worker lifetime'
    return None


class Supervisor:
    def __init__(self, *, idle_seconds=300, queue_size=4, cooldown_seconds=30,
                 worker_factory=None, memory_probe=sample_memory,
                 cache_entries=128, cache_ttl=300):
        if type(cache_entries) is not int or not 0 <= cache_entries <= 128:
            raise ValueError('cache_entries must be 0..128')
        if type(cache_ttl) not in (int, float) or not math.isfinite(cache_ttl) or not 0 < cache_ttl <= 300:
            raise ValueError('cache_ttl must be finite and within (0,300] seconds')
        self.cache_entries, self.cache_ttl = cache_entries, cache_ttl
        self.cache = OrderedDict()
        self.cache_hits = 0
        self.cache_shared = 0
        self.cache_namespace = (pinned.REPO, pinned.REVISION,
                                hashlib.sha256(pinned.MANIFEST.read_bytes()).hexdigest(),
                                'mlx-vlm-0.7.11:median:v1')
        self.idle_seconds = idle_seconds
        self.cooldown_seconds = cooldown_seconds
        self.memory_probe = memory_probe
        self.worker_factory = worker_factory or self._spawn
        # Admit one active + queue_size waiting even before the loop dequeues.
        self.queue_limit = queue_size
        self.admission = threading.BoundedSemaphore(queue_size + 1)
        self.jobs = queue.Queue(maxsize=queue_size + 1)
        self.lock = threading.RLock()
        self.stopping = threading.Event()
        self.proc = None
        self.lines = None
        self.model_state = 'idle'
        self.reason = None
        self.generation = 0
        self.completed = 0
        self.failed = 0
        self.cancelled = 0
        self.pending = {}
        self.active_id = None
        self.last_latency = None
        self.last_used = time.monotonic()
        self.retry_after = 0
        self.baseline_swap = None
        self.last_sample = 0
        self.last_memory = {}
        self.seen = OrderedDict()
        self.thread = threading.Thread(target=self._loop, name='d1-supervisor', daemon=True)
        self.thread.start()

    @staticmethod
    def _spawn():
        if sys.platform != 'darwin' or not PYTHON.is_file():
            raise ValueError('Pinned MLX Python and macOS required')
        cmd = ['/usr/bin/sandbox-exec', '-p', '(version 1)(allow default)(deny network*)',
               str(PYTHON), str(Path(engine.__file__).resolve()),
               '--weights', str(pinned.WEIGHTS.resolve()),
               '--lock-file', str((base.LAB / 'inference.lock').resolve())]
        env = {'PATH': '/usr/bin:/bin', 'HOME': str(base.LAB), 'PYTHONNOUSERSITE': '1',
               'HF_HUB_OFFLINE': '1', 'TRANSFORMERS_OFFLINE': '1', 'HF_HUB_DISABLE_TELEMETRY': '1',
               'TOKENIZERS_PARALLELISM': 'false'}
        return subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                cwd=str(base.LAB), env=env, start_new_session=True)

    def status(self):
        with self.lock:
            return {'status': 'ok', 'service': 'stopping' if self.stopping.is_set() else 'running',
                    'pid': os.getpid(), 'model_state': self.model_state, 'model': pinned.REPO,
                    'revision': pinned.REVISION, 'worker_pid': self.proc.pid if self.proc and self.proc.poll() is None else None,
                    'generation': self.generation, 'completed': self.completed, 'failed': self.failed,
                    'cancelled': self.cancelled,
                    'reuse': {'entries': len(self.cache), 'capacity': self.cache_entries,
                              'ttl_seconds': self.cache_ttl, 'hits': self.cache_hits,
                              'queued_reuses': self.cache_shared},
                    'queue_depth': self.jobs.qsize(), 'active_id': self.active_id,
                    'last_latency_seconds': self.last_latency, 'reason': self.reason,
                    'retry_after_seconds': max(0, round(self.retry_after-time.monotonic(), 1)),
                    'limits': {'state_bytes': engine.MAX_STATE_BYTES, 'frame_bytes': engine.MAX_FRAME_BYTES,
                               'text_tokens': engine.MAX_TEXT_TOKENS, 'packed_tokens': engine.MAX_PACKED_TOKENS,
                               'queue': self.queue_limit, 'idle_seconds': self.idle_seconds}}

    def submit(self, message):
        received = time.monotonic()
        if self.stopping.is_set():
            return {'status': 'blocked', 'reason': 'Service stopping'}
        ident = message.get('id')
        duration = message.get('timeout', 240)
        try:
            if not isinstance(ident, str) or not 1 <= len(ident) <= 80:
                raise ValueError('Request id required (1..80 characters)')
            if type(duration) not in (int, float) or not math.isfinite(duration) or not 0 < duration <= 300:
                raise ValueError('Timeout must be finite and within (0,300] seconds')
            engine.validate_input(message.get('state'), message.get('questions'))
            if type(message.get('reuse', True)) is not bool:
                raise ValueError('reuse must be boolean')
        except (ValueError, TypeError) as exc:
            return {'status': 'invalid_request', 'id': ident, 'reason': str(exc)}
        now = time.monotonic()
        job = {'id': ident, 'state': message['state'], 'questions': message['questions'],
               'deadline': now+duration, 'received': received, 'enqueued': now,
               'started': None, 'setup_done': None, 'inference_sent': None,
               'worker_inference_seconds': None, 'done': threading.Event(), 'result': None,
               'cancel_requested': threading.Event(), 'phase': 'queue', 'execution_complete': False}
        # Preserve question/option order: packing order may affect probabilities.
        job['cache_key'] = hashlib.sha256(json.dumps(
            [self.cache_namespace, job['state'], job['questions']],
            ensure_ascii=False, allow_nan=False).encode()).hexdigest() if message.get('reuse', True) else None
        with self.lock:
            if ident in self.pending or ident in self.seen:
                return {'status': 'duplicate', 'id': ident, 'reason': 'Request ID already submitted; no replay'}
            if now < self.retry_after:
                return {'status': 'blocked', 'id': ident, 'reason': self.reason,
                        'retry_after_seconds': round(self.retry_after-now, 1)}
            cached = self._cached(job, queued=False)
            if cached is not None:
                self.seen[ident] = now
                while len(self.seen) > 1024:
                    self.seen.popitem(last=False)
                return self._with_timing(job, cached)
            if not self.admission.acquire(blocking=False):
                return {'status': 'busy', 'id': ident, 'reason': 'Inference queue full'}
            try:
                self.jobs.put_nowait(job)
            except queue.Full:
                self.admission.release()
                return {'status': 'busy', 'id': ident, 'reason': 'Inference queue full'}
            self.pending[ident] = job
            self.seen[ident] = now
            while len(self.seen) > 1024:
                self.seen.popitem(last=False)
        if not job['done'].wait(max(0, job['deadline']-time.monotonic())):
            return self._with_timing(job, {'status': 'timeout', 'id': ident,
                'reason': 'Request deadline includes queue and load time'}, complete=False)
        return job['result']

    def _cached(self, job, *, queued):
        """Caller holds lock; reuse only validated successes, never caller IDs/timing."""
        now = time.monotonic()
        for key in list(self.cache):
            if self.cache[key][0] <= now:
                del self.cache[key]
        key = job['cache_key']
        if not self.cache_entries or key not in self.cache or now >= job['deadline']:
            return None
        _, source = self.cache[key]
        self.cache.move_to_end(key)
        result = copy.deepcopy(source)
        result['id'] = job['id']
        runtime = result['runtime']
        runtime['source_usage'] = result['response'].get('usage', {})
        runtime['source_engine_timing'] = runtime.pop('engine_timing', {})
        runtime['engine_timing'] = {}
        result['response']['usage'] = {'input_tokens': 0, 'output_tokens': 0}
        runtime.update(elapsed_seconds=0, inference_calls=0,
                       reuse='queued' if queued else 'cache')
        job.update(started=now, setup_done=now, cold_start=False,
                   worker_inference_seconds=0, execution_complete=True)
        self.cache_hits += 1
        self.cache_shared += int(queued)
        return result

    def cancel(self, ident):
        """Cancel exactly one accepted ID; never signal a process from the handler.

        Queued callers return immediately. Their queue slot is released when the
        supervisor consumes the cancelled entry, preserving bounded storage.
        Active cancellation is acknowledged before cleanup; the infer response
        confirms cancellation only after the owned worker has been reaped.
        """
        if not isinstance(ident, str) or not 1 <= len(ident) <= 80:
            return {'status': 'invalid_request', 'reason': 'Request id required (1..80 characters)'}
        with self.lock:
            job = self.pending.get(ident)
            if job is None or job['done'].is_set() or job['execution_complete']:
                return {'status': 'already_finished' if ident in self.seen else 'not_found', 'id': ident}
            job['cancel_requested'].set()
            if self.active_id != ident:
                job['result'] = self._with_timing(job, {'status': 'cancelled', 'id': ident,
                    'reason': 'Cancelled while queued; not executed'})
                self.cancelled += 1
                job['done'].set()
                return {'status': 'cancelled', 'id': ident, 'phase': 'queued'}
            return {'status': 'cancelling', 'id': ident, 'phase': 'active'}

    @staticmethod
    def _check_cancelled(job):
        if job is not None and job['cancel_requested'].is_set():
            raise RequestCancelled()

    def _finish_job(self, job, result):
        with self.lock:
            if not job['done'].is_set():
                job['result'] = self._with_timing(job, result)
                if result['status'] == 'cancelled':
                    self.cancelled += 1
                job['done'].set()
            self.pending.pop(job['id'], None)
            if self.active_id == job['id']:
                self.active_id = None
        self.jobs.task_done()
        self.admission.release()

    @staticmethod
    def _with_timing(job, result, *, complete=True):
        now = time.monotonic()
        start, setup, sent = (job.get(k) for k in ('started', 'setup_done', 'inference_sent'))
        timing = {
            'queue_wait_seconds': round((start if start is not None else now)-job['enqueued'], 6),
            'worker_setup_seconds': round(setup-start, 6) if setup is not None else None,
            'worker_roundtrip_seconds': round(now-sent, 6) if complete and sent is not None else None,
            'worker_inference_seconds': job['worker_inference_seconds'],
            'server_total_seconds': round(now-job['received'], 6),
            'cold_start': job.get('cold_start'),
            'completed': complete,
        }
        result.setdefault('runtime', {})['timing'] = timing
        result['runtime']['phase'] = 'complete' if result.get('status') == 'succeeded' else job.get('phase', 'queue')
        return result

    def _read_stdout(self, proc, lines):
        try:
            while True:
                raw = proc.stdout.readline(MAX_REPLY_BYTES + 1)
                if not raw:
                    break
                if len(raw) > MAX_REPLY_BYTES or not raw.endswith(b'\n'):
                    break
                lines.put_nowait(json.loads(raw))
        except (ValueError, queue.Full, OSError):
            pass
        try:
            lines.put_nowait({'event': 'eof'})
        except queue.Full:
            pass

    def _memory(self, force=False):
        now = time.monotonic()
        if force or now-self.last_sample >= 0.5:
            self.last_memory = self.memory_probe(self.proc.pid if self.proc else None)
            self.last_sample = now
        return self.last_memory

    def _stop_worker(self, reason=None, cooldown=False):
        with self.lock:
            proc = self.proc
            self.model_state = 'unloading' if proc else ('blocked' if reason else 'idle')
            self.reason = reason
        if proc:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=3)
            for stream in (proc.stdin, proc.stdout):
                if stream:
                    stream.close()
        with self.lock:
            self.proc = None
            self.lines = None
            self.model_state = 'blocked' if reason else 'idle'
            if cooldown:
                self.retry_after = time.monotonic()+self.cooldown_seconds

    def _wait_line(self, deadline, job=None):
        while True:
            self._check_cancelled(job)
            if self.stopping.is_set():
                raise RuntimeError('Service stopping')
            if time.monotonic() >= deadline:
                raise TimeoutError('Request deadline exceeded')
            problem = memory_problem(self._memory(), self.baseline_swap)
            if problem:
                raise RuntimeError(problem)
            try:
                row = self.lines.get(timeout=min(0.2, max(0.001, deadline-time.monotonic())))
            except queue.Empty:
                if self.proc.poll() is not None:
                    raise RuntimeError('Model worker exited')
                continue
            if not isinstance(row, dict) or row.get('event') == 'eof':
                raise RuntimeError('Model worker protocol ended')
            return row

    def _ensure_worker(self, deadline, job=None):
        self._check_cancelled(job)
        if self.proc is not None and self.proc.poll() is None:
            return
        if self.proc:
            self._stop_worker('Worker exited', cooldown=False)
        sample = self._memory(force=True)
        problem = memory_problem(sample)
        if problem:
            raise RuntimeError(problem)
        self._check_cancelled(job)
        self.baseline_swap = sample['swap_used_mib']
        with self.lock:
            self.model_state = 'loading'
            self.reason = None
            self.proc = self.worker_factory()
            self.generation += 1
            self.lines = queue.Queue(maxsize=4)
            proc, lines = self.proc, self.lines
        threading.Thread(target=self._read_stdout, args=(proc, lines), daemon=True).start()
        row = self._wait_line(deadline, job)
        if row.get('event') == 'failed' and row.get('phase') == 'startup':
            raise RuntimeError('Worker startup failed: ' + str(row.get('reason', 'unknown')))
        if row.get('event') != 'ready' or row.get('model') != pinned.REPO or row.get('revision') != pinned.REVISION:
            raise RuntimeError('Unexpected worker model or readiness handshake')
        with self.lock:
            self.model_state = 'ready'

    def _perform(self, job):
        self._check_cancelled(job)
        start = time.monotonic()
        job['started'] = start
        if start >= job['deadline']:
            return {'status': 'timeout', 'id': job['id'], 'reason': 'Expired in queue; not executed'}
        with self.lock:
            self._check_cancelled(job)
            cached = self._cached(job, queued=True)
            if cached is not None:
                return cached
        job['cold_start'] = self.proc is None or self.proc.poll() is not None
        job['phase'] = 'setup'
        self._ensure_worker(job['deadline'], job)
        self._check_cancelled(job)
        job['setup_done'] = time.monotonic()
        message = {key: job[key] for key in ('id', 'state', 'questions')}
        message['op'] = 'infer'
        job['phase'] = 'inference'
        job['inference_sent'] = time.monotonic()
        self.proc.stdin.write((json.dumps(message, ensure_ascii=False)+'\n').encode('utf-8'))
        self.proc.stdin.flush()
        row = self._wait_line(job['deadline'], job)
        self._check_cancelled(job)
        if row.get('id') != job['id']:
            raise RuntimeError('Worker response ID mismatch')
        if row.get('event') == 'rejected':
            return {'status': 'invalid_request', 'id': job['id'], 'reason': row.get('reason', 'Input rejected')}
        if row.get('event') != 'result':
            raise RuntimeError('Model worker failed')
        elapsed = row.get('inference_seconds')
        if type(elapsed) in (int, float) and math.isfinite(elapsed) and elapsed >= 0:
            job['worker_inference_seconds'] = elapsed
        base.validate_result(row.get('response'), job['questions'])
        with self.lock:
            self._check_cancelled(job)
            self.last_used = time.monotonic()
            self.last_latency = round(self.last_used-start, 4)
            self.completed += 1
            job['execution_complete'] = True
            result = {'status': 'succeeded', 'id': job['id'], 'response': row['response'],
                    'runtime': {'worker_pid': self.proc.pid, 'generation': self.generation,
                                'model': pinned.REPO, 'revision': pinned.REVISION,
                                'inference_calls': 1, 'reuse': 'miss' if job['cache_key'] else 'bypass',
                                'engine_timing': {k: v for k, v in row.get('engine_timing', {}).items()
                                                  if k in {'preparation_seconds', 'model_seconds', 'validation_cleanup_seconds'}
                                                  and type(v) in (int, float) and math.isfinite(v) and v >= 0},
                                'elapsed_seconds': self.last_latency}}
            if self.cache_entries and job['cache_key'] and len(json.dumps(result).encode()) <= MAX_REPLY_BYTES:
                self.cache[job['cache_key']] = (time.monotonic()+self.cache_ttl, copy.deepcopy(result))
                self.cache.move_to_end(job['cache_key'])
                while len(self.cache) > self.cache_entries:
                    self.cache.popitem(last=False)
            return result

    def _loop(self):
        try:
            while not self.stopping.is_set():
                try:
                    job = self.jobs.get(timeout=0.2)
                except queue.Empty:
                    if self.proc:
                        try:
                            problem = memory_problem(self._memory(), self.baseline_swap)
                            if self.proc.poll() is not None:
                                problem = 'Model worker exited while idle'
                        except Exception:
                            problem = 'Memory telemetry failed'
                        if problem:
                            self._stop_worker(problem, cooldown=True)
                        elif time.monotonic()-self.last_used >= self.idle_seconds:
                            self._stop_worker()
                    continue
                with self.lock:
                    self.active_id = job['id']
                # Published job results are immutable once done is set.
                # A cancelled queued caller may still be returning from wait.
                result = {'status': 'blocked', 'id': job['id'], 'reason': 'Processing interrupted'}
                try:
                    if time.monotonic() < self.retry_after:
                        result = {'status': 'blocked', 'id': job['id'], 'reason': self.reason}
                    else:
                        result = self._perform(job)
                except RequestCancelled:
                    # Only this loop owns worker lifecycle. A queued cancellation
                    # must not unload the healthy worker used by another request.
                    if job.get('started') is not None:
                        self._stop_worker()
                    result = {'status': 'cancelled', 'id': job['id'], 'reason': 'Request cancelled'}
                except Exception as exc:
                    self.failed += 1
                    reason = str(exc) if isinstance(exc, (ValueError, RuntimeError, TimeoutError)) else type(exc).__name__
                    self._stop_worker(reason, cooldown=True)
                    result = {'status': 'timeout' if isinstance(exc, TimeoutError) else 'blocked',
                                     'id': job['id'], 'reason': reason,
                                     'runtime': {'memory': {k: self.last_memory.get(k) for k in
                                         ('pressure_level', 'swap_used_mib', 'rss_kib')}}}
                finally:
                    self._finish_job(job, result)
        finally:
            self._stop_worker()
            while True:
                try:
                    job = self.jobs.get_nowait()
                except queue.Empty:
                    break
                result = {'status': 'blocked', 'id': job['id'], 'reason': 'Service stopped'}
                self._finish_job(job, result)

    def stop(self):
        self.stopping.set()

    def close(self):
        self.stop()
        self.thread.join(timeout=7)


class Server(socketserver.ThreadingUnixStreamServer):
    daemon_threads = True
    block_on_close = False
    request_queue_size = 8

    def __init__(self, path, supervisor):
        self.supervisor = supervisor
        self.connections = threading.BoundedSemaphore(12)
        super().__init__(str(path), Handler)
        os.chmod(path, 0o600)

    def process_request(self, request, client_address):
        if not self.connections.acquire(blocking=False):
            request.close()
            return
        try:
            super().process_request(request, client_address)
        except Exception:
            self.connections.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.connections.release()

    def handle_error(self, request, client_address):
        # No traceback containing source payloads in service logs.
        pass


class Handler(socketserver.StreamRequestHandler):
    def handle(self):
        self.connection.settimeout(5)
        try:
            raw = self.rfile.readline(engine.MAX_FRAME_BYTES+1)
            if len(raw) > engine.MAX_FRAME_BYTES or not raw.endswith(b'\n'):
                raise ValueError('Invalid request frame')
            payload = json.loads(raw)
            if not isinstance(payload, dict):
                raise ValueError('Request must be an object')
            op = payload.get('op')
            if op in {'status', 'ping'}:
                result = self.server.supervisor.status()
            elif op == 'stop':
                self.server.supervisor.stop()
                result = {'status': 'stopping'}
                threading.Thread(target=self.server.shutdown, daemon=True).start()
            elif op == 'cancel':
                result = self.server.supervisor.cancel(payload.get('id'))
            elif op == 'infer':
                result = self.server.supervisor.submit(payload)
            else:
                raise ValueError('Unknown operation')
        except (ValueError, TypeError, OSError):
            result = {'status': 'invalid_request', 'reason': 'Malformed request or frame'}
        self.wfile.write((json.dumps(result, ensure_ascii=False, allow_nan=False)+'\n').encode('utf-8'))


def serve(path, *, idle_seconds=300):
    path = Path(path).absolute()
    private_directory(path.parent, create=True)
    lock_path = path.with_suffix('.lock')
    fd = os.open(lock_path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'r+') as lock:
        info = os.fstat(lock.fileno())
        if info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise ValueError('Private owned service lock required')
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError('D1 service already running')
        if path.exists() or path.is_symlink():
            info = path.lstat()
            if not stat.S_ISSOCK(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
                raise ValueError('Refusing to replace non-service socket path')
            try:
                request(path, {'op': 'ping'}, timeout=1)
            except ConnectionRefusedError:
                path.unlink()  # Owned private stale socket, exclusive lifetime lock held.
            else:
                raise ValueError('Socket still serves requests; refusing replacement')
        supervisor = Supervisor(idle_seconds=idle_seconds)
        owned_inode = None
        try:
            with Server(path, supervisor) as server:
                owned_inode = path.lstat().st_ino
                def shutdown(signum, frame):
                    supervisor.stop()
                    threading.Thread(target=server.shutdown, daemon=True).start()
                signal.signal(signal.SIGTERM, shutdown)
                signal.signal(signal.SIGINT, shutdown)
                server.serve_forever(poll_interval=0.2)
        finally:
            supervisor.close()
            if path.exists():
                current = path.lstat()
                if stat.S_ISSOCK(current.st_mode) and current.st_ino == owned_inode and current.st_uid == os.getuid():
                    path.unlink()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['serve', 'status', 'stop'])
    parser.add_argument('--socket', type=Path, default=DEFAULT_SOCKET)
    parser.add_argument('--idle-seconds', type=float, default=300)
    args = parser.parse_args(argv)
    if not math.isfinite(args.idle_seconds) or args.idle_seconds <= 0:
        parser.error('--idle-seconds must be positive and finite')
    try:
        if args.command == 'serve':
            serve(args.socket, idle_seconds=args.idle_seconds)
            return 0
        result = request(args.socket, {'op': args.command}, timeout=5)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (OSError, ValueError) as exc:
        print(json.dumps({'status': 'unavailable', 'reason': str(exc)}, ensure_ascii=False))
        return 3


if __name__ == '__main__':
    for _stream in (sys.stdin, sys.stdout, sys.stderr):  # Windows: pipes/console default to cp1251
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    raise SystemExit(main())

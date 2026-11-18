"""Service lifecycle with real UNIX sockets/subprocesses, never MLX inference."""
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time

import pytest

import d1_engine as E
import d1_service as S

QUESTIONS = {'tier': {'type': 'choice', 'instructions': 'Choose tier',
                      'criteria': {'T1': 'simple', 'T2': 'medium'}}}


def message(state='simple', timeout=3, ident=None):
    return {'op': 'infer', 'id': ident or S.uuid.uuid4().hex, 'state': state,
            'questions': QUESTIONS, 'timeout': timeout}


def wait_until(predicate, timeout=3):
    end = time.monotonic()+timeout
    while time.monotonic() < end:
        if predicate():
            return
        time.sleep(0.02)
    assert predicate()


@pytest.fixture
def setup(tmp_path):
    script = tmp_path/'worker.py'
    script.write_text('''import json,sys,time,os
print(json.dumps({'event':'ready','model':MODEL,'revision':REVISION}),flush=True)
for line in sys.stdin:
    m=json.loads(line)
    started=time.monotonic()
    if m['state']=='crash': os._exit(9)
    if m['state']=='slow': time.sleep(0.6)
    r={'answers':{'tier':{'type':'choice','choice':'T1','confidence':0.9,'probabilities':{'T1':0.9,'T2':0.1}}},'usage':{'input_tokens':10,'output_tokens':0}}
    print(json.dumps({'event':'result','id':m['id'],'response':r,'inference_seconds':time.monotonic()-started}),flush=True)
'''.replace('MODEL', repr(S.pinned.REPO)).replace('REVISION', repr(S.pinned.REVISION)))
    processes = []
    def spawn():
        proc = subprocess.Popen([sys.executable, str(script)], stdin=subprocess.PIPE,
                                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        processes.append(proc)
        return proc
    telemetry = {'pressure_level': 2, 'swap_used_mib': 1200, 'rss_kib': 100000}
    supervisor = S.Supervisor(worker_factory=spawn, memory_probe=lambda pid: dict(telemetry),
                              cooldown_seconds=0.1, idle_seconds=5, cache_entries=0)
    yield supervisor, processes, telemetry
    supervisor.close()
    assert all(p.poll() is not None for p in processes)


@pytest.fixture
def server(setup):
    supervisor, processes, telemetry = setup
    # Short AF_UNIX path also fits macOS's sockaddr_un boundary.
    with tempfile.TemporaryDirectory(prefix='d1-test-', dir='/tmp') as folder:
        socket_path = Path(folder)/'service.sock'
        with S.Server(socket_path, supervisor) as server:
            thread = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': 0.02}, daemon=True)
            thread.start()
            yield socket_path, supervisor, processes, telemetry
            server.shutdown()
            thread.join(2)


def test_reuses_loaded_worker_and_readiness(server):
    path, supervisor, processes, telemetry = server
    assert S.request(path, {'op': 'status'})['model_state'] == 'idle'
    first = S.request(path, message())
    second = S.request(path, message())
    assert first['status'] == second['status'] == 'succeeded'
    assert first['runtime']['worker_pid'] == second['runtime']['worker_pid']
    assert first['runtime']['generation'] == second['runtime']['generation'] == 1
    assert len(processes) == 1
    status = S.request(path, {'op': 'status'})
    assert status['model_state'] == 'ready' and status['completed'] == 2
    assert os.stat(path).st_mode & 0o777 == 0o600


def test_status_and_stop_do_not_wait_for_slow_inference(server):
    path, supervisor, processes, telemetry = server
    answers = []
    task = threading.Thread(target=lambda: answers.append(S.request(path, message('slow'))))
    task.start()
    wait_until(lambda: supervisor.status()['active_id'] is not None)
    start = time.monotonic()
    assert S.request(path, {'op': 'status'})['service'] == 'running'
    assert S.request(path, {'op': 'stop'})['status'] == 'stopping'
    assert time.monotonic()-start < 0.4
    task.join(3)
    assert answers[0]['status'] == 'blocked'
    wait_until(lambda: all(p.poll() is not None for p in processes))


def test_queue_deadline_does_not_execute_expired_job(setup):
    service, processes, telemetry = setup
    first = threading.Thread(target=lambda: service.submit(message('slow')))
    first.start()
    wait_until(lambda: service.status()['model_state'] == 'ready')
    expired = service.submit(message('simple', timeout=0.05))
    assert expired['status'] == 'timeout'
    first.join(3)
    wait_until(lambda: service.status()['queue_depth'] == 0)
    assert service.status()['completed'] == 1 and len(processes) == 1


def test_queue_is_bounded(setup):
    service, _, _ = setup
    first = threading.Thread(target=lambda: service.submit(message('slow')))
    first.start()
    wait_until(lambda: service.status()['active_id'])
    jobs = [threading.Thread(target=lambda: service.submit(message('simple'))) for _ in range(4)]
    for thread in jobs:
        thread.start()
    wait_until(lambda: service.status()['queue_depth'] == 4)
    assert service.submit(message())['status'] == 'busy'
    first.join(3)
    for thread in jobs:
        thread.join(3)


def test_worker_death_cooldown_and_explicit_recovery(setup):
    service, processes, _ = setup
    assert service.submit(message('crash'))['status'] == 'blocked'
    assert service.submit(message())['status'] == 'blocked'
    assert len(processes) == 1
    time.sleep(0.12)
    assert len(processes) == 1  # no automatic restart loop
    result = service.submit(message())
    assert result['status'] == 'succeeded' and result['runtime']['generation'] == 2


def test_pressure_two_allowed_critical_blocks_before_spawn(setup):
    service, processes, telemetry = setup
    telemetry['pressure_level'] = 4
    assert service.submit(message())['status'] == 'blocked'
    assert not processes
    telemetry['pressure_level'] = 2
    time.sleep(0.12)
    assert service.submit(message())['status'] == 'succeeded'


def test_watchdog_kills_only_owned_worker(setup):
    service, processes, telemetry = setup
    assert service.submit(message())['status'] == 'succeeded'
    telemetry['pressure_level'] = 4
    wait_until(lambda: service.status()['model_state'] == 'blocked')
    assert processes[0].poll() is not None
    assert 'Critical' in service.status()['reason']


def test_idle_unload_then_next_request_reloads(setup):
    service, processes, _ = setup
    service.idle_seconds = 0.1
    assert service.submit(message())['status'] == 'succeeded'
    wait_until(lambda: service.status()['model_state'] == 'idle')
    assert processes[0].poll() is not None
    result = service.submit(message())
    assert result['status'] == 'succeeded' and result['runtime']['generation'] == 2


def test_duplicate_id_never_reexecutes(server):
    path, _, processes, _ = server
    item = message(ident='same-id')
    assert S.request(path, item)['status'] == 'succeeded'
    assert S.request(path, item)['status'] == 'duplicate'
    assert len(processes) == 1


@pytest.mark.parametrize('change', [
    {'timeout': False}, {'timeout': float('nan')}, {'timeout': 301}, {'id': ''},
    {'state': ''}, {'state': 'x'*33000}, {'questions': {}}, {'questions': {'x': {'type':'unknown'}}},
])
def test_invalid_input_no_worker(setup, change):
    service, processes, _ = setup
    assert service.submit({**message(), **change})['status'] == 'invalid_request'
    assert not processes


def test_malformed_frame_and_private_socket(server):
    path, _, processes, _ = server
    with socket.socket(socket.AF_UNIX) as connection:
        connection.connect(str(path))
        connection.sendall(b'not-json\n')
        assert json.loads(connection.recv(4096))['status'] == 'invalid_request'
    assert not processes
    os.chmod(path, 0o666)
    with pytest.raises(ValueError, match='private'):
        S.request(path, {'op': 'status'})
    os.chmod(path, 0o600)


def test_state_limit_is_not_old_lab_limit():
    E.validate_input('x'*12000, QUESTIONS)
    with pytest.raises(ValueError, match='32768'):
        E.validate_input('x'*33000, QUESTIONS)


def test_engine_loads_once_and_checks_actual_tokens(tmp_path, monkeypatch):
    import types
    import importlib.metadata
    loads = []
    mx = types.ModuleType('mlx.core')
    mx.metal = types.SimpleNamespace(is_available=lambda: True)
    mx.synchronize = lambda: None
    mx.clear_cache = lambda: None
    mlx = types.ModuleType('mlx')
    mlx.core = mx
    vlm = types.ModuleType('mlx_vlm')
    def hidden(self, ids, *args, **kwargs):
        return None
    def system_one(self, state, questions, score_readout=None):
        self.d1_hidden([1]*10)
        return {'answers': {'tier': {'type': 'choice', 'choice': 'T1', 'confidence': 0.9,
                                    'probabilities': {'T1': 0.9, 'T2': 0.1}}},
                'usage': {'input_tokens': 10, 'output_tokens': 0}}
    model_type = type('Model', (), {'__module__': 'mlx_vlm.models.d1_lfm3_vl.d1',
                                   'd1_hidden': hidden, 'system_one': system_one})
    model = model_type()
    model.engine = types.SimpleNamespace(calibration=object())
    processor = types.SimpleNamespace(tokenizer=types.SimpleNamespace(encode=lambda s: list(s)))
    def load(*args, **kwargs):
        assert kwargs['trust_remote_code'] is False and kwargs['local_files_only'] is True
        loads.append(args)
        return model, processor
    vlm.load = load
    monkeypatch.setitem(sys.modules, 'mlx', mlx)
    monkeypatch.setitem(sys.modules, 'mlx.core', mx)
    monkeypatch.setitem(sys.modules, 'mlx_vlm', vlm)
    monkeypatch.setattr(importlib.metadata, 'version', lambda name: '0.7.11')
    monkeypatch.setattr(E.base, 'LAB', tmp_path)
    monkeypatch.setattr(E.base, 'verify_weights', lambda *a: None)
    service_engine = E.Engine()
    service_engine.infer('one', QUESTIONS)
    service_engine.infer('two', QUESTIONS)
    assert len(loads) == 1
    with pytest.raises(ValueError, match='4096'):
        service_engine.infer('x'*4097, QUESTIONS)
    with pytest.raises(ValueError, match='8192'):
        service_engine.model.d1_hidden([1]*8193)
    with pytest.raises(ValueError, match='lock'):
        E.Engine()
    service_engine.model_lock.close()


def test_total_swap_cap_preserved_even_at_warning_pressure():
    assert S.memory_problem({'pressure_level':2, 'swap_used_mib':12000, 'rss_kib':100}) == 'Total system swap exceeds 8 GiB'
    assert S.memory_problem({'pressure_level':2, 'swap_used_mib':2000, 'rss_kib':100}) is None


def test_real_service_process_single_instance_and_graceful_stop():
    with tempfile.TemporaryDirectory(prefix='d1-life-', dir='/tmp') as folder:
        path = Path(folder)/'s.sock'
        command = [sys.executable, str(Path(S.__file__).resolve()), 'serve', '--socket', str(path)]
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            wait_until(path.exists)
            state = S.request(path, {'op': 'status'})
            assert state['model_state'] == 'idle' and state['worker_pid'] is None
            second = subprocess.run(command, capture_output=True, text=True, timeout=3)
            assert second.returncode == 3 and 'already running' in second.stdout
            assert S.request(path, {'op':'stop'})['status'] == 'stopping'
            assert process.wait(timeout=3) == 0
            assert not path.exists()
        finally:
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=3)


def test_service_refuses_foreign_regular_socket_path():
    with tempfile.TemporaryDirectory(prefix='d1-owner-', dir='/tmp') as folder:
        path = Path(folder)/'s.sock'
        path.write_text('do not replace')
        with pytest.raises(ValueError, match='Refusing'):
            S.serve(path)
        assert path.read_text() == 'do not replace'


def test_worker_receives_explicit_paths_under_isolated_home(tmp_path):
    actual_weights = tmp_path/'actual-weights'
    actual_lock = tmp_path/'actual.lock'
    isolated_home = tmp_path/'isolated-home'
    isolated_home.mkdir()
    code = '''import sys
from pathlib import Path
sys.path.insert(0, TOOLS)
import d1_engine as e
class Fake:
    def __init__(self, weights=None, lock_file=None):
        assert weights == Path(EXPECTED_WEIGHTS)
        assert lock_file == Path(EXPECTED_LOCK)
e.Engine=Fake
e.worker()
'''.replace('TOOLS', repr(str(Path(E.__file__).parent))).replace('EXPECTED_WEIGHTS', repr(str(actual_weights))).replace('EXPECTED_LOCK', repr(str(actual_lock)))
    process = subprocess.run([sys.executable, '-c', code, '--weights', str(actual_weights),
                              '--lock-file', str(actual_lock)], input='', capture_output=True, text=True,
                              env={'HOME':str(isolated_home), 'PATH':'/usr/bin:/bin'}, timeout=5)
    assert process.returncode == 0, process.stderr
    assert json.loads(process.stdout)['event'] == 'ready'


def test_worker_reports_sanitized_startup_failure(tmp_path):
    code = '''import sys
sys.path.insert(0, TOOLS)
import d1_engine as e
class Fake:
    def __init__(self, **kwargs): raise ValueError('Pinned calibration missing')
e.Engine=Fake
e.worker()
'''.replace('TOOLS', repr(str(Path(E.__file__).parent)))
    process = subprocess.run([sys.executable, '-c', code, '--weights', str(tmp_path),
                              '--lock-file', str(tmp_path/'lock')], input='', capture_output=True,
                              text=True, timeout=5)
    result = json.loads(process.stdout)
    assert result == {'event':'failed', 'phase':'startup', 'reason':'ValueError: Pinned calibration missing'}


def test_spawn_pins_paths_before_changing_worker_home(monkeypatch):
    captured = {}
    monkeypatch.setattr(S.sys, 'platform', 'darwin')
    monkeypatch.setattr(S.Path, 'is_file', lambda self: True)
    def popen(command, **kwargs):
        captured.update(command=command, **kwargs)
        return object()
    monkeypatch.setattr(S.subprocess, 'Popen', popen)
    S.Supervisor._spawn()
    command = captured['command']
    assert command[command.index('--weights')+1] == str(S.pinned.WEIGHTS.resolve())
    assert command[command.index('--lock-file')+1] == str((S.base.LAB/'inference.lock').resolve())
    assert captured['env']['HOME'] == str(S.base.LAB)


def test_timing_separates_queue_load_inference_and_roundtrip(server):
    path, service, _, _ = server
    cold = S.request(path, message())
    assert cold['runtime']['timing']['cold_start'] is True
    slow = []
    first = threading.Thread(target=lambda: slow.append(S.request(path, message('slow'))))
    first.start()
    wait_until(lambda: service.status()['active_id'] is not None)
    waiting = S.request(path, message())
    first.join(3)
    timings = waiting['runtime']['timing']
    assert timings['queue_wait_seconds'] > .4
    assert timings['worker_inference_seconds'] < .1
    assert timings['worker_setup_seconds'] < .1
    assert timings['cold_start'] is False
    assert timings['server_total_seconds'] >= timings['queue_wait_seconds']
    assert timings['client_roundtrip_seconds'] >= timings['server_total_seconds']
    assert slow[0]['runtime']['timing']['worker_inference_seconds'] >= .59
    assert slow[0]['id'] != waiting['id']


def test_expired_wait_has_partial_timing_not_invented_inference(setup):
    service, _, _ = setup
    first = threading.Thread(target=lambda: service.submit(message('slow')))
    first.start()
    wait_until(lambda: service.status()['model_state'] == 'ready')
    response = service.submit(message(timeout=.05))
    timing = response['runtime']['timing']
    assert response['status'] == 'timeout'
    assert timing['completed'] is False
    assert timing['queue_wait_seconds'] >= .04
    assert timing['worker_inference_seconds'] is None
    assert timing['worker_setup_seconds'] is None
    assert timing['cold_start'] is None
    first.join(3)


def test_five_admitted_even_before_supervisor_dequeues(setup, monkeypatch):
    original, _, _ = setup
    gate = threading.Event()
    loop = S.Supervisor._loop
    def delayed_loop(self):
        gate.wait(3)
        loop(self)
    monkeypatch.setattr(S.Supervisor, '_loop', delayed_loop)
    service = S.Supervisor(worker_factory=original.worker_factory,
                           memory_probe=original.memory_probe)
    responses = []
    threads = [threading.Thread(target=lambda i=i: responses.append(service.submit(message(ident=f'five-{i}'))))
               for i in range(5)]
    try:
        for thread in threads: thread.start()
        wait_until(lambda: service.jobs.qsize() == 5)
        assert not responses
        assert service.submit(message())['status'] == 'busy'
        gate.set()
        for thread in threads: thread.join(3)
        assert len(responses) == 5
        assert {r['id'] for r in responses} == {f'five-{i}' for i in range(5)}
        assert all(r['status'] == 'succeeded' for r in responses)
        assert len({r['runtime']['worker_pid'] for r in responses}) == 1
        assert service.submit(message())['status'] == 'succeeded'  # slots released
    finally:
        gate.set()
        service.close()
        for thread in threads: thread.join(3)


def test_worker_measures_inference_without_exposing_input(tmp_path):
    code = """import sys,time
sys.path.insert(0, TOOLS)
import d1_engine as e
class Fake:
    def __init__(self, **kwargs): pass
    def infer(self, state, questions):
        time.sleep(.03)
        return {'answers': {}, 'usage': {'input_tokens': 1, 'output_tokens': 0}}
e.Engine=Fake
e.worker()
""".replace('TOOLS', repr(str(Path(E.__file__).parent)))
    process = subprocess.run([sys.executable, '-c', code, '--weights', str(tmp_path),
                              '--lock-file', str(tmp_path/'lock')],
                             input=json.dumps(message('PRIVATE_MARKER'))+'\n', capture_output=True,
                             text=True, timeout=5)
    assert process.returncode == 0
    ready, result = map(json.loads, process.stdout.splitlines())
    assert ready['event'] == 'ready'
    assert result['event'] == 'result'
    assert .02 <= result['inference_seconds'] < 1
    assert 'PRIVATE_MARKER' not in process.stdout + process.stderr


def test_cancel_queued_returns_without_execution_or_unloading_active(server):
    path, service, processes, _ = server
    first, queued = [], []
    active = threading.Thread(target=lambda: first.append(S.request(path, message('slow', ident='active'))))
    waiting = threading.Thread(target=lambda: queued.append(S.request(path, message(ident='queued'))))
    active.start()
    wait_until(lambda: service.status()['model_state'] == 'ready')
    waiting.start()
    wait_until(lambda: service.status()['queue_depth'] == 1)
    before = time.monotonic()
    response = S.request(path, {'op': 'cancel', 'id': 'queued'})
    waiting.join(.3)
    assert time.monotonic() - before < .4 and not waiting.is_alive()
    assert response == {'status': 'cancelled', 'id': 'queued', 'phase': 'queued'}
    assert queued[0]['status'] == 'cancelled'
    assert queued[0]['runtime']['timing']['worker_inference_seconds'] is None
    assert processes[0].poll() is None
    active.join(3)
    wait_until(lambda: service.status()['queue_depth'] == 0)
    follow = S.request(path, message())
    assert first[0]['status'] == follow['status'] == 'succeeded'
    assert first[0]['runtime']['worker_pid'] == follow['runtime']['worker_pid']
    assert service.status()['completed'] == 2 and service.status()['cancelled'] == 1
    assert not service.pending
    assert S.request(path, message(ident='queued'))['status'] == 'duplicate'


def test_cancel_active_reaps_only_owned_worker_then_serves_waiting(server):
    path, service, processes, _ = server
    active_result, queued_result = [], []
    unrelated = subprocess.Popen([sys.executable, '-c', 'import time;time.sleep(10)'])
    active = threading.Thread(target=lambda: active_result.append(S.request(path, message('slow', ident='active'))))
    waiting = threading.Thread(target=lambda: queued_result.append(S.request(path, message(ident='waiting'))))
    try:
        active.start()
        wait_until(lambda: service.pending.get('active', {}).get('phase') == 'inference')
        cancelled_pid = processes[0].pid
        waiting.start()
        wait_until(lambda: service.status()['queue_depth'] == 1)
        assert S.request(path, {'op': 'cancel', 'id': 'active'})['status'] == 'cancelling'
        active.join(3)
        waiting.join(3)
        assert active_result[0]['status'] == 'cancelled'
        assert active_result[0]['runtime']['phase'] == 'inference'
        assert processes[0].poll() is not None  # infer reply confirms reap
        assert queued_result[0]['status'] == 'succeeded'
        assert queued_result[0]['runtime']['worker_pid'] != cancelled_pid
        assert unrelated.poll() is None
        assert service.status()['failed'] == 0
        assert service.status()['cancelled'] == 1
        assert service.status()['retry_after_seconds'] == 0
        assert not service.pending
        assert S.request(path, message())['status'] == 'succeeded'
    finally:
        unrelated.terminate()
        unrelated.wait(timeout=3)
        active.join(3)
        waiting.join(3)


def test_unknown_invalid_finished_cancel_never_stops_worker(server):
    path, service, processes, _ = server
    success = S.request(path, message(ident='finished'))
    for ident in [None, '', [], 'x' * 81]:
        assert S.request(path, {'op': 'cancel', 'id': ident})['status'] == 'invalid_request'
    assert S.request(path, {'op': 'cancel', 'id': 'unknown'})['status'] == 'not_found'
    assert S.request(path, {'op': 'cancel', 'id': 'finished'})['status'] == 'already_finished'
    assert processes[0].poll() is None
    assert S.request(path, message())['runtime']['worker_pid'] == success['runtime']['worker_pid']
    assert service.status()['cancelled'] == 0


def test_cancel_loading_does_not_apply_crash_cooldown(setup, tmp_path):
    original, _, _ = setup
    script = tmp_path/'loading.py'
    script.write_text('import time;time.sleep(10)')
    children = []
    def spawn():
        proc = subprocess.Popen([sys.executable, str(script)], stdin=subprocess.PIPE,
                                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        children.append(proc)
        return proc
    service = S.Supervisor(worker_factory=spawn, memory_probe=original.memory_probe)
    result = []
    thread = threading.Thread(target=lambda: result.append(service.submit(message(ident='loading'))))
    try:
        thread.start()
        wait_until(lambda: service.status()['model_state'] == 'loading')
        assert service.cancel('loading')['status'] == 'cancelling'
        thread.join(3)
        assert result[0]['status'] == 'cancelled'
        assert result[0]['runtime']['phase'] == 'setup'
        assert children[0].poll() is not None
        assert service.status()['failed'] == 0 and service.status()['retry_after_seconds'] == 0
        service.worker_factory = original.worker_factory
        assert service.submit(message())['status'] == 'succeeded'
    finally:
        service.close()
        thread.join(3)


def test_cancel_completed_execution_race_returns_already_finished(setup):
    service, _, _ = setup
    # A result has committed but the submitting handler has not been released.
    # Late cancellation must not claim it won or kill a healthy worker.
    job = {'done': threading.Event(), 'execution_complete': True}
    with service.lock:
        service.pending['committed'] = job
        service.seen['committed'] = time.monotonic()
    try:
        assert service.cancel('committed') == {'status': 'already_finished', 'id': 'committed'}
    finally:
        service.pending.pop('committed')


def test_resource_failure_reports_phase_and_numeric_memory_without_state(setup):
    service, processes, telemetry = setup
    telemetry['pressure_level'] = 4
    result = service.submit(message('PRIVATE_INPUT_MARKER'))
    assert result['status'] == 'blocked' and not processes
    assert result['runtime']['phase'] == 'setup'
    assert result['runtime']['memory']['pressure_level'] == 4
    assert 'PRIVATE_INPUT_MARKER' not in json.dumps(result)


@pytest.mark.parametrize('after_cancel', ['dequeue', 'cooldown', 'shutdown'])
def test_queued_cancel_result_is_immutable_for_delayed_waiter(setup, monkeypatch, after_cancel):
    service, _, _ = setup
    release_waiter = threading.Event()
    waiter_paused = threading.Event()
    class DelayedEvent(threading.Event):
        def wait(self, timeout=None):
            result = super().wait(timeout)
            if result:
                waiter_paused.set()
                release_waiter.wait(3)
            return result
    original_put = service.jobs.put_nowait
    def put(job):
        if job['id'] == 'queued-delayed':
            job['done'] = DelayedEvent()
        original_put(job)
    monkeypatch.setattr(service.jobs, 'put_nowait', put)
    active_results, queued_results = [], []
    active = threading.Thread(target=lambda: active_results.append(service.submit(message('slow', ident='active-delayed'))))
    waiting = threading.Thread(target=lambda: queued_results.append(service.submit(message(ident='queued-delayed'))))
    try:
        active.start()
        wait_until(lambda: service.pending.get('active-delayed', {}).get('phase') == 'inference')
        waiting.start()
        wait_until(lambda: service.status()['queue_depth'] == 1)
        job = service.pending['queued-delayed']
        assert service.cancel('queued-delayed')['status'] == 'cancelled'
        published = job['result']
        snapshot = json.loads(json.dumps(published))
        assert waiter_paused.wait(1)
        assert not queued_results
        if after_cancel == 'cooldown':
            with service.lock:
                service.retry_after = time.monotonic() + 5
        elif after_cancel == 'shutdown':
            service.stop()
        active.join(3)
        wait_until(lambda: 'queued-delayed' not in service.pending)
        release_waiter.set()
        waiting.join(3)
        assert queued_results[0] is published
        assert queued_results[0] == snapshot
        assert queued_results[0]['status'] == 'cancelled'
        assert queued_results[0]['runtime']['phase'] == 'queue'
        assert queued_results[0]['runtime']['timing']['worker_inference_seconds'] is None
        assert service.status()['cancelled'] == 1
        # All admission permits are restored once queue entries are consumed.
        acquired = [service.admission.acquire(blocking=False) for _ in range(5)]
        assert all(acquired)
        assert not service.admission.acquire(blocking=False)
        for _ in acquired:
            service.admission.release()
    finally:
        release_waiter.set()
        active.join(3)
        waiting.join(3)

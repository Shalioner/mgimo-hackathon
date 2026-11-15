"""Five independent callers over real sockets; fake inference, no model loading."""
from concurrent.futures import ThreadPoolExecutor
import threading

import d1_service as S
from test_d1_service import setup, server, message  # noqa: F401
from test_d1_service import wait_until


def test_five_callers_receive_only_their_own_result(server, tmp_path):
    path, _, processes, _ = server
    worker = tmp_path / 'worker.py'
    worker.write_text(worker.read_text().replace(
        "'input_tokens':10", "'input_tokens':10+int(m['state'])"))
    barrier = threading.Barrier(5)

    def call(index):
        barrier.wait(timeout=5)
        return index, S.request(path, message(str(index), timeout=10,
                                            ident=f'caller-{index}'))

    with ThreadPoolExecutor(max_workers=5) as pool:
        results = list(pool.map(call, [4, 1, 3, 0, 2]))
    for index, result in results:
        assert result['status'] == 'succeeded'
        assert result['id'] == f'caller-{index}'
        assert result['response']['usage']['input_tokens'] == 10 + index
    assert len(processes) == 1


def test_client_timeout_cancels_real_service_job_without_retry(server, monkeypatch):
    import decision_runtime as DR
    path, supervisor, _, _ = server
    original = S.request
    calls = []
    def short_request(socket_path, payload, timeout):
        calls.append(payload)
        return original(socket_path, payload, timeout=0.1 if payload['op'] == 'infer' else timeout)
    monkeypatch.setattr(S, 'request', short_request)
    cfg = {'automatic_routing': {'classifier': 'local-d1', 'local_d1': {'socket': str(path)}}}
    result = DR.infer('slow', message()['questions'], cfg, execute=True)
    assert result['route']['result'] == 'failed'
    assert result['route']['cancel_status'] in {'cancelled', 'cancelling'}
    assert [c['op'] for c in calls] == ['infer', 'cancel']
    assert calls[0]['id'] == calls[1]['id']
    wait_until(lambda: not supervisor.pending)
    assert supervisor.cancelled == 1
    assert original(path, message())['status'] == 'succeeded'

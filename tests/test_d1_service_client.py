import copy
import socket
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest

import decision_runtime as DR
import route_request as R
from test_decision_runtime import response


@pytest.fixture
def endpoint(tmp_path, monkeypatch):
    folder = tempfile.TemporaryDirectory(prefix='d1-client-', dir='/tmp')
    directory = Path(folder.name)
    path = directory / 'd1.sock'
    server = socket.socket(socket.AF_UNIX)
    server.bind(str(path))
    path.chmod(0o600)
    monkeypatch.setattr(DR.sys, 'platform', 'darwin')
    monkeypatch.setattr(DR, '_invoke', lambda *a, **kw: pytest.fail('Service must never fall back to lab subprocess'))
    calls = []
    data = {'status': 'succeeded', 'response': response(R.questions()), 'runtime': {
        'worker_pid': 123, 'generation': 1, 'model': DR.mlx.REPO, 'revision': DR.mlx.REVISION}}
    def request(socket_path, payload, timeout):
        assert socket_path == str(path)
        calls.append(payload)
        return {'id': payload['id'], **copy.deepcopy(data)}
    monkeypatch.setitem(sys.modules, 'd1_service', SimpleNamespace(request=request))
    cfg = {'automatic_routing': {'classifier': 'local-d1', 'local_d1': {'socket': str(path)}}}
    yield cfg, calls, data, path
    server.close()
    folder.cleanup()


def test_service_default_preflight_and_reused_worker(endpoint):
    cfg, calls, _, _ = endpoint
    assert DR.service_config(cfg)['mode'] == 'service'
    assert R.classify_request('Составь план', load_config=lambda: cfg)['status'] == 'pending'
    assert not calls
    for _ in range(2):
        result = R.classify_request('Составь план', execute=True, load_config=lambda: cfg)
        assert result['status'] == 'accepted'
        assert result['route']['worker_pid'] == 123
        assert result['route']['generation'] == 1
    assert len(calls) == 2 and calls[0]['id'] != calls[1]['id']


@pytest.mark.parametrize('failure', ['revision', 'probabilities', 'id', 'blocked'])
def test_invalid_service_reply_never_used(endpoint, failure):
    cfg, calls, data, _ = endpoint
    if failure == 'revision':
        data['runtime']['revision'] = 'foreign'
    elif failure == 'probabilities':
        data['response']['answers']['tier']['probabilities']['T2'] = 0.1
    elif failure == 'id':
        data['id'] = 'wrong'
    else:
        data.update(status='blocked', reason='critical memory')
    result = R.classify_request('Составь план', execute=True, load_config=lambda: cfg)
    assert result['status'] == 'needs_review' and result['labels'] is None
    assert len(calls) == 1


def test_no_service_means_no_lab_or_cloud_fallback(endpoint):
    cfg, calls, _, path = endpoint
    path.unlink()
    result = R.classify_request('Составь план', execute=True, load_config=lambda: cfg)
    assert result['status'] == 'unavailable' and not calls


def test_service_rejects_shared_socket_permissions(endpoint):
    cfg, calls, _, path = endpoint
    path.chmod(0o666)
    result = R.classify_request('Составь план', execute=True, load_config=lambda: cfg)
    assert result['status'] == 'unavailable' and not calls


def test_reuse_bypass_is_forwarded_and_invalid_flag_blocks(endpoint):
    cfg, calls, _, _ = endpoint
    cfg['automatic_routing']['local_d1']['reuse'] = False
    assert R.classify_request('Составь план', execute=True, load_config=lambda: cfg)['status'] == 'accepted'
    assert calls[0]['reuse'] is False
    cfg['automatic_routing']['local_d1']['reuse'] = 'false'
    assert R.classify_request('Составь план', execute=True, load_config=lambda: cfg)['status'] == 'unavailable'
    assert len(calls) == 1


def test_native_question_scope_only_removes_unused_capability(endpoint):
    cfg, calls, _, _ = endpoint
    R.classify_request('task', execute=True, load_config=lambda: cfg, question_scope='native')
    assert set(calls[-1]['questions']) == {'tier', 'effort'}
    R.classify_request('task', execute=True, load_config=lambda: cfg)
    assert set(calls[-1]['questions']) == {'tier', 'effort', 'capability'}
    with pytest.raises(ValueError):
        R.classify_request('task', load_config=lambda: cfg, question_scope='tier_only')


def test_larger_service_input_is_preserved_not_silently_cropped(endpoint):
    cfg, calls, _, _ = endpoint
    state = 'x' * 12000
    report = DR.infer(state, R.questions(), cfg, execute=True)
    assert report['route']['result'] == 'succeeded' and calls[0]['state'] == state
    before = len(calls)
    report = DR.infer('x'*40000, R.questions(), cfg, execute=True)
    assert report['route']['status'] == 'manual' and len(calls) == before


@pytest.mark.parametrize('failure', [TimeoutError('timeout'), ConnectionResetError('reset'), KeyboardInterrupt()])
def test_transport_interruption_cancels_only_own_uuid(endpoint, monkeypatch, failure):
    cfg, _, _, _, = endpoint
    calls = []
    def request(path, payload, timeout):
        calls.append((payload, timeout))
        if payload['op'] == 'infer':
            raise failure
        return {'status': 'cancelling', 'id': payload['id']}
    monkeypatch.setitem(sys.modules, 'd1_service', SimpleNamespace(request=request))
    if isinstance(failure, KeyboardInterrupt):
        with pytest.raises(KeyboardInterrupt):
            DR.infer('task', R.questions(), cfg, execute=True)
    else:
        report = DR.infer('task', R.questions(), cfg, execute=True)
        assert report['route']['result'] == 'failed'
        assert report['route']['cancel_status'] == 'cancelling'
    assert [p['op'] for p, _ in calls] == ['infer', 'cancel']
    assert calls[0][0]['id'] == calls[1][0]['id']
    assert calls[1][1] == 2


def test_unavailable_cancel_is_explicit_and_never_retries_inference(endpoint, monkeypatch):
    cfg, _, _, _ = endpoint
    calls = []
    def request(path, payload, timeout):
        calls.append(payload['op'])
        raise ConnectionResetError('reset')
    monkeypatch.setitem(sys.modules, 'd1_service', SimpleNamespace(request=request))
    report = DR.infer('task', R.questions(), cfg, execute=True)
    assert report['route']['cancel_status'] == 'unconfirmed'
    assert calls == ['infer', 'cancel']


@pytest.mark.parametrize('reply', [{'id': 'foreign', 'status': 'cancelled'}, {}, {'status': 'cancelled'}])
def test_cancel_reply_requires_matching_id(endpoint, monkeypatch, reply):
    cfg, _, _, _ = endpoint
    def request(path, payload, timeout):
        if payload['op'] == 'infer':
            raise TimeoutError('timeout')
        return reply
    monkeypatch.setitem(sys.modules, 'd1_service', SimpleNamespace(request=request))
    report = DR.infer('task', R.questions(), cfg, execute=True)
    assert report['route']['cancel_status'] == 'unconfirmed'

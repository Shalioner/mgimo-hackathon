"""Offline MLX contract tests; no heavyweight runtime imported."""
import json
import sys
from types import SimpleNamespace

import pytest
import local_decision_mlx as M
from test_local_decision import Q, RESULT


@pytest.mark.parametrize('invalid,oversized', [(False, False), (True, False), (False, True)])
def test_single_instance_and_stop_before_image_on_bad_text(monkeypatch, tmp_path, invalid, oversized):
    import importlib.metadata
    events = []
    monkeypatch.setitem(sys.modules, 'PIL', SimpleNamespace(Image=object()))
    mx = SimpleNamespace(metal=SimpleNamespace(is_available=lambda: True), synchronize=lambda: None,
                         get_active_memory=lambda: 100, get_peak_memory=lambda: 200, clear_cache=lambda: None)
    monkeypatch.setitem(sys.modules, 'mlx', SimpleNamespace(core=mx))
    monkeypatch.setitem(sys.modules, 'mlx.core', mx)
    class Model:
        __module__ = 'mlx_vlm.models.d1_lfm3_vl.d1'
        engine = SimpleNamespace(calibration=object())
        def d1_hidden(self, ids):
            events.append('forward')
        def system_one(self, state, questions, **kwargs):
            events.append(state)
            assert kwargs['score_readout'] == 'median'
            self.d1_hidden([1]*2049 if oversized else [1])
            return {} if invalid else RESULT
    def load(*args, **kwargs):
        events.append('load')
        assert kwargs['local_files_only'] and kwargs['trust_remote_code'] is False
        return Model(), SimpleNamespace(tokenizer=SimpleNamespace(encode=lambda _: [1]))
    monkeypatch.setitem(sys.modules, 'mlx_vlm', SimpleNamespace(load=load, utils=SimpleNamespace(load_processor=lambda *a, **kw: None)))
    monkeypatch.setattr(importlib.metadata, 'version', lambda _: '0.7.11')
    monkeypatch.setattr(M.base, 'verify_weights', lambda *a: None)
    payload = {'weights': str(tmp_path), 'cases': [
        {'id': 'text', 'state': 'text', 'questions': Q},
        {'id': 'next', 'state': 'next', 'questions': Q}]}
    if invalid or oversized:
        with pytest.raises(ValueError):
            M.worker(payload)
        assert events == (['load', 'text'] if oversized else ['load', 'text', 'forward'])
    else:
        report = M.worker(payload)
        assert report['status'] == 'succeeded' and len(report['results']) == 2
        assert events == ['load', 'text', 'forward', 'next', 'forward']


def test_cli_uses_same_supervisor_and_no_fallback(monkeypatch, tmp_path, capsys):
    q = tmp_path/'q.json'
    q.write_text(json.dumps(Q), encoding="utf-8")
    calls = []
    def execute(cases, directory, timeout, **kwargs):
        calls.append((cases, kwargs))
        return {'status': 'blocked', 'reason': 'memory guard'}
    monkeypatch.setattr(M.base, 'execute', execute)
    assert M.main(['run', '--state', 'Сравни стратегии', '--questions', str(q)]) == 3
    assert len(calls) == 1 and calls[0][1]['worker_script'] == M.__file__
    assert json.loads(capsys.readouterr().out)['status'] == 'blocked'


def test_download_pins_official_repo_and_manifest(monkeypatch, tmp_path, capsys):
    calls = []
    def download(directory, **kwargs):
        calls.append((directory, kwargs))
        return {'status': 'downloaded'}
    monkeypatch.setattr(M.base, 'download', download)
    assert M.main(['download', '--weights', str(tmp_path)]) == 0
    assert calls == [(tmp_path, {'manifest_path': M.MANIFEST, 'repo': M.REPO, 'revision': M.REVISION})]


def test_oversized_request_never_starts_child(monkeypatch, tmp_path):
    monkeypatch.setattr(M.base.sys, 'platform', 'darwin')
    monkeypatch.setattr(M.base.subprocess, 'Popen', lambda *a, **kw: pytest.fail('Must not spawn'))
    with pytest.raises(ValueError, match='8192'):
        M.base.execute([{'id': 'bad', 'state': 'x'*8193, 'questions': Q}], tmp_path, 10,
                       worker_script=M.__file__)


def test_phase_marker_survives_sandbox_ps_denial(monkeypatch, capsys):
    import subprocess
    def denied(*args, **kwargs):
        raise PermissionError('sandbox denies ps')
    monkeypatch.setattr(subprocess, 'run', denied)
    M.phase('hash_start')
    record = json.loads(capsys.readouterr().err.removeprefix('D1_PHASE '))
    assert record['phase'] == 'hash_start' and record['rss_kib'] is None
    assert record['rss_probe_error'] == 'PermissionError' and record['max_rss_bytes'] > 0

from pathlib import Path

import pytest

import route_tools as R


def test_choice_dispatches_real_ledger_stats(monkeypatch):
    def infer(state, qs, **kwargs):
        assert kwargs['execute']
        return {'status': 'succeeded', 'route': {'started': True}, 'response': {
            'answers': {'action': {'type': 'choice', 'choice': 'stats', 'confidence': .97,
                                  'probabilities': {'brief': .01, 'stats': .97, 'validate': .01, 'none': .01}}}}}
    monkeypatch.setattr(R.T, 'infer_questions', infer)
    report = R.run('Count objects', R.L.ROOT / 'projects/example', execute=True)
    assert report['selection']['selected'] == 'stats'
    result = report['execution']
    assert result['status'] == 'succeeded' and result['result']['exit_code'] == 0
    assert result['result']['stdout'].strip()


def test_preflight_never_dispatches(monkeypatch):
    monkeypatch.setattr(R.T, 'select_action', lambda *a, **kw: {'status': 'pending'})
    monkeypatch.setattr(R.subprocess, 'run', lambda *a, **kw: pytest.fail('No command in preflight'))
    assert R.run('Count', R.L.ROOT / 'projects/example')['execution']['status'] == 'not_started'


def test_invalid_project_rejected_before_inference(tmp_path, monkeypatch):
    monkeypatch.setattr(R.T, 'select_action', lambda *a, **kw: pytest.fail('No inference'))
    with pytest.raises(ValueError, match='Existing project'):
        R.run('Count', tmp_path, execute=True)

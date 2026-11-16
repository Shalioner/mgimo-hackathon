import copy
import json
from concurrent.futures import ThreadPoolExecutor

import pytest

import decision_runtime as DR
import tool_router as T

LOCAL = {'automatic_routing': {'classifier': 'local-d1'}}
REMOTE = {'automatic_routing': {'classifier': 'provider', 'decision_provider': 'fixture'}}
CANDIDATES = [{'id': 'count', 'description': 'Count records with deterministic code'},
              {'id': 'read', 'description': 'Read an unseen source'}]


def response(questions, choice='count', confidence=.95):
    answers = {}
    for name, q in questions.items():
        if q['type'] == 'noul':
            answers[name] = {'type': 'noul', 'noul': confidence}
        else:
            probabilities = {key: confidence if key == choice else (1-confidence)/(len(q['criteria'])-1)
                             for key in q['criteria']}
            answers[name] = {'type': 'choice', 'choice': choice, 'confidence': confidence,
                             'probabilities': probabilities}
    return {'answers': answers, 'usage': {'input_tokens': 10, 'output_tokens': 0}}


@pytest.fixture
def local(monkeypatch):
    calls = []
    settings = {'choice': 'count', 'confidence': .95, 'change': lambda r: r}
    def infer(state, questions, cfg, *, execute, timeout):
        calls.append(execute)
        route = {'status': 'executable', 'started': execute, 'result': 'succeeded' if execute else 'not_started'}
        return {'route': route, **({'response': settings['change'](response(questions, settings['choice'], settings['confidence']))} if execute else {})}
    monkeypatch.setattr(DR, 'infer', infer)
    return calls, settings


def select(**kwargs):
    return T.select_action('Count these records', 'a,b,c', CANDIDATES, load_config=lambda: LOCAL, **kwargs)


def test_pure_preflight_then_real_t0_dispatch(local):
    calls, _ = local
    assert select()['status'] == 'pending'
    assert calls == [False]
    report = select(execute=True)
    assert report['status'] == 'selected'
    rows = [{'x': 1}, {'x': 2}, {'x': 3}]
    out = T.execute_selected(report, CANDIDATES, {'count': lambda: len(rows)})
    assert out == {'status': 'succeeded', 'selected': 'count', 'result': 3}
    assert T.execute_selected(copy.deepcopy(report), CANDIDATES, {'count': lambda: pytest.fail('duplicate')})['status'] == 'not_started'


def test_abstention_and_uncertainty(local):
    _, cfg = local
    cfg['choice'] = 'none'
    assert select(execute=True)['status'] == 'abstained'
    cfg.update(choice='count', confidence=.6)
    assert select(execute=True)['status'] == 'needs_review'


@pytest.mark.parametrize('damage', ['unknown', 'extra', 'nan', 'negative', 'sum', 'confidence', 'type', 'missing'])
def test_malformed_never_dispatches(local, damage):
    _, cfg = local
    def change(data):
        a = data['answers']['action']
        if damage == 'unknown': a['choice'] = 'shell'
        elif damage == 'extra': data['answers']['injected'] = dict(a)
        elif damage == 'nan': a['probabilities']['count'] = float('nan')
        elif damage == 'negative': a['probabilities']['read'] = -.1
        elif damage == 'sum': a['probabilities']['read'] = .9
        elif damage == 'confidence': a['confidence'] = .99
        elif damage == 'type': a['type'] = 'noul'
        else: del a['probabilities']
        return data
    cfg['change'] = change
    report = select(execute=True)
    assert report['status'] == 'needs_review'
    assert T.execute_selected(report, CANDIDATES, {'count': lambda: pytest.fail('invalid')})['status'] == 'not_started'


def test_changed_config_never_infers(local):
    calls, _ = local
    configs = iter([LOCAL, {**LOCAL, 'changed': True}])
    result = T.select_action('count', '', CANDIDATES, execute=True, load_config=lambda: next(configs))
    assert result['status'] == 'unavailable' and calls == [False]


def test_unknown_classifier_no_fallback():
    result = T.select_action('count', '', CANDIDATES, execute=True, load_config=lambda: {'automatic_routing': {'classifier': 'bootstrap'}})
    assert result['status'] == 'unavailable'


def test_candidate_binding_and_handler_allowlist(local):
    report = select(execute=True)
    changed = copy.deepcopy(CANDIDATES)
    changed[0]['description'] += ' changed'
    handlers = {'count': lambda: 1}
    assert T.execute_selected(report, changed, handlers)['status'] == 'not_started'
    forged = {**report, 'selected': 'read'}
    assert T.execute_selected(forged, CANDIDATES, {'read': lambda: pytest.fail('forged')})['status'] == 'not_started'
    assert T.execute_selected(report, CANDIDATES, {})['status'] == 'not_started'
    assert T.execute_selected(report, CANDIDATES, handlers)['status'] == 'succeeded'


def test_failed_handler_consumes_ticket(local):
    report = select(execute=True)
    def fail(): raise RuntimeError('private details')
    out = T.execute_selected(report, CANDIDATES, {'count': fail})
    assert out['status'] == 'failed' and out['reason'] == 'RuntimeError'
    assert T.execute_selected(report, CANDIDATES, {'count': fail})['status'] == 'not_started'


def test_concurrent_dispatch_once(local):
    report = select(execute=True)
    calls = []
    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(lambda _: T.execute_selected(report, CANDIDATES, {'count': lambda: calls.append(1)}), range(2)))
    assert calls == [1]
    assert sorted(r['status'] for r in results) == ['not_started', 'succeeded']


@pytest.fixture
def remote(monkeypatch):
    calls = []
    provider = {'name': 'fixture', 'model': 'fixture-model', 'base_url': 'http://localhost:1'}
    monkeypatch.setattr(T.C, 'pick_provider', lambda *args: provider)
    def plan(*args):
        return {'status': 'executable', 'started': False, 'requested': {'model': 'fixture-model', 'provider': 'fixture', 'effort': None}}
    monkeypatch.setattr(T.C, 'decision_plan', plan)
    def call(p, body, timeout, cfg, receipt):
        calls.append(body)
        receipt.update(started=True, transmitted={'model': p['model'], 'effort': None})
        data = response(body['questions'])
        data['model'] = p['model']
        data['usage']['output_tokens'] = 5
        return 0, data, ''
    monkeypatch.setattr(T.C, 'call', call)
    return calls


def test_remote_same_contract_allows_nonzero_output_tokens(remote):
    report = T.select_action('count', '', CANDIDATES, execute=True, load_config=lambda: REMOTE)
    assert report['status'] == 'selected' and len(remote) == 1
    assert report['route']['integrity'] == 'reported'
    assert T.execute_selected(report, CANDIDATES, {'count': lambda: 42})['result'] == 42


def test_remote_preflight_denial_no_call(remote, monkeypatch):
    monkeypatch.setattr(T.C, 'decision_plan', lambda *args: {'status': 'manual', 'reason': 'No grant'})
    report = T.select_action('count', '', CANDIDATES, execute=True, load_config=lambda: REMOTE)
    assert report['status'] == 'unavailable' and not remote


def test_noul_batch_shared_transport(local):
    questions = {'keep0': {'type': 'noul', 'instructions': 'Keep first result?'},
                 'keep1': {'type': 'noul', 'instructions': 'Keep second result?'}}
    report = T.infer_questions('state', questions, execute=True, load_config=lambda: LOCAL)
    assert report['status'] == 'succeeded'
    assert report['response']['answers']['keep0']['noul'] == .95


@pytest.mark.parametrize('candidates', [[], [{'id': 'none', 'description': 'x'}], CANDIDATES * 2,
                                      [{'id': 'x', 'description': 'x', 'command': 'touch x'}]])
def test_bad_candidates_rejected_before_inference(candidates, local):
    calls, _ = local
    with pytest.raises(ValueError):
        T.select_action('count', '', candidates, execute=True, load_config=lambda: LOCAL)
    assert calls == []


def test_remote_model_mismatch_quarantined(remote, monkeypatch):
    original = T.C.call
    def changed(*args):
        code, data, error = original(*args)
        data['model'] = 'different-model'
        return code, data, error
    monkeypatch.setattr(T.C, 'call', changed)
    report = T.select_action('count', '', CANDIDATES, execute=True, load_config=lambda: REMOTE)
    assert report['status'] == 'needs_review'
    assert report['route']['result'] == 'quarantined'


def test_remote_transport_failure_no_retry(remote, monkeypatch):
    calls = []
    def failed(*args):
        calls.append(1)
        return 503, {}, 'unavailable'
    monkeypatch.setattr(T.C, 'call', failed)
    report = T.select_action('count', '', CANDIDATES, execute=True, load_config=lambda: REMOTE)
    assert report['status'] == 'needs_review' and calls == [1]


def test_local_unavailable_no_execution(local, monkeypatch):
    calls = []
    def unavailable(*args, **kwargs):
        calls.append(kwargs['execute'])
        return {'route': {'status': 'manual', 'reason': 'socket unavailable'}}
    monkeypatch.setattr(DR, 'infer', unavailable)
    assert select(execute=True)['status'] == 'unavailable'
    assert calls == [False]


def test_cli_execute_only_selects(local, tmp_path, capsys):
    request, candidates = tmp_path / 'request.txt', tmp_path / 'candidates.json'
    request.write_text('count records', encoding="utf-8")
    candidates.write_text(json.dumps(CANDIDATES), encoding="utf-8")
    # main uses the configured default; substitute selection to prove no dispatch.
    report = select(execute=True)
    original = T.select_action
    try:
        T.select_action = lambda *args, **kwargs: report
        assert T.main(['--request-file', str(request), '--candidates-file', str(candidates), '--execute']) == 0
    finally:
        T.select_action = original
    assert json.loads(capsys.readouterr().out)['status'] == 'selected'
    # CLI did not consume the ticket or call a tool.
    assert T.execute_selected(report, CANDIDATES, {'count': lambda: 7})['result'] == 7

"""Cascade contracts, no external inference or task executor."""
import copy
import json

import pytest

import tool_router as T
import subscription_classifier as S
from test_tool_router import local, remote, CANDIDATES
from test_native_codex import cfg

CASCADE = {'automatic_routing': {'classifier': 'cascade', 'decision_provider': 'fixture'},
           'execution_policy': {'mode': 'api_opt_in'}}


def run(config=CASCADE, **kwargs):
    return T.select_action('Count these records', 'a,b,c', CANDIDATES,
                           execute=True, load_config=lambda: config, **kwargs)


def test_api_stops_cascade_without_tool_execution(remote, monkeypatch):
    monkeypatch.setattr(S, 'infer_questions', lambda *a, **k: pytest.fail('no subscription'))
    result = run()
    assert result['status'] == 'selected' and result['selected_classifier'] == 'api'
    assert [a['classifier'] for a in result['attempts']] == ['api']
    assert T.execute_selected(result, CANDIDATES, {'count': lambda: 42})['result'] == 42


def test_api_denial_then_local(remote, local, monkeypatch):
    monkeypatch.setattr(T.C, 'decision_plan', lambda *a: {'status': 'denied', 'reason': 'no grant'})
    result = run()
    assert result['selected_classifier'] == 'local-decision' and not remote
    assert local[0] == [False, True]


def test_uncertain_api_falls_to_local(remote, local, monkeypatch):
    original = T.C.call
    def call(*args):
        code, data, error = original(*args)
        a = data['answers']['action']
        a.update(confidence=.6, probabilities={'count': .6, 'read': .2, 'none': .2})
        return code, data, error
    monkeypatch.setattr(T.C, 'call', call)
    assert run()['selected_classifier'] == 'local-decision'


def test_subscription_stage_restricts_policy_and_no_default(remote, monkeypatch):
    monkeypatch.setattr(T.C, 'decision_plan', lambda *a: {'status': 'denied'})
    import decision_runtime as DR
    monkeypatch.setattr(DR, 'infer', lambda *a, **k: {'route': {'status': 'manual'}})
    def infer(state, questions, **kw):
        assert kw['cfg']['execution_policy']['mode'] == 'subscription_only'
        assert 'decision_provider' not in kw['cfg']['automatic_routing']
        assert kw['load_config']() == kw['cfg']
        return {'status': 'needs_review', 'route': {'started': True}}
    monkeypatch.setattr(S, 'infer_questions', infer)
    result = run()
    assert result['status'] == 'needs_review' and result['selected'] is None
    assert [a['classifier'] for a in result['attempts']] == ['api', 'local-decision', 'subscription']


def test_revocation_after_api_never_dispatches(remote, monkeypatch):
    config = copy.deepcopy(CASCADE)
    original = T.C.call
    def call(*args):
        out = original(*args)
        config['execution_policy']['mode'] = 'subscription_only'
        return out
    monkeypatch.setattr(T.C, 'call', call)
    assert run(config)['status'] == 'unavailable'


def test_cascade_never_upgrades_api_policy(monkeypatch, local):
    config = copy.deepcopy(CASCADE)
    config['execution_policy']['mode'] = 'subscription_only'
    monkeypatch.setattr(T.C, 'pick_provider', lambda *a: {'model': 'fixture'})
    def plan(cfg, *args):
        assert cfg['execution_policy']['mode'] == 'subscription_only'
        return {'status': 'denied'}
    monkeypatch.setattr(T.C, 'decision_plan', plan)
    assert run(config)['selected_classifier'] == 'local-decision'


def test_preflight_no_execution(remote, local):
    result = T.select_action('count', '', CANDIDATES, load_config=lambda: CASCADE)
    assert result['status'] == 'pending' and not remote and not local[0]


def subscription(cfg, monkeypatch, labels, qs=None):
    cfg = copy.deepcopy(cfg)
    cfg.setdefault('automatic_routing', {}).update(bootstrap_host='codex', subscription_classifier={'enabled': True})
    cfg.setdefault('execution_policy', {})['mode'] = 'subscription_only'
    qs = qs or {'action': {'type': 'choice', 'instructions': 'choose',
                          'criteria': {'count': 'count', 'read': 'read', 'none': 'none', 'more': 'more'}}}
    def execute(binary, prompt, selection, **kw):
        assert kw['require_subscription'] is True
        assert selection['tier'] == 'T1' and selection['effort'] == 'low'
        return {'status': 'succeeded', 'transmitted_model': selection['model']}, json.dumps({'answers': labels})
    monkeypatch.setattr(S.N, 'execute', execute)
    return S.infer_questions('Count these records', qs, cfg=cfg, load_config=lambda: cfg, execute=True)


def test_subscription_arbitrary_choices_exact_distribution(cfg, monkeypatch):
    qs = {'action': {'type': 'choice', 'instructions': 'choose', 'criteria': {str(i): str(i) for i in range(8)}}}
    result = subscription(cfg, monkeypatch, {'action': {'choice': '0', 'sure': True, 'quote': 'Count these records'}}, qs)
    assert result['status'] == 'succeeded'
    assert T._valid_response(result['response'], qs)
    assert result['method'] == 'labels_with_quotes_not_calibrated_probabilities'


@pytest.mark.parametrize('label', [
    {'choice': 'count', 'sure': True, 'quote': ''},
    {'choice': 'count', 'sure': False, 'quote': 'Count these records'},
    {'type': 'choice', 'choice': 'count', 'confidence': 1, 'quote': 'Count these records'},
])
def test_untrusted_subscription_evidence_declined(cfg, monkeypatch, label):
    assert subscription(cfg, monkeypatch, {'action': label})['status'] == 'needs_review'


@pytest.mark.parametrize('quote,status', [('', 'needs_review'), ('Count these records', 'succeeded')])
def test_no_without_quote_cannot_remove_context(cfg, monkeypatch, quote, status):
    qs = {'keep': {'type': 'noul', 'instructions': 'Keep?'}}
    result = subscription(cfg, monkeypatch, {'keep': {'verdict': 'no', 'quote': quote}}, qs)
    assert result['status'] == status


def test_adapter_exception_allows_next_stage(remote, local, monkeypatch):
    monkeypatch.setattr(T.C, 'call', lambda *a: (_ for _ in ()).throw(OSError('offline')))
    assert run()['selected_classifier'] == 'local-decision'


def test_expired_total_deadline_never_accepts_late_result(remote, monkeypatch):
    now = [0.]
    monkeypatch.setattr(T.time, 'monotonic', lambda: now[0])
    original = T.C.call
    def call(*args):
        assert args[2] == 12
        out = original(*args)
        now[0] = 241.
        return out
    monkeypatch.setattr(T.C, 'call', call)
    result = run()
    assert result['status'] == 'unavailable' and 'deadline' in result['reason']


def test_all_three_stages_then_subscription_selection(remote, local, cfg, monkeypatch):
    from test_tool_router import response
    monkeypatch.setattr(T.C, 'decision_plan', lambda *a: {'status': 'denied'})
    local[1]['confidence'] = .6
    def infer(state, questions, **kw):
        return {'status': 'succeeded', 'route': {'started': True},
                'response': response(questions), 'method': 'labels_with_quotes_not_calibrated_probabilities'}
    monkeypatch.setattr(S, 'infer_questions', infer)
    result = run()
    assert result['status'] == 'selected' and result['selected_classifier'] == 'subscription'
    assert len(result['attempts']) == 3


def test_compaction_cascade_receipts_and_original_preserved(remote, local, monkeypatch):
    import tool_context
    from test_tool_context import history
    original_call = T.C.call
    def low_confidence(*args):
        code, data, error = original_call(*args)
        for answer in data['answers'].values():
            answer['noul'] = .5
        return code, data, error
    monkeypatch.setattr(T.C, 'call', low_confidence)
    local[1]['confidence'] = .05
    messages = history()
    before = copy.deepcopy(messages)
    result = tool_context.compact(messages, 'Discard obsolete output', execute=True,
                                  preserve_recent=1, load_config=lambda: CASCADE)
    assert result['status'] == 'compacted' and messages == before
    assert result['stats']['classifier_requests'] == 2
    assert [a['classifier'] for a in result['receipts'][0]['classifier_attempts']] == ['api', 'local-decision']


def test_subscription_failure_retains_entire_context(remote, local, monkeypatch):
    import tool_context
    from test_tool_context import history
    monkeypatch.setattr(T.C, 'decision_plan', lambda *a: {'status': 'denied'})
    local[1]['confidence'] = .5
    monkeypatch.setattr(S, 'infer_questions', lambda *a, **kw: {'status': 'needs_review', 'route': None})
    messages = history()
    result = tool_context.compact(messages, 'Task', execute=True, preserve_recent=1, load_config=lambda: CASCADE)
    assert result['status'] == 'unchanged' and result['messages'] == messages

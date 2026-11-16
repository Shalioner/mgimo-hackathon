import copy
import json

import pytest

import adaptive_execution as A
import routing_policy as P
from test_codex_subscription import subscription


@pytest.fixture
def flow(subscription, monkeypatch, tmp_path):
    cfg, *_ = subscription
    cfg['tasks']['alternatives'] = {'tier': 'T3'}
    cfg['families']['openai'] = {'T1': 'luna', 'T2': 'terra', 'T3': 'sol', 'T4': 'astra'}
    cfg['tiers'] = {t: {'effort': e} for t, e in [
        ('T1', ['low']), ('T2', ['medium', 'high']), ('T3', ['high', 'xhigh']), ('T4', ['high', 'xhigh'])]}
    profile = cfg['execution_policy']['codex_subscription']
    profile['models'] = ['luna', 'terra', 'sol', 'astra']
    profile['efforts'] = ['low', 'medium', 'high', 'xhigh']
    cfg['automatic_routing'] = {'quality': {'enabled': True, 'max_attempts': 6}, 't4': {
        'enabled': True, 'approval_ref': 'test-user-approval', 'max_calls': 2,
        'usage_file': str(tmp_path / 't4.json')}}
    calls = []
    def execute(cfg, host, model, prompt, effort, timeout, receipt):
        error = A.D.reserve_t4_attempt(cfg, receipt)
        assert error is None
        calls.append((model, effort))
        receipt.update(started=True, result='succeeded', transmitted={'model': model, 'effort': effort})
        return receipt, f'Answer by {model}', 'test usage'
    monkeypatch.setattr(A.D, 'execute_route', execute)
    return cfg, calls


def selected(cfg, tier, model, effort):
    result = P.plan(cfg, host='codex', provider='openai', tier=tier, model=model, effort=effort)
    result['tier'] = tier
    return result


def assessor(*verdicts):
    values = iter(verdicts)
    return lambda *args: {'status': 'accepted', 'verdict': next(values)}


def test_effort_before_model_and_next_task_returns_to_luna(flow):
    cfg, calls = flow
    result = A.execute('task', '', selected(cfg, 'T3', 'sol', 'high'), load_config=lambda: cfg,
                       assessor=assessor('revise', 'revise', 'accept'))
    assert result['status'] == 'succeeded'
    assert calls == [('sol', 'high'), ('sol', 'xhigh'), ('astra', 'high')]
    assert result['attempts'][2]['route']['t4_reservation']['calls'] == 1
    A.execute('extract next source', '', selected(cfg, 'T1', 'luna', 'low'), load_config=lambda: cfg,
              assessor=assessor('accept'))
    assert calls[-1] == ('luna', 'low')


def test_terra_is_not_skipped(flow):
    cfg, calls = flow
    result = A.execute('task', '', selected(cfg, 'T1', 'luna', 'low'), load_config=lambda: cfg,
                       assessor=assessor('revise', 'revise', 'revise', 'accept'))
    assert result['status'] == 'succeeded'
    assert calls == [('luna', 'low'), ('terra', 'medium'), ('terra', 'high'), ('sol', 'high')]


def test_missing_data_does_not_escalate(flow):
    cfg, calls = flow
    result = A.execute('task', '', selected(cfg, 'T2', 'terra', 'medium'), load_config=lambda: cfg,
                       assessor=assessor('insufficient'))
    assert result['status'] == 'needs_data' and len(calls) == 1
    assert result['text'] is None and result['candidate']


def test_transport_error_never_retries(flow, monkeypatch):
    cfg, calls = flow
    def fail(cfg, host, model, prompt, effort, timeout, receipt):
        calls.append(model)
        receipt.update(started=True, result='failed')
        return receipt, '', ''
    monkeypatch.setattr(A.D, 'execute_route', fail)
    result = A.execute('task', '', selected(cfg, 'T3', 'sol', 'high'), load_config=lambda: cfg,
                       assessor=lambda *a: pytest.fail('no verifier after failed transport'))
    assert result['status'] == 'failed' and calls == ['sol']


def test_t4_quota_shared_across_requests(flow):
    cfg, calls = flow
    for _ in range(2):
        assert A.execute('hard', '', selected(cfg, 'T4', 'astra', 'high'), load_config=lambda: cfg,
                         assessor=assessor('accept'))['status'] == 'succeeded'
    result = A.execute('hard again', '', selected(cfg, 'T4', 'astra', 'high'), load_config=lambda: cfg,
                       assessor=lambda *a: pytest.fail('quota exhausted'))
    assert result['status'] == 'manual' and 'quota' in result['reason']
    assert len(calls) == 2


def test_t4_approval_does_not_authorize_max_or_unreviewed_transport(flow):
    cfg, _ = flow
    assert selected(cfg, 'T4', 'astra', 'max')['status'] == 'denied'
    cfg['execution_policy']['codex_subscription']['enabled'] = False
    assert selected(cfg, 'T4', 'astra', 'high')['status'] == 'manual'


def test_revoked_policy_before_escalation_stops(flow):
    cfg, calls = flow
    def review(*args):
        cfg['execution_policy']['codex_subscription']['enabled'] = False
        return {'status': 'accepted', 'verdict': 'revise'}
    result = A.execute('task', '', selected(cfg, 'T3', 'sol', 'high'), load_config=lambda: cfg, assessor=review)
    assert result['status'] == 'manual' and calls == [('sol', 'high')]


def test_low_confidence_never_escalates(flow):
    cfg, calls = flow
    result = A.execute('task', '', selected(cfg, 'T1', 'luna', 'low'), load_config=lambda: cfg,
                       assessor=lambda *a: {'status': 'needs_review', 'verdict': 'revise'})
    assert result['status'] == 'needs_review' and len(calls) == 1


@pytest.mark.parametrize('bad', [True, 0, -1, 3, '2'])
def test_invalid_t4_budget_is_not_approval(flow, bad):
    cfg, _ = flow
    cfg['automatic_routing']['t4']['max_calls'] = bad
    assert selected(cfg, 'T4', 'astra', 'high')['status'] == 'denied'


def test_attempt_limit_returns_candidate_not_success(flow):
    cfg, calls = flow
    cfg['automatic_routing']['quality']['max_attempts'] = 1
    result = A.execute('task', '', selected(cfg, 'T1', 'luna', 'low'), load_config=lambda: cfg,
                       assessor=assessor('revise'))
    assert result['status'] == 'needs_review' and result['text'] is None
    assert len(calls) == 1


def test_direct_transport_cannot_bypass_t4_quota(subscription, tmp_path):
    cfg, *_ = subscription
    cfg['automatic_routing'] = {'t4': {'enabled': True, 'approval_ref': 'direct-test',
        'max_calls': 1, 'usage_file': str(tmp_path / 'quota.json')}}
    route = selected(cfg, 'T4', 'm', 'low')
    receipt, text, _ = A.D.execute_route(cfg, 'codex', 'm', 'task', 'low', 20, route)
    assert receipt['result'] == 'succeeded' and text
    assert json.loads((tmp_path / 'quota.json').read_text(encoding="utf-8"))['calls'] == 1
    route = selected(cfg, 'T4', 'm', 'low')
    assert route['status'] == 'denied'
    receipt, text, _ = A.D.execute_route(cfg, 'codex', 'm', 'task', 'low', 20, route)
    assert receipt['result'] == 'not_started' and not text


def test_large_context_uses_luna_verifier_without_d1(flow, monkeypatch):
    cfg, calls = flow
    import decision_runtime as Q
    monkeypatch.setattr(Q, 'infer', lambda *a, **kw: pytest.fail('large input must not enter d1'))
    context = 'Source evidence. ' * 1000
    def answer(cfg, host, model, prompt, effort, timeout, receipt):
        calls.append((model, effort))
        assert context in prompt
        receipt.update(result='succeeded', started=True)
        return receipt, json.dumps({'answers': {'quality': {
            'choice': 'accept', 'sure': True, 'quote': 'Source evidence.'}}}), ''
    monkeypatch.setattr(A.D, 'execute_route', answer)
    report = A.quality_check('Compare', context, 'Answer', cfg, 20)
    assert report['status'] == 'accepted' and report['verdict'] == 'accept'
    assert calls == [('luna', 'low')]


def test_unavailable_verifier_stops_before_senior(flow):
    cfg, calls = flow
    cfg['execution_policy']['codex_subscription']['models'].remove('luna')
    report = A.execute('Compare', 'x'*9000, selected(cfg, 'T3', 'sol', 'high'), load_config=lambda: cfg)
    assert report['status'] == 'manual' and not calls


def test_document_pipeline_end_to_end_uses_real_router_and_quality(subscription, tmp_path, monkeypatch):
    import document_flow as F
    import decision_runtime as Q
    cfg, *_ = subscription
    cfg['families']['openai'].update(T2='terra', T3='sol')
    cfg['tiers'].update(T2={'effort': ['medium', 'high']}, T3={'effort': ['high', 'xhigh']})
    profile = cfg['execution_policy']['codex_subscription']
    profile['models'] += ['terra', 'sol']
    profile['efforts'] += ['medium', 'high', 'xhigh']
    cfg['automatic_routing'] = {'classifier': 'local-d1', 'quality': {'enabled': True, 'max_attempts': 3}}
    files = [tmp_path / 'one.txt', tmp_path / 'two.txt']
    for index, path in enumerate(files):
        path.write_text(f'Value is {index}.\nSECRET_ORIGINAL_BACKGROUND\n' + 'unused text\n'*100, encoding="utf-8")
    calls = []
    def classify(state, questions, cfg, execute=False, timeout=600):
        assert 'SECRET_ORIGINAL_BACKGROUND' not in state and 'evidence_cards' not in state
        labels = {'tier': 'T2', 'effort': 'normal', 'capability': 'text'}
        return {'route': {'status': 'executable', 'result': 'succeeded' if execute else 'not_started'},
                'response': {'answers': {key: {'type': 'choice', 'choice': labels[key], 'confidence': 0.99}
                                          for key in questions}}}
    monkeypatch.setattr(Q, 'infer', classify)
    def transport(cfg, model, prompt, effort, timeout, receipt, human_approved=False):
        calls.append((model, effort))
        receipt.update(started=True, transmitted={'model': model, 'effort': effort})
        if len(calls) <= 2:
            payload = json.loads(prompt.rsplit('\n', 1)[-1])
            quote = payload['numbered_document'].splitlines()[0].split(': ', 1)[1]
            text = json.dumps({'topic': 'Value', 'findings': [{'kind': 'fact', 'statement': quote,
                              'quote': quote, 'start_line': 1, 'end_line': 1}],
                               'limitations': ['Small sample'], 'contradictions': []})
        elif model == 'terra':
            assert 'SECRET_ORIGINAL_BACKGROUND' not in prompt
            assert 'Value is 0.' in prompt and 'Value is 1.' in prompt
            text = 'Values differ across DOC-001:1 and DOC-002:1.'
        else:
            assert model == 'm' and 'SECRET_ORIGINAL_BACKGROUND' not in prompt
            text = json.dumps({'answers': {'quality': {'choice': 'accept', 'sure': True,
                                                       'quote': 'Values differ'}}})
        return 0, json.dumps({'type': 'turn.completed'}), '', text
    monkeypatch.setattr(A.D, 'call_codex', transport)
    report = F.run_documents('Compare values', files, ['codex'], execute=True, load_config=lambda: cfg)
    assert report['status'] == 'succeeded', report
    assert calls == [('m', 'low'), ('m', 'low'), ('terra', 'medium'), ('m', 'low')]
    assert 'Values differ' in report['result']['text']

import json

import pytest
import adaptive_execution as A
from test_adaptive_execution import flow, selected
from test_codex_subscription import subscription
from test_calculation_checks import contract


def test_t0_blocks_wrong_number_before_overconfident_model_verifier(flow):
    cfg, _ = flow
    calls, verified = [], []
    def execute(cfg, host, model, prompt, effort, timeout, route):
        calls.append((model, effort))
        assert '57.5' in prompt and 'T0 CALCULATIONS' in prompt
        return {**route, 'result': 'succeeded'}, json.dumps({'facts': {'rate': 62.5 if len(calls) == 1 else 57.5}}), []
    def verifier(request, context, text, cfg, timeout):
        assert 'T0 CALCULATIONS' in context and '57.5' in context
        verified.append(text)
        return {'status': 'accepted', 'verdict': 'accept'}
    result = A.execute('Compute', json.dumps(contract()), selected(cfg, 'T1', 'luna', 'low'),
                       load_config=lambda: cfg, assessor=verifier, route_executor=execute)
    assert result['status'] == 'succeeded'
    assert calls == [('luna', 'low'), ('terra', 'medium')]
    assert result['attempts'][0]['quality']['method'] == 'T0_calculation_check'
    assert len(verified) == 1 and '57.5' in verified[0]


def test_bad_source_contract_stops_before_model(flow):
    cfg, calls = flow
    context = contract()
    context['calculation_sources']['SRC-A']['values']['weight'] = .9
    result = A.execute('Compute', json.dumps(context), selected(cfg, 'T1', 'luna', 'low'),
        load_config=lambda: cfg, assessor=lambda *a: pytest.fail('No verifier for invalid data'))
    assert result['status'] == 'needs_data' and not calls and not result['attempts']


def test_repeated_wrong_calculation_is_never_success_at_attempt_limit(flow):
    cfg, _ = flow
    cfg['automatic_routing']['quality']['max_attempts'] = 2
    def execute(cfg, host, model, prompt, effort, timeout, route):
        return {**route, 'result': 'succeeded'}, '{"facts":{"rate":62.5}}', []
    result = A.execute('Compute', json.dumps(contract()), selected(cfg, 'T1', 'luna', 'low'),
        load_config=lambda: cfg, route_executor=execute,
        assessor=lambda *a: pytest.fail('Wrong arithmetic must not reach model verifier'))
    assert result['status'] == 'needs_review' and result['text'] is None
    assert len(result['attempts']) == 2

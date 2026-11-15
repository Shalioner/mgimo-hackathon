import copy
import json

import pytest
import document_flow as F
import host_routing as H
from test_document_flow_native import native_flow, uncertain_native


@pytest.mark.parametrize('mode,d1_calls,luna_calls,status', [
    ('d1', 1, 0, 'manual'), ('d1_luna', 1, 1, 'succeeded'),
    ('luna', 0, 1, 'succeeded'), ('none', 0, 0, 'succeeded')])
def test_four_arms_isolate_classifier_only(native_flow, uncertain_native, monkeypatch,
                                          mode, d1_calls, luna_calls, status):
    cfg, _, binary, calls, _, _, _ = native_flow
    original = H.decide
    decisions = []
    def decide(*args, **kwargs):
        decisions.append(1)
        return original(*args, **kwargs)
    monkeypatch.setattr(H, 'decide', decide)
    result = F.native_synthesis('Compare values', 'Source values.', 'Source values.',
        binary, lambda: copy.deepcopy(cfg), 120, routing_mode=mode,
        baseline={'tier': 'T2', 'effort': 'medium'})
    assert result['status'] == status
    assert len(decisions) == d1_calls
    assert sum(c['kind'] == 'classification_review' for c in calls) == luna_calls
    if status == 'succeeded':
        assert [c['kind'] for c in calls][-2:] == ['synthesis', 'verification']
        assert calls[-1]['model'] == 'gpt-6-luna'  # verifier unchanged even in none


def test_none_requires_baseline_without_any_classifier(native_flow, monkeypatch):
    cfg, _, binary, calls, _, _, _ = native_flow
    monkeypatch.setattr(H, 'decide', lambda *a, **k: pytest.fail('D1 disabled'))
    monkeypatch.setattr(F, 'native_luna_classify', lambda *a, **k: pytest.fail('Luna classifier disabled'))
    with pytest.raises(ValueError, match='baseline'):
        F.native_synthesis('Compare values', '', '', binary, lambda: cfg, 120, routing_mode='none')
    assert not calls


def test_d1_only_confident_route_keeps_same_quality(native_flow, monkeypatch):
    cfg, _, binary, calls, _, _, _ = native_flow
    monkeypatch.setattr(F, 'native_classification_review', lambda *a, **k: pytest.fail('Review disabled'))
    result = F.native_synthesis('Compare values', '', '', binary, lambda: cfg, 120, routing_mode='d1')
    assert result['status'] == 'succeeded'
    assert [c['kind'] for c in calls] == ['synthesis', 'verification']


def test_invalid_calculation_contract_stops_before_classification(native_flow, monkeypatch):
    cfg, _, binary, calls, _, _, _ = native_flow
    monkeypatch.setattr(H, 'decide', lambda *a, **k: pytest.fail('Invalid data reached D1'))
    context = json.dumps({'calculation_sources': {}, 'calculation_checks': [
        {'id': 'bad', 'answer_pointer': '/facts/rate',
         'expression': {'source': 'missing', 'pointer': '/value'}}]})
    result = F.native_synthesis('Compute rate', context, '', binary, lambda: cfg, 120)
    assert result['status'] == 'needs_data'
    assert calls == []

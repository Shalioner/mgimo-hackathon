"""Ordered decision-classifier failover; never execute the user's task."""
import copy
import json
import os

import pytest
import decide as D
import route_request as R
import routing_policy as P
import openrouter_decision_smoke as S


@pytest.mark.parametrize("winner", [0, 1, 2, None])
def test_cascade_order_and_billing_boundaries(monkeypatch, winner):
    cfg = S.configuration()
    cfg['automatic_routing']['classifier'] = 'cascade'
    calls = []
    def classify(request, context, **kwargs):
        staged = kwargs['load_config']()
        options = staged['automatic_routing']
        calls.append((options['classifier'], staged['execution_policy']['mode'], options.get('decision_provider')))
        return {'status': 'accepted' if len(calls) - 1 == winner else 'needs_review',
                'labels': {'tier': 'T1', 'effort': 'normal'}, 'route': {'result': 'succeeded'}}
    monkeypatch.setattr(R, 'classify_request', classify)
    result = R._cascade('synthetic', '', cfg, execute=True, load_config=lambda: cfg, timeout=90, question_scope='native')
    expected = [('provider', 'api_opt_in', 'openrouter'), ('local-d1', 'subscription_only', None),
                ('bootstrap', 'subscription_only', None)]
    assert calls == expected[:3 if winner is None else winner + 1]
    assert result['selected_classifier'] == (None if winner is None else ['api', 'local-decision', 'subscription'][winner])
    assert cfg['automatic_routing']['classifier'] == 'cascade'


def test_cascade_revocation_stops_before_second_stage(monkeypatch):
    cfg = S.configuration(); cfg['automatic_routing']['classifier'] = 'cascade'
    fresh = copy.deepcopy(cfg)
    calls = []
    def classify(*args, **kwargs):
        kwargs['load_config']()
        calls.append(1)
        fresh['automatic_routing']['classifier'] = 'local-d1'
        return {'status': 'unavailable'}
    monkeypatch.setattr(R, 'classify_request', classify)
    result = R._cascade('synthetic', '', cfg, execute=True, load_config=lambda: fresh, timeout=90, question_scope='native')
    assert result['status'] == 'unavailable' and len(calls) == 1


def test_cascade_cannot_upgrade_subscription_only_to_api(monkeypatch):
    cfg = S.configuration(); cfg['execution_policy']['mode'] = 'subscription_only'
    seen = []
    def classify(*args, **kwargs):
        seen.append(kwargs['load_config']()['execution_policy']['mode'])
        return {'status': 'unavailable'}
    monkeypatch.setattr(R, 'classify_request', classify)
    R._cascade('synthetic', '', cfg, execute=True, load_config=lambda: cfg, timeout=90, question_scope='native')
    assert seen == ['subscription_only'] * 3


def test_private_key_file_and_missing_file_fail_closed(tmp_path, monkeypatch):
    key = tmp_path / 'key'; key.write_text('fake-key'); key.chmod(0o600)
    provider = {**S.PROVIDER, 'api_key_file': str(key)}
    monkeypatch.setenv('OPENROUTER_API_KEY', 'wrong-environment-key')
    assert D.provider_key(provider) == 'fake-key'
    key.chmod(0o644)
    with pytest.raises(ValueError): D.provider_key(provider)
    key.unlink()
    result = D.decision_plan(S.configuration(), provider, {'model': S.MODEL, 'state': '', 'questions': {}})
    assert result['status'] == 'denied'


@pytest.mark.parametrize('cap', [None, 1, False])
def test_zero_cap_checked_before_inference(monkeypatch, cap):
    monkeypatch.setenv('OPENROUTER_API_KEY', 'fake-key')
    calls = []
    class Response:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def read(self): return json.dumps({'data': {'limit': cap, 'is_free_tier': True}}).encode()
    def open_(request, timeout):
        calls.append(request.full_url)
        return Response()
    monkeypatch.setattr(P, 'http_open', open_)
    code, _, _ = D.call(S.PROVIDER, {'model': S.MODEL, 'state': '', 'questions': {}}, 1, S.configuration())
    assert code == 3 and calls == ['https://openrouter.ai/api/v1/key']


@pytest.mark.parametrize('payload', [[], None, 'invalid'])
def test_malformed_cap_response_is_regular_failure(monkeypatch, payload):
    monkeypatch.setenv('OPENROUTER_API_KEY', 'fake-key')
    class Response:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def read(self): return json.dumps(payload).encode()
    monkeypatch.setattr(P, 'http_open', lambda *a: Response())
    assert D.call(S.PROVIDER, {'model': S.MODEL, 'state': '', 'questions': {}}, 1, S.configuration())[0] == 3

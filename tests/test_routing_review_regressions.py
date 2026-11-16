import copy
import json
from pathlib import Path

import pytest

import delegate as D
import decide as DC
import ledger as L
import routing_policy as P
from test_routing_policy import api_policy


@pytest.mark.parametrize('field,value,requested', [
    ('models', 'premium-model', 'premium'), ('models', {'premium': True}, 'premium'),
    ('models', True, 'premium'), ('models', [], 'premium'),
    ('efforts', 'xhigh', 'high'), ('efforts', {'high': True}, 'high'),
    ('efforts', True, 'high'), ('efforts', [], 'high')])
def test_malformed_allowlists_cannot_authorize(field, value, requested):
    cfg = {'execution_policy': api_policy('selfhosted', 'example', 'https://example.test/v1/chat/completions', 'premium')}
    cfg['execution_policy']['api']['grants'][0][field] = value
    assert P.plan(cfg, host='selfhosted', provider='example', endpoint='https://example.test/v1/chat/completions',
                  model=requested if field == 'models' else 'premium', effort=requested if field == 'efforts' else 'low',
                  key_present=True)['status'] != 'executable'


def test_disabled_effort_cannot_execute_low_as_default(monkeypatch):
    monkeypatch.setenv('FAKE_KEY', 'test-only')
    policy = api_policy('selfhosted', 'example', 'https://example.test/v1/chat/completions', 'm', ['low'])
    cfg = {'execution_policy': policy, 'selfhosted': {'base_url': 'https://example.test/v1',
            'provider': 'example', 'api_key_env': 'FAKE_KEY', 'send_effort': False}}
    receipt, _, body = D.selfhosted_plan(cfg, 'm', 'hello', 'low')
    assert receipt['status'] != 'executable'
    assert receipt['requested']['effort'] == 'low' and 'reasoning_effort' not in body
    policy['api']['grants'][0]['efforts'] = [None]
    assert D.selfhosted_plan(cfg, 'm', 'hello', None)[0]['status'] == 'executable'


@pytest.mark.parametrize('entry', ['delegate', 'decide'])
def test_runtime_refusal_is_audited_once(tmp_path, monkeypatch, entry):
    project = tmp_path / 'p'
    (project / 'state').mkdir(parents=True)
    log = project / 'state/run_log.md'
    log.write_text('', encoding="utf-8")
    prompt = tmp_path / 'q.md'
    prompt.write_text('public sample', encoding="utf-8")
    cfg = {'tasks': {'classify': {'tier': 'T1'}}, 'families': {'openai': {'T1': 'm'}},
           'decision': {'providers': [{'name': 'explicit', 'base_url': 'https://example.test', 'model': 'm'}]}}
    monkeypatch.setattr(L, 'load_routing', lambda: cfg)
    if entry == 'delegate':
        rc = D.main(['--project', str(project), '--task', 'classify', '--prompt-file', str(prompt)])
    else:
        rc = DC.main(['--project', str(project), '--provider', 'explicit', '--ask', 'Ready?', '--state', 'public sample'])
    assert rc == 3
    receipts = list((project / 'state/delegated').glob('*.receipt.json'))
    assert len(receipts) == 1
    receipt = json.loads(receipts[0].read_text(encoding="utf-8"))
    assert receipt['status'] in {'manual', 'denied'}
    assert all(x is None for x in receipt['transmitted'].values())
    assert all(x is None for x in receipt['observed'].values())
    assert log.read_text(encoding="utf-8").count('routing refusal') == 1


@pytest.mark.parametrize('value', ['grok-premium', {'grok': True}, True, [], [True], [''], [None]])
def test_cursor_list_schema_is_strict(value):
    cfg = {'environments': {'cursor': {'model_allowlist': value}}}
    receipt = P.plan(cfg, host='cursor', provider='cursor', model='grok')
    assert receipt['status'] in {'manual', 'denied'}
    if value != []:
        assert receipt['status'] == 'denied'


@pytest.mark.parametrize('field,value', [('models', [True]), ('models', ['']), ('models', [None]),
                                          ('efforts', [True]), ('efforts', ['ultra']), ('efforts', [{}]),
                                          ('efforts', []), ('models', {})])
def test_allowlist_elements_are_strict(field, value):
    cfg = {'execution_policy': api_policy('selfhosted', 'example', 'https://example.test/v1/chat/completions', 'm')}
    cfg['execution_policy']['api']['grants'][0][field] = value
    assert P.plan(cfg, host='selfhosted', provider='example', endpoint='https://example.test/v1/chat/completions',
                  model='m', effort=None, key_present=True)['status'] == 'denied'


def test_observed_compares_transmission_and_request_separately():
    r = P.plan({}, host='codex', provider='openai', model='m', effort='low')
    r['transmitted'] = {'model': 'm', 'effort': None}
    assert P.observe(r, {'model': 'm', 'effort': 'high'})
    assert r['request_satisfaction']['effort'] is False
    r['transmitted']['effort'] = 'low'
    assert not P.observe(r, {'model': 'm', 'effort': 'high'})


@pytest.mark.parametrize('entry', ['delegate', 'decide'])
def test_refusal_preflight_does_not_audit(tmp_path, monkeypatch, entry):
    project = tmp_path / 'p'
    (project / 'state').mkdir(parents=True)
    log = project / 'state/run_log.md'
    log.write_text('', encoding="utf-8")
    prompt = tmp_path / 'q.md'
    prompt.write_text('public sample', encoding="utf-8")
    cfg = {'tasks': {'classify': {'tier': 'T1'}}, 'families': {'openai': {'T1': 'm'}},
           'decision': {'providers': [{'name': 'explicit', 'base_url': 'https://example.test', 'model': 'm'}]}}
    monkeypatch.setattr(L, 'load_routing', lambda: cfg)
    args = ['--project', str(project), '--dry-run']
    if entry == 'delegate':
        rc = D.main(args + ['--task', 'classify', '--prompt-file', str(prompt)])
    else:
        rc = DC.main(args + ['--provider', 'explicit', '--ask', 'Ready?', '--state', 'public sample'])
    assert rc == 0 and log.read_text(encoding="utf-8") == '' and not (project / 'state/delegated').exists()

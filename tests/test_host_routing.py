import copy

import pytest

import host_routing as H


@pytest.fixture
def cfg(monkeypatch):
    data = H.L.load_routing()
    data['automatic_routing'] = {'classifier': 'local-d1', 'host_routing': {
        'enabled_hosts': list(H.FAMILIES), 'models': {
            host: [H.L.model_for(data, family, t) for t in ('T1','T2','T3','T4')]
            for host, family in H.FAMILIES.items()}}}
    return data


def classifier(tier, effort='normal'):
    return lambda *a, **k: {'status': 'accepted', 'labels': {'tier': tier, 'effort': effort, 'capability': 'tools'}}


@pytest.mark.parametrize('compact,scope', [(True, 'native'), (False, 'all')])
def test_native_compact_questions_has_explicit_rollback(cfg, monkeypatch, compact, scope):
    cfg['automatic_routing']['host_routing']['compact_questions'] = compact
    seen = []
    def classify(*args, **kwargs):
        seen.append(kwargs['question_scope'])
        return classifier('T1')()
    monkeypatch.setattr(H.R, 'classify_request', classify)
    report = H.decide({'host': 'codex', 'request': 'task'}, execute=True, load_config=lambda: cfg)
    assert seen == [scope] and report['question_scope'] == scope
    assert H.authorize(report, load_config=lambda: cfg)['status'] == 'authorized'


def test_invalid_compact_flag_never_calls_classifier(cfg, monkeypatch):
    cfg['automatic_routing']['host_routing']['compact_questions'] = 'false'
    monkeypatch.setattr(H.R, 'classify_request', lambda *a, **k: pytest.fail('no inference'))
    assert H.decide({'host': 'codex', 'request': 'task'}, execute=True, load_config=lambda: cfg)['status'] == 'unavailable'


@pytest.mark.parametrize('host', H.FAMILIES)
def test_select_and_authorize_each_tier_resets(cfg, monkeypatch, host):
    for tier in ('T3', 'T1', 'T2'):
        monkeypatch.setattr(H.R, 'classify_request', classifier(tier))
        result = H.decide({'host': host, 'request': 'task'}, execute=True, load_config=lambda: cfg)
        assert result['status'] == 'selected' and result['tier'] == tier
        assert result['model'] == H.L.model_for(cfg, H.FAMILIES[host], tier)
        assert H.authorize(result, load_config=lambda: cfg)['status'] == 'authorized'


def test_native_routing_never_enables_delegate_profile(cfg, monkeypatch):
    monkeypatch.setattr(H.R, 'classify_request', classifier('T1'))
    before = copy.deepcopy(cfg)
    H.decide({'host': 'codex', 'request': 'task'}, execute=True, load_config=lambda: cfg)
    assert cfg == before and not cfg.get('execution_policy', {}).get('codex_subscription', {}).get('enabled')


def test_config_changed_before_apply(cfg, monkeypatch):
    monkeypatch.setattr(H.R, 'classify_request', classifier('T1'))
    result = H.decide({'host': 'codex', 'request': 'task'}, execute=True, load_config=lambda: cfg)
    cfg['automatic_routing']['host_routing']['enabled_hosts'] = []
    assert H.authorize(result, load_config=lambda: cfg)['status'] == 'needs_review'


def test_t4_reservation_is_per_actual_request(cfg, monkeypatch):
    monkeypatch.setattr(H.R, 'classify_request', classifier('T4'))
    monkeypatch.setattr(H.A, 't4_available', lambda c: True)
    reservations = []
    def reserve(c):
        if len(reservations) == 2:
            raise ValueError('quota')
        reservations.append(1)
        return {'calls': len(reservations)}
    monkeypatch.setattr(H.A, 'reserve_t4', reserve)
    selected = H.decide({'host': 'codex', 'request': 'task'}, execute=True, load_config=lambda: cfg)
    assert selected['status'] == 'selected' and not reservations
    assert H.authorize(selected, load_config=lambda: cfg)['status'] == 'authorized'
    assert H.authorize(selected, load_config=lambda: cfg)['status'] == 'authorized'
    assert H.authorize(selected, load_config=lambda: cfg)['status'] == 'needs_review'


@pytest.mark.parametrize('case', ['disabled', 'remote', 'oversize'])
def test_no_inference_for_unapproved_routes(cfg, monkeypatch, case):
    request = 'task'
    if case == 'disabled':
        cfg['automatic_routing']['host_routing']['enabled_hosts'] = []
    elif case == 'remote':
        cfg['automatic_routing']['classifier'] = 'provider'
    else:
        request = 'x' * 24001
    monkeypatch.setattr(H.R, 'classify_request', lambda *a, **k: pytest.fail('no inference'))
    assert H.decide({'host': 'codex', 'request': request}, execute=True, load_config=lambda: cfg)['status'] != 'selected'


def test_disallowed_model_and_effort(cfg, monkeypatch):
    monkeypatch.setattr(H.R, 'classify_request', classifier('T1'))
    cfg['automatic_routing']['host_routing']['models']['codex'] = []
    assert H.decide({'host': 'codex', 'request': 'task'}, execute=True, load_config=lambda: cfg)['status'] == 'needs_review'
    assert H.authorize({'host': 'codex', 'tier': 'T1', 'model': 'attacker', 'effort': 'max'}, load_config=lambda: cfg)['status'] == 'needs_review'


@pytest.mark.parametrize('payload', [[], None, {'host': [], 'tier': 'T1'}, {'host': 'codex', 'tier': []}])
def test_invalid_authorization_payload_declines(cfg, payload):
    assert H.authorize(payload, load_config=lambda: cfg)['status'] == 'needs_review'


def test_string_allowlist_not_substring_authorization(cfg):
    cfg['automatic_routing']['host_routing']['enabled_hosts'] = 'codex'
    assert H.authorize({'host': 'codex', 'tier': 'T1', 'model': 'gpt-6-luna', 'effort': 'low'}, load_config=lambda: cfg)['status'] == 'needs_review'


@pytest.mark.parametrize('tier_confidence,expected', [(.95, 'selected'), (.55, 'needs_review')])
def test_native_tools_capability_is_not_a_model_gate(cfg, monkeypatch, tier_confidence, expected):
    from test_decision_runtime import response
    data = response(H.R.questions(cfg), choices={'tier': 'T1', 'effort': 'normal', 'capability': 'text'})
    data['answers']['capability']['confidence'] = .59
    data['answers']['capability']['probabilities'] = {'text': .59, 'tools': .41}
    data['answers']['tier']['confidence'] = tier_confidence
    data['answers']['tier']['probabilities'] = {key: tier_confidence if key == 'T1' else (1-tier_confidence)/3 for key in ['T1','T2','T3','T4']}
    monkeypatch.setattr(H.R, 'classify_request', lambda *a, **kw: {'status':'needs_review', 'route':{'result':'succeeded'}, 'response':data})
    assert H.decide({'host':'codex', 'request':'Extract 17'}, execute=True, load_config=lambda:cfg)['status'] == expected


def test_prompt_hook_honest_delegate_instruction():
    import importlib.util
    spec = importlib.util.spec_from_file_location('host_prompt', H.L.ROOT / 'tools/hooks/host_prompt.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    result = module.handle({'hook_event_name': 'UserPromptSubmit', 'prompt': 'task'},
                           decide=lambda *a, **k: {'status': 'selected', 'tier': 'T1', 'model': 'gpt-6-luna', 'effort': 'low'})
    context = result['hookSpecificOutput']['additionalContext']
    assert 'gpt-6-luna' in context and 'не меняет основную модель' in context and 'fork_turns="none"' in context
    for extra in [{'agent_id': 'child'}, {'agent_type': 'worker'}]:
        assert module.handle({'hook_event_name': 'UserPromptSubmit', 'prompt': 'task', **extra},
                             decide=lambda *a, **k: pytest.fail('No recursive routing')) == {}


@pytest.fixture
def prompt_hook(monkeypatch):
    import importlib.util
    monkeypatch.delenv('STRATEGIST_ROUTED_APP_SERVER', raising=False)
    spec = importlib.util.spec_from_file_location('host_prompt_ack', H.L.ROOT / 'tools/hooks/host_prompt.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize('prompt', ['спасибо', '  Спасибо!  ', 'ОК', 'окей.', 'Понятно…'])
def test_pure_acknowledgement_skips_classifier(prompt_hook, prompt):
    assert prompt_hook.handle({'hook_event_name': 'UserPromptSubmit', 'prompt': prompt},
                              decide=lambda *a, **k: pytest.fail('Acknowledgement needs no model')) == {}


@pytest.mark.parametrize('prompt', [
    'да', 'давай делай', 'продолжай', 'Продолжай ту же подзадачу',
    'окей, составь план', 'спасибо\nИсследуй рынок', 'понятно?',
    'Напиши слово «спасибо»', 'нет',
])
def test_acknowledgement_filter_does_not_swallow_tasks(prompt_hook, prompt):
    calls = []
    def decide(payload, **kwargs):
        calls.append(payload)
        return {'status': 'needs_review'}
    prompt_hook.handle({'hook_event_name': 'UserPromptSubmit', 'prompt': prompt}, decide=decide)
    assert len(calls) == 1 and calls[0]['request'] == prompt


@pytest.mark.parametrize('field', ['attachments', 'files', 'images', 'input', 'content'])
def test_acknowledgement_with_material_is_not_silently_skipped(prompt_hook, field):
    calls = []
    prompt_hook.handle({'hook_event_name': 'UserPromptSubmit', 'prompt': 'спасибо', field: ['material']},
                       decide=lambda *a, **k: calls.append(a) or {'status': 'needs_review'})
    assert len(calls) == 1


@pytest.mark.parametrize('opt_in', [None, False, 'true', 1])
def test_native_api_requires_boolean_opt_in(cfg, monkeypatch, opt_in):
    cfg['automatic_routing']['classifier'] = 'provider'
    cfg['automatic_routing']['decision_provider'] = 'openrouter'
    cfg['automatic_routing']['host_routing']['api_classifier_opt_in'] = opt_in
    monkeypatch.setattr(H.R, 'classify_request', lambda *a, **k: pytest.fail('no inference'))
    assert H.decide({'host': 'codex', 'request': 'task'}, execute=True, load_config=lambda: cfg)['status'] == 'unavailable'


@pytest.mark.parametrize('host', H.FAMILIES)
@pytest.mark.parametrize('classifier_name', ['provider', 'cascade'])
def test_native_api_selects_and_revocation_blocks_authorization(cfg, monkeypatch, host, classifier_name):
    cfg['automatic_routing'].update(classifier=classifier_name, decision_provider='openrouter')
    cfg['automatic_routing']['host_routing']['api_classifier_opt_in'] = True
    seen = []
    def classify(*a, **kwargs):
        seen.append((kwargs['execute'], kwargs['question_scope']))
        return classifier('T1')()
    monkeypatch.setattr(H.R, 'classify_request', classify)
    result = H.decide({'host': host, 'request': 'task'}, execute=True, load_config=lambda: cfg)
    assert result['status'] == 'selected' and result['classifier'] == classifier_name
    assert seen == [(True, 'native')]
    assert H.authorize(result, load_config=lambda: cfg)['status'] == 'authorized'
    cfg['automatic_routing']['host_routing']['api_classifier_opt_in'] = False
    assert H.authorize(result, load_config=lambda: cfg)['status'] == 'needs_review'


@pytest.mark.parametrize('stage', [1, 2])
def test_api_opt_in_revoked_before_classifier_transport(cfg, monkeypatch, stage):
    cfg['automatic_routing'].update(classifier='provider', decision_provider='openrouter')
    cfg['automatic_routing']['host_routing']['api_classifier_opt_in'] = True
    calls = []
    def load():
        fresh = copy.deepcopy(cfg)
        if len(calls) >= stage:
            fresh['automatic_routing']['host_routing']['api_classifier_opt_in'] = False
        calls.append(1)
        return fresh
    def classify(*a, **kwargs):
        kwargs['load_config']()
        kwargs['load_config']()
        pytest.fail('revocation should stop classification')
    monkeypatch.setattr(H.R, 'classify_request', classify)
    report = H.decide({'host': 'codex', 'request': 'task'}, execute=True, load_config=load)
    assert report['status'] == 'unavailable'


def test_native_api_still_requires_exact_execution_grant(cfg, monkeypatch):
    cfg['automatic_routing'].update(classifier='provider', decision_provider='openrouter')
    cfg['automatic_routing']['host_routing']['api_classifier_opt_in'] = True
    cfg['execution_policy'] = {'mode': 'subscription_only'}
    monkeypatch.setattr(H.R.C, 'call', lambda *a, **k: pytest.fail('no transport'))
    report = H.decide({'host': 'codex', 'request': 'task'}, execute=True, load_config=lambda: cfg)
    assert report['status'] == 'unavailable' and 'model' not in report


@pytest.mark.parametrize('status', ['needs_review', 'unavailable'])
def test_native_api_failed_or_uncertain_never_overrides(cfg, monkeypatch, status):
    cfg['automatic_routing'].update(classifier='provider', decision_provider='openrouter')
    cfg['automatic_routing']['host_routing']['api_classifier_opt_in'] = True
    monkeypatch.setattr(H.R, 'classify_request', lambda *a, **k: {'status': status})
    report = H.decide({'host': 'claude-code', 'request': 'task'}, execute=True, load_config=lambda: cfg)
    assert report['status'] == status and 'model' not in report

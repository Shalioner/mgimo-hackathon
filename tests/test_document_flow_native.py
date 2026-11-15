"""Native document integration: real plans/quality parser, offline model transport."""
import copy
import json

import pytest

import document_flow as F
import host_routing as H
import native_codex as N


@pytest.fixture
def native_flow(tmp_path, monkeypatch):
    cfg = F.L.load_routing()
    cfg['families']['openai'] = {'T1': 'gpt-6-luna', 'T2': 'gpt-6.1-terra',
                                 'T3': 'gpt-6.1-sol', 'T4': 'gpt-6-astra'}
    cfg['tiers'] = {tier: {'effort': efforts} for tier, efforts in [
        ('T1', ['low']), ('T2', ['medium', 'high']),
        ('T3', ['high', 'xhigh']), ('T4', ['high', 'xhigh'])]}
    cfg.setdefault('execution_policy', {}).setdefault('codex_subscription', {})['enabled'] = False
    cfg['automatic_routing'] = {'classifier': 'local-d1',
        'host_routing': {'enabled_hosts': ['codex'], 'models': {
            'codex': list(cfg['families']['openai'].values())}},
        'quality': {'enabled': True, 'max_attempts': 6}}
    paths = [tmp_path / 'one.md', tmp_path / 'two.md']
    for index, path in enumerate(paths):
        path.write_text(f'Value is {index}.\nORIGINAL_PRIVATE_BACKGROUND_{index}\n' + 'Unused background\n' * 100)
    binary = tmp_path / 'codex'
    binary.write_text('This fixture must never be executed.')
    calls, classifications = [], []
    controls = {'tier': 'T2', 'verdicts': ['accept'], 'callback': lambda kind: None,
                'transport_failure': None, 'sure': True, 'bad_card': False}
    def forbidden(*args, **kwargs):
        pytest.fail('Native document mode must not use isolated delegate transport')
    monkeypatch.setattr(F.D, 'execute_route', forbidden)
    monkeypatch.setattr(F.D, 'call_codex', forbidden)
    monkeypatch.setattr(F.R, 'route_plan', forbidden)
    monkeypatch.setattr(F.R, 'run_request', forbidden)
    def classify(payload, **kwargs):
        assert kwargs['execute'] is True
        assert 'ORIGINAL_PRIVATE_BACKGROUND' not in json.dumps(payload)
        assert 'evidence_cards' not in payload['context']
        classifications.append(payload)
        return N.plan(controls['tier'], kwargs['load_config']())
    monkeypatch.setattr(H, 'decide', classify)
    def execute(binary_arg, prompt, selection, *, load_config, timeout):
        assert binary_arg == binary
        kind = ('extraction' if 'numbered_document' in prompt else
                'verification' if selection['tier'] == 'T1' else 'synthesis')
        controls['callback'](kind)
        if H.authorize(selection, load_config=load_config)['status'] != 'authorized':
            return {'status': 'manual', 'transmitted_model': None, 'usage_events': []}, None
        calls.append({'kind': kind, 'prompt': prompt, **selection})
        if kind == controls['transport_failure']:
            return {'status': 'failed', 'transmitted_model': selection['model'], 'usage_events': []}, None
        if kind == 'extraction':
            payload = json.loads(prompt.rsplit('\n', 1)[-1])
            quote = payload['numbered_document'].splitlines()[0].split(': ', 1)[1]
            text = json.dumps({'topic': 'Values', 'findings': [{'kind': 'fact', 'statement': quote,
                              'quote': 'invented' if controls['bad_card'] else quote,
                              'start_line': 1, 'end_line': 1}],
                               'limitations': ['One period'], 'contradictions': []})
        elif kind == 'synthesis':
            assert 'ORIGINAL_PRIVATE_BACKGROUND' not in prompt and 'Unused background' not in prompt
            text = 'Values differ across DOC-001:1 and DOC-002:1.'
        else:
            assert 'ORIGINAL_PRIVATE_BACKGROUND' not in prompt and 'Unused background' not in prompt
            verdict = controls['verdicts'].pop(0)
            text = json.dumps({'answers': {'quality': {'choice': verdict,
                               'sure': controls['sure'], 'quote': 'Values differ'}}})
        return {'status': 'succeeded', 'transmitted_model': selection['model'],
                'usage_events': [{'test_tokens': 17}]}, text
    monkeypatch.setattr(N, 'execute', execute)
    def run(**kwargs):
        return F.run_documents('Compare values', paths, ['codex'], native_binary=binary,
                               load_config=lambda: cfg, **kwargs)
    return cfg, paths, binary, calls, classifications, controls, run


def test_explicit_native_preflight_no_inference_or_source_mutation(native_flow):
    cfg, paths, _, calls, classifications, _, run = native_flow
    before, cfg_before = [p.read_bytes() for p in paths], copy.deepcopy(cfg)
    result = run()
    assert result['transport'] == 'native-codex' and result['status'] == 'planned'
    assert len(result['extractions']) == 2 and not result['cards']
    assert result['synthesis']['status'] == 'awaiting_cards'
    assert not calls and not classifications
    assert [p.read_bytes() for p in paths] == before and cfg == cfg_before
    assert 'ORIGINAL_PRIVATE_BACKGROUND' not in json.dumps(result)


@pytest.mark.parametrize('hosts', [['claude-code'], ['codex', 'claude-code']])
def test_explicit_native_transport_cannot_select_other_host(native_flow, hosts):
    cfg, paths, binary, calls, _, _, _ = native_flow
    with pytest.raises(ValueError, match='--host codex'):
        F.run_documents('Compare', paths, hosts, native_binary=binary, load_config=lambda: cfg)
    assert not calls


def test_native_sequential_cards_and_quality_never_receive_originals(native_flow):
    _, paths, _, calls, classifications, _, run = native_flow
    result = run(execute=True)
    assert result['status'] == 'succeeded', result
    assert [c['kind'] for c in calls] == ['extraction', 'extraction', 'synthesis', 'verification']
    assert [c['tier'] for c in calls] == ['T1', 'T1', 'T2', 'T1']
    for index in range(2):
        assert f'ORIGINAL_PRIVATE_BACKGROUND_{index}' in calls[index]['prompt']
        assert f'ORIGINAL_PRIVATE_BACKGROUND_{1-index}' not in calls[index]['prompt']
        card = result['cards'][index]
        assert card['source']['sha256'] == F._hash(paths[index].read_bytes())
        assert card['findings'][0]['start_line'] == 1
    senior = calls[2]['prompt']
    assert 'Value is 0.' in senior and 'Value is 1.' in senior
    assert 'ORIGINAL_PRIVATE_BACKGROUND' not in senior
    assert len(json.loads(classifications[0]['context'])['documents']) == 2
    assert result['result']['attempts'][0]['quality']['verdict'] == 'accept'


def test_native_quality_revise_increases_effort_then_model(native_flow):
    _, _, _, calls, _, controls, run = native_flow
    controls['verdicts'] = ['revise', 'revise', 'accept']
    result = run(execute=True)
    assert result['status'] == 'succeeded', result
    assert [(c['tier'], c['effort']) for c in calls if c['kind'] == 'synthesis'] == [
        ('T2', 'medium'), ('T2', 'high'), ('T3', 'high')]
    assert len([c for c in calls if c['kind'] == 'verification']) == 3
    controls['verdicts'] = ['accept']
    before = len(calls)
    run(execute=True)
    assert calls[before]['tier'] == 'T1'


@pytest.mark.parametrize('verdict,sure,status', [('insufficient', True, 'needs_data'),
                                               ('revise', False, 'needs_review')])
def test_missing_evidence_or_uncertain_verifier_never_escalates(native_flow, verdict, sure, status):
    _, _, _, calls, _, controls, run = native_flow
    controls.update(verdicts=[verdict], sure=sure)
    result = run(execute=True)
    assert result['status'] == status and result['result']['text'] is None
    assert result['result']['candidate']
    assert len([c for c in calls if c['kind'] == 'synthesis']) == 1


def test_rejected_answer_at_attempt_limit_is_not_success(native_flow):
    cfg, _, _, calls, _, controls, run = native_flow
    cfg['automatic_routing']['quality']['max_attempts'] = 1
    controls['verdicts'] = ['revise']
    result = run(execute=True)
    assert result['status'] == 'needs_review' and result['result']['text'] is None
    assert len([c for c in calls if c['kind'] == 'synthesis']) == 1


def test_revocation_after_verifier_stops_escalation(native_flow):
    cfg, _, _, calls, _, controls, run = native_flow
    controls['verdicts'] = ['revise']
    # Revocation is observed by the next route plan after completed verification.
    original_pop = controls['verdicts']
    class RevokeAfterPop(list):
        def pop(self, index):
            cfg['automatic_routing']['host_routing']['enabled_hosts'] = []
            return super().pop(index)
    controls['verdicts'] = RevokeAfterPop(original_pop)
    result = run(execute=True)
    assert result['status'] == 'manual' and result['result']['text'] is None
    assert [c['kind'] for c in calls] == ['extraction', 'extraction', 'synthesis', 'verification']


def test_revocation_at_extraction_dispatch_stops_without_fallback(native_flow):
    cfg, _, _, calls, _, controls, run = native_flow
    controls['callback'] = lambda kind: cfg['automatic_routing']['host_routing'].update(enabled_hosts=[])
    result = run(execute=True)
    assert result['status'] == 'manual' and not calls and result['synthesis'] is None


@pytest.mark.parametrize('kind', ['extraction', 'synthesis', 'verification'])
def test_native_transport_failure_never_retries_or_escalates(native_flow, kind):
    _, _, _, calls, _, controls, run = native_flow
    controls['transport_failure'] = kind
    result = run(execute=True)
    assert result['status'] == ('needs_review' if kind == 'verification' else 'failed')
    assert result['result']['text'] is None
    assert len([c for c in calls if c['kind'] == kind]) == 1


def test_invalid_native_card_stops_before_second_source(native_flow):
    _, _, _, calls, _, controls, run = native_flow
    controls['bad_card'] = True
    result = run(execute=True)
    assert result['status'] == 'needs_review' and len(calls) == 1
    assert result['synthesis'] is None and not result['cards']


def test_native_quality_cannot_escalate_into_t4(native_flow):
    _, _, _, calls, _, controls, run = native_flow
    controls.update(tier='T3', verdicts=['revise', 'revise'])
    result = run(execute=True)
    assert result['status'] == 'manual' and result['result']['text'] is None
    assert result['result']['reason'] == 'Native document routing requires approved T1–T3 model/effort'
    assert all(c['tier'] != 'T4' for c in calls)
    assert [(c['tier'], c['effort']) for c in calls if c['kind'] == 'synthesis'] == [
        ('T3', 'high'), ('T3', 'xhigh')]


def test_native_preflight_refuses_disabled_host(native_flow):
    cfg, _, _, calls, classifications, _, run = native_flow
    cfg['automatic_routing']['host_routing']['enabled_hosts'] = []
    result = run()
    assert result['status'] == 'manual' and not calls and not classifications


def test_native_without_quality_uses_same_native_transport(native_flow):
    cfg, _, _, calls, _, _, run = native_flow
    cfg['automatic_routing']['quality']['enabled'] = False
    result = run(execute=True)
    assert result['status'] == 'succeeded' and result['result']['text']
    assert [c['kind'] for c in calls] == ['extraction', 'extraction', 'synthesis']


def test_native_config_revoked_between_plan_and_extraction(native_flow):
    cfg, paths, binary, calls, _, _, _ = native_flow
    reads = 0
    def load():
        nonlocal reads
        reads += 1
        if reads == 2:
            cfg['automatic_routing']['host_routing']['models']['codex'] = []
        return cfg
    result = F.run_documents('Compare', paths, ['codex'], native_binary=binary,
                             execute=True, load_config=load)
    assert result['status'] == 'manual' and not calls
    assert result['extractions'][0]['status'] == 'not_started'


@pytest.fixture
def uncertain_native(native_flow, monkeypatch):
    cfg, paths, binary, calls, classifications, controls, _ = native_flow
    labels = {'tier': 'T2', 'effort': 'normal', 'capability': 'text'}
    selection = {'status': 'needs_review', 'host': 'codex', 'classification': {
        'route': {'result': 'succeeded'}, 'response': {'answers': {
            key: {'type': 'choice', 'choice': value, 'confidence': .677}
            for key, value in labels.items()}}}}
    review = {'answers': {key: {'choice': value, 'sure': True, 'quote': 'Compare values'}
                          for key, value in labels.items()}}
    controls.update(review_callback=lambda: None, review_status='succeeded')
    original_execute = N.execute
    monkeypatch.setattr(H, 'decide', lambda *args, **kwargs: copy.deepcopy(selection))
    def transport(binary_arg, prompt, chosen, **kwargs):
        if '"tier"' in prompt and 'numbered_document' not in prompt:
            assert chosen['tier'] == 'T1' and chosen['effort'] == 'low'
            assert 'ORIGINAL_PRIVATE_BACKGROUND' not in prompt and 'evidence_cards' not in prompt
            calls.append({'kind': 'classification_review', 'prompt': prompt, **chosen})
            controls['review_callback']()
            return {'status': controls['review_status'], 'transmitted_model': chosen['model'],
                    'usage_events': []}, json.dumps(review)
        return original_execute(binary_arg, prompt, chosen, **kwargs)
    monkeypatch.setattr(N, 'execute', transport)
    def run():
        # Actual config reads return new snapshots, permitting change detection.
        return F.run_documents('Compare values', paths, ['codex'], execute=True,
                               native_binary=binary, load_config=lambda: copy.deepcopy(cfg))
    return cfg, calls, controls, selection, review, run


def test_uncertain_d1_gets_one_native_t1_review_then_synthesis(uncertain_native):
    _, calls, _, _, _, run = uncertain_native
    result = run()
    assert result['status'] == 'succeeded', result
    assert [c['kind'] for c in calls] == ['extraction', 'extraction', 'classification_review',
                                         'synthesis', 'verification']
    selected = result['synthesis']['classification']
    assert selected['tier'] == 'T2' and selected['status'] == 'selected'
    assert selected['classification']['response']['answers']['tier']['confidence'] == .677
    assert selected['t1_review']['labels']['tier'] == 'T2'
    assert 'not_calibrated_probability' in selected['t1_review']['method']


@pytest.mark.parametrize('bad', ['wrong_quote', 'missing_quote', 'missing_answer', 'unsure',
                                 'not_boolean_sure', 'probability_response'])
def test_native_classification_review_requires_supported_strict_labels(uncertain_native, bad):
    _, calls, _, _, review, run = uncertain_native
    answer = review['answers']['tier']
    if bad == 'wrong_quote':
        answer['quote'] = 'This text is absent from request and synopsis'
    elif bad == 'missing_quote':
        answer.pop('quote')
    elif bad == 'missing_answer':
        review['answers'].pop('tier')
    elif bad == 'unsure':
        answer['sure'] = False
    elif bad == 'not_boolean_sure':
        answer['sure'] = 'true'
    else:
        answer['type'] = 'choice'
        answer['confidence'] = 1.0
    result = run()
    assert result['status'] == 'manual' and result['result']['text'] is None
    assert [c['kind'] for c in calls] == ['extraction', 'extraction', 'classification_review']


@pytest.mark.parametrize('key,value', [('capability', 'tools'), ('tier', 'T4')])
def test_native_classification_review_cannot_grant_tools_or_t4(uncertain_native, key, value):
    _, calls, _, _, review, run = uncertain_native
    review['answers'][key]['choice'] = value
    result = run()
    assert result['status'] == 'manual' and result['result']['text'] is None
    assert len(calls) == 3 and calls[-1]['kind'] == 'classification_review'
    assert all(c['tier'] != 'T4' for c in calls)


@pytest.mark.parametrize('bad', ['transport_failed', 'missing_response', 'invalid_response', 'unavailable'])
def test_d1_transport_or_schema_failure_does_not_trigger_t1_fallback(uncertain_native, bad):
    _, calls, _, selection, _, run = uncertain_native
    if bad == 'transport_failed':
        selection['classification']['route']['result'] = 'failed'
    elif bad == 'missing_response':
        selection['classification'].pop('response')
    elif bad == 'invalid_response':
        selection['classification']['response']['answers']['tier']['confidence'] = float('nan')
    else:
        selection['status'] = 'unavailable'
    result = run()
    assert result['status'] == 'manual' and result['result']['text'] is None
    assert [c['kind'] for c in calls] == ['extraction', 'extraction']


def test_native_classification_review_config_change_discards_selection(uncertain_native):
    cfg, calls, controls, _, _, run = uncertain_native
    controls['review_callback'] = lambda: cfg['automatic_routing']['host_routing'].update(enabled_hosts=[])
    result = run()
    assert result['status'] == 'manual' and result['result']['text'] is None
    assert [c['kind'] for c in calls] == ['extraction', 'extraction', 'classification_review']
    assert result['synthesis']['classification']['status'] == 'needs_review'


def test_native_classification_review_transport_failure_is_not_retried(uncertain_native):
    _, calls, controls, _, _, run = uncertain_native
    controls['review_status'] = 'failed'
    result = run()
    assert result['status'] == 'manual' and result['result']['text'] is None
    assert len([c for c in calls if c['kind'] == 'classification_review']) == 1
    assert not any(c['kind'] == 'synthesis' for c in calls)


@pytest.mark.parametrize('tier,reason', [('T2', 'Model not approved by host policy'),
                                       ('T4', 'T4 approval/quota unavailable')])
def test_confident_d1_policy_denial_never_triggers_t1_review(uncertain_native, tier, reason, tmp_path):
    cfg, calls, _, selection, _, run = uncertain_native
    if tier == 'T4':
        quota = tmp_path / 'quota.json'
        quota.write_text(json.dumps({'approval_ref': 'test-scope', 'calls': 2}))
        cfg['automatic_routing']['t4'] = {'enabled': True, 'approval_ref': 'test-scope',
            'max_calls': 2, 'usage_file': str(quota)}
    selection['reason'] = reason
    answers = selection['classification']['response']['answers']
    answers['tier']['choice'] = tier
    for answer in answers.values():
        answer['confidence'] = .99
    result = run()
    assert result['status'] == 'manual' and result['result']['text'] is None
    assert [c['kind'] for c in calls] == ['extraction', 'extraction']
    assert 't1_review' not in result['synthesis']['classification']
    assert result['synthesis']['classification']['reason'] == reason


@pytest.mark.parametrize('exact', [True, False])
def test_synopsis_json_quote_matches_displayed_state_without_relaxing_evidence(uncertain_native, monkeypatch, exact):
    cfg, _, _, selection, review, _ = uncertain_native
    synopsis = json.dumps({'documents': [{'topic': 'Сравнение спроса', 'id': 'DOC-001'}]}, ensure_ascii=False)
    exact_quote = '"topic": "Сравнение спроса"'
    assert exact_quote in synopsis
    quote = exact_quote if exact else '"topic": "Сравнение выручки"'
    for answer in review['answers'].values():
        answer['quote'] = quote
    observed = []
    def execute(binary, prompt, plan, load_config, timeout):
        observed.append(prompt)
        assert synopsis in prompt and exact_quote in prompt
        return {'result': 'succeeded'}, json.dumps(review, ensure_ascii=False), []
    monkeypatch.setattr(F, 'native_execute', execute)
    result = F.native_classification_review(selection, 'Compare values', synopsis,
                                            '/bin/false', lambda: copy.deepcopy(cfg), 30)
    assert len(observed) == 1
    if exact:
        assert result['status'] == 'selected' and result['tier'] == 'T2'
    else:
        assert result['status'] == 'needs_review'
        assert result['t1_review']['labels'] is None

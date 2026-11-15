import copy
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / 'tools/hooks/codex_routing.py'
spec = importlib.util.spec_from_file_location('codex_routing_hook', SCRIPT)
H = importlib.util.module_from_spec(spec)
spec.loader.exec_module(H)


@pytest.fixture(autouse=True)
def isolated_routing_policy(monkeypatch, tmp_path):
    """Tests must not depend on the developer's ignored routing.local.yaml."""
    import ledger as L
    cfg = L.load_routing(local=tmp_path / 'absent.yaml')
    cfg['automatic_routing'] = {
        'classifier': 'local-d1',
        'host_routing': {'enabled_hosts': ['codex'], 'models': {'codex': [
            L.model_for(cfg, 'openai', tier) for tier in ('T1', 'T2', 'T3', 'T4')]}}}
    monkeypatch.setattr(L, 'load_routing', lambda *a, **k: copy.deepcopy(cfg))


def payload():
    return {'hook_event_name': 'PreToolUse', 'tool_name': 'spawn_agent',
            'tool_input': {'task_name': 'source', 'message': 'Extract source facts',
                           'fork_turns': 'none', 'model': 'old', 'reasoning_effort': 'high'}}


def selected(request, *, execute):
    assert execute is True
    assert request == {'host': 'codex', 'event': 'agent.spawn', 'request': 'Extract source facts', 'context': ''}
    return {'status': 'selected', 'host': 'codex', 'model': 'gpt-6-luna', 'effort': 'low', 'tier': 'T1', 'reason': 'extraction'}


def authorized(selection):
    assert selection['host'] == 'codex'
    return {'status': 'authorized'}


def test_selected_rewrites_only_model_effort():
    original = payload()
    before = copy.deepcopy(original)
    out = H.handle(original, decide=selected, authorize=authorized)['hookSpecificOutput']
    expected = copy.deepcopy(original['tool_input'])
    expected.update(model='gpt-6-luna', reasoning_effort='low')
    assert out['updatedInput'] == expected and original == before
    assert out['hookEventName'] == 'PreToolUse' and 'permissionDecision' not in out


@pytest.mark.parametrize('tool', sorted(H.TOOLS))
def test_known_native_names(tool):
    event = payload()
    event['tool_name'] = tool
    assert 'updatedInput' in H.handle(event, decide=selected, authorize=authorized)['hookSpecificOutput']


@pytest.mark.parametrize('tool', ['Agent', 'Task', 'exec_command', 'my_spawn_agent'])
def test_unverified_tools_ignored(tool):
    event = payload()
    event['tool_name'] = tool
    assert H.handle(event, decide=lambda *a, **k: pytest.fail('No classifier')) == {}


@pytest.mark.parametrize('change', [{'fork_turns': 'all'}, {'fork_turns': None},
                                  {'fork_context': True}, {'agent_type': 'reviewer'}])
def test_full_fork_and_named_role_preserved(change):
    event = payload()
    event['tool_input'].update(change)
    result = H.handle(event, decide=lambda *a, **k: pytest.fail('No classifier'))
    assert 'updatedInput' not in result['hookSpecificOutput']
    assert 'не изменена' in result['hookSpecificOutput']['additionalContext']


def test_missing_fork_preserved():
    event = payload()
    del event['tool_input']['fork_turns']
    assert 'updatedInput' not in H.handle(event, decide=lambda *a, **k: pytest.fail('No classifier'))['hookSpecificOutput']


@pytest.mark.parametrize('status', ['pending', 'needs_review', 'unavailable', 'denied'])
def test_nonselected_never_rewrites(status):
    result = H.handle(payload(), decide=lambda *a, **kw: {'status': status})
    assert 'updatedInput' not in result['hookSpecificOutput']


@pytest.mark.parametrize('bad', [{'model': None, 'effort': 'low'}, {'model': 'x', 'effort': 'max'},
                               {'model': 'x', 'effort': 'ultra'}, {'model': 'x', 'effort': None}])
def test_bad_selection_is_noop(bad):
    result = H.handle(payload(), decide=lambda *a, **kw: {'status': 'selected', **bad})
    assert 'updatedInput' not in result['hookSpecificOutput']


def test_failures_preserve_route():
    def fail(*a, **kw):
        raise RuntimeError('private details')
    result = H.handle(payload(), decide=fail)
    assert 'private details' not in json.dumps(result)
    assert 'updatedInput' not in result['hookSpecificOutput']


@pytest.mark.parametrize('auth_status', ['denied', 'unavailable', 'needs_review'])
def test_failed_reauthorization_denies_spawn(auth_status):
    out = H.handle(payload(), decide=selected, authorize=lambda s: {'status': auth_status})['hookSpecificOutput']
    assert out['permissionDecision'] == 'deny' and 'updatedInput' not in out


def test_authorize_exception_denies_spawn():
    def fail(selection):
        raise RuntimeError('secret')
    out = H.handle(payload(), decide=selected, authorize=fail)['hookSpecificOutput']
    assert out['permissionDecision'] == 'deny' and 'secret' not in json.dumps(out)


def test_other_events_not_claimed_as_model_switch():
    event = payload()
    event['hook_event_name'] = 'UserPromptSubmit'
    assert H.handle(event, decide=lambda *a, **kw: pytest.fail('No classifier')) == {}


@pytest.mark.parametrize('raw', ['not-json', 'null', '[]', 'x' * 65537])
def test_cli_invalid_input_denied_without_inference(raw):
    proc = subprocess.run([sys.executable, str(SCRIPT)], input=raw, text=True, capture_output=True, check=False)
    assert proc.returncode == 0 and proc.stderr == ''
    assert json.loads(proc.stdout)['hookSpecificOutput']['permissionDecision'] == 'deny'


@pytest.mark.parametrize('failure', ['needs_review', 'unavailable', 'denied', 'exception',
                                   'bad_model', 'bad_effort', 'unhashable_effort', 'empty', 'oversize'])
def test_original_astra_never_survives_failed_routing(failure):
    event = payload()
    event['tool_input']['model'] = 'gpt-6-astra'
    def decide(*args, **kwargs):
        if failure == 'exception':
            raise RuntimeError('private classifier details')
        if failure == 'bad_model':
            return {'status': 'selected', 'model': None, 'effort': 'low'}
        if failure == 'unhashable_effort':
            return {'status': 'selected', 'model': 'gpt-6-astra', 'effort': []}
        if failure == 'bad_effort':
            return {'status': 'selected', 'model': 'gpt-6-astra', 'effort': 'max'}
        return {'status': failure}
    if failure in {'empty', 'oversize'}:
        event['tool_input']['message'] = '' if failure == 'empty' else 'x' * 24001
        decide = lambda *a, **k: pytest.fail('No classification for invalid message')
    out = H.handle(event, decide=decide,
                   authorize=lambda s: pytest.fail('No unselected authorization'))['hookSpecificOutput']
    assert out['permissionDecision'] == 'deny'
    assert 'updatedInput' not in out
    assert 'private classifier details' not in json.dumps(out)


@pytest.mark.parametrize('change', [{'fork_turns': 'all'}, {'agent_type': 'reviewer'}])
def test_original_astra_named_role_or_full_fork_still_host_owned(change):
    event = payload()
    event['tool_input'].update(model='gpt-6-astra', **change)
    out = H.handle(event, decide=lambda *a, **k: pytest.fail('No classifier'))['hookSpecificOutput']
    assert 'permissionDecision' not in out and 'updatedInput' not in out


def test_real_helper_blocks_third_astra_spawn_without_inference(tmp_path, monkeypatch):
    import host_routing as routing

    cfg = routing.L.load_routing()
    model = routing.L.model_for(cfg, 'openai', 'T4')
    quota = tmp_path / 'quota.json'
    cfg['automatic_routing'] = {
        'classifier': 'local-d1',
        'host_routing': {'enabled_hosts': ['codex'], 'models': {'codex': [model]}},
        't4': {'enabled': True, 'approval_ref': 'offline-hook-test',
               'max_calls': 2, 'usage_file': str(quota)},
    }
    # Only the model classifier is substituted: selection, reservation and file
    # quota validation all use the production helper. No model is invoked.
    monkeypatch.setattr(routing.R, 'classify_request', lambda *a, **k: {
        'status': 'accepted', 'labels': {'tier': 'T4', 'effort': 'normal'}})
    event = payload()
    event['tool_input']['model'] = model
    dispatched = []
    receipts = []
    for _ in range(3):
        out = H.handle(event,
                       decide=lambda *a, **k: routing.decide(*a, **k, load_config=lambda: cfg),
                       authorize=lambda s: routing.authorize(s, load_config=lambda: cfg))['hookSpecificOutput']
        receipts.append(out)
        if out.get('permissionDecision') != 'deny':
            dispatched.append(out.get('updatedInput', event['tool_input']))
    assert len(dispatched) == 2
    assert all(item['model'] == model for item in dispatched)
    assert receipts[2]['permissionDecision'] == 'deny'
    assert 'updatedInput' not in receipts[2]
    assert json.loads(quota.read_text())['calls'] == 2


@pytest.mark.parametrize('event', [
    {'hook_event_name': 'SessionStart'},
    {'hook_event_name': 'PreToolUse', 'tool_name': 'exec_command', 'tool_input': {}},
])
def test_cli_valid_unrelated_event_preserved(event):
    proc = subprocess.run([sys.executable, str(SCRIPT)], input=json.dumps(event),
                          text=True, capture_output=True, check=False)
    assert proc.returncode == 0 and json.loads(proc.stdout) == {} and proc.stderr == ''


def test_cli_oversized_astra_payload_denied_without_inference():
    event = payload()
    event['tool_input'].update(model='gpt-6-astra', message='x' * 65536)
    proc = subprocess.run([sys.executable, str(SCRIPT)], input=json.dumps(event),
                          text=True, capture_output=True, check=False)
    out = json.loads(proc.stdout)['hookSpecificOutput']
    assert proc.returncode == 0 and proc.stderr == ''
    assert out['permissionDecision'] == 'deny'
    assert '65536' in out['permissionDecisionReason']


def test_main_unexpected_failure_denies_unknown_spawn(monkeypatch, capsys):
    import io
    monkeypatch.setattr(sys, 'stdin', io.TextIOWrapper(io.BytesIO(json.dumps(payload()).encode())))
    def fail(event):
        raise RuntimeError('private error details')
    monkeypatch.setattr(H, 'handle', fail)
    assert H.main() == 0
    out = capsys.readouterr()
    assert json.loads(out.out)['hookSpecificOutput']['permissionDecision'] == 'deny'
    assert 'private error details' not in out.out and out.err == ''


@pytest.mark.parametrize('tool_name', sorted(H.TOOLS))
def test_every_alias_denies_exhausted_real_quota(tool_name, tmp_path, monkeypatch):
    import host_routing as routing
    import install_host_routing as installer

    installed = installer.merged_hooks({}, tmp_path)['hooks']['PreToolUse'][0]['matcher'].split('|')
    assert set(installed) <= H.TOOLS
    cfg = routing.L.load_routing()
    model = routing.L.model_for(cfg, 'openai', 'T4')
    quota = tmp_path / 'quota.json'
    quota.write_text(json.dumps({'approval_ref': 'offline-alias-test', 'calls': 2}))
    cfg['automatic_routing'] = {
        'classifier': 'local-d1',
        'host_routing': {'enabled_hosts': ['codex'], 'models': {'codex': [model]}},
        't4': {'enabled': True, 'approval_ref': 'offline-alias-test',
               'max_calls': 2, 'usage_file': str(quota)},
    }
    monkeypatch.setattr(routing.R, 'classify_request', lambda *a, **k: {
        'status': 'accepted', 'labels': {'tier': 'T4', 'effort': 'normal'}})
    event = payload()
    event['tool_name'] = tool_name
    event['tool_input']['model'] = model
    out = H.handle(event,
                   decide=lambda *a, **k: routing.decide(*a, **k, load_config=lambda: cfg),
                   authorize=lambda s: routing.authorize(s, load_config=lambda: cfg))['hookSpecificOutput']
    assert out['permissionDecision'] == 'deny' and 'updatedInput' not in out
    assert json.loads(quota.read_text())['calls'] == 2


@pytest.mark.parametrize('failure', ['quota', 'exception', 'oversize'])
def test_custom_t4_fallback_denied(failure, tmp_path, monkeypatch):
    import host_routing as routing
    cfg = copy.deepcopy(routing.L.load_routing())
    cfg['families']['openai']['T4'] = 'gpt-betelgeuse'
    quota = tmp_path / 'quota.json'
    quota.write_text(json.dumps({'approval_ref': 'custom-t4', 'calls': 2}))
    cfg['automatic_routing'] = {
        'classifier': 'local-d1',
        'host_routing': {'enabled_hosts': ['codex'], 'models': {'codex': ['gpt-betelgeuse']}},
        't4': {'enabled': True, 'approval_ref': 'custom-t4', 'max_calls': 2, 'usage_file': str(quota)},
    }
    monkeypatch.setattr(routing.L, 'load_routing', lambda: cfg)
    monkeypatch.setattr(routing.R, 'classify_request', lambda *a, **k: {
        'status': 'accepted', 'labels': {'tier': 'T4', 'effort': 'normal'}})
    def decide(*a, **kw):
        if failure == 'exception':
            raise RuntimeError('private details')
        return routing.decide(*a, **kw, load_config=lambda: cfg)
    event = payload()
    event['tool_input']['model'] = 'gpt-betelgeuse'
    if failure == 'oversize':
        event['tool_input']['message'] = 'x' * 24001
    out = H.handle(event, decide=decide)['hookSpecificOutput']
    assert out['permissionDecision'] == 'deny'
    assert 'updatedInput' not in out
    assert json.loads(quota.read_text())['calls'] == 2


@pytest.mark.parametrize("message", ["Extract source facts", "x" * 24001, ""], ids=['normal', 'oversize', 'empty'])
def test_disabled_router_leaves_host_model_and_authorization_alone(monkeypatch, message):
    import host_routing as routing
    cfg = copy.deepcopy(routing.L.load_routing())
    cfg['automatic_routing']['host_routing']['enabled_hosts'] = []
    monkeypatch.setattr(routing.L, 'load_routing', lambda: cfg)
    monkeypatch.setattr(routing.R, 'classify_request', lambda *a, **k: pytest.fail('disabled classifier'))
    event = payload()
    event['tool_input'].update(model='gpt-6-astra', message=message)
    out = H.handle(event,
        decide=lambda *a, **kw: routing.decide(*a, **kw, load_config=lambda: cfg),
        authorize=lambda s: pytest.fail('disabled authorization'))['hookSpecificOutput']
    assert 'permissionDecision' not in out and 'updatedInput' not in out



def test_catalog_t4_not_in_current_inventory_is_still_protected(monkeypatch):
    import models as M
    monkeypatch.setattr(M, 'load_catalog', lambda: [
        {'id': 'gpt-renamed-senior', 'family': 'openai', 'tier': 'T4', 'kind': 'general'}])
    event = payload()
    event['tool_input']['model'] = 'gpt-renamed-senior'
    out = H.handle(event, decide=lambda *a, **kw: {'status': 'needs_review'})['hookSpecificOutput']
    assert out['permissionDecision'] == 'deny'


def test_unreadable_policy_cannot_bypass_original_route(monkeypatch):
    import ledger as L
    def fail():
        raise ValueError('private config details')
    monkeypatch.setattr(L, 'load_routing', fail)
    event = payload()
    event['tool_input']['model'] = 'custom-senior'
    out = H.handle(event, decide=lambda *a, **kw: {'status': 'unavailable'})['hookSpecificOutput']
    assert out['permissionDecision'] == 'deny'
    assert 'private config details' not in json.dumps(out)

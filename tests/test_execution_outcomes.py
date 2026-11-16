import hashlib
import json

import pytest
import delegate as D
from test_codex_subscription import subscription


def change(cfg, binary, old, new):
    binary.write_text(binary.read_text(encoding="utf-8").replace(old, new))
    cfg['execution_policy']['codex_subscription']['binary_sha256'] = hashlib.sha256(binary.read_bytes()).hexdigest()


def saved(project):
    return json.loads(next((project / 'state/delegated').glob('*.receipt.json')).read_text(encoding="utf-8"))


EVENT = "print(json.dumps({'type': 'turn.completed', 'usage': {'input_tokens': 10, 'output_tokens': 5}}))"


def test_started_exit3_keeps_transmitted(subscription):
    cfg, args, calls, binary, project = subscription
    change(cfg, binary, EVENT, 'sys.exit(3)')
    assert D.main(args) == 1
    r = saved(project)
    assert r['started'] is True and r['process_exit_code'] == 3
    assert r['transmitted']['model'] == 'm' and r['result'] == 'failed'
    assert '0 inference calls' not in (project / 'state/run_log.md').read_text(encoding="utf-8")


@pytest.mark.parametrize('replacement', ["print('[]')", "print('null')", "print('broken-json')",
    "print(json.dumps({'type': 'turn.completed', 'usage': []}))",
    "print(json.dumps({'type': 'turn.completed', 'usage': {'input_tokens': 'oops'}}))"])
def test_invalid_events_are_controlled(subscription, replacement):
    cfg, args, calls, binary, project = subscription
    change(cfg, binary, EVENT, replacement)
    assert D.main(args) == 1
    r = saved(project)
    assert r['process_exit_code'] == 0 and r['exit_code'] == 1
    assert r['result'] == 'invalid_result' and r['observed']['model'] is None
    assert not list((project / 'state/delegated').glob('*.md'))
    assert (project / 'state/run_log.md').read_text(encoding="utf-8").count('делегирование') == 1


def test_empty_result_is_not_success(subscription):
    cfg, args, calls, binary, project = subscription
    change(cfg, binary, "write_text('subscription answer')", "write_text('')")
    assert D.main(args) == 1
    r = saved(project)
    assert r['process_exit_code'] == 0 and r['exit_code'] == 1 and r['result'] == 'invalid_result'


def test_probe_mutation_stays_not_started(subscription):
    cfg, args, calls, binary, project = subscription
    change(cfg, binary, "print('Logged in using ChatGPT')", "Path(os.environ['CODEX_HOME'], 'config.toml').write_text('bad=1'); print('Logged in using ChatGPT')")
    assert D.main(args) == 3
    r = saved(project)
    assert r['started'] is False and r['process_exit_code'] is None and r['result'] == 'not_started'


@pytest.mark.parametrize('payload', [[], {'answers': []}, {'answers': {}},
    {'answers': {'q': {'type': 'noul', 'noul': 'bad'}}},
    {'answers': {'q': {'type': 'noul', 'noul': .9}}, 'usage': []}])
def test_decision_result_validation_rejects_malformed_payload(payload):
    import decide as DC
    assert not DC.valid_response(payload, {'q': {}})


def test_wrapper_scope_does_not_claim_cli_retry_enforcement(subscription):
    _, args, _, _, project = subscription
    assert D.main(args) == 0
    r = saved(project)
    assert r['max_attempts_scope'] == 'wrapper_invocations' and r['wrapper_invocations'] == 1
    assert r['internal_transport_retries'] == 'unknown_version_dependent'
    assert r['inference_calls'] is None

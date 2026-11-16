import hashlib
import json
import sys
from pathlib import Path

import pytest
import delegate as D
import ledger as L
import routing_policy as P
from test_codex_subscription import subscription


@pytest.fixture
def claude_profile(subscription, monkeypatch):
    cfg, args, calls, binary, project = subscription
    p = cfg['execution_policy'].pop('codex_subscription')
    cfg['execution_policy']['claude_subscription'] = p
    home = Path(p.pop('codex_home'))
    (home / 'auth.json').rename(home / '.credentials.json')
    p.update(config_dir=str(home), credential_store='file', version='claude fake-reviewed')
    binary.write_text('''#!PYTHON
import json, os, sys
from pathlib import Path
args = sys.argv[1:]
with Path(CALLS).open('a') as f:
    f.write(json.dumps({'args': args, 'env': dict(os.environ)}) + '\\n')
if '--version' in args:
    print('claude fake-reviewed')
elif 'status' in args:
    print(json.dumps({'authMethod': 'claude.ai', 'configDirectory': os.environ['CLAUDE_CONFIG_DIR'], 'email': 'PRIVATE_SENTINEL'}))
else:
    assert '-p' in args and '--restricted' in args
    assert args[args.index('--tools') + 1] == ''
    assert args[args.index('--disallowedTools') + 1] == '*'
    assert json.loads(args[args.index('--settings') + 1]) == {'disableAllHooks': True}
    assert '--strict-mcp-config' in args and '--disable-slash-commands' in args
    assert json.loads(args[args.index('--mcp-config') + 1]) == {'mcpServers': {}}
    assert args[args.index('--effort') + 1] == 'low'
    print(json.dumps({'type': 'result', 'result': 'subscription answer', 'is_error': False, 'usage': {'input_tokens': 10, 'output_tokens': 5}}))
'''.replace('PYTHON', sys.executable).replace('CALLS', repr(str(calls))))
    p['binary_sha256'] = hashlib.sha256(binary.read_bytes()).hexdigest()
    cfg['families'] = {'claude': {'T1': 'm'}}
    cfg['delegate_backends'] = ['claude']
    args[args.index('--host') + 1] = 'claude'
    monkeypatch.setattr(P, 'CLAUDE_SYSTEM_DIRS', (home.parent / 'managed-claude',))
    return cfg, args, calls, binary, project


@pytest.mark.parametrize('host', ['claude', 'claude-code'])
def test_claude_success_isolated_env_and_no_private_auth_output(claude_profile, monkeypatch, host):
    cfg, args, calls, _, project = claude_profile
    args[args.index('--host') + 1] = host
    for name in ('ANTHROPIC_API_KEY', 'ANTHROPIC_AUTH_TOKEN', 'CLAUDE_CODE_OAUTH_TOKEN', 'ANTHROPIC_BASE_URL',
                 'ANTHROPIC_PROFILE', 'AWS_PROFILE', 'CLAUDE_CODE_USE_BEDROCK', 'CLAUDE_CODE_USE_VERTEX',
                 'CLAUDE_CONFIG_DIR', 'NODE_OPTIONS', 'ORCA_AGENT_HOOK_TOKEN'):
        monkeypatch.setenv(name, 'LEAK_SENTINEL')
    assert D.main(args) == 0
    assert 'LEAK_SENTINEL' not in calls.read_text()
    assert len(calls.read_text().splitlines()) == 3
    assert all('PRIVATE_SENTINEL' not in f.read_text() for f in project.rglob('*') if f.is_file())
    receipt = json.loads(next((project / 'state/delegated').glob('*.receipt.json')).read_text())
    assert receipt['started'] and receipt['process_exit_code'] == 0 and receipt['result'] == 'succeeded'
    assert receipt['billing']['auth'] == 'claude.ai' and receipt['observed']['model'] is None
    assert receipt['internal_transport_retries'] == 'unknown_version_dependent'


@pytest.mark.parametrize('mode', ['api_key', 'oauth_token', 'third_party', 'none', 'directory', 'settings', 'managed', 'version', 'changed-during-probe'])
def test_claude_rejects_before_inference(claude_profile, mode):
    cfg, args, calls, binary, project = claude_profile
    p = cfg['execution_policy']['claude_subscription']
    if mode in {'api_key', 'oauth_token', 'third_party', 'none'}:
        binary.write_text(binary.read_text().replace("'authMethod': 'claude.ai'", "'authMethod': " + repr(mode)))
    elif mode == 'directory':
        binary.write_text(binary.read_text().replace("'configDirectory': os.environ['CLAUDE_CONFIG_DIR']", "'configDirectory': '/other'"))
    elif mode == 'settings':
        (Path(p['config_dir']) / 'settings.json').write_text('{"apiKeyHelper":"evil"}')
    elif mode == 'managed':
        P.CLAUDE_SYSTEM_DIRS[0].mkdir()
    elif mode == 'version':
        p['version'] = 'other'
    else:
        binary.write_text(binary.read_text().replace("elif 'status' in args:", "elif 'status' in args:\n    Path(os.environ['CLAUDE_CONFIG_DIR'], 'settings.json').write_text('{}')"))
    p['binary_sha256'] = hashlib.sha256(binary.read_bytes()).hexdigest()
    assert D.main(args) == 3
    if calls.exists():
        assert all('-p' not in json.loads(l)['args'] for l in calls.read_text().splitlines())
    receipt = json.loads(next((project / 'state/delegated').glob('*.receipt.json')).read_text())
    assert receipt['result'] == 'not_started' and not receipt['started']


def test_claude_preflight_no_spawn(claude_profile, capsys):
    _, args, calls, _, project = claude_profile
    assert D.main(args + ['--preflight']) == 0
    assert json.loads(capsys.readouterr().out)['status'] == 'executable'
    assert not calls.exists() and not (project / 'state/delegated').exists()


@pytest.mark.parametrize('body,code,outcome', [('[]', 0, 'invalid_result'),
    ('{"result":""}', 0, 'invalid_result'), ('{"result":"ok"}', 3, 'failed'),
    ('{"result":"ok","model":"wrong"}', 0, 'quarantined'),
    ('{"result":"quota","is_error":true}', 0, 'failed')])
def test_claude_result_validation(claude_profile, body, code, outcome):
    cfg, args, calls, binary, project = claude_profile
    old = "print(json.dumps({'type': 'result', 'result': 'subscription answer', 'is_error': False, 'usage': {'input_tokens': 10, 'output_tokens': 5}}))"
    binary.write_text(binary.read_text().replace(old, 'print(' + repr(body) + '); sys.exit(' + str(code) + ')'))
    cfg['execution_policy']['claude_subscription']['binary_sha256'] = hashlib.sha256(binary.read_bytes()).hexdigest()
    assert D.main(args) == 1
    receipt = json.loads(next((project / 'state/delegated').glob('*.receipt.json')).read_text())
    assert receipt['result'] == outcome and receipt['process_exit_code'] == code and receipt['started']
    assert not list((project / 'state/delegated').glob('*.md'))
    assert sum('-p' in json.loads(l)['args'] for l in calls.read_text().splitlines()) == 1

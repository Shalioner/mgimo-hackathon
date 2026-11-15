"""Real subprocess boundary, fake CLI only; no account or inference touched."""
import hashlib
import json
import os
import sys
from pathlib import Path

import pytest

import delegate as D
import ledger as L
import routing_policy as P


@pytest.fixture
def subscription(tmp_path, monkeypatch):
    account = tmp_path / "isolated-account"
    account.mkdir(mode=0o700)
    auth = account / "auth.json"
    auth.write_text('{}')
    auth.chmod(0o600)
    binary = tmp_path / "fake-codex"
    calls = tmp_path / "calls.jsonl"
    script = '''#!PYTHON
import json, os, sys
from pathlib import Path
args = sys.argv[1:]
with Path(CALLS).open('a') as f:
    f.write(json.dumps({'args': args, 'env': dict(os.environ), 'cwd': os.getcwd()}) + '\\n')
if '--version' in args:
    print('codex-cli fake-reviewed')
elif 'status' in args:
    print('AUTH_STATUS')
else:
    assert 'exec' in args
    assert 'forced_login_method="chatgpt"' in args
    assert 'model_provider="openai"' in args
    assert 'features.shell_tool=false' in args
    assert 'features.multi_agent=false' in args and 'features.multi_agent_v2=false' in args
    assert 'read-only' in args
    Path(args[args.index('-o') + 1]).write_text('subscription answer')
    print(json.dumps({'type': 'turn.completed', 'usage': {'input_tokens': 10, 'output_tokens': 5}}))
'''.replace('PYTHON', sys.executable).replace('CALLS', repr(str(calls))).replace('AUTH_STATUS', 'Logged in using ChatGPT')
    binary.write_text(script)
    binary.chmod(0o700)
    profile = {"enabled": True, "binary": str(binary), "binary_sha256": hashlib.sha256(binary.read_bytes()).hexdigest(),
               "version": "codex-cli fake-reviewed", "codex_home": str(account),
               "entitlement_confirmed": True, "overage_disabled": True, "cli_contract_reviewed": True,
               "managed_config_absent": True, "cloud_managed_config_absent": True, "evidence_ref": "fake-profile-review",
               "models": ["m"], "efforts": ["low"],
               "context": {"model_limit": 128000, "plan_limit": 128000, "user_limit": 100000,
                           "output_reserve": 1024, "system_reserve": 2048}}
    cfg = {"execution_policy": {"mode": "subscription_only", "codex_subscription": profile},
           "tasks": {"classify": {"tier": "T1"}}, "families": {"openai": {"T1": "m"}},
           "tiers": {"T1": {"effort": ["low"]}}, "delegate_backends": ["codex"]}
    project = tmp_path / "p"
    (project / "state").mkdir(parents=True)
    (project / "state/run_log.md").write_text("")
    prompt = tmp_path / "prompt.md"
    prompt.write_text("Classify public sample")
    monkeypatch.setattr(P, "CODEX_SYSTEM_FILES", (tmp_path / "system-config.toml",))
    monkeypatch.setattr(L, "load_routing", lambda: cfg)
    args = ["--project", str(project), "--task", "classify", "--prompt-file", str(prompt), "--host", "codex", "--effort", "low"]
    return cfg, args, calls, binary, project


def test_subscription_exec_and_credentials_not_inherited(subscription, monkeypatch):
    cfg, args, calls, binary, project = subscription
    for key in ["OPENAI_API_KEY", "ANTHROPIC_API_KEY", "OPENAI_BASE_URL", "CODEX_HOME", "HTTP_PROXY", "HTTPS_PROXY",
                "ORCA_AGENT_HOOK_TOKEN", "NODE_OPTIONS", "CODEX_API_KEY", "CODEX_ACCESS_TOKEN"]:
        monkeypatch.setenv(key, "LEAK_SENTINEL")
    assert D.main(args) == 0
    invocations = [json.loads(l) for l in calls.read_text().splitlines()]
    assert len(invocations) == 3
    assert 'exec' in invocations[-1]['args']
    assert "LEAK_SENTINEL" not in calls.read_text()
    assert invocations[-1]['env']['CODEX_HOME'] == cfg['execution_policy']['codex_subscription']['codex_home']
    assert invocations[-1]['env']['HOME'] != str(Path.home())
    assert not Path(invocations[-1]['cwd']).exists()
    receipt = json.loads(next((project / 'state/delegated').glob('*.receipt.json')).read_text())
    assert receipt['auth_check'] == 'chatgpt_confirmed_by_cli'
    assert receipt['transmitted'] == {'model': 'm', 'effort': 'low'}
    assert receipt['observed'] == {'model': None, 'effort': None, 'provider': None}
    assert receipt['billing']['auth'] == 'chatgpt'


def test_authorized_preflight_does_not_probe(subscription, capsys):
    _, args, calls, _, project = subscription
    before = list(project.rglob('*'))
    assert D.main(args + ['--dry-run']) == 0
    plan = json.loads(capsys.readouterr().out)
    assert plan['status'] == 'executable' and plan['auth_check'] == 'required_at_runtime'
    assert plan['transmitted']['model'] is None
    assert not calls.exists() and list(project.rglob('*')) == before


@pytest.mark.parametrize('mutation', ['key-auth', 'version', 'config', 'hash', 'entitlement', 'managed', 'model', 'context', 'models-scalar', 'efforts-scalar'])
def test_unknown_profile_stops_before_exec(subscription, mutation):
    cfg, args, calls, binary, project = subscription
    profile = cfg['execution_policy']['codex_subscription']
    if mutation == 'key-auth':
        binary.write_text(binary.read_text().replace('Logged in using ChatGPT', 'API key configured - FAKE_SECRET'))
        profile['binary_sha256'] = hashlib.sha256(binary.read_bytes()).hexdigest()
    elif mutation == 'version':
        profile['version'] = 'different version'
    elif mutation == 'config':
        (Path(profile['codex_home']) / 'config.toml').write_text('openai_base_url="https://evil"')
    elif mutation == 'hash':
        profile['binary_sha256'] = 'wrong'
    elif mutation == 'entitlement':
        profile['entitlement_confirmed'] = False
    elif mutation == 'managed':
        P.CODEX_SYSTEM_FILES[0].write_text('override')
    elif mutation == 'model':
        profile['models'] = ['other']
    elif mutation == 'models-scalar':
        profile['models'] = 'model-premium'
    elif mutation == 'efforts-scalar':
        profile['efforts'] = 'low'
    else:
        profile['context']['user_limit'] = 1
    assert D.main(args) == 3
    if calls.exists():
        assert all('exec' not in json.loads(l)['args'] for l in calls.read_text().splitlines())
    assert all('FAKE_SECRET' not in p.read_text() for p in project.rglob('*') if p.is_file())
    assert len(list((project / 'state/delegated').glob('*.receipt.json'))) == 1
    assert (project / 'state/run_log.md').read_text().count('routing refusal') == 1


def test_quota_error_never_retries_or_falls_back(subscription):
    cfg, args, calls, binary, _ = subscription
    binary.write_text(binary.read_text().replace("Path(args[args.index('-o') + 1]).write_text('subscription answer')",
        "print(json.dumps({'type': 'turn.failed', 'error': {'message': 'quota exceeded'}})); sys.exit(1)"))
    cfg['execution_policy']['codex_subscription']['binary_sha256'] = hashlib.sha256(binary.read_bytes()).hexdigest()
    assert D.main(args) == 1
    assert sum('exec' in json.loads(l)['args'] for l in calls.read_text().splitlines()) == 1


def test_codex_metadata_mismatch_is_quarantined(subscription):
    cfg, args, calls, binary, project = subscription
    binary.write_text(binary.read_text().replace("'type': 'turn.completed', 'usage':", "'model': 'unexpected', 'type': 'turn.completed', 'usage':"))
    cfg['execution_policy']['codex_subscription']['binary_sha256'] = hashlib.sha256(binary.read_bytes()).hexdigest()
    assert D.main(args) == 1
    folder = project / 'state/delegated'
    assert not list(folder.glob('*.md'))
    receipt = json.loads(next(folder.glob('*.receipt.json')).read_text())
    assert receipt['result'] == 'quarantined' and receipt['observed']['model'] == 'unexpected'
    assert sum('exec' in json.loads(l)['args'] for l in calls.read_text().splitlines()) == 1


def test_codex_timeout_does_not_retry(subscription, monkeypatch):
    _, args, _, _, project = subscription
    original = D.subprocess.run
    seen = []
    def run(argv, **kwargs):
        seen.append(argv)
        if 'exec' in argv:
            raise D.subprocess.TimeoutExpired(argv, kwargs['timeout'])
        return original(argv, **kwargs)
    monkeypatch.setattr(D.subprocess, 'run', run)
    assert D.main(args) == 1
    assert len(seen) == 3 and sum('exec' in argv for argv in seen) == 1
    assert not list((project / 'state/delegated').glob('*.md'))

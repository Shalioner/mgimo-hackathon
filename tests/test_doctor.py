import concurrent.futures
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

import doctor as D
import install_host_routing as I


@pytest.mark.parametrize('encoding', ['ascii', 'cp1251', 'utf-8'])
@pytest.mark.parametrize('command', ['doctor', 'skills'])
def test_json_cli_preserves_unicode_with_stdout_encoding(command, encoding):
    result = subprocess.run(
        [sys.executable, str(Path(D.__file__)), command, '--host', 'codex', '--json'],
        capture_output=True,
        env={**os.environ, 'PYTHONIOENCODING': f'{encoding}:strict'},
        timeout=10,
    )
    assert result.returncode == 0, result.stderr.decode('ascii', errors='backslashreplace')
    report = D.diagnose('codex')
    expected = report['skills'] if command == 'skills' else report
    assert json.loads(result.stdout) == expected


def test_catalog_is_local_not_personal_global_skills():
    rows = D.inventory()
    assert len(rows) == 13
    assert {r['name'] for r in rows} == set(D.PURPOSES)
    assert all(r['description'] and r['path'].startswith('skills/') for r in rows)


def test_unknown_host_never_claims_working_even_if_codex_enabled():
    result = D.diagnose('cursor', cfg={'automatic_routing': {'host_routing': {'enabled_hosts':['codex']}}})
    assert result['routing']['status'] == 'unverified'
    assert result['inference_calls'] == 0 and result['changes'] == []
    assert 'не проверен' in D.welcome(result)


def test_codex_desktop_config_is_not_trust_or_runtime(tmp_path):
    (tmp_path/'.codex').mkdir()
    for name in ['.venv/bin/python', 'tools/hooks/host_prompt.py', 'tools/hooks/codex_routing.py']:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('fixture')
        path.chmod(0o700)
    hooks = I.merged_hooks({}, tmp_path)
    (tmp_path/'.codex/hooks.json').write_text(json.dumps(hooks))
    report=D.diagnose('codex-app',root=tmp_path,cfg={'automatic_routing':{'host_routing':{'enabled_hosts':['codex']}}})
    assert report['routing']['status']=='configured_not_verified'
    assert report['routing']['trust']=='requires_host_review'


def test_once_per_profile_across_hosts_and_concurrent_calls(tmp_path):
    report=D.diagnose('unknown',cfg={})
    def show(_):
        return D.first_run(report,state_dir=tmp_path)
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        outputs=list(pool.map(show,range(8)))
    assert sum(bool(o) for o in outputs)==1
    assert D.first_run(D.diagnose('claude-code',cfg={}),state_dir=tmp_path)==''
    assert 'Начните обычным сообщением' in next(o for o in outputs if o)


def test_repeatable_preview_has_no_state_side_effects(tmp_path,monkeypatch):
    monkeypatch.setenv('XDG_STATE_HOME',str(tmp_path))
    assert D.main(['welcome','--host','cursor'])==0
    assert list(tmp_path.iterdir())==[]


def test_welcome_keeps_unverified_model_status_with_installation_guidance():
    local = D.welcome(D.diagnose('codex-app', cfg={
        'automatic_routing': {'classifier': 'local-d1'}}))
    assert 'доступность сервиса и качество ответов этим осмотром не проверены' in local
    unknown = D.welcome(D.diagnose('cursor', cfg={}))
    assert 'Decision-модель не подтверждена' in unknown
    for text in (local, unknown):
        assert 'https://huggingface.co/LiquidAI/d1-2B-MLX-8bit' in text
        assert 'Ниже 8-bit пока не рекомендуем: качество не проверено' in text
        assert 'docs/D1_MACOS.md' in text


def test_invalid_routing_shape_declines():
    result=D.diagnose('codex',cfg={'automatic_routing':'invalid'})
    assert result['routing']['status']=='needs_setup'
    assert 'Исправить конфигурацию' in result['next_steps'][0]


def test_doctor_rejects_stale_paths_missing_scripts_and_nonexecutable(tmp_path):
    (tmp_path / '.codex').mkdir()
    for name in ['.venv/bin/python', 'tools/hooks/host_prompt.py', 'tools/hooks/codex_routing.py']:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('fixture')
        path.chmod(0o700)
    cfg = {'automatic_routing': {'host_routing': {'enabled_hosts': ['codex']}}}
    config = tmp_path / '.codex/hooks.json'
    config.write_text(json.dumps(I.merged_hooks({}, tmp_path / 'old')))
    assert D.diagnose('codex', root=tmp_path, cfg=cfg)['routing']['status'] == 'needs_setup'
    config.write_text(json.dumps(I.merged_hooks({}, tmp_path)))
    assert D.diagnose('codex', root=tmp_path, cfg=cfg)['routing']['status'] == 'configured_not_verified'
    (tmp_path / '.venv/bin/python').chmod(0o600)
    report = D.diagnose('codex', root=tmp_path, cfg=cfg)
    assert not report['python_ready'] and report['routing']['status'] == 'needs_setup'
    (tmp_path / '.venv/bin/python').chmod(0o700)
    (tmp_path / 'tools/hooks/host_prompt.py').unlink()
    assert D.diagnose('codex', root=tmp_path, cfg=cfg)['routing']['status'] == 'needs_setup'


def test_doctor_rejects_names_only_and_duplicate_registration(tmp_path):
    for name in ['.venv/bin/python', 'tools/hooks/host_prompt.py', 'tools/hooks/codex_routing.py']:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('fixture')
        path.chmod(0o700)
    hooks = I.merged_hooks({}, tmp_path)
    assert I.hooks_ready(hooks, tmp_path)
    hooks['hooks']['UserPromptSubmit'] *= 2
    assert not I.hooks_ready(hooks, tmp_path)
    hooks = I.merged_hooks({}, tmp_path)
    hooks['hooks']['PreToolUse'][0]['matcher'] = 'wrong_tool'
    assert not I.hooks_ready(hooks, tmp_path)
    hooks = {'hooks': {event: [{'hooks': [{'command': 'python ' + script}]}] for event, script, _ in I.HOOKS}}
    assert not I.hooks_ready(hooks, tmp_path)


def test_doctor_unknown_legacy_alongside_current_needs_setup(tmp_path):
    for name in ['.venv/bin/python', 'tools/hooks/host_prompt.py', 'tools/hooks/codex_routing.py']:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('fixture')
        path.chmod(0o700)
    cfg = {'automatic_routing': {'host_routing': {'enabled_hosts': ['codex']}}}
    (tmp_path / '.codex').mkdir()
    path = tmp_path / '.codex/hooks.json'
    for event, script, _ in I.HOOKS:
        hooks = I.merged_hooks({}, tmp_path)
        foreign = {'type': 'command', 'command': I.hook_command(tmp_path / 'unknown-old', script)}
        hooks['hooks'][event][0]['hooks'].append(foreign)
        merged = I.merged_hooks(hooks, tmp_path)
        assert foreign in merged['hooks'][event][0]['hooks']
        path.write_text(json.dumps(merged))
        assert D.diagnose('codex', root=tmp_path, cfg=cfg)['routing']['status'] == 'needs_setup'
    for malformed in (None, [], {}, 42):
        hooks = I.merged_hooks({}, tmp_path)
        hooks['hooks']['UserPromptSubmit'][0]['hooks'].append({'type': 'command', 'command': malformed})
        path.write_text(json.dumps(hooks))
        assert D.diagnose('codex', root=tmp_path, cfg=cfg)['routing']['status'] == 'needs_setup'

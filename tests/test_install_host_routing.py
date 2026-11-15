import copy
import json

import pytest

import install_host_routing as I


def test_preserves_original_hooks_and_idempotent(tmp_path):
    original = {'hooks': {'UserPromptSubmit': [{'hooks': [{'type': 'command', 'command': 'existing-tool'}]}]}, 'extra': True}
    before = copy.deepcopy(original)
    merged = I.merged_hooks(original, tmp_path)
    assert merged['hooks']['UserPromptSubmit'][0] == original['hooks']['UserPromptSubmit'][0]
    assert original == before and merged['extra'] is True
    assert I.merged_hooks(merged, tmp_path) == merged


def test_preserves_claude_config_and_plugin_paths(tmp_path):
    original = {'env': {'CLAUDE_CODE_PLUGIN_DIRS': '/one:/two', 'ANOTHER': 'preserve'}, 'permissions': {'deny': ['Bash']}}
    merged = I.merged_claude(original, tmp_path)
    assert merged['permissions'] == original['permissions']
    assert merged['env']['ANOTHER'] == 'preserve'
    assert merged['env']['CLAUDE_CODE_PLUGIN_DIRS'].startswith('/one:/two:')
    assert I.merged_claude(merged, tmp_path) == merged


def test_merge_inherited_plugin_dirs(tmp_path, monkeypatch):
    monkeypatch.setenv('CLAUDE_CODE_PLUGIN_DIRS', '/environment')
    monkeypatch.setenv('CLAUDE_CONFIG_DIR', str(tmp_path / 'global'))
    (tmp_path / 'global').mkdir()
    (tmp_path / 'global/settings.json').write_text(json.dumps({'env': {'CLAUDE_CODE_PLUGIN_DIRS': '/global'}}))
    (tmp_path / '.claude').mkdir()
    (tmp_path / '.claude/settings.json').write_text(json.dumps({'env': {'CLAUDE_CODE_PLUGIN_DIRS': '/project'}}))
    merged = I.merged_claude({'env': {'CLAUDE_CODE_PLUGIN_DIRS': '/local'}}, tmp_path, I.inherited_plugin_dirs(tmp_path))
    assert merged['env']['CLAUDE_CODE_PLUGIN_DIRS'].split(':') == ['/environment', '/global', '/project', '/local', str(tmp_path / 'plugins/strategist-routing')]


def test_validate_all_before_writing(tmp_path):
    for name in ['tools/hooks/host_prompt.py', 'tools/hooks/codex_routing.py',
                 'plugins/strategist-routing/hooks/hooks.json', '.venv/bin/python']:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('fixture')
        path.chmod(0o700)
    (tmp_path / '.claude').mkdir()
    (tmp_path / '.claude/settings.local.json').write_text('invalid JSON')
    with pytest.raises(ValueError):
        I.install(tmp_path, apply=True)
    assert not (tmp_path / '.codex/hooks.json').exists()


def test_dry_run_then_apply(tmp_path, monkeypatch):
    monkeypatch.setattr(I.Path, 'home', lambda: tmp_path)
    for name in ['tools/hooks/host_prompt.py', 'tools/hooks/codex_routing.py',
                 'plugins/strategist-routing/hooks/hooks.json', '.venv/bin/python']:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('fixture')
        path.chmod(0o700)
    result = I.install(tmp_path)
    assert all(r['status'] == 'pending' for r in result)
    assert not (tmp_path / '.codex/hooks.json').exists()
    assert all(r['status'] == 'installed' for r in I.install(tmp_path, apply=True))
    assert all(r['status'] == 'current' for r in I.install(tmp_path, apply=True))


def test_relocation_replaces_owned_hooks_preserves_mixed_rows(tmp_path):
    old = tmp_path / 'old checkout'
    new = tmp_path / 'new checkout'
    original = I.merged_hooks({}, old)
    foreign = {'type': 'command', 'command': I.hook_command(tmp_path / 'foreign', 'host_prompt.py')}
    row = original['hooks']['UserPromptSubmit'][0]
    row['hooks'].append(foreign)
    row['extra'] = 'preserve'
    relocated = I.merged_hooks(original, new)
    assert relocated['hooks']['UserPromptSubmit'][0] == {'hooks': [foreign], 'extra': 'preserve'}
    assert str(old) not in json.dumps(relocated)
    assert I.merged_hooks(relocated, new) == relocated
    for event, script, _ in I.HOOKS:
        assert sum(I.owned_hook(h, new, script) for r in relocated['hooks'][event] for h in r['hooks']) == 1


def test_exact_shipped_legacy_migration_and_foreign_same_names(tmp_path):
    legacy = {'hooks': {event: [{'hooks': [
        {'type': 'command', 'command': I.hook_command(I.LEGACY_ROOT, script)},
        {'type': 'command', 'command': 'python ' + script},
        {'type': 'command', 'command': I.hook_command(tmp_path / 'foreign', script)},
    ]}] for event, script, _ in I.HOOKS}}
    result = I.merged_hooks(legacy, tmp_path)
    assert str(I.LEGACY_ROOT) not in json.dumps(result)
    for event, script, _ in I.HOOKS:
        assert result['hooks'][event][0]['hooks'] == legacy['hooks'][event][0]['hooks'][1:]
    assert I.merged_hooks(result, tmp_path) == result


def test_checkout_template_is_portable_and_installable(tmp_path):
    template = json.loads((I.L.ROOT / '.codex/hooks.json').read_text())
    assert '/Users/' not in json.dumps(template)
    result = I.merged_hooks(template, tmp_path)
    for event, script, _ in I.HOOKS:
        owned = [h for r in result['hooks'][event] for h in r['hooks'] if I.owned_hook(h, tmp_path, script)]
        assert len(owned) == 1
        assert str(tmp_path) in owned[0]['command']
    assert I.merged_hooks(result, tmp_path) == result


def test_nonexecutable_python_refused_before_writes(tmp_path):
    for name in ['tools/hooks/host_prompt.py', 'tools/hooks/codex_routing.py',
                 'plugins/strategist-routing/hooks/hooks.json', '.venv/bin/python']:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('fixture')
    with pytest.raises(ValueError, match='not executable'):
        I.install(tmp_path, apply=True)
    assert not (tmp_path / '.codex/hooks.json').exists()


def test_clean_install_copy_reinstall_and_idempotence(tmp_path, monkeypatch):
    import shutil
    monkeypatch.setattr(I.Path, 'home', lambda: tmp_path / 'profile')
    monkeypatch.delenv('CLAUDE_CODE_PLUGIN_DIRS', raising=False)
    monkeypatch.setenv('CLAUDE_CONFIG_DIR', str(tmp_path / 'profile/claude'))
    source = tmp_path / 'source'
    recipient = tmp_path / 'recipient checkout'
    for name in ['tools/hooks/host_prompt.py', 'tools/hooks/codex_routing.py',
                 'plugins/strategist-routing/hooks/hooks.json', '.venv/bin/python']:
        path = source / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('fixture')
        path.chmod(0o700)
    assert all(r['status'] == 'installed' for r in I.install(source, apply=True))
    shutil.copytree(source, recipient)
    # Only a disposable fixture is removed; the original checkout is unavailable.
    shutil.rmtree(source)
    assert not I.hooks_ready(json.loads((recipient / '.codex/hooks.json').read_text()), recipient)
    I.install(recipient, apply=True)
    hooks = json.loads((recipient / '.codex/hooks.json').read_text())
    assert I.hooks_ready(hooks, recipient)
    assert str(source) not in json.dumps(hooks)
    assert all(r['status'] == 'current' for r in I.install(recipient, apply=True))

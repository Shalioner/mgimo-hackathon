#!/usr/bin/env python3
"""Install project-scoped native hooks, preserving existing hooks and settings.

No credential changes, executable replacement, marketplace install or API calls.
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
from pathlib import Path
import shlex
import tempfile

import ledger as L


# Shell comments carry ownership without relying on unsupported host JSON fields.
MARKER = 'strategist-host-routing:v1'
LEGACY_ROOT = Path('/Users/shalioner/orca/workspaces/МегаФон-Стратег/night-orchestrator')
HOOKS = [
    ('UserPromptSubmit', 'host_prompt.py', None),
    ('PreToolUse', 'codex_routing.py', 'spawn_agent|collaborationspawn_agent|collaboration.spawn_agent|mcp__collaboration__spawn_agent'),
]


def hook_command(root, script):
    return ' '.join(shlex.quote(str(p)) for p in
                    (root / '.venv/bin/python', root / 'tools/hooks' / script))


def owned_hook(hook, root, script):
    """Only explicit ownership or exact known registrations; never a basename match."""
    command = hook.get('command', '')
    if hook.get('type') != 'command' or not isinstance(command, str):
        return False
    if command in {hook_command(root, script), hook_command(LEGACY_ROOT, script)}:
        return True
    return command.endswith(' # ' + MARKER + ':' + script)


def merged_hooks(current, root):
    import copy
    root = Path(root)
    result = copy.deepcopy(current)
    hooks = result.setdefault('hooks', {})
    for event, script, matcher in HOOKS:
        command = hook_command(root, script) + ' # ' + MARKER + ':' + script
        entry = {'hooks': [{'type': 'command', 'command': command, 'timeout': 45}]}
        if matcher:
            entry['matcher'] = matcher
        rows = hooks.setdefault(event, [])
        retained = []
        for row in rows:
            others = [h for h in row.get('hooks', []) if not owned_hook(h, root, script)]
            if others == row.get('hooks', []):
                retained.append(row)
            elif others:
                retained.append({**row, 'hooks': others})
        hooks[event] = retained + [entry]
    return result


def hooks_ready(settings, root):
    """Check current checkout targets and executable without running hook code."""
    root = Path(root).resolve()
    python = root / '.venv/bin/python'
    if not python.is_file() or not os.access(python, os.X_OK):
        return False
    for event, script, matcher in HOOKS:
        if not (root / 'tools/hooks' / script).is_file():
            return False
        matches = []
        for row in settings.get('hooks', {}).get(event, []):
            for hook in row.get('hooks', []):
                if hook.get('type') != 'command':
                    continue
                command = hook.get('command', '')
                if not isinstance(command, str):
                    return False
                try:
                    argv = shlex.split(command, comments=True)
                except ValueError:
                    if owned_hook(hook, root, script):
                        return False
                    continue
                if len(argv) != 2:
                    if owned_hook(hook, root, script):
                        return False
                    continue
                expected = [python, root / 'tools/hooks' / script]
                # Do not resolve the interpreter symlink: it may point to a shared Python.
                actual = [Path(os.path.abspath(root / arg)) for arg in argv]
                if actual == expected and row.get('matcher') == matcher:
                    matches.append(hook)
                elif owned_hook(hook, root, script):
                    return False  # stale managed registration alongside current one
                elif Path(argv[1]).parts[-3:] == ('tools', 'hooks', script):
                    # Unknown legacy ownership: preserve it on install, but do not
                    # claim a clean setup while another checkout can also execute.
                    return False
        if len(matches) != 1:
            return False
    return True


def merged_claude(current, root, inherited_dirs=()):
    import copy
    result = copy.deepcopy(current)
    env = result.setdefault('env', {})
    value = env.get('CLAUDE_CODE_PLUGIN_DIRS', '')
    if not isinstance(value, str):
        raise ValueError('CLAUDE_CODE_PLUGIN_DIRS must be text')
    paths = list(dict.fromkeys([*inherited_dirs, *[p for p in value.split(os.pathsep) if p]]))
    plugin = str(root / 'plugins/strategist-routing')
    if plugin not in paths:
        paths.append(plugin)
    env['CLAUDE_CODE_PLUGIN_DIRS'] = os.pathsep.join(paths)
    return result


def inherited_plugin_dirs(root):
    """Read only the named setting; never expose unrelated env/credential values."""
    values = [os.environ.get('CLAUDE_CODE_PLUGIN_DIRS', '')]
    config_dir = Path(os.environ.get('CLAUDE_CONFIG_DIR') or Path.home() / '.claude')
    for path in [config_dir / 'settings.json', root / '.claude/settings.json']:
        if path.is_file():
            data = json.loads(path.read_text())
            values.append((data.get('env') or {}).get('CLAUDE_CODE_PLUGIN_DIRS', ''))
    if not all(isinstance(value, str) for value in values):
        raise ValueError('Inherited plugin directories must be strings')
    return list(dict.fromkeys(p for value in values for p in value.split(os.pathsep) if p))


def install(root=L.ROOT, *, apply=False):
    root = Path(root).resolve()
    targets = [(root / '.codex/hooks.json', merged_hooks),
               (root / '.claude/settings.local.json', merged_claude)]
    missing = [str(p.relative_to(root)) for p in [root / 'tools/hooks/host_prompt.py',
               root / 'tools/hooks/codex_routing.py', root / 'plugins/strategist-routing/hooks/hooks.json',
               root / '.venv/bin/python'] if not p.is_file()]
    if not missing and not os.access(root / '.venv/bin/python', os.X_OK):
        raise ValueError('Python interpreter is not executable')
    if missing:
        raise ValueError('Missing installation files: ' + ', '.join(missing))
    plans = []
    for path, merge in targets:
        if path.is_symlink():
            raise ValueError('Refusing symlink settings target')
        old = path.read_bytes() if path.exists() else None
        current = json.loads(old) if old else {}
        if not isinstance(current, dict):
            raise ValueError('Object settings required')
        new = merge(current, root, inherited_plugin_dirs(root)) if merge is merged_claude else merge(current, root)
        plans.append((path, old, new, new != current))
    results = []
    for path, old, new, changed in plans:
        if apply and changed:
            # Validate both documents before any mutation; recheck current bytes.
            if (path.read_bytes() if path.exists() else None) != old:
                raise ValueError('Settings changed during installation')
            if old is not None:
                backup = Path.home() / '.cache/strategist/host-routing-backups'
                backup.mkdir(mode=0o700, parents=True, exist_ok=True)
                stamp = datetime.datetime.now().strftime('%Y%m%d-%H%M%S%f')
                fd = os.open(backup / (stamp + '-' + path.parent.name + '-' + path.name), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(fd, 'wb') as file:
                    file.write(old)
            path.parent.mkdir(exist_ok=True)
            fd, temp = tempfile.mkstemp(prefix='.routing-', dir=path.parent)
            try:
                with os.fdopen(fd, 'w') as file:
                    file.write(json.dumps(new, ensure_ascii=False, indent=2) + '\n')
                os.replace(temp, path)
            finally:
                if os.path.exists(temp):
                    os.unlink(temp)
        results.append({'path': str(path), 'status': 'installed' if apply and changed else 'pending' if changed else 'current'})
    return results


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args(argv)
    try:
        print(json.dumps(install(apply=args.apply), ensure_ascii=False, indent=2))
    except (ValueError, OSError, TypeError) as exc:
        parser.error(str(exc))


if __name__ == '__main__':
    main()

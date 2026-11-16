#!/usr/bin/env python3
"""Install/start/status/stop the user-local D1 service without changing other jobs."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import plistlib
import stat
import subprocess
import sys
import time

LABEL = 'local.strategist.d1'
ROOT = Path(__file__).resolve().parents[1]
DIRECTORY = Path.home() / '.cache/strategist/d1-service'
PLIST = Path.home() / 'Library/LaunchAgents' / (LABEL + '.plist')
PYTHON = Path.home() / '.cache/strategist/d1-2b-lab/mlx-venv/bin/python'
LAUNCHCTL = '/bin/launchctl'


def private_directory(path):
    if path.is_symlink():
        raise ValueError('Refusing symlink service directory')
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = path.stat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise ValueError('Service directory must be owned by current user and mode 0700')


def definition(idle_seconds=300):
    if type(idle_seconds) is not int or not 30 <= idle_seconds <= 3600:
        raise ValueError('Idle unload must be 30..3600 seconds')
    return {'Label': LABEL,
            'ProgramArguments': [str(PYTHON), str(ROOT / 'tools/d1_service.py'), 'serve',
                                 '--socket', str(DIRECTORY / 'service.sock'), '--idle-seconds', str(idle_seconds)],
            'WorkingDirectory': str(DIRECTORY), 'RunAtLoad': True,
            'KeepAlive': {'SuccessfulExit': False}, 'ThrottleInterval': 30,
            'ProcessType': 'Background', 'ExitTimeOut': 15, 'Umask': 0o077,
            'StandardOutPath': str(DIRECTORY / 'supervisor.log'),
            'StandardErrorPath': str(DIRECTORY / 'supervisor.err.log'),
            'EnvironmentVariables': {'PATH': '/usr/bin:/bin', 'HOME': str(Path.home()),
                                     'PYTHONNOUSERSITE': '1', 'HF_HUB_OFFLINE': '1',
                                     'TRANSFORMERS_OFFLINE': '1', 'TOKENIZERS_PARALLELISM': 'false'}}


def install(idle_seconds=300):
    if sys.platform != 'darwin':
        raise ValueError('This user service installer requires macOS')
    if not PYTHON.is_file() or not os.access(PYTHON, os.X_OK) or not (ROOT / 'tools/d1_service.py').is_file():
        raise ValueError('Existing MLX environment and service source required; no automatic installation')
    private_directory(DIRECTORY)
    desired = definition(idle_seconds)
    if PLIST.is_symlink():
        raise ValueError('Refusing symlink LaunchAgent')
    if PLIST.exists():
        existing = plistlib.loads(PLIST.read_bytes())
        if existing != desired:
            raise ValueError('Existing LaunchAgent differs; inspect explicitly before replacement')
    else:
        PLIST.parent.mkdir(parents=True, exist_ok=True)
        with PLIST.open('xb') as file:
            plistlib.dump(desired, file)
        PLIST.chmod(0o600)
    return {'status': 'installed', 'plist': str(PLIST), 'socket': str(DIRECTORY / 'service.sock')}


def launchctl(*args):
    return subprocess.run([LAUNCHCTL, *args], capture_output=True, text=True, timeout=20, encoding="utf-8")


def status():
    import d1_service
    try:
        return d1_service.request(str(DIRECTORY / 'service.sock'), {'op': 'status'}, timeout=3)
    except (FileNotFoundError, ConnectionRefusedError) as exc:
        registered = launchctl('print', f'gui/{os.getuid()}/{LABEL}').returncode == 0
        return {'status': 'unreachable' if registered else 'stopped', 'reason': str(exc),
                'registered': registered, 'installed': PLIST.is_file()}
    except (OSError, ValueError) as exc:
        return {'status': 'unknown', 'reason': str(exc), 'installed': PLIST.is_file()}


def verify_ownership(job):
    if PLIST.is_symlink() or not PLIST.is_file():
        raise ValueError('Cannot verify ownership of existing LaunchAgent')
    existing = plistlib.loads(PLIST.read_bytes())
    args = existing.get('ProgramArguments', [])
    try:
        idle = int(args[-1])
    except (IndexError, TypeError, ValueError):
        raise ValueError('Existing LaunchAgent does not match this service')
    if existing != definition(idle):
        raise ValueError('Existing LaunchAgent does not match this service; refusing to stop it')
    if job.returncode == 0 and ('path = ' + str(PLIST)) not in job.stdout:
        raise ValueError('Loaded job origin differs from installed service; refusing to stop it')


def start(idle_seconds=300):
    install(idle_seconds)
    target = f'gui/{os.getuid()}/{LABEL}'
    job = launchctl('print', target)
    if job.returncode:
        proc = launchctl('bootstrap', f'gui/{os.getuid()}', str(PLIST))
    else:
        verify_ownership(job)
        if status().get('service') == 'running':
            return status()
        # No -k: never kill a running worker just to make start idempotent.
        proc = launchctl('kickstart', target)
    if proc.returncode:
        raise ValueError('launchctl start failed: ' + proc.stderr.strip())
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        current = status()
        if current.get('service') == 'running':
            return current
        time.sleep(0.2)
    raise ValueError('Service did not become available; inspect supervisor.err.log')


def stop():
    # Launchd delivers SIGTERM to this job. The service owns/joins its model child.
    target = f'gui/{os.getuid()}/{LABEL}'
    job = launchctl('print', target)
    registered = job.returncode == 0
    if registered:
        verify_ownership(job)
        proc = launchctl('bootout', target)
        if proc.returncode:
            raise ValueError('launchctl stop failed: ' + proc.stderr.strip())
    else:
        import d1_service
        if (DIRECTORY / 'service.sock').exists():
            verify_ownership(job)
        try:
            d1_service.request(str(DIRECTORY / 'service.sock'), {'op': 'stop'}, timeout=3)
        except (FileNotFoundError, ConnectionRefusedError):
            pass
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        current = status()
        if current.get('status') == 'stopped':
            return {'status': 'stopped', 'installed': PLIST.is_file()}
        time.sleep(0.2)
    raise ValueError('Service shutdown not confirmed')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['install', 'start', 'status', 'stop'])
    parser.add_argument('--idle-seconds', type=int, default=300)
    args = parser.parse_args(argv)
    try:
        result = {'install': lambda: install(args.idle_seconds), 'start': lambda: start(args.idle_seconds),
                  'status': status, 'stop': stop}[args.command]()
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        print(json.dumps({'status': 'error', 'reason': str(exc)}, ensure_ascii=False))
        return 3


if __name__ == '__main__':
    for _stream in (sys.stdin, sys.stdout, sys.stderr):  # Windows: pipes/console default to cp1251
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    raise SystemExit(main())

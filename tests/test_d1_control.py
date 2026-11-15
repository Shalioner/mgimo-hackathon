import plistlib
import socket
import sys
from types import SimpleNamespace

import pytest

import d1_control as C


@pytest.fixture
def control(tmp_path, monkeypatch):
    root = tmp_path / 'repo'
    (root / 'tools').mkdir(parents=True)
    (root / 'tools/d1_service.py').write_text('# test only')
    binary = tmp_path / 'python'
    binary.write_text('# test only')
    binary.chmod(0o700)
    monkeypatch.setattr(C, 'ROOT', root)
    monkeypatch.setattr(C, 'PYTHON', binary)
    monkeypatch.setattr(C, 'DIRECTORY', tmp_path / 'service')
    monkeypatch.setattr(C, 'PLIST', tmp_path / 'LaunchAgents/local.strategist.d1.plist')
    monkeypatch.setattr(C.sys, 'platform', 'darwin')
    commands = []
    def launch(*args):
        commands.append(args)
        return SimpleNamespace(returncode=1, stdout='', stderr='not found')
    monkeypatch.setattr(C, 'launchctl', launch)
    return commands


def test_install_idempotent_and_no_other_jobs(control):
    C.install()
    data = C.PLIST.read_bytes()
    assert C.DIRECTORY.stat().st_mode & 0o777 == 0o700
    assert C.PLIST.stat().st_mode & 0o777 == 0o600
    C.install()
    assert C.PLIST.read_bytes() == data and not control
    assert plistlib.loads(data)['ProgramArguments'][1] == str(C.ROOT / 'tools/d1_service.py')


def test_existing_foreign_job_not_overwritten_or_stopped(control, monkeypatch):
    C.install()
    foreign = C.definition()
    foreign['ProgramArguments'] = ['/foreign/program']
    C.PLIST.write_bytes(plistlib.dumps(foreign))
    with pytest.raises(ValueError, match='differs'):
        C.install()
    def registered(*args):
        control.append(args)
        return SimpleNamespace(returncode=0, stdout='path = ' + str(C.PLIST), stderr='')
    monkeypatch.setattr(C, 'launchctl', registered)
    with pytest.raises(ValueError, match='match this service'):
        C.stop()
    assert all(cmd[0] == 'print' for cmd in control)


@pytest.mark.parametrize('error', [socket.timeout('timeout'), PermissionError('denied'), ValueError('bad JSON')])
def test_unreachable_not_reported_as_stopped(control, monkeypatch, error):
    def fail(*a, **kw):
        raise error
    monkeypatch.setitem(sys.modules, 'd1_service', SimpleNamespace(request=fail))
    assert C.status()['status'] == 'unknown'


def test_stopped_requires_missing_job(control, monkeypatch):
    def absent(*a, **kw):
        raise FileNotFoundError('absent')
    monkeypatch.setitem(sys.modules, 'd1_service', SimpleNamespace(request=absent))
    assert C.status()['status'] == 'stopped'
    monkeypatch.setattr(C, 'launchctl', lambda *a: SimpleNamespace(returncode=0))
    assert C.status()['status'] == 'unreachable'


def test_registered_origin_mismatch_not_kickstarted(control, monkeypatch):
    C.install()
    def registered(*args):
        control.append(args)
        return SimpleNamespace(returncode=0, stdout='path = /foreign/job.plist', stderr='')
    monkeypatch.setattr(C, 'launchctl', registered)
    with pytest.raises(ValueError, match='origin differs'):
        C.start()
    assert all(cmd[0] == 'print' for cmd in control)

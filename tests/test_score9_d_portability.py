"""D3 portability checks: Darwin local probes vs explicit platform-substituted mocks.

IMPORTANT:
- Live Windows and live WSL2 environments are NOT present on this Darwin arm64 host.
- Tests marked/using `monkeypatch` for `win32`/`nt`/`linux` are platform-substituted
  boundary checks, NOT live Windows or WSL2 execution.
- Live ~/.cache/strategist/d1-service/service.sock (owned by D2) is NEVER queried.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parent.parent
TOOLS = ROOT / 'tools'
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import d1_control as D1C  # noqa: E402
import decision_runtime as DR  # noqa: E402
import doctor as DOC  # noqa: E402
import install_host_routing as IHR  # noqa: E402
import ledger as L  # noqa: E402
import local_decision as LD  # noqa: E402
import opencode_free as OCF  # noqa: E402
import route_request as RR  # noqa: E402
import routing_policy as RP  # noqa: E402


@pytest.fixture(name="D1S")
def d1_service_module():
    pytest.importorskip("fcntl", reason="Тест внутреннего UNIX-сервиса требует POSIX fcntl")
    import d1_service
    return d1_service


# ---------------------------------------------------------------------------
# 1. Native Darwin offline checks (isolated temp paths, never live D1 socket)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(sys.platform != 'darwin', reason='Darwin-specific missing-socket local-d1 preflight')
def test_darwin_local_d1_missing_socket_stays_manual(tmp_path):
    missing_sock = tmp_path / 'absent-dir' / 'service.sock'
    cfg = {
        'automatic_routing': {
            'classifier': 'local-d1',
            'local_d1': {'mode': 'service', 'socket': str(missing_sock)},
        }
    }
    receipt = DR.plan('test state', RR.questions(cfg), cfg)
    assert receipt['status'] == 'manual'
    assert receipt['started'] is False
    assert receipt['result'] == 'not_started'
    assert receipt['network'] == 'denied'
    assert 'No such file or directory' in receipt['reason']


@pytest.mark.skipif(sys.platform != 'darwin', reason='Darwin-specific d1_service missing-socket CLI check')
def test_darwin_d1_service_missing_socket_returns_unavailable(tmp_path, capsys, D1S):
    missing_sock = tmp_path / 'missing-service' / 'service.sock'
    rc = D1S.main(['status', '--socket', str(missing_sock)])
    out = json.loads(capsys.readouterr().out)
    assert rc == 3
    assert out['status'] == 'unavailable'
    assert 'No such file or directory' in out['reason']


@pytest.mark.skipif(sys.platform != 'darwin', reason='Darwin-specific d1_control isolated install refusal')
def test_darwin_d1_control_install_requires_existing_mlx_env(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(D1C, 'PYTHON', tmp_path / 'missing-mlx-venv' / 'bin' / 'python')
    monkeypatch.setattr(D1C, 'DIRECTORY', tmp_path / 'd1-service')
    monkeypatch.setattr(D1C, 'PLIST', tmp_path / 'LaunchAgents' / 'local.strategist.d1.plist')
    rc = D1C.main(['install'])
    out = json.loads(capsys.readouterr().out)
    assert rc == 3
    assert out == {
        'status': 'error',
        'reason': 'Existing MLX environment and service source required; no automatic installation',
    }


def test_default_repo_config_keeps_route_request_manual_and_not_started():
    cfg = {
        'automatic_routing': {'classifier': 'bootstrap', 'bootstrap_host': 'codex'},
        'execution_policy': {'mode': 'subscription_only', 'codex_subscription': {'enabled': False}},
        'families': {'openai': {'T1': 'gpt-6-luna'}},
        'tiers': {'T1': {'effort': ['low']}},
    }
    report = RR.run_request(
        'Извлеки факты из таблицы',
        '',
        ['codex'],
        execute=True,
        classify_only=True,
        load_config=lambda: cfg,
    )
    assert report['status'] == 'manual'
    assert report['classification']['status'] == 'unavailable'
    assert report['classification']['route']['status'] == 'manual'
    assert 'Нет явно включённого Codex subscription profile' in report['classification']['reason']
    assert report['result'] == {'status': 'not_started', 'text': None}


def test_classify_only_never_starts_task_execution():
    cfg = {
        'automatic_routing': {'classifier': 'local-d1', 'local_d1': {'mode': 'service'}},
        'families': {'openai': {'T1': 'gpt-6-luna'}},
        'tiers': {'T1': {'effort': ['low']}},
    }
    fake_classify = {
        'status': 'accepted',
        'labels': {'tier': 'T1', 'effort': 'normal', 'capability': 'text'},
        'route': {'status': 'executable', 'result': 'succeeded'},
    }
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(RR, 'classify_request', lambda *a, **kw: fake_classify)
        report = RR.run_request(
            'Сравни два тарифа',
            '',
            ['codex'],
            execute=True,
            classify_only=True,
            load_config=lambda: cfg,
        )
    assert report['status'] == 'classified'
    assert report['result'] == {'status': 'not_started', 'text': None}
    assert report['start'] is None


# ---------------------------------------------------------------------------
# 2. Platform-substituted checks: Native Windows (win32 / nt) — NOT live Windows
# ---------------------------------------------------------------------------


def test_platform_sub_windows_local_d1_and_mlx_refused_as_manual(monkeypatch):
    monkeypatch.setattr(DR.sys, 'platform', 'win32')
    cfg = {'automatic_routing': {'classifier': 'local-d1', 'local_d1': {'mode': 'service'}}}
    receipt = DR.plan('test state', RR.questions(cfg), cfg)
    assert receipt['status'] == 'manual'
    assert receipt['reason'] == 'Local d1 MLX requires macOS'
    assert receipt['started'] is False
    assert receipt['result'] == 'not_started'


class _NtOsProxy:
    """Module-scoped os proxy reporting os.name='nt' without breaking global pathlib on POSIX."""

    name = 'nt'

    def __getattr__(self, item):
        return getattr(os, item)


def test_platform_sub_windows_opencode_and_posix_executors_refused(monkeypatch):
    monkeypatch.setattr(OCF.sys, 'platform', 'win32')
    err = OCF.platform_error()
    assert err == 'OpenCode adapter supports only reviewed POSIX platforms (macOS/Linux); Windows is manual'

    monkeypatch.setattr(RP, 'os', _NtOsProxy())
    codex_cfg = {'execution_policy': {'mode': 'subscription_only', 'codex_subscription': {'enabled': True}}}
    claude_cfg = {'execution_policy': {'mode': 'subscription_only', 'claude_subscription': {'enabled': True}}}
    assert RP.codex_profile(codex_cfg)[1] == 'Codex adapter v1 проверяет только POSIX config boundary'
    assert RP.claude_profile(claude_cfg)[1] == 'Claude adapter v1 поддерживает POSIX boundary'

    codex_plan = RP.plan(codex_cfg, host='codex', provider='openai', model='gpt-6-luna', effort='low', tier='T1')
    claude_plan = RP.plan(claude_cfg, host='claude-code', provider='anthropic', model='haiku', tier='T1')
    assert codex_plan['status'] == 'manual' and codex_plan['started'] is False
    assert claude_plan['status'] == 'manual' and claude_plan['started'] is False


def test_platform_sub_windows_d1_control_and_service_refuse_non_macos(monkeypatch, capsys, D1S):
    monkeypatch.setattr(D1C.sys, 'platform', 'win32')
    rc = D1C.main(['install'])
    out = json.loads(capsys.readouterr().out)
    assert rc == 3
    assert out == {'status': 'error', 'reason': 'This user service requires macOS'}

    monkeypatch.setattr(D1S.sys, 'platform', 'win32')
    with pytest.raises(ValueError, match='Pinned MLX Python and macOS required'):
        D1S.Supervisor._spawn()


def test_platform_sub_windows_doctor_vs_install_host_routing_venv_boundary(tmp_path, monkeypatch):
    for rel in [
        'tools/hooks/host_prompt.py',
        'tools/hooks/codex_routing.py',
        'plugins/strategist-routing/hooks/hooks.json',
        '.venv/Scripts/python.exe',
    ]:
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text('stub', encoding='utf-8')
        p.chmod(0o700)
    (tmp_path / 'skills').mkdir()
    (tmp_path / '.codex').mkdir()
    (tmp_path / '.codex/hooks.json').write_text('{}', encoding='utf-8')

    monkeypatch.setattr(DOC, 'os', _NtOsProxy())
    report = DOC.diagnose(
        'codex',
        root=tmp_path,
        cfg={'automatic_routing': {'host_routing': {'enabled_hosts': ['codex']}}},
    )
    assert report['python_ready'] is True
    assert report['routing']['registered'] is False
    assert report['routing']['status'] == 'needs_setup'

    with pytest.raises(ValueError, match=r'Missing installation files: \.venv/bin/python'):
        IHR.install(tmp_path, apply=False)


def test_platform_sub_windows_strategist_hook_path_regex_and_ledger_lock_or_write(tmp_path, monkeypatch):
    hooks_dir = TOOLS / 'hooks'
    if str(hooks_dir) not in sys.path:
        sys.path.insert(0, str(hooks_dir))
    import strategist_hook as SH

    payload = '{"tool_input":{"file_path":"C:\\\\repo\\\\projects\\\\example\\\\state\\\\ledger\\\\claims.yaml"}}'
    assert SH.LEDGER_PATH.findall(payload) == ['example']

    target = tmp_path / 'sample.txt'
    L.atomic_write_text(target, 'ok\n')
    assert target.read_text(encoding='utf-8') == 'ok\n'

    # On candidate cb228422, _project_write_lock also delegates to msvcrt.locking on os.name == 'nt'.
    if hasattr(L, '_project_write_lock'):
        proj = tmp_path / 'proj'
        (proj / 'state').mkdir(parents=True)
        calls = []

        class FakeMsvcrt:
            LK_NBLCK = 2
            LK_UNLCK = 0

            @staticmethod
            def locking(fd, mode, nbytes):
                calls.append((mode, nbytes))

        monkeypatch.setattr(L, 'os', _NtOsProxy())
        monkeypatch.setitem(sys.modules, 'msvcrt', FakeMsvcrt)
        with L._project_write_lock(proj) as acquired:
            assert acquired is True
        assert calls == [(FakeMsvcrt.LK_NBLCK, 1), (FakeMsvcrt.LK_UNLCK, 1)]


# ---------------------------------------------------------------------------
# 3. Platform-substituted checks: WSL2 / Linux (linux / posix) — NOT live WSL2
# ---------------------------------------------------------------------------


def test_platform_sub_wsl2_linux_local_d1_and_opencode_boundaries(monkeypatch):
    monkeypatch.setattr(DR.sys, 'platform', 'linux')
    cfg = {'automatic_routing': {'classifier': 'local-d1', 'local_d1': {'mode': 'service'}}}
    receipt = DR.plan('test state', RR.questions(cfg), cfg)
    assert receipt['status'] == 'manual'
    assert receipt['reason'] == 'Local d1 MLX requires macOS'

    monkeypatch.setattr(OCF.sys, 'platform', 'linux')
    assert OCF.platform_error() is None


def test_platform_sub_wsl2_linux_d1_control_rejects_before_launchctl(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(D1C.sys, 'platform', 'linux')
    monkeypatch.setattr(D1C, 'DIRECTORY', tmp_path / 'd1-service')
    monkeypatch.setattr(D1C, 'PLIST', tmp_path / 'LaunchAgents' / 'local.strategist.d1.plist')
    monkeypatch.setattr(D1C, 'LAUNCHCTL', str(tmp_path / 'nonexistent-launchctl'))

    rc_install = D1C.main(['install'])
    out_install = json.loads(capsys.readouterr().out)
    assert rc_install == 3
    assert out_install == {'status': 'error', 'reason': 'This user service requires macOS'}

    rc_status = D1C.main(['status'])
    out_status = json.loads(capsys.readouterr().out)
    assert rc_status == 3
    assert out_status == out_install


def test_platform_sub_wsl2_drvfs_insecure_permissions_vs_ext4_private(tmp_path, D1S):
    # Simulates DrvFs without `metadata` (0o777 mirrored bits) vs WSL2 ext4 (0o700)
    drvfs_dir = tmp_path / 'drvfs-like'
    drvfs_dir.mkdir()
    drvfs_dir.chmod(0o777)
    if (drvfs_dir.stat().st_mode & 0o077) != 0:
        with pytest.raises(ValueError, match='mode 0700'):
            D1S.private_directory(drvfs_dir)

    ext4_dir = tmp_path / 'ext4-like'
    ext4_dir.mkdir(mode=0o700)
    ext4_dir.chmod(0o700)
    D1S.private_directory(ext4_dir)


def test_platform_sub_wsl2_memory_telemetry_unavailable_without_macos_sysctl(monkeypatch, D1S):
    monkeypatch.setattr(
        LD.subprocess,
        'run',
        lambda *a, **kw: SimpleNamespace(stdout='', returncode=1),
    )
    mem = LD.memory()
    assert mem['swap_used_mib'] is None
    assert mem['pressure_level'] is None
    assert D1S.memory_problem(mem) == 'Memory telemetry unavailable'

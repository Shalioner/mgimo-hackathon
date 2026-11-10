"""Тесты хуков (tools/hooks/strategist_hook.py) и их конфигурации для Claude Code и Codex."""
import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HOOK = ROOT / "tools" / "hooks" / "strategist_hook.py"


def run(mode, payload=None, timeout=30):
    return subprocess.run([sys.executable, str(HOOK), mode], input=payload, capture_output=True, text=True, encoding="utf-8",
                          timeout=timeout, cwd=str(ROOT))


def test_session_start_prints_status():
    r = run("session-start")
    assert r.returncode == 0 and "example" in r.stdout


def test_after_write_ignores_non_ledger_files():
    assert run("after-write", json.dumps({"tool_input": {"file_path": "README.md"}})).returncode == 0


def test_after_write_passes_valid_ledger():
    p = {"tool_name": "Edit", "tool_input": {"file_path": str(ROOT / "projects/example/state/ledger/claims.yaml")}}
    assert run("after-write", json.dumps(p)).returncode == 0


def test_after_write_blocks_broken_ledger_from_codex_patch():
    tmp = ROOT / "projects" / "zz_hooktest"
    shutil.rmtree(tmp, ignore_errors=True)
    shutil.copytree(ROOT / "projects" / "example", tmp)
    try:
        f = tmp / "state/ledger/claims.yaml"
        f.write_text(f.read_text(encoding="utf-8").replace("type: fact", "type: факт", 1), encoding="utf-8")
        p = {"tool_name": "apply_patch", "tool_input": {"command": "*** Update File: projects/zz_hooktest/state/ledger/claims.yaml"}}
        r = run("after-write", json.dumps(p))
        assert r.returncode == 2 and "CLM-001" in r.stderr
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_after_write_does_not_hang_on_open_stdin():
    proc = subprocess.Popen([sys.executable, str(HOOK), "after-write"], stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=str(ROOT))
    try:
        assert proc.wait(timeout=10) == 0  # stdin так и не закрыт
    finally:
        proc.kill()


def test_hook_configs_point_to_same_script():
    claude = json.loads((ROOT / ".claude/settings.json").read_text(encoding="utf-8"))
    codex = json.loads((ROOT / ".codex/hooks.json").read_text(encoding="utf-8"))
    for cfg in (claude, codex):
        cmds = [h["command"] for ev in ("SessionStart", "PostToolUse") for blk in cfg["hooks"][ev] for h in blk["hooks"]]
        assert all("strategist_hook.py" in c for c in cmds) and len(cmds) == 2

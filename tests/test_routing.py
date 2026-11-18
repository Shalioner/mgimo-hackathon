"""Тесты маршрутизации по средам и делегирования (без реальных вызовов моделей)."""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
import delegate as D  # noqa: E402
import ledger as L  # noqa: E402

TZ_ENVS = ["claude-code", "codex", "opencode", "cursor", "copilot", "windsurf", "gigacode", "kodik", "zcode"]


def test_all_tz_environments_have_a_switch_route():
    cfg = L.load_routing()
    for env in TZ_ENVS:
        e = cfg["environments"][env]
        assert e["switch"] and e["switch"][-1] == "current", env  # всегда есть запасной путь
        assert set(e["switch"]) <= set(L.SWITCH_TEXT), env


def test_families_cover_tiers_and_real_codex_ids():
    cfg = L.load_routing()
    got = {t: L.model_for(cfg, "openai", t) for t in ("T1", "T2", "T3", "T4")}
    assert got == {"T1": "gpt-6-luna", "T2": "gpt-6.1-terra", "T3": "gpt-6.1-sol", "T4": "gpt-6-astra"}
    assert cfg["families"]["claude"]["T1"] == "haiku" and L.model_for(cfg, "claude", "T4") == "fable"


@pytest.mark.parametrize("env,expected", [("codex", "gpt-6-luna"), ("claude-code", "haiku"),
                                          ("opencode", "opencode/deepseek-v4.1-flash-free")])
def test_route_env_gives_concrete_model(env, expected, capsys):
    assert L.main(["route", "extract-claims", "--env", env]) == 0
    assert expected in capsys.readouterr().out


def test_route_unknown_env(capsys):
    assert L.main(["route", "extract-claims", "--env", "notepad"]) == 2


def test_route_gigacode_honest_about_unknown_models(capsys):
    assert L.main(["route", "alternatives", "--env", "gigacode"]) == 0
    assert "не задана" in capsys.readouterr().out


# ---------- delegate ----------
@pytest.fixture
def proj(tmp_path):
    p = tmp_path / "p"
    L.main(["init", str(p), "--title", "Д"])
    (tmp_path / "q.md").write_text("Выпиши проценты.", encoding="utf-8")
    (tmp_path / "data.md").write_text("Синхронизация: 34% обращений.", encoding="utf-8")
    return p


def run(proj, *extra):
    q = proj.parent / "q.md"
    return D.main(["--project", str(proj), "--prompt-file", str(q), *extra])


def test_dry_run_builds_isolated_read_only_codex_call(proj, capsys, monkeypatch):
    monkeypatch.setattr(D.shutil, "which", lambda b: "/bin/" + b if b == "codex" else None)
    assert run(proj, "--task", "extract-claims", "--input", str(proj.parent / "data.md"), "--dry-run") == 0
    out = capsys.readouterr().out
    assert json.loads(out)["status"] == "manual"
    assert json.loads(out)["transmitted"]["model"] is None  # делегат не запускается в репозитории


def test_backend_never_falls_back_to_installed_cli(proj, capsys, monkeypatch):
    monkeypatch.setattr(D.shutil, "which", lambda b: "/bin/" + b if b == "opencode" else None)
    assert run(proj, "--task", "classify", "--dry-run") == 0
    assert json.loads(capsys.readouterr().out)["requested"]["host"] == "codex"


def test_t0_and_t4_are_not_delegated(proj, monkeypatch):
    monkeypatch.setattr(D.shutil, "which", lambda b: "/bin/" + b)
    assert run(proj, "--task", "validate", "--dry-run") == 2
    assert run(proj, "--task", "alternatives", "--tier", "T4", "--dry-run") == 2


def test_no_cli_means_current_model(proj, monkeypatch):
    monkeypatch.setattr(D.shutil, "which", lambda b: None)
    assert run(proj, "--task", "extract-claims", "--dry-run") == 0
    assert not (proj / "state/delegated").exists()


def test_escalated_tier_picks_bigger_model(proj, capsys, monkeypatch):
    monkeypatch.setattr(D.shutil, "which", lambda b: "/bin/" + b if b == "codex" else None)
    assert run(proj, "--task", "extract-claims", "--tier", "T2", "--dry-run") == 0
    assert "gpt-6.1-terra" in capsys.readouterr().out


def test_parse_codex_usage(tmp_path):
    out = tmp_path / "o.md"
    out.write_text("ответ", encoding="utf-8")
    events = "\n".join([json.dumps({"type": "item.completed", "item": {"type": "error"}}),
                        json.dumps({"type": "turn.completed",
                                    "usage": {"input_tokens": 25170, "cached_input_tokens": 7168,
                                              "output_tokens": 72}})])
    text, usage = D.parse_output("codex", events, out)
    assert text == "ответ" and "25170" in usage and "72" in usage


def test_failed_call_is_logged_and_nonzero(proj, monkeypatch):
    monkeypatch.setattr(D.shutil, "which", lambda b: "/bin/" + b if b == "codex" else None)

    class R:
        returncode, stdout, stderr = 1, "", "boom"
    monkeypatch.setattr(D.subprocess, "run", lambda *a, **k: pytest.fail("unauthorized spawn"))
    assert run(proj, "--task", "extract-claims", "--step", "3") == 3
    last = (proj / "state/run_log.md").read_text(encoding="utf-8").strip().splitlines()[-1]
    assert "routing refusal" in last and "codex:gpt-6-luna" in last


# ---------- локальные переопределения и новые среды ----------
def test_local_override_merges_over_routing(tmp_path):
    loc = tmp_path / "routing.local.yaml"
    loc.write_text("environments:\n  opencode: {family: opencode-free}\ndelegate_families:\n  opencode: opencode-free\n",
                   encoding="utf-8")
    cfg = L.load_routing(loc)
    assert cfg["environments"]["opencode"]["family"] == "opencode-free"
    assert cfg["environments"]["opencode"]["switch"]  # остальное не потеряно
    assert L.model_for(cfg, "opencode-free", "T1") == "opencode/deepseek-v4.1-flash-free"
    assert cfg["_local"] == str(loc)


def test_delegate_uses_local_family(tmp_path, monkeypatch, capsys):
    loc = tmp_path / "routing.local.yaml"
    loc.write_text("delegate_families:\n  opencode: opencode-free\n", encoding="utf-8")
    monkeypatch.setattr(L, "ROUTING_LOCAL_PATH", loc)
    monkeypatch.setattr(D.shutil, "which", lambda b: "/bin/" + b if b == "opencode" else None)
    p = tmp_path / "p"
    L.main(["init", str(p), "--title", "Д"])
    q = tmp_path / "q.md"
    q.write_text("x", encoding="utf-8")
    assert D.main(["--project", str(p), "--prompt-file", str(q), "--task", "classify", "--backend", "opencode", "--dry-run"]) == 0
    assert "deepseek-v4.1-flash-free" in capsys.readouterr().out


def test_zcode_env_uses_glm_ids(capsys):
    assert L.main(["route", "alternatives", "--env", "zcode"]) == 0
    assert "GLM-5.3" in capsys.readouterr().out


def test_claude_code_proxy_warning(monkeypatch, capsys):
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "http://localhost:1/v1")
    assert L.main(["route", "extract-claims", "--env", "claude-code"]) == 0
    assert "прокси" in capsys.readouterr().out


# ---------- определение среды ----------
@pytest.mark.parametrize("environ,expected", [
    ({"ZCODE_SESSION_ID": "s", "CLAUDE_SESSION_ID": "s", "CLAUDE_PROJECT_DIR": "/p"}, "zcode"),  # ZCode маскируется под Claude
    ({"CLAUDECODE": "1", "CLAUDE_CODE_ENTRYPOINT": "cli"}, "claude-code"),
    ({"CODEX_THREAD_ID": "t"}, "codex"),
    ({"CLAUDE_SESSION_ID": "s"}, "unknown"),  # одни CLAUDE_* без CLAUDECODE — не доказательство Claude Code
    ({}, "unknown"),
])
def test_detect_env(environ, expected):
    assert L.detect_env(environ)[0] == expected

"""Самообновление моделей: выбор новейшей версии по шаблону, check, apply, подсказки делегату."""
import datetime as dt
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
import delegate as D  # noqa: E402
import ledger as L  # noqa: E402
import models as M  # noqa: E402

SOL = {"pattern": "gpt-*-sol", "pin": "gpt-6.1-sol"}


def inv(*ids):
    return {"scanned_at": dt.datetime.now().astimezone().isoformat(), "sources": {"codex": list(ids)}}


@pytest.mark.parametrize("mid,key", [("gpt-6.1-sol", (6, 1)), ("claude-fable-5-2", (5, 2)),
                                     ("GLM-5.3-Flash", (5, 3)), ("opencode/deepseek-v4.1-flash-free", (4, 1))])
def test_version_key(mid, key):
    assert M.version_key(mid) == key


def test_new_version_is_picked_without_editing_files():
    ids = ["gpt-6-luna", "gpt-6.1-sol", "gpt-6.2-sol", "gpt-6.1-terra"]
    assert M.resolve_spec(SOL, ids)[0] == "gpt-6.2-sol"
    fable = {"pattern": "claude-fable-*", "pin": "claude-fable-5-2"}
    assert M.resolve_spec(fable, ["claude-fable-5-2", "claude-fable-5-3"])[0] == "claude-fable-5-3"


def test_pin_used_when_no_inventory_or_no_match():
    assert M.resolve_spec(SOL, []) == ("gpt-6.1-sol", "запасной ID (в инвентаре совпадений нет)")
    assert M.resolve_spec(SOL, ["gpt-6-luna"])[0] == "gpt-6.1-sol"
    assert M.resolve_spec("haiku", ["x"])[0] == "haiku" and M.resolve_spec(None, [])[0] is None


def test_regex_spec_does_not_mix_flash_and_full():
    full = {"regex": r"^GLM-\d+(\.\d+)?$", "pin": "GLM-5.3"}
    assert M.resolve_spec(full, ["GLM-5.3", "GLM-5.3-Flash", "GLM-5.4-Flash"])[0] == "GLM-5.3"


def test_check_reports_new_version_missing_and_stale_guide(monkeypatch):
    cfg = {"families": {"openai": {"T3": SOL}, "x": {"T1": "unknown-model-9"}}}
    msgs = M.check(cfg, inv("gpt-6.2-sol"))
    assert any("НОВАЯ ВЕРСИЯ" in m and "gpt-6.2-sol" in m for m in msgs)
    assert any("НЕТ ГАЙДА" in m and "unknown-model-9" in m for m in msgs)
    far = dt.date.today() + dt.timedelta(days=400)
    assert any("ГАЙД УСТАРЕЛ" in m and "openai.md" in m for m in M.check(cfg, inv("gpt-6.2-sol"), today=far))


def test_real_routing_has_guides_for_every_resolved_model():
    cfg = L.load_routing()
    msgs = M.check(cfg, None, today=dt.date(2026, 11, 10))
    assert not [m for m in msgs if "НЕТ ГАЙДА" in m], msgs


def test_provider_prefix_is_ignored_for_guides():
    g = M.guide_for("opencode/claude-haiku-5-5", M.load_guides())
    assert g and g["_file"] == "anthropic.md"


def test_apply_updates_pin_in_copy(tmp_path, monkeypatch):
    r = tmp_path / "routing.yaml"
    r.write_text("families:\n  openai:\n    T3: {pattern: \"gpt-*-sol\", pin: gpt-6.1-sol}\n", encoding="utf-8")
    monkeypatch.setattr(M, "ROUTING", r)
    cfg = {"families": {"openai": {"T3": SOL}}}
    assert M.apply_pins(cfg, inv("gpt-6.2-sol")) == ["openai/T3: gpt-6.1-sol → gpt-6.2-sol"]
    assert "pin: gpt-6.2-sol" in r.read_text(encoding="utf-8")
    assert M.apply_pins(cfg, inv("gpt-6.1-sol")) == []


def test_ledger_route_follows_inventory(monkeypatch, capsys):
    monkeypatch.setattr(M, "load_inventory", lambda: inv("gpt-6.1-sol", "gpt-6.2-sol"))
    assert L.main(["route", "alternatives", "--env", "codex"]) == 0
    assert "gpt-6.2-sol" in capsys.readouterr().out


def test_hints_and_delegate_prompt_include_them(tmp_path, monkeypatch, capsys):
    assert M.hints("openai") and M.hints("glm") and M.hints("no-such-family") == []
    monkeypatch.setattr(M, "load_inventory", lambda: None)
    monkeypatch.setattr(D.shutil, "which", lambda b: "/bin/" + b if b == "codex" else None)
    p = tmp_path / "p"
    L.main(["init", str(p), "--title", "Д"])
    q = tmp_path / "q.md"
    q.write_text("Выпиши проценты.", encoding="utf-8")
    seen = {}
    orig = D.build_command
    monkeypatch.setattr(D, "build_command", lambda b, m, pr, o, w: seen.setdefault("p", pr) and orig(b, m, pr, o, w))
    assert D.main(["--project", str(p), "--prompt-file", str(q), "--task", "extract-claims", "--dry-run"]) == 0
    assert "Особенности модели" in seen["p"] and M.hints("openai")[0] in seen["p"]
    assert seen["p"].index("Особенности модели") < seen["p"].index("Выпиши проценты.")

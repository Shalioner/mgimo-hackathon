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
    monkeypatch.setattr(D, "build_command", lambda b, m, pr, o, w, e=None: seen.setdefault("p", pr) and orig(b, m, pr, o, w, e))
    assert D.main(["--project", str(p), "--prompt-file", str(q), "--task", "extract-claims", "--dry-run"]) == 0
    assert "Особенности модели" in seen["p"] and M.hints("openai")[0] in seen["p"]
    assert seen["p"].index("Особенности модели") < seen["p"].index("Выпиши проценты.")


# ---------- новые имена, специализированные модели, effort, self-hosted ----------
CAT = [{"id": "gpt-betelgeuse", "family": "openai", "tier": "T4", "kind": "general", "replaces": "gpt-6-astra"},
       {"id": "gpt-rosalind*", "kind": "specialized", "domain": "life-sciences", "access": "restricted"}]
ASTRA = {"pattern": "gpt-*-astra", "pin": "gpt-6-astra"}


def test_new_name_outside_pattern_is_picked_via_catalog_replaces():
    ids = ["gpt-6-astra", "gpt-betelgeuse"]
    assert M.resolve_spec(ASTRA, ids, CAT, "openai", "T4")[0] == "gpt-betelgeuse"
    assert M.resolve_spec(ASTRA, ["gpt-6-astra"], CAT, "openai", "T4")[0] == "gpt-6-astra"  # ещё не вышла
    assert M.resolve_spec(ASTRA, ids, CAT, "openai", "T3")[0] == "gpt-6-astra"  # другой уровень не трогаем


def test_specialized_models_never_win_general_pattern():
    sol = {"pattern": "gpt-*", "pin": "gpt-6.1-sol"}
    ids = ["gpt-6.1-sol", "gpt-7-cyber", "gpt-rosalind-2", "gpt-7.0-preview"]
    assert M.resolve_spec(sol, ids, CAT)[0] == "gpt-6.1-sol"
    glm = {"regex": r"^glm-", "pin": "glm-5.3"}
    assert M.resolve_spec(glm, ["glm-5.3", "glm-5.5-law", "glm-5.6-vision"], [])[0] == "glm-5.3"
    assert M.is_specialized("ling-3.0-flash-fin-free", []) and not M.is_specialized("gpt-6.1-sol", [])


def test_restricted_option_list_falls_back():
    spec = [{"pattern": "claude-mythos-*"}, "fable"]
    assert M.resolve_spec(spec, []) [0] == "fable"
    assert M.resolve_spec(spec, ["claude-mythos-5-2"])[0] == "claude-mythos-5-2"


def test_scan_tracks_unreviewed_and_check_reports_new_model(tmp_path, monkeypatch):
    monkeypatch.setattr(M, "INVENTORY", tmp_path / "inv.yaml")
    monkeypatch.setattr(M, "load_inventory", lambda: None)
    srcs = {"codex": ["gpt-6.1-sol"], "opencode": [], "zcode": []}
    monkeypatch.setattr(M, "scan_codex", lambda: srcs["codex"])
    monkeypatch.setattr(M, "scan_opencode", lambda: [])
    monkeypatch.setattr(M, "scan_zcode", lambda: [])
    monkeypatch.setattr(M, "scan_codex_efforts", lambda: {})
    assert M.scan()["unreviewed"] == []  # первый скан — точка отсчёта
    import yaml
    monkeypatch.setattr(M, "load_inventory", lambda: yaml.safe_load((tmp_path / "inv.yaml").read_text(encoding="utf-8")))
    srcs["codex"] = ["gpt-6.1-sol", "gpt-betelgeuse", "gpt-7.2-sol"]
    inv2 = M.scan()
    assert inv2["unreviewed"] == ["gpt-7.2-sol", "gpt-betelgeuse"]
    msgs = M.check(L.load_routing(), inv2, today=dt.date(2026, 11, 10))
    assert any("НОВАЯ МОДЕЛЬ: gpt-betelgeuse" in m and "OpenAI" in m for m in msgs)
    assert not any("НОВАЯ МОДЕЛЬ: gpt-7.2-sol" in m for m in msgs)  # легла в шаблон — классифицирована
    assert M.main(["ack", "gpt-betelgeuse"]) == 0
    assert yaml.safe_load((tmp_path / "inv.yaml").read_text(encoding="utf-8"))["unreviewed"] == ["gpt-7.2-sol"]


def test_map_efforts_to_model_levels():
    assert M.map_efforts(["medium", "high"], ["low", "medium", "high", "xhigh", "max"]) == ["medium", "high"]
    assert M.map_efforts(["medium", "high"], ["low", "max"]) == ["low"]  # GLM: max сам не подставляется
    assert M.map_efforts(["high", "xhigh"], ["low", "medium", "high", "max"]) == ["high"]
    assert M.map_efforts(["max"], ["low", "max"]) == ["max"]  # только если max указан явно
    assert M.map_efforts(["low"], ["none", "low", "high", "max"]) == ["low"]
    assert M.map_efforts(["medium"], []) == []


def test_escalation_effort_first_then_model(capsys):
    cfg = L.load_routing()
    assert L.resolve_step("alternatives", None, cfg, "claude")[:2] == ("T3", "high")
    assert L.resolve_step("alternatives", "T3", cfg, "claude")[:2] == ("T3", "xhigh")
    assert L.resolve_step("alternatives", "T3@xhigh", cfg, "claude")[:2] == ("T4", "high")
    assert L.resolve_step("alternatives", "T3@max", cfg, "claude")[:2] == ("T4", "high")  # вне лестницы → выше
    assert L.resolve_step("classify", "T1", cfg, "openai")[:2] == ("T2", "medium")  # у T1 одна ступень
    assert L.resolve_step("alternatives", None, cfg, "glm")[:2] == ("T3", "low")
    assert L.main(["route", "alternatives", "--env", "codex"]) == 0
    out = capsys.readouterr().out
    assert "Effort: high" in out and "согласия" in out


def test_codex_effort_from_inventory(monkeypatch):
    monkeypatch.setattr(M, "load_inventory", lambda: dict(inv("gpt-6.1-sol"), efforts={"gpt-6.1-sol": ["low", "medium", "high"]}))
    assert L.tier_efforts(L.load_routing(), "T3", "openai") == ["high"]  # не из гайда (там есть xhigh)


def test_delegate_passes_effort_to_codex(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(D.shutil, "which", lambda b: "/bin/" + b if b == "codex" else None)
    p = tmp_path / "p"
    L.main(["init", str(p), "--title", "Д"])
    q = tmp_path / "q.md"
    q.write_text("x", encoding="utf-8")
    assert D.main(["--project", str(p), "--prompt-file", str(q), "--task", "alternatives", "--tier", "T3",
                   "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "gpt-6.1-sol@high" in out and "model_reasoning_effort=high" in out
    assert D.main(["--project", str(p), "--prompt-file", str(q), "--task", "alternatives", "--tier", "T3",
                   "--effort", "max", "--dry-run"]) == 2  # max — только с согласия
    assert D.main(["--project", str(p), "--prompt-file", str(q), "--task", "alternatives", "--tier", "T3",
                   "--effort", "max", "--human-approved", "--dry-run"]) == 0
    assert D.main(["--project", str(p), "--prompt-file", str(q), "--task", "alternatives", "--tier", "T3",
                   "--effort", "ultra", "--human-approved", "--dry-run"]) == 2


def test_selfhosted_backend_roundtrip(tmp_path, monkeypatch):
    import json as J
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer
    got = {}

    class H(BaseHTTPRequestHandler):
        def do_POST(self):
            got["body"] = J.loads(self.rfile.read(int(self.headers["Content-Length"])))
            got["auth"] = self.headers.get("Authorization")
            data = J.dumps({"choices": [{"message": {"content": "классы: A"}}],
                            "usage": {"prompt_tokens": 11, "completion_tokens": 3}}).encode()
            self.send_response(200)
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *a):
            pass
    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    loc = tmp_path / "routing.local.yaml"
    loc.write_text(f"selfhosted: {{base_url: 'http://127.0.0.1:{srv.server_port}/v1', api_key_env: SH_TEST_KEY}}\n"
                   "families:\n  selfhosted: {T1: my-classifier, T2: null, T3: null, T4: null}\n"
                   "delegate_backends: [selfhosted, codex]\n", encoding="utf-8")
    monkeypatch.setattr(L, "ROUTING_LOCAL_PATH", loc)
    monkeypatch.setenv("SH_TEST_KEY", "k-test")
    monkeypatch.setattr(D.shutil, "which", lambda b: None)
    p = tmp_path / "p"
    L.main(["init", str(p), "--title", "Д"])
    q = tmp_path / "q.md"
    q.write_text("Классифицируй.", encoding="utf-8")
    assert D.main(["--project", str(p), "--prompt-file", str(q), "--task", "classify", "--step", "3"]) == 0
    srv.shutdown()
    assert got["body"]["model"] == "my-classifier" and got["auth"] == "Bearer k-test"
    assert "Классифицируй." in got["body"]["messages"][0]["content"]
    log = (p / "state/run_log.md").read_text(encoding="utf-8")
    assert "selfhosted:my-classifier" in log and "k-test" not in log
    assert list((p / "state/delegated").glob("*_classify.md"))[0].read_text(encoding="utf-8") == "классы: A"


def test_stronger_new_model_is_a_step_above_not_a_replacement(monkeypatch):
    cat = [{"id": "gpt-betelgeuse", "family": "openai", "tier": "T4", "kind": "general", "above": "gpt-6-astra"},
           {"id": "gpt-pebble", "family": "openai", "tier": "T3", "kind": "general", "below": "gpt-6.1-sol"}]
    monkeypatch.setattr(M, "load_catalog", lambda path=None: cat)
    monkeypatch.setattr(M, "load_inventory", lambda: inv("gpt-6-astra", "gpt-betelgeuse", "gpt-6.1-sol", "gpt-pebble"))
    cfg = L.load_routing()
    assert M.tier_models(cfg, "openai", "T4") == ["gpt-6-astra", "gpt-betelgeuse"]  # Astra остаётся стартовой
    assert M.tier_models(cfg, "openai", "T3") == ["gpt-pebble", "gpt-6.1-sol"]  # дешевле — раньше
    assert L.model_for(cfg, "openai", "T4") == "gpt-6-astra"
    assert L.resolve_step("alternatives", "T4@xhigh", cfg, "openai")[3] == "gpt-betelgeuse"
    with pytest.raises(ValueError):  # выше самой сильной модели T4 — только пробел и вопрос человеку
        L.resolve_step("alternatives", "T4:gpt-betelgeuse@xhigh", cfg, "openai")

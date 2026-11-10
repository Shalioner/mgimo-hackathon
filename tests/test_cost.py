"""Стоимость прогона: журналы Codex/ZCode → токены → деньги по prices.yaml."""
import datetime as dt
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
import cost as C  # noqa: E402


def test_price_lookup_and_math():
    prices = C.load_prices()
    p = C.price_for("opencode/gpt-6.1-sol", prices)
    assert p["input"] == 2.0 and p["cached"] == 0.10 and p["output"] == 10.0
    assert C.price_for("glm-5.3", prices)["input"] == 1.40 and C.price_for("glm-5.3-flash", prices)["input"] == 0.15
    assert C.price_for("unknown-model", prices) is None
    assert C.usd({"input": 1_000_000, "cached": 1_000_000, "output": 100_000}, p) == 2.0 + 0.10 + 1.0


def test_codex_threads_by_cwd_and_window(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    roll = tmp_path / "r.jsonl"
    ev = {"type": "event_msg", "payload": {"type": "token_count", "info": {"total_token_usage": {
        "input_tokens": 1_000_000, "cached_input_tokens": 900_000, "output_tokens": 10_000}}}}
    roll.write_text(json.dumps({"type": "x"}) + "\n" + json.dumps(ev) + "\n", encoding="utf-8")
    con = sqlite3.connect(home / "state_5.sqlite")
    con.execute("create table threads (id, rollout_path, model, created_at, updated_at, cwd)")
    t = int(dt.datetime(2026, 11, 10, 15, 0).timestamp())
    con.execute("insert into threads values ('a', ?, 'gpt-6.1-sol', ?, ?, '/x/e2e-ryadom')", (str(roll), t, t))
    con.execute("insert into threads values ('b', ?, 'gpt-6.1-sol', ?, ?, '/x/other')", (str(roll), t, t))
    con.commit()
    con.close()
    monkeypatch.setattr(C, "codex_homes", lambda: [home])
    recs = list(C.codex_usage("e2e-ryadom", C.parse_time("2026-11-10T14:00"), C.parse_time("2026-11-10T18:00")))
    assert len(recs) == 1 and recs[0][2] == {"input": 100_000, "cached": 900_000, "output": 10_000, "calls": 1}
    assert not list(C.codex_usage("e2e-ryadom", C.parse_time("2026-11-10T16:00"), None))
    s = C.summarize(recs, C.load_prices())
    assert s["total_usd"] == round((100_000 * 2 + 900_000 * 0.10 + 10_000 * 10) / 1e6, 4)


def test_zcode_and_unpriced(tmp_path, monkeypatch):
    d = tmp_path / ".zcode/cli/rollout"
    d.mkdir(parents=True)
    rec = {"completedAt": int(dt.datetime(2026, 11, 10, 18, 0).timestamp() * 1000), "request": "esim",
           "response": {"modelId": "glm-5.3-flash", "providerMetadata": {"anthropic": {"usage": {
               "input_tokens": 1000, "cache_read_input_tokens": 9000, "output_tokens": 100}}}}}
    odd = dict(rec, response={"modelId": "mystery-1", "usage": {"input_tokens": 5, "output_tokens": 1}})
    (d / "model-io-sess_x.jsonl").write_text(json.dumps(rec) + "\n" + json.dumps(odd) + "\n", encoding="utf-8")
    monkeypatch.setattr(C, "HOME", tmp_path)
    s = C.summarize(C.zcode_usage("esim", None, None), C.load_prices())
    assert s["unpriced"] == ["mystery-1"]
    text = C.render(s, "t", 90.0)
    assert "нет цены" in text and "₽" in text and "glm-5.3-flash" in text

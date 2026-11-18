"""Стоимость прогона: журналы Codex/ZCode → токены → деньги по prices.yaml."""
import datetime as dt
import json
import sqlite3
import sys
from pathlib import Path

import pytest

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


def test_json_cost_without_usage_has_no_measured_total(tmp_path, capsys):
    assert C.main(["--project", str(tmp_path), "--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["rows"] == []
    assert result["total_usd"] is None


def test_codex_threads_by_cwd_and_window(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    roll = tmp_path / "r.jsonl"
    ev = {"timestamp": "2026-11-10T15:00:00+03:00", "type": "event_msg", "payload": {"type": "token_count", "info": {"total_token_usage": {
        "input_tokens": 1_000_000, "cached_input_tokens": 900_000, "output_tokens": 10_000}}}}
    roll.write_text(json.dumps({"type": "x"}) + "\n" + json.dumps(ev) + "\n", encoding="utf-8")
    con = sqlite3.connect(home / "state_5.sqlite")
    con.execute("create table threads (id, rollout_path, model, created_at, updated_at, cwd)")
    t = int(dt.datetime.fromisoformat("2026-11-10T15:00:00+03:00").timestamp())
    con.execute("insert into threads values ('a', ?, 'gpt-6.1-sol', ?, ?, '/x/e2e-ryadom')", (str(roll), t, t))
    con.execute("insert into threads values ('b', ?, 'gpt-6.1-sol', ?, ?, '/x/other')", (str(roll), t, t))
    con.commit()
    con.close()
    monkeypatch.setattr(C, "codex_homes", lambda: [home])
    recs = list(C.codex_usage("e2e-ryadom", C.parse_time("2026-11-10T14:00+03:00"), C.parse_time("2026-11-10T18:00+03:00")))
    assert len(recs) == 1 and recs[0][2] == {"input": 100_000, "cached": 900_000, "output": 10_000, "calls": 1}
    assert not list(C.codex_usage("e2e-ryadom", C.parse_time("2026-11-10T16:00+03:00"), None))
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
    assert s["total_usd"] is None and s["incomplete"]
    assert "Полный итог неизвестен" in C.render(s, "test", None)
    text = C.render(s, "t", 90.0)
    assert "нет цены" in text and "₽" in text and "glm-5.3-flash" in text


def test_delegated_calls_from_run_log(tmp_path):
    p = tmp_path / "p"
    (p / "state").mkdir(parents=True)
    (p / "state/run_log.md").write_text(
        "| время | шаг | действие | уровень | модель | результат | расход |\n|---|---|---|---|---|---|---|\n"
        "| 2026-11-10T21:44:33+03:00 | 8 | делегирование revise-lines | T2 | codex:gpt-6.1-terra@medium | ok | 26 с; вход 75998 (кэш 34304), выход 555 ток. |\n"
        "| 2026-11-10T21:45:00+03:00 | 8 | правка строк | T2 | revise_lines.py | ok | см. выше |\n", encoding="utf-8")
    recs = list(C.delegated_usage(p, None, None))
    assert recs == [("delegate", "gpt-6.1-terra", {"input": 41694, "cached": 34304, "output": 555, "calls": 1},
                     "2026-11-10T21:44:33+03:00")]
    assert C.summarize(recs, C.load_prices())["total_usd"] == round((41694 * 1 + 34304 * 0.05 + 555 * 5) / 1e6, 4)


# Synthetic event_msg/token_count schema used by Codex rollouts. No user logs.
def codex_event(hour, input_tokens, cached=0, output=0):
    return {"timestamp": f"2026-11-10T{hour}:00:00Z", "type": "event_msg",
            "payload": {"type": "token_count", "info": {
                "total_token_usage": {"input_tokens": input_tokens,
                                      "cached_input_tokens": cached, "output_tokens": output},
                "last_token_usage": {"input_tokens": 99999, "output_tokens": 99999}}}}


@pytest.fixture
def codex_store(tmp_path, monkeypatch):
    home = tmp_path / "codex"
    home.mkdir()
    db = home / "state_5.sqlite"
    with sqlite3.connect(db) as con:
        con.execute("create table threads (id, rollout_path, model, created_at, updated_at, cwd)")
    monkeypatch.setattr(C, "codex_homes", lambda: [home])
    monkeypatch.setattr(C, "HOME", tmp_path)  # CLI cannot read real Claude/ZCode logs either.

    def add(tid, created, events, cwd="/synthetic/project"):
        path = home / f"{tid}.jsonl"
        path.write_text("\n".join(e if isinstance(e, str) else json.dumps(e) for e in events) + "\n",
                        encoding="utf-8")
        ts = C.parse_time(f"2026-11-10T{created}:00:00Z").timestamp()
        with sqlite3.connect(db) as con:
            con.execute("insert into threads values (?, ?, ?, ?, ?, ?)",
                        (tid, str(path), "gpt-6.1-sol", ts, ts, cwd))
        return path
    return add


def window_records():
    return list(C.codex_usage("/synthetic/project", C.parse_time("2026-11-10T10:00Z"),
                             C.parse_time("2026-11-10T11:00Z")))


def test_exact_cwd_excludes_adjacent_and_nested_streams(codex_store, tmp_path, capsys):
    cwd = tmp_path / "demo"
    claude_dir = tmp_path / ".claude" / "projects" / "demo"
    claude_dir.mkdir(parents=True)
    claude_rows = []
    for name, folder, tokens in (
        ("demo", cwd, 100), ("neighbor", tmp_path / "demo-review", 200),
        ("nested", cwd / "other", 300),
    ):
        codex_store(name, "10", [codex_event("10", tokens)], str(folder))
        claude_rows.append({
            "type": "assistant", "cwd": str(folder), "timestamp": "2026-11-10T10:00:00Z",
            "message": {"model": "sonnet", "usage": {"input_tokens": tokens}},
        })
    (claude_dir / "session.jsonl").write_text(
        "\n".join(json.dumps(row) for row in claude_rows), encoding="utf-8",
    )

    args = ["--cwd", str(cwd), "--since", "2026-11-10T10:00Z", "--json"]
    assert C.main(args) == 0
    broad = json.loads(capsys.readouterr().out)
    assert {row["env"]: row["input"] for row in broad["rows"]} == {"codex": 600, "claude-code": 600}

    assert C.main([*args, "--cwd-exact"]) == 0
    exact = json.loads(capsys.readouterr().out)
    assert {row["env"]: row["input"] for row in exact["rows"]} == {"codex": 100, "claude-code": 100}


def test_codex_old_and_new_threads_use_event_deltas(codex_store):
    events = [codex_event("09", 100), codex_event("10", 130), codex_event("12", 200)]
    codex_store("old", "09", events)
    codex_store("new", "10", events)  # representative pre-window baseline is allowed
    records = window_records()
    assert {r[3]: r[2] for r in records} == {
        tid: {"input": 30, "cached": 0, "output": 0, "calls": 1} for tid in ("old", "new")}


def test_codex_cache_repeats_bounds_and_non_token_events(codex_store):
    first = codex_event("10", 130, 70, 20)
    codex_store("a", "09", [codex_event("09", 100, 50, 10), first, first,
                             {"type": "event_msg", "payload": {"type": "token_count", "info": None}},
                             {"type": "response_item", "payload": first["payload"]},
                             codex_event("11", 170, 90, 30), codex_event("12", 900, 300, 100)])
    assert window_records()[0][2] == {"input": 30, "cached": 40, "output": 20, "calls": 1}


def test_codex_first_snapshot_new_thread_can_start_from_zero(codex_store):
    codex_store("new", "10", [codex_event("10", 30, 20, 5), codex_event("12", 100)])
    assert window_records()[0][2] == {"input": 10, "cached": 20, "output": 5, "calls": 1}


def test_codex_missing_baseline_is_partial_not_lifetime_total(codex_store):
    codex_store("old", "09", [codex_event("10", 130), codex_event("11", 150)])
    records = window_records()
    assert records[0][2]["input"] == 20
    summary = C.summarize(records, C.load_prices())
    assert summary["total_usd"] is None and summary["known_usd"] == 0.0
    text = C.render(summary, "test", 90)
    assert "Полный итог неизвестен" in text and "нет начальной точки" in text
    assert "**Итого" not in text


@pytest.mark.parametrize("broken", [
    '{"type":',
    {**codex_event("10", 130), "timestamp": "bad-time"},
    {k: v for k, v in codex_event("10", 130).items() if k != "timestamp"},
    codex_event("10", 130, cached=200),
    codex_event("10", "130"),
    {"timestamp": "2026-11-10T10:00:00Z", "type": "event_msg", "payload": {
        "type": "token_count", "info": {"total_token_usage": {"input_tokens": 130}}}},
])
def test_codex_bad_data_never_turns_into_measured_zero(codex_store, broken, capsys):
    codex_store("old", "09", [broken])
    assert C.main(["--cwd", "/synthetic/project", "--since", "2026-11-10T10:00Z",
                   "--until", "2026-11-10T11:00Z", "--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["total_usd"] is None and result["incomplete"]
    assert result["rows"][0]["input"] == 0


def test_codex_reset_rebaselines_without_negative_cost(codex_store):
    codex_store("a", "09", [codex_event("09", 100, 50, 10), codex_event("10", 20, 5, 2),
                             codex_event("11", 40, 15, 4)])
    usage = window_records()[0][2]
    assert (usage["input"], usage["cached"], usage["output"]) == (10, 10, 2)
    assert usage["incomplete"]


def test_codex_outside_window_and_other_cwd_are_excluded(codex_store):
    codex_store("before", "09", [codex_event("09", 100)])
    codex_store("after", "12", [codex_event("12", 100)]).unlink()
    codex_store("other", "10", [codex_event("10", 100)], cwd="/elsewhere")
    assert window_records() == []


def test_codex_unbounded_legacy_and_repeated_snapshots(codex_store):
    ev = codex_event("09", 100, 50, 10)
    ev.pop("timestamp")
    codex_store("legacy", "09", [ev, ev])
    assert list(C.codex_usage("/synthetic/project", None, None))[0][2] == {
        "input": 50, "cached": 50, "output": 10, "calls": 1}


def test_codex_missing_rollout_is_unknown(codex_store):
    codex_store("gone", "09", []).unlink()
    summary = C.summarize(window_records(), C.load_prices())
    assert summary["total_usd"] is None
    assert "журнал недоступен" in summary["notes"][0]


def test_codex_duplicate_homes_do_not_double_charge(codex_store, monkeypatch):
    path = codex_store("a", "09", [codex_event("09", 100), codex_event("10", 130)])
    monkeypatch.setattr(C, "codex_homes", lambda: [path.parent, path.parent])
    assert len(window_records()) == 1


def test_codex_unsorted_events_are_partial(codex_store):
    codex_store("a", "09", [codex_event("09", 100), codex_event("11", 150), codex_event("10", 130)])
    usage = window_records()[0][2]
    assert usage["input"] == 50 and usage["incomplete"]


def test_claude_cli_usage_preserved(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(C, "codex_homes", lambda: [])
    monkeypatch.setattr(C, "HOME", tmp_path)
    logs = tmp_path / ".claude/projects/test"
    logs.mkdir(parents=True)
    event = {"type": "assistant", "cwd": "/synthetic/project", "timestamp": "2026-11-10T10:00:00Z",
             "message": {"model": "gpt-6.1-sol", "usage": {
                 "input_tokens": 10, "cache_creation_input_tokens": 5,
                 "cache_read_input_tokens": 20, "output_tokens": 3}}}
    (logs / "chat.jsonl").write_text(json.dumps(event) + "\n", encoding="utf-8")
    assert C.main(["--cwd", "/synthetic/project", "--since", "2026-11-10T10:00Z",
                   "--until", "2026-11-10T11:00Z", "--json"]) == 0
    row = json.loads(capsys.readouterr().out)["rows"][0]
    assert (row["env"], row["input"], row["cached"], row["output"]) == ("claude-code", 15, 20, 3)


def test_codex_unavailable_database_does_not_hide_partial_total(codex_store, monkeypatch, tmp_path):
    path = codex_store("a", "10", [codex_event("10", 1000)])
    bad_home = tmp_path / "bad"
    bad_home.mkdir()
    (bad_home / "state_5.sqlite").write_bytes(b"not sqlite")
    monkeypatch.setattr(C, "codex_homes", lambda: [path.parent, bad_home])
    summary = C.summarize(window_records(), C.load_prices())
    assert summary["total_usd"] is None and summary["known_usd"] == 0.002


def test_codex_until_without_since_uses_lifetime_only_up_to_bound(codex_store):
    codex_store("a", "09", [codex_event("09", 100, 50, 10), codex_event("10", 130, 70, 20),
                             codex_event("12", 200, 80, 30)])
    records = list(C.codex_usage("/synthetic/project", None, C.parse_time("2026-11-10T11:00Z")))
    assert records[0][2] == {"input": 60, "cached": 70, "output": 20, "calls": 1}


def test_codex_zero_baseline_and_write_cli(codex_store, tmp_path, capsys):
    codex_store("a", "09", [codex_event("09", 0), codex_event("10", 1000, 400, 100)])
    (tmp_path / "state").mkdir()
    assert C.main(["--cwd", "/synthetic/project", "--since", "2026-11-10T10:00Z",
                   "--until", "2026-11-10T11:00Z", "--write", str(tmp_path)]) == 0
    assert "**Итого" in capsys.readouterr().out
    assert "**Итого" in (tmp_path / "state/cost_report.md").read_text(encoding="utf-8")

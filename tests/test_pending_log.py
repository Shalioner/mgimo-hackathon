"""Recover audit rows after real case-lock contention without rerunning actions."""
import datetime as dt
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import cost as C
import decide as DC
import delegate as D
import ledger as L
import pytest
import revise_lines as R
import routing_policy as P
from test_revise_lines import setup as revise_case
from test_routing_policy import api_policy


@pytest.fixture
def case(tmp_path, monkeypatch):
    project = tmp_path / "case with spaces"
    assert L.cmd_init(project, "Fixture") == 0
    monkeypatch.setattr(L, "_WRITE_LOCK_TIMEOUT", 0.02)
    return project


def fake_backend(monkeypatch, cfg, payload, *, key="F1_TEST_KEY"):
    calls = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def __init__(self, data):
            self.data = data

        def read(self):
            return json.dumps(self.data).encode()

    def send(request, _timeout):
        calls.append(1)
        return Response(payload(json.loads(request.data)) if callable(payload) else payload)

    monkeypatch.setattr(L, "load_routing", lambda: cfg)
    monkeypatch.setenv(key, "synthetic")
    monkeypatch.setattr(P, "http_open", send)
    return calls


def delegate_cfg(task="classify", model="gpt-6.1-terra"):
    return {
        "tasks": {task: {"tier": "T1"}}, "families": {"selfhosted": {"T1": model}},
        "delegate_backends": ["selfhosted"],
        "execution_policy": api_policy("selfhosted", "example", "https://fixture.test/v1/chat/completions", model),
        "selfhosted": {"provider": "example", "base_url": "https://fixture.test/v1", "api_key_env": "F1_TEST_KEY"},
    }


def test_delegate_timeout_recovers_exact_usage_in_original_window(case, tmp_path, monkeypatch, capsys):
    calls = fake_backend(monkeypatch, delegate_cfg(), {
        "choices": [{"message": {"content": "fixture answer"}}],
        "usage": {"input_tokens": 1200, "cached_input_tokens": 200, "output_tokens": 300},
    })
    monkeypatch.setattr(C, "HOME", tmp_path / "empty-home")
    prompt = tmp_path / "prompt.md"
    prompt.write_text("fixture prompt", encoding="utf-8")
    log = case / "state/run_log.md"
    before = log.read_text(encoding="utf-8")
    start = dt.datetime.now().astimezone().replace(microsecond=0)
    with L._project_write_lock(case) as locked:
        assert locked
        rc = D.main(["--project", str(case), "--prompt-file", str(prompt), "--task", "classify"])
    assert rc == 0 and calls == [1]
    delegated = case / "state/delegated"
    assert next(delegated.glob("*.md")).read_text(encoding="utf-8") == "fixture answer"
    receipt = json.loads(next(delegated.glob("*.receipt.json")).read_text(encoding="utf-8"))
    assert receipt["result"] == "succeeded" and receipt["exit_code"] == 0
    assert log.read_text(encoding="utf-8") == before
    assert "истекло ожидание" in (err := capsys.readouterr().err)
    assert "log_status=pending" in err
    pending, = L.pending_log_files(case)
    original_time, row = L.read_pending_log(pending)
    assert "вход 1200 (кэш 200), выход 300" in row
    assert C.parse_time(original_time) >= start
    assert pending.stem in err
    argv = ["--cwd", str(case), "--project", str(case), "--json"]
    window = ["--since", original_time, "--until", original_time]
    assert C.main(argv + window) == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["rows"] == [] and summary["unpriced"] == []
    assert summary["incomplete"] is True and summary["total_usd"] is None
    assert "log_status=pending" in C.render(summary, "Fixture", None)
    later = (C.parse_time(original_time) + dt.timedelta(seconds=1)).isoformat()
    assert C.main(argv + ["--since", later]) == 0
    assert "incomplete" not in json.loads(capsys.readouterr().out)
    command = err.split("Восстановление: ", 1)[1].strip()
    assert subprocess.run(command, shell=True, capture_output=True).returncode == 0
    assert L.main(["log-replay", str(case), pending.stem]) == 0
    assert not pending.exists() and log.read_text(encoding="utf-8") == before + row
    capsys.readouterr()
    assert C.main(argv + window) == 0
    summary = json.loads(capsys.readouterr().out)
    record, = summary["rows"]
    assert (record["model"], record["calls"], record["input"], record["cached"], record["output"]) == (
        "gpt-6.1-terra", 1, 1000, 200, 300)
    assert "incomplete" not in summary and calls == [1]


def test_replay_lock_and_retry_after_cleanup_failure(case, monkeypatch, capsys):
    class FrozenTime(dt.datetime):
        @classmethod
        def now(cls):
            return cls(2026, 11, 18, tzinfo=dt.timezone.utc)

    monkeypatch.setattr(L, "datetime", SimpleNamespace(datetime=FrozenTime))
    log = case / "state/run_log.md"
    before = log.read_text(encoding="utf-8")
    with L._project_write_lock(case) as locked:
        assert locked
        for _ in range(2):
            L.log_or_defer(case, "3", "одинаковое действие", "T1", "-", "-", "-")
        first, second = L.pending_log_files(case)
        assert L.cmd_log_replay(case, first.stem) == 1
    assert log.read_text(encoding="utf-8") == before

    original_unlink = Path.unlink

    def fail_cleanup(path, *args, **kwargs):
        if path == first:
            raise OSError("fixture cleanup failure")
        return original_unlink(path, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "unlink", fail_cleanup)
        assert L.cmd_log_replay(case, first.stem) == 1
    assert "fixture cleanup failure" in capsys.readouterr().err
    assert first.exists()
    _, first_row = L.read_pending_log(first)
    _, second_row = L.read_pending_log(second)
    assert first_row.replace(first.stem, second.stem) == second_row
    assert log.read_text(encoding="utf-8") == before + first_row
    assert L.cmd_log_replay(case, second.stem) == 0
    assert L.cmd_log_replay(case, first.stem) == 0
    assert log.read_text(encoding="utf-8") == before + first_row + second_row
    assert list(L.pending_log_files(case)) == []


def test_replay_is_scoped_to_pending_of_selected_case(case, tmp_path, capsys):
    other = tmp_path / "other"
    assert L.cmd_init(other, "Other fixture") == 0
    with L._project_write_lock(other) as locked:
        assert locked
        L.log_or_defer(other, "3", "другой кейс", "T1", "-", "-", "-")
    outside, = L.pending_log_files(other)
    assert L.cmd_log_replay(case, str(outside)) == 2
    assert L.cmd_log_replay(case, outside.stem) == 0
    assert outside.is_file()
    directory = case / "state" / outside.parent.name
    directory.mkdir()
    (directory / outside.name).write_text("corrupt fixture", encoding="utf-8")
    assert L.cmd_log_replay(case, outside.stem) == 1
    assert "Повреждённая строка" in capsys.readouterr().err
    assert "другой кейс" not in (case / "state/run_log.md").read_text(encoding="utf-8")


def test_failed_writes_keep_diagnostics_separate(case, monkeypatch, capsys):
    write = L.atomic_write_text
    attempted = []

    def fail_write(path, row):
        attempted.append(path)
        raise PermissionError("fixture denied: " + path.name)

    monkeypatch.setattr(L, "atomic_write_text", fail_write)
    L.log_or_defer(case, "3", "действие", "T1", "-", "-", "-")
    err = capsys.readouterr().err
    log, pending = attempted
    assert log.name == "run_log.md" and pending.suffix == ".md"
    assert "fixture denied: run_log.md" in err
    assert "log_status=failed" in err and pending.name in err and "log_status=pending" not in err
    assert not pending.exists()

    def fail_journal_only(path, row):
        if path == log:
            fail_write(path, row)
        write(path, row)

    monkeypatch.setattr(L, "atomic_write_text", fail_journal_only)
    L.log_or_defer(case, "3", "действие", "T1", "-", "-", "-")
    assert "log_status=pending" in capsys.readouterr().err
    pending, = L.pending_log_files(case)
    monkeypatch.setattr(L, "atomic_write_text", write)
    assert L.cmd_log_replay(case, pending.stem) == 0
    assert log.read_text(encoding="utf-8").count("действие | T1") == 1


@pytest.mark.parametrize("scenario,expected", [
    ("failure", 1), ("success", 0), ("01_brief.md", 4), ("04_options.md", 4), ("07_validation_plan.md", 4),
])
def test_decide_keeps_receipt_and_exit_code(case, monkeypatch, capsys, scenario, expected):
    model = "decision-fixture"
    provider = {"name": "test", "model": model, "base_url": "https://fixture.test", "api_key_env": "F1_TEST_KEY"}
    cfg = {
        "execution_policy": api_policy("decision", "test", "https://fixture.test/systemone", model),
        "decision": {"providers": [provider]},
    }

    def reply(body):
        if scenario == "failure":
            return {"answers": {"q": {"type": "noul", "noul": "bad"}}}
        return {"answers": {key: {"type": "noul", "noul": 0.99} for key in body["questions"]}}

    calls = fake_backend(monkeypatch, cfg, reply)
    argv = ["--project", str(case), "--provider", "test"]
    if scenario.endswith(".md"):
        artifact = case / "outputs" / scenario
        artifact.write_text("# Incomplete fixture\n", encoding="utf-8")
        argv += ["--gate", str(artifact)]
    else:
        argv += ["--ask", "Ready?", "--state", "fixture"]
    with L._project_write_lock(case) as locked:
        assert locked
        assert DC.main(argv) == expected
    out, err = capsys.readouterr()
    assert "log_status=pending" in err
    assert ("ПРОВЕРКА: НЕ ГОТОВО" in out) == (expected == 4)
    delegated = case / "state/delegated"
    receipt = json.loads(next(delegated.glob("*_decide.receipt.json")).read_text(encoding="utf-8"))
    assert receipt["exit_code"] == (1 if expected == 1 else 0)
    assert bool(list(delegated.glob("*_decide_*.json"))) == (expected != 1)
    pending, = L.pending_log_files(case)
    _, row = L.read_pending_log(pending)
    assert ("ошибка (код 1)" if expected == 1 else "ok →") in row
    assert L.cmd_log_replay(case, pending.stem) == 0
    assert (case / "state/run_log.md").read_text(encoding="utf-8").count(row) == 1
    assert calls == [1]


def test_revise_replays_both_rows_without_reapplying(tmp_path, monkeypatch, capsys):
    case, doc = revise_case(tmp_path)
    doc.write_text("Строка [HYP-001]\n", encoding="utf-8")
    monkeypatch.setattr(L, "_WRITE_LOCK_TIMEOUT", 0.02)
    answer = json.dumps([{"key": "03:1", "action": "rewrite", "text": "Актуально [HYP-001]."}])
    calls = fake_backend(monkeypatch, delegate_cfg("revise-lines"), {"choices": [{"message": {"content": answer}}]})
    with L._project_write_lock(case) as locked:
        assert locked
        assert R.main([str(case), "--change", "CHG-001", "--step", "8"]) == 0
    assert capsys.readouterr().err.count("log_status=pending") == 2
    expected = "Актуально [HYP-001]. (пересмотрено по CHG-001)\n"
    assert doc.read_text(encoding="utf-8") == expected
    first, second = L.pending_log_files(case)
    for path in (first, second):
        assert L.main(["log-replay", str(case), path.stem]) == 0
    log = (case / "state/run_log.md").read_text(encoding="utf-8")
    assert log.count("правка строк по CHG-001") == log.count("делегирование revise-lines") == 1
    assert doc.read_text(encoding="utf-8") == expected and calls == [1]


def test_routing_refusal_keeps_exit_3_without_transport(case, tmp_path, monkeypatch, capsys):
    cfg = delegate_cfg()
    del cfg["execution_policy"]
    calls = fake_backend(monkeypatch, cfg, {})
    prompt = tmp_path / "prompt.md"
    prompt.write_text("fixture", encoding="utf-8")
    with L._project_write_lock(case) as locked:
        assert locked
        assert D.main(["--project", str(case), "--prompt-file", str(prompt), "--task", "classify"]) == 3
    assert "log_status=pending" in capsys.readouterr().err
    receipt = json.loads(next((case / "state/delegated").glob("*_refusal.receipt.json")).read_text(encoding="utf-8"))
    assert receipt["exit_code"] == 3 and receipt["started"] is False and receipt["inference_calls"] == 0
    pending, = L.pending_log_files(case)
    assert L.cmd_log_replay(case, pending.stem) == 0
    log = (case / "state/run_log.md").read_text(encoding="utf-8")
    assert log.count("routing refusal: delegate classify") == 1 and "0 inference calls" in log and calls == []

"""Scenario 5 (B5): table versions and unreadable attachments (offline controls + regressions)."""
import json
import subprocess
import sys
from pathlib import Path

import pytest

import datacheck as DC
import delegate as D
import document_flow as F
from test_codex_subscription import subscription

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def table_flow(subscription, tmp_path, monkeypatch):
    cfg, _, _, _, _ = subscription
    cfg["families"]["openai"].update(T2="terra", T3="sol")
    cfg["tiers"].update(T2={"effort": ["medium", "high"]}, T3={"effort": ["high", "xhigh"]})
    cfg["execution_policy"]["codex_subscription"]["models"] += ["terra", "sol"]
    cfg["execution_policy"]["codex_subscription"]["efforts"] += ["medium", "high", "xhigh"]
    v1_dir = tmp_path / "v1"
    v2_dir = tmp_path / "v2"
    v1_dir.mkdir()
    v2_dir.mkdir()
    p1 = v1_dir / "metrics.csv"
    p2 = v2_dir / "metrics.csv"
    p1.write_bytes(b"period,segment,rate\r\n2026-10,win,10%\r\nRAW_V1_ONLY\r\n")
    p2.write_bytes(b"period,segment,rate\r\n2026-10,win,14%\r\nRAW_V2_ONLY\r\n")
    calls, syntheses = [], []

    def call(cfg, model, prompt, effort, timeout, receipt, human_approved=False):
        calls.append({"model": model, "prompt": prompt, "effort": effort})
        receipt.update(started=True, wrapper_invocations=1, process_exit_code=0,
                       transmitted={"model": model, "effort": effort})
        if model == "m":
            payload = json.loads(prompt.rsplit("\n", 1)[-1])
            quote = payload["numbered_document"].splitlines()[1].split(": ", 1)[1]
            card = {
                "topic": f"Metrics {payload['source']['id']}",
                "findings": [{"kind": "fact", "statement": quote, "quote": quote, "start_line": 2, "end_line": 2}],
                "limitations": ["Single segment"],
                "contradictions": [],
            }
            return 0, json.dumps({"type": "turn.completed"}), "", json.dumps(card)
        return 0, json.dumps({"type": "turn.completed"}), "", "Compared v1 and v2."

    def synthesize(request, context, hosts, **kw):
        syntheses.append({"request": request, "context": context, "hosts": hosts, **kw})
        prompt = D.GUARD + request + context
        route = F.R.route_plan(kw["load_config"](), hosts[0], "T2", prompt)
        receipt, answer, _ = D.execute_route(cfg, hosts[0], route["requested"]["model"], prompt,
                                             route["requested"]["effort"], kw["timeout"], route)
        return {"status": receipt["result"], "result": {"status": receipt["result"], "text": answer}}

    monkeypatch.setattr(D, "call_codex", call)
    monkeypatch.setattr(F.R, "run_request", synthesize)
    return cfg, [p1, p2], calls, syntheses


# --- Positive controls ---


def test_positive_control_two_table_versions_same_basename_distinct_hashes(table_flow):
    cfg, paths, calls, syntheses = table_flow
    result = F.run_documents("Compare metrics v1 vs v2", paths, ["codex"], execute=True, load_config=lambda: cfg)
    assert result["status"] == "succeeded"
    assert [s["id"] for s in result["sources"]] == ["DOC-001", "DOC-002"]
    assert result["sources"][0]["sha256"] != result["sources"][1]["sha256"]
    assert result["sources"][0]["path"].endswith("v1/metrics.csv")
    assert result["sources"][1]["path"].endswith("v2/metrics.csv")
    assert [c["model"] for c in calls] == ["m", "m", "terra"]
    senior_prompt = calls[-1]["prompt"]
    assert "RAW_V1_ONLY" not in senior_prompt and "RAW_V2_ONLY" not in senior_prompt
    assert "2026-10,win,10%" in senior_prompt and "2026-10,win,14%" in senior_prompt
    assert len(syntheses) == 1


def test_positive_control_datacheck_reads_cp1251_and_ledger_rejects_overwrite(tmp_path):
    cp1251 = ROOT / "tests" / "fixtures" / "datacheck" / "cp1251.csv"
    assert DC.main([str(cp1251), "--json"]) in (0, 1)

    proj = tmp_path / "case"
    v1 = tmp_path / "trial_v1.csv"
    v2 = tmp_path / "trial_v2.csv"
    v1.write_text("period,rate\n2026-10,10%\n", encoding="utf-8")
    v2.write_text("period,rate\n2026-10,14%\n", encoding="utf-8")

    init = subprocess.run([sys.executable, str(ROOT / "tools" / "ledger.py"), "init", str(proj), "--title", "B5"],
                          capture_output=True, text=True)
    assert init.returncode == 0, init.stderr
    r1 = subprocess.run([sys.executable, str(ROOT / "tools" / "ledger.py"), "add-source", str(proj), str(v1),
                         "--title", "Table v1", "--kind", "document", "--access", "own"],
                        capture_output=True, text=True)
    assert r1.returncode == 0, r1.stderr
    dup = subprocess.run([sys.executable, str(ROOT / "tools" / "ledger.py"), "add-source", str(proj), str(v1),
                          "--title", "Table v1 copy", "--kind", "document", "--access", "own"],
                         capture_output=True, text=True)
    assert dup.returncode == 3
    r2 = subprocess.run([sys.executable, str(ROOT / "tools" / "ledger.py"), "add-source", str(proj), str(v2),
                         "--title", "Table v2", "--kind", "document", "--access", "own"],
                        capture_output=True, text=True)
    assert r2.returncode == 0, r2.stderr


# --- Negative controls ---


def test_negative_control_null_byte_binary_and_empty_rejected_before_model(table_flow, tmp_path):
    cfg, paths, calls, _ = table_flow
    nul_file = tmp_path / "nul.csv"
    nul_file.write_bytes(b"col1,col2\x00\n1,2\n")
    empty_file = tmp_path / "empty.csv"
    empty_file.write_bytes(b"")
    for bad in (nul_file, empty_file):
        with pytest.raises(ValueError):
            F.run_documents("Compare", [paths[0], bad], ["codex"], execute=True, load_config=lambda: cfg)
    assert not calls


def test_negative_control_corrupted_table_hash_detected_mid_run(table_flow):
    cfg, paths, calls, syntheses = table_flow
    original_call = D.call_codex

    def mutating_call(*args, **kwargs):
        res = original_call(*args, **kwargs)
        paths[1].write_text("period,segment,rate\n2026-10,win,99%\n", encoding="utf-8")
        return res

    D.call_codex = mutating_call
    result = F.run_documents("Compare", paths, ["codex"], execute=True, load_config=lambda: cfg)
    assert result["status"] == "stale_input"
    assert len(calls) == 1 and not syntheses and not result["cards"]


# --- Regressions for Scenario 5 defects ---


def test_unreadable_non_utf8_attachment_names_file_and_limitation_before_model_call(table_flow, tmp_path, capsys):
    cfg, paths, calls, _ = table_flow
    cp1251 = tmp_path / "report_cp1251.csv"
    cp1251.write_bytes("Дата,Доля\n2026-10,12%\n".encode("cp1251"))

    with pytest.raises(ValueError) as exc_info:
        F.run_documents("Compare", [paths[0], cp1251], ["codex"], execute=True, load_config=lambda: cfg)
    assert type(exc_info.value) is ValueError
    msg = str(exc_info.value)
    assert "report_cp1251.csv" in msg
    assert "UTF-8" in msg and "datacheck.py" in msg
    assert not calls

    prompt_file = tmp_path / "req.txt"
    prompt_file.write_text("Compare", encoding="utf-8")
    with pytest.raises(SystemExit) as cli_exc:
        F.main(["--prompt-file", str(prompt_file), "--source-file", str(paths[0]),
                "--source-file", str(cp1251), "--host", "codex", "--dry-run"])
    assert cli_exc.value.code == 2
    assert "report_cp1251.csv" in capsys.readouterr().err


@pytest.mark.parametrize("filename,payload", [
    ("scan.pdf", b"%PDF-1.4\n1 0 obj\n<< /Type /Catalog >>\nendobj\n"),
    ("export.xlsx", b"PK\x03\x04minimal_zip_header_without_nulls"),
    ("screen.png", b"PNG_ASCII_HEADER_ONLY"),
    ("control.txt", b"header\x07\x1b[31msecret\n"),
])
def test_binary_container_and_control_bytes_rejected_before_model_call(table_flow, tmp_path, filename, payload):
    cfg, paths, calls, _ = table_flow
    bad = tmp_path / filename
    bad.write_bytes(payload)
    with pytest.raises(ValueError) as exc_info:
        F.run_documents("Analyze attachment", [paths[0], bad], ["codex"], execute=True, load_config=lambda: cfg)
    assert filename in str(exc_info.value)
    assert not calls


def test_utf8_bom_csv_table_extracts_and_validates_line1_quote(tmp_path):
    bom_csv = tmp_path / "excel_export.csv"
    raw = "\ufeffperiod,rate\r\n2026-10,12%\r\n".encode("utf-8")
    bom_csv.write_bytes(raw)
    source = F.snapshot_sources([bom_csv])[0]
    assert source["bytes"] == len(raw)
    assert source["sha256"] == F._hash(raw)
    assert source["lines"][0] == "period,rate"
    card = F.validate_card(json.dumps({
        "topic": "Excel export",
        "findings": [{"kind": "fact", "statement": "Header is period,rate",
                      "quote": "period,rate", "start_line": 1, "end_line": 1}],
        "limitations": [],
        "contradictions": [],
    }), source)
    assert card["findings"][0]["quote"] == "period,rate"


def test_directory_source_and_non_utf8_prompt_file_report_path(table_flow, tmp_path, capsys):
    cfg, _, calls, _ = table_flow
    folder = tmp_path / "attached_dir"
    folder.mkdir()
    with pytest.raises(ValueError) as dir_exc:
        F.snapshot_sources([folder])
    assert "attached_dir" in str(dir_exc.value)

    bad_prompt = tmp_path / "prompt_cp1251.txt"
    bad_prompt.write_bytes("Сравни таблицы".encode("cp1251"))
    ok_src = tmp_path / "ok.md"
    ok_src.write_text("Rate is 12%.\n", encoding="utf-8")
    with pytest.raises(SystemExit) as cli_exc:
        F.main(["--prompt-file", str(bad_prompt), "--source-file", str(ok_src),
                "--host", "codex", "--dry-run"])
    assert cli_exc.value.code == 2
    assert "prompt_cp1251.txt" in capsys.readouterr().err
    assert not calls

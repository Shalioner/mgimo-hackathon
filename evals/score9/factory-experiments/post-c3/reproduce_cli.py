#!/usr/bin/env python3
"""Воспроизведение CLI-контролей эксперимента post-c3 на изолированных временных копиях.

Не меняет замороженный кандидат и не пишет в реальные проекты репозитория.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

EXPECTED_C3_SHA = "b1473fbfb235891c6e552b00df8dd244fab8a06e2ccb1c32b72523f97e853483"
HERE = Path(__file__).resolve().parent
DEFAULT_CANDIDATE = HERE.parents[3]


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run_cli(python_bin: Path, tools_dir: Path, script: str, *args: str) -> dict:
    cmd = [str(python_bin), str(tools_dir / script), *args]
    proc = subprocess.run(
        cmd, capture_output=True, text=True, encoding="utf-8", timeout=45,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "PYTHONUTF8": "1"},
    )
    return {
        "command": " ".join(cmd),
        "returncode": proc.returncode,
        "stdout": proc.stdout.strip(),
        "stderr": proc.stderr.strip(),
    }


def parse_json_or_none(text: str) -> object | None:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def copy_case(candidate: Path, dest: Path) -> Path:
    src = candidate / "projects" / "family-connect"
    shutil.copytree(src, dest)
    return dest


def run_all(candidate: Path) -> dict:
    executable = "Scripts/python.exe" if os.name == "nt" else "bin/python"
    python_bin = candidate / ".venv" / executable
    if not python_bin.is_file():
        python_bin = Path(sys.executable)
    tools_dir = candidate / "tools"
    original = candidate / "projects" / "family-connect"
    orig_receipt = original / "state" / "c3_approvals.json"
    before = {path: sha256_file(path) for path in original.rglob("*") if path.is_file()}
    receipt_data = json.loads(orig_receipt.read_text(encoding="utf-8"))
    actual_c3_sha = receipt_data["approvals"][-1]["sha256"]

    results: dict = {
        "candidate": str(candidate),
        "expected_c3_sha": EXPECTED_C3_SHA,
        "actual_c3_sha": actual_c3_sha,
        "c3_sha_matches_expected": actual_c3_sha == EXPECTED_C3_SHA,
        "candidate_receipt_file_sha_before": before[orig_receipt],
        "scenarios": {},
    }

    with tempfile.TemporaryDirectory(prefix="post-c3-cli-") as tmp:
        tmp_root = Path(tmp)

        # 0. Baseline on fresh isolated copy
        base_proj = copy_case(candidate, tmp_root / "baseline")
        base_c3 = run_cli(python_bin, tools_dir, "c3_approval.py", "check", str(base_proj), "--json")
        base_val = run_cli(python_bin, tools_dir, "ledger.py", "validate", str(base_proj))
        base_cons = run_cli(python_bin, tools_dir, "consistency.py", str(base_proj))
        results["scenarios"]["baseline"] = {
            "c3_check": {**base_c3, "parsed": parse_json_or_none(base_c3["stdout"])},
            "validate": base_val,
            "consistency": base_cons,
            "pass": (
                base_c3["returncode"] == 0
                and base_val["returncode"] == 0
                and base_cons["returncode"] == 0
                and actual_c3_sha == EXPECTED_C3_SHA
            ),
        }

        # 1. Scenario A: Negative control — foundation-challenging synthetic source
        proj_a = copy_case(candidate, tmp_root / "scenario_a")
        input_a = HERE / "inputs" / "A_synthetic_billing_telemetry.md"
        add_src = run_cli(
            python_bin,
            tools_dir,
            "ledger.py",
            "add-source",
            str(proj_a),
            str(input_a),
            "--title",
            "Синтетическая сводка телеметрии биллинга (post-c3 A)",
            "--kind",
            "document",
            "--access",
            "own",
            "--reliability",
            "high",
        )

        # Append synthetic CLM-033 and CHG-001 (processed: false), refute HYP-001/HYP-003
        claims_path = proj_a / "state" / "ledger" / "claims.yaml"
        claims_path.write_text(
            claims_path.read_text(encoding="utf-8").rstrip()
            + "\n- id: CLM-033\n"
            + "  type: fact\n"
            + "  statement: Синтетическая телеметрия показывает, что в 92% случаев интернет участника уже ограничен до сигнала.\n"
            + "  evidence:\n"
            + "  - source: SRC-008\n"
            + "    locator: Новые сведения по диагностике совместимого тарифа, п. 1\n"
            + "    quote: В **92%** зафиксированных случаев интернет на номере участника уже ограничен к моменту, когда система могла бы сформировать сигнал организатору\n"
            + "  confidence:\n"
            + "    level: high\n"
            + "    rationale: Синтетический контрольный источник для проверки пересмотра после C3.\n"
            + "- id: CLM-034\n"
            + "  type: fact\n"
            + "  statement: Синтетическая телеметрия показывает поток 18 впервые подходящих уникальных семей в неделю за последние 4 недели.\n"
            + "  evidence:\n"
            + "  - source: SRC-008\n"
            + "    locator: Новые сведения по диагностике совместимого тарифа, п. 2\n"
            + "    quote: за последние 4 недели фиксируется в среднем **18 впервые подходящих уникальных семей в неделю**\n"
            + "  confidence:\n"
            + "    level: high\n"
            + "    rationale: Синтетический контрольный источник для проверки пересмотра после C3.\n",
            encoding="utf-8",
        )

        changes_path = proj_a / "state" / "ledger" / "changes.yaml"
        changes_path.write_text(
            "- id: CHG-001\n"
            "  date: '2026-11-22'\n"
            "  source: SRC-008\n"
            "  summary: Синтетический источник ставит под пересмотр своевременность (HYP-001) и объём потока (HYP-003).\n"
            "  effects:\n"
            "  - target: HYP-001\n"
            "    effect: contradicts\n"
            "    note: Задержка 4–6 ч и 92% ограничений до сигнала противоречат своевременному вмешательству.\n"
            "  - target: HYP-003\n"
            "    effect: contradicts\n"
            "    note: Поток 18 семей/нед не позволяет набрать выборку в Q1–Q2.\n"
            "  processed: false\n",
            encoding="utf-8",
        )

        hyps_path = proj_a / "state" / "ledger" / "hypotheses.yaml"
        hyps_text = hyps_path.read_text(encoding="utf-8")
        hyps_text = hyps_text.replace(
            "  verdict: open\n  would_change_decision: true\n- id: HYP-002",
            "  verdict: refuted\n  would_change_decision: true\n- id: HYP-002",
            1,
        )
        hyps_text = hyps_text.replace(
            "  verdict: open\n  would_change_decision: true\n- id: HYP-004",
            "  verdict: refuted\n  would_change_decision: true\n- id: HYP-004",
            1,
        )
        hyps_path.write_text(hyps_text, encoding="utf-8")

        a_val_stale = run_cli(python_bin, tools_dir, "ledger.py", "validate", str(proj_a))
        a_impact = run_cli(python_bin, tools_dir, "ledger.py", "impact", str(proj_a), "CHG-001", "--json")
        a_verify = run_cli(python_bin, tools_dir, "verify.py", str(proj_a), "--change", "CHG-001", "--json")
        a_c3_stale = run_cli(python_bin, tools_dir, "c3_approval.py", "check", str(proj_a), "--json")
        a_cons_stale = run_cli(python_bin, tools_dir, "consistency.py", str(proj_a), "--json")

        # Now set DEC-001 to needs_review (awaiting repeat C2)
        dec_path = proj_a / "state" / "ledger" / "decisions.yaml"
        dec_path.write_text(
            dec_path.read_text(encoding="utf-8").replace("  state: decided\n", "  state: needs_review\n", 1),
            encoding="utf-8",
        )
        a_val_nr = run_cli(python_bin, tools_dir, "ledger.py", "validate", str(proj_a))
        a_c3_nr = run_cli(python_bin, tools_dir, "c3_approval.py", "check", str(proj_a), "--json")
        a_prep_nr = run_cli(python_bin, tools_dir, "c3_approval.py", "prepare", str(proj_a))
        a_cons_nr = run_cli(python_bin, tools_dir, "consistency.py", str(proj_a), "--json")

        parsed_a_c3_stale = parse_json_or_none(a_c3_stale["stdout"])
        parsed_a_c3_nr = parse_json_or_none(a_c3_nr["stdout"])
        parsed_a_verify = parse_json_or_none(a_verify["stdout"])
        parsed_a_impact = parse_json_or_none(a_impact["stdout"])

        results["scenarios"]["scenario_a_negative_control"] = {
            "add_source": add_src,
            "stage_1_hypotheses_refuted_before_c2_reset": {
                "validate": a_val_stale,
                "impact_summary": {
                    "returncode": a_impact["returncode"],
                    "affected_objects": sorted(parsed_a_impact.get("affected", {}).keys())
                    if isinstance(parsed_a_impact, dict) and isinstance(parsed_a_impact.get("affected"), dict)
                    else parsed_a_impact,
                },
                "verify_change": {
                    "returncode": a_verify["returncode"],
                    "counts": parsed_a_verify.get("counts") if isinstance(parsed_a_verify, dict) else None,
                    "checked": parsed_a_verify.get("checked") if isinstance(parsed_a_verify, dict) else None,
                },
                "c3_check": {**a_c3_stale, "parsed": parsed_a_c3_stale},
                "consistency_error_codes": [
                    item["code"]
                    for item in (parse_json_or_none(a_cons_stale["stdout"]) or [])
                    if isinstance(item, dict) and item.get("status") == "error"
                ],
                "consistency_returncode": a_cons_stale["returncode"],
            },
            "stage_2_dec_needs_review_blocks_c3": {
                "validate": a_val_nr,
                "c3_check": {**a_c3_nr, "parsed": parsed_a_c3_nr},
                "c3_prepare": a_prep_nr,
                "consistency_error_codes": [
                    item["code"]
                    for item in (parse_json_or_none(a_cons_nr["stdout"]) or [])
                    if isinstance(item, dict) and item.get("status") == "error"
                ],
                "consistency_returncode": a_cons_nr["returncode"],
            },
            "pass": (
                add_src["returncode"] == 0
                and a_val_stale["returncode"] == 0
                and a_impact["returncode"] == 0
                and a_c3_stale["returncode"] == 1
                and isinstance(parsed_a_c3_stale, dict)
                and parsed_a_c3_stale.get("status") == "stale"
                and a_val_nr["returncode"] == 0
                and a_c3_nr["returncode"] == 2
                and isinstance(parsed_a_c3_nr, dict)
                and parsed_a_c3_nr.get("status") == "invalid"
                and a_prep_nr["returncode"] == 2
                and a_cons_nr["returncode"] == 1
            ),
        }

        # 2. Scenario B: Local wording edit INSIDE approved C3 package (no choice change -> repeat C3 only, no C2)
        proj_b = copy_case(candidate, tmp_root / "scenario_b")
        comms_path = proj_b / "outputs" / "06_comms_strategy.md"
        old_phrase = "Этот текст применим и к старой ссылке"
        new_phrase = "Тот же текст показывается при открытии старой ссылки"
        comms_text = comms_path.read_text(encoding="utf-8")
        assert old_phrase in comms_text, "expected phrase missing in 06_comms_strategy.md"
        comms_path.write_text(comms_text.replace(old_phrase, new_phrase, 1), encoding="utf-8")

        b_val = run_cli(python_bin, tools_dir, "ledger.py", "validate", str(proj_b))
        b_c3 = run_cli(python_bin, tools_dir, "c3_approval.py", "check", str(proj_b), "--json")
        b_cons = run_cli(python_bin, tools_dir, "consistency.py", str(proj_b), "--json")
        b_prep = run_cli(python_bin, tools_dir, "c3_approval.py", "prepare", str(proj_b))
        parsed_b_c3 = parse_json_or_none(b_c3["stdout"])
        parsed_b_cons = parse_json_or_none(b_cons["stdout"]) or []
        b_cons_errors = [item["code"] for item in parsed_b_cons if isinstance(item, dict) and item.get("status") == "error"]

        results["scenarios"]["scenario_b_package_wording"] = {
            "edited_file": "outputs/06_comms_strategy.md",
            "change": f"{old_phrase} -> {new_phrase}",
            "validate": b_val,
            "c3_check": {**b_c3, "parsed": parsed_b_c3},
            "consistency_returncode": b_cons["returncode"],
            "consistency_error_codes": b_cons_errors,
            "c3_prepare": b_prep,
            "pass": (
                b_val["returncode"] == 0
                and b_c3["returncode"] == 1
                and isinstance(parsed_b_c3, dict)
                and parsed_b_c3.get("status") == "stale"
                and parsed_b_c3.get("changed") == ["outputs/06_comms_strategy.md"]
                and b_cons["returncode"] == 1
                and b_cons_errors == ["consistency.c3_approval_stale"]
                and b_prep["returncode"] == 0
                and len(b_prep["stdout"]) == 64
                and b_prep["stdout"] != EXPECTED_C3_SHA
            ),
        }

        # 3. Scenario B-ext: Neutral external edit OUTSIDE approved C3 package (unchanged C3, no C2)
        proj_b_ext = copy_case(candidate, tmp_root / "scenario_b_ext")
        ext_path = proj_b_ext / "outputs" / "10_market_comparison.md"
        ext_text = ext_path.read_text(encoding="utf-8")
        ext_path.write_text(
            ext_text.replace("Рыночные аналоги для проверки макета", "Рыночные аналоги для проверки макета (редакционная правка)", 1),
            encoding="utf-8",
        )

        bext_val = run_cli(python_bin, tools_dir, "ledger.py", "validate", str(proj_b_ext))
        bext_c3 = run_cli(python_bin, tools_dir, "c3_approval.py", "check", str(proj_b_ext), "--json")
        bext_cons = run_cli(python_bin, tools_dir, "consistency.py", str(proj_b_ext), "--json")
        bext_prep = run_cli(python_bin, tools_dir, "c3_approval.py", "prepare", str(proj_b_ext))
        parsed_bext_c3 = parse_json_or_none(bext_c3["stdout"])
        parsed_bext_cons = parse_json_or_none(bext_cons["stdout"]) or []
        bext_cons_errors = [item["code"] for item in parsed_bext_cons if isinstance(item, dict) and item.get("status") == "error"]

        results["scenarios"]["scenario_b_ext_neutral"] = {
            "edited_file": "outputs/10_market_comparison.md",
            "change": "Локальная редакционная правка заголовка во внешнем справочном документе",
            "validate": bext_val,
            "c3_check": {**bext_c3, "parsed": parsed_bext_c3},
            "consistency_returncode": bext_cons["returncode"],
            "consistency_error_codes": bext_cons_errors,
            "c3_prepare": bext_prep,
            "pass": (
                bext_val["returncode"] == 0
                and bext_c3["returncode"] == 0
                and isinstance(parsed_bext_c3, dict)
                and parsed_bext_c3.get("status") == "current"
                and parsed_bext_c3.get("changed") == []
                and bext_cons["returncode"] == 0
                and bext_cons_errors == []
                and bext_prep["returncode"] == 0
                and bext_prep["stdout"] == EXPECTED_C3_SHA
            ),
        }

    after = {path: sha256_file(path) for path in original.rglob("*") if path.is_file()}
    results["candidate_receipt_file_sha_after"] = after[orig_receipt]
    results["candidate_files_checked"] = len(before)
    results["candidate_untouched"] = before == after
    results["all_pass"] = all(s["pass"] for s in results["scenarios"].values()) and results["candidate_untouched"]
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", type=Path, default=DEFAULT_CANDIDATE)
    parser.add_argument("--out", type=Path, default=None, help="Сохранить JSON-отчёт в файл")
    args = parser.parse_args()

    results = run_all(args.candidate)
    encoded = json.dumps(results, ensure_ascii=False, indent=2)
    if args.out is not None:
        args.out.write_text(encoded + "\n", encoding="utf-8")
    else:
        print(encoded)
    return 0 if results["all_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

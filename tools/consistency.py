#!/usr/bin/env python3
"""Содержательные проверки пакета перед согласованием и после пересмотра."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re

import c3_approval
import evidence
from ledger import Ledger, prefix
from strategy_checks import brief_coverage, comms, econ, options, rebuild, validation


def check(project: Path, *, include_approval: bool = True) -> list[dict]:
    ledger = Ledger(project)
    if ledger.load_problems:
        return [
            {
                "code": "consistency.ledger_unreadable",
                "status": "error",
                "message": f"{message}. Сначала запустите ledger.py validate.",
                "file": str(ledger.dir / file) if Path(file).name == file else file,
                "line": 0,
                "ids": [],
            }
            for file, message in ledger.load_problems
        ]

    findings = []
    for module in (rebuild, econ, options, comms, validation, evidence, brief_coverage):
        findings.extend(module.check(project))

    if include_approval:
        state, message, changed = c3_approval.status(ledger)
        if state in ("stale", "invalid"):
            findings.append({
                "code": f"consistency.c3_approval_{state}",
                "status": "error",
                "message": message + (" Изменилось: " + ", ".join(changed) if changed else ""),
                "file": c3_approval.RECEIPT,
                "line": 0,
                "ids": [key for key in changed if c3_approval.REFERENCE.fullmatch(key)],
            })

    decision_path = ledger.dir / "decisions.yaml"
    if not decision_path.is_file():
        return findings

    id_lines = {}
    for number, line in enumerate(decision_path.read_text(encoding="utf-8").splitlines(), 1):
        match = re.match(r"\s*-\s*id:\s*['\"]?(DEC-\d+)\b", line)
        if match:
            id_lines[match[1]] = number

    for decision_id, decision in ledger.objects.items():
        if prefix(decision_id) != "DEC" or decision.get("state") != "decided":
            continue
        if decision.get("status", "active") != "active":
            continue
        missing = [field for field in ("owner", "review_by") if not str(decision.get(field) or "").strip()]
        if missing:
            findings.append({
                "code": "consistency.decision_follow_up",
                "status": "error",
                "message": f"{decision_id}: заполните поля сопровождения решения ({', '.join(missing)}).",
                "file": "state/ledger/decisions.yaml",
                "line": id_lines.get(decision_id, 0),
                "ids": [decision_id],
            })
    return findings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("project", type=Path, help="Папка проекта")
    parser.add_argument("--json", action="store_true", help="Вывести находки в JSON")
    args = parser.parse_args(argv)
    findings = check(args.project)
    if args.json:
        print(json.dumps(findings, ensure_ascii=False, indent=2))
    else:
        for item in findings:
            location = f"{item['file']}:{item['line']}" if item["line"] else item["file"]
            print(f"[{item['status']}] {item['code']} ({location}): {item['message']}")
    return int(any(item["status"] == "error" for item in findings))


if __name__ == "__main__":
    raise SystemExit(main())

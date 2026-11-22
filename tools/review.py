#!/usr/bin/env python3
"""Вывод оснований и повторное C2 для одного согласованного решения."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml

import c3_approval
from ledger import Ledger, _project_write_lock, atomic_write_text, brief_line, impact, prefix, validate


def context(project: Path) -> None:
    ledger = Ledger(project)
    decisions = c3_approval.active_decisions(ledger)
    if len(decisions) != 1 or not c3_approval.history(project):
        raise ValueError("для нескольких решений или без квитанции C3 откройте workflows/update-decision.md")
    decision = decisions[0]
    print(f"{decision['id']} · {decision['state']} · прежний выбор: {decision.get('chosen')}")
    print(decision["question"])
    print(decision.get("rationale", ""))
    for oid in dict.fromkeys((*decision["options"], *decision.get("based_on", ()))):
        obj = ledger.objects.get(oid)
        if obj:
            print(brief_line(oid, obj))
            if prefix(oid) == "CLM":
                for evidence in obj.get("evidence", []):
                    print(f"  {evidence['source']} · {evidence.get('locator', '')} · {evidence.get('quote', '')}")
    for oid, obj in ledger.objects.items():
        if prefix(oid) in ("SRC", "EXP", "CHG") and obj.get("status", "active") == "active":
            print(brief_line(oid, obj))
            if obj.get("path"):
                print(f"  Файл: {obj['path']}")
    for trigger in decision.get("review_triggers", []):
        print(f"Условие пересмотра: {trigger}")


def open_review(project: Path, change_id: str, recommended: str, basis: list[str], rationale: str) -> str:
    if not rationale.strip():
        raise ValueError("укажите краткую записку об изменении выбора и ближайшем действии")
    with _project_write_lock(project) as locked:
        if not locked:
            raise ValueError("реестр занят")
        ledger = Ledger(project)
        check = validate(ledger)
        if check.errors:
            raise ValueError("\n".join(check.errors))
        decisions = c3_approval.active_decisions(ledger)
        entries = c3_approval.history(project)
        change = ledger.objects.get(change_id, {})
        if len(decisions) != 1 or not entries:
            raise ValueError("требуется одно решение с квитанцией C3")
        decision = decisions[0]
        if prefix(change_id) != "CHG" or change.get("processed") is not False:
            raise ValueError("требуется открытое событие CHG")
        if decision["id"] not in impact(ledger, [change_id]):
            raise ValueError("влияние CHG не доходит до указанного решения")
        if recommended == decision.get("chosen") or recommended not in decision["options"]:
            raise ValueError("укажите другой вариант из сравниваемых на C2")
        if ledger.objects.get(recommended, {}).get("status", "active") != "active":
            raise ValueError("рекомендованный вариант должен быть действующим")
        if not all(prefix(oid) == "CLM" and oid in ledger.objects
                   and ledger.objects[oid].get("status", "active") == "active" for oid in basis):
            raise ValueError("основаниями служат действующие CLM")
        if not any(change.get("source") in {target for target, _ in ledger.upstream(oid)} for oid in basis):
            raise ValueError("свяжите хотя бы одно новое основание с источником CHG")

        entry = entries[-1]
        sha = entry["sha256"]
        updates = {
            "state": "needs_review",
            "review_change": change_id,
            "recommended": recommended,
            "based_on": list(dict.fromkeys((*decision.get("based_on", ()), *basis))),
            "rationale": rationale.strip(),
        }
        if decision.get("state") == "needs_review" and all(decision.get(key) == value for key, value in updates.items()):
            c3_approval.check_archive(project, sha)
            return sha
        if decision.get("state") != "decided" or not decision.get("chosen"):
            raise ValueError("сначала завершите открытый выбор")
        c3_approval.archive_package(project, sha, entry["inputs"])
        decision.update(updates)
        after = validate(ledger)
        if after.errors:
            raise ValueError("\n".join(after.errors))
        filename = ledger.origin[decision["id"]]
        target = ledger.dir / filename
        if target.is_symlink():
            raise ValueError("запись решений должна быть обычным файлом")
        rows = [obj for oid, obj in ledger.objects.items() if ledger.origin[oid] == filename]
        atomic_write_text(target, yaml.safe_dump(rows, allow_unicode=True, sort_keys=False))
        return sha


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="action", required=True)
    commands.add_parser("context").add_argument("project", type=Path)
    opening = commands.add_parser("open")
    opening.add_argument("project", type=Path)
    opening.add_argument("change")
    opening.add_argument("--recommend", required=True)
    opening.add_argument("--basis", nargs="+", required=True)
    opening.add_argument("--memo", type=Path, required=True)
    args = parser.parse_args()
    try:
        project = args.project.resolve()
        if args.action == "context":
            context(project)
        else:
            sha = open_review(project, args.change, args.recommend, args.basis, args.memo.read_text(encoding="utf-8"))
            print(f"Повторный C2 открыт. Архив согласованного пакета: {sha}")
    except (OSError, ValueError) as exc:
        print(f"Пересмотр: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

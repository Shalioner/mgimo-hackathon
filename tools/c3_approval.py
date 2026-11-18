#!/usr/bin/env python3
"""Связать ответ человека на C3 с предъявленной версией пакета."""

from __future__ import annotations

import argparse
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
import sys

from ledger import Ledger, _project_write_lock, atomic_write_text, prefix, validate


RECEIPT = "state/c3_approvals.json"
STALE_MESSAGE = "Пакет изменился после последнего согласования C3. Требуется повторное C3."
DOCUMENTS = (
    "outputs/01_brief.md",
    "outputs/03_situation.md",
    "outputs/04_options.md",
    "outputs/05_product_strategy.md",
    "outputs/06_comms_strategy.md",
    "outputs/07_validation_plan.md",
)
REFERENCE = re.compile(r"\b(?:SRC|CLM|HYP|PAIN|ALT|DEC|PRM|EXP)-\d+\b")
SHA256 = re.compile(r"[0-9a-f]{64}")


def digest(value: object) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def read_input(project: Path, relative: str) -> bytes:
    path = project / relative
    if Path(relative).is_absolute() or not path.resolve().is_relative_to(project.resolve()):
        raise ValueError(f"путь за пределами проекта: {relative}")
    return path.read_bytes()


def snapshot(ledger: Ledger) -> dict[str, str]:
    if ledger.load_problems:
        raise ValueError(ledger.load_problems[0][1])
    objects = ledger.objects
    decisions = [
        obj for oid, obj in objects.items()
        if prefix(oid) == "DEC" and obj.get("status", "active") == "active"
    ]
    if not decisions or any(obj.get("state") != "decided" or not obj.get("chosen") for obj in decisions):
        raise ValueError("для C3 требуется действующий выбор руководителя на C2")

    inputs = {}
    pending = {
        oid for oid, obj in objects.items()
        if prefix(oid) in ("DEC", "PRM", "EXP") and obj.get("status", "active") == "active"
    }
    paths = list(DOCUMENTS)
    if (ledger.project / "state/economics.yaml").exists():
        paths.append("state/economics.yaml")
    for relative in paths:
        data = read_input(ledger.project, relative)
        inputs[relative] = hashlib.sha256(data).hexdigest()
        pending.update(set(REFERENCE.findall(data.decode("utf-8"))) & objects.keys())

    visited = set()
    while pending:
        oid = pending.pop()
        if oid in visited:
            continue
        visited.add(oid)
        obj = objects.get(oid)
        if obj is None:
            raise ValueError(f"основание {oid} отсутствует в реестре")
        inputs[oid] = digest(obj)
        if prefix(oid) == "SRC" and (relative := obj.get("path")):
            inputs[f"источник: {relative}"] = hashlib.sha256(read_input(ledger.project, relative)).hexdigest()
        pending.update(target for target, _ in ledger.upstream(oid))
    return inputs


def history(project: Path) -> list[dict]:
    if (project / RECEIPT).is_symlink():
        raise ValueError("запись C3 должна быть обычным файлом")
    try:
        value = json.loads(read_input(project, RECEIPT))
    except FileNotFoundError:
        return []
    if not isinstance(value, dict) or value.get("version") != 1:
        raise ValueError("неизвестный формат записи C3")
    entries = value.get("approvals")
    if not isinstance(entries, list) or not entries:
        raise ValueError("отсутствуют записи C3")
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError("некорректная запись C3")
        inputs = entry.get("inputs")
        response = entry.get("response")
        if (not isinstance(inputs, dict) or not set(DOCUMENTS) <= inputs.keys()
                or not all(isinstance(h, str) and SHA256.fullmatch(h) for h in inputs.values())
                or digest(inputs) != entry.get("sha256")
                or not isinstance(response, str) or not response.strip()):
            raise ValueError("некорректная запись C3")
    return entries


def status(ledger: Ledger) -> tuple[str, str, list[str]]:
    try:
        entries = history(ledger.project)
        if not entries:
            return "unrecorded", "Для C3 нет записи с версией предъявленного пакета.", []
        approved = entries[-1]["inputs"]
        current = snapshot(ledger)
    except (OSError, ValueError, TypeError, AttributeError) as exc:
        return "invalid", f"Версию C3 не удалось проверить. Требуется проверка перед согласованием. {exc}", []
    changed = sorted(key for key in approved.keys() | current.keys() if approved.get(key) != current.get(key))
    if changed:
        return "stale", STALE_MESSAGE, changed
    return "current", "Версия пакета совпадает с последней записью C3.", []


def prepare(project: Path) -> dict[str, str]:
    from consistency import check

    ledger = Ledger(project)
    inputs = snapshot(ledger)
    report = validate(ledger)
    if report.errors:
        raise ValueError("\n".join(report.errors))
    errors = [item["message"] for item in check(project, include_approval=False) if item["status"] == "error"]
    if errors:
        raise ValueError("\n".join(errors))
    if snapshot(Ledger(project)) != inputs:
        raise ValueError("пакет менялся во время проверки. Повторите подготовку C3.")
    return inputs


def record(project: Path, presented: str, response: str) -> None:
    if not response.strip():
        raise ValueError("нужен явный ответ человека об утверждении предъявленного пакета")
    with _project_write_lock(project) as locked:
        if not locked:
            raise ValueError("запись C3 не выполнена")
        entries = history(project)
        inputs = prepare(project)
        if digest(inputs) != presented:
            raise ValueError("предъявленная версия отличается от текущей. Покажите пакет заново и получите новое C3.")
        if entries and entries[-1]["sha256"] == presented:
            return
        entries.append({
            "sha256": presented,
            "recorded_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "response": response.strip(),
            "inputs": inputs,
        })
        payload = json.dumps({"version": 1, "approvals": entries}, ensure_ascii=False, indent=2, sort_keys=True)
        atomic_write_text(project / RECEIPT, payload + "\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    for name in ("prepare", "record", "check"):
        command = sub.add_parser(name)
        command.add_argument("project", type=Path)
        if name == "record":
            command.add_argument("--sha256", required=True, help="хэш пакета, показанного руководителю")
            command.add_argument("--response-file", required=True, type=Path, help="дословное утверждение руководителя")
        elif name == "check":
            command.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    project = args.project.resolve()
    try:
        if not (project / "state/ledger").is_dir():
            raise ValueError("нет папки state/ledger")
        if args.action == "prepare":
            print(digest(prepare(project)))
        elif args.action == "record":
            record(project, args.sha256, args.response_file.read_text(encoding="utf-8"))
            print("Запись C3 сохранена для предъявленной версии.")
        else:
            state, message, changed = status(Ledger(project))
            if args.json:
                print(json.dumps({"status": state, "message": message, "changed": changed}, ensure_ascii=False))
            else:
                print(message)
                if changed:
                    print("Изменилось: " + ", ".join(changed))
            return {"current": 0, "stale": 1, "unrecorded": 1, "invalid": 2}[state]
    except (OSError, ValueError, TypeError, AttributeError) as exc:
        print(f"C3: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

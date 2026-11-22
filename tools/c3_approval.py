#!/usr/bin/env python3
"""Связать ответ человека на C3 с предъявленной версией пакета."""

from __future__ import annotations

import argparse
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import tempfile

from ledger import Ledger, TYPES, _project_write_lock, atomic_write_text, prefix, validate


RECEIPT = "state/c3_approvals.json"
ARCHIVES = "state/history/c3"
STALE_MESSAGE = "Пакет изменился после последнего согласования C3. Требуется повторное C3."
STALE_PRESENTED_MESSAGE = "предъявленная версия отличается от текущей. Покажите пакет заново и получите новое C3."
CANONICAL_APPROVAL = "Утверждаю пакет C3"
DIAGNOSTIC_APPROVAL = "Утверждаю пакет C3 для внутренней диагностики"
DIAGNOSTIC_NOTICE = "Режим: только внутренняя диагностика"
EXPLICIT_APPROVAL = re.compile(
    r"утверждаю[ \t]+(?:обновл[её]нный[ \t]+)?пакет[ \t]+[cс]3"
    r"(?P<diagnostic>[ \t]+для внутренней диагностики)?\.?",
    re.IGNORECASE,
)
DOCUMENTS = (
    "outputs/01_brief.md",
    "outputs/03_situation.md",
    "outputs/04_options.md",
    "outputs/05_product_strategy.md",
    "outputs/06_comms_strategy.md",
    "outputs/07_validation_plan.md",
)
BRIEF_RESPONSE = "outputs/00_brief_response.md"
REFERENCE = re.compile(r"\b(?:SRC|CLM|HYP|PAIN|ALT|DEC|PRM|EXP)-\d+\b")
SHA256 = re.compile(r"[0-9a-f]{64}")


def package_documents(project: Path) -> list[str]:
    paths = list(DOCUMENTS)
    if (project / BRIEF_RESPONSE).exists():
        paths.append(BRIEF_RESPONSE)
    return paths


def digest(value: object) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def read_input(project: Path, relative: str) -> bytes:
    path = project / relative
    if Path(relative).is_absolute() or not path.resolve().is_relative_to(project.resolve()):
        raise ValueError(f"путь за пределами проекта: {relative}")
    return path.read_bytes()


def active_decisions(ledger: Ledger) -> list[dict]:
    return [
        obj for oid, obj in ledger.objects.items()
        if prefix(oid) == "DEC" and obj.get("status", "active") == "active"
    ]


def diagnostic_only(decisions: list[dict]) -> bool:
    return bool(decisions) and all(
        obj.get("approval_scope") == "diagnostic_only" and obj.get("state") == "decided" and obj.get("chosen")
        for obj in decisions
    )


def snapshot(ledger: Ledger) -> dict[str, str]:
    if ledger.load_problems:
        raise ValueError(ledger.load_problems[0][1])
    objects = ledger.objects
    decisions = active_decisions(ledger)
    if not decisions or any(obj.get("state") != "decided" or not obj.get("chosen") for obj in decisions):
        raise ValueError("для C3 требуется действующий выбор руководителя на C2")

    inputs = {}
    pending = {
        oid for oid, obj in objects.items()
        if prefix(oid) in ("DEC", "PRM", "EXP") and obj.get("status", "active") == "active"
    }
    paths = package_documents(ledger.project)
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
        pending = [obj["id"] for obj in active_decisions(ledger)
                   if obj.get("state") == "needs_review" and obj.get("review_change")]
        if pending:
            check_archive(ledger.project, entries[-1]["sha256"])
            return "stale", "Прежний пакет C3 сохранён. Новый пакет готовится после выбора на C2.", pending
        current = snapshot(ledger)
    except (OSError, ValueError, TypeError, AttributeError) as exc:
        return "invalid", f"Версию C3 не удалось проверить. Требуется проверка перед согласованием. {exc}", []
    changed = sorted(key for key in approved.keys() | current.keys() if approved.get(key) != current.get(key))
    if changed:
        return "stale", STALE_MESSAGE, changed
    if diagnostic_only(active_decisions(ledger)):
        match = EXPLICIT_APPROVAL.fullmatch(entries[-1]["response"])
        if not match or not match.group("diagnostic"):
            return "invalid", "В квитанции отсутствует явное согласование внутренней диагностики.", []
        return "current", "На C3 утверждён только внутренний план. Пилот и публикация требуют отдельных решений.", []
    return "current", "Версия пакета совпадает с последней записью C3.", []


def verify_package(project: Path, *, review: list[str] | None = None, change: str = "") -> list[str]:
    from verify import blocking_verdicts, verify

    files = [Path(relative).name for relative in package_documents(project)]
    blocked = blocking_verdicts(strict=True)
    errors = []
    for item in verify(project, files, change, exact_files=True, require_change_targets=True)["items"]:
        verdict = item["verdict"]
        if verdict in blocked:
            errors.append(f"[{verdict}] outputs/{item['file']}:{item['line']} — {item['note']}")
        elif verdict == "check" and review is not None:
            review.append(f"outputs/{item['file']}:{item['line']}: {item['note']}")
    return errors


def prepare(project: Path, *, review: list[str] | None = None) -> dict[str, str]:
    from consistency import check

    ledger = Ledger(project)
    inputs = snapshot(ledger)
    report = validate(ledger)
    if report.errors:
        raise ValueError("\n".join(report.errors))
    decisions = active_decisions(ledger)
    changes = {obj["review_change"] for obj in decisions if obj.get("review_change")}
    if changes:
        if any(prefix(change) != "CHG" or change not in ledger.objects for change in changes):
            raise ValueError("для повторного C3 требуется зарегистрированное событие пересмотра")
        entries = history(project)
        if not entries:
            raise ValueError("для повторного C3 нужна квитанция предыдущей версии")
        previous = entries[-1]
        check_archive(project, previous["sha256"])
        archived = Ledger(project / ARCHIVES / previous["sha256"])
        choice_changed = any(
            obj["chosen"] != archived.objects.get(obj["id"], {}).get("chosen")
            for obj in decisions if obj.get("review_change")
        )
        if choice_changed:
            unchanged = [name for name in DOCUMENTS if Path(name).name[:2] in ("05", "06")
                         and inputs[name] == previous["inputs"][name]]
            if unchanged:
                raise ValueError("после смены выбора на C2 обновите стратегии: " + ", ".join(unchanged))
    if any(obj.get("approval_scope") == "diagnostic_only" for obj in decisions):
        if not diagnostic_only(decisions):
            raise ValueError("смешанная область C3: ограничение должно действовать для всех решений пакета")
    errors = [item["message"] for item in check(project, include_approval=False) if item["status"] == "error"]
    if errors:
        raise ValueError("\n".join(errors))
    verify_errors = list(dict.fromkeys(
        error for change in sorted(changes or {""})
        for error in verify_package(project, review=review, change=change)
    ))
    if verify_errors:
        raise ValueError("\n".join(verify_errors))
    if snapshot(Ledger(project)) != inputs:
        raise ValueError("пакет менялся во время проверки. Повторите подготовку C3.")
    return inputs


def parse_approval_response(response: str, *, diagnostic: bool) -> str:
    text = response.strip() if isinstance(response, str) else ""
    found = EXPLICIT_APPROVAL.fullmatch(text)
    if not found or bool(found.group("diagnostic")) != diagnostic:
        phrase = DIAGNOSTIC_APPROVAL if diagnostic else CANONICAL_APPROVAL
        raise ValueError(
            f"нужно явное утверждение предъявленного пакета без оговорок: «{phrase}»"
        )
    return text


def archive_package(project: Path, presented: str, inputs: dict[str, str]) -> None:
    archive_root = project / ARCHIVES
    if not archive_root.resolve().is_relative_to(project.resolve()):
        raise ValueError("папка истории C3 за пределами проекта")
    archive_root.mkdir(parents=True, exist_ok=True)
    archive = archive_root / presented
    if archive.is_symlink():
        raise ValueError("архив C3 должен быть обычным каталогом")
    if archive.exists():
        if snapshot(Ledger(archive)) != inputs:
            raise ValueError("сохранённая версия C3 не совпадает с утверждённым пакетом")
        return

    sources = [name[len("источник: "):] for name in inputs if name.startswith("источник: ")]
    documents = [name for name in inputs if name.startswith(("outputs/", "state/"))]
    registry = [f"state/ledger/{filename}" for filename, _, _ in TYPES.values()
                if (project / "state/ledger" / filename).is_file()]
    with tempfile.TemporaryDirectory(dir=archive_root) as temporary:
        staged = Path(temporary) / "package"
        for name in (*documents, *registry, *sources):
            data = read_input(project, name)
            destination = staged / name
            if not destination.resolve().is_relative_to(staged.resolve()):
                raise ValueError(f"путь за пределами архива: {name}")
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(data)
        if snapshot(Ledger(staged)) != inputs:
            raise ValueError("пакет менялся во время сохранения версии C3")
        os.replace(staged, archive)


def record(project: Path, presented: str, response: str) -> None:
    ledger = Ledger(project)
    if digest(snapshot(ledger)) != presented:
        raise ValueError(STALE_PRESENTED_MESSAGE)
    text = parse_approval_response(response, diagnostic=diagnostic_only(active_decisions(ledger)))
    with _project_write_lock(project) as locked:
        if not locked:
            raise ValueError("запись C3 не выполнена")
        entries = history(project)
        inputs = prepare(project)
        if digest(inputs) != presented:
            raise ValueError(STALE_PRESENTED_MESSAGE)
        archive_package(project, presented, inputs)
        if entries and entries[-1]["sha256"] == presented:
            return
        entries.append({
            "sha256": presented,
            "recorded_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "response": text,
            "inputs": inputs,
        })
        payload = json.dumps({"version": 1, "approvals": entries}, ensure_ascii=False, indent=2, sort_keys=True)
        atomic_write_text(project / RECEIPT, payload + "\n")


def check_archive(project: Path, presented: str) -> None:
    entry = next((item for item in history(project) if item["sha256"] == presented), None)
    if entry is None:
        raise ValueError("нет квитанции для указанного SHA-256")
    archive = project / ARCHIVES / presented
    if archive.is_symlink() or not archive.resolve().is_relative_to(project.resolve()):
        raise ValueError("недопустимый путь архива C3")
    if not archive.is_dir():
        raise ValueError("для указанной квитанции отсутствует архив материалов C3")
    if snapshot(Ledger(archive)) != entry["inputs"]:
        raise ValueError("сохранённая версия C3 не совпадает с утверждённым пакетом")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    for name in ("prepare", "record", "check", "check-archive"):
        command = sub.add_parser(name)
        command.add_argument("project", type=Path)
        if name == "record":
            command.add_argument("--sha256", required=True, help="хэш пакета, показанного руководителю")
            command.add_argument("--response-file", required=True, type=Path, help="дословное утверждение руководителя")
        elif name == "check":
            command.add_argument("--json", action="store_true")
        elif name == "check-archive":
            command.add_argument("--sha256", required=True, help="хэш утверждённого пакета")
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
        elif args.action == "check-archive":
            check_archive(project, args.sha256)
            print(f"Архив C3 подтверждён: {args.sha256}")
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

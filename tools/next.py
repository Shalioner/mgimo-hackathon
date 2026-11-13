#!/usr/bin/env python3
"""Очередь действий руководителя по реестру; только чтение, без сети и моделей.

python tools/next.py projects/example --on 2026-11-12 --json
Дата включительна для review_by; date: null означает отсутствие даты в реестре.
Порядок — известная дата, затем ID; это не оценка бизнес-приоритета.
Эксперименты без однозначного срока в эту версию очереди не входят.
"""
from __future__ import annotations

import argparse
from datetime import date
import json
from pathlib import Path
import sys

import yaml

from ledger import Ledger, prefix


def iso_date(value: str) -> date:
    """Не угадывать дату из свободного текста или длительности."""
    try:
        parsed = date.fromisoformat(value)
        if parsed.isoformat() != value:
            raise ValueError
        return parsed
    except (TypeError, ValueError):
        raise ValueError(f"ожидается дата YYYY-MM-DD, получено {value!r}") from None


def field_date(obj: dict, field: str) -> str | None:
    value = obj.get(field)
    if value is None:
        return None
    try:
        return iso_date(value).isoformat()
    except ValueError as exc:
        raise ValueError(f"{obj['id']}.{field}: {exc}") from None


def stale_reasons(ledger: Ledger, oid: str) -> list[dict]:
    """Найти устаревшие основания через существующий граф, включая цепочки и циклы."""
    seen = {oid}
    pending = [(oid, [oid])]
    reasons = []
    while pending:
        current, path = pending.pop(0)
        for target, _role in sorted(ledger.upstream(current)):
            if target in seen:
                continue
            seen.add(target)
            if target not in ledger.objects:
                raise ValueError(f"{current}: ссылка на отсутствующий {target}")
            basis = ledger.objects[target]
            target_path = path + [target]
            status = basis.get("status", "active")
            if status in ("retracted", "superseded"):
                reasons.append({
                    "code": f"basis_{status}", "date": None,
                    "reference": target, "path": target_path,
                    "superseded_by": basis.get("superseded_by"),
                })
            pending.append((target, target_path))
    return reasons


def build_queue(project: Path, on: date) -> dict:
    ledger = Ledger(project)
    if ledger.load_problems:
        raise ValueError("; ".join(f"{where}: {problem}" for where, problem in ledger.load_problems))
    items = []
    for oid, obj in sorted(ledger.objects.items()):
        if obj.get("status", "active") in ("retracted", "superseded"):
            continue
        reasons = []
        if prefix(oid) == "DEC":
            review_by = field_date(obj, "review_by")
            if obj.get("state") == "needs_review":
                reasons.append({"code": "needs_review", "date": None})
            if review_by is not None and review_by <= on.isoformat():
                reasons.append({"code": "review_due", "date": review_by})
            reasons.extend(stale_reasons(ledger, oid))
            action = ("Руководителю продукта: проверить причины и основания решения, "
                      "уточнить необходимость пересмотра; выбор и C2 подтверждает человек.")
        elif prefix(oid) == "CHG":
            if obj.get("processed") is not False:
                continue
            reasons.append({"code": "unprocessed_change", "date": field_date(obj, "date")})
            action = (f"Руководителю продукта: уточнить влияние новых сведений; агенту выполнить "
                      f"ledger.py impact {project} {oid}; изменение выбора подтвердить человеку на C2.")
        if reasons:
            reasons.sort(key=lambda r: (r["code"], r.get("reference", ""), r["date"] or ""))
            dates = [r["date"] for r in reasons if r["date"] is not None]
            items.append({"id": oid, "date": min(dates) if dates else None,
                          "reasons": reasons, "next_action": action})
    items.sort(key=lambda item: (item["date"] is None, item["date"] or "", item["id"]))
    return {"on": on.isoformat(), "items": items}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("project", type=Path, help="локальная папка проекта")
    parser.add_argument("--on", type=iso_date, default=None, help="дата YYYY-MM-DD, по умолчанию сегодня")
    parser.add_argument("--json", action="store_true", help="машиночитаемый JSON")
    args = parser.parse_args(argv)
    on = args.on if args.on is not None else date.today()
    try:
        result = build_queue(args.project, on)
    except (OSError, UnicodeError, ValueError, TypeError, AttributeError, yaml.YAMLError) as exc:
        if args.json:
            print(json.dumps({"on": on.isoformat(), "items": [], "errors": [str(exc)]}, ensure_ascii=False))
        else:
            print(f"Ошибка чтения очереди: {exc}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"Что решать на {result['on']} (дата, затем ID; без бизнес-приоритета)")
        if not result["items"]:
            print("По проверяемым полям действий нет; эксперименты здесь не учитываются.")
        for item in result["items"]:
            reasons = ", ".join(r["code"] + (f" [{r['reference']}]" if "reference" in r else "")
                                for r in item["reasons"])
            print(f"{item['id']} | {item['date'] or 'дата не задана'} | {reasons}\n  {item['next_action']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

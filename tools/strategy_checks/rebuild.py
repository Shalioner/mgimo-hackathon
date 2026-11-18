#!/usr/bin/env python3
"""M07: Проверка перестройки стратегий 05/06 после пересмотра решения.

Проверяет:
- наличие всех обязательных H2-разделов шаблонов 05/06 в актуальной части документа
  (до «## Исторический снимок»);
- ровно один заголовок «# Главное» / «## Главное» на документ.

Контракт check(project: Path) -> list[dict] с ключами:
code, status (ok/warn/error/not_assessed), message, file, line, ids.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys
from typing import Dict, List, Pattern, Tuple

_TOOLS_DIR = pathlib.Path(__file__).resolve().parent.parent
if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))

_HISTORY_RE = re.compile(
    r"^##\s+(?:Исторический\s+снимок|История\s+до|Архив\s+до)\b",
    re.IGNORECASE,
)
_MAIN_RE = re.compile(r"^#{1,2}\s+Главное(?:\s|$|[—:\-])", re.IGNORECASE)
_H2_RE = re.compile(r"^##\s+(.+?)\s*$")


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().lower().replace("ё", "е").replace("<->", "↔"))


_REQUIRED_H2: Dict[str, List[Tuple[str, str]]] = {
    "outputs/05_product_strategy.md": [
        ("Аудитория и её задача", "аудитория и ее задача"),
        ("Альтернативы для аудитории", "альтернативы для аудитории"),
        ("Выбранное направление", "выбранное направление"),
        ("Осознанные отказы", "осознанные отказы"),
        ("Польза ↔ затраты и доходы", "польза ↔ затраты и доходы"),
        ("Риски и условия пересмотра", "риски и условия пересмотра"),
    ],
    "outputs/06_comms_strategy.md": [
        ("Аудитория и ситуация", "аудитория и ситуация"),
        ("Барьер понимания или выбора", "барьер понимания или выбора"),
        ("Ключевое сообщение и его подтверждение", "ключевое сообщение и его подтверждение"),
        ("Роль каналов", "роль каналов"),
        ("Чего мы не обещаем", "чего мы не обещаем"),
    ],
}


def _check_single_file(project: pathlib.Path, rel_path: str) -> List[dict]:
    path = project / rel_path
    if not path.is_file():
        return [
            {
                "code": "rebuild.not_assessed",
                "status": "not_assessed",
                "message": f"Файл {rel_path} отсутствует — проверка перестройки не применялась.",
                "file": rel_path,
                "line": 0,
                "ids": [],
            }
        ]

    lines = path.read_text(encoding="utf-8").splitlines()
    findings: List[dict] = []

    main_lines: List[int] = []
    history_line: int | None = None
    active_h2: set[str] = set()

    for idx, raw in enumerate(lines, start=1):
        stripped = raw.strip()
        if history_line is None and _HISTORY_RE.match(stripped):
            history_line = idx
        if _MAIN_RE.match(stripped):
            main_lines.append(idx)
        if history_line is None:
            m = _H2_RE.match(stripped)
            if m:
                active_h2.add(_norm(m.group(1)))

    if len(main_lines) == 0:
        findings.append(
            {
                "code": "rebuild.missing_main",
                "status": "error",
                "message": f"В {rel_path} отсутствует обязательный блок «# Главное» / «## Главное».",
                "file": rel_path,
                "line": 1,
                "ids": [],
            }
        )
    elif len(main_lines) > 1:
        lines_str = ", ".join(str(ln) for ln in main_lines)
        findings.append(
            {
                "code": "rebuild.duplicate_main",
                "status": "error",
                "message": (
                    f"В {rel_path} найдено {len(main_lines)} заголовков «Главное» "
                    f"(строки {lines_str}); должен быть ровно один."
                ),
                "file": rel_path,
                "line": main_lines[1],
                "ids": [],
            }
        )

    missing_sections: List[str] = []
    for display_name, norm_name in _REQUIRED_H2[rel_path]:
        if not any(norm_name == h or h.startswith(norm_name + " ") or h.startswith(norm_name + ":") for h in active_h2):
            missing_sections.append(display_name)

    if missing_sections:
        boundary_note = (
            f"до строки {history_line} («Исторический снимок»)"
            if history_line is not None
            else "в документе"
        )
        findings.append(
            {
                "code": "rebuild.missing_sections",
                "status": "error",
                "message": (
                    f"В {rel_path} {boundary_note} отсутствуют обязательные H2-разделы шаблона: "
                    + ", ".join(missing_sections)
                    + ". Стратегию нужно пересобрать по шаблону, а прежнюю версию перенести в «## Исторический снимок»."
                ),
                "file": rel_path,
                "line": history_line or 1,
                "ids": [],
            }
        )

    if not findings:
        findings.append(
            {
                "code": "rebuild.ok",
                "status": "ok",
                "message": f"{rel_path}: актуальная структура соответствует шаблону, один блок «Главное».",
                "file": rel_path,
                "line": main_lines[0] if main_lines else 1,
                "ids": [],
            }
        )

    return findings


def check(project: pathlib.Path) -> List[dict]:
    project = pathlib.Path(project)
    results: List[dict] = []
    for rel_path in _REQUIRED_H2:
        results.extend(_check_single_file(project, rel_path))
    return results


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="M07: Проверка перестройки стратегий 05 и 06.")
    parser.add_argument("project", type=pathlib.Path, help="Путь к папке проекта")
    parser.add_argument("--json", action="store_true", help="Вывести результат в JSON")
    args = parser.parse_args(argv)

    findings = check(args.project)
    has_error = any(item["status"] == "error" for item in findings)

    if args.json:
        print(json.dumps(findings, ensure_ascii=False, indent=2))
    else:
        for item in findings:
            loc = f"{item['file']}:{item['line']}" if item["line"] else item["file"]
            print(f"[{item['status']}] {item['code']} ({loc}): {item['message']}")

    return 1 if has_error else 0


if __name__ == "__main__":
    sys.exit(main())

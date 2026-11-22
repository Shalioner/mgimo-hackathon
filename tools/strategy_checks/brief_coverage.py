#!/usr/bin/env python3
"""Минимальный структурный T0-контроль карты покрытия сложного брифа (state/brief_coverage.md).

Принцип совместимости:
- Без `state/brief_coverage.md` возвращает `[]` (прежний JSON/вывод `consistency.py` без шума).
- С картой проверяет только структуру без стемминга и лексических порогов:
  * таблица, уникальные ID, `да`/`нет`, требование, валидный локатор источника;
  * `закрыто` / `пробел`: путь в `outputs/*.md` (кроме `outputs/01_brief.md`), активный раздел
    или строка до `## Исторический снимок`, и либо явная отметка `[ID]` на непустой строке ответа,
    либо точная короткая цитата `«…»` (≥10 непробельных символов) в этом разделе;
  * `пробел`: дополнительно `Почему:` и `Как узнать:` (остаётся видимым `warn`);
  * `открыто`: обязательные пункты на C3 блокируют (`error`), до C3 и необязательные — `warn`.
Смысл и соответствие поручению T0 не оценивает: их проверяют человек и `skills/claim-verifier` на C3.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys
from typing import List, Optional, Tuple

_TOOLS_DIR = pathlib.Path(__file__).resolve().parent.parent
if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))

from ledger import Ledger, prefix  # noqa: E402
from strategy_checks import comms, rebuild, validation  # noqa: E402

_MAP_FILE = "state/brief_coverage.md"
_C3_FILES = (
    "outputs/05_product_strategy.md",
    "outputs/06_comms_strategy.md",
    "outputs/07_validation_plan.md",
)
_ID_RE = re.compile(r"^[A-Za-z][A-Za-z0-9]{1,15}$")
_SRC_LOC_RE = re.compile(r"^(SRC-\d{3})(?::(\d+)(?:[-–—](\d+))?|#(.+))?$")
_PATH_LOC_RE = re.compile(r"^([^:#\n]+\.[A-Za-z0-9]+)(?::(\d+)(?:[-–—](\d+))?|#(.+))$")
_BINDING_SPLIT_RE = re.compile(
    r"^(?P<head>[^;«\"]+?(?:\s*(?:«[^»]+»|\"[^\"\n]+\"))?)\s*(?:;(?P<tail>.*)|(?P<gap>\b(?:почему|как\s+узнать)\s*:.*))?$",
    re.IGNORECASE,
)
_QUOTE_RE = re.compile(r"«([^»]+)»|\"([^\"\n]+)\"")
_FENCE_RE = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")
_HEADING_RE = re.compile(r"^(#{1,4})\s+(.+?)\s*$")
_BOLD_LABEL_RE = re.compile(r"^\*\*([^*]+?)\*\*\s*(.*)$")
_WHY_RE = re.compile(r"\bпочему\s*:\s*(.+?)(?=;|\n|\bкак\s+узнать\s*:|$)", re.IGNORECASE)
_HOW_RE = re.compile(r"\bкак\s+узнать\s*:\s*(.+?)(?=;|\n|\bпочему\s*:|$)", re.IGNORECASE)
_LEDGER_ID_RE = re.compile(r"\b(?:SRC|CLM|HYP|PAIN|ALT|DEC|PRM|EXP|CHG)-\d{3}\b")
_MIN_QUOTE_CHARS = 10


def _finding(code: str, status: str, message: str, *, line: int = 0, ids: Optional[List[str]] = None) -> dict:
    return {"code": code, "status": status, "message": message, "file": _MAP_FILE, "line": line, "ids": ids or []}


def _safe_project_file(project: pathlib.Path, rel_path: str) -> Optional[pathlib.Path]:
    raw = rel_path.replace("\\", "/").strip()
    path = pathlib.PurePosixPath(raw)
    if not raw or path.is_absolute() or ".." in path.parts:
        return None
    candidate = project / raw
    if candidate.is_symlink():
        return None
    try:
        resolved = candidate.resolve()
        if not resolved.is_relative_to(project.resolve()):
            return None
    except OSError:
        return None
    return resolved if resolved.is_file() else None


def _active_markdown(path: pathlib.Path) -> List[Tuple[int, str]]:
    lines = []
    fence = ""
    for line, text in comms._active_lines(path):
        match = _FENCE_RE.match(text)
        if match:
            marker, tail = match.groups()
            if not fence:
                fence = marker
            elif marker[0] == fence[0] and len(marker) >= len(fence) and not tail.strip():
                fence = ""
        elif not fence:
            lines.append((line, text))
    return lines


def _matches_title(title: str, want: str) -> bool:
    norm = rebuild._norm(title)
    return norm == want or norm.startswith((want + " ", want + ":", want + " ("))


def _resolve_locator(
    project: pathlib.Path,
    rel_path: str,
    line_start: Optional[str],
    line_end: Optional[str],
    section: Optional[str],
    *,
    allow_bold_label: bool = False,
) -> Tuple[Optional[List[Tuple[int, str]]], Optional[str]]:
    target = _safe_project_file(project, rel_path)
    if target is None:
        return None, f"файл «{rel_path}» не найден в проекте"
    try:
        numbered = _active_markdown(target) if target.suffix.lower() == ".md" else comms._active_lines(target)
    except (OSError, UnicodeDecodeError) as exc:
        return None, f"не удалось прочитать «{rel_path}»: {exc}"

    if line_start is not None:
        start, end = int(line_start), int(line_end) if line_end else int(line_start)
        if start < 1 or end < start:
            return None, f"некорректный диапазон строк {start}–{end} в {rel_path}"
        matched = [(ln, text) for ln, text in numbered if start <= ln <= end]
        if not matched or not any(text.strip() for _, text in matched):
            return None, f"строка {line_start} отсутствует или пуста в активной части {rel_path}"
        return matched, None

    want = rebuild._norm((section or "").strip().strip("`"))
    if not want:
        return None, f"не указано имя раздела для {rel_path}"

    for idx, (_, raw) in enumerate(numbered):
        match = _HEADING_RE.match(raw.strip())
        if match and _matches_title(match.group(2), want):
            level = len(match.group(1))
            body: List[Tuple[int, str]] = []
            for ln2, raw2 in numbered[idx + 1 :]:
                m2 = _HEADING_RE.match(raw2.strip())
                if m2 and len(m2.group(1)) <= level:
                    break
                body.append((ln2, raw2))
            if not any(text.strip() for _, text in body):
                return None, f"раздел «{section}» в {rel_path} пуст"
            return body, None

    if allow_bold_label:
        for idx, (ln, raw) in enumerate(numbered):
            match = _BOLD_LABEL_RE.match(raw.strip())
            if match and _matches_title(match.group(1).rstrip(":"), want):
                body = [(ln, match.group(2).strip())] if match.group(2).strip() else []
                for ln2, raw2 in numbered[idx + 1 :]:
                    st = raw2.strip()
                    if _HEADING_RE.match(st) or _BOLD_LABEL_RE.match(st):
                        break
                    body.append((ln2, raw2))
                if any(text.strip() for _, text in body):
                    return body, None

    return None, f"раздел «{section}» не найден в активной части {rel_path}"


def _check_source(project: pathlib.Path, ledger: Ledger, source_raw: str) -> Optional[str]:
    cleaned = source_raw.strip().strip("`").strip()
    if comms._empty_cell(cleaned):
        return "не указан локатор источника"

    src_match = _SRC_LOC_RE.match(cleaned)
    if src_match:
        src_id, ls, le, sec = src_match.groups()
        obj = ledger.objects.get(src_id)
        if not obj or prefix(src_id) != "SRC":
            return f"источник {src_id} отсутствует в реестре"
        if obj.get("status", "active") != "active":
            return f"источник {src_id} не активен ({obj.get('status')})"
        src_path = str(obj.get("path") or "").strip()
        if src_path and _safe_project_file(project, src_path) is None:
            return f"файл источника {src_id} («{src_path}») не найден в проекте"
        if ls or sec:
            if not src_path:
                return f"у {src_id} не задан path для проверки локатора"
            return _resolve_locator(project, src_path, ls, le, sec, allow_bold_label=True)[1]
        return None

    path_match = _PATH_LOC_RE.match(cleaned)
    if not path_match:
        return "локатор источника должен быть SRC-NNN или <путь>:строка / <путь>#Раздел"
    rel_path, ls, le, sec = path_match.groups()
    if pathlib.PurePosixPath(rel_path.replace("\\", "/")).as_posix() == _MAP_FILE:
        return "карта покрытия не может ссылаться на саму себя как на источник"
    return _resolve_locator(project, rel_path, ls, le, sec, allow_bold_label=True)[1]


def _check_binding(project: pathlib.Path, req_id: str, status: str, binding_raw: str) -> Tuple[Optional[str], Optional[str]]:
    if comms._empty_cell(binding_raw):
        return "brief_coverage.binding_missing", f"{req_id}: не указана привязка к ответу."

    parts = _BINDING_SPLIT_RE.match(binding_raw.strip())
    head = parts.group("head").strip() if parts else binding_raw.strip()
    gap_note = ((parts.group("tail") or parts.group("gap") or "").strip()) if parts else ""

    quote_match = _QUOTE_RE.search(head)
    if quote_match:
        quote = (quote_match.group(1) or quote_match.group(2) or "").strip()
        loc_str = (head[: quote_match.start()] + head[quote_match.end() :]).strip().strip("`").strip()
    else:
        quote, loc_str = None, head.strip().strip("`").strip()

    loc_match = _PATH_LOC_RE.match(loc_str)
    if not loc_match:
        return "brief_coverage.invalid_binding", f"{req_id}: привязка должна указывать <путь>:строка или <путь>#Раздел в outputs/ (получено «{loc_str}»)."

    rel_path, ls, le, sec = loc_match.groups()
    norm_rel = pathlib.PurePosixPath(rel_path.replace("\\", "/")).as_posix()
    path = pathlib.PurePosixPath(norm_rel)
    target = _safe_project_file(project, norm_rel)
    if (path.parent != pathlib.PurePosixPath("outputs") or path.suffix != ".md"
            or path.name == "01_brief.md"
            or (target is not None and target.parent != project.resolve() / "outputs")):
        return "brief_coverage.invalid_target", f"{req_id}: привязка ответа должна указывать на файл в outputs/*.md (кроме outputs/01_brief.md), получено «{rel_path}»."

    target_lines, err = _resolve_locator(project, norm_rel, ls, le, sec, allow_bold_label=False)
    if err or not target_lines:
        return "brief_coverage.target_not_found", f"{req_id}: {err}."

    content_lines = [(ln, text) for ln, text in target_lines if not _HEADING_RE.match(text.strip())]
    if quote is not None:
        norm_quote = re.sub(r"\s+", " ", quote).strip()
        if len(re.sub(r"\s+", "", norm_quote)) < _MIN_QUOTE_CHARS:
            return "brief_coverage.quote_too_short", f"{req_id}: цитата слишком короткая (нужно не менее {_MIN_QUOTE_CHARS} символов без пробелов)."
        joined_body = re.sub(r"\s+", " ", " ".join(text for _, text in content_lines)).strip()
        if norm_quote not in joined_body:
            return "brief_coverage.quote_missing", f"{req_id}: точная цитата «{norm_quote}» не найдена в {loc_str} (ответ удалён, изменён или подставлена чужая секция)."
    else:
        marker_re = re.compile(rf"\[{re.escape(req_id)}\]")
        found_line = any(
            len(re.sub(r"[\s|\-*#\[\]():.,;—–]+", "", _LEDGER_ID_RE.sub("", marker_re.sub("", text)))) >= 5
            for _, text in content_lines
            if marker_re.search(text)
        )
        if not found_line:
            return (
                "brief_coverage.binding_missing",
                f"{req_id}: в {loc_str} не найдена непустая строка ответа с отметкой [{req_id}] "
                "(ответ удалён или подставлена чужая секция; либо укажите точную цитату «…»).",
            )

    if status == "пробел":
        combined = f"{gap_note}\n" + "\n".join(text for _, text in content_lines)
        why, how = _WHY_RE.search(combined), _HOW_RE.search(combined)
        if not (why and not comms._empty_cell(why.group(1)) and how and not comms._empty_cell(how.group(1))):
            return "brief_coverage.gap_incomplete", f"{req_id}: для статуса «пробел» укажите непустые «Почему: …» и «Как узнать: …» в карте или в привязанном разделе."

    return None, None


def check(project: pathlib.Path) -> List[dict]:
    project = pathlib.Path(project)
    raw_map = project / _MAP_FILE
    if not raw_map.exists() and not raw_map.is_symlink():
        return []

    map_path = _safe_project_file(project, _MAP_FILE)
    if map_path is None:
        return [_finding("brief_coverage.invalid_path", "error", f"{_MAP_FILE} должен быть обычным файлом внутри проекта.")]

    try:
        lines = _active_markdown(map_path)
    except (OSError, UnicodeDecodeError) as exc:
        return [_finding("brief_coverage.unreadable_map", "error", f"Не удалось прочитать карту: {exc}.")]
    table, cols = None, None
    for candidate in comms._find_tables(lines):
        headers = candidate[1]
        mapped = {
            "id": validation._column(headers, "id"),
            "required": validation._column(headers, "обязательн"),
            "requirement": validation._column(headers, "требован"),
            "source": validation._column(headers, "источник", "локатор"),
            "status": validation._column(headers, "статус"),
            "binding": validation._column(headers, "привязк", "ответ"),
        }
        if all(idx is not None for idx in mapped.values()):
            table, cols = candidate, mapped
            break

    if table is None or cols is None or not table[2]:
        return [_finding("brief_coverage.invalid_table", "error", "В state/brief_coverage.md нет заполненной таблицы (ID, Обязательно, Требование, Источник, Статус, Привязка к ответу).", line=1)]

    ledger = Ledger(project)
    at_c3 = all((project / rel).is_file() for rel in _C3_FILES)
    findings: List[dict] = []
    seen_ids: set[str] = set()
    all_ids: List[str] = []
    counts = {"закрыто": 0, "пробел": 0, "открыто": 0}

    for row_line, cells in table[2]:
        cell = lambda key: cells[cols[key]].strip() if cols[key] < len(cells) else ""
        req_id = re.sub(r"[`*\[\]]", "", cell("id")).strip()
        if not _ID_RE.fullmatch(req_id):
            findings.append(_finding("brief_coverage.invalid_id", "error", f"Некорректный ID «{cell('id')}»: используйте короткий код без дефиса (например R1, R2).", line=row_line))
            continue
        if req_id in seen_ids:
            findings.append(_finding("brief_coverage.duplicate_id", "error", f"{req_id}: дубликат ID в карте покрытия.", line=row_line, ids=[req_id]))
            continue
        seen_ids.add(req_id)
        all_ids.append(req_id)

        req_flag = rebuild._norm(cell("required"))
        if req_flag not in {"да", "нет"}:
            findings.append(_finding("brief_coverage.invalid_required", "error", f"{req_id}: поле «Обязательно» должно быть «да» или «нет».", line=row_line, ids=[req_id]))
            continue
        required = req_flag == "да"

        req_text = cell("requirement")
        if comms._empty_cell(req_text):
            findings.append(_finding("brief_coverage.empty_requirement", "error", f"{req_id}: пустое описание требования.", line=row_line, ids=[req_id]))

        if src_err := _check_source(project, ledger, cell("source")):
            findings.append(_finding("brief_coverage.invalid_source", "error", f"{req_id}: {src_err}.", line=row_line, ids=[req_id]))

        status = rebuild._norm(cell("status"))
        if status not in counts:
            findings.append(_finding("brief_coverage.invalid_status", "error", f"{req_id}: недопустимый статус «{cell('status')}» (допустимы: открыто, закрыто, пробел).", line=row_line, ids=[req_id]))
            continue
        counts[status] += 1

        if status == "открыто":
            if required and at_c3:
                findings.append(_finding("brief_coverage.open_required", "error", f"{req_id}: обязательное требование осталось в статусе «открыто» на этапе C3.", line=row_line, ids=[req_id]))
            else:
                code = "brief_coverage.open_optional" if not required else "brief_coverage.open_before_c3"
                note = "необязательный пункт открыт" if not required else "пункт открыт (до этапа C3)"
                findings.append(_finding(code, "warn", f"{req_id}: {note} — «{req_text}».", line=row_line, ids=[req_id]))
            continue

        err_code, err_msg = _check_binding(project, req_id, status, cell("binding"))
        if err_code and err_msg:
            findings.append(_finding(err_code, "error", err_msg, line=row_line, ids=[req_id]))
        elif status == "пробел":
            findings.append(_finding("brief_coverage.gap", "warn", f"{req_id}: зафиксирован обоснованный пробел («{req_text}»). Соответствие поручению проверяется на C3.", line=row_line, ids=[req_id]))

    if not any(item["status"] == "error" for item in findings):
        findings.append(
            _finding(
                "brief_coverage.ok",
                "ok",
                f"Структурная привязка карты покрытия подтверждена (закрыто: {counts['закрыто']}, пробелы: {counts['пробел']}, открыто: {counts['открыто']}). "
                "T0 проверяет только наличие отметки ID или точной цитаты в указанном разделе; "
                "смысл и соответствие поручению проверяют человек и skills/claim-verifier на C3.",
                line=table[0],
                ids=all_ids,
            )
        )
    return findings


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Структурная T0-проверка карты покрытия сложного брифа.")
    parser.add_argument("project", type=pathlib.Path, help="Путь к папке проекта")
    parser.add_argument("--json", action="store_true", help="Вывести результат в JSON")
    args = parser.parse_args(argv)

    findings = check(args.project)
    if args.json:
        print(json.dumps(findings, ensure_ascii=False, indent=2))
    else:
        for item in findings:
            loc = f"{item['file']}:{item['line']}" if item["line"] else item["file"]
            print(f"[{item['status']}] {item['code']} ({loc}): {item['message']}")
    return 1 if any(item["status"] == "error" for item in findings) else 0


if __name__ == "__main__":
    raise SystemExit(main())

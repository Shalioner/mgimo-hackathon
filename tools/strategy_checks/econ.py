#!/usr/bin/env python3
"""M08: Проверка экономической модели в 05_product_strategy.md (и 03_situation.md).

Принцип совместимости со старыми документами:
- Если в 05_product_strategy.md нет нового раздела «## Экономическая модель»
  и нет v2-таблицы драйверов (low / base / high), возвращается `not_assessed`
  (без ложной ошибки на исторических проектах).
- Когда раздел/таблица v2 присутствуют, проверяются: >= 3 типизированных
  драйвера с диапазонами low/base/high и ссылкой/основанием, формула,
  точка безубыточности, топ-3 tornado, условие «решение меняется, если ...»,
  а для монетизируемых (платных) вариантов — «Каннибализация» с формулой
  и «Ценовые якоря» (>= 2 датированные цены конкурентов в одной единице).
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys
from typing import Dict, List, Optional, Tuple

import yaml

_TOOLS_DIR = pathlib.Path(__file__).resolve().parent.parent
if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))

_HISTORY_RE = re.compile(
    r"^##\s+(?:Исторический\s+снимок|История\s+до|Архив\s+до)\b",
    re.IGNORECASE,
)
_H2_RE = re.compile(r"^##\s+(.+?)\s*$")
_ECON_H2_RE = re.compile(r"^##\s+Экономическая\s+модель\b", re.IGNORECASE)
_OPP_H2_RE = re.compile(r"^##\s+Размер\s+возможности\b", re.IGNORECASE)
_REF_ID_RE = re.compile(r"\b(?:SRC|CLM|HYP|PAIN|ALT|DEC|PRM|EXP|CHG)-\d{3}\b")
_SEP_ROW_RE = re.compile(r"^\s*\|(?:\s*:?-+:?\s*\|)+\s*$")
_NUM_RE = re.compile(r"[-+]?\d+(?:[.,]\d+)?")
_DATE_MARKER_RE = re.compile(r"(?:\b20\d{2}\b|\b\d{2}\.\d{2}\.20\d{2}\b|\b\d{4}-\d{2}-\d{2}\b|\b\d{2}\.20\d{2}\b)")
_PRICE_UNIT_RE = re.compile(
    r"(\d+(?:[.,]\d+)?)\s*(₽|руб\.?|р\.?|RUB|\$|USD|EUR|€)"
    r"(?:\s*(?:/|в|за)\s*([A-Za-zА-Яа-яЁё0-9]+))?",
    re.IGNORECASE,
)


def _strip_html_comments(numbered_lines: List[Tuple[int, str]]) -> List[Tuple[int, str]]:
    """Удаляет HTML-комментарии <!-- ... -->, сохраняя нумерацию строк."""
    cleaned: List[Tuple[int, str]] = []
    in_comment = False
    for ln, raw in numbered_lines:
        cur = raw
        out_parts: List[str] = []
        while cur:
            if in_comment:
                end_idx = cur.find("-->")
                if end_idx == -1:
                    cur = ""
                else:
                    in_comment = False
                    cur = cur[end_idx + 3:]
            else:
                start_idx = cur.find("<!--")
                if start_idx == -1:
                    out_parts.append(cur)
                    cur = ""
                else:
                    out_parts.append(cur[:start_idx])
                    rest = cur[start_idx + 4:]
                    end_idx = rest.find("-->")
                    if end_idx == -1:
                        in_comment = True
                        cur = ""
                    else:
                        cur = rest[end_idx + 3:]
        cleaned.append((ln, " ".join(part for part in out_parts if part).strip() if len(out_parts) > 1 else (out_parts[0] if out_parts else "")))
    return cleaned


def _is_placeholder(text: str) -> bool:
    s = text.strip()
    if not s or s in {"—", "-", "–", "нет", "N/A", "?", "..."}:
        return True
    low = s.lower()
    if "…" in s or "yyyy-mm-dd" in low or "[clm-…]" in low or "[hyp-…]" in low:
        return True
    return False


def _norm_currency_unit(raw_currency: str, raw_unit: Optional[str]) -> Tuple[str, str]:
    c = raw_currency.strip().lower().rstrip(".")
    if c in {"₽", "руб", "р", "rub"}:
        cur = "RUB"
    elif c in {"$", "usd"}:
        cur = "USD"
    elif c in {"€", "eur"}:
        cur = "EUR"
    else:
        cur = c.upper()

    u = (raw_unit or "").strip().lower().rstrip(".")
    if not u:
        return cur, ""
    if u in {"мес", "месяц", "месяца", "месяцев", "mo", "month"} or u.startswith("мес"):
        return cur, "/мес"
    if u in {"год", "года", "лет", "yr", "year"} or u.startswith("год"):
        return cur, "/год"
    if u in {"нед", "неделя", "недели", "недель", "wk", "week"} or u.startswith("нед"):
        return cur, "/нед"
    if u in {"дн", "день", "дня", "дней", "сут", "сутки", "day"}:
        return cur, "/день"
    if u.startswith("поездк") or u == "trip":
        return cur, "/поездку"
    return cur, f"/{u}"


def _extract_anchor_block(sec_body: List[Tuple[int, str]]) -> List[str]:
    """Собирает блок ценовых якорей вместе со вложенными пунктами списка и таблицами."""
    block: List[str] = []
    i = 0
    while i < len(sec_body):
        _, raw = sec_body[i]
        low = raw.lower()
        if "ценовые якоря" in low or "якоря" in low:
            block.append(raw)
            j = i + 1
            while j < len(sec_body):
                _, nxt = sec_body[j]
                nxt_strip = nxt.strip()
                if not nxt_strip:
                    # Продолжаем через пустую строку только если дальше идёт вложенный элемент или таблица
                    if j + 1 < len(sec_body):
                        peek = sec_body[j + 1][1]
                        if peek.startswith((" ", "\t")) or re.match(r"^\s*(?:\d+\.|[|])", peek):
                            j += 1
                            continue
                    break
                if nxt_strip.startswith("#"):
                    break
                if nxt.startswith((" ", "\t")) or re.match(r"^\s*\d+\.\s+", nxt) or nxt_strip.startswith("|"):
                    block.append(nxt)
                    j += 1
                else:
                    break
            i = j
        else:
            i += 1
    return block


def _parse_anchor_entries(anchor_lines: List[str]) -> Tuple[List[Tuple[str, Tuple[str, str]]], int]:
    """Извлекает датированные ценовые якоря конкурентов и их нормализованные единицы измерения."""
    entries: List[Tuple[str, Tuple[str, str]]] = []
    undated_or_incomplete = 0
    segments: List[str] = []
    for raw in anchor_lines:
        if _SEP_ROW_RE.match(raw):
            continue
        if ";" in raw:
            segments.extend(part.strip() for part in raw.split(";") if part.strip())
        else:
            segments.append(raw.strip())

    for seg in segments:
        low = seg.lower()
        # Пропускаем чисто заголовочную строку без цены
        if ("ценовые якоря" in low or "якоря" in low) and not _PRICE_UNIT_RE.search(seg):
            continue
        if "yyyy-mm-dd" in low or "[clm-…" in low or "[hyp-…" in low:
            undated_or_incomplete += 1
            continue
        # Строки с нераскрытой ценой вида "… ₽/мес"
        if re.search(r"…\s*(?:₽|руб|RUB|\$|USD|EUR|€)", seg, re.IGNORECASE):
            undated_or_incomplete += 1
            continue
        prices = list(_PRICE_UNIT_RE.finditer(seg))
        dates = _DATE_MARKER_RE.findall(seg)
        if not prices and not dates:
            continue
        if len(prices) == 1 and len(dates) >= 1:
            pm = prices[0]
            entries.append((dates[0], _norm_currency_unit(pm.group(2), pm.group(3))))
        elif len(prices) >= 2 and len(dates) >= 2:
            for pm, dt in zip(prices, dates):
                entries.append((dt, _norm_currency_unit(pm.group(2), pm.group(3))))
        else:
            undated_or_incomplete += 1

    return entries, undated_or_incomplete


def _split_row(line: str) -> List[str]:
    stripped = line.strip()
    if stripped.startswith("|"):
        stripped = stripped[1:]
    if stripped.endswith("|"):
        stripped = stripped[:-1]
    return [c.strip() for c in stripped.split("|")]


def _extract_section(lines: List[str], header_re: re.Pattern[str]) -> Tuple[Optional[int], List[Tuple[int, str]]]:
    start_line: Optional[int] = None
    body: List[Tuple[int, str]] = []
    for idx, raw in enumerate(lines, start=1):
        stripped = raw.strip()
        if _HISTORY_RE.match(stripped):
            break
        if start_line is None:
            if header_re.match(stripped):
                start_line = idx
            continue
        if _H2_RE.match(stripped):
            break
        body.append((idx, raw))
    return start_line, body


def _find_driver_tables(numbered_lines: List[Tuple[int, str]]) -> List[Tuple[int, List[str], List[Tuple[int, List[str]]]]]:
    tables: List[Tuple[int, List[str], List[Tuple[int, List[str]]]]] = []
    i = 0
    while i < len(numbered_lines) - 1:
        ln, raw = numbered_lines[i]
        nxt_raw = numbered_lines[i + 1][1]
        if raw.strip().startswith("|") and _SEP_ROW_RE.match(nxt_raw):
            headers = _split_row(raw)
            rows: List[Tuple[int, List[str]]] = []
            j = i + 2
            while j < len(numbered_lines) and numbered_lines[j][1].strip().startswith("|"):
                row_ln, row_raw = numbered_lines[j]
                if not _SEP_ROW_RE.match(row_raw):
                    rows.append((row_ln, _split_row(row_raw)))
                j += 1
            tables.append((ln, headers, rows))
            i = j
        else:
            i += 1
    return tables


def _is_driver_table(headers: List[str]) -> bool:
    joined = " ".join(h.lower() for h in headers)
    has_low = any(k in joined for k in ("low", "мин", "пессим"))
    has_base = any(k in joined for k in ("base", "баз"))
    has_high = any(k in joined for k in ("high", "макс", "оптим"))
    has_bounds = (has_low and has_base and has_high) or ("диапазон" in joined and "баз" in joined)
    has_type = ("тип" in joined or "type" in joined)
    has_driver = any(k in joined for k in ("драйвер", "id", "множител", "переменн", "показател"))
    return (has_bounds and has_driver) or (has_bounds and has_type)


def _is_paid_alt(project: pathlib.Path, active_05_text: str) -> bool:
    decisions_path = project / "state" / "ledger" / "decisions.yaml"
    alts_path = project / "state" / "ledger" / "alternatives.yaml"

    target_texts: List[str] = []
    if decisions_path.is_file() and alts_path.is_file():
        try:
            decs = yaml.safe_load(decisions_path.read_text(encoding="utf-8")) or []
            alts = yaml.safe_load(alts_path.read_text(encoding="utf-8")) or []
            alt_by_id = {a.get("id"): a for a in alts if isinstance(a, dict) and a.get("id")}
            for dec in decs:
                if not isinstance(dec, dict):
                    continue
                chosen_id = dec.get("chosen") or dec.get("recommended")
                alt = alt_by_id.get(chosen_id)
                if alt:
                    target_texts.append(str(alt.get("title", "")))
                    target_texts.append(str(alt.get("description", "")))
                    target_texts.append(str(alt.get("costs_revenue", "")))
        except Exception:
            pass

    if not target_texts:
        target_texts.append(active_05_text)

    combined = "\n".join(target_texts).lower()
    # Explicitly free / zero monetary revenue cues without price > 0
    if "бесплатн" in combined and not re.search(r"[1-9]\d*\s*(?:₽|руб|\$|eur)", combined):
        return False
    paid_re = re.compile(
        r"(?:подписк|монетизац|тариф|выручк|марж|оплат|trial|чек|[1-9]\d*\s*(?:₽|руб|\$|eur))",
        re.IGNORECASE,
    )
    return bool(paid_re.search(combined))


def _check_05(project: pathlib.Path) -> List[dict]:
    rel = "outputs/05_product_strategy.md"
    path = project / rel
    if not path.is_file():
        return [
            {
                "code": "econ.not_assessed",
                "status": "not_assessed",
                "message": f"Файл {rel} отсутствует — экономическая модель не проверялась.",
                "file": rel,
                "line": 0,
                "ids": [],
            }
        ]

    lines = path.read_text(encoding="utf-8").splitlines()
    raw_active_lines: List[Tuple[int, str]] = []
    for idx, raw in enumerate(lines, start=1):
        if _HISTORY_RE.match(raw.strip()):
            break
        raw_active_lines.append((idx, raw))

    active_lines = _strip_html_comments(raw_active_lines)
    active_text = "\n".join(raw for _, raw in active_lines)

    sec_line, raw_sec_body = _extract_section(lines, _ECON_H2_RE)
    sec_body = _strip_html_comments(raw_sec_body)
    all_tables = _find_driver_tables(active_lines)
    driver_tables = [t for t in all_tables if _is_driver_table(t[1])]

    if sec_line is None and not driver_tables:
        return [
            {
                "code": "econ.not_assessed",
                "status": "not_assessed",
                "message": (
                    f"В {rel} отсутствует раздел «## Экономическая модель» и v2-таблица драйверов "
                    "(старый формат документа — проверка пропущена со статусом not_assessed)."
                ),
                "file": rel,
                "line": 0,
                "ids": [],
            }
        ]

    findings: List[dict] = []
    section_text = "\n".join(raw for _, raw in sec_body) if sec_body else active_text
    section_line = sec_line or driver_tables[0][0]

    # Locate driver table inside section (or fallback to discovered driver_table)
    sec_tables = [t for t in _find_driver_tables(sec_body) if _is_driver_table(t[1])] if sec_body else []
    target_table = sec_tables[0] if sec_tables else (driver_tables[0] if driver_tables else None)

    valid_driver_count = 0
    driver_ids: List[str] = []

    if target_table is None:
        findings.append(
            {
                "code": "econ.missing_driver_table",
                "status": "error",
                "message": "В разделе «Экономическая модель» отсутствует таблица драйверов (ID, low/base/high, тип, ссылка).",
                "file": rel,
                "line": section_line,
                "ids": [],
            }
        )
    else:
        tline, headers, rows = target_table
        h_lower = [h.lower() for h in headers]

        def _col_idx(*keywords: str) -> Optional[int]:
            for idx, h in enumerate(h_lower):
                if any(k in h for k in keywords):
                    return idx
            return None

        id_col = _col_idx("id", "драйвер", "показатель", "множител")
        low_col = _col_idx("low", "мин", "пессим")
        base_col = _col_idx("base", "баз")
        high_col = _col_idx("high", "макс", "оптим")
        range_col = _col_idx("диапазон")
        type_col = _col_idx("тип", "type")
        ref_col = _col_idx("ссылк", "основан", "ref", "источник")

        for rline, cells in rows:
            if not any(c.strip(" —-") for c in cells):
                continue
            did_cell = cells[id_col] if id_col is not None and id_col < len(cells) else cells[0]
            type_cell = cells[type_col].lower() if type_col is not None and type_col < len(cells) else ""
            ref_cell = cells[ref_col] if ref_col is not None and ref_col < len(cells) else " ".join(cells)

            # Check numeric bounds
            has_bounds = False
            if low_col is not None and base_col is not None and high_col is not None:
                if max(low_col, base_col, high_col) < len(cells):
                    has_bounds = all(
                        bool(_NUM_RE.search(cells[c])) for c in (low_col, base_col, high_col)
                    )
            elif range_col is not None and range_col < len(cells):
                has_bounds = len(_NUM_RE.findall(cells[range_col])) >= 3

            # Reject unfilled slash placeholder "fact / assumption"
            is_unfilled_type_choice = "/" in type_cell and "fact" in type_cell and "assumption" in type_cell
            valid_type = (not is_unfilled_type_choice) and any(
                t in type_cell for t in ("fact", "assumption", "hypothesis")
            )
            has_ref = bool(_REF_ID_RE.search(ref_cell)) or (
                valid_type and "assumption" in type_cell and not _is_placeholder(ref_cell)
            )

            found_ids = _REF_ID_RE.findall(" ".join(cells))
            driver_ids.extend(found_ids)

            if did_cell and valid_type and has_bounds and has_ref:
                valid_driver_count += 1
            else:
                missing_parts = []
                if not has_bounds:
                    missing_parts.append("low/base/high")
                if not valid_type:
                    missing_parts.append("тип (fact/assumption/hypothesis)")
                if not has_ref:
                    missing_parts.append("ссылка/основание")
                findings.append(
                    {
                        "code": "econ.invalid_driver_row",
                        "status": "error",
                        "message": f"Строка драйвера «{did_cell}» заполнена неполно: не хватает {', '.join(missing_parts)}.",
                        "file": rel,
                        "line": rline,
                        "ids": found_ids,
                    }
                )

        if valid_driver_count < 3:
            findings.append(
                {
                    "code": "econ.insufficient_drivers",
                    "status": "error",
                    "message": (
                        f"В экономической модели найдено {valid_driver_count} корректных типизированных драйверов; "
                        "требуется минимум 3."
                    ),
                    "file": rel,
                    "line": tline,
                    "ids": [],
                }
            )

    # Structural requirements in section (ignoring HTML comments & rejecting template stubs)
    lower_sec = section_text.lower()
    formula_lines = [raw for _, raw in sec_body if "формул" in raw.lower() or re.search(r"[a-zа-яё0-9_]+\s*\*\s*[a-zа-яё0-9_]+", raw.lower())]
    has_real_formula = any(
        not ("выручка_или_эффект - затраты" in raw and "например:" in raw)
        for raw in formula_lines
    )
    if not formula_lines or not has_real_formula:
        findings.append(
            {
                "code": "econ.missing_formula",
                "status": "error",
                "message": "В экономической модели не указана заполненная формула драйверов.",
                "file": rel,
                "line": section_line,
                "ids": [],
            }
        )

    be_lines = [raw for _, raw in sec_body if "безубыточност" in raw.lower()]
    has_real_be = any(
        not _is_placeholder(re.sub(r"^.*?безубыточност[а-яё]*\*{0,2}\s*[:—\-]*", "", raw, flags=re.IGNORECASE))
        for raw in be_lines
    )
    if not be_lines or not has_real_be:
        findings.append(
            {
                "code": "econ.missing_breakeven",
                "status": "error",
                "message": "В экономической модели не указана заполненная точка безубыточности.",
                "file": rel,
                "line": section_line,
                "ids": [],
            }
        )

    torn_lines = [raw for _, raw in sec_body if re.search(r"(?:tornado|торнадо|топ[\s\-]?3)", raw, re.IGNORECASE)]
    has_real_tornado = any(
        not _is_placeholder(re.sub(r"^.*?(?:tornado|торнадо|топ[\s\-]?3[^:]*)\*{0,2}\s*[:—\-]*", "", raw, flags=re.IGNORECASE))
        for raw in torn_lines
    )
    if not torn_lines or not has_real_tornado:
        findings.append(
            {
                "code": "econ.missing_tornado",
                "status": "error",
                "message": "В экономической модели не указан заполненный топ-3 драйверов по влиянию (tornado).",
                "file": rel,
                "line": section_line,
                "ids": [],
            }
        )

    thresh_lines = [raw for _, raw in sec_body if "решение меняется, если" in raw.lower()]
    has_real_thresh = any(
        not _is_placeholder(re.sub(r"^.*?решение\s+меняется,\s*если\*{0,2}\s*[:—\-]*", "", raw, flags=re.IGNORECASE))
        for raw in thresh_lines
    )
    if not thresh_lines or not has_real_thresh:
        findings.append(
            {
                "code": "econ.missing_threshold",
                "status": "error",
                "message": "В экономической модели отсутствует заполненное условие «решение меняется, если ...».",
                "file": rel,
                "line": section_line,
                "ids": [],
            }
        )

    # Paid ALT checks: cannibalization + >=2 dated price anchors in the same unit
    if _is_paid_alt(project, active_text):
        cannib_lines = [raw for _, raw in sec_body if "каннибализац" in raw.lower()]
        if not cannib_lines:
            findings.append(
                {
                    "code": "econ.missing_cannibalization",
                    "status": "error",
                    "message": "Для платного варианта отсутствует строка/блок «Каннибализация» с формулой.",
                    "file": rel,
                    "line": section_line,
                    "ids": [],
                }
            )
        else:
            cannib_text = " ".join(cannib_lines).lower()
            cannib_body = re.sub(r"^.*?каннибализац[а-яё]*\*{0,2}\s*(?:\([^)]*\))?\s*\*{0,2}\s*[:—\-]*", "", cannib_text)
            has_formula = not _is_placeholder(cannib_body) and bool(
                re.search(r"[×*]", cannib_body)
                or ("доля" in cannib_body and ("разниц" in cannib_body or "выруч" in cannib_body))
                or ("инкрементальн" in cannib_body)
                or ("не применимо" in cannib_body and len(cannib_body) > 25)
            )
            if not has_formula:
                findings.append(
                    {
                        "code": "econ.invalid_cannibalization",
                        "status": "error",
                        "message": (
                            "В «Каннибализации» отсутствует заполненная формула "
                            "(доля переключившихся × разница выручки против инкрементальной выручки)."
                        ),
                        "file": rel,
                        "line": section_line,
                        "ids": [],
                    }
                )

        anchor_lines = _extract_anchor_block(sec_body)
        if not anchor_lines:
            findings.append(
                {
                    "code": "econ.missing_price_anchors",
                    "status": "error",
                    "message": "Для платного варианта отсутствуют «Ценовые якоря» (>= 2 датированные цены конкурентов).",
                    "file": rel,
                    "line": section_line,
                    "ids": [],
                }
            )
        else:
            entries, _ = _parse_anchor_entries(anchor_lines)
            units = {unit for _, unit in entries}
            if len(entries) < 2 or len(units) != 1:
                unit_desc = ", ".join(f"{cur}{u}" for cur, u in sorted(units)) if units else "не найдены"
                findings.append(
                    {
                        "code": "econ.insufficient_price_anchors",
                        "status": "error",
                        "message": (
                            f"В «Ценовых якорях» найдено валидных датированных цен: {len(entries)} "
                            f"(единицы измерения: {unit_desc}); "
                            "нужно минимум 2 датированные цены конкурентов в одной единице."
                        ),
                        "file": rel,
                        "line": section_line,
                        "ids": [],
                    }
                )

    if not findings:
        findings.append(
            {
                "code": "econ.ok",
                "status": "ok",
                "message": f"{rel}: экономическая модель заполнена корректно ({valid_driver_count} драйверов).",
                "file": rel,
                "line": section_line,
                "ids": sorted(set(driver_ids)),
            }
        )

    return findings


def _check_03(project: pathlib.Path) -> List[dict]:
    rel = "outputs/03_situation.md"
    path = project / rel
    if not path.is_file():
        return []
    lines = path.read_text(encoding="utf-8").splitlines()
    sec_line, raw_sec_body = _extract_section(lines, _OPP_H2_RE)
    if sec_line is None:
        return []

    sec_body = _strip_html_comments(raw_sec_body)
    tables = _find_driver_tables(sec_body)
    multiplier_count = 0
    ids: List[str] = []
    if tables:
        for _, rows in [(t[1], t[2]) for t in tables]:
            for _, cells in rows:
                joined = " ".join(cells)
                low = joined.lower()
                if "…" in joined or ("fact" in low and "assumption" in low and "/" in low):
                    continue
                if _is_placeholder(cells[0] if cells else ""):
                    continue
                stripped_ids = _REF_ID_RE.sub("", joined)
                has_num = bool(_NUM_RE.search(stripped_ids))
                has_id = bool(_REF_ID_RE.search(joined))
                has_substantive_assump = bool(
                    re.search(r"\b(?:assumption|допущение)\b", low)
                    and len(re.sub(r"\b(?:assumption|допущение)\b|\W+", "", low)) >= 6
                )
                if has_num and (has_id or has_substantive_assump):
                    multiplier_count += 1
                    ids.extend(_REF_ID_RE.findall(joined))
    else:
        for _, raw in sec_body:
            st = raw.strip()
            if not st or "формула снизу вверх" in st.lower():
                continue
            low = st.lower()
            if (
                "…" in st
                or "..." in st
                or re.search(r"\[(?:clm|hyp|id)[^\]]*(?:…|\.\.\.|nnn|xxx)\]", low)
                or ("fact" in low and "assumption" in low and "/" in low)
            ):
                continue
            # Убираем префикс списка и номер множителя ("- Множитель 1 (...):"), а также ID перед поиском числа
            body_wo_prefix = re.sub(r"^\s*[-*]\s*(?:множитель\s*\d+\s*)?", "", st, flags=re.IGNORECASE)
            body_wo_ids = _REF_ID_RE.sub("", body_wo_prefix)
            has_num = bool(_NUM_RE.search(body_wo_ids))
            has_id = bool(_REF_ID_RE.search(st))
            has_substantive_assump = bool(
                re.search(r"\b(?:assumption|допущение)\b", low)
                and len(re.sub(r"\b(?:assumption|допущение|множитель)\b|\W+", "", body_wo_ids.lower())) >= 6
            )
            if has_num and (has_id or has_substantive_assump):
                multiplier_count += 1
                ids.extend(_REF_ID_RE.findall(st))

    if multiplier_count < 3:
        return [
            {
                "code": "econ.opportunity_size_incomplete",
                "status": "error",
                "message": (
                    f"В {rel} раздел «Размер возможности» содержит {multiplier_count} заполненных множителей "
                    "(с числом и CLM или обоснованным assumption); требуется минимум 3 снизу вверх."
                ),
                "file": rel,
                "line": sec_line,
                "ids": sorted(set(ids)),
            }
        ]
    return [
        {
            "code": "econ.opportunity_size_ok",
            "status": "ok",
            "message": f"{rel}: размер возможности снизу вверх содержит {multiplier_count} множителей.",
            "file": rel,
            "line": sec_line,
            "ids": sorted(set(ids)),
        }
    ]


def check(project: pathlib.Path) -> List[dict]:
    project = pathlib.Path(project)
    results = _check_05(project)
    results.extend(_check_03(project))
    return results


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="M08: Проверка экономической модели стратегии.")
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

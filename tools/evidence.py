#!/usr/bin/env python3
"""Линт доказательности (M12): прогноз как факт, числа вне цитаты, фильтрованный trace и «Что добрать».

API:
    check(project: Path) -> list[dict]
    Поля: code, status (ok|warn|error|not_assessed), message, file, line, ids.

CLI:
    python tools/evidence.py projects/<case> [--json]
"""
from __future__ import annotations

import argparse
from collections import deque
from decimal import Decimal, InvalidOperation
import json
from pathlib import Path
import re
import sys
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ledger as L  # noqa: E402
import next as N  # noqa: E402

FORECAST_MARKERS_RE = re.compile(
    r"(?:\b(?:достигн[еу]т|выраст[еу]т|состав[ия]т|стан[еу]т|прогноз\w*"
    r"|ожида\w*|оценива\w*|оценк[аеиу]|счита[ею]т|полага[ею]т"
    r"|вероятн\w*|предполага\w*"
    r"|will|expected|forecast\w*|projected?|estimate[ds]?|predict\w*)\b"
    r"|\bпо\s+(?:оценк\w*|мнению)\b"
    r"|\bскорее\s+всего\b)",
    re.I,
)

ID_NOISE_RE = re.compile(
    r"\b(?:SRC|CLM|HYP|PAIN|ALT|DEC|PRM|EXP|CHG)-\d{3}\b"
    r"|\b(?:C[1-3]|T[0-4]|G\d+|F\d+)\b"
    r"|\bv\d+(?:\.\d+)*\b",
    re.I,
)

LOCATOR_NOISE_RE = re.compile(
    r"(?:\bстрок[аеиу]?\s*\d+(?:\s*[–—-]\s*\d+)?|\bс\.\s*\d+(?:\s*[–—-]\s*\d+)?|\bp\.\s*\d+(?:\s*[–—-]\s*\d+)?)",
    re.I,
)

INFLECTED_NUM_RE = re.compile(r"(\d)-(?:й|я|е|м|му|го|х|мя)\b", re.I)

ISO_DATE_RE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
DOT_DATE_RE = re.compile(r"\b(\d{2})\.(\d{2})\.(\d{4})\b")

RU_MONTHS = {
    "январ": 1, "феврал": 2, "март": 3, "апрел": 4, "ма": 5, "июн": 6,
    "июл": 7, "август": 8, "сентябр": 9, "октябр": 10, "ноябр": 11, "декабр": 12,
}
RU_MONTH_YEAR_RE = re.compile(
    r"\b(январ\w*|феврал\w*|март\w*|апрел\w*|ма[йяею]\w*|июн\w*|июл\w*|август\w*|сентябр\w*|октябр\w*|ноябр\w*|декабр\w*)\s+(\d{4})(?:\s*г(?:ода?)?)?\b",
    re.I,
)

SCALE_WORDS = {
    "тыс": 1_000,
    "тысяч": 1_000,
    "тысяча": 1_000,
    "тысячи": 1_000,
    "thousand": 1_000,
    "k": 1_000,
    "млн": 1_000_000,
    "миллион": 1_000_000,
    "миллиона": 1_000_000,
    "миллионов": 1_000_000,
    "million": 1_000_000,
    "millions": 1_000_000,
    "mn": 1_000_000,
    "млрд": 1_000_000_000,
    "миллиард": 1_000_000_000,
    "миллиарда": 1_000_000_000,
    "миллиардов": 1_000_000_000,
    "billion": 1_000_000_000,
    "billions": 1_000_000_000,
    "bn": 1_000_000_000,
    "трлн": 1_000_000_000_000,
    "триллион": 1_000_000_000_000,
    "trillion": 1_000_000_000_000,
    "tn": 1_000_000_000_000,
}

WORD_NUMS = {
    "ноль": "0", "нуль": "0", "zero": "0",
    "один": "1", "одна": "1", "одно": "1", "one": "1",
    "два": "2", "две": "2", "two": "2",
    "три": "3", "three": "3",
    "четыре": "4", "four": "4",
    "пять": "5", "five": "5",
    "шесть": "6", "six": "6",
    "семь": "7", "seven": "7",
    "восемь": "8", "eight": "8",
    "девять": "9", "nine": "9",
    "десять": "10", "ten": "10",
}

_SCALE_PAT = (
    r"(тыс\.?|тысяч[аи]?|thousand|млн|миллион\w*|millions?"
    r"|млрд|миллиард\w*|billions?|трлн|триллион\w*|trillion|k|mn|bn|tn)\b"
)
_NUM_ATOM_PAT = (
    r"(?:[1-9]\d{0,2}(?:[ \u00a0]\d{3})+(?:[.,]\d+)?(?!\d)"
    r"|[1-9]\d{0,2}(?:,\d{3})+(?:\.\d+)?(?!\d)"
    r"|\d+(?:[.,]\d+)?)"
)
NUM_WITH_SCALE_RE = re.compile(
    rf"(?<!\d[ \u00a0])(?<![A-Za-zА-Яа-яЁё0-9.,])"
    rf"({_NUM_ATOM_PAT})(?:\s*{_SCALE_PAT})?",
    re.I,
)
RANGE_RE = re.compile(
    rf"(?<!\d[ \u00a0])(?<![A-Za-zА-Яа-яЁё0-9.,])"
    rf"(?:[$€£₽]\s*)?({_NUM_ATOM_PAT})(?:\s*{_SCALE_PAT})?"
    rf"\s*[–—-]\s*(?:[$€£₽]\s*)?"
    rf"({_NUM_ATOM_PAT})(?:\s*{_SCALE_PAT})?",
    re.I,
)
VERBAL_RANGE_RE = re.compile(
    rf"(?<!\d[ \u00a0])(?<![A-Za-zА-Яа-яЁё0-9.,])"
    rf"(?:от|с|from|between)\s+(?:[$€£₽]\s*)?"
    rf"({_NUM_ATOM_PAT})(?:\s*{_SCALE_PAT})?"
    rf"\s+(?:до|по|to|and)\s+(?:[$€£₽]\s*)?"
    rf"({_NUM_ATOM_PAT})(?:\s*{_SCALE_PAT})?",
    re.I,
)

RATE_UNIT_RE = re.compile(
    r"(?P<points>\bп\.[^\S\r\n]*п\b\.?|"
    r"\bпроцентн(?:ый|ого|ому|ым|ом|ые|ых|ыми)[^\S\r\n]+"
    r"пункт(?:а|у|ом|е|ы|ов|ам|ами|ах)?\b)"
    r"|(?P<other>\bб\.[^\S\r\n]*п\b\.?|\b(?:bps?|basis points?)\b|"
    r"\bбазисн\w*[^\S\r\n]+пункт\w*\b|‰)"
    r"|%(?!\w)|\bпроцент(?:а|у|ом|е|ы|ов|ам|ами|ах)?\b",
    re.I,
)
RATE_INLINE_MARKUP_RE = re.compile(
    r"(`+)[^\r\n]+?\1|\[[^\]\r\n]*\]\((?:<[^>\r\n]*>|[^()\s]*)\)"
    r"|<?https?://[^\s<>()[\]]+>?", re.I,
)
RATE_BLOCK_PREFIX_RE = re.compile(r"^(?:[ \t]{0,3}(?:#{1,6}|>|\d+[.)]|[-+*])[ \t]+)+")
RATE_NUMBER_RE = re.compile(r"(?<![\w.,])(\d+(?:[.,]\d+)?)[^\S\r\n]*\Z")


def _finding(code: str, status: str, message: str, file: str, line: int, ids: list[str]) -> dict:
    return {
        "code": code,
        "status": status,
        "message": message,
        "file": file,
        "line": int(line),
        "ids": list(ids),
    }


def _yaml_id_lines(ledger_dir: Path) -> dict[str, tuple[str, int]]:
    mapping: dict[str, tuple[str, int]] = {}
    rx = re.compile(r"^\s*-\s*id:\s*[\"']?([A-Z]+-\d{3})[\"']?\b")
    for pfx, (fname, _, _) in L.TYPES.items():
        path = ledger_dir / fname
        if not path.exists():
            continue
        rel = f"state/ledger/{fname}"
        for idx, ln in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            m = rx.match(ln)
            if m:
                mapping.setdefault(m.group(1), (rel, idx))
    return mapping


def _norm_dec(s: str) -> str:
    try:
        d = Decimal(s).normalize()
        txt = format(d, "f")
        if "." in txt:
            txt = txt.rstrip("0").rstrip(".")
        return txt or "0"
    except (InvalidOperation, ValueError):
        return s


def _scale_mult(unit: str | None) -> int:
    if not unit:
        return 1
    key = unit.lower().rstrip(".")
    if key in SCALE_WORDS:
        return SCALE_WORDS[key]
    for prefix_k, mult in SCALE_WORDS.items():
        if key.startswith(prefix_k):
            return mult
    return 1


def _extract_dates(text: str) -> tuple[set[tuple[int, int, int]], set[tuple[int, int]]]:
    exact_dates: set[tuple[int, int, int]] = set()
    month_years: set[tuple[int, int]] = set()
    for m in ISO_DATE_RE.finditer(text):
        exact_dates.add((int(m.group(1)), int(m.group(2)), int(m.group(3))))
        month_years.add((int(m.group(1)), int(m.group(2))))
    for m in DOT_DATE_RE.finditer(text):
        exact_dates.add((int(m.group(3)), int(m.group(2)), int(m.group(1))))
        month_years.add((int(m.group(3)), int(m.group(2))))
    for m in RU_MONTH_YEAR_RE.finditer(text):
        m_word = m.group(1).lower()
        year = int(m.group(2))
        for stem, num in RU_MONTHS.items():
            if m_word.startswith(stem):
                month_years.add((year, num))
                break
    return exact_dates, month_years


def _replace_known_dates(text: str, allowed_exact: set[tuple[int, int, int]], allowed_ym: set[tuple[int, int]]) -> str:
    def sub_iso(m: re.Match) -> str:
        d = (int(m.group(1)), int(m.group(2)), int(m.group(3)))
        return " " if d in allowed_exact else m.group(0)

    def sub_dot(m: re.Match) -> str:
        d = (int(m.group(3)), int(m.group(2)), int(m.group(1)))
        return " " if d in allowed_exact else m.group(0)

    def sub_ru(m: re.Match) -> str:
        m_word = m.group(1).lower()
        year = int(m.group(2))
        for stem, num in RU_MONTHS.items():
            if m_word.startswith(stem) and (year, num) in allowed_ym:
                return " "
        return m.group(0)

    out = ISO_DATE_RE.sub(sub_iso, text)
    out = DOT_DATE_RE.sub(sub_dot, out)
    out = RU_MONTH_YEAR_RE.sub(sub_ru, out)
    return out


def _resolve_num_atom(
    raw: str, text: str, start: int, end: int, scale_unit: str | None = None
) -> tuple[str, bool]:
    """Разобрать числовой атом и вернуть (нормализованная_строка, is_ambiguous_locale)."""
    s = re.sub(r"[ \u00a0]", "", raw.strip())
    if "," in s and "." in s:
        return s.replace(",", ""), False
    if re.fullmatch(r"[1-9]\d{0,2}(?:,\d{3}){2,}", s):
        return s.replace(",", ""), False
    if re.fullmatch(r"[1-9]\d{0,2},\d{3}", s):
        prefix = text[max(0, start - 6):start]
        has_usd_prefix = bool(re.search(r"(?:[$€£]|USD)\s*$", prefix, re.I))
        has_cyr_scale = bool(scale_unit and re.search(r"[а-яё]", scale_unit, re.I))
        win = text[max(0, start - 35):min(len(text), end + 35)]
        has_lat = bool(re.search(r"[A-Za-z]{2,}", win))
        has_cyr = bool(re.search(r"[А-Яа-яЁё]{2,}", win))

        if has_usd_prefix and not has_cyr_scale:
            return s.replace(",", ""), False
        if has_lat and not has_cyr:
            return s.replace(",", ""), False
        if has_cyr and not has_lat:
            return s.replace(",", "."), False
        # Смешанный контекст RU/EN без явного $ или отсутствие контекста: консервативно помечаем неоднозначность
        return s.replace(",", "."), True
    return s.replace(",", "."), False


def _extract_number_details(
    text: str, strip_locator_noise: bool = False, expand_words: bool = False
) -> tuple[list[tuple[str, str]], list[tuple[str, set[str]]]]:
    """Вернуть (однозначные (display, canonical), неоднозначные (display, {cand_1, cand_1000}))."""
    cleaned = ID_NOISE_RE.sub(" ", text.replace("\u00a0", " "))
    if strip_locator_noise:
        cleaned = LOCATOR_NOISE_RE.sub(" ", cleaned)
    cleaned = INFLECTED_NUM_RE.sub(r"\1", cleaned)
    cleaned = re.sub(r"\bD(\d+)\b", r" \1 ; ", cleaned, flags=re.I)
    cleaned = re.sub(r"\bQ([1-4])\b", r" \1 ; ", cleaned, flags=re.I)
    if expand_words:
        for w, digit in WORD_NUMS.items():
            cleaned = re.sub(rf"\b{w}\b", digit, cleaned, flags=re.I)

    found: list[tuple[str, str]] = []
    ambiguous: list[tuple[str, set[str]]] = []
    covered_spans: list[tuple[int, int]] = []

    def _record(disp: str, raw_atom: str, a_start: int, a_end: int, mult: int, scale_txt: str | None) -> None:
        num_str, is_amb = _resolve_num_atom(raw_atom, cleaned, a_start, a_end, scale_txt)
        if is_amb:
            cands: set[str] = set()
            for variant in (num_str, num_str.replace(".", "")):
                try:
                    cands.add(_norm_dec(str(Decimal(variant) * mult)))
                except InvalidOperation:
                    cands.add(_norm_dec(variant))
            ambiguous.append((disp, cands))
            return
        try:
            canon = _norm_dec(str(Decimal(num_str) * mult))
        except InvalidOperation:
            canon = _norm_dec(num_str)
        found.append((disp, canon))

    for rx in (VERBAL_RANGE_RE, RANGE_RE):
        for m in rx.finditer(cleaned):
            if any(s <= m.start() and m.end() <= e for s, e in covered_spans):
                continue
            scale_a = m.group(2)
            scale_b = m.group(4)
            mult_b = _scale_mult(scale_b)
            mult_a = _scale_mult(scale_a) if scale_a else mult_b
            _record(m.group(1).strip(), m.group(1), m.start(1), m.end(1), mult_a, scale_a or scale_b)
            _record(m.group(3).strip(), m.group(3), m.start(3), m.end(3), mult_b, scale_b)
            covered_spans.append((m.start(), m.end()))

    for m in NUM_WITH_SCALE_RE.finditer(cleaned):
        if any(s <= m.start() and m.end() <= e for s, e in covered_spans):
            continue
        _record(m.group(0).strip(), m.group(1), m.start(1), m.end(1), _scale_mult(m.group(2)), m.group(2))

    return found, ambiguous


def _extract_number_tokens(
    text: str, strip_locator_noise: bool = False, expand_words: bool = False
) -> list[tuple[str, str]]:
    """Вернуть список (display, canonical_value) для однозначно распознанных чисел."""
    found, _ = _extract_number_details(text, strip_locator_noise=strip_locator_noise, expand_words=expand_words)
    return found


def _classify_unquoted_evidence(ev: list[dict], objects: dict[str, dict] | None) -> tuple[bool, str]:
    """Найти основания, чьи числа нужно сверить вручную по локаторам."""
    if not ev or objects is None:
        return False, ""
    reasons: list[str] = []
    for item in ev:
        if item.get("quote"):
            continue
        sid = str(item.get("source") or "")
        src = objects.get(sid)
        if not src:
            return False, ""
        kind = str(src.get("kind") or "").lower()
        path_str = str(src.get("path") or "")
        suffix = Path(path_str).suffix.lower()
        if not path_str and src.get("url"):
            try:
                url_suffix = Path(urlsplit(src["url"]).path).suffix.lower()
            except ValueError:
                return False, ""
            if url_suffix in {".pdf", ".docx", ".xlsx", ".xls", ".ods", ".csv", ".tsv"}:
                suffix = url_suffix
        locator = str(item.get("locator") or "").strip()
        if not locator:
            return False, ""
        if kind in {"image", "audio", "video"} or (suffix and suffix not in L.TEXT_SUFFIXES):
            reasons.append(f"{sid} ({kind or suffix}, locator: «{locator}»)")
        elif kind in {"table", "dataset"} or suffix in {".csv", ".tsv"}:
            reasons.append(f"{sid} (таблица {path_str or kind}, locator: «{locator}»)")
        else:
            return False, ""
    return bool(reasons), "; ".join(reasons)


def assess_quote_numbers(clm: dict, objects: dict[str, dict] | None = None) -> dict:
    """Классифицировать применимость проверки чисел по quote и вернуть результат сверки.

    Возвращает dict с ключами:
      status: 'ok' | 'error' | 'not_assessed'
      reason: 'numbers_matched' | 'derived_calculation' | 'non_text_or_locator' | 'locale_ambiguous' | 'missing_numbers'
      missing: list[str]
      ambiguous: list[str]
      detail: str
    """
    ev = clm.get("evidence") or []
    quotes = [str(e.get("quote")) for e in ev if e.get("quote")]
    derived_from = [str(x) for x in (clm.get("derived_from") or []) if x]

    derived_valid = bool(derived_from) and (
        objects is None
        or all(
            ref in objects and objects[ref].get("status", "active") != "retracted"
            for ref in derived_from
        )
    )
    if derived_valid and clm.get("type") in ("forecast", "interpretation"):
        return {
            "status": "ok",
            "reason": "derived_calculation",
            "missing": [],
            "ambiguous": [],
            "detail": "",
        }

    locators = [str(e.get("locator")) for e in ev if e.get("locator")]
    pool_exact_dates: set[tuple[int, int, int]] = set()
    pool_ym: set[tuple[int, int]] = set()
    for txt in quotes + locators:
        ed, ym = _extract_dates(txt)
        pool_exact_dates |= ed
        pool_ym |= ym
    as_of = str(clm.get("as_of") or "").strip()
    if as_of:
        ed, ym = _extract_dates(as_of)
        pool_exact_dates |= ed
        pool_ym |= ym

    stmt = _replace_known_dates(str(clm.get("statement") or ""), pool_exact_dates, pool_ym)
    stmt_tokens, stmt_amb = _extract_number_details(stmt, strip_locator_noise=False, expand_words=False)

    pool_values: set[str] = set()
    quote_amb: list[tuple[str, set[str]]] = []
    for q in quotes:
        q_tokens, q_amb_items = _extract_number_details(q, strip_locator_noise=False, expand_words=True)
        for _, canon in q_tokens:
            pool_values.add(canon)
        quote_amb.extend(q_amb_items)

    missing: list[str] = []
    seen_disp: set[str] = set()
    amb_covered_missing: list[str] = []
    hard_missing: list[str] = []

    quote_amb_cands: set[str] = set()
    for _, cands in quote_amb:
        quote_amb_cands |= cands

    for disp, canon in stmt_tokens:
        if canon not in pool_values and disp not in seen_disp:
            seen_disp.add(disp)
            missing.append(disp)
            if canon in quote_amb_cands:
                amb_covered_missing.append(disp)
            else:
                hard_missing.append(disp)

    for disp, cands in stmt_amb:
        if not (cands & (pool_values | quote_amb_cands)) and disp not in seen_disp:
            seen_disp.add(disp)
            missing.append(disp)
            hard_missing.append(disp)

    amb_displays: list[str] = []
    for disp, _ in stmt_amb + quote_amb:
        if disp not in amb_displays:
            amb_displays.append(disp)

    if stmt_amb and not hard_missing:
        return {
            "status": "not_assessed",
            "reason": "locale_ambiguous",
            "missing": missing,
            "ambiguous": amb_displays,
            "detail": ", ".join(amb_displays),
        }

    if amb_covered_missing and not hard_missing:
        return {
            "status": "not_assessed",
            "reason": "locale_ambiguous",
            "missing": missing,
            "ambiguous": amb_displays,
            "detail": ", ".join(amb_displays),
        }

    if hard_missing or missing:
        is_nontext, detail = _classify_unquoted_evidence(ev, objects)
        if is_nontext:
            return {
                "status": "not_assessed",
                "reason": "non_text_or_locator",
                "missing": [],
                "ambiguous": amb_displays,
                "detail": f"{', '.join(missing)} — {detail}",
            }
        return {
            "status": "error",
            "reason": "missing_numbers",
            "missing": missing or [d for d, _ in stmt_amb],
            "ambiguous": amb_displays,
            "detail": ", ".join(missing),
        }

    return {
        "status": "ok",
        "reason": "numbers_matched",
        "missing": [],
        "ambiguous": [],
        "detail": "Сегмент, период, знаменатель и смысл утверждения требуют отдельной сверки.",
    }


def missing_numbers_in_quote(clm: dict, objects: dict[str, dict] | None = None) -> list[str]:
    """Вернуть числа из statement, отсутствующие в quote (locator и as_of — только для дат)."""
    res = assess_quote_numbers(clm, objects=objects)
    return res["missing"]


def _single_rate(text: str) -> tuple[tuple, str] | None:
    """Выделить одну явно записанную долю или изменение с единицей."""
    text = RATE_BLOCK_PREFIX_RE.sub("", text)
    markup = tuple(match.group() for match in RATE_INLINE_MARKUP_RE.finditer(text))
    text = RATE_INLINE_MARKUP_RE.sub(" ", text)
    units = list(RATE_UNIT_RE.finditer(text))
    if len(units) != 1 or units[0]["other"]:
        return None
    unit = units[0]
    number = RATE_NUMBER_RE.search(text[:unit.start()])
    if not number:
        return None
    before = text[:number.start()]
    after = text[unit.end():]
    if re.search(r"\d", before + after):
        return None
    tokens, ambiguous = _extract_number_details(text, expand_words=True)
    if ambiguous or len(tokens) != 1 or tokens[0][0] != number[1]:
        return None
    context = (" ".join(before.casefold().split()), " ".join(after.casefold().split()).removesuffix("."))
    return (*context, tokens[0][1], markup), "п.п." if unit["points"] else "%"


def rate_unit_mismatch(statement: str, reference: str) -> tuple[str, str] | None:
    """Сопоставить единицы при прямой замене в одной формулировке."""
    stmt_rate = _single_rate(statement)
    quote_rate = _single_rate(reference)
    if not stmt_rate or not quote_rate:
        return None
    stmt_context, stmt_unit = stmt_rate
    quote_context, quote_unit = quote_rate
    if stmt_context == quote_context and stmt_unit != quote_unit:
        return stmt_unit, quote_unit
    return None


def _quote_unit_mismatch(clm: dict) -> tuple[str, str] | None:
    evidence = clm.get("evidence") or []
    if clm.get("type") != "fact" or len(evidence) != 1 or not evidence[0].get("quote"):
        return None
    return rate_unit_mismatch(str(clm.get("statement") or ""), str(evidence[0]["quote"]))


def filtered_upstream(ledger: L.Ledger, oid: str) -> list[tuple[str, str]]:
    """Только поддерживающие связи для опоры: без contradicted_by, objections, unchosen options и CHG."""
    o = ledger.objects.get(oid, {})
    p = L.prefix(oid)
    out: list[tuple[str, str]] = []

    def add(ids, role: str) -> None:
        for x in ids or []:
            if x:
                out.append((str(x), role))

    if p == "CLM":
        add([e.get("source") for e in o.get("evidence", []) if e.get("source")], "источник")
        add(o.get("derived_from"), "выведено из")
        # намеренно НЕ включаем contradicted_by («противоречит»)
    elif p == "HYP":
        add(o.get("based_on"), "основано на")
    elif p == "PAIN":
        for name, crit in (o.get("criteria") or {}).items():
            add((crit or {}).get("basis"), f"критерий {name}")
    elif p == "ALT":
        add(o.get("supported_by"), "довод за")
        # намеренно НЕ включаем objections («возражение»)
        for crit, sc in (o.get("scores") or {}).items():
            add((sc or {}).get("refs"), f"оценка «{crit}»")
    elif p == "DEC":
        add(o.get("based_on"), "основание")
        if o.get("chosen"):
            add([o["chosen"]], "выбранный вариант")
        # намеренно НЕ включаем options («вариант»)
    elif p == "PRM":
        add(o.get("proof"), "подтверждение")
        if o.get("decision"):
            add([o["decision"]], "решение")
    elif p == "EXP":
        add(o.get("tests"), "проверяет")
        if o.get("decision"):
            add([o["decision"]], "решение")

    seen: set[tuple[str, str]] = set()
    uniq: list[tuple[str, str]] = []
    for x, role in out:
        if (x, role) not in seen:
            seen.add((x, role))
            uniq.append((x, role))
    return uniq


def filtered_trace(ledger: L.Ledger, root_id: str) -> dict:
    """Построить фильтрованный trace опоры и вычислить флаги доказательности."""
    seen: set[str] = {root_id}
    parent_path: dict[str, list[str]] = {root_id: [root_id]}
    queue: deque[str] = deque([root_id])
    nodes: list[str] = [root_id]
    sources: list[str] = []
    facts: list[str] = []
    interpretations: list[str] = []
    hypotheses: list[str] = []
    contradictions: list[tuple[str, str]] = []

    while queue:
        cur = queue.popleft()
        obj = ledger.objects.get(cur)
        if not obj:
            continue
        pfx = L.prefix(cur)
        if pfx == "SRC":
            sources.append(cur)
        elif pfx == "CLM":
            if obj.get("type") == "fact":
                facts.append(cur)
            else:
                interpretations.append(cur)
            for c_id in obj.get("contradicted_by") or []:
                contradictions.append((cur, str(c_id)))
        elif pfx == "HYP":
            hypotheses.append(cur)

        for target, _role in filtered_upstream(ledger, cur):
            if target not in seen:
                seen.add(target)
                parent_path[target] = parent_path[cur] + [target]
                nodes.append(target)
                queue.append(target)

    src_kinds = {
        s: (ledger.objects.get(s) or {}).get("kind", "")
        for s in sources
    }
    synthetic_only = bool(sources) and all(k == "synthetic_interview" for k in src_kinds.values())
    single_source = len(sources) == 1

    root_pfx = L.prefix(root_id)
    hypothesis_only = False
    if root_pfx in ("DEC", "ALT", "HYP", "PRM"):
        hypothesis_only = len(facts) == 0
    elif root_pfx == "CLM" and (ledger.objects.get(root_id) or {}).get("type") != "fact":
        hypothesis_only = len(facts) == 0

    try:
        raw_stale = N.stale_reasons(ledger, root_id)
    except ValueError:
        raw_stale = []
    stale = [r for r in raw_stale if all(n in seen for n in r.get("path", []))]
    stale_refs = {r.get("reference") for r in stale if r.get("reference")}
    for nid in nodes[1:]:
        if nid in stale_refs:
            continue
        obj = ledger.objects.get(nid)
        if not obj:
            continue
        status = obj.get("status", "active")
        if status in ("retracted", "superseded"):
            stale.append(
                {
                    "code": f"basis_{status}",
                    "date": None,
                    "reference": nid,
                    "path": parent_path.get(nid, [root_id, nid]),
                    "superseded_by": obj.get("superseded_by"),
                }
            )
            stale_refs.add(nid)

    return {
        "root": root_id,
        "nodes": nodes,
        "sources": sources,
        "source_kinds": src_kinds,
        "facts": facts,
        "interpretations": interpretations,
        "hypotheses": hypotheses,
        "contradictions": contradictions,
        "stale": stale,
        "flags": {
            "synthetic_only": synthetic_only,
            "hypothesis_only": hypothesis_only,
            "single_source": single_source,
            "contradicted": bool(contradictions),
            "stale": bool(stale),
        },
    }


def _what_to_gather_hint(trace_res: dict, root_pfx: str) -> str:
    flags = trace_res["flags"]
    src_kinds = set(trace_res["source_kinds"].values())
    parts: list[str] = []
    if flags["synthetic_only"]:
        parts.append("проверить реальными данными: наблюдениями продукта, аналитикой или живыми интервью с целевым сегментом (синтетика даёт только гипотезы)")
    if flags["hypothesis_only"]:
        parts.append("добавить подтверждённый факт: внутреннюю аналитику, первоисточник конкурента/регулятора или результат эксперимента")
    if flags["single_source"]:
        if src_kinds & {"vendor_blog", "media"}:
            parts.append("сверить с первоисточником (официальный документ, тарифная страница или внутренняя выгрузка)")
        else:
            parts.append("при повышении критичности для решения добавить второй независимый источник другого типа")
    if flags["contradicted"]:
        c_ids = sorted({cid for _, cid in trace_res["contradictions"]})
        parts.append(f"разобрать противоречие с {', '.join(c_ids)} и при необходимости обновить формулировку через supersede")
    if flags["stale"]:
        refs = sorted({r.get("reference", "") for r in trace_res["stale"] if r.get("reference")})
        parts.append(f"пересмотреть опору на устаревшие записи ({', '.join(refs)})")
    return "; ".join(parts)


def check(project: Path) -> list[dict]:
    """Проверить доказательность утверждений и опоры ключевых решений проекта."""
    project = Path(project)
    ledger = L.Ledger(project)
    if ledger.load_problems and not ledger.objects:
        return [
            _finding(
                "evidence_not_assessed",
                "not_assessed",
                f"Реестр недоступен ({ledger.load_problems[0][1]}); проверка доказательности не выполнена.",
                "state/ledger",
                1,
                [],
            )
        ]

    active_clms = [
        o for oid, o in sorted(ledger.objects.items())
        if L.prefix(oid) == "CLM" and o.get("status", "active") == "active"
    ]
    active_decs = [
        o for oid, o in sorted(ledger.objects.items())
        if L.prefix(oid) == "DEC" and o.get("status", "active") == "active"
    ]

    if not active_clms and not active_decs:
        return [
            _finding(
                "evidence_not_assessed",
                "not_assessed",
                "В реестре нет активных CLM и DEC для проверки доказательности.",
                "state/ledger/claims.yaml",
                1,
                [],
            )
        ]

    id_lines = _yaml_id_lines(ledger.dir)
    findings: list[dict] = []
    flagged_clms: set[str] = set()

    # 1 и 2. Проверка CLM: прогноз как fact и числа вне цитаты
    for clm in active_clms:
        cid = str(clm["id"])
        rel, line_no = id_lines.get(cid, ("state/ledger/claims.yaml", 1))
        if clm.get("type") == "fact":
            stmt = str(clm.get("statement") or "")
            m = FORECAST_MARKERS_RE.search(stmt)
            if m:
                flagged_clms.add(cid)
                findings.append(
                    _finding(
                        "evidence_forecast_as_fact",
                        "warn",
                        f"{cid}: записан как fact, но содержит маркер прогноза/оценки («{m.group(0)}»). "
                        f"Что добрать: перевести в type: forecast/interpretation через supersede или подтвердить отчётным фактом.",
                        rel,
                        line_no,
                        [cid],
                    )
                )

        qres = assess_quote_numbers(clm, ledger.objects)
        if qres["status"] == "not_assessed":
            flagged_clms.add(cid)
            if qres["reason"] == "non_text_or_locator":
                findings.append(
                    _finding(
                        "evidence_quote_not_assessed",
                        "not_assessed",
                        f"{cid}: текстовая проверка quote не применима ({qres['detail']}). "
                        f"Что проверить: сверить числа вручную в указанном нетекстовом/табличном источнике или добавить текстовую выдержку.",
                        rel,
                        line_no,
                        [cid],
                    )
                )
            else:
                findings.append(
                    _finding(
                        "evidence_locale_ambiguous",
                        "not_assessed",
                        f"{cid}: неоднозначный разделитель тысяч/дробной части в смешанном контексте ({qres['detail']}). "
                        f"Что проверить: уточнить формат числа (тысячи vs дробная часть, разница 1000x) и при необходимости поправить quote/statement.",
                        rel,
                        line_no,
                        [cid],
                    )
                )
        elif qres["status"] == "error":
            flagged_clms.add(cid)
            findings.append(
                _finding(
                    "evidence_number_not_in_quote",
                    "error",
                    f"{cid}: числа из statement отсутствуют в quote ({', '.join(qres['missing'])}). "
                    f"Что добрать: дополнить quote дословной выдержкой с этими числами из источника.",
                    rel,
                    line_no,
                    [cid],
                )
            )
        elif qres["reason"] == "numbers_matched" and (units := _quote_unit_mismatch(clm)):
            stmt_unit, quote_unit = units
            findings.append(
                _finding(
                    "evidence_unit_mismatch",
                    "warn",
                    f"{cid}: при совпадающем числе в statement «{stmt_unit}», в quote «{quote_unit}». "
                    "Сверьте единицу по источнику.",
                    rel,
                    line_no,
                    [cid],
                )
            )

    # 3. Фильтрованный trace для DEC и ключевых CLM
    key_clm_ids: set[str] = set()
    for dec in active_decs:
        for ref in dec.get("based_on") or []:
            if L.prefix(str(ref)) == "CLM":
                key_clm_ids.add(str(ref))
        chosen = dec.get("chosen")
        if chosen and chosen in ledger.objects:
            alt = ledger.objects[chosen]
            for ref in alt.get("supported_by") or []:
                if L.prefix(str(ref)) == "CLM":
                    key_clm_ids.add(str(ref))
            for sc in (alt.get("scores") or {}).values():
                for ref in (sc or {}).get("refs") or []:
                    if L.prefix(str(ref)) == "CLM":
                        key_clm_ids.add(str(ref))
    if not active_decs:
        key_clm_ids = {str(c["id"]) for c in active_clms}

    trace_targets: list[str] = [str(d["id"]) for d in active_decs] + sorted(
        cid for cid in key_clm_ids
        if cid in ledger.objects and ledger.objects[cid].get("status", "active") == "active"
    )

    for oid in trace_targets:
        rel, line_no = id_lines.get(oid, ("state/ledger/claims.yaml", 1))
        tr = filtered_trace(ledger, oid)
        flags = tr["flags"]
        pfx = L.prefix(oid)
        hint = _what_to_gather_hint(tr, pfx)

        if flags["synthetic_only"]:
            findings.append(
                _finding(
                    "evidence_synthetic_only",
                    "warn",
                    f"{oid}: опора только на синтетические интервью ({', '.join(tr['sources'])}). Что добрать: {hint}.",
                    rel,
                    line_no,
                    [oid],
                )
            )

        if flags["hypothesis_only"]:
            findings.append(
                _finding(
                    "evidence_hypothesis_only",
                    "warn",
                    f"{oid}: в фильтрованном trace нет подтверждённых фактов (type: fact), только гипотезы/допущения. Что добрать: {hint}.",
                    rel,
                    line_no,
                    [oid],
                )
            )

        if flags["single_source"]:
            src_id = tr["sources"][0]
            src_obj = ledger.objects.get(src_id) or {}
            src_kind = tr["source_kinds"].get(src_id, "")
            src_rel = str(src_obj.get("reliability") or "").lower()
            src_meta = " ".join(str(src_obj.get(k) or "") for k in ("title", "notes", "url"))
            clm_obj = ledger.objects[oid]
            conf_level = str(((clm_obj.get("confidence") or {}).get("level")) or "").lower()
            primary_kind_ok = src_kind in {"dataset", "document", "table", "interview", "website"}
            website_ok = src_kind != "website" or not re.search(
                r"блог|blog|нативн\w*\s+реклам|реклам|отзыв|сми|vc\.ru|medium\.com|habr|reviews?\b",
                src_meta,
                re.I,
            )
            src_status = str(src_obj.get("status") or "active").lower()
            is_narrow_fact = (
                pfx == "CLM"
                and clm_obj.get("type") == "fact"
                and oid not in flagged_clms
                and primary_kind_ok
                and website_ok
                and conf_level != "low"
                and src_rel != "low"
                and src_status == "active"
                and not flags["stale"]
                and not flags["contradicted"]
            )
            if is_narrow_fact:
                findings.append(
                    _finding(
                        "evidence_single_source",
                        "ok",
                        f"{oid}: узкий факт опирается на один источник {src_id} ({src_kind or 'unknown'}). "
                        "Смысл и контекст подтверждаются отдельной сверкой цитаты.",
                        rel,
                        line_no,
                        [oid],
                    )
                )
            else:
                findings.append(
                    _finding(
                        "evidence_single_source",
                        "warn",
                        f"{oid}: вся опора сведена к одному источнику {src_id} ({src_kind or 'unknown'}). Что добрать: {hint}.",
                        rel,
                        line_no,
                        [oid],
                    )
                )

        if flags["contradicted"]:
            c_ids = sorted({cid for _, cid in tr["contradictions"]})
            findings.append(
                _finding(
                    "evidence_contradicted",
                    "warn",
                    f"{oid}: в цепочке опоры есть зафиксированные противоречия ({', '.join(c_ids)}). Что добрать: {hint}.",
                    rel,
                    line_no,
                    [oid],
                )
            )

        if flags["stale"]:
            refs = sorted({r.get("reference", "") for r in tr["stale"] if r.get("reference")})
            findings.append(
                _finding(
                    "evidence_stale",
                    "warn",
                    f"{oid}: цепочка опоры ссылается на устаревшие записи ({', '.join(refs)}). Что добрать: {hint}.",
                    rel,
                    line_no,
                    [oid],
                )
            )

    return findings


def _gather_recommendations(ledger: L.Ledger, findings: list[dict]) -> list[str]:
    recs: list[str] = []
    codes = {f["code"] for f in findings}
    if "evidence_forecast_as_fact" in codes:
        cids = [cid for f in findings if f["code"] == "evidence_forecast_as_fact" for cid in f["ids"]]
        recs.append(f"Уточнить тип для {', '.join(cids)}: заменить type: fact на forecast/interpretation через supersede или приложить фактический отчёт.")
    if "evidence_number_not_in_quote" in codes:
        cids = [cid for f in findings if f["code"] == "evidence_number_not_in_quote" for cid in f["ids"]]
        recs.append(f"Дополнить дословные quote для {', '.join(cids)} из зарегистрированных источников.")
    if "evidence_unit_mismatch" in codes:
        cids = [cid for f in findings if f["code"] == "evidence_unit_mismatch" for cid in f["ids"]]
        recs.append(f"Сверить единицы для {', '.join(cids)} по дословным quote.")
    if "evidence_quote_not_assessed" in codes:
        cids = [cid for f in findings if f["code"] == "evidence_quote_not_assessed" for cid in f["ids"]]
        recs.append(f"Сверить вручную числа для {', '.join(cids)} по нетекстовым/табличным locator или приложить текстовую выдержку.")
    if "evidence_locale_ambiguous" in codes:
        cids = [cid for f in findings if f["code"] == "evidence_locale_ambiguous" for cid in f["ids"]]
        recs.append(f"Уточнить разделитель тысяч/десятичной запятой (риск 1000x) для {', '.join(cids)}.")
    if "evidence_synthetic_only" in codes:
        recs.append("Добавить наблюдательный или первоисточный материал (аналитика продукта, живые интервью с целевым сегментом, прайсы/документация), чтобы не опираться только на синтетику.")
    if "evidence_hypothesis_only" in codes:
        recs.append("Собрать фактическую базу (внутренняя воронка, официальный документ или пилот), чтобы ключевое решение опиралось не только на гипотезы.")
    if any(f["code"] == "evidence_single_source" and f["status"] == "warn" for f in findings):
        recs.append("Для ключевого решения на одном источнике подобрать независимое подтверждение другого типа (например, первоисточник к СМИ/блогу или внутреннюю аналитику к внешнему отчёту).")
    if "evidence_contradicted" in codes:
        recs.append("Сопоставить конфликтующие источники и зафиксировать разрешение противоречия в реестре (limitations или supersede).")
    if "evidence_stale" in codes:
        recs.append("Обновить ссылки с устаревших (retracted/superseded) объектов на актуальные версии через update-decision.")
    if not recs:
        recs.append("Критичных пробелов в прослеживаемости не обнаружено; при появлении новых данных зафиксируйте их через add-source и CHG.")
    return recs


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Линт доказательности по реестру (M12)")
    ap.add_argument("project", type=Path, help="папка проекта")
    ap.add_argument("--json", action="store_true", help="машиночитаемый вывод (список находок)")
    args = ap.parse_args(argv)
    proj = args.project.resolve()
    if not proj.is_dir():
        print(f"Проект не найден: {proj}", file=sys.stderr)
        return 2
    items = check(proj)
    if args.json:
        print(json.dumps(items, ensure_ascii=False, indent=2))
    else:
        for it in items:
            ids = f" [{', '.join(it['ids'])}]" if it["ids"] else ""
            print(f"[{it['status']}] {it['code']} ({it['file']}:{it['line']}){ids}: {it['message']}")
        print("\n## Что добрать")
        ledger = L.Ledger(proj)
        for line in _gather_recommendations(ledger, items):
            print(f"- {line}")
    return 1 if any(it["status"] == "error" for it in items) else 0


def _utf8_stdio() -> None:
    for s in (sys.stdin, sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


if __name__ == "__main__":
    _utf8_stdio()
    sys.exit(main())

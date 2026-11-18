#!/usr/bin/env python3
"""M09: Проверка вариантов как продуктовых ходов (04_options.md + alternatives.yaml).

Принцип совместимости со старыми документами:
- Если 04_options.md отсутствует или не содержит ни одного v2-маркера таблицы/блоков
  («Ось различия», «Потолок эффекта», «Возражение к постановке», явные «Вес»/«Правило выбора»),
  проверка возвращает `not_assessed` (без ложной ошибки на исторических проектах).
- Когда v2-разметка присутствует, проверяются:
  1) «Ось различия» (warn если одна ось у invest/change, только исследование,
     или нет смены ценности/монетизации);
  2) явные веса / правило выбора (error при сумме баллов без весов; error если
     recommended не лучший и нет строки «почему против таблицы»);
  3) «Потолок эффекта» + чистый T0-расчёт потолка воронки;
  4) сильные возражения с ID и без запрещённых фраз («противоречит цели»,
     «может не сработать»); непустое главное основание у status_quo и kill;
  5) наличие блока «Возражение к постановке» (иначе error с «НЕ ГОТОВО»).
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys
from typing import Any, Dict, List, Optional, Tuple

import yaml

_TOOLS_DIR = pathlib.Path(__file__).resolve().parent.parent
if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))

_HISTORY_RE = re.compile(
    r"^##\s+(?:Исторический\s+снимок|История\s+до|Архив\s+до)\b",
    re.IGNORECASE,
)
_H2_RE = re.compile(r"^##\s+(.+?)\s*$")
_SEP_ROW_RE = re.compile(r"^\s*\|(?:\s*:?-+:?\s*\|)+\s*$")
_HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
_ALT_ID_RE = re.compile(r"\bALT-\d{3}\b")
_BASIS_ID_RE = re.compile(r"\b(?:CLM|HYP|PAIN|SRC|EXP)-\d{3}\b")
_ANY_ID_RE = re.compile(r"\b(?:SRC|CLM|HYP|PAIN|ALT|DEC|PRM|EXP|CHG)-\d{3}\b")

_ALLOWED_AXES = (
    "сегмент",
    "ценность",
    "монетизация",
    "канал",
    "ресурсы",
    "только исследование",
)

_FORBIDDEN_OBJECTION_RE = re.compile(
    r"(?:противоречит(?:\s+заявленной|\s+выбранной|\s+поставленной|\s+\S+){0,4}\s+цел[иью]|может\s+не\s+сработать)",
    re.IGNORECASE,
)

_PLACEHOLDER_CELL_RE = re.compile(
    r"^\s*(?:[—\-–?]+|…+|\.{3,}|нет|n/a|tbd|\[id\]|"
    r"\*?\(заполнить,\s*если[^)]*\)\*?)\s*$",
    re.IGNORECASE,
)


def compute_effect_ceiling(
    total: float,
    passed: float,
    rate_passed: float,
    rate_blocked: float,
) -> Dict[str, float]:
    """T0-расчёт потолка эффекта: максимум сдвига целевой метрики в п.п.,
    если вариант полностью снимает барьер (непрошедшая доля total-passed
    достигает уровня rate_passed).
    Доли rate_passed / rate_blocked принимаются в [0, 1] или процентах (>1).
    """
    if total <= 0 or passed < 0 or passed > total:
        raise ValueError("Некорректные численности воронки для расчёта потолка эффекта")
    rp = rate_passed / 100.0 if rate_passed > 1.0 else float(rate_passed)
    rb = rate_blocked / 100.0 if rate_blocked > 1.0 else float(rate_blocked)
    blocked = total - passed
    current_rate = (passed * rp + blocked * rb) / total
    ceiling_rate = rp
    ceiling_pp = (ceiling_rate - current_rate) * 100.0
    return {
        "total": float(total),
        "passed": float(passed),
        "blocked": float(blocked),
        "current_rate": current_rate,
        "ceiling_rate": ceiling_rate,
        "ceiling_pp": ceiling_pp,
    }


def compute_ryadom_funnel_ceiling(project: pathlib.Path) -> Optional[Dict[str, float]]:
    """Извлекает воронку ryadom из claims.yaml (CLM-019, CLM-020) и считает потолок ALT-001."""
    claims_path = project / "state" / "ledger" / "claims.yaml"
    if not claims_path.is_file():
        return None
    try:
        claims = yaml.safe_load(claims_path.read_text(encoding="utf-8")) or []
    except Exception:
        return None

    by_id = {c.get("id"): str(c.get("statement", "")) for c in claims if isinstance(c, dict)}
    c19 = by_id.get("CLM-019", "")
    c20 = by_id.get("CLM-020", "")
    m19 = re.search(r"Из\s+([\d\s]+)\s+новых\s+установок.*?([\d\s]+)\s+получили\s+первую", c19)
    m20 = re.search(r"D7[^0-9]*(\d+(?:[.,]\d+)?)%.*?(\d+(?:[.,]\d+)?)%", c20)
    if not (m19 and m20):
        return None
    total = float(re.sub(r"\s+", "", m19.group(1)))
    passed = float(re.sub(r"\s+", "", m19.group(2)))
    rp = float(m20.group(1).replace(",", "."))
    rb = float(m20.group(2).replace(",", "."))
    return compute_effect_ceiling(total, passed, rp, rb)


def _strip_html_comments(text: str) -> str:
    return _HTML_COMMENT_RE.sub("", text)


def _is_placeholder(cell: str) -> bool:
    s = re.sub(r"[*_`]", "", cell).strip()
    if not s or _PLACEHOLDER_CELL_RE.match(s) or re.fullmatch(r"критерий\s*\d+", s, re.IGNORECASE):
        return True
    s_clean = re.sub(r"\(обязательно\s+непустое\)", "", s, flags=re.IGNORECASE).strip()
    if not s_clean or _PLACEHOLDER_CELL_RE.match(s_clean):
        return True
    if _BASIS_ID_RE.search(s_clean):
        return False
    if "…" in s_clean or "..." in s_clean:
        return True
    if re.search(r"\[(?:clm|hyp|pain|src|exp|id)\s*[-—]?\s*(?:…|\.{3}|x)?\]", s_clean, re.IGNORECASE):
        return True
    return False


def _split_row(line: str) -> List[str]:
    stripped = line.strip()
    if stripped.startswith("|"):
        stripped = stripped[1:]
    if stripped.endswith("|"):
        stripped = stripped[:-1]
    return [c.strip() for c in stripped.split("|")]


def _find_tables(numbered_lines: List[Tuple[int, str]]) -> List[Tuple[int, List[str], List[Tuple[int, List[str]]]]]:
    tables = []
    i = 0
    while i < len(numbered_lines) - 1:
        ln, raw = numbered_lines[i]
        nxt = numbered_lines[i + 1][1]
        if raw.strip().startswith("|") and _SEP_ROW_RE.match(nxt):
            headers = _split_row(raw)
            rows: List[Tuple[int, List[str]]] = []
            j = i + 2
            while j < len(numbered_lines) and numbered_lines[j][1].strip().startswith("|"):
                rln, rraw = numbered_lines[j]
                if not _SEP_ROW_RE.match(rraw):
                    rows.append((rln, _split_row(rraw)))
                j += 1
            tables.append((ln, headers, rows))
            i = j
        else:
            i += 1
    return tables


def _extract_axis(cell: str) -> Optional[str]:
    low = re.sub(r"[*_`]", "", cell).lower().strip()
    if not low or "…" in low or "..." in low:
        return None
    matched = [axis for axis in _ALLOWED_AXES if axis in low]
    if len(matched) == 1:
        return matched[0]
    return None


def _parse_numeric_score(cell: str) -> Optional[float]:
    m = re.match(r"^\s*(\d+(?:[.,]\d+)?)\b", cell.strip())
    if m:
        return float(m.group(1).replace(",", "."))
    return None


def _norm_criterion_name(name: str) -> str:
    cleaned = re.sub(r"[*_`]", "", name)
    cleaned = re.sub(r"\(\s*\d+\s*[–\-—]\s*\d+\s*\)", "", cleaned)
    return re.sub(r"\s+", " ", cleaned).strip().lower()


def _parse_weight_value(text: str) -> Optional[float]:
    s = text.strip()
    if not s or _is_placeholder(s):
        return None
    m_pct = re.search(r"(\d+(?:[.,]\d+)?)\s*%", s)
    if m_pct:
        val = float(m_pct.group(1).replace(",", ".")) / 100.0
        return val if val > 0 else None
    m_num = re.search(r"(\d+(?:[.,]\d+)?)", s)
    if m_num:
        val = float(m_num.group(1).replace(",", "."))
        return val if val > 0 else None
    return None


def _parse_weights_from_text(
    active_lines: List[Tuple[int, str]],
    tables: Optional[List[Tuple[int, List[str], List[Tuple[int, List[str]]]]]] = None,
) -> Dict[str, float]:
    """Ищет явные веса критериев в списке ('1. **Критерий** (вес 0.3 / 30%)')
    и в таблицах критериев ('| Критерий | Вес | ... |')."""
    weights: Dict[str, float] = {}
    for _, raw in active_lines:
        m = re.match(r"^\s*\d+\.\s*\*{0,2}([^(*:\n]+?)\*{0,2}\s*(?:\(([^)]+)\)|—|:|\-)(.*)$", raw)
        if m:
            cname = _norm_criterion_name(m.group(1))
            tail = (m.group(2) or "") + " " + (m.group(3) or "")
            wm = re.search(r"(?:вес[:\s=]*)?(\d+(?:[.,]\d+)?)\s*%", tail, re.IGNORECASE)
            if wm:
                val = float(wm.group(1).replace(",", ".")) / 100.0
                if val > 0 and cname and not _is_placeholder(cname):
                    weights[cname] = val
                continue
            wm2 = re.search(r"вес[:\s=]+(\d+(?:[.,]\d+)?)", tail, re.IGNORECASE)
            if wm2:
                val = float(wm2.group(1).replace(",", "."))
                if val > 0 and cname and not _is_placeholder(cname):
                    weights[cname] = val

    if tables:
        for _, headers, rows in tables:
            hl = [h.lower() for h in headers]
            crit_idx = next((idx for idx, h in enumerate(hl) if "критери" in h), None)
            weight_idx = next((idx for idx, h in enumerate(hl) if re.search(r"\bвес\b", h)), None)
            if crit_idx is None or weight_idx is None:
                continue
            for _, cells in rows:
                if crit_idx >= len(cells) or weight_idx >= len(cells):
                    continue
                cname = _norm_criterion_name(cells[crit_idx])
                if not cname or _is_placeholder(cells[crit_idx]):
                    continue
                wval = _parse_weight_value(cells[weight_idx])
                if wval is not None:
                    weights[cname] = wval
    return weights


def _has_why_against_rationale(
    active_lines: List[Tuple[int, str]],
    rows_by_key: Dict[str, Tuple[int, List[str]]],
    alt_cols: List[Tuple[int, str, str]],
    dec_recommended: Optional[str],
) -> bool:
    for rkey, (_, cells) in rows_by_key.items():
        if "почему против таблицы" in rkey or "почему вопреки таблице" in rkey:
            target_cells: List[str] = []
            if dec_recommended:
                for cidx, aid, _ in alt_cols:
                    if aid == dec_recommended and cidx < len(cells):
                        target_cells.append(cells[cidx])
            if not any(not _is_placeholder(cell) for cell in target_cells):
                target_cells = cells[1:]
            for cell in target_cells:
                cleaned = re.sub(r"[*_`]", "", cell).strip()
                if not cleaned or _is_placeholder(cleaned):
                    continue
                if re.search(r"заполнить,\s*если|рекомендован\s+не\s+лидер", cleaned, re.IGNORECASE):
                    continue
                alnum = re.findall(r"[A-Za-zА-Яа-яЁё0-9]", cleaned)
                if len(alnum) >= 6:
                    return True

    for _, raw in active_lines:
        st = raw.strip()
        if st.startswith("|"):
            continue
        m = re.search(r"почему\s+(?:против|вопреки)\s+таблицы\s*[:—\-]\s*(.+)", st, re.IGNORECASE)
        if m:
            val = re.sub(r"[*_`]", "", m.group(1)).strip()
            if val and not _is_placeholder(val) and not re.search(
                r"заполнить,\s*если|рекомендован\s+не\s+лидер", val, re.IGNORECASE
            ):
                if len(re.findall(r"[A-Za-zА-Яа-яЁё0-9]", val)) >= 6:
                    return True
    return False


def _extract_h2_body(active_lines: List[Tuple[int, str]], title_re: re.Pattern[str]) -> Tuple[Optional[int], List[Tuple[int, str]]]:
    start: Optional[int] = None
    body: List[Tuple[int, str]] = []
    for ln, raw in active_lines:
        st = raw.strip()
        if start is None:
            if title_re.match(st):
                start = ln
            continue
        if _H2_RE.match(st):
            break
        body.append((ln, raw))
    return start, body


def _has_substantive_premise_objection(active_lines: List[Tuple[int, str]]) -> bool:
    start, body = _extract_h2_body(active_lines, re.compile(r"^##\s+Возражение\s+к\s+постановке\b", re.IGNORECASE))
    if start is None:
        return False
    cleaned_text = _strip_html_comments("\n".join(raw for _, raw in body)).strip()
    if not cleaned_text:
        return False
    # Remove template boilerplate and placeholder tokens
    meaningful_lines = []
    for line in cleaned_text.splitlines():
        s = re.sub(r"[*_`]", "", line).strip()
        if not s:
            continue
        # Check if line is just unfilled template stub
        no_prefix = re.sub(r"^-?\s*Допущение\s+брифа\s*:\s*", "", s, flags=re.IGNORECASE)
        no_prefix = re.sub(r"что\s+его\s+опровергает\s+или\s+ограничивает\s*", "", no_prefix, flags=re.IGNORECASE)
        no_prefix = re.sub(r"\[(?:CLM|HYP|PAIN|SRC|EXP)[^\]]*…[^\]]*\]", "", no_prefix, flags=re.IGNORECASE)
        no_prefix = re.sub(r"[\s—\-–….:,;()\[\]]+", "", no_prefix)
        if len(no_prefix) >= 8:
            meaningful_lines.append(s)
    return bool(meaningful_lines)


def check(project: pathlib.Path) -> List[dict]:
    project = pathlib.Path(project)
    rel = "outputs/04_options.md"
    path = project / rel
    if not path.is_file():
        return [
            {
                "code": "options.not_assessed",
                "status": "not_assessed",
                "message": f"Файл {rel} отсутствует — проверка вариантов v2 не применялась.",
                "file": rel,
                "line": 0,
                "ids": [],
            }
        ]

    raw_lines = path.read_text(encoding="utf-8").splitlines()
    active_lines: List[Tuple[int, str]] = []
    for idx, line in enumerate(raw_lines, start=1):
        if _HISTORY_RE.match(line.strip()):
            break
        active_lines.append((idx, line))

    active_text = _strip_html_comments("\n".join(line for _, line in active_lines))
    active_lower = active_text.lower()

    tables = _find_tables(active_lines)
    weights = _parse_weights_from_text(active_lines, tables)

    comp_table = None
    for t in tables:
        if sum(1 for h in t[1] if _ALT_ID_RE.search(h)) >= 2:
            comp_table = t
            break

    alt_cols: List[Tuple[int, str, str]] = []  # (col_idx, alt_id, header)
    rows_by_key: Dict[str, Tuple[int, List[str]]] = {}
    if comp_table is not None:
        _, headers, rows = comp_table
        for cidx, h in enumerate(headers):
            m = _ALT_ID_RE.search(h)
            if m:
                alt_cols.append((cidx, m.group(0), h))
        for rln, rcells in rows:
            if not rcells:
                continue
            rkey = re.sub(r"[*`]", "", rcells[0]).strip().lower()
            rows_by_key[rkey] = (rln, rcells)

    def _find_row(*prefixes: str) -> Optional[Tuple[int, List[str]]]:
        for k, val in rows_by_key.items():
            if any(p in k for p in prefixes):
                return val
        return None

    sum_row = _find_row("сумма", "итого", "итоговый балл", "взвешенн")
    sum_has_numbers = bool(
        sum_row is not None
        and any(_parse_numeric_score(c) is not None for c in sum_row[1][1:])
    )

    has_v2_axis = bool(re.search(r"\bось\s+различия\b", active_lower))
    has_v2_ceiling = bool(re.search(r"\bпотолок\s+эффекта\b", active_lower))
    has_v2_premise = bool(re.search(r"\bвозражение\s+к\s+постановке\b", active_lower))
    has_weight_table_header = any(
        any(re.search(r"\bвес(?:а|ов)?\b", h, re.IGNORECASE) for h in t[1])
        for t in tables
    )
    has_v2_weights = (
        bool(weights)
        or has_weight_table_header
        or bool(re.search(r"^##\s*критерии\s+сравнения\s+и\s+веса\b", active_lower, re.MULTILINE))
        or bool(re.search(r"\bправило\s+выбора[*_\s]*[:—]", active_lower))
        or bool(re.search(r"\bпри\s+каком\s+весе\s+меняется\s+лидер\b", active_lower))
    )

    # Even without explicit v2 headers, an ALT comparison table summing scores without weights is an error
    if not (has_v2_axis or has_v2_ceiling or has_v2_premise or has_v2_weights):
        if sum_row is not None and sum_has_numbers and not weights:
            return [
                {
                    "code": "options.unweighted_sum",
                    "status": "error",
                    "message": "В таблице сравнения посчитана сумма баллов без объявленных весов или правила выбора.",
                    "file": rel,
                    "line": sum_row[0],
                    "ids": [aid for _, aid, _ in alt_cols],
                }
            ]
        return [
            {
                "code": "options.not_assessed",
                "status": "not_assessed",
                "message": (
                    f"В {rel} отсутствуют v2-таблица («Ось различия», «Потолок эффекта», "
                    "«Возражение к постановке», веса) — документ прежнего формата (not_assessed)."
                ),
                "file": rel,
                "line": 0,
                "ids": [],
            }
        ]

    # Load optional ledger files
    alt_ledger: Dict[str, dict] = {}
    alts_path = project / "state" / "ledger" / "alternatives.yaml"
    if alts_path.is_file():
        try:
            for item in yaml.safe_load(alts_path.read_text(encoding="utf-8")) or []:
                if isinstance(item, dict) and item.get("id"):
                    alt_ledger[item["id"]] = item
        except Exception:
            pass

    dec_recommended: Optional[str] = None
    dec_based_on: set[str] = set()
    decs_path = project / "state" / "ledger" / "decisions.yaml"
    if decs_path.is_file():
        try:
            decs = yaml.safe_load(decs_path.read_text(encoding="utf-8")) or []
            if decs and isinstance(decs[0], dict):
                dec_recommended = decs[0].get("recommended")
                for bid in decs[0].get("based_on") or []:
                    if bid:
                        dec_based_on.add(str(bid))
        except Exception:
            pass

    if not dec_recommended:
        m_rec = re.search(r"##\s*Рекомендация\s+агента\s*\n+\*{0,2}(ALT-\d{3})", active_text)
        if m_rec:
            dec_recommended = m_rec.group(1)

    findings: List[dict] = []

    def _is_inactive_alt(alt_id: str, header: str) -> bool:
        kind = str(alt_ledger.get(alt_id, {}).get("kind", "")).lower()
        if kind in {"status_quo", "kill"}:
            return True
        hl = header.lower()
        return any(
            kw in hl
            for kw in ("оставить как есть", "текущий курс", "закрыть", "заморозить", "осознанный отказ")
        )

    def _is_sq_or_kill(alt_id: str, header: str) -> bool:
        return _is_inactive_alt(alt_id, header)

    # 1. Ось различия
    axis_row = _find_row("ось различия")
    if axis_row is None:
        findings.append(
            {
                "code": "options.missing_axis_row",
                "status": "error",
                "message": "В таблице сравнения отсутствует строка «Ось различия».",
                "file": rel,
                "line": comp_table[0] if comp_table else 1,
                "ids": [],
            }
        )
    elif alt_cols:
        ax_ln, ax_cells = axis_row
        active_axes: List[Tuple[str, Optional[str]]] = []
        for cidx, aid, hdr in alt_cols:
            if cidx >= len(ax_cells):
                continue
            ax = _extract_axis(ax_cells[cidx])
            if not _is_inactive_alt(aid, hdr):
                active_axes.append((aid, ax))
                if ax is None:
                    findings.append(
                        {
                            "code": "options.invalid_axis",
                            "status": "error",
                            "message": (
                                f"У {aid} в «Ось различия» указано недопустимое значение «{ax_cells[cidx]}» "
                                f"(допустимо: {', '.join(_ALLOWED_AXES)})."
                            ),
                            "file": rel,
                            "line": ax_ln,
                            "ids": [aid],
                        }
                    )
        known_active = [ax for _, ax in active_axes if ax is not None]
        if known_active:
            distinct = set(known_active)
            if len(known_active) >= 2 and len(distinct) < 2:
                findings.append(
                    {
                        "code": "options.single_axis",
                        "status": "warn",
                        "message": (
                            f"У всех активных вариантов одна ось различия ({next(iter(distinct))}); "
                            "нужны >= 2 разные оси."
                        ),
                        "file": rel,
                        "line": ax_ln,
                        "ids": [aid for aid, _ in active_axes],
                    }
                )
            if all(ax == "только исследование" for ax in known_active):
                findings.append(
                    {
                        "code": "options.research_only_axes",
                        "status": "warn",
                        "message": "Все активные варианты имеют ось «только исследование» — нет продуктового или монетизационного хода.",
                        "file": rel,
                        "line": ax_ln,
                        "ids": [aid for aid, _ in active_axes],
                    }
                )
            elif not any(ax in {"ценность", "монетизация"} for ax in known_active):
                findings.append(
                    {
                        "code": "options.no_value_or_monetization_axis",
                        "status": "warn",
                        "message": "Среди активных вариантов нет ни одного с осью «ценность» или «монетизация».",
                        "file": rel,
                        "line": ax_ln,
                        "ids": [aid for aid, _ in active_axes],
                    }
                )

    # 2. Веса / правило выбора + сумма баллов без весов
    if sum_row is not None and sum_has_numbers and not weights:
        findings.append(
            {
                "code": "options.unweighted_sum",
                "status": "error",
                "message": "В таблице сравнения посчитана сумма баллов без объявленных весов критериев.",
                "file": rel,
                "line": sum_row[0],
                "ids": [aid for _, aid, _ in alt_cols],
            }
        )
    elif not weights and not re.search(
        r"правило выбора[*_\s]*:\s*[^\n]*(?:лексикограф|порог)", active_lower
    ):
        findings.append({
            "code": "options.missing_weights", "status": "error",
            "message": "Не заданы веса или явное невзвешенное правило выбора.",
            "file": rel, "line": comp_table[0] if comp_table else 0, "ids": [],
        })
    elif weights and alt_cols and comp_table is not None:
        crit_scores: Dict[str, float] = {aid: 0.0 for _, aid, _ in alt_cols}
        matched_crit = 0
        non_criterion_rows = (
            "суть",
            "ось различия",
            "затраты ↔ польза",
            "затраты <-> польза",
            "польза ↔ затраты",
            "почему против таблицы",
            "почему вопреки таблице",
            "главное основание",
            "сильное возражение",
            "возражение",
            "потолок эффекта",
            "взвешенная сумма",
            "взвешенный балл",
            "итоговый балл",
            "сумма",
            "итого",
        )
        for rkey, (_, rcells) in rows_by_key.items():
            norm_rkey = _norm_criterion_name(rkey)
            if any(skip == norm_rkey or skip in norm_rkey for skip in non_criterion_rows):
                continue
            w = None
            for wname, wval in weights.items():
                if wname == norm_rkey or wname in norm_rkey or norm_rkey in wname:
                    w = wval
                    break
            if w is None:
                continue
            row_ok = True
            row_vals: Dict[str, float] = {}
            for cidx, aid, _ in alt_cols:
                val = _parse_numeric_score(rcells[cidx]) if cidx < len(rcells) else None
                if val is None:
                    row_ok = False
                    break
                row_vals[aid] = val
            if row_ok:
                matched_crit += 1
                for aid, val in row_vals.items():
                    crit_scores[aid] += val * w

        if matched_crit == 0:
            findings.append({
                "code": "options.unmatched_weights", "status": "error",
                "message": "Ни один взвешенный критерий не сопоставлен с оценками вариантов.",
                "file": rel, "line": comp_table[0], "ids": [],
            })
        if matched_crit > 0 and dec_recommended and dec_recommended in crit_scores:
            best_score = max(crit_scores.values())
            if crit_scores[dec_recommended] + 1e-9 < best_score:
                if not _has_why_against_rationale(active_lines, rows_by_key, alt_cols, dec_recommended):
                    best_alts = [k for k, v in crit_scores.items() if abs(v - best_score) < 1e-9]
                    findings.append(
                        {
                            "code": "options.recommended_not_top",
                            "status": "error",
                            "message": (
                                f"Рекомендованный {dec_recommended} ({crit_scores[dec_recommended]:.2f}) уступает "
                                f"лидеру таблицы ({', '.join(best_alts)}: {best_score:.2f}), "
                                "но содержательное обоснование «Почему против таблицы» отсутствует."
                            ),
                            "file": rel,
                            "line": comp_table[0],
                            "ids": [dec_recommended] + best_alts,
                        }
                    )
        sens_m = re.search(r"при\s+каком\s+весе\s+меняется\s+лидер[*_\s]*[:—]\s*([^\n]+)", active_lower)
        if not sens_m or _is_placeholder(sens_m.group(1)):
            findings.append(
                {
                    "code": "options.missing_weight_sensitivity",
                    "status": "warn",
                    "message": "При взвешенном сравнении рекомендуется указать условие «при каком весе меняется лидер».",
                    "file": rel,
                    "line": comp_table[0],
                    "ids": [],
                }
            )

    # 3. Сильные возражения и главное основание
    obj_row = _find_row("сильное возражение", "возражение")
    if obj_row is not None and alt_cols:
        obj_ln, obj_cells = obj_row
        for cidx, aid, _ in alt_cols:
            if cidx >= len(obj_cells):
                continue
            txt = obj_cells[cidx].strip()
            if _FORBIDDEN_OBJECTION_RE.search(txt):
                findings.append(
                    {
                        "code": "options.forbidden_objection_phrase",
                        "status": "error",
                        "message": f"У {aid} в «Сильное возражение» использована запрещённая формальная фраза: «{txt}».",
                        "file": rel,
                        "line": obj_ln,
                        "ids": [aid],
                    }
                )
            found_ids = _BASIS_ID_RE.findall(txt)
            if not found_ids:
                findings.append(
                    {
                        "code": "options.objection_missing_id",
                        "status": "error",
                        "message": f"Возражение для {aid} должно атаковать конкретный ID (CLM/HYP/PAIN/SRC/EXP).",
                        "file": rel,
                        "line": obj_ln,
                        "ids": [aid],
                    }
                )
            else:
                alt_supported = {str(x) for x in (alt_ledger.get(aid, {}).get("supported_by") or []) if x}
                allowed_ids = dec_based_on | alt_supported
                if allowed_ids and not any(fid in allowed_ids for fid in found_ids):
                    findings.append(
                        {
                            "code": "options.objection_id_not_in_based_on",
                            "status": "error",
                            "message": (
                                f"Возражение для {aid} ссылается на {', '.join(found_ids)}, "
                                "которых нет в DEC.based_on / ALT.supported_by."
                            ),
                            "file": rel,
                            "line": obj_ln,
                            "ids": [aid] + found_ids,
                        }
                    )

    # Also check ledger alternatives objections when v2 options table is active
    for aid, alt in alt_ledger.items():
        alt_supported = {str(x) for x in (alt.get("supported_by") or []) if x}
        allowed_ids = dec_based_on | alt_supported
        for obj in alt.get("objections") or []:
            if not isinstance(obj, dict):
                continue
            otext = str(obj.get("text", ""))
            orefs = [str(r) for r in (obj.get("refs") or [])]
            if _FORBIDDEN_OBJECTION_RE.search(otext):
                findings.append(
                    {
                        "code": "options.ledger_forbidden_objection",
                        "status": "error",
                        "message": f"В alternatives.yaml у {aid} возражение содержит запрещённую фразу.",
                        "file": "state/ledger/alternatives.yaml",
                        "line": 1,
                        "ids": [aid],
                    }
                )
            all_ids = orefs + _BASIS_ID_RE.findall(otext)
            if not all_ids:
                findings.append(
                    {
                        "code": "options.ledger_objection_missing_id",
                        "status": "error",
                        "message": f"В alternatives.yaml у {aid} возражение не ссылается на конкретный ID основания.",
                        "file": "state/ledger/alternatives.yaml",
                        "line": 1,
                        "ids": [aid],
                    }
                )
            elif allowed_ids and not any(fid in allowed_ids for fid in all_ids):
                findings.append(
                    {
                        "code": "options.ledger_objection_not_in_based_on",
                        "status": "error",
                        "message": (
                            f"В alternatives.yaml у {aid} возражение ссылается на {', '.join(all_ids)}, "
                            "отсутствующие в DEC.based_on / ALT.supported_by."
                        ),
                        "file": "state/ledger/alternatives.yaml",
                        "line": 1,
                        "ids": [aid] + all_ids,
                    }
                )

    basis_row = _find_row("главное основание")
    if basis_row is not None and alt_cols:
        b_ln, b_cells = basis_row
        for cidx, aid, hdr in alt_cols:
            val = b_cells[cidx].strip() if cidx < len(b_cells) else ""
            if _is_sq_or_kill(aid, hdr):
                if _is_placeholder(val):
                    findings.append(
                        {
                            "code": "options.empty_main_basis",
                            "status": "error",
                            "message": f"У варианта status_quo/kill ({aid}) пустое или шаблонное «Главное основание».",
                            "file": rel,
                            "line": b_ln,
                            "ids": [aid],
                        }
                    )
            elif _is_placeholder(val):
                findings.append(
                    {
                        "code": "options.empty_main_basis",
                        "status": "error",
                        "message": f"У {aid} в «Главное основание» оставлена заглушка шаблона.",
                        "file": rel,
                        "line": b_ln,
                        "ids": [aid],
                    }
                )

    # 4. Блок «Возражение к постановке»
    if not has_v2_premise or not _has_substantive_premise_objection(active_lines):
        findings.append(
            {
                "code": "options.missing_premise_objection",
                "status": "error",
                "message": "В 04_options.md отсутствует или не заполнен блок «Возражение к постановке» — НЕ ГОТОВО к C2.",
                "file": rel,
                "line": 1,
                "ids": [],
            }
        )

    # 5. Потолок эффекта
    ceiling_row = _find_row("потолок эффекта")
    if ceiling_row is None:
        findings.append(
            {
                "code": "options.missing_effect_ceiling",
                "status": "warn",
                "message": "В v2-таблице 04_options.md не указана строка «Потолок эффекта».",
                "file": rel,
                "line": comp_table[0] if comp_table else 1,
                "ids": [],
            }
        )
    elif alt_cols:
        c_ln, c_cells = ceiling_row
        for cidx, aid, hdr in alt_cols:
            if _is_inactive_alt(aid, hdr):
                continue
            cval = c_cells[cidx].strip() if cidx < len(c_cells) else ""
            low_cval = cval.lower()
            if (
                _is_placeholder(cval)
                or "максимум сдвига" in low_cval
                or "t0-расч" in low_cval
                or not re.search(r"\d", cval)
            ):
                findings.append(
                    {
                        "code": "options.empty_effect_ceiling",
                        "status": "error",
                        "message": f"У активного варианта {aid} не заполнен числовой «Потолок эффекта».",
                        "file": rel,
                        "line": c_ln,
                        "ids": [aid],
                    }
                )

    if not findings:
        findings.append(
            {
                "code": "options.ok",
                "status": "ok",
                "message": f"{rel}: варианты, оси различия, веса, возражения и возражение к постановке корректны.",
                "file": rel,
                "line": comp_table[0] if comp_table else 1,
                "ids": [aid for _, aid, _ in alt_cols],
            }
        )

    return findings


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="M09: Проверка вариантов решения в 04_options.md и T0-расчёт потолка эффекта.")
    parser.add_argument("project", nargs="?", type=pathlib.Path, default=None, help="Путь к папке проекта")
    parser.add_argument("--ceiling", action="store_true", help="Выполнить T0-расчёт потолка эффекта")
    parser.add_argument("--total", type=float, default=None, help="Общая численность воронки")
    parser.add_argument("--passed", type=float, default=None, help="Число прошедших барьер")
    parser.add_argument("--rate-passed", type=float, default=None, help="Целевая метрика прошедших (доля или проценты)")
    parser.add_argument("--rate-blocked", type=float, default=None, help="Целевая метрика непрошедших (доля или проценты)")
    parser.add_argument("--json", action="store_true", help="Вывести результат в JSON")
    args = parser.parse_args(argv)

    if args.ceiling:
        numeric_args = (args.total, args.passed, args.rate_passed, args.rate_blocked)
        if all(x is not None for x in numeric_args):
            try:
                res = compute_effect_ceiling(args.total, args.passed, args.rate_passed, args.rate_blocked)
            except ValueError as exc:
                print(f"Ошибка расчёта потолка эффекта: {exc}", file=sys.stderr)
                return 1
        elif any(x is not None for x in numeric_args):
            parser.error("Для --ceiling с параметрами нужно указать все четыре флага: --total, --passed, --rate-passed, --rate-blocked.")
        elif args.project is not None:
            res = compute_ryadom_funnel_ceiling(args.project)
            if res is None:
                print(f"Не удалось извлечь воронку для расчёта потолка в {args.project}", file=sys.stderr)
                return 1
        else:
            parser.error("Для --ceiling передайте <project> или все четыре числовых параметра воронки.")

        if args.json:
            print(json.dumps(res, ensure_ascii=False, indent=2))
        else:
            print(
                f"ceiling_pp = {res['ceiling_pp']:.4f} п.п. "
                f"(current={res['current_rate']:.4f}, ceiling={res['ceiling_rate']:.4f}, "
                f"blocked={int(res['blocked'])}/{int(res['total'])})"
            )
        return 0

    if args.project is None:
        parser.error("Укажите путь к папке проекта или используйте --ceiling.")

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

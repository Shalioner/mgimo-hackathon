#!/usr/bin/env python3
"""M11: Проверка плана валидации 07_validation_plan.md и experiments.yaml.

Различает два типа EXP: discovery и causal.
Принцип совместимости со старыми документами:
- Если в 07_validation_plan.md нет новой v2-таблицы («Тип», «База», «MDE», «n на группу»):
  * проверка v2-таблицы возвращает `not_assessed` (без ложной ошибки на старых проектах);
  * однако если в таблице 07 есть явный A/B или причинный эксперимент (напр. eSIM EXP-002:
    A/B с «повторной покупкой» за 3 месяца без знаменателя, MDE и n), он помечается
    предупреждением (`warn`, не ложный `error`).
- Когда v2-таблица с колонкой «Тип» присутствует:
  * discovery (в т.ч. n = 6) проходит со статусом ok/пометкой «не основание для масштабирования»;
  * causal без метрики числитель/знаменатель, базы, MDE, n на группу, окна >= цикла
    поведения или порога stop из безубыточности M08 -> `error`.
"""
from __future__ import annotations

import argparse
import json
import math
import pathlib
import re
import sys
from decimal import Decimal
from typing import Dict, List, Optional, Tuple

import yaml

_TOOLS_DIR = pathlib.Path(__file__).resolve().parent.parent
if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))

import power  # noqa: E402
from econ import BREAKEVEN_VALUE_FORMAT, compute_model  # noqa: E402

_PLAN_FILE = "outputs/07_validation_plan.md"
_ECON_FILE = "state/economics.yaml"

_HISTORY_RE = re.compile(
    r"^##\s+(?:Исторический\s+снимок|История\s+до|Архив\s+до)\b",
    re.IGNORECASE,
)
_HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
_SEP_ROW_RE = re.compile(r"^\s*\|(?:\s*:?-+:?\s*\|)+\s*$")
_EXP_ID_RE = re.compile(r"\bEXP-\d{3}\b")
_CAUSAL_HINT_RE = re.compile(r"\b(?:a/b|ab-тест|а/б|контрольн\w+\s+групп|causal|причинн)\b", re.IGNORECASE)
_NUM_RE = re.compile(r"\d+(?:[.,]\d+)?")
_STOP_REF_RE = re.compile(r"\b[A-Z]{3}-\d+\b")
_STOP_PROPORTION_PATTERN = (
    r"(?<![\w.,₽$€{excluded}])"
    r"(?:({sign}\d+(?:[.,]\d+)?)\s*%|({sign}{whole}[.,]\d+)(?![\w%]|[.,]\d))"
    r"(?!\s*(?:[₽$€]|руб\w*\b|балл\w*\b|поезд\w*\b|покуп\w*\b|"
    r"секунд\w*\b|минут\w*\b|час\w*\b|день\b|дня\b|дней\b|сут\w*\b|"
    r"мес\w*\b|нед\w*\b|кварт\w*\b|год\w*\b|лет\b|п\.?\s*п\b))"
)
_STOP_PROPORTION_RE = re.compile(
    _STOP_PROPORTION_PATTERN.format(excluded=r"+\-−", sign="", whole="0"), re.IGNORECASE,
)
_LINKED_STOP_PROPORTION_RE = re.compile(
    _STOP_PROPORTION_PATTERN.format(excluded="", sign=r"[+\-−]?", whole=r"\d+"), re.IGNORECASE,
)


def _single_stop_proportion(text: str, *, linked: bool = False) -> Optional[Decimal]:
    """A Stop / Go boundary can be repeated in equivalent forms."""
    pattern = _LINKED_STOP_PROPORTION_RE if linked else _STOP_PROPORTION_RE
    values = set()
    for percent, fraction in pattern.findall(_STOP_REF_RE.sub("", text)):
        value = Decimal((percent or fraction).replace(",", ".").replace("−", "-"))
        values.add(value / 100 if percent else value)
    return next(iter(values)) if len(values) == 1 else None


def _active_lines(path: pathlib.Path) -> List[Tuple[int, str]]:
    out: List[Tuple[int, str]] = []
    in_comment = False
    for idx, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if _HISTORY_RE.match(raw.strip()):
            break
        cleaned = raw
        if in_comment:
            if "-->" in cleaned:
                cleaned = cleaned.split("-->", 1)[1]
                in_comment = False
            else:
                cleaned = ""
        cleaned = _HTML_COMMENT_RE.sub("", cleaned)
        if "<!--" in cleaned:
            cleaned = cleaned.split("<!--", 1)[0]
            in_comment = True
        out.append((idx, cleaned))
    return out


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


def _empty(val: str) -> bool:
    s = re.sub(r"[*_`]", "", val).strip()
    return not s or s in {"—", "-", "–", "нет", "не задан", "не задано", "N/A", "?", "…", "..."}


def _to_days(text: str) -> Optional[float]:
    low = text.lower()
    m = re.search(
        r"(\d+(?:[.,]\d+)?)\s*(д(?:ень|ня|ней)|дн|сут|час|недел|мес|квартал|год|лет)",
        low,
    )
    if not m:
        return None
    val = float(m.group(1).replace(",", "."))
    unit = m.group(2)
    if unit.startswith("д") or unit == "сут":
        return val
    if unit == "час":
        return val / 24.0
    if unit == "недел":
        return val * 7.0
    if unit == "мес":
        return val * 30.0
    if unit == "квартал":
        return val * 90.0
    if unit in ("год", "лет"):
        return val * 365.0
    return None


def _has_ratio_metric(text: str) -> bool:
    low = text.lower()
    if "числител" in low and "знаменател" in low:
        return True
    if re.search(r"[A-Za-zА-Яа-яЁё0-9_)\]]\s*/\s*[A-Za-zА-Яа-яЁё0-9_(]+", text):
        return True
    if re.search(r"(?:доля|конверсия|процент)\s+[^/]{4,}\s+среди\s+", low):
        return True
    return False


def _has_mde(text: str) -> bool:
    if _empty(text):
        return False
    return bool(_NUM_RE.search(text))


def _parse_sample_size(text: str) -> Optional[int]:
    if _empty(text):
        return None
    compact = re.sub(r"(?<=\d)\s+(?=\d)", "", text)
    m = re.search(r"(?<![.,\d])(-?\d+)(?![.,\d])", compact)
    if not m:
        return None
    return int(m.group(1))


def _parse_baseline_proportion(text: str) -> Optional[float]:
    if _empty(text):
        return None
    # Skip monetary or absolute non-rate baselines
    if re.search(r"[₽$€]|руб|чел|сек|мин|час|дн", text, re.IGNORECASE):
        return None
    m_pct = re.search(r"(\d+(?:[.,]\d+)?)\s*%", text)
    if m_pct:
        val = float(m_pct.group(1).replace(",", ".")) / 100.0
        return val if 0.0 < val < 1.0 else None
    m_num = re.fullmatch(r"\s*(0[.,]\d+)\s*", text)
    if m_num:
        val = float(m_num.group(1).replace(",", "."))
        return val if 0.0 < val < 1.0 else None
    return None


def _parse_mde_proportion(text: str) -> Optional[float]:
    if _empty(text):
        return None
    m_pp = re.search(r"([+-]?\d+(?:[.,]\d+)?)\s*(?:п\.?\s*п\.?|pp)\b", text, re.IGNORECASE)
    if m_pp:
        val = float(m_pp.group(1).replace(",", ".")) / 100.0
        return val if val != 0.0 else None
    m_dec = re.fullmatch(r"\s*([+-]?0[.,]\d+)\s*", text)
    if m_dec:
        val = float(m_dec.group(1).replace(",", "."))
        return val if val != 0.0 else None
    return None


def _has_breakeven_stop(stop_text: str, header: str = "") -> bool:
    low = stop_text.lower().replace("м08", "m08")
    if _empty(stop_text):
        return False
    has_stop_word = "stop" in low or "порог" in low or "безубыточ" in low or "m08" in low
    has_link = bool(re.search(r"[mм]08|безубыточ", header, re.IGNORECASE)) or any(
        k in low for k in ("безубыточ", "m08", "точк", "экономик", "окупаем", "убыт", "марж", "clm-")
    )
    has_num = bool(_NUM_RE.search(stop_text))
    return has_stop_word and has_link and has_num


def _column(headers: List[str], *keys: str) -> Optional[int]:
    for idx, header in enumerate(headers):
        lower = header.lower()
        if any(key in lower for key in keys):
            return idx
    return None


def _check_economic_stop(project: pathlib.Path, exp_table, exp_ledger: Dict[str, dict]) -> List[dict]:
    """An explicit sidecar link assigns breakeven_var to one active EXP."""
    path = project / _ECON_FILE
    if not path.is_file():
        return []

    def error(message: str, *, eid: Optional[str] = None, line: int = 0,
              code: str = "economic_stop_go_unverifiable") -> List[dict]:
        return [{
            "code": f"validation.{code}",
            "status": "error",
            "message": f"{eid + ': ' if eid else ''}{message} (основание: {_ECON_FILE}).",
            "file": _PLAN_FILE if line else _ECON_FILE,
            "line": line,
            "ids": [eid] if eid else [],
        }]

    try:
        spec = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        return error(f"Не удалось прочитать соответствие stop_go_experiment: {exc}")
    if not isinstance(spec, dict) or "stop_go_experiment" not in spec:
        return []

    eid = spec["stop_go_experiment"]
    if not isinstance(eid, str) or not _EXP_ID_RE.fullmatch(eid):
        return error(f"Некорректный stop_go_experiment {eid!r}, ожидается ID вида EXP-003")
    if exp_table is None:
        return error(f"В {_PLAN_FILE} ожидается таблица экспериментов со строкой Stop / Go", eid=eid)
    experiment = exp_ledger.get(eid)
    if experiment is None or experiment.get("status", "active") != "active":
        return error("Ожидается активная запись в state/ledger/experiments.yaml", eid=eid)

    decision = spec.get("decision")
    exp_decision = experiment.get("decision")
    if not isinstance(decision, str) or not re.fullmatch(r"DEC-\d{3}", decision) or decision != exp_decision:
        return error(f"Решение модели {decision!r}, ожидается решение EXP {exp_decision!r}", eid=eid)

    driver_id = spec.get("breakeven_var")
    if not isinstance(driver_id, str) or not driver_id.strip():
        return error("Ожидается явно заданный драйвер breakeven_var", eid=eid)
    driver_id = driver_id.strip()
    try:
        model = compute_model(spec)
    except (ValueError, TypeError, ArithmeticError) as exc:
        return error(f"Не удалось рассчитать безубыточность для {driver_id}: {exc}", eid=eid)
    breakeven = model["breakeven"]
    if breakeven is None:
        return error(f"Для {driver_id} не рассчитана точка безубыточности", eid=eid)
    driver = next(item for item in model["tornado"] if item["id"] == driver_id)
    expected = breakeven["value"]
    bounds = [driver["low_input"], driver["base_input"], driver["high_input"]]
    if not all(0 <= value <= 1 for value in [expected, *bounds]):
        return error(
            f"Для {driver_id} найдена безубыточность {expected} при входах low/base/high {bounds}, "
            "ожидаются порог и входы драйвера в [0, 1]", eid=eid,
        )

    _, headers, rows = exp_table
    exp_col = _column(headers, "exp") or 0
    stop_col = _column(headers, "stop", "порог")
    matches = [
        (line, cells)
        for line, cells in rows
        if exp_col < len(cells) and eid in _EXP_ID_RE.findall(cells[exp_col])
    ]
    if len(matches) != 1 or _EXP_ID_RE.findall(matches[0][1][exp_col]) != [eid]:
        return error(
            f"В {_PLAN_FILE} ожидается одна отдельная строка EXP для порога {driver_id} = {expected}, "
            f"найдено строк с этим ID: {len(matches)}", eid=eid,
        )
    line, cells = matches[0]
    table_stop = cells[stop_col] if stop_col is not None and stop_col < len(cells) else ""
    ledger_stop = experiment.get("stop_go")
    displayed = Decimal(format(expected, BREAKEVEN_VALUE_FORMAT))
    findings = []
    for source, text in (
        (f"таблице {_PLAN_FILE}", table_stop),
        ("поле stop_go файла state/ledger/experiments.yaml", ledger_stop),
    ):
        observed = _single_stop_proportion(text, linked=True) if isinstance(text, str) else None
        if observed is None or not 0 <= observed <= 1:
            findings.extend(error(
                f"В {source} нужен один однозначный долевой порог. Найдено {text!r}, "
                f"ожидалось {expected} для {driver_id}", eid=eid, line=line,
            ))
        elif observed != displayed and not math.isclose(float(observed), expected, rel_tol=0.0, abs_tol=1e-12):
            findings.extend(error(
                f"В {source} найден порог {observed}, ожидалось {expected} для {driver_id}",
                eid=eid, line=line, code="economic_stop_go_mismatch",
            ))
    return findings


def check(project: pathlib.Path) -> List[dict]:
    project = pathlib.Path(project)
    rel = _PLAN_FILE
    path = project / rel
    if not path.is_file():
        return [
            {
                "code": "validation.not_assessed",
                "status": "not_assessed",
                "message": f"Файл {rel} отсутствует — план проверки не проверялся.",
                "file": rel,
                "line": 0,
                "ids": [],
            }
        ] + _check_economic_stop(project, None, {})

    active_lines = _active_lines(path)
    full_text = "\n".join(raw for _, raw in active_lines)
    tables = _find_tables(active_lines)

    exp_ledger: Dict[str, dict] = {}
    exp_yaml = project / "state" / "ledger" / "experiments.yaml"
    if exp_yaml.is_file():
        try:
            for item in yaml.safe_load(exp_yaml.read_text(encoding="utf-8")) or []:
                if isinstance(item, dict) and item.get("id"):
                    exp_ledger[item["id"]] = item
        except Exception:
            pass

    exp_table = None
    for t in tables:
        hl = " ".join(h.lower() for h in t[1])
        if "exp" in hl and ("проверяет" in hl or "тип" in hl or "метрик" in hl or "stop" in hl or "mde" in hl):
            exp_table = t
            break

    has_v2_cols = False
    if exp_table is not None:
        hl = " ".join(h.lower() for h in exp_table[1])
        has_v2_cols = "тип" in hl or any(k in hl for k in ("mde", "n на группу", "цикл поведения"))

    findings = _check_economic_stop(project, exp_table, exp_ledger)

    if not has_v2_cols:
        flagged_legacy = False
        if exp_table is not None:
            for rln, cells in exp_table[2]:
                row_txt = " ".join(cells)
                exps = _EXP_ID_RE.findall(row_txt)
                eid = exps[0] if exps else ""
                l_obj = exp_ledger.get(eid, {})
                comp_txt = str(l_obj.get("comparison", "")) + " " + row_txt
                if _CAUSAL_HINT_RE.search(comp_txt):
                    flagged_legacy = True
                    missing_items: List[str] = []
                    if not _has_ratio_metric(str(l_obj.get("metric", "")) + " " + row_txt):
                        missing_items.append("метрика числитель/знаменатель")
                    missing_items.append("база, MDE и n на группу (tools/power.py)")
                    if re.search(r"повторн\w+\s+покупк", row_txt, re.IGNORECASE) and "3 мес" in row_txt.lower():
                        missing_items.append("окно 3 мес может быть короче цикла повторных поездок")
                    findings.append(
                        {
                            "code": "validation.causal_incomplete_legacy",
                            "status": "warn",
                            "message": (
                                f"{eid or 'EXP'} использует причинный/A/B дизайн в legacy-таблице 07 без v2-полей: "
                                + "; ".join(missing_items)
                                + "."
                            ),
                            "file": rel,
                            "line": rln,
                            "ids": exps,
                        }
                    )

        if re.search(r"\bn\s*=\s*6\b|\b6\s+сем", full_text, re.IGNORECASE):
            findings.append(
                {
                    "code": "validation.discovery_small_n",
                    "status": "ok",
                    "message": "Разведочный план (discovery, n=6): допустимо для диагностики; не основание для масштабирования.",
                    "file": rel,
                    "line": 1,
                    "ids": sorted(exp_ledger.keys()),
                }
            )

        if not flagged_legacy:
            findings.append(
                {
                    "code": "validation.v2_not_assessed",
                    "status": "not_assessed",
                    "message": (
                        f"В {rel} отсутствуют v2-колонки («Тип», «База», «MDE», «n на группу») — "
                        "причинная строгость оценена как not_assessed."
                    ),
                    "file": rel,
                    "line": 0,
                    "ids": [],
                }
            )
        return findings

    assert exp_table is not None
    tline, headers, rows = exp_table

    def _col(*keys: str) -> Optional[int]:
        return _column(headers, *keys)

    exp_col = _col("exp") or 0
    type_col = _col("тип")
    metric_col = _col("метрик", "числител")
    comp_col = _col("с чем сравнива", "сравн", "контрол", "comparison")
    base_col = _col("база", "baseline")
    mde_col = _col("mde")
    n_col = _col("n на группу", "выборк", "размер")
    win_col = _col("срок", "окно")
    cycle_col = _col("цикл")
    stop_col = _col("stop", "порог")

    valid_rows = [r for r in rows if any(not _empty(c) for c in r[1])]
    if not valid_rows:
        return findings + [
            {
                "code": "validation.empty_table",
                "status": "error",
                "message": f"В v2-таблице экспериментов {rel} нет ни одной заполненной строки.",
                "file": rel,
                "line": tline,
                "ids": [],
            }
        ]

    for rln, cells in valid_rows:
        eid_raw = cells[exp_col] if exp_col < len(cells) else ""
        exps = _EXP_ID_RE.findall(eid_raw)
        eid = exps[0] if exps else eid_raw.strip()

        etype = cells[type_col].strip().lower() if type_col is not None and type_col < len(cells) else ""
        metric_val = cells[metric_col] if metric_col is not None and metric_col < len(cells) else ""
        comp_val = cells[comp_col] if comp_col is not None and comp_col < len(cells) else ""
        base_val = cells[base_col] if base_col is not None and base_col < len(cells) else ""
        mde_val = cells[mde_col] if mde_col is not None and mde_col < len(cells) else ""
        n_val = cells[n_col] if n_col is not None and n_col < len(cells) else ""
        win_val = cells[win_col] if win_col is not None and win_col < len(cells) else ""
        cycle_val = cells[cycle_col] if cycle_col is not None and cycle_col < len(cells) else ""
        stop_val = cells[stop_col] if stop_col is not None and stop_col < len(cells) else ""
        registered_exp = exp_ledger.get(eid, {})
        registered_stop = registered_exp.get("stop_go")
        if len(exps) == 1 and registered_exp.get("status", "active") == "active" and isinstance(registered_stop, str):
            observed = _single_stop_proportion(stop_val)
            expected = _single_stop_proportion(registered_stop)
            if observed is not None and expected is not None and observed != expected:
                findings.append(
                    {
                        "code": "validation.stop_go_threshold_mismatch",
                        "status": "error",
                        "message": (
                            f"{eid}: долевой порог Stop / Go в таблице {observed} расходится "
                            f"с {expected} в state/ledger/experiments.yaml, поле stop_go."
                        ),
                        "file": rel,
                        "line": rln,
                        "ids": exps,
                    }
                )

        if "discovery" in etype or "развед" in etype:
            findings.append(
                {
                    "code": "validation.discovery_ok",
                    "status": "ok",
                    "message": (
                        f"{eid} (discovery{', n=' + n_val.strip() if not _empty(n_val) else ''}): "
                        "разведочный эксперимент допустим; не основание для масштабирования."
                    ),
                    "file": rel,
                    "line": rln,
                    "ids": exps,
                }
            )
            continue

        if "causal" in etype or "причин" in etype or "a/b" in etype:
            row_errors = False
            if not _has_ratio_metric(metric_val):
                row_errors = True
                findings.append(
                    {
                        "code": "validation.missing_metric_ratio",
                        "status": "error",
                        "message": f"{eid} (causal): метрика должна быть задана как числитель/знаменатель.",
                        "file": rel,
                        "line": rln,
                        "ids": exps,
                    }
                )
            if comp_col is None or _empty(comp_val):
                row_errors = True
                findings.append(
                    {
                        "code": "validation.missing_comparison",
                        "status": "error",
                        "message": f"{eid} (causal): не заполнено «С чем сравниваем».",
                        "file": rel,
                        "line": rln,
                        "ids": exps,
                    }
                )
            if _empty(base_val) or not _NUM_RE.search(base_val):
                row_errors = True
                findings.append(
                    {
                        "code": "validation.missing_baseline",
                        "status": "error",
                        "message": f"{eid} (causal): не указана числовая база (baseline).",
                        "file": rel,
                        "line": rln,
                        "ids": exps,
                    }
                )
            if not _has_mde(mde_val):
                row_errors = True
                findings.append(
                    {
                        "code": "validation.missing_mde",
                        "status": "error",
                        "message": f"{eid} (causal): не задан MDE.",
                        "file": rel,
                        "line": rln,
                        "ids": exps,
                    }
                )
            n_stated = _parse_sample_size(n_val)
            if n_stated is None or n_stated < 2:
                row_errors = True
                findings.append(
                    {
                        "code": "validation.missing_sample_size",
                        "status": "error",
                        "message": f"{eid} (causal): размер выборки n на группу должен быть целым числом >= 2.",
                        "file": rel,
                        "line": rln,
                        "ids": exps,
                    }
                )
            else:
                p_base = _parse_baseline_proportion(base_val)
                mde_prop = _parse_mde_proportion(mde_val)
                if p_base is not None and mde_prop is not None and 0.0 < p_base + mde_prop < 1.0:
                    row_joined = " ".join(cells)
                    alt_mode = (
                        "one-sided"
                        if re.search(r"one[-\s]?sided|односторон", row_joined, re.IGNORECASE)
                        else "two-sided"
                    )
                    req = power.sample_size_two_proportions(p_base, mde_prop, alternative=alt_mode)
                    n_req = int(req["n_per_group"])
                    if n_stated < n_req:
                        row_errors = True
                        findings.append(
                            {
                                "code": "validation.underpowered_sample_size",
                                "status": "error",
                                "message": (
                                    f"{eid} (causal): указано n={n_stated} на группу, что меньше расчётного "
                                    f"n={n_req} по tools/power.py (p={p_base}, MDE={mde_prop:+.4g}, {alt_mode})."
                                ),
                                "file": rel,
                                "line": rln,
                                "ids": exps,
                            }
                        )

            win_days = _to_days(win_val)
            cycle_days = _to_days(cycle_val) if not _empty(cycle_val) else None
            if cycle_col is None or _empty(cycle_val) or cycle_days is None:
                row_errors = True
                findings.append(
                    {
                        "code": "validation.missing_behavior_cycle",
                        "status": "error",
                        "message": f"{eid} (causal): не указан цикл поведения для сравнения с окном наблюдения.",
                        "file": rel,
                        "line": rln,
                        "ids": exps,
                    }
                )
            if _empty(win_val) or win_days is None:
                row_errors = True
                findings.append(
                    {
                        "code": "validation.missing_window",
                        "status": "error",
                        "message": f"{eid} (causal): не указано окно наблюдения.",
                        "file": rel,
                        "line": rln,
                        "ids": exps,
                    }
                )
            elif win_days is not None and cycle_days is not None and win_days < cycle_days:
                row_errors = True
                findings.append(
                    {
                        "code": "validation.window_shorter_than_cycle",
                        "status": "error",
                        "message": (
                            f"{eid} (causal): окно наблюдения ({win_val}) короче цикла поведения ({cycle_val})."
                        ),
                        "file": rel,
                        "line": rln,
                        "ids": exps,
                    }
                )

            if not _has_breakeven_stop(stop_val, headers[stop_col] if stop_col is not None else ""):
                row_errors = True
                findings.append(
                    {
                        "code": "validation.missing_breakeven_stop",
                        "status": "error",
                        "message": f"{eid} (causal): порог stop должен быть явно выведен из безубыточности (M08).",
                        "file": rel,
                        "line": rln,
                        "ids": exps,
                    }
                )

            if not row_errors:
                findings.append(
                    {
                        "code": "validation.causal_ok",
                        "status": "ok",
                        "message": f"{eid} (causal): числитель/знаменатель, база, MDE, n, окно и порог stop корректны.",
                        "file": rel,
                        "line": rln,
                        "ids": exps,
                    }
                )
        else:
            findings.append(
                {
                    "code": "validation.unknown_type",
                    "status": "error",
                    "message": f"{eid}: тип эксперимента должен быть 'discovery' или 'causal', получено «{etype}».",
                    "file": rel,
                    "line": rln,
                    "ids": exps,
                }
            )

    return findings


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="M11: Проверка плана валидации (мощность, MDE, окно).")
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

#!/usr/bin/env python3
"""M10: Проверка сегментов и согласованности коммуникаций с воронкой (05 + 06 + promises.yaml).

Принцип совместимости со старыми документами:
- Отсутствие новых v2-таблиц («Сегмент × задача × ...» в 05, «Этап воронки → ...» в 06)
  даёт статус `not_assessed` по соответствующей табличной проверке.
- При наличии v2-таблиц проверяются:
  * 05: >= 2 сегментов; все непервичные сегменты отражены в «Осознанные отказы»;
  * 06: у каждого PRM есть этап воронки, у каждого канала — метрика этапа;
    этап с наибольшим падением закрыт сообщением или осознанным отказом с причиной;
    сообщение формулирует результат для клиента + подтверждение, а не просто «функция есть»;
- Независимо от v2-таблиц, если присутствуют 05/06/promises.yaml:
  * перекрёстная сверка «Выбранное направление» (05) ↔ запреты/ограничения обещаний в 06
    (напр. eSIM 05:22 ↔ 06:25);
  * 0 активных PRM в promises.yaml → флаг «коммуникация не готова» (блокирует C3).
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
_SEP_ROW_RE = re.compile(r"^\s*\|(?:\s*:?-+:?\s*\|)+\s*$")
_HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
_PRM_ID_RE = re.compile(r"\bPRM-\d{3}\b")
_PRM_LEAD_RE = re.compile(r"^\s*(?:\[?PRM-\d{3}\]?)\s*[:—\-]*\s*", re.IGNORECASE)
_BARRIER_ID_RE = re.compile(r"\b(?:CLM|HYP|PAIN|SRC)-\d{3}\b")
_PROOF_ID_RE = re.compile(r"\b(?:CLM|EXP)-\d{3}\b")
_DROP_NUM_RE = re.compile(r"(?:падени[ея]|потер[яи]|отвал|уход|drop)[^0-9]{0,15}(\d+(?:[.,]\d+)?)\s*%", re.IGNORECASE)
_LOCAL_NEG_LEFT_RE = re.compile(
    r"(?:^|[\s.;,])(?:не|нет|без|не\s+обещаем|не\s+строим|не\s+делаем|не\s+используем|"
    r"не\s+заявляем|отложен[аоы]?|исключ[её]н\w*|запрещ\w*)(?:\s+[а-яёa-z0-9\-]+){0,2}\s*$",
    re.IGNORECASE,
)
_LOCAL_NEG_RIGHT_RE = re.compile(
    r"^(?:\s+[а-яёa-z0-9\-]+){0,4}\s+(?:не\s+установлен\w*|не\s+подтвержден\w*|не\s+строим|"
    r"не\s+делаем|не\s+обещаем|отложен[аоы]?|исключ[её]н\w*|отсутству\w*)\b",
    re.IGNORECASE,
)
_FEATURE_ONLY_RE = re.compile(
    r"^\s*[«\"]?(?:у\s+нас\s+есть|есть\s+функция|доступна\s+функция|функция\s+есть|добавлена\s+функция|имеется\s+функция)\b",
    re.IGNORECASE,
)
_BRAND_OR_META_QUOTES = (
    "рядом",
    "мегафон",
    "осознанные отказ",
    "выбранное направлени",
    "роль каналов",
    "чего мы не обещаем",
)


def _split_row(line: str) -> List[str]:
    stripped = line.strip()
    if stripped.startswith("|"):
        stripped = stripped[1:]
    if stripped.endswith("|"):
        stripped = stripped[:-1]
    return [c.strip() for c in stripped.split("|")]


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


def _extract_h2(numbered: List[Tuple[int, str]], title_re: re.Pattern[str]) -> Tuple[Optional[int], List[Tuple[int, str]]]:
    start: Optional[int] = None
    body: List[Tuple[int, str]] = []
    for ln, raw in numbered:
        st = raw.strip()
        if start is None:
            if title_re.match(st):
                start = ln
            continue
        if _H2_RE.match(st):
            break
        body.append((ln, raw))
    return start, body


def _find_tables(numbered: List[Tuple[int, str]]) -> List[Tuple[int, List[str], List[Tuple[int, List[str]]]]]:
    tables = []
    i = 0
    while i < len(numbered) - 1:
        ln, raw = numbered[i]
        nxt = numbered[i + 1][1]
        if raw.strip().startswith("|") and _SEP_ROW_RE.match(nxt):
            headers = _split_row(raw)
            rows: List[Tuple[int, List[str]]] = []
            j = i + 2
            while j < len(numbered) and numbered[j][1].strip().startswith("|"):
                rln, rraw = numbered[j]
                if not _SEP_ROW_RE.match(rraw):
                    rows.append((rln, _split_row(rraw)))
                j += 1
            tables.append((ln, headers, rows))
            i = j
        else:
            i += 1
    return tables


def _empty_cell(val: str) -> bool:
    s = re.sub(r"[*_`]", "", val).strip()
    if not s or s in {"—", "-", "–", "нет", "N/A", "?", "…", "..."}:
        return True
    if _PRM_ID_RE.search(s) or _BARRIER_ID_RE.search(s) or _PROOF_ID_RE.search(s):
        return False
    # Unfilled bracketed template stubs like `[PRM-…]`, `[HYP-… / PAIN-…]`, `[CLM-…]`
    if re.fullmatch(r"(?:\[[^\]]*(?:…|\.{3}|id)[^\]]*\]\s*)+", s, re.IGNORECASE):
        return True
    return False


def _strip_prm_prefix(text: str) -> str:
    s = _PRM_LEAD_RE.sub("", text.strip())
    s = s.strip().strip("«»\"'` ")
    return s


def _check_segments_05(lines_05: List[Tuple[int, str]]) -> Tuple[List[dict], bool]:
    rel = "outputs/05_product_strategy.md"
    tables = _find_tables(lines_05)
    seg_table = None
    for t in tables:
        hl = " ".join(h.lower() for h in t[1])
        if "сегмент" in hl and "приоритет" in hl and (
            "задач" in hl or "jtbd" in hl or "барьер" in hl or "альтернатив" in hl or "размер" in hl
        ):
            seg_table = t
            break

    if seg_table is None:
        return [
            {
                "code": "comms.segments_not_assessed",
                "status": "not_assessed",
                "message": f"В {rel} отсутствует v2-таблица сегментов (Сегмент × задача × приоритет).",
                "file": rel,
                "line": 0,
                "ids": [],
            }
        ], False

    tline, headers, rows = seg_table
    hl = [h.lower() for h in headers]

    def _col(*keys: str) -> Optional[int]:
        for idx, h in enumerate(hl):
            if any(k in h for k in keys):
                return idx
        return None

    seg_col = _col("сегмент") or 0
    task_col = _col("задач", "jtbd")
    prio_col = _col("приоритет")

    findings: List[dict] = []
    valid_rows = [r for r in rows if any(not _empty_cell(c) for c in r[1])]
    if len(valid_rows) < 2:
        findings.append(
            {
                "code": "comms.too_few_segments",
                "status": "error",
                "message": f"В таблице сегментов {rel} указано {len(valid_rows)} сегментов; требуется минимум 2.",
                "file": rel,
                "line": tline,
                "ids": [],
            }
        )

    # Check non-primary segments in "Осознанные отказы"
    _, ref_body = _extract_h2(lines_05, re.compile(r"^##\s+Осознанные\s+отказы\b", re.IGNORECASE))
    ref_text = " ".join(raw.lower().replace("ё", "е") for _, raw in ref_body)

    for rline, cells in valid_rows:
        seg_name = cells[seg_col].strip() if seg_col < len(cells) else ""
        prio_val = cells[prio_col].strip().lower() if prio_col is not None and prio_col < len(cells) else ""
        task_val = cells[task_col].strip() if task_col is not None and task_col < len(cells) else ""
        if not seg_name:
            continue
        if task_col is not None and _empty_cell(task_val):
            findings.append(
                {
                    "code": "comms.segment_missing_task",
                    "status": "error",
                    "message": f"У сегмента «{seg_name}» в {rel} не заполнена задача (JTBD).",
                    "file": rel,
                    "line": rline,
                    "ids": [],
                }
            )
        is_primary = any(k in prio_val for k in ("первичн", "основн", "primary", "p1", "главн"))
        if not is_primary:
            tokens = [
                w.lower().replace("ё", "е")
                for w in re.findall(r"[A-Za-zА-Яа-яЁё0-9\-]+", seg_name)
                if len(w) >= 4
            ]
            seg_norm = seg_name.lower().replace("ё", "е")
            matched = seg_norm in ref_text or any(tok[:5] in ref_text for tok in tokens)
            if not matched:
                findings.append(
                    {
                        "code": "comms.non_primary_not_in_refusals",
                        "status": "error",
                        "message": (
                            f"Непервичный сегмент «{seg_name}» (приоритет: {prio_val or 'не указан'}) "
                            "не зафиксирован в разделе «Осознанные отказы»."
                        ),
                        "file": rel,
                        "line": rline,
                        "ids": [],
                    }
                )

    return findings, True


def _check_channels_06(lines_06: List[Tuple[int, str]], has_fun: bool) -> List[dict]:
    rel = "outputs/06_comms_strategy.md"
    start, chan_body = _extract_h2(lines_06, re.compile(r"^##\s+Роль\s+каналов\b", re.IGNORECASE))
    if start is None or not chan_body:
        return []
    tables = _find_tables(chan_body)
    chan_table = None
    for t in tables:
        hl = " ".join(h.lower() for h in t[1])
        if "канал" in hl:
            chan_table = t
            break
    if chan_table is None:
        return []

    tline, headers, rows = chan_table
    hl = [h.lower() for h in headers]
    chan_col = next((i for i, h in enumerate(hl) if "канал" in h), None)
    metric_col = next((i for i, h in enumerate(hl) if "метрик" in h), None)

    # Legacy 3-col "Роль каналов" without funnel table and without metric column stays not_assessed
    if metric_col is None and not has_fun:
        return []

    findings: List[dict] = []
    if metric_col is None:
        findings.append(
            {
                "code": "comms.channel_without_metric",
                "status": "error",
                "message": "В таблице «Роль каналов» отсутствует колонка «Метрика этапа».",
                "file": rel,
                "line": tline,
                "ids": [],
            }
        )
        return findings

    non_empty_rows = [r for r in rows if any(not _empty_cell(c) for c in r[1])]
    if not non_empty_rows:
        findings.append(
            {
                "code": "comms.channels_table_empty",
                "status": "error",
                "message": "Таблица «Роль каналов» не содержит ни одного заполненного канала.",
                "file": rel,
                "line": tline,
                "ids": [],
            }
        )
        return findings

    for rline, cells in non_empty_rows:
        ch_name = cells[chan_col].strip() if chan_col is not None and chan_col < len(cells) else ""
        met_val = cells[metric_col].strip() if metric_col < len(cells) else ""
        if _empty_cell(ch_name):
            continue
        if _empty_cell(met_val):
            findings.append(
                {
                    "code": "comms.channel_without_metric",
                    "status": "error",
                    "message": f"В разделе «Роль каналов» у канала «{ch_name}» не указана метрика этапа.",
                    "file": rel,
                    "line": rline,
                    "ids": [],
                }
            )
    return findings


def _check_funnel_06(project: pathlib.Path, lines_06: List[Tuple[int, str]]) -> Tuple[List[dict], bool]:
    rel = "outputs/06_comms_strategy.md"
    tables = _find_tables(lines_06)
    funnel_table = None
    for t in tables:
        hl_cols = [h.lower() for h in t[1]]
        hl = " ".join(hl_cols)
        has_stage_header = any("этап воронки" in h or ("этап" in h and "метрик" not in h) for h in hl_cols)
        is_stage_v2 = has_stage_header and any(
            k in hl for k in ("барьер", "сообщени", "prm", "канал", "метрик", "подтвержд", "результат")
        )
        is_prm_channel_v2 = any(k in hl for k in ("prm", "сообщени", "барьер")) and any(
            k in hl for k in ("канал", "метрик", "результат")
        )
        if is_stage_v2 or is_prm_channel_v2:
            funnel_table = t
            break

    if funnel_table is None:
        return [
            {
                "code": "comms.funnel_not_assessed",
                "status": "not_assessed",
                "message": f"В {rel} отсутствует v2-таблица воронки (Этап воронки → барьер → PRM → канал → метрика).",
                "file": rel,
                "line": 0,
                "ids": [],
            }
        ], False

    tline, headers, rows = funnel_table
    hl = [h.lower() for h in headers]

    def _col(*keys: str) -> Optional[int]:
        for idx, h in enumerate(hl):
            if any(k in h for k in keys):
                return idx
        return None

    stage_col = next(
        (i for i, h in enumerate(hl) if "этап воронки" in h or ("этап" in h and "метрик" not in h)),
        None,
    )
    barrier_col = _col("барьер")
    msg_col = _col("сообщени", "prm")
    outcome_col = _col("результат")
    chan_col = _col("канал")
    metric_col = _col("метрик")
    proof_col = _col("подтвержд")

    findings: List[dict] = []

    missing_cols: List[str] = []
    if stage_col is None:
        missing_cols.append("Этап воронки")
    if barrier_col is None:
        missing_cols.append("Барьер (ID)")
    if msg_col is None:
        missing_cols.append("Сообщение (PRM)")
    if chan_col is None:
        missing_cols.append("Канал")
    if metric_col is None:
        missing_cols.append("Метрика этапа")
    if proof_col is None:
        missing_cols.append("Подтверждение")
    if missing_cols:
        findings.append(
            {
                "code": "comms.incomplete_funnel_table",
                "status": "error",
                "message": f"В v2-таблице воронки {rel} отсутствуют обязательные колонки: {', '.join(missing_cols)}.",
                "file": rel,
                "line": tline,
                "ids": [],
            }
        )

    # Load active PRM messages from promises.yaml if referenced
    prm_map: Dict[str, dict] = {}
    promises_path = project / "state" / "ledger" / "promises.yaml"
    if promises_path.is_file():
        try:
            for item in yaml.safe_load(promises_path.read_text(encoding="utf-8")) or []:
                if isinstance(item, dict) and item.get("id"):
                    prm_map[item["id"]] = item
        except Exception:
            pass

    max_drop_val: float = -1.0
    max_drop_rows: List[Tuple[int, List[str]]] = []

    valid_rows = [r for r in rows if any(not _empty_cell(c) for c in r[1])]
    if not valid_rows:
        findings.append(
            {
                "code": "comms.empty_funnel_table",
                "status": "error",
                "message": f"В v2-таблице воронки {rel} нет ни одной заполненной строки.",
                "file": rel,
                "line": tline,
                "ids": [],
            }
        )
        return findings, True

    for rline, cells in valid_rows:
        stage_val = cells[stage_col] if stage_col is not None and stage_col < len(cells) else ""
        barrier_val = cells[barrier_col] if barrier_col is not None and barrier_col < len(cells) else ""
        msg_val = cells[msg_col] if msg_col is not None and msg_col < len(cells) else ""
        outcome_val = cells[outcome_col] if outcome_col is not None and outcome_col < len(cells) else ""
        chan_val = cells[chan_col] if chan_col is not None and chan_col < len(cells) else ""
        metric_val = cells[metric_col] if metric_col is not None and metric_col < len(cells) else ""
        proof_val = cells[proof_col] if proof_col is not None and proof_col < len(cells) else ""

        row_joined = " ".join(cells)
        prms = _PRM_ID_RE.findall(row_joined)
        is_refusal = bool(re.search(r"\bотказ\b", row_joined, re.IGNORECASE))
        has_refusal_reason = is_refusal and bool(
            re.search(r"(?:потому что|причина|так как|поскольку|не трогаем|вне скоупа|:)", row_joined, re.IGNORECASE)
        )

        # PRM without stage
        if prms and (stage_col is None or _empty_cell(stage_val)):
            findings.append(
                {
                    "code": "comms.prm_without_stage",
                    "status": "error",
                    "message": f"У обещания {', '.join(prms)} не заполнен этап воронки.",
                    "file": rel,
                    "line": rline,
                    "ids": prms,
                }
            )

        # Channel without metric
        if chan_col is not None and not _empty_cell(chan_val) and not re.match(
            r"^(?:отказ|нет)\b", chan_val.strip(), re.IGNORECASE
        ):
            if metric_col is None or _empty_cell(metric_val):
                findings.append(
                    {
                        "code": "comms.channel_without_metric",
                        "status": "error",
                        "message": f"У канала «{chan_val}» не указана метрика этапа.",
                        "file": rel,
                        "line": rline,
                        "ids": prms,
                    }
                )

        # Track drop magnitudes / largest drop tag
        m_drop = _DROP_NUM_RE.search(stage_val + " " + barrier_val)
        if m_drop:
            val = float(m_drop.group(1).replace(",", "."))
            if val > max_drop_val:
                max_drop_val = val
                max_drop_rows = [(rline, cells)]
            elif val == max_drop_val:
                max_drop_rows.append((rline, cells))
        elif re.search(r"(?:наибольш|крупнейш|главн\w+\s+паден)", row_joined, re.IGNORECASE):
            max_drop_val = max(max_drop_val, 100.0)
            max_drop_rows.append((rline, cells))

        # Barrier ID required unless refusal
        if not is_refusal and not _BARRIER_ID_RE.search(barrier_val):
            findings.append(
                {
                    "code": "comms.missing_barrier_id",
                    "status": "error",
                    "message": f"В строке воронки «{stage_val}» у барьера отсутствует ID (CLM/HYP/PAIN).",
                    "file": rel,
                    "line": rline,
                    "ids": prms,
                }
            )

        # Message format check: client outcome + CLM/EXP proof (NOT HYP!), not feature-only
        if prms and not is_refusal:
            texts_to_check = [_strip_prm_prefix(msg_val)]
            has_proof = bool(_PROOF_ID_RE.search(proof_val) or _PROOF_ID_RE.search(msg_val))
            for pid in prms:
                pobj = prm_map.get(pid)
                if pobj:
                    pmsg = _strip_prm_prefix(str(pobj.get("message", "")))
                    if pmsg:
                        texts_to_check.append(pmsg)
                    pproof = pobj.get("proof")
                    if isinstance(pproof, list):
                        pproof_str = " ".join(str(x) for x in pproof)
                    else:
                        pproof_str = str(pproof or "")
                    if _PROOF_ID_RE.search(pproof_str):
                        has_proof = True

            has_feature_only = any(_FEATURE_ONLY_RE.match(t) for t in texts_to_check if t)
            if outcome_col is not None:
                outcome_clean = _strip_prm_prefix(outcome_val)
                if _empty_cell(outcome_val) or _FEATURE_ONLY_RE.match(outcome_clean):
                    has_feature_only = True
            elif not any(texts_to_check):
                has_feature_only = True

            if has_feature_only or not has_proof:
                findings.append(
                    {
                        "code": "comms.message_lacks_outcome_or_proof",
                        "status": "error",
                        "message": (
                            f"Сообщение {', '.join(prms)} должно описывать результат для клиента и иметь подтверждение "
                            "CLM/EXP (а не констатировать «функция есть» или ссылаться только на HYP)."
                        ),
                        "file": rel,
                        "line": rline,
                        "ids": prms,
                    }
                )
        elif is_refusal and not has_refusal_reason:
            findings.append(
                {
                    "code": "comms.refusal_without_reason",
                    "status": "error",
                    "message": f"Для этапа «{stage_val}» указан отказ от коммуникации без причины.",
                    "file": rel,
                    "line": rline,
                    "ids": [],
                }
            )
        elif not prms and not is_refusal:
            findings.append(
                {
                    "code": "comms.row_without_prm_or_refusal",
                    "status": "error",
                    "message": f"В строке воронки «{stage_val}» должен быть указан [PRM-NNN] или явный отказ с причиной.",
                    "file": rel,
                    "line": rline,
                    "ids": [],
                }
            )

    # Verify largest drop row has either PRM or refusal with reason
    for dline, dcells in max_drop_rows:
        djoined = " ".join(dcells)
        has_prm = bool(_PRM_ID_RE.search(djoined))
        has_refusal_with_reason = bool(re.search(r"\bотказ\b", djoined, re.IGNORECASE)) and bool(
            re.search(r"(?:потому что|причина|так как|поскольку|:)", djoined, re.IGNORECASE)
        )
        if not (has_prm or has_refusal_with_reason):
            findings.append(
                {
                    "code": "comms.largest_drop_uncovered",
                    "status": "error",
                    "message": "Этап воронки с наибольшим падением не закрыт сообщением (PRM) и не имеет отказа с причиной.",
                    "file": rel,
                    "line": dline,
                    "ids": [],
                }
            )

    return findings, True


def _is_locally_negated(clause: str, start: int, end: int) -> bool:
    left = clause[max(0, start - 50) : start]
    right = clause[end : min(len(clause), end + 55)]
    if _LOCAL_NEG_LEFT_RE.search(left):
        return True
    if _LOCAL_NEG_RIGHT_RE.match(right):
        return True
    return False


def _check_05_06_contradiction(
    lines_05: List[Tuple[int, str]],
    lines_06: List[Tuple[int, str]],
) -> List[dict]:
    """Сверяет положительные утверждения 05 «Выбранное направление» с запретами 06."""
    _, dir_body = _extract_h2(lines_05, re.compile(r"^##\s+Выбранное\s+направление\b", re.IGNORECASE))
    if not dir_body:
        return []

    _, no_prom_body = _extract_h2(lines_06, re.compile(r"^##\s+Чего\s+мы\s+не\s+обещаем\b", re.IGNORECASE))

    prohibition_lines: List[Tuple[int, str]] = list(no_prom_body)
    for ln, raw in lines_06:
        if (ln, raw) in prohibition_lines:
            continue
        if re.search(r"(?:не\s+использовать|проверять\s+отдельно|не\s+обещаем|не\s+заявляем)", raw, re.IGNORECASE):
            prohibition_lines.append((ln, raw))

    prohibited_tokens: List[Tuple[int, str, str]] = []  # (line06, exact_or_stem, label)
    for ln06, raw06 in prohibition_lines:
        # Quoted phrases like «за минуты» or "самый дешёвый интернет" — skip brand/product/section names
        for qm in re.finditer(r"[«\"]([^»\"]{3,45})[»\"]", raw06):
            phrase = qm.group(1).strip().lower().replace("ё", "е")
            if not phrase:
                continue
            if any(b in phrase for b in _BRAND_OR_META_QUOTES):
                continue
            # Skip quoted product reference right after preposition "в «...»" / "для «...»" / "у «...»"
            prefix = raw06[max(0, qm.start() - 6) : qm.start()].lower()
            if re.search(r"(?:^|\s)(?:в|для|у|про|о)\s+$", prefix):
                continue
            prohibited_tokens.append((ln06, phrase, phrase))

        # Phrases right before "проверять отдельно" (e.g. "автоактивацию проверять отдельно")
        for am in re.finditer(r"([А-Яа-яЁёA-Za-z\-]{6,})\s+проверять\s+отдельно", raw06, re.IGNORECASE):
            word = am.group(1).strip().lower().replace("ё", "е")
            stem = word[: max(6, len(word) - 2)]
            prohibited_tokens.append((ln06, stem, word))

        # Concrete forbidden noun phrases after "Не обещаем ..."
        m_no = re.search(r"не\s+обещаем\s+([^.—;\n]+)", raw06, re.IGNORECASE)
        if m_no:
            frag = m_no.group(1)
            for part in re.split(r"\s*(?:,| и )\s*", frag):
                words = [
                    w.lower().replace("ё", "е")
                    for w in re.findall(r"[А-Яа-яЁёA-Za-z]{4,}", part)
                    if w.lower() not in {"первого", "релиза", "первый", "релиз", "в", "на", "по", "до", "для", "при", "travel", "esim"}
                ]
                if len(words) == 1 and len(words[0]) >= 6:
                    stem = words[0][: max(5, len(words[0]) - 2)]
                    prohibited_tokens.append((ln06, stem, words[0]))
                elif len(words) >= 2:
                    stems = " ".join(w[: max(4, len(w) - 2)] for w in words[:2])
                    prohibited_tokens.append((ln06, stems, " ".join(words[:2])))

    if not prohibited_tokens:
        return []

    findings: List[dict] = []
    seen_pairs: set[Tuple[int, int, str]] = set()
    for ln05, raw05 in dir_body:
        low05 = raw05.lower().replace("ё", "е")
        clauses = re.split(r"[.;,]", low05)
        for clause in clauses:
            if not clause.strip():
                continue
            for ln06, pat, label in prohibited_tokens:
                if " " in pat:
                    parts = pat.split()
                    spans = [re.search(re.escape(p), clause) for p in parts]
                    if all(spans):
                        start = min(m.start() for m in spans if m is not None)
                        end = max(m.end() for m in spans if m is not None)
                        if not _is_locally_negated(clause, start, end):
                            key = (ln05, ln06, label)
                            if key not in seen_pairs:
                                seen_pairs.add(key)
                                findings.append(
                                    {
                                        "code": "comms.direction_forbidden_mismatch",
                                        "status": "error",
                                        "message": (
                                            f"Противоречие 05:{ln05} ↔ 06:{ln06}: в «Выбранном направлении» (05) указано «{label}», "
                                            f"хотя в 06:{ln06} это запрещено обещать / требует отдельной проверки."
                                        ),
                                        "file": "outputs/05_product_strategy.md",
                                        "line": ln05,
                                        "ids": [],
                                    }
                                )
                else:
                    for m in re.finditer(re.escape(pat), clause):
                        if not _is_locally_negated(clause, m.start(), m.end()):
                            key = (ln05, ln06, label)
                            if key not in seen_pairs:
                                seen_pairs.add(key)
                                findings.append(
                                    {
                                        "code": "comms.direction_forbidden_mismatch",
                                        "status": "error",
                                        "message": (
                                            f"Противоречие 05:{ln05} ↔ 06:{ln06}: в «Выбранном направлении» (05) указано «{label}», "
                                            f"хотя в 06:{ln06} это запрещено обещать / требует отдельной проверки."
                                        ),
                                        "file": "outputs/05_product_strategy.md",
                                        "line": ln05,
                                        "ids": [],
                                    }
                                )
                            break
    return findings


def _check_promises_active(project: pathlib.Path, has_06: bool) -> Tuple[List[dict], bool]:
    rel = "state/ledger/promises.yaml"
    path = project / rel
    if not has_06:
        # Before step 06 (e.g. fresh `ledger.py init` project), 0 PRMs is expected
        return [], False

    if not path.is_file():
        return [
            {
                "code": "comms.comms_not_ready",
                "status": "error",
                "message": f"При наличии 06_comms_strategy.md отсутствует {rel} — коммуникация не готова (блокирует C3).",
                "file": rel,
                "line": 1,
                "ids": [],
            }
        ], True

    try:
        items = yaml.safe_load(path.read_text(encoding="utf-8")) or []
    except Exception:
        return [], False

    if not isinstance(items, list):
        return [], False

    active = [
        p for p in items
        if isinstance(p, dict) and str(p.get("status", "active")).lower() == "active"
    ]
    if len(active) == 0:
        return [
            {
                "code": "comms.comms_not_ready",
                "status": "error",
                "message": "0 активных PRM в state/ledger/promises.yaml — коммуникация не готова (блокирует C3).",
                "file": rel,
                "line": 1,
                "ids": [str(p.get("id")) for p in items if isinstance(p, dict) and p.get("id")],
            }
        ], True

    # A planned test alone cannot substantiate an existing customer promise.
    experimental = []
    for promise in active:
        refs = promise.get("proof") or []
        if isinstance(refs, list) and refs and all(isinstance(ref, str) and ref.startswith("EXP-") for ref in refs):
            experimental.append((str(promise.get("id", "PRM")), refs))
    if not experimental:
        return [], True

    experiments_path = path.with_name("experiments.yaml")
    try:
        experiments = yaml.safe_load(experiments_path.read_text(encoding="utf-8")) or []
    except (FileNotFoundError, yaml.YAMLError):
        experiments = []
    if not isinstance(experiments, list):
        experiments = []
    completed = {e.get("id") for e in experiments if isinstance(e, dict)
                 and e.get("status", "active") == "active"
                 and isinstance(e.get("result"), str) and e["result"].strip()}
    findings = []
    for pid, refs in experimental:
        if completed.isdisjoint(refs):
            findings.append({"code": "comms.promise_without_result", "status": "error",
                             "file": rel, "line": 1, "ids": [pid, *refs],
                             "message": f"{pid}: единственная опора — эксперименты без действующего результата. "
                                        "До C3 проверьте свойство и его соответствие сообщению."})
    return findings, True


def check(project: pathlib.Path) -> List[dict]:
    project = pathlib.Path(project)
    p05 = project / "outputs" / "05_product_strategy.md"
    p06 = project / "outputs" / "06_comms_strategy.md"

    findings: List[dict] = []
    has_seg = False
    has_fun = False

    lines_05 = _active_lines(p05) if p05.is_file() else []
    lines_06 = _active_lines(p06) if p06.is_file() else []

    if lines_05:
        seg_findings, has_seg = _check_segments_05(lines_05)
        findings.extend(seg_findings)
    else:
        findings.append(
            {
                "code": "comms.segments_not_assessed",
                "status": "not_assessed",
                "message": "Файл outputs/05_product_strategy.md отсутствует — сегменты не проверялись.",
                "file": "outputs/05_product_strategy.md",
                "line": 0,
                "ids": [],
            }
        )

    if lines_06:
        fun_findings, has_fun = _check_funnel_06(project, lines_06)
        findings.extend(fun_findings)
        findings.extend(_check_channels_06(lines_06, has_fun))
    else:
        findings.append(
            {
                "code": "comms.funnel_not_assessed",
                "status": "not_assessed",
                "message": "Файл outputs/06_comms_strategy.md отсутствует — таблица воронки не проверялась.",
                "file": "outputs/06_comms_strategy.md",
                "line": 0,
                "ids": [],
            }
        )

    if lines_05 and lines_06:
        findings.extend(_check_05_06_contradiction(lines_05, lines_06))

    prm_findings, _ = _check_promises_active(project, has_06=p06.is_file())
    findings.extend(prm_findings)

    errors_or_warns = [f for f in findings if f["status"] in ("error", "warn")]
    if not errors_or_warns and has_seg and has_fun:
        assessed_only = [f for f in findings if f["status"] != "not_assessed"]
        if not assessed_only:
            return findings + [
                {
                    "code": "comms.ok",
                    "status": "ok",
                    "message": "Сегменты, таблица воронки, обещания и запреты 05/06 согласованы.",
                    "file": "outputs/06_comms_strategy.md",
                    "line": 1,
                    "ids": [],
                }
            ]

    return findings


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="M10: Проверка сегментов и коммуникаций ↔ воронка.")
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

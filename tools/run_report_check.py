#!/usr/bin/env python3
"""Проверка согласованности отчётов о прогоне, стоимости и документации (M06).

API:
    check(project: Path) -> list[dict]
    check_project(project: Path) -> list[dict]
    check_docs(root: Path) -> list[dict]
    check_all(root: Path) -> list[dict]
    Поля элемента: code, status (ok|warn|error|not_assessed), message, file, line, ids.

CLI:
    python tools/run_report_check.py [target ...] [--all] [--json]
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ledger as L  # noqa: E402
import verify as V  # noqa: E402
from cost import NO_USAGE_NOTE  # noqa: E402

ENV_ALIASES = {
    "claude-code": "claude-code",
    "claude code": "claude-code",
    "codex": "codex",
    "zcode": "zcode",
    "cursor": "cursor",
    "opencode": "opencode",
    "gemini": "gemini",
    "windsurf": "windsurf",
    "orca": "orca",
    "gigacode": "gigacode",
    "кодик": "kodik",
    "kodik": "kodik",
    "github copilot": "github-copilot",
    "github-copilot": "github-copilot",
    "copilot": "github-copilot",
    "openrouter": "openrouter",
}
_ENV_PATTERN = (
    r"claude[-\s]code|codex|zcode|cursor|opencode|gemini|windsurf|orca"
    r"|gigacode|кодик|kodik|github[-\s]copilot|copilot|openrouter"
)
ENV_RE = re.compile(
    rf"(?<![A-Za-zА-Яа-яЁё0-9_-])({_ENV_PATTERN})(?![A-Za-zА-Яа-яЁё0-9_-])",
    re.I,
)
ERRONEOUS_ENV_RE = re.compile(
    r"(?:ошибочно\s+определял\w*\s+как|неверно\s+определ\w*\s+как|ложно\s+определ\w*\s+как)"
    r"[^()\n;]*",
    re.I,
)
_ZERO_API_PAREN = (
    r"\([^();\n]*(?:\bбез\s+(?:модельн\w*\s+)?(?:вызовов|api)\b|zero[-\s]?api|no[-\s]?api)[^();\n]*\)"
)
NO_API_NOTE_RE = re.compile(
    rf"(?:;\s*[^;|\n]*?{_ZERO_API_PAREN}"
    rf"|(?:(?:{_ENV_PATTERN}|[`A-Za-zА-Яа-яЁё0-9_.-]+)\s*)?{_ZERO_API_PAREN})",
    re.I,
)
NEGATED_ENV_RE = re.compile(
    rf"\b(?:не|not)\s+(?:в\s+|среде\s+)?({_ENV_PATTERN})\b",
    re.I,
)
_MODEL_OR_NOISE_RE = re.compile(
    r"^(?:glm-\d|gpt-\d|claude-(?:\d|opus|sonnet|haiku)|gemini-\d|qwen-?\d|llama-?\d"
    r"|deepseek|opus|sonnet|haiku|sol|luna|terra|astra|t[0-4])$",
    re.I,
)
PLACEHOLDER_CELLS = {"", "-", "—", "–", "n/a", "none", "tbd", "unknown", "неизвестна", "неизвестно", "не указана", "не указано", "итого", "total", "всего"}

USD_RE = re.compile(r"\$\s*([0-9][0-9\s\u00a0]*(?:[.,][0-9]+)?)")
COST_TOTAL_RE = re.compile(r"Итого[^$\n]*\$\s*([0-9][0-9\s\u00a0]*(?:[.,][0-9]+)?)", re.I)

DENIAL_PATTERNS = [
    re.compile(r"стоимост[ьи]\s+не\s+предоставлен\w*", re.I),
    re.compile(r"точн\w*\s+стоимост[ьи]\s+не\s+(?:показывал\w*|предоставлен\w*|указан\w*)", re.I),
    re.compile(r"точн\w*\s+токен\w*\s+и\s+стоимост[ьи]\s+не\s+предоставлен\w*", re.I),
    re.compile(r"токен\w*\s+и\s+стоимост[ьи]\s+не\s+предоставлен\w*", re.I),
    re.compile(r"среда\s+(?:не\s+предоставила|точную\s+стоимость\s+не\s+показывала)", re.I),
    re.compile(r"стоимост[ьи]\s+(?:недоступна|не\s+предоставлялась|неизвестна)", re.I),
    re.compile(r"cost\s+(?:not\s+provided|unavailable)", re.I),
    re.compile(r"\$\s*(?:tbd|n/?a)\b", re.I),
]

COST_CONTEXT_RE = re.compile(
    r"(?:итог\w*|стоимост\w*|расход\w*|эквивалент\w*|cost_report|поток\w*|usd|долл)",
    re.I,
)
MARKET_SCALE_AFTER_RE = re.compile(
    r"^\s*(?:[–—-]\s*[0-9][0-9\s\u00a0]*(?:[.,][0-9]+)?\s*)?"
    r"(?:млрд|млн|трлн|billion|million|trillion|bn|mn|tn)\b",
    re.I,
)
MD_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)#]+)(?:#([^)]+))?\)")
PCT_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*(?:[–—-]\s*(\d+(?:[.,]\d+)?)\s*)?%")
COUNT_ENV_RE = re.compile(r"\*\*(\d+)\s+сред[аы]?\*\*", re.I)


def _finding(code: str, status: str, message: str, file: str, line: int, ids: list[str]) -> dict:
    return {
        "code": code,
        "status": status,
        "message": message,
        "file": file,
        "line": int(line),
        "ids": list(ids),
    }


def _rel(path: Path, base: Path | None = None) -> str:
    if base is not None:
        try:
            return str(path.relative_to(base))
        except ValueError:
            pass
    return path.name


def _parse_num(raw: str) -> tuple[float, int] | None:
    cleaned = re.sub(r"[\s\u00a0]", "", raw).replace(",", ".")
    if not cleaned:
        return None
    try:
        val = float(cleaned)
    except ValueError:
        return None
    decimals = len(cleaned.split(".", 1)[1]) if "." in cleaned else 0
    return val, decimals


def _usd_matches(candidate: float, decimals: int, target: float) -> bool:
    if decimals >= 3:
        return abs(candidate - target) <= 0.00051
    if decimals == 2:
        return abs(candidate - round(target, 2)) <= 1e-6 or abs(candidate - target) <= 0.0051
    return abs(candidate - target) <= 0.051


def _extract_envs(text: str) -> list[str]:
    cleaned = ERRONEOUS_ENV_RE.sub(" ", text)
    cleaned = NO_API_NOTE_RE.sub(" ", cleaned)
    cleaned = NEGATED_ENV_RE.sub(" ", cleaned)
    found: list[str] = []
    for m in ENV_RE.finditer(cleaned):
        key = re.sub(r"\s+", " ", m.group(1).lower())
        canon = ENV_ALIASES.get(key, key)
        if canon not in found:
            found.append(canon)
    return found


def _extract_raw_env_token(cell: str) -> list[str]:
    cleaned = ERRONEOUS_ENV_RE.sub(" ", cell)
    cleaned = NO_API_NOTE_RE.sub(" ", cleaned)
    cleaned = NEGATED_ENV_RE.sub(" ", cleaned)

    found: list[str] = []
    for k in _extract_envs(cleaned):
        if k not in found:
            found.append(k)

    no_paren = re.sub(r"\([^)]*\)", " ", cleaned)
    for part in re.split(r"[,;/+\n]+|\s+(?:и|and)\s+", no_paren):
        chunk = part.strip().strip("`*_ ")
        if not chunk:
            continue
        known_in_chunk = _extract_envs(chunk)
        if known_in_chunk:
            for k in known_in_chunk:
                if k not in found:
                    found.append(k)
            continue
        m_prep = re.fullmatch(
            r"(?:прогон\s+выполнен\s+)?(?:в|in)\s+([a-zа-яё][a-zа-яё0-9._-]{1,31})",
            chunk,
            re.I,
        )
        cand = (m_prep.group(1) if m_prep else chunk).strip().strip("`*_ ").lower()
        if not cand or cand in PLACEHOLDER_CELLS or _MODEL_OR_NOISE_RE.match(cand):
            continue
        if re.fullmatch(r"[a-zа-яё][a-zа-яё0-9._-]{1,31}", cand):
            canon = ENV_ALIASES.get(cand, cand)
            if canon not in found:
                found.append(canon)
    return found


def _is_market_scale_usd(line: str, match: re.Match[str]) -> bool:
    return bool(MARKET_SCALE_AFTER_RE.match(line[match.end():]))


def _parse_cost_report(path: Path) -> tuple[float | None, list[float], list[str], list[tuple[int, str]]]:
    return _parse_cost_lines(path.read_text(encoding="utf-8", errors="replace").splitlines())


def _parse_cost_lines(lines: list[str]) -> tuple[float | None, list[float], list[str], list[tuple[int, str]]]:
    header_issues: list[tuple[int, str]] = []
    if lines:
        first = lines[0].strip()
        if re.search(r":\s*\+", first) or first.endswith("+"):
            header_issues.append((1, f"повреждённый заголовок стоимости: «{first}»"))

    total: float | None = None
    for ln in lines:
        m = COST_TOTAL_RE.search(ln)
        if m:
            parsed = _parse_num(m.group(1))
            if parsed is not None:
                total = parsed[0]
                break

    row_usd: list[float] = []
    envs: list[str] = []
    for ln in lines:
        s = ln.strip()
        if not s.startswith("|") or s.startswith("|---") or "Среда" in s and "USD" in s:
            continue
        cells = [c.strip() for c in s.strip("|").split("|")]
        if len(cells) >= 2:
            for e in _extract_raw_env_token(cells[0]):
                if e not in envs:
                    envs.append(e)
            usd_parsed = _parse_num(cells[-1])
            if usd_parsed is not None:
                row_usd.append(usd_parsed[0])

    if not envs and lines:
        envs = _extract_envs(lines[0])
        if not envs:
            m_hdr = re.search(r"Стоимост[ьи]\s*:\s*([^(\n]+)", lines[0], re.I)
            if m_hdr:
                envs = _extract_raw_env_token(m_hdr.group(1))
    return total, row_usd, envs, header_issues


def _find_09_env_lines(lines: list[str]) -> tuple[list[str], int]:
    """Среды, объявленные в 09_run_report.md, и номер первой релевантной строки."""
    envs: list[str] = []
    env_line = 1
    in_main = False
    for idx, ln in enumerate(lines, 1):
        s = ln.strip()
        if V.MAIN_HEAD.match(s):
            in_main = True
        elif in_main and V.HEAD.match(s):
            in_main = False

        is_table_env = bool(re.match(r"^\|\s*Среда\b", s, re.I))
        m_kv = re.match(r"^\s*(?:[-*]\s*)?\*{0,2}Среда\*{0,2}\s*:\s*(.+)$", s, re.I)
        if is_table_env:
            cells = [c.strip() for c in s.strip("|").split("|")]
            found = _extract_raw_env_token(cells[1]) if len(cells) >= 2 else []
        elif m_kv:
            found = _extract_raw_env_token(m_kv.group(1))
        elif in_main:
            found = _extract_envs(s)
        else:
            continue

        if found and env_line == 1:
            env_line = idx
        for e in found:
            if e not in envs:
                envs.append(e)
    if not envs:
        for idx, ln in enumerate(lines, 1):
            found = _extract_envs(ln)
            if found:
                env_line = idx
                for e in found:
                    if e not in envs:
                        envs.append(e)
                break
    return envs, env_line


def _find_09_cost_lines(lines: list[str]) -> list[tuple[int, str]]:
    out: list[tuple[int, str]] = []
    in_limitations = False
    for idx, ln in enumerate(lines, 1):
        s = ln.strip()
        if V.HEAD.match(s):
            in_limitations = bool(re.search(r"Ограничени", s, re.I))

        if in_limitations:
            continue
        if "$" in s:
            out.append((idx, s))
    return out


def _parse_progress_envs(path: Path) -> tuple[list[str], int]:
    if not path.exists():
        return [], 1
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    for idx, ln in enumerate(lines, 1):
        m = re.match(r"^\s*(?:[-*]\s*)?\*{0,2}Среда\*{0,2}\s*:\s*(.*)$", ln, re.I)
        if m:
            return _extract_raw_env_token(m.group(1)), idx
    return [], 1


def check_project(project: Path) -> list[dict]:
    project = project.resolve()
    findings: list[dict] = []
    outputs_dir = project / "outputs"
    report_09 = outputs_dir / "09_run_report.md"
    cost_path = project / "state" / "cost_report.md"
    progress_path = project / "state" / "progress.md"

    has_09 = report_09.is_file()
    has_cost = cost_path.is_file()
    if not has_09 and not has_cost:
        return [
            _finding(
                "run_report_not_present",
                "not_assessed",
                f"в проекте {project.name} отсутствуют outputs/09_run_report.md и state/cost_report.md",
                "outputs/09_run_report.md",
                1,
                [project.name],
            )
        ]

    cost_total: float | None = None
    cost_rows: list[float] = []
    cost_envs: list[str] = []
    cost_unobserved = False
    if has_cost:
        lines = cost_path.read_text(encoding="utf-8", errors="replace").splitlines()
        start = max((i for i, line in enumerate(lines) if re.match(r"^\s*(?:#{1,6}\s*)?Стоимость\s*:", line)), default=0)
        current_lines = lines[start:]
        cost_total, cost_rows, cost_envs, header_issues = _parse_cost_lines(current_lines)
        cost_unobserved = (
            cost_total is None and not cost_rows
            and NO_USAGE_NOTE in current_lines
        )
        for ln, msg in header_issues:
            findings.append(
                _finding(
                    "cost_report_malformed_header",
                    "error",
                    msg,
                    "state/cost_report.md",
                    start + ln,
                    [project.name],
                )
            )
        if cost_unobserved:
            cost_envs = []
            findings.append(_finding(
                "cost_usage_unobserved", "warn", NO_USAGE_NOTE, "state/cost_report.md", start + 1, [project.name]
            ))
        elif cost_total is None:
            findings.append(
                _finding(
                    "cost_report_missing_total",
                    "error",
                    "в state/cost_report.md не найдена строка Итого с суммой $",
                    "state/cost_report.md",
                    1,
                    [project.name],
                )
            )
        if not cost_envs and not cost_unobserved:
            findings.append(
                _finding(
                    "cost_report_missing_env",
                    "error",
                    "в state/cost_report.md не определена среда (колонка «Среда»)",
                    "state/cost_report.md",
                    1,
                    [project.name],
                )
            )
    elif has_09:
        findings.append(
            _finding(
                "cost_report_missing",
                "error",
                "есть outputs/09_run_report.md, но отсутствует обязательный state/cost_report.md",
                "state/cost_report.md",
                1,
                [project.name],
            )
        )

    if not has_09:
        findings.append(
            _finding(
                "run_report_missing",
                "error",
                "есть state/cost_report.md, но отсутствует outputs/09_run_report.md",
                "outputs/09_run_report.md",
                1,
                [project.name],
            )
        )
        return findings

    text_09 = report_09.read_text(encoding="utf-8", errors="replace")
    lines_09 = text_09.splitlines()

    # 1. Запрещённые фразы отрицания стоимости / заглушки ($TBD, $N/A)
    for idx, ln in enumerate(lines_09, 1):
        for pat in DENIAL_PATTERNS:
            m = pat.search(ln)
            if m:
                ctx = (
                    f"при наличии state/cost_report.md (${cost_total:.4f})"
                    if (has_cost and cost_total is not None)
                    else "при обязательном учёте стоимости в state/cost_report.md"
                )
                findings.append(
                    _finding(
                        "run_report_cost_denied",
                        "error",
                        f"в 09_run_report.md указано «{m.group(0)}» {ctx}",
                        "outputs/09_run_report.md",
                        idx,
                        [project.name],
                    )
                )
                break

    # 2. Сверка итога $ с state/cost_report.md
    if cost_unobserved:
        for idx, ln in _find_09_cost_lines(lines_09):
            if any(not _is_market_scale_usd(ln, match) for match in USD_RE.finditer(ln)):
                findings.append(_finding(
                    "run_report_unobserved_cost_amount", "error",
                    "В 09_run_report.md указан денежный итог без записей токенов.",
                    "outputs/09_run_report.md", idx, [project.name],
                ))

    if has_cost and cost_total is not None:
        cost_lines = _find_09_cost_lines(lines_09)
        matched_total = False
        allowed_vals = [cost_total] + cost_rows
        for idx, ln in cost_lines:
            for m in USD_RE.finditer(ln):
                if _is_market_scale_usd(ln, m):
                    continue
                parsed = _parse_num(m.group(1))
                if parsed is None:
                    continue
                val, dec = parsed
                if _usd_matches(val, dec, cost_total):
                    matched_total = True
                if not any(_usd_matches(val, dec, ref) for ref in allowed_vals):
                    findings.append(
                        _finding(
                            "run_report_cost_mismatch",
                            "error",
                            f"сумма ${m.group(1)} в 09_run_report.md не совпадает с state/cost_report.md (итог ${cost_total:.4f})",
                            "outputs/09_run_report.md",
                            idx,
                            [project.name],
                        )
                    )
        if not matched_total:
            findings.append(
                _finding(
                    "run_report_missing_cost_total",
                    "error",
                    f"в 09_run_report.md не найден итог ${cost_total:.4f} из state/cost_report.md",
                    "outputs/09_run_report.md",
                    1,
                    [project.name],
                )
            )

    # 3. Сверка среды: envs_09 не пусто, envs_09 совпадает с cost_envs и (при наличии progress.md) входит в prog_envs
    envs_09, env_line_09 = _find_09_env_lines(lines_09)
    prog_envs, prog_line = _parse_progress_envs(progress_path)

    if not envs_09:
        ctx = f" (в cost_report.md: {', '.join(cost_envs)})" if cost_envs else ""
        findings.append(
            _finding(
                "run_report_env_missing",
                "error",
                f"в 09_run_report.md не указана распознанная среда{ctx}",
                "outputs/09_run_report.md",
                env_line_09,
                [project.name],
            )
        )
    else:
        if has_cost and cost_envs:
            missing_from_09 = [e for e in cost_envs if e not in envs_09]
            if missing_from_09:
                findings.append(
                    _finding(
                        "run_report_env_mismatch",
                        "error",
                        f"среда из cost_report.md ({', '.join(missing_from_09)}) не указана в 09_run_report.md ({', '.join(envs_09)})",
                        "outputs/09_run_report.md",
                        env_line_09,
                        [project.name],
                    )
                )
            extra_in_09 = [e for e in envs_09 if e not in cost_envs]
            if extra_in_09:
                findings.append(
                    _finding(
                        "run_report_env_mismatch",
                        "error",
                        f"среда прогона в 09_run_report.md ({', '.join(extra_in_09)}) отсутствует в state/cost_report.md ({', '.join(cost_envs)})",
                        "outputs/09_run_report.md",
                        env_line_09,
                        [project.name],
                    )
                )

    if prog_envs and envs_09:
        unknown_in_prog = [e for e in envs_09 if e not in prog_envs]
        if unknown_in_prog:
            findings.append(
                _finding(
                    "run_report_progress_env_mismatch",
                    "error",
                    f"среда в 09_run_report.md ({', '.join(unknown_in_prog)}) отсутствует в state/progress.md ({', '.join(prog_envs)})",
                    "outputs/09_run_report.md",
                    env_line_09,
                    [project.name],
                )
            )
    elif progress_path.exists() and not prog_envs:
        findings.append(
            _finding(
                "progress_env_missing",
                "error",
                "в state/progress.md не заполнена строка «Среда:»",
                "state/progress.md",
                prog_line,
                [project.name],
            )
        )

    if has_cost and cost_envs and prog_envs:
        missing_in_prog = [e for e in cost_envs if e not in prog_envs]
        if missing_in_prog:
            findings.append(
                _finding(
                    "progress_cost_env_mismatch",
                    "error",
                    f"среда cost_report.md ({', '.join(missing_in_prog)}) отсутствует в state/progress.md ({', '.join(prog_envs)})",
                    "state/progress.md",
                    prog_line,
                    [project.name],
                )
            )

    # 4. Проверка dup-main с включением 09_run_report.md
    if outputs_dir.is_dir():
        md_files = sorted(outputs_dir.glob("*.md"))
        for dup in V.duplicate_mains(md_files):
            fname = dup["file"]
            note = dup["note"]
            involves_09 = fname == "09_run_report.md" or "09_run_report.md" in note
            findings.append(
                _finding(
                    "run_report_dup_main" if involves_09 else "outputs_dup_main",
                    "error" if involves_09 else "warn",
                    f"{fname}: {note}",
                    f"outputs/{fname}",
                    dup["line"],
                    [project.name],
                )
            )

    if not any(f["status"] in ("error", "warn") for f in findings):
        findings.append(
            _finding(
                "run_report_ok",
                "ok",
                f"09_run_report.md, cost_report.md и progress.md согласованы ({', '.join(envs_09 or cost_envs)})",
                "outputs/09_run_report.md",
                1,
                [project.name],
            )
        )
    return findings


def _check_readme(root: Path) -> list[dict]:
    readme = root / "README.md"
    if not readme.is_file():
        return []
    findings: list[dict] = []
    lines = readme.read_text(encoding="utf-8", errors="replace").splitlines()
    top_end = next((i for i, ln in enumerate(lines, 1) if ln.startswith("## ")), min(len(lines), 25) + 1)

    for idx in range(1, top_end):
        ln = lines[idx - 1]
        links = MD_LINK_RE.findall(ln)
        if not links:
            continue
        for _, target_rel, _ in links:
            target = (root / target_rel).resolve()
            if target_rel.startswith(("http://", "https://", "mailto:")):
                continue
            if not target.exists():
                findings.append(
                    _finding(
                        "docs_broken_link",
                        "error",
                        f"битая ссылка в README.md: {target_rel}",
                        "README.md",
                        idx,
                        ["README"],
                    )
                )

        # Проверка $ и заглушек ($TBD/$N/A) со ссылкой на cost_report.md
        usd_matches = list(USD_RE.finditer(ln))
        cost_targets = [t for _, t, _ in links if t.endswith("cost_report.md")]
        if cost_targets:
            cost_file = root / cost_targets[0]
            if not cost_file.is_file():
                findings.append(
                    _finding(
                        "docs_missing_cost_source",
                        "error",
                        f"README.md:{idx} ссылается на стоимость в {cost_targets[0]}, но файл отсутствует",
                        "README.md",
                        idx,
                        ["README"],
                    )
                )
            else:
                total, _, _, _ = _parse_cost_report(cost_file)
                if total is None:
                    findings.append(
                        _finding(
                            "docs_unparseable_cost_source",
                            "error",
                            f"README.md:{idx} ссылается на стоимость в {cost_targets[0]}, но в нём не найден Итого $",
                            "README.md",
                            idx,
                            ["README"],
                        )
                    )
                else:
                    if re.search(r"\$\s*(?:tbd|n/?a)\b", ln, re.I) or (
                        not usd_matches and ("$" in ln or COST_CONTEXT_RE.search(ln))
                    ):
                        findings.append(
                            _finding(
                                "docs_source_number_mismatch",
                                "error",
                                f"README.md:{idx} содержит заглушку или не указывает сумму $ при ссылке на {cost_targets[0]} (итог ${total:.4f})",
                                "README.md",
                                idx,
                                ["README"],
                            )
                        )
                    for um in usd_matches:
                        parsed = _parse_num(um.group(1))
                        if parsed and not _usd_matches(parsed[0], parsed[1], total):
                            findings.append(
                                _finding(
                                    "docs_source_number_mismatch",
                                    "error",
                                    f"README.md:{idx} указывает ${um.group(1)}, но в {cost_targets[0]} итог ${total:.4f}",
                                    "README.md",
                                    idx,
                                    ["README"],
                                )
                            )

        # Проверка количества сред с реальным прогоном (например, **2 среды**: [Codex](...) и [ZCode](...))
        cm = COUNT_ENV_RE.search(ln)
        if cm:
            declared = int(cm.group(1))
            envs_linked = _extract_envs(ln)
            if envs_linked and declared != len(envs_linked):
                findings.append(
                    _finding(
                        "docs_source_number_mismatch",
                        "error",
                        f"README.md:{idx} заявляет {declared} сред(ы), но перечислены {', '.join(envs_linked)} ({len(envs_linked)})",
                        "README.md",
                        idx,
                        ["README"],
                    )
                )

        # Проверка % с явным маркером источника (T07.md / RESULTS.md)
        if "%" in ln:
            for pct_m in PCT_RE.finditer(ln):
                after = ln[pct_m.end():]
                before = ln[:pct_m.start()]
                # Сопоставляем ближайшую ссылку после числа или оборачивающую число [11–50%](RESULTS.md)
                link_after = MD_LINK_RE.search(ln[max(0, pct_m.start() - 2):])
                if not link_after:
                    continue
                target_rel = link_after.group(2)
                target_file = root / target_rel
                if not target_file.is_file():
                    continue
                src_text = target_file.read_text(encoding="utf-8", errors="replace")
                nums_to_check = [pct_m.group(1)] + ([pct_m.group(2)] if pct_m.group(2) else [])
                for raw_p in nums_to_check:
                    p_dot = raw_p.replace(",", ".")
                    p_comma = raw_p.replace(".", ",")
                    if not (
                        re.search(rf"(?<!\d){re.escape(p_dot)}\s*%", src_text)
                        or re.search(rf"(?<!\d){re.escape(p_comma)}\s*%", src_text)
                    ):
                        findings.append(
                            _finding(
                                "docs_source_number_mismatch",
                                "error",
                                f"README.md:{idx} указывает {raw_p}%, но в {target_rel} это значение не найдено",
                                "README.md",
                                idx,
                                ["README"],
                            )
                        )
                    elif before.strip().endswith("—") and len(nums_to_check) == 2:
                        continue
    return findings


def _check_description_s5(root: Path) -> list[dict]:
    desc = root / "docs" / "DESCRIPTION.md"
    if not desc.is_file():
        return []
    findings: list[dict] = []
    lines = desc.read_text(encoding="utf-8", errors="replace").splitlines()
    start = next((i for i, ln in enumerate(lines, 1) if ln.startswith("## 5.")), None)
    if start is None:
        return []
    end = next((i for i in range(start + 1, len(lines) + 1) if lines[i - 1].startswith("## ")), len(lines) + 1)

    col_projects: list[Path | None] = []
    for idx in range(start, end):
        ln = lines[idx - 1]
        if ln.startswith("|") and ("Рядом" in ln or "eSIM" in ln):
            cells = [c.strip() for c in ln.strip("|").split("|")]
            col_projects = []
            for c in cells:
                if "Рядом" in c or "ryadom" in c.lower():
                    col_projects.append(root / "projects" / "ryadom")
                elif "eSIM" in c or "esim" in c.lower():
                    col_projects.append(root / "projects" / "esim-travel")
                else:
                    col_projects.append(None)
            continue

        # Строка таблицы Стоимость (API-эквивалент) напрямую сверяется с state/cost_report.md
        if ln.startswith("| Стоимость"):
            cells = [c.strip() for c in ln.strip("|").split("|")]
            for col_i, cell in enumerate(cells):
                if col_i >= len(col_projects) or col_projects[col_i] is None:
                    continue
                proj = col_projects[col_i]
                cost_file = proj / "state" / "cost_report.md"
                if not cost_file.is_file():
                    findings.append(
                        _finding(
                            "docs_missing_cost_source",
                            "error",
                            f"DESCRIPTION.md:{idx} указывает стоимость для {proj.name}, но отсутствует state/cost_report.md",
                            "docs/DESCRIPTION.md",
                            idx,
                            [proj.name],
                        )
                    )
                    continue
                total, rows, _, _ = _parse_cost_report(cost_file)
                if total is None:
                    findings.append(
                        _finding(
                            "docs_unparseable_cost_source",
                            "error",
                            f"DESCRIPTION.md:{idx} указывает стоимость для {proj.name}, но в state/cost_report.md не найден Итого $",
                            "docs/DESCRIPTION.md",
                            idx,
                            [proj.name],
                        )
                    )
                    continue
                allowed = [total] + rows
                usd_list = list(USD_RE.finditer(cell))
                if not usd_list:
                    findings.append(
                        _finding(
                            "docs_source_number_mismatch",
                            "error",
                            f"DESCRIPTION.md:{idx} не содержит $ для {proj.name}",
                            "docs/DESCRIPTION.md",
                            idx,
                            [proj.name],
                        )
                    )
                    continue
                first_parsed = _parse_num(usd_list[0].group(1))
                if first_parsed and not _usd_matches(first_parsed[0], first_parsed[1], total):
                    findings.append(
                        _finding(
                            "docs_source_number_mismatch",
                            "error",
                            f"DESCRIPTION.md:{idx} указывает ${usd_list[0].group(1)} для {proj.name}, но в cost_report.md итог ${total:.4f}",
                            "docs/DESCRIPTION.md",
                            idx,
                            [proj.name],
                        )
                    )
                for um in usd_list[1:]:
                    parsed = _parse_num(um.group(1))
                    if parsed and not any(_usd_matches(parsed[0], parsed[1], ref) for ref in allowed):
                        findings.append(
                            _finding(
                                "docs_source_number_mismatch",
                                "error",
                                f"DESCRIPTION.md:{idx} указывает ${um.group(1)} для {proj.name}, что не совпадает с cost_report.md",
                                "docs/DESCRIPTION.md",
                                idx,
                                [proj.name],
                            )
                        )

        # Явные маркеры источника в §5 (формат `[...](path)` рядом с цифрами)
        for lm in MD_LINK_RE.finditer(ln):
            target_rel = lm.group(2)
            if target_rel.startswith(("http://", "https://", "mailto:")):
                continue
            target = (desc.parent / target_rel).resolve()
            if not target.exists():
                target = (root / target_rel).resolve()
            if not target.exists():
                findings.append(
                    _finding(
                        "docs_broken_link",
                        "error",
                        f"битая ссылка в DESCRIPTION.md §5: {target_rel}",
                        "docs/DESCRIPTION.md",
                        idx,
                        ["DESCRIPTION"],
                    )
                )
                if target.name == "cost_report.md":
                    findings.append(
                        _finding(
                            "docs_missing_cost_source",
                            "error",
                            f"DESCRIPTION.md:{idx} ссылается на {target_rel}, но файл отсутствует",
                            "docs/DESCRIPTION.md",
                            idx,
                            ["DESCRIPTION"],
                        )
                    )
            elif target.name == "cost_report.md":
                total, _, _, _ = _parse_cost_report(target)
                if total is None:
                    findings.append(
                        _finding(
                            "docs_unparseable_cost_source",
                            "error",
                            f"DESCRIPTION.md:{idx} ссылается на стоимость в {target_rel}, но в нём не найден Итого $",
                            "docs/DESCRIPTION.md",
                            idx,
                            ["DESCRIPTION"],
                        )
                    )
                else:
                    usd_found = list(USD_RE.finditer(ln))
                    if re.search(r"\$\s*(?:tbd|n/?a)\b", ln, re.I) or (
                        not usd_found and ("$" in ln or COST_CONTEXT_RE.search(ln))
                    ):
                        findings.append(
                            _finding(
                                "docs_source_number_mismatch",
                                "error",
                                f"DESCRIPTION.md:{idx} содержит заглушку или не указывает сумму $ при ссылке на {target_rel} (итог ${total:.4f})",
                                "docs/DESCRIPTION.md",
                                idx,
                                ["DESCRIPTION"],
                            )
                        )
                    for um in usd_found:
                        parsed = _parse_num(um.group(1))
                        if parsed and not _usd_matches(parsed[0], parsed[1], total):
                            findings.append(
                                _finding(
                                    "docs_source_number_mismatch",
                                    "error",
                                    f"DESCRIPTION.md:{idx} указывает ${um.group(1)}, но в {target_rel} итог ${total:.4f}",
                                    "docs/DESCRIPTION.md",
                                    idx,
                                    ["DESCRIPTION"],
                                )
                            )
            elif "%" in ln and target.is_file():
                src_text = target.read_text(encoding="utf-8", errors="replace")
                for pct_m in PCT_RE.finditer(ln):
                    nums_to_check = [pct_m.group(1)] + ([pct_m.group(2)] if pct_m.group(2) else [])
                    for raw_p in nums_to_check:
                        p_dot = raw_p.replace(",", ".")
                        p_comma = raw_p.replace(".", ",")
                        if not (
                            re.search(rf"(?<!\d){re.escape(p_dot)}\s*%", src_text)
                            or re.search(rf"(?<!\d){re.escape(p_comma)}\s*%", src_text)
                        ):
                            findings.append(
                                _finding(
                                    "docs_source_number_mismatch",
                                    "error",
                                    f"DESCRIPTION.md:{idx} указывает {raw_p}%, но в {target_rel} это значение не найдено",
                                    "docs/DESCRIPTION.md",
                                    idx,
                                    ["DESCRIPTION"],
                                )
                            )

        # Предупреждения о немаркированных исторических снимках в §5 (не валят прогон до обновления общих файлов координатором)
        if ln.startswith("| Реестр") and col_projects:
            cells = [c.strip() for c in ln.strip("|").split("|")]
            for col_i, cell in enumerate(cells):
                if col_i >= len(col_projects) or col_projects[col_i] is None:
                    continue
                proj = col_projects[col_i]
                if not (proj / "state" / "ledger").is_dir():
                    continue
                m_obj = re.search(r"(\d+)\s+объект", cell)
                if m_obj:
                    live_cnt = len(L.Ledger(proj).objects)
                    if int(m_obj.group(1)) != live_cnt:
                        findings.append(
                            _finding(
                                "docs_stale_snapshot",
                                "warn",
                                f"DESCRIPTION.md:{idx} содержит устаревший снимок числа объектов для {proj.name}: {m_obj.group(1)} вместо {live_cnt}",
                                "docs/DESCRIPTION.md",
                                idx,
                                [proj.name],
                            )
                        )
        if ln.startswith("| Помощь человека") and "координатор" not in ln.lower():
            findings.append(
                _finding(
                    "docs_missing_gate_disclosure",
                    "warn",
                    "DESCRIPTION.md §5 не раскрывает, что в ZCode-прогоне ответы на C1–C3 давал координатор (M16)",
                    "docs/DESCRIPTION.md",
                    idx,
                    ["DESCRIPTION"],
                )
            )
    return findings


def check(project: Path) -> list[dict]:
    """Единый контракт check-модулей: проверка одного проекта."""
    return check_project(project)


def check_docs(root: Path) -> list[dict]:
    root = root.resolve()
    return _check_readme(root) + _check_description_s5(root)


def _discover_projects(root: Path) -> list[Path]:
    pdir = root / "projects"
    if not pdir.is_dir():
        return []
    out: list[Path] = []
    for p in sorted(pdir.iterdir()):
        if not p.is_dir():
            continue
        if (p / "outputs" / "09_run_report.md").is_file() or (p / "state" / "cost_report.md").is_file():
            out.append(p)
    return out


def check_all(root: Path, include_docs: bool = True) -> list[dict]:
    root = root.resolve()
    findings: list[dict] = []
    for proj in _discover_projects(root):
        for item in check_project(proj):
            item_copy = dict(item)
            if not item_copy["file"].startswith(f"projects/{proj.name}/"):
                item_copy["file"] = f"projects/{proj.name}/{item_copy['file']}"
            findings.append(item_copy)
    if include_docs:
        findings.extend(check_docs(root))
    return findings


def _is_project_dir(path: Path) -> bool:
    return (path / "outputs").is_dir() or (path / "state").is_dir()


def _render_human(findings: list[dict]) -> str:
    counts: dict[str, int] = {}
    for f in findings:
        counts[f["status"]] = counts.get(f["status"], 0) + 1
    lines = ["Сводка run_report_check: " + ", ".join(f"{k}: {v}" for k, v in sorted(counts.items()))]
    for f in findings:
        if f["status"] == "ok":
            continue
        loc = f"{f['file']}:{f['line']}"
        lines.append(f"- [{f['status']}] {f['code']} {loc} — {f['message']}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Проверка согласованности 09_run_report.md, cost_report.md и README/DESCRIPTION §5 (M06)")
    ap.add_argument("targets", nargs="*", type=Path, help="проекты или корень репозитория (по умолчанию — все проекты и документы в cwd)")
    ap.add_argument("--all", action="store_true", help="проверить все проекты и документы корня")
    ap.add_argument("--no-docs", action="store_true", help="не проверять README.md и docs/DESCRIPTION.md")
    ap.add_argument("--json", action="store_true", help="вывод в JSON")
    args = ap.parse_args(argv)

    for t in args.targets:
        resolved = t.resolve()
        if not resolved.exists() or not resolved.is_dir():
            print(f"Путь не найден или не является каталогом: {resolved}", file=sys.stderr)
            return 2

    findings: list[dict] = []
    if not args.targets or args.all:
        root = Path.cwd().resolve()
        if args.targets and not _is_project_dir(args.targets[0].resolve()):
            root = args.targets[0].resolve()
        findings = check_all(root, include_docs=not args.no_docs)
    else:
        for t in args.targets:
            resolved = t.resolve()
            if _is_project_dir(resolved):
                findings.extend(check_project(resolved))
            else:
                findings.extend(check_all(resolved, include_docs=not args.no_docs))

    counts: dict[str, int] = {}
    for f in findings:
        counts[f["status"]] = counts.get(f["status"], 0) + 1

    if args.json:
        print(json.dumps({"counts": counts, "findings": findings}, ensure_ascii=False, indent=2))
    else:
        print(_render_human(findings))

    has_error = any(f["status"] == "error" for f in findings)
    return 1 if has_error else 0


def _utf8_stdio() -> None:
    for s in (sys.stdin, sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


if __name__ == "__main__":
    _utf8_stdio()
    sys.exit(main())

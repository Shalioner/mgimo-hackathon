#!/usr/bin/env python3
"""Детерминированная проверка утверждений в артефактах (первый проход верификатора, уровень T0).

    python tools/verify.py projects/<case> [--json] [--files 03 04 05 06 07]

Что проверяет кодом в outputs/*.md, построчно (абзац, пункт списка, строка таблицы):
  - строка с числом (цифры, %, ₽, $) без ссылки на реестр           → unsupported
  - ссылка на несуществующий объект                                   → wrong-ref
  - ссылка на объект со статусом retracted/superseded                 → stale-ref (в истории решения → historical-ref)
  - числа строки не встречаются в тексте объектов, на которые она
    ссылается (формулировка, цитаты, обоснования, и на шаг глубже)    → check   (нужен взгляд модели)
  - иначе                                                             → ok
Ещё две проверки целого пакета:
  - блок «Главное» совпадает с «Главное» другого документа            → dup-main (код выхода 1)
  - с --change CHG-NNN: строка ссылается на объект, пересмотренный по CHG
    (затронут impact и в его записи упомянут CHG), но сама не обновлена
    и не помечена «пересмотрено/проверено по CHG-NNN»                   → stale? (переписать на месте или пометить)

Модель-верификатор (T1) потом смотрит ТОЛЬКО строки check/unsupported, а не весь пакет:
так проверка стоит единицы тысяч токенов вместо сотен тысяч.
Claim support rate (код) = ok / (все проверенные строки с числами или ссылками).
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Dict, List, Set

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ledger as L  # noqa: E402
from evidence import rate_unit_mismatch  # noqa: E402

REF = re.compile(r"\b(SRC|CLM|HYP|PAIN|ALT|DEC|PRM|EXP|CHG)-(\d{3})(?:\s*[–—-]\s*(?:(?:SRC|CLM|HYP|PAIN|ALT|DEC|PRM|EXP|CHG)-)?(\d{3}))?\b")
NUM = re.compile(r"(?<![\w.])(\d{1,3}(?:[  ]\d{3})+|\d+)(?:[.,](\d+))?(?![\w])")
# То, что числом не считаем: ID, даты, номера шагов/чекпоинтов/уровней/вопросов/пробелов, нумерация списков.
NOISE = re.compile(r"\b(?:SRC|CLM|HYP|PAIN|ALT|DEC|PRM|EXP|CHG)-\d{3}\b|\b\d{4}-\d{2}-\d{2}\b|\b[CTQG]\d+\b|"
                   r"\b(?i:шаг)[а-я]*\s+\d+|\b\d{2}_[a-z_]+\.md\b|\bv\d+\b")
WEB_URL = re.compile(r"https?://[^\s<>)\]|]+", re.IGNORECASE)
SMALL_PERCENT = re.compile(r"(?<![\w.,+−-])([1-5])([.,]0+)?\s*%(?![\w%])")
LIST_MARK = re.compile(r"^\s*(?:[-*]|\d+[.)])\s+")
SKIP_LINE = re.compile(r"^\s*(?:\|?\s*-{3,}|#|>|```)")


REF_LIST = re.compile(r"\b(SRC|CLM|HYP|PAIN|ALT|DEC|PRM|EXP|CHG)-(\d{3})((?:\s*,\s*\d{3}\b)+)")


def normalize_short_lists(text: str) -> str:
    """«CLM-005, 009, 010» → «CLM-005, CLM-009, CLM-010» (префикс написан один раз)."""
    def sub(m):
        rest = re.findall(r"\d{3}", m.group(3))
        return ", ".join([f"{m.group(1)}-{m.group(2)}"] + [f"{m.group(1)}-{n}" for n in rest])
    return REF_LIST.sub(sub, text)


def expand_refs(text: str) -> List[str]:
    out: List[str] = []
    for m in REF.finditer(text):
        pfx, a, b = m.group(1), int(m.group(2)), m.group(3)
        rng = range(a, int(b) + 1) if b and int(b) >= a and int(b) - a <= 30 else [a]
        out.extend(f"{pfx}-{n:03d}" for n in rng)
    return list(dict.fromkeys(out))


META = re.compile(r"Счётчик|ledger\.py stats|Шкала\s*1|Claim support|`validate`|validate:", re.I)


def numbers(text: str, table_row: bool = False) -> Set[str]:
    clean = NOISE.sub(" ", REF.sub(" ", LIST_MARK.sub("", text)))
    res = set()
    for m in NUM.finditer(clean):
        whole = re.sub(r"[  ]", "", m.group(1))
        n = whole + ("." + m.group(2) if m.group(2) else "")
        if table_row and n in {"1", "2", "3", "4", "5"}:
            continue  # баллы 1–5 в таблицах сравнения — оценки агента, хранятся в ALT.scores
        res.add(n)
    return res


def small_percentages(text: str, *, accept_decimal: bool = False) -> Set[str]:
    """Малые проценты вне адресов ссылок, в каноническом виде."""
    return {
        match.group(1) + "%"
        for match in SMALL_PERCENT.finditer(WEB_URL.sub(" ", text))
        if accept_decimal or match.group(2) is None
    }


def obj_text(ledger: L.Ledger, oid: str, depth: int = 1) -> str:
    """Текст объекта и (на depth шагов) объектов, на которые он опирается."""
    o = ledger.objects.get(oid)
    if not o:
        return ""
    parts = [json.dumps(o, ensure_ascii=False)]
    if prefix_ok(oid) and o.get("path"):
        f = ledger.project / o["path"]
        if f.exists() and f.suffix.lower() in L.TEXT_SUFFIXES:
            parts.append(L.evidence_text(f.read_text(encoding="utf-8", errors="replace")))
    if depth > 0:
        for up, _ in ledger.upstream(oid):
            parts.append(obj_text(ledger, up, depth - 1))
    return " ".join(parts)


def prefix_ok(oid: str) -> bool:
    return L.prefix(oid) == "SRC"


def norm_numbers_in(text: str) -> Set[str]:
    return numbers(text.replace("\\u00a0", " "))


def direct_rate_mismatch(line: str, refs: List[str], ledger: L.Ledger) -> tuple[str, str] | None:
    claims = [ledger.objects[ref] for ref in refs if L.prefix(ref) == "CLM"]
    if len(claims) != 1 or claims[0].get("type") != "fact":
        return None
    clean = re.sub(r"\[\s*(?:,\s*)*\]", " ", REF.sub(" ", line)).replace("|", " ").strip()
    return rate_unit_mismatch(clean, str(claims[0].get("statement") or ""))


MAIN_HEAD = re.compile(r"^\s*(#{1,4}\s*|\*\*)Главное\b", re.I)
HEAD = re.compile(r"^\s*#{1,4}\s")


def main_blocks(text: str) -> List[tuple[int, str]]:
    """Все блоки «Главное» в документе: [(номер_строки_заголовка, нормализованный_текст)]."""
    out: List[tuple[int, str]] = []
    cur_lines: List[str] = []
    start_ln = 0
    on = False
    for idx, ln in enumerate(text.splitlines(), 1):
        if MAIN_HEAD.match(ln):
            if on:
                norm = re.sub(r"\s+", " ", " ".join(cur_lines)).strip().lower()
                out.append((start_ln, norm))
            on = True
            start_ln = idx
            cur_lines = []
            continue
        if on and HEAD.match(ln):
            norm = re.sub(r"\s+", " ", " ".join(cur_lines)).strip().lower()
            out.append((start_ln, norm))
            on = False
            cur_lines = []
            continue
        if on:
            cur_lines.append(ln)
    if on:
        norm = re.sub(r"\s+", " ", " ".join(cur_lines)).strip().lower()
        out.append((start_ln, norm))
    return out


def main_block(text: str) -> str:
    """Первый блок «Главное» (до следующего заголовка), нормализованный для сравнения."""
    blocks = main_blocks(text)
    return blocks[0][1] if blocks else ""


def duplicate_mains(files: List[Path]) -> List[dict]:
    """Одинаковый «Главное» в разных документах или повтор «Главное» внутри одного документа."""
    import difflib
    all_blocks = {f.name: main_blocks(f.read_text(encoding="utf-8", errors="replace")) for f in files}
    out: List[dict] = []
    for fname, blks in all_blocks.items():
        if len(blks) > 1:
            lines_str = ", ".join(str(ln) for ln, _ in blks)
            out.append({
                "file": fname,
                "line": blks[1][0],
                "verdict": "dup-main",
                "refs": [],
                "note": f"несколько блоков «Главное» в одном документе (строки {lines_str}) — должен быть один",
                "text": blks[1][1][:300],
            })
    first_blocks = {fname: blks[0][1] for fname, blks in all_blocks.items() if blks and len(blks[0][1]) > 40}
    names = list(first_blocks.keys())
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            ba, bb = first_blocks[a], first_blocks[b]
            r = difflib.SequenceMatcher(None, ba, bb).ratio()
            shorter, longer = (ba, bb) if len(ba) <= len(bb) else (bb, ba)
            prefix_r = difflib.SequenceMatcher(None, shorter, longer[:len(shorter)]).ratio() if len(shorter) > 60 else 0.0
            best_r = max(r, prefix_r)
            if best_r >= 0.8:
                out.append({"file": b, "line": all_blocks[b][0][0], "verdict": "dup-main", "refs": [],
                            "note": f"«Главное» совпадает с {a} на {best_r:.0%} — напиши вывод именно этого документа",
                            "text": bb[:300]})
    return out


UNCHANGED = re.compile(r"без изменени|не изменил|проверено", re.I)


def revised_objects(ledger: L.Ledger, change: str, max_depth: int = 3) -> Set[str]:
    """Объекты, изменённые по событию по существу: затронуты impact не дальше max_depth (обычно CLM → HYP → ALT),
    в записи упомянут CHG, и это упоминание — не «проверено, без изменений». Включает изменённые
    обещания и эксперименты. Статус решения (DEC) проверяется по блоку пересмотра."""
    res = L.impact(ledger, [change])
    out = set()
    for oid, (depth, _) in res.items():
        if oid == change or depth > max_depth or L.prefix(oid) == "DEC":
            continue
        o = ledger.objects.get(oid, {})
        notes = [str(v) for v in _walk(o) if re.search(r"(?<![\w-])" + re.escape(change) + r"(?![\w-])", str(v))]
        if notes and not all(UNCHANGED.search(n) for n in notes):
            out.add(oid)
    return out


def _walk(x):
    if isinstance(x, dict):
        for v in x.values():
            yield from _walk(v)
    elif isinstance(x, list):
        for v in x:
            yield from _walk(v)
    else:
        yield x


REVISION_MARK = re.compile(
    r"\b(?:пересмотрен[аоы]?|проверен[аоы]?|без\s+изменений)\s+по\s+\[?(CHG-\d+)(?![\w-])", re.I
)
HISTORY_HEAD = re.compile(r"^##\s+(?:Исторический\s+снимок|История\s+до|Архив\s+до)\b", re.I)
DECISION_SECTION = re.compile(r"^#{1,2}\s+(.+)$")


def marked_for_change(line: str, change: str) -> bool:
    return any(m.group(1) == change for m in REVISION_MARK.finditer(line))


def brief_request_quote(text: str) -> str:
    """Only the explicitly attributed request quotation in the first brief."""
    request = re.search(r"(?m)^\*\*Исходный запрос \(дословно\):\*\*[ \t]*\n((?:>[^\n]*(?:\n|$))+)", text)
    if not request:
        return ""
    return " ".join(line[1:].strip() for line in request[1].splitlines()).strip("«»")


def is_request_constraint(line: str, quote: str) -> bool:
    cells = [cell.strip() for cell in line.strip("|").split("|")]
    if len(cells) != 3 or cells[2] != "подтверждено запросом":
        return False
    field = cells[0].split(" (", 1)[0]
    if field not in {"Ограничения", "Горизонт и срок решения"} or not cells[1]:
        return False
    # Check only the edges of the copied sentence: an abbreviation inside it is literal.
    end = r"(?=$|\s+[А-ЯЁA-Z0-9])" if cells[1][-1] in ".!?" else "$"
    return re.search(r"(?:^|[.!?]\s+)" + re.escape(cells[1]) + end, quote) is not None


def blocking_verdicts(*, strict: bool = False) -> set[str]:
    verdicts = {"wrong-ref", "stale-ref", "dup-main", "stale?"}
    if strict:
        verdicts.add("unsupported")
    return verdicts


def verify(project: Path, file_filter: List[str], change: str = "", *, exact_files: bool = False) -> dict:
    ledger = L.Ledger(project)
    out_dir = project / "outputs"
    files = sorted(out_dir.glob("*.md"))
    if exact_files:
        names = set(file_filter)
        files = [file for file in files if file.name in names]
    elif file_filter:
        files = [file for file in files if any(file.name.startswith(prefix) for prefix in file_filter)]
    items = []
    revised = revised_objects(ledger, change) if change else set()
    for f in files:
        historical = False
        decision_history = False
        text = f.read_text(encoding="utf-8")
        request_quote = brief_request_quote(text) if f.name == "01_brief.md" else ""
        for ln, raw in enumerate(text.splitlines(), 1):
            heading = raw.strip()
            if HISTORY_HEAD.match(heading):
                historical = True
            if f.name.startswith("08_") and (section := DECISION_SECTION.match(heading)):
                decision_history = heading.startswith("## ") and section[1].casefold() == "история пересмотров"
            line = normalize_short_lists(heading)
            if not line or SKIP_LINE.match(line):
                continue
            if META.search(line):
                continue  # служебные цифры процесса (счётчик реестра, шкала оценок), а не утверждения о мире
            refs = expand_refs(line)
            nums = numbers(line, table_row=line.startswith("|"))
            # В таблицах экспериментов порог и его единицу сверяет проверка Stop/Go.
            exp_row = line.startswith("|") and f.name.startswith("07") and any(r.startswith("EXP-") for r in refs)
            small_percents = set()
            if line.startswith("|") and not exp_row:
                small_percents = small_percentages(line)
            if not refs and not (nums or small_percents):
                continue
            verdict, note = "ok", ""
            touched = [r for r in refs if r in revised]
            if touched and not (historical or decision_history) and not marked_for_change(line, change):
                items.append({"file": f.name, "line": ln, "verdict": "stale?", "refs": touched,
                              "note": f"ссылается на пересмотренные по {change}: {', '.join(touched)} — "
                                      f"перепиши на месте или пометь «проверено по {change}»", "text": line[:300],
                              "raw_text": raw})
                continue
            missing = [r for r in refs if r not in ledger.objects]
            stale = [r for r in refs if r in ledger.objects
                     and ledger.objects[r].get("status", "active") != "active"]
            if missing:
                verdict, note = "wrong-ref", "нет в реестре: " + ", ".join(missing)
            elif stale:
                verdict = "historical-ref" if decision_history else "stale-ref"
                note = ("исторические записи: " if decision_history else "устарело: ") + ", ".join(
                    f"{r} ({ledger.objects[r].get('status')}→{ledger.objects[r].get('superseded_by', '—')})" for r in stale)
            elif (nums or small_percents) and not refs:
                if request_quote and line.startswith("|") and is_request_constraint(heading, request_quote):
                    note = "ограничение дословно из исходного запроса"
                else:
                    verdict, note = "unsupported", "числа без ссылки: " + ", ".join(sorted(nums | small_percents))
            elif refs:
                lost = set()
                if nums or small_percents:
                    evidence = " ".join(obj_text(ledger, r) for r in refs)
                    pool = norm_numbers_in(evidence)
                    lost = {n for n in nums if n not in pool and n.replace(".", ",") not in pool}
                    if small_percents:
                        lost |= small_percents - small_percentages(evidence, accept_decimal=True)
                if lost:
                    verdict, note = "check", "числа не найдены в ссылках: " + ", ".join(sorted(lost))
                elif not exp_row and (units := direct_rate_mismatch(line, refs, ledger)):
                    verdict, note = "check", f"единица числа не совпадает с утверждением: {units[0]} вместо {units[1]}"
            items.append({"file": f.name, "line": ln, "verdict": verdict, "note": note,
                          "refs": refs, "text": line[:300]})
    items += duplicate_mains(files)
    counts: Dict[str, int] = {}
    for it in items:
        counts[it["verdict"]] = counts.get(it["verdict"], 0) + 1
    total = len(items)
    return {"project": str(project), "checked": total, "counts": counts,
            "support_rate": round(counts.get("ok", 0) / total, 3) if total else None,
            "items": items}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Проверка утверждений в outputs/ кодом (T0)")
    ap.add_argument("project", type=Path)
    ap.add_argument("--files", nargs="*", default=[], help="префиксы файлов, например 03 04 05")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--change", default="", help="CHG-NNN: найти строки, не обновлённые после пересмотра")
    ap.add_argument("--all", action="store_true", help="показать и строки ok")
    ap.add_argument("--sample", type=int, default=0,
                    help="добавить N случайных строк ok для выборочной смысловой проверки моделью (seed 42)")
    ap.add_argument("--strict", action="store_true",
                    help="считать строки unsupported жёсткой ошибкой (код выхода 1)")
    args = ap.parse_args(argv)
    proj = args.project.resolve()
    if not (proj / "outputs").is_dir():
        print(f"Нет папки outputs в {proj}", file=sys.stderr)
        return 2
    if args.change and args.change not in L.Ledger(proj).objects:
        print(f"Нет события {args.change} в реестре", file=sys.stderr)
        return 2
    res = verify(proj, args.files, args.change)
    if args.json:
        print(json.dumps(res, ensure_ascii=False, indent=2))
    else:
        c = res["counts"]
        print(f"Проверено строк: {res['checked']}; " + ", ".join(f"{k}: {v}" for k, v in sorted(c.items())))
        if res["support_rate"] is not None:
            print(f"Claim support rate (код): {res['support_rate']:.1%}")
        todo = [i for i in res["items"] if args.all or i["verdict"] not in {"ok", "historical-ref"}]
        if args.sample and not args.all:
            import random
            oks = [i for i in res["items"] if i["verdict"] == "ok"]
            for i in random.Random(42).sample(oks, min(args.sample, len(oks))):
                todo.append(dict(i, verdict="sample", note="выборочная проверка: не преувеличивает ли строка источник"))
        if todo:
            print("\nНа проверку модели (только эти строки, не весь пакет):")
            for i in todo:
                print(f"- [{i['verdict']}] {i['file']}:{i['line']} — {i['note']}\n    {i['text'][:220]}")
    hard = sum(res["counts"].get(k, 0) for k in blocking_verdicts(strict=args.strict))
    return 1 if hard else 0


def _utf8_stdio() -> None:
    """Windows: консоль и пайпы по умолчанию в cp1251 — кириллица и знаки вроде «↺» роняют вывод."""
    for s in (sys.stdin, sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


if __name__ == "__main__":
    _utf8_stdio()
    sys.exit(main())

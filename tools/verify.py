#!/usr/bin/env python3
"""Детерминированная проверка утверждений в артефактах (первый проход верификатора, уровень T0).

    python tools/verify.py projects/<case> [--json] [--files 03 04 05 06 07]

Что проверяет кодом в outputs/*.md, построчно (абзац, пункт списка, строка таблицы):
  - строка с числом (цифры, %, ₽, $) без ссылки на реестр           → unsupported
  - ссылка на несуществующий объект                                   → wrong-ref
  - ссылка на объект со статусом retracted/superseded                 → stale-ref
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

REF = re.compile(r"\b(SRC|CLM|HYP|PAIN|ALT|DEC|PRM|EXP|CHG)-(\d{3})(?:\s*[–—-]\s*(?:(?:SRC|CLM|HYP|PAIN|ALT|DEC|PRM|EXP|CHG)-)?(\d{3}))?\b")
NUM = re.compile(r"(?<![\w.])(\d{1,3}(?:[  ]\d{3})+|\d+)(?:[.,](\d+))?(?![\w])")
# То, что числом не считаем: ID, даты, номера шагов/чекпоинтов/уровней/вопросов/пробелов, нумерация списков.
NOISE = re.compile(r"\b(?:SRC|CLM|HYP|PAIN|ALT|DEC|PRM|EXP|CHG)-\d{3}\b|\b\d{4}-\d{2}-\d{2}\b|\b[CTQG]\d+\b|"
                   r"\b(?i:шаг)[а-я]*\s+\d+|\b\d{2}_[a-z_]+\.md\b|\bv\d+\b")
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


MAIN_HEAD = re.compile(r"^\s*(#{1,4}\s*|\*\*)Главное", re.I)
HEAD = re.compile(r"^\s*#{1,4}\s")


def main_block(text: str) -> str:
    """Текст блока «Главное» (до следующего заголовка), нормализованный для сравнения."""
    lines, on = [], False
    for ln in text.splitlines():
        if MAIN_HEAD.match(ln):
            on = True
            continue
        if on and HEAD.match(ln):
            break
        if on:
            lines.append(ln)
    return re.sub(r"\s+", " ", " ".join(lines)).strip().lower()


def duplicate_mains(files: List[Path]) -> List[dict]:
    """Одинаковый «Главное» в разных документах: вывод каждого документа должен быть своим."""
    import difflib
    blocks = {f.name: main_block(f.read_text(encoding="utf-8")) for f in files}
    names = [n for n, b in blocks.items() if len(b) > 40]
    out = []
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            r = difflib.SequenceMatcher(None, blocks[a], blocks[b]).ratio()
            if r >= 0.8:
                out.append({"file": b, "line": 0, "verdict": "dup-main", "refs": [],
                            "note": f"«Главное» совпадает с {a} на {r:.0%} — напиши вывод именно этого документа",
                            "text": blocks[b][:300]})
    return out


UNCHANGED = re.compile(r"без изменени|не изменил|проверено", re.I)


def revised_objects(ledger: L.Ledger, change: str, max_depth: int = 3) -> Set[str]:
    """Объекты, изменённые по событию по существу: затронуты impact не дальше max_depth (обычно CLM → HYP → ALT),
    в записи упомянут CHG, и это упоминание — не «проверено, без изменений». Решение (DEC) и дальние слои
    не входят: их новый статус показывает блок пересмотра вверху документа, а не каждая строка."""
    res = L.impact(ledger, [change])
    out = set()
    for oid, (depth, _) in res.items():
        if oid == change or depth > max_depth or L.prefix(oid) in ("DEC", "EXP", "PRM"):
            continue
        o = ledger.objects.get(oid, {})
        notes = [str(v) for v in _walk(o) if change in str(v)]
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


REVISION_MARK = re.compile(r"пересмотрен|проверено по|без изменени", re.I)


def verify(project: Path, file_filter: List[str], change: str = "") -> dict:
    ledger = L.Ledger(project)
    out_dir = project / "outputs"
    items = []
    revised = revised_objects(ledger, change) if change else set()
    for f in sorted(out_dir.glob("*.md")):
        if file_filter and not any(f.name.startswith(p) for p in file_filter):
            continue
        for ln, raw in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
            line = normalize_short_lists(raw.strip())
            if not line or SKIP_LINE.match(line):
                continue
            if META.search(line):
                continue  # служебные цифры процесса (счётчик реестра, шкала оценок), а не утверждения о мире
            refs = expand_refs(line)
            nums = numbers(line, table_row=line.startswith("|"))
            if not refs and not nums:
                continue
            verdict, note = "ok", ""
            touched = [r for r in refs if r in revised]
            if touched and change not in line and not REVISION_MARK.search(line):
                items.append({"file": f.name, "line": ln, "verdict": "stale?", "refs": touched,
                              "note": f"ссылается на пересмотренные по {change}: {', '.join(touched)} — "
                                      f"перепиши на месте или пометь «проверено по {change}»", "text": line[:300]})
                continue
            missing = [r for r in refs if r not in ledger.objects]
            stale = [r for r in refs if r in ledger.objects
                     and ledger.objects[r].get("status", "active") != "active"]
            if missing:
                verdict, note = "wrong-ref", "нет в реестре: " + ", ".join(missing)
            elif stale:
                verdict, note = "stale-ref", "устарело: " + ", ".join(
                    f"{r} ({ledger.objects[r].get('status')}→{ledger.objects[r].get('superseded_by', '—')})" for r in stale)
            elif nums and not refs:
                verdict, note = "unsupported", "числа без ссылки: " + ", ".join(sorted(nums))
            elif nums:
                pool = norm_numbers_in(" ".join(obj_text(ledger, r) for r in refs))
                lost = sorted(n for n in nums if n not in pool and n.replace(".", ",") not in pool)
                if lost:
                    verdict, note = "check", "числа не найдены в ссылках: " + ", ".join(lost)
            items.append({"file": f.name, "line": ln, "verdict": verdict, "note": note,
                          "refs": refs, "text": line[:300]})
    files = [f for f in sorted(out_dir.glob("*.md"))
             if not file_filter or any(f.name.startswith(p) for p in file_filter)]
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
        todo = [i for i in res["items"] if args.all or i["verdict"] != "ok"]
        if args.sample and not args.all:
            import random
            oks = [i for i in res["items"] if i["verdict"] == "ok"]
            for i in random.Random(42).sample(oks, min(args.sample, len(oks))):
                todo.append(dict(i, verdict="sample", note="выборочная проверка: не преувеличивает ли строка источник"))
        if todo:
            print("\nНа проверку модели (только эти строки, не весь пакет):")
            for i in todo:
                print(f"- [{i['verdict']}] {i['file']}:{i['line']} — {i['note']}\n    {i['text'][:220]}")
    hard = sum(res["counts"].get(k, 0) for k in ("wrong-ref", "stale-ref", "dup-main", "stale?"))
    return 1 if hard else 0


if __name__ == "__main__":
    sys.exit(main())

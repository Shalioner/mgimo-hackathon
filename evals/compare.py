#!/usr/bin/env python3
"""Сравнение двух ответов на один кейс по протоколу evals/baseline/PROTOCOL.md.

    python evals/compare.py --sources projects/ryadom/sources --doc chat=evals/baseline/run1/answer.md \
        --doc agent=projects/ryadom/outputs/03_situation.md,…,07_validation_plan.md --out evals/baseline/run1 [--no-judge]

1) Числа без опоры — кодом: числа документа, которых нет в тексте источников.
2) Рубрика — tools/decide.py (модель решений; без ключа — эмуляция на дешёвой LLM), каждый документ отдельно.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
import decide as DC  # noqa: E402
import ledger as L  # noqa: E402
import verify as V  # noqa: E402

RUBRIC = ROOT / "evals" / "baseline" / "rubric.yaml"


FILE_REF = re.compile(r"\b\d{2}_[\w.\-]+")          # ссылки на материалы вида 02_google_location.md
ID_TAIL = re.compile(r"[–—-]\s*\d{3}\b")              # хвосты диапазонов ID: CLM-020–024


def meaningful(n: str) -> bool:
    """Число-утверждение, а не номер: не ID-хвост вида 005 и не счётчик/номер пункта ≤ 10."""
    if re.fullmatch(r"0\d+", n):
        return False
    return "." in n or float(n) > 10


def doc_numbers(text: str) -> list:
    out = []
    for raw in text.splitlines():
        line = ID_TAIL.sub(" ", FILE_REF.sub(" ", raw.strip()))
        if not line or V.SKIP_LINE.match(line) or V.META.search(line):
            continue
        out += sorted(n for n in V.numbers(line, table_row=line.startswith("|")) if meaningful(n))
    return out


def number_support(text: str, sources_text: str) -> dict:
    pool = V.numbers(sources_text.replace("\n", " "))
    pool |= {p.replace(".", ",") for p in pool}
    nums = doc_numbers(text)
    lost = [n for n in nums if n not in pool and n.replace(".", ",") not in pool]
    return {"numbers": len(nums), "not_in_sources": len(lost),
            "share_unsupported": round(len(lost) / len(nums), 3) if nums else 0.0,
            "examples": sorted(set(lost))[:15]}


URL = re.compile(r"https?://\S+")


def traceability(text: str, ledger_dir: Path = None) -> dict:
    """Строки с числами, у которых есть проверяемая ссылка: ID реестра, существующий в проекте (агент),
    URL или ссылка на материал кейса NN_файл (чат)."""
    led = L.Ledger(ledger_dir) if ledger_dir else None
    total = traced = 0
    for raw in text.splitlines():
        line = raw.strip()
        clean = ID_TAIL.sub(" ", FILE_REF.sub(" ", line))
        if not clean or V.SKIP_LINE.match(clean) or V.META.search(clean):
            continue
        if not [n for n in V.numbers(clean, table_row=clean.startswith("|")) if meaningful(n)]:
            continue
        total += 1
        ids = V.expand_refs(V.normalize_short_lists(line))
        if (led and any(i in led.objects for i in ids)) or URL.search(line) or FILE_REF.search(line):
            traced += 1
    return {"lines_with_numbers": total, "traced": traced, "share": round(traced / total, 3) if total else 0.0}


def judge(name: str, text: str) -> dict:
    tmp = Path(tempfile.mkdtemp(prefix="cmp-"))
    try:
        proj = tmp / "p"
        L.main(["init", str(proj), "--title", "сравнение"])
        doc = tmp / "document.md"
        doc.write_text(text, encoding="utf-8")
        rc = DC.main(["--project", str(proj), "--questions", str(RUBRIC), "--input", str(doc),
                      "--step", f"compare-{name}", "--timeout", "600"])
        files = sorted((proj / "state" / "delegated").glob("*_decide_rubric.json"))
        data = json.loads(files[-1].read_text(encoding="utf-8")) if files else {}
        tokens = [l for l in (proj / "state" / "run_log.md").read_text(encoding="utf-8").splitlines()
                  if "делегирование classify" in l]
        return {"rc": rc, "answers": data.get("answers", {}), "needs_review": data.get("needs_review", {}),
                "judge_log": tokens[-1] if tokens else ""}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sources", type=Path, required=True)
    ap.add_argument("--doc", action="append", required=True, help="имя=файл[,файл…]")
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--no-judge", action="store_true")
    ap.add_argument("--ledger", action="append", default=[], help="имя=папка проекта: ID в тексте проверяются по его реестру")
    args = ap.parse_args(argv)
    src = "\n".join(f.read_text(encoding="utf-8") for f in sorted(args.sources.glob("*.md")))
    res = {}
    for d in args.doc:
        name, files = d.split("=", 1)
        text = "\n\n".join(Path(f).read_text(encoding="utf-8") for f in files.split(","))
        leds = dict(x.split("=", 1) for x in args.ledger)
        r = {"chars": len(text), "numbers": number_support(text, src),
             "traceability": traceability(text, Path(leds[name]) if name in leds else None)}
        if not args.no_judge:
            r["rubric"] = judge(name, text)
        res[name] = r
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "compare.json").write_text(json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")
    for name, r in res.items():
        n = r["numbers"]
        tr = r["traceability"]
        line = (f"{name}: {r['chars']} симв.; чисел {n['numbers']}, нет в источниках {n['not_in_sources']} "
                f"({n['share_unsupported']:.0%}); строк с числами со ссылкой {tr['traced']}/{tr['lines_with_numbers']} ({tr['share']:.0%})")
        if "rubric" in r:
            yes = sum(1 for a in r["rubric"]["answers"].values() if a.get("type") == "noul" and a.get("noul", 0) >= 0.5)
            line += f"; рубрика «да» {yes}/{len(r['rubric']['answers'])}, сомнительно {len(r['rubric']['needs_review'])}"
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())

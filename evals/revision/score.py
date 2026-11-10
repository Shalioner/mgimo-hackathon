#!/usr/bin/env python3
"""Оценка пересмотра по эталону evals/revision/PROTOCOL.md.

    python evals/revision/score.py --name chat --docs evals/revision/chat/answer.md \
        --keep evals/baseline/run1/answer.md,evals/revision/chat/answer.md --out evals/revision/chat
    python evals/revision/score.py --name agent --docs <wt>/projects/ryadom/outputs/0[3-7]*.md \
        --before projects/ryadom/outputs --after <wt>/projects/ryadom/outputs --out evals/revision/agent

--docs   — где искать пересмотр (E1–E5, S4: модель решений);
--keep   — что считается итоговым состоянием для S1–S3 (числа, кодом); по умолчанию = --docs;
--before/--after — папки документов до и после: стабильность строк (difflib).
"""
from __future__ import annotations

import argparse
import difflib
import json
import re
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
import decide as DC  # noqa: E402
import ledger as L  # noqa: E402

RUBRIC = Path(__file__).resolve().parent / "rubric.yaml"
KEEP = {  # S1–S3: числа, которые должны сохраниться
    "S1 установки 3 100": r"3[\s ]?100", "S1 подключили 2 650": r"2[\s ]?650", "S1 позиция 2 540": r"2[\s ]?540",
    "S1 D7 31%": r"31\s?%", "S1 D7 6%": r"(?<!\d)6\s?%", "S2 цена 299 ₽": r"299", "S2 trial 7 дней": r"7\s?(дн|-дн)",
    "S3 октябрь 37%": r"37\s?%",
}
NEW = {"ноябрь 11,8%": r"11[,.]8", "ноябрь 12,2%": r"12[,.]2", "ноябрь 46%": r"46\s?%", "ноябрь 35%": r"35\s?%"}


def read(files):
    return "\n\n".join(Path(f).read_text(encoding="utf-8") for f in files)


def stability(before: Path, after: Path) -> dict:
    out, same_all, total_all = {}, 0, 0
    for b in sorted(before.glob("0[3-7]_*.md")):
        a = after / b.name
        old = b.read_text(encoding="utf-8").splitlines()
        new = a.read_text(encoding="utf-8").splitlines() if a.exists() else []
        sm = difflib.SequenceMatcher(None, old, new, autojunk=False)
        same = sum(bl.size for bl in sm.get_matching_blocks())
        out[b.name] = {"lines_before": len(old), "lines_after": len(new), "unchanged": same,
                       "changed": len(old) != same or len(new) != same}
        same_all += same
        total_all += len(old)
    return {"files": out, "unchanged_share": round(same_all / total_all, 3) if total_all else None}


def judge(text: str, name: str) -> dict:
    tmp = Path(tempfile.mkdtemp(prefix="rev-"))
    try:
        proj = tmp / "p"
        L.main(["init", str(proj), "--title", "пересмотр"])
        doc = tmp / "document.md"
        doc.write_text(text, encoding="utf-8")
        rc = DC.main(["--project", str(proj), "--questions", str(RUBRIC), "--input", str(doc),
                      "--step", f"revision-{name}", "--timeout", "600"])
        f = sorted((proj / "state" / "delegated").glob("*_decide_rubric.json"))
        data = json.loads(f[-1].read_text(encoding="utf-8")) if f else {}
        return {"rc": rc, "answers": data.get("answers", {}), "needs_review": data.get("needs_review", {})}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", required=True)
    ap.add_argument("--docs", nargs="+", required=True)
    ap.add_argument("--keep")
    ap.add_argument("--before", type=Path)
    ap.add_argument("--after", type=Path)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--no-judge", action="store_true")
    args = ap.parse_args(argv)
    docs = read(args.docs)
    keep = read(args.keep.split(",")) if args.keep else docs
    res = {"name": args.name, "chars": len(docs),
           "kept": {k: bool(re.search(p, keep)) for k, p in KEEP.items()},
           "new_numbers": {k: bool(re.search(p, docs)) for k, p in NEW.items()}}
    if args.before and args.after:
        res["stability"] = stability(args.before, args.after)
    if not args.no_judge:
        res["rubric"] = judge(docs, args.name)
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "score.json").write_text(json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in res.items() if k != "rubric"}, ensure_ascii=False, indent=1))
    if "rubric" in res:
        for q, a in res["rubric"]["answers"].items():
            print(f"  {q}: {'да' if a.get('noul', 0) >= 0.5 else 'нет'} ({a.get('noul')})"
                  + ("  — сомнительно" if q in res["rubric"]["needs_review"] else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Счёт раунда polite2 (PROTOCOL.md). --split dev|test; --show — тексты пропусков и ложных (для test — только после заморозки)."""
import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT / "tools" / "hooks"))
import strategist_hook as H  # noqa: E402

SETS = {"dev": [HERE.parent / "sets" / "polite.json"],
        "test": [HERE / "sets" / "polite_sol.json", HERE / "sets" / "polite_luna.json"]}


def norm(t):
    return re.sub(r"\s+", " ", t.strip().lower().replace("ё", "е"))


def excluded():
    src = (ROOT / "tests" / "test_hooks.py").read_text(encoding="utf-8")
    out = {norm(m) for m in re.findall(r'"([^"\n]{3,})"', src)}
    out |= {norm(i["text"]) for i in json.loads(SETS["dev"][0].read_text(encoding="utf-8"))["items"]}
    return out


def score(split, show=False):
    ex = excluded() if split == "test" else set()
    res = {"lexicon_sha256": hashlib.sha256((ROOT / "tools/hooks/objection_lexicon.py").read_bytes()).hexdigest()[:16],
           "sets": {}}
    tot = {}
    for f in SETS[split]:
        by, rows, skipped = {}, [], 0
        for it in json.loads(f.read_text(encoding="utf-8"))["items"]:
            if norm(it["text"]) in ex:
                skipped += 1
                continue
            labels = H.detect_objection(it["text"])
            hit = bool(labels)
            for d in (by, tot):
                d.setdefault(it["class"], [0, 0])
                d[it["class"]][0] += hit
                d[it["class"]][1] += 1
            rows.append({"class": it["class"], "hit": hit, "labels": labels, "text": it["text"]})
        res["sets"][f.stem] = {"by_class": by, "excluded": skipped}
        print(f.stem, ", ".join(f"{c}: {h}/{n}" for c, (h, n) in sorted(by.items())), f"(исключено {skipped})")
        if show:
            for r in rows:
                if (r["class"] == "objection") != r["hit"]:
                    print(f"  {'ПРОПУСК' if r['hit'] is False else 'ЛОЖНОЕ '} [{r['class']}] {r['labels']} {r['text']}")
    res["total"] = tot
    print("всего", ", ".join(f"{c}: {h}/{n}" for c, (h, n) in sorted(tot.items())), "sha", res["lexicon_sha256"])
    return res


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", choices=("dev", "test"), required=True)
    ap.add_argument("--show", action="store_true")
    ap.add_argument("--out")
    a = ap.parse_args()
    r = score(a.split, a.show)
    if a.out:
        Path(a.out).write_text(json.dumps(r, ensure_ascii=False, indent=1), encoding="utf-8")

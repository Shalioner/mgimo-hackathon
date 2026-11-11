#!/usr/bin/env python3
"""Оценка хука возражений на отложенных наборах (протокол — evals/objections/PROTOCOL.md).

    python evals/objections/score.py                 # все наборы sets/*.json → results.json + сводка
    python evals/objections/score.py --split dev     # только прогоны 1 (для доработки словаря)
    python evals/objections/score.py --show-misses   # показать пропуски и ложные срабатывания (только dev!)
    python evals/objections/score.py --dir evals/objections/round2 --exclude evals/objections/sets   # раунд 2

Имя набора: <модель>_r<N>.json, например sol_r1.json. Хук вызывается так же, как в среде:
strategist_hook.detect_objection (вырезает цитаты и код, затем словарь).
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
spec = importlib.util.spec_from_file_location("strategist_hook", ROOT / "tools" / "hooks" / "strategist_hook.py")
HOOK = importlib.util.module_from_spec(spec)
spec.loader.exec_module(HOOK)

LEVELS = ("direct", "business", "subtle")
KINDS = ("task", "question", "ack", "style", "terms")


def tuned_phrases(exclude_dirs=()) -> set:
    """Фразы из tests/test_hooks.py и из наборов прошлых раундов — их из замера исключаем."""
    src = (ROOT / "tests" / "test_hooks.py").read_text(encoding="utf-8")
    out = {norm(m) for m in re.findall(r'"([^"\n]{3,})"', src)}
    for d in exclude_dirs:
        for f in Path(d).glob("*_r*.json"):
            out |= {norm(it.get("text", "")) for it in json.loads(f.read_text(encoding="utf-8"))["items"]}
    return out


def norm(t: str) -> str:
    return re.sub(r"\s+", " ", t.strip().lower().replace("ё", "е"))


def load(split: str, base: Path = HERE):
    sets = {}
    for f in sorted((base / "sets").glob("*_r*.json")):
        run = int(re.search(r"_r(\d+)", f.stem).group(1))
        if split == "dev" and run != 1 or split == "test" and run == 1:
            continue
        data = json.loads(f.read_text(encoding="utf-8"))
        sets[f.stem] = data["items"]
    return sets


def rate(hit, n):
    return round(hit / n, 3) if n else None


def score_items(items, tuned):
    out = {"objection": [0, 0], "neutral": [0, 0], "levels": {}, "kinds": {}, "excluded": 0,
           "misses": [], "false_hits": []}
    for it in items:
        text = it.get("text", "")
        if norm(text) in tuned:
            out["excluded"] += 1
            continue
        hit = bool(HOOK.detect_objection(text))
        key = "objection" if it.get("objection") else "neutral"
        out[key][0] += hit
        out[key][1] += 1
        sub = out["levels"] if key == "objection" else out["kinds"]
        cat = it.get("level") if key == "objection" else it.get("kind")
        sub.setdefault(cat or "?", [0, 0])
        sub[cat or "?"][0] += hit
        sub[cat or "?"][1] += 1
        if key == "objection" and not hit:
            out["misses"].append(text)
        if key == "neutral" and hit:
            out["false_hits"].append((text, HOOK.detect_objection(text)))
    return out


def summary(s):
    return {"recall": rate(*s["objection"]), "fpr": rate(*s["neutral"]),
            "n_obj": s["objection"][1], "n_neu": s["neutral"][1], "excluded": s["excluded"],
            "recall_by_level": {k: rate(*v) for k, v in sorted(s["levels"].items())},
            "fpr_by_kind": {k: rate(*v) for k, v in sorted(s["kinds"].items())}}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", choices=("all", "dev", "test"), default="all")
    ap.add_argument("--show-misses", action="store_true")
    ap.add_argument("--dir", type=Path, default=HERE, help="папка раунда (в ней sets/)")
    ap.add_argument("--exclude", nargs="*", default=[], help="папки наборов прошлых раундов: их фразы исключаются")
    args = ap.parse_args(argv)
    base = args.dir.resolve()
    sets = load(args.split, base)
    if not sets:
        print("Нет наборов в evals/objections/sets/", file=sys.stderr)
        return 2
    tuned = tuned_phrases(args.exclude)
    per_set = {name: score_items(items, tuned) for name, items in sets.items()}
    by_model = {}
    for name, s in per_set.items():
        by_model.setdefault(name.split("_r")[0], []).append(summary(s))
    pooled_items, seen = [], set()
    for items in sets.values():
        for it in items:
            k = (norm(it.get("text", "")), bool(it.get("objection")))
            if k not in seen:
                seen.add(k)
                pooled_items.append(it)
    pooled = summary(score_items(pooled_items, tuned))
    lex = (ROOT / "tools" / "hooks" / "objection_lexicon.py").read_bytes()
    res = {"split": args.split, "lexicon_sha256": hashlib.sha256(lex).hexdigest()[:16],
           "sets": {n: summary(s) for n, s in per_set.items()},
           "models": {m: {"runs": len(v),
                          "recall_mean": round(sum(x["recall"] for x in v) / len(v), 3),
                          "recall_range": [min(x["recall"] for x in v), max(x["recall"] for x in v)],
                          "fpr_mean": round(sum(x["fpr"] for x in v) / len(v), 3),
                          "fpr_range": [min(x["fpr"] for x in v), max(x["fpr"] for x in v)]}
                      for m, v in sorted(by_model.items())},
           "pooled_unique": pooled}
    (base / f"results_{args.split}.json").write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"Словарь sha256 {res['lexicon_sha256']}, наборов: {len(sets)} ({args.split})")
    for m, v in res["models"].items():
        print(f"  {m:6} полнота {v['recall_mean']:.0%} ({v['recall_range'][0]:.0%}–{v['recall_range'][1]:.0%}), "
              f"ложные {v['fpr_mean']:.0%} ({v['fpr_range'][0]:.0%}–{v['fpr_range'][1]:.0%})")
    p = pooled
    print(f"  всего уникальных: полнота {p['recall']:.0%} из {p['n_obj']}, ложные {p['fpr']:.0%} из {p['n_neu']}; "
          f"по уровням {p['recall_by_level']}; ложные по типам {p['fpr_by_kind']}")
    if args.show_misses:
        if args.split != "dev":
            print("--show-misses только для --split dev (test не смотрим по фразам до доработки)", file=sys.stderr)
            return 2
        for n, s in per_set.items():
            print(f"\n[{n}] пропуски:", *s["misses"], sep="\n  - ")
            print(f"[{n}] ложные:", *[f"{t} → {l}" for t, l in s["false_hits"]], sep="\n  - ")
    return 0


if __name__ == "__main__":
    sys.exit(main())

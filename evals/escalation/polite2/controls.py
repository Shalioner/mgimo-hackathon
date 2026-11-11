#!/usr/bin/env python3
"""Контроли раунда polite2: test-наборы раундов 1–4 тем же score.py, без перезаписи их results_*.json."""
import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
OBJ = ROOT / "evals" / "objections"
spec = importlib.util.spec_from_file_location("score", OBJ / "score.py")
S = importlib.util.module_from_spec(spec)
spec.loader.exec_module(S)

ROUNDS = {  # папка → наборы прошлых раундов, чьи фразы исключаются (как в протоколах раундов)
    "r1": (OBJ, []),
    "r2": (OBJ / "round2", [OBJ / "sets"]),
    "r3": (OBJ / "round3", [OBJ / "sets", OBJ / "round2" / "sets"]),
    "r4": (OBJ / "round4", [OBJ / "sets", OBJ / "round2" / "sets", OBJ / "round3" / "sets"]),
}


def run():
    out = {}
    for r, (d, ex) in ROUNDS.items():
        tuned = S.tuned_phrases(ex)
        items, seen = [], set()
        for its in S.load("test", d).values():
            for it in its:
                k = (S.norm(it.get("text", "")), bool(it.get("objection")))
                if k not in seen:
                    seen.add(k)
                    items.append(it)
        s = S.score_items(items, tuned)
        out[r] = {"recall": s["objection"], "false": s["neutral"]}
    return out


if __name__ == "__main__":
    res = run()
    for r, v in res.items():
        print(f"{r}: полнота {v['recall'][0]}/{v['recall'][1]}, ложных {v['false'][0]}/{v['false'][1]}")
    if len(sys.argv) > 1:
        Path(sys.argv[1]).write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")

#!/usr/bin/env python3
"""Клетки A–D по замороженному хуку и выборка в прогон (PROTOCOL.md, seed 20261111) → runs/plan.json."""
import json
import random
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[2] / "tools" / "hooks"))
import strategist_hook as H  # noqa: E402

items = []
for f in sorted((HERE / "sets").glob("gen-*.json")):
    for it in json.loads(f.read_text(encoding="utf-8"))["items"]:
        hit = bool(H.detect_objection(it["text"]))
        obj = bool(it.get("objection"))
        items.append({**it, "source": f.stem, "hint": hit, "cell": {(1, 1): "A", (0, 1): "B", (1, 0): "C", (0, 0): "D"}[(obj, hit)]})
rng = random.Random(20261111)
limits = {"A": 12, "B": 16, "C": 10, "D": 6}
plan = []
for cell, k in limits.items():
    pool = [it for it in items if it["cell"] == cell]
    plan += rng.sample(pool, min(k, len(pool)))
for i, r in enumerate(plan, 1):
    r["run"] = f"o5-{i:02d}"
(HERE / "runs" / "cells.json").write_text(json.dumps(items, ensure_ascii=False, indent=1), encoding="utf-8")
(HERE / "runs" / "plan.json").write_text(json.dumps(plan, ensure_ascii=False, indent=1), encoding="utf-8")
print({c: sum(it["cell"] == c for it in items) for c in "ABCD"}, "→ в прогон", {c: sum(r["cell"] == c for r in plan) for c in "ABCD"})

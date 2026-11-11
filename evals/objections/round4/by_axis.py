"""Сводка раунда 4 по модели и по effort: python evals/objections/round4/by_axis.py results_test.json"""
import json
import sys
from pathlib import Path

res = json.loads((Path(__file__).parent / sys.argv[1]).read_text(encoding="utf-8"))
axes = {"модель": {}, "effort": {}}
for name, s in res["sets"].items():
    model, effort = name.split("_r")[0].split("-")
    for axis, key in (("модель", model), ("effort", effort)):
        a = axes[axis].setdefault(key, [0, 0, 0, 0])
        a[0] += round(s["recall"] * s["n_obj"]); a[1] += s["n_obj"]
        a[2] += round(s["fpr"] * s["n_neu"]); a[3] += s["n_neu"]
for axis, d in axes.items():
    print(axis)
    for k, (h, n, f, m) in d.items():
        print(f"  {k:7} полнота {h}/{n} = {h / n:.0%}, ложные {f}/{m} = {f / m:.0%}")
worst = sorted(res["sets"].items(), key=lambda kv: kv[1]["recall"])
print("взгляды по полноте:", ", ".join(f"{n.split('_r')[0]} {s['recall']:.0%}/{s['fpr']:.0%}" for n, s in worst))

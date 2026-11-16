#!/usr/bin/env python3
"""D1 GGUF (llama.cpp /v1/systemone) против сохранённого MLX-прогона на тех же 20 задачах.

Задачи, вопросы и порог .75 — как в d1_quality.py (question_scope=all). Сеть — только 127.0.0.1.
Исполнители и облачные API не вызываются.

    python evals/routing/d1_gguf_parity.py --url http://127.0.0.1:8089/v1/systemone \
        --mlx docs/research/d1-routing-quality-2026-11-15 --output <file.json>
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
import decide as C  # noqa: E402
import ledger as L  # noqa: E402
import route_request as R  # noqa: E402


def post(url, body, timeout=120):
    req = urllib.request.Request(url, data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
                                 headers={"Content-Type": "application/json"})
    began = time.perf_counter()
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = json.loads(r.read().decode("utf-8"))
    return data, time.perf_counter() - began


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--url", required=True)
    p.add_argument("--mlx", type=Path, required=True)
    p.add_argument("--cases", type=Path, default=ROOT / "evals/routing/d1_quality_cases.json")
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--repeat", type=int, default=1, help="повторить весь набор N раз (стабильность)")
    args = p.parse_args()
    if not args.url.startswith("http://127.0.0.1:"):
        raise SystemExit("Только локальный сервер 127.0.0.1")
    cases = json.loads(args.cases.read_text(encoding="utf-8"))["cases"]
    cfg = L.load_routing()
    qs = R.questions(cfg)
    gate = {k: qs[k] for k in ("tier", "effort")}
    rows, times = [], []
    for rep in range(args.repeat):
        for c in cases:
            state = json.dumps({"request": c["request"], "context": c["context"]}, ensure_ascii=False)
            data, sec = post(args.url, {"state": state, "questions": qs})
            times.append(sec)
            valid = C.valid_response(data, qs)
            labels = R.classified(data, gate, .75) if valid else None
            ans = data.get("answers", {}) if isinstance(data, dict) else {}
            mlx_file = args.mlx / f"{c['id']}.json"
            mlx = None
            if mlx_file.exists():
                rep_ = json.loads(mlx_file.read_text(encoding="utf-8"))["report"]
                mresp = (rep_.get("classification") or {}).get("response") or {}
                mlx = {k: {"choice": v.get("choice"), "confidence": v.get("confidence")}
                       for k, v in (mresp.get("answers") or {}).items()}
            rows.append({"id": c["id"], "repeat": rep, "seconds": round(sec, 4), "valid": valid,
                         "reference": {k: c[k] for k in ("tier", "effort", "capability")},
                         "gguf": {k: {"choice": v.get("choice"), "confidence": v.get("confidence")} for k, v in ans.items()},
                         "mlx": mlx, "selected_labels": labels})

    def share(pred):
        return sum(1 for r in rows if pred(r))

    first = [r for r in rows if r["repeat"] == 0]
    tier_ok = sum(1 for r in first if r["gguf"].get("tier", {}).get("choice") in r["reference"]["tier"])
    eff_ok = sum(1 for r in first if r["gguf"].get("effort", {}).get("choice") in r["reference"]["effort"])
    both_ok = sum(1 for r in first if r["gguf"].get("tier", {}).get("choice") in r["reference"]["tier"]
                  and r["gguf"].get("effort", {}).get("choice") in r["reference"]["effort"])
    selected = [r for r in first if r["selected_labels"]]
    sel_err = sum(1 for r in selected if r["selected_labels"]["tier"] not in r["reference"]["tier"]
                  or r["selected_labels"]["effort"] not in r["reference"]["effort"])
    agree = [r for r in first if r["mlx"]]
    tier_agree = sum(1 for r in agree if r["mlx"].get("tier", {}).get("choice") == r["gguf"].get("tier", {}).get("choice"))
    drift = [abs(r["mlx"][k]["confidence"] - r["gguf"][k]["confidence"]) for r in agree for k in ("tier", "effort", "capability")
             if k in r["mlx"] and k in r["gguf"] and r["mlx"][k]["choice"] == r["gguf"][k]["choice"]]
    flips = 0
    if args.repeat > 1:
        by = {}
        for r in rows:
            by.setdefault(r["id"], set()).add(json.dumps(r["gguf"], sort_keys=True))
        flips = sum(1 for v in by.values() if len(v) > 1)
    summary = {
        "cases": len(first), "valid": share(lambda r: r["valid"]), "requests": len(rows),
        "tier_match": tier_ok, "effort_match": eff_ok, "tier_effort_match": both_ok,
        "selected_at_075": len(selected), "errors_among_selected": sel_err,
        "tier_agree_with_mlx": f"{tier_agree}/{len(agree)}",
        "confidence_drift_vs_mlx_same_choice": {"median": round(statistics.median(drift), 4) if drift else None,
                                                "max": round(max(drift), 4) if drift else None, "n": len(drift)},
        "latency_seconds": {"median": round(statistics.median(times), 4), "max": round(max(times), 4),
                            "min": round(min(times), 4)},
        "answers_changed_between_repeats": flips if args.repeat > 1 else None,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps({"summary": summary, "rows": rows}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

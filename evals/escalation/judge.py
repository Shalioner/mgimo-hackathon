#!/usr/bin/env python3
"""Оценка части 1 (PROTOCOL.md): по журналу главной сессии Codex — уровень подсказки, правки и чтение run_log по ходам.

    python evals/escalation/judge.py     # → runs/judged.json и сводка
"""
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "objections"))
import leak_check as L  # noqa: E402

MUTATE = re.compile(r'"name": "apply_patch"|\*\*\* (?:Update|Add|Delete) File|ledger\.py\W+(?:supersede|retract|add|log)\b'
                    r'|>\s*\S*projects/|\btee\b|write_text\(|\bsed\s+-i')


def level(blob):
    if "-е возражение за 30 минут" in blob:
        return 3
    if "повторное возражение" in blob:
        return 2
    if "похоже на возражение по существу" in blob:
        return 1
    return 0


def turns(name, texts):
    """Ходы главной сессии: границы — сообщения человека с текстом хода."""
    for f in L.rollouts(name):
        lines = f.read_text(encoding="utf-8", errors="replace").splitlines()
        if not any(texts[0][-60:].replace('"', '\\"') in ln or texts[0][-40:] in ln for ln in lines):
            continue
        out, cur = [], None
        for ln in lines:
            try:
                p = json.loads(ln).get("payload") or {}
            except ValueError:
                continue
            blob = json.dumps(p, ensure_ascii=False)
            if p.get("type") == "message" and p.get("role") == "user":
                for i, t in enumerate(texts):
                    if t[-40:] in blob and (cur is None or cur["turn"] < i + 1):
                        cur = {"turn": i + 1, "hint": 0, "calls": 0, "mutations": 0, "run_log": False, "answer": ""}
                        out.append(cur)
            if cur is None:
                continue
            if p.get("type") == "message" and p.get("role") != "assistant" and "Стратег:" in blob:
                cur["hint"] = max(cur["hint"], level(blob))
            if p.get("type") in ("function_call", "custom_tool_call", "local_shell_call", "exec_command"):
                cur["calls"] += 1
                cur["mutations"] += bool(MUTATE.search(blob))
                cur["run_log"] |= "run_log" in blob
            if p.get("type") == "message" and p.get("role") == "assistant":
                cur["answer"] = " ".join(c.get("text", "") for c in p.get("content", []) if isinstance(c, dict))
        return out
    return []


def main():
    res = []
    for sc in json.loads((HERE / "scenarios.json").read_text(encoding="utf-8")):
        name = "o9-" + sc["id"].lower()
        res.append({"id": sc["id"], "type": sc["type"], "turns": turns(name, sc["turns"])})
    (HERE / "runs" / "judged.json").write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    for r in res:
        t = r["turns"]
        print(r["id"], " | ".join(f"ход {x['turn']}: подсказка {x['hint']}, вызовов {x['calls']}, правок {x['mutations']}"
                                  f"{', run_log' if x['run_log'] else ''}" for x in t) or "нет журнала")


if __name__ == "__main__":
    main()

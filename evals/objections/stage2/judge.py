#!/usr/bin/env python3
"""Оценка второго этапа по журналам сессий Codex (правила — PROTOCOL.md, «Что считается самоаудитом»).

    python evals/objections/stage2/judge.py   # читает runs/plan.json, пишет runs/judged.json и печатает сводку
"""
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import leak_check as L  # noqa: E402

HINT = "похоже на возражение по существу"
AUDIT = re.compile(r"skills/self-audit|ledger\.py\W+trace\b")  # критерий протокола
SKILL = re.compile(r"skills/self-audit")  # строгий, введён после прогонов: trace — обычный инструмент
LEDGER_EDIT = re.compile(r"ledger\.py\W+(?:supersede|retract|add)\b|state/ledger/[\w.]+\.yaml.{0,40}(?:\*\*\* (?:Update|Add)|>|write)"
                         r"|\*\*\* (?:Update|Add) File: [^\n]*state/ledger/")


def session(name):
    hint = audit = edit = skill = False
    calls, last = 0, ""
    for f in L.rollouts(name):
        for line in f.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            p = rec.get("payload") or {}
            blob = json.dumps(p, ensure_ascii=False)
            if p.get("type") == "message" and p.get("role") != "assistant" and HINT in blob:
                hint = True
            if p.get("type") in ("function_call", "custom_tool_call", "local_shell_call", "exec_command"):
                calls += 1
                audit |= bool(AUDIT.search(blob))
                skill |= bool(SKILL.search(blob))
                edit |= bool(LEDGER_EDIT.search(blob))
            if p.get("type") == "message" and p.get("role") == "assistant":
                last = " ".join(c.get("text", "") for c in p.get("content", []) if isinstance(c, dict))
    return {"hint_seen": hint, "audit": audit, "skill_read": skill, "ledger_edit": edit, "tool_calls": calls, "last_answer": last}


def main():
    plan = json.loads((HERE / "runs" / "plan.json").read_text(encoding="utf-8"))
    out = []
    for r in plan:
        out.append({**r, **session(r["run"])})
    (HERE / "runs" / "judged.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    for cell in "ABCD":
        rs = [r for r in out if r["cell"] == cell]
        if rs:
            n = len(rs)
            print(f"{cell}: n={n} аудит {sum(r['audit'] for r in rs)}/{n}, навык {sum(r['skill_read'] for r in rs)}/{n}, правка реестра {sum(r['ledger_edit'] for r in rs)}/{n}, "
                  f"подсказка дошла {sum(r['hint_seen'] for r in rs)}/{n}, без сессии {sum(r['tool_calls'] == 0 and not r['last_answer'] for r in rs)}")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Проверка утечки: читал ли генератор словарь или примеры (PROTOCOL.md, «Условия»).

    python evals/objections/leak_check.py obj-sol-1 obj-sol-2 ...

Ищет сессии Codex по рабочей папке (state_5.sqlite во всех домах Codex, как tools/cost.py),
и в их журналах (rollout) — вызовы инструментов, где упомянуты запрещённые пути.
"""
import json
import sqlite3
from urllib.parse import quote
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
import cost as C  # noqa: E402

FORBIDDEN = ("tools/hooks", "objection_lexicon", "strategist_hook", "tests/", "test_hooks", "WORK_LOG",
             "DESCRIPTION", "evals/objections/PROTOCOL", "evals/objections/score", "self-audit",
             "objections/sets/", "RESULTS", "round2/PROTOCOL", "results_")


def rollouts(name: str):
    for home in C.codex_homes():
        db = home / "state_5.sqlite"
        if not db.exists():
            continue
        try:
            con = sqlite3.connect(f"file:{quote(str(db))}?mode=ro", uri=True)
        except sqlite3.Error:
            continue
        try:
            rows = con.execute("select rollout_path from threads where cwd like ?", (f"%/{name}",)).fetchall()
        except sqlite3.Error as exc:
            print(f"  (пропущен {db}: {exc})", file=sys.stderr)
            rows = []
        finally:
            con.close()
        for (path,) in rows:
            if path and Path(path).exists():
                yield Path(path)


def tool_calls(rollout: Path):
    for line in rollout.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        p = rec.get("payload") or {}
        if p.get("type") in ("function_call", "custom_tool_call", "local_shell_call", "exec_command"):
            yield json.dumps(p, ensure_ascii=False)


def main(names):
    bad = 0
    for name in names:
        files = list(rollouts(name))
        calls = [c for f in files for c in tool_calls(f)]
        hits = [c[:200] for c in calls if any(x in c for x in FORBIDDEN)]
        print(f"{name}: сессий {len(files)}, вызовов инструментов {len(calls)}, подозрительных {len(hits)}")
        for h in hits:
            print("   ", h)
        bad += bool(hits) or not files
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

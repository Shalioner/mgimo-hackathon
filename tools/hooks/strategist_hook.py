#!/usr/bin/env python3
"""Хуки агента «Стратег» — один скрипт для Claude Code и Codex (формат событий у них совместим).

  session-start  → печатает `ledger.py status`: агент сразу видит проекты, текущий шаг и то, что ждёт решения.
  after-write    → если инструмент записал файл в projects/<case>/state/ledger/, запускает validate для этого проекта.
                   Есть ошибки → код 2 и текст в stderr: среда возвращает его агенту как обратную связь.

Хук никогда не ломает работу: при любой собственной ошибке выходит с кодом 0.
"""
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PY = ROOT / ".venv" / "bin" / "python"
LEDGER = ROOT / "tools" / "ledger.py"


def run(args, timeout=60):
    py = str(PY) if PY.exists() else sys.executable
    return subprocess.run([py, str(LEDGER), *args], cwd=str(ROOT), capture_output=True, text=True, timeout=timeout)


def session_start() -> int:
    if not PY.exists():
        print("Стратег: окружение не создано — выполните `python3 -m venv .venv && .venv/bin/pip install -r requirements.txt` (AGENTS.md §1).")
        return 0
    r = run(["status"])
    print("Стратег — состояние проектов (ledger.py status):\n" + (r.stdout.strip() or "проектов нет"))
    return 0


def after_write(payload: str) -> int:
    projects = sorted(set(re.findall(r"projects/([A-Za-z0-9_.\-]+)/state/ledger/", payload)))
    if not projects or not PY.exists():
        return 0
    failed = []
    for slug in projects:
        proj = ROOT / "projects" / slug
        if not proj.is_dir():
            continue
        r = run(["validate", str(proj)])
        if r.returncode != 0:
            errs = [l for l in r.stdout.splitlines() if l.startswith("[ОШИБКА]")]
            failed.append(f"projects/{slug}: " + "; ".join(errs[:8]))
    if failed:
        print("Реестр не проходит validate после записи — исправьте до следующего шага:\n" + "\n".join(failed),
              file=sys.stderr)
        return 2
    return 0


def read_stdin(timeout: float = 2.0) -> str:
    """Читает JSON события, но не дольше timeout: незакрытый stdin не должен вешать сессию."""
    import os
    import select
    try:
        if sys.stdin.isatty():
            return ""
        fd = sys.stdin.fileno()
        chunks = []
        while True:
            ready, _, _ = select.select([fd], [], [], timeout)
            if not ready:
                break
            data = os.read(fd, 65536)
            if not data:
                break
            chunks.append(data)
        return b"".join(chunks).decode("utf-8", errors="replace")
    except Exception:
        return ""


def main() -> int:
    mode = sys.argv[1] if len(sys.argv) > 1 else ""
    try:
        if mode == "session-start":
            return session_start()  # stdin не читаем: он не нужен, а чтение незакрытого stdin вешает сессию
        if mode == "after-write":
            payload = read_stdin()
            try:
                payload = json.dumps(json.loads(payload), ensure_ascii=False)  # нормализуем экранирование путей
            except ValueError:
                pass
            return after_write(payload)
    except Exception as exc:  # хук не должен ронять сессию
        print(f"Стратег-хук: внутренняя ошибка {exc!r} — пропускаю", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""MCP-сервер «Стратега» (stdio, JSON-RPC 2.0, без внешних зависимостей).

Даёт средам с MCP (Claude Code, Cursor, Codex, OpenCode, Windsurf, Copilot…) те же команды, что и CLI в tools/:
состояние проектов, трассировку решения, impact, validate, маршрут, проверку утверждений, проверку готовности
и стоимость. CLI остаётся основным путём; MCP — для сред, где инструменты удобнее команд терминала.

    .venv/bin/python tools/mcp_server.py      # запускает среда по конфигу (.mcp.json, .cursor/mcp.json, .codex/config.toml)

Безопасность: проекты — только внутри projects/ этого репозитория; сервер ничего не удаляет; ключи не принимает
и не выводит. Проверка готовности может вызвать модель (через decide.py) — это отмечено в описании инструмента.
"""
from __future__ import annotations

import contextlib
import io
import json
import sys
from pathlib import Path
from typing import Callable, Dict, List

TOOLS_DIR = Path(__file__).resolve().parent
ROOT = TOOLS_DIR.parent
sys.path.insert(0, str(TOOLS_DIR))

PROTOCOL = "2025-06-18"
SERVER = {"name": "strategist", "version": "1.0.0"}


def project_path(slug: str) -> Path:
    p = (ROOT / "projects" / str(slug)).resolve()
    if (ROOT / "projects").resolve() not in p.parents:
        raise ValueError("проект должен лежать в projects/ этого репозитория")
    if not (p / "state").is_dir():
        raise ValueError(f"нет проекта projects/{slug}")
    return p


def run(fn: Callable[[List[str]], int], argv: List[str]) -> str:
    """Вызвать main() инструмента и вернуть его вывод как текст (stdout+stderr)."""
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            rc = fn(argv)
        except SystemExit as exc:  # argparse
            rc = exc.code if isinstance(exc.code, int) else 2
    text = (out.getvalue() + ("\n" + err.getvalue() if err.getvalue() else "")).strip()
    return f"{text}\n[код выхода {rc}]"


def _ledger(*a):
    import ledger as L
    return run(L.main, list(a))


def t_status(args):
    return _ledger("status")


def t_trace(args):
    return _ledger("trace", str(project_path(args["project"])), args["id"])


def t_impact(args):
    return _ledger("impact", str(project_path(args["project"])), args["change"])


def t_validate(args):
    return _ledger("validate", str(project_path(args["project"])))


def t_route(args):
    a = ["route", args["task"]]
    if args.get("env"):
        a += ["--env", args["env"]]
    if args.get("failed"):
        a += ["--failed", args["failed"]]
    if args.get("project"):
        a += ["--project", str(project_path(args["project"]))]
    return _ledger(*a)


def t_verify(args):
    import verify as V
    a = [str(project_path(args["project"]))]
    if args.get("files"):
        a += ["--files", *[str(f) for f in args["files"]]]
    return run(V.main, a)


def t_gate(args):
    import decide as DC
    p = project_path(args["project"])
    files = [str(p / "outputs" / f) for f in args["files"]]
    return run(DC.main, ["--project", str(p), "--gate", *files, "--step", str(args.get("step", "gate"))])


def t_cost(args):
    import cost as C
    a = []
    for k in ("cwd", "since", "until", "match"):
        if args.get(k):
            a += [f"--{k}", str(args[k])]
    if args.get("zcode"):
        a.append("--zcode")
    return run(C.main, a)


def t_models(args):
    import models as M
    return run(M.main, [args.get("command", "check")])


S = {"type": "string"}
TOOLS: Dict[str, dict] = {
    "strategist_status": {"fn": t_status, "description": "Проекты, текущий шаг каждого и что ждёт решения человека.",
                          "schema": {"type": "object", "properties": {}}},
    "strategist_trace": {"fn": t_trace, "description": "На чём держится объект реестра (например, DEC-001): цепочка до источников.",
                         "schema": {"type": "object", "properties": {"project": S, "id": S}, "required": ["project", "id"]}},
    "strategist_impact": {"fn": t_impact, "description": "Какие выводы затронуты изменением CHG-NNN (детерминированно, по графу).",
                          "schema": {"type": "object", "properties": {"project": S, "change": S}, "required": ["project", "change"]}},
    "strategist_validate": {"fn": t_validate, "description": "Проверка реестра проекта по схеме и ссылкам. Запускать после каждой записи.",
                            "schema": {"type": "object", "properties": {"project": S}, "required": ["project"]}},
    "strategist_route": {"fn": t_route, "description": "Уровень модели, effort и способ включения для задачи; --failed T3@high — эскалация.",
                         "schema": {"type": "object", "properties": {"task": S, "env": S, "failed": S, "project": S},
                                    "required": ["task"]}},
    "strategist_verify": {"fn": t_verify, "description": "Проверка утверждений в outputs кодом: числа без ссылок, битые и устаревшие ссылки.",
                          "schema": {"type": "object", "properties": {"project": S, "files": {"type": "array", "items": S}},
                                     "required": ["project"]}},
    "strategist_gate": {"fn": t_gate, "description": "Проверка готовности артефактов (outputs/NN_*.md) перед checkpoint. "
                                                     "Заголовки и ID — кодом; остальное — модель решений (может стоить токенов).",
                        "schema": {"type": "object", "properties": {"project": S, "files": {"type": "array", "items": S},
                                                                    "step": S}, "required": ["project", "files"]}},
    "strategist_cost": {"fn": t_cost, "description": "Стоимость прогона по журналам Codex/Claude Code/ZCode и официальным ценам.",
                        "schema": {"type": "object", "properties": {"cwd": S, "since": S, "until": S, "match": S,
                                                                    "zcode": {"type": "boolean"}}}},
    "strategist_models": {"fn": t_models, "description": "Модели: check (новые версии и устаревшие гайды), resolve, specialized.",
                          "schema": {"type": "object", "properties": {"command": {"type": "string",
                                                                                  "enum": ["check", "resolve", "specialized"]}}}},
}


def handle(msg: dict):
    method, mid = msg.get("method"), msg.get("id")
    if mid is None:  # уведомление (notifications/initialized и т. п.) — ответа не требует
        return None
    if method == "initialize":
        ver = (msg.get("params") or {}).get("protocolVersion") or PROTOCOL
        return {"jsonrpc": "2.0", "id": mid, "result": {
            "protocolVersion": ver, "capabilities": {"tools": {"listChanged": False}}, "serverInfo": SERVER,
            "instructions": "Инструменты агента «Стратег». Правила работы — AGENTS.md в корне репозитория."}}
    if method == "ping":
        return {"jsonrpc": "2.0", "id": mid, "result": {}}
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": mid, "result": {"tools": [
            {"name": n, "description": t["description"], "inputSchema": t["schema"]} for n, t in TOOLS.items()]}}
    if method == "tools/call":
        p = msg.get("params") or {}
        tool = TOOLS.get(p.get("name"))
        if not tool:
            return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32602, "message": f"нет инструмента {p.get('name')}"}}
        try:
            text = tool["fn"](p.get("arguments") or {})
            failed = not text.rstrip().endswith("[код выхода 0]")
        except (ValueError, KeyError) as exc:
            text, failed = f"Ошибка аргументов: {exc}", True
        return {"jsonrpc": "2.0", "id": mid, "result": {"content": [{"type": "text", "text": text}], "isError": failed}}
    return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": f"метод не поддерживается: {method}"}}


def main() -> int:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            resp = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "неверный JSON"}}
        else:
            resp = handle(msg)
        if resp is not None:
            sys.stdout.write(json.dumps(resp, ensure_ascii=False) + "\n")
            sys.stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())

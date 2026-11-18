"""MCP-сервер: протокол stdio JSON-RPC, список инструментов, вызовы, защита путей."""
import json
import subprocess
import sys
from pathlib import Path

import pytest

import mcp_server

ROOT = Path(__file__).resolve().parent.parent


def session(*msgs):
    proc = subprocess.run([sys.executable, str(ROOT / "tools/mcp_server.py")], cwd=str(ROOT), timeout=60,
                          input="\n".join(json.dumps(m, ensure_ascii=False) for m in msgs) + "\n",
                          capture_output=True, text=True, encoding="utf-8")
    return {d["id"]: d for d in (json.loads(l) for l in proc.stdout.splitlines() if l.strip())}


def call(i, name, **args):
    return {"jsonrpc": "2.0", "id": i, "method": "tools/call", "params": {"name": name, "arguments": args}}


def test_handshake_list_and_calls():
    r = session({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                 "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "0"}}},
                {"jsonrpc": "2.0", "method": "notifications/initialized"},
                {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
                call(3, "strategist_validate", project="example"),
                call(4, "strategist_trace", project="ryadom", id="DEC-001"),
                call(5, "strategist_validate", project="../../tools"),
                call(6, "strategist_route", task="alternatives", env="codex", failed="T3@xhigh"),
                {"jsonrpc": "2.0", "id": 7, "method": "resources/list"},
                {"jsonrpc": "2.0", "id": 8, "method": "ping"})
    assert r[1]["result"]["serverInfo"]["name"] == "strategist" and "tools" in r[1]["result"]["capabilities"]
    names = {t["name"] for t in r[2]["result"]["tools"]}
    assert {"strategist_status", "strategist_trace", "strategist_impact", "strategist_validate", "strategist_route",
            "strategist_verify", "strategist_gate", "strategist_cost", "strategist_models"} <= names
    assert all("inputSchema" in t for t in r[2]["result"]["tools"])
    assert r[3]["result"]["isError"] is False and "[код выхода 0]" in r[3]["result"]["content"][0]["text"]
    assert "DEC-001" in r[4]["result"]["content"][0]["text"]
    assert r[5]["result"]["isError"] is True and "projects/" in r[5]["result"]["content"][0]["text"]
    assert "T4" in r[6]["result"]["content"][0]["text"]
    assert r[7]["error"]["code"] == -32601 and r[8]["result"] == {}
    assert len(r) == 8  # на уведомление ответа нет


@pytest.mark.parametrize(("slug", "exit_code"), [("valid", 0), ("absent", 2)])
def test_validate_matches_cli(tmp_path, monkeypatch, slug, exit_code):
    monkeypatch.setattr(mcp_server, "ROOT", tmp_path)
    (tmp_path / "projects/valid/state/ledger").mkdir(parents=True)
    project = (tmp_path / "projects" / slug).resolve()
    cli = subprocess.run([sys.executable, str(ROOT / "tools/ledger.py"), "validate", str(project)],
                         cwd=tmp_path, capture_output=True, encoding="utf-8", timeout=60)
    assert cli.returncode == exit_code
    cli_text = cli.stdout if exit_code == 0 else cli.stderr
    assert mcp_server.handle(call(1, "strategist_validate", project=slug)) == {
        "jsonrpc": "2.0", "id": 1, "result": {
            "content": [{"type": "text", "text": f"{cli_text.strip()}\n[код выхода {exit_code}]"}],
            "isError": exit_code != 0,
        },
    }


@pytest.mark.parametrize(("slug", "message"), [
    ("uninitialized", "нет проекта projects/uninitialized"),
    ("../outside", "проект должен лежать в projects/ этого репозитория"),
    ("escaped", "проект должен лежать в projects/ этого репозитория"),
    ("escaped/absent", "проект должен лежать в projects/ этого репозитория"),
])
def test_validate_rejects_invalid_project_path(tmp_path, monkeypatch, slug, message):
    monkeypatch.setattr(mcp_server, "ROOT", tmp_path)
    projects = tmp_path / "projects"
    (projects / "uninitialized").mkdir(parents=True)
    outside = tmp_path / "outside"
    (outside / "state/ledger").mkdir(parents=True)
    if slug.startswith("escaped"):
        try:
            (projects / "escaped").symlink_to(outside, target_is_directory=True)
        except OSError as exc:
            if getattr(exc, "winerror", None) == 1314:
                pytest.skip("Windows не разрешает создание симлинков")
            raise
    assert mcp_server.handle(call(1, "strategist_validate", project=slug)) == {
        "jsonrpc": "2.0", "id": 1, "result": {
            "content": [{"type": "text", "text": f"Ошибка аргументов: {message}"}], "isError": True,
        },
    }


def test_configs_point_to_server():
    for f in (".mcp.json", ".cursor/mcp.json"):
        cfg = json.loads((ROOT / f).read_text(encoding="utf-8"))["mcpServers"]["strategist"]
        assert cfg["args"] == ["tools/mcp_server.py"]

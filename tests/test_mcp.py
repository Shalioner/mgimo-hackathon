"""MCP-сервер: протокол stdio JSON-RPC, список инструментов, вызовы, защита путей."""
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def session(*msgs):
    proc = subprocess.run([sys.executable, str(ROOT / "tools/mcp_server.py")], cwd=str(ROOT), timeout=60,
                          input="\n".join(json.dumps(m, ensure_ascii=False) for m in msgs) + "\n",
                          capture_output=True, text=True)
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


def test_configs_point_to_server():
    for f in (".mcp.json", ".cursor/mcp.json"):
        cfg = json.loads((ROOT / f).read_text(encoding="utf-8"))["mcpServers"]["strategist"]
        assert cfg["args"] == ["tools/mcp_server.py"]

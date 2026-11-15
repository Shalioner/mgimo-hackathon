"""Offline transport contracts; fake CLI outputs are not live model evidence."""
import copy
import datetime as dt
import hashlib
import json
from pathlib import Path
import subprocess

import pytest

import delegate as D
import ledger as L
import opencode_free as O
import routing_policy as P


def model(ident="big-pickle"):
    return {"id": ident, "type": "chat", "release_date": "2025-10-17", "name": ident,
            "modalities": {"input": ["text"], "output": ["text"]},
            "capabilities": {"tools": {"supported": True}, "reasoning": {"supported": True, "effort": ["low", "max"]}},
            "limit": {"context": 32000, "output": 4000},
            "cost": {"text": {"input": {"price": 0, "cache": {"read": 0, "write": {"5m": 0, "1h": 0}}}, "output": {"price": 0}}},
            "api": {"chat-completions": {"base_url": O.API, "sdk": {"opencodeai": O.PACKAGE}}}}


@pytest.fixture
def configured(tmp_path, monkeypatch):
    binary = tmp_path / "fake-opencode"
    binary.write_text("fake; never executed")
    binary.chmod(0o700)
    provider = {"id": "opencode", "name": "OpenCode", "env": ["OPENCODE_API_KEY"],
                "models": {"big-pickle": model(), "flash-free": model("flash-free")}}
    data = {"schema": 1, "source": O.SOURCE, "fetched_at": dt.datetime.now(dt.timezone.utc).isoformat(),
            "provider": provider, "sha256": O.digest(provider), "binary": str(binary),
            "binary_sha256": hashlib.sha256(binary.read_bytes()).hexdigest(), "version": O.VERSION}
    path = tmp_path / "catalog.json"
    path.write_text(json.dumps(data))
    cfg = {"tasks": {"classify": {"tier": "T1"}, "problem-framing": {"tier": "T2"}},
           "execution_policy": {"opencode_free": {"enabled": True, "catalog": str(path),
           "candidates": {"T1": ["opencode/flash-free", "opencode/big-pickle"], "T2": ["opencode/big-pickle"]}}}}
    monkeypatch.setattr(O, "system_boundary", lambda: None)
    return cfg, path, data


def plan(cfg, **overrides):
    return P.plan(cfg, **{"host": "opencode", "provider": "opencode", "model": "opencode/big-pickle", **overrides})


@pytest.mark.parametrize("mutate", [
    lambda m: m["cost"]["text"]["output"].update(price=1),
    lambda m: m["cost"]["text"]["input"]["cache"].update(read=0.001),
    lambda m: m["cost"]["text"]["input"]["cache"]["write"].update({"1h": 1}),
    lambda m: m["cost"]["text"]["input"].update(overrides=[{"when": {"context": {"gt": 100000}}, "price": 1}]),
    lambda m: m["cost"].update(multipliers=[{"when": {"service_tier": "priority"}, "factor": 2}]),
    lambda m: m.update(experimental={"modes": {"priority": {"cost": {"input": 1}}}}),
    lambda m: m.update(variants=[{"id": "high", "price": 1}]),
    lambda m: m["cost"].update(image={"price": 1}),
    lambda m: m["cost"]["text"]["output"].pop("price"),
    lambda m: m["cost"]["text"]["input"].update(price=False),
    lambda m: m["cost"]["text"]["input"].update(price=float("nan")),
    lambda m: m["cost"]["text"]["input"].update(unknown_surcharge=1),
    lambda m: m["api"]["chat-completions"].update(base_url="https://other.invalid"),
    lambda m: m["limit"].update(context=None),
    lambda m: m.update(capabilities=[]),
    lambda m: m.update(modalities=[]),
    lambda m: m["api"].update({"chat-completions": []}),
])
def test_every_price_and_unknown_shape_fails_closed(mutate):
    item = model("looks-free")
    mutate(item)
    with pytest.raises(ValueError):
        O.exact_free(item)


def test_no_suffix_heuristic_and_zero_context_tiers():
    item = model()
    item["cost"]["text"]["input"]["overrides"] = [{"when": {"context": {"gte": 200000}}, "price": 0}]
    assert O.exact_free(item)["id"] == "big-pickle"


def test_select_per_task_and_explicit_gate(configured):
    cfg, _, _ = configured
    assert O.select(cfg, "classify", "T1") == "opencode/flash-free"
    assert O.select(cfg, "problem-framing", "T2") == "opencode/big-pickle"
    with pytest.raises(ValueError):
        O.select(cfg, "unregistered", "T1")
    with pytest.raises(ValueError):
        O.select(cfg, "classify", "T1", "opencode/paid")
    assert plan(cfg, model="opencode/paid")["status"] == "denied"


@pytest.mark.parametrize("mutation", ["stale", "future", "malformed", "digest", "binary", "version", "source"])
def test_snapshot_validation(configured, mutation):
    cfg, path, data = configured
    if mutation == "stale":
        data["fetched_at"] = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=2)).isoformat()
    elif mutation == "future":
        data["fetched_at"] = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=1)).isoformat()
    elif mutation == "malformed":
        path.write_text("[]")
    elif mutation == "binary":
        Path(data["binary"]).write_text("changed")
    else:
        data[{"digest": "sha256", "version": "version", "source": "source"}[mutation]] = "unknown"
    if mutation != "malformed":
        path.write_text(json.dumps(data))
    assert plan(cfg)["status"] == "denied"


def test_prices_rechecked_each_task_no_paid_fallback(configured):
    cfg, path, data = configured
    for item in data["provider"]["models"].values():
        item["cost"]["text"]["output"]["price"] = 2
    data["sha256"] = O.digest(data["provider"])
    path.write_text(json.dumps(data))
    assert plan(cfg)["status"] == "denied"
    with pytest.raises(ValueError, match="No confirmed free"):
        O.select(cfg, "classify", "T1")


def test_variants_and_existing_gates(configured):
    cfg, _, _ = configured
    assert plan(cfg, effort="high")["status"] == "denied"
    assert plan(cfg, effort="low")["status"] == "executable"
    assert plan(cfg, effort="max")["status"] == "denied"
    assert plan(cfg, effort="max", human_approved=True)["status"] == "executable"
    assert plan(cfg, effort="ultra")["status"] == "denied"
    assert plan(cfg, tier="T4")["status"] == "denied"
    assert plan(cfg, input_bytes=100000)["status"] == "denied"


def test_preflight_no_network_process_or_writes(configured, monkeypatch):
    cfg, path, _ = configured
    before = {p: p.read_bytes() for p in path.parent.iterdir() if p.is_file()}
    def forbidden(*a, **kw):
        pytest.fail("preflight attempted execution")
    monkeypatch.setattr(subprocess, "run", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    monkeypatch.setattr(P, "http_open", forbidden)
    assert plan(cfg)["status"] == "executable"
    assert {p: p.read_bytes() for p in path.parent.iterdir() if p.is_file()} == before


def fake_probe(monkeypatch, auth="[]"):
    calls = []
    def run(command, **kw):
        calls.append(command)
        env = kw["env"]
        assert "OPENCODE_API_KEY" not in env and "HTTPS_PROXY" not in env
        assert "NODE_OPTIONS" not in env and "ORCA_TERM" not in env
        assert env["HOME"] != "/real-home"
        assert env["PATH"] == "/usr/bin:/bin"
        if command[-1] == "--version":
            return subprocess.CompletedProcess(command, 0, O.VERSION + "\n", "")
        if "auth" in command:
            assert "--standalone" in command
            return subprocess.CompletedProcess(command, 0, auth, "")
        assert "models" in command and "--standalone" in command
        return subprocess.CompletedProcess(command, 0, "opencode/big-pickle\n", "")
    monkeypatch.setattr(subprocess, "run", run)
    for key, value in {"HOME": "/real-home", "OPENCODE_API_KEY": "never inherit", "HTTPS_PROXY": "bad",
                       "NODE_OPTIONS": "bad", "ORCA_TERM": "bad"}.items():
        monkeypatch.setenv(key, value)
    return calls


@pytest.mark.parametrize("auth", ['[{"id":"opencode","connections":[{}]}]', '{}', 'invalid'])
def test_auth_leakage_blocks_before_launch(configured, monkeypatch, auth):
    cfg, _, _ = configured
    calls = fake_probe(monkeypatch, auth)
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **kw: pytest.fail("inference after failed auth"))
    receipt = plan(cfg)
    assert O.call(cfg, "opencode/big-pickle", "synthetic", None, 1, receipt)[0] == 3
    assert not receipt["started"] and len(calls) == 2


@pytest.mark.parametrize("failure", ["quota", "timeout", "partial"])
def test_one_attempt_no_fallback(configured, monkeypatch, failure):
    cfg, _, _ = configured
    fake_probe(monkeypatch)
    launches = []
    class Process:
        returncode = 1
        def __init__(self, command, **kw):
            launches.append(command)
            assert command[1:3] == ["--standalone", "run"]
            assert command[command.index("--model") + 1] == "opencode/big-pickle"
            catalog = json.loads(Path(kw["env"]["OPENCODE_MODELS_PATH"]).read_text())
            assert set(catalog) == {"opencode"}
            assert set(catalog["opencode"]["models"]) == {"big-pickle"}
        def communicate(self, timeout=None):
            if failure == "timeout" and timeout:
                raise subprocess.TimeoutExpired("fake", timeout)
            return ('{"type":"error","error":"quota"}\n' if failure == "quota" else '{"type":"text","part":{"text":"partial"}}', "")
        def kill(self):
            self.returncode = -9
    monkeypatch.setattr(subprocess, "Popen", Process)
    receipt = plan(cfg)
    code, stdout, _, _ = O.call(cfg, "opencode/big-pickle", "synthetic", None, 1, receipt)
    assert code != 0 and len(launches) == 1 and receipt["started"]
    assert receipt["process_exit_code"] is not None
    assert receipt["observed"]["model"] is None


def test_ndjson_result_shapes():
    events = [{"type": "text", "part": {"text": "OK"}}, {"type": "step_finish", "part":
              {"reason": "stop", "cost": 0, "tokens": {"input": 10, "output": 2, "cache": {"read": 0, "write": 0}}}}]
    def parse(value):
        return D.parse_result("opencode", "\n".join(json.dumps(e) for e in value))
    assert parse(events)["valid"] and parse(events)["text"] == "OK"
    assert not parse(events[:1])["valid"]  # partial success is not success
    for bad in [None, {}, [], "bad"]:
        assert not parse([{"type": "text", "part": bad}])["valid"]
    for cost in [1, False, None, "0"]:
        altered = copy.deepcopy(events)
        altered[-1]["part"]["cost"] = cost
        assert not parse(altered)["valid"]
    assert parse([{"type": "error", "error": {"message": "quota"}}])["error"]


def test_delegate_receipt_and_default_effort(configured, monkeypatch, tmp_path):
    cfg, _, _ = configured
    monkeypatch.setattr(L, "load_routing", lambda: cfg)
    project = tmp_path / "project"
    (project / "state").mkdir(parents=True)
    (project / "state" / "run_log.md").write_text("")
    prompt = tmp_path / "prompt.md"
    prompt.write_text("synthetic")
    def fake_call(config, model_id, text, effort, timeout, receipt, human_approved):
        assert model_id == "opencode/big-pickle" and effort is None
        receipt.update(started=True, process_exit_code=0, wrapper_invocations=1)
        receipt["transmitted"] = {"model": model_id, "effort": effort}
        return 0, '\n'.join(json.dumps(e) for e in [
            {"type": "text", "part": {"text": "OK"}},
            {"type": "step_finish", "part": {"reason": "stop", "cost": 0, "tokens": {"input": 2, "output": 1}}}
        ]), "", ""
    monkeypatch.setattr(O, "call", fake_call)
    assert D.main(["--project", str(project), "--prompt-file", str(prompt), "--task", "problem-framing",
                   "--host", "opencode"]) == 0
    receipts = list((project / "state" / "delegated").glob("*.receipt.json"))
    assert len(receipts) == 1
    receipt = json.loads(receipts[0].read_text())
    assert receipt["result"] == "succeeded" and receipt["started"]
    assert receipt["process_exit_code"] == 0
    assert receipt["requested"]["model"] == receipt["transmitted"]["model"] == "opencode/big-pickle"
    assert receipt["observed"] == {"provider": None, "model": None, "effort": None}
    assert receipt["integrity"] == "unconfirmed"

"""Offline request routing: gate + real parser, fake transport, no live inference."""
import copy
import builtins
import hashlib
import importlib
import json
import sys

import pytest

import delegate as D
import route_request as R
from test_codex_subscription import subscription
from test_opencode_free import configured
from test_claude_subscription import claude_profile
from test_routing_policy import api_policy


@pytest.mark.parametrize("execute", [False, True])
@pytest.mark.parametrize("platform", ["missing_pwd", "win32"])
def test_opencode_unsupported_platform_returns_manual(monkeypatch, execute, platform):
    """Import/platform simulation on the current OS, not native Windows testing."""
    original_import = builtins.__import__
    monkeypatch.delitem(sys.modules, "opencode_free", raising=False)
    if platform == "win32":
        monkeypatch.setattr(sys, "platform", "win32")
    def no_pwd(name, *args, **kwargs):
        if name == "pwd":
            raise ModuleNotFoundError("No module named 'pwd'", name="pwd")
        return original_import(name, *args, **kwargs)
    monkeypatch.setattr(builtins, "__import__", no_pwd)
    monkeypatch.setattr(D, "execute_route", lambda *a, **kw: pytest.fail("No inference allowed"))
    monkeypatch.setattr(D.subprocess, "run", lambda *a, **kw: pytest.fail("No subprocess allowed"))
    monkeypatch.setattr(R.P, "http_open", lambda *a, **kw: pytest.fail("No HTTP allowed"))
    cfg = {"automatic_routing": {"bootstrap_host": "opencode"}, "tasks": {"classify": {"tier": "T1"}}}
    result = R.run_request("test request", "", ["opencode"], execute=execute, load_config=lambda: cfg)
    result = json.loads(json.dumps(result))
    assert result["status"] == "manual"
    assert result["classification"]["status"] == "unavailable"
    receipt = result["classification"]["route"]
    assert receipt["status"] == "manual" and "POSIX" in receipt["reason"]
    assert receipt["started"] is False and receipt["wrapper_invocations"] == 0
    assert result["result"]["status"] == "not_started" and result["start"] is None
    # The adapter's own boundary must refuse too, even when called directly.
    adapter = importlib.import_module("opencode_free")
    with pytest.raises(ValueError, match="POSIX"):
        adapter.system_boundary()


def test_opencode_platform_check_does_not_hide_unrelated_import(monkeypatch):
    import opencode_free as O
    original_import = builtins.__import__
    def broken_dependency(name, *args, **kwargs):
        if name == "pwd":
            raise ModuleNotFoundError("Unrelated dependency failure", name="unrelated")
        return original_import(name, *args, **kwargs)
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(builtins, "__import__", broken_dependency)
    with pytest.raises(ModuleNotFoundError, match="Unrelated dependency failure"):
        O.platform_error()


def labels(tier="T2", effort="normal", capability="text", sure=True):
    return {"answers": {key: {"choice": choice, "sure": sure, "quote": "test request"}
                        for key, choice in {"tier": tier, "effort": effort, "capability": capability}.items()}}


@pytest.fixture
def routed(subscription, monkeypatch):
    cfg, _, _, _, _ = subscription
    cfg["families"]["openai"].update(T2="medium-model", T3="strong-model")
    cfg["tiers"].update(T2={"effort": ["medium", "high"]}, T3={"effort": ["high", "xhigh"]})
    profile = cfg["execution_policy"]["codex_subscription"]
    profile["models"] += ["medium-model", "strong-model"]
    profile["efforts"] += ["medium", "high", "xhigh"]
    calls = []
    response = {"classifier": labels(), "result": "answer", "metadata": {}, "code": 0}

    def call(cfg, model, prompt, effort, timeout, receipt, human_approved=False):
        calls.append({"model": model, "prompt": prompt, "effort": effort})
        receipt.update(started=True, wrapper_invocations=1, process_exit_code=0,
                       transmitted={"model": model, "effort": effort})
        is_classifier = len(calls) == 1
        text = json.dumps(response["classifier"]) if is_classifier else response["result"]
        event = {"type": "turn.completed", **({} if is_classifier else response["metadata"])}
        return 0 if is_classifier else response["code"], json.dumps(event), "", text

    monkeypatch.setattr(D, "call_codex", call)
    return cfg, calls, response


@pytest.mark.parametrize("tier,effort,model,expected", [
    ("T1", "normal", "m", "low"), ("T2", "normal", "medium-model", "medium"),
    ("T2", "deep", "medium-model", "high"), ("T3", "deep", "strong-model", "xhigh")])
def test_context_complexity_effort_and_no_recursive_classification(routed, tier, effort, model, expected):
    cfg, calls, response = routed
    response["classifier"] = labels(tier, effort)
    result = R.run_request("test request", "conflicting sources, costly decision", ["codex"], execute=True, load_config=lambda: cfg)
    assert result["status"] == "succeeded"
    assert result["request"] == "test request"
    assert "conflicting sources" in calls[0]["prompt"]
    assert len(calls) == 2
    assert calls[0]["model"] == "m" and calls[0]["effort"] == "low"
    assert (calls[1]["model"], calls[1]["effort"]) == (model, expected)
    assert result["plan"]["selected"]["started"] is False
    assert result["start"]["started"] is True


@pytest.mark.parametrize("host", ["cursor", "zcode", "gemini-cli", "antigravity", "antigravity-ide", "deepseek-harness", "kimi", "qwen"])
def test_unimplemented_hosts_remain_manual(routed, host):
    cfg, calls, _ = routed
    result = R.run_request("test request", "", [host], execute=True, load_config=lambda: cfg)
    assert result["status"] == "manual" and len(calls) == 1
    assert result["plan"]["candidates"][0]["status"] != "executable"


def test_no_side_effect_preflight_or_unavailable_classifier(routed):
    cfg, calls, _ = routed
    assert R.run_request("test request", "", ["codex"], load_config=lambda: cfg)["status"] == "awaiting_classification"
    assert not calls
    cfg["execution_policy"]["codex_subscription"]["enabled"] = False
    result = R.run_request("test request", "", ["codex"], execute=True, load_config=lambda: cfg)
    assert not calls and result["classification"]["status"] == "unavailable"


@pytest.mark.parametrize("data", [None, [], {"answers": {}}, labels(sure=False), labels("T4"), labels(effort="ultra"),
                                      {"answers": {k: {"type": "choice", "choice": c, "confidence": 1} for k, c in {"tier": "T3", "effort": "deep", "capability": "text"}.items()}}])
def test_untrusted_classification_never_defaults(routed, data):
    cfg, calls, response = routed
    response["classifier"] = data
    result = R.run_request("test request", "", ["codex"], execute=True, load_config=lambda: cfg)
    assert len(calls) == 1 and result["classification"]["status"] == "needs_review"
    assert result["plan"] is None


def test_forbidden_explicit_provider_does_not_fallback(routed, monkeypatch):
    cfg, calls, _ = routed
    cfg["automatic_routing"] = {"decision_provider": "forbidden"}
    cfg["decision"] = {"providers": [{"name": "forbidden", "base_url": "https://example.invalid", "model": "jev"}]}
    monkeypatch.setattr(R.C, "call", lambda *a, **kw: pytest.fail("forbidden HTTP"))
    result = R.run_request("test request", "", ["codex"], execute=True, load_config=lambda: cfg)
    assert not calls and result["status"] == "manual"


def test_missing_capability_does_not_execute(routed):
    cfg, calls, response = routed
    response["classifier"] = labels(capability="tools")
    result = R.run_request("test request", "", ["codex"], execute=True, load_config=lambda: cfg)
    assert len(calls) == 1 and result["plan"]["status"] == "manual"


@pytest.mark.parametrize("metadata,text,code,outcome", [({"model": "wrong"}, "answer", 0, "quarantined"),
                                                       ({}, "", 0, "invalid_result"), ({}, "answer", 429, "failed")])
def test_bad_result_or_quota_no_second_host_fallback(routed, metadata, text, code, outcome):
    cfg, calls, response = routed
    response.update(metadata=metadata, result=text, code=code)
    result = R.run_request("test request", "", ["codex", "claude-code"], execute=True, load_config=lambda: cfg)
    assert len(calls) == 2 and result["result"] == {"status": outcome, "text": None}


def test_recheck_blocks_changed_policy(routed):
    cfg, calls, _ = routed
    reads = []
    def load():
        reads.append(1)
        current = copy.deepcopy(cfg)
        if len(reads) == 4:
            current["execution_policy"]["codex_subscription"]["enabled"] = False
        return current
    result = R.run_request("test request", "", ["codex"], execute=True, load_config=load)
    assert len(calls) == 1 and result["start"]["status"] == "not_started"


def test_candidate_hosts_filtered_before_execution(routed):
    cfg, calls, _ = routed
    result = R.run_request("test request", "", ["cursor", "codex"], execute=True, load_config=lambda: cfg)
    assert len(calls) == 2 and result["plan"]["selected"]["requested"]["host"] == "codex"
    assert result["plan"]["candidates"][0]["status"] == "manual"


def test_stale_free_catalog_blocks_candidate(routed, configured):
    cfg, calls, _ = routed
    free_cfg, path, data = configured
    cfg["execution_policy"].update(free_cfg["execution_policy"])
    cfg["tasks"].update(free_cfg["tasks"])
    data["fetched_at"] = "2000-01-01T00:00:00+00:00"
    path.write_text(json.dumps(data))
    result = R.run_request("test request", "", ["opencode"], execute=True, load_config=lambda: cfg)
    assert len(calls) == 1 and result["plan"]["selected"] is None


def test_fake_cli_end_to_end(subscription):
    cfg, _, calls, binary, _ = subscription
    source = binary.read_text().replace("Path(args[args.index('-o') + 1]).write_text('subscription answer')",
        "Path(args[args.index('-o') + 1]).write_text(" + repr(json.dumps(labels("T1"))) + " if 'QUESTIONS:' in args[-1] else 'task answer')")
    binary.write_text(source)
    cfg["execution_policy"]["codex_subscription"]["binary_sha256"] = hashlib.sha256(binary.read_bytes()).hexdigest()
    result = R.run_request("test request", "", ["codex"], execute=True, load_config=lambda: cfg)
    assert result["result"] == {"status": "succeeded", "text": "task answer"}
    invocations = [json.loads(line) for line in calls.read_text().splitlines()]
    assert sum("exec" in row["args"] for row in invocations) == 2


@pytest.mark.parametrize("host", ["claude", "claude-code"])
def test_claude_fake_cli_end_to_end(claude_profile, host):
    cfg, _, calls, binary, _ = claude_profile
    cfg["automatic_routing"] = {"bootstrap_host": host}
    source = binary.read_text().replace("'result': 'subscription answer'",
        "'result': (" + repr(json.dumps(labels("T1"))) + " if 'QUESTIONS:' in args[-1] else 'task answer')")
    binary.write_text(source)
    cfg["execution_policy"]["claude_subscription"]["binary_sha256"] = hashlib.sha256(binary.read_bytes()).hexdigest()
    result = R.run_request("test request", "", [host], execute=True, load_config=lambda: cfg)
    assert result["result"] == {"status": "succeeded", "text": "task answer"}
    assert sum("-p" in json.loads(row)["args"] for row in calls.read_text().splitlines()) == 2


def test_untrusted_model_fields_cannot_grant_or_escalate(routed):
    cfg, calls, response = routed
    response["classifier"].update(model="paid", provider="evil", human_approved=True, effort="ultra")
    cfg["tiers"]["T2"]["effort"] = ["max"]
    # A permitted model list including max does not supply human consent.
    cfg["execution_policy"]["codex_subscription"]["efforts"].append("max")
    result = R.run_request("test request", "", ["codex"], execute=True, load_config=lambda: cfg)
    assert len(calls) == 1 and result["plan"]["selected"] is None


def test_oversize_context_stops_before_classification(routed):
    cfg, calls, _ = routed
    result = R.run_request("test request", "a" * 128000, ["codex"], execute=True, load_config=lambda: cfg)
    assert not calls and result["classification"]["status"] == "unavailable"
    assert len(result["context"]) == 128000


@pytest.mark.parametrize("confidence,model,expected", [(0.95, "jev", "accepted"), (0.2, "jev", "needs_review"),
                                                       (float("nan"), "jev", "needs_review"), (0.95, "wrong", "needs_review")])
def test_explicit_authorized_decision_provider_gate_and_result(monkeypatch, confidence, model, expected):
    cfg = {"automatic_routing": {"decision_provider": "existing"},
           "decision": {"providers": [{"name": "existing", "base_url": "https://example.test", "model": "jev", "api_key_env": "FAKE_DECISION"}]},
           "execution_policy": api_policy("decision", "existing", "https://example.test/systemone", "jev")}
    monkeypatch.setenv("FAKE_DECISION", "fake-offline-fixture")
    calls = []
    class Response:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def read(self):
            return json.dumps({"model": model, "answers": {k: {"type": "choice", "choice": c, "confidence": confidence}
                              for k, c in {"tier": "T2", "effort": "normal", "capability": "text"}.items()}}).encode()
    def open_fake(request, timeout):
        calls.append(json.loads(request.data))
        return Response()
    monkeypatch.setattr(R.P, "http_open", open_fake)
    result = R.run_request("test request", "", ["cursor"], execute=True, load_config=lambda: cfg)
    assert len(calls) == 1 and result["classification"]["status"] == expected
    assert result["start"] is None


def test_decision_permission_revoked_before_call(monkeypatch):
    cfg = {"automatic_routing": {"decision_provider": "existing"},
           "decision": {"providers": [{"name": "existing", "base_url": "https://example.test", "model": "jev", "api_key_env": "FAKE_DECISION"}]},
           "execution_policy": api_policy("decision", "existing", "https://example.test/systemone", "jev")}
    monkeypatch.setenv("FAKE_DECISION", "fake-offline-fixture")
    monkeypatch.setattr(R.P, "http_open", lambda *a: pytest.fail("Revoked permission must block HTTP"))
    reads = []
    def load():
        current = copy.deepcopy(cfg)
        if reads:
            current["execution_policy"]["api"]["enabled"] = False
        reads.append(1)
        return current
    result = R.run_request("test request", "", ["codex"], execute=True, load_config=load)
    receipt = result["classification"]["route"]
    assert receipt["result"] == "not_started" and receipt["inference_calls"] == 0
    assert result["start"] is None

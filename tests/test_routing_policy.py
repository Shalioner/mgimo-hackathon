"""Offline billing/transport regression tests. No real account/CLI is used."""
import copy
import json
import os
from pathlib import Path

import pytest

import delegate as D
import decide as DC
import ledger as L
import routing_policy as P


def api_policy(host, provider, endpoint, model, efforts=None):
    return {"mode": "api_opt_in", "max_input_bytes": 128000, "api": {"enabled": True, "grants": [{
        "host": host, "provider": provider, "endpoint": endpoint, "models": [model],
        "efforts": efforts or [None, "low", "medium", "high"], "budget_usd": 1,
        "external_cap": {"confirmed_by_operator": True, "amount_usd": 1, "evidence_ref": "fake-test-cap"},
        "context": {"model_limit": 128000, "plan_limit": 128000, "user_limit": 100000,
                    "output_reserve": 1024, "system_reserve": 2048}}]}}


def request(cfg, **over):
    args = dict(host="selfhosted", provider="example", model="m", endpoint="https://example.test/v1/chat/completions",
                input_bytes=10, key_present=True)
    args.update(over)
    return P.plan(cfg, **args)


@pytest.mark.parametrize("host", sorted(P.HOSTS))
def test_subscription_hosts_do_not_infer_entitlement(host):
    r = request({}, host=host)
    assert r["status"] == ("denied" if host == "opencode" else "manual")
    assert r["observed"]["model"] is None


def test_exact_api_grant_and_operator_attestation():
    cfg = {"execution_policy": api_policy("selfhosted", "example", "https://example.test/v1/chat/completions", "m")}
    assert request(cfg)["status"] == "executable"
    for override in ({"model": "newest"}, {"provider": "other"}, {"endpoint": "https://other.test"},
                     {"key_present": False}, {"host": "unknown"}, {"effort": "ultra"}, {"effort": "max"},
                     {"tier": "T4"}, {"input_bytes": 200000}):
        assert request(cfg, **override)["status"] != "executable"
    for path, val in [("budget_usd", float("nan")), ("external_cap", {}), ("context", {})]:
        changed = copy.deepcopy(cfg)
        changed["execution_policy"]["api"]["grants"][0][path] = val
        assert request(changed)["status"] != "executable"


@pytest.mark.parametrize("url", ["https://user:secret@example.test/v1", "https://x/v1?key=secret", "http://remote/v1", "https://x/v1#foo"])
def test_unsafe_endpoints_denied(url):
    assert not P.endpoint_ok(url)


def test_environment_has_no_api_config_or_account_paths():
    source = {k: "secret" for k in ["OPENAI_API_KEY", "ANTHROPIC_API_KEY", "KIMI_API_KEY", "GEMINI_API_KEY",
              "AWS_PROFILE", "GOOGLE_APPLICATION_CREDENTIALS", "CODEX_HOME", "CLAUDE_CONFIG_DIR", "HOME",
              "HTTP_PROXY", "HTTPS_PROXY", "NODE_OPTIONS", "PYTHONPATH", "ORCA_AGENT_HOOK_TOKEN"]}
    source["PATH"] = "/usr/bin"
    assert D.child_env(source) == {"PATH": "/usr/bin"}
    assert source["OPENAI_API_KEY"] == "secret"


def test_entry_points_gate_before_network(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "secret")
    monkeypatch.setattr(P, "http_open", lambda *a: pytest.fail("network bypass"))
    cfg = {"selfhosted": {"base_url": "https://example.test/v1", "api_key_env": "OPENAI_API_KEY"}}
    assert D.call_selfhosted(cfg, "m", "x", None, 1)[0] == 3
    assert DC.call({"name": "x", "model": "m", "base_url": "https://example.test/v1", "api_key_env": "OPENAI_API_KEY"},
                   {"model": "m", "state": "x", "questions": {}}, 1, cfg)[0] == 3


def test_observed_is_not_requested():
    r = request({})
    assert P.observe(r, {}) and r["observed"] == {"provider": None, "model": None, "effort": None}
    r["transmitted"] = {"model": "m", "effort": None}
    assert not P.observe(r, {"model": "other"})
    assert r["integrity"] == "mismatch"


def test_dry_run_no_spawn_network_or_writes(tmp_path, monkeypatch, capsys):
    p = tmp_path / "project"
    (p / "state").mkdir(parents=True)
    (p / "state/run_log.md").write_text("")
    q = tmp_path / "q.md"
    q.write_text("данные" * 100)
    monkeypatch.setattr(L, "load_routing", lambda: {"tasks": {"classify": {"tier": "T1"}},
        "families": {"openai": {"T1": "m"}}, "delegate_backends": ["codex"]})
    monkeypatch.setattr(D.subprocess, "run", lambda *a, **k: pytest.fail("spawn"))
    monkeypatch.setattr(P, "http_open", lambda *a: pytest.fail("network"))
    before = sorted(tmp_path.rglob("*"))
    assert D.main(["--project", str(p), "--prompt-file", str(q), "--task", "classify", "--dry-run"]) == 0
    plan = json.loads(capsys.readouterr().out)
    assert plan["status"] == "manual" and plan["files"][0]["bytes"] == len(q.read_bytes())
    assert sorted(tmp_path.rglob("*")) == before


@pytest.mark.parametrize("status", [401, 403, 429, 500])
def test_http_errors_no_retry_no_fallback(monkeypatch, status):
    import urllib.error
    calls = []
    cfg = {"execution_policy": api_policy("selfhosted", "example", "https://example.test/v1/chat/completions", "m"),
           "selfhosted": {"provider": "example", "base_url": "https://example.test/v1", "api_key_env": "FAKE_KEY"}}
    monkeypatch.setenv("FAKE_KEY", "secret")
    def fail(req, timeout):
        calls.append(req)
        raise urllib.error.HTTPError(req.full_url, status, "secret", {}, None)
    monkeypatch.setattr(P, "http_open", fail)
    code, _, error = D.call_selfhosted(cfg, "m", "x", None, 1)
    assert code == status and len(calls) == 1 and "secret" not in error


def test_proxy_env_and_redirects_cannot_change_endpoint(monkeypatch):
    import urllib.request
    got = []
    class Opener:
        def open(self, req, timeout):
            got.append(req.full_url)
            return "fake"
    def build(*handlers):
        assert isinstance(handlers[0], urllib.request.ProxyHandler) and handlers[0].proxies == {}
        assert handlers[1].redirect_request(None, None, 302, "", {}, "https://evil") is None
        return Opener()
    monkeypatch.setattr(urllib.request, "build_opener", build)
    monkeypatch.setenv("HTTPS_PROXY", "https://evil")
    assert P.http_open(urllib.request.Request("https://example.test"), 1) == "fake"
    assert got == ["https://example.test"]


@pytest.mark.parametrize("observed,expected", [(None, 0), ("m", 0), ("other", 1)])
def test_delegate_metadata_receipt_and_quarantine(tmp_path, monkeypatch, observed, expected):
    cfg = {"tasks": {"classify": {"tier": "T1"}}, "families": {"selfhosted": {"T1": "m"}},
           "delegate_backends": ["selfhosted"],
           "execution_policy": api_policy("selfhosted", "example", "https://example.test/v1/chat/completions", "m"),
           "selfhosted": {"provider": "example", "base_url": "https://example.test/v1", "api_key_env": "FAKE_KEY"}}
    monkeypatch.setattr(L, "load_routing", lambda: cfg)
    monkeypatch.setenv("FAKE_KEY", "fake-secret")
    calls = []
    class Response:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def read(self):
            data = {"choices": [{"message": {"content": "result"}}]}
            if observed is not None:
                data["model"] = observed
            return json.dumps(data).encode()
    def call(req, timeout):
        calls.append(req)
        assert json.loads(req.data)["max_completion_tokens"] == 1024
        return Response()
    monkeypatch.setattr(P, "http_open", call)
    project = tmp_path / "p"
    (project / "state").mkdir(parents=True)
    (project / "state/run_log.md").write_text("")
    prompt = tmp_path / "q.md"
    prompt.write_text("Classify")
    assert D.main(["--project", str(project), "--prompt-file", str(prompt), "--task", "classify"]) == expected
    receipt = json.loads(next((project / "state/delegated").glob("*.receipt.json")).read_text())
    assert receipt["requested"]["model"] == receipt["transmitted"]["model"] == "m"
    assert receipt["observed"]["model"] == observed and receipt["observed"]["effort"] is None
    assert len(calls) == 1
    if expected:
        assert receipt["result"] == "quarantined"
        assert not list((project / "state/delegated").glob("*.md"))


def test_cursor_allowlist_is_exact_not_family_fallback():
    cfg = {"environments": {"cursor": {"model_allowlist": ["grok-reviewed"]}}}
    assert request(cfg, host="cursor", model="grok-reviewed")["status"] == "manual"
    for model in ("claude-opus", "Auto", "grok-newest"):
        assert request(cfg, host="cursor", model=model)["status"] == "denied"


def test_timeout_single_attempt(monkeypatch):
    cfg = {"execution_policy": api_policy("selfhosted", "example", "https://example.test/v1/chat/completions", "m"),
           "selfhosted": {"provider": "example", "base_url": "https://example.test/v1", "api_key_env": "FAKE_KEY"}}
    monkeypatch.setenv("FAKE_KEY", "secret")
    seen = []
    def timeout(*args):
        seen.append(1)
        raise TimeoutError("secret")
    monkeypatch.setattr(P, "http_open", timeout)
    assert D.call_selfhosted(cfg, "m", "x", None, 1) == (1, "", "Transport error (details redacted); no fallback")
    assert len(seen) == 1


def test_code_only_gate_works_without_entitlement(tmp_path, monkeypatch):
    gate = tmp_path / "gate.yaml"
    gate.write_text('questions:\n  content:\n    instructions: Has content\n    check: {all: [content]}\n')
    art = tmp_path / "01_brief.md"
    art.write_text("content")
    project = tmp_path / "p"
    (project / "state").mkdir(parents=True)
    (project / "state/run_log.md").write_text("")
    monkeypatch.setattr(DC, "gate_file", lambda _: gate)
    monkeypatch.setattr(L, "load_routing", lambda: {})
    monkeypatch.setattr(P, "http_open", lambda *a: pytest.fail("network"))
    monkeypatch.setattr(D, "main", lambda *a: pytest.fail("LLM fallback"))
    assert DC.main(["--project", str(project), "--gate", str(art), "--no-emulate"]) == 0

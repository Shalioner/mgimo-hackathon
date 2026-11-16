"""Offline subscription fallback: auth/endpoint boundary and strict label evidence."""
import copy
import json
import sys
from pathlib import Path

import pytest

import native_codex as N
import subscription_classifier as S
from test_native_codex import cfg


@pytest.fixture
def subscription_server(tmp_path):
    def make(account_type="chatgpt", provider="openai", extra=None):
        binary, log = tmp_path / "fake-codex", tmp_path / "messages.jsonl"
        effective = {"model_provider": provider, "mcp_servers": {}, **(extra or {})}
        binary.write_text(f"""#!{sys.executable}
import json,sys,os
assert 'OPENAI_API_KEY' not in os.environ
assert sys.argv[1:3] == ['-c', 'forced_login_method="chatgpt"']
for line in sys.stdin:
 m=json.loads(line)
 with open({str(log)!r},'a') as f:f.write(json.dumps(m)+'\\n')
 method=m.get('method')
 if method=='initialize': out={{'id':1,'result':{{}}}}
 elif method=='config/read': out={{'id':4,'result':{{'config':{effective!r}}}}}
 elif method=='account/read': out={{'id':5,'result':{{'account':{{'type':{account_type!r},'email':'private@example.test'}}}}}}
 elif method=='thread/start': out={{'id':2,'result':{{'thread':{{'id':'thread'}}}}}}
 elif method=='turn/start':
  print(json.dumps({{'method':'item/completed','params':{{'threadId':'thread','item':{{'type':'agentMessage','text':'answer'}}}}}}),flush=True)
  out={{'method':'turn/completed','params':{{'threadId':'thread','turn':{{'id':'turn','status':'completed'}}}}}}
 else: continue
 print(json.dumps(out),flush=True)
""")
        binary.chmod(0o700)
        return binary, log
    return make


def test_subscription_auth_gate_and_no_api_environment(cfg, subscription_server, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "never-in-child")
    binary, log = subscription_server()
    receipt, text = N.execute(binary, "classify only", N.plan("T1", cfg, effort="low"),
        load_config=lambda: cfg, timeout=3, require_subscription=True)
    assert receipt["status"] == "succeeded" and text == "answer"
    assert receipt["auth_check"] == "chatgpt_confirmed_by_app_server"
    messages = [json.loads(line) for line in log.read_text().splitlines()]
    assert [m["method"] for m in messages] == ["initialize", "initialized", "config/read", "account/read", "thread/start", "turn/start"]
    assert messages[-2]["params"]["config"]["forced_login_method"] == "chatgpt"
    assert "private@example" not in json.dumps(receipt)


@pytest.mark.parametrize("account_type,provider,extra", [
    ("apiKey", "openai", {}), (None, "openai", {}),
    ("chatgpt", "custom", {}), ("chatgpt", "openai", {"chatgpt_base_url": "https://custom.test"}),
    ("chatgpt", "openai", {"model_providers": {"openai": {"env_key": "KEY"}}}),
])
def test_wrong_auth_or_provider_cannot_start_inference(cfg, subscription_server, account_type, provider, extra):
    binary, log = subscription_server(account_type, provider, extra)
    receipt, text = N.execute(binary, "classify only", N.plan("T1", cfg, effort="low"),
        load_config=lambda: cfg, timeout=3, require_subscription=True)
    assert receipt["status"] != "succeeded" and text is None
    assert "turn/start" not in log.read_text()


def test_native_classifier_low_effort_with_evidence_and_no_capability_gate(cfg, monkeypatch):
    request = "Read the provided file using tools"
    def execute(binary, prompt, selection, **kwargs):
        assert kwargs["require_subscription"] is True
        assert selection["tier"] == "T1" and selection["effort"] == "low"
        assert '"capability"' not in prompt
        answers = {key: {"choice": choice, "sure": True, "quote": request}
                   for key, choice in (("tier", "T1"), ("effort", "normal"))}
        return {"status": "succeeded", "transmitted_model": selection["model"]}, json.dumps({"answers": answers})
    monkeypatch.setattr(N, "execute", execute)
    out = S.classify(request, "", cfg=cfg, load_config=lambda:cfg, timeout=3,
                     question_scope="native", host="codex", binary="/fake")
    assert out["status"] == "accepted" and out["labels"] == {"tier": "T1", "effort": "normal"}
    assert out["threshold"] == .85


def test_self_declared_probabilities_are_not_subscription_evidence(cfg, monkeypatch):
    monkeypatch.setattr(N, "execute", lambda *a, **k: ({"status":"succeeded"},
        json.dumps({"answers": {"tier":{"type":"choice", "choice":"T1", "confidence":1}}})))
    out = S.classify("request", "", cfg=cfg, load_config=lambda:cfg, timeout=3,
                     question_scope="native", host="codex", binary="/fake")
    assert out["status"] == "needs_review"


def test_claude_unavailable_and_revoked_config_never_execute(cfg, monkeypatch):
    monkeypatch.setattr(N, "execute", lambda *a, **k: pytest.fail("no execution"))
    assert S.classify("request", "", cfg=cfg, load_config=lambda:cfg, timeout=3,
                     question_scope="native", host="claude", binary="/fake")["status"] == "unavailable"
    changed = copy.deepcopy(cfg); changed["automatic_routing"] = {}
    assert S.classify("request", "", cfg=cfg, load_config=lambda:changed, timeout=3,
                     question_scope="native", host="codex", binary="/fake")["status"] == "unavailable"

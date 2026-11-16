"""Локальный System One сервер (llama.cpp + d1 GGUF): loopback-only grant без ключа и оплаты."""
import copy
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

import decide as DC
import routing_policy as P

CTX = {"model_limit": 8192, "plan_limit": 8192, "user_limit": 8192, "output_reserve": 1, "system_reserve": 512}


def cfg_for(endpoint, *, host="decision", local=True, model="d1-2B-Q8_0"):
    grant = {"host": host, "provider": "llamacpp-d1", "endpoint": endpoint, "models": [model],
             "efforts": [None], "context": CTX}
    if local is not None:
        grant["local"] = local
    return {"execution_policy": {"mode": "api_opt_in", "max_input_bytes": 128000,
                                 "api": {"enabled": True, "grants": [grant]}},
            "decision": {"providers": [{"name": "llamacpp-d1", "protocol": "systemone",
                                        "base_url": endpoint.rsplit("/systemone", 1)[0], "model": model}]}}


def plan(cfg, endpoint, host="decision"):
    return P.plan(cfg, host=host, provider="llamacpp-d1", model="d1-2B-Q8_0", endpoint=endpoint,
                  input_bytes=300, key_present=False)


def test_local_loopback_grant_is_executable_without_key_or_budget():
    ep = "http://127.0.0.1:8089/v1/systemone"
    r = plan(cfg_for(ep), ep)
    assert r["status"] == "executable"
    assert r["billing"]["auth"] == "none" and r["billing"]["budget_usd"] == 0
    assert r["billing"]["cap_verification"] == "local_loopback_no_billing"


def test_local_flag_absent_still_requires_key_budget_and_cap():
    ep = "http://127.0.0.1:8089/v1/systemone"
    assert plan(cfg_for(ep, local=None), ep)["status"] != "executable"


@pytest.mark.parametrize("ep", ["https://example.com/v1/systemone", "https://api.local.example/v1/systemone"])
def test_local_grant_refused_off_loopback(ep):
    r = plan(cfg_for(ep), ep)
    assert r["status"] == "denied" and "loopback" in r["reason"]


def test_local_grant_refused_for_other_hosts():
    ep = "http://127.0.0.1:8089/v1/chat/completions"
    r = plan(cfg_for(ep, host="selfhosted"), ep, host="selfhosted")
    assert r["status"] == "denied"


def test_local_grant_keeps_context_bound():
    ep = "http://127.0.0.1:8089/v1/systemone"
    r = P.plan(cfg_for(ep), host="decision", provider="llamacpp-d1", model="d1-2B-Q8_0", endpoint=ep,
               input_bytes=10_000, key_present=False)
    assert r["status"] == "manual" and "Контекст" in r["reason"]


class _Handler(BaseHTTPRequestHandler):
    reply = {}

    def do_POST(self):  # noqa: N802
        self.rfile.read(int(self.headers["Content-Length"]))
        body = json.dumps(self.reply).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


@pytest.fixture
def server():
    srv = HTTPServer(("127.0.0.1", 0), _Handler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{srv.server_port}/v1/systemone"
    srv.shutdown()


def call(cfg, reply):
    _Handler.reply = reply
    provider = cfg["decision"]["providers"][0]
    body = {"model": "d1-2B-Q8_0", "state": "s", "questions": {"q": {"type": "noul", "instructions": "?"}}}
    receipt = {"billing": {}, "started": False}
    code, data, err = DC.call(provider, body, 10, cfg, receipt)
    return code, data, err


def test_call_does_not_contact_openrouter_and_accepts_answer(server):
    code, data, err = call(cfg_for(server), {"model": "d1-2B-Q8_0", "answers": {"q": {"type": "noul", "noul": 0.9}},
                                             "usage": {"input_tokens": 5, "output_tokens": 0}})
    assert code == 0 and data["answers"]["q"]["noul"] == 0.9, err


def test_observe_without_cost_field_is_not_mismatch_for_local_grant():
    receipt = {"requested": {"provider": "llamacpp-d1", "model": "d1-2B-Q8_0", "effort": None},
               "transmitted": {"model": "d1-2B-Q8_0", "effort": None},
               "billing": {"budget_usd": 0, "cap_verification": "local_loopback_no_billing"}}
    assert P.observe(copy.deepcopy(receipt), {"model": "d1-2B-Q8_0", "usage": {"input_tokens": 5}})
    wrong = copy.deepcopy(receipt)
    assert not P.observe(wrong, {"model": "weights/d1-2B-Q8_0.gguf"})  # путь вместо alias — расхождение

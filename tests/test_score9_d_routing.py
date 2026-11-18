"""Round D2 routing tests: preregistered cases, local-d1/service gates, failures, and T0 check.

All tests are offline and never touch the user's live D1 socket, service, or external APIs.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import socket
import subprocess
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
ROUND_D_ROUTING = ROOT / "evals/score9/design-round/d/routing"
PREREGISTERED_PATH = ROUND_D_ROUTING / "preregistered_cases.json"

sys.path.insert(0, str(TOOLS))
import decision_runtime as DR  # noqa: E402
import route_request as R  # noqa: E402


def _baseline_cfg(socket_path: Path, *, reuse=False) -> dict:
    cfg = yaml.safe_load((ROOT / "knowledge/routing.yaml").read_text(encoding="utf-8"))
    cfg["automatic_routing"] = {
        "classifier": "local-d1",
        "local_d1": {
            "mode": "service",
            "reuse": reuse,
            "socket": str(socket_path),
        },
    }
    return cfg


def _d1_answer(qs: dict, choices: dict[str, str], confidences: dict[str, float]) -> dict:
    answers = {}
    for key, q in qs.items():
        crit = list(q["criteria"].keys())
        choice = choices[key]
        conf = confidences[key]
        rest = (1.0 - conf) / (len(crit) - 1)
        probs = {opt: (conf if opt == choice else rest) for opt in crit}
        answers[key] = {
            "type": "choice",
            "choice": choice,
            "confidence": conf,
            "probabilities": probs,
        }
    return {"usage": {"input_tokens": 64, "output_tokens": 0}, "answers": answers}


@pytest.fixture
def fake_service_socket(monkeypatch):
    if not hasattr(os, "getuid"):
        pytest.skip("The socket ownership fixture requires POSIX getuid")
    with tempfile.TemporaryDirectory(prefix="score9-d2-sock-", dir="/tmp") as tmp:
        os.chmod(tmp, 0o700)
        sock_path = Path(tmp) / "service.sock"
        srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        srv.bind(str(sock_path))
        os.chmod(sock_path, 0o600)
        monkeypatch.setattr(DR.sys, "platform", "darwin")
        monkeypatch.setattr(DR, "_invoke", lambda *a, **kw: pytest.fail("Service must never fall back to lab subprocess"))
        monkeypatch.setattr(R.D, "execute_route", lambda *a, **kw: pytest.fail("No LLM task execution allowed in D2"))

        calls = []
        reply_template = {
            "status": "succeeded",
            "response": _d1_answer(
                R.questions(),
                {"tier": "T2", "effort": "normal", "capability": "text"},
                {"tier": 0.91, "effort": 0.88, "capability": 0.95},
            ),
            "runtime": {
                "worker_pid": 99999,
                "generation": 1,
                "model": DR.mlx.REPO,
                "revision": DR.mlx.REVISION,
                "inference_calls": 1,
                "reuse": "bypass",
            },
        }

        def request(path, payload, timeout):
            assert path == str(sock_path)
            calls.append(payload)
            out = copy.deepcopy(reply_template)
            out.setdefault("id", payload.get("id"))
            return out

        monkeypatch.setitem(sys.modules, "d1_service", SimpleNamespace(request=request))
        try:
            yield sock_path, calls, reply_template
        finally:
            srv.close()


def test_preregistered_cases_match_baseline_and_schema():
    data = json.loads(PREREGISTERED_PATH.read_text(encoding="utf-8"))
    baseline_cases_path = ROOT / "evals/routing/d1_quality_cases.json"
    assert data["source_cases_sha256"] == hashlib.sha256(baseline_cases_path.read_bytes()).hexdigest()
    baseline_by_id = {c["id"]: c for c in json.loads(baseline_cases_path.read_text(encoding="utf-8"))["cases"]}
    qs = R.questions()
    pos_ids = [c["id"] for c in data["positive_cases"]]
    assert pos_ids == ["Q01", "Q07", "Q13", "D2_NEW_01"]
    for case in data["positive_cases"]:
        assert isinstance(case["request"], str) and case["request"].strip()
        assert isinstance(case["context"], str)
        for axis in ("tier", "effort", "capability"):
            assert case[axis] and set(case[axis]) <= set(qs[axis]["criteria"])
        if case["origin"] == "evals/routing/d1_quality_cases.json":
            ref = baseline_by_id[case["id"]]
            for field in ("request", "context", "tier", "effort", "capability", "basis", "taxonomy_ceiling_T0"):
                assert case[field] == ref[field]


def test_preflight_and_classify_only_stop_before_execution(fake_service_socket):
    sock_path, calls, _ = fake_service_socket
    cfg = _baseline_cfg(sock_path, reuse=False)

    pre = R.run_request("Составь бриф", "контекст", ["codex"], execute=False, load_config=lambda: copy.deepcopy(cfg))
    assert pre["status"] == "awaiting_classification"
    assert pre["classification"]["status"] == "pending"
    assert pre["classification"]["route"]["status"] == "executable"
    assert pre["classification"]["route"]["started"] is False
    assert pre["result"]["status"] == "not_started"
    assert calls == []

    cls_only = R.run_request("Составь бриф", "контекст", ["codex"], execute=True, classify_only=True,
                             load_config=lambda: copy.deepcopy(cfg))
    assert cls_only["status"] == "classified"
    assert cls_only["classification"]["status"] == "accepted"
    assert cls_only["classification"]["labels"] == {"tier": "T2", "effort": "normal", "capability": "text"}
    assert cls_only["start"] is None
    assert cls_only["result"] == {"status": "not_started", "text": None}
    assert len(calls) == 1 and calls[0]["reuse"] is False


def test_confident_route_hits_baseline_policy_gate_not_started(fake_service_socket):
    sock_path, calls, _ = fake_service_socket
    cfg = _baseline_cfg(sock_path, reuse=False)

    report = R.run_request("Составь бриф", "контекст", ["codex"], execute=True, load_config=lambda: copy.deepcopy(cfg))
    assert len(calls) == 1 and calls[0]["reuse"] is False
    assert report["classification"]["status"] == "accepted"
    assert report["classification"]["route"]["observed"] == {"model": DR.mlx.REPO, "revision": DR.mlx.REVISION}
    assert report["plan"]["status"] == "manual"
    assert report["plan"]["selected"] is None
    assert report["plan"]["candidates"][0]["status"] == "manual"
    assert report["start"] is None
    assert report["result"] == {"status": "not_started", "text": None}
    assert report["status"] == "manual"


def test_capability_tools_stops_before_host_plan(fake_service_socket):
    sock_path, calls, reply_template = fake_service_socket
    reply_template["response"] = _d1_answer(
        R.questions(),
        {"tier": "T1", "effort": "normal", "capability": "tools"},
        {"tier": 0.90, "effort": 0.90, "capability": 0.92},
    )
    cfg = _baseline_cfg(sock_path, reuse=False)
    report = R.run_request("Открой файл", "нет содержимого", ["codex"], execute=True, load_config=lambda: copy.deepcopy(cfg))
    assert report["classification"]["status"] == "accepted"
    assert report["plan"]["status"] == "manual"
    assert report["start"] is None
    assert report["result"]["status"] == "not_started"


@pytest.mark.parametrize("low_axis", ["tier", "effort", "capability"])
def test_low_confidence_below_075_triggers_needs_review(fake_service_socket, low_axis):
    sock_path, calls, reply_template = fake_service_socket
    confs = {"tier": 0.91, "effort": 0.88, "capability": 0.95}
    confs[low_axis] = 0.74
    reply_template["response"] = _d1_answer(
        R.questions(),
        {"tier": "T2", "effort": "normal", "capability": "text"},
        confs,
    )
    cfg = _baseline_cfg(sock_path, reuse=False)
    report = R.run_request("Неясный запрос", "мало данных", ["codex"], execute=True, load_config=lambda: copy.deepcopy(cfg))
    assert len(calls) == 1
    assert report["classification"]["status"] == "needs_review"
    assert report["classification"]["labels"] is None
    assert report["plan"] is None
    assert report["start"] is None
    assert report["result"] == {"status": "not_started", "text": None}
    assert report["status"] == "manual"


@pytest.mark.parametrize("failure", ["blocked_guard", "wrong_revision", "bad_probabilities", "id_mismatch"])
def test_service_guard_and_invalid_replies_fail_closed(fake_service_socket, failure):
    sock_path, calls, reply_template = fake_service_socket
    if failure == "blocked_guard":
        reply_template.clear()
        reply_template.update({"status": "blocked", "reason": "Critical system memory pressure"})
    elif failure == "wrong_revision":
        reply_template["runtime"]["revision"] = "0000000000000000"
    elif failure == "bad_probabilities":
        reply_template["response"]["answers"]["tier"]["probabilities"]["T1"] = 0.9
    elif failure == "id_mismatch":
        reply_template["id"] = "foreign-id"

    cfg = _baseline_cfg(sock_path, reuse=False)
    report = R.run_request("Запрос", "контекст", ["codex"], execute=True, load_config=lambda: copy.deepcopy(cfg))
    assert len(calls) == 1
    assert report["classification"]["status"] == "needs_review"
    assert report["classification"]["labels"] is None
    assert report["plan"] is None
    assert report["start"] is None
    assert report["result"] == {"status": "not_started", "text": None}


@pytest.mark.skipif(not hasattr(os, "getuid"), reason="The socket ownership fixture requires POSIX getuid")
def test_missing_socket_and_refused_socket_and_oversize_fail_closed(monkeypatch, tmp_path):
    monkeypatch.setattr(DR.sys, "platform", "darwin")
    monkeypatch.setattr(DR, "_invoke", lambda *a, **kw: pytest.fail("No lab fallback"))

    missing_sock = tmp_path / "missing" / "service.sock"
    cfg_missing = _baseline_cfg(missing_sock, reuse=False)
    rep_missing = R.run_request("Запрос", "контекст", ["codex"], execute=True, load_config=lambda: copy.deepcopy(cfg_missing))
    assert rep_missing["classification"]["status"] == "unavailable"
    assert rep_missing["classification"]["route"]["status"] == "manual"
    assert rep_missing["classification"]["route"]["started"] is False
    assert rep_missing["result"]["status"] == "not_started"

    with tempfile.TemporaryDirectory(prefix="score9-d2-refused-", dir="/tmp") as td:
        os.chmod(td, 0o700)
        refused_sock = Path(td) / "service.sock"
        srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        srv.bind(str(refused_sock))
        os.chmod(refused_sock, 0o600)
        srv.close()
        cfg_refused = _baseline_cfg(refused_sock, reuse=False)
        rep_refused = R.run_request("Запрос", "контекст", ["codex"], execute=True, load_config=lambda: copy.deepcopy(cfg_refused))
        assert rep_refused["classification"]["status"] == "needs_review"
        assert rep_refused["classification"]["route"]["result"] == "failed"
        assert rep_refused["classification"]["route"]["cancel_status"] == "unconfirmed"
        assert rep_refused["result"]["status"] == "not_started"

    cfg_oversize = _baseline_cfg(tmp_path / "unused.sock", reuse=False)
    rep_oversize = R.run_request("Запрос", "я" * 17000, ["codex"], execute=True, load_config=lambda: copy.deepcopy(cfg_oversize))
    assert rep_oversize["classification"]["status"] == "unavailable"
    assert "no truncation" in rep_oversize["classification"]["route"]["reason"]
    assert rep_oversize["classification"]["route"]["started"] is False
    assert rep_oversize["result"]["status"] == "not_started"


def test_deterministic_t0_interface_runs_without_llm():
    proc = subprocess.run(
        [sys.executable, str(TOOLS / "ledger.py"), "route", "validate"],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert proc.returncode == 0
    assert "T0" in proc.stdout

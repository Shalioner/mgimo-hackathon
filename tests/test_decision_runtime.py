"""No inference: exercise local adapter boundaries and real System One validation."""
import copy
import json
import subprocess
from pathlib import Path

import pytest

import decision_runtime as DR
import route_request as R

CFG = {"automatic_routing": {"classifier": "local-d1", "local_d1": {"mode": "lab"}}}


def response(qs, choices=None, confidence=0.95):
    choices = choices or {"tier": "T2", "effort": "normal", "capability": "text"}
    return {"usage": {"input_tokens": 100, "output_tokens": 0}, "answers": {
        key: {"type": "choice", "choice": choices[key], "confidence": confidence,
              "probabilities": {option: confidence if option == choices[key] else
                                (1-confidence)/(len(q["criteria"])-1) for option in q["criteria"]}}
        for key, q in qs.items()}}


@pytest.fixture
def local(monkeypatch, tmp_path):
    binary = tmp_path / "python"
    binary.write_text("fixture")
    binary.chmod(0o700)
    weights = tmp_path / "weights"
    weights.mkdir()
    monkeypatch.setattr(DR, "PYTHON", binary)
    monkeypatch.setattr(DR, "SANDBOX", binary)
    monkeypatch.setattr(DR.mlx, "WEIGHTS", weights)
    monkeypatch.setattr(DR.sys, "platform", "darwin")
    calls = []
    report = {"status": "succeeded", "repo": DR.mlx.REPO, "revision": DR.mlx.REVISION,
              "results": [{"id": "request", "response": response(R.questions())}]}
    def invoke(command, **kwargs):
        calls.append((command, kwargs))
        # Actual wrapper inputs must survive exactly; no hidden truncation.
        calls[-1][1]["state"] = Path(command[command.index("--state-file") + 1]).read_text()
        calls[-1][1]["questions"] = json.loads(Path(command[command.index("--questions") + 1]).read_text())
        return (0 if report["status"] == "succeeded" else 3), json.dumps(report), ""
    monkeypatch.setattr(DR, "_invoke", invoke)
    monkeypatch.setattr(R.D, "execute_route", lambda *a, **kw: pytest.fail("No cloud task allowed"))
    return calls, report


def test_preflight_and_classify_only_are_distinct(local):
    calls, _ = local
    first = R.run_request("Составь план", "контекст", ["codex"], load_config=lambda: CFG, classify_only=True)
    assert first["status"] == "awaiting_classification" and not calls
    result = R.run_request("Составь план", "контекст", ["codex"], execute=True, load_config=lambda: CFG, classify_only=True)
    assert result["status"] == "classified"
    assert result["classification"]["labels"]["tier"] == "T2"
    assert result["result"]["status"] == "not_started" and result["start"] is None
    assert len(calls) == 1
    command, kwargs = calls[0]
    assert command[:3] == [str(DR.SANDBOX), "-p", "(version 1)(allow default)(deny network*)"]
    assert json.loads(kwargs["state"]) == {"request": "Составь план", "context": "контекст"}
    assert "--timeout" in command and kwargs["timeout"] == 330
    assert not any("KEY" in key or key.startswith("ORCA") or key == "PYTHONPATH" for key in kwargs["env"])


@pytest.mark.parametrize("failure", ["missing", "mismatch", "probabilities", "confidence", "low", "blocked"])
def test_local_failures_do_not_fallback(local, failure):
    calls, report = local
    answer = report["results"][0]["response"]["answers"]["tier"]
    if failure == "missing":
        del answer["probabilities"]
    elif failure == "mismatch":
        report["revision"] = "different"
    elif failure == "probabilities":
        answer["probabilities"] = {"T1": 0.1, "T2": 0.1, "T3": 0.1}
    elif failure == "confidence":
        answer["confidence"] = 0.999
    elif failure == "low":
        report["results"][0]["response"] = response(R.questions(), confidence=0.6)
    else:
        report.update(status="blocked", reason="Memory pressure")
    result = R.classify_request("request", execute=True, load_config=lambda: CFG)
    assert result["status"] == "needs_review" and not result["labels"]
    assert len(calls) == 1


def test_oversize_no_truncation_no_spawn(local):
    calls, _ = local
    result = R.classify_request("request", "я"*5000, execute=True, load_config=lambda: CFG)
    assert result["status"] == "unavailable" and not calls
    assert "no truncation" in result["route"]["reason"]


def test_explicit_synopsis_preserves_complete_context(local):
    calls, _ = local
    full = "я" * 10000
    result = R.run_request("request", full, ["codex"], execute=True, classify_only=True,
                           classification_context="summary", load_config=lambda: CFG)
    assert result["context"] == full and result["classification_context"] == "summary"
    assert json.loads(calls[0][1]["state"])["context"] == "summary"


def test_revoke_local_selection_before_spawn(local):
    calls, _ = local
    configs = iter([CFG, {}])
    result = R.classify_request("request", execute=True, load_config=lambda: next(configs))
    assert result["status"] == "unavailable" and not calls


@pytest.mark.parametrize("options", [{"classifier": "typo"}, {"classifier": "provider"},
                                      {"classifier": "local-d1", "decision_provider": "paid"},
                                      {"classifier": "bootstrap", "decision_provider": "paid"}])
def test_explicit_unknown_or_conflicting_selection_stops(local, options):
    calls, _ = local
    result = R.classify_request("request", execute=True, load_config=lambda: {"automatic_routing": options})
    assert result["status"] == "unavailable" and not calls


def test_timeout_kills_entire_owned_process_group(monkeypatch):
    class Process:
        pid = 123
        def communicate(self, **kwargs):
            raise subprocess.TimeoutExpired("local", 1)
        def poll(self):
            return None
        def wait(self):
            killed.append("wait")
    killed = []
    monkeypatch.setattr(DR.subprocess, "Popen", lambda *a, **kw: Process())
    monkeypatch.setattr(DR.os, "killpg", lambda pid, sig: killed.append((pid, sig)))
    with pytest.raises(subprocess.TimeoutExpired):
        DR._invoke(["local"], cwd="/tmp", env={}, timeout=1)
    assert killed == [(123, DR.signal.SIGKILL), "wait"]

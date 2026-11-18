"""Offline regression coverage; no credentials or network required."""
import copy
import json

import pytest

import decide as D
import openrouter_decision_smoke as S
import routing_policy as P


QUESTIONS = {
    "yes": {"type": "noul"},
    "team": {"type": "choice", "criteria": {"billing": None, "technical": None}},
    "level": {"type": "score", "criteria": ["low", "high"]},
}


def response():
    return {"model": "perplexity/pplx-decider-v1.3-20261112", "provider": "Perplexity",
            "usage": {"cost": 0}, "answers": {
                "yes": {"type": "noul", "noul": .99},
                "team": {"type": "choice", "choice": "billing", "confidence": .98,
                         "probabilities": {"billing": .99, "technical": .01}},
                "level": {"type": "score", "score": .8, "confidence": .6,
                          "probabilities": {"0": .2, "1": .8}}}}


@pytest.mark.parametrize("change", ["refusals", "missing_identity", "wrong_type", "bad_range",
                                    "bad_choice", "bad_sum", "missing_probability", "nan"])
def test_invalid_typed_response_rejected(change):
    data = response()
    if change == "refusals":
        data["answers"] = {key: {"type": "refusal"} for key in QUESTIONS}
    elif change == "missing_identity":
        data.pop("model")
    elif change == "wrong_type":
        data["answers"]["team"] = {"type": "noul", "noul": .99}
    elif change == "bad_range":
        data["answers"]["yes"]["noul"] = 999
    elif change == "bad_choice":
        data["answers"]["team"]["choice"] = "technical"
    elif change == "bad_sum":
        data["answers"]["team"]["probabilities"]["billing"] = .5
    elif change == "missing_probability":
        data["answers"]["team"]["probabilities"].pop("technical")
    else:
        data["answers"]["level"]["score"] = float("nan")
    assert not D.valid_openrouter_response(data, QUESTIONS, allow_refusal=False)


def test_partial_refusal_preserves_other_answers_for_review():
    data = response()
    data["answers"]["team"] = {"type": "refusal"}
    assert D.valid_openrouter_response(data, QUESTIONS)
    assert D.doubtful(data["answers"]["team"], .75)
    assert D.doubtful(data["answers"]["yes"], .75) is None
    assert not D.valid_openrouter_response(data, QUESTIONS, allow_refusal=False)


def test_live_response_contract_and_exact_alias(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "fake-key")
    receipt = D.decision_plan(S.configuration(), S.PROVIDER, {"model": S.MODEL, "state": "synthetic", "questions": QUESTIONS})
    receipt["transmitted"] = {"model": S.MODEL, "provider": "Perplexity", "effort": None}
    data = response()
    assert D.valid_openrouter_response(data, QUESTIONS)
    assert P.observe(receipt, data)
    assert receipt["observed"]["model"] != receipt["transmitted"]["model"]
    assert receipt["request_satisfaction"]["model"] is True
    for override in ({"model": "perplexity/pplx-decider-v1.3-20261113"},
                     {"provider": "another"}, {"usage": {"cost": .01}}):
        assert not P.observe(copy.deepcopy(receipt), {**data, **override})
    receipt["endpoint"] = "https://other.test"
    assert not P.observe(receipt, data)


def test_probe_dry_run_no_network_or_files(tmp_path, monkeypatch):
    monkeypatch.setattr(P, "http_open", lambda *a: pytest.fail("network"))
    output = tmp_path / "report.json"
    assert S.main(["--output", str(output)]) == 0
    assert not output.exists()


@pytest.mark.parametrize("limit", [None, 1, -1, False])
def test_probe_rejects_nonzero_or_missing_key_cap(monkeypatch, limit):
    monkeypatch.setenv("OPENROUTER_API_KEY", "fake-key")
    class Response:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def read(self): return json.dumps({"data": {"limit": limit, "is_free_tier": True}}).encode()
    monkeypatch.setattr(P, "http_open", lambda *a: Response())
    with pytest.raises(ValueError):
        S.verify_key(1)

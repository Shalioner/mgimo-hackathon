"""Sequential evidence pipeline, real routing gate/parser, offline transport only."""
import copy
import json

import pytest

import document_flow as F
import delegate as D
from test_codex_subscription import subscription


def evidence(quote="Retention is 24%."):
    return {"topic": "Retention", "findings": [
        {"kind": "fact", "statement": "Retention is 24%.", "quote": quote, "start_line": 1, "end_line": 1}],
        "limitations": ["One period only"], "contradictions": []}


@pytest.fixture
def flow(subscription, tmp_path, monkeypatch):
    cfg, _, _, _, _ = subscription
    cfg["families"]["openai"].update(T2="terra", T3="sol")
    cfg["tiers"].update(T2={"effort": ["medium", "high"]}, T3={"effort": ["high", "xhigh"]})
    cfg["execution_policy"]["codex_subscription"]["models"] += ["terra", "sol"]
    cfg["execution_policy"]["codex_subscription"]["efforts"] += ["medium", "high", "xhigh"]
    paths = [tmp_path / "a.md", tmp_path / "b.md"]
    paths[0].write_text("Retention is 24%.\nORIGINAL_A_NEVER_TO_SENIOR\n" + "irrelevant background\n" * 100)
    paths[1].write_text("Retention is 18%.\nORIGINAL_B_NEVER_TO_SENIOR\n" + "irrelevant background\n" * 100)
    calls, syntheses = [], []
    controls = {"tier": "T2", "transform": lambda card: card, "callback": lambda: None, "fail": False}

    def call(cfg, model, prompt, effort, timeout, receipt, human_approved=False):
        calls.append({"model": model, "prompt": prompt, "effort": effort})
        receipt.update(started=True, wrapper_invocations=1, process_exit_code=0,
                       transmitted={"model": model, "effort": effort})
        if model == "m":
            payload = json.loads(prompt.rsplit("\n", 1)[-1])
            quote = payload["numbered_document"].splitlines()[0].split(": ", 1)[1]
            card = evidence(quote)
            card["findings"][0]["statement"] = quote
            answer = json.dumps(controls["transform"](card))
        else:
            answer = "Synthesis with source evidence"
        controls["callback"]()
        return (429 if controls["fail"] else 0), json.dumps({"type": "turn.completed"}), "", answer

    def synthesize(request, context, hosts, **kw):
        syntheses.append({"request": request, "context": context, "hosts": hosts, **kw})
        prompt = D.GUARD + request + context
        route = F.R.route_plan(kw["load_config"](), hosts[0], controls["tier"], prompt)
        receipt, answer, _ = D.execute_route(cfg, hosts[0], route["requested"]["model"], prompt,
                                             route["requested"]["effort"], kw["timeout"], route)
        return {"status": receipt["result"], "result": {"status": receipt["result"], "text": answer}}

    monkeypatch.setattr(D, "call_codex", call)
    monkeypatch.setattr(F.R, "run_request", synthesize)
    return cfg, paths, calls, syntheses, controls


@pytest.mark.parametrize("tier,model", [("T2", "terra"), ("T3", "sol")])
def test_sequential_luna_cards_then_senior_without_originals(flow, tier, model):
    cfg, paths, calls, syntheses, controls = flow
    controls["tier"] = tier
    result = F.run_documents("Compare retention", paths, ["codex"], execute=True, load_config=lambda: cfg)
    assert result["status"] == "succeeded"
    assert [c["model"] for c in calls] == ["m", "m", model]
    assert "ORIGINAL_A_NEVER_TO_SENIOR" in calls[0]["prompt"]
    assert "ORIGINAL_B_NEVER_TO_SENIOR" not in calls[0]["prompt"]
    assert "ORIGINAL_A_NEVER_TO_SENIOR" not in calls[1]["prompt"]
    assert "ORIGINAL_B_NEVER_TO_SENIOR" in calls[1]["prompt"]
    senior = calls[-1]["prompt"]
    assert "NEVER_TO_SENIOR" not in senior and "irrelevant background" not in senior
    assert "Retention is 24%." in senior and "Retention is 18%." in senior
    assert len(result["cards"]) == 2
    assert result["cards"][0]["source"]["sha256"] == F._hash(paths[0].read_bytes())
    assert result["cards"][1]["findings"][0]["start_line"] == 1
    assert "semantic_support_not_verified" in result["cards"][0]["verification"]
    synopsis = json.loads(syntheses[0]["classification_context"])
    assert len(synopsis["documents"]) == 2 and "evidence_cards" not in synopsis
    # The next unrelated invocation starts at T1, not the last senior tier.
    F.run_documents("Simple comparison", paths[:1], ["codex"], execute=True, load_config=lambda: cfg)
    assert calls[3]["model"] == "m"


def test_dry_run_no_inference_no_synthesis_and_no_source_change(flow):
    cfg, paths, calls, syntheses, _ = flow
    before = [p.read_bytes() for p in paths]
    result = F.run_documents("Compare", paths, ["codex"], load_config=lambda: cfg)
    assert result["status"] == "planned" and len(result["extractions"]) == 2
    assert result["synthesis"]["status"] == "awaiting_cards"
    assert not calls and not syntheses and [p.read_bytes() for p in paths] == before
    assert "NEVER_TO_SENIOR" not in json.dumps(result)


@pytest.mark.parametrize("mutation", [
    lambda c: {**c, "findings": [], "limitations": []},
    lambda c: {**c, "findings": [{**c["findings"][0], "quote": "invented"}]},
    lambda c: {**c, "findings": [{**c["findings"][0], "start_line": True}]},
    lambda c: {**c, "findings": [{**c["findings"][0], "end_line": 3}]},
    lambda c: {**c, "topic": "x" * 121},
    lambda c: {**c, "limitations": "unknown"},
    lambda c: {**c, "unexpected": "ignored raw source"},
    lambda c: {**c, "limitations": ["x" * 7000]},
])
def test_invalid_card_stops_before_next_document_or_synthesis(flow, mutation):
    cfg, paths, calls, syntheses, controls = flow
    controls["transform"] = mutation
    result = F.run_documents("Compare", paths, ["codex"], execute=True, load_config=lambda: cfg)
    assert result["status"] == "needs_review"
    assert result["extractions"][0]["status"] == "invalid_card"
    assert len(calls) == 1 and not syntheses and not result["cards"]


def test_transport_failure_no_other_host_or_synthesis(flow):
    cfg, paths, calls, syntheses, controls = flow
    controls["fail"] = True
    result = F.run_documents("Compare", paths, ["codex", "claude-code"], execute=True, load_config=lambda: cfg)
    assert result["status"] == "failed" and len(calls) == 1 and not syntheses


def test_irrelevant_middle_document_keeps_provenance_and_continues(flow):
    cfg, paths, calls, syntheses, controls = flow
    third = paths[0].parent / "c.md"
    third.write_text("Retention is 30%.\n")
    paths.append(third)
    reason = "No findings relevant to this request"
    controls["transform"] = lambda c: {**c, "findings": [], "limitations": [reason]} if len(calls) == 2 else c
    result = F.run_documents("Compare", paths, ["codex"], execute=True, load_config=lambda: cfg)
    assert result["status"] == "succeeded"
    assert [c["model"] for c in calls] == ["m", "m", "m", "terra"]
    assert len(result["cards"]) == 3 and len(syntheses) == 1
    middle = result["cards"][1]
    assert middle["findings"] == [] and middle["limitations"] == [reason]
    assert middle["source"]["sha256"] == F._hash(paths[1].read_bytes())
    assert middle["source"]["id"] == "DOC-002"
    assert reason in syntheses[0]["context"]
    assert "Retention is 18%." not in syntheses[0]["context"]


def test_all_irrelevant_preserves_cards_without_senior_call(flow):
    cfg, paths, calls, syntheses, controls = flow
    controls["transform"] = lambda c: {**c, "findings": [], "limitations": ["No relevant findings"]}
    result = F.run_documents("Compare", paths, ["codex"], execute=True, load_config=lambda: cfg)
    assert len(calls) == 2 and not syntheses
    assert result["status"] == "needs_review"
    assert "No relevant findings" in result["reason"]
    assert len(result["cards"]) == 2
    assert result["result"] == {"status": "not_started", "text": None}


def test_source_changed_during_first_extraction_stops_pipeline(flow):
    cfg, paths, calls, syntheses, controls = flow
    controls["callback"] = lambda: paths[1].write_text("changed")
    result = F.run_documents("Compare", paths, ["codex"], execute=True, load_config=lambda: cfg)
    assert result["status"] == "stale_input" and len(calls) == 1 and not syntheses
    assert not result["cards"]


def test_source_changed_during_synthesis_withholds_result(flow):
    cfg, paths, calls, syntheses, controls = flow
    def mutate():
        if len(calls) == 3:
            paths[0].write_text("changed")
    controls["callback"] = mutate
    result = F.run_documents("Compare", paths, ["codex"], execute=True, load_config=lambda: cfg)
    assert result["status"] == "stale_input"
    assert result["result"]["text"] is None and result["synthesis"]["result"]["text"] is None


def test_policy_revoked_before_extraction(flow):
    cfg, paths, calls, syntheses, _ = flow
    reads = []
    def load():
        reads.append(1)
        current = copy.deepcopy(cfg)
        if len(reads) > 1:
            current["execution_policy"]["codex_subscription"]["enabled"] = False
        return current
    result = F.run_documents("Compare", paths, ["codex"], execute=True, load_config=load)
    assert result["status"] == "manual" and not calls and not syntheses


def test_quote_budget_and_context_budget_are_explicit(flow, monkeypatch):
    cfg, paths, calls, syntheses, _ = flow
    monkeypatch.setattr(F, "MAX_CONTEXT_BYTES", 40)
    result = F.run_documents("Compare", paths, ["codex"], execute=True, load_config=lambda: cfg)
    assert result["status"] == "needs_review" and "no truncation" in result["reason"]
    assert len(calls) == 2 and not syntheses
    source = F.snapshot_sources(paths[:1])[0]
    monkeypatch.setattr(F, "MAX_QUOTE_BYTES", 3)
    with pytest.raises(ValueError, match="quotes exceed"):
        F.validate_card(json.dumps(evidence()), source)


def test_contradictions_preserved_not_silently_resolved(flow):
    cfg, paths, _, syntheses, controls = flow
    controls["transform"] = lambda c: {**c, "contradictions": ["Different periods must not be conflated"]}
    result = F.run_documents("Compare", paths, ["codex"], execute=True, load_config=lambda: cfg)
    assert result["status"] == "succeeded"
    assert "Different periods must not be conflated" in syntheses[0]["context"]
    assert all(d["contradictions"] == 1 for d in json.loads(syntheses[0]["classification_context"])["documents"])


def test_source_count_duplicate_and_size_limits(tmp_path):
    source = tmp_path / "source.txt"
    source.write_text("source")
    for paths in ([], [source] * 16, [source, source]):
        with pytest.raises(ValueError):
            F.snapshot_sources(paths)
    source.write_bytes(b"x" * (F.MAX_SOURCE_BYTES + 1))
    with pytest.raises(ValueError, match="bytes"):
        F.snapshot_sources([source])


def test_cli_preflight_new_receipt_and_no_ledger_writes(flow, tmp_path, monkeypatch, capsys):
    cfg, paths, calls, _, _ = flow
    prompt, receipt = tmp_path / "request.txt", tmp_path / "receipt.json"
    prompt.write_text("Compare")
    real_run = F.run_documents
    monkeypatch.setattr(F, "run_documents", lambda *a, **kw: real_run(*a, **kw, load_config=lambda: cfg))
    args = ["--prompt-file", str(prompt), "--source-file", str(paths[0]), "--host", "codex", "--dry-run", "--receipt", str(receipt)]
    assert F.main(args) == 0
    assert json.loads(receipt.read_text())["status"] == "planned" and not calls
    with pytest.raises(SystemExit):
        F.main(args)

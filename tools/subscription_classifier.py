"""One low-effort classifier on the current native ChatGPT account, no task execution."""
from __future__ import annotations

import json

import decide as C
import native_codex as N
import route_request as R


def classify(request, context, *, cfg, load_config, timeout, question_scope, host, binary):
    """R-compatible result. Host/model permission and fresh auth stay fail-closed."""
    route = {"result": "not_started", "started": False, "purpose": "routing_classifier",
             "transport": "native-subscription", "requested": {"host": host, "effort": "low"}}
    unavailable = {"status": "unavailable", "labels": None, "route": route}
    if host not in ("codex", "orca"):
        return {**unavailable, "reason": "Claude subscription classifier requires a verified logged-in subscription adapter"}
    if question_scope not in ("all", "native"):
        return {**unavailable, "reason": "Unknown classifier question scope"}
    if load_config() != cfg:
        return {**unavailable, "reason": "Classifier configuration changed"}
    selection = N.plan("T1", cfg, effort="low")
    if selection["status"] != "selected":
        return {**unavailable, "reason": selection.get("reason", "T1 classifier is not approved")}
    route["requested"].update(model=selection["model"])
    qs = R.questions(cfg)
    if question_scope == "native":
        qs = {key: qs[key] for key in ("tier", "effort")}
    state = "REQUEST (DATA):\n" + request + "\nCONTEXT (DATA):\n" + context
    prompt = C.EMULATION_PROMPT.replace("{questions}", json.dumps(qs, ensure_ascii=False)).replace("{state}", state)
    # Native transport disables tools/hooks/agents and checks account/read before inference.
    receipt, text = N.execute(binary, prompt, selection, load_config=load_config,
                              timeout=timeout, require_subscription=True)
    route.update(result=receipt["status"], started=receipt.get("transmitted_model") is not None,
                 native_receipt=receipt)
    report = {"status": "needs_review", "labels": None, "route": route,
              "method": "labels_with_quotes_not_calibrated_probabilities"}
    if receipt["status"] != "succeeded" or load_config() != cfg:
        return {**report, "reason": "Subscription classifier unavailable or configuration changed"}
    raw = C.parse_answers(text).get("answers", {})
    if not all(isinstance(raw.get(key), dict) and "type" not in raw[key]
               and type(raw[key].get("sure")) is bool for key in qs):
        return {**report, "reason": "Invalid subscription classifier labels"}
    response = {"answers": C.from_labels(raw, qs, state)}
    labels = R.classified(response, qs, .85)
    return {**report, "status": "accepted" if labels else "needs_review", "labels": labels,
            "response": response, "threshold": .85,
            "reason": None if labels else "Uncertain subscription classifier; no inferred default"}

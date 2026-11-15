#!/usr/bin/env python3
"""One explicit request boundary, not a GUI hook. No retry or paid fallback."""
from __future__ import annotations

import argparse
import copy
import json
import math
from pathlib import Path

import decide as C
import delegate as D
import ledger as L
import routing_policy as P

ADAPTERS = {"codex", "claude", "claude-code", "opencode"}
TASKS = {"T1": "extract-claims", "T2": "problem-framing", "T3": "alternatives"}


def questions():
    # Extend the existing taxonomy; never let a model choose an endpoint or grant.
    result = C.load_questions(L.ROOT / "knowledge/decisions/route_tier.yaml")
    result = copy.deepcopy(result)
    result["tier"]["instructions"] += " Учитывай весь запрос и контекст: зависимости, неопределённость, противоречия и цену ошибки, а не длину."
    result["effort"] = {"type": "choice", "instructions": "Какая глубина нужна внутри выбранного уровня?",
                        "criteria": {"normal": "обычная задача уровня", "deep": "сложные зависимости или противоречия требуют углублённой проверки"}}
    result["capability"] = {"type": "choice", "instructions": "Достаточно ли текстового ответа по переданным данным?",
                            "criteria": {"text": "ответ по предоставленному тексту", "tools": "нужны файлы, команды, внешние данные, изображения или изменение состояния"}}
    return result


def route_plan(cfg, host, tier, prompt, deep=False, bootstrap=False):
    task = "classify" if bootstrap else TASKS[tier]
    requested_effort = "low" if bootstrap else "high" if deep and host == "opencode" else None
    if host == "opencode":
        import opencode_free as O
        error = O.platform_error()
        if error:
            receipt = P.plan(cfg, host=host, provider=D.HOST_PROVIDER[host], model=None,
                             effort=requested_effort, tier=tier, input_bytes=len(prompt.encode("utf-8")))
            receipt.update(status="manual", reason=error, tier=tier)
            return receipt
    family, model, effort, error = D.select_route(cfg, host, task, tier, explicit_effort=requested_effort)
    if bootstrap:
        # Fixed T1 route, never classified by this module. No escalation ladder.
        effort = "low"
    elif deep and host != "opencode":
        ladder = L.tier_efforts(cfg, tier, family, model)
        effort = ladder[-1] if ladder else effort
    receipt = P.plan(cfg, host=host, provider=D.HOST_PROVIDER.get(host), model=model,
                     effort=effort, tier=tier, input_bytes=len(prompt.encode("utf-8")))
    receipt["tier"] = tier
    if error:
        receipt.update(status="denied", reason=error)
    if host not in ADAPTERS and receipt["status"] == "executable":
        receipt.update(status="manual", reason="Execution adapter not implemented")
    return receipt


def classified(data, qs, threshold):
    if not C.valid_response(data, qs):
        return None
    result = {}
    for key, question in qs.items():
        answer = data["answers"][key]
        confidence = answer.get("confidence")
        choice = answer.get("choice")
        if (answer.get("type") != "choice" or not isinstance(choice, str)
                or choice not in question["criteria"] or type(confidence) not in (int, float)
                or not math.isfinite(confidence) or not threshold <= confidence <= 1):
            return None
        result[key] = choice
    return result


def run_request(request, context, hosts, *, execute=False, load_config=L.load_routing, timeout=600):
    """Trusted caller supplies hosts/config; untrusted classifier supplies only labels.

    execute=False performs no classification inference. execute=True authorizes at
    most one classifier invocation and one task invocation through existing gates.
    The returned object retains the original request and separate stage receipts.
    """
    if not isinstance(request, str) or not request.strip() or not isinstance(context, str):
        raise ValueError("Nonempty request and text context required")
    if not hosts or any(h not in D.BACKEND_FAMILY for h in hosts):
        raise ValueError("Explicit known hosts required")
    cfg = load_config()
    state = json.dumps({"request": request, "context": context}, ensure_ascii=False)
    qs = questions()
    prompt = C.EMULATION_PROMPT.replace("{questions}", json.dumps(qs, ensure_ascii=False)).replace("{state}", state)
    options = cfg.get("automatic_routing") or {}
    provider_name = options.get("decision_provider")
    provider = C.pick_provider(cfg, provider_name) if provider_name else None
    bootstrap_host = options.get("bootstrap_host", "codex")
    report = {"schema": 1, "request": request, "context": context, "hosts": hosts,
              "classification": {}, "plan": None, "start": None,
              "result": {"status": "not_started", "text": None}, "status": "manual"}
    if provider_name:
        if not provider:
            report["classification"] = {"status": "manual", "reason": "Explicit decision provider unavailable; no fallback"}
            return report
        body = {"model": provider["model"], "state": state, "questions": qs}
        receipt = C.decision_plan(cfg, provider, body)
    else:
        receipt = route_plan(cfg, bootstrap_host, "T1", prompt, bootstrap=True)
    report["classification"] = {"route": receipt, "status": "pending"}
    if receipt["status"] != "executable":
        report["classification"].update(status="unavailable", reason=receipt["reason"])
        return report
    if not execute:
        report["status"] = "awaiting_classification"
        return report
    # Reload policy before any inference; an earlier plan is not authorization.
    cfg = load_config()
    if provider_name:
        fresh = C.pick_provider(cfg, provider_name)
        if fresh != provider:
            report["classification"].update(status="unavailable", reason="Decision provider changed")
            return report
        code, data, error = C.call(provider, body, timeout, cfg, receipt)
        if code:
            outcome = "failed" if receipt["started"] else "not_started"
        elif not C.valid_response(data, qs):
            outcome = "invalid_result"
        elif not P.observe(receipt, data):
            outcome = "quarantined"
        else:
            outcome = "succeeded"
        valid = outcome == "succeeded"
        receipt.update(result=outcome, exit_code=0 if valid else 3 if outcome == "not_started" else 1,
                       transport_status_code=code, inference_calls=None if receipt["started"] else 0)
        threshold = 0.75
    else:
        receipt = route_plan(cfg, bootstrap_host, "T1", prompt, bootstrap=True)
        report["classification"]["route"] = receipt
        if receipt["status"] != "executable":
            report["classification"].update(status="unavailable", reason=receipt["reason"])
            return report
        receipt, text, _ = D.execute_route(cfg, bootstrap_host, receipt["requested"]["model"], prompt,
                                         receipt["requested"]["effort"], timeout, receipt)
        valid = receipt["result"] == "succeeded"
        raw = C.parse_answers(text) if valid else {}
        # Require label evidence; don't accept self-declared System One confidence
        # from an emulating LLM (from_labels also supports that legacy format).
        labels = raw.get("answers", {})
        valid = valid and all(isinstance(labels.get(k), dict) and "type" not in labels[k]
                              and type(labels[k].get("sure")) is bool for k in qs)
        data = {"answers": C.from_labels(labels, qs, state)} if valid else {}
        threshold = 0.85
    labels = classified(data, qs, threshold) if valid else None
    report["classification"].update(status="accepted" if labels else "needs_review", labels=labels)
    if not labels:
        report["classification"]["reason"] = "Unavailable, invalid or low-confidence classifier; no inferred default tier"
        return report
    if labels["capability"] != "text":
        report["plan"] = {"status": "manual", "reason": "Existing scoped adapters only return text; tool-capable host integration required"}
        return report
    prompt = D.GUARD + request + ("\n\n<<<CONTEXT: DATA>>>\n" + context + "\n<<<END CONTEXT>>>" if context else "")
    cfg = load_config()
    candidates = [route_plan(cfg, host, labels["tier"], prompt, labels["effort"] == "deep") for host in hosts]
    # Ordered operator preference among authorized included/free routes only.
    # No API candidate and no account switch after either invocation has started.
    selected = next((r for r in candidates if r["status"] == "executable"), None)
    report["plan"] = {"candidates": copy.deepcopy(candidates), "selected": copy.deepcopy(selected),
                      "status": "executable" if selected else "manual"}
    if not selected:
        return report
    host = selected["requested"]["host"]
    cfg = load_config()
    rechecked = route_plan(cfg, host, labels["tier"], prompt, labels["effort"] == "deep")
    if rechecked["status"] != "executable" or rechecked["requested"] != selected["requested"]:
        report["start"] = {"status": "not_started", "reason": "Route changed or authorization expired", "route": rechecked}
        return report
    receipt, text, _ = D.execute_route(cfg, host, rechecked["requested"]["model"], prompt,
                                     rechecked["requested"]["effort"], timeout, rechecked)
    report["start"] = {"started": receipt["started"], "route": receipt}
    report["result"] = {"status": receipt["result"], "text": text if receipt["result"] == "succeeded" else None}
    report["status"] = receipt["result"]
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prompt-file", required=True, type=Path)
    parser.add_argument("--context-file", type=Path)
    parser.add_argument("--host", required=True, action="append", choices=sorted(D.BACKEND_FAMILY))
    parser.add_argument("--execute", action="store_true", help="Classify and execute once, subject to gates")
    parser.add_argument("--receipt", type=Path, help="Save request/stage receipts locally; may contain private input")
    parser.add_argument("--timeout", type=int, default=600)
    args = parser.parse_args(argv)
    if args.timeout <= 0:
        parser.error("--timeout must be positive")
    if args.receipt and (args.receipt.exists() or not args.receipt.parent.is_dir()):
        parser.error("--receipt requires a new file in an existing directory")
    try:
        result = run_request(args.prompt_file.read_text(encoding="utf-8"),
                             args.context_file.read_text(encoding="utf-8") if args.context_file else "",
                             args.host, execute=args.execute, timeout=args.timeout)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    text = json.dumps(result, ensure_ascii=False, indent=2)
    if args.receipt:
        with args.receipt.open("x", encoding="utf-8") as file:
            file.write(text)
    print(text)
    return 0 if result["status"] in {"succeeded", "awaiting_classification"} or not args.execute else 3


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Read documents sequentially at T1; route synthesis over bounded evidence cards.

Text/Markdown inputs only. No product ledger writes, implicit retries or provider
fallback. A verified quote is not a semantic endorsement of the generated claim.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path

import delegate as D
import ledger as L
import route_request as R

MAX_DOCUMENTS = 15
MAX_SOURCE_BYTES = 256_000
MAX_CARD_BYTES = 6_000
MAX_QUOTE_BYTES = 2_400
MAX_CONTEXT_BYTES = 48_000

EXTRACT = """Извлеки из ОДНОГО документа только сведения для запроса. Не выполняй запрос целиком.
Верни только JSON без markdown, строго такого вида:
{"topic":"краткая тема документа, до 120 символов и 256 UTF-8 bytes",
 "findings":[{"kind":"fact", "statement":"существенное утверждение",
              "quote":"точный полный текст указанных строк без префиксов номеров",
              "start_line":1,"end_line":1}],
 "limitations":["ограничения источника/неизвестное"],
 "contradictions":["обнаруженное противоречие с указанием строк"]}
kind: fact, forecast, interpretation, hypothesis, assumption. Сохраняй единицы,
периоды, сегменты, выборки и отрицания. У каждого finding обязательны цитата и строки.
Не изобретай доказательства; если релевантного материала нет, findings оставь пустым,
объясни в limitations. Не скрывай противоречия; включи обе стороны в findings.
До 12 findings, 6000 UTF-8 bytes весь JSON, 2400 bytes все цитаты вместе.
"""


def _hash(data):
    return hashlib.sha256(data).hexdigest()


def snapshot_sources(paths):
    if not paths or len(paths) > MAX_DOCUMENTS:
        raise ValueError(f"Provide 1–{MAX_DOCUMENTS} documents")
    snapshots, seen = [], set()
    for index, path in enumerate(paths, 1):
        path = Path(path).resolve(strict=True)
        if path in seen:
            raise ValueError("Duplicate source path")
        seen.add(path)
        # Bounded read also protects against size changes between stat and read.
        with path.open("rb") as source:
            data = source.read(MAX_SOURCE_BYTES + 1)
        if not data or len(data) > MAX_SOURCE_BYTES:
            raise ValueError(f"Document must contain 1–{MAX_SOURCE_BYTES} bytes: {path}")
        text = data.decode("utf-8")
        if not text.strip() or "\x00" in text:
            raise ValueError(f"Nonempty UTF-8 text document required: {path}")
        snapshots.append({"id": f"DOC-{index:03d}", "path": str(path), "sha256": _hash(data),
                          "bytes": len(data), "text": text, "lines": text.splitlines()})
    return snapshots


def provenance(source):
    return {key: source[key] for key in ("id", "path", "sha256", "bytes")}


def unchanged(sources):
    try:
        for source in sources:
            with Path(source["path"]).open("rb") as stream:
                data = stream.read(MAX_SOURCE_BYTES + 1)
            if _hash(data) != source["sha256"]:
                return False
    except OSError:
        return False
    return True


def extraction_prompt(request, source):
    numbered = "\n".join(f"{index}: {line}" for index, line in enumerate(source["lines"], 1))
    return D.GUARD + EXTRACT + "\n" + json.dumps(
        {"request": request, "source": provenance(source), "numbered_document": numbered}, ensure_ascii=False)


def validate_card(text, source):
    """Validate shape and exact line evidence, never infer support from a paraphrase."""
    if not isinstance(text, str) or len(text.encode("utf-8")) > MAX_CARD_BYTES:
        raise ValueError("Evidence card exceeds byte budget")
    card = json.loads(text)
    if not isinstance(card, dict) or set(card) != {"topic", "findings", "limitations", "contradictions"}:
        raise ValueError("Invalid evidence card fields")
    topic = card["topic"]
    if not isinstance(topic, str) or not topic.strip() or len(topic) > 120 or len(topic.encode("utf-8")) > 256:
        raise ValueError("Missing or overlong document topic")
    for key in ("limitations", "contradictions"):
        if not isinstance(card[key], list) or len(card[key]) > 12 or any(
                not isinstance(item, str) or not item.strip() for item in card[key]):
            raise ValueError(f"Invalid {key}")
    findings = card["findings"]
    if not isinstance(findings, list) or len(findings) > 12:
        raise ValueError("Invalid findings or too many findings; synthesis not started")
    if not findings and not card["limitations"]:
        raise ValueError("Empty findings require an explanation in limitations")
    quote_bytes = 0
    for finding in findings:
        if not isinstance(finding, dict) or set(finding) != {"kind", "statement", "quote", "start_line", "end_line"}:
            raise ValueError("Invalid finding fields")
        if finding["kind"] not in ("fact", "forecast", "interpretation", "hypothesis", "assumption"):
            raise ValueError("Unknown finding kind")
        if not isinstance(finding["statement"], str) or not finding["statement"].strip():
            raise ValueError("Empty finding")
        start, end, quote = finding["start_line"], finding["end_line"], finding["quote"]
        if type(start) is not int or type(end) is not int or not 1 <= start <= end <= len(source["lines"]):
            raise ValueError("Invalid line locator")
        if not isinstance(quote, str) or not quote.strip() or quote != "\n".join(source["lines"][start - 1:end]):
            raise ValueError("Quote does not match the exact source line range")
        quote_bytes += len(quote.encode("utf-8"))
    if quote_bytes > MAX_QUOTE_BYTES:
        raise ValueError("Evidence quotes exceed byte budget")
    return {"source": provenance(source), **card,
            "verification": "exact_quotes_and_locators_only; semantic_support_not_verified"}


def routing_synopsis(cards):
    # Explicit metadata projection of ALL documents, not silent text truncation.
    # The task still receives complete accepted cards. D1 can refuse long requests.
    return json.dumps({"scope": "routing synopsis only; synthesis receives complete evidence cards",
                       "documents": [{"id": c["source"]["id"], "topic": c["topic"],
                                      "findings": len(c["findings"]), "limitations": len(c["limitations"]),
                                      "contradictions": len(c["contradictions"])} for c in cards]}, ensure_ascii=False)


def native_plan(cfg, tier, effort=None):
    """Explicit native lane; never relax the isolated delegate's profile policy."""
    import native_codex as N
    selection = N.plan(tier, cfg, effort=effort)
    return {"status": "executable" if selection["status"] == "selected" else "manual",
            "reason": selection.get("reason"), "tier": tier, "transport": "native-codex",
            "selection": selection, "requested": {"host": "codex", "model": selection.get("model"),
                                                   "effort": selection.get("effort")}}


def native_execute(binary, prompt, route, load_config, timeout):
    import native_codex as N
    receipt, text = N.execute(binary, prompt, route["selection"], load_config=load_config, timeout=timeout)
    return {**route, "result": receipt["status"], "started": receipt.get("transmitted_model") is not None,
            "native_receipt": receipt}, text, receipt.get("usage_events", [])


def native_classification_review(selection, request, synopsis, binary, load_config, timeout):
    """One T1 review of uncertain valid D1 labels; never a transport fallback."""
    import decide as C
    classification = selection.get('classification') or {}
    cfg = load_config()
    qs = R.questions(cfg)
    if (selection.get('status') != 'needs_review'
            or (classification.get('route') or {}).get('result') != 'succeeded'
            or not C.valid_response(classification.get('response'), qs)):
        return selection
    if R.classified(classification['response'], {k: qs[k] for k in ('tier', 'effort')}, .75):
        # A confident selection rejected by policy/quota is not uncertainty.
        return selection
    return native_luna_classify(request, synopsis, binary, load_config, timeout,
                                selection=selection, review_key='t1_review')


def native_luna_classify(request, synopsis, binary, load_config, timeout, *,
                         selection=None, review_key='luna_classification'):
    """One explicit Luna classifier; never invokes D1 or changes host grants."""
    import decide as C
    cfg = load_config()
    qs = R.questions(cfg)
    selection = selection or {'status': 'needs_review', 'host': 'codex'}
    plan = native_plan(cfg, 'T1', 'low')
    plan['purpose'] = 'routing_classifier'
    if plan['status'] != 'executable':
        return selection
    # Synopsis is already JSON. Do not double-escape its quotes: the reviewer
    # must be able to copy evidence literally from the displayed state.
    state = 'REQUEST (DATA):\n' + request + '\nROUTING SYNOPSIS (DATA):\n' + synopsis
    prompt = C.EMULATION_PROMPT.replace('{questions}', json.dumps(qs, ensure_ascii=False)).replace('{state}', state)
    receipt, text, _ = native_execute(binary, prompt, plan, load_config, timeout)
    report = {**selection, review_key: {'route': receipt, 'method': 'labels_with_quote_not_calibrated_probability'}}
    if receipt['result'] != 'succeeded' or load_config() != cfg:
        return report
    raw = C.parse_answers(text).get('answers', {})
    report[review_key]['answers'] = raw
    if not all(isinstance(raw.get(k), dict) and 'type' not in raw[k]
               and type(raw[k].get('sure')) is bool for k in qs):
        return report
    labels = R.classified({'answers': C.from_labels(raw, qs, state)}, qs, .85)
    report[review_key]['labels'] = labels
    if not labels or labels['capability'] != 'text' or labels['tier'] == 'T4':
        return report
    tier = labels['tier']
    model = L.model_for(cfg, 'openai', tier)
    ladder = L.tier_efforts(cfg, tier, 'openai', model)
    route = native_plan(cfg, tier, ladder[-1 if labels['effort'] == 'deep' else 0] if ladder else None)
    if route['status'] != 'executable':
        return report
    return {**report, **route['selection'], 'reason': 'Luna classifier selected route with source quotes'}


def native_synthesis(request, context, synopsis, binary, load_config, timeout, *,
                     routing_mode='d1_luna', baseline=None):
    import host_routing as H
    import adaptive_execution as A
    import calculation_checks as calculations
    numerical_input = calculations.check(context, {})
    if numerical_input['status'] == 'invalid':
        return {'status': 'needs_data', 'routing_mode': routing_mode,
                'reason': 'Invalid calculation source contract', 'calculation_check': numerical_input,
                'result': {'status': 'not_started', 'text': None}}
    if routing_mode not in ('d1', 'd1_luna', 'luna', 'none'):
        raise ValueError('Unknown routing mode')
    if routing_mode == 'none':
        if not isinstance(baseline, dict) or set(baseline) != {'tier', 'effort'}:
            raise ValueError('No-classifier mode requires explicit baseline tier and effort')
        selection = native_plan(load_config(), baseline['tier'], baseline['effort'])['selection']
    elif routing_mode == 'luna':
        selection = native_luna_classify(request, synopsis, binary, load_config, timeout)
    else:
        selection = H.decide({"host": "codex", "request": request, "context": synopsis},
                             execute=True, load_config=load_config)
        if routing_mode == 'd1_luna':
            selection = native_classification_review(selection, request, synopsis, binary, load_config, timeout)
    report = {"status": "manual", "classification": selection,
              "result": {"status": "not_started", "text": None}, "transport": "native-codex",
              "routing_mode": routing_mode}
    if selection.get("status") != "selected":
        return report
    cfg = load_config()
    route = native_plan(cfg, selection["tier"], selection["effort"])
    route['purpose'] = 'task'
    report["plan"] = route
    if route["status"] != "executable" or route["requested"]["model"] != selection["model"]:
        return report
    quality = (cfg.get('automatic_routing') or {}).get('quality') or {}
    if numerical_input.get('checks') and quality.get('enabled') is not True:
        report['reason'] = 'Declared calculation checks require the quality execution gate'
        return report
    if quality.get('enabled') is True:
        verifier = native_plan(cfg, 'T1')
        if verifier['status'] != 'executable':
            report['reason'] = 'Native verifier unavailable before synthesis'
            return report
        def assess(request, context, answer, cfg, timeout):
            state, prompt = A.verifier_prompt(request, context, answer)
            plan = native_plan(cfg, 'T1')
            plan['purpose'] = 'quality_verifier'
            if plan['status'] != 'executable':
                return {'status': 'unavailable', 'route': plan}
            receipt, text, _ = native_execute(binary, prompt, plan, load_config, timeout)
            if receipt['result'] != 'succeeded':
                return {'status': 'unavailable', 'route': receipt}
            return A.parse_quality_answer(state, text, receipt)
        def planner(cfg, **kwargs):
            plan = native_plan(cfg, kwargs['tier'], kwargs['effort'])
            plan['purpose'] = 'task'
            if plan['requested']['model'] != kwargs['model']:
                plan.update(status='manual', reason='Native model changed during escalation')
            return plan
        def executor(cfg, host, model, prompt, effort, timeout, plan):
            return native_execute(binary, prompt, plan, load_config, timeout)
        result = A.execute(request, context, route, load_config=load_config, timeout=timeout,
                           assessor=assess, route_planner=planner, route_executor=executor)
        report.update(status=result['status'], result=result)
    else:
        prompt = D.GUARD + request + '\n\n<<<CONTEXT: DATA>>>\n' + context + '\n<<<END CONTEXT>>>'
        receipt, text, _ = native_execute(binary, prompt, route, load_config, timeout)
        report.update(status=receipt['result'], start=receipt,
                      result={'status': receipt['result'], 'text': text if receipt['result'] == 'succeeded' else None})
    return report


def run_documents(request, paths, hosts, *, execute=False, load_config=L.load_routing, timeout=600,
                  native_binary=None):
    if not isinstance(request, str) or not request.strip():
        raise ValueError("Nonempty request required")
    if not hosts or any(h not in D.BACKEND_FAMILY for h in hosts):
        raise ValueError("Explicit known hosts required")
    if type(timeout) is not int or timeout <= 0:
        raise ValueError("Positive timeout required")
    if native_binary is not None:
        if hosts != ['codex'] or not Path(native_binary).is_absolute() or not Path(native_binary).is_file():
            raise ValueError('Native transport requires --host codex and an absolute existing binary')
    sources = snapshot_sources(paths)
    report = {"schema": 1, "request": request, "status": "planned", "sources": [provenance(s) for s in sources],
              "extractions": [], "cards": [], "synthesis": None, "result": {"status": "not_started", "text": None},
              "transport": "native-codex" if native_binary is not None else "isolated-delegate",
              "limits": {"documents": MAX_DOCUMENTS, "card_bytes": MAX_CARD_BYTES,
                         "quote_bytes": MAX_QUOTE_BYTES, "context_bytes": MAX_CONTEXT_BYTES}}
    host = None
    for source in sources:
        if not unchanged(sources):
            report.update(status="stale_input", reason="Source changed since snapshot; no accepted synthesis")
            return report
        prompt = extraction_prompt(request, source)
        cfg = load_config()
        candidates = ([native_plan(cfg, 'T1')] if native_binary is not None else
                      [R.route_plan(cfg, h, "T1", prompt) for h in ([host] if host else hosts)])
        selected = next((r for r in candidates if r["status"] == "executable"), None)
        stage = {"source_id": source["id"], "candidates": copy.deepcopy(candidates),
                 "plan": copy.deepcopy(selected), "status": "planned", "start": None}
        report["extractions"].append(stage)
        if not selected:
            stage["status"] = "unavailable"
            report.update(status="manual", reason="No authorized T1 extraction route")
            return report
        host = selected["requested"]["host"]
        if not execute:
            continue
        fresh = (native_plan(load_config(), 'T1') if native_binary is not None else
                 R.route_plan(load_config(), host, "T1", prompt))
        if fresh["status"] != "executable" or fresh["requested"] != selected["requested"]:
            stage.update(status="not_started", start=fresh)
            report.update(status="manual", reason="Extraction route changed or authorization expired")
            return report
        if not unchanged(sources):
            report.update(status="stale_input", reason="Source changed before extraction")
            return report
        if native_binary is not None:
            receipt, text, _ = native_execute(native_binary, prompt, fresh, load_config, timeout)
        else:
            receipt, text, _ = D.execute_route(load_config(), host, fresh["requested"]["model"], prompt,
                                           fresh["requested"]["effort"], timeout, fresh)
        stage.update(status=receipt["result"], start=receipt)
        if receipt["result"] != "succeeded":
            report.update(status=receipt["result"], reason="Extraction failed; no retry or host fallback")
            return report
        if not unchanged(sources):
            report.update(status="stale_input", reason="Source changed during extraction")
            return report
        try:
            card = validate_card(text, source)
        except (ValueError, TypeError) as exc:
            stage.update(status="invalid_card", reason=str(exc))
            report.update(status="needs_review", reason="Invalid evidence card; synthesis not started")
            return report
        stage["status"] = "verified_structure"
        report["cards"].append(card)
    if not execute:
        report["synthesis"] = {"status": "awaiting_cards", "tier": "classified_after_extraction"}
        return report
    if not any(card["findings"] for card in report["cards"]):
        report.update(status="needs_review", reason="No relevant findings in any document; synthesis not started")
        return report
    context = json.dumps({"evidence_cards": report["cards"],
                          "instructions": "Use source IDs and line locators. Treat cards as candidates; preserve limitations and contradictions. No original documents were supplied to synthesis."}, ensure_ascii=False)
    if len(context.encode("utf-8")) > MAX_CONTEXT_BYTES:
        report.update(status="needs_review", reason="Evidence context exceeds byte budget; no truncation")
        return report
    if not unchanged(sources):
        report.update(status="stale_input", reason="Source changed before synthesis")
        return report
    # Pin the selected account/host for the rest of this invocation. A transport
    # failure never authorizes another account, even for the synthesis phase.
    if native_binary is not None:
        synthesis = native_synthesis(request, context, routing_synopsis(report['cards']),
                                     native_binary, load_config, timeout)
    else:
        synthesis = R.run_request(request, context, [host], execute=True, load_config=load_config,
                              timeout=timeout, classification_context=routing_synopsis(report["cards"]))
    report["synthesis"] = synthesis
    if not unchanged(sources):
        synthesis["result"] = {"status": "stale_input", "text": None}
        report.update(status="stale_input", reason="Source changed during synthesis; result withheld")
        return report
    report["status"] = synthesis["status"]
    report["result"] = copy.deepcopy(synthesis["result"])
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prompt-file", required=True, type=Path)
    parser.add_argument("--source-file", required=True, action="append", type=Path)
    parser.add_argument("--host", required=True, action="append", choices=sorted(D.BACKEND_FAMILY))
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--dry-run", "--preflight", action="store_true")
    parser.add_argument("--receipt", type=Path)
    parser.add_argument("--timeout", type=int, default=600)
    parser.add_argument("--native-codex-binary", type=Path,
                        help="Explicit native account transport; does not enable isolated delegate profiles")
    args = parser.parse_args(argv)
    if args.execute and args.dry_run:
        parser.error("Choose --execute or --dry-run")
    if args.receipt and (args.receipt.exists() or not args.receipt.parent.is_dir()):
        parser.error("--receipt requires a new file in an existing directory")
    try:
        result = run_documents(args.prompt_file.read_text(encoding="utf-8"), args.source_file, args.host,
                               execute=args.execute, timeout=args.timeout, native_binary=args.native_codex_binary)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    text = json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False)
    if args.receipt:
        with args.receipt.open("x", encoding="utf-8") as file:
            file.write(text + "\n")
    print(text)
    return 0 if not args.execute or result["status"] == "succeeded" else 3


if __name__ == "__main__":
    raise SystemExit(main())

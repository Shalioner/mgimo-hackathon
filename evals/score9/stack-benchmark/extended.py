#!/usr/bin/env python3
"""Local D1 and offline document-flow measurements on synthetic inputs."""

import json
from pathlib import Path
import sys
import tempfile
import time
from unittest.mock import patch
from uuid import uuid4


sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "tools"))
import d1_service
import document_flow


def d1():
    status = d1_service.request(d1_service.DEFAULT_SOCKET, {"op": "status"}, timeout=3)
    payload = {
        "op": "infer", "timeout": 20,
        "state": "Учебный запрос: проверить существующий реестр без изменений.",
        "questions": {"tier": {
            "type": "choice", "instructions": "Выбери подходящий уровень.",
            "criteria": {"T0": "Детерминированная проверка реестра", "T3": "Пересмотр стратегии"},
        }},
    }
    rows = []
    for arm, count in (("first_no_cache", 1), ("warm_no_cache", 5), ("seed_cache", 1), ("repeat_exact", 5)):
        for _ in range(count):
            reply = d1_service.request(d1_service.DEFAULT_SOCKET, {
                **payload, "id": uuid4().hex, "reuse": arm in {"seed_cache", "repeat_exact"},
            }, timeout=25)
            runtime = reply.get("runtime", {})
            rows.append({"arm": arm, "status": reply.get("status"), "reason": reply.get("reason"),
                         "reuse": runtime.get("reuse"), "inference_calls": runtime.get("inference_calls"),
                         "timing": runtime.get("timing"), "response": reply.get("response")})
            if reply.get("status") != "succeeded":
                return {"initial_state": status.get("model_state"), "runs": rows}
    return {"initial_state": status.get("model_state"), "runs": rows}


def document_run(paths, fail_at=None, saved_cards=None):
    extraction_bytes = []
    synthesis_bytes = []
    reused = 0

    def extract(cfg, host, model, prompt, effort, timeout, plan):
        nonlocal reused
        key = (host, model, effort, prompt)
        if saved_cards is not None and key in saved_cards:
            reused += 1
            return {"result": "succeeded"}, saved_cards[key], None
        extraction_bytes.append(len(prompt.encode()))
        if len(extraction_bytes) == fail_at:
            return {"result": "failed"}, None, None
        payload = json.loads(prompt.rsplit("\n", 1)[-1])
        quote = payload["numbered_document"].splitlines()[0].split(": ", 1)[1]
        card = {"topic": "Учебная проверка", "findings": [
            {"kind": "fact", "statement": quote, "quote": quote, "start_line": 1, "end_line": 1},
        ], "limitations": [], "contradictions": []}
        payload = json.dumps(card, ensure_ascii=False)
        if saved_cards is not None:
            saved_cards[key] = payload
        return {"result": "succeeded"}, payload, None

    def synthesize(request, context, hosts, **kwargs):
        synthesis_bytes.append(len(context.encode()))
        return {"status": "succeeded", "result": {"status": "succeeded", "text": "Учебный итог"}}

    plan = {"status": "executable", "requested": {"host": "codex", "model": "stub", "effort": "low"}}
    with patch.object(document_flow.R, "route_plan", return_value=plan), \
            patch.object(document_flow.D, "execute_route", side_effect=extract), \
            patch.object(document_flow.R, "run_request", side_effect=synthesize):
        start = time.perf_counter()
        report = document_flow.run_documents("Сопоставь учебные данные", paths, ["codex"],
                                             execute=True, load_config=dict)
        elapsed = time.perf_counter() - start
    return {"status": report["status"], "validated_cards": len(report["cards"]), "seconds": elapsed,
            "extraction_calls": len(extraction_bytes), "extraction_bytes": sum(extraction_bytes),
            "synthesis_calls": len(synthesis_bytes), "synthesis_context_bytes": sum(synthesis_bytes),
            "reused": reused}


def documents():
    with tempfile.TemporaryDirectory(prefix="strategist-document-benchmark-") as temp:
        paths = [Path(temp) / f"{i}.md" for i in range(1, 9)]
        for i, path in enumerate(paths, 1):
            path.write_text(f"Учебный факт {i}.\n" + "Пример нейтрального контекста.\n" * 80, encoding="utf-8")
        series = {}
        for count in (1, 2, 4, 8):
            document_run(paths[:count])
            samples = [document_run(paths[:count]) for _ in range(5)]
            if not all(x["status"] == "succeeded" and x["validated_cards"] == x["extraction_calls"] == count
                       and x["synthesis_calls"] == 1 for x in samples):
                raise ValueError("Document flow did not complete the expected work")
            series[count] = samples
        failed = document_run(paths[:4], fail_at=3)
        retry = document_run(paths[:4])
        if (failed["status"], failed["validated_cards"], failed["extraction_calls"],
            retry["status"], retry["validated_cards"], retry["extraction_calls"]) != ("failed", 2, 3, "succeeded", 4, 4):
            raise ValueError("Unexpected failure and retry sequence")
        return {"transport": "stub", "series": series, "failure": failed, "retry": retry}


def recovery_probe():
    """Simulate reuse at the transport boundary, keeping validation in document_flow."""
    with tempfile.TemporaryDirectory(prefix="strategist-document-recovery-") as temp:
        paths = [Path(temp) / f"{i}.md" for i in range(1, 5)]
        for index, path in enumerate(paths, 1):
            path.write_text(f"Учебный факт {index}.\n", encoding="utf-8")
        saved = {}
        interrupted = document_run(paths, fail_at=3, saved_cards=saved)
        resumed = document_run(paths, saved_cards=saved.copy())
        original = paths[0].read_bytes()
        paths[0].write_bytes(original + "Текст источника изменился.\n".encode("utf-8"))
        changed = document_run(paths, saved_cards=saved.copy())
        paths[0].write_bytes(original)
        with patch.object(document_flow, "EXTRACT", document_flow.EXTRACT + "\nСмена инструкции."):
            changed_instruction = document_run(paths, saved_cards=saved.copy())
        assert interrupted["status"] == "failed" and len(saved) == 2
        assert resumed["status"] == "succeeded" and resumed["validated_cards"] == 4
        assert resumed["extraction_calls"] == 2 and resumed["reused"] == 2
        assert changed["status"] == "succeeded" and changed["extraction_calls"] == 3 and changed["reused"] == 1
        assert changed_instruction["status"] == "succeeded" and changed_instruction["reused"] == 0
        return {"transport": "stub", "interrupted": interrupted, "resumed": resumed,
                "changed_source": changed, "changed_instruction": changed_instruction}


if __name__ == "__main__":
    result = {"documents": documents(), "recovery_probe": recovery_probe()}
    try:
        result["d1"] = d1()
    except (OSError, ValueError) as exc:
        result["d1"] = {"error": str(exc)}
    print(json.dumps(result, ensure_ascii=False, indent=2))

#!/usr/bin/env python3
"""Opt-in synthetic probe; exact free model, zero account-key cap, no executor."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import time
import urllib.request
import urllib.error

import decide as D
import routing_policy as P

MODEL = "perplexity/pplx-decider-v1.3:free"
ENDPOINT = "https://openrouter.ai/api/alpha/decisions"
PROVIDER = {"name": "openrouter", "protocol": "openrouter-decisions",
            "base_url": "https://openrouter.ai/api/alpha", "model": MODEL,
            "provider_slug": "perplexity", "response_provider": "Perplexity",
            "api_key_env": "OPENROUTER_API_KEY"}


def configuration():
    return {"decision": {"providers": [dict(PROVIDER)]},
            "automatic_routing": {"classifier": "provider", "decision_provider": "openrouter"},
            "execution_policy": {"mode": "api_opt_in", "max_input_bytes": 32000,
                "api": {"enabled": True, "grants": [{
                    "host": "decision", "provider": "openrouter", "endpoint": ENDPOINT,
                    "models": [MODEL], "efforts": [None], "budget_usd": 0,
                    "external_cap": {"confirmed_by_operator": True, "amount_usd": 0,
                                     "evidence_ref": "runtime-verified-GET-/api/v1/key"},
                    "context": {"model_limit": 262144, "plan_limit": 32000,
                                "user_limit": 24000, "output_reserve": 1024, "system_reserve": 2048}
                }]}}}


def verify_key(timeout):
    req = urllib.request.Request("https://openrouter.ai/api/v1/key",
        headers={"Authorization": "Bearer " + os.environ["OPENROUTER_API_KEY"]})
    with P.http_open(req, timeout) as response:
        data = json.load(response)["data"]
    # Fail closed on unlimited, positive or malformed caps. Never print the key/label.
    if type(data.get("limit")) not in {int, float} or data["limit"] != 0:
        raise ValueError("A zero-dollar key limit is required")
    if data.get("is_free_tier") is not True:
        raise ValueError("A free-tier key is required")
    return {k: data.get(k) for k in ("limit", "limit_remaining", "usage", "expires_at", "is_free_tier")}




def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true", help="Explicitly send synthetic state to OpenRouter")
    parser.add_argument("--key-file", type=Path, help="Private key file; otherwise use OPENROUTER_API_KEY")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--timeout", type=int, default=30)
    args = parser.parse_args(argv)
    if not args.execute:
        print("No network call: use --execute for the synthetic free-model probe")
        return 0
    if args.output.exists():
        parser.error("output already exists; choose a fresh report path")
    previous = os.environ.get("OPENROUTER_API_KEY")
    result = {"timestamp": datetime.now(timezone.utc).isoformat(), "model": MODEL,
              "endpoint": ENDPOINT, "synthetic_only": True, "executor_calls": 0}
    try:
        if args.key_file:
            if args.key_file.stat().st_mode & 0o077:
                raise ValueError("Key file must have owner-only permissions")
            os.environ["OPENROUTER_API_KEY"] = args.key_file.read_text(encoding="utf-8").strip()
        if not os.environ.get("OPENROUTER_API_KEY"):
            raise ValueError("OPENROUTER_API_KEY is missing")
        result["key_cap"] = verify_key(args.timeout)
        body = {"model": MODEL, "state": "The ticket says: I was charged twice for one subscription.",
                "questions": {
                    "billing": {"type": "noul", "instructions": "Is this a billing issue?"},
                    "team": {"type": "choice", "instructions": "Which team owns this issue?",
                             "criteria": {"billing": "Charges and payments", "technical": "Software failures"}},
                    "urgency": {"type": "score", "instructions": "Rate urgency.",
                                "criteria": ["No issue", "Individual customer blocked", "Global outage"]}}}
        receipt = D.decision_plan(configuration(), PROVIDER, body)
        start = time.perf_counter()
        code, data, error = D.call(PROVIDER, body, args.timeout, cfg=configuration(), receipt=receipt)
        result.update(wall_seconds=time.perf_counter() - start, code=code, error=error,
                      response=data, receipt=receipt)
        valid = code == 0 and D.valid_openrouter_response(data, body["questions"], allow_refusal=False) and P.observe(receipt, data)
        result["status"] = "passed" if valid else "failed"
    except (OSError, ValueError, KeyError) as exc:
        result.update(status="blocked", error_type=type(exc).__name__)
        if isinstance(exc, urllib.error.HTTPError):
            result["http_status"] = exc.code
    finally:
        if previous is None:
            os.environ.pop("OPENROUTER_API_KEY", None)
        else:
            os.environ["OPENROUTER_API_KEY"] = previous
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2, allow_nan=False)
    print(json.dumps({k: result.get(k) for k in ("status", "code", "http_status", "wall_seconds")}, ensure_ascii=False))
    return 0 if result["status"] == "passed" else 1


if __name__ == "__main__":
    import sys as _sys
    for _stream in (_sys.stdin, _sys.stdout, _sys.stderr):  # Windows: pipes/console default to cp1251
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    raise SystemExit(main())

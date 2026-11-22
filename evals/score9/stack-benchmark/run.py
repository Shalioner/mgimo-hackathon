#!/usr/bin/env python3
"""Offline benchmark of the actual Strategist commands and a batch control."""

import argparse
import contextlib
import hashlib
import importlib
import io
import json
import os
from pathlib import Path
import platform
import shutil
import statistics
import subprocess
import sys
import tempfile
import time


ROOT = Path(__file__).resolve().parents[3]
FAMILY = "projects/family-connect"
ESIM = "projects/esim-travel"
M27 = "projects/rehearsal-mobile-spend"
SNAPSHOT_ROOTS = (
    "AGENTS.md", ".mcp.json", ".claude", ".codex", "requirements.txt",
    "tools", "schemas", "knowledge", "workflows", "skills", "plugins",
    "projects", "evals/score9/jury", "tests/fixtures/datacheck",
)


def capture(command, env=None, stdin=None, timeout=45):
    result = subprocess.run(command, cwd=ROOT, env=env, input=stdin,
                            text=True, encoding="utf-8", capture_output=True, timeout=timeout)
    return {"exit": result.returncode, "out": result.stdout, "err": result.stderr}


def checked(result, code=0, marker=None):
    if result["exit"] != code or (marker is not None and marker not in result["out"]):
        raise ValueError(f"Expected exit {code}, marker {marker!r}: {str(result)[:800]}")
    return result["out"]


def script(name, *args):
    return [sys.executable, f"tools/{name}.py", *args]


def bundle(case):
    return [
        ("ledger", ["status", "--root", "projects"]),
        ("next", [case, "--on", "2026-11-21", "--json"]),
        ("ledger", ["brief", case, "DEC", "CHG", "EXP", "PRM", "--all"]),
        ("c3_approval", ["check", case, "--json"]),
    ]


def batch(case):
    sys.path.insert(0, str(ROOT / "tools"))
    results = []
    for module, args in bundle(case):
        run = importlib.import_module(module).main
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = run(args)
        results.append({"exit": code or 0, "out": out.getvalue(), "err": err.getvalue()})
    return results


def timed(fn):
    start = time.perf_counter()
    value = fn()
    return value, time.perf_counter() - start


def measure(fn, count):
    expected = fn()
    samples = []
    for _ in range(count):
        value, elapsed = timed(fn)
        if value != expected:
            raise ValueError("Repeated command changed its verified output")
        samples.append(elapsed)
    return {"seconds": samples, "median_s": statistics.median(samples),
            "stdout_bytes": len(expected.encode("utf-8"))}


def snapshot():
    data = subprocess.check_output([
        "git", "ls-files", "--cached", "--others", "--exclude-standard", "-z",
        "--", *SNAPSHOT_ROOTS,
    ], cwd=ROOT)
    paths = sorted(set(os.fsdecode(path) for path in data.split(b"\0") if path))
    return {path: hashlib.sha256((ROOT / path).read_bytes()).hexdigest()
            for path in paths if (ROOT / path).is_file()}


def resume_pair(case, expected_c3, env, count):
    separate = lambda: [capture(script(name, *args), env) for name, args in bundle(case)]
    combined = lambda: json.loads(checked(capture(
        [sys.executable, str(Path(__file__).resolve()), "--batch", case], env)))
    expected = separate()
    checked(expected[0], marker=f"{case.split('/')[-1]}:")
    checked(expected[1], marker='"items"')
    checked(expected[2], marker="DEC-001")
    approval = json.loads(checked(expected[3], code=0 if expected_c3 == "current" else 1))
    if approval["status"] != expected_c3 or combined() != expected:
        raise ValueError(f"{case}: baseline or batch output did not match")
    pairs = []
    for index in range(count):
        timings = {}
        for name, fn in (("separate", separate), ("batch", combined))[::(-1 if index % 2 else 1)]:
            value, timings[name] = timed(fn)
            if value != expected:
                raise ValueError(f"{case}: changed exit code, stdout or stderr")
        pairs.append(timings)
    return {"pairs_s": pairs, "separate_processes": 4, "batch_processes": 1,
            "identical_outputs": True, "c3_status": expected_c3,
            "payload_bytes": sum(len(item["out"].encode("utf-8")) for item in expected)}


def run_suite(count, jury_count, temp):
    env = {"PATH": os.environ["PATH"], "HOME": str(temp), "USERPROFILE": str(temp),
           "PYTHONUTF8": "1", "PYTHONDONTWRITEBYTECODE": "1", "CODEX_THREAD_ID": "benchmark-offline"}
    request = temp / "request.txt"
    request.write_text("Сопоставь правила общего пакета и полномочия.\n", encoding="utf-8")
    mcp_list = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}) + "\n"
    output_html = temp / "family.html"
    tools = {
        "ledger_status": (script("ledger", "status"), 0, "family-connect:"),
        "ledger_validate": (script("ledger", "validate", FAMILY), 0, "ошибок 0"),
        "verify_claims": (script("verify", FAMILY, "--files", "03", "04", "05", "06", "07", "--strict", "--json"), 0, '"checked"'),
        "consistency": (script("consistency", FAMILY, "--json"), 0, "validation.causal_ok"),
        "trace": (script("ledger", "trace", FAMILY, "DEC-001"), 0, "ALT-003"),
        "impact": (script("ledger", "impact", ESIM, "CHG-001", "--json"), 0, "PRM-001"),
        "c3_prepare": (script("c3_approval", "prepare", FAMILY), 0, None),
        "datacheck": (script("datacheck", "tests/fixtures/datacheck/clean.csv", "--json"), 0, '"issues"'),
        "report_generate": (script("report", FAMILY, "--out", str(output_html)), 0, None),
        "route_t0": (script("ledger", "route", "validate", "--env", "codex"), 0, "T0"),
        "documents_preflight": (
            script("document_flow", "--prompt-file", str(request), "--host", "codex",
                   "--source-file", f"{FAMILY}/sources/mts-family-roles.md",
                   "--source-file", f"{FAMILY}/sources/mts-family-allowance.md"),
            0, '"extractions"'),
    }
    actions = {}
    for name, (cmd, code, marker) in tools.items():
        actions[name] = measure(lambda: checked(capture(cmd, env), code, marker), count)
    if "checkpoint-approval" not in output_html.read_text(encoding="utf-8"):
        raise ValueError("Rendered family report lacks the checkpoint")
    actions["report_generate"]["artifact_bytes"] = output_html.stat().st_size
    actions["mcp_list"] = measure(lambda: checked(capture(script("mcp_server"), env, mcp_list),
                                                 marker="strategist_status"), count)
    catalog = json.loads(checked(capture(script("mcp_server"), env, mcp_list)))["result"]["tools"]
    compact_catalog = json.dumps(catalog, ensure_ascii=False, separators=(",", ":")).encode()
    jury = [sys.executable, "evals/score9/jury/run.py", "--json"]
    examples = []
    for _ in range(jury_count):
        raw = json.loads(checked(capture(jury, env, timeout=180)))
        if raw["model_calls"] != 0 or "negative_controls" not in raw:
            raise ValueError("Unexpected jury coverage")
        examples.append({key: raw[key] for key in (
            "timing_seconds", "model_calls", "commands_executed", "unchanged_sha256_files",
        )})
    rtk = None
    if shutil.which("rtk"):
        rtk = {"version": checked(capture(["rtk", "--version"], env)).strip(),
               "samples": {}}
        for label, commands in {
            "git_status": ["git", "status", "--short", "--branch"],
            "git_diff": ["git", "diff", "--", "tools/report.py", "tools/c3_approval.py", "workflows/strategy.md"],
        }.items():
            original = checked(capture(commands, env))
            filtered = checked(capture(["rtk", *commands], env))
            rtk["samples"][label] = {"plain_bytes": len(original.encode()),
                                     "rtk_bytes": len(filtered.encode()),
                                     "identical": original == filtered}
    return {"actions": actions, "juried_replay": examples,
            "resume": {case: resume_pair(case, status, env, count) for case, status in
                       ((FAMILY, "current"), (ESIM, "unrecorded"))},
            "mcp_catalog": {"tools": [item["name"] for item in catalog],
                            "definition_bytes": len(compact_catalog)}, "rtk": rtk}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repeats", type=int, default=5)
    parser.add_argument("--jury-repeats", type=int, default=1)
    parser.add_argument("--batch", choices=[FAMILY, ESIM], help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.batch:
        print(json.dumps(batch(args.batch), ensure_ascii=False))
        return
    if args.repeats < 1 or args.jury_repeats < 1:
        parser.error("Repeat counts must be positive")
    before = snapshot()
    load_before = os.getloadavg()
    with tempfile.TemporaryDirectory(prefix="strategist-stack-") as temp:
        results = run_suite(args.repeats, args.jury_repeats, Path(temp))
    if snapshot() != before:
        raise ValueError("Runtime or fixtures changed during the benchmark")
    digest = hashlib.sha256(json.dumps(before, sort_keys=True).encode()).hexdigest()
    results.update(repeats=args.repeats, platform=platform.platform(), python=platform.python_version(),
                   logical_cpus=os.cpu_count(), load_avg_before=load_before, load_avg_after=os.getloadavg(),
                   git_head=checked(capture(["git", "rev-parse", "HEAD"])).strip(),
                   snapshot_sha256=digest, snapshot_files=len(before),
                   requires_model_calls=False)
    print(json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Benchmark a fresh isolated installation from a verified project archive."""

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import zipfile

from run import ROOT, snapshot


def read_checksum(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run_clean():
    inputs = snapshot()
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    timings = {}
    with tempfile.TemporaryDirectory(prefix="strategist-clean-install-") as directory:
        temp = Path(directory)
        work = temp / "unpacked"
        home = temp / "home"
        home.mkdir()
        env = {"PATH": os.environ["PATH"], "HOME": str(home), "USERPROFILE": str(home),
               "PIP_CONFIG_FILE": os.devnull, "PYTHONUTF8": "1", "PYTHONDONTWRITEBYTECODE": "1"}

        def command(name, *argv, timeout=180):
            start = time.perf_counter()
            result = subprocess.run(argv, cwd=work, env=env, capture_output=True,
                                    text=True, encoding="utf-8", timeout=timeout)
            timings[name] = time.perf_counter() - start
            if result.returncode:
                raise RuntimeError(f"{name}: {result.returncode}\n{result.stderr[-1200:]}")
            return result.stdout

        archive = temp / "strategist.zip"
        start = time.perf_counter()
        with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as bundle:
            for path in inputs:
                bundle.write(ROOT / path, path)
            bundle.writestr("SHA256-MANIFEST.json", json.dumps(inputs, sort_keys=True))
        timings["pack"] = time.perf_counter() - start
        start = time.perf_counter()
        with zipfile.ZipFile(archive) as bundle:
            bundle.extractall(work)
        manifest = json.loads((work / "SHA256-MANIFEST.json").read_text())
        if manifest != inputs or any(read_checksum(work / path) != sha for path, sha in manifest.items()):
            raise ValueError("Extracted archive does not match the original inputs")
        timings["unpack_and_verify"] = time.perf_counter() - start
        if (work / ".git").exists() or (work / ".venv").exists():
            raise ValueError("Expected archive without Git or an existing Python environment")
        command("venv", sys.executable, "-m", "venv", ".venv")
        python = str(work / ".venv/bin/python")
        command("dependency_install", python, "-m", "pip", "install", "--disable-pip-version-check",
                "--no-input", "-r", "requirements.txt")
        dependencies = command("freeze", python, "-m", "pip", "freeze")
        report = json.loads(command("offline_jury", python, "evals/score9/jury/run.py"))
        if (report["model_calls"] != 0 or "negative_controls" not in report
                or report.get("c3_record_controls") != {
                    "rejected_without_changes": 5, "recorded": 1, "retry_unchanged": True,
                }):
            raise ValueError("Unexpected offline jury coverage")
        if snapshot() != inputs:
            raise ValueError("Working copy changed during installation replay")
        return {"git_head": head, "file_count": len(inputs), "zip_bytes": archive.stat().st_size,
                "zip_sha256": read_checksum(archive), "git_present": False,
                "seconds": timings, "dependencies": dependencies.splitlines(),
                "commands_executed": report["commands_executed"],
                "model_calls": report["model_calls"], "case_hashes_unchanged": report["unchanged_sha256_files"],
                "negative_controls": report["negative_controls"],
                "c3_record_controls": report["c3_record_controls"]}


if __name__ == "__main__":
    print(json.dumps(run_clean(), ensure_ascii=False, indent=2))

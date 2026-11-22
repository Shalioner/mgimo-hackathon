"""Verify and time the deterministic queue on the frozen four-case inputs."""
import hashlib
import json
from pathlib import Path
import shutil
import statistics
import subprocess
import sys
import tempfile
import time


root = Path(__file__).resolve().parents[3]
expected = {
    "01": ("ALT-003", None, "current"),
    "02": ("ALT-003", "C3", "stale"),
    "03": ("ALT-003", None, "current"),
    "04": ("ALT-001", "C2", "invalid"),
}


def manifest(root):
    return {path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(root.rglob("*")) if path.is_file()}


def make_inputs(directory):
    for key in ("01", "02", "03"):
        shutil.copytree(root / "projects/family-connect", directory / key,
                        ignore=shutil.ignore_patterns(".ledger-write.lock"))
    for path in ("02/outputs/06_comms_strategy.md", "03/outputs/10_market_comparison.md"):
        with (directory / path).open("a", encoding="utf-8") as stream:
            stream.write("\nПримечание редактора. Текст проверен для передачи в новый чат.\n")
    shutil.copytree(root / "evals/score9/paired-workshop/control/review", directory / "04")
    return manifest(directory)


def batch(directory):
    start = time.perf_counter()
    rows = {}
    for case in expected:
        result = subprocess.run([sys.executable, "-B", str(root / "tools/next.py"),
                                 str(directory / case), "--resume", "--json", "--on", "2026-11-22"],
                                capture_output=True, text=True, check=True, cwd=root)
        rows[case] = json.loads(result.stdout)["resume"]
    return time.perf_counter() - start, rows


def main():
    frozen = json.loads(Path(__file__).with_name("frozen.json").read_text(encoding="utf-8"))
    with tempfile.TemporaryDirectory(prefix="strategist-queue-check-") as temp:
        inputs = Path(temp)
        hashes = make_inputs(inputs)
        if hashes != frozen["files"]:
            raise ValueError("case bytes differ from the frozen paired inputs")
        seconds = []
        for _ in range(5):
            elapsed, rows = batch(inputs)
            seconds.append(round(elapsed, 3))
            for key, reply in rows.items():
                choices = reply["previous_choices"] if key == "04" else reply["choices"]
                observed = choices[0]["chosen"], reply["open_checkpoint"], reply["c3"]["status"]
                assert observed == expected[key], (key, observed)
                if key in ("01", "03"):
                    assert "EXP-001" in reply["next_action"] and "дизайнер" in reply["next_action"]
        assert hashes == manifest(inputs)
    print(json.dumps({"verified_rows": 4, "verified_batches": len(seconds),
                      "files_unchanged": len(hashes), "seconds": seconds,
                      "median_seconds": statistics.median(seconds), "model_calls": 0,
                      "human_active_seconds": None, "commands": 20, "error_count": 0, "last_rows": rows},
                     indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()

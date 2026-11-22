"""Reproduce C3 archival on saved inputs and apply the recorded subsequent edits."""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
CAPTURE = HERE / "evidence/gate/compact"
RECEIPT = "state/c3_approvals.json"
PLAN = "outputs/07_validation_plan.md"


def run(tool, *args, check=True):
    return subprocess.run([sys.executable, "-B", str(ROOT / "tools" / (tool + ".py")), *map(str, args)],
                          cwd=ROOT, capture_output=True, text=True, check=check)


def case(turn):
    return next((CAPTURE / f"{turn}-state").iterdir())


def main():
    entry = json.loads((case(4) / RECEIPT).read_text(encoding="utf-8"))["approvals"][-1]
    presented = entry["sha256"]
    assert presented in (CAPTURE / "3-answer.md").read_text(encoding="utf-8")
    missing = run("c3_approval", "check-archive", case(5), "--sha256", presented, check=False)
    assert missing.returncode == 2
    original = (case(4) / PLAN).read_bytes()
    original_hash = hashlib.sha256(original).hexdigest()
    assert original_hash == entry["inputs"][PLAN]
    assert (case(5) / PLAN).read_bytes() != original

    with tempfile.TemporaryDirectory(prefix="strategist-c3-archive-replay-") as directory:
        workspace = Path(directory)
        restored = shutil.copytree(case(3), workspace / "case")
        response = workspace / "answer.txt"
        response.write_text(entry["response"], encoding="utf-8")
        run("c3_approval", "record", restored, "--sha256", presented, "--response-file", response)
        approved_receipt = (restored / RECEIPT).read_bytes()
        shutil.copytree(case(5), restored, dirs_exist_ok=True,
                        ignore=lambda _, names: [name for name in names if name == "c3_approvals.json"])
        assert (restored / RECEIPT).read_bytes() == approved_receipt
        assert (restored / "state/history/c3" / presented / PLAN).read_bytes() == original
        verified = run("c3_approval", "check-archive", restored, "--sha256", presented)
        next_step = json.loads(run("next", restored, "--resume", "--json").stdout)["resume"]
        assert next_step["open_checkpoint"] == "C2" and next_step["previous_choices"]
        print(json.dumps({"archive_before_exit": missing.returncode, "archive_after_exit": verified.returncode,
                          "package_sha256": presented, "old_plan_sha256": original_hash,
                          "receipt_unchanged_after_edits": True, "next_step": next_step["open_checkpoint"],
                          "historical_choice": next_step["previous_choices"]},
                         ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

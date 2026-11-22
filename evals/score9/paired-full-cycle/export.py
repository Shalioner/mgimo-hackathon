"""Export model responses and verify the workflow snapshots outside the measured window."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess


def result(python, repo, tool, *args):
    proc = subprocess.run([str(python), "-B", str(repo / "tools" / (tool + ".py")), *map(str, args)],
                          capture_output=True, text=True, cwd=repo)
    return {"exit": proc.returncode, "value": json.loads(proc.stdout) if proc.stdout else None,
            "error": proc.stderr.strip() or None}


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def snapshot_result(python, repo, state, turn):
    projects = [p for p in sorted(state.iterdir()) if (p / "state/progress.md").is_file()]
    if len(projects) != 1:
        return {"error": f"expected one project, found {len(projects)}"}
    project = projects[0]
    receipt = project / "state/c3_approvals.json"
    approval = result(python, repo, "c3_approval", "check", project, "--json")
    queue = result(python, repo, "next", project, "--resume", "--json")
    if not queue["value"] or "resume" not in queue["value"]:
        return {"project": project.name, "queue_error": queue, "approval": approval}
    stage = {1: "C1", 2: "C2", 3: "C3"}.get(turn)
    checkpoint = None
    if stage:
        checked = subprocess.run([str(python), "-B", str(repo / "tools/checkpoint.py"), stage, str(project)],
                                 capture_output=True, text=True, cwd=repo)
        checkpoint = {"exit": checked.returncode, "stdout": checked.stdout.strip(), "stderr": checked.stderr.strip()}
    return {"project": project.name, "checkpoint_check": checkpoint, "outputs": sorted(p.name for p in (project / "outputs").glob("*.md")),
            "open_checkpoint": queue["value"]["resume"]["open_checkpoint"],
            "choices": queue["value"]["resume"]["choices"],
            "previous_choices": queue["value"]["resume"].get("previous_choices", []),
            "c3": approval["value"], "receipt_sha256": digest(receipt) if receipt.is_file() else None,
            "next_action": queue["value"]["resume"]["next_action"]}


def safe_excerpt(value, root):
    prefix = str(root).replace("/private/tmp/", "/tmp/")
    value = value.replace("/private" + prefix, "").replace(prefix, "")
    return re.sub(r"/Users/[^ /\n]+", "<локальный профиль>", value)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run", type=Path)
    parser.add_argument("out", type=Path)
    parser.add_argument("repo", type=Path)
    parser.add_argument("python", type=Path)
    args = parser.parse_args()
    root, out, repo, python = (args.run, args.out, args.repo, args.python)
    root, out, repo, python = root.resolve(), out.resolve(), repo.resolve(), python.absolute()
    out.mkdir(parents=True, exist_ok=True)
    for name in ("frozen.json", "metrics.json"):
        shutil.copy2(root / name, out / name)
    checks = {}
    for mode in json.loads((root / "metrics.json").read_text()):
        event_dir = root / f"{mode}-events"
        target = out / mode
        target.mkdir()
        checks[mode] = {}
        index = []
        for turn in range(1, 6):
            reply = event_dir / f"{turn}-answer.md"
            if reply.is_file():
                dest = target / reply.name
                dest.write_text(safe_excerpt(reply.read_text(), root))
            state = event_dir / f"{turn}-state"
            if state.is_dir():
                saved = target / state.name
                shutil.copytree(state, saved)
                if mode != "plain":
                    checks[mode][str(turn)] = snapshot_result(python, repo, saved, turn)
            trace = event_dir / f"{turn}.jsonl"
            if trace.is_file():
                with trace.open() as f:
                    for raw in f:
                        event = json.loads(raw)
                        item = event.get("item", {})
                        if event["type"] == "item.completed" and item.get("type") == "command_execution":
                            index.append({"turn": turn,
                                          "command_prefix": safe_excerpt(item["command"], root)[:220],
                                          "exit": item.get("exit_code"),
                                          "output_chars": len(item.get("aggregated_output") or "")})
        (target / "commands.json").write_text(json.dumps(index, indent=2, ensure_ascii=False) + "\n")
    (out / "checks.json").write_text(json.dumps(checks, ensure_ascii=False, indent=2) + "\n")


if __name__ == "__main__":
    main()

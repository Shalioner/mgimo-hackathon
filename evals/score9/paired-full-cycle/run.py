"""Run the frozen case in separate Codex CLI workspaces."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import runpy
import shutil
import subprocess
import time

HERE = Path(__file__).resolve().parent
BASELINE = "191c09f3856914e1428817f7b198ddf061410566"
MODEL = "gpt-6.2-sol"
EFFORT = "medium"


def sha(data):
    return hashlib.sha256(data).hexdigest()


def dump(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_inputs(root, files):
    for name, contents in files.items():
        assert Path(name).name == name
        (root / name).write_text(contents, encoding="utf-8")


def prepare(root, repository, python, modes):
    candidate = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repository, text=True).strip()
    if subprocess.check_output(["git", "status", "--porcelain"], cwd=repository).strip():
        raise ValueError("The candidate must be committed and clean")
    case = json.loads((HERE / "case.json").read_text(encoding="utf-8"))
    available = {"plain": None, "original": BASELINE, "compact": candidate}
    versions = {mode: available[mode] for mode in modes}
    private_eval_dir = HERE.relative_to(repository)
    trial_paths = {p: (HERE / p).read_bytes() for p in ("run.py", "PROTOCOL.md", "case.json")}
    manifest = {
        "versions": versions,
        "protocol": {name: sha(data) for name, data in trial_paths.items()},
        "inputs": {key: {name: sha(value.encode()) for name, value in case[key].items()}
                   for key in ("initial_files", "delayed_files")},
        "messages": case["messages"],
        "model": MODEL, "effort": EFFORT,
        "omitted_from_subject_workspace": private_eval_dir.as_posix(),
        "codex_version": subprocess.check_output(["codex", "--version"], text=True).strip(),
        "started_epoch": time.time(),
    }
    for mode, version in versions.items():
        workspace = root / mode
        workspace.mkdir()
        if version:
            with subprocess.Popen(["git", "archive", version], cwd=repository, stdout=subprocess.PIPE) as archive:
                subprocess.run(["tar", "-xf", "-", "-C", workspace], stdin=archive.stdout, check=True)
                if archive.wait():
                    raise RuntimeError(f"could not archive {version}")
            evaluator = workspace / private_eval_dir
            if evaluator.exists():
                shutil.rmtree(evaluator)
        (workspace / ".venv").symlink_to(python.parent.parent, target_is_directory=True)
        inputs = workspace / "lab-input"
        inputs.mkdir()
        write_inputs(inputs, case["initial_files"])
    dump(root / "frozen.json", manifest)
    return case, versions


def save_state(workspace, path, mode):
    path.mkdir()
    skip = shutil.ignore_patterns("__pycache__", ".ledger-write.lock")
    if mode == "plain":
        for item in workspace.iterdir():
            if item.name.startswith(".") or item.name == "lab-input":
                continue
            if item.is_dir():
                shutil.copytree(item, path / item.name, ignore=skip)
            else:
                shutil.copy2(item, path / item.name)
        return
    cases = sorted(p for p in (workspace / "projects").iterdir() if (p / "state/progress.md").is_file())
    for item in cases:
        # Bundled cases already exist before this trial.
        if item.name not in {"family-connect", "esim-travel", "example", "rehearsal-mobile-spend", "ryadom"}:
            shutil.copytree(item, path / item.name, ignore=skip)


def run(root, mode, case):
    workspace = root / mode
    records = root / f"{mode}-events"
    records.mkdir()
    env = os.environ.copy()
    for key in ("OPENAI_API_KEY", "AZURE_OPENAI_API_KEY", "OPENROUTER_API_KEY", "ANTHROPIC_API_KEY", "JEV_API_KEY"):
        env.pop(key, None)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    metrics = []
    session = None
    for turn, prompt in enumerate(case["messages"], 1):
        if turn == 5:
            write_inputs(workspace / "lab-input", case["delayed_files"])
        resume = 1 < turn < 5
        reply = records / f"{turn}-answer.md"
        cmd = ["codex", "exec", *(["resume"] if resume else []),
               "--ignore-user-config", "--disable", "hooks", "--skip-git-repo-check", "--json",
               "-c", 'approval_policy="never"', "-c", 'sandbox_mode="workspace-write"',
               "-c", "sandbox_workspace_write.network_access=false", "-c", f'model_reasoning_effort="{EFFORT}"',
               "-m", MODEL, "-o", str(reply), *([session] if resume else []), "-"]
        log = records / f"{turn}.jsonl"
        begin = time.perf_counter()
        with log.open("w") as stdout, (records / f"{turn}.stderr").open("w") as stderr:
            try:
                ret = subprocess.run(cmd, cwd=workspace, env=env, input=prompt, text=True, stdout=stdout,
                                     stderr=stderr, timeout=600).returncode
            except subprocess.TimeoutExpired:
                ret = 124
        seconds = round(time.perf_counter() - begin, 3)
        events = [json.loads(line) for line in log.read_text().splitlines() if line.startswith("{")]
        current = next((event["thread_id"] for event in events if event["type"] == "thread.started"), None)
        usage = [event["usage"] for event in events if event["type"] == "turn.completed"]
        commands = [event["item"] for event in events if event["type"] == "item.completed"
                    and event["item"]["type"] == "command_execution"]
        errors = sum(item.get("exit_code") != 0 for item in commands)
        errors += sum(event["type"] in ("error", "turn.failed") for event in events)
        metrics.append(dict(turn=turn, seconds=seconds, exit=ret, session="initial" if turn < 5 else "fresh",
                            usage=usage, tool_or_turn_errors=errors, commands=len(commands),
                            command_output_chars=sum(len(item.get("aggregated_output") or "") for item in commands)))
        dump(records / "metrics.json", metrics)
        print(f"{mode} turn {turn}: {ret}, {seconds:.1f} s, {len(commands)} commands, {errors} errors", flush=True)
        if ret or not current or not reply.is_file() or len(usage) != 1:
            return mode, 1
        if (resume and current != session) or (turn == 5 and current == session):
            raise ValueError(f"{mode}: incorrect session continuity at turn {turn}")
        session = current
        save_state(workspace, records / f"{turn}-state", mode)
        files = dict(case["initial_files"])
        if turn == 5:
            files.update(case["delayed_files"])
        for name, contents in files.items():
            assert (workspace / "lab-input" / name).read_bytes() == contents.encode()
    return mode, 0


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("root", type=Path)
    cli.add_argument("repo", type=Path)
    cli.add_argument("python", type=Path)
    cli.add_argument("--modes", nargs="+", choices=("plain", "original", "compact"),
                     default=("plain", "original", "compact"))
    args = cli.parse_args()
    root = args.root.resolve()
    case, versions = prepare(root, args.repo.resolve(), args.python.absolute(), args.modes)
    with ThreadPoolExecutor(max_workers=len(versions)) as pool:
        statuses = dict(pool.map(lambda mode: run(root, mode, case), versions))
    measure = runpy.run_path(str(HERE.parent / "paired-workshop/export_pair.py"))["measure"]
    result = {}
    for mode, status in statuses.items():
        turns = json.loads((root / f"{mode}-events" / "metrics.json").read_text())
        result[mode] = {"status": status, "turns": turns,
                        "total": measure(turns) if not status else None}
    dump(root / "metrics.json", result)
    return any(statuses.values())


if __name__ == "__main__":
    raise SystemExit(main())

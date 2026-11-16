#!/usr/bin/env python3
"""Optional, pinned d1-2B laboratory. No cloud transport or router integration."""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time
import urllib.request

REPO = "LiquidAI/d1-2B"
REVISION = "a6bce774e6dde338dcc3036ceb5d91f78bf4c19e"
LAB = Path.home() / ".cache/strategist/d1-2b-lab"
WEIGHTS = LAB / "weights" / REVISION
MANIFEST = Path(__file__).with_name("local_decision_manifest.json")
MAX_BYTES = 8192
MAX_PIXELS = 384 * 384


def require(condition, message):
    if not condition:
        raise ValueError(message)


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for block in iter(lambda: file.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def verify_weights(directory, manifest_path=None):
    manifest = json.loads((manifest_path or MANIFEST).read_text(encoding="utf-8"))
    for name, expected in manifest.items():
        path = directory / name
        require(path.is_file() and not path.is_symlink(), f"Missing regular pinned file: {name}; run download")
        require(sha256(path) == expected, f"Pinned SHA256 mismatch: {name}")
    require(not set(p.name for p in directory.iterdir()) - set(manifest), "Unreviewed file in weights")


def download(directory, *, manifest_path=None, repo=REPO, revision=REVISION):
    """Explicit network action for official public artifacts only, never inference."""
    directory.mkdir(parents=True, exist_ok=True)
    for name, expected in json.loads((manifest_path or MANIFEST).read_text(encoding="utf-8")).items():
        target = directory / name
        if target.is_file() and not target.is_symlink() and sha256(target) == expected:
            continue
        require(not target.exists(), f"Refusing to overwrite modified artifact: {target}")
        partial = target.with_name(name + ".partial")
        require(not partial.exists(), f"Incomplete download exists: {partial}; inspect before retry")
        print(f"Downloading {name}", file=sys.stderr, flush=True)
        with urllib.request.urlopen(f"https://huggingface.co/{repo}/resolve/{revision}/{name}", timeout=120) as response, partial.open("xb") as output:
            for block in iter(lambda: response.read(8 * 1024 * 1024), b""):
                output.write(block)
        require(sha256(partial) == expected, f"Download SHA256 mismatch: {name}")
        partial.rename(target)
    verify_weights(directory, manifest_path)
    return {"status": "downloaded", "repo": repo, "revision": revision, "weights": str(directory)}


def validate_request(state, questions):
    require(len(json.dumps(state, ensure_ascii=False, allow_nan=False).encode()) <= MAX_BYTES, "State exceeds 8192 bytes; no truncation")
    require(type(questions) is dict and 1 <= len(questions) <= 8, "Use 1–8 named questions")
    require(len(json.dumps(questions, ensure_ascii=False, allow_nan=False).encode()) <= MAX_BYTES, "Questions exceed 8192 bytes")
    for name, q in questions.items():
        require(isinstance(name, str) and bool(name) and type(q) is dict, "Invalid named question")
        require(set(q) <= {"type", "instructions", "criteria", "multiple"}, "Unsupported question fields")
        require(isinstance(q.get("type"), str) and q["type"] in {"noul", "choice", "score"}, "Local wrapper supports noul/choice/score only")
        require(isinstance(q.get("instructions"), str) and bool(q["instructions"].strip()), "Text instructions required; no embedded media/URLs")
        criteria = q.get("criteria")
        if q["type"] == "choice":
            require(type(criteria) is dict and 2 <= len(criteria) <= 10, "Choice requires 2–10 criteria")
            require(all(isinstance(k, str) and k and isinstance(v, str) for k, v in criteria.items()), "Criteria must be text")
            require(type(q.get("multiple", False)) is bool, "multiple must be boolean")
        elif q["type"] == "score":
            require(type(criteria) is list and 2 <= len(criteria) <= 10 and all(isinstance(v, str) for v in criteria), "Score requires 2–10 ordered labels")
            require("multiple" not in q, "multiple is supported only for choice")
        else:
            require(criteria is None and "multiple" not in q, "noul does not accept criteria/multiple")


def validate_result(result, questions):
    require(type(result) is dict and type(result.get("answers")) is dict, "Invalid System One result")
    require(set(result["answers"]) == set(questions), "Result question IDs differ")
    def probability(value):
        return type(value) in (int, float) and math.isfinite(value) and 0 <= value <= 1
    for name, q in questions.items():
        answer = result["answers"][name]
        require(type(answer) is dict and answer.get("type") == q["type"], "Invalid answer type")
        if q["type"] == "noul":
            require(probability(answer.get("noul")), "Invalid P(yes)")
            continue
        expected = set(q["criteria"]) if q["type"] == "choice" else set(map(str, range(len(q["criteria"]))))
        probs = answer.get("probabilities")
        require(type(probs) is dict and set(probs) == expected and all(probability(v) for v in probs.values()), "Invalid probabilities")
        if q.get("multiple", False):
            require(type(answer.get("choices")) is list and all(isinstance(c, str) and c in expected for c in answer["choices"]), "Invalid choices")
            require(answer["choices"] == [k for k in q["criteria"] if probs[k] >= 0.5], "Choices disagree with probabilities")
        else:
            require(abs(sum(probs.values()) - 1) <= 0.01 and probability(answer.get("confidence")), "Invalid distribution/confidence")
            if q["type"] == "choice":
                require(isinstance(answer.get("choice"), str) and answer["choice"] in expected, "Unknown choice")
                require(answer["choice"] == max(q["criteria"], key=probs.__getitem__), "Choice disagrees with probabilities")
                require(math.isclose(answer["confidence"], probs[answer["choice"]], abs_tol=1e-6), "Confidence disagrees with probabilities")
            else:
                score = answer.get("score")
                require(type(score) in (int, float) and math.isfinite(score) and 0 <= score < len(q["criteria"]), "Invalid score")
                total = sum(probs.values())
                mass = 0.0
                median = len(q["criteria"]) - 1
                for i in range(len(q["criteria"])):
                    mass += probs[str(i)] / total
                    if mass >= 0.5:
                        median = i
                        break
                require(score == median, "Score disagrees with default median readout")
                require(math.isclose(answer["confidence"], max(probs.values()), abs_tol=1e-6), "Confidence disagrees with probabilities")
    usage = result.get("usage")
    require(type(usage) is dict and type(usage.get("input_tokens")) is int and usage["input_tokens"] >= 0 and usage.get("output_tokens") == 0, "Invalid usage")


def read_questions(path):
    import yaml
    require(path.stat().st_size <= MAX_BYTES, "Questions file too large")
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ValueError("Invalid questions YAML") from exc
    return data.get("questions", data) if isinstance(data, dict) else data


def memory():
    text = subprocess.run(["/usr/sbin/sysctl", "vm.swapusage", "kern.memorystatus_vm_pressure_level"], capture_output=True, text=True, timeout=5, encoding="utf-8").stdout.strip()
    match = re.search(r"used = ([\d.]+)M", text)
    pressure = re.search(r"kern.memorystatus_vm_pressure_level: (\d+)", text)
    return {"swap_used_mib": float(match[1]) if match else None,
            "pressure_level": int(pressure[1]) if pressure else None, "swap_raw": text}


def worker(payload):
    """One model instance, sequential requests; executed with OS network denied."""
    import resource
    import torch
    from transformers import AutoModel
    from PIL import Image
    directory = Path(payload["weights"])
    verify_weights(directory)  # before trust_remote_code, including all custom code
    require(torch.backends.mps.is_available(), "MPS unavailable; no CPU/cloud fallback on this 8 GiB lab")
    torch.set_num_threads(2)
    t0 = time.monotonic()
    model = AutoModel.from_pretrained(str(directory), revision=REVISION, code_revision=REVISION,
                                      trust_remote_code=True, local_files_only=True, use_safetensors=True,
                                      dtype=torch.bfloat16, device_map="mps").eval()
    torch.mps.synchronize()
    load_seconds = time.monotonic() - t0
    # The reviewed runner optionally concatenates projection weights on engine init.
    # Keep the checkpoint's original separate linears on this 8 GiB machine:
    # same model/weights, no transient concatenation and cached old MPS copies.
    model.fuse_projections = lambda: None
    torch.mps.empty_cache()
    print(f"Loaded model: MPS allocated={torch.mps.current_allocated_memory()} driver={torch.mps.driver_allocated_memory()}; projection fusion disabled", file=sys.stderr, flush=True)
    results = []
    for case in payload["cases"]:
        validate_request(case["state"], case["questions"])
        encoded = model.engine.tokenizer.encode(json.dumps([case["state"], case["questions"]], ensure_ascii=False))
        require(len(encoded) <= 1024, "Text exceeds lab 1024-token budget; no truncation")
        model.engine.token_budget = 4096
        media = None
        image_info = None
        if case.get("image"):
            image_path = Path(case["image"])
            require(image_path.is_file() and image_path.stat().st_size <= 8 * 1024 * 1024, "Image must be a local file <=8 MiB")
            with Image.open(image_path) as source:
                require(source.width * source.height <= 16_000_000, "Image exceeds 16M pixels")
                image = source.convert("RGB")
            original = image.size
            image.thumbnail((384, 384))
            media = [image]
            image_info = {"original_size": original, "inference_size": image.size}
        start = time.monotonic()
        with torch.inference_mode():
            answer = model.system_one(case["state"], case["questions"], media=media)
        torch.mps.synchronize()
        validate_result(answer, case["questions"])
        results.append({"id": case["id"], "response": answer, "seconds": round(time.monotonic() - start, 3),
                        "image": image_info, "max_rss_bytes": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
                        "mps_allocated_bytes": torch.mps.current_allocated_memory(),
                        "mps_driver_bytes": torch.mps.driver_allocated_memory()})
        print(f"Completed {case['id']}", file=sys.stderr, flush=True)
    return {"status": "succeeded", "repo": REPO, "revision": REVISION, "device": "mps", "dtype": "bfloat16",
            "projection_fusion": False, "load_seconds": round(load_seconds, 3), "results": results}


def execute(cases, directory, timeout, *, worker_script=None, preflight_samples=0):
    import fcntl
    require(sys.platform == "darwin", "This optional lab launcher supports macOS only; no fallback")
    require(1 <= len(cases) <= 6 and 1 <= timeout <= 300, "At most 6 cases and timeout 1–300 seconds")
    for case in cases:
        validate_request(case["state"], case["questions"])
    LAB.mkdir(parents=True, exist_ok=True)
    with (LAB / "inference.lock").open("a", encoding="utf-8") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError("Another local d1 process holds the lab lock")
        preflight = []
        for i in range(preflight_samples):
            sample = {"timestamp": time.time(), **memory()}
            preflight.append(sample)
            if sample.get("pressure_level") != 1 or sample.get("swap_used_mib") is None or sample["swap_used_mib"] > 8192:
                return {"status": "blocked", "reason": "Preflight requires normal pressure=1 and swap<=8 GiB; no process started",
                        "preflight": preflight, "process_started": False, "fallback": False}
            if i + 1 < preflight_samples:
                time.sleep(1)
        before = memory()
        if preflight_samples and (before.get("pressure_level") != 1 or before.get("swap_used_mib") is None or before["swap_used_mib"] > 8192):
            return {"status": "blocked", "reason": "Pressure changed after preflight; no process started",
                    "preflight": preflight, "memory_before": before, "process_started": False, "fallback": False}
        with tempfile.TemporaryDirectory(prefix="run-", dir=LAB) as tmp:
            env = {"PATH": "/usr/bin:/bin", "HOME": str(LAB), "HF_HOME": str(Path(tmp)/"hf"),
                   "HF_MODULES_CACHE": str(Path(tmp)/"modules"), "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1",
                   "HF_HUB_DISABLE_TELEMETRY": "1", "TOKENIZERS_PARALLELISM": "false", "D1_VISION_MAX_PIXELS": str(MAX_PIXELS)}
            command = ["/usr/bin/sandbox-exec", "-p", "(version 1)(allow default)(deny network*)",
                       sys.executable, str(Path(worker_script or __file__).resolve()), "_worker"]
            start = time.monotonic()
            proc = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                    text=True, cwd=tmp, env=env, encoding="utf-8")
            payload = json.dumps({"weights": str(directory.resolve()), "cases": cases}, ensure_ascii=False)
            stopped = None
            samples = []
            try:
                while True:
                    try:
                        stdout, stderr = proc.communicate(input=payload, timeout=5)
                        break
                    except subprocess.TimeoutExpired:
                        payload = None
                        rss = subprocess.run(["/bin/ps", "-o", "rss=", "-p", str(proc.pid)], capture_output=True, text=True, timeout=5, encoding="utf-8").stdout.strip()
                        sample = {"seconds": round(time.monotonic()-start, 1), "rss_kib": int(rss) if rss.isdigit() else None, **memory()}
                        samples.append(sample)
                        swap_growth = (sample["swap_used_mib"] or 0) - (before["swap_used_mib"] or 0)
                        if (time.monotonic()-start > timeout or (sample["rss_kib"] or 0) > 6*1024*1024
                                or swap_growth > 3072 or (sample["swap_used_mib"] or 0) > 8192
                                or (sample.get("pressure_level") or 0) >= 4):
                            stopped = "Timeout or lab memory budget (RSS 6 GiB / swap growth 3 GiB / total swap 8 GiB / critical pressure) reached; no fallback"
                            proc.kill()
                            stdout, stderr = proc.communicate()
                            break
            finally:
                if proc.poll() is None:
                    proc.kill()
                    proc.wait()
            if stopped:
                report = {"status": "blocked", "reason": stopped}
            else:
                try:
                    report = json.loads(stdout)
                    require(type(report) is dict and report.get("status") in {"succeeded", "blocked"}, "Invalid worker result")
                    if proc.returncode and report["status"] != "blocked":
                        report = {"status": "blocked", "reason": "Worker exited unsuccessfully"}
                except (ValueError, TypeError):
                    report = {"status": "blocked", "reason": "Local model process failed or returned invalid JSON"}
            if report["status"] == "blocked":
                report["stderr"] = stderr[-12000:]
            report.update(worker_stderr=stderr, preflight=preflight, process_started=True,
                          process_exit=proc.returncode, process_finished=proc.poll() is not None,
                          elapsed_seconds=round(time.monotonic()-start, 3), memory_before=before,
                          memory_after=memory(), memory_samples=samples, network="OS sandbox deny network; HF offline")
            return report


def smoke_cases():
    from PIL import Image, ImageDraw
    LAB.mkdir(parents=True, exist_ok=True)
    picture = LAB / "synthetic-red-square.png"
    image = Image.new("RGB", (256, 256), "white")
    ImageDraw.Draw(image).rectangle((32, 32, 224, 224), fill="red")
    image.save(picture)
    questions = read_questions(Path(__file__).resolve().parents[1] / "knowledge/decisions/route_tier.yaml")
    states = ["Извлеки из одного отчёта даты запуска и три точные цитаты; ничего не сравнивай.",
              "Составь план исследования удержания и краткую сводку трёх предоставленных отчётов.",
              "Сравни три стратегии запуска продукта, разреши противоречия источников и обоснуй выбор с учётом рисков."]
    return [{"id": f"routing_T{i+1}", "state": state, "questions": questions} for i, state in enumerate(states)] + [
        {"id": "image_red_square", "state": "Какого цвета квадрат?", "image": str(picture), "questions": {
            "color": {"type": "choice", "instructions": "Какого цвета большой квадрат на изображении?",
                      "criteria": {"red": "красный", "blue": "синий", "green": "зелёный"}}}}]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["download", "run", "smoke", "_worker"])
    parser.add_argument("--weights", type=Path, default=WEIGHTS)
    parser.add_argument("--questions", type=Path)
    inputs = parser.add_mutually_exclusive_group()
    inputs.add_argument("--state")
    inputs.add_argument("--state-file", type=Path)
    inputs.add_argument("--state-json", help="JSON state (object/list/scalar)")
    parser.add_argument("--image", type=Path)
    parser.add_argument("--timeout", type=int, default=240)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == "_worker":
            with contextlib.redirect_stdout(sys.stderr):
                report = worker(json.load(sys.stdin))
        elif args.command == "download":
            report = download(args.weights)
        elif args.command == "smoke":
            report = execute(smoke_cases(), args.weights, args.timeout)
        else:
            require(args.questions is not None, "--questions is required")
            state = args.state
            if args.state_file:
                require(args.state_file.stat().st_size <= MAX_BYTES, "State file exceeds limit")
                state = args.state_file.read_text(encoding="utf-8")
            elif args.state_json is not None:
                state = json.loads(args.state_json)
            require(state is not None or args.image is not None, "Provide state or image")
            report = execute([{"id": "request", "state": state, "questions": read_questions(args.questions),
                               "image": str(args.image.resolve()) if args.image else None}], args.weights, args.timeout)
    except (ValueError, OSError, ImportError, RuntimeError) as exc:
        report = {"status": "blocked", "reason": f"{type(exc).__name__}: {exc}", "fallback": False}
    text = json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False)
    if args.output:
        args.output.write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0 if report["status"] in {"succeeded", "downloaded"} else 3


if __name__ == "__main__":
    import sys as _sys
    for _stream in (_sys.stdin, _sys.stdout, _sys.stderr):  # Windows: pipes/console default to cp1251
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    raise SystemExit(main())

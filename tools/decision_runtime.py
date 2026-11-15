"""Local d1 System One adapter: reusable service or explicit laboratory CLI."""
from __future__ import annotations

import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import stat
import uuid

import local_decision as base
import local_decision_mlx as mlx

PYTHON = base.LAB / "mlx-venv/bin/python"
SCRIPT = Path(mlx.__file__).resolve()
SANDBOX = Path("/usr/bin/sandbox-exec")
SERVICE_SOCKET = Path.home() / '.cache/strategist/d1-service/service.sock'


def service_config(cfg):
    value = (cfg.get('automatic_routing') or {}).get('local_d1') or {}
    if not isinstance(value, dict) or value.get('mode', 'service') not in {'lab', 'service'}:
        raise ValueError('local_d1 mode must be lab or service')
    return {'mode': 'service', **value}


def plan(state, questions, cfg, timeout=240):
    receipt = {"status": "manual", "reason": "", "started": False,
               "wrapper_invocations": 0, "result": "not_started", "fallback": False,
               "requested": {"host": "local", "provider": "local-d1", "model": mlx.REPO,
                             "revision": mlx.REVISION, "effort": None},
               "transmitted": None, "observed": None, "network": "denied"}
    options = cfg.get("automatic_routing") or {}
    if not isinstance(options, dict) or options.get("classifier") != "local-d1":
        receipt["reason"] = "Local d1 was not explicitly selected"
        return receipt
    try:
        settings = service_config(cfg)
        base.require(type(settings.get('reuse', True)) is bool, 'local_d1 reuse must be boolean')
        base.require(isinstance(state, str), "Local classifier state must be text")
        if settings.get('mode') == 'service':
            base.validate_request('', questions)
            base.require(len(state.encode('utf-8')) <= 32768, 'State exceeds service 32768-byte limit; no truncation')
        else:
            base.validate_request(state, questions)
        base.require(type(timeout) is int and timeout > 0, "Positive integer timeout required")
        base.require(sys.platform == "darwin", "Local d1 MLX requires macOS")
        if settings.get('mode') == 'service':
            path = Path(settings.get('socket', SERVICE_SOCKET))
            base.require(path.is_absolute(), 'Absolute local service socket required')
            info, parent = path.lstat(), path.parent.lstat()
            base.require(stat.S_ISDIR(parent.st_mode) and parent.st_uid == os.getuid()
                         and not parent.st_mode & 0o077, 'Service directory must be owned by current user and private')
            base.require(stat.S_ISSOCK(info.st_mode) and info.st_uid == os.getuid()
                         and not info.st_mode & 0o077, 'Service socket must be owned by current user and private')
            receipt.update(status='executable', reason='Private service socket present; worker readiness checked at request',
                           transport='unix-service', socket=str(path), input_bytes=len(state.encode('utf-8')),
                           reuse=settings.get('reuse', True),
                           timeout_seconds=min(timeout, 300))
            return receipt
        base.require(PYTHON.is_file() and os.access(PYTHON, os.X_OK), "Local d1 MLX Python is unavailable")
        base.require(SCRIPT.is_file() and SANDBOX.is_file() and mlx.WEIGHTS.is_dir(), "Local d1 runtime/weights unavailable")
    except (ValueError, OSError, TypeError) as exc:
        receipt["reason"] = str(exc)
        return receipt
    receipt.update(status="executable", reason="Local runtime present; memory and weight integrity rechecked by guarded CLI",
                   input_bytes=len(state.encode("utf-8")), timeout_seconds=min(timeout, 300))
    return receipt


def _invoke(command, *, cwd, env, timeout):
    # Own a process group so an outer timeout cannot leave the model child running.
    proc = subprocess.Popen(command, cwd=cwd, env=env, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True, start_new_session=True)
    try:
        stdout, stderr = proc.communicate(timeout=timeout)
        return proc.returncode, stdout, stderr
    finally:
        if proc.poll() is None:
            os.killpg(proc.pid, signal.SIGKILL)
            proc.wait()


def infer(state, questions, cfg, *, execute=False, timeout=240):
    """Return {route, response?, runtime?}. Pure preflight unless execute=True.

    Callers must re-read their configuration before execution. Model input is never
    silently shortened. Memory limits, hash checks and lab lock remain in the CLI.
    """
    receipt = plan(state, questions, cfg, timeout)
    result = {"route": receipt}
    if not execute or receipt["status"] != "executable":
        return result
    if receipt.get('transport') == 'unix-service':
        return _infer_service(state, questions, receipt)
    try:
        with tempfile.TemporaryDirectory(prefix="strategist-local-decision-") as temp:
            folder = Path(temp)
            state_file, questions_file = folder / "state.txt", folder / "questions.json"
            state_file.write_text(state, encoding="utf-8")
            questions_file.write_text(json.dumps(questions, ensure_ascii=False), encoding="utf-8")
            command = [str(SANDBOX), "-p", "(version 1)(allow default)(deny network*)",
                       str(PYTHON), str(SCRIPT), "run", "--state-file", str(state_file),
                       "--questions", str(questions_file), "--timeout", str(receipt["timeout_seconds"])]
            env = {"PATH": "/usr/bin:/bin", "HOME": str(Path.home()), "PYTHONNOUSERSITE": "1",
                   "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1", "HF_HUB_DISABLE_TELEMETRY": "1"}
            receipt.update(started=True, wrapper_invocations=1, transmitted=dict(receipt["requested"]))
            code, stdout, _ = _invoke(command, cwd=temp, env=env, timeout=receipt["timeout_seconds"] + 30)
        receipt["process_exit_code"] = code
        report = json.loads(stdout)
        base.require(type(report) is dict, "Invalid local runtime report")
        if code or report.get("status") != "succeeded":
            receipt.update(result="blocked", reason=report.get("reason", "Local runtime blocked; no fallback"))
            result["runtime"] = report
            return result
        base.require(report.get("repo") == mlx.REPO and report.get("revision") == mlx.REVISION,
                     "Unexpected local model/revision")
        rows = report.get("results")
        base.require(type(rows) is list and len(rows) == 1 and rows[0].get("id") == "request",
                     "Expected exactly one local classification result")
        response = rows[0].get("response")
        base.validate_result(response, questions)
        receipt.update(result="succeeded", observed={"model": report["repo"], "revision": report["revision"]})
        result.update(response=response, runtime=report)
    except (OSError, ValueError, TypeError, AttributeError, subprocess.SubprocessError) as exc:
        receipt.update(result="failed" if receipt["started"] else "not_started", reason=type(exc).__name__ + ": " + str(exc))
    return result


def _infer_service(state, questions, receipt):
    import d1_service
    result = {'route': receipt}
    request_id = uuid.uuid4().hex
    try:
        receipt.update(started=True, wrapper_invocations=1, request_id=request_id,
                       transmitted=dict(receipt['requested']))
        report = d1_service.request(receipt['socket'], {
            'op': 'infer', 'id': request_id, 'state': state, 'questions': questions,
            'reuse': receipt['reuse'],
            'timeout': receipt['timeout_seconds']}, timeout=receipt['timeout_seconds'] + 5)
        base.require(isinstance(report, dict) and report.get('id') == request_id,
                     'Service response request ID mismatch')
        result['runtime'] = report.get('runtime', {})
        if report.get('status') != 'succeeded':
            receipt.update(result=report.get('status', 'failed'), reason=report.get('reason', 'Local service unavailable'))
            return result
        runtime = report.get('runtime') or {}
        base.require(runtime.get('model') == mlx.REPO and runtime.get('revision') == mlx.REVISION,
                     'Unexpected service model/revision')
        response = report.get('response')
        base.validate_result(response, questions)
        receipt.update(result='succeeded', observed={'model': runtime['model'], 'revision': runtime['revision']},
                       inference_calls=runtime.get('inference_calls'), reuse_result=runtime.get('reuse'),
                       worker_pid=runtime.get('worker_pid'), generation=runtime.get('generation'))
        result['response'] = response
    except (OSError, KeyboardInterrupt) as exc:
        # Cancel only this UUID, never the worker or another client's request.
        # Transport failure is ambiguous: never retry inference automatically.
        try:
            cancelled = d1_service.request(receipt['socket'], {'op': 'cancel', 'id': request_id}, timeout=2)
            receipt['cancel_status'] = (cancelled['status'] if isinstance(cancelled, dict)
                and cancelled.get('id') == request_id
                and cancelled.get('status') in {'cancelled', 'cancelling', 'not_found', 'already_finished'}
                else 'unconfirmed')
        except (OSError, ValueError, TypeError):
            receipt['cancel_status'] = 'unconfirmed'
        if isinstance(exc, KeyboardInterrupt):
            raise
        receipt.update(result='failed', reason=type(exc).__name__ + ': ' + str(exc))
    except (OSError, ValueError, TypeError, AttributeError) as exc:
        receipt.update(result='failed', reason=type(exc).__name__ + ': ' + str(exc))
    return result

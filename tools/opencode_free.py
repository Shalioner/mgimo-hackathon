"""OpenCode 2.2.5: offline exact-zero gate and signed-out private execution.

Catalog refresh is an explicit command, never a side effect of planning/inference.
No gateway imitation, user credentials, account migration, or automatic retry.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import pwd
import subprocess
import sys
import tempfile

SOURCE = "https://models.opencode.ai/experimental/v2.0/api.json?type=chat,decision,image"
VERSION = "opencode v2.2.5"
MAX_AGE = 24 * 3600
API = "https://opencode.ai/inference/openai/v1"
PACKAGE = "@opencode/ai/providers/openai-compatible"


def digest(data):
    return hashlib.sha256(json.dumps(data, sort_keys=True, allow_nan=False).encode()).hexdigest()


def require(ok, message):
    if not ok:
        raise ValueError(message)


def zero(value):
    return type(value) in (int, float) and value == 0


def zero_cache(cache):
    require(type(cache) is dict and set(cache) <= {"read", "write", "explicit"}, "Unknown cache pricing")
    for key, value in cache.items():
        if key == "explicit":
            require(type(value) is dict and set(value) == {"write"}, "Unknown explicit cache pricing")
            zero_cache(value)
        elif key == "write" and type(value) is dict:
            require(bool(value) and all(zero(v) for v in value.values()), "Nonzero cache TTL price")
        else:
            require(zero(value), "Nonzero/unknown cache price")


def exact_free(model):
    """Conservative v2 subset: reject unreviewed modes/modalities/multipliers.

All context/conditional overrides and cache TTL prices are checked, even if the
current prompt would not reach them. Missing optional cache prices mean no
separate cache tariff in this schema; input/output price must be explicit.
"""
    require(type(model) is dict and model.get("type") == "chat", "Not a chat model")
    require(model.get("status") in (None, "active"), "Unknown/unavailable model status")
    require(not model.get("experimental") and not model.get("variants"), "Unreviewed modes/variants")
    cost = model.get("cost")
    require(type(cost) is dict and set(cost) == {"text"}, "Unknown pricing dimensions/multipliers")
    text = cost["text"]
    require(type(text) is dict and set(text) == {"input", "output"}, "Missing text pricing")
    for direction, item in text.items():
        require(type(item) is dict and set(item) <= {"price", "unit", "cache", "overrides"}, "Unknown price field")
        require(zero(item.get("price")), "Nonzero/unknown price")
        require(item.get("unit") in (None, "1M tokens", "tokens", "million_tokens"), "Unknown price unit")
        if "cache" in item:
            zero_cache(item["cache"])
        overrides = item.get("overrides", [])
        require(type(overrides) is list, "Malformed price overrides")
        for override in overrides:
            require(type(override) is dict and set(override) <= {"when", "price", "cache"}
                    and type(override.get("when")) is dict and bool(override["when"]), "Unknown price override")
            require("price" in override or "cache" in override, "Empty price override")
            if "price" in override:
                require(zero(override["price"]), "Nonzero conditional/context price")
            if "cache" in override:
                zero_cache(override["cache"])
    api = model.get("api")
    require(type(api) is dict and set(api) == {"chat-completions"}, "Unreviewed API protocol")
    route = api["chat-completions"]
    require(type(route) is dict and type(route.get("sdk")) is dict, "Malformed route metadata")
    require(route.get("base_url") == API and route["sdk"].get("opencodeai") == PACKAGE,
            "Unreviewed endpoint/package")
    caps = model.get("capabilities", {})
    require(type(caps) is dict and type(caps.get("tools")) is dict, "Malformed capabilities")
    require(caps.get("tools", {}).get("supported") is True, "No tool capability metadata")
    modalities = model.get("modalities")
    require(type(modalities) is dict and type(modalities.get("input")) is list, "Malformed modalities")
    require("text" in modalities["input"] and modalities.get("output") == ["text"], "No text capability metadata")
    limits = model.get("limit", {})
    require(type(limits) is dict, "Malformed limits")
    require(all(type(limits.get(k)) is int and limits[k] > 0 for k in ("context", "output")), "Unknown limits")
    if "input" in limits:
        require(type(limits["input"]) is int and limits["input"] > 0, "Unknown input limit")
    return model


def system_boundary():
    require(sys.platform in {"darwin", "linux"}, "Only reviewed POSIX platforms supported")
    paths = [Path("/etc/opencode"), Path("/Library/Application Support/opencode"),
             Path("/Library/Managed Preferences/ai.opencode.managed.plist"),
             Path("/Library/Managed Preferences") / pwd.getpwuid(os.getuid()).pw_name / "ai.opencode.managed.plist"]
    require(not any(p.exists() or p.is_symlink() for p in paths), "System/MDM configuration exists; isolation not proven")


def load_catalog(profile, now=None):
    path = Path(profile.get("catalog") or "")
    require(path.is_absolute() and path.is_file(), "Explicit absolute catalog snapshot required; run refresh")
    data = json.loads(path.read_text())
    require(type(data) is dict and data.get("source") == SOURCE and data.get("schema") == 1,
            "Unknown catalog source/schema")
    stamp = dt.datetime.fromisoformat(data["fetched_at"])
    now = now or dt.datetime.now(dt.timezone.utc)
    require(stamp.tzinfo is not None and 0 <= (now - stamp).total_seconds() <= MAX_AGE, "Catalog stale/future; explicit refresh required")
    provider = data["provider"]
    require(type(provider) is dict and provider.get("id") == "opencode" and type(provider.get("models")) is dict,
            "Malformed provider catalog")
    require(digest(provider) == data.get("sha256"), "Catalog digest mismatch")
    binary = Path(data["binary"])
    require(binary.is_absolute() and binary.is_file() and os.access(binary, os.X_OK), "Reviewed binary unavailable")
    require(data.get("version") == VERSION and hashlib.sha256(binary.read_bytes()).hexdigest() == data.get("binary_sha256"),
            "Binary/version changed; review contract again")
    system_boundary()
    return data


def candidate(data, model, effort, input_bytes):
    require(type(model) is str and model.startswith("opencode/") and "#" not in model, "Exact opencode/model required")
    ident = model.split("/", 1)[1]
    item = exact_free(data["provider"]["models"].get(ident))
    require(item.get("id") == ident, "Model alias mismatch")
    reasoning = item["capabilities"].get("reasoning", {})
    require(type(reasoning) is dict, "Malformed reasoning metadata")
    variants = reasoning.get("effort", []) if reasoning.get("supported") is True else []
    require(type(variants) is list and all(type(v) is str for v in variants), "Malformed reasoning metadata")
    require(effort is None or effort in variants and effort != "default", "Unknown variant; no guessed effort")
    limits = item["limit"]
    capacity = min(limits["context"] - limits["output"] - 8192, limits.get("input", limits["context"]) - 8192)
    require(input_bytes <= capacity, "Context exceeded; no truncation/fallback")
    return item, capacity


def select(cfg, task, tier, explicit=None, effort=None):
    """Re-evaluate fixed policy order for each task, never round-robin or newest."""
    profile = (cfg.get("execution_policy") or {}).get("opencode_free") or {}
    require(task in cfg.get("tasks", {}) and tier in {"T1", "T2", "T3"}, "Task/tier not allowed")
    # Validate task authorization before explicit model overrides too.
    choices = profile.get("candidates", {}).get(tier, [])
    require(type(choices) is list and choices and all(type(x) is str for x in choices), "No approved tier candidates")
    if explicit:
        require(explicit in choices, "Explicit model outside tier candidate allowlist")
        choices = [explicit]
    data = load_catalog(profile)
    for model in choices:
        try:
            candidate(data, model, effort, 0)
            return model
        except (ValueError, TypeError, KeyError):
            continue
    raise ValueError("No confirmed free candidate; no paid fallback")


def authorize(cfg, result, model, effort, tier, input_bytes):
    profile = (cfg.get("execution_policy") or {}).get("opencode_free") or {}
    try:
        require(profile.get("enabled") is True, "OpenCode free execution not enabled")
        require(model in profile.get("candidates", {}).get(tier, []), "Model outside tier allowlist")
        data = load_catalog(profile)
        item, capacity = candidate(data, model, effort, input_bytes)
        result.update(status="executable", reason="Exact-zero catalog; runtime must confirm signed-out isolated CLI",
                      adapter="opencode-free-2.2.5", auth_check="required_at_runtime",
                      catalog={k: data[k] for k in ("source", "fetched_at", "sha256", "version", "binary_sha256")},
                      context={"capacity": capacity, "limits": item["limit"], "input_byte_upper_bound": input_bytes})
        result["billing"] = {"mode": "free_only", "auth": "signed_out_required", "cap_verification": "all_catalog_prices_zero"}
    except (ValueError, TypeError, KeyError, OSError) as exc:
        result.update(status="denied", reason=str(exc))
    return result


def isolated_env(root):
    # Construct from scratch, including PATH/TMP: no shell startup, proxies, API
    # keys, cloud credentials, Node/Bun injection, service variables or hooks.
    env = {"PATH": "/usr/bin:/bin", "LANG": "en_US.UTF-8", "HOME": str(root / "home"),
           "TMPDIR": str(root / "tmp"), "OPENCODE_DISABLE_MODELS_FETCH": "1",
           "OPENCODE_DISABLE_PROJECT_CONFIG": "1", "OPENCODE_DISABLE_CLAUDE_CODE": "1",
           "OPENCODE_FILEWATCHER_DISABLE": "1"}
    for key, directory in (("XDG_DATA_HOME", "data"), ("XDG_CACHE_HOME", "cache"),
                           ("XDG_CONFIG_HOME", "config"), ("XDG_STATE_HOME", "state")):
        env[key] = str(root / directory)
    for key in ("HOME", "TMPDIR", "XDG_DATA_HOME", "XDG_CACHE_HOME", "XDG_CONFIG_HOME", "XDG_STATE_HOME"):
        Path(env[key]).mkdir(mode=0o700)
    return env


def call(cfg, model, prompt, effort, timeout, receipt, human_approved=False):
    import routing_policy as P
    checked = P.plan(cfg, host="opencode", provider="opencode", model=model, effort=effort,
                     tier=receipt.get("tier", "T1"), input_bytes=len(prompt.encode()), human_approved=human_approved)
    if checked["status"] != "executable":
        return 3, "", checked["reason"], ""
    profile = cfg["execution_policy"]["opencode_free"]
    data = load_catalog(profile)
    item, _ = candidate(data, model, effort, len(prompt.encode()))
    with tempfile.TemporaryDirectory(prefix="strateg-opencode-", dir="/tmp") as directory:
        root = Path(directory)
        env = isolated_env(root)
        # Pin the CLI's catalog to one validated route: even auxiliary calls
        # cannot choose a paid/default/newest model or fetch a replacement.
        catalog = root / "catalog.json"
        catalog.write_text(json.dumps({"opencode": {**data["provider"], "models": {item["id"]: item}}}))
        env["OPENCODE_MODELS_PATH"] = str(catalog)
        env["OPENCODE_CONFIG_CONTENT"] = json.dumps({
            "model": model, "autoupdate": False,
            "permissions": [{"action": "*", "resource": "*", "effect": "deny"}],
        })
        binary = data["binary"]
        def run(args, seconds):
            return subprocess.run([binary, *args], cwd=root, env=env, input="", text=True,
                                  capture_output=True, timeout=seconds)
        try:
            require(run(["--version"], 15).stdout.strip() == VERSION, "Runtime version mismatch")
            auth = run(["--standalone", "auth", "list", "--format", "json"], 60)
            require(auth.returncode == 0 and json.loads(auth.stdout) == [], "Signed-out auth not confirmed")
            listed = run(["--standalone", "models"], 60)
            require(listed.returncode == 0 and listed.stdout.split() == [model], "Isolated catalog not exact single free model")
            # Recheck freshness/system boundaries immediately before generation.
            latest = load_catalog(profile)
            require(latest["sha256"] == data["sha256"], "Catalog changed during preflight; restart task")
            candidate(latest, model, effort, len(prompt.encode()))
            receipt.update(auth_check="signed_out_empty_integrations", wrapper_invocations=1)
            receipt["transmitted"] = {"model": model, "effort": effort}
            command = [binary, "--standalone", "run", "--model", model + ("#" + effort if effort else ""),
                       "--format", "json", "--title", "Scoped delegation", "--", prompt]
            proc = subprocess.Popen(command, cwd=root, env=env, stdin=subprocess.DEVNULL,
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            receipt["started"] = True
            try:
                stdout, _ = proc.communicate(timeout=timeout)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.communicate()
                receipt["process_exit_code"] = proc.returncode
                return 1, "", "Timeout; no retry", ""
            receipt["process_exit_code"] = proc.returncode
            return proc.returncode, stdout, "CLI failure; no retry" if proc.returncode else "", ""
        except (OSError, ValueError, subprocess.TimeoutExpired):
            return 3 if not receipt["started"] else 1, "", "Isolation/runtime check failed; no inference retry", ""


def refresh(binary, output):
    """Explicit public metadata GET only; no inference or auth inspection."""
    system_boundary()
    binary = binary.resolve(strict=True)
    with tempfile.TemporaryDirectory(prefix="strateg-opencode-refresh-", dir="/tmp") as directory:
        env = isolated_env(Path(directory))
        proc = subprocess.run([str(binary), "--version"], env=env, cwd=directory,
                              capture_output=True, text=True, timeout=15)
        require(proc.returncode == 0 and proc.stdout.strip() == VERSION, "Unreviewed OpenCode version")
        # System curl handles the platform CA store; no user curlrc/proxy/redirects.
        response = subprocess.run(["/usr/bin/curl", "-q", "--fail", "--silent", "--show-error",
                                   "--proto", "=https", "--max-time", "30", SOURCE],
                                  env=env, cwd=directory, capture_output=True, text=True, timeout=35)
        require(response.returncode == 0, "Official catalog refresh failed")
        provider = json.loads(response.stdout)["opencode"]
        require(provider.get("id") == "opencode" and type(provider.get("models")) is dict, "Malformed catalog")
        data = {"schema": 1, "source": SOURCE, "fetched_at": dt.datetime.now(dt.timezone.utc).isoformat(),
                "provider": provider, "sha256": digest(provider), "binary": str(binary), "version": VERSION,
                "binary_sha256": hashlib.sha256(binary.read_bytes()).hexdigest()}
        # Validate before replacing the operator's snapshot.
        require(any(_is_free(m) for m in provider["models"].values()), "No exact-zero models in catalog")
        output.write_text(json.dumps(data, ensure_ascii=False, indent=2))
    return data


def _is_free(model):
    try:
        exact_free(model)
        return True
    except (ValueError, TypeError, KeyError):
        return False


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("command", choices=["refresh"])
    ap.add_argument("--binary", type=Path, required=True)
    ap.add_argument("--output", type=Path, required=True)
    args = ap.parse_args()
    try:
        data = refresh(args.binary, args.output)
        print(json.dumps({k: data[k] for k in ("source", "fetched_at", "sha256", "version")}))
        return 0
    except (OSError, ValueError, KeyError, TypeError, subprocess.TimeoutExpired) as exc:
        print(str(exc), file=sys.stderr)
        return 3


if __name__ == "__main__":
    sys.exit(main())

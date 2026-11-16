"""Offline, fail-closed authorization shared by every inference transport.

Local configuration is operator authorization, never discovery of an entitlement.
No credentials, account files, CLI probes or network calls are read here.
"""
from __future__ import annotations

import hashlib
import datetime
import json
import math
import os
import sys
from pathlib import Path
from urllib.parse import urlsplit
import urllib.request

HOSTS = {"codex", "claude", "claude-code", "opencode", "zcode", "deepseek-harness",
         "kimi", "qwen", "gemini-cli", "antigravity", "antigravity-ide", "cursor",
         "selfhosted", "decision"}
EFFORTS = {None, "low", "medium", "high", "xhigh", "max"}
# Deliberately no HOME/config/proxy/cloud/API/session inheritance. A future CLI
# adapter must supply its own isolated home and enforce auth before it is enabled.
ENV_NAMES = {"PATH", "LANG", "LC_ALL", "LC_CTYPE", "SYSTEMROOT", "WINDIR", "TMPDIR", "TEMP", "TMP"}

# Known system sources; MDM/cloud absence additionally requires operator review.
CODEX_SYSTEM_FILES = tuple(Path("/etc/codex") / name for name in
                           ("config.toml", "managed_config.toml", "requirements.toml"))
CLAUDE_SYSTEM_DIRS = (Path("/etc/claude-code"), Path("/Library/Application Support/ClaudeCode"))


def claude_profile(cfg):
    """Dedicated native credential profile; auth contents/keychain are never read."""
    profile = (cfg.get("execution_policy") or {}).get("claude_subscription") or {}
    if profile.get("enabled") is not True:
        return profile, "Нет явно включённого Claude subscription profile"
    if os.name != "posix":
        return profile, "Claude adapter v1 поддерживает POSIX boundary"
    required = ("entitlement_confirmed", "overage_disabled", "cli_contract_reviewed",
                "managed_config_absent", "cloud_managed_config_absent")
    if any(profile.get(k) is not True for k in required) or not profile.get("evidence_ref"):
        return profile, "Unknown Claude subscription/config enforcement"
    try:
        binary, home = Path(profile.get("binary") or ""), Path(profile.get("config_dir") or "")
        if not binary.is_absolute() or not home.is_absolute() or not home.is_dir() or home.is_symlink():
            return profile, "Нужны absolute executable и отдельный CLAUDE_CONFIG_DIR"
        if home.resolve() == (Path.home() / ".claude").resolve():
            return profile, "Общий ~/.claude не является dedicated profile"
        if not binary.is_file() or not os.access(binary, os.X_OK) or not profile.get("version"):
            return profile, "Неизвестны executable/version Claude"
        if hashlib.sha256(binary.read_bytes()).hexdigest() != profile.get("binary_sha256"):
            return profile, "Claude binary fingerprint изменился"
        if home.stat().st_uid != os.getuid() or home.stat().st_mode & 0o077:
            return profile, "Claude profile должен принадлежать текущему пользователю и иметь права 0700"
        allowed = {".credentials.json", ".claude.json", "debug", "projects", "todos", "statsig",
                   "cache", "session-env", "telemetry", "history.jsonl", "backups"}
        if any(p.name not in allowed or p.is_symlink() for p in home.iterdir()):
            return profile, "Claude profile содержит settings/extensions/symlink или неизвестный файл"
        store = profile.get("credential_store")
        if store == "file":
            auth = home / ".credentials.json"
            if not auth.is_file() or auth.is_symlink() or auth.stat().st_uid != os.getuid() or auth.stat().st_mode & 0o077:
                return profile, "Нужен заранее настроенный private credential file (0600)"
        elif store != "keychain" or sys.platform != "darwin":
            return profile, "Неизвестный credential store; login/import автоматически не выполняется"
        if any(p.exists() or p.is_symlink() for p in CLAUDE_SYSTEM_DIRS):
            return profile, "Найдены Claude managed settings: нужна отдельная проверка"
    except OSError:
        return profile, "Невозможно проверить dedicated Claude profile"
    return profile, None


def codex_profile(cfg):
    """Inspect non-secret profile boundaries, never read/copy auth.json.

    A dedicated pre-authenticated CODEX_HOME is supplied by the operator. Runtime
    login-status checks the credential type with the same environment/config flags.
    """
    profile = (cfg.get("execution_policy") or {}).get("codex_subscription") or {}
    if profile.get("enabled") is not True:
        return profile, "Нет явно включённого Codex subscription profile"
    if os.name != "posix":
        return profile, "Codex adapter v1 проверяет только POSIX config boundary"
    required = ("entitlement_confirmed", "overage_disabled", "cli_contract_reviewed",
                "managed_config_absent", "cloud_managed_config_absent")
    if any(profile.get(k) is not True for k in required) or not profile.get("evidence_ref"):
        return profile, "Unknown subscription/profile enforcement: требуется проверка оператора"
    try:
        binary = Path(profile.get("binary") or "")
        home = Path(profile.get("codex_home") or "")
        if not binary.is_absolute() or not home.is_absolute() or home.is_symlink():
            return profile, "Нужны абсолютные binary и отдельный CODEX_HOME без symlink"
        if not binary.is_file() or not os.access(binary, os.X_OK):
            return profile, "Проверенный executable недоступен"
        if hashlib.sha256(binary.read_bytes()).hexdigest() != profile.get("binary_sha256"):
            return profile, "Binary fingerprint изменился: повторите проверку CLI contract"
        if not profile.get("version") or not home.is_dir():
            return profile, "Неизвестны CLI version или dedicated CODEX_HOME"
        if home.resolve() == (Path.home() / ".codex").resolve():
            return profile, "Нельзя использовать общий ~/.codex как isolated profile"
        # Non-secret runtime files may be generated by exec; config/extensions
        # and any new unknown entry require explicit review before another call.
        allowed = {"auth.json", "models_cache.json", "version.json", "log", "logs", "sessions",
                   "tmp", ".tmp", "state_5.sqlite", "state_5.sqlite-wal", "state_5.sqlite-shm",
                   "history.jsonl", ".codex-global-state.json", "shell_snapshots"}
        if any(p.name not in allowed or p.is_symlink() for p in home.iterdir()):
            return profile, "CODEX_HOME содержит config/extensions/symlink или неизвестный файл"
        auth = home / "auth.json"
        if not auth.is_file() or auth.is_symlink():
            return profile, "Нет заранее настроенного file auth; автоматический login запрещён"
        if home.stat().st_uid != os.getuid() or auth.stat().st_uid != os.getuid():
            return profile, "Профиль принадлежит другому пользователю"
        if home.stat().st_mode & 0o077 or auth.stat().st_mode & 0o077:
            return profile, "Нужны закрытые права: CODEX_HOME 0700 и auth.json 0600"
        if any(p.exists() or p.is_symlink() for p in CODEX_SYSTEM_FILES):
            return profile, "Найдена system/managed config: автоматический adapter требует отдельной проверки"
    except OSError:
        return profile, "Невозможно проверить isolated profile"
    return profile, None


def context_check(result, bounds, input_bytes):
    values = [bounds.get(k) for k in ("model_limit", "plan_limit", "user_limit", "output_reserve", "system_reserve")]
    if any(not isinstance(v, int) or isinstance(v, bool) or v <= 0 for v in values):
        return "Неизвестны границы контекста или резервы"
    capacity = min(values[:3]) - values[3] - values[4]
    result["context"] = {"input_byte_upper_bound": input_bytes, "capacity": capacity, **bounds}
    if input_bytes > capacity:
        return "Контекст превышен; разделите вход, дорогого fallback и усечения нет"
    return None


def valid_allowlist(value, *, efforts=False):
    if type(value) is not list or not value:
        return False
    if efforts:
        return all((item is None or type(item) is str) and item in EFFORTS for item in value)
    return all(type(item) is str and bool(item.strip()) for item in value)


def audit_refusal(project, step, action, tier, receipt):
    """Runtime only; one redacted receipt and log row, without any transport."""
    import ledger as L
    receipt["transmitted"] = {"model": None, "effort": None}
    receipt["observed"] = {"provider": None, "model": None, "effort": None}
    receipt["result"] = "not_started"
    receipt["started"] = False
    receipt["process_exit_code"] = None
    receipt["wrapper_invocations"] = 0
    receipt["inference_calls"] = 0
    receipt["exit_code"] = 3
    directory = project / "state" / "delegated"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / (datetime.datetime.now().strftime("%Y%m%d-%H%M%S%f") + "_refusal.receipt.json")
    path.write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding="utf-8")
    req = receipt["requested"]
    L.cmd_log(project, step, "routing refusal: " + action, tier,
              f"{req.get('host')}:{req.get('model')}",
              f"{receipt['status']}: {receipt['reason']} → {path.relative_to(project)}", "0 inference calls")


def child_env(environ=None):
    env = os.environ if environ is None else environ
    return {k: v for k, v in env.items() if k in ENV_NAMES}


def endpoint_ok(endpoint):
    if not isinstance(endpoint, str):
        return False
    try:
        u = urlsplit(endpoint)
        return bool(u.hostname and not u.username and not u.password and not u.query and not u.fragment
                    and (u.scheme == "https" or (u.scheme == "http" and u.hostname in {"localhost", "127.0.0.1", "::1"})))
    except ValueError:
        return False


def positive_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value > 0


def plan(cfg, *, host, provider, model, effort=None, tier="T1", endpoint=None,
         input_bytes=0, human_approved=False, key_present=False):
    """Return an explanation, not a launch token. Transports recheck at entry.

    HTTP API authorization requires an explicit operator attestation of a separately
    enforced provider/account cap. We cannot verify that cap locally and say so in
    the receipt; the local budget value is NOT an implemented spending meter.
    """
    policy = cfg.get("execution_policy") or {}
    mode = policy.get("mode", "subscription_only")
    result = {"status": "denied", "reason": "", "max_attempts": 1,
              "max_attempts_scope": "wrapper_invocations", "wrapper_invocations": 0,
              "internal_transport_retries": "unknown_version_dependent" if host not in {"decision", "selfhosted"} else "disabled",
              "started": False, "process_exit_code": None,
              "requested": {"host": host, "provider": provider, "model": model, "effort": effort},
              "transmitted": {"model": None, "effort": None},
              "observed": {"provider": None, "model": None, "effort": None},
              "billing": {"mode": mode, "auth": None, "cap_verification": "unknown"},
              "input_bytes": input_bytes, "endpoint": endpoint if endpoint_ok(endpoint) else None,
              "policy_digest": hashlib.sha256(json.dumps(policy, sort_keys=True).encode()).hexdigest()}

    def stop(reason, status="denied"):
        result.update(status=status, reason=reason)
        return result

    if tier == "T0":
        return stop("T0 выполняется кодом; T4 не делегируется автоматически без standing approval")
    if tier == "T4":
        from adaptive_execution import t4_settings, t4_available
        if not t4_settings(cfg):
            return stop("T4 не делегируется автоматически без standing approval и общего лимита")
        if host not in {'codex', 'claude', 'claude-code'}:
            return stop('T4 bounded transport is implemented only for Codex/Claude')
        if not t4_available(cfg):
            return stop('T4 approved scope quota exhausted or usage record unavailable')
        result['t4_approval_ref'] = t4_settings(cfg)['approval_ref']
    if effort not in EFFORTS or effort == "ultra":
        return stop("Неизвестный или запрещённый effort")
    if effort == "max" and not human_approved:
        return stop("max требует отдельного согласия человека")
    if host not in HOSTS:
        return stop("Неизвестный host: исполняемый adapter отсутствует")
    if host == "cursor":
        allowed = ((cfg.get("environments") or {}).get("cursor") or {}).get("model_allowlist", [])
        if allowed != [] and not valid_allowlist(allowed):
            return stop("Cursor model_allowlist должен быть list[str] с точными ID")
        if not model or not allowed:
            return stop("Cursor: нужен проверенный точный model_allowlist; Composer/Grok — лишь предпочтения", "manual")
        if model not in allowed or not model.lower().startswith(("composer", "grok")):
            return stop("Cursor: модель вне явного Composer/Grok allowlist; Auto/third-party fallback запрещён")
    if not model or not provider:
        return stop("Неизвестны provider/model", "manual")
    limit = policy.get("max_input_bytes", 128000)
    if not isinstance(limit, int) or isinstance(limit, bool) or limit <= 0:
        return stop("Некорректный max_input_bytes")
    if input_bytes > limit:
        return stop("Вход превышает max_input_bytes; разделите материалы, усечения нет")
    if host == "opencode":
        if provider != "opencode":
            return stop("OpenCode adapter supports only signed-out opencode free routes")
        import opencode_free
        return opencode_free.authorize(cfg, result, model, effort, tier, input_bytes)
    if mode == "subscription_only":
        result["billing"]["auth"] = "subscription"
        if (host == "codex" and provider == "openai") or (host in {"claude", "claude-code"} and provider == "anthropic"):
            is_codex = host == "codex"
            profile, error = codex_profile(cfg) if is_codex else claude_profile(cfg)
            if error:
                return stop(error, "manual")
            if not valid_allowlist(profile.get("models")) or not valid_allowlist(profile.get("efforts"), efforts=True):
                return stop("Subscription models/efforts должны быть непустыми точными списками")
            if model not in profile["models"] or effort not in profile["efforts"]:
                return stop("Model/effort вне подтверждённого subscription allowlist")
            error = context_check(result, profile.get("context") or {}, input_bytes)
            if error:
                return stop(error)
            result["billing"].update(auth="chatgpt" if is_codex else "claude.ai", cap_verification="included_quota_operator_confirmed")
            result["adapter"] = "codex-chatgpt-v1" if is_codex else "claude-subscription-v1"
            result["auth_check"] = "required_at_runtime"
            return stop("Подтверждённый профиль; runtime проверит version и subscription auth перед запуском", "executable")
        return stop("Не подтверждены изоляция account config, подписочное entitlement и запрет overage; "
                    "используйте проверенный вручную host. CLI/ключ не являются разрешением", "manual")
    if mode != "api_opt_in":
        return stop("Неизвестный billing mode")
    api = policy.get("api") or {}
    if api.get("enabled") is not True:
        return stop("API выключен: нужен explicit opt-in")
    if host not in {"selfhosted", "decision"}:
        return stop("API через CLI не разрешён; adapter/auth precedence не проверены", "manual")
    if not endpoint_ok(endpoint):
        return stop("Endpoint должен быть HTTPS (HTTP только loopback), без credentials/query/fragment")
    grants = api.get("grants", [])
    if type(grants) is not list:
        return stop("API grants должен быть списком объектов")
    if any(type(g) is not dict or not valid_allowlist(g.get("models"))
           or not valid_allowlist(g.get("efforts"), efforts=True) for g in grants):
        return stop("API models/efforts должны быть непустыми точными списками")
    grant = next((g for g in grants if isinstance(g, dict) and g.get("host") == host
                  and g.get("provider") == provider and g.get("endpoint") == endpoint
                  and model in g["models"]), None)
    if not grant:
        return stop("Нет разрешения для точного host/provider/endpoint/model")
    if not key_present:
        return stop("API требует отдельный ключ, но ключ сам не даёт разрешение")
    zero_budget = (type(grant.get("budget_usd")) in {int, float}
                   and grant["budget_usd"] == 0 and host == "decision"
                   and provider == "openrouter"
                   and endpoint == "https://openrouter.ai/api/alpha/decisions"
                   and model == "perplexity/pplx-decider-v1.3:free")
    if not positive_number(grant.get("budget_usd")) and not zero_budget:
        return stop("Нужен конечный положительный budget_usd")
    cap = grant.get("external_cap") or {}
    if (cap.get("confirmed_by_operator") is not True or not cap.get("evidence_ref")
            or not (positive_number(cap.get("amount_usd")) or
                    (zero_budget and type(cap.get("amount_usd")) in {int, float} and cap["amount_usd"] == 0))
            or cap["amount_usd"] > grant["budget_usd"]):
        return stop("Unknown enforcement: нужен отдельно установленный внешний лимит в пределах бюджета "
                    "и подтверждение оператора; локальная оценка не hard cap", "manual")
    bounds = grant.get("context") or {}
    # Conservative byte bound, not a tokenizer promise. Includes serialized request
    # and separately reserved server/system/reasoning/output overhead.
    error = context_check(result, bounds, input_bytes)
    if error:
        return stop(error, "manual")
    if effort not in grant["efforts"]:
        return stop("Effort не входит в подтверждённый allowlist модели")
    result["billing"].update(auth="api_key", budget_usd=grant["budget_usd"],
                             cap_verification="operator_attestation_not_locally_verified")
    return stop("Явное API-разрешение; внешний cap подтверждён оператором, не измерен этим кодом", "executable")


def observe(receipt, data):
    """Only explicit response fields are evidence. Missing fields remain null."""
    observed = {k: data.get(k) if isinstance(data.get(k), str) else None
                for k in ("provider", "model", "effort")}
    receipt["observed"] = observed
    contract = {"provider": receipt["requested"].get("provider"), **receipt["transmitted"]}
    # Exact catalog identity, verified through OpenRouter /api/v1/models 2026-11-16.
    # Do not strip suffixes or accept arbitrary version/prefix matches.
    canonical_match = (receipt.get("endpoint") == "https://openrouter.ai/api/alpha/decisions"
        and receipt["requested"].get("host") == "decision"
        and receipt["requested"].get("provider") == "openrouter"
        and contract.get("model") == "perplexity/pplx-decider-v1.3:free"
        and observed["model"] == "perplexity/pplx-decider-v1.3-20261112")
    if canonical_match:
        contract["model"] = observed["model"]
        receipt["model_identity_basis"] = "exact_openrouter_catalog_alias_2026-11-16"
    mismatch = [k for k, v in observed.items() if v is not None and
                contract.get(k) is not None and v != contract[k]]
    if receipt.get("billing", {}).get("budget_usd") == 0:
        usage = data.get("usage")
        cost = usage.get("cost") if isinstance(usage, dict) else None
        if type(cost) not in {int, float} or cost != 0:
            mismatch.append("cost")
    requested = receipt["requested"]
    receipt["request_satisfaction"] = {
        k: None if observed[k] is None or requested.get(k) is None else observed[k] == requested[k]
        for k in ("model", "effort")}
    if canonical_match:
        receipt["request_satisfaction"]["model"] = True
    receipt["integrity"] = "mismatch" if mismatch else "unconfirmed" if observed["model"] is None else "reported"
    return not mismatch


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def http_open(request, timeout):
    """Endpoint grant cannot be overridden by env proxies or an HTTP redirect."""
    return urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect()).open(request, timeout=timeout)

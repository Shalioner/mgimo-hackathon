"""Offline, fail-closed authorization shared by every inference transport.

Local configuration is operator authorization, never discovery of an entitlement.
No credentials, account files, CLI probes or network calls are read here.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
from urllib.parse import urlsplit
import urllib.request

HOSTS = {"codex", "claude", "claude-code", "opencode", "zcode", "deepseek-harness",
         "kimi", "qwen", "gemini-cli", "antigravity", "antigravity-ide", "cursor",
         "selfhosted", "decision"}
EFFORTS = {None, "low", "medium", "high", "xhigh", "max"}
# Deliberately no HOME/config/proxy/cloud/API/session inheritance. A future CLI
# adapter must supply its own isolated home and enforce auth before it is enabled.
ENV_NAMES = {"PATH", "LANG", "LC_ALL", "LC_CTYPE", "SYSTEMROOT", "WINDIR", "TMPDIR", "TEMP", "TMP"}


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
              "requested": {"host": host, "provider": provider, "model": model, "effort": effort},
              "transmitted": {"model": None, "effort": None},
              "observed": {"provider": None, "model": None, "effort": None},
              "billing": {"mode": mode, "auth": None, "cap_verification": "unknown"},
              "input_bytes": input_bytes, "endpoint": endpoint if endpoint_ok(endpoint) else None,
              "policy_digest": hashlib.sha256(json.dumps(policy, sort_keys=True).encode()).hexdigest()}

    def stop(reason, status="denied"):
        result.update(status=status, reason=reason)
        return result

    if tier in {"T0", "T4"}:
        return stop("T0 выполняется кодом; T4 не делегируется автоматически")
    if effort not in EFFORTS or effort == "ultra":
        return stop("Неизвестный или запрещённый effort")
    if effort == "max" and not human_approved:
        return stop("max требует отдельного согласия человека")
    if host not in HOSTS:
        return stop("Неизвестный host: исполняемый adapter отсутствует")
    if host == "cursor":
        allowed = ((cfg.get("environments") or {}).get("cursor") or {}).get("model_allowlist") or []
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
    if mode == "subscription_only":
        result["billing"]["auth"] = "subscription"
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
    grants = api.get("grants") or []
    grant = next((g for g in grants if isinstance(g, dict) and g.get("host") == host
                  and g.get("provider") == provider and g.get("endpoint") == endpoint
                  and model in (g.get("models") or [])), None)
    if not grant:
        return stop("Нет разрешения для точного host/provider/endpoint/model")
    if not key_present:
        return stop("API требует отдельный ключ, но ключ сам не даёт разрешение")
    if not positive_number(grant.get("budget_usd")):
        return stop("Нужен конечный положительный budget_usd")
    cap = grant.get("external_cap") or {}
    if (cap.get("confirmed_by_operator") is not True or not cap.get("evidence_ref")
            or not positive_number(cap.get("amount_usd")) or cap["amount_usd"] > grant["budget_usd"]):
        return stop("Unknown enforcement: нужен отдельно установленный внешний лимит в пределах бюджета "
                    "и подтверждение оператора; локальная оценка не hard cap", "manual")
    bounds = grant.get("context") or {}
    values = [bounds.get(k) for k in ("model_limit", "plan_limit", "user_limit", "output_reserve", "system_reserve")]
    if any(not isinstance(v, int) or isinstance(v, bool) or v <= 0 for v in values):
        return stop("Неизвестны границы контекста или резервы", "manual")
    # Conservative byte bound, not a tokenizer promise. Includes serialized request
    # and separately reserved server/system/reasoning/output overhead.
    capacity = min(values[:3]) - values[3] - values[4]
    result["context"] = {"input_byte_upper_bound": input_bytes, "capacity": capacity, **bounds}
    if input_bytes > capacity:
        return stop("Контекст превышен; разделите вход, дорогого fallback и усечения нет")
    if effort not in (grant.get("efforts") or [None]):
        return stop("Effort не входит в подтверждённый allowlist модели")
    result["billing"].update(auth="api_key", budget_usd=grant["budget_usd"],
                             cap_verification="operator_attestation_not_locally_verified")
    return stop("Явное API-разрешение; внешний cap подтверждён оператором, не измерен этим кодом", "executable")


def observe(receipt, data):
    """Only explicit response fields are evidence. Missing fields remain null."""
    observed = {k: data.get(k) if isinstance(data.get(k), str) else None
                for k in ("provider", "model", "effort")}
    receipt["observed"] = observed
    mismatch = [k for k, v in observed.items() if v is not None and
                receipt["requested"].get(k) is not None and v != receipt["requested"][k]]
    receipt["integrity"] = "mismatch" if mismatch else "unconfirmed" if observed["model"] is None else "reported"
    return not mismatch


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def http_open(request, timeout):
    """Endpoint grant cannot be overridden by env proxies or an HTTP redirect."""
    return urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect()).open(request, timeout=timeout)

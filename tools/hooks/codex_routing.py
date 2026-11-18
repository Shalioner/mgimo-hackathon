#!/usr/bin/env python3
"""Codex PreToolUse adapter: route fresh native subagents, never the parent turn.

Codex UserPromptSubmit cannot override the main model. Only explicit no-history
spawn_agent calls are rewritten; full forks and named roles retain host settings.
"""
from __future__ import annotations

import copy
from fnmatch import fnmatchcase
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

TOOLS = {'spawn_agent', 'collaborationspawn_agent', 'collaboration.spawn_agent',
         'functions.collaboration.spawn_agent', 'mcp__collaboration__spawn_agent'}
MAX_INPUT_BYTES = 65536


def _context(reason):
    return {'hookSpecificOutput': {'hookEventName': 'PreToolUse',
                                  'additionalContext': 'Стратег: модель субагента не изменена. ' + reason}}


def _fallback(tool_input, reason):
    # Resolve the original route independently of the failed classifier. T4 IDs
    # may be renamed or configured locally; the Astra pattern is only a backstop.
    model = tool_input.get('model')
    try:
        import ledger as L
        import models as M
        from host_routing import host_settings
        cfg = L.load_routing()
        settings = host_settings(cfg)
        if not settings or 'codex' not in settings['enabled_hosts']:
            return _context('Автороутинг отключён; выбор модели и разрешения остаются у среды.')
        t4_models = {L.model_for(cfg, 'openai', 'T4'), *M.tier_models(cfg, 'openai', 'T4')}
        entry = M.catalog_entry(model, M.load_catalog()) if isinstance(model, str) else None
        is_t4 = isinstance(model, str) and (
            model in t4_models or fnmatchcase(model, 'gpt-*-astra') or
            bool(entry and entry.get('family') == 'openai' and entry.get('tier') == 'T4'))
    except Exception:
        return _deny_input('Стратег: не удалось проверить уровень исходной модели; субагент не запущен.')
    if is_t4:
        return {'hookSpecificOutput': {'hookEventName': 'PreToolUse',
                                      'permissionDecision': 'deny',
                                      'permissionDecisionReason': 'Стратег: исходный маршрут T4 не авторизован; субагент не запущен. ' + reason}}
    return _context(reason)


def handle(payload, *, decide=None, authorize=None):
    """Return the exact command-hook stdout object. Unrelated calls are no-ops."""
    if not isinstance(payload, dict) or payload.get('hook_event_name') != 'PreToolUse':
        return {}
    if payload.get('tool_name') not in TOOLS:
        return {}
    tool_input = payload.get('tool_input')
    if not isinstance(tool_input, dict):
        return _context('Некорректный вход spawn_agent.')
    if tool_input.get('fork_turns') != 'none' or tool_input.get('fork_context') is True:
        return _context('Полный или неуказанный fork сохраняет модель родителя; для автороутинга нужен fork_turns="none".')
    if tool_input.get('agent_type') not in (None, 'default'):
        return _context('Именованная роль сохраняет свои настройки модели.')
    message = tool_input.get('message')
    if not isinstance(message, str) or not message.strip():
        return _fallback(tool_input, 'Нет текстового задания для классификации.')
    if len(message.encode('utf-8')) > 24000:
        return _fallback(tool_input, 'Задание превышает лимит классификатора; скрытое усечение не применяется.')
    try:
        if decide is None:
            from host_routing import decide
        result = decide({'host': 'codex', 'event': 'agent.spawn', 'request': message,
                         'context': ''}, execute=True)
    except Exception:
        # Preserve ordinary host routes, but never bypass the T4 dispatch gate.
        return _fallback(tool_input, 'Классификатор недоступен.')
    if isinstance(result, dict) and result.get('status') == 'disabled':
        return _context('Автороутинг отключён; выбор модели и разрешения остаются у среды.')
    if not isinstance(result, dict) or result.get('status') != 'selected':
        return _fallback(tool_input, 'Классификация не дала разрешённого уверенного маршрута.')
    model, effort = result.get('model'), result.get('effort')
    if not isinstance(model, str) or not model.strip() or len(model) > 200:
        return _fallback(tool_input, 'Классификатор вернул некорректную модель.')
    if not isinstance(effort, str) or effort not in {'low', 'medium', 'high', 'xhigh'}:
        return _fallback(tool_input, 'Классификатор вернул неподдерживаемый effort.')
    try:
        if authorize is None:
            from host_routing import authorize
        authorized = authorize(result)
    except Exception:
        authorized = {'status': 'unavailable'}
    if not isinstance(authorized, dict) or authorized.get('status') != 'authorized':
        return {'hookSpecificOutput': {'hookEventName': 'PreToolUse', 'permissionDecision': 'deny',
                                      'permissionDecisionReason': 'Стратег: разрешение выбранного маршрута истекло или лимит исчерпан; субагент не запущен.'}}
    updated = copy.deepcopy(tool_input)
    updated.update(model=model, reasoning_effort=effort)
    # Never emit permissionDecision=allow: existing approvals remain host-owned.
    return {'hookSpecificOutput': {'hookEventName': 'PreToolUse', 'updatedInput': updated,
                                  'additionalContext': f'Стратег: для этого субагента выбран {model}, effort {effort}.'}}


def main():
    try:
        raw = sys.stdin.buffer.read(MAX_INPUT_BYTES + 1)
        if len(raw) > MAX_INPUT_BYTES:
            output = _deny_input('Hook input exceeds 65536 bytes; route cannot be safely checked.')
        else:
            payload = json.loads(raw)
            output = handle(payload) if isinstance(payload, dict) else _deny_input('Expected a JSON object.')
    except Exception:
        # Installed on spawn matchers: failure cannot preserve an unknown model.
        output = _deny_input('Hook input could not be verified; retry with a valid task.')
    print(json.dumps(output, ensure_ascii=False))
    return 0


def _deny_input(reason):
    return {'hookSpecificOutput': {'hookEventName': 'PreToolUse',
                                  'permissionDecision': 'deny',
                                  'permissionDecisionReason': reason}}


if __name__ == '__main__':
    raise SystemExit(main())

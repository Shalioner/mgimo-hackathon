#!/usr/bin/env python3
"""Native Codex UserPromptSubmit: automatic classification, not main-model override."""
import json
import os
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import host_routing as H


# Only whole-message acknowledgements. A question mark, an added task, or a
# continuation instruction must still reach the normal route. This does not
# authorize anything or infer a previous task's model from conversation text.
ACKNOWLEDGEMENT = re.compile(r'\s*(?:спасибо|ок|окей|понятно)[.!…]*\s*', re.IGNORECASE)


def handle(payload, decide=None):
    if os.environ.get('STRATEGIST_ROUTED_APP_SERVER') == '1':
        return {}
    if not isinstance(payload, dict) or payload.get('hook_event_name') != 'UserPromptSubmit':
        return {}
    if payload.get('agent_id') or payload.get('agent_type'):
        return {}  # A delegated initial prompt must not recursively delegate itself.
    prompt = payload.get('prompt')
    if not isinstance(prompt, str) or not prompt.strip():
        return {}
    if (ACKNOWLEDGEMENT.fullmatch(prompt)
            and not any(payload.get(key) for key in ('attachments', 'files', 'images', 'input', 'content'))):
        return {}
    # This hook is project-scoped. Preserve account auth and native permissions.
    result = (decide or H.decide)({'host': 'codex', 'request': prompt, 'context': '',
                                  'event': 'user.prompt'}, execute=True)
    if result.get('status') != 'selected':
        message = ('Автоматический маршрутизатор: ' + str(result.get('status', 'unavailable')) +
                   '. Выбранная модель не запускалась; не заявляй автоматическое переключение.')
    else:
        route = json.dumps({k: result.get(k) for k in ('tier', 'model', 'effort')}, ensure_ascii=False)
        message = ('Классификатор автоматически выбрал маршрут подзадачи: ' + route + '. '
                   'Этот hook не меняет основную модель Codex. Для исполнения передай самостоятельное задание '
                   'новому spawn_agent с указанными model и reasoning_effort, fork_turns="none"; '
                   'дай ему нужные данные, не весь диалог. Перед запуском native PreToolUse повторно проверит маршрут. '
                   'Не меняй фиксированную модель именованной роли и не пересоздавай полный fork ради override. '
                   'Если делегирование недоступно, явно укажи это вместо утверждения о выполненном переключении.')
    return {'hookSpecificOutput': {'hookEventName': 'UserPromptSubmit', 'additionalContext': message}}


def main():
    try:
        raw = sys.stdin.buffer.read(65537)
        if len(raw) > 65536:
            raise ValueError('Event too large')
        output = handle(json.loads(raw))
    except (ValueError, TypeError, OSError, KeyError):
        output = {'hookSpecificOutput': {'hookEventName': 'UserPromptSubmit', 'additionalContext':
                  'Маршрутизатор недоступен: автоматическое переключение модели не выполнено.'}}
    print(json.dumps(output, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    import sys as _sys
    for _stream in (_sys.stdin, _sys.stdout, _sys.stderr):  # Windows: pipes/console default to cp1251
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    raise SystemExit(main())

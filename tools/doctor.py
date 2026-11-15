#!/usr/bin/env python3
"""Offline installation diagnosis and profile-scoped first-run guide. No inference."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import yaml

import ledger as L
from install_host_routing import hooks_ready

PURPOSES = {
    'problem-framing': 'постановка задачи', 'research-plan': 'план исследования',
    'source-intake': 'разбор файлов и источников', 'situation-map': 'факты, пробелы и гипотезы',
    'alternatives': 'сравнение вариантов', 'strategy': 'продуктовая и коммуникационная стратегия',
    'validation-plan': 'план проверки решения', 'decision-support': 'сопровождение решения',
    'impact-analysis': 'пересмотр при новых данных', 'interviews': 'подготовка и разбор интервью',
    'claim-verifier': 'проверка утверждений и ссылок', 'self-audit': 'разбор собственных ошибок',
    'model-refresh': 'обновление правил выбора моделей',
}


def inventory(root=L.ROOT):
    rows = []
    for path in sorted((Path(root) / 'skills').glob('*/SKILL.md')):
        text = path.read_text(encoding='utf-8')
        parts = text.split('---', 2)
        meta = yaml.safe_load(parts[1]) if text.startswith('---') and len(parts) == 3 else {}
        meta = meta if isinstance(meta, dict) else {}
        name = str(meta.get('name') or path.parent.name)
        rows.append({'name': name, 'purpose': PURPOSES.get(name, str(meta.get('description') or 'Назначение не описано')),
                     'description': str(meta.get('description') or ''), 'path': str(path.relative_to(root))})
    return rows


def diagnose(host, *, root=L.ROOT, cfg=None):
    root = Path(root)
    config_error = False
    if cfg is None:
        try:
            cfg = L.load_routing()
        except (OSError, ValueError, TypeError, yaml.YAMLError):
            cfg, config_error = {}, True
    routing = (cfg.get('automatic_routing') or {}) if isinstance(cfg, dict) else {}
    native = (routing.get('host_routing') or {}) if isinstance(routing, dict) else {}
    if not isinstance(routing, dict) or not isinstance(native, dict):
        routing, native, config_error = {}, {}, True
    enabled = native.get('enabled_hosts', [])
    enabled = enabled if isinstance(enabled, list) else []
    native_host = 'codex' if host == 'codex-app' else host
    files = {'codex': root / '.codex/hooks.json', 'codex-app': root / '.codex/hooks.json', 'orca': root / '.codex/hooks.json',
             'claude-code': root / '.claude/settings.local.json'}
    registered = False
    try:
        settings = json.loads(files[host].read_text()) if host in files else {}
        if host in {'codex', 'codex-app', 'orca'}:
            registered = hooks_ready(settings, root)
        elif host == 'claude-code':
            paths = settings.get('env', {}).get('CLAUDE_CODE_PLUGIN_DIRS', '').split(os.pathsep)
            registered = str(root / 'plugins/strategist-routing') in paths and (root / 'plugins/strategist-routing/hooks/register.mjs').is_file()
    except (ValueError, OSError, TypeError, AttributeError):
        pass
    if host == 'claude-code':
        mechanism = 'Claude Code: автоматическая маршрутизация через мод turn.start/turn.step.'
        limitation = 'Регистрация мода не доказывает его загрузку; нужен пробный запрос в вашей сессии Claude.'
    elif host in {'codex', 'codex-app', 'orca'}:
        mechanism = 'Codex: D1 выбирает маршрут; hooks применяют модель субагента, app-server адаптер — основную модель.'
        limitation = 'Основная модель обычного Codex/Orca chat через prompt-hook не переключается. Для Codex проверьте доверие hooks (Review hooks / Settings → Hooks в приложении).'
    else:
        mechanism = 'Автороутинг в этой среде не проверен.'
        limitation = 'Нужна проверка возможностей среды и настройка адаптера; наличие CLI рядом не подтверждает работу IDE.'
    state = 'configured_not_verified' if registered and native_host in enabled else 'needs_setup' if host in files else 'unverified'
    classifier = routing.get('classifier')
    if classifier == 'local-d1':
        decision = 'Выбран локальный D1; доступность сервиса и качество ответов этим осмотром не проверены.'
    elif classifier == 'provider' and routing.get('decision_provider'):
        decision = 'Выбран System One API-провайдер; разрешения, доступность и совместимость требуют проверки.'
    else:
        decision = 'Decision-модель не подтверждена: можно подключить System One API (например Jev) или локальный D1.'
    actions = []
    if config_error:
        actions.append('Исправить конфигурацию маршрутизации; диагностика не смогла её прочитать.')
    if state == 'needs_setup':
        actions.append('Проверить tools/install_host_routing.py и настройку host_routing; применять после сверки с этой средой.')
    if state == 'unverified':
        actions.append('Определить точное приложение и версию, проверить официальные hooks/MCP и способ применения модели.')
    actions += ['Проверить decision-модель без запуска платного API и без чтения ключей.',
                'В отдельном пробном запросе подтвердить выбранную модель и результат; до этого не считать автороутинг рабочим.']
    python = root / '.venv' / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
    return {'host': host, 'skills': inventory(root), 'python_ready': python.is_file() and os.access(python, os.X_OK),
            'routing': {'status': state, 'registered': registered, 'enabled': native_host in enabled,
                        'trust': 'requires_host_review' if host in {'codex', 'codex-app', 'orca'} else 'not_checked',
                        'mechanism': mechanism, 'limitation': limitation},
            'decision': decision, 'next_steps': actions, 'inference_calls': 0, 'changes': []}


def welcome(report):
    installed = {row['name']: row for row in report['skills']}
    ordered = [installed[key] for key in PURPOSES if key in installed]
    ordered += [row for row in report['skills'] if row['name'] not in PURPOSES]
    purposes = '; '.join(row['purpose'] for row in ordered) or 'предметные навыки не найдены'
    route = report['routing']
    status = 'настройка обнаружена, запуск в вашей среде ещё нужно проверить' if route['status'] == 'configured_not_verified' else 'требуется настройка или проверка среды'
    mechanism = ('Автороутинг — через мод Claude Code.' if report['host'] == 'claude-code' else
                 'В Codex автороутинг зависит от подключения адаптера; обычный prompt-hook не меняет основную модель.'
                 if report['host'] in {'codex', 'codex-app', 'orca'} else 'Автороутинг в этой среде не проверен.')
    return ('Привет! Я Стратег.\n\n'
            'Начните обычным сообщением: опишите задачу или приложите файлы — я помогу её выполнить '
            'либо проведу углублённое исследование на основе ваших материалов.\n\n'
            f'Установленные навыки: {purposes}.\n\n'
            f'{mechanism} Сейчас: {status}.\n'
            f'{report["decision"]}\n'
            'Для локальной работы на macOS с Apple Silicon используется D1: '
            '[исходная модель](https://huggingface.co/LiquidAI/d1-2B), '
            '[MLX 8-bit](https://huggingface.co/LiquidAI/d1-2B-MLX-8bit). '
            'Если исходная версия не помещается в память, выбирайте 8-bit — '
            'именно она проверена в этой поставке. Ниже 8-bit пока не рекомендуем: качество не проверено. '
            'Установка и границы поддержки: docs/D1_MACOS.md.\n\n'
            'Напишите «покажи навыки» для подробностей или «проверь и настрой под мой компьютер» '
            'для диагностики. В непроверенной среде сначала выясним, что доступно и что нужно донастроить.')


def profile_state_dir(environ=None):
    env = os.environ if environ is None else environ
    # One user profile, not one checkout, case, thread or app version.
    return Path(env.get('XDG_STATE_HOME') or Path.home() / '.local/state') / 'strategist'


def first_run(report, *, state_dir=None):
    directory = Path(state_dir) if state_dir else profile_state_dir()
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    marker = directory / 'welcome-offered-v1.json'
    try:
        fd = os.open(marker, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return ''
    with os.fdopen(fd, 'w') as file:
        json.dump({'schema': 1, 'status': 'offered', 'host': report['host']}, file)
    return welcome(report)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['doctor', 'welcome', 'skills'])
    parser.add_argument('--host', default=None, help='Explicit host; do not infer desktop support from CLI installation')
    parser.add_argument('--once', action='store_true', help='Offer welcome once per user profile')
    parser.add_argument('--json', action='store_true')
    args = parser.parse_args(argv)
    report = diagnose(args.host or L.detect_env()[0])
    if args.command == 'welcome':
        print(first_run(report) if args.once else welcome(report))
    elif args.json:
        print(json.dumps(report['skills'] if args.command == 'skills' else report, ensure_ascii=False, indent=2))
    elif args.command == 'skills':
        print('\n'.join(f'- {s["name"]}: {s["purpose"]}.' for s in report['skills']))
    else:
        print(f'Среда: {report["host"]}\nНавыков: {len(report["skills"])}\n'
              f'Маршрутизация: {report["routing"]["status"]}\n{report["routing"]["mechanism"]}\n'
              f'{report["routing"]["limitation"]}\n{report["decision"]}\nДалее:\n' +
              '\n'.join('- ' + step for step in report['next_steps']))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

import argparse
import concurrent.futures
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import time


MODEL = 'gpt-6.2-sol'
EFFORT = 'medium'
BRIEF = '''# Запись на онлайн-практикум

Учебный продукт и вымышленные данные. Подписчики школы записываются на один бесплатный
практикум после покупки подписки. Мы думаем, что основная операционная проблема состоит
в пропусках уже забронированных практикумов. Руководитель решает, продолжать ли ограниченную
проверку внутреннего напоминания со ссылкой на перенос или менять направление.

В pilot.csv приведена итоговая сводка четырёх учебных недель. Единица назначения и анализа —
один подписчик, одна бронь. `reserved` — распределённые брони, `completed` — дошли и завершили
мероприятие в течение 7 календарных дней после бронирования, `support_contacts` — подписчики,
которые хотя бы раз обратились в поддержку в том же 7-дневном окне. В сводке нет пропусков
исходов. Распределение в таблице описано как рандомизация. Проверь качество сведений до
вывода о масштабировании: экономика долгосрочного удержания не измерялась.

Полезный эффект для решения — +4 п.п. к доле завершивших против контроля. Вред — обращения
у более 5% подписчиков в тестовой ветке. 80 000 руб. — потолок всего бюджета на четыре недели,
текущая смета 62 000 руб. Смета не включает неизвестный долгосрочный доход. Рассматриваем
текущее обслуживание, внутреннее напоминание с переносом, более дорогой звонок куратора и
прекращение инициативы. Для звонков нет сопоставимого теста. Сигнал ведёт только в кабинет,
никому не раскрывает расписание подписчика и содержит отказ от уведомлений. Следующая
волна не может начаться без подтверждения технической возможности переноса, согласий и бюджета.
'''
CORRECTION = '''# Поправка владельца учебной выгрузки

После утверждения выяснилось, что join с таблицей командных аккаунтов включил завершения
несвоих заказов и пропустил часть обращений именно в ветке reminder. Владелец пересобрал
показатели на тех же 400 подписчиках на ветку и том же окне. Файл pilot-corrected.csv
заменяет pilot.csv для completed/support_contacts во всех выводах. Смета, контроль,
пороги и ограничения согласия остаются прежними. Данные вымышленные. Приостановите
следующую волну до решения о пересмотре. Сохраните историю утверждённой версии.
'''
CSV = 'arm,reserved,completed,support_contacts\ncontrol,400,264,12\nreminder,400,300,16\n'
CSV_CORRECTED = 'arm,reserved,completed,support_contacts\ncontrol,400,264,12\nreminder,400,276,24\n'
MESSAGES = [
    'Помоги разобраться, что делать с пропусками уже забронированных онлайн-практикумов. '
    'Вход в lab-input. Это учебный кейс «Запись на онлайн-практикум». Сначала сформулируй '
    'цель, решение и ограничения. Остановись на согласовании брифа (C1). '
    'Используй текущую модель и локальный терминал, без сети, MCP, API, дополнительных моделей или субагентов. '
    'В конце этапов сохраняй результаты на диск, новый чат должен их найти. Работаешь только в текущей папке. '
    'Перед изменением файлов проекта прими, что сообщения C1/C2/C3 дальше подаёт сценарный руководитель.',
    'Подтверждаю бриф C1. Сопоставь варианты, посчитай доли по исходным данным. '
    'Выведи результат и остановись на выборе варианта C2. Сохрани основания в файлах.',
    'На C2 выбираю ограниченную проверку внутреннего напоминания со ссылкой на перенос. '
    'Подготовь продуктовую и коммуникационную стратегии и план проверки, сохрани на диск. '
    'Предъяви их на C3. Запуск и расширение ещё не утверждаю.',
    'Утверждаю пакет C3',
    'Продолжи кейс «Запись на онлайн-практикум» после паузы. В lab-input поздняя поправка '
    'и новая выгрузка. Восстанови утверждённое решение из файлов, покажи что меняется, '
    'что сделать сейчас и что требует нового решения. Сохрани результат. Работай только в текущей папке, '
    'используй текущую модель и локальный терминал без сети, MCP, API, других моделей или субагентов.',
]


def digest(data):
    return hashlib.sha256(data.encode()).hexdigest()


def prepare(root, repo, python):
    for mode in ('plain', 'strategist'):
        workspace = root / mode
        workspace.mkdir()
        if mode == 'strategist':
            with subprocess.Popen(['git', '-C', str(repo), 'archive', 'HEAD'], stdout=subprocess.PIPE) as proc:
                subprocess.run(['tar', '-xf', '-', '-C', str(workspace)], stdin=proc.stdout, check=True)
                if proc.wait():
                    raise RuntimeError('git archive failed')
            for path in ('tools/power.py', 'skills/decision-support/SKILL.md'):
                (workspace / path).write_bytes((repo / path).read_bytes())
            (workspace / '.venv').symlink_to(python.parent.parent, target_is_directory=True)
        inputs = workspace / 'lab-input'
        inputs.mkdir()
        (inputs / 'brief.md').write_text(BRIEF)
        (inputs / 'pilot.csv').write_text(CSV)
    inputs = {'brief.md': BRIEF, 'pilot.csv': CSV, 'correction.md': CORRECTION, 'pilot-corrected.csv': CSV_CORRECTED}
    protocol = Path(__file__).with_name('pair_protocol.md')
    freeze = {
        'created_local': time.strftime('%Y-%m-%dT%H:%M:%S%z'),
        'protocol_sha256': hashlib.sha256(protocol.read_bytes()).hexdigest(),
        'runner_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'inputs_sha256': {name: digest(value) for name, value in inputs.items()},
        'messages': MESSAGES, 'model': MODEL, 'effort': EFFORT,
        'candidate_git_head': subprocess.check_output(['git', '-C', str(repo), 'rev-parse', 'HEAD'], text=True).strip(),
        'candidate_overlay_sha256': {p: hashlib.sha256((repo / p).read_bytes()).hexdigest()
                                    for p in ('tools/power.py', 'skills/decision-support/SKILL.md')},
    }
    (root / 'frozen.json').write_text(json.dumps(freeze, ensure_ascii=False, indent=2))


def run(root, mode, continue_after_failed_launch=False):
    workspace = root / mode
    receipts = root / f'{mode}-events'
    previous = None
    metrics = []
    if continue_after_failed_launch:
        metrics = json.loads((receipts / 'metrics.json').read_text())
        if [item['exit'] for item in metrics] != [0, 2]:
            raise ValueError(f'{mode}: expected success on turn 1 and rejected launch on turn 2')
        if (receipts / '2.jsonl').stat().st_size or 'unexpected argument' not in (receipts / '2.stderr.txt').read_text():
            raise ValueError(f'{mode}: turn 2 actually ran')
        archived = receipts / 'failed-launch'
        archived.mkdir()
        for name in ('metrics.json', '2.jsonl', '2.stderr.txt'):
            (receipts / name).rename(archived / name)
        metrics = metrics[:1]
        previous = metrics[0]['session']
    else:
        receipts.mkdir()
    env = os.environ.copy()
    for key in ('OPENAI_API_KEY', 'AZURE_OPENAI_API_KEY', 'OPENROUTER_API_KEY', 'JEV_API_KEY', 'ANTHROPIC_API_KEY'):
        env.pop(key, None)
    env['PYTHONDONTWRITEBYTECODE'] = '1'
    for turn, message in enumerate(MESSAGES[len(metrics):], len(metrics) + 1):
        if turn == 5:
            (workspace / 'lab-input/correction.md').write_text(CORRECTION)
            (workspace / 'lab-input/pilot-corrected.csv').write_text(CSV_CORRECTED)
        resume = 1 < turn < 5
        cmd = ['codex', 'exec'] + (['resume'] if resume else [])
        cmd += ['--ignore-user-config', '--disable', 'hooks', '--skip-git-repo-check', '--json',
                '-c', 'approval_policy="never"', '-c', 'sandbox_mode="workspace-write"',
                '-c', 'sandbox_workspace_write.network_access=false', '-c', f'model_reasoning_effort="{EFFORT}"',
                '-m', MODEL, '-o', str(receipts / f'{turn}-answer.md')]
        if resume:
            cmd.append(previous)
        cmd.append('-')
        started = time.time()
        with (receipts / f'{turn}.jsonl').open('w') as stdout, (receipts / f'{turn}.stderr.txt').open('w') as stderr:
            proc = subprocess.run(cmd, cwd=workspace, env=env, input=message, stdout=stdout, stderr=stderr,
                                  text=True, timeout=420)
        ended = time.time()
        events = [json.loads(line) for line in (receipts / f'{turn}.jsonl').read_text().splitlines() if line.startswith('{')]
        session = next((entry.get('thread_id') for entry in events if entry.get('type') == 'thread.started'), None)
        usage = [entry['usage'] for entry in events if entry.get('type') == 'turn.completed' and 'usage' in entry]
        errors = sum(1 for entry in events if entry.get('type') in ('error', 'turn.failed') or
                     (entry.get('type') == 'item.completed' and entry.get('item', {}).get('type') == 'command_execution'
                      and entry['item'].get('exit_code', 0) != 0))
        metrics.append({'turn': turn, 'started_epoch': started, 'ended_epoch': ended, 'seconds': round(ended-started, 3),
                        'exit': proc.returncode, 'session': session, 'usage': usage, 'tool_or_turn_errors': errors})
        (receipts / 'metrics.json').write_text(json.dumps(metrics, ensure_ascii=False, indent=2))
        print(f'{mode} turn {turn}: {proc.returncode}, {ended-started:.1f} s, errors {errors}', flush=True)
        if proc.returncode or not (receipts / f'{turn}-answer.md').exists() or not session:
            return 1
        if resume and previous != session or turn == 5 and previous == session:
            raise RuntimeError(f'incorrect session continuity: {mode}, {turn}')
        previous = session
    return 0


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('root', type=Path)
    parser.add_argument('repo', type=Path)
    parser.add_argument('python', type=Path)
    parser.add_argument('--prepare-only', action='store_true')
    parser.add_argument('--run-prepared', action='store_true')
    parser.add_argument('--continue-after-failed-launch', action='store_true')
    args = parser.parse_args()
    root = args.root.resolve()
    if not args.run_prepared and not args.continue_after_failed_launch:
        prepare(root, args.repo.resolve(), args.python.absolute())
    if args.prepare_only:
        return 0
    freeze = json.loads((root / 'frozen.json').read_text())
    runner_hash = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    if args.continue_after_failed_launch:
        if freeze['messages'] != MESSAGES or freeze['model'] != MODEL or freeze['effort'] != EFFORT:
            raise ValueError('experimental conditions changed')
        (root / 'runner-repair.json').write_text(json.dumps({
            'original_sha256': freeze['runner_sha256'], 'repaired_sha256': runner_hash,
            'change': 'Remove unsupported color flag from resume. Keep successful first turns and archive rejected launches.',
        }, indent=2))
    elif runner_hash != freeze['runner_sha256']:
        raise ValueError('runner changed after freeze')
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        failures = list(executor.map(lambda mode: run(root, mode, args.continue_after_failed_launch), ('plain', 'strategist')))
    return int(any(failures))


if __name__ == '__main__':
    raise SystemExit(main())

import argparse
from decimal import Decimal, ROUND_HALF_UP
import json
from pathlib import Path
import re
import shutil


def measure(turns):
    latest_by_session = {}
    for turn in turns:
        if turn['exit'] or len(turn['usage']) != 1:
            raise ValueError(f'No comparable token measurement for turn {turn["turn"]}')
        usage = turn['usage'][0]
        session = turn['session']
        previous = latest_by_session.get(session, {})
        if any(usage[key] < previous.get(key, 0) for key in usage):
            raise ValueError(f'Token counter decreased in session {session}')
        latest_by_session[session] = usage
    inputs = sum(usage['input_tokens'] for usage in latest_by_session.values())
    cached = sum(usage['cached_input_tokens'] for usage in latest_by_session.values())
    outputs = sum(usage['output_tokens'] for usage in latest_by_session.values())
    usd = (Decimal(inputs - cached) * 2 + Decimal(cached) / 10 + Decimal(outputs) * 10) / 1_000_000
    return {'seconds': round(sum(turn['seconds'] for turn in turns), 3), 'uncached_input': inputs-cached,
            'cached_input': cached, 'output': outputs, 'api_equivalent_usd': str(usd.quantize(Decimal('.0001'), ROUND_HALF_UP)),
            'tool_or_turn_errors': sum(turn['tool_or_turn_errors'] for turn in turns)}


def redact_local_paths(text, root):
    prefix = str(root).replace('/private/tmp/', '/tmp/')
    text = text.replace('/private' + prefix, '').replace(prefix, '')
    return re.sub(r'\[([^\]]+)\]\((/(?:strategist|plain)/[^)]+)\)', lambda m: m[1] + ' (`' + m[2].lstrip('/') + '`)', text)


def main():
    cli = argparse.ArgumentParser()
    cli.add_argument('trial', type=Path)
    cli.add_argument('output', type=Path)
    args = cli.parse_args()
    root = args.trial.resolve()
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=True)
    freeze = json.loads((root/'frozen.json').read_text())
    shutil.copy2(root/'frozen.json', out/'frozen.json')
    if (root/'runner-repair.json').is_file():
        shutil.copy2(root/'runner-repair.json', out/'runner-repair.json')
    lines = ['# Фактические ответы двух режимов', '',
             'Относительные пути заменяют пути отдельных рабочих папок. Текст ответов сохранён.', '']
    metrics = {'model': freeze['model'], 'effort': freeze['effort'], 'human_active_seconds': None,
               'usage_basis': 'Codex CLI 0.171.0: final cumulative turn.completed.usage by session', 'modes': {}}
    for label, name in [('plain', 'Обычный чат с терминалом'), ('strategist', 'Стратег')]:
        receipts = root/f'{label}-events'
        turns = json.loads((receipts/'metrics.json').read_text())
        assert [turn['turn'] for turn in turns] == list(range(1, 6))
        assert len({turn['session'] for turn in turns[:4]}) == 1 and turns[-1]['session'] != turns[0]['session']
        for turn in turns:
            turn['session'] = 'initial' if turn['turn'] < 5 else 'fresh'
        metrics['modes'][label] = {'initial': measure(turns[:4]), 'fresh_chat': measure(turns[4:]),
                                   'total': measure(turns), 'fresh_session_distinct': True,
                                   'turns': turns}
        lines.extend([f'## {name}', ''])
        for turn, message in enumerate(freeze['messages'], 1):
            answer = (receipts/f'{turn}-answer.md').read_text().strip()
            if not answer:
                raise ValueError(f'Empty response: {label} turn {turn}')
            lines.extend([f'### Реплика {turn}', '', '> '+message, '', redact_local_paths(answer, root), ''])
    (out/'TRANSCRIPTS.md').write_text('\n'.join(lines))
    (out/'metrics.json').write_text(json.dumps(metrics, ensure_ascii=False, indent=2))
    for name in ('brief.md', 'pilot.csv', 'correction.md', 'pilot-corrected.csv'):
        src = root/'plain/lab-input'/name
        assert src.read_bytes() == (root/'strategist/lab-input'/name).read_bytes()
        target = out/'inputs'/name
        target.parent.mkdir(exist_ok=True)
        shutil.copy2(src, target)


if __name__ == '__main__':
    main()

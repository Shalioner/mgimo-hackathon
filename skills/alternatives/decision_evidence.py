#!/usr/bin/env python3
"""Local evidence admissibility, not a demand/economics predictor. No network writes."""
import argparse
from datetime import date
import hashlib
import json
from pathlib import Path
import sys


def assess(case, omitted=()):
    conditions = {c['id']: c for c in case['conditions']}
    evidence = [e for e in case['observations'] if e['id'] not in omitted]
    states = {}
    for cid, condition in conditions.items():
        refs = [e for e in evidence if e.get('condition') == cid]
        values = {e['value'] for e in refs}
        states[cid] = {'value': next(iter(values)) if len(values) == 1 else None,
                       'conflict': len(values) > 1, 'refs': [e['id'] for e in refs]}
    options = []
    for option in case['options']:
        failed, unknown = [], []
        for cid, expected in option['requires'].items():
            value = states[cid]['value']
            if value is None:
                unknown.append(cid)
            elif value != expected:
                failed.append(cid)
        options.append({**option, 'status': 'excluded' if failed else 'conditional' if unknown else 'eligible',
                        'failed': failed, 'unknown': unknown})
    eligible = [o for o in options if o['status'] == 'eligible']
    best = min((o['priority'] for o in eligible), default=None)
    leaders = [o['id'] for o in eligible if o['priority'] == best]
    return {'recommended': leaders, 'options': options, 'conditions': states}


def validate(case, root):
    if not isinstance(case, dict):
        raise ValueError('Корень JSON должен быть объектом')
    for field in ('conditions', 'options', 'observations'):
        if not isinstance(case.get(field), list) or not all(isinstance(item, dict) for item in case[field]):
            raise ValueError(f'{field}: нужен список объектов')
    for key in ('question', 'as_of', 'priority_basis', 'scope', 'conditions', 'options'):

        if not case.get(key):
            raise ValueError(f'Заполните {key}')
    today = date.fromisoformat(case['as_of'])
    conditions = {c['id']: c for c in case['conditions']}
    if len(conditions) != len(case['conditions']):
        raise ValueError('Повтор condition ID')
    ids = [e['id'] for e in case['observations']] + [o['id'] for o in case['options']]
    if len(ids) != len(set(ids)):
        raise ValueError('Повтор ID')
    kinds = {o['kind'] for o in case['options']}
    if len(case['options']) < 3 or not {'status_quo', 'kill'} <= kinds:
        raise ValueError('Нужны >=3 варианта, status_quo и kill')
    for c in conditions.values():
        if not c.get('question') or not c.get('scope') or c['type'] not in ('documented_property', 'own_outcome'):
            raise ValueError('Условию нужны вопрос, scope и тип')
    for o in case['options']:
        if not isinstance(o.get('requires'), dict):
            raise ValueError('requires должен быть объектом')
        if (not o.get('action') or not o.get('objection') or (not o['requires'] and o['kind'] not in ('status_quo', 'kill'))
                or type(o['priority']) is not int or o['priority'] < 0):
            raise ValueError('Варианту нужны действие, возражение, необходимые условия и приоритет')
        for cid, value in o['requires'].items():
            if cid not in conditions or type(value) is not bool:
                raise ValueError('Неверное необходимое условие')
    used = {cid for option in case['options'] for cid in option['requires']}
    if set(conditions) - used:
        raise ValueError(f'Условия без связи с действиями: {sorted(set(conditions) - used)}')
    for e in case['observations']:
        if (e.get('type') != 'fact' or e.get('access') != 'open' or e.get('primary') is not True
                or not isinstance(e.get('url'), str) or not e['url'].startswith('https://') or not e.get('locator') or not e.get('limitations')):
            raise ValueError(f"{e['id']}: нужен первичный открытый факт с URL/локатором/ограничением")
        if not (date.fromisoformat(e['observed_at']) <= today <= date.fromisoformat(e['review_by'])):
            raise ValueError(f"{e['id']}: требуется перепроверка даты")
        path = (root / e['snapshot']).resolve()
        if not path.is_relative_to(root.resolve()):
            raise ValueError('Снимок должен быть внутри папки входа')
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != e['sha256'] or not e.get('quote') or e['quote'] not in raw.decode('utf-8'):
            raise ValueError(f"{e['id']}: хэш или цитата не совпадают со снимком")
        cid = e.get('condition')
        if cid is not None:
            if (cid not in conditions or conditions[cid]['type'] != 'documented_property'
                    or e['scope'] != conditions[cid]['scope'] or type(e.get('value')) is not bool
                    or not e.get('mapping_reason')):
                raise ValueError(f"{e['id']}: факт не применим к условию/области; исход клиента не выводится из документации")


def run(case, root):
    validate(case, root)
    result = assess(case)
    # What changes without each observation? Do not label every source decisive.
    result['evidence_effects'] = []
    for e in case['observations']:
        other = assess(case, (e['id'],))
        changes = [o['id'] for o, p in zip(result['options'], other['options']) if o['status'] != p['status']]
        result['evidence_effects'].append({'id': e['id'], 'changes_admissibility': changes,
                                          'changes_recommendation': other['recommended'] != result['recommended']})
    result['next_questions'] = []
    for cid, state in result['conditions'].items():
        if state['value'] is not None:
            continue
        branches = {}
        for value in (False, True):
            # A counterfactual answer, never stored as an observed fact.
            hypothetical = {**case, 'observations': case['observations'] + [
                {'id': 'counterfactual', 'condition': cid, 'value': value}]}
            # Conflicts require resolving the original observations first.
            if state['conflict']:
                hypothetical['observations'] = [e for e in hypothetical['observations'] if e.get('condition') != cid or e['id'] == 'counterfactual']
            branches[str(value).lower()] = assess(hypothetical)['recommended']
        relevant = [o['id'] for o in result['options'] if cid in o['unknown'] and not o['failed']]
        if relevant:
            result['next_questions'].append({'condition': cid, 'question': next(c['question'] for c in case['conditions'] if c['id'] == cid),
                'unblocks': relevant, 'if_answer': branches, 'changes_today': branches['false'] != branches['true'],
                'conflict': state['conflict']})
    result['next_questions'].sort(key=lambda q: not q['changes_today'])
    return result


def render(case, result):
    actions = {option['id']: option['action'] for option in result['options']}
    def action_names(ids):
        return ' / '.join(actions[key] for key in ids) or 'рекомендации пока нет'

    lines = ['# Решение сегодня', '', case['question'], 'Область: ' + case['scope'], '',
             'Что предлагаем руководителю: ' + (' / '.join(actions[key] for key in result['recommended']) if result['recommended'] else 'ОТКАЗ: недостаточно данных для рекомендации.'),
             'Основание приоритета (допущение): ' + case['priority_basis'],
             'Это допуск к указанному действию, не доказательство спроса, эффекта или окупаемости. Выбирает человек.', '', '## Действия']
    conflicts = [cid for cid, state in result['conditions'].items() if state['conflict']]
    if conflicts:
        lines += ['КОНФЛИКТ источников: ' + ', '.join(conflicts) + '. Разберите область и дату до выбора зависимого действия.']
    for o in result['options']:
        lines += [f"- {o['id']} [{o['status']}]: {o['action']}",
                  f"  Возражение: {o['objection']}. Условия против: {o['failed']}; неизвестно: {o['unknown']}."]
    lines += ['', '## Какие открытые данные меняют выбор']
    for e, effect in zip(case['observations'], result['evidence_effects']):
        binding = f" [{e['condition']}={e['value']}]" if e.get('condition') else ''
        lines += [f"- {e['id']}{binding}: {e['mapping_reason'] if e.get('condition') else 'Фоновое сведение; не используется в выборе.'}",
                  ("  Без этого сведения изменится допуск к действию: " + action_names(effect['changes_admissibility']) + "." if effect['changes_admissibility'] else "  Допустимые действия сохраняются."),
                  ("  Рекомендация изменится." if effect['changes_recommendation'] else "  Рекомендация сохранится."),
                  f"  Источник: {e['url']} ({e['locator']}); снимок {e['snapshot']}. Ограничение: {e['limitations']}"]
    lines += ['', '## Что узнать дальше']
    if not result['next_questions']:
        lines += ['Нет неизвестных условий, меняющих допуск текущих действий. Это не доказательство готовности коммерческого запуска.']
    for q in result['next_questions']:
        marker = '[КОНФЛИКТ] ' if q['conflict'] else ''
        lines.append(f"- {marker}{q['question']}")
        if q['changes_today']:
            lines.extend(["  Если нет — " + action_names(q['if_answer']['false']) + ".",
                          "  Если да — " + action_names(q['if_answer']['true']) + "."])
        else:
            lines.append("  Ответ не меняет сегодняшний выбор: " + action_names(q['if_answer']['true']) + ".")
    lines += ['', 'Полный журнал условий, ID и контрфактические ветви: запуск с --json.']
    return '\n'.join(lines) + '\n'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('case', type=Path)
    parser.add_argument('--json', action='store_true')
    args = parser.parse_args()
    try:
        case = json.loads(args.case.read_text(encoding='utf-8'))
        result = run(case, args.case.parent)
    except (ValueError, KeyError, TypeError, OSError) as exc:
        print(f'ОТКАЗ: некорректные/неполные основания: {exc}', file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2) if args.json else render(case, result), end='\n' if args.json else '')
    return 0 if result['recommended'] else 2


if __name__ == '__main__':
    sys.exit(main())

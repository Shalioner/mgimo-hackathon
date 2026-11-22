# -*- coding: utf-8 -*-
from pathlib import Path
import subprocess
import sys
import yaml

root = Path('projects/stezhok')
ledger = root / 'state/ledger'

def save(name, rows):
    (ledger / name).write_text(yaml.safe_dump(rows, allow_unicode=True, sort_keys=False))
    subprocess.run(['.venv/bin/python', 'tools/ledger.py', 'validate', str(root)], check=True)

def read(name):
    return yaml.safe_load((ledger / name).read_text())

if sys.argv[1] == 'intake':
    claims = read('claims.yaml')
    claims.extend([
        dict(id='CLM-008', type='fact', statement='В локальном повторе CLI на заполненном примере записал одну карточку Markdown (exit 0); неполный пример отклонил (exit 2) без файла результата. Выход заполненного примера побайтно совпал с предоставленным example-result.md.', evidence=[dict(source='SRC-006', quote='Заполненный пример: exit 0, записана одна карточка Markdown; выход совпадает побайтно с example-result.md.', locator='Проверенный результат, первое предложение'), dict(source='SRC-006', quote='Неполный пример: exit 2, файл результата отсутствует.', locator='Проверенный результат, второе предложение')], confidence=dict(level='high', rationale='Независимый локальный запуск текущим агентом; коды и байты проверены кодом.'), limitations='Только локальная CLI-поверхность и заданные входы; не полнота аудита, спрос, мастерская или проверка истинности.'),
        dict(id='CLM-009', type='fact', statement='В локальном отрицательном контроле CLI принял заведомо недостоверную, но заполненную карточку (exit 0). Реализация проверяет четыре непустых текстовых поля; истинность, полнота аудита и пригодность обещания не проверяются.', evidence=[dict(source='SRC-006', quote='Заведомо недостоверная, но заполненная карточка: exit 0, записана без проверки истины.', locator='Проверенный результат, третье предложение'), dict(source='SRC-004', quote='Проверяет лишь наличие четырёх непустых полей. Не проверяет истинность, полноту аудита или пригодность обещания.', locator='Второй абзац README'), dict(source='SRC-005', locator='Цикл for row in rows: условие set(row) и непустых строк; затем формирование Markdown')], confidence=dict(level='high', rationale='Отрицательный контроль совпал с прочитанной реализацией.'), limitations='Проверены конкретная версия и вход; надёжность на всём домене не установлена.'),
    ])
    save('claims.yaml', claims)
    hyps = read('hypotheses.yaml')
    hyps.append(dict(id='HYP-004', kind='assumption', statement='Только сценарные вводные лаборатории: один синтетический аналитик; лимит 2 часа труда на внутренний разбор; условная оценка 1000 рублей/час; прямой денежный доход 0 рублей/разбор. Это не рыночная цена, не реальный бюджет, не измеренный труд и не прогноз спроса.', based_on=['SRC-004'], confidence=dict(level='low', rationale='Явно условные вводные README; принятие их для следующего разбора ожидает повторного C2/C3.'), verdict='open', would_change_decision=False))
    save('hypotheses.yaml', hyps)
    claims = read('claims.yaml')
    claims.append(dict(id='CLM-010', type='interpretation', statement='При HYP-004 потолок оценённых затрат труда: 2 часа × 1000 рублей/час = 2000 рублей/разбор; при прямом доходе 0 рублей денежный вклад при полном лимите равен −2000 рублей/разбор. Это условный бюджет внутреннего исследования; денежная безубыточность при положительном труде из данных не следует.', derived_from=['HYP-004'], confidence=dict(level='low', rationale='Арифметика проверена Python: 2*1000=2000; 0-2000=-2000. Драйверы остаются сценарными допущениями.'), limitations='Не оценка фактической стоимости или полезности; стоимость текущего native-сеанса сюда не включена.'))
    save('claims.yaml', claims)
    save('changes.yaml', [dict(id='CHG-001', date='2026-11-19', source='SRC-006', summary='Новое собственное локальное свидетельство форматирования карточек; условные бюджетные вводные SRC-004. Срабатывает review_trigger New own function or access rules; функция не доказывает аудит по существу и не меняет мастерскую.', effects=[dict(target='ALT-001', effect='strengthens', note='Появился готовый способ оформить вручную проверенные карточки, не доказательство их полезности. CLM-008/009 ограничивают свойство; HYP-004/CLM-010 дают только условный лимит труда.')], processed=False)])
elif sys.argv[1] == 'revision':
    alts = read('alternatives.yaml')
    alts[0]['supported_by'] = list(alts[0]['supported_by']) + ['CLM-008', 'CLM-009', 'HYP-004', 'CLM-010']
    alts[0]['costs_revenue'] += '\nПересмотрено по CHG-001: прежняя неизвестность фактических затрат сохраняется; HYP-004/CLM-010 задают только условный лимит внутреннего разбора. CLI оформляет заполненное, но не заменяет ручной аудит (CLM-008/009). Баллы HYP-001 не пересчитаны вверх: измерения пользы и экономии нет.'
    alts[0]['status_note'] = 'CHG-001: готовый локальный CLI остаётся вспомогательной поверхностью ALT-001; разработка и интеграция не предлагаются. Рекомендация прежняя, повторный C2 открыт.'
    save('alternatives.yaml', alts)
    decs = read('decisions.yaml')
    decs[0]['state'] = 'needs_review'
    decs[0]['rationale'] += '\n\nCHG-001: сработал существующий review_trigger New own function or access rules. Исторический chosen ALT-001 сохранён; подтверждения пересмотренной версии нет. Рекомендация ALT-001 сохраняется условно: CLI доказывает только оформление карточки; бюджет HYP-004 условен. Требуется повторный C2 по workflows/update-decision.md, затем стратегии и C3. Ни нового ответа человека, ни решения агента за него не записано.'
    decs[0]['based_on'] += ['CLM-008', 'CLM-009', 'HYP-004', 'CLM-010']
    decs[0]['status_note'] = 'CHG-001: awaiting_human на повторном C2; старый синтетический выбор не является новым подтверждением.'
    save('decisions.yaml', decs)
    exps = read('experiments.yaml')
    for item in exps:
        item['status_note'] = item.get('status_note', '') + ' Проверено по CHG-001: локальное форматирование не измеряет полезность аудита EXP-001 и не проверяет потребительское обещание EXP-002. Пороги, содержание и результаты не изменены; повторный C2 открыт.'
    save('experiments.yaml', exps)

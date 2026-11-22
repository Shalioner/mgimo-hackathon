# -*- coding: utf-8 -*-
from pathlib import Path
import yaml
import subprocess
import sys

p = Path('projects/stezhok')
f = p / 'state/ledger/claims.yaml'
xs = yaml.safe_load(f.read_text())
items = [
    {
        'id': 'CLM-006', 'type': 'interpretation',
        'statement': 'Агент задал 3 класса примеров обещаний: заявка гарантирует машину; запись достаточна для допуска; правило снижает неявки. Это не реальные обещания владельца. Документарный потолок охвата ALT-001 — 3 из 3 классов; методика ALT-002 адресует 1 из 3. Расчёт: 2 класса границ доступа + 1 класс причинного обещания = 3. Это предел проверки формулировок в выбранном наборе, не прогноз ошибок или сдвига продуктовой метрики.',
        'derived_from': ['CLM-004', 'CLM-005'],
        'confidence': {'level': 'medium', 'rationale': 'Операционализация границ интерпретаций и подсчёт заданных примеров; полнота для будущего продукта неизвестна.'},
        'limitations': 'Набор предложен агентом. Публичные правила не доказывают наличие или ошибочность местных обещаний.'
    },
    {
        'id': 'CLM-007', 'type': 'interpretation',
        'statement': 'Условный T0-расчёт HYP-001: веса 0.50, 0.30, 0.20; суммы ALT-001 4.00, ALT-002 1.80, ALT-003 2.40, ALT-004 1.90. При весе экономности e и отношении прочих весов 5:3: ALT-001 = 4.25 - 1.25e; ALT-003 = 1.75 + 3.25e; ALT-002 = 2 - e; ALT-004 = 1.375 + 2.625e. Равенство лидеров при e = 5/9, приблизительно 0.5556. При равенстве сохраняется ALT-003. Это арифметика допущений, не измеренные эффекты.',
        'derived_from': ['HYP-001'],
        'confidence': {'level': 'low', 'rationale': 'Арифметика воспроизводима; содержательные веса и баллы остаются допущением.'},
        'limitations': 'Не денежная экономия, не измеренная стоимость, не эмпирический порог.'
    }
]
def validate():
    result = subprocess.run(['rtk', 'proxy', sys.executable, 'tools/ledger.py', 'validate', str(p)], capture_output=True, text=True)
    print(result.stdout.strip())
    if result.returncode:
        print(result.stderr)
        raise SystemExit(result.returncode)

for item in items:
    if any(x['id'] == item['id'] for x in xs):
        raise SystemExit('Existing ID: ' + item['id'])
    xs.append(item)
    f.write_text(yaml.safe_dump(xs, allow_unicode=True, sort_keys=False))
    validate()

f = p / 'state/ledger/decisions.yaml'
decisions = yaml.safe_load(f.read_text())
decisions[0]['based_on'] += ['CLM-006', 'CLM-007']
f.write_text(yaml.safe_dump(decisions, allow_unicode=True, sort_keys=False))
validate()

f = p / 'outputs/02_research_plan.md'
s = f.read_text().replace('репликой ../inputs/03-C1-confirm.txt', 'репликой подтверждения из входов учебного прогона')
f.write_text(s)
f = p / 'outputs/03_situation.md'
s = f.read_text().replace('3 факта / 2 интерпретации', '3 факта / 4 интерпретации')
s += '\n## Расчётные основания сравнения\nCLM-006 задаёт ограниченный набор классов обещаний и предел документарного охвата (interpretation, medium). CLM-007 воспроизводит арифметику HYP-001 (interpretation, low). Это не новые наблюдения о мастерской или жителях.\n'
f.write_text(s)
f = p / 'outputs/04_options.md'
s = f.read_text().replace('нет [HYP-001] |', 'нет [HYP-001; CLM-007] |')
s = s.replace('| **Взвешенная сумма** | **4.00** | **1.80** | **2.40** | **1.90** |', '| **Взвешенная сумма** [CLM-007] | **4.00** | **1.80** | **2.40** | **1.90** |')
s = s.replace('| **Потолок эффекта** | Требования к доказательству обещаний; численный сдвиг не оценивается | Операциональное определение события; численный сдвиг не оценивается | Сохранённая возможность возврата, не нулевой вклад | Снятие текущей нагрузки с ценой возможного возврата, не нулевой вклад |', '| **Потолок эффекта** [CLM-006] | Документарный охват: максимум 3 из 3 заданных классов обещаний, не продуктовый эффект | Документарный охват: максимум 1 из 3 классов, не продуктовый эффект | Новый охват отсутствует; возможность возврата сохранена | Новый охват отсутствует; снимается текущая нагрузка |')
s = s.replace('## Чувствительность и условия смены лидера', '## При каком весе меняется лидер')
s = s.replace('Порог равенства e = 5/9 [HYP-001]', 'Порог равенства e = 5/9 [HYP-001; CLM-007]')
for operator in ['меньше', 'равен', 'больше']:
    s = s.replace('| e ' + operator + ' 5/9 |', '| e ' + operator + ' 5/9 [CLM-007] |')
s = s.replace('Все баллы и суммы ниже — условная модель [HYP-001]', 'Все баллы и суммы ниже — условная модель [HYP-001; CLM-007]')
f.write_text(s)
print('Corrected ledger calculations and artifact references.')

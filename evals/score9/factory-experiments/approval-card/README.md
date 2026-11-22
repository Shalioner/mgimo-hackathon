# Волна 1: A/B UX-тест «компактная карточка C3 vs штатный отчёт»

Воспроизводимый экспериментальный пакет сравнения **одной компактной карточки C3** против **живого штатного отчёта (`report.html`)** на одинаковых фактах из двух состояний C3:
1. `family-connect` — утверждённый C3 (`status: current`, SHA-256 `b1473fbfb235891c6e552b00df8dd244fab8a06e2ccb1c32b72523f97e853483`).
2. `esim-travel` — повторно открытый C3 (`status: unrecorded` по `c3_approval.py`, историческое C3 сохранено и не переписано, `PRM-001..003` отозваны, `CHG-001` открыт).

## Источник фактов (read-only)
- Замороженный архив: `strategist-jury.tar.gz`, SHA-256 `45067b201632781870cc73f95e2810283b26102f1df3e0114ec80e88e7eac006`.
- Распакованный пакет: `/private/var/folders/ss/4g0fyyvx3hgczmghb26lyfhh0000gn/T/strategist-candidate-c3-43zkm4xk/jury-final/unpacked`.
- В baseline используются **живые** `outputs/report.html` из замороженного пакета без копирования и модификации.

## Состав папки
- `manifest.json` — привязка к замороженному пакету, SHA-256 архива, baseline-отчётов и C3-квитанции.
- `cards/family-connect-card.html` — локальная компактная карточка C3 для `family-connect`.
- `cards/esim-travel-card.html` — локальная компактная карточка C3 для `esim-travel`.
- `tasks.md` — 10 конкретных заданий для участника (`T01`–`T10`, без ответов).
- `answer_key.yaml` — закрытый ключ модератора с проверяемыми ссылками, флагами `scope_critical` и маркерами восстанавливаемости.
- `protocol.md` — протокол 2×2 кроссовера (`S1`–`S4`, ноль повторов ответов), методика тайминга и критерий `>= 8/10` + `0` scope errors.
- `run_session.py` — локальный CLI для плана распределения, таймированной сессии (`time.monotonic()`) и сводки результатов.
- `verify.py` — автоматическая техническая сверка (SHA архива, baseline и C3; отсутствие недоказанных ролей/дат; восстанавливаемость всех 10 заданий из обоих вариантов; баланс `S1`–`S4`).
- `verification_report.md` — зафиксированный протокол технической сверки.

## Команды

```bash
# 1. Техническая сверка (использует Python из .venv замороженного пакета или локального .venv с PyYAML)
/private/var/folders/ss/4g0fyyvx3hgczmghb26lyfhh0000gn/T/strategist-candidate-c3-43zkm4xk/jury-final/unpacked/.venv/bin/python \
  evals/score9/factory-experiments/approval-card/verify.py

# 2. Показать план распределения для 8 участников (2 полных блока S1-S4)
python3 evals/score9/factory-experiments/approval-card/run_session.py plan --participants 8

# 3. Провести таймированную сессию с участником P01
python3 evals/score9/factory-experiments/approval-card/run_session.py run \
  --participant-id P01 --index 1 --out /tmp/approval-card-sessions/P01.json

# 4. Посчитать сводку после оценки модератором по answer_key.yaml
python3 evals/score9/factory-experiments/approval-card/run_session.py summarize \
  --sessions-dir /tmp/approval-card-sessions
```

## Статус UX-времени
Без реальных участников UX-время и фактическая точность **не считаются подтверждёнными** (`BLOCKED / UNCONFIRMED: 0 participants`).

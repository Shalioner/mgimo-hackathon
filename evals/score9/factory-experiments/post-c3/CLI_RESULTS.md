# Результаты CLI-воспроизведения (`post-c3`)

Все проверки выполнены на **изолированных временных копиях** `projects/family-connect` из замороженного кандидата. До и после прогона совпали SHA-256 всех 33 файлов исходного кейса, включая квитанцию C3. [Скрипт](reproduce_cli.py) · [Результаты](cli_results.json).

## Команда воспроизведения

```bash
python3 evals/score9/factory-experiments/post-c3/reproduce_cli.py \
  --candidate /private/var/folders/ss/4g0fyyvx3hgczmghb26lyfhh0000gn/T/strategist-candidate-c3-43zkm4xk/jury-final/unpacked \
  --out evals/score9/factory-experiments/post-c3/cli_results.json
```

## Сводка результатов

| Контроль | Что изменено в изолированной копии | `ledger.py validate` | `c3_approval.py check` | `consistency.py` | `c3_approval.py prepare` | Вывод |
|---|---|---|---|---|---|---|
| **Baseline** | Без изменений | exit 0 (0 ошибок, 0 предупреждений, 55 объектов) | exit 0 (`current`, `changed: []`) | exit 0 (0 ошибок) | — (SHA `b1473fbf…853483`) | **PASS** |
| **A (этап 1, до сброса C2)** | `add-source` → `SRC-008` (`A_synthetic_billing_telemetry.md`), `CLM-033`, `CLM-034`, `CHG-001` (`processed: false`), `HYP-001`/`HYP-003` → `refuted` | exit 0 (0 ошибок, 1 предупреждение: `CHG-001` не обработан) | exit 1 (`stale`, `changed: ["HYP-001", "HYP-003"]`) | exit 1 (`consistency.c3_approval_stale`) | — | **PASS** (негативный контроль) |
| **A (этап 2, `DEC-001: needs_review`)** | Дополнительно `DEC-001.state: needs_review` (ожидание повторного C2) | exit 0 (0 ошибок, 2 предупреждения: `PRM-001` и `CHG-001`) | exit 2 (`invalid`: «для C3 требуется действующий выбор руководителя на C2») | exit 1 (`consistency.c3_approval_invalid`) | exit 2 («C3: для C3 требуется действующий выбор руководителя на C2») | **PASS** (C3 заблокирован до повторного C2) |
| **B (локальная правка в пакете C3)** | В `outputs/06_comms_strategy.md` фраза о старой ссылке заменена на «Тот же текст показывается при открытии старой ссылки». Выбор `ALT-003` сохранён | exit 0 (0 ошибок, 0 предупреждений) | exit 1 (`stale`, `changed: ["outputs/06_comms_strategy.md"]`) | exit 1 (только `consistency.c3_approval_stale`) | exit 0, новый SHA `edaeb9fc6d3e2121f471f8479062742675d485fd3690ba97996a1004e123ec6d` | **PASS** (повторное C3 без повторного C2) |
| **B-ext (нейтральный внешний вариант)** | Только редакционная правка заголовка в `outputs/10_market_comparison.md` (вне контура C3) | exit 0 (0 ошибок, 0 предупреждений) | exit 0 (`current`, `changed: []`) | exit 0 (0 ошибок) | exit 0, прежний SHA `b1473fbfb235891c6e552b00df8dd244fab8a06e2ccb1c32b72523f97e853483` | **PASS** (неизменный C3) |

## Дополнительные сигналы CLI по Сценарию A

- **`ledger.py impact <copy_A> CHG-001 --json`** (exit 0): детерминированно затронуто **12 объектов** по графу зависимостей:
  - глубина 0: `HYP-001`, `HYP-003`
  - глубина 1: `PAIN-001`, `ALT-001`, `ALT-003`, `ALT-004`, `ALT-005`, `DEC-001`, `EXP-001`, `EXP-002`, `EXP-003`
  - глубина 2: `PRM-001`
- **`verify.py <copy_A> --change CHG-001 --json`** (exit 0): проверено 170 строк (`ok: 162`, `check: 2`, `unsupported: 6`).

В A раннер моделирует смену состояния гипотез и решения. Контроль проверяет реакцию команд на эту смену. Содержание нового материала и обоснованность следующего управленческого шага проверяются по ответам модели.

## Замеченная особенность версий инструментов (ограничение)

В замороженном кандидате `tools/c3_approval.py` включает `outputs/00_brief_response.md` в снимок C3 (`BRIEF_RESPONSE`), тогда как в базовом commit worktree `e89f775` этого файла в `tools/c3_approval.py` нет. Поэтому `reproduce_cli.py` запускает именно CLI замороженного кандидата (`<candidate>/.venv/bin/python` + `<candidate>/tools/*.py`), что гарантирует точное совпадение с семейным C3 `b1473fbfb235891c6e552b00df8dd244fab8a06e2ccb1c32b72523f97e853483`.

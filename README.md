# Стратег Фабрики продуктов

Откройте папку в агентной среде и задайте вопрос о продукте. Стратег поможет выбрать, что исследовать, сравнить варианты и подготовить продуктовую и коммуникационную стратегии. Руководитель подтверждает бриф, выбирает вариант и утверждает пакет.

## Демонстрационный проект «Связь для близких»

Руководитель выбрал ограниченную диагностику на одном тарифе (ALT-003) и утвердил её план на C3. Диагностика проверит причины перебоев, права участников и доступность пакета. Первый шаг после согласования — сухая проверка макета дизайнером по EXP-001. [Основания решения](projects/family-connect/outputs/08_decision_log.md)

| Материал | Где открыть |
|---|---|
| Запрос и исходные материалы | [Постановка задачи](projects/family-connect/outputs/01_brief.md) · [Бриф МегаФона](projects/family-connect/sources/2994317a-e9da-4c24-bbe7-598abf45f6ee-dc138c98-7bfb-4523-a3dc-ee32f1e77a0b-pdf.pdf) · [Папка источников](projects/family-connect/sources/) |
| Варианты и стратегии | [Альтернативы](projects/family-connect/outputs/04_options.md) · [Продуктовая стратегия](projects/family-connect/outputs/05_product_strategy.md) · [Коммуникационная стратегия](projects/family-connect/outputs/06_comms_strategy.md) · [План проверки](projects/family-connect/outputs/07_validation_plan.md) |
| Решение и продолжение | [Пятичастный ответ](projects/family-connect/outputs/00_brief_response.md) · [HTML-отчёт](projects/family-connect/outputs/report.html) · [Журнал решений](projects/family-connect/outputs/08_decision_log.md) |
| Видео и описание работы | [Живая запись с D1 и таймером](evals/score9/demonstration/family-connect-live.mp4) · [Таймкоды](docs/JURY_EVIDENCE.md#таймкоды) · [PDF со скриншотами](output/pdf/strategist.pdf) |

## Быстрый старт

Нужны Python 3.9+ и доступ к модели в вашей агентной среде. Источники и готовые отчёты можно читать офлайн. Из корня репозитория на macOS или Linux выполните:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python tools/ledger.py status
```

В Windows используйте `py -m venv .venv` и `.venv\Scripts\python.exe` вместо `.venv/bin/python`. Учебные сквозные прогоны выполнены в Codex и ZCode. Для базового сценария достаточно модели среды; дополнительные интеграции и их доступы описаны в [руководстве](docs/ROUTING_ADVANCED.md).

Команда `status` читает локальные файлы. Чтобы получить содержательный ответ на свой вопрос, откройте папку в IDE с доступной вам моделью. Если он не загрузил правила проекта, попросите прочитать [AGENTS.md](AGENTS.md). Для новой задачи напишите:

> Клиенты бросают настройку уведомлений. У нас есть журналы шагов и обращения в поддержку. Какую проверку поручить первой?

Агент укажет папку нового кейса. Откройте `projects/<имя-кейса>/outputs/01_brief.md` и подтвердите рамку или уточните её в чате. Это согласование C1. Для продолжения откройте новый чат в той же папке и напишите «Продолжи кейс eSIM». Вы увидите сохранённый выбор и вопрос для повторного согласования. Основания и историю можно проверить в [журнале решений eSIM](projects/esim-travel/outputs/08_decision_log.md).

Посмотреть сохранённый выбор, следующее действие и открытое согласование без модели можно командой `.venv/bin/python tools/next.py projects/esim-travel --resume`.

## Выбор навыка и инструмента

`$strategist-route-skills` выбирает процедуру по короткой реплике и состоянию проекта. Например, «что выбрали?» ведёт к сохранённому решению, «вот новый отчёт» — к пересмотру. В среде без меню навыков откройте инструкцию напрямую. [Навык маршрутизации](skills/strategist-route-skills/SKILL.md)

В Codex `SessionStart` показывает сохранённое состояние. При настроенном D1 `UserPromptSubmit` добавляет рекомендацию по модели, а `PreToolUse` проверяет параметры отдельного субагента перед запуском. Эти два хука работают после штатного доверия в Codex и при наличии локальной настройки. Основная модель остаётся моделью чата. [Конфигурация хуков](.codex/hooks.json) · [Маршрут и ограничения](docs/NATIVE_HOST_ROUTING.md)

Проектный интерфейс `tools/route_tools.py` позволяет D1 выбрать действие для реестра. В записи он направляет один запрос на проверку ошибок, другой на подсчёт объектов. [Повтор команд](docs/TOOL_ROUTING.md#пример-из-видео)

В записи префикс `rtk proxy` принадлежит терминалу автора. Например, команда из записи работает как `.venv/bin/python tools/next.py projects/family-connect --resume`. `rg` ищет текст, `sed` и `cat` читают файлы, `shasum` сверяет отпечатки. Для основного сценария используйте Python-команды из быстрого старта.

## Материалы для жюри

- [Официальный кейс «Связь для близких»](projects/family-connect/outputs/00_brief_response.md) · [HTML-отчёт](projects/family-connect/outputs/report.html) · [Текущее согласование](projects/family-connect/state/progress.md).
- [Решение о допуске пилота](docs/FAMILY_PILOT_DECISION.md) · условия допуска, порог набора и границы сценарной экономики.
- [Запись официального кейса в Codex](evals/score9/demonstration/family-connect-live.mp4) · [Продолжение после утверждения](projects/family-connect/outputs/08_decision_log.md) · [Время и расходы](projects/family-connect/outputs/09_run_report.md).
- [Описание работы со скриншотами, PDF](output/pdf/strategist.pdf) · [Архитектура, инструменты и замеры](docs/DESCRIPTION.md) · [Карта доказательств и таймкоды](docs/JURY_EVIDENCE.md).
- Проверка комплекта офлайн — `.venv/bin/python evals/score9/jury/run.py`. Она проверяет официальный пакет, учебные сценарии и отрицательные контроли на временных копиях.

### Дополнительные доказательства

- [Отказ руководителя и продолжение в новом чате на примере «Стежка»](evals/score9/fresh-case/README.md).
- [Состояние eSIM после новых данных](projects/esim-travel/state/progress.md).
- [Ответ Стратега и обычного чата на одинаковых материалах](evals/score9/paired-handoff/README.md).
- [Живая редактура и продолжение в новой сессии](evals/score9/factory-experiments/post-c3/LIVE_REVISION.md) · сравнение фактических изменений и статуса C3 на двух копиях.
- [Сравнение на трёх запросах и обнаруженный пробел](evals/score9/paired-handoff/three-requests/README.md).

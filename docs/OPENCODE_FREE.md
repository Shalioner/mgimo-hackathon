---
vendor: OpenCode hosted routes
families: []
checked: 2026-11-15
refresh_after_days: 1
covers: ["opencode/big-pickle", "opencode/minimax-m3.1-flash-free"]
sources:
  - https://opencode.ai/docs/models/
  - https://models.opencode.ai/experimental/v2.0/api.json?type=chat,decision,image
delegate_hints: []
---
# OpenCode: только бесплатные маршруты

Состояние на 2026-11-15: реализация и offline-проверки готовы к независимому ревью.
Live smoke **не прошёл**: оба разрешённых синтетических запроса получили HTTP 429
`provider.free-limit`. Успешная генерация на двух разных моделях пока не доказана.
Нельзя называть интеграцию проверенной в рабочем режиме до снятия этого gate.

## Использование

Поддержан установленный `opencode v2.2.5`; другая версия требует проверки контракта.
Глобальные настройки, auth, Keychain и фоновый пользовательский сервис не меняются.
Установка зависимостей или OpenCode этим адаптером не выполняется.

Отдельное явное обновление открытого каталога (не inference):

```sh
.venv/bin/python tools/opencode_free.py refresh \
  --binary /Users/shalioner/.npm-global/bin/opencode \
  --output /absolute/existing/directory/opencode-free-catalog.json
```

Объедините с существующим `knowledge/routing.local.yaml`, сохранив другие настройки:

```yaml
execution_policy:
  opencode_free:
    enabled: true
    catalog: /absolute/existing/directory/opencode-free-catalog.json
```

При обновлении списка бесплатных моделей выполните metadata-only refresh с opt-in
синхронизацией локального списка (повторного подтверждения каждой модели не требуется):

```sh
.venv/bin/python tools/opencode_free.py refresh \
  --binary /Users/shalioner/.npm-global/bin/opencode \
  --output /absolute/existing/directory/opencode-free-catalog.json \
  --write-candidates knowledge/routing.local.yaml
```

В JSON-отчёте: `diff.added`, `removed`, `repriced`, `supported_exact_zero` и
`quality: not_evaluated`. Diff сравнивает новый каталог с предыдущим snapshot,
в том числе просроченным; это сравнение метаданных, а не результат inference.
Без `--write-candidates` команда печатает готовый `candidate_patch` и **не меняет
selection policy**. Сам refresh snapshot не означает автоматическую смену allowlist.
С флагом обновляются только catalog/candidates/provenance профиля OpenCode;
остальные значения YAML сохраняются (форматирование и комментарии YAML могут измениться).
Существующее `enabled` сохраняется; в новом профиле оно остаётся false — включение
из раздела выше делается один раз, не после каждого обновления списка.

Кандидаты пересобираются по проверенным text/tool capabilities, официальному
поддержанному chat protocol и всем нулевым тарифам. T2/T3 дополнительно требуют
объявленную поддержку reasoning; это **не свидетельство качества T3** и не заменяет
валидацию результата человеком. Исключаются известные специализированные имена/
семейства из проектного классификатора, явные domain/category/task ограничения,
image/non-chat и неизвестные protocols. Отсутствие доменной метки не доказывает
универсальность неизвестной модели; provenance всегда `not_evaluated`.
Ранее заданный порядок подходящих кандидатов сохраняется, новые ID добавляются
лексикографически; случайного выбора нет. Пустой уровень запрещает исполнение.
Если все прежние ID исчезли и остался один новый подходящий free chat ID,
эта команда делает его доступным для разрешённых по capabilities задач без
редактирования исходников. Затем запустите preflight из следующего блока.

```sh
.venv/bin/python tools/delegate.py --host opencode --project projects/<case> \
  --task classify --prompt-file /path/task.md --preflight
.venv/bin/python tools/delegate.py --host opencode --project projects/<case> \
  --task classify --prompt-file /path/task.md
.venv/bin/python tools/delegate.py --host opencode --project projects/<case> \
  --task problem-framing --prompt-file /path/next-task.md
```

Включение локального адаптера и передача реальных материалов остаются решением
оператора; в этой проверке отправлялся только синтетический текст.
Preflight возвращает JSON, а его exit 0 означает успешное построение плана;
исполняемость определяет поле `status`. Сетевых запросов, запуска CLI или записей
в preflight нет. Без свежего snapshot запуск запрещён, auto-refresh отсутствует.

Этот документ зарегистрирован также как transport guide для двух точных hosted ID:
он описывает проверенные параметры и ограничения OpenCode, а не внутренние инструкции
вендора или качество модели. Уровни effort не объявлены статически — их gate читает
из свежего каталога. Ежедневный сигнал обновления гайда не заменяет runtime freshness.

## Выбор модели и границы

Для каждой задачи заново проверяется её регистрация в routing, уровень и список
кандидатов. Начальные предпочтения: T1 — DeepSeek V4.1 Flash Free, T2 — Big Pickle,
T3 — MiniMax M3.1 Flash Free; локальная синхронизация выше заменяет этот список.
Вторые кандидаты заданы явно. Это начальная операционная политика, **не рейтинг
качества**: сравнительного eval ещё нет. Разные задачи одного уровня могут иметь
одну модель; случайного round-robin нет. T4 не делегируется.

Проверяется точный ID, а не суффикс `free`. Обязательны нулевые input/output цены,
все объявленные cache read/write и TTL, все условные/context overrides. Неизвестные
ценовые поля, modalities в стоимости, multipliers и experimental modes консервативно
отклоняются. Известный нулевой override разрешён. Отсутствующий обязательный тариф,
неизвестный формат, неверный digest, будущая дата или snapshot старше 24 часов — отказ.
Срок в 24 часа — операционная граница свежести, не обещание неизменности тарифа;
от аккаунтного списания дополнительно защищает подтверждённый signed-out режим.

Поддержан только chat-completions маршрут официального OpenCode с проверенным
пакетом openai-compatible; неподдержанный протокол исключается. Это намеренно
консервативное подмножество, а не весь бесплатный каталог. Проверяются text/tool
capabilities и context/input/output limits, с резервом для системного контекста.
Качество рассуждений из наличия capabilities не выводится.

Явный `--model opencode/<id>` проходит тот же gate и allowlist уровня.
`#variant` и `--effort` должны совпадать и присутствовать в reasoning.effort
метаданных модели для проверенного протокола. По умолчанию effort не передаётся;
`high` не угадывается. Существующие max approval и ultra/T4 запреты сохранены.

## Изоляция и исполнение

Каждая попытка получает новый временный HOME/XDG/TMP и рабочую папку вне проекта.
Окружение строится с нуля: фиксированный PATH, без proxy, API/auth/cloud/Orca,
Node/Bun overrides и пользовательских config/env. Проверяется отсутствие system
config и macOS MDM config. Проектные настройки и `.claude` отключены; глобальные
папки пустые. Permissions запрещают все действия инструментов, autoupdate выключен.

CLI получает локальную копию каталога **с одной прошедшей gate моделью** через
`OPENCODE_MODELS_PATH`; загрузка другого каталога отключена. Поэтому default/newest
и вспомогательные вызовы не могут выбрать платную модель. Перед генерацией проверяются
hash/version бинарника, `--standalone auth list --format json == []` и точный
одноэлементный вывод `--standalone models`. Credentials не экспортируются/читаются.
Отдельный `--standalone run --model … --format json --title …` использует private
server с временными данными; команда service не вызывается.

При квоте, ошибке, timeout, частичном ответе — остановка. Автоповторов и перехода
на другой аккаунт/API/модель нет. Выбор другого заранее проверенного кандидата
возможен только при новом планировании до генерации. Wrapper запускается один раз;
число внутренних transport retries/вспомогательных inference CLI не измерено.
Это не OS sandbox против вредоносного бинарника: доверяется проверенная установленная
версия с зафиксированным SHA256, как и в других adapters.

Receipt сохраняет source/time/digest каталога, fingerprint CLI, requested/transmitted/
observed, started, process_exit_code и result. `observed` не заполняется запросом:
NDJSON v2.2.5 не сообщает достоверный model ID. Ошибки и незавершённые результаты не
публикуются как ответы. Даже валидный ответ без ID остаётся `integrity: unconfirmed`.

`delegate` и preflight эмуляции `decide` используют один offline selector:
после смены цены/удаления первого кандидата оба выбирают одинаковый разрешённый
ID, а отсутствие variants оставляет effort=null. Один строгий list[str] gate
применяется в selection, authorize и прямом transport call; scalar/dict/bool/
пустой список и подстроки не считаются allowlist.
Для успешного NDJSON у каждого увиденного assistant messageID должен быть
`step_finish`; незавершённый следующий шаг отвергается. Отдельное событие конца
потока не выдумывается; reconcile-текст после finish того же messageID разрешён,
как в исходниках v2.2.5.

## Проверка 2026-11-15

Среда: codex (Orca worker); `ledger.py env/status` выполнены без изменения кейсов.

Baseline: `d9efed8be05a68a0dbcefe907b5fa8d2f03ee853`, отдельная worker-ветка
`Shalioner/opencode-free-routing` (не исходное имя `research/mgimo-video-2026-11-15`).
CLI: `opencode v2.2.5`; SHA256 executable:
`abe4d88594bffb12b364375b8a6732d2d1f7a977195ed1a35c5c76f8e89c9e0e`.
Каталог получен `2026-11-15T12:45:42.867486+00:00`; SHA256 нормализованного provider:
`3a345b690d20b0c81f8cd2418aebc91e8514c19be7c80eef376e4bce690c32d0`.

Read-only probe: `debug paths` подтвердил временные HOME, data, cache, config, state,
tmp и DB; auth вернул `[]`; models вернул только выбранный ID. Проверка системных
config/MDM путей прошла. Внешние auth/token/config-файлы не открывались.

Два запроса `Reply with exactly OK. Do not use tools.`:

| Task / tier | Transmitted | Process exit | Результат |
|---|---|---|---|
| classify / T1 | opencode/deepseek-v4.1-flash-free | 1 | provider.free-limit, HTTP 429 |
| problem-framing / T2 | opencode/big-pickle | 1 | provider.free-limit, HTTP 429 |

В обоих случаях started=true, auth_check=signed_out_empty_integrations,
wrapper_invocations=1, observed provider/model/effort=null. Подтверждены разные
переданные ID и корректная остановка. Успешный ответ и обслужившая его модель
не подтверждены. Больше live-запросов не делалось; topup/login не выполнялись.
Следующий gate: после обновления квоты отдельно разрешённая повторная проверка
двух маршрутов. Текущий разрешённый лимит двух smoke prompts исчерпан.

Offline fake tests проверяют тарифные изменения, stale/malformed snapshot,
платные/неизвестные explicit ID, variants, T4/max, утечку auth/env, запрет fallback,
quota/timeout/partial и NDJSON формы. Они не доказывают доступность провайдера.

```sh
.venv/bin/pytest -q tests/test_opencode_free.py tests/test_delegate.py tests/test_routing_policy.py tests/test_routing.py
```

Итог первичной проверки: полный `pytest -q` — **379 passed, 1 skipped** (26.73 с).
Целевой набор выше вместе с `tests/test_models.py` — **119 passed**.
`git diff --check` прошёл. Первоначальный полный прогон обнаружил отсутствие
guide для двух hosted ID; добавлен ограниченный transport guide в этом документе,
зарегистрированный `models.load_guides`, затем полный прогон прошёл.

## Источники и контракт версии

### Исправления после review 390ff20

Основание: независимый отчёт `review-opencode-390ff20.md`, findings F1/F2 и
fixture оборванного второго NDJSON шага. Причины устранены общим selector,
общим exact-list validator и проверкой завершения всех увиденных messageID.
Добавлены metadata diff и opt-in sync кандидатов; другие настройки профилей
сохраняются, платные/удалённые маршруты исключаются, пустой список закрывает gate.

Проверки этой правки (offline, **без новых live inference или metadata GET**):

```sh
.venv/bin/pytest -q tests/test_opencode_free.py tests/test_delegate.py tests/test_decide.py tests/test_routing_policy.py tests/test_routing.py tests/test_models.py
# 168 passed
.venv/bin/pytest -q tests/test_execution_outcomes.py tests/test_codex_subscription.py tests/test_claude_subscription.py tests/test_routing_review_regressions.py
# 75 passed
git diff --check
# passed
```

Итого 243 targeted/regression checks; полный прогон 379/1 выше относится к baseline,
а не повторялся для этой правки. Проверены совпадение preflight при changed pricing/
removed/no variants/all paid, malformed allowlists до probe, partial second step,
reconcile после finish, единственный новый free ID после refresh+sync, исключение
specialized/image/unsupported routes, сохранение прочих YAML-настроек и отзыв всех
кандидатов при paid-only каталоге. Live gate остаётся незакрытым: прежние два HTTP 429,
успешная генерация и observed model ID не подтверждены; квота не обходилась.

### Официальные источники

- [Модели: explicit run, варианты, опасность default fallback](https://opencode.ai/docs/models/).
- [Console: бесплатный chat требует OpenCode](https://opencode.ai/docs/console/models/).
- [Официальный v2 каталог](https://models.opencode.ai/experimental/v2.0/api.json?type=chat,decision,image).
- [v2.2.5 server-process: environment/config/catalog flags](https://github.com/anomalyco/opencode/blob/v2.2.5/packages/cli/src/server-process.ts).
- [v2.2.5 standalone: private child, random lease credential, EOF shutdown](https://github.com/anomalyco/opencode/blob/v2.2.5/packages/cli/src/services/standalone.ts).
- [v2.2.5 models-dev: схема цен и чтение локального snapshot](https://github.com/anomalyco/opencode/blob/v2.2.5/packages/core/src/models-dev.ts).
- [v2.2.5 OpenCode provider: signed-out public route](https://github.com/anomalyco/opencode/blob/v2.2.5/packages/core/src/plugin/provider/opencode.ts).
- [v2.2.5 config discovery: system/MDM/global/project](https://github.com/anomalyco/opencode/blob/v2.2.5/packages/core/src/config/discovery.ts).
- [v2.2.5 NDJSON события](https://github.com/anomalyco/opencode/blob/v2.2.5/packages/cli/src/run/noninteractive.ts).

Context7 resolve OpenCode → `/anomalyco/opencode`, затем query выполнены.
Ответ ссылался на dev: контракт дополнительно сверялся с tag v2.2.5 и локальным help.
Начальный Python urllib GET старого models.dev/api.json вернул 403; вместо обхода
gateway использован системный curl для открытых метаданных и затем правильный v2
URL из исходников версии. Upstream-код не исполнялся. Состояние продуктовых проектов
не менялось, потому что это разработка адаптера, а не стратегический кейс.

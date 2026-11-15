# Исполнение маршрутов: безопасное ядро

**Рабочий режим D1:** local-d1 теперь по умолчанию использует постоянный локальный
сервис с очередью и переиспользованием модели. One-shot лаборатория доступна только
через явный `local_d1.mode: lab`. Установка, команды, readiness, ограничения и
результаты приёмки: [D1_SERVICE.md](D1_SERVICE.md). Описания one-shot запуска и
прежние memory-blocker ниже сохранены как история предыдущего этапа.

## Шлифовка сквозного маршрута — 2026-11-15

Среда: codex внутри Orca. Baseline: `99c2685`. Этот раздел описывает текущую
интеграцию; расположенные ниже отчёты сохраняют ограничения прежних версий.

Запрос пользователя: автоматически выбирать модель **подзадачи**, включая Terra,
поднимать сначала effort, затем уровень; возвращаться вниз после сложного участка.
Документы последовательно читает Luna, старшие модели получают отдельные доказательные
карточки. Пользователь подтвердил автоматическую Astra для исключительно сложных задач.

### Что исполняется

- `route_request.classify_request`: local-d1 / ранее настроенный decision provider /
  T1 bootstrap. Выбор явный, отказ одного пути не включает другой. Локальная D1 —
  существующий pinned MLX CLI с network deny, memory guards, lock и полным распределением
  вероятностей. CLI `--classify-only --execute` выполняет только классификацию и выдаёт
  рекомендации модели/effort, даже если исполнитель недоступен.
- `document_flow.py`: один T1-вызов на документ, по порядку. UTF-8 text/Markdown,
  до 15 документов; PDF/таблицы предварительно обрабатываются существующими инструментами.
  Каждая карточка ограничена 6000 bytes, цитаты — 2400 bytes, общий пакет — 48000 bytes.
  Сохраняются путь, SHA-256, тип утверждения, полный текст цитируемых строк и локаторы.
  Исходники проверяются на изменение перед/после этапов. Невалидная/пустая карточка
  останавливает синтез. Совпадение цитаты не доказывает смысл утверждения: это явно указано.
- Синтез использует общий router: Terra для планов/сводок, Sol для сложного сравнения,
  Astra — только с предварительным разрешением. Полные оригиналы в синтез не передаются.
  D1 получает явно обозначенный metadata synopsis, исполнитель — все принятые карточки.
- `adaptive_execution.py`: до шести попыток содержательной доработки на одном host;
  сначала следующая ступень effort, затем модель/уровень по существующей лестнице.
  Каждый следующий самостоятельный запрос начинает со своего уровня. Ошибка транспорта,
  неверные metadata и недостаток данных не вызывают повторов или смены провайдера.
- Короткая проверка качества может использовать D1. Если полный вход проверки не
  укладывается в консервативный 1024-byte бюджет вместе с вопросами, сразу выбирается
  T1 verifier в том же host. Это выбор по размеру, не fallback после отказа D1.
  Verifier возвращает label, sure и цитату из входа; его уверенность не объявляется
  калиброванной вероятностью. Доступность T1-проверки проверяется до генерации с резервом
  8192 bytes на ответ. Неуверенность возвращает needs_review, не успешную приёмку.

### Предварительное разрешение T4

`automatic_routing.t4` содержит enabled, approval_ref, max_calls (1–2), абсолютный
usage_file в существующем каталоге. Общий счётчик относится ко всей согласованной области,
а не обнуляется на каждом запросе. Чтение — в policy preflight; атомарное резервирование
под file lock — непосредственно перед inference в Codex/Claude transport. Неудавшийся
запуск не возвращает слот. Повреждённый счётчик, смена approval_ref или исчерпание лимита
останавливают T4. Автоматический сброс не предусмотрен. Это лимит запусков, не hard cap денег.

По умолчанию opt-in отсутствует. Для текущей пользовательской настройки разрешение
зафиксировано локально как `user-request-2026-11-15-adaptive-routing`, общий лимит — два.
Подписочные профили, API grants, max/ultra этим не разрешаются. CLI delegate сохраняет
прежний запрет T4; ограниченный путь работает через общий router. T4 transport поддержан
только для Codex/Claude, где резервирование встроено перед запуском.

### Использование

```sh
# Классифицировать и показать рекомендацию; исполнителя не запускать
.venv/bin/python tools/route_request.py --prompt-file request.md --host codex --classify-only --execute
# Последовательно извлечь документы и синтезировать по карточкам
.venv/bin/python tools/document_flow.py --prompt-file request.md --source-file a.md --source-file b.md --host codex --execute
# Без --execute оба входа выполняют только preflight, без inference
```

AGENTS.md предписывает агенту использовать вход для новых продуктовых запросов и
самостоятельно готовить материалы. **GUI interception Orca не установлен.** Исполнение
задач с инструментами всё ещё требует host integration; текстовые adapters не получили
прав на команды/запись. Native agents не объявляются прошедшими Python billing gate.

### Проверки и открытые ограничения

Общий offline E2E использует настоящие document_flow → route_request → adaptive_execution
и fake только D1/CLI transports: T1 → T1 → Terra → T1 verifier, исходники в senior не попали.
Отдельные регрессии проверяют Sol high → xhigh → Astra, возврат к Luna, наличие Terra,
недостаток данных, отказ транспорта, quota across requests и прямой transport T4.

Живая попытка нового общего входа в этой сессии остановлена runtime **до загрузки модели**:
macOS pressure_level=2 при требуемом 1, swap=2802.38 MiB. Облачного fallback не было,
приложения и лимиты памяти не менялись. Прежний успешный MLX smoke остаётся историческим
доказательством runtime, не доказательством нового сквозного вызова. Повтор при normal
pressure остаётся открытым. Реальные Luna/Terra/Sol/Astra-вызовы не выполнялись:
подтверждённого Codex subscription profile в локальной конфигурации пока нет.

Качество маршрутизации и семантической проверки на реальных задачах не откалибровано;
offline/fake тесты доказывают управление потоком и ограничения, не превосходство моделей.

Итог проверки: `.venv/bin/python -m pytest -q tests` — **545 passed, 1 skipped**, 49.18 s;
пропуск — необязательный openpyxl. Независимое ревью нашло и после исправлений подтвердило
закрытие двух дефектов: переполнение D1 при проверке длинных карточек и обход quota через
прямой transport. Повторный targeted review — 18 adaptive tests passed. `git diff --check`
без ошибок. Текущий offline preflight выбирает Luna low, Terra medium, Sol high, Astra high,
но все четыре Codex-маршрута manual из-за отсутствующего включённого subscription profile.

## Общий вход на каждый запрос (2026-11-15)

Среда разработки: codex внутри Orca. Baseline этого изменения: `63a17d8910e4b4d0b9b2c1e4b69b906c3b0c8317`.
`tools/route_request.py` — явная точка подключения для одного запроса из любого host.
Она реализует classification → candidates/plan → rechecked start → result.
Это исполняемый scoped text pipeline, **не установленный перехват ввода GUI/Orca**.
Host или вызывающий скрипт должен передавать каждый новый запрос сюда до своей генерации;
сама установка репозитория этого не делает. Запросы напрямую в обычный чат идут мимо router.

```bash
# Offline preflight: план bootstrap, без классификации, subprocess и сети
.venv/bin/python tools/route_request.py --prompt-file request.md --context-file context.md --host codex
# Один classifier + один task; только после существующего gate каждого транспорта
.venv/bin/python tools/route_request.py --prompt-file request.md --context-file context.md --host codex --execute --receipt request-routing.json
```

`--host` можно повторить: это разрешённые оператором кандидаты в порядке предпочтения,
а не инструкция пробовать аккаунты после ошибки. Выбирается первый executable кандидат
до запуска задачи; после ошибки, quota, timeout или неверного результата переключения нет.
Порядок задаёт операционное предпочтение, а не измеренную экономию или качество моделей.
Default — subscription-only; новый вход не включает API, не получает ключи и не меняет конфиги.
Выбор модели переиспользует `delegate.select_route`, resolver и существующие tier/effort ladders;
это один кандидат модели на host, без нового рейтинга моделей и без поиска обхода квот.

Текст запроса и контекст сохраняются без усечения в JSON (`request`, `context`). Классификатор
получает оба поля и существующий `route_tier.yaml`, дополненный вопросами глубины и capability.
Он возвращает T1/T2/T3, normal/deep и text/tools; model/provider/endpoint/grants он не выбирает.
Normal выбирает первую ступень effort, deep — последнюю доступную ступень уровня.
Для OpenCode deep запрашивает high через существующий variant/capability gate; неизвестный
или неподдерживаемый вариант останавливает маршрут, а не подменяется default effort.
Задачи с инструментами, внешними данными, изображениями или изменениями файлов пока manual:
исполняемые адаптеры здесь изолированы и возвращают только текст. Оценка сложности моделью
не гарантирует правильный выбор: quoted evidence — структурная проверка, не calibration/eval.

Bootstrap отделён от маршрутизации задачи: фиксированный T1/low, `classify`, host codex
по умолчанию. Локальная настройка `automatic_routing.bootstrap_host` может указать
claude-code или другой уже разрешённый adapter. Resolver выбирает T1, policy проверяет
точные model/effort; bootstrap не вызывает router, `decide.emulate` или повторную классификацию.
Нужен разрешённый профиль и для classifier, и для задачи. Если его нет, результат manual,
без молчаливой подстановки дешёвой/дорогой модели. Неуверенность, отказ или неверная форма
ответа дают `classification.status=needs_review` и не запускают задачу.

Для **уже разрешённой** decision-модели оператор может явно задать
`automatic_routing.decision_provider: <имя из decision.providers>`. Используются существующие
System One protocol, `decide.decision_plan/call`, API grants и повторный gate перед HTTP.
Автоматического поиска платного провайдера или fallback к LLM после его отказа нет.
Без этой настройки используется только T1 bootstrap. JSON LLM не может объявить себе
System One confidence: принимаются labels с boolean sure и цитатой, проверенной кодом.
Пороги — 0.85 для bootstrap и 0.75 для decision provider; недопустимые/нечисловые значения
и confidence вне [0,1] отклоняются. T4 не автоматизируется, max требует прежнего согласования
в существующем ручном процессе, ultra запрещён; этот entrypoint согласие сам не выдаёт.

`classification`, `plan`, `start`, `result` разделены; plan — снимок до выполнения,
start содержит фактическую receipt adapter. `requested/transmitted/observed` не смешиваются.
Перед запуском reload конфигурации и повторный план; смена выбранного model/effort или
отзыв разрешения останавливают задачу. Transport повторно проверяет свой профиль/auth.
Один общий `delegate.execute_route` обслуживает прежний delegate и новый router:
невалидный ответ, metadata mismatch и transport failure не публикуют текст результата.
Старые ограничения внутренних CLI retries и race с hostile same-user процессом сохраняются.

JSON печатается в stdout, `--receipt` дополнительно сохраняет его в **новый** локальный файл.
Он содержит исходные данные: получателя и путь выбирает вызывающая сторона. Product ledger
и projects router не изменяет. Exit 0 — offline план либо successful execution; exit 3 —
manual/needs_review/ошибка выполнения, подробность в JSON; неправильные аргументы — exit 2.

| Host | Общий request interface | Исполнение | Автоматический trigger в host |
|---|---|---|---|
| Codex | `--host codex` | Проверенный ChatGPT profile на POSIX; Windows manual | Отсутствует; wrapper вызывается для каждого запроса |
| Claude Code | `--host claude-code` (alias claude) | Проверенный claude.ai profile на POSIX; Windows manual | Отсутствует; wrapper вызывается для каждого запроса |
| OpenCode | `--host opencode` | Fresh exact-zero adapter на macOS/Linux с pwd; Windows manual | Отсутствует; wrapper вызывается для каждого запроса |
| ZCode | `--host zcode` | Manual: isolation/auth/billing contract не доказан | Отсутствует |
| Cursor | `--host cursor` | Manual: picker/allowlist не execution adapter | Отсутствует |
| Gemini CLI | `--host gemini-cli` | Manual: нет проверенного adapter | Отсутствует |
| Antigravity App/IDE | `--host antigravity` / `antigravity-ide` | Manual: нет проверенного adapter | Отсутствует |
| DeepSeek Harness | `--host deepseek-harness` | Manual: нет проверенного adapter | Отсутствует |
| Kimi / Qwen | `--host kimi` / `qwen` | Manual: нет проверенного adapter | Отсутствует |
| Orca | Вызывает тот же CLI с выбранным host | Сам Orca не объявлен прошедшим Python gate | Перехват ввода не реализован |

Offline/fake регрессии: `tests/test_route_request.py`; реальные локальные fake subprocess
для Codex/Claude, контекст и сложность/effort, manual hosts, denied/authorized decision
provider с HTTP spy, confidence/invalid result, max, stale free catalog, повторный policy
gate и отсутствие recursive classification/paid fallback. Live inference, установки модов
и глобальные конфиги вне проверки. Тарифные подтверждения остаются заявлениями оператора.

Проверки общего входа: `rtk proxy .venv/bin/python -m pytest -q tests` — 445 passed,
1 skipped (необязательный openpyxl), 40.04 s. После уточнения outcome decision transport
и двух дополнительных регрессий выполнен отдельный финальный прогон
`rtk proxy .venv/bin/python -m pytest -q tests/test_route_request.py` — 38 passed.
`rtk proxy git diff --check` — без ошибок. Реальный CLI preflight без inference:
status manual, причина — отсутствует включённый Codex subscription profile, transmitted/observed null.
Product projects, doctor.py и глобальные настройки не изменены.

### Исправление review F1 к 42d715b

Безусловный POSIX import `pwd` в OpenCode вызывал traceback ещё до policy gate.
Четыре regression-сценария (preflight/execute, отсутствие pwd/симуляция win32)
сначала воспроизвели ModuleNotFoundError. Теперь импорт адаптера переносим:
`platform_error` проверяет платформу и доступность pwd, а общий router возвращает
classification unavailable/manual и result not_started до selector/transport.
Сам `system_boundary` сохраняет тот же запрет исполнения неподдерживаемой платформы.
Отсутствие чужого модуля не маскируется как отсутствие pwd; платёжные gates и отсутствие
fallback не менялись. Проверки — import/platform simulation на macOS, **не native Windows**.
Native Windows smoke остаётся непроверенным; Windows adapter этим исправлением не добавлен.
Финальная проверка: `rtk proxy .venv/bin/python -m pytest -q tests/test_route_request.py
tests/test_opencode_free.py tests/test_delegate.py tests/test_execution_outcomes.py` —
126 passed, 11.23 s; `rtk proxy git diff --check` — без ошибок. Все transport checks
offline/fake; subprocess/HTTP/inference в новых platform-regressions запрещены spies.

Среда разработки: codex внутри Orca. Baseline: `afd90c2cd712a23eb56557c9add43fe9fadd3028`.

Рекомендации `ledger route` и offline validate сохранены. Уровни, задачи и лестницы effort не заменены. Исполнение теперь отделено от рекомендации: установленный CLI, inventory, API key и legacy local config не разрешают запрос. Default — `subscription_only`, API выключен.

## Статусы и границы

`tools/routing_policy.py` — общий offline gate; `delegate` и `decide` повторно проверяют его непосредственно перед HTTP. `revise_lines` вызывает delegate и наследует gate. Остальные вызовы в tools: hooks запускают локальные ledger/models команды; ledger запускает только git. Автосканирование больше не запускает OpenCode и не обращается к selfhosted `/models`: их auth/config precedence не доказан. Чтение локального inventory остаётся рекомендацией.

- `executable`: Codex ChatGPT и Claude Code claude.ai с явно проверенными отдельными профилями; HTTP adapters selfhosted Chat Completions и decision System One — только после API opt-in.
- `manual`: неизвестны entitlement, auth isolation или billing enforcement. **Unknown Codex/Claude profiles и остальные подписочные CLI остаются manual.** CLI или argv builder не является разрешением запуска.
- `denied`: неверный маршрут, нет точного grant, превышен вход/контекст, запрещён effort.

Codex subscription adapter проверяет fingerprint, version, isolated config и ChatGPT auth; entitlement/overage подтверждает оператор. Одной настройки `verified: true` недостаточно. Нативные субагенты/Orca workers вне этих инструментов технически этим Python gate не контролируются и требуют отдельной host enforcement интеграции.

## Платёжная политика

Пример полного локального opt-in — `knowledge/routing.local.example.yaml`. Для API одновременно нужны:

1. `execution_policy.mode: api_opt_in` и `api.enabled: true`.
2. Grant точного `host/provider/endpoint/model`, отдельный ключ из названной env variable.
3. Конечный положительный `budget_usd`; подтверждение оператором **ранее установленного внешнего account cap** не выше бюджета, с `evidence_ref`.
4. Известные model/plan/user context limits, output/system reserves и allowlist effort.

`external_cap` — доверенное заявление оператора в локальной конфигурации, не результат проверки аккаунта кодом. Receipt честно содержит `operator_attestation_not_locally_verified`. Локальный бюджет не счётчик, не reservation между процессами и не гарантия hard cap; этот cap обязан обеспечиваться вне агента для используемого ключа/аккаунта, включая параллельные запросы. При неизвестном cap оставляйте подтверждение выключенным: маршрут manual. Реальные cap/тарифы/счета в этой реализации не проверялись.

Никакого API fallback: wrapper выполняет не более одного запуска; ошибки квоты/auth/таймаута не вызывают повторный запуск. Внутренние transport retries CLI зависят от версии и не измерены. Explicit `--provider` не переключается на LLM, даже если провайдер недоступен. Fallback decision→T1, если прямой провайдер не выбран, проходит тот же delegate gate; подписочный режим не превращается в API из-за ключа.

HTTP endpoint берётся только из конфигурации и сверяется как полный URL, без wildcard. Запрещены credentials/query/fragment, non-loopback HTTP, redirects и proxy-env. Глобальные настройки не меняются. CLI child env не наследует HOME, CODEX_HOME, cloud/API credentials, endpoint overrides, NODE_OPTIONS и ORCA session bindings; adapter добавляет временный HOME и явно разрешённый dedicated CODEX_HOME. Автоматического login/install нет.

## Контекст, preflight и receipts

`delegate.py --dry-run` и `decide.py --dry-run` не делают inference, CLI/network probes, login или записи в project state. Они печатают JSON с причиной, запрошенным маршрутом и объёмом файлов/сериализованного входа. Exit 0 означает успешное построение плана, **смотрите status**, это не разрешение исполнения. `--host` задаёт host явно; installed CLI не меняет выбор.

Проверяется полный подготовленный prompt/JSON. Для HTTP применяется консервативная оценка: число UTF-8 bytes сериализованного request сравнивается с `min(model_limit, plan_limit, user_limit) - output_reserve - system_reserve`. Это не точный tokenizer и не гарантия для произвольного сервера; оператор должен подтвердить применимость оценки и резервы. Selfhosted передаёт `max_completion_tokens`, включая reasoning, из output reserve; сервер должен поддерживать этот OpenAI-совместимый контракт. System One оставляет свой протокол без выдуманных token flags, его серверные накладные расходы и лимиты входят в внешнее подтверждение. Превышение даёт отказ: нет скрытого усечения, дорогого long-context или автоматического разбиения источников без сохранения связей.

Receipt хранит раздельно `requested`, `transmitted`, `observed`, auth/billing и digest политики. Observed — только явные поля ответа; отсутствующие model/effort/provider остаются null, не копируются из запроса. Несовпадение метаданных — quarantined, ответ не становится рабочим артефактом, wrapper не запускается повторно. Отсутствие model отмечается unconfirmed. Это серверное сообщение, а не независимая аттестация модели. Ошибки транспорта не печатают response body, ключи или адреса из исключений.

## Среды

Codex, Claude Code, ZCode/GLM, DeepSeek Harness, Kimi, Qwen, Gemini CLI, Antigravity App/CLI и IDE зарегистрированы как отдельные hosts либо рекомендации. Непроверенные возможности остаются manual, effort не переносится между провайдерами по имени host. Cursor больше не family Claude: только picker, предпочтения Composer/Grok и пустой по умолчанию точный allowlist; Auto/Balance/Intelligence и shell-out в чужой CLI не безопасный fallback. Included pool не означает бесплатно.

T0 не вызывает LLM. T4 автоматически не делегируется. max требует отдельного human approval, ultra и неизвестный effort запрещены; согласие на max не является согласием на API. Новые ID из inventory не добавляются в API allowlist автоматически.

## Проверка и оставшиеся gates

Исследование координатора: `docs/research/adaptive-routing-2026-11-15/providers/` (matrix, adaptation-plan, sources; в отдельном worktree). CCR не установлен, OAuth gateway и scripts не переносились. Для output bound сверены официальные [Chat Completions docs](https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/create) через Context7: `max_completion_tokens` включает reasoning.

Тесты работают на fake HTTP/CLI spies и локальных HTTP fixtures, без реальных аккаунтов. Обязательные проверки: `.venv/bin/python -m pytest -q tests`, `.venv/bin/python tools/ledger.py validate projects/example`. Команды и финальные результаты — ниже после прогона.

Незакрытые live gates: account entitlement и overage; фактическое enforcement external cap; версия/изоляция Codex и Claude; остальные CLI contracts; observed model/effort и tokenizer limits конкретного сервера. Live calls не разрешались и не выполнялись.

## CLI contract для методических инструкций

```bash
.venv/bin/python tools/delegate.py --project projects/<case> --task extract-claims --prompt-file <prompt.md> --input <source.md> --host codex --preflight
.venv/bin/python tools/decide.py --project projects/<case> --ask 'Готово?' --state '<материал>' --preflight
```

`--preflight` — alias `--dry-run`. У delegate JSON-план на верхнем уровне, у decide — в `route`. `status: manual|denied` означает остановку автоматического запуска; `requested` не доказательство `observed`. Для выполнения убирается preflight, но gate остаётся. Exit execution: 0 — результат, 1 — ошибка транспорта/результата, 2 — некорректный запрос или T0/T4/max/ultra gate, 3 — маршрут недоступен/не разрешён; у decide 4 — содержательная проверка не пройдена. При dry-run exit 0 означает сформированный план, а не executable. Отсутствующий проект/файл и ошибочные аргументы дают ненулевой exit.

`--backend` сохранён; `--host` — явный выбор host, противоречащие значения отклоняются. Grant приходит из проверенной локальной конфигурации. Codex/Claude subscription profile может дать executable без включения API; unknown profiles и другие hosts остаются manual.

### Результаты offline-проверки 2026-11-15

- `.venv/bin/python -m pytest -q tests` — **261 passed, 1 skipped**, 14.15 s (финальный код).
- `.venv/bin/python -m pytest -q tests/test_routing_policy.py tests/test_delegate.py tests/test_decide.py tests/test_models.py tests/test_routing.py` — 101 passed, 3.64 s перед финальной полной проверкой.
- `.venv/bin/python tools/ledger.py validate projects/example` — exit 0, ошибок 0; два существующих предупреждения HYP-002/CHG-001, материалы проекта не менялись.
- `git diff --check` — без ошибок.
- `.venv/bin/python tools/ledger.py route classify --env cursor` — exit 0, model не подменена Claude; picker/allowlist/manual явно указаны.
- Реальные процессы обоих CLI с `--preflight` в временном fixture-проекте: exit 0, status manual, transmitted.model null; снимок списка файлов до/после одинаков. Первоначальная попытка с projects/example корректно дала exit 2: там нет state/run_log.md; кейс не изменяли, проверку перенесли во временный fixture.

Регрессии: API key/env isolation; legacy config; точные endpoint/model grants; unknown enforcement/context; max/T4/ultra; context overflow без усечения; JSON preflight без spawn/network/записи; proxy/redirect; 401/403/429/500/timeout без повторов и paid fallback; metadata mismatch quarantine и unknown null; code-only gate без модели. Все HTTP-вызовы тестов — spies или loopback fixtures, не реальные провайдеры.

## Codex subscription adapter v1: follow-up к eef2fde

После замечания координатора API-only промежуточное ядро дополнено исполняемым Codex adapter. `subscription_only` разрешает included quota без отдельного денежного hard-cap ритуала. Unknown profiles по-прежнему manual, API отдельно выключен.

Оператор задаёт `execution_policy.codex_subscription`: absolute binary + SHA256 + exact version, заранее авторизованный отдельный CODEX_HOME, точные списки model/effort и context bounds; подтверждает entitlement, отключённый overage, проверку CLI contract и отсутствие managed/cloud config. Это заявление оператора, не измерение тарифа кодом. Нет установки, login, импорта OAuth, чтения/копирования auth.json или изменения общего профиля.

Preflight проверяет fingerprint, absolute paths, owner/private permissions (home 0700, auth.json 0600), отсутствие config/extensions/symlinks/unknown files в dedicated home и известных `/etc/codex/{config,managed_config,requirements}.toml`. На non-POSIX — manual. MDM/cloud absence требует отдельного подтверждения, поскольку не определяется одним local file. Runtime files Codex допускаются узким списком; неизвестный файл блокирует следующий вызов до проверки.

Runtime создаёт пустые cwd/HOME вне репозитория и проверяет ancestor configs. Минимальный child env получает только разрешённый CODEX_HOME. Exact binary последовательно проверяется через `--version`, затем `login status`; только точный `Logged in using ChatGPT` разрешает один `exec`. Auth output не сохраняется. Перед exec повторяется проверка профиля. Применяются documented forced ChatGPT auth, file credential store, OpenAI provider, фиксированные официальные endpoints, default service tier, read-only sandbox, never approval, ephemeral JSON. Shell tools и обе версии multi-agent выключены, чтобы делегат не создавал скрытые дочерние вызовы. Model/effort передаются явно. Quota/error/timeout завершают маршрут без нового wrapper invocation и без API/account fallback; внутренние CLI transport retries неизвестны. Ответ переносится в проект только после проверки metadata; временный каталог удаляется.

Проверенные первичные контракты (Context7 + upstream, 2026-11-15):

- [Config precedence](https://github.com/openai/codex/blob/main/codex-rs/config/src/loader/README.md): managed layers могут перекрывать session flags, поэтому одних `-c` недостаточно.
- [Loader sources](https://github.com/openai/codex/blob/main/codex-rs/config/src/loader/mod.rs): system, cloud и project layers.
- [Auth manager](https://github.com/openai/codex/blob/main/codex-rs/login/src/auth/manager.rs): forced login policy фильтрует загруженный auth.
- [Login status](https://github.com/openai/codex/blob/main/codex-rs/cli/src/login.rs): различает ChatGPT, API key, workload identity и tokens.
- [Config fields](https://github.com/openai/codex/blob/main/codex-rs/config/src/config_toml.rs): auth, credential store, provider, endpoints и service tier.
- [Feature definitions](https://github.com/openai/codex/blob/main/codex-rs/features/src/lib.rs) и [tool planner](https://github.com/openai/codex/blob/main/codex-rs/core/src/tools/spec_plan.rs): shell_tool, multi_agent и multi_agent_v2 выключаются через config overrides.

Реальный аккаунт/CLI не запускался. Fake CLI проверяет процессную границу, argv/env, runtime probes и stop до inference, но не тариф пользователя. Оператор должен проверить весь contract выбранной версии, включая поддерживаемые feature keys. Context reserve остаётся консервативным preflight, не доказанным token meter. При неизвестном профиле доступна ручная работа в текущем host; native/Orca не объявляется прошедшим Python gate.

## Исправления независимого review F1–F3

Отчёт координатора `review-core-eef2fde.md` воспроизведён в `tests/test_routing_review_regressions.py`: до исправлений 9 failed, 2 passed.

- F1: API grants, subscription models/efforts и Cursor allowlist требуют строгих списков и допустимых элементов. Scalars/dicts/bools/empty и substring не расширяют разрешение; отсутствующий default effort не подразумевает `[null]`.
- F2: фактический effort вычисляется до gate. Explicit effort, который adapter не передаёт, запрещён; default возможен только при запросе без effort и явном `[null]` в allowlist. `observed` сравнивается с transmitted contract, `request_satisfaction` отдельно отражает выполнение запроса.
- F3: runtime refusal записывает одну redacted receipt и одну строку routing refusal с null transmitted/observed и нулём inference calls. Для preflight сохраняется отсутствие записей. Runtime version/auth stop также сохраняет причину и `not_started`.

Проверки subscription adapter используют реальный subprocess с fake executable; credentials — пустой fixture-файл, без live inference. Одна инструментальная попытка обновления документации через heredoc получила encoding error до записи файлов; обновление выполнено через apply_patch.

Финальная проверка follow-up: `.venv/bin/python -m pytest -q tests` — **305 passed, 1 skipped**, 26.82 s; skip — необязательный openpyxl. `git diff --check` — без ошибок. Целевой прогон F1–F3 + Codex + policy/routing/decide до финальных timeout/mismatch assertions — 118 passed, 6.17 s. Успешный validate example из предыдущего этапа не повторялся: ledger, схемы и projects не изменены. Финальный CLI contract прежний (`--host codex --preflight`, alias `--dry-run`); новый opt-in находится только в локальном `execution_policy.codex_subscription`, не в флаге, который обходит policy. Preflight status executable требует последующих runtime probes, отказ runtime возвращает 3 и сохраняет receipt/log один раз.

## Review b8b216c: R1–R3 и Claude Code

Baseline этого исправления — `b8b216ccaa122daf4067d48a5d26b5bcd3bb1534`. Независимые R1/R2 воспроизведены до правки: `tests/test_execution_outcomes.py` — 8 failed. Дополнительные варианты включают JSON null/array/non-JSON, неверные usage, пустой ответ и изменение config во время auth probe.

**Итог исполнения.** Receipt записывается после единственного структурированного разбора и проверки ответа. `started` означает попытку передачи запроса исполняемому transport, а не доказанное начало серверной генерации; `wrapper_invocations` считает такие вызовы, исключая version/auth probes. `process_exit_code` сохраняет сырой exit CLI (null при HTTP, отсутствии запуска или неопределённом исходе); `transport_status_code` относится к HTTP adapter. `exit_code` — окончательный код задачи. Exit 3 уже запущенного CLI даёт task exit 1 / failed и сохраняет transmitted. Только настоящий отказ до вызова даёт not_started / task exit 3 / ноль inference calls.

`result` — succeeded, failed, invalid_result, quarantined либо not_started. Некорректный JSON/usage/пустой успешный ответ обрабатывается как invalid_result, не публикует ответ и оставляет одну receipt и одну строку журнала. Metadata не восстанавливается из requested: при невалидном результате она остаётся null. Decision HTTP также проверяет форму answers/usage перед финальной записью. Совместимый parse_output остаётся обёрткой, execution не разбирает stdout второй раз.

**Retries, согласованное ограничение R3.** `max_attempts=1` имеет явный `max_attempts_scope=wrapper_invocations`. Для Codex/Claude `internal_transport_retries=unknown_version_dependent`: wrapper не контролирует внутренние stream reconnect/request retries CLI и не заявляет их отсутствие. `inference_calls=null` после передачи запроса, в том числе при timeout. Транспортный reconnect/WebSocket→HTTP не равен API/account fallback. Wrapper не запускает новый процесс после ошибки и не меняет account/provider. Для included quota отдельный денежный hard cap не добавлялся; непроверенные retry flags не вводились. Основание ограничения: [upstream Codex provider](https://github.com/openai/codex/blob/main/codex-rs/model-provider-info/src/lib.rs) и [responses retry](https://github.com/openai/codex/blob/main/codex-rs/core/src/responses_retry.rs), указанные независимым reviewer; это не замер пользовательского binary.

### Claude subscription adapter

`--host claude-code` и `--host claude` используют `execution_policy.claude_subscription`; локальный пример добавлен в routing.local.example.yaml. Это отдельный opt-in с pin binary/version, dedicated config_dir, credential_store file либо macOS keychain, точными models/efforts/context и подтверждением included entitlement/отключённого extra usage/отсутствия managed layers. Default выключен. CLI/API key сами не разрешают вызов.

Preflight не запускает CLI. Он проверяет private owner directory, fingerprint, отсутствие settings/extensions/symlinks/unknown entries и системных managed directories. Native `.claude.json` допускается как account state, его содержимое не читается; credential file/keychain также не читается и не копируется wrapper. Runtime создаёт пустые HOME/cwd, передаёт только явный CLAUDE_CONFIG_DIR; API/auth-token/cloud/profile/proxy env не наследуются. Auth probe требует exit 0, `authMethod=claude.ai` и `configDirectory`, совпадающий с профилем. Поля email/org и другой auth output не сохраняются. Старый CLI без требуемого поля не допускается.

Native `--restricted` исключает user/project/local settings; явные settings включают forceLoginMethod=claudeai и disableAllHooks. Built-in tools и MCP отключены через tools/disallowedTools и strict empty mcp-config; slash commands/skills выключены. Затем повторно проверяется профиль и выполняется один print-вызов с model/effort и JSON output. Нет login, update, установки, keychain migrations или token gateway. Managed settings имеют приоритет над runtime settings, поэтому неизвестные managed/MDM/cloud sources означают manual, а не попытку переопределить их.

Первичные контракты, прочитанные через Context7 и официальный Markdown:

- [Authentication](https://code.claude.com/docs/en/authentication.md): отдельные CLAUDE_CONFIG_DIR аккаунты, native credential storage и его Keychain namespace; environment/helper credentials имеют собственный приоритет.
- [CLI reference](https://code.claude.com/docs/en/cli-reference.md): auth status JSON, configDirectory с v2.1.268, значения authMethod, restricted/tools/MCP/effort/print flags.
- [Configuration directory](https://code.claude.com/docs/en/claude-directory.md): CLAUDE_CONFIG_DIR распространяется также на native .claude.json.
- [Managed settings](https://code.claude.com/docs/en/managed-settings.md) и [settings reference](https://code.claude.com/docs/en/settings-reference): приоритет managed policy, forceLoginMethod и disableAllHooks.

Поддержка относится к явно проверенной версии со всем указанным CLI contract, не к произвольному установленному Claude. Обновлять/менять binary или dedicated profile одновременно с run запрещено организационно; immutable snapshot/lease и защита от hostile same-user процесса не реализованы. Entitlement, extra usage и отсутствие MDM/cloud — подтверждения оператора, не измерения. Реальные аккаунты/CLI не использовались. Для неизвестного профиля минимальный путь — текущий native host вручную после проверки его auth/плана; Python receipt не переносит разрешение на сторонний транспорт. ZCode, DeepSeek, Kimi, Qwen, Gemini, Antigravity и Cursor сохраняют capability/manual matrix; исполняемые adapters для них не выдуманы.

Проверка этого изменения: `rtk proxy .venv/bin/python -m pytest -q tests` — **336 passed, 1 skipped**, 38.12 s; skip — необязательный openpyxl. Прогон повторён потому, что завершение предыдущей process session стало недоступно после восстановления контекста; неподтверждённый результат не учитывается. После уточнения двух redacted error strings («no wrapper retry») выполнен `rtk proxy .venv/bin/python -m pytest -q tests/test_execution_outcomes.py tests/test_codex_subscription.py` — **29 passed**, 8.58 s. `rtk proxy git diff --check` — без ошибок. Ранее успешный validate example не повторялся: ledger/schema/projects не изменены. Все новые исполняемые fixtures — локальные fake CLI/HTTP spies; live inference/account changes отсутствуют.
# Нативные входы хостов — 15.11.2026

После предыдущих этапов добавлены project hooks Codex/Orca, Claude function-hook мод
по архитектуре optim-jev и настоящий Codex app-server middleware. Подробная матрица
«выбор → применение → приёмка» и команды: [NATIVE_HOST_ROUTING.md](NATIVE_HOST_ROUTING.md).
Native session route сохраняет host permissions/account и не включает автоматически
isolated delegate profile. Реальный Codex smoke: Luna/low переданы серверу, ответ `17`.
Основной Orca chat middleware не использует; Claude native test блокируется sandbox.

### Native document lane

Для текущего аккаунта Codex доступен явный `document_flow.py --native-codex-binary`
(вместе с `--host codex`). Он использует общую лестницу quality/effort через
передаваемые planner/executor, сохраняя стандартный isolated transport по умолчанию.
Неуверенный валидный D1 проходит один T1-review; policy/quota/transport отказ не
переклассифицируется. T4 здесь запрещён. Подробности и ограниченный live comparison:
[повторная проверка](research/live-recheck-2026-11-15/README.md).

### Четыре режима классификатора и T0-расчёты

Python-вход `document_flow.native_synthesis(..., routing_mode=...)` принимает
`d1`, `d1_luna` (default), `luna`, `none`. Последний требует заранее заданный
`baseline={"tier":"T2","effort":"medium"}`. Это выбор только классификатора:
исполнители и verifier остаются общими. CLI document lane сохраняет default.
Протокол сравнения: [FOUR_ARM_PROTOCOL](../evals/routing/FOUR_ARM_PROTOCOL.md).

`calculation_checks.check(context, answer)` обрабатывает явный JSON-контракт:
`calculation_sources` содержит source ID, locator и числовые поля;
`calculation_checks` — id, answer_pointer, expression и tolerance. Лист expression
ссылается на source/pointer; произвольный код, числовые литералы и ссылки на ответ
запрещены. Операции: sum, multiply, subtract, ratio, percent, weighted_mean.
Примеры контрактов: `evals/routing/four_cases.json`.

До вызова LLM проверяются источники и вычисляются значения Decimal; исполнителю
передаются результат и происхождение операндов. После ответа проверяются только
объявленные числовые поля. Несовпадение требует исправления по общей лестнице до
модельного verifier; неверный контракт даёт needs_data до вызовов. Контракт требует
включённого quality gate. Обычный текст без контракта остаётся not_applicable:
автоматическое извлечение всех формул и подтверждение истинности источника не
реализованы. Проверка цитаты verifier доказывает буквальное присутствие, а не
семантическую правильность. T0 не заменяет проверку остальных выводов.

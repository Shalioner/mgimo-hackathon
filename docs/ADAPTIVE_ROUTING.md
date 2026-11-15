# Исполнение маршрутов: безопасное ядро

Среда разработки: codex внутри Orca. Baseline: `afd90c2cd712a23eb56557c9add43fe9fadd3028`.

Рекомендации `ledger route` и offline validate сохранены. Уровни, задачи и лестницы effort не заменены. Исполнение теперь отделено от рекомендации: установленный CLI, inventory, API key и legacy local config не разрешают запрос. Default — `subscription_only`, API выключен.

## Статусы и границы

`tools/routing_policy.py` — общий offline gate; `delegate` и `decide` повторно проверяют его непосредственно перед HTTP. `revise_lines` вызывает delegate и наследует gate. Остальные вызовы в tools: hooks запускают локальные ledger/models команды; ledger запускает только git. Автосканирование больше не запускает OpenCode и не обращается к selfhosted `/models`: их auth/config precedence не доказан. Чтение локального inventory остаётся рекомендацией.

- `executable`: Codex ChatGPT с явно проверенным отдельным профилем; HTTP adapters selfhosted Chat Completions и decision System One — только после API opt-in.
- `manual`: неизвестны entitlement, auth isolation или billing enforcement. **Unknown Codex profiles и остальные подписочные CLI, включая Claude Code, остаются manual.** CLI или argv builder не является разрешением запуска.
- `denied`: неверный маршрут, нет точного grant, превышен вход/контекст, запрещён effort.

Codex subscription adapter проверяет fingerprint, version, isolated config и ChatGPT auth; entitlement/overage подтверждает оператор. Одной настройки `verified: true` недостаточно. Нативные субагенты/Orca workers вне этих инструментов технически этим Python gate не контролируются и требуют отдельной host enforcement интеграции.

## Платёжная политика

Пример полного локального opt-in — `knowledge/routing.local.example.yaml`. Для API одновременно нужны:

1. `execution_policy.mode: api_opt_in` и `api.enabled: true`.
2. Grant точного `host/provider/endpoint/model`, отдельный ключ из названной env variable.
3. Конечный положительный `budget_usd`; подтверждение оператором **ранее установленного внешнего account cap** не выше бюджета, с `evidence_ref`.
4. Известные model/plan/user context limits, output/system reserves и allowlist effort.

`external_cap` — доверенное заявление оператора в локальной конфигурации, не результат проверки аккаунта кодом. Receipt честно содержит `operator_attestation_not_locally_verified`. Локальный бюджет не счётчик, не reservation между процессами и не гарантия hard cap; этот cap обязан обеспечиваться вне агента для используемого ключа/аккаунта, включая параллельные запросы. При неизвестном cap оставляйте подтверждение выключенным: маршрут manual. Реальные cap/тарифы/счета в этой реализации не проверялись.

Никакого API fallback: одна попытка, ошибки квоты/auth/таймаута завершают вызов. Explicit `--provider` не переключается на LLM, даже если провайдер недоступен. Fallback decision→T1, если прямой провайдер не выбран, проходит тот же delegate gate; подписочный режим не превращается в API из-за ключа.

HTTP endpoint берётся только из конфигурации и сверяется как полный URL, без wildcard. Запрещены credentials/query/fragment, non-loopback HTTP, redirects и proxy-env. Глобальные настройки не меняются. CLI child env не наследует HOME, CODEX_HOME, cloud/API credentials, endpoint overrides, NODE_OPTIONS и ORCA session bindings; adapter добавляет временный HOME и явно разрешённый dedicated CODEX_HOME. Автоматического login/install нет.

## Контекст, preflight и receipts

`delegate.py --dry-run` и `decide.py --dry-run` не делают inference, CLI/network probes, login или записи в project state. Они печатают JSON с причиной, запрошенным маршрутом и объёмом файлов/сериализованного входа. Exit 0 означает успешное построение плана, **смотрите status**, это не разрешение исполнения. `--host` задаёт host явно; installed CLI не меняет выбор.

Проверяется полный подготовленный prompt/JSON. Для HTTP применяется консервативная оценка: число UTF-8 bytes сериализованного request сравнивается с `min(model_limit, plan_limit, user_limit) - output_reserve - system_reserve`. Это не точный tokenizer и не гарантия для произвольного сервера; оператор должен подтвердить применимость оценки и резервы. Selfhosted передаёт `max_completion_tokens`, включая reasoning, из output reserve; сервер должен поддерживать этот OpenAI-совместимый контракт. System One оставляет свой протокол без выдуманных token flags, его серверные накладные расходы и лимиты входят в внешнее подтверждение. Превышение даёт отказ: нет скрытого усечения, дорогого long-context или автоматического разбиения источников без сохранения связей.

Receipt хранит раздельно `requested`, `transmitted`, `observed`, auth/billing и digest политики. Observed — только явные поля ответа; отсутствующие model/effort/provider остаются null, не копируются из запроса. Несовпадение метаданных — quarantined, ответ не становится рабочим артефактом, повторов нет. Отсутствие model отмечается unconfirmed. Это серверное сообщение, а не независимая аттестация модели. Ошибки транспорта не печатают response body, ключи или адреса из исключений.

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

`--backend` сохранён; `--host` — явный выбор host, противоречащие значения отклоняются. Grant приходит из проверенной локальной конфигурации. Codex subscription profile может дать executable без включения API; unknown profiles и другие hosts остаются manual.

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

Runtime создаёт пустые cwd/HOME вне репозитория и проверяет ancestor configs. Минимальный child env получает только разрешённый CODEX_HOME. Exact binary последовательно проверяется через `--version`, затем `login status`; только точный `Logged in using ChatGPT` разрешает один `exec`. Auth output не сохраняется. Перед exec повторяется проверка профиля. Применяются documented forced ChatGPT auth, file credential store, OpenAI provider, фиксированные официальные endpoints, default service tier, read-only sandbox, never approval, ephemeral JSON. Shell tools и обе версии multi-agent выключены, чтобы делегат не создавал скрытые дочерние вызовы. Model/effort передаются явно. Quota/error/timeout завершают маршрут без retries/API/account fallback. Ответ переносится в проект только после проверки metadata; временный каталог удаляется.

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

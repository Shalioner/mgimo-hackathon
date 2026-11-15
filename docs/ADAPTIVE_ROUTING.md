# Исполнение маршрутов: безопасное ядро

Среда разработки: codex внутри Orca. Baseline: `afd90c2cd712a23eb56557c9add43fe9fadd3028`.

Рекомендации `ledger route` и offline validate сохранены. Уровни, задачи и лестницы effort не заменены. Исполнение теперь отделено от рекомендации: установленный CLI, inventory, API key и legacy local config не разрешают запрос. Default — `subscription_only`, API выключен.

## Статусы и границы

`tools/routing_policy.py` — общий offline gate; `delegate` и `decide` повторно проверяют его непосредственно перед HTTP. `revise_lines` вызывает delegate и наследует gate. Остальные вызовы в tools: hooks запускают локальные ledger/models команды; ledger запускает только git. Автосканирование больше не запускает OpenCode и не обращается к selfhosted `/models`: их auth/config precedence не доказан. Чтение локального inventory остаётся рекомендацией.

- `executable`: только прямые HTTP adapters selfhosted Chat Completions и decision System One, после явного API opt-in.
- `manual`: неизвестны entitlement, auth isolation или billing enforcement. **Все подписочные CLI, включая Codex и Claude Code, пока manual.** Существующие argv builders не являются разрешением запуска.
- `denied`: неверный маршрут, нет точного grant, превышен вход/контекст, запрещён effort.

Это сознательное сужение исполняемых adapters: из исследования нельзя вывести безопасный доступ к аккаунту пользователя. Автоматический Codex/Claude subscription запуск остаётся отдельным live gate: нужна изоляция config/hooks/plugins, pin auth, подтверждение плана/overage и версии. Одной настройки `verified: true` для включения CLI нет. Нативные субагенты/Orca workers, запущенные вне этих инструментов, технически этим Python gate не контролируются; их нельзя выдавать за проверенное исполнение, они требуют отдельной host enforcement интеграции.

## Платёжная политика

Пример полного локального opt-in — `knowledge/routing.local.example.yaml`. Для API одновременно нужны:

1. `execution_policy.mode: api_opt_in` и `api.enabled: true`.
2. Grant точного `host/provider/endpoint/model`, отдельный ключ из названной env variable.
3. Конечный положительный `budget_usd`; подтверждение оператором **ранее установленного внешнего account cap** не выше бюджета, с `evidence_ref`.
4. Известные model/plan/user context limits, output/system reserves и allowlist effort.

`external_cap` — доверенное заявление оператора в локальной конфигурации, не результат проверки аккаунта кодом. Receipt честно содержит `operator_attestation_not_locally_verified`. Локальный бюджет не счётчик, не reservation между процессами и не гарантия hard cap; этот cap обязан обеспечиваться вне агента для используемого ключа/аккаунта, включая параллельные запросы. При неизвестном cap оставляйте подтверждение выключенным: маршрут manual. Реальные cap/тарифы/счета в этой реализации не проверялись.

Никакого API fallback: одна попытка, ошибки квоты/auth/таймаута завершают вызов. Explicit `--provider` не переключается на LLM, даже если провайдер недоступен. Fallback decision→T1, если прямой провайдер не выбран, проходит тот же delegate gate; подписочный режим не превращается в API из-за ключа.

HTTP endpoint берётся только из конфигурации и сверяется как полный URL, без wildcard. Запрещены credentials/query/fragment, non-loopback HTTP, redirects и proxy-env. Глобальные настройки не меняются. Для потенциального CLI child env — узкий allowlist без HOME, CODEX_HOME, cloud/API credentials, endpoint overrides, NODE_OPTIONS и ORCA session bindings. Автоматического login/install нет.

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

`--backend` сохранён; `--host` — явный выбор host, противоречащие значения отклоняются. Grant не создаётся CLI flag, а приходит из проверенной локальной конфигурации. Подписочные маршруты пока требуют manual работы в текущем host; этот факт надо сохранять в методических инструкциях.

### Результаты offline-проверки 2026-11-15

- `.venv/bin/python -m pytest -q tests` — **261 passed, 1 skipped**, 14.15 s (финальный код).
- `.venv/bin/python -m pytest -q tests/test_routing_policy.py tests/test_delegate.py tests/test_decide.py tests/test_models.py tests/test_routing.py` — 101 passed, 3.64 s перед финальной полной проверкой.
- `.venv/bin/python tools/ledger.py validate projects/example` — exit 0, ошибок 0; два существующих предупреждения HYP-002/CHG-001, материалы проекта не менялись.
- `git diff --check` — без ошибок.
- `.venv/bin/python tools/ledger.py route classify --env cursor` — exit 0, model не подменена Claude; picker/allowlist/manual явно указаны.
- Реальные процессы обоих CLI с `--preflight` в временном fixture-проекте: exit 0, status manual, transmitted.model null; снимок списка файлов до/после одинаков. Первоначальная попытка с projects/example корректно дала exit 2: там нет state/run_log.md; кейс не изменяли, проверку перенесли во временный fixture.

Регрессии: API key/env isolation; legacy config; точные endpoint/model grants; unknown enforcement/context; max/T4/ultra; context overflow без усечения; JSON preflight без spawn/network/записи; proxy/redirect; 401/403/429/500/timeout без повторов и paid fallback; metadata mismatch quarantine и unknown null; code-only gate без модели. Все HTTP-вызовы тестов — spies или loopback fixtures, не реальные провайдеры.

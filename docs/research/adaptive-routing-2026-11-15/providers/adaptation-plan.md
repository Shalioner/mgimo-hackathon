# Минимальный план адаптации

Рекомендация: расширить существующие routing.yaml / models.py / delegate.py малым контрактом adapters и платежной политикой. CCR не устанавливать; оригинальный Ben AI ZIP не получен. Из аналога взять разделение выбора, передачи и наблюдения, реализовать в зрелом Python-стеке. Это предложение, код продукта не изменён.

## Baseline и наблюдения

Baseline `afd90c2cd712a23eb56557c9add43fe9fadd3028`, ветка `research/mgimo-video-2026-11-15`; перед работой status и diff были пустыми. Просмотрены routing.yaml, delegate.py --help, models.py --help и точечно delegate.py. Среда codex подтверждена ledger env; ledger status прочитан, проекты не изменены. models check startup сообщает новую opencode/gemini-4-hydrogen; классификация модели вне этой задачи, auto-enable запрещён.

- `tools/delegate.py:BACKEND_FAMILY` связывает backend с family; `pick_backend` выбирает по установленному executable, не по entitlement. Сам факт наличия CLI не даёт разрешения использовать его счет.
- `child_env` убирает ORCA_*, сохраняя остальное окружение. Это не платежная изоляция: env API keys/base URLs могут менять auth или получателя.
- EFFORT_BACKENDS = codex/selfhosted; Claude CLI может поддерживать --effort [S06], но текущий delegate его не передаёт. Нельзя записывать requested effort как выполненный.
- model/effort в журнале — выбранные значения; parse_output извлекает ответ/usage, а не доказательство модели. Пробел не закрывается успешным exit code.
- selfhosted_ready проверяет http(s) URL: название selfhosted не означает локальный или бесплатный backend.
- `routing.yaml` содержит decision.providers с выбором по наличию key, fallback llm, платный OpenCode и cursor.family=claude. Платежный gate должен охватывать decide.py и все пути вызова, а не только основной delegate.
- dry-run создаёт каталог delegated перед веткой выхода; в новом контракте plan/preflight должны быть без spawn/network и без state side effects. Сам dry-run здесь не запускался, чтобы не писать projects.
- Автовыбор новейшей модели по glob инвентаря не доказывает plan eligibility, billing class или поддержку effort.

## Контракт adapter v1 (предложение)

```text
Adapter.probe(local_evidence) -> Capabilities
Adapter.plan(request, capabilities, billing_policy) -> LaunchPlan | Manual | Denied
Adapter.observe(redacted_events) -> ExecutionEvidence
```

`probe` не делает inference, refresh токенов, login, установку и запись account config. Должен отличать absent, unsupported и unknown. CLI help/version допустимы лишь для известных установленных executable; новые неизвестные бинарники не запускать. Auth status обрабатывать без вывода секретов. Возможность CLI help не означает поддержку нужного flag данной моделью.

```yaml
request:
  task: extract-claims
  tier: T1
  host: codex
  provider: openai
  model: exact-allowed-id
  effort: low
  auth_profile_ref: subscription-local   # ссылка, не credential
  billing_mode: subscription_only
  context: {manifest: inputs.json, input_tokens_estimate: null, reserve_output_tokens: 2048}
capabilities:
  host_version: verified-version
  model_select: true
  effort_values: [low, medium, high]     # только проверенные, не универсальная лестница
  subagent_model_select: unknown
  auth_enforcement: verified
  billing_class: included_quota
  overage_disabled: true
  evidence_refs: [local-status, official-doc]
plan:
  status: executable                    # либо manual/denied
  argv_redacted: []
  env_allowlist_names: []
  allowed_endpoint: verified-endpoint
  credentials_ref: subscription-local
  max_attempts: 1
  policy_digest: sha256
receipt:
  requested: {host: codex, provider: openai, model: exact-allowed-id, effort: low}
  transmitted: {model: exact-allowed-id, effort: low, mechanism: cli_flag}
  observed: {provider: null, model: null, effort: null, evidence: absent}
  usage: {input: null, output: null, cache_read: null, cache_write: null}
  billing: {mode: subscription_only, marginal_charge_usd: null, state: unconfirmed}
```

Пример структурный, не фактическая capability какого-либо аккаунта; `2048` — проектный резерв, который настраивается, а не измерение. `observed=null` не заменять transmitted; отсутствие effort в ответе не считать low. При несовпадении модели/получателя/оплаты — остановить последующие вызовы, записать нарушение, результат карантинировать для ручной приёмки. Постфактум evidence не может вернуть уже потраченные деньги, поэтому до запуска обязательна техническая enforcement.

## Fail-closed billing

1. Default `subscription_only`, `api.enabled=false`, `allow_additional_charge=false`; предпочтения пользователя сужают allowlist, не расширяют entitlement. Бесплатные/локальные пути включаются отдельным подтверждённым профилем, не по названию.
2. Перед spawn/HTTP решить tuple host + provider + endpoint + model + auth + billing + effort + context class. Любое обязательное unknown → manual/denied, никакого fallback на первый CLI или current без записи неизвестности.
3. Существование ключа не разрешает API. Различать подписочный credential для Coding Plan и metered API credential. Для API требовать явный opt-in scope/provider/endpoint/model/budget + key; одобрение max не является одобрением платежа.
4. Строить child env из узкой allowlist, не удалять несколько известных ключей из полного окружения. Изолировать user/project config, proxies, cloud ADC, API base URLs, plugins и hooks; если host нельзя безопасно pin к подписочному профилю без изменения account settings — manual. Не копировать секреты в report/config repo.
5. Subscription quota exhaustion, 401/403/429 и timeout останавливают цепочку. Повтор после неопределённого timeout может удвоить списание; default одна попытка. Не делать API fallback, top-up, extra usage, смену account или provider.
6. Даже в subscription режиме overage/credits должны быть технически заблокированы либо доказан закрытый путь host. Публичная таблица цен недостаточна. Локальное budget preflight не гарантирует жёсткий лимит, если провайдер не даёт hard cap; при требовании нулевого дополнительного платежа такой маршрут manual.
7. Тот же gate обязателен для decision model, LLM fallback, verifier, retry, native subagent и outer Orca worker. Подписка одного host не переносится в другой автоматически.
8. Детектирование новой модели — предложение обновить allowlist; не разрешать её автоматически. Сохранять T4/max ограничения и never ultra; любое новое effort, например ultracode в docs, запрещено пока не классифицировано.

## Контекст и экономия

Бюджет: `input_budget = min(model_limit, plan_context_limit, user_cap) - output_reserve - system_tools_reserve`. Все величины имеют источник/оценку; неизвестный лимит не значит бесконечный. Проверять полный serialized prompt, instructions, tools и refs, а не только текст задачи. В output reserve включать известную специфику reasoning токенов; не считать cache бесплатным.

Делегату передавать узкую задачу, acceptance, baseline, явный manifest разрешённых исходников и краткое состояние с ссылками. Не наследовать полную переписку по умолчанию. При переполнении — deterministic split по независимым источникам или предложить сужение; не молча отрезать evidence/ограничения и не перейти к дорогому long-context. Каждый chunk сохраняет source locators. В handoff оставить next step, decisions, evidence IDs, unknowns; authoritative ledger не заменять summary.

Не обещать процент экономии по ролику. Будущий эксперимент отдельно одобряется: одинаковая задача/качество, полные затраты lead+delegates+retries+cache, время и human rework. API-equivalent cost — оценка, не сумма списаний подписки.

## Порядок реализации

1. Добавить schema/pure policy и migration явного legacy config в recommendation-only; существующие tiers/tasks сохранить. Без подключения новых providers.
2. Ввести route receipt и adapter boundary в delegate; писать requested/transmitted/observed отдельно. Тот же gate подключить в decide/selfhosted/revise callers после инвентаризации вызовов.
3. Первые исполняемые adapters — Codex и Claude только для подтверждённых подписочных профилей; остальные строки matrix остаются manual, пока не доказаны version/auth/billing capabilities.
4. Добавить Cursor профиль рекомендаций Composer/Grok без автоматического third-party/Auto; никаких вызовов через чужой CLI на основании имени host.
5. Только после offline gates и отдельного разрешения на расход — bounded smoke на конкретном аккаунте; без него статус «план проверен, платный путь не проверен».

## Offline тесты при реализации (без платных вызовов)

- Fake executable + subprocess spy: no inference/network; snapshot argv/env для каждого supported version. API-key присутствует в parent env → ни включения API, ни утечки в child/log.
- API mode без opt-in, без budget, без key, с чужим endpoint → denied до spawn. max approval не даёт billing approval.
- Subscription auth + API key override, cloud ADC, proxy/base URL, persisted paid-provider config → отказ либо доказанное изолирование. Непроверенная credential precedence → manual.
- 429/401/timeout/unknown response → нет provider/model/account fallback и повторного вызова.
- Cursor legacy/new plan fixtures; Composer недоступен, Grok fast/long-context, Auto third-party, exhausted quota → не меняют биллинг молча.
- Запрошенный low, CLI без --effort → transmitted null, observed null; server response model differs → mismatch. Отсутствие metadata не синтезирует observed.
- JSONL malformed, partial stream, interrupted run, usage missing → неполный receipt, unknown usage; cache read/write не подменяют total; secrets редактируются до сохранения ошибки.
- T4/max/ultra gates, newly discovered model, unsupported subagent override → не обходят policy.
- Unicode/paths/oversized inputs: сумма budget с reserves, deterministic splitting, source locators сохраняются; no silent truncation.
- Offline sockets запрещены; dry-run не меняет accounts/projects, не refresh OAuth и не исполняет hooks. Проверить registry каждого model-call entrypoint, чтобы decide fallback не обходил gate.

Эти тесты пока предложены, не написаны/не выполнены: задача — исследование. Приёмка исследования: реальные источники, SHA/license и скачанные git objects; ограничения матрицы явны. Открытые gates реализации — account entitlement, hard billing enforcement и live route evidence.

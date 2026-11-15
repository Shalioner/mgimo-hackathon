# Независимое review core eef2fde

Дата: 2026-11-15. Reviewer, среда Codex/Orca. Только чтение продукта; единственная запись — этот отчёт.

Baseline: `afd90c2cd712a23eb56557c9add43fe9fadd3028`.
Core: `eef2fde37388b6cf0cd85acb30a41f4267e1a10a`.
Cross-contract: `87edc862e8d75a28ebdad51cd9b53f650408a2b4`, включая `63892afd377291b3c69be14731e13d1bfb681ac3`.
Все номера строк ниже относятся к core commit, не к меняющемуся worktree исполнителя.

**Вердикт: changes requested.** Помимо известного отсутствия исполняемых subscription CLI, подтверждены два обхода точных разрешений и потеря журнала отказов. Проверки final followup commit здесь нет.

## F1 — P1: scalar YAML превращает точный allowlist в поиск подстроки

Место: `tools/routing_policy.py:100–103`, `125`.

Вход: полностью разрешённый API grant, но `models: premium-model` вместо `models: [premium-model]`; запрос модели `premium`. Отдельный случай: `efforts: xhigh`, запрос `high`. Это обычная валидная YAML-запись, которую загрузчик не валидирует по схеме execution policy.

Наблюдение в точном commit: оба плана имеют `status: executable`. Первый body содержит `model: premium`, хотя оператор указал только `premium-model`. Оператор `in` на строке проверяет подстроку, а не точный элемент списка. Проверка списка отсутствует также у Cursor allowlist. Это не обход при выключенном API: требуются остальные разрешения; дефект расширяет scope уже включённого API.

Минимальный fix: до выбора grant валидировать коллекции и их элементы (models — непустой list[str], efforts — list допустимых значений, allowlist — list[str]); неверные типы возвращают denied с причиной. Не преобразовывать произвольную строку в коллекцию неявно. Регрессии: scalar, dict, bool, пустой список и substring должны не разрешать вызов.

## F2 — P1: разрешён requested effort, но отправлен неразрешённый server default

Место: `tools/delegate.py:101–116`; связанная проверка `tools/routing_policy.py:125` и `137–138`.

Вход: действующий API grant `efforts: [low]`, `selfhosted.send_effort: false`, запрос `effort=low`.

Наблюдение: `selfhosted_plan` возвращает executable, но в body нет `reasoning_effort`. Тот же фактический запрос с честным `effort=None` возвращает denied, поскольку default не разрешён grant. Таким образом, флаг сериализации обходит capability/effort gate. Сервер вправе использовать иной default; при отсутствии metadata этот результат принимается unconfirmed. Вторая сторона дефекта: явный ответ `effort: high` карантинируется как несовпадение с low, хотя low вовсе не передавался.

Минимальный fix: вычислять transmitted effort до gate и проверять как запрос пользователя, так и реально исполняемый режим. Если low обязателен, а adapter его не умеет передать, вернуть manual/denied; если default отдельно разрешён, сохранить requested low / transmitted null и явно обозначить неподтверждённое выполнение пожелания. Сравнивать observed с реально переданным контрактом и отдельно оценивать удовлетворение requested, не смешивая эти две проверки. Добавить регрессию send_effort=false с grant без null.

## F3 — P2: runtime manual/denied исчезают из истории маршрутизации

Место: `tools/delegate.py:244–246`; аналогичный ранний выход `tools/decide.py:400–402` (ветка `if receipt and ...`).

Вход: обычный запуск delegate без --preflight/--dry-run, default subscription_only; либо полный API config с превышенным контекстом. Наблюдение по коду и новому `tests/test_routing.py::test_failed_call_is_logged_and_nonzero`: return 3 происходит до записи receipt и cmd_log. Сохраняется лишь stderr, в run_log нет ни выбранного маршрута, ни причины отказа. Baseline логировал ошибки реального вызова; теперь обычные остановки по новой policy не оставляют историю. Это мешает продолжению сессии, учёту неудачных попыток и обязательной записи решения маршрутизации (§6 правил проекта).

Минимальный fix: runtime-отказ записывать один раз как blocked/manual/denied с redacted причиной и requested полями; transmitted/observed остаются null. Для preflight сохранить отсутствие записей. Не запускать транспорт ради получения receipt.

## Воспроизведение и проверки

Исходники извлечены только через `git show eef2fde:<path>` в временную папку `review-eef2fde-qekde9t7`; не выполнялись неизвестные scripts, subprocess CLI или сетевые запросы. Использована существующая .venv, без установки.

30 точечных существующих тестов прошли, 4 deselected:

```sh
PYTHONPATH=<точная-временная-копия>/tools .venv/bin/python -m pytest -q <точная-временная-копия>/tests/test_routing_policy.py -k 'not dry_run and not delegate_metadata'
```

Это проверки policy, отказов transport, redirects/proxy, code-only gate. Полный suite не повторялся. Пройденные тесты не покрывают F1/F2.

Минимальная воспроизводимая fixture для F1/F2 (Python, импорт delegate из точного commit; сеть не вызывается):

```python
import os
import delegate as D
import routing_policy as P
os.environ['REVIEW_FAKE_KEY'] = 'not-a-real-key'
g = dict(host='selfhosted', provider='example',
         endpoint='https://example.test/v1/chat/completions',
         models=['premium-model'], efforts=['low'], budget_usd=1,
         external_cap=dict(confirmed_by_operator=True, amount_usd=1,
                           evidence_ref='fixture'),
         context=dict(model_limit=128000, plan_limit=128000,
                      user_limit=100000, output_reserve=1024, system_reserve=2048))
c = dict(execution_policy=dict(mode='api_opt_in', api=dict(enabled=True, grants=[g])),
         selfhosted=dict(base_url='https://example.test/v1', provider='example',
                         api_key_env='REVIEW_FAKE_KEY', send_effort=False))
g['models'] = 'premium-model'
assert D.selfhosted_plan(c, 'premium', 'hello', 'low')[0]['status'] == 'executable'
g['models'] = ['premium-model']
g['efforts'] = 'xhigh'
assert D.selfhosted_plan(c, 'premium-model', 'hello', 'high')[0]['status'] == 'executable'
g['efforts'] = ['low']
r, _, body = D.selfhosted_plan(c, 'premium-model', 'hello', 'low')
assert r['status'] == 'executable' and 'reasoning_effort' not in body
assert D.selfhosted_plan(c, 'premium-model', 'hello', None)[0]['status'] == 'denied'
r['transmitted'] = dict(model='premium-model', effort=None)
assert not P.observe(r, dict(model='premium-model', effort='high'))
```

## Ограничения, которые не следует выдавать за новые дефекты

- Все subscription CLI manual — известный функциональный блокер исходной задачи, а не завершённый адаптивный роутинг. Followup исполнителя здесь не проверялся.
- External cap — заявление оператора, не локальный счётчик. Код явно сообщает это, поэтому отсутствие concurrent reservations не называю скрытой гарантией. Два параллельных вызова разрешены без суммирования: внешний cap обязан действовать на общий account/key. До независимой проверки такого cap нельзя обещать фактическое соблюдение бюджета. Нужна привязка подтверждения к действующему аккаунту/ключу и сроку, если продукт собирается обещать enforcement.
- Для included subscription с технически исключённым metered fallback/extra usage требование отдельного денежного hard cap избыточно. Достаточно доказанного auth/billing пути, разрешённой квоты и остановки на исчерпании. Не следует переносить обязательный API external_cap на любую подписочную работу или превращать неизвестную денежную оценку в вечный запрет исполнения.
- Проверка serialized UTF-8 bytes против token capacity консервативна, это прямо оговорено. Selfhosted дополнительно использует JSON ensure_ascii=True, поэтому кириллица/emoji ещё сильнее раздувают оценку. Это ограничение полезного контекста, не доказанный overflow; для универсальных tokenizer/server overhead гарантия не доказана. Улучшение — именованные единицы, модельный tokenizer либо документированная conservative bound, без тихого truncation.
- Default no-API, повторный gate на HTTP entrypoints, отключённые redirects/proxy, отсутствие retry и error-body redaction по проверенным веткам работают. Скрытого network/auth fallback в этих ветках не найдено. Это не live-проверка аккаунтов.
- `revise_lines` использует D.main, а decision emulation — тот же delegate: отдельного обхода policy там не найдено. Независимый native/Orca транспорт Python-код не контролирует, документация честно это отделяет.
- Core не раздувает scheduler/DAG и сохраняет зрелые T0/T4/max/ultra gates; дальнейшее решение лучше строить adapter-specific поверх общего policy. Строгая схема разрешений полезнее новых уровней обёрток. Внешние account configs и projects не менялись.

## Cross-contract: 87edc862 (включая 63892afd)

Проверены AGENT_TASK_CONTRACT, diff self-audit и изменения workflows/source-intake/research-plan относительно baseline.

- `delegate --host codex --preflight` и alias --dry-run существуют в eef2fde; status наверху JSON. У decide статус в route; код 0 preflight не приравнен к разрешению. Документ требует повторный gate при исполнении и не делает receipt другого транспорта универсальным разрешением.
- Native/Orca не разрешены обходом Python gate: нужен собственный подтверждённый gate. Manual допускает работу текущего агента и T0; финальная правка прямо запрещает считать manual вечным свойством подписок. Это соответствует будущему subscription adapter, не снимая требования его проверить.
- C1–C3, verify/decide gates и validate после записи сохранены/усилены. Awaiting_human отделён от технической приёмки. Self-audit не даёт продуктовой задаче полномочий менять общую методику или память; исправление инструмента принадлежит разработке.
- Контракт preflight не исправляет F1/F2: executable в дефектном core всё ещё может разрешить неподходящий фактический запрос. Поэтому методический commit не закрывает блокеры core.
- Нового самостоятельного блокера в проверенном методическом diff не найдено. Live-поведение модели, загрузка правил свежей сессией и подписочный runtime остаются открытыми проверками; markdown не является их доказательством.

## Следующая приёмка

Проверить final core commit отдельно: F1/F2, runtime-аудит отказов, работающие Codex/Claude subscription adapters без денежных hard-cap ритуалов для закрытого included пути, auth isolation и честные receipts на fake CLI fixtures; затем общий пользовательский сценарий и обязательные gates у интегратора. Реальные платные/подписочные вызовы этим review не разрешались и не выполнялись.

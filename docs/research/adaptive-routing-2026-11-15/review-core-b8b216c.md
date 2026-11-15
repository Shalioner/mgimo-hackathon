# Независимое review core b8b216c

Дата: 2026-11-15. Среда: Codex/Orca. **Вердикт: changes requested.**

Проверен чужой core `b8b216ccaa122daf4067d48a5d26b5bcd3bb1534` поверх `eef2fde37388b6cf0cd85acb30a41f4267e1a10a`, общий baseline `afd90c2cd712a23eb56557c9add43fe9fadd3028`. Исходники читались через git show; для offline исполнения извлечён git archive именно b8b216c. Текущий worktree продукта не использовался как источник core и не менялся. Мой методический commit `87edc862` не является предметом этой независимой приёмки; его уже проверял другой reviewer.

F1 и F2 закрыты на исходных воспроизводимых входах. F3 закрыт для отказов до запуска, но новый adapter смешивает отказ и код завершения уже запущенного CLI. Codex subscription adapter полезен как ограниченный первый путь текстового делегирования: он действительно запускает проверяемый fake CLI с изоляцией, а не только печатает manual. Полную переносимость/готовность к реальному аккаунту это не доказывает.

## R1 — P2: код 3 запущенного CLI превращается в ложное «0 inference calls»

Место: `tools/delegate.py:213–217`, `354–359`; `tools/routing_policy.py:98–112`.

`call_codex` возвращает сырой `proc.returncode`. Если уже запущенный exec завершился кодом 3, main принимает это за собственный preflight/runtime refusal и вызывает audit_refusal. Эта функция стирает transmitted/observed, пишет `result=not_started` и журнал `0 inference calls`. При неизвестной причине завершения нельзя утверждать, что inference отсутствовал.

**Воспроизведено:** в существующем fixture subscription заменено финальное событие fake CLI на `sys.exit(3)`; fake уже записал вызов exec и ответ. Наблюдалось: один exec; main=3; receipt `not_started`, transmitted.model=null; журнал `0 inference calls`. Контрпример: отказ version/auth до exec корректно остаётся not_started.

**Минимальное исправление:** отделить тип исхода адаптера от process exit code; сохранять `started`, сырой CLI code и известные transmitted. После запуска классифицировать failure/unknown, не выдавать отсутствие inference за измеренный факт. Добавить сценарий exit3-after-exec к существующему test_codex_subscription.

## R2 — P2: receipt сохраняется до проверки результата; невалидная форма JSON обрывает журнал

Место: `tools/delegate.py:70–80`, `326–331`, `360–375`.

Первый JSON-проход main фильтрует не-объекты, но затем parse_output повторно разбирает тот же stdout и без проверки вызывает `ev.get`. Строка `[]` даёт AttributeError; receipt уже сохранён как `response_received`, exit_code=0, а run_log остаётся пустым. Ещё один воспроизведённый случай: пустой файл ответа при exit0 возвращает из main код 1 и не создаёт результат, но receipt по-прежнему содержит exit_code=0 / response_received. Название response_received может описывать транспорт, однако отдельного outcome задачи в receipt нет; ошибка валидации результата оказывается скрыта от потребителя receipt.

**Минимальное исправление:** разобрать и проверить структуру событий/usage и содержимое ответа до финальной записи receipt; неизвестные формы обрабатывать как контролируемый invalid_result с одним журналом, не как exception. Явно разделить process_exit_code и окончательный outcome/exit_code задачи. Сохранить unknown metadata как null; нельзя заполнять их requested ради успешной проверки.

**Fixtures:** `test_repro_valid_json_array_crashes_without_journal` и `test_repro_empty_answer_receipt_says_success` ниже. Эти тесты подтверждают наличие дефектов, а не прохождение желаемой приёмки.

## R3 — P2: одна CLI-попытка не обеспечивает заявленный запрет внутренних inference retries

Место: `tools/delegate.py:176–180`, `193–195`, `214`; `tools/routing_policy.py:145` (`max_attempts=1`); `tests/test_codex_subscription.py:130–136`, `151–163`; `docs/ADAPTIVE_ROUTING.md`, раздел Codex adapter.

Wrapper не повторяет subprocess, но список forced settings не контролирует внутренние request/stream retries Codex. Тесты считают `exec` и искусственно бросают subprocess timeout — они не моделируют повтор запроса внутри одного живого CLI.

Проверен первичный upstream через Context7 и прямое чтение официальных исходников: [model-provider-info](https://github.com/openai/codex/blob/main/codex-rs/model-provider-info/src/lib.rs) задаёт defaults request=4 и stream=5; [responses_retry](https://github.com/openai/codex/blob/main/codex-rs/core/src/responses_retry.rs) содержит цикл reconnect и переход WebSocket→HTTP. Это transport fallback, **не** доказанный API/account fallback. Сведения относятся к прочитанному upstream, не к измеренному поведению пользовательского binary. В том же upstream merge встроенного openai не принимает произвольные provider overrides, поэтому нельзя просто дописать непроверенные flags и объявить запрет реализованным.

**Вывод:** отсутствие повторного subprocess доказано offline, отсутствие внутренних inference retries — нет; утверждение документации «ошибка/timeout завершают маршрут без retries» шире реализации. Включение профиля с cli_contract_reviewed=true не измеряет это свойство.

**Минимальное исправление/приёмка:** для поддерживаемой версии определить и доказать её реальный retry-контракт либо явно сузить max_attempts до числа запусков wrapper, отразить внутренние retries как отдельное ограничение и согласовать это с исходной политикой одной попытки. Не делать это поводом требовать денежный API hardcap для included subscription. Нужен offline transport harness конкретной версии/контрактный fixture, а не новый платный прогон и не тест «exec вызван один раз». Реальный Codex для проверки не запускался.

## Проверка прежних F1–F3

| Finding | Проверка точного b8b216c | Результат |
|---|---|---|
| F1 | Scalar models='premium-model' при запросе premium; scalar efforts='xhigh' при high; dict/bool/empty/недопустимые элементы в целевых тестах | denied; строгие списки в routing_policy.py:90–95, 165–172, 186–189, 208–216 закрывают исходный обход |
| F2 | send_effort=false, explicit low, grant [low]; затем запрос без effort и grant [null] | Первый denied и body без reasoning_effort; второй executable. Проверка фактического режима до gate — delegate.py:101–125; observe разделяет transmitted и requested — routing_policy.py:241–254 |
| F3 | Runtime manual/denied delegate и decide; preflight тех же входов | Runtime: один redacted receipt, одна строка, null transmitted/observed; preflight не пишет state. Новая ветка exit3-after-exec остаётся R1 |

Целевой запуск: **44 passed, 4.38 s**:

```sh
<venv-python> -m pytest -q <exact-tree>/tests/test_routing_review_regressions.py <exact-tree>/tests/test_codex_subscription.py
```

Дополнительно независимый прямой F1/F2 fixture на копии b8b216c дал:

```json
{"F1_model_scalar":"denied","F1_effort_scalar":"denied","F2_explicit_low_omitted":"denied","F2_body_has_effort":false,"F2_explicitly_allowed_default":"executable"}
```

Четыре дополнительные независимые fixtures ниже: **4 passed, 2.55 s** (три воспроизводят текущие дефекты, четвёртая подтверждает защиту). Полный suite 305/1 — отчёт исполнителя, здесь он не повторялся и не выдаётся за независимый результат. Установок, login, live/paid inference и доступа к настоящим credentials не было; внешние запросы — только публичная документация.

## Изоляция, secrets, TOCTOU и область полезности

- Fake subprocess действительно получает минимальный env, temporary HOME/cwd вне репозитория и заданный CODEX_HOME. Sentinel API keys, endpoint/proxy, NODE_OPTIONS и ORCA tokens не наследуются. Проверены отдельный file auth, private permissions и fingerprint/version; plaintext auth status при отказе не попадает в файлы проекта. Это сильнее наличия executable в PATH.
- Auth, model/provider, read-only, shell и multi-agent flags явно передаются. Успешный fake без model metadata оставляет observed=null; явное несовпадение отправляет ответ в quarantine. Это доказательство формирования argv и обработки fake events, не доказательство соблюдения flags каждой версией Codex.
- Дополнительный fixture создаёт config.toml во время login status: повторная проверка профиля останавливает до exec, журнал not_started корректен. Это закрывает конкретное окно между probe и recheck.
- **Остаточный TOCTOU:** финальные hash/stat/check выполняются перед запуском по pathname, без immutable snapshot/lease. Другой процесс того же пользователя может заменить binary/profile/system config после последней проверки; auth account identity также не привязана к fingerprint профиля. Не воспроизводился реальный обход forced ChatGPT auth, поэтому не объявляю его установленной атакой. Для управляемого dedicated profile следует явно запретить параллельное изменение/обновление на время run и подтвердить, что выбранный path — реальный проверенный executable, а не изменяемая цепочка launcher/dependency. Защита от hostile same-user процесса не доказана.
- Entitlement/overage и отсутствие managed/cloud config — **заявление оператора**, не локальное измерение. Receipt `included_quota_operator_confirmed` и документация это признают. Для included subscription отдельный API monetary hardcap не нужен; проверка реального подписочного account/overage и соответствия CLI contract остаётся открытым gate, не автоматически пройденным boolean.
- Quota/error/timeout не создают новый subprocess, не переключают backend/account/API. Нулевая дополнительная стоимость и отсутствие повторной генерации внутри CLI из этих fixtures не следуют (R3). Timeout может оставить неопределённый серверный исход; повторять автоматически нельзя.
- Размер контекста учитывает prompt/hints и резервы консервативной оценкой, без обрезания. Output reserve для CLI не является принудительным лимитом вывода; это documented estimation, а не token meter. Полезный размер реального вызова и собственные системные инструкции CLI требуют проверки версии.
- После устранения R1/R2 и честного закрытия/ограничения R3 адаптер пригоден для ограниченного Codex subscription rollout. Claude subscription и ZCode/DeepSeek/Kimi/Qwen/Gemini/Antigravity/Cursor adapters остаются manual: это безопасное и прозрачно описанное сужение, но **не завершение исходного запроса переносимости**. Особо Claude остаётся незакрытой ближайшей реализацией; отсутствие live-проверки всех hosts нельзя заменить числом тестов.
- Native/Orca транспорты не считаются автоматически покрытыми этим Python gate. Нового scheduler/памяти/ledger нет; данные продуктовых кейсов не изменены. Мою методику здесь независимо не переоценивал.

## Воспроизводимые независимые fixtures

Извлечь commit в временную лабораторию (не текущий checkout):

```sh
git archive b8b216ccaa122daf4067d48a5d26b5bcd3bb1534 tools knowledge tests schemas | tar -x -C <exact-tree>
```

В `<exact-tree>/tests/test_independent_review.py` поместить код ниже и запустить существующим venv-python `-m pytest -q -s <exact-tree>/tests/test_independent_review.py`. Он использует уже проверенный fixture subscription из данного commit; fake executable не обращается к сети/аккаунту. `replace_script` обновляет hash фиктивного бинарника, чтобы тестировал обработку исхода, а не заведомый fingerprint mismatch.

```python
import hashlib
import json
import pytest
import delegate as D
from test_codex_subscription import subscription


def replace_script(cfg, binary, old, new):
    binary.write_text(binary.read_text().replace(old, new))
    cfg['execution_policy']['codex_subscription']['binary_sha256'] = hashlib.sha256(binary.read_bytes()).hexdigest()


def receipt(project):
    return json.loads(next((project / 'state/delegated').glob('*.receipt.json')).read_text())


def test_repro_executed_exit3_erases_transmitted(subscription):
    cfg, args, calls, binary, project = subscription
    replace_script(cfg, binary, "print(json.dumps({'type': 'turn.completed', 'usage': {'input_tokens': 10, 'output_tokens': 5}}))", "sys.exit(3)")
    assert D.main(args) == 3
    assert sum('exec' in json.loads(line)['args'] for line in calls.read_text().splitlines()) == 1
    r = receipt(project)
    assert r['result'] == 'not_started' and r['transmitted']['model'] is None
    assert '0 inference calls' in (project / 'state/run_log.md').read_text()
    print('REPRO: exec launched once, receipt not_started, log 0 inference calls')


def test_repro_empty_answer_receipt_says_success(subscription):
    cfg, args, calls, binary, project = subscription
    replace_script(cfg, binary, "write_text('subscription answer')", "write_text('')")
    assert D.main(args) == 1
    r = receipt(project)
    assert r['result'] == 'response_received' and r['exit_code'] == 0
    assert not list((project / 'state/delegated').glob('*.md'))
    print('REPRO: empty answer returns 1 but receipt exit_code=0 response_received')


def test_repro_valid_json_array_crashes_without_journal(subscription):
    cfg, args, calls, binary, project = subscription
    replace_script(cfg, binary, "print(json.dumps({'type': 'turn.completed', 'usage': {'input_tokens': 10, 'output_tokens': 5}}))", "print('[]')")
    with pytest.raises(AttributeError):
        D.main(args)
    assert (project / 'state/run_log.md').read_text() == ''
    assert receipt(project)['result'] == 'response_received'
    print('REPRO: valid non-object JSON causes AttributeError after success receipt and before log')


def test_recheck_stops_config_change_during_probe(subscription):
    cfg, args, calls, binary, project = subscription
    replace_script(cfg, binary, "print('Logged in using ChatGPT')", "Path(os.environ['CODEX_HOME'], 'config.toml').write_text('bad=1'); print('Logged in using ChatGPT')")
    assert D.main(args) == 3
    assert sum('exec' in json.loads(line)['args'] for line in calls.read_text().splitlines()) == 0
    assert receipt(project)['result'] == 'not_started'
```

Фактическая лаборатория этого review: `/var/folders/ss/4g0fyyvx3hgczmghb26lyfhh0000gn/T/strategist-review-b8-im3vx6ho`; venv-python — существующий `.venv/bin/python` рабочей области adaptive-routing-workflow. В репозитории записан только этот отчёт; временная лаборатория содержит копию exact commit и fixtures. Номера строк findings относятся к b8b216c, не upstream и не worktree.

## Следующий шаг

Вернуть R1/R2 исходному исполнителю с fixtures; R3 закрыть конкретным version contract или честно согласованным ограничением. После правок повторить только эти сценарии и затронутые gates, затем интегратор проверяет общий подписочный текстовый сценарий и явные ограничения rollout. До этого считать core готовым к полной приёмке нельзя.

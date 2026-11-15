# Независимое review OpenCode free-only 390ff20

Дата: 2026-11-15. **Вердикт: changes requested — два P2 в согласованности планирования и schema gate.** В проверенных путях не найдено доказанного перехода на платный account/API. Реальный HTTP 429 — незакрытый live gate, не дефект реализации и не основание обходить квоту.

Exact target `390ff2011b68ec7539948194b008b3a3f6f59589`, baseline `d9efed8be05a68a0dbcefe907b5fa8d2f03ee853`. Код читался git show/archive этого commit, не из текущего checkout. В продукт не вносились изменения. Проверка ограничена offline/fake CLI/transport; реальный OpenCode, login, ключи, аккаунты и inference не запускались.

## F1 — P2: decide preflight не использует новый OpenCode selector

**Место:** `tools/decide.py:409–425` в target (существующий caller), новая ветка `tools/delegate.py:373–387`, `tools/opencode_free.py:146–163`.

У delegate для OpenCode специальный выбор кандидата и default effort=None; у decide preflight эмуляции остаются L.model_for + generic tier effort. После обновления snapshot первая рекомендованная модель может стать платной/исчезнуть, а вторая остаться free. Delegate выберет вторую, но decide preflight откажет по первой. Аналогично generic effort может дать ложный отказ для модели без такого variant, хотя actual delegate его по умолчанию не передаёт.

**Воспроизведено:** fixture T1 `[opencode/flash-free, opencode/big-pickle]`; family T1 рекомендует flash-free; snapshot обновлён — input price flash-free=1, big-pickle=0. `decide --ask ... --preflight`: denied, requested.model=flash-free. `delegate --task classify --host opencode --preflight`: executable, requested.model=big-pickle. Оба — только план, без процессов/сети. Реальная emulation вызывает D.main, поэтому не исполняет план, который показал preflight. С точки зрения оператора правильный отказ платной модели скрывает доступный бесплатный маршрут.

**Минимальная правка:** один общий offline route-selection контракт для delegate и decide (task=classify, tier, explicit/default effort, snapshot/candidates), без нового runtime. Приёмка: оба entrypoint дают одинаковые selected model/effort/status на том же каталоге; paid/removed первый кандидат пропускается до запуска, unknown ID не даёт fallback вне списка. Добавить fixture смены цены и модель без reasoning variants.

## F2 — P2: authorize снова проверяет scalar candidate list как подстроку

**Место:** `tools/opencode_free.py:166–172`; отличие от строгой проверки `151–155`. `call:200–203` повторно использует policy gate, но не select.

При YAML `candidates: {T1: opencode/big-pickle-paid}` запрос `opencode/big-pickle` удовлетворяет Python `in` на строке. P.plan возвращает executable, хотя точного элемента allowlist нет. Select тот же malformed policy отвергает; основная D.main поэтому защищена. Однако прямой adapter call после P.plan действительно доходит до fake Popen: проверка перед transport уже не является самостоятельной строгой границей.

**Воспроизведено:** два независимых fixtures ниже — executable от policy и одна fake генерация через O.call после version/auth/single-model probes. Модель при этом по snapshot бесплатна; **это не доказательство платного списания или обхода exact-zero**, а нарушение точного scope allowlist и расхождение публичных внутренних контрактов.

**Минимальная правка:** общая строгая валидация непустого list[str] и точных элементов на authorize/select; dict/bool/scalar/empty должны давать denied до любого probe/transport. Приёмка — эти формы и подстрока против корректного списка; нельзя чинить неявным превращением строк в список.

## Смена бесплатных моделей: что реально поддержано

Цены/наличие **существующих кандидатов** перепроверяются по snapshot на каждой задаче. Порядок кандидатов остаётся статичным в routing.yaml (T1/T2/T3), O.select не перебирает новые модели каталога. `refresh` обновляет provider snapshot, но не candidates и не family pins.

**Воспроизведено:** свежий корректный snapshot содержит единственный exact-zero `opencode/new-free`, старые модели удалены; O.select выдаёт No confirmed free candidate. После явного изменения T1 candidates на `[opencode/new-free]` выбор проходит. Значит, это актуализация тарифов/доступности фиксированного allowlist, **не динамический полный выбор бесплатного каталога**.

Документ честно называет порядок начальной операционной политикой и не обещает рейтинг качества, поэтому статический список сам по себе не является платёжным дефектом. Но предупреждение пользователя о меняющихся free-моделях пока закрыто лишь частично. Для полезного supported rollout нужен явный путь обслуживания: refresh → список добавленных/удалённых/изменивших цену supported exact-zero ID → обновление локальных candidates по задаче/уровню → preflight. Как минимум описать этот обязательный шаг и не называть refresh автоматическим обновлением selection; лучше вывести такой diff из refresh без inference. Не требуется автоматически разрешать все незнакомые модели либо придумывать их качество/уровень.

В текущем наборе задачи одного уровня могут использовать одну модель; это детерминированный task-tier policy, не адаптация к содержанию и не сравнение качества. Изменившаяся цена первого кандидата приводит к выбору следующего до генерации; runtime 429 не запускает перебор следующих моделей. Эта граница разумна для квот и не должна быть снята ради smoke.

## Дополнительная robustness-находка: неполный второй шаг NDJSON

`tools/delegate.py:77–98` требует наличие хотя бы одного step_finish, но не проверяет завершение последнего step_start. Synthetic последовательность start → text → finish(stop,cost0) → start → text(partial) принимается valid; текст объединяется целиком. Fixture ниже это воспроизводит. В v2.2.5 реальные step events имеют messageID, а CLI делает reconcile и обычно сообщает неуспех; см. [tagged noninteractive.ts](https://github.com/anomalyco/opencode/blob/v2.2.5/packages/cli/src/run/noninteractive.ts). Реальная эмиссия такого оборванного потока с exit0 здесь **не доказана**, поэтому не выдаю находку за live-инцидент/платёжный bypass и не ставлю её на один уровень с F1/F2. Для заявленного строгого partial-result gate стоит учитывать завершение последнего шага по messageID либо сузить обещание документации до проверенного одноступенчатого потока.

## Каталог, цены и changed-pricing edge

- Точный endpoint/package/chat protocol, input/output цены, cache read/write и TTL, условные/context overrides проверяются. Multipliers, experimental modes, иные cost dimensions и неизвестные price-поля отвергаются. Boolean не принимается за ноль. Это консервативное подмножество [schema v2.2.5](https://github.com/anomalyco/opencode/blob/v2.2.5/packages/core/src/models-dev.ts), а не suffix-free эвристика.
- Проверяются source/schema/provider ID/digest, timestamp с timezone, future/stale, fingerprint executable и VERSION; повтор перед генерацией замечает смену snapshot. TTL 24 часа не удостоверяет текущую цену у сервера. Digest — целостность локальной копии, не подпись поставщика. Об этом нельзя говорить как о live price verification.
- Между refresh и запуском formerly-free ID может стать платным. Клиент увидит старую нулевую цену и попробует signed-out маршрут. Это ограничение доступности, а не доказанный путь списания: fresh HOME/XDG не содержит account credential, provider использует public access, server paid route требует ключ. При ошибке маршрут останавливается. Не нужен гипотетический бесконечный денежный hardcap для этого signed-out пути.
- [Tagged provider](https://github.com/anomalyco/opencode/blob/v2.2.5/packages/core/src/plugin/provider/opencode.ts) при отсутствии key/connection задаёт public credential и отключает модели с ненулевым input cost. Локальный exact-zero gate строже: проверяет и output/cache/overrides. [Официальный inference contract](https://opencode.ai/docs/console/api/inference/) различает paid auth и free chat внутри OpenCode. Это первичные основания, не замер тарифа пользовательского аккаунта.

## Изоляция, helper/default fallback и receipts

- Изолированный env строится с нуля, включая PATH, HOME/XDG/TMP; пользовательские API/auth/proxy/Node/Orca/service переменные не наследуются. Permissions deny-all, autoupdate=false, project/.claude config отключены; известные system/MDM пути блокируют маршрут. Проверено по коду и fake spies, первичный [server-process v2.2.5](https://github.com/anomalyco/opencode/blob/v2.2.5/packages/cli/src/server-process.ts) подтверждает env flags, а [config discovery](https://github.com/anomalyco/opencode/blob/v2.2.5/packages/core/src/config/discovery.ts) — system/MDM источники.
- В отдельный local catalog передаётся один прошедший gate ID; `models` должен вернуть ровно его. В upstream валидный локальный файл имеет приоритет, fetch отключён. Если файл нечитаем, upstream может взять bundled snapshot; проверка single-model list — важная защита перед генерацией. Отсутствие модели не заменяется другим ID wrapper. Платный helper/default не доступен в проверенной одноэлементной catalog boundary; число внутренних free helper calls/retries не измерено и честно unknown.
- Auth probe — публичный CLI `auth list` с пустыми временными данными, ожидает []; private API key не требуется, реальные secrets не извлекаются. Private standalone server использует случайный локальный lease credential, а не auth пользователя; [tagged standalone](https://github.com/anomalyco/opencode/blob/v2.2.5/packages/cli/src/services/standalone.ts) связывает срок жизни с EOF. Это нормальный поддержанный CLI-путь, не имитация gateway.
- Quota/timeout/partial/malformed response не вызывают wrapper retry/account/API fallback. На timeout wrapper убивает свой процесс; upstream lease прекращается при закрытии pipe. Реальное прекращение всех удалённых вычислений не измеряется. Snapshot, binary и temp directory всё ещё не являются OS sandbox против вредоносного same-user процесса; такой threat model здесь не требую.
- NDJSON не даёт надёжного model ID: observed остаётся null, integrity unconfirmed; requested/transmitted не выдаются за фактически обслужившую модель. Receipt сохраняет snapshot provenance и outcome. Live 429 подтверждает лишь переданные ID/отказ по отчёту исполнителя. Успешная генерация двух разных моделей остаётся открытой приёмкой; новые live attempts данным review не разрешены.

## Исполненные проверки

Лаборатория exact commit: `/var/folders/ss/4g0fyyvx3hgczmghb26lyfhh0000gn/T/review-opencode-390-0rj_0gpg`. Использован существующий `.venv/bin/python` рабочего каталога; ничего не устанавливалось.

```sh
git archive 390ff2011b68ec7539948194b008b3a3f6f59589 tools knowledge tests schemas docs/OPENCODE_FREE.md | tar -x -C <exact-tree>
<venv-python> -m pytest -q <exact-tree>/tests/test_opencode_free.py
# 37 passed, 0.18 s
<venv-python> -m pytest -q <exact-tree>/tests/test_delegate.py <exact-tree>/tests/test_routing_policy.py <exact-tree>/tests/test_routing.py
# 59 passed, 1.13 s
<venv-python> -m pytest -q -s <exact-tree>/tests/test_review_390.py
# Первые 4 независимых fixtures: 4 passed, 0.13 s
<venv-python> -m pytest -q -s <exact-tree>/tests/test_review_390.py -k scalar_allowlist_reaches_adapter
# Добавленная пятая fixture: 1 passed, 4 deselected, 0.08 s
```

96 существующих целевых тестов и 5 независимых fixtures; последние подтверждают текущие дефекты/ограничения, а не желаемую приёмку. Полный 379/1 — отчёт исполнителя, здесь не повторялся. `git diff d9efed8..390ff20 --check` прошёл. Внешние запросы этого review — только публичная документация через Context7/web; v2.2.5 проверен отдельно от dev-ответов Context7. Продуктовые проекты и auth state не менялись.

## Независимые fixtures

Поместить следующий код в `<exact-tree>/tests/test_review_390.py`. Fixtures configured/model/fake_probe импортируются из exact target; subprocess run/Popen подменены, fake binary никогда реально не выполняется.

```python
import copy
import json
import pytest
import delegate as D
import opencode_free as O
from test_opencode_free import configured, plan, model, fake_probe


def test_scalar_candidate_plan_authorizes_substring(configured):
    cfg, _, _ = configured
    cfg['execution_policy']['opencode_free']['candidates']['T1'] = 'opencode/big-pickle-paid'
    assert plan(cfg)['status'] == 'executable'
    with pytest.raises(ValueError):
        O.select(cfg, 'classify', 'T1', 'opencode/big-pickle')
    print('REPRO policy allows substring but select rejects malformed allowlist')


def test_new_free_model_not_selected_after_refresh(configured):
    cfg, path, data = configured
    data['provider']['models'] = {'new-free': model('new-free')}
    data['sha256'] = O.digest(data['provider'])
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match='No confirmed free'):
        O.select(cfg, 'classify', 'T1')
    cfg['execution_policy']['opencode_free']['candidates']['T1'] = ['opencode/new-free']
    assert O.select(cfg,'classify','T1') == 'opencode/new-free'
    print('LIMITATION refresh alone does not update candidates; explicit policy update works')


def test_partial_second_step_is_accepted():
    events = [
      {'type':'step_start','part':{}},
      {'type':'text','part':{'text':'first'}},
      {'type':'step_finish','part':{'reason':'stop','cost':0,'tokens':{'input':1,'output':1}}},
      {'type':'step_start','part':{}},
      {'type':'text','part':{'text':'partial second'}}]
    parsed = D.parse_result('opencode','\n'.join(map(json.dumps,events)))
    assert parsed['valid'] and parsed['text'] == 'firstpartial second'
    print('REPRO incomplete second step accepted after earlier finish')



def test_decide_preflight_disagrees_with_delegate_selection(configured, monkeypatch, tmp_path, capsys):
    import decide as DC
    import ledger as L
    cfg, path, data = configured
    cfg.update(delegate_backends=['opencode'], families={'opencode-free': {'T1': 'opencode/flash-free'}}, tiers={'T1': {'effort': ['low']}})
    data['provider']['models']['flash-free']['cost']['text']['input']['price'] = 1
    data['sha256'] = O.digest(data['provider'])
    path.write_text(json.dumps(data))
    monkeypatch.setattr(L,'load_routing',lambda:cfg)
    project=tmp_path/'p';(project/'state').mkdir(parents=True)
    (project/'state/run_log.md').write_text('')
    prompt=tmp_path/'prompt.md';prompt.write_text('fixture')
    assert DC.main(['--project',str(project),'--ask','Ready?','--state','fixture','--preflight'])==0
    decision=json.loads(capsys.readouterr().out)['route']
    assert D.main(['--project',str(project),'--task','classify','--prompt-file',str(prompt),'--host','opencode','--preflight'])==0
    delegate=json.loads(capsys.readouterr().out)
    assert decision['status']=='denied' and decision['requested']['model']=='opencode/flash-free'
    assert delegate['status']=='executable' and delegate['requested']['model']=='opencode/big-pickle'
    print('REPRO decide preflight rejects old paid ID, delegate chooses eligible free fallback')



def test_scalar_allowlist_reaches_adapter(configured, monkeypatch):
    import subprocess
    cfg, _, _ = configured
    cfg['execution_policy']['opencode_free']['candidates']['T1'] = 'opencode/big-pickle-paid'
    fake_probe(monkeypatch)
    launches=[]
    class Process:
        returncode=0
        def __init__(self, command, **kwargs): launches.append(command)
        def communicate(self, timeout=None): return ('', '')
    monkeypatch.setattr(subprocess,'Popen',Process)
    receipt=plan(cfg)
    assert O.call(cfg,'opencode/big-pickle','fixture',None,1,receipt)[0]==0
    assert len(launches)==1 and receipt['started']
    print('REPRO malformed tier list reaches adapter after policy gate; main selector separately blocks')
```

## Приёмка следующей правки

Вернуть F1/F2 владельцу: общий selector для preflight/execute и строгая схема allowlist на transport gate. Уточнить процедуру появления новых free ID в docs/refresh output; не представлять фиксированные candidates как полный динамический каталог. После точечных offline регрессий можно принять ограниченный signed-out rollout, сохранив live 429 gate и не выполняя новые запросы до отдельного разрешения.

# Review OpenCode free-only 52f1e6f

Дата: 2026-11-15. **Вердикт: approve scoped offline rollout, с неблокирующим P3 ниже.** F1/F2 предыдущего review и partial-message finding закрыты. Metadata sync закрывает практический сценарий смены бесплатных моделей. Это не приёмка успешной live-генерации: прежний HTTP 429 gate остаётся открытым.

Exact commit: `52f1e6f2b2b8c53ec017da0cd50d6e812e192137`, parent `390ff2011b68ec7539948194b008b3a3f6f59589`, общий baseline `d9efed8be05a68a0dbcefe907b5fa8d2f03ee853`. Проверен git show и отдельный git archive, не mutable checkout. Продукт, ledger и аккаунты не изменялись. Все subprocess в дополнительных fixtures подменены; сеть, реальный OpenCode и inference не запускались.

## Закрытие прежних замечаний

- **F1:** `tools/delegate.py:336–358`, `tools/decide.py:413–424` — общий `select_route`. Повторены changed-price/removed/no-variants/all-paid сценарии через оба preflight entrypoint. Requested model/effort и status совпадают; первый платный/удалённый кандидат пропускается до исполнения, effort по умолчанию null. Нет подмены requested на observed.
- **F2:** `tools/opencode_free.py:149–157,182,212–216` — общий strict list[str] gate используется selection, authorize и повторным P.plan в call. Scalar `opencode/big-pickle-paid` больше не разрешает `opencode/big-pickle` по подстроке. Dict/bool/empty/некорректные элементы и чужой ID отклоняются до probes; subprocess spies это проверяют.
- **Partial NDJSON:** `tools/delegate.py:78–93` — все увиденные assistant messageID требуют finish. Первый завершённый m1 + незавершённый m2 отклоняется; m2 с finish проходит; reconcile text после finish того же messageID допустим. Это согласовано с ранее проверенным tagged [noninteractive.ts v2.2.5](https://github.com/anomalyco/opencode/blob/v2.2.5/packages/cli/src/run/noninteractive.ts), без выдуманного final-stream event. На этой итерации upstream-контракт не менялся и live-поток не проверялся.

## Новый refresh / sync

`tools/opencode_free.py:265–350,353–415` проверен отдельными end-to-end fake metadata fixtures, а не только вызовом candidate_patch.

1. Каталог со всеми исчезнувшими прежними моделями и единственной новой `opencode/new-unknown`: `main(refresh --write-candidates ...)` возвращает 0, diff показывает added/removed, quality=`not_evaluated`; selection T1 и T2 выбирает новый ID, policy executable. Unknown paid ID остаётся denied.
2. YAML после sync сохраняет произвольный nested setting, весь Claude profile, custom field OpenCode и existing enabled=true. Отсутствующий профиль по существующему тесту создаётся disabled. Сохраняются значения, не комментарии/форматирование — docs это явно оговаривают. Нового runtime или автоматического включения transport нет.
3. Новый ID с cache TTL price=1 либо tools.supported=false исключается из всех уровней; без reasoning остаётся только T1. Known specialized/image/protocol exclusions и deterministic order покрыты целевыми тестами: прежний допустимый порядок, затем новые ID лексикографически. Это metadata eligibility, не доказательство качества/универсальности неизвестной модели.
4. После sync snapshot старше 24h даёт denied. Exact-zero проверки всех тарифов/overrides/variants не ослаблены. TTL не является live гарантией цены; прежняя signed-out single-model boundary остаётся необходимой.
5. Symlink policy path, directory и отсутствующий parent дают штатный код 3; JSON snapshot остаётся корректным. Refresh snapshot и policy sync не являются общей транзакцией: при отказе записи policy snapshot уже может быть обновлён. Это безопасно для free-only gate (новая цена всё равно проверяется при запуске), но после ошибки оператору нужно исправить path и повторить sync.
6. Нулевой eligible набор сохраняет snapshot и отзывает candidates, а не оставляет устаревшее разрешение. Refresh — отдельный metadata GET, не обход 429; не добавлены inference retry, fallback на account/API либо перебор моделей после quota failure.

## P3, неблокирующий: malformed remote provider вызывает traceback

**Место:** `tools/opencode_free.py:367–368` и catch в `411–413`. После `json.loads(response.stdout)["opencode"]` код вызывает provider.get без проверки type. Для `{"opencode": null}`, `{"opencode": []}` и `{"opencode": "bad"}` O.main выбрасывает AttributeError вместо возврата 3.

**Воспроизведение:** `test_malformed_remote_provider_repro` ниже подменяет version и curl, Popen запрещён; все три формы воспроизвелись. Существующий snapshot побайтно сохранён. Это ошибка обработки внешней schema/диагностики; не разрешение платного запуска, не потеря snapshot и не причина блокировать ограниченный rollout. Минимальная правка: require(type(provider) is dict, ...) до .get и тест с ожидаемым code=3. Не следует просто ловить все исключения и скрывать программные ошибки. Утверждать «никаких shape crashes» пока нельзя.

## Проверки и границы

Архив: `/var/folders/ss/4g0fyyvx3hgczmghb26lyfhh0000gn/T/review52-8wq_q6dl` (ниже `$ARCHIVE` — сокращение для пути, не переменная продукта).

```sh
.venv/bin/python -m pytest -q $ARCHIVE/tests/test_opencode_free.py $ARCHIVE/tests/test_decide.py $ARCHIVE/tests/test_delegate.py
# 88 passed in 1.41s
.venv/bin/python -m pytest -q $ARCHIVE/tests/test_independent52.py
# 10 passed in 0.19s
git diff 390ff20 52f1e6f --check
# exit 0
```

10 дополнительных tests включают 3 **подтверждения дефекта** через raises(AttributeError), а не успешную приёмку malformed schema. Полный suite не повторялся; числа 168+75 исполнителя не выдаются за собственные прогоны. Receipt/outcome plumbing не менялся кроме общего selection и parser; целевой delegate набор прошёл. Проверка isolate env/catalog/auth опирается на неизменённый transport и существующие fake spies; это не новый live security measurement.

Оставшиеся gates: сохранить P3 как известную ошибку/вернуть владельцу для небольшой правки; реальный успешный ответ двух разрешённых task routes после отдельного разрешения и восстановления доступности free quota; model quality и observed served ID всё ещё не доказаны. Не требуется денежный hardcap для signed-out free, не разрешены login/topup/paid fallback или новые попытки ради обхода quota. Документ `docs/OPENCODE_FREE.md` честно отделяет offline-ready от live-ready и `quality: not_evaluated` от оценки модели.

## Дополнительные воспроизводимые fixtures

Поместить в архивный `tests/test_independent52.py`; они используют только target fixture configured/model/plan, реальные subprocess полностью заменены.

```python
import copy, json, subprocess, datetime as dt
from pathlib import Path
import pytest, yaml
import opencode_free as O
from test_opencode_free import configured, model, plan

def metadata(monkeypatch, provider):
    calls=[]
    def run(cmd, **kw):
        calls.append(cmd)
        assert "OPENCODE_API_KEY" not in kw["env"]
        if cmd[-1]=="--version":
            return subprocess.CompletedProcess(cmd,0,O.VERSION,"")
        assert cmd[0]=="/usr/bin/curl" and cmd[-1]==O.SOURCE
        return subprocess.CompletedProcess(cmd,0,json.dumps({"opencode":provider}),"")
    monkeypatch.setattr(subprocess,"run",run)
    monkeypatch.setattr(subprocess,"Popen",lambda *a,**k:pytest.fail("inference"))
    return calls

def test_full_sync_new_only(configured,monkeypatch,tmp_path,capsys):
    cfg,path,old=configured
    cfg["execution_policy"]["claude_subscription"]={"enabled":False,"arbitrary":[1,"x"]}
    cfg["other"]={"nested":{"keep":True}}
    cfg["execution_policy"]["opencode_free"]["custom"]="retained"
    local=tmp_path/"local.yaml"; local.write_text(yaml.safe_dump(cfg))
    provider=copy.deepcopy(old["provider"])
    provider["models"]={"new-unknown":model("new-unknown")}
    calls=metadata(monkeypatch,provider)
    assert O.main(["refresh","--binary",old["binary"],"--output",str(path),"--write-candidates",str(local)])==0
    report=json.loads(capsys.readouterr().out)
    synced=yaml.safe_load(local.read_text())
    assert synced["other"]==cfg["other"]
    assert synced["execution_policy"]["claude_subscription"]==cfg["execution_policy"]["claude_subscription"]
    assert synced["execution_policy"]["opencode_free"]["custom"]=="retained"
    assert synced["execution_policy"]["opencode_free"]["enabled"] is True
    assert report["diff"]["quality"]=="not_evaluated"
    assert len(calls)==2
    assert O.select(synced,"classify","T1")=="opencode/new-unknown"
    assert O.select(synced,"problem-framing","T2")=="opencode/new-unknown"
    assert plan(synced,model="opencode/new-unknown")["status"]=="executable"
    assert plan(synced,model="opencode/unknown-paid")["status"]=="denied"
    data=json.loads(path.read_text()); data["fetched_at"]=(dt.datetime.now(dt.timezone.utc)-dt.timedelta(days=2)).isoformat()
    path.write_text(json.dumps(data))
    assert plan(synced,model="opencode/new-unknown")["status"]=="denied"

@pytest.mark.parametrize("mutation",["paid","no_tools","no_reasoning"])
def test_sync_changed_metadata(configured,monkeypatch,tmp_path,mutation):
    cfg,path,old=configured
    provider=copy.deepcopy(old["provider"]); provider["models"]={"new":model("new")}
    item=provider["models"]["new"]
    if mutation=="paid": item["cost"]["text"]["input"]["cache"]["write"]["1h"]=1
    elif mutation=="no_tools": item["capabilities"]["tools"]["supported"]=False
    else: item["capabilities"]["reasoning"]["supported"]=False
    metadata(monkeypatch,provider)
    local=tmp_path/"local.yaml"; local.write_text(yaml.safe_dump(cfg))
    assert O.main(["refresh","--binary",old["binary"],"--output",str(path),"--write-candidates",str(local)])==0
    synced=yaml.safe_load(local.read_text()); choices=synced["execution_policy"]["opencode_free"]["candidates"]
    assert choices["T2"]==choices["T3"]==[]
    assert choices["T1"]==(["opencode/new"] if mutation=="no_reasoning" else [])

@pytest.mark.parametrize("bad",[[],None,"bad"])
def test_malformed_remote_provider_repro(configured,monkeypatch,bad):
    cfg,path,old=configured; before=path.read_bytes()
    metadata(monkeypatch,bad)
    with pytest.raises(AttributeError):
        O.main(["refresh","--binary",old["binary"],"--output",str(path)])
    assert path.read_bytes()==before

@pytest.mark.parametrize("kind",["symlink","directory","missing_parent"])
def test_write_path_refusal(configured,monkeypatch,tmp_path,kind):
    cfg,path,old=configured; metadata(monkeypatch,old["provider"])
    dest=tmp_path/"local"
    if kind=="symlink": dest.symlink_to(path)
    elif kind=="directory": dest.mkdir()
    else: dest=dest/"child.yaml"
    assert O.main(["refresh","--binary",old["binary"],"--output",str(path),"--write-candidates",str(dest)])==3
    assert json.loads(path.read_text())["provider"]==old["provider"]
```

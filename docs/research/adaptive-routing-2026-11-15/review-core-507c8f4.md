# Финальное независимое review core 507c8f4

Дата: 2026-11-15. Среда: Codex/Orca. **Вердикт: changes requested — один P2 в новой валидации decision-ответа.** R1/R2 делегирования закрыты; согласованное ограничение R3 теперь отражено честно. Нового блокера в проверенном Claude subscription path не найдено, live gates остаются открытыми.

Exact target: `507c8f4f465313f5e409e2faba976868f770278c` поверх `b8b216ccaa122daf4067d48a5d26b5bcd3bb1534`, общий baseline `afd90c2cd712a23eb56557c9add43fe9fadd3028`. Источники — git show/archive exact target, не mutable checkout. Мой commit методики `87edc862` повторно независимо не принимался. В продукт ничего не записывалось; новые fixtures исполнялись в временной копии exact tree.

## F4 — P2: valid_response падает на контейнерном type до receipt и журнала

**Место:** `tools/decide.py:249–254`, вызов `454–455`, запись receipt `458–465`, журнал ошибки `470–474` (номера строк 507c8f4).

Внешний JSON `{"answers":{"q":{"type":[]}}}` или `type:{}` — синтаксически корректный JSON, но невалидный ответ. Новая valid_response делает проверку членства `a.get("type") not in TYPES | {"refusal"}` до проверки типа этого значения. List/dict не hashable: возникает `TypeError: unhashable type: 'list'` / `'dict'`. main не преобразует исключение в invalid_result. После одного fake HTTP-вызова не остаётся ни receipt, ни строки run_log, хотя запрос уже передан transport.

**Воспроизведено через DC.main**, не только прямой вызов helper: корректный fixture policy для decision, настоящий call/JSON parsing, заменён только P.http_open на локальный response spy. Оба параметра дали ожидаемый TypeError; по одному transport-вызову, пустой run_log, отсутствие receipt. Реальная сеть и аккаунт не использовались.

**Минимальное исправление:** проверять `type(a.get("type")) is str` до membership и возвращать False для любого неверного типа; main должен сохранять `invalid_result`, task exit1, started/transmitted и одну строку журнала. Аналогичное membership есть в `from_labels:175` на пути emulation: проверить этот вариант при исправлении, без нового runtime или переделки схемы. Прямое воспроизведение ниже относится к HTTP decision; emulation-вариант отмечен по коду, не объявляется исполненным.

**Приёмка:** два malformed JSON ответа дают контролируемый invalid_result вместо исключения и одну receipt/log; корректный noul-ответ и refusal продолжают работать. Это узкое продолжение требования invalid-result receipts, а не требование запретить included subscription или вернуть все hosts в manual.

## R1/R2/R3: результат повторной проверки

| Предыдущее замечание | Проверка на 507c8f4 | Итог |
|---|---|---|
| R1: exec exit3 стирал запуск | Тот же fake Codex после exec завершился 3 | task exit1, process_exit_code=3, started=true, transmitted сохранён, result=failed; «0 inference calls» в журнале отсутствует |
| R2: пустой ответ | Файл ответа пуст, процесс exit0 | task exit1, process_exit_code=0, invalid_result; результат не публикуется |
| R2: JSON array | stdout `[]` | Контролируемый invalid_result, одна строка журнала, без AttributeError |
| Контрпример: config во время probe | Fake login-status создаёт config.toml | Остановка до exec, task exit3, not_started остаётся корректным |
| R3: wrapper vs internal retries | Проверены receipt и новые пояснения | max_attempts_scope=wrapper_invocations, internal_transport_retries=unknown_version_dependent, inference_calls=null после передачи; ограничение принято в задании и больше не считается блокером |

`started` теперь определён как попытка передачи transport; значение выставляется перед subprocess и не выдаётся за доказательство серверной генерации. Timeout сохраняет неизвестный process exit и inference count. Не требую запрета всех внутренних reconnect/retries и не добавляю денежный API hardcap для included quota.

## Claude: первичные контракты и локальное поведение

- Официальный [CLI reference](https://code.claude.com/docs/en/cli-reference.md) подтверждает restricted (с v2.1.248), settings/tools/MCP/print и JSON auth status; configDirectory требует v2.1.268, authMethod различает claude.ai, api_key, helper, oauth_token и third_party. `--restricted` загружает managed и явные --settings, поэтому проверка отсутствия managed источников существенна, а не заменяется этим флагом.
- [Authentication](https://code.claude.com/docs/en/authentication.md) описывает приоритет environment/profile credentials и forceLoginMethod. [Configuration directory](https://code.claude.com/docs/en/claude-directory.md) подтверждает перенос .claude.json под CLAUDE_CONFIG_DIR и приоритет managed policy над обычными settings. Это подтверждение контракта поставщика, не live-наблюдение конкретного binary пользователя.
- `tools/delegate.py:235–290` использует один явный profile, empty HOME/cwd, restricted, inline forceLoginMethod=claudeai и disableAllHooks, empty tools и MCP, disable-slash-commands. Auth probe проверяет exit0, claude.ai и resolved configDirectory, затем повторяет profile check. Alias claude/claude-code реально проверены fake subprocess.
- `tools/routing_policy.py:32–71` проверяет binary hash/version declaration, private owner directory, допустимые entries, file/keychain mode, отсутствие известных managed dirs; секреты из auth storage не читаются wrapper. Общий ~/.claude не принимается. File и keychain не объявляются взаимозаменяемыми: macOS keychain остаётся native CLI контрактом, реальный Keychain не проверялся.
- Sentinel API/OAuth/base URL/cloud/profile/ORCA env не наследуются; auth email sentinel не попал в проект. Дополнительный fake runtime с секретом в stdout/stderr и exit1 также не записал его в receipt/log/output. Ошибка не запускает другой provider/account/API. Отказ auth/config и изменение settings во время probe останавливаются до print-вызова.
- Дополнительный Claude timeout fixture: один print invocation, result=failed, process_exit_code=null, inference_calls=null, выходного артефакта нет. Предоставленные error/quota, exit3, empty/array, metadata mismatch fixtures проходят. Проверенные невалидные delegate-ответы не публикуются.
- Наблюдаемые model/effort не копируются из requested: нет metadata — null; явное несовпадение — quarantine. Полнота metadata конкретной версии и все её вспомогательные сетевые действия здесь не измерялись.

## Ограничения, не новые блокеры

Entitlement/отключённый extra usage/отсутствие MDM/cloud settings остаются подтверждениями оператора, не измеренным enforcement. Receipt `included_quota_operator_confirmed` и документация это не скрывают. Для native included пути это не основание навязать API monetary cap. Перед эксплуатацией конкретного профиля требуется проверка его версии, account и настроек; этот review не выполнял login, установку, обновление, real CLI или paid/live inference.

Организационный запрет менять dedicated profile/binary во время run документирован. Последняя проверка и запуск по pathname не являются immutable lease или защитой от hostile same-user изменения. Это признанный остаточный TOCTOU, а не новая доказанная атака. Fingerprint/version следует проверять для исполняемого пути и его зависимостей, особенно если выбран launcher. Fake fixtures проверяют argv/env/отказы, не реализацию чужого CLI.

R3 ограничен wrapper invocations; внутренние повторы и серверный исход timeout неизвестны. Дополнительного account/API fallback в проверенных ветках нет. Данные о тарифе/полной стоимости не следуют из счётчика процессов.

ZCode/DeepSeek/Kimi/Qwen/Gemini/Antigravity/Cursor остаются явно неполным manual rollout. Добавленные Codex/Claude paths закрывают полезный узкий сценарий подписочного текстового делегата, но не всю переносимость. Native/Orca транспорты не получают разрешение от receipt другого транспорта.

Нового CLI-расхождения с методикой 87edc862 не обнаружено: --host/--preflight (--dry-run), JSON status, manual/denied и task exit-коды сохраняются. process_exit_code уточняет внутреннюю диагностику и не изменяет значение task exit3. Нового scheduler, memory storage или дублирующего ledger нет.

## Что исполнено

Exact tree: `/var/folders/ss/4g0fyyvx3hgczmghb26lyfhh0000gn/T/strategist-review-507-cx_4yef1`; interpreter — существующий `.venv/bin/python` worktree adaptive-routing-workflow.

```sh
git archive 507c8f4f465313f5e409e2faba976868f770278c tools knowledge tests schemas | tar -x -C <exact-tree>
<venv-python> -m pytest -q <exact-tree>/tests/test_execution_outcomes.py <exact-tree>/tests/test_claude_subscription.py <exact-tree>/tests/test_codex_subscription.py
# 46 passed, 15.33 s
<venv-python> -m pytest -q -s <exact-tree>/tests/test_independent_review_507.py
# 8 passed, 2.94 s: 4 прежних сценария с обновлённой ожидаемой приёмкой,
# 2 воспроизведения F4 и 2 независимых Claude timeout/secret сценария
```

Первый независимый прогон: 5 passed / 1 failed из-за моей механической замены ожидаемого кода 3 на 1 также в контрпримере отказа до запуска. Исправлена только временная fixture; правильное поведение продукта (exit3 до exec) сохранено. Финальный результат указан выше; ошибку harness не считаю дефектом продукта.

`git diff b8b216c 507c8f4 --check` прошёл. Полный suite 336/1 и прогон 29 — отчёты исполнителя, здесь не повторялись. Копии тестов/fixtures находятся только в лаборатории; единственная новая запись в repo — этот отчёт. Чтение публичных docs через Context7/web не запускало модельных провайдеров продукта.

## Воспроизводимая fixture F4

В `<exact-tree>/tests/test_review_decision_shape.py` поместить код ниже и выполнить существующим venv-python `-m pytest -q -s <exact-tree>/tests/test_review_decision_shape.py`. Эти assertions подтверждают текущий дефект, после исправления их нужно заменить на приёмку invalid_result/exit1/receipt/log.

```python
import json
import pytest

@pytest.mark.parametrize('bad_type', [[], {}])
def test_new_decision_validator_crashes_on_container_type(tmp_path, monkeypatch, bad_type):
    import decide as DC
    import ledger as L
    import routing_policy as P
    from test_routing_policy import api_policy
    project = tmp_path / 'p'
    (project / 'state').mkdir(parents=True)
    log = project / 'state/run_log.md'
    log.write_text('')
    provider = {'name': 'test', 'model': 'm', 'base_url': 'https://fixture.test', 'api_key_env': 'REVIEW_FAKE_KEY'}
    cfg = {'execution_policy': api_policy('decision','test','https://fixture.test/systemone','m'), 'decision': {'providers': [provider]}}
    monkeypatch.setattr(L, 'load_routing', lambda: cfg)
    monkeypatch.setenv('REVIEW_FAKE_KEY', 'fake-no-secret')
    body = {'answers': {'q': {'type': bad_type}}}
    class Response:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def read(self): return json.dumps(body).encode()
    calls = []
    def http_spy(*args):
        calls.append(1)
        return Response()
    monkeypatch.setattr(P, 'http_open', http_spy)
    with pytest.raises(TypeError, match='unhashable type'):
        DC.main(['--project', str(project), '--provider', 'test', '--ask', 'Ready?', '--state', 'fixture'])
    assert len(calls) == 1 and log.read_text() == ''
    assert not list((project / 'state').rglob('*.receipt.json'))
    print('NEW REPRO: decision type container -> TypeError, no receipt/log after transport')

```

Следующий шаг: вернуть F4 исходному исполнителю, повторить только malformed type и близкие валидные/refusal сценарии; затем принять узкий Codex/Claude rollout с перечисленными live gates. R1/R2 и согласованный R3 заново открывать не требуется.

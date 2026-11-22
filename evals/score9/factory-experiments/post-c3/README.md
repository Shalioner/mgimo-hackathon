# Эксперимент `post-c3`: пересмотр после утверждённого C3

Обычный чат и «Стратег» набрали по **12/12** в слепой оценке двух синтетических сценариев пересмотра после C3 на кейсе `family-connect`. [Результаты с ограничениями](RUN_RESULTS.md) · [Обезличенные ответы](BLINDED_RESPONSES.md) · [Карта доказательств](../../../../docs/JURY_EVIDENCE.md).

Пакет состоит из двух частей:
1. **Детерминированные CLI-контроли** ([reproduce_cli.py](reproduce_cli.py)) — воспроизводятся офлайн на любой машине из корня кандидата (`--candidate .`) без моделей и без записи в исходный кейс.
2. **Парные слепые сессии моделей** ([PROTOCOL.md](PROTOCOL.md), [RUN_RESULTS.md](RUN_RESULTS.md)) — уже проведены координатором; их результаты и ограничения зафиксированы в папке.

## Портируемое воспроизведение CLI-контролей

Из корня репозитория или распакованного архива кандидата (с подготовленной `.venv` на macOS/Linux/WSL):

```bash
.venv/bin/python evals/score9/factory-experiments/post-c3/reproduce_cli.py \
  --candidate . \
  --out /tmp/post-c3-cli-results.json
```

Опустите `--out`, чтобы вывести JSON в `stdout`. Не указывайте `--out evals/score9/factory-experiments/post-c3/cli_results.json`, если хотите сохранить архивный отчёт без изменений.

**Ожидаемый результат:** код выхода `0`, в JSON: `"all_pass": true`, `"c3_sha_matches_expected": true`, `"candidate_untouched": true` (все 4 контроля: `baseline`, `scenario_a_negative_control`, `scenario_b_package_wording` с новым SHA-256 `edaeb9fc6d3e2121f471f8479062742675d485fd3690ba97996a1004e123ec6d`, `scenario_b_ext_neutral` с неизменным SHA-256 `b1473fbf…853483`).

Без `--candidate` раннер берёт корень своей распаковки. Укажите этот флаг, чтобы проверить другую копию с тем же утверждённым пакетом. Раннер использует Python из `.venv` выбранной копии, а при её отсутствии использует запустивший интерпретатор. В этом интерпретаторе должны быть установлены зависимости из `requirements.txt`. На Windows путь к Python в `.venv` определяется автоматически. Сам переносимый запуск проверен на macOS.

## Проверенные входы и контрольные SHA-256

Перед воспроизведением можно сверить точные отпечатки входов и утверждённого состояния C3 (`shasum -a 256` / `sha256sum`):

| Артефакт / контрольный объект | SHA-256 | Назначение |
|---|---|---|
| Снимок утверждённого пакета C3 (`projects/family-connect`) | `b1473fbfb235891c6e552b00df8dd244fab8a06e2ccb1c32b72523f97e853483` | Хэш `c3_approval.py prepare` и запись в квитанции |
| Пятичастная стратегия (`projects/family-connect/outputs/00_brief_response.md`) | `ec145d11fddba4f13bc68edd64e837efc14b0e457549765a91ec7aff25aca96c` | Входит в контур C3 кандидата |
| Файл квитанции ([c3_approvals.json](../../../../projects/family-connect/state/c3_approvals.json)) | `aaf09a1bd70983fb59f3896d3c68fcd714759662b2054852da7771321e7875eb` | Фиксирует утверждённую версию C3 |
| Синтетический вход A ([A_synthetic_billing_telemetry.md](inputs/A_synthetic_billing_telemetry.md)) | `64455e432dd88e456c0396778b20ded2e5bc056c350008a7ce93a1057829e34a` | Подрыв `HYP-001`, `HYP-003` → повторное C2 + C3 |
| Синтетический вход B ([B_synthetic_wording_note.md](inputs/B_synthetic_wording_note.md)) | `74c5659ef2f1fec9d670d2d497f5debea2e5e42baad004e260fe9186bf2683f0` | Правка в [06_comms_strategy.md](../../../../projects/family-connect/outputs/06_comms_strategy.md) → повторное C3 без C2; внешняя правка в [10_market_comparison.md](../../../../projects/family-connect/outputs/10_market_comparison.md) → C3 неизменен |
| Единый промпт ([PROMPT_COMMON.md](PROMPT_COMMON.md)) | `038a4f7e4db7d0cee0202788b32cd07547f51194152131ec487f4b66b513e38a` | Дословно общий промпт обеих веток |
| Протокол и критерии ([PROTOCOL.md](PROTOCOL.md)) | `44a92f09a6c88c4d39516f905373cbe630180e4159becdc4843ef1a4c72c2839` | Ключ оценки, зафиксированный до запусков. Ссылка на распаковку сделана переносимой |
| CLI-раннер ([reproduce_cli.py](reproduce_cli.py)) | `17136df5b22f844b54ce02125b11d261b43319bf3628e2d9866d4e1d4585d810` | Автономная проверка 4 сценариев на временных копиях |

**Историческая привязка архивных отчётов:** базовый commit worktree до наложения правок кандидата — `e89f775`. Зафиксированные в папке [CLI_RESULTS.md](CLI_RESULTS.md) и [cli_results.json](cli_results.json) были сняты на более ранней замороженной распаковке кандидата (SHA-256 архива `45067b201632781870cc73f95e2810283b26102f1df3e0114ec80e88e7eac006`, 33 файла в `projects/family-connect`). В текущей поставке (`--candidate .`) контур C3 и файл квитанции побайтно те же (`b1473fbf…853483` и `aaf09a1b…875eb`), а число файлов в папке кейса составляет 34 в чистом архиве (35 в рабочем каталоге с файлом блокировки `.ledger-write.lock`), что не влияет на контур C3 и прохождение всех четырёх контролей.

## Состав папки

- [PROTOCOL.md](PROTOCOL.md) — дизайн слепых сессий, таблица разграничения трёх режимов согласования (повторное C2+C3 / повторное C3 без C2 / неизменный C3), заранее определённые критерии и критические ошибки.
- [PROMPT_COMMON.md](PROMPT_COMMON.md) — дословно единый промпт для обычного чата и «Стратега».
- [inputs/A_synthetic_billing_telemetry.md](inputs/A_synthetic_billing_telemetry.md) — синтетический источник **A**, подрывающий основания `DEC-001` (`ALT-003`: `HYP-001`, `HYP-003`, `EXP-002`, `review_triggers`).
- [inputs/B_synthetic_wording_note.md](inputs/B_synthetic_wording_note.md) — синтетическое уточнение **B**, меняющее только локальную формулировку в `outputs/06_comms_strategy.md` без смены выбора `ALT-003` (плюс граница с внешним файлом `outputs/10_market_comparison.md`).
- [reproduce_cli.py](reproduce_cli.py) — CLI-скрипт на изолированных временных копиях (без записи в кандидат и реальный кейс).
- [CLI_RESULTS.md](CLI_RESULTS.md) и [cli_results.json](cli_results.json) — исторические результаты прогона CLI-контролей (Baseline, A, B, B-ext).
- [RUN_RESULTS.md](RUN_RESULTS.md) и [BLINDED_RESPONSES.md](BLINDED_RESPONSES.md) — итоги и обезличенные тексты уже проведённых слепых сессий (6 ответов, паритет 12/12).

## Честные границы и статус

- **Что доказывает офлайн-CLI (`reproduce_cli.py`):** детерминированную реакцию `ledger.py`, `verify.py`, `consistency.py` и `c3_approval.py` на смену статусов гипотез/решения и на правку внутри vs вне пакета C3 без вызова моделей и без изменения исходного кейса. В сценарии A скрипт сам моделирует смену статусов `HYP-001`/`HYP-003` и `DEC-001` — качество рассуждения модели он не проверяет.
- **Что показывает слепое сравнение ([RUN_RESULTS.md](RUN_RESULTS.md)):** паритет 12/12 на двух синтетических сценариях одного учебного кейса в режиме чтения (без записи изменений в реестр). Это **не** доказывает статистического преимущества «Стратега», не замеряет скорость, стоимость или полный цикл сопровождения.
- **Ограничения воспроизводимости модельной части:** в [результатах](RUN_RESULTS.md) сохранены оценки, а в [обезличенных ответах](BLINDED_RESPONSES.md) — тексты для самостоятельной сверки. Во время сессий использовался общий рабочий каталог хоста с ограничением чтения через инструкции. Чтобы повторить модельные прогоны самостоятельно, скопируйте `projects/family-connect` во временную папку и подайте [PROMPT_COMMON.md](PROMPT_COMMON.md) вместе с входом A или B своей модели.
- **Неприкосновенность исходников:** ни при подготовке эксперимента, ни при запуске `reproduce_cli.py --candidate .` записи в `projects/family-connect` не выполняются.

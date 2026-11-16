# Нативная маршрутизация Codex, Orca и Claude Code

Обновление проверки 15.11.2026: официальное приложение Codex поддерживает hooks;
неподтверждённой остаётся наша сквозная интеграция, не сама возможность hooks.
Project hooks требуют доверия точному хэшу после изменений. Doctor различает
регистрацию и реальное исполнение: [ONBOARDING.md](ONBOARDING.md).

Среда разработки: Codex в Orca, 15.11.2026. Требование пользователя — автоматический
выбор и применение модели в разных хостах. Выбор только в Python CLI недостаточен.

## Что подключено

| Хост / вход | Автоматический выбор | Где применяется модель | Приёмка |
|---|---|---|---|
| Codex, обычная сессия проекта | `UserPromptSubmit` → настроенный классификатор | Hook даёт агенту маршрут для делегирования; `PreToolUse` реально переписывает model/effort нового native spawn | Схема и offline hook tests; выполнение агентом инструкции не гарантируется кодом |
| Orca с Codex в этом проекте | Те же нативные Codex hooks | Новый самостоятельный subagent; основная модель Orca chat этим hook не меняется | Проектная конфигурация установлена; следующий prompt/new session нужен для host adoption |
| Codex app-server через `codex_routed.py` | Каждый `turn/start` → настроенный классификатор | Реальная замена `model` и `effort`, включая `collaborationMode.settings` | Живой запрос завершился, выбранные параметры Luna/low переданы серверу |
| Claude Code, мод проекта | `turn.start` → настроенный классификатор один раз на ход | `turn.step` передаёт host-у новый model/effort | Native validator success, Node contract tests; native runtime test заблокирован sandbox |
| Claude general-purpose subagent | `agent.spawn` → настроенный классификатор | Модель при spawn; effort через проверенный agentId на step | Contract tests; initial engine ordering живьём не подтверждён, T4 spawn запрещён |

Основной чат Orca не подключён к middleware: в изученной версии 1.4.256 не найден
поддерживаемый пользовательский hook, позволяющий подменить его `turn/start`.
Нативный Codex TUI также не позволяет prompt-hook сменить свою основную модель.
Не подменяли системный бинарник/symlink и не патчили Orca.app. Не называть установленные
subagent hooks универсальным переключателем основной модели.

## Общая логика

`tools/host_routing.py` выбирает модель из существующего `knowledge/routing.yaml`:
Luna → Terra → Sol → Astra в Codex/Orca, Haiku → Sonnet → Opus → Fable в Claude.
Версии разрешаются существующим каталогом; нативный allowlist задаётся отдельно
в `automatic_routing.host_routing.models`. Новый неизвестный model ID не включается
молча: обновление allowlist — явная настройка.

Каждая новая задача начинает со своего уровня. Effort normal/deep соответствует
началу/концу разрешённой лестницы этого уровня. Повторные попытки и оценка качества
по-прежнему реализованы в `adaptive_execution.py`; native hook сам не объявляет
неудачу задачи и не создаёт цикл автоматических повторов. `max`/`ultra` не применяются.

Для нативного хоста gate «текст или инструменты» не ограничивает выбор модели:
инструменты/permissions остаются у хоста. Уровень и effort должны пройти прежний порог
0.75; исходные вероятности сохраняются в классификации. На первом live smoke T1=.948,
effort=.986, capability=.592: лишний capability gate остановил запрос до исполнения.
Исправление удаляет именно неприменимый gate, а не снижает confidence threshold.

`decide` выбирает, `authorize` повторно проверяет конфигурацию непосредственно перед
применением. T4 использует прежнее standing approval и общий атомарный счётчик.
Единица квоты: Codex turn/spawn attempt, Claude model step. Внутренние retries и
tool-loop запросы одного Codex turn отдельно не считаются — это не денежный лимит.
Claude T4 subagent блокируется до доказанной квоты первого запроса.

С 16.11.2026 native путь поддерживает явный каскад
**API → локальная decision-модель → подписочный T1/low**:
[конфигурация и результаты](CLASSIFIER_CASCADE.md). API требует отдельного opt-in,
точного grant и закрытого ключа. Native session сохраняет собственные permissions,
tools и credentials. Изолированные профили `delegate.py` этим не включаются;
для Codex есть отдельный text-only native transport с проверкой ChatGPT auth.
Неуверенный Codex middleware возвращает JSON-RPC ошибку до `turn/start`, без fallback.
Нативные prompt/Claude hooks сохраняют текущий маршрут с сообщением при отсутствии
уверенного выбора. Это видимый отказ автоматизации, не подтверждённое переключение.

## Установка и запуск

В этом checkout уже выполнено:

```bash
.venv/bin/python tools/install_host_routing.py --apply
```

Установщик добавляет `.codex/hooks.json` entries и локальный `.claude/settings.local.json`
с `CLAUDE_CODE_PLUGIN_DIRS`. Существующие hooks, permissions и inline plugins из
user/project/env сохраняются; прежние изменяемые файлы копируются в private
`~/.cache/strategist/host-routing-backups`. `CLAUDE.md`, глобальные config/auth и
другие приложения не меняются. Без `--apply` — проверка состояния.
После установки открой новую сессию Claude; Codex/Orca adoption проверяй на следующем
prompt, а не по одному наличию файлов.

Настройка в ignored `knowledge/routing.local.yaml`:

```yaml
automatic_routing:
  classifier: local-d1
  host_routing:
    enabled_hosts: [codex, orca, claude-code]
    timeout_seconds: 30
    models:
      codex: [gpt-6-luna, gpt-6.1-terra, gpt-6.1-sol, gpt-6-astra]
      orca: [gpt-6-luna, gpt-6.1-terra, gpt-6.1-sol, gpt-6-astra]
      claude-code: [haiku, sonnet, opus, fable]
```

Настоящий Codex app-server, JSONL stdin/stdout:

```bash
.venv/bin/python tools/codex_routed.py --binary /absolute/path/to/codex
```

Текстовый однократный запуск через тот же middleware:

```bash
.venv/bin/python tools/codex_routed.py --binary /absolute/path/to/codex --prompt-file request.txt
```

Этот smoke-вход создаёт ephemeral read-only thread и отклоняет интерактивные запросы
сервера. Полный app-server relay сохраняет исходные approvals/notifications. Credentials
не читает и не переносит. Он не является заменой интерактивного Codex TUI. Передавай
ему короткую подзадачу; изображения и текст >24000 байт сейчас дают явный отказ,
полные документы по-прежнему обрабатываются `document_flow.py`.

Повторный turn ID отклоняется без повторного списания T4. SIGTERM/timeout завершает
дочерний app-server, даже во время классификации. Отдельный supervisor для неперехватываемого
SIGKILL отсутствует. CLI stderr содержит маршрут без текста задачи; stdout — только протокол.

## GitHub-мод и первичные источники

Точное соответствие запросу пользователя: [Oqura-ai/optim-jev](https://github.com/Oqura-ai/optim-jev),
MIT, commit `ad9135aa78bcbf1c247ac15d0dac1a0cabd7686e`, 67 stars на дату исследования.
От него адаптировано разделение spawn/step и граница выбора модели. Его принудительное
отключение fork и `permissionDecision: allow` не переносились. Наш код написан отдельно;
новые зависимости, OAuth gateway и marketplace не устанавливались.

- [Claude mods reference](https://code.claude.com/docs/en/plugins/mods/reference)
  и [events](https://code.claude.com/docs/en/plugins/mods/events): актуальный Claude 2.1.311,
  function hooks действительно принимают model/effort. Проверено через Context7,
  опубликованные types и настоящий native validator.
- [Codex UserPromptSubmit schema](https://github.com/openai/codex/blob/main/codex-rs/hooks/schema/generated/user-prompt-submit.command.output.schema.json):
  model/effort в output отсутствуют.
- [Codex tool input rewrite](https://github.com/openai/codex/blob/main/codex-rs/core/src/tools/registry.rs)
  и [spawn args](https://github.com/openai/codex/blob/main/codex-rs/core/src/tools/handlers/multi_agents_v2/spawn.rs):
  `updatedInput` реально меняет аргументы до handler; full fork и фиксированные роли сохраняем.
- [Codex turn protocol](https://github.com/openai/codex/blob/main/codex-rs/app-server-protocol/src/protocol/v2/turn.rs):
  model/effort и precedence collaborationMode. Установлен Codex 0.169.0;
  CLI help и generated JSON schema сверены локально.

## Доказательства и открытые проверки

[Первый отказ](research/native-host-routing-live-2026-11-15.json) и
[исправленный live smoke](research/native-host-routing-fixed-live-2026-11-15.json):
реальный D1 → переданы `gpt-6-luna`, `low` → native app-server completed → ответ `17`.
`observed_model: null` означает, что независимый provider echo модели не получен;
зафиксированы переданные параметры и успешное исполнение, а не независимая аттестация backend.

Claude native `plugin validate --json` — success, ошибки 0, распознаны четыре hooks.
`claude plugin test` остановлен ОС до запуска тестов: `sandbox_apply: Operation not permitted`,
exit 71, 0 tests. Node contract harness проверяет мод, но не доказывает работу Claude engine.
Нужно закрыть этот runtime gate в разрешённой host-среде и проверить реальное поведение
первого дочернего шага. Нельзя объявлять универсальную production-готовность только по unit tests.

Итог: полный Python suite **705 passed, 1 skipped**, Node mod suite **12 passed**,
`git diff --check` без ошибок. Independent review замечания исправлены и воспроизведены
регрессиями; live Codex receipt приведён выше. Skip — прежняя необязательная openpyxl проверка.


### Уточнение fallback и opt-in после ревью

Codex hook определяет исходный T4 по текущей конфигурации семейства и каталогу,
включая модели с новым именем; шаблон Astra остаётся дополнительной защитой.
Неудачная классификация не разрешает исходный T4, ошибка чтения политики также
не разрешает непроверенный маршрут. Явно отключённый native routing возвращает
управление среде без смены модели или резервирования квоты, в том числе при
пустом/слишком длинном для классификатора задании. Это не разрешение оплаты или
обход собственных правил host. Общий malformed/oversized stdin hook остаётся
fail-closed: такой payload нельзя безопасно распознать.

## Явный native transport для документов

`tools/document_flow.py --host codex --native-codex-binary /absolute/path/to/codex`
использует текущую native учётную запись; без `--execute` выполняется только preflight.
Это отдельный явный режим, который не включает изолированный subscription profile.
Каждый источник последовательно получает T1-карточку с проверяемыми цитатами;
синтез получает только карточки, D1 — краткую сводку. Валидный, но неуверенный
D1-выбор проверяется одним T1/low вызовом со строгими цитатами. Отказы транспорта,
политики и квоты не запускают такую перепроверку. Quality gate и лестница effort
сохранены; T4 этот транспорт не запускает.

`tools/native_codex.py` создаёт временный read-only thread, отключает инструменты,
MCP, skills, hooks и plugins только для него, заново проверяет разрешение перед
turn/start и прекращает работу при запросе инструмента. SIGTERM в основном потоке
завершает группу app-server; SIGKILL перехватить нельзя. Настройки аккаунта не меняются.
Конфигурация опирается на официальный
[temporary_structured_request.rs](https://github.com/openai/codex/blob/main/codex-rs/tui/src/temporary_structured_request.rs).
Успех означает completed text turn с переданными model/effort; независимого provider echo нет.

Причина блокировки Claude уточнена в
[диагностике launcher](research/live-recheck-2026-11-15/claude-diagnosis.md):
plugin test удаляет sandbox-маркер из окружения дочернего процесса. Защита не ослаблялась;
нативная проверка Claude остаётся незакрытой.
# Оптимизация native-классификации — 2026-11-16

Native маршрутизатор по умолчанию задаёт D1 два вопроса (tier, effort): инструменты
сохраняются у host, третий capability-вопрос не влиял на принятие решения.
`automatic_routing.host_routing.compact_questions: false` возвращает три вопроса.
Обычный delegate сохраняет свою проверку capability; allowlist, fresh authorization,
порог уверенности и лимит T4 не отключены.

Codex UserPromptSubmit не обращается к D1 для отдельного «спасибо», «ок», «окей»,
«понятно» без материалов; точное совпадение, разрешены завершающие . ! ….
«Понятно?», «окей, составь план», «продолжай», «давай делай» и вложения не пропускаются.
Это no-op, а не наследование/разрешение предыдущего маршрута.

Открытые границы: prompt и последующий переформулированный spawn могут классифицироваться
дважды. Для пропуска второго решения нужен проверяемый receipt, связанный с задачей;
поле model само по себе не подтверждает явный выбор человека. App-server relay
классифицирует синхронно; полноценная отмена при замене запроса требует асинхронной
машины состояний и отдельной проверки протокола. Claude mod уже переиспользует
маршрут внутри turn, проверяя разрешения на каждом шаге.

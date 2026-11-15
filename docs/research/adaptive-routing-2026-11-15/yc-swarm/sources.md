# Основания для переносимости

Проверено 2026-11-15. Это исследование возможностей, а не отчёт об установленном и испытанном рое. Видео, локальные документы, текущая документация поставщиков и архитектурные предложения имеют разный статус доказательства.

## Первичный материал

**V1** — собственный файл пользователя `/Users/shalioner/Downloads/videoplayback (1).mp4`, SHA-256 `933ce0582534f53d5f06934d875b080925ec10bf09c39ff96f66c0b9abde9f40`, 1958,835374 с. Авторство и тезисы устанавливаются по нему; [транскрипт](transcript.md), [метод](method.md), [кадры](frames.md). Его пересказ экспериментов не заменяет проверку оригинальных исследований.

## Текущая документация сред

**D1 — Codex.** [Subagents](https://learn.chatgpt.com/docs/agent-configuration/subagents): отдельные агентные потоки, настройки модели и effort, сбор результатов; права наследуются от родителя. Текст роли с просьбой «только читать» сам по себе не создаёт отдельную песочницу. Страница получена через поиск и fetch OpenAI Docs. Локально `codex-cli 0.167.2`; `codex exec --help` подтверждает `--model`, `--json`, `--output-schema`, `--sandbox`, `--worktree`. Это чтение help, без модельного вызова и без создания worktree. Ограничения конкретного хоста и инструкции текущей сессии остаются обязательными.

**D2 — Claude Code.** [Custom subagents](https://code.claude.com/docs/en/sub-agents), [agent teams](https://code.claude.com/docs/en/agent-teams), [worktrees](https://code.claude.com/docs/en/worktrees): роль с настройками модели/инструментов, собственный контекст; teams дают иную координацию и более дорогие отдельные инстансы. Отдельное дерево Git — дополнительная изоляция записи. Получено через Context7 resolve → query по официальным docs и открытие страницы subagents. Живой локальный запуск **не подтверждён**: даже `claude --version` вернул `Claude blocked: the pinned VPS transport cannot be verified`. Обход блокировки не предпринимался. Предложение не зависит от установки или включения teams.

**D3 — ZCode.** [Subagents](https://zcode.z.ai/en/docs/subagents), [configuration](https://zcode.z.ai/en/docs/configuration): официально описаны встроенные general-purpose/Explore с отдельным контекстом, а также custom subagents **Beta** с выбором модели, effort и инструментов; пользовательские роли сохраняются в `~/.zcode/agents/`. Получено через Context7 resolve → query и открытие страницы subagents. Наличие описания не доказывает доступность в установленной версии/плане. Проверенного общего headless-контракта для локального ZCode из этого чтения не следует.

**D4 — Cursor.** [Subagents](https://cursor.com/docs/subagents), [Python SDK](https://cursor.com/docs/sdk/python): отдельный контекст, собственная модель, параллельные задачи, изолированные копии проекта. Получено через Context7 resolve → query и открытие страницы subagents. Новые docs богаче проектного описания picker/CLI; установленная версия и права аккаунта не проверялись. Не переносить Python SDK контракт MCP на TypeScript без сверки: полученные выдержки описывают разные ограничения.

## Модель — не среда выполнения

**D5 — GLM.** [GLM-5.3](https://docs.z.ai/guides/llm/glm-5.3), [thinking](https://docs.z.ai/guides/capabilities/thinking): для GLM-5.3/Flash описаны `low/high/max`, мышление всегда включено; правила API и Coding Plan различаются. Context7 resolve → query и открытие страницы модели. В локальном `knowledge/model_guides/glm.md` пока `effort_levels: [low, max]`; это расхождение требует отдельной актуализации и проверки, а не молчаливой подмены конфигурации в исследовании. Разрешение на `max` по правилам проекта всё равно нужно.

**D6 — DeepSeek.** [Thinking mode](https://api-docs.deepseek.com/guides/thinking_mode/): при запросах с `tools` требуется сохранять и возвращать `reasoning_content` предыдущих ходов; иначе возможен 400. Получено через Context7 resolve → query по официальным docs. Совместимый URL и поле `messages` не гарантируют корректность инструментального цикла.

**D7 — Kimi.** [Models overview](https://platform.kimi.ai/docs/api/models-overview), [reasoning effort](https://platform.kimi.ai/docs/guide/use-reasoning-effort.md): у K3 `low/high/max`, по умолчанию max; смена effort затрагивает prefix cache. Получено через Context7 resolve → query. Эти свойства конкретной серии нельзя переносить на все Kimi; наличие модели не означает включённый пользователю режим swarm или SDK.

**D8 — Qwen.** [Quickstart](https://qwen.readthedocs.io/en/latest/getting_started/quickstart.html): гибридный Qwen3, Instruct-2507 и Thinking-2507 имеют разные режимы; единое `enable_thinking=False` подходит не всем. Получено через Context7 resolve → query по официальным docs. Нужны точные ID, шаблон диалога и фактические возможности сервера; `/no_think` не является переносимым контрактом для всего семейства.

**D9 — ASR.** [faster-whisper v1.2.1 README](https://github.com/SYSTRAN/faster-whisper/blob/v1.2.1/README.md): локальная модель, CPU/int8, start/end сегментов; генератор надо дочитать до конца. Context7 resolve `faster-whisper` → query `/systran/faster-whisper/v1.2.1`. Параметры фактического запуска и ограничения — в [методе](method.md).

## Репозиторий на baseline afd90c2cd712a23eb56557c9add43fe9fadd3028

**R1** — [AGENTS.md](../../../../AGENTS.md), разделы 2–6: C1–C3, источники/типы утверждений, единственная человеческая фиксация решения, T0–T4, лестница эскалации, лимиты исследования. Это правила проекта, не экспериментальная оценка роя.

**R2** — [workflow](../../../../workflows/strategy.md), [.claude source-extractor](../../../../.claude/agents/source-extractor.md), [.claude claim-verifier](../../../../.claude/agents/claim-verifier.md): отделение извлечения от записи, затем независимая сверка короткого списка. Ревьюер не переписывает outputs.

**R3** — [routing.yaml](../../../../knowledge/routing.yaml), [routing.local.example.yaml](../../../../knowledge/routing.local.example.yaml), локальные `tools/delegate.py --help`, `tools/verify.py --help`, `tools/decide.py --help`: существующие маршруты и контракты. Исходники `tools/` не читались; корректность реализации сверх контрактов здесь не аудировалась.

**R4** — [измерения](../../../../docs/MEASUREMENTS.md), строки таблицы 4–8 и раздел «Что не измерено»: исторические измерения цены пересмотра, объёма контекста и накладных расходов; пользы всего мультиагентного процесса отдельно не доказывают. [WORK_LOG](../../../../docs/WORK_LOG.md), раздел 7: исторический isolated CLI эксперимент с входом 153098 → 25170 токенов. Значения не переносим как постоянную цену современных запусков.

**R5** — [E2E ZCode](../../../../docs/E2E_RUN_2_ZCODE.md): историческая версия 0.17.1, UI-прогон и одна модель на всех шагах; в том прогоне выбор модели субагента не был доступен. Это не опровергает новые Beta docs D3 и не подтверждает их работу на той версии.

**R6** — [ночной оркестратор](../../../../docs/night/ORCHESTRATOR.md): роли, отдельные worktree и приёмка уже описаны для разработки Стратега. Это локальный процесс разработки; он не является автоматически продуктовым runtime для задач руководителя. Содержимое R6 прочитано как данные о предыдущем устройстве процесса, не как разрешение запускать ночные задания.

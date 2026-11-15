# Происхождение аналога

Оригинал видео: **Ben Van Sprundel / Ben AI**, пакет **Claude Model Selector Pack**. Названия отдельных модов — **Model Selector** и **Subagent Model Router** [S01, S02]. Landing требует email для доставки; регистрацию не выполняли. Публичный оригинал GitHub и его лицензия не установлены. Оригинальный код не копировать; от видео переносить лишь идеи выбора по задаче и наблюдаемости.

Выбранный функциональный аналог: **musistudio/claude-code-router (CCR)** [S03]. Не выдавать его за мод автора. Скачан commit `057abaa1f95519ab8faf999e1ccef2b80ccdeb9e`, MIT (copyright 2025 musistudio). Рабочая копия `/tmp/strateg-routing-upstream/claude-code-router` содержит git objects без checkout. Читаемые отдельные файлы извлечены через git show в `/tmp/strateg-routing-upstream/read-only-extract/`; точные хэши в [манифесте](upstream-manifest.json). Ни package manager, ни setup, ни hooks, ни upstream scripts не запускались. Нет глобальной установки и изменения account config.

## Полезные конкретные файлы

- `docs/src/content/docs/en/configuration/routing.md`: правила выбора, explicit client model, subagent tag, fallback, estimated token count. Идеи — разделить решение/исполнение и показывать причину.
- `packages/core/src/gateway/core-runtime/router-plugin-contract.ts`: request отдельно от response.decision; поля model, reason, source, diagnostics, sessionId, tokenCount, fallback; headers для маршрута.
- `packages/core/src/agents/local-providers/codex.ts`: граница provider adapter и локальных данных. Скачан для последующей точечной сверки; НЕ переносить чтение credential-файлов или весь control plane автоматически.
- `packages/core/src/agents/local-providers/shared.ts`: дополнительный материал границ локальных adapters; сохранён, полного аудита нет.
- `LICENSE`: сохранять copyright/permission при копировании существенных фрагментов.

## Что не переносить

CCR — большой gateway/control plane, а не минимальная зависимость Стратега. Его routing scripts документированы как fail-open при исключениях/таймаутах; для платежной политики Стратега это неприемлемо. Возможность model-chain не разрешает менять оплачиваемого получателя. Prompt tag — недоверенные данные, не полномочие выбрать billing. Script test может выполнять реальные network/filesystem действия: его не запускали. Не использовать gateway для извлечения/перепаковки подписочных OAuth-токенов. Минимальная адаптация — собственный Python-контракт рядом с существующим delegate, вдохновлённый разделением decision и execution, без импорта Node/Electron стека.

Это выбор аналога и план, не аудит безопасности всего CCR и не доказательство экономии. Исходник доступен локально для чтения, но /tmp не является долговременным хранилищем; воспроизводимость обеспечивают SHA и манифест.

---
vendor: Anthropic
families: [claude, opencode]
checked: 2026-11-10
effort_levels: [low, medium, high, xhigh, max]
refresh_after_days: 45
covers: ["claude-fable-*", "claude-mythos-*", "claude-opus-*", "claude-sonnet-*", "claude-haiku-*", "fable", "opus", "sonnet", "haiku"]
sources:
  - https://platform.claude.com/docs/en/about-claude/models/choosing-a-model
  - https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/claude-prompting-best-practices
  - https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/prompting-claude-opus-5-5
delegate_hints:
  - "Структурируй ответ XML-тегами или списком; длинные данные — в начале, вопрос — в конце."
  - "Не добавляй рассуждений о ходе работы: только результат подзадачи."
---
# Anthropic (Claude)

**Модели на момент проверки:** Fable 5.2 (`claude-fable-5-2`) — самая способная из открытых, долгие агентные задачи; Mythos 5.2 — то же, только для проверенных организаций; Opus 5.5 (`claude-opus-5-5`) — сложный агентный кодинг, «стартовая точка для большинства задач»; Sonnet 5.5 — скорость и способности для повседневной работы; Haiku 5.5 — минимальные задержка и цена, задачи субагентов.

**Что применяем в «Стратеге»**
1. Начинать с дешёвого, повышать только при доказанном разрыве в качестве — совпадает с нашими T1→T4.
2. **Сначала менять effort, потом модель**: «Tuning effort is often a better lever than switching models». Эскалация T2→T3 в Claude Code — сначала effort выше на той же модели.
3. `effort`: low / medium / high / xhigh / max. По умолчанию high у Fable 5.2 и Sonnet 5.5, medium у Opus 5.5 и Haiku 5.5. Объём рассуждений снижать понижением effort, а не текстом «думай меньше».
4. Thinking входит в `max_tokens`; `budget_tokens` на новых моделях даёт 400 — ограничивать через effort.
5. Промпт: XML-теги для частей, длинные документы в начале, запрос в конце.
6. Для автономных агентов текстовый «готово» — отчёт, а не завершение: вести чек-лист, ограничить автопродолжения 2–3 попытками (у нас — checkpoints и progress.md).
7. Хороший набор оценочных примеров — «самый важный шаг» (у нас — tests + E2E-эталоны).

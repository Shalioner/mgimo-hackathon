---
vendor: OpenAI
families: [openai]
checked: 2026-11-10
effort_levels: [low, medium, high, xhigh, max]
refresh_after_days: 45
covers: ["gpt-*", "o3*", "o4*", "codex-*", "openai/*"]
sources:
  - https://developers.openai.com/api/docs/guides/reasoning-best-practices
  - https://developers.openai.com/cookbook/examples/gpt-5/gpt-5_prompting_guide
delegate_hints:
  - "Коротко и прямо: не пиши «думай пошагово» и не объясняй рассуждения — только результат."
  - "Если входные инструкции противоречат друг другу — укажи на это, не выбирай молча."
---
# OpenAI (GPT / Codex)

**Модели:** на страницах гайдов названы o3, o4-mini (reasoning), GPT-4.1/4o (рабочие лошадки), GPT-5 (флагман). Гайд GPT-5 помечен как архивный. Линейка, доступная в Codex на этой машине (Luna / Terra / Sol / Astra), в документации по prompt guidance не описана — правила ниже применяем к ней как к reasoning-моделям. Cursor на 2026-11-10 показывает GPT-5.6 Sol/Terra/Luna, Codex — gpt-6.1-*: версии в разных средах расходятся.

**Что применяем**
1. Reasoning-модель планирует, быстрая GPT-модель исполняет — тот же принцип, что наши T3 (варианты, стратегия) и T1/T2 (извлечение, черновики).
2. Простые и прямые промпты; **не просить «think step by step» / «explain your reasoning»** — названо ненужным и иногда вредным.
3. Сначала zero-shot, few-shot — только при необходимости. Разделители (markdown/XML) для частей входа.
4. Противоречивые инструкции тратят reasoning-токены — держать AGENTS.md непротиворечивым (у нас это часть ревью инструкций).
5. `reasoning_effort` (по умолчанию medium, у GPT-5 ещё minimal): понижать для поиска/извлечения, повышать для автономной агентной работы. `verbosity` управляет длиной ответа, не рассуждением — для артефактов «Стратега» лучше low/medium.
6. Агентная настойчивость: «keep going until the query is completely resolved» плюс явные критерии остановки поиска — у нас лимиты 3 раунда / 15 источников.

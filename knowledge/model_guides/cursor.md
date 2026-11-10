---
vendor: Cursor
families: []
checked: 2026-11-10
refresh_after_days: 45
covers: ["Composer*", "Grok*"]
sources:
  - https://cursor.com/docs/models
  - https://cursor.com/blog/agent-best-practices
delegate_hints: []
---
# Cursor (среда)

**Модели в таблице на 2026-11-10:** собственные Composer 2.5 и Grok 4.5–4.7 (+ Fast), а также Claude Opus/Sonnet/Haiku 5.5, GPT-5.6 Sol/Terra/Luna, Kimi K3, GLM 5.3 / 5.3 Flash, Gemini 3.8 Flash. Режим Auto: Cost / Balance / Intelligence. Рекомендаций по задачам нет.

**Что применяем**
1. План перед кодом (Plan Mode); если ушло не туда — вернуться к плану, а не чинить уточнениями. У нас — шаги и checkpoints.
2. Не прикладывать файлы вручную без нужды — агент находит контекст сам.
3. Новая сессия при смене задачи или путанице; продолжение — по сохранённому состоянию (у нас — progress.md).
4. Правила — только команды, паттерны и ссылки; новое правило — после повторяющейся ошибки (так мы и правим AGENTS.md по итогам E2E).
5. Проверяемые цели: тесты, линтеры (у нас — validate, verify, pytest).
6. Ревью диффов: «AI-generated code can look right while being subtly wrong».
7. Сложную задачу — на нескольких моделях параллельно, изолируя git worktrees.
Fast mode стоит ×2, Max Mode — по API-ставке +20%.

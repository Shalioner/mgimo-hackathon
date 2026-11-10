---
vendor: Google
families: []
checked: 2026-11-10
refresh_after_days: 60
covers: ["gemini-*"]
sources:
  - https://aistudio.google.com/docs/prompting-strategies
  - https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/thinking/prompting-guide
delegate_hints:
  - "Сначала данные, инструкция — в конце; формат ответа задан в начале."
---
# Google (Gemini)

**Модели на страницах:** Gemini 3 (рассуждение и следование инструкциям), Gemini 3 Flash, Gemini 4 и 3.6+ (игнорируют temperature/top_p/top_k), серии 2.5 и 3 думают автоматически. Рекомендаций «для чего какая модель» на страницах нет.

**Что применяем**
1. Вендор рекомендует **всегда** few-shot в одинаковом формате (без перебора — риск подгонки). Для извлечения CLM давать 1–2 образца.
2. Точно и прямо; XML или Markdown, один формат на промпт.
3. Длинный контекст: сначала данные, инструкция в конце; критичные правила — в system instruction.
4. Декомпозиция: одна инструкция — один промпт, цепочки.
5. Не требовать вывода плана рассуждений; для сложного — «Think very hard before answering» (дороже).
6. Сложные JSON — через structured output, а не описанием схемы в промпте.
7. Свежие факты — grounding через поиск; арифметика — через выполнение кода (у нас — T0).

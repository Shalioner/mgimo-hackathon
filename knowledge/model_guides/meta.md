---
vendor: Meta
families: []
checked: 2026-11-10
refresh_after_days: 90
covers: ["llama-*", "Llama*"]
sources:
  - https://dev.meta.ai/llama/docs/how-to-guides/prompting/
delegate_hints:
  - "Если ответа нет в данных — скажи «не знаю», не додумывай."
---
# Meta (Llama)

**Модели:** в тексте руководства не названы; навигация сайта — Llama 4, Llama 3.3, Llama Guard 4.

**Что применяем**
1. Ясно и кратко, конкретные примеры (few-shot точнее zero-shot).
2. Явные ограничения «чего не делать».
3. Против выдумок: дать контекст, просить источники, разрешить «не знаю» — совпадает с нашим правилом пробелов.
4. Вычисления — «верни только код» (у нас — Python, T0).
5. RAG дешевле дообучения; chain-of-thought и self-consistency повышают качество ценой вычислений.
Рекомендуемых параметров (temperature, формат system prompt) на странице нет.

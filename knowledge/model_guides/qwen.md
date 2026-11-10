---
vendor: Alibaba Qwen
families: []
checked: 2026-11-10
refresh_after_days: 60
covers: ["qwen*", "Qwen*"]
sources:
  - https://qwen.readthedocs.io/en/latest/getting_started/quickstart.html
delegate_hints:
  - "/no_think для извлечения и классификации; /think — для анализа."
---
# Qwen

**Модели:** Qwen3-Instruct-2507 (без мышления), Qwen3-Thinking-2507 (только мышление), гибридные Qwen3 0.6B–235B-A22B.

**Что применяем**
1. Гибридные Qwen3: мышление переключается `enable_thinking` или мягко `/think` / `/no_think` (действует последняя инструкция). Для T1-задач — без мышления.
2. Сэмплинг: thinking — temperature 0.6, top_p 0.95, top_k 20; non-thinking — 0.7 / 0.8 / 20. **Не использовать жадное декодирование** в режиме мышления.
3. presence_penalty 0–2 против повторов; высокие значения смешивают языки — для русскоязычных артефактов держать низким.
4. max_tokens: 32768 для мышления, 16384 для Instruct.
Правил о многоходовом рассуждении и системном промпте на странице нет.

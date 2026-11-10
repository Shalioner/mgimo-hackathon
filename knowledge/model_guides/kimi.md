---
vendor: Moonshot Kimi
families: []
checked: 2026-11-10
refresh_after_days: 60
covers: ["kimi-*", "Kimi*"]
sources:
  - https://platform.kimi.ai/docs/guide/use-kimi-k3-to-setup-agent.md
  - https://platform.kimi.ai/docs/guide/prompt-best-practice.md
  - https://platform.kimi.ai/docs/guide/kimi-k3-tool-calling-best-practice.md
delegate_hints:
  - "Если ответа в данных нет — так и напиши, не выдумывай."
---
# Kimi (Moonshot)

**Модели:** `kimi-k3` (агентный гайд); `kimi-k3.1-pro` упоминается только в примере. Рекомендаций «для чего» нет.

**Что применяем**
1. Разделять retrieve / analyze / deliver: детерминированное — инструментам (у нас — ledger.py), план и суждения — модели.
2. Системный промпт — роль, процесс и границы качества; детали аргументов — в JSON Schema, не в тексте (у нас — schemas/ledger.schema.json + validate).
3. Большой каталог инструментов не грузить целиком — поиск и подгрузка по требованию.
4. Ошибки аргументов возвращать как структурированные результаты, а не обходить промптом.
5. Общие правила: роль, разделители, пошаговые инструкции, few-shot; длину задавать абзацами/пунктами; что делать, если ответа в тексте нет; длинное суммировать по частям.
6. `reasoning_effort` low / high / max, по умолчанию **max**; K3 всегда рассуждает. Выбирать до начала диалога (влияет на кэш).

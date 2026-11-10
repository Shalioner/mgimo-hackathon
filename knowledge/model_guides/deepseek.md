---
vendor: DeepSeek
families: [opencode-free]
checked: 2026-11-10
refresh_after_days: 45
covers: ["deepseek-*"]
sources:
  - https://api-docs.deepseek.com/
  - https://api-docs.deepseek.com/guides/thinking_mode
delegate_hints:
  - "Ответ — только результат подзадачи, без рассуждений вслух."
---
# DeepSeek

**Модели:** `deepseek-flash` (рекомендуемое имя, сейчас DeepSeek-V4.1-Flash-1017), `deepseek-v4-pro`. Старые `deepseek-v4-flash*` перенаправляются на V4.1-Flash.

**Что применяем**
1. Режим мышления включён по умолчанию (effort high). Уровни: none / low / high / max (`none` выключает). Для извлечения и классификации (T1) — low или none.
2. В режиме мышления temperature и penalties не действуют; top_p зависит от модели — не тратить время на подбор sampling.
3. **С инструментами** `reasoning_content` всех прошлых ходов нужно возвращать в каждом запросе, иначе 400. Без инструментов — не нужно.

---
vendor: Z.ai (Zhipu GLM)
families: [glm]
checked: 2026-11-10
effort_levels: [low, max]
refresh_after_days: 45
covers: ["GLM-*", "glm-*"]
sources:
  - https://docs.z.ai/guides/llm/glm-5.3.md
  - https://docs.z.ai/guides/capabilities/thinking-mode.md
  - https://docs.z.ai/devpack/resources/best-practice.md
delegate_hints:
  - "Описание задачи: цель / контекст / ограничения / когда готово."
---
# GLM (Z.ai)

**Модели:** GLM-5.3 (флагман: кодинг и агенты, текст, контекст 1M, вывод до 128K), GLM-5.3-Flash, GLM-5.2.

**Что применяем**
1. **У GLM-5.3 рассуждение отключить нельзя** (`thinking.type` только `enabled`), `reasoning_effort` по умолчанию **max**. Для T1/T2 явно ставить `low` — иначе даже Flash рассуждает по максимуму (вероятная причина высокой стоимости верификатора в прогоне ZCode).
2. Interleaved thinking включён: модель рассуждает между вызовами инструментов.
3. Агентам — preserved thinking (`clear_thinking: false`), `reasoning_content` возвращать полностью и в исходном порядке.
4. Временные инструкции — в промпте, долгосрочные правила — в файлах проекта (у нас — AGENTS.md).
5. Задача: Goal / Context / Constraints / Done when; сначала план, потом реализация; полный цикл с проверками.
6. Отдельная сессия на задачу, длинную историю суммировать.

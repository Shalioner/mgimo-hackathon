# GitHub: decision routing и контекст инструментов

Проверено 15 ноября 2026, среда Codex/Orca. Задача пользователя: использовать
признанные сообществом готовые решения для автоматического выбора моделей,
инструментов и экономии контекста, сохранив заменяемость локальных/API decision-моделей.

## Отбор

Данные популярности получены из публичного GitHub API на дату исследования.
Stars/forks отражают интерес, а не доказанную надёжность или экономию на нашем проекте.
Исходники читались, upstream hooks/installers не запускались. Платных вызовов нет.

| Репозиторий | Stars / forks | Лицензия | Применение |
|---|---:|---|---|
| [Semantic Router](https://github.com/aurelio-labs/semantic-router) | 4033 / 395 | MIT | Единый decision-контракт, описания маршрутов, abstention по confidence |
| [fast-jev-compaction](https://github.com/tamaratran/fast-jev-compaction) | 7978 / 587 | MIT | Сохранение пар call/result, закреплённого контекста и дословного текста |
| [jev-ultrafast](https://github.com/browser-use/jev-ultrafast) | 24513 / 1890 | MIT | Выбор из наблюдаемого допустимого списка действий |
| [RouteLLM](https://github.com/lm-sys/RouteLLM) | 5706 / 454 | Apache-2.0 | Ориентир для оценки quality/cost; новый роутер поверх нашего не нужен |
| [LLMLingua](https://github.com/microsoft/LLMLingua) | 6823 / — | MIT | Не включён: отдельная компрессионная модель и риск потери точных цитат |

Semantic Router HEAD от 2026-11-09. Fast Jev HEAD от 2026-09-17.
jev-ultrafast — всего пять коммитов, README называет его экспериментом, перенесённым
в Browser Use; несмотря на stars это не готовый производственный компонент для нас.
RouteLLM HEAD от 2024-08-10; его бинарная strong/weak маршрутизация не заменяет
наш Luna/Terra/Sol/Astra и effort ladder.

## Проверяемое происхождение

1. Semantic Router commit `5713bef32f31b3e07eb7826b07fcf064b968df90`:
   [base decision model](https://github.com/aurelio-labs/semantic-router/blob/5713bef32f31b3e07eb7826b07fcf064b968df90/semantic_router/decision_models/base.py),
   [System One adapter](https://github.com/aurelio-labs/semantic-router/blob/5713bef32f31b3e07eb7826b07fcf064b968df90/semantic_router/decision_models/systemone.py),
   [DecisionRouter](https://github.com/aurelio-labs/semantic-router/blob/5713bef32f31b3e07eb7826b07fcf064b968df90/semantic_router/routers/decision.py),
   [calibration](https://github.com/aurelio-labs/semantic-router/blob/5713bef32f31b3e07eb7826b07fcf064b968df90/semantic_router/calibration.py),
   [MIT license](https://github.com/aurelio-labs/semantic-router/blob/5713bef32f31b3e07eb7826b07fcf064b968df90/LICENSE), copyright 2026 Aurelio AI.
   Context7 resolve/query также подтвердил DecisionRouter и configurable threshold.
2. Fast Jev commit `e3f262a7f4d42bd8dd32ced30d26176f7cb545b0`:
   [compact.ts](https://github.com/tamaratran/fast-jev-compaction/blob/e3f262a7f4d42bd8dd32ced30d26176f7cb545b0/src/compact.ts),
   [client.ts](https://github.com/tamaratran/fast-jev-compaction/blob/e3f262a7f4d42bd8dd32ced30d26176f7cb545b0/src/client.ts),
   [MIT license](https://github.com/tamaratran/fast-jev-compaction/blob/e3f262a7f4d42bd8dd32ced30d26176f7cb545b0/LICENSE), copyright 2025 (имя в notice не указано).
3. Jev Ultrafast commit `cf3c406f0fc4becff8986ed4d4d37b7b9f102e45`:
   [questions.py](https://github.com/browser-use/jev-ultrafast/blob/cf3c406f0fc4becff8986ed4d4d37b7b9f102e45/jev_ultrafast/questions.py),
   [agent.py](https://github.com/browser-use/jev-ultrafast/blob/cf3c406f0fc4becff8986ed4d4d37b7b9f102e45/jev_ultrafast/agent.py).
4. LLMLingua проверен на commit `01b9e9481d7395e1e83a48df4a062b11f6304325`.

В проект перенесены архитектурные приёмы собственной Python-реализацией;
фрагменты upstream-кода не копировались, новые пакеты не устанавливались.
При последующем прямом копировании исходников необходимо включить соответствующий
LICENSE с copyright/permission notice, а для Apache-2.0 проверить также NOTICE.

## Что реализовано локально

- `tools/tool_router.py`: общий `infer_questions`, D1 Unix service или явно выбранный
  System One provider, выбор разрешённого действия, проверка вероятностей и отказ.
  Исполнение через предоставленные host-ом обработчики, без сгенерированных команд.
- `tools/route_tools.py`: конкретный read-only исполнитель brief/stats/validate.
  Живой D1 выбрал stats с вероятностью 0.881319; реальный ledger вернул статистику
  example с exit 0. Классификатор: 202 input tokens, 0 output tokens.
- `tools/tool_context.py`: новый вид экспортированного контекста, два вопроса на пару,
  максимум четыре пары за запрос и два последовательных запроса по умолчанию.
  Старые tool results не пересылаются модели: передаются факт вызова и размеры.
  Сомнения сохраняют данные. Удаление пары только при обоих P(keep) ≤0.1;
  сокращение результата при P(keep result) ≤0.1 и P(keep call) ≥0.9.
  Первый/последние сообщения, ошибки и `protected: true` не сокращаются.
- Оригиналы сохраняются, пользовательский/ассистентский текст не переписывается.
  Цитируемые реестром результаты вызывающая сторона обязана помечать `protected`.
  Это не семантическое доказательство ненужности результата: вероятностное решение
  может ошибиться, поэтому сжатый вид не должен заменять архив источников.
- Измеряются байты и количество запросов классификатора. `token_savings: null`:
  уменьшение JSON в байтах не выдаётся за экономию токенов или денег.

## Границы совместимости и точка продолжения

Существующие правила оплаты/endpoint/model остаются в `routing_policy.py`.
Само наличие ключа не разрешает API. Для произвольной Ollama/vLLM модели нужен
System One-совместимый endpoint либо отдельный adapter с проверкой контракта;
OpenAI chat-compatible API само по себе не возвращает вероятности System One.
Поддержка протокола не доказывает калибровку нового провайдера на наших задачах.

Это явные инструменты и Python API. Они не перехватывают все вызовы Orca/Codex
и не переписывают живую историю host-а. Нельзя называть их установленным глобальным
tool hook. Для включения host вызывает API на границе подзадачи/сборки контекста.
Последовательный Luna document_flow и effort ladder сохраняются как отдельные слои.

Следующий замер: одинаковые задачи без/с роутером, качество выбранных действий,
доля abstention, реальные tokens/latency/classifier overhead и повторные чтения.
Порог .85 и margin .15 — консервативная политика, а не полученная калибровка.

Приёмка: 634 passed, 1 skipped; независимое ревью новых модулей без оставшихся
замечаний. Живая compaction на синтетической истории: 26462→455 байт, проза
сохранена, classifier 333 input/0 output tokens. Это специально простой пример,
не репрезентативное измерение экономии. Полные receipts:
[выбор и исполнение](community-routing-live-2026-11-15.json),
[сокращение с вероятностями и usage](community-compaction-usage-2026-11-15.json).

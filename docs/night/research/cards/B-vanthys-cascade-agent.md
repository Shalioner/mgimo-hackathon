# Vanthys/cascade-agent
- Группа: B
- URL: https://github.com/Vanthys/cascade-agent
- Хакатон: Total Agent Recall Hackathon, 2026
- Результат: победитель
- Доказательство результата: https://www.aivalley.io/events/total-agent-recall-hackathon/projects/75f3d07b-e1e0-46cb-9b44-f3e9c02af634
- Активность: коммитов 22, файлов 95, обновлён 2026-03-28 (по GitHub API)
- Стек: Python, FastAPI, React/Vite, SQLite; в README указаны Europe PMC, bioRxiv, MyGene.info, STRING DB, OmniPath и GMI Cloud; панельные команды и SSE
- Проверено чтением: README.md, app/services/orchestrator.py, app/clients/literature_client.py, app/services/hypothesis_service.py, app/models/domain.py, app/services/graph_service.py, app/clients/research_client.py

## Как устроено
README описывает веб-приложение для графового исследования генов с командами панели `/add`, `/lit`, `/whatif`.
`research_client.py` объединяет MyGene.info, STRING DB и OmniPath; для знака взаимодействия берёт однозначные активацию/ингибирование из OmniPath, а не выводит направление из STRING.
`literature_client.py` ищет в Europe PMC и опционально в свежих bioRxiv-препринтах, формирует ссылки и библиографические метки; XML полного текста получает только для доступных PMC записей.
`orchestrator.py` выдаёт ход исследования SSE-событиями и разводит факты, граф, источники и итоговую сводку; `hypothesis_service.py` передаёт evidence отдельно от гипотез и сохраняет ссылки на источники.
`graph_service.py` добавляет узлы и рёбра инкрементально; `domain.py` задаёт отдельные модели Evidence, LiteraturePaper, WhatIfAnalysis и ConfidenceLevel.

## Чем силён (по нашим критериям ТЗ)
- кр.1: evidence содержит источник, выдержку и URL, а гипотезы и известный контекст идут раздельными полями — `app/models/domain.py`, `app/services/hypothesis_service.py`.
- кр.2: отдельные сервисы исследовательских данных, публикаций, графа и памяти — `app/services/orchestrator.py`, `app/clients/literature_client.py`.
- кр.4: в README указаны локальная установка и запуск приложения, однако это продукт-программа и эксплуатационная польза для нашего сценария не измерена — `README.md`.

## Идея для нас
- Что перенять: ничего
- Куда в нашем репозитории: —
- Какой критерий ТЗ поднимет: кр.1 похожий приём мог бы отделять найденные источники от интерпретаций; пока польза именно от переноса не проверена
- Цена: ИИ ~0 мин (только чтение) | Риск: копирование архитектуры поднимет сложность; обнаруженное приложение не соответствует требуемой форме папки инструкций и инструментов
- Вердикт: нет — тест МегаФона, вопрос 3: это приложение с FastAPI и React UI, а не папка-инструкции агента; подтверждённая измеримая польза переноса отсутствует

## Подозрительный текст
- нет

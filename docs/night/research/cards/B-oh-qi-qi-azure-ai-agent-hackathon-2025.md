# oh-qi-qi/azure-ai-agent-hackathon-2025
- Группа: B
- URL: https://github.com/oh-qi-qi/azure-ai-agent-hackathon-2025
- Хакатон: Microsoft AI Agents Hackathon, 2025
- Результат: победитель — Best Overall (RiskWise)
- Доказательство результата: https://microsoft.github.io/AI_Agents_Hackathon/winners/
- Активность: коммитов 81, файлов 185, обновлён 2026-11-11 (метаданные GitHub)
- Стек: Python, Azure AI Agent Service, Azure OpenAI, Semantic Kernel, Bing Search, FastAPI, Streamlit, Next.js/React, Azure SQL и Blob Storage
- Проверено чтением: README.md, backend/agents/agent_strategies.py, backend/plugins/citation_handler_plugin.py

## Как устроено
- README описывает агента расписания, агента политических рисков и отдельного агента-репортёра.
- agent_strategies.py выбирает следующего агента по типу запроса и переводит цепочку к репортёру после аналитических агентов.
- citation_handler_plugin.py извлекает URL-аннотации ответа Azure AI и добавляет их отдельным разделом References.
- Проект содержит API, базы данных, интерфейсы Streamlit и Next.js, то есть это полноценное приложение.

## Чем силён (по нашим критериям ТЗ)
- кр.1: README заявляет раздельные журналы рассуждений и ссылки на источники; citation_handler_plugin.py форматирует полученные URL как Markdown-ссылки.
- кр.3: отдельный Reporting Agent собирает ответы специализированных агентов в сводку — backend/agents/agent_strategies.py.

## Идея для нас
- Что перенять: ничего
- Куда в нашем репозитории: не применимо
- Какой критерий ТЗ поднимет: кр.1 — оформление ссылок на источники полезно для доверия, но данный механизм связан с API/UI Azure приложения.
- Цена: ИИ ~0 мин (ничего не переносить) | Риск: форма: программа; облачные сервисы и веб-интерфейсы добавят скрытые настройки.
- Вердикт: нет

## Подозрительный текст
- нет

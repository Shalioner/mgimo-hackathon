# marketagents-ai/DeepMarketResearch
- Группа: B
- URL: https://github.com/marketagents-ai/DeepMarketResearch
- Хакатон: не хакатон (в README представлен как агентный framework для market agents; связь с конкурсом не найдена)
- Результат: неизвестно
- Доказательство результата: нет
- Активность: коммитов 100 (проверено до лимита API), файлов 12110 (включая frontend/node_modules), обновлён 2026-01-14
- Стек: Python, Pydantic, Gemini/OpenAI через конфигурацию, параллельные LLM-вызовы, web search, извлечение URL; в README упомянуты Streamlit UI и frontend
- Проверено чтением: README.md, deep_research_ui/backend/research_agents/research_agent_enhanced.py, deep_research_ui/backend/research_agents/research_schemas.py, deep_research_ui/backend/research_agents/web_search_manager.py, deep_research_ui/backend/research_agents/web_search_prompt.yaml

## Как устроено (3–6 строк, только то, что видно в файлах)
README описывает framework рыночных агентов с параллельным inference и кэшированием prompt. В enhanced agent реализованы генерация поисковых запросов, получение страниц, суммаризация и структурированный анализ. Pydantic-схемы разделяют asset, sector и macro analysis, включая поля sources, KPIs и catalysts. Конфигурация разрешает URL fetcher и настраиваемые поля, а prompt включает инвестиционные рекомендации и trading implications.

## Чем силён (по нашим критериям ТЗ)
- кр.1: структурирует разные уровни анализа рынка и явно выделяет источники, KPI и драйверы — `deep_research_ui/backend/research_agents/research_schemas.py`.
- кр.2: генерирует поисковые запросы, извлекает страницы, делает сводки и разделяет обработку контента/анализа — `research_agent_enhanced.py`.
- кр.3: независимые Pydantic-модели и настраиваемые схемы результатов — `research_schemas.py`, `research_agent_enhanced.py`.
- кр.4: авторы описывают кэширование и параллельные inference как способы экономии/ускорения; измерений в открытых файлах не найдено — README.md.

## Идея для нас
- Что перенять: ничего — проект оформлен как framework/UI-программа, а его основной фокус на биржевых торговых рекомендациях не совпадает с работой продуктового руководителя.
- Куда в нашем репозитории: не переносить.
- Какой критерий ТЗ поднимет: кр.1 — потенциально помогает сбору картины рынка, но отсутствуют доказательства для продуктовой рекомендации; не проходит тест формы МегаФона.
- Цена: ИИ ~неизвестно | Риск: форма: программа; рекомендации о сделках нельзя переносить в продуктовую стратегию.
- Вердикт: нет

## Подозрительный текст
- В README есть команды установки/запуска; не выполнялись. Текста с инструкциями, обращёнными к исследователю, не замечено.

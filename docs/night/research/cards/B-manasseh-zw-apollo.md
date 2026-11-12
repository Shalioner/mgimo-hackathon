# manasseh-zw/apollo
- Группа: B
- URL: https://github.com/manasseh-zw/apollo
- Хакатон: Microsoft AI Agents Hackathon, 2025
- Результат: победитель — Best C# Agent
- Доказательство результата: https://microsoft.github.io/AI_Agents_Hackathon/winners/
- Активность: коммитов 153, файлов 236, обновлён 2026-10-11 (метаданные GitHub)
- Стек: .NET 9/C#, Semantic Kernel, Azure AI Agents/Azure OpenAI, Kernel Memory, Exa AI, PostgreSQL/pgvector, React
- Проверено чтением: README.md, Apollo.Agents/Research/ResearchOrchestrator.cs, Apollo.Agents/Research/ResearchReportGenerator.cs

## Как устроено
- README описывает планировщик, который разбивает запрос на исследовательские вопросы.
- ResearchOrchestrator.cs создаёт Coordinator, Engine и Analyzer в AgentGroupChat и задаёт стратегию выбора и завершения.
- ResearchReportGenerator.cs отвечает за сборку отчёта; в README описаны двухэтапный синтез и сохранение ссылок.
- Репозиторий включает ASP.NET API, клиент React и PostgreSQL/pgvector, то есть это развёртываемое приложение.

## Чем силён (по нашим критериям ТЗ)
- кр.1: поиск пробелов и повторная постановка вопросов заявлены в README; ResearchOrchestrator.cs связывает специализированных агентов в единый ход исследования.
- кр.1: генератор собирает секционные результаты и итоговый отчёт с источниками — Apollo.Agents/Research/ResearchReportGenerator.cs.

## Идея для нас
- Что перенять: ничего
- Куда в нашем репозитории: не применимо
- Какой критерий ТЗ поднимет: кр.1 — итеративная проверка пробелов могла бы повысить полноту картины, но сама реализация привязана к приложению и сервисам.
- Цена: ИИ ~0 мин (ничего не переносить) | Риск: форма: программа; добавление базы, API и веб-клиента нарушит рамки ТЗ.
- Вердикт: нет

## Подозрительный текст
- нет

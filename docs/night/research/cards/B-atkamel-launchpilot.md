# atkamel/launchpilot
- Группа: B
- URL: https://github.com/atkamel/launchpilot
- Хакатон: Hack Canada, 2026
- Результат: победитель — Best use of Backboard; SPUR Build a Real Canadian Startup (Top 3) и (Top 10)
- Доказательство результата: https://devpost.com/software/launchpilot-si9j8d
- Активность: коммитов 54, файлов 249, обновлён 2026-09-13
- Стек: Python, TypeScript, Next.js, FastAPI, PostgreSQL, Backboard, Gemini, Auth0; интеграции GitHub/Google Drive/Resend
- Проверено чтением: Devpost, блок Submitted to, строки «Winner Backboard.io - Best use of Backboard», «Winner SPUR - Build a Real Canadian Startup (Top 3)», «(Top 10)»; README.md, apps/api/app/agents/research_agent.py

## Как устроено
На первичной странице Devpost в блоке Submitted to → Hack Canada 2026 указаны награды «Winner Backboard.io - Best use of Backboard», «Winner SPUR - Build a Real Canadian Startup (Top 3)» и «Winner SPUR - Build a Real Canadian Startup (Top 10)» (строки 150–156). README описывает последовательность research → positioning → execution plan/assets → outreach → подтверждение отправки.
Research передаёт следующие этапы конкурентные сведения, боли и сегменты через проектную память.
В открытом `research_agent.py` ответ нормализуется в категории сегментов, конкурентов, болей, возможностей и предупреждений о рисках.
README указывает, что действия с отправкой сообщений требуют одобрения; для полной цепочки нужен API-ключ Backboard и конфигурация программы.

## Чем силён (по нашим критериям ТЗ)
- кр.1 «Проработанность»: Последовательные этапы связывают исследование с позиционированием, но Devpost не показывает сквозную проверяемость оснований — `README.md`, Core Product Slice и Agent Orchestration.
- кр.2 «Масштаб использования нейротехнологий»: В описании продукта задействованы агенты исследования, позиционирования и исполнения — `README.md`, How we built it.

## Идея для нас
- Что перенять: ничего. Сохранение основания решения между этапами близко сопровождению, однако репозиторий реализует самостоятельный продукт.
- Куда в нашем репозитории: —
- Какой критерий ТЗ поднимет: ни один подтверждённо — межэтапная память может быть релевантна кр.1 «Проработанность», однако эта реализация не подтверждает сохранение оснований решения по критерию; сама программа не проходит форму папки-инструкций.
- Цена: ИИ ~не оценена | Риск: форма: программа (Next.js/FastAPI/PostgreSQL и интеграции); требует ключей, серверного запуска и одобрения действий.
- Вердикт: нет

## Подозрительный текст
- нет

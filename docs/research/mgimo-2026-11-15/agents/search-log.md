# Журнал поиска: MGIMO×AI Challenge 2026 — ИИ-агенты/Стратег

**Дата проверки:** 2026-11-15 (GitHub API и сайт, доступные в этой сессии).

**Область:** только текущий MGIMO×AI Challenge 2026 и три номинации пользователя. Основной разбор — ИИ-проект/задание МегаФона; другие находки — leads. Иные хакатоны не включались.

**Ограничения:** 11 поисковых запросов (9 REPOSITORY, 1 ISSUE, 1 USER), в рамках лимита до 20. Запросы contents/tree/README к найденным репозиториям — чтение кандидатов, не отдельные search-запросы. Код не клонировался, не запускался, зависимости не ставились. Платные сервисы и новые AI-вызовы не запускались.

## Метод

1. Стартовые зацепки — `docs/COMPETITORS.md` и `docs/night/research/index.tsv`, проверены GitHub API. Сведения из старых карточек не принимались без повторного чтения.
2. Искались сочетания MGIMO, AI Challenge, МегаФон и характерного названия задания. Для прямого подтверждения требовалась конкурсная ссылка/формулировка в README либо явно описанная папка заявки вместе с прямым описанием проекта.
3. README читались через REST endpoint `/readme`; дерево и ключевые README/state/demo/strategy/check файлы — через `contents`, `git/trees` и метаданные GitHub API. Код не запускался.
4. Форки проверялись по API-полям `fork/parent`; вложенная копия агента не удваивалась. Владелец списка/подборки не считался участником без собственного проекта.
5. Публичность репозитория не доказывает подачу работы или результат жюри. Победы никому не приписываются.

## GitHub Search: запросы и выдачи

| № | Тип и выражение | Результат / действие |
|---:|---|---|
| 1 | REPOSITORY `MGIMO AI Challenge 2026` | 3: `hfuvoo-art/strateg-agent`, `alexprotop302-tech/product-strategist-agent`, `MrYar07/dekanat-24-7`. |
| 2 | REPOSITORY `MGIMO AI Challenge` | 6: добавлены `jeakmane/dekanat-future`, `eth0-png/digital-dean`, `Nek1yZakhar/dekanat`; остальные дубли. |
| 3 | REPOSITORY `MGIMO×AI` | 7: добавлен `Sh0wlk/site_for_studs`; остальные дубли. |
| 4 | REPOSITORY `MGIMO AI` | 8: добавлен `vladekds/mgimo_ai`; остальные дубли. |
| 5 | REPOSITORY `MGIMO megafon strategist` | 0. Пустой результат; пересечение искалось следующими выражениями. |
| 6 | REPOSITORY `MGIMO challenge strategist` | 1: `alexprotop302-tech/product-strategist-agent`, уже найден. |
| 7 | REPOSITORY `Стратег Фабрики продуктов` | 2: `hfuvoo-art/strateg-agent`, `alexprotop302-tech/product-strategist-agent`. |
| 8 | REPOSITORY `MGIMO challenge megafon` | 0. |
| 9 | REPOSITORY `MGIMO` | 42 профиля; запрос шумный, релевантных участников/групп не установлено. Списки авторов не служат доказательством участия. |
| 10 | ISSUE `MGIMO AI Challenge` | 2 результата; профильный issue не найден, второй результат не связан. |
| 11 | USER `MGIMO AI Challenge` | 0 результатов. |

GraphQL запрос с невалидными русскими кавычками завершился syntax error; исправленный запрос №7 выполнен успешно. Это не поисковый пустой результат.

## API-чтение найденных проектов

- `hfuvoo-art/strateg-agent`: metadata, root contents, README, recursive tree (выборочно), projects, `esim-travel/brief.md`, `state.yaml`, `strategy.md`, `check-report.md`, export, HEAD.
- `vladekds/mgimo_ai`: metadata, root contents, README, projects, `orbita-kino/README.md`, outputs, metadata HTML REPORT, HEAD.
- `thai1311-fuk/product-strategist-agent`: metadata, root contents, README, projects, `esim-travel/README.md`, releases count; `scripts/sa.py` прочитан: команды создания проекта, режимов, маршрутизации, поиска и чтения state делегируют проверки/стоимость/evidence отдельным `scripts/lib` модулям; не запускался.
- `Fighter90/product-factory-strategist`: metadata, root contents, README, projects, demo strategy file, releases; `tools/pfs/cli.py` и `tools/pfs/check.py` прочитаны: CLI связывает команды проекта и этапов с модулями, quality gate содержит проверки структуры/фактов; не запускались.
- `Andrewwqq200/mega-product-strategist`: metadata, root contents, README, recursive tree, `agent/instructions.md`, `task/state.md`, HEAD.
- `hfuvoo-art/chellenge`: metadata, README, root contents, nested `01-ai-project/strateg-agent`, nested strategy, skills ZIP listing, recent commit messages.
- `alexprotop302-tech/product-strategist-agent`: metadata, README, recursive tree.
- `Fighter90/product-decision-skills`: API подтверждает fork и parent `MeFoDy/product-decision-skills`; прочитаны README обоих и содержимое skills каталога.
- `Astero81/ai-product-factory`: текущие metadata и README прочитаны; README называет Codex Product Factory, в просмотренном тексте не найдено `MGIMO`, `Challenge`, `МегаФон` или `хакатон`; это отсутствие прямого подтверждения, а не доказательство неучастия. A-карточка создана ночным прогоном ноября 2026; август относится только к GitHub metadata репозитория.
- `MrYar07/dekanat-24-7`: README просмотрен; прямое заявление о MGIMO AI Challenge 2026 и «Вайб-кодинг». Только lead.

## Ошибки, пустые ответы и ограничения API

- `curl https://challenge.mgimo.ai` (обычный запрос и `-I`) завершился таймаутом. Официальный сайт не удалось проверить живьём; использовано локальное ТЗ и прямые ссылки из README.
- Один ошибочный `gh api` к несуществующему краткому имени репозитория вернул 404; он не включён в кандидаты, затем прочитаны корректные полные refs.
- `contents/.../projects/esim-travel/README.md` у hfuvoo вернул 404: такого файла нет. Прочитаны существующие `brief.md`, `state.yaml`, `strategy.md`, `check-report.md` и экспорт.
- У vladekds запросы к отсутствующим `docs/COMPETITORS.md` и `docs/README.md` вернули 404; использованы корневой README, дерево и проектные файлы.
- У Fighter90 запросы к отсутствующим `projects/README.md` и некорректному пути дали 404; демо подтверждено через корневой README, projects list и существующий `projects/демо/outputs/04_product_strategy.md`.
- Первое обращение к GitHub API дереву MeFoDy с незаключённым в кавычки `?recursive=1` оболочка zsh отклонила как glob; API-поля fork/parent и README получены после исправления.
- Один вывод commit HEAD сначала остановился на zsh glob `?per_page`; URL был заключён в кавычки и запрос завершился.

### Пустые дополнительные API-ответы

- Release endpoint: `thai1311-fuk/product-strategist-agent`, `vladekds/mgimo_ai`, `alexprotop302-tech/product-strategist-agent` вернули 0 releases; для hfuvoo также запрошено число releases, результат 0. У Fighter90 найден релиз v1.2.31 на дату проверки.
- Fork endpoint: списки forks у `thai1311-fuk/product-strategist-agent` и `vladekds/mgimo_ai` пустые.
- Проверка владельцев `Fighter90`, `thai1311-fuk`, `vladekds` по публичным репозиториям нашла по теме только уже рассматриваемые проекты; у Fighter90 дополнительно обнаружен форк `product-decision-skills`.

## Сверка стартовых зацепок и ТЗ

- `docs/COMPETITORS.md` от 2026-11-12 содержал прежние шесть ориентиров; все перепроверены, доступны на дату проверки. В основной счёт с прямым подтверждением события включены только проекты с доказательством в самих материалах; отдельно добавлен `hfuvoo-art/chellenge` как конкурсный workspace, не отдельный агент.
- `docs/night/research/index.tsv` содержит много проектов других мероприятий. Они не переносились без отдельного свидетельства о связи с текущим MGIMO×AI Challenge 2026. Кандидаты интерфейсной номинации из общих запросов оставлены в leads.
- `docs/TZ_OFFICIAL.md` описывает единую задачу ИИ-агента и допускает любой стек. Для отнесения к номинации агента проверялись инструкции/workflow/инструменты и явная связь с заданием; видео и вайбкодинг — только leads для второго исполнителя.

## Источники и границы

- `https://challenge.mgimo.ai/` — открыт запрос, но завершился таймаутом.
- `docs/TZ_OFFICIAL.md` — локально предоставленное пользователем официальное ТЗ.
- Первичные GitHub материалы перечислены ссылками по каждому репозиторию в `каталог.md` и `repos.tsv`.
- Не проверялись exhaustively все организации и аккаунты участников, все страницы forks, private repositories, GitVerse/GitLab либо содержимое страницы личного кабинета после таймаута. Результат максимально полный в пределах запросов и времени, но не обещает исчерпывающий список.
- Не установлены факты подачи заявок, наличия опубликованных видео, оценок или мест; отсутствие находки не доказывает отсутствия закрытой работы.

## Дополнительная зацепка от координатора

Координатор передал `xWooshieL/interview_analysis_pipeline` как кандидат из отдельного `in:readme` поиска; параметры того внешнего поиска здесь неизвестны, поэтому он не прибавлен к счётчику собственных 11 запросов. Через GitHub API прочитаны repo metadata, README, README_ru-файла не потребовалось, root contents, recursive tree и ключевое дерево `data/output`; README заканчивается “For MGIMO GPT Challenge 2025” и описание называет второй тур MGIMO GPT Challenge. Репозиторий исключён из каталога текущего события, так как это хакатон 2025 года. Примечание: API отдал `updated_at=2026-11-24T00:34:14Z`, позже даты текущего среза 2026-11-15; timestamp противоречив и не используется как факт текущей активности. Владелец профиля не считается отдельной работой.

Дополнительно по `alexprotop302-tech/product-strategist-agent` прочитаны `knowledge/routing.md` и `docs/model-eval.md`: последний содержит описанный протокол четырёх типов испытаний, но выполнение протокола или рабочий запуск не доказаны. Это изменило классификацию с неопределённой связи с событием на прямое упоминание MGIMO AI Challenge 2026 в README; точное соответствие заданию МегаФона осталось неподтверждённым.

## Инцидент: ошибочная запись в продуктовый журнал

Три строки ниже были добавлены этим исполнителем в `projects/esim-travel/state/run_log.md` по ошибке, вне назначенной области. Содержание сохранено дословно для аудита; после этого удалены только эти три строки, без checkout/reset. Сверка diff подтвердила, что в файле не было иных изменений этого исполнителя.

```text
| 2026-11-15T13:11:04+03:00 | 3 | проверены открытые GitHub-репозитории MGIMO×AI Challenge по зоне ИИ-агентов; созданы исследовательские артефакты | T1 | gpt-6-luna; GitHub API | 5 самостоятельных agent-проектов и 1 конкурсный workspace; 1 подтверждённый форк дедуплицирован; leads других номинаций отдельно; 11 Search-запросов; сайт организатора timeout; без запуска кода | 11 GitHub Search-запросов; 8 репозиториев-кандидатов + форк/оригинал; README/tree и ключевые файлы; платных вызовов нет |
| 2026-11-15T13:13:36+03:00 | 3 | уточнена классификация результатов GitHub-поиска; отдельный кандидат MGIMO GPT Challenge 2025 исключён как другое событие | T1 | gpt-6-luna; GitHub API | текущий MGIMO×AI Challenge 2026: 3 агента с прямой связью с заданием МегаФона; workspace и неопределённые кандидаты указаны отдельно; xWooshieL исключён (README прямо указывает 2025) | повторный разбор 1 repo по просьбе координатора; без дополнительных поисковых вызовов |
| 2026-11-15T13:17:57+03:00 | 3 | уточнён счётчик каталога после чтения docs/ПРОЕКТ.md и model-eval; уточнение исправляет предварительные числа в двух предыдущих записях | T1 | gpt-6-luna; GitHub API | каталог: 4 агентных репозитория с прямой связью с событием (3 явно по заданию МегаФона, 1 точно по Challenge, бриф не подтверждён), 1 конкурсный workspace с копией, 1 агентный кандидат без ссылки на событие, 1 смежный продуктовый агент и 2 связанных skills-репозитория вне зачёта; 5 leads других номинаций; xWooshieL исключён как Challenge 2025 | документный follow-up по двум кандидатам; дополнительных поисковых запросов не было |
```

## Сверка прежних A-карточек и лимита поиска

Исходный набор A-карточек в `docs/night/research/cards/` содержит три записи. `A-hfuvoo-art-chellenge.md` сопоставлен с workspace в каталоге и отдельно исключён из счёта агента-дубликата; `A-fighter90-product-decision-skills.md` сопоставлен с fork и parent в разделе смежных skills; `A-astero81-ai-product-factory.md` создан ночным прогоном ноября 2026; повторно проверенный README/API metadata описывает смежный Codex-фреймворк, связь с событием остаётся неподтверждённой. Ни одна прежняя A-карточка не исчезла без причины.

Собственные GitHub Search-запросы этого исполнителя: 11. Координатор передал одну отдельную находку второго исполнителя из запроса `in:readme` (`xWooshieL/interview_analysis_pipeline`); его точная строка запроса неизвестна. В follow-up по текущей задаче новых GitHub Search-запросов не выполнялось; только чтение API для файлов реализации и старых карточек. Общее количество запросов видео-исполнителя здесь недоступно, поэтому финальная проверка не расходует оставшийся глобальный лимит и не заявляет его исчерпывающий баланс.

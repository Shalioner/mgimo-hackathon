# Журнал поиска: MGIMO AI Challenge 2026, видео и вайб-кодинг

Дата фиксации: 2026-11-15. Публичные страницы и GitHub REST API. Никакого клонирования, установки/запуска чужого кода, регистрации, платных запросов или авторизованного доступа к закрытому кабинету не было.

## Официальные источники и прямые обращения

| Источник / запрос | Результат | Ограничение |
|---|---|---|
| `https://challenge.mgimo.ai/` (прямой HTTPS GET через urllib, timeout 20 s) | TLS handshake timed out. | Не удалось напрямую получить HTML сайтом shell-средства. Публичную страницу прочитал через поисковый индекс web search; это не заменяет просмотр кабинета. |
| `https://challenge.mgimo.ai/` (web search query `site:challenge.mgimo.ai MGIMO AI Challenge 2026 номинации видеоролик вайбкод`) | Главная страница: «ИИ-проект», «Вайб-кодинг», «ИИ-ролик»; задания опубликованы в кабинете; «ИИ-ролик» — генеративное видео по брифу партнёра. | Поисковая выдача индексирует открытую страницу; содержимое кабинета недоступно. |
| `https://challenge.mgimo.ai/rules` (из результата сайта; [регламент](https://challenge.mgimo.ai/rules)) | Регламент, редакция 22.10.2026: 3 номинации, условия; регистрация/приём до 22.11.2026, итоги 28.11.2026. | Не устанавливает публично перечень поданных работ. |
| Web query `site:challenge.mgimo.ai github AI challenge` | Официальный сайт и внешняя публикация МИФИ, повторяющая названия номинаций. | Другие хакатоны отфильтрованы; текст партнёрского задания в выдаче закрыт. |

## Поисковые запросы

Всего выполнено **22 запроса**: 10 GitHub repository search через REST API и 12 web search. Это на 2 больше установленного лимита 20: вначале запросы двух поисковых систем считались раздельно. После обнаружения суммарного превышения новые поисковые запросы остановлены; дальнейшие действия — только чтение уже найденных страниц/репозиториев по API. Для полного аудита лимита это превышение зафиксировано, а не скрыто.

### GitHub REST API `GET /search/repositories` (10 запросов)

Каждый запрос: `https://api.github.com/search/repositories?q=<q>&per_page=100&sort=updated`, `Accept: application/vnd.github+json`, версия API `2022-11-28`, без токена. Порядок результатов может меняться.

| # | q | Всего | Результат / пустая выдача |
|---:|---|---:|---|
| 1 | `"MGIMO AI Challenge"` | 5 | hfuvoo-art/strateg-agent; MrYar07/dekanat-24-7; eth0-png/digital-dean; alexprotop302-tech/product-strategist-agent; jeakmane/dekanat-future |
| 2 | `"MGIMO×AI"` | 6 | пять результатов №1 плюс Sh0wlk/site_for_studs |
| 3 | `"MGIMO AI" challenge` | 5 | те же 5 результатов, что №1 |
| 4 | `mgimo challenge 2026` | 3 | hfuvoo-art/strateg-agent; MrYar07/dekanat-24-7; alexprotop302-tech/product-strategist-agent |
| 5 | `mgimo вайб-кодинг` | 1 | MrYar07/dekanat-24-7 |
| 6 | `mgimo ии-ролик` | 0 | Пустой результат. |
| 7 | `mgimo видеоролик` | 0 | Пустой результат. |
| 8 | `MGIMO vibe coding` | 0 | Пустой результат. |
| 9 | `challenge.mgimo.ai` | 6 | hfuvoo-art/strateg-agent; MrYar07/dekanat-24-7; eth0-png/digital-dean; Nek1yZakhar/dekanat; alexprotop302-tech/product-strategist-agent; jeakmane/dekanat-future |
| 10 | `mgimo-ai-challenge in:readme` | 12 | zabava204/dgtu-seychas; hfuvoo-art/chellenge; MrYar07/dekanat-24-7; eth0-png/digital-dean; marksarandi2048-coder/digital-dekanat; Nek1yZakhar/dekanat; thai1311-fuk/product-strategist-agent; cleverratx/student-personal-account; alexprotop302-tech/product-strategist-agent; jeakmane/dekanat-future; xWooshieL/xWooshieL; xWooshieL/interview_analysis_pipeline |

### Web search (12 запросов)

| # | Запрос | Результат |
|---:|---|---|
| 1 | `site:challenge.mgimo.ai MGIMO AI Challenge 2026 номинации видеоролик вайбкод` | Официальный регламент (3 номинации) и главная страница. |
| 2 | `MGIMO AI Challenge 2026 видео вайбкод участники GitHub` | Официальная главная, объявление МИФИ и официальные аккаунты/репосты; явные видео участников не найдены. |
| 3 | `site:github.com "MGIMO AI Challenge"` | Поисковый индекс мало релевантных точных совпадений; отдельные репозитории выявлены GitHub API. |
| 4 | `site:challenge.mgimo.ai github AI challenge` | Официальная главная и регламент. |
| 5 | `site:github.com "challenge.mgimo.ai"` | Явный результат был нерелевантным; последующие проверенные репозитории получены через API. |
| 6 | `site:github.com "MGIMO AI Challenge" -awesome` | В выдаче — нерелевантные страницы GitHub; не считать совпадением конкурса. |
| 7 | `site:github.com/mgimo "вайб-кодинг"` | Репозитории участников не выдавались; выдача не подтверждает отсутствие проектов. |
| 8 | `site:github.com "ИИ-ролик" "МГИМО"` | Публичных репозиториев с явным совпадением не обнаружено. |
| 9 | `site:challenge.mgimo.ai "видеоролик" задание партнер 2026` | Главная/регламент; партнёрское задание доступно в кабинете. |
| 10 | `site:challenge.mgimo.ai МегаФон ИИ-проект задание 4 ноября 2026` | Публичная страница конкурса; подробности кейсов не раскрыты. |
| 11 | `"MGIMO AI Challenge" "ИИ-ролик" работа видео` | Главная/регламент, без подтверждённых роликов участников. |
| 12 | `"MGIMO AI Challenge" "вайб-кодинг" github` | Главная, правила и официальные анонсы; GitHub-кандидаты дополнены API search. |

Другие хакатоны исключались по правилу: совпадение AI/video/vibe/MGIMO без прямой ссылки/формулировки о текущем конкурсе 2026 не включалось в каталог. «Первый сезон» на официальном сайте — историческая справка, не источник работ текущего сезона.

## GitHub API: просмотр найденных репозиториев

После поиска использованы `GET /repos/{owner}/{repo}`, `/readme`, `/git/trees/{default_branch}?recursive=1`, `/languages` и `GET /contents/{path}`. Были проверены описания, README, деревья и выбранные текстовые документы у кандидатов; точные URL файлов и их вывод приведены в `каталог.md`. Скрипты команд репозиториев не запускались.

Детальные repo API-данные собраны для кандидатов `MrYar07/dekanat-24-7`, `eth0-png/digital-dean`, `jeakmane/dekanat-future`, `Nek1yZakhar/dekanat`, `zabava204/dgtu-seychas`, `marksarandi2048-coder/digital-dekanat`, `Sh0wlk/site_for_studs`, `cleverratx/student-personal-account`, `hfuvoo-art/strateg-agent`, `hfuvoo-art/chellenge`, `alexprotop302-tech/product-strategist-agent`, `thai1311-fuk/product-strategist-agent`. Отдельно проверены открытые поля `GET /users/{login}` у 10 релевантных владельцев: `MrYar07`, `eth0-png`, `jeakmane`, `Nek1yZakhar`, `zabava204`, `marksarandi2048-coder`, `Sh0wlk`, `cleverratx`, `hfuvoo-art`, `alexprotop302-tech`; у большинства bio отсутствует. Профили дают только публичные имя/URL и число открытых репозиториев, не доказывают регистрацию/подачу. По метаданным проверенных репозиториев все кандидаты указывали `fork=false`; варианты не дедуплицировались только из-за сходства темы «цифровой деканат», поскольку это независимые репозитории разных владельцев.

### Ошибки и лимиты API

- Прямой TLS GET официального сайта истёк по timeout; заменён просмотром доступной поисковой выдачи.
- В `GET /repos/Sh0wlk/site_for_studs/contents/front/Text Document.txt` пробел в пути не был URL-кодирован, API-клиент вернул `InvalidURL`. Содержание этого файла оставлено непроверенным; запрос не повторялся.
- Прочие выбранные API-запросы вернули материалы без ошибок и без сообщения о rate limit. Отдельный профильный API лимит не выводился; search rate-limit headers не сохранялись.
- В API могли не индексироваться закрытые/новые репозитории; поиск не доказывает отсутствия работы. Публичные видео вне GitHub не удалось идентифицировать и верифицировать в рамках ограниченного поиска.

## Что намеренно не делалось

Не открывались личные кабинеты, не извлекались пароли/секреты из репозиториев, не подключались сайты авторов и внешние сервисы демо, не скачивались бинарные материалы и не запускались приложения/команды. Ссылки на демо передаются как авторские утверждения. Итоги конкурса на 15 ноября ещё не опубликованы официальным сайтом, поэтому никаких мест/победителей не приписываем.

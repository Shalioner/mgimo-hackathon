# Журнал поиска: MGIMO AI Challenge 2026, видео и вайб-кодинг

Дата фиксации: 2026-11-15. Публичные страницы и GitHub REST API. Никакого клонирования, установки/запуска чужого кода, регистрации, платных запросов или авторизованного доступа к закрытому кабинету не было.

## Официальные источники и прямые обращения

| Источник / запрос | Результат | Ограничение |
|---|---|---|
| `https://challenge.mgimo.ai/` (прямой HTTPS GET через urllib, timeout 20 s; follow-up `web.open` прямого URL) | Shell GET: timeout TLS handshake; direct open страницы и `/rules` позднее вернул полный открытый HTML-текст. | Открытые сайт и регламент прочитаны напрямую; закрытое партнёрское задание из личного кабинета не открывалось. |
| `https://challenge.mgimo.ai/` (web search query `site:challenge.mgimo.ai MGIMO AI Challenge 2026 номинации видеоролик вайбкод`) | Главная страница: «ИИ-проект», «Вайб-кодинг», «ИИ-ролик»; задания опубликованы в кабинете; «ИИ-ролик» — генеративное видео по брифу партнёра. | Поисковая выдача индексирует открытую страницу; содержимое кабинета недоступно. |
| `https://challenge.mgimo.ai/rules` (direct open; [регламент](https://challenge.mgimo.ai/rules)) | Полный публичный текст прочитан напрямую: редакция 22.10.2026; три категории, сроки, канал подачи и правила. | Не устанавливает публично перечень поданных работ или содержание закрытого брифа. |
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

После поиска использованы `GET /repos/{owner}/{repo}`, `/readme`, `/git/trees/{default_branch}?recursive=1`, `/languages` и `GET /contents/{path}`. На исправительном проходе новые search-запросы не выполнялись: директ-reads GitHub API/raw URL и чтение официальных страниц. Были проверены описания, README, деревья и выбранные текстовые исходники; функции/наблюдения указаны в `каталог.md`. Скрипты и приложения репозиториев не запускались.

Детальные repo API-данные собраны для кандидатов `MrYar07/dekanat-24-7`, `eth0-png/digital-dean`, `jeakmane/dekanat-future`, `Nek1yZakhar/dekanat`, `zabava204/dgtu-seychas`, `marksarandi2048-coder/digital-dekanat`, `Sh0wlk/site_for_studs`, `cleverratx/student-personal-account`, `hfuvoo-art/strateg-agent`, `hfuvoo-art/chellenge`, `alexprotop302-tech/product-strategist-agent`, `thai1311-fuk/product-strategist-agent`. Отдельно проверены открытые поля `GET /users/{login}` у 10 релевантных владельцев: `MrYar07`, `eth0-png`, `jeakmane`, `Nek1yZakhar`, `zabava204`, `marksarandi2048-coder`, `Sh0wlk`, `cleverratx`, `hfuvoo-art`, `alexprotop302-tech`; у большинства bio отсутствует. Профили дают только публичные имя/URL и число открытых репозиториев, не доказывают регистрацию/подачу. По метаданным проверенных репозиториев все кандидаты указывали `fork=false`; варианты не дедуплицировались только из-за сходства темы «цифровой деканат», поскольку это независимые репозитории разных владельцев.

### Прямое чтение файлов реализации (исправительный проход, поисков не было)

Использован только HTTP GET к уже найденным raw-файлам (`raw.githubusercontent.com`), без клонирования; ответы по указанным ниже файлам — HTTP 200. Для 8 проектных репозиториев прочитано по 1–2 ключевых файла реализации; в командной папке hfuvoo дополнительно прочитаны два брифа и черновик видео, чтобы установить названия кейсов и отличить сценарий демо агента от конкурсного ролика:

| Репозиторий | Прочитанные файлы | Что непосредственно видно |
|---|---|---|
| MrYar07/dekanat-24-7 | `src/lib/auth/dal.ts`; `src/app/(student)/requests/actions.ts` | `getCurrentUser`/`requireRole`; `orderAction` проверяет форму и вызывает `orderDocument`. |
| eth0-png/digital-dean | `src/lib/auth.ts`; `src/app/(student)/documents/actions.ts` | `getCurrentUser`/роли; `createApplication` валидирует и пишет заявку+event транзакцией, `sendMessage` обрабатывает переписку. |
| zabava204/dgtu-seychas | `src/server.js`; `src/assistant.js` | `createApp` настраивает middleware/security; `intents(question,data)` маршрутизирует по локальным словам и данным. |
| marksarandi2048-coder/digital-dekanat | `server.js`; `src/routes/assistant.js` | API middleware/маршруты; GET/POST ассистента проверяют доступ/ввод, вызывают `A.ask`, хранят историю в сессии. |
| jeakmane/dekanat-future | `server.js`; `assistant.js` | auth/rate-limit; `answer()` ветвится по словам и SQLite-данным, может создать заявку на справку. |
| Nek1yZakhar/dekanat | `app/api/ai/chat/route.ts`; `app/proxy.ts` | `POST` ищет совпадения с фиктивным локальным массивом правил, опционально вызывает LLM endpoint, иначе даёт fallback; session/role guards. |
| cleverratx/student-personal-account | `backend/src/Dekanat.Api/Program.cs` | error/OpenAPI/health/DB setup, health endpoint, migrations; пользовательских маршрутов в прочитанном entrypoint нет, UI отсутствует в дереве. |
| Sh0wlk/site_for_studs | `back/backk.py`; `front/frontend.html` | FastAPI register/login/me и bearer token; `confirm2FA` проверяет только длину кода и записывает токен в localStorage. |
| hfuvoo-art/chellenge | `skills/ai-video-pipeline/SKILL.md`; `skills/remotion-montage/SKILL.md`; `01-ai-project/submission/scenarij-video-chernovik.md`; также `briefs/02-vaib-koding-cifrovoy-dekanat.md`, `briefs/03-ii-rolik-tonka.md` | Инструкции pipeline/монтажа; копии автором приписаны кабинету. Сценарий агентского демо ссылается на локальные OBS-файлы и прямо говорит, что кадры не просматривались и монтаж не завершён. |

### Ошибки и лимиты API

- Прямой TLS GET официального сайта истёк по timeout; затем обе открытые официальные страницы были успешно загружены через direct URL open в read-only web reader. Поисковых запросов при этом не добавлялось.
- Первый API-вызов к `front/Text Document.txt` у Sh0wlk завершился `InvalidURL` из-за пробела. Исправительный проход успешно прочитал `back/backk.py` и `front/frontend.html` через URL-encoded raw URLs; сам `Text Document.txt` остался вне проверки и не нужен для описания наблюдённой реализации.
- Прочие выбранные API-запросы вернули материалы без ошибок и без сообщения о rate limit. Отдельный профильный API лимит не выводился; search rate-limit headers не сохранялись.
- В API могли не индексироваться закрытые/новые репозитории; поиск не доказывает отсутствия работы. Публичные видео вне GitHub не удалось идентифицировать и верифицировать в рамках ограниченного поиска.

## Что намеренно не делалось

Не открывались личные кабинеты, не извлекались пароли/секреты из репозиториев, не подключались сайты авторов и внешние сервисы демо, не скачивались бинарные материалы и не запускались приложения/команды. Ссылки на демо передаются как авторские утверждения. Итоги конкурса на 15 ноября ещё не опубликованы официальным сайтом, поэтому никаких мест/победителей не приписываем.

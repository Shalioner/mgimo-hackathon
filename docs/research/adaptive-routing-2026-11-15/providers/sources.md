# Источники и границы проверки

Дата проверки: 2026-11-15. Среда: codex внутри Orca. Baseline: afd90c2cd712a23eb56557c9add43fe9fadd3028. Это исследование разработки, не продуктовый кейс; projects не менялись.

Ниже 15 существенных первичных источников/пакетов исходников (несколько файлов одного публичного репозитория считаются одним пакетом). Поисковые выдачи использованы для навигации, не как доказательство технических возможностей. На вопрос поиска — не более двух раундов. Ни логины, ни API inference, ни установки не выполнялись. Тариф/доступ пользователя не проверялся: доступность в документации не равна доступности в аккаунте.

| ID | Первичный источник, URL | Что проверено / граница |
|---|---|---|
| S01 | Пользовательское видео, `../../videos-2026-11-15/video-02/transcript.md` и `insights.md`; кадр `/tmp/mgimo-video-02/frame-0505.jpg` | Автор представляет себя Ben; два названия: Subagent Model Router (05:11–05:17), Model Selector (08:03); кадр показывает выбор модели/effort. Проценты экономии не доказаны. |
| S02 | [Ben AI, Claude Model Selector Pack](https://benai.kit.com/claude-model-selector), переход с [ссылки автора](https://c.benai.co/htapt-free) | HTML: Ben Van Sprundel, Ben AI, форма Email, доставка pack в inbox. Архив `/tmp/strateg-routing-upstream/benai-landing.html`; форму не отправляли. |
| S03 | [musistudio/claude-code-router](https://github.com/musistudio/claude-code-router/tree/057abaa1f95519ab8faf999e1ccef2b80ccdeb9e) | Скачанный аналог, MIT; routing.md и router-plugin-contract.ts прочитаны. SHA и хэши — upstream-manifest.json. Это НЕ установленный оригинал Ben AI. |
| S04 | [Codex authentication](https://developers.openai.com/codex/auth/) → [актуальный URL](https://learn.chatgpt.com/docs/auth) | ChatGPT subscription и API usage-based — разные способы входа; login status; forced_login_method. Локальный codex --help подтверждает -m, -c, exec, read-only. |
| S05 | [Claude Code authentication](https://code.claude.com/docs/en/authentication) | Подписка claude.ai, Console и cloud — разные пути. Нельзя считать API key подпиской. |
| S06 | [Claude Code CLI reference](https://code.claude.com/docs/en/cli-reference) | Через Context7: --model и --effort существуют, допустимость effort зависит от модели/версии. |
| S07 | [GLM Coding Plan](https://docs.z.ai/devpack/overview) | Подписка применяется к ZCode и другим агентам; GLM-5.3 / Flash, лимиты; сервер может переназначать старые ID новым. Не доказан автономный CLI ZCode вне приложения. |
| S08 | [DeepSeek Harness](https://github.com/deepseek-ai/deepseek-harness) | Официальный DeepSeek AI harness dsh, Cordis, developer preview, MIT, команда web. Подписочный inference и headless contract в выбранном источнике не подтверждены. |
| S09 | [Kimi CLI source/docs](https://github.com/moonshotai/kimi-cli) | Через Context7: docs/en/reference/slash-commands.md — Kimi Code OAuth и other-platform API login; docs/en/configuration/providers.md — coding endpoint; src/kimi_cli/llm.py — KIMI_API_KEY override. |
| S10 | [Qwen Code](https://github.com/QwenLM/qwen-code) | Qwen Code host, Qwen model отдельно; /auth Qwen Account with Subscription (Plus/Pro) либо API key; qwen -p, subagents; preview 1.0 отдельно от main. |
| S11 | [Gemini CLI source/docs](https://github.com/google-gemini/gemini-cli) | Context7: packages/core/src/core/contentGenerator.ts — auth precedence; docs/resources/quota-and-pricing.md — login/API/Vertex раздельно; docs/cli/enterprise.md — enforcedType oauth-personal и ошибка в noninteractive при несовпадении. |
| S12 | [Introducing Google Antigravity 2.0](https://antigravity.google/blog/introducing-google-antigravity-2) | 19 May 2026: отдельное desktop-приложение, не просто версия IDE; dynamic subagents, JSON hooks, отдельный CLI. |
| S13 | [Antigravity releases](https://antigravity.google/releases) | Страница разделяет App, CLI, IDE; на дату чтения App 2.28.0. Не значит, что эта версия установлена у пользователя. |
| S14 | [Google Codelab](https://codelabs.developers.google.com/getting-started-google-antigravity) | Google AI Pro/Ultra влияют на quota; различает App 2.0 и IDE. Конкретный остаток, overage и усилие не подтверждены. |
| S15 | [Cursor models and pricing](https://cursor.com/docs/models-and-pricing) | Новые планы/legacy различаются; Composer/Grok имеют included pool; third-party модели и Auto могут иметь отдельную оплату. Доступ в аккаунте не проверен. |

Context7 использован: resolve Gemini CLI → /google-gemini/gemini-cli; Kimi CLI → /moonshotai/kimi-cli; Claude Code → /websites/code_claude; затем query-docs. Для остальных применены первичные документы; версия установленного CLI и точные flags не установлены, поэтому автоматизация manual/unknown, а не выдуманный контракт.

Ошибки доступа: web open короткой ссылки Ben AI недоступен; urllib получил HTTP 403. curl -L успешно прочитал landing HTML; обнаружена форма email, доступ к оригинальному ZIP не получен. Отсутствие найденного GitHub оригинала не доказывает, что его нет. Инструкции install/start/login в upstream — данные, не выполнены.

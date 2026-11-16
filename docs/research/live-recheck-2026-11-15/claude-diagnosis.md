# Диагностика Claude plugin test

Дата: 2026-11-15T18:09:12.265417+00:00. Установленный Claude: 2.1.311. SHA256 бинарника: `85d8c08707fcfd1e78ffa0204eefbc6ec9e4fe22f375fb2767ef85d957ed3161`.

## Вывод

Причина воспроизводится как несовместимость очищенного окружения дочернего `plugin test` и маркера повторного входа корпоративного launcher. Это не доказательство того, что mod test сам требует отдельной ОС-песочницы. Поддерживаемая публичная опция отключения второй песочницы для `plugin test` не найдена. Ничего в защите не изменено.

## Доказательства

1. `claude.json`: штатный wrapper успешно выполняет strict validate; native test получает CLI exit 1, дочерний exit 71, `sandbox_apply: Operation not permitted`; тестовый файл не загрузился.
2. `/Users/shalioner/VPS-AI-Proxy/claude-process-launcher.py:26–44`: проверяет транспорт и `inherited_sandbox()`; при True делает exec непосредственно в уже защищённом процессе, иначе применяет `sandbox-exec -f proxy-only.sb`.
3. `tailscale_identity.py:43–55`: без env-маркера возвращает False до системной проверки; при наличии маркера проверяет `sandbox_check(pid, network-outbound, 0) == 1`. `transport_identity.py` импортирует эту функцию.
4. Статическое чтение встроенного JS установленного бинарника (не запуск и не модификация): функция `gp` использует self-invocation `ju({pinToCurrentBinary:!0})` и запускает дочерний `plugin test --file` с `env:{...pi(mi(Do())),...xr()}, extendEnv:!1`. `pi` оставляет лишь список `jt`: PATH/HOME/temp/locale/color/user config и `i5s`; `i5s` состоит из перечисленных Claude/telemetry переменных и списка provider-переменных `jfe`. Пользовательский marker launcher отсутствует. Локаторы в бинарнике: gp около byte 238744523; i5s около 225844647. Внутренние имена/смещения относятся только к этому SHA256, не являются публичным API.
5. Read-only probe через существующий защищённый launcher и `/usr/bin/python3`, timeout 15 s: внутри outer sandbox `marker_present=True`, `inherited_sandbox=True`, exit 0. Гипотеза о неправильном результате OS-проверки при обычном наследовании опровергнута.
6. Второй read-only probe: из уже защищённого Python запущен тот же launcher с копией env, из которой удалён только marker, затем `/usr/bin/python3 -c 'print(123)'`. Outer exit 0, nested exit 71, тот же `sandbox-exec: sandbox_apply: Operation not permitted`; целевой Python не стартовал. Это воспроизводит причину без Claude inference, без повторения plugin test и без изменения сохранённых настроек. Удаление маркера в дочерней копии env не снимало sandbox: оно заставило wrapper попытаться применить её повторно.

Сопоставление статического пути plugin test и изолированного воспроизведения даёт сильное основание для причины. Прямая трассировка окружения конкретного native test child не выполнялась; утверждение о потерянном маркере именно в нём является выводом из кода и совпавшего воспроизведения.

## Официальные документы

Context7: resolve Claude Code → `/websites/code_claude`, три запроса; только публичный вопрос, без локальных адресов и секретов.

- [Plugin CLI reference](https://code.claude.com/docs/en/plugins/cli-reference): `claude plugin test [directory]`, без session/sign-in/network; exit 1 при ошибке тестов. Локальный `plugin test --help` показывает только `-h/--help`. Публичного skip-sandbox или pass-through-env параметра здесь не найдено.
- [Corporate launcher contract](https://code.claude.com/docs/en/corporate-launcher): launcher должен передавать inherited env и выдерживать вложенный вызов; Claude применяет его к nested self-spawn. Публичный механизм launcher поддерживается, но blanket env-cleaning в данном plugin-test child конфликтует с пользовательским marker.
- [Mod reach](https://code.claude.com/docs/en/plugins/mods/overview): mods не покрываются Bash sandbox. Отключение настройки Bash sandbox не устраняет доказанную потерю env-маркера.

## Что необходимо вне проекта

Предпочтительный путь — исправление совместимости native `plugin test` с corporate launcher у поставщика Claude: сохранение требуемого контекста повторного входа либо документированный контракт безопасного sandbox inheritance. Другая область возможного исправления — принадлежащий пользователю VPS launcher, но безопасное распознавание именно доверенной унаследованной политики без env-маркера требует отдельного security review. Проверки «какая-то сеть запрещена» недостаточно для доказательства полного совпадения нужной политики; автоматически снимать требование marker нельзя.

В этой задаче не изменялись wrapper, profile, auth, sandbox/trust или native binary; не использовались undocumented `--file` для обхода runner, прямой native запуск, новые пакеты, API/model calls. Новое исполнение `plugin test` имеет смысл после поддерживаемого исправления совместимости. Node contract harness остаётся отдельной проверкой и не закрывает native runtime gate.

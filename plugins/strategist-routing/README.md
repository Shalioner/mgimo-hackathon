# Strategist routing — Claude Code mod

Автоматически классифицирует новый пользовательский ход и обычный новый
`general-purpose` subagent локальным маршрутизатором. Claude Code получает
реально переписанные поля `model`/`effort` через mods API, а не рекомендацию в промпте.

## Протокол

- `turn.start` передаёт `text` в Python один раз на новый основной ход.
- `turn.step` применяет сохранённый маршрут через `yield* next({...e, model, effort})`.
  До **каждого** запроса Python `--authorize` повторно проверяет полномочия и лимит T4.
  Отказ возвращает локальное сообщение без `next()`, поэтому запрос не отправляется.
  Бросать исключение здесь нельзя: движок пропускает сломанный hook.
- `agent.spawn` меняет `model` у нового `general-purpose`; effort применяется к
  последующим шагам по подтверждённому `agentId` и фактической модели.
  Перед spawn повторно вызывается `--authorize`; в следующих проверках сохраняется
  исходный alias из конфигурации, даже если host вернул разрешённый полный model ID.
  Fork, resume, teammate и именованные специальные роли сохраняются.
- T4 subagent возвращает `deny` до запуска: пока нельзя доказать сопоставление ID
  перед самым первым шагом, квота каждого запроса не гарантирована. Основной T4
  поддерживает повторную авторизацию каждого шага.
- `turn.complete` удаляет маршрут. Карты ограничены 256 элементами; новая задача
  классифицируется заново. Неуверенность/недоступность выбора сохраняет модель host-а
  с сообщением; она не выдаётся за автоматически выбранную модель.

Python argv: `[absolute_python, absolute_helper, '--execute'|'--authorize']`.
JSON stdin выбора: `{host:'claude-code', request, context:'', event:'turn'|'agent.spawn'}`.
Выход выбора: `{status:'selected',model,effort,tier}`. Другие статусы не меняют модель.
JSON stdin авторизации: `{host:'claude-code',model,effort,tier}`;
только `{status:'authorized'}` допускает запрос выбранной модели.
Секреты, API endpoints и подписочные credentials мод не читает и не изменяет.

## Подключение

Плагин использует `.claude-plugin/plugin.json`, `hooks/hooks.json` с `modules`,
ES module `register(on, options)`. Локальный запуск:

```sh
claude --plugin-dir /absolute/repo/plugins/strategist-routing
```

Для автоматической загрузки официально доступен `CLAUDE_CODE_PLUGIN_DIRS`
(абсолютные пути, разделитель `:` на macOS); менять его должен установщик с
сохранением существующих путей. Параметры `python`/`helper` задаются через
`pluginConfigs['strategist-routing@inline']`; defaults указывают на `.venv/bin/python`
и `tools/host_routing.py` того же репозитория. Этот каталог сам ничего не устанавливает.
Изменение текущей сессии задним числом не предполагается: нужен новый запуск host-а.

Установленный при исследовании Claude Code: **2.1.311**. Официальная документация
mods разрешает отсутствие отдельного types-файла с 2.1.299. Доступ/модели аккаунта
и managed policy остаются отдельными ограничениями; live inference не выполнялся.

## Источники и лицензии

Применён подход [Oqura-ai/optim-jev](https://github.com/Oqura-ai/optim-jev), commit
`ad9135aa78bcbf1c247ac15d0dac1a0cabd7686e`, MIT (67 stars на 15.11.2026):
[`hooks/optim-jev.ts`](https://github.com/Oqura-ai/optim-jev/blob/ad9135aa78bcbf1c247ac15d0dac1a0cabd7686e/hooks/optim-jev.ts)
разделяет изменение модели при spawn и effort при step. Исходный код не копировался;
это собственная минимальная реализация поверх официального API. Не перенесены
upstream cloud fallback, изменение правил самой моделью, запись auth и compaction.

Официальные контракты: [mods reference](https://code.claude.com/docs/en/plugins/mods/reference),
[events](https://code.claude.com/docs/en/plugins/mods/events),
[types](https://github.com/anthropics/claude-code/blob/main/mods/types/claude-code.d.ts),
[test kit](https://code.claude.com/docs/en/plugins/mods/test).
Context7 resolve/query использовался для проверки актуального API.

CCR (`musistudio/claude-code-router`) — отдельный gateway, уже исследованный в проекте;
для нативных Claude hooks optim-jev точнее подходит. Публичный исходник/лицензия
Ben AI Model Selector Pack по-прежнему не установлены; его код не переносился.

## Проверка

```sh
node --test plugins/strategist-routing/tests/routing.node.mjs
claude plugin validate --strict --json plugins/strategist-routing
claude plugin test plugins/strategist-routing
```

Node runner проверяет тот же `register` и общие сценарии, включая перезапись
реального аргумента `next`, отказ без model request, T4 и сохранение чужих ролей.
Он не заменяет нативный runtime. В этой среде нативный test runner обнаружил тест,
но не смог запустить свой sandbox (`sandbox_apply: Operation not permitted`, exit71).
Настройки sandbox не ослаблялись. Live загрузка/исполнение под реальным Claude
остаётся отдельным gate после подключения root-установщиком.

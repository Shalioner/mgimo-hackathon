# Decision-модель через OpenRouter

Проверено 16 ноября 2026 в Codex на MacBook Air M1, 8 GiB, macOS 15.7.9.
Модель: `perplexity/pplx-decider-v1.3:free`. Это отдельная проверка API-классификатора:
исполнители задач, Luna и платные резервные модели не запускались.

## Быстрый проверочный запуск

Создайте отдельный ключ OpenRouter с **лимитом $0**, без сброса лимита, с ограниченным
сроком действия. В этом прогоне выбран один день. Сохраните его вне репозитория
в файл с правами `600` либо передайте через `OPENROUTER_API_KEY`. Не вставляйте ключ
в чат, командную строку, Git или отчёт.

```bash
.venv/bin/python tools/openrouter_decision_smoke.py \
  --execute \
  --key-file "$HOME/.config/strategist/secrets/openrouter-test-key.txt" \
  --output /tmp/strategist-openrouter-smoke.json
```

Имя отчёта должно быть новым. Без `--execute` сеть не вызывается. Скрипт проверяет
лимит ключа через `GET /api/v1/key`, затем отправляет один синтетический запрос с
вопросами `noul`, `choice`, `score`. Он не меняет `routing.local.yaml` или глобальные
настройки. При ошибке нет повторного запроса, смены модели или исполнителя.

## Точный grant для бесплатного API

Добавьте в игнорируемый `knowledge/routing.local.yaml` вместе с провайдером из
[настройки каскада](CLASSIFIER_CASCADE.md). Поля дополняют конфигурацию проекта;
не заменяйте ими allowlist хостов. Это явное разрешение отправлять классифицируемый
текст указанному API. `confirmed_by_operator` ставится после настройки лимита ключа.

```yaml
execution_policy:
  mode: api_opt_in
  max_input_bytes: 32000
  api:
    enabled: true
    grants:
      - host: decision
        provider: openrouter
        endpoint: https://openrouter.ai/api/alpha/decisions
        models: ["perplexity/pplx-decider-v1.3:free"]
        efforts: [null]
        budget_usd: 0
        external_cap:
          confirmed_by_operator: true
          amount_usd: 0
          evidence_ref: runtime-verified-GET-/api/v1/key
        context:
          model_limit: 262144
          plan_limit: 32000
          user_limit: 24000
          output_reserve: 1024
          system_reserve: 2048
```

Этот grant соответствует `configuration()` проверочного скрипта. Ключ, абсолютный
путь к нему и настройки подписки пользователь задаёт сам; они не входят в Git.

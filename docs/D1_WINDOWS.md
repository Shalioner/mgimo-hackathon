# D1 на Windows и Linux: llama.cpp + официальный GGUF

MLX-путь ([LOCAL_DECISION_MLX.md](LOCAL_DECISION_MLX.md), [D1_SERVICE.md](D1_SERVICE.md)) работает только
на macOS Apple Silicon. На Windows/Linux та же модель решений LiquidAI d1-2B запускается через
llama.cpp из официального репозитория **LiquidAI/d1-2B-GGUF**. Сервер отдаёт протокол System One
(`POST /v1/systemone`), который `tools/decide.py` уже понимает, — отдельного адаптера не нужно.

Проверено 16.11.2026: Windows 11, i9-10900K, 32 ГБ RAM, RTX 4060 Ti 8 ГБ, драйвер 610.88.

## Установка (≈2,3 ГБ, только публичные файлы)

| Файл | Источник | SHA-256 |
|---|---|---|
| `d1-2B-Q8_0.gguf` (1 846 240 576 B) | [LiquidAI/d1-2B-GGUF @ 4a18109](https://huggingface.co/LiquidAI/d1-2B-GGUF/tree/4a181094d1b6addfaa3a0956f78e7ab2aa406a0c) | `073313d92b53b0ac8193db6e906cb3c78a98183d244b15b7b8f5426115c6738c` |
| `mmproj-d1-2B-Q8_0.gguf` (203 031 584 B) | там же (нужен только для изображений) | `62cc6d74fd1e5b1d6b48518566bf4c0cfd2468079a0c096ea323b74eed01e0d1` |
| `llama-b12433-bin-win-cuda-13.5-x64.zip` | [ggml-org/llama.cpp b12433](https://github.com/ggml-org/llama.cpp/releases/tag/b12433) | `e48b162946143a4a76dbdff04a19de98a6af29c85fedaadea1d86f87aa141a34` |
| `cudart-llama-bin-win-cuda-13.5-x64.zip` | там же | `c2bf4cc202bfbaaa7a0b75f5082457c3811326947bb216059b72839d844e9cba` |

Без NVIDIA — `llama-b12433-bin-win-cpu-x64.zip` или `-vulkan-x64.zip`; d1 поддерживается в llama.cpp
начиная с b11960. Q8_0 — аналог MLX 8bit. `d1-2B-Q4_K_M.gguf` (1 099 629 888 B, SHA-256
`adca5789ad30ae6027e8e936749bb9f19673097492ab26fa1c5b69643ca50b4b`) на тех же 20 задачах и фильтре
внедрений дал идентичные результаты при ≈2,4 ГБ VRAM вместо ≈3,7 ГБ — подходит для GPU 4–6 ГБ.

```powershell
$D = "$env:USERPROFILE\.cache\strategist\d1-2b-gguf"
# скачать файлы из таблицы в $D\weights и $D\llama, сверить Get-FileHash -Algorithm SHA256, распаковать zip в $D\llama\bin
& "$D\llama\bin\llama-server.exe" -m "$D\weights\d1-2B-Q8_0.gguf" --mmproj "$D\weights\mmproj-d1-2B-Q8_0.gguf" `
    --alias d1-2B-Q8_0 -b 8192 -ub 8192 -ngl 99 --host 127.0.0.1 --port 8089
```

`--alias` обязателен: без него сервер называет модель путём к файлу, и проверка квитанции
(`routing_policy.observe`) правильно помечает ответ как расхождение («Response metadata mismatch»).
`--host 127.0.0.1` — сервер не должен слушать сеть.

## Подключение к агенту

В `knowledge/routing.local.yaml` (не в git) — явное разрешение оператора. `local: true` допускается
только для host `decision` и только на loopback-адресе: без ключа, без бюджета, с теми же точными
model/endpoint/effort и границами контекста.

```yaml
execution_policy:
  mode: api_opt_in
  max_input_bytes: 128000
  api:
    enabled: true
    grants:
      - host: decision
        provider: llamacpp-d1
        endpoint: http://127.0.0.1:8089/v1/systemone
        models: [d1-2B-Q8_0]
        efforts: [null]
        local: true
        context: {model_limit: 8192, plan_limit: 8192, user_limit: 8192, output_reserve: 1, system_reserve: 512}
decision:
  providers:
    - {name: llamacpp-d1, protocol: systemone, base_url: "http://127.0.0.1:8089/v1", model: d1-2B-Q8_0}
```

Проверка:

```powershell
.venv\Scripts\python.exe tools\decide.py --project projects\<case> --provider llamacpp-d1 `
    --questions knowledge\decisions\route_tier.yaml --state "Сравни три стратегии запуска продукта и разреши противоречия источников." --step 1
```

Факт: `tier: T3 (0.97)`, 0,4 с вместе с запуском Python; в `run_log.md` — `decision:d1-2B-Q8_0`.
MLX на M1 на том же запросе: T3 0,968.

## Качество и скорость против MLX (20 задач маршрутизации)

`evals/routing/d1_gguf_parity.py`, те же задачи и вопросы, что в
[d1-routing-quality](research/d1-routing-quality-2026-11-15/README.md), порог 0,75, 3 повтора.
Сырые данные: [parity.json](research/d1-gguf-windows-2026-11-16/parity.json).

| | MLX 8bit, M1 8 ГБ | GGUF Q8_0, RTX 4060 Ti |
|---|---:|---:|
| tier совпал с эталоном | 17/20 | 17/20 |
| tier+effort совпали | 17/20 | 17/20 |
| уверенно выбрано при 0,75 | 8/20 | 12/20 |
| ошибок среди уверенных | 0/8 | **2/12** |
| медиана инференса | 2,10 с | **0,089 с** |
| холодный старт | 14,6 с | 1,5–3,8 с (до `listening`, два запуска) |
| ответы между 3 повторами | — | без изменений |

Совпадение выбора tier с MLX — 16/20; при одинаковом выборе медианный сдвиг уверенности 0,06,
максимум 0,42. GGUF исправил две ошибки MLX (Q06, Q16 → верный tier), но **уверенно понизил оба
T4-кейса до T3** (Q19 0,876, Q20 0,889). Для решения о дорогом уровне это риск: порог 0,75 из MLX
на GGUF переносить нельзя без своей калибровки. T4 по-прежнему требует согласия человека,
поэтому ошибка не запускает дорогую модель, а наоборот — недоподнимает задачу.

## Ограничения

- Один компьютер, один GPU; CPU-сборка и Linux не проверялись.
- Набор из 20 синтетических задач — инженерная рубрика, не человеческая разметка.
- Лицензия весов — LFM Open License v1.0 (ограничение коммерческого использования для компаний
  с выручкой > $10 млн), веса в git не входят.
- Сетевой sandbox, как у MLX-воркера, здесь не применяется: изоляция — только `--host 127.0.0.1`
  и то, что llama.cpp не исполняет custom Python из репозитория модели.

# Локальная лаборатория LiquidAI d1-2B

Отдельный последующий путь MLX 8bit и его фактическая проверка описаны в
[LOCAL_DECISION_MLX.md](LOCAL_DECISION_MLX.md): text + image smoke на M1 успешно пройден.
Ниже — исходный bf16 runtime и история его memory blocker.

Optional CLI: `tools/local_decision.py`. Он не подключён к автоматическому router,
не меняет cloud grants, не использует API-ключи и не делает cloud/CPU fallback.
Назначение — короткие локальные исследования System One на этом Mac, не production service.
Основной `.venv`, `projects/`, глобальные CLI-конфиги и облачная авторизация не изменяются.

**Текущий результат: runtime установлен, но реальный inference BLOCKED памятью.**
На M1 8 GiB/macOS 15.7.9 загрузка официальных весов прошла, однако до первого ответа
ОС достигла critical memory pressure. Ни одного фактического probability-ответа не получено;
примеры ниже — готовые команды повторного запуска, не обещание успешной работы при текущей
нагрузке. Модельные процессы остановлены, ограничения MPS не повышались, приложения не закрывались.

## Запуск

Из корня этого репозитория, отдельным Python лаборатории:

```bash
~/.cache/strategist/d1-2b-lab/venv/bin/python tools/local_decision.py run \
  --questions knowledge/decisions/route_tier.yaml \
  --state 'Сравни три стратегии запуска продукта, разреши противоречия источников и обоснуй выбор.'

~/.cache/strategist/d1-2b-lab/venv/bin/python tools/local_decision.py run \
  --questions docs/examples/d1-image-questions.yaml \
  --state 'Какого цвета квадрат?' \
  --image ~/.cache/strategist/d1-2b-lab/synthetic-red-square.png
```

Вместо `--state` доступны `--state-file <локальный UTF-8 файл>` и
`--state-json '{"запрос":"..."}'`. Без state допускается одно изображение.
Questions — YAML/JSON с `questions: {...}` либо непосредственно словарь вопросов.
Поддержаны `noul`, `choice` (включая multiple), `score`; другие типы модели пока
не выставлены этим узким wrapper. Ответы и probabilities — фактический System One JSON,
не сгенерированный пересказ. `--output <путь>` сохраняет полный JSON с временем и памятью.
Exit 0 означает успешное выполнение, exit 3 — blocked с причиной, без fallback.

Smoke: три коротких русских задачи на готовом `route_tier.yaml`, затем синтетическое
изображение с красным квадратом; один экземпляр модели, последовательные вызовы:

```bash
~/.cache/strategist/d1-2b-lab/venv/bin/python tools/local_decision.py smoke \
  --output ~/.cache/strategist/d1-2b-lab/smoke.json
```

Процесс заканчивается после ответа; фонового сервера нет. Lock лаборатории исключает
одновременный запуск нескольких экземпляров через этот CLI. Stop только своего процесса:
Ctrl-C; wrapper завершает дочерний процесс в finally. Таймаут по умолчанию 240 секунд,
допустимо 1–300 (`--timeout`). RSS выше 6 GiB, рост системного swap более 3 GiB,
общий swap выше 8 GiB либо critical pressure ОС останавливают процесс;
swap общий, поэтому причиной могут быть и другие приложения.
Это guard с периодом 5 секунд, не жёсткая гарантия общего потребления ОС.

## Установка и pin

Лаборатория: `~/.cache/strategist/d1-2b-lab`.
Python: `venv/bin/python`, Python 3.12; зависимости зафиксированы в отдельном
`requirements-local-decision.txt`. Веса и custom code:
`weights/a6bce774e6dde338dcc3036ceb5d91f78bf4c19e/`.

Для воспроизведения в отдельной лаборатории (не в основном `.venv`):

```bash
python3.12 -m venv ~/.cache/strategist/d1-2b-lab/venv
~/.cache/strategist/d1-2b-lab/venv/bin/python -m pip install -r requirements-local-decision.txt
~/.cache/strategist/d1-2b-lab/venv/bin/python tools/local_decision.py download
```

Только `download` обращается в сеть — за публичными файлами официального
[LiquidAI/d1-2B](https://huggingface.co/LiquidAI/d1-2B/tree/a6bce774e6dde338dcc3036ceb5d91f78bf4c19e).
Ревизия неизменяемая: `a6bce774e6dde338dcc3036ceb5d91f78bf4c19e`.
Размер `model.safetensors`: 3 836 417 920 bytes; SHA-256 из LFS metadata:
`b5e573687315e82ea80f4f7dd8d76b5cec3b6ee97e3509fd0493cfebb203719f`.
Существующие несовпадающие файлы/незавершённые загрузки не перезаписываются автоматически.

Перед `trust_remote_code` проверяются SHA-256 всех файлов из
`tools/local_decision_manifest.json`, включая custom Python, config, tokenizer,
calibration и safetensors. Неизвестные дополнительные файлы отклоняются.
Статический обзор pinned source проверил import graph, Auto registrations, model/runner,
media loading, projection fusion и attention/convolution code: собственных shell/process
или socket/HTTP вызовов не найдено; вспомогательная загрузка tokenizer/processor использует
Transformers cached-file/from_pretrained. Это ограниченный source review, не формальная
гарантия безопасности произвольного обновления; новая ревизия требует отдельного review.

Inference запускается под macOS `sandbox-exec` с `deny network*`, в отдельном cwd и
минимальном env без API-ключей. HF offline и `local_files_only=True` дополняют запрет сети.
Проверено, что пробный socket connect из этого sandbox получает PermissionError.
Динамический HF module cache создаётся отдельно на каждый запуск и удаляется после него.
Веса читаются локально с `revision`, `code_revision`, `use_safetensors=True`, bf16,
`device_map="mps"`; accelerate нужен для размещения без полной fp32 копии на CPU.
Никаких изменений MPS memory watermarks, torch.compile или CUDA kernels.
После первой остановки отключена необязательная `fuse_projections` в runner: сохраняются
исходные отдельные linear layers и те же веса, без временного torch.cat проекций.
Это локальная настройка wrapper, файлы official custom code не изменены; численная parity
не измерена, так как реальный ответ не получен. Отключение fusion не решило дефицит памяти.

Вход ограничен 8192 UTF-8 bytes state и 8192 bytes questions, 1–8 вопросов,
2–10 вариантов/уровней; после tokenizer — до 1024 текстовых токенов перед шаблоном.
Изображение — только локальный файл до 8 MiB/16M исходных pixels, thumbnail до 384×384
с сохранением пропорций; исходный и фактический размеры записываются. Видео/аудио/URL
не поддержаны wrapper. Это существенно меньше рекламируемого контекста модели и намеренно
соответствует 8 GiB RAM; качества на больших документах эти проверки не доказывают.

## Лицензия и границы

[LFM Open License v1.0, pinned LICENSE](https://huggingface.co/LiquidAI/d1-2B/blob/a6bce774e6dde338dcc3036ceb5d91f78bf4c19e/LICENSE)
не является Apache/MIT-лицензией модели. В ней есть ограничение коммерческого использования
для Legal Entity выше указанного порога годовой выручки $10 млн; для такого использования
эта лицензия не предоставляет коммерческие права. Лицензия сохранена вместе с весами;
они не включены в Git и не распространяются этим commit. Текущий запуск — локальный
исследовательский smoke на синтетических задачах, не разрешение на коммерческое внедрение.

Вероятности — выход модели с её calibration, а не доказанная калибровка на решениях Стратега.
Три ожидаемых tier — контрольные примеры, не benchmark или оценка спроса/стратегии.
Результаты не дают разрешения выполнять маршруты и не отменяют T4/max/оплатные gates.
Native Windows/Linux runtime в этом optional CLI не заявлен.

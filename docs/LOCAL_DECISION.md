# Локальная лаборатория LiquidAI d1-2B

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

## Фактическая проверка 2026-11-15

Официальный HF API вернул публичную доступную модель и закреплённую ревизию; скачивание
3 836 417 920 bytes завершено за 329 секунд, LFS SHA-256 совпал. Python 3.12.13,
Transformers 5.19.0, Torch 2.15.0, torchvision 0.29.2, Pillow 13.0.0, accelerate 1.15.0.
MPS available=true; отдельный bf16 matmul 2×2 дал `[[2,2],[2,2]]`;
`recommended_max_memory=5726633984` bytes. Это probe backend, не успешный запуск модели.

| Попытка | Время | Swap до → после, MiB | Причина остановки | Ответы |
|---|---:|---:|---|---|
| Первоначальный smoke | 15.377 s | 3476.19 → 6141.62 | Тогдашний консервативный guard роста swap 2 GiB | 0 |
| Без projection fusion | 15.318 s | 4543.56 → 6624.38 | Тот же guard 2 GiB; веса загрузились, MPS driver 3845242880 bytes | 0 |
| Уточнённый guard 3 GiB + pressure | 15.226 s | 4238.88 → 6236.19 | pressure=4 на 15.1 s, процесс завершён сигналом 9 | 0 |

Третья попытка остановилась именно по системному critical pressure, а не timeout/лимиту MPS.
После неё не запускались новые модельные процессы. Краткая `resource_tracker` warning о
semaphore при принудительном завершении сохранена в сыром отчёте; оставшихся worker/resource_tracker
процессов в проверке `ps` не обнаружено. Нет чисел accuracy или выдуманных вероятностей.
Ожидаемые T1/T2/T3 и red — только эталон будущего smoke, не предсказания модели.

Сырые JSON и версии: [папка доказательств](research/local-decision-2026-11-15/).
Локальные оригиналы: `~/.cache/strategist/d1-2b-lab/smoke-memory-stop.json`,
`smoke-unfused.json`, `smoke-final.json`; синтетическая картинка —
`synthetic-red-square.png`. `smoke.json` содержит первую попытку и не является успешным результатом.
Полный benchmark не запускался. Для завершения приёмки нужен успешный повтор короткого smoke
при достаточной доступной RAM; runtime на данном занятом 8 GiB Mac остаётся blocked.

Проверки кода: `.venv/bin/python -m pytest -q tests/test_local_decision.py` — 26 passed;
`.venv/bin/python -m pytest -q tests` — 478 passed, 1 skipped (необязательный openpyxl),
40.99 s. `git diff --check` — без ошибок. Повторная локальная проверка всех SHA-256
snapshot прошла без загрузки модели. Fake tests проверяют parser, distributions,
input limits, integrity, network-denied child env и остановку только собственного
процесса по RSS/swap/critical pressure; они не заменяют провалившийся real smoke.

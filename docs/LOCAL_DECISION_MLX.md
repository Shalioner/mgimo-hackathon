# d1-2B: изолированный MLX 8bit runtime

**Статус: установлен, реальный smoke BLOCKED системным memory pressure;
работающий inference на этом занятом M1 8 GiB не подтверждён.**

`tools/local_decision_mlx.py` использует официальный quantized **d1-2B**, его System One
и calibration, не генеративный LFM3. Автоматический router пока этот CLI не вызывает.
Cloud/API gates и defaults не изменены; fallback отсутствует.

## Запросы на этом Mac

Из корня репозитория:

```sh
~/.cache/strategist/d1-2b-lab/mlx-venv/bin/python tools/local_decision_mlx.py run \
  --state 'Сравни три стратегии запуска продукта и разреши противоречия источников.' \
  --questions knowledge/decisions/route_tier.yaml

~/.cache/strategist/d1-2b-lab/mlx-venv/bin/python tools/local_decision_mlx.py run \
  --state 'Какого цвета квадрат?' --questions docs/examples/d1-image-questions.yaml \
  --image ~/.cache/strategist/d1-2b-lab/synthetic-red-square.png
```

Также `--state-json`, `--state-file`, `--output`, `--timeout` (1–300 s).
Exit 0 — ответ, exit 3 — blocked. Новый запрос локален, subprocess имеет OS network deny,
HF offline и минимальное окружение без API-ключей. Процесс завершается после результата;
сервис не остаётся в памяти. Исходные веса bf16 и отдельный прежний venv сохранены.

## Воспроизводимость и источники

- [Официальная карточка 8bit, pinned](https://huggingface.co/LiquidAI/d1-2B-MLX-8bit/blob/3247d0483b11e5da31fa10e7979c23252fbe3580/README.md).
- Repo `LiquidAI/d1-2B-MLX-8bit`, revision `3247d0483b11e5da31fa10e7979c23252fbe3580`.
- Веса `~/.cache/strategist/d1-2b-lab/mlx-weights/3247d0483b11e5da31fa10e7979c23252fbe3580`.
- Python `~/.cache/strategist/d1-2b-lab/mlx-venv/bin/python` (3.12.13).
- Все зависимости отдельного venv: `requirements-local-decision-mlx.txt`; основная `.venv` не менялась.
- `mlx-vlm 0.7.11`, `mlx 0.32.4`, Transformers `5.23.0`.
- Wheel `mlx_vlm-0.7.11-py3-none-any.whl` SHA256
  `927df3387cc1dd4e4e6158696dfb97abf2791ea67f6c77077213bb25c343ba0e`,
  сверено с [PyPI release metadata](https://pypi.org/pypi/mlx-vlm/0.7.11/json).
- SHA256 каждого файла snapshot проверяются перед загрузкой по
  `tools/local_decision_mlx_manifest.json`; LFS hash весов сверялся при скачивании.

Проверены исходники wheel: `utils.py` распознаёт `architectures: D1Model` и выбирает
`d1_lfm3_vl`; `d1.py` использует set attention и чтение option rows tied head,
`attach_processor` загружает `calibration.json`; `calibration.py` применяет temperatures/tables;
`media.py` кодирует PIL image через соответствующий vision tower и projector.
`api.py` задаёт choice argmax, independent multiple >=0.5 и score median по умолчанию,
confidence для score — максимальная вероятность (не вероятность медианной категории).
Именно этот контракт проверяет валидатор; abstention override wrapper не выставляет.
`load(... lazy=False, strict=True, local_files_only=True, trust_remote_code=False)`
использует установленный адаптер и локальный NumPy processor, зарегистрированный MLX
при импорте d1 backbone; generic Transformers AutoProcessor без этой регистрации
на данном venv не работал (нет соответствующего image processor без torch).
Отдельная проверка зарегистрированного processor прошла без загрузки модели;
HF custom Python не исполняется.
Файлы модели не редактировались. Source inspection не является полным аудитом зависимостей.

## Память, качество и лицензия

Прежний bf16 peak локализован до первого текстового ответа, а не в vision: image был
четвёртым случаем. Во втором отчёте загрузка дошла до MPS driver 3 845 242 880 bytes;
в третьем critical pressure наступил до сообщения о завершённой загрузке.
Пятиисекундных samples недостаточно, чтобы отделить materialization/copies от первого
forward во второй попытке. Transformers allocator warmup пропускает MPS; возможные
CPU file pages плюс MPS buffers — гипотеза, не измеренная причина. Повтор bf16 не запускался.

MLX8bit уменьшает веса до 2 216 067 578 bytes; language — affine 8bit/group64,
vision/projector/norms — FP16. Это изменение численного представления; побитовая
эквивалентность bf16 не обещается. По официальной карточке, 52/52 тестовых ответов
сохранились, max probability drift 0.008 относительно original FP32. Это данные автора,
не наша оценка русских задач и не доказательство доменной калибровки.

[Официальный 4bit](https://huggingface.co/LiquidAI/d1-2B-MLX-4bit/blob/afeb4a3141bf4040cbc312d0ccc14eac719024c5/README.md)
скачан в лабораторию до уточнения приоритета 8bit, но inference не запускался:
по карточке 3/52 ответов изменились, drift до 0.25. Для вероятностных решений 8bit предпочтительнее.
[GGUF](https://huggingface.co/LiquidAI/d1-2B-GGUF/blob/4a181094d1b6addfaa3a0956f78e7ab2aa406a0c/README.md)
официально поддерживает `/v1/systemone` в llama.cpp b11960/v0.8.0+, включая images и
calibration для noul/choice/score; локально этот runtime не устанавливался и не проверялся.
Generic torch quantization без подтверждённой поддержки d1 не применялась.

Супервизор тот же: RSS 6 GiB, swap growth 3 GiB, total swap 8 GiB, critical pressure>=4,
проверка каждые 5 s, timeout до300 s. MPS/MLX memory/wired limits не изменяются.
Вход до8192 bytes state/questions, 1–8 questions, raw text до1024 tokens,
дополнительно actual packed row до2048 tokens перед language forward.
Локальная картинка <=8 MiB/16M pixels, thumbnail <=384×384; audio/video/URLs не выставлены.

[LFM Open License v1.0](https://huggingface.co/LiquidAI/d1-2B-MLX-8bit/blob/3247d0483b11e5da31fa10e7979c23252fbe3580/LICENSE)
сохранена с весами. Коммерческое использование Legal Entity выше указанного в ней
порога $10M annual revenue ею не лицензируется. Smoke — исследовательский,
не разрешение коммерческого внедрения. MLX-VLM package MIT, но адаптированные d1
модули отдельно помечены LFM Open License; wheel ссылается на LICENSE в папке d1,
которого в wheel нет — полный текст доступен в закреплённом snapshot модели.
Модель/чужие исходники в Git не распространяются.

Повторная установка в **новую отдельную** lab venv (для текущего runtime уже сделана):

```sh
python3.12 -m venv ~/.cache/strategist/d1-2b-lab/mlx-venv
~/.cache/strategist/d1-2b-lab/mlx-venv/bin/python -m pip install -r requirements-local-decision-mlx.txt
~/.cache/strategist/d1-2b-lab/mlx-venv/bin/python tools/local_decision_mlx.py download
```

`download` — явная сетевая операция только для закреплённых официальных артефактов;
`run` ничего не скачивает. Изменённые файлы и незавершённые загрузки автоматически
не перезаписываются. Конкретный размер памяти зависит от открытых приложений;
успешный короткий smoke не гарантирует выполнение на любом занятом Mac.


## Реальная проверка 2026-11-15

Команда (единственная попытка MLX inference в этой работе):

```sh
~/.cache/strategist/d1-2b-lab/mlx-venv/bin/python tools/local_decision_mlx.py smoke \
  --timeout 240 --output docs/research/local-decision-2026-11-15/mlx8-smoke.json
```

[Сырой JSON](research/local-decision-2026-11-15/mlx8-smoke.json): exit3, worker signal9,
`process_finished=true`, 5.557 s. До запуска pressure2, swap3517.62 MiB;
на5.4 s pressure4, swap4029.19 MiB, worker RSS177952 KiB (173.78 MiB).
После остановки swap4070.38 MiB, pressure2; последующая проверка pressure1,
оставшихся `_worker`/resource_tracker процессов нет. RSS/swap guards не превышены;
сработал именно **системный critical pressure**. Никакие приложения не закрывались.

Нет probability JSON от модели, нет успешного text, image не запускался.
Сообщения о завершённой загрузке весов тоже нет, stderr пустой; loader/forward
не измерены отдельно. Малый process RSS не доказывает размер Metal allocations,
а системное давление может зависеть от других процессов. Поэтому причину нельзя
сужать до «8bit веса не помещаются» или «пик vision»: доказан лишь pressure blocker
до первого ответа. Нового bf16/4bit/8bit inference после остановки не было.

Проверки: 36 scoped offline tests passed; полный `pytest -q tests` —
488 passed, 1 skipped за42.56 s; `git diff --check` чистый.
Веса проверены по pinned manifest/LFS, зарегистрированный processor загружен отдельно
без модели, adapter source inspected. Это не заменяет отсутствующий real smoke.

| Возможность | Фактическая готовность |
|---|---|
| Official d18bit + calibration + image implementation | Подтверждены карточкой и source wheel |
| Lab venv, weights, CLI, offline process supervision | Установлены; offline/fake проверки прошли |
| Text probabilities на текущем M1 | BLOCKED pressure, 0 ответов |
| Image inference | Не запускался: text gate не пройден |
| Несколько русских задач/доменная calibration | Не проверены, ограничение этой итерации — один text smoke |
| Автоматический router/host triggers | Не подключены; gates не изменены |
| 4bit/GGUF inference | Не проверены; 4bit веса сохранены, GGUF не установлен |

Дальше возможен новый ограниченный прогон после освобождения памяти пользователем
или на машине с большим запасом RAM, с теми же guards и дополнительными фазовыми
метками. Это потребует нового бюджета попыток, а не снятия ограничений.
4bit — официальный меньший вариант, но с существенно большим заявленным drift;
автоматически переходить на него для probability routing нельзя. GGUF поддерживает
System One, но его преимущества по памяти здесь ещё не измерены. 600M и generative
модели заменой пользовательской d1-2B не считаются.

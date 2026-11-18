# d1-2B: изолированный MLX 8bit runtime

Для обычной работы используется [локальный сервис D1](D1_SERVICE.md) с повторным использованием модели.

`tools/local_decision_mlx.py` использует официальный quantized **d1-2B**, его System One
и calibration, не генеративный LFM3. Общий router теперь вызывает этот CLI при явном
`automatic_routing.classifier: local-d1`; интеграция описана в [ADAPTIVE_ROUTING.md](ADAPTIVE_ROUTING.md).
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

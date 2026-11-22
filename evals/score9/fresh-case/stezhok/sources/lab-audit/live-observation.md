# Независимое наблюдение локального CLI
СИНТЕТИЧЕСКАЯ УЧЕБНАЯ ЛАБОРАТОРИЯ, не полевой продукт.

## Оригинал
```json
[
  {
    "case": "valid",
    "command": [
      ".venv/bin/python",
      "projects/stezhok/sources/lab-audit/audit.py",
      "--input",
      "projects/stezhok/sources/lab-audit/example.json",
      "--output",
      "projects/stezhok/state/lab-session1/valid-result.md"
    ],
    "exit": 0,
    "stdout": "{\"written\": 1, \"output\": \"projects/stezhok/state/lab-session1/valid-result.md\"}\n",
    "stderr": "",
    "output_exists": true
  },
  {
    "case": "invalid",
    "command": [
      ".venv/bin/python",
      "projects/stezhok/sources/lab-audit/audit.py",
      "--input",
      "projects/stezhok/sources/lab-audit/invalid.json",
      "--output",
      "projects/stezhok/state/lab-session1/invalid-result.md"
    ],
    "exit": 2,
    "stdout": "",
    "stderr": "usage: audit.py [-h] --input INPUT --output OUTPUT\naudit.py: error: four nonempty text fields required\n",
    "output_exists": false
  },
  {
    "case": "false-but-filled",
    "command": [
      ".venv/bin/python",
      "projects/stezhok/sources/lab-audit/audit.py",
      "--input",
      "projects/stezhok/state/lab-session1/false-input.json",
      "--output",
      "projects/stezhok/state/lab-session1/false-result.md"
    ],
    "exit": 0,
    "stdout": "{\"written\": 1, \"output\": \"projects/stezhok/state/lab-session1/false-result.md\"}\n",
    "stderr": "",
    "output_exists": true
  }
]
```

## Проверенный результат
Заполненный пример: exit 0, записана одна карточка Markdown; выход совпадает побайтно с example-result.md.
Неполный пример: exit 2, файл результата отсутствует.
Заведомо недостоверная, но заполненная карточка: exit 0, записана без проверки истины.

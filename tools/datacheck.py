#!/usr/bin/env python3
"""Проверка CSV/XLSX перед извлечением CLM; расчёты локальные, без модели.

Номера строк — физические строки CSV или строки листа XLSX. В period_gap
указана строка после разрыва, в duplicate/period_duplicate — повторные строки.
Единица заголовка применяется к числам без суффикса. Выбросы сравниваются
внутри одной единицы, min/max сохраняют масштаб исходных чисел (12 % → 12).
"""
from __future__ import annotations

import argparse
import calendar
import csv
import io
import json
import math
import re
import statistics
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from xml.etree.ElementTree import ParseError
from zipfile import BadZipFile


MISSING = {"", "na", "n/a", "null", "-", "—"}
DATE_HEADER = re.compile(r"дата|date|месяц|month|период|period|год|year|квартал|quarter", re.I)
NUMBER = re.compile(
    r"^([+-]?(?:(?:\d{1,3}(?:[ \u00a0\u202f]\d{3})+|\d+)(?:[.,]\d+)?|[.,]\d+)"
    r"(?:[eE][+-]?\d+)?)(.*)$"
)
UNIT = re.compile(r"(тыс\.?|млн\.?|млрд\.?)?(руб\.?|₽|\$|%)?", re.I)


def is_missing(value: str) -> bool:
    return value.strip().casefold() in MISSING


def parse_unit(text: str):
    match = UNIT.fullmatch(re.sub(r"\s+", "", text))
    if not match:
        return None
    scale, measure = (part.lower().rstrip(".") if part else "" for part in match.groups())
    return scale, "₽" if measure in {"руб", "₽"} else measure


def unit_label(unit) -> str | None:
    return " ".join(part for part in unit if part) or None


def parse_number(text: str):
    match = NUMBER.fullmatch(text.strip().replace("−", "-"))
    if not match:
        return None
    unit = parse_unit(match[2])
    if unit is None:
        return None
    value = float(re.sub(r"\s+", "", match[1]).replace(",", "."))
    return (value, unit) if math.isfinite(value) else None


@dataclass(frozen=True)
class Period:
    grain: str
    index: int
    start: date
    label: str


def parse_period(text: str, allow_year: bool = False) -> Period | None:
    text = text.strip()
    year = month = day = quarter = None
    grain = ""
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        year, month, day = map(int, text.split("-")); grain = "day"
    elif re.fullmatch(r"\d{2}\.\d{2}\.\d{4}", text):
        day, month, year = map(int, text.split(".")); grain = "day"
    elif re.fullmatch(r"\d{4}-\d{2}", text):
        year, month = map(int, text.split("-")); grain = "month"
    elif re.fullmatch(r"\d{2}\.\d{4}", text):
        month, year = map(int, text.split(".")); grain = "month"
    elif match := re.fullmatch(r"(\d{4})[Qq]([1-4])", text):
        year, quarter = map(int, match.groups()); grain = "quarter"
    elif match := re.fullmatch(r"[Qq]([1-4])\s+(\d{4})", text):
        quarter, year = map(int, match.groups()); grain = "quarter"
    elif allow_year and re.fullmatch(r"\d{4}", text):
        year = int(text); grain = "year"
    else:
        return None
    try:
        start = date(year, (quarter - 1) * 3 + 1 if quarter else month or 1, day or 1)
    except ValueError:
        return None
    if grain == "day":
        return Period(grain, start.toordinal(), start, start.isoformat())
    if grain == "month":
        return Period(grain, year * 12 + month - 1, start, f"{year:04d}-{month:02d}")
    if grain == "quarter":
        return Period(grain, year * 4 + quarter - 1, start, f"{year:04d}Q{quarter}")
    return Period(grain, year, start, f"{year:04d}")


def _cell(value) -> str:
    if isinstance(value, (datetime, date)):
        return value.date().isoformat() if isinstance(value, datetime) else value.isoformat()
    return "" if value is None else str(value)


def read_table(path: Path, sheet: str | None = None, sep: str | None = None):
    if path.suffix.lower() == ".csv":
        if sheet:
            raise ValueError("--sheet применим только к XLSX")
        raw = path.read_bytes()
        try:
            text = raw.decode("utf-8-sig")
        except UnicodeDecodeError:
            text = raw.decode("cp1251")
        if "\x00" in text:
            raise ValueError("CSV содержит нулевые байты; ожидается текст UTF-8 или cp1251")
        if sep is None:
            try:
                sep = csv.Sniffer().sniff(text[:65536], delimiters=",;\t").delimiter
            except csv.Error:
                # Короткие/неполные строки не мешают прочесть заголовок.
                first = text.splitlines()[0] if text else ""
                sep = max(",;\t", key=first.count) if any(c in first for c in ",;\t") else ","
        reader = csv.reader(io.StringIO(text, newline=""), delimiter=sep, strict=True)
        records = []
        while True:
            line = reader.line_num + 1
            row = next(reader, None)
            if row is None:
                break
            if row:
                records.append((line, row))
    elif path.suffix.lower() == ".xlsx":
        if not path.is_file():
            raise ValueError(f"нет файла {path}")
        try:
            import openpyxl
        except ImportError:
            raise ValueError("XLSX не поддержан без openpyxl, сохраните как CSV") from None
        workbook = openpyxl.load_workbook(path, read_only=True, data_only=True)
        try:
            if sheet and sheet not in workbook.sheetnames:
                raise ValueError(f"нет листа «{sheet}»")
            worksheet = workbook[sheet] if sheet else workbook.worksheets[0]
            records = [(i, [_cell(v) for v in row])
                       for i, row in enumerate(worksheet.iter_rows(values_only=True), 1)]
        finally:
            workbook.close()
    else:
        raise ValueError("поддерживаются только CSV и XLSX")
    if not records:
        raise ValueError("файл пуст; нужен заголовок")
    headers = [value.strip() for value in records[0][1]]
    if not all(headers) or len(set(headers)) != len(headers):
        raise ValueError("заголовки столбцов должны быть непустыми и уникальными")
    rows = []
    for line, row in records[1:]:
        if len(row) > len(headers):
            raise ValueError(f"строка {line}: больше ячеек, чем в заголовке; проверьте разделитель")
        rows.append((line, row + [""] * (len(headers) - len(row))))
    return headers, rows


def period_gaps(periods):
    """Шаг — мода положительных интервалов, при равенстве берём меньший.

    Для дат на одном дне месяца или на концах месяцев шаг календарный.
    Две точки не устанавливают регулярность; разрыв привязан к следующей строке.
    """
    by_grain = defaultdict(dict)
    for row, period in periods:
        by_grain[period.grain].setdefault(period.index, (row, period))
    for grain, items in by_grain.items():
        ordered = [items[key] for key in sorted(items)]
        if len(ordered) < 3:
            continue
        dates = [p.start for _, p in ordered]
        calendar_step = grain == "day" and (
            len({d.day for d in dates}) == 1 or
            all(d.day == calendar.monthrange(d.year, d.month)[1] for d in dates)
        ) and len({(d.year, d.month) for d in dates}) == len(dates)
        indices = [p.start.year * 12 + p.start.month - 1 if calendar_step else p.index
                   for _, p in ordered]
        deltas = [b - a for a, b in zip(indices, indices[1:])]
        counts = Counter(deltas)
        step = min(counts, key=lambda delta: (-counts[delta], delta))
        for i, delta in enumerate(deltas):
            if delta > step and delta % step == 0:
                row, after = ordered[i + 1]
                before = ordered[i][1]
                step_unit = "месяц" if calendar_step else {"day": "день", "month": "месяц",
                                                          "quarter": "квартал", "year": "год"}[grain]
                yield row, (f"между {before.label} и {after.label} пропущено периодов: "
                            f"{delta // step - 1}; регулярный шаг {step} "
                            f"({step_unit})")


def check_table(headers, rows) -> dict:
    issues, summary, parsed = [], {}, {}

    def issue(kind, column, lines, detail):
        issues.append({"type": kind, "column": column, "rows": sorted(lines), "detail": detail})

    duplicates = defaultdict(list)
    for line, row in rows:
        duplicates[tuple(row)].append(line)
    for lines in duplicates.values():
        if len(lines) > 1:
            issue("duplicate", None, lines[1:], f"строки полностью повторяют строку {lines[0]}")

    for index, column in enumerate(headers):
        values = [(line, row[index].strip()) for line, row in rows if not is_missing(row[index])]
        missing = [line for line, row in rows if is_missing(row[index])]
        if values and missing:
            issue("missing", column, missing, "пустое значение или маркер пропуска")
        numbers = [(line, value) for line, text in values if (value := parse_number(text)) is not None]
        periods = [(line, value) for line, text in values
                   if (value := parse_period(text, bool(DATE_HEADER.search(column)))) is not None]
        kind = ("date" if periods and len(periods) / len(values) >= .8 else
                "number" if numbers and len(numbers) / len(values) >= .8 else "text")
        entry = {"type": kind, "nonempty": len(values), "unit": None}
        summary[column] = entry
        parsed[column] = periods
        if kind == "date":
            ordered = sorted((p for _, p in periods), key=lambda p: p.start)
            entry.update(first=ordered[0].label, last=ordered[-1].label)
            for line, detail in period_gaps(periods):
                issue("period_gap", column, [line], detail)
            continue
        if not numbers:
            continue
        header_unit = next((u for part in re.findall(r"\(([^()]*)\)", column)
                            if (u := parse_unit(part)) and any(u)), None)
        effective = [(line, value, header_unit if unit == ("", "") and header_unit else unit)
                     for line, (value, unit) in numbers]
        units = Counter(unit for _, _, unit in effective)
        expected = header_unit or units.most_common(1)[0][0]
        wrong = [line for line, _, unit in effective if unit != expected]
        if wrong:
            labels = ", ".join(unit_label(u) or "без единицы" for u in units)
            issue("unit_mismatch", column, wrong,
                  f"единицы: {labels}; ожидается {unit_label(expected) or 'без единицы'}")
        entry["unit"] = "смешанные" if wrong else unit_label(expected)
        if kind == "number":
            entry.update(min=min(n for _, (n, _) in numbers), max=max(n for _, (n, _) in numbers))
            numeric_rows = {line for line, _ in numbers}
            mixed = [line for line, _ in values if line not in numeric_rows]
            if mixed:
                issue("type_mix", column, mixed, "нечисловое значение в числовом столбце (чисел ≥ 80%)")
        groups = defaultdict(list)
        for line, value, unit in effective:
            groups[unit].append((line, value))
        for group in groups.values():
            if len(group) < 8:
                continue
            q1, _, q3 = statistics.quantiles([n for _, n in group], n=4, method="inclusive")
            iqr = q3 - q1
            outliers = [line for line, n in group if n < q1 - 3 * iqr or n > q3 + 3 * iqr]
            if outliers:
                issue("outlier", column, outliers, f"вне [Q1 − 3×IQR; Q3 + 3×IQR], Q1={q1:g}, Q3={q3:g}")

    dimensions = [i for i, col in enumerate(headers) if summary[col]["type"] == "text"]
    row_values = dict(rows)
    for column in headers:
        if summary[column]["type"] != "date":
            continue
        groups = defaultdict(list)
        for line, period in parsed[column]:
            key = (period.grain, period.index, *(row_values[line][i].strip() for i in dimensions))
            groups[key].append(line)
        for lines in groups.values():
            if len(lines) > 1:
                issue("period_duplicate", column, lines[1:],
                      f"повтор периода и текстовых измерений из строки {lines[0]}")
    return {"summary": summary, "issues": issues}


def markdown(result: dict) -> str:
    def escape(value):
        return str(value).replace("|", "\\|").replace("\r", " ").replace("\n", " ")

    lines = ["# Проверка таблицы", "", "| Столбец | Тип | Непустых | Минимум / первый период | Максимум / последний период | Единица |",
             "|---|---|---:|---|---|---|"]
    types = {"number": "число", "date": "дата", "text": "текст"}
    for column, item in result["summary"].items():
        cells = [column, types[item["type"]], item["nonempty"], item.get("min", item.get("first", "—")),
                 item.get("max", item.get("last", "—")), item["unit"] or "—"]
        lines.append("| " + " | ".join(map(escape, cells)) + " |")
    lines += ["", "## Проблемы", ""]
    labels = {"missing": "пропуск", "duplicate": "повтор строки", "unit_mismatch": "разные единицы",
              "period_gap": "пропуск периода", "period_duplicate": "повтор периода",
              "outlier": "выброс", "type_mix": "смешение типов"}
    for item in result["issues"]:
        where = f" в «{escape(item['column'])}»" if item["column"] is not None else ""
        lines.append(f"- строки {', '.join(map(str, item['rows']))}: {labels[item['type']]}{where}; {escape(item['detail'])}")
    if not result["issues"]:
        lines.append("Проблем не найдено.")
    return "\n".join(lines)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Проверить CSV/XLSX перед извлечением утверждений CLM.")
    parser.add_argument("file", type=Path, help="файл CSV или XLSX")
    parser.add_argument("--sheet", help="имя листа XLSX (по умолчанию первый)")
    parser.add_argument("--json", action="store_true", help="вывести summary и issues как JSON")
    parser.add_argument("--sep", choices=[",", ";", "\t", r"\t"], help="разделитель CSV; поддерживается также литерал \\t")
    args = parser.parse_args(argv)
    try:
        result = check_table(*read_table(args.file, args.sheet, "\t" if args.sep == r"\t" else args.sep))
    except (OSError, UnicodeError, ValueError, csv.Error, BadZipFile, ParseError, KeyError) as exc:
        print(f"Не удалось прочитать файл: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, allow_nan=False) if args.json else markdown(result))
    return 1 if result["issues"] else 0


if __name__ == "__main__":
    sys.exit(main())

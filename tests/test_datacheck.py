"""Регрессии проверок данных: CLI, строки источника, единицы и календарь."""
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import datacheck as D
import mcp_server as MCP

FIXTURES = ROOT / "tests/fixtures/datacheck"


def cli(path, *args):
    return subprocess.run([sys.executable, str(ROOT / "tools/datacheck.py"), str(path), *args],
                          capture_output=True, text=True, encoding="utf-8", timeout=30)


def table(tmp_path, text, encoding="utf-8"):
    path = tmp_path / "данные.csv"
    path.write_bytes(text.encode(encoding))
    return path


def issues(result, kind):
    return [item for item in result["issues"] if item["type"] == kind]


@pytest.mark.parametrize("kind,column,rows", [
    ("missing", "Выручка", [3]),
    ("duplicate", None, [4, 5]),
    ("unit_mismatch", "Доля", [4]),
    ("period_gap", "Месяц", [4]),
    ("period_duplicate", "Месяц", [4]),
    ("outlier", "Выручка", [9]),
    ("type_mix", "Выручка", [10]),
])
def test_each_issue_fixture(kind, column, rows):
    proc = cli(FIXTURES / f"{kind}.csv", "--json")
    assert proc.returncode == 1, proc.stderr
    found = issues(json.loads(proc.stdout), kind)
    assert len(found) == 1
    assert found[0]["column"] == column
    assert found[0]["rows"] == rows
    assert set(found[0]) == {"type", "column", "rows", "detail"}
    assert found[0]["detail"]


def test_clean_summary():
    proc = cli(FIXTURES / "clean.csv", "--json")
    assert proc.returncode == 0, proc.stderr
    result = json.loads(proc.stdout)
    assert result["issues"] == []
    assert result["summary"]["revenue"] == {
        "type": "number", "nonempty": 6, "min": 100, "max": 140, "unit": None}
    assert result["summary"]["month"] == {
        "type": "date", "nonempty": 6, "first": "2025-01", "last": "2025-06", "unit": None}


def test_cp1251_fixture():
    proc = cli(FIXTURES / "cp1251.csv", "--json")
    assert proc.returncode == 0, proc.stderr
    result = json.loads(proc.stdout)
    assert result["issues"] == []
    assert result["summary"]["Выручка"]["min"] == 1234.5
    assert result["summary"]["Выручка"]["max"] == 1410.25


@pytest.mark.parametrize("text,value,unit", [
    ("1 234,5", 1234.5, None), ("12,3 %", 12.3, "%"),
    ("1 200 ₽", 1200, "₽"), ("−5", -5, None),
    ("1\u00a0234,5", 1234.5, None), ("1\u202f234,5", 1234.5, None),
    ("3 тыс. руб", 3, "тыс ₽"), ("4 млн", 4, "млн"), ("5 млрд", 5, "млрд"),
])
def test_russian_numbers(tmp_path, text, value, unit):
    path = table(tmp_path, f"Показатель;Значение\nA;{text}\n")
    proc = cli(path, "--json")
    assert proc.returncode == 0, proc.stderr
    item = json.loads(proc.stdout)["summary"]["Значение"]
    assert item["type"] == "number" and item["min"] == value and item["max"] == value
    assert item["unit"] == unit


@pytest.mark.parametrize("marker", ["", "NA", "N/A", "null", "-", "—", " nA "])
def test_missing_markers_and_empty_column(tmp_path, marker):
    path = table(tmp_path, f"id,value,empty\na,1,\nb,{marker},\nc,2,\n")
    result = D.check_table(*D.read_table(path))
    assert issues(result, "missing") == [{"type": "missing", "column": "value", "rows": [3],
                                          "detail": "пустое значение или маркер пропуска"}]
    assert result["summary"]["value"]["nonempty"] == 2
    assert result["summary"]["empty"]["nonempty"] == 0


@pytest.mark.parametrize("header,values,wrong", [
    ("Доход", ["1 ₽", "2 руб", "3 $"], [4]),
    ("Доход", ["1 тыс.", "2 тыс.", "3"], [4]),
    ("Доход", ["1 млн", "2 млн", "3 млрд"], [4]),
    ("Выручка (тыс. руб)", ["1", "2 тыс. руб", "3000 руб"], [4]),
    ("Доля (%)", ["1", "2 %", "3 ₽"], [4]),
])
def test_unit_mismatch_variants(tmp_path, header, values, wrong):
    path = table(tmp_path, header + "\n" + "\n".join(values) + "\n")
    result = D.check_table(*D.read_table(path))
    found = issues(result, "unit_mismatch")
    assert len(found) == 1 and found[0]["column"] == header and found[0]["rows"] == wrong


def test_header_units_apply_to_bare_values(tmp_path):
    path = table(tmp_path, "Выручка (тыс. руб)\n1\n2 тыс. ₽\n3 тыс. руб\n")
    result = D.check_table(*D.read_table(path))
    assert result["issues"] == []
    assert result["summary"]["Выручка (тыс. руб)"]["unit"] == "тыс ₽"


@pytest.mark.parametrize("periods", [
    ["2025-01", "2025-02", "2025-04", "2025-05"],
    ["01.2025", "02.2025", "04.2025", "05.2025"],
    ["2025-01-01", "2025-01-02", "2025-01-04", "2025-01-05"],
    ["01.01.2025", "02.01.2025", "04.01.2025", "05.01.2025"],
    ["2025Q1", "2025Q2", "2025Q4", "2026Q1"],
    ["Q1 2025", "Q2 2025", "Q4 2025", "Q1 2026"],
    ["2023", "2024", "2026", "2027"],
])
def test_period_formats_and_rows(tmp_path, periods):
    path = table(tmp_path, "Период,value\n" + "\n".join(f"{p},{i}" for i, p in enumerate(periods)) + "\n")
    result = D.check_table(*D.read_table(path))
    found = issues(result, "period_gap")
    assert len(found) == 1 and found[0]["column"] == "Период" and found[0]["rows"] == [4]


@pytest.mark.parametrize("periods", [
    ["2025-01", "2025-04", "2025-07", "2025-10"],
    ["2025-01-01", "2025-02-01", "2025-03-01", "2025-04-01"],
    ["2025-01-31", "2025-02-28", "2025-03-31", "2025-04-30"],
    ["2025-01-01", "2025-01-08", "2025-01-15", "2025-01-22"],
])
def test_regular_non_daily_step_is_clean(tmp_path, periods):
    path = table(tmp_path, "date\n" + "\n".join(periods) + "\n")
    assert D.check_table(*D.read_table(path))["issues"] == []


def test_period_inferred_without_header_and_segment_duplicates(tmp_path):
    path = table(tmp_path, "key,city,channel,value\n2025-01,A,web,1\n2025-01,B,web,2\n"
                 "01.2025,A,app,3\n01.2025,A,web,4\n2025-02,A,web,5\n")
    result = D.check_table(*D.read_table(path))
    assert result["summary"]["key"]["type"] == "date"
    found = issues(result, "period_duplicate")
    assert len(found) == 1 and found[0]["column"] == "key" and found[0]["rows"] == [5]


def test_outlier_threshold_and_type_mix_boundary(tmp_path):
    path = table(tmp_path, "id,value\n" + "\n".join(f"r{i},{v}" for i, v in enumerate([1, 2, 3, 4, "x"])) + "\n")
    result = D.check_table(*D.read_table(path))
    assert issues(result, "type_mix")[0]["rows"] == [6]
    assert issues(result, "outlier") == []
    path = table(tmp_path, "id,value\n" + "\n".join(f"r{i},{v}" for i, v in enumerate([1, 2, 3, "x", "y"])) + "\n")
    assert issues(D.check_table(*D.read_table(path)), "type_mix") == []


def test_tsv_override_bom_and_physical_rows(tmp_path):
    # Перенос внутри кавычек занимает физическую строку; пропуск — строка 4.
    path = table(tmp_path, 'label\tvalue\n"a\nb"\t1\nc\t\n', "utf-8-sig")
    for sep in (None, "\t", r"\t"):
        proc = cli(path, "--json", *(["--sep", sep] if sep else []))
        assert proc.returncode == 1, proc.stderr
        assert issues(json.loads(proc.stdout), "missing")[0]["rows"] == [4]


def test_markdown_is_russian():
    proc = cli(FIXTURES / "missing.csv")
    assert proc.returncode == 1
    assert "| Столбец |" in proc.stdout
    assert "строки 3: пропуск в «Выручка»" in proc.stdout


@pytest.mark.parametrize("content", ["", 'a,b\n"незакрытая,1\n', "a,a\n1,2\n", "a,b\n1,2,3\n", "a\x00b"])
def test_unreadable_csv(tmp_path, content):
    proc = cli(table(tmp_path, content), "--json")
    assert proc.returncode == 2
    assert "Не удалось прочитать файл" in proc.stderr and "Traceback" not in proc.stderr


def test_missing_file(tmp_path):
    proc = cli(tmp_path / "отсутствует.csv")
    assert proc.returncode == 2 and "Traceback" not in proc.stderr


def test_xlsx_without_optional_dependency(tmp_path, monkeypatch, capsys):
    path = tmp_path / "input.xlsx"
    path.write_bytes("не читается без openpyxl".encode("utf-8"))
    monkeypatch.setitem(sys.modules, "openpyxl", None)
    assert D.main([str(path)]) == 2
    assert "XLSX не поддержан без openpyxl, сохраните как CSV" in capsys.readouterr().err


@pytest.mark.skipif(importlib.util.find_spec("openpyxl") is None, reason="openpyxl не установлен; зависимость необязательна")
def test_xlsx_sheet_and_dates(tmp_path):
    import openpyxl
    from datetime import datetime
    path = tmp_path / "input.xlsx"
    book = openpyxl.Workbook()
    book.active.title = "Пустой"
    sheet = book.create_sheet("Данные")
    sheet.append(["date", "value"])
    sheet.append([datetime(2025, 1, 1), 10])
    sheet.append([datetime(2025, 1, 2), 11])
    sheet.append([datetime(2025, 1, 4), 12])
    book.save(path)
    book.close()
    proc = cli(path, "--sheet", "Данные", "--json")
    assert proc.returncode == 1, proc.stderr
    assert issues(json.loads(proc.stdout), "period_gap")[0]["rows"] == [4]
    proc = cli(path, "--sheet", "нет")
    assert proc.returncode == 2 and "нет листа" in proc.stderr


def test_mcp_registration_and_options():
    listing = MCP.handle({"id": 1, "method": "tools/list"})
    tool = next(t for t in listing["result"]["tools"] if t["name"] == "strategist_datacheck")
    assert tool["inputSchema"]["required"] == ["file"]
    result = MCP.handle({"id": 2, "method": "tools/call", "params": {
        "name": "strategist_datacheck", "arguments": {"file": str(FIXTURES / "cp1251.csv"), "json": True, "sep": ";"}}})
    assert result["result"]["isError"] is False
    assert '"issues": []' in result["result"]["content"][0]["text"]

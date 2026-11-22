"""Правка устаревших строк после пересмотра: задание без целых документов, применение с защитами."""
import json
import shutil
import sys
from pathlib import Path

import yaml
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
import delegate as D  # noqa: E402
import revise_lines as R  # noqa: E402
import verify as V  # noqa: E402


def setup(tmp_path):
    proj = tmp_path / "example"
    shutil.copytree(ROOT / "projects" / "example", proj)
    (proj / "outputs").mkdir(exist_ok=True)
    (proj / "state/run_log.md").write_text("| время | шаг | действие | уровень | модель | результат | расход |\n|---|---|---|---|---|---|---|\n",
                                           encoding="utf-8")
    f = proj / "state/ledger/hypotheses.yaml"
    data = yaml.safe_load(f.read_text(encoding="utf-8"))
    for o in data:
        if o["id"] == "HYP-001":
            o["status_note"] = "Пересмотрено по CHG-001: ослаблена — синхронизация у 8%."
    f.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
    doc = proj / "outputs" / "03_situation.md"
    doc.write_text("# Ситуация\n"
                   "Синхронизация — главная причина ухода [HYP-001].\n"
                   "| HYP-001 | синхронизация | high |\n"
                   "- Причина пока не ясна [HYP-001].\n"
                   "Решение прежнее [DEC-001].\n", encoding="utf-8")
    return proj, doc


def fake(reply, seen):
    def run(argv):
        a = dict(zip(argv[::2], argv[1::2]))
        seen["task"] = Path(a["--prompt-file"]).read_text(encoding="utf-8")
        seen["kind"] = a["--task"]
        (Path(a["--project"]) / "state/delegated/20990101-000000_revise-lines.md").write_text(reply, encoding="utf-8")
        return 0
    return run


def test_rewrites_marks_and_guards(tmp_path, monkeypatch):
    proj, doc = setup(tmp_path)
    seen = {}
    reply = json.dumps([
        {"key": "03:2", "action": "rewrite", "text": "Синхронизация уже не главная причина: затронуто 8% [HYP-001]."},
        {"key": "03:3", "action": "rewrite", "text": "| HYP-001 | синхронизация | low | лишняя ячейка |"},  # ломает таблицу
        {"key": "03:4", "action": "rewrite", "text": "Причина пока не ясна."},                                # потерян ID
    ], ensure_ascii=False)
    monkeypatch.setattr(D, "main", fake("```json\n" + reply + "\n```", seen))
    assert R.main([str(proj), "--change", "CHG-001", "--step", "8"]) == 1
    assert seen["kind"] == "revise-lines" and "ослаблена" in seen["task"] and "[03:2]" in seen["task"]
    assert "Решение прежнее" not in seen["task"]  # в задание идут только устаревшие строки, не документ
    lines = doc.read_text(encoding="utf-8").split("\n")
    assert lines[1] == "Синхронизация уже не главная причина: затронуто 8% [HYP-001]. (пересмотрено по CHG-001)"
    assert lines[2] == "| HYP-001 | синхронизация | high |"
    assert lines[3] == "- Причина пока не ясна [HYP-001]."
    assert lines[4] == "Решение прежнее [DEC-001]."
    assert [i["line"] for i in V.verify(proj, [], "CHG-001")["items"] if i["verdict"] == "stale?"] == [3, 4]
    assert "правка строк по CHG-001" in (proj / "state/run_log.md").read_text(encoding="utf-8")


def test_nothing_to_do_and_no_backend(tmp_path, monkeypatch):
    proj, doc = setup(tmp_path)
    monkeypatch.setattr(D, "main", lambda argv: 3)
    assert R.main([str(proj), "--change", "CHG-001"]) == 3
    doc.write_text("Решение прежнее [DEC-001].\n", encoding="utf-8")
    assert R.main([str(proj), "--change", "CHG-001"]) == 0


def test_changed_line_is_not_overwritten(tmp_path, monkeypatch):
    proj, doc = setup(tmp_path)

    def run(argv):
        doc.write_text(doc.read_text(encoding="utf-8").replace("главная причина ухода", "ГЛАВНАЯ"), encoding="utf-8")
        return fake(json.dumps([{"key": "03:2", "action": "rewrite", "text": "x [HYP-001]"}]), {})(argv)
    monkeypatch.setattr(D, "main", run)
    R.main([str(proj), "--change", "CHG-001"])
    assert "ГЛАВНАЯ" in doc.read_text(encoding="utf-8").split("\n")[1]


@pytest.mark.parametrize("text", ["", "   ", None, 42,
                                    "| HYP-001 | синхронизация | high x",
                                    "Текст [HYP-001]\nещё строка"])
def test_invalid_rewrite_leaves_line_stale(tmp_path, monkeypatch, text):
    proj, doc = setup(tmp_path)
    doc.write_text("| HYP-001 | синхронизация | high |\n", encoding="utf-8")
    before = doc.read_bytes()
    monkeypatch.setattr(D, "main", fake(json.dumps([
        {"key": "03:1", "action": "rewrite", "text": text}]), {}))
    assert R.main([str(proj), "--change", "CHG-001"]) == 1
    assert doc.read_bytes() == before
    assert V.verify(proj, [], "CHG-001")["counts"]["stale?"] == 1


def test_explicit_mark_preserves_table_and_closes_current_change(tmp_path, monkeypatch):
    proj, doc = setup(tmp_path)
    monkeypatch.setattr(D, "main", fake(json.dumps([
        {"key": f"03:{ln}", "action": "mark"} for ln in (2, 3, 4)]), {}))
    assert R.main([str(proj), "--change", "CHG-001"]) == 0
    assert "| HYP-001 | синхронизация | high (проверено по CHG-001) |" in doc.read_text(encoding="utf-8")
    assert "stale?" not in V.verify(proj, [], "CHG-001")["counts"]


def test_full_line_compared_before_write(tmp_path, monkeypatch):
    proj, doc = setup(tmp_path)
    original = "Строка [HYP-001] " + "а" * 310 + " хвост\n"
    doc.write_text(original, encoding="utf-8")

    def run(argv):
        # Даже изменение за пределами 300 символов отменяет запись.
        doc.write_text(original.replace("хвост", "новый хвост"), encoding="utf-8")
        return fake(json.dumps([{"key": "03:1", "action": "mark"}]), {})(argv)

    monkeypatch.setattr(D, "main", run)
    assert R.main([str(proj), "--change", "CHG-001"]) == 1
    assert doc.read_text(encoding="utf-8") == original.replace("хвост", "новый хвост")


def test_long_line_and_short_ref_lists_can_be_rewritten(tmp_path, monkeypatch):
    proj, doc = setup(tmp_path)
    doc.write_text("  Строка [HYP-001, 002] " + "а" * 310 + "\n", encoding="utf-8")
    seen = {}
    monkeypatch.setattr(D, "main", fake(json.dumps([
        {"key": "03:1", "action": "rewrite", "text": "Актуально [HYP-001, 002]."}]), seen))
    assert R.main([str(proj), "--change", "CHG-001"]) == 0
    assert "а" * 310 in seen["task"]
    assert doc.read_text(encoding="utf-8") == "  Актуально [HYP-001, 002]. (пересмотрено по CHG-001)\n"


def test_valid_table_rewrite_closes_current_change(tmp_path, monkeypatch):
    proj, doc = setup(tmp_path)
    doc.write_text("| HYP-001 | синхронизация | high |\n", encoding="utf-8")
    monkeypatch.setattr(D, "main", fake(json.dumps([
        {"key": "03:1", "action": "rewrite", "text": "| HYP-001 | синхронизация | low |"}]), {}))
    assert R.main([str(proj), "--change", "CHG-001"]) == 0
    assert doc.read_text(encoding="utf-8") == "| HYP-001 | синхронизация | low (пересмотрено по CHG-001) |\n"


@pytest.mark.parametrize("extra", ["CLM-999", "DEC-001"])
def test_cannot_add_unrelated_reference(tmp_path, monkeypatch, extra):
    proj, doc = setup(tmp_path)
    original = "Исходное основание [HYP-001].\n"
    doc.write_text(original, encoding="utf-8")
    monkeypatch.setattr(D, "main", fake(json.dumps([
        {"key": "03:1", "action": "rewrite", "text": f"Уточнение [HYP-001, {extra}]."}]), {}))
    assert R.main([str(proj), "--change", "CHG-001"]) == 1
    assert doc.read_text(encoding="utf-8") == original


def test_already_marked_wrong_reference_is_reported(tmp_path):
    proj, doc = setup(tmp_path)
    doc.write_text("Уточнение [HYP-001, CLM-999] (пересмотрено по CHG-001).\n", encoding="utf-8")
    assert R.main([str(proj), "--change", "CHG-001"]) == 1

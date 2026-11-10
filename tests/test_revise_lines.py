"""Правка устаревших строк после пересмотра: задание без целых документов, применение с защитами."""
import json
import shutil
import sys
from pathlib import Path

import yaml

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
    assert R.main([str(proj), "--change", "CHG-001", "--step", "8"]) == 0
    assert seen["kind"] == "revise-lines" and "ослаблена" in seen["task"] and "[03:2]" in seen["task"]
    assert "Решение прежнее" not in seen["task"]  # в задание идут только устаревшие строки, не документ
    lines = doc.read_text(encoding="utf-8").split("\n")
    assert lines[1] == "Синхронизация уже не главная причина: затронуто 8% [HYP-001]. (пересмотрено по CHG-001)"
    assert lines[2] == "| HYP-001 | синхронизация | high (проверено по CHG-001) |"  # защита таблицы → пометка
    assert lines[3] == "- Причина пока не ясна [HYP-001]. (проверено по CHG-001)"       # защита ID → пометка
    assert lines[4] == "Решение прежнее [DEC-001]."
    assert not [i for i in V.verify(proj, [], "CHG-001")["items"] if i["verdict"] == "stale?"]
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

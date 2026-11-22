"""Контракт очереди: сроки, граф оснований, действия человека и отсутствие записи."""
import hashlib
import importlib
import json
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import urllib.request

from datetime import date

import pytest
import yaml

N = importlib.import_module("next")
ROOT = Path(__file__).resolve().parents[1]
FIXTURE = Path(__file__).parent / "fixtures" / "next" / "actionable"
ON = date(2026, 11, 12)


def write(project, filename, objects):
    folder = project / "state" / "ledger"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / filename).write_text(yaml.safe_dump(objects, allow_unicode=True), encoding="utf-8")


def hashes(project):
    return {str(path.relative_to(project)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in project.rglob("*") if path.is_file()}


def test_retracted_basis_reopens_c2_before_unrecorded_c3(tmp_path):
    project = shutil.copytree(ROOT / "projects/esim-travel", tmp_path / "esim")
    claims = yaml.safe_load((project / "state/ledger/claims.yaml").read_text())
    next(claim for claim in claims if claim["id"] == "CLM-015")["status"] = "retracted"
    write(project, "claims.yaml", claims)

    def run(tool, *args):
        return subprocess.check_output([sys.executable, str(ROOT / "tools" / tool), *map(str, args)], text=True)

    resume = json.loads(run("next.py", project, "--resume", "--json"))["resume"]
    assert resume["open_checkpoint"] == "C2"
    assert resume["c3"]["status"] == "unrecorded"
    assert resume["choices"] == [{"decision": "DEC-001", "chosen": "ALT-001"}]
    report = tmp_path / "report.html"
    run("report.py", project, "--out", report)
    assert "Согласование C2" in report.read_text()
    assert 'id="c3-executive-summary"' not in report.read_text()
    assert "на повторном C2" in run("ledger.py", "status", "--root", tmp_path)


def test_future_processed_and_dedup():
    result = N.build_queue(FIXTURE, ON)
    assert result["on"] == "2026-11-12"
    assert [item["id"] for item in result["items"]] == ["CHG-001", "DEC-001", "DEC-002"]
    by_id = {item["id"]: item for item in result["items"]}
    assert "DEC-003" not in by_id  # завтрашний срок
    assert "CHG-002" not in by_id  # уже обработано
    assert len(by_id["DEC-002"]["reasons"]) == 2
    assert by_id["DEC-002"]["reasons"] == [
        {"code": "needs_review", "date": None},
        {"code": "review_due", "date": "2026-11-12"},
    ]
    assert all("Руководителю продукта" in item["next_action"] for item in by_id.values())
    assert all("C2" in item["next_action"] for item in by_id.values())


def test_read_only_and_reproducible(tmp_path, monkeypatch, capsys):
    project = tmp_path / "case"
    shutil.copytree(FIXTURE, project)
    # Хэшируем не только реестр: произвольные материалы и состояние сохраняются.
    (project / "progress.md").write_text("Выбор остаётся у человека", encoding="utf-8")
    (project / "source.bin").write_bytes(b"\x00\xff\x01")
    before = hashes(project)
    calls = []

    def forbidden(*args, **kwargs):
        calls.append((args, kwargs))
        raise AssertionError("Очереди запрещены внешние вызовы")

    monkeypatch.setattr(socket, "socket", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(socket, "getaddrinfo", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    monkeypatch.setattr(urllib.request, "urlopen", forbidden)
    outputs = []
    # Вызовы точки входа CLI внутри процесса, чтобы запрет сети/запуска моделей действовал.
    for _ in range(2):
        assert N.main([str(project), "--on", "2026-11-12", "--json"]) == 0
        outputs.append(capsys.readouterr().out)
        assert hashes(project) == before
    assert outputs[0] == outputs[1]
    assert len(json.loads(outputs[0])["items"]) == 3
    assert calls == []


def test_transitive_stale_bases_cycles_and_unique_reasons(tmp_path):
    write(tmp_path, "decisions.yaml", [{"id": "DEC-001", "state": "decided",
          "options": ["ALT-001", "ALT-002"], "based_on": ["CLM-001"]}])
    write(tmp_path, "alternatives.yaml", [
        {"id": "ALT-001", "supported_by": ["HYP-001"]},
        {"id": "ALT-002", "scores": {"цена": {"refs": ["CLM-002"]}}},
    ])
    write(tmp_path, "hypotheses.yaml", [{"id": "HYP-001", "based_on": ["CLM-001"]}])
    write(tmp_path, "claims.yaml", [
        {"id": "CLM-001", "status": "retracted", "derived_from": ["CLM-002"]},
        {"id": "CLM-002", "status": "superseded", "superseded_by": "CLM-003",
         "derived_from": ["CLM-001"]},
        {"id": "CLM-003", "status": "active"},
    ])
    items = N.build_queue(tmp_path, ON)["items"]
    assert len(items) == 1
    assert items[0]["id"] == "DEC-001" and items[0]["date"] is None
    reasons = {r["reference"]: r for r in items[0]["reasons"]}
    assert len(items[0]["reasons"]) == len(reasons) == 2
    assert reasons["CLM-001"]["code"] == "basis_retracted"
    assert reasons["CLM-002"]["code"] == "basis_superseded"
    assert reasons["CLM-002"]["superseded_by"] == "CLM-003"
    assert reasons["CLM-002"]["path"][0] == "DEC-001"
    assert reasons["CLM-002"]["path"][-1] == "CLM-002"


def test_no_guessed_dates_or_experiment_deadlines(tmp_path):
    write(tmp_path, "decisions.yaml", [
        {"id": "DEC-002", "state": "needs_review"},
        {"id": "DEC-001", "state": "needs_review"},
        {"id": "DEC-003", "state": "decided"},
        {"id": "DEC-004", "state": "needs_review", "status": "retracted"},
        {"id": "DEC-005", "review_by": "2026-01-01", "status": "superseded"},
    ])
    write(tmp_path, "changes.yaml", [{"id": "CHG-001", "processed": False}])
    write(tmp_path, "experiments.yaml", [{"id": "EXP-001", "duration": "две недели"}])
    items = N.build_queue(tmp_path, ON)["items"]
    assert [i["id"] for i in items] == ["CHG-001", "DEC-001", "DEC-002"]
    assert all(i["date"] is None for i in items)


def test_input_order_does_not_set_priority(tmp_path):
    objects = [{"id": "DEC-002", "state": "needs_review"},
               {"id": "DEC-001", "review_by": "2026-11-12"}]
    write(tmp_path, "decisions.yaml", objects)
    first = N.build_queue(tmp_path, ON)
    write(tmp_path, "decisions.yaml", list(reversed(objects)))
    assert N.build_queue(tmp_path, ON) == first


@pytest.mark.parametrize("content", ["bad: mapping", "- id: DEC-001\n  review_by: завтра",
                         "- id: DEC-001\n  review_by: 2026-02-30", "[unclosed",
                         "- id: DEC-001\n- id: DEC-001",
                         "- id: DEC-001\n  based_on: [CLM-999]"])
def test_invalid_input_is_not_empty_success(tmp_path, capsys, content):
    folder = tmp_path / "state" / "ledger"
    folder.mkdir(parents=True)
    (folder / "decisions.yaml").write_text(content, encoding="utf-8")
    assert N.main([str(tmp_path), "--on", "2026-11-12", "--json"]) == 2
    assert json.loads(capsys.readouterr().out)["errors"]


def test_missing_project_and_invalid_on(tmp_path, capsys):
    assert N.main([str(tmp_path / "missing"), "--json"]) == 2
    assert json.loads(capsys.readouterr().out)["errors"]
    with pytest.raises(SystemExit) as exc:
        N.main([str(FIXTURE), "--on", "2026-02-30"])
    assert exc.value.code == 2


def test_default_today_and_text_output(monkeypatch, capsys):
    class FixedDate(date):
        @classmethod
        def today(cls):
            return cls(2026, 11, 12)

    monkeypatch.setattr(N, "date", FixedDate)
    assert N.main([str(FIXTURE), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["on"] == "2026-11-12"
    assert N.main([str(FIXTURE), "--on", "2026-11-12"]) == 0
    out = capsys.readouterr().out
    assert "DEC-001" in out and "без бизнес-приоритета" in out


def test_empty_ledger_is_empty_queue(tmp_path):
    (tmp_path / "state" / "ledger").mkdir(parents=True)
    assert N.build_queue(tmp_path, ON) == {"on": "2026-11-12", "items": []}

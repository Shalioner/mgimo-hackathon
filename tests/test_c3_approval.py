import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]


def run(tool, *args):
    return subprocess.run(
        [sys.executable, "-B", str(ROOT / "tools" / (tool + ".py")), *map(str, args)],
        cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
    )


@pytest.fixture
def project(tmp_path):
    return shutil.copytree(ROOT / "projects/ryadom", tmp_path / "ryadom")


def approve(project, presented):
    answer = project.parent / "answer.txt"
    answer.write_text("Учебный контроль: утверждаю предъявленную версию.", encoding="utf-8")
    return run("c3_approval", "record", project, "--sha256", presented, "--response-file", answer)


def prepare(project):
    result = run("c3_approval", "prepare", project)
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


@pytest.mark.parametrize("mutation", ["source", "promise"])
def test_c3_reopens_for_material_changes(project, mutation):
    receipt = project / "state/c3_approvals.json"
    history = project / "state/run_log.md"
    historical_bytes = history.read_bytes()
    unrecorded = run("c3_approval", "check", project, "--json")
    assert unrecorded.returncode == 1
    assert json.loads(unrecorded.stdout)["status"] == "unrecorded"
    assert not receipt.exists()

    first = prepare(project)
    assert approve(project, first).returncode == 0
    accepted = receipt.read_bytes()
    with (project / "state/progress.md").open("a", encoding="utf-8") as note:
        note.write("\nСлужебная заметка.\n")
    assert run("c3_approval", "check", project).returncode == 0
    assert approve(project, first).returncode == 0
    assert receipt.read_bytes() == accepted

    if mutation == "source":
        relative = "sources/08_analytics_2026-10.md"
        key = f"источник: {relative}"
        source = project / relative
        before = hashlib.sha256(source.read_bytes()).hexdigest()
        with source.open("a", encoding="utf-8") as stream:
            stream.write("\nОтветы об отмене получены на тестовых устройствах.\n")
        registry = project / "state/ledger/sources.yaml"
        text = registry.read_text(encoding="utf-8")
        assert before in text
        after = hashlib.sha256(source.read_bytes()).hexdigest()
        registry.write_text(text.replace(before, after), encoding="utf-8")
    else:
        key = "PRM-001"
        promises = project / "state/ledger/promises.yaml"
        text = promises.read_text(encoding="utf-8")
        old = "message: Геопозиция детского устройства"
        assert old in text
        promises.write_text(text.replace(old, "message: Последняя известная геопозиция детского устройства"),
                            encoding="utf-8")

    assert run("ledger", "validate", project).returncode == 0
    result = run("c3_approval", "check", project, "--json")
    assert result.returncode == 1
    assert json.loads(result.stdout)["status"] == "stale"
    assert key in json.loads(result.stdout)["changed"]
    result = run("consistency", project, "--json")
    assert result.returncode == 1
    assert [item["code"] for item in json.loads(result.stdout) if item["status"] == "error"] == [
        "consistency.c3_approval_stale"
    ]
    assert "Требуется повторное C3" in run("ledger", "status", "--root", project.parent).stdout
    assert run("report", project).returncode == 0
    html = (project / "outputs/report.html").read_text(encoding="utf-8")
    assert "Требуется повторное C3" in html and "явное утверждение руководителя" in html

    rejected = approve(project, first)
    assert rejected.returncode == 2 and "предъявленная версия отличается" in rejected.stderr
    assert receipt.read_bytes() == accepted and history.read_bytes() == historical_bytes
    second = prepare(project)
    assert second != first
    assert approve(project, second).returncode == 0
    assert run("consistency", project).returncode == 0
    assert [entry["sha256"] for entry in json.loads(receipt.read_bytes())["approvals"]] == [first, second]
    assert history.read_bytes() == historical_bytes


def test_c3_prepare_still_enforces_package_gate():
    result = run("c3_approval", "prepare", ROOT / "projects/esim-travel")
    assert result.returncode == 2
    assert "0 активных PRM" in result.stderr


def test_invalid_c3_record_cannot_pass_consistency(project):
    receipt = project / "state/c3_approvals.json"
    receipt.write_text('{"version": 1, "approvals": []}', encoding="utf-8")
    result = run("consistency", project, "--json")
    assert result.returncode == 1
    assert "consistency.c3_approval_invalid" in result.stdout

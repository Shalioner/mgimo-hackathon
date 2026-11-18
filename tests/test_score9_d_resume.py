"""Детерминированные проверки возобновления учебного решения после паузы."""

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest
import yaml

ROOT = Path(os.environ.get("STRATEGIST_TARGET_ROOT", Path(__file__).resolve().parents[1])).resolve()
ON_DATE = "2026-11-18"


def run(project, tool, *args, expected=0):
    proc = subprocess.run(
        [sys.executable, str(ROOT / "tools" / tool), *[str(arg) for arg in args]],
        cwd=project, capture_output=True, text=True, encoding="utf-8", timeout=30,
    )
    assert proc.returncode == expected, (proc.returncode, proc.stdout, proc.stderr)
    return proc.stdout


def hashes(folder):
    return {f.relative_to(folder): hashlib.sha256(f.read_bytes()).hexdigest()
            for f in sorted(folder.rglob("*")) if f.is_file()}


def load(project, table):
    return yaml.safe_load((project / "state" / "ledger" / f"{table}.yaml").read_text(encoding="utf-8"))


def save(project, table, rows):
    (project / "state" / "ledger" / f"{table}.yaml").write_text(
        yaml.safe_dump(rows, allow_unicode=True, sort_keys=False), encoding="utf-8",
    )


def queue(project, day=ON_DATE):
    return json.loads(run(project, "next.py", project, "--on", day, "--json"))["items"]


@pytest.mark.skipif(not (ROOT / "tools/c3_approval.py").is_file(), reason="Нужен учебный снимок cb228422 с C3")
def test_saved_step_and_pending_c3_survive_separate_cli_processes(tmp_path):
    project = tmp_path / "esim"
    shutil.copytree(ROOT / "projects/esim-travel", project)
    before = hashes(project)
    progress = (project / "state/progress.md").read_text(encoding="utf-8")
    assert "Текущий шаг: 8" in progress and "повторное C3 открыто" in progress
    assert [(item["id"], item["reasons"][0]["code"]) for item in queue(project)] == [
        ("CHG-001", "unprocessed_change"),
    ]
    assert load(project, "decisions")[0]["chosen"] == "ALT-001"
    assert load(project, "changes")[0]["processed"] is False
    assert hashes(project) == before


@pytest.mark.skipif(not (ROOT / "tools/c3_approval.py").is_file(), reason="Нужен учебный снимок с C3")
@pytest.mark.parametrize(("case", "expected"), [("esim-travel", 2), ("ryadom", 0)])
def test_preparing_a_c3_version_checks_package_without_recording_approval(tmp_path, case, expected):
    project = tmp_path / case
    shutil.copytree(ROOT / "projects" / case, project)
    before = hashes(project)

    run(project, "ledger.py", "status", "--root", tmp_path)
    queue(project)
    assert hashes(project) == before

    version = run(project, "c3_approval.py", "prepare", project, expected=expected).strip()
    if expected == 0:
        assert len(version) == 64 and len(bytes.fromhex(version)) == 32
    else:
        assert not version
    assert hashes(project) == before


def test_fresh_source_limits_impact_and_preserves_chosen_while_waiting(tmp_path):
    project = tmp_path / "notes-new-period"
    shutil.copytree(ROOT / "projects/example", project)
    source = project / "sources" / "new_period.md"
    source.write_text("Учебная сводка. В новой сопоставимой выборке сбои синхронизации редки.\n", encoding="utf-8")
    run(project, "ledger.py", "add-source", project, source, "--title", "Учебная сводка",
        "--kind", "document", "--access", "own")
    alternate = load(project, "alternatives")[1]
    decisions = load(project, "decisions")
    decisions[0]["review_by"] = ON_DATE
    save(project, "decisions", decisions)
    change = load(project, "changes")[0]
    change.update(source="SRC-006", date=ON_DATE, summary="Новая сводка ослабляет причину ухода.")
    change["effects"][0].update(effect="weakens", note="Сбои редки в новом окне.")
    save(project, "changes", [change])
    run(project, "ledger.py", "validate", project)
    assert {item["id"] for item in queue(project, "2026-11-17")} == {"CHG-001"}
    assert {item["id"] for item in queue(project)} == {"CHG-001", "DEC-001"}
    graph = json.loads(run(project, "ledger.py", "impact", project, "CHG-001", "--json"))
    assert set(graph) == {"CLM-004", "HYP-001", "PAIN-001", "ALT-001", "DEC-001", "PRM-001", "EXP-001"}
    assert graph["ALT-001"]["path"] == ["CLM-004", "HYP-001", "ALT-001"]

    claims = load(project, "claims")
    claim = next(row for row in claims if row["id"] == "CLM-004")
    claim["status_note"] = "Пересмотрено по CHG-001, причинность требует новой проверки."
    save(project, "claims", claims)
    run(project, "ledger.py", "validate", project)
    output = project / "outputs" / "98_rehearsal.md"
    output.parent.mkdir(exist_ok=True)
    output.write_text("Основная причина — сбои синхронизации [CLM-004].\n", encoding="utf-8")

    def verify(expected=0):
        return json.loads(run(project, "verify.py", project, "--change", "CHG-001", "--files", "98",
                              "--json", expected=expected))

    assert verify(expected=1)["counts"].get("stale?") == 1
    output.write_text(
        "Причинность требует новой проверки [CLM-004] (пересмотрено по CHG-001).\n", encoding="utf-8",
    )
    assert verify()["counts"].get("stale?", 0) == 0
    assert load(project, "alternatives")[1] == alternate
    assert load(project, "decisions")[0]["chosen"] == "ALT-001"
    assert load(project, "changes")[0]["processed"] is False


def test_cosmetic_change_is_a_hash_signal_without_semantic_change(tmp_path):
    project = tmp_path / "cosmetic"
    shutil.copytree(ROOT / "projects/example", project)
    source = project / "sources" / "survey_sync_2026-10.md"
    original = source.read_text(encoding="utf-8")
    output = project / "outputs" / "08_decision_log.md"
    output.parent.mkdir(exist_ok=True)
    output.write_text("Руководитель выбрал ALT-001 [DEC-001].\n", encoding="utf-8")
    state_before, outputs_before = hashes(project / "state"), hashes(project / "outputs")
    source.write_text(original + "\n", encoding="utf-8")
    assert original.split() == source.read_text(encoding="utf-8").split()
    assert "sha256" in run(project, "ledger.py", "validate", project, expected=1)
    source.write_text(original, encoding="utf-8")
    run(project, "ledger.py", "validate", project)
    assert hashes(project / "state") == state_before
    assert hashes(project / "outputs") == outputs_before


def test_skills_prescribe_pending_checkpoint_and_cosmetic_control():
    decision = (ROOT / "skills/decision-support/SKILL.md").read_text(encoding="utf-8")
    impact = (ROOT / "skills/impact-analysis/SKILL.md").read_text(encoding="utf-8")
    assert all(token in decision for token in ("progress.md", "run_log.md", "повторное C3", "awaiting_human"))
    assert all(token in impact for token in ("Косметическое изменение", "processed: false", "c3_approval.py prepare"))

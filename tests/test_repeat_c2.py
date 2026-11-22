import json
import shutil

import pytest
import yaml

from test_c3_approval import ROOT, run


@pytest.fixture
def corrected_case(tmp_path):
    evidence = ROOT / "evals/score9/paired-full-cycle/evidence/gate/compact"
    project = shutil.copytree(evidence / "4-state/warehouse-returns", tmp_path / "warehouse-returns")
    registry = project / "state/ledger"
    report = tmp_path / "audit.md"
    quote = "В тестовой ветке 222 правильные приёмки и 21 обращение из 300."
    report.write_text(quote + "\n")
    result = run("ledger", "add-source", project, report, "--title", "Учебная поправка",
                 "--kind", "document", "--access", "own")
    assert result.returncode == 0, result.stdout + result.stderr
    claim = {"id": "CLM-030", "type": "fact", "statement": "В тестовой ветке 222 правильные приёмки и 21 обращение из 300.",
             "confidence": {"level": "high", "rationale": "Исправленная выгрузка."},
             "evidence": [{"source": "SRC-004", "quote": quote, "locator": "строка 1"}]}
    with (registry / "claims.yaml").open("a", encoding="utf-8") as file:
        file.write(yaml.safe_dump([claim], allow_unicode=True))
    change = {"id": "CHG-001", "date": "2026-11-22", "source": "SRC-004", "summary": "Пересчитана тестовая ветка.",
              "effects": [{"target": "CLM-010", "effect": "contradicts", "note": "222 вместо 240."}],
              "processed": False}
    (registry / "changes.yaml").write_text(yaml.safe_dump([change], allow_unicode=True), encoding="utf-8")
    memo = tmp_path / "memo.md"
    memo.write_text("Эффект +2 п.п., обращения 7%. Пауза полевой волны [CLM-030].", encoding="utf-8")
    return project, memo


def test_pending_choice_preserves_agreed_package(corrected_case):
    project, memo = corrected_case
    receipt = project / "state/c3_approvals.json"
    before = receipt.read_bytes()
    original = json.loads(before)["approvals"][-1]
    decisions = project / "state/ledger/decisions.yaml"
    open_args = (project, "CHG-001", "--recommend", "ALT-003", "--basis", "CLM-030", "--memo", memo)
    result = run("review", "open", *open_args)
    assert result.returncode == 0, result.stderr
    decision_bytes = decisions.read_bytes()
    assert run("review", "open", *open_args).returncode == 0
    assert decisions.read_bytes() == decision_bytes
    assert receipt.read_bytes() == before
    archive = project / "state/history/c3" / original["sha256"]
    for name in original["inputs"]:
        if name.startswith("outputs/"):
            assert (project / name).read_bytes() == (archive / name).read_bytes()
    assert run("c3_approval", "check-archive", project, "--sha256", original["sha256"]).returncode == 0

    result = run("next", project, "--resume", "--json")
    resume = json.loads(result.stdout)["resume"]
    assert resume["open_checkpoint"] == "C2"
    assert resume["previous_choices"] == [{"decision": "DEC-001", "chosen": "ALT-001"}]
    assert resume["pending_review"][0]["recommended"] == "ALT-003"
    assert "приостановлен" in resume["next_action"]
    assert resume["c3"]["status"] == "stale" and "сохранён" in resume["c3"]["message"]
    result = run("report", project)
    assert result.returncode == 0, result.stderr
    report = (project / "outputs/report.html").read_text()
    assert "Запуск приостановлен" in report and 'id="c3-executive-summary"' not in report
    assert "требуется действующий выбор" in run("c3_approval", "prepare", project).stderr

    decision = yaml.safe_load(decision_bytes)
    decision[0].update(state="decided", chosen="ALT-003")
    decisions.write_text(yaml.safe_dump(decision, allow_unicode=True), encoding="utf-8")
    assert "после смены выбора на C2 обновите стратегии" in run("c3_approval", "prepare", project).stderr

    decision[0]["chosen"] = "ALT-001"
    decisions.write_text(yaml.safe_dump(decision, allow_unicode=True), encoding="utf-8")
    verification = run("c3_approval", "prepare", project)
    assert verification.returncode == 2 and "[stale?]" in verification.stderr, verification.stderr


def test_bad_recommendation_leaves_choice_and_approval_intact(corrected_case):
    project, memo = corrected_case
    paths = [project / p for p in ("state/ledger/decisions.yaml", "state/c3_approvals.json")]
    before = [path.read_bytes() for path in paths]
    result = run("review", "open", project, "CHG-001", "--recommend", "ALT-001",
                 "--basis", "CLM-030", "--memo", memo)
    assert result.returncode == 2
    assert [path.read_bytes() for path in paths] == before

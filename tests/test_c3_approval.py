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


def approve(project, presented, response="Утверждаю пакет C3"):
    answer = project.parent / "answer.txt"
    answer.write_text(response, encoding="utf-8")
    return run("c3_approval", "record", project, "--sha256", presented, "--response-file", answer)


def prepare(project):
    result = run("c3_approval", "prepare", project)
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def state_files(project):
    state = project / "state"
    return {path.relative_to(state): path.read_bytes() for path in state.rglob("*") if path.is_file()}


@pytest.mark.parametrize("response", [
    "Не утверждаю пакет C3",
    "Утверждаю пакет C3, если поменяете макет",
    "Прочитал",
    "  \n",
])
def test_c3_rejects_response_without_explicit_approval(project, response):
    presented = prepare(project)
    before = state_files(project)
    rejected = approve(project, presented, response)
    assert rejected.returncode == 2, rejected.stdout
    assert state_files(project) == before

    assert approve(project, presented, "Утверждаю обновлённый пакет C3.").returncode == 0
    accepted = state_files(project)
    entries = json.loads(accepted[Path("c3_approvals.json")])["approvals"]
    assert len(entries) == 1
    assert entries[0]["sha256"] == presented
    assert entries[0]["response"] == "Утверждаю обновлённый пакет C3."
    assert approve(project, presented, response).returncode == 2
    assert state_files(project) == accepted
    assert approve(project, presented, "Утверждаю обновлённый пакет C3.").returncode == 0
    assert state_files(project) == accepted


def test_c3_rejects_stale_presentation_without_writing_state(project):
    presented = prepare(project)
    plan = project / "outputs/07_validation_plan.md"
    plan.write_text(plan.read_text(encoding="utf-8") + "\nУточнена редакция плана.\n", encoding="utf-8")
    assert prepare(project) != presented
    before = state_files(project)

    rejected = approve(project, presented)

    assert rejected.returncode == 2
    assert state_files(project) == before


@pytest.mark.parametrize("mutation", ["source", "promise", "brief_response"])
def test_c3_reopens_for_material_changes(project, mutation):
    receipt = project / "state/c3_approvals.json"
    history = project / "state/run_log.md"
    historical_bytes = history.read_bytes()
    unrecorded = run("c3_approval", "check", project, "--json")
    assert unrecorded.returncode == 1
    assert json.loads(unrecorded.stdout)["status"] == "unrecorded"
    assert not receipt.exists()

    response = project / "outputs/00_brief_response.md"
    if mutation == "brief_response":
        response.write_text("# Ответ на бриф\n\nСначала проверяем гипотезу.\n", encoding="utf-8")
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
    elif mutation == "promise":
        key = "PRM-001"
        promises = project / "state/ledger/promises.yaml"
        text = promises.read_text(encoding="utf-8")
        old = "message: Геопозиция детского устройства"
        assert old in text
        promises.write_text(text.replace(old, "message: Последняя известная геопозиция детского устройства"),
                            encoding="utf-8")
    else:
        key = "outputs/00_brief_response.md"
        response.write_text("# Ответ на бриф\n\nПереходим к запуску.\n", encoding="utf-8")

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


def test_missing_experiment_metric_reference_is_blocked(project):
    plan = project / "outputs/07_validation_plan.md"
    wording = "точное окно и исходная когорта ещё не определены"
    original = f"{wording} [EXP-002]."
    text = plan.read_text(encoding="utf-8")
    assert original in text
    plan.write_text(text.replace(original, f"{wording}."), encoding="utf-8")

    result = run("c3_approval", "prepare", project)
    assert result.returncode == 2
    assert not result.stdout.strip()
    assert "[unsupported] outputs/07_validation_plan.md:" in result.stderr


@pytest.mark.skipif(not (ROOT / "projects/family-connect").is_dir(), reason="Нужен кейс family-connect")
def test_c3_prepare_accepts_family_connect_and_excludes_08_09(tmp_path):
    copy = shutil.copytree(ROOT / "projects/family-connect", tmp_path / "family-connect")
    receipt = copy / "state/c3_approvals.json"
    receipt_bytes = receipt.read_bytes()
    expected_sha = json.loads(receipt_bytes)["approvals"][-1]["sha256"]

    assert prepare(copy) == expected_sha
    checked = run("c3_approval", "check", copy, "--json")
    assert checked.returncode == 0
    assert json.loads(checked.stdout)["status"] == "current"
    assert receipt.read_bytes() == receipt_bytes

    for excluded in ("outputs/08_decision_log.md", "outputs/09_run_report.md"):
        with (copy / excluded).open("a", encoding="utf-8") as stream:
            stream.write("\nСлужебная запись вне периметра C3: рост 77% [CLM-999].\n")

    shutil.copyfile(copy / "outputs/05_product_strategy.md", copy / "outputs/00_brief_response.md.notes.md")
    assert prepare(copy) == expected_sha
    checked_after = run("c3_approval", "check", copy, "--json")
    assert checked_after.returncode == 0
    assert json.loads(checked_after.stdout)["status"] == "current"
    assert receipt.read_bytes() == receipt_bytes


@pytest.mark.skipif(not (ROOT / "projects/family-connect").is_dir(), reason="Нужен кейс family-connect")
@pytest.mark.parametrize(
    ("relative", "line", "verdict", "token"),
    [
        ("outputs/05_product_strategy.md", "Скорость выдачи вырастет на 42% [CLM-999].", "wrong-ref", "CLM-999"),
        ("outputs/05_product_strategy.md", "Скорость выдачи вырастет на 42%.", "unsupported", "42"),
        ("outputs/00_brief_response.md", "Скорость выдачи вырастет на 42% [CLM-999].", "wrong-ref", "CLM-999"),
        ("outputs/00_brief_response.md", "По прогнозу конверсия вырастет на 18%.", "unsupported", "18"),
    ],
)
def test_c3_prepare_blocks_unverified_numbers_and_wrong_refs(tmp_path, relative, line, verdict, token):
    copy = shutil.copytree(ROOT / "projects/family-connect", tmp_path / "family-connect")
    receipt = copy / "state/c3_approvals.json"
    receipt_bytes = receipt.read_bytes()
    prev_sha = json.loads(receipt_bytes)["approvals"][-1]["sha256"]

    with (copy / relative).open("a", encoding="utf-8") as stream:
        stream.write(f"\n{line}\n")

    blocked = run("c3_approval", "prepare", copy)
    assert blocked.returncode == 2
    assert not blocked.stdout.strip()
    assert f"[{verdict}] {relative}:" in blocked.stderr
    assert token in blocked.stderr

    rejected_record = approve(copy, prev_sha)
    assert rejected_record.returncode == 2
    assert receipt.read_bytes() == receipt_bytes

    checked = run("c3_approval", "check", copy, "--json")
    assert checked.returncode == 1
    payload = json.loads(checked.stdout)
    assert payload["status"] == "stale"
    assert relative in payload["changed"]
    assert receipt.read_bytes() == receipt_bytes


@pytest.mark.skipif(not (ROOT / "projects/family-connect").is_dir(), reason="Нужен кейс family-connect")
def test_c3_prepare_blocks_stale_ref_and_dup_main_but_preserves_manual_check(tmp_path):
    # 1. Статус check (число с активной ссылкой, требующее смысловой проверки) не блокирует prepare.
    copy_check = shutil.copytree(ROOT / "projects/family-connect", tmp_path / "fc-check")
    strategy_check = copy_check / "outputs/05_product_strategy.md"
    with strategy_check.open("a", encoding="utf-8") as stream:
        stream.write("\nСводный ориентир буфера — 777 единиц [CLM-001].\n")
    verified = run("verify", copy_check, "--files", "05", "--strict", "--json")
    assert verified.returncode == 0
    assert json.loads(verified.stdout)["counts"].get("check", 0) >= 1
    assert len(prepare(copy_check)) == 64

    # 2. Дубль блока «Главное» (dup-main) в документе периметра C3 блокирует prepare через verify.
    copy_dup = shutil.copytree(ROOT / "projects/family-connect", tmp_path / "fc-dup")
    with (copy_dup / "outputs/03_situation.md").open("a", encoding="utf-8") as stream:
        stream.write("\n## Главное\nПовторный блок вывода.\n")
    blocked_dup = run("c3_approval", "prepare", copy_dup)
    assert blocked_dup.returncode == 2
    assert "[dup-main] outputs/03_situation.md:" in blocked_dup.stderr

    # 3. Ссылка на отозванное утверждение (stale-ref) блокирует prepare.
    copy_stale = shutil.copytree(ROOT / "projects/family-connect", tmp_path / "fc-stale")
    claims = copy_stale / "state/ledger/claims.yaml"
    claims_text = claims.read_text(encoding="utf-8")
    claims.write_text(
        claims_text
        + "\n- id: CLM-998\n  statement: Отозванный учебный тезис.\n  type: interpretation\n"
        + "  derived_from: [CLM-001]\n"
        + "  confidence:\n    level: low\n    rationale: учебный контроль\n  status: retracted\n",
        encoding="utf-8",
    )
    with (copy_stale / "outputs/05_product_strategy.md").open("a", encoding="utf-8") as stream:
        stream.write("\nСверка со старой записью [CLM-998].\n")
    blocked_stale = run("c3_approval", "prepare", copy_stale)
    assert blocked_stale.returncode == 2
    assert "[stale-ref] outputs/05_product_strategy.md:" in blocked_stale.stderr
    assert "CLM-998" in blocked_stale.stderr

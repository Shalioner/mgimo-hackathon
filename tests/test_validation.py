from __future__ import annotations

import json
import pathlib
import shutil
import subprocess
import sys

import pytest

from strategy_checks import validation

ROOT = pathlib.Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "tests" / "fixtures" / "validation"
ALLOWED_STATUSES = {"ok", "warn", "error", "not_assessed"}


def _assert_contract(findings):
    assert isinstance(findings, list) and len(findings) >= 1
    for item in findings:
        assert set(item.keys()) == {"code", "status", "message", "file", "line", "ids"}
        assert item["status"] in ALLOWED_STATUSES
        assert isinstance(item["line"], int)
        assert isinstance(item["ids"], list)


def test_ok_v2_passes():
    findings = validation.check(FIXTURES / "ok_v2")
    _assert_contract(findings)
    assert not any(f["status"] == "error" for f in findings)
    disc = [f for f in findings if f["code"] == "validation.discovery_ok"]
    assert len(disc) == 1
    assert "не основание для масштабирования" in disc[0]["message"].lower()
    causal = [f for f in findings if f["code"] == "validation.causal_ok"]
    assert len(causal) == 1


def test_discovery_only_n6_passes_with_scale_warning_note():
    findings = validation.check(FIXTURES / "discovery_only")
    _assert_contract(findings)
    assert not any(f["status"] == "error" for f in findings)
    disc = [f for f in findings if f["code"] == "validation.discovery_ok"]
    assert len(disc) == 1
    assert "не основание для масштабирования" in disc[0]["message"].lower()


@pytest.fixture
def rehearsal(tmp_path):
    source = ROOT / "projects" / "rehearsal-mobile-spend"
    project = tmp_path / "rehearsal"
    shutil.copytree(source, project)
    return project


def _consistency(project):
    return subprocess.run(
        [sys.executable, str(ROOT / "tools" / "consistency.py"), str(project), "--json"],
        capture_output=True, text=True, check=False,
    )


def _replace(path, old, new):
    original = path.read_text(encoding="utf-8")
    assert original.count(old) == 1
    path.write_text(original.replace(old, new), encoding="utf-8")


def test_consistency_blocks_changed_discovery_threshold(rehearsal):
    project = rehearsal
    path = project / "outputs" / "07_validation_plan.md"
    original = path.read_text(encoding="utf-8")
    marker = "0.50 — ориентир только для учебной базы"
    assert original.count(marker) == 1

    def check_with(threshold):
        path.write_text(original.replace(marker, f"{threshold} — ориентир только для учебной базы"),
                        encoding="utf-8")
        return _consistency(project)

    assert check_with("0.50").returncode == 0
    assert check_with("50%").returncode == 0
    bad = check_with("0.90")
    assert bad.returncode == 1
    assert "validation.stop_go_threshold_mismatch" in bad.stdout
    assert "EXP-003" in bad.stdout
    assert check_with("0.50").returncode == 0
    ledger = project / "state/ledger/experiments.yaml"
    _replace(ledger, "граница 0.50", "граница 0.90")
    bad = check_with("0.50")
    assert bad.returncode == 1
    assert "validation.stop_go_threshold_mismatch" in bad.stdout
    bad = check_with("0.90")
    assert bad.returncode == 1
    findings = json.loads(bad.stdout)
    mismatches = [f for f in findings if f["code"] == "validation.economic_stop_go_mismatch"]
    assert len(mismatches) == 2
    for mismatch in mismatches:
        assert mismatch["ids"] == ["EXP-003"]
        assert mismatch["file"] == "outputs/07_validation_plan.md"
        assert path.read_text(encoding="utf-8").splitlines()[mismatch["line"] - 1].startswith("| EXP-003 |")
        assert all(value in mismatch["message"] for value in ("0.90", "0.5", "state/economics.yaml", "timely"))

    _replace(project / "state/economics.yaml", "stop_go_experiment: EXP-003\n", "")
    assert _consistency(project).returncode == 0


def test_economic_link_leaves_independent_threshold_for_same_decision(rehearsal):
    plan = rehearsal / "outputs/07_validation_plan.md"
    ledger = rehearsal / "state/ledger/experiments.yaml"
    _replace(plan, "при преимуществе от 2 сценариев", "при преимуществе от 75%")
    _replace(ledger, "хотя бы 2 дополнительных своевременных действия", "хотя бы 0.75 дополнительных действий")
    assert _consistency(rehearsal).returncode == 0


@pytest.mark.parametrize(("relative_path", "old", "new", "diagnostic", "eid"), [
    ("state/economics.yaml", "stop_go_experiment: EXP-003", "stop_go_experiment: EXP-999",
     "активная запись", "EXP-999"),
    ("state/economics.yaml", "stop_go_experiment: EXP-003", "stop_go_experiment: null",
     "Некорректный stop_go_experiment", None),
    ("state/economics.yaml", "decision: DEC-001", "decision: DEC-002",
     "Решение модели 'DEC-002', ожидается решение EXP 'DEC-001'", "EXP-003"),
    ("state/economics.yaml", "breakeven_var: timely", "",
     "явно заданный драйвер breakeven_var", "EXP-003"),
    ("state/economics.yaml", "breakeven_var: timely", "breakeven_var: missing",
     "не найден в drivers", "EXP-003"),
    ("state/economics.yaml", "high: 0.70", "high: 2.0",
     "найдена безубыточность 0.5 при входах", "EXP-003"),
    ("state/economics.yaml", "formula: opportunities * timely * avoided_loss - combined_cost",
     "formula: opportunities * avoided_loss - combined_cost",
     "не рассчитана точка безубыточности", "EXP-003"),
    ("outputs/07_validation_plan.md", "| EXP-003 | discovery |", "| EXP-002, EXP-003 | discovery |",
     "ожидается одна отдельная строка", "EXP-003"),
    ("outputs/07_validation_plan.md", "0.50 — ориентир", "0.50 или 1.00 — ориентир",
     "нужен один однозначный долевой порог", "EXP-003"),
    ("outputs/07_validation_plan.md", "0.50 — ориентир", "0.50-0.90 — ориентир",
     "нужен один однозначный долевой порог", "EXP-003"),
    ("outputs/07_validation_plan.md", "0.50 — ориентир", "−50% — ориентир",
     "нужен один однозначный долевой порог", "EXP-003"),
    ("state/ledger/experiments.yaml", "граница 0.50", "граница 0.50 или 0.90",
     "нужен один однозначный долевой порог", "EXP-003"),
    ("state/ledger/experiments.yaml", "- id: EXP-003\n", "- id: EXP-003\n  status: retracted\n",
     "активная запись", "EXP-003"),
])
def test_explicit_economic_link_fails_when_unverifiable(rehearsal, relative_path, old, new, diagnostic, eid):
    _replace(rehearsal / relative_path, old, new)
    result = _consistency(rehearsal)
    assert result.returncode == 1, result.stderr
    findings = json.loads(result.stdout)
    error = next(f for f in findings if f["code"] == "validation.economic_stop_go_unverifiable")
    assert error["status"] == "error"
    assert error["ids"] == ([eid] if eid else [])
    assert diagnostic in error["message"]
    assert "state/economics.yaml" in error["message"]


def test_linked_threshold_uses_current_economic_inputs(rehearsal):
    _replace(rehearsal / "state/economics.yaml", "base: 4500", "base: 5400")
    errors = [f for f in validation.check(rehearsal) if f["status"] == "error"]
    assert len(errors) == 2
    assert all(f["code"] == "validation.economic_stop_go_mismatch" for f in errors)
    assert all("найден порог 0.50, ожидалось 0.6 для timely" in f["message"] for f in errors)


def test_linked_threshold_accepts_proportion_endpoint(rehearsal):
    _replace(rehearsal / "state/economics.yaml", "base: 4500", "base: 9000")
    _replace(rehearsal / "outputs/07_validation_plan.md", "0.50 — ориентир", "1.00 — ориентир")
    _replace(rehearsal / "state/ledger/experiments.yaml", "граница 0.50", "граница 100%")
    assert _consistency(rehearsal).returncode == 0


def test_linked_threshold_accepts_rounding_shown_by_economics(rehearsal):
    _replace(rehearsal / "state/economics.yaml", "base: 4500", "base: 3000")
    plan = rehearsal / "outputs/07_validation_plan.md"
    ledger = rehearsal / "state/ledger/experiments.yaml"
    _replace(plan, "0.50 — ориентир", "0.333333 — ориентир")
    _replace(ledger, "граница 0.50", "граница 33.3333%")
    assert _consistency(rehearsal).returncode == 0

    _replace(plan, "0.333333 — ориентир", "0.333334 — ориентир")
    _replace(ledger, "граница 33.3333%", "граница 33.3334%")
    result = _consistency(rehearsal)
    assert result.returncode == 1
    assert "validation.economic_stop_go_mismatch" in result.stdout


def test_stop_proportion_leaves_other_units_and_multiple_thresholds_for_review():
    assert validation._single_stop_proportion("Stop при 0,50 [CLM-002]") == validation._single_stop_proportion("50%")
    assert validation._single_stop_proportion("Stop при 0.50 поездок") is None
    assert validation._single_stop_proportion("Stop при 0.50, go при 60%") is None


def test_bad_causal_no_denom_and_no_mde_errors():
    findings = validation.check(FIXTURES / "bad_causal_no_denom")
    _assert_contract(findings)
    codes = {f["code"] for f in findings if f["status"] == "error"}
    assert "validation.missing_metric_ratio" in codes
    assert "validation.missing_mde" in codes


def test_bad_causal_short_window_and_missing_n_errors():
    findings = validation.check(FIXTURES / "bad_causal_short_window")
    _assert_contract(findings)
    codes = {f["code"] for f in findings if f["status"] == "error"}
    assert "validation.missing_sample_size" in codes
    assert "validation.window_shorter_than_cycle" in codes
    assert "validation.missing_breakeven_stop" in codes


def test_esim_exp002_flagged_as_warn_not_error():
    findings = validation.check(ROOT / "projects" / "esim-travel")
    _assert_contract(findings)
    assert not any(f["status"] == "error" for f in findings)
    flagged = [f for f in findings if "EXP-002" in f["ids"] and f["status"] == "warn"]
    assert len(flagged) == 1
    assert flagged[0]["line"] == 15
    assert "3 мес" in flagged[0]["message"]


def test_legacy_ryadom_and_example_do_not_false_error():
    for name in ("ryadom", "example"):
        findings = validation.check(ROOT / "projects" / name)
        _assert_contract(findings)
        assert not any(f["status"] == "error" for f in findings)
        assert any(f["status"] == "not_assessed" for f in findings)


def test_missing_project_returns_not_assessed(tmp_path):
    findings = validation.check(tmp_path)
    _assert_contract(findings)
    assert findings[0]["status"] == "not_assessed"


def test_cli_json_and_exit_codes(tmp_path):
    val_py = str(ROOT / "tools" / "strategy_checks" / "validation.py")
    bad = subprocess.run(
        [sys.executable, val_py, str(FIXTURES / "bad_causal_no_denom"), "--json"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    assert bad.returncode == 1
    assert any(item["status"] == "error" for item in json.loads(bad.stdout))

    ok = subprocess.run(
        [sys.executable, val_py, str(FIXTURES / "ok_v2"), "--json"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    assert ok.returncode == 0
    assert not any(item["status"] == "error" for item in json.loads(ok.stdout))


def test_validation_partial_v2_empty_table_units_sample_size_and_history_cutoff(tmp_path: pathlib.Path):
    # 1) Partial v2 causal table without Метрика/MDE/Цикл must fail (NOT fall back to legacy warn)
    proj_partial = tmp_path / "partial"
    (proj_partial / "outputs").mkdir(parents=True)
    (proj_partial / "outputs" / "07_validation_plan.md").write_text(
        "# План проверки\n\n"
        "| EXP | Тип | Проверяет | Ожидаемое изменение | Срок | Stop / Go |\n"
        "|---|---|---|---|---|---|\n"
        "| EXP-001 | causal | HYP-001 | Рост D7 | 14 дн | stop < 20% |\n",
        encoding="utf-8",
    )
    res_p = validation.check(proj_partial)
    _assert_contract(res_p)
    assert any(f["status"] == "error" for f in res_p)

    # 2) Partial table with MDE but without Тип must also fail
    (proj_partial / "outputs" / "07_validation_plan.md").write_text(
        "# План проверки\n\n"
        "| EXP | Проверяет | База | MDE | n на группу | Срок | Stop / Go |\n"
        "|---|---|---|---|---|---|---|\n"
        "| EXP-001 | HYP-001 | 20% | +3 п.п. | 3000 | 14 дн | stop |\n",
        encoding="utf-8",
    )
    res_no_type = validation.check(proj_partial)
    assert any(f["status"] == "error" and f["code"] == "validation.unknown_type" for f in res_no_type)

    # 3) Empty v2 table must return validation.empty_table error (never [])
    (proj_partial / "outputs" / "07_validation_plan.md").write_text(
        "# План проверки\n\n"
        "| EXP | Тип | Проверяет | Метрика (числитель / знаменатель) | С чем сравниваем | База | MDE | n на группу | Срок / окно | Цикл поведения | Stop / Go (от M08) | Ответственный |\n"
        "|---|---|---|---|---|---|---|---|---|---|---|---|\n",
        encoding="utf-8",
    )
    res_empty = validation.check(proj_partial)
    _assert_contract(res_empty)
    assert res_empty[0]["status"] == "error" and res_empty[0]["code"] == "validation.empty_table"

    # 4) Duration parsing ('1 день', '12 часов', '1 квартал'), n=0, underpowered n=100, missing comparison, and history cutoff
    (proj_partial / "outputs" / "07_validation_plan.md").write_text(
        "# План проверки\n\n"
        "| EXP | Тип | Проверяет | Метрика (числитель / знаменатель) | С чем сравниваем | База | MDE | n на группу | Срок / окно | Цикл поведения | Stop / Go (от M08) | Ответственный |\n"
        "|---|---|---|---|---|---|---|---|---|---|---|---|\n"
        "| EXP-001 | causal | A | удержание / установки | контроль | 0.265 | +3 п.п. | 0 | 1 день | 12 часов | stop < 25% (безубыточность M08) | PM |\n"
        "| EXP-002 | causal | B | покупки / визиты | | 26.5% | +3 п.п. | 100 | 1 квартал | 1 месяц | stop < 20% (безубыточность M08) | PM |\n\n"
        "## Исторический снимок до CHG-001\n\n"
        "| EXP | Тип | Проверяет |\n|---|---|---|\n| EXP-999 | unknown | ignored |\n",
        encoding="utf-8",
    )
    res_complex = validation.check(proj_partial)
    codes = {f["code"] for f in res_complex if f["status"] == "error"}
    ids_with_err = {eid for f in res_complex if f["status"] == "error" for eid in f["ids"]}
    assert "validation.missing_sample_size" in codes  # n=0
    assert "validation.underpowered_sample_size" in codes  # n=100 < 3516
    assert "validation.missing_comparison" in codes  # empty comparison on EXP-002
    assert "validation.missing_behavior_cycle" not in codes  # 12 часов & 1 месяц recognized
    assert "validation.missing_window" not in codes  # 1 день & 1 квартал recognized
    assert "EXP-999" not in ids_with_err  # cutoff at Исторический снимок


def test_validation_integrated_template_e2e(tmp_path: pathlib.Path):
    unfilled = tmp_path / "unfilled"
    (unfilled / "outputs").mkdir(parents=True)
    patched_tpl = (ROOT / "knowledge" / "templates" / "07_validation_plan.md").read_text(encoding="utf-8")
    assert "С чем сравниваем" in patched_tpl
    (unfilled / "outputs" / "07_validation_plan.md").write_text(patched_tpl, encoding="utf-8")
    bad = validation.check(unfilled)
    _assert_contract(bad)
    assert bad[0]["status"] == "error"

    filled = tmp_path / "filled"
    (filled / "outputs").mkdir(parents=True)
    row = "| EXP-001 | causal | HYP-001 | активные на D7 / все установки | контрольная группа (старый онбординг) | 26.5% | +3 п.п. | 3516 | 14 дней | 1 день | нагрузка | go >= 29.5%, stop < 28.0% (точка безубыточности M08) | PM |\n"
    sep = "|---|---|---|---|---|---|---|---|---|---|---|---|---|\n"
    assert sep in patched_tpl
    (filled / "outputs" / "07_validation_plan.md").write_text(
        patched_tpl.replace(sep, sep + row, 1),
        encoding="utf-8",
    )
    good = validation.check(filled)
    _assert_contract(good)
    assert all(f["status"] == "ok" for f in good)


def test_carbon_n2_comparison_column_required(tmp_path):
    original = (FIXTURES / "ok_v2/outputs/07_validation_plan.md").read_text()
    (tmp_path / "outputs").mkdir()
    lines = []
    for line in original.splitlines():
        if line.startswith("|"):
            cells = line.split("|")
            del cells[5]  # С чем сравниваем после EXP/Тип/Проверяет/Метрика
            line = "|".join(cells)
        lines.append(line)
    (tmp_path / "outputs/07_validation_plan.md").write_text("\n".join(lines))
    assert any(f["code"] == "validation.missing_comparison" for f in validation.check(tmp_path))


def test_carbon_n3_continuous_metric_and_economic_stop(tmp_path):
    assert validation._parse_baseline_proportion("0.45 поездок") is None
    assert validation._parse_mde_proportion("+0.05 поездок") is None
    assert validation._parse_baseline_proportion("0.45") == 0.45
    assert validation._parse_baseline_proportion("45%") == 0.45
    assert validation._has_breakeven_stop("Stop при conv < 2.09% (по М08)")
    assert validation._has_breakeven_stop("Stop при conv < 2.09%, Go при >= 5%", "Stop / Go (от M08)")
    assert not validation._has_breakeven_stop("Stop при conv < 2.09%")
    (tmp_path / "outputs").mkdir()
    header = "| EXP | Тип | Метрика | С чем сравниваем | База | MDE | n на группу | Срок | Цикл поведения | Stop / Go (от М08) |"
    separator = "|---|---|---|---|---|---|---|---|---|---|"
    row = "| EXP-001 | causal | Число поездок / Активные пользователи | контроль | 0.45 поездок | +0.05 поездок | 300 | 35 дней | 30 дней | Stop < 0.2 |"
    (tmp_path / "outputs/07_validation_plan.md").write_text("\n".join([header, separator, row]))
    assert not any(f["status"] == "error" for f in validation.check(tmp_path))

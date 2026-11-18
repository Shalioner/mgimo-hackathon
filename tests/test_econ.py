"""Тесты M08: расчёт экономической модели (tools/econ.py) и проверка стратегии (strategy_checks/econ.py)."""
from __future__ import annotations

import json
import math
import pathlib
import subprocess
import sys

import yaml

import econ as econ_calc
from strategy_checks import econ as econ_check

ROOT = pathlib.Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "tests" / "fixtures" / "econ"
VALID_STATUSES = {"ok", "warn", "error", "not_assessed"}


def _assert_contract(items: list[dict]) -> None:
    assert isinstance(items, list) and items
    for item in items:
        assert set(item.keys()) == {"code", "status", "message", "file", "line", "ids"}
        assert isinstance(item["code"], str) and item["code"]
        assert item["status"] in VALID_STATUSES
        assert isinstance(item["message"], str) and item["message"]
        assert isinstance(item["file"], str)
        assert isinstance(item["line"], int)
        assert isinstance(item["ids"], list)


def test_econ_calculator_matches_exact_reference() -> None:
    spec = yaml.safe_load((FIXTURES / "reference_drivers.yaml").read_text(encoding="utf-8"))
    res = econ_calc.compute_model(spec, claim_id="CLM-901")

    # Exact closed-form expectations:
    # base = 2000 * 0.10 * 300 * 1.5 - 50000 = 40000
    # pessimistic = 1000 * 0.05 * 200 * 1.0 - 70000 = -60000
    # optimistic = 3000 * 0.20 * 400 * 2.0 - 40000 = 440000
    # breakeven for conv: 2000 * x * 300 * 1.5 - 50000 = 0 => x = 50000 / 900000 = 1/18
    # tornado swings at base:
    #   conv: 2000 * (0.20 - 0.05) * 300 * 1.5 = 135000
    #   base: (3000 - 1000) * 0.10 * 300 * 1.5 = 90000
    #   price: 2000 * 0.10 * (400 - 200) * 1.5 = 60000
    #   retention: 2000 * 0.10 * 300 * (2.0 - 1.0) = 60000
    #   cost: 70000 - 40000 = 30000
    assert math.isclose(res["scenarios"]["base"], 40000.0, rel_tol=0.0, abs_tol=1e-9)
    assert math.isclose(res["scenarios"]["pessimistic"], -60000.0, rel_tol=0.0, abs_tol=1e-9)
    assert math.isclose(res["scenarios"]["optimistic"], 440000.0, rel_tol=0.0, abs_tol=1e-9)
    assert res["breakeven"] is not None
    assert math.isclose(res["breakeven"]["value"], 50000.0 / 900000.0, rel_tol=0.0, abs_tol=1e-9)
    assert res["top_3"] == ["conv", "base", "price"]  # price == retention (60000), alphabetical tie-break
    assert [t["id"] for t in res["tornado"]] == ["conv", "base", "price", "retention", "cost"]
    assert "решение меняется, если conv <" in res["decision_threshold"]

    clm = res["derived_claim"]
    assert clm["id"] == "CLM-901"
    assert clm["type"] == "forecast"
    # CLM-001 appears twice in drivers -> deduplicated
    assert clm["derived_from"] == ["CLM-001", "HYP-001", "CLM-002", "HYP-002"]


def test_econ_ryadom_data_produces_nonempty_model_readonly() -> None:
    ryadom_ledger_before = (
        ROOT / "projects" / "ryadom" / "state" / "ledger" / "claims.yaml"
    ).read_bytes()

    spec = yaml.safe_load((FIXTURES / "ryadom_drivers.yaml").read_text(encoding="utf-8"))
    res = econ_calc.compute_model(spec)
    assert res["scenarios"]["pessimistic"] < res["scenarios"]["base"] < res["scenarios"]["optimistic"]
    assert res["breakeven"] is not None
    assert 0.0 < res["breakeven"]["value"] < 1.0
    assert len(res["top_3"]) == 3
    assert set(["CLM-019", "CLM-020", "CLM-021"]).issubset(set(res["derived_claim"]["derived_from"]))

    # Verify read-only: ledger untouched
    ryadom_ledger_after = (
        ROOT / "projects" / "ryadom" / "state" / "ledger" / "claims.yaml"
    ).read_bytes()
    assert ryadom_ledger_before == ryadom_ledger_after


def test_econ_check_ok_and_error_cases() -> None:
    ok_findings = econ_check.check(FIXTURES / "ok_paid")
    _assert_contract(ok_findings)
    assert all(f["status"] == "ok" for f in ok_findings)

    few_findings = econ_check.check(FIXTURES / "bad_few_drivers")
    _assert_contract(few_findings)
    assert any(f["status"] == "error" and f["code"] == "econ.insufficient_drivers" for f in few_findings)

    paid_bad = econ_check.check(FIXTURES / "bad_paid_no_anchors")
    _assert_contract(paid_bad)
    paid_codes = {f["code"] for f in paid_bad if f["status"] == "error"}
    assert "econ.missing_cannibalization" in paid_codes
    assert "econ.missing_price_anchors" in paid_codes


def test_econ_check_legacy_projects_not_assessed() -> None:
    for proj in ("ryadom", "esim-travel", "example"):
        findings = econ_check.check(ROOT / "projects" / proj)
        _assert_contract(findings)
        assert all(f["status"] == "not_assessed" for f in findings)


def test_econ_clis_json_and_exit_codes() -> None:
    calc = subprocess.run(
        [sys.executable, str(ROOT / "tools" / "econ.py"), str(FIXTURES / "reference_drivers.yaml"), "--json"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert calc.returncode == 0
    calc_json = json.loads(calc.stdout)
    assert calc_json["top_3"] == ["conv", "base", "price"]

    bad_cli = subprocess.run(
        [sys.executable, str(ROOT / "tools" / "strategy_checks" / "econ.py"), str(FIXTURES / "bad_few_drivers"), "--json"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert bad_cli.returncode == 1
    _assert_contract(json.loads(bad_cli.stdout))

    leg_cli = subprocess.run(
        [sys.executable, str(ROOT / "tools" / "strategy_checks" / "econ.py"), str(ROOT / "projects" / "ryadom"), "--json"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert leg_cli.returncode == 0
    leg_data = json.loads(leg_cli.stdout)
    assert all(x["status"] == "not_assessed" for x in leg_data)


def test_econ_ast_rejects_complex_and_overflow_and_solves_denominator_breakeven() -> None:
    import pytest

    for bad_expr in ("(-2) ** 0.5 + a", "(10 ** 20) ** 20 + a", "1e308 * 1e308 + a"):
        spec = {
            "formula": bad_expr,
            "drivers": [{"id": "a", "low": 1.0, "base": 2.0, "high": 3.0, "type": "fact", "refs": ["CLM-001"]}],
        }
        with pytest.raises(ValueError):
            econ_calc.compute_model(spec)

    # Denominator driver: LTV = arpu * margin / churn - cac, root at churn = 200*0.6/1800 = 0.0666667
    denom_spec = {
        "formula": "arpu * margin / churn - cac",
        "breakeven_var": "churn",
        "drivers": [
            {"id": "arpu", "low": 150.0, "base": 200.0, "high": 250.0, "type": "fact", "refs": ["CLM-001"]},
            {"id": "margin", "low": 0.5, "base": 0.6, "high": 0.7, "type": "assumption"},
            {"id": "churn", "low": 0.03, "base": 0.08, "high": 0.12, "type": "assumption"},
            {"id": "cac", "low": 1500.0, "base": 1800.0, "high": 2100.0, "type": "fact", "refs": ["CLM-002"]},
        ],
    }
    res = econ_calc.compute_model(denom_spec)
    assert res["breakeven"] is not None
    assert math.isclose(res["breakeven"]["value"], 120.0 / 1800.0, rel_tol=0.0, abs_tol=1e-7)

    # Pole (no real root, only sign jump across 0): must NOT return fake pole root
    pole_spec = {
        "formula": "1 / x",
        "breakeven_var": "x",
        "drivers": [{"id": "x", "low": -1.0, "base": 0.5, "high": 1.0, "type": "assumption"}],
    }
    pole_res = econ_calc.compute_model(pole_spec)
    assert pole_res["breakeven"] is None


def test_econ_ungrounded_forecast_not_insertable() -> None:
    spec = {
        "formula": "a * b - c",
        "drivers": [
            {"id": "a", "low": 10.0, "base": 20.0, "high": 30.0, "type": "assumption"},
            {"id": "b", "low": 1.0, "base": 2.0, "high": 3.0, "type": "assumption"},
            {"id": "c", "low": 5.0, "base": 10.0, "high": 15.0, "type": "assumption"},
        ],
    }
    res = econ_calc.compute_model(spec, claim_id="CLM-999")
    assert res["insertable"] is False
    assert res["derived_claim"] is None
    assert res["draft_claim"] is not None
    assert "derived_from" not in res["draft_claim"]


def test_econ_russian_bounds_anchors_and_empty_situation_rejected(tmp_path: pathlib.Path) -> None:
    proj = tmp_path / "case_ru"
    (proj / "outputs").mkdir(parents=True)

    # Empty 03_situation with template placeholders must fail
    (proj / "outputs" / "03_situation.md").write_text(
        "# Ситуация\n\n## Размер возможности\n"
        "<!-- assumption -->\n"
        "- **Формула снизу вверх:** `база × достижимость` = …\n"
        "- Множитель 1 (…): значение [CLM-…] (fact / assumption)\n"
        "- Множитель 2 (…): значение [CLM-…] (fact / assumption)\n"
        "- Множитель 3 (…): значение [CLM-…] (fact / assumption)\n",
        encoding="utf-8",
    )
    # Valid 05 with Russian driver headers, nested anchors, but mismatched units
    (proj / "outputs" / "05_product_strategy.md").write_text(
        "# Продуктовая стратегия\n\n## Экономическая модель\n"
        "- **Формула драйверов:** `base * conv * price - cost`\n"
        "| Драйвер | Переменная | Мин | База | Макс | Тип | Основание |\n"
        "|---|---|---|---|---|---|---|\n"
        "| База | `base` | 100 | 200 | 300 | fact | [CLM-001] |\n"
        "| Конверсия | `conv` | 0.1 | 0.2 | 0.3 | assumption | оценка |\n"
        "| Затраты | `cost` | 1000 | 1500 | 2000 | fact | [CLM-002] |\n\n"
        "- **Точка безубыточности:** `conv = 0.08`\n"
        "- **Топ-3 драйвера по влиянию (tornado):** 1) conv 2) base 3) cost\n"
        "- **Решение меняется, если:** `conv < 0.08`\n"
        "- **Каннибализация** *(для платного ALT)*: `0.05 * 100 = 5`\n"
        "- **Ценовые якоря** *(платный ALT)*:\n"
        "  1. Конкурент А — 299 ₽/мес (2026-10-15, [CLM-003])\n"
        "  2. Конкурент Б — 2400 ₽/год (2026-10-16, [CLM-004])\n",
        encoding="utf-8",
    )
    findings = econ_check.check(proj)
    _assert_contract(findings)
    codes = {f["code"] for f in findings if f["status"] == "error"}
    assert "econ.opportunity_size_incomplete" in codes
    assert "econ.insufficient_price_anchors" in codes  # unit mismatch ₽/мес vs ₽/год

    # Fix units -> 05 passes, fix 03 -> 03 passes
    (proj / "outputs" / "05_product_strategy.md").write_text(
        (proj / "outputs" / "05_product_strategy.md")
        .read_text(encoding="utf-8")
        .replace("2400 ₽/год", "199 ₽/мес"),
        encoding="utf-8",
    )
    (proj / "outputs" / "03_situation.md").write_text(
        "# Ситуация\n\n## Размер возможности\n"
        "- **Формула снизу вверх:** `база × достижимость × чек` = 6 млн ₽/мес\n"
        "- Множитель 1 (база): 100 000 абонентов [CLM-001]\n"
        "- Множитель 2 (конверсия): 20 % (assumption: по аналогу)\n"
        "- Множитель 3 (чек): 300 ₽/мес [CLM-002]\n",
        encoding="utf-8",
    )
    ok_findings = econ_check.check(proj)
    _assert_contract(ok_findings)
    assert all(f["status"] == "ok" for f in ok_findings)


def test_econ_integrated_templates_e2e(tmp_path: pathlib.Path) -> None:
    # Apply M08.patch in temporary copy of knowledge/templates and test unfilled vs filled
    unfilled = tmp_path / "unfilled_proj"
    (unfilled / "outputs").mkdir(parents=True)
    (unfilled / "outputs" / "03_situation.md").write_text(
        (ROOT / "knowledge" / "templates" / "03_situation.md").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    (unfilled / "outputs" / "05_product_strategy.md").write_text(
        (ROOT / "knowledge" / "templates" / "05_product_strategy.md").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    bad_res = econ_check.check(unfilled)
    assert any(f["status"] == "error" for f in bad_res)

    filled = tmp_path / "filled_proj"
    (filled / "outputs").mkdir(parents=True)
    (filled / "outputs" / "03_situation.md").write_text(
        "# Ситуация\n\n## Размер возможности\n"
        "- **Формула снизу вверх:** `1000 * 0.2 * 299` = 59 800 ₽\n"
        "- Множитель 1 (аудитория): 1000 семей [CLM-001]\n"
        "- Множитель 2 (достижимость): 20 % (assumption: оценка пилота)\n"
        "- Множитель 3 (чек): 299 ₽/мес [CLM-002]\n",
        encoding="utf-8",
    )
    (filled / "outputs" / "05_product_strategy.md").write_text(
        "# Продуктовая стратегия\n\n## Экономическая модель\n"
        "- **Формула драйверов:** `base * conv * price - cost`\n\n"
        "| Драйвер | Переменная | Low | Base | High | Тип | Основание |\n"
        "|---|---|---|---|---|---|---|\n"
        "| База | `base` | 1000 | 2000 | 3000 | fact | [CLM-001] |\n"
        "| Конверсия | `conv` | 0.05 | 0.10 | 0.20 | assumption | гипотеза [HYP-001] |\n"
        "| Цена | `price` | 200 | 299 | 400 | fact | [CLM-002] |\n"
        "| Затраты | `cost` | 30000 | 40000 | 50000 | fact | [CLM-003] |\n\n"
        "- **Точка безубыточности:** `conv = 0.067`\n"
        "- **Топ-3 драйвера по влиянию (tornado):** 1) conv 2) base 3) price\n"
        "- **Решение меняется, если:** `conv < 0.067`\n"
        "- **Каннибализация** *(обязательно для платного ALT)*: `0.05 * 200 * 2000 - 40000` = 0\n"
        "- **Ценовые якоря** *(обязательно для платного ALT, ≥ 2 датированные цены конкурентов в одной единице)*:\n"
        "  1. Конкурент А — 299 ₽/мес (2026-10-15, [CLM-004])\n"
        "  2. Конкурент Б — 199 ₽/мес (2026-10-15, [CLM-005])\n",
        encoding="utf-8",
    )
    good_res = econ_check.check(filled)
    _assert_contract(good_res)
    assert all(f["status"] == "ok" for f in good_res)

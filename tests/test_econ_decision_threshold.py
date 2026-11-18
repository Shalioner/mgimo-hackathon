"""CLI и модельные тесты порога решения (B2 / вход M) для tools/econ.py."""
from __future__ import annotations

import json
import math
import pathlib
import subprocess
import sys

import yaml

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import econ as econ_calc  # noqa: E402

B2_YAML_DIR = ROOT / "evals" / "score9" / "design-round" / "b" / "b2" / "yaml"


def _run_econ_cli(yaml_path: pathlib.Path) -> dict:
    proc = subprocess.run(
        [sys.executable, str(ROOT / "tools" / "econ.py"), str(yaml_path), "--json"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, f"stderr: {proc.stderr}\nstdout: {proc.stdout}"
    return json.loads(proc.stdout)


def test_cli_m_healthy_switch_and_sides_and_tie() -> None:
    res = _run_econ_cli(B2_YAML_DIR / "m_healthy_switch.yaml")
    assert res["unit"] == "у.е./квартал"
    assert res["scenarios"] == {"pessimistic": 1000.0, "base": 3000.0, "optimistic": 5000.0}
    assert res["threshold_status"] == "in_range"
    be = res["breakeven"]
    assert be is not None
    assert be["driver"] == "m"
    assert be["unit"] == "у.е./продление"
    assert math.isclose(be["value"], 5.0, abs_tol=1e-9)
    assert be["direction"] == "below_breakeven"
    assert be["in_checked_range"] is True
    assert be["range_status"] == "in_range"
    assert be["checked_range"] == [4.0, 8.0]
    assert res["decision_threshold"] == "решение меняется, если m < 5"

    # Проверка знака по обе стороны порога m=5 и ничьей в m=5 (цель status quo = 2000)
    spec = yaml.safe_load((B2_YAML_DIR / "m_healthy_switch.yaml").read_text(encoding="utf-8"))
    for m_val, expected_contrib in ((4.9, 1900.0), (5.0, 2000.0), (5.1, 2100.0)):
        s = yaml.safe_load(yaml.safe_dump(spec))
        for d in s["drivers"]:
            if d["id"] == "m":
                d["base"] = m_val
        r = econ_calc.compute_model(s)
        assert math.isclose(r["scenarios"]["base"], expected_contrib, abs_tol=1e-9)


def test_cli_m_narrow_range_separates_theoretical_threshold_from_checked_range() -> None:
    res = _run_econ_cli(B2_YAML_DIR / "m_narrow_stable.yaml")
    assert res["scenarios"] == {"pessimistic": 3000.0, "base": 3000.0, "optimistic": 5000.0}
    assert res["threshold_status"] == "out_of_range"
    be = res["breakeven"]
    assert be is not None
    assert math.isclose(be["value"], 5.0, abs_tol=1e-9)
    assert be["direction"] == "below_breakeven"
    assert be["in_checked_range"] is False
    assert be["range_status"] == "below_range"
    assert be["checked_range"] == [6.0, 8.0]
    assert res["decision_threshold"] == "найденный порог m = 5 вне диапазона [6…8]"
    assert res["decision_threshold"] in res["draft_claim"]["statement"]


def test_cli_m_zero_threshold_in_and_out_of_range_and_unhealthy_sq() -> None:
    res_in = _run_econ_cli(B2_YAML_DIR / "m_zero_threshold.yaml")
    assert res_in["scenarios"] == {"pessimistic": -1000.0, "base": 3000.0, "optimistic": 5000.0}
    assert res_in["threshold_status"] == "in_range"
    assert math.isclose(res_in["breakeven"]["value"], 3.0, abs_tol=1e-9)
    assert res_in["breakeven"]["in_checked_range"] is True
    assert res_in["decision_threshold"] == "решение меняется, если m < 3"

    # Проверка контрастных значений m = 2.5, 3.0, 3.5 относительно порога 0 (Kill)
    spec = yaml.safe_load((B2_YAML_DIR / "m_zero_threshold.yaml").read_text(encoding="utf-8"))
    for m_val, expected_contrib in ((2.5, -500.0), (3.0, 0.0), (3.5, 500.0)):
        s = yaml.safe_load(yaml.safe_dump(spec))
        for d in s["drivers"]:
            if d["id"] == "m":
                d["base"] = m_val
        r = econ_calc.compute_model(s)
        assert math.isclose(r["scenarios"]["base"], expected_contrib, abs_tol=1e-9)

    res_out = _run_econ_cli(B2_YAML_DIR / "m_zero_out_of_range.yaml")
    assert res_out["threshold_status"] == "out_of_range"
    assert math.isclose(res_out["breakeven"]["value"], 3.0, abs_tol=1e-9)
    assert res_out["breakeven"]["in_checked_range"] is False
    assert res_out["breakeven"]["range_status"] == "below_range"

    res_sq = _run_econ_cli(B2_YAML_DIR / "m_unhealthy_sq.yaml")
    assert res_sq["threshold_status"] == "in_range"
    assert math.isclose(res_sq["breakeven"]["value"], 2.0, abs_tol=1e-9)
    assert res_sq["breakeven"]["in_checked_range"] is True


def test_cli_m_tie_at_base() -> None:
    res = _run_econ_cli(B2_YAML_DIR / "m_tie_at_base.yaml")
    assert math.isclose(res["scenarios"]["base"], 2000.0, abs_tol=1e-9)
    assert res["threshold_status"] == "in_range"
    assert math.isclose(res["breakeven"]["value"], 5.0, abs_tol=1e-9)
    assert res["decision_threshold"] == "решение меняется, если m < 5"


def test_cli_m_constant_equal_target_does_not_declare_transition(tmp_path: pathlib.Path) -> None:
    res = _run_econ_cli(B2_YAML_DIR / "m_constant_equal_target.yaml")
    assert res["scenarios"] == {"pessimistic": 2000.0, "base": 2000.0, "optimistic": 2000.0}
    assert res["breakeven"] is None
    assert "m" not in res["breakevens"]
    assert res["threshold_status"] == "equal_at_probes"
    assert "решение меняется" not in res["decision_threshold"]
    assert "переход не объявляется" in res["decision_threshold"]

    # Чистая константная формула без breakeven_var и с target=2000
    const_yaml = tmp_path / "pure_const_eq.yaml"
    const_yaml.write_text(
        "name: pure-const\n"
        "formula: '2000'\n"
        "breakeven_target: 2000\n"
        "drivers:\n"
        "  - id: m\n"
        "    low: 4.0\n"
        "    base: 6.0\n"
        "    high: 8.0\n",
        encoding="utf-8",
    )
    res_pure = _run_econ_cli(const_yaml)
    assert res_pure["breakeven"] is None
    assert res_pure["breakevens"] == {}
    assert res_pure["threshold_status"] == "equal_at_probes"
    assert "переход не объявляется" in res_pure["decision_threshold"]


def test_cli_negative_controls_inverse_direction_and_unreached_constant(tmp_path: pathlib.Path) -> None:
    res_inv = _run_econ_cli(B2_YAML_DIR / "m_inverse_direction.yaml")
    assert res_inv["threshold_status"] == "in_range"
    be = res_inv["breakeven"]
    assert be is not None
    assert be["driver"] == "cost"
    assert math.isclose(be["value"], 4000.0, abs_tol=1e-9)
    assert be["direction"] == "above_breakeven"
    assert be["in_checked_range"] is True
    assert res_inv["decision_threshold"] == "решение меняется, если cost > 4000"

    # Отрицательный контроль: постоянная модель строго выше target (не равна target)
    const_above = tmp_path / "const_above.yaml"
    const_above.write_text(
        "name: const-above\n"
        "formula: '0 * m + 3000'\n"
        "breakeven_var: m\n"
        "breakeven_target: 2000\n"
        "drivers:\n"
        "  - id: m\n"
        "    low: 4.0\n"
        "    base: 6.0\n"
        "    high: 8.0\n",
        encoding="utf-8",
    )
    res_above = _run_econ_cli(const_above)
    assert res_above["breakeven"] is None
    assert res_above["threshold_status"] == "not_reached"
    assert "не достигнута" in res_above["decision_threshold"]


def test_outside_sampled_root_does_not_claim_stability_for_nonlinear_formula() -> None:
    model = econ_calc.compute_model({
        "formula": "(m - 1) * (m - 5.123) * (m - 5.124)",
        "breakeven_var": "m",
        "drivers": [{"id": "m", "low": 4, "base": 6, "high": 8}],
    })
    assert model["threshold_status"] == "out_of_range"
    assert model["decision_threshold"] == "найденный порог m = 1 вне диапазона [4…8]"

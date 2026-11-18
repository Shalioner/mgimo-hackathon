from __future__ import annotations

import json
import math
import pathlib
import subprocess
import sys

import pytest

import power

ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_benchmark_sample_size_exact_formula():
    # Эталон M11: p=0.265, MDE=3 п.п. (0.03), alpha=0.05 (two-sided), power=0.80.
    # По формуле Fleiss/Kelsey (pooled null + unpooled alt, эквивалент R power.prop.test):
    # z_{0.975} = 1.959963984540054, z_{0.80} = 0.8416212335729143
    res = power.sample_size_two_proportions(p=0.265, mde=0.03, alpha=0.05, power=0.80)
    z_a = 1.959963984540054
    z_b = 0.8416212335729143
    p1, p2 = 0.265, 0.295
    p_bar = 0.5 * (p1 + p2)
    expected_exact = (
        (z_a * math.sqrt(2 * p_bar * (1 - p_bar)) + z_b * math.sqrt(p1 * (1 - p1) + p2 * (1 - p2)))
        / 0.03
    ) ** 2
    assert abs(res["n_exact"] - expected_exact) < 1e-6
    assert abs(res["n_per_group"] - 3516) <= 1
    assert res["n_per_group"] == 3516
    assert res["n_total"] == 7032
    assert "3515.118954" in (power.__doc__ or "")


def test_sample_size_one_sided_and_negative_mde():
    two = power.sample_size_two_proportions(0.265, 0.03, alpha=0.05, power=0.80, alternative="two-sided")
    one = power.sample_size_two_proportions(0.265, 0.03, alpha=0.05, power=0.80, alternative="one-sided")
    assert one["n_per_group"] < two["n_per_group"]

    down = power.sample_size_two_proportions(0.265, -0.03, alpha=0.05, power=0.80)
    assert down["p2"] == pytest.approx(0.235)
    assert down["n_per_group"] > 0


def test_mde_inverse_round_trip():
    inv = power.mde_two_proportions(p=0.265, n_per_group=3516, alpha=0.05, power=0.80)
    assert abs(inv["mde"] - 0.03) < 5e-5
    assert abs(inv["mde_pp"] - 3.0) < 5e-3

    # Подстановка найденного MDE обратно даёт n_per_group == 3516
    check_n = power.sample_size_two_proportions(p=0.265, mde=inv["mde"], alpha=0.05, power=0.80)
    assert abs(check_n["n_exact"] - 3516.0) < 1e-4

    inv_down = power.mde_two_proportions(p=0.265, n_per_group=3516, direction="down")
    assert inv_down["p2"] < 0.265
    assert inv_down["mde"] > 0


def test_wilson_ci_formula_and_boundaries():
    res = power.wilson_ci(k=265, n=1000, alpha=0.05)
    z = power.norm_ppf(0.975)
    z2 = z * z
    p_hat = 0.265
    denom = 1.0 + z2 / 1000.0
    center = (p_hat + z2 / 2000.0) / denom
    half = z * math.sqrt(p_hat * (1 - p_hat) / 1000.0 + z2 / (4.0 * 1000.0**2)) / denom
    assert res["low"] == pytest.approx(center - half, abs=1e-12)
    assert res["high"] == pytest.approx(center + half, abs=1e-12)
    assert res["low"] < p_hat < res["high"]

    zero = power.wilson_ci(k=0, n=50)
    assert zero["low"] == pytest.approx(0.0, abs=1e-12)
    assert 0.0 < zero["high"] < 0.1

    full = power.wilson_ci(k=50, n=50)
    assert full["high"] == pytest.approx(1.0, abs=1e-12)
    assert 0.9 < full["low"] < 1.0


def test_validation_errors():
    with pytest.raises(ValueError):
        power.sample_size_two_proportions(0.0, 0.03)
    with pytest.raises(ValueError):
        power.sample_size_two_proportions(0.99, 0.02)
    with pytest.raises(ValueError):
        power.mde_two_proportions(0.5, 1)
    with pytest.raises(ValueError):
        power.wilson_ci(11, 10)


def test_power_cli_json(tmp_path: pathlib.Path):
    power_py = str(ROOT / "tools" / "power.py")
    proc = subprocess.run(
        [sys.executable, power_py, "n", "--p", "0.265", "--mde", "0.03", "--json"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=True,
    )
    payload = json.loads(proc.stdout)
    assert payload["n_per_group"] == 3516
    assert payload["formula"] == "fleiss_kelsey_unpooled_alt_normal_approx"

    proc_mde = subprocess.run(
        [sys.executable, power_py, "mde", "--p", "0.265", "--n", "3516", "--json"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=True,
    )
    assert abs(json.loads(proc_mde.stdout)["mde_pp"] - 3.0) < 0.01

    proc_w = subprocess.run(
        [sys.executable, power_py, "wilson", "--k", "265", "--n", "1000", "--json"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=True,
    )
    w_payload = json.loads(proc_w.stdout)
    assert 0.23 < w_payload["low"] < 0.265 < w_payload["high"] < 0.30

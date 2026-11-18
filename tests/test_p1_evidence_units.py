"""Регрессия единиц измерения по реестру и дословным выдержкам."""
from pathlib import Path
import sys

import pytest

import evidence

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "evals/parity/p1_units"))
from controls import CASES, UNIT_WARNING, materialize  # noqa: E402


@pytest.mark.parametrize("case", CASES, ids=[case.name for case in CASES])
def test_units_in_quote(tmp_path, case):
    materialize(tmp_path / "case", case)
    findings = evidence.check(tmp_path / "case")
    units = [item for item in findings if item["code"] == UNIT_WARNING]
    if case.units:
        assert len(units) == 1, findings
        [item] = units
        assert item["status"] == "warn"
        assert (item["ids"], item["file"], item["line"]) == (
            ["CLM-001"], "state/ledger/claims.yaml", 1,
        )
        assert f"в statement «{case.units[0]}», в quote «{case.units[1]}»" in item["message"]
    else:
        assert units == []

    if case.legacy:
        assert any(
            (item["code"], item["status"], item["ids"]) == (*case.legacy, ["CLM-001"])
            for item in findings
        )

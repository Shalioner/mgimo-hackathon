"""Регрессия П1.1 для процентов от 1 до 5 в таблицах артефактов."""
from pathlib import Path

import pytest

import verify


@pytest.mark.parametrize(("cell", "claim_value", "verdict"), [
    ("4% [CLM-001]", "4%", "ok"),
    ("4% [CLM-001]", "4,0%", "ok"),
    ("4\u202f% [CLM-001]", "4.00%", "ok"),
    ("5% [CLM-001]", "4%", "check"),
    ("5% [CLM-001]", "5 поездок", "check"),
    ("5 %", "4%", "unsupported"),
])
def test_table_percentage_is_checked_as_a_number(tmp_path: Path, cell: str, claim_value: str, verdict: str) -> None:
    ledger = tmp_path / "state" / "ledger"
    ledger.mkdir(parents=True)
    (ledger / "claims.yaml").write_text(
        f"- id: CLM-001\n  type: fact\n  statement: Значение составило {claim_value}.\n",
        encoding="utf-8",
    )
    outputs = tmp_path / "outputs"
    outputs.mkdir()
    (outputs / "03_situation.md").write_text(
        f"| Метрика | Значение |\n| --- | --- |\n| Доля возвратов | {cell} |\n",
        encoding="utf-8",
    )

    items = verify.verify(tmp_path, [])["items"]

    assert [(item["line"], item["verdict"]) for item in items] == [(3, verdict)]


def test_encoded_url_is_not_a_table_percentage(tmp_path: Path) -> None:
    outputs = tmp_path / "outputs"
    outputs.mkdir()
    (outputs / "03_situation.md").write_text(
        "| Источник | [Отчёт](https://example.org/reports/4%20weeks) |\n",
        encoding="utf-8",
    )

    assert verify.main([str(tmp_path), "--strict"]) == 0


def test_small_experiment_threshold_leaves_fraction_comparison_to_stop_go(tmp_path: Path) -> None:
    ledger = tmp_path / "state" / "ledger"
    ledger.mkdir(parents=True)
    (ledger / "experiments.yaml").write_text(
        "- id: EXP-001\n  stop_go: Stop при доле ниже 0.05\n",
        encoding="utf-8",
    )
    outputs = tmp_path / "outputs"
    outputs.mkdir()
    (outputs / "07_validation_plan.md").write_text(
        "| EXP-001 | discovery | Stop < 5 % |\n",
        encoding="utf-8",
    )

    assert [item["verdict"] for item in verify.verify(tmp_path, [])["items"]] == ["ok"]

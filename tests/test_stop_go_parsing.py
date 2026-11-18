from decimal import Decimal

import pytest

from strategy_checks.validation import _single_stop_proportion


@pytest.mark.parametrize(
    "condition",
    [
        "Stop < 0.50, Go >= 0.50",
        "Stop < 50%, Go ≥ 0.5",
        "0.50 за 1.5 мес",
        "Stop < 0.50 через 0.25 недели",
    ],
)
def test_equivalent_stop_go_boundaries_and_duration(condition):
    assert _single_stop_proportion(condition, linked=True) == Decimal("0.50")


def test_different_stop_go_boundaries_are_ambiguous():
    assert _single_stop_proportion("Stop < 0.50, Go >= 0.55", linked=True) is None

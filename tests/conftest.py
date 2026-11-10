"""Общее для тестов: маршрутизация не зависит от инвентаря моделей конкретной машины."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import models as M  # noqa: E402


@pytest.fixture(autouse=True)
def no_machine_inventory(monkeypatch):
    monkeypatch.setattr(M, "load_inventory", lambda: None)

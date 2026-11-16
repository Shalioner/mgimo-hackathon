"""Общее для тестов: маршрутизация не зависит от инвентаря моделей конкретной машины."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import models as M  # noqa: E402


@pytest.fixture(autouse=True)
def no_machine_inventory(monkeypatch):
    monkeypatch.setattr(M, "load_inventory", lambda: None)


# ---------- Windows: честная граница вместо 150 «красных» тестов ----------
# Адаптеры исполнения (Codex/Claude/OpenCode subscription, native host routing) и локальный D1 MLX
# закрыты на не-POSIX по замыслу (fail-closed → status "manual"). Их тесты проверяют POSIX-поведение.
# На Windows такие модули: (а) не собираются, если импортируют fcntl/resource на верхнем уровне;
# (б) помечаются xfail(strict=False) — прошедшие видны как XPASS, упавшие — с причиной, а не прячутся.
import os  # noqa: E402

WINDOWS_COLLECT_IGNORE = {  # модуль импортирует fcntl на верхнем уровне (macOS D1 service)
    "test_d1_service.py", "test_d1_reuse.py", "test_d1_reply_isolation.py",
}
POSIX_BOUNDARY = {  # адаптер исполнения или D1 MLX поддерживает только POSIX/macOS
    "test_route_request.py", "test_document_flow.py", "test_d1_service_client.py", "test_opencode_free.py",
    "test_native_codex.py", "test_adaptive_execution.py", "test_local_decision.py", "test_execution_outcomes.py",
    "test_claude_subscription.py", "test_subscription_classifier.py", "test_local_decision_mlx.py",
    "test_codex_subscription.py", "test_d1_control.py", "test_install_host_routing.py", "test_codex_routed.py",
    "test_tool_cascade.py", "test_doctor.py", "test_calculation_gate.py", "test_decision_runtime.py",
    "test_codex_routing_hook.py", "test_classifier_cascade.py",
}
collect_ignore = sorted(WINDOWS_COLLECT_IGNORE) if os.name != "posix" else []


def pytest_collection_modifyitems(config, items):
    if os.name == "posix":
        return
    mark = pytest.mark.xfail(strict=False, reason="POSIX-only execution/D1 boundary: на Windows маршрут fail-closed "
                                                  "(manual), задачу выполняет сам хост; см. docs/WINDOWS.md")
    for item in items:
        if item.path.name in POSIX_BOUNDARY:
            item.add_marker(mark)

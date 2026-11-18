"""Контракт D1 CLI на границе платформ. Платформа подставляется в тесте."""

import json
import sys
from types import SimpleNamespace

import pytest

import d1_control


@pytest.mark.parametrize("platform", ["win32", "linux"])
@pytest.mark.parametrize("command", ["install", "start", "status", "stop"])
def test_d1_control_reports_unsupported_platform_without_io(
    platform, command, monkeypatch, capsys
):
    monkeypatch.setattr(d1_control.sys, "platform", platform)

    def unexpected_io(*args, **kwargs):
        pytest.fail("D1 control must check the platform before checking the socket or launchd")

    monkeypatch.setattr(d1_control, "launchctl", unexpected_io)
    monkeypatch.setitem(sys.modules, "d1_service", SimpleNamespace(request=unexpected_io))

    assert d1_control.main([command]) == 3
    receipt = json.loads(capsys.readouterr().out)
    assert receipt["status"] == "error"
    assert "requires macOS" in receipt["reason"]

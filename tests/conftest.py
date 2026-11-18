"""Общие фикстуры для тестов маршрутизации и подставных CLI."""
import os
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import models as M  # noqa: E402


@pytest.fixture(autouse=True)
def no_machine_inventory(monkeypatch):
    monkeypatch.setattr(M, "load_inventory", lambda: None)


@pytest.fixture
def neutral_cli_temp(monkeypatch):
    """Use a private temp directory outside Codex and Claude project config."""
    markers = (".codex", ".claude", "AGENTS.md", "CLAUDE.md", "config.toml")
    roots = (tempfile.gettempdir(), os.getenv("TEMP"), os.getenv("TMP"), "/tmp", "/var/tmp", "/usr/tmp")
    for root in filter(None, roots):
        try:
            area = tempfile.TemporaryDirectory(prefix="strategist-test-", dir=root)
        except OSError:
            continue
        with area as directory:
            path = Path(directory).resolve()
            if any((parent / marker).exists() for parent in (path, *path.parents) for marker in markers):
                continue
            # Keep the inherited TMPDIR intact. Only the mock CLI tests use this override.
            with monkeypatch.context() as patch:
                patch.setattr(tempfile, "tempdir", str(path))
                yield path
            return
    pytest.fail("No writable temporary directory outside ancestor CLI configuration")


@pytest.fixture
def guarded_cli_temp(neutral_cli_temp, monkeypatch):
    guard = neutral_cli_temp / "guard"
    work = guard / "tmp"
    work.mkdir(parents=True)
    (guard / "AGENTS.md").write_text("# Synthetic ancestor marker\n", encoding="utf-8")
    with monkeypatch.context() as patch:
        patch.setattr(tempfile, "tempdir", str(work))
        yield

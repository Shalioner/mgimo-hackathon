"""AGENTS.md целиком доходит до агента: Codex читает не больше project_doc_max_bytes (по умолчанию 32 768 байт)."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CODEX_PROJECT_DOC_MAX_BYTES = 32768


def test_agents_md_fits_codex_project_doc_limit():
    size = len((ROOT / "AGENTS.md").read_bytes())
    assert size <= CODEX_PROJECT_DOC_MAX_BYTES - 1024, (
        f"AGENTS.md {size} байт: Codex обрежет хвост (лимит {CODEX_PROJECT_DOC_MAX_BYTES}); выносите подробности в docs/")


def test_moved_rules_are_linked_and_exist():
    text = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
    for doc in ("docs/AGENT_AUTO_ROUTING.md", "docs/ONBOARDING.md"):
        assert doc in text and (ROOT / doc).is_file()
    assert "## 7." in text and "## 8." in text

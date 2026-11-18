from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import consistency


def test_consistency_distinguishes_legacy_or_incomplete_artifacts_from_errors(tmp_path, capsys):
    ledger = tmp_path / "state" / "ledger"
    ledger.mkdir(parents=True)
    decisions = ledger / "decisions.yaml"
    decisions.write_text("- id: DEC-001\n  state: proposed\n", encoding="utf-8")
    assert consistency.main([str(tmp_path)]) == 0
    assert "[not_assessed]" in capsys.readouterr().out

    decisions.write_text("- id: DEC-001\n  state: decided\n", encoding="utf-8")
    findings = consistency.check(tmp_path)
    finding = next(item for item in findings if item["code"] == "consistency.decision_follow_up")
    assert finding["status"] == "error"
    assert finding["file"] == "state/ledger/decisions.yaml"
    assert finding["line"] == 1
    assert finding["ids"] == ["DEC-001"]
    assert consistency.main([str(tmp_path), "--json"]) == 1


def test_consistency_includes_rebuild_errors_before_c3(tmp_path):
    (tmp_path / "state" / "ledger").mkdir(parents=True)
    outputs = tmp_path / "outputs"
    outputs.mkdir()
    (outputs / "05_product_strategy.md").write_text("# Главное\n\n# Главное\n", encoding="utf-8")

    assert any(item["code"] == "rebuild.duplicate_main" for item in consistency.check(tmp_path))
    assert consistency.main([str(tmp_path)]) == 1

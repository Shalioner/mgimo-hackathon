"""Тесты детерминированных инструментов реестра (tools/ledger.py)."""
import re
import shutil
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
import ledger as L  # noqa: E402

EXAMPLE = ROOT / "projects" / "example"


@pytest.fixture
def proj(tmp_path):
    dst = tmp_path / "example"
    shutil.copytree(EXAMPLE, dst)
    return dst


def load(proj, fname):
    return yaml.load((proj / "state/ledger" / fname).read_text(encoding="utf-8"), Loader=L._Loader)


def dump(proj, fname, data):
    (proj / "state/ledger" / fname).write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False),
                                               encoding="utf-8")


def errors(proj):
    return L.validate(L.Ledger(proj)).errors


def edit(proj, fname, oid, fn):
    data = load(proj, fname)
    for o in data:
        if o["id"] == oid:
            fn(o)
    dump(proj, fname, data)


# ---------- эталонный пример ----------
def test_example_is_valid():
    rep = L.validate(L.Ledger(EXAMPLE))
    assert rep.errors == []


def test_impact_of_change_is_exactly_expected():
    res = L.impact(L.Ledger(EXAMPLE), ["CHG-001"])
    assert set(res) == {"CLM-004", "HYP-001", "PAIN-001", "ALT-001", "DEC-001", "PRM-001", "EXP-001"}


def test_impact_leaves_unrelated_branches_untouched():
    res = L.impact(L.Ledger(EXAMPLE), ["CHG-001"])
    for untouched in ("ALT-002", "ALT-003", "ALT-004", "HYP-002", "CLM-001", "CLM-005"):
        assert untouched not in res


def test_impact_from_source_follows_contradiction():
    res = L.impact(L.Ledger(EXAMPLE), ["SRC-005"])
    assert {"CLM-006", "CLM-004", "DEC-001"} <= set(res)


# ---------- правила доказательности ----------
def test_fact_without_evidence_rejected(proj):
    edit(proj, "claims.yaml", "CLM-001", lambda o: o.pop("evidence"))
    assert any("факт без evidence" in e for e in errors(proj))


def test_fact_from_synthetic_interview_rejected(proj):
    edit(proj, "claims.yaml", "CLM-005", lambda o: o.update(type="fact"))
    assert any("синтетическое интервью" in e for e in errors(proj))


def test_wrong_quote_rejected(proj):
    edit(proj, "claims.yaml", "CLM-002",
         lambda o: o["evidence"][0].update(quote="90% обращений касаются синхронизации"))
    assert any("цитата не найдена" in e for e in errors(proj))


def test_changed_source_file_detected(proj):
    f = proj / "sources" / "competitor_notes.md"
    f.write_text(f.read_text(encoding="utf-8") + "\nПравка.\n", encoding="utf-8")
    assert any("sha256 не совпадает" in e for e in errors(proj))


def test_dangling_reference_rejected(proj):
    edit(proj, "hypotheses.yaml", "HYP-001", lambda o: o.update(based_on=["CLM-999"]))
    assert any("несуществующий CLM-999" in e for e in errors(proj))


def test_closed_source_rejected(proj):
    edit(proj, "sources.yaml", "SRC-003", lambda o: o.update(access="confidential"))
    assert any("SRC-003" in e and "access" in e for e in errors(proj))


# ---------- правила решения ----------
def test_decision_requires_status_quo(proj):
    edit(proj, "alternatives.yaml", "ALT-003", lambda o: o.update(kind="other"))
    assert any("оставить как есть" in e for e in errors(proj))


def test_decision_requires_three_options(proj):
    edit(proj, "decisions.yaml", "DEC-001", lambda o: o.update(options=["ALT-001", "ALT-003"], rejected=[]))
    assert any("DEC-001" in e and "options" in e for e in errors(proj))


def test_agent_cannot_decide(proj):
    edit(proj, "decisions.yaml", "DEC-001", lambda o: o.update(decided_by="агент"))
    assert any("финальный выбор делает человек" in e for e in errors(proj))


def test_chosen_must_be_an_option(proj):
    edit(proj, "decisions.yaml", "DEC-001", lambda o: o.update(chosen="ALT-099"))
    errs = errors(proj)
    assert any("chosen=ALT-099 не входит в options" in e for e in errs)


def test_alternative_needs_objection(proj):
    edit(proj, "alternatives.yaml", "ALT-004", lambda o: o.update(objections=[]))
    assert any("ALT-004" in e and "objections" in e for e in errors(proj))


def test_experiment_needs_side_effects(proj):
    edit(proj, "experiments.yaml", "EXP-001", lambda o: o.pop("side_effects"))
    assert any("EXP-001" in e and "side_effects" in e for e in errors(proj))


# ---------- жизненный цикл ----------
def test_retracted_dependency_warns(proj):
    edit(proj, "claims.yaml", "CLM-004", lambda o: o.update(status="retracted", status_note="опровергнуто"))
    rep = L.validate(L.Ledger(proj))
    assert any("HYP-001" in w and "retracted" in w for w in rep.warnings)


def test_superseded_requires_pointer(proj):
    edit(proj, "claims.yaml", "CLM-003", lambda o: o.update(status="superseded"))
    assert any("superseded_by" in e for e in errors(proj))


def test_cli_exit_codes(proj, capsys):
    assert L.main(["validate", str(EXAMPLE)]) == 0
    assert L.main(["validate", str(EXAMPLE), "--strict"]) == 1  # в примере есть ожидаемые предупреждения
    edit(proj, "claims.yaml", "CLM-001", lambda o: o.pop("evidence"))
    assert L.main(["validate", str(proj)]) == 1


# ---------- init / add-source / status ----------
def test_init_creates_valid_empty_project(tmp_path):
    proj = tmp_path / "p"
    assert L.main(["init", str(proj), "--title", "Тест"]) == 0
    assert (proj / "state/progress.md").exists() and (proj / "state/run_log.md").exists()
    assert L.validate(L.Ledger(proj)).errors == []
    assert L.main(["init", str(proj), "--title", "Тест"]) == 1  # повторно не затирает


def test_add_source_registers_copies_and_rejects_duplicates(tmp_path):
    proj = tmp_path / "p"
    L.main(["init", str(proj), "--title", "Тест"])
    f = tmp_path / "note.md"
    f.write_text("Рынок вырос на 12%.", encoding="utf-8")
    args = ["add-source", str(proj), str(f), "--title", "Заметка", "--kind", "document", "--access", "own"]
    assert L.main(args) == 0
    assert (proj / "sources/note.md").exists()
    led = L.Ledger(proj)
    assert led.objects["SRC-001"]["path"] == "sources/note.md"
    assert L.validate(led).errors == []
    assert L.main(args) == 3  # тот же sha256 — дубликат
    f2 = tmp_path / "other.md"
    f2.write_text("Другое.", encoding="utf-8")
    assert L.main(["add-source", str(proj), str(f2), "--title", "Другая", "--kind", "document",
                   "--access", "open", "--url", "https://example.org"]) == 0
    assert "SRC-002" in L.Ledger(proj).objects


def test_add_source_rejects_closed_access(tmp_path):
    proj = tmp_path / "p"
    L.main(["init", str(proj), "--title", "Тест"])
    f = tmp_path / "x.md"
    f.write_text("x", encoding="utf-8")
    with pytest.raises(SystemExit):
        L.main(["add-source", str(proj), str(f), "--title", "X", "--kind", "document", "--access", "confidential"])


def test_status_reports_pending_change(capsys):
    assert L.main(["status", "--root", str(ROOT / "projects")]) == 0
    out = capsys.readouterr().out
    assert "example" in out and "CHG-001" in out


def test_add_source_refuses_invalid_entry_and_cleans_copy(tmp_path):
    proj = tmp_path / "p"
    L.main(["init", str(proj), "--title", "Тест"])
    f = tmp_path / "a.md"
    f.write_text("x", encoding="utf-8")
    assert L.main(["add-source", str(proj), str(f), "--title", "А", "--kind", "document", "--access", "own"]) == 1
    assert not (proj / "sources/a.md").exists()
    assert L.Ledger(proj).objects == {}


# ---------- route / log ----------
def test_route_starts_cheap_and_escalates_one_step(capsys):
    assert L.main(["route", "extract-claims"]) == 0
    assert "T1" in capsys.readouterr().out
    assert L.main(["route", "alternatives"]) == 0
    out = capsys.readouterr().out
    assert "T3" in out and "Opus" in out and "Sol" in out
    assert L.main(["route", "alternatives", "--failed", "T3"]) == 0
    out = capsys.readouterr().out
    assert "T4" in out and "Fable" in out and "Astra" in out and "согласия" in out
    assert L.main(["route", "validate"]) == 0
    assert "кодом" in capsys.readouterr().out


def test_every_task_maps_to_known_tier():
    cfg = L.load_routing()
    for name, t in cfg["tasks"].items():
        assert t["tier"] in cfg["tiers"], name
    assert cfg["tiers"]["T4"]["claude"] == "Fable" and cfg["tiers"]["T1"]["openai"] == "Luna"


def test_t4_budget_enforced(tmp_path, capsys):
    proj = tmp_path / "p"
    L.main(["init", str(proj), "--title", "Т"])
    for _ in range(2):
        assert L.main(["log", str(proj), "--step", "5", "--action", "эскалация", "--tier", "T4",
                       "--model", "Fable", "--result", "ok"]) == 0
    assert L.main(["route", "alternatives", "--failed", "T3", "--project", str(proj)]) == 1
    assert "исчерпан" in capsys.readouterr().out


def test_log_appends_row_with_timestamp_and_escapes(tmp_path):
    proj = tmp_path / "p"
    L.main(["init", str(proj), "--title", "Т"])
    assert L.main(["log", str(proj), "--step", "3", "--action", "a | b", "--tier", "T1",
                   "--model", "Haiku", "--result", "5 CLM"]) == 0
    last = (proj / "state/run_log.md").read_text(encoding="utf-8").strip().splitlines()[-1]
    cells = [c.strip() for c in last.strip("|").split("|")]
    assert len(cells) == 7 and cells[2] == "a / b" and cells[3] == "T1"
    assert re.match(r"\d{4}-\d{2}-\d{2}T", cells[0])
    assert L.main(["log", str(proj), "--step", "3", "--action", "x", "--tier", "T9", "--result", "-"]) == 2

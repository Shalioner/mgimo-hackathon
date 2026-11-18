"""Контракт команды supersede и проверка истории замены."""
import sys
from datetime import date
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import ledger as L  # noqa: E402


@pytest.fixture
def case(tmp_path):
    directory = tmp_path / "state" / "ledger"
    directory.mkdir(parents=True)
    records = [
        dict(id=f"SRC-{i:03}", title=f"Источник {i}", url=f"https://example.test/{i}",
             kind="document", access="open", retrieved="2026-01-01", reliability="high")
        for i in range(1, 6)
    ]
    (directory / "sources.yaml").write_text(
        "# исходный формат\n" + yaml.safe_dump(records, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    (directory / "hypotheses.yaml").write_text(
        yaml.safe_dump([dict(id="HYP-001", kind="assumption", statement="Допущение",
                             confidence=dict(level="medium", rationale="Допущение"))], allow_unicode=True),
        encoding="utf-8",
    )
    return tmp_path


def supersede(case, old="SRC-001", new="SRC-002", note=None):
    args = ["supersede", str(case), old, "--by", new]
    if note is not None:
        args += ["--note", note]
    return L.main(args)


def edit_sources(case, **changes):
    path = case / "state/ledger/sources.yaml"
    records = yaml.load(path.read_text(encoding="utf-8"), Loader=L._Loader)
    for obj in records:
        obj.update(changes.get(obj["id"], {}))
    path.write_text(yaml.safe_dump(records, allow_unicode=True, sort_keys=False), encoding="utf-8")


def snapshot(case):
    return {p.name: p.read_bytes() for p in (case / "state/ledger").glob("*.yaml")}


def forbid_write(monkeypatch):
    def unexpected_write(*args):
        pytest.fail("supersede попытался записать YAML")

    monkeypatch.setattr(L, "atomic_write_text", unexpected_write)


@pytest.mark.parametrize("active_id", ["SRC-001", "SRC-002"])
def test_supersede_refuses_inconsistent_active_replacement(case, active_id, monkeypatch):
    edit_sources(case, **{active_id: dict(superseded_by="SRC-003")})
    before = snapshot(case)
    forbid_write(monkeypatch)
    assert supersede(case) == 2
    assert snapshot(case) == before


@pytest.mark.parametrize("retry_note", [None, "", "первая заметка"])
@pytest.mark.parametrize("continued", [False, True])
def test_same_successor_is_noop_even_after_chain_continued(case, retry_note, continued, monkeypatch):
    assert supersede(case, note="первая заметка") == 0
    if continued:
        assert supersede(case, "SRC-002", "SRC-003") == 0
    ledger = L.Ledger(case)
    old = ledger.objects["SRC-001"]
    assert old["status"] == "superseded"
    assert old["superseded_by"] == "SRC-002"
    assert old["status_note"] == "первая заметка"
    assert old["updated"] == date.today().isoformat()
    report = L.validate(ledger)
    assert report.errors == report.warnings == []
    before = snapshot(case)
    forbid_write(monkeypatch)
    assert supersede(case, note=retry_note) == 0
    assert snapshot(case) == before


@pytest.mark.parametrize(("new", "first_note", "retry_note"), [
    ("SRC-004", "первая заметка", "другой преемник"),
    ("SRC-002", "первая заметка", "другая заметка"),
    ("SRC-002", None, "добавленная заметка"),
])
def test_conflict_preserves_history(case, new, first_note, retry_note, monkeypatch, capsys):
    assert supersede(case, note=first_note) == 0
    assert supersede(case, "SRC-002", "SRC-003") == 0
    before = snapshot(case)
    forbid_write(monkeypatch)
    assert supersede(case, new=new, note=retry_note) == 3
    assert snapshot(case) == before
    err = capsys.readouterr().err
    assert "уже заменён SRC-002" in err
    assert "последний активный объект" in err


@pytest.mark.parametrize(("old", "new"), [
    ("SRC-999", "SRC-002"),
    ("SRC-001", "SRC-999"),
    ("SRC-001", "HYP-001"),
    ("SRC-001", "SRC-001"),
    ("SRC-004", "SRC-002"),
    ("SRC-001", "SRC-004"),
    ("SRC-001", "SRC-003"),
])
def test_invalid_request_never_writes(case, old, new, monkeypatch):
    edit_sources(case, **{
        "SRC-003": dict(status="superseded", superseded_by="SRC-005"),
        "SRC-004": dict(status="retracted"),
    })
    before = snapshot(case)
    forbid_write(monkeypatch)
    assert supersede(case, old, new, note="попытка") == 2
    assert snapshot(case) == before


@pytest.mark.parametrize(("fields", "diagnostic"), [
    (dict(status="superseded"), "не указан superseded_by"),
    (dict(status="superseded", superseded_by="SRC-999"), "несуществующий объект"),
    (dict(status="superseded", superseded_by="HYP-001"), "другого типа"),
    (dict(status="superseded", superseded_by="SRC-001"), "цикл superseded_by"),
])
def test_broken_history_is_rejected_by_validate_and_supersede(case, fields, diagnostic, monkeypatch, capsys):
    edit_sources(case, **{"SRC-001": fields})
    assert L.main(["validate", str(case)]) == 1
    assert diagnostic in capsys.readouterr().out
    before = snapshot(case)
    forbid_write(monkeypatch)
    assert supersede(case) == 2
    err = capsys.readouterr().err
    assert diagnostic in err
    assert "--force" not in err
    assert snapshot(case) == before


def test_validate_reports_each_cycle_once(case):
    edit_sources(case, **{
        "SRC-001": dict(status="superseded", superseded_by="SRC-003"),
        "SRC-002": dict(status="superseded", superseded_by="SRC-003"),
        "SRC-003": dict(status="superseded", superseded_by="SRC-002"),
        "SRC-004": dict(status="superseded", superseded_by="SRC-004"),
        "SRC-005": dict(status="superseded", superseded_by="SRC-004"),
    })
    report = L.validate(L.Ledger(case))
    assert len(report.errors) == 2
    assert any("SRC-003 → SRC-002 → SRC-003" in e for e in report.errors)
    assert any("SRC-004 → SRC-004" in e for e in report.errors)


@pytest.mark.parametrize("third_successor", ["SRC-001", "SRC-002"])
def test_new_link_cannot_create_or_lead_to_cycle(case, third_successor, monkeypatch, capsys):
    edit_sources(case, **{
        "SRC-002": dict(superseded_by="SRC-003"),
        "SRC-003": dict(status="superseded", superseded_by=third_successor),
    })
    before = snapshot(case)
    forbid_write(monkeypatch)
    assert supersede(case) == 2
    assert "цикл superseded_by" in capsys.readouterr().err
    assert snapshot(case) == before


def test_validate_warns_on_retracted_leaf_and_active_pointer(case, monkeypatch):
    edit_sources(case, **{
        "SRC-001": dict(status="superseded", superseded_by="SRC-002"),
        "SRC-002": dict(status="superseded", superseded_by="SRC-003"),
        "SRC-003": dict(status="retracted"),
        "SRC-004": dict(status="active", superseded_by="SRC-005"),
    })
    report = L.validate(L.Ledger(case))
    assert report.errors == []
    assert len(report.warnings) == 2
    assert any("SRC-002" in w and "SRC-003" in w and "retracted" in w for w in report.warnings)
    assert any("SRC-004" in w and "активной" in w for w in report.warnings)
    before = snapshot(case)
    forbid_write(monkeypatch)
    assert supersede(case) == 0
    assert snapshot(case) == before

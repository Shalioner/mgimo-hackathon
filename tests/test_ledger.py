"""Тесты детерминированных инструментов реестра (tools/ledger.py)."""
import multiprocessing
import os
import re
import shutil
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

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



@pytest.mark.parametrize("actor", ["Стратег", "GPT-6.1-sol", "assistant", "auto", "-"])
def test_model_or_placeholder_cannot_be_recorded_as_human(proj, actor):
    edit(proj, "decisions.yaml", "DEC-001", lambda o: o.update(decided_by=actor))
    assert any("финальный выбор делает человек" in e for e in errors(proj))


def test_proposed_decision_cannot_contain_human_choice(proj):
    edit(proj, "decisions.yaml", "DEC-001", lambda o: o.update(state="proposed"))
    assert any("proposed не может содержать chosen/decided_by/decided_at" in e for e in errors(proj))


def test_chosen_option_cannot_also_be_rejected(proj):
    def reject_chosen(o):
        o["rejected"].append({"option": o["chosen"], "reason": "Противоречивый отказ"})
    edit(proj, "decisions.yaml", "DEC-001", reject_chosen)
    assert any("одновременно указан в rejected" in e for e in errors(proj))


def test_decided_requires_nonblank_rationale(proj):
    edit(proj, "decisions.yaml", "DEC-001", lambda o: o.update(rationale="   "))
    assert any("не заполнено поле rationale" in e for e in errors(proj))


@pytest.mark.parametrize("historical", [False, True])
def test_one_active_decision_per_question_preserves_history(proj, historical):
    import copy
    data = load(proj, "decisions.yaml")
    duplicate = copy.deepcopy(data[0])
    duplicate.update(id="DEC-002", question="  " + duplicate["question"].upper() + "  ")
    if historical:
        duplicate["status"] = "retracted"
    data.append(duplicate)
    dump(proj, "decisions.yaml", data)
    found = any("дубль активного решения" in e for e in errors(proj))
    assert found is not historical


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
    assert "T3" in out and "opus" in out and "gpt-6.1-sol" in out
    assert L.main(["route", "alternatives", "--failed", "T3"]) == 0
    out = capsys.readouterr().out
    assert "Уровень: T3" in out and "Effort: xhigh" in out  # сначала effort на той же модели
    assert L.main(["route", "alternatives", "--failed", "T3@xhigh"]) == 0
    out = capsys.readouterr().out
    assert "T4" in out and "fable" in out and "gpt-6-astra" in out and "согласия" in out
    assert L.main(["route", "validate"]) == 0
    assert "кодом" in capsys.readouterr().out


def test_every_task_maps_to_known_tier():
    cfg = L.load_routing()
    for name, t in cfg["tasks"].items():
        assert t["tier"] in cfg["tiers"], name
    assert L.model_for(cfg, "claude", "T4") == "fable" and L.model_for(cfg, "openai", "T1") == "gpt-6-luna"


def test_t4_budget_enforced(tmp_path, capsys):
    proj = tmp_path / "p"
    L.main(["init", str(proj), "--title", "Т"])
    for _ in range(2):
        assert L.main(["log", str(proj), "--step", "5", "--action", "эскалация", "--tier", "T4",
                       "--model", "Fable", "--result", "ok"]) == 0
    assert L.main(["route", "alternatives", "--failed", "T3@xhigh", "--project", str(proj)]) == 1
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


# ---------- пересказ агента не доказательство; шум до решения ----------
def test_quote_from_agent_paraphrase_rejected(proj):
    f = proj / "sources" / "competitor_notes.md"
    f.write_text(f.read_text(encoding="utf-8") + "\n## Пересказ агента\nКонкурент В бесплатен для семей.\n",
                 encoding="utf-8")
    edit(proj, "sources.yaml", "SRC-003", lambda o: o.pop("sha256"))
    edit(proj, "claims.yaml", "CLM-003", lambda o: o["evidence"][0].update(quote="Конкурент В бесплатен для семей"))
    assert any("пересказа агента" in e for e in errors(proj))


def test_quote_after_paraphrase_section_is_evidence_again():
    text = "# Шапка\n## Выдержки\nА\n## Пересказ\nБ\n## Оригинал, продолжение\nВ\n"
    ev = L.evidence_text(text)
    assert "А" in ev and "В" in ev and "Б" not in ev


def test_no_experiment_warnings_before_decision(proj):
    edit(proj, "decisions.yaml", "DEC-001", lambda o: o.update(state="proposed"))
    rep = L.validate(L.Ledger(proj))
    assert not any("ни один эксперимент" in w for w in rep.warnings)


def test_stats_reports_contradictions_and_hypothesis_split(capsys):
    assert L.main(["stats", str(EXAMPLE)]) == 0
    out = capsys.readouterr().out
    assert "Противоречий (пар): 1" in out and "Гипотез: 2, допущений: 1" in out


# ---------- supersede и правка на месте ----------
def _git(cwd, *a):
    import subprocess
    subprocess.run(["git", *a], cwd=cwd, check=True, capture_output=True)


def test_in_place_statement_edit_warns_against_git_head(proj):
    _git(proj, "init", "-q")
    _git(proj, "-c", "user.name=t", "-c", "user.email=t@t", "add", "-A")
    _git(proj, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "base")
    edit(proj, "claims.yaml", "CLM-003", lambda o: o.update(statement="Конкурент А даёт 20 ГБ бесплатно."))
    rep = L.validate(L.Ledger(proj))
    assert any("CLM-003" in w and "изменена на месте" in w for w in rep.warnings)


def test_supersede_marks_old_and_keeps_record(proj):
    data = load(proj, "claims.yaml")
    new = dict(data[2], id="CLM-007", statement="Конкурент А даёт 15 ГБ бесплатно (уточнено).")
    dump(proj, "claims.yaml", data + [new])
    assert L.main(["supersede", str(proj), "CLM-003", "--by", "CLM-007", "--note", "уточнено"]) == 0
    led = L.Ledger(proj)
    assert led.objects["CLM-003"]["status"] == "superseded" and led.objects["CLM-003"]["superseded_by"] == "CLM-007"
    assert L.validate(led).errors == []
    assert L.main(["supersede", str(proj), "CLM-003", "--by", "HYP-001"]) == 2


def test_interrupted_supersede_preserves_previous_ledger(proj, monkeypatch):
    data = load(proj, "claims.yaml")
    dump(proj, "claims.yaml", data + [dict(data[2], id="CLM-007")])
    path = proj / "state/ledger/claims.yaml"
    old = path.read_bytes()

    def interrupt(_):
        raise KeyboardInterrupt

    with monkeypatch.context() as interrupted:
        interrupted.setattr(L.os, "fsync", interrupt)
        with pytest.raises(KeyboardInterrupt):
            L.cmd_supersede(proj, "CLM-003", "CLM-007", None)
    assert path.read_bytes() == old
    assert not list(path.parent.glob(".claims.yaml.*.tmp"))
    assert L.cmd_supersede(proj, "CLM-003", "CLM-007", None) == 0
    assert L.Ledger(proj).objects["CLM-003"]["superseded_by"] == "CLM-007"


def _add_source(project, source):
    return L.cmd_add_source(project, source, "Источник", "document", "own",
                            None, "high", None, None)


@pytest.mark.parametrize("duplicate", [True, False])
def test_add_source_concurrent_conflict(tmp_path, duplicate):
    project = tmp_path / "case"
    assert L.cmd_init(project, "Тест") == 0
    a = tmp_path / "a" / "note.md"
    b = tmp_path / "b" / ("other.md" if duplicate else "note.md")
    for path, content in ((a, "Первый"), (b, "Первый" if duplicate else "Второй")):
        path.parent.mkdir()
        path.write_text(content, encoding="utf-8")
    start = Barrier(2, timeout=10)

    def add(path):
        start.wait()
        return _add_source(project, path)

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(add, (a, b))) == [0, 3 if duplicate else 1]
    ledger = L.Ledger(project)
    assert list(ledger.objects) == ["SRC-001"]
    assert list((project / "sources").iterdir()) == [project / ledger.objects["SRC-001"]["path"]]
    assert L.validate(ledger).errors == []


def _hold_project_write_lock(project, connection):
    with connection, L._project_write_lock(project) as locked:
        connection.send(locked)
        connection.poll(20)


def test_project_write_lock_timeout_and_recovery(proj, tmp_path, monkeypatch, capsys):
    other = tmp_path / "other"
    assert L.cmd_init(other, "Другой") == 0
    log = proj / "state/run_log.md"
    log.write_text(L.RUN_LOG_HEADER, encoding="utf-8")
    source = tmp_path / "new.md"
    source.write_text("Новый источник", encoding="utf-8")
    sources = proj / "state/ledger/sources.yaml"
    before = sources.read_bytes(), log.read_bytes()
    monkeypatch.chdir(tmp_path)
    alias = Path(proj.name)
    if os.name == "posix":
        alias = Path("alias")
        alias.symlink_to(proj.name, target_is_directory=True)
    monkeypatch.setattr(L, "_WRITE_LOCK_TIMEOUT", 0.05)

    ctx = multiprocessing.get_context("spawn")
    parent, child = ctx.Pipe()
    owner = ctx.Process(target=_hold_project_write_lock, args=(proj, child))
    owner.start()
    child.close()
    lock_file = proj / "state/.ledger-write.lock"
    try:
        assert parent.poll(10) and parent.recv()
        inode = lock_file.stat().st_ino
        assert L.cmd_log(other, "3", "другая запись", "T0", "-", "ok", "-") == 0
        assert _add_source(alias, source) == 1
        assert L.cmd_log(alias, "3", "заблокировано", "T0", "-", "ok", "-") == 1
        assert L.cmd_supersede(alias, "SRC-001", "SRC-002", None) == 1
        assert capsys.readouterr().err.count("истекло ожидание") == 3
        assert (sources.read_bytes(), log.read_bytes()) == before
        assert not (proj / "sources" / source.name).exists()
    finally:
        owner.kill()
        owner.join(10)
        parent.close()

    assert owner.exitcode is not None and owner.exitcode != 0
    assert _add_source(alias, source) == 0
    assert lock_file.stat().st_ino == inode


def test_supersede_and_add_source_preserve_both_updates(proj, tmp_path):
    source = tmp_path / "new.md"
    source.write_text("Новый источник", encoding="utf-8")
    start = Barrier(2, timeout=10)

    def supersede():
        start.wait()
        return L.cmd_supersede(proj, "SRC-001", "SRC-002", "Уточнение")

    with ThreadPoolExecutor(max_workers=2) as pool:
        replaced = pool.submit(supersede)
        start.wait()
        assert _add_source(proj, source) == 0
        assert replaced.result(timeout=10) == 0
    ledger = L.Ledger(proj)
    assert ledger.objects["SRC-001"]["superseded_by"] == "SRC-002"
    assert ledger.objects["SRC-006"]["path"] == "sources/new.md"
    assert L.validate(ledger).errors == []


def test_brief_one_line_per_object(capsys):
    assert L.main(["brief", str(ROOT / "projects/example"), "HYP", "DEC-001"]) == 0
    out = capsys.readouterr().out.strip().splitlines()
    assert out and all(l.startswith(("HYP-", "DEC-001")) for l in out)
    assert any("увер." in l for l in out if l.startswith("HYP-"))


def test_add_source_retry_after_interrupted_registration(tmp_path, monkeypatch):
    project = tmp_path / "case"
    L.main(["init", str(project), "--title", "Проверка"])
    source = tmp_path / "data.csv"
    source.write_bytes(b"original")
    def interrupt(*args):
        raise KeyboardInterrupt
    with monkeypatch.context() as patch:
        patch.setattr(L, "atomic_write_text", interrupt)
        with pytest.raises(KeyboardInterrupt):
            _add_source(project, source)
    target = project / "sources/data.csv"
    assert target.read_bytes() == b"original"
    source.write_bytes(b"changed")
    assert _add_source(project, source) == 1
    assert target.read_bytes() == b"original"
    source.write_bytes(b"original")
    assert _add_source(project, source) == 0
    assert _add_source(project, source) == 3
    assert list(L.Ledger(project).objects) == ["SRC-001"]


def test_interrupted_attachment_copy_does_not_leave_partial_source(tmp_path, monkeypatch):
    import shutil

    project = tmp_path / "case"
    L.main(["init", str(project), "--title", "Проверка"])
    source = tmp_path / "data.csv"
    source.write_bytes(b"complete data")

    def partial_copy(src, staged):
        Path(staged).write_bytes(b"partial")
        raise KeyboardInterrupt

    with monkeypatch.context() as patch:
        patch.setattr(shutil, "copy2", partial_copy)
        with pytest.raises(KeyboardInterrupt):
            _add_source(project, source)
    assert list((project / "sources").iterdir()) == []
    assert _add_source(project, source) == 0
    assert (project / "sources/data.csv").read_bytes() == source.read_bytes()

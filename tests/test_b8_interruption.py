"""Сценарий 8 (B8): прерывание и повтор init, записи решения и сохранения checkpoint.

Проверяем:
- отсутствие дублей решения (повтор записи, дубли активных DEC, повтор после прерывания);
- отсутствие ложного согласия (явный выбор человека, запрет агентов/заглушек/пустых полей,
  запрет chosen/decided_by при state=proposed и отметки C2/C3 [x] без принятого решения);
- сохранение существующего CLI-контракта и append-only.
"""
import hashlib
import shutil
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
import ledger as L  # noqa: E402


def _make_case_with_proposed_dec(tmp_path: Path, name: str = "case_b8") -> Path:
    """Создать валидный временный кейс с 1 источником, 1 CLM, 3 ALT и 1 DEC (proposed)."""
    proj = tmp_path / name
    assert L.main(["init", str(proj), "--title", "Временный кейс B8"]) == 0
    src_file = tmp_path / f"{name}_src.md"
    src_file.write_text("В отчёте указана базовая конверсия 10%.", encoding="utf-8")
    assert L.main([
        "add-source", str(proj), str(src_file),
        "--title", "Отчёт по воронке", "--kind", "document", "--access", "own",
    ]) == 0
    (proj / "outputs" / "01_brief.md").write_text("# Бриф\n\nЦель: проверить сценарий 8.\n", encoding="utf-8")
    (proj / "outputs" / "04_options.md").write_text("# Варианты\n\nALT-001, ALT-002, ALT-003.\n", encoding="utf-8")
    (proj / "outputs" / "07_validation_plan.md").write_text("# План проверки\n\nEXP-001.\n", encoding="utf-8")

    ledger_dir = proj / "state" / "ledger"
    (ledger_dir / "claims.yaml").write_text(yaml.safe_dump([{
        "id": "CLM-001",
        "type": "fact",
        "statement": "Базовая конверсия составляет 10%.",
        "evidence": [{"source": "SRC-001", "quote": "базовая конверсия 10%"}],
        "confidence": {"level": "high", "rationale": "Прямая цитата из отчёта"},
    }], allow_unicode=True, sort_keys=False), encoding="utf-8")
    (ledger_dir / "alternatives.yaml").write_text(yaml.safe_dump([
        {
            "id": "ALT-001", "title": "Пилот сценария", "kind": "invest",
            "description": "Запустить ограниченный пилот на одном сегменте.",
            "supported_by": ["CLM-001"],
            "objections": [{"text": "Требует времени команды на запуск пилота"}],
        },
        {
            "id": "ALT-002", "title": "Оставить как есть", "kind": "status_quo",
            "description": "Не менять текущий процесс и продолжить наблюдение.",
            "objections": [{"text": "Текущие потери конверсии сохраняются"}],
        },
        {
            "id": "ALT-003", "title": "Закрыть направление", "kind": "kill",
            "description": "Остановить инициативу и перераспределить ресурсы.",
            "objections": [{"text": "Теряем потенциальный сегмент роста"}],
        },
    ], allow_unicode=True, sort_keys=False), encoding="utf-8")
    (ledger_dir / "decisions.yaml").write_text(yaml.safe_dump([{
        "id": "DEC-001",
        "question": "Какой вариант развития выбрать на квартал?",
        "options": ["ALT-001", "ALT-002", "ALT-003"],
        "recommended": "ALT-002",
        "state": "proposed",
        "based_on": ["CLM-001"],
    }], allow_unicode=True, sort_keys=False), encoding="utf-8")
    assert L.validate(L.Ledger(proj)).errors == []
    return proj


def _snap_dir(path: Path) -> dict[str, str]:
    return {
        str(p.relative_to(path)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(path.rglob("*")) if p.is_file()
    }


def test_b8_init_interruption_and_retry(tmp_path, monkeypatch):
    proj = tmp_path / "case_init"

    # Прерывание посреди init (между созданием progress.md и run_log.md)
    real_write = Path.write_text

    def interrupt_after_partial(self, data, *args, **kwargs):
        if self.name == "run_log.md":
            raise KeyboardInterrupt("имитация обрыва при записи run_log.md")
        return real_write(self, data, *args, **kwargs)

    # Перехватываем также atomic_write_text, если init использует его
    orig_atomic = L.atomic_write_text
    calls = {"n": 0}

    def interrupt_atomic(path: Path, text: str) -> None:
        calls["n"] += 1
        if Path(path).name == "run_log.md":
            raise KeyboardInterrupt("имитация обрыва на run_log.md")
        return orig_atomic(path, text)

    with monkeypatch.context() as mp:
        mp.setattr(Path, "write_text", interrupt_after_partial)
        mp.setattr(L, "atomic_write_text", interrupt_atomic)
        with pytest.raises(KeyboardInterrupt):
            L.cmd_init(proj, "Временный кейс")

    # Остаточные .tmp файлы отсутствуют
    assert not list(proj.rglob("*.tmp"))

    # Положительный контроль: повтор init завершает неполную инициализацию
    assert L.main(["init", str(proj), "--title", "Временный кейс"]) == 0
    assert (proj / "state/progress.md").is_file()
    assert (proj / "state/run_log.md").is_file()
    assert L.validate(L.Ledger(proj)).errors == []
    assert L.Ledger(proj).objects == {}

    # Отрицательный контроль: повтор init после полного завершения НЕ перезаписывает проект (код 1)
    before = _snap_dir(proj)
    assert L.main(["init", str(proj), "--title", "Другое название"]) == 1
    assert _snap_dir(proj) == before


def test_b8_init_refuses_incomplete_dir_with_user_file(tmp_path):
    """Отрицательный контроль: если в неполной папке уже есть пользовательский файл, init отказывает."""
    proj = tmp_path / "case_user_file"
    (proj / "sources").mkdir(parents=True)
    (proj / "sources" / "client_note.md").write_text("Данные клиента", encoding="utf-8")
    assert L.main(["init", str(proj), "--title", "Кейс"]) == 1
    assert not (proj / "state/progress.md").exists()


def test_resume_reads_do_not_create_choice_or_duplicate_decision(tmp_path):
    proj = _make_case_with_proposed_dec(tmp_path)
    before = _snap_dir(proj)
    for _ in range(2):
        assert L.main(["brief", str(proj), "DEC-001"]) == 0
        assert L.main(["trace", str(proj), "DEC-001"]) == 0
        assert L.main(["validate", str(proj)]) == 0
    assert _snap_dir(proj) == before
    assert L.Ledger(proj).objects["DEC-001"]["state"] == "proposed"


def test_atomic_decision_write_interruption_preserves_old_state_and_retry(tmp_path, monkeypatch):
    proj = _make_case_with_proposed_dec(tmp_path)
    path = proj / "state/ledger/decisions.yaml"
    original = path.read_bytes()
    data = yaml.safe_load(original)
    data[0].update(state="decided", chosen="ALT-001", decided_by="Руководитель AI продукта",
                   decided_at="2026-11-18", rationale="Явный выбор человека в фикстуре",
                   review_triggers=["Результат пилота"])
    intended = yaml.safe_dump(data, allow_unicode=True, sort_keys=False)
    with monkeypatch.context() as mp:
        def interrupt(_):
            raise KeyboardInterrupt()
        mp.setattr(L.os, "fsync", interrupt)
        with pytest.raises(KeyboardInterrupt):
            L.atomic_write_text(path, intended)
    assert path.read_bytes() == original
    for _ in range(2):
        L.atomic_write_text(path, intended)
        assert L.validate(L.Ledger(proj)).errors == []
    assert len(yaml.safe_load(path.read_bytes())) == 1
    assert not list(path.parent.glob("*.tmp"))


@pytest.mark.parametrize("field,value", [("question", None), ("rejected", ["bad entry"]), ("rejected", 7)])
def test_malformed_question_reports_schema_error_instead_of_crashing(tmp_path, field, value):
    proj = _make_case_with_proposed_dec(tmp_path)
    path = proj / "state/ledger/decisions.yaml"
    data = yaml.safe_load(path.read_bytes())
    data[0][field] = value
    if field == "rejected":
        data[0]["chosen"] = "ALT-001"
    path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    assert L.validate(L.Ledger(proj)).errors


@pytest.mark.parametrize("actor", [
    "   ",
    "gpt-6.1-sol",
    "codex",
    "claude",
    "opus",
    "gemini",
    "ии",
    "бот",
    "assistant",
    "стратег",
    "auto",
    "по умолчанию",
    "tbd",
    "-",
])
def test_b8_validate_rejects_non_human_or_blank_decided_by(tmp_path, actor):
    proj = _make_case_with_proposed_dec(tmp_path)
    dec_path = proj / "state/ledger/decisions.yaml"
    data = yaml.load(dec_path.read_text(encoding="utf-8"), Loader=L._Loader)
    data[0].update({
        "state": "decided",
        "chosen": "ALT-001",
        "decided_by": actor,
        "decided_at": "2026-11-18",
        "rationale": "Обоснование",
        "review_triggers": ["Триггер"],
    })
    dec_path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
    errs = L.validate(L.Ledger(proj)).errors
    assert any("финальный выбор делает человек" in e or "decided_by" in e for e in errs)


def test_b8_validate_rejects_duplicate_active_decisions_and_proposed_with_chosen(tmp_path):
    proj = _make_case_with_proposed_dec(tmp_path)
    dec_path = proj / "state/ledger/decisions.yaml"
    base = yaml.load(dec_path.read_text(encoding="utf-8"), Loader=L._Loader)

    # Ложное согласие: state=proposed с заполненным chosen/decided_by
    premature = [dict(base[0], chosen="ALT-001", decided_by="Руководитель продукта")]
    dec_path.write_text(yaml.safe_dump(premature, allow_unicode=True, sort_keys=False), encoding="utf-8")
    errs = L.validate(L.Ledger(proj)).errors
    assert any("proposed" in e and ("ложное согласие" in e or "chosen" in e) for e in errs)

    # Дубль активного решения на один и тот же вопрос (нормализованный)
    d1 = dict(base[0], state="decided", chosen="ALT-001", decided_by="Руководитель продукта",
              decided_at="2026-11-18", rationale="Первый выбор", review_triggers=["Т1"])
    d2 = dict(d1, id="DEC-002", question="  Какой   вариант развития выбрать на квартал? ",
              chosen="ALT-002", rationale="Дубль выбора")
    dec_path.write_text(yaml.safe_dump([d1, d2], allow_unicode=True, sort_keys=False), encoding="utf-8")
    errs_dup = L.validate(L.Ledger(proj)).errors
    assert any("дубл" in e.lower() and "DEC-002" in e for e in errs_dup)

    # Положительный контроль (append-only): если DEC-001 superseded -> DEC-002, ошибок нет
    assert L.main(["supersede", str(proj), "DEC-001", "--by", "DEC-002", "--note", "пересмотр"]) == 0
    assert L.validate(L.Ledger(proj)).errors == []

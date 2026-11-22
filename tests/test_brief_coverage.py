"""Целевые тесты минимального структурного T0-контроля карты покрытия брифа."""
from __future__ import annotations

import hashlib
import json
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import consistency  # noqa: E402
from strategy_checks import brief_coverage  # noqa: E402

VALID_STATUSES = {"ok", "warn", "error", "not_assessed"}


def _assert_contract(items: list[dict]) -> None:
    for item in items:
        assert set(item.keys()) == {"code", "status", "message", "file", "line", "ids"}
        assert isinstance(item["code"], str) and item["code"]
        assert item["status"] in VALID_STATUSES
        assert isinstance(item["message"], str) and item["message"]
        assert isinstance(item["file"], str) and item["file"]
        assert isinstance(item["line"], int)
        assert isinstance(item["ids"], list)


def _make_case(tmp_path: pathlib.Path, *, include_c3: bool = True) -> pathlib.Path:
    proj = tmp_path / "case"
    (proj / "state" / "ledger").mkdir(parents=True)
    (proj / "outputs").mkdir(parents=True)
    (proj / "sources").mkdir(parents=True)

    (proj / "sources" / "brief_note.md").write_text(
        "# Приложение к брифу\n\n## Ограничения пилота\nБез массовых рассылок и автоподключений.\n",
        encoding="utf-8",
    )
    (proj / "state" / "ledger" / "sources.yaml").write_text(
        "- id: SRC-001\n  title: Приложение к брифу\n  type: document\n  access: own\n  path: sources/brief_note.md\n  status: active\n",
        encoding="utf-8",
    )
    (proj / "state" / "ledger" / "decisions.yaml").write_text(
        "- id: DEC-001\n  title: Пилот\n  state: proposed\n  status: active\n",
        encoding="utf-8",
    )
    (proj / "outputs" / "01_brief.md").write_text(
        "# Бриф: Пилот удержания\n\n"
        "**Исходный запрос (дословно):**\n"
        "> Проверить пилот удержания в Q1. Исключить массовые рассылки и автоматическое подключение пакетов.\n\n"
        "## Ограничения\n"
        "- Исключить массовые рассылки и автоматическое подключение пакетов.\n"
        "- Оценить долю клиентов с двумя сим-картами.\n"
        "- Желательно предложить сценарий для корпоративных тарифов.\n",
        encoding="utf-8",
    )
    if include_c3:
        (proj / "outputs" / "03_situation.md").write_text(
            "# Ситуация\n\n"
            "## Что неизвестно\n"
            "- Неизвестно: доля клиентов с двумя сим-картами [R3]. Почему: нет выгрузки биллинга. Как узнать: запросить агрегат в EXP-002.\n",
            encoding="utf-8",
        )
        (proj / "outputs" / "05_product_strategy.md").write_text(
            "# Продуктовая стратегия\n\n"
            "## Главное\nПроверяем адресный пилот.\n\n"
            "## Аудитория и её задача\nСемейные клиенты [DEC-001].\n\n"
            "## Альтернативы для аудитории\nСамостоятельная проверка.\n\n"
            "## Выбранное направление\nАдресный сигнал в личном кабинете; запуск на одном тарифе [DEC-001].\n\n"
            "## Осознанные отказы\n"
            "- В опытный запуск не берём отправку однотипных сообщений всей базе и активацию опций без действий клиента [R2] [DEC-001].\n\n"
            "## Польза ↔ затраты и доходы\nОписано в модели.\n\n"
            "## Риски и условия пересмотра\nОписаны в плане.\n",
            encoding="utf-8",
        )
    return proj


def _write_map(proj: pathlib.Path, rows: list[str]) -> None:
    header = (
        "# Карта покрытия сложного брифа\n\n"
        "| ID | Обязательно | Требование | Источник | Статус | Привязка к ответу |\n"
        "|---|---|---|---|---|---|\n"
    )
    (proj / "state" / "brief_coverage.md").write_text(header + "\n".join(rows) + "\n", encoding="utf-8")


def test_valid_paraphrase_marker_and_quote_and_visible_gap_and_optional_open(tmp_path: pathlib.Path) -> None:
    proj = _make_case(tmp_path, include_c3=True)
    _write_map(
        proj,
        [
            "| R1 | да | Выбрать формат пилота на Q1 | outputs/01_brief.md#Исходный запрос | закрыто | outputs/05_product_strategy.md#Выбранное направление «Адресный сигнал в личном кабинете; запуск на одном тарифе» |",
            "| R2 | да | Исключить массовые рассылки и автоматическое подключение пакетов | SRC-001#Ограничения пилота | закрыто | outputs/05_product_strategy.md#Осознанные отказы |",
            "| R3 | да | Оценить долю клиентов с двумя сим-картами | outputs/01_brief.md:8 | пробел | outputs/03_situation.md#Что неизвестно |",
            "| R4 | нет | Сценарий для корпоративных тарифов | outputs/01_brief.md:9 | открыто | — |",
        ],
    )

    findings = brief_coverage.check(proj)
    _assert_contract(findings)
    assert not [f for f in findings if f["status"] == "error"]
    codes = {f["code"] for f in findings}
    assert {"brief_coverage.ok", "brief_coverage.gap", "brief_coverage.open_optional"} <= codes

    ok_item = next(f for f in findings if f["code"] == "brief_coverage.ok")
    assert "T0 проверяет только" in ok_item["message"]
    assert "claim-verifier" in ok_item["message"]
    assert brief_coverage.main([str(proj)]) == 0

    # Marker [R2] must NOT trigger unsupported number warning in verify.py
    verify = subprocess.run(
        [sys.executable, str(TOOLS / "verify.py"), str(proj), "--files", "05", "--strict"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert verify.returncode == 0, verify.stdout + verify.stderr


def test_deleted_answer_and_section_substitution_blocked_addressably(tmp_path: pathlib.Path) -> None:
    proj = _make_case(tmp_path, include_c3=True)
    _write_map(
        proj,
        [
            "| R1 | да | Выбрать формат пилота | outputs/01_brief.md:4 | закрыто | outputs/05_product_strategy.md#Выбранное направление «Адресный сигнал в личном кабинете» |",
            "| R2 | да | Исключить массовые рассылки и автоматическое подключение пакетов | outputs/01_brief.md:7 | закрыто | outputs/05_product_strategy.md#Аудитория и её задача |",
        ],
    )

    findings = brief_coverage.check(proj)
    _assert_contract(findings)
    errors = [f for f in findings if f["status"] == "error"]
    assert len(errors) == 1
    assert errors[0]["code"] == "brief_coverage.binding_missing"
    assert errors[0]["file"] == "state/brief_coverage.md"
    assert errors[0]["line"] == 6
    assert errors[0]["ids"] == ["R2"]
    assert consistency.main([str(proj)]) == 1

    # Delete quoted answer from Выбранное направление as well
    p05 = proj / "outputs" / "05_product_strategy.md"
    p05.write_text(p05.read_text(encoding="utf-8").replace("Адресный сигнал в личном кабинете", "Черновик без решения"), encoding="utf-8")
    findings2 = brief_coverage.check(proj)
    err_codes = {(f["ids"][0], f["code"]) for f in findings2 if f["status"] == "error"}
    assert ("R1", "brief_coverage.quote_missing") in err_codes
    assert ("R2", "brief_coverage.binding_missing") in err_codes
    assert brief_coverage.main([str(proj)]) == 1


def test_c1_immutability_and_c1_vs_c3_stage_behavior(tmp_path: pathlib.Path) -> None:
    proj = _make_case(tmp_path, include_c3=False)
    brief_path = proj / "outputs" / "01_brief.md"
    c1_sha = hashlib.sha256(brief_path.read_bytes()).hexdigest()

    # On C1, mandatory open items warn but do NOT block
    _write_map(proj, ["| R2 | да | Исключить массовые рассылки | outputs/01_brief.md:7 | открыто | — |"])
    c1_findings = brief_coverage.check(proj)
    assert not [f for f in c1_findings if f["status"] == "error"]
    assert any(f["code"] == "brief_coverage.open_before_c3" and f["status"] == "warn" for f in c1_findings)

    # A partial package must still allow drafts with open items.
    (proj / "outputs" / "05_product_strategy.md").write_text(
        "# Продуктовая стратегия\n\n## Осознанные отказы\n- В опытный запуск не берём рассылки [R2].\n",
        encoding="utf-8",
    )
    assert any(f["code"] == "brief_coverage.open_before_c3" for f in brief_coverage.check(proj))
    for name in ("06_comms_strategy.md", "07_validation_plan.md"):
        (proj / "outputs" / name).write_text("# Черновик\n", encoding="utf-8")
    c3_open = brief_coverage.check(proj)
    assert any(f["code"] == "brief_coverage.open_required" and f["status"] == "error" for f in c3_open)

    # Filling state/brief_coverage.md for C3 does NOT touch outputs/01_brief.md
    _write_map(
        proj,
        ["| R2 | да | Исключить массовые рассылки | outputs/01_brief.md:7 | закрыто | outputs/05_product_strategy.md#Осознанные отказы |"],
    )
    c3_closed = brief_coverage.check(proj)
    assert not [f for f in c3_closed if f["status"] == "error"]
    assert hashlib.sha256(brief_path.read_bytes()).hexdigest() == c1_sha


def test_archived_history_short_quote_bad_source_and_map_gap_note(tmp_path: pathlib.Path) -> None:
    proj = _make_case(tmp_path, include_c3=True)
    p05 = proj / "outputs" / "05_product_strategy.md"
    p05.write_text(
        "# Продуктовая стратегия\n\n"
        "## Осознанные отказы\n- Общий текст без отметки.\n\n"
        "## Исторический снимок\n- Старый ответ [R2].\n",
        encoding="utf-8",
    )
    (proj / "outputs" / "03_situation.md").write_text(
        "# Ситуация\n\n## Что неизвестно\n- Доля неизвестна [R3].\n",
        encoding="utf-8",
    )
    _write_map(
        proj,
        [
            "| R1 | да | Цель | outputs/01_brief.md:4 | закрыто | outputs/01_brief.md#Ограничения |",
            "| R2 | да | Ограничение | outputs/01_brief.md:7 | закрыто | outputs/05_product_strategy.md#Осознанные отказы |",
            "| R3 | да | Пробел без деталей | outputs/01_brief.md:8 | пробел | outputs/03_situation.md#Что неизвестно |",
            "| R4 | да | Короткая цитата | ../outside.md:1 | закрыто | outputs/05_product_strategy.md#Осознанные отказы «Общий» |",
        ],
    )
    findings = brief_coverage.check(proj)
    err_pairs = {(f["ids"][0], f["code"]) for f in findings if f["status"] == "error" and f["ids"]}
    assert ("R1", "brief_coverage.invalid_target") in err_pairs
    assert ("R2", "brief_coverage.binding_missing") in err_pairs
    assert ("R3", "brief_coverage.gap_incomplete") in err_pairs
    assert ("R4", "brief_coverage.invalid_source") in err_pairs
    assert ("R4", "brief_coverage.quote_too_short") in err_pairs

    # Providing Почему/Как узнать directly in the map cell satisfies пробел
    _write_map(
        proj,
        ["| R3 | да | Доля | outputs/01_brief.md:8 | пробел | outputs/03_situation.md#Что неизвестно; Почему: нет выгрузки; Как узнать: EXP-002 |"],
    )
    ok_gap = brief_coverage.check(proj)
    assert not [f for f in ok_gap if f["status"] == "error"]
    assert any(f["code"] == "brief_coverage.gap" and f["status"] == "warn" for f in ok_gap)


def test_opposite_meaning_is_not_claimed_semantically_verified(tmp_path: pathlib.Path) -> None:
    """T0 checks structural binding only — never pretends to judge semantic negation."""
    proj = _make_case(tmp_path, include_c3=True)
    p05 = proj / "outputs" / "05_product_strategy.md"
    p05.write_text(
        p05.read_text(encoding="utf-8").replace(
            "В опытный запуск не берём отправку однотипных сообщений всей базе и активацию опций без действий клиента [R2] [DEC-001].",
            "Массовые рассылки и автоматическое подключение пакетов включены в рамки пилота [R2] [DEC-001].",
        ),
        encoding="utf-8",
    )
    _write_map(
        proj,
        ["| R2 | да | Исключить массовые рассылки и автоматическое подключение пакетов | outputs/01_brief.md:7 | закрыто | outputs/05_product_strategy.md#Осознанные отказы |"],
    )
    findings = brief_coverage.check(proj)
    ok_item = next(f for f in findings if f["code"] == "brief_coverage.ok")
    assert "смысл и соответствие поручению проверяют человек и skills/claim-verifier" in ok_item["message"]


def test_legacy_project_without_map_produces_zero_extra_noise(tmp_path: pathlib.Path) -> None:
    proj = _make_case(tmp_path, include_c3=True)
    assert brief_coverage.check(proj) == []
    cli = subprocess.run(
        [sys.executable, str(TOOLS / "strategy_checks" / "brief_coverage.py"), str(proj), "--json"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert cli.returncode == 0
    assert json.loads(cli.stdout) == []

    cons_findings = consistency.check(proj)
    assert not any(item["code"].startswith("brief_coverage.") for item in cons_findings)


def test_coverage_links_local_names_and_does_not_count_examples_as_answers(tmp_path: pathlib.Path) -> None:
    proj = _make_case(tmp_path)
    source = proj / "sources" / "Условия пилота.md"
    source.write_text("Исключить массовые рассылки.\n", encoding="utf-8")
    product = proj / "outputs" / "05_product_strategy.md"
    fixture = "# Стратегия\n\n## Ответ\n\n"
    answer = "Рассылки всей базе исключены из пилота [R2].\n"
    _write_map(proj, ["| R2 | да | Исключить массовые рассылки | sources/Условия пилота.md:1 | закрыто | outputs/05_product_strategy.md#Ответ |"])
    product.write_text(fixture + answer, encoding="utf-8")
    assert not any(f["status"] == "error" for f in brief_coverage.check(proj))
    product.write_text(fixture + "```text\n" + answer + "```\nОткрытый вопрос.\n", encoding="utf-8")
    assert any(f["code"] == "brief_coverage.binding_missing" for f in brief_coverage.check(proj))

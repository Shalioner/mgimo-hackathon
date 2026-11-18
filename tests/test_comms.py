"""Тесты M10: сегменты, воронка коммуникаций, запрещённые обещания и готовность PRM."""
from __future__ import annotations

import json
import pathlib
import shutil
import subprocess
import sys

from strategy_checks import comms
import yaml

ROOT = pathlib.Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "tests" / "fixtures" / "comms"
VALID_STATUSES = {"ok", "warn", "error", "not_assessed"}


def _assert_contract(items: list[dict]) -> None:
    assert isinstance(items, list) and items
    for item in items:
        assert set(item.keys()) == {"code", "status", "message", "file", "line", "ids"}
        assert isinstance(item["code"], str) and item["code"]
        assert item["status"] in VALID_STATUSES
        assert isinstance(item["message"], str) and item["message"]
        assert isinstance(item["file"], str)
        assert isinstance(item["line"], int)
        assert isinstance(item["ids"], list)


def test_existing_active_promise_backed_only_by_a_planned_experiment(tmp_path):
    project = tmp_path / "case"
    shutil.copytree(FIXTURES / "ok_v2", project)
    ledger = project / "state/ledger"
    path = ledger / "promises.yaml"
    promises = yaml.safe_load(path.read_text())
    promises[0]["proof"] = ["EXP-004"]
    path.write_text(yaml.safe_dump(promises, allow_unicode=True))
    exp_path = ledger / "experiments.yaml"
    exp_path.write_text("- id: EXP-004\n  expected_change: Пользователь увидит лимит до оплаты\n")
    findings = comms.check(project)
    assert any(f["code"] == "comms.promise_without_result" and f["status"] == "error"
               and "PRM-001" in f["ids"] for f in findings)
    with exp_path.open("a") as stream:
        stream.write("  result: На учебном экране лимит виден до оплаты\n")
    assert all(f["code"] != "comms.promise_without_result" for f in comms.check(project))


def test_comms_ok_v2_passes() -> None:
    findings = comms.check(FIXTURES / "ok_v2")
    _assert_contract(findings)
    assert all(f["status"] == "ok" for f in findings)


def test_comms_segment_errors() -> None:
    few = comms.check(FIXTURES / "bad_segments")
    _assert_contract(few)
    assert any(f["status"] == "error" and f["code"] == "comms.too_few_segments" for f in few)

    unlisted = comms.check(FIXTURES / "bad_nonprimary_unlisted")
    _assert_contract(unlisted)
    assert any(f["status"] == "error" and f["code"] == "comms.non_primary_not_in_refusals" for f in unlisted)


def test_comms_funnel_errors() -> None:
    findings = comms.check(FIXTURES / "bad_funnel")
    _assert_contract(findings)
    codes = {f["code"] for f in findings if f["status"] == "error"}
    assert "comms.prm_without_stage" in codes
    assert "comms.channel_without_metric" in codes
    assert "comms.largest_drop_uncovered" in codes
    assert "comms.message_lacks_outcome_or_proof" in codes


def test_comms_esim_catches_contradiction_and_zero_active_prm(tmp_path: pathlib.Path) -> None:
    import shutil

    project = ROOT / "projects" / "esim-travel"
    fixed = comms.check(project)
    assert not any(item["code"] == "comms.direction_forbidden_mismatch" for item in fixed)
    replica = tmp_path / "esim-travel"
    shutil.copytree(project, replica)
    strategy = replica / "outputs" / "05_product_strategy.md"
    text = strategy.read_text(encoding="utf-8")
    lines = text.splitlines()
    direction = lines.index("## Выбранное направление") + 1
    lines[direction] = "Пакеты данных, покупка за минуты, автоактивация по прилёте."
    strategy.write_text("\n".join(lines) + "\n", encoding="utf-8")

    findings = comms.check(replica)
    _assert_contract(findings)
    errs = {f["code"]: f for f in findings if f["status"] == "error"}
    # Исходное противоречие остаётся диагностируемым, открытый C3 требует подтверждённых обещаний.
    assert "comms.direction_forbidden_mismatch" in errs
    assert errs["comms.direction_forbidden_mismatch"]["line"] == direction + 1
    assert "06:25" in errs["comms.direction_forbidden_mismatch"]["message"]

    assert "comms.comms_not_ready" in errs
    assert "коммуникация не готова" in errs["comms.comms_not_ready"]["message"]

    # Missing v2 segment & funnel tables remain not_assessed
    na_codes = {f["code"] for f in findings if f["status"] == "not_assessed"}
    assert "comms.segments_not_assessed" in na_codes
    assert "comms.funnel_not_assessed" in na_codes


def test_comms_ryadom_and_example_no_false_errors() -> None:
    for proj in ("ryadom", "example"):
        findings = comms.check(ROOT / "projects" / proj)
        _assert_contract(findings)
        assert not any(f["status"] == "error" for f in findings)
        assert all(f["status"] == "not_assessed" for f in findings)


def test_comms_cli_json_and_exit_codes() -> None:
    bad = subprocess.run(
        [sys.executable, str(ROOT / "tools" / "strategy_checks" / "comms.py"), str(FIXTURES / "bad_funnel"), "--json"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert bad.returncode == 1
    _assert_contract(json.loads(bad.stdout))

    ok = subprocess.run(
        [sys.executable, str(ROOT / "tools" / "strategy_checks" / "comms.py"), str(FIXTURES / "ok_v2"), "--json"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert ok.returncode == 0
    assert all(x["status"] == "ok" for x in json.loads(ok.stdout))


def test_comms_feature_after_prm_empty_outcome_hyp_proof_and_channels(tmp_path: pathlib.Path) -> None:
    import yaml

    proj = tmp_path / "p1_funnel"
    (proj / "outputs").mkdir(parents=True)
    (proj / "state" / "ledger").mkdir(parents=True)
    (proj / "state" / "ledger" / "promises.yaml").write_text(
        yaml.safe_dump([{"id": "PRM-001", "status": "active", "message": "Есть функция", "proof": ["HYP-001"]}], allow_unicode=True),
        encoding="utf-8",
    )
    (proj / "outputs" / "05_product_strategy.md").write_text(
        "# Продуктовая стратегия\n\n## Аудитория и её задача\n"
        "| Сегмент | Задача (JTBD) | Текущая альтернатива | Барьер | Размер | Приоритет | Основания |\n"
        "|---|---|---|---|---|---|---|\n"
        "| Семьи | Контроль | Звонки | Сложность | 1000 | первичный | [CLM-001] |\n"
        "| Подростки | — | Мессенджер | Личное | 500 | отложен | [CLM-002] |\n\n"
        "## Выбранное направление\n"
        "Запускаем пошаговый онбординг.\n\n"
        "## Осознанные отказы\n"
        "От сегмента Подростки отказались.\n",
        encoding="utf-8",
    )
    (proj / "outputs" / "06_comms_strategy.md").write_text(
        "# Коммуникационная стратегия\n\n## Ключевое сообщение и его подтверждение\n"
        "| Этап воронки | Барьер (ID) | Сообщение (PRM) | Результат для клиента | Функция продукта | Подтверждение | Канал | Метрика этапа |\n"
        "|---|---|---|---|---|---|---|---|\n"
        "| Онбординг (падение 50%) | [PAIN-001] | [PRM-001] У нас есть функция карты | — | Карта | [HYP-001] | Push | % кликов |\n\n"
        "## Роль каналов\n"
        "| Канал | Задача канала | Почему этот канал | Метрика этапа |\n"
        "|---|---|---|---|\n"
        "| Email | Дожим | Дёшево | — |\n",
        encoding="utf-8",
    )
    findings = comms.check(proj)
    _assert_contract(findings)
    codes = {f["code"] for f in findings if f["status"] == "error"}
    assert "comms.message_lacks_outcome_or_proof" in codes
    assert "comms.channel_without_metric" in codes


def test_comms_incomplete_or_empty_funnel_table_errors(tmp_path: pathlib.Path) -> None:
    import yaml

    proj = tmp_path / "bad_headers"
    (proj / "outputs").mkdir(parents=True)
    (proj / "state" / "ledger").mkdir(parents=True)
    (proj / "state" / "ledger" / "promises.yaml").write_text(
        yaml.safe_dump([{"id": "PRM-001", "status": "active", "message": "Результат", "proof": ["CLM-001"]}], allow_unicode=True),
        encoding="utf-8",
    )
    # Missing Этап воронки header even though PRM/Канал/Метрика are present
    (proj / "outputs" / "06_comms_strategy.md").write_text(
        "# Коммуникационная стратегия\n\n## Ключевое сообщение и его подтверждение\n"
        "| Барьер | Сообщение (PRM) | Канал | Метрика этапа | Подтверждение |\n"
        "|---|---|---|---|---|\n"
        "| [CLM-001] | [PRM-001] Быстро | Push | % кликов | [CLM-001] |\n",
        encoding="utf-8",
    )
    res = comms.check(proj)
    assert any(f["status"] == "error" and f["code"] == "comms.incomplete_funnel_table" for f in res)
    # Also must NOT emit comms.ok when 05 is absent!
    assert not any(f["code"] == "comms.ok" for f in res)

    # Empty v2 funnel table (header only) must error
    (proj / "outputs" / "06_comms_strategy.md").write_text(
        "# Коммуникационная стратегия\n\n## Ключевое сообщение и его подтверждение\n"
        "| Этап воронки | Барьер (ID) | Сообщение (PRM) | Результат для клиента | Функция продукта | Подтверждение | Канал | Метрика этапа |\n"
        "|---|---|---|---|---|---|---|---|\n",
        encoding="utf-8",
    )
    res_empty = comms.check(proj)
    assert any(f["status"] == "error" and f["code"] == "comms.empty_funnel_table" for f in res_empty)

    # Row with unfilled [PRM-…] placeholder and no refusal must error with row_without_prm_or_refusal
    (proj / "outputs" / "06_comms_strategy.md").write_text(
        "# Коммуникационная стратегия\n\n## Ключевое сообщение и его подтверждение\n"
        "| Этап воронки | Барьер (ID) | Сообщение (PRM) | Результат для клиента | Функция продукта | Подтверждение | Канал | Метрика этапа |\n"
        "|---|---|---|---|---|---|---|---|\n"
        "| Регистрация | [PAIN-001] | [PRM-…] | Быстро | Авто | [CLM-001] | Push | % кликов |\n",
        encoding="utf-8",
    )
    res_noprm = comms.check(proj)
    assert any(f["status"] == "error" and f["code"] == "comms.row_without_prm_or_refusal" for f in res_noprm)


def test_comms_quotes_and_local_negations(tmp_path: pathlib.Path) -> None:
    import yaml

    proj = tmp_path / "quotes_neg"
    (proj / "outputs").mkdir(parents=True)
    (proj / "state" / "ledger").mkdir(parents=True)
    (proj / "state" / "ledger" / "promises.yaml").write_text(
        yaml.safe_dump([{"id": "PRM-001", "status": "active", "message": "Прозрачность", "proof": ["CLM-001"]}], allow_unicode=True),
        encoding="utf-8",
    )
    # 1) Brand quote after preposition in 06 ('В «МегаФон Трэвел»') must NOT false-match brand mention in 05
    (proj / "outputs" / "05_product_strategy.md").write_text(
        "# Продуктовая стратегия\n\n## Выбранное направление\n"
        "Развиваем сервис МегаФон Трэвел с прозрачным выбором тарифа.\n",
        encoding="utf-8",
    )
    (proj / "outputs" / "06_comms_strategy.md").write_text(
        "# Коммуникационная стратегия\n\n## Чего мы не обещаем\n"
        "В «МегаФон Трэвел» не обещаем полный безлимит.\n",
        encoding="utf-8",
    )
    res_brand = comms.check(proj)
    assert not any(f["code"] == "comms.direction_forbidden_mismatch" for f in res_brand)

    # 2) Real contradiction in 05 despite local negation on ANOTHER object ('без визита в салон') MUST be caught
    (proj / "outputs" / "05_product_strategy.md").write_text(
        "# Продуктовая стратегия\n\n## Выбранное направление\n"
        "Запускаем автоактивацию при прилёте без визита в салон.\n",
        encoding="utf-8",
    )
    (proj / "outputs" / "06_comms_strategy.md").write_text(
        "# Коммуникационная стратегия\n\n## Чего мы не обещаем\n"
        "Автоактивацию проверять отдельно; не обещаем «автоактивацию при прилёте».\n",
        encoding="utf-8",
    )
    res_contra = comms.check(proj)
    assert any(f["status"] == "error" and f["code"] == "comms.direction_forbidden_mismatch" for f in res_contra)


def test_comms_fresh_project_before_05_06_is_not_assessed(tmp_path: pathlib.Path) -> None:
    proj = tmp_path / "fresh"
    (proj / "outputs").mkdir(parents=True)
    (proj / "state" / "ledger").mkdir(parents=True)
    (proj / "state" / "ledger" / "promises.yaml").write_text("[]\n", encoding="utf-8")

    findings = comms.check(proj)
    _assert_contract(findings)
    assert not any(f["status"] == "error" for f in findings)
    assert all(f["status"] == "not_assessed" for f in findings)


def test_comms_integrated_templates_e2e(tmp_path: pathlib.Path) -> None:
    import yaml

    unfilled = tmp_path / "unfilled"
    (unfilled / "outputs").mkdir(parents=True)
    (unfilled / "state" / "ledger").mkdir(parents=True)
    (unfilled / "state" / "ledger" / "promises.yaml").write_text("[]\n", encoding="utf-8")
    (unfilled / "outputs" / "05_product_strategy.md").write_text(
        (ROOT / "knowledge" / "templates" / "05_product_strategy.md").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    (unfilled / "outputs" / "06_comms_strategy.md").write_text(
        (ROOT / "knowledge" / "templates" / "06_comms_strategy.md").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    bad = comms.check(unfilled)
    assert any(f["status"] == "error" for f in bad)

    filled = tmp_path / "filled"
    (filled / "outputs").mkdir(parents=True)
    (filled / "state" / "ledger").mkdir(parents=True)
    (filled / "state" / "ledger" / "promises.yaml").write_text(
        yaml.safe_dump(
            [{"id": "PRM-001", "status": "active", "message": "Первая геопозиция за 2 шага", "proof": ["CLM-001"]}],
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    (filled / "outputs" / "05_product_strategy.md").write_text(
        "# Продуктовая стратегия\n\n## Аудитория и её задача\n"
        "| Сегмент | Задача (JTBD) | Текущая альтернатива | Барьер | Размер | Приоритет | Основания |\n"
        "|---|---|---|---|---|---|---|\n"
        "| Семьи с детьми 6–12 | Видеть ребёнка в городе | Звонки и мессенджеры | Долгий онбординг | 100 000 | первичный | [CLM-001, PAIN-001] |\n"
        "| Школьники старшие | Самостоятельность | Нативные карты | Личное пространство | 40 000 | отложен | [CLM-002] |\n\n"
        "## Выбранное направление\nУпрощаем онбординг семей с детьми 6–12.\n\n"
        "## Осознанные отказы\nШкольники старшие отложены на второй этап.\n",
        encoding="utf-8",
    )
    (filled / "outputs" / "06_comms_strategy.md").write_text(
        "# Коммуникационная стратегия\n\n## Ключевое сообщение и его подтверждение\n"
        "| Этап воронки | Барьер (ID) | Сообщение (PRM) | Результат для клиента | Функция продукта | Подтверждение | Канал | Метрика этапа |\n"
        "|---|---|---|---|---|---|---|---|\n"
        "| Онбординг (наибольшее падение 50%) | [PAIN-001] | [PRM-001] | Увидите ребёнка на карте за 2 шага | Мастер разрешений | [CLM-001] | Экран онбординга | % завершивших настройку |\n\n"
        "## Роль каналов\n"
        "| Канал | Задача канала | Почему этот канал | Метрика этапа |\n"
        "|---|---|---|---|\n"
        "| Экран онбординга | Провести к первой геопозиции | Точка максимального отвала | % завершивших настройку |\n\n"
        "## Чего мы не обещаем\nНе обещаем работу без геолокации.\n",
        encoding="utf-8",
    )
    good = comms.check(filled)
    _assert_contract(good)
    assert all(f["status"] == "ok" for f in good)

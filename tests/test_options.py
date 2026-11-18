"""Тесты M09: варианты как продуктовые ходы и расчёт потолка эффекта."""
from __future__ import annotations

import json
import pathlib
import subprocess
import sys

from strategy_checks import options

ROOT = pathlib.Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "tests" / "fixtures" / "options"
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


def test_options_ok_v2_passes() -> None:
    findings = options.check(FIXTURES / "ok_v2")
    _assert_contract(findings)
    assert all(f["status"] == "ok" for f in findings)


def test_options_axis_warnings() -> None:
    single = options.check(FIXTURES / "warn_single_axis")
    _assert_contract(single)
    assert any(f["status"] == "warn" and f["code"] == "options.single_axis" for f in single)

    research = options.check(FIXTURES / "warn_research_only")
    _assert_contract(research)
    assert any(f["status"] == "warn" and f["code"] == "options.research_only_axes" for f in research)


def test_options_unweighted_sum_error() -> None:
    findings = options.check(FIXTURES / "bad_unweighted_sum")
    _assert_contract(findings)
    assert any(f["status"] == "error" and f["code"] == "options.unweighted_sum" for f in findings)


def test_options_objections_and_basis_errors() -> None:
    findings = options.check(FIXTURES / "bad_objections")
    _assert_contract(findings)
    codes = {f["code"] for f in findings if f["status"] == "error"}
    assert "options.forbidden_objection_phrase" in codes
    assert "options.objection_missing_id" in codes
    assert "options.empty_main_basis" in codes


def test_options_missing_premise_not_ready() -> None:
    findings = options.check(FIXTURES / "bad_no_premise")
    _assert_contract(findings)
    errs = [f for f in findings if f["status"] == "error" and f["code"] == "options.missing_premise_objection"]
    assert errs
    assert "НЕ ГОТОВО" in errs[0]["message"]


def test_options_ryadom_funnel_ceiling() -> None:
    ceiling = options.compute_ryadom_funnel_ceiling(ROOT / "projects" / "ryadom")
    assert ceiling is not None
    # (3100 - 2540) / 3100 * (31 - 6) = 560/3100 * 25 = 4.5161 п.п. (4.5 ± 0.1 п.п.)
    assert abs(ceiling["ceiling_pp"] - 4.5) <= 0.1


def test_options_legacy_projects_not_assessed() -> None:
    for proj in ("ryadom", "esim-travel", "example"):
        findings = options.check(ROOT / "projects" / proj)
        _assert_contract(findings)
        assert all(f["status"] == "not_assessed" for f in findings)


def test_options_cli_json_and_exit_codes() -> None:
    bad = subprocess.run(
        [sys.executable, str(ROOT / "tools" / "strategy_checks" / "options.py"), str(FIXTURES / "bad_unweighted_sum"), "--json"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert bad.returncode == 1
    _assert_contract(json.loads(bad.stdout))

    ok = subprocess.run(
        [sys.executable, str(ROOT / "tools" / "strategy_checks" / "options.py"), str(FIXTURES / "ok_v2"), "--json"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert ok.returncode == 0
    assert all(x["status"] == "ok" for x in json.loads(ok.stdout))


def test_options_recommended_not_top_override(tmp_path: pathlib.Path) -> None:
    src = (FIXTURES / "ok_v2" / "outputs" / "04_options.md").read_text(encoding="utf-8")
    # Меняем рекомендацию на ALT-002 (ниже по сумме), не добавляя «Почему против таблицы»
    bad_text = src.replace("**ALT-001**", "**ALT-002**")
    proj_bad = tmp_path / "bad_override"
    (proj_bad / "outputs").mkdir(parents=True)
    (proj_bad / "outputs" / "04_options.md").write_text(bad_text, encoding="utf-8")
    findings = options.check(proj_bad)
    _assert_contract(findings)
    assert any(f["status"] == "error" and f["code"] == "options.recommended_not_top" for f in findings)

    # С явным обоснованием «Почему против таблицы» ошибка снимается
    ok_text = bad_text + "\n**Почему против таблицы:** ALT-002 проще и дешевле в 2 раза при меньшем риске.\n"
    proj_ok = tmp_path / "ok_override"
    (proj_ok / "outputs").mkdir(parents=True)
    (proj_ok / "outputs" / "04_options.md").write_text(ok_text, encoding="utf-8")
    findings_ok = options.check(proj_ok)
    _assert_contract(findings_ok)
    assert not any(f["code"] == "options.recommended_not_top" for f in findings_ok)


def test_options_table_weights_stub_rationale_and_unweighted_sum(tmp_path: pathlib.Path) -> None:
    # 1) Table weights + 'Затраты и обратимость' + '(1–5)' + stub rationale must still catch ALT-002 < ALT-001
    proj = tmp_path / "table_weights"
    (proj / "outputs").mkdir(parents=True)
    (proj / "outputs" / "04_options.md").write_text(
        "# Варианты\n\n"
        "## Критерии сравнения и веса\n"
        "- **Правило выбора:** максимальная взвешенная сумма (сумма весов = 1.00).\n\n"
        "| Критерий | Вес | Почему важен |\n"
        "|---|---:|---|\n"
        "| Влияние на цель | 0.50 | Основной KPI |\n"
        "| Затраты и обратимость | 0.30 | Ресурсы |\n"
        "| Скорость | 0.20 | Срок пилота |\n\n"
        "## Сравнение\n"
        "| | ALT-001 Продукт | ALT-002 Канал | ALT-003 Оставить как есть | ALT-004 Закрыть |\n"
        "|---|---|---|---|---|\n"
        "| **Ось различия** | ценность | канал | — | — |\n"
        "| Влияние на цель (1–5) | 5 | 2 | 1 | 1 |\n"
        "| Затраты и обратимость (1–5) | 4 | 3 | 5 | 5 |\n"
        "| Скорость (1–5) | 4 | 4 | 5 | 5 |\n"
        "| **Потолок эффекта** | +4.5 п.п. | +1.0 п.п. | 0 | 0 |\n"
        "| **Главное основание** | [CLM-001] | [CLM-002] | [CLM-003] | [CLM-004] |\n"
        "| **Сильное возражение** | Если [CLM-001] неверно, спрос ниже | Если [EXP-001] неверно, канал пуст | Если [CLM-003] неверно, отток растёт | Если [CLM-004] неверно, потеряем нишу |\n"
        "| Почему против таблицы | *(заполнить, если рекомендован не лидер)* | — | — | — |\n\n"
        "## Что может изменить ранжирование\n"
        "- **При каком весе меняется лидер:** при весе скорости > 0.7.\n\n"
        "## Возражение к постановке\n"
        "- Допущение брифа: отток вызван ценой — что опровергает [CLM-001]: главная причина в онбординге.\n\n"
        "## Рекомендация агента\n"
        "**ALT-002** — рекомендуем начать с канала.\n",
        encoding="utf-8",
    )
    res = options.check(proj)
    _assert_contract(res)
    assert any(f["status"] == "error" and f["code"] == "options.recommended_not_top" for f in res)

    # 2) Unweighted 'Сумма' table without other v2 markers must error, NOT return not_assessed
    proj_sum = tmp_path / "unweighted_only"
    (proj_sum / "outputs").mkdir(parents=True)
    (proj_sum / "outputs" / "04_options.md").write_text(
        "# Варианты\n\n## Сравнение\n"
        "| | ALT-001 A | ALT-002 B | ALT-003 Оставить как есть |\n"
        "|---|---|---|---|\n"
        "| Влияние | 4 | 3 | 1 |\n"
        "| Риск | 3 | 5 | 5 |\n"
        "| Сумма | 7 | 8 | 6 |\n",
        encoding="utf-8",
    )
    res_sum = options.check(proj_sum)
    assert any(f["status"] == "error" and f["code"] == "options.unweighted_sum" for f in res_sum)

    # 3) Prose word 'вес' without weights block or v2 markers stays not_assessed
    proj_prose = tmp_path / "prose_only"
    (proj_prose / "outputs").mkdir(parents=True)
    (proj_prose / "outputs" / "04_options.md").write_text(
        "# Варианты\n\nНаибольший вес для пользователя имеет простота подключения.\n",
        encoding="utf-8",
    )
    res_prose = options.check(proj_prose)
    assert all(f["status"] == "not_assessed" for f in res_prose)


def test_options_based_on_validation_empty_ceiling_and_cli(tmp_path: pathlib.Path) -> None:
    import yaml

    proj = tmp_path / "based_on_case"
    (proj / "outputs").mkdir(parents=True)
    (proj / "state" / "ledger").mkdir(parents=True)
    (proj / "state" / "ledger" / "decisions.yaml").write_text(
        yaml.safe_dump([{"id": "DEC-001", "based_on": ["CLM-001", "EXP-001"]}], allow_unicode=True),
        encoding="utf-8",
    )
    (proj / "state" / "ledger" / "alternatives.yaml").write_text(
        yaml.safe_dump(
            [
                {"id": "ALT-001", "kind": "invest", "supported_by": ["CLM-001"]},
                {"id": "ALT-002", "kind": "change", "supported_by": ["EXP-001"]},
                {"id": "ALT-003", "kind": "status_quo", "supported_by": ["CLM-001"]},
                {"id": "ALT-004", "kind": "kill", "supported_by": ["CLM-001"]},
            ],
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    (proj / "outputs" / "04_options.md").write_text(
        "# Варианты\n\n## Критерии сравнения и веса\n"
        "- **Правило выбора:** максимальная взвешенная сумма.\n\n"
        "| Критерий | Вес |\n|---|---:|\n| Влияние | 0.6 |\n| Риск | 0.4 |\n\n"
        "## Сравнение\n"
        "| | ALT-001 A | ALT-002 B | ALT-003 Оставить как есть | ALT-004 Закрыть |\n"
        "|---|---|---|---|---|\n"
        "| **Ось различия** | ценность | монетизация | — | — |\n"
        "| Влияние (1–5) | 5 | 3 | 1 | 1 |\n"
        "| Риск (1–5) | 4 | 4 | 5 | 5 |\n"
        "| **Потолок эффекта** | — | +2.0 п.п. | 0 | 0 |\n"
        "| **Главное основание** | [CLM-001] | [EXP-001] | [CLM-001] | [CLM-001] |\n"
        "| **Сильное возражение** | Если [CLM-999] неверно, то падение | Если [EXP-001] неверно, то убыток | Если [CLM-001] неверно, то риск | Если [CLM-001] неверно, то стоп |\n\n"
        "## Что может изменить ранжирование\n"
        "- **При каком весе меняется лидер:** при весе риска > 0.85.\n\n"
        "## Возражение к постановке\n"
        "- Допущение брифа: спрос есть — опровергает [CLM-001]: нужна проверка.\n\n"
        "## Рекомендация агента\n**ALT-001** — лидер.\n",
        encoding="utf-8",
    )
    res = options.check(proj)
    codes = {f["code"] for f in res if f["status"] == "error"}
    assert "options.empty_effect_ceiling" in codes
    assert "options.objection_id_not_in_based_on" in codes

    # Test --ceiling CLI mode
    cli = subprocess.run(
        [
            sys.executable,
            str(ROOT / "tools" / "strategy_checks" / "options.py"),
            "--ceiling",
            "--total",
            "3100",
            "--passed",
            "2540",
            "--rate-passed",
            "31",
            "--rate-blocked",
            "6",
            "--json",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    data = json.loads(cli.stdout)
    assert abs(data["ceiling_pp"] - 4.5161) < 0.01


def test_options_integrated_template_e2e(tmp_path: pathlib.Path) -> None:
    unfilled = tmp_path / "unfilled"
    (unfilled / "outputs").mkdir(parents=True)
    (unfilled / "outputs" / "04_options.md").write_text(
        (ROOT / "knowledge" / "templates" / "04_options.md").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    bad = options.check(unfilled)
    assert any(f["status"] == "error" for f in bad)

    filled = tmp_path / "filled"
    (filled / "outputs").mkdir(parents=True)
    (filled / "outputs" / "04_options.md").write_text(
        "# Варианты решения\n\n"
        "## Критерии сравнения и веса\n"
        "- **Правило выбора:** максимальная взвешенная сумма (сумма весов = 1.00).\n\n"
        "| Критерий | Вес | Почему важен для цели брифа |\n"
        "|---|---:|---|\n"
        "| Влияние на удержание | 0.40 | Цель брифа |\n"
        "| Затраты и обратимость | 0.35 | Ограничение команды |\n"
        "| Скорость проверки | 0.25 | Срок квартала |\n\n"
        "## Сравнение\n"
        "| | ALT-001 Новый онбординг | ALT-002 Тарифный бандл | ALT-003 Оставить как есть | ALT-004 Закрыть |\n"
        "|---|---|---|---|---|\n"
        "| Суть | Пошаговый запуск | Скидка на пакет | Без изменений | Заморозка |\n"
        "| **Ось различия** | ценность | монетизация | — | — |\n"
        "| Влияние на удержание (1–5) | 5 | 3 | 1 | 1 |\n"
        "| Затраты и обратимость (1–5) | 4 | 3 | 5 | 5 |\n"
        "| Скорость проверки (1–5) | 4 | 4 | 5 | 5 |\n"
        "| Взвешенная сумма | 4.40 | 3.25 | 3.40 | 3.40 |\n"
        "| **Потолок эффекта** | +4.5 п.п. | +2.1 п.п. | 0 п.п. | 0 п.п. |\n"
        "| Затраты ↔ польза | 2 спринта [CLM-001] | 4 спринта [CLM-002] | 0 | 0 |\n"
        "| **Главное основание** | [CLM-001] | [CLM-002] | [CLM-003] | [CLM-004] |\n"
        "| **Сильное возражение** | Если [CLM-001] неверно, то падения не будет | Если [CLM-002] неверно, то каннибализация | Если [CLM-003] неверно, то отток вырастет | Если [CLM-004] неверно, то потеряем сегмент |\n"
        "| Почему против таблицы | *(заполнить, если рекомендован не лидер)* | | | |\n\n"
        "## Что может изменить ранжирование\n"
        "- **При каком весе меняется лидер:** при весе скорости > 0.85.\n\n"
        "## Возражение к постановке\n"
        "- Допущение брифа: пользователи уходят из-за цены — опровергает [CLM-001]: падение происходит до оплаты.\n\n"
        "## Рекомендация агента\n"
        "**ALT-001** — лидер по взвешенной сумме.\n",
        encoding="utf-8",
    )
    good = options.check(filled)
    _assert_contract(good)
    assert all(f["status"] == "ok" for f in good)


def test_carbon_n1_common_rationale_missing_and_unmatched_weights(tmp_path):
    original = (FIXTURES / "ok_v2" / "outputs/04_options.md").read_text()
    project = tmp_path / "project"
    (project / "outputs").mkdir(parents=True)
    path = project / "outputs/04_options.md"
    losing = original.replace("**ALT-001** — лучший", "**ALT-002** — лучший")
    path.write_text(losing)
    assert any(f["code"] == "options.recommended_not_top" for f in options.check(project))
    rationale = "| Почему против таблицы | ALT-001 заблокирован юристами, рекомендуем ALT-002 | | | |\n"
    path.write_text(losing.replace("\n\nПравило выбора:", "\n" + rationale + "\nПравило выбора:"))
    assert not any(f["status"] == "error" for f in options.check(project))

    # Все пустые веса и пустая сумма не должны давать options.ok.
    unweighted = "\n".join(line for line in original.splitlines() if "(вес " not in line and not line.startswith("Правило выбора:"))
    unweighted = unweighted.replace("| Взвешенный балл | 4.5 | 3.0 | 3.0 | 2.7 |", "| Взвешенный балл | | | | |")
    path.write_text(unweighted)
    assert any(f["code"] == "options.missing_weights" for f in options.check(project))

    # Непустые, но несопоставленные веса не доказывают ранжирование.
    unmatched = losing.replace("**Влияние на удержание**", "**Экономический эффект**", 1).replace("**Затраты и обратимость**", "**Объём рынка**", 1).replace("**Скорость проверки**", "**Горизонт инвестиций**", 1)
    path.write_text(unmatched)
    assert any(f["code"] == "options.unmatched_weights" for f in options.check(project))
    assert options._is_placeholder("Критерий 1")
    assert not options._is_placeholder("Влияние на удержание")


def test_carbon_n1_explicit_nonweighted_rule_remains_available(tmp_path):
    original = (FIXTURES / "ok_v2" / "outputs/04_options.md").read_text()
    text = "\n".join(line for line in original.splitlines() if "(вес " not in line and "Взвешенный балл" not in line and not line.startswith("Правило выбора:"))
    text += "\nПравило выбора: пороги — сначала допустимый риск, затем скорость.\n"
    (tmp_path / "outputs").mkdir()
    (tmp_path / "outputs/04_options.md").write_text(text)
    assert not any(f["code"] in {"options.missing_weights", "options.unweighted_sum"} for f in options.check(tmp_path))

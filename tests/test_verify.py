"""Тесты проверки утверждений кодом (tools/verify.py)."""
import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
import verify as V  # noqa: E402


@pytest.fixture
def proj(tmp_path):
    dst = tmp_path / "example"
    shutil.copytree(ROOT / "projects" / "example", dst)
    (dst / "outputs").mkdir(exist_ok=True)
    return dst


def run(proj, text):
    (proj / "outputs" / "03_situation.md").write_text(text, encoding="utf-8")
    return {(i["verdict"], i["text"][:40]) for i in V.verify(proj, [])["items"]}, V.verify(proj, [])


def verdicts(proj, text):
    return [i["verdict"] for i in run(proj, text)[1]["items"]]


def test_number_backed_by_quote_is_ok(proj):
    assert verdicts(proj, "Синхронизация — 34% обращений [CLM-002].") == ["ok"]


def test_number_without_ref_is_unsupported(proj):
    assert verdicts(proj, "Рынок вырос на 85% за год.") == ["unsupported"]


@pytest.mark.parametrize(("budget", "exit_code"), [("120000 рублей", 0), ("130000 рублей", 1), ("120000 евро", 1)])
def test_brief_accepts_only_verbatim_user_budget(proj, budget, exit_code):
    brief = proj / "outputs" / "01_brief.md"
    brief.write_text("**Исходный запрос (дословно):**\n> «Нужен план. Бюджет проверки — 120000 рублей.»\n\n"
                     f"| Ограничения | Бюджет проверки — {budget}. | подтверждено запросом |\n",
                     encoding="utf-8")
    assert V.main([str(proj), "--files", "01", "--strict"]) == exit_code


@pytest.mark.parametrize("constraint", ["Бюджет проверки — 120 тыс. рублей.",
                                        "Бюджет — 120000 руб. на весь пилот.",
                                        "Решение в 2027 г. до встречи с заказчиком."])
def test_request_with_internal_period(proj, constraint):
    (proj / "outputs/01_brief.md").write_text(
        f"**Исходный запрос (дословно):**\n> «Подготовь бриф. {constraint} Начнём завтра.»\n"
        f"| Ограничения | {constraint} | подтверждено запросом |\n", encoding="utf-8")
    assert V.main([str(proj), "--files", "01", "--strict"]) == 0


def test_number_not_in_referenced_claim_needs_check(proj):
    assert verdicts(proj, "Синхронизация — 54% обращений [CLM-002].") == ["check"]


@pytest.mark.parametrize("format_line", ["{} [CLM-002].", "| {} [CLM-002] |"])
def test_direct_fact_rate_unit_mismatch(proj, format_line):
    claims = proj / "state" / "ledger" / "claims.yaml"
    text = claims.read_text(encoding="utf-8")
    claims.write_text(text.replace(
        "34% обращений в поддержку касаются медленной или несработавшей синхронизации.",
        "Отток снизился на 4%.",
    ), encoding="utf-8")

    _, result = run(proj, format_line.format("Отток снизился на 4 п.п."))
    assert result["counts"] == {"check": 1}
    assert "п.п. вместо %" in result["items"][0]["note"]
    assert verdicts(proj, format_line.format("Отток снизился на 4%")) == ["ok"]


def test_missing_and_stale_refs(proj):
    assert verdicts(proj, "См. [CLM-999].") == ["wrong-ref"]
    import yaml
    f = proj / "state/ledger/claims.yaml"
    data = yaml.safe_load(f.read_text(encoding="utf-8"))
    for c in data:
        if c["id"] == "CLM-003":
            c.update(status="superseded", superseded_by="CLM-006")
    f.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
    assert verdicts(proj, "Конкурент даёт 15 ГБ [CLM-003].") == ["stale-ref"]


def test_decision_history_keeps_retired_ids_distinct_from_current_refs(proj):
    import yaml
    claims = proj / "state" / "ledger" / "claims.yaml"
    records = yaml.safe_load(claims.read_text(encoding="utf-8"))
    next(record for record in records if record["id"] == "CLM-003")["status"] = "retracted"
    claims.write_text(yaml.safe_dump(records, allow_unicode=True), encoding="utf-8")
    log = proj / "outputs" / "08_decision_log.md"
    historical = "## История пересмотров\n\n- Ранее использовали [CLM-003].\n"
    log.write_text(historical, encoding="utf-8")
    assert V.verify(proj, ["08"])["counts"] == {"historical-ref": 1}
    assert V.main([str(proj), "--files", "08"]) == 0

    log.write_text(historical + "\n- Несуществующая запись [CLM-999].\n\n"
                   "## Текущее решение\n\n- Действуем по [CLM-003].\n", encoding="utf-8")
    assert V.verify(proj, ["08"])["counts"] == {"historical-ref": 1, "wrong-ref": 1, "stale-ref": 1}
    assert V.main([str(proj), "--files", "08"]) == 1


def test_ranges_and_short_lists_expand():
    assert V.expand_refs("CLM-001–003") == ["CLM-001", "CLM-002", "CLM-003"]
    assert V.expand_refs(V.normalize_short_lists("CLM-005, 009, 010")) == ["CLM-005", "CLM-009", "CLM-010"]
    assert V.numbers("см. CLM-019–023 и HYP-001") == set()


def test_noise_is_not_counted_as_numbers():
    assert V.numbers("Шаг 5, C2, T3, Q1, 2026-11-10, 04_options.md") == set()
    assert V.numbers("| ALT-001 | 4 | 3 | 5 |", table_row=True) == set()  # баллы 1–5
    assert V.numbers("1 800 ₽ и 1,8 млрд") == {"1800", "1.8"}


def test_meta_lines_skipped(proj):
    assert verdicts(proj, "**Счётчик:** 13 фактов / 2 гипотезы") == []


def test_number_found_one_level_deeper(proj):
    # HYP-001 опирается на CLM-004 → CLM-001/002; число 34 есть в CLM-002 на два шага ниже — не найдётся на глубине 1
    assert verdicts(proj, "Ускорение синхронизации даст +3 п.п. [HYP-001].") == ["ok"]


def test_cli_exit_code_and_sample(proj, capsys):
    (proj / "outputs" / "03_situation.md").write_text("Рынок 85% [CLM-999].\nСинхронизация — 34% [CLM-002].\n",
                                                       encoding="utf-8")
    assert V.main([str(proj)]) == 1  # wrong-ref — жёсткая ошибка
    (proj / "outputs" / "03_situation.md").write_text("Синхронизация — 34% [CLM-002].\n", encoding="utf-8")
    assert V.main([str(proj), "--sample", "1"]) == 0
    assert "[sample]" in capsys.readouterr().out


def test_strict_mode_unsupported_and_json_fields(proj, capsys):
    import json
    (proj / "outputs" / "03_situation.md").write_text("Рынок вырос на 85% за год.\n", encoding="utf-8")
    assert V.main([str(proj)]) == 0  # обратная совместимость: без --strict exit 0
    capsys.readouterr()
    assert V.main([str(proj), "--strict", "--json"]) == 1  # --strict: unsupported -> exit 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["items"]
    assert all("file" in it and "verdict" in it for it in payload["items"])
    assert any(it["file"] == "03_situation.md" and it["verdict"] == "unsupported" for it in payload["items"])


# ---------- целый пакет: одинаковый «Главное» и строки, не обновлённые после пересмотра ----------
def test_duplicate_main_block_is_error(proj):
    main = "# Главное\nРуководитель выбрал ALT-001; бюджет до 24 часов, разбор 24 ноября, команда прежняя [DEC-001].\n\n## Дальше\n"
    for f in ("03_situation.md", "04_options.md"):
        (proj / "outputs" / f).write_text(main + "текст", encoding="utf-8")
    (proj / "outputs" / "05_product_strategy.md").write_text(
        "## Главное\n- Направление: онбординг для новых пользователей, отказ от новых функций в этом квартале.\n", encoding="utf-8")
    res = V.verify(proj, [])
    dups = [i for i in res["items"] if i["verdict"] == "dup-main"]
    assert len(dups) == 1 and dups[0]["file"] == "04_options.md" and "03_situation.md" in dups[0]["note"]
    assert V.main([str(proj)]) == 1


def test_multiple_main_blocks_in_one_file_and_h1_h2_normalization(proj):
    (proj / "outputs" / "05_product_strategy.md").write_text(
        "# Главное\nВыбор продукта A для сегмента X на ближайший квартал.\n\n"
        "## Исторический снимок\n\n"
        "## Главное\nПрежний выбор продукта B.\n",
        encoding="utf-8",
    )
    res = V.verify(proj, [])
    dups = [i for i in res["items"] if i["verdict"] == "dup-main"]
    assert len(dups) == 1 and dups[0]["file"] == "05_product_strategy.md" and dups[0]["line"] == 6


def test_copied_main_prefix_in_09_run_report_is_caught(proj):
    shared = (
        "# Главное\n"
        "Руководитель выбрал ALT-001; бюджет до 24 часов, разбор 24 ноября, команда прежняя [DEC-001].\n"
    )
    (proj / "outputs" / "05_product_strategy.md").write_text(shared + "\n## Стратегия\nТекст.\n", encoding="utf-8")
    (proj / "outputs" / "09_run_report.md").write_text(
        shared + "\nДополнительный длинный абзац отчёта о прогоне с цифрами 57 объектов и журналом.\n\n## Отчёт\n",
        encoding="utf-8",
    )
    res = V.verify(proj, [])
    dups = [i for i in res["items"] if i["verdict"] == "dup-main"]
    assert any(i["file"] == "09_run_report.md" and "05_product_strategy.md" in i["note"] for i in dups)


def _mark(proj, oid, field_text):
    import yaml
    for f in (proj / "state/ledger").glob("*.yaml"):
        data = yaml.safe_load(f.read_text(encoding="utf-8")) or []
        for o in data:
            if o.get("id") == oid:
                o["status_note"] = field_text
                f.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
                return


def test_lines_not_updated_after_change_are_listed(proj):
    _mark(proj, "HYP-001", "Пересмотрено по CHG-001: ослаблена — синхронизация у 8%.")      # изменена по существу
    _mark(proj, "ALT-001", "Пересмотрено по CHG-001: проверено, без изменений.")              # только проверена
    (proj / "outputs" / "03_situation.md").write_text(
        "Синхронизация — главная причина ухода [HYP-001].\n"
        "Синхронизация — уже не главная причина (пересмотрено по CHG-001) [HYP-001].\n"
        "Вариант про синхронизацию остаётся [ALT-001].\n"
        "Решение пока прежнее [DEC-001].\n", encoding="utf-8")
    assert V.revised_objects(V.L.Ledger(proj), "CHG-001") == {"HYP-001"}
    res = V.verify(proj, [], change="CHG-001")
    stale = [i for i in res["items"] if i["verdict"] == "stale?"]
    assert [i["line"] for i in stale] == [1] and stale[0]["refs"] == ["HYP-001"]
    assert V.main([str(proj), "--change", "CHG-001"]) == 1
    assert V.main([str(proj), "--change", "CHG-999"]) == 2


def test_changed_experiment_and_promise_require_revision_with_same_choice(proj):
    _mark(proj, "EXP-001", "Пересмотрено по CHG-001: обновлён порог.")
    _mark(proj, "PRM-001", "Пересмотрено по CHG-001: сужено обещание.")
    doc = proj / "outputs/07_validation_plan.md"
    doc.write_text("Порог 50% [EXP-001].\nПубличное обещание [PRM-001].\n", encoding="utf-8")
    stale = [item for item in V.verify(proj, ["07"], "CHG-001")["items"]
             if item["verdict"] == "stale?"]
    assert [item["refs"] for item in stale] == [["EXP-001"], ["PRM-001"]]


@pytest.mark.parametrize("mark", [
    "проверено по CHG-999", "пересмотрено по CHG-999", "без изменений",
    "пересмотрено", "проверено по CHG-0010", "проверено по CHG-001-extra",
    "см. CHG-001", "CHG-999 проверено по; см. CHG-001",
])
def test_only_explicit_current_change_mark_clears_stale(proj, mark):
    _mark(proj, "HYP-001", "Пересмотрено по CHG-001: ослаблена.")
    (proj / "outputs/03_situation.md").write_text(f"Причина [HYP-001] ({mark}).\n", encoding="utf-8")
    assert V.verify(proj, [], "CHG-001")["counts"]["stale?"] == 1
    assert V.main([str(proj), "--change", "CHG-001"]) == 1


@pytest.mark.parametrize("mark", ["проверено по CHG-001", "Пересмотрено по CHG-001",
                                  "без изменений по CHG-001", "**проверено по [CHG-001]**"])
def test_manual_current_change_mark_is_accepted(proj, mark):
    _mark(proj, "HYP-001", "Пересмотрено по CHG-001: ослаблена.")
    (proj / "outputs/03_situation.md").write_text(f"Причина [HYP-001] ({mark}).\n", encoding="utf-8")
    assert "stale?" not in V.verify(proj, [], "CHG-001")["counts"]
    assert V.main([str(proj), "--change", "CHG-001"]) == 0


def test_sequential_changes_require_new_mark(proj):
    import yaml
    changes = proj / "state/ledger/changes.yaml"
    data = yaml.safe_load(changes.read_text(encoding="utf-8"))
    data.append(dict(data[0], id="CHG-002"))
    changes.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    _mark(proj, "HYP-001", "Пересмотрено по CHG-001: ослаблена.")
    doc = proj / "outputs/03_situation.md"
    doc.write_text("Причина [HYP-001] (проверено по CHG-001).\n", encoding="utf-8")
    assert V.main([str(proj), "--change", "CHG-001"]) == 0
    _mark(proj, "HYP-001", "Пересмотрено по CHG-002: опровергнута.")
    assert V.main([str(proj), "--change", "CHG-002"]) == 1
    doc.write_text(doc.read_text(encoding="utf-8").rstrip() + " (проверено по CHG-002)\n", encoding="utf-8")
    assert V.main([str(proj), "--change", "CHG-002"]) == 0


def test_change_id_prefix_in_ledger_does_not_touch_object(proj):
    _mark(proj, "HYP-001", "Пересмотрено по CHG-0010: ослаблена.")
    assert "HYP-001" not in V.revised_objects(V.L.Ledger(proj), "CHG-001")


@pytest.mark.parametrize("heading", ["Исторический снимок до CHG-001", "История до CHG-001", "Архив до CHG-001"])
def test_historical_tail_does_not_require_current_change_marks(proj, heading):
    _mark(proj, "HYP-001", "Пересмотрено по CHG-001: ослаблена.")
    (proj / "outputs/03_situation.md").write_text(
        "Актуальная причина [HYP-001].\n"
        f"## {heading}\n"
        "### Краткий вывод до CHG-001\n"
        "Прежняя причина [HYP-001].\n"
        "Неизвестная ссылка [CLM-999].\n", encoding="utf-8")
    items = V.verify(proj, [], "CHG-001")["items"]
    assert [i["line"] for i in items if i["verdict"] == "stale?"] == [1]
    # Исключение касается актуализации по CHG; остальные проверки остаются.
    assert any(i["line"] == 5 and i["verdict"] == "wrong-ref" for i in items)

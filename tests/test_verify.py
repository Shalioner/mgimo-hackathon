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


def test_number_not_in_referenced_claim_needs_check(proj):
    assert verdicts(proj, "Синхронизация — 54% обращений [CLM-002].") == ["check"]


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

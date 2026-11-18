"""Тесты M07: проверка перестройки 05/06 после пересмотра решения."""
from __future__ import annotations

import json
import pathlib
import subprocess
import sys

from strategy_checks import rebuild

ROOT = pathlib.Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "tests" / "fixtures" / "rebuild"
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


def test_ryadom_broken_fails_duplicate_main_and_missing_sections() -> None:
    findings = rebuild.check(FIXTURES / "ryadom_broken")
    _assert_contract(findings)
    errors = [f for f in findings if f["status"] == "error"]
    codes = {f["code"] for f in errors}
    files = {f["file"] for f in errors}
    assert "rebuild.duplicate_main" in codes
    assert "rebuild.missing_sections" in codes
    assert "outputs/05_product_strategy.md" in files
    assert "outputs/06_comms_strategy.md" in files


def test_ryadom_fixed_passes() -> None:
    findings = rebuild.check(FIXTURES / "ryadom_fixed")
    _assert_contract(findings)
    assert all(f["status"] == "ok" for f in findings)
    assert {f["file"] for f in findings} == {
        "outputs/05_product_strategy.md",
        "outputs/06_comms_strategy.md",
    }


def test_legacy_esim_passes_and_missing_is_not_assessed(tmp_path: pathlib.Path) -> None:
    esim_findings = rebuild.check(ROOT / "projects" / "esim-travel")
    _assert_contract(esim_findings)
    assert all(f["status"] == "ok" for f in esim_findings)

    missing_findings = rebuild.check(tmp_path)
    _assert_contract(missing_findings)
    assert all(f["status"] == "not_assessed" for f in missing_findings)


def test_rebuild_cli_json_and_exit_codes(tmp_path: pathlib.Path) -> None:
    bad = subprocess.run(
        [sys.executable, str(ROOT / "tools" / "strategy_checks" / "rebuild.py"), str(FIXTURES / "ryadom_broken"), "--json"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert bad.returncode == 1
    bad_data = json.loads(bad.stdout)
    _assert_contract(bad_data)
    assert any(item["status"] == "error" for item in bad_data)

    good = subprocess.run(
        [sys.executable, str(ROOT / "tools" / "strategy_checks" / "rebuild.py"), str(FIXTURES / "ryadom_fixed"), "--json"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert good.returncode == 0
    good_data = json.loads(good.stdout)
    _assert_contract(good_data)
    assert all(item["status"] == "ok" for item in good_data)

    na = subprocess.run(
        [sys.executable, str(ROOT / "tools" / "strategy_checks" / "rebuild.py"), str(tmp_path), "--json"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert na.returncode == 0
    na_data = json.loads(na.stdout)
    assert all(item["status"] == "not_assessed" for item in na_data)


def test_rebuild_historical_main_demotion_and_case_insensitive_header(tmp_path: pathlib.Path) -> None:
    proj = tmp_path / "case"
    (proj / "outputs").mkdir(parents=True)

    # Undemoted '# главное' inside historical snapshot must fail as duplicate_main
    (proj / "outputs" / "05_product_strategy.md").write_text(
        "# Продуктовая стратегия\n\n"
        "# главное\nТекст.\n\n"
        "## Выбранное направление\nОк.\n\n"
        "## Осознанные отказы\nОк.\n\n"
        "## Исторический снимок до CHG-001\n\n"
        "## главное\nСтарая версия.\n",
        encoding="utf-8",
    )
    (proj / "outputs" / "06_comms_strategy.md").write_text(
        "# Коммуникационная стратегия\n\n"
        "## Главное\nТекст.\n\n"
        "## Аудитория и ситуация\nОк.\n\n"
        "## Барьер понимания или выбора\nОк.\n\n"
        "## Ключевое сообщение и его подтверждение\nОк.\n\n"
        "## Роль каналов\nОк.\n\n"
        "## Чего мы не обещаем\nОк.\n\n"
        "## Исторический снимок до CHG-001\n\n"
        "### Главное (до CHG-001)\nСтарая версия.\n",
        encoding="utf-8",
    )
    findings = rebuild.check(proj)
    _assert_contract(findings)
    err_05 = [f for f in findings if f["file"] == "outputs/05_product_strategy.md" and f["status"] == "error"]
    ok_06 = [f for f in findings if f["file"] == "outputs/06_comms_strategy.md" and f["status"] == "ok"]
    assert any(f["code"] == "rebuild.duplicate_main" for f in err_05)
    assert ok_06

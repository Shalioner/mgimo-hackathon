from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import run_report_check as RRC  # noqa: E402


def _make_project(
    base: Path,
    *,
    report_09: str,
    cost_report: str | None = None,
    progress: str | None = None,
    extra_outputs: dict[str, str] | None = None,
) -> Path:
    proj = base / "proj"
    (proj / "outputs").mkdir(parents=True, exist_ok=True)
    (proj / "state").mkdir(parents=True, exist_ok=True)
    (proj / "outputs" / "09_run_report.md").write_text(report_09, encoding="utf-8")
    if cost_report is not None:
        (proj / "state" / "cost_report.md").write_text(cost_report, encoding="utf-8")
    if progress is not None:
        (proj / "state" / "progress.md").write_text(progress, encoding="utf-8")
    for fname, body in (extra_outputs or {}).items():
        (proj / "outputs" / fname).write_text(body, encoding="utf-8")
    return proj


def _cli(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(ROOT / "tools" / "run_report_check.py"), *args],
        cwd=str(cwd or ROOT),
        capture_output=True,
        text=True,
    )


def test_repo_all_projects_and_docs_exit_zero():
    proc = _cli("--json", cwd=ROOT)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    data = json.loads(proc.stdout)
    assert data["counts"].get("error", 0) == 0
    for item in data["findings"]:
        assert set(item) >= {"code", "status", "message", "file", "line", "ids"}
        assert item["status"] in {"ok", "warn", "error", "not_assessed"}


def test_example_project_is_not_assessed_exit_zero():
    proc = _cli(str(ROOT / "projects" / "example"), "--json")
    assert proc.returncode == 0
    data = json.loads(proc.stdout)
    assert any(f["status"] == "not_assessed" for f in data["findings"])


def test_empty_usage_is_disclosed_without_an_invented_zero_total(tmp_path: Path):
    import cost

    report = cost.render(cost.summarize([], {}), "Стоимость: учебный прогон", None)
    assert "$0.0000" not in report
    project = _make_project(
        tmp_path,
        report_09="# Отчёт\n\nСреда: codex\n\nДенежный итог неизвестен.\n",
        cost_report="## Стоимость: прошлый прогон\n\n**Итого (API-эквивалент): $0.4507**\n\n" + report,
        progress="Среда: codex\n",
    )
    proc = _cli(str(project), "--json")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert {item["code"] for item in json.loads(proc.stdout)["findings"]} == {"cost_usage_unobserved"}

    report_09 = project / "outputs" / "09_run_report.md"
    report_09.write_text("# Отчёт\n\nСреда: codex\n\nИтого (API-эквивалент): $0.00\n", encoding="utf-8")
    proc = _cli(str(project), "--json")
    assert proc.returncode == 1
    assert "run_report_unobserved_cost_amount" in proc.stdout


def test_cost_mismatch_and_denial_phrase_fail_exit_one(tmp_path: Path):
    proj = _make_project(
        tmp_path,
        cost_report="## Стоимость: zcode (2026-11-10T17:30 — 2026-11-10T19:00)\n\n| Среда | Модель | Вызовы/потоки | Вход | Кэш | Выход | USD |\n|---|---|---|---|---|---|---|\n| zcode | glm | 10 | 100 | 0 | 50 | 0.4507 |\n\n**Итого (API-эквивалент): $0.4507**\n",
        progress="# Прогресс\nСреда: zcode\n",
        report_09="# Отчёт о прогоне\n| Среда | zcode |\n| Стоимость | $1.50; среда точную стоимость не показывала, стоимость не предоставлена |\n",
    )
    proc = _cli(str(proj), "--json")
    assert proc.returncode == 1
    codes = {f["code"] for f in json.loads(proc.stdout)["findings"] if f["status"] == "error"}
    assert "run_report_cost_mismatch" in codes
    assert "run_report_missing_cost_total" in codes
    assert "run_report_cost_denied" in codes


def test_malformed_cost_header_and_env_mismatch_exit_one(tmp_path: Path):
    proj = _make_project(
        tmp_path,
        cost_report="## Стоимость:  + ZCode\n\n| Среда | Модель | USD |\n|---|---|---|\n| zcode | glm | 0.4507 |\n\n**Итого: $0.4507**\n",
        progress="# Прогресс\nСреда: codex\n",
        report_09="# Отчёт о прогоне\n| Среда | claude-code |\n| Стоимость | $0.4507 |\n",
    )
    proc = _cli(str(proj), "--json")
    assert proc.returncode == 1
    codes = {f["code"] for f in json.loads(proc.stdout)["findings"] if f["status"] == "error"}
    assert "cost_report_malformed_header" in codes
    assert "run_report_env_mismatch" in codes
    assert "run_report_progress_env_mismatch" in codes


def test_erroneous_parenthetical_env_is_ignored_and_rounding_matches(tmp_path: Path):
    proj = _make_project(
        tmp_path,
        cost_report="## Стоимость: zcode (2026-11-10T17:30 — 2026-11-10T19:00)\n\n| Среда | Модель | USD |\n|---|---|---|\n| zcode | glm | 3.1803 |\n| zcode | luna | 0.0548 |\n\n**Итого: $3.2351**\n",
        progress="# Прогресс\nСреда: codex (аудит; исходный прогон — zcode)\n",
        report_09=(
            "# Отчёт о прогоне\n"
            "| Среда | zcode (в журнале ошибочно определялась как claude-code); аудит — codex (без модельных вызовов по API) |\n"
            "| Стоимость | **$3,24** (Sol $3.1803, Luna $0.0548) |\n"
            "## Ограничения\n- Рынок оценивается в $1,8–3,3 млрд.\n"
        ),
    )
    proc = _cli(str(proj), "--json")
    assert proc.returncode == 0, proc.stdout
    findings = json.loads(proc.stdout)["findings"]
    assert all(f["status"] == "ok" for f in findings)


def test_dup_main_in_09_fails_exit_one(tmp_path: Path):
    shared_main = (
        "Общий вывод проекта на 50 установок и 2 сегмента с фокусом на удержание новых семей в первый месяц использования.\n"
    )
    proj = _make_project(
        tmp_path,
        cost_report="## Стоимость: codex\n\n| Среда | Модель | USD |\n|---|---|---|\n| codex | sol | 3.2351 |\n\n**Итого: $3.2351**\n",
        progress="# Прогресс\nСреда: codex\n",
        report_09=f"# Главное\n{shared_main}\n# Отчёт о прогоне\nСреда: codex, итог $3.2351\n",
        extra_outputs={"05_product_strategy.md": f"# Главное\n{shared_main}\n## Стратегия\n"},
    )
    proc = _cli(str(proj), "--json")
    assert proc.returncode == 1
    codes = {f["code"] for f in json.loads(proc.stdout)["findings"] if f["status"] == "error"}
    assert "run_report_dup_main" in codes


def test_docs_source_mismatch_fails_exit_one(tmp_path: Path):
    (tmp_path / "projects" / "esim-travel" / "state").mkdir(parents=True)
    (tmp_path / "projects" / "esim-travel" / "outputs").mkdir(parents=True)
    (tmp_path / "projects" / "ryadom" / "state").mkdir(parents=True)
    (tmp_path / "projects" / "ryadom" / "outputs").mkdir(parents=True)
    (tmp_path / "docs" / "night" / "reports").mkdir(parents=True)
    (tmp_path / "evals" / "baseline").mkdir(parents=True)

    (tmp_path / "projects" / "esim-travel" / "state" / "cost_report.md").write_text(
        "## Стоимость: ZCode\n| Среда | Модель | USD |\n|---|---|---|\n| zcode | glm | 0.4507 |\n\n**Итого: $0.4507**\n",
        encoding="utf-8",
    )
    (tmp_path / "projects" / "esim-travel" / "state" / "progress.md").write_text("Среда: zcode\n", encoding="utf-8")
    (tmp_path / "projects" / "esim-travel" / "outputs" / "09_run_report.md").write_text(
        "# Отчёт\nСреда: zcode, итог $0.4507\n", encoding="utf-8"
    )
    (tmp_path / "projects" / "ryadom" / "state" / "cost_report.md").write_text(
        "## Стоимость: codex\n| Среда | Модель | USD |\n|---|---|---|\n| codex | sol | 3.2351 |\n\n**Итого: $3.2351**\n",
        encoding="utf-8",
    )
    (tmp_path / "projects" / "ryadom" / "state" / "progress.md").write_text("Среда: codex\n", encoding="utf-8")
    (tmp_path / "projects" / "ryadom" / "outputs" / "09_run_report.md").write_text(
        "# Отчёт\nСреда: codex, итог $3.2351\n", encoding="utf-8"
    )

    (tmp_path / "docs" / "E2E_RUN_1.md").write_text("# E2E 1\n", encoding="utf-8")
    (tmp_path / "docs" / "E2E_RUN_1_REVIEW.md").write_text("# E2E 1 review\n", encoding="utf-8")
    (tmp_path / "docs" / "E2E_RUN_2_ZCODE.md").write_text("# E2E 2\n", encoding="utf-8")
    (tmp_path / "docs" / "night" / "reports" / "T07.md").write_text("Claim support rate (код): 90.3%\n", encoding="utf-8")
    (tmp_path / "evals" / "baseline" / "RESULTS.md").write_text("11% / 50%\n", encoding="utf-8")

    (tmp_path / "README.md").write_text(
        "# Стратег\n"
        "- **$0.9900** ([расчёт](projects/esim-travel/state/cost_report.md))\n"
        "- **3 среды**: [Codex](docs/E2E_RUN_1.md) и [ZCode](docs/E2E_RUN_2_ZCODE.md)\n"
        "- **88,8%** ([свежий verify](docs/night/reports/T07.md)); у чата — [11–50%](evals/baseline/RESULTS.md)\n",
        encoding="utf-8",
    )
    (tmp_path / "docs" / "DESCRIPTION.md").write_text(
        "## 5. Демонстрационный проект, время и расходы\n"
        "| | «Рядом» | «eSIM» |\n"
        "|---|---|---|\n"
        "| Стоимость (API-эквивалент) | **$9,99** | **$0,45** |\n",
        encoding="utf-8",
    )

    proc = _cli("--json", cwd=tmp_path)
    assert proc.returncode == 1
    findings = json.loads(proc.stdout)["findings"]
    err_files = {(f["file"], f["code"]) for f in findings if f["status"] == "error"}
    assert ("README.md", "docs_source_number_mismatch") in err_files
    assert ("docs/DESCRIPTION.md", "docs_source_number_mismatch") in err_files
    assert len([f for f in findings if f["status"] == "error"]) >= 4


def test_missing_cost_report_unknown_env_and_scale_next_to_cost_fail(tmp_path: Path):
    import run_report_check as RRC

    # 1. Есть 09_run_report.md с неизвестной средой и $TBD, но отсутствует cost_report.md
    p_nocost = _make_project(
        tmp_path / "no_cost",
        progress="Среда: codex\n",
        report_09="# Отчёт\n| Среда | неизвестна |\n| Стоимость | $TBD; стоимость не предоставлена |\n",
    )
    items_nc = RRC.check(p_nocost)
    codes_nc = {f["code"] for f in items_nc if f["status"] == "error"}
    assert {"cost_report_missing", "run_report_cost_denied", "run_report_env_missing"} <= codes_nc
    assert _cli(str(p_nocost)).returncode == 1

    # 2. Несовместимые среды (09 выходит за allowed_envs) и $9.99 в одной строке с млн кэш-токенов
    p_bad = _make_project(
        tmp_path / "bad_env",
        cost_report="## Стоимость: ZCode\n| Среда | Модель | USD |\n|---|---|---|\n| zcode | glm | 0.4507 |\n**Итого: $0.4507**\n",
        report_09="# Отчёт\n| Среда | claude-code, cursor, zcode |\n| Стоимость | $9.99 (12,3 млн кэш-токенов); учтено в $0.4507 |\n",
    )
    items_bad = RRC.check(p_bad)
    codes_bad = {f["code"] for f in items_bad if f["status"] == "error"}
    assert {"run_report_cost_mismatch", "run_report_env_mismatch"} <= codes_bad
    assert _cli(str(p_bad)).returncode == 1

    # 3. Незахардкоженная среда openrouter/gigacode не теряется в cost_report.md
    p_unk = _make_project(
        tmp_path / "unk_env",
        cost_report="## Стоимость: OpenRouter\n| Среда | Модель | USD |\n|---|---|---|\n| openrouter | gpt | 0.4507 |\n**Итого: $0.4507**\n",
        report_09="# Отчёт\n| Среда | claude-code |\n| Стоимость | $0.4507 |\n",
    )
    codes_unk = {f["code"] for f in RRC.check(p_unk) if f["status"] == "error"}
    assert "run_report_env_mismatch" in codes_unk

    p_or_ok = _make_project(
        tmp_path / "or_ok",
        cost_report="## Стоимость: OpenRouter\n| Среда | Модель | USD |\n|---|---|---|\n| openrouter | gpt | 0.4507 |\n**Итого: $0.4507**\n",
        progress="Среда: openrouter\n",
        report_09="# Отчёт\n| Среда | openrouter |\n| Стоимость | $0.4507 (12,3 млн кэш-токенов) |\n",
    )
    assert all(f["status"] == "ok" for f in RRC.check(p_or_ok))


def test_docs_missing_or_unparseable_cost_report_and_invalid_cli_path(tmp_path: Path):
    import run_report_check as RRC

    (tmp_path / "projects" / "esim-travel" / "outputs").mkdir(parents=True)
    (tmp_path / "README.md").write_text(
        "# Стратег\n- **$0.4507** ([расчёт](projects/esim-travel/state/cost_report.md))\n",
        encoding="utf-8",
    )
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "DESCRIPTION.md").write_text(
        "## 5. Расходы\n| | «eSIM» |\n|---|---|\n| Стоимость (API) | **$0,45** |\n",
        encoding="utf-8",
    )
    codes_missing = {f["code"] for f in RRC.check_docs(tmp_path) if f["status"] == "error"}
    assert "docs_missing_cost_source" in codes_missing

    (tmp_path / "projects" / "esim-travel" / "state").mkdir(parents=True)
    (tmp_path / "projects" / "esim-travel" / "state" / "cost_report.md").write_text(
        "| Среда | Модель | USD |\n|---|---|---|\n| zcode | glm | — |\n",
        encoding="utf-8",
    )
    codes_unparse = {f["code"] for f in RRC.check_docs(tmp_path) if f["status"] == "error"}
    assert "docs_unparseable_cost_source" in codes_unparse

    # CLI exit 2 на несуществующий путь и наличие check()
    assert hasattr(RRC, "check")
    assert RRC.main([str(tmp_path / "does_not_exist_xyz"), "--json"]) == 2


def test_cost_progress_not_unioned_zero_api_aider_and_tbd_docs_f2(tmp_path: Path):
    # 1. progress.md не маскирует лишнюю среду в 09_run_report.md, отсутствующую в cost_report.md
    p_hide = _make_project(
        tmp_path / "hide",
        cost_report="## Стоимость: ZCode\n| Среда | Модель | USD |\n|---|---|---|\n| zcode | glm | 0.4507 |\n**Итого: $0.4507**\n",
        progress="Среда: claude-code, zcode\n",
        report_09="# Отчёт\n| Среда | Прогон выполнен в claude-code и zcode |\n| Стоимость | $0.4507 |\n",
    )
    codes_hide = {f["code"] for f in RRC.check(p_hide) if f["status"] == "error"}
    assert "run_report_env_mismatch" in codes_hide

    # 2. Неизвестная среда aider и смешанный список zcode, aider не теряются
    p_aider_ok = _make_project(
        tmp_path / "aider_ok",
        cost_report="## Стоимость: aider\n| Среда | Модель | USD |\n|---|---|---|\n| aider | gpt | 0.4507 |\n**Итого: $0.4507**\n",
        progress="Среда: aider\n",
        report_09="# Отчёт\n| Среда | aider |\n| Стоимость | $0.4507 |\n",
    )
    assert all(f["status"] == "ok" for f in RRC.check(p_aider_ok))

    p_aider_extra = _make_project(
        tmp_path / "aider_extra",
        cost_report="## Стоимость: ZCode\n| Среда | Модель | USD |\n|---|---|---|\n| zcode | glm | 0.4507 |\n**Итого: $0.4507**\n",
        report_09="# Отчёт\n| Среда | zcode, aider |\n| Стоимость | $0.4507 |\n",
    )
    assert any(f["code"] == "run_report_env_mismatch" and f["status"] == "error" for f in RRC.check(p_aider_extra))

    p_mixed_ok = _make_project(
        tmp_path / "mixed_ok",
        cost_report="## Стоимость: ZCode + aider\n| Среда | Модель | USD |\n|---|---|---|\n| zcode | glm | 0.2500 |\n| aider | gpt | 0.2007 |\n**Итого: $0.4507**\n",
        progress="Среда: zcode, aider\n",
        report_09="# Отчёт\n| Среда | zcode, aider; аудит — codex (без модельных вызовов по API) |\n| Стоимость | $0.4507 |\n",
    )
    assert all(f["status"] == "ok" for f in RRC.check(p_mixed_ok))

    # 3. $TBD в README (со ссылкой на cost_report) и в DESCRIPTION §5 ловится без USD_RE
    d_tbd = tmp_path / "docs_tbd"
    (d_tbd / "projects" / "esim-travel" / "state").mkdir(parents=True)
    (d_tbd / "docs").mkdir()
    (d_tbd / "README.md").write_text(
        "# Стратег\n- **Стоимость: $TBD** ([расчёт](projects/esim-travel/state/cost_report.md))\n",
        encoding="utf-8",
    )
    (d_tbd / "docs" / "DESCRIPTION.md").write_text(
        "## 5. Расходы\n| | «eSIM» |\n|---|---|\n| Стоимость (API) | **$TBD** |\n",
        encoding="utf-8",
    )
    doc_codes = {f["code"] for f in RRC.check_docs(d_tbd) if f["status"] == "error"}
    assert "docs_missing_cost_source" in doc_codes

    (d_tbd / "projects" / "esim-travel" / "state" / "cost_report.md").write_text(
        "## Стоимость: ZCode\n| Среда | Модель | USD |\n|---|---|---|\n| zcode | glm | 0.4507 |\n**Итого: $0.4507**\n",
        encoding="utf-8",
    )
    doc_codes2 = {f["code"] for f in RRC.check_docs(d_tbd) if f["status"] == "error"}
    assert "docs_source_number_mismatch" in doc_codes2
    assert any(f["file"] == "README.md" and f["code"] == "docs_source_number_mismatch" for f in RRC.check_docs(d_tbd))
    assert any(f["file"] == "docs/DESCRIPTION.md" and f["code"] == "docs_source_number_mismatch" for f in RRC.check_docs(d_tbd))

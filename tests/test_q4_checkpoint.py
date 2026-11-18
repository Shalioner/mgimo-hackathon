from __future__ import annotations

from pathlib import Path

import pytest

from test_report import ROOT, Tags, current_screen, report


@pytest.fixture
def case(tmp_path):
    (tmp_path / "outputs").mkdir()
    (tmp_path / "state/ledger").mkdir(parents=True)
    return tmp_path


def screen_for(project: Path, progress: str | None) -> str:
    if progress is not None:
        (project / "state/progress.md").write_text(progress, encoding="utf-8")
    assert report.main([str(project)]) == 0
    return current_screen((project / "outputs/report.html").read_text(encoding="utf-8"))


def checkpoint(screen: str) -> str:
    return screen.split('<div id="checkpoint-approval">', 1)[1].split('</div>', 1)[0]


@pytest.mark.parametrize(("step", "gate", "name"), [
    ("1 — бриф ожидает подтверждения C1", "C1", "01_brief.md"),
    ("5 — варианты ждут выбора на C2", "C2", "04_options.md"),
    ("7 — первый пакет ожидает утверждения C3", "C3", "07_validation_plan.md"),
    ("8 — сопровождение; повторное C3 открыто", "C3", "07_validation_plan.md"),
])
def test_open_checkpoint_uses_current_questions_and_existing_stage_material(case, step, gate, name):
    (case / "outputs" / name).write_text("# Материал\n", encoding="utf-8")
    screen = screen_for(case, f"""# Прогресс
Текущий шаг: {step}

## Шаги
- [x] C3 закрыт

## Открытые вопросы к человеку
- **Роль:** Владелец задачи
- **Ожидаемый ответ:** Согласовать условия или вернуть правки.

## История
Текущий шаг: C3 закрыт
### Открытые вопросы к человеку
- Роль: Прежний согласующий
- Ожидаемый ответ: Утвердить старый пакет.
""")
    block = checkpoint(screen)
    assert screen.index("Текущий шаг:") < screen.index("Согласование") < screen.index("Выбранный вариант")
    assert f"Согласование {gate}" in block
    assert "Владелец задачи" in block and "Согласовать условия или вернуть правки." in block
    assert "Прежний согласующий" not in screen and "старый пакет" not in screen
    assert f'href="#doc-{name[:2]}"' in block


@pytest.mark.parametrize("progress", [
    pytest.param("Текущий шаг: 8 — C3 закрыт\n", id="closed"),
    pytest.param(None, id="no-file"),
    pytest.param("## История\nТекущий шаг: C3 открыто\n", id="history"),
    pytest.param("```md\nТекущий шаг: C3 открыто\n```\n", id="code-example"),
    pytest.param("Текущий шаг: 7 — C3\n", id="missing-status"),
    pytest.param("Текущий шаг: 7 — C3 открыто?\n", id="question"),
    pytest.param("Текущий шаг: 7 — если C3 открыто\n", id="conditional"),
    pytest.param("Текущий шаг: 7 — C3 открыто, если владелец согласится\n", id="conditional-suffix"),
    pytest.param("Текущий шаг: 7 — C3 не открыто\n", id="negated"),
    pytest.param("Текущий шаг: 7 — C4 открыто\n", id="unknown-gate"),
    pytest.param("Текущий шаг: C2 открыто; C3 открыто\n", id="multiple-gates"),
    pytest.param("Текущий шаг: C2 открыто; C3 сейчас открыто\n", id="two-open-variants"),
    pytest.param("Текущий шаг: C3 открыто, но это неверно\n", id="contradiction"),
    pytest.param("Текущий шаг: C3 открыто, когда готов пакет\n", id="when-ready"),
    pytest.param("Текущий шаг: В истории записано, C3 открыто\n", id="historical-clause"),
    pytest.param("# История\nТекущий шаг: C3 открыто\n", id="historical-h1"),
    pytest.param("Текущий шаг: C3 открыто; C3 закрыт\n", id="conflicting-status"),
    pytest.param("Текущий шаг: C3 открыто\nТекущий шаг: C3 закрыт\n", id="multiple-steps"),
])
def test_no_checkpoint_from_unclear_or_inactive_step(case, progress):
    (case / "state/ledger/decisions.yaml").write_text(
        "- id: DEC-001\n  state: proposed\n  recommended: ALT-001\n", encoding="utf-8")
    (case / "state/ledger/changes.yaml").write_text(
        "- id: CHG-001\n  status: open\n  summary: Требуется повторное C3\n", encoding="utf-8")
    if progress is not None:
        progress = "# Прогресс\n" + progress + (
            "\n## Шаги\n- [ ] C3 открыто\n"
            "\n## Открытые вопросы к человеку\n- Руководитель: Утвердить пакет.\n"
        )
    assert 'id="checkpoint-approval"' not in screen_for(case, progress)


def test_open_checkpoint_discloses_missing_current_fields(case):
    screen = screen_for(case, """# Прогресс
Текущий шаг: Требуется повторное C3

## Открытые вопросы к человеку
- C2 требуется согласовать с руководителем.
### История
- Владелец: Утвердить прежний пакет.
""")
    block = checkpoint(screen)
    assert "Роль не указана" in block
    assert "Не указан в актуальных вопросах" in block
    assert "прежний пакет" not in block
    assert "Материалы C3" not in block


def test_checkpoint_links_only_present_report_sections(case):
    for name in ("04_options.md", "06_comms_strategy.md", "08_decision_log.md"):
        (case / "outputs" / name).write_text("# Материал\n", encoding="utf-8")
    screen = screen_for(case, """# Прогресс: [[описание](https://example.org/intro)](https://example.org/outer)
Текущий шаг: C3 открыто

## Открытые вопросы к человеку
- Руководитель: Уточнить [свойства](https://example.org/04_options.md) <точно> (06).
- Материалы: outputs/06_comms_strategy.md, outputs/04_missing.md, outputs/07_validation_plan.md, outputs/08_decision_log.md.
""")
    block = checkpoint(screen)
    assert "example.org" not in block
    assert "Уточнить свойства &lt;точно&gt;" in block
    assert [attrs["href"] for tag, attrs in Tags(block).tags if tag == "a"] == ["#doc-06"]


def test_esim_reopened_c3_retains_selected_alternative(tmp_path):
    project = ROOT / "projects/esim-travel"
    out = tmp_path / "report.html"
    assert report.main([str(project), "--out", str(out)]) == 0
    screen = current_screen(out.read_text(encoding="utf-8"))
    block = checkpoint(screen)
    assert "Согласование C3" in block
    assert "Роль не указана" in block
    assert "Уточнить приоритет stop/go" in block
    assert "Согласовать проверки свойств" in block
    assert 'href="#doc-06"' in block and 'href="#doc-07"' in block
    assert "CHG-001" not in block
    assert '<strong>Выбранный вариант:</strong>' in screen
    assert 'href="#ref-ALT-001"' in screen.split('Выбранный вариант:', 1)[1]


def test_checkpoint_uses_gate_fields_without_removing_references_to_other_gates(case):
    screen = screen_for(case, """# Прогресс
Текущий шаг: C3 открыто

## Открытые вопросы к человеку
### C2 (открыт)
- Роль: Ответственный за C2
- Ожидаемый ответ: Выбрать вариант.
### C3 (открыт)
- C3 — Роль: Руководитель продукта
- C3 — Ожидаемый ответ: Утвердить уточнения C3 со ссылкой на решение C2.
- Запустить EXP-001?

    Роль: Устаревший согласующий
    Ожидаемый ответ: Запустить EXP-001.
""")
    block = checkpoint(screen)
    assert "Руководитель продукта" in block
    assert "Утвердить уточнения C3 со ссылкой на решение C2" in block
    assert "Ответственный за C2" not in block
    assert "Устаревший согласующий" not in block
    assert "EXP-001" not in block


def test_report_keeps_existing_title_and_status_links(case):
    screen = screen_for(case, """# Прогресс: [Краткий бриф](./01_brief.md)
Текущий шаг: C3 закрыт, [план проверки](./07_validation_plan.md)
""")
    assert 'id="checkpoint-approval"' not in screen
    assert 'href="./07_validation_plan.md"' in screen
    html = (case / "outputs/report.html").read_text(encoding="utf-8")
    assert 'href="./01_brief.md"' in html.split("</header>", 1)[0]


def test_explicit_current_step_below_document_title(case):
    screen = screen_for(case, "\n# Кейс\nТекущий шаг: C3 открыто.\n")
    assert "Согласование C3" in checkpoint(screen)


def test_checkpoint_ignores_gate_example_nested_under_history(case):
    screen = screen_for(case, """# Прогресс
Текущий шаг: C3 открыто
## Открытые вопросы к человеку
### История
#### C3 (открыт)
- Ожидаемый ответ: Утвердить прошлый пакет.
""")
    assert "прошлый пакет" not in checkpoint(screen)

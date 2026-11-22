"""Сценарий B6: понятное человеку решение без необходимости знать ID (положительные и отрицательные контроли)."""
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
import re
import shutil
import subprocess
import sys

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import report


class Tags(HTMLParser):
    def __init__(self, html: str):
        super().__init__()
        self.tags = []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))


def current_screen(html: str) -> str:
    return html.split('<section id="current-state">', 1)[1].split("</section>", 1)[0]


def checkpoint_html(screen: str) -> str:
    assert 'id="checkpoint-approval"' in screen
    return screen.split('<div id="checkpoint-approval">', 1)[1].split("</div>", 1)[0]


def plain_text(fragment: str) -> str:
    return unescape(re.sub(r"<[^>]*>", "", fragment))


def run_cli_report(project_dir: Path, out: Path) -> str:
    proc = subprocess.run(
        [sys.executable, str(ROOT / "tools/report.py"), str(project_dir), "--out", str(out)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, f"CLI report.py failed ({proc.returncode}): {proc.stderr}"
    return out.read_text(encoding="utf-8")


@pytest.fixture
def example_copy(tmp_path: Path) -> Path:
    dest = tmp_path / "example"
    shutil.copytree(ROOT / "projects/example", dest)
    (dest / "outputs").mkdir(exist_ok=True)
    (dest / "outputs/01_brief.md").write_text("# Бриф\n\n## Главное\nКраткий бриф.\n", encoding="utf-8")
    (dest / "outputs/04_options.md").write_text("# Варианты\n\n## Главное\nСравнение вариантов.\n", encoding="utf-8")
    (dest / "outputs/07_validation_plan.md").write_text("# План проверки\n\n## Главное\nПроверка гипотез.\n", encoding="utf-8")
    return dest


# --- Положительные контроли на временных копиях реальных кейсов (CLI) ---


def test_esim_travel_cli_shows_open_checkpoint_and_all_alternatives_without_id_knowledge(tmp_path: Path):
    case = tmp_path / "esim-travel"
    shutil.copytree(ROOT / "projects/esim-travel", case)
    html = run_cli_report(case, tmp_path / "esim.html")
    screen = current_screen(html)
    plain = plain_text(screen)

    # Открытый checkpoint C3 (Q4 интеграция)
    block = checkpoint_html(screen)
    assert "Согласование C3" in block
    assert "Роль не указана" in block
    assert "Уточнить приоритет stop/go" in block
    assert "Согласовать проверки свойств" in block
    assert 'href="#doc-06"' in block and 'href="#doc-07"' in block
    assert "CHG-001" not in block

    # Название выбранной альтернативы идёт до дополнительного ID и виден автор решения
    chosen_line = screen.split("<strong>Выбранный вариант:</strong>", 1)[1].split("</p>", 1)[0]
    assert chosen_line.index("Свой travel eSIM МегаФона") < chosen_line.index("ALT-001")
    assert "руководитель продукта" in plain

    # Видны вопрос решения и названия всех рассмотренных альтернатив с причинами отказа
    assert "Запускать ли МегаФону travel eSIM для россиян за рубежом" in plain
    assert "Партнёрство/дистрибуция агрегатора в приложении МегаФона" in plain
    assert "Не запускать — оставить как есть" in plain
    assert "Осознанный отказ от сегмента travel-интернета" in plain
    assert "Усиливает агрегаторов стратегически" in plain


def test_ryadom_cli_expands_ids_in_rationale_and_lists_all_alternatives(tmp_path: Path):
    case = tmp_path / "ryadom"
    shutil.copytree(ROOT / "projects/ryadom", case)
    html = run_cli_report(case, tmp_path / "ryadom.html")
    screen = current_screen(html)
    plain = plain_text(screen)

    # C3 закрыт -> ложного открытого checkpoint нет
    assert 'id="checkpoint-approval"' not in screen

    # В «Основании» и блоке вариантов названия альтернатив/событий понятны без знания ID
    rationale_part = plain.split("Основание (из решения):", 1)[1].split("\n\n", 1)[0]
    assert "Октябрьская аналитика изменила приоритет" in rationale_part
    assert "Диагностика и улучшение первой ценности" in rationale_part
    assert "Позиционирование вокруг понятного семейного результата" in rationale_part

    # Все 6 альтернатив видны по названиям на первом экране
    for title in (
        "Диагностика ценности платной подписки и перехода после trial",
        "Диагностика и улучшение первой ценности",
        "Расширение функций",
        "Позиционирование вокруг понятного семейного результата",
        "Сохранить текущий курс",
        "Заморозить развитие направления",
    ):
        assert title in plain

    # Заголовки в приложении «Реестр» тоже содержат понятные названия, а не только голые ID
    registry = html.split('<section id="registry">', 1)[1]
    assert "Диагностика ценности платной подписки и перехода после trial" in registry.split("</article>", 10)[1] or \
        "Переписать" in registry or "Диагностика ценности платной подписки" in re.search(r"<h3[^>]*>[^<]*ALT-006[^<]*</h3>", registry).group(0)


def test_rehearsal_cli_distinguishes_human_choice_from_agent_recommendation(tmp_path: Path):
    case = tmp_path / "rehearsal"
    shutil.copytree(ROOT / "projects/rehearsal-mobile-spend", case)
    html = run_cli_report(case, tmp_path / "rehearsal.html")
    screen = current_screen(html)
    plain = plain_text(screen)

    assert 'id="checkpoint-approval"' not in screen
    assert "Прототип предупреждений ответственному за связь" in plain
    assert "Прототип отчёта о потреблении" in plain
    assert "Сохранить текущий подход" in plain
    assert "Заморозить работу над отдельной функцией" in plain
    assert "рекомендован агентом" in plain


def test_open_c2_pending_decision_shows_alternatives_without_false_chosen(example_copy: Path, tmp_path: Path):
    (example_copy / "state/progress.md").write_text(
        """# Прогресс: Выбор направления
Текущий шаг: 5 — варианты ждут выбора на C2

## Открытые вопросы к человеку
- Руководитель продукта: Выбрать один из вариантов развития синхронизации.
- Материалы: outputs/04_options.md
""",
        encoding="utf-8",
    )
    (example_copy / "state/ledger/decisions.yaml").write_text(
        """- id: DEC-001
  question: Во что вложить следующий квартал, чтобы остановить падение удержания?
  options: [ALT-001, ALT-002, ALT-003, ALT-004]
  state: proposed
  recommended: ALT-001
  rationale: Синхронизация — самая частая жалоба, но эффект на удержание нужно проверить.
""",
        encoding="utf-8",
    )
    html = run_cli_report(example_copy, tmp_path / "c2.html")
    screen = current_screen(html)
    plain = plain_text(screen)

    # Открытый checkpoint C2
    block = checkpoint_html(screen)
    assert "Согласование C2" in block
    assert "Руководитель продукта" in block
    assert "Выбрать один из вариантов развития синхронизации" in block
    assert 'href="#doc-04"' in block

    # Никакого ложного выбранного варианта / согласия человека
    assert "Выбранный вариант неизвестен" in plain
    assert "<strong>Выбранный вариант:</strong>" not in screen
    assert "Рекомендация агента (не выбор человека)" in plain

    # Все альтернативы на выбор показаны по-человечески
    assert "Во что вложить следующий квартал, чтобы остановить падение удержания?" in plain
    for title in (
        "Переписать синхронизацию",
        "Сменить позиционирование на семейные общие блокноты",
        "Оставить как есть",
        "Заморозить развитие и перевести пользователей на партнёрский сервис",
    ):
        assert title in plain


# --- Отрицательные контроли: отсутствие ложных chosen, согласия человека и ложных checkpoint ---


@pytest.mark.parametrize(
    "decision_yaml",
    [
        pytest.param(
            "- id: DEC-001\n  question: Что выбрать?\n  options: [ALT-001, ALT-002, ALT-003]\n"
            "  state: proposed\n  recommended: ALT-001\n  chosen: ALT-002\n",
            id="proposed-with-stray-chosen",
        ),
        pytest.param(
            "- id: DEC-001\n  question: Что выбрать?\n  options: [ALT-001, ALT-002, ALT-003]\n"
            "  state: decided\n  recommended: ALT-001\n",
            id="decided-without-chosen",
        ),
        pytest.param(
            "- id: DEC-001\n  question: Что выбрать?\n  options: [ALT-001, ALT-002, ALT-003]\n"
            "  state: decided\n  chosen: ALT-001\n  status: superseded\n",
            id="superseded-decision",
        ),
        pytest.param(
            "- id: DEC-001\n  question: Что выбрать?\n  options: [ALT-001, ALT-002, ALT-003]\n"
            "  state: decided\n  chosen: ALT-001\n  status: retracted\n",
            id="retracted-decision",
        ),
    ],
)
def test_never_fabricates_human_choice_or_approval(example_copy: Path, tmp_path: Path, decision_yaml: str):
    (example_copy / "state/progress.md").write_text("# Прогресс\nТекущий шаг: 5 — C2 закрыт\n", encoding="utf-8")
    (example_copy / "state/ledger/decisions.yaml").write_text(decision_yaml, encoding="utf-8")
    html = run_cli_report(example_copy, tmp_path / "neg.html")
    screen = current_screen(html)
    assert "<strong>Выбранный вариант:</strong>" not in screen
    assert "Выбранный вариант неизвестен" in screen
    assert "утверждено человеком" not in screen.lower() or "не утверждено человеком" in screen.lower()


def test_needs_review_decision_does_not_claim_final_approval(example_copy: Path, tmp_path: Path):
    (example_copy / "state/progress.md").write_text(
        "# Прогресс\nТекущий шаг: 8 — сопровождение; требуется пересмотр\n", encoding="utf-8"
    )
    (example_copy / "state/ledger/decisions.yaml").write_text(
        """- id: DEC-001
  question: Во что вложить квартал?
  options: [ALT-001, ALT-002, ALT-003]
  state: needs_review
  chosen: ALT-001
  recommended: ALT-002
  rationale: После CHG-001 гипотеза ALT-001 ослаблена; требуется пересмотр.
""",
        encoding="utf-8",
    )
    html = run_cli_report(example_copy, tmp_path / "review.html")
    screen = current_screen(html)
    plain = plain_text(screen)

    assert "<strong>Выбранный вариант:</strong>" not in screen
    assert "Выбранный вариант неизвестен" in screen
    assert "требует пересмотра" in plain.lower()
    assert "Переписать синхронизацию" in plain
    assert "Сменить позиционирование на семейные общие блокноты" in plain


@pytest.mark.parametrize(
    "progress",
    [
        "Текущий шаг: 8 — C3 закрыт\n",
        "## История\nТекущий шаг: C3 открыто\n",
        "```md\nТекущий шаг: C3 открыто\n```\n",
        "Текущий шаг: 7 — C3 не открыто\n",
        "Текущий шаг: 7 — если C3 открыто\n",
        "Текущий шаг: C2 открыто; C3 открыто\n",
    ],
)
def test_no_false_open_checkpoint(example_copy: Path, tmp_path: Path, progress: str):
    (example_copy / "state/progress.md").write_text(
        "# Прогресс\n" + progress + "\n## Открытые вопросы к человеку\n- Руководитель: Утвердить пакет.\n",
        encoding="utf-8",
    )
    html = run_cli_report(example_copy, tmp_path / "nockpt.html")
    assert 'id="checkpoint-approval"' not in current_screen(html)


def test_escapes_html_and_unsafe_links_in_human_decision_fields(example_copy: Path, tmp_path: Path):
    (example_copy / "state/progress.md").write_text(
        "# Прогресс\nТекущий шаг: C2 открыто\n\n## Открытые вопросы к человеку\n- Руководитель <test>: Выбрать [вариант](javascript:alert(1)).\n",
        encoding="utf-8",
    )
    (example_copy / "state/ledger/alternatives.yaml").write_text(
        """- id: ALT-001
  title: "<script>alert(1)</script> Вариант А"
  kind: invest
  description: "Описание <b>жирное</b>"
  objections:
    - text: "Сильное возражение <риск>"
- id: ALT-002
  title: "Оставить как есть"
  kind: status_quo
  description: "Ничего не менять"
  objections:
    - text: "Удержание падает"
- id: ALT-003
  title: "Закрыть"
  kind: kill
  description: "Остановить"
  objections:
    - text: "Потеря сегмента"
""",
        encoding="utf-8",
    )
    (example_copy / "state/ledger/decisions.yaml").write_text(
        """- id: DEC-001
  question: "Что делать <срочно>?"
  options: [ALT-001, ALT-002, ALT-003]
  state: decided
  chosen: ALT-002
  rejected:
    - option: ALT-001
      reason: "Риск <высокий> и ссылка [клик](javascript:alert(1))"
""",
        encoding="utf-8",
    )
    html = run_cli_report(example_copy, tmp_path / "xss.html")
    assert "<script>alert(1)</script>" not in html
    assert 'href="javascript:' not in html.lower()
    assert not any(
        tag == "a" and attrs.get("href", "").lower().startswith("javascript:")
        for tag, attrs in Tags(html).tags
    )
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html
    assert "&lt;срочно&gt;" in html
    assert "&lt;высокий&gt;" in html


def test_checkpoint_gate_scoping_missing_fields_and_history_isolation(example_copy: Path, tmp_path: Path):
    (example_copy / "state/progress.md").write_text(
        """# Прогресс
Текущий шаг: C3 открыто

## Открытые вопросы к человеку
### C2 (открыт)
- Роль: Ответственный за C2
- Ожидаемый ответ: Выбрать вариант.
### История
#### C3 (открыт)
- Роль: Устаревший согласующий
- Ожидаемый ответ: Утвердить прошлый пакет.
""",
        encoding="utf-8",
    )
    html = run_cli_report(example_copy, tmp_path / "q4_missing.html")
    block = checkpoint_html(current_screen(html))
    assert "Согласование C3" in block
    assert "Роль не указана" in block
    assert "Не указан в актуальных вопросах" in block
    assert "Ответственный за C2" not in block
    assert "Устаревший согласующий" not in block
    assert "прошлый пакет" not in block
    assert 'href="#doc-07"' in block
    assert 'href="#doc-04"' not in block


def test_pending_decision_remains_visible_beside_another_decided_question(example_copy, tmp_path):
    path = example_copy / "state/ledger/decisions.yaml"
    decisions = yaml.safe_load(path.read_text(encoding="utf-8"))
    decisions.append({
        "id": "DEC-002", "question": "Как пересмотреть отдельный эксперимент?",
        "state": "needs_review", "options": ["ALT-001", "ALT-002"],
        "chosen": "ALT-001", "recommended": "ALT-002",
    })
    path.write_text(yaml.safe_dump(decisions, allow_unicode=True), encoding="utf-8")
    screen = current_screen(run_cli_report(example_copy, tmp_path / "mixed.html"))
    assert "<strong>Выбранный вариант:</strong>" in screen  # прежнее самостоятельное решение
    assert "Как пересмотреть отдельный эксперимент?" in plain_text(screen)
    assert "Прежний выбор (требует пересмотра, не подтверждён)" in plain_text(screen)


@pytest.mark.parametrize("checked, marker, visible", [(" ", "awaiting_human", True),
    ("x", "awaiting_human", False), (" ", "черновик", False)])
@pytest.mark.parametrize("steps_header", ["## Шаги\n", ""])
def test_numeric_first_step_checkpoint(example_copy, checked, marker, visible, steps_header):
    progress = (f"# Прогресс\nТекущий шаг: 1\n\n{steps_header}- [{checked}] 1. Постановка задачи → C1\n"
                f"\n## Что дальше\n- {marker}\n\n## Открытые вопросы к человеку\n"
                f"- {marker}\n- Роль: владелец\n- Ожидаемый ответ: подтвердите рамку\n")
    (example_copy / "state/progress.md").write_text(progress, encoding="utf-8")
    screen = current_screen(run_cli_report(example_copy, example_copy / "outputs/report.html"))
    assert ('id="checkpoint-approval"' in screen) == visible
    if visible:
        assert "подтвердите рамку" in checkpoint_html(screen)

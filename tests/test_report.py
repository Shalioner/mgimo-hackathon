"""Проверки офлайн-отчёта: содержимое, доказательства и безопасная разметка."""
from hashlib import sha256
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
import re
import shlex
import shutil
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import report


class Tags(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.tags = []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))


@pytest.fixture
def project(tmp_path):
    path = tmp_path / "example"
    shutil.copytree(ROOT / "projects/example", path)
    # Учебный baseline содержит реестр без outputs; документы создаём только в копии.
    (path / "outputs").mkdir(exist_ok=True)
    (path / "outputs/01_brief.md").write_text("# Бриф\n\n## Главное\nПроверить CLM-001 и SRC-001.\n", encoding="utf-8")
    return path


def render(project, tmp_path):
    out = tmp_path / "report.html"
    assert report.main([str(project), "--out", str(out)]) == 0
    return out.read_text(encoding="utf-8")


def test_example_generation_and_every_id(project, tmp_path):
    html = render(project, tmp_path)
    count = sum(len(report.ID.findall(p.read_text(encoding="utf-8"))) for p in (project / "outputs").glob("0*.md"))
    assert html.count('class="ref') >= count
    assert '<section id="doc-01">' in html
    assert 'id="registry"' in html
    tags = Tags(html).tags
    assert not any(tag == "script" and "src" in attrs for tag, attrs in tags)
    assert not any(tag == "link" and attrs.get("href", "").startswith(("http:", "https:")) for tag, attrs in tags)


def test_unknown_id_escaping_and_code(project, tmp_path):
    doc = project / "outputs/01_brief.md"
    doc.write_text(doc.read_text(encoding="utf-8") + "\n\n<b>тег</b> CLM-999\n\n```text\nCLM-998 <script>alert(1)</script>\n```\n", encoding="utf-8")
    html = render(project, tmp_path)
    assert '&lt;b&gt;тег&lt;/b&gt;' in html
    assert '<b>тег</b>' not in html
    refs = {a.get("data-id"): a for tag, a in Tags(html).tags if tag == "a" and "data-id" in a}
    for oid in ("CLM-999", "CLM-998"):
        assert refs[oid]["class"] == "ref missing"
        assert refs[oid]["title"] == "нет в реестре"
    assert '<script>alert' not in html


def test_gfm_table_and_inline():
    html = report.Renderer({}).markdown("# Заголовок\n\n| A | B |\n| :--- | ---: |\n| **жирный** | *курсив* |\n| `a|b` | текст \\| далее |\n\n- пункт\n\n1. один\n2. два\n\n[t](https://example.com)\n")
    tags = Tags(html).tags
    assert sum(t == "table" for t, _ in tags) == 1
    assert sum(t == "tr" for t, _ in tags) == 3
    assert sum(t == "td" for t, _ in tags) == 4
    for expected in ("<strong>жирный</strong>", "<em>курсив</em>", "<code>a|b</code>", "текст | далее", "<ul>", '<ol start="1">'):
        assert expected in html


def test_full_tooltips_and_obsolete():
    objects = {"CLM-001": {"id": "CLM-001", "type": "fact", "confidence": {"level": "high"},
                           "statement": "Сказано <точно>", "evidence": [{"quote": 'Длинная "цитата"', "source": "SRC-001"}],
                           "status": "superseded", "superseded_by": "CLM-002"}}
    html = report.Renderer(objects).markdown("**CLM-001** и `CLM-001` и [CLM-001](https://example.com)")
    links = [a for t, a in Tags(html).tags if t == "a" and a.get("data-id") == "CLM-001"]
    assert len(links) == 3
    for attrs in links:
        assert attrs["class"] == "ref obsolete"
        assert attrs["title"] == 'fact · high · Сказано <точно> · «Длинная "цитата"» (SRC-001) · superseded · superseded_by: CLM-002'
    assert '<точно>' not in html
    for field in ("message", "question", "expected_change", "summary"):
        assert report.tooltip({"id": "PRM-001", field: "Содержание"}) == "Содержание"


def test_main_blocks_headings_and_no_code_heading():
    md = "# Документ\n## Главное\nПервое\n```\n## Не заголовок\n```\n##### История\nСтарое\n## Главное\nВторое"
    assert report.main_blocks(md) == ["Первое\n```\n## Не заголовок\n```"]
    assert "Старое" in report.Renderer({}).markdown(md)
    assert report.main_blocks("# Нет резюме\nТекст") == []


def test_missing_project_and_ledger(tmp_path, capsys):
    assert report.main([str(tmp_path / "нет")]) == 2
    assert "нет папки" in capsys.readouterr().err
    (tmp_path / "outputs").mkdir()
    assert report.main([str(tmp_path)]) == 2


def test_default_output_title_cost_and_unsafe_links(project):
    (project / "state/cost_report.md").write_text("**Итого: $1.23**\n", encoding="utf-8")
    assert report.main([str(project)]) == 0
    html = (project / "outputs/report.html").read_text(encoding="utf-8")
    assert "Итого: $1.23" in unescape(re.sub('<[^>]*>', '', html))
    rendered = report.Renderer({}).markdown("[опасно](javascript:alert) <img src=x> ~~оставить~~")
    assert 'href="javascript:' not in rendered
    assert "&lt;img src=x&gt;" in rendered
    assert "~~оставить~~" in rendered


def test_strategy_table_rows_preserved():
    for slug in ("esim-travel", "ryadom"):
        md = (ROOT / "projects" / slug / "outputs/05_product_strategy.md").read_text(encoding="utf-8")
        rows = sum(1 for line in md.splitlines() if line.strip().startswith("|") and not report.table_separator(line))
        html = report.Renderer({}).markdown(md)
        assert sum(t == "tr" for t, _ in Tags(html).tags) == rows


def test_mcp_report_uses_project_boundary(tmp_path, monkeypatch):
    import mcp_server
    monkeypatch.setattr(mcp_server, "ROOT", tmp_path)
    project = tmp_path / "projects/demo"
    (project / "outputs").mkdir(parents=True)
    (project / "state/ledger").mkdir(parents=True)
    result = mcp_server.TOOLS["strategist_report"]["fn"]({"project": "demo"})
    assert "[код выхода 0]" in result
    assert (project / "outputs/report.html").is_file()
    with pytest.raises(ValueError):
        mcp_server.t_report({"project": "../../outside"})


def current_screen(html):
    return html.split('<section id="current-state">', 1)[1].split('</section>', 1)[0]


@pytest.mark.parametrize("step", [
    "8 — сопровождение; ALT-006 выбран. Следующий gate — условия EXP-001.",
    "8 — после аудита требуется согласование уточнений C3",
    "8 — пакет утверждён, подготовка учебных сценариев EXP-001",
])
def test_current_status_is_first_progress_not_historical_decision(project, tmp_path, step):
    (project / "state/progress.md").write_text(
        "# Прогресс: Пример\nТекущий шаг: " + step +
        "\n## История\nТекущий шаг: 8 — C3 закрыт, исторический пакет\n", encoding="utf-8")
    (project / "state/ledger/decisions.yaml").write_text("""
- id: DEC-001
  state: decided
  chosen: ALT-006
  recommended: ALT-001
  rationale: 'Записанное основание <проверить>. Вторая фраза. Исторически C3 закрыт.'
  status_note: 'C3 закрыт в прошлом'
- id: DEC-002
  state: decided
  chosen: ALT-001
  status: superseded
""", encoding="utf-8")
    for name in ("05_product_strategy.md", "06_comms_strategy.md"):
        (project / "outputs" / name).write_text(
            "# Стратегия\n## Главное\nТекущее резюме\n### Исторический снимок\nСтарое резюме\n## Главное\nВторое резюме", encoding="utf-8")
    html = render(project, tmp_path)
    screen = current_screen(html)
    plain = unescape(re.sub('<[^>]*>', '', screen))
    assert step in plain
    assert "исторический пакет" not in screen
    assert "C3 закрыт" not in screen
    assert "ALT-006" in plain and "ALT-001" not in plain
    assert "Записанное основание <проверить>. Вторая фраза." in plain
    assert "&lt;проверить&gt;" in screen
    assert 'href="#doc-05"' in screen and 'href="#doc-06"' in screen
    summaries = html.split('<section id="main-points">')[1].split('</section>')[0]
    assert "Текущее резюме" in summaries
    assert "Старое резюме" not in summaries and "Второе резюме" not in summaries
    assert "Старое резюме" in html and "Второе резюме" in html


def test_missing_status_does_not_infer_c3_from_decision(project, tmp_path):
    (project / "state/progress.md").write_text(
        "# Прогресс: Пример\n\n## История\nТекущий шаг: 8 — C3 закрыт\n",
        encoding="utf-8",
    )
    screen = current_screen(render(project, tmp_path))
    assert "текущий шаг не записан" in screen
    assert "C3" not in screen


def test_report_first_screen_links_and_next_action(project, tmp_path):
    progress = project / "state/progress.md"
    progress.write_text(
        "# Прогресс: Пример\nТекущий шаг: 8 — сопровождение\n\n"
        "## Что дальше\n1. Подтвердить доступ к данным EXP-001.\n2. Поздняя проверка.\n\n"
        "# Историческая копия\n## Что дальше\n- Старое действие.\n", encoding="utf-8")
    (project / "outputs/08_decision_log.md").write_text("# Журнал решения\n", encoding="utf-8")

    html = render(project, tmp_path)
    screen = current_screen(html)
    assert "Ближайшее действие" in screen and "Подтвердить доступ к данным" in screen
    assert "Поздняя проверка" not in screen and "Старое действие" not in screen
    assert 'href="#doc-08"' in screen and '<details id="cost-summary">' in screen
    assert '<section id="main-points"><details>' in html
    assert 'id="doc-08"' in html

    progress.write_text(progress.read_text(encoding="utf-8").replace(
        "## Что дальше\n1. Подтвердить доступ к данным EXP-001.\n2. Поздняя проверка.\n\n", ""), encoding="utf-8")
    assert "Ближайшее действие" not in current_screen(render(project, tmp_path))


@pytest.mark.parametrize("unknown", [False, True])
@pytest.mark.parametrize("reverse", [False, True])
def test_latest_cost_period_including_unknown(project, tmp_path, unknown, reverse):
    old = "## Стоимость: Старый (2026-11-10T14:00 — 2026-11-10T18:00)\n**Итого: $3.2351**"
    new = "## Стоимость: M27 (2026-11-17T21:46 — 2026-11-17T22:05)\n"
    new += ("Статус расчёта: нет записей токенов.\nДенежный итог неизвестен." if unknown
            else "**Итого (API-эквивалент): $1.2300**\nЦены неполные: модель без цены. Подписка — не цена API.")
    parts = [new, old] if reverse else [old, new]
    (project / "state/cost_report.md").write_text(
        "**Итого: $999**\n\n" + "\n\n".join(parts), encoding="utf-8")
    screen = current_screen(render(project, tmp_path))
    plain = unescape(re.sub('<[^>]*>', '', screen))
    assert "2026-11-17T21:46 — 2026-11-17T22:05" in plain
    assert "$999" not in plain and "$3.2351" not in plain
    if unknown:
        assert "итог неизвестен" in plain and "нет записей токенов" in plain
        assert "$0" not in plain
    else:
        assert "$1.2300" in plain and "модель без цены" in plain and "Подписка — не цена API" in plain


def test_cost_generation_date_and_full_details():
    value = """## Стоимость: новое формирование (2026-11-10 — 2026-11-11)
**Итого: $2**
_Сформировано 2026-11-17T12:00:00+03:00: cost.py_
## Стоимость: прежнее формирование (2026-11-12 — 2026-11-13)
**Итого: $1**
_Сформировано 2026-11-15T12:00:00+03:00: cost.py_
"""
    assert "$2" in report.latest_cost(value)
    assert "$1" not in report.latest_cost(value)


def test_no_cost_or_choice_is_explicit(project, tmp_path):
    (project / "state/ledger/decisions.yaml").write_text("[]\n", encoding="utf-8")
    screen = current_screen(render(project, tmp_path))
    assert "Выбранный вариант неизвестен" in screen
    assert "Денежный итог неизвестен" in screen


def test_document_starting_with_main_does_not_take_title_from_history(project, tmp_path):
    (project / "outputs/05_product_strategy.md").write_text(
        "# Главное\nТекущая стратегия\n## Историческая стратегия\nСтарый текст", encoding="utf-8")
    html = render(project, tmp_path)
    summary = html.split('<section id="main-points">')[1].split('</section>')[0]
    assert "Продуктовая стратегия" in summary
    assert "Историческая стратегия" not in summary
    assert "Историческая стратегия" in html


def check_without_writes(project, tmp_path, *args):
    def snapshot():
        return {
            path.relative_to(tmp_path): sha256(path.read_bytes()).hexdigest() if path.is_file() else None
            for path in tmp_path.rglob("*")
        }

    before = snapshot()
    status = report.main([str(project), "--check", *args])
    assert snapshot() == before
    return status


def test_check_detects_reopened_c3(project, tmp_path, capsys):
    progress = project / "state/progress.md"
    closed = "# Прогресс: Пример\n\nТекущий шаг: C3 закрыт, подготовка EXP-001\n"
    progress.write_text(closed, encoding="utf-8")
    assert report.main([str(project)]) == 0
    capsys.readouterr()
    assert check_without_writes(project, tmp_path) == 0
    assert "Отчёт актуален" in capsys.readouterr().out

    progress.write_text(closed.replace("C3 закрыт, подготовка EXP-001", "Требуется повторное C3"), encoding="utf-8")
    assert check_without_writes(project, tmp_path) == 1
    error = capsys.readouterr().err
    assert "Отчёт устарел" in error
    assert "Пересобрать: " in error


def test_check_custom_path_and_missing_report(project, tmp_path, capsys):
    out = tmp_path / "архив отчётов" / "report.html"
    assert check_without_writes(project, tmp_path, "--out", str(out)) == 1
    assert not out.parent.exists()
    error = capsys.readouterr().err
    assert "Отчёт отсутствует" in error
    rebuild = shlex.split(error.split("Пересобрать: ", 1)[1])
    assert Path(rebuild[1]) == ROOT / "tools/report.py"
    assert report.main(rebuild[2:]) == 0

    html = out.read_text(encoding="utf-8")
    assert 'href="../example/sources/retention_2026.csv"' in html
    assert check_without_writes(project, tmp_path, "--out", str(out)) == 0

    default_out = project / "outputs/report.html"
    shutil.copyfile(out, default_out)
    assert check_without_writes(project, tmp_path) == 1


@pytest.mark.parametrize("invalid_yaml", ["- id: [", "- id: CLM-001\n  evidence: null\n"])
def test_check_cannot_render(project, tmp_path, capsys, invalid_yaml):
    assert report.main([str(project)]) == 0
    capsys.readouterr()
    (project / "state/ledger/claims.yaml").write_text(invalid_yaml, encoding="utf-8")
    assert check_without_writes(project, tmp_path) == 2
    assert "Не удалось проверить отчёт" in capsys.readouterr().err


@pytest.mark.parametrize(("metadata", "extra", "status"), [
    ("2030-02-03T12:34:56+05:30", "", 0),
    ("2030-02-03T12:34:56+05:30 Дополнение", "", 1),
    ("2030-02-30T12:34:56+05:30", "", 1),
    ("2030-02-03T12:34:56+05:30", '\n<p class="meta">Сформировано: 2030-02-03T12:34:56+05:30</p>', 1),
])
def test_check_ignores_only_generated_timestamp(project, tmp_path, metadata, extra, status):
    assert report.main([str(project)]) == 0
    out = project / "outputs/report.html"
    html = out.read_text(encoding="utf-8")
    html, count = re.subn(r'(<p class="meta">Сформировано: )[^<]+</p>',
                          lambda match: match[1] + metadata + "</p>" + extra, html, count=1)
    assert count == 1
    out.write_text(html, encoding="utf-8")
    assert check_without_writes(project, tmp_path) == status

"""Проверки офлайн-отчёта: содержимое, доказательства и безопасная разметка."""
from hashlib import sha256
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
import re
import shlex
import shutil
import sys
from urllib.parse import urlsplit

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


def _hrefs_and_ids(html: str) -> tuple[list[str], set[str]]:
    tags = Tags(html).tags
    hrefs = [attrs["href"] for tag, attrs in tags if tag == "a" and "href" in attrs]
    ids = {attrs["id"] for _, attrs in tags if "id" in attrs}
    return hrefs, ids


def _relative_hrefs(hrefs: list[str]) -> list[str]:
    return [
        href for href in hrefs
        if not href.startswith("#")
        and not urlsplit(href).scheme
        and not urlsplit(href).netloc
        and not urlsplit(href).path.startswith("/")
    ]


def test_embedded_doc_links_redirect_to_anchors_and_preserve_fragments_queries_and_external(project, tmp_path):
    (project / "state/verification.md").write_text("# Сверка\n", encoding="utf-8")
    (project / "sources/mockup.md").write_text("# Макет\n", encoding="utf-8")
    (project / "state/progress.md").write_text(
        "# Прогресс: Кейс\nТекущий шаг: 7 — пакет ожидает утверждения на C3\n",
        encoding="utf-8",
    )
    (project / "outputs/00_brief_response.md").write_text(
        "# Ответ руководителю\n\n## Главное\n"
        "См. [Бриф](01_brief.md), [Стратегию](./05_product_strategy.md), "
        "[CLM-001](06_comms_strategy.md), [Отсутствующий](02_research_plan.md).\n\n"
        "С фрагментом и параметрами: [Критерии](07_validation_plan.md#критерии-остановки), "
        "[Сырой бриф](01_brief.md?mode=raw), [Секция](01_brief.md?mode=raw#sec).\n\n"
        "Материалы: [Сверка](../state/verification.md), "
        "[Сверка с якорем](../state/verification.md#L5), "
        "[Макет](../sources/mockup.md).\n\n"
        "Внешние и прочие: [Сайт](https://example.com/01_brief.md), "
        "[Почта](mailto:lead@example.com), [Якорь](#local-anchor), "
        "[Протокол](//example.com/01_brief.md), [Корень](/docs/01_brief.md), "
        "[XSS](javascript:alert(1)), [Data](data:text/html,hi).\n",
        encoding="utf-8",
    )
    (project / "outputs/05_product_strategy.md").write_text(
        "# Продуктовая стратегия\n\n## Главное\n"
        "См. [ответ руководителю](00_brief_response.md) и [макет](../sources/mockup.md).\n",
        encoding="utf-8",
    )
    (project / "outputs/06_comms_strategy.md").write_text(
        "# Коммуникационная стратегия\n\n## Главное\n"
        "Обещания сверены с [макетом](../sources/mockup.md).\n",
        encoding="utf-8",
    )
    (project / "outputs/07_validation_plan.md").write_text(
        "# План проверки\n\n## Главное\nПроверка по плану.\n",
        encoding="utf-8",
    )

    ext_out = tmp_path / "external_dir" / "report.html"
    default_out = project / "outputs/report.html"
    assert report.main([str(project)]) == 0
    assert report.main([str(project), "--check"]) == 0
    assert report.main([str(project), "--out", str(ext_out)]) == 0
    assert report.main([str(project), "--out", str(ext_out), "--check"]) == 0

    for out_path, is_external in ((default_out, False), (ext_out, True)):
        html = out_path.read_text(encoding="utf-8")
        hrefs, ids = _hrefs_and_ids(html)

        # Все встроенные документы без фрагмента/query ведут на реальные #doc-NN.
        for doc_id in ("doc-00", "doc-01", "doc-05", "doc-06"):
            assert doc_id in ids
            assert f"#{doc_id}" in hrefs
        for bare_md in ("00_brief_response.md", "01_brief.md", "./05_product_strategy.md", "06_comms_strategy.md"):
            assert bare_md not in hrefs

        # Несуществующий документ не получает фиктивный якорь #doc-02.
        assert "#doc-02" not in hrefs
        assert "02_research_plan.md" in hrefs

        # Содержательные #fragment, ?query и относительные материалы сохранены и пересчитаны от out.parent.
        prefix = "../example/outputs/" if is_external else ""
        state_prefix = "../example/state/" if is_external else "../state/"
        sources_prefix = "../example/sources/" if is_external else "../sources/"
        expected_rel = [
            f"{prefix}07_validation_plan.md#критерии-остановки",
            f"{prefix}01_brief.md?mode=raw",
            f"{prefix}01_brief.md?mode=raw#sec",
            f"{state_prefix}verification.md",
            f"{state_prefix}verification.md#L5",
            f"{sources_prefix}mockup.md",
        ]
        for expected in expected_rel:
            assert expected in hrefs
            assert (out_path.parent / urlsplit(expected).path).resolve().exists()

        # Внешние, якорные, protocol-relative и root-absolute URL сохранены; небезопасные заблокированы.
        for preserved in (
            "https://example.com/01_brief.md",
            "mailto:lead@example.com",
            "#local-anchor",
            "//example.com/01_brief.md",
            "/docs/01_brief.md",
        ):
            assert preserved in hrefs
        assert not any(h.lower().startswith(("javascript:", "data:")) for h in hrefs)

        # В сводке C3 на первом экране ссылки из «Главного» также перенаправлены и пересчитаны.
        c3_block = html.split('<div id="c3-executive-summary">', 1)[1].split("</div>", 1)[0]
        c3_hrefs, _ = _hrefs_and_ids(c3_block)
        assert "#doc-00" in c3_hrefs
        assert f"{sources_prefix}mockup.md" in c3_hrefs


def test_relative_links_respect_each_source_file_base_in_default_and_custom_out(project, tmp_path):
    (project / "state/verification.md").write_text("# Сверка\n", encoding="utf-8")
    (project / "state/run_log.md").write_text("# Лог\n", encoding="utf-8")
    (project / "outputs/00_brief_response.md").write_text("# Стратегия\n", encoding="utf-8")
    (project / "state/progress.md").write_text(
        "# Прогресс: [Сверка](verification.md) и [Стратегия](../outputs/00_brief_response.md)\n"
        "Текущий шаг: 8 — сопровождение, [фрагмент](../outputs/00_brief_response.md#резюме)\n",
        encoding="utf-8",
    )
    (project / "state/cost_report.md").write_text(
        "## Стоимость: 2026-11-20\n"
        "**Итого: $1.50** — см. [журнал](run_log.md#L3) и [бриф](../outputs/01_brief.md).\n",
        encoding="utf-8",
    )
    (project / "state/ledger/decisions.yaml").write_text(
        "- id: DEC-001\n"
        "  state: decided\n"
        "  chosen: ALT-001\n"
        "  options: [ALT-001, ALT-002]\n"
        "  rationale: 'См. [таблицу](sources/retention_2026.csv) и [бриф](outputs/01_brief.md).'\n",
        encoding="utf-8",
    )

    default_out = project / "outputs/report.html"
    ext_out = tmp_path / "outside" / "nested" / "report.html"
    assert report.main([str(project)]) == 0
    assert report.main([str(project), "--check"]) == 0
    assert report.main([str(project), "--out", str(ext_out)]) == 0
    assert report.main([str(project), "--out", str(ext_out), "--check"]) == 0

    for out_path, state_prefix, outputs_prefix, sources_prefix in (
        (default_out, "../state/", "", "../sources/"),
        (ext_out, "../../example/state/", "../../example/outputs/", "../../example/sources/"),
    ):
        html = out_path.read_text(encoding="utf-8")
        hrefs, ids = _hrefs_and_ids(html)

        assert "#doc-00" in hrefs and "doc-00" in ids
        assert "#doc-01" in hrefs and "doc-01" in ids
        assert f"{state_prefix}verification.md" in hrefs
        assert f"{outputs_prefix}00_brief_response.md#резюме" in hrefs
        assert f"{state_prefix}run_log.md#L3" in hrefs
        assert f"{sources_prefix}retention_2026.csv" in hrefs
        assert "verification.md" not in hrefs
        assert "run_log.md#L3" not in hrefs

        for rel in _relative_hrefs(hrefs):
            assert (out_path.parent / urlsplit(rel).path).resolve().exists(), f"broken {rel} in {out_path}"


@pytest.mark.parametrize("slug", ["esim-travel", "ryadom", "rehearsal-mobile-spend"])
def test_existing_cases_relative_links_valid_with_custom_out(slug, tmp_path):
    project_dir = ROOT / "projects" / slug
    out = tmp_path / "external" / f"{slug}.html"
    assert report.main([str(project_dir), "--out", str(out)]) == 0
    assert report.main([str(project_dir), "--out", str(out), "--check"]) == 0

    html = out.read_text(encoding="utf-8")
    hrefs, ids = _hrefs_and_ids(html)

    for href in hrefs:
        if href.startswith("#"):
            assert href[1:] in ids, f"broken anchor {href} in {slug}"

    for rel in _relative_hrefs(hrefs):
        assert (out.parent / urlsplit(rel).path).resolve().exists(), f"broken relative link {rel} in {slug}"

    if slug == "esim-travel":
        assert "#doc-06" in hrefs
        assert "06_comms_strategy.md" not in hrefs
        assert any(h.endswith("outputs/07_validation_plan.md#ограничения-критериев-и-решения-требующие-согласования") for h in hrefs)
        assert any(h.endswith("outputs/06_comms_strategy.md#ключевое-сообщение-и-его-подтверждение") for h in hrefs)
    elif slug == "ryadom":
        assert "#doc-08" in hrefs
        assert "08_decision_log.md" not in hrefs
    elif slug == "rehearsal-mobile-spend":
        for anchor in ("#doc-01", "#doc-07", "#doc-08"):
            assert anchor in hrefs
        assert any(h.endswith("state/run_log.md#L7") for h in hrefs)
        assert any(h.endswith("state/run_log.md#L19") for h in hrefs)


def _c1_brief_block(screen: str) -> str:
    assert '<div id="c1-brief">' in screen
    return screen.split('<div id="c1-brief">', 1)[1].split("</div>", 1)[0]


def test_open_c1_first_screen_brief_links_and_no_checkpoint_duplication(project, tmp_path):
    (project / "state/ledger/decisions.yaml").write_text("[]\n", encoding="utf-8")
    (project / "state/progress.md").write_text(
        "# Прогресс: Контроль связи\n\n"
        "Текущий шаг: 1 — бриф ожидает подтверждения C1\n\n"
        "## Шаги\n- [ ] 1. Постановка задачи → C1 (outputs/01_brief.md)\n\n"
        "## Что дальше\n- После подтверждения C1 составить план исследования.\n\n"
        "## Открытые вопросы к человеку\n"
        "- Роль: руководитель направления\n"
        "- Ожидаемый ответ: подтвердить рамку пилота и критерии выбора.\n",
        encoding="utf-8",
    )
    (project / "outputs/01_brief.md").write_text(
        "# Бриф\n\n"
        "## Главное\n"
        "Проверить отток [CLM-001] и [выгрузку](../sources/retention_2026.csv); масштаб эффекта неизвестен.\n\n"
        "## Какое решение предстоит принять\n"
        "Пилотировать решение А или оставить текущий процесс [CLM-999] <тест> [xss](javascript:alert(1))?\n\n"
        "| Поле | Значение |\n|---|---|\n| Цель | Табличная строка не для первого экрана |\n\n"
        "## История\n"
        "## Какое решение предстоит принять\n"
        "Устаревший вопрос из истории.\n",
        encoding="utf-8",
    )

    default_out = project / "outputs/report.html"
    ext_out = tmp_path / "external" / "c1.html"
    assert report.main([str(project)]) == 0
    assert check_without_writes(project, tmp_path) == 0
    assert report.main([str(project), "--out", str(ext_out)]) == 0
    assert check_without_writes(project, tmp_path, "--out", str(ext_out)) == 0

    for out_path, sources_prefix in ((default_out, "../sources/"), (ext_out, "../example/sources/")):
        html = out_path.read_text(encoding="utf-8")
        screen = current_screen(html)
        brief = _c1_brief_block(screen)
        plain = unescape(re.sub("<[^>]*>", " ", screen))

        assert screen.index("Текущий шаг:") < screen.index('id="c1-brief"') < screen.index('id="checkpoint-approval"')
        assert html.index('id="c1-brief"') < html.index('<nav aria-label="Оглавление">')
        assert "Пилотировать решение А или оставить текущий процесс" in brief
        assert "масштаб эффекта неизвестен" in brief
        assert "Табличная строка не для первого экрана" not in brief
        assert "Устаревший вопрос из истории" not in brief
        assert "<тест>" not in brief and "&lt;тест&gt;" in brief
        assert not any(h.strip().lower().startswith("javascript:") for h in _hrefs_and_ids(html)[0])

        # Роль и ожидаемый ответ не дублируются между #c1-brief и #checkpoint-approval.
        assert plain.count("руководитель направления") == 1
        assert plain.count("подтвердить рамку пилота и критерии выбора.") == 1

        hrefs, ids = _hrefs_and_ids(html)
        brief_hrefs, _ = _hrefs_and_ids(brief)
        assert "#doc-01" in brief_hrefs and "doc-01" in ids
        assert "#ref-CLM-001" in brief_hrefs and "ref-CLM-001" in ids
        assert f"{sources_prefix}retention_2026.csv" in brief_hrefs
        assert (out_path.parent / f"{sources_prefix}retention_2026.csv").resolve().exists()
        refs = {a.get("data-id"): a for tag, a in Tags(brief).tags if tag == "a" and "data-id" in a}
        assert refs["CLM-001"]["class"] == "ref"
        assert refs["CLM-999"]["class"] == "ref missing"


def test_open_c1_numeric_step_and_legacy_or_incomplete_briefs(project, tmp_path):
    (project / "state/ledger/decisions.yaml").write_text("[]\n", encoding="utf-8")
    progress = project / "state/progress.md"
    open_numeric = (
        "# Прогресс: Кейс\nТекущий шаг: 1\n\n## Шаги\n"
        "- [ ] 1. Постановка задачи → C1\n\n"
        "## Что дальше\n- awaiting_human\n\n"
        "## Открытые вопросы к человеку\n- awaiting_human\n"
        "- Роль: владелец\n- Ожидаемый ответ: подтвердить рамку.\n"
    )
    progress.write_text(open_numeric, encoding="utf-8")

    # Старый Markdown без «Главного», но с разделом «Допущения, принятые без вопросов».
    (project / "outputs/01_brief.md").write_text(
        "# Старый бриф\n\n## Какое решение предстоит принять\nЗапускать ли пилот X?\n\n"
        "## Допущения, принятые без вопросов\n- A1: частота проблемы неизвестна.\n",
        encoding="utf-8",
    )
    html = render(project, tmp_path)
    brief = _c1_brief_block(current_screen(html))
    assert "Запускать ли пилот X?" in brief
    assert "A1: частота проблемы неизвестна." in brief

    # Минимальный старый бриф без «Главного» и без «Допущений» честно показывает пробел.
    (project / "outputs/01_brief.md").write_text(
        "# Старый бриф\n\n## Какое решение предстоит принять\nЗапускать ли пилот Y?\n",
        encoding="utf-8",
    )
    brief = _c1_brief_block(current_screen(render(project, tmp_path)))
    assert "Запускать ли пилот Y?" in brief and "Пробел:" in brief

    # Раздел с одной таблицей и пустой бриф не подменяют формулировку решения таблицей.
    (project / "outputs/01_brief.md").write_text(
        "# Бриф\n\n## Какое решение предстоит принять\n| Поле | Значение |\n|---|---|\n| Цель | Текст |\n",
        encoding="utf-8",
    )
    brief = _c1_brief_block(current_screen(render(project, tmp_path)))
    assert "Пробел:" in brief and "| Поле |" not in brief and "Текст" not in brief

    (project / "outputs/01_brief.md").write_text("   \n", encoding="utf-8")
    assert "пуст" in _c1_brief_block(current_screen(render(project, tmp_path)))

    # Отсутствующий 01_brief.md не создаёт фиктивный якорь #doc-01.
    (project / "outputs/01_brief.md").unlink()
    brief = _c1_brief_block(current_screen(render(project, tmp_path)))
    assert "отсутствует" in brief and "#doc-01" not in brief

    # Закрытый шаг 1 выключает и #c1-brief, и #checkpoint-approval, и C1 CSS.
    progress.write_text(open_numeric.replace("- [ ] 1.", "- [x] 1."), encoding="utf-8")
    closed_html = render(project, tmp_path)
    assert 'id="c1-brief"' not in closed_html
    assert 'id="checkpoint-approval"' not in closed_html
    assert "#c1-brief" not in closed_html.split("</style>", 1)[0]


@pytest.mark.parametrize("progress_body", [
    "Текущий шаг: 1 — C1 закрыт\n",
    "Текущий шаг: 1 — C1 не открыт\n",
    "Текущий шаг: 1 — C1 открыт?\n",
    "Текущий шаг: 2 — план исследования\n",
    "Текущий шаг: 5 — варианты ждут выбора на C2\n",
    "Текущий шаг: 7 — пакет ожидает утверждения на C3\n",
    "Текущий шаг: 8 — сопровождение\n",
    "## История\nТекущий шаг: 1 — C1 открыт\n",
])
def test_c1_brief_not_rendered_for_closed_or_other_stages(project, tmp_path, progress_body):
    (project / "state/ledger/decisions.yaml").write_text("[]\n", encoding="utf-8")
    (project / "state/progress.md").write_text("# Прогресс: Кейс\n\n" + progress_body, encoding="utf-8")
    html = render(project, tmp_path)
    assert 'id="c1-brief"' not in html
    assert "#c1-brief" not in html.split("</style>", 1)[0]


def test_c3_and_accepted_cases_keep_existing_html_without_c1_css(tmp_path):
    for slug in ("esim-travel", "ryadom", "rehearsal-mobile-spend"):
        out = tmp_path / f"{slug}.html"
        assert report.main([str(ROOT / "projects" / slug), "--out", str(out)]) == 0
        assert report.main([str(ROOT / "projects" / slug), "--out", str(out), "--check"]) == 0
        html = out.read_text(encoding="utf-8")
        assert 'id="c1-brief"' not in html
        assert "#c1-brief" not in html.split("</style>", 1)[0]
        if slug == "esim-travel":
            assert 'id="c3-executive-summary"' in html
            assert "Согласование C3" in current_screen(html)

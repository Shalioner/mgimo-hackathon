"""Проверки офлайн-отчёта: содержимое, доказательства и безопасная разметка."""
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
import re
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
    md = "# Документ\n## Главное\nПервое\n### Деталь\nТекст\n```\n## Не заголовок\n```\n## Дальше\nОстальное\n## Главное\nВторое"
    blocks = report.main_blocks(md)
    assert len(blocks) == 2
    assert "### Деталь" in blocks[0] and "Не заголовок" in blocks[0]
    assert "Остальное" not in blocks[0]
    assert blocks[1] == "Второе"


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

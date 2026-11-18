from __future__ import annotations

import hashlib
from html import unescape
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import report  # noqa: E402


class AnchorAndSectionParser(HTMLParser):
    def __init__(self, html: str):
        super().__init__()
        self.anchors: list[dict[str, str]] = []
        self.ids: set[str] = set()
        self.feed(html)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_map = {k: (v or "") for k, v in attrs}
        if "id" in attr_map:
            self.ids.add(attr_map["id"])
        if tag == "a" and "href" in attr_map:
            self.anchors.append(attr_map)


def plain_text(fragment: str) -> str:
    return unescape(re.sub(r"<[^>]+>", " ", fragment))


def first_screen_before_toc(html: str) -> str:
    assert '<section id="current-state">' in html
    assert '<nav aria-label="Оглавление">' in html
    before_toc = html.split('<nav aria-label="Оглавление">', 1)[0]
    assert '<section id="current-state">' in before_toc
    return before_toc.split('<section id="current-state">', 1)[1].split("</section>", 1)[0]


def c3_summary_block(screen: str) -> str:
    return screen.split('<div id="c3-executive-summary">', 1)[1].split("</div>", 1)[0]


def c3_answer_cards(summary_html: str) -> dict[str, str]:
    cards: dict[str, str] = {}
    for doc_num, body in re.findall(
        r'<article class="summary-card c3-answer" data-doc="(0[567])">(.*?)</article>',
        summary_html,
        flags=re.DOTALL,
    ):
        cards[doc_num] = body
    return cards


def render_and_check(project: Path, out: Path) -> str:
    assert report.main([str(project), "--out", str(out)]) == 0
    assert report.main([str(project), "--out", str(out), "--check"]) == 0
    return out.read_text(encoding="utf-8")


def open_c3_holdout(tmp_path: Path) -> Path:
    copy = tmp_path / "rehearsal-mobile-spend"
    shutil.copytree(ROOT / "projects/rehearsal-mobile-spend", copy)
    progress_path = copy / "state/progress.md"
    progress_text = progress_path.read_text(encoding="utf-8")
    updated, count = re.subn(
        r"^Текущий шаг:.*$",
        "Текущий шаг: 7 — план проверки ожидает утверждения C3",
        progress_text,
        count=1,
        flags=re.MULTILINE,
    )
    assert count == 1
    progress_path.write_text(updated, encoding="utf-8")
    return copy


def test_esim_open_c3_shows_three_answers_before_toc(tmp_path):
    copy = tmp_path / "esim-travel"
    shutil.copytree(ROOT / "projects/esim-travel", copy)
    out = tmp_path / "esim.html"
    html = render_and_check(copy, out)

    screen = first_screen_before_toc(html)
    assert '<div id="c3-executive-summary">' in screen
    # Сводка видима напрямую в текущем состоянии, а не спрятана внутри свёрнутого <details>.
    assert screen.index('<div id="c3-executive-summary">') < screen.index('<details id="cost-summary">')
    summary = c3_summary_block(screen)
    cards = c3_answer_cards(summary)
    assert set(cards) == {"05", "06", "07"}

    parsed = AnchorAndSectionParser(html)
    for num, expected_fragment in [
        ("05", "white-label/CaaS"),
        ("06", "готового подтверждённого сообщения для запуска нет"),
        ("07", "EXP-003"),
    ]:
        card = cards[num]
        card_plain = plain_text(card)
        assert expected_fragment in card_plain
        assert f'href="#doc-{num}"' in card
        assert f"doc-{num}" in parsed.ids
        assert "Статус:" in card_plain
        assert "Ожидает решения руководителя на C3" in card_plain
        assert "не является утверждением" in card_plain
        assert "Ожидаемое действие человека:" in card_plain

    assert "Согласовать проверки свойств" in plain_text(cards["06"])
    assert "Уточнить приоритет stop/go" in plain_text(cards["07"])
    rules = plain_text(cards["07"])
    assert "Условия stop/go из реестра" in rules
    for threshold in ("≥3%", "<1%", ">50%"):
        assert threshold in rules
    assert "EXP-001" in rules and "<5%" in rules


def test_holdout_rehearsal_open_c3_three_answers_and_links(tmp_path):
    copy = open_c3_holdout(tmp_path)
    out = tmp_path / "holdout.html"
    html = render_and_check(copy, out)

    screen = first_screen_before_toc(html)
    assert "Согласование C3" in screen
    assert "ALT-001" in plain_text(screen)
    summary = c3_summary_block(screen)
    cards = c3_answer_cards(summary)
    assert set(cards) == {"05", "06", "07"}

    parsed = AnchorAndSectionParser(html)
    for num, expected_fragment in [
        ("05", "макет предупреждения"),
        ("06", "Публичных обещаний экономии"),
        ("07", "корректность адресата"),
    ]:
        card = cards[num]
        card_plain = plain_text(card)
        assert expected_fragment in card_plain
        assert f'href="#doc-{num}"' in card
        assert f"doc-{num}" in parsed.ids
        assert "Ожидает решения руководителя на C3" in card_plain
        assert "Ожидаемое действие человека:" in card_plain
        # Историческая заметка из 09/progress не превращается в статус согласия на C3.
        assert "утверждён" not in card_plain.lower()


@pytest.mark.parametrize("doc_name,doc_num", [
    ("06_comms_strategy.md", "06"),
    ("07_validation_plan.md", "07"),
])
def test_honest_gap_when_document_missing(tmp_path, doc_name, doc_num):
    copy = tmp_path / "esim-gap-missing-file"
    shutil.copytree(ROOT / "projects/esim-travel", copy)
    (copy / "outputs" / doc_name).unlink()
    progress = copy / "state/progress.md"
    header = "## Открытые вопросы к человеку\n"
    progress.write_text(progress.read_text(encoding="utf-8").replace(
        header, header + f"- Утвердить текущий документ ({doc_num}).\n", 1,
    ), encoding="utf-8")
    out = tmp_path / f"gap-file-{doc_num}.html"
    html = render_and_check(copy, out)

    cards = c3_answer_cards(c3_summary_block(first_screen_before_toc(html)))
    assert set(cards) == {"05", "06", "07"}
    gap_card = cards[doc_num]
    assert "Пробел" in plain_text(gap_card)
    assert "Запросить документ до согласования" in plain_text(gap_card)
    assert "Утвердить текущий документ" not in plain_text(gap_card)
    assert f'href="#doc-{doc_num}"' not in gap_card
    for other in {"05", "06", "07"} - {doc_num}:
        assert f'href="#doc-{other}"' in cards[other]


@pytest.mark.parametrize("doc_name,doc_num", [
    ("06_comms_strategy.md", "06"),
    ("07_validation_plan.md", "07"),
])
def test_honest_gap_when_main_section_missing(tmp_path, doc_name, doc_num):
    copy = tmp_path / "esim-gap-no-main"
    shutil.copytree(ROOT / "projects/esim-travel", copy)
    (copy / "outputs" / doc_name).write_text("# Документ без краткого вывода\n\nТекст без блока.\n", encoding="utf-8")
    out = tmp_path / f"gap-main-{doc_num}.html"
    html = render_and_check(copy, out)

    cards = c3_answer_cards(c3_summary_block(first_screen_before_toc(html)))
    assert set(cards) == {"05", "06", "07"}
    gap_card = cards[doc_num]
    gap_plain = plain_text(gap_card)
    assert "Пробел" in gap_plain
    assert "Главное" in gap_plain
    assert f'href="#doc-{doc_num}"' in gap_card


def test_c2_does_not_render_c3_executive_summary(tmp_path):
    copy = tmp_path / "esim-c2"
    shutil.copytree(ROOT / "projects/esim-travel", copy)
    (copy / "state/progress.md").write_text(
        "# Прогресс: eSIM\nТекущий шаг: 5 — варианты ждут выбора на C2\n\n"
        "## Открытые вопросы к человеку\n- Роль: Руководитель продукта\n- Ожидаемый ответ: Выбрать вариант.\n",
        encoding="utf-8",
    )
    out = tmp_path / "c2.html"
    html = render_and_check(copy, out)
    screen = first_screen_before_toc(html)
    assert "Согласование C2" in screen
    assert 'id="c3-executive-summary"' not in html


def test_stale_c3_at_step_8_points_to_reapproval_without_faking_consent(tmp_path):
    copy = tmp_path / "ryadom-stale"
    shutil.copytree(ROOT / "projects/ryadom", copy)

    def run_tool(tool: str, *args: str | Path) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-B", str(ROOT / "tools" / f"{tool}.py"), *map(str, args)],
            cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
        )

    prep = run_tool("c3_approval", "prepare", copy)
    assert prep.returncode == 0, prep.stderr
    ans = tmp_path / "answer.txt"
    ans.write_text("Утверждаю учебный пакет.", encoding="utf-8")
    rec = run_tool("c3_approval", "record", copy, "--sha256", prep.stdout.strip(), "--response-file", ans)
    assert rec.returncode == 0, rec.stderr

    # Меняем подтверждённое обещание -> запись C3 становится stale при шаге 8.
    promises = copy / "state/ledger/promises.yaml"
    promises.write_text(
        promises.read_text(encoding="utf-8").replace(
            "message: Геопозиция детского устройства",
            "message: Изменённая геопозиция детского устройства",
        ),
        encoding="utf-8",
    )

    out = tmp_path / "stale-step8.html"
    html = render_and_check(copy, out)
    screen = first_screen_before_toc(html)
    plain = plain_text(screen)
    assert "Требуется повторное C3" in plain
    assert "явное утверждение руководителя на повторном C3" in plain
    assert 'id="c3-executive-summary"' not in html

    # Если при этом открыто повторное C3 в progress.md, карточки честно отражают повторное согласование.
    progress = copy / "state/progress.md"
    progress.write_text(
        re.sub(
            r"^Текущий шаг:.*$",
            "Текущий шаг: 8 — сопровождение; повторное C3 открыто, изменённый пакет ожидает согласования",
            progress.read_text(encoding="utf-8"),
            count=1,
            flags=re.MULTILINE,
        ),
        encoding="utf-8",
    )
    html_open = render_and_check(copy, tmp_path / "stale-open-c3.html")
    cards = c3_answer_cards(c3_summary_block(first_screen_before_toc(html_open)))
    assert set(cards) == {"05", "06", "07"}
    for num in ("05", "06", "07"):
        assert "повторном согласовании C3" in plain_text(cards[num])


def test_timestamp_neutrality_with_c3_executive_summary(tmp_path):
    copy = tmp_path / "esim-ts"
    shutil.copytree(ROOT / "projects/esim-travel", copy)
    out = tmp_path / "ts.html"
    assert report.main([str(copy), "--out", str(out)]) == 0
    html = out.read_text(encoding="utf-8")
    assert 'id="c3-executive-summary"' in html

    altered, count = re.subn(
        r'(<p class="meta">Сформировано: )[^<]+</p>',
        r"\g<1>2030-05-01T09:15:30+03:00</p>",
        html,
        count=1,
    )
    assert count == 1
    out.write_text(altered, encoding="utf-8")
    assert report.main([str(copy), "--out", str(out), "--check"]) == 0

    tampered = altered.replace('data-doc="05"', 'data-doc="99"', 1)
    assert tampered != altered
    out.write_text(tampered, encoding="utf-8")
    assert report.main([str(copy), "--out", str(out), "--check"]) == 1


def test_does_not_infer_c3_consent_or_business_verdict_from_prose(tmp_path):
    copy = tmp_path / "esim-misleading-prose"
    shutil.copytree(ROOT / "projects/esim-travel", copy)
    (copy / "outputs/07_validation_plan.md").write_text(
        "# План проверки\n\n## Главное\n"
        "C3 закрыт и утверждён автоматически: GO при любой конверсии.\n",
        encoding="utf-8",
    )
    out = tmp_path / "misleading.html"
    html = render_and_check(copy, out)
    summary = c3_summary_block(first_screen_before_toc(html))
    card_07 = c3_answer_cards(summary)["07"]
    status_match = re.search(r'<p class="c3-status">(.*?)</p>', card_07, flags=re.DOTALL)
    assert status_match is not None
    status_plain = plain_text(status_match.group(1))
    assert "Ожидает решения руководителя на C3" in status_plain
    assert "не является утверждением" in status_plain
    assert "C3 закрыт" not in status_plain

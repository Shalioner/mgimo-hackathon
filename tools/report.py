#!/usr/bin/env python3
"""Собрать документы и реестр проекта в один читаемый офлайн-отчёт."""
from __future__ import annotations

import argparse
from datetime import datetime
from html import escape
from pathlib import Path
import re
import sys
from urllib.parse import urlsplit

from ledger import Ledger


ID = re.compile(r"\b(?:SRC|CLM|HYP|PAIN|ALT|DEC|PRM|EXP|CHG)-[0-9]{3}\b")
HEADING = re.compile(r"^ {0,3}(#{1,4})\s+(.+?)\s*#*\s*$")
FENCE = re.compile(r"^\s*(`{3,}|~{3,})(.*)$")
LIST = re.compile(r"^\s*(?:(-)|([0-9]+)\.)\s+(.*)$")
# Сначала код и ссылки: их содержимое не должно стать разметкой случайно.
INLINE = re.compile(r"(`+)(.+?)\1|\[([^\]\n]+)\]\(([^\s)]+)\)|\*\*(.+?)\*\*|\*([^*\n]+)\*")


def text(value) -> str:
    return "" if value is None else str(value)


def tooltip(obj: dict) -> str:
    oid = obj["id"]
    parts = []
    if oid.startswith("CLM-"):
        confidence = obj.get("confidence", {})
        parts = [text(obj.get("type")), text(confidence.get("level") if isinstance(confidence, dict) else confidence),
                 text(obj.get("statement"))]
        parts += [f'«{e.get("quote", "")}» ({e.get("source", "")})' for e in obj.get("evidence", [])]
    elif oid.startswith("SRC-"):
        parts = [text(obj.get("title")), text(obj.get("kind")), text(obj.get("url") or obj.get("path"))]
    else:
        # Реестр использует также предметные поля PRM, DEC, EXP и CHG.
        parts = [next((text(obj[k]) for k in ("statement", "title", "description", "message", "question", "expected_change", "summary") if obj.get(k)), "")]
    if obj.get("status") or obj.get("state"):
        parts.append(text(obj.get("status") or obj.get("state")))
    if obj.get("superseded_by"):
        parts.append("superseded_by: " + text(obj["superseded_by"]))
    return " · ".join(p for p in parts if p)


def safe_url(url: str) -> bool:
    """Разрешить обычные ссылки, исключить исполняемые схемы и управляющие символы."""
    try:
        return not any(ord(c) < 32 for c in url) and urlsplit(url).scheme.lower() in ("", "http", "https", "mailto")
    except ValueError:
        return False


class Renderer:
    def __init__(self, objects: dict):
        self.objects = objects

    def plain(self, value: str) -> str:
        chunks, pos = [], 0
        for match in ID.finditer(value):
            chunks.append(escape(value[pos:match.start()]))
            oid = match.group()
            obj = self.objects.get(oid)
            cls = "ref" if obj else "ref missing"
            if obj and obj.get("status") in ("superseded", "retracted"):
                cls += " obsolete"
            hint = tooltip(obj) if obj else "нет в реестре"
            chunks.append(f'<a class="{cls}" data-id="{oid}" href="#ref-{oid}" title="{escape(hint, quote=True)}">{oid}</a>')
            pos = match.end()
        chunks.append(escape(value[pos:]))
        return "".join(chunks)

    def inline(self, value: str) -> str:
        chunks, pos = [], 0
        for match in INLINE.finditer(value):
            chunks.append(self.plain(value[pos:match.start()]))
            ticks, code, label, url, bold, italic = match.groups()
            if ticks:
                chunks.append("<code>" + self.plain(code) + "</code>")
            elif label is not None:
                if safe_url(url):
                    # Ссылки на ID остаются самостоятельными якорями, без вложенных <a>.
                    if ID.search(label):
                        chunks.append(self.inline(label) + f' <a href="{escape(url, quote=True)}">↗</a>')
                    else:
                        chunks.append(f'<a href="{escape(url, quote=True)}">{escape(label)}</a>')
                else:
                    chunks.append(self.plain(match.group()))
            else:
                tag, content = ("strong", bold) if bold is not None else ("em", italic)
                chunks.append(f"<{tag}>" + self.inline(content) + f"</{tag}>")
            pos = match.end()
        chunks.append(self.plain(value[pos:]))
        return "".join(chunks)

    def markdown(self, value: str) -> str:
        lines, out, i = value.splitlines(), [], 0
        while i < len(lines):
            line = lines[i]
            if not line.strip():
                i += 1
                continue
            fence = FENCE.match(line)
            if fence:
                marker, info = fence.groups()
                i += 1
                code = []
                while i < len(lines) and not re.fullmatch(r"\s*" + re.escape(marker[0]) + "{" + str(len(marker)) + r",}\s*", lines[i]):
                    code.append(lines[i])
                    i += 1
                caption = '<div class="code-label">' + self.plain(info.strip()) + '</div>' if info.strip() else ""
                out.append(caption + "<pre><code>" + self.plain("\n".join(code)) + "</code></pre>")
                i += 1
                continue
            heading = HEADING.match(line)
            if heading:
                level, title = len(heading[1]), heading[2]
                out.append(f"<h{level}>" + self.inline(title) + f"</h{level}>")
                i += 1
                continue
            if i + 1 < len(lines) and "|" in line and table_separator(lines[i + 1]):
                headers = cells(line)
                out.append('<div class="table-scroll" tabindex="0" role="region" aria-label="Таблица"><table><thead><tr>' +
                           "".join("<th>" + self.inline(c) + "</th>" for c in headers) + "</tr></thead><tbody>")
                i += 2
                while i < len(lines) and lines[i].strip() and "|" in lines[i]:
                    out.append("<tr>" + "".join("<td>" + self.inline(c) + "</td>" for c in cells(lines[i])) + "</tr>")
                    i += 1
                out.append("</tbody></table></div>")
                continue
            item = LIST.match(line)
            if item:
                ordered = item[2] is not None
                tag = "ol" if ordered else "ul"
                start = f' start="{int(item[2])}"' if ordered else ""
                out.append(f"<{tag}{start}>")
                while i < len(lines):
                    item = LIST.match(lines[i])
                    if not item or (item[2] is not None) != ordered:
                        break
                    number = f' value="{int(item[2])}"' if ordered else ""
                    out.append(f"<li{number}>" + self.inline(item[3]) + "</li>")
                    i += 1
                out.append(f"</{tag}>")
                continue
            # Неподдержанная разметка сохраняется как экранированный текст.
            paragraph = [line]
            i += 1
            while i < len(lines) and lines[i].strip():
                if HEADING.match(lines[i]) or FENCE.match(lines[i]) or LIST.match(lines[i]):
                    break
                if i + 1 < len(lines) and "|" in lines[i] and table_separator(lines[i + 1]):
                    break
                paragraph.append(lines[i])
                i += 1
            out.append("<p>" + self.inline("\n".join(paragraph)) + "</p>")
        return "\n".join(out)


def cells(line: str) -> list:
    """Разделить строку GFM, сохранив экранированные и находящиеся в коде |."""
    line = line.strip()
    if line.startswith("|"):
        line = line[1:]
    if line.endswith("|") and not line.endswith("\\|"):
        line = line[:-1]
    result, part, ticks, i = [], [], "", 0
    while i < len(line):
        char = line[i]
        if char == "\\" and i + 1 < len(line) and line[i + 1] == "|":
            part.append("|")
            i += 2
            continue
        if char == "`":
            end = i + 1
            while end < len(line) and line[end] == "`":
                end += 1
            marker = line[i:end]
            ticks = "" if ticks == marker else (marker if not ticks else ticks)
            part.append(marker)
            i = end
            continue
        if char == "|" and not ticks:
            result.append("".join(part).strip())
            part = []
        else:
            part.append(char)
        i += 1
    return result + ["".join(part).strip()]


def table_separator(line: str) -> bool:
    parts = cells(line)
    return bool(parts) and all(re.fullmatch(r":?-{3,}:?", p) for p in parts)


def headings(value: str):
    """Заголовки вне блоков кода; позиции нужны и для извлечения «Главного»."""
    fence = None
    for index, line in enumerate(value.splitlines()):
        match = FENCE.match(line)
        if match:
            marker = match[1]
            if fence is None:
                fence = marker
            elif marker[0] == fence[0] and len(marker) >= len(fence) and not match[2].strip():
                fence = None
        elif fence is None:
            match = HEADING.match(line)
            if match:
                yield index, len(match[1]), match[2]


def main_blocks(value: str) -> list:
    lines, found, blocks = value.splitlines(), list(headings(value)), []
    for pos, (index, level, title) in enumerate(found):
        if title.strip().strip("*").strip() == "Главное":
            end = next((n for n, depth, _ in found[pos + 1:] if depth <= level), len(lines))
            blocks.append("\n".join(lines[index + 1:end]))
    return blocks


CSS = """
:root { color-scheme: light dark; --bg:#f5f6f8; --paper:#fff; --ink:#17232f; --muted:#536271; --line:#d7dee5; --accent:#00684c; }
@media (prefers-color-scheme: dark) { :root { --bg:#12191f; --paper:#1c262f; --ink:#e7edf3; --muted:#b0becb; --line:#42515e; --accent:#75d9b4; } }
* { box-sizing:border-box; } body { margin:0; background:var(--bg); color:var(--ink); font:17px/1.65 system-ui,sans-serif; overflow-wrap:anywhere; }
main { max-width:1120px; padding:36px 28px; margin:auto; min-width:0; } header { margin-bottom:32px; } h1,h2,h3,h4 { line-height:1.25; } h1 { font-size:1.9rem; } h2 { font-size:1.5rem; } h3 { font-size:1.2rem; }
a { color:var(--accent); text-underline-offset:3px; } a:focus-visible { outline:3px solid var(--accent); outline-offset:3px; }
section,nav { background:var(--paper); padding:28px; margin:24px 0; border:1px solid var(--line); border-radius:12px; min-width:0; }
.meta,.document-label { color:var(--muted); font-size:.9rem; } .summary-card { border-top:1px solid var(--line); padding:12px 0; } .summary-card:last-child { padding-bottom:0; }
.ref { font-size:.9em; white-space:nowrap; } .missing { color:#c34f37; text-decoration-style:wavy; } .obsolete,.obsolete .ref { text-decoration:line-through; }
.table-scroll { max-width:100%; overflow-x:auto; margin:18px 0; } table { border-collapse:collapse; width:100%; font-size:.9rem; } th,td { padding:10px 12px; border:1px solid var(--line); text-align:left; vertical-align:top; min-width:100px; } th { background:var(--bg); }
pre { overflow-x:auto; padding:16px; background:var(--bg); border:1px solid var(--line); } code { font-size:.9em; } p { white-space:pre-line; } .registry-row { padding:18px 0; border-top:1px solid var(--line); } .registry-row p { margin:6px 0; } :target { outline:2px solid var(--accent); outline-offset:4px; }
@media (max-width:600px) { main { padding:18px 10px; } section,nav { padding:18px 14px; } body { font-size:16px; } h1 { font-size:1.5rem; } }
@media print { :root { --bg:white; --paper:white; --ink:black; --muted:#333; --line:#aaa; } body { font-size:10pt; } main { max-width:none; padding:0; } section,nav { border:0; padding:0; border-radius:0; } a { color:black !important; } .table-scroll,pre { overflow:visible; } table { page-break-inside:auto; table-layout:fixed; } tr { page-break-inside:avoid; } th,td { min-width:0; overflow-wrap:anywhere; } pre { white-space:pre-wrap; } h1,h2,h3,h4 { page-break-after:avoid; } }
"""


def generate(project: Path, out: Path) -> None:
    if not (project / "outputs").is_dir() or not (project / "state/ledger").is_dir():
        raise ValueError("нет папки outputs/ или реестра state/ledger/")
    ledger = Ledger(project)
    if ledger.load_problems:
        raise ValueError("реестр не читается: " + "; ".join(f"{p}: {m}" for p, m in ledger.load_problems))
    renderer = Renderer(ledger.objects)
    docs = []
    for path in sorted((project / "outputs").glob("0*.md")):
        value = path.read_text(encoding="utf-8")
        title = next((t for _, _, t in headings(value) if t != "Главное"), path.stem)
        docs.append(("doc-" + path.name[:2], title, value, path.name))
    progress = project / "state/progress.md"
    title = ""
    if progress.is_file():
        title = next((t for _, _, t in headings(progress.read_text(encoding="utf-8"))), "")
        title = re.sub(r"^Прогресс:\s*", "", title)
    if not title:
        title = next((t for _, t, _, name in docs if name.startswith("01")), project.name)
    page = ['<!doctype html><html lang="ru"><head><meta charset="utf-8">',
            '<meta name="viewport" content="width=device-width, initial-scale=1">',
            "<title>" + escape(title) + "</title><style>" + CSS + "</style></head><body><main>",
            "<header><h1>" + renderer.inline(title) + "</h1>",
            '<p class="meta">Сформировано: ' + datetime.now().astimezone().isoformat(timespec="seconds") + "</p>"]
    cost = project / "state/cost_report.md"
    if cost.is_file():
        total = next((line for line in cost.read_text(encoding="utf-8").splitlines() if "Итого" in line), "")
        if total:
            page.append('<p class="meta">' + renderer.inline(total) + "</p>")
    page.append('</header><section id="main-points"><h2>Главное</h2>')
    for anchor, label, value, name in docs:
        for block in main_blocks(value):
            page.append('<article class="summary-card"><h3><a href="#' + anchor + '">' +
                        escape(name + " — " + label) + "</a></h3>" + renderer.markdown(block) + "</article>")
    page.append('</section><nav aria-label="Оглавление"><h2>Оглавление</h2><ol>')
    for anchor, label, _, name in docs:
        page.append('<li><a href="#' + anchor + '">' + escape(name + " — " + label) + "</a></li>")
    page.append('<li><a href="#registry">Реестр</a></li></ol></nav>')
    for anchor, _, value, name in docs:
        page.append(f'<section id="{anchor}"><p class="document-label">{escape(name)}</p>' + renderer.markdown(value) + "</section>")
    page.append('<section id="registry"><h2>Приложение: Реестр</h2>')
    for oid, obj in sorted(ledger.objects.items()):
        cls = "registry-row obsolete" if obj.get("status") in ("superseded", "retracted") else "registry-row"
        page.append(f'<article class="{cls}" id="ref-{escape(oid, quote=True)}"><h3>{escape(oid)}</h3>')
        page.append("<p>" + renderer.plain(tooltip(obj)) + "</p>")
        if oid.startswith("SRC-"):
            url = text(obj.get("url") or obj.get("path"))
            if url:
                # Локальные материалы считаются относительно проекта, а не outputs/.
                if not urlsplit(url).scheme and obj.get("path") == url:
                    import os
                    url = Path(os.path.relpath(project / url, out.parent)).as_posix()
                page.append("<p>Источник: " + (f'<a href="{escape(url, quote=True)}">{escape(url)}</a>' if safe_url(url) else escape(url)) + "</p>")
            page.append("<p>" + escape(" · ".join(f"{k}: {text(obj.get(k))}" for k in ("kind", "access", "retrieved"))) + "</p>")
        page.append("</article>")
    page.append("</section></main></body></html>")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(page) + "\n", encoding="utf-8")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("project", type=Path)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args(argv)
    out = args.out or args.project / "outputs/report.html"
    try:
        generate(args.project, out)
    except (OSError, ValueError) as exc:
        print(f"Не удалось собрать отчёт: {exc}", file=sys.stderr)
        return 2
    print(f"Отчёт: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Собрать документы и реестр проекта в один читаемый офлайн-отчёт."""
from __future__ import annotations

import argparse
from datetime import datetime
from html import escape
import os
from pathlib import Path
import re
import shlex
import sys
from urllib.parse import urlsplit

from c3_approval import status as approval_status
from ledger import Ledger


ID = re.compile(r"\b(?:SRC|CLM|HYP|PAIN|ALT|DEC|PRM|EXP|CHG)-[0-9]{3}\b")
HEADING = re.compile(r"^ {0,3}(#{1,6})\s+(.+?)\s*#*\s*$")
FENCE = re.compile(r"^\s*(`{3,}|~{3,})(.*)$")
LIST = re.compile(r"^\s*(?:(-)|([0-9]+)\.)\s+(.*)$")
# Сначала код и ссылки: их содержимое не должно стать разметкой случайно.
MD_LINK = r"\[([^\]\n]+)\]\(([^\s)]+)\)"
INLINE = re.compile(r"(`+)(.+?)\1|" + MD_LINK + r"|\*\*(.+?)\*\*|\*([^*\n]+)\*")
CHECKPOINT_DOCS = {"C1": "01", "C2": "04", "C3": "07"}
CHECKPOINT_MATERIAL = re.compile(
    r"(?<![\w./:-])(?:outputs/)?((?:01|04|05|06|07)_[\w-]+\.md)(?![\w/])"
    r"|\((?:предложения в )?(01|04|05|06|07)\)"
)
OPEN_CHECKPOINT = re.compile(
    r"(?:повторное\s+)?(C[123])\s*:?[ \t]+(?:сейчас )?(?:открыт[оа]?|"
    r"(?:ожидает|жд[её]т) (?:ответа|подтверждения|выбора|утверждения|согласования))"
    r"|(?:бриф|(?:первый |изменённый )?пакет|план проверки|варианты) "
    r"(?:ожида[ею]т|жд[её]т|ждут) (?:подтверждения|выбора|утверждения|согласования) (?:на )?(C[123])"
    r"|(?:после аудита(?: качества)? )?(?:требуется|ожидается) "
    r"(?:повторное |согласование уточнений )?(C[123])", re.IGNORECASE
)
CHECKPOINT_FOLLOWUP = re.compile(
    r",\s*изменённый пакет (?:ожидает согласования|жд[её]т ответа)$", re.IGNORECASE,
)
INLINE_REQUEST = re.compile(
    r"\.\s+(?:(?:руководитель|владелец|заказчик|согласующий)\b[^:.;!?]*):\s+"
    r"(?:подтвердить|выбрать|утвердить|согласовать|уточнить)\b[^.;!?]+"
    r"\.\s+Материал\s+outputs/(?:01|04|06|07)_[\w-]+\.md\.?$", re.IGNORECASE,
)
GATE_FIELD = re.compile(r"^(C[123])\s*[—–:-]\s*", re.IGNORECASE)
GATE_SECTION = re.compile(r"(C[123])(?:\s*\(открыт[оа]?\))?", re.IGNORECASE)
RESPONSE_ACTION = re.compile(
    r"^(?:подтвердить|выбрать|утвердить|согласовать|уточнить)\b", re.IGNORECASE,
)
DOC_TITLES = {
    "01": "Бриф", "02": "План исследования", "03": "Картина ситуации",
    "04": "Варианты", "05": "Продуктовая стратегия", "06": "Коммуникационная стратегия",
    "07": "План проверки", "08": "Журнал решения", "09": "Отчёт о прогоне",
}
C3_SUMMARY_DOCS = (
    ("05", "Что делать", "Проверить объём работ и осознанные отказы."),
    ("06", "Что обещать клиенту", "Сверить обещания с их прямыми подтверждениями."),
    ("07", "Когда продолжать или останавливать пилот", "Согласовать сроки и условия остановки и продолжения."),
)
GENERATED_AT_PREFIX = '<p class="meta">Сформировано: '


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

    def inline(self, value: str, *, links: bool = True, references: bool = True) -> str:
        plain = self.plain if references else escape
        chunks, pos = [], 0
        for match in INLINE.finditer(value):
            chunks.append(plain(value[pos:match.start()]))
            ticks, code, label, url, bold, italic = match.groups()
            if ticks:
                chunks.append("<code>" + plain(code) + "</code>")
            elif label is not None:
                if not links:
                    chunks.append(plain(label))
                elif safe_url(url):
                    # Ссылки на ID остаются самостоятельными якорями, без вложенных <a>.
                    if ID.search(label):
                        chunks.append(self.inline(label) + f' <a href="{escape(url, quote=True)}">↗</a>')
                    else:
                        chunks.append(f'<a href="{escape(url, quote=True)}">{escape(label)}</a>')
                else:
                    chunks.append(self.plain(match.group()))
            else:
                tag, content = ("strong", bold) if bold is not None else ("em", italic)
                chunks.append(f"<{tag}>" + self.inline(content, links=links, references=references) + f"</{tag}>")
            pos = match.end()
        chunks.append(plain(value[pos:]))
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


def prose_lines(value: str):
    """Строки Markdown вне блоков кода с исходными позициями."""
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
            yield index, line


def headings(value: str):
    """Заголовки вне блоков кода; позиции нужны и для извлечения «Главного»."""
    for index, line in prose_lines(value):
        match = HEADING.match(line)
        if match:
            yield index, len(match[1]), match[2]


def main_blocks(value: str) -> list:
    lines, found = value.splitlines(), list(headings(value))
    for pos, (index, _, title) in enumerate(found):
        if title.strip().strip("*").strip() == "Главное":
            end = found[pos + 1][0] if pos + 1 < len(found) else len(lines)
            return ["\n".join(lines[index + 1:end])]
    return []


def current_step(value: str) -> str:
    """Одна строка актуального состояния перед разделами progress."""
    steps = []
    title_seen = False
    for _, line in prose_lines(value):
        heading = HEADING.match(line)
        if heading:
            if title_seen or steps or heading[1] != "#" or re.match(r"^(?:Истори|Архив)", heading[2], re.I):
                break
            title_seen = True
            continue
        match = re.match(r"^ {0,3}Текущий шаг:[ \t]*(.*)", line)
        if match:
            steps.append(match[1].strip())
    if len(steps) == 1 and steps[0]:
        return steps[0]
    if len(steps) > 1:
        return "Неизвестно: в progress.md несколько строк текущего шага."
    return "Неизвестно: текущий шаг не записан в progress.md."


def next_action(value: str) -> str:
    """Первый пункт текущего раздела «Что дальше» в progress.md."""
    start = None
    top_seen = False
    for index, level, title in headings(value):
        if level == 1:
            if top_seen:
                break
            top_seen = True
        if level == 2 and title.strip("* ").casefold() == "что дальше":
            start = index
            break
    if start is None:
        return ""
    for index, line in prose_lines(value):
        if index <= start:
            continue
        heading = HEADING.match(line)
        if heading and len(heading[1]) <= 2:
            break
        item = LIST.match(line)
        if item:
            return item[3].strip()
    return ""


def active_checkpoint(step: str) -> str | None:
    step = re.sub(r"^\d+\s*[—–-]\s*", "", step)
    step = re.sub(r"^сопровождение;\s*", "", step, flags=re.IGNORECASE)
    step = CHECKPOINT_FOLLOWUP.sub("", step)
    step = INLINE_REQUEST.sub("", step)
    match = OPEN_CHECKPOINT.fullmatch(step.removesuffix("."))
    return next(part.upper() for part in match.groups() if part) if match else None


def checkpoint_questions(progress: str, gate: str) -> list[str]:
    sections = list(headings(progress))
    starts = [pos for pos, (_, level, title) in enumerate(sections)
              if level == 2 and title.strip("* ").casefold() == "открытые вопросы к человеку"]
    if len(starts) != 1:
        return []
    pos = starts[0]
    start = sections[pos][0] + 1
    end = next((index for index, level, _ in sections[pos + 1:] if level <= 2), len(progress.splitlines()))
    questions = []
    in_scope = True
    for index, line in prose_lines(progress):
        if not start <= index < end or line.startswith(("    ", "\t")):
            continue
        heading = HEADING.match(line)
        if heading:
            scope = GATE_SECTION.fullmatch(heading[2]) if len(heading[1]) == 3 else None
            in_scope = bool(scope and scope[1].upper() == gate)
        elif line.strip() and in_scope:
            questions.append(line.strip())
    return questions


def checkpoint_block(progress: str, step: str, docs: list, renderer: Renderer) -> list[str]:
    gate = active_checkpoint(step)
    if not gate:
        return []
    roles, responses = [], []
    materials = [step]
    for line in checkpoint_questions(progress, gate):
        item = LIST.match(line)
        value = (item[3] if item else line).lstrip("* ")
        field_gate = GATE_FIELD.match(value)
        if field_gate:
            if field_gate[1].upper() != gate:
                continue
            value = value[field_gate.end():]
        label, colon, detail = value.partition(":")
        label, detail = label.strip("* "), detail.strip().removeprefix("**").strip()
        if colon and label.casefold() in ("материал", "материалы"):
            materials.append(detail)
        elif colon and label.casefold() == "роль":
            if detail:
                roles.append(detail)
        elif colon and label.casefold() == "ожидаемый ответ":
            if detail:
                responses.append(detail)
        elif item and colon and re.match(r"^(?:руководитель|владелец|заказчик|согласующий)\b", label, re.IGNORECASE):
            roles.append(label)
            if detail:
                responses.append(detail)
        elif item and RESPONSE_ACTION.match(value):
            responses.append(value)

    numbers, names = {CHECKPOINT_DOCS[gate]}, set()
    for value in materials + responses:
        # Цель Markdown-ссылки из progress не служит основанием для ссылки в отчёте.
        visible = re.sub(MD_LINK, r"\1", value)
        for name, number in CHECKPOINT_MATERIAL.findall(visible):
            if name:
                names.add(name)
            else:
                numbers.add(number)
    links = [f'<a href="#{anchor}">{escape(DOC_TITLES[name[:2]])}</a>'
             for anchor, _, _, name in docs if name in names or name[:2] in numbers]
    role = "Роль не указана" if not roles else " / ".join(dict.fromkeys(roles))
    answer = "<br>".join(renderer.inline(value, links=False, references=False)
                         for value in dict.fromkeys(responses))
    block = [f'<div id="checkpoint-approval"><h3>Согласование {gate}</h3>',
             '<p><strong>Согласующий:</strong> ' + renderer.inline(role, links=False, references=False) + '</p>',
             '<p><strong>Ожидаемый ответ:</strong> ' + (answer or "Не указан в актуальных вопросах.") + '</p>']
    if links:
        block.append(f'<p><strong>Материалы {gate}:</strong> ' + " · ".join(links) + '</p>')
    return block + ['</div>']


def c3_doc_actions(progress: str) -> dict[str, list[str]]:
    by_doc = {"05": [], "06": [], "07": []}
    for line in checkpoint_questions(progress, "C3"):
        item = LIST.match(line)
        value = (item[3] if item else line).lstrip("* ")
        field_gate = GATE_FIELD.match(value)
        if field_gate:
            if field_gate[1].upper() != "C3":
                continue
            value = value[field_gate.end():]
        label, colon, detail = value.partition(":")
        label_clean = label.strip("* ")
        detail_clean = detail.strip().removeprefix("**").strip()
        if colon and label_clean.casefold() in ("материал", "материалы", "роль"):
            continue
        if colon and (label_clean.casefold() == "ожидаемый ответ" or
                      re.match(r"^(?:руководитель|владелец|заказчик|согласующий)\b", label_clean, re.IGNORECASE)):
            candidate = detail_clean
        else:
            candidate = value
        visible = re.sub(MD_LINK, r"\1", candidate).strip()
        if not visible:
            continue
        for name, number in CHECKPOINT_MATERIAL.findall(visible):
            num = name[:2] if name else number
            if num in by_doc:
                by_doc[num].append(visible)
    return by_doc


def c3_registered_rules(block: str, renderer: Renderer) -> str:
    main_rules = []
    other_rules = []
    mentioned = set(ID.findall(block))
    for oid, experiment in renderer.objects.items():
        if not oid.startswith("EXP-") or experiment.get("status", "active") != "active":
            continue
        rule = experiment.get("stop_go")
        if isinstance(rule, str) and rule.strip():
            dest = main_rules if oid in mentioned else other_rules
            dest.append("<li>" + renderer.plain(f"{oid} — {rule.strip()}") + "</li>")
    html = ""
    if main_rules:
        html = '<p><strong>Условия stop/go из реестра</strong></p><ul>' + "".join(main_rules) + "</ul>"
    if other_rules:
        html += (f"<details><summary>Другие действующие условия stop/go ({len(other_rules)})</summary>"
                 "<ul>" + "".join(other_rules) + "</ul></details>")
    return html


def c3_executive_summary(progress: str, step: str, approval: str, docs: list, renderer: Renderer) -> list[str]:
    if active_checkpoint(step) != "C3":
        return []
    doc_by_num = {}
    for anchor, doc_title, value, name in docs:
        doc_by_num.setdefault(name[:2], (anchor, doc_title, value, name))
    doc_actions = c3_doc_actions(progress)
    if approval == "stale":
        card_status = "На повторном согласовании C3 (прежняя версия пакета устарела; проектный текст не является утверждением)."
    elif approval == "invalid":
        card_status = "Требует проверки и явного решения C3 (запись версии C3 недействительна; проектный текст не является утверждением)."
    else:
        card_status = "Ожидает решения руководителя на C3 (проектный текст не является утверждением)."

    out = ['<div id="c3-executive-summary"><h3>Сводка для решения на C3</h3>']
    for num, heading, default_action in C3_SUMMARY_DOCS:
        doc = doc_by_num.get(num)
        actions = doc_actions[num]
        action_html = "<br>".join(renderer.inline(v, links=False, references=False) for v in dict.fromkeys(actions))
        if doc is None:
            out.append(
                f'<article class="summary-card c3-answer" data-doc="{num}">'
                f'<h4>{escape(heading)}</h4>'
                f'<p>Пробел: отсутствует документ «{escape(DOC_TITLES[num])}».</p>'
                '<p class="c3-action"><strong>Ожидаемое действие человека:</strong> '
                'Запросить документ до согласования.</p>'
                '</article>'
            )
            continue
        anchor, _, value, name = doc
        blocks = [b.strip() for b in main_blocks(value) if b.strip()]
        if not blocks:
            body_html = f'<p>Пробел: в <code>{escape(name)}</code> не заполнен раздел «Главное» для первого экрана C3.</p>'
            status_text = "Пробел — краткий ответ в «Главном» отсутствует; проектный текст не является утверждением."
            fallback_action = f"Открыть раздел {num} и дополнить вывод «Главного» или вернуть правки до решения на C3."
            action_html = ""
        else:
            body_html = renderer.markdown(blocks[0])
            if num == "07":
                body_html += c3_registered_rules(blocks[0], renderer)
            status_text = card_status
            fallback_action = default_action
        out.append(
            f'<article class="summary-card c3-answer" data-doc="{num}">'
            f'<h4><a href="#{anchor}">{escape(heading)}</a></h4>'
            + body_html
            + f'<p class="c3-status"><strong>Статус:</strong> {escape(status_text)}</p>'
            + '<p class="c3-action"><strong>Ожидаемое действие человека:</strong> '
            + (action_html or escape(fallback_action)) + '</p>'
            + '</article>'
        )
    return out + ['</div>']


def selected_decisions(objects: dict) -> list:
    # Несколько самостоятельных решений показываем явно, не выбираем по номеру ID.
    return sorted((obj for oid, obj in objects.items()
                   if oid.startswith("DEC-") and obj.get("state") == "decided"
                   and obj.get("chosen") and obj.get("status") not in ("superseded", "retracted")),
                  key=lambda obj: (text(obj.get("decided_at")), obj["id"]), reverse=True)


def short_rationale(value: str) -> str:
    """Дословный начальный фрагмент основания, без генерации объяснения."""
    value = " ".join(value.split())
    sentences = re.split(r"(?<=[.!?])\s+", value)
    brief = " ".join(sentences[:2])
    if len(brief) > 420:
        brief = brief[:420].rsplit(" ", 1)[0]
    return brief + (" …" if len(brief) < len(value) else "")


def latest_cost(value: str) -> str:
    """Последний датированный расчёт; бездатный итог не заслоняет новое окно.

    cost.py записывает заголовок окна и время формирования. Сравниваем время
    формирования (если есть), иначе даты окна; при равенстве — последний блок.
    """
    lines = value.splitlines()
    starts = [i for i, _, title in headings(value) if title.startswith("Стоимость:")]
    if not starts:
        return value.strip()
    if starts[0]:
        starts.insert(0, 0)
    candidates = []
    for pos, start in enumerate(starts):
        end = starts[pos + 1] if pos + 1 < len(starts) else len(lines)
        block = "\n".join(lines[start:end]).strip()
        generated = re.search(r"Сформировано\s+(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:\d{2})?)", block)
        dates = re.findall(r"\d{4}-\d{2}-\d{2}(?:T[\d:.+Z-]+)?", lines[start])
        stamp = generated[1] if generated else (max(dates) if dates else "")
        # ISO timestamps in supported reports; normalize offsets for ordering.
        try:
            rank = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
            if rank.tzinfo is None:
                rank = rank.replace(tzinfo=datetime.now().astimezone().tzinfo)
            timestamp = rank.timestamp()
        except ValueError:
            timestamp = float("-inf")
        candidates.append((bool(stamp), timestamp, pos, block))
    return max(candidates)[3]


def cost_summary(value: str) -> str:
    """Окно, итог и все текстовые оговорки выбранного расчёта, без таблицы токенов."""
    lines = [line.lstrip("# ") for line in value.splitlines()
             if line.strip() and not line.lstrip().startswith("|")
             and not line.lstrip("_* ").startswith("Сформировано")]
    if not any("Итого" in line or "итог" in line.lower() for line in lines):
        lines.append("Денежный итог неизвестен: сумма в расчёте не записана.")
    return "\n\n".join(lines)


CSS = """
:root { color-scheme: light dark; --bg:#f5f6f8; --paper:#fff; --ink:#17232f; --muted:#536271; --line:#d7dee5; --accent:#00684c; }
@media (prefers-color-scheme: dark) { :root { --bg:#12191f; --paper:#1c262f; --ink:#e7edf3; --muted:#b0becb; --line:#42515e; --accent:#75d9b4; } }
* { box-sizing:border-box; } body { margin:0; background:var(--bg); color:var(--ink); font:17px/1.65 system-ui,sans-serif; overflow-wrap:anywhere; }
main { max-width:1120px; padding:36px 28px; margin:auto; min-width:0; } header { margin-bottom:32px; } h1,h2,h3,h4 { line-height:1.25; } h1 { font-size:1.9rem; } h2 { font-size:1.5rem; } h3 { font-size:1.2rem; }
a { color:var(--accent); text-underline-offset:3px; } a:focus-visible { outline:3px solid var(--accent); outline-offset:3px; }
section,nav { background:var(--paper); padding:28px; margin:24px 0; border:1px solid var(--line); border-radius:12px; min-width:0; }
.meta,.document-label { color:var(--muted); font-size:.9rem; } .summary-card { border-top:1px solid var(--line); padding:12px 0; } .summary-card:last-child { padding-bottom:0; }
.current-links { display:flex; flex-wrap:wrap; column-gap:20px; row-gap:6px; padding:0; list-style:none; } .next-action { border-inline-start:3px solid var(--accent); padding-inline-start:12px; }
summary { cursor:pointer; } summary:focus-visible { outline:3px solid var(--accent); outline-offset:3px; } summary h2,summary h3 { display:inline; }
.ref { font-size:.9em; white-space:nowrap; } .missing { color:#c34f37; text-decoration-style:wavy; } .obsolete,.obsolete .ref { text-decoration:line-through; }
.table-scroll { max-width:100%; overflow-x:auto; margin:18px 0; } table { border-collapse:collapse; width:100%; font-size:.9rem; } th,td { padding:10px 12px; border:1px solid var(--line); text-align:left; vertical-align:top; min-width:100px; } th { background:var(--bg); }
pre { overflow-x:auto; padding:16px; background:var(--bg); border:1px solid var(--line); } code { font-size:.9em; } p { white-space:pre-line; } .registry-row { padding:18px 0; border-top:1px solid var(--line); } .registry-row p { margin:6px 0; } :target { outline:2px solid var(--accent); outline-offset:4px; }
@media (max-width:600px) { main { padding:18px 10px; } section,nav { padding:18px 14px; } body { font-size:16px; } h1 { font-size:1.5rem; } }
@media print { :root { --bg:white; --paper:white; --ink:black; --muted:#333; --line:#aaa; } body { font-size:10pt; } main { max-width:none; padding:0; } section,nav { border:0; padding:0; border-radius:0; } #current-state { break-after:page; } a { color:black !important; } .table-scroll,pre { overflow:visible; } table { page-break-inside:auto; table-layout:fixed; } tr { page-break-inside:avoid; } th,td { min-width:0; overflow-wrap:anywhere; } pre { white-space:pre-wrap; } h1,h2,h3,h4 { page-break-after:avoid; } }
"""


def render(project: Path, out: Path) -> str:
    if not (project / "outputs").is_dir() or not (project / "state/ledger").is_dir():
        raise ValueError("нет папки outputs/ или реестра state/ledger/")
    ledger = Ledger(project)
    if ledger.load_problems:
        raise ValueError("реестр не читается: " + "; ".join(f"{p}: {m}" for p, m in ledger.load_problems))
    renderer = Renderer(ledger.objects)
    docs = []
    for path in sorted((project / "outputs").glob("0*.md")):
        value = path.read_text(encoding="utf-8")
        number = path.name[:2]
        title = next((t for _, _, t in headings(value)), path.stem)
        if title.strip().strip("*").strip() == "Главное":
            title = DOC_TITLES.get(number, path.stem)
        docs.append(("doc-" + number, title, value, path.name))
    progress = project / "state/progress.md"
    title = ""
    progress_text = progress.read_text(encoding="utf-8") if progress.is_file() else ""
    if progress_text:
        title = next((t for _, _, t in headings(progress_text)), "")
        title = re.sub(r"^Прогресс:\s*", "", title)
    if not title:
        title = next((t for _, t, _, name in docs if name.startswith("01")), project.name)
    page = ['<!doctype html><html lang="ru"><head><meta charset="utf-8">',
            '<meta name="viewport" content="width=device-width, initial-scale=1">',
            "<title>" + escape(title) + "</title><style>" + CSS + "</style></head><body><main>",
            "<header><h1>" + renderer.inline(title) + "</h1>",
            GENERATED_AT_PREFIX + datetime.now().astimezone().isoformat(timespec="seconds") + "</p>"]
    page.append('</header><section id="current-state"><h2>Текущее состояние</h2>')
    step = current_step(progress_text)
    page.append('<p><strong>Текущий шаг:</strong> ' + renderer.inline(step) + '</p>')
    approval, message, changed = approval_status(ledger)
    if approval != "unrecorded":
        page.append('<p><strong>Версия C3:</strong> ' + escape(message) + '</p>')
    if changed:
        page.append('<p><strong>Согласованные основания изменились:</strong> ' +
                    renderer.plain(", ".join(changed)) + '</p>')
    action = next_action(progress_text)
    if approval in ("stale", "invalid") and active_checkpoint(step) not in ("C1", "C2"):
        action = "Проверить актуальный пакет и получить явное утверждение руководителя на повторном C3."
    if action:
        page.append('<p class="next-action"><strong>Ближайшее действие</strong><br>' +
                    renderer.inline(action, links=False) + '</p>')
    page.extend(checkpoint_block(progress_text, step, docs, renderer))
    decisions = selected_decisions(ledger.objects)
    if not decisions:
        page.append('<p>Выбранный вариант неизвестен: действующее решение с chosen не записано.</p>')
    for decision in decisions:
        chosen = text(decision["chosen"])
        alternative = ledger.objects.get(chosen, {})
        label = chosen + (" — " + text(alternative["title"]) if alternative.get("title") else "")
        page.append('<p><strong>Выбранный вариант:</strong> ' + renderer.inline(label) +
                    ' (' + renderer.plain(decision["id"]) + ')</p>')
        rationale = short_rationale(text(decision.get("rationale")))
        page.append('<p><strong>Основание (из решения):</strong> ' +
                    renderer.inline(rationale or "Неизвестно: основание не записано.") + '</p>')
    page.extend(c3_executive_summary(progress_text, step, approval, docs, renderer))
    present_docs = {name[:2]: anchor for anchor, _, _, name in docs}
    links = [f'<li><a href="#{present_docs[number]}">{DOC_TITLES[number]}</a></li>'
             for number in ("08", "05", "06", "07") if number in present_docs]
    if links:
        page.append('<ul class="current-links">' + "".join(links) + '</ul>')
    cost = project / "state/cost_report.md"
    cost_text = latest_cost(cost.read_text(encoding="utf-8")) if cost.is_file() else ""
    page.append('<details id="cost-summary"><summary><h3>Стоимость прогона</h3></summary>' +
                renderer.markdown(cost_summary(cost_text) if cost_text else
                                  "Денежный итог неизвестен: state/cost_report.md отсутствует или пуст.") + '</details>')
    page.append('</section><section id="main-points"><details><summary><h2>Главное по документам</h2></summary>')
    for anchor, label, value, name in docs:
        for block in main_blocks(value):
            page.append('<article class="summary-card"><h3><a href="#' + anchor + '">' +
                        escape(name + " — " + label) + "</a></h3>" + renderer.markdown(block) + "</article>")
    page.append('</details></section><nav aria-label="Оглавление"><h2>Оглавление</h2><ol>')
    for anchor, label, _, name in docs:
        page.append('<li><a href="#' + anchor + '">' + escape(name + " — " + label) + "</a></li>")
    page.append('<li><a href="#registry">Реестр</a></li></ol></nav>')
    for anchor, _, value, name in docs:
        page.append(f'<section id="{anchor}"><p class="document-label">{escape(name)}</p>' + renderer.markdown(value) + "</section>")
    if cost_text:
        page.append('<section id="cost-details"><h2>Расчёт стоимости выбранного периода</h2>' +
                    renderer.markdown(cost_text) + '</section>')
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
                    url = Path(os.path.relpath(project / url, out.parent)).as_posix()
                page.append("<p>Источник: " + (f'<a href="{escape(url, quote=True)}">{escape(url)}</a>' if safe_url(url) else escape(url)) + "</p>")
            page.append("<p>" + escape(" · ".join(f"{k}: {text(obj.get(k))}" for k in ("kind", "access", "retrieved"))) + "</p>")
        page.append("</article>")
    page.append("</section></main></body></html>")
    return "\n".join(page) + "\n"


def generate(project: Path, out: Path) -> None:
    html = render(project, out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")


def same_report(saved: bytes, rendered: str) -> bool:
    before, marker, metadata_and_body = rendered.partition("\n" + GENERATED_AT_PREFIX)
    _, closing_tag, after = metadata_and_body.partition("</p>")
    # Сравниваем всё до и после даты, включая разметку и остальные метаданные.
    # Один и тот же текст может быть сохранён как LF или CRLF.
    saved = saved.replace(b"\r\n", b"\n")
    prefix = (before + marker).encode("utf-8")
    suffix = (closing_tag + after).encode("utf-8")
    if not saved.startswith(prefix) or not saved.endswith(suffix):
        return False
    try:
        stamp = saved[len(prefix):-len(suffix)].decode("ascii")
        parsed = datetime.fromisoformat(stamp)
    except ValueError:
        return False
    return parsed.tzinfo is not None and parsed.isoformat(timespec="seconds") == stamp


def check(project: Path, out: Path) -> int:
    rendered = render(project, out)
    try:
        saved = out.read_bytes()
    except FileNotFoundError:
        print(f"Отчёт отсутствует: {out}", file=sys.stderr)
    else:
        if same_report(saved, rendered):
            print(f"Отчёт актуален: {out}")
            return 0
        print(f"Отчёт устарел: {out}", file=sys.stderr)
    return 1


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("project", type=Path)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--check", action="store_true", help="проверить актуальность HTML без записи")
    args = parser.parse_args(argv)
    out = args.out or args.project / "outputs/report.html"
    if args.check:
        try:
            status = check(args.project, out)
        except Exception as exc:
            print(f"Не удалось проверить отчёт {out}: {exc}", file=sys.stderr)
            status = 2
        if status:
            command = [sys.executable, __file__]
            if args.out is not None:
                command.append(f"--out={out}")
            command.extend(["--", str(args.project)])
            action = "Пересобрать" if status == 1 else "Пересобрать после устранения ошибки"
            print(f"{action}: {shlex.join(command)}", file=sys.stderr)
        return status
    try:
        generate(args.project, out)
    except (OSError, ValueError) as exc:
        print(f"Не удалось собрать отчёт: {exc}", file=sys.stderr)
        return 2
    print(f"Отчёт: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

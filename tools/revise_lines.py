#!/usr/bin/env python3
"""Правка устаревших строк после пересмотра — одной командой и дешёвой моделью, а не основной.

    python tools/revise_lines.py projects/<case> --change CHG-NNN --step 8 [--no-apply] [--tier T2]

Зачем: при пересмотре основная модель тратит деньги на перечитывание больших документов (замер: 38 ходов
по ~100K токенов, $0,81). Здесь всё механическое делает код, а суждение по каждой строке — модель уровня T2:
  1. `verify.py --change` находит строки, которые ссылаются на изменённые по событию объекты и не обновлены;
  2. собирается короткое задание: только эти строки + текущие формулировки изменённых объектов
     (statement, confidence, status_note) + описание события — без целых документов;
  3. `delegate.py --task revise-lines` (T2, только чтение) возвращает JSON: для каждой строки rewrite или mark;
  4. код применяет: строка должна совпасть с исходной (иначе пропуск), ID из исходной строки сохраняются,
     у таблицы сохраняется число ячеек; в конце — «(пересмотрено по CHG)» или «(проверено по CHG)»;
  5. повторный `verify --change`: что осталось — список для основной модели.
Основная модель потом смотрит только `git diff` и остаток списка, а не документы целиком.
Код выхода: 0 — список пуст; 1 — что-то осталось (см. вывод); 3 — делегировать некому (делай сам по списку).
"""
from __future__ import annotations

import argparse
import datetime
import json
import re
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ledger as L  # noqa: E402
import verify as V  # noqa: E402

TASK = """Ты правишь строки документов стратегии после пересмотра по событию {change}.
Событие: {summary}

Изменённые объекты реестра (актуальные формулировки — опирайся только на них):
{objects}

Ниже строки документов, которые ссылаются на эти объекты и ещё не обновлены. Для каждой реши:
- "mark" — смысл строки по-прежнему верен (она не противоречит актуальным формулировкам);
- "rewrite" — строка теперь неточна: перепиши её коротко, сохранив стиль, разметку (таблица — те же ячейки,
  список — тот же маркер) и все активные ID в квадратных скобках; если объект в статусе superseded → NEW-ID,
  замени [OLD-ID] на [NEW-ID]; не добавляй сведений, которых нет в объектах выше.
Ответ — только JSON-массив, по одному элементу на строку:
[{{"key": "<key>", "action": "mark" | "rewrite", "text": "<новая строка целиком, только для rewrite>"}}]

Строки:
{lines}
"""


def _superseded_target(ledger: L.Ledger, oid: str) -> Optional[str]:
    """Конечный активный ID замены для superseded-объекта (или None при обрыве/цикле)."""
    cur, seen = oid, {oid}
    while True:
        o = ledger.objects.get(cur)
        if not o:
            return None
        if cur != oid and o.get("status", "active") == "active":
            return cur
        nxt = o.get("superseded_by")
        if not nxt or nxt in seen:
            return None
        seen.add(nxt)
        cur = nxt


def object_summary(o: dict) -> str:
    conf = o.get("confidence") or {}
    st = o.get("status", "active")
    text = (o.get("statement") or o.get("title") or o.get("summary")
            or o.get("question") or o.get("message") or o.get("expected_change") or "")
    parts = [text,
             (f"статус: {st} → {o['superseded_by']}" if o.get("superseded_by") else f"статус: {st}") if st != "active" else "",
             f"уверенность: {conf.get('level', '')} — {conf.get('rationale', '')}" if conf else "",
             f"вердикт: {o.get('verdict')}" if o.get("verdict") else "",
             f"заметка: {o.get('status_note')}" if o.get("status_note") else ""]
    if o.get("scores"):
        parts.append("баллы: " + ", ".join(f"{k} {v.get('value') if isinstance(v, dict) else v}"
                                           for k, v in o["scores"].items()))
    return "; ".join(p for p in parts if p)


def build_task(ledger: L.Ledger, change: str, stale: List[dict]) -> Tuple[str, Dict[str, dict]]:
    refs = {r for i in stale for r in i["refs"]}
    for r in list(refs):
        if r in ledger.objects and ledger.objects[r].get("status", "active") == "superseded":
            tgt = _superseded_target(ledger, r)
            if tgt:
                refs.add(tgt)
    objs = "\n".join(f"- {r}: {object_summary(ledger.objects[r])}" for r in sorted(refs) if r in ledger.objects)
    files_by_pfx: Dict[str, set] = {}
    for i in stale:
        files_by_pfx.setdefault(i["file"][:2], set()).add(i["file"])
    keyed = {
        f"{(i['file'][:2] if len(files_by_pfx.get(i['file'][:2], ())) <= 1 else i['file'])}:{i['line']}": i
        for i in stale
    }
    lines = "\n".join(f"[{k}] {i['raw_text']}" for k, i in keyed.items())
    chg = ledger.objects.get(change, {})
    return TASK.format(change=change, summary=chg.get("summary", ""), objects=objs, lines=lines), keyed


def parse_proposals(text: str) -> List[dict]:
    t = text.strip()
    if "```" in t:
        t = t.split("```")[1].removeprefix("json").strip()
    s, e = t.find("["), t.rfind("]")
    if s < 0 or e < 0:
        return []
    try:
        data = json.loads(t[s:e + 1])
    except ValueError:
        return []
    return [d for d in data if isinstance(d, dict) and d.get("key") and d.get("action") in ("mark", "rewrite")]


def _with_mark(line: str, mark: str) -> str:
    """Дописать пометку в конец строки; у строки таблицы — внутрь последней ячейки."""
    s = line.rstrip()
    if s.startswith("|") and s.endswith("|"):
        return s[:-1].rstrip() + f" {mark} |"
    return f"{s} {mark}"


def _ids_valid_after_rewrite(old: str, cand: str, ledger: Optional[L.Ledger]) -> bool:
    keep_ids = set(V.expand_refs(V.normalize_short_lists(old)))
    cand_ids = set(V.expand_refs(V.normalize_short_lists(cand)))
    if ledger is None:
        return keep_ids == cand_ids
    allowed = set()
    for oid in keep_ids:
        obj = ledger.objects.get(oid)
        if obj is None:
            return False
        status = obj.get("status", "active")
        if status == "active":
            allowed.add(oid)
        elif status == "superseded":
            replacement = _superseded_target(ledger, oid)
            if replacement is None:
                return False
            allowed.add(replacement)
        else:
            return False
    return cand_ids == allowed


def apply(project: Path, change: str, keyed: Dict[str, dict], proposals: List[dict],
          ledger: Optional[L.Ledger] = None) -> Dict[str, int]:
    stats = {"rewrite": 0, "mark": 0, "skipped": 0}
    if ledger is None and (project / "state" / "ledger").is_dir():
        ledger = L.Ledger(project)
    by_file: Dict[str, Dict[int, dict]] = {}
    for p in proposals:
        item = keyed.get(p["key"])
        if item:
            by_file.setdefault(item["file"], {})[item["line"]] = dict(p, item=item)
    for fname, edits in by_file.items():
        f = project / "outputs" / fname
        lines = f.read_text(encoding="utf-8").split("\n")
        for ln, p in edits.items():
            if not 1 <= ln <= len(lines):
                stats["skipped"] += 1
                continue
            old = lines[ln - 1]
            if old != p["item"]["raw_text"]:
                stats["skipped"] += 1  # строка изменилась после проверки — не трогаем
                continue
            new = None
            if p["action"] == "rewrite" and isinstance(p.get("text"), str):
                cand = p["text"].strip()
                # A delegate may already include the receipt for the current change.
                evidence = V.REVISION_MARK.sub(
                    lambda m: "" if m.group(1) == change else m.group(0), cand)
                original = V.REVISION_MARK.sub(
                    lambda m: "" if m.group(1) == change else m.group(0), old)
                ok_ids = _ids_valid_after_rewrite(original, evidence, ledger)
                ok_table = (not old.strip().startswith("|")) or (
                    cand.startswith("|") and cand.endswith("|")
                    and cand.count("|") == old.strip().count("|"))
                if cand and len(cand.splitlines()) == 1 and ok_ids and ok_table:
                    indent = old[:len(old) - len(old.lstrip())]
                    line_txt = indent + cand
                    new = line_txt if V.marked_for_change(line_txt, change) else _with_mark(
                        line_txt, f"(пересмотрено по {change})")
                    stats["rewrite"] += 1
            elif p["action"] == "mark":
                old_ids = set(V.expand_refs(V.normalize_short_lists(old)))
                has_nonactive = ledger is not None and any(
                    oid not in ledger.objects or ledger.objects[oid].get("status", "active") != "active"
                    for oid in old_ids)
                if not has_nonactive:
                    new = old if V.marked_for_change(old, change) else _with_mark(old, f"(проверено по {change})")
                    stats["mark"] += 1
            if new is None:
                stats["skipped"] += 1
                continue
            lines[ln - 1] = new
        L.atomic_write_text(f, "\n".join(lines))
    return stats


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Правка устаревших строк после пересмотра дешёвой моделью")
    ap.add_argument("project", type=Path)
    ap.add_argument("--change", required=True)
    ap.add_argument("--step", default="-")
    ap.add_argument("--files", nargs="*", default=[])
    ap.add_argument("--tier", default=None, help="уровень модели (по умолчанию из routing.yaml: revise-lines)")
    ap.add_argument("--no-apply", action="store_true", help="только предложения, файлы не менять")
    args = ap.parse_args(argv)
    project = args.project.resolve()
    ledger = L.Ledger(project)
    if args.change not in ledger.objects:
        print(f"Нет события {args.change}", file=sys.stderr)
        return 2
    items = V.verify(project, args.files, args.change)["items"]
    broken = [i for i in items if i["verdict"] == "wrong-ref"]
    if broken:
        for item in broken:
            print(f"{item['file']}:{item['line']} — {item['note']}", file=sys.stderr)
        return 1
    stale = [i for i in items if i["verdict"] == "stale?"]
    if not stale:
        print(f"Необновлённых строк по {args.change} нет.")
        return 0
    task, keyed = build_task(ledger, args.change, stale)
    work = project / "state" / "delegated"
    work.mkdir(parents=True, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    qfile = work / f"{stamp}_revise_{args.change}_task.md"
    qfile.write_text(task, encoding="utf-8")
    print(f"Строк на правку: {len(stale)}; задание {len(task)} символов (вместо целых документов) → {qfile.name}")
    import delegate as D
    before = set(work.glob("*_revise-lines.md"))
    dargs = ["--project", str(project), "--prompt-file", str(qfile), "--task", "revise-lines", "--step", str(args.step)]
    if args.tier:
        dargs += ["--tier", args.tier]
    code = D.main(dargs)
    if code != 0:
        print(f"Делегирование не удалось (код {code}) — исправь строки сам по списку `verify.py --change {args.change}`.")
        return 3 if code == 3 else 1
    new = sorted(set(work.glob("*_revise-lines.md")) - before)
    proposals = parse_proposals(new[-1].read_text(encoding="utf-8")) if new else []
    (work / f"{stamp}_revise_{args.change}_proposals.json").write_text(
        json.dumps(proposals, ensure_ascii=False, indent=1), encoding="utf-8")
    if not proposals:
        print("Модель не вернула JSON с правками — исправь строки сам по списку.")
        return 1
    if args.no_apply:
        print(f"Предложений: {len(proposals)} (не применены, --no-apply).")
        return 0
    stats = apply(project, args.change, keyed, proposals, ledger=ledger)
    left = [i for i in V.verify(project, args.files, args.change)["items"]
            if i["verdict"] in ("stale?", "wrong-ref")]
    L.log_or_defer(project, args.step, f"правка строк по {args.change}", "T2", "revise_lines.py",
                   f"переписано {stats['rewrite']}, помечено {stats['mark']}, пропущено {stats['skipped']}, осталось {len(left)}",
                   "см. строку делегирования выше")
    print(f"Переписано {stats['rewrite']}, помечено «проверено» {stats['mark']}, пропущено {stats['skipped']}; "
          f"осталось {len(left)}. Проверь `git diff -- projects/<case>/outputs` и исправь остаток сам.")
    for i in left[:20]:
        print(f"- {i['file']}:{i['line']} {i['text'][:160]}")
    return 0 if not left else 1


def _utf8_stdio() -> None:
    """Windows: консоль и пайпы по умолчанию в cp1251 — кириллица и знаки вроде «↺» роняют вывод."""
    for s in (sys.stdin, sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


if __name__ == "__main__":
    _utf8_stdio()
    sys.exit(main())

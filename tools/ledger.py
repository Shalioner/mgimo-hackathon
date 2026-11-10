#!/usr/bin/env python3
"""Детерминированные операции над реестром решения (Evidence Ledger + Decision Memory).

Использование (из корня репозитория):
    python tools/ledger.py validate projects/<case>     # схемы, ссылки, правила доказательности, цитаты
    python tools/ledger.py impact   projects/<case> CHG-001   # что затронуто новыми сведениями
    python tools/ledger.py impact   projects/<case> SRC-004 CLM-002
    python tools/ledger.py trace    projects/<case> DEC-001   # на чём держится объект, вплоть до источников
    python tools/ledger.py stats    projects/<case>     # сводка для отчёта

Код намеренно не вызывает LLM: проверка и поиск затронутых выводов должны быть
воспроизводимыми. LLM-агент читает вывод и обновляет формулировки сам.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

import yaml
from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parent.parent
SCHEMA_PATH = ROOT / "schemas" / "ledger.schema.json"

# префикс ID -> (файл в state/ledger, определение в схеме, человекочитаемое имя)
TYPES: Dict[str, Tuple[str, str, str]] = {
    "SRC": ("sources.yaml", "source", "Источники"),
    "CLM": ("claims.yaml", "claim", "Утверждения"),
    "HYP": ("hypotheses.yaml", "hypothesis", "Гипотезы и допущения"),
    "PAIN": ("pains.yaml", "pain", "Боли клиентов"),
    "ALT": ("alternatives.yaml", "alternative", "Варианты"),
    "DEC": ("decisions.yaml", "decision", "Решения"),
    "PRM": ("promises.yaml", "promise", "Коммуникационные обещания"),
    "EXP": ("experiments.yaml", "experiment", "Эксперименты"),
    "CHG": ("changes.yaml", "change_event", "Новые сведения"),
}
# порядок слоёв цепочки — для вывода impact/trace
LAYER_ORDER = ["SRC", "CLM", "HYP", "PAIN", "ALT", "DEC", "PRM", "EXP", "CHG"]
TEXT_SUFFIXES = {".md", ".txt", ".csv", ".tsv", ".json", ".yaml", ".yml", ".html"}


class _Loader(yaml.SafeLoader):
    """SafeLoader, который оставляет даты строками (2026-10-01 → "2026-10-01")."""


_Loader.yaml_implicit_resolvers = {
    k: [(tag, rx) for tag, rx in v if tag != "tag:yaml.org,2002:timestamp"]
    for k, v in yaml.SafeLoader.yaml_implicit_resolvers.items()
}


def prefix(obj_id: str) -> str:
    return obj_id.split("-", 1)[0]


@dataclass
class Report:
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    def err(self, where: str, msg: str) -> None:
        self.errors.append(f"[ОШИБКА] {where}: {msg}")

    def warn(self, where: str, msg: str) -> None:
        self.warnings.append(f"[ВНИМАНИЕ] {where}: {msg}")


class Ledger:
    def __init__(self, project: Path):
        self.project = project
        self.dir = project / "state" / "ledger"
        self.objects: Dict[str, dict] = {}
        self.origin: Dict[str, str] = {}  # id -> файл
        self.load_problems: List[Tuple[str, str]] = []
        self._load()

    def _load(self) -> None:
        if not self.dir.is_dir():
            self.load_problems.append((str(self.dir), "нет папки state/ledger"))
            return
        for pfx, (fname, _, _) in TYPES.items():
            path = self.dir / fname
            if not path.exists():
                continue
            try:
                data = yaml.load(path.read_text(encoding="utf-8"), Loader=_Loader) or []
            except yaml.YAMLError as exc:
                self.load_problems.append((fname, f"YAML не читается: {exc}"))
                continue
            if not isinstance(data, list):
                self.load_problems.append((fname, "файл должен содержать список объектов"))
                continue
            for i, obj in enumerate(data):
                if not isinstance(obj, dict) or "id" not in obj:
                    self.load_problems.append((fname, f"элемент #{i + 1} без поля id"))
                    continue
                oid = str(obj["id"])
                if oid in self.objects:
                    self.load_problems.append((fname, f"повтор ID {oid} (уже есть в {self.origin[oid]})"))
                    continue
                if prefix(oid) != pfx:
                    self.load_problems.append((fname, f"{oid}: префикс не соответствует файлу (ожидается {pfx}-NNN)"))
                self.objects[oid] = obj
                self.origin[oid] = fname

    # ---------- граф ----------
    def upstream(self, oid: str) -> List[Tuple[str, str]]:
        """Ссылки объекта на то, на чём он основан: [(id, роль связи)]."""
        o = self.objects.get(oid, {})
        p = prefix(oid)
        out: List[Tuple[str, str]] = []

        def add(ids: Optional[Iterable[str]], role: str) -> None:
            for x in ids or []:
                out.append((x, role))

        if p == "CLM":
            add([e.get("source") for e in o.get("evidence", []) if e.get("source")], "источник")
            add(o.get("derived_from"), "выведено из")
            add(o.get("contradicted_by"), "противоречит")
        elif p == "HYP":
            add(o.get("based_on"), "основано на")
        elif p == "PAIN":
            for name, crit in (o.get("criteria") or {}).items():
                add((crit or {}).get("basis"), f"критерий {name}")
        elif p == "ALT":
            add(o.get("supported_by"), "довод за")
            for ob in o.get("objections", []) or []:
                add(ob.get("refs"), "возражение")
            for crit, sc in (o.get("scores") or {}).items():
                add((sc or {}).get("refs"), f"оценка «{crit}»")
        elif p == "DEC":
            add(o.get("options"), "вариант")
            add(o.get("based_on"), "основание")
        elif p == "PRM":
            add(o.get("proof"), "подтверждение")
            add([o["decision"]] if o.get("decision") else [], "решение")
        elif p == "EXP":
            add(o.get("tests"), "проверяет")
            add([o["decision"]] if o.get("decision") else [], "решение")
        elif p == "CHG":
            add([o["source"]] if o.get("source") else [], "новый источник")
            add([e.get("target") for e in o.get("effects", []) if e.get("target")], "воздействие")
        # уникальность с сохранением порядка
        seen = set()
        uniq = []
        for x, role in out:
            if (x, role) not in seen:
                seen.add((x, role))
                uniq.append((x, role))
        return uniq

    def downstream_index(self) -> Dict[str, List[Tuple[str, str]]]:
        """Обратный граф: кто опирается на объект. События CHG не считаются зависимыми."""
        idx: Dict[str, List[Tuple[str, str]]] = {}
        for oid in self.objects:
            if prefix(oid) == "CHG":
                continue
            for target, role in self.upstream(oid):
                idx.setdefault(target, []).append((oid, role))
        return idx

    def label(self, oid: str) -> str:
        o = self.objects.get(oid)
        if not o:
            return f"{oid} (не найден)"
        text = o.get("statement") or o.get("title") or o.get("question") or o.get("message") \
            or o.get("expected_change") or o.get("summary") or ""
        text = re.sub(r"\s+", " ", str(text)).strip()
        if len(text) > 90:
            text = text[:87] + "…"
        status = o.get("status", "active")
        mark = "" if status == "active" else f" [{status}]"
        return f"{oid}{mark} — {text}"


# ---------- validate ----------
def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip().lower()


def validate(ledger: Ledger) -> Report:
    rep = Report()
    for where, msg in ledger.load_problems:
        rep.err(where, msg)

    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    validators = {}
    for pfx, (_, ref, _) in TYPES.items():
        sub = {"$schema": schema["$schema"], "$defs": schema["$defs"], "$ref": f"#/$defs/{ref}"}
        validators[pfx] = Draft202012Validator(sub)

    objs = ledger.objects

    # 1. схемы
    for oid, o in objs.items():
        v = validators.get(prefix(oid))
        if v is None:
            rep.err(oid, "неизвестный тип объекта")
            continue
        for e in sorted(v.iter_errors(o), key=lambda e: list(e.absolute_path)):
            loc = "/".join(str(x) for x in e.absolute_path) or "(объект)"
            rep.err(oid, f"схема: {loc}: {e.message}")

    # 2. висячие ссылки и ссылки на неактуальные записи
    for oid, o in objs.items():
        for target, role in ledger.upstream(oid):
            t = objs.get(target)
            if t is None:
                rep.err(oid, f"ссылка «{role}» на несуществующий {target}")
                continue
            if o.get("status", "active") == "active" and t.get("status", "active") != "active" \
                    and prefix(oid) != "CHG":
                rep.warn(oid, f"опирается на {target} со статусом {t.get('status')} — пересмотреть")
        if o.get("status") == "superseded" and not o.get("superseded_by"):
            rep.err(oid, "status=superseded, но не указан superseded_by")

    # 3. источники: открытость, наличие файла, контрольная сумма
    for oid, o in objs.items():
        if prefix(oid) != "SRC":
            continue
        rel = o.get("path")
        if rel:
            fpath = (ledger.project / rel)
            if not fpath.exists():
                rep.err(oid, f"файл источника не найден: {rel}")
            elif o.get("sha256"):
                actual = hashlib.sha256(fpath.read_bytes()).hexdigest()
                if actual != o["sha256"]:
                    rep.err(oid, f"sha256 не совпадает с файлом {rel} — источник изменился, нужен CHG")

    # 4. правила доказательности для утверждений
    for oid, o in objs.items():
        if prefix(oid) != "CLM":
            continue
        ctype = o.get("type")
        ev = o.get("evidence") or []
        if ctype == "fact" and not ev:
            rep.err(oid, "факт без evidence: укажите источник и цитату/локатор или смените тип")
        if ctype in ("forecast", "interpretation") and not ev and not o.get("derived_from"):
            rep.err(oid, f"{ctype} без оснований: нужен evidence или derived_from")
        for e in ev:
            src = objs.get(e.get("source", ""))
            if not src:
                continue
            if ctype == "fact" and src.get("kind") == "synthetic_interview":
                rep.err(oid, f"факт опирается на синтетическое интервью {src['id']}: "
                             "синтетические ответы не доказывают спрос — используйте тип interpretation/гипотезу")
            quote = e.get("quote")
            rel = src.get("path")
            if quote and rel:
                fpath = ledger.project / rel
                if fpath.exists() and fpath.suffix.lower() in TEXT_SUFFIXES:
                    text = fpath.read_text(encoding="utf-8", errors="replace")
                    if _norm(quote) not in _norm(text):
                        rep.err(oid, f"цитата не найдена в {src['id']} ({rel}): «{quote[:60]}»")
        for c in o.get("contradicted_by") or []:
            if c == oid:
                rep.err(oid, "утверждение противоречит само себе")

    # 5. решения
    for oid, o in objs.items():
        if prefix(oid) != "DEC":
            continue
        options = o.get("options") or []
        kinds = set()
        for opt in options:
            if prefix(opt) != "ALT":
                rep.err(oid, f"в options должен быть ALT, а не {opt}")
                continue
            if opt in objs:
                kinds.add(objs[opt].get("kind"))
        if "status_quo" not in kinds:
            rep.err(oid, "среди вариантов нет «оставить как есть» (kind: status_quo)")
        if "kill" not in kinds:
            rep.warn(oid, "среди вариантов нет «закрыть направление» (kind: kill) — объясните, почему он неприменим")
        for f in ("recommended", "chosen"):
            if o.get(f) and o[f] not in options:
                rep.err(oid, f"{f}={o[f]} не входит в options")
        if o.get("state") == "decided":
            for f in ("chosen", "decided_by", "decided_at", "rationale", "review_triggers"):
                if not o.get(f):
                    rep.err(oid, f"решение принято (decided), но не заполнено поле {f}")
            if o.get("decided_by", "").strip().lower() in ("agent", "агент", "ai", "llm"):
                rep.err(oid, "финальный выбор делает человек: decided_by не может быть агентом")
        for b in o.get("based_on") or []:
            t = objs.get(b)
            if t and prefix(b) == "CLM" and (t.get("confidence") or {}).get("level") == "low":
                rep.warn(oid, f"основание {b} имеет низкую уверенность")

    # 6. обещания соответствуют продукту и решению
    for oid, o in objs.items():
        if prefix(oid) != "PRM":
            continue
        dec = objs.get(o.get("decision", ""))
        if dec and dec.get("state") != "decided":
            rep.warn(oid, f"обещание привязано к решению {dec['id']}, которое ещё не принято")
        if not o.get("proof"):
            rep.warn(oid, "у обещания нет подтверждения (proof) — чем аудитория убедится, что это правда?")

    # 7. ключевые гипотезы должны проверяться
    tested = {t for oid, o in objs.items() if prefix(oid) == "EXP" for t in (o.get("tests") or [])}
    for oid, o in objs.items():
        if prefix(oid) == "HYP" and o.get("would_change_decision") and o.get("verdict", "open") == "open" \
                and o.get("status", "active") == "active" and oid not in tested:
            rep.warn(oid, "гипотеза может изменить решение, но ни один эксперимент её не проверяет")

    # 8. необработанные новые сведения
    for oid, o in objs.items():
        if prefix(oid) == "CHG" and not o.get("processed"):
            rep.warn(oid, f"новые сведения не обработаны — запустите: ledger.py impact {ledger.project} {oid}")

    return rep


# ---------- impact ----------
def impact(ledger: Ledger, starts: List[str]) -> Dict[str, Tuple[int, List[str]]]:
    """BFS по обратному графу. Возвращает {id: (глубина, путь от стартового узла)}."""
    seeds: List[str] = []
    for s in starts:
        if prefix(s) == "CHG" and s in ledger.objects:
            for eff in ledger.objects[s].get("effects", []):
                if eff.get("target"):
                    seeds.append(eff["target"])
        else:
            seeds.append(s)
    idx = ledger.downstream_index()
    result: Dict[str, Tuple[int, List[str]]] = {}
    q: deque = deque()
    for s in seeds:
        if s not in result:
            result[s] = (0, [s])
            q.append(s)
    while q:
        cur = q.popleft()
        depth, path = result[cur]
        for nxt, _role in idx.get(cur, []):
            if nxt not in result:
                result[nxt] = (depth + 1, path + [nxt])
                q.append(nxt)
    return result


def cmd_impact(ledger: Ledger, starts: List[str], as_json: bool) -> int:
    missing = [s for s in starts if s not in ledger.objects]
    if missing:
        print(f"Не найдены: {', '.join(missing)}", file=sys.stderr)
        return 2
    res = impact(ledger, starts)
    if as_json:
        print(json.dumps({k: {"depth": d, "path": p} for k, (d, p) in res.items()}, ensure_ascii=False, indent=2))
        return 0
    print(f"# Анализ влияния: {', '.join(starts)}\n")
    for s in starts:
        if prefix(s) == "CHG":
            o = ledger.objects[s]
            print(f"Событие {s} ({o.get('date')}): {o.get('summary', '')}")
            for eff in o.get("effects", []):
                print(f"  - {eff.get('target')}: {eff.get('effect')} — {eff.get('note', '')}")
            print()
    for pfx in LAYER_ORDER:
        items = sorted((k for k in res if prefix(k) == pfx), key=lambda k: (res[k][0], k))
        if not items:
            continue
        print(f"## {TYPES[pfx][2]} ({len(items)})")
        for k in items:
            depth, path = res[k]
            via = "" if depth == 0 else f"   ← {' → '.join(path)}"
            print(f"- {ledger.label(k)}{via}")
        print()
    decs = [k for k in res if prefix(k) == "DEC"]
    if decs:
        print("## Что сделать агенту")
        print("1. Пересмотреть формулировку и confidence затронутых утверждений и гипотез (слой CLM/HYP).")
        print("2. Пересчитать оценки затронутых вариантов (ALT) и проверить, меняется ли ранжирование.")
        print(f"3. Перевести решения {', '.join(sorted(decs))} в state: needs_review и спросить человека.")
        print("4. Проверить затронутые обещания (PRM) и эксперименты (EXP): остаются ли они верными.")
        print("5. Незатронутые объекты не переписывать. Отметить событие processed: true.")
    return 0


# ---------- trace ----------
def cmd_trace(ledger: Ledger, oid: str) -> int:
    if oid not in ledger.objects:
        print(f"Не найден: {oid}", file=sys.stderr)
        return 2
    print(f"# На чём держится {oid}\n")
    seen = set()

    def walk(cur: str, depth: int, role: str) -> None:
        pad = "  " * depth
        rtxt = f"({role}) " if role else ""
        again = cur in seen
        print(f"{pad}- {rtxt}{ledger.label(cur)}{' ↺ (уже показан выше)' if again else ''}")
        if again:
            return
        seen.add(cur)
        for nxt, r in ledger.upstream(cur):
            walk(nxt, depth + 1, r)

    walk(oid, 0, "")
    return 0


# ---------- stats ----------
def cmd_stats(ledger: Ledger) -> int:
    objs = ledger.objects
    print("# Сводка реестра\n")
    for pfx in LAYER_ORDER:
        items = [o for k, o in objs.items() if prefix(k) == pfx]
        if not items:
            continue
        active = sum(1 for o in items if o.get("status", "active") == "active")
        print(f"- {TYPES[pfx][2]}: {len(items)} (активных {active})")
    claims = [o for k, o in objs.items() if prefix(k) == "CLM" and o.get("status", "active") == "active"]
    if claims:
        by_type: Dict[str, int] = {}
        by_conf: Dict[str, int] = {}
        for c in claims:
            by_type[c.get("type", "?")] = by_type.get(c.get("type", "?"), 0) + 1
            lvl = (c.get("confidence") or {}).get("level", "?")
            by_conf[lvl] = by_conf.get(lvl, 0) + 1
        print(f"\nУтверждения по типу: {by_type}")
        print(f"Утверждения по уверенности: {by_conf}")
        contradicted = [c["id"] for c in claims if c.get("contradicted_by")]
        if contradicted:
            print(f"Есть противоречия: {', '.join(contradicted)}")
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Операции над реестром решения")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("validate", help="проверить реестр")
    p.add_argument("project", type=Path)
    p.add_argument("--strict", action="store_true", help="считать предупреждения ошибками")
    p = sub.add_parser("impact", help="найти затронутые выводы")
    p.add_argument("project", type=Path)
    p.add_argument("ids", nargs="+")
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("trace", help="показать основания объекта")
    p.add_argument("project", type=Path)
    p.add_argument("id")
    p = sub.add_parser("stats", help="сводка")
    p.add_argument("project", type=Path)
    args = ap.parse_args(argv)

    ledger = Ledger(args.project.resolve())
    if args.cmd == "validate":
        rep = validate(ledger)
        for line in rep.errors + rep.warnings:
            print(line)
        print(f"\nИтог: ошибок {len(rep.errors)}, предупреждений {len(rep.warnings)}, объектов {len(ledger.objects)}")
        if rep.errors or (args.strict and rep.warnings):
            return 1
        return 0
    if args.cmd == "impact":
        return cmd_impact(ledger, args.ids, args.json)
    if args.cmd == "trace":
        return cmd_trace(ledger, args.id)
    if args.cmd == "stats":
        return cmd_stats(ledger)
    return 2


if __name__ == "__main__":
    sys.exit(main())

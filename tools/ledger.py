#!/usr/bin/env python3
"""Детерминированные операции над реестром решения (Evidence Ledger + Decision Memory).

Использование (из корня репозитория):
    python tools/ledger.py validate projects/<case>     # схемы, ссылки, правила доказательности, цитаты
    python tools/ledger.py impact   projects/<case> CHG-001   # что затронуто новыми сведениями
    python tools/ledger.py impact   projects/<case> SRC-004 CLM-002
    python tools/ledger.py trace    projects/<case> DEC-001   # на чём держится объект, вплоть до источников
    python tools/ledger.py stats    projects/<case>     # сводка для отчёта
    python tools/ledger.py status                       # проекты, текущий шаг, что ждёт решения
    python tools/ledger.py init     projects/<case> --title "…"
    python tools/ledger.py add-source projects/<case> <файл> --title "…" --kind document --access open

Код намеренно не вызывает LLM: проверка и поиск затронутых выводов должны быть
воспроизводимыми. LLM-агент читает вывод и обновляет формулировки сам.
"""
from __future__ import annotations

import argparse
import hashlib
import os
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
sys.path.insert(0, str(Path(__file__).resolve().parent))
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


_AGENT_SECTION = re.compile(r"^#{1,6}\s.*(пересказ|заметк\w* агента|комментари\w* агента)", re.I)
_HEADING = re.compile(r"^#{1,6}\s")


def evidence_text(text: str) -> str:
    """Текст источника без разделов, написанных агентом (заголовок со словом «пересказ» и т. п.)."""
    out, skip = [], False
    for line in text.splitlines():
        if _HEADING.match(line):
            skip = bool(_AGENT_SECTION.match(line))
        if not skip:
            out.append(line)
    return "\n".join(out)


def _head_objects(ledger: Ledger) -> Dict[str, dict]:
    """Объекты CLM/HYP из последнего коммита git (если проект в git). Без git — пусто."""
    import subprocess
    out: Dict[str, dict] = {}
    for fname in ("claims.yaml", "hypotheses.yaml"):
        path = ledger.dir / fname
        try:
            top = subprocess.run(["git", "rev-parse", "--show-toplevel"], cwd=str(ledger.dir),
                                 capture_output=True, text=True, timeout=10, encoding="utf-8")
            if top.returncode != 0:
                return {}
            rel = path.resolve().relative_to(Path(top.stdout.strip()).resolve())
            show = subprocess.run(["git", "show", f"HEAD:{rel.as_posix()}"], cwd=str(ledger.dir),
                                  capture_output=True, text=True, timeout=10, encoding="utf-8")
        except (OSError, ValueError, subprocess.SubprocessError):
            return {}
        if show.returncode != 0:
            continue
        try:
            data = yaml.load(show.stdout, Loader=_Loader) or []
        except yaml.YAMLError:
            continue
        for o in data if isinstance(data, list) else []:
            if isinstance(o, dict) and "id" in o:
                out[str(o["id"])] = o
    return out


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
                    elif _norm(quote) not in _norm(evidence_text(text)):
                        rep.err(oid, f"цитата взята из пересказа агента в {src['id']}: пересказ не доказательство — "
                                     "процитируйте оригинал или смените тип на interpretation")
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

    # 7. ключевые гипотезы должны проверяться — актуально, только когда решение уже принято (шаг 7+)
    tested = {t for oid, o in objs.items() if prefix(oid) == "EXP" for t in (o.get("tests") or [])}
    decided = any(prefix(k) == "DEC" and o.get("state") == "decided" for k, o in objs.items())
    for oid, o in objs.items() if decided else []:
        if prefix(oid) == "HYP" and o.get("would_change_decision") and o.get("verdict", "open") == "open" \
                and o.get("status", "active") == "active" and oid not in tested:
            rep.warn(oid, "гипотеза может изменить решение, но ни один эксперимент её не проверяет")

    # 8. формулировки нельзя переписывать на месте: только новый объект + superseded
    head = _head_objects(ledger)
    for oid, o in objs.items():
        old = head.get(oid)
        if not old or prefix(oid) not in ("CLM", "HYP") or o.get("status", "active") != "active":
            continue
        if _norm(str(old.get("statement", ""))) != _norm(str(o.get("statement", ""))):
            rep.warn(oid, "формулировка изменена на месте относительно последнего коммита — история решения теряется. "
                          f"Создайте новый объект и пометьте старый: ledger.py supersede {ledger.project} {oid} --by <новый ID>")

    # 9. необработанные новые сведения
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
        pairs = sorted({tuple(sorted((c["id"], x))) for c in claims for x in (c.get("contradicted_by") or [])})
        print(f"Противоречий (пар): {len(pairs)}" + (f" — {', '.join(a + '↔' + b for a, b in pairs)}" if pairs else ""))
    hyps = [o for k, o in objs.items() if prefix(k) == "HYP" and o.get("status", "active") == "active"]
    if hyps:
        n_h = sum(1 for h in hyps if h.get("kind") == "hypothesis")
        print(f"Гипотез: {n_h}, допущений: {len(hyps) - n_h}, "
              f"способных изменить решение: {sum(1 for h in hyps if h.get('would_change_decision'))}")
    sit = ledger.project / "outputs" / "03_situation.md"
    if sit.exists():
        gaps = len(re.findall(r"неизвестно\s*:", sit.read_text(encoding="utf-8"), flags=re.I))
        print(f"Пробелов («Неизвестно:» в 03_situation.md): {gaps}")
    return 0


# ---------- init / add-source / status ----------
STEPS = [
    "1. Постановка задачи → C1 (outputs/01_brief.md)",
    "2. План исследования (outputs/02_research_plan.md)",
    "3. Сбор материалов и утверждений (sources/, SRC/CLM)",
    "4. Картина ситуации (outputs/03_situation.md, HYP/PAIN)",
    "5. Варианты → C2 (outputs/04_options.md, ALT/DEC)",
    "6. Продуктовая и коммуникационная стратегии (outputs/05, 06, PRM)",
    "7. План проверки → C3 (outputs/07_validation_plan.md, EXP)",
    "8. Сопровождение решения (outputs/08_decision_log.md, 09_run_report.md)",
]


RUN_LOG_HEADER = ("| Время | Шаг | Действие | Уровень | Модель / инструмент | Результат | Стоимость |\n"
                  "|---|---|---|---|---|---|---|\n")
ROUTING_PATH = ROOT / "knowledge" / "routing.yaml"
TIER_ORDER = ["T0", "T1", "T2", "T3", "T4"]


ROUTING_LOCAL_PATH = ROOT / "knowledge" / "routing.local.yaml"


def _deep_merge(base: dict, over: dict) -> dict:
    out = dict(base)
    for k, v in (over or {}).items():
        out[k] = _deep_merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def load_routing(local: Optional[Path] = None) -> dict:
    """routing.yaml + необязательные локальные переопределения этой машины (routing.local.yaml, не в git):
    какие модели реально стоят за уровнями у конкретного пользователя (прокси, бесплатные модели и т. п.)."""
    cfg = yaml.safe_load(ROUTING_PATH.read_text(encoding="utf-8"))
    path = local if local is not None else ROUTING_LOCAL_PATH
    if path.exists():
        cfg = _deep_merge(cfg, yaml.safe_load(path.read_text(encoding="utf-8")) or {})
        cfg["_local"] = str(path)
    return cfg


def count_tier_in_log(project: Path, tier: str) -> int:
    log = project / "state" / "run_log.md"
    if not log.exists():
        return 0
    n = 0
    for line in log.read_text(encoding="utf-8").splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) >= 4 and cells[3] == tier:
            n += 1
    return n


SWITCH_TEXT = {
    "subagent": "встроенный субагент с моделью уровня",
    "cli": "делегировать подзадачу: python tools/delegate.py --project … --task {task} --prompt-file …",
    "picker": "попросить человека выбрать модель в интерфейсе в начале шага",
    "current": "выполнить текущей моделью и записать в журнал «рекомендован {tier}, выполнен на <модель>»",
}


def resolve_tier(task: str, failed_tier: Optional[str], cfg: dict) -> Tuple[str, str]:
    """Стартовый уровень задачи или следующая ступень после неудачи. ValueError — если некуда/неизвестно."""
    if task not in cfg["tasks"]:
        raise ValueError(f"Неизвестная задача «{task}». Доступны: {', '.join(sorted(cfg['tasks']))}")
    tier = cfg["tasks"][task]["tier"]
    if not failed_tier:
        return tier, "стартовый уровень задачи"
    if failed_tier not in TIER_ORDER:
        raise ValueError(f"Неизвестный уровень {failed_tier}")
    i = TIER_ORDER.index(failed_tier)
    if i == len(TIER_ORDER) - 1:
        raise ValueError("Выше T4 подниматься некуда: зафиксируйте пробел и спросите человека.")
    return TIER_ORDER[i + 1], f"эскалация после неудачи на {failed_tier} (ровно на одну ступень)"


def model_for(cfg: dict, family: Optional[str], tier: str) -> Optional[str]:
    """Модель семейства для уровня: фиксированный ID/алиас или новейшая версия по шаблону из инвентаря машины."""
    if tier == "T0" or not family:
        return None
    spec = (cfg.get("families", {}).get(family) or {}).get(tier)
    if isinstance(spec, (dict, list)):
        import models as M  # tools/models.py
        return M.resolve_for(cfg, family, tier)[0]
    return spec


def parse_failed(failed: Optional[str]) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """«T3», «T3@high» или «T4:gpt-6-astra@xhigh» → (уровень, модель, effort)."""
    if not failed:
        return None, None, None
    head, _, effort = failed.partition("@")
    tier, _, model = head.partition(":")
    return tier, (model or None), (effort or None)


def tier_efforts(cfg: dict, tier: str, family: Optional[str], model: Optional[str] = None) -> List[str]:
    """Лестница effort уровня (tiers.Tn.effort), переведённая на уровни, которые поддерживает модель."""
    wanted = (cfg.get("tiers", {}).get(tier) or {}).get("effort") or []
    if not wanted or tier == "T0":
        return []
    if not family:
        return list(wanted)
    import models as M
    supported = M.effort_levels(cfg, family, model or model_for(cfg, family, tier))
    return M.map_efforts(wanted, supported) if supported else []


def tier_steps(cfg: dict, tier: str, family: Optional[str]) -> List[Tuple[Optional[str], Optional[str]]]:
    """Ступени уровня по порядку: (модель, effort). Сначала effort на стартовой модели, затем следующая модель
    уровня (из каталога: above — сильнее), снова с первого effort."""
    if tier == "T0":
        return [(None, None)]
    models_ = [model_for(cfg, family, tier)] if family else [None]
    if family:
        import models as M
        models_ = M.tier_models(cfg, family, tier) or models_
    steps = []
    for m in models_:
        ladder = tier_efforts(cfg, tier, family, m)
        steps += [(m, e) for e in ladder] or [(m, None)]
    return steps


def resolve_step(task: str, failed: Optional[str], cfg: dict,
                 family: Optional[str] = None) -> Tuple[str, Optional[str], str, Optional[str]]:
    """(уровень, effort, причина, модель). Эскалация: больше effort → следующая модель уровня → следующий уровень."""
    failed_tier, failed_model, failed_effort = parse_failed(failed)
    if not failed_tier:
        tier, reason = resolve_tier(task, None, cfg)
        m, eff = tier_steps(cfg, tier, family)[0]
        return tier, eff, reason, m
    if failed_tier not in TIER_ORDER:
        raise ValueError(f"Неизвестный уровень {failed_tier}")
    steps = tier_steps(cfg, failed_tier, family)
    cur_m = failed_model or steps[0][0]
    cur_e = failed_effort or next((e for m, e in steps if m == cur_m), None)
    idx = next((i for i, st in enumerate(steps) if st == (cur_m, cur_e)), None)
    if idx is None and failed_effort:  # указан effort вне лестницы (например, max) — считаем, что ступени модели пройдены
        idx = max((i for i, (m, _) in enumerate(steps) if m == cur_m), default=len(steps) - 1)
    if idx is not None and idx + 1 < len(steps):
        m, eff = steps[idx + 1]
        if m == cur_m:
            why = (f"эскалация после неудачи на {failed_tier}@{cur_e}: сначала глубже рассуждение на той же модели "
                   f"({cur_e} → {eff}), модель старше — только если и это не поможет")
        else:
            why = f"эскалация после неудачи на {failed_tier}:{cur_m}@{cur_e}: следующая, более сильная модель того же уровня"
        return failed_tier, eff, why, m
    tier, reason = resolve_tier(task, failed_tier, cfg)
    m, eff = tier_steps(cfg, tier, family)[0]
    return tier, eff, reason, m


def cmd_route(task: str, failed_tier: Optional[str], project: Optional[Path], env: Optional[str] = None) -> int:
    cfg = load_routing()
    envs_ = cfg.get("environments", {})
    family = (envs_.get(env) or {}).get("family") if env else None
    try:
        tier, effort, reason, step_model = resolve_step(task, failed_tier, cfg, family)
    except ValueError as exc:
        print(str(exc), file=sys.stderr if "Неизвестн" in str(exc) else sys.stdout)
        return 2 if "Неизвестн" in str(exc) else 1
    envs = cfg.get("environments", {})
    if env and env not in envs:
        print(f"Неизвестная среда «{env}». Доступны: {', '.join(envs)}", file=sys.stderr)
        return 2
    print(f"Задача: {task}")
    print(f"Уровень: {tier} — {cfg['tiers'][tier]['name']} ({reason})")
    if effort or step_model:
        steps = tier_steps(cfg, tier, family)
        ladder = " → ".join(f"{m}@{e}" if m and e else (m or e or "") for m, e in steps)
        tag = f"{tier}:{step_model}@{effort}" if step_model and len({m for m, _ in steps}) > 1 else f"{tier}@{effort}"
        if effort:
            print(f"Effort: {effort} (ступени уровня: {ladder}; не справился — `--failed {tag}`)")
        pol = cfg.get("effort_policy") or {}
        print(f"Effort {', '.join(pol.get('ask', ['max']))} — только с согласия человека; "
              f"{', '.join(pol.get('never', ['ultra']))} — не используется (см. effort_policy в routing.yaml).")
    if cfg["tasks"][task].get("note"):
        print(f"Заметка: {cfg['tasks'][task]['note']}")
    dq = ((cfg.get("decision") or {}).get("questions") or {}).get(task)
    if dq:
        import decide as DM
        prov = DM.pick_provider(cfg)
        print(f"Модель решений: tools/decide.py --questions knowledge/decisions/{dq}.yaml — "
              + (f"доступна ({prov['name']}: {prov['model']})" if prov else
                 "не настроена (нет ключа в окружении) — выполнять на уровне задачи"))
    if cfg.get("_local"):
        print(f"Учтены локальные переопределения: {cfg['_local']}")
    if tier == "T0":
        print("Модель: не нужна — выполнить кодом")
    elif env:
        e = envs[env]
        m = step_model or model_for(cfg, e.get("family"), tier)
        print(f"Среда: {env}; модель: {m or 'не задана для этой среды — см. how'}")
        for i, s in enumerate(e.get("switch", []), 1):
            print(f"  {i}. {SWITCH_TEXT[s].format(task=task, tier=tier)}")
        print(f"Как в этой среде: {e.get('how', '')}")
        if env == "claude-code" and os.environ.get("ANTHROPIC_BASE_URL"):
            print("ВНИМАНИЕ: задан ANTHROPIC_BASE_URL — алиасы haiku/sonnet/opus разрешает ваш прокси, а не Anthropic. "
                  "Какая модель реально ответит, решает прокси: пропишите соответствие в knowledge/routing.local.yaml "
                  "и в настройках прокси (см. knowledge/routing.local.example.yaml).")
    else:
        fams = ", ".join(f"{f} — {model_for(cfg, f, tier)}" for f, v in cfg.get("families", {}).items()
                         if v and v.get(tier))
        print(f"Модели уровня: {fams}")
        print("Укажите --env <среда>, чтобы получить способ переключения "
              f"({', '.join(envs)}).")
    if tier == "T4":
        budget = cfg.get("budget", {})
        used = count_tier_in_log(project, "T4") if project else 0
        limit = budget.get("max_t4_calls", 0)
        print(f"T4 уже использован в проекте: {used} из {limit}")
        if budget.get("ask_before_t4", True):
            print("Перед запуском T4 сообщите человеку причину эскалации и спросите согласия.")
        if used >= limit:
            print("Лимит T4 исчерпан — без согласия человека не запускать.")
            return 1
    return 0


def _cell(s: str) -> str:
    return re.sub(r"\s+", " ", str(s)).replace("|", "/").strip()


# Порядок важен: ZCode ради совместимости выставляет и CLAUDE_* переменные (CLAUDE_SESSION_ID, CLAUDE_PROJECT_DIR),
# поэтому его признаки проверяются раньше признаков Claude Code. Проверено по коду ZCode 0.17.1 и Claude Code (CLAUDECODE=1).
ENV_MARKERS = [
    ("zcode", ("ZCODE_SESSION_ID", "ZCODE_PROJECT_DIR")),
    ("claude-code", ("CLAUDECODE",)),
    ("codex", ("CODEX_THREAD_ID", "CODEX_SANDBOX", "CODEX_SANDBOX_NETWORK_DISABLED", "CODEX_CI")),
    ("opencode", ("OPENCODE", "OPENCODE_SESSION_ID")),
    ("cursor", ("CURSOR_TRACE_ID", "CURSOR_AGENT")),
]


def detect_env(environ: Optional[dict] = None) -> Tuple[str, str]:
    """(среда, основание). 'unknown' — признаков нет: агент называет среду сам (по своему названию), не по CLAUDE.md."""
    env = os.environ if environ is None else environ
    for name, markers in ENV_MARKERS:
        hit = [m for m in markers if env.get(m)]
        if hit:
            return name, f"переменная {hit[0]}"
    return "unknown", "нет известных переменных окружения"


def cmd_env() -> int:
    name, why = detect_env()
    print(f"Среда: {name} ({why})")
    if name == "unknown":
        print("Определите среду по своему названию (Codex, Cursor, Copilot, Windsurf, GigaCode, Кодик…). "
              "Не по наличию CLAUDE.md: его читают многие среды.")
    return 0


def cmd_supersede(project: Path, old_id: str, new_id: str, note: Optional[str]) -> int:
    """Пометить старый объект заменённым новым (запись не удаляется и не переписывается)."""
    import datetime
    ledger = Ledger(project)
    for x in (old_id, new_id):
        if x not in ledger.objects:
            print(f"Не найден {x}", file=sys.stderr)
            return 2
    if prefix(old_id) != prefix(new_id) or old_id == new_id:
        print("Заменять можно только объектом того же типа и с другим ID", file=sys.stderr)
        return 2
    fname = TYPES[prefix(old_id)][0]
    path = ledger.dir / fname
    text = path.read_text(encoding="utf-8")
    data = yaml.load(text, Loader=_Loader) or []
    for o in data:
        if o.get("id") == old_id:
            o["status"] = "superseded"
            o["superseded_by"] = new_id
            o["updated"] = datetime.date.today().isoformat()
            if note:
                o["status_note"] = note
    path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
    print(f"{old_id} → superseded, заменён {new_id}. Запустите validate: всё, что опирается на {old_id}, получит предупреждение.")
    return 0


def brief_line(oid: str, o: dict) -> str:
    """Объект одной строкой: суть, статус, уверенность, вердикт, баллы — без цитат и длинных обоснований."""
    text = o.get("statement") or o.get("title") or o.get("summary") or o.get("question") or o.get("message") or ""
    bits = [f"{oid} [{o.get('status', 'active')}{'/' + o['state'] if o.get('state') else ''}]"]
    conf = o.get("confidence") or {}
    if conf.get("level"):
        bits.append(f"увер. {conf['level']}")
    if o.get("verdict"):
        bits.append(f"вердикт {o['verdict']}")
    if o.get("type"):
        bits.append(str(o["type"]))
    sc = o.get("scores") or {}
    if sc:
        vals = [v.get("value") if isinstance(v, dict) else v for v in sc.values()]
        nums = [v for v in vals if isinstance(v, (int, float))]
        bits.append(f"баллы {'/'.join(str(v) for v in vals)}" + (f" = {sum(nums)}" if nums else ""))
    for k in ("chosen", "recommended", "superseded_by"):
        if o.get(k):
            bits.append(f"{k}: {o[k]}")
    bits.append(re.sub(r"\s+", " ", str(text))[:160])
    return " | ".join(bits)


def cmd_brief(ledger: "Ledger", ids: List[str], include_all: bool) -> int:
    sel = []
    for oid, o in sorted(ledger.objects.items()):
        if ids and not any(oid == i or prefix(oid) == i for i in ids):
            continue
        if not include_all and o.get("status", "active") in ("superseded", "retracted"):
            continue
        sel.append(brief_line(oid, o))
    print("\n".join(sel) if sel else "Ничего не найдено.")
    return 0


def cmd_log(project: Path, step: str, action: str, tier: str, model: str, result: str, cost: str,
            now: Optional[str] = None) -> int:
    import datetime

    log = project / "state" / "run_log.md"
    if not log.exists():
        print(f"Нет журнала {log}: сначала ledger.py init", file=sys.stderr)
        return 1
    if tier not in TIER_ORDER and tier != "-":
        print(f"Уровень должен быть одним из {', '.join(TIER_ORDER)} или «-»", file=sys.stderr)
        return 2
    ts = now or datetime.datetime.now().astimezone().isoformat(timespec="seconds")
    row = "| " + " | ".join(_cell(x) for x in (ts, step, action, tier, model, result, cost)) + " |\n"
    text = log.read_text(encoding="utf-8")
    if not text.endswith("\n"):
        text += "\n"
    log.write_text(text + row, encoding="utf-8")
    print(f"Записано в журнал: {ts} · шаг {step} · {tier}")
    return 0


def cmd_init(project: Path, title: str) -> int:
    if project.exists() and any(project.iterdir()):
        print(f"Папка {project} уже существует и не пуста — продолжите работу по её state/progress.md",
              file=sys.stderr)
        return 1
    for sub in ("sources", "outputs", "state/ledger"):
        (project / sub).mkdir(parents=True, exist_ok=True)
    for fname, _, _ in TYPES.values():
        (project / "state/ledger" / fname).write_text("[]\n", encoding="utf-8")
    steps = "\n".join(f"- [ ] {s}" for s in STEPS)
    (project / "state/progress.md").write_text(
        f"# Прогресс: {title}\n\n"
        f"Текущий шаг: 1\n"
        f"Среда: (агент записывает свою среду: claude-code, codex, cursor, …)\n\n"
        f"## Шаги\n{steps}\n\n"
        f"## Что сделано в текущем шаге\n- (пока ничего)\n\n"
        f"## Что дальше\n- Прочитать запрос пользователя и составить бриф (skills/problem-framing)\n\n"
        f"## Открытые вопросы к человеку\n- (нет)\n",
        encoding="utf-8")
    (project / "state/run_log.md").write_text(
        f"# Журнал действий: {title}\n\n"
        "Пишется командой `ledger.py log`, а не вручную.\n\n"
        f"{RUN_LOG_HEADER}",
        encoding="utf-8")
    print(f"Создан проект {project}. Следующий шаг: workflows/strategy.md, шаг 1.")
    return 0


def _next_id(ledger: Ledger, pfx: str) -> str:
    nums = [int(k.split("-")[1]) for k in ledger.objects if prefix(k) == pfx]
    return f"{pfx}-{(max(nums) + 1 if nums else 1):03d}"


def cmd_add_source(project: Path, file: Path, title: str, kind: str, access: str,
                   url: Optional[str], reliability: str, published: Optional[str], notes: Optional[str],
                   today: Optional[str] = None) -> int:
    import datetime
    import shutil

    if access not in ("open", "own"):
        print("access должен быть open или own: закрытые данные не используются", file=sys.stderr)
        return 1
    # путь может быть абсолютным, относительным от текущей папки или от папки проекта
    candidates = [file] if file.is_absolute() else [Path.cwd() / file, project / file]
    file = next((c for c in candidates if c.exists()), candidates[0])
    if not file.exists():
        print(f"Файл не найден: {file}", file=sys.stderr)
        return 1
    ledger = Ledger(project)
    digest = hashlib.sha256(file.read_bytes()).hexdigest()
    for oid, o in ledger.objects.items():
        if prefix(oid) == "SRC" and o.get("sha256") == digest:
            print(f"Уже зарегистрирован как {oid} — повторно не добавляю", file=sys.stderr)
            return 3
    sources_dir = (project / "sources").resolve()
    target = file.resolve()
    if sources_dir not in target.parents:
        target = sources_dir / file.name
        if target.exists():
            print(f"В sources/ уже есть другой файл {file.name} — переименуйте", file=sys.stderr)
            return 1
        shutil.copy2(file, target)
    new_id = _next_id(ledger, "SRC")
    entry = {
        "id": new_id, "title": title, "kind": kind, "access": access,
        "path": target.relative_to(project.resolve()).as_posix(), "sha256": digest,
        "retrieved": today or datetime.date.today().isoformat(), "reliability": reliability,
    }
    if url:
        entry["url"] = url
    if published:
        entry["published"] = published
    if notes:
        entry["notes"] = notes
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    problems = [e.message for e in Draft202012Validator(
        {"$schema": schema["$schema"], "$defs": schema["$defs"], "$ref": "#/$defs/source"}).iter_errors(entry)]
    if problems:
        if target != file.resolve():
            target.unlink()  # убрать только что скопированную копию
        print("Источник не зарегистрирован: " + "; ".join(problems), file=sys.stderr)
        return 1
    path = project / "state/ledger/sources.yaml"
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    if existing.strip() in ("", "[]"):
        existing = ""
    block = yaml.safe_dump([entry], allow_unicode=True, sort_keys=False)
    sep = "" if existing.endswith("\n") or not existing else "\n"
    path.write_text(existing + sep + block, encoding="utf-8")
    print(f"{new_id} зарегистрирован: {entry['path']} (sha256 {digest[:12]}…)")
    return 0


def cmd_status(projects_root: Path) -> int:
    if not projects_root.is_dir():
        print("Проектов пока нет. Начните: python tools/ledger.py init projects/<slug> --title \"…\"")
        return 0
    found = False
    for proj in sorted(p for p in projects_root.iterdir() if p.is_dir()):
        prog = proj / "state" / "progress.md"
        if not prog.exists():
            continue
        found = True
        text = prog.read_text(encoding="utf-8")
        title = text.splitlines()[0].replace("# Прогресс:", "").strip() if text else proj.name
        m = re.search(r"Текущий шаг:\s*(.+)", text)
        done = len(re.findall(r"- \[x\]", text, flags=re.I))
        ledger = Ledger(proj)
        todo = []
        for oid, o in ledger.objects.items():
            if prefix(oid) == "CHG" and not o.get("processed"):
                todo.append(f"необработанные сведения {oid}")
            if prefix(oid) == "DEC" and o.get("state") == "needs_review":
                todo.append(f"{oid} ждёт пересмотра")
        print(f"- {proj.name}: «{title}» — текущий шаг {m.group(1).strip() if m else '?'}, "
              f"выполнено шагов {done}/{len(STEPS)}, объектов в реестре {len(ledger.objects)}")
        for t in todo:
            print(f"    ! {t}")
    if not found:
        print("Проектов пока нет. Начните: python tools/ledger.py init projects/<slug> --title \"…\"")
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
    p = sub.add_parser("brief", help="одна строка на объект: вместо чтения YAML целиком")
    p.add_argument("project", type=Path)
    p.add_argument("ids", nargs="*", help="ID или префиксы (HYP, ALT…); пусто — все активные")
    p.add_argument("--all", action="store_true", help="включая superseded/retracted")
    p = sub.add_parser("stats", help="сводка")
    p.add_argument("project", type=Path)
    p = sub.add_parser("init", help="создать проект")
    p.add_argument("project", type=Path)
    p.add_argument("--title", required=True)
    p = sub.add_parser("add-source", help="зарегистрировать источник")
    p.add_argument("project", type=Path)
    p.add_argument("file", type=Path)
    p.add_argument("--title", required=True)
    p.add_argument("--kind", required=True, choices=["document", "table", "website", "interview",
                                                     "synthetic_interview", "dataset", "image", "audio",
                                                     "video", "other"])
    p.add_argument("--access", required=True, choices=["open", "own"])
    p.add_argument("--url")
    p.add_argument("--reliability", default="medium", choices=["high", "medium", "low"])
    p.add_argument("--published")
    p.add_argument("--notes")
    p = sub.add_parser("supersede", help="пометить объект заменённым новым")
    p.add_argument("project", type=Path)
    p.add_argument("old")
    p.add_argument("--by", required=True, help="ID нового объекта")
    p.add_argument("--note")
    sub.add_parser("env", help="определить среду (claude-code, codex, zcode, …) по переменным окружения")
    p = sub.add_parser("status", help="проекты и текущий шаг")
    p.add_argument("--root", type=Path, default=ROOT / "projects")
    p = sub.add_parser("route", help="какой уровень модели взять для задачи")
    p.add_argument("task")
    p.add_argument("--failed", metavar="TIER[@EFFORT]", help="уровень (и effort) неудачи: T3 или T3@high → сначала больше effort, потом уровень выше")
    p.add_argument("--project", type=Path, help="для учёта лимита T4 по журналу")
    p.add_argument("--env", help="среда: claude-code, codex, opencode, cursor, copilot, windsurf, gigacode, kodik, orca")
    p = sub.add_parser("log", help="добавить строку в state/run_log.md")
    p.add_argument("project", type=Path)
    p.add_argument("--step", required=True)
    p.add_argument("--action", required=True)
    p.add_argument("--tier", default="-", help="T0–T4 или «-»")
    p.add_argument("--model", default="-", help="модель или инструмент")
    p.add_argument("--result", required=True)
    p.add_argument("--cost", default="-", help="токены / поиски / источники, если известно")
    args = ap.parse_args(argv)

    if args.cmd == "status":
        return cmd_status(args.root)
    if args.cmd == "env":
        return cmd_env()
    if args.cmd == "supersede":
        return cmd_supersede(args.project.resolve(), args.old, args.by, args.note)
    if args.cmd == "route":
        return cmd_route(args.task, args.failed, args.project.resolve() if args.project else None, args.env)
    if args.cmd == "log":
        return cmd_log(args.project.resolve(), args.step, args.action, args.tier, args.model, args.result,
                       args.cost)
    if args.cmd == "init":
        return cmd_init(args.project.resolve(), args.title)
    if args.cmd == "add-source":
        return cmd_add_source(args.project.resolve(), args.file, args.title, args.kind, args.access,
                              args.url, args.reliability, args.published, args.notes)

    if not args.project.resolve().is_dir():
        print(f"Нет папки проекта: {args.project}", file=sys.stderr)
        return 2
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
    if args.cmd == "brief":
        return cmd_brief(ledger, args.ids, args.all)
    return 2


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

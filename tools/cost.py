#!/usr/bin/env python3
"""Сколько стоил прогон: токены основной модели и помощников из журналов сред → деньги по официальным ценам.

    python tools/cost.py --cwd e2e-ryadom --since 2026-11-10T14:00 --until 2026-11-10T18:00 [--write projects/ryadom]
    python tools/cost.py --zcode --since 2026-11-10T18:00 --match МегаФон-Стратег

Откуда берутся токены (только чтение, ничего не меняет):
  codex       — $CODEX_HOME, ~/.codex и домашние папки Codex, которые создаёт Orca: state_5.sqlite (потоки по cwd)
                + файл сессии (прирост total_token_usage по времени событий token_count);
  claude-code — ~/.claude/projects/*/*.jsonl (message.usage по cwd);
  zcode       — ~/.zcode/cli/rollout/model-io-*.jsonl (usage ответа модели; фильтр по времени и --match).
Цены — knowledge/prices.yaml (официальные страницы, дата проверки). Модели без цены показываются с токенами
и пометкой «нет цены» — не угадываем. Подписка (ChatGPT Pro, Coding Plan) — это не цена API: отчёт даёт
API-эквивалент, т. е. сколько стоил бы прогон при оплате по токенам.
"""
from __future__ import annotations

import argparse
import datetime as dt
import fnmatch
import glob
import json
import os
import re
import sqlite3
from urllib.parse import quote
import sys
from collections import defaultdict
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

import yaml

ROOT = Path(__file__).resolve().parent.parent
PRICES = ROOT / "knowledge" / "prices.yaml"
HOME = Path.home()


def parse_time(s: Optional[str]) -> Optional[dt.datetime]:
    if not s:
        return None
    t = dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
    return t if t.tzinfo else t.astimezone()


def load_prices(path: Path = PRICES) -> List[dict]:
    return (yaml.safe_load(path.read_text(encoding="utf-8")) or {}).get("models") or []


def price_for(model: str, prices: List[dict]) -> Optional[dict]:
    m = model.split("/")[-1].lower()
    for p in prices:
        if fnmatch.fnmatch(m, str(p["match"]).lower()):
            return p
    return None


def usd(u: dict, p: Optional[dict]) -> Optional[float]:
    if not p:
        return None
    return (u["input"] * p["input"] + u["cached"] * p["cached"] + u["output"] * p["output"]) / 1_000_000


# ---------- источники ----------
def codex_homes() -> List[Path]:
    cands = [Path(os.environ["CODEX_HOME"])] if os.environ.get("CODEX_HOME") else []
    cands += [HOME / ".codex"]
    cands += [Path(p) for p in glob.glob(str(HOME / "Library/Application Support/orca/codex-accounts/*/home"))]
    return [c for c in dict.fromkeys(cands) if (c / "state_5.sqlite").exists()]


def _codex_window(path, created, since, until) -> Optional[dict]:
    """Sum cumulative deltas at event timestamps (inclusive bounds).

    A first snapshot is charged from zero only for an unbounded start or a
    thread created inside the window. Otherwise its earlier usage is unknown.
    last_token_usage is deliberately unused: repeated snapshots repeat it too.
    """
    totals = {"input": 0, "cached": 0, "output": 0, "calls": 0}
    previous = None
    previous_ts = None
    notes = set()
    fields = ("input_tokens", "cached_input_tokens", "output_tokens")
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                try:
                    event = json.loads(line)
                except ValueError:
                    notes.add("повреждённая строка журнала")
                    continue
                if not isinstance(event, dict) or event.get("type") != "event_msg":
                    continue
                payload = event.get("payload")
                if not isinstance(payload, dict) or payload.get("type") != "token_count":
                    continue
                info = payload.get("info")
                # info:null is a real non-measuring token_count event.
                if info is None:
                    continue
                try:
                    ts = parse_time(event.get("timestamp"))
                except (ValueError, TypeError, AttributeError):
                    ts = None
                if ts is None and (since or until):
                    notes.add("нет времени события")
                    continue
                if ts and until and ts > until:
                    continue
                in_window = not (ts and since and ts < since)
                usage = info.get("total_token_usage") if isinstance(info, dict) else None
                if (not isinstance(usage, dict) or
                        any(type(usage.get(k)) is not int or usage[k] < 0 for k in fields) or
                        usage["cached_input_tokens"] > usage["input_tokens"]):
                    if in_window:
                        notes.add("неполные или некорректные счётчики")
                    continue
                current = tuple(usage[k] for k in fields)
                if ts and previous_ts and ts < previous_ts:
                    if in_window:
                        notes.add("нарушен порядок времени событий")
                    continue
                if previous is None:
                    if in_window and (not since or (created is not None and created >= since)):
                        delta = current
                    else:
                        delta = (0, 0, 0)
                        if in_window:
                            notes.add("нет начальной точки до окна")
                else:
                    delta = tuple(v - p for v, p in zip(current, previous))
                previous, previous_ts = current, ts
                if not in_window:
                    continue
                if any(v < 0 for v in delta) or delta[1] > delta[0]:
                    notes.add("сброс или несогласованный прирост счётчиков")
                    continue
                totals["input"] += delta[0] - delta[1]
                totals["cached"] += delta[1]
                totals["output"] += delta[2]
                if any(delta):
                    totals["calls"] = 1  # streams, not snapshot count
    except (OSError, UnicodeError) as exc:
        notes.add(f"журнал недоступен: {exc.__class__.__name__}")
    if notes:
        totals.update(incomplete=True, notes=sorted(notes))
    return totals if totals["calls"] or notes else None


def cwd_matches(recorded: Optional[str], selected: str, exact: bool) -> bool:
    if not isinstance(recorded, str) or not recorded:
        return False
    if not exact:
        return selected in recorded
    return (os.path.isabs(recorded)
            and os.path.normcase(os.path.realpath(recorded)) == selected)


def codex_usage(cwd_sub: str, since, until, *, exact: bool = False) -> Iterable[Tuple[str, str, dict, str]]:
    """Codex streams by cwd; usage is bounded by event time, not creation time."""
    seen = set()
    for home in codex_homes():
        try:
            con = sqlite3.connect(f"file:{quote(str(home / 'state_5.sqlite'))}?mode=ro", uri=True)
            try:
                rows = con.execute("select id, rollout_path, model, created_at, updated_at, cwd from threads").fetchall()
            finally:
                con.close()
        except sqlite3.Error as exc:  # база занята или недоступна — не роняем весь отчёт
            print(f"cost.py: пропущен {home}: {exc}", file=sys.stderr)
            yield "codex", "?", {"input": 0, "cached": 0, "output": 0, "calls": 0,
                                  "incomplete": True, "notes": ["база потоков недоступна"]}, str(home)
            continue
        for tid, path, model, c, u, cwd in rows:
            if tid in seen or not cwd_matches(cwd, cwd_sub, exact):
                continue
            seen.add(tid)
            try:
                created = dt.datetime.fromtimestamp(c).astimezone()
            except (TypeError, ValueError, OverflowError, OSError):
                created = None
            if created is not None and until and created > until:
                continue
            usage = _codex_window(path, created, since, until)
            if usage is not None:
                yield "codex", model or "?", usage, tid


def claude_usage(cwd_sub: str, since, until, *, exact: bool = False) -> Iterable[Tuple[str, str, dict, str]]:
    for f in glob.glob(str(HOME / ".claude/projects/*/*.jsonl")):
        try:
            fh = open(f, encoding="utf-8")
        except OSError:
            continue
        with fh:
            for line in fh:
                if '"usage"' not in line or (not exact and cwd_sub not in line):
                    continue
                try:
                    e = json.loads(line)
                except ValueError:
                    continue
                msg = e.get("message") or {}
                u = msg.get("usage")
                if e.get("type") != "assistant" or not u or not cwd_matches(e.get("cwd"), cwd_sub, exact):
                    continue
                ts = parse_time(e.get("timestamp", "").replace("Z", "+00:00")) if e.get("timestamp") else None
                if ts and ((since and ts < since) or (until and ts > until)):
                    continue
                yield "claude-code", msg.get("model", "?"), {
                    "input": int(u.get("input_tokens", 0)) + int(u.get("cache_creation_input_tokens", 0)),
                    "cached": int(u.get("cache_read_input_tokens", 0)), "output": int(u.get("output_tokens", 0)),
                    "calls": 1}, Path(f).stem


def zcode_usage(match: Optional[str], since, until) -> Iterable[Tuple[str, str, dict, str]]:
    for f in glob.glob(str(HOME / ".zcode/cli/rollout/model-io-*.jsonl")):
        if match and not any(match in line for line in open(f, encoding="utf-8", errors="replace")):
            continue
        with open(f, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                try:
                    e = json.loads(line)
                except ValueError:
                    continue
                resp = e.get("response") or {}
                u = ((resp.get("providerMetadata") or {}).get("anthropic") or {}).get("usage") or resp.get("usage")
                if not u:
                    continue
                ts = e.get("completedAt")
                ts = (dt.datetime.fromtimestamp(ts / 1000).astimezone() if isinstance(ts, (int, float))
                      else parse_time(str(ts).replace("Z", "+00:00")) if ts else None)
                if ts and ((since and ts < since) or (until and ts > until)):
                    continue
                yield "zcode", resp.get("modelId") or e.get("model") or "?", {
                    "input": int(u.get("input_tokens", 0) or 0),
                    "cached": int(u.get("cache_read_input_tokens", 0) or 0),
                    "output": int(u.get("output_tokens", 0) or 0), "calls": 1}, Path(f).stem


DELEG = re.compile(r"вход (\d+)(?: \(кэш (\d+)\))?, выход (\d+)")


def delegated_usage(project: Path, since, until) -> Iterable[Tuple[str, str, dict, str]]:
    """Вызовы tools/delegate.py из run_log проекта: они идут в пустой временной папке (--ephemeral),
    поэтому в журналах сред по cwd проекта их не видно. Токены берём из строки журнала."""
    log = project / "state" / "run_log.md"
    if not log.exists():
        return
    for line in log.read_text(encoding="utf-8").splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 7 or not cells[2].startswith("делегирование"):
            continue
        ts = parse_time(cells[0]) if re.match(r"\d{4}-\d{2}-\d{2}T", cells[0]) else None
        if ts and ((since and ts < since) or (until and ts > until)):
            continue
        m = DELEG.search(cells[6])
        if not m:
            continue
        model = cells[4].split(":", 1)[-1].split("@")[0]
        total, cached, out = int(m.group(1)), int(m.group(2) or 0), int(m.group(3))
        yield "delegate", model, {"input": total - cached, "cached": cached, "output": out, "calls": 1}, cells[0]


def pending_log_notes(project: Path, since, until) -> Iterable[str]:
    from ledger import pending_log_files, read_pending_log

    try:
        for path in pending_log_files(project):
            try:
                ts, _ = read_pending_log(path)
                stamp = parse_time(ts)
            except FileNotFoundError:
                continue  # Replay already moved the row into the journal.
            except (OSError, ValueError) as exc:
                yield f"log_status=pending: {path}: {exc}"
                continue
            if (since and stamp < since) or (until and stamp > until):
                continue
            yield f"log_status=pending: {path} ({ts})"
    except (OSError, ValueError) as exc:
        yield f"Не удалось проверить ожидающие записи {project}: {exc}"


# ---------- сводка ----------
def summarize(records: Iterable[Tuple[str, str, dict, str]], prices: List[dict],
              *, pending_notes: Iterable[str] = ()) -> dict:
    agg: Dict[Tuple[str, str], dict] = defaultdict(lambda: {"input": 0, "cached": 0, "output": 0, "calls": 0})
    notes = set(pending_notes)
    for env, model, u, record_id in records:
        if u.get("incomplete"):
            notes.update(f"{env}/{record_id}: {note}" for note in u.get("notes", ["неполные данные"]))
        a = agg[(env, model)]
        for k in a:
            a[k] += u.get(k, 0)
    rows, total, unpriced = [], 0.0, []
    for (env, model), u in sorted(agg.items()):
        p = price_for(model, prices)
        cost = usd(u, p)
        if cost is None:
            unpriced.append(model)
        else:
            total += cost
        rows.append({"env": env, "model": model, **u, "usd": cost})
    result = {"rows": rows, "total_usd": round(total, 4), "unpriced": unpriced}
    if notes or unpriced:
        result.update(total_usd=None, known_usd=round(total, 4) if rows else None, incomplete=True, notes=sorted(notes))
    return result


NO_USAGE_NOTE = "Статус расчёта: нет записей токенов для выбранного окна."


def render(s: dict, title: str, usd_rub: Optional[float]) -> str:
    if not s["rows"]:
        text = f"## {title}\n\n{NO_USAGE_NOTE}\nДенежный итог по этому окну неизвестен."
        if s.get("notes"):
            text += "\nНеполные данные: " + "; ".join(s["notes"]) + "."
        return text

    out = [f"## {title}", "", "| Среда | Модель | Вызовы/потоки | Вход (без кэша) | Кэш | Выход | USD |",
           "|---|---|---|---|---|---|---|"]
    for r in s["rows"]:
        c = f"{r['usd']:.4f}" if r["usd"] is not None else "нет цены"
        out.append(f"| {r['env']} | {r['model']} | {r['calls']} | {r['input']:,} | {r['cached']:,} | {r['output']:,} | {c} |"
                   .replace(",", " "))
    amount = s["known_usd"] if s.get("incomplete") else s["total_usd"]
    if s.get("incomplete"):
        tot = f"**Полный итог неизвестен; учтённая часть (API-эквивалент): ${amount:.4f}**"
    else:
        tot = f"**Итого (API-эквивалент): ${amount:.4f}**"
    if usd_rub:
        tot += f" ≈ {amount * usd_rub:.2f} ₽ (курс {usd_rub}, указан вручную)"
    out += ["", tot]
    if s.get("notes"):
        out.append("Неполные данные: " + "; ".join(s["notes"]) + ".")
    if s["unpriced"]:
        out.append(f"Без цены (в итог не вошли): {', '.join(s['unpriced'])} — добавить в knowledge/prices.yaml.")
    out.append(f"Цены: knowledge/prices.yaml (официальные страницы вендоров). Подписка — не цена API.")
    return "\n".join(out)


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Стоимость прогона по журналам сред и официальным ценам")
    ap.add_argument("--cwd", help="часть пути рабочей папки сессии (Codex, Claude Code)")
    ap.add_argument("--cwd-exact", action="store_true", help="сопоставить --cwd с полным путём, исключая соседние и вложенные папки")
    ap.add_argument("--zcode", action="store_true", help="учесть журналы ZCode")
    ap.add_argument("--match", help="для ZCode: строка, которая должна встречаться в сессии (например, путь проекта)")
    ap.add_argument("--since")
    ap.add_argument("--until")
    ap.add_argument("--usd-rub", type=float, help="курс для пересчёта в рубли (указывается явно)")
    ap.add_argument("--write", type=Path, help="дописать отчёт в <проект>/state/cost_report.md")
    ap.add_argument("--project", type=Path, help="учесть вызовы delegate.py из run_log проекта (их нет в журналах сред)")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    cwd = args.cwd
    if args.cwd_exact:
        if not cwd or not os.path.isabs(os.path.expanduser(cwd)):
            ap.error("--cwd-exact требует абсолютный путь в --cwd")
        cwd = os.path.normcase(os.path.realpath(os.path.expanduser(cwd)))
    if not args.cwd and not args.zcode and not args.project:
        print("Укажите --cwd, --zcode и/или --project", file=sys.stderr)
        return 2
    since, until = parse_time(args.since), parse_time(args.until)
    recs: List = []
    if cwd:
        recs += list(codex_usage(cwd, since, until, exact=args.cwd_exact))
        recs += list(claude_usage(cwd, since, until, exact=args.cwd_exact))
    if args.zcode:
        recs += list(zcode_usage(args.match, since, until))
    proj = args.project or args.write
    pending_notes = []
    if proj:
        # Scan pending first: replay publishes the journal row before removing its pending file.
        pending_notes = list(pending_log_notes(proj, since, until))
        recs += list(delegated_usage(proj, since, until))
    s = summarize(recs, load_prices(), pending_notes=pending_notes)
    if args.json:
        print(json.dumps(s if s["rows"] else {**s, "total_usd": None}, ensure_ascii=False, indent=2))
        return 0
    title = f"Стоимость: {args.cwd or ''}{' + ZCode' if args.zcode else ''} ({args.since or '…'} — {args.until or '…'})"
    text = render(s, title, args.usd_rub)
    print(text)
    if args.write:
        f = args.write / "state" / "cost_report.md"
        with open(f, "a", encoding="utf-8") as fh:
            fh.write(text + f"\n\n_Сформировано {dt.datetime.now().astimezone().isoformat(timespec='seconds')}: "
                            f"python tools/cost.py {' '.join(argv or sys.argv[1:])}_\n\n")
        print(f"Записано: {f}")
    return 0 if recs else 1


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

#!/usr/bin/env python3
"""Сколько стоил прогон: токены основной модели и помощников из журналов сред → деньги по официальным ценам.

    python tools/cost.py --cwd e2e-ryadom --since 2026-11-10T14:00 --until 2026-11-10T18:00 [--write projects/ryadom]
    python tools/cost.py --zcode --since 2026-11-10T18:00 --match МегаФон-Стратег

Откуда берутся токены (только чтение, ничего не меняет):
  codex       — $CODEX_HOME, ~/.codex и домашние папки Codex, которые создаёт Orca: state_5.sqlite (потоки по cwd)
                + файл сессии (последний token_count: input, cached, output);
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
    t = dt.datetime.fromisoformat(s)
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


def codex_usage(cwd_sub: str, since, until) -> Iterable[Tuple[str, str, dict, str]]:
    """(среда, модель, usage, id) по потокам Codex, чей cwd содержит cwd_sub и чьё время попадает в окно."""
    for home in codex_homes():
        con = sqlite3.connect(f"file:{home / 'state_5.sqlite'}?mode=ro", uri=True)
        try:
            rows = con.execute("select id, rollout_path, model, created_at, updated_at, cwd from threads").fetchall()
        finally:
            con.close()
        for tid, path, model, c, u, cwd in rows:
            if cwd_sub not in (cwd or ""):
                continue
            created = dt.datetime.fromtimestamp(c).astimezone()
            if (since and created < since) or (until and created > until):
                continue
            last = None
            try:
                with open(path, encoding="utf-8") as f:
                    for line in f:
                        if '"token_count"' in line:
                            p = json.loads(line).get("payload") or {}
                            if (p.get("info") or {}).get("total_token_usage"):
                                last = p["info"]["total_token_usage"]
            except OSError:
                continue
            if not last:
                continue
            cached = int(last.get("cached_input_tokens", 0))
            yield "codex", model or "?", {"input": int(last.get("input_tokens", 0)) - cached, "cached": cached,
                                          "output": int(last.get("output_tokens", 0)), "calls": 1}, tid


def claude_usage(cwd_sub: str, since, until) -> Iterable[Tuple[str, str, dict, str]]:
    for f in glob.glob(str(HOME / ".claude/projects/*/*.jsonl")):
        try:
            fh = open(f, encoding="utf-8")
        except OSError:
            continue
        with fh:
            for line in fh:
                if '"usage"' not in line or cwd_sub not in line:
                    continue
                try:
                    e = json.loads(line)
                except ValueError:
                    continue
                msg = e.get("message") or {}
                u = msg.get("usage")
                if e.get("type") != "assistant" or not u or cwd_sub not in (e.get("cwd") or ""):
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


# ---------- сводка ----------
def summarize(records: Iterable[Tuple[str, str, dict, str]], prices: List[dict]) -> dict:
    agg: Dict[Tuple[str, str], dict] = defaultdict(lambda: {"input": 0, "cached": 0, "output": 0, "calls": 0})
    for env, model, u, _ in records:
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
    return {"rows": rows, "total_usd": round(total, 4), "unpriced": unpriced}


def render(s: dict, title: str, usd_rub: Optional[float]) -> str:
    out = [f"## {title}", "", "| Среда | Модель | Вызовы/потоки | Вход (без кэша) | Кэш | Выход | USD |",
           "|---|---|---|---|---|---|---|"]
    for r in s["rows"]:
        c = f"{r['usd']:.4f}" if r["usd"] is not None else "нет цены"
        out.append(f"| {r['env']} | {r['model']} | {r['calls']} | {r['input']:,} | {r['cached']:,} | {r['output']:,} | {c} |"
                   .replace(",", " "))
    tot = f"**Итого (API-эквивалент): ${s['total_usd']:.4f}**"
    if usd_rub:
        tot += f" ≈ {s['total_usd'] * usd_rub:.2f} ₽ (курс {usd_rub}, указан вручную)"
    out += ["", tot]
    if s["unpriced"]:
        out.append(f"Без цены (в итог не вошли): {', '.join(s['unpriced'])} — добавить в knowledge/prices.yaml.")
    out.append(f"Цены: knowledge/prices.yaml (официальные страницы вендоров). Подписка — не цена API.")
    return "\n".join(out)


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Стоимость прогона по журналам сред и официальным ценам")
    ap.add_argument("--cwd", help="часть пути рабочей папки сессии (Codex, Claude Code)")
    ap.add_argument("--zcode", action="store_true", help="учесть журналы ZCode")
    ap.add_argument("--match", help="для ZCode: строка, которая должна встречаться в сессии (например, путь проекта)")
    ap.add_argument("--since")
    ap.add_argument("--until")
    ap.add_argument("--usd-rub", type=float, help="курс для пересчёта в рубли (указывается явно)")
    ap.add_argument("--write", type=Path, help="дописать отчёт в <проект>/state/cost_report.md")
    ap.add_argument("--project", type=Path, help="учесть вызовы delegate.py из run_log проекта (их нет в журналах сред)")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    if not args.cwd and not args.zcode and not args.project:
        print("Укажите --cwd, --zcode и/или --project", file=sys.stderr)
        return 2
    since, until = parse_time(args.since), parse_time(args.until)
    recs: List = []
    if args.cwd:
        recs += list(codex_usage(args.cwd, since, until)) + list(claude_usage(args.cwd, since, until))
    if args.zcode:
        recs += list(zcode_usage(args.match, since, until))
    proj = args.project or args.write
    if proj:
        recs += list(delegated_usage(proj, since, until))
    s = summarize(recs, load_prices())
    if args.json:
        print(json.dumps(s, ensure_ascii=False, indent=2))
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


if __name__ == "__main__":
    sys.exit(main())

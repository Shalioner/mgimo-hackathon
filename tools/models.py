#!/usr/bin/env python3
"""Самообновление маршрутизации: какие модели реально доступны, какие версии новее, какие гайды устарели.

    python tools/models.py scan            # собрать инвентарь моделей этой машины → knowledge/models.inventory.yaml
    python tools/models.py resolve         # какая модель выбрана для каждого семейства и уровня (шаблон → новейшая версия)
    python tools/models.py check [--brief] # новые версии, устаревшие гайды, модели без гайда; при старом инвентаре — пересканировать
    python tools/models.py apply           # обновить запасные ID (pin) в routing.yaml на найденные новейшие версии
    python tools/models.py hints <семейство>  # подсказки из гайдов вендора для промптов делегата

Принцип: в routing.yaml семейство может задавать уровень шаблоном {pattern|regex, pin}. Модель выбирается как
самая новая версия из инвентаря, подходящая под шаблон; если инвентаря или совпадений нет — используется pin.
Вышла новая версия (например, Fable 5.2 → 5.3) — она подхватится без правки файлов, а check об этом сообщит.
"""
from __future__ import annotations

import argparse
import datetime as dt
import fnmatch
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import yaml

ROOT = Path(__file__).resolve().parent.parent
KNOW = ROOT / "knowledge"
INVENTORY = KNOW / "models.inventory.yaml"
GUIDES = KNOW / "model_guides"
ROUTING = KNOW / "routing.yaml"
HOME = Path.home()
TIERS = ["T1", "T2", "T3", "T4"]


# ---------- инвентарь ----------
def scan_codex() -> List[str]:
    f = HOME / ".codex" / "models_cache.json"
    if not f.exists():
        return []
    try:
        d = json.loads(f.read_text(encoding="utf-8"))
        items = d.get("models", d) if isinstance(d, dict) else d
        return [m.get("slug") or m.get("id") for m in items if isinstance(m, dict) and (m.get("slug") or m.get("id"))]
    except (ValueError, OSError):
        return []


def scan_opencode() -> List[str]:
    if not shutil.which("opencode"):
        return []
    try:
        r = subprocess.run(["opencode", "models"], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return []
    return [l.strip() for l in r.stdout.splitlines() if re.match(r"^[\w.\-]+/[\w.\-:#]+$", l.strip())]


def scan_zcode() -> List[str]:
    ids: List[str] = []
    cfg = HOME / ".zcode" / "v2" / "config.json"
    if cfg.exists():
        try:
            for p in json.loads(cfg.read_text(encoding="utf-8")).get("provider", {}).values():
                ids += list((p.get("models") or {}).keys())
        except (ValueError, OSError):
            pass
    builtin = Path("/Applications/ZCode.app/Contents/Resources/config/provider/zcode-builtin.json")
    if builtin.exists():
        try:
            rules = json.loads(builtin.read_text(encoding="utf-8"))["config"]["providerConfigRules"]["templateRules"]
            for r in rules:
                ids += r.get("config", {}).get("builtinModelIds", [])
        except (ValueError, OSError, KeyError):
            pass
    return list(dict.fromkeys(ids))


def scan() -> dict:
    inv = {"scanned_at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
           "sources": {"codex": scan_codex(), "opencode": scan_opencode(), "zcode": scan_zcode()}}
    INVENTORY.write_text("# Инвентарь моделей этой машины (генерируется tools/models.py scan, не в git)\n"
                         + yaml.safe_dump(inv, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return inv


def load_inventory() -> Optional[dict]:
    if not INVENTORY.exists():
        return None
    try:
        return yaml.safe_load(INVENTORY.read_text(encoding="utf-8"))
    except yaml.YAMLError:
        return None


def all_ids(inv: Optional[dict]) -> List[str]:
    if not inv:
        return []
    out: List[str] = []
    for ids in (inv.get("sources") or {}).values():
        out += ids or []
    return list(dict.fromkeys(out))


# ---------- выбор версии ----------
def version_key(model_id: str) -> Tuple[int, ...]:
    """Первая числовая группа: gpt-6.1-sol → (6, 1); claude-fable-5-2 → (5, 2); GLM-5.3-Flash → (5, 3)."""
    m = re.search(r"(\d{1,3}(?:[.\-]\d{1,3})*)", model_id)
    return tuple(int(x) for x in re.split(r"[.\-]", m.group(1))) if m else ()


def matches(spec: dict, model_id: str) -> bool:
    if spec.get("regex"):
        return re.search(spec["regex"], model_id) is not None
    if spec.get("pattern"):
        return fnmatch.fnmatch(model_id.lower(), spec["pattern"].lower())
    return False


def resolve_spec(spec, inventory_ids: List[str]) -> Tuple[Optional[str], str]:
    """(модель, как выбрана). spec — строка (фиксированный ID/алиас) или {pattern|regex, pin}."""
    if spec is None or isinstance(spec, str):
        return spec, "фиксировано"
    cands = [m for m in inventory_ids if matches(spec, m)]
    if cands:
        best = max(cands, key=lambda m: (version_key(m), m == spec.get("pin")))
        return best, "новейшая по шаблону из инвентаря"
    return spec.get("pin"), "запасной ID (в инвентаре совпадений нет)"


def resolve_all(cfg: dict, inv: Optional[dict]) -> Dict[str, Dict[str, Tuple[Optional[str], str]]]:
    ids = all_ids(inv)
    return {fam: {t: resolve_spec(tiers.get(t), ids) for t in TIERS}
            for fam, tiers in (cfg.get("families") or {}).items()}


# ---------- гайды ----------
def load_guides() -> List[dict]:
    out = []
    for f in sorted(GUIDES.glob("*.md")):
        text = f.read_text(encoding="utf-8")
        if not text.startswith("---"):
            continue
        try:
            meta = yaml.safe_load(text.split("---", 2)[1]) or {}
        except yaml.YAMLError:
            continue
        meta["_file"] = f.name
        out.append(meta)
    return out


def guide_for(model_id: str, guides: List[dict]) -> Optional[dict]:
    """Гайд вендора для модели; префикс провайдера (opencode/…) при сопоставлении отбрасывается."""
    names = {model_id.lower(), model_id.split("/")[-1].lower()}
    for g in guides:
        if any(fnmatch.fnmatch(n, str(p).lower()) for p in g.get("covers") or [] for n in names):
            return g
    return None


def is_stale(g: dict, today: dt.date) -> bool:
    checked = g.get("checked")
    if isinstance(checked, str):
        checked = dt.date.fromisoformat(checked)
    return not checked or (today - checked).days > int(g.get("refresh_after_days", 60))


def hints(family: str) -> List[str]:
    out: List[str] = []
    for g in load_guides():
        if family in (g.get("families") or []):
            out += [h for h in (g.get("delegate_hints") or []) if h]
    return list(dict.fromkeys(out))


# ---------- check / apply ----------
def check(cfg: dict, inv: Optional[dict], today: Optional[dt.date] = None) -> List[str]:
    today = today or dt.date.today()
    msgs: List[str] = []
    guides = load_guides()
    for fam, tiers in resolve_all(cfg, inv).items():
        for t, (model, how) in tiers.items():
            spec = (cfg["families"][fam] or {}).get(t)
            if isinstance(spec, dict) and model and spec.get("pin") and model != spec["pin"]:
                msgs.append(f"НОВАЯ ВЕРСИЯ: {fam}/{t}: {spec['pin']} → {model} (используется автоматически; "
                            f"запасной ID обновит `models.py apply`)")
            if model:
                g = guide_for(model, guides)
                if not g:
                    msgs.append(f"НЕТ ГАЙДА: {fam}/{t} → {model} — добавить в covers гайда вендора (skills/model-refresh)")
                elif is_stale(g, today):
                    msgs.append(f"ГАЙД УСТАРЕЛ: {g['_file']} (проверен {g.get('checked')}, срок {g.get('refresh_after_days')} дн.) "
                                f"— перечитать источники (skills/model-refresh)")
    return list(dict.fromkeys(msgs))


def apply_pins(cfg: dict, inv: Optional[dict]) -> List[str]:
    text = ROUTING.read_text(encoding="utf-8")
    changed = []
    for fam, tiers in resolve_all(cfg, inv).items():
        for t, (model, _) in tiers.items():
            spec = (cfg["families"][fam] or {}).get(t)
            if isinstance(spec, dict) and model and spec.get("pin") and model != spec["pin"]:
                old = f"pin: {spec['pin']}"
                if text.count(old) >= 1:
                    text = text.replace(old, f"pin: {model}", 1)
                    changed.append(f"{fam}/{t}: {spec['pin']} → {model}")
    if changed:
        ROUTING.write_text(text, encoding="utf-8")
    return changed


def inventory_age_hours(inv: Optional[dict]) -> Optional[float]:
    if not inv or not inv.get("scanned_at"):
        return None
    try:
        ts = dt.datetime.fromisoformat(str(inv["scanned_at"]))
    except ValueError:
        return None
    return (dt.datetime.now(ts.tzinfo) - ts).total_seconds() / 3600


def main(argv=None) -> int:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import ledger as L  # noqa: E402

    ap = argparse.ArgumentParser(description="Инвентарь моделей и самообновление маршрутизации")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("scan")
    sub.add_parser("resolve")
    p = sub.add_parser("check")
    p.add_argument("--brief", action="store_true", help="коротко, для хука; пересканировать, если инвентарь старше суток")
    sub.add_parser("apply")
    p = sub.add_parser("hints")
    p.add_argument("family")
    args = ap.parse_args(argv)
    cfg = L.load_routing()

    if args.cmd == "scan":
        inv = scan()
        for k, v in inv["sources"].items():
            print(f"{k}: {len(v)} моделей")
        print(f"Записано: {INVENTORY.relative_to(ROOT)}")
        return 0
    inv = load_inventory()
    if args.cmd == "resolve":
        if not inv:
            print("Инвентаря нет — используются запасные ID. Выполните: python tools/models.py scan")
        for fam, tiers in resolve_all(cfg, inv).items():
            row = "; ".join(f"{t}={m or '—'}" for t, (m, _) in tiers.items())
            print(f"- {fam}: {row}")
        return 0
    if args.cmd == "check":
        age = inventory_age_hours(inv)
        if args.brief and (age is None or age > 24):
            inv = scan()
        msgs = check(cfg, inv)
        if args.brief:
            if msgs:
                print("Модели и гайды (models.py check):\n" + "\n".join(f"  ! {m}" for m in msgs[:8]))
        else:
            print("\n".join(msgs) if msgs else "Новых версий нет, гайды актуальны.")
        return 0
    if args.cmd == "apply":
        ch = apply_pins(cfg, inv)
        print("Обновлены запасные ID:\n" + "\n".join(f"  {c}" for c in ch) if ch else "Обновлять нечего.")
        return 0
    if args.cmd == "hints":
        print("\n".join(hints(args.family)) or "(нет подсказок)")
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())

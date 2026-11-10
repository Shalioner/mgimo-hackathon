#!/usr/bin/env python3
"""Самообновление маршрутизации: какие модели реально доступны, какие версии новее, какие гайды устарели.

    python tools/models.py scan            # собрать инвентарь моделей этой машины → knowledge/models.inventory.yaml
    python tools/models.py resolve         # какая модель выбрана для каждого семейства и уровня (шаблон → новейшая версия)
    python tools/models.py check [--brief] # новые версии, устаревшие гайды, модели без гайда; при старом инвентаре — пересканировать
    python tools/models.py apply           # обновить запасные ID (pin) в routing.yaml на найденные новейшие версии
    python tools/models.py hints <семейство>  # подсказки из гайдов вендора для промптов делегата
    python tools/models.py specialized     # специализированные модели (домен, ограниченный доступ) — не для общего роутинга
    python tools/models.py ack <id> [...]  # отметить новые модели просмотренными (например, чужой вендор)

Принцип: в routing.yaml семейство может задавать уровень шаблоном {pattern|regex, pin}. Модель выбирается как
самая новая версия из инвентаря, подходящая под шаблон; если инвентаря или совпадений нет — используется pin.
Вышла новая версия (например, Fable 5.2 → 5.3) — она подхватится без правки файлов, а check об этом сообщит.

Имя может и не лечь в шаблон (не «gpt-6.2-astra», а «gpt-betelgeuse»). Такие модели scan запоминает как новые,
check показывает «НОВАЯ МОДЕЛЬ», а агент по навыку model-refresh вносит её в knowledge/model_catalog.yaml:
семейство, уровень, вид (general / specialized), домен, доступ и replaces — какую модель она сменяет.
Специализированные модели (кибербезопасность, биология, право, финансы, ревью кода, зрение, превью…) в общий
роутинг не попадают, даже если имя совпало с шаблоном: их предлагают человеку только под задачу своего домена.
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
def _codex_items() -> List[dict]:
    f = HOME / ".codex" / "models_cache.json"
    if not f.exists():
        return []
    try:
        d = json.loads(f.read_text(encoding="utf-8"))
        items = d.get("models", d) if isinstance(d, dict) else d
        return [m for m in items if isinstance(m, dict) and (m.get("slug") or m.get("id"))]
    except (ValueError, OSError):
        return []


def scan_codex() -> List[str]:
    return [m.get("slug") or m.get("id") for m in _codex_items()]


def scan_codex_efforts() -> Dict[str, List[str]]:
    """Уровни effort, которые Codex сам сообщает для каждой модели (supported_reasoning_levels)."""
    out = {}
    for m in _codex_items():
        lv = [x.get("effort") for x in m.get("supported_reasoning_levels") or [] if isinstance(x, dict)]
        if lv:
            out[m.get("slug") or m.get("id")] = [x for x in lv if x]
    return out


def scan_selfhosted(cfg: Optional[dict]) -> List[str]:
    """Модели своего OpenAI-совместимого сервера (Ollama, vLLM, LM Studio, корпоративный шлюз): GET {base_url}/models."""
    sh = (cfg or {}).get("selfhosted") or {}
    if not sh.get("base_url"):
        return []
    import os
    import urllib.request
    req = urllib.request.Request(sh["base_url"].rstrip("/") + "/models")
    key = os.environ.get(sh.get("api_key_env") or "", "")
    if key:
        req.add_header("Authorization", f"Bearer {key}")
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            data = json.loads(r.read().decode("utf-8"))
    except (OSError, ValueError):
        return []
    return [m.get("id") for m in data.get("data", []) if isinstance(m, dict) and m.get("id")]


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


def scan(cfg: Optional[dict] = None) -> dict:
    old = load_inventory()
    sources = {"codex": scan_codex(), "opencode": scan_opencode(), "zcode": scan_zcode()}
    sh = scan_selfhosted(cfg)
    if sh:
        sources["selfhosted"] = sh
    inv = {"scanned_at": dt.datetime.now().astimezone().isoformat(timespec="seconds"), "sources": sources,
           "efforts": scan_codex_efforts()}
    new_ids = set(all_ids(inv))
    if old:  # первый скан — точка отсчёта; дальше копятся модели, которых раньше не было
        added = new_ids - set(all_ids(old))
        inv["unreviewed"] = sorted((set(old.get("unreviewed") or []) | added) & new_ids)
    else:
        inv["unreviewed"] = []
    save_inventory(inv)
    return inv


def save_inventory(inv: dict) -> None:
    INVENTORY.write_text("# Инвентарь моделей этой машины (генерируется tools/models.py scan, не в git)\n"
                         + yaml.safe_dump(inv, allow_unicode=True, sort_keys=False), encoding="utf-8")


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


CATALOG = KNOW / "model_catalog.yaml"
# Слова в ID, по которым модель считается специализированной, если её нет в каталоге (переопределяется в
# routing.yaml → model_selection.specialized_tokens). Такие модели не выбираются общими шаблонами.
DEFAULT_SPECIALIZED = ["cyber", "security", "rosalind", "bio", "med", "medical", "health", "law", "legal",
                       "fin", "finance", "codex", "review", "vision", "vl", "omni", "audio", "realtime", "tts",
                       "image", "embed", "embedding", "exp", "preview", "guard", "safety"]
EFFORT_ORDER = ["none", "minimal", "low", "medium", "high", "xhigh", "max"]
# max (и ultra у Codex) не подставляются при переводе лестницы: цена и непредсказуемость действий растут резко,
# поэтому такие уровни — только если явно указаны и человек согласен (routing.yaml → effort_policy).
NO_AUTO_EFFORT = {"max", "ultra"}


def tokens(model_id: str) -> set:
    return set(t for t in re.split(r"[-_./:\s]+", model_id.lower()) if t)


def load_catalog(path: Optional[Path] = None) -> List[dict]:
    f = path or CATALOG
    if not f.exists():
        return []
    try:
        return [e for e in (yaml.safe_load(f.read_text(encoding="utf-8")) or {}).get("models") or [] if e.get("id")]
    except yaml.YAMLError:
        return []


def catalog_entry(model_id: str, catalog: List[dict]) -> Optional[dict]:
    names = {model_id.lower(), model_id.split("/")[-1].lower()}
    for e in catalog:
        if any(fnmatch.fnmatch(n, str(e["id"]).lower()) for n in names):
            return e
    return None


def is_specialized(model_id: str, catalog: List[dict], specialized_tokens: Optional[List[str]] = None) -> bool:
    e = catalog_entry(model_id, catalog)
    if e:
        return e.get("kind", "general") != "general"
    return bool(tokens(model_id) & set(specialized_tokens or DEFAULT_SPECIALIZED))


def matches(spec: dict, model_id: str) -> bool:
    if spec.get("regex"):
        return re.search(spec["regex"], model_id) is not None
    if spec.get("pattern"):
        return fnmatch.fnmatch(model_id.lower(), spec["pattern"].lower())
    return False


def _drop_replaced(cands: List[str], catalog: List[dict]) -> List[str]:
    """Убрать модели, которые сменила другая доступная модель (replaces в каталоге, по цепочке)."""
    replaced = set()
    for c in cands:
        seen, e = set(), catalog_entry(c, catalog)
        while e and e.get("replaces") and e["replaces"] not in seen:
            seen.add(e["replaces"])
            replaced.add(e["replaces"])
            e = catalog_entry(e["replaces"], catalog)
    return [c for c in cands if c not in replaced] or cands


def resolve_spec(spec, inventory_ids: List[str], catalog: Optional[List[dict]] = None,
                 family: Optional[str] = None, tier: Optional[str] = None,
                 specialized_tokens: Optional[List[str]] = None) -> Tuple[Optional[str], str]:
    """(модель, как выбрана). spec — строка (фиксированный ID/алиас), {pattern|regex, pin} или список вариантов
    по предпочтению (первый доступный; например, Mythos при доступе, иначе Fable)."""
    catalog = catalog or []
    if isinstance(spec, list):
        for opt in spec:
            m, how = resolve_spec(opt, inventory_ids, catalog, family, tier, specialized_tokens)
            if m and (m in inventory_ids or (isinstance(opt, str) and opt == spec[-1])):
                return m, how
        last = spec[-1] if spec else None
        return resolve_spec(last, inventory_ids, catalog, family, tier, specialized_tokens) if last is not None else (None, "не задано")
    if spec is None or isinstance(spec, str):
        return spec, "фиксировано"
    cands = [m for m in inventory_ids if matches(spec, m) and not is_specialized(m, catalog, specialized_tokens)
             and not _ranked(catalog_entry(m, catalog))]
    if family and tier:  # модели из каталога, чьё имя не ложится в шаблон и которые сменяют модель уровня
        cands += [m for m in inventory_ids if m not in cands and (e := catalog_entry(m, catalog))
                  and e.get("family") == family and e.get("tier") == tier and e.get("kind", "general") == "general"
                  and not _ranked(e)]
    if cands:
        cands = _drop_replaced(cands, catalog)
        best = max(cands, key=lambda m: (version_key(m), m == spec.get("pin")))
        return best, "новейшая по шаблону/каталогу из инвентаря"
    return spec.get("pin"), "запасной ID (в инвентаре совпадений нет)"


def _ranked(e: Optional[dict]) -> bool:
    """Модель из каталога, поставленная выше/ниже другой (above/below), — отдельная ступень уровня, а не замена."""
    return bool(e and (e.get("above") or e.get("below")))


def tier_models(cfg: dict, family: str, tier: str, inv=False, catalog: Optional[List[dict]] = None) -> List[str]:
    """Модели уровня от первой (стартовой) к последней (сильнее и дороже). Основа — resolve_for; модели каталога
    с above/below встают рядом с указанной моделью: below — перед ней (дешевле), above — после (сильнее)."""
    inv = load_inventory() if inv is False else inv
    catalog = load_catalog() if catalog is None else catalog
    base = resolve_for(cfg, family, tier, inv, catalog)[0]
    out = [base] if base else []
    ids = family_ids(cfg, family, inv)
    for m in ids:
        e = catalog_entry(m, catalog)
        if not (_ranked(e) and e.get("family") == family and e.get("tier") == tier
                and e.get("kind", "general") == "general") or m in out:
            continue
        anchor = e.get("above") or e.get("below")
        if anchor in out:
            i = out.index(anchor)
            out.insert(i + 1 if e.get("above") else i, m)
        elif e.get("above"):
            out.append(m)
        else:
            out.insert(0, m)
    return out


def family_ids(cfg: dict, family: str, inv: Optional[dict]) -> List[str]:
    """ID моделей, доступных семейству: из своего источника инвентаря (inventory: codex), иначе из всех."""
    src = ((cfg.get("families") or {}).get(family) or {}).get("inventory")
    if src and inv:
        srcs = [src] if isinstance(src, str) else src
        out: List[str] = []
        for s in srcs:
            out += (inv.get("sources") or {}).get(s) or []
        return out
    return all_ids(inv)


def _tokens_cfg(cfg: dict) -> Optional[List[str]]:
    return (cfg.get("model_selection") or {}).get("specialized_tokens")


def resolve_for(cfg: dict, family: str, tier: str, inv=False, catalog: Optional[List[dict]] = None):
    inv = load_inventory() if inv is False else inv
    catalog = load_catalog() if catalog is None else catalog
    spec = ((cfg.get("families") or {}).get(family) or {}).get(tier)
    return resolve_spec(spec, family_ids(cfg, family, inv), catalog, family, tier, _tokens_cfg(cfg))


def resolve_all(cfg: dict, inv: Optional[dict]) -> Dict[str, Dict[str, Tuple[Optional[str], str]]]:
    catalog = load_catalog()
    return {fam: {t: resolve_for(cfg, fam, t, inv, catalog) for t in TIERS}
            for fam in (cfg.get("families") or {})}


# ---------- effort ----------
def effort_levels(cfg: dict, family: Optional[str], model: Optional[str], inv=False) -> List[str]:
    """Какие уровни effort поддерживает модель: сначала из инвентаря (Codex сообщает сам), затем из гайда вендора."""
    inv = load_inventory() if inv is False else inv
    if model and inv and (inv.get("efforts") or {}).get(model):
        return list(inv["efforts"][model])
    for g in load_guides():
        if (family and family in (g.get("families") or [])) or (model and guide_for(model, [g])):
            if g.get("effort_levels"):
                return list(g["effort_levels"])
    return []


def map_efforts(wanted: List[str], supported: List[str]) -> List[str]:
    """Перевести желаемую лестницу effort на уровни модели: ближайший поддерживаемый (при равенстве — выше)."""
    if not supported:
        return []
    sup = [s for s in supported if s in EFFORT_ORDER]
    if not sup:
        return []
    out: List[str] = []
    for w in wanted:
        if w in sup:
            pick = w
        elif w in EFFORT_ORDER:
            wi = EFFORT_ORDER.index(w)
            pool = [s for s in sup if s not in NO_AUTO_EFFORT] or sup
            pick = min(pool, key=lambda s: (abs(EFFORT_ORDER.index(s) - wi), -EFFORT_ORDER.index(s)))
        else:
            continue
        if pick not in out:
            out.append(pick)
    return out


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
    catalog = load_catalog()
    for mid in (inv or {}).get("unreviewed") or []:
        if catalog_entry(mid, catalog) or any(
                isinstance(s, dict) and matches(s, mid)
                for tiers in (cfg.get("families") or {}).values() if isinstance(tiers, dict)
                for t, s in tiers.items() if t in TIERS):
            continue
        g = guide_for(mid, guides)
        kind = "похожа на специализированную" if is_specialized(mid, catalog, _tokens_cfg(cfg)) else "не попадает в шаблоны"
        msgs.append(f"НОВАЯ МОДЕЛЬ: {mid} ({'вендор ' + g['vendor'] if g else 'вендор неизвестен'}; {kind}) — "
                    f"классифицировать в knowledge/model_catalog.yaml или `models.py ack` (skills/model-refresh)")
    return list(dict.fromkeys(msgs))


def specialized(cfg: dict, inv: Optional[dict]) -> List[str]:
    catalog = load_catalog()
    out = []
    ids = all_ids(inv)
    for e in catalog:
        if e.get("kind", "general") != "general":
            here = [m for m in ids if catalog_entry(m, [e])]
            out.append(f"{e['id']}: {e.get('kind')}, домен {e.get('domain', '—')}, доступ {e.get('access', 'открытый')}"
                       f"{'; на машине: ' + ', '.join(here) if here else '; на машине нет'} — {e.get('note', '')}".rstrip(" —"))
    for m in ids:
        if not catalog_entry(m, catalog) and is_specialized(m, catalog, _tokens_cfg(cfg)):
            out.append(f"{m}: по имени похожа на специализированную (не в каталоге)")
    return out


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
    sub.add_parser("specialized")
    p = sub.add_parser("ack")
    p.add_argument("ids", nargs="+")
    args = ap.parse_args(argv)
    cfg = L.load_routing()

    if args.cmd == "scan":
        inv = scan(cfg)
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
            inv = scan(cfg)
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
    if args.cmd == "specialized":
        print("\n".join(specialized(cfg, inv)) or "Специализированных моделей не найдено.")
        return 0
    if args.cmd == "ack":
        if not inv:
            print("Инвентаря нет — сначала scan")
            return 2
        inv["unreviewed"] = [m for m in inv.get("unreviewed") or [] if m not in set(args.ids)]
        save_inventory(inv)
        print(f"Отмечено просмотренными: {', '.join(args.ids)}")
        return 0
    if args.cmd == "hints":
        print("\n".join(hints(args.family)) or "(нет подсказок)")
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env bash
# T05: репетиция — полный прогон Стратега на новой задаче, с перерывом и модельными респондентами.
. "$(dirname "$0")/lib.sh"
echo "T05"
P=projects/rehearsal-kids-watch
need "test -d $P/state/ledger" "проект $P создан"
r=$($PY tools/ledger.py validate $P 2>&1 | tail -1)
if echo "$r" | grep -q 'ошибок 0'; then ok "validate: $r"; else bad "validate: $r"; fi
for n in 01 02 03 04 05 06 07 08 09; do
  need "ls $P/outputs/${n}_*.md" "есть outputs/${n}_*.md"
done
need "grep -q -E 'Среда:\s*codex' $P/state/progress.md" "progress.md: Среда: codex"
need "grep -q 'Итого' $P/state/cost_report.md" "cost_report.md с итогом"

v=$($PY tools/verify.py $P --files 03 04 05 06 07 2>&1)
rate=$(echo "$v" | grep 'Claim support rate' | grep -o -E '[0-9]+([.,][0-9]+)?' | head -1 | tr , .)
if [ -n "$rate" ] && awk "BEGIN{exit !($rate >= 90)}"; then ok "claim support $rate% ≥ 90%"; else bad "claim support ${rate:-?}% < 90%"; fi
if echo "$v" | grep -q -E '\[unsupported\] 0[56]_'; then bad "есть unsupported в 05/06"; else ok "в 05/06 нет unsupported"; fi
if echo "$v" | grep -q 'dup-main'; then bad "есть dup-main"; else ok "нет dup-main"; fi

$PY - "$P" <<'PYEOF'
import sys, re, pathlib, yaml
P = pathlib.Path(sys.argv[1]); L = P / "state/ledger"; fail = 0
def bad(m):
    global fail; fail = 1; print("  ✗", m)
def ok(m): print("  ✓", m)
def load(n):
    f = L / f"{n}.yaml"
    return (yaml.safe_load(f.read_text(encoding="utf-8")) or []) if f.exists() else []
src = load("sources"); clm = load("claims"); hyp = load("hypotheses"); alt = load("alternatives"); dec = load("decisions")
syn = {s["id"] for s in src if s.get("kind") == "synthetic_interview"}
(ok if len(syn) >= 1 else bad)(f"синтетических интервью {len(syn)} ≥ 1")
(ok if len(src) <= 15 else bad)(f"источников {len(src)} ≤ 15")
(ok if all(s.get("access") in ("open", "own") for s in src) else bad)("все источники open/own")
badfact = [c["id"] for c in clm if c.get("type") == "fact" and any(e.get("source") in syn for e in (c.get("evidence") or []))]
(ok if not badfact else bad)(f"нет фактов на синтетике {badfact or ''}")
syn_clm = {c["id"] for c in clm if any(e.get("source") in syn for e in (c.get("evidence") or []))}
def refs(h):
    s = str(h)
    return any(re.search(rf"\b{x}\b", s) for x in syn | syn_clm)
hs = [h for h in hyp if refs(h)]
(ok if len(hs) >= 2 else bad)(f"гипотез со ссылкой на синтетику {len(hs)} ≥ 2")
kinds = {a.get("kind") for a in alt}
(ok if len(alt) >= 3 and {"status_quo", "kill"} <= kinds else bad)(f"ALT {len(alt)} ≥ 3, есть status_quo и kill ({sorted(k for k in kinds if k)})")
(ok if all(a.get("objections") for a in alt) else bad)("у каждого ALT есть возражения")
d = [x for x in dec if x.get("chosen")]
(ok if d and all("имитац" in str(x.get("decided_by", "")) for x in d) else bad)("DEC: chosen есть, decided_by помечен как имитация")
(ok if d and all(x.get("recommended") for x in d) else bad)("DEC: агент дал recommended")
log = (P / "state/run_log.md").read_text(encoding="utf-8").splitlines()
rows = [l for l in log if re.match(r"^\|\s*\d{4}-", l)]
(ok if any("имитирован" in l for l in rows) else bad)("run_log: ответы руководителя помечены «имитирован»")
idx = [i for i, l in enumerate(rows) if "продолжение после перерыва" in l]
if not idx:
    bad("run_log: нет строки «продолжение после перерыва»")
else:
    after = rows[idx[0] + 1:]
    steps = [l.split("|")[2].strip() for l in after]
    early = [s for s in steps if s in ("1", "2", "3", "4", "5")]
    (ok if not early else bad)(f"после перерыва нет строк шагов 1–5 ({len(early)} найдено)")
    (ok if any(s in ("6", "7", "8") for s in steps) else bad)("после перерыва есть шаги 6–8")
tiers = {l.split("|")[4].strip() for l in rows if len(l.split("|")) > 5}
(ok if "T1" in tiers else bad)(f"в журнале есть работа на T1 (маршрутизация): {sorted(tiers)}")
sys.exit(fail)
PYEOF
[ $? -eq 0 ] || FAIL=1
need "test -s docs/night/reports/T05.md" "есть отчёт T05"
finish

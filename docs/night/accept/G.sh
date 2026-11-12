#!/usr/bin/env bash
# G: приёмка выдачи придумывателя (5 идей по шаблону). Использование:
#   bash docs/night/accept/G.sh               # текущая ветка
#   bash docs/night/accept/G.sh night/G-2     # ветка придумывателя
#   bash docs/night/accept/G.sh --all         # все идеи + verdicts.tsv в текущей ветке (утром)
. "$(dirname "$0")/lib.sh"
MODE="${1:-}"
REF=HEAD; [ -n "$MODE" ] && [ "$MODE" != "--all" ] && REF="$MODE"
BASE=$(git merge-base night/integration "$REF" 2>/dev/null || git merge-base main "$REF")
echo "G: $REF"
if [ "$MODE" = "--all" ]; then
  files=$(git ls-tree -r --name-only "$REF" -- docs/night/ideas | grep -E 'IDEA-[0-9]{3}\.md$')
else
  files=$(git diff --name-only --diff-filter=A "$BASE" "$REF" -- docs/night/ideas | grep -E 'IDEA-[0-9]{3}\.md$')
  other=$(git diff --name-only "$BASE" "$REF" | grep -v -E '^docs/night/ideas/IDEA-[0-9]{3}\.md$|^docs/night/reports/G-[0-9]+\.md$')
  [ -z "$other" ] && ok "изменены только идеи и свой отчёт" || bad "изменено лишнее: $(echo $other)"
  n=$(echo "$files" | grep -c .)
  [ "$n" -eq 5 ] && ok "идей ровно 5" || bad "идей $n, нужно 5"
fi
echo "$files" | grep . | while read -r f; do git show "$REF:$f" > "$TMPD/$(basename "$f")"; done
git show "$REF:docs/TZ_OFFICIAL.md" > "$TMPD/TZ.md"
git show "$REF:docs/night/ideas/verdicts.tsv" > "$TMPD/verdicts.tsv" 2>/dev/null || : > "$TMPD/verdicts.tsv"

$PY - "$TMPD" "$MODE" <<'PYEOF'
import re, sys, pathlib
tmp = pathlib.Path(sys.argv[1]); mode = sys.argv[2]; fail = 0
def bad(m):
    global fail; fail = 1; print("  ✗", m)
def ok(m): print("  ✓", m)
tz = re.sub(r"\s+", " ", (tmp / "TZ.md").read_text(encoding="utf-8"))
F = ["Критерий ТЗ", "Цитата ТЗ", "Новизна", "Что сделать", "Польза, которую можно измерить", "ИИ-время", "Риск", "Зона правок", "Черновик приёмки", "Почему не дубль", "Тест МегаФона"]
FORB = re.compile(r"objection_lexicon|ledger\.schema\.json|(^|[\s`/])AGENTS\.md|\.env\b|docs/night/(tasks|accept)/")
crit = []; novel = 0
ideas = sorted(tmp.glob("IDEA-*.md"))
for f in ideas:
    t = f.read_text(encoding="utf-8"); e = []
    v = {}
    for k in F:
        m = re.search(rf"^- {re.escape(k)}:\s*(.*)$", t, re.M)
        if not m: e.append(f"поле «{k}»")
        else: v[k] = m.group(1).strip()
    c = re.match(r"кр\.\s*([1-4])", v.get("Критерий ТЗ", ""))
    if not c: e.append("критерий не кр.1–4")
    else: crit.append(c.group(1))
    q = re.search(r"«(.+?)»", v.get("Цитата ТЗ", ""))
    if not q or re.sub(r"\s+", " ", q.group(1)).strip(" .") not in tz: e.append("цитата ТЗ не найдена дословно")
    if not re.search(r"\d", v.get("Польза, которую можно измерить", "")) and "`" not in v.get("Польза, которую можно измерить", ""):
        e.append("польза без числа или команды")
    mins = re.search(r"(\d+)", v.get("ИИ-время", ""))
    if not mins: e.append("ИИ-время без числа")
    if FORB.search(v.get("Зона правок", "")): e.append("зона правок задевает запретное")
    steps = re.findall(r"^\s+\d+\.\s+`[^`]+`\s*→\s*\S", t, re.M)
    if len(steps) < 3: e.append(f"в черновике приёмки {len(steps)} проверяемых пунктов < 3")
    if v.get("Новизна", "").startswith("ни у кого"): novel += 1
    if e: bad(f"{f.name}: " + "; ".join(e))
    else: ok(f.name)
if mode != "--all" and ideas:
    from collections import Counter
    top = Counter(crit).most_common(1)
    (ok if not top or top[0][1] <= 2 else bad)(f"не больше 2 идей на один критерий ({dict(Counter(crit))})")
    (ok if novel >= 2 else bad)(f"идей «ни у кого» {novel} ≥ 2")
if mode == "--all":
    rows = [l.split("\t") for l in (tmp / "verdicts.tsv").read_text(encoding="utf-8").splitlines()[1:] if l.strip()]
    badrows = [r[0] for r in rows if len(r) != 5 or r[1] not in ("да", "нет") or (r[1] == "да" and not re.match(r"^[1-5]$", r[4]))]
    (ok if not badrows else bad)(f"verdicts.tsv: {len(rows)} строк, формат верный {badrows or ''}")
    names = {f.stem for f in ideas}
    nov = [r[0] for r in rows if r[0] not in names]
    (ok if not nov else bad)(f"вердикты только на существующие идеи {nov or ''}")
    unjudged = sorted(names - {r[0] for r in rows})
    (ok if not unjudged else bad)(f"у каждой идеи есть вердикт Astra {unjudged or ''}")
    yes = sum(1 for r in rows if len(r) > 1 and r[1] == "да")
    if rows: print(f"  · принято {yes} из {len(rows)} ({100 * yes // len(rows)}%)")
sys.exit(fail)
PYEOF
[ $? -eq 0 ] || FAIL=1
finish

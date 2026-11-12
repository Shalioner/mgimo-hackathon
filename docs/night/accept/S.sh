#!/usr/bin/env bash
# S: приёмка синтеза исследования docs/night/research/SYNTHESIS.md.
. "$(dirname "$0")/lib.sh"
echo "S"
need "test -s docs/night/research/SYNTHESIS.md" "SYNTHESIS.md существует"
$PY - <<'PYEOF'
import re, sys, pathlib
fail = 0
def bad(m):
    global fail; fail = 1; print("  ✗", m)
def ok(m): print("  ✓", m)
p = pathlib.Path("docs/night/research/SYNTHESIS.md"); t = p.read_text(encoding="utf-8"); L = t.splitlines()
for s in ["Главное", "Паттерны победителей", "Что перенять", "Кандидаты на ночную реализацию", "Чего не делать"]:
    (ok if re.search(rf"^#+\s*{s}", t, re.M) else bad)(f"раздел «{s}»")
def table(head):
    i = next((k for k, l in enumerate(L) if re.match(head, l)), None)
    if i is None: return None, []
    cols = [c.strip() for c in L[i].strip().strip("|").split("|")]; rows = []
    for l in L[i + 2:]:
        if not l.startswith("|"): break
        rows.append([c.strip() for c in l.strip().strip("|").split("|")])
    return cols, rows
cols, rows = table(r"^\|\s*#\s*\|\s*Что сделать")
want = ["#", "Что сделать", "Критерий ТЗ", "Польза (почему поднимет балл)", "ИИ-время", "Риск", "Карточки"]
(ok if cols == want else bad)(f"столбцы «Что перенять» = {want}")
(ok if 1 <= len(rows) <= 10 else bad)(f"пунктов «Что перенять» {len(rows)} (1–10)")
cards = p.parent / "cards"
missing = 0; nolink = 0
for r in rows:
    links = re.findall(r"\]\(([^)]+)\)", r[-1] if r else "")
    if not links: nolink += 1
    for l in links:
        if not (p.parent / l).resolve().exists(): missing += 1; print("    нет карточки", l)
    if r and len(r) > 2 and not re.search(r"кр\.?\s*[1-4]", r[2]): bad(f"пункт {r[0]}: критерий не кр.1–4")
(ok if nolink == 0 else bad)("у каждого пункта есть ссылка на карточку")
(ok if missing == 0 else bad)("все ссылки на карточки существуют")
pc, pr = table(r"^\|\s*Паттерн\s*\|")
(ok if pc and all(re.match(r"^\d+$", r[1]) and int(r[1]) >= 2 for r in pr if len(r) > 1) else bad)("паттерны встречаются ≥ 2 раз")
sec = re.search(r"^#+\s*Кандидаты на ночную реализацию(.*?)(?=^#+\s|\Z)", t, re.M | re.S)
body = sec.group(1) if sec else ""
cand = len(re.findall(r"^(?:#{3,}\s|\d+\.\s)", body, re.M))
(ok if cand <= 3 else bad)(f"кандидатов на реализацию {cand} ≤ 3")
forb = re.findall(r"objection_lexicon|ledger\.schema\.json|AGENTS\.md|\.env\b", body)
(ok if not forb else bad)(f"кандидаты не трогают запретные зоны {forb or ''}")
sys.exit(fail)
PYEOF
[ $? -eq 0 ] || FAIL=1
finish

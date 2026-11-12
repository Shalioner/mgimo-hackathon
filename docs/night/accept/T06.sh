#!/usr/bin/env bash
# T06: сценарий видео и доработка описания.
. "$(dirname "$0")/lib.sh"
echo "T06"
need "test -s docs/VIDEO_SCRIPT.md" "docs/VIDEO_SCRIPT.md существует"

$PY - "$(base_ref)" <<'PYEOF'
import re, sys, subprocess, pathlib
base = sys.argv[1]; fail = 0
def bad(m):
    global fail; fail = 1; print("  ✗", m)
def ok(m): print("  ✓", m)
v = pathlib.Path("docs/VIDEO_SCRIPT.md").read_text(encoding="utf-8").splitlines()
hi = [i for i, l in enumerate(v) if re.match(r"^\|\s*Таймкод\s*\|", l)]
if not hi:
    bad("нет таблицы с заголовком | Таймкод | …"); sys.exit(1)
cols = [c.strip() for c in v[hi[0]].strip().strip("|").split("|")]
(ok if cols == ["Таймкод", "Что на экране", "Что говорим", "Обязательный момент ТЗ / козырь"] else bad)("столбцы сценария")
rows = []
for l in v[hi[0] + 2:]:
    if not l.startswith("|"): break
    rows.append([c.strip() for c in l.strip().strip("|").split("|")])
def sec(t):
    m, s = t.split(":"); return int(m) * 60 + int(s)
prev = 0; gaps = 0; last = 0
for r in rows:
    m = re.match(r"^(\d+:\d{2})\s*[–-]\s*(\d+:\d{2})$", r[0])
    if not m: bad(f"таймкод не M:SS–M:SS: {r[0]}"); continue
    a, b = sec(m.group(1)), sec(m.group(2))
    if a != prev or b <= a: gaps += 1
    prev = last = b
(ok if gaps == 0 else bad)(f"таймкоды подряд без дыр (нарушений {gaps})")
(ok if 0 < last <= 570 else bad)(f"длина {last // 60}:{last % 60:02d} ≤ 9:30")
tags = " ".join(r[3] for r in rows if len(r) > 3)
for t in ["ТЗ: запрос", "ТЗ: работа агента", "ТЗ: варианты", "ТЗ: стратегия", "ТЗ: продолжение после перерыва",
          "козырь: три цифры", "козырь: перенос среды", "козырь: возражение руководителя", "козырь: report.html", "козырь: стоимость"]:
    (ok if t in tags else bad)(f"метка «{t}»")
first = [r for r in rows if "козырь: три цифры" in r[3]]
(ok if first and sec(re.match(r"^(\d+:\d{2})", first[0][0]).group(1)) < 30 else bad)("«три цифры» в первые 30 секунд")
long = []
for r in rows:
    m = re.match(r"^(\d+:\d{2})\s*[–-]\s*(\d+:\d{2})$", r[0])
    if m and len(r) > 2:
        dur = sec(m.group(2)) - sec(m.group(1))
        if len(r[2].split()) > 25 * dur / 30 + 5: long.append(r[0])
(ok if not long else bad)(f"текст читается за отведённое время (длинных строк: {long})")
t = "\n".join(v)
for s in ["Чек-лист перед записью", "Команды для кадров"]:
    (ok if s in t else bad)(f"раздел «{s}»")

d = pathlib.Path("docs/DESCRIPTION.md").read_text(encoding="utf-8")
(ok if "[TODO] при оформлении PDF" not in d else bad)("закрыт TODO про AGENTS §3 и §6")
old = subprocess.run(["git", "show", f"{base}:docs/DESCRIPTION.md"], capture_output=True, text=True).stdout
(ok if old.count("[СКРИНШОТ]") == d.count("[СКРИНШОТ]") else bad)("пометки [СКРИНШОТ] не тронуты")
(ok if "[TODO] Демо-проект МегаФона" in d else bad)("пометка [TODO] Демо-проект МегаФона на месте")
(ok if "MEASUREMENTS.md" in d else bad)("ссылка на MEASUREMENTS.md")
a = pathlib.Path("AGENTS.md").read_text(encoding="utf-8")
def block(n):
    m = re.search(rf"^## {n}\..*?(?=^## {n+1}\.)", a, re.M | re.S); return m.group(0) if m else ""
for n in (3, 6):
    lines = [l for l in block(n).splitlines() if l.strip() and not l.startswith("#")]
    miss = [l for l in lines if l not in d]
    (ok if lines and not miss else bad)(f"AGENTS §{n} вставлен дословно (не найдено строк: {len(miss)} из {len(lines)})")
sys.exit(fail)
PYEOF
[ $? -eq 0 ] || FAIL=1
need "test -s docs/night/reports/T06.md" "есть отчёт T06"
finish

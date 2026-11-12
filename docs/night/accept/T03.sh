#!/usr/bin/env bash
# T03: docs/MEASUREMENTS.md — таблица замеров с проверяемыми коммитами и файлами.
. "$(dirname "$0")/lib.sh"
echo "T03"
need "test -s docs/MEASUREMENTS.md" "docs/MEASUREMENTS.md существует"

$PY - <<'PYEOF'
import re, subprocess, sys, pathlib
fail = 0
def bad(m):
    global fail; fail = 1; print("  ✗", m)
def ok(m): print("  ✓", m)
p = pathlib.Path("docs/MEASUREMENTS.md")
t = p.read_text(encoding="utf-8")
for sec in ["Главное", "Что не измерено", "Как воспроизвести"]:
    (ok if re.search(rf"^#+\s*{sec}", t, re.M) else bad)(f"раздел «{sec}»")
lines = t.splitlines()
hdr = [i for i, l in enumerate(lines) if re.match(r"^\|\s*#\s*\|\s*Что проверяли", l)]
if not hdr:
    bad("нет таблицы с заголовком | # | Что проверяли (гипотеза) | …"); sys.exit(1)
cols = [c.strip() for c in lines[hdr[0]].strip().strip("|").split("|")]
want = ["#", "Что проверяли (гипотеза)", "Протокол до прогона", "Результат", "Решение", "Файлы"]
(ok if cols == want else bad)(f"столбцы таблицы ровно {want}")
rows = []
for l in lines[hdr[0] + 2:]:
    if not l.startswith("|"): break
    rows.append([c.strip() for c in l.strip().strip("|").split("|")])
(ok if len(rows) >= 10 else bad)(f"строк замеров {len(rows)} ≥ 10")
bad_hash = bad_file = bad_dec = no_num = 0
for r in rows:
    if len(r) != 6: bad(f"строка с {len(r)} столбцами: {r[:2]}"); continue
    proto, res, dec, files = r[2], r[3], r[4], r[5]
    for h in re.findall(r"\b[0-9a-f]{7,40}\b", proto):
        if subprocess.run(["git", "cat-file", "-e", h + "^{commit}"], capture_output=True).returncode != 0:
            bad_hash += 1; print("    нет коммита", h)
    links = re.findall(r"\]\(([^)#]+)", files)
    if not links: bad_file += 1; print("    нет ссылок на файлы в строке", r[0])
    for l in links:
        if not (p.parent / l).resolve().exists():
            bad_file += 1; print("    нет файла", l)
    if not re.match(r"(принято|частично|отклонено|остановлено)\b", dec): bad_dec += 1; print("    решение не из списка:", dec[:40])
    if not re.search(r"\d", res) and "не зафиксировано" not in res: no_num += 1; print("    в результате нет числа:", r[0])
(ok if bad_hash == 0 else bad)("все hash протоколов существуют")
(ok if bad_file == 0 else bad)("у каждой строки есть ссылки на существующие файлы")
(ok if bad_dec == 0 else bad)("решения из списка принято/частично/отклонено/остановлено")
(ok if no_num == 0 else bad)("в каждом результате есть число или «не зафиксировано»")
with_proto = sum(1 for r in rows if len(r) == 6 and re.search(r"\b[0-9a-f]{7,40}\b", r[2]))
(ok if with_proto >= 5 else bad)(f"строк с протоколом до прогона {with_proto} ≥ 5")
must = ["baseline|чат", "пересмотр", "verify", "gate", "возражен", "угодлив", "несоглас", "внедр|injection"]
low = t.lower()
for m in must:
    (ok if re.search(m, low) else bad)(f"упомянут замер: {m}")
sys.exit(fail)
PYEOF
[ $? -eq 0 ] || FAIL=1
need "test -s docs/night/reports/T03.md" "есть отчёт T03"
finish

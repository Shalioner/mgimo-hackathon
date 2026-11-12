#!/usr/bin/env bash
# T04: tools/datacheck.py на скрытых файлах приёмки (не на фикстурах реализатора).
. "$(dirname "$0")/lib.sh"
echo "T04"
need "test -f tools/datacheck.py" "tools/datacheck.py существует"
need "test -f tests/test_datacheck.py" "tests/test_datacheck.py существует"
need "$PY -m pytest -q tests/test_datacheck.py" "tests/test_datacheck.py зелёный"

$PY - "$TMPD" <<'PYEOF'
import json, subprocess, sys, pathlib
tmp = pathlib.Path(sys.argv[1]); fail = 0
py = ".venv/bin/python" if pathlib.Path(".venv/bin/python").exists() else "python3"
def bad(m):
    global fail; fail = 1; print("  ✗", m)
def ok(m): print("  ✓", m)
def run(name, text, enc="utf-8"):
    f = tmp / name; f.write_bytes(text.encode(enc))
    r = subprocess.run([py, "tools/datacheck.py", str(f), "--json"], capture_output=True, text=True)
    try: data = json.loads(r.stdout)
    except Exception: data = None
    return r.returncode, data, r.stderr
def has(data, typ, col=None, row=None):
    for i in (data or {}).get("issues", []):
        if i.get("type") == typ and (col is None or i.get("column") == col) and (row is None or row in (i.get("rows") or [])):
            return True
    return False

rc, d, e = run("clean.csv", "month,revenue\n2025-01,100\n2025-02,110\n2025-03,120\n2025-04,130\n2025-05,125\n2025-06,140\n")
(ok if rc == 0 and d is not None and d.get("issues") == [] else bad)(f"чистый файл: код 0 и issues [] (код {rc}, {str(d)[:120]})")

rc, d, e = run("missing.csv", "month,revenue\n2025-01,100\n2025-02,110\n2025-03,\n2025-04,130\n")
(ok if rc == 1 and has(d, "missing", "revenue", 4) else bad)(f"missing: revenue, строка 4 (код {rc})")

rc, d, e = run("dup.csv", "city,users\nМосква,10\nКазань,7\nСочи,5\nМосква,10\n")
(ok if rc == 1 and has(d, "duplicate", None, 5) else bad)(f"duplicate: строка 5 (код {rc})")

rc, d, e = run("unit.csv", "segment,share\nA,12 %\nB,15 %\nC,18\nD,20 %\n")
(ok if rc == 1 and has(d, "unit_mismatch", "share") else bad)(f"unit_mismatch: share (код {rc})")

rc, d, e = run("gap.csv", "month,revenue\n2025-01,100\n2025-02,110\n2025-04,130\n2025-05,140\n2025-06,150\n")
(ok if rc == 1 and has(d, "period_gap", "month") else bad)(f"period_gap: month (код {rc})")

rc, d, e = run("cp.csv", "Месяц;Выручка\n01.2025;1 234,5\n02.2025;1 300,0\n03.2025;1 410,25\n", enc="cp1251")
(ok if rc == 0 and d is not None and d.get("issues") == [] else bad)(f"cp1251 + ';' + русские числа: код 0, issues [] (код {rc}, {str(d)[:160]} {e[-160:]})")

rc, d, e = run("mix.csv", "k,v\n" + "".join(f"r{i},{i}\n" for i in range(1, 10)) + "r10,нет данных\n")
(ok if rc == 1 and has(d, "type_mix", "v") else bad)(f"type_mix: v (код {rc})")

r =subprocess.run([py, "tools/datacheck.py", str(tmp / "нет-файла.csv")], capture_output=True, text=True)
(ok if r.returncode == 2 else bad)(f"нет файла → код 2 (получен {r.returncode})")
sys.exit(fail)
PYEOF
[ $? -eq 0 ] || FAIL=1

n=$(git diff "$(base_ref)" HEAD -- skills/source-intake/SKILL.md | grep -c -E '^[+-][^+-]')
if [ "$n" -le 6 ]; then ok "правка source-intake/SKILL.md ≤ 6 строк ($n)"; else bad "правка source-intake/SKILL.md $n строк > 6"; fi
need "test -s docs/night/reports/T04.md" "есть отчёт T04"
finish

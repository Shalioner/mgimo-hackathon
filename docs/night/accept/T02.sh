#!/usr/bin/env bash
# T02: tools/report.py → один офлайн report.html со ссылками на реестр.
. "$(dirname "$0")/lib.sh"
echo "T02"

need "test -f tools/report.py" "tools/report.py существует"
need "test -f tests/test_report.py" "tests/test_report.py существует"
need "$PY -m pytest -q tests/test_report.py" "tests/test_report.py зелёный"

for p in esim-travel ryadom; do
  need "test -s projects/$p/outputs/report.html" "закоммичен projects/$p/outputs/report.html"
done

# Свежая генерация в tmp и проверки содержимого
$PY - "$TMPD" <<'PYEOF'
import re, subprocess, sys, pathlib, shutil, time
tmp = pathlib.Path(sys.argv[1]); fail = 0
def bad(m):
    global fail; fail = 1; print("  ✗", m)
def ok(m): print("  ✓", m)
ID = re.compile(r"\b(SRC|CLM|HYP|PAIN|ALT|DEC|PRM|EXP|CHG)-[0-9]{3}\b")
py = ".venv/bin/python" if pathlib.Path(".venv/bin/python").exists() else "python3"
for proj in ["esim-travel", "ryadom"]:
    out = tmp / f"{proj}.html"
    t = time.time()
    r = subprocess.run([py, "tools/report.py", f"projects/{proj}", "--out", str(out)], capture_output=True, text=True)
    dt = time.time() - t
    if r.returncode != 0 or not out.exists():
        bad(f"{proj}: report.py код {r.returncode}: {r.stderr[-300:]}"); continue
    ok(f"{proj}: сгенерирован за {dt:.1f} с")
    if dt > 10: bad(f"{proj}: генерация дольше 10 с")
    h = out.read_text(encoding="utf-8")
    md_ids = sum(len(ID.findall(f.read_text(encoding="utf-8"))) for f in sorted(pathlib.Path(f"projects/{proj}/outputs").glob("0*.md")))
    refs = len(re.findall(r'class="ref', h))
    (ok if refs >= md_ids else bad)(f"{proj}: ссылок class=ref {refs} ≥ ID в md {md_ids}")
    if re.search(r"<script[^>]+src=", h, re.I) or re.search(r"<link[^>]+href=[\"']?https?:", h, re.I) or re.search(r"@import\s+url\(\s*[\"']?https?:", h, re.I):
        bad(f"{proj}: есть внешние ресурсы")
    else: ok(f"{proj}: внешних ресурсов нет")
    (ok if "Главное" in h else bad)(f"{proj}: есть блок «Главное»")
    (ok if re.search(r'id="ref-CLM-\d{3}"', h) else bad)(f"{proj}: есть приложение «Реестр» (id=ref-CLM-…)")
    (ok if "prefers-color-scheme" in h else bad)(f"{proj}: тёмная тема")
    (ok if "@media print" in h else bad)(f"{proj}: стили печати")
    m = re.search(r'data-id="(CLM-\d{3})"[^>]*title="([^"]*)"', h) or re.search(r'title="([^"]*)"[^>]*data-id="(CLM-\d{3})"', h)
    (ok if m and len(max(m.groups(), key=len)) > 20 else bad)("у ссылки на CLM есть содержательная подсказка title")

# Несуществующий ID → ref missing; экранирование HTML
ex = tmp / "example"
shutil.copytree("projects/example", ex)
# В projects/example может не быть outputs/ — тогда создаём один md в копии
(ex / "outputs").mkdir(exist_ok=True)
mds = sorted((ex / "outputs").glob("0*.md"))
f = mds[0] if mds else ex / "outputs" / "01_brief.md"
prev = f.read_text(encoding="utf-8") if f.exists() else "# Проверка\n\n## Главное\nПроверка отчёта.\n"
f.write_text(prev + "\n\nПроверка CLM-999 и <b>тег</b>.\n", encoding="utf-8")
out = tmp / "ex.html"
r = subprocess.run([py, "tools/report.py", str(ex), "--out", str(out)], capture_output=True, text=True)
if r.returncode == 0 and out.exists():
    h = out.read_text(encoding="utf-8")
    (ok if re.search(r'class="ref missing"[^>]*>CLM-999<|data-id="CLM-999"[^>]*class="ref missing"', h) or ('ref missing' in h and 'CLM-999' in h) else bad)("CLM-999 помечен как ref missing")
    (ok if "<b>тег</b>" not in h and "&lt;b&gt;тег&lt;/b&gt;" in h else bad)("HTML в тексте экранирован")
else:
    bad(f"example-копия: код {r.returncode}: {r.stderr[-300:]}")
r = subprocess.run([py, "tools/report.py", str(tmp / "нет-такого")], capture_output=True, text=True)
(ok if r.returncode == 2 else bad)(f"нет проекта → код 2 (получен {r.returncode})")
sys.exit(fail)
PYEOF
[ $? -eq 0 ] || FAIL=1

need "! grep -n -E '^\s*(import|from)\s+(requests|urllib\.request|http\.client|socket)' tools/report.py" "report.py без сетевых модулей"
need "test -s docs/night/reports/T02.md" "есть отчёт T02"
finish

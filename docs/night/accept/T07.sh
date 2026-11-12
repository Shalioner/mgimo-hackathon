#!/usr/bin/env bash
# T07: первый экран README — три проверяемые цифры.
. "$(dirname "$0")/lib.sh"
echo "T07"
ref=night/integration; git rev-parse -q --verify "$ref" >/dev/null || ref=$(base_ref)

$PY - "$ref" <<'PYEOF'
import re, sys, subprocess, pathlib
ref = sys.argv[1]; fail = 0
py = ".venv/bin/python" if pathlib.Path(".venv/bin/python").exists() else "python3"
def bad(m):
    global fail; fail = 1; print("  ✗", m)
def ok(m): print("  ✓", m)
r = pathlib.Path("README.md").read_text(encoding="utf-8")
lines = r.splitlines()
first_h2 = next(i for i, l in enumerate(lines) if l.startswith("## "))
head = "\n".join(lines[1:first_h2])
old = subprocess.run(["git", "show", f"{ref}:README.md"], capture_output=True, text=True).stdout.splitlines()
old_h2 = next(i for i, l in enumerate(old) if l.startswith("## "))
added = first_h2 - old_h2
(ok if 0 < added <= 15 else bad)(f"новый блок {added} строк (1–15)")
(ok if lines[first_h2:] == old[old_h2:] else bad)(f"ниже первого ## README не изменён (сравнение с {ref})")
for s in ["MEASUREMENTS.md", "report.html"]:
    (ok if s in head else bad)(f"ссылка на {s} в первом экране")
money = re.findall(r"\$\s?(\d+[.,]\d{2})", head)
costs = {}
for f in pathlib.Path("projects").glob("*/state/cost_report.md"):
    m = re.findall(r"Итого[^$]*\$\s?(\d+[.,]\d+)", f.read_text(encoding="utf-8"))
    if m: costs[f.parts[1]] = [x.replace(",", ".") for x in m]
hit = [p for p, xs in costs.items() for m in money if any(abs(float(m.replace(",", ".")) - float(x)) < 0.006 for x in xs)]
(ok if hit else bad)(f"цена из README {money} совпадает с «Итого» cost_report ({hit or costs})")
pct = re.findall(r"(\d{2,3}(?:[.,]\d)?)\s?%", head)
proj = hit[0] if hit else "esim-travel"
v = subprocess.run([py, "tools/verify.py", f"projects/{proj}", "--files", "03", "04", "05", "06", "07"], capture_output=True, text=True).stdout
m = re.search(r"Claim support rate \(код\):\s*([\d.,]+)", v)
real = float(m.group(1).replace(",", ".")) if m else None
(ok if real is not None and any(abs(float(p.replace(",", ".")) - real) <= 0.6 for p in pct) else bad)(f"процент проверяемости {pct} = verify по {proj} ({real})")
(ok if re.search(r"11\s*[–-]\s*50\s?%", head) else bad)("есть сравнение с чатом 11–50%")
(ok if re.search(r"codex", head, re.I) and re.search(r"zcode", head, re.I) else bad)("названы среды с реальным прогоном (Codex, ZCode)")
sys.exit(fail)
PYEOF
[ $? -eq 0 ] || FAIL=1
need "test -s docs/night/reports/T07.md" "есть отчёт T07"
finish

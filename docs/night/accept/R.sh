#!/usr/bin/env bash
# R: приёмка выдачи исследователя. Использование:
#   bash docs/night/accept/R.sh                 # текущая ветка (воркер)
#   bash docs/night/accept/R.sh night/R-B-07    # ветка исследователя (оркестратор)
#   bash docs/night/accept/R.sh --all           # все карточки в текущей ветке (утром, на night/integration)
. "$(dirname "$0")/lib.sh"
MODE="${1:-}"
REF=HEAD; [ -n "$MODE" ] && [ "$MODE" != "--all" ] && REF="$MODE"
BASE=$(git merge-base night/integration "$REF" 2>/dev/null || git merge-base main "$REF")
echo "R: $REF (база $(echo "$BASE" | cut -c1-8))"

if [ "$MODE" = "--all" ]; then
  cards=$(git ls-tree -r --name-only "$REF" -- docs/night/research/cards | grep '\.md$')
else
  cards=$(git diff --name-only --diff-filter=A "$BASE" "$REF" -- docs/night/research/cards | grep '\.md$')
  other=$(git diff --name-only "$BASE" "$REF" | grep -v -E '^docs/night/research/cards/[^/]+\.md$|^docs/night/reports/R-[AB]-[0-9]+\.md$')
  if [ -z "$other" ]; then ok "изменены только карточки и свой отчёт"; else bad "изменено лишнее: $(echo $other)"; fi
  rep=$(git diff --name-only "$BASE" "$REF" | grep -E '^docs/night/reports/R-[AB]-[0-9]+\.md$' | head -1)
  if [ -n "$rep" ]; then
    git show "$REF:$rep" | grep -q -E 'карточек:\s*[0-9]+.*отсеяно:\s*[0-9]+.*новых зацепок:\s*[0-9]+' \
      && ok "отчёт $rep с тремя числами" || bad "в отчёте $rep нет строки «карточек: N, отсеяно: M, новых зацепок: K»"
    git show "$REF:$rep" | grep -q -E '^#+ *Новые зацепки' && ok "в отчёте есть раздел «Новые зацепки»" || bad "нет раздела «Новые зацепки»"
  else bad "нет отчёта docs/night/reports/R-<группа>-<номер>.md"; fi
fi
n=$(echo "$cards" | grep -c . )
echo "  карточек к проверке: $n"

echo "$cards" | grep . | while read -r f; do git show "$REF:$f" > "$TMPD/$(basename "$f")"; done
git show "$REF:docs/night/research/index.tsv" > "$TMPD/index.tsv" 2>/dev/null || git show "main:docs/night/research/index.tsv" > "$TMPD/index.tsv"

$PY - "$TMPD" "$MODE" <<'PYEOF'
import re, sys, pathlib, subprocess, shutil
tmp = pathlib.Path(sys.argv[1]); mode = sys.argv[2]; fail = 0
def bad(m):
    global fail; fail = 1; print("  ✗", m)
def ok(m): print("  ✓", m)
idx = set()
for l in (tmp / "index.tsv").read_text(encoding="utf-8").splitlines()[1:]:
    p = l.split("\t")
    if len(p) > 1: idx.add(p[1].strip().lower())
FIELDS = ["Группа", "URL", "Хакатон", "Результат", "Доказательство результата", "Активность", "Стек", "Проверено чтением"]
SECS = ["Как устроено", "Чем силён", "Идея для нас", "Подозрительный текст"]
have_gh = shutil.which("gh") is not None
seen = set()
for f in sorted(tmp.glob("*.md")):
    t = f.read_text(encoding="utf-8"); errs = []
    vals = {}
    for k in FIELDS:
        m = re.search(rf"^- {re.escape(k)}:\s*(.+)$", t, re.M)
        if not m or not m.group(1).strip() or m.group(1).strip().startswith("<"): errs.append(f"поле «{k}»")
        else: vals[k] = m.group(1).strip()
    for s in SECS:
        if not re.search(rf"^##\s*{s}", t, re.M): errs.append(f"раздел «{s}»")
    g = vals.get("Группа", "")
    if g not in ("A", "B"): errs.append("Группа не A/B")
    if not f.name.lower().startswith(g.lower() + "-"): errs.append("имя файла не начинается с группы")
    res = vals.get("Результат", "")
    if not re.match(r"(победитель|призёр|финалист|участник|неизвестно)\b", res): errs.append("Результат не из списка")
    if re.match(r"(победитель|призёр|финалист)", res) and not re.search(r"https?://", vals.get("Доказательство результата", "")):
        errs.append("заявлен результат без URL-доказательства")
    if not re.search(r"^- Вердикт:\s*(перенять|может быть|нет)\s*$", t, re.M): errs.append("Вердикт не перенять/может быть/нет")
    if not re.search(r"кр\.\s*[1-4]", t): errs.append("нет ссылки на критерий ТЗ (кр.1–4)")
    m = re.match(r"https?://github\.com/([^/\s]+/[^/\s#?]+)", vals.get("URL", ""))
    repo = m.group(1).lower().removesuffix(".git") if m else None
    if repo:
        if mode != "--all" and repo in idx: errs.append(f"дубль: {repo} уже в index.tsv")
        if repo in seen: errs.append(f"дубль внутри выдачи: {repo}")
        seen.add(repo)
        if have_gh and subprocess.run(["gh", "api", f"repos/{repo}", "--jq", ".full_name"], capture_output=True).returncode != 0:
            errs.append(f"репозиторий {repo} не найден через gh api")
    if errs: bad(f"{f.name}: " + "; ".join(errs))
    else: ok(f"{f.name}")
if not have_gh: print("  ! gh не найден — существование репозиториев не проверено")
sys.exit(fail)
PYEOF
[ $? -eq 0 ] || FAIL=1
finish

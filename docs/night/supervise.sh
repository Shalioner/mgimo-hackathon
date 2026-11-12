#!/usr/bin/env bash
# Ежечасный снимок ночного прогона для надзирателя (только чтение: ничего не меняет и никому не пишет).
#   bash docs/night/supervise.sh
# Печатает факты и сигналы ухода в сторону (⚠). Решение о поправке принимает надзиратель по SUPERVISOR.md.
set -u
ROOT="$(git rev-parse --show-toplevel)" || exit 2
cd "$ROOT"
I=night/integration
now=$(date +%s)
echo "== Снимок $(date '+%Y-%m-%d %H:%M')"

# 0. main не тронут, ничего не запушено
mh=$(git rev-parse main); oh=$(git rev-parse origin/main 2>/dev/null || echo "-")
echo "main: $(git log -1 --format='%h %cr %s' main | cut -c1-90)"
[ -f "$(git rev-parse --git-common-dir)/night_main_head" ] || echo "$mh" > "$(git rev-parse --git-common-dir)/night_main_head" 2>/dev/null || true
[ "$(cat "$(git rev-parse --git-common-dir)/night_main_head" 2>/dev/null)" = "$mh" ] || echo "⚠ main изменился с начала ночи"
[ "$oh" = "-" ] || [ "$(git rev-list --count "$mh..$oh")" = "0" ] || echo "⚠ origin/main впереди main — кто-то пушил?"
for b in $(git for-each-ref --format='%(refname:short)' 'refs/remotes/origin/night/*'); do echo "⚠ запушена ночная ветка $b"; done

# 1. Интеграционная ветка
if ! git rev-parse -q --verify $I >/dev/null; then echo "⚠ ветки $I нет — оркестратор не стартовал"; exit 0; fi
last=$(git log -1 --format=%ct $I); age=$(( (now - last) / 60 ))
echo "$I: $(git rev-list --count main..$I) коммитов поверх main, последний $age мин назад: $(git log -1 --format=%s $I | cut -c1-80)"
[ $age -gt 60 ] && echo "⚠ в $I нет коммитов больше 60 мин"
echo "Слито задач: $(git log --format=%s main..$I | grep -c -E '^night: T0[1-7] принят')/7; ветки: $(git for-each-ref --format='%(refname:short)' 'refs/heads/night/*' | wc -l | tr -d ' ')"

# 2. STATUS.md оркестратора
st=$(git show $I:docs/night/reports/STATUS.md 2>/dev/null)
if [ -n "$st" ]; then
  echo "--- STATUS.md:"; echo "$st" | head -14
  sa=$(git log -1 --format=%ct $I -- docs/night/reports/STATUS.md); [ $(( (now - sa) / 60 )) -gt 45 ] && echo "⚠ STATUS.md не обновлялся больше 45 мин"
else echo "⚠ STATUS.md ещё нет"; fi

# 3. Исследование и идеи
cA=$(git ls-tree --name-only $I docs/night/research/cards/ 2>/dev/null | grep -c '/a-'); cB=$(git ls-tree --name-only $I docs/night/research/cards/ 2>/dev/null | grep -c '/b-')
nR=$(git for-each-ref --format='%(refname:short)' 'refs/heads/night/R-*' | wc -l | tr -d ' ')
echo "Исследование: выдач (веток) $nR, карточек слито A=$cA B=$cB"
[ "$nR" -ge 6 ] && [ $((cA + cB)) -lt $((nR / 2)) ] && echo "⚠ карточек меньше половины выдач — исследователи ищут не то или приёмка режет"
cards=$(git ls-tree --name-only $I docs/night/research/cards/ 2>/dev/null | grep '\.md$')
if [ -n "$cards" ]; then
  no=0; tot=0; win=0
  for c in $cards; do tot=$((tot+1)); t=$(git show "$I:$c"); echo "$t" | grep -q '^- Вердикт: нет' && no=$((no+1)); echo "$t" | grep -q -E '^- Результат: (победитель|призёр|финалист)' && win=$((win+1)); done
  echo "Карточки: всего $tot, вердикт «нет» $no, победителей/призёров $win"
  [ $tot -ge 8 ] && [ $((no * 100 / tot)) -gt 60 ] && echo "⚠ больше 60% карточек «нет» — поиск уходит в нерелевантное"
  [ $cB -ge 8 ] && [ $win -eq 0 ] && echo "⚠ в группе B нет ни одного подтверждённого победителя — ищут не победителей"
fi
vt=$(git show $I:docs/night/ideas/verdicts.tsv 2>/dev/null | tail -n +2 | grep -c .)
if [ "$vt" -gt 0 ]; then
  yes=$(git show $I:docs/night/ideas/verdicts.tsv | tail -n +2 | awk -F'\t' '$2=="да"' | grep -c .)
  echo "Идеи: вердиктов $vt, «да» $yes"
  [ $vt -ge 5 ] && { [ $((yes * 100 / vt)) -gt 80 ] && echo "⚠ Astra принимает больше 80% идей — штампует"; [ $yes -eq 0 ] && echo "⚠ Astra отклонила все идеи"; }
fi
nI=$(git for-each-ref --format='%(refname:short)' 'refs/heads/night/I-*' | wc -l | tr -d ' ')
echo "Доработок I: $nI (лимит 3)"; [ "$nI" -gt 3 ] && echo "⚠ доработок I больше 3"

# 4. Запретные зоны в интеграции
bad=$(git diff --name-only main $I | grep -E '^docs/night/(tasks|accept)/|^docs/night/(ORCHESTRATOR|WORKER_RULES|README|SUPERVISOR)\.md$|^tools/hooks/objection_lexicon\.py$|^AGENTS\.md$|^schemas/ledger\.schema\.json$|^projects/(example|ryadom|esim-travel)/' | grep -v -E 'outputs/report\.html$|accept/I-[0-9]+\.sh$')
[ -z "$bad" ] || echo "⚠ в $I изменены запретные файлы: $(echo $bad)"
del=$(git diff --name-only --diff-filter=D main $I); [ -z "$del" ] || echo "⚠ в $I удалены файлы: $(echo $del)"

# 5. Orca: воркеры
if command -v orca >/dev/null; then
  echo "--- Orca:"
  orca orchestration worker-list --terminal-state active --json 2>/dev/null | python3 -c '
import json,sys
try: d=json.load(sys.stdin)
except Exception: print("  worker-list недоступен"); sys.exit()
r=d.get("result",d); ws=r.get("workers",r.get("items",[])) if isinstance(r,dict) else r
print("  активных воркеров:", len(ws))
if len(ws)>12: print("⚠ воркеров больше 12")
' 2>/dev/null
  orca worktree list --json 2>/dev/null | python3 -c '
import json,sys
d=json.load(sys.stdin); ws=d.get("result",d); ws=ws.get("worktrees",ws) if isinstance(ws,dict) else ws
o=[w for w in ws if "night-orchestrator" in (w.get("path","")+w.get("displayName",""))]
print("  оркестратор:", (o[0]["identity"]["key"] if o else "не найден"))
' 2>/dev/null
fi

# 6. Стоимость (по журналам Codex)
start=$(git log --reverse --format=%cI main..$I | head -1 | cut -c1-16)
if [ -n "$start" ] && [ -x .venv/bin/python ]; then
  c=$(.venv/bin/python tools/cost.py --cwd night- --since "$start" 2>/dev/null | grep -E 'Итого' | tail -1)
  echo "Стоимость с $start: ${c:-не посчиталась}"
  usd=$(echo "$c" | grep -o -E '\$[0-9]+(\.[0-9]+)?' | tr -d '$' | head -1)
  [ -n "$usd" ] && awk "BEGIN{exit !($usd > 30)}" && echo "⚠ потрачено больше \$30"
fi
echo "== Конец снимка"

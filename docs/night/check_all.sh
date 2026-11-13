#!/usr/bin/env bash
# Утренняя проверка ночного прогона. Запуск из корня репозитория:
#   bash docs/night/check_all.sh            # проверяет ветку night/integration
#   bash docs/night/check_all.sh night/T02  # одну ветку
# Ничего не меняет в твоём checkout: проверка идёт в отдельном временном worktree.
# Временный worktree остаётся в $TMPDIR (путь печатается в конце) — убрать: git worktree remove <путь>.
set -u
ROOT="$(git rev-parse --show-toplevel)" || exit 2
cd "$ROOT"
BR="${1:-night/integration}"
git rev-parse -q --verify "$BR" >/dev/null || { echo "Ветки $BR нет — прогон не начинался или не дошёл до слияния."; exit 2; }
WT="${TMPDIR:-/tmp}/night-check-$(date +%H%M%S)"
git worktree add -q --detach "$WT" "$BR" || exit 2
# Скрипты приёмки берём из main — чтобы ветка не могла подменить контракт.
mkdir -p "$WT/docs/night/accept"
for f in docs/night/accept/*.sh; do git show "main:$f" > "$WT/$f"; done
cd "$WT"
python3 -m venv .venv >/dev/null 2>&1 && .venv/bin/pip install -q -r requirements.txt >/dev/null 2>&1

echo "== Ветка $BR ($(git rev-parse --short HEAD)), коммитов поверх main: $(git rev-list --count main..HEAD)"
echo
printf '%-8s %-14s %-10s %s\n' "Задача" "Приёмка" "Ревью" "Отчёт"
summary=""
for id in T01 T02 T03 T04 T05 T06 T07; do
  log="$WT/.night-$id.log"
  bash "docs/night/accept/$id.sh" >"$log" 2>&1; rc=$?
  verdict=$(grep -h -m1 -o -E 'Вердикт:\s*(ACCEPT|REJECT)' "docs/night/reports/$id.review.md" 2>/dev/null | awk '{print $2}')
  status=$(grep -h -m1 -E '^Статус:' "docs/night/reports/$id.md" 2>/dev/null | sed 's/Статус: *//')
  [ $rc -eq 0 ] && a="ПРИНЯТО" || a="НЕ ПРИНЯТО"
  printf '%-8s %-14s %-10s %s\n' "$id" "$a" "${verdict:--}" "${status:-нет отчёта}"
  [ $rc -eq 0 ] || summary="$summary\n--- $id: непройденные пункты\n$(grep '✗' "$log")"
done
echo
echo "== Исследование"
nA=$(ls docs/night/research/cards/a-*.md 2>/dev/null | wc -l | tr -d ' ')
nB=$(ls docs/night/research/cards/b-*.md 2>/dev/null | wc -l | tr -d ' ')
echo "Карточек: A — $nA, B — $nB; в index.tsv: $(($(wc -l < docs/night/research/index.tsv) - 1)) репозиториев"
echo "Вердикты: перенять — $(grep -l -E '^- Вердикт: перенять' docs/night/research/cards/*.md 2>/dev/null | wc -l | tr -d ' '), может быть — $(grep -l -E '^- Вердикт: может быть' docs/night/research/cards/*.md 2>/dev/null | wc -l | tr -d ' ')"
echo "Победители/призёры с доказательством: $(grep -l -E '^- Результат: (победитель|призёр|финалист)' docs/night/research/cards/*.md 2>/dev/null | wc -l | tr -d ' ')"
bash docs/night/accept/R.sh --all > "$WT/.night-R.log" 2>&1 && echo "Приёмка всех карточек: ПРИНЯТО" || { echo "Приёмка всех карточек: НЕ ПРИНЯТО"; grep '✗' "$WT/.night-R.log" | head -10; }
bash docs/night/accept/S.sh > "$WT/.night-S.log" 2>&1 && echo "Синтез SYNTHESIS.md: ПРИНЯТО" || { echo "Синтез SYNTHESIS.md: НЕ ПРИНЯТО"; grep '✗' "$WT/.night-S.log" | head -5; }
[ -f docs/night/research/SYNTHESIS.md ] && { echo "--- Главное из SYNTHESIS.md:"; awk '/^#+ *Главное/{f=1;next} /^#/{if(f)exit} f' docs/night/research/SYNTHESIS.md | head -8; }
echo
echo "== Автономные доработки (ветки night/I-*, НЕ слиты; приёмку написал сам воркер — смотреть глазами)"
for b in $(git -C "$ROOT" for-each-ref --format='%(refname:short)' 'refs/heads/night/I-*'); do
  id=$(echo "$b" | sed -E 's#night/(I-[0-9]+).*#\1#')
  first=$(git -C "$ROOT" log --reverse --format=%h "night/integration..$b" | head -1)
  only=$(git -C "$ROOT" show --name-only --format= "$first" | tr '\n' ' ')
  rv=$(git -C "$ROOT" show "$b:docs/night/reports/$id.review.md" 2>/dev/null | grep -m1 -o -E 'ACCEPT|REJECT')
  echo "$b · первый коммит: ${only}· ревью: ${rv:--} · diff: git diff night/integration...$b --stat"
done
echo
bash docs/night/accept/common.sh 2>&1 | tail -12
echo -e "$summary"
echo
[ -f docs/night/reports/NIGHT_REPORT.md ] && { echo "== Из NIGHT_REPORT.md:"; sed -n '1,40p' docs/night/reports/NIGHT_REPORT.md; } || echo "NIGHT_REPORT.md нет."
echo
echo "Логи по задачам: $WT/.night-T0*.log"
echo "Временный worktree: $WT (убрать: git worktree remove \"$WT\")"

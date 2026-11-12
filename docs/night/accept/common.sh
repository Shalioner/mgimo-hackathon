#!/usr/bin/env bash
# Общая приёмка любой ночной ветки: тесты, реестры, запретные зоны, секреты.
. "$(dirname "$0")/lib.sh"
echo "common: ветка $(git rev-parse --abbrev-ref HEAD), база $(base_ref | cut -c1-8)"

# 1. Тесты
out=$($PY -m pytest -q tests 2>&1 | tail -3)
if echo "$out" | grep -q -E '[0-9]+ passed' && ! echo "$out" | grep -q -E '[0-9]+ (failed|error)'; then
  ok "pytest: $(echo "$out" | tail -1)"
else bad "pytest: $out"; fi

# 2. Все реестры валидны
for p in projects/*/; do
  [ -d "$p/state/ledger" ] || continue
  r=$($PY tools/ledger.py validate "$p" 2>&1 | tail -1)
  if echo "$r" | grep -q 'ошибок 0'; then ok "validate $p"; else bad "validate $p: $r"; fi
done

# 3. Запретные зоны
changed=$(git diff --name-only "$(base_ref)" HEAD)
bad_paths=$(echo "$changed" | grep -E \
  -e '^docs/night/(tasks|accept)/' -e '^docs/night/(ORCHESTRATOR|WORKER_RULES|README|SUPERVISOR|MEGAFON_INTENT)\.md$' -e '^docs/night/(check_all|supervise)\.sh$' \
  -e '^tools/hooks/objection_lexicon\.py$' -e '^AGENTS\.md$' -e '^schemas/ledger\.schema\.json$' \
  -e '(^|/)\.env$' -e '^\.claude/settings\.json$' -e '^\.codex/hooks\.json$' \
  -e '^projects/(example|ryadom|esim-travel)/' \
  | grep -v -E '^projects/(esim-travel|ryadom)/outputs/report\.html$' \
  | grep -v -E '^docs/night/accept/I-[0-9]+\.sh$')
if [ -z "$bad_paths" ]; then ok "запретные зоны не тронуты"; else bad "изменены запретные файлы: $(echo $bad_paths)"; fi

# 4. Удалённых файлов нет
deleted=$(git diff --name-only --diff-filter=D "$(base_ref)" HEAD)
if [ -z "$deleted" ]; then ok "ни один файл не удалён"; else bad "удалены файлы: $(echo $deleted)"; fi

# 5. Секреты в добавленных строках
sec=$(git diff "$(base_ref)" HEAD | grep -E '^\+' | grep -E '(sk-[A-Za-z0-9_-]{20,}|ghp_[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{16}|(API_KEY|TOKEN|SECRET)=[^ <$]{8,})' | grep -v -i example | head -3)
if [ -z "$sec" ]; then ok "секретов в diff нет"; else bad "похоже на секрет в diff (значение не печатаю)"; fi

# 6. Рабочее дерево закоммичено
if [ -z "$(git status --porcelain --untracked-files=no)" ]; then ok "изменения закоммичены"; else bad "есть незакоммиченные изменения"; fi

finish

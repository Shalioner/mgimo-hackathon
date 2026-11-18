#!/usr/bin/env bash
# Проверка сохранённого прогона без модельных вызовов.
set -euo pipefail

if [[ $# -lt 2 || $# -gt 3 || ( $# -eq 3 && $3 != --snapshot ) ]]; then
  echo "Использование: bash check.sh <корень> <абсолютный-путь-к-python> [--snapshot]" >&2
  exit 2
fi
repo=$1
py=$2
artifacts=$(cd "$(dirname "$0")" && pwd)
scratch=$(mktemp -d)
trap 'rm -rf -- "$scratch"' EXIT
export PYTHONDONTWRITEBYTECODE=1

case_dir=evals/score9/interviews
m27=projects/rehearsal-mobile-spend
mkdir -p "$scratch/projects" "$scratch/evals/score9/live"
cp -R "$repo/tools" "$repo/schemas" "$repo/knowledge" "$scratch/"
cp -R "$repo/$m27" "$scratch/projects/"
cp "$repo/evals/score9/live/sha256-tree.py" "$scratch/evals/score9/live/"
cd "$scratch"
"$py" evals/score9/live/sha256-tree.py "$m27" > m27-before.sha256

cp -R "$artifacts" "$case_dir"
"$py" tools/ledger.py validate "$case_dir"
"$py" tools/ledger.py trace "$case_dir" EXP-101

cp -R "$case_dir" "$scratch/negative"
cp "$case_dir/controls/forbidden-fact.yaml" "$scratch/negative/state/ledger/claims.yaml"
negative_status=0
"$py" tools/ledger.py validate "$scratch/negative" > negative.log 2>&1 || negative_status=$?
cat negative.log
test "$negative_status" -eq 1
grep -Eq 'CLM-901.*SRC-002.*синтетическ|CLM-901.*синтетическ.*SRC-002' negative.log
cmp "$artifacts/state/ledger/claims.yaml" "$case_dir/state/ledger/claims.yaml"

"$py" evals/score9/live/sha256-tree.py "$m27" > m27-after.sha256
cmp m27-before.sha256 m27-after.sha256
printf 'M27: SHA-256 совпали для %s файлов. Отрицательный контроль отклонён (код %s).\n' \
  "$(wc -l < m27-after.sha256 | tr -d ' ')" "$negative_status"

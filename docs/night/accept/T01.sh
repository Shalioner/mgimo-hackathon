#!/usr/bin/env bash
# T01: чистый клон → запуск по README; секреты в истории.
. "$(dirname "$0")/lib.sh"
echo "T01"

need "! grep -q '<ссылка' README.md" "в README нет плейсхолдера <ссылка…>"
url=$(git remote get-url origin 2>/dev/null | sed 's/\.git$//')
if [ -n "$url" ] && grep -q -F "$url" README.md; then ok "URL origin есть в README"; else bad "URL origin ($url) не найден в README"; fi
for c in 'python3 -m venv .venv' 'pip install -r requirements.txt' 'tools/ledger.py status' 'pytest'; do
  need "grep -q -F '$c' README.md" "README содержит команду: $c"
done
need "grep -q -i 'JEV' README.md" "README говорит про JEV-ключ (необязательный)"

# Секреты во всей истории
s=$(git log -p --all 2>/dev/null | grep -E '(sk-[A-Za-z0-9_-]{20,}|ANTHROPIC_API_KEY=[^ <$]{8,}|OPENAI_API_KEY=[^ <$]{8,}|ghp_[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{16})' | grep -v -i example | head -1)
if [ -z "$s" ]; then ok "секретов в истории git нет"; else bad "в истории git похоже на ключ (значение не печатаю)"; fi
if [ -z "$(git ls-files | grep -E '(^|/)\.env$')" ]; then ok ".env не в git"; else bad ".env лежит в git"; fi

# Чистый клон текущего HEAD и запуск как у жюри (системный python3, без .venv исходника)
C="$TMPD/clone"
git clone -q "$ROOT" "$C" && git -C "$C" checkout -q "$(git rev-parse HEAD)" || { bad "не удалось склонировать"; finish; }
(
  cd "$C" || exit 1
  python3 -m venv .venv >/dev/null 2>&1 || exit 11
  .venv/bin/pip install -q -r requirements.txt >/dev/null 2>&1 || exit 12
  .venv/bin/python tools/ledger.py status >/dev/null 2>&1 || exit 13
  .venv/bin/python -m pytest -q tests 2>&1 | tail -1 | grep -q -E 'passed' || exit 14
  .venv/bin/python -m pytest -q tests 2>&1 | tail -1 | grep -q -E 'failed|error' && exit 15
  .venv/bin/python tools/ledger.py validate projects/example 2>&1 | tail -1 | grep -q 'ошибок 0' || exit 16
  printf '%s\n' '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"t","version":"0"}}}' '{"jsonrpc":"2.0","id":2,"method":"tools/list"}' \
    | .venv/bin/python tools/mcp_server.py 2>/dev/null | grep -q 'strategist_' || exit 17
  exit 0
)
rc=$?
case $rc in
  0) ok "чистый клон: venv, pip, status, pytest, validate, MCP tools/list — всё прошло";;
  11) bad "чистый клон: python3 -m venv упал";;
  12) bad "чистый клон: pip install -r requirements.txt упал";;
  13) bad "чистый клон: ledger.py status упал";;
  14|15) bad "чистый клон: pytest не зелёный";;
  16) bad "чистый клон: validate projects/example с ошибками";;
  17) bad "чистый клон: MCP tools/list не вернул strategist_*";;
  *) bad "чистый клон: код $rc";;
esac

need "test -s docs/night/reports/T01.md" "есть отчёт docs/night/reports/T01.md"
finish

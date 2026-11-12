# Общие функции скриптов приёмки. Подключается через: . "$(dirname "$0")/lib.sh"
# Совместимо с bash 3.2 (macOS).

ROOT="$(git rev-parse --show-toplevel)" || exit 2
cd "$ROOT" || exit 2
if [ -x .venv/bin/python ]; then PY=.venv/bin/python; else PY=python3; fi
FAIL=0
TMPD="${TMPDIR:-/tmp}/night-accept-$$"
mkdir -p "$TMPD"

ok()   { echo "  ✓ $*"; }
bad()  { echo "  ✗ $*"; FAIL=1; }
need() { if eval "$1" >/dev/null 2>&1; then ok "$2"; else bad "$2"; fi; }
finish() {
  if [ "$FAIL" -eq 0 ]; then echo "ИТОГ $(basename "$0" .sh): ПРИНЯТО"; exit 0
  else echo "ИТОГ $(basename "$0" .sh): НЕ ПРИНЯТО"; exit 1; fi
}
base_ref() { git merge-base main HEAD 2>/dev/null || echo main; }

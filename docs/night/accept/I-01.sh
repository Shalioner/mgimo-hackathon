#!/usr/bin/env bash
# I-01: обязательный контракт IDEA-003 — очередь без изменения решений.
. "$(dirname "$0")/lib.sh"
echo "I-01"
need "test -f tools/next.py" "tools/next.py существует"
need "test -f tests/test_next.py" "tests/test_next.py существует"
need "test -d tests/fixtures/next/actionable/state/ledger" "есть эталон actionable"
# До реализации завершаемся без pytest: доказательство отсутствующей команды.
[ "$FAIL" -eq 0 ] || finish

$PY - <<'PYEOF'
import json
import subprocess
import sys

r = subprocess.run([sys.executable, "tools/next.py",
                    "tests/fixtures/next/actionable", "--on", "2026-11-12", "--json"],
                   capture_output=True, text=True)
try:
    data = json.loads(r.stdout)
    items = data["items"]
    assert r.returncode == 0, r.stderr
    assert data["on"] == "2026-11-12"
    assert len(items) == 3
    by_id = {item["id"]: item for item in items}
    assert set(by_id) == {"DEC-001", "DEC-002", "CHG-001"}
    assert all(item["reasons"] and "date" in item and item["next_action"] for item in items)
    assert "review_due" in {reason["code"] for reason in by_id["DEC-001"]["reasons"]}
    assert {reason["code"] for reason in by_id["DEC-002"]["reasons"]} >= {"needs_review", "review_due"}
    assert any(reason["date"] == "2026-11-12" for reason in by_id["DEC-002"]["reasons"]
               if reason["code"] == "review_due"), "review_by в день проверки наступил"
    print("  ✓ actionable: код 0, ровно 3 ID, причины, даты и действия человека; срок включительно")
except (ValueError, KeyError, TypeError, AssertionError) as exc:
    print("  ✗ actionable:", exc, r.stdout, r.stderr)
    sys.exit(1)
PYEOF
[ $? -eq 0 ] || FAIL=1

need "$PY -m pytest -q tests/test_next.py -k future_processed_and_dedup" "будущий срок и обработанный CHG исключены; причины DEC объединены"
need "$PY -m pytest -q tests/test_next.py -k read_only_and_reproducible" "одинаковая дата → одинаковый JSON; хэши сохранены; внешних вызовов 0"
need "$PY -m pytest -q tests/test_next.py" "все тесты очереди зелёные"
finish

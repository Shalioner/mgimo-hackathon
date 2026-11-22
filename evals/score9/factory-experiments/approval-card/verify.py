#!/usr/bin/env python3
"""Техническая сверка A/B UX-теста «компактная карточка C3 vs штатный отчёт».

Проверяет:
1. Целостность замороженного пакета (SHA-256 архива и живых baseline report.html).
2. Сохранность C3 SHA-256 через frozen tools/c3_approval.py (family-connect=current, esim-travel=unrecorded + blocked prepare).
3. Отсутствие недоказанных дат и ролей в компактных карточках (все даты и роли на карточках обязаны присутствовать в живом baseline report.html).
4. Фактическую восстанавливаемость условий всех 10 заданий (T01..T10) из ОБОИХ вариантов (baseline report.html и компактная карточка).
5. Корректность кроссовер-схемы S1..S4 (ни один участник не видит один кейс/ответ дважды, баланс форматов и порядка).
6. Явную фиксацию, что без живых участников UX-время остаётся BLOCKED / UNCONFIRMED.
"""

from __future__ import annotations

import hashlib
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import subprocess
import sys

import yaml


HERE = Path(__file__).resolve().parent
MANIFEST_PATH = HERE / "manifest.json"
KEY_PATH = HERE / "answer_key.yaml"

ISO_DATE_RE = re.compile(r"\b20\d{2}-\d{2}-\d{2}\b")
RU_DATE_RE = re.compile(
    r"\b\d{1,2}\s+(?:января|февраля|марта|апреля|мая|июня|июля|августа|сентября|октября|ноября|декабря)\s+20\d{2}(?:\s+года)?\b",
    re.IGNORECASE,
)

# Все роли, которые вообще допускается проверять (каждая найденная на карточке роль должна быть в baseline).
ROLE_PATTERNS = [
    "руководитель направления",
    "руководитель цифрового продукта",
    "руководитель продукта",
    "продуктовый аналитик",
    "владелец биллинга",
    "дизайнер",
    "роль не указана",
    "директор",
    "cto",
    "cfo",
    "ceo",
    "юрист",
    "финансовый директор",
    "менеджер проекта",
    "тимлид",
    "разработчик",
]


class _VisibleTextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._skip = 0
        self._parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in ("style", "script", "head"):
            self._skip += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in ("style", "script", "head") and self._skip > 0:
            self._skip -= 1

    def handle_data(self, data: str) -> None:
        if self._skip == 0:
            self._parts.append(data)

    def text(self) -> str:
        raw = " ".join(self._parts)
        return re.sub(r"\s+", " ", raw).strip()


def html_to_text(path: Path) -> str:
    parser = _VisibleTextExtractor()
    parser.feed(path.read_text(encoding="utf-8"))
    return parser.text()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def run_verify() -> dict:
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    answer_key = yaml.safe_load(KEY_PATH.read_text(encoding="utf-8"))

    passes: list[str] = []
    issues: list[str] = []
    blocked: list[str] = []

    frozen = manifest["frozen_package"]
    archive_path = Path(frozen["archive_path"])
    unpacked_root = Path(frozen["unpacked_root"])
    frozen_python = unpacked_root / ".venv/bin/python"
    c3_tool = unpacked_root / "tools/c3_approval.py"

    # 1. Archive SHA-256
    if not archive_path.is_file():
        issues.append(f"Отсутствует архив замороженного пакета: {archive_path}")
    else:
        got_archive_sha = sha256_file(archive_path)
        if got_archive_sha == frozen["archive_sha256"]:
            passes.append(f"Archive SHA-256 совпал: {got_archive_sha}")
        else:
            issues.append(f"Archive SHA-256 mismatch: expected {frozen['archive_sha256']}, got {got_archive_sha}")

    baseline_texts: dict[str, str] = {}
    card_texts: dict[str, str] = {}
    compactness: dict[str, dict[str, object]] = {}

    # 2. Baseline report SHAs + C3 approval checks + unproven dates/roles checks
    for case_id, cfg in manifest["cases"].items():
        base_path = unpacked_root / cfg["baseline_report_rel"]
        card_path = HERE / cfg["compact_card_rel"]

        if not base_path.is_file():
            issues.append(f"[{case_id}] Нет baseline report.html: {base_path}")
            continue
        if not card_path.is_file():
            issues.append(f"[{case_id}] Нет компактной карточки: {card_path}")
            continue

        got_base_sha = sha256_file(base_path)
        if got_base_sha == cfg["baseline_report_sha256"]:
            passes.append(f"[{case_id}] Baseline report.html SHA-256 совпал ({got_base_sha[:12]})")
        else:
            issues.append(f"[{case_id}] Baseline report.html SHA-256 mismatch: {got_base_sha}")

        base_txt = html_to_text(base_path)
        card_txt = html_to_text(card_path)
        baseline_texts[case_id] = base_txt
        card_texts[case_id] = card_txt

        compactness[case_id] = {
            "baseline_bytes": base_path.stat().st_size,
            "card_bytes": card_path.stat().st_size,
            "baseline_words": len(base_txt.split()),
            "card_words": len(card_txt.split()),
            "word_reduction_ratio": round(len(base_txt.split()) / max(1, len(card_txt.split())), 1),
        }

        # Check C3 tool against frozen project
        proj_dir = unpacked_root / "projects" / case_id
        chk = subprocess.run(
            [str(frozen_python), str(c3_tool), "check", str(proj_dir), "--json"],
            capture_output=True,
            text=True,
            check=False,
        )
        try:
            chk_json = json.loads(chk.stdout)
        except json.JSONDecodeError:
            issues.append(f"[{case_id}] c3_approval.py check вернул не-JSON: {chk.stdout!r} {chk.stderr!r}")
            chk_json = {}

        if chk_json.get("status") == cfg["expected_c3_check_status"]:
            passes.append(f"[{case_id}] c3_approval.py check -> status={chk_json.get('status')}")
        else:
            issues.append(
                f"[{case_id}] c3_approval.py check status mismatch: expected {cfg['expected_c3_check_status']}, got {chk_json}"
            )

        prep = subprocess.run(
            [str(frozen_python), str(c3_tool), "prepare", str(proj_dir)],
            capture_output=True,
            text=True,
            check=False,
        )
        if cfg["c3_state_label"] == "approved":
            exp_sha = cfg["expected_c3_package_sha256"]
            got_prep = prep.stdout.strip()
            if prep.returncode == 0 and got_prep == exp_sha:
                passes.append(f"[{case_id}] C3 SHA-256 подтверждён c3_approval.py prepare: {exp_sha}")
            else:
                issues.append(f"[{case_id}] C3 prepare mismatch: rc={prep.returncode}, out={got_prep!r}, err={prep.stderr!r}")

            if exp_sha in base_txt and exp_sha in card_txt:
                passes.append(f"[{case_id}] C3 SHA-256 сохранён дословно и в baseline, и в карточке")
            else:
                issues.append(f"[{case_id}] C3 SHA-256 отсутствует в baseline ({exp_sha in base_txt}) или карточке ({exp_sha in card_txt})")

            receipt_path = unpacked_root / cfg["c3_receipt_rel"]
            got_r_sha = sha256_file(receipt_path)
            if got_r_sha == cfg["c3_receipt_sha256"]:
                passes.append(f"[{case_id}] c3_approvals.json SHA-256 совпал ({got_r_sha[:12]})")
            else:
                issues.append(f"[{case_id}] c3_approvals.json SHA-256 mismatch: {got_r_sha}")
        else:
            needle = cfg["expected_c3_prepare_blocked_substring"]
            if prep.returncode != 0 and needle in (prep.stderr + prep.stdout):
                passes.append(f"[{case_id}] c3_approval.py prepare ожидаемо заблокирован ({needle})")
            else:
                issues.append(f"[{case_id}] c3_approval.py prepare неожиданный результат: rc={prep.returncode}, stderr={prep.stderr!r}")

            # Verify card explicitly distinguishes unrecorded from absence of past approval
            if "не означает отсутствия прошлого утверждения" in card_txt.lower() and "историческое согласование не переписано" in card_txt.lower():
                passes.append(f"[{case_id}] Карточка явно разделяет unrecorded и историческое согласование C3")
            else:
                issues.append(f"[{case_id}] В карточке нет явного пояснения, что unrecorded != отсутствие прошлого утверждения")

        # 3. Unproven dates check: every ISO/RU date on card must exist in baseline report.html
        card_iso_dates = set(ISO_DATE_RE.findall(card_txt))
        base_iso_dates = set(ISO_DATE_RE.findall(base_txt))
        unproven_iso = sorted(card_iso_dates - base_iso_dates)

        card_ru_dates = {d.lower() for d in RU_DATE_RE.findall(card_txt)}
        base_ru_dates = {d.lower() for d in RU_DATE_RE.findall(base_txt)}
        unproven_ru = sorted(d for d in card_ru_dates if d not in base_ru_dates and d.replace(" года", "") not in base_txt.lower())

        if not unproven_iso and not unproven_ru:
            passes.append(f"[{case_id}] Недоказанных дат на карточке нет (ISO={sorted(card_iso_dates)}, RU={sorted(card_ru_dates)})")
        else:
            issues.append(f"[{case_id}] Найдены недоказанные даты на карточке: ISO={unproven_iso}, RU={unproven_ru}")

        # Unproven roles check: every matched role on card must exist in baseline report.html
        base_lower = base_txt.lower()
        card_lower = card_txt.lower()
        found_roles = [r for r in ROLE_PATTERNS if r in card_lower]
        unproven_roles = [r for r in found_roles if r not in base_lower]
        if not unproven_roles:
            passes.append(f"[{case_id}] Недоказанных ролей на карточке нет (подтверждены: {found_roles})")
        else:
            issues.append(f"[{case_id}] Найдены недоказанные роли на карточке: {unproven_roles}")

    # 4. Recoverability of all 10 tasks from BOTH baseline and card
    tasks = answer_key.get("tasks", [])
    if len(tasks) != 10:
        issues.append(f"Ожидалось 10 заданий в answer_key.yaml, получено {len(tasks)}")
    else:
        passes.append("В answer_key.yaml ровно 10 заданий (T01..T10)")

    for task in tasks:
        tid = task["id"]
        cid = task["case"]
        b_low = baseline_texts.get(cid, "").lower()
        c_low = card_texts.get(cid, "").lower()
        missing_base = [n for n in task["required_needles_both"] if n.lower() not in b_low]
        missing_card = [n for n in task["required_needles_both"] if n.lower() not in c_low]
        if not missing_base and not missing_card:
            passes.append(f"[{tid}/{cid}] Условия восстанавливаются из обоих вариантов ({len(task['required_needles_both'])} ключей)")
        else:
            issues.append(f"[{tid}/{cid}] Неполная восстанавливаемость: missing_baseline={missing_base}, missing_card={missing_card}")

    # 5. Crossover design invariants from run_session.py
    sys.path.insert(0, str(HERE))
    import run_session  # noqa: E402

    seq_ok = True
    first_arm_counts = {"card": 0, "baseline": 0}
    first_case_counts = {"family-connect": 0, "esim-travel": 0}
    for seq_name, blocks in run_session.SEQUENCES.items():
        seen_cases = [b["case"] for b in blocks]
        seen_arms = [b["arm"] for b in blocks]
        seen_tasks = [t for b in blocks for t in b["tasks"]]
        if len(seen_cases) != len(set(seen_cases)) or set(seen_cases) != {"family-connect", "esim-travel"}:
            issues.append(f"[{seq_name}] Нарушено правило уникальности кейсов: {seen_cases}")
            seq_ok = False
        if set(seen_arms) != {"card", "baseline"}:
            issues.append(f"[{seq_name}] Нарушен баланс форматов: {seen_arms}")
            seq_ok = False
        if len(seen_tasks) != 10 or len(set(seen_tasks)) != 10:
            issues.append(f"[{seq_name}] Повтор или пропуск заданий: {seen_tasks}")
            seq_ok = False
        first_arm_counts[str(blocks[0]["arm"])] += 1
        first_case_counts[str(blocks[0]["case"])] += 1

    if seq_ok and first_arm_counts == {"card": 2, "baseline": 2} and first_case_counts == {"family-connect": 2, "esim-travel": 2}:
        passes.append("Кроссовер-схема S1..S4 сбалансирована: 0 повторов ответов, равный порядок форматов и кейсов")
    else:
        issues.append(f"Дисбаланс кроссовер-схемы: first_arm={first_arm_counts}, first_case={first_case_counts}")

    # 6. Human UX timing guard
    blocked.append(
        "UX-время и фактическая точность участников не подтверждены: 0 живых участников (требуется прогон run_session.py на живых участниках кратно 4)."
    )

    return {
        "status": "PASS_TECHNICAL_UX_BLOCKED" if not issues else "ISSUES_FOUND",
        "passes": passes,
        "issues": issues,
        "blocked": blocked,
        "compactness": compactness,
    }


def main() -> int:
    result = run_verify()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if not result["issues"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

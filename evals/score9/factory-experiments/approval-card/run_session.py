#!/usr/bin/env python3
"""Локальный CLI для проведения таймированной сессии A/B UX-теста и расчёта сводки.

Не вызывает модели, внешние API или сеть. Использует только стандартную библиотеку Python.
"""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime
import json
import math
from pathlib import Path
import statistics
import sys
import time


HERE = Path(__file__).resolve().parent
MANIFEST_PATH = HERE / "manifest.json"

SEQUENCES: dict[str, list[dict[str, object]]] = {
    "S1": [
        {"block": 1, "case": "family-connect", "arm": "card", "tasks": ["T01", "T02", "T03", "T04", "T05"]},
        {"block": 2, "case": "esim-travel", "arm": "baseline", "tasks": ["T06", "T07", "T08", "T09", "T10"]},
    ],
    "S2": [
        {"block": 1, "case": "family-connect", "arm": "baseline", "tasks": ["T01", "T02", "T03", "T04", "T05"]},
        {"block": 2, "case": "esim-travel", "arm": "card", "tasks": ["T06", "T07", "T08", "T09", "T10"]},
    ],
    "S3": [
        {"block": 1, "case": "esim-travel", "arm": "card", "tasks": ["T06", "T07", "T08", "T09", "T10"]},
        {"block": 2, "case": "family-connect", "arm": "baseline", "tasks": ["T01", "T02", "T03", "T04", "T05"]},
    ],
    "S4": [
        {"block": 1, "case": "esim-travel", "arm": "baseline", "tasks": ["T06", "T07", "T08", "T09", "T10"]},
        {"block": 2, "case": "family-connect", "arm": "card", "tasks": ["T01", "T02", "T03", "T04", "T05"]},
    ],
}

TASK_PROMPTS: dict[str, str] = {
    "T01": "Даёт ли текущее утверждение C3 по family-connect право сразу запускать причинный пилот EXP-003? Обоснуйте в 1–2 фразах.",
    "T02": "Каков статус версии пакета C3 и какой SHA-256 (достаточно первых 8 символов или целиком) привязан к утверждению?",
    "T03": "Какая ближайшая работа по плану сейчас к выполнению и какая роль за неё отвечает?",
    "T04": "Какие два численных условия указаны для перехода к решению о пилоте по EXP-002: (а) допустимая доля неизвестных исходов в пригодной выборке и (б) требуемый недельный темп набора для первичного исхода?",
    "T05": "Какой вариант выбран на C2, какая роль приняла решение DEC-001 и какой крайний срок пересмотра установлен?",
    "T06": "Утверждён ли сейчас обновлённый пакет esim-travel на C3 и разрешён ли немедленный запуск пилота EXP-002 или новых экспериментов?",
    "T07": "Означает ли текущий статус повторного C3, что пакет esim-travel никогда ранее не утверждался, а выбор ALT-001 отменён?",
    "T08": "Что произошло с прежними коммуникационными обещаниями PRM-001..003 и что требуется перед использованием новых сообщений?",
    "T09": "Какое ближайшее действие открыто по CHG-001 и какие три вопроса по критериям EXP-002 вынесены на повторное C3?",
    "T10": "Какая роль указана в блоке «Согласование C3» как согласующий, и что означает дата 2027-03-10 в DEC-001?",
}

SCOPE_CRITICAL_TASKS = {"T01", "T06", "T07"}


def load_manifest() -> dict:
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))


def assign_sequence(participant_index: int) -> str:
    if participant_index < 1:
        raise ValueError("participant_index must be >= 1")
    order = ["S1", "S2", "S3", "S4"]
    return order[(participant_index - 1) % 4]


def resolve_material_path(manifest: dict, case_id: str, arm: str) -> str:
    case_cfg = manifest["cases"][case_id]
    if arm == "card":
        return str((HERE / case_cfg["compact_card_rel"]).resolve())
    unpacked = Path(manifest["frozen_package"]["unpacked_root"])
    return str((unpacked / case_cfg["baseline_report_rel"]).resolve())


def cmd_plan(args: argparse.Namespace) -> int:
    manifest = load_manifest()
    rows = []
    for idx in range(1, args.participants + 1):
        seq = assign_sequence(idx)
        blocks = SEQUENCES[seq]
        rows.append({
            "participant_id": f"P{idx:02d}",
            "sequence": seq,
            "blocks": [
                {
                    "block": b["block"],
                    "case": b["case"],
                    "arm": b["arm"],
                    "tasks": b["tasks"],
                    "material_path": resolve_material_path(manifest, str(b["case"]), str(b["arm"])),
                }
                for b in blocks
            ],
        })
    print(json.dumps({"participants": rows}, ensure_ascii=False, indent=2))
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    manifest = load_manifest()
    seq = args.sequence or assign_sequence(args.index)
    if seq not in SEQUENCES:
        print(f"Неизвестная последовательность: {seq}", file=sys.stderr)
        return 2

    out_path = Path(args.out).resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)

    record: dict[str, object] = {
        "participant_id": args.participant_id,
        "sequence": seq,
        "started_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "responses": [],
    }

    print(f"=== Сессия A/B UX-теста: {args.participant_id} (последовательность {seq}) ===")
    for block in SEQUENCES[seq]:
        case_id = str(block["case"])
        arm = str(block["arm"])
        material = resolve_material_path(manifest, case_id, arm)
        print(f"\n--- Блок {block['block']}: кейс={case_id}, формат={arm} ---")
        print(f"Откройте документ: {material}")
        input("Нажмите Enter, когда документ открыт и участник готов начать блок...")

        for tid in block["tasks"]:
            tid_s = str(tid)
            print(f"\n[{tid_s}] {TASK_PROMPTS[tid_s]}")
            t0 = time.monotonic()
            answer = input("Ответ участника > ").strip()
            elapsed = round(time.monotonic() - t0, 2)
            record["responses"].append({
                "task_id": tid_s,
                "block": block["block"],
                "case": case_id,
                "arm": arm,
                "scope_critical": tid_s in SCOPE_CRITICAL_TASKS,
                "elapsed_sec": elapsed,
                "answer": answer,
                "correct": None,
                "scope_error": None,
            })
            print(f"Записано ({elapsed:.2f} с).")

    record["finished_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
    out_path.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"\nСессия сохранена: {out_path}")
    print("Заполните поля correct (true/false) и scope_error (true/false) по answer_key.yaml и запустите `summarize`.")
    return 0


def cmd_summarize(args: argparse.Namespace) -> int:
    sessions_dir = Path(args.sessions_dir).resolve()
    files = sorted(sessions_dir.glob("*.json")) if sessions_dir.is_dir() else []
    if not files:
        summary = {
            "participants": 0,
            "ux_timing_status": "UNCONFIRMED",
            "reason": "Нет завершённых сессий с живыми участниками.",
        }
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0

    times = {arm: {"task": [], "block": []} for arm in ("card", "baseline")}
    paired_deltas = []
    card_scores = []
    sequences = Counter()
    participant_ids = set()
    participant_reports = []
    total_scope_errors = 0
    ungraded = 0

    for fp in files:
        data = json.loads(fp.read_text(encoding="utf-8"))
        participant_id = data.get("participant_id")
        sequence = data.get("sequence")
        if not participant_id or participant_id in participant_ids or sequence not in SEQUENCES:
            print(f"{fp.name}: требуется уникальный участник и известная последовательность.", file=sys.stderr)
            return 2
        participant_ids.add(participant_id)
        sequences[sequence] += 1
        responses = data.get("responses", [])
        expected = [
            (task, block["block"], block["case"], block["arm"])
            for block in SEQUENCES[sequence]
            for task in block["tasks"]
        ]
        observed = [
            (row.get("task_id"), row.get("block"), row.get("case"), row.get("arm"))
            for row in responses
        ]
        if observed != expected:
            print(f"{fp.name}: набор или порядок заданий не совпадает с {sequence}.", file=sys.stderr)
            return 2
        correct_cnt = 0
        card_correct = 0
        scope_err_cnt = 0
        participant_ungraded = 0
        participant_times = {"card": [], "baseline": []}
        for r in responses:
            correct = r.get("correct")
            scope_error = r.get("scope_error")
            if type(correct) is not bool or type(scope_error) is not bool:
                participant_ungraded += 1
            correct_cnt += correct is True
            card_correct += correct is True and r["arm"] == "card"
            if scope_error is True or (correct is False and r["task_id"] in SCOPE_CRITICAL_TASKS):
                scope_err_cnt += 1
            t = r.get("elapsed_sec")
            if type(t) not in (int, float) or not math.isfinite(t) or t < 0:
                print(f"{fp.name}: некорректное время для {r['task_id']}.", file=sys.stderr)
                return 2
            participant_times[r["arm"]].append(t)
        for arm, values in participant_times.items():
            times[arm]["task"].extend(values)
            times[arm]["block"].append(sum(values))
        baseline_median = statistics.median(participant_times["baseline"])
        card_median = statistics.median(participant_times["card"])
        paired_deltas.append(baseline_median - card_median)
        card_scores.append(card_correct)
        ungraded += participant_ungraded
        total_scope_errors += scope_err_cnt
        participant_reports.append({
            "participant_id": participant_id,
            "sequence": sequence,
            "correct": correct_cnt,
            "total": len(responses),
            "card_correct": card_correct,
            "scope_errors": scope_err_cnt,
            "pass": None if participant_ungraded else correct_cnt >= 8 and scope_err_cnt == 0,
        })

    balanced = len(sequences) == len(SEQUENCES) and len(set(sequences.values())) == 1
    ready = balanced and ungraded == 0
    passed = all(row["pass"] for row in participant_reports) and statistics.median(card_scores) >= 4
    summary = {
        "participants": len(files),
        "sequence_counts": dict(sequences),
        "ungraded_items": ungraded,
        "total_scope_errors": total_scope_errors,
        "card_median_correct_out_of_5": statistics.median(card_scores) if ungraded == 0 else None,
        "accuracy_and_scope_pass": passed if ready else None,
        "ux_timing_status": "READY_FOR_COMPARISON" if ready and passed else "UNCONFIRMED",
        "timing_sec": {
            arm: {unit: time_summary(values) for unit, values in by_unit.items()}
            for arm, by_unit in times.items()
        },
        "paired_task_median_delta_sec": time_summary(paired_deltas),
        "participant_reports": participant_reports,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


def time_summary(values: list[float]) -> dict[str, float]:
    median = round(statistics.median(values), 2)
    if len(values) == 1:
        return {"median": median, "iqr": 0.0}
    q1, _, q3 = statistics.quantiles(values, method="inclusive")
    return {"median": median, "iqr": round(q3 - q1, 2)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_plan = sub.add_parser("plan", help="Показать сбалансированный план распределения S1-S4")
    p_plan.add_argument("--participants", type=int, default=8)
    p_plan.set_defaults(func=cmd_plan)

    p_run = sub.add_parser("run", help="Провести интерактивную таймированную сессию")
    p_run.add_argument("--participant-id", required=True)
    p_run.add_argument("--index", type=int, default=1, help="Порядковый номер участника (1..N)")
    p_run.add_argument("--sequence", choices=sorted(SEQUENCES.keys()), default=None)
    p_run.add_argument("--out", required=True, help="Путь для сохранения JSON сессии")
    p_run.set_defaults(func=cmd_run)

    p_sum = sub.add_parser("summarize", help="Посчитать сводку по сохранённым сессиям")
    p_sum.add_argument("--sessions-dir", required=True)
    p_sum.set_defaults(func=cmd_summarize)

    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())

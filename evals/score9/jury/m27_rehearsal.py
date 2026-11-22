#!/usr/bin/env python3
"""Учебный терминальный макет M27 на вымышленных событиях."""

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import sys
from time import monotonic


MIN_TIMELY = 4
MIN_ADVANTAGE = 2
MODES = ("warning", "report")


@dataclass(frozen=True)
class Event:
    id: str
    line: str
    arrived: str
    deadline: str
    allowed: tuple
    distractor: str


CASES = (
    Event("A1", "А", "09:00", "09:30", ("А", "В"), "В"),
    Event("A2", "Б", "09:50", "10:00", ("С", "Б"), "С"),
    Event("A3", "В", "11:00", "11:45", ("Л", "В"), "Л"),
    Event("A4", "Г", "12:00", "12:05", ("Г", "К"), "К"),
    Event("A5", "Д", "14:00", "14:30", ("Е", "Д"), "Е"),
    Event("A6", "Ж", "16:00", "16:20", ("Ж", "И"), "И"),
)


def seconds(value):
    hours, minutes = value.split(":")
    return 3600 * int(hours) + 60 * int(minutes)


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def notification(event, received, seen):
    if event.line not in event.allowed:
        return "forbidden"
    if event.id in seen:
        return "duplicate"
    if received >= seconds(event.deadline):
        return "stale"
    seen.add(event.id)
    return "shown"


def run_controls():
    first, second, third = CASES[:3]
    forbidden = Event(first.id, first.line, first.arrived, first.deadline, (), first.distractor)
    seen = set()
    if notification(third, seconds(third.arrived), seen) != "shown":
        raise ValueError("Не удалось подготовить показ первичного события для N3.")
    results = {
        "N1": (notification(forbidden, seconds(first.arrived), set()), "forbidden"),
        "N2": (notification(second, seconds("10:05"), set()), "stale"),
        "N3": (notification(third, seconds(third.arrived) + 1, seen), "duplicate"),
    }
    return {case: {"observed": got, "expected": expected, "passed": got == expected}
            for case, (got, expected) in results.items()}


def save(path, session):
    pending = path.with_suffix(path.suffix + ".tmp")
    with pending.open("x", encoding="utf-8") as output:
        json.dump(session, output, ensure_ascii=False, indent=2)
        output.write("\n")
    pending.replace(path)


def start(path, actor):
    if path.exists():
        raise ValueError("Для начала укажите новый файл состояния.")
    beginning = monotonic()
    started = utc_now()
    controls = run_controls()
    for case, row in controls.items():
        print(f"{case} · {row['observed']} · {'пройден' if row['passed'] else 'ошибка'}")
    ready = all(row["passed"] for row in controls.values())
    session = {
        "experiment": "M27/EXP-001",
        "started_utc": started,
        "control_seconds": round(monotonic() - beginning, 6),
        "actor": actor,
        "controls": controls,
        "attempts": [],
        "status": "ready" if ready else "stop",
        "model_calls": 0,
    }
    save(path, session)
    if not ready:
        print("Стоп. Сначала исправь обработку отрицательных контролей.")
        return 1
    print("Пауза. Следующий сценарий A1. Состояние записано.")
    return 0


def scripted_answer(event):
    if event.id == "A4":
        return event.line, float(seconds(event.deadline) - seconds(event.arrived))
    if event.id == "A5":
        return event.distractor, 15.0
    return event.line, 15.0


def human_answer(event):
    prompt = " / ".join(f"{i + 1} — линия {line}" for i, line in enumerate(event.allowed))
    start_time = monotonic()
    choice = input(f"{prompt} / Enter — пропустить: ").strip()
    elapsed = monotonic() - start_time
    options = {str(i + 1): line for i, line in enumerate(event.allowed)}
    return options.get(choice), elapsed


def run_case(event, actor, mode="warning", comparison=False):
    arrival = seconds(event.arrived)
    decision = notification(event, arrival, set())
    if decision != "shown":
        raise ValueError(f"{event.id}: прототип не показал доступное предупреждение ({decision}).")
    if mode == "warning":
        print(f"{event.id} · {event.arrived} · Линия {event.line}")
        print("  Скоро исчерпается пакет. Откройте карточку номера, чтобы проверить остаток.")
    else:
        print(f"{event.id} · Отчёт об остатках на {event.arrived}")
        print("  Номер       Состояние пакета")
        for line in event.allowed:
            status = "Скоро исчерпается" if line == event.line else "Пакета достаточно"
            print(f"  Линия {line}       {status}")
    chosen, elapsed = human_answer(event) if actor == "human" else scripted_answer(event)
    remaining = seconds(event.deadline) - arrival
    timely = chosen == event.line and elapsed < remaining
    reason = "вовремя" if timely else "другая линия" if chosen and chosen != event.line else (
        "пропуск" if chosen is None else "в сроке или позже")
    attempt = {"id": event.id, "chosen": chosen, "elapsed_s": elapsed,
               "deadline_s_from_event": remaining, "timely_correct": timely}
    if comparison:
        meaning = input("Что произошло? 1 — списались деньги / 2 — пакет скоро иссякнет / "
                        "3 — связь уже прервалась / Enter — пропустить: ").strip()
        if meaning and meaning not in {"1", "2", "3"}:
            meaning = "invalid"
        attempt.update(mode=mode, meaning=meaning or None, meaning_correct=meaning == "2")
    else:
        print(f"  Выбор {chosen or '—'} · {elapsed:.2f} с · {reason}")
    return attempt


def resume(path, limit):
    session = json.loads(path.read_text(encoding="utf-8"))
    if session["experiment"] != "M27/EXP-001" or session["status"] != "ready":
        raise ValueError("Для resume нужен открытый проход EXP-001.")
    completed = len(session["attempts"])
    actor = session["actor"]
    if actor == "human" and not sys.stdin.isatty():
        raise ValueError("Режим human требует интерактивного терминала.")
    beginning = monotonic()
    print(f"Продолжи M27 · следующий сценарий {CASES[completed].id} · режим {actor}")
    for event in CASES[completed:completed + limit]:
        attempt = run_case(event, actor)
        session["attempts"].append(attempt)
        if len(session["attempts"]) == len(CASES):
            timely = sum(row["timely_correct"] for row in session["attempts"])
            session.update(
                status="done", finished_utc=utc_now(), timely_correct=timely,
                scripted_gate=timely >= MIN_TIMELY if actor == "scripted" else None,
                exp001_gate=timely >= MIN_TIMELY if actor == "human" else None,
                human_prompt_wait_seconds=sum(row["elapsed_s"] for row in session["attempts"])
                if actor == "human" else None,
            )
        save(path, session)
    print(f"Команда продолжения выполнена за {monotonic() - beginning:.3f} с.")
    if session["status"] == "done":
        print(f"Итог · {session['timely_correct']}/{len(CASES)} своевременных · "
              f"{sum(row['passed'] for row in session['controls'].values())}/3 контроля.")
        print("EXP-001 · требуется ответ участника." if actor == "scripted" else
              f"EXP-001 · порог {'пройден' if session['exp001_gate'] else 'не пройден'}.")
    else:
        print(f"Пауза. Следующий сценарий {CASES[len(session['attempts'])].id}.")
    return 0


def comparison_summary(attempts):
    totals = {}
    for mode in MODES:
        rows = [row for row in attempts if row["mode"] == mode]
        totals[mode] = {
            "timely_correct": sum(row["timely_correct"] for row in rows),
            "meaning_correct": sum(row["meaning_correct"] for row in rows),
            "choice_wait_seconds": sum(row["elapsed_s"] for row in rows),
            "scenarios": len(rows),
        }
    advantage = totals["warning"]["timely_correct"] - totals["report"]["timely_correct"]
    if advantage >= MIN_ADVANTAGE:
        return totals, "keep_warning"
    if advantage == 0:
        return totals, "prefer_report"
    return totals, "report_ahead" if advantage < 0 else "below_margin"


def compare(prerequisite, path):
    if not sys.stdin.isatty():
        raise ValueError("EXP-002 требует интерактивного терминала.")
    input_bytes = prerequisite.read_bytes()
    gate = json.loads(input_bytes)
    if not (gate["experiment"] == "M27/EXP-001" and gate["actor"] == "human"
            and gate["status"] == "done" and gate["exp001_gate"] is True):
        raise ValueError("Нужен завершённый проход EXP-001 с участником и пройденным порогом.")
    fingerprint = sha256(input_bytes).hexdigest()
    if path.exists():
        session = json.loads(path.read_text(encoding="utf-8"))
        if not (session["experiment"] == "M27/EXP-002" and session["status"] == "ready"
                and session["prerequisite_sha256"] == fingerprint):
            raise ValueError("Продолжение требует открытого EXP-002 и того же исходного прохода.")
    else:
        started = utc_now()
        controls = run_controls()
        ready = all(row["passed"] for row in controls.values())
        session = {
            "experiment": "M27/EXP-002", "actor": "human",
            "started_utc": started, "prerequisite_sha256": fingerprint,
            "controls": controls, "attempts": [], "status": "ready" if ready else "stop",
            "model_calls": 0,
        }
        save(path, session)
        if not ready:
            print("Стоп. Отрицательный контроль обработки события не прошёл.")
            return 1

    # Two blocks space out repeat views of the same event and balance the first mode.
    order = [(event, MODES[(index + block) % 2])
             for block in range(2) for index, event in enumerate(CASES)]
    for index, (event, mode) in enumerate(order[len(session["attempts"]):],
                                          len(session["attempts"]) + 1):
        print(f"\nЭкран {index}/{len(order)}")
        session["attempts"].append(run_case(event, "human", mode, comparison=True))
        save(path, session)
    totals, outcome = comparison_summary(session["attempts"])
    session.update(status="done", finished_utc=utc_now(), totals=totals, outcome=outcome)
    save(path, session)
    for mode, name in zip(MODES, ("Предупреждение", "Отчёт")):
        total = totals[mode]
        print(f"{name} · {total['timely_correct']}/{total['scenarios']} своевременных · "
              f"{total['choice_wait_seconds']:.2f} с до ввода решений.")
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    subcommands = parser.add_subparsers(dest="command", required=True)
    first = subcommands.add_parser("start")
    first.add_argument("receipt", type=Path)
    first.add_argument("--actor", choices=("scripted", "human"), required=True)
    next_step = subcommands.add_parser("resume")
    next_step.add_argument("receipt", type=Path)
    next_step.add_argument("--limit", type=int, default=len(CASES))
    comparison = subcommands.add_parser("compare")
    comparison.add_argument("prerequisite", type=Path)
    comparison.add_argument("receipt", type=Path)
    args = parser.parse_args()
    try:
        if args.command == "start":
            return start(args.receipt, args.actor)
        if args.command == "compare":
            return compare(args.prerequisite, args.receipt)
        if args.limit < 1:
            parser.error("--limit должен быть положительным")
        return resume(args.receipt, args.limit)
    except (OSError, ValueError, EOFError) as error:
        parser.exit(1, f"{error}\n")


if __name__ == "__main__":
    raise SystemExit(main())

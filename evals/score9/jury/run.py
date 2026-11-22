#!/usr/bin/env python3
"""Локальная проверка пути жюри на временной копии поставки."""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory
from time import perf_counter

import yaml


HERE = Path(__file__).resolve().parent
FRESH = Path("projects/jury-library")
FAMILY = Path("projects/family-connect")
M27 = Path("projects/rehearsal-mobile-spend")
ESIM = Path("projects/esim-travel")
REPEAT_C3 = "повторное C3 открыто"


def require(condition, message):
    if not condition:
        raise ValueError(message)


def snapshot(directory):
    return {
        str(path.relative_to(directory)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(directory.rglob("*")) if path.is_file()
    }


def read(path):
    return path.read_text(encoding="utf-8")


def decisions(root, case):
    return yaml.safe_load(read(root / case / "state/ledger/decisions.yaml"))


def command_runner(root, env, commands):
    def run(script, *args, code=0, kind="commands", payload=None):
        argv = [sys.executable, f"tools/{script}.py", *map(str, args)]
        start = perf_counter()
        process = subprocess.run(
            argv, cwd=root, env=env, input=payload or "", capture_output=True,
            text=True, encoding="utf-8", timeout=45,
        )
        commands.append({
            "kind": kind, "argv": argv, "input": payload, "exit": process.returncode,
            "seconds": round(perf_counter() - start, 6),
            "stdout": process.stdout, "stderr": process.stderr,
        })
        require(process.returncode == code,
                f"{' '.join(argv)}: ожидался код {code}, получен {process.returncode}")
        return process.stdout
    return run


def fresh_c1(root, run):
    title = "Учебный пример — статус заявки в библиотеку"
    run("ledger", "init", FRESH, "--title", title)
    progress = root / FRESH / "state/progress.md"
    draft = root / FRESH / "outputs/01_brief.md"
    shutil.copyfile(HERE / "brief.md", draft)
    steps = read(progress).split("## Шаги\n", 1)[1].split("\n\n", 1)[0]
    progress.write_text(
        f"# Прогресс: {title}\n\n"
        "Текущий шаг: 1 — C1 открыт\n"
        "Среда: локальная проверка CLI\n\n"
        f"## Шаги\n{steps}\n"
        "\n## Что сделано в текущем шаге\n- Сохранён учебный бриф.\n"
        "\n## Что дальше\n- После подтверждения C1 составить план исследования.\n"
        "\n## Открытые вопросы к человеку\n- Роль: руководитель сервиса библиотеки\n"
        "- Ожидаемый ответ: подтвердить рамку или уточнить область работы (01).\n",
        encoding="utf-8",
    )
    run("ledger", "validate", FRESH)
    verified = json.loads(run("verify", FRESH, "--files", "01", "--strict", "--json"))
    run("ledger", "log", FRESH, "--step", "1", "--tier", "T0", "--model", "-",
        "--action", "Проверена учебная фикстура C1", "--result", "Бриф ожидает ответа",
        "--cost", "Модели и поиск не запускались")
    status = run("ledger", "status")
    line = next((line for line in status.splitlines() if "jury-library:" in line), "")
    require("шаг 1 — C1 открыт" in line and "шагов 0/8" in line,
            "Новый бриф должен ждать C1 с нулём завершённых шагов")
    require(sorted(path.name for path in draft.parent.iterdir()) == [draft.name]
            and not decisions(root, FRESH), "В новом кейсе появились артефакты за C1")
    run("report", FRESH, "--out", "jury-c1.html")
    checkpoint = read(root / "jury-c1.html").partition('id="checkpoint-approval"')[2].split("</div>", 1)[0]
    require(all(token in checkpoint for token in ("Согласование C1", 'href="#doc-01"',
                                                   "руководитель сервиса библиотеки")),
            "Отчёт C1 не показывает согласующего и ссылку на бриф")
    return {"state": line, "verify_checked_lines": verified["checked"]}


def saved_m27(root, run):
    run("ledger", "validate", M27)
    verified = json.loads(run("verify", M27, "--files", "03", "04", "05", "06", "07", "--strict", "--json"))
    findings = json.loads(run("consistency", M27, "--json"))
    chosen, = decisions(root, M27)
    rationale = "Дополнительная причина выбора не указана."
    require(chosen["state"] == "decided"
            and (chosen["chosen"], chosen["recommended"]) == ("ALT-001", "ALT-002")
            and rationale in chosen["rationale"] and not chosen.get("rejected"),
            "Сохранённый DEC-001 M27 изменился: перепроверьте воспроизведение F4")
    journal = read(root / M27 / "outputs/08_decision_log.md")
    rows = [line for line in journal.splitlines() if line.startswith(("| C2 |", "| C3 |"))]
    require(len(rows) == 2 and "ALT-001" in rows[0] and "Утверждаю пакет C3" in rows[1],
            "Исторические записи C2 и C3 не найдены в журнале M27")
    return {"historical_checkpoint_rows": rows, "decision": chosen["id"],
            "chosen": chosen["chosen"], "recommended": chosen["recommended"],
            "rationale": rationale, "rejected_recorded": False,
            "consistency": dict(Counter(f["status"] for f in findings)), "verify": verified["counts"]}


def resume_esim(root, run):
    startup = run("hooks/strategist_hook", "session-start", kind="hooks")
    line = next((line for line in startup.splitlines() if "esim-travel:" in line), "")
    require(REPEAT_C3 in line and "шаг 8" in line, "Хук не показал открытый повторный C3 eSIM")
    prompt = json.dumps({"prompt": "Продолжи", "session_id": "jury-local"}, ensure_ascii=False)
    require(not run("hooks/strategist_hook", "user-prompt", kind="hooks", payload=prompt).strip(),
            "Лексический хук неожиданно выдал подсказку на «Продолжи»")
    run("ledger", "validate", ESIM)
    trace = run("ledger", "trace", ESIM, "DEC-001")
    quote = run("ledger", "trace", ESIM, "CLM-016")
    require("ALT-001" in trace and "SRC-007" in trace and "SRC-006" in quote,
            "Ожидаемые цепочки от решения и уточнённого утверждения до источника отсутствуют")
    impact = json.loads(run("ledger", "impact", ESIM, "CHG-001", "--json"))
    for target, route in {
        "PAIN-001": ["CLM-011", "PAIN-001"],
        "PRM-001": ["DEC-001", "PRM-001"],
        "EXP-002": ["DEC-001", "EXP-002"],
    }.items():
        require(impact.get(target, {}).get("path") == route, f"Не найден путь до {target}")
    verified = json.loads(run("verify", ESIM, "--files", "03", "04", "05", "06", "07", "--json"))
    findings = json.loads(run("consistency", ESIM, "--json", code=1))
    blockers = [f for f in findings if f["status"] == "error"]
    require([f["code"] for f in blockers] == ["comms.comms_not_ready"],
            "Ожидалась остановка C3 из-за отсутствия активных PRM")
    run("report", ESIM, "--check")
    html = read(root / ESIM / "outputs/report.html")
    checkpoint = html.partition('id="checkpoint-approval"')[2].split("</div>", 1)[0]
    require(all(token in checkpoint for token in ("Согласование C3", 'href="#doc-06"', 'href="#doc-07"')),
            "В отчёте не найден блок согласования C3 и материалы")
    chosen, = decisions(root, ESIM)
    require(chosen["state"] == "decided" and chosen["chosen"] == "ALT-001",
            "Исторический выбор eSIM изменился")
    resume = json.loads(run("next", ESIM, "--resume", "--json"))
    saved = resume["resume"]
    require(saved["open_checkpoint"] == "C3" and saved["c3"]["status"] == "unrecorded"
            and {"decision": "DEC-001", "chosen": "ALT-001"} in saved["choices"]
            and "CHG-001" in {item["id"] for item in resume["items"]},
            "Возврат к eSIM должен показать выбор, изменение и открытое C3")
    return {"state": line, "chosen": chosen["chosen"], "impact_count": len(impact),
            "verify": verified["counts"], "blockers": blockers,
            "approval_role_recorded_in_report": "Роль не указана" not in checkpoint,
            "resume": saved}


def official_family(root, run):
    run("ledger", "validate", FAMILY)
    verified = json.loads(run("verify", FAMILY, "--files", "00", "01", "02", "03", "04",
                              "05", "06", "07", "--strict", "--json"))
    findings = json.loads(run("consistency", FAMILY, "--json"))
    package_sha = run("c3_approval", "prepare", FAMILY).strip()
    progress = read(root / FAMILY / "state/progress.md")
    reader = root / FAMILY / "outputs/00_brief_response.md"
    reader_sha = hashlib.sha256(reader.read_bytes()).hexdigest()
    require(f"SHA-256 пакета C3: `{package_sha}`" in progress
            and f"SHA-256 пятичастного документа: `{reader_sha}`" in progress,
            "Версии предъявленных материалов family-connect не совпадают с progress.md")
    recorded = (root / FAMILY / "state/c3_approvals.json").is_file()
    approval = json.loads(run("c3_approval", "check", FAMILY, "--json", code=0 if recorded else 1))
    require(approval["status"] == ("current" if recorded else "unrecorded"),
            "Квитанция C3 отсутствует либо не соответствует пакету family-connect")
    run("report", FAMILY, "--check")
    continuation = None
    resume = json.loads(run("next", FAMILY, "--resume", "--json"))["resume"]
    if recorded:
        status = run("ledger", "status")
        continuation = next((line for line in status.splitlines() if "family-connect:" in line), "")
        journal = read(root / FAMILY / "outputs/08_decision_log.md")
        require("шаг 8" in continuation and all(token in journal for token in (
            "DEC-001", "ALT-003", "EXP-001", package_sha,
        )), "Согласование family-connect должно открывать сопровождение с сохранённым решением")
        require((root / FAMILY / "outputs/09_run_report.md").is_file(),
                "В поставке отсутствуют замеры и отчёт о прогоне family-connect")
        require(resume["open_checkpoint"] is None and resume["c3"]["status"] == "current"
                and {"decision": "DEC-001", "chosen": "ALT-003"} in resume["choices"]
                and all(token in (resume["next_action"] or "") for token in ("EXP-001", "дизайнер")),
                "Возврат к family-connect должен показать утверждённый пакет и сухую проверку макета")
    else:
        html = read(root / FAMILY / "outputs/report.html")
        checkpoint = html.partition('id="checkpoint-approval"')[2].split("</div>", 1)[0]
        require(all(token in checkpoint for token in (
            "Согласование C3", "руководитель цифрового продукта", 'href="#doc-00"',
        )), "В отчёте family-connect отсутствует согласующий или ссылка на пятичастную стратегию")
    return {"verify": verified["counts"], "consistency": dict(Counter(f["status"] for f in findings)),
            "package_sha256": package_sha, "reader_sha256": reader_sha, "approval": approval["status"],
            "continuation": continuation, "resume": resume}


def negative_controls(root, run):
    draft = root / FRESH / "outputs/01_brief.md"
    original = read(draft)
    try:
        draft.write_text(original + "\nВыдача ускорится на 42% [CLM-999].\n", encoding="utf-8")
        report = json.loads(run("verify", FRESH, "--files", "01", "--strict", "--json", code=1))
        require(any(item["verdict"] == "wrong-ref" and "CLM-999" in item["text"]
                    for item in report["items"]), "verify не указал подложную ссылку CLM-999")
    finally:
        draft.write_text(original, encoding="utf-8")
    run("ledger", "impact", ESIM, "CHG-999", "--json", code=2)
    return {"verify": "CLM-999 → wrong-ref / 1", "impact": "CHG-999 → выход 2"}


def c3_record_controls(root, run, package_sha):
    case = Path("c3-record-control")
    copy = shutil.copytree(root / FAMILY, root / case)
    receipt = copy / "state/c3_approvals.json"
    receipt.unlink(missing_ok=True)
    (copy / "state/.ledger-write.lock").unlink(missing_ok=True)
    answer = root / "c3-response.txt"
    before = snapshot(copy)
    explicit = "Утверждаю пакет C3"

    def record(text, presented=package_sha, *, code=0):
        answer.write_text(text, encoding="utf-8")
        run("c3_approval", "record", case, "--sha256", presented,
            "--response-file", answer.name, code=code)

    for text in ("Не утверждаю пакет C3", "Утверждаю пакет C3, если внесёте правки", "Прочитал", ""):
        record(text, code=2)
        require(snapshot(copy) == before, "Отклонённая реплика изменила проект C3")
    record(explicit, "0" * 64, code=2)
    require(snapshot(copy) == before, "Устаревшая предъявленная версия изменила проект C3")
    record(explicit)
    approved = snapshot(copy)
    entries = json.loads(read(receipt))["approvals"]
    require(len(entries) == 1 and entries[0]["sha256"] == package_sha and entries[0]["response"] == explicit,
            "Явное согласие должно записать одну квитанцию предъявленного пакета")
    record(explicit)
    require(snapshot(copy) == approved, "Повторная запись C3 изменила квитанцию")
    return {"rejected_without_changes": 5, "recorded": len(entries), "retry_unchanged": True}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="включить команды и полные выводы")
    args = parser.parse_args()
    source = HERE.parents[2]
    require(sys.prefix != sys.base_prefix, "Запустите Python из подготовленной .venv")
    commands = []
    start = perf_counter()
    with TemporaryDirectory(prefix="strategist-jury-") as directory:
        root = Path(directory)
        home = root / "home"
        home.mkdir()
        env = {"PATH": os.defpath, "HOME": str(home), "USERPROFILE": str(home),
               "CODEX_THREAD_ID": "jury-local", "PYTHONDONTWRITEBYTECODE": "1", "PYTHONUTF8": "1"}
        hook_venv = root / ".venv"
        # SessionStart checks for a project-local environment before running ledger.
        if os.name == "nt":
            windows = Path(os.environ["SystemRoot"])
            temp = root / "tmp"
            temp.mkdir()
            env.update(SystemRoot=str(windows), PATH=str(windows / "System32"),
                       TEMP=str(temp), TMP=str(temp))
            shutil.copytree(sys.prefix, hook_venv)
        else:
            hook_venv.symlink_to(sys.prefix, target_is_directory=True)
        for path in ("tools", "schemas", "knowledge", "skills", FAMILY, M27, ESIM):
            shutil.copytree(source / path, root / path, ignore=shutil.ignore_patterns(
                "__pycache__", "*.pyc", "models.inventory.yaml", "*.local.yaml", "*.local.json", ".env*"))
        (root / ".codex").mkdir()
        shutil.copyfile(source / ".codex/hooks.json", root / ".codex/hooks.json")
        before = {case: snapshot(source / case) for case in (FAMILY, ESIM, M27)}
        run = command_runner(root, env, commands)
        try:
            family = official_family(root, run)
            results = {"official_family": family,
                       "fresh_c1": fresh_c1(root, run), "saved_m27": saved_m27(root, run),
                       "esim_repeat_c3": resume_esim(root, run),
                       "negative_controls": negative_controls(root, run),
                       "c3_record_controls": c3_record_controls(root, run, family["package_sha256"])}
            for case, files in before.items():
                require(snapshot(root / case) == snapshot(source / case) == files,
                        f"Содержимое {case} отличается от входного снимка")
        except (ValueError, OSError, subprocess.SubprocessError) as exc:
            print(json.dumps({"error": str(exc), "commands": commands}, ensure_ascii=False, indent=2),
                  file=sys.stderr)
            return 1
    results["unchanged_sha256_files"] = {str(case): len(files) for case, files in before.items()}
    results["timing_seconds"] = {
        kind: round(sum(c["seconds"] for c in commands if c["kind"] == kind), 3)
        for kind in ("hooks", "commands")
    } | {"wall": round(perf_counter() - start, 3), "human_active": None, "model": None}
    results["model_calls"] = 0
    results["commands_executed"] = len(commands)
    if args.json:
        results["commands"] = commands
    print(json.dumps(results, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

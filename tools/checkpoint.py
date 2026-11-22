#!/usr/bin/env python3
"""Проверка готовности к C1, C2 или C3 без вызова модели."""
from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path
import sys

import yaml

from c3_approval import digest, prepare
from decide import code_check, gate_file
from ledger import Ledger, validate
from next import build_resume
from strategy_checks import options
from verify import blocking_verdicts, verify

ARTIFACTS = {
    "C1": ("01_brief.md",),
    "C2": ("02_research_plan.md", "03_situation.md", "04_options.md"),
    "C3": ("05_product_strategy.md", "06_comms_strategy.md", "07_validation_plan.md"),
}
STEPS = {"C1": 1, "C2": 5, "C3": 7}


def required_structure(artifact: Path) -> list[str]:
    gate = gate_file(artifact)
    if gate is None:
        return []
    body = artifact.read_text(encoding="utf-8")
    questions = yaml.safe_load(gate.read_text(encoding="utf-8"))["questions"]
    return [
        f"{artifact.name}: {question['fix']}"
        for question in questions.values()
        if question.get("check") and not code_check(question["check"], body)
    ]


def main(argv=None) -> int:
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("stage", choices=ARTIFACTS)
    cli.add_argument("project", type=Path)
    args = cli.parse_args(argv)
    stage, project = args.stage, args.project.resolve()
    try:
        files = ARTIFACTS[stage]
        artifacts = [project / "outputs" / name for name in files]
        errors = [f"Не хватает outputs/{file.name}" for file in artifacts if not file.is_file()]
        if build_resume(project, date.today())["resume"]["open_checkpoint"] != stage:
            errors.append(f"В state/progress.md укажи «Текущий шаг: {STEPS[stage]} — {stage} открыт» "
                          "и вопросы в разделе «Открытые вопросы к человеку».")
        review = []
        for artifact in artifacts:
            if artifact.is_file():
                errors.extend(required_structure(artifact))
        if stage != "C3":
            report = validate(Ledger(project))
            errors.extend(report.errors)
            if not report.errors:
                items = verify(project, list(files), exact_files=True)["items"]
                errors.extend(f"{item['file']}:{item['line']}: {item['note']}" for item in items
                              if item["verdict"] in blocking_verdicts(strict=True))
                review.extend(f"outputs/{item['file']}:{item['line']}: {item['note']}" for item in items
                              if item["verdict"] == "check")
                if stage == "C2":
                    errors.extend(f"{item['code']}: {item['message']}" for item in options.check(project)
                                  if item["status"] == "error")
        if errors:
            print(*errors, sep="\n", file=sys.stderr)
            return 2
        if stage == "C3":
            print(f"SHA-256: {digest(prepare(project, review=review))}")
        for finding in review:
            print(f"На ручную сверку: {finding}")
        print(f"{stage}: кодовые проверки пройдены. Сверь смысл с источниками и предъяви пакет руководителю.")
    except (OSError, ValueError, TypeError, yaml.YAMLError) as exc:
        print(f"{stage}: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())

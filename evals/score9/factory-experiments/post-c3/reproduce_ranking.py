#!/usr/bin/env python3
"""Проверить смену лидера и согласование на временной копии семейного кейса."""
import argparse
from fractions import Fraction
import json
from pathlib import Path
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory

import yaml


def run(candidate, *args):
    return subprocess.run([sys.executable, *args], cwd=candidate, capture_output=True,
                          text=True, check=False)


def remove_basis(project):
    ledger = project / "state/ledger"
    claims_file = ledger / "claims.yaml"
    claims = yaml.safe_load(claims_file.read_text())
    by_id = {claim["id"]: claim for claim in claims}
    by_id["CLM-014"]["derived_from"].remove("CLM-013")
    if not by_id["CLM-014"]["derived_from"]:
        del by_id["CLM-014"]["derived_from"]
    claims_file.write_text(yaml.safe_dump(claims, allow_unicode=True, sort_keys=False))
    decisions_file = ledger / "decisions.yaml"
    decisions = yaml.safe_load(decisions_file.read_text())
    decisions[0]["based_on"].remove("CLM-014")
    decisions_file.write_text(yaml.safe_dump(decisions, allow_unicode=True, sort_keys=False))


def add_weight_update(project):
    claims_file = project / "state/ledger/claims.yaml"
    claims = yaml.safe_load(claims_file.read_text())
    claims.append({
        "id": "CLM-033", "type": "assumption", "statement": "Учебная поправка весов 60/15/15/10.",
        "confidence": {"level": "medium", "rationale": "Сценарий проверки пересмотра."},
    })
    claims_file.write_text(yaml.safe_dump(claims, allow_unicode=True, sort_keys=False))


def ranking(project, weights):
    rows = {}
    for row in (project / "outputs/04_options.md").read_text().splitlines():
        name, *cells = row.strip("|").split("|")
        if "(1–5)" in name:
            rows[name.strip()] = [int(cell.strip()) for cell in cells]
    if len(rows) != len(weights):
        raise ValueError("не удалось прочитать четыре строки оценок из таблицы вариантов")
    scores = [sum(weight * score for weight, score in zip(weights, column))
              for column in zip(*rows.values())]
    return {f"ALT-{i:03d}": float(score) for i, score in enumerate(scores, 1)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", default=Path.cwd(), type=Path)
    args = parser.parse_args()
    candidate = args.candidate.resolve()
    official = candidate / "projects/family-connect"
    with TemporaryDirectory(prefix="strategist-ranking-") as temporary:
        control = Path(temporary) / "without-links"
        shutil.copytree(official, control)
        remove_basis(control)
        project = Path(temporary) / "family-connect"
        shutil.copytree(official, project)
        before = json.loads(run(candidate, "tools/ledger.py", "impact", str(control), "CLM-013", "--json").stdout)
        warnings_before = json.loads(run(candidate, "tools/strategy_checks/options.py", str(control), "--json").stdout)
        warnings_after = json.loads(run(candidate, "tools/strategy_checks/options.py", str(project), "--json").stdout)
        add_weight_update(project)
        supersede = run(candidate, "tools/ledger.py", "supersede", str(project), "CLM-013", "--by", "CLM-033")
        if supersede.returncode:
            raise RuntimeError(supersede.stdout + supersede.stderr)
        after = json.loads(run(candidate, "tools/ledger.py", "impact", str(project), "CLM-013", "--json").stdout)
        status = json.loads(run(candidate, "tools/next.py", str(project), "--on", "2026-11-22", "--resume", "--json").stdout)
        decisions_file = project / "state/ledger/decisions.yaml"
        decisions = yaml.safe_load(decisions_file.read_text())
        decisions[0].update(state="needs_review", recommended="ALT-001")
        decisions_file.write_text(yaml.safe_dump(decisions, allow_unicode=True, sort_keys=False))
        review = json.loads(run(candidate, "tools/next.py", str(project), "--on", "2026-11-22", "--resume", "--json").stdout)
        report = project / "review.html"
        report_run = run(candidate, "tools/report.py", str(project), "--out", str(report))
        if report_run.returncode:
            raise RuntimeError(report_run.stdout + report_run.stderr)
        html_c2 = "Согласование C2" in report.read_text()
        approved = json.loads(run(candidate, "tools/c3_approval.py", "check", str(official), "--json").stdout)
        scores_before = ranking(project, [Fraction(value, 100) for value in (35, 25, 25, 15)])
        scores_after = ranking(project, [Fraction(value, 100) for value in (60, 15, 15, 10)])
    resume = status["resume"]
    result = {"before_impact": list(before), "decision_path": after["DEC-001"]["path"],
              "scores_before": scores_before, "scores_after": scores_after,
              "next_step": resume["open_checkpoint"], "next_action": resume["next_action"],
              "historical_choice": resume["choices"], "case_c3": approved["status"],
              "explicit_review": review["resume"], "html_c2": html_c2,
              "ranking_warning_before": any(item["code"] == "options.unlinked_ranking_basis" for item in warnings_before),
              "ranking_warning_after": any(item["code"] == "options.unlinked_ranking_basis" for item in warnings_after)}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return int(not (result["decision_path"] == ["CLM-013", "CLM-014", "DEC-001"]
                    and max(scores_before, key=scores_before.get) == "ALT-003"
                    and max(scores_after, key=scores_after.get) == "ALT-001"
                    and result["historical_choice"] == [{"decision": "DEC-001", "chosen": "ALT-003"}]
                    and review["resume"]["open_checkpoint"] == "C2"
                    and review["resume"].get("previous_choices") == result["historical_choice"]
                    and html_c2 and result["ranking_warning_before"] and not result["ranking_warning_after"]
                    and result["next_step"] == "C2" and result["case_c3"] == "current"))


if __name__ == "__main__":
    sys.exit(main())

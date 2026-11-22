from pathlib import Path
import re
import shutil
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]


def run(command, *args):
    return subprocess.run([sys.executable, "-B", str(ROOT / "tools" / command), *map(str, args)],
                          text=True, capture_output=True)


def open_stage(tmp_path, label):
    case = shutil.copytree(ROOT / "projects/family-connect", tmp_path / "family-connect")
    progress = case / "state/progress.md"
    progress.write_text(re.sub(r"^Текущий шаг:.*$", f"Текущий шаг: {label} открыт",
                               progress.read_text(encoding="utf-8"), count=1, flags=re.M), encoding="utf-8")
    return case


def test_c2_blocks_invalid_axes_and_missing_rule_in_one_call(tmp_path):
    case = open_stage(tmp_path, "5 — C2")
    assert run("checkpoint.py", "C2", case).returncode == 0
    options = case / "outputs/04_options.md"
    original = options.read_text(encoding="utf-8")
    invalid = original.replace("| Ось различия | ценность |", "| Ось различия | интерфейс |")
    invalid = re.sub(r"^.*Правило выбора.*\n", "", invalid, flags=re.M)
    invalid = re.sub(r"(?ms)^\| Критерий \| Вес \|.*?(?=\n\n)", "", invalid)
    invalid = re.sub(r"(?m)^\| Взвешенная сумма[^\n]+", "", invalid)
    options.write_text(invalid, encoding="utf-8")
    checked = run("checkpoint.py", "C2", case)
    assert checked.returncode == 2
    assert "options.invalid_axis" in checked.stderr
    assert "options.missing_weights" in checked.stderr


def test_c3_gate_outputs_the_exact_presentable_hash(tmp_path):
    case = open_stage(tmp_path, "7 — C3")
    shown = run("checkpoint.py", "C3", case)
    expected = run("c3_approval.py", "prepare", case)
    assert shown.returncode == expected.returncode == 0
    assert shown.stdout.splitlines()[0] == f"SHA-256: {expected.stdout.strip()}"


def test_c2_surfaces_numbers_that_require_manual_review(tmp_path):
    case = open_stage(tmp_path, "5 — C2")
    situation = case / "outputs/03_situation.md"
    with situation.open("a", encoding="utf-8") as stream:
        stream.write("\nНесверенное значение 777777% [CLM-001].\n")
    checked = run("checkpoint.py", "C2", case)
    assert checked.returncode == 0
    assert "На ручную сверку: outputs/03_situation.md:" in checked.stdout
    assert "777777" in checked.stdout

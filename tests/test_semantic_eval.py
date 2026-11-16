"""Scoring regressions, not model-quality results."""
import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('semantic_score', ROOT / 'evals/semantic/score.py')
S = importlib.util.module_from_spec(spec)
spec.loader.exec_module(S)


def test_scorer_separates_missed_defects_from_false_alarms():
    cases = S.load_cases(ROOT / 'evals/semantic/cases.json')
    always_ok = S.score(cases, {c['id']: 'ok' for c in cases})
    assert always_ok['correct'] == 9
    assert sum(len(a['missed_defects']) for a in always_ok['axes'].values()) == 9
    assert not any(a['false_alarms'] for a in always_ok['axes'].values())
    always_reject = S.score(cases, {c['id']: 'unsupported' for c in cases})
    assert sum(len(a['false_alarms']) for a in always_reject['axes'].values()) == 9
    assert not any(a['missed_defects'] for a in always_reject['axes'].values())


@pytest.mark.parametrize('bad', [{}, {'extra': 'ok'}, None])
def test_scorer_does_not_hide_missing_answers(bad):
    cases = S.load_cases(ROOT / 'evals/semantic/cases.json')
    with pytest.raises(ValueError):
        S.score(cases, bad)


@pytest.mark.parametrize('label', ['pass', [], None])
def test_scorer_rejects_unknown_verdict(label):
    cases = S.load_cases(ROOT / 'evals/semantic/cases.json')
    answers = {c['id']: 'ok' for c in cases}
    answers[cases[0]['id']] = label
    with pytest.raises(ValueError):
        S.score(cases, answers)

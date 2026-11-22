import copy
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
SCRIPT = ROOT / 'skills/alternatives/decision_evidence.py'
spec = importlib.util.spec_from_file_location('decision_evidence', SCRIPT)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


def load(name):
    return json.loads((HERE / (name + '.json')).read_text())


def test_real_case_and_public_price_control():
    result = mod.run(load('m27'), HERE)
    control = mod.run(load('m27-negative'), HERE)
    assert result['recommended'] == control['recommended'] == ['TODAY-02']
    assert result['options'][0]['status'] == 'conditional'
    assert control['evidence_effects'][-1] == {
        'id': 'CLM-D02', 'changes_admissibility': [], 'changes_recommendation': False}
    question = result['next_questions'][0]
    assert question['if_answer'] == {'false': ['TODAY-02'], 'true': ['TODAY-01']}
    assert result['evidence_effects'][0]['changes_recommendation']


def test_transfer_compatibility_and_missing_internal_outcomes():
    result = mod.run(load('esim'), HERE)
    assert result['recommended'] == ['TODAY-02']
    assert result['options'][0]['status'] == 'excluded'
    assert mod.assess(load('esim'), ['CLM-D03'])['options'][0]['status'] == 'conditional'
    assert mod.run(load('insufficient'), HERE)['recommended'] == []


def test_cli_refuses_without_recommendation():
    p = subprocess.run([sys.executable, str(SCRIPT), str(HERE / 'insufficient.json')], capture_output=True, text=True)
    assert p.returncode == 2
    assert 'ОТКАЗ' in p.stdout


def test_no_outcome_inference_scope_transfer_or_stale_evidence():
    for change in ('scope', 'outcome', 'hash', 'date'):
        case = load('m27')
        if change == 'scope': case['observations'][0]['scope'] = 'all tariffs'
        if change == 'outcome': case['conditions'][0]['type'] = 'own_outcome'
        if change == 'hash': case['observations'][0]['sha256'] = 'wrong'
        if change == 'date': case['as_of'] = '2026-11-20'
        try:
            mod.run(case, HERE)
        except ValueError:
            pass
        else:
            raise AssertionError(change)


def test_conflict_is_not_false_and_tie_is_not_hidden():
    case = load('m27')
    contrary = copy.deepcopy(case['observations'][0])
    contrary.update(id='CLM-CONFLICT', value=False)
    case['observations'].append(contrary)
    result = mod.run(case, HERE)
    assert result['options'][1]['status'] == 'conditional'
    assert result['conditions']['speed_limited']['conflict']
    case = load('m27')
    case['options'][2]['priority'] = 1
    assert mod.run(case, HERE)['recommended'] == ['TODAY-02', 'TODAY-03']


def test_short_report_shows_scope_and_conflict():
    case = load('m27')
    contrary = copy.deepcopy(case['observations'][0])
    contrary.update(id='CLM-CONFLICT', value=False)
    case['observations'].append(contrary)
    text = mod.render(case, mod.run(case, HERE))
    assert case['scope'] in text
    assert 'КОНФЛИКТ' in text
    assert '[speed_limited=True]' in text
    assert '[speed_limited=False]' in text


def test_unused_conditions_refused_and_commercial_gate_visible():
    case = load('esim')
    result = mod.run(case, HERE)
    question = next(q for q in result['next_questions'] if q['condition'] == 'retention')
    assert question['if_answer'] == {'false': ['TODAY-02'], 'true': ['TODAY-05']}
    case['options'].pop()
    try:
        mod.run(case, HERE)
    except ValueError as error:
        assert 'Условия без связи' in str(error)
    else:
        raise AssertionError('unused condition accepted')


def test_malformed_json_shapes_refused_without_traceback(tmp_path):
    for value in ([], {**load('m27'), 'observations': [{**load('m27')['observations'][0], 'url': None}]},
                  {**load('m27'), 'options': [{**load('m27')['options'][0], 'requires': []}] + load('m27')['options'][1:]}):
        path = tmp_path / 'invalid.json'
        path.write_text(json.dumps(value), encoding='utf-8')
        p = subprocess.run([sys.executable, str(SCRIPT), str(path)], capture_output=True, text=True)
        assert p.returncode == 2
        assert 'ОТКАЗ' in p.stderr and 'Traceback' not in p.stderr


def test_no_sources_is_structured_refusal_not_a_shutdown_advice():
    case = load('insufficient')
    assert case['observations'] == []
    result = mod.run(case, HERE)
    assert result['recommended'] == []
    for q in result['next_questions']:
        assert q['if_answer']['false'] == []
        assert len(q['if_answer']['true']) == 1

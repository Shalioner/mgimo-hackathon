import importlib.util
import json
from pathlib import Path

spec = importlib.util.spec_from_file_location('d1_quality', Path(__file__).resolve().parents[1]/'evals/routing/d1_quality.py')
Q = importlib.util.module_from_spec(spec)
spec.loader.exec_module(Q)


def case(ident):
    return {'id': ident, 'tier': ['T2'], 'effort': ['normal'], 'capability': ['text']}


def row(ident, tier, confidence, status='selected'):
    return {'id': ident, 'report': {'status': status, 'classification': {
        'route': {'result': 'succeeded'}, 'response': {'answers': {
            'tier': {'choice': tier, 'confidence': confidence},
            'effort': {'choice': 'normal', 'confidence': .9},
            'capability': {'choice': 'text', 'confidence': .9}}}}}}


def test_abstention_is_distinct_from_wrong_and_missing_stays_in_denominator():
    s = Q.summarize([case(str(i)) for i in range(4)], [
        row('0', 'T2', .6, 'needs_review'), row('1', 'T1', .9), row('2', 'T2', .8)])
    assert s['tasks'] == 4 and s['transport_success'] == 3
    assert s['top1_route_matches'] == 2 and s['correct_but_uncertain'] == 1
    assert s['selected_routes'] == 2 and s['selected_route_errors'] == 1
    assert s['rows'][3]['status'] == 'missing'


def test_policy_denial_does_not_become_classifier_uncertainty():
    s = Q.summarize([case('0')], [row('0', 'T2', .9, 'needs_review')])
    assert s['confident_routes'] == 1 and s['selected_routes'] == 0
    assert s['correct_but_uncertain'] == 0


def test_saved_receipt_survives_interrupted_batch_and_corrupt_is_visible(tmp_path):
    (tmp_path/'0.json').write_text(json.dumps(row('0', 'T2', .9)))
    (tmp_path/'1.json').write_text('{')
    cases = [case(str(i)) for i in range(3)]
    s = Q.summarize(cases, Q.saved_rows(tmp_path, cases))
    assert s['tasks'] == 3 and s['selected_routes'] == 1
    assert [r['status'] for r in s['rows']] == ['selected', 'corrupt_receipt', 'missing']

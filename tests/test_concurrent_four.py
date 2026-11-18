import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('four', ROOT/'evals/routing/concurrent_four.py')
B = importlib.util.module_from_spec(spec)
spec.loader.exec_module(B)


def test_missing_results_remain_in_denominator(tmp_path):
    B.write(tmp_path/'manifest.json', {'order': ['r1-d1', 'r1-d1_luna', 'r1-luna', 'r1-none'],
                                      'cases': [{'id': f'C{i}'} for i in range(1, 6)]})
    rows = B.summarize(tmp_path)['rows']
    assert len(rows) == 40
    assert all(r['status'] == 'missing_result' and not r['accounting_complete'] for r in rows)


def test_completed_call_survives_lost_step_and_unfinished_is_unknown(tmp_path):
    B.write(tmp_path/'manifest.json', {'order': ['r1-routed'], 'cases': [{'id': 'C1'}]})
    folder = tmp_path/'r1-routed'
    folder.mkdir()
    B.write(folder/'C1-step1-call01-start.json', {'plan': {'purpose': 'routing_classifier'}})
    B.write(folder/'C1-step1-call01-end.json', {'native_receipt': {
        'transmitted_model': 'gpt-6-luna', 'transmitted_effort': 'low', 'status': 'succeeded',
        'elapsed_seconds': 1, 'usage_events': [{'params': {'tokenUsage': {'total': {
            'inputTokens': 100, 'cachedInputTokens': 20, 'outputTokens': 10, 'reasoningOutputTokens': 5}}}}]}})
    B.write(folder/'C1-step1-call02-start.json', {'plan': {'purpose': 'routing_classifier'}})
    row = B.summarize(tmp_path)['rows'][0]
    assert row['calls'][0]['purpose'] == 'routing_classifier'
    assert row['tokens'] == {'input': 100, 'cached': 20, 'output': 10, 'reasoning_output': 5}
    assert row['missing_usage_calls'] == 1 and not row['accounting_complete']
    assert row['status'] == 'missing_result'


def test_unstarted_plan_is_not_actual_switch(tmp_path):
    B.write(tmp_path/'manifest.json', {'order': ['r1-routed'], 'cases': [{'id': 'C1'}]})
    folder = tmp_path/'r1-routed'
    folder.mkdir()
    B.write(folder/'C1-step1.json', {'case': 'C1', 'step': 1, 'arm': 'routed', 'elapsed_seconds': 1,
        'report': {'status': 'manual', 'result': {'attempts': [
            {'plan': {'requested': {'model': 'gpt-6.1-sol', 'effort': 'high'}},
             'route': {'native_receipt': {'transmitted_model': None}}}]}}})
    assert B.summarize(tmp_path)['rows'][0]['task_sequence'] == []


def test_case_inputs_have_no_expected_values_injected():
    cases = json.loads((ROOT/'evals/routing/four_cases.json').read_text())['cases']
    assert len(cases) == 5
    for case in cases:
        assert len(case['steps']) == 2
        for step in case['steps']:
            assert 'expectedfacts' not in step['request']
            assert step['baseline']['tier'] in ('T1', 'T2', 'T3')
            assert isinstance(step['context'], str) and step['context']

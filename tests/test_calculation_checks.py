import copy
import json

import pytest

import calculation_checks as C


def ref(key):
    return {'source': 'SRC-A', 'pointer': '/values/' + key}


def contract(expression=None):
    return {'calculation_sources': {'SRC-A': {'locator': 'table 1, rows 1-2',
                'values': {'a': 95, 'b': 20, 'weight': .5, 'count': 80, 'base': 10000}}},
            'calculation_checks': [{'id': 'standardized', 'answer_pointer': '/facts/rate',
                'expression': expression or {'op': 'weighted_mean', 'values': [ref('a'), ref('b')],
                                             'weights': [ref('weight'), ref('weight')]},
                'tolerance': '0.01'}]}


def test_standardization_catches_actual_benchmark_error_and_preserves_locators():
    result = C.check(json.dumps(contract()), '{"facts":{"rate":62.5}}')
    assert result['status'] == 'failed'
    row = result['checks'][0]
    assert row['expected'] == '57.5' and row['actual'] == '62.5'
    assert {s['pointer'] for s in row['sources']} == {'/values/a', '/values/b', '/values/weight'}
    assert all(s['locator'] == 'table 1, rows 1-2' for s in row['sources'])
    assert C.check(contract(), {'facts': {'rate': 57.5}})['status'] == 'verified'


def test_changed_source_values_recompute_instead_of_using_fixture_answer():
    ctx = contract()
    ctx['calculation_sources']['SRC-A']['values']['a'] = 80
    assert C.check(ctx, {'facts': {'rate': 50}})['status'] == 'verified'
    assert C.check(ctx, {'facts': {'rate': 57.5}})['status'] == 'failed'


@pytest.mark.parametrize('op,keys,expected', [
    ('sum', ['a', 'b'], 115), ('subtract', ['a', 'b'], 75),
    ('multiply', ['a', 'b'], 1900), ('ratio', ['a', 'b'], 4.75),
    ('percent', ['count', 'base'], .8)])
def test_safe_operations(op, keys, expected):
    ctx = contract({'op': op, 'args': [ref(k) for k in keys]})
    assert C.check(ctx, {'facts': {'rate': expected}})['status'] == 'verified'


def test_nested_source_calculation_and_array_pointer():
    ctx = contract({'op': 'subtract', 'args': [
        {'op': 'weighted_mean', 'values': [ref('a'), ref('b')], 'weights': [ref('weight'), ref('weight')]},
        {'source': 'SRC-A', 'pointer': '/values/previous/0'}]})
    ctx['calculation_sources']['SRC-A']['values']['previous'] = [50]
    assert C.check(ctx, {'facts': {'rate': 7.5}})['status'] == 'verified'


@pytest.mark.parametrize('context', ['ordinary text', None, {}, {'sources': []}])
def test_absent_contract_does_not_claim_verification(context):
    assert C.check(context, {'facts': {'rate': 62.5}})['status'] == 'not_applicable'


@pytest.mark.parametrize('answer', ['not JSON', [], {}, {'facts': {}},
                                  {'facts': {'rate': True}}, {'facts': {'rate': '57.5'}},
                                  {'facts': {'rate': float('nan')}}])
def test_missing_or_invalid_answer_needs_review(answer):
    assert C.check(contract(), answer)['status'] == 'needs_review'


@pytest.mark.parametrize('change', [
    lambda c: c['calculation_sources']['SRC-A']['values'].update(weight=.6),
    lambda c: c['calculation_sources']['SRC-A']['values'].update(weight=-.5),
    lambda c: c['calculation_sources']['SRC-A']['values'].update(a=float('inf')),
    lambda c: c['calculation_sources']['SRC-A']['values'].update(a=True),
    lambda c: c['calculation_sources']['SRC-A']['values'].update(a=10**19),
    lambda c: c['calculation_sources']['SRC-A'].update(locator=''),
    lambda c: c['calculation_checks'][0].update(tolerance='NaN'),
    lambda c: c['calculation_checks'][0].update(tolerance='100'),
    lambda c: c['calculation_checks'][0].update(expression={'op': 'eval', 'args': ['1+1']}),
    lambda c: c['calculation_checks'][0].update(expression={'op': 'sum', 'args': [57.5]}),
    lambda c: c['calculation_checks'][0].update(expression={'source': 'answer', 'pointer': '/facts/rate'}),
    lambda c: c['calculation_checks'][0].update(answer_pointer='/conclusion'),
    lambda c: c['calculation_checks'].append(copy.deepcopy(c['calculation_checks'][0])),
    lambda c: c.update(calculation_checks=[]),
])
def test_invalid_contract_fails_closed_before_answer_comparison(change):
    ctx = contract()
    change(ctx)
    result = C.check(ctx, {'facts': {'rate': 57.5}})
    assert result['status'] == 'invalid' and result['checks'] == []


def test_division_zero_and_bad_pointer_are_invalid():
    ctx = contract({'op': 'ratio', 'args': [ref('count'), ref('base')]})
    ctx['calculation_sources']['SRC-A']['values']['base'] = 0
    assert C.check(ctx, {'facts': {'rate': 0}})['status'] == 'invalid'
    ctx = contract({'source': 'SRC-A', 'pointer': '/values/no-such-value'})
    assert C.check(ctx, {'facts': {'rate': 0}})['status'] == 'invalid'


def test_expression_depth_and_node_count_are_bounded():
    expr = ref('a')
    for _ in range(10):
        expr = {'op': 'sum', 'args': [expr]}
    assert C.check(contract(expr), {'facts': {'rate': 95}})['status'] == 'invalid'
    expr = {'op': 'sum', 'args': [{'op': 'sum', 'args': [ref('a')]*32}]*32}
    assert C.check(contract(expr), {'facts': {'rate': 0}})['status'] == 'invalid'


def test_malformed_opted_in_json_is_invalid_and_tolerance_is_exact_decimal():
    assert C.check('{"calculation_checks":', '{}')['status'] == 'invalid'
    ctx = contract()
    assert C.check(ctx, '{"facts":{"rate":57.51}}')['status'] == 'verified'
    assert C.check(ctx, '{"facts":{"rate":57.511}}')['status'] == 'failed'


def test_underflow_does_not_silently_turn_source_into_zero():
    ctx = json.dumps(contract()).replace('95', '1e-1000000')
    assert C.check(ctx, '{"facts":{"rate":10}}')['status'] == 'invalid'

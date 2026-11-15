import copy

import pytest

import tool_context as T


def history():
    return [{'role': 'system', 'text': 'Do not lose this rule'},
            {'role': 'assistant', 'text': 'Original prose',
             'tool_calls': [{'id': 'old', 'name': 'read', 'arguments': {'path': 'old.txt'}}]},
            {'role': 'tool', 'text': '', 'tool_results': [{'id': 'old', 'text': 'я' * 10000}]},
            {'role': 'user', 'text': 'Current task'}]


def asker(call=.01, result=.01, status='succeeded'):
    def infer(state, qs, **kwargs):
        assert 'я' * 100 not in state
        return {'status': status, 'route': {'started': kwargs['execute']},
                'response': {'answers': {key: {'type': 'noul', 'noul': call if key.endswith('_call') else result}
                                         for key in qs}}}
    return infer


def test_drop_pair_preserve_prose_and_input():
    messages = history()
    before = copy.deepcopy(messages)
    report = T.compact(messages, 'task', execute=True, preserve_recent=1, infer=asker())
    assert report['status'] == 'compacted'
    assert report['stats']['removed_bytes'] > 19000
    assert report['stats']['classifier_requests'] == 1
    assert report['stats']['token_savings'] is None
    assert messages == before
    assert [m['text'] for m in messages] == [m['text'] for m in report['messages']]
    assert report['messages'][1]['tool_calls'] == report['messages'][2]['tool_results'] == []


def test_truncate_with_explicit_note():
    result = T.compact(history(), 'task', execute=True, preserve_recent=1, infer=asker(.99, .01))
    text = result['messages'][2]['tool_results'][0]['text']
    assert text.startswith('я' * 300) and 'original retained' in text
    assert result['messages'][1]['tool_calls'][0]['id'] == 'old'


@pytest.mark.parametrize('prob', [.11, .5, .89, .99])
def test_uncertain_or_needed_results_stay(prob):
    original = history()
    result = T.compact(original, 'task', execute=True, preserve_recent=1, infer=asker(.01, prob))
    assert result['status'] == 'unchanged' and result['messages'] == original


@pytest.mark.parametrize('reason', ['recent', 'protected_call', 'protected_result', 'error', 'small', 'no_calls'])
def test_t0_skip_does_not_infer(reason):
    messages = history()
    recent = 1
    if reason == 'recent':
        recent = 3
    elif reason == 'protected_call':
        messages[1]['tool_calls'][0]['protected'] = True
    elif reason == 'protected_result':
        messages[2]['tool_results'][0]['protected'] = True
    elif reason == 'error':
        messages[2]['tool_results'][0]['is_error'] = True
    elif reason == 'small':
        messages[2]['tool_results'][0]['text'] = 'small'
    else:
        messages = [{'role': 'user', 'text': 'large' * 10000}]
    result = T.compact(messages, 'task', execute=True, preserve_recent=recent,
                       infer=lambda *a, **k: pytest.fail('Must skip inference'))
    assert result['status'] == 'unchanged'


@pytest.mark.parametrize('failure', ['unavailable', 'missing', 'nan'])
def test_failure_retains_entire_history(failure):
    def infer(state, qs, **kwargs):
        answer = asker()(state, qs, **kwargs)
        if failure == 'unavailable':
            answer['status'] = 'unavailable'
        elif failure == 'missing':
            answer['response']['answers'].pop('0_call')
        else:
            answer['response']['answers']['0_call']['noul'] = float('nan')
        return answer
    original = history()
    result = T.compact(original, 'task', execute=True, preserve_recent=1, infer=infer)
    assert result['status'] == 'unchanged' and result['messages'] == original


def test_incomplete_or_duplicate_pairs_rejected():
    messages = history()
    messages[2]['tool_results'].clear()
    with pytest.raises(ValueError, match='Complete'):
        T.compact(messages, 'task')
    messages = history()
    messages[1]['tool_calls'] *= 2
    with pytest.raises(ValueError, match='unique'):
        T.compact(messages, 'task')


def test_pure_preflight():
    seen = []
    def infer(*args, **kwargs):
        seen.append(kwargs['execute'])
        return {'status': 'pending', 'route': {'started': False}}
    result = T.compact(history(), 'task', preserve_recent=1, infer=infer)
    assert seen == [False] and result['status'] == 'pending'
    assert result['messages'] == history()


def test_unavailable_provider_with_no_receipt():
    result = T.compact(history(), 'task', execute=True, preserve_recent=1,
                       infer=lambda *a, **kw: {'status': 'unavailable', 'route': None})
    assert result['messages'] == history() and result['stats']['classifier_requests'] == 0


def test_bounded_batches_and_atomic_failure():
    messages = [{'role': 'user', 'text': 'Goal'}]
    for i in range(10):
        messages.extend([{'role': 'assistant', 'tool_calls': [{'id': str(i), 'name': 'read', 'arguments': {}}]},
                         {'role': 'tool', 'tool_results': [{'id': str(i), 'text': 'x' * 2000}]}])
    messages.append({'role': 'user', 'text': 'latest'})
    calls = []
    def infer(state, qs, **kwargs):
        calls.append(qs)
        return asker()(state, qs, **kwargs)
    result = T.compact(messages, 'task', execute=True, preserve_recent=1, max_batches=2, infer=infer)
    assert len(calls) == 2 and all(len(qs) == 8 for qs in calls)
    assert len(result['decisions']) == 8
    assert result['messages'][-2]['tool_results'][0]['id'] == '9'
    calls.clear()
    def fail_second(state, qs, **kwargs):
        calls.append(qs)
        return asker(status='unavailable' if len(calls) == 2 else 'succeeded')(state, qs, **kwargs)
    result = T.compact(messages, 'task', execute=True, preserve_recent=1, infer=fail_second)
    assert result['messages'] == messages

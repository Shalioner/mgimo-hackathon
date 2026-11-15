"""Exact reuse over real sockets with a fake worker; no paid or model calls."""
from concurrent.futures import ThreadPoolExecutor
import copy
import threading
import time

import d1_service as S
from test_d1_service import setup, server, message, wait_until  # noqa: F401


def enable(server, capacity=128):
    path, supervisor, _, _ = server
    supervisor.cache_entries = capacity
    return path, supervisor


def test_exact_hit_has_own_id_zero_compute_and_immutable_response(server):
    path, s = enable(server)
    first = S.request(path, message(ident='first'))
    second = S.request(path, message(ident='second'))
    assert second['id'] == 'second' and second['runtime']['reuse'] == 'cache'
    assert first['response']['answers'] == second['response']['answers']
    assert first['response']['usage']['input_tokens'] == 10
    assert second['response']['usage']['input_tokens'] == 0
    assert second['runtime']['source_usage']['input_tokens'] == 10
    assert second['runtime']['inference_calls'] == 0
    assert second['runtime']['timing']['worker_inference_seconds'] == 0
    second['response']['answers'].clear()
    assert S.request(path, message())['response']['answers']
    assert s.completed == 1
    assert S.request(path, message(ident='second'))['status'] == 'duplicate'


def test_five_concurrent_duplicates_compute_once(server):
    path, s = enable(server)
    barrier = threading.Barrier(5)
    def call(i):
        barrier.wait(timeout=5)
        return S.request(path, message('slow', ident=f'agent-{i}', timeout=10))
    with ThreadPoolExecutor(max_workers=5) as pool:
        results = list(pool.map(call, range(5)))
    assert [r['id'] for r in results] == [f'agent-{i}' for i in range(5)]
    assert all(r['status'] == 'succeeded' for r in results)
    assert sum(r['runtime']['inference_calls'] for r in results) == 1
    assert s.completed == 1 and s.cache_hits == 4


def test_different_context_question_order_and_bypass_do_not_hit(server):
    path, s = enable(server)
    original = message()
    assert S.request(path, original)['runtime']['reuse'] == 'miss'
    variants = [message('other'), message(), message(), message()]
    variants[1]['questions'] = copy.deepcopy(variants[1]['questions'])
    variants[1]['questions']['tier']['instructions'] += ' changed'
    variants[2]['questions'] = copy.deepcopy(variants[2]['questions'])
    variants[2]['questions']['tier']['criteria'] = {'T2': 'medium', 'T1': 'simple'}
    variants[3]['reuse'] = False
    for request in variants:
        assert S.request(path, request)['runtime']['inference_calls'] == 1
    assert s.completed == 5


def test_ttl_lru_and_restart_namespace(server):
    path, s = enable(server, capacity=1)
    S.request(path, message('a'))
    S.request(path, message('b'))
    assert len(s.cache) == 1
    assert S.request(path, message('a'))['runtime']['inference_calls'] == 1
    with s.lock:
        key = next(iter(s.cache))
        _, value = s.cache[key]
        s.cache[key] = (time.monotonic()-1, value)
    assert S.request(path, message('a'))['runtime']['inference_calls'] == 1
    s.cache_namespace = (*s.cache_namespace, 'new-calibration')
    assert S.request(path, message('a'))['runtime']['inference_calls'] == 1


def test_cancelling_duplicate_does_not_cancel_other_callers(server):
    path, s = enable(server)
    with ThreadPoolExecutor(max_workers=2) as pool:
        active = pool.submit(S.request, path, message('slow', ident='active'))
        wait_until(lambda: s.active_id == 'active')
        duplicate = pool.submit(S.request, path, message('slow', ident='cancel-me'))
        wait_until(lambda: 'cancel-me' in s.pending)
        assert S.request(path, {'op': 'cancel', 'id': 'cancel-me'})['status'] == 'cancelled'
        assert duplicate.result()['status'] == 'cancelled'
        assert active.result()['status'] == 'succeeded'
    assert S.request(path, message('slow'))['runtime']['reuse'] == 'cache'
    assert s.completed == 1


def test_errors_and_expired_duplicates_are_not_replayed(server):
    path, s = enable(server)
    with ThreadPoolExecutor(max_workers=2) as pool:
        active = pool.submit(S.request, path, message('slow', ident='active'))
        wait_until(lambda: s.active_id == 'active')
        expired = S.request(path, message('slow', timeout=0.05))
        assert expired['status'] == 'timeout'
        assert active.result()['status'] == 'succeeded'
    wait_until(lambda: not s.pending)
    assert s.cache_hits == 0
    assert S.request(path, message('crash'))['status'] != 'succeeded'
    assert len(s.cache) == 1  # only successful slow result


def test_reuse_flag_must_be_boolean(server):
    path, s = enable(server)
    request = message()
    request['reuse'] = 'false'
    assert S.request(path, request)['status'] == 'invalid_request'
    assert s.completed == 0


def test_active_id_stays_reserved_after_seen_history_eviction(server):
    path, s = enable(server)
    with ThreadPoolExecutor(max_workers=1) as pool:
        active = pool.submit(S.request, path, message('slow', ident='active'))
        wait_until(lambda: s.active_id == 'active')
        with s.lock:
            original = s.pending['active']
            # Same state as eviction by a large stream of fast cache hits.
            s.seen.pop('active')
        result = S.request(path, message(ident='active'))
        assert result['status'] == 'duplicate'
        assert s.pending['active'] is original
        assert active.result()['status'] == 'succeeded'

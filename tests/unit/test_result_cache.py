# -*- coding: utf-8 -*-
"""进程内结果缓存 + 单飞（v2.37 审查报告 ⑬）。

动机：用户点一次「查询」要花 9 秒 + 300~900 加权额度；同一组参数在几分钟内
再点一次（切城市来回、重看同一份报告）没有任何理由重算。缓存键必须覆盖
所有影响结果的查询参数，命中时要有可观测的标记。
"""
import threading
import time

import pytest

from app.services.result_cache import ResultCache


def test_same_key_computes_once():
    c = ResultCache(ttl=60)
    calls = []
    v1, hit1 = c.get_or_compute('k', lambda: calls.append(1) or {'a': 1})
    v2, hit2 = c.get_or_compute('k', lambda: calls.append(1) or {'a': 2})
    assert v1 == {'a': 1} and v2 == {'a': 1}
    assert (hit1, hit2) == (False, True)
    assert len(calls) == 1


def test_different_keys_are_independent():
    c = ResultCache(ttl=60)
    c.get_or_compute('k1', lambda: 1)
    v, hit = c.get_or_compute('k2', lambda: 2)
    assert v == 2 and hit is False


def test_ttl_expiry_recomputes():
    c = ResultCache(ttl=0.05)
    c.get_or_compute('k', lambda: 'old')
    time.sleep(0.08)
    v, hit = c.get_or_compute('k', lambda: 'new')
    assert v == 'new' and hit is False


def test_errors_are_not_cached():
    c = ResultCache(ttl=60)

    def boom():
        raise RuntimeError('nope')

    with pytest.raises(RuntimeError):
        c.get_or_compute('k', boom)
    v, hit = c.get_or_compute('k', lambda: 'ok')
    assert v == 'ok' and hit is False


def test_per_value_ttl():
    """降级结果只能用短 TTL：否则"数据源缺失"的报告会被缓存 5 分钟。"""
    c = ResultCache(ttl=60)
    c.get_or_compute('k', lambda: {'data_missing': ['forecast']},
                     ttl_fn=lambda v: 0.05)
    time.sleep(0.08)
    v, hit = c.get_or_compute('k', lambda: {'data_missing': []},
                              ttl_fn=lambda v: 0.05)
    assert v == {'data_missing': []} and hit is False


def test_single_flight_concurrent():
    """并发同键只算一次（防击穿）。"""
    c = ResultCache(ttl=60)
    calls = []
    start = threading.Barrier(8)

    def worker():
        start.wait()
        c.get_or_compute('k', lambda: calls.append(1) or {'v': 1})

    ts = [threading.Thread(target=worker) for _ in range(8)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert len(calls) == 1


def test_max_entries_evicted():
    c = ResultCache(ttl=60, max_entries=4)
    for i in range(10):
        c.get_or_compute(f'k{i}', lambda i=i: i)
    assert len(c) <= 4


def test_stats_and_clear():
    c = ResultCache(ttl=60)
    c.get_or_compute('k', lambda: 1)
    c.get_or_compute('k', lambda: 1)
    s = c.stats()
    assert s['misses'] == 1 and s['hits'] == 1 and s['entries'] == 1
    c.clear()
    assert c.stats()['entries'] == 0

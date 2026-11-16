# -*- coding: utf-8 -*-
"""进程内结果缓存 + 单飞（v2.37 审查报告 ⑬）。

动机：点一次「查询」要花 9 秒、300~900 加权额度，而同一组参数在几分钟内再点
一次（切城市来回、重看同一份报告、深链刷新）没有任何理由重算。上游数据本身
在 openmeteo 层已有 3 小时缓存，所以这里缓存的是"同一输入的装配结果"。

设计要点：
- 键由调用方给（必须覆盖所有影响结果的查询参数，见 app/main.py 的 _report_key）
- 单飞：并发同键只算一次，其余等结果（防击穿）
- 失败不入缓存（错误不该被固化）
- 每条可带自己的 TTL：降级结果（data_missing/weather_error）只能用短 TTL
"""
import logging
import os
import threading
import time

log = logging.getLogger(__name__)

RESULT_TTL = int(os.environ.get('GC_RESULT_TTL') or 300)
RESULT_TTL_DEGRADED = int(os.environ.get('GC_RESULT_TTL_DEGRADED') or 60)
RESULT_MAX = int(os.environ.get('GC_RESULT_MAX') or 64)


class ResultCache:
    def __init__(self, ttl=RESULT_TTL, max_entries=RESULT_MAX):
        self._ttl = ttl
        self._max = max_entries
        self._lock = threading.Lock()
        self._items = {}        # key -> (expire_at, value)
        self._inflight = {}     # key -> Event
        self._hits = 0
        self._misses = 0

    def __len__(self):
        with self._lock:
            return len(self._items)

    def clear(self):
        with self._lock:
            self._items.clear()

    def stats(self):
        with self._lock:
            return {'entries': len(self._items), 'hits': self._hits,
                    'misses': self._misses}

    def _peek(self, key, now):
        ent = self._items.get(key)
        if ent and ent[0] > now:
            return ent[1]
        if ent:
            self._items.pop(key, None)
        return None

    def get_or_compute(self, key, fn, ttl=None, ttl_fn=None):
        """返回 (value, hit)。同一键并发调用时只有一个线程真正执行 fn。"""
        now = time.time()
        with self._lock:
            val = self._peek(key, now)
            if val is not None:
                self._hits += 1
                return val, True
            ev = self._inflight.get(key)
            if ev is None:
                ev = self._inflight[key] = threading.Event()
                mine = True
            else:
                mine = False
        if not mine:
            ev.wait(timeout=120)      # 等第一个线程算完再读缓存
            with self._lock:
                val = self._peek(key, time.time())
                if val is not None:
                    self._hits += 1
                    return val, True
                self._misses += 1
            return fn(), False        # 对方失败了：自己算（不再占坑）
        try:
            value = fn()
        except Exception:
            with self._lock:
                self._inflight.pop(key, None)
                self._misses += 1
            ev.set()
            raise
        use_ttl = ttl if ttl is not None else (
            ttl_fn(value) if ttl_fn else self._ttl)
        with self._lock:
            self._items[key] = (time.time() + use_ttl, value)
            self._inflight.pop(key, None)
            self._misses += 1
            if len(self._items) > self._max:      # 丢最早过期的
                for k in sorted(self._items, key=lambda k: self._items[k][0])[
                        :len(self._items) - self._max]:
                    self._items.pop(k, None)
        ev.set()
        return value, False

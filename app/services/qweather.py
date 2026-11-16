# -*- coding: utf-8 -*-
"""和风天气实况服务：地面实测交叉验证
- /v7/weather/now：实况云量(总)/能见度/湿度/露点/天气现象 —— 与卫星 B13（中高云）
  和模式做三方交叉验证：实况云量高但卫星光路无中高云 => 低云/雾遮挡预警
- /v7/minutely/5m：未来 2 小时分钟级降水 => 雨幕临近预警
key 存于环境变量 QWEATHER_HOST/QWEATHER_KEY，或回退 app/config_private.json
（不随静态目录暴露）
"""
import json
import logging
import os
import threading
import time

import requests

log = logging.getLogger(__name__)

_HERE = os.path.dirname(os.path.abspath(__file__))
_CFG = os.path.join(os.path.dirname(_HERE), 'config_private.json')
_CACHE = {}

# 实况缓存（v2.37 审查报告 ⑩）：实况本身的更新粒度是 5~10 分钟，而前端在
# 事件窗口内每 10 分钟自动拉一次 /api/nowcast，每次又会打 2 个 HTTP —— 没有
# 缓存时和风额度被反复消耗，且失败时静默消失、完全不可观测。
QW_CACHE_TTL = int(os.environ.get('GC_QW_CACHE_TTL') or 90)
QW_FAIL_TTL = int(os.environ.get('GC_QW_FAIL_TTL') or 30)   # 失败也短暂缓存
_Q_LOCK = threading.Lock()
_Q_CACHE = {}          # 'lat,lon' -> (ts, payload, ttl)
_Q_REQ = {'calls': 0, 'fail': 0, 'last_err': ''}


def _cfg():
    """优先读环境变量 QWEATHER_HOST/QWEATHER_KEY，回退 config_private.json。
    未配置时不缓存失败结果，便于补配后热恢复。"""
    if 'cfg' in _CACHE:
        return _CACHE['cfg']
    host = (os.environ.get('QWEATHER_HOST') or '').strip()
    key = (os.environ.get('QWEATHER_KEY') or '').strip()
    cfg = {'host': host, 'key': key} if host and key else None
    if cfg is None:
        try:
            with open(_CFG, encoding='utf-8') as f:
                qw = (json.load(f).get('qweather') or {})
            if qw.get('host') and qw.get('key'):
                cfg = {'host': qw['host'], 'key': qw['key']}
        except Exception:
            cfg = None
    if cfg:
        _CACHE['cfg'] = cfg
    return cfg


def _get(path, params, timeout=15):
    cfg = _cfg()
    if not cfg:
        return None
    url = cfg['host'].rstrip('/') + path
    with _Q_LOCK:
        _Q_REQ['calls'] += 1
    try:
        r = requests.get(url, params=params,
                         headers={'X-QW-Api-Key': cfg['key']}, timeout=timeout)
        if r.status_code != 200:
            _note_fail(f'{path} http {r.status_code}')
            return None
        data = r.json()
        if isinstance(data, dict) and data.get('code') not in (None, '200'):
            _note_fail(f"{path} code={data.get('code')}")
        return data
    except Exception as e:
        _note_fail(f'{path} {type(e).__name__}: {str(e)[:80]}')
        return None


def _note_fail(msg: str) -> None:
    with _Q_LOCK:
        _Q_REQ['fail'] += 1
        _Q_REQ['last_err'] = msg
    log.warning('[qweather] %s', msg)


def stats() -> dict:
    """和风调用/失败计数（诊断用，/api/health 可见）。"""
    with _Q_LOCK:
        out = dict(_Q_REQ)
        out['cached'] = len(_Q_CACHE)
        return out


def clear_cache() -> None:
    with _Q_LOCK:
        _Q_CACHE.clear()


def _cache_key(lat: float, lon: float) -> str:
    """约 1km 粒度：同一城市的不同选点应命中同一条实况。"""
    return '%.2f,%.2f' % (round(lat, 2), round(lon, 2))


def _cached(lat, lon):
    key = _cache_key(lat, lon)
    with _Q_LOCK:
        ent = _Q_CACHE.get(key)
        if ent and time.time() - ent[0] < ent[2]:
            return dict(ent[1])
    return None


def _store(lat, lon, payload):
    ttl = QW_CACHE_TTL if payload.get('available') else QW_FAIL_TTL
    key = _cache_key(lat, lon)
    with _Q_LOCK:
        _Q_CACHE[key] = (time.time(), dict(payload), ttl)
        if len(_Q_CACHE) > 64:      # 上限：丢最早写入的
            for k in sorted(_Q_CACHE, key=lambda k: _Q_CACHE[k][0])[
                    :len(_Q_CACHE) - 64]:
                _Q_CACHE.pop(k, None)


def ground_truth(lat: float, lon: float) -> dict:
    """地面实况：实况天气 + 分钟级降水。任一失败返回 available=False。

    v2.37（审查报告 ⑩）：结果（含失败）进 30~90 秒进程内缓存——前端在事件
    窗口内每 10 分钟自动刷一次 /api/nowcast，没有缓存时每次都要重打 2 个
    上游请求，失败还完全静默。
    """
    hit = _cached(lat, lon)
    if hit is not None:
        return hit
    out = _fetch_ground_truth(lat, lon)
    _store(lat, lon, out)
    return out


def _fetch_ground_truth(lat: float, lon: float) -> dict:
    out = {'available': False}
    now = _get('/v7/weather/now', {'location': f'{lon},{lat}'})
    if now and now.get('code') == '200':
        n = now.get('now', {})
        out.update({
            'available': True,
            'obs_time': (n.get('obsTime') or '')[11:16],
            'text': n.get('text'),
            'cloud': int(n['cloud']) if n.get('cloud') else None,
            'vis_km': float(n['vis']) if n.get('vis') else None,
            'humidity': int(n['humidity']) if n.get('humidity') else None,
            'temp': n.get('temp'),
            'dew': n.get('dew'),
            'pressure': n.get('pressure'),
        })
    minute = _get('/v7/minutely/5m', {'location': f'{lon},{lat}'})
    if minute and minute.get('code') == '200':
        out['rain_summary'] = minute.get('summary')
        arr = minute.get('minutely') or []
        vals = [float(m.get('precip') or 0) for m in arr]
        out['rain_next2h_max'] = max(vals) if vals else 0.0
        out['rain_any'] = any(v > 0.0 for v in vals)
    return out

# -*- coding: utf-8 -*-
"""和风天气服务：实况缓存与失败可观测（v2.37 审查报告 ⑩）。

背景：前端在事件窗口内每 10 分钟自动刷 /api/nowcast，每次都会打 2 个和风
HTTP；旧实现没有缓存也没有任何日志，失败静默变成 available=False。
"""
import pytest

from app.services import qweather as qw


@pytest.fixture(autouse=True)
def _isolated(monkeypatch):
    qw.clear_cache()
    monkeypatch.setattr(qw, '_Q_REQ', {'calls': 0, 'fail': 0, 'last_err': ''})
    yield
    qw.clear_cache()


def _ok_get(calls):
    def _get(path, params, timeout=15):
        calls.append(path)
        if path.endswith('/now'):
            return {'code': '200', 'now': {
                'obsTime': '2026-10-08T18:00+08:00', 'text': '多云',
                'cloud': '40', 'vis': '20', 'humidity': '60',
                'temp': '28', 'dew': '22', 'pressure': '1008'}}
        return {'code': '200', 'summary': '未来两小时无降水',
                'minutely': [{'precip': '0.0'}] * 12}
    return _get


def test_ground_truth_is_cached_between_calls(monkeypatch):
    """同一城市（坐标取整后同键）第二次调用不得再打上游。"""
    calls = []
    monkeypatch.setattr(qw, '_get', _ok_get(calls))
    out1 = qw.ground_truth(22.27, 113.58)
    out2 = qw.ground_truth(22.2712, 113.5803)
    assert calls == ['/v7/weather/now', '/v7/minutely/5m']   # 无缓存时是 4 次
    assert out1 == out2
    assert out1['available'] is True
    assert out1['cloud'] == 40
    assert out1['rain_any'] is False


def test_different_cities_are_not_shared(monkeypatch):
    calls = []
    monkeypatch.setattr(qw, '_get', _ok_get(calls))
    qw.ground_truth(22.27, 113.58)
    qw.ground_truth(23.13, 113.26)
    assert len(calls) == 4


def test_failures_are_logged_and_counted(monkeypatch, caplog):
    monkeypatch.setattr(qw, '_cfg', lambda: {
        'host': 'https://api.qweather.com', 'key': 'k'})

    def boom(*a, **kw):
        raise RuntimeError('net down')

    monkeypatch.setattr(qw.requests, 'get', boom)
    with caplog.at_level('WARNING'):
        out = qw.ground_truth(23.13, 113.26)
    assert out['available'] is False
    s = qw.stats()
    assert s['fail'] >= 2
    assert 'net down' in s['last_err']
    assert any('qweather' in r.getMessage() for r in caplog.records)


def test_non_200_status_is_counted_as_failure(monkeypatch, caplog):
    monkeypatch.setattr(qw, '_cfg', lambda: {
        'host': 'https://api.qweather.com', 'key': 'k'})

    class _R:
        status_code = 403
        text = 'forbidden'

        def json(self):
            return {}

    monkeypatch.setattr(qw.requests, 'get', lambda *a, **kw: _R())
    with caplog.at_level('WARNING'):
        qw.ground_truth(23.13, 113.26)
    assert qw.stats()['fail'] >= 1
    assert 'http 403' in qw.stats()['last_err']


def test_failure_is_cached_briefly(monkeypatch):
    """失败结果也要短暂缓存：否则 10 分钟一次的自动刷新会持续打上游。"""
    monkeypatch.setattr(qw, '_cfg', lambda: None)      # 未配置 key
    out1 = qw.ground_truth(22.27, 113.58)
    assert out1['available'] is False
    calls = []
    monkeypatch.setattr(qw, '_fetch_ground_truth',
                        lambda lat, lon: calls.append(1) or {'available': True})
    out2 = qw.ground_truth(22.27, 113.58)
    assert calls == []          # 命中失败缓存，不再重算
    assert out2['available'] is False

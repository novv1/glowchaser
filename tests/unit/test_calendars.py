# -*- coding: utf-8 -*-
"""悬日月日历：方位对准扫描（与 /api/align 窗口表同源）。

覆盖：
  1) events 的 az/time/alt 取自 astro.sun_times/moon_times（容差 0）
  2) 月亮日历可用（珠海首日月出方位 -> 第一条事件就是当天）
  3) 簇式粗筛与逐日严格扫描结果完全一致（含独立参考实现对拍）
  4) next 语义（第一个未发生事件 / 全部已过 -> None）
  5) 非法输入（body/kind/tol/days）
  6) 缓存命中不改变结果且不重复扫描
"""
from datetime import date, datetime, timedelta

import pytest

from app.core import astronomy as astro
from app.services import calendars as cal

LAT, LON = 22.27, 113.58          # 珠海
START = date(2026, 9, 14)


def _strict_reference(lat, lon, target, tol, body, kind, days, start):
    """独立的逐日严格扫描参考（直接调 astro，不复用 calendars 内部）。"""
    tkey = {'sun': ('sunrise', 'sunset'),
            'moon': ('moonrise', 'moonset')}[body][0 if kind == 'rise' else 1]
    akey = tkey + '_az'
    alt = -0.833 if body == 'sun' else 0.125
    out = []
    for i in range(days):
        d = start + timedelta(days=i)
        dt0 = datetime(d.year, d.month, d.day)
        t = (astro.sun_times(dt0, lat, lon, cal.TZ_OFF) if body == 'sun'
             else astro.moon_times(dt0, lat, lon, cal.TZ_OFF))
        t_ev, az = t.get(tkey), t.get(akey)
        if t_ev is None or az is None:
            continue
        daz = abs((az - target + 180.0) % 360.0 - 180.0)
        if daz <= tol:
            out.append({'date': d.isoformat(),
                        'time': t_ev.strftime('%H:%M'), 'az': az,
                        'daz': round(daz, 2), 'alt': alt,
                        'illum': t.get('moon_illum') if body == 'moon'
                        else None})
    return out


def test_tz_offset_same_as_report():
    """本地时区偏移必须与窗口表（report.TZ_OFF）一致，否则差一小时。"""
    from app.services import report as rpt
    assert cal.TZ_OFF == rpt.TZ_OFF


def test_sun_alignment_calendar():
    """旧接口保持兼容（sunset_az 键名、daz <= tol、type=悬日）。"""
    r = cal.sun_alignment_calendar(22.27, 113.58, 270.0, 1.0, 2026)
    assert r['type'] == '悬日'
    assert isinstance(r['events'], list)
    for e in r['events']:
        assert abs(e['daz']) <= 1.0 + 1e-9
        assert 0 <= e['sunset_az'] < 360


def test_events_az_time_identical_to_astro():
    """日历方位/时刻必须与窗口表同源：逐日比对 astro 返回值，容差 0。"""
    for body, kind, target in (('sun', 'rise', 90.0), ('sun', 'set', 270.0),
                              ('moon', 'rise', 90.0),
                              ('moon', 'set', 270.0)):
        tkey = {'sun': ('sunrise', 'sunset'),
                'moon': ('moonrise', 'moonset')}[body][
                    0 if kind == 'rise' else 1]
        r = cal.alignment_calendar(LAT, LON, target, 5.0, body=body,
                                   kind=kind, days=40, start=START)
        assert r['events'], '%s %s 应有事件' % (body, kind)
        for e in r['events']:
            d = datetime.strptime(e['date'], '%Y-%m-%d')
            t = (astro.sun_times(d, LAT, LON, cal.TZ_OFF) if body == 'sun'
                 else astro.moon_times(d, LAT, LON, cal.TZ_OFF))
            assert e['az'] == t[tkey + '_az']       # 容差 0
            assert e['time'] == t[tkey].strftime('%H:%M')
            assert e['alt'] == (-0.833 if body == 'sun' else 0.125)
            assert e['illum'] == (t.get('moon_illum')
                                  if body == 'moon' else None)
            assert e['daz'] <= 5.0 + 1e-9
        assert r['kind_name'] == {'sun': '日出' if kind == 'rise' else '日落',
                                  'moon': '月出' if kind == 'rise'
                                  else '月落'}[body]


def test_moon_calendar_zhuhai_first_event_is_today():
    """珠海：目标方位取当天月出方位 -> 30 天内 events 非空且首条为当天。"""
    mt = astro.moon_times(datetime(2026, 9, 14), LAT, LON, cal.TZ_OFF)
    target = mt['moonrise_az']
    r = cal.alignment_calendar(LAT, LON, target, 1.0, body='moon',
                               kind='rise', days=30, start=START)
    assert r['events'], '当天月出方位应能命中'
    first = r['events'][0]
    assert first['date'] == '2026-09-14'
    assert first['time'] == mt['moonrise'].strftime('%H:%M')
    assert first['az'] == target
    assert first['illum'] == mt['moon_illum']
    assert r['type'] == '悬月' and r['kind_name'] == '月出'
    assert r['scanned_days'] == 30


def test_coarse_scan_equals_strict_daily_scan(monkeypatch):
    """簇式粗筛(_COARSE=True) 与逐日严格扫描(_COARSE=False) 结果一致，
    并与独立参考实现（直接调 astro 逐日）完全一致。"""
    cases = [(LAT, LON, 90.0, 'moon', 'rise'),
             (LAT, LON, 240.0, 'moon', 'set'),
             (LAT, LON, 270.0, 'sun', 'set'),
             (39.9, 116.4, 60.0, 'sun', 'rise'),
             (39.9, 116.4, 120.0, 'moon', 'rise'),
             (-33.9, 151.2, 240.0, 'sun', 'set'),
             (60.2, 25.0, 300.0, 'moon', 'set')]
    for lat, lon, taz, body, kind in cases:
        monkeypatch.setattr(cal, '_CACHE', {})
        monkeypatch.setattr(cal, '_COARSE', True)
        fast = cal.alignment_calendar(lat, lon, taz, 1.0, body=body,
                                      kind=kind, days=30, start=START)
        monkeypatch.setattr(cal, '_CACHE', {})
        monkeypatch.setattr(cal, '_COARSE', False)
        slow = cal.alignment_calendar(lat, lon, taz, 1.0, body=body,
                                      kind=kind, days=30, start=START)
        ref = _strict_reference(lat, lon, taz, 1.0, body, kind, 30, START)
        assert fast['events'] == slow['events'], (lat, taz, body, kind)
        assert fast['events'] == ref, (lat, taz, body, kind)
    monkeypatch.setattr(cal, '_COARSE', True)


class _FixedDT(datetime):
    """固定"当前时刻"，让 next 语义可确定地断言。"""

    _now = datetime(2026, 10, 1, 12, 0)

    @classmethod
    def now(cls, tz=None):
        return cls._now


def test_next_points_to_first_future_event(monkeypatch):
    """next = 第一条尚未发生的事件；其之前的都是已过事件。"""
    monkeypatch.setattr(cal, '_CACHE', {})
    monkeypatch.setattr(cal, 'datetime', _FixedDT)
    r = cal.alignment_calendar(LAT, LON, 270.0, 1.0, body='sun', kind='set',
                               days=400, start=START)
    assert len(r['events']) >= 2
    now = _FixedDT._now
    future = [e for e in r['events']
              if datetime.strptime('%s %s' % (e['date'], e['time']),
                                   '%Y-%m-%d %H:%M') >= now]
    assert r['next'] == future[0]
    # "把它之前的事件都删掉"后，next 指向剩下事件里的第一条
    keep = r['events'][r['events'].index(r['next']):]
    assert keep[0]['date'] >= now.isoformat()[:10]
    assert future[0]['date'] == r['next']['date']


def test_next_none_when_all_events_passed(monkeypatch):
    """全部事件都已过 -> next 为 None（events 本身保留）。"""
    monkeypatch.setattr(cal, '_CACHE', {})
    monkeypatch.setattr(cal, 'datetime', _FixedDT)
    _FixedDT._now = datetime(2099, 1, 1, 0, 0)
    try:
        r = cal.alignment_calendar(LAT, LON, 270.0, 1.0, body='sun',
                                   kind='set', days=400, start=START)
    finally:
        _FixedDT._now = datetime(2026, 10, 1, 12, 0)
    assert r['events']
    assert r['next'] is None


def test_invalid_inputs_raise_value_error():
    """非法 body/kind/tol/days 一律 ValueError（API 层转 400）。"""
    with pytest.raises(ValueError):
        cal.alignment_calendar(LAT, LON, 270.0, body='mars')
    with pytest.raises(ValueError):
        cal.alignment_calendar(LAT, LON, 270.0, kind='dawn')
    with pytest.raises(ValueError):
        cal.alignment_calendar(LAT, LON, 270.0, tol_deg=0)
    with pytest.raises(ValueError):
        cal.alignment_calendar(LAT, LON, 270.0, days=0)
    with pytest.raises(ValueError):
        cal.sun_alignment_calendar(LAT, LON, 270.0, kind='noon')


def test_cache_hit_reuses_result_without_rescan(monkeypatch):
    """缓存命中：结果不变且不再调 sun_times；horizon_fn 参与时不走缓存。"""
    calls = {'n': 0}
    real = astro.sun_times

    def counting(*a, **kw):
        calls['n'] += 1
        return real(*a, **kw)

    monkeypatch.setattr(cal, '_CACHE', {})
    monkeypatch.setattr(cal.astro, 'sun_times', counting)
    kw = dict(body='sun', kind='set', days=30, start=START)
    a = cal.alignment_calendar(LAT, LON, 270.0, 1.0, **kw)
    assert calls['n'] > 0
    n1 = calls['n']
    b = cal.alignment_calendar(LAT, LON, 270.0, 1.0, **kw)
    assert calls['n'] == n1                      # 第二次没有重复扫描
    assert b['events'] == a['events']
    assert b['scanned_days'] == a['scanned_days']
    # horizon_fn is not None -> 不用缓存（遮蔽角不同结果可能不同）
    c = cal.alignment_calendar(LAT, LON, 270.0, 1.0,
                               horizon_fn=lambda az: 0.0, **kw)
    assert calls['n'] > n1
    assert c['events'] == a['events']            # 0° 遮蔽不改变升落

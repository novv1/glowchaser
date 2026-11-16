# -*- coding: utf-8 -*-
"""天文核心：太阳/月亮位置与升落时刻（对照 astral 库逐分钟校准过）。"""
from datetime import datetime

from app.core import astronomy as astro


def _mins(dt):
    return dt.hour * 60 + dt.minute


def test_sun_times_shenzhen_2026_09_10():
    st = astro.sun_times(datetime(2026, 9, 10), 22.27, 113.58, 8.0)
    assert st['sunrise'] is not None and st['sunset'] is not None
    # 与 astral 对照：日出 06:11 / 日落 18:34（容差 2 分钟）
    assert abs(_mins(st['sunrise']) - (6 * 60 + 11)) <= 2
    assert abs(_mins(st['sunset']) - (18 * 60 + 34)) <= 2
    assert 240 < st['sunset_az'] < 300


def test_sun_times_seasonal_order():
    jun = astro.sun_times(datetime(2026, 6, 21), 22.27, 113.58, 8.0)
    dec = astro.sun_times(datetime(2026, 12, 21), 22.27, 113.58, 8.0)
    assert _mins(jun['sunset']) > _mins(dec['sunset'])   # 夏至日落更晚
    assert _mins(jun['sunrise']) < _mins(dec['sunrise'])  # 夏至日出更早


def test_moon_position_plausible():
    jd = astro.jd_from_datetime(datetime(2026, 9, 10, 12, 0))
    ra, dec, dist, lam, beta = astro.moon_position(jd)
    assert 0 <= ra < 360
    assert -30 <= dec <= 30
    assert 356000 < dist < 407000        # 地月距离范围 km
    assert 0 <= lam < 360


def test_altaz_bounds():
    jd = astro.jd_from_datetime(datetime(2026, 9, 10, 12, 0))
    alt, az = astro.altaz(0.0, 0.0, 22.27, 113.58, jd)
    assert -90 <= alt <= 90
    assert 0 <= az < 360

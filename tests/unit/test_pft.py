# -*- coding: utf-8 -*-
"""Planit .pft 解析与窗口扫描（离线；不触发天气请求）。"""
from app.services import pft

SAMPLE = {
    'map': {'lat': 22.27, 'lng': 113.58},
    'tools': {'centerBearing': 270.0, 'horizontalAngleOfView': 3.0,
              'centerElevation': 2.0},
    'ephemeris': {'timeZoneID': 'Asia/Shanghai'},
}


def test_extract_geometry():
    g = pft.extract_geometry(SAMPLE)
    assert g['lat'] == 22.27 and g['lon'] == 113.58
    assert g['bearing'] == 270.0
    assert g['aov'] == 3.0
    assert g['elevation'] == 2.0


def test_extract_geometry_defaults():
    g = pft.extract_geometry({'map': {'lat': 1, 'lng': 2}, 'tools': {}})
    assert g['aov'] == 3.0 and g['elevation'] == 0.0
    assert g['tz'] == 'Asia/Shanghai'


def test_scan_alignment_events_short_window():
    ev = pft.scan_alignment_events(22.27, 113.58, 270.0, 3.0, 2.0,
                                   days=3, body_list=('moon', 'sun'))
    assert isinstance(ev, list)
    for e in ev:
        assert e['body'] in ('moon', 'sun')
        assert 0 <= e['az'] < 360
        assert -90 <= e['alt'] <= 90
        assert e['utc'].tzinfo is not None      # aware UTC（调用方需按 aware 比较）

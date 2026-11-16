# -*- coding: utf-8 -*-
"""几何引擎：霞光弦长/等效云底/视线角（含 glow_elevation_angle NameError 回归）。"""
from app.core import geometry as g


def test_glow_elevation_angle_no_nameerror():
    """回归：该函数曾引用未定义变量 h，一调用就 NameError。"""
    v = g.glow_elevation_angle(3.0, 100.0)
    assert 0 < v < 90
    assert g.max_glow_elevation(3.0, 100.0) == v


def test_elevation_distance_roundtrip():
    checked = 0
    for h in (1.0, 3.0, 6.0):
        for e in (2.0, 3.0, 8.0):
            d = g.distance_at_elevation(h, e)
            if d <= 0:
                continue          # 低于该云高的可见仰角下限，无解
            assert abs(g.glow_elevation_angle(h, d) - e) < 0.05
            checked += 1
    assert checked >= 5


def test_distance_at_elevation_below_min_returns_zero():
    # h=6km 的可见仰角下限约 2.5°，2° 无解 -> 0
    assert g.distance_at_elevation(6.0, 2.0) == 0.0


def test_glow_chord_and_band_increase_with_height():
    assert g.glow_chord_km(6.0) > g.glow_chord_km(3.0) > g.glow_chord_km(1.0)
    assert g.glow_band_km(6.0) > g.glow_band_km(3.0) > g.glow_band_km(1.0)
    # 教程口径：h=2km -> 弦长 ≈ 319km
    assert abs(g.glow_chord_km(2.0) - 319.2) < 2.0


def test_estimate_cloud_base_by_dominant_layer():
    assert g.estimate_cloud_base_km(0, 0, 80) == 6.5     # 高云主导
    assert g.estimate_cloud_base_km(80, 0, 0) == 1.0     # 低云为主
    assert g.estimate_cloud_base_km(0, 80, 0) == 3.5     # 中云主导


def test_equivalent_cloud_base_floor_and_effect():
    assert g.equivalent_cloud_base_km(1.0, 0.0) == 1.0    # 无 AOD 不减高
    assert g.equivalent_cloud_base_km(6.0, 0.05) > 5.0    # 低 AOD 轻微抬升
    assert g.equivalent_cloud_base_km(1.0, 2.0) == 0.15   # 下限保护


def test_brightness_and_burn_quality_bounds():
    assert g.brightness_class(1 / 10) == '亮烧'
    assert g.brightness_class(1 / 1000) == '微光'
    q = g.burn_quality(6.5, 0.1, 0.1)
    assert q['brightness'] in ('亮烧', '闷烧', '微光', '过暗')
    assert 0 < q['transmission'] <= 1

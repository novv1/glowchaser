# -*- coding: utf-8 -*-
"""晨昏线/地影照明判据（离线，无网络）。

回归基准（任务给定的解析值）：
- b(2/5/10/12 km) = 1.44/2.27/3.21/3.51 度
- h=10 km,d=0,α=-2 受光；α=-4 变暗
- h=10 km,d=300,α=-4 受光；h=2 km,d=100,α=-3 变暗；d=300 受光
- 与 sightline.twilight_envelopes_km(h)[3] = -2b 在 d = R·b 处一致
"""
import math

from app.core import illumination as illum
from app.core import sightline as sight

R = 6371.0088


def test_critical_alt_matches_closed_form_and_reference():
    """b(h) = acos(R/(R+h))，且与 1.44/2.27/3.21/3.51 的参考值一致"""
    for h, ref in ((2.0, 1.44), (5.0, 2.27), (10.0, 3.21), (12.0, 3.51)):
        got = illum.critical_alt_deg(h)
        expect = math.degrees(math.acos(R / (R + h)))
        assert abs(got - expect) < 1e-6, h
        assert abs(got - ref) < 0.005, h


def test_critical_alt_grows_with_height():
    prev = 0.0
    for h in (1.0, 2.0, 5.0, 10.0, 20.0, 30.0):
        cur = illum.critical_alt_deg(h)
        assert cur > prev
        prev = cur


def test_is_lit_reference_cases():
    """任务给出的 5 个数值用例"""
    # 天顶（d=0）：10 km 云在 -3.21 之上仍亮，之下变暗
    assert illum.is_lit(10.0, 0.0, -2.0) is True
    assert illum.is_lit(10.0, 0.0, -4.0) is False
    # 朝日 300 km：α_cloud = -1.30 > -3.21
    assert illum.is_lit(10.0, 300.0, -4.0) is True
    # 低云 100 km：α_cloud = -2.10 < -1.44
    assert illum.is_lit(2.0, 100.0, -3.0) is False
    # 低云 300 km：α_cloud = -0.30 > -1.44
    assert illum.is_lit(2.0, 300.0, -3.0) is True
    # 反日 100 km：α_cloud = -3.90 < -1.44
    assert illum.is_lit(2.0, 100.0, -3.0, toward_sun=False) is False


def test_toward_sun_stays_lit_longer_than_away():
    """同一高度的云：朝日方向比反日方向更晚变暗（差值约 2d/R）"""
    h, d, alt = 2.0, 100.0, -2.0
    assert illum.is_lit(h, d, alt, toward_sun=True) is True
    assert illum.is_lit(h, d, alt, toward_sun=False) is False
    toward = illum.dark_sun_alt_deg(h, d, toward_sun=True)
    away = illum.dark_sun_alt_deg(h, d, toward_sun=False)
    assert away - toward > 0
    assert abs((away - toward) - 2 * math.degrees(d / R)) < 1e-9


def test_matches_sightline_critical_angle_minus_2b():
    """一致口径：d = R·b 处本判据的边界即 sightline 的 -2b 临界角"""
    for h in (1.0, 2.0, 5.0, 10.0, 12.0, 20.0):
        b = math.acos(R / (R + h))
        d = R * b                       # 地平线云距（擦地前一点）
        env = sight.twilight_envelopes_km(h * 1000.0)
        assert env is not None
        rail = illum.dark_sun_alt_deg(h, d, toward_sun=True)
        assert rail is not None
        assert abs(rail - env[3]) < 1e-9, h
        # 临界角两侧的受光状态与 -2b 判据一致
        assert illum.is_lit(h, d, env[3] + 0.01) is True
        assert illum.is_lit(h, d, env[3] - 0.01) is False


def test_lit_fraction_boundary_and_saturation():
    """边界处约 0.5；带内单调；带外饱和到 0.0 / 1.0"""
    h, d = 10.0, 0.0
    rail = illum.dark_sun_alt_deg(h, d)
    assert abs(illum.lit_fraction(h, d, rail) - 0.5) < 1e-9
    assert illum.lit_fraction(h, d, rail + 0.5) == 1.0
    assert illum.lit_fraction(h, d, rail - 0.5) == 0.0
    assert illum.lit_fraction(h, d, rail + 5.0) == 1.0
    assert illum.lit_fraction(h, d, rail - 5.0) == 0.0
    vals = [illum.lit_fraction(h, d, rail + t)
            for t in (-0.25, -0.1, 0.0, 0.1, 0.25)]
    assert all(b > a for a, b in zip(vals, vals[1:]))
    assert 0.0 < vals[0] < 0.5 < vals[-1] < 1.0


def test_lit_fraction_width_follows_taper():
    """过渡带总宽度由 taper_deg 决定：±taper/2 处恰好饱和"""
    h, d = 5.0, 120.0
    rail = illum.dark_sun_alt_deg(h, d)
    for taper in (0.2, 0.6, 2.0):
        mid0 = illum.lit_fraction(h, d, rail, taper_deg=taper)
        assert abs(mid0 - 0.5) < 1e-9
        hi = illum.lit_fraction(h, d, rail + taper / 2, taper_deg=taper)
        lo = illum.lit_fraction(h, d, rail - taper / 2, taper_deg=taper)
        assert abs(lo) < 1e-12 and abs(hi - 1.0) < 1e-12
        mid = illum.lit_fraction(h, d, rail + taper / 4, taper_deg=taper)
        assert 0.5 < mid < 1.0
    # 非法 taper 返回 0.0
    assert illum.lit_fraction(h, d, rail - 1.0, taper_deg=0.0) == 0.0
    assert illum.lit_fraction(h, d, rail - 1.0, taper_deg=None) == 0.0


def test_lit_fraction_agrees_with_is_lit_at_saturation():
    """充分远离边界时 lit_fraction 与 is_lit 同号（无互相矛盾）"""
    for h, d in ((2.0, 50.0), (5.0, 200.0), (10.0, 400.0)):
        rail = illum.dark_sun_alt_deg(h, d)
        assert illum.lit_fraction(h, d, rail + 1.0) == 1.0
        assert illum.is_lit(h, d, rail + 1.0) is True
        assert illum.lit_fraction(h, d, rail - 1.0) == 0.0
        assert illum.is_lit(h, d, rail - 1.0) is False
        assert illum.lit_fraction(h, d, rail) == 0.5
        assert illum.is_lit(h, d, rail) is False


def test_dark_sun_alt_consistent_with_is_lit():
    """边界 ±0.01° 处 is_lit 恰好翻转（两方向、多组合）。

    注意方向：受光 ⟺ α > 边界（任务书该条写作 α = dark − 0.01 受光，
    与同书「α + d/R > −b(h) 受光」及各数值用例（h=2,d=100,α=−3 变暗）
    相互矛盾，此处按几何定义断言：边界 + 0.01 受光、边界 − 0.01 变暗。
    """
    combos = ((1.0, 0.0), (2.0, 0.0), (2.0, 100.0), (2.0, 300.0),
              (5.0, 60.0), (10.0, 300.0), (12.0, 500.0), (20.0, 800.0))
    for h, d in combos:
        for toward in (True, False):
            rail = illum.dark_sun_alt_deg(h, d, toward_sun=toward)
            assert rail is not None, (h, d, toward)
            assert illum.is_lit(h, d, rail + 0.01, toward) is True
            assert illum.is_lit(h, d, rail - 0.01, toward) is False
            assert illum.lit_fraction(h, d, rail, toward) == 0.5


def test_dark_sun_alt_sign_and_monotonicity():
    """朝日恒 <= 0 且随高度/距离下降；反日可为正（太阳未落已变暗）"""
    assert illum.dark_sun_alt_deg(10.0, 0.0) < 0
    assert (illum.dark_sun_alt_deg(10.0, 0.0)
            > illum.dark_sun_alt_deg(10.0, 300.0))     # 越远越晚变暗
    assert (illum.dark_sun_alt_deg(2.0, 0.0)
            > illum.dark_sun_alt_deg(10.0, 0.0))       # 越高越晚变暗
    assert illum.dark_sun_alt_deg(2.0, 300.0, toward_sun=False) > 0
    assert abs(illum.dark_sun_alt_deg(2.0, 300.0, toward_sun=False)
               - (math.degrees(300.0 / R)
                  - illum.critical_alt_deg(2.0))) < 1e-9


def test_invalid_inputs_never_raise():
    """非法输入逐个断言返回 None / False / 0.0，且不抛异常"""
    bad_heights = (None, float('nan'), float('inf'), float('-inf'),
                   0.0, -1.0, -500.0, 30.1, 1e6)
    for h in bad_heights:
        assert illum.critical_alt_deg(h) is None, h
        assert illum.is_lit(h, 100.0, -2.0) is False, h
        assert illum.is_lit(h, 100.0, -2.0, False) is False, h
        assert illum.lit_fraction(h, 100.0, -2.0) == 0.0, h
        assert illum.dark_sun_alt_deg(h, 100.0) is None, h
    bad_dists = (None, float('nan'), float('inf'), float('-inf'), -1.0)
    for d in bad_dists:
        assert illum.is_lit(5.0, d, -2.0) is False, d
        assert illum.lit_fraction(5.0, d, -2.0) == 0.0, d
        assert illum.dark_sun_alt_deg(5.0, d) is None, d
    bad_alts = (None, float('nan'), float('inf'), 90.5, -90.5, 180.0)
    for a in bad_alts:
        assert illum.is_lit(5.0, 100.0, a) is False, a
        assert illum.lit_fraction(5.0, 100.0, a) == 0.0, a
    # 合法边界值不受影响
    assert illum.critical_alt_deg(30.0) is not None
    assert illum.is_lit(30.0, 0.0, -3.5) is True
    assert illum.dark_sun_alt_deg(30.0, 0.0) is not None

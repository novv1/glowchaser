# -*- coding: utf-8 -*-
"""晚霞评分的光照（地影）接线：逐层剖面 / 受光画布 / 逐层变暗时刻 / 摘要。

这些用例锁定的是"哪一层的云还算画布"这条链路：
- `_level_profiles` 把 ECMWF 气压层的云量与真实层高摊到光路采样点上；
- `_glow_illumination` 按受光判据折算每一带的受光程度；
- `_band_transition_min` 给出该带代表云变暗的事件相对分钟；
- `_illum_payload` 是给前端的摘要（缺数据时必须返回 None，不能抛）。
"""
import math
from datetime import datetime

from app.core import sightline as sight
from app.services import report as rpt

# 与 illumination.py 一致的地球半径，用于构造"恰好受光"的临界太阳高度角
RE = sight.R_EARTH_KM
N_PTS = len(rpt.PATH_DISTANCES)          # 光路采样点数（含观测点）


def _rec(cover, gh=None, hour=18):
    """构造一条 ECMWF 气压层记录：24 小时序列，事件小时填给定值。"""
    t = [f'2026-09-14T{h:02d}:00' for h in range(24)]
    rec = {'time': t}
    for lv in rpt.GLOW_LEVEL_BANDS:
        cv = (cover or {}).get(lv)
        rec[f'cloudcover_{lv}'] = [
            cv if h == hour else (0 if cv is not None else None)
            for h in range(24)]
        gv = (gh or {}).get(lv)
        rec[f'geopotential_height_{lv}'] = [
            gv if h == hour else (None if gv is None else gv)
            for h in range(24)]
    return rec


def _profiles(cover, gh=None, hour=18, n_pts=N_PTS):
    """n_pts 个光路点用同一份记录（便于按层断言）。"""
    return rpt._level_profiles([_rec(cover, gh, hour) for _ in range(n_pts)],
                               hour, n_pts)


def _alt_series(rate_deg_per_min=-0.25, start=0.0, lo=-90, hi=90):
    """线性下降的太阳高度角序列（事件后为正分钟）。"""
    out = []
    for m in range(-90, 91):
        out.append((m, start + rate_deg_per_min * m))
    return out


# ------------------------------------------------ _level_profiles

def test_level_profiles_uses_geopotential_height():
    prof = _profiles({'500hPa': 40}, {'500hPa': 5600.0})
    assert prof['500hPa']['cover'][0] == 40
    assert prof['500hPa']['h_km'][0] == 5.6


def test_level_profiles_falls_back_to_plevel_table():
    """位势高度缺失或异常（负值/超出对流层量级）时回退压高关系表。"""
    prof = _profiles({'300hPa': 50}, {'300hPa': None})
    assert prof['300hPa']['h_km'][0] == rpt.PLEVEL_HEIGHTS['300hPa']
    prof2 = _profiles({'300hPa': 50}, {'300hPa': -100.0})
    assert prof2['300hPa']['h_km'][0] == rpt.PLEVEL_HEIGHTS['300hPa']
    prof3 = _profiles({'300hPa': 50}, {'300hPa': 99999.0})
    assert prof3['300hPa']['h_km'][0] == rpt.PLEVEL_HEIGHTS['300hPa']


def test_level_profiles_missing_cover_is_none_and_clamped():
    prof = _profiles({}, {})
    assert prof['700hPa']['cover'][0] is None
    prof2 = _profiles({'700hPa': 180.0}, {})
    assert prof2['700hPa']['cover'][0] == 100.0
    prof3 = _profiles({'700hPa': -5.0}, {})
    assert prof3['700hPa']['cover'][0] == 0.0


# ------------------------------------------------ _glow_illumination

def test_illumination_all_lit_at_horizon():
    """太阳还在地平线上（alpha=0）时，各带各距离全部受光。"""
    prof = _profiles({'1000hPa': 30, '850hPa': 30, '700hPa': 60,
                      '500hPa': 60, '300hPa': 80})
    il = rpt._glow_illumination(prof, rpt.PATH_DISTANCES, 0.0)
    for band in ('low', 'mid', 'high'):
        assert all(f == 1.0 for f in il['frac'][band]), band


def test_illumination_low_dark_first_high_lasts_longer():
    """同一距离深暮光下：越低越早黑（受光程度随云高递增）。

    alpha=-4.5°、d=310km：0.8km 低云已进入地影，3km 中云半受光，
    9.2km 高云仍全亮——这就是"低云先黑、高云续烧"。
    """
    prof = _profiles({'1000hPa': 50, '850hPa': 50, '700hPa': 50,
                      '500hPa': 50, '300hPa': 50})
    il = rpt._glow_illumination(prof, rpt.PATH_DISTANCES, -4.5)
    frac = il['frac']
    k = rpt.PATH_DISTANCES.index(310)
    assert frac['low'][k] == 0.0
    assert 0.0 < frac['mid'][k] < 1.0
    assert frac['high'][k] == 1.0
    # 带内单调：越远（朝太阳方向）受光越好
    for band in ('low', 'mid', 'high'):
        assert all(a <= b + 1e-9 for a, b in zip(frac[band], frac[band][1:]))


def test_illumination_far_cloud_lit_while_near_dark():
    """单层定量：2km 云在 50km 处已暗、在 580km 处仍亮（alpha=-3°）。"""
    prof = _profiles({'850hPa': 100})
    il = rpt._glow_illumination(prof, rpt.PATH_DISTANCES, -3.0)
    assert il['h_km']['low'] == rpt.PLEVEL_HEIGHTS['850hPa']
    assert il['frac']['low'][1] == 0.0        # 50 km
    assert il['frac']['low'][-1] == 1.0       # 580 km


def test_illumination_band_height_is_cover_weighted():
    """带内多层按云量加权：全在 700hPa 时，mid 代表层高即 3.0km。"""
    prof = _profiles({'700hPa': 100, '500hPa': 0})
    il = rpt._glow_illumination(prof, rpt.PATH_DISTANCES, -1.0)
    assert abs(il['h_km']['mid'] - rpt.PLEVEL_HEIGHTS['700hPa']) < 1e-6
    assert il['ref_d_km']['mid'] == rpt.GLOW_BAND_REPR_KM['mid']


def test_illumination_empty_band_uses_table_height():
    """整带无云时受光程度回退到代表层高（保证"何时变暗"仍有意义）。"""
    prof = _profiles({})
    il = rpt._glow_illumination(prof, rpt.PATH_DISTANCES, -5.0)
    h = rpt.PLEVEL_HEIGHTS[list(rpt.GLOW_LEVEL_BANDS)[0]]
    expect = rpt.illum.lit_fraction(h, rpt.PATH_DISTANCES[1], -5.0)
    assert abs(il['frac']['low'][1] - round(expect, 3)) < 1e-6


def test_illumination_anti_solar_darker_than_sunward():
    """反日方向同一朵云更早变暗（阳光要绕过地球）：

    alpha=-3.3°、9.2km 高云在 50km 处——朝日侧 rail=-3.53° 仍受光，
    反日侧 rail=-2.63° 已进地影。
    """
    prof = _profiles({'300hPa': 100})
    sun_ward = rpt._glow_illumination(prof, rpt.PATH_DISTANCES, -3.3)
    anti = rpt._glow_illumination(prof, rpt.PATH_DISTANCES, -3.3,
                                  toward_sun=False)
    assert anti['frac']['high'][1] == 0.0
    assert sun_ward['frac']['high'][1] > 0.5
    assert rpt.illum.dark_sun_alt_deg(9.2, 50.0, toward_sun=False) > \
        rpt.illum.dark_sun_alt_deg(9.2, 50.0, toward_sun=True)


# ------------------------------------------------ _alt_at

def test_alt_at_interpolates_and_clamps():
    s = [(0, -1.0), (10, -3.0)]
    assert rpt._alt_at(s, 0) == -1.0
    assert abs(rpt._alt_at(s, 5) - (-2.0)) < 1e-9
    assert rpt._alt_at(s, -50) == -1.0        # 序列外取端点
    assert rpt._alt_at(s, 999) == -3.0
    assert rpt._alt_at([], 3) is None


# ------------------------------------------------ _band_transition_min

def test_band_transition_min_finds_nearest_crossing():
    """alpha = -0.25°/min：2km 云在 d=50km 的临界角约 -1.7°，约 7 分钟后变暗。"""
    s = _alt_series()
    rail = rpt.illum.dark_sun_alt_deg(2.0, 50.0)
    tr = rpt._band_transition_min(s, 2.0, 50.0)
    assert tr is not None
    expect = rail / -0.25
    assert abs(tr - expect) <= 1.5


def test_band_transition_min_none_when_never_dark():
    """整段序列都远高于临界角（太阳很高）时没有翻转。"""
    s = [(m, 30.0) for m in range(-90, 91)]
    assert rpt._band_transition_min(s, 2.0, 50.0) is None


def test_band_transition_min_anti_solar_is_earlier():
    s = _alt_series()
    tw = rpt._band_transition_min(s, 9.2, 160.0, toward_sun=True)
    ta = rpt._band_transition_min(s, 9.2, 160.0, toward_sun=False)
    assert tw is not None and ta is not None
    assert ta < tw


# ------------------------------------------------ _illum_payload

def _model_il(alpha=-2.0):
    prof = _profiles({'1000hPa': 20, '850hPa': 40, '700hPa': 60,
                      '500hPa': 60, '300hPa': 80})
    geo = rpt._glow_illumination(prof, rpt.PATH_DISTANCES, alpha)
    anti = rpt._glow_illumination(prof, rpt.PATH_DISTANCES, alpha,
                                  toward_sun=False)
    s = _alt_series()
    return {
        't_mid': 6.0, 'sun_alt': -2.5, 'geo': geo, 'anti': anti,
        'canvas_raw': 120.0, 'canvas_lit': 96.0,
        'trans': {b: rpt._band_transition_min(s, geo['h_km'][b],
                                             rpt.GLOW_BAND_REPR_KM[b])
                  for b in ('low', 'mid', 'high')},
        'anti_trans': rpt._band_transition_min(s, anti['h_km']['high'],
                                              rpt.GLOW_BAND_REPR_KM['high'],
                                              toward_sun=False),
    }


def test_illum_payload_shape_and_times():
    key_time = datetime(2026, 9, 14, 18, 30)
    p = rpt._illum_payload(_model_il(), key_time)
    assert p['enabled'] is True
    assert p['canvas_lit'] == 96.0 and p['canvas_raw'] == 120.0
    assert set(p['bands']) == {'low', 'mid', 'high'}
    for b in ('low', 'mid', 'high'):
        v = p['bands'][b]
        assert len(v['lit_frac']) == len(rpt.PATH_DISTANCES) - 1
        assert v['dist_km'] == rpt.GLOW_BAND_REPR_KM[b]
        if v['transition_min'] is not None:
            assert v['transition_time'] == (
                key_time + __import__('datetime').timedelta(
                    minutes=v['transition_min'])).strftime('%H:%M')
    assert len(p['anti_high_lit_frac']) == 3


def test_illum_payload_none_without_geometry():
    """无光照数据（GC_ILLUM=0 / 取数失败）时必须返回 None，前端才好退化。"""
    assert rpt._illum_payload(None) is None
    assert rpt._illum_payload({}) is None
    assert rpt._illum_payload({'geo': None, 'anti': None}) is None


# ------------------------------------------------ 与评分链路的耦合

def test_lit_canvas_changes_dim2_when_bands_go_dark():
    """受光折算确实会改变 dim2：全暗时画布归零，评分必须不同。"""
    from app.core import scoring as sc
    dists = rpt.PATH_DISTANCES[1:]
    p_mid = [60.0] * len(dists)
    p_high = [60.0] * len(dists)
    p_low = [10.0] * len(dists)
    raw = [m + h for m, h in zip(p_mid, p_high)]
    lit = [0.0] * len(dists)
    d2_raw, canvas_raw, _, _ = sc.dim2_path_canvas(raw, p_low, dists)
    d2_lit, canvas_lit, _, _ = sc.dim2_path_canvas(lit, p_low, dists)
    assert canvas_raw == 120.0 and canvas_lit == 0.0
    assert d2_lit < d2_raw


def test_illumination_consistent_with_sightline_envelope():
    """与 sightline 既有口径一致：d = R·b 处的临界角就是 -2b。"""
    for h in (2.0, 5.5, 9.2):
        b = rpt.illum.critical_alt_deg(h)
        d = RE * math.radians(b)
        assert abs(rpt.illum.dark_sun_alt_deg(h, d)
                   - sight.twilight_envelopes_km(h * 1000.0)[3]) < 1e-9

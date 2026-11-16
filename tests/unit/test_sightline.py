# -*- coding: utf-8 -*-
"""视线-云层遮挡几何 + 悬日月视线评估（离线，无网络）。

回归基准：windy-plugin-sun-moon-path docs/cloud-obstruction.md
- 视高度角 0.5949585° 时 2000/4000/6000 m 云壳交点 = 106.62/169.04/218.04 km
- 2000/4000/6000 m 地平线/擦地/最远无云距 + 临界角
"""
import math

from app.core import sightline as sight
from app.services import report as rpt

R = 6371.0088


def test_sight_intersection_matches_plugin_regression():
    """插件文档四舍五入值：106.62 / 169.04 / 218.04 km"""
    assert round(sight.sight_intersection_km(2000, 0.5949585), 2) == 106.62
    assert round(sight.sight_intersection_km(4000, 0.5949585), 2) == 169.04
    assert round(sight.sight_intersection_km(6000, 0.5949585), 2) == 218.04


def test_twilight_envelopes_match_plugin_regression():
    low = sight.twilight_envelopes_km(2000)
    mid = sight.twilight_envelopes_km(4000)
    high = sight.twilight_envelopes_km(6000)
    assert [round(x) for x in low[:3]] == [160, 319, 479]
    assert [round(x) for x in mid[:3]] == [226, 451, 677]
    assert [round(x) for x in high[:3]] == [276, 553, 829]
    assert round(low[3], 1) == -2.9
    assert round(mid[3], 1) == -4.1
    assert round(high[3], 1) == -5.0


def test_envelope_closed_form():
    """R·b / 2R·b / 3R·b 与 acos(R/(R+h)) 一致"""
    h = 3.0
    b = math.acos(R / (R + h))
    horizon, tangent, clear, crit = sight.twilight_envelopes_km(3000)
    assert abs(horizon - R * b) < 1e-6
    assert abs(tangent - 2 * R * b) < 1e-6
    assert abs(clear - 3 * R * b) < 1e-6
    assert abs(crit - (-2 * math.degrees(b))) < 1e-9


def test_sight_intersection_rejects_invalid():
    assert sight.sight_intersection_km(0, 5.0) is None
    assert sight.sight_intersection_km(-100, 5.0) is None
    assert sight.sight_intersection_km(50000, 5.0) is None
    assert sight.sight_intersection_km(2000, None) is None
    assert sight.sight_intersection_km(2000, 95.0) is None


def test_sight_intersection_planning_regime_distances():
    """规划区间（|alt| ≤ 1°）：1.5~9.8km 云壳交点落在 100~420km 量级，
    与插件文档低角度实例一致（2000m/0.595° -> 106.62km）。"""
    for h_m in (1500, 3200, 5900, 7600, 9800):
        for alt in (-1.0, -0.5, 0.0, 0.5, 1.0):
            d = sight.sight_intersection_km(h_m, alt)
            assert d is not None and 50 < d < 500, (h_m, alt, d)


def test_higher_cloud_has_farther_intersection():
    """同一视高度角下：云越高，交点越远（单调）"""
    d_low = sight.sight_intersection_km(1500, 0.6)
    d_mid = sight.sight_intersection_km(3200, 0.6)
    d_high = sight.sight_intersection_km(7600, 0.6)
    assert d_low < d_mid < d_high


def test_occlusion_band_below_horizon_extends_to_clear_envelope():
    """几何高度角 <0（折射可见的贴地平线悬日）：远端放宽到顶高最远无云距"""
    near, far = sight.occlusion_band_km(1500, 3500, 0.5, -0.3)
    assert near is not None and far is not None
    assert far > near
    env = sight.twilight_envelopes_km(3500)
    assert abs(far - env[2]) < 1e-9


def test_occlusion_band_above_horizon_uses_top_shell():
    """几何高度角 >=0：远端为顶高球壳交点"""
    near, far = sight.occlusion_band_km(1500, 3500, 5.0, 5.0)
    assert abs(near - sight.sight_intersection_km(1500, 5.0)) < 1e-9
    assert abs(far - sight.sight_intersection_km(3500, 5.0)) < 1e-9


def test_occlusion_band_none_only_for_invalid_input():
    """几何上观察点在球壳内必有交点，None 只来自非法输入"""
    assert sight.occlusion_band_km(0, 200, 5.0, 5.0) == (None, None)
    assert sight.occlusion_band_km(-1, 200, 5.0, 5.0) == (None, None)
    near, far = sight.occlusion_band_km(1500, 3500, 0.5, 0.5)
    assert near is not None and far >= near


def test_band_of_height_planit_bins():
    assert sight.band_of_height(800) == 'low'
    assert sight.band_of_height(1999) == 'low'
    assert sight.band_of_height(2000) == 'mid'
    assert sight.band_of_height(5999) == 'mid'
    assert sight.band_of_height(6000) == 'high'


def test_occlusion_transmission_none_gives_full():
    """无云层数据不得被当作有云"""
    assert sight.occlusion_transmission([]) == 1.0
    assert sight.occlusion_transmission([None, None]) == 1.0
    assert sight.occlusion_transmission([0]) == 1.0


def test_occlusion_transmission_monotonic_in_cloud():
    prev = 1.0
    for cc in (10, 30, 50, 70, 90, 100):
        cur = sight.occlusion_transmission([cc])
        assert cur < prev
        prev = cur
    assert round(sight.occlusion_transmission([100]), 3) == 0.1


def test_moon_upper_limb_adds_semidiameter():
    limb = sight.moon_upper_limb_alt(-0.2, 384400.0)
    assert limb is not None and limb > -0.2
    assert round(limb - (-0.2), 2) == 0.26
    assert sight.moon_upper_limb_alt(-0.2, None) is None


def test_sight_risk_rows_report_obstruction():
    """遮挡严重 -> 风险；云量低 -> 依据"""
    bands = {'low': {'occ': 85, 'gh_m': 1500, 'd_km': 110, 'level': 925},
             'high': {'occ': 10, 'gh_m': 9000, 'd_km': 360, 'level': 300}}
    risks, reasons = rpt._sight_risk_rows(bands)
    assert any('会挡住天体' in x for x in risks)
    assert any('110km' in x for x in risks)
    assert any('云量仅 10%' in x for x in reasons)


def test_weather_feasibility_uses_sight_when_available():
    """有视线结果时云况维度取视线透射率，而非机位三云量"""
    sight_clear = {'visible': True, 'bands': {}, 'trans': 1.0}
    sight_block = {'visible': True, 'bands': {'low': {'occ': 95, 'gh_m': 1200,
                                                      'd_km': 100, 'level': 925}},
                   'trans': 0.145}
    cloud_local = {'low': 0, 'mid': 0, 'high': 0}   # 机位晴空
    a = rpt._weather_feasibility(cloud_local, 0.15, 10, 25, 0, {'std': 5},
                                 sight=sight_clear)
    b = rpt._weather_feasibility(cloud_local, 0.15, 10, 25, 0, {'std': 5},
                                 sight=sight_block)
    assert a['dims']['cloud'] == 100
    assert b['dims']['cloud'] < 20
    assert b['score'] < a['score']
    assert a['cloud_basis'] == '视线交点'
    assert any('挡住天体' in x for x in b['risks'])


def test_weather_feasibility_falls_back_to_camera_cloud():
    """无视线结果（数据缺测）时回退机位云量，并标注口径"""
    cloud = {'low': 70, 'mid': 0, 'high': 0}
    f = rpt._weather_feasibility(cloud, 0.15, 10, 25, 0, {'std': 5})
    assert f['cloud_basis'] == '机位云量'
    assert f['dims']['cloud'] < 30


def test_weather_feasibility_transparency_uses_worst_aod():
    """通透度取本地与光路 AOD 的较差者"""
    cloud = {'low': 0, 'mid': 0, 'high': 0}
    local_only = rpt._weather_feasibility(cloud, 0.1, 5, 30, 0, {'std': 5},
                                          aod_path=0.1)
    path_dirty = rpt._weather_feasibility(cloud, 0.1, 5, 30, 0, {'std': 5},
                                          aod_path=0.8)
    assert path_dirty['dims']['transparency'] < local_only['dims']['transparency']


def _fake_rec(times, cc_by_level, gh_by_level=None, vis=None):
    rec = {'time': times}
    for lv, vals in (cc_by_level or {}).items():
        rec[f'cloudcover_{lv}hPa'] = vals
    for lv, vals in (gh_by_level or {}).items():
        rec[f'geopotential_height_{lv}hPa'] = vals
    if vis is not None:
        rec['visibility'] = vis
    return rec


def test_window_sight_spans_bands_and_cross_checks_models():
    """视线评估：分层判读 + GFS 同几何复核 + 模式离散度"""
    times = [f'2026-09-13T{h:02d}:00' for h in range(24)]
    n = len(times)
    # ECMWF：925hPa(低) 80% 遮挡、300hPa(高) 10% 通透
    ecmwf_cam = _fake_rec(times,
                          {'925': [80] * n, '300': [10] * n,
                           '500': [0] * n, '850': [0] * n,
                           '1000': [0] * n, '700': [0] * n,
                           '400': [0] * n, '250': [0] * n, '200': [0] * n},
                          {'925': [800] * n, '300': [9500] * n,
                           '500': [5900] * n, '850': [1500] * n,
                           '1000': [100] * n, '700': [3100] * n,
                           '400': [7600] * n, '250': [11000] * n,
                           '200': [12500] * n})
    # GFS：同一套几何下低云仅 5%
    gfs_cam = _fake_rec(times, {'925': [5] * n, '300': [5] * n},
                        vis=[20000] * n)
    gfs_path = [_fake_rec(times, {'925': [5] * n, '300': [5] * n},
                          vis=[20000] * n) for _ in range(16)]
    w = {'date': '2026-09-13', 'best_time': '18:30', 'best_alt': 0.4,
         'body': 'sun', 'jd_best': 0}
    s = rpt._window_sight(w, ecmwf_cam, [ecmwf_cam] * 16, [], 50.0,
                          gfs_cam=gfs_cam, gfs_path=gfs_path)
    assert s['visible'] is True
    assert s['bands']['low']['occ'] == 80
    assert s['bands']['high']['occ'] == 10
    assert s['vis_prob'] < 30                 # 低云 80% 严重削弱
    assert s['bands']['mid']['occ'] == 0      # 实测 0% 也作为"通透"记录在案
    assert s['models']['ECMWF']['vis_prob'] == s['vis_prob']
    assert s['models']['GFS']['vis_prob'] > 80
    assert s['model_spread'] >= 40
    assert s['vis_path_km'] == 20.0


def test_window_sight_flags_data_gap_when_levels_missing():
    """气压层云量全缺测：标记 data_gap，不当作无云满分"""
    times = [f'2026-09-13T{h:02d}:00' for h in range(24)]
    n = len(times)
    rec = _fake_rec(times, {}, {'925': [800] * n, '300': [9500] * n})
    w = {'date': '2026-09-13', 'best_time': '18:30', 'best_alt': 0.4,
         'body': 'sun', 'jd_best': 0}
    s = rpt._window_sight(w, rec, [rec] * 16, [], 50.0)
    assert s['data_gap'] is True
    assert s['bands'] == {}


def test_window_sight_moon_upper_limb_gate():
    """月亮上缘在地平线下时不给遮挡评估"""
    times = [f'2026-09-13T{h:02d}:00' for h in range(24)]
    n = len(times)
    rec = _fake_rec(times, {'925': [50] * n}, {'925': [800] * n})
    w = {'date': '2026-09-13', 'best_time': '05:00', 'best_alt': -5.0,
         'body': 'moon', 'jd_best': 2460946.0}
    s = rpt._window_sight(w, rec, [rec] * 16, [], 50.0)
    assert s['visible'] is False
    assert '地平线下' in s['note']


def test_window_sight_sun_limb_allows_partial_disc():
    """太阳中心略低于地平线但上缘可见：仍做遮挡评估"""
    times = [f'2026-09-13T{h:02d}:00' for h in range(24)]
    n = len(times)
    rec = _fake_rec(times, {'925': [50] * n},
                    {'925': [800] * n, '300': [9500] * n})
    w = {'date': '2026-09-13', 'best_time': '18:30', 'best_alt': -0.2,
         'body': 'sun', 'jd_best': 0}
    s = rpt._window_sight(w, rec, [rec] * 16, [], 50.0)
    assert s['visible'] is True
    assert s['limb_alt'] > 0


# ---------------- 机位高度 / 目标高度（v2.19） ----------------

def test_camera_height_shrinks_cloud_distances():
    """机位抬高后同一视高度角下的遮蔽云距收缩（物理正确）"""
    base = sight.sight_intersection_km(2000, 0.595, 0)
    high = sight.sight_intersection_km(2000, 0.595, 1201)
    assert base is not None and high is not None
    assert high < base
    # 与参考插件文档记录的 1201 m 机位数值一致（54.26/133.56/189.35）
    got = [sight.sight_intersection_km(h, 0.595, 1201)
           for h in (2000, 4000, 6000)]
    assert [round(x, 1) for x in got] == [54.5, 133.9, 189.7]


def test_camera_height_zero_matches_plugin_regression():
    """机位 0 时必须仍与插件/巧摄回归值一致（不破坏原口径）"""
    for h, exp in ((2000, 106.62), (4000, 169.04), (6000, 218.04)):
        assert round(sight.sight_intersection_km(h, 0.5949585, 0), 2) == exp


def test_ray_height_at_intersection_equals_cloud_height():
    """自洽校验：交点处射线高度应精确等于云层高度（两种机位高度）"""
    for cam_m, theta in ((0, 0.5949585), (1201, 0.595), (3000, 1.2)):
        for cloud_m in (1500, 3000, 8000):
            d = sight.sight_intersection_km(cloud_m, theta, cam_m)
            if d is None:
                continue
            r = sight.R_EARTH_KM + cam_m / 1000.0
            alt = math.radians(theta)
            # 由弧距反解弦长 t，再算交点半径
            lo, hi = 0.0, 4 * sight.R_EARTH_KM

            def f(t):
                return sight.R_EARTH_KM * math.atan2(
                    t * math.cos(alt), r + t * math.sin(alt)) - d

            for _ in range(200):
                mid = (lo + hi) / 2
                if f(mid) < 0:
                    lo = mid
                else:
                    hi = mid
            t = (lo + hi) / 2
            radius = math.hypot(r + t * math.sin(alt), t * math.cos(alt))
            assert abs((radius - sight.R_EARTH_KM) * 1000 - cloud_m) < 1.0


def test_horizon_dip_grows_with_height():
    assert sight.horizon_dip_deg(0) == 0.0
    assert sight.horizon_dip_deg(None) == 0.0
    assert abs(sight.horizon_dip_deg(100) - 0.321) < 0.005
    assert abs(sight.horizon_dip_deg(1201) - 1.112) < 0.005
    assert abs(sight.horizon_dip_deg(3000) - 1.758) < 0.005
    assert sight.horizon_dip_deg(100) < sight.horizon_dip_deg(3000)


def test_envelopes_shrink_with_camera_height():
    """包络按云层相对机位的高度差计算：机位抬高后三档距离同步收缩"""
    sea = sight.twilight_envelopes_km(2000, 0)
    high = sight.twilight_envelopes_km(2000, 1201)
    assert [round(x) for x in sea[:3]] == [160, 319, 479]
    assert [round(x) for x in high[:3]] == [101, 202, 303]
    assert high[3] > sea[3]        # 临界角绝对值变小（抬高后不易被同高云挡）


def test_envelopes_none_when_cloud_below_camera():
    """云层不高于机位时三包络无意义"""
    assert sight.twilight_envelopes_km(800, 3000) is None
    assert sight.twilight_envelopes_km(3000, 3000) is None


def test_cloud_below_camera_needs_downward_sight():
    """站在云海之上：水平视线够不到云层，俯视才进入（云海悬日）"""
    assert sight.sight_intersection_km(800, 0.0, 3000) is None
    d = sight.sight_intersection_km(800, -2.0, 3000)
    assert d is not None and 50 < d < 120
    far = sight.sight_intersection_km(800, -10.0, 3000)
    assert far is not None and far < d       # 俯角越大交点越近


def test_occlusion_band_uses_camera_height():
    near, far = sight.occlusion_band_km(1500, 3500, 0.5, -0.3,
                                        observer_height_m=1201)
    env = sight.twilight_envelopes_km(3500, 1201)
    assert near is not None and far is not None
    assert abs(far - env[2]) < 1e-9
    # 抬高机位后区间整体更近
    near0, _ = sight.occlusion_band_km(1500, 3500, 0.5, -0.3, 0)
    assert near < near0


def test_target_elevation_flat_and_curvature():
    """目标仰角：平距/高度/曲率下沉"""
    # 10 km 处 100 m 高目标：atan(0.01)=0.573° 减去曲率 0.045° -> 0.528°
    assert abs(sight.target_elevation_deg(0, 100, 10) - 0.528) < 0.005
    # 5 km 处 200 m -> 约 2.27°
    assert abs(sight.target_elevation_deg(0, 200, 5) - 2.268) < 0.01
    # 同高度的远处目标因曲率呈负角：100 km 约 -0.45°
    assert abs(sight.target_elevation_deg(0, 0, 100) + 0.45) < 0.01
    # 机位与目标等高、30 km：仍为负角
    assert sight.target_elevation_deg(1000, 1000, 30) < 0
    # 目标高于机位 -> 正角
    assert sight.target_elevation_deg(1201, 2000, 10) > 0


def test_target_elevation_monotonic():
    """高度越高仰角越大；距离越远仰角越小（同高度）"""
    assert (sight.target_elevation_deg(0, 300, 8)
            > sight.target_elevation_deg(0, 100, 8))
    assert (sight.target_elevation_deg(0, 200, 20)
            < sight.target_elevation_deg(0, 200, 8))


def test_target_elevation_rejects_invalid():
    assert sight.target_elevation_deg(0, 100, 0) is None
    assert sight.target_elevation_deg(0, 100, None) is None
    assert sight.target_elevation_deg(0, None, 10) is None
    assert sight.target_elevation_deg(0, 100, -3) is None

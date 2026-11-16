# -*- coding: utf-8 -*-
"""珠三角卫星客观标签的纯数值部分（离线，无网络、无卫星数据）。

只测与网络/文件无关的数学：亮温反演云顶高度、球面偏移、光路受光画布、
AUC 与分箱。真实拉帧见 tests/backtest/b13_grid.py（需 P-Tree 凭据）。
"""
import os
import sys

import numpy as np
import pytest
from datetime import date, datetime

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
BACKTEST = os.path.join(ROOT, 'tests', 'backtest')
if BACKTEST not in sys.path:
    sys.path.insert(0, BACKTEST)

import auto_labels as al   # noqa: E402
import b13_grid as bg      # noqa: E402

# 一条单调递减的合成廓线（热带）：z km / T °C
PROFILE = (np.array([0.0, 2.0, 4.0, 6.0, 8.0, 10.0, 12.0, 14.0]),
           np.array([28.0, 14.0, 0.0, -14.0, -28.0, -36.0, -54.0, -56.0]))


def _win(bt_value):
    """整窗同温的合成 B13 窗口，覆盖珠三角 + 西向光路。"""
    lat = np.linspace(28.0, 18.0, 201)
    lon = np.linspace(105.0, 122.0, 341)
    bt = np.full((201, 341), bt_value, dtype=np.float32)
    return {'tbb_13': bt, 'lat': lat, 'lon': lon}


def test_profile_from_t2m_anchors_to_observed_surface():
    """T2m 锚定：地面温度校到实况，垂直结构（含对流层顶）保持不变。

    免费档案接口不给气压层，只能这样拿到带季节性的廓线——所以这条要有测试。
    """
    z, t = al.profile_from_t2m(31.0, PROFILE)
    assert t[0] == 31.0                       # 地面锚定到实况
    assert t[-1] == PROFILE[1][-1] + (31.0 - PROFILE[1][0])
    drop = t[0] - t[1]                        # 层间递减量不变
    assert drop == PROFILE[1][0] - PROFILE[1][1]


def test_profile_from_t2m_keeps_heights():
    z, _ = al.profile_from_t2m(10.0, PROFILE)
    assert list(z) == list(PROFILE[0])


# ------------------------------------------------ 亮温 -> 云顶高度

def test_bt_to_height_interpolates_profile():
    """-30°C 落在 8 km(-28) 与 10 km(-36) 之间 -> 8.5 km"""
    assert al.bt_to_height(-30.0, PROFILE) == pytest.approx(8.5, abs=1e-6)


def test_bt_to_height_clamps_and_handles_bad_input():
    """比地面暖 -> 0 km（无云/贴地）；比最冷层更冷 -> 廓线顶；NaN -> None"""
    assert al.bt_to_height(35.0, PROFILE) == 0.0
    assert al.bt_to_height(-70.0, PROFILE) == 14.0
    assert al.bt_to_height(float('nan'), PROFILE) is None


def test_bt_to_height_monotonic():
    """越冷越高（反演必须单调，否则「受光」判据会被打乱）"""
    hs = [al.bt_to_height(t, PROFILE) for t in (10.0, 0.0, -10.0, -20.0, -50.0)]
    assert hs == sorted(hs)


def test_bt_to_height_falls_back_to_std_atmo():
    """拿不到 ERA5 廓线时用标准大气，仍给出有限高度"""
    h = al.bt_to_height(-40.0, None)
    assert h is not None and 5.0 < h < 20.0


# ------------------------------------------------ 光路几何

def test_offset_point_north_is_one_degree_lat():
    """正北 111.19 km ≈ 1° 纬度"""
    lat, lon = al._offset_point(0.0, 0.0, 0.0, 111.19)
    assert lat == pytest.approx(1.0, abs=0.01)
    assert lon == pytest.approx(0.0, abs=0.01)


def test_offset_point_zero_distance_is_identity():
    lat, lon = al._offset_point(22.27, 113.58, 270.0, 0.0)
    assert lat == pytest.approx(22.27, abs=1e-6)
    assert lon == pytest.approx(113.58, abs=1e-6)


def test_offset_point_west_decreases_lon():
    lat, lon = al._offset_point(22.27, 113.58, 270.0, 111.0)
    assert lon < 113.58 and abs(lat - 22.27) < 0.5


# ------------------------------------------------ 光路受光画布

def test_path_canvas_clear_sky_is_neither_canvas_nor_low():
    """整片暖亮温（30°C，地面 28°C）-> 晴空。

    晴空必须**既不算画布也不算低云**——早期版本把晴空默认归进低云，
    于是无云日被记成「低云 100%」，是本模块最容易犯的语义错误。
    """
    pc = al.path_canvas(_win(30.0), 23.13, 113.26, sun_az=290.0, sun_alt=-2.0,
                        profile=PROFILE)
    assert pc['canvas_lit_avg'] == 0.0
    assert pc['low_avg'] == 0.0
    assert all(p['clear'] == 100.0 for p in pc['per_distance'])
    assert pc['score'] <= 25.0        # 空画布闸门生效


def test_clear_margin_boundary():
    """地面 28°C、余量 4 K -> 判晴空线 24°C：23.9°C 算云，24.1°C 算晴空。"""
    cloudy = al.path_canvas(_win(23.9), 23.13, 113.26, 290.0, -2.0, PROFILE)
    clear = al.path_canvas(_win(24.1), 23.13, 113.26, 290.0, -2.0, PROFILE)
    assert cloudy['low_avg'] == 100.0
    assert clear['low_avg'] == 0.0


def test_path_canvas_cold_cloud_is_lit_when_sun_barely_down():
    """整片 -30°C 高云（8.5 km），太阳 -1° 时仍受光 -> 画布拉满。

    总分不是 100：``scoring.dim2_path_canvas`` 对画布 >90% 有惩罚
    （对应「过厚多层云封天」的实证结论），所以满分约 94.6。
    """
    pc = al.path_canvas(_win(-30.0), 23.13, 113.26, sun_az=290.0, sun_alt=-1.0,
                        profile=PROFILE)
    assert pc['canvas_lit_avg'] == 100.0
    assert pc['low_avg'] == 0.0
    assert 90.0 < pc['score'] <= 100.0


def test_path_canvas_cold_cloud_goes_dark_when_sun_deeper():
    """同一片高云，太阳 -12° 时连最远的云也入地影 -> 画布归零"""
    pc = al.path_canvas(_win(-30.0), 23.13, 113.26, sun_az=290.0, sun_alt=-12.0,
                        profile=PROFILE)
    assert pc['canvas_lit_avg'] == 0.0
    assert pc['score'] < 60.0


def test_far_cloud_stays_lit_longer_than_near_cloud():
    """地影判据的核心：太阳越低，先变暗的近处云，远处（朝日）还亮着。

    太阳 -8° 时近处 30 km 的 8.5 km 云已入地影（rail≈-3.23），
    但 630 km 处 rail≈-8.62 仍受光 —— 所以画布是「部分受光」而非 0。
    """
    pc = al.path_canvas(_win(-30.0), 23.13, 113.26, sun_az=290.0, sun_alt=-8.0,
                        profile=PROFILE)
    assert 0.0 < pc['canvas_lit_avg'] < 100.0
    per = {p['dist']: p['canvas_lit'] for p in pc['per_distance']}
    assert per[min(per)] == 0.0        # 近处已暗
    assert per[max(per)] == 100.0      # 最远处仍受光


def test_path_canvas_low_cloud_counts_as_low_not_canvas():
    """+20°C 约 1.1 km，算低云：画布 0、低云 100"""
    pc = al.path_canvas(_win(20.0), 23.13, 113.26, sun_az=290.0, sun_alt=-1.0,
                        profile=PROFILE)
    assert pc['canvas_lit_avg'] == 0.0
    assert pc['low_avg'] > 90.0


def test_path_canvas_reports_per_distance_and_boundary():
    pc = al.path_canvas(_win(-30.0), 23.13, 113.26, sun_az=290.0, sun_alt=-1.0,
                        profile=PROFILE)
    assert len(pc['per_distance']) == len(al.PATH_KM)
    assert pc['boundary_km'] is not None


# ------------------------------------------------ 槽位与 UTC 日界

def test_slot_time_floor_to_10min_and_utc():
    """日落 19:11 +30min -> 本地 19:40 = 同日 11:40 UTC（分钟向下取整）"""
    t, utc_day, hhmm = bg.slot_time(datetime(2024, 6, 1, 19, 11), 8.0, 30)
    assert (t.hour, t.minute) == (19, 40)
    assert utc_day == date(2024, 6, 1)
    assert hhmm == '1140'


def test_slot_time_am_crosses_utc_day_boundary():
    """日出 06:02 CST = **前一日** 22:02 UTC：日期必须跟着换日，
    否则 AM 的远端目录与 window_key 都差一天，表现为永远「无数据」。
    （PM 恰好同日，只拿晚霞冒烟测不出这个 bug。）"""
    t, utc_day, hhmm = bg.slot_time(datetime(2024, 6, 1, 6, 2), 8.0, 0)
    assert (t.hour, t.minute) == (6, 0)
    assert utc_day == date(2024, 5, 31)
    assert hhmm == '2200'


def test_slots_around_pairs_utc_day_with_slot():
    """_slots_around 返回 (UTC 日期, 槽位) 对，供 day_slots 按正确目录取帧"""
    pairs = bg._slots_around(datetime(2024, 6, 1, 6, 12), 8.0, (-10, 10))
    assert pairs == [(date(2024, 5, 31), '2200'), (date(2024, 5, 31), '2220')]


# ------------------------------------------------ 评估指标

def test_auc_perfect_and_tied():
    assert al.auc([(0.9, 1), (0.8, 1), (0.2, 0), (0.1, 0)]) == pytest.approx(1.0)
    assert al.auc([(0.1, 1), (0.2, 1), (0.8, 0), (0.9, 0)]) == pytest.approx(0.0)
    assert al.auc([(0.5, 1), (0.5, 0)]) == pytest.approx(0.5)


def test_auc_needs_both_classes():
    assert al.auc([(0.5, 1), (0.6, 1)]) is None
    assert al.auc([]) is None


def test_calibration_bins():
    pairs = [(90, 1), (95, 1), (10, 0), (15, 0)]
    cal = al.calibration(pairs, bins=(0, 50, 101))
    assert cal[0]['n'] == 2 and cal[0]['rate'] == 0.0
    assert cal[1]['n'] == 2 and cal[1]['rate'] == 1.0

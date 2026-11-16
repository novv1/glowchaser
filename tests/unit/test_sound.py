# -*- coding: utf-8 -*-
"""探空要素纯函数（v2.33）：露点 Magnus 公式与 0°C 层插值。

数据来自同一次气压层请求的增量变量（零新增 HTTP）；判据语义见
``report.dewpoint_c`` / ``report.freezing_level_km`` 的 docstring。
"""
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

from app.services.report import dewpoint_c, freezing_level_km   # noqa: E402


# ---------------- 露点 ----------------

def test_dewpoint_known_values():
    """对照常用值：20°C/80% -> 16.4°C，30°C/50% -> 18.4°C"""
    assert dewpoint_c(20.0, 80.0) == pytest.approx(16.4, abs=0.15)
    assert dewpoint_c(30.0, 50.0) == pytest.approx(18.4, abs=0.2)


def test_dewpoint_saturated_equals_air_temp():
    """RH=100% 时露点=气温（T-Td=0 即该层饱和）"""
    assert dewpoint_c(15.0, 100.0) == pytest.approx(15.0, abs=0.01)


def test_dewpoint_clamps_and_bad_input():
    """RH 缺测/为 0 -> None；RH>100 钳到 100（不发散）"""
    assert dewpoint_c(None, 60.0) is None
    assert dewpoint_c(20.0, None) is None
    assert dewpoint_c(20.0, 0) is None
    assert dewpoint_c(20.0, 150.0) == pytest.approx(20.0, abs=0.01)


def test_dewpoint_monotonic_in_rh():
    """同一气温，湿度越高露点越高（判据方向不能反）"""
    assert dewpoint_c(10.0, 30.0) < dewpoint_c(10.0, 60.0) < dewpoint_c(10.0, 90.0)


# ---------------- 0°C 层 ----------------

def test_freezing_interpolates_crossing():
    """12°C@1.5km 与 -2°C@3.0km 之间穿越 0 -> 2.79 km"""
    assert freezing_level_km(
        [(0.0, 25.0), (1.5, 12.0), (3.0, -2.0), (5.5, -8.0)]
    ) == pytest.approx(2.79, abs=0.01)


def test_freezing_takes_lowest_crossing():
    """有多个穿越取最低的（标准 0°C 层定义）"""
    assert freezing_level_km(
        [(0.0, 5.0), (0.8, -1.0), (1.2, -3.0), (2.0, 3.0)]
    ) == pytest.approx(0.667, abs=0.01)


def test_freezing_all_warm_is_none():
    """整层冰点以上 -> None（前端如实显示「未出现」）"""
    assert freezing_level_km([(0.0, 25.0), (3.0, 6.0)]) is None


def test_freezing_below_ground_is_zero():
    """首层就在冰点以下 -> 0（0°C 层在地面以下）"""
    assert freezing_level_km([(0.0, -3.0), (1.5, -12.0)]) == 0.0


def test_freezing_skips_missing_levels():
    """缺测层跳过：穿越发生在 (0,25)->(3,-2)，25*3/27 = 2.78"""
    assert freezing_level_km(
        [(0.0, 25.0), (1.5, None), (3.0, -2.0)]
    ) == pytest.approx(2.78, abs=0.01)

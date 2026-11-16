# -*- coding: utf-8 -*-
"""城市库：球面距离/方位点/省域网格。"""
from app.core import cities as c


def test_haversine_shenzhen_guangzhou():
    d = c.haversine_km(22.5431, 114.0579, 23.1291, 113.2644)
    assert abs(d - 104.2) < 1.5


def test_destination_point_west():
    lat, lon = c.destination_point(22.27, 113.58, 270.0, 100.0)
    assert lon < 113.58
    assert abs(lat - 22.27) < 0.5


def test_bearing_between_east_west():
    assert abs(c.bearing_between(0, 0, 0, 1) - 90.0) < 0.5
    assert abs(c.bearing_between(0, 0, 1, 0) - 0.0) < 0.5


def test_province_grid_inside_guangdong():
    pts = c.province_grid_points()
    assert len(pts) > 100
    assert all(c.in_guangdong(la, lo) for la, lo in pts)


def test_province_grid_points_step_aware_cache():
    """step 必须真的生效，且缓存按 step 分键。

    原实现用单一 `_GRID_025` 缓存，传 0.5 仍返回 0.25 的点集——于是「把省网格
    抽稀一半省额度」这件事根本做不到，而省网格正是单次报告 80% 的上游额度
    （2026-10-07 审查报告 2.3 节：250 点 = 120+120+10 三个批次）。
    """
    p50 = c.province_grid_points(0.5)
    p25 = c.province_grid_points(0.25)
    assert len(p50) == 64, '0.5° 珠三角省域网格点数'
    assert len(p25) == 250
    assert c.province_grid_points(0.5) == p50, '缓存按 step 分键，不能互相顶掉'
    assert c.province_grid_points(0.25) == p25
    assert all(c.in_guangdong(la, lo) for la, lo in p50)


def test_nearest_city_is_itself():
    # nearest_city 用的是光路标注城市子集（珠海/江门/…），以其中一城自身坐标验证
    nm, d = c.nearest_city(22.27, 113.58)
    assert nm == '珠海'
    assert d < 1

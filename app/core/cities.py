# -*- coding: utf-8 -*-
"""城市库：珠三角优先 + 光路沿线标注城市 + 全国主要城市（推广预留）"""
import os

# 珠三角核心城市（默认预报点）
PRD_CITIES = [
    {'id': 'zhuhai',    'name': '珠海', 'lat': 22.2710, 'lon': 113.5767, 'region': '珠三角',
     'spots': [{'name': '情侣路', 'lat': 22.2830, 'lon': 113.5820},
               {'name': '野狸岛', 'lat': 22.2780, 'lon': 113.5900},
               {'name': '海滨公园', 'lat': 22.2680, 'lon': 113.5790}]},
    {'id': 'shenzhen',  'name': '深圳', 'lat': 22.5431, 'lon': 114.0579, 'region': '珠三角',
     'spots': [{'name': '深圳湾', 'lat': 22.5170, 'lon': 113.9500},
               {'name': '莲花山', 'lat': 22.5590, 'lon': 114.0550}]},
    {'id': 'guangzhou', 'name': '广州', 'lat': 23.1291, 'lon': 113.2644, 'region': '珠三角',
     'spots': [{'name': '猎德大桥', 'lat': 23.1140, 'lon': 113.3220},
               {'name': '二沙岛', 'lat': 23.1100, 'lon': 113.3000}]},
    {'id': 'zhongshan', 'name': '中山', 'lat': 22.5150, 'lon': 113.3926, 'region': '珠三角'},
    {'id': 'foshan',    'name': '佛山', 'lat': 23.0218, 'lon': 113.1219, 'region': '珠三角'},
    {'id': 'dongguan',  'name': '东莞', 'lat': 23.0207, 'lon': 113.7518, 'region': '珠三角'},
    {'id': 'huizhou',   'name': '惠州', 'lat': 23.1115, 'lon': 114.4160, 'region': '珠三角'},
    {'id': 'jiangmen',  'name': '江门', 'lat': 22.5789, 'lon': 113.0815, 'region': '珠三角'},
    {'id': 'zhaoqing',  'name': '肇庆', 'lat': 23.0472, 'lon': 112.4652, 'region': '珠三角'},
    {'id': 'macau_w',   'name': '拱北', 'lat': 22.2200, 'lon': 113.5400, 'region': '珠三角'},
]

# 光路沿线城市标注（用于大气截面图横轴，覆盖西/西北/西南/东向光路）
PATH_LABEL_CITIES = [
    {'name': '珠海',   'lat': 22.27, 'lon': 113.58},
    {'name': '江门',   'lat': 22.58, 'lon': 113.08},
    {'name': '佛山',   'lat': 23.02, 'lon': 113.12},
    {'name': '肇庆',   'lat': 23.05, 'lon': 112.47},
    {'name': '云浮',   'lat': 22.92, 'lon': 112.04},
    {'name': '阳江',   'lat': 21.86, 'lon': 111.98},
    {'name': '梧州',   'lat': 23.48, 'lon': 111.28},
    {'name': '玉林',   'lat': 22.65, 'lon': 110.18},
    {'name': '贵港',   'lat': 23.11, 'lon': 109.60},
    {'name': '柳州',   'lat': 24.33, 'lon': 109.42},
    {'name': '南宁',   'lat': 22.82, 'lon': 108.37},
    {'name': '河池',   'lat': 24.70, 'lon': 108.09},
    {'name': '贵阳',   'lat': 26.65, 'lon': 106.63},
    {'name': '广州',   'lat': 23.13, 'lon': 113.26},
    {'name': '东莞',   'lat': 23.02, 'lon': 113.75},
    {'name': '惠州',   'lat': 23.11, 'lon': 114.42},
    {'name': '汕尾',   'lat': 22.79, 'lon': 115.37},
    {'name': '汕头',   'lat': 23.35, 'lon': 116.68},
    {'name': '漳州',   'lat': 24.51, 'lon': 117.65},
    {'name': '厦门',   'lat': 24.48, 'lon': 118.09},
    {'name': '清远',   'lat': 23.68, 'lon': 113.06},
    {'name': '韶关',   'lat': 24.81, 'lon': 113.59},
    {'name': '郴州',   'lat': 25.77, 'lon': 113.01},
    {'name': '永州',   'lat': 26.42, 'lon': 111.61},
    {'name': '邵阳',   'lat': 27.24, 'lon': 111.47},
    {'name': '怀化',   'lat': 27.55, 'lon': 110.00},
    {'name': '铜仁',   'lat': 27.73, 'lon': 109.19},
    {'name': '遵义',   'lat': 27.73, 'lon': 106.93},
    {'name': '重庆',   'lat': 29.56, 'lon': 106.55},
    {'name': '赣州',   'lat': 25.83, 'lon': 114.93},
    {'name': '韶关',   'lat': 24.81, 'lon': 113.59},
    {'name': '长沙',   'lat': 28.23, 'lon': 112.94},
    {'name': '桂林',   'lat': 25.28, 'lon': 110.29},
    {'name': '茂名',   'lat': 21.66, 'lon': 110.93},
    {'name': '湛江',   'lat': 21.27, 'lon': 110.36},
    {'name': '海口',   'lat': 20.04, 'lon': 110.32},
    {'name': '北海',   'lat': 21.48, 'lon': 109.12},
    {'name': '钦州',   'lat': 21.95, 'lon': 108.65},
    {'name': '百色',   'lat': 23.90, 'lon': 106.62},
    {'name': '昆明',   'lat': 25.04, 'lon': 102.71},
]

# PM2.5 分布图城市（珠三角周边采样点）
AQ_CITIES = [
    {'name': '珠海',   'lat': 22.27, 'lon': 113.58},
    {'name': '深圳',   'lat': 22.54, 'lon': 114.06},
    {'name': '广州',   'lat': 23.13, 'lon': 113.26},
    {'name': '中山',   'lat': 22.52, 'lon': 113.39},
    {'name': '佛山',   'lat': 23.02, 'lon': 113.12},
    {'name': '东莞',   'lat': 23.02, 'lon': 113.75},
    {'name': '惠州',   'lat': 23.11, 'lon': 114.42},
    {'name': '江门',   'lat': 22.58, 'lon': 113.08},
    {'name': '肇庆',   'lat': 23.05, 'lon': 112.47},
    {'name': '香港',   'lat': 22.32, 'lon': 114.17},
    {'name': '澳门',   'lat': 22.19, 'lon': 113.54},
    {'name': '云浮',   'lat': 22.92, 'lon': 112.04},
    {'name': '阳江',   'lat': 21.86, 'lon': 111.98},
    {'name': '清远',   'lat': 23.68, 'lon': 113.06},
    {'name': '韶关',   'lat': 24.81, 'lon': 113.59},
    {'name': '汕尾',   'lat': 22.79, 'lon': 115.37},
]

# 火烧云地图网格（广东 0.5° 网格，110-117E / 21-25N，135 点，控制 API 权重）
def grid_points():
    pts = []
    lat = 25.0
    while lat >= 21.0:
        lon = 110.0
        while lon <= 117.0:
            pts.append((round(lat, 2), round(lon, 2)))
            lon = round(lon + 0.5, 2)
        lat = round(lat - 0.5, 2)
    return pts


def destination_point(lat: float, lon: float, bearing_deg: float,
                      distance_km: float):
    """给定起点、方位角、球面距离（km），返回终点经纬度"""
    import math
    r = 6371.0
    br = math.radians(bearing_deg)
    la1 = math.radians(lat)
    lo1 = math.radians(lon)
    dr = distance_km / r
    la2 = math.asin(math.sin(la1) * math.cos(dr)
                    + math.cos(la1) * math.sin(dr) * math.cos(br))
    lo2 = lo1 + math.atan2(math.sin(br) * math.sin(dr) * math.cos(la1),
                           math.cos(dr) - math.sin(la1) * math.sin(la2))
    return round(math.degrees(la2), 4), round((math.degrees(lo2) + 540) % 360 - 180, 4)


def bearing_between(lat1, lon1, lat2, lon2):
    """两点间初始方位角（度，北=0 顺时针）
    标准公式：θ = atan2( sin Δλ·cos φ2 ,  cos φ1·sin φ2 − sin φ1·cos φ2·cos Δλ )"""
    import math
    la1, la2 = math.radians(lat1), math.radians(lat2)
    dlo = math.radians(lon2 - lon1)
    y = math.sin(dlo) * math.cos(la2)
    x = (math.cos(la1) * math.sin(la2) - math.sin(la1) * math.cos(la2)
         * math.cos(dlo))
    return (math.degrees(math.atan2(y, x)) + 360) % 360


def haversine_km(lat1, lon1, lat2, lon2):
    import math
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def nearest_city(lat: float, lon: float):
    best, bd = None, 1e9
    for c in PATH_LABEL_CITIES:
        d = haversine_km(lat, lon, c['lat'], c['lon'])
        if d < bd:
            best, bd = c['name'], d
    return best, round(bd)


# =================================================================
# 广东省轮廓网格（点在省内才保留；步长由调用方给，默认 0.25°）
# =================================================================

_GD_GEO = None
_GD_BBOXES = None
_GRID_CACHE = {}


def _gd_geo():
    """加载广东省 GeoJSON（懒加载）"""
    global _GD_GEO, _GD_BBOXES
    if _GD_GEO is None:
        import json as _json
        p = os.path.normpath(os.path.join(os.path.dirname(__file__), '..',
                                          'static', 'data', 'guangdong.json'))
        _GD_GEO = _json.load(open(p, encoding='utf-8'))
        _GD_BBOXES = []
        for feat in _GD_GEO['features']:
            g = feat['geometry']
            polys = (g['coordinates'] if g['type'] == 'MultiPolygon'
                     else [g['coordinates']])
            for poly in polys:
                ring = poly[0]
                xs = [pt[0] for pt in ring]
                ys = [pt[1] for pt in ring]
                _GD_BBOXES.append((min(xs), min(ys), max(xs), max(ys), ring))
    return _GD_GEO


def _pip(lon, lat, ring):
    """射线法点在多边形内判定"""
    inside = False
    n = len(ring)
    for i in range(n):
        x1, y1 = ring[i][0], ring[i][1]
        x2, y2 = ring[(i + 1) % n][0], ring[(i + 1) % n][1]
        if (y1 > lat) != (y2 > lat):
            xin = (x2 - x1) * (lat - y1) / (y2 - y1) + x1
            if lon < xin:
                inside = not inside
    return inside


def in_guangdong(lat: float, lon: float) -> bool:
    _gd_geo()
    for x0, y0, x1, y1, ring in _GD_BBOXES:
        if x0 <= lon <= x1 and y0 <= lat <= y1:
            if _pip(lon, lat, ring):
                return True
    return False


def province_grid_points(step: float = 0.25):
    """广东省轮廓内的规则网格点（懒加载，按 step 分键缓存）。

    原来只有一个 `_GRID_025` 缓存，传 0.5 也照样返回 0.25 的点集——「抽稀省额度」
    根本做不到。step 直接决定上游取数点数：省网格是单次报告最大的额度开销
    （0.25° = 250 点 = 120+120+10 三个批次，占 690 加权调用的 80%；2026-10-07
    审查报告 2.3 节）。报告取数用 0.5°（64 点）即可，地图上画的是离散散点。
    """
    key = round(float(step), 4)
    pts = _GRID_CACHE.get(key)
    if pts is None:
        _gd_geo()
        pts = []
        lat = 25.25
        while lat >= 20.75:
            lon = 110.0
            while lon <= 117.25:
                if in_guangdong(lat, lon):
                    pts.append((round(lat, 2), round(lon, 2)))
                lon = round(lon + step, 4)
            lat = round(lat - step, 4)
        _GRID_CACHE[key] = pts
    return pts

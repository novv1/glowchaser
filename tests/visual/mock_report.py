# -*- coding: utf-8 -*-
"""视觉回归用的合成报告 bundle（不依赖网络/上游 API）。

字段与 app/services/report.py 的真实 bundle 对齐，用于在 Playwright 中
直接调用 renderSky / renderAlign / renderCelestialPreview 做界面与模拟校验。
"""
import math
import os
import re

SUNRISE = 6 * 60 + 10      # 06:10
SUNSET = 18 * 60 + 10      # 18:10（12h 日长，便于 mock 轨迹自洽）
DAY_LEN = SUNSET - SUNRISE
ALT_AMP = 42.0


def _ui_version():
    """读前端 app.js 的 UI_VERSION 作为 mock 的版本号。

    页面有「前后端版本不一致就提示重启」的自检（checkUiVersion），mock 冒充的是
    服务端，版本号写死会在每次改前端版本时制造一次假告警。这里直接对齐前端。
    """
    try:
        root = os.path.dirname(os.path.dirname(
            os.path.dirname(os.path.abspath(__file__))))
        with open(os.path.join(root, 'app', 'static', 'app.js'),
                  encoding='utf-8') as f:
            m = re.search(r"const UI_VERSION = '([^']+)'", f.read())
        return m.group(1) if m else '0'
    except OSError:
        return '0'


UI_VERSION = _ui_version()


def _hm(minutes):
    minutes = int(round(minutes)) % 1440
    return '%02d:%02d' % (minutes // 60, minutes % 60)


def _sun_alt(m):
    return ALT_AMP * math.sin(math.pi * (m - SUNRISE) / 720.0)


def _sun_az(m):
    return 90.0 + 180.0 * (m - SUNRISE) / DAY_LEN


def _tracks():
    times, sun_alt, sun_az, moon_alt, moon_az = [], [], [], [], []
    for i in range(49):
        m = i * 30
        times.append(_hm(m))
        sun_alt.append(round(_sun_alt(m), 1))
        sun_az.append(round(_sun_az(m) % 360, 1))
        # 月亮：简单相位偏移，低角度轨迹
        mm = (m + 300) % 1440
        moon_alt.append(round(30 * math.sin(math.pi * (mm - 360) / 720.0), 1))
        moon_az.append(round((90 + 180 * ((m + 300) % 1440) / DAY_LEN) % 360, 1))
    return {'time': times, 'sun_alt': sun_alt, 'sun_az': sun_az,
            'moon_alt': moon_alt, 'moon_az': moon_az}


# ---- A单列 mock 满血：与 report.py 真实 bundle 同口径的合成数据 ----
_DIM_KEYS = ['dim1', 'dim2', 'dim3', 'dim4', 'dim5', 'dim6', 'dim7', 'dim8']
_DIM_NAMES = {'dim1': '中高层云分布', 'dim2': '光路城市云况', 'dim3': '近地降水',
              'dim4': '大气能见度/颗粒物', 'dim5': '云运动趋势', 'dim6': '气压变化',
              'dim7': 'CAMS 霾/气溶胶', 'dim8': '高层湿层卷云潜力'}
_DIM_WEIGHTS = {'dim1': 23, 'dim2': 30, 'dim3': 10, 'dim4': 11,
                'dim5': 3, 'dim6': 1, 'dim7': 12, 'dim8': 10}
_MOCK_PM_CITIES = [
    ('珠海', 22.27, 113.58, 20), ('深圳', 22.54, 114.06, 14),
    ('广州', 23.13, 113.26, 24), ('中山', 22.52, 113.39, 18),
    ('佛山', 23.02, 113.12, 22), ('东莞', 23.02, 113.75, 19),
    ('惠州', 23.11, 114.42, 12), ('江门', 22.58, 113.08, 16),
    ('肇庆', 23.05, 112.47, 10), ('香港', 22.32, 114.17, 15),
    ('澳门', 22.19, 113.54, 21), ('云浮', 22.92, 112.04, 9),
    ('阳江', 21.86, 111.98, 8), ('清远', 23.68, 113.06, 11),
    ('韶关', 24.81, 113.59, 7), ('汕尾', 22.79, 115.37, 10),
]
_MOCK_RANK_CITIES = [
    ('珠海', 22.27, 113.58), ('中山', 22.52, 113.39),
    ('江门', 22.58, 113.08), ('广州', 23.13, 113.26),
    ('深圳', 22.54, 114.06), ('佛山', 23.02, 113.12),
    ('东莞', 23.02, 113.75), ('惠州', 23.11, 114.42),
]


def _mock_dims(low, mid, high, canvas, aod):
    """8 维分：优先调真实 scoring.py，与后端同口径；调不到则用固定值。"""
    try:
        from app.core import scoring as _sc
        _d2, _c, _l, _b = _sc.dim2_path_canvas(
            [mid + high, mid + high + 4, mid + high - 6],
            [low, low + 3, low - 2], [80, 200, 350])
        return {
            'dim1': _sc.dim1_midhigh_canvas(mid, high, low),
            'dim2': _d2,
            'dim3': _sc.dim3_precip(0.0, 0.0, 5),
            'dim4': _sc.dim4_visibility_pm25(18000, 22),
            'dim5': _sc.dim5_cloud_trend(mid + high, mid + high - 8),
            'dim6': _sc.dim6_pressure(1008, 1006.3),
            'dim7': _sc.dim7_aod(aod, aod + 0.05),
            'dim8': _sc.dim8_high_moisture(65, 50, high),
        }
    except Exception:
        return {'dim1': 72.0, 'dim2': 68.0, 'dim3': 95.0, 'dim4': 80.0,
                'dim5': 55.0, 'dim6': 80.0, 'dim7': 75.0, 'dim8': 70.0}


def _mock_models(probability, low, mid, high, canvas, aod, dims):
    out = {}
    for name, dp, dc in (('ECMWF', 0, 0), ('GFS', -6, 5),
                         ('ICON', -4, -3), ('CMA', -8, 7)):
        p = max(3, min(98, probability + dp))
        out[name] = {
            'model_id': name.lower(),
            'cloud': {'total': max(0, min(100, 72 + dc)),
                      'low': max(0, low + dc), 'mid': max(0, mid - dc),
                      'high': max(0, high + dc)},
            'weather': {'temp': 27, 'humidity': 68, 'pressure': 1008,
                        'visibility_km': 18, 'wind': 11, 'precip_prob': 5},
            'aod_local': round(aod, 2), 'aod_path': round(aod + 0.05, 2),
            'pm25': 22, 'dims': dict(dims),
            'score': 62 + dp, 'probability': p,
            'canvas_avg': canvas + dc, 'low_avg': low + dc,
            'boundary_km': 210, 'eq_base_km': 3.6, 'base_est_km': 4.2,
            't_start_min': 4.8, 't_end_min': 9.5, 'geo_ok': True,
            'quality': {'color': '橘色到橘红', 'brightness': '亮烧',
                        'transmission': 0.18},
            'illum': True,
        }
    return out


def _mock_map_points(probability, low, mid, high, canvas, aod):
    pts = []
    olat, olon = 22.27, 113.58
    lat = 21.0
    while lat <= 25.0:
        lon = 110.0
        while lon <= 117.0:
            dlat = (lat - olat) * 111.0
            dlon = (lon - olon) * 101.0
            dist = math.sqrt(dlat * dlat + dlon * dlon)
            decay = max(0.25, 1.0 - dist / 700.0)
            ph = ((round(lat * 10) * 7 + round(lon * 10) * 13) % 11) - 5
            p = round(max(3, min(95, probability * decay + ph)), 1)
            pts.append({'lat': round(lat, 2), 'lon': round(lon, 2),
                        'score': p, 'prob': p,
                        'quality': round(max(5, min(100, 60 * decay + ph)), 1),
                        'rev': round(max(0, min(100, 40 * decay)), 1),
                        'dist': round(dist),
                        'brg': round(math.degrees(math.atan2(dlon, dlat)) % 360, 1),
                        'canvas': canvas, 'low': low, 'mid': mid,
                        'high': high, 'aod': round(aod, 2)})
            lon = round(lon + 0.5, 2)
        lat = round(lat + 0.5, 2)
    return pts


def _mock_ranking(probability):
    rows = []
    for i, (nm, la, lo) in enumerate(_MOCK_RANK_CITIES):
        p = max(5, probability - i * 3)
        rows.append({'name': nm, 'lat': la, 'lon': lo, 'prob': p,
                     'quality': max(5, 62 - i * 2), 'dist': 20 + i * 45,
                     'brg': round((270 + i * 12) % 360, 1)})
    return rows


def _mock_pm25():
    return [{'name': n, 'lat': la, 'lon': lo, 'pm25': pm,
             'aqi': pm * 2 + 8}
            for n, la, lo, pm in _MOCK_PM_CITIES]


def _mock_table_48h(low, mid, high):
    rows = []
    for i in range(17):
        mins = 17 * 60 + i * 180
        day = 10 + mins // 1440
        mm = mins % 1440
        rows.append({
            'time': '09-%02d %02d:%02d' % (day, mm // 60, mm % 60),
            'total': max(8, min(100, 72 - i)),
            'high': max(5, 45 - i // 2),
            'mid': max(3, 35 - i // 3),
            'low': max(5, 18 - i // 4),
        })
    return rows


def _mock_weather_3h(table_48h, aod):
    rows = []
    for i, t in enumerate(table_48h[:13]):
        rows.append({
            'time': t['time'],
            'temp': round(29.0 - i * 0.4, 1),
            'precip': 0.0,
            'wind': 11 - (i % 4),
            'visibility': round(18.0 - i * 0.5, 1),
            'aod': round(aod + 0.12 - i * 0.008, 2),
        })
    return rows


def build(kind='sunset', probability=58, aod=0.22, low=18, mid=35, high=45,
          canvas=52, low_avg=20, waxing=True):
    sunset_txt = _hm(SUNSET)
    sunrise_txt = _hm(SUNRISE)
    key_time = sunset_txt if kind == 'sunset' else sunrise_txt
    key_az = 270.0 if kind == 'sunset' else 90.0
    horizon = [0.4, 0.6, 0.8, 1.0, 1.2, 1.6, 2.4, 3.6, 5.2, 3.8, 2.6, 1.8,
               1.2, 0.9, 0.7, 0.5]
    # 两条悬日窗口（日出/日落）+ 一条悬月
    alignment = [
        {'type': '悬日', 'period': 'sunset', 'period_name': '日落',
         'best_time': '17:52', 'date': '2026-09-10', 'best_alt': 2.4,
         'best_az': 269.7, 'best_daz': 0.3, 'vis_prob': 72,
         'alt_quality': '低角度', 'reason': '云量少，可见性好',
         'cloud_at': {'low': low, 'mid': mid, 'high': high}},
        {'type': '悬日', 'period': 'sunrise', 'period_name': '日出',
         'best_time': '06:26', 'date': '2026-09-10', 'best_alt': 2.1,
         'best_az': 90.2, 'best_daz': 0.2, 'vis_prob': 64,
         'alt_quality': '低角度', 'reason': '云量中等，薄云可透',
         'cloud_at': {'low': low, 'mid': mid, 'high': high}},
        {'type': '悬月', 'period': 'moonset', 'period_name': '月落',
         'best_time': '05:12', 'date': '2026-09-10', 'best_alt': 1.6,
         'best_az': 270.4, 'best_daz': 0.4, 'vis_prob': 55, 'illum': 86.0,
         'alt_quality': '低角度', 'reason': '云量中等，薄云可透',
         'cloud_at': {'low': low, 'mid': mid, 'high': high}},
    ]
    # ---- A单列满血：与后端同口径的合成数（spec m02642） ----
    _dims = _mock_dims(low, mid, high, canvas, aod)
    _models = _mock_models(probability, low, mid, high, canvas, aod,
                           _dims)
    _dims_detail = [{'key': k, 'name': _DIM_NAMES[k],
                     'weight': _DIM_WEIGHTS[k], 'score': _dims[k]}
                    for k in _DIM_KEYS]
    _pts = _mock_map_points(probability, low, mid, high, canvas, aod)
    _pm = _mock_pm25()
    _t48 = _mock_table_48h(low, mid, high)
    _w3h = _mock_weather_3h(_t48, aod)
    _rank = _mock_ranking(probability)
    return {
        'meta': {'location': {'lat': 22.27, 'lon': 113.58},
                 'date': '2026-09-10', 'type': kind,
                 'generated_at': '2026-09-09 20:00', 'line_speed': 20.5},
        'astronomy': {
            'sunrise': sunrise_txt, 'sunrise_az': 90.0,
            'sunset': sunset_txt, 'sunset_az': 270.0,
            'moonrise': '16:40', 'moonrise_az': 105.0,
            'moonset': '05:12', 'moonset_az': 270.4,
            'moon_phase': '盈凸月', 'moon_illum': 86.0,
            'moon_phase_angle': 120.0, 'moon_waxing': waxing,
            'blue_evening': ['18:14', '18:44'],
            'blue_morning': ['05:36', '06:06'],
            'key_time': key_time, 'key_az': key_az},
        'probability': probability, 'verdict': '中烧',
        'credibility': '高 · 多模式高度一致',
        'best_window': {'start': '17:50', 'end': '18:20'},
        'main': {
            'cloud': {'total': 72, 'low': low, 'mid': mid, 'high': high},
            'weather': {'temp': 27, 'humidity': 68, 'pressure': 1008,
                        'visibility_km': 18, 'wind': 11, 'precip_prob': 5},
            'aod_local': aod, 'aod_path': aod + 0.05, 'pm25': 22,
            'dims': dict(_dims), 'score': 62, 'probability': probability,
            'canvas_avg': canvas, 'low_avg': low_avg,
            'boundary_km': 210, 'eq_base_km': 3.6, 'base_est_km': 4.2,
            't_start_min': 4.8, 't_end_min': 9.5, 'geo_ok': True,
            'quality': {'color': '橘色到橘红', 'brightness': '亮烧',
                        'transmission': 0.18},
            # v2.33 探空载荷：T 在 3.1~5.6km 间穿越 0°C（内插 0 + 6*2.5/12）
            'sound': {'levels': [
                {'p': '地面', 'alt_km': 0.0, 't_c': 27.0, 'td_c': 20.9, 'rh': 68},
                {'p': '1000hPa', 'alt_km': 0.2, 't_c': 25.0, 'td_c': 20.0, 'rh': 74},
                {'p': '850hPa', 'alt_km': 1.5, 't_c': 18.0, 'td_c': 14.0, 'rh': 80},
                {'p': '700hPa', 'alt_km': 3.1, 't_c': 6.0, 'td_c': 1.0, 'rh': 82},
                {'p': '500hPa', 'alt_km': 5.6, 't_c': -6.0, 'td_c': -12.0, 'rh': 65},
                {'p': '300hPa', 'alt_km': 9.3, 't_c': -30.0, 'td_c': -44.0, 'rh': 30},
            ], 'freezing_km': 4.25}},
        'main_model': 'ECMWF', 'models': _models,
        'dims_detail': _dims_detail,
        'risks': ['低云(0.8km)交点 114km 处云量 82%，会挡住天体'],
        'alignment': alignment,
        'alignment_meta': {'target_az': key_az, 'tol': 2.0, 'alt_max': 10.0},
        'tracks': _tracks(),
        'site_info': {'lat': 22.27, 'lon': 113.58, 'elevation': 12.0,
                      'horizon_mask': horizon, 'bortle': 8,
                      'bortle_src': 'heuristic', 'ok': True},
        'hourly_clouds': {
            'time': [_hm(SUNRISE + i * 60) for i in range(24)],
            'low': [low] * 24, 'mid': [mid] * 24, 'high': [high] * 24},
        'nowcast': None, 'ranking': _rank,
        # 霞光周概览：服务端 v2.38 起给 14 天（14 天内加长时间不额外计费）
        'week': [{'date': '2026-09-%02d' % (10 + i), 'prob': 58 - i,
                  'weekday': '周' + '一二三四五六日'[i % 7],
                  'verdict': '小烧', 'canvas': 45}
                 for i in range(14)],
        'reason': '光路中高云量充足，西侧边界清晰，值得期待。',
        'path_analysis': [], 'reverse_burn': {'score': 55, 'az': 90.0},
        'map': {'points': _pts, 'sun_az': key_az,
                'observer': {'lat': 22.27, 'lon': 113.58},
                'probability': probability,
                'geometry': {'line_speed': 20.5, 'eq_base_km': 3.6,
                             'glow_band_km': 214.0, 't_start_min': 4.8,
                             't_end_min': 9.5, 'kind': kind}},
        'pm25': _pm, 'table_48h': _t48, 'weather_3h': _w3h,
        'cross_section': {'x_labels': ['珠海', '江门'], 'x_dist': [0, 80],
                          'levels': [{'name': '850hPa', 'height_km': 1.5},
                                     {'name': '500hPa', 'height_km': 5.5}],
                          'rh': [[60, 70], [40, 55]], 'timeline': [],
                          'cloud_bands': {'low': low, 'mid': mid,
                                          'high': high}},
    }


def build_align(kind='sunset', probability=58, aod=0.22, waxing=True,
                target_az=270.0, tol=2.0, alt_max=10.0):
    """合成 /api/align 返回（4 个时段窗口），用于前端模式/事件联动回归。"""
    base = build(kind=kind, probability=probability, aod=aod, waxing=waxing)

    def win(body, period, name, time, alt, az, daz, rising, illum=None):
        return {
            'type': '悬日' if body == 'sun' else '悬月',
            'body': body, 'period': period, 'period_name': name,
            'date': '2026-09-10', 'best_time': time,
            'best_alt': alt, 'best_az': az, 'best_daz': daz,
            'rising': rising, 'alt_quality': '低角度',
            'vis_prob': 72 if body == 'sun' else 55,
            'cloud_at': {'low': 18, 'mid': 35, 'high': 45},
            'illum': illum,
            'reason': '云量少，可见性好' if body == 'sun'
                      else '云量中等，薄云可透',
        }

    def pro(w):
        # 专业版字段：几何/天空/多模式/可行性/风险
        w.update({
            'alt_rate': -0.23 if w['period'] == 'sunset' else 0.23,
            'az_rate': 0.10, 'angular_size_deg': 0.53,
            'refraction_deg': 0.09, 'apparent_alt': round(w['best_alt'] + 0.09, 2),
            'time_in_frame_min': 17.3,
            'sky': {'sun_alt': 9.6 if w['body'] == 'sun' else -6.2,
                    'twilight': '白天' if w['body'] == 'sun' else '民用暮光',
                    'moon_illum': 86.0, 'moon_phase': '盈凸月'},
            'models': {
                'ECMWF': {'cloud': {'low': 18, 'mid': 35, 'high': 45},
                          'vis_prob': 66},
                'GFS': {'cloud': {'low': 30, 'mid': 40, 'high': 50},
                        'vis_prob': 52},
                'ICON': {'cloud': {'low': 15, 'mid': 30, 'high': 40},
                         'vis_prob': 70},
                'CMA': {'cloud': {'low': 20, 'mid': 35, 'high': 45},
                        'vis_prob': 64},
            },
            'consensus': {'vis_prob': 63, 'std': 7.8,
                          'credibility': '高 · 多模式高度一致'},
            'feasibility': {
                'score': 45, 'verdict': '云层遮挡',
                'dims': {'cloud': 11, 'transparency': 74,
                         'precip': 80, 'stability': 84},
                'cloud_basis': '视线交点',
                'reasons': ['大气通透', '降水风险低'],
                'risks': ['低云(0.8km)交点 114km 处云量 82%，会挡住天体'],
            },
            'sight': {
                'visible': True,
                'apparent_alt': round(w['best_alt'] + 0.09, 2),
                'geo_alt': w['best_alt'],
                'limb_alt': round(w['best_alt'] + 0.36, 2),
                'trans': 0.11, 'vis_prob': 11, 'path_aod': 0.27,
                'vis_path_km': 22.5, 'dip_deg': 1.112,
                'observer_height_m': 1201,
                'bands': {
                    'low': {'occ': 82, 'gh_m': 803, 'd_km': 114.5,
                            'level': 925, 'env_km': [101, 303]},
                    'high': {'occ': 63, 'gh_m': 9756, 'd_km': 365.1,
                             'level': 300, 'env_km': [352, 1057]},
                },
                'models': {
                    'ECMWF': {'vis_prob': 11,
                              'bands': {'low': 82, 'high': 63}},
                    'GFS': {'vis_prob': 68, 'bands': {'low': 5, 'high': 10}},
                },
                'model_spread': 57,
                'levels': [
                    {'level': 925, 'gh_m': 803, 'agl_m': 783, 'band': 'low',
                     'd_km': 114.5, 'sample_km': 100, 'cc': 82.0,
                     'cc_src': 'path', 'gap': False},
                    {'level': 850, 'gh_m': 1535, 'agl_m': 1515, 'band': 'low',
                     'd_km': 153.0, 'sample_km': 140, 'cc': 37.0,
                     'cc_src': 'path', 'gap': False},
                    {'level': 500, 'gh_m': 5908, 'agl_m': 5888, 'band': 'mid',
                     'd_km': 287.1, 'sample_km': 320, 'cc': 51.0,
                     'cc_src': 'path', 'gap': False},
                    {'level': 300, 'gh_m': 9756, 'agl_m': 9736, 'band': 'high',
                     'd_km': 365.1, 'sample_km': 400, 'cc': 63.0,
                     'cc_src': 'path', 'gap': False},
                ],
            },
            'risks': ['低云(0.8km)交点 114km 处云量 82%，会挡住天体',
                      '高云(9.8km)交点 365km 处云量 63%，薄云削弱对比度'],
            'reason': '大气通透；降水风险低',
        })
        # 与后端有窗口时的口径一致：有逐层视线结果就用视线透射率当可见概率。
        # 否则表格写 72%、视线卡写 11%，同一张卡里两个数字互相打脸。
        if w.get('sight') and w['sight'].get('trans') is not None:
            w['vis_prob'] = round(w['sight']['trans'] * 100)
        return w

    out = dict(base)
    out['meta'] = dict(out.get('meta') or {}, version=UI_VERSION,
                       weather_error=None, weather_note=None)
    out['alignment'] = [pro(win('sun', 'sunrise', '日出', '06:26', 2.1, 90.2,
                               0.2, True)),
                        pro(win('sun', 'sunset', '日落', '17:52', 2.4, 269.7,
                                0.3, False)),
                        pro(win('moon', 'moonrise', '月出', '17:40', 1.8, 105.0,
                                0.4, True, 86.0)),
                        pro(win('moon', 'moonset', '月落', '05:12', 1.6, 270.4,
                                0.4, False, 86.0))]
    out['alignment_meta'] = {'target_az': target_az, 'tol': tol,
                             'alt_max': alt_max, 'aov': 3.0, 'elev': 0.3,
                             'alt_lo': -1.8, 'alt_hi': 8.3}
    out['day_events'] = [
        {'key': 'sunrise', 'name': '日出', 'time': '06:12', 'az': 85.8,
         'alt': -0.833, 'daz': 2.6, 'in_range': False},
        {'key': 'sunset', 'name': '日落', 'time': '18:30', 'az': 273.9,
         'alt': -0.833, 'daz': 4.1, 'in_range': True},
        {'key': 'moonrise', 'name': '月出', 'time': '08:56', 'az': 106.1,
         'alt': 0.125, 'daz': 22.9, 'in_range': False},
        {'key': 'moonset', 'name': '月落', 'time': '20:14', 'az': 251.2,
         'alt': 0.125, 'daz': 18.9, 'in_range': False},
    ]
    out['observer'] = {'height_m': 1201, 'height_src': 'terrain',
                       'terrain_elevation_m': 1212, 'horizon_dip_deg': 1.112}
    out['target'] = {'height_m': 900, 'distance_km': 8.0,
                     'elev_geo_deg': -2.19, 'elev_apparent_deg': -2.19,
                     'source': 'target'}
    out['geometry'] = {'lat': 22.271, 'lon': 113.5767, 'azimuth': target_az,
                       'aov': 3.0, 'elev': 0.3, 'elev_source': 'target',
                       'tol': 1.8,
                       'alt_lo': -0.7, 'alt_hi': 8.3}
    _ev = {
        'sunrise': {'time': '06:26', 'cloud': {'low': 18, 'mid': 35, 'high': 45},
                    'vis_prob': 66, 'aod': 0.22, 'pm25': 12, 'precip_prob': 10,
                    'score': 72, 'verdict': '薄云可拍',
                    'dims': {'cloud': 66, 'transparency': 74,
                             'precip': 80, 'stability': 84},
                    'reasons': ['云量条件好'], 'risks': [],
                    'models': {}, 'consensus': {'vis_prob': 63, 'std': 7.8,
                                                'credibility': '高'},
                    'temp': 29, 'humidity': 63, 'wind': 10, 'pressure': 1010,
                    'visibility_km': 24},
        'sunset': {'time': '17:52', 'cloud': {'low': 18, 'mid': 35, 'high': 45},
                   'vis_prob': 66, 'aod': 0.22, 'pm25': 12, 'precip_prob': 10,
                   'score': 72, 'verdict': '薄云可拍',
                   'dims': {'cloud': 66, 'transparency': 74,
                            'precip': 80, 'stability': 84},
                   'reasons': ['云量条件好'], 'risks': [],
                   'models': {}, 'consensus': {'vis_prob': 63, 'std': 7.8,
                                               'credibility': '高'},
                   'temp': 29, 'humidity': 63, 'wind': 10, 'pressure': 1010,
                   'visibility_km': 24},
        'moonrise': {'time': '17:40', 'cloud': {'low': 25, 'mid': 40,
                                                'high': 50},
                     'vis_prob': 55, 'aod': 0.22, 'pm25': 12,
                     'precip_prob': 10, 'score': 62, 'verdict': '薄云可拍',
                     'dims': {'cloud': 55, 'transparency': 74,
                              'precip': 80, 'stability': 84},
                     'reasons': ['大气通透'], 'risks': [],
                     'models': {}, 'consensus': {'vis_prob': 60, 'std': 8.0,
                                                 'credibility': '高'},
                     'temp': 27, 'humidity': 70, 'wind': 8, 'pressure': 1010,
                     'visibility_km': 20},
        'moonset': {'time': '05:12', 'cloud': {'low': 25, 'mid': 40,
                                               'high': 50},
                    'vis_prob': 55, 'aod': 0.22, 'pm25': 12,
                    'precip_prob': 10, 'score': 62, 'verdict': '薄云可拍',
                    'dims': {'cloud': 55, 'transparency': 74,
                             'precip': 80, 'stability': 84},
                    'reasons': ['大气通透'], 'risks': [],
                    'models': {}, 'consensus': {'vis_prob': 60, 'std': 8.0,
                                                'credibility': '高'},
                    'temp': 27, 'humidity': 70, 'wind': 8, 'pressure': 1010,
                    'visibility_km': 20},
    }
    out['outlook'] = [
        {'date': '2026-09-%02d' % (10 + i),
         'weekday': '周' + '一二三四五六日'[i % 7],
         'events': dict(_ev)}
        for i in range(7)
    ]
    _sight_ev = {
        'visible': True, 'apparent_alt': -0.21, 'geo_alt': -0.833,
        'limb_alt': 0.05, 'dip_deg': 1.112, 'observer_height_m': 1201,
        'trans': 0.54, 'vis_prob': 54, 'path_aod': 0.22,
        'vis_path_km': 20.0, 'event_az': 85.8, 'event_alt_geo': -0.833,
        'bands': {
            'low': {'occ': 34, 'gh_m': 1536, 'd_km': 62.2, 'level': 850,
                    'env_km': [101, 303]},
            'mid': {'occ': 0, 'gh_m': 3182, 'd_km': 124.1, 'level': 700,
                    'env_km': [139, 418]},
            'high': {'occ': 24, 'gh_m': 12505, 'd_km': 222.2, 'level': 200,
                     'env_km': [399, 1197]},
        },
        'levels': [
            {'level': 1000, 'gh_m': 122, 'agl_m': 122, 'band': 'low',
             'd_km': 57.2, 'sample_km': 45, 'cc': 33.0, 'cc_src': 'path',
             'gap': False},
            {'level': 925, 'gh_m': 805, 'agl_m': 805, 'band': 'low',
             'd_km': 62.9, 'sample_km': 70, 'cc': 0.0, 'cc_src': 'path',
             'gap': False},
            {'level': 850, 'gh_m': 1536, 'agl_m': 1536, 'band': 'low',
             'd_km': 62.2, 'sample_km': 70, 'cc': 34.0, 'cc_src': 'path',
             'gap': False},
            {'level': 700, 'gh_m': 3182, 'agl_m': 3182, 'band': 'mid',
             'd_km': 124.1, 'sample_km': 100, 'cc': 0.0, 'cc_src': 'path',
             'gap': False},
            {'level': 500, 'gh_m': 5901, 'agl_m': 5901, 'band': 'mid',
             'd_km': 168.4, 'sample_km': 190, 'cc': 70.0, 'cc_src': 'path',
             'gap': False},
            {'level': 300, 'gh_m': 9750, 'agl_m': 9750, 'band': 'high',
             'd_km': 214.4, 'sample_km': 190, 'cc': 12.0, 'cc_src': 'path',
             'gap': False},
            {'level': 200, 'gh_m': 12505, 'agl_m': 12505, 'band': 'high',
             'd_km': 222.2, 'sample_km': 250, 'cc': 24.0, 'cc_src': 'path',
             'gap': False},
        ],
        'models': {'ECMWF': {'vis_prob': 54}, 'GFS': {'vis_prob': 70}},
        'model_spread': 16,
    }
    # 与视线口径自洽：主判定改用逐层透射（dims.cloud = 100*trans），
    # 分数按后端同一公式算，模式表为沿方位采样口径。
    _models_dir = {
        'ECMWF': {'cloud': {'low': 34, 'mid': 0, 'high': 24}, 'vis_prob': 54},
        'GFS': {'cloud': {'low': 12, 'mid': 8, 'high': 40}, 'vis_prob': 62},
        'ICON': {'cloud': {'low': 46, 'mid': 15, 'high': 30}, 'vis_prob': 36},
        'CMA': {'cloud': {'low': 20, 'mid': 5, 'high': 55}, 'vis_prob': 47},
    }
    _cons_dir = {'vis_prob': 50, 'std': 11.0,
                 'credibility': '中高 · 模式基本一致',
                 'basis': '方向采样', 'vis_prob_cam': 66}
    _ev = {k: dict(v) for k, v in _ev.items()}
    for _k, _v in _ev.items():
        _v['vis_prob_agg'] = _v.get('vis_prob')
        _v['vis_prob'] = _sight_ev['vis_prob']
        _v['cloud_basis'] = '视线交点'
        _v['sight'] = dict(_sight_ev)
        _v['dir_sampled'] = True
        _v['dir_distances_km'] = [138, 202, 311]
        _v['dir_azimuth'] = 85.8
        _v['cam_cloud'] = {'low': 20, 'mid': 5, 'high': 90}
        _v['models'] = _models_dir
        _v['models_cam'] = {'ECMWF': {'cloud': {'low': 18, 'mid': 35,
                                               'high': 45}, 'vis_prob': 66}}
        _v['consensus'] = _cons_dir
        # 四维与总分按后端公式重算（0.50*云况 + 0.22*通透 + 0.13*降水 + 0.15*稳定）
        _cloud = round(100 * _sight_ev['trans'])
        _dim = {'cloud': _cloud, 'transparency': 74, 'precip': 30,
                'stability': 24}
        _v['dims'] = _dim
        _v['score'] = round(0.50 * _dim['cloud'] + 0.22 * _dim['transparency']
                            + 0.13 * _dim['precip'] + 0.15 * _dim['stability'])
        _v['verdict'] = ('通透可拍' if _v['score'] >= 78 else
                         '薄云可拍' if _v['score'] >= 58 else
                         '云层遮挡' if _v['score'] >= 38 else '不建议')
        _v['reasons'] = ['视线交点云况好', '大气通透']
        _v['risks'] = ['低云(1.5km)交点 62km 处云量 34%，薄云削弱对比度']
    out['day_weather'] = dict(_ev)
    out['main'] = dict(out['main'])
    out['main']['models'] = out['alignment'][1]['models']
    out['main']['consensus'] = out['alignment'][1]['consensus']
    out['main']['sight'] = out['alignment'][1]['sight']
    out['sight_points'] = {
        'azimuth': target_az,
        'distances_km': [70, 140, 320],
        'points': [{'lat': 22.29, 'lon': 113.20, 'dist_km': 70},
                   {'lat': 22.31, 'lon': 112.80, 'dist_km': 140},
                   {'lat': 22.40, 'lon': 111.70, 'dist_km': 320}],
        'bands': [
            {'band': 'low', 'height_m': 1500,
             'envelope_km': [138, 276, 415], 'crit_alt_deg': -2.5},
            {'band': 'mid', 'height_m': 3200,
             'envelope_km': [202, 404, 606], 'crit_alt_deg': -3.6},
            {'band': 'high', 'height_m': 7600,
             'envelope_km': [311, 622, 933], 'crit_alt_deg': -5.6},
        ],
    }
    out['week'] = []
    for i in range(7):
        ds = '2026-09-%02d' % (10 + i)
        out['week'].append({
            'date': ds, 'weekday': '周' + '一二三四五六日'[i % 7],
            'windows': {
                'sunset': {'best_time': '17:%02d' % (50 + i), 'best_alt': 2.4,
                           'period_name': '日落', 'vis_prob': 66,
                           'verdict': '薄云可拍'},
                'sunrise': {'best_time': '06:%02d' % (20 + i), 'best_alt': 2.1,
                            'period_name': '日出', 'vis_prob': 60,
                            'verdict': '薄云可拍'},
                'moonset': {'best_time': '05:%02d' % (10 + i), 'best_alt': 1.6,
                            'period_name': '月落', 'vis_prob': 55,
                            'verdict': '薄云可拍'},
            }})
    return out

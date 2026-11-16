# -*- coding: utf-8 -*-
"""报告组装：数据获取 -> 8 维评分 -> 几何 -> 天文 -> 地图 -> 全量 JSON"""
import logging
import math
import os
import time
from datetime import datetime, timedelta, timezone
try:  # 以项目根运行（uvicorn app.main:app）
    from app.core import astronomy as astro
    from app.core import geometry as geo
    from app.core import scoring as sc
    from app.core import cities as citydb
    from app.core import sightline as sight
    from app.core import illumination as illum
    from app.services import openmeteo as om
    from app.services import satellite as sat
    from app.services import qweather as qw
    from app.services import forecast_log as flog
except ImportError:  # 以 app/ 为根运行（发布沙箱 / python main.py）
    from core import astronomy as astro
    from core import geometry as geo
    from core import scoring as sc
    from core import cities as citydb
    from core import sightline as sight
    from core import illumination as illum
    from services import openmeteo as om
    from services import satellite as sat
    from services import qweather as qw
    from services import forecast_log as flog

log = logging.getLogger(__name__)

# 服务端版本：前端会与自身 UI 版本比对，不一致时提示重启
# （uvicorn 不带 --reload 时 Python 模块常驻，改代码后必须重启才生效）
APP_VERSION = '2.38'

TZ = 'Asia/Shanghai'
TZ_OFF = 8.0

# 省域网格取数步长：0.25° = 250 点 = 120+120+10 三个上游批次，是单次报告最大的
# 额度开销（约占 80% 加权调用）；0.5° = 64 点，前端画的是离散散点不是插值热力图，
# 这个疏密仍然可读（2026-10-07 审查报告 2.3 / 五章第一档）。
GRID_STEP = 0.5

# 展望窗口（v2.38）：7 → 14 天。14 天以内加长时间窗口**不额外计费**
# （官方计费式 lengthFactor = max(1, days/14)，即 14 天以内恒为 1），所以这是
# 纯赚的分辨率：霞光周概览格子 7 → 14、"未来 48 小时"逐时表 7 → 14 天。
# 本地实时（fetch_local_now）与展望（fc7）共用这个窗口。
OUTLOOK_DAYS = 14

# 气压层廓线双源（v2.37）：ECMWF 是受光判据 / 探空 / 大气截面的主源，
# GFS 做交叉验证（同一小时的云层分歧进风险提示）。成本 +18 加权/份。
PLEVEL_MODELS = ['ecmwf_ifs025', 'gfs_global']
# 视线剖面三方交叉验证（v2.37）：ECMWF + GFS + ICON，`model_spread` 才有意义
SIGHT_MODELS = ['ECMWF', 'GFS', 'ICON']

# 标定口径（v2.37）：主概率 = `sc.consensus` + 555 天标定曲线（CALIB_PTS[_PRD]），
# 而那条曲线是拿**核心模式**的 raw 分布拟合的（mean - std*0.18 - ens_pen）。
# 核心 = 原来的四家确定性模式 + ENS（ENS 是用 ECMWF 集合成员均值另算的一份
# 合成评分，权重 0.25，本来就在 consensus 里，动它等于改判据）。
# 实测（2026-10-08 珠海）：GEM 报全天无高云、其余 6 家报 47~100%，把 7 家一起
# 送进 consensus 会让 raw 76.5 → 66.1（概率 76 → 65，跨过小烧/微烧判定线）——
# 也就是说加料会整体左移校准曲线，等于把「已标定」变成「没标定」。
# 所以：核心模式出主概率；JMA/GEM/UKMO 只做旁证（逐模式展示、扩展分歧提示、
# 视线交叉验证）。要真正让它们进主概率，必须先拿到带标签的多模式历史重跑标定。
CALIB_MODELS = ['ECMWF', 'GFS', 'ICON', 'CMA', 'ENS']

# 取数降级时告诉用户「缺了哪一路」（data_missing 里是这些键）
DATA_SOURCE_NAMES = {
    'forecast': '逐时预报',
    'pressure-levels': '气压层廓线',
    'air-quality': '空气质量 / AOD',
    'grid-forecast': '区域网格预报',
    'grid-aod': '区域气溶胶',
    'ensemble': '集合不确定度',
    'ensemble-total': '集合总云量',
    'local-now': '本地实时预报',
    'aod-today': '当日 AOD',
    'aod-next-day': '次日 AOD',
    'outlook-14d': '14 天展望',
    'anti-burn': '反烧光路',
    'satellite-nowcast': '卫星临近校准',
}

# 集合源显示名（v2.37）：内部 id 出现在给用户看的风险文案里太生硬。
ENS_SOURCE_LABELS = {
    'ecmwf_ifs025': 'ECMWF IFS',
    'ecmwf_aifs025': 'ECMWF AIFS',
    'ncep_gefs025': 'NCEP GEFS',
    'icon_global_eps': 'ICON-EPS',
    'gem_global': 'GEM',
    'ukmo_global_ensemble_20km': 'UKMO',
}

HOUR_VARS = ['cloud_cover', 'cloud_cover_low', 'cloud_cover_mid',
             'cloud_cover_high', 'precipitation', 'precipitation_probability',
             'pressure_msl', 'relative_humidity_2m', 'temperature_2m',
             'wind_speed_10m', 'visibility']
PLEVEL_VARS = ['relative_humidity_1000hPa', 'relative_humidity_850hPa',
               'relative_humidity_700hPa', 'relative_humidity_500hPa',
               'relative_humidity_300hPa']
AQ_VARS = ['aerosol_optical_depth', 'pm2_5', 'us_aqi']

# ---- 悬日月视线遮挡采样（v2.16）----
# 沿视线逐气压层评估"哪层云在多远挡住天体"。ECMWF 全层可用（2026-09 实测）。
SIGHT_LEVELS = [1000, 925, 850, 700, 500, 400, 300, 250, 200]
SIGHT_LEVEL_VARS = ([f'cloudcover_{p}hPa' for p in SIGHT_LEVELS]
                    + [f'geopotential_height_{p}hPa' for p in SIGHT_LEVELS])
# 沿目标方位的远端采样距离（km）：覆盖 0.1~12.5km 云层在低角度下的交点范围。
# v2.37：16 → 24 点——交点距离与最近采样点的 gap 直接决定逐层云量取错点的
# 概率，加密后 `_window_sight` 的 gap 标注与"回退机位云量"会明显减少。
SIGHT_DISTANCES_KM = [1.5, 5, 8, 12, 25, 33, 45, 70, 85, 100, 140, 165, 190,
                      250, 285, 320, 400, 450, 500, 620, 690, 760, 830, 900]
# 7 天概览沿事件方位的代表采样：低/中/高云层高的地平线云距 R·b
OUTLOOK_BAND_HEIGHTS_M = [1500, 3200, 7600]

# 光路采样距离（km）。v2.37：9 → 17 点（保留 0/580 端点与 310 锚点）——
# 光路 AOD 与逐层云量是"沿路平均"，采样越密越不会被单点带偏；旧采样在
# 100~310km 段只有 3 个点，而这一段正是霞光最亮的低空光路。
PATH_DISTANCES = [0, 25, 50, 75, 100, 130, 160, 195, 230, 270, 310, 355, 400,
                  445, 490, 535, 580]

# 气压层 -> 高度 km（教程压高关系表）
PLEVEL_HEIGHTS = {'1000hPa': 0.1, '850hPa': 1.5, '700hPa': 3.0,
                  '500hPa': 5.5, '300hPa': 9.2}

# ---- 光路光照（地影/晨昏线）判据（v2.26）----
# 与 PLEVEL_VARS 同一请求增量拉取：逐层云量 + 真实层高。
# 层 -> 云带映射与压高关系一致：1000/850≈低云、700/500≈中云、300≈高云。
GLOW_LEVEL_BANDS = {'1000hPa': 'low', '850hPa': 'low', '700hPa': 'mid',
                    '500hPa': 'mid', '300hPa': 'high'}
PLEVEL_CLOUD_VARS = ([f'cloudcover_{p}hPa' for p in GLOW_LEVEL_BANDS]
                     + [f'geopotential_height_{p}hPa' for p in GLOW_LEVEL_BANDS])
# 探空要素（v2.33）：同一次气压层请求增量带温度，配合位势高度（层高）与
# RH（露点）得 T/Td 廓线与 0°C 层高度——零新增 HTTP
PLEVEL_TEMP_VARS = [f'temperature_{p}hPa' for p in GLOW_LEVEL_BANDS]
# 云带代表距离（km）：仅用于"该带何时变暗"的展示与 sky.js 逐层受光，
# 受光程度本身逐采样距离各自计算，不受此值影响。
GLOW_BAND_REPR_KM = {'low': 100.0, 'mid': 200.0, 'high': 320.0}
# 受光判据开关：GC_ILLUM=0 时完全退回旧口径（原始画布），便于 A/B 对照
ILLUM_ENABLED = os.environ.get('GC_ILLUM', '1') not in ('0', 'false', 'False')


def _v(rec, name, idx, default=None):
    arr = rec.get(name) or []
    if idx is None or idx < 0 or idx >= len(arr):
        return default
    val = arr[idx]
    return default if val is None else val


def _avg_valid(vals):
    xs = [v for v in vals if v is not None]
    return sum(xs) / len(xs) if xs else None


def horizon_visibility_map(lat: float, lon: float, date_str: str,
                           body: str = 'moon', kind: str = 'rise') -> dict:
    """地平线悬日月可见性热力图：
    在日/月处于地平线附近（月出+1h≈10°、月落-1h、日出/日落时刻）评估各网格
    的可见概率（云况遮挡）与通透质量（CAMS AOD），供"去哪里拍悬日月"决策。
    注：全区统一采用观测点事件时刻所在小时评估（网格内事件时刻差 <25min，
    小于云量小时分辨率）。"""
    date = datetime.strptime(date_str, '%Y-%m-%d')

    # ---------- 观测点事件天文 ----------
    info = {}
    eval_date_str = date_str
    eval_hour = None
    if body == 'sun':
        st = astro.sun_times(date, lat, lon, TZ_OFF)
        ev = st['sunrise'] if kind == 'rise' else st['sunset']
        az = st.get('sunrise_az' if kind == 'rise' else 'sunset_az')
        eval_hour = ev.hour if ev else None
        info.update({'event_time': ev.strftime('%H:%M') if ev else None,
                     'az': az,
                     'label': '悬日（' + ('日出' if kind == 'rise' else '日落') + '）'})
    else:
        mt = astro.moon_times(date, lat, lon, TZ_OFF)
        ev = mt.get('moonrise') if kind == 'rise' else mt.get('moonset')
        info.update({'illum': mt.get('moon_illum'),
                     'phase': mt.get('moon_phase')})
        if not ev:
            return {'error': '该日月亮' + ('升起在次日' if kind == 'rise'
                                          else '落下次日') + '，无地平线窗口',
                    'points': []}
        shift = 1 if kind == 'rise' else -1
        total_h = ev.hour + shift
        day_off = total_h // 24
        eval_hour = total_h % 24
        eval_date_str = (date + timedelta(days=day_off)).strftime('%Y-%m-%d')
        jd_ev = astro.jd_from_datetime(ev) - TZ_OFF / 24.0 + shift / 24.0
        _, az = astro._body_altaz('moon', lat, lon, jd_ev)
        info.update({'event_time': ev.strftime('%H:%M'),
                     'eval_time': f'{eval_hour:02d}:00',
                     'az': round(az, 1),
                     'label': '悬月（' + ('月出初升' if kind == 'rise'
                                       else '月落将没') + '）'})
    if eval_hour is None:
        return {'error': '无有效事件时刻', 'points': []}

    # ---------- 网格数据（省轮廓 0.5°；拉三天防月落跨零点向前一天） ----------
    grid = citydb.province_grid_points(GRID_STEP)
    g_lats = [p[0] for p in grid]
    g_lons = [p[1] for p in grid]
    GRID_MODELS = ['ecmwf_ifs025', 'gfs_global']
    prev_day = (date - timedelta(days=1)).strftime('%Y-%m-%d')
    next_day = (date + timedelta(days=1)).strftime('%Y-%m-%d')
    day_range = ','.join([prev_day, date_str, next_day])
    gfc = om.fetch_hourly(g_lats, g_lons,
                          ['cloud_cover_low', 'cloud_cover_mid',
                           'cloud_cover_high', 'precipitation_probability'],
                          GRID_MODELS, day_range, TZ)
    gaq = om.fetch_aq(g_lats, g_lons, ['aerosol_optical_depth'],
                      day_range, TZ)
    # 评估时刻的完整时间戳（可能跨零点）
    eval_dt_str = f'{eval_date_str}T{eval_hour:02d}:00'

    def quality_word(prob, aod):
        if aod is not None and aod > 0.5:
            return '浑浊'
        if prob >= 70 and (aod is None or aod <= 0.3):
            return '通透'
        if prob >= 45:
            return '尚可'
        if prob >= 25:
            return '勉强'
        return '遮挡'

    points = []
    for i, (la, lo) in enumerate(grid):
        vals = {'low': [], 'mid': [], 'high': [], 'pprob': []}
        for m in GRID_MODELS:
            recs = gfc.get(m) or []
            r = recs[i] if i < len(recs) else {}
            tarr = r.get('time') or []
            ij = tarr.index(eval_dt_str) if eval_dt_str in tarr else \
                om.hour_index(tarr, eval_hour)
            vals['low'].append(_v(r, 'cloud_cover_low', ij, 0.0))
            vals['mid'].append(_v(r, 'cloud_cover_mid', ij, 0.0))
            vals['high'].append(_v(r, 'cloud_cover_high', ij, 0.0))
            vals['pprob'].append(_v(r, 'precipitation_probability', ij, 0.0))
        low = _avg_valid(vals['low']) or 0
        mid = _avg_valid(vals['mid']) or 0
        high = _avg_valid(vals['high']) or 0
        pprob = _avg_valid(vals['pprob']) or 0
        aq_t = (gaq[i].get('time') or []) if i < len(gaq) else []
        ai = aq_t.index(eval_dt_str) if eval_dt_str in aq_t else \
            om.hour_index(aq_t, eval_hour)
        aod = _v(gaq[i] if i < len(gaq) else {}, 'aerosol_optical_depth',
                 ai, 0.2)
        # 地平线视线遮挡：低云全遮挡，中云次之，高云薄可透，降水概率附加
        prob = 100 - (low * 1.0 + mid * 0.4 + high * 0.15
                      + pprob * 0.2)
        prob = max(0.0, min(100.0, prob))
        points.append({'lat': la, 'lon': lo,
                       'prob': round(prob, 1),
                       'quality': quality_word(prob, aod),
                       'low': round(low), 'mid': round(mid),
                       'high': round(high),
                       'aod': round(aod, 2) if aod is not None else None})

    best = max(points, key=lambda p: p['prob']) if points else None
    return {
        'meta': {'lat': lat, 'lon': lon, 'date': date_str,
                 'body': body, 'kind': kind,
                 'generated_at': datetime.now().strftime('%Y-%m-%d %H:%M')},
        **info,
        'eval_hour': eval_hour,
        'eval_date': eval_date_str,
        'cross_day': eval_date_str != date_str,
        'points': points,
        'best': best,
    }


def _probe_site(lat: float, lon: float):
    """站点地形探测（结果缓存）；返回 (site_info, horizon_fn) 或 (None, None)。"""
    try:
        try:
            from app.services import terrain as terrain_svc
        except ImportError:  # app/ 根运行
            from services import terrain as terrain_svc  # type: ignore
        site_info = terrain_svc.probe_site(lat, lon)
        horizon_fn = None
        if site_info.get('horizon_mask'):
            horizon_fn = (lambda az: terrain_svc.horizon_alt_at(
                site_info['horizon_mask'], az))
        return site_info, horizon_fn
    except Exception as e:
        log.info('terrain skip: %s', str(e)[:80])
        return None, None


def _build_alignment_windows(lat: float, lon: float, sun: dict,
                             target_azimuth: float, tol: float, alt_hi: float,
                             site_info: dict = None, horizon_fn=None,
                             alt_lo: float = -1.0):
    """悬日月低角度对准窗口：日+月、晨昏两侧，按高度角升序。
    返回 (windows, meta)。日/月通用，报告与 /api/align 共用。"""
    _ha_target = None
    if site_info and site_info.get('horizon_mask') and horizon_fn:
        try:
            _ha_target = round(horizon_fn(target_azimuth), 1)
        except Exception:
            pass
    centers = []
    for ev in (sun.get('sunrise'), sun.get('sunset')):
        if ev:
            centers.append(astro.jd_from_datetime(ev) - TZ_OFF / 24.0)
    alignment = []
    if centers:
        jd_lo = min(centers) - 0.35
        jd_hi = max(centers) + 0.35
        for body, name in [('sun', '悬日'), ('moon', '悬月')]:
            wins = astro.find_alignment_windows(
                body, lat, lon, jd_lo, jd_hi, target_azimuth,
                tol_deg=tol, alt_lo=alt_lo, alt_hi=alt_hi)
            for w in wins:
                t_best = astro.datetime_from_jd(w['jd_best'] + TZ_OFF / 24.0)
                # 升/落判定：天体在最佳时刻处于上升段还是下降段
                rising = astro.body_rising(body, lat, lon, w['jd_best'])
                period = ('sunrise' if rising else 'sunset') if body == 'sun' \
                    else ('moonrise' if rising else 'moonset')
                w['type'] = name
                w['body'] = body
                w['period'] = period
                w['period_name'] = {'sunrise': '日出', 'sunset': '日落',
                                    'moonrise': '月出', 'moonset': '月落'}[period]
                w['rising'] = bool(rising)
                w['best_time'] = t_best.strftime('%H:%M')
                w['date'] = t_best.strftime('%Y-%m-%d')
                for _k in ('best_alt', 'best_az', 'best_daz'):
                    if w.get(_k) is not None:
                        w[_k] = round(float(w[_k]), 2)
                _alt = w.get('best_alt', 0) or 0
                w['alt_quality'] = ('地平线' if _alt < 2.0 else
                                    '低角度' if _alt < 6.0 else '中角度')
                if _ha_target is not None:
                    w['horizon_alt'] = _ha_target
                    if w.get('best_alt', 0) < _ha_target + 0.3:
                        w['horizon_blocked'] = True
                alignment.append(w)
        # 低角度优先：按高度角升序，再按日期/时刻
        alignment.sort(key=lambda x: (x.get('best_alt', 99),
                                      x.get('date', ''),
                                      x.get('best_time', '')))
    return alignment, {'target_az': target_azimuth, 'tol': tol,
                       'alt_max': alt_hi, 'alt_lo': alt_lo}


def _tracks(date: datetime, lat: float, lon: float):
    """日月 24h 高度角/方位轨迹（30 分钟步长，49 点）。"""
    try:
        jd0 = astro.jd_from_datetime(date) - TZ_OFF / 24.0
        trk = {'time': [], 'sun_alt': [], 'sun_az': [],
               'moon_alt': [], 'moon_az': []}
        for i in range(0, 49):
            jd = jd0 + i / 48.0
            lt = astro.datetime_from_jd(jd + TZ_OFF / 24.0)
            sa, saz = astro._body_altaz('sun', lat, lon, jd)
            ma, maz = astro._body_altaz('moon', lat, lon, jd)
            trk['time'].append(lt.strftime('%H:%M'))
            trk['sun_alt'].append(round(sa, 1))
            trk['sun_az'].append(round(saz, 1))
            trk['moon_alt'].append(round(ma, 1))
            trk['moon_az'].append(round(maz, 1))
        return trk
    except Exception as e:
        log.info('track skip: %s', e)
        return None


def _attach_align_visibility(alignment: list, fc3: dict, moon: dict):
    """给对准窗口补可见概率/云量分量/月相照度/原因（与报告同款加权）。"""
    def _cloud_vis_prob(hh_mm: str, date_prefix: str = None):
        try:
            hh = int(hh_mm[:2])
        except Exception:
            return None
        recs = fc3.get(om.MODELS['ECMWF']) or []
        r = recs[0] if recs else {}
        ij = om.hour_index(r.get('time'), hh, date_prefix)
        lo = _v(r, 'cloud_cover_low', ij, 0.0)
        mi = _v(r, 'cloud_cover_mid', ij, 0.0)
        hi = _v(r, 'cloud_cover_high', ij, 0.0)
        block = lo + 0.45 * mi + 0.2 * hi          # 加权遮挡
        return round(max(0.0, 100.0 - block * 1.05))

    for w in alignment:
        _wdate = w.get('date')
        w['vis_prob'] = _cloud_vis_prob(w['best_time'], _wdate)
        try:
            _hh = int(w['best_time'][:2])
            _recs = fc3.get(om.MODELS['ECMWF']) or []
            _r0 = _recs[0] if _recs else {}
            _ij = om.hour_index(_r0.get('time'), _hh, _wdate)
            w['cloud_at'] = {
                'low': round(_v(_r0, 'cloud_cover_low', _ij, 0.0)),
                'mid': round(_v(_r0, 'cloud_cover_mid', _ij, 0.0)),
                'high': round(_v(_r0, 'cloud_cover_high', _ij, 0.0)),
            }
        except Exception:
            w['cloud_at'] = None
        if w.get('type') == '悬月':
            w['illum'] = (moon or {}).get('moon_illum')
        vp = w.get('vis_prob')
        if vp is None:
            w['reason'] = '窗口时刻云况未知'
        elif vp >= 60:
            w['reason'] = '云量少，可见性好'
        elif vp >= 30:
            w['reason'] = '云量中等，薄云可透'
        else:
            w['reason'] = '云量高，遮挡风险大'
    return alignment


def dewpoint_c(t_c, rh):
    """Magnus 公式：气温(°C) + 相对湿度(%) -> 露点温度(°C)。

    T - Td 小表示该层近饱和（成云更"实"、不易成幡状云）；RH 缺测返回 None。
    """
    if t_c is None or rh is None or rh <= 0:
        return None
    rh = min(float(rh), 100.0)
    g = math.log(rh / 100.0) + 17.62 * t_c / (243.12 + t_c)
    return round(243.12 * g / (17.62 - g), 1)


def freezing_level_km(profile):
    """温度廓线 -> 0°C 层高度（km）。

    ``profile``: [(alt_km, t_c)] 按高度升序；取**最低**的正->负穿越
    （标准 0°C 层定义，线性内插）。首层就在冰点以下 -> 0.0（0°C 层在
    地面以下）；整层冰点以上 -> None（如实返回，前端不显示）。
    """
    prev = None
    for alt, t in profile:
        if alt is None or t is None:
            continue
        if t < 0.0:
            if prev is None:
                return 0.0
            a0, t0 = prev
            if t == t0:
                return round(float(alt), 2)
            return round(a0 + (0.0 - t0) * (alt - a0) / (t - t0), 2)
        prev = (alt, t)
    return None


def _level_profiles(plrecs, idx_h, n_pts):
    """沿光路的逐层云量(%)与真实层高(km)，来自 ECMWF 气压层。

    返回 {'1000hPa': {'cover': [...], 'h_km': [...]}, ...}。
    层高优先取 geopotential_height_*hPa（位势高度即海拔），缺失回退
    PLEVEL_HEIGHTS 的压高关系表；云量缺失记 0（该层不参与受光加权）。
    """
    prof = {}
    for lv in GLOW_LEVEL_BANDS:
        covers, heights = [], []
        for j in range(n_pts):
            r = plrecs[j] if j < len(plrecs) else {}
            ij = om.hour_index(r.get('time'), idx_h) if r else None
            c = _v(r, f'cloudcover_{lv}', ij)
            covers.append(max(0.0, min(100.0, c)) if c is not None else None)
            gh = _v(r, f'geopotential_height_{lv}', ij)
            h_km = gh / 1000.0 if gh is not None else PLEVEL_HEIGHTS.get(lv)
            # 位势高度异常（负值/超过对流层顶量级）时回退压高关系表
            if h_km is None or not (0.03 <= h_km <= 16.0):
                h_km = PLEVEL_HEIGHTS.get(lv)
            heights.append(h_km)
        prof[lv] = {'cover': covers, 'h_km': heights}
    return prof


def _glow_illumination(prof, distances, sun_alt_deg, toward_sun=True):
    """逐云带（low/mid/high）、逐采样距离的受光程度 0~1。

    带内各层按其云量加权（云量 0 的层不参与，否则空层会污染几何）；
    整带无云时用压高关系表的代表层高估算，保证"该带何时变暗"仍有意义。
    返回 {'frac': {band: [...]}, 'h_km': {band: 代表层高},
          'ref_d_km': {band: 展示用代表距离}}。
    """
    out = {'frac': {}, 'h_km': {}, 'ref_d_km': {}}
    for band in ('low', 'mid', 'high'):
        levels = [lv for lv, b in GLOW_LEVEL_BANDS.items() if b == band]
        num = [0.0] * len(distances)
        den = [0.0] * len(distances)
        hs_all, cs_all = [], []
        for lv in levels:
            p = prof.get(lv) or {}
            covers = p.get('cover') or []
            hs = p.get('h_km') or []
            for j, d in enumerate(distances):
                c = covers[j] if j < len(covers) else None
                h = hs[j] if j < len(hs) else None
                if h:
                    hs_all.append(h)
                    cs_all.append(c or 0.0)
                if not c or not h:
                    continue
                num[j] += illum.lit_fraction(h, d, sun_alt_deg,
                                             toward_sun=toward_sun) * c
                den[j] += c
        fracs = []
        for j, d in enumerate(distances):
            if den[j] > 0:
                fracs.append(num[j] / den[j])
            else:
                h_rep = hs_all[0] if hs_all else PLEVEL_HEIGHTS[levels[0]]
                fracs.append(illum.lit_fraction(h_rep, d, sun_alt_deg,
                                                toward_sun=toward_sun))
        out['frac'][band] = [round(f, 3) for f in fracs]
        tot = sum(cs_all)
        out['h_km'][band] = round(
            (sum(h * c for h, c in zip(hs_all, cs_all)) / tot) if tot > 0
            else (hs_all[0] if hs_all else PLEVEL_HEIGHTS[levels[0]]), 2)
        out['ref_d_km'][band] = GLOW_BAND_REPR_KM[band]
    return out


def _alt_at(alt_series, minutes):
    """从 (分钟, 太阳高度角) 序列线性插值；序列外取端点值。"""
    if not alt_series:
        return None
    if minutes <= alt_series[0][0]:
        return alt_series[0][1]
    if minutes >= alt_series[-1][0]:
        return alt_series[-1][1]
    for (m0, a0), (m1, a1) in zip(alt_series, alt_series[1:]):
        if minutes <= m1:
            if m1 == m0:
                return a1
            return a0 + (a1 - a0) * (minutes - m0) / (m1 - m0)
    return alt_series[-1][1]


def _band_transition_min(alt_series, height_km, dist_km, toward_sun=True):
    """该带代表云受光状态翻转的事件相对分钟（负 = 事件前）。

    在 ±90 分钟内以 1 分钟步长找受光程度跨过 0.5 的时刻，返回最靠近
    事件（0 分钟）的那次；全程未跨越返回 None。
    """
    prev = None
    best = None
    for m, alt in alt_series:
        f = illum.lit_fraction(height_km, dist_km, alt, toward_sun=toward_sun)
        if prev is not None and (prev - 0.5) * (f - 0.5) <= 0 and prev != f:
            if best is None or abs(m) < abs(best):
                best = m
        prev = f
    return best


def _sun_alt_at(lat, lon, jd_event, minutes):
    """升落事件后 minutes 分钟的太阳几何高度角（度）。

    用几何高度角（不含折射）——地影判据是纯几何关系，折射不参与。
    异常返回 None。
    """
    try:
        return astro._body_altaz('sun', lat, lon,
                                 jd_event + minutes / 1440.0)[0]
    except Exception:
        return None


def _illum_payload(il, key_time=None):
    """光照（地影）判据摘要，供前端逐层受光渲染与地影线绘制。

    il 为 build_report 里 model_illum[主模式]；无数据返回 None（前端退回
    旧口径渲染，不会因为没有光照信息而报错）。
    """
    if not il or not il.get('geo'):
        return None
    geo_il = il.get('geo') or {}
    bands = {}
    for b in ('low', 'mid', 'high'):
        frac = (geo_il.get('frac') or {}).get(b) or []
        tr = (il.get('trans') or {}).get(b)
        item = {'height_km': (geo_il.get('h_km') or {}).get(b),
                'dist_km': GLOW_BAND_REPR_KM[b],
                'lit_frac': [round(f, 3) for f in frac[1:]],
                'transition_min': tr}
        if tr is not None and key_time is not None:
            item['transition_time'] = (key_time
                                       + timedelta(minutes=tr)).strftime('%H:%M')
        bands[b] = item
    anti = ((il.get('anti') or {}).get('frac') or {}).get('high') or []
    return {
        'enabled': True,
        'minutes_from_event': round(il.get('t_mid') or 0.0, 1),
        'sun_alt_deg': (round(il['sun_alt'], 2)
                        if il.get('sun_alt') is not None else None),
        'distances_km': PATH_DISTANCES[1:],
        'bands': bands,
        'anti_high_lit_frac': [round(f, 3) for f in anti[1:4]],
        'anti_high_transition_min': il.get('anti_trans'),
        'canvas_raw': round(il.get('canvas_raw') or 0.0, 1),
        'canvas_lit': round(il.get('canvas_lit') or 0.0, 1),
        'note': ('受光画布＝光路各层云量 × 该时刻受光程度（地影判据）；'
                 '判定时刻取最佳观赏时段中点，transition_min 为该带代表云'
                 '变暗的事件相对分钟（负 = 事件前）'),
    }


def build_report(lat: float, lon: float, date_str: str, kind: str = 'sunset',
                 target_azimuth: float = None, tol: float = 2.0,
                 alt_max: float = 10.0) -> dict:
    date = datetime.strptime(date_str, '%Y-%m-%d')
    _t0 = time.time()          # 额度降级检测起点（degraded_since）
    # 校准区域分流（v2.35）：珠三角用 PRD 曲线（事件率高、概率映射不同），
    # meta.region 下发给前端，判定词/配色同源分流
    region = sc.region_for(lat, lon)

    # ---------------- 站点地形探测（缓存，海拔永不过期） ----------------
    site_info, horizon_fn = _probe_site(lat, lon)

    # ---------------- 天文（有地形数据时按方位遮蔽角修正升落时刻） ----------------
    sun = astro.sun_times(date, lat, lon, TZ_OFF, horizon_fn=horizon_fn)
    moon = astro.moon_times(date, lat, lon, TZ_OFF, horizon_fn=horizon_fn)
    key_time = sun['sunset'] if kind == 'sunset' else sun['sunrise']
    key_az = sun['sunset_az'] if kind == 'sunset' else sun['sunrise_az']
    jd_key = astro.jd_from_datetime(key_time) - TZ_OFF / 24.0
    line_speed = astro.sunset_line_speed(lat, lon, jd_key)
    hour = key_time.hour
    # 时段：取日落(日出)时刻所在小时与前一小时做平均（云量取整点前后平均）
    idx_h = hour

    # 光照（地影）判据用的太阳高度角序列：事件前后各 90 min、1 min 步长。
    # 纯解析计算，各模式共用；GC_ILLUM=0 时不建序列（退回旧口径）。
    alt_series = []
    if ILLUM_ENABLED:
        for _m in range(-90, 91):
            _a = _sun_alt_at(lat, lon, jd_key, _m)
            if _a is not None:
                alt_series.append((_m, _a))

    # 悬日月窗口（低角度对准：默认高度角上限 10°，晨昏两侧都查）
    alignment = []
    alignment_meta = None
    if target_azimuth is not None:
        alignment, alignment_meta = _build_alignment_windows(
            lat, lon, sun, target_azimuth, tol, alt_max,
            site_info=site_info, horizon_fn=horizon_fn)

    # 天文遮蔽修正量（修正时刻 vs 无遮蔽基准，正 = 推迟）
    if site_info and site_info.get('horizon_mask'):
        try:
            sun0 = astro.sun_times(date, lat, lon, TZ_OFF)
            moon0 = astro.moon_times(date, lat, lon, TZ_OFF)

            def _dmin(a, b):
                if not a or not b:
                    return None
                return round((a - b).total_seconds() / 60.0, 1)
            site_info['astro_delay'] = {
                'sunrise_min': _dmin(sun.get('sunrise'), sun0.get('sunrise')),
                'sunset_min': _dmin(sun.get('sunset'), sun0.get('sunset')),
                'moonrise_min': _dmin(moon.get('moonrise'),
                                      moon0.get('moonrise')),
                'moonset_min': _dmin(moon.get('moonset'), moon0.get('moonset')),
            }
        except Exception as e:
            log.info('astro delay skip: %s', e)

    # ---------------- 数据获取（批次 1：独立 fetch 并行） ----------------
    path_pts = [citydb.destination_point(lat, lon, key_az, d)
                for d in PATH_DISTANCES]
    all_lats = [p[0] for p in path_pts]
    all_lons = [p[1] for p in path_pts]
    aq_lats = list(all_lats) + [c['lat'] for c in citydb.AQ_CITIES]
    aq_lons = list(all_lons) + [c['lon'] for c in citydb.AQ_CITIES]
    grid = citydb.province_grid_points(GRID_STEP)
    g_lats = [p[0] for p in grid]
    g_lons = [p[1] for p in grid]
    GRID_MODELS = ['ecmwf_ifs025', 'gfs_global']
    next_day = (date + timedelta(days=1)).strftime('%Y-%m-%d')
    outlook_end = (date + timedelta(days=OUTLOOK_DAYS - 1)).strftime('%Y-%m-%d')

    # 卫星 P-Tree 高分辨率预取尽早触发（110MB FTP 后台线程，幂等）
    try:
        ev_utc = (key_time - timedelta(hours=8)).replace(tzinfo=timezone.utc)
        sat.arm_event(lat, lon, date_str, kind, ev_utc)
    except Exception:
        pass

    # 10 个 fetch 两两独立（依赖图：入参只依赖 key_az/grid/日期算术），
    # 线程池并行 + openmeteo 域名分桶节流，冷缓存 15 次 HTTP 从串行变流水
    #
    # 单路失败只降级这一路（v2.36）：此前 8 个任务用裸 .result()，任一上游
    # 抖动都会让整份报告变成「API 请求失败」的不透明 500；现在统一走 _res()，
    # 失败/超时 → 默认值 + 记进 data_missing，其余结论照常给出。
    import concurrent.futures as _cf
    import threading as _th

    _missing = []
    _miss_lock = _th.Lock()
    _batch_wait = float(os.environ.get('GC_BATCH_WAIT') or 45)
    _dl1 = time.monotonic() + _batch_wait      # 批次 1 共用一个截止时刻

    def _res(fut, label, default, deadline):
        """取线程结果；超时/异常/None 一律按「该数据源缺失」降级。"""
        if fut is None:
            return default
        v = None
        try:
            v = fut.result(timeout=max(0.1, deadline - time.monotonic()))
        except _cf.TimeoutError:
            log.warning('[batch] %s 超时未返回（>%.0fs），按缺失处理',
                        label, _batch_wait)
        except Exception as e:
            log.warning('[batch] %s 失败：%s', label, str(e)[:100])
        if v is None:
            with _miss_lock:
                _missing.append(label)
            return default
        return v

    _pool = _cf.ThreadPoolExecutor(max_workers=6)
    try:
        f_fc = _pool.submit(om.fetch_hourly, all_lats, all_lons, HOUR_VARS,
                            list(om.MODELS.values()), date_str, TZ)
        f_pl = _pool.submit(om.fetch_hourly, all_lats, all_lons,
                            PLEVEL_VARS + PLEVEL_CLOUD_VARS + PLEVEL_TEMP_VARS,
                            PLEVEL_MODELS, date_str, TZ)
        f_aq = _pool.submit(om.fetch_aq, aq_lats, aq_lons, AQ_VARS,
                            date_str, TZ)
        f_gfc = _pool.submit(om.fetch_hourly, g_lats, g_lons,
                             ['cloud_cover_low', 'cloud_cover_mid',
                              'cloud_cover_high', 'precipitation_probability',
                              'visibility', 'pressure_msl'],
                             GRID_MODELS, date_str, TZ)
        f_gaq = _pool.submit(om.fetch_aq, g_lats, g_lons,
                             ['aerosol_optical_depth'], date_str, TZ)
        f_ens = _pool.submit(om.fetch_ensemble, [lat], [lon],
                             ['cloud_cover_low', 'cloud_cover_mid',
                              'cloud_cover_high'],
                             ','.join([date_str, next_day]), TZ)
        # 总云量四源集合：分层云量只有 ECMWF 系两家有成员，总云量才有四家
        # （2026-10-08 实测，见 openmeteo.ENSEMBLE_TOTAL_MODELS 注释）。
        f_ens_total = _pool.submit(om.fetch_ensemble, [lat], [lon],
                                   ['cloud_cover'],
                                   ','.join([date_str, next_day]), TZ,
                                   om.ENSEMBLE_TOTAL_MODELS)
        f_fc3 = _pool.submit(om.fetch_local_now, [lat], [lon], HOUR_VARS,
                             list(om.MODELS.values()), TZ, OUTLOOK_DAYS)
        f_aq3 = _pool.submit(om.fetch_aq, [lat], [lon], AQ_VARS,
                             date_str, TZ)
        f_aq3n = _pool.submit(om.fetch_aq, [lat], [lon], AQ_VARS,
                              next_day, TZ)
        f_fc7 = _pool.submit(om.fetch_hourly, all_lats, all_lons,
                             ['cloud_cover_low', 'cloud_cover_mid',
                              'cloud_cover_high',
                              'precipitation_probability'],
                             list(om.MODELS.values()),
                             ','.join([date_str, outlook_end]), TZ)
        fc = _res(f_fc, 'forecast', {}, _dl1)
        pl = _res(f_pl, 'pressure-levels', {}, _dl1)
        aq = _res(f_aq, 'air-quality', [], _dl1)
        gfc = _res(f_gfc, 'grid-forecast', {}, _dl1)
        gaq = _res(f_gaq, 'grid-aod', [], _dl1)
        ens = _res(f_ens, 'ensemble', None, _dl1)
        ens_total = _res(f_ens_total, 'ensemble-total', None, _dl1)
        fc3 = _res(f_fc3, 'local-now', {}, _dl1)
        aq3 = _res(f_aq3, 'aod-today', [], _dl1)
        aq3_next = _res(f_aq3n, 'aod-next-day', [], _dl1)
        fc7 = _res(f_fc7, 'outlook-14d', {}, _dl1)
    finally:
        # 不 join：超过截止时刻的请求让它在后台自己收尾，别把 HTTP 响应
        # 也一起拖住（requests 自身仍有 40s×3 的读超时兜底）
        _pool.shutdown(wait=False, cancel_futures=True)

    # ---------------- 每模式 8 维评分 ----------------
    model_results = {}
    per_model_scores = {}
    ec_path_midhigh = ec_p_low = None
    # 光路逐层云量/层高（ECMWF 气压层，与模式无关）：供受光判据使用
    prof = (_level_profiles(pl.get('ecmwf_ifs025') or [], idx_h,
                            len(PATH_DISTANCES))
            if ILLUM_ENABLED else {})
    model_illum = {}          # 各模式的光照几何（择主模式后进 payload）

    for disp_name, model_id in om.MODELS.items():
        recs = fc.get(model_id) or []
        if len(recs) < len(PATH_DISTANCES):
            continue
        local = recs[0]
        idx = om.hour_index(local.get('time'), idx_h)
        idx_m3 = om.hour_index(local.get('time'), (idx_h - 3) % 24)

        low = _v(local, 'cloud_cover_low', idx, 0.0)
        mid = _v(local, 'cloud_cover_mid', idx, 0.0)
        high = _v(local, 'cloud_cover_high', idx, 0.0)
        total_c = _v(local, 'cloud_cover', idx, 0.0)
        pprob = _v(local, 'precipitation_probability', idx, 0.0)
        pmm = _v(local, 'precipitation', idx, 0.0)
        vis = _v(local, 'visibility', idx)
        press = _v(local, 'pressure_msl', idx)
        press3 = _v(local, 'pressure_msl', idx_m3)
        canvas_3h = (_v(local, 'cloud_cover_mid', idx_m3, 0.0)
                     + _v(local, 'cloud_cover_high', idx_m3, 0.0))

        # 光路
        p_low, p_mid, p_high, p_pprob = [], [], [], []
        for j in range(1, len(PATH_DISTANCES)):
            r = recs[j]
            ij = om.hour_index(r.get('time'), idx_h)
            p_low.append(_v(r, 'cloud_cover_low', ij, 0.0))
            p_mid.append(_v(r, 'cloud_cover_mid', ij, 0.0))
            p_high.append(_v(r, 'cloud_cover_high', ij, 0.0))
            p_pprob.append(_v(r, 'precipitation_probability', ij, 0.0))
        path_midhigh = [m + h for m, h in zip(p_mid, p_high)]

        # AOD（光路均值与本地，来自 CAMS，模式无关）；CAMS 取不到时按缺省走
        aq_path = aq[0:len(PATH_DISTANCES)]
        aq0 = aq_path[0] if aq_path else {}      # 空表不得直接下标（原 IndexError）
        aod_local = _v(aq0, 'aerosol_optical_depth', idx, 0.2)
        aod_path_vals = [_v(aq_path[j], 'aerosol_optical_depth', idx)
                         for j in range(1, len(PATH_DISTANCES))
                         if j < len(aq_path)]
        aod_path = _avg_valid(aod_path_vals) or aod_local
        pm25 = _v(aq0, 'pm2_5', idx)

        # 气压层 RH（本地+光路均值）
        plrecs = pl.get('ecmwf_ifs025') or []
        rh300s, rh500s = [], []
        for j in range(len(PATH_DISTANCES)):
            if j < len(plrecs):
                r = plrecs[j]
                ij = om.hour_index(r.get('time'), idx_h)
                rh300s.append(_v(r, 'relative_humidity_300hPa', ij, 30.0))
                rh500s.append(_v(r, 'relative_humidity_500hPa', ij, 40.0))
        rh300 = _avg_valid(rh300s) or 35.0
        rh500 = _avg_valid(rh500s) or 45.0

        # 几何：等效云底与边界
        base_est = geo.estimate_cloud_base_km(low, mid, high)
        eq_base = geo.equivalent_cloud_base_km(base_est, (aod_local + aod_path) / 2)
        # 先用原始光路定「云边界 -> 最佳观赏时段」，再按该时刻各层云的
        # 受光程度把光路画布折成"受光画布"：已被地影遮住的中高云不再算画布
        # （v2.26，见 app/core/illumination.py）。判定时刻取观赏时段中点，
        # 复用既有口径而不是引入新的经验常数。
        d2_raw, canvas_raw, low_avg, boundary = sc.dim2_path_canvas(
            path_midhigh, p_low, PATH_DISTANCES[1:])
        # 边界超出霞光弦长 95% => 视为连续云层（无有效边界）
        if boundary is not None and boundary >= geo.glow_chord_km(eq_base) * 0.95:
            boundary = None
        t_start, t_end, geo_ok = geo.burn_with_cloud_edge(
            eq_base, boundary, line_speed)
        t_mid = (t_start + t_end) / 2.0
        if kind != 'sunset':
            t_mid = -t_mid          # 朝霞：观赏时段在事件之前
        il_geo = il_anti = None
        if ILLUM_ENABLED and alt_series:
            _alt_mid = _alt_at(alt_series, t_mid)
            if _alt_mid is not None:
                il_geo = _glow_illumination(prof, PATH_DISTANCES, _alt_mid)
                il_anti = _glow_illumination(prof, PATH_DISTANCES, _alt_mid,
                                             toward_sun=False)
                _mf, _hf = il_geo['frac']['mid'][1:], il_geo['frac']['high'][1:]
                path_midhigh_lit = [pm * m + ph * h
                                    for pm, ph, m, h
                                    in zip(p_mid, p_high, _mf, _hf)]
                d2, canvas_avg, low_avg, boundary = sc.dim2_path_canvas(
                    path_midhigh_lit, p_low, PATH_DISTANCES[1:])
                if (boundary is not None
                        and boundary >= geo.glow_chord_km(eq_base) * 0.95):
                    boundary = None
        if il_geo is None:
            d2, canvas_avg = d2_raw, canvas_raw
        model_illum[disp_name] = {
            't_mid': t_mid, 'sun_alt': _alt_at(alt_series, t_mid),
            'geo': il_geo, 'anti': il_anti, 'canvas_raw': canvas_raw,
            'canvas_lit': canvas_avg,
            'trans': ({b: _band_transition_min(alt_series,
                                               il_geo['h_km'][b],
                                               GLOW_BAND_REPR_KM[b])
                       for b in ('low', 'mid', 'high')} if il_geo else {}),
            'anti_trans': _band_transition_min(
                alt_series, (il_anti or {}).get('h_km', {}).get('high')
                or PLEVEL_HEIGHTS['300hPa'], GLOW_BAND_REPR_KM['high'],
                toward_sun=False) if il_anti else None,
        }
        if disp_name == 'ECMWF':
            ec_path_midhigh = path_midhigh
            ec_p_low = p_low

        # dim1 特征：事件时刻 ±1h 窗口平均（2026-09-05 实证：留出 AUC 0.722->0.727，
        # 平滑单时刻云量噪声；光路点维持事件时刻口径）
        def _win1h(var, default=0.0):
            vals = []
            for ii in (idx - 1, idx, idx + 1):
                if ii < 0:
                    continue
                v = _v(local, var, ii)
                if v is not None:
                    vals.append(v)
            return sum(vals) / len(vals) if vals else default

        dims = {
            'dim1': sc.dim1_midhigh_canvas(
                _win1h('cloud_cover_mid'), _win1h('cloud_cover_high'),
                _win1h('cloud_cover_low')),
            'dim2': d2,
            'dim3': sc.dim3_precip(pmm, _avg_valid(p_pprob) or 0, pprob),
            'dim4': sc.dim4_visibility_pm25(vis, pm25),
            'dim5': sc.dim5_cloud_trend(mid + high, canvas_3h),
            'dim6': sc.dim6_pressure(press, press3),
            'dim7': sc.dim7_aod(aod_local, aod_path),
            'dim8': sc.dim8_high_moisture(rh300, rh500, high),
        }
        feasible = boundary is not None or (mid + high) >= 30
        dead = (low + mid + high) <= 5
        total = sc.combine(dims, feasible, dead)
        # 云层抖动加成（v2.6 回测采纳：事件前 3h 画布标准差，留出 AUC +0.008；
        # 云层开合/系统过境形成边界更易烧，权重 0.02 微调不破坏 8 维语义）
        if not dead:
            cvs = []
            for _off in (0, -1, -2, -3):
                _i = idx + _off
                if _i < 0:
                    continue
                cvs.append(_v(local, 'cloud_cover_mid', _i, 0.0)
                           + _v(local, 'cloud_cover_high', _i, 0.0))
            if cvs:
                _mu = sum(cvs) / len(cvs)
                _cv3 = (sum((c - _mu) ** 2 for c in cvs) / len(cvs)) ** 0.5
                total = round(min(100.0, total + 0.02 * min(_cv3 * 6.0, 100.0)), 1)
        per_model_scores[disp_name] = total
        model_probability = sc.calibrated_probability(
            total if not dead else min(total, 8), region)

        quality = geo.burn_quality(eq_base, aod_local, aod_path)

        model_results[disp_name] = {
            'model_id': model_id,
            'cloud': {'total': round(total_c), 'low': round(low),
                      'mid': round(mid), 'high': round(high)},
            'weather': {'temp': _v(local, 'temperature_2m', idx),
                        'humidity': _v(local, 'relative_humidity_2m', idx),
                        'pressure': press, 'visibility_km':
                            round(vis / 1000, 1) if vis else None,
                        'wind': _v(local, 'wind_speed_10m', idx),
                        'precip_prob': pprob},
            'aod_local': round(aod_local, 2) if aod_local is not None else None,
            'aod_path': round(aod_path, 2) if aod_path is not None else None,
            'pm25': round(pm25, 1) if pm25 is not None else None,
            'dims': dims,
            'score': total,
            'probability': model_probability,
            'canvas_avg': canvas_avg, 'low_avg': low_avg,
            'canvas_avg_raw': canvas_raw,
            'boundary_km': boundary,
            'eq_base_km': round(eq_base, 2),
            'base_est_km': base_est,
            't_start_min': round(t_start, 1), 't_end_min': round(t_end, 1),
            'geo_ok': bool(geo_ok and feasible),
            'quality': quality,
            'illum': bool(model_illum.get(disp_name, {}).get('geo')),
        }

    # ---------------- 多源集合预报（ECMWF+GEFS+ICON，共 ~119 成员） ----------------
    # （ens 已在批次 1 并行拉取，失败为 None）
    # 注意口径：`ens_spread` 仍只取 ECMWF 成员均值——它参与 consensus 的
    # ens_pen 且已被 555 天标定吸收；多出来的源只用于**不确定区间**与跨源分歧。
    ens_spread = None
    ens_band = None
    ens_disp = None
    ens_sources = None
    ens_src_spread = None
    # 总云量四源（v2.38）：分层只有 ECMWF 系两家，总云量有四家
    ens_total_sources = None
    ens_total_src_spread = None
    try:
        if ens:
            r0 = ens[0]
            t_ens = r0.get('time') or []
            idx_e = om.hour_index(t_ens, idx_h)
            idx_e3 = om.hour_index(t_ens, (idx_h - 3) % 24)

            def ens_v(var, i):
                d = r0.get(var) or {}
                arr_m = d.get('mean') or []
                arr_s = d.get('std') or []
                m = arr_m[i] if 0 <= i < len(arr_m) else None
                s = arr_s[i] if 0 <= i < len(arr_s) else None
                return m, s

            def ens_q(var, i, q):
                """全体成员分位值（P10/P50/P90），缺失返回 None。"""
                d = r0.get(var) or {}
                arr = d.get(q) or []
                return arr[i] if 0 <= i < len(arr) else None

            def ens_ec(var, i):
                """ECMWF 单源均值；单源响应没有 ec_mean 时回退全体均值。"""
                d = r0.get(var) or {}
                arr = d.get('ec_mean') or d.get('mean') or []
                return arr[i] if 0 <= i < len(arr) else None

            def ens_ss(var, i):
                """各源均值的标准差（跨源分歧），缺失返回 None。"""
                d = r0.get(var) or {}
                arr = d.get('src_spread') or []
                return arr[i] if 0 <= i < len(arr) else None

            lm, _ = ens_v('cloud_cover_low', idx_e)
            mm, _ = ens_v('cloud_cover_mid', idx_e)
            hm, hs = ens_v('cloud_cover_high', idx_e)
            ms3, _ = ens_v('cloud_cover_mid', idx_e3)
            hs3, _ = ens_v('cloud_cover_high', idx_e3)
            if mm is not None and hm is not None:
                if ec_path_midhigh:
                    d2e, canvas_e, low_e, boundary_e = sc.dim2_path_canvas(
                        ec_path_midhigh, ec_p_low, PATH_DISTANCES[1:])
                else:
                    d2e, canvas_e, low_e, boundary_e = 50.0, None, None, None
                # 复用 ECMWF 的 AOD（CAMS 与模式无关）
                ec_res = model_results.get('ECMWF', {})
                aod_l = ec_res.get('aod_local') or 0.2
                aod_p = ec_res.get('aod_path') or 0.2
                base_est_e = geo.estimate_cloud_base_km(lm or 0, mm, hm)
                eq_base_e = geo.equivalent_cloud_base_km(
                    base_est_e, (aod_l + aod_p) / 2)
                dims_e = {
                    'dim1': sc.dim1_midhigh_canvas(mm, hm, lm or 0),
                    'dim2': d2e,
                    'dim3': ec_res.get('dims', {}).get('dim3', 70),
                    'dim4': ec_res.get('dims', {}).get('dim4', 70),
                    'dim5': sc.dim5_cloud_trend(mm + hm,
                                                (ms3 or 0) + (hs3 or 0)),
                    'dim6': ec_res.get('dims', {}).get('dim6', 80),
                    'dim7': ec_res.get('dims', {}).get('dim7', 70),
                    'dim8': ec_res.get('dims', {}).get('dim8', 70),
                }
                feas_e = boundary_e is not None or (mm + hm) >= 30
                dead_e = ((lm or 0) + mm + hm) <= 5
                total_e = sc.combine(dims_e, feas_e, dead_e)
                per_model_scores['ENS'] = total_e
                model_results['ENS'] = {
                    'model_id': 'ecmwf_ifs025_ensemble',
                    'cloud': {'total': round((lm or 0) + mm + hm),
                              'low': round(lm or 0), 'mid': round(mm),
                              'high': round(hm)},
                    'weather': ec_res.get('weather', {}),
                    'aod_local': ec_res.get('aod_local'),
                    'aod_path': ec_res.get('aod_path'),
                    'pm25': ec_res.get('pm25'),
                    'dims': dims_e,
                    'score': total_e,
                    'probability': sc.calibrated_probability(
                        total_e if not dead_e else min(total_e, 8), region),
                    'canvas_avg': canvas_e, 'low_avg': low_e,
                    'boundary_km': boundary_e,
                    'eq_base_km': round(eq_base_e, 2),
                    'base_est_km': base_est_e,
                    't_start_min': ec_res.get('t_start_min'),
                    't_end_min': ec_res.get('t_end_min'),
                    'geo_ok': ec_res.get('geo_ok'),
                    'quality': ec_res.get('quality'),
                }
            sm_ = ens_ec('cloud_cover_mid', idx_e)
            sh_ = ens_ec('cloud_cover_high', idx_e)
            _ss_m = ens_ss('cloud_cover_mid', idx_e)
            _ss_h = ens_ss('cloud_cover_high', idx_e)
            if _ss_m is not None and _ss_h is not None:
                ens_src_spread = round((_ss_m + _ss_h) / 2, 1)
            _srcs = set()
            for _cv in ('cloud_cover_low', 'cloud_cover_mid',
                        'cloud_cover_high'):
                _srcs.update((r0.get(_cv) or {}).get('sources') or [])
            if _srcs:
                ens_sources = sorted(_srcs)
            if sm_ is not None and sh_ is not None:
                ens_spread = round((sm_ + sh_) / 2, 1)
                # 分位带：50 成员的中高云量分布（P10/P50/P90），把"离散度"
                # 从一句风险提示变成一个可看的区间
                ens_band = {}
                for _q in ('p10', 'p50', 'p90'):
                    _mv = ens_q('cloud_cover_mid', idx_e, _q)
                    _hv = ens_q('cloud_cover_high', idx_e, _q)
                    if _mv is not None and _hv is not None:
                        ens_band[_q] = round((_mv + _hv) / 2, 1)
                if (ens_band.get('p10') is not None
                        and ens_band.get('p90') is not None):
                    # 真实离散度 = P10~P90 半宽；只用于措辞，不参与惩罚
                    ens_disp = round((ens_band['p90'] - ens_band['p10']) / 2.0, 1)
        # 总云量四源跨源分歧（v2.38）：独立于分层集合的另一个请求
        if ens_total:
            t0 = ens_total[0]
            _td = t0.get('cloud_cover') or {}
            _tss = _td.get('src_spread') or []
            _it = om.hour_index(t0.get('time') or [], idx_h)
            if 0 <= _it < len(_tss) and _tss[_it] is not None:
                ens_total_src_spread = round(_tss[_it], 1)
            _tcs = _td.get('sources') or []
            if _tcs:
                ens_total_sources = sorted(_tcs)
    except Exception as e:
        log.info('ensemble skip: %s', e)

    # 主概率只吃核心四模式（见 CALIB_MODELS 注释）；扩展模式另行量化分歧
    _core = {m: v for m, v in per_model_scores.items() if m in CALIB_MODELS}
    _ext = {m: v for m, v in per_model_scores.items() if m not in _core}
    if not _core:                      # 核心全缺（极端降级）：退回可用模式
        _core, _ext = dict(per_model_scores), {}
    _core_w = sum(om.MODEL_WEIGHTS.get(m, 0.25) for m in _core)
    _core_mean = (sum(v * om.MODEL_WEIGHTS.get(m, 0.25)
                      for m, v in _core.items()) / _core_w) if _core_w else 0.0
    # 扩展模式的分歧按「与核心共识的最大偏离」量化：整组均值会把单一离群
    # 模式（恰恰是预警信号）稀释掉——实测 GEM 独自报「全天无高云」时，
    # 整组均值只偏离 14.6 分，而 GEM 自己是 40+ 分的偏离。
    ext_spread = None
    _ext_off = {}
    if _ext:
        _ext_off = {m: round(abs(v - _core_mean), 1) for m, v in _ext.items()}
        ext_spread = max(_ext_off.values())
    cons = sc.consensus(_core, om.MODEL_WEIGHTS, ens_spread,
                        ens_disp=ens_disp, region=region)
    probability = cons['probability']

    # 主模式（按 om.MODEL_PRIORITY，ECMWF 优先；缺失时依次回退到下一家）
    main_name = next((m for m in om.MODEL_PRIORITY
                      if m in model_results), None)
    main = model_results.get(main_name, {}) if main_name else {}

    # 最佳观赏时段（本地日落后 t_start ~ t_end；朝霞为日前）
    best_window = None
    if main:
        if kind == 'sunset':
            t0 = key_time + timedelta(minutes=main.get('t_start_min', 0))
            t1 = key_time + timedelta(minutes=main.get('t_end_min', 10))
        else:
            t0 = key_time - timedelta(minutes=main.get('t_end_min', 10))
            t1 = key_time - timedelta(minutes=main.get('t_start_min', 0))
        best_window = {'start': t0.strftime('%H:%M'), 'end': t1.strftime('%H:%M')}

    # ---------------- 大气截面图（ECMWF + CAMS）----------------
    plrecs = pl.get('ecmwf_ifs025') or []
    x_labels, x_dist = [], []
    heat_rh = []          # [level][point] RH
    levels_order = ['1000hPa', '850hPa', '700hPa', '500hPa', '300hPa']
    for li, lv in enumerate(levels_order):
        row = []
        for j in range(len(PATH_DISTANCES)):
            r = plrecs[j] if j < len(plrecs) else {}
            ij = om.hour_index(r.get('time'), idx_h)
            row.append(_v(r, f'relative_humidity_{lv}', ij, 30.0))
        heat_rh.append([round(x) for x in row])

    # ---- 探空（v2.33）：0°C 层 + T/Td 廓线（ECMWF 观测点，事件时刻）----
    # 位势高度给层高（异常回退压高关系表，与 _level_profiles 同口径），
    # 露点由 T+RH 的 Magnus 公式得。缺层如实跳过，凑不足 2 层不给图。
    sound = None
    try:
        _ec = (fc.get(om.MODELS['ECMWF']) or [{}])[0]
        _ij = om.hour_index(_ec.get('time'), idx_h)
        surf_t = _v(_ec, 'temperature_2m', _ij)
        surf_rh = _v(_ec, 'relative_humidity_2m', _ij)
        prof_pts = []
        if surf_t is not None:
            prof_pts.append({'p': '地面', 'alt_km': 0.0, 't_c': surf_t,
                             'td_c': dewpoint_c(surf_t, surf_rh),
                             'rh': surf_rh})
        for lv in levels_order:
            r = plrecs[0] if plrecs else {}
            ij = om.hour_index(r.get('time'), idx_h)
            t = _v(r, f'temperature_{lv}', ij)
            gh = _v(r, f'geopotential_height_{lv}', ij)
            rh = _v(r, f'relative_humidity_{lv}', ij)
            if t is None:
                continue
            z_km = gh / 1000.0 if gh is not None else PLEVEL_HEIGHTS.get(lv)
            if z_km is None or not (0.03 <= z_km <= 16.0):
                z_km = PLEVEL_HEIGHTS.get(lv)
            prof_pts.append({'p': lv, 'alt_km': round(z_km, 2), 't_c': t,
                             'td_c': dewpoint_c(t, rh), 'rh': rh})
        prof_pts.sort(key=lambda d: d['alt_km'])
        if len(prof_pts) >= 2:
            sound = {
                'levels': prof_pts,
                'freezing_km': freezing_level_km(
                    [(d['alt_km'], d['t_c']) for d in prof_pts]),
            }
            main['sound'] = sound
    except Exception as e:
        log.info('sound skip: %s', str(e)[:80])

    for j, d in enumerate(PATH_DISTANCES):
        pt = path_pts[j]
        nm, bd = citydb.nearest_city(pt[0], pt[1])
        x_labels.append(f'{nm}\n{d}km' if j > 0 else f'{nm}·观测点')
        x_dist.append(d)

    # 剖面时间演变（T-6h ~ T+1h 逐时，复用气压层/云量全天序列，零 API 增量）
    cs_timeline = []
    try:
        base_i0 = om.hour_index((plrecs[0] or {}).get('time'), idx_h) \
            if plrecs else None
        lrec0 = (fc.get(om.MODELS.get(main_name, '')) or [{}])[0] \
            if main_name else {}
        li0 = om.hour_index(lrec0.get('time'), idx_h) if lrec0 else None
        n_hrs = len((plrecs[0] or {}).get('time') or []) if plrecs else 0
        for off in range(-6, 2):
            ij = base_i0 + off if base_i0 is not None else None
            if ij is None or ij < 0 or ij >= n_hrs:
                continue
            rows_t = []
            for lv in levels_order:
                row_t = []
                for j in range(len(PATH_DISTANCES)):
                    r = plrecs[j] if j < len(plrecs) else {}
                    row_t.append(_v(r, f'relative_humidity_{lv}', ij, 30.0))
                rows_t.append([round(x) for x in row_t])
            cb = None
            if li0 is not None:
                ii = li0 + off
                if 0 <= ii < len(lrec0.get('time') or []):
                    cb = {'low': round(_v(lrec0, 'cloud_cover_low', ii, 0.0)),
                          'mid': round(_v(lrec0, 'cloud_cover_mid', ii, 0.0)),
                          'high': round(_v(lrec0, 'cloud_cover_high', ii, 0.0))}
            cs_timeline.append({'offset_min': off * 60, 'rh': rows_t,
                                'cloud_bands': cb})
    except Exception as e:
        log.info('cs timeline skip: %s', e)

    # ---------------- 反烧 ----------------
    rev_recs = fc[om.MODELS[main_name]] if main_name else []
    p_high_rev = []
    for j in range(1, len(PATH_DISTANCES)):
        r = rev_recs[j] if j < len(rev_recs) else {}
        ij = om.hour_index(r.get('time'), idx_h)
        p_high_rev.append(_v(r, 'cloud_cover_high', ij, 0.0))
    # 反向路径高云
    anti_az = (key_az + 180) % 360
    anti_pts = [citydb.destination_point(lat, lon, anti_az, d)
                for d in PATH_DISTANCES[1:4]]
    # 批次 2：反烧光路 fetch 与卫星临近校准并行（两者均只依赖 main/key_az）
    import os as _os
    _nc_window = float(_os.environ.get('NOWCAST_WINDOW_MIN', 360))
    minutes_to_event = (key_time - datetime.now()).total_seconds() / 60.0
    _need_nc = -90 <= minutes_to_event <= _nc_window
    # 反烧光路：主模式全缺（上游整路挂掉）时没有可走的模式，不能拿 None
    # 去查 om.MODELS（会 KeyError 打成 500）
    _pool2 = _cf.ThreadPoolExecutor(max_workers=2)
    try:
        f_anti = (_pool2.submit(om.fetch_hourly,
                                [p[0] for p in anti_pts],
                                [p[1] for p in anti_pts],
                                ['cloud_cover_high'],
                                [om.MODELS[main_name]], date_str, TZ)
                  if main_name else None)
        f_nc = (_pool2.submit(build_nowcast, lat, lon, date_str, kind,
                              key_time, key_az, main) if _need_nc else None)
        _dl2 = time.monotonic() + _batch_wait
        anti_fc = _res(f_anti, 'anti-burn', {}, _dl2)
        nowcast_data = _res(f_nc, 'satellite-nowcast', None, _dl2)
    finally:
        _pool2.shutdown(wait=False, cancel_futures=True)
    anti_high = []
    arecs = (anti_fc.get(om.MODELS[main_name]) or []) if main_name else []
    for j in range(len(anti_pts)):
        r = arecs[j] if j < len(arecs) else {}
        ij = om.hour_index(r.get('time'), idx_h)
        anti_high.append(_v(r, 'cloud_cover_high', ij, 0.0))
    # 反日侧受光修正：反日方向的阳光要"绕"过地球，同一太阳高度角下比
    # 向日侧更早变暗（临界角 α* = d/R − b(h)，可为正）。反烧/余晖因此常被
    # 高估——这里只统计仍然受光的高云（v2.26，GC_ILLUM=0 可关闭）。
    anti_high_eff = anti_high
    _main_il = model_illum.get(main_name) or {}
    _anti = (_main_il.get('anti') or {}).get('frac', {}).get('high')
    if _anti:
        anti_high_eff = [max(0.0, a * f)
                         for a, f in zip(anti_high, _anti[1:1 + len(anti_high)])]
    rev_score = sc.reverse_burn_score(anti_high_eff, PATH_DISTANCES[1:4],
                                      main.get('aod_local'),
                                      main.get('cloud', {}).get('high'))

    # ---------------- 地图网格评分（省轮廓 0.25°，概率分+质量分） ----------------
    gaq_map = {}
    for i, (la, lo) in enumerate(grid):
        gaq_map[(la, lo)] = gaq[i] if i < len(gaq) else {}
    map_points = []
    obs_best_d = 1e9
    for i, (la, lo) in enumerate(grid):
        vals = {}
        for m in GRID_MODELS:
            recs = gfc.get(m) or []
            r = recs[i] if i < len(recs) else {}
            ij = om.hour_index(r.get('time'), idx_h)
            ij3 = om.hour_index(r.get('time'), (idx_h - 3) % 24)
            vals.setdefault('low', []).append(_v(r, 'cloud_cover_low', ij, 0.0))
            vals.setdefault('mid', []).append(_v(r, 'cloud_cover_mid', ij, 0.0))
            vals.setdefault('high', []).append(_v(r, 'cloud_cover_high', ij, 0.0))
            vals.setdefault('mid3', []).append(
                _v(r, 'cloud_cover_mid', ij3, 0.0))
            vals.setdefault('high3', []).append(
                _v(r, 'cloud_cover_high', ij3, 0.0))
            vals.setdefault('pprob', []).append(
                _v(r, 'precipitation_probability', ij, 0.0))
            vals.setdefault('vis', []).append(_v(r, 'visibility', ij, 15000))
            vals.setdefault('press', []).append(_v(r, 'pressure_msl', ij))
            vals.setdefault('press3', []).append(_v(r, 'pressure_msl', ij3))
        low = _avg_valid(vals['low']) or 0
        mid = _avg_valid(vals['mid']) or 0
        high = _avg_valid(vals['high']) or 0
        mid3 = _avg_valid(vals['mid3']) or 0
        high3 = _avg_valid(vals['high3']) or 0
        pprob = _avg_valid(vals['pprob']) or 0
        vis = _avg_valid(vals['vis'])
        press = _avg_valid(vals['press'])
        press3 = _avg_valid(vals['press3'])
        # AOD 用该格点自己的时间轴定位（原先借用模型循环遗留的 idx：既没有
        # 数据也会 UnboundLocalError，有数据时又依赖上一步的巧合）
        _gr = gaq_map.get((la, lo)) or {}
        aod = _v(_gr, 'aerosol_optical_depth',
                 om.hour_index(_gr.get('time'), idx_h), 0.2)
        canvas = mid + high
        dead = (low + mid + high) <= 5
        dims_cell = {
            'dim1': sc.dim1_midhigh_canvas(mid, high, low),
            'dim2': 50,          # 光路维度按格点不计算（中性）
            'dim3': sc.dim3_precip(0, 0, pprob),
            'dim4': sc.dim4_visibility_pm25(vis, None),
            'dim5': sc.dim5_cloud_trend(canvas, mid3 + high3),
            'dim6': sc.dim6_pressure(press, press3),
            'dim7': sc.dim7_aod(aod, aod),
            'dim8': 50,          # 气压层湿度按格点不计算（中性）
        }
        total_cell = sc.combine(dims_cell, feasible=True, dead_sky=dead)
        prob_cell = sc.calibrated_probability(
            total_cell if not dead else min(total_cell, 8), region)
        # 质量分：通透(AOD) + 画布结构 + 云底高度（颜色区间）
        base_est_cell = geo.estimate_cloud_base_km(low, mid, high)
        quality_cell = round(
            0.45 * sc.dim7_aod(aod, aod)
            + 0.35 * dims_cell['dim1']
            + 0.20 * min(100.0, base_est_cell / 8.0 * 100.0), 1)
        if dead:
            quality_cell = round(quality_cell * 0.3, 1)
        # 方向调制：沿太阳方位 1.0，垂直 0.75，反向 0.5（正烧区域）
        brg = citydb.bearing_between(lat, lon, la, lo)
        daz = abs((brg - key_az + 180) % 360 - 180)
        direction = math.cos(math.radians(daz / 2)) ** 1.5
        dist = citydb.haversine_km(lat, lon, la, lo)
        decay = max(0.35, 1.0 - dist / 650.0)
        prob_final = round(prob_cell * (0.7 + 0.3 * direction) * decay, 1)
        # 观测点最近格：应用与主报告一致的光路可行性门控（口径统一）
        if dist < obs_best_d:
            obs_best_d = dist
            if main and main.get('geo_ok') is False:
                prob_final = round(min(prob_final, 25.0 * 0.4), 1)
        # 反烧评分：仅高云参与，沿反日方位，有效距离包络 30~350km
        daz_rev = abs((brg - anti_az + 180) % 360 - 180)
        rev_dir = max(0.0, math.cos(math.radians(daz_rev))) ** 1.2
        if 30 <= dist <= 350:
            rev_env = 1.0
        elif dist < 30:
            rev_env = max(0.4, dist / 30.0)
        else:
            rev_env = max(0.0, 1.0 - (dist - 350) / 250.0)
        high_q = sc.dim1_midhigh_canvas(0, high, 0)  # 仅高云画布
        rev_s = (0.55 * high_q + 0.25 * sc.dim7_aod(aod, aod)) * rev_dir * rev_env \
            * (0.7 + 0.3 * (1 - decay * 0.3))
        map_points.append({'lat': la, 'lon': lo,
                           'score': prob_final,
                           'prob': prob_final,
                           'quality': quality_cell,
                           'rev': round(min(100, rev_s), 1),
                           'dist': round(dist), 'brg': round(brg, 1),
                           'canvas': round(canvas), 'low': round(low),
                           'mid': round(mid), 'high': round(high),
                           'aod': round(aod, 2) if aod else None})

    # PM2.5 城市点（AQ 数组的后半段）
    pm_points = []
    base_i = len(PATH_DISTANCES)
    for j, c in enumerate(citydb.AQ_CITIES):
        r = aq[base_i + j] if base_i + j < len(aq) else {}
        _ij = om.hour_index(r.get('time'), idx_h)   # 用该记录自己的时间轴
        pm = _v(r, 'pm2_5', _ij)
        aqi = _v(r, 'us_aqi', _ij)
        pm_points.append({'name': c['name'], 'lat': c['lat'], 'lon': c['lon'],
                          'pm25': round(pm, 0) if pm else None,
                          'aqi': round(aqi) if aqi else None})

    # ---------------- 48h 云量分层表 + 气象要素 ----------------
    # （fc3/aq3/aq3_next 已在批次 1 并行拉取）
    # 合并两日 AQ 时间轴，便于按预报下标取 AOD/PM2.5
    aq_merged = {}
    if aq3 and aq3_next:
        t0 = list(aq3[0].get('time') or [])
        t1 = list(aq3_next[0].get('time') or [])
        aq_merged['time'] = t0 + t1
        for v in AQ_VARS:
            aq_merged[v] = list(aq3[0].get(v) or []) + list(aq3_next[0].get(v) or [])
    table_48h = []
    now_local = datetime.now()
    times0 = (fc3[om.MODELS['ECMWF']][0].get('time') if fc3.get(om.MODELS['ECMWF'])
              else []) or []
    for i, t in enumerate(times0):
        try:
            tt = datetime.strptime(t, '%Y-%m-%dT%H:%M')
        except Exception:
            continue
        if tt < now_local - timedelta(hours=1):
            continue
        if tt.hour % 3 != 0:
            continue
        row = {'time': t[5:16]}
        cs, hs, ms, ls = [], [], [], []
        for m in om.MODELS.values():
            recs = fc3.get(m) or []
            r = recs[0] if recs else {}
            arr_c = r.get('cloud_cover') or []
            arr_h = r.get('cloud_cover_high') or []
            arr_m = r.get('cloud_cover_mid') or []
            arr_l = r.get('cloud_cover_low') or []
            if i < len(arr_c) and arr_c[i] is not None:
                cs.append(arr_c[i])
                hs.append(arr_h[i] or 0)
                ms.append(arr_m[i] or 0)
                ls.append(arr_l[i] or 0)
        if not cs:
            continue
        row.update({'total': round(_avg_valid(cs) or 0),
                    'high': round(_avg_valid(hs) or 0),
                    'mid': round(_avg_valid(ms) or 0),
                    'low': round(_avg_valid(ls) or 0)})
        table_48h.append(row)
        if len(table_48h) >= 17:
            break

    weather_3h = []
    aq_src = aq_merged if aq_merged else (aq3[0] if aq3 else {})
    for row in table_48h[:13]:
        i0 = times0.index(next(t for t in times0 if t[5:16] == row['time']))
        r = fc3[om.MODELS['ECMWF']][0]
        r_gfs = fc3[om.MODELS['GFS']][0] if fc3.get(om.MODELS['GFS']) else {}
        aod_now = _v(aq_src, 'aerosol_optical_depth', i0)
        weather_3h.append({
            'time': row['time'],
            'temp': _v(r, 'temperature_2m', i0),
            'precip': _v(r, 'precipitation', i0, 0.0),
            'wind': _v(r, 'wind_speed_10m', i0),
            'visibility': round((_v(r_gfs, 'visibility', i0) or 15000) / 1000, 1),
            'aod': round(aod_now, 2) if aod_now is not None else None,
        })

    # ---------------- 悬日月窗口云况可见性 ----------------
    _attach_align_visibility(alignment, fc3, moon)

    # 卫星临近校准已在批次 2 与反烧 fetch 并行执行（nowcast_data）

    # ---------------- 附近城市晚霞排行榜（格点评分 → 每城市取最优格点） ----------------
    ranking = []
    try:
        seen = set()
        for pt in sorted(map_points, key=lambda p: -(p.get('prob') or 0)):
            nm, _bd = citydb.nearest_city(pt['lat'], pt['lon'])
            if nm in seen:
                continue
            seen.add(nm)
            ranking.append({'name': nm, 'lat': pt['lat'], 'lon': pt['lon'],
                            'prob': pt.get('prob') or 0,
                            'quality': pt.get('quality') or 0,
                            'dist': pt.get('dist'), 'brg': pt.get('brg')})
            if len(ranking) >= 8:
                break
    except Exception as e:
        log.info('ranking skip: %s', e)

    # ---------------- 翻车风险 ----------------
    low_local_main = main.get('cloud', {}).get('low')
    risks = sc.risk_notes(None, None, None, low_local_main)
    # 说明（v2.26）：这里的 ens_spread 实为"ECMWF 成员中高云量的**均值**"，
    # 不是离散度——它参与 consensus 的 ens_pen 且已被 555 天标定吸收，
    # 故不改动数值口径；但文案必须说它真实的含义，否则会把"云多"
    # 讲成"模式分歧大"。真正的离散度是 ens_disp（P10~P90 半宽）。
    _src_txt = ('（含 ' + ' + '.join(ENS_SOURCE_LABELS.get(s, s)
                                    for s in ens_sources)
                + f' 共 {len(ens_sources)} 源）'
                if ens_sources and len(ens_sources) > 1 else '')
    if ens_spread is not None:
        _band_txt = (f'（全体成员 P10~P90 {ens_band["p10"]:.0f}~'
                     f'{ens_band["p90"]:.0f}%，半宽 ±{ens_disp:.0f}%）'
                     if ens_band and ens_band.get('p10') is not None
                     and ens_disp is not None else '')
        if ens_spread > 20:
            risks.insert(0, f'ECMWF 成员中高云量均值 {ens_spread:.0f}%{_src_txt}'
                            f'{_band_txt}，云况不确定，临近复核实况云图再出发')
        elif ens_spread < 10:
            risks.insert(0, f'ECMWF 成员中高云量均值仅 {ens_spread:.0f}%'
                            f'{_src_txt}{_band_txt}，云况确定性高')
    # 跨集合源分歧（v2.37）：各源均值的标准差，比"成员内离散"更能暴露
    # "各家看法不一致"——成员内离散只反映同一模式的初始场扰动。
    if ens_src_spread is not None and ens_src_spread >= 15:
        risks.insert(0, f'不同集合源分歧较大（各源均值相差 ±{ens_src_spread:.0f}%），'
                        '云量结论取保守值，临近再确认')
    # 总云量四源分歧（v2.38）：分层云量只有 ECMWF 系两家有成员，总云量才有
    # ECMWF/GEFS/ICON-EPS/UKMO 四家。四家各自的**源内**均值之间的离散，是
    # 「不同集合系统对今天云量总量看法差多少」的唯一量化——比单一源内部成员
    # 离散更能预警"这天的云量预报本身就不可靠"。
    if ens_total_src_spread is not None and ens_total_src_spread >= 15:
        _tsrc = (' + '.join(ENS_SOURCE_LABELS.get(s, s)
                            for s in (ens_total_sources or []))
                 if ens_total_sources else '')
        risks.insert(0, f'四个集合系统对总云量分歧较大'
                        f'（各源均值相差 ±{ens_total_src_spread:.0f}%，{_tsrc}），'
                        '总云量结论取保守值，临近再确认')

    # 扩展模式分歧（v2.37）：JMA/GEM/UKMO 不参与主概率（见 CALIB_MODELS），
    # 但它们整组偏离核心共识时是实打实的坏消息，必须说出来而不是藏起来。
    if ext_spread is not None and ext_spread >= 15:
        risks.insert(0, f'扩展模式（%s）与核心模式分歧较大（相差 ±{ext_spread:.0f} 分），'
                        '主概率仍按已标定的核心模式给出，建议临近复核'
                     % ' / '.join(m for m in om.MODELS
                                  if _ext_off.get(m, 0) >= 15))

    # 有数据源没取到：如实放在风险提示最前面，别让人以为是完整结论
    if _missing:
        risks.insert(0, '数据源缺失（%s），本次结论可信度下降，建议稍后重试'
                     % '、'.join(DATA_SOURCE_NAMES.get(m, m)
                                 for m in sorted(set(_missing))))

    # ---------------- 地形微气候提示 + site_info ----------------
    mc_notes = []
    if site_info and site_info.get('ok'):
        try:
            try:
                from app.services import terrain as terrain_svc
            except ImportError:  # app/ 根运行
                from services import terrain as terrain_svc  # type: ignore
            mc_notes = terrain_svc.microclimate_notes(
                site_info.get('elevation'),
                humidity=(main.get('weather') or {}).get('humidity'),
                wind=(main.get('weather') or {}).get('wind'),
                coastal=site_info.get('coastal'),
                low_cloud=(main.get('cloud') or {}).get('low'))
            site_info['bortle_desc'] = terrain_svc.bortle_desc(
                site_info.get('bortle'))
        except Exception as e:
            log.info('microclimate skip: %s', e)
        site_info['microclimate'] = mc_notes
        if mc_notes:
            risks.extend(mc_notes)

    # ---------------- 日月 24h 高度角轨迹（30 分钟步长） ----------------
    trk = _tracks(date, lat, lon)

    # ---------------- 未来 14 天概率概览 ----------------
    week = []
    try:
        # 光路云量：fc7 已在批次 1 并行拉取（14 天，17 点 × 4 变量 × 7 模式）
        for off in range(0, OUTLOOK_DAYS):
            day = (date + timedelta(days=off)).date()
            ds = day.isoformat()
            try:
                st7 = astro.sun_times(datetime(day.year, day.month, day.day),
                                      lat, lon, TZ_OFF)
                ss = st7.get('sunset')
                if not ss:
                    continue
                hh = ss.hour
                if kind == 'sunrise':
                    ss = st7.get('sunrise')
                    hh = ss.hour if ss else 6
                dims_d = {}
                low_d, mid_d, high_d = [], [], []
                for m in om.MODELS.values():
                    recs = fc7.get(m) or []
                    if len(recs) < len(PATH_DISTANCES):
                        continue
                    pm_l, pm_m, pm_h, pm_pp = [], [], [], []
                    target_key = f'{ds}T{hh:02d}:00'
                    for j in range(len(PATH_DISTANCES)):
                        r = recs[j]
                        t_arr = r.get('time') or []
                        ij = (t_arr.index(target_key) if target_key in t_arr
                              else om.hour_index(
                                  [t for t in t_arr if t.startswith(ds)], hh))
                        if ij is None or ij < 0:
                            continue
                        pm_l.append(_v(r, 'cloud_cover_low', ij, 0.0))
                        pm_m.append(_v(r, 'cloud_cover_mid', ij, 0.0))
                        pm_h.append(_v(r, 'cloud_cover_high', ij, 0.0))
                        pm_pp.append(_v(r, 'precipitation_probability', ij, 0.0))
                    low_d.append(_avg_valid(pm_l) or 0)
                    mid_d.append(_avg_valid(pm_m) or 0)
                    high_d.append(_avg_valid(pm_h) or 0)
                if not mid_d:
                    continue
                lo_m = _avg_valid(low_d) or 0
                mi_m = _avg_valid(mid_d) or 0
                hi_m = _avg_valid(high_d) or 0
                dims_d = {
                    'dim1': sc.dim1_midhigh_canvas(mi_m, hi_m, lo_m),
                    'dim2': 50,
                    'dim3': sc.dim3_precip(0, 0, 0),
                    'dim4': 70,
                    'dim5': 70,
                    'dim6': 80,
                    'dim7': 70,
                    'dim8': 50,
                }
                dead_d = (lo_m + mi_m + hi_m) <= 5
                total_d = sc.combine(dims_d, feasible=True, dead_sky=dead_d)
                pd7 = sc.calibrated_probability(
                    total_d if not dead_d else min(total_d, 8), region)
                week.append({'date': ds, 'weekday': '周' + '一二三四五六日'[
                    day.weekday()],
                    'prob': round(pd7),
                    'verdict': sc.verdict_word(pd7, region),
                    'canvas': round(mi_m + hi_m)})
            except Exception as e:
                log.info('week skip %s %s', ds, str(e)[:50])
    except Exception as e:
        log.info('week overview skip: %s', e)

    # 额度降级标注（v2.34）：任一上游请求在额度用尽/冷却时用过期缓存兜底，
    # 如实写进 meta（前端在 Hero 提示行展示）
    _deg = om.degraded_since(_t0)
    bundle = {
        'meta': {
            'location': {'lat': lat, 'lon': lon},
            'date': date_str, 'type': kind, 'version': APP_VERSION,
            'region': region,
            'generated_at': datetime.now().strftime('%Y-%m-%d %H:%M'),
            'line_speed': round(line_speed, 1),
            **({'degraded': '上游额度用尽，部分数据为 %d 小时前缓存'
                            % ((_deg + 59) // 60)} if _deg else {}),
        },        'astronomy': {
            'sunrise': sun['sunrise'].strftime('%H:%M') if sun['sunrise'] else None,
            'sunrise_az': sun.get('sunrise_az'),
            'sunset': sun['sunset'].strftime('%H:%M') if sun['sunset'] else None,
            'sunset_az': sun.get('sunset_az'),
            'moonrise': moon.get('moonrise').strftime('%H:%M')
            if moon.get('moonrise') else None,
            'moonrise_az': moon.get('moonrise_az'),
            'moonset': moon.get('moonset').strftime('%H:%M')
            if moon.get('moonset') else None,
            'moonset_az': moon.get('moonset_az'),
            'moon_phase': moon.get('moon_phase'),
            'moon_illum': moon.get('moon_illum'),
            'moon_phase_angle': moon.get('moon_phase_angle'),
            'moon_waxing': (moon.get('moon_phase_angle') or 0) < 180,
            'blue_evening': [sun['blue_evening_start'].strftime('%H:%M')
                             if sun.get('blue_evening_start') else None,
                             sun['blue_evening_end'].strftime('%H:%M')
                             if sun.get('blue_evening_end') else None],
            'blue_morning': [sun['blue_morning_start'].strftime('%H:%M')
                             if sun.get('blue_morning_start') else None,
                             sun['blue_morning_end'].strftime('%H:%M')
                             if sun.get('blue_morning_end') else None],
            'key_time': key_time.strftime('%H:%M'),
            'key_az': key_az,
        },
        'probability': probability,
        'data_missing': sorted(set(_missing)),   # 取数降级时如实标注（空=完整）
        'verdict': sc.verdict_word(probability, region),
        'credibility': cons['credibility'],
        'ens_spread': ens_spread,
        'ens_band': ens_band,
        'ens_sources': ens_sources,          # 参与集合的源（多源时 >1）
        'ens_src_spread': ens_src_spread,    # 跨源均值标准差（跨模式分歧）
        'ens_total_sources': ens_total_sources,        # 总云量四源（v2.38）
        'ens_total_src_spread': ens_total_src_spread,  # 总云量跨源分歧（%）
        'ext_spread': ext_spread,            # 扩展模式整组 vs 核心共识的偏离（分）
        'best_window': best_window,
        'main': main,
        'main_model': main_name,
        'models': model_results,
        'dims_detail': [{'key': k, 'name': sc.DIM_NAMES[k],
                         'weight': round(sc.WEIGHTS[k] * 100),
                         'score': main.get('dims', {}).get(k)}
                        for k in ['dim1', 'dim2', 'dim3', 'dim4',
                                  'dim5', 'dim6', 'dim7', 'dim8']],
        'risks': risks,
        'cross_section': {
            'x_labels': x_labels, 'x_dist': x_dist,
            'levels': [{'name': lv, 'height_km': PLEVEL_HEIGHTS[lv]}
                       for lv in levels_order],
            'rh': heat_rh,
            'timeline': cs_timeline,
            'cloud_bands': {
                'low': main.get('cloud', {}).get('low'),
                'mid': main.get('cloud', {}).get('mid'),
                'high': main.get('cloud', {}).get('high'),
            },
        },
        'reverse_burn': {'score': rev_score,
                         'az': round(anti_az),
                         'lit_high': [round(a, 1) for a in anti_high_eff]},
        'illumination': _illum_payload(_main_il, key_time),
        'map': {'points': map_points, 'sun_az': key_az,
                'observer': {'lat': lat, 'lon': lon},
                'probability': probability,
                'geometry': {
                    'line_speed': round(line_speed, 1),
                    'eq_base_km': main.get('eq_base_km'),
                    'glow_band_km': round(geo.glow_chord_km(
                        main.get('eq_base_km') or 2.0), 1),
                    't_start_min': main.get('t_start_min'),
                    't_end_min': main.get('t_end_min'),
                    'kind': kind,
                }},
        'pm25': pm_points,
        'table_48h': table_48h,
        'weather_3h': weather_3h,
        'alignment': alignment,
        'alignment_meta': alignment_meta,
        'nowcast': nowcast_data,
        'tracks': trk,
        'site_info': site_info,
        'ranking': ranking,
        'hourly_clouds': _hourly_clouds(fc3, alignment, date_str),
        'reason': _one_line_reason(probability, main, ens_spread,
                                   nowcast_data, region),
        'path_analysis': _path_analysis(heat_rh, x_dist, main, x_labels),
        'week': week,
    }
    return _log_and_attach(bundle)


def _refraction_deg(alt_deg: float) -> float:
    # Bennett 大气折射（度）：把几何高度角换算为折射量
    if alt_deg is None or alt_deg < -1.0:
        return 0.0
    alt = min(89.9, max(-1.0, alt_deg))
    r_arcmin = 1.02 / math.tan(math.radians(alt + 10.3 / (alt + 5.11)))
    return round(r_arcmin / 60.0, 3)


def _twilight_phase(sun_alt: float) -> str:
    if sun_alt is None:
        return '未知'
    if sun_alt > 0:
        return '白天'
    if sun_alt > -6:
        return '民用暮光'
    if sun_alt > -12:
        return '航海暮光'
    if sun_alt > -18:
        return '天文暮光'
    return '夜'


def _cloud_at_hour(fc: dict, date_str: str, hh: int):
    # ECMWF 指定日期/小时的云量；缺失返回 None
    recs = fc.get(om.MODELS['ECMWF']) or []
    r0 = recs[0] if recs else {}
    ij = om.hour_index(r0.get('time'), hh, date_str)
    if ij is None or ij < 0:
        return None
    return {'low': _v(r0, 'cloud_cover_low', ij, 0.0),
            'mid': _v(r0, 'cloud_cover_mid', ij, 0.0),
            'high': _v(r0, 'cloud_cover_high', ij, 0.0)}


def _vis_at_hour(fc: dict, date_str: str, hh: int):
    # 能见度：优先 GFS（ECMWF 常缺该变量）
    for mid in (om.MODELS['GFS'], om.MODELS['ECMWF']):
        recs = fc.get(mid) or []
        r0 = recs[0] if recs else {}
        ij = om.hour_index(r0.get('time'), hh, date_str)
        v = _v(r0, 'visibility', ij)
        if v:
            return v
    return None


def _align_models(fc: dict, date_str: str, hh: int):
    # 多模式在事件小时的云量/可见概率 + 共识
    out = {}
    for name, mid in om.MODELS.items():
        recs = fc.get(mid) or []
        r0 = recs[0] if recs else {}
        ij = om.hour_index(r0.get('time'), hh, date_str)
        if ij is None or ij < 0:
            continue
        lo = _v(r0, 'cloud_cover_low', ij, 0.0)
        mi = _v(r0, 'cloud_cover_mid', ij, 0.0)
        hi = _v(r0, 'cloud_cover_high', ij, 0.0)
        vis = max(0.0, 100.0 - (lo + 0.45 * mi + 0.2 * hi) * 1.05)
        out[name] = {'cloud': {'low': round(lo), 'mid': round(mi),
                               'high': round(hi)},
                     'vis_prob': round(vis)}
    probs = [v['vis_prob'] for v in out.values()]
    if probs:
        mu = sum(probs) / len(probs)
        std = (sum((x - mu) ** 2 for x in probs) / len(probs)) ** 0.5
    else:
        mu, std = 0.0, 0.0
    cred = ('高 · 多模式高度一致' if std < 8 else
            '中高 · 模式基本一致' if std < 15 else
            '中 · 模式存在分歧' if std < 25 else '低 · 模式分歧大')
    return out, {'vis_prob': round(mu), 'std': round(std, 1),
                 'credibility': cred}


def _sight_risk_rows(bands: dict) -> tuple:
    """视线分层结果 -> (风险列表, 依据列表)"""
    names = {'low': '低云', 'mid': '中云', 'high': '高云'}
    risks, reasons = [], []
    for b in ('low', 'mid', 'high'):
        bd = bands.get(b)
        if not bd:
            continue
        label = (f"{names[b]}({bd['gh_m'] / 1000:.1f}km)"
                 if bd.get('gh_m') else names[b])
        d = bd.get('d_km')
        dist_txt = f"交点 {d:.0f}km 处" if d is not None else "交点距离未知"
        occ = bd.get('occ')
        if occ is None:
            continue
        if occ >= 70:
            risks.append(f"{label}{dist_txt}云量 {occ:.0f}%，会挡住天体")
        elif occ >= 40:
            risks.append(f"{label}{dist_txt}云量 {occ:.0f}%，薄云削弱对比度")
        else:
            reasons.append(f"{label}{dist_txt}云量仅 {occ:.0f}%")
    return risks, reasons


def _sight_bands_for(cam_rec: dict, path_recs: list, hh: int, ds: str,
                     levels: list, observer_height_m: float = 0.0) -> dict:
    """按 levels 的交点距离，在指定模型上读取各层云量 -> 分档透射率。
    优先远端采样点云量，缺测回退该模型机位云量。
    用于 ECMWF 主判 + GFS 交叉验证（同一套几何，只换云量数据源）。
    返回 {'bands', 'trans', 'vis_prob'}；无有效数据返回 None。"""
    bands = {}
    for lv in levels:
        p, band, gh = lv['level'], lv['band'], lv['gh_m']
        if not lv.get('d_km'):
            continue
        si = min(range(len(SIGHT_DISTANCES_KM)),
                 key=lambda k: abs(SIGHT_DISTANCES_KM[k] - lv['d_km']))
        prec = path_recs[si] if si < len(path_recs) else {}
        ij_pt = om.hour_index(prec.get('time'), hh, ds) if prec else None
        cc = _v(prec, f'cloudcover_{p}hPa', ij_pt) if ij_pt is not None else None
        if cc is None:
            ij_cam = om.hour_index(cam_rec.get('time'), hh, ds)
            cc = _v(cam_rec, f'cloudcover_{p}hPa', ij_cam)
        if cc is None:
            continue
        cur = bands.get(band)
        if cur is None or cc > cur['occ']:
            env = sight.twilight_envelopes_km(gh, observer_height_m)
            bands[band] = {'occ': round(cc, 1), 'gh_m': gh, 'd_km': lv['d_km'],
                           'level': p,
                           'env_km': [round(env[0]), round(env[2])]
                           if env else None}
    if not bands:
        return None
    trans = sight.occlusion_transmission(
        [bands[b]['occ'] for b in ('low', 'mid', 'high') if b in bands])
    return {'bands': bands, 'trans': round(trans, 3),
            'vis_prob': round(100.0 * trans)}


def _window_sight(w: dict, cam_rec: dict, path_recs: list, aq_path: list,
                  elev0_m: float, gfs_cam: dict = None,
                  gfs_path: list = None, extra: dict = None) -> dict:
    """对准窗口的视线遮挡评估：
    1) 视高度角（几何+折射）下逐气压层求视线-云壳交点距离；
    2) 在交点最近的远端采样点读取该层云量（ECMWF 气压层云量）；
    3) 分档合成透射率 -> 可见概率；GFS 同几何交叉验证；给出光路 AOD/能见度。
    elev0_m 为机位海拔：抬高后遮蔽云距收缩、可见地平线下沉。
    天气数据缺失时返回 None。"""
    ds = w.get('date')
    hh = int((w.get('best_time') or '00:00')[:2])
    ij = om.hour_index(cam_rec.get('time'), hh, ds)
    if ij is None or ij < 0:
        return None
    theta_geo = w.get('best_alt', 0) or 0
    theta_app = theta_geo + _refraction_deg(theta_geo)
    body = w.get('body')
    limb = None
    if body == 'moon':
        try:
            _, _, dist, _, _ = astro.moon_position(w['jd_best'])
            limb = sight.moon_upper_limb_alt(theta_app, dist)
        except Exception:
            limb = None
    else:
        limb = theta_app + 0.266        # 太阳上缘（视半径）
    # 可见性门控：机位抬高后可见地平线下沉 -dip，天体上缘高于该角即已露出地面
    dip = sight.horizon_dip_deg(elev0_m)
    visible = limb is not None and limb >= -dip
    if not visible:
        return {'visible': False, 'apparent_alt': round(theta_app, 2),
                'limb_alt': round(limb, 2) if limb is not None else None,
                'dip_deg': round(dip, 3), 'observer_height_m': round(elev0_m),
                'note': '天体上缘在地平线下，无遮挡评估'}

    levels = []
    for p in SIGHT_LEVELS:
        gh = _v(cam_rec, f'geopotential_height_{p}hPa', ij)
        if gh is None or gh <= elev0_m + 30 or gh > 20000:
            continue
        agl = gh - elev0_m
        d = sight.sight_intersection_km(gh, theta_app, elev0_m)
        if d is None:
            continue
        # 交点最近的远端采样点
        si = min(range(len(SIGHT_DISTANCES_KM)),
                 key=lambda k: abs(SIGHT_DISTANCES_KM[k] - d))
        sample_km = SIGHT_DISTANCES_KM[si]
        gap = abs(sample_km - d) > max(60.0, 0.5 * d)
        prec = path_recs[si] if si < len(path_recs) else {}
        ij_pt = om.hour_index(prec.get('time'), hh, ds)
        cc_pt = _v(prec, f'cloudcover_{p}hPa', ij_pt)
        cc_cam = _v(cam_rec, f'cloudcover_{p}hPa', ij)
        cc, src = (cc_pt, 'path') if cc_pt is not None else (cc_cam, 'camera')
        levels.append({'level': p, 'gh_m': round(gh), 'agl_m': round(agl),
                       'band': sight.band_of_height(agl),
                       'd_km': round(d, 1), 'sample_km': sample_km,
                       'cc': round(cc, 1) if cc is not None else None,
                       'cc_src': src, 'gap': gap})

    main_res = _sight_bands_for(cam_rec, path_recs, hh, ds, levels,
                                elev0_m)
    if not main_res:
        # 气压层云量缺测：不能当成无云给满分，标记缺口由上层回退机位云量
        return {'visible': True, 'apparent_alt': round(theta_app, 2),
                'geo_alt': round(theta_geo, 2),
                'limb_alt': round(limb, 2), 'levels': levels,
                'bands': {}, 'data_gap': True,
                'dip_deg': round(dip, 3), 'observer_height_m': round(elev0_m),
                'note': '气压层云量缺测，回退机位云量评估'}
    bands, trans = main_res['bands'], main_res['trans']
    vis_prob = main_res['vis_prob']

    # GFS 交叉验证：同一套交点几何，只换云量数据源（气压层 + 光路能见度）
    models_extra = {}
    if gfs_cam:
        g_res = _sight_bands_for(gfs_cam, (gfs_path or []), hh, ds, levels,
                                 elev0_m)
        if g_res:
            models_extra['GFS'] = {'vis_prob': g_res['vis_prob'],
                                   'bands': {b: v['occ']
                                             for b, v in g_res['bands'].items()}}
    # 更多模式（v2.37）：同一几何换第三家数据源，model_spread 才不是两家之差
    for _mn, (_mcam, _mpath) in (extra or {}).items():
        if not _mcam or _mn in models_extra:
            continue
        _r = _sight_bands_for(_mcam, (_mpath or []), hh, ds, levels, elev0_m)
        if _r:
            models_extra[_mn] = {'vis_prob': _r['vis_prob'],
                                 'bands': {b: v['occ']
                                           for b, v in _r['bands'].items()}}
    models_extra['ECMWF'] = {'vis_prob': vis_prob,
                             'bands': {b: v['occ'] for b, v in bands.items()}}
    probs = [v['vis_prob'] for v in models_extra.values()]
    spread = round(max(probs) - min(probs)) if len(probs) > 1 else None

    # 光路 AOD（CAMS）/ 能见度（GFS，ECMWF 无该变量）：远端采样均值
    aods, viss = [], []
    for arec in (aq_path or []):
        ai = om.hour_index(arec.get('time'), hh, ds) if arec else None
        v = _v(arec, 'aerosol_optical_depth', ai) if ai is not None else None
        if v is not None:
            aods.append(v)
    for i, prec in enumerate(gfs_path or []):
        ij_pt = om.hour_index(prec.get('time'), hh, ds) if prec else None
        v = _v(prec, 'visibility', ij_pt) if ij_pt is not None else None
        if v:
            # 近端采样点（<25km）代表机位附近能见度，不计入"光路"均值
            if i < 3:
                continue
            viss.append(v / 1000.0)
    aod_path = round(sum(aods) / len(aods), 2) if aods else None
    vis_path = round(sum(viss) / len(viss), 1) if viss else None

    return {'visible': True, 'apparent_alt': round(theta_app, 2),
            'geo_alt': round(theta_geo, 2),
            'limb_alt': round(limb, 2),
            'dip_deg': round(dip, 3), 'observer_height_m': round(elev0_m),
            'levels': levels, 'bands': bands,
            'trans': round(trans, 3), 'vis_prob': vis_prob,
            'path_aod': aod_path, 'vis_path_km': vis_path,
            'models': models_extra, 'model_spread': spread}


def _align_geometry(body: str, lat: float, lon: float, w: dict,
                    moon: dict, fov_h: float = None) -> dict:
    # 事件时刻的速率/角尺寸/折射/取景框停留时间/天空背景
    # fov_h：用户设定的水平视场角（全画幅等效）。"框内停留"必须按**这个**视场算
    # ——原来写死 6°×4°，于是 840mm（2.46°）的机位会拿到 2.5 倍偏长的停留时间。
    jd = w['jd_best']
    alt0, az0 = astro._body_altaz(body, lat, lon, jd)
    alt_m = astro._body_altaz(body, lat, lon, jd - 1 / 1440.0)[0]
    alt_p = astro._body_altaz(body, lat, lon, jd + 1 / 1440.0)[0]
    az_m = astro._body_altaz(body, lat, lon, jd - 1 / 1440.0)[1]
    az_p = astro._body_altaz(body, lat, lon, jd + 1 / 1440.0)[1]
    alt_rate = (alt_p - alt_m) / 2.0
    az_rate = (((az_p - az_m + 180) % 360) - 180) / 2.0
    if body == 'sun':
        ang = 0.533
    else:
        _, _, dist, _, _ = astro.moon_position(jd)
        ang = 2.0 * math.degrees(math.atan(1737.4 / max(1.0, dist)))
    refr = _refraction_deg(alt0)
    fh = float(fov_h) if (fov_h and fov_h > 0.05) else 6.0
    fv = fh * 2.0 / 3.0                    # 画幅 3:2（与预览画布一致）
    tx = fh / abs(az_rate) if abs(az_rate) > 1e-6 else 999.0
    ty = fv / abs(alt_rate) if abs(alt_rate) > 1e-6 else 999.0
    sun_alt = astro._body_altaz('sun', lat, lon, jd)[0]
    return {
        'alt_rate': round(alt_rate, 3), 'az_rate': round(az_rate, 3),
        'angular_size_deg': round(ang, 2),
        'refraction_deg': refr,
        'apparent_alt': round(alt0 + refr, 2),
        'time_in_frame_min': round(min(tx, ty, 999.0), 1),
        'sky': {'sun_alt': round(sun_alt, 1),
                'twilight': _twilight_phase(sun_alt),
                'moon_illum': moon.get('moon_illum'),
                'moon_phase': moon.get('moon_phase')},
    }


def _weather_feasibility(cloud: dict, aod, pm25, vis_km, precip_prob,
                         consensus, sight: dict = None,
                         aod_path=None, loc: str = '机位') -> dict:
    # 天气可行性（仅天气，不含机位/几何）：
    # 云况 = 视线交点遮挡（sight）优先，回退机位三点云量；
    # 通透度 = 本地/光路 AOD 较差者 + PM2.5 + 能见度
    lo = (cloud or {}).get('low', 0) or 0
    mi = (cloud or {}).get('mid', 0) or 0
    hi = (cloud or {}).get('high', 0) or 0
    if sight and sight.get('visible') and sight.get('bands') is not None:
        cloud_score = 100.0 * (sight.get('trans') or 0.0)
        cloud_basis = '视线交点'
    else:
        cloud_score = max(0.0, 100.0 - (lo * 1.15 + mi * 0.5 + hi * 0.2))
        cloud_basis = '机位云量'
    aod_eff = max(x for x in (aod, aod_path) if x is not None) \
        if (aod is not None or aod_path is not None) else 0.2
    trans = 100.0 - aod_eff * 90.0 - (pm25 or 0) * 0.35
    if vis_km:
        trans = min(trans, min(100.0, vis_km * 6.0))
    trans = max(0.0, min(100.0, trans))
    precip = max(0.0, 100.0 - (precip_prob or 0) * 1.3)
    std = (consensus or {}).get('std', 20.0) or 0
    stability = max(0.0, min(100.0, 100.0 - std * 2.0))
    total = 0.50 * cloud_score + 0.22 * trans + 0.13 * precip + 0.15 * stability
    verdict = ('通透可拍' if total >= 78 else '薄云可拍' if total >= 58 else
               '云层遮挡' if total >= 38 else '不建议')
    risks = []
    reasons = []
    if sight and sight.get('visible'):
        s_risks, s_reasons = _sight_risk_rows(sight.get('bands') or {})
        risks.extend(s_risks)
        reasons.extend(s_reasons)
        spread = sight.get('model_spread')
        if spread is not None and spread >= 40:
            risks.append(f'视线云况模式分歧大（EC/GFS 可见概率差 {spread:.0f}%）')
    if lo >= 60:
        risks.append(f'{loc}低云 {lo:.0f}%（当地天气/出行参考）')
    elif lo >= 35:
        risks.append(f'{loc}低云 {lo:.0f}% 有一定遮挡风险')
    if mi >= 70:
        risks.append(f'{loc}中云 {mi:.0f}% 较厚')
    if hi >= 85:
        risks.append(f'{loc}高云 {hi:.0f}% 近乎全覆盖')
    if (precip_prob or 0) >= 50:
        risks.append(f'降水概率 {precip_prob:.0f}%')
    if (aod_eff or 0) >= 0.5:
        risks.append(f'AOD {aod_eff} 浑浊，对比度下降')
    if (pm25 or 0) >= 75:
        risks.append(f'PM2.5 {pm25:.0f} μg/m³ 偏高')
    if std >= 20:
        risks.append(f'模式分歧大（离散度 ±{std:.0f}%）')
    if cloud_score >= 70:
        reasons.append('视线交点云况好' if cloud_basis == '视线交点'
                       else '云量条件好')
    if trans >= 70:
        reasons.append('大气通透')
    if precip >= 80:
        reasons.append('降水风险低')
    if stability >= 75:
        reasons.append('多模式一致')
    if not reasons:
        reasons.append('天气条件一般，建议临近再看')
    return {'score': round(total), 'verdict': verdict,
            'dims': {'cloud': round(cloud_score),
                     'transparency': round(trans),
                     'precip': round(precip),
                     'stability': round(stability)},
            'cloud_basis': cloud_basis,
            'reasons': reasons, 'risks': risks}


def _event_weather(fc: dict, aq0: dict, r0: dict, ds: str, ev,
                   dir_data: dict = None):
    # 某天某事件时刻的天气：云量/通透度/降水/稳定性 + 可行性评分。
    # dir_data 提供沿事件方位远端采样（低/中/高各自代表云距）的云量与 AOD，
    # 为空时回退机位单点。
    if not ev:
        return None
    hh = ev.hour
    cloud = _cloud_at_hour(fc, ds, hh)
    cam_cloud = dict(cloud) if cloud else None
    if dir_data and dir_data.get('cloud'):
        cloud = dir_data['cloud']
    if not cloud:
        return None
    models, cons = _align_models(fc, ds, hh)
    ai = om.hour_index(aq0.get('time'), hh, ds)
    aod = _v(aq0, 'aerosol_optical_depth', ai, 0.2)
    if dir_data and dir_data.get('aod') is not None:
        aod = dir_data['aod']
    pm25 = _v(aq0, 'pm2_5', ai)
    vis_m = _vis_at_hour(fc, ds, hh)
    ij = om.hour_index(r0.get('time'), hh, ds)
    pprob = _v(r0, 'precipitation_probability', ij)
    vis_prob = round(max(0.0, 100.0 - (
        cloud['low'] + 0.45 * cloud['mid']
        + 0.2 * cloud['high']) * 1.05))
    feas = _weather_feasibility(cloud, aod, pm25,
                                (vis_m / 1000.0) if vis_m else None,
                                pprob, cons,
                                loc='方向' if dir_data else '机位')
    out = {'time': ev.strftime('%H:%M'),
           'cloud': {k: round(v) for k, v in cloud.items()},
           'vis_prob': vis_prob,
           'aod': round(aod, 2) if aod is not None else None,
           'pm25': pm25, 'precip_prob': pprob,
           'temp': _v(r0, 'temperature_2m', ij),
           'humidity': _v(r0, 'relative_humidity_2m', ij),
           'wind': _v(r0, 'wind_speed_10m', ij),
           'pressure': _v(r0, 'pressure_msl', ij),
           'visibility_km': round(vis_m / 1000.0, 1) if vis_m else None,
           'score': feas['score'], 'verdict': feas['verdict'],
           'dims': feas['dims'], 'reasons': feas['reasons'],
           'risks': feas['risks'], 'models': models, 'consensus': cons}
    if dir_data:
        out['dir_sampled'] = True
        out['dir_distances_km'] = dir_data.get('distances') or []
        out['dir_azimuth'] = dir_data.get('azimuth')
        out['cam_cloud'] = ({k: round(v) for k, v in cam_cloud.items()}
                            if cam_cloud else None)
        # 多模式对比改用「沿方位采样点」云量，与上面的判定同口径；
        # 各模式可见概率与机位口径分别给出，避免数字互相矛盾。
        dm = dir_data.get('models') or {}
        if dm:
            cam_probs = [v.get('vis_prob') for v in (models or {}).values()
                         if v.get('vis_prob') is not None]
            out['models_cam'] = models
            out['models'] = dm
            probs = [v['vis_prob'] for v in dm.values()]
            mu = sum(probs) / len(probs)
            std = (sum((p - mu) ** 2 for p in probs) / len(probs)) ** 0.5
            out['consensus'] = {
                'vis_prob': round(mu), 'std': round(std, 1),
                'credibility': ('高 · 多模式高度一致' if std < 8 else
                                '中高 · 模式基本一致' if std < 15 else
                                '中 · 模式存在分歧' if std < 25 else
                                '低 · 模式分歧大'),
                'basis': '方向采样'}
            if cam_probs:
                out['consensus']['vis_prob_cam'] = round(
                    sum(cam_probs) / len(cam_probs))
    return out


def _align_week(lat: float, lon: float, date: datetime, target_azimuth: float,
                tol: float, alt_lo: float, alt_hi: float, fc: dict,
                days: int = 7) -> list:
    # 未来 N 天窗口概览（0-2 天带预报判定，其余仅几何）
    out = []
    for off in range(days):
        day = date + timedelta(days=off)
        ds = day.strftime('%Y-%m-%d')
        try:
            st = astro.sun_times(day, lat, lon, TZ_OFF)
            wins, _ = _build_alignment_windows(
                lat, lon, st, target_azimuth, tol, alt_hi,
                alt_lo=alt_lo)
        except Exception:
            wins = []
        entry = {'date': ds,
                 'weekday': '周' + '一二三四五六日'[day.weekday()],
                 'windows': {}}
        for w in wins:
            key = w['period']
            cur = entry['windows'].get(key)
            if cur is not None and cur['best_alt'] <= w['best_alt']:
                continue
            item = {'best_time': w['best_time'], 'best_alt': w['best_alt'],
                    'period_name': w['period_name']}
            if off <= 6:
                cloud = _cloud_at_hour(fc, ds, int(w['best_time'][:2]))
                if cloud:
                    vis = round(max(0.0, 100.0 - (
                        cloud['low'] + 0.45 * cloud['mid']
                        + 0.2 * cloud['high']) * 1.05))
                    item['vis_prob'] = vis
                    item['verdict'] = ('通透可拍' if vis >= 70 else
                                       '薄云可拍' if vis >= 40 else '云层遮挡')
                else:
                    item['verdict'] = '无预报'
            else:
                item['verdict'] = '超出预报窗口'
            entry['windows'][key] = item
        out.append(entry)
    return out


def _aq_range(rng: str):
    """空气质量接口的预报范围比天气接口短（实测仅到今日+6，超出返回 400）。
    这里把区间末端钳制到可用上限；起点已超出时返回 None（跳过该请求）。
    失败时退回原区间，交由调用方兜底。"""
    try:
        parts = rng.split(',')
        start_s, end_s = parts[0], (parts[1] if len(parts) > 1 else parts[0])
        s = datetime.strptime(start_s, '%Y-%m-%d').date()
        e = datetime.strptime(end_s, '%Y-%m-%d').date()
    except Exception:
        return rng
    hi = datetime.now().date() + timedelta(days=6)
    if s > hi:
        return None
    if e > hi:
        return f"{start_s},{hi.isoformat()}"
    return rng


def _event_sight(body: str, ev, az: float, recs: dict,
                 elev0_m: float, aq_path=None):
    """把当日升落事件当作"伪窗口"做视线遮挡评估，复用 _window_sight 的全部几何。

    body: 'sun'|'moon'；ev: 事件本地时间；az: 事件方位（仅用于回填标注）。
    recs: {模式名: [采样记录]}——每条列表的第一个是机位点、其余沿方位。
    太阳取视地平线标准几何高度角 -0.833°（上缘恰好贴地平线，含折射后上缘略高于
    0°，机位抬高时可见地平线继续下沉）；月亮取 +0.125°（含视差与折射）。
    这样无对准窗口时也能按真实气压层给出逐层云量、交点距离与可见概率。
    """
    alt_geo = -0.833 if body == 'sun' else 0.125
    try:
        jd = astro.jd_from_datetime(ev) - TZ_OFF / 24.0
    except Exception:
        return None
    w = {'date': ev.strftime('%Y-%m-%d'), 'best_time': ev.strftime('%H:%M'),
         'best_alt': alt_geo, 'body': body, 'jd_best': jd}
    _ec = recs.get('ECMWF') or []
    _gf = recs.get('GFS') or []
    _extra = {k: (v[0], v[1:]) for k, v in recs.items()
              if k not in ('ECMWF', 'GFS') and v}
    s = _window_sight(w, _ec[0] if _ec else {}, _ec[1:], aq_path or [], elev0_m,
                      gfs_cam=(_gf[0] if _gf else None),
                      gfs_path=_gf[1:], extra=_extra)
    if s:
        s['event_az'] = round(az, 1)
        s['event_alt_geo'] = alt_geo
    return s


def _day_events(lat: float, lon: float, date: datetime, target_azimuth: float,
                tol: float, alt_lo: float, alt_hi: float) -> list:
    """当日四个升落事件与目标方位的差距（回答"这天能不能拍、差多少"）。
    alt 取各事件的视地平线标准值（太阳 -0.833°、月亮 +0.125°）。"""
    try:
        st = astro.sun_times(date, lat, lon, TZ_OFF)
        mt = astro.moon_times(date, lat, lon, TZ_OFF)
    except Exception:
        return []
    specs = (('sunrise', '日出', st.get('sunrise'), st.get('sunrise_az'), -0.833),
             ('sunset', '日落', st.get('sunset'), st.get('sunset_az'), -0.833),
             ('moonrise', '月出', mt.get('moonrise'),
              mt.get('moonrise_az'), 0.125),
             ('moonset', '月落', mt.get('moonset'), mt.get('moonset_az'), 0.125))
    out = []
    for key, name, t, az, alt in specs:
        if t is None or az is None:
            continue
        daz = abs((az - target_azimuth + 180) % 360 - 180)
        out.append({'key': key, 'name': name, 'time': t.strftime('%H:%M'),
                    'az': round(az, 1), 'alt': alt, 'daz': round(daz, 2),
                    'in_range': bool(daz <= tol and alt_lo <= alt <= alt_hi)})
    return out


def build_alignment(lat: float, lon: float, date_str: str,
                    target_azimuth: float, aov: float = 3.0,
                    elev: float = None, tol: float = None,
                    alt_max: float = None, days: int = 1,
                    cam_height: float = None, target_height: float = None,
                    target_dist_km: float = None) -> dict:
    # 悬日月拍摄天气评估：机位（含高度）+ 目标方位/高度/视场
    # -> 所选日期的对准窗口 + 该方位的天气可行性与日月可见性
    # days 仅用于兼容多日扫描（默认 1：只评估所选日期，不做未来窗口搜索）
    # cam_height: 机位海拔 m（缺省取地形探测海拔）
    # target_height + target_dist_km: 目标高度 m 与水平距离 km -> 反推目标仰角
    # elev 显式给出时优先（巧摄 .pft 或手动覆盖）
    date = datetime.strptime(date_str, '%Y-%m-%d')
    _t0 = time.time()          # 额度降级检测起点（degraded_since）
    site_info, horizon_fn = _probe_site(lat, lon)
    # 机位海拔：显式 > 地形探测 > 0
    _site_elev = (site_info or {}).get('elevation')
    cam_h = cam_height if cam_height is not None else (_site_elev or 0.0)
    cam_h = max(0.0, min(9000.0, float(cam_h)))
    cam_dip = sight.horizon_dip_deg(cam_h)
    # 目标仰角：给定目标高度与距离则反推（几何 + 折射），否则用显式 elev
    target_elev_geo = None
    if target_height is not None and target_dist_km:
        target_elev_geo = sight.target_elevation_deg(
            cam_h, target_height, target_dist_km)
    if elev is not None:
        _elev, elev_src = float(elev), 'manual'
    elif target_elev_geo is not None:
        _elev, elev_src = target_elev_geo, 'target'
    else:
        _elev, elev_src = 0.0, 'default'
    elev = _elev
    _tol = tol if tol is not None else max(0.5, aov / 2.0 + 0.3)
    # 高度角下限：机位抬高后可见地平线下沉（-dip），贴地平线的天体仍可拍
    alt_lo = max(-1.0 - cam_dip, elev - 1.0)
    alt_hi = alt_max if alt_max is not None else min(45.0, elev + 8.0)
    sun = astro.sun_times(date, lat, lon, TZ_OFF, horizon_fn=horizon_fn)
    moon = astro.moon_times(date, lat, lon, TZ_OFF, horizon_fn=horizon_fn)
    trk = _tracks(date, lat, lon)

    # ---- 逐日搜索窗口（选定日期起 days 天）----
    alignment = []
    seen = set()
    for off in range(max(1, min(days, 60))):
        day = date + timedelta(days=off)
        try:
            st = astro.sun_times(day, lat, lon, TZ_OFF, horizon_fn=horizon_fn)
            wins, _ = _build_alignment_windows(
                lat, lon, st, target_azimuth, _tol, alt_hi,
                site_info=site_info, horizon_fn=horizon_fn, alt_lo=alt_lo)
        except Exception:
            wins = []
        for w in wins:
            key = (w.get('body'), w.get('date'), w.get('best_time'))
            if key in seen:
                continue
            seen.add(key)
            w['day_offset'] = off
            alignment.append(w)
    alignment.sort(key=lambda x: (x.get('date', ''), x.get('best_time', '')))

    alignment_meta = {'target_az': round(target_azimuth, 1),
                      'tol': round(_tol, 2), 'aov': round(aov, 2),
                      'elev': round(elev, 2), 'alt_lo': round(alt_lo, 2),
                      'alt_hi': round(alt_hi, 2), 'days': max(1, min(days, 60))}

    # 几何事实（纯天文，与天气无关；天气失败也保留）
    for w in alignment:
        try:
            w.update(_align_geometry(w['body'], lat, lon, w, moon, aov))
        except Exception as e:
            log.info('align geometry skip: %s', str(e)[:80])
        if w.get('body') == 'moon':
            w['illum'] = moon.get('moon_illum')

    # 已过/未来：窗口只从所选日期向后扫，当天早晨的事件在下午查询时其实
    # 已经过了。带上分钟数让前端能把"已过"的窗口让位给下一个未来窗口
    # （否则页面会用一条已经失效的窗口当结论）。
    _now = datetime.now()
    for w in alignment:
        try:
            _t_ev = datetime.strptime(
                f"{w['date']} {w.get('best_time') or '00:00'}", '%Y-%m-%d %H:%M')
            _mins = (_t_ev - _now).total_seconds() / 60.0
            w['minutes_to_event'] = int(round(_mins))
            w['is_past'] = _mins < 0
        except Exception:
            w['minutes_to_event'] = None
            w['is_past'] = None
    day_events_past = None
    try:
        _de_past = []
        for e in _day_events(lat, lon, date, target_azimuth, _tol, alt_lo, alt_hi):
            _t_ev = datetime.strptime(f"{date_str} {e.get('time')}", '%Y-%m-%d %H:%M')
            e['minutes_to_event'] = int(round((_t_ev - _now).total_seconds() / 60))
            e['is_past'] = e['minutes_to_event'] < 0
            _de_past.append(e)
        day_events_past = _de_past
    except Exception as e:
        log.info('day_events past skip: %s', str(e)[:80])

    hc = {'time': [], 'low': [], 'mid': [], 'high': []}
    hc_by_date = {}
    day_weather = {}
    outlook = []
    main = {'cloud': {}, 'weather': {}, 'aod_local': None, 'aod_path': None,
            'pm25': None, 'quality': None, 'models': {}, 'consensus': None}
    week = []
    sl_pts_meta = []
    _wf_err = None            # 天气块失败原因（用于如实提示）
    try:
        # 14 天预报区间（v2.37：7 → 14 天。Open-Meteo 计费在 ≤14 天时
        # lengthFactor 恒为 1——拉长窗口不额外计费，而窗口落在第 8~14 天时
        # 原来会退化成"只给几何、天气待临近"）
        rng = f"{date_str},{(date + timedelta(days=13)).strftime('%Y-%m-%d')}"
        fc = om.fetch_hourly([lat], [lon], HOUR_VARS,
                             list(om.MODELS.values()), rng, TZ)
        # AQ 预报范围更短：钳制区间，且失败只降级通透度维度、不拖垮整个天气块
        aq = []
        try:
            _aqr = _aq_range(rng)
            if _aqr:
                aq = om.fetch_aq([lat], [lon], AQ_VARS, _aqr, TZ)
        except Exception as e:
            log.info('aq skip: %s', str(e)[:120])
        aq0 = aq[0] if aq else {}
        recs = fc.get(om.MODELS['ECMWF']) or []
        r0 = recs[0] if recs else {}
        times = r0.get('time') or []
        idx = {t: i for i, t in enumerate(times)}
        for off in range(14):
            ds = (date + timedelta(days=off)).strftime('%Y-%m-%d')
            keep = [t for t in times if t.startswith(ds)]
            rec = {'time': keep, 'low': [], 'mid': [], 'high': []}
            for v in ('cloud_cover_low', 'cloud_cover_mid',
                      'cloud_cover_high'):
                arr = r0.get(v) or []
                rec[v[12:]] = [arr[idx[t]]
                               if (t in idx and idx[t] < len(arr)) else None
                               for t in keep]
            hc_by_date[ds] = rec
        # 默认云带用第一个窗口所在日期
        _hc_date = alignment[0]['date'] if alignment else date_str
        hc = hc_by_date.get(_hc_date, hc)

        # ---- 视线遮挡采样：机位 + 沿目标方位远端（ECMWF 气压层云量/层高）----
        # v2.37：请求区间只覆盖**窗口所在日期**——视线评估只用得到窗口那一个小时，
        # 而按 14 天区间拉 25 坐标 × 19 变量 × 3 模式会让上游多算 14 倍
        # （实测 40s → 单日 5s 级），额度与等待都白花。
        fc_sl = aq_sl = None
        _sl_dates = sorted({w.get('date') for w in alignment if w.get('date')})
        _sl_rng = (f'{_sl_dates[0]},{_sl_dates[-1]}' if _sl_dates else date_str)
        try:
            sl_coords = [(lat, lon)] + [
                citydb.destination_point(lat, lon, target_azimuth, d)
                for d in SIGHT_DISTANCES_KM]
            fc_sl = om.fetch_hourly([c[0] for c in sl_coords],
                                    [c[1] for c in sl_coords],
                                    SIGHT_LEVEL_VARS + ['visibility'],
                                    [om.MODELS[_m] for _m in SIGHT_MODELS],
                                    _sl_rng, TZ)
            aq_sl = om.fetch_aq([c[0] for c in sl_coords],
                                [c[1] for c in sl_coords],
                                ['aerosol_optical_depth'],
                                _aq_range(_sl_rng) or _sl_rng, TZ)
            sl_pts_meta = [{'lat': c[0], 'lon': c[1], 'dist_km': d}
                           for c, d in zip(sl_coords[1:], SIGHT_DISTANCES_KM)]
        except Exception as e:
            log.info('sight fetch skip: %s', str(e)[:120])
        elev0_m = cam_h

        for w in alignment:
            if w.get('day_offset', 99) > 13:
                # 超出预报窗口：只给几何，天气待临近
                w['cloud_at'] = None
                w['sight'] = None
                w['vis_prob'] = None
                w['feasibility'] = None
                w['risks'] = []
                w['reason'] = '超出预报窗口（临近再评估天气）'
                continue
            wdate = w.get('date')
            hh = int(w['best_time'][:2])
            cloud = _cloud_at_hour(fc, wdate, hh)
            if not cloud:
                w['cloud_at'] = None
                w['sight'] = None
                w['vis_prob'] = None
                w['feasibility'] = None
                w['risks'] = []
                w['reason'] = '预报数据缺失'
                continue
            w['cloud_at'] = {k: round(v) for k, v in cloud.items()}
            models, cons = _align_models(fc, wdate, hh)
            w['models'] = models
            w['consensus'] = cons
            ij = om.hour_index(r0.get('time'), hh, wdate)
            ai = om.hour_index(aq0.get('time'), hh, wdate)
            aod = _v(aq0, 'aerosol_optical_depth', ai, 0.2)
            pm25 = _v(aq0, 'pm2_5', ai)
            vis_m = _vis_at_hour(fc, wdate, hh)
            pprob = _v(r0, 'precipitation_probability', ij)
            # 视线遮挡评估：逐气压层求交 + 远端采样云量
            sinfo = None
            if fc_sl:
                try:
                    sl_recs = fc_sl.get(om.MODELS['ECMWF']) or []
                    gfs_recs = fc_sl.get(om.MODELS['GFS']) or []
                    _sl_extra = {}
                    for _m in SIGHT_MODELS:
                        if _m in ('ECMWF', 'GFS'):
                            continue
                        _mr = fc_sl.get(om.MODELS[_m]) or []
                        if _mr:
                            _sl_extra[_m] = (_mr[0], _mr[1:])
                    sinfo = _window_sight(
                        w, sl_recs[0] if sl_recs else {}, sl_recs[1:],
                        (aq_sl or [])[1:], elev0_m,
                        gfs_cam=(gfs_recs[0] if gfs_recs else None),
                        gfs_path=gfs_recs[1:], extra=_sl_extra)
                except Exception as e:
                    log.info('sight eval skip: %s', str(e)[:120])
            w['sight'] = sinfo
            if sinfo and sinfo.get('visible') and not sinfo.get('data_gap'):
                w['vis_prob'] = sinfo['vis_prob']
            else:
                w['vis_prob'] = round(max(0.0, 100.0 - (
                    cloud['low'] + 0.45 * cloud['mid']
                    + 0.2 * cloud['high']) * 1.05))
            vis_path = (sinfo or {}).get('vis_path_km')
            vis_opts = [v for v in ((vis_m / 1000.0) if vis_m else None,
                                    vis_path) if v]
            vis_km = min(vis_opts) if vis_opts else None
            feas = _weather_feasibility(
                cloud, aod, pm25, vis_km, pprob, cons, sight=sinfo,
                aod_path=(sinfo or {}).get('path_aod'))
            w['feasibility'] = feas
            w['risks'] = feas['risks']
            w['reason'] = '；'.join(feas['reasons'][:2])

        # main 取第一个有天气的窗口（否则第一个窗口）
        w0 = next((x for x in alignment if x.get('feasibility')), None) \
            or (alignment[0] if alignment else None)
        if w0:
            main['cloud'] = dict(w0.get('cloud_at') or {})
            main['models'] = w0.get('models') or {}
            main['consensus'] = w0.get('consensus')
            main['sight'] = w0.get('sight')
            main['aod_path'] = (w0.get('sight') or {}).get('path_aod')
            hh = int(w0['best_time'][:2])
            ij = om.hour_index(r0.get('time'), hh, w0.get('date'))
            ai = om.hour_index(aq0.get('time'), hh, w0.get('date'))
            aod = _v(aq0, 'aerosol_optical_depth', ai, 0.2)
            main['aod_local'] = round(aod, 2) if aod is not None else None
            main['pm25'] = _v(aq0, 'pm2_5', ai)
            vis_m = _vis_at_hour(fc, w0.get('date'), hh)
            main['weather'] = {
                'temp': _v(r0, 'temperature_2m', ij),
                'humidity': _v(r0, 'relative_humidity_2m', ij),
                'pressure': _v(r0, 'pressure_msl', ij),
                'visibility_km': round(vis_m / 1000.0, 1) if vis_m else None,
                'wind': _v(r0, 'wind_speed_10m', ij),
                'precip_prob': _v(r0, 'precipitation_probability', ij),
            }
            base_est = geo.estimate_cloud_base_km(
                main['cloud'].get('low', 0), main['cloud'].get('mid', 0),
                main['cloud'].get('high', 0))
            main['quality'] = geo.burn_quality(base_est, aod or 0.2,
                                               aod or 0.2)
        # 逐日事件天气（未来 7 天）：沿事件方位远端采样（方向天气）
        out_dists = []
        for h in OUTLOOK_BAND_HEIGHTS_M:
            env = sight.twilight_envelopes_km(h)
            out_dists.append(round(env[0]) if env else 200)
        out_specs = []   # (off, key, ev, az, 采样点 x3)
        for off in range(7):
            _day = date + timedelta(days=off)
            try:
                _st = astro.sun_times(_day, lat, lon, TZ_OFF)
                _mt = astro.moon_times(_day, lat, lon, TZ_OFF)
            except Exception:
                _st, _mt = {}, {}
            for _key, _ev, _az in (
                    ('sunrise', _st.get('sunrise'), _st.get('sunrise_az')),
                    ('sunset', _st.get('sunset'), _st.get('sunset_az')),
                    ('moonrise', _mt.get('moonrise'), _mt.get('moonrise_az')),
                    ('moonset', _mt.get('moonset'), _mt.get('moonset_az'))):
                if _ev and _az is not None:
                    out_specs.append(
                        (off, _key, _ev, _az,
                         [citydb.destination_point(lat, lon, _az, d)
                          for d in out_dists]))
        dir_data_map = {}
        if out_specs:
            try:
                # v2.37：展望卡仍是 7 天，请求区间就按 7 天给——用 14 天区间
                # 会让 84 坐标 × 3 变量 × 7 模式的响应凭空翻倍（实测 35s+）。
                _out_rng = (f'{date_str},'
                            f'{(date + timedelta(days=6)).strftime("%Y-%m-%d")}')
                oc = [p for _, _, _, _, pts in out_specs for p in pts]
                fc_out = om.fetch_hourly([p[0] for p in oc],
                                         [p[1] for p in oc],
                                         ['cloud_cover_low',
                                          'cloud_cover_mid',
                                          'cloud_cover_high'],
                                         list(om.MODELS.values()), _out_rng, TZ)
                aq_out = om.fetch_aq([p[0] for p in oc], [p[1] for p in oc],
                                     ['aerosol_optical_depth'],
                                     _aq_range(_out_rng) or _out_rng, TZ)
                out_recs = (fc_out or {}).get(om.MODELS['ECMWF']) or []
                out_aq = aq_out or []
                for si, (off, key, ev, az, _pts) in enumerate(out_specs):
                    recs3 = out_recs[si * 3: si * 3 + 3]
                    aq3 = out_aq[si * 3: si * 3 + 3]
                    ds_ev = (date + timedelta(days=off)).strftime('%Y-%m-%d')
                    hh = ev.hour
                    c3 = []
                    for rec in recs3:
                        ij_e = om.hour_index(rec.get('time'), hh, ds_ev) \
                            if rec else None
                        if ij_e is None or ij_e < 0:
                            c3.append(None)
                            continue
                        c3.append((_v(rec, 'cloud_cover_low', ij_e, 0.0),
                                   _v(rec, 'cloud_cover_mid', ij_e, 0.0),
                                   _v(rec, 'cloud_cover_high', ij_e, 0.0)))
                    aods = []
                    for arec in aq3:
                        ai_e = om.hour_index(arec.get('time'), hh, ds_ev) \
                            if arec else None
                        v = _v(arec, 'aerosol_optical_depth', ai_e) \
                            if ai_e is not None else None
                        if v is not None:
                            aods.append(v)
                    if all(c is not None for c in c3):
                        # 同一请求内已含多模型：顺带给出各模型在**沿方位采样点**
                        # 的云量，让多模式对比与方向判定同口径（否则会与机位云量打架）
                        per_model = {}
                        for _mname, _mid in om.MODELS.items():
                            _mrecs = (fc_out or {}).get(_mid) or []
                            _m3 = _mrecs[si * 3: si * 3 + 3]
                            _mc = []
                            for _rec in _m3:
                                _ij = om.hour_index(_rec.get('time'), hh, ds_ev) \
                                    if _rec else None
                                if _ij is None or _ij < 0:
                                    _mc.append(None)
                                    continue
                                _mc.append((_v(_rec, 'cloud_cover_low', _ij, 0.0),
                                            _v(_rec, 'cloud_cover_mid', _ij, 0.0),
                                            _v(_rec, 'cloud_cover_high', _ij, 0.0)))
                            if not all(c is not None for c in _mc):
                                continue
                            _cloud = {'low': round(_mc[0][0]),
                                      'mid': round(_mc[1][1]),
                                      'high': round(_mc[2][2])}
                            _vis = round(max(0.0, 100.0 - (
                                _cloud['low'] + 0.45 * _cloud['mid']
                                + 0.2 * _cloud['high']) * 1.05))
                            per_model[_mname] = {'cloud': _cloud,
                                                 'vis_prob': _vis}
                        dir_data_map[(off, key)] = {
                            'cloud': {'low': round(c3[0][0]),
                                      'mid': round(c3[1][1]),
                                      'high': round(c3[2][2])},
                            'aod': (round(sum(aods) / len(aods), 2)
                                    if aods else None),
                            'distances': out_dists, 'azimuth': round(az, 1),
                            'models': per_model}
            except Exception as e:
                log.info('outlook dir fetch skip: %s', str(e)[:120])
        for off in range(7):
            _day = date + timedelta(days=off)
            _ds = _day.strftime('%Y-%m-%d')
            try:
                _st = astro.sun_times(_day, lat, lon, TZ_OFF)
                _mt = astro.moon_times(_day, lat, lon, TZ_OFF)
            except Exception:
                _st, _mt = {}, {}
            _entry = {'date': _ds,
                      'weekday': '周' + '一二三四五六日'[_day.weekday()],
                      'events': {}}
            for _key, _ev, _az in (
                    ('sunrise', _st.get('sunrise'), _st.get('sunrise_az')),
                    ('sunset', _st.get('sunset'), _st.get('sunset_az')),
                    ('moonrise', _mt.get('moonrise'), _mt.get('moonrise_az')),
                    ('moonset', _mt.get('moonset'), _mt.get('moonset_az'))):
                _ew = _event_weather(fc, aq0, r0, _ds, _ev,
                                     dir_data=dir_data_map.get((off, _key)))
                if _ew:
                    _entry['events'][_key] = _ew
            outlook.append(_entry)
            if off == 0:
                day_weather = _entry['events']
        week = _align_week(lat, lon, date, target_azimuth, _tol,
                           alt_lo, alt_hi, fc, 7)
        # ---- 事件时刻视线：把当日升落事件当作"伪窗口"，供方向天气卡的剖面图/逐层明细 ----
        # 沿**事件方位**取与窗口同一套采样距离；4 事件 × 24 点 = 96 坐标（1 次请求）
        _ev_specs = []
        for _key, _ev, _az, _body in (
                ('sunrise', sun.get('sunrise'), sun.get('sunrise_az'), 'sun'),
                ('sunset', sun.get('sunset'), sun.get('sunset_az'), 'sun'),
                ('moonrise', moon.get('moonrise'), moon.get('moonrise_az'),
                 'moon'),
                ('moonset', moon.get('moonset'), moon.get('moonset_az'),
                 'moon')):
            if _ev is not None and _az is not None and _key in day_weather:
                _ev_specs.append((_key, _ev, _az, _body))
        if _ev_specs:
            try:
                _coords = [p for _k, _e, _a, _b in _ev_specs
                           for p in [citydb.destination_point(lat, lon, _a, d)
                                     for d in SIGHT_DISTANCES_KM]]
                _fc_ev = om.fetch_hourly(
                    [c[0] for c in _coords], [c[1] for c in _coords],
                    SIGHT_LEVEL_VARS + ['visibility'],
                    [om.MODELS[_m] for _m in SIGHT_MODELS],
                    date.strftime('%Y-%m-%d'), TZ)
                for _si, (_key, _ev, _az, _body) in enumerate(_ev_specs):
                    _lo = _si * len(SIGHT_DISTANCES_KM)
                    _hi = _lo + len(SIGHT_DISTANCES_KM)
                    _ent = day_weather[_key]
                    _recs = {_m: ((_fc_ev or {}).get(om.MODELS[_m]) or [])[_lo:_hi]
                             for _m in SIGHT_MODELS}
                    _s = _event_sight(_body, _ev, _az, _recs, elev0_m, None)
                    _ent['sight'] = _s
                    # 有逐层视线结果时主判定改用视线口径（与"有窗口"场景一致）；
                    # 原三层代表云口径存为 vis_prob_agg 供对照，避免同卡两个数字打架。
                    if _s and _s.get('visible') and not _s.get('data_gap'):
                        _ent['vis_prob_agg'] = _ent.get('vis_prob')
                        _ent['vis_prob'] = _s['vis_prob']
                        _feas2 = _weather_feasibility(
                            _ent.get('cloud'), _ent.get('aod'),
                            _ent.get('pm25'), _ent.get('visibility_km'),
                            _ent.get('precip_prob'), _ent.get('consensus'),
                            sight=_s, aod_path=_s.get('path_aod'), loc='方向')
                        _ent['score'] = _feas2['score']
                        _ent['verdict'] = _feas2['verdict']
                        _ent['dims'] = _feas2['dims']
                        _ent['reasons'] = _feas2['reasons']
                        _ent['risks'] = _feas2['risks']
                        _ent['cloud_basis'] = _feas2['cloud_basis']
            except Exception as e:
                log.info('event sight skip: %s', str(e)[:120])
        # week 概览与主表口径一致：有视线评估的窗口直接沿用其可见概率
        vis_lookup = {}
        for w in alignment:
            if w.get('vis_prob') is not None:
                vis_lookup[(w.get('date'), w.get('best_time'),
                            w.get('period'))] = w['vis_prob']
        for entry in week:
            for key, item in (entry.get('windows') or {}).items():
                vp = vis_lookup.get((entry['date'],
                                     item.get('best_time'), key))
                if vp is not None:
                    item['vis_prob'] = vp
                    item['verdict'] = ('通透可拍' if vp >= 70 else
                                       '薄云可拍' if vp >= 40 else '云层遮挡')
    except Exception as e:
        log.info('align weather skip: %s', e)
        _wf_err = str(e)
    # 天气获取整体失败：给出**可分辨的原因**（几何仍保留）。
    # 日期超出预报范围、接口限流、其他故障是三种不同情况，不能都说成"稍后重试"。
    _fc_hi = datetime.now().date() + timedelta(days=15)
    _wf_beyond = date.date() > _fc_hi
    if _wf_beyond:
        weather_error, weather_note = (
            'beyond_range', '所选日期超出预报范围（约未来 16 天）')
    elif _wf_err and ('429' in _wf_err or 'Too Many Requests' in _wf_err):
        weather_error, weather_note = (
            'rate_limited',
            '天气接口触发限流（Open-Meteo 免费额度用尽），请等待一段时间后重试')
    elif _wf_err:
        weather_error, weather_note = ('failed', '天气数据获取失败，请稍后重试')
    else:
        weather_error, weather_note = (None, None)
    for w in alignment:
        if 'reason' not in w:
            w['reason'] = weather_note or '天气数据获取失败，请稍后重试'
            w['feasibility'] = None
            w['vis_prob'] = None

    return {
        'meta': {'location': {'lat': lat, 'lon': lon}, 'date': date_str,
                 'version': APP_VERSION,
                 'now': _now.strftime('%Y-%m-%d %H:%M'),
                 'weather_error': weather_error,
                 'weather_note': weather_note,
                 **({'degraded': '上游额度用尽，部分数据为 %d 小时前缓存'
                                 % ((_deg + 59) // 60)}
                    if (_deg := om.degraded_since(_t0)) else {}),
                 'generated_at': datetime.now().strftime('%Y-%m-%d %H:%M')},
        'geometry': {'lat': lat, 'lon': lon, 'azimuth': round(target_azimuth, 1),
                     'aov': round(aov, 2), 'elev': round(elev, 2),
                     'elev_source': elev_src,
                     'tol': round(_tol, 2), 'alt_lo': round(alt_lo, 2),
                     'alt_hi': round(alt_hi, 2)},
        'observer': {
            'height_m': round(cam_h),
            'height_src': ('manual' if cam_height is not None
                           else ('terrain' if _site_elev is not None
                                 else 'default')),
            'terrain_elevation_m': (round(_site_elev)
                                    if _site_elev is not None else None),
            'horizon_dip_deg': round(cam_dip, 3),
        },
        'target': {
            'height_m': (round(target_height)
                         if target_height is not None else None),
            'distance_km': (round(target_dist_km, 2)
                            if target_dist_km else None),
            'elev_geo_deg': (round(target_elev_geo, 3)
                             if target_elev_geo is not None else None),
            'elev_apparent_deg': (round(target_elev_geo
                                        + _refraction_deg(target_elev_geo), 3)
                                  if target_elev_geo is not None else None),
            'source': elev_src,
        },
        'astronomy': {
            'sunrise': sun['sunrise'].strftime('%H:%M')
            if sun['sunrise'] else None,
            'sunrise_az': sun.get('sunrise_az'),
            'sunset': sun['sunset'].strftime('%H:%M') if sun['sunset'] else None,
            'sunset_az': sun.get('sunset_az'),
            'moonrise': moon.get('moonrise').strftime('%H:%M')
            if moon.get('moonrise') else None,
            'moonrise_az': moon.get('moonrise_az'),
            'moonset': moon.get('moonset').strftime('%H:%M')
            if moon.get('moonset') else None,
            'moonset_az': moon.get('moonset_az'),
            'moon_phase': moon.get('moon_phase'),
            'moon_illum': moon.get('moon_illum'),
            'moon_phase_angle': moon.get('moon_phase_angle'),
            'moon_waxing': (moon.get('moon_phase_angle') or 0) < 180,
            'blue_evening': [
                sun['blue_evening_start'].strftime('%H:%M')
                if sun.get('blue_evening_start') else None,
                sun['blue_evening_end'].strftime('%H:%M')
                if sun.get('blue_evening_end') else None],
            'blue_morning': [
                sun['blue_morning_start'].strftime('%H:%M')
                if sun.get('blue_morning_start') else None,
                sun['blue_morning_end'].strftime('%H:%M')
                if sun.get('blue_morning_end') else None],
        },
        'tracks': trk,
        'site_info': site_info,
        'hourly_clouds': hc,
        'hourly_clouds_by_date': hc_by_date,
        'day_weather': day_weather,
        'outlook': outlook,
        'main': main,
        'alignment': alignment,
        'alignment_meta': alignment_meta,
        'day_events': (day_events_past if day_events_past is not None
                       else _day_events(lat, lon, date, target_azimuth, _tol,
                                        alt_lo, alt_hi)),
        'week': week,
        'sight_points': {
            'azimuth': round(target_azimuth, 1),
            'distances_km': SIGHT_DISTANCES_KM,
            'points': sl_pts_meta,
            'observer_height_m': round(cam_h),
            'bands': [_sight_band_envelope(b, h, cam_h)
                      for b, h in (('low', 1500), ('mid', 3200),
                                   ('high', 7600))],
        },
    }


def _sight_band_envelope(band: str, height_m: float,
                         observer_height_m: float) -> dict:
    """参考包络（机位高度基准）：地平线云距/擦地云距/最远无云距 + 临界角。"""
    env = sight.twilight_envelopes_km(height_m, observer_height_m)
    out = {'band': band, 'height_m': height_m,
           'envelope_km': [round(x) for x in env[:3]] if env else None,
           'crit_alt_deg': round(env[3], 1) if env else None}
    return out

def _one_line_reason(probability, main, ens_spread, nowcast_data,
                     region=None):
    """综合评分下的一句话归因：不烧的原因 / 看烧的依据"""
    main = main or {}
    cloud = main.get('cloud', {}) or {}
    canvas = (cloud.get('mid', 0) or 0) + (cloud.get('high', 0) or 0)
    low = cloud.get('low', 0) or 0
    aod = main.get('aod_local')
    boundary = main.get('boundary_km')
    path_canvas = main.get('canvas_avg')
    dims = main.get('dims', {})
    # 每条理由带极性：'+' 支持烧、'-' 不利、'?' 中性事实。
    # 此前按 probability 选前缀、却固定取列表前两条，于是判「不烧」的日子会配上
    # 「云边界 xxx km 在霞光可达内」这种有利证据——读起来像在劝人出门，与结论
    # 相反。前缀与理由必须同极性。
    #
    # 措辞注意：这里的 canvas 是**本地格点**的中+高云量，不是报告头部的
    # canvas_avg（沿光路 0-580km 的距离加权受光画布）。两者都叫「画布」会让人
    # 以为是同一个数（实测珠海：本地 50% vs 光路 33%），所以本地口径一律写明
    # 「本地」。
    reasons = []
    if canvas < 10:
        reasons.append(('-', '本地中高云近零（无画布可烧）'))
    elif canvas > 150:
        reasons.append(('-', '云层过厚整体遮光（本地中高云 %d%%）' % canvas))
    elif canvas < 30:
        reasons.append(('-', '本地中高云偏薄（%d%%，画布不足）' % canvas))
    elif dims.get('dim2', 50) < 40:
        reasons.append(('-', '光路上游云况不支持（云边界远或上游少云）'))
    # 本地不薄但光路薄——真正拉低 dim2（权重 30%）的是光路上的受光画布，
    # 只本地够是烧不起来的。实测广州：本地中高云 62%（看着挺好）而光路画布
    # 只有 19.5%，只讲本地就解释不了为什么判「不烧」。
    if (path_canvas is not None and path_canvas < 30 and canvas >= 30
            and canvas <= 150):
        reasons.append(('-', '沿光路受光画布偏薄（%.0f%%，本地不薄但西向光路缺云）'
                         % path_canvas))
    if aod is not None and aod > 0.5:
        reasons.append(('-', '大气浑浊（AOD %.2f，污烧）' % aod))
    if low > 60:
        reasons.append(('-', '低云 %d%% 遮挡地平线' % low))
    elif low > 40:
        reasons.append(('-', '低云 %d%% 偏高，有遮挡地平线风险' % low))
    if boundary is not None:
        reasons.append(('?', '云边界 %dkm 在霞光可达内' % int(boundary)))
    if canvas >= 30 and canvas <= 150 and (aod or 1) <= 0.4 and low <= 40:
        reasons.append(('+', '本地中高云 %d%% 且大气通透' % canvas))
    if ens_spread is not None and ens_spread > 30:
        reasons.append(('-', '50 成员中高云量均值 %d%%，云况不确定' % int(ens_spread)))
    # 卫星实测（若有）
    clp = (nowcast_data or {}).get('clp') or {}
    if clp.get('runs'):
        tops = sorted(clp['runs'].items(),
                      key=lambda kv: -(kv[1][0][1] - kv[1][0][0]))[:2]
        segs = '、'.join(f"{k} {int(v[0][0])}-{int(v[0][1])}km"
                         for k, v in tops)
        reasons.append(('?', f'卫星云型实测：{segs}'))
    if not reasons:
        reasons.append(('?', '多源数据正常，无显著异常因素'))

    # 「值得出门」门槛 = 各区曲线的微烧线（上海 18 / 珠三角 65）
    if probability >= (65 if region == 'prd' else 18):
        head = '看烧依据：'
        pick = [t for s, t in reasons if s != '-'] or [t for _, t in reasons]
    else:
        head = '不烧主因：'
        pick = [t for s, t in reasons if s == '-'] or [t for _, t in reasons]
    return head + '；'.join(pick[:2])


def _path_analysis(heat_rh, x_dist, main, x_labels):
    """大气截面图下的光路分析：分段判读哪一段出了问题"""
    main = main or {}
    bullets = []
    segs = [(0, 3, x_dist[0], x_dist[3]), (3, 6, x_dist[3], x_dist[6]),
            (6, 9, x_dist[6], x_dist[8] if len(x_dist) > 8 else x_dist[-1])]
    level_rows = {'low': 1, 'mid': 2, 'high': 4}   # 850hPa/700hPa/300hPa 行
    for a, b, d0, d1 in segs:
        def avg(row):
            xs = heat_rh[row][a:b]
            xs = [x for x in xs if x is not None]
            return sum(xs) / len(xs) if xs else None
        rh_low = avg(level_rows['low'])
        rh_mid = avg(level_rows['mid'])
        rh_high = avg(level_rows['high'])
        feats = []
        if rh_low is not None and rh_low >= 85:
            feats.append(f'低云湿层 RH{rh_low:.0f}%（遮挡光路）')
        if rh_mid is not None and 60 <= rh_mid < 85:
            feats.append(f'中云层 RH{rh_mid:.0f}%（潜在画布）')
        if rh_high is not None and rh_high >= 55:
            feats.append(f'高云 RH{rh_high:.0f}%（远端画布）')
        if not feats:
            feats.append('中高层干燥（无画布）')
        bullets.append(f'光路 {d0:.0f}-{d1:.0f}km：' + '、'.join(feats))
    aod_p = main.get('aod_path')
    if aod_p is not None:
        tone = '通透' if aod_p <= 0.3 else ('一般' if aod_p <= 0.5 else '浑浊（颜色发灰）')
        bullets.append(f'光路平均 AOD {aod_p:.2f}（{tone}）')
    cl = main.get('cloud', {}) or {}
    canvas = (cl.get('mid', 0) or 0) + (cl.get('high', 0) or 0)
    if canvas < 30:
        bullets.append('综合：本地与光路中高云画布不足，是当前不烧的主因')
    elif (cl.get('low', 0) or 0) > 60:
        bullets.append('综合：本地低云/雾遮挡地平线，是当前不烧的主因')
    else:
        bullets.append('综合：光路画布条件成立，主要风险在云边界位置与 AOD')
    return bullets


def _log_and_attach(report_bundle: dict) -> dict:
    """前瞻性验证日志落盘 + 回填状态标记（log_prediction 直接返回记录，免二次读盘）"""
    try:
        rec = flog.log_prediction(report_bundle) or {}
        report_bundle['log_key'] = rec.get('event_key')
        report_bundle['awaiting_outcome'] = bool(rec.get('awaiting_outcome'))
        report_bundle['outcome'] = rec.get('outcome')
    except Exception as e:
        log.warning('forecast log skip: %s', e)
    return report_bundle


def _hourly_clouds(fc3, alignment, date_str: str) -> dict:
    """当日逐时低/中/高云（ECMWF），供天体预览时间滑块的云带遮挡插值"""
    out = {'time': [], 'low': [], 'mid': [], 'high': []}
    try:
        recs = fc3.get(om.MODELS['ECMWF']) or []
        r0 = recs[0] if recs else {}
        times = r0.get('time') or []
        keep = [t for t in times if t.startswith(date_str)]
        out['time'] = keep
        for v in ('cloud_cover_low', 'cloud_cover_mid', 'cloud_cover_high'):
            arr = r0.get(v) or []
            idx = {t: i for i, t in enumerate(times)}
            out[v[12:] if v.startswith('cloud_cover_') else v] = \
                [arr[idx[t]] if (t in idx and idx[t] < len(arr)) else None
                 for t in keep]
    except Exception as e:
        log.info('hourly_clouds skip: %s', e)
    return out


def build_nowcast(lat: float, lon: float, date_str: str, kind: str,
                  key_time, key_az: float, main: dict = None) -> dict:
    """卫星临近校准（可独立调用，供自动刷新端点使用）。
    内部 4 路并行：JMA B13 / P-Tree 亮温 / CLP 云型 / 和风实况。"""
    main = main or {}
    now_local = datetime.now()
    minutes_to_event = (key_time - now_local).total_seconds() / 60.0
    nowcast_data = None
    import concurrent.futures as _cf2

    def _safe2(fn, *a, **kw):
        try:
            return fn(*a, **kw)
        except Exception as e:
            log.info('[nowcast] part skip: %s', str(e)[:80])
            return None
    try:
        # 卫星采样加密到 30km 间距（判读精度），与 API 光路 9 点解耦
        sat_path = [(lat, lon, 0.0)]
        for d in range(30, 620, 30):
            plat, plon = citydb.destination_point(lat, lon, key_az, d)
            sat_path.append((plat, plon, float(d)))
        # 触发 P-Tree 高分辨率后台预取（幂等；build_report 阶段 0 已提前触发）
        try:
            ev_utc = (key_time - timedelta(hours=8)).replace(
                tzinfo=timezone.utc)
            sat.arm_event(lat, lon, date_str, kind, ev_utc)
        except Exception:
            pass

        def _clp_task():
            try:
                jd_now = astro.jd_from_datetime(datetime.now()) - 8.0 / 24.0
                if sat.solar_elevation_at(lat, lon, jd_now) <= 5.0:
                    return None   # 夜间 CLP 不可用
                return sat.clp_profile(sat_path)
            except Exception as e:
                log.info('[nowcast] clp skip: %s', str(e)[:80])
                return None

        with _cf2.ThreadPoolExecutor(max_workers=4) as _npool:
            # 四路都要包 _safe2 + 各自限时（审查报告 ⑧）：JMA 那路原来裸
            # 提交且 .result() 无超时——JMA/FTP 抖动会连累另外三路已取到的
            # 数据，甚至拖住整份报告。
            f_jma = _npool.submit(_safe2, sat.nowcast, lat, lon, key_time,
                                  key_az, sat_path, kind)
            f_pt = _npool.submit(_safe2, sat.ptree_nowcast, lat, lon, sat_path)
            f_clp = _npool.submit(_clp_task)
            f_qw = _npool.submit(_safe2, qw.ground_truth, lat, lon)
            _dl3 = time.monotonic() + float(os.environ.get('GC_NOWCAST_WAIT')
                                             or 45)

            def _r3(fut, default):
                try:
                    v = fut.result(timeout=max(0.1, _dl3 - time.monotonic()))
                except Exception as e:
                    log.info('[nowcast] part timeout/fail: %s', str(e)[:80])
                    return default
                return default if v is None else v

            nowcast_data = _r3(f_jma, {})
            pt = _r3(f_pt, None)
            pt_clp = _r3(f_clp, None)
            gt = _r3(f_qw, None)
        if not nowcast_data:
            nowcast_data = {'available': False,
                            'reason': 'JMA 卫星通道暂不可用（已重试）'}
        nowcast_data['minutes_to_event'] = round(minutes_to_event)
        nowcast_data['mode'] = 'jma601'
        judgments = []
        # P-Tree 5km B13 亮温判读（高分辨率，优先展示）
        if pt:
            nowcast_data['mode'] = 'ptree5km'
            nowcast_data['ptree'] = pt
            parts = []
            for cls, rng in pt['runs'].items():
                rt = '、'.join(f'{int(a)}-{int(b)}km'
                               for a, b in rng[:4])
                parts.append(f'{cls} {rt}')
            head = (f"卫星亮温判读（P-Tree 5km，BJT {pt['slot_bjt']}，"
                    f"{pt['age_min']} 分钟前）：")
            judgments.append(head + ('；'.join(parts) if parts
                                     else '光路全程晴空'))
        # JAXA CLP 云型判读（白昼；太阳高度 >5° 时可用）——云属+光学厚度
        # （pt_clp 已在 4 路并行中取回，None=夜间或失败）
        if pt_clp and pt_clp.get('runs'):
            nowcast_data['clp'] = pt_clp
            nowcast_data['mode'] = ('clp5km+' + str(nowcast_data.get('mode'))
                                    if nowcast_data.get('mode')
                                    else 'clp5km')
            parts = []
            for tn, rng in pt_clp['runs'].items():
                code = next((s['type'] for s in pt_clp['points']
                             if s.get('type_name') == tn), None)
                val = sat.ISCCP_VALUE.get(code, '')
                cots = [s['cot'] for s in pt_clp['points']
                        if s.get('type_name') == tn
                        and s.get('cot') is not None]
                cot_t = (f'COT{min(cots):.0f}-{max(cots):.0f}'
                         if cots else '')
                rt = '、'.join(f'{int(a)}-{int(b)}km'
                               for a, b in rng[:3])
                parts.append(f'{tn}（{val}，{cot_t}）{rt}')
            judgments.insert(0, '卫星云型判读（JAXA CLP，BJT '
                             + pt_clp['slot_bjt'] + '，'
                             + str(pt_clp['age_min']) + ' 分钟前）：'
                             + '；'.join(parts[:4]))
        if nowcast_data.get('available'):
            bands = nowcast_data.get('cloud_bands') or []
            gaps = nowcast_data.get('clear_gaps') or []
            frac = nowcast_data.get('cloudy_frac', 0)
            if frac <= 0.2:
                judgments.append(f'卫星实测（BJT {nowcast_data["sat_time_bjt"]}）：'
                                 f'光路整体晴透（云量 {frac*100:.0f}%），'
                                 '若模式同样报好则是高置信好天')
            elif frac >= 0.8:
                judgments.append(f'卫星实测：光路几乎全被云盖（云量 {frac*100:.0f}%），'
                                 '临近翻车风险高')
            else:
                judgments.append(f'卫星实测：光路云量 {frac*100:.0f}%')
            if bands:
                bt = '、'.join(f'{int(a)}-{int(b)}km' for a, b in bands[:4])
                judgments.append(f'卫星实测云带：{bt}'
                                 '（亮=冷=中高云）')
            if gaps and min(g[1] for g in gaps) <= 500:
                gt = '、'.join(f'{int(a)}-{int(b)}km' for a, b in gaps[:4])
                judgments.append(f'卫星实测云隙：{gt} —— '
                                 '云隙扫过观测点时存在短时烧机会')
            mb = main.get('boundary_km')
            if mb and bands:
                nearest = min(bands, key=lambda b: abs(b[0] - mb))
                dev = nearest[0] - mb
                if abs(dev) <= 30:
                    judgments.append(f'云边界卫星实测 ≈{int(nearest[0])}km，'
                                     f'与模型预报 {int(mb)}km 基本一致')
                else:
                    d = '近' if dev < 0 else '远'
                    judgments.append(f'云边界卫星实测 ≈{int(nearest[0])}km，'
                                     f'比模型预报 {int(mb)}km 更{d} {abs(int(dev))}km'
                                     f'（观赏窗口相应{"提前" if dev < 0 else "推迟"}）')
            # 和风地面实况三方交叉验证（gt 已在 4 路并行中取回）
            gt = gt or {}
            if gt.get('available'):
                nowcast_data['ground'] = gt
                gc = gt.get('cloud')
                gt_vis = gt.get('vis_km')
                judgments.append(
                    f"地面实况（和风 {gt.get('obs_time')}）：{gt.get('text')}，"
                    f"实测总云量 {gc if gc is not None else '-'}%，"
                    f"能见度 {gt_vis if gt_vis is not None else '-'}km，湿度 {gt.get('humidity')}%")
                if gc is not None and gc >= 70 and frac <= 0.3:
                    judgments.append('⚠ 实况总云量高但卫星光路无中高云——'
                                     '很可能是低云/雾遮挡地平线，低角度目标可能被挡')
                if gt.get('rain_any'):
                    judgments.append(f"⚠ 分钟级降水临近：{gt.get('rain_summary')}，"
                                     "雨幕会遮挡光路")
            nowcast_data['judgments'] = judgments
    except Exception as e:
        log.warning('nowcast skip: %s', e)
        nowcast_data = {'available': False, 'reason': str(e)}
    if nowcast_data is not None:
        nowcast_data['minutes_to_event'] = round(minutes_to_event)
    return nowcast_data

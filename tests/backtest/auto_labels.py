# -*- coding: utf-8 -*-
"""珠三角朝晚霞「客观标签」自动生成：卫星观测替代预报云场。

思路
----
预报引擎预测的是「日落（日出）后，朝太阳一侧多远、多高的云还受光」。
要验证它，就要有**同一把尺子的观测值**——所以本模块不另立判据，而是把
``app/core`` 里那套判据原样搬到观测云场上：

1. 云顶高度：由向日葵 B13（10.4 µm）亮温 + ERA5 温度廓线反演出云顶海拔
   （``tbb_13`` 对不透明云即云顶温度；薄卷云因半透明会偏暖，即高度偏低，
   属保守方向）。
2. 受光判据：``app.core.illumination.lit_fraction(h, d, sun_alt)``——
   与预报侧完全同一函数。
3. 云况打分：``app.core.scoring.dim2_path_canvas``——同一曲线、同一低云惩罚。
   预报侧的 ``canvas`` 本就是「受光画布」，所以这里喂进去的是**受光**的
   中高云占比，两边口径对齐。

得到的是 0~100 的「卫星受光画布占比」``canvas_lit_avg``（**标签就用它**）、
一个参照用的综合分 ``score``，逐日逐朝晚各一行。

为什么标签取 ``canvas_lit_avg`` 而不是 ``score``
------------------------------------------------
``scoring.dim2_path_canvas`` 里低云少是加分项，于是"一片晴空、无云可烧"也能
拿到 62 分——预报侧靠 ``scoring.combine`` 的 dead_sky/feasible 上限兜住，
标签侧这里同样加了闸门（画布 <10% 时上限 25 分）。但受光画布占比本身才是
「有没有霞的画布」这个物理量，直接用它做标签更干净。

局限（必须写进结论，别当成万能真值）
------------------------------------
- 它验证的是**云场是否满足受光条件**（霞的必要条件），不是「肉眼是否看到
  橙红色」。颜色还取决于气溶胶、云滴谱等，卫星给不出。
- 只有一层云顶。多层云叠置时低云遮挡看不出来。
- 5 km 网格：比预报的模式网格细，但仍会把小尺度的云隙抹平。

用法::

    python tests/backtest/b13_grid.py  --start 2024-06-01 --limit 5   # 先拉窗口
    python tests/backtest/auto_labels.py --start 2024-06-01 --limit 5
"""
import argparse
import csv
import json
import math
import os
import sys
from datetime import date, datetime, timedelta

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, '..', '..'))
sys.path.insert(0, ROOT)

from app.core import astronomy as astro                 # noqa: E402
from app.core import illumination as illum              # noqa: E402
from app.core import scoring as sc                      # noqa: E402
from app.core.sightline import R_EARTH_KM               # noqa: E402
import b13_grid                                         # noqa: E402

DATA_DIR = os.path.join(ROOT, 'data', 'b13_grid')
PROFILE_DIR = os.path.join(DATA_DIR, 'profiles')

# 光路采样：弧距 30~600 km、横向 ±120 km（9 条），对标 report.py 的光路口径
PATH_KM = (30, 60, 100, 150, 200, 280, 360, 450, 540, 630)
CROSS_KM = (-120, -90, -60, -30, 0, 30, 60, 90, 120)

# 晴空判据：亮温比地面温度低不足此值 -> 视为晴空（无云），既不算画布也不算低云。
# 取 4 K 是给地表发射率与地面皮温差留余量；夏季白天陆面会高估一点，
# 表现是无云日被判成「低云」——所以这一步必须显式判晴空，不能默认归低云。
CLEAR_MARGIN_K = 4.0

PRESSURE_LEVELS = (1000, 950, 900, 850, 800, 700, 600, 500, 400, 300, 250, 200)

# 冬季/热带标准大气回退（拿不到 ERA5 廓线时用；rh 无关）
_STD_ATMO_Z_KM = np.array([0.0, 0.5, 1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0,
                           8.0, 9.0, 10.0, 11.0, 12.0, 14.0, 16.0, 18.0])
_STD_ATMO_T_C = np.array([28.0, 25.0, 22.0, 14.0, 7.0, 0.0, -7.0, -14.0,
                          -21.0, -28.0, -36.0, -45.0, -54.0, -56.0, -56.0,
                          -56.0, -56.0])


# ---------------------------------------------------------------- 温度廓线

def profile_from_t2m(t2m_c, base=None):
    """用实况 2 m 气温锚定一条标准廓线。

    免费档案接口（``archive-api.open-meteo.com``）**不提供气压层变量**：
    实测 ``temperature_850hPa``／``geopotential_height_850hPa`` 一律返回
    null，只有 ``temperature_2m`` 有值（气压层只在**预报**接口才有）。
    所以拿不到真实廓线时，保留标准大气的垂直结构（含对流层顶），把整条
    曲线平移到实测地面温度——季节变化由 T2m 带进来，比固定廓线准得多。
    """
    z = _STD_ATMO_Z_KM if base is None else base[0]
    t = _STD_ATMO_T_C if base is None else base[1]
    return z, t + (t2m_c - t[0])


def profile_from_era5(hourly, idx):
    """从 Open-Meteo 气压层数据取某时刻廓线 -> (z_km 升序, T_C)；不足返回 None。"""
    zs, ts = [], []
    for p in PRESSURE_LEVELS:
        z = hourly.get('geopotential_height_%dhPa' % p)
        t = hourly.get('temperature_%dhPa' % p)
        if not z or not t or idx >= len(z):
            continue
        if z[idx] is None or t[idx] is None:
            continue
        zs.append(z[idx] / 1000.0)
        ts.append(t[idx])
    if len(zs) < 4:
        return None
    order = np.argsort(zs)
    return np.asarray(zs)[order], np.asarray(ts)[order]


def fetch_profile(day, lat, lon, cache_dir=None):
    """取（并缓存）当日 12:00 UTC 的温度廓线；全失败返回 None。

    优先气压层实测；档案接口给不出就用 T2m 锚定标准大气；连 T2m 也没有
    （离线／额度耗尽）才返回 None，由调用方回退固定标准大气。
    """
    import requests
    cache_dir = cache_dir or PROFILE_DIR
    os.makedirs(cache_dir, exist_ok=True)
    path = os.path.join(cache_dir, day.isoformat() + '.json')
    if os.path.exists(path):
        try:
            with open(path, encoding='utf-8') as f:
                d = json.load(f)
            if d.get('z'):
                return (np.asarray(d['z']), np.asarray(d['t']),
                        d.get('kind') or ('t2m' if d.get('t2m_anchored')
                                          else 'levels'))
        except Exception:
            return None
    var = ['temperature_2m']
    for p in PRESSURE_LEVELS:
        var += ['temperature_%dhPa' % p, 'geopotential_height_%dhPa' % p]
    try:
        r = requests.get('https://archive-api.open-meteo.com/v1/archive', params={
            'latitude': lat, 'longitude': lon,
            'start_date': day.isoformat(), 'end_date': day.isoformat(),
            'hourly': ','.join(var), 'timezone': 'UTC'}, timeout=60)
        r.raise_for_status()
        hourly = r.json().get('hourly') or {}
        times = hourly.get('time') or []
        key = day.isoformat() + 'T12:00'
        idx = times.index(key) if key in times else len(times) // 2
        pr = profile_from_era5(hourly, idx)
        anchored = False
        if pr is None:
            t2m = hourly.get('temperature_2m') or []
            if idx < len(t2m) and t2m[idx] is not None:
                pr = profile_from_t2m(t2m[idx])
                anchored = True
        if pr is None:
            return None
        kind = 'levels' if not anchored else 't2m'
        with open(path, 'w', encoding='utf-8') as f:
            json.dump({'z': pr[0].tolist(), 't': pr[1].tolist(),
                       'kind': kind, 't2m_anchored': anchored}, f)
        return pr[0], pr[1], kind
    except Exception:
        return None


def bt_to_height(bt_c, profile):
    """亮温 -> 云顶海拔(km)。用单调递减的 T(z) 反查；比地面暖按 0 处理。

    ``profile`` 取 ``(z_km, T_C)`` 两元组（``fetch_profile`` 返回的第三位
    是来源标记，这里不用）。
    """
    if profile is None:
        z, t = _STD_ATMO_Z_KM, _STD_ATMO_T_C
    else:
        z, t = profile[0], profile[1]     # 第三位是来源标记，这里不用
    if bt_c is None or not math.isfinite(bt_c):
        return None
    if bt_c >= t[0]:
        return 0.0
    if bt_c <= t[-1]:
        return float(z[-1])
    # T(z) 递减 -> 反序后 np.interp 正确
    return float(np.interp(bt_c, t[::-1], z[::-1]))


# ---------------------------------------------------------------- 采样与打分

def _offset_point(lat, lon, bearing_deg, dist_km):
    """从 (lat,lon) 沿 bearing 走 dist_km 后的经纬度（球面）。"""
    d = dist_km / R_EARTH_KM
    b = math.radians(bearing_deg)
    la = math.radians(lat)
    lo = math.radians(lon)
    la2 = math.asin(math.sin(la) * math.cos(d) + math.cos(la) * math.sin(d) * math.cos(b))
    lo2 = lo + math.atan2(math.sin(b) * math.sin(d) * math.cos(la),
                          math.cos(d) - math.sin(la) * math.sin(la2))
    return math.degrees(la2), math.degrees(lo2)


def _bilinear(arr, lat, lon, grid_lat, grid_lon):
    """最近邻采样（0.05° 网格，最近邻与双线性差异远小于 5 km 尺度）。"""
    j = int(np.argmin(np.abs(grid_lat - lat)))
    i = int(np.argmin(np.abs(grid_lon - lon)))
    return float(arr[j, i])


def path_canvas(win, lat, lon, sun_az, sun_alt, profile,
                distances=PATH_KM, cross=CROSS_KM):
    """沿日向光路统计「受光的中高云占比」与低云占比。

    返回 dict：canvas_lit / canvas_all / low / 各距离明细。
    """
    glat, glon = win['lat'], win['lon']
    bt = win['tbb_13']
    t_sfc = float(profile[1][0]) if profile else float(_STD_ATMO_T_C[0])
    clear_rail = t_sfc - CLEAR_MARGIN_K
    per_d = []
    for d in distances:
        lit_hits = all_hits = low_hits = clear_hits = n = 0
        for o in cross:
            brg = sun_az + math.degrees(math.atan2(o, d))
            dist = math.hypot(d, o)
            plat, plon = _offset_point(lat, lon, brg, dist)
            if not (glat.min() <= plat <= glat.max()
                    and glon.min() <= plon <= glon.max()):
                continue
            t = _bilinear(bt, plat, plon, glat, glon)
            if not math.isfinite(t):
                continue
            n += 1
            if t >= clear_rail:        # 晴空：不是画布，也不是低云
                clear_hits += 1
                continue
            h = bt_to_height(t, profile)
            if h is None:
                continue
            if h >= 6.0:
                kind = 'high'
            elif h >= 2.0:
                kind = 'mid'
            else:
                low_hits += 1
                continue
            all_hits += 1
            if kind and illum.lit_fraction(h, dist, sun_alt, toward_sun=True) > 0.5:
                lit_hits += 1
        if n:
            per_d.append({'dist': d,
                          'canvas_lit': 100.0 * lit_hits / n,
                          'canvas_all': 100.0 * all_hits / n,
                          'low': 100.0 * low_hits / n,
                          'clear': 100.0 * clear_hits / n,
                          'n': n})
    if not per_d:
        return None
    ds = [p['dist'] for p in per_d]
    lit = [p['canvas_lit'] for p in per_d]
    low = [p['low'] for p in per_d]
    score, canvas_avg, low_avg, boundary = sc.dim2_path_canvas(lit, low, ds)
    # 空画布闸门：dim2 的「低云少=加分」会让一片晴空也拿到 62 分，预报侧靠
    # scoring.combine 的 dead_sky/feasible 上限兜住；标签侧必须同样兜住，
    # 否则「无云可烧」会被误记成中高事件。
    if canvas_avg < 10.0:
        score = min(score, 25.0)
    return {'score': score, 'canvas_lit_avg': canvas_avg, 'low_avg': low_avg,
            'boundary_km': boundary, 'per_distance': per_d}


# ---------------------------------------------------------------- 逐日打标

def label_one_day(day, lat, lon, tz, period, offsets, cache_dir=DATA_DIR):
    """对某日某朝/晚，取各槽位光路分，输出一行标签。"""
    st = astro.sun_times(datetime(day.year, day.month, day.day), lat, lon, tz)
    event = st.get('sunset' if period == 'PM' else 'sunrise')
    if not event:
        return None
    profile = fetch_profile(day, lat, lon)
    rows = []
    for off in offsets:
        t_slot_local, utc_day, slot = b13_grid.slot_time(event, tz, off)
        # 朝霞的槽位在前一天的 UTC 目录（06:02 CST = 前日 22:02 UTC），
        # window_key 必须用 UTC 日期，否则 AM 永远读不到帧
        npz = os.path.join(cache_dir,
                           b13_grid.window_key(utc_day, slot) + '.npz')
        if not os.path.exists(npz):
            continue
        jd = astro.jd_from_datetime(t_slot_local - timedelta(hours=tz))
        ra, dec, _ = astro.sun_position(jd)
        sun_alt, sun_az = astro.altaz(ra, dec, lat, lon, jd)
        win = b13_grid.read_window(npz)
        pc = path_canvas(win, lat, lon, sun_az, sun_alt, profile)
        if pc:
            pc.update({'slot': slot, 'offset_min': off,
                       'sun_alt': round(sun_alt, 2), 'sun_az': round(sun_az, 1)})
            rows.append(pc)
    if not rows:
        return None
    best = max(rows, key=lambda r: r['canvas_lit_avg'])
    return {'date': day.isoformat(), 'period': period,
            'canvas_lit_avg': best['canvas_lit_avg'], 'score': best['score'],
            'low_avg': best['low_avg'], 'boundary_km': best['boundary_km'],
            'slot': best['slot'], 'sun_alt': best['sun_alt'],
            'sun_az': best['sun_az'], 'n_slots': len(rows),
            'profile': profile[2] if profile else 'std',
            'canvas_per_slot': ';'.join(
                '%s:%.0f' % (r['slot'], r['canvas_lit_avg']) for r in rows)}


# ---------------------------------------------------------------- 评估

def auc(pairs):
    """pairs: [(score, label)] -> AUC（Mann-Whitney，含并列半分）。"""
    pos = [s for s, y in pairs if y]
    neg = [s for s, y in pairs if not y]
    if not pos or not neg:
        return None
    ranks = {}
    order = sorted(range(len(pairs)), key=lambda i: pairs[i][0])
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and pairs[order[j + 1]][0] == pairs[order[i]][0]:
            j += 1
        avg = (i + j) / 2.0 + 1
        for k in range(i, j + 1):
            ranks[order[k]] = avg
        i = j + 1
    rsum = sum(ranks[i] for i, (s, y) in enumerate(pairs) if y)
    n1, n0 = len(pos), len(neg)
    return (rsum - n1 * (n1 + 1) / 2.0) / (n1 * n0)


def calibration(pairs, bins=(0, 20, 40, 60, 80, 101)):
    out = []
    for lo, hi in zip(bins, bins[1:]):
        xs = [y for s, y in pairs if lo <= s < hi]
        if xs:
            out.append({'bin': [lo, hi], 'n': len(xs),
                        'rate': round(sum(xs) / len(xs), 3)})
    return out


def print_report(rows, threshold):
    pairs = [(r['canvas_lit_avg'], 1 if r['canvas_lit_avg'] >= threshold else 0)
             for r in rows]
    n = len(pairs)
    pos = sum(y for _, y in pairs)
    print('\n样本 %d 天次：受光画布 >=%d%% 的占 %d（%.1f%%）'
          % (n, threshold, pos, 100.0 * pos / n if n else 0))
    print('受光画布分箱 -> 占比：')
    for lo, hi in ((0, 10), (10, 25), (25, 50), (50, 75), (75, 101)):
        xs = [c for c, _ in pairs if lo <= c < hi]
        if xs:
            print('  [%3d,%3d)  n=%4d  %5.1f%%' % (lo, hi, len(xs),
                                                   100.0 * len(xs) / n))


def main():
    ap = argparse.ArgumentParser(description='珠三角朝晚霞客观标签自动生成')
    ap.add_argument('--start', required=True)
    ap.add_argument('--end')
    ap.add_argument('--limit', type=int, default=0)
    ap.add_argument('--lat', type=float, default=23.1291)
    ap.add_argument('--lon', type=float, default=113.2644)
    ap.add_argument('--tz', type=float, default=8.0)
    ap.add_argument('--offsets', default='-10,0,10,20,30',
                    help='相对日落/日出的分钟偏移，逗号分隔。默认是**超集**：'
                         '历史帧分批用过 0/20 与 ±10/30 两套偏移，打标侧宁可'
                         '多列几个不存在的槽位（自动跳过），也不要与下载侧'
                         '错位 —— 错位的表现是「明明有帧却无数据」')
    ap.add_argument('--period', default='PM', choices=['AM', 'PM', 'both'])
    ap.add_argument('--threshold', type=int, default=30,
                    help='受光画布占比阈值（%），>= 记为事件')
    ap.add_argument('--out', default=os.path.join(ROOT, 'data', 'glow_labels.csv'))
    args = ap.parse_args()

    offsets = [int(x) for x in args.offsets.split(',') if x.strip()]
    periods = ['AM', 'PM'] if args.period == 'both' else [args.period]
    d0 = date.fromisoformat(args.start)
    d1 = date.fromisoformat(args.end) if args.end else d0
    os.makedirs(os.path.dirname(args.out), exist_ok=True)

    rows = []
    cur, done = d0, 0
    while cur <= d1:
        if args.limit and done >= args.limit:
            break
        done += 1
        for p in periods:
            r = label_one_day(cur, args.lat, args.lon, args.tz, p, offsets)
            if r:
                rows.append(r)
                print('%s %s  受光画布=%5.1f%%  分=%3d  低云=%5.1f%%  槽=%s'
                      % (r['date'], p, r['canvas_lit_avg'], r['score'],
                         r['low_avg'], r['canvas_per_slot']), flush=True)
            else:
                print('%s %s  无数据' % (cur, p), flush=True)
        cur += timedelta(days=1)

    if rows:
        with open(args.out, 'w', newline='', encoding='utf-8') as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            w.writeheader()
            w.writerows(rows)
        print('\n-> %s（%d 行）' % (args.out, len(rows)))
        print_report(rows, args.threshold)


if __name__ == '__main__':
    main()

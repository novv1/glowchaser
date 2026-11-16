# -*- coding: utf-8 -*-
"""珠三角预报分生成：ERA5 再分析当「完美预报」，引擎同口径打分。

对账里「预报分」这一侧（``validate_prd.py`` 的第二份输入）。上海 555 天基线
（``eval_baseline.py``，AUC 0.719）是**本地单点**特征（dim1/3/5/6）；本脚本把它
升级到与线上引擎相同的**光路口径**：

1. 沿事件（日落/日出）方位取引擎同款采样距离 ``PATH_DISTANCES[1:]``
   （50~580 km，8 点），每点取 ERA5 低/中/高云量；
2. ``path_midhigh = mid + high``、低云单列 —— 与 ``report.py`` 喂给
   ``scoring.dim2_path_canvas`` 的形状一字不差，曲线与低云惩罚同一套；
3. dim1/3/5/6 与 ``eval_baseline.py`` 同式；组合分 = 现有引擎权重在
   **可算维度内归一**（dim4/7/8 档案接口拿不到，见下）。

为什么用 ERA5 而不是拿今天的预报接口查历史
------------------------------------------
拿预报接口查历史日期，拿到的是**当前模式对过去的分析场**（上帝视角），
算出的 AUC 是假的。ERA5 再分析把云场当"完美预报"，衡量的是**评分函数
本身的判别力上限**——与上海 0.719 同口径，可直接对比。真正的"预报精度"
要等 ``forecast_log`` 的逐日落盘攒够天数（``validate_prd.py`` 文档里那条）。

已知近似（读结论前先看）
------------------------
- **无受光（地影）折减**：ERA5 档案接口只有低/中/高三层云量，没有云顶
  高度，``illumination.lit_fraction`` 需要高度。本引擎自己的实测（v2.26）
  是判定时刻（观赏时段中点）多在日落后 1~4 分钟，绝大多数云仍受光、
  dim2 几乎不变——所以省略它对 PM 影响很小，AM（日出前）同理对称。
  若未来标签侧出现深暮光差异，这里要用名义层高（2/6/10 km）补上。
- **dim4（能见度/PM2.5）、dim7（AOD）缺席**：档案接口不给；权重在剩余
  维度内归一（与 ``eval_baseline.py`` 的 keys4 做法一致）。dim8 气压层
  RH 一律 null（README「珠三角验证」节踩过），同样缺席。
- **事件时刻取整点**：ERA5 是逐小时瞬时值，日落 11:16 UTC 取 11:00 的
  云场（±1h 窗口平均在 ``eval_baseline`` 里实测只挪 AUC 0.005，不另做）。

用法::

    python tests/backtest/prd_scores.py --start 2024-06-01 --end 2024-07-03
    # 断点续跑：逐 (date,period) 缓存在 data/prd_scores_cache/，重跑只补缺
"""
import argparse
import csv
import json
import os
import sys
import time
from datetime import date, datetime, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, '..', '..'))
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

from app.core import astronomy as astro          # noqa: E402
from app.core import scoring as sc               # noqa: E402
from auto_labels import _offset_point             # noqa: E402

# 引擎光路采样距离（report.py 同款；[0] 是观测点自身）
PATH_DISTANCES = [0, 50, 100, 160, 230, 310, 400, 490, 580]

CACHE_DIR = os.path.join(ROOT, 'data', 'prd_scores_cache')
OUT_DEFAULT = os.path.join(ROOT, 'data', 'prd_forecast_scores.csv')

# 参与组合的维度（档案接口能算的）；权重在其中的归一与 eval_baseline 一致
DIMS_USED = ('dim1', 'dim2', 'dim3', 'dim5', 'dim6')


def fetch_day(day_utc, coords, cache_dir=CACHE_DIR):
    """取某 UTC 日、多点坐标的逐时云场（带磁盘缓存，429 退避重试）。

    返回 Open-Meteo 的 list[{'hourly': {...}}]（每坐标一项，顺序同入参）。

    缓存键 = UTC 日期 + 全部坐标摘要：同一 UTC 日既有本地晚霞（PM）又有
    次日朝霞（AM）的事件，两者方位不同、坐标不同，**不能**只用日期做键，
    否则后跑的一方会静默命中前者的云场。
    """
    os.makedirs(cache_dir, exist_ok=True)
    coord_sig = '-'.join('%+.2f%+.2f' % c for c in coords)
    path = os.path.join(
        cache_dir, '%s_%s.json' % (day_utc.isoformat(), coord_sig[:60]))
    if os.path.exists(path):
        with open(path, encoding='utf-8') as f:
            return json.load(f)

    import requests
    lats = ','.join('%.4f' % c[0] for c in coords)
    lons = ','.join('%.4f' % c[1] for c in coords)
    var = ('cloud_cover_low,cloud_cover_mid,cloud_cover_high,'
           'precipitation,pressure_msl')
    last_err = None
    for attempt in range(4):
        try:
            r = requests.get(
                'https://archive-api.open-meteo.com/v1/archive', params={
                    'latitude': lats, 'longitude': lons,
                    'start_date': day_utc.isoformat(),
                    'end_date': day_utc.isoformat(),
                    'hourly': var, 'timezone': 'UTC'}, timeout=120)
            if r.status_code == 429:
                time.sleep(30 * (attempt + 1))
                last_err = RuntimeError('429')
                continue
            r.raise_for_status()
            data = r.json()
            if isinstance(data, list):
                with open(path, 'w', encoding='utf-8') as f:
                    json.dump(data, f)
                return data
            raise RuntimeError(str(data)[:120])
        except Exception as e:
            last_err = e
            time.sleep(10 * (attempt + 1))
    raise RuntimeError('archive 请求失败 %s: %s' % (day_utc, last_err))


def _hourly(recs, i, var, t_key, default=None):
    h = recs[i].get('hourly') or {}
    times = h.get('time') or []
    if t_key not in times:
        return default
    arr = h.get(var) or []
    idx = times.index(t_key)
    if idx >= len(arr) or arr[idx] is None:
        return default
    return arr[idx]


def score_one(day, lat, lon, tz, period):
    """对某日某朝/晚出一条预报分记录；拿不到云场返回 None。"""
    st = astro.sun_times(datetime(day.year, day.month, day.day), lat, lon, tz)
    event = st.get('sunset' if period == 'PM' else 'sunrise')
    if not event:
        return None
    jd = astro.jd_from_datetime(event - timedelta(hours=tz))
    ra, dec, _ = astro.sun_position(jd)
    _, sun_az = astro.altaz(ra, dec, lat, lon, jd)

    # 事件对应的 UTC 时刻与日期（日出在本地清晨 => 前一 UTC 日）
    ev_utc = event - timedelta(hours=tz)
    day_utc = ev_utc.date()
    t_key = '%sT%02d:00' % (day_utc.isoformat(), ev_utc.hour)
    h3 = ev_utc - timedelta(hours=3)
    t_key3 = '%sT%02d:00' % (h3.date().isoformat(), h3.hour)

    coords = [(lat, lon)] + [
        _offset_point(lat, lon, sun_az, d) for d in PATH_DISTANCES[1:]]
    recs = fetch_day(day_utc, coords)

    mid = _hourly(recs, 0, 'cloud_cover_mid', t_key, 0.0)
    high = _hourly(recs, 0, 'cloud_cover_high', t_key, 0.0)
    low = _hourly(recs, 0, 'cloud_cover_low', t_key, 0.0)
    mid3 = _hourly(recs, 0, 'cloud_cover_mid', t_key3, 0.0)
    high3 = _hourly(recs, 0, 'cloud_cover_high', t_key3, 0.0)
    press = _hourly(recs, 0, 'pressure_msl', t_key)
    press3 = _hourly(recs, 0, 'pressure_msl', t_key3)
    precip = _hourly(recs, 0, 'precipitation', t_key, 0.0)

    # 光路：引擎同形状（中高云相加、低云单列）
    p_mid, p_high, p_low = [], [], []
    for j in range(1, len(PATH_DISTANCES)):
        p_mid.append(_hourly(recs, j, 'cloud_cover_mid', t_key, 0.0))
        p_high.append(_hourly(recs, j, 'cloud_cover_high', t_key, 0.0))
        p_low.append(_hourly(recs, j, 'cloud_cover_low', t_key, 0.0))
    path_midhigh = [m + h for m, h in zip(p_mid, p_high)]

    d2, canvas, low_avg, boundary = sc.dim2_path_canvas(
        path_midhigh, p_low, PATH_DISTANCES[1:])

    dims = {
        'dim1': sc.dim1_midhigh_canvas(mid, high, low),
        'dim2': d2,
        'dim3': sc.dim3_precip(precip),
        'dim5': sc.dim5_cloud_trend(mid + high, mid3 + high3),
        'dim6': sc.dim6_pressure(press, press3),
    }
    tw = sum(sc.WEIGHTS[k] for k in DIMS_USED)
    score = sum(sc.WEIGHTS[k] / tw * dims[k] for k in DIMS_USED)

    return {
        'date': day.isoformat(), 'period': period,
        'score': round(score, 1),
        'sun_az': round(sun_az, 1),
        'canvas': canvas, 'low': low_avg,
        'boundary_km': boundary if boundary is not None else '',
        'path_midhigh': ';'.join('%.0f' % v for v in path_midhigh),
        'path_low': ';'.join('%.0f' % v for v in p_low),
        'dim1': dims['dim1'], 'dim2': dims['dim2'], 'dim3': dims['dim3'],
        'dim5': dims['dim5'], 'dim6': dims['dim6'],
    }


def main():
    ap = argparse.ArgumentParser(description='珠三角预报分（ERA5 完美预报口径）')
    ap.add_argument('--start', required=True)
    ap.add_argument('--end')
    ap.add_argument('--lat', type=float, default=23.1291)
    ap.add_argument('--lon', type=float, default=113.2644)
    ap.add_argument('--tz', type=float, default=8.0)
    ap.add_argument('--period', default='both', choices=['AM', 'PM', 'both'])
    ap.add_argument('--out', default=OUT_DEFAULT)
    args = ap.parse_args()

    periods = ['AM', 'PM'] if args.period == 'both' else [args.period]
    d0 = date.fromisoformat(args.start)
    d1 = date.fromisoformat(args.end) if args.end else d0

    rows = []
    cur, n = d0, 0
    while cur <= d1:
        for p in periods:
            try:
                r = score_one(cur, args.lat, args.lon, args.tz, p)
            except Exception as e:
                print('%s %s  ERR %s' % (cur, p, str(e)[:80]), flush=True)
                continue
            n += 1
            if r is None:
                print('%s %s  无事件' % (cur, p), flush=True)
                continue
            rows.append(r)
            print('%s %s  分=%5.1f  dim2=%5.1f  画布=%5.1f  低云=%5.1f'
                  % (r['date'], p, r['score'], r['dim2'], r['canvas'],
                     r['low']), flush=True)
            time.sleep(1.2)          # 档案接口温和限速
        cur += timedelta(days=1)

    if not rows:
        raise SystemExit('没有产出任何预报分（检查日期范围与网络）')
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    cols = ['date', 'period', 'score', 'dim1', 'dim2', 'dim3', 'dim5', 'dim6',
            'canvas', 'low', 'boundary_km', 'sun_az', 'path_midhigh', 'path_low']
    with open(args.out, 'w', newline='', encoding='utf-8') as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction='ignore')
        w.writeheader()
        w.writerows(rows)
    print('-> %s（%d 行 / %d 天次）' % (args.out, len(rows), n))


if __name__ == '__main__':
    main()

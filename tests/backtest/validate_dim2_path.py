# -*- coding: utf-8 -*-
"""dim2（光路城市云况，权重 30%）历史验证 · 光路特征拉取
对 555 天上海标签期，逐日沿真实日落/日出方位角采样光路点，
用 Open-Meteo archive API 拉取各点 ERA5 云量分层（low/mid/high）。

设计：
- 采样距离 PROD_DIST = [50, 130, 260, 460] km/方向（生产 8 点的等距压缩，
  ERA5 0.25° 网格 ≈28km，4 点/方向足以刻画距离加权画布与云边界）
- 逐日一次调用：8 坐标 × 1 天 × 3 变量 ≈ 25 权重，全程 ≈ 1.4 万（分批/断点续跑）
- 断点续跑：结果增量落盘 dim2_path_features.json，重跑自动跳过已完成日期
- 限速：调用间隔 1.2s；429 退避重试

产出：dim2_path_features.json  {date: {sunset: {low/mid/high: [4]}}, sunrise: {...}, az: {...}}
运行：python tests/backtest/validate_dim2_path.py            # 拉取
      python tests/backtest/validate_dim2_path.py --eval     # 仅评估
"""
import json
import math
import os
import sys
import time
from datetime import date, datetime, timedelta

import requests

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

from app.core import astronomy as astro   # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
LABELS = os.path.join(HERE, 'sb_labels.json')
ERA = os.path.join(HERE, 'era5_shanghai.json')
OUT = os.path.join(HERE, 'dim2_path_features.json')

LAT, LON, TZ = 31.2397, 121.4998, 8.0
WINDOW = (date(2022, 6, 24), date(2023, 12, 30))
PROD_DIST = [50, 130, 260, 460]          # km，每个方向
URL = 'https://archive-api.open-meteo.com/v1/archive'
VARS = ['cloud_cover_low', 'cloud_cover_mid', 'cloud_cover_high']
SLEEP = 1.2
MAX_RETRY = 5

EARTH_KM = 6371.0


def dest_point(lat, lon, az_deg, dist_km):
    """从 (lat,lon) 沿方位角 az 走 dist_km 的终点坐标"""
    br = math.radians(az_deg)
    la1, lo1 = math.radians(lat), math.radians(lon)
    dr = dist_km / EARTH_KM
    la2 = math.asin(math.sin(la1) * math.cos(dr)
                    + math.cos(la1) * math.sin(dr) * math.cos(br))
    lo2 = lo1 + math.atan2(math.sin(br) * math.sin(dr) * math.cos(la1),
                           math.cos(dr) - math.sin(la1) * math.sin(la2))
    return round(math.degrees(la2), 4), round(math.degrees(lo2), 4)


def load_features():
    if os.path.exists(OUT):
        return json.load(open(OUT, encoding='utf-8'))
    return {}


def save_features(feat):
    tmp = OUT + '.tmp'
    json.dump(feat, open(tmp, 'w', encoding='utf-8'), ensure_ascii=False)
    os.replace(tmp, OUT)


def event_utc_key(local_str, tz=8):
    """本地事件时间字符串 -> (UTC date, UTC hour)"""
    t = datetime.strptime(local_str, '%Y-%m-%d %H:%M') - timedelta(hours=tz)
    return t.strftime('%Y-%m-%d'), t.hour


def fetch_day(ds, coords, sess):
    """拉取一天的多坐标小时云量。coords: [(lat, lon), ...]"""
    params = {
        'latitude': ','.join(f'{c[0]}' for c in coords),
        'longitude': ','.join(f'{c[1]}' for c in coords),
        'hourly': ','.join(VARS),
        'start_date': ds, 'end_date': ds,
        'timezone': 'UTC',
    }
    for attempt in range(MAX_RETRY):
        try:
            r = sess.get(URL, params=params, timeout=60)
            if r.status_code == 429:
                wait = 60 * (attempt + 1)
                print(f'  429 rate-limited, sleep {wait}s ...', flush=True)
                time.sleep(wait)
                continue
            r.raise_for_status()
            data = r.json()
            if isinstance(data, dict):
                data = [data]
            return data
        except Exception as e:
            print(f'  retry {attempt + 1}: {str(e)[:80]}', flush=True)
            time.sleep(5 * (attempt + 1))
    raise RuntimeError(f'fetch failed {ds}')


def extract_at(day_data, utc_date, utc_hour):
    """从单坐标响应中取指定 UTC 小时的 low/mid/high"""
    hourly = day_data.get('hourly', {})
    times = hourly.get('time', [])
    key = f'{utc_date}T{utc_hour:02d}:00'
    if key not in times:
        return None
    i = times.index(key)
    out = []
    for v in VARS:
        arr = hourly.get(v) or []
        val = arr[i] if i < len(arr) else None
        out.append(float(val) if val is not None else None)
    return out


def main_fetch(limit=None):
    labels = json.load(open(LABELS, encoding='utf-8'))
    feat = load_features()
    sess = requests.Session()
    done = skipped = failed = 0
    cur = WINDOW[0]
    while cur <= WINDOW[1]:
        if limit is not None and done >= limit:
            break
        ds = cur.isoformat()
        cur += timedelta(days=1)
        if ds not in labels or ds in feat:
            skipped += 1
            continue
        d = datetime.strptime(ds, '%Y-%m-%d')
        st = astro.sun_times(d, LAT, LON, TZ)
        az_s, az_r = st.get('sunset_az'), st.get('sunrise_az')
        rec = {'az': {'sunset': az_s, 'sunrise': az_r}}
        # 坐标：sunset 方向 4 点 + sunrise 方向 4 点
        coords, tags = [], []
        for kind, az in (('sunset', az_s), ('sunrise', az_r)):
            if az is None:
                continue
            for dist in PROD_DIST:
                coords.append(dest_point(LAT, LON, az, dist))
                tags.append(kind)
        if not coords:
            feat[ds] = rec
            save_features(feat)
            continue
        try:
            results = fetch_day(ds, coords, sess)
        except RuntimeError as e:
            print(ds, 'FAILED', e, flush=True)
            failed += 1
            save_features(feat)
            continue
        # 按方向分组取值
        for kind, key_time in (('sunset', st.get('sunset')),
                               ('sunrise', st.get('sunrise'))):
            if not key_time:
                continue
            ud, uh = event_utc_key(key_time.strftime('%Y-%m-%d %H:%M'))
            idxs = [i for i, t in enumerate(tags) if t == kind]
            lows, mids, highs = [], [], []
            ok = True
            for i in idxs:
                vals = extract_at(results[i], ud, uh)
                if vals is None or any(v is None for v in vals):
                    ok = False
                    break
                lows.append(vals[0]); mids.append(vals[1]); highs.append(vals[2])
            if ok:
                rec[kind] = {'low': lows, 'mid': mids, 'high': highs,
                             'dist': PROD_DIST}
        feat[ds] = rec
        save_features(feat)
        done += 1
        if done % 20 == 0:
            print(f'  {ds}  done={done}  elapsed_requests ok', flush=True)
        time.sleep(SLEEP)
    print(f'fetch complete: new={done} skipped={skipped} failed={failed} '
          f'total={len(feat)}')


# ------------------------------------------------ 评估

def eval_mode():
    import bisect

    feat = load_features()
    labels = json.load(open(LABELS, encoding='utf-8'))
    era = json.load(open(ERA, encoding='utf-8'))['hourly']
    from app.core import scoring as sc

    def at(var, ds, hour, default=None):
        arr = era.get(var) or []
        key = f'{ds}T{hour:02d}:00'
        if key not in era['time']:
            return default
        i = era['time'].index(key)
        if i >= len(arr) or arr[i] is None:
            return default
        return arr[i]

    rows = []
    for ds, rec in sorted(feat.items()):
        if ds not in labels or rec['az'].get('sunset') is None:
            continue
        d = datetime.strptime(ds, '%Y-%m-%d')
        st = astro.sun_times(d, LAT, LON, TZ)
        ev = {}
        for kind, key_time in (('sunset', st.get('sunset')),
                               ('sunrise', st.get('sunrise'))):
            if not key_time or kind not in rec:
                continue
            hour = key_time.hour
            low_l = at('cloud_cover_low', ds, hour, 0.0)
            mid_l = at('cloud_cover_mid', ds, hour, 0.0)
            high_l = at('cloud_cover_high', ds, hour, 0.0)
            h3 = (hour - 3) % 24
            mid3 = at('cloud_cover_mid', ds, h3, 0.0)
            high3 = at('cloud_cover_high', ds, h3, 0.0)
            press = at('pressure_msl', ds, hour)
            press3 = at('pressure_msl', ds, h3)
            precip_now = at('precipitation', ds, (hour - 1) % 24, 0.0)
            p = rec[kind]
            path_midhigh = [m + h for m, h in zip(p['mid'], p['high'])]
            d2, canvas, low_avg, boundary = sc.dim2_path_canvas(
                path_midhigh, p['low'], p['dist'])
            dims = {
                'dim1': sc.dim1_midhigh_canvas(mid_l, high_l, low_l),
                'dim2': d2,
                'dim3': sc.dim3_precip(precip_now),
                'dim5': sc.dim5_cloud_trend(mid_l + high_l, mid3 + high3),
                'dim6': sc.dim6_pressure(press, press3),
            }
            ev[kind] = {'dims': dims, 'canvas': canvas, 'low_avg': low_avg,
                        'boundary': boundary, 'hour': hour}
        if not ev:
            continue
        rows.append({'date': ds, 'label': labels[ds], 'ev': ev})
    print('rows with path features:', len(rows))

    def auc(pairs):
        pos = sorted(s for s, p in pairs if p)
        neg = sorted(s for s, p in pairs if not p)
        if not pos or not neg:
            return None
        wins = 0.0
        for s in pos:
            lo = bisect.bisect_left(neg, s)
            hi = bisect.bisect_right(neg, s)
            wins += lo + 0.5 * (hi - lo)
        return wins / (len(pos) * len(neg))

    n_pos = sum(1 for r in rows if r['label'] > 0)
    print(f'正例 {n_pos}/{len(rows)}')

    # dim2 单独判别力
    ps = [(r['ev']['sunset']['dims']['dim2'], r['label'] > 0)
          for r in rows if 'sunset' in r['ev']]
    print(f'dim2 单独 AUC（日落）: {auc(ps):.3f}')
    # 光路画布原始值判别力（打分前）
    ps2 = [(r['ev']['sunset']['canvas'], r['label'] > 0)
           for r in rows if 'sunset' in r['ev']]
    print(f'光路画布原始值 AUC  : {auc(ps2):.3f}')
    ps3 = [(r['ev']['sunset']['low_avg'], r['label'] > 0)
           for r in rows if 'sunset' in r['ev']]
    print(f'光路低云均值 AUC    : {auc(ps3):.3f}')

    # 组合对比：4 维基线 vs 5 维含 dim2
    keys4 = ['dim1', 'dim3', 'dim5', 'dim6']
    w4 = {'dim1': 0.23 / 0.37, 'dim3': 0.10 / 0.37, 'dim5': 0.03 / 0.37,
          'dim6': 0.01 / 0.37}
    keys5 = keys4 + ['dim2']
    w5 = {'dim1': 0.23 / 0.67, 'dim3': 0.10 / 0.67, 'dim5': 0.03 / 0.67,
          'dim6': 0.01 / 0.67, 'dim2': 0.30 / 0.67}

    def combo_auc(w, keys):
        pairs = []
        for r in rows:
            s_max = None
            for kind in ('sunset', 'sunrise'):
                if kind not in r['ev']:
                    continue
                dims = r['ev'][kind]['dims']
                s = sum(w[k] * dims[k] for k in keys)
                s_max = s if s_max is None else max(s_max, s)
            pairs.append((s_max, r['label'] > 0))
        return auc(pairs)

    a4 = combo_auc(w4, keys4)
    a5 = combo_auc(w5, keys5)
    print(f'组合 AUC 4 维（无 dim2）: {a4:.3f}')
    print(f'组合 AUC 5 维（含 dim2）: {a5:.3f}')

    # 光路画布分箱烧率（曲线重拟合依据）
    bins = [(0, 20), (20, 35), (35, 50), (50, 65), (65, 80), (80, 95),
            (95, 115), (115, 210)]
    print('\n光路画布分箱 -> 有烧率（日落事件）:')
    cal = []
    for lo, hi in bins:
        xs = [r for r in rows if 'sunset' in r['ev']
              and lo <= r['ev']['sunset']['canvas'] < hi]
        if not xs:
            continue
        rate = sum(1 for r in xs if r['label'] > 0) / len(xs)
        cal.append({'bin': [lo, hi], 'n': len(xs), 'rate': round(rate, 3)})
        print(f'  [{lo:>3},{hi:>3})  n={len(xs):>3}  {rate*100:5.1f}%')

    json.dump({'n_rows': len(rows), 'n_pos': n_pos,
               'auc_dim2_sunset': auc(ps), 'auc_canvas_raw': auc(ps2),
               'auc_lowavg': auc(ps3), 'auc_combo4': a4, 'auc_combo5': a5,
               'canvas_calibration': cal},
              open(os.path.join(HERE, 'dim2_path_validation.json'), 'w',
                   encoding='utf-8'), ensure_ascii=False, indent=1)
    print('saved -> dim2_path_validation.json')


if __name__ == '__main__':
    if '--eval' in sys.argv:
        eval_mode()
    else:
        lim = None
        for a in sys.argv[1:]:
            if a.startswith('--limit='):
                lim = int(a.split('=')[1])
        main_fetch(lim)

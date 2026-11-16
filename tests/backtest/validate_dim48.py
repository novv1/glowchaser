# -*- coding: utf-8 -*-
"""dim4（能见度/颗粒物，11%）与 dim8（高层湿层，10%）历史验证
数据：
- dim8: ERA5 archive 气压层 RH（300/500hPa），单点 555 天一次拉取（≈1110 权重）
- dim4: CAMS EAC4 PM2.5（ADS ~/.cdsapirc，同 v1.9 AOD 拉取方式）
        + ERA5 RH-2m 作雾/低能见度代理（能见度无历史再分析）
评估：
- 各分量单独 AUC、维度分 AUC、组合增量（训练/留出时间序列切分）
- dim8 曲线重拟合候选对比
输出：era5_rh_shanghai.json / cams_pm25_shanghai.nc / dim48_validation.json
运行：python tests/backtest/validate_dim48.py --fetch     # 拉数据
      python tests/backtest/validate_dim48.py --eval      # 评估
"""
import bisect
import json
import os
import subprocess
import sys
from datetime import date, datetime, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

from app.core import astronomy as astro   # noqa: E402
from app.core import scoring as sc        # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
LABELS = os.path.join(HERE, 'sb_labels.json')
ERA = os.path.join(HERE, 'era5_shanghai.json')
RH_OUT = os.path.join(HERE, 'era5_rh_shanghai.json')
RH_NC = os.path.normpath(os.path.join(ROOT, 'data', 'cams_rh_shanghai.nc'))
PM25_NC = os.path.normpath(os.path.join(ROOT, 'data', 'cams_pm25_shanghai.nc'))
OUT = os.path.join(HERE, 'dim48_validation.json')

LAT, LON, TZ = 31.2397, 121.4998, 8.0
WINDOW = (date(2022, 6, 24), date(2023, 12, 30))
SPLIT = '2023-07-01'
URL = 'https://archive-api.open-meteo.com/v1/archive'
RH_VARS = ['relative_humidity_300hPa', 'relative_humidity_500hPa',
           'relative_humidity_700hPa']


def fetch_rh():
    """CAMS EAC4 气压层相对湿度（ERA5 archive 气压层无数据，实测全 None）"""
    import cdsapi
    c = cdsapi.Client()
    req = {
        'variable': ['relative_humidity'],
        'pressure_level': ['300', '500', '700'],
        'date': f'{WINDOW[0].isoformat()}/{WINDOW[1].isoformat()}',
        'time': ['00:00', '03:00', '06:00', '09:00', '12:00', '15:00',
                 '18:00', '21:00'],
        'area': [32.2, 120.0, 30.2, 123.0],
        'data_format': 'netcdf',
    }
    print('requesting CAMS EAC4 relative_humidity @300/500/700hPa ...')
    c.retrieve('cams-global-reanalysis-eac4', req, RH_NC)
    print('saved ->', RH_NC)


def fetch_pm25():
    """CAMS EAC4 PM2.5 经 ADS（参照 v1.9 AOD 拉取方式）"""
    import cdsapi
    c = cdsapi.Client()
    req = {
        'variable': ['particulate_matter_2.5um'],
        'date': f'{WINDOW[0].isoformat()}/{WINDOW[1].isoformat()}',
        'time': ['00:00', '03:00', '06:00', '09:00', '12:00', '15:00',
                 '18:00', '21:00'],
        'area': [32.2, 120.0, 30.2, 123.0],   # N/W/S/E 上海小框
        'format': 'netcdf',
    }
    print('requesting CAMS EAC4 PM2.5 via ADS ...')
    c.retrieve('cams-global-reanalysis-eac4', req, PM25_NC)
    print('saved ->', PM25_NC)


def _interp(x, pts):
    if x <= pts[0][0]:
        return pts[0][1]
    for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
        if x <= x1:
            return y0 + (y1 - y0) * (x - x0) / (x1 - x0)
    return pts[-1][1]


def eval_mode():
    import numpy as np
    import h5py

    labels = json.load(open(LABELS, encoding='utf-8'))
    era = json.load(open(ERA, encoding='utf-8'))['hourly']

    def read_nc_series(nc_path, var_names, nearest=None):
        """读 CAMS nc：返回 (times[utc], {var: series})，气压层维折叠到 var"""
        f = h5py.File(nc_path, 'r')
        kvar = None
        for k in f.keys():
            for vn in var_names:
                if vn in k.lower():
                    kvar = k
        if kvar is None:
            print('nc keys:', list(f.keys()))
            return None, None
        arr = np.asarray(f[kvar][:], dtype=np.float32)
        lat = np.asarray(f['latitude'][:]) if 'latitude' in f else None
        lon = np.asarray(f['longitude'][:]) if 'longitude' in f else None
        vt = np.asarray(f['valid_time'][:] if 'valid_time' in f
                        else f['time'][:])
        plevs = (np.asarray(f['pressure_level'][:]).tolist()
                 if 'pressure_level' in f else None)
        f.close()
        while arr.ndim > 2 and arr.shape[-1] == 1:
            arr = arr[..., 0]
        times = [datetime.fromtimestamp(int(v),
                                        tz=__import__('datetime').timezone.utc
                                        ).replace(tzinfo=None) for v in vt]
        out = {}
        if plevs and arr.ndim == 4:   # (time, plev, lat, lon)
            iy = int(np.abs(lat - LAT).argmin())
            ix = int(np.abs(lon - LON).argmin())
            for j, pl in enumerate(plevs):
                out[int(pl)] = arr[:, j, iy, ix]
        elif arr.ndim == 3:           # (time, lat, lon)
            iy = int(np.abs(lat - LAT).argmin())
            ix = int(np.abs(lon - LON).argmin())
            out[None] = arr[:, iy, ix]
        else:
            out[None] = arr[:, 0] if arr.ndim == 2 else arr
        return times, out

    def series_lookup(times, series, dt_utc):
        if times is None or series is None:
            return None
        # EAC4 3h 步长，取最近槽位
        best_i, best_d = 0, None
        for i in range(0, len(times), 1):
            d = abs((times[i] - dt_utc).total_seconds())
            if best_d is None or d < best_d:
                best_i, best_d = i, d
        v = series[best_i]
        return float(v) if v == v else None

    # PM2.5 序列
    pm_times, pm_series = (None, None)
    if os.path.exists(PM25_NC):
        pm_times, pm_series = read_nc_series(PM25_NC, ['2.5um', '2p5'])
        if pm_series and None in pm_series:
            s = pm_series[None]
            scale = 1e9 if float(np.nanmax(s)) < 1e-3 else 1.0
            pm_series[None] = s * scale
            print(f'PM2.5: {len(pm_times)} steps, scale x{scale:.0e}, '
                  f'max={np.nanmax(s) * scale:.1f}')

    # 气压层 RH 序列（CAMS EAC4，%）
    rh_times, rh_series = (None, None)
    if os.path.exists(RH_NC):
        rh_times, rh_series = read_nc_series(RH_NC, ['r'])  # EAC4 变量短名 r
        if rh_series:
            print('RH plevels:', sorted(rh_series))

    rows = []
    cur = WINDOW[0]
    while cur <= WINDOW[1]:
        ds = cur.isoformat()
        cur += timedelta(days=1)
        if ds not in labels:
            continue
        d = datetime.strptime(ds, '%Y-%m-%d')
        st = astro.sun_times(d, LAT, LON, TZ)
        key_time = st.get('sunset')
        if not key_time:
            continue
        hour = key_time.hour
        dt_utc = key_time - timedelta(hours=TZ)

        def era_at(var, h=None, default=None):
            arr = era.get(var) or []
            hh = hour if h is None else h
            key = f'{ds}T{hh:02d}:00'
            if key not in era['time']:
                return default
            i = era['time'].index(key)
            if i >= len(arr) or arr[i] is None:
                return default
            return arr[i]

        high_l = era_at('cloud_cover_high', default=0.0)
        mid_l = era_at('cloud_cover_mid', default=0.0)
        low_l = era_at('cloud_cover_low', default=0.0)
        rh2m = era_at('relative_humidity_2m')
        rh300 = rh500 = rh700 = None
        if rh_series:
            if 300 in rh_series:
                rh300 = series_lookup(rh_times, rh_series[300], dt_utc)
            if 500 in rh_series:
                rh500 = series_lookup(rh_times, rh_series[500], dt_utc)
            if 700 in rh_series:
                rh700 = series_lookup(rh_times, rh_series[700], dt_utc)
        pm = (series_lookup(pm_times, pm_series[None], dt_utc)
              if pm_series else None)
        rows.append({'date': ds, 'label': labels[ds] > 0,
                     'rh300': rh300, 'rh500': rh500, 'rh700': rh700,
                     'rh2m': rh2m, 'pm25': pm, 'high': high_l,
                     'dim8': sc.dim8_high_moisture(rh300, rh500, high_l),
                     'dim1': sc.dim1_midhigh_canvas(mid_l, high_l, low_l)})
    print('rows:', len(rows))

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

    n_pm = sum(1 for r in rows if r['pm25'] is not None)
    n_rh = sum(1 for r in rows if r['rh300'] is not None)
    print(f'有 PM2.5 的行: {n_pm}   有气压层 RH 的行: {n_rh}')

    for name, key in [('rh300', 'rh300'), ('rh500', 'rh500'),
                      ('rh700', 'rh700'), ('rh2m', 'rh2m'),
                      ('pm25', 'pm25'), ('dim8_cur', 'dim8')]:
        ps = [(r[key], r['label']) for r in rows
              if r.get(key) is not None]
        a = auc(ps)
        print(f'  {name:<10} AUC = {a:.3f}' if a else f'  {name:<10} n/a')

    # PM2.5 分箱烧率
    if n_pm > 200:
        print('\nPM2.5 分箱 -> 有烧率:')
        for lo, hi in [(0, 10), (10, 20), (20, 35), (35, 55), (55, 90),
                       (90, 500)]:
            xs = [r for r in rows if r['pm25'] is not None
                  and lo <= r['pm25'] < hi]
            if not xs:
                continue
            rate = sum(1 for r in xs if r['label']) / len(xs)
            print(f'  [{lo:>3},{hi:>3})  n={len(xs):>3}  {rate*100:5.1f}%')
        print('\nRH300/500 均值分箱 -> 有烧率:')
        for lo, hi in [(0, 20), (20, 40), (40, 60), (60, 80), (80, 101)]:
            xs = [r for r in rows if r['rh300'] is not None
                  and lo <= 0.5 * (r['rh300'] + r['rh500']) < hi]
            if not xs:
                continue
            rate = sum(1 for r in xs if r['label']) / len(xs)
            print(f'  [{lo:>3},{hi:>3})  n={len(xs):>3}  {rate*100:5.1f}%')

    # 组合增量：4 维基线 vs +dim8 vs +dim8+dim4(pm25/rh2m 分量)
    keys4 = ['dim1']
    w4 = {'dim1': 1.0}
    # 用完整基线：重算 dim3/5/6 太重，此处 dim1 近似 + 直接与 baseline 对比
    print('\n(组合增量在 refit_calibration.py 全维统一评估)')

    json.dump({'n_rows': len(rows), 'n_pm25': n_pm, 'n_rh': n_rh,
               'rows': rows},
              open(OUT, 'w', encoding='utf-8'), ensure_ascii=False)
    print('saved ->', OUT)


if __name__ == '__main__':
    if '--fetch' in sys.argv:
        fetch_rh()
        if '--pm25' in sys.argv:
            fetch_pm25()
    elif '--pm25' in sys.argv:
        fetch_pm25()
    elif '--eval' in sys.argv:
        eval_mode()
    else:
        print(__doc__)

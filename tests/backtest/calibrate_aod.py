# -*- coding: utf-8 -*-
"""dim7（CAMS AOD EAC4 再分析）历史验证：AOD vs sunsetbot 标签"""
import json
import os
import statistics as st
import sys
from datetime import date, datetime, timedelta, timezone

import h5py
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, '..', '..'))
sys.path.insert(0, ROOT)

from app.core import astronomy as astro   # noqa: E402
from app.core import scoring as sc        # noqa: E402
from calibrate import score_day, auc      # noqa: E402

LAT, LON = 31.2397, 121.4998
NC = os.path.normpath(os.path.join(HERE, '..', '..', 'data',
                                   'cams_aod_full.nc'))
LABELS = os.path.join(HERE, 'sb_labels.json')
ERA = os.path.join(HERE, 'era5_shanghai.json')


def main():
    labels = json.load(open(LABELS, encoding='utf-8'))
    f = h5py.File(NC, 'r')
    aod_grid = np.asarray(f['aod550'][:], dtype=np.float32)
    lat = np.asarray(f['latitude'][:])
    lon = np.asarray(f['longitude'][:])
    vt = np.asarray(f['valid_time'][:])
    f.close()
    iy = int(np.abs(lat - LAT).argmin())
    ix = int(np.abs(lon - LON).argmin())
    aod_series = aod_grid[:, iy, ix]
    times = [datetime.fromtimestamp(int(v), tz=timezone.utc).replace(
        tzinfo=None) for v in vt]

    def aod_at(dt_utc):
        best_i, best_d = None, None
        for i, t in enumerate(times):
            d = abs((t - dt_utc).total_seconds())
            if best_d is None or d < best_d:
                best_i, best_d = i, d
        v = aod_series[best_i]
        return float(v) if v == v else None

    era = json.load(open(ERA, encoding='utf-8'))['hourly']
    rows = []
    cur = date.fromisoformat('2022-06-24')
    end = date.fromisoformat('2023-12-30')
    while cur <= end:
        ds = cur.isoformat()
        day = cur
        if ds in labels:
            try:
                r = score_day(labels, era, ds)
                st_ = astro.sun_times(datetime(day.year, day.month, day.day),
                                      LAT, LON, 8.0)
                sunset = st_.get('sunset')
                a = None
                if sunset:
                    a = aod_at(sunset - timedelta(hours=8))
                r['aod'] = a
                r['date'] = ds
                r['label'] = labels[ds]
                rows.append(r)
            except Exception as e:
                print(ds, 'ERR', str(e)[:60], flush=True)
        cur += timedelta(days=1)
    print('rows:', len(rows))

    keys4 = ['dim1', 'dim3', 'dim5', 'dim6']
    w4 = {'dim1': 0.23 / 0.37, 'dim3': 0.10 / 0.37, 'dim5': 0.03 / 0.37,
          'dim6': 0.01 / 0.37}
    keys5 = keys4 + ['dim7']
    w5 = {'dim1': 0.23 / 0.49, 'dim3': 0.10 / 0.49, 'dim5': 0.03 / 0.49,
          'dim6': 0.01 / 0.49, 'dim7': 0.12 / 0.49}

    def daily(with7):
        pairs = []
        for r in rows:
            sd, rd = r['sunset']['dims'], r['sunrise']['dims']
            if with7:
                s7 = sc.dim7_aod(r['aod'], r['aod'])
                smax = max(sum(w5[k] * sd.get(k, 0) for k in keys5)
                           - 0 + w5['dim7'] * s7,
                           sum(w5[k] * rd.get(k, 0) for k in keys5)
                           + w5['dim7'] * s7) / sum(w5.values())
            else:
                smax = max(sum(w4[k] * sd[k] for k in keys4) / sum(w4.values()),
                           sum(w4[k] * rd[k] for k in keys4) / sum(w4.values()))
            pairs.append((smax, r['label'] > 0))
        return pairs

    a4 = auc(daily(False))
    a5 = auc(daily(True))
    a7 = auc([(sc.dim7_aod(r['aod'], r['aod']) if r['aod'] is not None else 0,
               r['label'] > 0) for r in rows])
    print(f'AUC 4 维(无AOD): {a4:.3f}')
    print(f'AUC 5 维(含dim7): {a5:.3f}')
    print(f'AUC dim7 单独: {a7:.3f}')
    pos = [r['aod'] for r in rows if r['label'] > 0 and r['aod'] is not None]
    neg = [r['aod'] for r in rows if r['label'] <= 0 and r['aod'] is not None]
    if pos and neg:
        print(f'AOD 均值: 有烧日 {st.mean(pos):.3f} vs 无烧日 {st.mean(neg):.3f}')
    bins = [(0, 0.2), (0.2, 0.4), (0.4, 0.6), (0.6, 0.9), (0.9, 3.0)]
    print('日落时刻 AOD 分箱 -> 天数, 有烧率:')
    for lo, hi in bins:
        xs = [r for r in rows if r['aod'] is not None and lo <= r['aod'] < hi]
        if xs:
            rate = sum(1 for r in xs if r['label'] > 0) / len(xs)
            print(f'  [{lo:.1f},{hi:.1f})  n={len(xs):>3}  {rate*100:5.1f}%')
    json.dump({'a4': a4, 'a5': a5, 'a7': a7,
               'rows': [{'date': r['date'], 'aod': r['aod'],
                         'label': r['label'],
                         'sunset_dims': r['sunset']['dims']} for r in rows]},
              open(os.path.join(HERE, 'dim7_validation.json'), 'w',
                   encoding='utf-8'), ensure_ascii=False, indent=1)
    print('saved -> dim7_validation.json')


if __name__ == '__main__':
    main()

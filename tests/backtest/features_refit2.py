# -*- coding: utf-8 -*-
"""精度提升第二轮：特征工程扩展（时间留出验证，有增量才落地）
候选特征（全部可从 ERA5 逐时序列/已有回测数据离线计算，零 API）：
  S1 cv3   : 事件前 3h 画布（中+高云）标准差——云层抖动/开合
  S2 slope : 前 6h 画布线性趋势斜率（替代 dim5 的单点差分）
  S3 dlow  : 低云 3h 变化量（低云逼近=遮挡风险）
  S4 hfrac : 高云占画布比例（高云=高质量画布）
  S5 dim1±2h : dim1 窗口从 ±1h 扩到 ±2h
方案对比（训练 <2023-07 选优 / 留出 >=2023-07 验证）：
  S0 基线（v2.4 落地版 8 维）
  S1..S5 各自并入基线的增量
输出：features_refit2.json
运行：python tests/backtest/features_refit2.py
"""
import bisect
import json
import math
import os
import sys
from datetime import datetime, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

from app.core import astronomy as astro   # noqa: E402
from app.core import scoring as sc        # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
LAT, LON, TZ = 31.2397, 121.4998, 8.0
SPLIT = '2023-07-01'
KEYS = ['dim1', 'dim2', 'dim3', 'dim5', 'dim6', 'dim7', 'dim8']


def _interp(x, pts):
    if x <= pts[0][0]:
        return pts[0][1]
    for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
        if x <= x1:
            return y0 + (y1 - y0) * (x - x0) / (x1 - x0)
    return pts[-1][1]


def _clamp(x, lo=0.0, hi=100.0):
    return max(lo, min(hi, x))


def build_rows():
    feat2 = json.load(open(os.path.join(HERE, 'dim2_path_features.json'),
                           encoding='utf-8'))
    d7 = {r['date']: r.get('aod')
          for r in json.load(open(os.path.join(HERE, 'dim7_validation.json'),
                                  encoding='utf-8'))['rows']}
    d48 = {r['date']: r for r in json.load(
        open(os.path.join(HERE, 'dim48_validation.json'),
             encoding='utf-8'))['rows']}
    labels = json.load(open(os.path.join(HERE, 'sb_labels.json'),
                            encoding='utf-8'))
    era = json.load(open(os.path.join(HERE, 'era5_shanghai.json'),
                         encoding='utf-8'))['hourly']

    def at(var, ds, hour, default=None):
        arr = era.get(var) or []
        key = f'{ds}T{hour:02d}:00'
        if key not in era['time']:
            return default
        i = era['time'].index(key)
        if i >= len(arr) or arr[i] is None:
            return default
        return arr[i]

    def at_off(ds, hour, off, var, default=0.0):
        t = datetime.strptime(ds, '%Y-%m-%d') + timedelta(hours=hour + off)
        return at(var, t.strftime('%Y-%m-%d'), t.hour, default)

    rows = []
    for ds in sorted(labels):
        if not ('2022-06-24' <= ds <= '2023-12-30'):
            continue
        if ds not in d7 or ds not in d48 or 'sunset' not in feat2.get(ds, {}):
            continue
        d = datetime.strptime(ds, '%Y-%m-%d')
        st = astro.sun_times(d, LAT, LON, TZ)
        key_time = st.get('sunset')
        if not key_time:
            continue
        hour = key_time.hour
        p = feat2[ds]['sunset']
        w = [1.0 / (1.0 + dd / 150.0) for dd in p['dist']]
        tw = sum(w)
        path_midhigh = [m + h for m, h in zip(p['mid'], p['high'])]
        canvas = sum(a * wi for a, wi in zip(path_midhigh, w)) / tw
        low_avg = sum(a * wi for a, wi in zip(p['low'], w)) / tw
        low_l = at('cloud_cover_low', ds, hour, 0.0)
        mid_l = at('cloud_cover_mid', ds, hour, 0.0)
        high_l = at('cloud_cover_high', ds, hour, 0.0)
        press = at('pressure_msl', ds, hour)
        press3 = at('pressure_msl', ds, (hour - 3) % 24)
        precip_now = at('precipitation', ds, (hour - 1) % 24, 0.0)
        r48 = d48[ds]

        # ---- 扩展特征（前序时刻序列）----
        canvases = []
        for off in (0, -1, -2, -3, -4, -5, -6):
            m_ = at_off(ds, hour, off, 'cloud_cover_mid', 0.0)
            h_ = at_off(ds, hour, off, 'cloud_cover_high', 0.0)
            canvases.append(m_ + h_)
        cv3 = (sum((c - sum(canvases[:4]) / 4) ** 2 for c in canvases[:4])
               / 4) ** 0.5
        slope6 = (canvases[0] - canvases[6]) / 6.0
        dlow3 = low_l - at_off(ds, hour, -3, 'cloud_cover_low', 0.0)
        hfrac = high_l / (mid_l + high_l + 1.0)
        # dim1 ±2h 窗口
        def at_w2(var):
            vals = []
            for off in (-2, -1, 0, 1, 2):
                v = at_off(ds, hour, off, var, None)
                if v is not None:
                    vals.append(v)
            return sum(vals) / len(vals) if vals else 0.0
        mid3 = at_off(ds, hour, -3, 'cloud_cover_mid', 0.0)
        high3 = at_off(ds, hour, -3, 'cloud_cover_high', 0.0)

        rows.append({
            'date': ds, 'label': labels[ds] > 0,
            'canvas': canvas, 'low_avg': low_avg,
            'dim1': sc.dim1_midhigh_canvas(mid_l, high_l, low_l),
            'dim1_w2': sc.dim1_midhigh_canvas(at_w2('cloud_cover_mid'),
                                              at_w2('cloud_cover_high'),
                                              at_w2('cloud_cover_low')),
            'dim2': _interp(canvas, [(0, 42), (30, 55), (50, 100), (90, 100),
                                     (120, 75), (160, 40), (210, 20)]) * 0.65
                    + (100.0 if low_avg <= 25 else
                       _clamp(100 - (low_avg - 25) * 2.2, 5, 100)) * 0.35,
            'dim3': sc.dim3_precip(precip_now),
            'dim5': sc.dim5_cloud_trend(mid_l + high_l, mid3 + high3),
            'dim6': sc.dim6_pressure(press, press3),
            'aod': d7[ds], 'rh300': r48.get('rh300'),
            'rh500': r48.get('rh500'), 'high': high_l,
            'cv3': cv3, 'slope6': slope6, 'dlow3': dlow3, 'hfrac': hfrac,
        })
    for r in rows:
        r['dim7'] = sc.dim7_aod(r['aod'], r['aod'])
        r['dim8'] = sc.dim8_high_moisture(r['rh300'], r['rh500'], r['high'])
    return rows


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


def main():
    rows = build_rows()
    train = [r for r in rows if r['date'] < SPLIT]
    hold = [r for r in rows if r['date'] >= SPLIT]
    print(f'rows={len(rows)} train={len(train)} hold={len(hold)}')

    W = dict(sc.WEIGHTS)

    def combo(r, extra=None, dim1key='dim1', dim5fn=None):
        s = sum(W[k] * r[k] for k in KEYS)
        if dim5fn:
            s = s - W['dim5'] * r['dim5'] + W['dim5'] * dim5fn(r)
        if extra:
            s += extra[0] * extra[1](r)
        return s

    def ev(fn, **kw):
        a_tr = auc([(fn(r, **kw), r['label']) for r in train])
        a_ho = auc([(fn(r, **kw), r['label']) for r in hold])
        return a_tr, a_ho

    results = []

    # S0 基线
    results.append(('S0_baseline', *ev(combo)))

    # S1 cv3 附加维度（权重网格）
    best1 = (None, 0)
    for wcv in (0.02, 0.04, 0.06, 0.08):
        a_tr, a_ho = ev(combo, extra=(wcv, lambda r: _clamp(r['cv3'] * 6, 0, 100)))
        if a_tr > best1[1]:
            best1 = (wcv, a_tr, a_ho)
    if best1[0]:
        results.append((f'S1_cv3(w={best1[0]})', best1[1], best1[2]))

    # S2 dim5 -> 斜率版（斜率绝对值放大，k 网格）
    best2 = (None, 0)
    for k in (8, 12, 16, 24):
        a_tr, a_ho = ev(combo, dim5fn=lambda r, k=k:
                        _clamp(30 + abs(r['slope6']) * k, 30, 100))
        if a_tr > best2[1]:
            best2 = (k, a_tr, a_ho)
    if best2[0]:
        results.append((f'S2_slope5(k={best2[0]})', best2[1], best2[2]))

    # S3 低云变化并入 dim1（低云逼近惩罚）：dim1' = dim1 - max(0,dlow3)*k
    best3 = (None, 0)
    for k in (0.5, 1.0, 1.5):
        a_tr, a_ho = ev(combo, extra=(-k, lambda r: max(0.0, r['dlow3']) * 100 / 100))
        if a_tr > best3[1]:
            best3 = (k, a_tr, a_ho)
    if best3[0]:
        results.append((f'S3_dlow(k={best3[0]})', best3[1], best3[2]))

    # S4 高云占比 bonus 并入 dim1：dim1' = dim1 + (hfrac-0.5)*k（高云占比高加分）
    best4 = (None, 0)
    for k in (10, 20, 30):
        a_tr, a_ho = ev(combo, extra=(k, lambda r: r['hfrac'] - 0.5))
        if a_tr > best4[1]:
            best4 = (k, a_tr, a_ho)
    if best4[0]:
        results.append((f'S4_hfrac(k={best4[0]})', best4[1], best4[2]))

    # S5 dim1 用 ±2h 窗口
    a_tr, a_ho = ev(combo, dim1key='dim1_w2')
    results.append(('S5_dim1w2', a_tr, a_ho))

    print(f"\n{'方案':<20}{'训练AUC':>8}{'留出AUC':>8}")
    for name, a_tr, a_ho in sorted(results, key=lambda x: -x[2]):
        print(f'{name:<20}{a_tr:>8.3f}{a_ho:>8.3f}')

    base_ho = dict((n, (t, h)) for n, t, h in results)['S0_baseline'][1]
    winners = [(n, h) for n, t, h in results
               if n != 'S0_baseline' and h > base_ho + 0.002]
    print(f'\n基线留出 {base_ho:.3f}；有增量（>+0.002）的方案: '
          f'{winners if winners else "无——维持 v2.4 落地常量"}')

    json.dump({'split': SPLIT, 'results': [
        {'name': n, 'auc_train': round(t, 3), 'auc_hold': round(h, 3)}
        for n, t, h in results]},
        open(os.path.join(HERE, 'features_refit2.json'), 'w',
             encoding='utf-8'), ensure_ascii=False, indent=1)
    print('saved -> features_refit2.json')


if __name__ == '__main__':
    main()

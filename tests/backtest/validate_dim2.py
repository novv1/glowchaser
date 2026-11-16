# -*- coding: utf-8 -*-
"""dim2 光路维度历史验证：
沿上海日落平均方位(~262°)取 3 个上游点（80/180/300km），
检验"光路上游云况"相对"本地云况"的增量判别力。
方向随季节摆动(245-292°)，此处用平均方位近似。"""
import bisect
import json
import math
import os
import sys
from datetime import date, datetime, timedelta

import requests

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

from app.core import astronomy as astro   # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
LAT, LON, TZ = 31.2397, 121.4998, 8.0
START, END = '2022-06-24', '2023-12-30'
URL = 'https://archive-api.open-meteo.com/v1/archive'
OUT = os.path.join(HERE, 'era5_path.json')
LABELS = os.path.join(HERE, 'sb_labels.json')


def dest(lat, lon, bearing, dist_km):
    r = 6371.0
    br = math.radians(bearing)
    p1 = math.radians(lat)
    l1 = math.radians(lon)
    dr = dist_km / r
    p2 = math.asin(math.sin(p1) * math.cos(dr)
                   + math.cos(p1) * math.sin(dr) * math.cos(br))
    l2 = l1 + math.atan2(math.sin(br) * math.sin(dr) * math.cos(p1),
                         math.cos(dr) - math.sin(p1) * math.sin(p2))
    return round(math.degrees(p2), 4), round(math.degrees(l2), 4)


def fetch_path():
    pts = [dest(LAT, LON, 262, d) for d in (80, 180, 300)]
    lats = ','.join(str(p[0]) for p in pts)
    lons = ','.join(str(p[1]) for p in pts)
    params = {
        'latitude': lats, 'longitude': lons,
        'hourly': 'cloud_cover_mid,cloud_cover_high',
        'start_date': START, 'end_date': END,
        'timezone': 'Asia/Shanghai',
    }
    print('fetching path points', pts, '...')
    r = requests.get(URL, params=params, timeout=300)
    r.raise_for_status()
    data = r.json()
    items = data if isinstance(data, list) else [data]
    out = []
    for it in items:
        out.append({'lat': it['latitude'], 'lon': it['longitude'],
                    'hourly': it['hourly']})
    json.dump(out, open(OUT, 'w', encoding='utf-8'), ensure_ascii=False)
    print('saved ->', OUT, f'({len(out)} points)')
    return out


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


def logistic_fit(xs, ys, lr=0.3, epochs=400):
    """二维特征逻辑回归（含截距），返回权重"""
    n = len(xs)
    w = [0.0, 0.0, 0.0]
    for _ in range(epochs):
        gw = [0.0, 0.0, 0.0]
        for x, y in zip(xs, ys):
            z = w[0] + w[1] * x[0] + w[2] * x[1]
            p = 1 / (1 + math.exp(-max(-30, min(30, z))))
            e = p - y
            gw[0] += e
            gw[1] += e * x[0]
            gw[2] += e * x[1]
        w = [wi - lr * gi / n for wi, gi in zip(w, gw)]
    return w


def zscore(vals):
    mu = sum(vals) / len(vals)
    var = sum((v - mu) ** 2 for v in vals) / len(vals)
    sd = math.sqrt(var) or 1.0
    return [(v - mu) / sd for v in vals]


def main():
    if not os.path.exists(OUT):
        fetch_path()
    path_pts = json.load(open(OUT, encoding='utf-8'))
    labels = json.load(open(LABELS, encoding='utf-8'))
    era = json.load(open(os.path.join(HERE, 'era5_shanghai.json'),
                         encoding='utf-8'))['hourly']
    times0 = era['time']

    d0 = date.fromisoformat(START)
    d1 = date.fromisoformat(END)
    rows = []
    cur = d0
    while cur <= d1:
        ds = cur.isoformat()
        if ds in labels:
            st = astro.sun_times(datetime(cur.year, cur.month, cur.day),
                                 LAT, LON, TZ)
            h = st['sunset'].hour
            i0 = times0.index(f'{ds}T{h:02d}:00')

            def local(v):
                x = (era[v] or [])[i0]
                return x if x is not None else 0.0
            l_low = local('cloud_cover_low')
            l_mid = local('cloud_cover_mid')
            l_high = local('cloud_cover_high')
            l_canvas = l_mid + l_high
            # 光路画布：距离加权 (1/(1+d/150))
            ws, cs = [], []
            for k, pt in enumerate(path_pts):
                ph = pt['hourly']
                t = ph['time']
                j = t.index(f'{ds}T{h:02d}:00')
                m = (ph['cloud_cover_mid'] or [])[j] or 0.0
                hh = (ph['cloud_cover_high'] or [])[j] or 0.0
                w = 1.0 / (1.0 + (80 + 110 * k) / 150.0)
                ws.append(w)
                cs.append((m + hh) * w)
            p_canvas = sum(cs) / sum(ws)
            rows.append({'date': ds, 'label': labels[ds],
                         'l_canvas': l_canvas, 'l_low': l_low,
                         'p_canvas': round(p_canvas, 1)})
        cur += timedelta(days=1)
    print('rows:', len(rows))

    y = [1 if r['label'] > 0 else 0 for r in rows]
    # 单特征 AUC
    a_local = auc([(r['l_canvas'], r['label'] > 0) for r in rows])
    a_path = auc([(r['p_canvas'], r['label'] > 0) for r in rows])
    print(f'AUC 本地画布(中+高): {a_local:.3f}')
    print(f'AUC 光路画布(加权): {a_path:.3f}')

    # 二维逻辑回归组合
    f1 = zscore([r['l_canvas'] for r in rows])
    f2 = zscore([r['p_canvas'] for r in rows])
    xs = list(zip(f1, f2))
    w = logistic_fit(xs, y)
    combo = [w[0] + w[1] * a + w[2] * b for (a, b) in xs]
    a_combo = auc(list(zip(combo, y)))
    print(f'AUC 组合(本地+光路, logistic): {a_combo:.3f}  weights={["%.2f" % x for x in w]}')
    # 光路画布分布对比
    import statistics as st
    pos_p = [r['p_canvas'] for r in rows if r['label'] > 0]
    neg_p = [r['p_canvas'] for r in rows if r['label'] <= 0]
    print(f'光路画布均值: 正例 {st.mean(pos_p):.1f}% vs 负例 {st.mean(neg_p):.1f}%')
    json.dump({'a_local': a_local, 'a_path': a_path, 'a_combo': a_combo,
               'logistic_w': w, 'rows': rows},
              open(os.path.join(HERE, 'dim2_validation.json'), 'w',
                   encoding='utf-8'), ensure_ascii=False)


if __name__ == '__main__':
    main()

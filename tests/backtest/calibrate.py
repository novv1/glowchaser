# -*- coding: utf-8 -*-
"""回测校准 v1：ERA5 历史 + sunsetbot 每日质量标签
- 用本引擎的天文模块与可计算维度（①③⑤⑥）对每个日期评分
- 维度②光路/④能见度/⑦AOD/⑧气压层湿度 历史数据不可得 -> 中性化，权重归一
- 输出：判别力（AUC）、校准曲线、各维度单独 AUC
"""
import json
import math
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

from app.core import astronomy as astro          # noqa: E402
from app.core import scoring as sc               # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
LAT, LON, TZ = 31.2397, 121.4998, 8.0
WINDOW = ('2022-06-24', '2023-12-30')

# 参与校准的维度与权重（归一化用）
CAL_DIMS = {'dim1': 0.23, 'dim3': 0.10, 'dim5': 0.03, 'dim6': 0.01}


def load_data():
    labels = json.load(open(os.path.join(HERE, 'sb_labels.json'), encoding='utf-8'))
    era = json.load(open(os.path.join(HERE, 'era5_shanghai.json'),
                         encoding='utf-8'))['hourly']
    return labels, era


def hour_idx(times, date_str, hour):
    key = f'{date_str}T{hour:02d}:00'
    if key in times:
        return times.index(key)
    return None


def series_at(era, var, date_str, hour, default=None):
    arr = era.get(var) or []
    times = era['time']
    i = hour_idx(times, date_str, hour)
    if i is None or i >= len(arr) or arr[i] is None:
        return default
    return arr[i]


def score_day(labels, era, date_str):
    """单日：计算日落/日出事件分，返回 dict"""
    d = __import__('datetime').datetime.strptime(date_str, '%Y-%m-%d')
    out = {}
    for kind in ('sunset', 'sunrise'):
        st = astro.sun_times(d, LAT, LON, TZ)
        key_time = st['sunset'] if kind == 'sunset' else st['sunrise']
        if not key_time:
            continue
        hour = key_time.hour
        low = series_at(era, 'cloud_cover_low', date_str, hour, 0.0)
        mid = series_at(era, 'cloud_cover_mid', date_str, hour, 0.0)
        high = series_at(era, 'cloud_cover_high', date_str, hour, 0.0)
        h3 = (hour - 3) % 24
        mid3 = series_at(era, 'cloud_cover_mid', date_str, h3, 0.0)
        high3 = series_at(era, 'cloud_cover_high', date_str, h3, 0.0)
        press = series_at(era, 'pressure_msl', date_str, hour)
        press3 = series_at(era, 'pressure_msl', date_str, h3)
        # 事件前一小时降水（"正在下雨"代理）
        precip_now = series_at(era, 'precipitation', date_str, (hour - 1) % 24, 0.0)
        dims = {
            'dim1': sc.dim1_midhigh_canvas(mid, high, low),
            'dim3': sc.dim3_precip(precip_now),
            'dim5': sc.dim5_cloud_trend(mid + high, mid3 + high3),
            'dim6': sc.dim6_pressure(press, press3),
        }
        wsum = sum(CAL_DIMS.values())
        total = sum(CAL_DIMS[k] * v for k, v in dims.items()) / wsum
        out[kind] = {'score': round(total, 1), 'dims': dims,
                     'cloud': (round(low), round(mid), round(high)),
                     'hour': hour}
    return out


def auc(pairs):
    """pairs: [(score, is_positive)]  Mann-Whitney AUC（并列分记 0.5）"""
    pos = sorted(s for s, p in pairs if p)
    neg = sorted(s for s, p in pairs if not p)
    if not pos or not neg:
        return None
    import bisect
    wins = 0.0
    for s in pos:
        lo = bisect.bisect_left(neg, s)
        hi = bisect.bisect_right(neg, s)
        wins += lo + 0.5 * (hi - lo)   # 严格小于 + 0.5*并列
    return wins / (len(pos) * len(neg))


def main():
    labels, era = load_data()
    d0 = __import__('datetime').datetime.strptime(WINDOW[0], '%Y-%m-%d')
    d1 = __import__('datetime').datetime.strptime(WINDOW[1], '%Y-%m-%d')
    results = []
    cur = d0
    while cur <= d1:
        ds = cur.strftime('%Y-%m-%d')
        if ds in labels:
            try:
                r = score_day(labels, era, ds)
                r['date'] = ds
                r['label'] = labels[ds]
                results.append(r)
            except Exception as e:
                print(ds, 'ERR', e)
        cur += __import__('datetime').timedelta(days=1)
    print('scored days:', len(results))

    daily = [{'date': r['date'], 'score': max(r['sunset']['score'],
                                              r['sunrise']['score']),
              'label': r['label']} for r in results]
    pairs = [(x['score'], x['label'] > 0) for x in daily]
    a = auc(pairs)
    n_pos = sum(1 for _, p in pairs if p)
    print(f'\n== 每日判别 ==  正例(质量>0): {n_pos}/{len(pairs)} '
          f'({n_pos/len(pairs)*100:.1f}%)  AUC={a:.3f}' if a else 'AUC n/a')

    # 校准曲线：分数分箱 -> 实际出现率
    bins = [(0, 15), (15, 25), (25, 35), (35, 45), (45, 55), (55, 70), (70, 101)]
    print('\n分数分箱 -> 实际有烧率 (校准曲线):')
    cal = []
    for lo, hi in bins:
        xs = [x for x in daily if lo <= x['score'] < hi]
        if not xs:
            continue
        rate = sum(1 for x in xs if x['label'] > 0) / len(xs)
        cal.append({'bin': f'{lo}-{hi}', 'n': len(xs), 'rate': round(rate, 3)})
        print(f"  [{lo:>3},{hi:>3})  n={len(xs):>3}  实际有烧率={rate*100:5.1f}%")
    json.dump({'daily': daily, 'auc': a, 'calibration': cal},
              open(os.path.join(HERE, 'calibration_v1.json'), 'w',
                   encoding='utf-8'), ensure_ascii=False, indent=1)

    # 各维度单独判别力
    print('\n各维度单独 AUC（日落事件）:')
    for k in ['dim1', 'dim3', 'dim5', 'dim6']:
        ps = [(r['sunset']['dims'][k], r['label'] > 0) for r in results]
        a_k = auc(ps)
        print(f'  {k}: {a_k:.3f}' if a_k else f'  {k}: n/a')
    # 云量结构特征单独看
    for name, idx in [('low', 0), ('mid', 1), ('high', 2)]:
        ps = [(r['sunset']['cloud'][idx], r['label'] > 0) for r in results]
        print(f'  日落{name}云 AUC: {auc(ps):.3f}')


if __name__ == '__main__':
    main()

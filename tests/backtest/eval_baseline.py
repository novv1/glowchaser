# -*- coding: utf-8 -*-
"""统一基线评估（精度优化项目 · 第 1 步）
对当前引擎在 555 天上海回测集上输出标准化指标，作为全部优化动作的对比基线：
- 各维度单独 AUC（可用历史特征的维度）
- 组合分 AUC（当前引擎权重归一）+ Brier 分（经 CALIB_PTS 校准映射）
- 校准曲线（分数分箱 -> 实际烧率）
- 特征工程变体：事件时刻单小时 vs ±1h 窗口平均（仅本地 ERA5 特征，零额外 API）

输出：tests/backtest/baseline_metrics.json
运行：python tests/backtest/eval_baseline.py
"""
import bisect
import json
import os
import sys
from datetime import date, datetime, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

from app.core import astronomy as astro   # noqa: E402
from app.core import scoring as sc        # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
LABELS = os.path.join(HERE, 'sb_labels.json')
ERA = os.path.join(HERE, 'era5_shanghai.json')
DIM7 = os.path.join(HERE, 'dim7_validation.json')
OUT = os.path.join(HERE, 'baseline_metrics.json')

LAT, LON, TZ = 31.2397, 121.4998, 8.0
WINDOW = (date(2022, 6, 24), date(2023, 12, 30))

# 当前引擎权重（归一前）
ENGINE_W = dict(sc.WEIGHTS)


# ------------------------------------------------ 数据加载

def load():
    labels = json.load(open(LABELS, encoding='utf-8'))
    era = json.load(open(ERA, encoding='utf-8'))['hourly']
    return labels, era


def at(era, var, date_str, hour, default=None):
    arr = era.get(var) or []
    key = f'{date_str}T{hour:02d}:00'
    if key not in era['time']:
        return default
    i = era['time'].index(key)
    if i >= len(arr) or arr[i] is None:
        return default
    return arr[i]


def mean_at(era, var, date_str, hour, span=1, default=None):
    """±span 小时窗口平均（跨日进位）"""
    vals = []
    d = datetime.strptime(date_str, '%Y-%m-%d')
    for off in range(-span, span + 1):
        t = d + timedelta(hours=hour + off)
        v = at(era, var, t.strftime('%Y-%m-%d'), t.hour)
        if v is not None:
            vals.append(v)
    return sum(vals) / len(vals) if vals else default


# ------------------------------------------------ 单日特征/打分

def cloud_features(era, date_str, hour):
    """返回单时刻与 ±1h 平均两套云量特征"""
    f = {}
    for var in ('cloud_cover_low', 'cloud_cover_mid', 'cloud_cover_high'):
        f[var] = at(era, var, date_str, hour, 0.0)
        f[var + '_w'] = mean_at(era, var, date_str, hour, 1, 0.0)
    return f


def score_dims(mid, high, low, mid3, high3, press, press3, precip_now):
    return {
        'dim1': sc.dim1_midhigh_canvas(mid, high, low),
        'dim3': sc.dim3_precip(precip_now),
        'dim5': sc.dim5_cloud_trend(mid + high, mid3 + high3),
        'dim6': sc.dim6_pressure(press, press3),
    }


def day_record(labels, era, ds, aod_by_date=None):
    d = datetime.strptime(ds, '%Y-%m-%d')
    rec = {}
    for kind in ('sunset', 'sunrise'):
        st = astro.sun_times(d, LAT, LON, TZ)
        key_time = st.get('sunset' if kind == 'sunset' else 'sunrise')
        if not key_time:
            continue
        hour = key_time.hour
        h3 = (hour - 3) % 24
        cf = cloud_features(era, ds, hour)
        cfw = cloud_features(era, ds, hour)  # 单时刻与窗口两套
        mid3 = at(era, 'cloud_cover_mid', ds, h3, 0.0)
        high3 = at(era, 'cloud_cover_high', ds, h3, 0.0)
        press = at(era, 'pressure_msl', ds, hour)
        press3 = at(era, 'pressure_msl', ds, h3)
        precip_now = at(era, 'precipitation', ds, (hour - 1) % 24, 0.0)
        dims = score_dims(cf['cloud_cover_mid'], cf['cloud_cover_high'],
                          cf['cloud_cover_low'], mid3, high3,
                          press, press3, precip_now)
        # 窗口平均变体（dim5 仍用单时刻 trend，窗口化在组合层另行测试）
        dims_w = score_dims(cfw['cloud_cover_mid_w'], cfw['cloud_cover_high_w'],
                            cfw['cloud_cover_low_w'], mid3, high3,
                            press, press3, precip_now)
        rec[kind] = {'hour': hour, 'dims': dims, 'dims_w': dims_w,
                     'cloud': (round(cf['cloud_cover_low']),
                               round(cf['cloud_cover_mid']),
                               round(cf['cloud_cover_high']))}
    if 'sunset' not in rec and 'sunrise' not in rec:
        return None
    rec['date'] = ds
    rec['label'] = labels[ds]
    if aod_by_date is not None:
        rec['aod'] = aod_by_date.get(ds)
    return rec


# ------------------------------------------------ 指标

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


def brier(rows, weight_fn):
    """加权组合分 -> CALIB_PTS 概率 -> Brier（rows 含 dims dict + label）"""
    tot, n = 0.0, 0
    for r in rows:
        s = weight_fn(r)
        if s is None:
            continue
        p = sc.calibrated_probability(s) / 100.0
        y = 1.0 if r['label'] > 0 else 0.0
        tot += (p - y) ** 2
        n += 1
    return tot / n if n else None


def cal_curve(rows, weight_fn, bins):
    out = []
    for lo, hi in bins:
        xs = [weight_fn(r) for r in rows]
        xs = [s for s, r in zip(xs, rows)
              if s is not None and lo <= s < hi]
        ys = [r['label'] > 0 for r in rows
              if weight_fn(r) is not None and lo <= weight_fn(r) < hi]
        if not ys:
            continue
        out.append({'bin': [lo, hi], 'n': len(ys),
                    'rate': round(sum(ys) / len(ys), 3)})
    return out


def norm_w(keys):
    tw = sum(ENGINE_W[k] for k in keys)
    return {k: ENGINE_W[k] / tw for k in keys}


# ------------------------------------------------ 主流程

def main():
    labels, era = load()
    # dim7 历史 AOD（复用已验证数据）
    aod_by_date = {}
    if os.path.exists(DIM7):
        for r in json.load(open(DIM7, encoding='utf-8'))['rows']:
            aod_by_date[r['date']] = r.get('aod')

    rows = []
    cur = WINDOW[0]
    while cur <= WINDOW[1]:
        ds = cur.isoformat()
        if ds in labels:
            try:
                r = day_record(labels, era, ds, aod_by_date)
                if r:
                    rows.append(r)
            except Exception as e:
                print(ds, 'ERR', e)
        cur += timedelta(days=1)
    print('scored days:', len(rows))

    # 组合权重方案：4 维本地（当前基线）/ 5 维含 dim7
    keys4 = ['dim1', 'dim3', 'dim5', 'dim6']
    keys5 = keys4 + ['dim7']
    w4, w5 = norm_w(keys4), norm_w(keys5)

    def daily_pairs(w, keys, attr='dims'):
        ps = []
        for r in rows:
            s_max = None
            for kind in ('sunset', 'sunrise'):
                if kind not in r:
                    continue
                dims = r[kind][attr]
                s = sum(w[k] * dims[k] for k in keys if k in dims)
                if 'dim7' in w and r.get('aod') is not None:
                    s += w['dim7'] * sc.dim7_aod(r['aod'], r['aod'])
                s_max = s if s_max is None else max(s_max, s)
            ps.append((s_max, r['label'] > 0))
        return ps

    def daily_rows_for_brier(w, keys):
        out = []
        for r in rows:
            s_max, best = None, None
            for kind in ('sunset', 'sunrise'):
                if kind not in r:
                    continue
                dims = r[kind]['dims']
                s = sum(w[k] * dims[k] for k in keys if k in dims)
                if 'dim7' in w and r.get('aod') is not None:
                    s += w['dim7'] * sc.dim7_aod(r['aod'], r['aod'])
                if s_max is None or s > s_max:
                    s_max, best = s, {'label': r['label']}
            if s_max is not None:
                out.append({'score': s_max, 'label': r['label']})
        return out

    a4 = auc(daily_pairs(w4, keys4))
    a4w = auc(daily_pairs(w4, keys4, attr='dims_w'))
    has_aod = sum(1 for r in rows if r.get('aod') is not None)
    a5 = auc(daily_pairs(w5, keys5)) if has_aod > 100 else None
    b4 = brier(daily_rows_for_brier(w4, keys4), lambda r: r['score'])
    aod_a = auc([(sc.dim7_aod(r['aod'], r['aod']), r['label'] > 0)
                 for r in rows if r.get('aod') is not None])

    n_pos = sum(1 for r in rows if r['label'] > 0)
    base_rate = n_pos / len(rows)
    print(f'\n== 基线（当前引擎常量，{WINDOW[0]}~{WINDOW[1]}）==')
    print(f'正例 {n_pos}/{len(rows)}（基率 {base_rate*100:.1f}%）')
    print(f'组合 AUC（4 维本地）      : {a4:.3f}')
    print(f'组合 AUC（±1h 窗口特征）  : {a4w:.3f}' if a4w else '窗口变体 n/a')
    print(f'组合 AUC（5 维含 dim7）   : {a5:.3f}' if a5 else 'dim7 样本不足')
    print(f'Brier（4 维, CALIB_PTS）  : {b4:.4f}  '
          f'(气候基线 {base_rate*(1-base_rate):.4f})')
    print(f'dim7 单独 AUC            : {aod_a:.3f}')

    print('\n各维度单独 AUC（日落事件）:')
    dim_auc = {}
    for k in keys4:
        ps = [(r['sunset']['dims'][k], r['label'] > 0)
              for r in rows if 'sunset' in r]
        dim_auc[k] = auc(ps)
        print(f'  {k}: {dim_auc[k]:.3f}')
    for name, idx in [('low', 0), ('mid', 1), ('high', 2)]:
        ps = [(r['sunset']['cloud'][idx], r['label'] > 0)
              for r in rows if 'sunset' in r]
        dim_auc[f'cloud_{name}'] = auc(ps)
        print(f'  日落{name}云: {auc(ps):.3f}')

    bins = [(0, 15), (15, 25), (25, 35), (35, 45), (45, 55), (55, 70),
            (70, 101)]
    brier_rows = daily_rows_for_brier(w4, keys4)
    cal = cal_curve(brier_rows, lambda r: r['score'], bins)
    print('\n校准曲线（4 维组合分 -> 实际烧率）:')
    for c in cal:
        print(f"  [{c['bin'][0]:>3},{c['bin'][1]:>3})  n={c['n']:>3}  "
              f"{c['rate']*100:5.1f}%")

    json.dump({
        'window': [str(WINDOW[0]), str(WINDOW[1])],
        'n_days': len(rows), 'n_pos': n_pos, 'base_rate': round(base_rate, 4),
        'metrics': {
            'auc_4dim': a4, 'auc_4dim_window1h': a4w, 'auc_5dim_dim7': a5,
            'brier_4dim_calibrated': b4,
            'brier_climatology': round(base_rate * (1 - base_rate), 4),
            'auc_dim7_alone': aod_a, 'dim_auc_sunset': dim_auc,
        },
        'calibration_curve': cal,
    }, open(OUT, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    print(f'\nsaved -> {OUT}')


if __name__ == '__main__':
    main()

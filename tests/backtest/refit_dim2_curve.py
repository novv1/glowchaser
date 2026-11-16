# -*- coding: utf-8 -*-
"""dim2 打分曲线重拟合（基于 555 天光路历史特征）
方法：
1. 光路画布分箱烧率 -> 实证定标曲线（候选 C1/C2）
2. 时间序列切分：2022-06~2023-06 训练选优，2023-07~12 留出验证
3. 网格对比：画布曲线 × 低云惩罚 × 画布/低云配比，目标 dim2 单独 AUC 与组合 5 维 AUC
输出：dim2_curve_refit.json
运行：python tests/backtest/refit_dim2_curve.py
"""
import bisect
import json
import os
import sys
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

from app.core import astronomy as astro   # noqa: E402
from app.core import scoring as sc        # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
LABELS = os.path.join(HERE, 'sb_labels.json')
ERA = os.path.join(HERE, 'era5_shanghai.json')
FEAT = os.path.join(HERE, 'dim2_path_features.json')

LAT, LON, TZ = 31.2397, 121.4998, 8.0
SPLIT = '2023-07-01'   # 留出集起点（后 ~6 个月）


def _interp(x, pts):
    if x <= pts[0][0]:
        return pts[0][1]
    for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
        if x <= x1:
            return y0 + (y1 - y0) * (x - x0) / (x1 - x0)
    return pts[-1][1]


def _clamp(x, lo=0.0, hi=100.0):
    return max(lo, min(hi, x))


# ---- 候选曲线（画布：中+高云合计 %）----
CURVES = {
    # 现状（trapz 30~70 满分）：作为基线复现
    'C0_cur': lambda c: sc._trapz(c, 30, 70, 0, 100, floor=15),
    # C1 实证定标（分箱烧率归一，峰值带 50~95）
    'C1_emp': lambda c: _interp(c, [(0, 45), (25, 50), (40, 75), (50, 100),
                                    (95, 100), (115, 88), (150, 45), (210, 25)]),
    # C2 平滑版（更宽峰值带，减少拐点过拟合）
    'C2_smooth': lambda c: _interp(c, [(0, 42), (30, 55), (50, 100), (90, 100),
                                       (120, 75), (160, 40), (210, 20)]),
    # C3 偏保守（峰值带 60~110，对应薄云幕厚画布）
    'C3_wide': lambda c: _interp(c, [(0, 40), (35, 60), (60, 100), (110, 100),
                                     (140, 55), (210, 20)]),
}

# ---- 低云惩罚曲线（低云均值 % -> 0~100）----
LOWS = {
    'L0_cur': lambda l: _clamp(100 - max(0.0, l - 30) * 2.0, 5, 100),
    'L1_hard': lambda l: 100.0 if l <= 25 else (
        _clamp(100 - (l - 25) * 2.2, 5, 100)),
    'L2_soft': lambda l: _clamp(100 - max(0.0, l - 40) * 1.6, 10, 100),
}

# ---- 画布/低云配比 ----
RATIOS = [(0.65, 0.35), (0.75, 0.25), (0.85, 0.15)]


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


def build_rows():
    """日落事件行：canvas, low_avg, 其他维度, label, date"""
    feat = json.load(open(FEAT, encoding='utf-8'))
    labels = json.load(open(LABELS, encoding='utf-8'))
    era = json.load(open(ERA, encoding='utf-8'))['hourly']

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
        if ds not in labels or 'sunset' not in rec:
            continue
        d = datetime.strptime(ds, '%Y-%m-%d')
        st = astro.sun_times(d, LAT, LON, TZ)
        key_time = st.get('sunset')
        if not key_time or 'sunset' not in rec:
            continue
        hour = key_time.hour
        p = rec['sunset']
        path_midhigh = [m + h for m, h in zip(p['mid'], p['high'])]
        # 加权画布与低云（权重同 dim2_path_canvas：1/(1+d/150)）
        dist = p['dist']
        w = [1.0 / (1.0 + dd / 150.0) for dd in dist]
        tw = sum(w)
        canvas = sum(a * wi for a, wi in zip(path_midhigh, w)) / tw
        low_avg = sum(a * wi for a, wi in zip(p['low'], w)) / tw
        low_l = at('cloud_cover_low', ds, hour, 0.0)
        mid_l = at('cloud_cover_mid', ds, hour, 0.0)
        high_l = at('cloud_cover_high', ds, hour, 0.0)
        h3 = (hour - 3) % 24
        mid3 = at('cloud_cover_mid', ds, h3, 0.0)
        high3 = at('cloud_cover_high', ds, h3, 0.0)
        press = at('pressure_msl', ds, hour)
        press3 = at('pressure_msl', ds, h3)
        precip_now = at('precipitation', ds, (hour - 1) % 24, 0.0)
        other = {
            'dim1': sc.dim1_midhigh_canvas(mid_l, high_l, low_l),
            'dim3': sc.dim3_precip(precip_now),
            'dim5': sc.dim5_cloud_trend(mid_l + high_l, mid3 + high3),
            'dim6': sc.dim6_pressure(press, press3),
        }
        rows.append({'date': ds, 'label': labels[ds] > 0,
                     'canvas': canvas, 'low_avg': low_avg, 'other': other})
    return rows


def main():
    rows = build_rows()
    train = [r for r in rows if r['date'] < SPLIT]
    hold = [r for r in rows if r['date'] >= SPLIT]
    print(f'rows={len(rows)}  train={len(train)}  holdout={len(hold)}')

    def d2_score(r, curve, lowf, ratio):
        return ratio[0] * curve(r['canvas']) + ratio[1] * lowf(r['low_avg'])

    keys4 = ['dim1', 'dim3', 'dim5', 'dim6']
    w4 = {'dim1': 0.23 / 0.37, 'dim3': 0.10 / 0.37, 'dim5': 0.03 / 0.37,
          'dim6': 0.01 / 0.37}
    w5 = {'dim1': 0.23 / 0.67, 'dim3': 0.10 / 0.67, 'dim5': 0.03 / 0.67,
          'dim6': 0.01 / 0.67, 'dim2': 0.30 / 0.67}

    def combo(rows_, curve, lowf, ratio, keys, w):
        pairs = []
        for r in rows_:
            s = sum(w[k] * r['other'][k] for k in keys) \
                + w['dim2'] * d2_score(r, curve, lowf, ratio)
            pairs.append((s, r['label']))
        return auc(pairs)

    def dim2_alone(rows_, curve, lowf, ratio):
        return auc([(d2_score(r, curve, lowf, ratio), r['label'])
                    for r in rows_])

    results = []
    for cname, curve in CURVES.items():
        for lname, lowf in LOWS.items():
            for ratio in RATIOS:
                a_tr = dim2_alone(train, curve, lowf, ratio)
                a_ho = dim2_alone(hold, curve, lowf, ratio)
                c_tr = combo(train, curve, lowf, ratio, keys4, w5)
                c_ho = combo(hold, curve, lowf, ratio, keys4, w5)
                results.append({
                    'curve': cname, 'low': lname, 'ratio': ratio,
                    'auc_dim2_train': round(a_tr, 3) if a_tr else None,
                    'auc_dim2_hold': round(a_ho, 3) if a_ho else None,
                    'auc_combo_train': round(c_tr, 3) if c_tr else None,
                    'auc_combo_hold': round(c_ho, 3) if c_ho else None,
                })
    # 以训练集组合 AUC 选优，报告留出集
    results.sort(key=lambda x: -(x['auc_combo_train'] or 0))
    print('\n== 全部候选（按训练集组合 AUC 降序）==')
    print(f"{'curve':<10}{'low':<9}{'ratio':<16}{'d2_tr':>6}{'d2_ho':>7}"
          f"{'cmb_tr':>7}{'cmb_ho':>7}")
    for x in results:
        print(f"{x['curve']:<10}{x['low']:<9}"
              f"{f'{x['ratio'][0]:.2f}/{x['ratio'][1]:.2f}':<16}"
              f"{x['auc_dim2_train']:>6.3f}{x['auc_dim2_hold']:>7.3f}"
              f"{x['auc_combo_train']:>7.3f}{x['auc_combo_hold']:>7.3f}")

    # 基线（现状曲线）
    base = [x for x in results if x['curve'] == 'C0_cur'
            and x['low'] == 'L0_cur' and x['ratio'] == (0.65, 0.35)][0]
    best = results[0]
    print(f"\n现状: 组合训练 {base['auc_combo_train']:.3f} / "
          f"留出 {base['auc_combo_hold']:.3f}")
    print(f"最优: {best['curve']} + {best['low']} + {best['ratio']}"
          f"  组合训练 {best['auc_combo_train']:.3f} / "
          f"留出 {best['auc_combo_hold']:.3f}")

    # 4 维对照（无 dim2）
    def combo4(rows_):
        pairs = []
        for r in rows_:
            s = sum(w4[k] * r['other'][k] for k in keys4)
            pairs.append((s, r['label']))
        return auc(pairs)
    print(f"4 维对照（无 dim2）: 训练 {combo4(train):.3f} / "
          f"留出 {combo4(hold):.3f}")

    json.dump({'split': SPLIT, 'candidates': results, 'chosen': best},
              open(os.path.join(HERE, 'dim2_curve_refit.json'), 'w',
                   encoding='utf-8'), ensure_ascii=False, indent=1)
    print('saved -> dim2_curve_refit.json')


if __name__ == '__main__':
    main()

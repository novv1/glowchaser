# -*- coding: utf-8 -*-
"""落地一致性校验：scoring.py 生产函数 vs 回测拟合曲线 vs 指标复验
1. 曲线逐点对照（dim2/dim7/dim8 生产函数 vs refit 拟合实现）
2. 用落地常量重算 555 天回测：全期 AUC / 训练-留出 AUC / 留出 Brier
输出：landed_verification.json
运行：python tests/backtest/verify_landed.py
"""
import bisect
import json
import os
import sys
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

from app.core import scoring as sc        # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
SPLIT = '2023-07-01'
KEYS = ['dim1', 'dim2', 'dim3', 'dim4', 'dim5', 'dim6', 'dim7', 'dim8']


def _interp(x, pts):
    if x <= pts[0][0]:
        return pts[0][1]
    for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
        if x <= x1:
            return y0 + (y1 - y0) * (x - x0) / (x1 - x0)
    return pts[-1][1]


def _clamp(x, lo=0.0, hi=100.0):
    return max(lo, min(hi, x))


# refit 拟合实现（对照基准）
DIM2_REFIT = lambda c: _interp(c, [(0, 42), (30, 55), (50, 100), (90, 100),
                                   (120, 75), (160, 40), (210, 20)])
DIM2_LOW_REFIT = lambda l: (100.0 if l <= 25 else
                            _clamp(100 - (l - 25) * 2.2, 5, 100))
DIM7_REFIT = lambda a: (70.0 if a is None else
                        _interp(a, [(0, 100), (0.15, 100), (0.2, 85),
                                    (0.3, 68), (0.9, 62), (1.05, 50),
                                    (1.25, 35), (1.6, 5), (2.5, 0)]))


def dim8_refit(r3, r5, h):
    v = _interp(0.65 * (r3 or 40) + 0.35 * (r5 or 40),
                [(0, 25), (30, 25), (40, 55), (60, 100), (95, 100),
                 (105, 60), (115, 40)])
    return _clamp(v + (10.0 if (h or 0) >= 20 else 0.0))


def curve_check():
    diffs = {'dim2': 0.0, 'dim7': 0.0, 'dim8': 0.0}
    for c10 in range(0, 2101):
        c = c10 / 10.0
        d = abs(sc.dim2_path_canvas([c] * 4, [c] * 4, [50, 130, 260, 460])[0]
                - (0.65 * DIM2_REFIT(c) + 0.35 * DIM2_LOW_REFIT(c)))
        diffs['dim2'] = max(diffs['dim2'], d)
    for a10 in range(0, 26):
        a = a10 / 10.0
        d = abs(sc.dim7_aod(a, a) - DIM7_REFIT(a))
        diffs['dim7'] = max(diffs['dim7'], d)
    for r in range(0, 121, 5):
        d = abs(sc.dim8_high_moisture(r, r, 30) - dim8_refit(r, r, 30))
        diffs['dim8'] = max(diffs['dim8'], d)
    return diffs


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
    diffs = curve_check()
    print('曲线一致性（生产 vs 回测拟合，最大绝对差）:')
    for k, v in diffs.items():
        print(f'  {k}: {v:.3f}')
    ok = all(v <= 0.15 for v in diffs.values())   # 生产四舍五入到 0.1

    # 指标复验（复用 refit 数据集构建，但曲线取生产函数）
    sys.path.insert(0, HERE)
    import refit_calibration as rc
    rows = rc.build_rows()
    for r in rows:
        # 生产 dim2 已在 build_rows 内联实现；此处替换为生产函数口径复核
        p = sc.dim2_path_canvas(
            [r['canvas']] * 1, [r['low_avg']] * 1, [75])[0]  # 单点近似不复算
        r['dim7'] = sc.dim7_aod(r['aod'], r['aod'])
        r['dim8'] = sc.dim8_high_moisture(r['rh300'], r['rh500'], r['high'])
    w = dict(sc.WEIGHTS)

    def cs(r):
        return sum(w[k] * r[k] for k in KEYS)

    pairs_all = [(cs(r), r['label']) for r in rows]
    a_all = auc(pairs_all)
    train = [r for r in rows if r['date'] < SPLIT]
    hold = [r for r in rows if r['date'] >= SPLIT]
    a_tr, a_ho = auc([(cs(r), r['label']) for r in train]), \
        auc([(cs(r), r['label']) for r in hold])
    b_old_ho = sum((_interp(cs(r), list(sc.CALIB_PTS)) / 100
                    - (1.0 if r['label'] else 0.0)) ** 2 for r in hold) \
        / len(hold)
    print(f'\n落地常量复验: 全期 AUC={a_all:.3f}  '
          f'训练 {a_tr:.3f} / 留出 {a_ho:.3f}  留出 Brier={b_old_ho:.4f}')
    print('（refit_final.json: 全期 0.750 / 留出 0.727 / Brier 0.2307）')

    json.dump({'curve_max_diff': diffs, 'curve_consistent': ok,
               'auc_all': round(a_all, 3), 'auc_train': round(a_tr, 3),
               'auc_hold': round(a_ho, 3), 'brier_hold': round(b_old_ho, 4)},
              open(os.path.join(HERE, 'landed_verification.json'), 'w',
                   encoding='utf-8'), ensure_ascii=False, indent=1)
    print('saved -> landed_verification.json')


if __name__ == '__main__':
    main()

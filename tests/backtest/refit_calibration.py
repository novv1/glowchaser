# -*- coding: utf-8 -*-
"""全维校准重拟合（精度优化项目 · 第 4/5 步）
输入：dim2_path_features.json / dim7_validation.json / dim48_validation.json /
      era5_shanghai.json / sb_labels.json
步骤：
1. 构建 555 天日落事件全特征数据集（dim1~dim8）
2. dim7 / dim8 打分曲线新旧候选对比（训练/留出）
3. 时间序列 CV（2023-07 前训练 / 后留出）全维权重坐标寻优（基线 ±50%）
4. CALIB_PTS 保序重拟合（PAVA 单调化，保持基率语义）
5. 特征窗口变体（±1h 平均）复测
输出：refit_final.json（推荐落地常量 + 新旧指标对比）
运行：python tests/backtest/refit_calibration.py
"""
import bisect
import json
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
KEYS = ['dim1', 'dim2', 'dim3', 'dim4', 'dim5', 'dim6', 'dim7', 'dim8']
# dim4 无独立历史特征（PM2.5 AUC 0.477 无判别力）——按现曲线用 PM2.5 参与组合评估
BASE_W = dict(sc.WEIGHTS)


# ---------------------------------------------- 打分曲线候选

def _interp(x, pts):
    if x <= pts[0][0]:
        return pts[0][1]
    for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
        if x <= x1:
            return y0 + (y1 - y0) * (x - x0) / (x1 - x0)
    return pts[-1][1]


def _clamp(x, lo=0.0, hi=100.0):
    return max(lo, min(hi, x))


DIM7_CURVES = {
    # 现状：教程表 2.3 口径
    'D0_cur': lambda a: sc.dim7_aod(a, a),
    # 混合实证：洁净段满分、主区间缓坡（保颜色质量语义）、>1.0 陡降
    'D1_hybrid': lambda a: (70.0 if a is None else
                            _interp(a, [(0, 100), (0.15, 100), (0.3, 72),
                                        (0.6, 52), (0.9, 38), (1.1, 22),
                                        (1.4, 6), (2.0, 0)])),
    # 纯实证单调化：主区间接近平坦
    'D2_flat': lambda a: (70.0 if a is None else
                          _interp(a, [(0, 100), (0.15, 100), (0.2, 85),
                                      (0.3, 68), (0.9, 62), (1.05, 50),
                                      (1.25, 35), (1.6, 5), (2.5, 0)])),
}

DIM8_CURVES = {
    # 现状：trapz 40~80 满分，300/500 等权
    'E0_cur': lambda r3, r5, h: sc.dim8_high_moisture(r3, r5, h),
    # 实证：300 为主（0.65/0.35），峰值带 60~95，80+ 不衰减
    'E1_emp': lambda r3, r5, h: _clamp(
        _interp(0.65 * (r3 or 40) + 0.35 * (r5 or 40),
                [(0, 25), (30, 25), (40, 55), (60, 100), (95, 100),
                 (105, 60), (115, 40)])
        + (10.0 if (h or 0) >= 20 else 0.0)),
    # 单变量：只用 rh300
    'E2_r300': lambda r3, r5, h: _clamp(
        _interp(r3 or 40, [(0, 25), (30, 30), (50, 65), (60, 100),
                           (95, 100), (105, 55)])
        + (10.0 if (h or 0) >= 20 else 0.0)),
}

DIM2_CURVE = lambda c: _interp(c, [(0, 42), (30, 55), (50, 100), (90, 100),
                                   (120, 75), (160, 40), (210, 20)])
DIM2_LOW = lambda l: (100.0 if l <= 25 else _clamp(100 - (l - 25) * 2.2, 5, 100))


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


# ---------------------------------------------- 数据集构建

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

    rows = []
    for ds in sorted(labels):
        if not (ds >= '2022-06-24' and ds <= '2023-12-30'):
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
        dist = p['dist']
        w = [1.0 / (1.0 + dd / 150.0) for dd in dist]
        tw = sum(w)
        path_midhigh = [m + h for m, h in zip(p['mid'], p['high'])]
        canvas = sum(a * wi for a, wi in zip(path_midhigh, w)) / tw
        low_avg = sum(a * wi for a, wi in zip(p['low'], w)) / tw
        low_l = at('cloud_cover_low', ds, hour, 0.0)
        mid_l = at('cloud_cover_mid', ds, hour, 0.0)
        high_l = at('cloud_cover_high', ds, hour, 0.0)
        # ±1h 窗口平均变体
        def at_w(var, span=1, default=0.0):
            vals = []
            t0 = d + timedelta(hours=hour)
            for off in range(-span, span + 1):
                t = t0 + timedelta(hours=off)
                v = at(var, t.strftime('%Y-%m-%d'), t.hour)
                if v is not None:
                    vals.append(v)
            return sum(vals) / len(vals) if vals else default
        h3 = (hour - 3) % 24
        mid3 = at('cloud_cover_mid', ds, h3, 0.0)
        high3 = at('cloud_cover_high', ds, h3, 0.0)
        press = at('pressure_msl', ds, hour)
        press3 = at('pressure_msl', ds, h3)
        precip_now = at('precipitation', ds, (hour - 1) % 24, 0.0)
        r48 = d48[ds]
        rows.append({
            'date': ds, 'label': labels[ds] > 0,
            'canvas': canvas, 'low_avg': low_avg,
            'dim1': sc.dim1_midhigh_canvas(mid_l, high_l, low_l),
            'dim1_w': sc.dim1_midhigh_canvas(at_w('cloud_cover_mid'),
                                             at_w('cloud_cover_high'),
                                             at_w('cloud_cover_low')),
            'dim2': DIM2_CURVE(canvas) * 0.65 + DIM2_LOW(low_avg) * 0.35,
            'dim3': sc.dim3_precip(precip_now),
            'dim4': sc.dim4_visibility_pm25(None, r48.get('pm25')),
            'dim5': sc.dim5_cloud_trend(mid_l + high_l, mid3 + high3),
            'dim6': sc.dim6_pressure(press, press3),
            'aod': d7[ds],
            'rh300': r48.get('rh300'), 'rh500': r48.get('rh500'),
            'high': high_l,
        })
    return rows


def fill_scores(rows, dim7_curve, dim8_curve):
    for r in rows:
        r['dim7'] = dim7_curve(r['aod'])
        r['dim8'] = dim8_curve(r['rh300'], r['rh500'], r['high'])


# ---------------------------------------------- 评估工具

def combo_score(r, w):
    return sum(w[k] * r[k] for k in KEYS)


def combo_auc(rows_, w):
    return auc([(combo_score(r, w), r['label']) for r in rows_])


def brier(rows_, w, calib_pts):
    tot = n = 0
    for r in rows_:
        p = _interp(combo_score(r, w), calib_pts) / 100.0
        tot += (p - (1.0 if r['label'] else 0.0)) ** 2
        n += 1
    return tot / n


def pava_mono(xs, ys):
    """PAVA 保序回归：ys 单调不减"""
    blocks = [[x, y, 1] for x, y in zip(xs, ys)]
    i = 0
    while i < len(blocks) - 1:
        if blocks[i][1] > blocks[i + 1][1]:
            n1, n2 = blocks[i][2], blocks[i + 1][2]
            blocks[i][1] = (blocks[i][1] * n1 + blocks[i + 1][1] * n2) / \
                (n1 + n2)
            blocks[i][2] = n1 + n2
            del blocks[i + 1]
            if i > 0:
                i -= 1
        else:
            i += 1
    return [(b[0], round(b[1], 1)) for b in blocks]


def refit_calib(rows_, base_pts, w):
    """分箱烧率 -> PAVA -> 分段点（保持 0 端 2%、保持基率语义）"""
    edges = list(range(0, 101, 10))
    xs, ys = [], []
    for lo, hi in zip(edges[:-1], edges[1:]):
        xs_ = [r for r in rows_ if lo <= combo_score(r, w) < hi]
        if not xs_:
            continue
        xs.append(round((lo + hi) / 2, 1))
        ys.append(sum(1 for r in xs_ if r['label']) / len(xs_) * 100)
    if len(xs) < 3:
        return base_pts
    mono = pava_mono(xs, ys)
    pts = [(0, min(2.0, mono[0][1]))] + mono
    # 尾部外推（>100 区间保持平台，供 ENS 惩罚前分数使用）
    if pts[-1][0] < 130:
        pts.append((130, pts[-1][1]))
    return pts


# ---------------------------------------------- 主流程

def main():
    rows = build_rows()
    print('rows:', len(rows))
    train = [r for r in rows if r['date'] < SPLIT]
    hold = [r for r in rows if r['date'] >= SPLIT]
    print(f'train={len(train)}  holdout={len(hold)}')

    # ---- 1) 曲线候选对比（权重用基线）----
    print('\n== 曲线候选（组合权重=当前基线权重）==')
    fill_scores(rows, DIM7_CURVES['D0_cur'], DIM8_CURVES['E0_cur'])
    base_auc_all = combo_auc(rows, BASE_W)   # 真基线（旧曲线+旧权重）
    curve_results = []
    for d7n, d7f in DIM7_CURVES.items():
        for d8n, d8f in DIM8_CURVES.items():
            fill_scores(rows, d7f, d8f)
            a_tr = combo_auc(train, BASE_W)
            a_ho = combo_auc(hold, BASE_W)
            curve_results.append((a_tr, a_ho, d7n, d8n))
            print(f'  {d7n:<10}+{d8n:<8} 组合 AUC 训练 {a_tr:.3f} / '
                  f'留出 {a_ho:.3f}')
    curve_results.sort(reverse=True)
    _, _, d7_best, d8_best = curve_results[0]
    print(f'选中曲线: {d7_best} + {d8_best}')
    fill_scores(rows, DIM7_CURVES[d7_best], DIM8_CURVES[d8_best])

    # ---- 2) 全维权重坐标寻优（训练集选优，±50% 约束）----
    print('\n== 权重坐标寻优（基线 ±50%，训练集选优）==')
    W_BEST = dict(BASE_W)

    def opt_auc(w):
        return combo_auc(train, w)

    cur = opt_auc(W_BEST)
    for it in range(4):
        improved = False
        for k in KEYS:
            others = [x for x in KEYS if x != k]
            best_v, best_a = W_BEST[k], cur
            for mult in (0.5, 0.7, 0.85, 1.15, 1.3, 1.5):
                v2 = BASE_W[k] * mult
                w2 = dict(W_BEST)
                w2[k] = v2
                tw = sum(w2.values())
                w2 = {kk: vv / tw for kk, vv in w2.items()}
                a = opt_auc(w2)
                if a > best_a + 1e-5:
                    best_v, best_a = v2, a
            if abs(best_v - W_BEST[k]) > 1e-9:
                W_BEST[k] = best_v
                tw = sum(W_BEST.values())
                W_BEST = {kk: vv / tw for kk, vv in W_BEST.items()}
                cur = best_a
                improved = True
        print(f'  iter{it + 1}: train AUC={cur:.3f}')
        if not improved:
            break
    a_tr = combo_auc(train, W_BEST)
    a_ho = combo_auc(hold, W_BEST)
    a_tr_base = combo_auc(train, BASE_W)
    a_ho_base = combo_auc(hold, BASE_W)
    print(f'权重: 基线 训练 {a_tr_base:.3f}/留出 {a_ho_base:.3f}')
    print(f'      寻优 训练 {a_tr:.3f}/留出 {a_ho:.3f}')
    print('      ' + json.dumps({k: round(v, 3) for k, v in W_BEST.items()}))

    # 防过拟合：留出集必须不降才采纳寻优权重
    if a_ho >= a_ho_base - 0.003:
        W_FINAL = W_BEST
        print('  -> 采纳寻优权重')
    else:
        W_FINAL = BASE_W
        print('  -> 留出集退化，维持基线权重')

    # ---- 3) 特征窗口变体（±1h，与 W_FINAL 单时刻公平对比）----
    rows_w = [dict(r) for r in rows]
    for r in rows_w:
        r['dim1'] = r['dim1_w']
    a_w_tr = combo_auc([r for r in rows_w if r['date'] < SPLIT], W_FINAL)
    a_w_ho = combo_auc([r for r in rows_w if r['date'] >= SPLIT], W_FINAL)
    a_f_tr = combo_auc(train, W_FINAL)
    a_f_ho = combo_auc(hold, W_FINAL)
    print(f'\n±1h 窗口变体: 训练 {a_w_tr:.3f} / 留出 {a_w_ho:.3f} '
          f'(单时刻 训练 {a_f_tr:.3f} / 留出 {a_f_ho:.3f})')
    use_window = a_w_ho >= a_f_ho
    print(f'  -> {"采纳窗口特征" if use_window else "维持单时刻"}')

    # ---- 4) CALIB_PTS 保序重拟合 ----
    base_pts = list(sc.CALIB_PTS)
    b_old_tr = brier(train, W_FINAL, base_pts)
    b_old_ho = brier(hold, W_FINAL, base_pts)
    new_pts = refit_calib(train, base_pts, W_FINAL)
    # 0 端点保持 2%（避免绝对 0 概率语义）
    new_pts[0] = (0, 2.0)
    b_new_tr = brier(train, W_FINAL, new_pts)
    b_new_ho = brier(hold, W_FINAL, new_pts)
    print('\n== CALIB_PTS 重拟合 ==')
    print(f'  旧映射 Brier: 训练 {b_old_tr:.4f} / 留出 {b_old_ho:.4f}')
    print(f'  新映射 Brier: 训练 {b_new_tr:.4f} / 留出 {b_new_ho:.4f}')
    print('  新点: ' + str(new_pts))

    # 全期基率语义检查 + 基率缩放校正候选
    base_rate = sum(1 for r in rows if r['label']) / len(rows)
    p_all = [_interp(combo_score(r, W_FINAL), new_pts) / 100 for r in rows]
    mean_pred = sum(p_all) / len(p_all) * 100
    print(f'  全期均值预测 {mean_pred:.1f}% vs 实际基率 {base_rate*100:.1f}%')
    scaled_pts = [(x, min(95.0, y * base_rate * 100 / mean_pred))
                  for x, y in new_pts]
    b_scl_ho = brier(hold, W_FINAL, scaled_pts)
    b_scl_tr = brier(train, W_FINAL, scaled_pts)
    print(f'  基率缩放版 Brier: 训练 {b_scl_tr:.4f} / 留出 {b_scl_ho:.4f}')
    if b_scl_ho < b_new_ho:
        new_pts, b_new_tr, b_new_ho = scaled_pts, b_scl_tr, b_scl_ho
        print('  -> 采纳基率缩放映射')
    else:
        print('  -> 维持原始 PAVA 映射')

    # ---- 5) 汇总输出 ----
    final_auc_all = combo_auc(rows, W_FINAL)
    out = {
        'split': SPLIT,
        'chosen': {
            'dim7_curve': d7_best, 'dim8_curve': d8_best,
            'weights': {k: round(v, 4) for k, v in W_FINAL.items()},
            'weights_changed': W_FINAL != BASE_W,
            'use_window_feature': use_window,
            'calib_pts': new_pts,
        },
        'metrics': {
            'baseline_combo_auc': round(base_auc_all, 3),
            'final_combo_auc': round(final_auc_all, 3),
            'train_hold_auc_final': [round(a_f_tr, 3), round(a_f_ho, 3)],
            'train_hold_auc_window': [round(a_w_tr, 3), round(a_w_ho, 3)],
            'brier_old': [round(b_old_tr, 4), round(b_old_ho, 4)],
            'brier_new': [round(b_new_tr, 4), round(b_new_ho, 4)],
            'auc_4dim_baseline': 0.719,
        },
        'curve_results': [{'dim7': c[2], 'dim8': c[3],
                           'train': round(c[0], 3), 'hold': round(c[1], 3)}
                          for c in curve_results],
    }
    json.dump(out, open(os.path.join(HERE, 'refit_final.json'), 'w',
                        encoding='utf-8'), ensure_ascii=False, indent=1)
    print('\nsaved -> refit_final.json')
    print(f'\n最终: 全期组合 AUC {base_auc_all:.3f} -> {final_auc_all:.3f}')


if __name__ == '__main__':
    main()

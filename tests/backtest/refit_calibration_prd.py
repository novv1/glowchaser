# -*- coding: utf-8 -*-
"""珠三角 CALIB_PTS 重拟合（R-003-s2 · 只重拟合概率映射，不动权重/曲线）

输入：
- ``data/prd_forecast_scores.csv`` —— ERA5 完美预报分（``prd_scores.py`` 产出）
- ``data/glow_labels.csv``         —— 卫星受光画布标签（``auto_labels.py`` 产出）

步骤（对应上海版 ``refit_calibration.py`` 的第 4 步，权重/打分曲线不动）：
1. 按 (date, period) 对齐，标签 = canvas_lit_avg >= 30（受光画布必要条件口径，
   事件率 ~77.6%，与上海「值得记录的大烧」基率 27.7% **不是同一个语义**）；
2. 组合分分箱（10 分一箱）烧率 -> PAVA 保序 -> 分段点；
3. 基率缩放候选对比（同上海版）；
4. 时间留出（2026-08-01 前训练 / 8~9 月留出）防过拟合；
5. 输出新曲线点 + 新旧映射 Brier 对比 + 分箱命中率（65~75 凹陷是否缓解）。

口径提醒：概率语义变为「受光画布 >=30%（有可烧的受光云）」，不是上海侧
「值得记录的大烧」——``verdict_word`` 判定阈值须按新语义配套重定。

运行：python tests/backtest/refit_calibration_prd.py
"""
import csv
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

from app.core import scoring as sc   # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
SPLIT = '2026-08-01'
THRESHOLD = 30


def _interp(x, pts):
    if x <= pts[0][0]:
        return pts[0][1]
    for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
        if x <= x1:
            return y0 + (y1 - y0) * (x - x0) / (x1 - x0)
    return pts[-1][1]


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


def load_pairs():
    fc = {}
    with open(os.path.join(ROOT, 'data', 'prd_forecast_scores.csv'),
              newline='', encoding='utf-8') as f:
        for r in csv.DictReader(f):
            if r.get('score') not in (None, ''):
                fc[(r['date'], r.get('period') or 'PM')] = float(r['score'])
    labels = {}
    with open(os.path.join(ROOT, 'data', 'glow_labels.csv'),
              newline='', encoding='utf-8') as f:
        for r in csv.DictReader(f):
            labels[(r['date'], r.get('period') or 'PM')] = \
                float(r['canvas_lit_avg'])
    pairs = [(k[0], s, 1 if labels[k] >= THRESHOLD else 0)
             for k, s in fc.items() if k in labels]
    return sorted(pairs)


def brier(rows_, calib_pts):
    if not rows_:
        return None
    return sum((_interp(s, calib_pts) / 100.0 - y) ** 2 for _, s, y in rows_) \
        / len(rows_)


def refit_prd(rows_):
    """分箱烧率 -> PAVA -> 分段点（PRD 口径：0 端用首箱实际烧率）"""
    edges = list(range(0, 101, 10))
    xs, ys = [], []
    print('  分箱烧率（训练集）：')
    for lo, hi in zip(edges[:-1], edges[1:]):
        xs_ = [(d, s, y) for d, s, y in rows_ if lo <= s < hi]
        if not xs_:
            continue
        rate = sum(y for _, _, y in xs_) / len(xs_) * 100
        xs.append(round((lo + hi) / 2, 1))
        ys.append(rate)
        print('    [%3d,%3d)  n=%3d  %5.1f%%' % (lo, hi, len(xs_), rate))
    if len(xs) < 3:
        raise SystemExit('可用分箱不足 3 个，无法重拟合')
    mono = pava_mono(xs, ys)
    pts = list(mono)
    # 尾部外推（>100 保持平台，供 ENS 惩罚前分数使用，同上海版）
    if pts[-1][0] < 130:
        pts.append((130, pts[-1][1]))
    return pts


def main():
    rows = load_pairs()
    n = len(rows)
    pos = sum(y for _, _, y in rows)
    base = pos / n
    train = [r for r in rows if r[0] < SPLIT]
    hold = [r for r in rows if r[0] >= SPLIT]
    print('对齐 %d 天次，事件率 %.1f%%；train=%d  holdout=%d'
          % (n, base * 100, len(train), len(hold)))

    old_pts = list(sc.CALIB_PTS)
    print('\n== CALIB_PTS 珠三角重拟合（PAVA，时间留出 %s）==' % SPLIT)
    new_pts = refit_prd(train)
    b_old_tr, b_old_ho = brier(train, old_pts), brier(hold, old_pts)
    b_new_tr, b_new_ho = brier(train, new_pts), brier(hold, new_pts)
    print('  旧映射(上海) Brier: 训练 %.4f / 留出 %.4f' % (b_old_tr, b_old_ho))
    print('  新映射(PRD)  Brier: 训练 %.4f / 留出 %.4f' % (b_new_tr, b_new_ho))

    # 基率缩放候选（同上海版：全期均值预测对齐实际基率）
    mean_pred = sum(_interp(s, new_pts) for _, s, _ in rows) / n
    scaled = [(x, min(95.0, y * base * 100 / mean_pred)) for x, y in new_pts]
    b_scl_tr, b_scl_ho = brier(train, scaled), brier(hold, scaled)
    print('  基率缩放版 Brier: 训练 %.4f / 留出 %.4f（均值预测 %.1f%% vs 基率 %.1f%%）'
          % (b_scl_tr, b_scl_ho, mean_pred, base * 100))
    if b_scl_ho < b_new_ho:
        new_pts, b_new_tr, b_new_ho = scaled, b_scl_tr, b_scl_ho
        print('  -> 采纳基率缩放映射')
    else:
        print('  -> 维持原始 PAVA 映射')

    # 分箱命中率对比（65~75 凹陷是否缓解）——全期，新映射
    print('\n全期分箱命中率（旧 -> 新映射）：')
    edges = list(range(0, 101, 10))
    for lo, hi in zip(edges[:-1], edges[1:]):
        xs_ = [(d, s, y) for d, s, y in rows if lo <= s < hi]
        if not xs_:
            continue
        rate = sum(y for _, _, y in xs_) / len(xs_)
        p_old = _interp((lo + hi) / 2, old_pts) / 100
        p_new = _interp((lo + hi) / 2, new_pts) / 100
        print('  [%3d,%3d) n=%3d 实际 %5.1f%%  旧映射 %.1f%%  新映射 %.1f%%'
              % (lo, hi, len(xs_), rate * 100, p_old * 100, p_new * 100))

    # 推荐判定阈值：新映射概率的分位数（对应上海语义的 8/18/28/40）
    probs = sorted(_interp(s, new_pts) for _, s, _ in rows)
    def q(p):
        return probs[int(p * (len(probs) - 1))]
    print('\n新映射概率分位数: p10=%.0f p25=%.0f p50=%.0f p75=%.0f p90=%.0f'
          % (q(.1), q(.25), q(.5), q(.75), q(.9)))

    out = {
        'split': SPLIT, 'label_threshold': THRESHOLD,
        'n': n, 'event_rate': round(base, 4),
        'base_brier': round(base * (1 - base), 4),
        'calib_pts_prd': new_pts,
        'brier_old': [round(b_old_tr, 4), round(b_old_ho, 4)],
        'brier_new': [round(b_new_tr, 4), round(b_new_ho, 4)],
        'quantiles': {'p10': round(q(.1)), 'p25': round(q(.25)),
                      'p50': round(q(.5)), 'p75': round(q(.75)),
                      'p90': round(q(.9))},
    }
    json.dump(out, open(os.path.join(HERE, 'refit_prd.json'), 'w',
                        encoding='utf-8'), ensure_ascii=False, indent=1)
    print('\nsaved -> refit_prd.json')
    print('新曲线点: ' + str(new_pts))


if __name__ == '__main__':
    main()

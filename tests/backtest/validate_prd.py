# -*- coding: utf-8 -*-
"""珠三角：预报分 vs 卫星客观标签 的对账。

输入两份 CSV，按 ``date``（+ 可选 ``period``）对齐：

1. **标签**（``auto_labels.py`` 产出）：``date,period,canvas_lit_avg,...``
   —— 卫星观测反演的「受光画布占比」，是对账的真值侧。
2. **预报分** —— 每行 ``date,score``（可选 ``period,model``）。

预报分从哪来（这一栏最容易做错，务必看清）
--------------------------------------------
- ❌ **别用「今天调的预报 API 去查历史日期」**：那拿到的是当前模式对过去的
  分析场，等于开了上帝视角，算出来的 AUC 是假的。
- ✅ **ERA5 再分析**（``archive-api.open-meteo.com``）：把云场当"完美预报"，
  衡量的是**评分函数本身的判别力上限**。现有上海 555 天基线就是这么做的
  （``eval_baseline.py``），口径一致，便于和 0.719 对比。
- ✅ **归档的预报场**（Open-Meteo ``/v1/previous-runs`` 或自建落盘）：
  才真正回答"预报精度"。项目里 ``app/services/forecast_log.py`` 已经在
  逐日落盘预报，攒够天数后直接指向它最省事。

用法::

    python tests/backtest/validate_prd.py --labels data/glow_labels.csv \\
        --forecast data/prd_forecast_scores.csv
"""
import argparse
import csv
import os
import sys
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, '..', '..'))
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

import auto_labels as al   # noqa: E402


def read_csv(path):
    with open(path, newline='', encoding='utf-8') as f:
        return list(csv.DictReader(f))


def brier(pairs, prob_of):
    """pairs: [(score,label)]，prob_of 把 score 映到 0~1 的概率。"""
    if not pairs:
        return None
    return sum((prob_of(s) - y) ** 2 for s, y in pairs) / len(pairs)


def score_to_prob(score, region=None):
    """用项目自己的实证校准映射把 0~100 分映到概率（与线上同一条曲线）。

    线上是 ``calibrated_probability`` 作用于多模式共识后的 raw 分；这里
    直接作用于单个分数，口径不完全等价，仅用于 Brier 的相对比较。
    region='prd' 走珠三角曲线（R-003-s2），默认上海曲线。
    """
    from app.core import scoring as sc
    return sc.calibrated_probability(score, region) / 100.0


def main():
    ap = argparse.ArgumentParser(description='预报分 vs 卫星标签 对账')
    ap.add_argument('--labels', required=True, help='auto_labels.py 产出的 CSV')
    ap.add_argument('--forecast', required=True, help='预报分 CSV: date,score[,period]')
    ap.add_argument('--label-threshold', type=int, default=30,
                    help='受光画布 %% >= 该值记为事件标签')
    ap.add_argument('--region', choices=('sh', 'prd'), default='sh',
                    help='校准曲线：prd=珠三角（R-003-s2 重拟合），sh=上海默认')
    ap.add_argument('--by-month', action='store_true', help='再按月分层')
    args = ap.parse_args()

    labels = {}
    for r in read_csv(args.labels):
        key = (r['date'], r.get('period') or 'PM')
        labels[key] = float(r['canvas_lit_avg'])
    fc = {}
    for r in read_csv(args.forecast):
        key = (r['date'], r.get('period') or 'PM')
        if r.get('score') not in (None, ''):
            fc[key] = float(r['score'])

    pairs, months = [], defaultdict(list)
    for key, score in fc.items():
        if key in labels:
            y = 1 if labels[key] >= args.label_threshold else 0
            pairs.append((score, y))
            months[key[0][:7]].append((score, y))

    if not pairs:
        raise SystemExit('两份 CSV 没有可对齐的日期（检查 date/period 字段）')

    n = len(pairs)
    pos = sum(y for _, y in pairs)
    print('对齐 %d 天次；事件标签（受光画布 >=%d%%）%d 次（%.1f%%）'
          % (n, args.label_threshold, pos, 100.0 * pos / n))

    a = al.auc(pairs)
    br = brier(pairs, lambda s: score_to_prob(s, args.region))
    base = 100.0 * pos / n
    print('AUC = %s' % ('—' if a is None else '%.3f' % a))
    print('Brier = %s（基率预报的 Brier = %.3f）'
          % ('—' if br is None else '%.3f' % br,
             (base / 100.0) * (1 - base / 100.0)))

    print('\n预报分分箱 -> 卫星判定的实际发生率：')
    for lo, hi in ((0, 45), (45, 55), (55, 65), (65, 75), (75, 85), (85, 101)):
        xs = [y for s, y in pairs if lo <= s < hi]
        if xs:
            print('  [%3d,%3d)  n=%4d  %5.1f%%'
                  % (lo, hi, len(xs), 100.0 * sum(xs) / len(xs)))

    # 高分命中率（用户只关心「报大烧是不是真的能烧」）
    for thr in (65, 75, 85):
        hi = [y for s, y in pairs if s >= thr]
        if hi:
            print('  预报 >=%d 分的 %d 天里，卫星判定有霞的占 %.1f%%'
                  % (thr, len(hi), 100.0 * sum(hi) / len(hi)))

    if args.by_month:
        print('\n按月分层：')
        for m in sorted(months):
            ps = months[m]
            am = al.auc(ps)
            p = sum(y for _, y in ps)
            print('  %s  n=%3d  事件 %5.1f%%  AUC=%s'
                  % (m, len(ps), 100.0 * p / len(ps),
                     '—' if am is None else '%.3f' % am))


if __name__ == '__main__':
    main()

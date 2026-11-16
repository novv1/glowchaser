# -*- coding: utf-8 -*-
"""精度套餐 A/B 对照（离线、零额度）——采样密度消融 + 判别力/校准影响。

为什么只能做到这一步（读结论前先看）
----------------------------------
"精度套餐"里可以离线验证的只有**光路采样密度**这一项：`data/prd_forecast_scores.csv`
把 366 天（2026-04-01 ~ 2026-09-30）ERA5 再分析当"完美预报"，按引擎同款口径在
8 个光路距离（50/100/160/230/310/400/490/580 km）上取云量，且 `data/glow_labels.csv`
有同期的**实拍标签**（`canvas_lit_avg`，按 `tests/backtest/refit_prd.json` 的
`label_threshold=30` 二值化）。于是可以问：把光路采样从 8 点降到 6/4/2 点，
①dim2/总分偏离多少（自洽性）②对标签的 AUC 与 Brier 变差多少（判别力）。

**本脚本不能证明、也不声称证明的**：
- 17 点（v2.37 加密）比 9 点**更准**：档案里只有那 8 个距离的 ERA5 格点值，
  加密出来的 25/75/130 km 等新点在 0.25°（≈25 km）再分析里是插值出来的，
  没有新信息。能测的只是"采样密度对结论的影响有多大"这个**敏感度**。
- 集合多源（ECMWF+GEFS+ICON-EPS+UKMO）与 14 天窗口：两者都需要
  「同一时刻多模式/多天的历史预报 + 实拍标签」的对账档案，而 `data/forecast_log.json`
  目前只有 31 条且 `outcome` 全为 null（2026-10-08 实测）。要验证它们，得先让
  逐日 outcome 落盘攒够样本——这条写在 `tests/backtest/validate_prd.py` 文档里。

用法::

    python tests/backtest/ab_precision.py            # 打印对照表
    python tests/backtest/ab_precision.py --json out.json
"""
import argparse
import csv
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, '..', '..'))
sys.path.insert(0, ROOT)

from app.core import scoring as sc              # noqa: E402

SCORES = os.path.join(ROOT, 'data', 'prd_forecast_scores.csv')
LABELS = os.path.join(ROOT, 'data', 'glow_labels.csv')
REFIT = os.path.join(HERE, 'refit_prd.json')

# 档案里的 8 个光路距离 (= 旧 PATH_DISTANCES[1:]，观测点自身不在内)
ARCHIVE_KM = [50, 100, 160, 230, 310, 400, 490, 580]
DIMS_USED = ('dim1', 'dim2', 'dim3', 'dim5', 'dim6')   # 档案能算的维度（同 prd_scores.py）

# 嵌套抽稀：始终保留最近/最远锚点，中间按密度取。k=8 是全档案（参照系）。
SUBSETS = {
    8: ARCHIVE_KM,
    6: [50, 100, 230, 310, 490, 580],
    4: [50, 160, 310, 580],
    2: [50, 580],
}


def _load():
    with open(SCORES, encoding='utf-8') as f:
        rows = list(csv.DictReader(f))
    with open(LABELS, encoding='utf-8') as f:
        lab = {(r['date'], r['period']): r for r in csv.DictReader(f)}
    with open(REFIT, encoding='utf-8') as f:
        thr = float(json.load(f)['label_threshold'])
    out = []
    for r in rows:
        lb = lab.get((r['date'], r['period']))
        if not lb or lb.get('canvas_lit_avg') in (None, ''):
            continue
        mh = [float(v) for v in r['path_midhigh'].split(';') if v != '']
        lo = [float(v) for v in r['path_low'].split(';') if v != '']
        if len(mh) != len(ARCHIVE_KM) or len(lo) != len(ARCHIVE_KM):
            continue
        out.append({
            'key': (r['date'], r['period']),
            'mh': mh, 'lo': lo,
            'dims': {k: float(r[k]) for k in DIMS_USED},
            'lit': float(lb['canvas_lit_avg']),
            'y': 1 if float(lb['canvas_lit_avg']) >= thr else 0,
        })
    return out, thr


def _score(dims):
    tw = sum(sc.WEIGHTS[k] for k in DIMS_USED)
    return sum(sc.WEIGHTS[k] / tw * dims[k] for k in DIMS_USED)


def _auc(scores, ys):
    """秩和 AUC（并列取平均秩），不依赖 sklearn。"""
    pairs = sorted(zip(scores, ys))
    n = len(pairs)
    ranks = [0.0] * n
    i = 0
    while i < n:
        j = i
        while j + 1 < n and pairs[j + 1][0] == pairs[i][0]:
            j += 1
        avg = (i + j) / 2.0 + 1.0
        for k in range(i, j + 1):
            ranks[k] = avg
        i = j + 1
    pos = sum(1 for _s, y in pairs if y == 1)
    neg = n - pos
    if pos == 0 or neg == 0:
        return None
    rsum = sum(r for r, (_s, y) in zip(ranks, pairs) if y == 1)
    return (rsum - pos * (pos + 1) / 2.0) / (pos * neg)


def _brier(probs, ys):
    return sum((p / 100.0 - y) ** 2 for p, y in zip(probs, ys)) / len(ys)


def run():
    rows, thr = _load()
    out = {'n': len(rows), 'label_threshold': thr,
           'archive_km': ARCHIVE_KM, 'variants': []}
    ref = {}
    for r in rows:
        d2, canvas, low, boundary = sc.dim2_path_canvas(
            r['mh'], r['lo'], ARCHIVE_KM)
        dims = dict(r['dims'])
        dims['dim2'] = d2
        ref[r['key']] = {'dim2': d2, 'score': _score(dims),
                         'canvas': canvas, 'low': low}
    ref_auc = _auc([ref[r['key']]['score'] for r in rows],
                   [r['y'] for r in rows])
    ref_brier = _brier([sc.calibrated_probability(
        ref[r['key']]['score'], 'prd') for r in rows], [r['y'] for r in rows])
    for k in sorted(SUBSETS, reverse=True):
        dists = SUBSETS[k]
        idx = [ARCHIVE_KM.index(d) for d in dists]
        d2s, scores, canv = [], [], []
        for r in rows:
            d2, canvas, _low, _b = sc.dim2_path_canvas(
                [r['mh'][i] for i in idx], [r['lo'][i] for i in idx], dists)
            dims = dict(r['dims'])
            dims['dim2'] = d2
            d2s.append(d2)
            canv.append(canvas)
            scores.append(_score(dims))
        ys = [r['y'] for r in rows]
        probs = [sc.calibrated_probability(s, 'prd') for s in scores]
        dd2 = [abs(d2s[i] - ref[rows[i]['key']]['dim2'])
               for i in range(len(rows))]
        dsc = [abs(scores[i] - ref[rows[i]['key']]['score'])
               for i in range(len(rows))]
        out['variants'].append({
            'points': k, 'dists': dists,
            'mean_abs_d_dim2': round(sum(dd2) / len(dd2), 2),
            'max_abs_d_dim2': round(max(dd2), 2),
            'mean_abs_d_score': round(sum(dsc) / len(dsc), 2),
            'max_abs_d_score': round(max(dsc), 2),
            'auc': round(_auc(scores, ys), 4),
            'brier': round(_brier(probs, ys), 4),
            'event_rate': round(sum(ys) / len(ys), 3),
        })
    out['reference'] = {'points': 8, 'auc': round(ref_auc, 4),
                        'brier': round(ref_brier, 4)}
    return out


def main():
    ap = argparse.ArgumentParser(description='精度套餐 A/B 对照（离线）')
    ap.add_argument('--json', default=os.path.join(HERE, 'ab_precision.json'))
    args = ap.parse_args()
    res = run()
    print('样本 %d 天次（%s，标签阈值 canvas_lit_avg >= %g，事件率 %.3f）'
          % (res['n'], '2026-04-01~2026-09-30', res['label_threshold'],
             res['variants'][0]['event_rate']))
    print('%-6s %-34s %8s %8s %7s %7s'
          % ('点数', '光路距离(km)', 'Δdim2均', 'Δ总分均', 'AUC', 'Brier'))
    for v in res['variants']:
        print('%-6d %-34s %8.2f %8.2f %7.4f %7.4f'
              % (v['points'], '/'.join(str(d) for d in v['dists']),
                 v['mean_abs_d_dim2'], v['mean_abs_d_score'],
                 v['auc'], v['brier']))
    print('参照点（8 点全档案）：AUC %.4f / Brier %.4f'
          % (res['reference']['auc'], res['reference']['brier']))
    if args.json:
        with open(args.json, 'w', encoding='utf-8') as f:
            json.dump(res, f, ensure_ascii=False, indent=1)
        print('-> %s' % args.json)


if __name__ == '__main__':
    main()

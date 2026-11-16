# -*- coding: utf-8 -*-
"""本地 4 维子模型权重网格寻优（dim1/dim5/dim6/dim3）"""
import bisect
import json
import os
import sys
from datetime import date, datetime, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

from calibrate import score_day, auc   # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
LABELS = os.path.join(HERE, 'sb_labels.json')
ERA = os.path.join(HERE, 'era5_shanghai.json')


def main():
    labels = json.load(open(LABELS, encoding='utf-8'))
    era = json.load(open(ERA, encoding='utf-8'))['hourly']
    keys = ['dim1', 'dim5', 'dim6', 'dim3']
    rows = []
    cur = date(2022, 6, 24)
    end = date(2023, 12, 30)
    while cur <= end:
        ds = cur.isoformat()
        if ds in labels:
            r = score_day(labels, era, ds)
            rows.append((r['sunset']['dims'], r['sunrise']['dims'], labels[ds]))
        cur += timedelta(days=1)
    print('days:', len(rows))

    def sub_auc(w):
        pairs = []
        for sd, rd, lab in rows:
            smax = max(sum(w[k] * sd[k] for k in keys),
                       sum(w[k] * rd[k] for k in keys))
            pairs.append((smax, lab > 0))
        return auc(pairs)

    best = (0.0, None)
    for w1 in (0.5, 0.6, 0.7, 0.8):
        for w5 in (0.05, 0.1, 0.15, 0.2):
            for w6 in (0.0, 0.05, 0.1):
                w3 = round(1 - w1 - w5 - w6, 2)
                if w3 < 0 or w3 > 0.2:
                    continue
                w = {'dim1': w1, 'dim5': w5, 'dim6': w6, 'dim3': w3}
                a = sub_auc(w)
                if a and a > best[0]:
                    best = (a, w)
    cur_w = {'dim1': 0.23 / 0.37, 'dim5': 0.03 / 0.37,
             'dim6': 0.01 / 0.37, 'dim3': 0.10 / 0.37}
    print(f'当前权重 AUC={sub_auc(cur_w):.3f}  '
          f'{ {k: round(v, 2) for k, v in cur_w.items()} }')
    print(f'最优权重 AUC={best[0]:.3f}  {best[1]}')


if __name__ == '__main__':
    main()

# -*- coding: utf-8 -*-
"""临时分析：dim7 AOD 细分箱（0.1 步长）+ dim8 RH 细分箱，为曲线重拟合定标"""
import json
import os
import sys
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, '..', '..'))
sys.path.insert(0, ROOT)

d7 = json.load(open(os.path.join(HERE, 'dim7_validation.json'), encoding='utf-8'))
rows = d7['rows']
print('rows:', len(rows), 'with aod:', sum(1 for r in rows if r.get('aod') is not None))
print('\nAOD 0.1 分箱 -> 有烧率 (日落):')
for lo in [0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0, 1.2, 1.5]:
    hi = lo + (0.2 if lo >= 1.2 else 0.1)
    xs = [r for r in rows if r.get('aod') is not None and lo <= r['aod'] < hi]
    if not xs:
        continue
    rate = sum(1 for r in xs if r['label'] > 0) / len(xs)
    print(f'  [{lo:.1f},{hi:.1f})  n={len(xs):>3}  {rate*100:5.1f}%')

d48 = json.load(open(os.path.join(HERE, 'dim48_validation.json'), encoding='utf-8'))
r48 = d48['rows']
print('\nRH300 0.1 分箱 -> 有烧率:')
for lo in range(0, 101, 10):
    hi = lo + 10
    xs = [r for r in r48 if r.get('rh300') is not None and lo <= r['rh300'] < hi]
    if not xs:
        continue
    rate = sum(1 for r in xs if r['label']) / len(xs)
    print(f'  [{lo:>3},{hi:>3})  n={len(xs):>3}  {rate*100:5.1f}%')
print('\nRH500 0.1 分箱 -> 有烧率:')
for lo in range(0, 101, 10):
    hi = lo + 10
    xs = [r for r in r48 if r.get('rh500') is not None and lo <= r['rh500'] < hi]
    if not xs:
        continue
    rate = sum(1 for r in xs if r['label']) / len(xs)
    print(f'  [{lo:>3},{hi:>3})  n={len(xs):>3}  {rate*100:5.1f}%')

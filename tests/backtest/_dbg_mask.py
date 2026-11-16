# -*- coding: utf-8 -*-
"""临时调试：珠海方位遮蔽采样原始海拔"""
import sys

sys.path.insert(0, r'e:\Glowchaser')
from app.services import terrain as t   # noqa: E402

lats, lons = t._sample_coords(22.2710, 113.5767)
print('coords check az315 2km:', lats[1 + 14 * 2], lons[1 + 14 * 2])
elevs = t.fetch_elevations(lats, lons)
print('elev0:', elevs[0])
for i, az in enumerate(t._AZS):
    pair = [elevs[1 + i * 2 + j] for j in range(2)]
    print(f'az={az:5.1f}  2km={pair[0]:>8}  6km={pair[1]:>8}')

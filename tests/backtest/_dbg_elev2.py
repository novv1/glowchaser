# -*- coding: utf-8 -*-
"""临时调试：Elevation 批量顺序验证（前 6 点逐点对比）"""
import sys

sys.path.insert(0, r'e:\Glowchaser')
import requests   # noqa: E402
from app.services import terrain as t   # noqa: E402

lats, lons = t._sample_coords(22.2710, 113.5767)
elevs = t.fetch_elevations(lats, lons)
print('batch:', elevs[:6])
for i in range(6):
    r = requests.get('https://api.open-meteo.com/v1/elevation',
                     params={'latitude': f'{lats[i]:.4f}',
                             'longitude': f'{lons[i]:.4f}'}, timeout=30)
    single = r.json()['elevation'][0]
    print(f'pt{i} ({lats[i]:.4f},{lons[i]:.4f})  batch={elevs[i]}  '
          f'single={single}  {"OK" if abs(elevs[i]-single) < 1 else "<<< MISMATCH"}')

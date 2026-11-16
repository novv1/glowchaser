# -*- coding: utf-8 -*-
"""临时调试：批量 vs 单点海拔对比 + 坐标验证"""
import sys

sys.path.insert(0, r'e:\Glowchaser')
import requests   # noqa: E402
from app.services import terrain as t   # noqa: E402
from app.core import cities as citydb   # noqa: E402

lat, lon = 22.2710, 113.5767
lats, lons = t._sample_coords(lat, lon)
# az=315 2km 的点
plat, plon = citydb.destination_point(lat, lon, 315.0, 2000.0)
print('az315 2km point:', plat, plon)
# 单点请求
r = requests.get('https://api.open-meteo.com/v1/elevation',
                 params={'latitude': plat, 'longitude': plon}, timeout=30)
print('single:', r.json())
# 批量里同点
idx = 1 + 14 * 2 + 0   # az 是第 15 个方位（i=14），d=2km j=0
r2 = requests.get('https://api.open-meteo.com/v1/elevation',
                  params={'latitude': ','.join(f'{x:.4f}' for x in lats),
                          'longitude': ','.join(f'{x:.4f}' for x in lons)},
                  timeout=60)
el = r2.json().get('elevation')
print('batch same idx:', el[idx], 'batch len:', len(el) if el else None)
print('input order lat[:5]:', lats[:5])
print('input order lon[:5]:', lons[:5])

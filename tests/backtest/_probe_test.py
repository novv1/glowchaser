# -*- coding: utf-8 -*-
"""临时测试：站点探测（Elevation API + 启发式光污染）"""
import sys
import time

sys.path.insert(0, r'e:\Glowchaser')
from app.services import terrain as t   # noqa: E402

t0 = time.time()
out = t.probe_site(22.2710, 113.5767)   # 珠海
dt = time.time() - t0
print(f'elapsed {dt:.1f}s')
print('elevation:', out['elevation'], 'm')
print('horizon_mask:', out['horizon_mask'])
print('bortle:', out['bortle'], '(', out['bortle_src'], ')')
print('coastal:', out.get('coastal'))
# 二次调用应命中缓存
t0 = time.time()
t.probe_site(22.2710, 113.5767)
print(f'cache hit: {time.time() - t0:.3f}s')

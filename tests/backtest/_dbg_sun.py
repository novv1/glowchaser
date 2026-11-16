# -*- coding: utf-8 -*-
"""临时调试：sun_times 遮蔽修正"""
import sys
from datetime import datetime

sys.path.insert(0, r'e:\Glowchaser')
from app.core import astronomy as astro   # noqa: E402
from app.services import terrain as t     # noqa: E402

d = datetime(2026, 9, 5)
si = t.probe_site(22.2710, 113.5767)
mask = si['horizon_mask']
print('mask:', mask)
fn = lambda az: t.horizon_alt_at(mask, az)
print('ha@265:', fn(265.0), ' ha@81:', fn(81.0))
s0 = astro.sun_times(d, 22.2710, 113.5767, 8.0)
print('base sunset:', s0.get('sunset'), 'az', s0.get('sunset_az'))
s1 = astro.sun_times(d, 22.2710, 113.5767, 8.0, horizon_fn=fn)
print('mod  keys:', sorted(s1.keys()))
print('mod  sunset:', s1.get('sunset'), 'az', s1.get('sunset_az'))

# -*- coding: utf-8 -*-
"""临时计时脚本：并行化后冷缓存回归"""
import sys
import time

sys.path.insert(0, r'e:\Glowchaser')
from app.services import report as rpt   # noqa: E402

t0 = time.time()
j = rpt.build_report(22.2710, 113.5767, '2026-09-06', 'sunset', None, 2.0)
dt = time.time() - t0
nc = j.get('nowcast') or {}
si = j.get('site_info') or {}
import json
print(f'ELAPSED {dt:.1f}s  prob={j["probability"]}  verdict={j["verdict"]}  '
      f'rev={j["reverse_burn"]["score"]}  week_n={len(j["week"])}  '
      f'nc_mode={nc.get("mode")}  '
      f'timeline={len(j["cross_section"]["timeline"])}  '
      f'models={len(j["models"])}')
print('site_info:', json.dumps(
    {k: v for k, v in si.items() if k != 'horizon_mask'},
    ensure_ascii=False))
print('horizon_mask:', si.get('horizon_mask'))

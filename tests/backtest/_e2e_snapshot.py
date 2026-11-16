# -*- coding: utf-8 -*-
"""临时脚本：端到端快照（落地前后对比用，直接调用报告构建函数）"""
import json
import sys
import traceback

sys.path.insert(0, r'e:\Glowchaser')

from app.services import report as rpt   # noqa: E402

CASES = [
    ('zhuhai_today', (22.2710, 113.5767, '2026-09-05', 'sunset', None, 2.0)),
    ('shenzhen_tmoro_rise', (22.5431, 114.0579, '2026-09-06', 'sunrise',
                             None, 2.0)),
    ('shanghai_case', (31.2397, 121.4998, '2026-09-07', 'sunset', None, 2.0)),
]

out = {}
for name, args in CASES:
    try:
        j = rpt.build_report(*args)
    except Exception as e:
        traceback.print_exc()
        out[name] = {'error': str(e)[:200]}
        continue
    out[name] = {
        'probability': j.get('probability'),
        'verdict': j.get('verdict'),
        'raw': (j.get('main') or {}).get('raw'),
        'dims': (j.get('main') or {}).get('dims'),
        'credibility': (j.get('consensus') or {}).get('credibility'),
        'model_probs': {k: (v or {}).get('probability')
                        for k, v in (j.get('model_results') or {}).items()},
    }
    print(name, '->', out[name]['probability'], out[name]['verdict'],
          'raw=', out[name]['raw'])

path = sys.argv[1] if len(sys.argv) > 1 else 'e2e_before.json'
json.dump(out, open(r'e:\Glowchaser\tests\backtest' + '\\' + path, 'w',
                    encoding='utf-8'), ensure_ascii=False, indent=1)
print('saved', path)

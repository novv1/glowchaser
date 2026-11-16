# -*- coding: utf-8 -*-
"""临时脚本：检查 EAC4 variable 参数 schema 与 particulate 完整名"""
import json
import re

import cdsapi

c = cdsapi.Client()
r = c.session.get(
    'https://ads.atmosphere.copernicus.eu/api/retrieve/v1/processes/'
    'cams-global-reanalysis-eac4')
j = r.json()
inp = j.get('inputs', {})
var = inp.get('variable', {})
print('variable schema keys:', list(var) if isinstance(var, dict) else var)
s = json.dumps(var)
m = sorted(set(re.findall(r'particulate_matter_[a-z0-9_]*', s)))
print('particulate full names:', m)
# 打印 variable 相关的原始片段
i = s.find('particulate_matter_2')
print(s[max(0, i - 120):i + 120])

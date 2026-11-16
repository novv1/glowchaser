# -*- coding: utf-8 -*-
"""临时调试 RH 匹配"""
import json
from datetime import datetime, timedelta

rh = json.load(open('era5_rh_shanghai.json', encoding='utf-8'))
print('sample times:', rh['time'][10:13])
print('sample values 300hPa:', rh['relative_humidity_300hPa'][10:13])

# 模拟 2022-06-24 日落
t_local = datetime(2022, 6, 24, 18, 55)
dt_utc = t_local - timedelta(hours=8)
tkey = dt_utc.strftime('%Y-%m-%dT%H:00')
print('tkey:', tkey, 'in times:', tkey in rh['time'])
i = rh['time'].index(tkey)
print('rh300@i:', rh['relative_humidity_300hPa'][i])

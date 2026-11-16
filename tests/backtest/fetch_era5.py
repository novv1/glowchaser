# -*- coding: utf-8 -*-
"""拉取上海 2022-06-24 ~ 2023-12-30 ERA5 再分析特征（sunsetbot 统计窗口）"""
import json
import os
import requests

URL = 'https://archive-api.open-meteo.com/v1/archive'
VARS = ['cloud_cover', 'cloud_cover_low', 'cloud_cover_mid', 'cloud_cover_high',
        'precipitation', 'pressure_msl', 'relative_humidity_2m',
        'temperature_2m', 'wind_speed_10m', 'wind_direction_10m']
# 陆家嘴观测点（sunsetbot 记录地点）
LAT, LON = 31.2397, 121.4998
START, END = '2022-06-24', '2023-12-30'

OUT = os.path.join(os.path.dirname(__file__), 'era5_shanghai.json')


def main():
    params = {
        'latitude': LAT, 'longitude': LON,
        'hourly': ','.join(VARS),
        'start_date': START, 'end_date': END,
        'timezone': 'Asia/Shanghai',
    }
    print('fetching ERA5', START, '->', END, '...')
    r = requests.get(URL, params=params, timeout=180)
    r.raise_for_status()
    data = r.json()
    hourly = data.get('hourly', {})
    n = len(hourly.get('time', []))
    print('hours:', n)
    json.dump({'meta': {'lat': LAT, 'lon': LON, 'start': START, 'end': END,
                        'vars': VARS},
               'hourly': hourly},
              open(OUT, 'w', encoding='utf-8'), ensure_ascii=False)
    print('saved ->', OUT)


if __name__ == '__main__':
    main()

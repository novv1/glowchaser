# -*- coding: utf-8 -*-
"""临时：连发报告请求检查偶发 500"""
import sys

sys.path.insert(0, r'e:\Glowchaser')
import requests   # noqa: E402

for i in range(3):
    r = requests.get(
        'http://localhost:8000/api/report',
        params={'lat': 22.271, 'lon': 113.5767, 'date': '2026-09-06',
                'type': 'sunset', 'azimuth': 265, 'tol': 2}, timeout=120)
    n = len(r.json().get('alignment', [])) if r.status_code == 200 else -1
    print(f'#{i} status={r.status_code} align={n} len={len(r.content)}')

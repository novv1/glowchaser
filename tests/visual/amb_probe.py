# -*- coding: utf-8 -*-
"""定位环境光层的块状瑕疵：逐个关掉一层再截图对比。"""
import os
import subprocess
import sys
import time

import requests
from playwright.sync_api import sync_playwright

ROOT = r'E:\Glowchaser'
OUT = os.path.join(ROOT, 'tests', 'visual', 'out')
sys.path.insert(0, os.path.join(ROOT, 'tests', 'visual'))

PORT = 8177
CASES = [
    ('amb_1_asis', ''),
    ('amb_2_nograin', '.amb-grain{display:none !important}'),
    ('amb_3_noblur', '.amb-blob{filter:none !important;will-change:auto !important}'),
    ('amb_4_nostars', '.amb-stars{display:none !important}'),
    ('amb_5_none', '.ambient{display:none !important}'),
]

env = dict(os.environ)
env['PYTHONPATH'] = ROOT
server = subprocess.Popen(
    [sys.executable, '-m', 'uvicorn', 'app.main:app', '--host', '127.0.0.1',
     '--port', str(PORT)], cwd=ROOT, env=env,
    stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
try:
    t0 = time.time()
    while time.time() - t0 < 30:
        try:
            if requests.get(f'http://127.0.0.1:{PORT}/api/cities',
                            timeout=2).status_code == 200:
                break
        except Exception:
            pass
        time.sleep(0.4)
    import mock_report
    with sync_playwright() as p:
        browser = p.chromium.launch(channel='msedge', headless=True)
        page = browser.new_page(viewport={'width': 1280, 'height': 900},
                                device_scale_factor=2)
        page.route('**/api/report*', lambda r: r.abort())
        page.route('**/api/align*', lambda r: r.abort())
        page.goto(f'http://127.0.0.1:{PORT}/', wait_until='domcontentloaded')
        page.wait_for_function('typeof render === "function"', timeout=30000)
        page.wait_for_timeout(1800)
        bundle = mock_report.build(kind='sunset', probability=58, aod=0.22)
        page.evaluate('(r) => { window.__gcCurReport = r; curReport = r;'
                      ' render(); }', bundle)
        for dark in (False, True):
            page.evaluate('(d) => window.__setThemeMode(d ? "dark" : "light")',
                          dark)
            page.wait_for_timeout(500)
            for name, css in CASES:
                page.evaluate("""(css) => {
                  let el = document.getElementById('__probe');
                  if (!el) { el = document.createElement('style');
                             el.id = '__probe';
                             document.head.appendChild(el); }
                  el.textContent = css;
                }""", css)
                page.wait_for_timeout(400)
                tag = ('dark_' if dark else 'light_') + name
                page.screenshot(path=os.path.join(OUT, 'zprobe_%s.png' % tag))
                print('saved zprobe_%s.png' % tag)
        browser.close()
finally:
    server.terminate()
    try:
        server.wait(timeout=5)
    except Exception:
        server.kill()

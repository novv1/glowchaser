# -*- coding: utf-8 -*-
"""交互层回归闸门：折叠高度 / 横向溢出 / 控制台报错 / 三档截图。

用法：
    python tests/visual/ui_audit.py --mock                 # 离线（拦截 report/align）
    python tests/visual/ui_audit.py --real --city 珠海      # 真取数（耗额度，慎用）
    python tests/visual/ui_audit.py --mock --mobile        # 390 宽

产出：tests/visual/out/ui_audit/ 下的截图 + 控制台 JSON 摘要。
退出码：有 FAIL 项时为 1，可直接进 CI。
"""
import argparse
import json
import os
import subprocess
import sys
import time

import requests
from playwright.sync_api import sync_playwright

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
OUT = os.path.join(ROOT, 'tests', 'visual', 'out', 'ui_audit')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# 验收阈值（docs/frontend-single-theme-spec.md）
MAX_COLLAPSED_HEIGHT = 3200


def wait_server(port, timeout=40):
    url = 'http://127.0.0.1:%d/api/cities' % port
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            if requests.get(url, timeout=2).status_code == 200:
                return True
        except Exception:
            pass
        time.sleep(0.4)
    return False


def run(port, args):
    env = dict(os.environ)
    env['PYTHONPATH'] = ROOT
    server = subprocess.Popen(
        [sys.executable, '-m', 'uvicorn', 'app.main:app',
         '--host', '127.0.0.1', '--port', str(port)],
        cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
    results = []
    try:
        if not wait_server(port):
            raise SystemExit('server 未就绪')
        with sync_playwright() as p:
            browser = p.chromium.launch(channel='msedge', headless=True)
            for w, h, label in [(1440, 900, '1440'), (1280, 800, '1280'),
                                (390, 844, '390')]:
                if args.only and args.only != label:
                    continue
                page = browser.new_page(viewport={'width': w, 'height': h},
                                        device_scale_factor=1)
                errors = []
                page.on('console', lambda m: errors.append(m.text)
                        if m.type == 'error' else None)
                page.on('pageerror', lambda e: errors.append('pageerror: ' + str(e)))
                if args.mock:
                    page.route('**/api/report*', lambda r: r.abort())
                    page.route('**/api/align*', lambda r: r.abort())
                page.goto('http://127.0.0.1:%d/' % port,
                          wait_until='domcontentloaded')
                page.wait_for_function(
                    'typeof render === "function"', timeout=30000)
                page.wait_for_timeout(1500)

                if args.mock:
                    import mock_report
                    bundle = mock_report.build(kind='sunset', probability=68,
                                               aod=0.22, waxing=True)
                    page.evaluate('(r) => { window.__gcCurReport = r;'
                                  ' curReport = r; render(); }', bundle)
                    page.wait_for_timeout(900)
                else:
                    page.click('#btnGo')
                    page.wait_for_function(
                        "document.getElementById('heroCard').style.display"
                        " === 'block'", timeout=180000)
                    page.wait_for_timeout(2500)

                # 场景帧挂载时元素是隐藏的，触发一次 resize 让画布按真实尺寸重建
                page.evaluate("window.dispatchEvent(new Event('resize'))")
                page.wait_for_timeout(500)

                page.screenshot(path=os.path.join(OUT, 'first-%s.png' % label))
                collapsed = page.evaluate(
                    'document.documentElement.scrollHeight')
                overflow_x = page.evaluate(
                    'document.documentElement.scrollWidth - window.innerWidth')

                # 展开全部章节后复测
                page.evaluate("""() => document.querySelectorAll('.tier-toggle')
                    .forEach(b => { if (b.getAttribute('aria-expanded') === 'false') b.click(); })""")
                page.wait_for_timeout(1600)
                expanded = page.evaluate(
                    'document.documentElement.scrollHeight')
                page.screenshot(path=os.path.join(OUT, 'full-%s.png' % label),
                                full_page=True)

                checks = [
                    ('collapsed_height', collapsed, collapsed <= MAX_COLLAPSED_HEIGHT),
                    ('no_horizontal_overflow', overflow_x, overflow_x <= 0),
                    ('no_console_error', len(errors), len(errors) == 0),
                    ('expanded_height', expanded, expanded > collapsed),
                ]
                results.append({
                    'viewport': label,
                    'collapsed_height': collapsed,
                    'expanded_height': expanded,
                    'overflow_x': overflow_x,
                    'errors': errors[:5],
                    'checks': [{'name': n, 'value': v, 'ok': ok}
                               for n, v, ok in checks],
                })
                page.close()
            browser.close()
    finally:
        server.terminate()
        try:
            server.wait(timeout=6)
        except Exception:
            server.kill()

    os.makedirs(OUT, exist_ok=True)
    with open(os.path.join(OUT, 'result.json'), 'w', encoding='utf-8') as f:
        json.dump(results, f, ensure_ascii=False, indent=2)

    failed = 0
    for r in results:
        print('--- %s ---' % r['viewport'])
        for c in r['checks']:
            flag = 'PASS' if c['ok'] else 'FAIL'
            if not c['ok']:
                failed += 1
            print('  [%s] %-24s %s' % (flag, c['name'], c['value']))
        for e in r['errors']:
            print('   ! ' + e[:160])
    print('shots ->', OUT)
    return 1 if failed else 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--port', type=int, default=8155)
    ap.add_argument('--mock', action='store_true')
    ap.add_argument('--real', dest='real', action='store_true')
    ap.add_argument('--only', default=None, choices=['1440', '1280', '390'])
    args = ap.parse_args()
    if not args.mock:
        args.mock = not args.real      # 默认走 mock，安全
    os.makedirs(OUT, exist_ok=True)
    raise SystemExit(run(args.port, args))


if __name__ == '__main__':
    main()

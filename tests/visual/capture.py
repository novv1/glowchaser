# -*- coding: utf-8 -*-
"""界面截图工具（Playwright + Edge）：真实/离线 mock 两种模式。

用法：
    # 霞光模式（真实数据）
    python tests/visual/capture.py --mode glow --city 珠海
    # 霞光模式（离线 mock）
    python tests/visual/capture.py --mode glow --mock --prob 58
    # 悬日月模式（离线 mock，悬日/日出）
    python tests/visual/capture.py --mode align --mock --align-body sun --align-event rise
    # 悬日月模式（真实数据）
    python tests/visual/capture.py --mode align --az 276 --align-event set
"""
import argparse
import os
import subprocess
import sys
import time

import requests
from playwright.sync_api import sync_playwright

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
OUT_DEFAULT = os.path.join(ROOT, 'tests', 'visual', 'out')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def wait_server(port, timeout=30):
    url = f'http://127.0.0.1:{port}/api/cities'
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            if requests.get(url, timeout=2).status_code == 200:
                return True
        except Exception:
            pass
        time.sleep(0.4)
    return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--mode', default='glow', choices=['glow', 'align'])
    ap.add_argument('--port', type=int, default=8123)
    ap.add_argument('--out', default=OUT_DEFAULT)
    ap.add_argument('--az', type=float, default=270.0)
    ap.add_argument('--city', default=None)
    ap.add_argument('--date', default=None)
    ap.add_argument('--sky-offset', type=int, default=0)
    ap.add_argument('--pv-offset', type=int, default=0)
    ap.add_argument('--mock', action='store_true')
    ap.add_argument('--kind', default='sunset', choices=['sunset', 'sunrise'])
    ap.add_argument('--prob', type=int, default=58)
    ap.add_argument('--aod', type=float, default=0.22)
    ap.add_argument('--moon-waning', action='store_true')
    ap.add_argument('--align-body', default='sun', choices=['sun', 'moon'])
    ap.add_argument('--align-event', default='rise', choices=['rise', 'set'])
    ap.add_argument('--cam-lat', type=float, default=None)
    ap.add_argument('--cam-lon', type=float, default=None)
    ap.add_argument('--aov', type=float, default=3.0)
    ap.add_argument('--elev', type=float, default=0.0)
    ap.add_argument('--align-no-window', action='store_true')
    args = ap.parse_args()

    os.makedirs(args.out, exist_ok=True)
    env = dict(os.environ)
    env['PYTHONPATH'] = ROOT
    server = subprocess.Popen(
        [sys.executable, '-m', 'uvicorn', 'app.main:app',
         '--host', '127.0.0.1', '--port', str(args.port)],
        cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
    try:
        if not wait_server(args.port):
            raise SystemExit('server 未就绪')
        url = f'http://127.0.0.1:{args.port}/'
        with sync_playwright() as p:
            browser = p.chromium.launch(channel='msedge', headless=True)
            page = browser.new_page(viewport={'width': 1280, 'height': 1000},
                                    device_scale_factor=2)
            errors = []
            page.on('console',
                    lambda m: errors.append('console: ' + m.text)
                    if m.type == 'error' else None)
            page.on('pageerror',
                    lambda e: errors.append('pageerror: ' + str(e)
                                          + ' | ' + str(getattr(e, 'stack', ''))[:300]))
            if args.mock:
                page.route('**/api/report*', lambda route: route.abort())
                page.route('**/api/align*', lambda route: route.abort())
            page.goto(url, wait_until='domcontentloaded')
            page.wait_for_function(
                'typeof renderSky === "function" '
                '&& typeof renderAlignPage === "function"', timeout=30000)
            page.wait_for_timeout(2200)   # 等 init/loadReport（被拦截）结束

            if args.mode == 'align':
                page.click('#modeSeg button[data-v="align"]')
                page.wait_for_timeout(700)
                if args.mock:
                    import mock_report
                    bundle = mock_report.build_align(
                        kind=args.kind, probability=args.prob, aod=args.aod,
                        waxing=not args.moon_waning, target_az=args.az)
                    page.evaluate(
                        '(d) => { const c = curCity();'
                        ' alignState.data = d;'
                        ' alignState.key = alignRequestKey(c,'
                        '   document.getElementById("dateSel").value);'
                        ' alignState.body = "%s"; alignState.period = "%s";'
                        ' rebuildModeUI(); renderAlignPage(); }'
                        % (args.align_body, args.align_event), bundle)
                else:
                    if args.cam_lat is not None:
                        page.evaluate(
                            '(c) => { alignState.cam = {lat: c[0], lon: c[1],'
                            ' name: "巧摄 .pft"};'
                            ' alignState.az = c[2]; alignState.aov = c[3];'
                            ' alignState.elev = c[4]; syncAlignControls(); }',
                            [args.cam_lat, args.cam_lon, args.az, args.aov,
                             args.elev])
                    else:
                        page.fill('#azInput', str(args.az))
                    page.click('#btnAlign')
                    page.wait_for_function(
                        'alignState.data && alignState.data.alignment_meta'
                        ' && alignState.data.alignment_meta.target_az === %s'
                        % float(args.az), timeout=180000)
                    page.wait_for_timeout(800)
                    page.click('#subSeg button[data-v="%s"]' % args.align_body)
                    page.wait_for_timeout(2500)
                    page.click('#alignEventSeg button[data-v="%s"]'
                               % args.align_event)
                if args.mock and args.align_no_window:
                    page.evaluate(
                        '() => { alignState.data.alignment = [];'
                        ' renderAlignPage(); }')
                    page.wait_for_timeout(600)
                page.wait_for_timeout(900)
                page.screenshot(path=os.path.join(args.out, 'align_full.png'),
                                full_page=True)
                for sel, name in [
                        ('#alignCard', 'align.png'),
                        ('#celestialCanvas', 'celestial.png'),
                        ('#alignDetailCard', 'align_detail.png'),
                        ('#alignDirCard', 'align_dir.png'),
                        ('#alignWeekCard', 'align_week.png')]:
                    try:
                        page.locator(sel).screenshot(
                            path=os.path.join(args.out, name), timeout=8000)
                    except Exception as e:
                        print('shot skip %s: %s' % (name, str(e)[:60]))
                page.wait_for_timeout(600)
                try:
                    page.click('#btnAlignShare')
                    page.wait_for_timeout(1200)
                    page.locator('#alignSharePreview').screenshot(
                        path=os.path.join(args.out, 'align_share.png'))
                except Exception as e:
                    print('share shot skip:', str(e)[:80])
            else:
                if args.mock:
                    import mock_report
                    bundle = mock_report.build(
                        kind=args.kind, probability=args.prob, aod=args.aod,
                        waxing=not args.moon_waning)
                    page.evaluate(
                        '(r) => { window.__gcCurReport = r; curReport = r;'
                        ' render(); }', bundle)
                    page.wait_for_timeout(700)
                else:
                    if args.city:
                        page.select_option('#citySel', label=args.city)
                    if args.date:
                        page.eval_on_selector(
                            '#dateSel', '(el, v) => { el.value = v; }',
                            args.date)
                        page.evaluate('syncDateUI()')
                    page.wait_for_selector('#heroCard', state='visible',
                                           timeout=180000)
                    page.wait_for_timeout(1200)
                if args.sky_offset:
                    page.eval_on_selector(
                        '#skySlider',
                        '(el, v) => { el.value = v;'
                        ' el.dispatchEvent(new Event("input")); }',
                        str(args.sky_offset))
                    page.wait_for_timeout(400)
                page.screenshot(path=os.path.join(args.out, 'full.png'),
                                full_page=True)
                page.locator('#skyCard').screenshot(
                    path=os.path.join(args.out, 'sky.png'))
                page.locator('#heroCard').screenshot(
                    path=os.path.join(args.out, 'hero.png'))

            if errors:
                print('PAGE ERRORS (%d):' % len(errors))
                for e in errors[:10]:
                    print('  -', e[:200])
            else:
                print('PAGE ERRORS: none')
            print('screenshots ->', args.out)
            browser.close()
    finally:
        server.terminate()
        try:
            server.wait(timeout=5)
        except Exception:
            server.kill()


if __name__ == '__main__':
    main()

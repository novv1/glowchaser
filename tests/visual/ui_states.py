# -*- coding: utf-8 -*-
"""交互状态截图：加载 / 错误 / 缓存回退 / 滚动收窄 / 卡片柔光 / 窄屏顶栏。

这些状态没有数据驱动，走不了 theme_preview 那条路，但在 v2.25 的视觉重构里
改动最大（骨架屏、进度条、顶栏收窄、滚动进度、回到顶部、卡片柔光）。
全部离线（拦截 /api/*），不需要网络与凭据。

用法：
    python tests/visual/ui_states.py            # 深浅各一套 + 窄屏
    python tests/visual/ui_states.py --skin deepspace
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


def wait_geo(page, timeout=20000):
    """等广东省底图注册完，再注入报告。

    不等的话 geo 还没注册，地图 setOption 会抛错（v2.25 已让地图卡退化，
    但截图要的是正常态）。
    """
    try:
        page.wait_for_function('window.gdGeoReady === true', timeout=timeout)
    except Exception:
        page.wait_for_timeout(1500)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--port', type=int, default=8131)
    ap.add_argument('--out', default=OUT_DEFAULT)
    ap.add_argument('--skin', default='ember',
                    choices=['ember', 'skyshift', 'deepspace', 'inkpaper'])
    args = ap.parse_args()

    os.makedirs(args.out, exist_ok=True)
    env = dict(os.environ)
    env['PYTHONPATH'] = ROOT
    server = subprocess.Popen(
        [sys.executable, '-m', 'uvicorn', 'app.main:app', '--host', '127.0.0.1',
         '--port', str(args.port)], cwd=ROOT, env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
    errors = []
    try:
        if not wait_server(args.port):
            raise SystemExit('server 未就绪')
        with sync_playwright() as p:
            browser = p.chromium.launch(channel='msedge', headless=True)

            def new_page(width, height):
                pg = browser.new_page(
                    viewport={'width': width, 'height': height},
                    device_scale_factor=2)
                pg.on('pageerror', lambda e: errors.append(str(e)[:200]))
                return pg

            # ── ① 加载态：把 /api/report 挂住（不 fulfill），骨架屏一直显示 ──
            page = new_page(1280, 900)
            page.route('**/api/report*', lambda r: None)
            page.goto(f'http://127.0.0.1:{args.port}/', wait_until='domcontentloaded')
            page.wait_for_function('typeof render === "function"', timeout=30000)
            page.evaluate('(s) => window.__setSkin(s)', args.skin)
            wait_geo(page)
            page.click('#btnGo')     # v2.36：进入不再自动查询，手动触发加载态
            page.wait_for_timeout(600)
            page.screenshot(path=os.path.join(args.out, 'ui_loading.png'))
            print('saved ui_loading.png')
            page.wait_for_timeout(3200)      # 等出「已等待 Ns」
            page.locator('#loading').screenshot(
                path=os.path.join(args.out, 'ui_loading_elapsed.png'))
            print('saved ui_loading_elapsed.png')
            # 取消按钮：应切到「查询已取消」错误卡
            page.click('#btnLoadCancel')
            page.wait_for_timeout(900)
            page.screenshot(path=os.path.join(args.out, 'ui_cancelled.png'))
            print('saved ui_cancelled.png')
            page.close()

            # ── ② 错误态：直接失败 ──
            page = new_page(1280, 900)
            page.route('**/api/report*', lambda r: r.abort())
            page.route('**/api/align*', lambda r: r.abort())
            page.goto(f'http://127.0.0.1:{args.port}/', wait_until='domcontentloaded')
            page.wait_for_function('typeof render === "function"', timeout=30000)
            page.evaluate('(s) => window.__setSkin(s)', args.skin)
            wait_geo(page)
            page.click('#btnGo')     # v2.36：进入不再自动查询，手动触发失败
            page.wait_for_selector('#errCard', state='visible', timeout=20000)
            page.wait_for_timeout(700)
            page.screenshot(path=os.path.join(args.out, 'ui_error.png'))
            print('saved ui_error.png')

            # ── ③ 缓存回退横幅：先落一份缓存，再让请求失败 ──
            import mock_report
            bundle = mock_report.build(kind='sunset', probability=58)
            page.evaluate("""(r) => {
              const c = curCity();
              const d = document.getElementById('dateSel').value;
              localStorage.setItem('gc_last_' + c.id + '_' + d + '_sunset',
                                   JSON.stringify(r));
            }""", bundle)
            page.reload(wait_until='domcontentloaded')
            page.wait_for_function('typeof render === "function"', timeout=30000)
            wait_geo(page)
            page.click('#btnGo')     # v2.36：进入不再自动查询，手动触发缓存回退
            page.wait_for_selector('#cacheBanner', state='visible', timeout=20000)
            page.wait_for_timeout(900)
            page.screenshot(path=os.path.join(args.out, 'ui_cache_fallback.png'))
            print('saved ui_cache_fallback.png')

            # ── ④ 正常报告 + 滚动：顶栏收窄 / 滚动进度 / 回到顶部 ──
            page.evaluate(
                '(r) => { window.__gcCurReport = r; curReport = r; render(); }',
                bundle)
            page.wait_for_timeout(800)
            page.evaluate('() => window.scrollTo(0, 1400)')
            page.wait_for_timeout(900)
            page.screenshot(path=os.path.join(args.out, 'ui_scrolled.png'))
            print('saved ui_scrolled.png')

            # ── ⑤ 卡片柔光：鼠标停在卡片中部 ──
            page.evaluate('() => window.scrollTo(0, 900)')
            page.wait_for_timeout(500)
            box = page.locator('#bestCard').bounding_box()
            if box:
                page.mouse.move(box['x'] + box['width'] * 0.72,
                                box['y'] + box['height'] * 0.6, steps=6)
                page.wait_for_timeout(700)
                page.locator('#bestCard').screenshot(
                    path=os.path.join(args.out, 'ui_card_spotlight.png'))
                print('saved ui_card_spotlight.png')

            # ── ⑥ 切 Tab：地图 / 天文（图表与地图的配色跟随主题） ──
            page.evaluate('() => { showTab("maps"); window.scrollTo(0, 0); }')
            page.wait_for_timeout(1200)
            page.screenshot(path=os.path.join(args.out, 'ui_tab_maps.png'))
            print('saved ui_tab_maps.png')
            page.evaluate('() => showTab("astro")')
            page.wait_for_timeout(1200)
            page.screenshot(path=os.path.join(args.out, 'ui_tab_astro.png'))
            print('saved ui_tab_astro.png')
            page.close()

            # ── ⑦ 窄屏：滚动后顶栏收起次要控件 ──
            mob = new_page(375, 760)
            mob.route('**/api/report*', lambda r: r.abort())
            mob.route('**/api/align*', lambda r: r.abort())
            mob.goto(f'http://127.0.0.1:{args.port}/', wait_until='domcontentloaded')
            mob.wait_for_function('typeof render === "function"', timeout=30000)
            wait_geo(mob)
            # 先等被 abort 的那次请求落定：否则注入后它才失败，
            # 会拍出「错误卡 + 报告并存」——那是脚本时序，不是页面状态
            try:
                mob.click('#btnGo')  # v2.36：进入不再自动查询
                mob.wait_for_selector('#errCard', state='visible', timeout=8000)
            except Exception:
                pass
            mob.evaluate('(r) => { window.__gcCurReport = r; curReport = r;'
                         ' render(); }', bundle)
            mob.wait_for_timeout(900)
            mob.screenshot(path=os.path.join(args.out, 'ui_mobile_top.png'))
            print('saved ui_mobile_top.png')
            mob.evaluate('() => window.scrollTo(0, 900)')
            mob.wait_for_timeout(900)
            mob.screenshot(path=os.path.join(args.out, 'ui_mobile_scrolled.png'))
            print('saved ui_mobile_scrolled.png')
            mob.close()

            if errors:
                print('PAGE ERRORS:', errors[:5])
            else:
                print('PAGE ERRORS: none')
            browser.close()
    finally:
        server.terminate()
        try:
            server.wait(timeout=5)
        except Exception:
            server.kill()


if __name__ == '__main__':
    main()

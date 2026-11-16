# -*- coding: utf-8 -*-
"""主题候选预览：用真实页面 + mock 报告渲染各候选配色，产出对比截图。

配色以 CSS 变量覆盖的方式注入（不修改 style.css），所以预览与最终落地效果一致。

用法：
    python tests/visual/theme_preview.py                 # 全部候选
    python tests/visual/theme_preview.py --only ember    # 只跑一个候选
输出：
    tests/visual/out/theme_<palette>_<variant>.png
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


# ---------------- 配色工具 ----------------
def hex2rgb(h):
    h = h.lstrip('#')
    if len(h) == 3:
        h = ''.join(c * 2 for c in h)
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def mix(c1, c2, t):
    """按 t 把 c1 混向 c2（t=1 得到 c2）。"""
    a, b = hex2rgb(c1), hex2rgb(c2)
    return '#%02x%02x%02x' % tuple(
        round(a[i] + (b[i] - a[i]) * t) for i in range(3))


def rgba(c, alpha):
    r, g, b = hex2rgb(c)
    return 'rgba(%d,%d,%d,%s)' % (r, g, b, alpha)


def vars_of(s, dark):
    """由精简 spec 补齐 style.css 用到的全部变量。"""
    accent, accent2 = s['accent'], s['accent2']
    ink, ink2 = s['ink'], s['ink2']
    page, card, line = s['page'], s['card'], s['line']
    item, seg = s.get('item', mix(card, ink, .04)), s.get('seg', mix(card, ink, .07))
    heroa, herob = s['heroa'], s['herob']
    blue = s.get('blue', accent)
    hl_ink = s.get('hl_ink', mix(accent, ink, .45))
    risk = s.get('risk', '#B4402C')
    v = {
        '--accent': accent,
        '--accent-2': accent2,
        '--accent-soft': mix(accent, card, .86),
        '--ink': ink,
        '--ink-2': ink2,
        '--line': line,
        '--card-bg': card,
        '--page-bg': page,
        '--blue': blue,
        '--seg-bg': seg,
        '--seg-active-bg': card if not dark else mix(card, '#ffffff', .06),
        '--item-bg': item,
        '--hero-grad-a': heroa,
        '--hero-grad-b': herob,
        # 渐变深端 + 强调阴影：供按钮/胶囊/Tab 指示器复用（替代原先写死的 #d94f1e）
        '--accent-deep': herob,
        '--shadow-accent': '0 4px 12px ' + rgba(accent, .32),
        '--highlight-bg': s.get('hl_bg', mix(accent, card, .93)),
        '--highlight-line': s.get('hl_line', mix(accent, card, .78)),
        '--highlight-ink': hl_ink,
        '--highlight-title': s.get('hl_title', mix(accent, ink, .3)),
        '--risk-bg': mix(risk, card, .9),
        '--risk-ink': mix(risk, ink, .25),
        '--input-bg': card,
        '--input-line': line,
        '--shadow-card': s.get('shadow', '0 1px 4px rgba(0,0,0,.05)'),
        '--shadow-hero': '0 8px 24px ' + rgba(accent, .25),
        '--chart-axis': ink2,
        '--chart-geo-area': item,
        '--chart-geo-line': line,
        '--chart-geo-label': ink2,
        # 省界描边：--line 画在浅色省份上有如隐形，地图需要一条看得见的界线
        '--chart-geo-border': (mix(line, '#ffffff', .22) if dark
                               else mix(line, ink, .45)),
        # ── 玻璃 / 层次 / 环境光（v2.25 视觉重构新增；同样在预览里生效） ──
        # 卡片与浮层用半透明底色压在环境渐变上，形成「玻璃」层次
        '--glass-bg': rgba(card, .74 if not dark else .68),
        '--glass-line': rgba(ink, .07) if not dark else rgba('#ffffff', .07),
        '--hair': rgba('#ffffff', .85) if not dark else rgba('#ffffff', .06),
        # 三级阴影：静置卡片 / 悬浮导航 / 弹出面板
        # （静置阴影可由 spec 的 shadow 覆盖 —— 纸墨极简就是靠这一项保持扁平）
        '--elev-1': s.get('shadow',
                          '0 1px 2px rgba(0,0,0,.04), 0 6px 20px -8px rgba(0,0,0,.12)'),
        '--elev-2': ('0 2px 8px rgba(0,0,0,.35), 0 16px 40px -16px rgba(0,0,0,.6)'
                     if dark else
                     '0 2px 6px rgba(0,0,0,.05), 0 12px 32px -14px rgba(0,0,0,.16)'),
        '--elev-3': ('0 20px 52px -18px rgba(0,0,0,.75), 0 2px 10px rgba(0,0,0,.4)'
                     if dark else
                     '0 18px 44px -16px rgba(0,0,0,.26), 0 2px 8px rgba(0,0,0,.06)'),
        # 卡片跟随光标的柔光（ spotlight ）
        '--spot': rgba(accent, .13 if dark else .10),
        # 环境背景：页面底色渐变 + 三团极光（主色 / 副色 / 蓝）
        '--page-a': page,
        '--page-b': mix(accent, page, .93),
        '--amb-1': accent,
        '--amb-2': accent2,
        '--amb-3': blue,
        '--amb-op': '.30' if dark else '.40',
        '--grain-op': '.055' if dark else '.03',
        # 骨架屏扫光的高光端
        '--skl-hi': mix(card, '#ffffff', .16) if dark else mix(card, ink, .10),
        # Hero 概率环
        '--ring-a': accent2,
        '--ring-b': mix(accent2, '#ffffff', .55),
        '--hero-glow': rgba(heroa, .5 if dark else .42),
    }
    return v


def css_block(sel, v):
    return sel + '{' + ''.join('%s:%s;' % kv for kv in v.items()) + '}'


# ---------------- 唯一主题 ----------------
# 2026-10-23 决策：前端收敛为单一主题「日暮片场」，不再有皮肤与明暗切换。
# 只保留一套 spec；生成器仍负责输出 style.css 的令牌块，保证预览与实现同源。
PALETTES = [
    {
        'id': 'cinema', 'name': '日暮片场',
        'note': '电影帧质感：近黑片场底 + 金/青双色调 + 米白字幕（唯一主题）',
        'variants': [
            ('dark', True, 'sunset', dict(
                accent='#E8B06A', accent2='#6FA8A0', ink='#ECE7DD', ink2='#9A9488',
                page='#0C0E11', card='#12151B', line='#262B33', blue='#6FA8A0',
                heroa='#E07A44', herob='#8A4A5E')),
        ],
    },
]


def emit_css():
    """把唯一主题输出为 style.css 的令牌块（预览 = 实现，同一份色值）。

    用法：python tests/visual/theme_preview.py --emit-css
    """
    pal = PALETTES[0]
    label, dark, _kind, spec = pal['variants'][0]
    lines = ['/* ── 单一主题「日暮片场」令牌'
             '（由 tests/visual/theme_preview.py --emit-css 生成，勿手改） ── */']
    lines.append(css_block(':root', vars_of(spec, dark)))
    return '\n'.join(lines)


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


def apply_theme(page, skin, mode, kind):
    """走真实主题接口切皮肤/深浅，再按事件重渲染 mock 报告。

    不注入任何 CSS —— 预览即实现，皮肤坏了这里就会看出来。
    """
    import mock_report
    bundle = mock_report.build(kind=kind, probability=58, aod=0.22,
                               waxing=True)
    page.evaluate(
        '(a) => {'
        ' window.__setSkin(a[0]);'
        ' window.__setThemeMode(a[1]);'
        ' window.__setSky(a[3] === "sunrise" ? "dawn" : "dusk");'
        ' glowType = a[3];'
        ' window.__gcCurReport = a[4]; curReport = a[4];'
        ' syncDateUI(); render();'
        '}', [skin, mode, kind, kind, bundle])
    page.wait_for_timeout(900)


# 出图清单：(皮肤, 深浅, 事件)
PREVIEWS = [
    ('ember', 'light', 'sunset'),
    ('ember', 'dark', 'sunset'),
    ('skyshift', 'light', 'sunset'),
    ('skyshift', 'dark', 'sunset'),
    ('skyshift', 'light', 'sunrise'),
    ('skyshift', 'dark', 'sunrise'),
    ('deepspace', 'dark', 'sunset'),
    ('deepspace', 'light', 'sunset'),
    ('inkpaper', 'light', 'sunset'),
    ('inkpaper', 'dark', 'sunset'),
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--port', type=int, default=8124)
    ap.add_argument('--out', default=OUT_DEFAULT)
    ap.add_argument('--only', default=None)
    ap.add_argument('--emit-css', action='store_true',
                    help='只输出 CSS 令牌块（供 style.css 使用），不截图')
    args = ap.parse_args()

    if args.emit_css:
        print(emit_css())
        return

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
        with sync_playwright() as p:
            browser = p.chromium.launch(channel='msedge', headless=True)
            page = browser.new_page(viewport={'width': 1280, 'height': 980},
                                    device_scale_factor=2)
            errors = []
            page.on('pageerror', lambda e: errors.append(str(e)[:200]))
            page.route('**/api/report*', lambda route: route.abort())
            page.route('**/api/align*', lambda route: route.abort())
            page.goto(f'http://127.0.0.1:{args.port}/',
                      wait_until='domcontentloaded')
            page.wait_for_function(
                'typeof render === "function"', timeout=30000)
            page.wait_for_timeout(2200)

            for skin, mode, kind in PREVIEWS:
                if args.only and skin != args.only:
                    continue
                apply_theme(page, skin, mode, kind)
                page.evaluate('() => window.scrollTo(0, 0)')
                page.wait_for_timeout(300)
                tag = '%s_%s%s' % (skin, mode,
                                   '_dawn' if kind == 'sunrise' else '')
                path = os.path.join(args.out, 'theme_%s.png' % tag)
                page.screenshot(path=path)
                print('saved', os.path.basename(path))

            # 主题面板本身也要出图（面板是新增 UI，必须眼见为实）
            if not args.only:
                page.evaluate('() => window.__themeToggle()')
                page.wait_for_timeout(420)
                page.screenshot(path=os.path.join(args.out, 'theme_panel.png'))
                print('saved theme_panel.png')

            # 窄屏（375px）：底部导航 + 顶栏精简
            mob = browser.new_page(viewport={'width': 375, 'height': 760},
                                   device_scale_factor=2)
            mob.on('pageerror', lambda e: errors.append('mobile: ' + str(e)[:200]))
            mob.route('**/api/report*', lambda route: route.abort())
            mob.route('**/api/align*', lambda route: route.abort())
            mob.goto(f'http://127.0.0.1:{args.port}/',
                     wait_until='domcontentloaded')
            mob.wait_for_function('typeof render === "function"', timeout=30000)
            mob.wait_for_timeout(1800)
            apply_theme(mob, 'ember', 'light', 'sunset')
            mob.screenshot(path=os.path.join(args.out, 'theme_mobile_375.png'))
            print('saved theme_mobile_375.png')
            mob.close()
            if errors:
                print('PAGE ERRORS:', errors[:5])
            browser.close()
    finally:
        server.terminate()
        try:
            server.wait(timeout=5)
        except Exception:
            server.kill()


if __name__ == '__main__':
    main()

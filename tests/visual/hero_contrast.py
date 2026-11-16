# -*- coding: utf-8 -*-
"""Hero 文字对比度体检：四套皮肤 × 深浅（含昼夜双色的朝/晚霞档）。

白色文字压在渐变上，观感很容易「看着还行、实际读不清」。这里用真实元素
位置取像素：文字是白的、底色比字暗，所以取框内**最暗的一档**像素当底色，
再按 WCAG 相对亮度算对比度（大号粗体 ≥3:1 为 AA 大字号门槛，正文 ≥4.5:1）。

用法：python tests/visual/hero_contrast.py
"""
import os
import subprocess
import sys
import time

import requests
from playwright.sync_api import sync_playwright

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
OUT = os.path.join(ROOT, 'tests', 'visual', 'out')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

PORT = 8188
# (label, skin, mode, kind) —— kind 决定昼夜双色走晚霞档还是朝霞档
CASES = [
    ('ember/light', 'ember', 'light', 'sunset'),
    ('ember/dark', 'ember', 'dark', 'sunset'),
    ('skyshift-dusk/light', 'skyshift', 'light', 'sunset'),
    ('skyshift-dusk/dark', 'skyshift', 'dark', 'sunset'),
    ('skyshift-dawn/light', 'skyshift', 'light', 'sunrise'),
    ('skyshift-dawn/dark', 'skyshift', 'dark', 'sunrise'),
    ('deepspace/light', 'deepspace', 'light', 'sunset'),
    ('deepspace/dark', 'deepspace', 'dark', 'sunset'),
    ('inkpaper/light', 'inkpaper', 'light', 'sunset'),
    ('inkpaper/dark', 'inkpaper', 'dark', 'sunset'),
]


def srgb_to_lin(c):
    c = c / 255
    return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4


def rel_lum(px):
    return (0.2126 * srgb_to_lin(px[0]) + 0.7152 * srgb_to_lin(px[1])
            + 0.0722 * srgb_to_lin(px[2]))


def contrast_vs_white(lum):
    return 1.05 / (lum + 0.05)


def main():
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
        from PIL import Image
        rows = []
        with sync_playwright() as p:
            browser = p.chromium.launch(channel='msedge', headless=True)
            page = browser.new_page(viewport={'width': 1280, 'height': 900},
                                    device_scale_factor=2)
            page.route('**/api/report*', lambda r: r.abort())
            page.route('**/api/align*', lambda r: r.abort())
            page.goto(f'http://127.0.0.1:{PORT}/', wait_until='domcontentloaded')
            page.wait_for_function('typeof render === "function"', timeout=30000)
            # 等底图，避免地图卡抛错打断渲染
            try:
                page.wait_for_function('window.gdGeoReady === true', timeout=15000)
            except Exception:
                pass
            shots = os.path.join(OUT, '_contrast')
            os.makedirs(shots, exist_ok=True)
            for label, skin, mode, kind in CASES:
                bundle = mock_report.build(kind=kind, probability=58)
                page.evaluate(
                    """(a) => {
                      window.__setSkin(a[0]); window.__setThemeMode(a[1]);
                      window.__setSky(a[2] === 'sunrise' ? 'dawn' : 'dusk');
                      glowType = a[2];
                      window.__gcCurReport = a[3]; curReport = a[3];
                      syncDateUI(); render();
                    }""", [skin, mode, kind, bundle])
                page.wait_for_timeout(700)
                page.evaluate('() => window.scrollTo(0, 0)')
                page.wait_for_timeout(250)
                boxes = page.evaluate("""() => {
                  const g = id => {
                    const el = document.getElementById(id);
                    if (!el) return null;
                    const r = el.getBoundingClientRect();
                    return [r.x, r.y, r.width, r.height];
                  };
                  return {sub: g('heroSub'), title: g('heroTitle'),
                          date: g('heroDate'), cred: g('heroCred')};
                }""")
                tag = label.replace('/', '_')
                path = os.path.join(shots, tag + '.png')
                page.screenshot(path=path)
                im = Image.open(path).convert('RGB')
                for key, name in [('sub', '副标题12px'), ('title', '标题25px粗'),
                                  ('date', '日期13px'), ('cred', '来源12px')]:
                    b = boxes.get(key)
                    if not b or b[3] < 2:
                        continue
                    # DPR=2
                    crop = im.crop((int(b[0] * 2), int(b[1] * 2),
                                    int((b[0] + b[2]) * 2),
                                    int((b[1] + b[3]) * 2)))
                    px = list(crop.get_flattened_data())
                    px.sort(key=rel_lum)
                    # 底色 = 最暗的 20%（文字是白的，比底色亮）
                    bg = px[max(0, int(len(px) * 0.2) - 1)]
                    rows.append((label, name, bg, contrast_vs_white(rel_lum(bg))))
            browser.close()

        worst = []
        print('%-22s %-12s %-9s %s' % ('皮肤/深浅', '元素', '底色', '白字对比度'))
        for label, name, bg, c in rows:
            large = ('标题' in name or '副标题' in name)
            need = 3.0 if large else 4.5
            flag = 'OK' if c >= need else ('LOW(大字号尚可)' if c >= 3 else 'FAIL')
            print('%-22s %-12s #%02x%02x%02x  %5.2f:1  %s'
                  % (label, name, bg[0], bg[1], bg[2], c, flag))
            if c < need:
                worst.append((label, name, round(c, 2), need))
        print()
        if worst:
            print('低于门槛的项（大字号门槛 3:1 / 正文 4.5:1）：')
            for w in worst:
                print('  %s / %s  %.2f:1 (need %.1f)' % w)
        else:
            print('全部达标')
        import shutil
        shutil.rmtree(shots, ignore_errors=True)
    finally:
        server.terminate()
        try:
            server.wait(timeout=5)
        except Exception:
            server.kill()


if __name__ == '__main__':
    main()

#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""M7 验收：三档窗口（1366 / 1920 / 390）截图 + 海报同场景 / 性能 / 降级 / 移动端 / 回归检查。

用法：
    python tests/visual/m7_shots.py              # 全跑（三档 + reduced-motion）
    python tests/visual/m7_shots.py --only 390   # 只跑一档
    python tests/visual/m7_shots.py --no-motion  # 跳过 reduced-motion 一档
输出：
    docs/shots/v2.0-M7/<view>/*.png    页面截图 + 海报原图（1080×1920）
    docs/shots/v2.0-M7/report.txt      检查结果 + 关键数字（交付说明底稿）
    docs/shots/v2.0-M7/metrics.json    关键数字（跨档比对用）

检查口径见 v2.0 §7 M7 行 / §4 海报行 / 附录B#4#5 / §2.1 性能红线 / §7 回归表 8 项。
「海报=页面帧同场景」判定：①海报卡与其同参数离屏静帧逐带均值差 ≤8/255（含卡内引擎地景带）；
②**天对天**：海报卡顶部 62%（纯天空）vs 页面画布顶部 62%（地平线在 72%）逐带均值差 ≤12/255；
③生成海报前后 SceneEngine.params() 不变。整卡比页面帧会白吃一个地景带差 —— 海报卡底部是
引擎 stillGround 画的山脊+灯火，页面同位置是 DOM 层 #Lridges/#citylights，两者本就不该相同。
"""
import argparse
import base64
import json
import os
import re
import statistics
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import mock_report                                   # noqa: E402
from playwright.sync_api import sync_playwright       # noqa: E402

VIEWS = {
    '1366': dict(w=1366, h=900, mobile=False, kind='sunset', prob=58,
                 align_body='sun', align_event='set'),
    '1920': dict(w=1920, h=1080, mobile=False, kind='sunrise', prob=72,
                 align_body='moon', align_event='rise'),
    '390': dict(w=390, h=844, mobile=True, kind='sunset', prob=41,
                align_body='sun', align_event='set'),
}

TAB_IDS = ['overview', 'maps', 'atmo', 'astro', 'data']
SKINS = ['cinema', 'skyshift', 'deepspace', 'inkpaper', 'ember']

# 页面内工具：横带均值 / 亮度标准差 / 高亮像素计数（找日月盘）
JS_HELPERS = r"""
window.__m7 = {
  stats(src, sx, sy, sw, sh) {
    const c = document.createElement('canvas'); c.width = sw; c.height = sh;
    const g = c.getContext('2d');
    g.drawImage(src, sx || 0, sy || 0, sw, sh, 0, 0, sw, sh);
    const d = g.getImageData(0, 0, sw, sh).data;
    const bands = [];
    for (let b = 0; b < 3; b++) {
      const y0 = Math.floor(sh * b / 3), y1 = Math.floor(sh * (b + 1) / 3);
      let r = 0, gg = 0, bb = 0, n = 0;
      for (let y = y0; y < y1; y += 2) for (let x = 0; x < sw; x += 2) {
        const i = (y * sw + x) * 4; r += d[i]; gg += d[i + 1]; bb += d[i + 2]; n++;
      }
      bands.push([r / n, gg / n, bb / n]);
    }
    let s = 0, s2 = 0, m = 0;
    for (let y = 0; y < sh; y += 3) for (let x = 0; x < sw; x += 3) {
      const i = (y * sw + x) * 4;
      const l = d[i] * .299 + d[i + 1] * .587 + d[i + 2] * .114;
      s += l; s2 += l * l; m++;
    }
    const mean = s / m;
    return {bands: bands, std: Math.sqrt(Math.max(0, s2 / m - mean * mean)), mean: mean};
  },
  pageSky() {                        /* 页面实景帧：顶部 74%（与海报卡同口径） */
    const cv = document.getElementById('cvSky');
    if (!cv) return null;
    return this.stats(cv, 0, 0, cv.width, Math.round(cv.height * 0.74));
  },
  cardOf(imgId) {                    /* 海报顶部帧整卡 PCARD = (56, 96, 968, 400) */
    const img = document.getElementById(imgId);
    if (!img || !img.src || img.src.indexOf('data:image/png') !== 0) return null;
    return this.stats(img, 56, 96, 968, 400);
  },
  cardSky(imgId) {                   /* 海报卡上空域：顶部 74%，避开 scrim/角标 */
    const img = document.getElementById(imgId);
    if (!img || !img.src || img.src.indexOf('data:image/png') !== 0) return null;
    return this.stats(img, 56, 96, 968, 296);
  },
  cardSkyTop(imgId) {                /* 海报卡「纯天空」区：顶部 62%（不含引擎补的地景带） */
    const img = document.getElementById(imgId);
    if (!img || !img.src || img.src.indexOf('data:image/png') !== 0) return null;
    return this.stats(img, 56, 96, 968, 248);
  },
  pageSkyTop() {                     /* 页面实景帧「纯天空」区：顶部 62%（地平线在 72%） */
    const cv = document.getElementById('cvSky');
    if (!cv) return null;
    return this.stats(cv, 0, 0, cv.width, Math.round(cv.height * 0.62));
  },
  posterRegion(imgId, x, y, w, h) {  /* 海报任意区域统计（地图快照 PMAP 等） */
    const img = document.getElementById(imgId);
    if (!img || !img.src || img.src.indexOf('data:image/png') !== 0) return null;
    return this.stats(img, x, y, w, h);
  },
  stillFrame(p) {                    /* 同参数离屏静帧（与海报卡同尺寸/同口径） */
    const st = window.SceneEngine.renderStill({w: 968, h: 400, params: p || null});
    if (!st) return null;
    return this.stats(st, 0, 0, 968, 296);
  },
  pageStill(p) {                     /* 同参数离屏静帧（页面画布尺寸，只取顶部 62% 天空） */
    const cv = document.getElementById('cvSky');
    if (!cv) return null;
    const st = window.SceneEngine.renderStill({w: cv.width, h: cv.height, params: p || null});
    if (!st) return null;
    return this.stats(st, 0, 0, st.width, Math.round(st.height * 0.62));
  },
  alignPosterParams() {              /* 海报用的那条窗口 → 场景参数（复刻 share.js:299-307 选窗） */
    const SE = window.SceneEngine;
    if (!SE || !SE.mapAlignToScene) return null;
    let w = null;
    try {
      const list = (typeof alignWindowList === 'function') ? alignWindowList() : [];
      const selEl = document.getElementById('pvWindowSel');
      const picked = (selEl && selEl.__list && selEl.value !== '')
        ? selEl.__list[+selEl.value] : null;
      w = (selEl && selEl.__cur) || picked || list[0] || null;
    } catch (e) { return null; }
    if (!w) return null;
    const city = (typeof curCity === 'function') ? curCity() : null;
    const p = SE.mapAlignToScene(w, {cityId: (city && (city.id || city.name)) || ''});
    p.__from = String(w.type || '') + ' ' + String(w.period_name || '');
    return p;
  },
  moonDelta(p) {                     /* 满月盘贡献像素：moon='full' 与 'none' 差分 */    const SE = window.SceneEngine;
    const a = SE.renderStill({w: 968, h: 400, params: p});
    const b = SE.renderStill({w: 968, h: 400,
                              params: Object.assign({}, p, {moon: 'none'})});
    if (!a || !b) return null;
    const c = document.createElement('canvas'); c.width = 968; c.height = 400;
    const g = c.getContext('2d');
    g.drawImage(a, 0, 0);
    const da = g.getImageData(0, 0, 968, 400).data;
    g.clearRect(0, 0, 968, 400);
    g.drawImage(b, 0, 0);
    const db = g.getImageData(0, 0, 968, 400).data;
    let cnt = 0, mx = 0, fx = -1, fy = -1;
    for (let k = 0; k < 968 * 400; k++) {
      const i = k * 4;
      const la = da[i] * .299 + da[i + 1] * .587 + da[i + 2] * .114;
      const lb = db[i] * .299 + db[i + 1] * .587 + db[i + 2] * .114;
      const d = la - lb;
      if (d > 10) { cnt++; if (fx < 0) { fx = k % 968; fy = Math.floor(k / 968); } }
      if (d > mx) mx = d;
    }
    return {count: cnt, maxDelta: mx, firstX: fx, firstY: fy};
  }
};
"""


# ───────────────────────── 小工具 ─────────────────────────

class Rep:
    def __init__(self):
        self.rows = []

    def add(self, view, name, ok, detail=''):
        self.rows.append((view, name, ok, detail))
        tag = 'PASS' if ok is True else ('FAIL' if ok is False else 'INFO')
        print('[%s] %-7s %-24s %s' % (tag, view, name, detail))

    def fails(self):
        return [r for r in self.rows if r[2] is False]

    def counts(self):
        p = sum(1 for r in self.rows if r[2] is True)
        f = sum(1 for r in self.rows if r[2] is False)
        return p, len(self.rows) - p - f, f

    def dump(self, path):
        lines = ['# M7 验收报告（%s）' % time.strftime('%Y-%m-%d %H:%M'), '',
                 '| 视图 | 检查 | 结果 | 数字/说明 |', '| --- | --- | --- | --- |']
        for view, name, ok, detail in self.rows:
            tag = 'PASS' if ok is True else ('FAIL' if ok is False else 'INFO')
            lines.append('| %s | %s | %s | %s |' % (view, name, tag, detail))
        p, w, f = self.counts()
        lines += ['', 'PASS %d / INFO %d（非判定项，仅信息）/ FAIL %d ，共 %d 项'
                  % (p, w, f, len(self.rows))]
        path.write_text('\n'.join(lines) + '\n', encoding='utf-8')


def band_diff(a, b):
    """两组横带均值之间的最大通道差（0–255）。"""
    if not a or not b:
        return None
    return max(abs(a['bands'][i][c] - b['bands'][i][c])
               for i in range(3) for c in range(3))


def png_size(data):
    if data[:8] != b'\x89PNG\r\n\x1a\n':
        return None
    return (int.from_bytes(data[16:20], 'big'), int.from_bytes(data[20:24], 'big'))


def save_poster(page, img_id, path, rep, view, fallback_fn=None):
    """把预览 <img> 的 data URL 落成原始 PNG，返回 (尺寸, 字节数)。"""
    src = page.evaluate(
        "() => { const i = document.getElementById('%s'); return (i && i.src) || ''; }"
        % img_id)
    if not src.startswith('data:image/png') and fallback_fn:
        src = page.evaluate(fallback_fn)
    if not src.startswith('data:image/png'):
        rep.add(view, 'poster %s' % img_id, False, '预览没有 data URL')
        return None, 0
    data = base64.b64decode(src.split(',', 1)[1])
    path.write_bytes(data)
    rep.add(view, 'poster %s' % img_id, True,
            '%s · %dKB' % (png_size(data), len(data) // 1024))
    return png_size(data), len(data)


def jclick(page, sel):
    """合成点击：绕过粘性顶栏/覆盖层的命中测试（.click() 仍会触发应用监听）。"""
    page.eval_on_selector(sel, 'el => el.click()')


def shot(page, sel, path, rep, view, name=None):
    """元素截图：先确认可见，避免隐藏元素白等 30s。"""
    vis = page.evaluate(
        """(s) => { const el = document.querySelector(s);
             if (!el) return false;
             const r = el.getBoundingClientRect();
             return r.width > 4 && r.height > 4 && el.offsetParent !== null; }""", sel)
    if not vis:
        rep.add(view, 'shot %s' % (name or sel), None, '元素不可见，跳过')
        return False
    try:
        page.locator(sel).screenshot(path=str(path), timeout=8000)
        return True
    except Exception as e:
        rep.add(view, 'shot %s' % (name or sel), None, str(e)[:70])
        return False


def frame_timing(page, n=60):
    return page.evaluate("""(n) => new Promise(res => {
      const out = []; let last = performance.now(); let k = 0;
      function step(){ const now = performance.now(); out.push(now - last); last = now;
        if (++k < n) requestAnimationFrame(step); else res(out); }
      requestAnimationFrame(step);
    })""", n)


# ───────────────────────── 服务端 ─────────────────────────

def start_server(port):
    env = dict(os.environ)
    env['PYTHONPATH'] = str(ROOT)
    proc = subprocess.Popen(
        [sys.executable, '-m', 'uvicorn', 'app.main:app', '--port', str(port)],
        cwd=str(ROOT), env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    url = 'http://127.0.0.1:%d/api/cities' % port
    for _ in range(120):
        if proc.poll() is not None:
            raise SystemExit('uvicorn 提前退出，端口 %d 可能被占用' % port)
        try:
            with urllib.request.urlopen(url, timeout=2) as r:
                if r.status == 200:
                    return proc
        except Exception:
            time.sleep(0.25)
    proc.kill()
    raise SystemExit('服务启动超时：%s' % url)


def new_page(ctx, cfg, errors, res_errors):
    page = ctx.new_page()
    page.add_init_script(JS_HELPERS)
    page.on('pageerror', lambda e: errors.append('pageerror: %s' % str(e)[:200]))
    page.on('console', lambda m: (res_errors if 'Failed to load resource' in m.text
                                  else errors).append('console: %s' % m.text[:160])
            if m.type == 'error' else None)
    page.route('**/api/report*', lambda r: r.abort())
    page.route('**/api/align*', lambda r: r.abort())
    page.set_viewport_size({'width': cfg['w'], 'height': cfg['h']})
    return page


def load(page, url, settle=2200):
    page.goto(url, wait_until='domcontentloaded')
    page.wait_for_function(
        'typeof renderSky === "function" && typeof renderAlignPage === "function"')
    page.wait_for_timeout(settle)
    page.evaluate('(s) => { window.__setSkin(s); }', 'cinema')
    page.wait_for_timeout(500)


def inject_glow(page, cfg):
    bundle = mock_report.build(kind=cfg['kind'], probability=cfg['prob'],
                               aod=0.22, waxing=True)
    bundle.setdefault('log_key', 'm7-mock-key')
    page.evaluate('(r) => { window.__gcCurReport = r; curReport = r; render(); }', bundle)
    page.wait_for_timeout(1500)
    return bundle


def inject_align(page, cfg):
    bundle = mock_report.build_align(kind=cfg['kind'], probability=cfg['prob'],
                                     aod=0.22, waxing=True, target_az=277.0)
    jclick(page, '#modeSeg button[data-v="align"]')
    page.wait_for_timeout(700)
    page.evaluate(
        '(a) => { const c = curCity();'
        ' alignState.data = a.d;'
        ' alignState.key = alignRequestKey(c, document.getElementById("dateSel").value);'
        ' alignState.body = a.body; alignState.period = a.period;'
        ' rebuildModeUI(); renderAlignPage(); }',
        {'d': bundle, 'body': cfg['align_body'], 'period': cfg['align_event']})
    page.wait_for_timeout(1600)
    return bundle


def fulfill_route(page, body):
    page.route('**/api/report*', lambda r: r.fulfill(
        status=200, content_type='application/json', body=json.dumps(body)))


# ⑦ 回归里故意注入的失败响应标记：只有含这个串的诊断日志才被排除在「意外报错」之外
INJECTED_MARK = 'm7-boom'


# ───────────────────────── 每档视图 ─────────────────────────

def run_view(ctx, view, cfg, out_root, url, rep, metrics):
    out = out_root / view
    out.mkdir(parents=True, exist_ok=True)
    errors, res_errors = [], []
    page = new_page(ctx, cfg, errors, res_errors)

    load(page, url)
    bundle = inject_glow(page, cfg)

    # ---- 截图（§8.5 三档）----
    page.screenshot(path=str(out / 'full.png'), full_page=True)
    shot(page, '#heroFrame', out / 'hero-frame.png', rep, view, 'hero-frame')
    shot(page, '#heroCard', out / 'hero-card.png', rep, view, 'hero-card')

    # ---- 性能（§2.1）----
    page.evaluate('() => window.SceneEngine.resetStats()')
    time.sleep(1.0)
    st = page.evaluate('() => window.SceneEngine.stats()')
    rep.add(view, 'perf rAF 单循环', st['frames'] > 20, 'frames=%d/1s' % st['frames'])
    rep.add(view, 'perf 禁每帧 fBm', st['genField'] == 0, 'genField=%d/1s' % st['genField'])
    rep.add(view, 'perf 云场 debounce', st['regen'] <= 1, 'regen=%d/1s' % st['regen'])
    ts = frame_timing(page, 60)
    p50, p95 = statistics.median(ts), sorted(ts)[int(len(ts) * 0.95) - 1]
    metrics['%s.frame_p95' % view] = round(p95, 1)
    rep.add(view, 'perf 帧时 p50/p95', p95 < 50, 'p50=%.1fms p95=%.1fms' % (p50, p95))

    # ---- 移动端/桌面收口（附录B#5）----
    lamps = page.evaluate("() => document.querySelectorAll('#citylights > i').length")
    stars = page.evaluate('() => window.SceneEngine.starCount()')
    metrics['%s.stars' % view] = stars
    ratio = page.evaluate(
        "() => { const r = document.querySelector('.cn-frame').getBoundingClientRect();"
        " return +(r.width / r.height).toFixed(2); }")
    if cfg['mobile']:
        rep.add(view, 'mobile 灯火 6 点', lamps == 6, 'lamps=%d' % lamps)
        rep.add(view, 'mobile 星点 45', stars == 45, 'stars=%d' % stars)
        rep.add(view, 'mobile 帧 16:10', abs(ratio - 1.6) < 0.06, 'ratio=%.2f' % ratio)
    else:
        rep.add(view, 'desktop 灯火 11 点', lamps == 11, 'lamps=%d' % lamps)
        rep.add(view, 'desktop 星点 90', stars == 90, 'stars=%d' % stars)
        rep.add(view, 'desktop 帧 21:9', abs(ratio - 2.33) < 0.06, 'ratio=%.2f' % ratio)

    # ---- 时间码按 24fps 走码（附录B#4，运行时证据）----
    tcs = page.evaluate("""async () => {
        const el = document.getElementById('frameTc'); const out = [];
        for (let i = 0; i < 12; i++) {
          out.push(el ? el.textContent : '');
          await new Promise(r => setTimeout(r, 45));
        }
        return out; }""")
    ffs = [m.group(1) for m in (re.match(r'^\d{2}:\d{2}:\d{2}:(\d{2})', s or '') for s in tcs)
           if m]
    # 阈值收紧：12 次采样间隔 45ms ≈ 540ms，24fps 下应跨过 ~13 帧；若只要求「≥3 个不同值」，
    # 1fps 的走码也能凑出来。故要求 ≥6 个不同帧号，并把全部帧号打进报告（不再截断前 8 个）。
    rep.add(view, 'timecode 24fps 走码',
            len(ffs) == len(tcs) and len(set(ffs)) >= 6
            and all(0 <= int(f) <= 23 for f in ffs),
            '%d 次采样读到 %d 个不同帧号（%s）'
            % (len(tcs), len(set(ffs)), ','.join(sorted(set(ffs)))))
    page.evaluate("() => window.SceneEngine.set({rain: 'drizzle'})")
    page.wait_for_timeout(400)
    drops = page.evaluate('() => window.SceneEngine.rainDrops()')
    metrics['%s.rain_drops' % view] = drops
    rain_on = page.evaluate(
        "() => { const el = document.getElementById('cvRain');"
        " return !!el && getComputedStyle(el).opacity !== '0'; }")
    rep.add(view, 'rain 雨丝生效', drops > 0 and rain_on,
            'drops=%d visible=%s' % (drops, rain_on))
    page.evaluate("() => window.SceneEngine.set({rain: 'none'})")
    page.wait_for_timeout(300)

    # ---- 海报 = 页面帧同场景（§4）----
    jclick(page, '#btnShare')
    page.wait_for_timeout(1000)
    size, _ = save_poster(
        page, 'sharePreview', out / 'poster.png', rep, view,
        fallback_fn="() => buildShareImage(curReport).toDataURL('image/png')")
    rep.add(view, 'poster 1080x1920', size == (1080, 1920), str(size))
    card = page.evaluate("() => window.__m7.cardOf('sharePreview')")
    card_sky = page.evaluate("() => window.__m7.cardSky('sharePreview')")
    card_top = page.evaluate("() => window.__m7.cardSkyTop('sharePreview')")
    page_top = page.evaluate('() => window.__m7.pageSkyTop()')
    live = page.evaluate('() => window.SceneEngine.params()')
    still = page.evaluate('(p) => window.__m7.stillFrame(p)', live)
    d_cs = band_diff(card_sky, still)
    # 天对天：海报卡顶部 62%（纯天空）vs 页面画布顶部 62%（地平线 72% 以上）。
    # 不拿整卡比页面帧 —— 海报卡底部有引擎补的地景带（stillGround 山脊+灯火），
    # 而页面把那一段画在 DOM 层（#Lridges/#citylights），整卡比会白吃一个地景带差。
    d_cp = band_diff(card_top, page_top)
    rep.add(view, 'poster 卡非空', bool(card) and card['std'] > 6,
            'std=%.1f mean=%.1f' % (card['std'], card['mean']) if card else 'null')
    rep.add(view, 'poster=同参数离屏', d_cs is not None and d_cs <= 8,
            '最大带差 %.1f/255' % d_cs if d_cs is not None else 'null')
    rep.add(view, 'poster=页面帧同场景', d_cp is not None and d_cp <= 12,
            '天对天（卡顶62%% vs 画布顶62%%）最大带差 %.1f/255' % d_cp
            if d_cp is not None else 'null')
    kept = page.evaluate("""() => {
        const SE = window.SceneEngine; const a = JSON.stringify(SE.params());
        SE.renderStill({w: 968, h: 400});
        return a === JSON.stringify(SE.params()); }""")
    rep.add(view, 'poster 不扰动实时状态', kept is True, 'params 前后一致=%s' % kept)
    mstat = page.evaluate("() => window.__m7.posterRegion('sharePreview', 140, 1395, 800, 365)")
    msize = page.evaluate("""() => { const c = document.querySelector('#burnMap canvas');
        return c ? [c.width, c.height] : null; }""")
    rep.add(view, 'poster 地图快照非空', bool(mstat) and mstat['std'] > 8,
            '地图区 std=%.1f mean=%.1f（概览 tab 下 #burnMap canvas=%s）'
            % (mstat['std'], mstat['mean'], msize) if mstat else 'null')
    metrics['%s.card_vs_still' % view] = round(d_cs, 1) if d_cs is not None else None
    metrics['%s.card_vs_page' % view] = round(d_cp, 1) if d_cp is not None else None   # 天对天

    # 基线：同一参数在**页面画布尺寸**上离屏重绘（同样只取顶部 62% 天空）vs 页面实景帧，
    # 用来隔离「尺寸/版面」因素，说明跨尺寸天对天的差值来自分辨率而非参数/场景不一致。
    pstill = page.evaluate('(p) => window.__m7.pageStill(p)', live)
    d_pp = band_diff(pstill, page_top) if pstill else None
    metrics['%s.page_vs_pagestill' % view] = round(d_pp, 1) if d_pp is not None else None
    rep.add(view, '（信息）基线 页面帧vs同尺寸离屏', None,
            '同尺寸重绘（顶部 62%% 天空）带差 %.1f/255；跨尺寸天对天 %.1f/255 即尺寸/重采样所致'
            % (d_pp if d_pp is not None else -1, d_cp if d_cp is not None else -1))

    # 降级：离屏引擎不可用时海报必须退回原版式（不抛错、不露白、仍可下载）
    err0 = len(errors)
    page.evaluate("""() => { const SE = window.SceneEngine;
        window.__m7_renderStill = SE.renderStill;
        SE.renderStill = function () { return null; }; }""")
    jclick(page, '#btnShare')
    page.wait_for_timeout(1200)
    fsrc = page.evaluate("() => document.getElementById('sharePreview').src || ''")
    fsize = None
    if fsrc.startswith('data:image/png'):
        try:
            fsize = png_size(base64.b64decode(fsrc.split(',', 1)[1]))
            (out / 'poster-fallback.png').write_bytes(
                base64.b64decode(fsrc.split(',', 1)[1]))
        except Exception:                                    # noqa: BLE001
            fsize = None
    # 判据：降级版式没有场景卡 → 顶部 62% 区域不会等于同参数离屏静帧（有卡时 3.1/255），
    # 该区此时是无卡版式的品牌/城市/日期文字，与静帧差异远大于 15。
    fcard = page.evaluate("() => window.__m7.cardSkyTop('sharePreview')")
    d_fb = band_diff(fcard, still)
    page.evaluate("() => { window.SceneEngine.renderStill = window.__m7_renderStill; }")
    rep.add(view, 'poster 降级退回原版式',
            fsize == (1080, 1920) and d_fb is not None and d_fb > 15
            and len(errors) == err0,
            '尺寸=%s 顶部区和静帧差=%.1f/255（有卡时 3.1）新增页面错误=%d；存 poster-fallback.png'
            % (fsize, d_fb if d_fb is not None else -1, len(errors) - err0))

    # ---- 悬日月：窗口 → 场景（含满月盘）----
    inject_align(page, cfg)
    page.screenshot(path=str(out / 'align-full.png'), full_page=True)
    shot(page, '#alignCard', out / 'align-card.png', rep, view, 'align-card')
    shot(page, '#celestialCanvas', out / 'align-viewfinder.png', rep, view, 'align-vf')
    jclick(page, '#btnAlignShare')
    page.wait_for_timeout(1200)
    size2, _ = save_poster(
        page, 'alignSharePreview', out / 'align-poster.png', rep, view,
        fallback_fn=("() => { const w = (typeof alignWindowList === 'function'"
                     " && alignWindowList()[0]) || null;"
                     " return w ? buildAlignShareImage(w, alignState.data)"
                     ".toDataURL('image/png') : ''; }"))
    rep.add(view, 'align poster 1080x1920', size2 == (1080, 1920), str(size2))
    card2 = page.evaluate("() => window.__m7.cardOf('alignSharePreview')")
    rep.add(view, 'align poster 卡非空', bool(card2) and card2['std'] > 6,
            'std=%.1f' % card2['std'] if card2 else 'null')
    acard = page.evaluate("() => window.__m7.cardSky('alignSharePreview')")
    app_ = page.evaluate('() => window.__m7.alignPosterParams()')
    astill = page.evaluate('(p) => window.__m7.stillFrame(p)', app_)
    d_as = band_diff(acard, astill)
    src = (app_ or {}).get('__from', '?')
    rep.add(view, 'align poster=本窗口离屏', d_as is not None and d_as <= 8,
            '窗口=%s 最大带差 %s' % (src, ('%.1f/255' % d_as) if d_as is not None else 'null'))
    metrics['%s.align_card_vs_still' % view] = round(d_as, 1) if d_as is not None else None
    livep = page.evaluate('() => window.SceneEngine.params()')
    diff = [k for k in ('genus', 'cover', 'lit', 'alt', 'moon')
            if (app_ or {}).get(k) != livep.get(k)]
    rep.add(view, '（信息）align poster 参数说明', None,
            '海报窗口 %s：%s；页面实时：%s；差异字段 %s'
            % (src, {k: (app_ or {}).get(k) for k in ('genus', 'lit', 'alt', 'moon')},
               {k: livep.get(k) for k in ('genus', 'lit', 'alt', 'moon')}, diff or '无'))
    if cfg['align_body'] == 'moon':
        md = page.evaluate('(p) => window.__m7.moonDelta(p)', app_)
        rep.add(view, 'align 悬月满月盘', bool(md) and md['count'] >= 250
                and md['firstX'] > 968 * 0.4,
                ('盘贡献像素=%d 最大差=%.0f 首点=(%d,%d)'
                 % (md['count'], md['maxDelta'], md['firstX'], md['firstY'])) if md else 'null')
        metrics['%s.moon_pixels' % view] = md['count'] if md else None

    # ---- 回归（§7 回归表）----
    if view == '1366':
        regression(page, cfg, bundle, out, rep, view, errors)

    # ⑦ 故意注入的 500 响应体会出现在应用自带的诊断日志里
    #（app.js:3104 `console.error('[loadReport] fallback to cache:' ...)` —— 任何非取消失败
    #  都会先打这行再决定是否回退缓存）。这是注入失败的预期副作用，不算「意外报错」；
    #  其余任何 console.error / pageerror 仍然计入。按自己注入的标记串排除，避免放宽口径。
    injected = [e for e in errors if INJECTED_MARK in e]
    real = [e for e in errors if INJECTED_MARK not in e]
    rep.add(view, '页面无 JS 报错', not real,
            (('已排除 %d 条注入失败诊断；' % len(injected)) if injected else '')
            + (('; '.join(real[:2]) or '无')[:120]))
    metrics['%s.errors' % view] = len(real)
    metrics['%s.resource_errors' % view] = len(res_errors)
    page.close()


def regression(page, cfg, bundle, out, rep, view, errors):
    # 前置：悬日月一段把页面留在 align 模式，而 #btnGo → reloadCurrent() 在 align 模式
    # 会去打 /api/align（脚本只放行 /api/report）→ 先切回 glow。
    jclick(page, '#modeSeg button[data-v="glow"]')
    page.wait_for_timeout(1000)
    mode = page.evaluate('() => appMode')
    rep.add(view, '回归前置 回到 glow 模式', mode == 'glow', 'appMode=%s' % mode)

    # ① 五 tab（A单列：导航=滚动到锚点 + 高亮，cinema 下走 film burn 转场，等足）
    tabs_bad = []
    for t in TAB_IDS:
        jclick(page, '#tabBtn-%s' % t)
        page.wait_for_timeout(900)
        vis = page.evaluate(
            """(t) => { const el = document.getElementById('sec-' + t);
                 const btn = document.getElementById('tabBtn-' + t);
                 return !!el && !!btn && btn.classList.contains('active')
                   && getComputedStyle(el).display !== 'none'; }""", t)
        if not vis:
            tabs_bad.append(t)
        if t == 'data':
            page.evaluate("() => window.scrollTo(0, 0)")
            page.wait_for_timeout(300)
            shot(page, '#report-flow', out / 'tab-data.png', rep, view, 'tab-data')
    rep.add(view, '回归① 五 tab 可切', not tabs_bad,
            'bad=%s' % (tabs_bad or '无'))
    jclick(page, '#tabBtn-overview')
    page.wait_for_timeout(900)

    # ② 四 skin × 明暗 互切（ember 无 data-skin；dark 才有 data-theme）
    skin_bad = []
    for sk in SKINS:
        for md in ['dark', 'light']:
            page.evaluate('(a) => { window.__setSkin(a[0]); window.__setThemeMode(a[1]); }',
                          [sk, md])
            page.wait_for_timeout(260)
            got = page.evaluate(
                """() => ({skin: document.documentElement.getAttribute('data-skin') || '',
                           theme: document.documentElement.getAttribute('data-theme') || '',
                           bg: getComputedStyle(document.body).backgroundColor})""")
            want_skin = '' if sk == 'ember' else sk
            want_theme = 'dark' if md == 'dark' else ''
            if got['skin'] != want_skin or got['theme'] != want_theme:
                skin_bad.append('%s/%s→%s|%s' % (sk, md, got['skin'], got['theme']))
    rep.add(view, '回归② 四 skin×明暗 10 组', not skin_bad,
            'bad=%s' % (skin_bad[:3] or '无'))
    page.evaluate('(a) => { window.__setSkin(a[0]); window.__setThemeMode(a[1]); }',
                  ['cinema', 'dark'])
    page.wait_for_timeout(400)
    fr = page.evaluate('() => window.__m7.pageSky()')
    rep.add(view, '回归② 互切后画面仍在', bool(fr) and fr['std'] > 4,
            'std=%.1f' % fr['std'] if fr else 'null')

    # ③ 城市切换（同城市不同 seed → 云形随城市变）+ 排行/对比入口
    page.unroute('**/api/report*')
    fulfill_route(page, bundle)
    p0 = page.evaluate('() => window.SceneEngine.params()')
    opts = page.evaluate(
        "() => Array.from(document.getElementById('citySel').options)"
        ".map(o => ({v: o.value, t: o.textContent.trim()}))")
    if len(opts) > 1:
        page.select_option('#citySel', label=opts[1]['t'])
        page.wait_for_timeout(300)
        jclick(page, '#btnGo')
        page.wait_for_timeout(1600)
        p1 = page.evaluate('() => window.SceneEngine.params()')
        rep.add(view, '回归③ 城市切换', p0['seed'] != p1['seed'],
                'seed %s→%s（%d 城可选）' % (p0['seed'], p1['seed'], len(opts)))
    else:
        rep.add(view, '回归③ 城市切换', None, '城市列表为空，跳过')
    entry = page.evaluate("() => !!document.getElementById('btnCompare')")
    rep.add(view, '回归③ 排行/对比入口', entry is True, 'btnCompare=%s' % entry)

    # ④ 实况回填（POST /api/feedback 本地放行）
    page.route('**/api/feedback*', lambda r: r.fulfill(
        status=200, content_type='application/json', body='{"ok":true}'))
    err0 = len(errors)
    jclick(page, '#btnFeedback')        # 未选实况：应给提示，而不是抛未捕获 TypeError
    page.wait_for_timeout(500)
    hint = page.evaluate("() => (document.getElementById('fbText') || {}).textContent || ''")
    rep.add(view, '回归④ 未选实况给提示', ('先选' in hint) and len(errors) == err0,
            'fbText=%s 新报错=%d' % (hint[:24] or '(空)', len(errors) - err0))
    page.eval_on_selector('#fbSeg button', 'el => el.click()')   # 选第一条实况
    page.wait_for_timeout(300)
    jclick(page, '#btnFeedback')
    page.wait_for_timeout(900)
    fb = page.evaluate("() => (document.getElementById('fbText') || {}).textContent || ''")
    rep.add(view, '回归④ 实况回填', '已记录' in fb or '谢谢' in fb, 'fbText=%s' % fb[:40])

    # ⑤ 分享图连出两次
    ok2 = True
    for _ in range(2):
        jclick(page, '#btnShare')
        page.wait_for_timeout(800)
        s = page.evaluate("() => document.getElementById('sharePreview').src || ''")
        ok2 = ok2 and s.startswith('data:image/png')
    rep.add(view, '回归⑤ 分享图连出两次', ok2, '两次均可生成')

    # ⑤b 风险卡空态/有态（M7 抛光：无条目不得摆空壳卡；报告页底部实测命中过）
    page.evaluate("""() => { const r = Object.assign({}, curReport, {risks: []});
        window.__gcCurReport = r; curReport = r; render(); }""")
    page.wait_for_timeout(500)
    rk_hidden = page.evaluate(
        "() => getComputedStyle(document.getElementById('riskCard')).display === 'none'")
    page.evaluate("""() => { const r = Object.assign({}, curReport,
        {risks: ['低云(0.8km)交点 114km 处云量 82%，会挡住天体']});
        window.__gcCurReport = r; curReport = r; render(); }""")
    page.wait_for_timeout(500)
    rk_vis = page.evaluate("""() => { const c = document.getElementById('riskCard');
        return getComputedStyle(c).display !== 'none'
            ? document.querySelectorAll('#riskList li').length : 0; }""")
    rep.add(view, '回归⑤b 风险卡空/有态', bool(rk_hidden) and rk_vis == 1,
            'risks=[] → 隐藏=%s；risks 1 条 → 可见条目=%s' % (rk_hidden, rk_vis))

    # ⑥ 连切 10 次（DOM 不膨胀 + 主循环仍活）
    nodes0 = page.evaluate("() => document.getElementsByTagName('*').length")
    for i, prob in enumerate(range(20, 90, 7)):
        rb = mock_report.build(kind='sunset' if i % 2 == 0 else 'sunrise',
                               probability=prob, aod=0.2 + i * 0.01, waxing=True)
        page.evaluate('(r) => { window.__gcCurReport = r; curReport = r; render(); }', rb)
        page.wait_for_timeout(140)
    page.wait_for_timeout(700)
    nodes1 = page.evaluate("() => document.getElementsByTagName('*').length")
    frames = page.evaluate('() => window.SceneEngine.stats().frames')
    rep.add(view, '回归⑥ 连切 10 次', nodes1 - nodes0 <= 40 and frames > 0,
            'DOM %d→%d（+%d）frames=%d' % (nodes0, nodes1, nodes1 - nodes0, frames))

    # ⑦ 骨架 / 取消 / 重试（换日期避开本地缓存，请求挂起 → 骨架）
    page.unroute('**/api/report*')
    err_i = len(errors)          # ⑦ 之前的报错基线：本段只认新增
    held = []
    page.route('**/api/report*', lambda r: held.append(r))
    page.evaluate("""() => { const d = document.getElementById('dateSel');
        d.value = '2026-09-09';
        d.dispatchEvent(new Event('change', {bubbles: true})); }""")
    page.wait_for_timeout(250)
    jclick(page, '#btnGo')
    page.wait_for_timeout(900)
    skel = page.evaluate(
        """() => { const el = document.getElementById('loading');
             return !!el && getComputedStyle(el).display !== 'none'; }""")
    page.screenshot(path=str(out / 'loading-skeleton.png'), full_page=True)
    # 判据必须能 FAIL：骨架屏没出现或根本没发出请求 → 红（原先写 None 只记 WARN，
    # 功能坏掉也永远停在「FAIL: 0」，等于这一条没有证据力）。
    rep.add(view, '回归⑦ 骨架屏出现', bool(skel) and len(held) >= 1,
            'loading 可见=%s（挂起请求 %d）' % (skel, len(held)))
    before_cancel = page.evaluate('() => (curReport && curReport.probability) || null')
    jclick(page, '#btnLoadCancel')
    page.wait_for_timeout(900)
    hidden, after_cancel, err_shown, err_title = page.evaluate("""() => {
        const el = document.getElementById('loading');
        const eb = document.getElementById('errCard');
        const et = document.getElementById('errTitle');
        return [!el || getComputedStyle(el).display === 'none',
                (curReport && curReport.probability) || null,
                !!eb && getComputedStyle(eb).display !== 'none',
                et ? et.textContent.trim() : '']; }""")
    # 取消不是静默返回：app.js:3094-3098 的 AbortError 分支弹「查询已取消」提示卡。
    # 这里断言的是「提示文案对 + 报告没被替换」，而不是「不出现提示卡」。
    rep.add(view, '回归⑦ 取消返回',
            hidden is True and after_cancel == before_cancel
            and err_shown is True and err_title == '查询已取消',
            'loading 隐藏=%s；报告未被替换=%s（%s→%s）；取消提示=%s「%s」'
            % (hidden, after_cancel == before_cancel, before_cancel, after_cancel,
               err_shown, err_title))
    page.unroute('**/api/report*')
    # 真正要走到错误卡，必须避开缓存回退：非取消失败时 app.js:3099-3134 会先找
    # localStorage 的 gc_last_<city>_* 报告（同日期优先、否则取最近一份）并直接渲染，
    # 有缓存就永远不显示错误卡。故先清掉报告缓存，让 500 真正落到 showError()。
    cleared = page.evaluate("""() => {
        const ks = [];
        for (let i = 0; i < localStorage.length; i++) {
          const k = localStorage.key(i);
          if (k && k.indexOf('gc_last_') === 0) ks.push(k);
        }
        ks.forEach(k => localStorage.removeItem(k));
        return ks.length; }""")
    page.route('**/api/report*', lambda r: r.fulfill(
        status=500, content_type='application/json', body='{"detail":"m7-boom"}'))
    jclick(page, '#btnGo')
    page.wait_for_timeout(1500)
    err_title2 = page.evaluate("""() => { const eb = document.getElementById('errCard');
        const et = document.getElementById('errTitle');
        return (!!eb && getComputedStyle(eb).display !== 'none' && et)
               ? et.textContent.trim() : ''; }""")
    # 换一份数据：证明重试真的走网络并重渲染（不是拿旧卡片糊过去）
    retry_bundle = mock_report.build(kind=cfg['kind'], probability=63, aod=0.31,
                                     waxing=True)
    retry_bundle.setdefault('log_key', 'm7-mock-retry')
    page.unroute('**/api/report*')
    fulfill_route(page, retry_bundle)
    jclick(page, '#btnErrRetry')
    page.wait_for_timeout(2400)
    back, now_prob, err_after = page.evaluate("""() => {
        const el = document.getElementById('heroCard');
        const eb = document.getElementById('errCard');
        return [!!el && el.offsetParent !== null,
                (curReport && +curReport.probability) || null,
                !!eb && getComputedStyle(eb).display !== 'none']; }""")
    shot(page, '#heroCard', out / 'after-retry.png', rep, view, 'after-retry')
    # 成功渲染须撤下错误态（app.js:3234 hideError），故错误卡此时应为隐藏。
    rep.add(view, '回归⑦ 重试恢复',
            err_title2 == '数据获取失败' and back is True and now_prob == 63
            and err_after is False,
            '错误卡「%s」（已清 %d 条缓存避开回退）；RETAKE 后 heroCard 可见=%s；'
            '新报告概率=%s（重试返回 63）；错误卡已撤=%s；本段新增报错=%s'
            % (err_title2, cleared, back, now_prob, err_after is False,
               (errors[err_i:] or ['无'])))


# ───────────────────────── reduced-motion 降级 ─────────────────────────

def run_motion(ctx, out_root, url, rep, metrics):
    out = out_root / 'reduced-motion'
    out.mkdir(parents=True, exist_ok=True)
    errors, res_errors = [], []
    cfg = dict(VIEWS['1366'])
    page = new_page(ctx, cfg, errors, res_errors)
    page.emulate_media(reduced_motion='reduce')
    load(page, url)
    inject_glow(page, cfg)
    page.evaluate('() => window.SceneEngine.resetStats()')
    time.sleep(0.9)
    st = page.evaluate('() => window.SceneEngine.stats()')
    rep.add('reduce', '静态无 rAF 循环', st['frames'] == 0, 'frames=%d/0.9s' % st['frames'])
    fr = page.evaluate('() => window.__m7.pageSky()')
    rep.add('reduce', '静态画面仍出图', bool(fr) and fr['std'] > 4,
            'std=%.1f mean=%.1f' % (fr['std'], fr['mean']) if fr else 'null')
    page.screenshot(path=str(out / 'full.png'), full_page=True)
    shot(page, '#heroFrame', out / 'hero-frame.png', rep, 'reduce', 'hero-frame')
    jclick(page, '#btnShare')
    page.wait_for_timeout(900)
    size, _ = save_poster(page, 'sharePreview', out / 'poster.png', rep, 'reduce',
                          fallback_fn="() => buildShareImage(curReport).toDataURL('image/png')")
    card = page.evaluate("() => window.__m7.cardOf('sharePreview')")
    rep.add('reduce', '降级下海报可用',
            size == (1080, 1920) and bool(card) and card['std'] > 6,
            'size=%s std=%.1f' % (size, card['std'] if card else -1))
    metrics['reduce.frames'] = st['frames']
    rep.add('reduce', '页面无 JS 报错', not errors, ('; '.join(errors[:2]) or '无')[:120])
    metrics['reduce.errors'] = len(errors)
    page.close()


# ───────────────────────── main ─────────────────────────

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--port', type=int, default=8124)
    ap.add_argument('--out', default=str(ROOT / 'docs' / 'shots' / 'v2.0-M7'))
    ap.add_argument('--only', default='', help='只跑一档：1366 / 1920 / 390')
    ap.add_argument('--no-motion', action='store_true')
    args = ap.parse_args()

    out_root = Path(args.out)
    out_root.mkdir(parents=True, exist_ok=True)
    rep = Rep()
    metrics = {}
    url = 'http://127.0.0.1:%d/' % args.port
    srv = start_server(args.port)
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(channel='msedge', headless=True)
            ctx = browser.new_context(device_scale_factor=2, locale='zh-CN')
            views = [args.only] if args.only else list(VIEWS)
            for view in views:
                cfg = VIEWS[view]
                print('── %s (%dx%d) ──' % (view, cfg['w'], cfg['h']))
                run_view(ctx, view, cfg, out_root, url, rep, metrics)
            if not args.only and not args.no_motion:
                print('── reduced-motion ──')
                run_motion(ctx, out_root, url, rep, metrics)
            browser.close()
    finally:
        srv.terminate()
        try:
            srv.wait(timeout=10)
        except Exception:
            srv.kill()

    # 跨档比对：移动端雨丝 = 桌面 ×.6（附录B#5）
    # 只有两档都跑过才算这一条；跑单档（--only）时 390 数据本就不存在，不产生假红。
    # 两档都跑时判据必须能 FAIL：原先 `if d390 and d1366:` 在数据为 0/缺失时整行
    # 静默消失，坏掉既不 PASS 也不 FAIL（等于没有这一条）。
    if '1366' in views and '390' in views:
        d390, d1366 = metrics.get('390.rain_drops'), metrics.get('1366.rain_drops')
        rep.add('跨档', 'mobile 雨丝 ×.6',
                bool(d390) and bool(d1366) and d390 == round(d1366 * 0.6),
                '390=%s vs 1366=%s（×.6=%.1f）'
                % (d390, d1366, (d1366 or 0) * 0.6))

    rep.dump(out_root / 'report.txt')
    (out_root / 'metrics.json').write_text(
        json.dumps(metrics, ensure_ascii=False, indent=2), encoding='utf-8')
    # 跑单档时另存一份带档位后缀的副本：三档分开跑（省内存/省时）时，
    # report.txt 只保留最后一次的，各档证据要靠 report-<档>.txt 分别留底。
    if args.only:
        rep.dump(out_root / ('report-%s.txt' % args.only))
        (out_root / ('metrics-%s.json' % args.only)).write_text(
            json.dumps(metrics, ensure_ascii=False, indent=2), encoding='utf-8')
    print('\n数字：%s' % json.dumps(metrics, ensure_ascii=False))
    print('报告：%s' % (out_root / 'report.txt'))
    return 1 if rep.fails() else 0


if __name__ == '__main__':
    raise SystemExit(main())

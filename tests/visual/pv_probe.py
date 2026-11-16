# -*- coding: utf-8 -*-
"""天体预览（sky_preview.js）状态探针：一次跑完多种场景，出图 + 出数。

用途：改渲染器后快速回归——
  - 每张图看"像不像照片"（通透/霾/暮色/夜色/地形遮挡/框外）
  - 每组数看"准不准"（px/° 是否等比、取景框是否 = 视场、云带是否落在天体仰角、
    可见度是否与后端 vision/trans 同口径）

用法：
    python tests/visual/pv_probe.py            # 全部场景
    python tests/visual/pv_probe.py --only clear,sight
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
OUT = os.path.join(ROOT, 'tests', 'visual', 'out', 'pv')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def wait_server(port, timeout=40):
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


def bands(low, mid, high, h=(1536, 3200, 9756), d=(92, 190, 354)):
    """按视线口径合成 sight（occ 为该层沿视线云量）"""
    out = {}
    for b, o, hh, dd in (('low', low, h[0], d[0]), ('mid', mid, h[1], d[1]),
                         ('high', high, h[2], d[2])):
        if o is None:
            continue
        out[b] = {'occ': o, 'gh_m': hh, 'd_km': dd, 'level': 850,
                  'env_km': [100, 300]}
    trans = 1.0
    for v in out.values():
        trans *= (1 - 0.9 * v['occ'] / 100.0)
    return {'visible': True, 'trans': round(trans, 3),
            'vis_prob': round(100 * trans), 'bands': out}


def sight_blocked():
    s = bands(78, 60, 55)
    s['levels'] = []
    return s


CASES = [
    # name, canvas, 参数
    ('clear_lowsun', 'celestialCanvas',
     dict(body='sun', type='悬日', period_name='日落', best_time='17:52',
          best_alt=2.4, best_az=269.7, aod=0.12, cloud=(4, 12, 20),
          sight=bands(4, 12, 20), ridge=1.2)),
    ('veiled_lowsun', 'celestialCanvas',
     dict(body='sun', type='悬日', period_name='日落', best_time='17:52',
          best_alt=2.4, best_az=269.7, aod=0.28, cloud=(30, 25, 40),
          sight=bands(30, 25, 40), ridge=1.2)),
    ('blocked_lowsun', 'celestialCanvas',
     dict(body='sun', type='悬日', period_name='日落', best_time='17:52',
          best_alt=2.4, best_az=269.7, aod=0.45, cloud=(78, 60, 55),
          sight=sight_blocked(), ridge=1.2)),
    ('highsun_noon', 'celestialCanvas',
     dict(body='sun', type='悬日', period_name='日落', best_time='12:00',
          best_alt=62.0, best_az=180.0, aod=0.18, cloud=(10, 20, 30),
          sight=bands(10, 20, 30), ridge=1.2)),
    ('ridge_block', 'celestialCanvas',
     dict(body='sun', type='悬日', period_name='日落', best_time='17:45',
          best_alt=1.5, best_az=270.0, aod=0.2, cloud=(10, 15, 25),
          sight=bands(10, 15, 25), ridge=3.4)),
    ('moon_night', 'celestialCanvas',
     dict(body='moon', type='悬月', period_name='月落', best_time='23:40',
          best_alt=34.0, best_az=95.0, aod=0.15, cloud=(5, 10, 30),
          sight=bands(5, 10, 30), illum=38, sun_alt=-22, moon_ang=0.516,
          ridge=1.2)),
    ('moon_dusk', 'celestialCanvas',
     dict(body='moon', type='悬月', period_name='月出', best_time='18:20',
          best_alt=3.0, best_az=102.0, aod=0.22, cloud=(12, 30, 45),
          sight=bands(12, 30, 45), illum=94, sun_alt=-2.5, moon_ang=0.53,
          ridge=1.2)),
    ('wide_fov', 'celestialCanvas',
     dict(body='sun', type='悬日', period_name='日落', best_time='17:52',
          best_alt=2.4, best_az=269.7, aod=0.2, cloud=(15, 25, 35),
          sight=bands(15, 25, 35), ridge=1.2, aov=24)),
    ('tele_fov', 'celestialCanvas',
     dict(body='sun', type='悬日', period_name='日落', best_time='17:52',
          best_alt=2.4, best_az=269.7, aod=0.2, cloud=(0, 0, 0),
          sight=None, ridge=1.2, aov=1.5)),
    ('dir_card_offframe', 'alignDirSky',
     dict(body='sun', type='悬日', period_name='太阳', best_time='18:30',
          best_alt=-0.83, best_az=273.9, frame_az=270.0, frame_alt=0.3,
          aod=0.2, cloud=(20, 30, 40), sight=bands(20, 30, 40), ridge=1.2,
          aov=3.0)),
]


def build_payload(r, case):
    """把 mock 报告改成该场景（几何/云/山脊/视场）"""
    s = case['sight']
    r['alignment_meta']['aov'] = case.get('aov', 3.0)
    r['geometry']['aov'] = case.get('aov', 3.0)
    r['geometry']['elev'] = case.get('frame_alt', 0.3)
    r['main']['aod_local'] = case['aod']
    r['observer'] = {'height_m': 1201, 'height_src': 'terrain',
                     'terrain_elevation_m': 1212, 'horizon_dip_deg': 1.112}
    mask = [case['ridge'], case['ridge'], case['ridge'], case['ridge'],
            case['ridge'], case['ridge'], case['ridge'], case['ridge'],
            5.2, 3.8, 2.6, 1.8] + [case['ridge']] * 4
    r['site_info']['horizon_mask'] = mask
    r['tracks'] = {'time': ['%02d:%02d' % (i // 2, (i % 2) * 30) for i in range(49)],
                   'sun_alt': [], 'sun_az': [], 'moon_alt': [], 'moon_az': []}
    sa = case.get('sun_alt')
    for i in range(49):
        m = i * 30
        if case['body'] == 'sun':
            alt = case['best_alt'] + (m - 1072) * -0.0023
            az = case['best_az'] + (m - 1072) * 0.0016
        else:
            alt = case['best_alt'] + (m - 1420) * -0.0025
            az = case['best_az'] + (m - 1420) * 0.0022
        r['tracks']['sun_alt'].append(round(alt if sa is None else sa, 2))
        # 太阳与月亮不要同方位：真实天象里两者的方位差决定了月相亮面朝向
        # （同方位会让亮面"朝下"这种事看着像 bug）
        sun_az = az if case['body'] == 'sun' else az - 45
        r['tracks']['sun_az'].append(round(sun_az % 360, 2))
        r['tracks']['moon_alt'].append(round(alt, 2))
        r['tracks']['moon_az'].append(round(az % 360, 2))
    w = r['alignment'][1]                      # 日落窗口
    w.update({'body': case['body'], 'type': case['type'],
              'period_name': case['period_name'], 'best_time': case['best_time'],
              'best_az': case['best_az'], 'best_alt': case['best_alt'],
              'date': r['meta']['date']})
    for k in ('frame_az', 'frame_alt', 'sight', 'vis_prob'):
        if k in case:
            w[k] = case[k]
    ca = case.get('cloud') or (0, 0, 0)
    if s is not None:
        w['sight'] = s
        w['vis_prob'] = s['vis_prob']
    else:
        w.pop('sight', None)
        w['vis_prob'] = round(max(0.0, 100.0 - (ca[0] + 0.45 * ca[1]
                                                + 0.2 * ca[2]) * 1.05))
    w['cloud_at'] = {'low': ca[0], 'mid': ca[1], 'high': ca[2]}
    w['cloud_locked'] = True
    if case['body'] == 'moon':
        w['illum'] = case.get('illum', 90)
        w['angular_size_deg'] = case.get('moon_ang', 0.52)
    r['astronomy']['moon_waxing'] = True
    r['astronomy']['moon_illum'] = case.get('illum', 90)
    return r


def pft_case(path, kind='sunrise'):
    """用巧摄 .pft 的真实几何建一个场景（机位/方位/视场/目标高度与距离）。"""
    import json as _json
    with open(path, encoding='utf-8') as fh:
        pft = _json.load(fh)
    sys.path.insert(0, ROOT)
    from app.services import pft as pft_svc
    g = pft_svc.extract_geometry(pft)
    gm, tl, eph = pft.get('map', {}), pft.get('tools', {}), pft.get('ephemeris', {})
    import math
    lat1, lon1 = tl.get('lat1'), tl.get('lng1')
    lat2, lon2 = tl.get('lat2'), tl.get('lng2')
    dist = None
    if None not in (lat1, lon1, lat2, lon2):
        dlat = (lat2 - lat1) * 111.32
        dlon = (lon2 - lon1) * 111.32 * math.cos(math.radians(lat1))
        dist = round((dlat ** 2 + dlon ** 2) ** 0.5, 2)
    return dict(lat=g['lat'], lon=g['lon'], az=g['bearing'], aov=g['aov'],
                elev=g['elevation'], target_height=eph.get('subjectHeight'),
                dist_km=dist, kind=kind, camera=(lat1, lon1))


def fetch_real_align(case, port=8131, date=None):
    """直接问后端要一份真实 /api/align（含真实天文与预报）。"""
    from datetime import date as _date
    d = date or _date.today().isoformat()
    q = (f"lat={case['lat']}&lon={case['lon']}&date={d}"
         f"&azimuth={case['az']}&aov={case['aov']}")
    if case.get('elev') is not None:
        q += f"&elev={case['elev']}"
    if case.get('target_height') is not None and case.get('dist_km'):
        q += (f"&target_height={case['target_height']}"
              f"&target_dist_km={case['dist_km']}")
    return requests.get(f'http://127.0.0.1:{port}/api/align?{q}',
                        timeout=180).json()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--port', type=int, default=8131)
    ap.add_argument('--only', default=None)
    ap.add_argument('--dark', action='store_true')
    ap.add_argument('--keep', action='store_true')
    ap.add_argument('--pft', default=None, help='用巧摄 .pft 的真实几何出图')
    args = ap.parse_args()
    os.makedirs(OUT, exist_ok=True)
    names = set((args.only or '').split(',')) - {''}
    cases = [c for c in CASES if not names or c[0] in names]

    env = dict(os.environ)
    env['PYTHONPATH'] = ROOT
    server = subprocess.Popen(
        [sys.executable, '-m', 'uvicorn', 'app.main:app', '--host', '127.0.0.1',
         '--port', str(args.port)],
        cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
    try:
        if not wait_server(args.port):
            raise SystemExit('server 未就绪')
        import mock_report
        base = mock_report.build_align(kind='sunset', probability=58, aod=0.22)
        with sync_playwright() as p:
            browser = p.chromium.launch(channel='msedge', headless=True)
            page = browser.new_page(viewport={'width': 1280, 'height': 1000},
                                    device_scale_factor=2)
            page.route('**/api/report*', lambda route: route.abort())
            page.route('**/api/align*', lambda route: route.abort())
            page.on('pageerror', lambda e: print('PAGEERROR', e))
            page.goto(f'http://127.0.0.1:{args.port}/',
                      wait_until='domcontentloaded')
            page.wait_for_function('typeof renderCelestialPreview === "function"',
                                   timeout=30000)
            page.wait_for_timeout(1500)
            page.evaluate('(a) => { window.__setSkin("ember");'
                          ' window.__setThemeMode(a); }',
                          'dark' if args.dark else 'light')
            page.wait_for_timeout(200)
            summary = []
            if args.pft:
                pcase = pft_case(args.pft)
                real = fetch_real_align(pcase, args.port)
                wins = real.get('alignment') or []
                if not wins:
                    print('该 .pft 今日无对准窗口：', [
                        {'date': d.get('date'), 'weekday': d.get('weekday'),
                         'windows': list((d.get('windows') or {}).keys())}
                        for d in (real.get('week') or [])][:3])
                    browser.close()
                    return
                cw = wins[0]
                page.evaluate("""async ([payload, canvas]) => {
                  if (typeof switchMode === 'function') switchMode('align', true);
                  if (typeof alignState === 'object') alignState.data = payload;
                  document.getElementById('alignCard').style.display = 'block';
                  document.getElementById('celestialWrap').style.display = 'block';
                  document.getElementById(canvas).style.display = 'block';
                  await new Promise(r => requestAnimationFrame(
                    () => requestAnimationFrame(r)));
                  _pvCtx = {w: payload.alignment[0], r: payload};
                  _pvCanvasId = canvas;
                  renderPlanitPreview(0);
                }""", [real, 'celestialCanvas'])
                page.wait_for_timeout(150)
                out = page.evaluate('() => _pvLast')
                page.locator('#celestialCanvas').screenshot(
                    path=os.path.join(OUT, 'pft_' + os.path.basename(
                        args.pft).replace('.pft', '') + '.png'))
                print()
                print('=== .pft 实测 ===')
                print(f"机位 {pcase['lat']}, {pcase['lon']} · 目标方位 "
                      f"{pcase['az']}° · 视场 {round(pcase['aov'],3)}° · "
                      f"目标仰角 {pcase['elev']}° · 目标高 "
                      f"{pcase['target_height']}m / 距离 {pcase['dist_km']}km")
                print(f"窗口：{cw.get('type')}{cw.get('period_name')} "
                      f"{cw.get('date')} {cw.get('best_time')} · 高度 "
                      f"{cw.get('best_alt')}° · 方位 {cw.get('best_az')}° · "
                      f"可见度 {cw.get('vis_prob')}%")
                print(f"渲染：等效 {out['focalMm']}mm · 视场 "
                      f"{round(out['fovH'],2)}°×{round(out['fovV'],2)}° · "
                      f"日面占宽 {round(out['bodyPct'],1)}% · px/° "
                      f"{round(out['pxPerDegX'],1)} · 在画面内 {out['inFrame']}")
                for c in out['clouds']:
                    print(f"    {c['band']:>4} occ={c['occ']}% "
                          f"d={None if c['dKm'] is None else round(c['dKm'])}km "
                          f"thick={round(c['thick'],2)}° src={c['src']}")
                browser.close()
                return
            for name, canvas, case in cases:
                payload = build_payload(json.loads(json.dumps(base)), case)
                out = page.evaluate("""async ([payload, canvas, name]) => {
                  // 先切到悬日月模式（#alignView 可见），等一帧让 canvas 拿到真实
                  // 显示尺寸（渲染器按显示尺寸 × DPR 出图，隐藏时只能按设计尺寸直出）
                  if (typeof switchMode === 'function') switchMode('align', true);
                  if (typeof alignState === 'object') {
                    alignState.data = payload;
                    alignState.body = payload.alignment[1].body || 'sun';
                    alignState.period = 'set';
                  }
                  document.getElementById('alignCard').style.display = 'block';
                  document.getElementById('alignDirCard').style.display = 'block';
                  document.getElementById('celestialWrap').style.display = 'block';
                  document.getElementById(canvas).style.display = 'block';
                  await new Promise(r => requestAnimationFrame(
                    () => requestAnimationFrame(r)));
                  _pvCtx = {w: payload.alignment[1], r: payload};
                  _pvCanvasId = canvas;
                  renderPlanitPreview(0);
                  return _pvLast;
                }""", [payload, canvas, name])
                page.wait_for_timeout(120)
                path = os.path.join(OUT, ('dark_' if args.dark else '') + name
                                    + '.png')
                page.locator('#' + canvas).screenshot(path=path)
                summary.append((name, out))
            print('\n=== 渲染摘要（px/° 应横竖相等；取景框 ≤ 画布；云带厚/透明度取视线口径） ===')
            for name, o in summary:
                if not o:
                    print(name, 'NO DATA')
                    continue
                print(f"[{name}] fovH={o['fovH']} fovV={round(o['fovV'],2)} "
                      f"pxX={round(o['pxPerDegX'],2)} pxY={round(o['pxPerDegY'],2)} "
                      f"frame={[round(v) for v in o['frame']]} "
                      f"alt={round(o['alt'],2)} az={round(o['az'],2)} "
                      f"vis={o['vis']} inFrame={o['inFrame']} "
                      f"hidden={o['bodyHidden']} exit={o['exitMin']} "
                      f"sunAlt={o['sunAlt']}")
                for c in o['clouds']:
                    print(f"    {c['band']:>4} occ={c['occ']}% d={c['dKm']}km "
                          f"thick={round(c['thick'],2)}° lit={round(c['lit'],2)} "
                          f"src={c['src']}")
            browser.close()
    finally:
        if not args.keep:
            server.terminate()
    print('\n->', OUT)


if __name__ == '__main__':
    main()

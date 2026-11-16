# -*- coding: utf-8 -*-
"""卫星临近校准（Himawari-9 B13, JMA MSC 免费源）
数据：https://www.data.jma.go.jp/mscweb/data/himawari/img/fd_/fd__b13_{HHMM}.jpg
      全圆盘 601x601，正交投影（星下点 0°N,140.7°E），10 分钟节奏，免费无鉴权。
几何：像素 (px,py)：px = cx + R·cosφ·sin(λ-λ0)，py = cy - R·sin(φ)，
      R 为圆盘半径像素（γ=90°）。JMA 601 产品 R≈280，中心≈(300.5,300.5)。
判读：B13 亮=冷=高云/厚云，暗=暖=晴空/低云。Otsu 自适应阈值 + 路径亮度剖面。
"""
import io
import json
import logging
import math
import os
import threading
import time as _time
from datetime import datetime, timedelta, timezone
from ftplib import FTP

import numpy as np
import requests
from PIL import Image, ImageDraw

log = logging.getLogger(__name__)

_HERE = os.path.dirname(os.path.abspath(__file__))
_CFG = os.path.join(os.path.dirname(_HERE), 'config_private.json')


def _ptree_cfg():
    """优先环境变量 PTREE_HOST/PTREE_USER/PTREE_PASS，回退 config_private.json。"""
    host = (os.environ.get('PTREE_HOST') or '').strip()
    user = (os.environ.get('PTREE_USER') or '').strip()
    pw = (os.environ.get('PTREE_PASS') or '').strip()
    if host and user and pw:
        return {'host': host, 'user': user, 'pass': pw}
    try:
        with open(_CFG, encoding='utf-8') as f:
            cfg = json.load(f).get('ptree') or {}
        if cfg.get('host') and cfg.get('user') and cfg.get('pass'):
            return cfg
    except Exception:
        pass
    return None

BASE = ('https://www.data.jma.go.jp/mscweb/data/himawari/img/fd_/'
        'fd__b13_{hhmm}.jpg')
SUB_LON = 140.7
IMG_SIZE = 601
# 圆盘几何（2026-09-14 实测标定，见下）。旧值 (300.5, 300.5, 280.0) 是估的：
# 它把珠三角的光路采样点画在真实位置西南约 400km 处——用户在卫星预览里看到的
# 「光路不在目标点那边」，一半原因就是这个。
#
# 实测依据（fd__b13_*.jpg，601x601）：
#   1) 水平：y=300 行上纯白是图像边框（x<=11），x=12 起即圆盘内容，
#      右侧 x=588 起为边框 -> 弦长 576 -> 圆心 x=300.0、R=288。
#   2) 垂直：x=300 列 y=0 已是圆盘内容（顶边被画面裁掉约 2px），
#      底部内容到 y≈575（其下是白带 + 黑底字幕条）-> 圆心 y≈287、R≈288。
#   3) 交叉验证：把 P-Tree 5km 真实亮温网格按本投影重采样，与 JMA 灰度做相关
#      （冷=亮，故应为强负相关）：旧常量 r=-0.258，实测常量 r=-0.376，
#      内圈最优约 (300, 286, 292) r=-0.45。三者一致指向 CY≈286、R≈288~292。
# 残差不确定性约 ±1%，在珠三角相当于 ±30~40km；如需更准，可用
# GC_SAT_CX/GC_SAT_CY/GC_SAT_RAD 覆盖，或看 docs/方案设计.md 的自标定说明。
CX = float(os.environ.get('GC_SAT_CX', 300.0))
CY = float(os.environ.get('GC_SAT_CY', 288.0))
RAD = float(os.environ.get('GC_SAT_RAD', 288.0))
# 圆盘右/下侧的边框与字幕条（画布裁剪时排除，避免把边框当云）
CROP_Y_MAX = 574

_CACHE = {}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def fetch_b13_slot(slot_utc, max_age_min: int = 180):
    """取指定槽位的 B13 帧。

    返回 (灰度ndarray, 槽位UTC时间, url, 文件Last-Modified datetime) 或
    全 None。**槽位时间才是观测时次**，Last-Modified 只是发布时间——两者
    要分开：JMA 同名文件每日覆盖，实测有的槽位停在 24 小时前（13 日
    10:21 的图挂在 14 日 10:10 的文件名上），只按文件名取会拿到旧图。
    故这里用 Last-Modified 做新鲜度闸门，并把两个时间都回给调用方。
    """
    now = _utcnow()
    hhmm = slot_utc.strftime('%H%M')
    url = BASE.format(hhmm=hhmm)
    ckey = slot_utc.strftime('%Y%m%d%H%M')
    entry = _CACHE.get(ckey)          # .get 原子读，避免与淘汰 pop 竞态
    if entry is not None:
        return entry[0], slot_utc, entry[2], entry[1]
    try:
        head = requests.head(url, timeout=15)
        lm = head.headers.get('Last-Modified')
        if not lm:
            return None, None, None, None
        lm_dt = datetime.strptime(
            lm.strip(), '%a, %d %b %Y %H:%M:%S GMT').replace(tzinfo=timezone.utc)
        if (now - lm_dt).total_seconds() / 60.0 > max_age_min:
            return None, None, None, None
        resp = requests.get(url, timeout=25)
        if resp.status_code == 200 and len(resp.content) > 15000:
            img = Image.open(io.BytesIO(resp.content)).convert('L')
            arr = np.asarray(img, dtype=np.float32)
            _CACHE[ckey] = (arr, lm_dt, url)
            if len(_CACHE) > 24:
                _CACHE.pop(next(iter(_CACHE)), None)
            return arr, slot_utc, url, lm_dt
    except Exception:
        pass
    return None, None, None, None


def fetch_latest_b13(max_back_min: int = 150):
    """拉取最新且新鲜的 B13 全圆盘图。
    返回 (灰度ndarray, 槽位UTC时间, url) 或全 None。"""
    now = _utcnow()
    grid = now.replace(minute=(now.minute // 10) * 10, second=0, microsecond=0)
    for k in range(0, max_back_min // 10 + 1):
        slot = grid - timedelta(minutes=10 * k)
        arr, s, url, _lm = fetch_b13_slot(slot, max_age_min=25)
        if arr is not None:
            return arr, s, url
    return None, None, None


def latlon_to_px(lat: float, lon: float):
    dlam = math.radians(lon - SUB_LON)
    phi = math.radians(lat)
    if math.cos(phi) * math.cos(dlam) < 0:   # 背面
        return None
    px = CX + RAD * math.cos(phi) * math.sin(dlam)
    py = CY - RAD * math.sin(phi)
    # 必须落在圆盘内部：图像四周有纯白边框、底部有字幕条，采到那里会
    # 读到 255（极冷）而被误判成"高云"。
    if not disk_interior(px, py):
        return None
    return px, py


def sample_gray(arr, px, py, k=2):
    """3x3(k=2) 邻域均值；出界返回 None"""
    x, y = int(round(px)), int(round(py))
    h, w = arr.shape
    if not (k <= x < w - k and k <= y < h - k):
        return None
    return float(arr[y - k:y + k + 1, x - k:x + k + 1].mean())


def px_per_km(lat: float, lon: float) -> float:
    """该点附近 1km 对应多少像素（正交投影局部尺度）。

    正交投影下地面线度被压缩 cos(c) 倍，c 为到星下点的大圆角距。
    珠三角实测约 0.039 px/km —— 反过来说 **1 像素约 26km**，
    这就是 JMA 601px 全圆盘在本地的真实分辨率（P-Tree 5km 细 5 倍）。
    """
    c = math.acos(max(-1.0, min(1.0, math.cos(math.radians(lat))
                               * math.cos(math.radians(lon - SUB_LON)))))
    return RAD / 6371.0088 * math.cos(c)


def disk_interior(px, py, margin_px=2.0) -> bool:
    """像素是否落在圆盘内部（排除边框/字幕条/背面）。"""
    if px is None or py is None:
        return False
    if not (margin_px <= px < IMG_SIZE - margin_px):
        return False
    if not (margin_px <= py < CROP_Y_MAX):
        return False
    dx, dy = px - CX, py - CY
    return math.hypot(dx, dy) <= RAD - margin_px


def otsu_threshold(arr):
    """圆盘内 Otsu 二值化阈值（暗=晴空/低云，亮=中高云）"""
    h, w = arr.shape
    yy, xx = np.ogrid[:h, :w]
    mask = ((xx - CX) ** 2 + (yy - CY) ** 2) <= (RAD * 0.98) ** 2
    vals = arr[mask]
    hist, _ = np.histogram(vals, bins=64, range=(0, 256))
    total = vals.size
    sum_all = np.dot(np.arange(64), hist)
    sum_b = 0.0
    w_b = 0
    best_t, best_var = 0, -1.0
    for t in range(64):
        w_b += hist[t]
        if w_b == 0:
            continue
        w_f = total - w_b
        if w_f == 0:
            break
        sum_b += t * hist[t]
        m_b = sum_b / w_b
        m_f = (sum_all - sum_b) / w_f
        var_between = w_b * w_f * (m_b - m_f) ** 2
        if var_between > best_var:
            best_var = var_between
            best_t = t
    return best_t * 4.0   # 映射回 0-255 灰度


def _north_vec(lat, lon):
    """该点的投影空间"北"方向单位向量（正交投影下北并非正上方）。

    x ∝ cosφ·sinΔλ、y ∝ -sinφ，对 φ 求导得 (-sinφ·sinΔλ, -cosφ)，归一化。
    """
    dlam = math.radians(lon - SUB_LON)
    phi = math.radians(lat)
    return (-math.sin(phi) * math.sin(dlam), -math.cos(phi))


def _path_runs(samples):
    """沿光路的连续同判读段 -> [(d1, d2, state)]。

    state 取样本的 cloud 值（JMA 为 True/False，P-Tree 为云类字符串）；
    判读为 None（缺测/出盘）的样本不入段。
    """
    runs, start, prev, state = [], None, None, None
    for s in samples:
        c = s.get('cloud')
        if c is None or c != state:
            if state is not None and start is not None:
                runs.append((start, prev, state))
            start, state = (s['dist'] if c is not None else None), c
        prev = s['dist']
    if state is not None and start is not None:
        runs.append((start, prev, state))
    return runs


def crop_with_path(arr, observer_px, path_px_list, pad_km=150.0, out_path=None,
                   lat=None, lon=None, az=None, uarr=None, samples=None):
    """按**光路**取景裁剪 JMA 全圆盘并叠加标注，返回 PIL Image（RGB）。

    旧版以观测点为中心固定裁 70px：在珠三角相当于 3700km 宽的画面，
    而 620km 的光路只占其中约 25px（约 1% 面积），方向完全看不出来——
    这正是"光路好像不在我选的目标点那边"的表象来源。现改为按
    观测点 + 光路点的包围盒取景，pad 用 km 给定（按本地 px/km 换算）。

    标注：橙色光路折线 + 采样环、红色机位点（OBS）、黄十字目标端
    （TGT，即光路最远点，也就是目标方位方向）、比例尺、指北针、
    方位角。全部用 ASCII 标签（PIL 默认点阵字体不含中文）。
    """
    img = Image.fromarray(arr.astype(np.uint8)).convert('RGB')
    src_w, src_h = img.size
    pts = [(p[0], p[1]) for p in path_px_list if p[0] is not None]
    if observer_px:
        pts.append((observer_px[0], observer_px[1]))
    if len(pts) < 2:
        return None
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    # 本地 px/km（拿不到经纬度时按圆盘中心附近近似，宁可裁大一点）
    ppk = px_per_km(lat, lon) if (lat is not None and lon is not None) else (
        RAD / 6371.0088)
    ppk = max(1e-3, ppk)
    pad = pad_km * ppk
    x0 = int(max(4, math.floor(min(xs) - pad)))
    x1 = int(min(src_w - 4, math.ceil(max(xs) + pad)))
    y0 = int(max(4, math.floor(min(ys) - pad)))
    y1 = int(min(CROP_Y_MAX, math.ceil(max(ys) + pad)))
    # 太小的框补到最小可见尺寸（短光路也要有上下文）
    min_px = 44
    if x1 - x0 < min_px:
        c = (x0 + x1) // 2
        x0, x1 = max(4, c - min_px // 2), min(src_w - 4, c + min_px // 2)
    if y1 - y0 < min_px:
        c = (y0 + y1) // 2
        y0, y1 = max(4, c - min_px // 2), min(CROP_Y_MAX, c + min_px // 2)
    if x1 - x0 < 8 or y1 - y0 < 8:
        return None
    crop = img.crop((x0, y0, x1, y1))
    scale = max(2, int(round(560.0 / max(1, x1 - x0))))
    scale = min(scale, 24)                    # 上采样上限，别把马赛克放太大
    cw, ch = (x1 - x0) * scale, (y1 - y0) * scale
    crop = crop.resize((cw, ch), Image.NEAREST)

    def tf(px, py):
        return (int((px - x0) * scale), int((py - y0) * scale))

    draw = ImageDraw.Draw(crop)
    # 逐段按云判读着色（红=云 绿=隙 琥珀=段内含云隙边界 橙=缺测）：
    # 深色衬底先整条铺，再逐段覆盖 —— 图上直接读出"哪一段有云"，
    # 不用对着下面的判读列表逐条对距离
    valid_pairs = []
    for i, p in enumerate(path_px_list):
        if p[0] is None:
            continue
        c = samples[i].get('cloud') if samples and i < len(samples) else None
        valid_pairs.append((p, c))
    line = [tf(p[0], p[1]) for p, _ in valid_pairs]
    if len(line) >= 2:
        draw.line(line, fill=(120, 40, 0), width=5)       # 深色描边衬底
    seg_col = {True: (255, 60, 60), False: (90, 225, 130)}
    for (p0, c0), (p1, c1) in zip(valid_pairs, valid_pairs[1:]):
        a, b = tf(p0[0], p0[1]), tf(p1[0], p1[1])
        if c0 is True and c1 is True:
            col = seg_col[True]
        elif c0 is False and c1 is False:
            col = seg_col[False]
        elif c0 is None and c1 is None:
            col = (255, 150, 40)                          # 缺测：保持原橙色
        else:
            col = (255, 210, 60)                          # 段内跨云隙边界
        draw.line([a[0], a[1], b[0], b[1]], fill=col, width=2)
    # 逐段标注：只标 ≥60km 的云带/云隙（短的看颜色即可），C=云带 G=云隙
    for d1, d2, st in (_path_runs(samples) if samples else []):
        if st is None or d2 - d1 < 60:
            continue
        mid = (d1 + d2) / 2.0
        k = min(range(len(samples)),
                key=lambda i: abs(samples[i]['dist'] - mid))
        if k >= len(path_px_list) or path_px_list[k][0] is None:
            continue
        cxp, cyp = tf(path_px_list[k][0], path_px_list[k][1])
        txt = ('C %.0f-%.0fkm' if st else 'G %.0f-%.0fkm') % (d1, d2)
        draw.text((cxp + 6, cyp - 14), txt,
                  fill=(255, 120, 110) if st else (150, 240, 170))
    for (px, py, d) in path_px_list:
        if px is None:
            continue
        cxp, cyp = tf(px, py)
        draw.ellipse([cxp - 4, cyp - 4, cxp + 4, cyp + 4], outline=(255, 200, 60),
                     width=1)
    # 距离标签：只标几个，且避开目标端（否则和目标标签叠在一起）
    valid_pts = [p for p in path_px_list if p[0] is not None]
    tgt_xy = tf(valid_pts[-1][0], valid_pts[-1][1]) if valid_pts else None
    step = max(1, len(valid_pts) // 4)
    for i, (px, py, d) in enumerate(valid_pts):
        if i % step:
            continue
        cxp, cyp = tf(px, py)
        if tgt_xy and math.hypot(cxp - tgt_xy[0], cyp - tgt_xy[1]) < 34:
            continue
        draw.text((cxp + 6, cyp + 4), f'{d:.0f}km', fill=(255, 235, 150))
    if observer_px:
        ox, oy = tf(observer_px[0], observer_px[1])
        draw.ellipse([ox - 6, oy - 6, ox + 6, oy + 6], fill=(255, 40, 40))
        draw.text((ox + 8, oy + 6), 'OBS', fill=(255, 90, 60))
    if tgt_xy:
        tx, ty = tgt_xy
        draw.line([tx - 9, ty, tx + 9, ty], fill=(255, 240, 80), width=2)
        draw.line([tx, ty - 9, tx, ty + 9], fill=(255, 240, 80), width=2)
        tgt_txt = 'TGT' + (f' {az:.0f}' if az is not None else '')
        draw.text((tx + 11, ty - 18), tgt_txt, fill=(255, 240, 80))
        draw.text((tx + 11, ty + 6), f'{valid_pts[-1][2]:.0f}km',
                  fill=(255, 235, 150))
    # 指北针
    if lat is not None and lon is not None:
        nx, ny = _north_vec(lat, lon)
        cx0, cy0 = 30, 34
        draw.line([cx0, cy0, cx0 + nx * 20, cy0 + ny * 20], fill=(200, 240, 255),
                  width=2)
        draw.text((cx0 - 4, cy0 + 22), 'N', fill=(200, 240, 255))
    # 比例尺（取 40~140px 之间的整数公里）
    bar_km = None
    for cand in (25, 50, 100, 200, 400, 800):
        if 40 <= cand * ppk * scale <= 140:
            bar_km = cand
            break
    if bar_km:
        bx0, by0 = 16, ch - 22
        bx1 = int(bx0 + bar_km * ppk * scale)
        draw.line([bx0, by0, bx1, by0], fill=(255, 255, 255), width=3)
        draw.text((bx0, by0 - 16), f'{bar_km}km', fill=(255, 255, 255))
    if out_path:
        crop.save(out_path)
    meta = {'x0': x0, 'y0': y0, 'x1': x1, 'y1': y1, 'scale': scale,
            'km_w': round((x1 - x0) / ppk, 1),
            'km_h': round((y1 - y0) / ppk, 1)}
    return crop, meta


def _to_b64(img):
    import base64
    buf = io.BytesIO()
    img.save(buf, format='PNG')
    return 'data:image/png;base64,' + base64.b64encode(buf.getvalue()).decode()


def path_points_along(lat, lon, az, span_km=620.0, step_km=30.0):
    """沿方位取光路采样点 [(lat, lon, dist), ...]（含观测点）。"""
    try:
        from app.core import cities as _citydb
    except ImportError:
        from core import cities as _citydb
    pts = [(lat, lon, 0.0)]
    d = step_km
    while d <= span_km + 1e-6:
        plat, plon = _citydb.destination_point(lat, lon, az, d)
        pts.append((plat, plon, float(d)))
        d += step_km
    return pts


def nowcast(lat: float, lon: float, event_time_local, az: float,
            path_points=None, kind='sunset', arr=None, t_utc=None, url=None,
            lm_utc=None):
    """卫星临近校准主入口。
    path_points: [(lat, lon, distance_km), ...] 沿光路采样（含观测点）。
    arr/t_utc/url: 复用外部已取到的帧（时间轴拖动时逐帧复用同一缓存）。
    lm_utc: 该帧的文件发布时间（Last-Modified），用于区分"观测时次"与
    "发布时次"（JMA 有槽位停在 24h 前，只看文件名会把旧图当新图）。
    返回 dict：卫星时次、路径逐点判读（亮值/云类）、云带边界、模型对比、裁剪图。"""
    if arr is None:
        arr, t_utc, url = fetch_latest_b13()
    if arr is None:
        return {'available': False, 'reason': '卫星图获取失败（稍后重试）'}
    thr = otsu_threshold(arr)

    obs_px = latlon_to_px(lat, lon)
    samples = []
    path_px_list = []
    for (plat, plon, dist) in (path_points or [(lat, lon, 0)]):
        px = latlon_to_px(plat, plon)
        if px is None:
            samples.append({'dist': dist, 'gray': None, 'cloud': None})
            path_px_list.append((None, None, dist))
            continue
        g = sample_gray(arr, px[0], px[1])
        cloud = None if g is None else (g > thr)
        samples.append({'dist': dist, 'gray': None if g is None else round(g),
                        'cloud': cloud, 'lat': plat, 'lon': plon})
        path_px_list.append((px[0], px[1], dist))

    ppk = px_per_km(lat, lon)
    crop = None
    crop_meta = None
    if obs_px:
        try:
            cimg, crop_meta = crop_with_path(
                arr, obs_px, path_px_list, lat=lat, lon=lon, az=az,
                samples=samples)
            crop = _to_b64(cimg) if cimg is not None else None
        except Exception as e:
            log.info('[sat] crop skip: %s', str(e)[:80])
            crop, crop_meta = None, None

    # 云带/云隙判读
    cloudy_d = [s['dist'] for s in samples if s['cloud']]
    clear_d = [s['dist'] for s in samples if s['cloud'] is False]
    bands, gaps = [], []
    if samples:
        def runs(pred):
            out = []
            start = None
            prev = None
            for s in samples:
                ok = pred(s)
                if ok and start is None:
                    start = s['dist']
                    prev = s['dist']
                elif ok and start is not None:
                    prev = s['dist']
                elif not ok and start is not None:
                    out.append((start, prev))
                    start = None
            if start is not None:
                out.append((start, prev))
            return out
        bands = runs(lambda s: s['cloud'] is True)
        gaps = runs(lambda s: s['cloud'] is False)
    frac = (len(cloudy_d) / max(1, len(cloudy_d) + len(clear_d)))
    lag_min = (_utcnow() - t_utc).total_seconds() / 60.0
    pub_min = (round((_utcnow() - lm_utc).total_seconds() / 60.0)
               if lm_utc is not None else None)
    valid_px = [p for p in path_px_list if p[0] is not None]
    return {
        'available': True,
        # 卫星时次 = 观测槽位时刻（不是文件发布时间）；age_min 为观测龄
        'sat_time_utc': t_utc.strftime('%H:%M'),
        'sat_time_bjt': (t_utc + timedelta(hours=8)).strftime('%H:%M'),
        'age_min': round(lag_min),
        'published_min': pub_min,
        'threshold': round(thr),
        'path': samples,
        'cloud_bands': bands,          # [(d1,d2)] 卫星实测云带
        'clear_gaps': gaps,            # [(d1,d2)] 卫星实测云隙
        'cloudy_frac': round(frac, 2),
        'obs_px': list(obs_px) if obs_px else None,
        'target_px': (list(valid_px[-1][:2]) if valid_px else None),
        'crop_png': crop,
        'crop_km': (crop_meta or {}).get('km_w'),
        'crop_scale': (crop_meta or {}).get('scale'),
        # 分辨率事实：601px 全圆盘在本地的像素尺度（珠三角约 26km/px）
        'px_per_km': round(ppk, 4),
        'km_per_px': round(1.0 / ppk, 1) if ppk else None,
        'sample_km': (path_points[1][2] - path_points[0][2]
                      if path_points and len(path_points) > 1 else None),
        'source': url,
    }


# =================================================================
# JAXA P-Tree 5km B13 亮温：后台预取 + 高分辨率判读
# 文件：/jma/netcdf/{YYYYMM}/{DD}/NC_H09_{date}_{HHMM}_R21_FLDK.02801_02401.nc
# 网格：等经纬 EQR 60S-60N / 70E-150W，2801x2401（0.0785°/px lon, 0.05°/px lat）
# =================================================================

_PTREE_LOCK = threading.Lock()
_CLP_LOCK = threading.Lock()     # CLP FTP 下载互斥（同槽位并发写文件冲突）
_START_LOCK = threading.Lock()   # 预取线程启动互斥
_PREFETCH_STARTED = False
_ARMED = {}
_ARMED_LOCK = threading.Lock()
_ARMED_KEEP_MIN = 720      # 事件过去 12 小时后不再值得预取
_ARMED_MAX = 256           # 条目上限（防常驻进程无界增长）
_FTP_BREAKER = {'fail_streak': 0, 'blocked_until': 0.0}  # FTP 熔断器


def _ftp_blocked():
    return _time.time() < _FTP_BREAKER['blocked_until']


def _ftp_fail():
    _FTP_BREAKER['fail_streak'] += 1
    if _FTP_BREAKER['fail_streak'] >= 2:
        _FTP_BREAKER['blocked_until'] = _time.time() + 1800  # 熔断 30 分钟


def _ftp_ok():
    _FTP_BREAKER['fail_streak'] = 0


def _cache_dir():
    d = os.path.normpath(os.path.join(_HERE, '..', '..', 'data', 'sat_cache'))
    os.makedirs(d, exist_ok=True)
    return d


def _latest_cached_slot():
    """返回 (slot_dt, path) 最新完整缓存文件（大小>100MB 视为完整）"""
    d = _cache_dir()
    best = None
    try:
        for fn in os.listdir(d):
            m = fn.startswith('NC_H09_') and fn.endswith('.02801_02401.nc')
            if not m:
                continue
            p = os.path.join(d, fn)
            if os.path.getsize(p) < 100e6:
                continue
            # 解析槽位
            try:
                dt_s = fn.split('_')[2] + fn.split('_')[3]
                slot = datetime.strptime(dt_s, '%Y%m%d%H%M').replace(
                    tzinfo=timezone.utc)
            except Exception:
                continue
            if best is None or slot > best[0]:
                best = (slot, p)
    except Exception:
        pass
    return best


def _download_latest_slot():
    cfg = _ptree_cfg()
    if not cfg:
        return None
    now = _utcnow()
    grid = now.replace(minute=(now.minute // 10) * 10, second=0,
                       microsecond=0)
    for k in range(0, 7):
        slot = grid - timedelta(minutes=10 * k)
        d = slot.strftime('%Y%m%d')
        hhmm = slot.strftime('%H%M')
        fn = f'NC_H09_{d}_{hhmm}_R21_FLDK.02801_02401.nc'
        out = os.path.join(_cache_dir(), fn)
        remote = f'jma/netcdf/{d[:6]}/{d[6:]}/{fn}'
        if _ftp_blocked():
            break
        ftp = None
        try:
            ftp = FTP(cfg['host'], timeout=120)
            ftp.login(cfg['user'], cfg['pass'])
            ftp.voidcmd('TYPE I')
            rsize = None
            try:
                rsize = ftp.size(remote)
            except Exception:
                pass
            if not rsize:
                continue          # 远端没有该槽位；连接由 finally 关闭
            lsize = os.path.getsize(out) if os.path.exists(out) else 0
            if lsize == rsize:
                ftp.quit()
                _prune_cache()
                return slot, out
            log.info('[ptree] %s %s %s/%s',
                         'resume' if lsize else 'download', fn, lsize, rsize)
            t0 = _time.time()
            with open(out, 'ab' if 0 < lsize < rsize else 'wb') as f:
                ftp.retrbinary('RETR ' + remote, f.write, blocksize=1 << 20,
                               rest=lsize if 0 < lsize < rsize else None)
            ftp.quit()
            lsize2 = os.path.getsize(out)
            if lsize2 == rsize:
                log.info('[ptree] done %.0fMB in %.0fs', lsize2/1e6,
                              _time.time()-t0)
                _ftp_ok()
                _prune_cache()
                return slot, out
            log.warning('[ptree] size mismatch %s != %s', lsize2, rsize)
        except Exception as e:
            log.warning('[ptree] fail: %s', str(e)[:100])
        finally:
            # v2.37 审查报告 ⑫：早先只有成功路径 quit()，`continue` 与异常路径
            # 都靠引用计数回收 —— 每次进入循环都可能多挂一条控制连接。
            if ftp is not None:
                try:
                    ftp.close()       # 已 quit 的连接再 close 是幂等的
                except Exception:
                    pass
    return None


def _prune_cache(keep: int = 2):
    """清理卫星缓存目录，仅保留最新 keep 个槽位文件（防磁盘占满）"""
    d = _cache_dir()
    files = []
    try:
        for fn in os.listdir(d):
            if not (fn.startswith('NC_H09_') and fn.endswith('.nc')):
                continue
            p = os.path.join(d, fn)
            try:
                dt_s = fn.split('_')[2] + fn.split('_')[3]
                slot = datetime.strptime(dt_s, '%Y%m%d%H%M').replace(
                    tzinfo=timezone.utc)
            except Exception:
                continue
            files.append((slot, p))
    except Exception:
        return
    files.sort(key=lambda x: x[0])
    for slot, p in files[:-keep] if len(files) > keep else []:
        try:
            os.remove(p)
            log.info('[ptree] pruned %s', os.path.basename(p))
        except Exception:
            pass


def _prune_armed(now=None):
    """清掉过期事件，并把条目数压到上限内（先丢事件时刻最早的）。

    旧版 ``_ARMED`` 只增不减：常驻服务里每查一次报告就多一行，60 秒循环
    每次全表遍历（审查报告 ⑥）。这里在写入前与循环里各调一次。
    """
    now = now or _utcnow()
    with _ARMED_LOCK:
        for k, ev in list(_ARMED.items()):
            if (now - ev['event_utc']).total_seconds() / 60.0 > _ARMED_KEEP_MIN:
                _ARMED.pop(k, None)
        extra = len(_ARMED) - _ARMED_MAX
        if extra > 0:
            stale = sorted(_ARMED.items(), key=lambda kv: kv[1]['event_utc'])
            for k, _ev in stale[:extra]:
                _ARMED.pop(k, None)


def _prefetch_loop():
    while True:
        try:
            now = _utcnow()
            _prune_armed(now)
            for key, ev in list(_ARMED.items()):
                dtmin = (ev['event_utc'] - now).total_seconds() / 60.0
                if -60 <= dtmin <= 480:
                    fresh = _latest_cached_slot()
                    age = None
                    if fresh:
                        age = (now - fresh[0]).total_seconds() / 60.0
                    if age is None or age > 30:
                        with _PTREE_LOCK:
                            _download_latest_slot()
        except Exception as e:
            log.warning('[ptree] loop err: %s', str(e)[:100])
        _time.sleep(60)


def arm_event(lat: float, lon: float, date_str: str, kind: str,
              event_utc: datetime):
    """注册事件触发后台预取（幂等，锁内检查-置位防重复启动预取线程）。"""
    global _PREFETCH_STARTED
    with _ARMED_LOCK:
        _ARMED[f'{lat},{lon},{date_str},{kind}'] = {'event_utc': event_utc}
    _prune_armed()      # 插入后再裁：过期事件当场出表，超上限时先丢最早的
    with _START_LOCK:
        if not _PREFETCH_STARTED:
            threading.Thread(target=_prefetch_loop, daemon=True).start()
            _PREFETCH_STARTED = True


def parse_ptree_b13(nc_path):
    """解析 P-Tree NetCDF（h5py 直读，netCDF4 与该文件不兼容）。
    返回 (bt2d[K], lat 向量[降序], lon 向量[升序])。
    tbb_13: int16, scale=0.01, offset=273.15, missing=-32768。"""
    import h5py
    f = h5py.File(nc_path, 'r')
    d = f['tbb_13']
    sf = float(np.asarray(d.attrs.get('scale_factor', [1.0])).ravel()[0])
    ao = float(np.asarray(d.attrs.get('add_offset', [0.0])).ravel()[0])
    lat = np.asarray(f['latitude'][:], dtype=np.float32)
    lon = np.asarray(f['longitude'][:], dtype=np.float32)
    arr = np.asarray(d[:], dtype=np.float32) * sf + ao
    arr[arr < 150] = np.nan      # missing (-32768) 与无效值
    f.close()
    return arr, lat, lon


def bt_class(bt):
    if bt is None or bt != bt:  # NaN
        return None
    if bt < 240:
        return '高云砧'
    if bt < 265:
        return '中云'
    if bt < 285:
        return '低云/可能晴'
    return '晴'


def ptree_nowcast(lat, lon, path_points):
    """P-Tree 5km B13 亮温判读（优先于 JMA 601px）"""
    fresh = _latest_cached_slot()
    if not fresh:
        return None
    slot, nc_path = fresh
    arr, latv, lonv = parse_ptree_b13(nc_path)
    lat_desc = latv[0] > latv[-1]
    dlat = abs(latv[1] - latv[0])
    dlon = lonv[1] - lonv[0]

    def bt_at(plat, plon):
        iy = (latv[0] - plat) / dlat if lat_desc else (plat - latv[0]) / dlat
        ix = (plon - lonv[0]) / dlon
        h, w = arr.shape
        if not (0 <= ix < w - 1 and 0 <= iy < h - 1):
            return None
        x0, y0 = int(ix), int(iy)
        fx, fy = ix - x0, iy - y0
        v00 = arr[y0, x0]
        v10 = arr[y0, x0 + 1]
        v01 = arr[y0 + 1, x0]
        v11 = arr[y0 + 1, x0 + 1]
        if any(v != v for v in (v00, v10, v01, v11)):
            return None
        return float((v00 * (1 - fx) * (1 - fy) + v10 * fx * (1 - fy)
                      + v01 * (1 - fx) * fy + v11 * fx * fy))

    samples = []
    for (plat, plon, dist) in path_points:
        bt = bt_at(plat, plon)
        samples.append({'dist': dist, 'bt': None if bt is None else round(bt),
                        'cloud': bt_class(bt),
                        'lat': plat, 'lon': plon})
    runs = {}
    for cls in ['高云砧', '中云', '低云/可能晴', '晴']:
        out, start, prev = [], None, None
        for s in samples:
            ok = s['cloud'] == cls
            if ok and start is None:
                start, prev = s['dist'], s['dist']
            elif ok and start is not None:
                prev = s['dist']
            elif not ok and start is not None:
                out.append((start, prev))
                start = None
        if start is not None:
            out.append((start, prev))
        if out:
            runs[cls] = out
    # 亮温彩色裁剪图（路径带 + 观测点）
    crop = None
    try:
        crop = _to_b64(_bt_crop_image(arr, latv, lonv, samples, (lat, lon)))
    except Exception as e:
        log.info('[ptree] crop skip: %s', str(e)[:80])
    return {'slot_utc': slot.strftime('%H:%M'),
            'slot_bjt': (slot + timedelta(hours=8)).strftime('%H:%M'),
            'age_min': round((_utcnow() - slot).total_seconds() / 60.0),
            'path': samples, 'runs': runs, 'crop_png': crop,
            'nc_file': os.path.basename(nc_path)}


def _bt_color(bt):
    """B13 亮温 -> RGB（类离散配色）"""
    if bt is None or bt != bt:
        return (28, 28, 38)
    if bt > 285:
        return (18, 30, 68)       # 晴空 深蓝
    if bt > 273:
        return (52, 68, 105)      # 低云/可能晴 蓝灰
    if bt > 265:
        return (128, 108, 78)     # 过渡 棕
    if bt > 240:
        return (225, 130, 45)     # 中云 橙
    return (255, 55, 40)          # 高云砧 红


def _bt_lut_rgb(sub):
    """亮温矩阵 -> RGB（查表，避免逐像素 Python 调用）。"""
    lut = np.zeros((512, 3), dtype=np.uint8)
    for v in range(512):
        lut[v] = _bt_color(200.0 + v * 0.25)      # 200~328K
    idx = np.clip((sub - 200.0) / 0.25, 0, 511)
    idx = np.where(np.isfinite(sub), idx, 0).astype(np.int32)
    rgb = lut[idx]
    rgb[~np.isfinite(sub)] = (28, 28, 38)
    return rgb


def _bt_crop_image(arr, latv, lonv, samples, obs, pad_km=150, upscale=4):
    """沿路径带生成亮温伪彩色裁剪图（RGB PIL Image）+ 右侧色标。

    取景按"路径包围盒 + pad_km"，纵横比才不会退化成一条极其细长的条；
    色标给出亮温->颜色对应（不画色标只能靠猜），并标出目标端（TGT）。
    """
    lats = [s['lat'] for s in samples] + [obs[0]]
    lons = [s['lon'] for s in samples] + [obs[1]]
    margin = pad_km / 111.0
    lat_lo, lat_hi = min(lats) - margin, max(lats) + margin
    lon_lo, lon_hi = min(lons) - margin, max(lons) + margin
    # 网格索引范围（lat 向量可能降序）
    if latv[0] > latv[-1]:
        j0 = max(0, int((latv[0] - lat_hi) / abs(latv[1] - latv[0])) - 1)
        j1 = min(len(latv) - 1, int((latv[0] - lat_lo) / abs(latv[1] - latv[0])) + 1)
        lat_sec = latv[j0:j1 + 1]
    else:
        j0 = max(0, int((lat_lo - latv[0]) / (latv[1] - latv[0])) - 1)
        j1 = min(len(latv) - 1, int((lat_hi - latv[0]) / (latv[1] - latv[0])) + 1)
        lat_sec = latv[j0:j1 + 1]
    i0 = max(0, int((lon_lo - lonv[0]) / (lonv[1] - lonv[0])) - 1)
    i1 = min(len(lonv) - 1, int((lon_hi - lonv[0]) / (lonv[1] - lonv[0])) + 1)
    lon_sec = lonv[i0:i1 + 1]
    sub = arr[j0:j1 + 1, i0:i1 + 1]
    # 不翻转：lat 阵列本身降序，行 0 即最北 -> 画面北在上（地图口径）。
    # 旧版把 sub 上下翻了一次（行 0 = 最南），整片云场南北镜像——
    # 判断"云带在我北边还是南边"时会得出相反结论。
    lon_span = float(lon_sec[-1] - lon_sec[0]) if len(lon_sec) > 1 else 0.0785
    lat_span = float(lat_sec[0] - lat_sec[-1]) if len(lat_sec) > 1 else 0.05
    rgb = _bt_lut_rgb(sub)
    cw = max(60, sub.shape[1] * upscale)
    ch = max(40, sub.shape[0] * upscale)
    img = Image.fromarray(rgb).resize((cw, ch), Image.NEAREST)

    def to_xy(la, lo):
        xi = int((lo - lon_sec[0]) / lon_span * (cw - 1)) if lon_span else 0
        yi = int((lat_sec[0] - la) / lat_span * (ch - 1)) if lat_span else 0
        return max(0, min(cw - 1, xi)), max(0, min(ch - 1, yi))

    draw = ImageDraw.Draw(img)
    pts = [to_xy(s['lat'], s['lon']) for s in samples]
    draw.line(pts, fill=(40, 40, 40), width=5)
    # 逐段按云类着色（与右侧色标同一语义：红=高云砧 橙=中云 蓝=低云可能晴
    # 绿=晴，黄=段内跨类），一眼读出"哪一段有云"
    cls_col = {'高云砧': (255, 60, 60), '中云': (225, 130, 45),
               '低云/可能晴': (120, 150, 220), '晴': (90, 225, 130)}
    for s0, s1 in zip(samples, samples[1:]):
        p0, p1 = to_xy(s0['lat'], s0['lon']), to_xy(s1['lat'], s1['lon'])
        c0, c1 = s0.get('cloud'), s1.get('cloud')
        c = c0 if (c0 is not None and c0 == c1) else None
        draw.line([p0[0], p0[1], p1[0], p1[1]],
                  fill=cls_col.get(c, (255, 230, 90)), width=2)
    # 逐段标注：≥60km 的云类段（高/中云记 C，低云记 L，晴记 G）
    for d1, d2, st in _path_runs(samples):
        if st is None or d2 - d1 < 60:
            continue
        mid = (d1 + d2) / 2.0
        k = min(range(len(samples)),
                key=lambda i: abs(samples[i]['dist'] - mid))
        xy = to_xy(samples[k]['lat'], samples[k]['lon'])
        tag = 'C' if st in ('高云砧', '中云') else ('L' if st == '低云/可能晴'
                                                   else 'G')
        draw.text((xy[0] + 6, xy[1] - 14), '%s %.0f-%.0fkm' % (tag, d1, d2),
                  fill=cls_col.get(st, (255, 230, 90)))
    n = len(samples)
    for i, (xy, s) in enumerate(zip(pts, samples)):
        if s['bt'] is None:
            continue
        # 标签只打首/中/末与整百公里处，避免糊成一片
        keep = (i in (0, n - 1) or i == n // 2
                or abs(s['dist'] % 200.0) < 1e-6)
        draw.ellipse([xy[0] - 4, xy[1] - 4, xy[0] + 4, xy[1] + 4],
                     outline=(255, 255, 255), width=2)
        if keep:
            draw.text((xy[0] + 7, xy[1] - 14),
                      f"{s['dist']:.0f}km {s['bt']}K", fill=(255, 255, 255))
    ox, oy = to_xy(obs[0], obs[1])
    draw.ellipse([ox - 8, oy - 8, ox + 8, oy + 8], fill=(255, 40, 40))
    draw.text((ox + 10, oy + 4), 'OBS', fill=(255, 70, 40))
    if pts:
        tx, ty = pts[-1]
        draw.line([tx - 11, ty, tx + 11, ty], fill=(120, 255, 120), width=2)
        draw.line([tx, ty - 11, tx, ty + 11], fill=(120, 255, 120), width=2)
        draw.text((tx + 13, ty + 6), 'TGT', fill=(120, 255, 120))
    # 右侧色标：连续色带 + 刻度
    legend_w = 86
    out = Image.new('RGB', (cw + legend_w, ch), (16, 16, 22))
    out.paste(img, (0, 0))
    ld = ImageDraw.Draw(out)
    bx0, bx1 = cw + 12, cw + 30
    top, bot = 16, ch - 16
    if bot > top:
        for y in range(top, bot):
            f = (y - top) / max(1, bot - top)      # 0=顶
            bt = 300.0 - f * 80.0                  # 300K -> 220K
            ld.line([bx0, y, bx1, y], fill=_bt_color(bt))
        ld.rectangle([bx0, top, bx1, bot], outline=(90, 90, 100))
        for lbl, bt in (('300K', 300), ('280K', 280), ('260K', 260),
                        ('240K', 240), ('220K', 220)):
            y = int(top + (300.0 - bt) / 80.0 * (bot - top))
            ld.text((bx1 + 5, max(6, min(ch - 12, y - 6))), lbl,
                    fill=(225, 225, 235))
        ld.text((bx0 - 4, ch - 13), '亮温', fill=(170, 170, 180))
    return out


# =================================================================
# JAXA CLP（云特性 L2）：云型 ISCCP 分类 + 光学厚度 + 云顶温度
# /pub/himawari/L2/CLP/010/{YYYYMM}/{DD}/{HH}/NC_H09_*_L2CLP010_FLDK.nc
# 仅白昼有效（太阳反射波段反演）；0.5MB/槽位
# =================================================================

ISCCP_NAMES = {0: '无云', 1: '积云', 2: '层积云', 3: '层云', 4: '积雨云',
               5: '雨层云', 6: '高层云', 7: '高积云', 8: '卷云',
               9: '卷层云', 10: '深对流'}
# 教程云属价值：火烧云画布质量
ISCCP_VALUE = {8: '★★★★ 理想画布', 9: '★★★★★ 顶级画布（厚则挡）',
               6: '★★★★★ 顶级画布', 7: '★★★★ 理想画布',
               4: '★★★ 云砧晚霞', 10: '★★★ 云砧晚霞',
               2: '★★ 条件苛刻', 1: '★★ 对流云晚霞',
               5: '✗ 挡光', 3: '✗ 挡光', 0: '无画布'}


def _clp_cfg_ok():
    return _ptree_cfg() is not None


def clp_fetch_slot(dt_utc):
    """下载 CLP 槽位文件（0.5MB，秒级），返回本地路径或 None"""
    cfg = _ptree_cfg()
    if not cfg:
        return None
    d = dt_utc.strftime('%Y%m%d')
    hh = dt_utc.strftime('%H')
    hhmm = dt_utc.strftime('%H%M')
    fn = f'NC_H09_{d}_{hhmm}_L2CLP010_FLDK.02401_02401.nc'
    out = os.path.join(_cache_dir(), fn)
    if os.path.exists(out) and os.path.getsize(out) > 300e3:
        return out
    remote = f'pub/himawari/L2/CLP/010/{d[:6]}/{d[6:]}/{hh}/{fn}'
    if _ftp_blocked():
        return None
    # CLP 下载互斥：/api/report 与 /api/nowcast 并发时同槽位同路径写文件冲突
    with _CLP_LOCK:
        if os.path.exists(out) and os.path.getsize(out) > 300e3:
            return out   # 双检：别的线程可能刚下载完
        try:
            ftp = FTP(cfg['host'], timeout=60)
            ftp.login(cfg['user'], cfg['pass'])
            ftp.voidcmd('TYPE I')
            rsize = None
            try:
                rsize = ftp.size(remote)
            except Exception:
                pass
            if not rsize:
                ftp.quit()
                return None
            with open(out, 'wb') as fout:
                ftp.retrbinary('RETR ' + remote, fout.write,
                               blocksize=1 << 18)
            ftp.quit()
            _ftp_ok()
            return out
        except Exception as e:
            log.warning('[clp] fail: %s', str(e)[:80])
            _ftp_fail()
            try:
                if os.path.exists(out):
                    os.remove(out)
            except OSError:
                pass
            return None


def clp_profile(path_points, slot_utc=None):
    """沿光路采样 CLP：返回 {slot_bjt, points: [{dist,type_code,type,cot,cltt}], runs}"""
    now = _utcnow()
    if slot_utc is None:
        slot_utc = now.replace(minute=(now.minute // 10) * 10, second=0,
                               microsecond=0) - timedelta(minutes=20)
    nc_path = None
    for k in range(0, 10):
        cand = clp_fetch_slot(slot_utc - timedelta(minutes=10 * k))
        if cand:
            nc_path = cand
            slot_utc = slot_utc - timedelta(minutes=10 * k)
            break
    if not nc_path:
        return None
    import h5py
    f = h5py.File(nc_path, 'r')
    lat = np.asarray(f['latitude'][:], dtype=np.float32)
    lon = np.asarray(f['longitude'][:], dtype=np.float32)
    clt = np.asarray(f['CLTYPE'][:])
    cot = np.asarray(f['CLOT'][:], dtype=np.float32) * 0.01
    cltt = np.asarray(f['CLTT'][:], dtype=np.float32) * 0.01 + 150.0
    f.close()
    dlat = abs(lat[1] - lat[0])
    dlon = abs(lon[1] - lon[0])
    lat_desc = lat[0] > lat[-1]

    def idx(la, lo):
        iy = (lat[0] - la) / dlat if lat_desc else (la - lat[0]) / dlat
        ix = (lo - lon[0]) / dlon
        h, w = len(lat), len(lon)
        if not (0 <= iy < h - 1 and 0 <= ix < w - 1):
            return None
        return int(round(iy)), int(round(ix))

    points = []
    for (plat, plon, dist) in path_points:
        ij = idx(plat, plon)
        if ij is None:
            points.append({'dist': dist, 'type': None})
            continue
        iy, ix = ij
        tv = int(clt[iy, ix])
        cv = float(cot[iy, ix])
        tv = None if tv < 0 or tv > 12 or tv == 255 else tv
        cv = None if (cv != cv or cv < 0 or cv > 200) else round(cv, 1)
        points.append({'dist': dist, 'type': tv,
                       'type_name': ISCCP_NAMES.get(tv, f'类型{tv}')
                       if tv is not None else None,
                       'cot': cv,
                       'cltt': (round(float(cltt[iy, ix]), 1)
                                if -100 < cltt[iy, ix] < 400 else None)})
    # 云型 runs
    runs = {}
    for s in points:
        tn = s.get('type_name')
        if tn:
            runs.setdefault(tn, [])
            r = runs[tn]
            if r and r[-1][1] >= s['dist'] - 31:
                r[-1] = (r[-1][0], s['dist'])
            else:
                r.append((s['dist'], s['dist']))
    runs = {k: v for k, v in runs.items() if k != '无云'}
    return {'slot_bjt': (slot_utc + timedelta(hours=8)).strftime('%H:%M'),
            'age_min': round((now - slot_utc).total_seconds() / 60.0),
            'points': points, 'runs': runs,
            'nc_file': os.path.basename(nc_path)}


# 槽位可用性缓存：'list' 只装**探测成功**的槽位，最多扫 18 个候选，
# 所以真实可用槽位少于 n 时 len(list) >= n 永远为假（旧版的 bug：
# 每次调用重扫 18 次 HEAD，planet_frame(back_steps=24) 要 25 个更是
# 结构性永不命中）。现在记 'n'（本次扫描服务的目标数）与 'full'
# （18 个候选已扫完）：扫完一轮即可复用，负结果也缓存 60 秒。
_SLOT_AVAIL = {'ts': 0.0, 'list': [], 'n': 0, 'full': False}
_SLOT_LOCK = threading.Lock()
_SLOT_SCAN = 18


def available_slots(n: int = 8, max_age_min: int = 240):
    """最近 n 个**真实可用**的 B13 槽位 [(slot_utc, lm_utc), ...]，最新在前。

    "可用"= 文件存在且 Last-Modified 新鲜（JMA 会把 24h 前的旧图挂在今天的
    槽位名上，实测有）。结果缓存 60 秒：拖时间轴时同一批探测不重做。
    可用槽位不足 n 个时返回现有的全部（调用方用 len() 兜底）。
    """
    now = _utcnow()
    with _SLOT_LOCK:
        if (now.timestamp() - _SLOT_AVAIL['ts'] < 60
                and (_SLOT_AVAIL['full'] or _SLOT_AVAIL['n'] >= n)):
            return _SLOT_AVAIL['list'][:n]
    grid = now.replace(minute=(now.minute // 10) * 10, second=0, microsecond=0)
    out = []
    scanned = 0
    for k in range(0, _SLOT_SCAN):
        if len(out) >= n:
            break
        cand = grid - timedelta(minutes=10 * k)
        scanned += 1
        arr, slot, _url, lm = fetch_b13_slot(cand, max_age_min=max_age_min)
        if arr is not None:
            out.append((slot, lm))
    with _SLOT_LOCK:
        _SLOT_AVAIL.update({'ts': now.timestamp(), 'list': out, 'n': n,
                            'full': scanned >= _SLOT_SCAN})
    return out[:n]


def frame_slots(n=8):
    """最近 n 个真实可用槽位的 BJT 时刻串，最新在前。"""
    return [(s + timedelta(hours=8)).strftime('%H:%M') for s, _ in
            available_slots(n)]


def planet_frame(lat, lon, az, back_steps=0, span_km=620.0, step_km=30.0):
    """取「时间轴上第 back_steps 帧」（0 = 最新可用帧）做光路判读（轻量）。

    供前端时间轴逐帧拖动：滑块位置严格对应一帧（先探测真实可用槽位，
    不靠"名义槽位"碰运气），每帧只 1 次 HTTP，且按槽位进进程内缓存，
    来回拖同一帧为 0 请求。不含 P-Tree/CLP（那是 110MB/0.5MB 的重量级
    来源，只跟最新槽位）。
    """
    slots = available_slots(max(8, int(back_steps) + 1))
    idx = min(max(0, int(back_steps)), max(0, len(slots) - 1))
    if not slots:
        return {'available': False, 'back_steps': 0, 'slot_bjt': None,
                'reason': '近期无可用卫星帧（JMA 源不可达或全部过期）'}
    slot, lm = slots[idx]
    arr, s2, url, lm2 = fetch_b13_slot(slot, max_age_min=240)
    if arr is None:
        return {'available': False, 'back_steps': idx,
                'slot_bjt': (slot + timedelta(hours=8)).strftime('%H:%M'),
                'reason': '该时次无可用卫星帧'}
    path = path_points_along(lat, lon, az, span_km, step_km)
    nc = nowcast(lat, lon, None, az, path, arr=arr, t_utc=s2,
                 url=url, lm_utc=lm2 or lm)
    nc['back_steps'] = idx
    nc['slot_bjt'] = (s2 + timedelta(hours=8)).strftime('%H:%M')
    nc['frame_count'] = len(slots)
    return nc


def solar_elevation_at(lat, lon, jd):
    """太阳高度角（度）——jd 为 UTC 儒略日"""
    try:
        from app.core import astronomy as _astro
    except ImportError:
        from core import astronomy as _astro
    alt, _ = _astro._body_altaz('sun', lat, lon, jd)
    return alt

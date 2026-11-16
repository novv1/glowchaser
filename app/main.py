# -*- coding: utf-8 -*-
"""GlowChaser 逐光者 · 火烧云与悬日月预测 —— FastAPI 入口"""
import collections
import logging
import os
import threading
import time as _time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime

from fastapi import FastAPI, HTTPException, Query, Response
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
try:  # 以项目根运行（uvicorn app.main:app）
    from app.core import cities as citydb
    from app.core import astronomy as astro_svc
    from app.core import log_config as logcfg
    from app.services import report as rpt
    from app.services import forecast_log as flog
    from app.services import openmeteo as om_svc
    from app.services import qweather as qw_svc
    from app.services import result_cache as rc_svc
    from app.services import satellite as sat_svc
    from app.services import pft as pft_svc
except ImportError:  # 以 app/ 为根运行（发布沙箱）
    from core import cities as citydb
    from core import astronomy as astro_svc
    from core import log_config as logcfg
    from services import report as rpt
    from services import forecast_log as flog
    from services import openmeteo as om_svc
    from services import qweather as qw_svc
    from services import result_cache as rc_svc
    from services import satellite as sat_svc
    from services import pft as pft_svc

logcfg.setup_logging()
log = logging.getLogger(__name__)

def _startup_prune():
    """启动时后台清一次过期磁盘缓存（导入 openmeteo 不再有写盘副作用）。

    阈值在 prune_disk_cache 里取 max(CACHE_TTL, STALE_MAX_AGE)，不会碰
    限流兜底要用的 48h 数据。
    """
    try:
        threading.Thread(target=om_svc.prune_disk_cache, daemon=True,
                         name='om-prune').start()
    except Exception:       # 线程起不来也不该影响取数
        pass


@asynccontextmanager
async def _lifespan(_app):
    _startup_prune()
    yield


app = FastAPI(title='GlowChaser 逐光者', version='1.0.0', lifespan=_lifespan)
# 静态资源与报告 JSON 体积大（echarts 1MB），统一 gzip 压缩（首次加载省 ~70%）
app.add_middleware(GZipMiddleware, minimum_size=1024)

# 前端的 html/js/css 一律走"每次都校验"（no-cache = 用 ETag 复验，没变就是 304）。
# 默认只有 Last-Modified/ETag，浏览器会按启发式直接吃缓存：改完代码重启了服务、
# 页面却仍是旧前端（顶部于是报「前端 vX 与后端 vY 不一致」），而用户怎么刷新都没用
# ——因为缓存的旧 index.html 还指向旧 ?v= 的旧 js。宁可多一次 304 也不要这种假故障。
_CACHELESS = ('.html', '.js', '.css', '.json', '.webmanifest')


@app.middleware('http')
async def _no_store_frontend(request, call_next):
    resp = await call_next(request)
    path = request.url.path
    if path == '/' or path.endswith(_CACHELESS):
        resp.headers['Cache-Control'] = 'no-cache'
    return resp

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(BASE_DIR, 'static')


_RECENT_ERRORS = collections.deque(maxlen=20)


def _server_error(msg: str, stage: str = '-'):
    """记录完整堆栈，对外只返回通用文案 + 短编号（不把内部细节回给客户端）。

    v2.37（审查报告 ⑨）：配额/网络压力下这条兜底路径原来既没有通路名、也
    没有可核对的编号——用户报"失败了"时无从下手。现在堆栈里带 stage，
    响应里带短编号，编号同时进进程内最近错误环（`/api/health` 可见）。
    """
    ref = uuid.uuid4().hex[:8]
    log.exception('[%s] %s（stage=%s）', ref, msg, stage)
    _RECENT_ERRORS.appendleft({
        'ref': ref, 'stage': stage,
        'kind': 'unknown',
        'at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
    })
    raise HTTPException(
        status_code=500,
        detail=f'服务内部错误，请稍后重试（错误编号 {ref}）')


def _recent_errors(n: int = 5):
    return list(_RECENT_ERRORS)[:n]


def _upstream_limited(e):
    """上游额度用尽：回 429 + 可读原因，前端据此走"显示缓存 + 说明原因"。

    免费层额度按 IP 计，共享出口 IP 下很容易被别人用光；这既不是本服务的
    内部错误（500），也不是用户操作错误，必须让前端能分辨出来。
    """
    log.warning('upstream limited (%s): %s', getattr(e, 'scope', '?'),
                getattr(e, 'detail', '') or e)
    return JSONResponse(status_code=429, content={
        'error': True,
        'source': 'open-meteo',
        'scope': getattr(e, 'scope', 'limit'),
        'message': getattr(e, 'user_message', str(e)),
    })


@app.get('/api/cities')
def api_cities():
    return {'cities': citydb.PRD_CITIES}


@app.get('/api/site_probe')
def api_site_probe(
    lat: float = Query(..., ge=-90, le=90),
    lon: float = Query(..., ge=-180, le=180),
):
    """站点探测：海拔、16 方位地平线遮蔽角、光污染 Bortle（选点用）"""
    try:
        from app.services import terrain as _t
    except ImportError:  # app/ 根运行（发布沙箱）
        from services import terrain as _t  # type: ignore
    try:
        return _t.probe_site(lat, lon)
    except om_svc.UpstreamLimitError as e:
        return _upstream_limited(e)
    except Exception:
        _server_error('API 请求失败', stage='site-probe')


@app.get('/api/report')
def api_report(
    response: Response,
    lat: float = Query(..., ge=-90, le=90),
    lon: float = Query(..., ge=-180, le=180),
    date: str = Query(None, pattern=r'^\d{4}-\d{2}-\d{2}$'),
    type: str = Query('sunset', pattern='^(sunset|sunrise)$'),
    azimuth: float = Query(None, ge=0, le=360),
    tol: float = Query(2.0, ge=0.5, le=10),
    alt_max: float = Query(10.0, ge=0.5, le=60),
):
    from datetime import datetime
    date_str = date or datetime.now().strftime('%Y-%m-%d')
    try:
        return _cached(_report_key(lat, lon, date_str, type, azimuth, tol,
                                   alt_max),
                       lambda: rpt.build_report(lat, lon, date_str, type,
                                                azimuth, tol, alt_max),
                       response)
    except om_svc.UpstreamLimitError as e:
        return _upstream_limited(e)
    except Exception:
        _server_error('API 请求失败', stage='report')


@app.get('/api/align')
def api_align(
    response: Response,
    lat: float = Query(..., ge=-90, le=90),
    lon: float = Query(..., ge=-180, le=180),
    date: str = Query(None, pattern=r'^\d{4}-\d{2}-\d{2}$'),
    azimuth: float = Query(..., ge=0, le=360),
    aov: float = Query(3.0, ge=0.2, le=60),
    elev: float = Query(None, ge=-10, le=60),
    tol: float = Query(None, ge=0.2, le=30),
    alt_max: float = Query(None, ge=0.5, le=60),
    days: int = Query(14, ge=1, le=60),
    cam_height: float = Query(None, ge=0, le=9000),
    target_height: float = Query(None, ge=0, le=9000),
    target_dist_km: float = Query(None, gt=0, le=2000),
):
    """悬日月拍摄天气评估：机位（含海拔）+ 目标方位/高度/视场
    -> 对准窗口 + 天气可行性。目标高度与距离齐备时反推目标仰角。"""
    from datetime import datetime
    date_str = date or datetime.now().strftime('%Y-%m-%d')
    try:
        return _cached(_align_key(lat, lon, date_str, azimuth, aov, elev, tol,
                                  alt_max, days, cam_height, target_height,
                                  target_dist_km),
                       lambda: rpt.build_alignment(
                           lat, lon, date_str, azimuth, aov, elev, tol,
                           alt_max, days, cam_height, target_height,
                           target_dist_km),
                       response)
    except om_svc.UpstreamLimitError as e:
        return _upstream_limited(e)
    except Exception:
        _server_error('API 请求失败', stage='align')


# ---------------------------------------------------------------- 结果缓存（⑬）
# 一次 /api/report 冷查询 ≈ 9 秒 + 300~900 加权额度；同一组参数在几分钟内
# 再点一次（切城市来回、重看同一份、深链刷新）没有理由重算。上游数据本身在
# openmeteo 层已有 3 小时缓存，这里缓存的是"同一输入的装配结果"。
result_cache = rc_svc.ResultCache()
RESULT_TTL = rc_svc.RESULT_TTL
RESULT_TTL_DEGRADED = rc_svc.RESULT_TTL_DEGRADED


def _result_ttl(payload):
    """降级结果只能短缓存：数据源缺失/天气失败时的结论会随上游恢复而变。"""
    if isinstance(payload, dict) and (payload.get('data_missing')
                                      or payload.get('weather_error')):
        return RESULT_TTL_DEGRADED
    return RESULT_TTL


def _report_key(lat, lon, date_str, kind, azimuth, tol, alt_max):
    return ('report|%.4f|%.4f|%s|%s|%s|%s|%s'
            % (lat, lon, date_str, kind, azimuth, tol, alt_max))


def _align_key(lat, lon, date_str, azimuth, aov, elev, tol, alt_max, days,
               cam_height, target_height, target_dist_km):
    return ('align|%.4f|%.4f|%s|%s|%s|%s|%s|%s|%s|%s|%s|%s'
            % (lat, lon, date_str, azimuth, aov, elev, tol, alt_max, days,
               cam_height, target_height, target_dist_km))


def _cached(key, fn, response, ttl_fn=_result_ttl):
    value, hit = result_cache.get_or_compute(key, fn, ttl_fn=ttl_fn)
    if response is not None:
        response.headers['X-GC-Result-Cache'] = 'hit' if hit else 'miss'
    return value


# 地名搜索是外部免费服务（Photon / Nominatim），重复搜索没有理由重打；
# Nominatim 的使用条款明确要求 ≤1 请求/秒。
GEOCODE_TTL = int(os.environ.get('GC_GEOCODE_TTL') or 600)
GEOCODE_FAIL_TTL = int(os.environ.get('GC_GEOCODE_FAIL_TTL') or 30)
geocode_cache = rc_svc.ResultCache(ttl=GEOCODE_TTL, max_entries=128)
_geo_lock = threading.Lock()
_geo_last = [0.0]


def _geocode_ttl(payload):
    if isinstance(payload, dict) and payload.get('source') == 'unavailable':
        return GEOCODE_FAIL_TTL
    return GEOCODE_TTL


def _geocode_throttle():
    """出站地理检索串行化：间隔不小于 GC_GEOCODE_MIN_INTERVAL（默认 1s）。"""
    iv = float(os.environ.get('GC_GEOCODE_MIN_INTERVAL') or 1.0)
    if iv <= 0:
        return
    with _geo_lock:
        wait = _geo_last[0] + iv - _time.time()
        if wait > 0:
            _time.sleep(wait)
        _geo_last[0] = _time.time()


def _dedupe_places(items, tol=0.0015):
    """同一地点常返回多条（塔/车站/路口等重复条目）：按坐标去重（约 150m）。"""
    out = []
    for it in items:
        lat, lon = it.get('lat'), it.get('lon')
        if lat is None or lon is None:
            continue
        if any(abs(lat - o['lat']) <= tol and abs(lon - o['lon']) <= tol
               for o in out):
            continue
        out.append({'name': it.get('name') or '', 'lat': lat, 'lon': lon})
    return out


def _rank_local_places(tokens, limit=8):
    """本地城市/地标模糊匹配：支持空格分隔的多关键字（全部命中才算），
    并按匹配质量排序（名称前缀 > 名称包含 > 地标名 > 所属地区）。"""
    hits = []
    for c in citydb.PRD_CITIES:
        name = c.get('name') or ''
        region = c.get('region') or ''
        for sp in (c.get('spots') or []):
            sname = sp.get('name') or ''
            hay = f'{sname} {name} {region}'
            if not all(t in hay for t in tokens):
                continue
            score = 4 if sname.startswith(tokens[0]) else 1
            hits.append((score, 3, {
                'name': f"{sname}（{name}）",
                'lat': sp.get('lat'), 'lon': sp.get('lon')}))
        hay = f'{name} {region}'
        if all(t in hay for t in tokens):
            score = 4 if name.startswith(tokens[0]) else (
                3 if tokens[0] in name else 1)
            hits.append((score, 2, {
                'name': f"{name} · {region}".strip(' ·'),
                'lat': c.get('lat'), 'lon': c.get('lon')}))
    # 分高者先（同分保持原顺序：城市库自带优先级）
    hits.sort(key=lambda x: (-x[0], x[1]))
    return [h[2] for h in hits[:limit]]


@app.get('/api/geocode')
def api_geocode(
    response: Response,
    q: str = Query(..., min_length=1, max_length=60),
    limit: int = Query(8, ge=1, le=20),
):
    """地名/地标模糊搜索：本地城市与地标优先（支持多关键字），
    其次 Photon（OSM，中文地标覆盖好），Nominatim 兜底。均无需 key。

    v2.37（审查报告 ⑮）：结果进 10 分钟缓存、出站请求按 1 次/秒节流，并且
    区分「搜索服务不可用」（source=unavailable）与「确实没这个地方」（none）。
    """
    ql = q.strip()
    # 空格分隔的多关键字：全部命中才算匹配（"珠海 渔女" 也能找到）
    tokens = [t for t in ql.split() if t]
    if tokens:
        local = _rank_local_places(tokens, limit)
        if local:
            return {'results': _dedupe_places(local)[:limit],
                    'source': 'local'}
    key = 'geocode|%s|%d' % (ql, limit)
    return _cached(key, lambda: _geocode_upstream(ql, tokens, limit), response,
                   ttl_fn=_geocode_ttl)


def _geocode_upstream(ql, tokens, limit):
    import requests as _rq
    headers = {'User-Agent': 'GlowChaser/1.0 (photography weather app)'}
    # 多关键字：逐词当检索词试探，用其余词过滤结果。
    # "珠海 渔女"：先用"珠海"查（过滤"渔女"为空）→ 再用"渔女"查并限定"珠海"
    # 命中"渔女故事壁画/珠海市"。最多试两个最长的词以控住请求数与延迟。
    probes = ([ql] if len(tokens) <= 1
              else sorted(tokens, key=len, reverse=True)[:2])
    fallback = None
    answered = 0          # 有几次上游真的回了 200（用于区分"没找到"与"都挂了"）

    def _parse_photon(items_raw):
        items = []
        for f in items_raw:
            pr = f.get('properties') or {}
            c = (f.get('geometry') or {}).get('coordinates') or []
            if len(c) != 2:
                continue
            label = '/'.join(x for x in (pr.get('name'), pr.get('city'),
                                         pr.get('county'),
                                         pr.get('state')) if x)
            items.append({'name': label or f'{c[1]:.4f}, {c[0]:.4f}',
                          'lat': float(c[1]), 'lon': float(c[0])})
        return items

    # 1) Photon：OSM POI 检索，中文地标（广州塔/珠海渔女/港珠澳大桥）可命中
    for probe in probes:
        rest = [t for t in tokens if t != probe]
        try:
            _geocode_throttle()
            r = _rq.get('https://photon.komoot.io/api/',
                        params={'q': probe, 'limit': limit * 2},
                        headers=headers, timeout=12)
            if r.status_code != 200:
                continue
            answered += 1
            items = _parse_photon(r.json().get('features') or [])
            if not items:
                continue
            kept = [x for x in items
                    if all(t in (x.get('name') or '') for t in rest)]
            if kept:
                return {'results': _dedupe_places(kept)[:limit],
                        'source': 'photon'}
            if fallback is None:
                fallback = items       # 记下未过滤结果兜底
        except Exception:
            continue
    if fallback:
        return {'results': _dedupe_places(fallback)[:limit],
                'source': 'photon'}
    # 2) Nominatim 兜底（部分网络下不可达，故不作为首选）
    try:
        _geocode_throttle()
        r = _rq.get('https://nominatim.openstreetmap.org/search',
                    params={'q': probes[0], 'format': 'json',
                            'limit': limit * 2,
                            'accept-language': 'zh-CN'},
                    headers=headers, timeout=12)
        if r.status_code == 200:
            answered += 1
            items = []
            for it in (r.json() or []):
                try:
                    items.append({'name': it.get('display_name', ''),
                                  'lat': float(it['lat']),
                                  'lon': float(it['lon'])})
                except Exception:
                    continue
            kept = [x for x in items if all(
                t in (x.get('name') or '')
                for t in [y for y in tokens if y != probes[0]])]
            out = _dedupe_places(kept or items)[:limit]
            if out:
                return {'results': out, 'source': 'nominatim'}
    except Exception:
        pass
    if not answered:
        # 一个上游都没答话：这是"搜索服务不可用"，不是"没这个地方"
        return {'results': [], 'source': 'unavailable',
                'message': '地名搜索服务暂时不可用（Photon / Nominatim 均未响应），'
                           '可稍后重试，或直接在地图上点选位置。'}
    return {'results': [], 'source': 'none'}


@app.get('/api/horizon')
def api_horizon(
    lat: float = Query(..., ge=-90, le=90),
    lon: float = Query(..., ge=-180, le=180),
    date: str = Query(None, pattern=r'^\d{4}-\d{2}-\d{2}$'),
    body: str = Query('moon', pattern='^(sun|moon)$'),
    kind: str = Query('rise', pattern='^(rise|set)$'),
):
    from datetime import datetime
    date_str = date or datetime.now().strftime('%Y-%m-%d')
    try:
        return rpt.horizon_visibility_map(lat, lon, date_str, body, kind)
    except om_svc.UpstreamLimitError as e:
        return _upstream_limited(e)
    except Exception:
        _server_error('API 请求失败', stage='horizon')


@app.get('/api/nowcast')
def api_nowcast(
    lat: float = Query(..., ge=-90, le=90),
    lon: float = Query(..., ge=-180, le=180),
    date: str = Query(None, pattern=r'^\d{4}-\d{2}-\d{2}$'),
    type: str = Query('sunset', pattern='^(sunset|sunrise)$'),
    time: str = Query(None, pattern=r'^\d{2}:\d{2}$'),
    azimuth: float = Query(None, ge=0, le=360),
    body: str = Query(None, pattern='^(sun|moon)$'),
):
    """轻量临近校准（卫星+和风+几何），供前端自动刷新。
    time/azimuth/body 可选：悬日月模式按对准窗口的显式时刻/方位调用。"""
    from datetime import datetime
    date_str = date or datetime.now().strftime('%Y-%m-%d')
    try:
        if time:
            key_time = datetime.strptime(f'{date_str} {time}', '%Y-%m-%d %H:%M')
            key_az = azimuth if azimuth is not None else 270.0
            kind = body or type
        else:
            st = astro_svc.sun_times(datetime.strptime(date_str, '%Y-%m-%d'),
                                     lat, lon, 8.0)
            key_time = st['sunset'] if type == 'sunset' else st['sunrise']
            key_az = st.get('sunset_az' if type == 'sunset' else 'sunrise_az')
            kind = type
        if not key_time:
            return {'available': False, 'reason': '无事件时刻'}
        return rpt.build_nowcast(lat, lon, date_str, kind, key_time,
                                 key_az, None)
    except om_svc.UpstreamLimitError as e:
        return _upstream_limited(e)
    except Exception:
        _server_error('API 请求失败', stage='nowcast')


@app.get('/api/sat_frame')
def api_sat_frame(
    lat: float = Query(..., ge=-90, le=90),
    lon: float = Query(..., ge=-180, le=180),
    azimuth: float = Query(..., ge=0, le=360),
    back: int = Query(0, ge=0, le=24),
    span_km: float = Query(620.0, ge=60, le=1200),
):
    """单帧 JMA B13 光路判读（时间轴逐帧用，轻量：1 次 HTTP + 进程内缓存）。

    与 /api/nowcast 的区别：不拉 P-Tree(110MB)/CLP/和风，也不做形态判读，
    只给"这一帧的光路上云在哪里" + 按光路取景的裁剪图，供前端 10 分钟
    时间轴来回拖看云带移动。返回体含 slots（最近若干槽位 BJT 时刻）。"""
    try:
        from app.services import satellite as sat_svc
    except ImportError:
        from services import satellite as sat_svc  # type: ignore
    try:
        out = sat_svc.planet_frame(lat, lon, azimuth, back_steps=back,
                                   span_km=span_km)
        out['slots'] = sat_svc.frame_slots(8)
        return out
    except Exception:
        _server_error('API 请求失败', stage='sat-frame')


@app.get('/api/sun_calendar')
def api_sun_calendar(
    lat: float = Query(..., ge=-90, le=90),
    lon: float = Query(..., ge=-180, le=180),
    azimuth: float = Query(..., ge=0, le=360),
    tol: float = Query(1.0, ge=0.2, le=5),
    body: str = Query('sun', pattern='^(sun|moon)$'),
    kind: str = Query('rise', pattern='^(rise|set|sunrise|sunset)$'),
    days: int = Query(400, ge=30, le=400),
    year: int = Query(None, ge=2020, le=2100),
):
    """悬日月"下次对准"日历：未来 days 天内升/落方位落在 azimuth±tol。

    body/kind: sun|moon × rise|set（兼容旧的 sunrise|sunset 写法）。
    days: 扫描天数，默认 400（≈13 个月），限制 30~400。
    year: 旧参数，向后兼容——给出时 start = (max(当年, year), 今天的月日)，
      即"从今年今天的日子 / 指定年份的同月同日"起算；未给出即从今天起算。
    与 /api/align 同源：判定用 core.astronomy 的 sun_times/moon_times
    （此处不传 horizon_fn，即不叠加地形遮蔽）。
    """
    try:
        from app.services import calendars as cal
    except ImportError:
        from services import calendars as cal
    _kind = {'sunrise': 'rise', 'sunset': 'set'}.get(kind, kind)
    try:
        start = None
        if year is not None:
            from datetime import date, datetime
            _now = datetime.now()
            start = date(max(_now.year, year), _now.month, _now.day)
        return cal.alignment_calendar(lat, lon, azimuth, tol, body=body,
                                      kind=_kind, days=days, start=start)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception:
        _server_error('API 请求失败', stage='sun-calendar')


@app.post('/api/feedback')
def api_feedback(body: dict):
    """回填实况：{event_key, outcome, note?}"""
    ok = flog.save_outcome(body.get('event_key', ''),
                           body.get('outcome', ''),
                           body.get('note', ''))
    if not ok:
        raise HTTPException(status_code=400, detail='event_key 不存在或 outcome 无效')
    return {'ok': True}


@app.get('/api/stats')
def api_stats():
    """按预报判定的实测命中统计（校准迭代用）"""
    return flog.stats()


@app.get('/api/health')
def api_health():
    """数据源连通性诊断（远程自检用）。

    v2.37（审查报告 ⑭）：四段探测共用 20 秒总预算，超预算的段标
    `skipped(deadline)` 而不是继续串行等下去；JMA 那段改 HEAD（或流式读
    首块），不再为报一个 KB 数把整张 601×601 JPEG 读进内存。
    另附熔断状态（⑦）与最近错误编号（⑨），让"失败了"可核对。
    """
    import time as _t
    from datetime import datetime, timezone
    t0 = _t.monotonic()
    out = {'checked_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

    def left():
        return 20.0 - (_t.monotonic() - t0)

    if left() > 0:
        try:
            d = om_svc._get(om_svc.FORECAST_URL,
                            {'latitude': 22.27, 'longitude': 113.58,
                             'hourly': 'cloud_cover', 'forecast_days': 1})
            out['open_meteo'] = 'ok' if d.get('hourly') else 'degraded'
        except om_svc.UpstreamLimitError as e:
            # 额度用尽与网络故障是两回事：前者等一会儿/换出口，后者要查网络
            st = om_svc.limit_state()
            out['open_meteo'] = 'limited(%s)%s' % (
                e.scope,
                '，冷却剩余 %ds' % st['remaining'] if st else '')
            out['open_meteo_hint'] = e.user_message
        except Exception as e:
            out['open_meteo'] = 'fail: ' + str(e)[:80]
    else:
        out['open_meteo'] = 'skipped(deadline)'
    if left() > 0:
        try:
            nowutc = datetime.now(timezone.utc)
            slot = nowutc.replace(minute=(nowutc.minute // 10) * 10)
            url = ('https://www.data.jma.go.jp/mscweb/data/himawari/img/fd_/'
                   'fd__b13_' + slot.strftime('%H%M') + '.jpg')
            import requests as _rq
            r = _rq.head(url, timeout=min(10.0, left()), allow_redirects=True)
            if r.status_code != 200:
                out['jma_sat'] = f'http {r.status_code}'
            else:
                clen = (r.headers.get('Content-Length') or '').strip()
                if clen.isdigit():
                    out['jma_sat'] = 'ok(%dKB)' % (int(clen) // 1024)
                else:      # 没给长度：只读首块，绝不把整张图拉进内存
                    with _rq.get(url, timeout=min(10.0, left()),
                                 stream=True) as rg:
                        chunk = next(rg.iter_content(65536), b'')
                    out['jma_sat'] = 'ok(≥%dKB)' % (len(chunk) // 1024)
        except Exception as e:
            out['jma_sat'] = 'fail: ' + str(e)[:80]
    else:
        out['jma_sat'] = 'skipped(deadline)'
    if left() > 0:
        try:
            gt = qw_svc.ground_truth(22.27, 113.58)
            out['qweather'] = 'ok' if gt.get('available') else 'no key / fail'
        except Exception as e:
            out['qweather'] = 'fail: ' + str(e)[:80]
    else:
        out['qweather'] = 'skipped(deadline)'
    try:
        fresh = sat_svc._latest_cached_slot()
        out['ptree_cache'] = (fresh[0].strftime('%H:%M UTC') if fresh
                              else 'empty')
    except Exception as e:
        out['ptree_cache'] = 'fail: ' + str(e)[:80]
    br = om_svc.fail_state()
    if br:
        out['open_meteo_breaker'] = 'open(剩余 %ds，连续失败 %d 次)' % (
            br['remaining'], br['streak'])
    try:      # ⑩：和风是静默降级的重灾区，把调用/失败计数摊出来
        qs = qw_svc.stats()
        out['qweather_calls'] = '%d 次调用 / %d 次失败（缓存 %d）' % (
            qs['calls'], qs['fail'], qs['cached'])
        if qs['fail']:
            out['qweather_last_error'] = qs['last_err']
    except Exception as e:
        out['qweather_calls'] = 'fail: ' + str(e)[:80]
    errs = _recent_errors(3)
    if errs:
        out['recent_errors'] = errs
    out['elapsed_s'] = round(_t.monotonic() - t0, 1)
    return out


class PftBody(BaseModel):
    """`.pft` 机位评估入参。

    days/weather_days 必须有上界：days 会直送 5 分钟步长的全天扫描
    （days×288×2 次天文计算），weather_days 决定逐事件天气请求的条数，
    照单全收等于把线程池和上游额度交给调用方随意支配。
    """
    pft: dict = Field(default_factory=dict)
    days: int = Field(90, ge=1, le=366)
    weather_days: int = Field(10, ge=0, le=16)


@app.post('/api/pft')
def api_pft(body: PftBody):
    """Planit .pft 机位天气评估：
    body = {pft: {…Planit JSON…}, days?: 90（1..366）, weather_days?: 10（0..16）}
    几何/规划归巧摄，本端点只回答天气可行性。"""
    from datetime import datetime, timedelta, timezone
    pft = body.pft
    if not pft:
        raise HTTPException(status_code=400, detail='缺少 pft JSON')
    geo = pft_svc.extract_geometry(pft)
    if not geo.get('lat') or geo.get('bearing') is None:
        raise HTTPException(status_code=400, detail='pft 缺少 map.lat/lng 或 tools.centerBearing')
    days = body.days
    ev = pft_svc.scan_alignment_events(geo['lat'], geo['lon'],
                                       geo['bearing'], geo['aov'],
                                       geo['elevation'], days=days)
    # 天气评估：预报窗口内（≤16 天）的事件
    wlimit = body.weather_days
    for e in ev:
        if e['body'] == 'moon':
            frac, _ = _moon_illum_at(astro_jd(e['utc']))
            e['illum'] = round(frac * 100, 1)
        dt_bjt = e['utc'] + timedelta(hours=8)
        e['time_bjt'] = dt_bjt.strftime('%m-%d %H:%M')
        # e['utc'] 为 aware UTC（scan_alignment_events 用 timezone.utc），
        # 比较基准也必须是 aware，否则 naive/aware 相减 TypeError
        if 0 <= (dt_bjt - datetime.now(timezone.utc)).total_seconds() / 86400.0 <= wlimit:
            w = pft_svc.weather_for_event(geo['lat'], geo['lon'],
                                          dt_bjt.replace(tzinfo=None))
            e['weather'] = w
            if w.get('available'):
                tot = w.get('total', 100)
                e['verdict'] = ('通透可拍' if tot <= 40 else
                                '薄云可拍' if tot <= 70 else '云层遮挡')
        else:
            e['weather'] = None
            e['verdict'] = '超出预报窗口'
    return {'geometry': geo, 'events': ev}


def astro_jd(dt_utc):
    return astro_svc.jd_from_datetime(dt_utc.replace(tzinfo=None))


def _moon_illum_at(jd):
    import math
    _, _, dist, lam_m, _ = astro_svc.moon_position(jd)
    _, _, _, lam_s, _ = astro_svc.sun_ecliptic(jd)
    elong = (lam_m - lam_s) % 360
    return (1 - math.cos(elong * math.pi / 180)) / 2, elong


app.mount('/', StaticFiles(directory=STATIC_DIR, html=True), name='static')


if __name__ == '__main__':
    import uvicorn
    uvicorn.run(app, host='0.0.0.0', port=int(os.environ.get('PORT', 8000)))

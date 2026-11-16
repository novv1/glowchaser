# -*- coding: utf-8 -*-
"""FastAPI 应用冒烟：路由/静态页/GZip 中间件（不触发任何外部网络）。"""
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.testclient import TestClient

from app.main import app
from app import main as app_mod

client = TestClient(app)


def test_cities_endpoint():
    r = client.get('/api/cities')
    assert r.status_code == 200
    cities = r.json()['cities']
    assert len(cities) >= 8
    assert all('lat' in c and 'lon' in c for c in cities)


def test_stats_endpoint():
    assert client.get('/api/stats').status_code == 200


def test_index_served():
    r = client.get('/')
    assert r.status_code == 200
    assert 'GlowChaser' in r.text


def test_frontend_assets_always_revalidate():
    """html/js/css 必须带 no-cache：只有 ETag/Last-Modified 时浏览器会按启发式
    直接吃缓存，改了代码、重启了服务，页面却还是旧前端（顶部报「前端 vX 与后端 vY
    不一致」而用户怎么刷新都没用，因为缓存的旧 index.html 还指向旧 ?v= 的旧 js）。"""
    for path in ('/', '/app.js?v=1', '/style.css?v=1'):
        r = client.get(path)
        assert r.status_code == 200, path
        assert r.headers.get('cache-control') == 'no-cache', path
    # 接口是动态数据，不该被浏览器缓存（应用自己有一层缓存与"回退到缓存"的提示）
    assert client.get('/api/cities').headers.get('cache-control') is None


def test_gzip_middleware_registered():
    assert any(m.cls is GZipMiddleware for m in app.user_middleware)


def test_error_detail_not_leaked():
    """非法日期应返回 422/400 校验错误，而不是内部堆栈。"""
    r = client.get('/api/report?lat=999&lon=113.58')
    assert r.status_code == 422
    assert 'Traceback' not in r.text


def test_internal_error_is_generic(monkeypatch):
    """500 只返回通用文案 + 可核对的短编号，不回异常细节（可能含路径/凭据）。

    v2.37（审查报告 ⑨）：编号把用户看到的失败与服务日志里的堆栈对上，
    否则配额/网络压力下这条兜底路径对双方都是黑盒。
    """
    import re
    from app.services import report as rpt

    def boom(*args, **kwargs):
        raise RuntimeError('内部敏感路径 C:/secret/key.txt')

    monkeypatch.setattr(rpt, 'build_report', boom)
    r = client.get('/api/report?lat=22.27&lon=113.58'
                   '&date=2026-09-10&type=sunset')
    assert r.status_code == 500
    detail = r.json()['detail']
    assert detail.startswith('服务内部错误，请稍后重试')
    assert re.search(r'错误编号 [0-9a-f]{8}', detail)      # 可核对
    assert 'secret' not in r.text
    # 编号也要进进程内最近错误环（/api/health 能看到）
    ref = detail.split('错误编号 ')[1].rstrip('）')
    assert any(e['ref'] == ref and e['stage'] == 'report'
               for e in app_mod._recent_errors(20))


def test_report_result_is_cached_for_repeat_click(monkeypatch):
    """重复点「查询」（同一组参数）不该重算：一次 9 秒 + 300~900 加权额度。

    v2.37 审查报告 ⑬。命中与否要能观测（X-GC-Result-Cache），否则"到底有没有
    省下这一次"永远只能靠猜。
    """
    from app.services import report as rpt
    calls = []

    def fake(*a, **kw):
        calls.append(a)
        return {'probability': 55, 'main_model': 'ECMWF'}

    monkeypatch.setattr(rpt, 'build_report', fake)
    app_mod.result_cache.clear()
    url = ('/api/report?lat=22.27&lon=113.58'
           '&date=2026-10-08&type=sunset')
    r1 = client.get(url)
    r2 = client.get(url)
    assert r1.status_code == 200 and r2.status_code == 200
    assert r1.json() == r2.json()
    assert len(calls) == 1
    assert r1.headers.get('x-gc-result-cache') == 'miss'
    assert r2.headers.get('x-gc-result-cache') == 'hit'


def test_report_cache_key_covers_every_param(monkeypatch):
    """参数不同就是不同的报告：键漏了任何一项都会串味。"""
    from app.services import report as rpt
    calls = []
    monkeypatch.setattr(rpt, 'build_report',
                        lambda *a, **kw: calls.append(a) or {'probability': 55})
    app_mod.result_cache.clear()
    base = '/api/report?lat=22.27&lon=113.58&date=2026-10-08'
    client.get(base + '&type=sunset')
    for extra in ('&type=sunrise', '&azimuth=270', '&tol=3', '&alt_max=12'):
        client.get(base + extra)
    client.get('/api/report?lat=22.28&lon=113.58&date=2026-10-08&type=sunset')
    assert len(calls) == 6


def test_align_result_is_cached_too(monkeypatch):
    from app.services import report as rpt
    calls = []
    monkeypatch.setattr(rpt, 'build_alignment',
                        lambda *a, **kw: calls.append(a) or {'windows': []})
    app_mod.result_cache.clear()
    url = ('/api/align?lat=22.27&lon=113.58&date=2026-10-08'
           '&azimuth=270&aov=3')
    client.get(url)
    r2 = client.get(url)
    assert len(calls) == 1
    assert r2.headers.get('x-gc-result-cache') == 'hit'


def test_degraded_result_uses_short_ttl():
    """降级报告（数据源缺失/天气失败）只能短缓存：否则恢复会被缓存挡住。"""
    assert app_mod._result_ttl({'data_missing': ['forecast']}) == \
        app_mod.RESULT_TTL_DEGRADED
    assert app_mod._result_ttl({'weather_error': 'x'}) == \
        app_mod.RESULT_TTL_DEGRADED
    assert app_mod._result_ttl({'probability': 50}) == app_mod.RESULT_TTL


def test_upstream_limit_is_not_cached(monkeypatch):
    """429 不得进结果缓存：用户等冷却期结束再来一次，必须真的重试。"""
    from app.services import report as rpt
    from app.services.openmeteo import UpstreamLimitError
    calls = []

    def boom(*a, **kw):
        calls.append(a)
        raise UpstreamLimitError('daily', detail='Monthly API request limit')

    monkeypatch.setattr(rpt, 'build_report', boom)
    app_mod.result_cache.clear()
    url = '/api/report?lat=22.27&lon=113.58&date=2026-10-09&type=sunset'
    assert client.get(url).status_code == 429
    assert client.get(url).status_code == 429
    assert len(calls) == 2


def test_geocode_is_cached(monkeypatch):
    """同一关键词的重复搜索不该再打 Photon/Nominatim（v2.37 审查报告 ⑮）。"""
    import requests
    calls = []

    class _R:
        status_code = 200

        def json(self):
            return {'features': [{
                'properties': {'name': '测试地标'},
                'geometry': {'coordinates': [113.6, 22.3]}}]}

    def fake_get(*a, **kw):
        calls.append(a)
        return _R()

    monkeypatch.setattr(requests, 'get', fake_get)
    app_mod.geocode_cache.clear()
    r1 = client.get('/api/geocode?q=zzqq测试地标')
    r2 = client.get('/api/geocode?q=zzqq测试地标')
    assert r1.json()['results'] == r2.json()['results']
    assert len(calls) == 1
    assert r2.headers.get('x-gc-result-cache') == 'hit'


def test_geocode_all_sources_down_is_not_not_found(monkeypatch):
    """「搜索服务不可用」与「没找到这个地方」是两回事，不能都回 none。"""
    import requests

    def boom(*a, **kw):
        raise OSError('dns fail')

    monkeypatch.setattr(requests, 'get', boom)
    app_mod.geocode_cache.clear()
    j = client.get('/api/geocode?q=zzqq地标xyz').json()
    assert j['results'] == []
    assert j['source'] == 'unavailable'      # 旧实现只能回 'none'
    assert j.get('message')


def test_geocode_not_found_stays_none(monkeypatch):
    import requests

    class _R:
        status_code = 200

        def json(self):
            return {'features': []}

    monkeypatch.setattr(requests, 'get', lambda *a, **kw: _R())
    app_mod.geocode_cache.clear()
    j = client.get('/api/geocode?q=zzqq没有这个地标').json()
    assert j['results'] == [] and j['source'] == 'none'
    assert not j.get('message')


def test_geocode_throttle_serialises_upstream(monkeypatch):
    """Nominatim 使用条款要求 ≤1 请求/秒：出站地理检索必须串行。"""
    import requests

    class _R:
        status_code = 200

        def json(self):
            return {'features': []}

    seen = []
    monkeypatch.setattr(requests, 'get',
                        lambda *a, **kw: seen.append(app_mod._time.time()) or _R())
    monkeypatch.setenv('GC_GEOCODE_MIN_INTERVAL', '0.12')
    app_mod.geocode_cache.clear()
    app_mod._geo_last[0] = 0.0
    client.get('/api/geocode?q=zzqq第一条')
    client.get('/api/geocode?q=zzqq第二条')
    assert len(seen) >= 2
    assert seen[1] - seen[0] >= 0.10


def test_upstream_limit_is_429_not_500(monkeypatch):
    """额度用尽不是本服务内部错误：必须回 429 + 可读原因，
    前端据此走「显示缓存 + 说清原因」，而不是弹一张泛泛的失败卡。"""
    from app.services import report as rpt
    from app.services.openmeteo import UpstreamLimitError

    def boom(*args, **kwargs):
        raise UpstreamLimitError('daily', detail='Daily API request limit exceeded')

    monkeypatch.setattr(rpt, 'build_report', boom)
    r = client.get('/api/report?lat=22.27&lon=113.58'
                   '&date=2026-09-14&type=sunset')
    assert r.status_code == 429
    j = r.json()
    assert j['source'] == 'open-meteo' and j['scope'] == 'daily'
    assert 'Open-Meteo' in j['message'] and 'IP' in j['message']


def test_pft_endpoint_handles_aware_utc(monkeypatch):
    """回归：scan_alignment_events 返回 aware UTC 后，api_pft 的比较基准
    也必须是 aware，否则 naive/aware 相减 TypeError -> 500。"""
    from datetime import datetime, timedelta, timezone
    from app.services import pft as pft_svc

    ev = [{'body': 'sun', 'az': 270.0, 'alt': 3.0, 'daz': 0.1,
           'utc': datetime.now(timezone.utc) + timedelta(days=1)}]
    monkeypatch.setattr(pft_svc, 'scan_alignment_events', lambda *a, **k: ev)
    monkeypatch.setattr(pft_svc, 'weather_for_event',
                        lambda *a, **k: {'available': False})
    body = {'pft': {'map': {'lat': 22.27, 'lng': 113.58},
                    'tools': {'centerBearing': 270,
                              'horizontalAngleOfView': 3,
                              'centerElevation': 2}}}
    r = client.post('/api/pft', json=body)
    assert r.status_code == 200
    assert r.json()['events'][0]['time_bjt']


def test_geocode_local_hits_are_offline():
    """本地城市/地标命中时不发起任何外部请求（source=local）"""
    r = client.get('/api/geocode?q=珠海')
    assert r.status_code == 200
    d = r.json()
    assert d['source'] == 'local'
    assert any('珠海' in x['name'] for x in d['results'])
    for x in d['results']:
        assert -90 <= x['lat'] <= 90 and -180 <= x['lon'] <= 180


def test_geocode_dedupes_near_identical_points():
    """同一地点返回的多条近似坐标（塔/车站/路口）应合并为一条"""
    from app.main import _dedupe_places
    items = [
        {'name': '广州塔', 'lat': 23.1088, 'lon': 113.3180},
        {'name': '广州塔', 'lat': 23.1090, 'lon': 113.3191},
        {'name': '广州塔', 'lat': 23.1090, 'lon': 113.3180},
        {'name': '广州塔', 'lat': 23.0456, 'lon': 113.3605},   # 明显不同点
    ]
    out = _dedupe_places(items)
    assert len(out) == 2
    assert out[0]['lat'] == 23.1088


def test_geocode_dedupe_drops_missing_coords():
    from app.main import _dedupe_places
    out = _dedupe_places([{'name': 'x'}, {'name': 'y', 'lat': None, 'lon': 1},
                          {'name': 'z', 'lat': 22.0, 'lon': 113.0}])
    assert len(out) == 1 and out[0]['name'] == 'z'


def test_geocode_rejects_empty_query():
    assert client.get('/api/geocode?q=').status_code == 422


def test_local_place_ranking_prefers_prefix():
    """本地地名模糊匹配：名称前缀命中优先于地区命中"""
    from app.main import _rank_local_places
    hits = _rank_local_places(['珠'])
    assert hits, '应命中珠三角城市'
    # 名称以"珠"开头的（珠海）应排在地区含"珠三角"的（深圳/广州）之前
    assert hits[0]['name'].startswith('珠海')
    names = [h['name'] for h in hits]
    assert any('深圳' in n for n in names)


def test_local_place_multi_token_requires_all():
    """空格分隔的多关键字须全部命中；地标（情侣路）也能被关键字找到"""
    from app.main import _rank_local_places
    assert _rank_local_places(['珠海', '渔女']) == []      # 库中无该地标
    got = _rank_local_places(['珠海', '情侣路'])
    assert got and '情侣路' in got[0]['name']
    # "深圳 湾" -> 深圳湾地标
    got2 = _rank_local_places(['深圳', '湾'])
    assert got2 and '深圳湾' in got2[0]['name']
    # 顺序无关
    assert _rank_local_places(['湾', '深圳'])[0]['name'] == got2[0]['name']


def test_geocode_multi_token_local_hit_offline():
    r = client.get('/api/geocode?q=深圳 湾')
    d = r.json()
    assert d['source'] == 'local'
    assert any('深圳湾' in x['name'] for x in d['results'])


def test_geocode_limit_bounds():
    assert client.get('/api/geocode?q=珠&limit=0').status_code == 422
    assert client.get('/api/geocode?q=珠&limit=99').status_code == 422


PFT_MIN = {
    'map': {'lat': 22.27, 'lng': 113.58},
    'tools': {'centerBearing': 270, 'horizontalAngleOfView': 3.0,
              'centerElevation': 0.0},
    'ephemeris': {'timeZoneID': 'Asia/Shanghai'},
}


def test_pft_scan_params_bounded(monkeypatch):
    """days 会直送 5 分钟步长的全天扫描（days*288*2 次天文计算），必须拒绝而非照单全收。"""
    from app.services import pft as pft_svc
    seen = {}

    def fake_scan(lat, lon, bearing, aov, elev, days=90,
                  body_list=('moon', 'sun')):
        seen['days'] = days
        return []

    monkeypatch.setattr(pft_svc, 'scan_alignment_events', fake_scan)
    assert client.post('/api/pft',
                       json={'pft': PFT_MIN, 'days': 10 ** 6}).status_code == 422
    assert client.post('/api/pft', json={'pft': PFT_MIN, 'days': 0}).status_code == 422
    assert client.post('/api/pft',
                       json={'pft': PFT_MIN, 'days': 'abc'}).status_code == 422
    assert client.post('/api/pft',
                       json={'pft': PFT_MIN, 'weather_days': 999}).status_code == 422
    r = client.post('/api/pft', json={'pft': PFT_MIN, 'days': 30, 'weather_days': 3})
    assert r.status_code == 200
    assert seen['days'] == 30

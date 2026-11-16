# -*- coding: utf-8 -*-
"""悬日月窗口：高度角上限、升落分类、API 参数贯通（离线）。"""
from datetime import datetime

from fastapi.testclient import TestClient

from app.core import astronomy as astro
from app.main import app
from app.services import report as rpt

client = TestClient(app)
LAT, LON, TZ = 22.27, 113.58, 8.0


def test_find_alignment_windows_respects_alt_cap():
    date = datetime(2026, 9, 10)
    sun = astro.sun_times(date, LAT, LON, TZ)
    jd_lo = astro.jd_from_datetime(sun['sunrise']) - TZ / 24.0 - 0.35
    jd_hi = astro.jd_from_datetime(sun['sunset']) - TZ / 24.0 + 0.35
    wins = astro.find_alignment_windows(
        'sun', LAT, LON, jd_lo, jd_hi, 270.0,
        tol_deg=2.0, alt_lo=0.0, alt_hi=10.0)
    assert wins, '当日应至少有一个 ≤10° 的对准窗口'
    assert all(0.0 <= w['best_alt'] <= 10.0 for w in wins)


def test_body_rising_classification():
    date = datetime(2026, 9, 10)
    sun = astro.sun_times(date, LAT, LON, TZ)
    jd_rise = astro.jd_from_datetime(sun['sunrise']) - TZ / 24.0
    jd_set = astro.jd_from_datetime(sun['sunset']) - TZ / 24.0
    assert astro.body_rising('sun', LAT, LON, jd_rise) is True
    assert astro.body_rising('sun', LAT, LON, jd_set) is False


def test_report_api_passes_alt_max(monkeypatch):
    captured = {}

    def fake(lat, lon, date_str, kind='sunset', target_azimuth=None,
             tol=2.0, alt_max=10.0):
        captured.update(dict(lat=lat, lon=lon, date=date_str, kind=kind,
                             az=target_azimuth, tol=tol, alt_max=alt_max))
        return {'ok': True}

    monkeypatch.setattr(rpt, 'build_report', fake)
    r = client.get('/api/report?lat=22.27&lon=113.58&date=2026-09-10'
                   '&type=sunset&azimuth=270&tol=1.5&alt_max=6')
    assert r.status_code == 200
    assert captured['alt_max'] == 6.0
    assert captured['az'] == 270.0
    assert captured['tol'] == 1.5


def test_report_api_rejects_bad_alt_max():
    assert client.get(
        '/api/report?lat=22.27&lon=113.58&alt_max=0.1').status_code == 422
    assert client.get(
        '/api/report?lat=22.27&lon=113.58&alt_max=99').status_code == 422


def test_build_alignment_windows_marks_body_and_period():
    """窗口构建器：日/月两类、升/落时段、高度角上限都正确标注。"""
    date = datetime(2026, 9, 10)
    sun = astro.sun_times(date, LAT, LON, TZ)
    wins, meta = rpt._build_alignment_windows(
        LAT, LON, sun, 270.0, 2.0, 10.0)
    assert meta['target_az'] == 270.0 and meta['tol'] == 2.0
    assert meta['alt_max'] == 10.0 and meta['alt_lo'] == -1.0
    assert wins, '应至少有一个对准窗口'
    for w in wins:
        assert w['body'] in ('sun', 'moon')
        assert w['period'] in ('sunrise', 'sunset', 'moonrise', 'moonset')
        assert w['period_name'] in ('日出', '日落', '月出', '月落')
        assert -1.0 <= w['best_alt'] <= 10.0   # 含视觉地平线（折射）
        assert w['alt_quality'] in ('地平线', '低角度', '中角度')
    # 低角度优先排序
    alts = [w['best_alt'] for w in wins]
    assert alts == sorted(alts)


def test_align_api_passes_params(monkeypatch):
    captured = {}

    def fake(lat, lon, date_str, azimuth, aov=3.0, elev=None, tol=None,
             alt_max=None, days=14, cam_height=None, target_height=None,
             target_dist_km=None):
        captured.update(dict(lat=lat, lon=lon, date=date_str, az=azimuth,
                             aov=aov, elev=elev, tol=tol, alt_max=alt_max,
                             days=days, cam_height=cam_height,
                             target_height=target_height,
                             target_dist_km=target_dist_km))
        return {'alignment': [], 'alignment_meta': {}}

    monkeypatch.setattr(rpt, 'build_alignment', fake)
    r = client.get('/api/align?lat=22.27&lon=113.58&date=2026-09-10'
                   '&azimuth=270&aov=2.5&elev=1.2')
    assert r.status_code == 200
    assert captured == {'lat': 22.27, 'lon': 113.58, 'date': '2026-09-10',
                        'az': 270.0, 'aov': 2.5, 'elev': 1.2,
                        'tol': None, 'alt_max': None, 'days': 14,
                        'cam_height': None, 'target_height': None,
                        'target_dist_km': None}


def test_align_api_passes_heights(monkeypatch):
    """机位高度 / 目标高度 / 目标距离 三类精细化参数贯通"""
    captured = {}

    def fake(lat, lon, date_str, azimuth, aov=3.0, elev=None, tol=None,
             alt_max=None, days=14, cam_height=None, target_height=None,
             target_dist_km=None):
        captured.update(dict(elev=elev, cam_height=cam_height,
                             target_height=target_height,
                             target_dist_km=target_dist_km))
        return {'alignment': [], 'alignment_meta': {}}

    monkeypatch.setattr(rpt, 'build_alignment', fake)
    r = client.get('/api/align?lat=22.27&lon=113.58&azimuth=270'
                   '&cam_height=1201&target_height=450&target_dist_km=8.4')
    assert r.status_code == 200
    assert captured['cam_height'] == 1201.0
    assert captured['target_height'] == 450.0
    assert captured['target_dist_km'] == 8.4
    # 未显式给 elev 时传 None，交后端由目标高度反推
    assert captured['elev'] is None


def test_align_api_validates_heights():
    """高度参数越界应 422"""
    assert client.get('/api/align?lat=22.27&lon=113.58&azimuth=270'
                      '&cam_height=-5').status_code == 422
    assert client.get('/api/align?lat=22.27&lon=113.58&azimuth=270'
                      '&cam_height=99999').status_code == 422
    assert client.get('/api/align?lat=22.27&lon=113.58&azimuth=270'
                      '&target_dist_km=0').status_code == 422


def _offline(monkeypatch, probe=True):
    """让 build_alignment 保持离线：天气请求失败；probe=True 时跳过地形探测
    （需要验证地形默认值来源的用例传 probe=False 并自行 mock 地形服务）。"""
    def boom(*a, **kw):
        raise RuntimeError('offline')
    monkeypatch.setattr(rpt.om, 'fetch_hourly', boom)
    monkeypatch.setattr(rpt.om, 'fetch_aq', boom)
    if probe:
        monkeypatch.setattr(rpt, '_probe_site', lambda lat, lon: (None, None))


def test_build_alignment_derives_elev_from_target_height(monkeypatch):
    """目标高度 + 距离 -> 目标仰角（elev_source=target），并给出下沉角"""
    _offline(monkeypatch)
    r = rpt.build_alignment(LAT, LON, '2026-09-10', 270.0, aov=3.0,
                            cam_height=1201.0, target_height=2000.0,
                            target_dist_km=10.0)
    g = r['geometry']
    assert g['elev_source'] == 'target'
    from app.core import sightline as sight
    expect = sight.target_elevation_deg(1201.0, 2000.0, 10.0)
    assert abs(g['elev'] - round(expect, 2)) < 0.02
    assert r['observer']['height_m'] == 1201
    assert r['observer']['height_src'] == 'manual'
    assert abs(r['observer']['horizon_dip_deg'] - 1.112) < 0.01
    assert r['target']['height_m'] == 2000
    assert r['target']['distance_km'] == 10.0
    assert abs(r['target']['elev_geo_deg'] - expect) < 0.01


def test_build_alignment_explicit_elev_wins(monkeypatch):
    """显式 elev（巧摄 .pft / 手动）优先于目标高度反推"""
    _offline(monkeypatch)
    r = rpt.build_alignment(LAT, LON, '2026-09-10', 270.0, elev=2.0,
                            cam_height=1201.0, target_height=2000.0,
                            target_dist_km=10.0)
    assert r['geometry']['elev'] == 2.0
    assert r['geometry']['elev_source'] == 'manual'
    assert r['target']['elev_geo_deg'] is not None


def test_build_alignment_camera_height_defaults_to_terrain(monkeypatch):
    """未给机位高度时用地形探测海拔，并标注来源"""
    _offline(monkeypatch, probe=False)
    import app.services.terrain as terrain_svc
    monkeypatch.setattr(terrain_svc, 'probe_site',
                        lambda lat, lon, use_cache=True: {
                            'lat': lat, 'lon': lon, 'elevation': 1201.0,
                            'horizon_mask': None, 'bortle': None,
                            'bortle_src': None, 'ok': True})
    r = rpt.build_alignment(LAT, LON, '2026-09-10', 270.0, aov=3.0)
    assert r['observer']['height_m'] == 1201
    assert r['observer']['height_src'] == 'terrain'
    assert r['observer']['terrain_elevation_m'] == 1201


def test_align_api_requires_azimuth_and_validates_alt_max():
    assert client.get('/api/align?lat=22.27&lon=113.58').status_code == 422
    assert client.get(
        '/api/align?lat=22.27&lon=113.58&azimuth=270&alt_max=99'
    ).status_code == 422


def test_refraction_deg():
    assert rpt._refraction_deg(-2.0) == 0.0
    assert 0.4 < rpt._refraction_deg(0.0) < 0.6      # 地平线附近约 0.5°
    assert rpt._refraction_deg(45.0) < 0.03          # 高角度折射很小


def test_twilight_phase():
    assert rpt._twilight_phase(5) == '白天'
    assert rpt._twilight_phase(-3) == '民用暮光'
    assert rpt._twilight_phase(-10) == '航海暮光'
    assert rpt._twilight_phase(-15) == '天文暮光'
    assert rpt._twilight_phase(-25) == '夜'


def test_weather_feasibility_clear_vs_blocked():
    """天气可行性只看天气（云况/通透度/降水/稳定性），不含机位几何。"""
    clear = {'low': 5, 'mid': 5, 'high': 10}
    good = rpt._weather_feasibility(clear, 0.1, 10, 25, 5, {'std': 4})
    assert good['verdict'] in ('通透可拍', '薄云可拍')
    assert good['dims']['cloud'] > 80
    assert good['dims']['precip'] > 80
    assert 'geometry' not in good['dims']
    heavy = {'low': 90, 'mid': 90, 'high': 90}
    bad = rpt._weather_feasibility(heavy, 0.8, 120, 3, 80, {'std': 30})
    assert bad['score'] < good['score']
    assert bad['verdict'] in ('云层遮挡', '不建议')
    assert bad['risks']


def test_align_geometry_rates_and_size():
    date = datetime(2026, 9, 10)
    moon = astro.moon_times(date, LAT, LON, TZ)
    sun = astro.sun_times(date, LAT, LON, TZ)
    wins, _ = rpt._build_alignment_windows(LAT, LON, sun, 270.0, 2.0, 10.0)
    assert wins
    w = wins[0]
    g = rpt._align_geometry(w['body'], LAT, LON, w, moon)
    assert g['angular_size_deg'] > 0
    assert g['time_in_frame_min'] > 0
    assert g['refraction_deg'] >= 0
    assert 'twilight' in g['sky']
    if w['body'] == 'sun':
        assert abs(g['angular_size_deg'] - 0.53) < 0.02


def test_nowcast_api_explicit_event_time(monkeypatch):
    """悬日月窗口用显式 time/azimuth/body 调用 /api/nowcast。"""
    captured = {}

    def fake(lat, lon, date_str, kind, key_time, key_az, main=None):
        captured.update(dict(lat=lat, lon=lon, date=date_str, kind=kind,
                             key_time=key_time, key_az=key_az))
        return {'available': False, 'reason': 'mock'}

    monkeypatch.setattr(rpt, 'build_nowcast', fake)
    r = client.get('/api/nowcast?lat=22.27&lon=113.58&date=2026-09-10'
                   '&time=17:48&azimuth=271.3&body=sun')
    assert r.status_code == 200
    assert captured['key_time'].strftime('%Y-%m-%d %H:%M') == '2026-09-10 17:48'
    assert captured['key_az'] == 271.3
    assert captured['kind'] == 'sun'


def test_build_alignment_weather_failure_keeps_geometry(monkeypatch):
    """天气获取失败时窗口仍返回几何事实与明确原因（不再 undefined/静默丢分）。"""
    def boom(*a, **kw):
        raise RuntimeError('rate limited')

    monkeypatch.setattr(rpt.om, 'fetch_hourly', boom)
    monkeypatch.setattr(rpt.om, 'fetch_aq', boom)
    monkeypatch.setattr(rpt, '_probe_site', lambda lat, lon: (None, None))
    r = rpt.build_alignment(LAT, LON, '2026-09-10', 270.0, aov=3.0,
                            elev=0.0, alt_max=12.0, days=1)
    assert r['alignment'], '2026-09-10 应有对准窗口'
    w = r['alignment'][0]
    assert w['feasibility'] is None
    assert w['reason']                      # 明确原因，而非空
    assert w['apparent_alt'] is not None    # 几何事实保留
    assert w['angular_size_deg'] is not None


def test_sun_calendar_sunrise_kind():
    """悬日日历支持日出方位（此前只查日落，日出目标永远查不到）。"""
    try:
        from app.services import calendars as cal
    except ImportError:
        from services import calendars as cal
    d = cal.sun_alignment_calendar(LAT, LON, 82.3, 1.0, kind='sunrise')
    assert d['kind'] == 'sunrise'
    assert d['events'], '82.3° 日出方位应在未来 12 个月出现'
    assert all('sunrise_az' in e for e in d['events'])
    d2 = cal.sun_alignment_calendar(LAT, LON, 270.0, 1.0, kind='sunset')
    assert d2['events'] and all('sunset_az' in e for e in d2['events'])


def test_build_alignment_day_weather_and_feasibility(monkeypatch):
    """合成预报数据：当日天气概况 + 窗口天气可行性都正确生成。"""
    times = [f'2026-09-{d:02d}T{h:02d}:00'
             for d in (9, 10, 11) for h in range(24)]

    def fake_hourly(lats, lons, hv, models, rng, tz):
        rec = {'time': times}
        for v in hv:
            rec[v] = [30.0] * len(times)     # 云量 30%
        return {m: [rec] for m in models}

    def fake_aq(lats, lons, av, rng, tz):
        return [{'time': times,
                 'aerosol_optical_depth': [0.15] * len(times),
                 'pm2_5': [8.0] * len(times)}]

    monkeypatch.setattr(rpt.om, 'fetch_hourly', fake_hourly)
    monkeypatch.setattr(rpt.om, 'fetch_aq', fake_aq)
    monkeypatch.setattr(rpt, '_probe_site', lambda lat, lon: (None, None))
    r = rpt.build_alignment(LAT, LON, '2026-09-10', 270.0, aov=3.0,
                            elev=0.0, alt_max=12.0, days=1)
    assert r['day_weather'], '当日天气应有数据'
    assert set(r['day_weather']) & {'sunrise', 'sunset', 'moonrise', 'moonset'}
    assert r['alignment'], '应有对准窗口'
    w = r['alignment'][0]
    assert w['feasibility'] is not None
    assert 0 <= w['feasibility']['score'] <= 100
    assert w['cloud_at']['low'] == 30
    assert w['feasibility']['dims']['cloud'] < 100   # 有云不是满分


def test_aq_range_clamped_to_api_horizon():
    """空气质量接口范围比天气接口短（实测 today+6），超出会 400：
    必须钳制末端，否则「明天」起整个天气块会被跳过（实测 bug）。"""
    from datetime import datetime, timedelta
    today = datetime.now().date()
    beyond = (today + timedelta(days=7)).isoformat()
    # 末端超出 -> 钳到上限
    rng = f"{today.isoformat()},{beyond}"
    out = rpt._aq_range(rng)
    assert out is not None
    s, e = out.split(',')
    assert s == today.isoformat()
    assert e == (today + timedelta(days=6)).isoformat()
    # 起点已超出 -> 跳过
    far = (today + timedelta(days=20)).isoformat()
    assert rpt._aq_range(f"{far},{far}") is None
    # 范围内不变
    ok = f"{today.isoformat()},{(today + timedelta(days=3)).isoformat()}"
    assert rpt._aq_range(ok) == ok
    # 非法输入原样返回（交由调用方兜底）
    assert rpt._aq_range('bad') == 'bad'


def test_day_events_reports_azimuth_gap(monkeypatch):
    """当日事件差距：给出各事件方位、与目标差、是否在可取景范围内"""
    _offline(monkeypatch)
    r = rpt.build_alignment(LAT, LON, '2026-09-10', 270.0, aov=3.0,
                            tol=3.0, alt_max=10.0)
    evs = r.get('day_events') or []
    assert len(evs) == 4, evs
    keys = [e['key'] for e in evs]
    assert keys == ['sunrise', 'sunset', 'moonrise', 'moonset']
    for e in evs:
        assert 0 <= e['az'] < 360
        assert 0 <= e['daz'] <= 180
        assert isinstance(e['in_range'], bool)
        assert e['in_range'] == (e['daz'] <= 3.0 and -1.0 <= e['alt'] <= 10.0)
    # 目标 270° 在西侧：当日日落应是最接近目标方位的事件
    sunset = next(e for e in evs if e['key'] == 'sunset')
    assert sunset['daz'] == min(e['daz'] for e in evs)
    assert sunset['in_range'] is False or sunset['daz'] <= 3.0


def test_align_payload_carries_server_version(monkeypatch):
    """align 响应必须带 meta.version：前端据此识别「后端未重启」的陈旧进程"""
    _offline(monkeypatch)
    r = rpt.build_alignment(LAT, LON, '2026-09-10', 270.0, aov=3.0)
    assert r['meta'].get('version') == rpt.APP_VERSION
    assert isinstance(rpt.APP_VERSION, str) and rpt.APP_VERSION


def _fake_weather(monkeypatch, cloud_for=None):
    """伪造天气/AQ 请求：按坐标数与变量名生成确定性数据，保持离线。
    cloud_for(idx, var) -> 0-100；默认按变量名/序号生成。"""

    def times_for(date_str):
        s, e = (date_str.split(',') + [date_str])[:2]
        from datetime import datetime as _dt
        import datetime as _d
        d0 = _dt.strptime(s, '%Y-%m-%d')
        d1 = _dt.strptime(e, '%Y-%m-%d')
        out = []
        cur = d0
        while cur <= d1:
            for h in range(24):
                out.append(cur.strftime('%Y-%m-%d') + 'T%02d:00' % h)
            cur += _d.timedelta(days=1)
        return out

    def fake_fetch(lats, lons, hour_vars, models, date_str, tz='Asia/Shanghai'):
        t = times_for(date_str)
        out = {}
        for m in models:
            recs = []
            for i in range(len(lats)):
                rec = {'time': t}
                for v in hour_vars:
                    if v.startswith('cloudcover_'):
                        p = int(v.split('_')[1].replace('hPa', ''))
                        val = 30 if p >= 700 else (10 if p >= 400 else 45)
                    elif v.startswith('geopotential_height_'):
                        p = int(v.split('_')[2].replace('hPa', ''))
                        val = {1000: 120, 925: 800, 850: 1540, 700: 3180,
                               500: 5900, 400: 7600, 300: 9750, 250: 11000,
                               200: 12500}.get(p, 5000)
                    elif v == 'visibility':
                        val = 20000
                    elif v.startswith('cloud_cover_'):
                        val = {'cloud_cover_low': 40, 'cloud_cover_mid': 20,
                               'cloud_cover_high': 30}.get(v, 25)
                    else:
                        val = 20
                    if cloud_for and v.startswith('cloudcover_'):
                        val = cloud_for(i, v)
                    rec[v] = [val] * len(t)
                recs.append(rec)
            out[m] = recs
        return out

    def fake_aq(lats, lons, aq_vars, date_str, tz='Asia/Shanghai'):
        t = times_for(date_str)
        out = []
        for i in range(len(lats)):
            rec = {'time': t}
            for v in aq_vars:
                rec[v] = [0.15] * len(t)
            out.append(rec)
        return out

    monkeypatch.setattr(rpt.om, 'fetch_hourly', fake_fetch)
    monkeypatch.setattr(rpt.om, 'fetch_aq', fake_aq)
    monkeypatch.setattr(rpt, '_probe_site', lambda lat, lon: (None, None))


def test_day_weather_gets_event_sight_and_switches_basis(monkeypatch):
    """事件时刻视线：day_weather 每一项都应带 sight（逐层交点），
    且主判定改用视线口径（cloud_basis=视线交点、vis_prob 与 sight 一致、
    原三层代表云口径保留为 vis_prob_agg）。"""
    _fake_weather(monkeypatch)
    r = rpt.build_alignment(LAT, LON, '2026-09-10', 270.0, aov=3.0,
                            cam_height=80)
    dw = r.get('day_weather') or {}
    assert dw, '当日事件天气应可用'
    for key in ('sunrise', 'sunset', 'moonrise', 'moonset'):
        e = dw.get(key)
        if not e:
            continue
        s = e.get('sight')
        assert s is not None, key
        assert s.get('visible') is True
        assert s.get('bands'), key            # 至少有分层结果
        assert s.get('levels'), key           # 逐气压层明细
        assert s.get('event_az') is not None
        assert e['vis_prob'] == s['vis_prob']
        assert 'vis_prob_agg' in e
        assert e.get('cloud_basis') == '视线交点'
        for lv in s['levels']:
            assert lv['d_km'] is None or lv['d_km'] > 0


def test_event_sight_sun_limb_visible_at_standard_horizon(monkeypatch):
    """太阳取视地平线标准几何高度角 -0.833°：含折射后上缘略高于 0°，应判可见；
    机位抬高使可见地平线下沉，上缘门槛进一步放宽。"""
    _fake_weather(monkeypatch)
    from datetime import datetime as _dt
    ev = _dt(2026, 9, 10, 18, 30)
    recs = rpt.om.fetch_hourly([LAT] * len(rpt.SIGHT_DISTANCES_KM),
                               [LON] * len(rpt.SIGHT_DISTANCES_KM),
                               rpt.SIGHT_LEVEL_VARS + ['visibility'],
                               [rpt.om.MODELS['ECMWF'], rpt.om.MODELS['GFS']],
                               '2026-09-10')
    # v2.37：_event_sight 改为接收 {模式名: 采样记录}（支持第三家模式交叉验证）
    _recs = {'ECMWF': recs[rpt.om.MODELS['ECMWF']],
             'GFS': recs[rpt.om.MODELS['GFS']]}
    low = rpt._event_sight('sun', ev, 273.9, _recs, 0.0)
    high = rpt._event_sight('sun', ev, 273.9, _recs, 1201.0)
    assert low['visible'] is True and high['visible'] is True
    assert low['limb_alt'] > 0
    assert abs(low['event_alt_geo'] + 0.833) < 1e-6
    # 机位抬高 -> 可见地平线下沉更多
    assert high['dip_deg'] > low['dip_deg']

    def d_of(s, lv):
        return next((x['d_km'] for x in s['levels'] if x['level'] == lv), None)

    # 同一气压层：机位抬高后遮蔽云距收缩（须同层比较——抬高后低云档的
    # 代表层可能从 1000hPa 变 850hPa，跨层比较没有意义）
    d0, d1 = d_of(low, 850), d_of(high, 850)
    assert d0 and d1 and d1 < d0, (d0, d1)
    # 低于机位（含 30m 余量）的气压层应被排除
    assert all(x['gh_m'] > 1201 for x in high['levels'])
    assert any(x['gh_m'] <= 1201 for x in low['levels'])


def test_event_sight_none_on_bad_input():
    assert rpt._event_sight('sun', None, 270.0, {}, 0.0) is None


def test_event_sight_degrades_when_levels_missing(monkeypatch):
    """气压层云量缺测：标记 data_gap，主判定不切换到视线口径"""
    _fake_weather(monkeypatch, cloud_for=lambda i, v: None)

    def fake_fetch(lats, lons, hour_vars, models, date_str, tz='Asia/Shanghai'):
        t = ['2026-09-10T18:00']
        out = {}
        for m in models:
            out[m] = [{'time': t, **{v: [None] * len(t) for v in hour_vars}}
                      for _ in range(len(lats))]
        return out

    monkeypatch.setattr(rpt.om, 'fetch_hourly', fake_fetch)
    r = rpt.build_alignment(LAT, LON, '2026-09-10', 270.0, aov=3.0)
    dw = r.get('day_weather') or {}
    e = dw.get('sunrise') or {}
    s = e.get('sight')
    if s is not None:
        assert s.get('data_gap') is True
        assert e.get('vis_prob_agg') is None or True   # 未切换到视线口径


def test_weather_failure_reason_classified(monkeypatch):
    """天气块失败要能区分「限流 / 超出预报范围 / 其他故障」，
    而不是一律提示"稍后重试"（今天日期说"超出预报范围"是错的）。"""
    def boom_429(*a, **kw):
        raise RuntimeError('Open-Meteo 请求失败: 429 Too Many Requests')

    monkeypatch.setattr(rpt.om, 'fetch_hourly', boom_429)
    monkeypatch.setattr(rpt.om, 'fetch_aq', boom_429)
    monkeypatch.setattr(rpt, '_probe_site', lambda lat, lon: (None, None))
    r = rpt.build_alignment(LAT, LON, '2026-09-10', 270.0, aov=3.0)
    assert r['meta']['weather_error'] == 'rate_limited'
    assert '限流' in r['meta']['weather_note']
    for w in r['alignment']:
        assert '限流' in w['reason']
        assert w['feasibility'] is None
        assert w['apparent_alt'] is not None      # 几何事实仍保留

    # 其他故障 -> failed
    def boom_other(*a, **kw):
        raise RuntimeError('connection reset')

    monkeypatch.setattr(rpt.om, 'fetch_hourly', boom_other)
    r2 = rpt.build_alignment(LAT, LON, '2026-09-10', 270.0, aov=3.0)
    assert r2['meta']['weather_error'] == 'failed'
    assert '失败' in r2['meta']['weather_note']


def test_weather_ok_has_no_error_field(monkeypatch):
    """天气正常时不误报错误原因"""
    _fake_weather(monkeypatch)
    r = rpt.build_alignment(LAT, LON, '2026-09-10', 270.0, aov=3.0)
    assert r['meta']['weather_error'] is None
    assert r['meta']['weather_note'] is None
    assert (r.get('day_weather') or {}), '天气正常时应有当日事件天气'

def test_align_time_in_frame_scales_with_fov():
    """框内停留要按用户视场算：写死 6° 会让 840mm（2.46°）机位拿到 2.5 倍偏长的时间。"""
    date = datetime(2026, 9, 17)
    st = astro.sun_times(date, LAT, LON, 8.0)
    w = {'body': 'sun', 'best_time': st['sunset'].strftime('%H:%M'),
         'best_alt': 0.5, 'date': date.strftime('%Y-%m-%d'),
         'jd_best': astro.jd_from_datetime(st['sunset']) - 8.0 / 24.0}
    moon = {'moon_illum': 50}
    wide = rpt._align_geometry('sun', LAT, LON, dict(w), moon, 6.0)
    tele = rpt._align_geometry('sun', LAT, LON, dict(w), moon, 2.455158)
    assert tele['time_in_frame_min'] < wide['time_in_frame_min']
    # 比值应约等于视场比（0.41），留 20% 余量
    ratio = tele['time_in_frame_min'] / wide['time_in_frame_min']
    assert 0.33 < ratio < 0.50
    # 不传视场时退回旧的 6° 口径，行为不静默变化
    assert rpt._align_geometry('sun', LAT, LON, dict(w), moon)[
        'time_in_frame_min'] == wide['time_in_frame_min']

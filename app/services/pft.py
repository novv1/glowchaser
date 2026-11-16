# -*- coding: utf-8 -*-
"""Planit .pft 机位天气评估
pft 是 Planit 巧摄导出的 JSON：提取几何（位置/方位/视场角/目标仰角），
扫描未来 N 天中日/月通过该取景框的时刻，并对预报窗口内的事件做天气可行性判定。
几何/规划归巧摄，本模块只回答"那一天那一刻天气行不行"。
"""
import math
from datetime import datetime, timedelta, timezone

D2R = math.pi / 180.0


def extract_geometry(pft: dict) -> dict:
    m = pft.get('map', {})
    t = pft.get('tools', {})
    return {
        'lat': m.get('lat'),
        'lon': m.get('lng'),
        'bearing': t.get('centerBearing'),
        'aov': t.get('horizontalAngleOfView', 3.0),
        'elevation': t.get('centerElevation', 0.0),
        'tz': (pft.get('ephemeris', {}) or {}).get('timeZoneID', 'Asia/Shanghai'),
    }


def scan_alignment_events(lat, lon, bearing, aov, elev, days=90,
                          body_list=('moon', 'sun')):
    """扫描 days 天内，日/月进入取景框（方位 ±AoV/2，仰角 elev−1.5 ~ elev+8）的时刻。
    返回 [(body, naive_utc_dt, az, alt)]，相邻合并为窗口。"""
    try:
        from app.core import astronomy as _astro
    except ImportError:
        from core import astronomy as _astro
    half = max(0.5, aov / 2.0 + 0.3)
    # 含大气折射的视觉地平线（太阳贴地平线约 -0.83°）
    alt_lo = max(-1.0, elev - 1.5)
    alt_hi = elev + 8.0
    now = datetime.now(timezone.utc)
    start = now.replace(minute=0, second=0, microsecond=0)
    events = []
    for body in body_list:
        raw = []
        t = start
        end = start + timedelta(days=days)
        step = timedelta(minutes=5)
        while t <= end:
            jd = _astro.jd_from_datetime(t)
            alt, az = _astro._body_altaz(body, lat, lon, jd)
            daz = abs((az - bearing + 180) % 360 - 180)
            if daz <= half and alt_lo <= alt <= alt_hi:
                raw.append((t, az, alt))
            t += step
        # 合并相邻为窗口，并二分精化峰值时刻
        i = 0
        while i < len(raw):
            j = i
            while j + 1 < len(raw) and (raw[j + 1][0] - raw[j][0]) <= timedelta(
                    minutes=10):
                j += 1
            t0, t1 = raw[i][0], raw[j][0]
            # 精化：窗口内找方位差最小的时刻
            best = None
            tt = t0
            while tt <= t1:
                jd = _astro.jd_from_datetime(tt)
                alt, az = _astro._body_altaz(body, lat, lon, jd)
                daz = abs((az - bearing + 180) % 360 - 180)
                cand = (abs(alt - elev), daz, tt, az, alt)
                if best is None or (cand[0], cand[1]) < (best[0], best[1]):
                    best = cand
                tt += timedelta(minutes=1)
            if best:
                events.append({'body': body, 'utc': best[2],
                               'az': round(best[3], 1), 'alt': round(best[4], 2),
                               'daz': round(best[0], 2)})
            i = j + 1
    return events


def weather_for_event(lat, lon, ev_utc_local):
    """事件时刻（BJT naive）的天气可行性（轻量：单点云量/AOD/降水）
    复用 openmeteo 模块的缓存/限速/重试，避免绕过配额控制。"""
    try:
        from app.services import openmeteo as om
    except ImportError:
        from services import openmeteo as om
    out = {'available': False}
    try:
        h = (om._get(om.FORECAST_URL, {
            'latitude': lat, 'longitude': lon,
            'hourly': ('cloud_cover,cloud_cover_low,cloud_cover_mid,'
                       'cloud_cover_high,precipitation_probability,'
                       'visibility'),
            'start_date': ev_utc_local.date().isoformat(),
            'end_date': ev_utc_local.date().isoformat(),
            'timezone': 'Asia/Shanghai'}, timeout=20) or {}).get('hourly', {})
        times = h.get('time') or []
        key = ev_utc_local.strftime('%Y-%m-%dT%H:00')
        if key not in times:
            key = min(times, key=lambda t: abs(
                (datetime.fromisoformat(t) - ev_utc_local).total_seconds())) \
                if times else None
        if key is None:
            return out
        i = times.index(key)

        def v(name, dft=None):
            arr = h.get(name) or []
            x = arr[i] if i < len(arr) else None
            return x if x is not None else dft
        low, mid, high = v('cloud_cover_low', 0), v('cloud_cover_mid', 0), \
            v('cloud_cover_high', 0)
        out.update({'available': True, 'hour': key[11:16],
                    'low': round(low), 'mid': round(mid), 'high': round(high),
                    'total': round(low + mid + high),
                    'precip_prob': v('precipitation_probability', 0),
                    'vis_km': round((v('visibility', 15000) or 15000) / 1000, 1)})
    except Exception as e:
        out['reason'] = str(e)[:80]
    return out

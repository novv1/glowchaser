# -*- coding: utf-8 -*-
"""
天文引擎：太阳/月亮位置、升落时刻、方位角、月相、蓝调时刻
算法依据：
- 太阳：Meeus《Astronomical Algorithms》Ch.25 低精度算法（~0.01°）
- 月亮：Meeus Ch.47 截断 ELP2000-82（主项，精度 ~0.05°），含视差修正
- 升落：数值迭代求高度角穿越，太阳标准高度 -0.833°（视半径+折射），月亮 +0.125°
"""
import math
from datetime import datetime

D2R = math.pi / 180.0
R2D = 180.0 / math.pi


def jd_from_datetime(dt_utc: datetime) -> float:
    """Gregorian datetime(UTC) -> Julian Day"""
    y, m = dt_utc.year, dt_utc.month
    d = dt_utc.day + (dt_utc.hour + dt_utc.minute / 60 + dt_utc.second / 3600) / 24.0
    if m <= 2:
        y -= 1
        m += 12
    a = y // 100
    b = 2 - a + a // 4
    return int(365.25 * (y + 4716)) + int(30.6001 * (m + 1)) + d + b - 1524.5


def datetime_from_jd(jd: float) -> datetime:
    jd = jd + 0.5
    z = int(jd)
    f = jd - z
    if z < 2299161:
        a = z
    else:
        alpha = int((z - 1867216.25) / 36524.25)
        a = z + 1 + alpha - alpha // 4
    b = a + 1524
    c = int((b - 122.1) / 365.25)
    d = int(365.25 * c)
    e = int((b - d) / 30.6001)
    day = b - d - int(30.6001 * e) + f
    month = e - 1 if e < 14 else e - 13
    year = c - 4716 if month > 2 else c - 4715
    day_int = int(day)
    frac = day - day_int
    hour = frac * 24
    hh = int(hour)
    mm_f = (hour - hh) * 60
    mm = int(mm_f)
    ss = int(round((mm_f - mm) * 60))
    if ss >= 60:
        mm += 1
        ss -= 60
    if mm >= 60:
        hh += 1
        mm -= 60
    if hh >= 24:  # 进位到次日
        import datetime as _dt
        base = _dt.datetime(year, month, day_int)
        return (base + _dt.timedelta(days=1)).replace(hour=hh - 24, minute=mm,
                                                      second=ss)
    return datetime(year, month, day_int, hh, mm, ss)


def _norm360(x: float) -> float:
    return x % 360.0


def _sin_d(x: float) -> float:
    return math.sin(x * D2R)


def _cos_d(x: float) -> float:
    return math.cos(x * D2R)


def _tan_d(x: float) -> float:
    return math.tan(x * D2R)


# ---------------------------------------------------------------- 太阳

def sun_position(jd: float):
    """太阳几何黄经/黄纬(0)/距离 -> 赤经赤纬(度)。Meeus Ch.25"""
    n = jd - 2451545.0
    L = _norm360(280.460 + 0.9856474 * n)
    g = _norm360(357.528 + 0.9856003 * n)
    lam = _norm360(L + 1.915 * _sin_d(g) + 0.020 * _sin_d(2 * g))
    eps = 23.439 - 0.0000004 * n
    ra = math.atan2(_cos_d(eps) * _sin_d(lam), _cos_d(lam)) * R2D % 360
    dec = math.asin(_sin_d(eps) * _sin_d(lam)) * R2D
    dist = 1.00014 - 0.01671 * _cos_d(g) - 0.00014 * _cos_d(2 * g)  # AU
    return ra, dec, dist


def moon_position(jd: float):
    """月亮地心黄经/黄纬/距离。Meeus Ch.47 截断 ELP2000-82（主项）。
    返回 (ra, dec, dist_km, lam, beta)  精度 ~0.05°"""
    t = (jd - 2451545.0) / 36525.0
    lp = _norm360(218.3164477 + 481267.88123421 * t - 0.0015786 * t * t
                  + t ** 3 / 538841 - t ** 4 / 65194000)
    d = _norm360(297.8501921 + 445267.1114034 * t - 0.0018819 * t * t
                 + t ** 3 / 545868 - t ** 4 / 113065000)
    m = _norm360(357.5291092 + 35999.0502909 * t - 0.0001536 * t * t
                 + t ** 3 / 24490000)
    mp = _norm360(134.9633964 + 477198.8675055 * t + 0.0087414 * t * t
                  + t ** 3 / 69699 - t ** 4 / 14712000)
    f = _norm360(93.2720950 + 483202.0175233 * t - 0.0036539 * t * t
                 - t ** 3 / 3526000 + t ** 4 / 863310000)
    a1 = _norm360(119.75 + 131.849 * t)
    a2 = _norm360(53.09 + 479264.290 * t)
    a3 = _norm360(313.45 + 481266.484 * t)
    e = 1 - 0.002516 * t - 0.0000074 * t * t

    def efac(k):
        k = abs(k)
        return e if k == 1 else (e * e if k == 2 else 1.0)

    # Σl: (d, m, mp, f, coeff)
    sl_terms = [
        (0, 0, 1, 0, 6288774), (2, 0, -1, 0, 1274027), (2, 0, 0, 0, 658314),
        (0, 0, 2, 0, 213618), (0, 1, 0, 0, -185116), (0, 0, 0, 2, -114332),
        (2, 0, -2, 0, 58793), (2, -1, 0, 0, 57066), (2, 0, 1, 0, 53322),
        (2, -1, 1, 0, 45758), (2, -1, -1, 0, -40923), (0, 1, 1, 0, -34720),
        (2, -2, 0, 0, -30383), (0, 1, -1, 0, 15327), (1, 0, 0, 0, -12528),
        (0, 1, 1, 1, 10980), (0, 1, 1, -1, 10675), (0, 1, 0, 1, 10034),
        (0, 1, 0, -1, 8548), (1, 0, 0, 1, -7888), (0, 0, 2, 1, -6766),
        (0, 0, 2, -1, -5163), (2, 0, 0, -2, 4987), (2, 0, 2, 0, 4036),
        (2, 0, -3, 0, 3994), (0, 0, 0, 1, 3861), (2, 0, 2, -2, 3665),
        (0, 1, 0, -2, -2689), (0, 0, 1, 2, -2602), (0, 0, 1, -2, 2390),
        (2, 0, -1, 2, -2348), (0, 1, 2, 0, 2236), (0, 1, -2, 0, -2120),
        (2, 0, 0, 1, -2069), (2, 0, 0, -1, 2048), (0, 1, 2, -2, -1773),
        (0, 1, 2, 2, -1595), (2, 1, 0, 0, 1215), (2, 0, -1, -2, -1110),
        (2, 1, -1, 0, -892), (2, 0, 1, 2, -810), (2, 0, -1, -1, 759),
        (2, -1, 1, 2, -713), (2, -1, -2, 2, -700), (2, 0, 0, 2, 691),
        (2, 0, 0, -3, 596), (2, 0, 3, 0, 549), (2, -1, 2, 0, 537),
        (2, -1, -3, 0, 520), (2, 0, -2, 2, -487), (2, 1, -1, 2, -399),
        (0, 0, 1, -3, -381), (2, 0, 3, 2, 351), (2, -1, 0, 2, 340),
    ]
    sb_terms = [
        (0, 0, 0, 1, 5128122), (0, 0, 1, 1, 280602), (0, 0, 1, -1, 277693),
        (2, 0, 0, -1, 173237), (2, 0, -1, 1, 55413), (2, 0, -1, -1, 46271),
        (2, 0, 0, 1, 32573), (0, 0, 2, 1, 17198), (2, 0, 1, -1, 9266),
        (0, 0, 2, -1, 8822), (2, -1, 0, -1, 8216), (2, 0, -2, -1, 4324),
        (2, 0, 1, 1, 4200), (2, 1, 0, -1, -3359), (2, -1, -1, 1, 2463),
        (2, -1, 0, 1, 2211), (2, -1, -1, -1, 2065), (0, 1, -1, -1, -1870),
        (4, 0, -1, -1, 1828), (0, 1, 0, 1, -1794), (0, 0, 0, 3, -1749),
        (0, 1, -1, 1, -1565), (1, 0, 0, 1, -1491), (0, 1, 1, 1, -1475),
        (0, 1, 1, -1, -1410), (0, 1, 0, -1, -1344), (1, 0, 0, -1, -1335),
        (0, 0, 3, 1, 1107), (4, 0, 0, -1, 1021), (4, 0, -1, 1, 833),
        (0, 0, 1, -3, 777), (4, 0, -2, 1, 671), (2, 0, 0, -3, 607),
        (2, 0, 2, -1, 596), (2, -1, 1, -1, 491), (2, 0, -2, 1, -451),
        (0, 0, 3, -1, 439), (2, 0, 2, 1, 422), (2, 0, -3, -1, 421),
    ]
    sr_terms = [
        (0, 0, 1, 0, -20905355), (2, 0, -1, 0, -3699111), (2, 0, 0, 0, -2955968),
        (0, 0, 2, 0, -569925), (0, 1, 0, 0, 48888), (2, 0, -2, 0, -3149),
        (2, -1, 0, 0, 246158), (2, 0, 1, 0, -152138), (2, -1, -1, 0, -170733),
        (2, -1, 1, 0, -204586), (2, -1, -2, 0, -129620), (0, 1, -1, 0, 108743),
        (0, 1, 1, 0, 104755), (2, -2, 0, 0, 10321), (0, 0, -2, 0, 0),
        (2, 0, -1, 2, 79661), (2, -1, -2, -2, -34782), (2, -1, 0, -2, -23210),
        (2, 0, 1, -2, -21636), (0, 0, 0, 2, 24208), (2, 0, 2, 0, 30824),
        (2, -2, 0, -2, -8379), (0, 1, -2, -2, -16675), (0, 1, 2, 2, -12831),
        (2, -1, 2, 0, -12831),
    ]
    sl = 0.0
    sb = 0.0
    sr = 0.0
    for td, tm, tmp, tf, c in sl_terms:
        arg = td * d + tm * m + tmp * mp + tf * f
        sl += c * efac(tm) * _sin_d(arg)
    for td, tm, tmp, tf, c in sb_terms:
        arg = td * d + tm * m + tmp * mp + tf * f
        sb += c * efac(tm) * _sin_d(arg)
    for td, tm, tmp, tf, c in sr_terms:
        arg = td * d + tm * m + tmp * mp + tf * f
        sr += c * efac(tm) * _cos_d(arg)
    sl += 3958 * _sin_d(a1) + 1962 * _sin_d(lp - f) + 318 * _sin_d(a2)
    sb += (-2235 * _sin_d(lp) + 382 * _sin_d(a3) + 175 * _sin_d(a1 - f)
           + 175 * _sin_d(a1 + f) + 127 * _sin_d(lp - mp) - 115 * _sin_d(lp + mp))

    lam = lp + sl / 1e6
    beta = sb / 1e6
    dist = 385000.56 + sr / 1000.0

    # 黄道 -> 赤道
    eps = 23.4392911 - 0.0130042 * t
    ra = math.atan2(_sin_d(lam) * _cos_d(eps) - math.tan(beta * D2R) * _sin_d(eps),
                    _cos_d(lam)) * R2D % 360
    dec = math.asin(_sin_d(beta) * _cos_d(eps)
                    + _cos_d(beta) * _sin_d(eps) * _sin_d(lam)) * R2D
    return ra, dec, dist, lam, beta


def gmst(jd: float) -> float:
    """格林尼治平恒星时(度)"""
    t = (jd - 2451545.0) / 36525.0
    theta = 280.46061837 + 360.98564736629 * (jd - 2451545.0) + 0.000387933 * t * t
    return theta % 360.0


def altaz(ra: float, dec: float, lat: float, lon: float, jd: float):
    """赤经赤纬 -> 测站几何高度角/方位角(度, 北=0 东=90)。
    注意：返回几何高度角（不含折射）。升落判定用标准视高度阈值：
    太阳 -0.833°（视半径+平均折射），月亮 +0.125°（视差+折射+视半径）。"""
    lst = (gmst(jd) + lon) % 360.0
    ha = (lst - ra) * D2R
    sin_alt = _sin_d(dec) * _sin_d(lat) + _cos_d(dec) * _cos_d(lat) * math.cos(ha)
    sin_alt = max(-1.0, min(1.0, sin_alt))
    alt = math.asin(sin_alt) * R2D
    # 方位角
    az = math.atan2(-math.sin(ha),
                    _tan_d(dec) * _cos_d(lat) - _sin_d(lat) * math.cos(ha)) * R2D
    az = (az + 360.0) % 360.0
    return alt, az


def _body_altaz(body: str, lat: float, lon: float, jd: float):
    if body == 'sun':
        ra, dec, _ = sun_position(jd)
        return altaz(ra, dec, lat, lon, jd)
    ra, dec, dist, _, _ = moon_position(jd)
    alt, az = altaz(ra, dec, lat, lon, jd)
    # 月亮视差修正（地平视差 ~0.95°，低高度角显著）
    hp = math.asin(6378.14 / dist) * R2D
    par = hp * _cos_d(alt)
    return alt - par, az


def find_crossing(body: str, lat: float, lon: float, jd_start: float,
                  target_alt: float, rising: bool,
                  max_hours: float = 24.0, step_min: float = 20.0):
    """数值搜索天体高度角穿越 target_alt 的时刻(JD)。rising=True 向上穿越"""
    def f(jd):
        return _body_altaz(body, lat, lon, jd)[0] - target_alt

    step = step_min / 1440.0
    t = jd_start
    f_prev = f(t)
    while t - jd_start < max_hours / 24.0:
        t2 = t + step
        f_next = f(t2)
        if (rising and f_prev <= 0 < f_next) or ((not rising) and f_prev >= 0 > f_next):
            # 二分精化
            lo, hi = t, t2
            for _ in range(40):
                mid = (lo + hi) / 2
                if (f(lo) <= 0) == (f(mid) <= 0):
                    lo = mid
                else:
                    hi = mid
            return (lo + hi) / 2
        t, f_prev = t2, f_next
    return None


def sun_times(date_local: datetime, lat: float, lon: float, tz_offset_h: float,
              horizon_fn=None):
    """给定本地日期 -> 日出/日落/正午/太阳最高高度角/蓝调/民用晨昏蒙影。
    date_local 为本地时区当日 00:00。返回 dict(本地时间字符串/角度)。
    horizon_fn(az_deg)->遮蔽角（度，来自地形采样）：先按 -0.833°（折射 0.57 +
    太阳半径 0.27 补偿）正常算出升落方位，再按该方位的遮蔽角重算交叉时刻——
    山脊/地形会推迟日出、提前日落。蓝调/蒙影不受地形影响保持原值。"""
    jd0 = jd_from_datetime(date_local) - tz_offset_h / 24.0  # 当地 0 时对应 UTC JD
    jd_sunset = find_crossing('sun', lat, lon, jd0, -0.833, rising=False, max_hours=26)
    jd_sunrise = find_crossing('sun', lat, lon, jd0, -0.833, rising=True, max_hours=26)
    if horizon_fn:
        try:
            if jd_sunset:
                _, az_s = _body_altaz('sun', lat, lon, jd_sunset)
                ha = horizon_fn(az_s)
                if ha and ha > 0.2:
                    jd_sunset = find_crossing('sun', lat, lon, jd0,
                                              ha - 0.833, rising=False,
                                              max_hours=26)
            if jd_sunrise:
                _, az_r = _body_altaz('sun', lat, lon, jd_sunrise)
                ha = horizon_fn(az_r)
                if ha and ha > 0.2:
                    jd_sunrise = find_crossing('sun', lat, lon, jd0,
                                               ha - 0.833, rising=True,
                                               max_hours=26)
        except Exception:
            pass

    # 太阳正午：高度角最大（在日出日落中间附近扫描）
    if jd_sunrise and jd_sunset:
        lo, hi = jd_sunrise, jd_sunset
    else:
        lo, hi = jd0, jd0 + 1.0

    def neg_alt(jd):
        return -_body_altaz('sun', lat, lon, jd)[0]

    # 黄金分割近似一维极小化
    gr = (math.sqrt(5) - 1) / 2
    a, b = lo, hi
    c = b - gr * (b - a)
    dd = a + gr * (b - a)
    for _ in range(60):
        if neg_alt(c) < neg_alt(dd):
            b = dd
        else:
            a = c
        c = b - gr * (b - a)
        dd = a + gr * (b - a)
    jd_noon = (a + b) / 2
    noon_alt, noon_az = _body_altaz('sun', lat, lon, jd_noon)

    def fmt(jd):
        return datetime_from_jd(jd + tz_offset_h / 24.0)

    out = {
        'sunrise': fmt(jd_sunrise) if jd_sunrise else None,
        'sunset': fmt(jd_sunset) if jd_sunset else None,
        'noon': fmt(jd_noon),
        'noon_alt': round(noon_alt, 2),
        'noon_az': round(noon_az, 2),
    }
    for t in (jd_sunrise, jd_sunset):
        pass
    if jd_sunrise:
        _, az = _body_altaz('sun', lat, lon, jd_sunrise)
        out['sunrise_az'] = round(az, 1)
    if jd_sunset:
        _, az = _body_altaz('sun', lat, lon, jd_sunset)
        out['sunset_az'] = round(az, 1)

    # 蓝调 (-6 ~ -4) / 黄金时段 (-4 ~ 6) / 民用晨昏蒙影 -6
    def crossing(alt, rising):
        return find_crossing('sun', lat, lon, jd0, alt, rising=rising, max_hours=26)

    for key, alt, rising in [
        ('blue_morning_start', -6.0, True), ('blue_morning_end', -4.0, True),
        ('blue_evening_start', -4.0, False), ('blue_evening_end', -6.0, False),
        ('civil_dawn', -6.0, True), ('civil_dusk', -6.0, False),
    ]:
        j = crossing(alt, rising)
        out[key] = fmt(j) if j else None
    return out


def moon_times(date_local: datetime, lat: float, lon: float, tz_offset_h: float,
               horizon_fn=None):
    """月出/月落/月相照度。horizon_fn(az)->遮蔽角（度）：按方位重算月出月落，
    山脊推迟月出/提前月落。"""
    jd0 = jd_from_datetime(date_local) - tz_offset_h / 24.0
    jd_mr = find_crossing('moon', lat, lon, jd0, 0.125, rising=True, max_hours=30,
                          step_min=30)
    jd_ms = find_crossing('moon', lat, lon, jd0, 0.125, rising=False, max_hours=30,
                          step_min=30)
    if horizon_fn:
        try:
            if jd_mr:
                _, az = _body_altaz('moon', lat, lon, jd_mr)
                ha = horizon_fn(az)
                if ha and ha > 0.2:
                    jd_mr = find_crossing('moon', lat, lon, jd0, ha + 0.125,
                                          rising=True, max_hours=30, step_min=30)
            if jd_ms:
                _, az = _body_altaz('moon', lat, lon, jd_ms)
                ha = horizon_fn(az)
                if ha and ha > 0.2:
                    jd_ms = find_crossing('moon', lat, lon, jd0, ha + 0.125,
                                          rising=False, max_hours=30, step_min=30)
        except Exception:
            pass

    def fmt(jd):
        return datetime_from_jd(jd + tz_offset_h / 24.0)

    out = {}
    if jd_mr:
        _, az = _body_altaz('moon', lat, lon, jd_mr)
        out['moonrise'] = fmt(jd_mr)
        out['moonrise_az'] = round(az, 1)
    if jd_ms:
        _, az = _body_altaz('moon', lat, lon, jd_ms)
        out['moonset'] = fmt(jd_ms)
        out['moonset_az'] = round(az, 1)

    # 月相：日月黄经差
    _, _, _, lam_m, _ = moon_position(jd0 + 0.5)
    _, _, _, lam_s, _ = sun_ecliptic(jd0 + 0.5)
    elong = (lam_m - lam_s) % 360.0
    illum = (1 - _cos_d(elong)) / 2
    out['moon_illum'] = round(illum * 100, 1)
    out['moon_phase_angle'] = round(elong, 1)
    # 相位名称
    names = ['新月', '娥眉月', '上弦月', '盈凸月', '满月', '亏凸月', '下弦月', '残月']
    idx = int(((elong + 22.5) % 360) // 45)
    out['moon_phase'] = names[idx]
    return out


def sun_ecliptic(jd: float):
    """太阳黄经（供月相计算）"""
    n = jd - 2451545.0
    L = _norm360(280.460 + 0.9856474 * n)
    g = _norm360(357.528 + 0.9856003 * n)
    lam = _norm360(L + 1.915 * _sin_d(g) + 0.020 * _sin_d(2 * g))
    return None, None, None, lam, 0.0


def body_track(body: str, lat: float, lon: float, jd_start: float, jd_end: float,
               step_min: float = 10.0):
    """天体高度角/方位角轨迹"""
    pts = []
    t = jd_start
    while t <= jd_end:
        alt, az = _body_altaz(body, lat, lon, t)
        pts.append((t, round(alt, 2), round(az, 1)))
        t += step_min / 1440.0
    return pts


def find_alignment_windows(body: str, lat: float, lon: float, jd_start: float,
                           jd_end: float, target_az: float, tol_deg: float = 2.0,
                           alt_lo: float = 1.0, alt_hi: float = 40.0,
                           step_min: float = 2.0):
    """悬日/悬月窗口：方位角对准目标 ±tol 且高度角在 [alt_lo, alt_hi] 的时间段"""
    windows = []
    cur = None
    t = jd_start
    while t <= jd_end:
        alt, az = _body_altaz(body, lat, lon, t)
        daz = abs((az - target_az + 180) % 360 - 180)
        ok = (daz <= tol_deg) and (alt_lo <= alt <= alt_hi)
        if ok and cur is None:
            cur = {'jd_start': t, 'alt_min': alt, 'alt_max': alt, 'daz_max': daz}
        elif ok and cur:
            cur['alt_min'] = min(cur['alt_min'], alt)
            cur['alt_max'] = max(cur['alt_max'], alt)
            cur['daz_max'] = max(cur['daz_max'], daz)
        elif not ok and cur:
            cur['jd_end'] = t
            windows.append(cur)
            cur = None
        t += step_min / 1440.0
    if cur:
        cur['jd_end'] = t
        windows.append(cur)
    # 中心时刻方位差重新采样（更精确的对准时刻）。
    # 候选必须仍满足高度角范围：结束边界采样本身可能 alt 越界，
    # 不能让"方位更准但已低于 alt_lo"的点成为最佳时刻。
    for w in windows:
        best_t = w['jd_start']
        best_alt, best_az = _body_altaz(body, lat, lon, best_t)
        best_d = abs((best_az - target_az + 180) % 360 - 180)
        tt = w['jd_start']
        while tt <= w['jd_end']:
            alt, az = _body_altaz(body, lat, lon, tt)
            daz = abs((az - target_az + 180) % 360 - 180)
            if daz < best_d and alt_lo <= alt <= alt_hi:
                best_t, best_d, best_alt, best_az = tt, daz, alt, az
            tt += 0.5 / 1440.0
        w['jd_best'] = best_t
        w['best_daz'] = round(best_d, 2)
        w['best_alt'] = round(best_alt, 2)
        w['best_az'] = round(best_az, 1)
        w.pop('jd_start'), w.pop('jd_end'), w.pop('alt_min'), w.pop('alt_max'), w.pop('daz_max')
    return windows


def body_rising(body: str, lat: float, lon: float, jd: float,
                minutes: float = 5.0) -> bool:
    """判断天体在 jd 时刻处于上升段（用于日出/日落、月出/月落分类）。"""
    dt = minutes / 1440.0
    a0 = _body_altaz(body, lat, lon, jd - dt)[0]
    a1 = _body_altaz(body, lat, lon, jd + dt)[0]
    return a1 > a0


def sunset_line_speed(lat: float, lon: float, jd_sunset: float) -> float:
    """日落线速度 km/min（教程经验口径）。
    几何值 = R·|dα/dt|（晨昏线沿太阳方位大圆的掠过速度）。
    教程查表值（22.5°N 秋季 ~21 km/min 等）系统性约为几何值的 0.83 倍，
    故采用经验系数 0.83 校准，与教程实战案例口径一致；后续可由回测精调。"""
    dt = 5.0 / 1440.0
    a1 = _body_altaz('sun', lat, lon, jd_sunset - dt)[0]
    a2 = _body_altaz('sun', lat, lon, jd_sunset + dt)[0]
    rate = abs(a2 - a1) / (2 * 5.0)  # 度/分钟
    return 6371.0 * D2R * rate * 0.83


if __name__ == '__main__':
    # 珠海 2026-09-03 验证
    tz = 8.0
    d = datetime(2026, 9, 3)
    st = sun_times(d, 22.27, 113.57, tz)
    print('sunset:', st['sunset'], 'az:', st.get('sunset_az'))
    print('sunrise:', st['sunrise'], 'az:', st.get('sunrise_az'))
    print('noon:', st['noon'], 'alt:', st['noon_alt'])
    print('blue evening:', st['blue_evening_start'], '->', st['blue_evening_end'])
    mt = moon_times(d, 22.27, 113.57, tz)
    print('moonrise:', mt.get('moonrise'), 'az:', mt.get('moonrise_az'),
          'phase:', mt.get('moon_phase'), mt.get('moon_illum'), '%')
    if st.get('sunset'):
        jd_s = jd_from_datetime(st['sunset']) - tz / 24.0
        print('sunset line speed:', round(sunset_line_speed(22.27, 113.57, jd_s), 1), 'km/min')

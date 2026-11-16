# -*- coding: utf-8 -*-
"""悬日月长期日历：未来 N 天内升/落方位落在目标方位 ± 容差的日期。

与 /api/align、报告窗口表**同源**：方位一律取 core.astronomy 的
sun_times() / moon_times() 返回值（视地平线标准高度角 太阳 -0.833°、
月亮 +0.125°，地形遮蔽经 horizon_fn 透传），不再用解析式近似——旧的
解析式（太阳高度取 0°、正午赤纬）与 sun_times 差 0.3~0.5°，容差最小
0.5° 时日历和窗口表会互相打架。

性能策略：**粗筛 + 严格复核**，返回结果与逐日严格扫描完全一致。
  1) 粗筛（不调 sun_times/moon_times，只用赤纬采样 + 球面三角解析式）：
     同一天的升/落方位只由穿越时刻的赤纬决定，而 A(δ) 对 δ 单调，
     故取 [jd0, jd0+26h(日)/30h(月)] 内赤纬的极值（采样步长 3h，并按
     半步长×最大赤纬速率外推）算出的方位区间 **必包住**严格方位
     （实测包络余量 < 0.1°）。区间与 target ± (tol + 冗余) 无交集 →
     当天不可能命中，直接跳过；无法判定（极区/无穿越）→ 保守复核。
  2) 严格复核：候选日才调 sun_times/moon_times，用返回值方位判定 tol。
  粗筛失败只影响速度不影响正确性；_COARSE=False 可强制逐日严格扫描
  （测试用它做 A/B 对拍）。典型 400 天：粗筛 ~30ms + 复核 1~3 天/事件。

进程内结果缓存在 horizon_fn is None 时启用（地形遮蔽参与时不用缓存），
TTL 默认 6h，可用环境变量 GC_CAL_CACHE_TTL（秒）覆盖。
"""
import math
import os
import threading
from datetime import date, datetime, timedelta

try:  # 项目根运行（uvicorn app.main:app）
    from app.core import astronomy as astro
except ImportError:  # app/ 为根运行（发布沙箱 / python main.py）
    from core import astronomy as astro

D2R = math.pi / 180.0
# 本地时区偏移（小时）：与 services/report.py::TZ_OFF 严格一致
TZ_OFF = 8.0

# 升落事件名与视地平线标准高度角（与 report.py::_day_events 同口径）
_KIND_NAME = {'sun': {'rise': '日出', 'set': '日落'},
              'moon': {'rise': '月出', 'set': '月落'}}
_ALT = {'sun': -0.833, 'moon': 0.125}
_TYPE = {'sun': '悬日', 'moon': '悬月'}
# 月亮"地心"穿越高度角：视地平线 +0.125° 叠加地平视差 ~0.95°
# （astronomy._body_altaz 把视差从几何高度角里减掉，故反推 +0.95）
_MOON_ALT_GEO = 1.075
# 粗筛包络冗余（度）：覆盖解析式误差与赤纬采样残差，宁宽勿漏
_MARGIN = {'sun': 0.6, 'moon': 1.0}
_SPAN_H = {'sun': 26.0, 'moon': 30.0}   # 与 sun_times/moon_times 搜索跨度一致
_STEP_H = 3.0                           # 赤纬采样步长（小时）

# 关闭后逐日严格扫描（仅测试对拍用；生产恒为 True）
_COARSE = True

_CACHE: dict = {}
_CACHE_LOCK = threading.Lock()
_CACHE_MAX = 48
try:
    _CACHE_TTL = float(os.environ.get('GC_CAL_CACHE_TTL', 6 * 3600))
except (TypeError, ValueError):
    _CACHE_TTL = 6 * 3600.0


def _as_date(value):
    """start 参数归一化：None -> 今天（本地）；接受 date/datetime/字符串。"""
    if value is None:
        return datetime.now().date()
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return datetime.strptime(str(value), '%Y-%m-%d').date()


def _az_from_dec(lat, dec_deg, alt_deg):
    """解析式：给定赤纬与几何高度角 -> 升落方位（东半边 0~180，南=90）。
    cos A = (sinδ − sin h·sinφ) / (cos h·cosφ)。仅用于粗筛，不进入返回值。
    A(δ) 对 δ 单调递减：|cos A| > 1（该赤纬不穿越）时按 A 的极限值
    （0 或 180）截断——区间只会更宽，保证包住真实穿越方位。
    仅 cosφ -> 0（极区）返回 None。"""
    num = (math.sin(dec_deg * D2R)
           - math.sin(alt_deg * D2R) * math.sin(lat * D2R))
    den = math.cos(alt_deg * D2R) * math.cos(lat * D2R)
    if abs(den) < 1e-9:
        return None
    c = max(-1.0, min(1.0, num / den))
    return math.acos(c) / D2R


def _coarse_span(lat, body, kind, jd0):
    """粗筛：当天升/落方位的可能区间 (az_lo, az_hi)，必包含严格方位。
    返回 None 表示无法判定（极区/无穿越/采样异常）-> 交由严格复核。"""
    step = _STEP_H
    span = _SPAN_H[body]
    alt_geo = _MOON_ALT_GEO if body == 'moon' else _ALT['sun']
    decs = []
    for i in range(int(math.ceil(span / step)) + 1):
        jd = jd0 + (i * step) / 24.0
        try:
            if body == 'moon':
                decs.append(astro.moon_position(jd)[1])
            else:
                decs.append(astro.sun_position(jd)[1])
        except Exception:
            return None
    # 采样点之间可能藏着赤纬极值：按最大单步变化量的半幅外推
    rate = 0.0
    for a, b in zip(decs, decs[1:]):
        rate = max(rate, abs(b - a))
    half = rate / 2.0
    d_lo, d_hi = min(decs) - half, max(decs) + half
    a_at_hi = _az_from_dec(lat, d_hi, alt_geo)
    a_at_lo = _az_from_dec(lat, d_lo, alt_geo)
    if a_at_hi is None or a_at_lo is None:
        return None
    # A(δ) 单调递减：δ↑ -> A↓，故方位区间为 [A(d_hi), A(d_lo)]
    if kind == 'set':
        return 360.0 - a_at_lo, 360.0 - a_at_hi
    return a_at_hi, a_at_lo


def _coarse_hit(lat, body, kind, jd0, target_az, window):
    """粗筛判定：方位区间与 target ± window 有交集则需严格复核。"""
    sp = _coarse_span(lat, body, kind, jd0)
    if sp is None:
        return True                     # 无法判定 -> 保守复核
    return sp[0] <= target_az + window and sp[1] >= target_az - window


def _strict_event(day, lat, lon, body, kind, target_az, tol, horizon_fn):
    """严格复核某天：调 sun_times/moon_times，命中则返回事件字典。
    方位/时刻全部取 astro 返回值（不自己算），异常与缺事件一律视为无。"""
    dt0 = datetime(day.year, day.month, day.day)
    try:
        if body == 'moon':
            t = astro.moon_times(dt0, lat, lon, TZ_OFF,
                                 horizon_fn=horizon_fn)
            tkey, akey = ('moonrise', 'moonrise_az') if kind == 'rise' \
                else ('moonset', 'moonset_az')
        else:
            t = astro.sun_times(dt0, lat, lon, TZ_OFF, horizon_fn=horizon_fn)
            tkey, akey = ('sunrise', 'sunrise_az') if kind == 'rise' \
                else ('sunset', 'sunset_az')
    except Exception:
        return None
    t_ev, az = t.get(tkey), t.get(akey)
    if t_ev is None or az is None:
        return None
    daz = abs((az - target_az + 180.0) % 360.0 - 180.0)
    if daz > tol:
        return None
    return {'date': day.isoformat(), 'time': t_ev.strftime('%H:%M'),
            'az': az, 'daz': round(daz, 2), 'alt': _ALT[body],
            'illum': t.get('moon_illum') if body == 'moon' else None}


def _cache_key(lat, lon, target_az, tol, body, kind, start, days):
    return (round(lat, 3), round(lon, 3), round(target_az, 2), round(tol, 2),
            body, kind, start.isoformat(), int(days))


def _cache_get(key):
    """命中且未过期则返回深拷贝（调用方可安全改写）。"""
    now = datetime.now().timestamp()
    with _CACHE_LOCK:
        item = _CACHE.get(key)
        if not item:
            return None
        ts, value = item
        if now - ts > _CACHE_TTL:
            _CACHE.pop(key, None)
            return None
    return _copy_result(value)


def _cache_put(key, value):
    with _CACHE_LOCK:
        _CACHE[key] = (datetime.now().timestamp(), _copy_result(value))
        if len(_CACHE) > _CACHE_MAX:    # FIFO 淘汰最旧一半
            for k in list(_CACHE)[:len(_CACHE) - _CACHE_MAX // 2]:
                _CACHE.pop(k, None)


def _copy_result(r):
    out = dict(r)
    out['events'] = [dict(e) for e in r.get('events', [])]
    out['next'] = dict(r['next']) if r.get('next') else None
    return out


def _pick_next(events, now=None):
    """第一个尚未发生的事件（按 date+time 与本机当前时刻比较）。"""
    if now is None:
        now = datetime.now()
    now = now.replace(second=0, microsecond=0)
    for e in events:
        try:
            t_ev = datetime.strptime('%s %s' % (e['date'], e['time']),
                                     '%Y-%m-%d %H:%M')
        except (KeyError, ValueError):
            continue
        if t_ev >= now:
            return dict(e)
    return None


def alignment_calendar(lat, lon, target_az, tol_deg=1.0, body='sun',
                       kind='rise', days=400, start=None, horizon_fn=None):
    """未来 days 天（默认 400 天 ≈ 13 个月）内，日出/日落/月出/月落方位
    落在 target_az ± tol_deg 的日期。

    body: 'sun'|'moon'；kind: 'rise'|'set'。
    start: date 对象，默认今天（本地）。
    horizon_fn: 遮蔽角查询函数（az->度），透传给 sun_times/moon_times，
      与 /api/align 窗口表同口径；None 表示未计入地形遮蔽。
    返回 {'type': '悬日'|'悬月', 'body', 'kind', 'kind_name',
          'target_az', 'tol', 'scanned_days', 'events': [...],
          'next': {...}|None, 'note': str}
    events 每项：{'date': 'YYYY-MM-DD', 'time': 'HH:MM', 'az': 86.3,
                 'daz': 0.42, 'alt': -0.833, 'illum': 17.1 或 None}
      - az 取自 sun_times/moon_times 的对应方位角（不自己算）
      - daz = |az - target_az| 的最小角差（0~180）
      - alt 用视地平线标准值：太阳 -0.833、月亮 +0.125
      - illum 仅月亮有（moon_illum）
    next: events 中第一个"尚未发生"的（date+time 与当前本地时间比较）。
    """
    body = str(body or 'sun').lower()
    kind = str(kind or 'rise').lower()
    if body not in ('sun', 'moon'):
        raise ValueError("body 只能是 'sun' 或 'moon'")
    if kind not in ('rise', 'set'):
        raise ValueError("kind 只能是 'rise' 或 'set'")
    try:
        tol = float(tol_deg)
    except (TypeError, ValueError):
        raise ValueError('tol_deg 必须是数字')
    if not tol > 0:
        raise ValueError('tol_deg 必须 > 0')
    try:
        n_days = int(days)
    except (TypeError, ValueError):
        raise ValueError('days 必须是整数')
    if n_days <= 0:
        raise ValueError('days 必须 > 0')
    target = float(target_az) % 360.0
    start_d = _as_date(start)

    # 缓存：仅 horizon_fn is None 时使用（地形遮蔽不同 -> 结果不同）
    use_cache = horizon_fn is None
    ckey = _cache_key(lat, lon, target, tol, body, kind, start_d, n_days)
    if use_cache:
        try:
            hit = _cache_get(ckey)
            if hit is not None:
                # next 依赖"当前时刻"：缓存命中时重算，避免给出已过事件
                hit['next'] = _pick_next(hit.get('events', []))
                return hit
        except Exception:
            pass                        # 缓存故障不影响主流程

    window = tol + _MARGIN[body]
    events = []
    for off in range(n_days):
        day = start_d + timedelta(days=off)
        if _COARSE:
            try:
                jd0 = astro.jd_from_datetime(
                    datetime(day.year, day.month, day.day)) - TZ_OFF / 24.0
                if not _coarse_hit(lat, body, kind, jd0, target, window):
                    continue            # 当天不可能命中
            except Exception:
                pass                    # 粗筛异常 -> 回退严格复核
        ev = _strict_event(day, lat, lon, body, kind, target, tol, horizon_fn)
        if ev:
            events.append(ev)

    # next：第一个尚未发生（date+time 晚于当前本地时间）的事件
    nxt = _pick_next(events)

    kind_name = _KIND_NAME[body][kind]
    note = ('未来 %d 天内%s方位落在 %.1f° ± %.1f° 的日期'
            % (n_days, kind_name, target, tol))
    if horizon_fn is None:
        note += '；未计入地形遮蔽（传入 horizon_fn 时与 /api/align 窗口表同口径）'
    else:
        note += '；已按 horizon_fn 计入地形遮蔽'
    out = {'type': _TYPE[body], 'body': body, 'kind': kind,
           'kind_name': kind_name, 'target_az': round(target, 2),
           'tol': tol, 'scanned_days': n_days, 'events': events,
           'next': dict(nxt) if nxt else None, 'note': note}
    if use_cache:
        try:
            _cache_put(ckey, out)
        except Exception:
            pass                        # 缓存故障不影响返回
    return out


_LEGACY_KIND = {'sunrise': 'rise', 'sunset': 'set'}


def sun_alignment_calendar(lat, lon, target_az, tol_deg=1.0, year=None,
                           kind='sunset'):
    """旧接口薄封装（向后兼容旧调用方与既有测试）。

    kind: 'sunrise'|'sunset' -> 新接口的 'rise'|'set'。
    year: 与旧实现同语义——start = (max(当年, year), 今天的月日)，
      未给 year 即从今天起算；扫描 366 天（覆盖一个回归年）。
    返回值保留旧的 sunrise_az/sunset_az 键名；旧键 delta（当日正午赤纬）
    已不再计算，改由 az/daz/alt 表达（不再有调用方依赖）。
    """
    if kind not in _LEGACY_KIND:
        raise ValueError("kind 只能是 'sunrise' 或 'sunset'")
    now = datetime.now()
    try:
        y = max(now.year, int(year)) if year is not None else now.year
    except (TypeError, ValueError):
        raise ValueError('year 必须是整数')
    res = alignment_calendar(lat, lon, target_az, tol_deg, body='sun',
                             kind=_LEGACY_KIND[kind], days=366,
                             start=date(y, now.month, now.day))
    akey = 'sunrise_az' if kind == 'sunrise' else 'sunset_az'
    events = []
    for e in res['events']:
        events.append({'date': e['date'], 'time': e['time'], akey: e['az'],
                       'az': e['az'], 'daz': e['daz'], 'alt': e['alt']})
    out = dict(res)
    out.update({'type': '悬日', 'kind': kind, 'events': events,
                'note': '悬日=低角度太阳对准目标方位；一年通常 1-2 簇日期'})
    return out

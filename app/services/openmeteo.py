# -*- coding: utf-8 -*-
"""Open-Meteo 数据服务：预报（多模式）+ 空气质量（CAMS），带内存 + 磁盘缓存。

免费层额度**按 IP 计**（10000 次/日，无可用性保证）。在共享出口 IP 下（云主机、
运营商 CGNAT——国内宽带/移动网络大量如此）额度会被别的用户一起消耗，随时可能
拿不到数据。因此这里做四件事：

1. **429 分级**：日额度用尽立刻抛 `UpstreamLimitError` 且不重试；突发限流才退避重试。
2. **进程内冷却**：一旦确认额度用尽，后续请求直接快速失败，不再逐个撞墙 + sleep
   （一次报告有 11~13 个上游请求，逐个重试会把失败耗时从秒级拖到几十秒）。
3. **缓存 TTL 对齐上游节奏**：模式 6 小时才更新一轮，30 分钟 TTL 等于反复取同一份数据。
4. **缓存落盘**：uvicorn 不带 --reload 时经常重启，重启即失忆等于白白重打上游；
   磁盘命中完全不发请求，这也是额度被跑光时应用仍能出结果的兜底。
5. **过期兜底（v2.34）**：额度用尽/冷却期时，磁盘里 ≤48h（STALE_MAX_AGE）的
   过期缓存直接拿出来用——查「明天」这种场景，20 小时前的模式数据仍远好于
   没有数据；报告 meta 会如实标注「部分数据来自 N 小时前缓存」。
"""
import os
import math
import time
import json
import hashlib
import logging
import threading
import weakref
from typing import Any
import requests

log = logging.getLogger(__name__)

FORECAST_URL = 'https://api.open-meteo.com/v1/forecast'
AQ_URL = 'https://air-quality-api.open-meteo.com/v1/air-quality'
ENSEMBLE_URL = 'https://ensemble-api.open-meteo.com/v1/ensemble'

# 模式定义：名称 -> open-meteo models 标识
# v2.37 由 4 个扩到 7 个：新增的三个都在珠三角点位（22.27,113.58）实测有数据。
MODELS = {
    'ECMWF': 'ecmwf_ifs025',
    'GFS': 'gfs_global',
    'ICON': 'icon_global',
    'CMA': 'cma_grapes_global',
    'JMA': 'jma_gsm',
    'GEM': 'gem_global',
    'UKMO': 'ukmo_global_deterministic_10km',
}
# 主模式优先级：ECMWF 缺失时按此顺序回退（报告正文/最佳时段用它）
MODEL_PRIORITY = ['ECMWF', 'GFS', 'ICON', 'CMA', 'JMA', 'GEM', 'UKMO']
# 参与共识的权重（EC 精度优先；ENS=集合均值，单独计算）。
# **每个模式都必须在这张表里**：`scoring.consensus` 对未登记模式默认给
# 0.25（高于 ICON/CMA），漏配等于悄悄改变共识口径。
MODEL_WEIGHTS = {'ECMWF': 0.30, 'GFS': 0.20, 'ICON': 0.15, 'CMA': 0.10,
                 'JMA': 0.12, 'GEM': 0.09, 'UKMO': 0.09, 'ENS': 0.25}

# 集合源（v2.37）：多一个源只多几分钱的加权额度，却能把"不确定性"从
# 「单一模式内部离散」升级成「跨模式 + 多成员」。ECMWF 是主源——ens_spread
# 惩罚项的数值口径已被 555 天标定吸收，必须继续只按 ECMWF 成员算。
#
# 2026-10-08 实测（22.27N,113.58E）：**云量成员只有 ECMWF 系两家真有值**——
#   ecmwf_ifs025（物理模式，50 成员）、ecmwf_aifs025（AI 模式，50 成员）;
#   而 gfs025 / gfs05 / icon_global / gem_global / ukmo_global_ensemble_20km
#   在集合接口上对 cloud_cover_low/mid/high 全部返回 null
#   （同一请求换 temperature_2m 就有值 ⇒ 不是参数写错，是变量覆盖不同）。
# 因此默认源只能是这两家：带上别的源只会得到"成员列全 null"的假多源。
ENSEMBLE_PRIMARY = 'ecmwf_ifs025'
ENSEMBLE_MODELS = [s.strip() for s in
                   (os.environ.get('GC_ENS_MODELS')
                    or 'ecmwf_ifs025,ecmwf_aifs025').split(',')
                   if s.strip()]
# 总云量的多源集合（v2.38）：分层云量只有 ECMWF 系两家有成员，但**总云量**
# 上面 2026-10-08 实测四家都有完整成员列：
#   ecmwf_ifs025 51 成员 / ncep_gefs025 31 / icon_global_eps 40 /
#   ukmo_global_ensemble_20km 18（同一请求换 cloud_cover_low 则只剩 ECMWF 系）。
# 这四家的**源间均值离散度**是「各家集合系统对总云量看法差多少」的唯一量化，
# 因此单独发一次总云量请求；分层结论（dim1/受光/分位带）仍只用 ECMWF 系，
# 不动已被 555 天标定吸收的口径。
ENSEMBLE_TOTAL_MODELS = [s.strip() for s in
                         (os.environ.get('GC_ENS_TOTAL_MODELS')
                          or ('ecmwf_ifs025,ncep_gefs025,icon_global_eps,'
                              'ukmo_global_ensemble_20km')).split(',')
                         if s.strip()]

_CACHE: dict[str, tuple[float, Any]] = {}
# 上游模式 6 小时一轮（00/06/12/18Z + 数小时延迟），因此 3 小时 TTL 对精度无损失，
# 却能砍掉同一窗口内的绝大部分重复请求。可用 GC_OM_CACHE_TTL 覆盖（秒）。
CACHE_TTL = int(os.environ.get('GC_OM_CACHE_TTL') or 3 * 3600)
CACHE_MAX = 512   # 内存条目上限（每条是一份完整上游 JSON，必须封顶防内存泄漏）

# 上游额度冷却：确认额度用尽后直接快速失败，不再逐个请求撞墙。
# 日额度用尽也可能只是"共享 IP 被别人用光"，会陆续恢复，所以 10 分钟后允许再探测一次。
DAILY_COOLDOWN = 600
BURST_COOLDOWN = 60
_LIMIT_UNTIL = 0.0
_LIMIT_SCOPE = ''
_LIMIT_LOCK = threading.Lock()


class UpstreamLimitError(RuntimeError):
    """上游限流/额度用尽（区别于网络故障，调用方应据此给出"额度"而非"网络"提示）。"""

    def __init__(self, scope: str = 'daily', retry_after=None, detail: str = ''):
        self.scope = scope              # 'daily' | 'burst'
        self.retry_after = retry_after
        self.detail = detail
        super().__init__(detail or self.user_message)

    @property
    def user_message(self) -> str:
        if self.scope == 'daily':
            return ('Open-Meteo 免费用量已用尽——该额度按 IP 计算，'
                    '共享出口 IP（云主机 / 运营商大内网）下会被其他用户一起消耗。'
                    '通常需等到次日恢复。')
        return 'Open-Meteo 触发突发限流，稍后会自动恢复，可稍等再试。'


def _classify_429(resp) -> tuple:
    """区分「日额度用尽」与「突发限流」：前者重试无意义，必须立刻放弃。"""
    body = ''
    try:
        body = (resp.text or '').strip()
    except Exception:
        pass
    low = body.lower()
    if ('daily' in low and 'limit' in low) or 'daily api request' in low:
        return 'daily', body[:200]
    return 'burst', body[:200]


def limit_state():
    """当前冷却状态：{'scope':.., 'remaining':秒} 或 None（供 /api/health 诊断）。"""
    with _LIMIT_LOCK:
        if time.time() < _LIMIT_UNTIL:
            return {'scope': _LIMIT_SCOPE,
                    'remaining': int(_LIMIT_UNTIL - time.time())}
    return None


def _set_cooldown(scope: str) -> None:
    global _LIMIT_UNTIL, _LIMIT_SCOPE
    with _LIMIT_LOCK:
        _LIMIT_UNTIL = time.time() + (DAILY_COOLDOWN if scope == 'daily'
                                      else BURST_COOLDOWN)
        _LIMIT_SCOPE = scope


def clear_limit_cooldown() -> None:
    """手动清除冷却（测试与诊断用）。"""
    global _LIMIT_UNTIL, _LIMIT_SCOPE
    with _LIMIT_LOCK:
        _LIMIT_UNTIL = 0.0
        _LIMIT_SCOPE = ''

# 出站限速：Open-Meteo 有突发限流。按 API 域名分桶（三主机限流独立），
# 全局单锁做"时间槽预约"，sleep 在锁外进行——多线程并行时各域流水发出，
# 同域请求自动保持 MIN_INTERVAL 间隔且不会同时放行（防 429 惊群）。
# 0.8s 实测无 429（_get 内有 429 指数退避兜底），并行批次整体 ≈8-10s
MIN_INTERVAL = 0.8
_NEXT_SLOT: dict[str, float] = {}
_THROTTLE_LOCK = threading.Lock()
# 弱引用锁表：临界区持有者天然强引用，空闲锁会被 GC，天然有界（无需淘汰）
_CACHE_LOCKS: 'weakref.WeakValueDictionary[str, threading.Lock]' = \
    weakref.WeakValueDictionary()
_CACHE_LOCKS_GUARD = threading.Lock()
_local = threading.local()


def _mem_put(key: str, ts: float, data) -> None:
    """写入内存缓存（ts 保留原始时间戳：磁盘回填时不能刷新时间，否则永不过期）。"""
    _CACHE[key] = (ts, data)
    if len(_CACHE) <= CACHE_MAX:
        return
    now = time.time()
    for k in [k for k, (t, _) in _CACHE.items() if now - t >= CACHE_TTL]:
        _CACHE.pop(k, None)
    if len(_CACHE) > CACHE_MAX:
        drop = len(_CACHE) - int(CACHE_MAX * 0.75)
        for k in sorted(_CACHE, key=lambda k: _CACHE[k][0])[:drop]:
            _CACHE.pop(k, None)


def _cache_put(key: str, data) -> None:
    """写内存 + 落盘（同一个 key 磁盘只保留一份）。"""
    ts = time.time()
    _mem_put(key, ts, data)
    _disk_put(key, data)


# ---------------- 磁盘缓存 ----------------
# 目的：重启不失忆（README 明确提醒改后端要重启），以及额度被跑光时仍能出结果。
_CACHE_DIR_RESOLVED = None


def _cache_dir():
    """缓存目录；GC_OM_CACHE_DIR 可覆盖（置空字符串 = 关闭落盘）。"""
    global _CACHE_DIR_RESOLVED
    if _CACHE_DIR_RESOLVED is None:
        env = os.environ.get('GC_OM_CACHE_DIR')
        if env is not None:
            _CACHE_DIR_RESOLVED = env
        else:
            here = os.path.dirname(os.path.abspath(__file__))
            # 开发态 <root>/app/services、沙箱态 <root>/services，两种布局都找一遍
            for base in (os.path.dirname(os.path.dirname(here)),
                         os.path.dirname(here)):
                if os.path.isdir(os.path.join(base, 'data')):
                    _CACHE_DIR_RESOLVED = os.path.join(base, 'data', 'om_cache')
                    break
            else:
                _CACHE_DIR_RESOLVED = os.path.join(
                    os.path.dirname(os.path.dirname(here)), 'data', 'om_cache')
    return _CACHE_DIR_RESOLVED


def _disk_get(key: str, max_age: float = None):
    """磁盘命中返回 (mtime, data)；过期/缺失/损坏返回 None。

    ``max_age`` 缺省用 CACHE_TTL；额度用尽时的兜底读会用更大的
    ``STALE_MAX_AGE``（宁可旧数据，不把整份报告打死）。
    """
    d = _cache_dir()
    if not d:
        return None
    path = os.path.join(d, key + '.json')
    try:
        mtime = os.path.getmtime(path)
        if time.time() - mtime >= (CACHE_TTL if max_age is None else max_age):
            return None
        with open(path, encoding='utf-8') as f:
            return (mtime, json.load(f))
    except (OSError, ValueError):
        return None


def _disk_put(key: str, data) -> None:
    d = _cache_dir()
    if not d:
        return
    tmp = os.path.join(d, key + '.json.tmp')
    path = os.path.join(d, key + '.json')
    try:
        os.makedirs(d, exist_ok=True)
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, separators=(',', ':'))
        os.replace(tmp, path)          # 原子替换，避免读到写了一半的文件
    except (OSError, TypeError, ValueError):
        try:
            os.remove(tmp)
        except OSError:
            pass


def prune_disk_cache() -> int:
    """删除过期磁盘条目（返回删除数）。由调用方显式触发（app 启动时后台跑一次）。

    阈值取 ``max(CACHE_TTL, STALE_MAX_AGE)``：CACHE_TTL（3h）只决定「还算不算
    新鲜」，而额度被限流时兜底读的是 48h 内的旧数据——按 3h 删会把兜底数据
    清空（实测 data/om_cache 只剩 3h 档）。写了一半的 .tmp 一律删。
    """
    d = _cache_dir()
    if not d or not os.path.isdir(d):
        return 0
    keep_age = max(CACHE_TTL, STALE_MAX_AGE)
    now, removed = time.time(), 0
    try:
        names = os.listdir(d)
    except OSError:
        return 0
    for name in names:
        if not (name.endswith('.json') or name.endswith('.json.tmp')):
            continue
        p = os.path.join(d, name)
        try:
            if name.endswith('.tmp') or now - os.path.getmtime(p) >= keep_age:
                os.remove(p)
                removed += 1
        except OSError:
            pass
    return removed


def _session() -> requests.Session:
    """线程本地 Session：并行请求复用连接池（TLS 握手开销省 100-300ms/次）"""
    s = getattr(_local, 'session', None)
    if s is None:
        s = requests.Session()
        _local.session = s
    return s


def _domain(url: str) -> str:
    try:
        return url.split('/')[2]
    except Exception:
        return 'default'


def _throttle(url: str):
    """预约下一个可用时间槽（锁内 O(1)），到点前在锁外 sleep"""
    dom = _domain(url)
    now = time.time()
    with _THROTTLE_LOCK:
        nxt = max(_NEXT_SLOT.get(dom, 0.0) + MIN_INTERVAL, now)
        _NEXT_SLOT[dom] = nxt
    if nxt > now:
        time.sleep(nxt - now)


def _key_lock(key: str) -> threading.Lock:
    lock = _CACHE_LOCKS.get(key)
    if lock is None:
        with _CACHE_LOCKS_GUARD:
            lock = _CACHE_LOCKS.setdefault(key, threading.Lock())
    return lock


def _cache_key(url: str, params: dict) -> str:
    raw = url + json.dumps(params, sort_keys=True, ensure_ascii=False)
    return hashlib.md5(raw.encode()).hexdigest()


# ---- 额度韧性 v2（v2.34）：额度用尽/冷却时用过期磁盘缓存兜底 ----
# 共享出口 IP（家庭宽带 CGNAT、同 IP 线上租户）下，10k/日额度经常被
# 同 IP 的其他用户耗尽，而自己 3 小时 TTL 内根本没查几回——此时把磁盘里
# ≤STALE_MAX_AGE 的旧数据拿出来继续出报告，比「整份报告失败」有用得多。
# 服务过旧数据时记录年龄，report 层用 degraded_since() 如实标注。
STALE_MAX_AGE = int(os.environ.get('GC_OM_STALE_AGE') or 48 * 3600)
_DEGRADED = {'ts': 0.0, 'age_min': 0}
_DEGRADED_LOCK = threading.Lock()


def _mark_stale(age_s: float) -> None:
    with _DEGRADED_LOCK:
        _DEGRADED['ts'] = time.time()
        _DEGRADED['age_min'] = max(_DEGRADED['age_min'],
                                   int(age_s // 60) + (1 if age_s % 60 else 0))


def degraded_since(ts: float):
    """t0 之后是否有请求被过期缓存兜底过；返回最大数据年龄（分钟）或 None。"""
    with _DEGRADED_LOCK:
        if _DEGRADED['ts'] >= ts and _DEGRADED['age_min'] > 0:
            return _DEGRADED['age_min']
    return None


def _stale_fallback(key: str):
    """额度不可用时的兜底读：过期但 ≤STALE_MAX_AGE 的磁盘缓存。命中则记录年龄。"""
    stale = _disk_get(key, max_age=STALE_MAX_AGE)
    if stale is not None:
        _mark_stale(time.time() - stale[0])
        _mem_put(key, stale[0], stale[1])
        return stale[1]
    return None


# ---- 非 429 失败熔断（v2.37 审查报告 ⑦） ----
# 旧版只有 429 才进冷却：上游 5xx / DNS / 连接超时时，批次 1 的 6 个并发
# 任务各自重试 3 次（18 次请求）仍一无所获，还把线程池占满。现在连续
# FAIL_STREAK_TO_TRIP 次非 429 失败即短暂熔断，熔断期内直接走过期缓存兜底。
FAIL_STREAK_TO_TRIP = int(os.environ.get('GC_OM_FAIL_STREAK') or 6)
FAIL_COOLDOWN = int(os.environ.get('GC_OM_FAIL_COOLDOWN') or 120)
_FAIL = {'streak': 0, 'until': 0.0}
_FAIL_LOCK = threading.Lock()


def _note_failure() -> None:
    with _FAIL_LOCK:
        _FAIL['streak'] += 1
        if _FAIL['streak'] >= FAIL_STREAK_TO_TRIP and not _FAIL['until']:
            _FAIL['until'] = time.time() + FAIL_COOLDOWN
            log.warning('[om] 连续 %d 次非 429 失败，熔断 %ds（期间用过期缓存兜底）',
                        _FAIL['streak'], FAIL_COOLDOWN)


def _note_success() -> None:
    with _FAIL_LOCK:
        _FAIL['streak'] = 0
        _FAIL['until'] = 0.0


def fail_state():
    """非 429 失败的熔断状态：{'streak','remaining'} 或 None（未熔断）。"""
    with _FAIL_LOCK:
        left = _FAIL['until'] - time.time()
        if left > 0:
            return {'streak': _FAIL['streak'], 'remaining': int(left) + 1}
    return None


def clear_fail_breaker() -> None:
    with _FAIL_LOCK:
        _FAIL['streak'] = 0
        _FAIL['until'] = 0.0


def _get(url: str, params: dict, timeout: int = 40):
    key = _cache_key(url, params)
    hit = _CACHE.get(key)
    if hit and time.time() - hit[0] < CACHE_TTL:
        return hit[1]
    # 缓存未命中：按 key 加锁防击穿（并发同 key 只下载一次）
    with _key_lock(key):
        hit = _CACHE.get(key)
        if hit and time.time() - hit[0] < CACHE_TTL:
            return hit[1]
        # 磁盘缓存：重启不失忆；额度用尽时靠它继续出结果（全程不发请求）
        disk = _disk_get(key)
        if disk is not None:
            _mem_put(key, disk[0], disk[1])
            return disk[1]
        # 已确认上游额度不可用：先找过期但仍保留在磁盘的缓存兜底，
        # 真没有才不再逐个撞墙（一次报告有 11~13 个请求）
        cd = limit_state()
        if cd:
            stale = _stale_fallback(key)
            if stale is not None:
                return stale
            raise UpstreamLimitError(
                cd['scope'],
                detail='上游限流冷却中（未发请求，剩余 %ds）' % cd['remaining'])
        # 连续非 429 失败熔断中：同样不再撞墙，能兜底就兜底
        br = fail_state()
        if br:
            stale = _stale_fallback(key)
            if stale is not None:
                return stale
            raise RuntimeError(
                'Open-Meteo 连续失败已熔断（未发请求，剩余 %ds）' % br['remaining'])
        last_err = None
        for attempt in range(3):
            _throttle(url)
            try:
                resp = _session().get(url, params=params, timeout=timeout)
                if resp.status_code == 429:
                    scope, detail = _classify_429(resp)
                    _set_cooldown(scope)
                    if scope == 'daily':
                        stale = _stale_fallback(key)
                        if stale is not None:
                            return stale
                        raise UpstreamLimitError('daily', detail=detail)
                    last_err = '429 Too Many Requests'
                    try:
                        wait_s = min(15.0, max(1.0,
                                               float(resp.headers.get('Retry-After'))))
                    except (TypeError, ValueError):
                        wait_s = 2.0 * (attempt + 1)
                    if attempt < 2:
                        time.sleep(wait_s)
                    continue
                resp.raise_for_status()
                data = resp.json()
                _cache_put(key, data)
                _note_success()
                return data
            except UpstreamLimitError:
                raise
            except requests.exceptions.HTTPError as e:
                if e.response is not None and e.response.status_code == 429:
                    scope, detail = _classify_429(e.response)
                    _set_cooldown(scope)
                    if scope == 'daily':
                        stale = _stale_fallback(key)
                        if stale is not None:
                            return stale
                        raise UpstreamLimitError('daily', detail=detail)
                    last_err = '429'
                    continue
                # 5xx 计入熔断；4xx（除 429）是我们自己的参数错，重试无意义也不该熔断
                if e.response is not None and e.response.status_code >= 500:
                    _note_failure()
                raise
            except requests.exceptions.RequestException as e:
                last_err = str(e)
                _note_failure()
                time.sleep(2)
        raise RuntimeError(f'Open-Meteo 请求失败（已重试）: {last_err}')


def _coords_batches(lats, lons, size=120):
    """多坐标分批（避免 URL 过长）"""
    for i in range(0, len(lats), size):
        yield (','.join(str(x) for x in lats[i:i + size]),
               ','.join(str(x) for x in lons[i:i + size]))


def _split_range(date_str: str):
    """'2026-01-01' 或 '2026-01-01,2026-01-03' -> (start, end)"""
    parts = [p for p in date_str.split(',') if p]
    if len(parts) == 1:
        return parts[0], parts[0]
    return parts[0], parts[-1]


def fetch_hourly(lats, lons, hour_vars, models, date_str: str,
                 tz: str = 'Asia/Shanghai'):
    """多点多模式逐小时变量。date_str 可为单日或 '起,止' 区间。
    返回 {model: [ {var: [...], ...} per location ]}，时间轴为本地时区。"""
    start_s, end_s = _split_range(date_str)
    out = {m: [] for m in models}
    for lat_s, lon_s in _coords_batches(lats, lons):
        params = {
            'latitude': lat_s, 'longitude': lon_s,
            'hourly': ','.join(hour_vars),
            'models': ','.join(models),
            'start_date': start_s, 'end_date': end_s,
            'timezone': tz,
        }
        data = _get(FORECAST_URL, params)
        items = data if isinstance(data, list) else [data]
        for item in items:
            hourly = item.get('hourly', {})
            t = hourly.get('time', [])
            for m in models:
                rec = {'time': t}
                for v in hour_vars:
                    # 多模式请求返回 var_model 后缀；单模式请求返回裸 var
                    val = hourly.get(f'{v}_{m}')
                    if val is None:
                        val = hourly.get(v)
                    rec[v] = val
                out[m].append(rec)
    return out


def fetch_local_now(lats, lons, hour_vars, models, tz: str = 'Asia/Shanghai',
                    forecast_days: int = 3):
    """不限定日期的多天预报（用于 48h 表与逐时）。返回 {model: [rec]}"""
    out = {m: [] for m in models}
    for lat_s, lon_s in _coords_batches(lats, lons):
        params = {
            'latitude': lat_s, 'longitude': lon_s,
            'hourly': ','.join(hour_vars),
            'models': ','.join(models),
            'forecast_days': forecast_days,
            'timezone': tz,
        }
        data = _get(FORECAST_URL, params)
        items = data if isinstance(data, list) else [data]
        for item in items:
            hourly = item.get('hourly', {})
            t = hourly.get('time', [])
            for m in models:
                rec = {'time': t}
                for v in hour_vars:
                    # 多模式请求返回 var_model 后缀；单模式请求返回裸 var
                    val = hourly.get(f'{v}_{m}')
                    if val is None:
                        val = hourly.get(v)
                    rec[v] = val
                out[m].append(rec)
    return out


def fetch_aq(lats, lons, aq_vars, date_str: str, tz: str = 'Asia/Shanghai'):
    """空气质量（CAMS）：AOD/PM2.5/AQI。date_str 可为单日或 '起,止' 区间。
    返回 [rec] per location"""
    start_s, end_s = _split_range(date_str)
    out = []
    for lat_s, lon_s in _coords_batches(lats, lons):
        params = {
            'latitude': lat_s, 'longitude': lon_s,
            'hourly': ','.join(aq_vars),
            'start_date': start_s, 'end_date': end_s,
            'timezone': tz,
        }
        data = _get(AQ_URL, params)
        items = data if isinstance(data, list) else [data]
        for item in items:
            out.append(item.get('hourly', {}))
    return out


def hour_index(times, target_hh: int, date_prefix: str = None):
    """在本地时间轴上定位某个整点的下标；找不到返回最接近的（-1 = 无可用）。

    date_prefix（'YYYY-MM-DD'）非空时只在该日期内查找——多天数组（如 3 天
    fc3）必须传它，否则会取到今天的同一小时而非目标日。"""
    best, bi = None, -1
    for i, t in enumerate(times or []):
        if date_prefix and not str(t).startswith(date_prefix):
            continue
        try:
            hh = int(t[11:13])
        except Exception:
            continue
        if hh == target_hh:
            return i
        if best is None or abs(hh - target_hh) < best:
            best, bi = abs(hh - target_hh), i
    return bi


def _member_source(var: str, key: str):
    """从成员列名里取集合源标签（已归一）。

    `cloud_cover_mid_member07_ncep_gefs025` -> `ncep_gefs025`
    `cloud_cover_mid_member07_ecmwf_ifs025_ensemble` -> `ecmwf_ifs025`
    单源请求的列名没有源后缀（`cloud_cover_mid_member07`）-> None（视作主源）。
    """
    tail = key[len(var) + len('_member'):]
    num, _, src = tail.partition('_')
    if not num.isdigit() or not src:
        return None
    if src.endswith('_ensemble'):
        src = src[:-len('_ensemble')]
    return src


def _std(vals):
    if not vals:
        return None
    mu = sum(vals) / len(vals)
    return (sum((x - mu) ** 2 for x in vals) / len(vals)) ** 0.5


def fetch_ensemble(lats, lons, hour_vars, date_str: str,
                   tz: str = 'Asia/Shanghai', models=None):
    """多源集合预报（源与变量见 ENSEMBLE_MODELS / ENSEMBLE_TOTAL_MODELS，3h 分辨率）。
    date_str 可为区间。

    返回 [ { 'time': [...],
             var: {'mean': [...], 'std': [...], 'members': n,
                   'p10': [...], 'p50': [...], 'p90': [...],
                   'ec_mean': [...], 'ec_members': n_ec,
                   'sources': [...], 'src_spread': [...]} } ]

    - `mean/std/p10/p50/p90` 用**全部源的全部成员**算：成员越多，均值越稳，
      分位带也越真实（离散度直接进不确定区间措辞）。
    - `ec_mean/ec_members` 只取主源（ECMWF）——`ens_spread` 惩罚项沿用这个
      口径，避免动到已被 555 天标定吸收的数值。
    - `src_spread` 是各源**均值的标准差**：跨模式分歧，比成员内离散更能
      暴露"各家看法不一致"。
    """
    start_s, end_s = _split_range(date_str)
    src_models = list(models or ENSEMBLE_MODELS)
    out = []
    for lat_s, lon_s in _coords_batches(lats, lons):
        params = {
            'latitude': lat_s, 'longitude': lon_s,
            'hourly': ','.join(hour_vars),
            'models': ','.join(src_models),
            'start_date': start_s, 'end_date': end_s,
            'timezone': tz,
        }
        data = _get(ENSEMBLE_URL, params)
        items = data if isinstance(data, list) else [data]
        for item in items:
            hourly = item.get('hourly', {})
            t = hourly.get('time', [])
            rec = {'time': t}
            for v in hour_vars:
                pref = v + '_member'
                groups: dict[str, list] = {}
                for k in hourly:
                    if not k.startswith(pref):
                        continue
                    src = _member_source(v, k) or ENSEMBLE_PRIMARY
                    groups.setdefault(src, []).append(k)
                contributed: set[str] = set()   # 真有值的源（成员列全 null 的不算）
                mean_arr, std_arr = [], []
                p10_arr, p50_arr, p90_arr = [], [], []
                ec_mean_arr, src_spread_arr = [], []
                for i in range(len(t)):
                    vals, per_src = [], {}
                    for src, ks in groups.items():
                        sv = []
                        for k in ks:
                            arr = hourly.get(k) or []
                            if i < len(arr) and arr[i] is not None:
                                sv.append(arr[i])
                        if sv:
                            per_src[src] = sum(sv) / len(sv)
                            vals.extend(sv)
                            contributed.add(src)
                    if vals:
                        mu = sum(vals) / len(vals)
                        sd = _std(vals)
                        mean_arr.append(round(mu, 1))
                        std_arr.append(round(sd, 1))
                        vals.sort()
                        p10_arr.append(_pct(vals, 10))
                        p50_arr.append(_pct(vals, 50))
                        p90_arr.append(_pct(vals, 90))
                        ec_mean_arr.append(
                            round(per_src[ENSEMBLE_PRIMARY], 1)
                            if ENSEMBLE_PRIMARY in per_src else None)
                        spread = (_std(list(per_src.values()))
                                  if len(per_src) > 1 else None)
                        src_spread_arr.append(
                            round(spread, 1) if spread is not None else None)
                    else:
                        mean_arr.append(None)
                        std_arr.append(None)
                        p10_arr.append(None)
                        p50_arr.append(None)
                        p90_arr.append(None)
                        ec_mean_arr.append(None)
                        src_spread_arr.append(None)
                rec[v] = {'mean': mean_arr, 'std': std_arr,
                          'members': sum(len(groups[s]) for s in contributed),
                          'p10': p10_arr, 'p50': p50_arr, 'p90': p90_arr,
                          'ec_mean': ec_mean_arr,
                          'ec_members': len(groups.get(ENSEMBLE_PRIMARY, []))
                          if ENSEMBLE_PRIMARY in contributed else 0,
                          'sources': sorted(contributed),
                          'src_spread': src_spread_arr}
            out.append(rec)
    return out


def _pct(sorted_vals, q):
    """已排序序列的线性插值分位值（q 为 0~100）。空序列返回 None。"""
    if not sorted_vals:
        return None
    if len(sorted_vals) == 1:
        return round(sorted_vals[0], 1)
    pos = (len(sorted_vals) - 1) * q / 100.0
    lo = int(math.floor(pos))
    hi = min(lo + 1, len(sorted_vals) - 1)
    v = sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (pos - lo)
    return round(v, 1)


# 磁盘清理不再在导入期自动触发（v2.36）：import 一下就去删别人的缓存，是测试
# 与沙箱环境里不该有的写盘副作用。改由 app 启动时显式调用：
#   app/main.py: _startup_prune() -> threading.Thread(prune_disk_cache)

# -*- coding: utf-8 -*-
"""Open-Meteo 服务：缓存键/时间索引/缓存封顶/单飞锁（全部离线纯函数）。

额度相关：429 分级（日额度用尽必须快速失败）、进程内冷却、磁盘缓存。
免费层额度按 IP 计，共享出口 IP 下随时会被别人用光，这几条是"额度没了也
别把请求打成风暴、并且还能出结果"的底线。
"""
import os
import subprocess
import sys
import time

import pytest
import requests

from app.services import openmeteo as om


@pytest.fixture(autouse=True)
def _isolated_state(tmp_path, monkeypatch):
    """冷却/缓存都是进程级状态：逐用例隔离，且不要往 data/om_cache 写测试垃圾。"""
    monkeypatch.setenv('GC_OM_CACHE_DIR', str(tmp_path / 'om_cache'))
    monkeypatch.setattr(om, '_CACHE_DIR_RESOLVED', None)
    monkeypatch.setattr(om, 'MIN_INTERVAL', 0)   # 限速会让用例慢 0.8s/次
    om._CACHE.clear()
    om.clear_limit_cooldown()
    om.clear_fail_breaker()
    yield
    om._CACHE.clear()
    om.clear_limit_cooldown()
    om.clear_fail_breaker()


class _FakeResp:
    def __init__(self, status_code=200, payload=None, text='', headers=None):
        self.status_code = status_code
        self._payload = payload
        self.text = text
        self.headers = headers or {}

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.exceptions.HTTPError(response=self)


class _FakeSession:
    """按调用次序返回预设响应；超出则重复最后一个。记录调用次数。"""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = 0

    def get(self, url, params=None, timeout=None):
        self.calls += 1
        return self.responses[min(self.calls - 1, len(self.responses) - 1)]


DAILY_BODY = ('{"reason":"Daily API request limit exceeded. '
              'Please try again tomorrow.","error":true}')


def _use(monkeypatch, *responses):
    fs = _FakeSession(responses)
    monkeypatch.setattr(om, '_session', lambda: fs)
    return fs


# ---------------- 原有纯函数用例 ----------------

def test_hour_index_basic():
    times = ['2026-09-10T00:00', '2026-09-10T18:00']
    assert om.hour_index(times, 18) == 1
    assert om.hour_index(times, 0) == 0
    assert om.hour_index([], 8) == -1


def test_hour_index_date_prefix():
    times = ['2026-09-10T00:00', '2026-09-10T18:00',
             '2026-09-11T00:00', '2026-09-11T18:00']
    # 无前缀时取第一个匹配小时（旧行为，单日数组无歧义）
    assert om.hour_index(times, 18) == 1
    # 带日期前缀必须锁定到目标日（跨天数组的关键修复）
    assert om.hour_index(times, 18, '2026-09-10') == 1
    assert om.hour_index(times, 18, '2026-09-11') == 3
    assert om.hour_index(times, 18, '2026-12-01') == -1


def test_cache_key_deterministic_and_order_insensitive():
    u = om.FORECAST_URL
    assert om._cache_key(u, {'a': 1, 'b': 2}) == om._cache_key(u, {'b': 2, 'a': 1})
    assert om._cache_key(u, {'a': 1}) != om._cache_key(u, {'a': 2})


def test_split_range():
    assert om._split_range('2026-09-10') == ('2026-09-10', '2026-09-10')
    assert om._split_range('2026-09-10,2026-09-16') == ('2026-09-10', '2026-09-16')


def test_coords_batches():
    lats = list(range(5))
    lons = list(range(100, 105))
    batches = list(om._coords_batches(lats, lons, size=2))
    assert len(batches) == 3
    assert batches[0] == ('0,1', '100,101')
    assert batches[-1] == ('4', '104')


def test_cache_is_bounded():
    om._CACHE.clear()
    for i in range(om.CACHE_MAX + 100):
        om._cache_put(f'k{i}', {'i': i})
    assert len(om._CACHE) <= om.CACHE_MAX


def test_key_lock_single_flight_identity():
    lock1 = om._key_lock('unit-test-key')
    lock2 = om._key_lock('unit-test-key')
    assert lock1 is lock2


# ---------------- 429 分级与冷却 ----------------

def test_daily_limit_fails_fast_without_retry(monkeypatch):
    fs = _use(monkeypatch, _FakeResp(429, text=DAILY_BODY))
    with pytest.raises(om.UpstreamLimitError) as ei:
        om._get(om.FORECAST_URL, {'latitude': 1, 'longitude': 2})
    assert ei.value.scope == 'daily'
    assert fs.calls == 1                     # 重试无意义：只打一次
    assert om.limit_state()['scope'] == 'daily'


def test_cooldown_makes_further_calls_skip_network(monkeypatch):
    """一次报告有 11~13 个上游请求：冷却期内必须全部快速失败，不能再撞墙。"""
    fs = _use(monkeypatch, _FakeResp(429, text=DAILY_BODY))
    with pytest.raises(om.UpstreamLimitError):
        om._get(om.FORECAST_URL, {'latitude': 1, 'longitude': 2})
    assert fs.calls == 1
    t0 = time.time()
    with pytest.raises(om.UpstreamLimitError) as ei:
        om._get(om.FORECAST_URL, {'latitude': 9, 'longitude': 9})
    assert fs.calls == 1                     # 另一个 key 也没发请求
    assert time.time() - t0 < 0.5            # 且是立刻失败（不 sleep 退避）
    assert '冷却' in str(ei.value)


def test_burst_limit_retries_then_succeeds(monkeypatch):
    ok = _FakeResp(200, payload={'hourly': {'time': ['2026-09-14T00:00']}})
    fs = _use(monkeypatch,
              _FakeResp(429, text='Minutely API request limit exceeded',
                        headers={'Retry-After': '1'}), ok)
    monkeypatch.setattr(om.time, 'sleep', lambda *_: None)   # 跳过退避等待
    assert om._get(om.FORECAST_URL, {'latitude': 1, 'longitude': 2}) \
        == ok._payload
    assert fs.calls == 2
    assert om.limit_state()['scope'] == 'burst'


def test_limit_error_message_mentions_shared_ip():
    """提示要把"额度按 IP 计、共享出口会被别人用光"说清楚，否则用户会去查网络。"""
    msg = om.UpstreamLimitError('daily').user_message
    assert 'IP' in msg and '已用尽' in msg


# ---------------- 磁盘缓存 ----------------

def test_disk_cache_survives_memory_loss(monkeypatch):
    """uvicorn 无 --reload 时常重启：内存没了也不能重新打上游。"""
    p = {'latitude': 3, 'longitude': 4}
    fs = _use(monkeypatch, _FakeResp(200, payload={'hourly': {'x': [1]}}))
    om._get(om.FORECAST_URL, p)
    assert fs.calls == 1
    om._CACHE.clear()                        # 模拟进程重启
    assert om._get(om.FORECAST_URL, p) == {'hourly': {'x': [1]}}
    assert fs.calls == 1


def test_disk_cache_expires_after_ttl(monkeypatch):
    p = {'latitude': 3, 'longitude': 4}
    fs = _use(monkeypatch,
              _FakeResp(200, payload={'hourly': {'x': [1]}}),
              _FakeResp(200, payload={'hourly': {'x': [2]}}))
    om._get(om.FORECAST_URL, p)
    om._CACHE.clear()
    path = os.path.join(om._cache_dir(),
                        om._cache_key(om.FORECAST_URL, p) + '.json')
    old = time.time() - om.CACHE_TTL - 10
    os.utime(path, (old, old))
    assert om._get(om.FORECAST_URL, p) == {'hourly': {'x': [2]}}
    assert fs.calls == 2


def test_disk_cache_is_served_during_cooldown(monkeypatch):
    """额度用尽但磁盘有旧数据：应当拿旧数据把报告跑出来，而不是直接失败。"""
    p = {'latitude': 5, 'longitude': 6}
    fs = _use(monkeypatch, _FakeResp(200, payload={'hourly': {'x': [7]}}))
    om._get(om.FORECAST_URL, p)              # 先成功一次落到磁盘
    om._CACHE.clear()
    om._set_cooldown('daily')                # 之后额度被打光
    assert om._get(om.FORECAST_URL, p) == {'hourly': {'x': [7]}}
    assert fs.calls == 1


def test_ttl_aligned_with_model_cadence():
    """上游模式 6 小时一轮，TTL 必须明显长于旧值 30 分钟，否则纯属重复取数。"""
    assert om.CACHE_TTL >= 2 * 3600


def test_disk_cache_can_be_disabled(monkeypatch):
    monkeypatch.setenv('GC_OM_CACHE_DIR', '')
    monkeypatch.setattr(om, '_CACHE_DIR_RESOLVED', None)
    assert om._cache_dir() == ''
    om._cache_put('k', {'a': 1})             # 不应抛异常
    assert om._disk_get('k') is None
    assert om.prune_disk_cache() == 0


def test_prune_disk_cache_removes_expired(monkeypatch):
    """清理阈值 = max(CACHE_TTL, STALE_MAX_AGE)：3h 只决定「新鲜度」，
    删文件要按 48h 的兜底窗口来（否则限流时手里一条旧数据都不剩）。
    见 test_prune_keeps_files_inside_stale_window。"""
    om._cache_put('fresh', {'a': 1})
    om._cache_put('stale', {'a': 2})
    path = os.path.join(om._cache_dir(), 'stale.json')
    old = time.time() - max(om.CACHE_TTL, om.STALE_MAX_AGE) - 10
    os.utime(path, (old, old))
    removed = om.prune_disk_cache()
    assert removed >= 1
    assert os.path.exists(os.path.join(om._cache_dir(), 'fresh.json'))


# ---------------- 磁盘清理阈值（v2.36）：不得吃掉 48h 兜底窗口 ----------------

def _aged_cache_file(tmp_path, name, age_hours):
    d = tmp_path / 'om_cache'
    d.mkdir(parents=True, exist_ok=True)
    p = d / name
    p.write_text('{}', encoding='utf-8')
    t = time.time() - age_hours * 3600
    os.utime(p, (t, t))
    return p


def test_prune_keeps_files_inside_stale_window(tmp_path):
    """真实故障：清理按 3h 的 TTL 删，而额度兜底要读 48h 内的数据——
    `data/om_cache` 被清得只剩 3h 档，限额一断就没有任何可用兜底。
    """
    fresh = _aged_cache_file(tmp_path, 'fresh.json', 1)
    stale_ok = _aged_cache_file(tmp_path, 'stale30h.json', 30)
    expired = _aged_cache_file(tmp_path, 'expired.json', 72)
    half = _aged_cache_file(tmp_path, 'half.json.tmp', 0)   # 写了一半的残file

    removed = om.prune_disk_cache()

    assert not expired.exists(), '超出兜底窗口的才该删'
    assert not half.exists(), '半成品 .tmp 永远删'
    assert fresh.exists() and stale_ok.exists()
    assert removed == 2


def test_import_does_not_touch_disk(tmp_path):
    """导入模块不得有写盘副作用：测试/沙箱里 import 一下就去删别人的缓存。

    （用子进程真跑一次 import —— 这是唯一能证明「导入期没有后台清理线程」的
    办法；进程内 import 已经被测试收集阶段跑过了。）
    """
    d = tmp_path / 'om_cache'
    d.mkdir(parents=True, exist_ok=True)
    old = d / 'old.json'
    old.write_text('{}', encoding='utf-8')
    t = time.time() - 72 * 3600
    os.utime(old, (t, t))

    env = dict(os.environ, GC_OM_CACHE_DIR=str(d))
    r = subprocess.run([sys.executable, '-c',
                        'import app.services.openmeteo as om; '
                        'print(om.STALE_MAX_AGE)'],
                       cwd=os.getcwd(), env=env,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    assert r.returncode == 0, '子进程 import 失败'
    time.sleep(0.5)          # 导入期若真起了清理线程，这点时间足够删掉文件
    assert old.exists(), '导入模块时不应该清理磁盘缓存'


# ---------------- 额度韧性 v2（v2.34）：过期磁盘缓存兜底 ----------------

def _seed_stale(params, data, age_s):
    """写一条磁盘缓存并把 mtime 拨旧，模拟「TTL 已过但还在磁盘上」"""
    key = om._cache_key(om.FORECAST_URL, params)
    om._disk_put(key, data)
    p = os.path.join(om._cache_dir(), key + '.json')
    t = time.time() - age_s
    os.utime(p, (t, t))
    om._DEGRADED.update(ts=0.0, age_min=0)
    return key


def test_daily_limit_serves_stale_disk_cache(monkeypatch):
    """额度用尽：≤48h 的过期缓存兜底返回（比整份报告失败有用得多），
    并通过 degraded_since 如实记录数据年龄。"""
    params = {'latitude': 1.5, 'longitude': 2.5}
    payload = {'hourly': {'cloud_cover': [10]}}
    _seed_stale(params, payload, age_s=5 * 3600)     # 5h：超 TTL(3h)、在 48h 内
    fs = _use(monkeypatch, _FakeResp(429, text=DAILY_BODY))
    t0 = time.time()
    assert om._get(om.FORECAST_URL, params) == payload
    assert fs.calls == 1                             # 撞一次 429 后走兜底
    age = om.degraded_since(t0)
    assert age is not None and 300 <= age <= 302     # ≈300 分钟
    # 冷却期内、另一 key 无兜底缓存：仍然快速失败
    with pytest.raises(om.UpstreamLimitError):
        om._get(om.FORECAST_URL, {'latitude': 9.5, 'longitude': 9.5})
    assert fs.calls == 1


def test_stale_beyond_max_age_still_raises(monkeypatch):
    """超过 STALE_MAX_AGE 的缓存不能用（数据太旧误导决策），如实失败"""
    monkeypatch.setattr(om, 'STALE_MAX_AGE', 4 * 3600)   # 只容忍 4h
    params = {'latitude': 3.5, 'longitude': 4.5}
    _seed_stale(params, {'ok': 1}, age_s=5 * 3600)       # 超 TTL(3h) 也超容忍
    fs = _use(monkeypatch, _FakeResp(429, text=DAILY_BODY))
    with pytest.raises(om.UpstreamLimitError):
        om._get(om.FORECAST_URL, params)
    assert fs.calls == 1
    assert om.degraded_since(time.time() - 60) is None


def test_fresh_ttl_cache_beats_stale_and_marks_nothing(monkeypatch):
    """TTL 内的正常缓存优先，且不产生降级标记"""
    params = {'latitude': 5.5, 'longitude': 6.5}
    payload = {'fresh': True}
    om._disk_put(om._cache_key(om.FORECAST_URL, params), payload)
    fs = _use(monkeypatch, _FakeResp(429, text=DAILY_BODY))
    assert om._get(om.FORECAST_URL, params) == payload
    assert fs.calls == 0                             # 磁盘命中，根本没发请求
    assert om.degraded_since(time.time() - 60) is None


# ---------------- 非 429 失败熔断（v2.37 审查报告 ⑦） ----------------

class _DownSession:
    """永远连不上：模拟上游 5xx/DNS/连接超时（非 429）"""

    def __init__(self):
        self.calls = 0

    def get(self, url, params=None, timeout=None):
        self.calls += 1
        raise requests.exceptions.ConnectionError('upstream down')


def test_repeated_non_429_failures_trip_a_breaker(monkeypatch):
    """连续非 429 失败到阈值后必须停止撞墙。

    旧版只有 429 才熔断：上游 5xx/DNS 抖动时，批次 1 的 6 个并发任务
    各撞 3 次重试（= 18 次请求）却什么也拿不到。
    """
    fs = _DownSession()
    monkeypatch.setattr(om, '_session', lambda: fs)
    monkeypatch.setattr(om.time, 'sleep', lambda *_: None)

    with pytest.raises(RuntimeError):
        om._get(om.FORECAST_URL, {'latitude': 1.1, 'longitude': 2.2})
    assert fs.calls == 3                       # 第一条 key 老实重试 3 次

    while om.fail_state() is None:             # 撞到熔断阈值为止
        with pytest.raises(RuntimeError):
            om._get(om.FORECAST_URL, {'latitude': 1.1, 'longitude': 3.3,
                                      'n': fs.calls})

    before = fs.calls
    with pytest.raises(RuntimeError) as ei:
        om._get(om.FORECAST_URL, {'latitude': 1.1, 'longitude': 4.4})
    assert fs.calls == before                  # 熔断期内一次请求都不发
    assert om.fail_state()['remaining'] > 0
    assert '失败' in str(ei.value)


def test_breaker_resets_after_a_success():
    om._note_failure()
    om._note_failure()
    assert om._FAIL['streak'] == 2
    om._note_success()
    assert om._FAIL['streak'] == 0
    assert om.fail_state() is None


def test_breaker_serves_stale_cache_instead_of_raising(monkeypatch):
    """熔断期内仍给报告兜底：与额度冷却共用「≤48h 过期缓存」这条路。"""
    params = {'latitude': 22.27, 'longitude': 113.58}
    payload = {'hourly': {'time': ['2026-10-08T18:00']}}
    _seed_stale(params, payload, age_s=6 * 3600)
    om._FAIL.update({'streak': om.FAIL_STREAK_TO_TRIP,
                     'until': time.time() + 60})
    fs = _use(monkeypatch, _FakeResp(200, payload={'fresh': True}))
    t0 = time.time()
    assert om._get(om.FORECAST_URL, params) == payload
    assert fs.calls == 0                       # 没发请求，直接兜底
    assert om.degraded_since(t0) is not None   # 如实标注数据年龄


# ---------------- v2.37 多模式加料：登记表与多源集合 ----------------

class _CaptureSession:
    """记录每次出站请求的参数，并按 URL 返回预设 payload。"""

    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    def get(self, url, params=None, timeout=None):
        self.calls.append((url, dict(params or {})))
        return _FakeResp(200, payload=self.payload)


def test_every_model_has_explicit_weight_and_priority():
    """加新模式必须同时进 MODEL_WEIGHTS：`scoring.consensus` 对未登记模式
    默认给 0.25 权重（高于 ICON/CMA），会静默改变共识结果。"""
    assert set(om.MODELS) <= set(om.MODEL_WEIGHTS), '新模式缺权重'
    assert set(om.MODEL_PRIORITY) == set(om.MODELS)
    assert len(om.MODEL_PRIORITY) == len(om.MODELS)
    for name, mid in om.MODELS.items():
        assert isinstance(mid, str) and mid, name


def test_model_registry_covers_extra_sources():
    """B 套餐：确定性模式从 4 个扩到 7 个（实测都有华南数据）。"""
    assert len(om.MODELS) == 7
    assert om.MODELS['JMA'] == 'jma_gsm'
    assert om.MODELS['GEM'] == 'gem_global'
    assert om.MODELS['UKMO'] == 'ukmo_global_deterministic_10km'
    assert om.MODEL_PRIORITY[0] == 'ECMWF'


def test_fetch_ensemble_asks_for_multiple_sources(monkeypatch):
    payload = {'hourly': {'time': ['2026-10-09T18:00'],
                          'cloud_cover_mid_member01_ecmwf_ifs025_ensemble': [30],
                          'cloud_cover_mid_member02_ecmwf_aifs025': [60]}}
    cs = _CaptureSession(payload)
    monkeypatch.setattr(om, '_session', lambda: cs)
    om.fetch_ensemble([22.27], [113.58], ['cloud_cover_mid'], '2026-10-09')
    assert len(cs.calls) == 1
    models = cs.calls[0][1].get('models', '')
    assert 'ecmwf_ifs025' in models
    assert ',' in models, '一次请求要带多个集合源（多一个源只多几分钱）'


def test_ensemble_default_sources_are_the_two_that_have_cloud_members(monkeypatch):
    """实测（2026-10-08）：云量成员**只有 ECMWF 系**提供——ecmwf_ifs025 与
    ecmwf_aifs025 各 50 个成员有值，而 gfs025/gfs05/icon_global/gem_global/
    ukmo_global_ensemble_20km 的云量成员列全是 null。默认源必须落在这两家，
    否则 mean 恒等于 ec_mean、src_spread 恒为 None，"多源"只是假象。
    """
    payload = {'hourly': {'time': ['2026-10-09T18:00'],
                          'cloud_cover_mid_member01_ecmwf_ifs025_ensemble': [30]}}
    cs = _CaptureSession(payload)
    monkeypatch.setattr(om, '_session', lambda: cs)
    om.fetch_ensemble([22.27], [113.58], ['cloud_cover_mid'], '2026-10-09')
    models = cs.calls[0][1]['models'].split(',')
    assert 'ecmwf_ifs025' in models
    assert 'ecmwf_aifs025' in models
    assert 'gfs025' not in models and 'icon_global' not in models


def test_ensemble_drops_sources_that_return_no_values(monkeypatch):
    """某源成员列全 null（GEFS/ICON 对云量就是这样）时，不能把它算进
    sources/members——否则文案会吹"共 3 源"，而均值其实只来自 1 源。"""
    payload = {'hourly': {
        'time': ['2026-10-09T18:00'],
        'cloud_cover_mid_member01_ecmwf_ifs025_ensemble': [30],
        'cloud_cover_mid_member02_ecmwf_ifs025_ensemble': [50],
        'cloud_cover_mid_member01_ncep_gefs025': [None],
        'cloud_cover_mid_member02_ncep_gefs025': [None],
    }}
    cs = _CaptureSession(payload)
    monkeypatch.setattr(om, '_session', lambda: cs)
    out = om.fetch_ensemble([22.27], [113.58], ['cloud_cover_mid'],
                            '2026-10-09')
    d = out[0]['cloud_cover_mid']
    assert d['sources'] == ['ecmwf_ifs025']
    assert d['members'] == 2
    assert d['mean'] == [40.0]
    assert d['src_spread'] == [None], '只有一源贡献时不该编出跨源分歧'


def test_fetch_ensemble_merges_sources_and_keeps_ecmwf_breakdown(monkeypatch):
    """合并成员算离散度，同时保留 ECMWF 单源均值（ens_spread 惩罚项
    的数值口径已被 555 天标定吸收，不能跟着变）。"""
    payload = {'hourly': {
        'time': ['2026-10-09T18:00'],
        'cloud_cover_mid_member01_ecmwf_ifs025_ensemble': [20],
        'cloud_cover_mid_member02_ecmwf_ifs025_ensemble': [40],
        'cloud_cover_mid_member01_ecmwf_aifs025': [60],
        'cloud_cover_mid_member02_ecmwf_aifs025': [80],
    }}
    cs = _CaptureSession(payload)
    monkeypatch.setattr(om, '_session', lambda: cs)
    out = om.fetch_ensemble([22.27], [113.58], ['cloud_cover_mid'],
                            '2026-10-09')
    d = out[0]['cloud_cover_mid']
    assert d['members'] == 4
    assert d['mean'] == [50.0]                  # (20+40+60+80)/4
    assert d['ec_mean'] == [30.0]               # 只 ECMWF 两个成员
    assert d['ec_members'] == 2
    assert d['sources'] == ['ecmwf_aifs025', 'ecmwf_ifs025']
    assert d['src_spread'] == [20.0]            # IFS均值30 vs AIFS均值70 的总体标准差
    assert d['p10'][0] < d['p50'][0] < d['p90'][0]


def test_fetch_ensemble_single_source_without_suffix_still_works(monkeypatch):
    """老口径（单源、成员键无源后缀）必须继续可用。"""
    payload = {'hourly': {'time': ['2026-10-09T18:00'],
                          'cloud_cover_mid_member01': [10],
                          'cloud_cover_mid_member02': [30]}}
    cs = _CaptureSession(payload)
    monkeypatch.setattr(om, '_session', lambda: cs)
    out = om.fetch_ensemble([22.27], [113.58], ['cloud_cover_mid'],
                            '2026-10-09')
    d = out[0]['cloud_cover_mid']
    assert d['members'] == 2
    assert d['mean'] == [20.0]
    assert d['ec_mean'] == [20.0]
    assert d['sources'] == [om.ENSEMBLE_PRIMARY]
    assert d['src_spread'] == [None]

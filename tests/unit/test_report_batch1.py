"""批次 1/2 取数降级：单个数据源挂掉不能让整份报告变成不透明 500。

真实故障（2026-10-07 审查报告 P0）：`app/services/report.py` 批次 1 提交的
10 个任务里只有 `f_ens`/`f_fc7` 包了 `_safe`，其余 `.result()` 直接把异常抛给
`app/main.py` 的兜底 `except Exception: _server_error('API 请求失败')`——用户只
看到「API 请求失败」，既不知道缺了什么数据，也拿不到还能用的那部分结论。

这里用 `om._get` 这一个接缝伪造上游（其余解析/评分/装配全走真实代码），
验证：①单源失败 → 仍有报告 + `data_missing` 如实标注；②全源失败 → 不抛异常。
"""
from datetime import datetime, timedelta

import app.services.openmeteo as om
import app.services.report as rpt

LAT, LON = 22.2710, 113.5767
DATE = '2026-09-10'          # 过去日期：临近校准窗口外，不触发卫星/和风取数


def _times(start, end):
    d = datetime.strptime(start, '%Y-%m-%d')
    d1 = datetime.strptime(end, '%Y-%m-%d')
    out = []
    while d <= d1:
        for h in range(24):
            out.append(d.strftime('%Y-%m-%d') + 'T%02d:00' % h)
        d += timedelta(days=1)
    return out


def _value_for(var, model=None, diverge=None):
    """给每个变量一个物理上说得过去的值，避免用 0/None 掩盖真实的健壮性问题。

    diverge 指定某个模式时，该模式的云量刻意唱反调（用来验证扩展模式
    只做旁证、不改经标定的主概率）。
    """
    if diverge and model == diverge and 'cloud_cover' in var:
        return 2.0
    if 'aerosol_optical_depth' in var:
        return 0.18
    if 'relative_humidity' in var:
        return 55.0
    if 'temperature' in var:
        return 24.0
    if 'geopotential' in var:
        return 5500.0
    if var.startswith('pressure'):
        return 1008.0
    if 'visibility' in var:
        return 20000.0
    if 'precipitation' in var:
        return 10.0
    return 40.0              # 云量等


def _install_om_stub(monkeypatch, fail=(), ens_suffixed=False, diverge=None):
    """`om._get` 是唯一的出站接缝：只伪造它，批次 1/2 之后的全部逻辑都跑真的。

    ens_suffixed=True 时集合成员列名带源后缀（与真实 api 一致），且两源均值
    明显不同——用来验证「ens_spread 仍只按 ECMWF 成员算」。
    diverge=<model_id> 时该模式返回「无云」的相反值。
    """
    def fake_get(url, params, timeout=40):
        if url in fail:
            raise RuntimeError('stub upstream down: %s' % url)
        n = len(str(params.get('latitude', '')).split(','))
        if 'elevation' in url:
            return {'elevation': [10.0] * n}
        if 'start_date' in params:
            t = _times(params['start_date'], params.get('end_date')
                       or params['start_date'])
        else:                                  # fetch_local_now：forecast_days
            today = datetime.now().date()
            last = today + timedelta(days=int(params.get('forecast_days', 3)) - 1)
            t = _times(today.strftime('%Y-%m-%d'), last.strftime('%Y-%m-%d'))
        models = [m for m in str(params.get('models', '')).split(',') if m]
        items = []
        for _ in range(n):
            hourly = {'time': t}
            for v in params['hourly'].split(','):
                for m in models:
                    hourly['%s_%s' % (v, m)] = [
                        _value_for(v, m, diverge)] * len(t)
                hourly[v] = [_value_for(v)] * len(t)
                if url == om.ENSEMBLE_URL:     # 集合成员（50 成员取小的等价样本）
                    if v == 'cloud_cover':
                        # 总云量是唯一有跨源成员的变量（实测：低/中/高分层只有
                        # ECMWF 系两家有值）。四个源的均值刻意拉开，验证跨源分歧。
                        for src, base in (('ecmwf_ifs025_ensemble', 20),
                                          ('ncep_gefs025', 45),
                                          ('icon_global_eps', 70),
                                          ('ukmo_global_ensemble_20km', 30)):
                            for mi in range(10):
                                hourly['%s_member%02d_%s' % (v, mi, src)] = [
                                    base + mi] * len(t)
                    elif ens_suffixed:
                        for src, base in (('ecmwf_ifs025_ensemble', 20),
                                          ('ncep_gefs025', 60)):
                            for mi in range(10):
                                hourly['%s_member%02d_%s' % (v, mi, src)] = [
                                    base + mi] * len(t)
                    else:
                        for mi in range(10):
                            hourly['%s_member%02d' % (v, mi)] = [
                                _value_for(v) + mi] * len(t)
            items.append({'hourly': hourly})
        return items[0] if n == 1 else items

    monkeypatch.setattr(om, '_get', fake_get)


def _spy(monkeypatch):
    """在已装好的桩外面再包一层，记录出站请求（用来断言「取数口径」）。"""
    real = om._get
    seen = []

    def spy(url, params, timeout=40):
        seen.append((url, dict(params)))
        return real(url, params, timeout)

    monkeypatch.setattr(om, '_get', spy)
    return seen


def _offline(monkeypatch):
    """去掉与数据源无关的外部件：地形探测、卫星 P-Tree 预取。"""
    monkeypatch.setattr(rpt, '_probe_site', lambda lat, lon: (None, None))
    monkeypatch.setattr(rpt.sat, 'arm_event', lambda *a, **kw: None)


def test_grid_fetch_is_coarsened(monkeypatch):
    """省域网格是单次报告的最大额度开销，取数必须抽稀到 0.5°（≈64 点）。

    2026-10-07 审查报告 2.3：16 个请求里省网格 6 个（`f_gfc` 3 批 + `f_gaq`
    3 批，各 120+120+10 点）就占 690 加权调用里的 550。抽稀只影响地图点阵的
    疏密（前端是离散散点、不是插值热力图），不触碰评分口径。
    """
    _offline(monkeypatch)
    _install_om_stub(monkeypatch)
    real_get = om._get
    seen = []

    def spy(url, params, timeout=40):
        lats = str(params.get('latitude', '')).split(',')
        # 省网格是唯一「大批量」的取数：多点且点数远超光路/AQ 城市那十来个点
        if len(lats) > 40 and 'elevation' not in url:
            seen.append((url, tuple(lats), tuple(str(params.get('longitude', '')).split(','))))
        return real_get(url, params, timeout)

    monkeypatch.setattr(om, '_get', spy)
    rpt.build_report(LAT, LON, DATE, 'sunset')

    grid_pts = {p for _u, lats, lons in seen for p in zip(lats, lons)}
    assert len(grid_pts) == 64, '省网格应抽稀到 0.5°（64 点），实际 %d' % len(grid_pts)
    assert all(len(lats) <= 120 for _u, lats, _l in seen), '单批不得超过 _coords_batches 的 120'


def test_partial_source_failure_degrades_with_label(monkeypatch):
    """空气质量源（CAMS）挂掉：报告照出，并在 data_missing 里写明缺了哪一路。"""
    _offline(monkeypatch)
    _install_om_stub(monkeypatch, fail=(om.AQ_URL,))

    r = rpt.build_report(LAT, LON, DATE, 'sunset')

    assert r['models'], '预报源正常时模式结论不该丢'
    assert 'air-quality' in r['data_missing']
    assert r['probability'] is not None


def test_all_sources_down_returns_report_not_500(monkeypatch):
    """上游全挂：仍要返回可渲染的报告（概率退化 + 标注），而不是抛异常。"""
    _offline(monkeypatch)
    _install_om_stub(monkeypatch, fail=(om.FORECAST_URL, om.AQ_URL,
                                        om.ENSEMBLE_URL,
                                        'https://api.open-meteo.com/v1/elevation'))

    r = rpt.build_report(LAT, LON, DATE, 'sunset')

    assert r['models'] == {}
    assert r['main_model'] is None
    assert r['data_missing'], '全源失败必须如实标注'
    assert 'forecast' in r['data_missing']
    assert r['probability'] == 0 or r['probability'] is None


# ---------------- v2.37 精度套餐：取数口径必须真的变密/变宽 ----------------
# 这些断言盯的是「有没有真的多发数据」，而不是分数：分数口径一旦改动会
# 破坏 555 天标定的概率映射（data/calibration_v1.json 按旧采样拟合）。


def test_path_samples_are_densified():
    """光路采样 9 → 16 点：AOD/云量沿光路的分辨率提高，端点与 310km 保持不变
    （受光用例按 310km 取索引）。"""
    d = rpt.PATH_DISTANCES
    assert len(d) >= 16, '光路采样仍是 %d 点，没加密' % len(d)
    assert d[0] == 0 and d[-1] == 580, '端点不能变（光路仍是机位→580km）'
    assert 310 in d, '310km 是受光判据的索引锚点，必须保留'
    assert d == sorted(d), '采样距离必须单调'


def test_sight_distances_are_densified():
    """悬日月视线采样 16 → 24 点：交点距离与最近采样点的 gap 更小。"""
    d = rpt.SIGHT_DISTANCES_KM
    assert len(d) >= 24, '视线采样仍是 %d 点，没加密' % len(d)
    assert d == sorted(d)
    assert d[0] == 1.5 and d[-1] == 900, '覆盖范围不变，只提高分辨率'


def test_pressure_levels_fetch_ecmwf_and_gfs(monkeypatch):
    """气压层廓线要 ECMWF + GFS 双源：受光判据/探空都建在这条廓线上。"""
    _offline(monkeypatch)
    _install_om_stub(monkeypatch)
    seen = _spy(monkeypatch)

    rpt.build_report(LAT, LON, DATE, 'sunset')

    pl = [p for _u, p in seen
          if 'relative_humidity_1000hPa' in p.get('hourly', '')]
    assert pl, '气压层请求没发出去'
    assert set(pl[0]['models'].split(',')) == {'ecmwf_ifs025', 'gfs_global'}


def test_local_now_covers_two_weeks(monkeypatch):
    """本地实时预报 3 → 7 → 14 天（14 天内加长时间不额外计费）。

    规格变更（v2.38）：用户口径是「宁可多花额度换精度」，14 天窗口不额外计费，
    这张 3 小时步长的表本来就是**全量渲染**，拉长即多得两周逐时云况。
    """
    _offline(monkeypatch)
    _install_om_stub(monkeypatch)
    seen = _spy(monkeypatch)

    rpt.build_report(LAT, LON, DATE, 'sunset')

    ln = [p for _u, p in seen if p.get('forecast_days')]
    assert ln, '本地实时请求没发出去'
    assert int(ln[0]['forecast_days']) >= 14, (
        '本地实时仍只有 %s 天' % ln[0]['forecast_days'])


def test_outlook_covers_two_weeks(monkeypatch):
    """7 天展望 → 14 天：概览格子数与取数区间必须一起走，否则后面 7 天没数据。"""
    _offline(monkeypatch)
    _install_om_stub(monkeypatch)
    seen = _spy(monkeypatch)

    r = rpt.build_report(LAT, LON, DATE, 'sunset')

    fc7 = [p for _u, p in seen
           if 'precipitation_probability' in p['hourly'].split(',')
           and 'visibility' not in p['hourly'].split(',')]
    assert fc7, '展望请求没发出去'
    span = (datetime.strptime(fc7[0]['end_date'], '%Y-%m-%d')
            - datetime.strptime(fc7[0]['start_date'], '%Y-%m-%d')).days
    assert span >= 13, '展望取数仍是 %d 天' % (span + 1)
    assert len(r['week']) == 14, '概览仍是 %d 格' % len(r['week'])


def test_ensemble_total_cloud_uses_four_sources(monkeypatch):
    """集合源 3~4 个：分层云量只有 ECMWF 系两家有成员（实测），但**总云量**有
    ECMWF / GEFS / ICON-EPS / UKMO 四家 ⇒ 总云量单独再发一次多源集合请求。"""
    _offline(monkeypatch)
    _install_om_stub(monkeypatch)
    seen = _spy(monkeypatch)

    rpt.build_report(LAT, LON, DATE, 'sunset')

    tot = [p for u, p in seen
           if u == om.ENSEMBLE_URL and 'cloud_cover' in p['hourly'].split(',')]
    assert tot, '总云量集合请求没发出去'
    assert len(tot[0]['models'].split(',')) >= 4, (
        '总云量仍只有 %s' % tot[0]['models'])


def test_ensemble_total_cross_source_spread(monkeypatch):
    """四源总云量要给出跨源分歧：各源均值 24.5/49.5/74.5/34.5 ⇒ 标准差≈18.8。

    这个数字是「各家集合系统对总云量看法差多少」的唯一量化，必须进载荷与风险提示。
    """
    _offline(monkeypatch)
    _install_om_stub(monkeypatch)

    r = rpt.build_report(LAT, LON, DATE, 'sunset')

    assert r['ens_total_sources'] == ['ecmwf_ifs025', 'icon_global_eps',
                                      'ncep_gefs025', 'ukmo_global_ensemble_20km'], \
        '总云量集合源不对：%r' % (r.get('ens_total_sources'),)
    assert r['ens_total_src_spread'] >= 15, (
        '跨源分歧没量化：%r' % r.get('ens_total_src_spread'))
    assert any('集合系统' in x for x in r['risks']), '跨源分歧没进风险提示'


def test_align_sightline_uses_three_models(monkeypatch):
    """视线剖面从 2 个模式扩到 3 个：model_spread 才有意义。"""
    _offline(monkeypatch)
    _install_om_stub(monkeypatch)
    seen = _spy(monkeypatch)

    rpt.build_alignment(LAT, LON, DATE, 270.0)

    sl = [p for _u, p in seen if 'cloudcover_1000hPa' in p.get('hourly', '')]
    assert sl, '视线剖面请求没发出去'
    for p in sl:
        assert len(p['models'].split(',')) >= 3, '视线剖面仍是 2 模式：%s' % p['models']


def test_align_outlook_covers_fourteen_days(monkeypatch):
    """悬日月天气前瞻 7 → 14 天：窗口落在第 8~14 天时不再退化成「只给几何」。"""
    _offline(monkeypatch)
    _install_om_stub(monkeypatch)
    seen = _spy(monkeypatch)

    rpt.build_alignment(LAT, LON, DATE, 270.0)

    fc = [p for _u, p in seen
          if 'start_date' in p and 'pressure_msl' in p.get('hourly', '')]
    assert fc, '悬日月主预报请求没发出去'
    span = (datetime.strptime(fc[0]['end_date'], '%Y-%m-%d')
            - datetime.strptime(fc[0]['start_date'], '%Y-%m-%d')).days
    assert span >= 13, '前瞻仍是 %d 天' % (span + 1)


def test_ensemble_spread_still_uses_ecmwf_only(monkeypatch):
    """ens_spread 是 consensus 惩罚项的输入、已被 555 天标定吸收：
    多源集合只影响不确定区间，不能改这个数字（ECMWF 成员 20..29 → 均值 24.5；
    若混入 GEFS 的 60..69 会变成 44.5）。"""
    _offline(monkeypatch)
    _install_om_stub(monkeypatch, ens_suffixed=True)

    r = rpt.build_report(LAT, LON, DATE, 'sunset')

    assert r['ens_spread'] == 24.5, '多源集合污染了标定口径：%r' % r.get('ens_spread')
    assert r['ens_sources'] == ['ecmwf_ifs025', 'ncep_gefs025']


def test_consensus_keeps_the_calibrated_core_models(monkeypatch):
    """主概率（要过 555 天标定曲线）只能用核心四模式的均值/离散度。

    实测（2026-10-08 珠海，真实数据）：GEM 报「全天无高云」，其余 6 家报 47~100%
    高云，把 7 家一起送进 `sc.consensus` 会让 raw 从 76.5 掉到 66.1、概率 76→65
    ——刚好跨过「小烧 / 微烧」的判定线。而校准曲线（CALIB_PTS_PRD）是拿核心
    四模式的 raw 分布拟合的，所以扩展模式只能当旁证，不能改判据本身。
    """
    _offline(monkeypatch)
    _install_om_stub(monkeypatch)
    base = rpt.build_report(LAT, LON, DATE, 'sunset')

    _install_om_stub(monkeypatch, diverge='gem_global')   # 扩展模式唱反调
    div = rpt.build_report(LAT, LON, DATE, 'sunset')

    assert div['probability'] == base['probability'], (
        '扩展模式（GEM）改动了经标定的主概率：%r → %r'
        % (base['probability'], div['probability']))
    assert div['ext_spread'] >= 20, '扩展模式分歧必须如实量化：%r' % div.get('ext_spread')
    assert any('扩展模式' in x for x in div['risks']), '分歧没有进入风险提示'
    assert div['models']['GEM']['probability'] < div['models']['ECMWF']['probability'], \
        '逐模式展示仍要如实反映唱反调的那家'

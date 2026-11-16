# -*- coding: utf-8 -*-
"""8 维评分引擎：维度取值域、加权合成上限、共识与校准映射。"""
import pytest

from app.core import scoring as sc

DIM_FUNCS = [
    lambda: sc.dim1_midhigh_canvas(40, 30, 10),
    lambda: sc.dim2_path_canvas([30, 40, 50], [10, 20, 30], [50, 100, 160])[0],
    lambda: sc.dim3_precip(0.5, 1.0, 60),
    lambda: sc.dim4_visibility_pm25(15000, 20),
    lambda: sc.dim5_cloud_trend(50, 30),
    lambda: sc.dim6_pressure(1010, 1008),
    lambda: sc.dim7_aod(0.2, 0.3),
    lambda: sc.dim8_high_moisture(40, 50, 60),
]


@pytest.mark.parametrize('fn', DIM_FUNCS)
def test_dimensions_in_range(fn):
    v = fn()
    assert isinstance(v, (int, float))
    assert 0.0 <= v <= 100.0


def test_calibrated_probability_monotonic():
    xs = list(range(0, 131, 5))
    ps = [sc.calibrated_probability(x) for x in xs]
    assert all(b >= a - 1e-9 for a, b in zip(ps, ps[1:]))
    assert all(0 <= p <= 100 for p in ps)


def test_verdict_word_thresholds():
    assert sc.verdict_word(0) == '不烧'
    assert sc.verdict_word(10) == '微烧'
    assert sc.verdict_word(20) == '小烧'
    assert sc.verdict_word(30) == '中烧'
    assert sc.verdict_word(50) == '大烧'


def test_combine_caps():
    dims = {k: 100.0 for k in sc.WEIGHTS}
    assert sc.combine(dims, feasible=True, dead_sky=False) == 100.0
    assert sc.combine(dims, feasible=True, dead_sky=True) <= 8.0
    assert sc.combine(dims, feasible=False, dead_sky=False) <= 25.0


def test_consensus_empty():
    r = sc.consensus({}, {})
    assert r['probability'] == 0.0
    assert r['credibility'] == '无数据'


def test_consensus_identical_models_no_spread():
    weights = {'ECMWF': 1.0, 'GFS': 1.0, 'ICON': 1.0, 'CMA': 1.0}
    scores = {k: 60 for k in weights}
    r = sc.consensus(scores, weights)
    assert r['std'] == 0.0
    assert r['probability'] == round(sc.calibrated_probability(60))


def test_consensus_credibility_uses_real_dispersion():
    """credibility 措辞要用真实离散度（ens_disp），不能用 ens_spread。

    ens_spread 实为"50 成员中高云量的均值"，曾被当作离散度展示，于是同一个
    51% 既能说"云多"又能说"模式分歧大"——旁边一行的 P10~P90 半宽会与它矛盾。
    """
    weights = {'ECMWF': 1.0, 'GFS': 1.0}
    scores = {'ECMWF': 60, 'GFS': 62}
    r = sc.consensus(scores, weights, ens_spread=51.0, ens_disp=5.0)
    assert '高度一致' in r['credibility'], r['credibility']
    assert '51' not in r['credibility']
    r2 = sc.consensus(scores, weights, ens_spread=51.0, ens_disp=25.0)
    assert '半宽 ±25%' in r2['credibility'], r2['credibility']
    r3 = sc.consensus(scores, weights, ens_spread=51.0)
    assert '51' not in r3['credibility'], r3['credibility']
    assert r3['ens_disp'] is None
    # ens_pen 仍按 ens_spread 走：数值口径不动，555 天标定不被破坏
    assert (sc.consensus(scores, weights, ens_spread=51.0)['raw']
            < sc.consensus(scores, weights, ens_spread=0.0)['raw'])


# ---------------- v2.35 地区分流校准（R-003-s2） ----------------

def test_region_for_box():
    """珠三角框内 'prd'，框外（上海）与边界值各验一例"""
    assert sc.region_for(22.579, 113.082) == 'prd'   # 中山
    assert sc.region_for(23.111, 114.416) == 'prd'   # 惠州
    assert sc.region_for(31.2397, 121.4998) == 'sh'  # 上海
    assert sc.region_for(21.0, 111.5) == 'prd'       # 框角（含边界）
    assert sc.region_for(20.9, 113.0) == 'sh'
    assert sc.region_for(24.0, 113.0) == 'sh'


def test_calibrated_probability_prd_monotonic_and_higher():
    """PRD 曲线单调、值域合法；中分段显著高于上海曲线（基率 77.6% vs 27.7%）"""
    xs = list(range(0, 131, 5))
    ps = [sc.calibrated_probability(x, 'prd') for x in xs]
    assert all(b >= a - 1e-9 for a, b in zip(ps, ps[1:]))
    assert all(0 <= p <= 100 for p in ps)
    # 上海曲线在中分段几乎归零（raw 50 -> 2.75%），PRD 应在 60% 以上
    assert sc.calibrated_probability(50) <= 5.0
    assert sc.calibrated_probability(50, 'prd') >= 60.0
    # 高分段两条曲线收敛（判别力同源）
    assert abs(sc.calibrated_probability(95) - sc.calibrated_probability(95, 'prd')) < 5.0


def test_calibrated_probability_default_is_shanghai():
    """不传 region 与传 'sh' 同值——默认路径零变化，旧调用点不迁移也正确"""
    for x in (0, 30, 60, 90, 120):
        assert sc.calibrated_probability(x) == sc.calibrated_probability(x, 'sh')


def test_verdict_word_prd_thresholds():
    """PRD 判定词阈值按新语义重定（不烧<50/微烧<65/小烧<75/中烧<85/大烧）"""
    assert sc.verdict_word(0, 'prd') == '不烧'
    assert sc.verdict_word(55, 'prd') == '微烧'
    assert sc.verdict_word(70, 'prd') == '小烧'
    assert sc.verdict_word(80, 'prd') == '中烧'
    assert sc.verdict_word(90, 'prd') == '大烧'
    # 上海阈值不受影响
    assert sc.verdict_word(55) == '大烧'
    assert sc.verdict_word(20) == '小烧'


def test_consensus_region_passthrough():
    """consensus 的 region 透传到校准映射"""
    weights = {'ECMWF': 1.0}
    r = sc.consensus({'ECMWF': 70}, weights, region='prd')
    raw = r['raw']
    assert r['probability'] == round(sc.calibrated_probability(raw, 'prd'))

# -*- coding: utf-8 -*-
"""8 维度评分引擎
权重与维度设计对齐 sample.pdf（TAKO 珠海晚霞报告），打分逻辑基于教程理论：
① 中高层云分布 23%   ② 光路城市云况 30%   ③ 近地降水 10%   ④ 能见度/颗粒物 11%
⑤ 云运动趋势 3%      ⑥ 气压变化 1%        ⑦ CAMS 霾/气溶胶 12%
⑧ 高层湿层卷云潜力 10%
"""
import math


def _clamp(x, lo=0.0, hi=100.0):
    return max(lo, min(hi, x))


def _trapz(x, lo, hi, fall_lo, fall_hi, floor=30.0):
    """梯形打分：[lo,hi] 满分，向两侧线性跌到 floor"""
    if lo <= x <= hi:
        return 100.0
    if x < lo:
        return _clamp(floor + (100 - floor) * (x - fall_lo) / (lo - fall_lo)
                      if fall_lo < lo else 0)
    return _clamp(floor + (100 - floor) * (fall_hi - x) / (fall_hi - hi)
                  if fall_hi > hi else 0)


# ------------------------------------------------ 维度打分

def dim1_midhigh_canvas(mid, high, low):
    """① 中高层云分布（网格模式口径，2026-09-04 回测实证校准）：
    中+高云合计（画布）100~125% 最佳（薄卷云/高层云幕，实测烧率 44.5%），
    >150% 重罚（厚多层云封天，实测 14.3%），<25% 无画布（20.3%）。
    低云 <30% 不罚，>90% 封死（实测 7.5%）。
    注：教程"中高云 30-70% 理想"为地面视角云量；网格云量是分层覆盖率，口径不同。"""
    canvas = mid + high
    # 画布分段（由 555 天实证烧率归一）
    pts = [(0, 45), (25, 60), (50, 80), (75, 88), (100, 100), (125, 100),
           (150, 60), (200, 25)]
    c_score = _interp(canvas, pts)
    if low <= 30:
        l_score = 100.0
    elif low <= 60:
        l_score = 100 - (low - 30) * 1.0
    elif low <= 90:
        l_score = 70 - (low - 60) * 1.5
    else:
        l_score = max(10.0, 25 - (low - 90) * 1.0)
    return round(0.72 * c_score + 0.28 * l_score, 1)


def _interp(x, pts):
    """分段线性插值"""
    if x <= pts[0][0]:
        return pts[0][1]
    for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
        if x <= x1:
            return y0 + (y1 - y0) * (x - x0) / (x1 - x0)
    return pts[-1][1]


def dim2_path_canvas(path_midhigh_avgs, path_low_avgs, distances):
    """② 光路城市云况：距离加权中高云画布 + 低云惩罚。
    曲线 2026-09-05 实证重拟合（555 天上海光路历史，dim2 单独 AUC
    0.563->0.713）：实测峰值带画布 50~90%（烧率 40-45%），旧曲线
    trapz(30,70) 峰值带错位且在组合中拖累（0.704->0.674）。
    返回 (score, canvas_avg, low_avg, boundary_km)"""
    w = [1.0 / (1.0 + d / 150.0) for d in distances]
    tw = sum(w)
    canvas = sum(a * wi for a, wi in zip(path_midhigh_avgs, w)) / tw
    low = sum(a * wi for a, wi in zip(path_low_avgs, w)) / tw
    c_score = _interp(canvas, [(0, 42), (30, 55), (50, 100), (90, 100),
                               (120, 75), (160, 40), (210, 20)])
    # 低云惩罚（实证 AUC 0.414：低云多显著少烧，25% 起罚）
    if low <= 25:
        l_score = 100.0
    else:
        l_score = _clamp(100 - (low - 25) * 2.2, 5, 100)
    # 云边界：最远的中高云>=30% 的采样距离
    boundary = None
    for a, d in zip(path_midhigh_avgs, distances):
        if a >= 30:
            boundary = d
    return round(0.65 * c_score + 0.35 * l_score, 1), round(canvas, 1), \
        round(low, 1), boundary


def dim3_precip(precip_now_mm, precip_prev3h_mm=0.0, precip_prob=None):
    """③ 近地降水：罚事件时刻正在下的雨（雨幕遮挡光路）。
    注意（回测修正 2026-09-04）：此前几小时有降水不罚——
    上海火烧云主力是积雨云砧/对流云，雨停后云砧滞留恰是火烧云良机，
    一律罚降水与实况相反（旧版 AUC 仅 0.30）。"""
    s = 100.0
    if precip_prob is not None:
        s -= 0.25 * max(0.0, precip_prob - 30)
    if precip_now_mm is not None:
        if precip_now_mm >= 2.0:
            s -= 55
        elif precip_now_mm >= 0.5:
            s -= 35
        elif precip_now_mm >= 0.1:
            s -= 18
    return round(_clamp(s), 1)


def dim4_visibility_pm25(visibility_m, pm25):
    """④ 大气能见度/颗粒物"""
    v_km = (visibility_m or 15000) / 1000.0
    if v_km >= 20:
        vs = 100.0
    elif v_km >= 8:
        vs = 60 + (v_km - 8) / 12 * 40
    else:
        vs = _clamp(v_km / 8 * 60, 10, 60)
    if pm25 is None:
        ps = 70.0
    elif pm25 <= 15:
        ps = 100.0
    elif pm25 <= 35:
        ps = 100 - (pm25 - 15) / 20 * 35
    elif pm25 <= 75:
        ps = 65 - (pm25 - 35) / 40 * 35
    else:
        ps = _clamp(30 - (pm25 - 75) / 2, 0, 30)
    return round(0.6 * vs + 0.4 * ps, 1)


def dim5_cloud_trend(canvas_now, canvas_3h_ago):
    """⑤ 云运动趋势（2026-09-04 回测实证反转）：
    云层平稳（|Δ|小）实际更少烧（静止满天天幕无云边界，AUC 0.41）；
    云量变化大（系统过境/云层开合/对流生消）更易烧（形成云边界）。
    方向与 sample.pdf 原表相反，以 555 天上海数据为准。"""
    delta = abs((canvas_now or 0) - (canvas_3h_ago or 0))
    return round(_clamp(30 + delta * 2.0, 30, 100), 1)


def dim6_pressure(p_now, p_3h_ago):
    """⑥ 气压变化：升压趋向晴好"""
    delta = (p_now or 1010) - (p_3h_ago or 1010)
    if delta >= 0.5:
        return 100.0
    if delta <= -1.5:
        return 30.0
    return round(_clamp(80 + delta * 13.3, 30, 100), 1)


def dim7_aod(aod_local, aod_path):
    """⑦ CAMS 霾/气溶胶（2026-09-05 实证重拟合）：
    AOD 0.1-0.2 洁净段烧率 41.2% 最高，0.2~0.9 主区间实证平坦（22-30%），
    >1.0 陡降（1.5+ 无烧）。曲线兼顾概率判别与颜色质量语义
    （前段陡/中段缓/尾段陡），主区间放缓旧曲线的过度衰减。"""
    pts = [(0, 100), (0.15, 100), (0.2, 85), (0.3, 68), (0.9, 62),
           (1.05, 50), (1.25, 35), (1.6, 5), (2.5, 0)]

    def f(a):
        if a is None:
            return 70.0
        return _interp(a, pts)
    return round(0.5 * f(aod_local) + 0.5 * f(aod_path), 1)


def dim8_high_moisture(rh_300_mean, rh_500_mean, high_cover):
    """⑧ 高层湿层卷云潜力（2026-09-05 实证重拟合）：
    RH300 单独 AUC 0.633 强正向（RH500 0.565 弱、RH700 0.493 无），
    实测峰值带 RH 60~95%（80-90 段烧率 56.2%），旧曲线 trapz(40,80)
    在 80+ 衰减与实证相反。改为 300 为主 0.65/0.35、峰值带 60~95。"""
    rh = 0.65 * (rh_300_mean or 40) + 0.35 * (rh_500_mean or 40)
    base = _interp(rh, [(0, 25), (30, 25), (40, 55), (60, 100), (95, 100),
                        (105, 60), (115, 40)])
    bonus = 10.0 if (high_cover or 0) >= 20 else 0.0
    return round(_clamp(base + bonus), 1)


# ------------------------------------------------ 汇总

WEIGHTS = {
    'dim1': 0.23, 'dim2': 0.30, 'dim3': 0.10, 'dim4': 0.11,
    'dim5': 0.03, 'dim6': 0.01, 'dim7': 0.12, 'dim8': 0.10,
}
DIM_NAMES = {
    'dim1': '中高层云分布', 'dim2': '光路城市云况', 'dim3': '近地降水',
    'dim4': '大气能见度/颗粒物', 'dim5': '云运动趋势', 'dim6': '气压变化',
    'dim7': 'CAMS 霾/气溶胶', 'dim8': '高层湿层卷云潜力',
}


def combine(dims: dict, feasible: bool, dead_sky: bool) -> float:
    total = sum(WEIGHTS[k] * v for k, v in dims.items())
    if dead_sky:
        return round(min(total, 8.0), 1)
    if not feasible:
        return round(min(total, 25.0), 1)
    return round(total, 1)


def consensus(model_scores: dict, model_weights: dict,
              ens_spread: float = None, ens_disp: float = None,
              region: str = None) -> dict:
    """多模式共识：加权均值 - 模式离散度惩罚 - 集合离散度惩罚 -> 实证校准映射。

    ens_spread: 50 成员集合在**中高云量上的均值**（历史命名有误导；它参与
        ens_pen 且已被 555 天标定吸收，故数值口径不动）。
    ens_disp: 集合的**真实离散度**（P10~P90 半宽，%），只用于 credibility
        措辞——否则会把"云多"讲成"模式分歧大"。
    region: 校准区域（region_for 的返回值），'prd' 用珠三角曲线。
    """
    vals = list(model_scores.values())
    if not vals:
        return {'probability': 0.0, 'std': 0.0, 'credibility': '无数据',
                'ens_spread': ens_spread, 'ens_disp': ens_disp}
    mean = sum(model_scores[m] * model_weights.get(m, 0.25)
               for m in model_scores) / sum(model_weights.get(m, 0.25)
                                            for m in model_scores)
    mu = sum(vals) / len(vals)
    std = math.sqrt(sum((v - mu) ** 2 for v in vals) / len(vals))
    ens_pen = min(10.0, (ens_spread or 0) * 0.25)
    raw = _clamp(mean - std * 0.18 - ens_pen)
    prob = calibrated_probability(raw, region)
    # 措辞优先用真实离散度；拿不到时退回模式间标准差
    if ens_disp is not None and ens_disp > 12:
        cred = f'中低 · 集合 P10~P90 半宽 ±{ens_disp:.0f}%，云况不确定'
    elif ens_disp is not None and ens_disp <= 6:
        cred = '高 · 50 成员高度一致'
    elif std < 8:
        cred = '高 · 多模式高度一致'
    elif std < 15:
        cred = '中高 · 模式基本一致'
    elif std < 25:
        cred = '中 · 模式存在分歧'
    else:
        cred = '低 · 模式分歧大'
    return {'probability': round(prob, 0), 'raw': round(raw, 1),
            'std': round(std, 1), 'ens_spread': ens_spread,
            'ens_disp': ens_disp, 'credibility': cred}


# 实证校准映射（2026-09-05 重拟合：555 天上海 2022.6-2023.12 分箱烧率
# -> PAVA 保序 -> 基率缩放；时间留出 2023H2 Brier 0.269->0.231。
# 标签=当日出现值得记录的火烧云；基率 27.7%）
CALIB_PTS = [(0, 2.5), (45, 2.5), (55, 3.0), (65, 12.0), (75, 23.0),
             (85, 57.5), (95, 88.0), (130, 88.0)]

# 珠三角校准映射（2026-10-04 重拟合：366 天次 2026-04~09 卫星标签分箱烧率
# -> PAVA 保序 -> 基率缩放；时间留出 8~9 月 Brier 0.255->0.170，优于基率
# 预报 0.174——旧上海曲线 0.289 系统性偏低，因 PRD 事件率 77.6% 远高于
# 上海 27.7%。工具 tests/backtest/refit_calibration_prd.py，数字
# tests/backtest/refit_prd.json。标签=受光画布>=30%（霞的必要条件，语义
# 比上海「值得记录的大烧」宽，故判定词阈值按新语义重定）。
# 0 端 20% 为保守外推锚：低分端无本地样本（<30 分 0 天次），取介于上海
# 2.5% 与 PRD 首箱 60.8% 之间的值，防止极差预报天次被高估。
CALIB_PTS_PRD = [(0, 20.0), (35, 60.8), (65, 63.3), (75, 74.7),
                 (85, 85.1), (95, 89.6), (130, 89.6)]

# 地区分流框：珠三角机位（PRD 回测集覆盖广州/中山/深圳/江门/惠州一带）
PRD_BOX = (21.0, 23.9, 111.5, 116.5)   # lat_min, lat_max, lon_min, lon_max


def region_for(lat: float, lon: float) -> str:
    """按机位坐标选校准区域：珠三角框内 'prd'，其余 'sh'（默认上海曲线）"""
    la, lb, lo1, lo2 = PRD_BOX
    return 'prd' if (la <= lat <= lb and lo1 <= lon <= lo2) else 'sh'


def calibrated_probability(raw_score: float, region: str = None) -> float:
    """综合分 -> 火烧云事件概率（分段线性实证映射，按地区选曲线）"""
    return _interp(raw_score, CALIB_PTS_PRD if region == 'prd' else CALIB_PTS)


def verdict_word(p: float, region: str = None) -> str:
    """判定词（阈值按校准后概率语义；PRD 基率 77.6%、上限 ~90%，阈值重定）"""
    if region == 'prd':
        if p < 50:
            return '不烧'
        if p < 65:
            return '微烧'
        if p < 75:
            return '小烧'
        if p < 85:
            return '中烧'
        return '大烧'
    if p < 8:
        return '不烧'
    if p < 18:
        return '微烧'
    if p < 28:
        return '小烧'
    if p < 40:
        return '中烧'
    return '大烧'


def risk_notes(cloud_saturation, boundary_speed_ms, boundary_angle_deg,
               low_local):
    """翻车风险提示（教程 4.1 不确定性）"""
    notes = []
    if cloud_saturation is not None and cloud_saturation < 88:
        notes.append(f'云层欠饱和（RH 峰值 {cloud_saturation:.0f}%），云量存在蒸发风险')
    if boundary_speed_ms is not None and boundary_speed_ms > 15:
        level = '较大' if boundary_speed_ms <= 30 else '很大'
        notes.append(f'云边界风速 {boundary_speed_ms:.0f} m/s，边界位置不确定性{level}')
    if boundary_angle_deg is not None and boundary_angle_deg < 10:
        notes.append(f'云边界与日落方向夹角仅 {boundary_angle_deg:.0f}°，'
                     '可烧区域窄、方位敏感')
    if low_local is not None and low_local > 60:
        notes.append(f'本地低云 {low_local:.0f}%，存在遮挡与近地消光风险')
    return notes or ['未发现明显翻车因素，仍建议临近复核实况云图']


def reverse_burn_score(path_high_avgs, distances, aod_local, high_local):
    """反烧/余晖：仅高云参与（日落反方向高层被地影边缘照亮）"""
    w = [1.0 / (1.0 + d / 200.0) for d in distances]
    tw = sum(w)
    canvas = sum(a * wi for a, wi in zip(path_high_avgs, w)) / tw
    c_score = _trapz(canvas, 30, 75, 5, 100, floor=15)
    a_score = dim7_aod(aod_local, aod_local)
    local_score = _trapz(high_local or 0, 30, 75, 5, 100, floor=15)
    return round(0.5 * c_score + 0.25 * a_score + 0.25 * local_score, 1)

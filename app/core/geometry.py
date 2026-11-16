# -*- coding: utf-8 -*-
"""
火烧云几何引擎
理论依据：sunsetbot.top《火烧云预报教程》章节一 1.2（几何模型）、章节四（实战流程）
核心公式：
- 霞光弦长（教程表1.3口径）: W = 2·sqrt(2Rh + h²)
- 火烧云三角：霞光带 [x_t - W_band, x_t] 随日落线以速度 v 扫过云层
- 观察者头顶云底被照亮时长: t_overhead = W_band / v，其中 W_band = sqrt(2Rh)
- 光需绕过云边界 d_e 到达观察者: t_start = max(0, (d_e - h·R/d_e)) / v
- 目标高度角处云的距离: d = h / (tanθ + d/(2R)·...) 近似 d ≈ h/tanθ - d²/(2R) 迭代
- AOD 等效地表高度: h_eq_ground = H · ln(AOD / k_th)，k_th = 0.02/km (550nm)
"""
import math

R_EARTH_KM = 6371.0


def glow_chord_km(cloud_base_km: float) -> float:
    """教程表1.3 霞光弦长: 2·sqrt(2Rh+h²)。h=2km -> 319.23km"""
    h = max(0.05, cloud_base_km)
    return 2.0 * math.sqrt(2.0 * R_EARTH_KM * h + h * h)


def glow_band_km(cloud_base_km: float) -> float:
    """霞光带宽度（自日落线向夜侧）: sqrt(2Rh)。h=2km -> 159.6km"""
    h = max(0.05, cloud_base_km)
    return math.sqrt(2.0 * R_EARTH_KM * h)


def equivalent_ground_height_km(aod: float, scale_height_km: float = 1.5,
                                k_threshold: float = 0.02) -> float:
    """AOD 消光等效地表高度（教程 1.3.4/4.1）：
    气溶胶消光系数 k(z) = k0·exp(-z/H)，AOD = k0·H，
    k(z) = k_threshold 对应高度 h = H·ln(AOD/(k_threshold·H))。
    低于阈值返回 0（阳光可穿透至地面）。"""
    if aod is None or aod <= 0:
        return 0.0
    k0 = aod / max(0.2, scale_height_km)  # 地面消光系数 /km
    if k0 <= k_threshold:
        return 0.0
    return scale_height_km * math.log(k0 / k_threshold)


def equivalent_cloud_base_km(cloud_base_km: float, aod: float,
                             scale_height_km: float = 1.5) -> float:
    """等效云底高度 = 云底 - 等效地表高度（下限 0.15km）"""
    return max(0.15, cloud_base_km - equivalent_ground_height_km(aod, scale_height_km))


def burn_overhead_minutes(equiv_base_km: float, line_speed_km_min: float) -> float:
    """观察者头顶云底被照亮持续时间（分钟）= sqrt(2Rh)/v"""
    return glow_band_km(equiv_base_km) / max(0.1, line_speed_km_min)


def burn_with_cloud_edge(equiv_base_km: float, edge_distance_km: float,
                         line_speed_km_min: float):
    """典型云况（云层位于向日侧，边界距观察者 d_e）火烧云起止时刻。
    推导（对照教程深圳 2022-10-13 案例精确吻合：start=4.8, end=7.6 min）：
    - 霞光带 [x_e - 2αR, x_e - αR] 随太阳沉角 α 增大向东扫过，
      相切光线边界（近端）到达观察者: t_start = d_e / (2v)
      （教程：200km / (2×21km/min) = 4.76 ≈ 4.8 min ✓）
    - 结束：霞光带扫尽 √(2Rh)/v（教程：159.6/21 = 7.6 min ✓）
      与光路维持 d_e/v 取较小者
    返回 (t_start_min, t_end_min, feasible)"""
    h = equiv_base_km
    v = max(0.1, line_speed_km_min)
    if edge_distance_km is None or edge_distance_km <= 0:
        # 连续云层无近端边界：日落即可见西侧低角度霞光带
        t_start = 0.0
        t_end = glow_band_km(h) / v
    else:
        d_e = max(1.0, edge_distance_km)
        t_start = d_e / (2.0 * v)
        t_end = min(glow_band_km(h) / v, d_e / v)
        if t_end <= t_start:
            t_end = t_start + glow_band_km(h) / (2.0 * v)
    feasible = (edge_distance_km is None
                or edge_distance_km <= glow_chord_km(h) * 1.15)
    return t_start, max(t_start + 0.5, t_end), feasible


def glow_elevation_angle(equiv_base_km: float, distance_km: float) -> float:
    """距离 d 处云底（等效高度 h）的视线高度角（度）：z = d·tanθ - d²/2R = h"""
    d = max(0.5, distance_km)
    theta = math.atan(equiv_base_km / d + d / (2.0 * R_EARTH_KM)) * (180.0 / math.pi)
    return theta


def max_glow_elevation(equiv_base_km: float, edge_distance_km: float) -> float:
    """霞光在云边界处的最高视线角（度）"""
    return glow_elevation_angle(equiv_base_km, edge_distance_km)


def distance_at_elevation(equiv_base_km: float, elevation_deg: float) -> float:
    """给定视线高度角，被照亮云底的水平距离（忽略曲率一次迭代 + 曲率修正）"""
    e = max(0.05, elevation_deg) * math.pi / 180.0
    # d·tan(e) - d²/2R = h  → 解二次方程 d²/(2R) - d·tan(e) + h = 0 的近根
    a = 1.0 / (2.0 * R_EARTH_KM)
    b = -math.tan(e)
    c = equiv_base_km
    disc = b * b - 4 * a * c
    if disc < 0:
        return 0.0
    d = (-b - math.sqrt(disc)) / (2 * a)  # 近根
    return d


def glow_reach_km(equiv_base_km: float, sun_depression_deg: float) -> float:
    """太阳落下 α 度时霞光可达的最远距离：x = R(α + sqrt(α²+2h/R))"""
    a = math.radians(max(0.0, sun_depression_deg))
    h = max(0.05, equiv_base_km)
    return R_EARTH_KM * (a + math.sqrt(a * a + 2.0 * h / R_EARTH_KM))


def estimate_cloud_base_km(low: float, mid: float, high: float) -> float:
    """由三分层云量估计主导可照云层的云底高度（km）。
    传统云底：高云 ~6.5km，中云 ~3.5km，低云 ~1.0km。
    取中高云中占比大者的代表高度；若中高云均稀薄且低云为主，返回低云高度。"""
    if mid + high < 5:
        return 1.0 if low > 5 else 3.0
    if high >= mid:
        return 6.5 if high >= 15 else 3.5
    return 3.5 if mid >= 15 else 1.0


def brightness_class(transmission: float) -> str:
    """按 550nm 透射率分级（教程 FAQ 口径）：
    >=1/30 亮烧; >=1/300 闷烧; >=1/1000 微光; else 不可见"""
    if transmission >= 1 / 30:
        return '亮烧'
    if transmission >= 1 / 300:
        return '闷烧'
    if transmission >= 1 / 1000:
        return '微光'
    return '过暗'


def path_transmission(aod_local: float, aod_path: float,
                      path_len_km: float = 300.0) -> float:
    """近似 550nm 光路透射率：
    - 垂直 AOD（本地+光路均值）换算低角度斜程（有效气团 ~ 路径水平距离下限化）
    - 瑞利垂直光学厚度 0.098，低角度地平线气团约 12（考虑地球曲率与大气厚度）
    简化：tau_total = (AOD_local + AOD_path)·(AM_eff/2) + 0.098·AM_eff，AM_eff 上限 12"""
    am = min(12.0, max(1.0, path_len_km / 60.0))
    tau = ((aod_local or 0.1) + (aod_path or 0.1)) * 0.5 * am * 0.6 + 0.098 * am
    return math.exp(-tau)


def burn_quality(equiv_base_km: float, aod_local: float, aod_path: float) -> dict:
    """由等效云底高度 + AOD 推断颜色区间与亮度级别（教程 1.3.4 + 2.4 口径）"""
    trans = path_transmission(aod_local, aod_path)
    bright = brightness_class(trans)
    if equiv_base_km >= 6.0:
        color = '金黄到暗红（颜色区间全）'
    elif equiv_base_km >= 3.0:
        color = '橘色到橘红'
    elif equiv_base_km >= 1.8:
        color = '橘红为主'
    else:
        color = '橘红（范围窄、偏暗）'
    return {'color': color, 'brightness': bright, 'transmission': round(trans, 4)}

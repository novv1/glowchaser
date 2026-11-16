# -*- coding: utf-8 -*-
"""悬日月视线-云层遮挡几何（支持机位高度与目标高度）。

回答「哪一层云、在机位方向多远处会挡住太阳/月亮」：
- 视线（视高度角）与"云高球壳"求交 -> 沿地表弧距（遮蔽云距）；
- 地平线包络（巧摄/Planit 口径）：地平线云距 R·b、擦地云距 2R·b、
  最远无云距 3R·b、太阳临界几何高度角 -2b（b = acos(R/(R+h))）；
- 机位高度：抬高观察点带来的地平线下沉与遮蔽距离收缩；
- 目标高度：由「机位高度 + 目标高度 + 水平距离」反推目标视仰角。

参考实现：windy-plugin-sun-moon-path（docs/cloud-obstruction.md）。
注意与插件的口径差异：插件为贴合巧摄截图，三档云距**统一按海平面基准**，
不扣除机位海拔（其文档记录：扣除后 1201m 机位得 54.26/133.56/189.35 km，
而巧摄给 106.62/169.03/218.03 km）。本模块的用途是判断"云会不会挡住天体"，
故按物理正确的抬高观察点计算——机位越高，同一视高度角下的遮蔽云距越近、
可见地平线越向下沉。observer_height_m=0 时与插件/巧摄数值完全一致。

回归基准（视高度角 0.5949585°，海平面）：2000/4000/6000 m 云壳交点
= 106.62/169.04/218.04 km。
"""
import math

R_EARTH_KM = 6371.0088
MAX_CLOUD_HEIGHT_M = 30000.0
MAX_OBSERVER_HEIGHT_M = 9000.0


def _obs_km(observer_height_m) -> float:
    """观察者（机位）海拔 -> km；非法/负值按 0（海平面）处理。"""
    if observer_height_m is None or not math.isfinite(observer_height_m):
        return 0.0
    return max(0.0, min(MAX_OBSERVER_HEIGHT_M, observer_height_m)) / 1000.0


def sight_intersection_km(cloud_height_m: float, apparent_alt_deg: float,
                          observer_height_m: float = 0.0):
    """视线与云高球壳的最近交点，返回地表弧距 km；输入非法返回 None。

    cloud_height_m: 云层海拔高度（AMSL 米）
    apparent_alt_deg: 天体视高度角（含折射）
    observer_height_m: 机位海拔（AMSL 米）。观察点在球壳内部时几何上必有交点
    （单向前方出口）；机位高于云层时只在视高度角足够向下时才有交点，
    此时对应「站在云海之上俯视云层」的情形。

    射线方向取观察者局部水平面为准：球心为原点、观察者位于 (0, R+h_obs)，
    方向 (cos θ, sin θ)，与半径 R+h_cloud 的球壳求交后取最近正根 t，
    再换算为弧距 R·atan2(t·cosθ, (R+h_obs) + t·sinθ)。
    观察点位于球壳内部时几何上必有交点：只有输入非法或视线够不到时才返回 None。
    """
    if not (cloud_height_m is not None and math.isfinite(cloud_height_m)
            and 0 < cloud_height_m <= MAX_CLOUD_HEIGHT_M):
        return None
    if not (apparent_alt_deg is not None and math.isfinite(apparent_alt_deg)
            and abs(apparent_alt_deg) <= 90):
        return None
    r = R_EARTH_KM + _obs_km(observer_height_m)
    shell = R_EARTH_KM + cloud_height_m / 1000.0
    alt = math.radians(apparent_alt_deg)
    b = r * math.sin(alt)
    disc = shell * shell - (r * math.cos(alt)) ** 2
    if disc < 0:
        return None                      # 机位高于云层且视线不够向下
    roots = [t for t in (-b - math.sqrt(disc), -b + math.sqrt(disc))
             if t >= -1e-8]
    if not roots:
        return None
    ray = min(roots)
    return R_EARTH_KM * math.atan2(ray * math.cos(alt), r + ray * math.sin(alt))


def horizon_dip_deg(observer_height_m: float) -> float:
    """机位海拔带来的地平线下沉角（度）：acos(R/(R+h))。
    抬高后可见地平线低于局部水平面，天体在该角度以上即已露出地面
    （例：1201 m -> 1.112°）。"""
    h = _obs_km(observer_height_m)
    if h <= 0:
        return 0.0
    return math.degrees(math.acos(R_EARTH_KM / (R_EARTH_KM + h)))


def twilight_envelopes_km(cloud_height_m: float,
                          observer_height_m: float = 0.0):
    """地平线包络（观察者局部水平面基准）：
    (地平线云距, 擦地云距, 最远无云距, 临界角°)。

    用云层相对机位的高度差 ΔH = 云高 − 机位高度：机位抬高后同一云层的
    三档距离同步收缩（1201 m 机位看 2000 m 云：159.6 -> 100.9 km）。
    云层不高于机位（ΔH <= 0）时三包络无意义，返回 None。
    非法输入返回 None。"""
    if not (cloud_height_m is not None and math.isfinite(cloud_height_m)
            and 0 < cloud_height_m <= MAX_CLOUD_HEIGHT_M):
        return None
    h = (cloud_height_m - _obs_km(observer_height_m) * 1000.0) / 1000.0
    if h <= 0:
        return None
    b = math.acos(R_EARTH_KM / (R_EARTH_KM + h))
    return (R_EARTH_KM * b, 2 * R_EARTH_KM * b, 3 * R_EARTH_KM * b,
            -2 * math.degrees(b))


def occlusion_band_km(base_m: float, top_m: float, apparent_alt_deg: float,
                      geometric_alt_deg: float,
                      observer_height_m: float = 0.0):
    """某云层 [底, 顶] 能遮挡天体的地表距离区间 (d_near, d_far) km。

    - 天体几何高度角 >= 0（光线沿视线抬升）：
      区间 = [底高交点, 顶高交点]；
    - 天体几何高度角 < 0（折射可见的贴地平线悬日，光线自上而下弯入）：
      近端仍取底高视线交点，远端放宽到顶高的最远无云距 3R·b（巧摄口径）。
    视线够不到底高或区间倒置时 d_far 取 d_near；近端不可得返回 (None, None)。
    """
    d_near = sight_intersection_km(base_m, apparent_alt_deg, observer_height_m)
    if d_near is None:
        return None, None
    if geometric_alt_deg is not None and geometric_alt_deg >= 0:
        d_far = sight_intersection_km(top_m, apparent_alt_deg,
                                      observer_height_m)
    else:
        env = twilight_envelopes_km(top_m, observer_height_m)
        d_far = env[2] if env else None
    if d_far is None or d_far <= d_near:
        d_far = d_near
    return d_near, d_far


def target_elevation_deg(cam_height_m: float, target_height_m: float,
                         distance_km: float):
    """目标视仰角（几何，未含折射）：机位海拔 + 目标海拔 + 水平距离 -> 仰角。

    机位在 (R+h_cam) 的 (0, R+h_cam)，目标在中央角 φ = d/R 处的
    ((R+H_t)·sinφ, (R+H_t)·cosφ)；仰角 = atan2(径向差, 切向差)。
    平地上的远处目标会得到负角（地球曲率下沉）：
    h=0 时 10 km 处地面约为 -0.045°。非法输入返回 None。
    """
    if not (distance_km is not None and math.isfinite(distance_km)
            and distance_km > 0):
        return None
    if target_height_m is None or not math.isfinite(target_height_m):
        return None
    cam = _obs_km(cam_height_m)
    tgt = max(0.0, min(MAX_CLOUD_HEIGHT_M, target_height_m)) / 1000.0
    phi = min(math.pi / 2.0, distance_km / R_EARTH_KM)
    r_t = R_EARTH_KM + tgt
    radial = r_t * math.cos(phi) - (R_EARTH_KM + cam)
    tangent = r_t * math.sin(phi)
    return math.degrees(math.atan2(radial, tangent))


def moon_upper_limb_alt(apparent_alt_deg: float, moon_dist_km: float):
    """月亮上缘视高度角（月出/月落附近中心尚在地平线下时上缘可能已可见）。
    dist 非法时返回 None。"""
    if not (moon_dist_km is not None and math.isfinite(moon_dist_km)
            and moon_dist_km > 1000):
        return None
    semi = math.degrees(math.asin(1737.4 / moon_dist_km))
    return apparent_alt_deg + semi if apparent_alt_deg is not None else None


def band_of_height(alt_m_agl: float) -> str:
    """按离地高度分档（巧摄口径）：低 <2km / 中 2-6km / 高 >=6km。"""
    if alt_m_agl < 2000:
        return 'low'
    if alt_m_agl < 6000:
        return 'mid'
    return 'high'


def occlusion_transmission(occ_list) -> float:
    """沿视线的合成透射率（0-1）：各层透射率连乘。
    occ_list 为各相关云层云量百分比序列（0-100，None 跳过）。
    满云层透射 0.1（非 0：薄隙仍可能露光）。"""
    trans = 1.0
    for occ in occ_list:
        if occ is None:
            continue
        occ = max(0.0, min(100.0, occ))
        trans *= (1.0 - 0.9 * occ / 100.0)
    return trans

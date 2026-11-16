# -*- coding: utf-8 -*-
"""晨昏线/地影几何：判断某高度、某水平距离的云是否仍被太阳照亮。

物理含义
--------
日落（日出）之后地面先变暗，而高处、远处（朝太阳一侧）的云仍能被
阳光直射，那一片"云顶亮带"就是火烧云。本模块给出该判据的解析形式，
是晚霞/朝霞评分的物理基础。

设地球半径 R（与 app/core/sightline.py 同取 6371.0088 km），云位于
海平面之上 h km。由该云处可见的地平线下沉角（临界角）

    b(h) = acos(R / (R + h))          （单位：度）

含义：该高度处的太阳高度角高于 -b(h) 时云仍受直射，低于则进入地影。
天上一点 P（高度 h、离观测者的地表弧距 d）受光，当且仅当由 P 指向
太阳的射线不与地球相交，或 P 本身已位于向阳侧。化简后：

    朝太阳方向（方位角与太阳同侧）：受光 ⟺ α + d/R > -b(h)
    反日方向（背对太阳）          ：受光 ⟺ α - d/R > -b(h)

其中 α 为**观测者本地**的太阳高度角（度，日落前后为负），d/R 取弧度。

等价说法（便于理解）：云所在地的太阳高度角 α_cloud = α ± d/R
（朝日为 +，反日为 −），受光 ⟺ α_cloud > -b(h)。所以云越高、越远
（朝太阳方向），变暗越晚——这就是"高云/远云先烧、低云先黑"的
定量依据。

与既有口径的一致性
------------------
app/core/sightline.py 的 twilight_envelopes_km(h)[3] 返回"临界角
-2b"，正是本判据在 d = R·b 处的特例：d = R·b 时
α + b > -b ⟺ α > -2b。tests/unit/test_illumination.py 对该一致性
有断言（容差 1e-9）。

输入校验风格与 sightline.py 一致：非法输入不抛异常，按各函数说明返回
None / False / 0.0。仅用 math，不引入 numpy/pandas 等新依赖。
"""
import math

from app.core.sightline import R_EARTH_KM

MAX_CLOUD_HEIGHT_KM = 30.0


def _height_km(height_km):
    """云层海拔（km）校验：None/非有限/<=0/>30km 一律返回 None。"""
    if height_km is None or not math.isfinite(height_km):
        return None
    if height_km <= 0 or height_km > MAX_CLOUD_HEIGHT_KM:
        return None
    return height_km


def _dist_ok(dist_km) -> bool:
    """地表弧距（km）校验：None/非有限/负值非法。"""
    return (dist_km is not None and math.isfinite(dist_km)
            and dist_km >= 0)


def _alt_ok(sun_alt_deg) -> bool:
    """太阳高度角（度）校验：None/非有限/|alt|>90 非法。"""
    return (sun_alt_deg is not None and math.isfinite(sun_alt_deg)
            and abs(sun_alt_deg) <= 90)


def _critical_rail_deg(h_km, dist_km, toward_sun) -> float:
    """受光边界值 α*：α > α* 受光，α <= α* 变暗（h/dist 须已校验）。

    朝日 α* = -(b + d/R)；反日 α* = d/R - b（可为正）。
    """
    b = math.degrees(math.acos(R_EARTH_KM / (R_EARTH_KM + h_km)))
    arc = math.degrees(dist_km / R_EARTH_KM)
    return -(b + arc) if toward_sun else arc - b


def critical_alt_deg(height_km) -> float:
    """临界角 b(h)（度）= acos(R/(R+h))；h 为云层海拔（km）。

    该高度的云在太阳高度角低于 -b(h) 时（其所在地平线以下）变暗。
    校验：b(2 km)=1.435°、b(5 km)=2.269°、b(10 km)=3.208°、
    b(12 km)=3.514°（四舍五入即 1.44/2.27/3.21/3.51）。
    非法输入（None/非有限/<=0/>30 km）返回 None。
    """
    h = _height_km(height_km)
    if h is None:
        return None
    return math.degrees(math.acos(R_EARTH_KM / (R_EARTH_KM + h)))


def is_lit(height_km, dist_km, sun_alt_deg, toward_sun=True) -> bool:
    """该云在当前太阳高度角下是否仍被阳光直射。

    height_km: 云层海拔（km，>0 且 <=30）
    dist_km: 云与观测者的地表水平弧距（km，>=0）
    sun_alt_deg: 观测者本地太阳高度角（度，日落前后为负）
    toward_sun: True 表示云位于朝太阳方向（方位角与太阳同侧），
        False 表示反日方向（背对太阳）。缺省按朝日处理。

    判据：朝日 α + d/R > -b(h)；反日 α - d/R > -b(h)，
    等价于云的本地太阳高度角 α ± d/R 高于 -b(h)。
    边界处按"未受光"处理（严格大于）。任一输入非法返回 False。
    """
    h = _height_km(height_km)
    if h is None or not _dist_ok(dist_km) or not _alt_ok(sun_alt_deg):
        return False
    return sun_alt_deg > _critical_rail_deg(h, dist_km, toward_sun)


def lit_fraction(height_km, dist_km, sun_alt_deg, toward_sun=True,
                 taper_deg=0.6) -> float:
    """受光程度 0~1，边界附近按 taper_deg 线性过渡，避免评分硬跳变。

    height_km/dist_km/sun_alt_deg/toward_sun: 同 is_lit。
    taper_deg: 过渡带总宽度（度，缺省 0.6）。以受光边界 α* 为中心，
        α ∈ [α* - taper/2, α* + taper/2] 内线性 0 -> 1（边界处 0.5），
        taper 内单调递增，带外饱和到 0.0 / 1.0。
    taper_deg 非正或非有限、其余任一输入非法返回 0.0。
    """
    if not (taper_deg is not None and math.isfinite(taper_deg)
            and taper_deg > 0):
        return 0.0
    h = _height_km(height_km)
    if h is None or not _dist_ok(dist_km) or not _alt_ok(sun_alt_deg):
        return 0.0
    rail = _critical_rail_deg(h, dist_km, toward_sun)
    frac = (sun_alt_deg - rail) / taper_deg + 0.5
    return min(1.0, max(0.0, frac))


def dark_sun_alt_deg(height_km, dist_km, toward_sun=True) -> float:
    """该云变暗（受光临界）时观测者本地的太阳高度角（度）。

    height_km/dist_km/toward_sun: 同 is_lit。
    朝太阳方向：-(b + d/R)，恒 <= 0 —— 云越远越高，越晚变暗。
    反日方向：d/R - b，**可为正**：表示该云在太阳尚在地平线之上时
        就已变暗（例如 h=2 km、d=300 km 反日约为 +1.26°，即太阳
        还高 1.26° 时该云已进入地影）。
    α 高于此值受光，低于/等于此值变暗（与 is_lit 的严格大于一致）。
    非法输入返回 None。
    """
    h = _height_km(height_km)
    if h is None or not _dist_ok(dist_km):
        return None
    return _critical_rail_deg(h, dist_km, toward_sun)

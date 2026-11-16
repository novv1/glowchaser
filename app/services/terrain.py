# -*- coding: utf-8 -*-
"""站点地形探测服务（v2.7）：海拔批量采样、地平线遮蔽角、微气候提示、光污染。

数据源：
- 海拔：Open-Meteo Elevation API（免费无鉴权，多坐标单请求）
- 光污染：VIIRS World Atlas 静态栅格（data/lightpollution.tif，tifffile 采样），
  文件缺失/解析失败自动降级启发式（距最近大城市距离+人口权重）
- 结果按坐标缓存（海拔/地形不变，永不过期）

供 /api/site_probe 端点与 build_report（site_info 注入）调用。
"""
import json
import logging
import math
import os
import tempfile
import threading
import time

try:  # 项目根运行（uvicorn app.main:app）
    from app.core import cities as citydb
    from app.services import openmeteo as om
except ImportError:  # app/ 为根运行（发布沙箱 / python main.py）
    from core import cities as citydb  # type: ignore
    from services import openmeteo as om  # type: ignore

log = logging.getLogger(__name__)

ELEV_URL = 'https://api.open-meteo.com/v1/elevation'
TIF_PATH = os.path.normpath(os.path.join(
    os.path.dirname(os.path.abspath(__file__)), '..', 'data',
    'lightpollution.tif'))
ELEV_CACHE_PATH = os.path.normpath(os.path.join(
    os.path.dirname(os.path.abspath(__file__)), '..', '..', 'data',
    'elev_cache.json'))

# 16 方位 × 2 距离（2km 近场 + 6km 远场，单位 km），加观测点 = 33 坐标
_AZS = [i * 22.5 for i in range(16)]
_DISTS_KM = [2.0, 6.0]

_SITE_CACHE: dict[tuple, dict] = {}
_SITE_CACHE_MAX = 512   # 地图选点会产生任意坐标，缓存必须封顶
_LOCK = threading.Lock()

# 海拔缓存：地形是不变的常量，所以按坐标永久有效（没有 TTL）。
# 不落盘的话，进程一重启、或 _SITE_CACHE 被淘汰，就要为同一批坐标再花一次额度
# ——每次探测 33 个坐标（观测点 + 16 方位 × 2 距离）。
_ELEV_CACHE = None      # {f'{lat:.4f},{lon:.4f}': 米}，懒加载
_ELEV_CACHE_MAX = 20000
# 可重入：_elev_cache_save 持锁期间会再调 _load_elev_cache，用普通 Lock 会自锁死
_ELEV_LOCK = threading.RLock()

# 城市"灯光权重"（启发式光污染用，万人级粗略量级）
_CITY_LIGHT = {
    '深圳': 3.0, '广州': 3.0, '香港': 3.0, '澳门': 2.5, '珠海': 2.0,
    '东莞': 2.0, '佛山': 2.0, '中山': 1.6, '惠州': 1.2, '江门': 1.0,
    '肇庆': 0.8, '拱北': 2.0, '云浮': 0.5, '阳江': 0.5, '清远': 0.5,
    '韶关': 0.5, '汕尾': 0.5, '汕头': 1.2, '厦门': 1.2, '海口': 1.0,
    '北海': 0.5, '湛江': 0.8, '茂名': 0.6, '南宁': 1.2, '昆明': 1.2,
    '长沙': 1.5, '重庆': 2.0, '桂林': 0.6, '郴州': 0.4, '永州': 0.4,
    '邵阳': 0.4, '怀化': 0.4, '铜仁': 0.3, '遵义': 0.5, '梧州': 0.4,
    '玉林': 0.4, '贵港': 0.4, '柳州': 0.7, '河池': 0.3, '百色': 0.3,
    '钦州': 0.4, '漳州': 0.7, '赣州': 0.6, '广州塔': 3.0,
}


# ------------------------------------------------ 海拔与遮蔽角

def _sample_coords(lat, lon):
    """观测点 + 16 方位 × 2 距离的采样坐标（与 _AZS/_DISTS_KM 顺序对应）"""
    lats, lons = [lat], [lon]
    for az in _AZS:
        for d in _DISTS_KM:
            plat, plon = citydb.destination_point(lat, lon, az, d)
            lats.append(plat)
            lons.append(plon)
    return lats, lons


def _elev_key(lat, lon):
    return f'{lat:.4f},{lon:.4f}'


def _load_elev_cache():
    """懒加载海拔缓存；文件缺失或损坏一律按「没命中」处理（最多白花一次请求）。"""
    global _ELEV_CACHE
    with _ELEV_LOCK:
        if _ELEV_CACHE is None:
            data = {}
            try:
                with open(ELEV_CACHE_PATH, encoding='utf-8') as f:
                    raw = json.load(f)
                if isinstance(raw, dict):
                    for k, v in raw.items():
                        if isinstance(v, (int, float)) and not isinstance(v, bool):
                            data[k] = float(v)
            except FileNotFoundError:
                pass
            except Exception as e:
                log.warning('[terrain] 海拔缓存读取失败（按未命中处理）：%s',
                            str(e)[:80])
            _ELEV_CACHE = data
        return _ELEV_CACHE


def _elev_cache_save(new: dict):
    """把新坐标并入缓存并原子落盘（容量封顶，FIFO 淘汰最旧一半）。

    `_load_elev_cache()` 返回的就是内存里的那个 dict（并负责赋值给
    `_ELEV_CACHE`），所以在它上面 update 等于同步更新了内存缓存，这里不需要
    再声明 `global`（CI 的 pyflakes 会因「未被赋值却声明 global」报错）。
    """
    with _ELEV_LOCK:
        cache = _load_elev_cache()
        cache.update(new)
        if len(cache) > _ELEV_CACHE_MAX:
            for k in list(cache)[:len(cache) - _ELEV_CACHE_MAX // 2]:
                cache.pop(k, None)
        d = os.path.dirname(ELEV_CACHE_PATH)
        try:
            os.makedirs(d, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=d, prefix='.elev_cache_',
                                       suffix='.tmp')
            try:
                with os.fdopen(fd, 'w', encoding='utf-8') as f:
                    json.dump(cache, f, ensure_ascii=False)
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(tmp, ELEV_CACHE_PATH)
            except Exception:
                try:
                    os.remove(tmp)
                except OSError:
                    pass
                raise
        except Exception as e:
            # 落盘失败不影响本次结果，只是下次还得再请求一次
            log.warning('[terrain] 海拔缓存写入失败：%s', str(e)[:80])


def fetch_elevations(lats, lons):
    """Open-Meteo Elevation 批量查询，返回 [海拔m]（失败返回 None）。

    命中永久缓存的坐标不再向上游请求（跨进程重启也命中），只把缺的坐标
    一次性补齐。返回列表必须与入参下标一一对应 —— horizon_mask/probe_site
    按下标取用，半份数据比没有更危险，所以补不齐就整体返回 None。
    """
    cache = _load_elev_cache()
    keys = [_elev_key(la, lo) for la, lo in zip(lats, lons)]
    miss = [(i, k) for i, k in enumerate(keys) if k not in cache]
    if miss:
        params = {
            'latitude': ','.join(f'{lats[i]:.4f}' for i, _ in miss),
            'longitude': ','.join(f'{lons[i]:.4f}' for i, _ in miss),
        }
        try:
            r = om._get(ELEV_URL, params, timeout=30)
            el = r.get('elevation')
            if not el or len(el) != len(miss):
                return None
            fresh = {k: float(v) for (_, k), v in zip(miss, el)}
        except Exception as e:
            log.info('[terrain] elevation skip: %s', str(e)[:80])
            return None
        _elev_cache_save(fresh)
    return [cache[k] for k in keys]


def horizon_mask(lat, lon, elev0, elevs):
    """16 方位地平线遮蔽角（度）：每方位取两个距离中的最大仰角。
    atan((h_i - h_0) / d)，h_0 为观测点海拔。注意 Δh（米）与距离（米）一致。"""
    mask = []
    for i, az in enumerate(_AZS):
        best = 0.0
        for j, d_km in enumerate(_DISTS_KM):
            h = elevs[1 + i * 2 + j]
            if h is None or elev0 is None:
                continue
            ang = math.degrees(math.atan(max(0.0, h - elev0) / (d_km * 1000.0)))
            best = max(best, ang)
        mask.append(round(min(89.0, best), 1))
    return mask


def horizon_alt_at(mask, az_deg):
    """按方位角插值遮蔽角（mask 为 16 方位、间隔 22.5°，起始 0°=北）"""
    if not mask or len(mask) != 16:
        return 0.0
    az = az_deg % 360.0
    i0 = int(az // 22.5) % 16
    i1 = (i0 + 1) % 16
    f = (az - i0 * 22.5) / 22.5
    return mask[i0] * (1 - f) + mask[i1] * f


# ------------------------------------------------ 光污染

_tif_cache = {'loaded': False, 'data': None, 'meta': None,
              'fails': 0, 'fail_ts': 0.0}
# v2.37（审查报告 ⑪）：早先任何异常都写 loaded=True ⇒ 一次瞬时失败（文件正在
# 拷贝、tifffile 未装、句柄被占）就把光污染永久降级成启发式。现在改成有限重试：
# 失败后冷却 TIF_RETRY_SEC 再试，连续失败 TIF_MAX_FAILS 次才真正放弃。
TIF_RETRY_SEC = int(os.environ.get('GC_TIF_RETRY') or 60)
TIF_MAX_FAILS = int(os.environ.get('GC_TIF_MAX_FAILS') or 5)


def _tif_fail(why: str):
    _tif_cache['fails'] = int(_tif_cache.get('fails') or 0) + 1
    _tif_cache['fail_ts'] = time.time()
    _tif_cache['loaded'] = False
    if _tif_cache['fails'] == TIF_MAX_FAILS:
        log.warning('[terrain] lightpollution.tif 连续 %d 次不可用（%s），'
                    '本进程内改用启发式 Bortle；补好文件后重启即可恢复',
                    TIF_MAX_FAILS, why)
    return None


def _load_tif():
    """懒加载 VIIRS tif（tifffile），返回 (data2d, scale, tiepoint, shape) 或 None"""
    if _tif_cache['loaded']:
        return _tif_cache['data']
    fails = int(_tif_cache.get('fails') or 0)
    if fails >= TIF_MAX_FAILS:
        return None
    if fails and time.time() - float(_tif_cache.get('fail_ts') or 0.0) < \
            TIF_RETRY_SEC:
        return None
    if not os.path.exists(TIF_PATH):
        return _tif_fail('文件不存在')
    try:
        import tifffile
        import numpy as np
        with tifffile.TiffFile(TIF_PATH) as tif:
            arr = tif.pages[0].asarray()
            tags = tif.pages[0].tags
            scale = tags['ModelPixelScaleTag'].value if \
                'ModelPixelScaleTag' in tags else None
            tie = tags['ModelTiepointTag'].value if \
                'ModelTiepointTag' in tags else None
        if not scale or not tie:
            return _tif_fail('缺少 GeoTIFF 定位标签')
        _tif_cache['data'] = (np.asarray(arr, dtype=np.float32),
                              tuple(scale), tuple(tie), arr.shape)
        _tif_cache['loaded'] = True
        _tif_cache['fails'] = 0
        log.info('[terrain] lightpollution.tif loaded %s', arr.shape)
        return _tif_cache['data']
    except Exception as e:
        return _tif_fail(str(e)[:80])


def _bortle_from_radiance(v):
    """VIIRS 辐亮度 nW/cm²/sr → Bortle 粗映射（经验分段）"""
    if v is None or v != v:
        return None
    thresholds = [(0.11, 1), (0.33, 2), (0.67, 3), (1.34, 4), (2.7, 5),
                  (5.4, 6), (10.7, 7), (21.4, 8)]
    for t, b in thresholds:
        if v < t:
            return b
    return 9


def light_pollution(lat, lon):
    """返回 (bortle 1-9 或 None, source: 'viirs'/'heuristic')"""
    tif = _load_tif()
    if tif:
        import numpy as np
        arr, (sx, sy, _), tie, shape = tif
        # ModelTiepoint: (i, j, k, x, y, z) —— (0,0,0, lon0, lat0, 0)
        lon0, lat0 = tie[3], tie[4]
        col = int((lon - lon0) / sx)
        row = int((lat0 - lat) / sy)
        if 0 <= row < shape[0] and 0 <= col < shape[1]:
            window = arr[max(0, row - 1):row + 2, max(0, col - 1):col + 2]
            v = float(np.nanmean(window))
            b = _bortle_from_radiance(v)
            if b:
                return b, 'viirs'
    return _heuristic_bortle(lat, lon), 'heuristic'


def _heuristic_bortle(lat, lon):
    """启发式：按到最近城市距离 + 灯光权重估算 Bortle（3-9）"""
    best = 0.0
    allc = list(citydb.PRD_CITIES) + [
        {'name': c['name'], 'lat': c['lat'], 'lon': c['lon']}
        for c in citydb.AQ_CITIES]
    for c in allc:
        w = _CITY_LIGHT.get(c['name'], 0.5)
        d = citydb.haversine_km(lat, lon, c['lat'], c['lon'])
        contrib = w * max(0.0, 1.0 - d / 120.0)
        best = max(best, contrib)
    if best <= 0.01:
        return 3
    return int(max(3, min(9, round(3 + best * 2.2))))


# ------------------------------------------------ 微气候提示

def microclimate_notes(elev, elev0_ref=None, humidity=None, wind=None,
                       is_night_clear=None, coastal=False, low_cloud=None):
    """地形/微气候提示文案（结合气象参数由调用方传入）"""
    notes = []
    if elev and elev >= 500:
        notes.append(f'海拔 {elev:.0f}m：气温较海平面低约 '
                     f'{elev * 0.0065:.1f}°C，大气更通透，注意保暖')
    elif elev is not None and elev < 50:
        notes.append('低海拔平原/滨海：湿度大，夜晨易辐射雾')
    if humidity is not None and wind is not None:
        if humidity >= 90 and wind < 6 and (elev or 0) < 200:
            notes.append('高湿 + 静风 + 低海拔：山谷/辐射雾风险高，'
                         '地平线目标可能被雾遮蔽')
        elif 70 <= humidity < 90 and wind < 10:
            notes.append('湿度偏高：低空霾/薄雾会削弱低角度天体亮度')
    if coastal and low_cloud is not None and low_cloud >= 60:
        notes.append('沿海坐标：注意海雾/平流低云从海上登陆遮挡地平线')
    return notes


# ------------------------------------------------ 主入口

def probe_site(lat, lon, use_cache=True):
    """站点探测：海拔、16 方位遮蔽角、光污染。结果按坐标缓存。"""
    key = (round(lat, 3), round(lon, 3))
    with _LOCK:
        if use_cache and key in _SITE_CACHE:
            return dict(_SITE_CACHE[key])
    lats, lons = _sample_coords(key[0], key[1])
    elevs = fetch_elevations(lats, lons)
    out = {'lat': key[0], 'lon': key[1], 'elevation': None,
           'horizon_mask': None, 'bortle': None, 'bortle_src': None,
           'ok': False}
    if elevs:
        elev0 = elevs[0]
        # 滨海粗判：任一 6km 采样点为 0 或负值（海上）
        coastal = any(e <= 0.5 for e in elevs[2::2])
        out.update({
            'elevation': round(elev0, 1),
            'horizon_mask': horizon_mask(key[0], key[1], elev0, elevs),
            'coastal': coastal,
            'ok': True,
        })
    bortle, src = light_pollution(key[0], key[1])
    out['bortle'] = bortle
    out['bortle_src'] = src
    with _LOCK:
        _SITE_CACHE[key] = dict(out)
        if len(_SITE_CACHE) > _SITE_CACHE_MAX:   # FIFO 淘汰最旧一半
            for k in list(_SITE_CACHE)[:len(_SITE_CACHE) - _SITE_CACHE_MAX // 2]:
                _SITE_CACHE.pop(k, None)
    return out


def bortle_desc(b):
    if not b:
        return ''
    desc = {1: '原生态暗夜', 2: '天然暗夜', 3: '乡村', 4: '乡村/郊区过渡',
            5: '郊区', 6: '明亮郊区', 7: '郊区/城市过渡', 8: '城市',
            9: '市中心'}
    return desc.get(b, '')


if __name__ == '__main__':
    import sys
    la, lo = float(sys.argv[1]), float(sys.argv[2])
    print(json.dumps(probe_site(la, lo), ensure_ascii=False, indent=1))

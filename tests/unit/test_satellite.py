# -*- coding: utf-8 -*-
"""卫星模块纯函数：投影、亮温分类、Otsu 阈值、取景裁剪、逐槽位帧。"""
import numpy as np

from app.services import satellite


def test_latlon_to_px_center():
    px, py = satellite.latlon_to_px(0.0, satellite.SUB_LON)
    assert abs(px - satellite.CX) < 1.0
    assert abs(py - satellite.CY) < 1.0


def test_latlon_backside_returns_none():
    assert satellite.latlon_to_px(0.0, -30.0) is None


def test_projection_constants_match_measured_disk():
    """投影常量必须与实测圆盘几何一致（2026-09-14 标定）。

    旧值 (300.5, 300.5, 280) 把珠三角的光路采样点画到真实位置西南约
    400km 处 —— 用户在卫星预览里看到"光路不在目标点那边"的一半原因。
    这里锁住半径量级与圆心，防止再被改回拍脑袋的值。
    """
    assert 284 <= satellite.RAD <= 296
    assert 296 <= satellite.CX <= 304
    assert 280 <= satellite.CY <= 294
    # 圆盘必须能放进画面（含边框）：2R + 边框 < 601
    assert 2 * satellite.RAD + 16 < satellite.IMG_SIZE


def test_px_per_km_local_scale():
    """局部比例：星下点约 0.045px/km，珠三角约 0.039px/km（≈26km 一个像素）。"""
    center = satellite.px_per_km(0.0, satellite.SUB_LON)
    prd = satellite.px_per_km(22.27, 113.58)
    assert abs(center - satellite.RAD / 6371.0088) < 1e-6
    assert 0.036 <= prd <= 0.042
    assert 24 <= 1 / prd <= 28          # 珠三角 1 像素约 26km


def test_disk_interior_excludes_border_and_caption():
    """白边框与底部字幕条必须判为盘外：采到那里会读成 255（极冷）= 假高云。"""
    assert satellite.disk_interior(satellite.CX, satellite.CY) is True
    assert satellite.disk_interior(5, 5) is False            # 左上白边框
    assert satellite.disk_interior(300, 300) is True          # 盘内
    assert satellite.disk_interior(300, 590) is False         # 底部字幕条
    assert satellite.disk_interior(satellite.CX + satellite.RAD + 5,
                                   satellite.CY) is False     # 盘外


def test_latlon_to_px_never_returns_border():
    """任何返回的像素都必须落在圆盘内部（否则会采到边框）。"""
    for lat in range(-60, 61, 5):
        for lon in range(60, 181, 5):
            p = satellite.latlon_to_px(float(lat), float(lon))
            if p is not None:
                assert satellite.disk_interior(p[0], p[1]), (lat, lon, p)


def test_path_points_along_spacing_and_direction():
    pts = satellite.path_points_along(22.27, 113.58, 106.1, span_km=620,
                                      step_km=30)
    assert pts[0][:2] == (22.27, 113.58) and pts[0][2] == 0.0
    assert len(pts) == 1 + 20                      # 0..600km 每 30km
    assert abs(pts[-1][2] - 600.0) < 1e-6
    # 朝 106°（东南偏东）：经度增、纬度减
    assert pts[-1][1] > pts[0][1] and pts[-1][0] < pts[0][0]


def test_frame_slots_step_is_10_minutes():
    """槽位必须落在 10 分钟网格上且严格降序。

    **不断言「每步正好 10 分钟」**：``available_slots`` 的契约是**真实可用**
    的槽位（见其文档——JMA 会把 24h 前的旧图挂在今天的槽位名上，所以逐个
    HEAD 探测）。中间某个槽位缺失时相邻两帧就会隔 20 分钟，这是设计如此。
    实测踩到过 [10, 20, 10]：写死等距会让 CI 随 JMA 当天的发布情况随机变红。
    """
    slots = satellite.frame_slots(4)
    if not slots:
        return                      # JMA 不可达时无槽位，只校验间隔规则
    mins = []
    for s in slots:
        h, m = s.split(':')
        mins.append(int(h) * 60 + int(m))
    diffs = [(mins[i] - mins[i + 1]) % 1440 for i in range(len(mins) - 1)]
    assert all(d > 0 for d in diffs), diffs            # 最新在前，严格降序
    assert all(d % 10 == 0 for d in diffs), diffs      # 只落在 10 分钟网格上


def test_crop_frames_the_path_not_just_observer():
    """取景必须跟着光路走。

    旧版以观测点为中心固定裁 70px：珠三角约 3700km 宽的画面里，
    620km 的光路只占约 25px，方向完全看不出来。
    """
    arr = np.full((601, 601), 40, dtype=np.float32)
    obs = satellite.latlon_to_px(22.27, 113.58)
    path = [(p[0], p[1], p[2]) for p in
            satellite.path_points_along(22.27, 113.58, 106.1)]
    px_list = []
    for (plat, plon, d) in path:
        p = satellite.latlon_to_px(plat, plon)
        px_list.append((p[0], p[1], d) if p else (None, None, d))
    img, meta = satellite.crop_with_path(arr, obs, px_list, pad_km=150,
                                        lat=22.27, lon=113.58, az=106.1)
    assert img is not None and meta is not None
    # 覆盖 600km 光路 + 两侧各 150km 留白：约 900km 宽，远小于旧的 3700km
    assert 700 <= meta['km_w'] <= 1200, meta
    # 光路首末点都要落在裁剪框内
    for (px, py, d) in px_list:
        if px is None:
            continue
        assert meta['x0'] - 1 <= px <= meta['x1'] + 1
        assert meta['y0'] - 1 <= py <= meta['y1'] + 1


def test_bt_crop_latitude_orientation():
    """P-Tree 裁剪图必须北在上（地图口径）。

    旧版把纬度阵列上下翻了一次（行 0 = 最南），整片云场南北镜像，
    同时 to_xy 又把"纬度总跨度"当成"每像素跨度"再乘行数，光路被压到
    画面顶部 —— 与 JMA 那张一起，两个视图的光路/云场都是错的。

    这里用双色数据场直接验证方向：北半 210K（冷->红）、南半 290K
    （暖->深蓝），则画面上半应偏红、下半应偏蓝。
    """
    n = 60
    latv = np.linspace(30.0, 15.0, n, dtype=np.float32)     # 降序 = 北在上
    lonv = np.linspace(105.0, 120.0, n, dtype=np.float32)
    arr = np.full((n, n), 210.0, dtype=np.float32)          # 北半：冷
    arr[latv < 22.0, :] = 290.0                             # 南半：暖
    samples = [{'lat': 29.5, 'lon': 108.0, 'dist': 0.0, 'bt': 210.0},
               {'lat': 15.5, 'lon': 118.0, 'dist': 600.0, 'bt': 290.0}]
    img = satellite._bt_crop_image(arr, latv, lonv, samples, (29.5, 108.0),
                                   pad_km=0)
    a = np.asarray(img).astype(int)
    a = a[:, :int(a.shape[1] * 0.9)]         # 去掉右侧色标
    h = a.shape[0]
    top = a[:h // 3]
    bot = a[-h // 3:]
    # 上半是冷云（红占优）、下半是暖区（蓝占优）
    assert top[:, :, 0].mean() > top[:, :, 2].mean() + 40, top.mean(axis=(0, 1))
    assert bot[:, :, 2].mean() > bot[:, :, 0].mean() + 20, bot.mean(axis=(0, 1))
    assert top[:, :, 0].mean() > bot[:, :, 0].mean() + 40


def test_bt_crop_path_spans_frame():
    """光路首末点必须落在画面内（含纬度方向的映射），不被压到边缘。"""
    n = 60
    latv = np.linspace(30.0, 15.0, n, dtype=np.float32)
    lonv = np.linspace(105.0, 120.0, n, dtype=np.float32)
    arr = np.full((n, n), 260.0, dtype=np.float32)
    samples = [{'lat': la, 'lon': 105.0 + i * 15.0 / 10, 'dist': i * 60.0,
                'bt': 260.0} for i, la in enumerate(np.linspace(29.0, 16.0, 11))]
    img = satellite._bt_crop_image(arr, latv, lonv, samples, (29.0, 105.0),
                                   pad_km=0)
    a = np.asarray(img)
    # 路径线偏黄（R、G 高、B 低），找它的行分布
    yellow = (a[:, :, 0] > 180) & (a[:, :, 1] > 150) & (a[:, :, 2] < 140)
    assert yellow.any(), '未画出光路'
    rows = np.nonzero(yellow)[0]
    h = a.shape[0]
    # 南北跨 13°、画面覆盖约 15°：路径应铺满大部分高度，而不是挤在顶部
    assert (rows.max() - rows.min()) > h * 0.6, (rows.min(), rows.max(), h)
    assert rows.min() < h * 0.35, (rows.min(), h)


def test_planet_frame_index_maps_to_slot(monkeypatch):
    """时间轴契约：第 back 帧严格对应 available_slots()[back]，且每帧一槽。

    现场测过 JMA 槽位会在"刚发布/24h 旧图"之间翻转，靠现场运气容易看起来
    时对时错；这里用假槽位把契约钉死。
    """
    from datetime import datetime, timedelta, timezone
    base = datetime(2026, 9, 14, 10, 0, tzinfo=timezone.utc)
    slots = [(base - timedelta(minutes=10 * k),
              base - timedelta(minutes=10 * k) + timedelta(minutes=11))
             for k in range(8)]
    arr = np.full((601, 601), 40, dtype=np.float32)

    monkeypatch.setattr(satellite, 'available_slots',
                        lambda n=8, max_age_min=240: slots[:n])
    monkeypatch.setattr(satellite, 'fetch_b13_slot',
                        lambda slot, max_age_min=180: (arr, slot, 'u',
                                                       slot + timedelta(
                                                           minutes=11)))
    for i in range(4):
        nc = satellite.planet_frame(22.27, 113.58, 273.9, back_steps=i)
        assert nc['available'] is True
        assert nc['back_steps'] == i
        want = (slots[i][0] + timedelta(hours=8)).strftime('%H:%M')
        assert nc['slot_bjt'] == want, (i, nc['slot_bjt'], want)
        assert nc['frame_count'] == 8
        assert nc['published_min'] is not None
        assert nc['age_min'] is not None


def test_planet_frame_handles_no_slots(monkeypatch):
    monkeypatch.setattr(satellite, 'available_slots',
                        lambda n=8, max_age_min=240: [])
    nc = satellite.planet_frame(22.27, 113.58, 273.9, back_steps=0)
    assert nc['available'] is False and '无可用' in nc['reason']


def test_bt_class():
    assert satellite.bt_class(230) == '高云砧'
    assert satellite.bt_class(255) == '中云'
    assert satellite.bt_class(275) == '低云/可能晴'
    assert satellite.bt_class(300) == '晴'
    assert satellite.bt_class(float('nan')) is None


def test_otsu_bimodal_threshold():
    rng = np.random.default_rng(0)
    arr = (rng.normal(40, 20, (601, 601))).astype(np.float32)
    yy, xx = np.ogrid[:601, :601]
    disk = ((xx - satellite.CX) ** 2 + (yy - satellite.CY) ** 2) <= \
        (satellite.RAD * 0.9) ** 2
    arr[disk] = (rng.normal(200, 20, int(disk.sum()))).astype(np.float32)
    t = satellite.otsu_threshold(arr)
    assert 80 < t < 160


def test_solar_elevation_at_bounds():
    from datetime import datetime
    from app.core import astronomy as astro
    jd = astro.jd_from_datetime(datetime(2026, 9, 10, 12, 0))
    alt = satellite.solar_elevation_at(22.27, 113.58, jd)
    assert -90 <= alt <= 90


# ---------------- 光路逐段判读（v2.33：裁剪图逐段着色的数据侧） ----------------

def test_path_runs_binary_states():
    """JMA 二值判读：云/隙分段，缺测(None)断开并跳过"""
    runs = satellite._path_runs([
        {'dist': 0, 'cloud': False}, {'dist': 30, 'cloud': False},
        {'dist': 60, 'cloud': True}, {'dist': 90, 'cloud': True},
        {'dist': 120, 'cloud': None}, {'dist': 150, 'cloud': False}])
    assert runs == [(0, 30, False), (60, 90, True), (150, 150, False)]


def test_path_runs_class_states():
    """P-Tree 云类字符串：同类连续成段"""
    runs = satellite._path_runs([
        {'dist': 0, 'cloud': '晴'}, {'dist': 30, 'cloud': '晴'},
        {'dist': 60, 'cloud': '高云砧'}, {'dist': 90, 'cloud': '中云'}])
    assert runs == [(0, 30, '晴'), (60, 60, '高云砧'), (90, 90, '中云')]


def test_path_runs_empty_and_all_unknown():
    assert satellite._path_runs([]) == []
    assert satellite._path_runs([{'dist': 0, 'cloud': None}]) == []


# ---------------- 槽位探测缓存与 _ARMED 生命周期（v2.37 审查报告 ⑤⑥） ----------

def _slot_probe_fixture(monkeypatch, base, avail_offsets):
    """假 JMA：只有 base-10k（k∈avail_offsets）这些槽位有图，其余 404。

    返回 calls 计数器，用来断言"探测次数"（每次 fetch_b13_slot = 1 次 HTTP）。
    """
    from datetime import timedelta
    avail = {base - timedelta(minutes=10 * k) for k in avail_offsets}
    calls = {'n': 0}
    arr = np.full((4, 4), 40, dtype=np.float32)

    def fake_fetch(cand, max_age_min=180):
        calls['n'] += 1
        if cand in avail:
            return arr, cand, 'u', cand + timedelta(minutes=11)
        return None, cand, 'u', None

    monkeypatch.setattr(satellite, 'fetch_b13_slot', fake_fetch)
    monkeypatch.setattr(satellite, '_utcnow', lambda: base)
    satellite._SLOT_AVAIL.update({'ts': 0.0, 'list': [], 'n': 0, 'full': False})
    return calls


def test_slot_probe_caches_partial_results(monkeypatch):
    """只探到 3 个可用槽位、却要 8 个时，60 秒内不得重探。

    旧缓存条件是 ``len(list) >= n``：``out`` 只装**成功**的槽位（最多探 18 个
    候选），所以只要真实可用槽位少于 n，条件**永远为假**，每次调用都重扫
    18 次 HEAD。用户拖时间轴时这是纯浪费。
    """
    from datetime import datetime, timezone
    base = datetime(2026, 9, 14, 10, 0, tzinfo=timezone.utc)
    calls = _slot_probe_fixture(monkeypatch, base, (0, 3, 7))

    got = satellite.available_slots(8)
    assert len(got) == 3
    assert calls['n'] == 18                     # 探满 18 个候选仍凑不够 8 个
    assert len(satellite.available_slots(8)) == 3
    assert calls['n'] == 18                     # 命中缓存；旧版这里变 36


def test_slot_probe_handles_n_beyond_the_sweep(monkeypatch):
    """``planet_frame(back_steps=24)`` 要 25 个槽位，而扫描最多 18 个候选。

    旧版这里是**结构性永不命中**（3 >= 25 恒假），每拖一帧重扫 18 次。
    """
    from datetime import datetime, timezone
    base = datetime(2026, 9, 14, 10, 0, tzinfo=timezone.utc)
    calls = _slot_probe_fixture(monkeypatch, base, (0, 3, 7))

    first = satellite.available_slots(25)
    assert calls['n'] == 18
    assert len(first) == 3
    assert len(satellite.available_slots(25)) == 3
    assert calls['n'] == 18                     # 旧版这里变 36


def test_slot_probe_rescans_after_ttl(monkeypatch):
    """60 秒过期后必须重探（缓存不能变成永久的"无槽位"）。"""
    from datetime import datetime, timedelta, timezone
    base = datetime(2026, 9, 14, 10, 0, tzinfo=timezone.utc)
    calls = _slot_probe_fixture(monkeypatch, base, (0,))
    satellite.available_slots(4)
    assert calls['n'] == 18
    monkeypatch.setattr(satellite, '_utcnow',
                        lambda: base + timedelta(seconds=61))
    satellite.available_slots(4)
    assert calls['n'] == 36


def test_armed_entries_expire_after_the_event(monkeypatch):
    """事件过去 12 小时后必须清出 ``_ARMED``（旧版只增不减，常驻进程里单调涨）。"""
    from datetime import datetime, timedelta, timezone
    now = datetime(2026, 10, 8, 12, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(satellite, '_PREFETCH_STARTED', True)   # 别起后台线程
    monkeypatch.setattr(satellite, '_utcnow', lambda: now)
    satellite._ARMED.clear()

    satellite.arm_event(22.27, 113.58, '2026-10-07', 'sunset',
                        now - timedelta(hours=30))
    assert satellite._ARMED == {}               # 过期条目不得进表（旧版留在表里）

    satellite.arm_event(22.27, 113.58, '2026-10-08', 'sunset',
                        now + timedelta(hours=3))
    assert len(satellite._ARMED) == 1
    assert '2026-10-08' in next(iter(satellite._ARMED))
    satellite._ARMED.clear()


def test_armed_table_is_capped(monkeypatch):
    """条目数必须有上限（长时间运行 + 多城市/多事件不会把表撑爆）。"""
    from datetime import datetime, timedelta, timezone
    now = datetime(2026, 10, 8, 12, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(satellite, '_PREFETCH_STARTED', True)
    monkeypatch.setattr(satellite, '_utcnow', lambda: now)
    satellite._ARMED.clear()

    for i in range(satellite._ARMED_MAX + 25):
        satellite.arm_event(22.0 + i % 5, 113.0 + i % 7, f'2026-11-{i % 28 + 1:02d}',
                            f'k{i}', now + timedelta(hours=i))
    assert len(satellite._ARMED) <= satellite._ARMED_MAX
    satellite._ARMED.clear()


def test_ftp_connection_closed_on_early_continue(monkeypatch, tmp_path):
    """远端没有这个槽位时（`if not rsize: continue`）也必须关掉 FTP 连接。

    v2.37 审查报告 ⑫：旧实现只在文件已存在/下载完成时 quit()，`continue` 与
    异常路径都靠引用计数回收 —— 7 次循环就可能同时挂着 7 条控制连接。
    """
    opened = []
    closed = []

    class _FakeFTP:
        def __init__(self, host, timeout=120):
            self.host = host
            opened.append(self)

        def login(self, u, p):
            pass

        def voidcmd(self, cmd):
            pass

        def size(self, remote):
            return None                     # 远端没有该槽位

        def quit(self):
            closed.append(self)

        def close(self):
            closed.append(self)

    monkeypatch.setattr(satellite, 'FTP', _FakeFTP)
    monkeypatch.setattr(satellite, '_ptree_cfg',
                        lambda: {'host': 'h', 'user': 'u', 'pass': 'p'})
    monkeypatch.setattr(satellite, '_cache_dir', lambda: str(tmp_path))
    monkeypatch.setattr(satellite, '_ftp_blocked', lambda: False)
    monkeypatch.setattr(satellite, '_ftp_ok', lambda: None)
    monkeypatch.setattr(satellite, '_ftp_fail', lambda *a, **k: None)
    assert satellite._download_latest_slot() is None
    assert len(opened) == 7                 # 7 个候选槽位都探了一遍
    assert len(closed) == len(opened)       # 每一个都关了（旧实现：0 个）


def test_ftp_connection_closed_on_error(monkeypatch, tmp_path):
    """登录/列目录抛异常时同样要关连接。"""
    closed = []

    class _BoomFTP:
        def __init__(self, host, timeout=120):
            pass

        def login(self, u, p):
            raise OSError('connection refused')

        def close(self):
            closed.append(1)

        def quit(self):
            closed.append(1)

    monkeypatch.setattr(satellite, 'FTP', _BoomFTP)
    monkeypatch.setattr(satellite, '_ptree_cfg',
                        lambda: {'host': 'h', 'user': 'u', 'pass': 'p'})
    monkeypatch.setattr(satellite, '_cache_dir', lambda: str(tmp_path))
    monkeypatch.setattr(satellite, '_ftp_blocked', lambda: False)
    monkeypatch.setattr(satellite, '_ftp_fail', lambda *a, **k: None)
    assert satellite._download_latest_slot() is None
    assert len(closed) == 7

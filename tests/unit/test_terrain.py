# -*- coding: utf-8 -*-
"""地形服务纯函数：遮蔽角插值、光污染分级、微气候文案、缓存封顶。"""
from app.services import terrain


def test_horizon_alt_interpolation():
    mask = [0.0] * 16
    mask[0] = 10.0
    assert terrain.horizon_alt_at(mask, 0.0) == 10.0
    assert terrain.horizon_alt_at(mask, 22.5) == 0.0
    assert abs(terrain.horizon_alt_at(mask, 11.25) - 5.0) < 1e-9
    # 环绕：az=337.5 应在 15 号方位与 0 号之间
    assert terrain.horizon_alt_at(mask, 348.75) > 0


def test_bortle_mapping_monotonic():
    vals = [0.05, 0.2, 1.0, 5.0, 30.0]
    bs = [terrain._bortle_from_radiance(v) for v in vals]
    assert bs == sorted(bs)
    assert bs[0] == 1 and bs[-1] == 9
    assert terrain._bortle_from_radiance(float('nan')) is None


def test_bortle_desc():
    assert terrain.bortle_desc(3) == '乡村'
    assert terrain.bortle_desc(None) == ''


def test_microclimate_notes():
    notes = terrain.microclimate_notes(600, humidity=50, wind=3)
    assert any('海拔' in n for n in notes)
    fog = terrain.microclimate_notes(50, humidity=95, wind=2)
    assert any('雾' in n for n in fog)


def test_site_cache_bounded():
    terrain._SITE_CACHE.clear()
    for i in range(terrain._SITE_CACHE_MAX + 50):
        terrain._SITE_CACHE[(i, i)] = {'ok': True}
        if len(terrain._SITE_CACHE) > terrain._SITE_CACHE_MAX:
            for k in list(terrain._SITE_CACHE)[
                    :len(terrain._SITE_CACHE) - terrain._SITE_CACHE_MAX // 2]:
                terrain._SITE_CACHE.pop(k, None)
    assert len(terrain._SITE_CACHE) <= terrain._SITE_CACHE_MAX


def _isolate_elev(tmp_path, monkeypatch):
    """把海拔缓存指到临时目录，并当作"进程刚重启"（内存缓存清空）。"""
    monkeypatch.setattr(terrain, 'ELEV_CACHE_PATH',
                        str(tmp_path / 'elev_cache.json'))
    monkeypatch.setattr(terrain, '_ELEV_CACHE', None)
    terrain._SITE_CACHE.clear()


def test_elevation_persisted_across_restart(tmp_path, monkeypatch):
    """海拔是地形常量：落盘后重启进程也不再花上游额度（33 坐标/次）。"""
    _isolate_elev(tmp_path, monkeypatch)
    calls = []

    def fake_get(url, params, timeout=30):
        calls.append(params)
        n = len(params['latitude'].split(','))
        return {'elevation': [10.0 + i for i in range(n)]}

    monkeypatch.setattr(terrain.om, '_get', fake_get)
    lats, lons = terrain._sample_coords(22.27, 113.58)
    first = terrain.fetch_elevations(lats, lons)
    assert first and len(first) == len(lats)
    assert len(calls) == 1 and len(calls[0]['latitude'].split(',')) == 33
    assert (tmp_path / 'elev_cache.json').exists()
    monkeypatch.setattr(terrain, '_ELEV_CACHE', None)   # 模拟重启
    assert terrain.fetch_elevations(lats, lons) == first
    assert len(calls) == 1                              # 全命中，0 次上游


def test_elevation_only_missing_coords_requested(tmp_path, monkeypatch):
    """部分命中时只补缺的坐标：请求里不得再带上已缓存的点。"""
    _isolate_elev(tmp_path, monkeypatch)
    lats = [22.1, 22.2, 22.3]
    lons = [113.1, 113.2, 113.3]
    terrain._elev_cache_save({
        terrain._elev_key(22.1, 113.1): 5.0,
        terrain._elev_key(22.3, 113.3): 7.0,
    })
    got = []

    def fake_get(url, params, timeout=30):
        got.append(params['latitude'])
        return {'elevation': [6.0]}

    monkeypatch.setattr(terrain.om, '_get', fake_get)
    assert terrain.fetch_elevations(lats, lons) == [5.0, 6.0, 7.0]
    assert got == ['22.2000']


def test_elevation_upstream_failure_returns_none(tmp_path, monkeypatch):
    """上游失败仍然整体返回 None（调用方据此降级），且不写坏缓存文件。"""
    _isolate_elev(tmp_path, monkeypatch)

    def boom(*a, **k):
        raise RuntimeError('上游挂了')

    monkeypatch.setattr(terrain.om, '_get', boom)
    assert terrain.fetch_elevations([22.2], [113.2]) is None
    assert terrain._load_elev_cache() == {}


def test_elevation_cache_corrupt_file_ignored(tmp_path, monkeypatch):
    """缓存文件损坏只当作没命中，不得抛异常（坏缓存最多白花一次请求）。"""
    _isolate_elev(tmp_path, monkeypatch)
    (tmp_path / 'elev_cache.json').write_text('{坏文件', encoding='utf-8')
    monkeypatch.setattr(terrain.om, '_get', lambda *a, **k: {'elevation': [3.0]})
    assert terrain.fetch_elevations([22.2], [113.2]) == [3.0]

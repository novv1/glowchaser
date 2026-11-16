# -*- coding: utf-8 -*-
"""VIIRS 光污染栅格的加载失败必须可重试（v2.37 审查报告 ⑪）。

旧实现：任何异常（含 tifffile 未安装、文件被占用、刚拷到一半）都写
`_tif_cache['loaded'] = True` 后返回 None —— 等于**一次瞬时失败就把光污染
永久降级成启发式**，直到进程重启。
"""
import sys
import types

import pytest

from app.services import terrain


def _iso(monkeypatch, path):
    monkeypatch.setattr(terrain, 'TIF_PATH', str(path))
    monkeypatch.setattr(terrain, '_tif_cache',
                        {'loaded': False, 'data': None, 'meta': None})
    monkeypatch.setattr(terrain, 'TIF_RETRY_SEC', 0)   # 测试里不留冷却期
    return path


def test_tif_corrupt_file_is_not_pinned(tmp_path, monkeypatch):
    p = _iso(monkeypatch, tmp_path / 'lp.tif')
    p.write_bytes(b'not-a-tiff-at-all')
    assert terrain._load_tif() is None
    assert terrain._tif_cache['loaded'] is False       # 旧代码这里被钉成 True
    assert terrain._tif_cache.get('fails') == 1
    assert terrain._load_tif() is None                 # 冷却期后确实又试了一次
    assert terrain._tif_cache['fails'] == 2


def test_tif_missing_file_is_rechecked(tmp_path, monkeypatch):
    """文件还没下载好时不能永久钉死：出现后必须能被发现。"""
    _iso(monkeypatch, tmp_path / 'lp.tif')
    assert terrain._load_tif() is None
    assert terrain._tif_cache['loaded'] is False
    assert terrain._tif_cache['fails'] == 1


def test_tif_retry_is_bounded(tmp_path, monkeypatch):
    """连续失败到上限后停止重试：不能让每个请求都去开一次大文件。"""
    p = _iso(monkeypatch, tmp_path / 'lp.tif')
    p.write_bytes(b'broken')
    for _ in range(terrain.TIF_MAX_FAILS + 5):
        assert terrain._load_tif() is None
    assert terrain._tif_cache['fails'] == terrain.TIF_MAX_FAILS


def test_tif_retry_respects_cooldown(tmp_path, monkeypatch):
    """冷却期内不得反复重试（同一秒里 10 个请求只允许试 1 次）。"""
    p = _iso(monkeypatch, tmp_path / 'lp.tif')
    p.write_bytes(b'broken')
    monkeypatch.setattr(terrain, 'TIF_RETRY_SEC', 3600)
    assert terrain._load_tif() is None
    assert terrain._tif_cache['fails'] == 1
    for _ in range(5):
        assert terrain._load_tif() is None
    assert terrain._tif_cache['fails'] == 1            # 冷却期内没有重试


def test_tif_success_path_loads_and_pins(tmp_path, monkeypatch):
    """成功加载后仍然钉住（好的结果才该永久缓存）。"""
    p = _iso(monkeypatch, tmp_path / 'lp.tif')
    p.write_bytes(b'dummy')

    class _Tag:
        def __init__(self, v):
            self.value = v

    np = pytest.importorskip('numpy')

    class _Page:
        tags = {'ModelPixelScaleTag': _Tag((0.01, 0.01, 0.0)),
                'ModelTiepointTag': _Tag((0.0, 0.0, 0.0, 110.0, 25.0, 0.0))}

        def asarray(self):
            return np.array([[1.0, 2.0], [3.0, 4.0]], dtype=np.float32)

    class _Tif:
        pages = [_Page()]

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    fake = types.ModuleType('tifffile')
    fake.TiffFile = lambda path: _Tif()
    monkeypatch.setitem(sys.modules, 'tifffile', fake)
    pytest.importorskip('numpy')
    data = terrain._load_tif()
    assert data is not None
    assert terrain._tif_cache['loaded'] is True
    assert terrain._tif_cache['fails'] == 0
    assert terrain._load_tif() is data                 # 第二次直接命中

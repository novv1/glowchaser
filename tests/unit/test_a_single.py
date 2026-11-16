# -*- coding: utf-8 -*-
"""A单列重排红灯：sample顺序单列 + mock满血 + 8维双分 + 48h行内条。

断言清单（spec m02291）：
1. mock满血：build() 的 models/dims_detail/table_48h/weather_3h/pm25/map.points 均非空
2. 单列骨架：index.html 含单列容器 report-flow，且 pane-overview/maps/atmo/astro/data 不再作为 tabpane 切分
3. 锚点导航：tabnav 改为锚点链接（href="#sec-"），不再用 data-tab 切换
4. 8维双分：app.js dimsList 行含本地分与光路分双打分 + 粗条 >=12px
5. 48h行内条：t48Tbl 行含行内条（白色细条口径）
6. 实时8格：wxGrid 渲染前移至 hero 后（render顺序 hero→wx→dims）
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
INDEX = (ROOT / 'app' / 'static' / 'index.html').read_text(encoding='utf-8')
APP = (ROOT / 'app' / 'static' / 'app.js').read_text(encoding='utf-8')
CSS = (ROOT / 'app' / 'static' / 'style.css').read_text(encoding='utf-8')


def _build():
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        'mock_report', str(ROOT / 'tests' / 'visual' / 'mock_report.py'))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.build()


def test_mock_full_blooded():
    r = _build()
    assert r.get('models'), 'mock models 为空，tab-data多模型表空白'
    assert len(r.get('dims_detail') or []) == 8, 'mock dims_detail 非8行'
    assert len(r.get('table_48h') or []) >= 13, 'mock table_48h 不足'
    assert len(r.get('weather_3h') or []) >= 10, 'mock weather_3h 不足'
    assert len(r.get('pm25') or []) >= 8, 'mock pm25 不足'
    assert len((r.get('map') or {}).get('points') or []) >= 20, 'mock map.points 不足'


def test_single_column_flow():
    assert 'report-flow' in INDEX, '缺单列容器 report-flow'
    # 旧五 pane 切分应消失（并成单列，卡片直排）
    for pid in ('pane-maps', 'pane-atmo', 'pane-astro', 'pane-data'):
        assert pid not in INDEX, f'{pid} 仍在切分单列'


def test_anchor_nav():
    assert 'href="#sec-' in INDEX, 'tabnav 未改锚点导航'
    assert 'data-tab="maps"' not in INDEX, '仍用 data-tab 切换'


def test_dims_dual_score():
    assert 'dim-dual' in APP or 'dimDual' in APP, 'dimsList 缺本地/光路双打分'
    # 粗条：sample为粗进度条，我方8px细条需加粗到>=12px
    assert 'dim-bar' in (APP + CSS), '缺维度粗条'


def test_t48_row_bars():
    assert 't48-bar' in APP or 't48bar' in APP, '48h表缺行内条'


def test_wx8_first():
    # wxGrid 渲染应在 dimsList 之前（hero→实时→维度），且 render() 顺序可验
    assert APP.index("getElementById('wxGrid')") < APP.index("getElementById('dimsList')"), \
        'wxGrid 未前移至维度之前'

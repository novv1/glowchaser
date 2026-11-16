# -*- coding: utf-8 -*-
"""M0 · 单一主题契约（红绿纪律的红灯测试）

设计决策（2026-10-23）：前端收敛为唯一主题「日暮片场」。
- 不再有 data-skin / data-theme / data-sky 三个属性；
- 不再有皮肤面板、主题按钮、深浅切换；
- style.css 只有一套令牌块；cinema.css 不再按皮肤作用域。
"""
import os

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _read(rel):
    with open(os.path.join(ROOT, rel), encoding='utf-8') as f:
        return f.read()


def test_html_has_no_theme_attributes():
    html = _read('app/static/index.html')
    assert 'data-skin' not in html
    assert 'data-theme' not in html
    assert 'data-sky' not in html
    assert 'gc_skin' not in html, '启动脚本不应再读旧的皮肤键'


def test_style_css_has_exactly_one_token_block():
    css = _read('app/static/style.css')
    assert css.count(':root{') == 1, '只允许一套令牌'
    assert 'data-skin' not in css
    assert 'data-theme' not in css
    assert 'data-sky' not in css
    assert css.count('--accent:') == 1, '令牌不许重复声明'


def test_cinema_css_is_unscoped():
    css = _read('app/static/cinema.css')
    assert 'data-skin' not in css, '主题层不再按皮肤隔离'
    assert 'html:not(' not in css, '否定规则（非 cinema 隐藏）必须随皮肤一起删掉'
    assert 'html:root' in css
    assert '--gold' in css and '--grain-op' in css
    assert '.grain' in css and '.vig' in css


def test_theme_js_is_gone():
    """皮肤/明暗体系的最后载体 theme.js 必须彻底移除。"""
    assert not os.path.exists(os.path.join(ROOT, 'app/static/theme.js')), \
        'theme.js 只服务于多皮肤，单一主题后不应存在'
    html = _read('app/static/index.html')
    assert 'theme.js' not in html


def test_no_theme_button_anywhere():
    html = _read('app/static/index.html')
    app = _read('app/static/app.js')
    assert 'themeBtn' not in html
    assert 'themeBtn' not in app
    assert 'theme-panel' not in html


def test_palette_generator_emits_one_theme():
    import sys
    sys.path.insert(0, os.path.join(ROOT, 'tests', 'visual'))
    import theme_preview
    assert len(theme_preview.PALETTES) == 1, '生成器只应保留一套配色'
    css = theme_preview.emit_css()
    assert 'data-skin' not in css
    assert css.count(':root{') == 1

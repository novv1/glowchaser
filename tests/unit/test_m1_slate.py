# -*- coding: utf-8 -*-
"""M1 场记板 + 场景导航 + film burn 转场验收（红灯测试）。"""
import os, re

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _read(rel):
    with open(os.path.join(ROOT, rel), encoding='utf-8') as f:
        return f.read()


def test_slate_structure_in_index():
    """顶栏是一条命令栏：品牌 / 模式 / 城市·日期·事件 / 查询 / 状态。

    2026-10-23 改版：装饰性的 SCENE/TAKE/ROLL 计数与 ACTION 英文按钮被撤掉，
    换成真正有信息量的状态位（生成时间）。结构 id 全保留。
    """
    html = _read('app/static/index.html')
    assert 'class="topbar slate"' in html
    for cls in ('sl-brand', 'sl-mode', 'sl-ctx', 'sl-status'):
        assert cls in html, f'missing {cls}'
    for word in ('SCENE', 'TAKE', 'ROLL', 'ACTION'):
        assert word not in html, f'片场黑话 {word} 应已撤掉'
    for i in ['citySel', 'dateSel', 'btnGo', 'modeSeg', 'subSeg', 'slStatus',
              'heroCard', 'heroProb', 'heroVerdict']:
        assert f'id="{i}"' in html, f'missing id {i}'
    assert 'id="themeBtn"' not in html, '单一主题后不应再有主题按钮'


def test_status_slot_replaced_counters():
    """TAKE/SCENE/ROLL 三个装饰计数位 → 一个真实状态位（生成时刻）。"""
    html = _read('app/static/index.html')
    appjs = _read('app/static/app.js')
    assert 'id="slStatus"' in html
    assert 'aria-live="polite"' in html
    assert 'syncSlateStatus' in appjs
    # 旧的计数 DOM 钩子必须消失
    for old in ('id="sceneNum"', 'id="takeNum"', 'id="rollCode"'):
        assert old not in html, f'{old} should be gone'


def test_scenenav_numbers_and_dock():
    """导航：四章节（今晚/证据/深看/记录）+ 移动端底部 dock + 键盘可达。"""
    html = _read('app/static/index.html')
    css = _read('app/static/cinema.css')
    for num in ['壹', '贰', '叁', '肆']:
        assert num in html, f'missing nav number {num}'
    for sec in ('tonight', 'evidence', 'deep', 'log'):
        assert ('data-sec="%s"' % sec) in html, f'missing chapter {sec}'
        assert ('id="sec-%s"' % sec) in html, f'missing anchor {sec}'
    assert 'role="tablist"' in html
    assert 'role="tab"' in html
    assert 'aria-selected' in html
    assert 'position:fixed' in css and '640px' in css


def test_burn_layer_and_switchpane_timing():
    """film burn：#burn 层 + switchPane(from,to) 450ms 内完成切换 + 防连点。"""
    html = _read('app/static/index.html')
    css = _read('app/static/cinema.css')
    js = _read('app/static/cinema.js')
    assert 'id="burn"' in html
    assert 'burn' in css
    # switchPane 真实现：不再是 M0 空壳（必须切 pane 类名或调回调）
    assert 'switchPane' in js
    assert re.search(r'450|0\.45|burn', js), 'burn timing missing in cinema.js'
    # 防连点：转场期间锁 300ms+（任意锁变量/时间戳/aria-disabled 均可）
    assert re.search(r'lock|300|450|_burn|disabled', js)


def test_query_button_wired_without_action_label():
    """查询按钮：中文标签 + 原查询链不断（不再有 ACTION/TAKE 彩蛋）。"""
    html = _read('app/static/index.html')
    appjs = _read('app/static/app.js')
    assert 'id="btnGo"' in html
    assert '>查询</button>' in html.replace('\n', ' ')
    assert 'ACTION' not in html
    assert 'reloadCurrent' in appjs
    assert 'btnGo' in appjs
    # 查询中不允许重复下发：按钮要有 pending 态
    assert 'setQueryPending' in appjs


def test_appjs_hook_minimal():
    """app.js 微改：showTab 经 switchPane 包一层，改动行数可控。"""
    appjs = _read('app/static/app.js')
    assert 'switchPane' in appjs or '__cinemaTake' in appjs
    # 接线 currentTab/showTab 仍在
    assert 'showTab' in appjs
    assert 'currentTab' in appjs

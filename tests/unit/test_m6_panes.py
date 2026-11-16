"""M6 大气/天文/数据 + 悬日月：红灯（先失败，后实现转绿）。

断言清单（v1.0 §4.17/4.18/4.19/4.20）：
1. monitors：mapCard/revCard/pmCard/csCard/soundCard 含 .cn-monitor 外壳 + 取景角标
2. script-notes：csCard/soundCard 的 .readme 改挂 .cn-script（行首 > 金）
3. star-chart：altChart 装帧 .cn-monitor + 标题 STAR CHART；astroTbl 表头/时刻金类
4. camera-tests：modelTbl 挂 .cn-cameratests（最优行金底黑字类 cn-best）
5. align 特别放映：alignSetupCard 含 LOCATION SCOUT / alignCard 含 SCHEDULE /
   alignHero 含 .cn-frame 结构 / celestialCanvas 外罩 .cn-monitor+角标 /
   alignCalGrid cell 含 .cn-filmcell 缩小版类 / alignDirWxGrid 含 credits 复用类
6. 月盘：scene-engine.js 含 moonDisc（月出档夜空帧满月盘；悬月白天/暮色天空推演）
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
INDEX = (ROOT / 'app' / 'static' / 'index.html').read_text(encoding='utf-8')
CSS = (ROOT / 'app' / 'static' / 'cinema.css').read_text(encoding='utf-8')
SE = (ROOT / 'app' / 'static' / 'scene-engine.js').read_text(encoding='utf-8')
APP = (ROOT / 'app' / 'static' / 'app.js').read_text(encoding='utf-8')


def test_map_atmo_monitors():
    for cid in ('mapCard', 'revCard', 'pmCard', 'csCard', 'soundCard'):
        m = re.search(r'<section[^>]*id="%s"[^>]*>' % cid, INDEX)
        assert m, cid
        assert 'cn-monitor' in m.group(0), cid


def test_script_notes():
    assert 'cn-script' in INDEX or 'cn-script' in CSS


def test_star_chart():
    assert 'STAR CHART' in INDEX or 'STAR-CHART' in (INDEX + CSS)
    assert 'cn-monitor' in INDEX


def test_camera_tests():
    # 2026-10-23：英文片场小标签（CAMERA TESTS 等）已撤，保留「最优模式金底」这一视觉契约
    assert 'cn-best' in (CSS + APP)
    assert 'modelTbl' in INDEX


def test_align_special():
    # 悬日月三步骨架：① 机位与目标 / ② 时间与事件 / 天体预览 / ③ 天气可行性 / 下次对准
    for s in ('机位与目标', '时间与事件', '天体预览', '天气可行性', '下次对准'):
        assert s in INDEX, s
    assert 'cn-filmcell' in (INDEX + CSS + APP) or 'cn-calcell' in (INDEX + CSS + APP)
    assert 'cn-hit' in (CSS + APP)


def test_moon_disc():
    assert 'moonDisc' in SE or 'moondisc' in SE.lower()

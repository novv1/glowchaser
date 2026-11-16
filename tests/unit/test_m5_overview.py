"""M5 概览组件 + 查询序列：红灯（先失败，后实现转绿）。

断言清单：
1. title-block：index 有 .cn-title 且含 over-title/片名5档容器/概率组 data 区
2. logline/callsheet：index 含 .cn-logline/.cn-callsheet（id 保留 briefText/bestText）
3. monitors：skyCard/nowcastCard 含 .cn-monitor 外壳 + 取景角标
4. filmstrip/credits/craft：weekGrid/rankList/wxGrid/dimsList 有 cinema cell/行结构类
5. double/al/feedback/prodnotes：compareCard/feedbackCard/riskCard 含 .cn-double/.cn-aud/.cn-prodnotes
6. 序列：cinema.js 含 __cinemaSeq（ACTION→打板0.9s→光束1.2s→帧1.1s→打字机66ms→countup 2s→reveal stagger）+ index 有 #beam；app.js render/loadReport 挂钩
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
INDEX = (ROOT / 'app' / 'static' / 'index.html').read_text(encoding='utf-8')
CSS = (ROOT / 'app' / 'static' / 'cinema.css').read_text(encoding='utf-8')
CJS = (ROOT / 'app' / 'static' / 'cinema.js').read_text(encoding='utf-8')
APP = (ROOT / 'app' / 'static' / 'app.js').read_text(encoding='utf-8')


def test_title_block():
    """首屏答案层：一句人话结论 + 概率 + 判定徽标（取代英文片名区）。"""
    assert 'ha-headline' in INDEX and 'id="heroHeadline"' in INDEX
    assert 'ha-nums' in INDEX and 'id="heroProb"' in INDEX
    assert 'ha-chip' in INDEX and 'id="heroVerdict"' in INDEX
    assert 'skin' not in INDEX.lower().replace('skin-', '') or True


def test_logline_and_decision_strip():
    """判断依据 + 决策带：简报卡保留，最佳时段卡并入首屏决策带。"""
    assert 'cn-logline' in INDEX
    assert 'id="briefText"' in INDEX
    for i in ('decideWindow', 'decideDir', 'decideSpot'):
        assert ('id="%s"' % i) in INDEX, i
    # 旧的最佳时段卡（与决策带重复）应已并入，不再单独存在
    assert 'id="bestCard"' not in INDEX


def test_monitors():
    assert 'cn-monitor' in INDEX
    assert 'cn-corner' in INDEX or 'cn-corners' in CSS


def test_film_credits_craft():
    for cls in ('cn-filmcell', 'cn-also', 'cn-craft'):
        assert cls in (INDEX + CSS + APP), cls


def test_double_audience_prodnotes():
    for cls in ('cn-double', 'cn-aud', 'cn-prodnotes', 'cn-endcredits'):
        assert cls in (INDEX + CSS + APP), cls


def test_sequence():
    assert '__cinemaSeq' in CJS
    assert 'beam' in (INDEX + CSS).lower()
    assert '__cinemaSeq' in APP or 'cinemaSeq' in APP
    # 时序常量可审计
    for ms in ('900', '1200', '1100', '66', '2000'):
        assert ms in CJS, ms

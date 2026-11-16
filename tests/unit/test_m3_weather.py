# -*- coding: utf-8 -*-
"""M3 红灯：雨3档×闪电单例×地景联动（storm天板/太阳/水面/灯火×.6）。"""
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ENG = ROOT / 'app' / 'static' / 'scene-engine.js'


def test_engine_has_m3_units():
    """雨参数表/雨丝循环/闪电单例/水面灯火函数存在。"""
    src = ENG.read_text(encoding='utf-8')
    for needle in ['RAIN_P', 'rainDrops', 'boltRunning', 'groundSync', 'cityTw',
                   'stormMix', 'rainAlpha']:
        assert needle in src, f'scene-engine.js 缺 {needle}'


def test_m3_runtime_node():
    """node直跑：3档雨丝数/雨幕alpha/单例/水面公式/storm规则。"""
    js = r"""
const fs=require('fs');
let src=fs.readFileSync('app/static/scene-engine.js','utf8');
src=src.replace(/export\s+\{/,'/*EXP{').replace(/document\./g,'null.');
eval(src+';globalThis.__E={RAIN_P,rainAlpha,stormMix,waterCols};');
const d=__E.RAIN_P.drizzle,h=__E.RAIN_P.heavy,s=__E.RAIN_P.storm;
console.log('DROPS:'+[d.n,h.n,s.n].join(','));
console.log('ALPHA:'+[__E.rainAlpha('drizzle'),__E.rainAlpha('heavy'),__E.rainAlpha('storm'),__E.rainAlpha('none')].join(','));
console.log('STORM:'+__E.stormMix([[22,39,62],[55,67,95]]).map(c=>c.join('.')).join('|').slice(0,24));
const w=__E.waterCols(78,-2),w2=__E.waterCols(10,-6);
console.log('WATER:'+w.map(c=>c.join('.')).join('|').slice(0,24));
console.log('WATERD:'+(w.join()!==w2.join()?'DIFF':'SAME'));
"""
    r = subprocess.run(['node', '-e', js], capture_output=True, text=True, cwd=str(ROOT))
    assert r.returncode == 0, f'node失败: {r.stderr[:300]}'
    assert 'DROPS:130,340,220' in r.stdout, f'雨丝数不对: {r.stdout[:200]}'
    assert 'ALPHA:0.12,0.3,0.22,0' in r.stdout, f'雨幕alpha不对: {r.stdout[:200]}'
    assert 'WATERD:DIFF' in r.stdout, f'水面不随lit/alt变: {r.stdout[:200]}'


def test_m3_ground_rules():
    """rain!=none无太阳/storm天板青灰/lit<=20/灯火×.6/road规则有实现。"""
    src = ENG.read_text(encoding='utf-8')
    for needle in ["rain!=='none'", 'storm', 'boltOnce', 'cityTwinkle',
                   "rain==='none'", 'lampsDim']:
        assert needle in src, f'缺地景规则 {needle}'

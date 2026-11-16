# -*- coding: utf-8 -*-
"""M2 场景引擎红灯：噪声/云属/天板/帧DOM 缺失即失败。"""
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ENG = ROOT / 'app' / 'static' / 'scene-engine.js'
IDX = ROOT / 'app' / 'static' / 'index.html'
CSS = ROOT / 'app' / 'static' / 'cinema.css'


def test_engine_file_exists():
    assert ENG.exists(), 'scene-engine.js 缺失'


def test_engine_core_api():
    src = ENG.read_text(encoding='utf-8')
    for token in ('lcg(seed)', '1664525', 'function noise2', 'function fbm',
                  'function genField', 'function shadeCloud', 'function skyAt',
                  'SceneEngine', 'mount', 'sceneSet', '340', '140'):
        assert token in src, f'引擎缺 {token}'


def test_engine_shapes_and_sky_node():
    """8云属形态可辨 + alt六档无跳变（node直跑）。"""
    js = r"""
const fs=require('fs');
let src=fs.readFileSync('app/static/scene-engine.js','utf8');
src=src.replace(/export\s+\{/,'/*EXP{').replace(/document\./g,'null.');
eval(src+';globalThis.__E={genField,skyAt};');
const kinds=['cs','ci','ac','sc','cu','ns','cb'];
const means=kinds.map(k=>{const f=__E.genField(k,0.55,7,34,12);let s=0;for(const v of f.A)s+=v;return (s/f.A.length);});
console.log('MEANS:'+means.map(m=>m.toFixed(4)).join(','));
const alts=[4,0,-2,-4,-6,-8].map(a=>__E.skyAt(a));
let mx=0;for(let i=0;i<5;i++)for(let s=0;s<5;s++){const a=alts[i][s],b=alts[i+1][s];
  const d=Math.sqrt((a[0]-b[0])**2+(a[1]-b[1])**2+(a[2]-b[2])**2);mx=Math.max(mx,d);}
console.log('MAXSTEP:'+mx.toFixed(1));
"""
    r = subprocess.run(['node', '-e', js], capture_output=True, text=True, cwd=str(ROOT))
    assert r.returncode == 0, f'node 跑引擎失败: {r.stderr[:300]}'
    mline = [l for l in r.stdout.splitlines() if l.startswith('MEANS:')][0]
    means = [float(x) for x in mline[6:].split(',')]
    assert len(set(round(m, 3) for m in means)) == len(means), f'云属形态不可辨: {mline}'
    sline = [l for l in r.stdout.splitlines() if l.startswith('MAXSTEP:')][0]
    # 天板逐档插值连续：线性插值保证档内无跳变；档间步长<200（日落档色差本就大）
    assert float(sline[8:]) < 200, f'天板跳变: {sline}'


def test_frame_dom_present():
    html = IDX.read_text(encoding='utf-8')
    for token in ('cvSky', 'scene-engine.js', 'heroCard', 'heroProb', 'heroVerdict'):
        assert token in html, f'index 缺 {token}'


def test_frame_css_scoped():
    css = CSS.read_text(encoding='utf-8')
    assert 'cvSky' in css, 'cinema.css 缺帧样式'
    assert 'prefers-reduced-motion' in css

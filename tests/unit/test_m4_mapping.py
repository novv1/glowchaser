# -*- coding: utf-8 -*-
"""M4 红灯：mapReportToScene 纯函数（§3 映射表·真实报告字段）+ render 钩子 + ?debug=1 调参台。"""
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ENG = ROOT / 'app' / 'static' / 'scene-engine.js'
APP = ROOT / 'app' / 'static' / 'app.js'


def test_map_fn_exists():
    """mapReportToScene 在引擎内可导出。"""
    src = ENG.read_text(encoding='utf-8')
    assert 'function mapReportToScene' in src, 'scene-engine.js 缺 mapReportToScene 定义'
    assert 'mapReportToScene:mapReportToScene' in src, 'SceneEngine 未导出 mapReportToScene'


def test_map_rules_node():
    """node直跑 §3 规则（真实字段）：genus优先级/rain档/lit=prob/seed稳定/alt链路。"""
    js = r"""
const fs=require('fs');
let src=fs.readFileSync('app/static/scene-engine.js','utf8');
eval(src+';globalThis.__M={mapReportToScene};');
// Ns：低云>85 且有降水
const low={main:{cloud:{total:100,low:90,mid:20,high:10},weather:{precip_prob:80}},probability:80,meta:{date:'2026-10-08'},tracks:{time:['18:10'],sun_alt:[-2]},astronomy:{key_time:'18:10'}};
console.log('NS:'+__M.mapReportToScene(low,{cityId:'zh'}).genus);
// Cb：risks 雷暴文案 -> storm
const cb={main:{cloud:{total:100,low:30,mid:40,high:60},weather:{precip_prob:20}},probability:55,risks:['雷暴大风'],meta:{date:'2026-10-08'},tracks:{time:['18:10'],sun_alt:[-1]},astronomy:{key_time:'18:10'}};
const cbp=__M.mapReportToScene(cb,{cityId:'zh'});
console.log('CB:'+cbp.genus+','+cbp.rain);
// seed 稳定（同 cityId+date+genus）
const a=__M.mapReportToScene({main:{cloud:{total:70,low:10,mid:10,high:60},weather:{}},probability:70,meta:{date:'2026-10-08'}},{cityId:'zh'});
const b=__M.mapReportToScene({main:{cloud:{total:70,low:10,mid:10,high:60},weather:{}},probability:70,meta:{date:'2026-10-08'}},{cityId:'zh'});
console.log('SEED:'+(a.seed===b.seed?'STABLE':'JITTER')+','+a.seed);
// alt：illumination 优先 > tracks 索引 > -2 默认；25 被夹到 4
console.log('ALT:'+__M.mapReportToScene({main:{cloud:{total:10},weather:{}},probability:50,meta:{},illumination:{sun_alt_deg:25}},{}).alt);
console.log('ALT2:'+__M.mapReportToScene({main:{cloud:{total:10},weather:{}},probability:50,meta:{},tracks:{time:['18:10'],sun_alt:[-3.5]},astronomy:{key_time:'18:10'}},{}).alt);
// lit=prob；rain!=none 时 <=20
console.log('LIT:'+__M.mapReportToScene({main:{cloud:{total:15,low:5,mid:5,high:5},weather:{precip_prob:5}},probability:66,meta:{}},{}).lit);
console.log('LITR:'+__M.mapReportToScene({main:{cloud:{total:90,low:30,mid:30,high:30},weather:{precip_prob:80}},probability:80,meta:{}},{}).lit);
// clear：总云量<12
console.log('CLEAR:'+__M.mapReportToScene({main:{cloud:{total:5,low:2,mid:2,high:2},weather:{}},probability:5,meta:{}},{}).genus);
"""
    r = subprocess.run(['node', '-e', js], capture_output=True, text=True, cwd=str(ROOT))
    assert r.returncode == 0, f'node失败: {r.stderr[:300]}'
    assert 'NS:ns' in r.stdout, f'Ns优先级错: {r.stdout[:200]}'
    assert 'CB:cb,storm' in r.stdout, f'雷暴→storm错: {r.stdout[:200]}'
    assert 'SEED:STABLE' in r.stdout, f'seed不稳定: {r.stdout[:200]}'
    assert 'ALT:4' in r.stdout, f'alt未夹取: {r.stdout[:200]}'
    assert 'ALT2:-3.5' in r.stdout, f'tracks索引alt错: {r.stdout[:200]}'
    assert 'LIT:66' in r.stdout, f'lit!=prob: {r.stdout[:200]}'
    assert 'LITR:20' in r.stdout, f'降水lit未压到20: {r.stdout[:200]}'
    assert 'CLEAR:clear' in r.stdout, f'clear错: {r.stdout[:200]}'


def test_render_hook_and_debug():
    """render() 尾调 SceneEngine.set(mapReportToScene)；?debug=1 调参台。"""
    app = APP.read_text(encoding='utf-8')
    assert 'mapReportToScene' in app, 'app.js 未接 mapReportToScene'
    assert 'SceneEngine' in app and '.set(' in app, 'app.js 未调 SceneEngine.set'
    assert 'debug=1' in app, '?debug=1 调参台缺失'
    assert 'bindSceneDebug' in app, 'bindSceneDebug 缺失'

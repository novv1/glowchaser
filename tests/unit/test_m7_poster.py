# -*- coding: utf-8 -*-
"""M7 红灯：海报离屏场景渲染 + 性能/降级/移动端收口（v2.0 §4 海报行 / §7 M7 / 附录B#4#5）。

断言清单：
1. renderStill：引擎导出离屏静帧（复用 draw* 同一套函数）；node 下可用注入 canvas 工厂跑；
   无 document 且无工厂 → 返回 null（调用方回退原生渐变，不抛错）；渲染后实时参数不变。
2. 离屏=页面同场景：同一参数两次渲染 ops 签名一致（确定性）；渲染不写回 SceneP。
3. mapAlignToScene：悬日月窗口 → 场景参数（云量取视线交点最厚层、悬月→moon:'full' 且云染色 ×.3）。
4. 海报接线：share.js 两个海报（buildShareImage / buildAlignShareImage）顶部帧都走
   SceneEngine.renderStill；画面卡在 1920 内不溢出。
5. 移动端：帧 16:10（cinema.css）、星点 90→45、雨丝 ×.6、灯火 11→6（附录B#5）。
6. 时间码：帧号按 24fps 跑码（附录B#4），1s 采样不再是恒 00。
7. 性能：stats() 暴露 frames/regen/genField；dt 封顶 50ms；regen debounce 140ms。
"""
import json
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
STATIC = ROOT / 'app' / 'static'
SE_PATH = STATIC / 'scene-engine.js'
SE = SE_PATH.read_text(encoding='utf-8')
APP = (STATIC / 'app.js').read_text(encoding='utf-8')
SHARE = (STATIC / 'share.js').read_text(encoding='utf-8')
CSS = (STATIC / 'cinema.css').read_text(encoding='utf-8')

_HARNESS_HEAD = r"""
const fs=require('fs');
let src=fs.readFileSync('app/static/scene-engine.js','utf8');
eval(src+';globalThis.__E=SceneEngine;');
const ops=[];
function mkctx(tag){
  const store={};
  const grad=()=>({addColorStop:(o,c)=>ops.push(tag+':stop:'+o+'='+c)});
  const base={
    createLinearGradient:()=>{ops.push(tag+':linGrad');return grad();},
    createRadialGradient:()=>{ops.push(tag+':radGrad');return grad();},
    createImageData:(w,h)=>({data:new Uint8ClampedArray(w*h*4),width:w,height:h}),
    putImageData:()=>ops.push(tag+':put'),
    fillRect:(x,y,w,h)=>ops.push(tag+':fillRect:'+Math.round(w)+'x'+Math.round(h)),
    clearRect:()=>{}, measureText:()=>({width:10}),
    arc:(x,y,r)=>ops.push(tag+':arc:'+Math.round(r)),
    drawImage:(im,x,y,w,h)=>ops.push(tag+':drawImage:'+Math.round(w)+'x'+Math.round(h))
  };
  return new Proxy(base,{
    get(t,k){ if(k in t)return t[k]; if(k in store)return store[k];
      return (...a)=>ops.push(tag+':'+String(k)); },
    set(t,k,v){ store[k]=v;
      if(k==='fillStyle'||k==='strokeStyle')ops.push(tag+':'+String(k)+'='+String(v));
      return true; }
  });
}
function mkcanvas(w,h){const cv={width:w,height:h,clientWidth:w,clientHeight:h};
  cv.getContext=function(){ if(!cv._c)cv._c=mkctx('c'+ops.length); return cv._c; };return cv;}
"""


def _node(js):
    r = subprocess.run(['node', '-e', _HARNESS_HEAD + js], capture_output=True,
                       text=True, encoding='utf-8', errors='replace', cwd=str(ROOT))
    assert r.returncode == 0, f'node失败: {r.stderr[:600]}'
    return r.stdout


def test_renderstill_exported():
    assert 'function renderStill' in SE, 'scene-engine.js 缺 renderStill'
    assert 'renderStill:renderStill' in SE, 'SceneEngine 未导出 renderStill'
    assert 'makeCanvas' in SE, 'shadeCloud 仍硬依赖 document.createElement（离屏无法建云场）'


def test_renderstill_offscreen_and_state_kept():
    """离屏出图：走同一套 draw*；渲染后实时参数/画布尺寸原样；无工厂时返回 null。"""
    out = _node(r"""
const before=JSON.stringify(__E.params());
const noFactory=__E.renderStill({w:200,h:100});     // node 无 document：应诚实返回 null
__E.__setCanvasFactory(mkcanvas);
__E.set({genus:'ci',cover:45,lit:72,alt:-6.2,rain:'none',seed:11,moon:'full'});
const live=JSON.stringify(__E.params());
ops.length=0;
const cv=mkcanvas(968,400);
const r=__E.renderStill({w:968,h:400,canvas:cv});
const sig=ops.join('|');
console.log('NULL:'+(noFactory===null?'OK':'BAD'));
console.log('RET:'+(r===cv?'OK':'BAD'+(r&&r.width)));
console.log('SKY:'+(sig.indexOf('linGrad')>=0?'OK':'BAD'));
console.log('SUN:'+(/arc:/.test(sig)?'OK':'BAD'));
console.log('MOON:'+(sig.indexOf('stop:0=rgba(242,240,232')>=0?'OK':'BAD'));
console.log('CLOUD:'+(sig.indexOf('drawImage:')>=0?'OK':'BAD'));
console.log('STATE:'+(JSON.stringify(__E.params())===live?'KEPT':'DIRTY'));
console.log('SIZE:'+(cv.width+'x'+cv.height));
console.log('FRAME:'+(before!==live?'OK':'BAD'));
// 同参二次渲染 → ops 签名一致（确定性：海报=页面同场景）
ops.length=0;__E.renderStill({w:968,h:400,canvas:mkcanvas(968,400)});
console.log('DETERMIN:'+(ops.join('|')===sig?'SAME':'DRIFT'));
// 失败恢复：坏 canvas → null，且不污染实时状态
const badd={width:10,height:10,getContext:function(){throw new Error('boom');}};
console.log('BADRET:'+(__E.renderStill({w:10,h:10,canvas:badd})===null?'OK':'BAD'));
console.log('STATE2:'+(JSON.stringify(__E.params())===live?'KEPT':'DIRTY'));
console.log('STILL:'+(__E.stats().still>=1?'OK':'BAD'));
""")
    for key in ('NULL:OK', 'RET:OK', 'SKY:OK', 'SUN:OK', 'MOON:OK', 'CLOUD:OK',
                'STATE:KEPT', 'DETERMIN:SAME', 'BADRET:OK', 'STATE2:KEPT', 'STILL:OK',
                'SIZE:968x400', 'FRAME:OK'):
        assert key in out, f'{key} 未满足: {out[:600]}'


def test_map_align_to_scene():
    """悬日月窗口 → 场景参数：云量取视线交点最厚层；悬月满月盘 + 云染色 ×.3。"""
    out = _node(r"""
const w={type:'悬月',body:'moon',period:'moonset',period_name:'月落',date:'2026-09-10',
  best_time:'05:12',best_alt:1.6,best_az:250.2,apparent_alt:1.69,vis_prob:55,
  cloud_at:{low:18,mid:35,high:45},reason:'云量中等，薄云可透',
  sky:{sun_alt:-6.2,twilight:'民用暮光',moon_illum:86,moon_phase:'盈凸月'},
  feasibility:{score:45,verdict:'云层遮挡',dims:{cloud:11,transparency:74,precip:80,stability:84},
    reasons:['大气通透'],risks:['低云交点云量 82%']}};
const m=__E.mapAlignToScene(w,{cityId:'zhuhai'});
console.log('M:'+JSON.stringify(m));
const s=__E.mapAlignToScene({type:'悬日',body:'sun',period:'sunset',best_alt:2.4,
  apparent_alt:2.49,vis_prob:72,cloud_at:{low:18,mid:35,high:45},
  sky:{sun_alt:9.6},feasibility:{score:45,dims:{precip:80}},date:'2026-09-10'},{cityId:'zhuhai'});
console.log('S:'+JSON.stringify(s));
console.log('STABLE:'+(__E.mapAlignToScene(w,{cityId:'zhuhai'}).seed===m.seed?'OK':'BAD'));
// 悬月云染色 ×.3 落在 litK()（渲染期统一乘，映射本身不缩放 lit）
__E.set({lit:55,moon:'full'});
console.log('LITK:'+__E.litK().toFixed(3));
__E.set({moon:'none'});
console.log('LITK0:'+__E.litK().toFixed(3));
""")
    m = json.loads(re.search(r'^M:(.+)$', out, re.M).group(1))
    s = json.loads(re.search(r'^S:(.+)$', out, re.M).group(1))
    assert m['moon'] == 'full', f"悬月未出满月盘: {m}"
    assert m['alt'] == -6.2, f"天空高度角未取窗口 sky.sun_alt: {m}"
    assert m['genus'] == 'ci', f"云属映射错（高云 45 → ci）: {m}"
    assert m['cover'] == 45, f"云量应为视线交点最厚层(45): {m}"
    assert m['lit'] == 55, f"lit 应取窗口 vis_prob（×.3 由 litK 负责）: {m}"
    assert m['rain'] == 'none', f"precip dim 80 → 无降水: {m}"
    assert isinstance(m['seed'], int), f"seed 非整数: {m}"
    assert s['moon'] == 'none', f"悬日不应出月盘: {s}"
    assert s['alt'] == 4, f"天空高度角夹取到 4（白天）: {s}"
    assert s['lit'] == 72, f"悬日 lit=vis_prob: {s}"
    assert 'STABLE:OK' in out, f'窗口参数不确定: {out[:400]}'
    assert 'LITK:0.165' in out, f'悬月云染色未 ×.3: {out[:400]}'
    assert 'LITK0:0.550' in out, f'悬日染色被误缩放: {out[:400]}'


def test_posters_use_scene_still():
    """两个海报顶部帧都改由 SceneEngine.renderStill 离屏渲染；卡片与地图不溢出 1920。"""
    assert SHARE.count('renderStill') >= 2, 'share.js 两张海报未都接 renderStill'
    assert 'SceneEngine' in SHARE and 'params()' in SHARE, '海报未复用当前场景参数'
    assert 'mapAlignToScene' in SHARE, '悬日月海报未按窗口推场景'
    for m in re.finditer(r'const (PCARD|PMAP|PFOOT)=\{([^}]*)\}', SHARE):
        body = dict(re.findall(r'(\w+)\s*:\s*(\d+)', m.group(2)))
        assert body, f'{m.group(1)} 常量解析失败'
        assert int(body.get('y', 0)) + int(body.get('h', 0)) <= 1920, m.group(1)
    assert 'roundRectPath' in SHARE, '卡片圆角裁剪缺失'


def test_mobile_polish():
    """附录B#5：帧 16:10 + 星点/灯火减半 + 雨丝 ×.6。"""
    # 2026-10-23：移动端帧高改由 clamp() 控制（aspect-ratio 会反推宽度导致横向溢出）
    assert 'clamp(260px' in CSS or 'aspect-ratio:16/10' in CSS, '移动端帧未改 16:10'
    assert "max-width:640px" in SE, '引擎缺移动端分支'
    assert 'isNarrow()?45:90' in SE, '星点未减半（移动端 45）'
    assert 'Math.round(n*.6)' in SE, '雨丝数未 ×.6'
    assert re.search(r'function lampCount\(\)\{return isNarrow\(\)\?6:11;\}', SE), '灯火未减半(11→6)'
    lights = SE[SE.index('function groundMount'):SE.index('function groundMount') + 1400]
    assert 'lampCount()' in lights, '灯火未走 lampCount()'


def test_timecode_24fps():
    """附录B#4：帧号按 24fps 跑码（1s 采样恒 00 的问题修掉）。"""
    assert 'paintSceneTc' in APP, 'app.js 缺 24fps 时间码 ticker'
    assert 'setInterval(paintSceneTc' in APP, 'ticker 未挂'
    m = re.search(r'setInterval\(paintSceneTc,\s*(\d+)\)', APP)
    assert m and int(m.group(1)) <= 100, f'时间码刷新周期过长（帧号仍恒 00）: {m}'
    assert re.search(r"Math\.floor\(d\.getMilliseconds\(\)\s*/\s*1000\s*\*\s*24\)", APP), \
        '帧号未按 24fps 推（应为 Math.floor(d.getMilliseconds()/1000*24)）'


def test_perf_hooks():
    """性能红线可观测：stats() 计数 + dt 封顶 50ms + regen debounce 140ms。"""
    assert 'stats:function' in SE or 'stats:stats' in SE, '引擎未导出 stats()'
    assert 'still' in SE and 'genField' in SE, 'stats 缺 still/genField 计数'
    assert 'Math.min(50' in SE, 'dt 未封顶 50ms'
    assert 'setTimeout(regenClouds,140)' in SE, 'regen debounce 非 140ms'


def test_renderstill_returns_null_on_draw_error():
    """绘制期抛异常 → renderStill 必须返回 null（不能把半成品画布当成功交出去）。

    交付说明 §2.1 承诺「失败返回 null，调用方退回原生渐变」；share.js 的
    `if (!still) return false` 也只认 null。但原先 catch 是空的、函数末尾无条件
    `return cv`，drawSky/drawClouds 中途抛异常时会把「画了一半」的画布返回给海报，
    海报于是印上半成品帧（探针实测：把 clearRect 改成 throw，返回 canvas 320x160 而非 null）。
    """
    out = _node(r"""
__E.__setCanvasFactory(mkcanvas);
__E.set({genus:'ci',cover:45,lit:72,alt:-6.2,rain:'none',seed:11,moon:'full'});
const live=JSON.stringify(__E.params());
function mkThrowCtx(){
  const base={
    createLinearGradient:()=>({addColorStop:()=>{}}),
    createRadialGradient:()=>({addColorStop:()=>{}}),
    createImageData:(w,h)=>({data:new Uint8ClampedArray(w*h*4),width:w,height:h}),
    putImageData:()=>{}, measureText:()=>({width:10}),
    fillRect:()=>{throw new Error('draw-boom');},   // 绘制期炸（sky/云层都要 fillRect）
    clearRect:()=>{}, arc:()=>{}, drawImage:()=>{}
  };
  return new Proxy(base,{get(t,k){return (k in t)?t[k]:()=>{};},set(){return true;}});
}
const cvT={width:320,height:160,getContext:()=>mkThrowCtx()};
let ret=null,threw=false;
try{ ret=__E.renderStill({w:320,h:160,canvas:cvT}); }catch(e){ threw=true; }
console.log('NULLRET:'+(ret===null?'OK':'BAD:'+(ret&&ret.width)));
console.log('NOTHROW:'+(threw?'BAD':'OK'));
console.log('STATE3:'+(JSON.stringify(__E.params())===live?'KEPT':'DIRTY'));
""")
    for key in ('NULLRET:OK', 'NOTHROW:OK', 'STATE3:KEPT'):
        assert key in out, f'{key} 未满足: {out[:600]}'


def test_feedback_guard():
    """回归④ 实况回填：没选实况就点「提交实况」时不得抛未捕获 TypeError。

    app.js 的 submitFeedback() 原先直接 `document.querySelector('#fbSeg button.active').dataset.v`：
    默认没有任何按钮带 .active（app.js:471-476 只在用户点击后才加），于是裸点即
    `TypeError: Cannot read properties of null (reading 'dataset')` —— 用户看不到任何提示，
    控制台报错（M7 三档回归脚本 1366 档实测命中）。
    """
    src = APP[APP.index('async function submitFeedback'):]
    body = src[:src.index('\n}\n')]
    assert 'dataset.v' in body, '未按 active 选项取 outcome'
    assert re.search(r"querySelector\('#fbSeg button\.active'\)", body), \
        'outcome 未取自 #fbSeg button.active'
    head = body.split('dataset.v')[0]
    assert re.search(r"if\s*\(\s*!\s*active\s*\)", head), \
        '未选实况时直接读 .dataset → 未捕获 TypeError'
    assert 'fbText' in head, '未选实况时没给用户任何提示'


def test_poster_map_snapshot_guard():
    """海报地图快照：ECharts 在隐藏容器里会被 resize 成 0×0，取图前必须先量出尺寸。

    实测（tests/visual/m7_shots.py，1366 档）：查询后停「概览」tab 直接点「生成分享图」，
    `#burnMap` 容器 rect=0×0、`mapChart.getWidth()==0`，此时
    `mapChart.getDataURL()` 抛 `Failed to execute 'drawImage' on
    'CanvasRenderingContext2D': The image argument is a canvas element with a width or
    height of 0.` —— 海报地图区只剩底色（std 4.3/255，等于空白）。
    修法：取图前若图表尺寸 < 8px，先 `mapChart.resize({width, height})` 给出明确尺寸。
    """
    before = SHARE[:SHARE.index('mapChart.getDataURL')]
    tail = before[-400:]
    assert 'getWidth()' in tail and 'resize(' in tail, \
        '取图前没有量尺寸/兜底 resize → 隐藏容器下地图快照为空'
    assert re.search(r'resize\(\{\s*width', tail), '兜底 resize 未给出明确尺寸'


def test_mobile_star_count_observable():
    """移动端星点减半（附录B#5）必须运行时可观测：与既有 rainDrops() 同款只读探针。

    只是静态断言 `isNarrow()?45:90` 无法证明运行时真的减了 —— 三档截图脚本要拿它做
    「390 = 45 / 1366 = 90」的运行时验收，故引擎导出 starCount()。
    """
    assert 'starCount' in SE, '引擎未导出 starCount()（星点数量不可运行时验收）'
    assert re.search(r"starCount\s*:\s*function", SE) or 'starCount:function' in SE, \
        'starCount 未按既有 rainDrops() 同款只读探针导出'
    assert re.search(r'starN\s*=\s*stars\.length', SE), \
        '星点数量没有落到模块级变量，导出探针拿不到真实数量'
    assert re.search(r'isNarrow\(\)\s*\?\s*45\s*:\s*90', SE), '星点未按窗口宽度减半'


def test_risk_card_hidden_when_empty():
    """抛光：`r.risks` 为空时不得摆一张空壳「翻车风险提示」卡。

    实测（tests/visual/m7_shots.py，1920 档 full.png）：mock 的 risks=[]（app.js:3179
    也承认 risks 可空），但 app.js:3406-3409 无条件 `riskCard.style.display='block'` →
    页面底部挂一张只有标题、没有条目的空卡。与既有约定一致（renderBrief 在
    probability==null 时 `card.style.display='none'`）。
    """
    seg = APP[APP.index("// 风险"):]
    body = seg[:seg.index('// M4：预报→场景映射')]
    assert 'riskCard' in body and 'riskList' in body, '未找到风险卡渲染段'
    assert re.search(r"riskCard'\)\.style\.display\s*=", body), \
        '风险卡未按条目数控制显隐'
    assert re.search(r"risks\.length\s*\?", body) or re.search(r"length\s*\?\s*'block'", body), \
        '风险卡未按条目数控制显隐（仍无条件 block）'

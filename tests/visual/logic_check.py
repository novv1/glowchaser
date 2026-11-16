# -*- coding: utf-8 -*-
"""前端回归检查（Playwright + Edge，离线 mock 数据）。

覆盖：
- 纯逻辑：跨午夜时间插值、方位/高度插值、遮蔽角插值、月相方向
- 两级模式：霞光 / 悬日月；悬日 / 悬月；日出 / 日落 / 月出 / 月落
- 联动：表格 / 预览 / 日历 / 热力图事件同步，悬日月日历行数
- 霞光模式：高度角曲线事件标记
- v2.36 入口不自动查询：进入 0 次 /api/report，改参数不发请求，点「查询」才发
用法：python tests/visual/logic_check.py
"""
import json
import os
import subprocess
import sys
import time

import requests
from playwright.sync_api import sync_playwright

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

CHECKS = """
() => {
  const out = [];
  const eq = (name, got, want) => out.push(
    [name, JSON.stringify(got) === JSON.stringify(want), got, want]);
  eq('minToHM(1450)', minToHM(1450), '00:10');
  eq('minToHM(-10)', minToHM(-10), '23:50');
  eq('minToHM(0)', minToHM(0), '00:00');
  eq('h2m(17:52)', h2m('17:52'), 1072);
  eq('interpTrkMin linear', interpTrkMin(['00:00','00:30'], [0,10], 15), 5);
  eq('interpTrkMin cross-midnight',
     interpTrkMin(['23:30','00:00'], [1,2], 1440), 2);
  eq('interpTrkMin clamp low',
     interpTrkMin(['06:00','06:30'], [5,6], 300), 5);
  eq('skyTrackValue cross-midnight',
     skyTrackValue({time:['23:30','00:00'], sun_alt:[1,2]}, 'sun_alt', 1440), 2);
  // v2.26 受光（地影）判据：太阳还在地平线上时线下无遮蔽；
  // 太阳落到 -3° 时近处低云已入影、远端高云仍受光（地影线随距离下沉）。
  const csFix = {x_dist: [0, 50, 320, 580]};
  eq('csShadowHeights 白天为 0',
     csShadowHeights(csFix, 0.5), [0, 0, 0, 0]);
  eq('csShadowHeights 入影后随距离下沉',
     csShadowHeights(csFix, -3).map(v => v > 0),
     [true, true, true, false]);
  eq('csShadowHeights 单点 h=R(1/cos b -1)',
     Math.round(csShadowHeights({x_dist: [0]}, -3)[0] * 100) / 100, 8.74);
  eq('csSunAltAt 无 tracks 返回 null', csSunAltAt({offset_min: 0}), null);
  // 已过窗口让位：当天早晨查傍晚时，第一条窗口若已过不应作为结论
  eq('alignPickLive 跳过已过窗口',
     (alignPickLive([{is_past: true, t: 1}, {is_past: false, t: 2}]) || {}).t, 2);
  eq('alignPickLive 全已过时保留首条',
     (alignPickLive([{is_past: true, t: 1}]) || {}).t, 1);
  eq('alignPickLive 空列表', alignPickLive([]), null);
  eq('alignPickLive 无标记时取首条',
     (alignPickLive([{t: 9}]) || {}).t, 9);
  const mask = new Array(16).fill(0); mask[0] = 10;
  eq('skyHorizonAtMask mid', skyHorizonAtMask(mask, 11.25), 5);
  eq('horizonAltAtMask wrap',
     horizonAltAtMask((() => { const m = new Array(16).fill(0); m[0] = 10; return m; })(),
                      348.75), 5);
  eq('clamp01 high', clamp01(2), 1);
  eq('clamp01 low', clamp01(-2), 0);
  const moonAlpha = (illum, waxing) => {
    const cv = document.createElement('canvas'); cv.width = 200; cv.height = 200;
    const c = cv.getContext('2d');
    drawMoonPhase(c, 100, 100, 60, illum, waxing, 1);
    const d = c.getImageData(0, 0, 200, 200).data;
    const a = (x, y) => d[(y * 200 + x) * 4 + 3];
    return [a(48, 100), a(152, 100)];
  };
  const w86 = moonAlpha(86, false), x86 = moonAlpha(86, true);
  eq('waning86 left lit', w86[0] > 0, true);
  eq('waning86 right dark', w86[1], 0);
  eq('waxing86 left dark', x86[0], 0);
  eq('waxing86 right lit', x86[1] > 0, true);
  const full = moonAlpha(100, true);
  eq('full moon both lit', [full[0] > 0, full[1] > 0], [true, true]);
  // ---- 天体预览 v3：等比投影 / 视线云带几何 / 方位展开 ----
  eq('pvWrap180(-190)', pvWrap180(-190), 170);
  eq('pvWrap180(359)', pvWrap180(359), -1);
  eq('pvRefraction 0° 约 0.48°', Math.round(pvRefractionDeg(0) * 100) / 100, 0.48);
  eq('pvRefraction 地平线下为 0', pvRefractionDeg(-2), 0);
  // pvElevOfDist 是 sightline.sight_intersection_km 的逆：交点距离代回来应还原
  // 天体视高度角 —— 云带落位就是靠这一条与后端视线几何对齐
  const dInv = pvSightIntersectKm(1536, -0.21, 1201);
  eq('pvSightIntersectKm 1536m/-0.21° ≈ 92.7km',
     Math.round(dInv * 10) / 10, 92.7);
  eq('pvElevOfDist 逆算回 -0.21°',
     Math.abs(pvElevOfDist(1536, dInv, 1201) + 0.21) < 0.005, true);
  eq('pvInterpAz 跨北不跳（返回展开值，取模后 0.4°）',
     Math.round(pvWrap180(pvInterpAz(['23:30', '00:00'], [359.5, 0.4], 1440))
                * 10) / 10, 0.4);
  eq('pvInterpAz 无数据', pvInterpAz(null, null, 0), null);
  eq('pvLitAngle 同方位低日 → 亮面朝下',
     Math.abs(pvLitAngleDeg(30, 90, 0, 90) + 90) < 3, true);
  eq('pvLitAngle 日在其右 → 亮面朝右',
     Math.abs(pvLitAngleDeg(30, 90, 30, 120)) < 25, true);
  return out;
}
"""

PV_UI = """
() => {
  const out = [];
  const eq = (name, got, want) => out.push(
    [name, JSON.stringify(got) === JSON.stringify(want), got, want]);
  const r = alignState.data;
  const w = r.alignment.find(x => x.period === 'sunset') || r.alignment[0];
  // 渲染到真画布（卡片此时可见），拿渲染摘要做几何断言
  renderCelestialPreview(w, r, 0);
  const p = _pvLast;
  eq('预览 px/° 横竖一致（等比投影）',
     Math.abs(p.pxPerDegX - p.pxPerDegY) < 1e-6, true);
  eq('取景框长宽比 = 视场比',
     Math.abs(p.frame[2] / p.frame[3] - p.fovH / p.fovV) < 1e-3, true);
  eq('取景框不超出画布（3:2 画布 1200×800）',
     p.frame[2] <= 1200 + 0.5 && p.frame[3] <= 800 + 0.5, true);
  eq('偏移 0 时天体 = 窗口表值',
     [Math.round(p.alt * 100) / 100, Math.round(p.az * 100) / 100],
     [w.best_alt, w.best_az]);
  eq('可见度取后端 vis_prob', p.vis, Math.round(w.vis_prob));
  eq('云带都落在视线交点仰角上',
     p.clouds.every(c => Math.abs(c.dKm) > 0), true);
  eq('云带厚度为正', p.clouds.every(c => c.thick > 0.05), true);
  // 画布 = 相机画面：视野固定，拖滑块只让天体动（这是"天/云/取景框一起动"的回归）
  const px0 = p.px, focal0 = p.focalMm, anchor0 = p.anchorX;
  eq('画布即取景框（占满整幅）',
     [p.frame[0], p.frame[1], p.frame[2], p.frame[3]], [0, 0, 1200, 800]);
  eq('画幅 3:2（全画幅等价，焦距换算才成立）',
     Math.abs(p.fovV / p.fovH - 2 / 3) < 1e-6, true);
  eq('焦距与视场角互算一致',
     Math.abs(pvFocalFromFov(p.fovH) - p.focalMm) <= 1, true);
  eq('日面占画面宽 = 0.533/视场', Math.abs(p.bodyPct - 0.533 / p.fovH * 100) < .05,
     true);
  renderCelestialPreview(w, r, 20);
  const p2 = _pvLast;
  eq('滑块不改变视野（px/°）', p2.px, px0);
  eq('滑块不改变焦距/视场', [p2.focalMm, p2.fovH], [focal0, p.fovH]);
  eq('滑块不改变云图锚点（云不跟着天体跑）', p2.anchorX, anchor0);
  eq('滑块 +20min 天体位置变化', Math.abs(p2.alt - w.best_alt) > 0.01, true);
  eq('滑块 +20min 位移量级合理（<10°）',
     Math.abs(p2.alt - w.best_alt) < 10, true);
  eq('滑块 +20min 有出框时刻或仍在框内',
     p2.exitMin == null || (p2.exitMin >= 1 && p2.exitMin <= 30), true);
  renderCelestialPreview(w, r, 0);
  // 焦距控件：改焦距 = 改视场角（并按新视场重算），预设按钮存在
  eq('焦距预设按钮', document.querySelectorAll('#focalPresets button').length > 0,
     true);
  eq('fovToFocal(2.455°) ≈ 840mm', Math.abs(fovToFocal(2.455158) - 838) <= 2, true);
  eq('focalToFov(838mm) ≈ 2.455°',
     Math.abs(focalToFov(838) - 2.455158) < 0.01, true);
  // 只验算与控件同步（真的派发 change 会触发重算请求，离线 mock 下会清空数据）
  const aovSaved = alignState.aov;
  alignState.aov = focalToFov(135);
  syncFocalUI();
  eq('改视场角后焦距控件跟着走',
     Math.abs(+document.getElementById('focalInput').value - 135) <= 1, true);
  eq('焦距胶囊高亮当前值',
     document.querySelectorAll('#focalPresets button.on').length <= 1, true);
  alignState.aov = aovSaved;
  syncAlignControls();
  renderCelestialPreview(w, r, 0);
  // 构图：地面落在画面下半部（目标"下方 1/3"），且日/月盘完整、不被顶部 HUD 压住
  const pvH = 800, pvW = 1200;
  eq('地面线在画面下半部', _pvLast.groundY >= pvH * 0.5, true);
  const bodyY = pvH / 2 - (_pvLast.alt - _pvLast.altC) * _pvLast.px;
  const bodyRpx = 0.2665 * _pvLast.px;
  eq('日面完整且不被 HUD 压住', bodyY - bodyRpx >= 82, true);
  eq('日面不压到底部刻度', bodyY + bodyRpx <= pvH - 30, true);
  // 地平线/山脊之下的日面：仍要显示（低透明度底片），并如实说明
  const wLow = Object.assign({}, w, {best_alt: -1.5});
  renderCelestialPreview(wLow, r, 0);
  eq('地平线下的天体仍显示（底片）', _pvLast.occulted, true);
  eq('底片时状态行说明被挡/地平线下',
     /挡住|地平线下/.test(_pvLast.note), true);
  renderCelestialPreview(w, r, 0);
  return out;
}
"""

ALIGN_UI = """
() => {
  const out = [];
  const eq = (name, got, want) => out.push(
    [name, JSON.stringify(got) === JSON.stringify(want), got, want]);
  const evTxt = () => [...document.querySelectorAll('#alignEventSeg button')]
    .map(b => b.textContent.trim());
  eq('mode=align', appMode, 'align');
  eq('default body', alignState.body, 'sun');
  eq('sun events', evTxt(), ['\\u2600 \\u65e5\\u51fa', '\\u2600 \\u65e5\\u843d']);
  eq('sun table has rows',
     document.querySelectorAll('#alignTbl tr').length - 1 >= 1, true);
  document.querySelector('#alignEventSeg button[data-v="set"]').click();
  eq('period=set', alignState.period, 'set');
  eq('preview period', _pvCtx.w.period_name, '\\u65e5\\u843d');
  eq('preview az', _pvCtx.w.best_az, 269.7);
  document.querySelector('#subSeg button[data-v="moon"]').click();
  eq('body=moon', alignState.body, 'moon');
  eq('moon events', evTxt(), ['\\ud83c\\udf19 \\u6708\\u51fa',
                              '\\ud83c\\udf19 \\u6708\\u843d']);
  eq('moon preview body', _pvCtx.w.body, 'moon');
  document.querySelector('#subSeg button[data-v="sun"]').click();
  document.querySelector('#alignEventSeg button[data-v="rise"]').click();
  eq('back to sun rise', _pvCtx.w.period_name, '\\u65e5\\u51fa');
  // 专业版卡片
  eq('hero visible', document.getElementById('alignHero').style.display, 'flex');
  eq('hero score', document.getElementById('alignScore').textContent, '45');
  eq('dim cells', document.querySelectorAll('#alignDims .dim-cell').length, 4);
  eq('model rows',
     document.querySelectorAll('#alignModelTbl tr').length - 1, 4);
  eq('week cells',
     document.querySelectorAll('#alignWeekGrid .week-cell').length, 7);
  eq('risk list', document.querySelectorAll('#alignRiskList li').length >= 1, true);
  eq('detail card', document.getElementById('alignDetailCard').style.display, 'block');
  eq('nowcast hidden (far event)',
     document.getElementById('alignNowcastCard').style.display, 'none');
  eq('align share button', !!document.getElementById('btnAlignShare'), true);
  eq('day weather shown',
     document.getElementById('alignDayWx').textContent.indexOf('可见概率') >= 0,
     true);
  eq('setup card visible',
     document.getElementById('alignSetupCard').style.display, 'block');
  eq('align map init',
     !!(window._alignMap && window._alignMap.getCenter()), true);
  eq('align map is leaflet', typeof L !== 'undefined'
     && !!document.getElementById('alignMap')._leaflet_id, true);
  eq('align map has tile layer', !!window._alignTile, true);
  eq('align map tile source select',
     !!document.getElementById('alignMapSrc'), true);
  const bNE = bearingBetween(22.271, 113.5767, 22.28, 113.59);
  eq('bearing NE 40-60', bNE > 40 && bNE < 60, true);
  eq('bearing due north', Math.round(bearingBetween(22.27, 113.58, 22.28, 113.58)), 0);
  // 视线遮挡诊断卡
  eq('sight card visible',
     document.getElementById('alignSightCard').style.display, 'block');
  eq('sight band rows',
     document.querySelectorAll('#alignSightTbl tr').length - 1, 3);
  eq('sight level rows',
     document.querySelectorAll('#alignSightLevelTbl tr').length - 1, 4);
  const sNote = document.getElementById('alignSightNote').textContent;
  eq('sight note has hit band', sNote.indexOf('低云') >= 0 && sNote.indexOf('高云') >= 0, true);
  eq('sight note has transmission', sNote.indexOf('综合透射率') >= 0, true);
  eq('sight note cross-model', sNote.indexOf('模式复核') >= 0
     && sNote.indexOf('GFS 68%') >= 0, true);
  eq('sight note spread warning', sNote.indexOf('分歧大') >= 0, true);
  const sTbl = document.getElementById('alignSightTbl').textContent;
  eq('sight table distance', sTbl.indexOf('114.5 km') >= 0, true);
  eq('sight table obstruction word', sTbl.indexOf('会挡住天体') >= 0, true);
  eq('sight table thin-cloud word', sTbl.indexOf('薄云削弱对比度') >= 0, true);
  eq('sight table no-cloud band', sTbl.indexOf('未检出云层') >= 0, true);
  const heroReason = document.getElementById('alignReason').textContent;
  eq('hero reason basis label', heroReason.indexOf('视线交点云况') >= 0, true);
  const lvlTbl = document.getElementById('alignSightLevelTbl').textContent;
  eq('sight level hPa shown', lvlTbl.indexOf('925 hPa') >= 0, true);
  eq('sight level path source', lvlTbl.indexOf('远端 100km') >= 0, true);
  // 机位高度 / 目标高度 精细化
  eq('sight title has camera height',
     document.getElementById('alignSightTitle').textContent
       .indexOf('机位 1201 m') >= 0, true);
  eq('sight title has dip',
     document.getElementById('alignSightTitle').textContent
       .indexOf('下沉 1.112°') >= 0, true);
  const geomTxt = document.getElementById('alignGeomGrid').textContent;
  eq('geom grid camera height', geomTxt.indexOf('1201 m') >= 0, true);
  eq('geom grid dip', geomTxt.indexOf('-1.112°') >= 0, true);
  eq('geom grid target height/distance',
     geomTxt.indexOf('900 m') >= 0 && geomTxt.indexOf('8.00 km') >= 0, true);
  eq('geom grid target elev source', geomTxt.indexOf('由高度反推') >= 0, true);
  eq('height summary line',
     document.getElementById('alignHeightTxt').textContent
       .indexOf('地平线下沉') >= 0, true);
  eq('elev input backfilled from target',
     document.getElementById('elevInput').value, '0.30');
  // 高度输入存在且触发查询
  eq('camera/target height inputs present',
     !!document.getElementById('camHeightInput')
     && !!document.getElementById('targetHeightInput'), true);
  eq('height clear button present',
     !!document.getElementById('btnAlignHeightClear'), true);
  // 高度差公式与后端同口径（机位正北 100 km，同高度 -> 曲率负角）
  eq('haversineKm 同点=0', haversineKm(22.0, 113.0, 22.0, 113.0) < 1e-9, true);
  eq('haversineKm 1度纬度约111km',
     Math.abs(haversineKm(22.0, 113.0, 23.0, 113.0) - 111.19) < 0.5, true);
  // 版本自检：mock 的版本号取自前端 UI_VERSION（mock_report 会自动对齐），
  // 因此应判定为不陈旧且不显示横幅；写死旧版本号会让这条检查变得脆弱。
  eq('versions match -> not stale',
     serverStale(alignState.data), false);
  eq('version missing -> stale',
     serverStale({meta: {date: '2026-09-10'}}), true);
  eq('version older -> stale',
     serverStale({meta: {version: '2.19'}}), true);
  eq('stale banner hidden when fresh',
     document.getElementById('staleBanner').style.display, 'none');
  // 地图（Leaflet）：机位/目标/采样点/云层交点 + 连线 + 视线射线
  const layers = window._alignMapLayers || [];
  const circles = layers.filter(l => l instanceof L.CircleMarker);
  const lines = layers.filter(l => l instanceof L.Polyline
                                   && !(l instanceof L.CircleMarker));
  eq('map markers+points present', circles.length >= 3, true);
  eq('map polylines (机位→目标 + 视线)', lines.length, 2);
  const tips = circles.map(c => (c.getTooltip() && c.getTooltip().getContent)
    ? String(c.getTooltip().getContent()) : '');
  eq('map has camera marker', tips.some(t => t.indexOf('机位') >= 0), true);
  eq('map has target marker', tips.some(t => t.indexOf('目标') >= 0), true);
  eq('map has sight samples', tips.some(t => t.indexOf('光路采样') >= 0), true);
  eq('map has cloud hit markers',
     tips.filter(t => t.indexOf('云层交点') >= 0).length, 2);
  eq('map cloud marker km format',
     tips.some(t => t.indexOf('0.8km 云层交点') >= 0), true);
  // destPoint 与后端球面公式一致（机位正北 100km）
  const dp = destPoint(22.0, 113.0, 0, 100);
  eq('destPoint north lat delta ~0.9', Math.abs(dp.lat - 22.8994) < 0.01, true);
  eq('destPoint north lon same', Math.abs(dp.lon - 113.0) < 0.001, true);
  const dpE = destPoint(22.0, 113.0, 90, 100);
  eq('destPoint east lon increase', dpE.lon > 113.0, true);
  return out;
}
"""

GLOW_UI = """
() => {
  const out = [];
  const eq = (name, got, want) => out.push(
    [name, JSON.stringify(got) === JSON.stringify(want), got, want]);
  eq('mode=glow', appMode, 'glow');
  eq('glow view visible',
     document.getElementById('glowView').style.display, 'block');
  eq('align view hidden',
     document.getElementById('alignView').style.display, 'none');
  // ---- v2.38 两周窗口：周概览 14 格、标题写明 14 天 ----
  eq('glow week has 14 cells',
     document.querySelectorAll('#weekGrid .week-cell').length, 14);
  eq('glow week title says 14 days',
     document.getElementById('weekCard').querySelector('h3')
       .textContent.indexOf('14') >= 0, true);
  const o = window._altChart.getOption();
  const s = (o.series || []).find(x => x.name === '\\u4e8b\\u4ef6');
  eq('chart event marks >= 7', s ? s.data.length >= 7 : false, true);
  // ---- v2.33 今日简报 / 探空 / 0°C 层 ----
  eq('brief card visible',
     document.getElementById('briefCard').style.display, 'block');
  // 2026-10-23：判定/概率/窗口移到首屏答案层，简报只留「依据 + 风险」
  eq('brief text has basis',
     document.getElementById('briefText').textContent
       .indexOf('\\u4f9d\\u636e') >= 0, true);
  eq('hero keeps verdict',
     document.getElementById('heroVerdict').textContent
       .indexOf('\\u4e2d\\u70e7') >= 0, true);
  eq('hero keeps window',
     document.getElementById('decideWindow').textContent
       .indexOf('17:50') >= 0, true);
  eq('sound card visible',
     document.getElementById('soundCard').style.display, 'block');
  const so = window._soundChart ? window._soundChart.getOption() : null;
  eq('sound chart 2 series', so ? (so.series || []).length >= 2 : false,
     true);
  eq('sound T 6 pts', so ? so.series[0].data.length : 0, 6);
  const gridTxt = document.getElementById('wxGrid').textContent;
  eq('wx grid 0C level', gridTxt.indexOf('0\\u00b0C') >= 0
     && gridTxt.indexOf('4.25 km') >= 0, true);
  const cso = typeof csChart !== 'undefined' && csChart
    ? csChart.getOption() : null;
  const fz = cso ? (cso.series || []).find(
    x => x.name === '0\\u00b0C \\u5c42') : null;
  eq('cs freezing series has data', fz ? fz.data.length >= 2 : false, true);
  // ---- v2.33.1 缓存回退不再劫持日期输入框 ----
  const freshDate = document.getElementById('dateSel').value;
  curReport.__from_cache = true;
  curReport.meta.date = '2026-09-06';
  render();
  eq('cache fallback keeps date input',
     document.getElementById('dateSel').value, freshDate);
  curReport.meta.date = freshDate;
  delete curReport.__from_cache;
  render();
  eq('fresh report re-syncs date input',
     document.getElementById('dateSel').value, freshDate);
  // ---- v2.34 额度降级标注 ----
  curReport.meta.degraded = '\\u4e0a\\u6e38\\u989d\\u5ea6\\u7528\\u5c3d';
  render();
  eq('degraded note in hero cred',
     document.getElementById('heroCred').textContent.indexOf('\\u26a0') >= 0,
     true);
  delete curReport.meta.degraded;
  render();
  return out;
}
"""


THEME_UI = """
() => {
  const out = [];
  const eq = (name, got, want) => out.push(
    [name, JSON.stringify(got) === JSON.stringify(want), got, want]);
  const root = document.documentElement;
  const accent = () =>
    getComputedStyle(root).getPropertyValue('--accent').trim().toUpperCase();

  // 单一主题（2026-10-23）：只剩一套配色，不再有皮肤/明暗/朝晚霞档三组属性
  eq('唯一主题主色', accent(), '#E8B06A');
  eq('没有 data-skin', root.getAttribute('data-skin'), null);
  eq('没有 data-theme', root.getAttribute('data-theme'), null);
  eq('没有 data-sky', root.getAttribute('data-sky'), null);

  // 主题系统整体下线：面板/按钮/接口都不该存在
  eq('没有主题面板', !!document.getElementById('themePanel'), false);
  eq('没有主题按钮', !!document.getElementById('themeBtn'), false);
  eq('皮肤接口已移除', typeof window.__setSkin, 'undefined');
  eq('深浅接口已移除', typeof window.__setThemeMode, 'undefined');

  // 令牌唯一：--accent 只有一个声明（重复声明会重新长回多主题）
  let accentDecls = 0;
  for (const sheet of document.styleSheets) {
    let rules = [];
    try { rules = sheet.cssRules || []; } catch (e) { continue; }
    for (const r of rules) {
      if (!r.style) continue;
      for (let i = 0; i < r.style.length; i++) {
        if (r.style[i] === '--accent') accentDecls++;
      }
    }
  }
  eq('--accent 只声明一次', accentDecls, 1);

  // 章节：四个锚点 + 三个可折叠层，默认收起
  const secs = ['tonight', 'evidence', 'deep', 'log'];
  eq('四个章节锚点', secs.filter(s => !!document.getElementById('sec-' + s)).length, 4);
  const tiers = Array.from(document.querySelectorAll('.tier'));
  eq('三个可折叠章节', tiers.length, 3);
  eq('默认全部收起', tiers.filter(t => t.querySelector('.tier-body').hidden).length, 3);

  // 展开/收起：按钮文案与 aria 同步
  const t0 = tiers[0];
  const btn = t0.querySelector('.tier-toggle');
  btn.click();
  eq('展开后 aria', btn.getAttribute('aria-expanded'), 'true');
  eq('展开后文案', btn.textContent, '收起');
  eq('展开后可见', t0.querySelector('.tier-body').hidden, false);
  btn.click();
  eq('收起后 aria', btn.getAttribute('aria-expanded'), 'false');
  eq('收起后隐藏', t0.querySelector('.tier-body').hidden, true);
  return out;
}
"""


# v2.37 D 档：无障碍与地名搜索降级（键盘可达 / ARIA / 注入防护）
A11Y_UI = """
() => {
  const out = [];
  const push = (n, ok, got, want) => out.push([n, ok, got, want]);

  const tabs = Array.from(document.querySelectorAll('#tabnav [role="tab"]'));
  const withSel = tabs.filter(t => t.hasAttribute('aria-selected'));
  push('tabs carry aria-selected', tabs.length >= 4 && withSel.length === tabs.length,
       withSel.length + '/' + tabs.length, 'all');

  // A单列：导航改锚点（href="#sec-x"），锚点必须真实存在
  const anchors = tabs.map(t => document.getElementById((t.getAttribute('href') || '').replace('#', '')));
  push('tabs point at an existing anchor',
       anchors.length >= 4 && anchors.every(a => !!a),
       anchors.map(a => (a ? a.id : null)), 'every tab -> existing anchor');

  const cards = Array.from(document.querySelectorAll('#report-flow .card, #report-flow .hero'));
  const named = cards.filter(c => (c.getAttribute('aria-label') || c.id || '').trim());
  push('report-flow cards are self-describing', cards.length >= 15 && named.length === cards.length,
       named.length + '/' + cards.length, 'every card has id or aria-label');

  const plays = ['skyPlay', 'nowcastPlay', 'sweepPlay', 'csPlay', 'pvPlay']
    .map(id => document.getElementById(id)).filter(Boolean);
  const noLabel = plays.filter(b => !(b.getAttribute('aria-label') || '').trim());
  push('play buttons carry aria-label', plays.length >= 4 && noLabel.length === 0,
       noLabel.map(b => b.id), 'every playback toggle labelled');
  return out;
}
"""

# 周概览格子：键盘可达（alignWeekGrid 用 mock 数据渲染出 7 格）
WEEK_A11Y_UI = """
() => {
  const out = [];
  const push = (n, ok, got, want) => out.push([n, ok, got, want]);
  const cells = Array.from(document.querySelectorAll('#alignWeekGrid .week-cell[data-date]'));
  const bad = cells.filter(c => c.getAttribute('tabindex') !== '0'
                                || c.getAttribute('role') !== 'button');
  push('week cells are focusable buttons', cells.length >= 7 && bad.length === 0,
       {n: cells.length, bad: bad.length}, '>=7 cells, all tabindex=0 + role=button');
  return out;
}
"""


def main():
    port = 8154
    env = dict(os.environ)
    env['PYTHONPATH'] = ROOT
    server = subprocess.Popen(
        [sys.executable, '-m', 'uvicorn', 'app.main:app',
         '--host', '127.0.0.1', '--port', str(port)],
        cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
    try:
        t0 = time.time()
        while time.time() - t0 < 30:
            try:
                if requests.get(f'http://127.0.0.1:{port}/api/cities',
                                timeout=2).status_code == 200:
                    break
            except Exception:
                pass
            time.sleep(0.4)
        import mock_report
        with sync_playwright() as p:
            browser = p.chromium.launch(channel='msedge', headless=True)
            page = browser.new_page()
            # 计数 + 阻断：既保证离线，又能用「发了几次请求」做行为断言
            net = {'report': 0, 'align': 0}

            def _block_report(r):
                net['report'] += 1
                r.abort()

            def _block_align(r):
                net['align'] += 1
                r.abort()

            page.route('**/api/report*', _block_report)
            page.route('**/api/align*', _block_align)

            # 地名搜索 mock：普通查询回一个带引号与标签的恶意名字（验证注入被堵），
            # 「zzqq-broken」回上游不可用，用来验证前端不再谎报「没有找到」。
            evil = '危" onmouseover="window.__xss=1 <img src=x onerror="window.__xss=2">'

            def _mock_geocode(r):
                if 'zzqq' not in r.request.url:
                    r.continue_()        # 其它查询走真接口（本地地名库）
                    return
                if 'zzqq-broken' in r.request.url:
                    body = {'results': [], 'source': 'unavailable',
                            'message': '地名搜索服务暂时不可用（Photon / Nominatim '
                                       '均未响应），可稍后重试，或直接在地图上点选位置。'}
                else:
                    body = {'results': [{'name': evil, 'lat': 22.31, 'lon': 113.59,
                                         'adm1': '广东', 'adm2': '珠海市',
                                         'display': '测试地名'}],
                            'source': 'photon'}
                r.fulfill(status=200, content_type='application/json',
                          body=json.dumps(body, ensure_ascii=False))

            page.route('**/api/geocode*', _mock_geocode)
            page.on('pageerror', lambda e: print('PAGEERROR:', e))
            page.on('console',
                    lambda m: print('CONSOLE-ERR:', m.text[:200])
                    if m.type == 'error' and 'ERR_FAILED' not in m.text
                    else None)
            page.goto(f'http://127.0.0.1:{port}/', wait_until='domcontentloaded')
            page.wait_for_function(
                'typeof renderSky === "function" '
                '&& typeof renderAlignPage === "function"', timeout=30000)
            page.wait_for_timeout(2000)
            results = page.evaluate(CHECKS)

            # ---- v2.36 入口不自动查询：省上游额度 ----
            # 口径：进入页面/深链/改参数都只改参数并提示；只有点「查询」按钮
            # 才发 /api/report。这是行为断言，不是静态检查 —— 计的是真请求数。
            results.append(['entry fires no report request', net['report'] == 0,
                            net['report'], 0])
            idle = page.evaluate("""() => {
              const c = document.getElementById('idleCard');
              const load = document.getElementById('loading');
              const b = document.getElementById('btnGo');
              return {idle: c ? getComputedStyle(c).display : 'missing',
                      loading: load ? getComputedStyle(load).display : 'missing',
                      idleTxt: c ? c.textContent.replace(/\\s+/g, ' ').trim() : '',
                      go: !!b};
            }""")
            results.append(['entry shows idle card, not skeleton',
                            idle['idle'] == 'block' and idle['loading'] == 'none'
                            and idle['go'] and len(idle['idleTxt']) > 6,
                            idle, 'idleCard block / loading none / 有文案'])
            results.append(['idle card tells the user to click 查询',
                            '查询' in idle['idleTxt'], idle['idleTxt'][:60],
                            '含「查询」'])
            page.click('#btnGo')
            page.wait_for_timeout(1200)
            results.append(['query button fires exactly one report request',
                            net['report'] == 1, net['report'], 1])
            results.append(['idle card hidden while loading',
                            page.evaluate('() => {'
                                          ' const c = document.getElementById('
                                          '"idleCard");'
                                          ' return !c || getComputedStyle(c)'
                                          '.display === "none"; }'),
                            True, True])
            # 改参数（城市）不再自动查询，只标「待查询」
            page.select_option('#citySel', index=1)
            page.wait_for_timeout(500)
            results.append(['changing city does not auto-query',
                            net['report'] == 1, net['report'], 1])
            hint = page.evaluate("""() => {
              const h = document.getElementById('queryHint');
              const b = document.getElementById('btnGo');
              return {hint: h ? getComputedStyle(h).display : 'missing',
                      txt: h ? h.textContent.replace(/\\s+/g, ' ').trim() : '',
                      pending: !!b && b.classList.contains('pending')};
            }""")
            results.append(['param change marks query pending',
                            hint['pending'] and hint['hint'] != 'none'
                            and hint['hint'] != 'missing' and hint['txt'] != '',
                            hint, 'pending class + 可见提示文案'])
            # 深链/后退（hashchange）同样不自动查询
            page.evaluate('() => { location.hash = '
                          '"#/glow/overview?city=zhuhai&date=2026-09-13'
                          '&type=sunrise"; }')
            page.wait_for_timeout(700)
            results.append(['deep link does not auto-query',
                            net['report'] == 1, net['report'], 1])
            dl_date = page.evaluate(
                '() => document.getElementById("dateSel").value')
            results.append(['deep link still applies params', dl_date,
                            dl_date, '2026-09-13'])
            page.click('#btnGo')
            page.wait_for_timeout(1200)
            results.append(['second query fires second request',
                            net['report'] == 2, net['report'], 2])
            results.append(['pending cleared after query',
                            page.evaluate('() => !document.getElementById('
                                          '"btnGo").classList.contains('
                                          '"pending")'), True, True])
            # 复位成 init 之后的状态（珠海 / 今天 / 晚霞 / 无 dirty 标记），
            # 后续检查都建立在「刚打开页面」这一点上
            page.evaluate("""() => {
              glowType = 'sunset';
              document.getElementById('citySel').value = '0';
              document.getElementById('dateSel').value = localToday();
              rebuildModeUI();
              syncDateUI();
              writeRoute(false);
            }""")
            page.wait_for_timeout(300)

            # 悬日月模式：注入 mock 数据（阻断真实网络，key 对齐缓存）
            page.click('#modeSeg button[data-v="align"]')
            page.wait_for_timeout(900)

            # 地图点选：第一次点设机位，第二次点设目标并算方位
            # 先滚到视野内：mouse.click 用视口坐标、不会自动滚动。入口不再
            # 自动取数后页面顶部多了空态卡/待查提示（原来那里是错误卡），
            # 地图整体下移，落在折叠线以下就会点空。
            page.evaluate("() => document.getElementById('alignMap')"
                          ".scrollIntoView({block: 'center'})")
            page.wait_for_timeout(200)
            box = page.locator('#alignMap').bounding_box()
            page.mouse.click(box['x'] + box['width'] * 0.38,
                             box['y'] + box['height'] * 0.5)
            page.wait_for_timeout(400)
            cam_ok = page.evaluate(
                '!!(alignState.cam && alignState.cam.lat != null)')
            box = page.locator('#alignMap').bounding_box()
            page.mouse.click(box['x'] + box['width'] * 0.62,
                             box['y'] + box['height'] * 0.5)
            page.wait_for_timeout(400)
            tgt = page.evaluate(
                '({t: !!alignState.target, az: alignState.az,'
                ' pts: (window._alignMapLayers||[]).filter(l =>'
                '   l instanceof L.CircleMarker).length,'
                ' lines: (window._alignMapLayers||[]).filter(l =>'
                '   l instanceof L.Polyline).length})')
            results.append(['map click set camera', cam_ok, cam_ok, True])
            results.append(['map click set target',
                            tgt['t'] and tgt['pts'] == 2 and tgt['lines'] == 1,
                            tgt, 'target+2pts+1line'])
            results.append(['map target azimuth 0-360',
                            0 <= tgt['az'] <= 360, tgt['az'], '0..360'])
            # 重置为珠海/270（后续 mock 注入与日历检查依赖）
            page.evaluate(
                '() => { alignState.cam = {lat: 22.271, lon: 113.5767,'
                ' name: "珠海"}; alignState.target = null;'
                ' alignState.az = 270; syncAlignControls(); }')
            page.wait_for_timeout(200)

            align = mock_report.build_align()
            # 注入的状态需与 fixture 自洽：机位/目标高度齐备、目标在 8km 外，
            # 这样仰角为「由高度反推」（elevAuto），key 口径才与页面一致。
            page.evaluate("""(d) => {
              const c = alignState.cam || curCity();
              document.getElementById("camHeightInput").value = 1201;
              document.getElementById("targetHeightInput").value = 900;
              alignState.camHeight = 1201;
              alignState.targetHeight = 900;
              alignState.target = destPoint(c.lat, c.lon, 270, 8.0);
              alignState.elevManual = false;
              alignState.data = d;
              alignState.key = alignRequestKey(
                c, document.getElementById("dateSel").value);
              renderAlignPage();
            }""", align)
            page.wait_for_timeout(600)
            results += page.evaluate(ALIGN_UI)
            results += page.evaluate(PV_UI)
            # 无对准窗口时显示方向天气卡
            page.evaluate(
                '() => { window.__savedAlign = alignState.data.alignment;'
                ' alignState.data.alignment = []; renderAlignPage(); }')
            page.wait_for_timeout(500)
            dirw = page.evaluate(
                '({vis: document.getElementById("alignDirCard").style.display,'
                ' score: document.getElementById("alignDirScore").textContent,'
                ' facts: document.getElementById("alignDirFacts").textContent})')
            results.append(['direction weather card',
                            dirw['vis'] == 'block' and dirw['score'] == '51'
                            and '可见概率' in dirw['facts'],
                            dirw, 'block/51/可见概率'])
            # 方向天气卡的可视化：天空实景预览 / 视线剖面 / 当日轨迹 / 模式点图
            viz = page.evaluate("""() => {
              const cv = document.getElementById('alignDirSky');
              const ctx = cv.getContext('2d');
              const d = ctx.getImageData(0, 0, cv.width, cv.height).data;
              let painted = 0;
              for (let i = 3; i < d.length; i += 4000) if (d[i] > 0) painted++;
              const sec = (window._adrSection || {}).getOption
                ? window._adrSection.getOption() : null;
              const trk = (window._adrTrack || {}).getOption
                ? window._adrTrack.getOption() : null;
              const mdl = (window._adrModel || {}).getOption
                ? window._adrModel.getOption() : null;
              return {
                skyShown: cv.style.display, skyPainted: painted,
                secSeries: sec ? sec.series.length : 0,
                secBlocks: !!(sec && sec.series[0] && sec.series[0].markArea),
                secHits: sec ? (sec.series.filter(s => s.type === 'scatter')
                                 .map(s => (s.data || []).length)[0] || 0) : 0,
                secHint: document.getElementById('alignDirSectionHint')
                           .textContent.slice(0, 240),
                trkSeries: trk ? trk.series.length : 0,
                trkMarks: trk ? (((trk.series[0] || {}).markPoint || {}).data
                                || []).length : 0,
                trkNote: document.getElementById('alignDirTrackNote').textContent,
                mdlPts: mdl ? ((mdl.series[0] || {}).data || []).length : 0,
                mdlMark: !!(mdl && mdl.series[0] && mdl.series[0].markLine),
                dimBars: Array.from(
                  document.querySelectorAll('#alignDirDims .dim-bar > div'))
                  .map(e => [e.style.width, e.style.backgroundColor]),
              };
            }""")
            results.append(['dir sky preview painted',
                            viz['skyShown'] == 'block' and viz['skyPainted'] > 5,
                            viz['skyPainted'], '>5'])
            results.append(['dir section chart', viz['secSeries'] >= 3
                            and viz['secHits'] >= 1,
                            [viz['secSeries'], viz['secHits']], '>=3 series/>=1 hit'])
            results.append(['dir section occlusion band',
                            viz['secBlocks'], viz['secBlocks'], True])
            daywx_txt = page.evaluate(
                '() => document.getElementById("alignDayWx").textContent')
            results.append(['no misleading range hint for today',
                            '可能超出预报范围' not in daywx_txt,
                            daywx_txt[:70], '不含该误导文案'])
            results.append(['dir facts show 逐层 basis',
                            '逐层' in page.evaluate(
                              '() => document.getElementById("alignDirFacts")'
                              '.textContent'), True, True])
            results.append(['dir sky hint says outside frame',
                            '在框外' in page.evaluate(
                              '() => document.getElementById("alignDirSkyHint")'
                              '.textContent'),
                            True, True])
            results.append(['dir section hint names blocker',
                            '挡得最重' in viz['secHint'], viz['secHint'],
                            '含挡得最重'])
            results.append(['dir track chart + band',
                            viz['trkSeries'] >= 2 and viz['trkMarks'] >= 1,
                            [viz['trkSeries'], viz['trkMarks']], '>=2 />=1'])
            results.append(['dir model dot chart',
                            viz['mdlPts'] >= 3 and viz['mdlMark'],
                            [viz['mdlPts'], viz['mdlMark']], '>=3 pts/markLine'])
            # 维度条配色语义：高分应偏绿（通透度 74 -> 绿），低分偏红（稳定性 24 -> 红）
            dimchk = page.evaluate("""() => {
              const bars = Array.from(
                document.querySelectorAll('#alignDirDims .dim-bar > div'));
              const rgb = s => (s.match(/\\d+/g) || []).map(Number);
              const isGreen = s => { const c = rgb(s);
                return c[1] > c[0] && c[1] > c[2]; };
              const isRed = s => { const c = rgb(s);
                return c[0] > c[1] && c[0] > c[2]; };
              return {
                n: bars.length,
                green: bars.filter(e => isGreen(e.style.backgroundColor)).length,
                red: bars.filter(e => isRed(e.style.backgroundColor)).length,
                colors: bars.map(e => e.style.backgroundColor),
                scoreColor: [scoreColor(80), scoreColor(20)],
              };
            }""")
            results.append(['dim bar color semantic (high=green/low=red)',
                            dimchk['n'] == 4 and dimchk['green'] >= 1
                            and dimchk['red'] >= 1,
                            dimchk, '4 bars, has green+red'])
            results.append(['scoreColor high=green low=red',
                            dimchk['scoreColor'] == ['#1e8a4c', '#c93a3f'],
                            dimchk['scoreColor'], ['#1e8a4c', '#c93a3f']])
            # 恢复对准窗口，供分享图检查使用
            page.evaluate(
                '() => { alignState.data.alignment = window.__savedAlign;'
                ' renderAlignPage(); }')
            page.wait_for_timeout(300)
            # 搜目标点：应设为目标并自动推导方位（机位缺失时用当前城市）
            # 注意：设置机位/目标会触发真实 /api/align 请求（被本测试 abort），
            # 会清空注入的 mock 数据，故先备份、交互后恢复。
            page.evaluate('() => { window.__savedData = alignState.data;'
                          ' alignState.cam = null; alignState.target = null;'
                          ' alignState.az = 0; syncAlignControls(); }')
            page.fill('#alignCamSearch', '')
            # 输入即出模糊匹配（不点按钮）：本地地名库命中应即时返回
            page.fill('#alignTargetSearch', '珠海')
            live = 0
            try:
                page.wait_for_selector(
                    '#alignTargetResults .sr-item[data-i]', timeout=20000)
                live = page.locator('#alignTargetResults .sr-item[data-i]').count()
            except Exception:
                live = 0
            results.append(['live search on typing (no button)', live >= 1,
                            live, '>=1'])
            style = page.evaluate("""() => {
              const p = document.getElementById('alignTargetResults');
              const cs = getComputedStyle(p);
              return {overflowY: cs.overflowY, maxH: cs.maxHeight,
                      overscroll: cs.overscrollBehaviorY,
                      hasMark: !!p.querySelector('.sr-item mark'),
                      hasHead: !!p.querySelector('.sr-head'),
                      pos: cs.position};
            }""")
            results.append(['search panel styled as dropdown',
                            style['pos'] == 'absolute'
                            and style['overflowY'] == 'auto'
                            and style['maxH'] not in ('none', ''),
                            style, 'absolute+auto+maxH'])
            results.append(['search match highlighted',
                            style['hasMark'], style['hasMark'], True])
            results.append(['search panel has count/source head',
                            style['hasHead'], style['hasHead'], True])
            results.append(['search panel overscroll contained',
                            style['overscroll'] == 'contain',
                            style['overscroll'], 'contain'])
            # 滚轮可滚动：填满面板后悬停滚动，scrollTop 必须变化
            wheel = page.evaluate("""() => {
              const p = document.getElementById('alignTargetResults');
              p.innerHTML = Array.from({length: 30}, (_, i) =>
                `<div class="sr-item" data-i="${i}">地点 ${i}</div>`).join('');
              p.scrollTop = 0;
              return {sh: p.scrollHeight, ch: p.clientHeight};
            }""")
            box = page.locator('#alignTargetResults').bounding_box()
            page.mouse.move(box['x'] + box['width'] / 2,
                            box['y'] + box['height'] / 2)
            page.mouse.wheel(0, 400)
            page.wait_for_timeout(300)
            scrolled = page.evaluate(
                '() => document.getElementById("alignTargetResults").scrollTop')
            results.append(['search panel wheel-scrollable',
                            wheel['sh'] > wheel['ch'] and scrolled > 0,
                            [wheel, scrolled], 'scrollHeight>clientHeight & scrollTop>0'])
            # 键盘：↓ 选择下一项、Esc 关闭
            page.fill('#alignTargetSearch', '珠海')
            page.wait_for_selector('#alignTargetResults .sr-item[data-i]',
                                   timeout=20000)
            page.press('#alignTargetSearch', 'ArrowDown')
            kb = page.evaluate(
                '() => (document.querySelector("#alignTargetResults .sr-item.active")'
                ' || {}).textContent')
            page.press('#alignTargetSearch', 'Escape')
            kb2 = page.evaluate(
                '() => { const p = document.getElementById("alignTargetResults");'
                ' return {hidden: p.hidden, display: p.style.display}; }')
            results.append(['search keyboard arrow-down activates item',
                            bool(kb), str(kb)[:30], '有 active 项'])
            results.append(['search Escape closes panel',
                            kb2['hidden'] and kb2['display'] == 'none',
                            kb2, 'hidden'])
            # 清空按钮：清输入并收起面板
            page.fill('#alignTargetSearch', '珠海')
            page.wait_for_selector('#alignTargetResults .sr-item[data-i]',
                                   timeout=20000)
            page.click('.srch[data-role="target"] .srch-clr')
            page.wait_for_timeout(200)
            clr = page.evaluate("""() => ({
              val: document.getElementById('alignTargetSearch').value,
              hidden: document.getElementById('alignTargetResults').hidden,
              btnHidden: document.querySelector(
                '.srch[data-role="target"] .srch-clr').hidden})""")
            results.append(['search clear button resets input & panel',
                            clr['val'] == '' and clr['hidden']
                            and clr['btnHidden'], clr, '空+收起'])
            page.fill('#alignTargetSearch', '广州')
            page.click('#btnAlignTargetSearch')
            try:
                page.wait_for_selector(
                    '#alignTargetResults .sr-item[data-i]', timeout=20000)
                sr = page.locator('#alignTargetResults .sr-item[data-i]').count()
                results.append(['geocode search results', sr >= 1, sr, '>=1'])
            except Exception:
                sr = 0
                results.append(['geocode search results', False, 0, '>=1'])
            if sr:
                page.locator(
                    '#alignTargetResults .sr-item[data-i]').first.click()
                page.wait_for_timeout(400)
                st = page.evaluate(
                    '({hasTgt: !!alignState.target, cam: alignState.cam,'
                    ' az: alignState.az, city: curCity().name,'
                    ' expected: (() => { const o = alignState.cam || curCity();'
                    '   return Math.round(bearingBetween(o.lat, o.lon,'
                    '     alignState.target.lat, alignState.target.lon)'
                    '     * 10) / 10; })(),'
                    ' camTxt: document.getElementById("alignCamTxt").textContent,'
                    ' tgtTxt: document.getElementById("alignTargetTxt").textContent,'
                    ' camSearch: document.getElementById("alignCamSearch").value,'
                    ' boxHidden: document.getElementById("alignTargetResults")'
                    '   .style.display})')
                results.append(['search target sets target',
                                st['hasTgt'] and st['cam'] is None,
                                st['hasTgt'], True])
                results.append(['search target keeps default city camera',
                                st['city'] in st['camTxt'],
                                st['camTxt'], st['city']])
                # 方位应等于页面内独立算出的「机位→目标」连线方位
                results.append(['search target derives azimuth',
                                st['az'] == st['expected'],
                                st['az'], st['expected']])
                results.append(['search target shows target name',
                                '广州' in st['tgtTxt'],
                                st['tgtTxt'], '广州…'])
            # 地图点击不再覆盖已设角色：已有目标时点地图应改机位
            box2 = page.locator('#alignMap').bounding_box()
            page.mouse.click(box2['x'] + box2['width'] * 0.3,
                             box2['y'] + box2['height'] * 0.45)
            page.wait_for_timeout(400)
            keep = page.evaluate(
                '({cam: !!alignState.cam, tgt: !!alignState.target,'
                ' az: alignState.az})')
            results.append(['map click keeps existing target',
                            keep['cam'] and keep['tgt'], keep,
                            'both set'])
            results.append(['map click recomputes azimuth',
                            0 < keep['az'] < 360, keep['az'], '0..360'])
            # 清除机位回到当前城市默认
            page.click('#btnAlignClearCam')
            page.wait_for_timeout(300)
            back = page.evaluate(
                '({cam: alignState.cam,'
                ' txt: document.getElementById("alignCamTxt").textContent})')
            results.append(['clear camera falls back to city',
                            back['cam'] is None and '默认' in back['txt'],
                            back['txt'], '默认'])
            # 恢复注入的 mock 数据（上面的交互已把 alignState.data 清空）
            page.evaluate('() => { alignState.data = window.__savedData;'
                          ' renderAlignPage(); }')
            page.wait_for_timeout(400)
            share_size = page.evaluate(
                '(async () => { const w = alignWindowList()[0];'
                ' const cv = await buildAlignShareImage(w, alignState.data);'
                ' return [cv.width, cv.height]; })()')
            results.append(['align share canvas', share_size == [1080, 1920],
                            share_size, [1080, 1920]])

            # 日历已移除：确认卡片与接口调用都不再存在
            results.append(['calendar card removed',
                            page.locator('#calendarCard').count() == 0,
                            page.locator('#calendarCard').count(), 0])
            results.append(['calendar symbols removed',
                            page.evaluate('() => typeof initCalendar'
                                          ' === "undefined"'
                                          ' && typeof loadMoonCalendar'
                                          ' === "undefined"'),
                            True, True])

            # 切回霞光：注入 mock 报告，检查曲线事件标记
            page.click('#modeSeg button[data-v="glow"]')
            page.wait_for_timeout(600)
            bundle = mock_report.build(kind='sunset', probability=58)
            page.evaluate(
                '(r) => { window.__gcCurReport = r; curReport = r; render(); }',
                bundle)
            page.wait_for_timeout(500)
            results += page.evaluate(GLOW_UI)
            results += page.evaluate(THEME_UI)

            # v2.35 地区分流：PRD 判定词/配色阈值（verdictCN/probColor 按
            # curRegion 分流，与后端曲线同源；悬日月渲染入口会重置回默认）
            results += page.evaluate("""
              (() => {
                const out = [];
                const push = (name, ok) => out.push([name, ok, ok, true]);
                curRegion = 'prd';
                push('PRD 判定词阈值（58=微烧）', verdictCN(58) === '微烧');
                push('PRD 判定词阈值（70=小烧/80=中烧/90=大烧）',
                     verdictCN(70) === '小烧' && verdictCN(80) === '中烧'
                     && verdictCN(90) === '大烧');
                push('PRD 配色阈值（70 橙/85 红/60 绿）',
                     probColor(70) === '#e8973a' && probColor(85) === '#d94f1e'
                     && probColor(60) === '#4a8a4a');
                curRegion = null;
                push('上海阈值不受影响（58=大烧/70 红）',
                     verdictCN(58) === '大烧' && probColor(70) === '#d94f1e');
                return out;
              })()
            """)
            # ---- v2.37 D 档：无障碍 + 地名搜索降级 ----
            page.goto(f'http://127.0.0.1:{port}/', wait_until='domcontentloaded')
            page.wait_for_function(
                'typeof renderSky === "function" '
                '&& typeof renderAlignPage === "function"', timeout=30000)
            page.wait_for_timeout(1200)
            page.evaluate('(r) => { window.__gcCurReport = r; curReport = r; '
                          'render(); hideIdle(); }', bundle)
            page.wait_for_timeout(400)
            results += page.evaluate(A11Y_UI)

            # 键盘焦点必须看得见（:focus-visible）——用真 Tab 键建立键盘模态
            page.evaluate('() => { if (document.activeElement) '
                          'document.activeElement.blur(); }')
            page.keyboard.press('Tab')
            focus = page.evaluate("""() => {
              const el = document.activeElement;
              const cs = getComputedStyle(el);
              return {id: el.id || el.tagName, style: cs.outlineStyle,
                      w: cs.outlineWidth, shadow: cs.boxShadow};
            }""")
            results.append(['keyboard focus is visibly outlined',
                            (focus['style'] not in ('none', '')
                             and focus['w'] not in ('0px', ''))
                            or (focus['shadow'] not in ('none', '')),
                            focus, 'outline or focus ring'])

            # 切换分区：aria-selected 要跟着动，且锚点存在（A单列：导航=滚动锚点）
            page.click('#tabnav [data-sec="evidence"]')
            page.wait_for_timeout(400)
            tabstate = page.evaluate("""() => {
              const b = document.querySelector('#tabnav [data-sec="evidence"]');
              const anchor = document.getElementById('sec-evidence');
              return {sel: b.getAttribute('aria-selected'),
                      anchor: !!anchor,
                      top: anchor ? anchor.getBoundingClientRect().top : null};
            }""")
            results.append(['active tab announces itself',
                            tabstate['sel'] == 'true' and tabstate['anchor'],
                            tabstate, 'aria-selected=true + anchor exists'])
            page.click('#tabnav [data-sec="tonight"]')
            page.wait_for_timeout(300)

            # 悬日月：日历预览按钮的 aria-label + 周格键盘 + 地名搜索注入/降级
            page.click('#modeSeg button[data-v="align"]')
            page.wait_for_timeout(900)
            align = mock_report.build_align()
            page.evaluate("""(d) => {
              const c = alignState.cam || curCity();
              alignState.cam = c;
              alignState.target = destPoint(c.lat, c.lon, 270, 8.0);
              alignState.az = 270;
              alignState.data = d;
              alignState.key = alignRequestKey(
                c, document.getElementById('dateSel').value);
              renderAlignPage();
            }""", align)
            page.wait_for_timeout(600)
            results += page.evaluate(WEEK_A11Y_UI)

            prevlab = page.evaluate("""() => {
              const b = Array.from(document.querySelectorAll('.prev-btn'));
              return {n: b.length,
                      bad: b.filter(x => !(x.getAttribute('aria-label') || '').trim()).length};
            }""")
            results.append(['preview buttons are labelled',
                            prevlab['n'] > 0 and prevlab['bad'] == 0, prevlab,
                            'every 👁 button labelled'])

            # 周格：Tab 可聚焦 + 回车等同点击（放最后：回车会触发一次取数，
            # loadAlign 里的 hideAll() 会清掉日历表，故先查日历按钮）
            cell_date = page.evaluate("""() => {
              const c = document.querySelector(
                '#alignWeekGrid .week-cell[data-date]');
              return c ? c.dataset.date : null;
            }""")
            focusable = page.evaluate("""() => {
              const c = document.querySelector(
                '#alignWeekGrid .week-cell[data-date]');
              if (!c) return null;
              c.focus();
              return document.activeElement === c;
            }""")
            page.keyboard.press('Enter')
            page.wait_for_timeout(250)
            picked = page.evaluate(
                "() => document.getElementById('dateSel').value")
            results.append(['week cell works from the keyboard',
                            bool(focusable) and picked == cell_date,
                            {'focusable': focusable, 'picked': picked},
                            {'focusable': True, 'picked': cell_date}])

            page.fill('.srch[data-role="target"] input', 'zzqq-xss')
            page.press('.srch[data-role="target"] input', 'Enter')
            page.wait_for_timeout(800)
            inj = page.evaluate("""() => {
              const p = document.getElementById('alignTargetResults');
              const it = p ? p.querySelector('.sr-item') : null;
              return {n: p ? p.querySelectorAll('.sr-item').length : 0,
                      onmouseover: it ? it.hasAttribute('onmouseover') : null,
                      img: p ? !!p.querySelector('.sr-item img') : null,
                      xss: !!window.__xss,
                      nm: it ? (it.querySelector('.nm') || {}).textContent : ''};
            }""")
            results.append(['place name cannot inject markup or attributes',
                            inj['n'] >= 1 and not inj['onmouseover']
                            and not inj['img'] and not inj['xss'], inj,
                            '1 result, no onmouseover/img/window.__xss'])

            page.fill('.srch[data-role="target"] input', 'zzqq-broken')
            page.press('.srch[data-role="target"] input', 'Enter')
            page.wait_for_timeout(800)
            down = page.evaluate("""() => {
              const p = document.getElementById('alignTargetResults');
              return {txt: (p ? p.textContent : '').trim().slice(0, 160)};
            }""")
            results.append(['geocode outage is reported, not "没有找到"',
                            '暂时不可用' in down['txt'] and '没有找到' not in down['txt'],
                            down, 'contains 暂时不可用, not 没有找到'])

            browser.close()
        failed = [r for r in results if not r[1]]
        for name, ok, got, want in results:
            print(('PASS ' if ok else 'FAIL ') + name
                  + ('' if ok else f'  got={got!r} want={want!r}'))
        print(f'\n{len(results) - len(failed)}/{len(results)} passed')
        raise SystemExit(1 if failed else 0)
    finally:
        server.terminate()
        try:
            server.wait(timeout=5)
        except Exception:
            server.kill()


if __name__ == '__main__':
    main()

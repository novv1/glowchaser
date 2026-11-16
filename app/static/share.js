/* 分享图生成：离屏 canvas 1080×1920 竖图
   （概率/判定/最佳时段/一句话归因/色带/品牌水印），下载或原生分享。 */

document.addEventListener('DOMContentLoaded', () => {
  const btn = document.getElementById('btnShare');
  if (!btn) return;
  btn.onclick = async () => {
    const r = window.__gcCurReport || curReport;
    if (!r) return;
    btn.disabled = true;
    btn.textContent = '生成中…';
    try {
      const canvas = await buildShareImage(r);
      const url = canvas.toDataURL('image/png');
      const wrap = document.getElementById('sharePreviewWrap');
      const img = document.getElementById('sharePreview');
      const dl = document.getElementById('shareDownload');
      img.src = url;
      dl.href = url;
      wrap.style.display = 'block';
      wrap.scrollIntoView({behavior: 'smooth', block: 'center'});
      // 移动端原生分享
      if (navigator.share && navigator.canShare) {
        canvas.toBlob(async blob => {
          if (!blob) return;
          const file = new File([blob], 'glowchaser.png', {type: 'image/png'});
          try {
            if (navigator.canShare({files: [file]})) {
              await navigator.share({files: [file], title: '逐光者火烧云预报'});
            }
          } catch (e) { /* 用户取消 */ }
        });
      }
    } finally {
      btn.disabled = false;
      btn.textContent = '📸 生成分享图';
    }
  };
  const closeBtn = document.getElementById('btnShareClose');
  if (closeBtn) {
    closeBtn.onclick = () => {
      document.getElementById('sharePreviewWrap').style.display = 'none';
    };
  }
});

/* ---------------- M7 · 海报版式锚点与场景帧 ----------------
   v2.0 §4：分享海报顶部帧改由 SceneEngine 离屏渲染当前场景（复用同一参数 → 海报=页面所见）。
   三个锚点常量把版式钉死在 1920 内：画面卡 / 地图卡 / 底部信息区。 */
const PCARD = {x: 56, y: 96, w: 968, h: 400, r: 26};
const PMAP = {x: 140, y: 1395, w: 800, h: 365, r: 26};
const PFOOT = {y: 1810, h: 78};

/* 卡内右下角的场景参数行（与页面 #frameMetaC 同一口径） */
function sceneMetaLine(p) {
  try {
    if (!p) {
      const SE = window.SceneEngine;
      p = (SE && SE.params) ? SE.params() : null;
    }
    if (!p) return '';
    return String(p.genus || '--').toUpperCase() + ' · LIT ' + p.lit + '% · ALT '
      + (+p.alt).toFixed(1) + '°'
      + (p.rain && p.rain !== 'none' ? ' · ' + String(p.rain).toUpperCase() : '')
      + (p.moon === 'full' ? ' · MOON' : '');
  } catch (e) { return ''; }
}

/* 顶部场景帧卡片：引擎离屏出图（params 省略=用页面当前场景参数）。
   引擎不可用/2D 失败 → 返回 false，海报退回原版式（不抛错、不露白）。 */
function posterSceneCard(ctx, params, caption) {
  const SE = window.SceneEngine;
  if (!SE || typeof SE.renderStill !== 'function') return false;
  let still = null;
  try {
    still = params ? SE.renderStill({w: PCARD.w, h: PCARD.h, params})
                   : SE.renderStill({w: PCARD.w, h: PCARD.h});
  } catch (e) { still = null; }
  if (!still) return false;
  ctx.save();
  roundRectPath(ctx, PCARD.x, PCARD.y, PCARD.w, PCARD.h, PCARD.r);
  ctx.clip();
  ctx.fillStyle = 'rgba(10,12,20,.9)';
  ctx.fillRect(PCARD.x, PCARD.y, PCARD.w, PCARD.h);
  try { ctx.drawImage(still, PCARD.x, PCARD.y, PCARD.w, PCARD.h); } catch (e) {}
  const scrim = ctx.createLinearGradient(0, PCARD.y + PCARD.h - 104, 0,
                                         PCARD.y + PCARD.h);
  scrim.addColorStop(0, 'rgba(0,0,0,0)');
  scrim.addColorStop(1, 'rgba(0,0,0,.66)');
  ctx.fillStyle = scrim;
  ctx.fillRect(PCARD.x, PCARD.y + PCARD.h - 104, PCARD.w, 104);
  ctx.restore();
  /* 边框 + 取景角标：与页面 .cn-frame 同一套语言 */
  ctx.strokeStyle = 'rgba(255,255,255,.30)';
  ctx.lineWidth = 2;
  roundRectPath(ctx, PCARD.x, PCARD.y, PCARD.w, PCARD.h, PCARD.r);
  ctx.stroke();
  ctx.strokeStyle = 'rgba(255,214,140,.9)';
  ctx.lineWidth = 4;
  ctx.lineCap = 'round';
  const L = 34, pad = 16;
  [[PCARD.x, PCARD.y, 1, 1], [PCARD.x + PCARD.w, PCARD.y, -1, 1],
   [PCARD.x, PCARD.y + PCARD.h, 1, -1],
   [PCARD.x + PCARD.w, PCARD.y + PCARD.h, -1, -1]].forEach(([x, y, sx, sy]) => {
    ctx.beginPath();
    ctx.moveTo(x + sx * pad, y + sy * (L + pad));
    ctx.lineTo(x + sx * pad, y + sy * pad);
    ctx.lineTo(x + sx * (L + pad), y + sy * pad);
    ctx.stroke();
  });
  ctx.textAlign = 'left';
  ctx.font = '500 26px "PingFang SC",sans-serif';
  ctx.fillStyle = 'rgba(255,255,255,.92)';
  ctx.fillText('● REC  ' + (caption || 'SCENE · 日暮片场'), PCARD.x + 30,
               PCARD.y + PCARD.h - 28);
  ctx.textAlign = 'right';
  ctx.font = '400 24px "PingFang SC",sans-serif';
  ctx.fillStyle = 'rgba(255,255,255,.78)';
  ctx.fillText(sceneMetaLine(params), PCARD.x + PCARD.w - 30,
               PCARD.y + PCARD.h - 28);
  ctx.textAlign = 'center';
  return true;
}

async function buildShareImage(r) {
  const W = 1080, H = 1920;
  const cv = document.createElement('canvas');
  cv.width = W;
  cv.height = H;
  const ctx = cv.getContext('2d');
  const p = +r.probability || 0;
  const city = (typeof curCity === 'function') ? curCity() : {name: ''};
  const typeTxt = r.meta.type === 'sunset' ? '晚霞' : '朝霞';

  // 底色：按概率的暖色渐变
  const g = ctx.createLinearGradient(0, 0, 0, H);
  if (p >= 38)      { g.addColorStop(0, '#7a2418'); g.addColorStop(.55, '#c23a1e'); g.addColorStop(1, '#e8602a'); }
  else if (p >= 26) { g.addColorStop(0, '#6e3a1a'); g.addColorStop(.55, '#c2622a'); g.addColorStop(1, '#f0923c'); }
  else if (p >= 18) { g.addColorStop(0, '#3a4258'); g.addColorStop(.55, '#6a5a52'); g.addColorStop(1, '#b8764a'); }
  else              { g.addColorStop(0, '#141a26'); g.addColorStop(.55, '#2a3448'); g.addColorStop(1, '#4a5a70'); }
  ctx.fillStyle = g;
  ctx.fillRect(0, 0, W, H);

  // 装饰光晕
  const rg = ctx.createRadialGradient(W / 2, H * 0.30, 0, W / 2, H * 0.30, W * 0.7);
  rg.addColorStop(0, 'rgba(255,200,120,.22)');
  rg.addColorStop(1, 'rgba(255,200,120,0)');
  ctx.fillStyle = rg;
  ctx.fillRect(0, 0, W, H);

  /* 顶部场景帧（离屏渲染当前场景；引擎不可用则整块退回原版式） */
  const hasCard = posterSceneCard(ctx, null, 'SCENE · 日暮片场');
  const L = hasCard
    ? {brand: 60, city: 572, date: 632, prob: 932, caps: 974, band: 1112,
       best: 1207, reason: 1267, probFont: 250}
    : {brand: 110, city: 200, date: 262, prob: H * 0.36, caps: H * 0.40,
       band: H * 0.48, best: H * 0.55, reason: H * 0.61, probFont: 300};

  ctx.textAlign = 'center';
  ctx.fillStyle = 'rgba(255,255,255,.85)';
  ctx.font = '500 34px "PingFang SC","Microsoft YaHei",sans-serif';
  ctx.fillText('逐 光 者 GlowChaser', W / 2, L.brand);
  ctx.font = '600 44px "PingFang SC","Microsoft YaHei",sans-serif';
  ctx.fillStyle = '#fff';
  ctx.fillText(`${city.name} · ${typeTxt}`, W / 2, L.city);
  ctx.font = '400 32px "PingFang SC",sans-serif';
  ctx.fillStyle = 'rgba(255,255,255,.85)';
  ctx.fillText(`${r.meta.date} · ${r.meta.type === 'sunset' ? '日落' : '日出'} ${r.astronomy.key_time} · 方位 ${r.astronomy.key_az}°`,
    W / 2, L.date);

  // 大号概率
  ctx.font = `800 ${L.probFont}px "PingFang SC",sans-serif`;
  ctx.fillStyle = '#fff';
  ctx.shadowColor = 'rgba(0,0,0,.25)';
  ctx.shadowBlur = 30;
  ctx.fillText(p + '%', W / 2, L.prob);
  ctx.shadowBlur = 0;

  // 判定词胶囊
  const verdict = r.verdict || '';
  ctx.font = '600 52px "PingFang SC",sans-serif';
  const vw = ctx.measureText(verdict).width + 120;
  roundRect(ctx, W / 2 - vw / 2, L.caps, vw, 96, 48);
  ctx.fillStyle = 'rgba(255,255,255,.18)';
  ctx.fill();
  ctx.fillStyle = '#fff';
  ctx.fillText(verdict, W / 2, L.caps + 68);

  // 概率色带（概率语义统一色阶）
  const bandY = L.band;
  const bg2 = ctx.createLinearGradient(W * 0.12, 0, W * 0.88, 0);
  bg2.addColorStop(0, '#4a8a4a');
  bg2.addColorStop(0.5, '#e8973a');
  bg2.addColorStop(1, '#d94f1e');
  ctx.fillStyle = bg2;
  roundRect(ctx, W * 0.12, bandY, W * 0.76, 14, 7);
  ctx.fill();
  const px = W * 0.12 + W * 0.76 * Math.min(1, p / 60);
  ctx.fillStyle = '#fff';
  ctx.beginPath();
  ctx.arc(px, bandY + 7, 14, 0, Math.PI * 2);
  ctx.fill();

  // 最佳时段 + 归因（归因最多 2 行，保证不压到地图卡）
  ctx.font = '500 38px "PingFang SC",sans-serif';
  ctx.fillStyle = '#fff';
  if (r.best_window) {
    ctx.fillText(`最佳时段  ${r.best_window.start} – ${r.best_window.end}`,
      W / 2, L.best);
  }
  wrapText(ctx, r.reason || '', W / 2, L.reason, W * 0.78, 46,
    '400 34px "PingFang SC",sans-serif', 'rgba(255,255,255,.92)', 2);

  // 地图快照（若地图已渲染；等比装入地图卡，不拉伸）
  try {
    if (typeof mapChart !== 'undefined' && mapChart && !mapChart.isDisposed()) {
      // 隐藏 tab 里的 ECharts 会被切 tab 的 resize() 量成 0×0（#burnMap rect 0×0），
      // 此时 getDataURL() 抛「canvas element with a width or height of 0」→ 地图区空白。
      // 取图前先量尺寸，必要时给一个明确尺寸；取完还原（用户切回「地图」tab 会自行 resize）。
      const forced = mapChart.getWidth() < 8 || mapChart.getHeight() < 8;
      if (forced) mapChart.resize({width: PMAP.w, height: PMAP.h});
      let mapUrl;
      try {
        mapUrl = mapChart.getDataURL({pixelRatio: 2, backgroundColor: '#1a2233'});
      } finally {
        if (forced) { try { mapChart.resize(); } catch (e2) { /* disposed */ } }
      }
      const img = await loadImage(mapUrl);
      ctx.save();
      roundRectPath(ctx, PMAP.x, PMAP.y, PMAP.w, PMAP.h, PMAP.r);
      ctx.clip();
      ctx.fillStyle = 'rgba(255,255,255,.06)';
      ctx.fillRect(PMAP.x, PMAP.y, PMAP.w, PMAP.h);
      const sc = Math.min(PMAP.w / img.width, PMAP.h / img.height);
      const dw = img.width * sc, dh = img.height * sc;
      ctx.drawImage(img, PMAP.x + (PMAP.w - dw) / 2, PMAP.y + (PMAP.h - dh) / 2,
                    dw, dh);
      ctx.restore();
      ctx.strokeStyle = 'rgba(255,255,255,.35)';
      ctx.lineWidth = 3;
      roundRectPath(ctx, PMAP.x, PMAP.y, PMAP.w, PMAP.h, PMAP.r);
      ctx.stroke();
    }
  } catch (e) { /* 地图快照失败则跳过 */ }

  // 底部信息（固定在 PFOOT 内，不压地图卡）
  ctx.font = '400 28px "PingFang SC",sans-serif';
  ctx.fillStyle = 'rgba(255,255,255,.75)';
  ctx.fillText(`${r.credibility || ''}`, W / 2, PFOOT.y);
  ctx.fillText('八维评分 · 多模式共识 · 卫星校准', W / 2, PFOOT.y + 38);
  ctx.font = '500 30px "PingFang SC",sans-serif';
  ctx.fillStyle = 'rgba(255,255,255,.9)';
  ctx.fillText('🌇 逐光者 GlowChaser', W / 2, PFOOT.y + 76);

  return cv;
}

function wrapText(ctx, text, x, y, maxW, lineH, font, color, maxLines) {
  ctx.font = font;
  ctx.fillStyle = color;
  if (!text) return;
  const limit = maxLines || 0;
  let line = '';
  let yy = y;
  let n = 0;
  for (const ch of text) {
    if (ctx.measureText(line + ch).width > maxW) {
      if (limit && n === limit - 1) {          /* 最后一行：截断 + 省略号 */
        let cut = line;
        while (cut && ctx.measureText(cut + '…').width > maxW) cut = cut.slice(0, -1);
        ctx.fillText(cut + '…', x, yy);
        return;
      }
      ctx.fillText(line, x, yy);
      line = ch;
      yy += lineH;
      n++;
    } else {
      line += ch;
    }
  }
  if (line) ctx.fillText(line, x, yy);
}

function roundRectPath(ctx, x, y, w, h, r) {
  ctx.beginPath();
  ctx.moveTo(x + r, y);
  ctx.arcTo(x + w, y, x + w, y + h, r);
  ctx.arcTo(x + w, y + h, x, y + h, r);
  ctx.arcTo(x, y + h, x, y, r);
  ctx.arcTo(x, y, x + w, y, r);
  ctx.closePath();
}
function roundRect(ctx, x, y, w, h, r) { roundRectPath(ctx, x, y, w, h, r); }

function loadImage(url) {
  return new Promise((res, rej) => {
    const img = new Image();
    img.onload = () => res(img);
    img.onerror = rej;
    img.src = url;
  });
}

/* ---------------- 悬日月分享图 ---------------- */
document.addEventListener('DOMContentLoaded', () => {
  const abtn = document.getElementById('btnAlignShare');
  if (!abtn) return;
  abtn.onclick = async () => {
    const r = (typeof alignState !== 'undefined') ? alignState.data : null;
    // 与预览下拉保持一致：分享图应该是"我正在看的那条窗口"
    const list = (typeof alignWindowList === 'function')
      ? alignWindowList() : [];
    const selEl = document.getElementById('pvWindowSel');
    const picked = (selEl && selEl.__list && selEl.value !== '')
      ? selEl.__list[+selEl.value] : null;
    const w = (selEl && selEl.__cur) || picked || list[0] || null;
    if (!r || !w) return;
    abtn.disabled = true;
    abtn.textContent = '生成中…';
    try {
      const canvas = await buildAlignShareImage(w, r);
      const url = canvas.toDataURL('image/png');
      const wrap = document.getElementById('alignShareWrap');
      const img = document.getElementById('alignSharePreview');
      const dl = document.getElementById('alignShareDownload');
      img.src = url;
      dl.href = url;
      wrap.style.display = 'block';
      wrap.scrollIntoView({behavior: 'smooth', block: 'center'});
    } finally {
      abtn.disabled = false;
      abtn.textContent = '📸 生成分享图';
    }
  };
  const aclose = document.getElementById('alignShareClose');
  if (aclose) {
    aclose.onclick = () => {
      document.getElementById('alignShareWrap').style.display = 'none';
    };
  }
});

async function buildAlignShareImage(w, r) {
  const W = 1080, H = 1920;
  const cv = document.createElement('canvas');
  cv.width = W;
  cv.height = H;
  const ctx = cv.getContext('2d');
  const f = w.feasibility
    || {score: 0, verdict: '-', dims: {}, reasons: [], risks: []};
  const city = (typeof curCity === 'function') ? curCity() : {name: ''};
  const g = ctx.createLinearGradient(0, 0, 0, H);
  if (f.score >= 65) {
    g.addColorStop(0, '#0d2419'); g.addColorStop(.5, '#1c5a3a');
    g.addColorStop(1, '#2e9e5a');
  } else if (f.score >= 45) {
    g.addColorStop(0, '#2a2410'); g.addColorStop(.5, '#6a5418');
    g.addColorStop(1, '#c8a02a');
  } else {
    g.addColorStop(0, '#1a1420'); g.addColorStop(.5, '#4a2a2a');
    g.addColorStop(1, '#8a3a2a');
  }
  ctx.fillStyle = g;
  ctx.fillRect(0, 0, W, H);
  const rg = ctx.createRadialGradient(W / 2, H * 0.28, 0, W / 2, H * 0.28,
                                      W * 0.7);
  rg.addColorStop(0, 'rgba(255,255,255,.14)');
  rg.addColorStop(1, 'rgba(255,255,255,0)');
  ctx.fillStyle = rg;
  ctx.fillRect(0, 0, W, H);

  /* 顶部场景帧：按当前对准窗口推场景（悬月出满月盘）；与页面同参数口径 */
  let sp = null;
  try {
    const SE = window.SceneEngine;
    if (SE && typeof SE.mapAlignToScene === 'function') {
      sp = SE.mapAlignToScene(w, {cityId: (city && (city.id || city.name)) || ''});
    }
  } catch (e) { sp = null; }
  const hasCard = posterSceneCard(ctx, sp, `SCENE · ${w.type} ${w.period_name}`);
  const L = hasCard
    ? {brand: 60, title: 572, sub: 632, score: 932, verdict: 1000, facts: 1075,
       factStep: 54, bars: 1300, barStep: 86, good: 1616, risk: 1672,
       scoreFont: 240}
    : {brand: 110, title: 230, sub: 300, score: 720, verdict: 800, facts: 900,
       factStep: 56, bars: 1180, barStep: 90, good: 1600, risk: 1660,
       scoreFont: 280};

  ctx.textAlign = 'center';
  ctx.fillStyle = 'rgba(255,255,255,.85)';
  ctx.font = '500 34px "PingFang SC",sans-serif';
  ctx.fillText('逐 光 者 GlowChaser', W / 2, L.brand);
  ctx.fillStyle = '#fff';
  ctx.font = '700 68px "PingFang SC",sans-serif';
  ctx.fillText(`${w.type} · ${w.period_name}`, W / 2, L.title);
  ctx.font = '400 38px "PingFang SC",sans-serif';
  ctx.fillStyle = 'rgba(255,255,255,.88)';
  ctx.fillText(`${city.name || ''} · ${(w.date || '').slice(5)} ${w.best_time}`,
               W / 2, L.sub);

  ctx.fillStyle = '#fff';
  ctx.font = `800 ${L.scoreFont}px "PingFang SC",sans-serif`;
  ctx.fillText(String(f.score), W / 2, L.score);
  ctx.font = '600 46px "PingFang SC",sans-serif';
  ctx.fillText(`拍摄可行性 · ${f.verdict}`, W / 2, L.verdict);

  const sky = w.sky || {};
  // 该方位的山脊遮蔽角：window.horizon_alt 是"目标仰角"，不是山脊，缺测时
  // 之前只印一个「-」。这里直接从 site_info 的 16 方位遮蔽角插值出真实山脊，
  // 并说清高于/低于天体（悬日月最要紧的一条）。
  let ridge = null;
  if (r.site_info && r.site_info.horizon_mask
      && typeof horizonAltAtMask === 'function' && w.best_az != null) {
    ridge = horizonAltAtMask(r.site_info.horizon_mask, w.best_az);
  }
  const ridgeTxt = ridge == null ? '山脊 无地形数据'
    : `山脊 ${ridge.toFixed(1)}°`
      + (w.best_alt != null
        ? (ridge > w.best_alt ? '（高于天体，遮挡）' : '（低于天体）') : '');
  const facts = [
    `方位 ${w.best_az}°（差 ±${w.best_daz}°）`,
    `高度 ${w.best_alt}°（视 ${w.apparent_alt}°）`,
    ridgeTxt,
    `${sky.moon_phase || ''}`
      + `${sky.moon_illum != null ? ' ' + sky.moon_illum + '%' : ''}`,
  ].filter(Boolean);
  ctx.font = '400 34px "PingFang SC",sans-serif';
  ctx.fillStyle = 'rgba(255,255,255,.92)';
  facts.forEach((t, i) => ctx.fillText(t, W / 2, L.facts + i * L.factStep));

  const dims = [
    ['云况（视线交点遮挡）', f.dims.cloud],
    ['通透度（AOD/PM2.5）', f.dims.transparency],
    ['降水（概率）', f.dims.precip],
    ['稳定性（模式一致）', f.dims.stability],
  ];
  const barX = 140, barW = 800, barH = 26;
  dims.forEach(([name, v], i) => {
    const y = L.bars + i * L.barStep;
    ctx.textAlign = 'left';
    ctx.font = '500 32px "PingFang SC",sans-serif';
    ctx.fillStyle = 'rgba(255,255,255,.9)';
    ctx.fillText(name, barX, y - 10);
    ctx.textAlign = 'right';
    ctx.fillText(v == null ? '-' : String(v), barX + barW, y - 10);
    ctx.fillStyle = 'rgba(255,255,255,.22)';
    roundRect(ctx, barX, y, barW, barH, barH / 2);
    ctx.fill();
    ctx.fillStyle = v >= 65 ? '#7ce8a0' : v >= 45 ? '#f0c040' : '#f08060';
    roundRect(ctx, barX, y, Math.max(barH, barW * (v || 0) / 100), barH,
              barH / 2);
    ctx.fill();
  });

  ctx.textAlign = 'left';
  ctx.font = '500 30px "PingFang SC",sans-serif';
  ctx.fillStyle = '#a8f0c0';
  let good = '有利：' + ((f.reasons || []).join('、') || '-');
  if (good.length > 30) good = good.slice(0, 30) + '…';
  ctx.fillText(good, 100, L.good);
  ctx.fillStyle = '#ffd08a';
  let risk = '风险：' + ((f.risks || []).join('；') || '暂无');
  if (risk.length > 30) risk = risk.slice(0, 30) + '…';
  ctx.fillText(risk, 100, L.risk);

  ctx.textAlign = 'center';
  ctx.font = '400 30px "PingFang SC",sans-serif';
  ctx.fillStyle = 'rgba(255,255,255,.7)';
  ctx.fillText('逐光者 GlowChaser · 悬日月对准', W / 2, H - 100);
  ctx.font = '400 24px "PingFang SC",sans-serif';
  ctx.fillStyle = 'rgba(255,255,255,.5)';
  ctx.fillText('仅供观赏参考 · 数据：Open-Meteo / CAMS / JMA',
               W / 2, H - 56);
  return cv;
}

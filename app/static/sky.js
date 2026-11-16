/* 霞光模拟 v2：太阳高度角驱动的时间演变 + 真实地平线遮蔽 + 光路云量。
   数据来自报告 bundle：
   - tracks.sun_alt/sun_az  24h 真实轨迹（30 分钟步长，线性插值）
   - main.canvas_avg/low_avg 光路（0-580km）中高云画布 / 低云遮挡
   - main.quality/aod_local 颜色区间、透射率、霾
   - site_info.horizon_mask 16 方位真实遮蔽角（山脊剪影）
   - astronomy.key_time/key_az 事件基准时刻与视野中心方位 */

const SKY_PALETTES = {
  '金黄': ['#ffe9b0', '#ffc85a', '#ff9a3c', '#e8543a', '#b8352a'],
  '橘色': ['#ffc85a', '#ff9a3c', '#e8543a', '#b8352a', '#8a2a20'],
  '橘红': ['#ff9a3c', '#e8543a', '#c23a2e', '#8a2a20', '#5c1a14'],
  '暗':   ['#e8643a', '#b8352a', '#7a2418', '#4a1a12', '#2a0f0a'],
  '灰':   ['#aab2bc', '#8d96a1', '#6c757f', '#4d545d', '#33383f'],
};

const SKY_FOV = 60;          // 模拟视野水平角（度）
const SKY_PX_PER_DEG = 0.045; // 每 1° 高度角占画面高度的比例

function skyPalette(quality, probability) {
  // 颜色区间由后端几何模型给出（云底高+AOD）；概率低只降低强度（dull），
  // 不再直接替换成灰色调，避免「颜色区间橘红却渲染成灰」的自相矛盾。
  const c = (quality && quality.color) || '';
  if (c.includes('金黄')) return SKY_PALETTES['金黄'];
  if (c.includes('橘色')) return SKY_PALETTES['橘色'];
  if (c.includes('橘红')) return SKY_PALETTES['橘红'];
  if (probability < 10) return SKY_PALETTES['灰'];
  return SKY_PALETTES['暗'];
}

function isDarkTheme() {
  return document.documentElement.getAttribute('data-theme') === 'dark';
}

function _skyH2M(t) {
  return parseInt(t.slice(0, 2), 10) * 60 + parseInt(t.slice(3, 5), 10);
}

/* 连续分钟插值：minutes 可 <0 / >1440（跨午夜），轨迹按分钟轴展开 */
function skyTrackValue(tracks, key, minutes) {
  if (!tracks || !tracks.time || !tracks[key]) return null;
  const pts = [];
  for (let i = 0; i < tracks.time.length; i++) {
    const t = (tracks.time[i] || '').slice(0, 5);
    const v = tracks[key][i];
    if (v == null || !/^\d{2}:\d{2}$/.test(t)) continue;
    let m = _skyH2M(t);
    if (pts.length && m < pts[pts.length - 1][0]) m += 1440;  // 23:30 -> 00:00
    pts.push([m, v]);
  }
  if (!pts.length) return null;
  if (minutes <= pts[0][0]) return pts[0][1];
  if (minutes >= pts[pts.length - 1][0]) return pts[pts.length - 1][1];
  for (let i = 1; i < pts.length; i++) {
    if (minutes <= pts[i][0]) {
      const [m0, v0] = pts[i - 1], [m1, v1] = pts[i];
      return v0 + (v1 - v0) * (minutes - m0) / Math.max(1, m1 - m0);
    }
  }
  return pts[pts.length - 1][1];
}

function skyHorizonAtMask(mask, az) {
  az = ((az % 360) + 360) % 360;
  const i0 = Math.floor(az / 22.5) % 16, i1 = (i0 + 1) % 16;
  const f = (az - i0 * 22.5) / 22.5;
  return (mask[i0] || 0) * (1 - f) + (mask[i1] || 0) * f;
}

function renderSky(r, offsetMin) {
  offsetMin = offsetMin || 0;
  const cv = document.getElementById('skyCanvas');
  if (!cv) return;
  const W = cv.width, H = cv.height;
  const ctx = cv.getContext('2d');
  const HZ = Math.round(H * 0.74);
  const m = r.main || {};
  const cl = m.cloud || {};
  const q = m.quality || {};
  const prob = +r.probability || 0;
  const dark = isDarkTheme();
  const isSunset = !r.meta || r.meta.type !== 'sunrise';

  // ---- 基准时刻 + 偏移 → 太阳高度角/方位（真实轨迹插值）----
  const baseHM = (r.astronomy && r.astronomy.key_time)
    || (isSunset ? '18:00' : '06:00');
  const tMin = _skyH2M(baseHM) + offsetMin;
  let sunAlt = skyTrackValue(r.tracks, 'sun_alt', tMin);
  if (sunAlt == null) sunAlt = -0.25 * offsetMin;      // 兜底约 0.25°/min
  let sunAz = skyTrackValue(r.tracks, 'sun_az', tMin);
  if (sunAz == null) sunAz = (r.astronomy && r.astronomy.key_az) || 270;
  const azC = (r.astronomy && r.astronomy.key_az) || sunAz;

  // ---- 光照阶段 ----
  const glowF = clamp01(1 - Math.abs(sunAlt + 2) / 7);   // 峰值在 -2°
  const litF = clamp01((sunAlt + 8) / 8);                // 兜底：全局照亮程度
  const nightF = clamp01((-sunAlt - 4) / 8);             // 星星出现
  const dayF = clamp01(sunAlt / 14);                     // 白天亮度
  // 逐层受光（v2.26）：地影判据 —— 云高 h、朝日方向水平距离 d 的云，
  // 受光 ⟺ 观测者太阳高度角 > −(b(h) + d/R)，b(h)=acos(R/(R+h))。
  // 高层/远云比低层/近云晚好多分钟才变暗，这个差异是"低云先黑、高云续烧"
  // 的来源；后端已给出每带的代表层高与距离，这里按滑块时刻现算。
  const litBand = band => {
    const b = ((r.illumination || {}).bands || {})[band];
    const h = b && b.height_km, d = b && b.dist_km;
    if (!(h > 0) || !(d >= 0)) return litF;
    const RE = 6371.0088;
    const crit = Math.acos(RE / (RE + h)) * 180 / Math.PI;
    const rail = -(crit + d / RE * 180 / Math.PI);
    return clamp01((sunAlt - rail) / 0.6 + 0.5);
  };
  const aod = m.aod_local != null ? m.aod_local : 0.25;
  const haze = clamp01((aod - 0.2) * 0.7) * 0.6;

  // ---- 云量：光路平均优先（画布=中高云，low_avg=低云遮挡）----
  const hiMid = m.canvas_avg != null
    ? m.canvas_avg : ((cl.mid || 0) + (cl.high || 0));
  const lowPath = m.low_avg != null ? m.low_avg : (cl.low || 0);
  const covCanvas = clamp01(hiMid / 100);
  const covLow = clamp01(lowPath / 100);
  const pal = skyPalette(q, prob);
  const dull = Math.max(0.25, Math.min(1,
    prob / 40 * 0.5
    + (q.transmission != null ? Math.min(1, q.transmission * 6) : 0.6)));

  // ---- 天空底色：白天蓝 → 黄昏暖 → 夜间深蓝 ----
  const zenith = mixHex(dark ? '#070b18' : '#0b1020', '#3f6fb0', dayF);
  const midSky = mixHex(dark ? '#101a30' : '#16203a', '#7aa6d8', dayF);
  // 地平线暖色必须是「不透明混色」，否则渐变末段半透明会露出卡片白底
  const duskBase = dark ? '#2a1f2a' : '#3a2c33';
  const horizonCol = mixHex(duskBase, pal[1],
                            Math.min(1, glowF * dull * 0.95));
  const sky = ctx.createLinearGradient(0, 0, 0, HZ);
  sky.addColorStop(0, zenith);
  sky.addColorStop(0.58, midSky);
  sky.addColorStop(1, horizonCol);
  ctx.fillStyle = sky;
  ctx.fillRect(0, 0, W, HZ);

  // ---- 星星（夜深后）----
  drawSkyStars(ctx, W, HZ, nightF,
               skyHashStr((r.meta && r.meta.date) || 'sky'));

  // ---- 太阳与霞光带 ----
  const sunX = W / 2 + (sunAz - azC) / SKY_FOV * W;
  const sunY = HZ - sunAlt * H * SKY_PX_PER_DEG;
  drawSkyGlow(ctx, W, H, HZ, sunX, glowF, dull, pal);
  if (sunAlt > -0.8 && sunX > -80 && sunX < W + 80) {
    drawSkySun(ctx, sunX, sunY, H, sunAlt, glowF, dayF, aod, dark);
  }

  // ---- 云层：高云(远) → 中云 → 低云(近地平线) ----
  // 中高云（画布）按比例拆分：高云占上部、中云在中部
  const hiCov = covCanvas * 0.65, midCov = covCanvas * 0.75;
  drawSkyBand(ctx, W, HZ, 0.52, 0.16, hiCov, pal, litBand('high'), false);
  drawSkyBand(ctx, W, HZ, 0.32, 0.11, midCov, pal, litBand('mid'), false);
  drawSkyBand(ctx, W, HZ, 0.14, 0.07, covLow, pal, litBand('low'), true);

  // ---- 霾罩（AOD 高时压低对比）----
  if (haze > 0.02) {
    ctx.fillStyle = `rgba(206,201,191,${haze.toFixed(2)})`;
    ctx.fillRect(0, 0, W, HZ);
  }

  // ---- 地平线剪影（真实遮蔽角，画在最前，遮挡云与太阳）----
  drawSkyHorizon(ctx, W, H, HZ, r, azC, dark);

  // ---- 水面反光（地平线以下）----
  const water = ctx.createLinearGradient(0, HZ, 0, H);
  water.addColorStop(0, mixHex(duskBase, pal[1],
                               Math.min(1, 0.45 * glowF * dull)));
  water.addColorStop(1, dark ? '#070b18' : '#1a2434');
  ctx.fillStyle = water;
  ctx.fillRect(0, HZ, W, H - HZ);
  if (glowF > 0.05 && sunX > 0 && sunX < W) {
    ctx.globalAlpha = 0.32 * glowF * dull;
    const refW = W * 0.08 * (0.5 + glowF);
    const refGrad = ctx.createLinearGradient(sunX - refW, 0, sunX + refW, 0);
    refGrad.addColorStop(0, 'rgba(0,0,0,0)');
    refGrad.addColorStop(0.5, warmMix(pal, 0.9));
    refGrad.addColorStop(1, 'rgba(0,0,0,0)');
    ctx.fillStyle = refGrad;
    ctx.fillRect(sunX - refW, HZ, refW * 2, H - HZ);
    ctx.globalAlpha = 1;
  }

  // ---- 说明行 ----
  const note = document.getElementById('skyNote');
  if (note) {
    const dir = typeof azimuthName === 'function'
      ? azimuthName(azC) : azC;
    const aodTxt = aod <= 0.15 ? '通透' : aod <= 0.3 ? '较透'
                 : aod <= 0.5 ? '浑浊' : '污烧';
    const sign = offsetMin > 0 ? '+' : '';
    const blocked = (r.site_info && r.site_info.horizon_mask)
      ? skyHorizonAtMask(r.site_info.horizon_mask, sunAz) : 0;
    const blockedTxt = blocked > sunAlt + 0.2
      ? ` · ⛰ 该方位山脊 ${blocked.toFixed(1)}° 高于太阳，日面被挡` : '';
    note.textContent =
      `${isSunset ? '晚霞' : '朝霞'} T${sign}${offsetMin} min（基准 ${baseHM}）· `
      + `太阳高度角 ${sunAlt.toFixed(1)}° · 视野面向 ${dir}（${Math.round(azC)}°）· `
      + `光路中高云 ${Math.round(hiMid)}% / 低云 ${Math.round(lowPath)}% · `
      + `AOD ${aod}（${aodTxt}）· 颜色区间 ${q.color || '-'} · 概率 ${prob}%`
      + blockedTxt + '（推演仅供预览）';
  }
}

function clamp01(v) { return Math.max(0, Math.min(1, v)); }

/* 霞光带：沿太阳方位的水平暖光带，宽度与强度随太阳高度角演变 */
function drawSkyGlow(ctx, W, H, HZ, sunX, glowF, dull, pal) {
  if (glowF <= 0.02) return;
  const gw = W * (0.30 + 0.45 * glowF);
  const gh = HZ * (0.18 + 0.32 * glowF);
  const g = ctx.createLinearGradient(sunX - gw, 0, sunX + gw, 0);
  g.addColorStop(0, 'rgba(0,0,0,0)');
  g.addColorStop(0.5, warmMix(pal, 0.85 * glowF * dull));
  g.addColorStop(1, 'rgba(0,0,0,0)');
  ctx.globalAlpha = 0.75;
  ctx.fillStyle = g;
  ctx.fillRect(sunX - gw, HZ - gh, gw * 2, gh);
  // 贴地平线的更亮核心
  const core = ctx.createLinearGradient(0, HZ - gh * 0.5, 0, HZ);
  core.addColorStop(0, 'rgba(0,0,0,0)');
  core.addColorStop(1, warmMix(pal, 0.95 * glowF * dull));
  ctx.fillStyle = core;
  ctx.fillRect(0, HZ - gh * 0.5, W, gh * 0.5);
  ctx.globalAlpha = 1;
}

/* 太阳：地平线附近时保留大光晕；落到地平线下则只留霞光 */
function drawSkySun(ctx, cx, cy, H, alt, glowF, dayF, aod, dark) {
  const R = H * 0.035;
  const clear = Math.max(0.2, 1 - Math.max(0, aod - 0.15) / 0.6);
  const halo = alt > 3 ? '255,235,180' : '255,190,110';
  const hg = ctx.createRadialGradient(cx, cy, R * 0.3, cx, cy, R * (4 + 3 * glowF));
  hg.addColorStop(0, `rgba(${halo},${(0.75 * clear).toFixed(2)})`);
  hg.addColorStop(0.3, `rgba(255,170,90,${(0.35 * clear).toFixed(2)})`);
  hg.addColorStop(1, 'rgba(255,150,80,0)');
  ctx.fillStyle = hg;
  ctx.beginPath();
  ctx.arc(cx, cy, R * (4 + 3 * glowF), 0, Math.PI * 2);
  ctx.fill();
  ctx.fillStyle = `rgba(255,244,214,${(0.95 * clear).toFixed(2)})`;
  ctx.beginPath();
  ctx.arc(cx, cy, R, 0, Math.PI * 2);
  ctx.fill();
}

/* 云带：高/中云被夕阳照亮（底部亮），低云为暗剪影 */
function drawSkyBand(ctx, W, HZ, yFrac, thickFrac, cover, pal, litF, lowDark) {
  if (cover <= 0.03) return;
  const bandH = HZ * thickFrac;
  const yTop = HZ - HZ * yFrac - bandH;
  const rand = mulberry(Math.round(yFrac * 1000) + Math.round(cover * 100));
  const blockW = W * (0.08 + 0.10 * (1 - cover));
  const lit = Math.max(0.08, litF);
  let x = -blockW * rand();
  while (x < W) {
    const w = blockW * (0.7 + rand() * 0.7);
    const h = bandH * (0.55 + rand() * 0.5);
    const gap = cover >= 0.97 ? 0 : blockW * (1 - cover) * (0.7 + rand() * 0.6);
    const yy = yTop + (bandH - h) * (0.5 + 0.3 * rand());
    const g = ctx.createLinearGradient(0, yy, 0, yy + h);
    if (lowDark) {
      g.addColorStop(0, `rgba(20,26,38,${(0.9 * cover).toFixed(2)})`);
      g.addColorStop(1, `rgba(34,42,58,${(0.95 * cover).toFixed(2)})`);
    } else {
      g.addColorStop(0, hexA(pal[4], Math.min(1, 0.8 * lit * cover * 0.5)));
      g.addColorStop(1, hexA(pal[0], Math.min(1, 0.95 * lit * cover)));
    }
    ctx.fillStyle = g;
    roundedCloud(ctx, x, yy, w, h);
    x += w + gap;
  }
}

/* 真实地平线剪影：16 方位遮蔽角按视野方位插值 */
function drawSkyHorizon(ctx, W, H, HZ, r, azC, dark) {
  const si = r.site_info || {};
  const mask = si.horizon_mask;
  const azL = azC - SKY_FOV / 2, azR = azC + SKY_FOV / 2;
  const altToY = a => HZ - Math.min(H * 0.42,
                                    Math.max(0, a) * H * SKY_PX_PER_DEG);
  ctx.beginPath();
  ctx.moveTo(0, H);
  ctx.lineTo(0, HZ);
  if (mask && mask.length === 16) {
    const steps = 140;
    for (let i = 0; i <= steps; i++) {
      const a = azL + (azR - azL) * i / steps;
      const alt = skyHorizonAtMask(mask, a);
      ctx.lineTo(W * i / steps, altToY(alt));
    }
  } else {
    // 无地形数据：以位置为种子生成低缓起伏
    const seed = skyHashStr(JSON.stringify((r.meta && r.meta.location) || {}));
    const rand = mulberry(seed);
    const steps = 40;
    let h = 0.6;
    for (let i = 0; i <= steps; i++) {
      h += (rand() - 0.5) * 0.9;
      h = Math.max(0.2, Math.min(2.2, h));
      ctx.lineTo(W * i / steps, altToY(h));
    }
  }
  ctx.lineTo(W, H);
  ctx.closePath();
  ctx.fillStyle = dark ? '#04060b' : '#121a26';
  ctx.fill();
}

function drawSkyStars(ctx, W, HZ, nightF, seed) {
  if (nightF <= 0.15) return;
  const rand = mulberry(seed || 7);
  const n = Math.round(130 * nightF);
  for (let i = 0; i < n; i++) {
    const x = rand() * W;
    const y = rand() * HZ * 0.72;
    const r = 0.5 + rand() * 1.3;
    ctx.fillStyle = `rgba(255,255,242,${(0.25 + 0.55 * rand()) * nightF})`;
    ctx.beginPath();
    ctx.arc(x, y, r, 0, Math.PI * 2);
    ctx.fill();
  }
}

function roundedCloud(ctx, x, y, w, h) {
  const r = Math.min(h / 2, w / 3);
  ctx.beginPath();
  ctx.moveTo(x + r, y);
  ctx.arcTo(x + w, y, x + w, y + h, r);
  ctx.arcTo(x + w, y + h, x, y + h, r);
  ctx.arcTo(x, y + h, x, y, r);
  ctx.arcTo(x, y, x + w, y, r);
  ctx.closePath();
  ctx.fill();
}

function warmMix(pal, a) {
  const c = pal[Math.min(1, pal.length - 1)];
  return hexA(c, Math.max(0, Math.min(1, a)));
}

function hexA(hex, a) { return rgbStr(hex, a); }

function mulberry(seed) {
  let t = seed >>> 0;
  return function() {
    t += 0x6D2B79F5;
    let z = t;
    z = Math.imul(z ^ (z >>> 15), z | 1);
    z ^= z + Math.imul(z ^ (z >>> 7), z | 61);
    return ((z ^ (z >>> 14)) >>> 0) / 4294967296;
  };
}

function skyHashStr(s) {
  let h = 7;
  for (let i = 0; i < s.length; i++) h = Math.imul(h, 31) + s.charCodeAt(i) | 0;
  return h >>> 0;
}

/* 兼容旧调用（sky_preview.js 用） */
function hashStr(s) { return skyHashStr(s); }

/* 颜色工具：接受 #rrggbb 与 rgb()/rgba() 两种输入（mixHex 的结果会被继续参与
   混色，只认 hex 会在链式调用里悄悄算出 NaN 颜色、让整块填充被浏览器丢弃）。 */
function colorRGB(c) {
  const s = String(c || '#000000').trim();
  if (s[0] === '#') {
    const n = parseInt(s.slice(1), 16);
    return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
  }
  const m = s.match(/[\d.]+/g) || [0, 0, 0];
  return [Math.round(+m[0] || 0), Math.round(+m[1] || 0), Math.round(+m[2] || 0)];
}

function rgbStr(c, a) {
  const p = colorRGB(c);
  return a == null ? `rgb(${p[0]},${p[1]},${p[2]})`
    : `rgba(${p[0]},${p[1]},${p[2]},${Math.max(0, Math.min(1, a)).toFixed(2)})`;
}

function mixHex(h1, h2, f) {
  const a = colorRGB(h1), b = colorRGB(h2);
  const c = i => Math.round(a[i] * (1 - f) + b[i] * f);
  return `rgb(${c(0)},${c(1)},${c(2)})`;
}


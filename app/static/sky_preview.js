/* 巧摄式天体取景预览 v3 —— 真实视场 / 视线云带 / 地形遮挡
 *
 * 与 v2 的关键差别（v2 的画面其实与你的镜头无关）：
 * - 等比投影：横竖两轴同一 px/°，取景框严格等于「视场角 aov × 画布比例」；
 *   视线内容装不下时按最小必要倍率外扩，框外多出来的部分只是取景参考（画面上
 *   仍画出框线）。v2 横向 400px/°、纵向 124px/°（压缩 3.2 倍），太阳只有真实
 *   角径的三分之一，「视场 3°」名不副实。
 * - 天体位置锚定窗口值：偏移 0 时严格等于窗口表里的 (方位, 高度)——读的就是后端
 *   解出来的同一个数；拖动滑块时叠加轨迹增量（方位先展开成连续序列再求差，
 *   跨 0/360 不会飞；窗口日期与 tracks 日期不一致也不会跑偏）。
 * - 云带按视线几何摆放：低/中/高云画在「视线与该层交点」所在的真实视高度角上
 *   （即天体高度），厚度由该层在交点距离处的上下界仰角换算；透明度取该层沿视线
 *   云量 occ，三层连乘后与后端 trans/vis_prob 同口径。v2 按画面高度 18/45/72%
 *   摆放、与天体高度无关，会把「挡在视线上的云」和「天上的云」画成同一件事。
 * - z 序正确：天空 → 天体 → 云 → 山脊/水面。因此山脊能挡住悬日（v2 把日面画在
 *   山脊之上，低头看山也显示太阳挂在山上）。
 * - 天空按该时刻真实太阳高度角推演（所以悬月也会是白天/暮色天空，而不是永远
 *   深夜），朝日方位暖光 + 反日方位「金星带」紫粉，AOD 起霾，夜深出星；云按
 *   地影判据分「受光/入影」两色（与后端 illumination 同一判据）。
 * - 画布按显示尺寸 × DPR 渲染（v2 把 1200×640 的位图拉伸到卡片宽度，文字发虚）。
 */

const PV_FOV_H = 6.0;        // 无 aov 时的默认水平视场（度）
const PV_SUN_R_DEG = 0.2665; // 太阳视半径（度）
const PV_BAND_H_M = {low: 1500, mid: 3200, high: 7600};   // 缺测时的代表层高
const PV_BAND_DH_M = {low: 400, mid: 900, high: 1400};    // 层厚（画带用）
const PV_TOP = 90;           // HUD（三行）占用高度
const PV_BOTTOM = 34;        // 方位标尺高度
const PV_RE = 6371.0088;     // 地球半径（km），与后端 sightline 同值

let _pvCtx = null;                 // {w, r}
let _pvCanvasId = 'celestialCanvas';
let _pvOff = 0;
let _pvLast = null;                // 最近一次渲染的几何摘要（回归检查用）

function renderCelestialPreview(w, r, tOffsetMin) {
  if (!w) return;
  _pvCtx = {w, r};
  _pvCanvasId = 'celestialCanvas';
  stopPvPlay();
  const slider = document.getElementById('pvSlider');
  if (slider) slider.value = String(tOffsetMin || 0);
  renderPlanitPreview(tOffsetMin || 0);
  bindPvSlider();
  pvBindResize();
}

// 无对准窗口时（方向天气卡）复用同一渲染器：把事件时刻当作"伪窗口"渲染到另一画布。
// 取景框中心 = 目标方位/仰角，天体按事件时刻真实位置绘制 —— 框外即说明拍不到。
/* 只改视场（用户换镜头）时立刻按新视场重画，不等后端把窗口重算回来——
   否则"改了焦距画面没反应"，要么以为没生效、要么以为又回到"画面乱动"。 */
function pvSetFov(fov) {
  if (!_pvCtx || !(fov > 0)) return;
  const r = _pvCtx.r || {};
  _pvCtx.r = Object.assign({}, r, {
    geometry: Object.assign({}, r.geometry || {}, {aov: fov}),
  });
  renderPlanitPreview(_pvOff || 0);
}

function renderEventPreview(canvasId, w, r) {
  if (!w) return;
  _pvCtx = {w, r};
  _pvCanvasId = canvasId || 'alignDirSky';
  renderPlanitPreview(0);
  pvBindResize();
}

function stopPvPlay() {
  const play = document.getElementById('pvPlay');
  if (play && play.__timer) {
    clearInterval(play.__timer);
    play.__timer = null;
    setPlayState(play, false);
  }
}

function bindPvSlider() {
  const slider = document.getElementById('pvSlider');
  const label = document.getElementById('pvLabel');
  const play = document.getElementById('pvPlay');
  if (!slider || slider.__pvBound) return;
  slider.__pvBound = true;
  const showLabel = () => {
    if (!label || !_pvCtx) return;
    const off = +slider.value;
    const base = (_pvCtx.w && _pvCtx.w.best_time) || '00:00';
    label.textContent = `${off > 0 ? '+' : ''}${off} min · ${minToHM(h2m(base) + off)}`;
  };
  slider.addEventListener('input', () => {
    stopPvPlay();
    if (!_pvCtx) return;
    showLabel();
    renderPlanitPreview(+slider.value);
  });
  if (play) {
    play.onclick = () => {
      if (play.__timer) { stopPvPlay(); return; }
      let v = +slider.value;
      if (v >= +slider.max) { v = +slider.min; slider.value = String(v); }
      setPlayState(play, true);
      play.__timer = setInterval(() => {
        v = Math.min(+slider.max, v + 2);
        slider.value = String(v);
        if (_pvCtx) {
          showLabel();
          renderPlanitPreview(v);
        }
        if (v >= +slider.max) stopPvPlay();
      }, 150);
    };
  }
  showLabel();
}

/* 窗口尺寸变化（换主题、切卡、转屏、缩放）后按新尺寸重画，避免位图被拉伸发虚 */
let _pvResizeBound = false;
function pvBindResize() {
  if (_pvResizeBound) return;
  _pvResizeBound = true;
  let t = null;
  window.addEventListener('resize', () => {
    clearTimeout(t);
    t = setTimeout(() => {
      if (_pvCtx && document.getElementById(_pvCanvasId)) {
        renderPlanitPreview(_pvOff);
      }
    }, 160);
  });
}

/* ══════════ 一、几何与插值 ══════════ */

function pvIsSun(w) {
  return w && (w.body ? w.body === 'sun' : w.type === '悬日');
}

function pvWrap180(x) {
  return ((x + 180) % 360 + 360) % 360 - 180;
}

/* Bennett 大气折射（度），与后端 report._refraction_deg 同式 */
function pvRefractionDeg(alt) {
  if (alt == null || alt < -1.0) return 0;
  const a = Math.min(89.9, Math.max(-1.0, alt));
  return 1.02 / Math.tan((a + 10.3 / (a + 5.11)) * Math.PI / 180) / 60;
}

/* 云层（海拔 hM、地表弧距 dKm）在机位（海拔 h0M）眼中的视高度角。
   与后端 sightline.sight_intersection_km 互为逆运算：把该式的解代回来即得
   天体视高度角，因此"交点"永远落在天体所在的仰角上。 */
function pvElevOfDist(hM, dKm, h0M) {
  const r = PV_RE + Math.max(0, Math.min(9000, h0M || 0)) / 1000;
  const rc = PV_RE + Math.max(0, Math.min(30000, hM || 0)) / 1000;
  const phi = Math.min(Math.PI / 2, (dKm || 0) / PV_RE);
  return Math.atan2(rc * Math.cos(phi) - r, rc * Math.sin(phi)) * 180 / Math.PI;
}

/* sightline.sight_intersection_km 的 JS 版：无视线评估时定云带距离 */
function pvSightIntersectKm(cloudHeightM, apparentAltDeg, observerHm) {
  if (!(cloudHeightM > 0) || cloudHeightM > 30000) return null;
  if (apparentAltDeg == null || !isFinite(apparentAltDeg)) return null;
  const r = PV_RE + Math.max(0, Math.min(9000, observerHm || 0)) / 1000;
  const shell = PV_RE + cloudHeightM / 1000;
  const alt = apparentAltDeg * Math.PI / 180;
  const b = r * Math.sin(alt);
  const disc = shell * shell - Math.pow(r * Math.cos(alt), 2);
  if (disc < 0) return null;
  const roots = [-b - Math.sqrt(disc), -b + Math.sqrt(disc)]
    .filter(t => t >= -1e-8);
  if (!roots.length) return null;
  const ray = Math.min.apply(null, roots);
  return PV_RE * Math.atan2(ray * Math.cos(alt), r + ray * Math.sin(alt));
}

/* 方位序列插值：先展开成连续序列（359.5 -> 0.2 记作 359.5 -> 360.2），
   再交给 interpTrkMin 线性插值；否则跨北的窗口会插出 180° 的假值。 */
function pvInterpAz(times, arr, minutes) {
  if (!times || !arr || !arr.length) return null;
  const un = [];
  let prev = null;
  for (let i = 0; i < arr.length; i++) {
    if (arr[i] == null) { un.push(null); continue; }
    let v = +arr[i];
    if (prev != null) {
      while (v - prev > 180) v -= 360;
      while (prev - v > 180) v += 360;
    }
    un.push(v);
    prev = v;
  }
  return interpTrkMin(times, un, minutes);
}

function pvBaseAz(w) {
  if (typeof w.best_az === 'number') return w.best_az;
  if (typeof w.az === 'number') return w.az;
  if (w.target_az != null) return +w.target_az;
  const el = document.getElementById('azInput');
  return (el && +el.value) || 270;
}

/* 天体位置：base = 窗口表里的权威值；偏移时叠加轨迹增量。
   用增量而不是绝对轨迹，既保证与表格一致，也不受 tracks 日期/近似程度影响。 */
function pvBodyPos(w, r, offMin) {
  const baseAlt = (typeof w.best_alt === 'number') ? w.best_alt
    : (typeof w.alt === 'number') ? w.alt
    : (typeof w.frame_alt === 'number') ? w.frame_alt : 0;
  const baseAz = pvBaseAz(w);
  const pos = {alt: baseAlt, az: baseAz, baseAlt: baseAz === null ? 0 : baseAlt,
               baseAz};
  if (!offMin) return pos;
  const isSun = pvIsSun(w);
  const trk = r.tracks || {};
  const altArr = isSun ? trk.sun_alt : trk.moon_alt;
  const azArr = isSun ? trk.sun_az : trk.moon_az;
  const t0 = h2m((w.best_time || '00:00').slice(0, 5));
  const t1 = t0 + offMin;
  const a0 = interpTrkMin(trk.time, altArr, t0);
  const a1 = interpTrkMin(trk.time, altArr, t1);
  if (a0 != null && a1 != null) pos.alt = baseAlt + (a1 - a0);
  else if (typeof w.alt_rate === 'number') pos.alt = baseAlt + w.alt_rate * offMin;
  const z0 = pvInterpAz(trk.time, azArr, t0);
  const z1 = pvInterpAz(trk.time, azArr, t1);
  if (z0 != null && z1 != null) pos.az = baseAz + pvWrap180(z1 - z0);
  else if (typeof w.az_rate === 'number') pos.az = baseAz + w.az_rate * offMin;
  return pos;
}

/* 该时刻的太阳高度角（决定天空颜色；悬月也要用白天的天空） */
function pvSunAltAt(r, w, offMin, isSun, bodyAlt) {
  if (isSun) return bodyAlt;
  const trk = r.tracks || {};
  const t = h2m((w.best_time || '00:00').slice(0, 5)) + (offMin || 0);
  const v = interpTrkMin(trk.time, trk.sun_alt, t);
  if (v != null) return v;
  if (w.sky && w.sky.sun_alt != null) return w.sky.sun_alt;
  return 12;
}

/* 山脊遮蔽角：site_info.horizon_mask 方位插值（16 方位） */
function horizonRidgeAlt(r, az) {
  const si = r.site_info;
  if (!si || !si.horizon_mask || si.horizon_mask.length !== 16) return 0;
  return horizonAltAtMask(si.horizon_mask, az);
}

function horizonAltAtMask(mask, az) {
  az = ((az % 360) + 360) % 360;
  const i0 = Math.floor(az / 22.5) % 16, i1 = (i0 + 1) % 16;
  const f = (az - i0 * 22.5) / 22.5;
  return mask[i0] * (1 - f) + mask[i1] * f;
}

/* 云是否还受光（地影判据，与后端 illumination / sky.js litBand 同式）：
   云高 h、朝日方向水平距离 d 的云，受光 ⟺ 太阳高度角 > −(b(h) + d/R)。 */
function pvCloudLit(sunAlt, hM, dKm, h0M) {
  if (sunAlt == null) return 0.5;
  const dh = Math.max(0.001, (hM - (h0M || 0)) / 1000);
  const crit = Math.acos(PV_RE / (PV_RE + dh)) * 180 / Math.PI;
  const rail = -(crit + (dKm || 0) / PV_RE * 180 / Math.PI);
  return clamp01((sunAlt - rail) / 0.6 + 0.5);
}

function pvRefHeight(r, band) {
  const bs = ((r.sight_points || {}).bands) || [];
  const hit = bs.filter(b => b && b.band === band)[0];
  return (hit && hit.height_m) || PV_BAND_H_M[band];
}

/* 视线云带模型：位置=天体仰角（视线与该层的交点必然在天体仰角上），
   厚度=该层在交点距离处的上下界仰角差，透明度=该层沿视线云量。 */
function pvCloudBands(w, r, tKey, altC, h0M, sunAlt, fovV) {
  const out = [];
  const sight = w.sight || null;
  const bands = (sight && sight.bands) || null;
  const hc = (r.hourly_clouds_by_date || {})[w.date] || r.hourly_clouds || {};
  const baseKey = key5(w.best_time || '00:00');
  const camAt = (b, k) => interpCloud(hc.time, hc[b], k);
  for (const band of ['low', 'mid', 'high']) {
    const sb = bands && bands[band];
    const hM = (sb && sb.gh_m) ? +sb.gh_m : pvRefHeight(r, band);
    let dKm = (sb && sb.d_km != null) ? +sb.d_km : null;
    let occ = null, src = 'est';
    if (sb && sb.occ != null) {
      occ = +sb.occ;
      src = 'sight';
    } else {
      const cAt = camAt(band, tKey);
      const cWin = (w.cloud_at || {})[band];
      occ = cAt != null ? cAt : (cWin != null ? +cWin : 0);
    }
    // 时间轴上的变化：按机位逐时云量相对窗口时刻的比例缩放（cloud_locked 不动）
    if (occ != null && !w.cloud_locked && hc.time && hc.time.length
        && tKey !== baseKey) {
      const c0 = camAt(band, baseKey), c1 = camAt(band, tKey);
      if (c0 != null && c1 != null && c0 > 1) {
        occ = occ * Math.max(0.25, Math.min(2, c1 / c0));
      }
    }
    occ = Math.max(0, Math.min(100, occ == null ? 0 : occ));
    if (dKm == null || !(dKm > 0)) {
      dKm = pvSightIntersectKm(hM, altC, h0M);
    }
    let thick = 0.42;
    if (dKm && dKm > 0) {
      const top = pvElevOfDist(hM + PV_BAND_DH_M[band], dKm, h0M);
      const bot = pvElevOfDist(Math.max((h0M || 0) + 30, hM - PV_BAND_DH_M[band]),
                               dKm, h0M);
      thick = Math.min(2.2, Math.max(0.30, Math.abs(top - bot)));
    }
    // 层的"代表厚度"是个约定值，别让它把整个取景框糊满（小视场时会出现）
    thick = Math.min(thick, Math.max(0.34, 0.6 * fovV));
    out.push({band, occ, hM, dKm, thick, lit: pvCloudLit(sunAlt, hM, dKm, h0M),
              src});
  }
  return out;
}

/* ══════════ 二、画布环境（DPR + 小屏自适应） ══════════ */

function pvCanvasEnv(cv) {
  if (!cv.__pvDesign) cv.__pvDesign = [cv.width, cv.height];
  const DW = cv.__pvDesign[0], DH = cv.__pvDesign[1];
  const cssW = cv.clientWidth;
  const ctx = cv.getContext('2d');
  if (!cssW) {                       // 卡片隐藏/尚未布局：按设计尺寸直出
    if (cv.width !== DW || cv.height !== DH) {
      cv.width = DW; cv.height = DH;
    }
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    return {ctx, W: DW, H: DH, s: 1, dense: true};
  }
  const dpr = Math.min(2, window.devicePixelRatio || 1);
  const bw = Math.round(cssW * dpr);
  const bh = Math.round(bw * DH / DW);
  if (cv.width !== bw || cv.height !== bh) {
    cv.width = bw; cv.height = bh;
  }
  cv.style.height = (cssW * DH / DW) + 'px';
  const s = bw / DW;
  ctx.setTransform(s, 0, 0, s, 0, 0);
  return {ctx, W: DW, H: DH, s, dense: s >= 0.62};
}

/* ══════════ 三、主渲染 ══════════ */

function renderPlanitPreview(tOffsetMin) {
  const cv = document.getElementById(_pvCanvasId || 'celestialCanvas');
  if (!cv || !_pvCtx) return;
  _pvOff = tOffsetMin || 0;
  const {w, r} = _pvCtx;
  const {ctx, W, H, dense} = pvCanvasEnv(cv);
  const isSun = pvIsSun(w);
  const off = _pvOff;
  const aod = (r.main && r.main.aod_local) != null ? +r.main.aod_local : 0.3;

  // ---- 天体位置（锚定窗口值 + 轨迹增量）----
  const pos = pvBodyPos(w, r, off);
  const alt = pos.alt;
  const azDisp = ((pos.az % 360) + 360) % 360;
  const sunAlt = pvSunAltAt(r, w, off, isSun, alt);
  const tKey = minToHM(h2m((w.best_time || '00:00').slice(0, 5)) + off);

  // ---- 相机画面 = 整块画布：视场由焦距换算（全画幅等效，按 36mm 画幅宽）。
  // 画布即取景框，只有一个尺度、横竖严格等比，拖滑块不改视野。
  const azC = (typeof w.frame_az === 'number') ? w.frame_az : pos.baseAz;
  const fovH = Math.max(0.4, Math.min(60,
    (r.geometry && +r.geometry.aov) || PV_FOV_H));
  const focalMm = pvFocalFromFov(fovH);
  const fovV = fovH * H / W;                 // 画幅比例 = 画布比例
  const px = W / fovH;                       // 等比 px/°，画布即取景框时只有这一个尺度
  const h0M = +(((r.observer || {}).height_m) != null
    ? r.observer.height_m : 0) || 0;
  const ridge = pvRidgeMax(r, azC, fovH);
  const dip = +(((r.observer || {}).horizon_dip_deg) || 0);
  const groundTop = Math.max(ridge, -dip);   // 地面线：山脊顶（高于海平线时）或海平线

  // ---- 垂直构图：地面尽量落在画面下方 1/3（地面线在 2/3 高度处），
  //      但绝不牺牲天体：日/月盘（含半径）既不能被顶部 HUD 压住，也不能压到底部
  //      刻度；地面本来就不在附近（高日 / 无地面）时画面中心仍取窗口最佳高度。
  //      焦距越短、天体越低，地面自然占得越多；装不下时如实由状态行说明地平线
  //      在画面下方多少度 ----
  const baseAlt = (typeof w.frame_alt === 'number') ? w.frame_alt : pos.baseAlt;
  const bodyR0 = pvBodyRadiusDeg(w, isSun);
  const bodyPx0 = bodyR0 * px;
  let altC = baseAlt;
  const groundNear = Math.abs(groundTop - baseAlt) <= 1.35 * fovV + ridge;
  if (groundNear) {
    const pref = groundTop + fovV / 6;     // 地面线落在 2/3 高度 => 中心在其上 fovV/6
    // y = H/2 - (baseAlt - altC)*px，altC 越大天体在画面里越靠下
    const altMin = baseAlt + (PV_TOP + bodyPx0 + 6 - H / 2) / px;
    const altMax = baseAlt + (H - PV_BOTTOM - bodyPx0 - 6 - H / 2) / px;
    altC = (altMin <= altMax)
      ? Math.min(altMax, Math.max(altMin, pref)) : baseAlt;
  }
  const xOfAz = a => W / 2 + pvWrap180(a - azC) * px;   // 跨北不跳到画面外
  const yOfAlt = a => H / 2 - (a - altC) * px;
  const horizonY = yOfAlt(0);
  const frameRect = [0, 0, W, H];
  const inFrame = Math.abs(pvWrap180(azDisp - azC)) <= fovH / 2 + 1e-6
    && alt <= altC + fovV / 2 + 1e-6 && alt >= altC - fovV / 2 - 1e-6;
  const bodyAng = pvBodyAngSize(w, isSun);   // 天体视直径（度）
  const bodyPct = bodyAng / fovH * 100;      // 占画面宽的比例（= 照片里的真实大小）

  // ---- 云带（视线口径）----
  // 云带几何按「对准时刻天体所在的视高度角」定（视线与该层的交点本来就在那个
  // 方向），与画面中心解耦：垂直构图下移时云不会跟着挪。
  const clouds = pvCloudBands(w, r, tKey, baseAlt, h0M, sunAlt, fovV);
  const cSum = {low: 0, mid: 0, high: 0};
  let cloudBasis = '无';
  clouds.forEach(b => {
    cSum[b.band] = b.occ;
    if (b.occ >= 4) cloudBasis = (b.src === 'sight' ? '视线' : '机位');
  });
  // 云图锚点固定在「对准时刻天体所在处」，与滑块无关 —— 否则云会跟着天体跑
  const anchorX = xOfAz(pvBodyPos(w, r, 0).az);

  // ---- 可见度（后端权威值优先，避免与表格两个数字打架）----
  const cover = cSum.low + 0.45 * cSum.mid + 0.2 * cSum.high;
  let vis = w.vis_prob != null ? Math.round(w.vis_prob)
    : Math.max(0, Math.round(100 - cover * 1.05));
  const sight = w.sight || null;
  const bodyHidden = !!(sight && sight.visible === false);
  // 日/月面是否被地平线或山脊挡到（用于低透明度"底片"，让人知道它在哪儿）
  const occulted = bodyHidden
    || (alt - bodyR0 < -dip) || (ridge > 0.15 && alt < ridge);

  // ---- 天体在画面内的停留（拖动滑块的直觉来源）----
  let exitMin = null;
  for (let m = 1; m <= 30; m++) {
    const p = pvBodyPos(w, r, off + m);
    if (Math.abs(pvWrap180(p.az - azC)) > fovH / 2
        || p.alt > altC + fovV / 2 || p.alt < altC - fovV / 2) {
      exitMin = m; break;
    }
  }
  // 状态行：谁挡住了 / 还在不在画面里 / 还能待多久
  let note = '';
  if (bodyHidden) {
    vis = 0;
    note = '天体在地平线下（上缘也未露出），本时刻拍不到日/月面；'
      + '画面里用半透明圆盘标了它的位置';
  } else if (ridge > 0.15 && alt < ridge) {
    note = `该方位山脊 ${ridge.toFixed(1)}° 高于天体 ${alt.toFixed(2)}°，`
      + '日/月面被地形挡住（半透明圆盘是它的实际位置）';
  } else if (!inFrame) {
    const dAz = pvWrap180(azDisp - azC), dAlt = alt - altC;
    note = `天体在画面外（方位差 ${dAz > 0 ? '+' : ''}${dAz.toFixed(1)}°，`
      + `高度差 ${dAlt > 0 ? '+' : ''}${dAlt.toFixed(1)}°）`;
  } else if (occulted) {
    note = `日/月面下缘在地平线下 ${(-dip - (alt - bodyR0)).toFixed(2)}°，`
      + '仍在水面之下（半透明部分为地平线以下）';
  } else if (exitMin != null) {
    note = `天体约 +${exitMin} 分钟后移出画面`;
  } else {
    note = '该时刻天体在画面内';
  }

  // ================= 绘制 =================
  ctx.clearRect(0, 0, W, H);
  const bodyR = bodyR0 * px;
  const bx = xOfAz(azDisp), by = yOfAlt(alt);
  const altOfY = y => altC - (y - H / 2) / px;
  pvDrawSky(ctx, W, H, horizonY, sunAlt, xOfAz(pvSunAzC(r, w, off, isSun, pos)),
            altOfY);
  if (isSun) pvDrawStars(ctx, W, H, sunAlt,
                         hashStr(((r.meta || {}).date || '') + 'pv'),
                         bx, by, bodyR);
  // 轨迹弧 → 天体 → 霾（霾在天体之下、云之上，与后端 AOD 口径一致）
  pvDrawPathArc(ctx, w, r, off, frameRect, xOfAz, yOfAlt);
  if (!bodyHidden && inFrame) {
    pvDrawBody(ctx, bx, by, bodyR, isSun, aod, w.illum, sunAlt, alt, px,
               w, r, azDisp);
  }
  pvDrawHaze(ctx, W, H, aod, horizonY, sunAlt, false);

  // 云带（画在天体之上：视线上的云本来就该遮住它；远的先画、近的后画）。
  // 位置锚定在**对准时刻天体所在处**，拖滑块时云不动、只有天体在云后移动。
  pvDrawClouds(ctx, W, H, clouds, yOfAlt(baseAlt), px, sunAlt, dense, anchorX,
               bodyR);

  // 水面在地理水平面下沉 dip 处（机位越高下沉越多），山脊画在水面之后
  const seaY = yOfAlt(-dip);
  const horizonSeen = seaY > -0.15 * H && seaY < 1.35 * H;
  if (horizonSeen) pvDrawWater(ctx, W, H, seaY, sunAlt, isSun, bx, alt);
  const ridgePath = pvDrawRidge(ctx, W, H, r, azC, fovH, xOfAz, yOfAlt, seaY,
                                dense);

  // 被地平线/山脊挡住的日/月面：在同一位置叠一层低透明度"底片"（只画在地面
  // 区域里，用山脊轮廓裁剪）——照片里当然拍不到，但规划时要看得见它在哪儿。
  if (!bodyHidden && inFrame && occulted) {
    ctx.save();
    if (ridgePath) ctx.clip(ridgePath);
    ctx.globalAlpha = 0.3;
    pvDrawBody(ctx, bx, by, bodyR, isSun, aod, w.illum, sunAlt, alt, px,
               w, r, azDisp, true);
    ctx.restore();
  }

  // 画面外的天体：贴边三角箭头 + 差多少（它只是"差多少"的指示，画在最前）
  const dAz = pvWrap180(azDisp - azC), dAlt = alt - altC;
  if (!bodyHidden && !inFrame) {
    pvDrawEdgeMark(ctx, W, H, dAz * px, -dAlt * px, isSun,
                   `${dAz > 0 ? '→' : '←'} ${Math.abs(dAz).toFixed(1)}°`
                   + ` / ${dAlt > 0 ? '↑' : '↓'} ${Math.abs(dAlt).toFixed(1)}°`);
  }

  // 框外压暗已不需要（画布就是取景框），但顶部/底部压暗保留给 HUD 与刻度
  pvDrawHud(ctx, W, H, w, r, isSun, alt, azDisp, sunAlt, aod, fovH, fovV,
            vis, cloudBasis, cSum, note, exitMin, inFrame, dense,
            ridge > 0.15 && alt < ridge, focalMm, bodyPct, dip, altC,
            Math.max(-0.6, ridge));
  pvDrawRulers(ctx, W, H, xOfAz, yOfAlt, azC, altC, px, dense);

  _pvLast = {fovH, fovV, px, focalMm, bodyPct, frame: frameRect.slice(),
             alt, az: azDisp, altC, azC, vis,
             pxPerDegX: Math.abs(xOfAz(azC + 1) - xOfAz(azC)),
             pxPerDegY: Math.abs(yOfAlt(altC + 1) - yOfAlt(altC)),
             sunAlt, inFrame, bodyHidden, exitMin, note, ridge, dip,
             anchorX, occulted, groundTop,
             groundY: Math.max(-0.6, ridge) > -90
               ? yOfAlt(Math.max(-0.6, ridge)) : null,
             clouds: clouds.map(c => ({band: c.band, occ: Math.round(c.occ),
                                       dKm: c.dKm, thick: c.thick,
                                       lit: c.lit, src: c.src}))};
}

/* ══════════ 四、天空与天体 ══════════ */

/* 朝日方位（用于暖光带的横向位置；悬月时也需要） */
function pvSunAzC(r, w, off, isSun, pos) {
  if (isSun) return pos.az;
  const trk = r.tracks || {};
  const t = h2m((w.best_time || '00:00').slice(0, 5)) + (off || 0);
  const v = pvInterpAz(trk.time, trk.sun_az, t);
  if (v != null) return v;
  if (w.sky && w.sky.sun_az != null) return w.sky.sun_az;
  return pos.az + 180;
}

function pvDrawSky(ctx, W, H, horizonY, sunAlt, sunX, altOfY) {
  // 暮光曲线：太阳 0° 附近起暖光，-6° 之后转夜（经验式，与 sky.js 同族）
  const dayF = clamp01((sunAlt + 1) / 8);
  const glowF = clamp01(1 - Math.abs(sunAlt + 1.5) / 12);  // 峰值 ~-1.5°，±12° 内起暖
  // 场景是「照片」，配色不随界面深浅主题变（否则同一个窗口在深浅模式下
  // 会渲染成两种天色，与数据无关）。
  const zenith = mixHex('#0a1120', '#3f6fb0', dayF);
  const midSky = mixHex('#131f36', '#7aa6d8', dayF);
  const duskBase = '#3a2b34';
  const horizonDay = mixHex(duskBase, '#d6e5f4', dayF);
  const horizonCol = mixHex(horizonDay, '#ff9a3c', Math.min(1, glowF));
  const bot = Math.max(32, Math.min(H, horizonY));
  // 按真实高度角取色：暮光梯度集中在近地平线十来度内，所以只拍地平线附近几度时
  // 整幅都该是暖色。若像 v2 那样把"天顶→地平线"硬铺满画布，2.4° 的日落会渲成
  // 深夜色（画面上半部离天顶还有几十度，却拿到了天顶色）。
  const g = ctx.createLinearGradient(0, 0, 0, bot);
  const N = 10;
  for (let i = 0; i <= N; i++) {
    const a = altOfY(bot * i / N);
    const w = Math.pow(clamp01(1 - Math.max(0, a) / 11), 0.75);
    g.addColorStop(i / N, mixHex(zenith, horizonCol, w));
  }
  ctx.fillStyle = g;
  ctx.fillRect(0, 0, W, bot);

  if (glowF > 0.03) {
    // 朝日方位暖光（贴地平线最亮）+ 反日方位"金星带"紫粉
    const rw = W * (0.16 + 0.26 * glowF);
    const sunY = Math.min(H, Math.max(0, pvAltToY(sunAlt, altOfY)));
    const rg = ctx.createRadialGradient(sunX, sunY, 0, sunX, sunY, rw);
    rg.addColorStop(0, hexA('#ffb055', 0.5 * glowF));
    rg.addColorStop(0.3, hexA('#f07a3c', 0.26 * glowF));
    rg.addColorStop(0.65, hexA('#e8543a', 0.1 * glowF));
    rg.addColorStop(1, 'rgba(232,84,58,0)');
    ctx.globalCompositeOperation = 'lighter';
    ctx.fillStyle = rg;
    ctx.fillRect(0, 0, W, bot);
    const ax = W - sunX;
    const ag = ctx.createRadialGradient(ax, bot, 0, ax, bot, rw * 1.1);
    ag.addColorStop(0, hexA('#c98ab0', 0.34 * glowF));
    ag.addColorStop(1, 'rgba(201,138,176,0)');
    ctx.fillStyle = ag;
    ctx.fillRect(0, 0, W, bot);
    ctx.globalCompositeOperation = 'source-over';
    // 贴地平线的一层薄亮带
    const bh = 0.13 * (bot - PV_TOP);
    const hg = ctx.createLinearGradient(0, bot - bh, 0, bot);
    hg.addColorStop(0, 'rgba(255,170,90,0)');
    hg.addColorStop(1, hexA('#ffc478', 0.38 * glowF));
    ctx.fillStyle = hg;
    ctx.fillRect(0, bot - bh, W, bh);
  }
}

/* 由 y 反查高度角：给天空渐变用（梯度按真实高度角取色，而非按画布比例） */
function pvAltToY(alt, altOfY) {
  // 二分反解（映射单调）
  let lo = -90, hi = 90;
  for (let i = 0; i < 40; i++) {
    const mid = (lo + hi) / 2;
    if (altOfY(mid) > alt) lo = mid; else hi = mid;
  }
  return altOfY((lo + hi) / 2);
}

function pvDrawStars(ctx, W, H, sunAlt, seed, moonX, moonY, R) {
  const nightF = clamp01((-sunAlt - 4) / 8);
  if (nightF <= 0.15) return;
  const rand = mulberry(seed || 7);
  const n = Math.round(160 * nightF);
  const keep = Math.max(40, (R || 10) * 7);
  for (let i = 0; i < n; i++) {
    const x = rand() * W;
    const y = rand() * H * 0.94;
    const rr = 0.6 + rand() * 1.4;
    // 月亮附近的星星被月光压住，不画（否则月亮周围一圈假星）
    if (moonX != null
        && Math.abs(x - moonX) < keep && Math.abs(y - moonY) < keep) continue;
    ctx.fillStyle = `rgba(255,255,242,${((0.22 + 0.6 * rand()) * nightF).toFixed(2)})`;
    ctx.beginPath();
    ctx.arc(x, y, rr, 0, Math.PI * 2);
    ctx.fill();
  }
}

/* AOD 霾：thin=true 时叠一层轻的（盖住云，但不把云压成一片灰） */
function pvDrawHaze(ctx, W, H, aod, horizonY, sunAlt, thin) {
  if (!(aod > 0.2)) return;
  const alpha = Math.min(0.42, (aod - 0.2) * 0.62) * (thin ? 0.4 : 1);
  if (alpha <= 0.01) return;
  const bot = Math.max(32, Math.min(H, horizonY));
  const g = ctx.createLinearGradient(0, 0, 0, bot);
  const col = sunAlt < -4 ? '40,46,60' : '206,201,191';
  g.addColorStop(0, `rgba(${col},${(alpha * 0.55).toFixed(2)})`);
  g.addColorStop(1, `rgba(${col},${alpha.toFixed(2)})`);
  ctx.fillStyle = g;
  ctx.fillRect(0, 0, W, bot);
}

/* 天体视半径（度）：太阳 0.2665，月亮用后端给的角径（0.49~0.56 随距离变） */
function pvBodyRadiusDeg(w, isSun) {
  if (isSun) return PV_SUN_R_DEG;
  const ang = +w.angular_size_deg;
  return (ang > 0.2 && ang < 1.2) ? ang / 2 : 0.26;
}

/* 焦距 ↔ 视场角（全画幅等效：按 36mm 画幅宽换算）
   f = 18 / tan(fovH/2)：2.455° → 840mm、3° → 687mm、10° → 206mm */
function pvFocalFromFov(fovH) {
  return Math.round(18 / Math.tan(Math.max(0.05, fovH) * Math.PI / 360));
}
function pvFovFromFocal(f) {
  return 2 * Math.atan(18 / Math.max(1, f)) * 180 / Math.PI;
}

/* 天体视直径（度）：太阳 0.533；月亮用后端角径（0.49~0.56 随距离变） */
function pvBodyAngSize(w, isSun) {
  if (isSun) return PV_SUN_R_DEG * 2;
  const ang = +w.angular_size_deg;
  return (ang > 0.2 && ang < 1.2) ? ang : 0.52;
}

/* ±30 分钟轨迹弧：画在取景框内，让"会怎么动、多久出框"看得见 */
function pvDrawPathArc(ctx, w, r, off, rect, xOfAz, yOfAlt) {
  const pts = [];
  for (let m = -30; m <= 30; m += 2) {
    const p = pvBodyPos(w, r, off + m);
    pts.push([xOfAz(p.az), yOfAlt(p.alt)]);
  }
  ctx.save();
  ctx.beginPath();
  ctx.rect(rect[0], rect[1], rect[2], rect[3]);
  ctx.clip();
  ctx.strokeStyle = 'rgba(255,255,255,.45)';
  ctx.lineWidth = 1.6;
  ctx.setLineDash([5, 6]);
  ctx.beginPath();
  pts.forEach((p, i) => i ? ctx.lineTo(p[0], p[1]) : ctx.moveTo(p[0], p[1]));
  ctx.stroke();
  ctx.setLineDash([]);
  ctx.restore();
}

/* 月亮亮面朝向（屏幕上从月心指向太阳的方向，0°=右 90°=上）。
   用两个天体的单位矢量在"月心切平面"上做投影：只看方位差会在太阳与月亮
   近乎相对（满月附近）时给出左右颠倒的假方向，投影法不会。 */
function pvLitAngleDeg(mAlt, mAz, sAlt, sAz) {
  const d2r = Math.PI / 180;
  const M = [Math.cos(mAlt * d2r) * Math.sin(mAz * d2r),
             Math.cos(mAlt * d2r) * Math.cos(mAz * d2r), Math.sin(mAlt * d2r)];
  const S = [Math.cos(sAlt * d2r) * Math.sin(sAz * d2r),
             Math.cos(sAlt * d2r) * Math.cos(sAz * d2r), Math.sin(sAlt * d2r)];
  const R = [Math.cos(mAz * d2r), -Math.sin(mAz * d2r), 0];   // 屏幕右（方位增大）
  const U = [R[1] * M[2] - R[2] * M[1], R[2] * M[0] - R[0] * M[2],
             R[0] * M[1] - R[1] * M[0]];                      // 屏幕上
  const dx = S[0] * R[0] + S[1] * R[1] + S[2] * R[2];
  const dy = S[0] * U[0] + S[1] * U[1] + S[2] * U[2];
  if (!isFinite(dx) || !isFinite(dy) || (dx === 0 && dy === 0)) return null;
  return Math.atan2(dy, dx) * 180 / Math.PI;
}

/* 天体本体 */
function pvDrawBody(ctx, cx, cy, R, isSun, aod, illum, sunAlt, alt, px,
                    w, r, azDisp, ghost) {
  R = Math.max(3.5, R);
  if (!isSun) {
    // 月亮：夜里的银白光晕，白天几乎没有光晕
    const day = clamp01(sunAlt / 12);
    const halo = 0.45 * (1 - day * 0.75);
    if (halo > 0.02) {
      const hg = ctx.createRadialGradient(cx, cy, R * 0.6, cx, cy, R * 4.5);
      hg.addColorStop(0, `rgba(206,216,236,${halo.toFixed(2)})`);
      hg.addColorStop(1, 'rgba(206,216,236,0)');
      ctx.fillStyle = hg;
      ctx.beginPath();
      ctx.arc(cx, cy, R * 4.5, 0, Math.PI * 2);
      ctx.fill();
    }
    // 亮面朝向太阳；拿不到太阳位置时退回盈亏方向（waxing=true 亮面在右）
    const sunAz = pvSunAzC(r, w, _pvOff, false, {az: azDisp});
    const ang = (sunAz == null || sunAlt == null) ? null
      : pvLitAngleDeg(alt, azDisp, sunAlt, sunAz);
    const litRight = (ang == null) ? (w.moon_waxing !== false) : true;
    const alpha = Math.max(0.18, 0.96 * (1 - clamp01(aod - 0.2) * 0.25));
    drawMoonPhase(ctx, cx, cy, R, illum, litRight, alpha, ang);
    return;
  }
  // 太阳：核心色由「高度角（低空消光）」与 AOD 共同决定——通透高日是白亮盘面，
  // 低日偏黄橙，污烧收成暗橙；通透时才带芒。
  const clear = clamp01(1 - Math.max(0, aod - 0.15) / 0.55);
  const warmF = Math.pow(clamp01(1 - Math.max(0, alt) / 15), 1.2);   // 越低越暖
  const aodF = clamp01((aod - 0.12) / 0.5);
  const warmth = clamp01(Math.max(warmF * 0.9, aodF));
  const core1 = mixHex('#ffffff', '#ffe2ae', warmth * 0.6);
  const core2 = mixHex('#fff6d8', '#ef7a35', warmth * 0.85);
  const halo = warmth > 0.5 ? '255,205,130' : '255,238,200';
  // 被云挡得越多，日面的芒与晕越弱（可见度 11% 还挂着满圈亮芒会自相矛盾）
  const veil = (w.sight && w.sight.trans != null) ? clamp01(w.sight.trans)
    : (w.vis_prob != null ? clamp01(w.vis_prob / 100) : 1);
  const showF = 0.3 + 0.7 * veil;
  const g0 = Math.max(0.06, 0.5 * clear * (0.4 + 0.6 * warmth) * showF);
  const hg = ctx.createRadialGradient(cx, cy, R * 0.3, cx, cy, R * (4 + 5 * clear));
  hg.addColorStop(0, `rgba(${halo},${g0.toFixed(2)})`);
  hg.addColorStop(0.35, `rgba(${halo},${(g0 * 0.35).toFixed(2)})`);
  hg.addColorStop(1, `rgba(${halo},0)`);
  ctx.fillStyle = hg;
  ctx.beginPath();
  ctx.arc(cx, cy, R * (4 + 5 * clear), 0, Math.PI * 2);
  ctx.fill();
  if (!ghost && clear > 0.35 && alt > -0.6) {   // 通透低日才有芒（底片不画）
    const rayLen = R * (1.15 + 1.7 * clear) * (0.45 + 0.55 * warmth);
    ctx.fillStyle = `rgba(${halo},${(0.34 * clear * showF).toFixed(2)})`;
    for (let i = 0; i < 16; i++) {
      const ang = i * Math.PI / 8 + Math.PI / 16;
      const len = (i % 2 === 0 ? rayLen : rayLen * 0.42)
        * (0.85 + 0.3 * Math.sin(i * 2.7));
      if (len <= R * 1.15) continue;
      const wTip = Math.max(1, R * 0.1);
      ctx.beginPath();
      ctx.moveTo(cx + Math.cos(ang) * len, cy + Math.sin(ang) * len);
      ctx.lineTo(cx + Math.cos(ang + Math.PI / 2) * wTip,
                 cy + Math.sin(ang + Math.PI / 2) * wTip);
      ctx.lineTo(cx + Math.cos(ang - Math.PI / 2) * wTip,
                 cy + Math.sin(ang - Math.PI / 2) * wTip);
      ctx.closePath();
      ctx.fill();
    }
  }
  // 日面：中心亮、边缘暗（低空大气消光），再叠一层小晕
  const dg = ctx.createRadialGradient(cx, cy, R * 0.1, cx, cy, R);
  dg.addColorStop(0, core1);
  dg.addColorStop(0.62, core2);
  dg.addColorStop(1, mixHex(core2, '#c8452a', 0.5 * warmth));
  ctx.fillStyle = dg;
  ctx.beginPath();
  ctx.arc(cx, cy, R, 0, Math.PI * 2);
  ctx.fill();
}

/* 画面外的天体：在画面边缘画一个朝外的三角箭头 + 差多少。
   不再画"半透明日面"——画面即照片，照片里没有的东西不该出现在照片里。 */
function pvDrawEdgeMark(ctx, W, H, dxPx, dyPx, isSun, label) {
  if (Math.abs(dxPx) < 1e-6 && Math.abs(dyPx) < 1e-6) return;
  // 上下左右各留出 HUD/刻度的位置，箭头与说明文字不要压到它们
  const mX = 34, mTop = 108, mBot = 56;
  const tx = Math.abs(dxPx) > 1e-6 ? (W / 2 - mX) / Math.abs(dxPx) : Infinity;
  const tyUp = dyPx < 0 ? (H / 2 - mTop) / Math.abs(dyPx) : Infinity;
  const tyDn = dyPx > 0 ? (H / 2 - mBot) / Math.abs(dyPx) : Infinity;
  const t = Math.min(tx, tyUp, tyDn);
  const x = W / 2 + dxPx * t, y = H / 2 + dyPx * t;
  const ang = Math.atan2(dyPx, dxPx);
  const col = isSun ? '#ffd08a' : '#dfe6f2';
  ctx.save();
  ctx.translate(x, y);
  ctx.rotate(ang);
  ctx.fillStyle = col;
  ctx.beginPath();
  ctx.moveTo(16, 0);
  ctx.lineTo(-8, 11);
  ctx.lineTo(-8, -11);
  ctx.closePath();
  ctx.fill();
  ctx.restore();
  ctx.font = '600 20px "PingFang SC",sans-serif';
  ctx.textAlign = 'center';
  ctx.lineWidth = 3.5;
  ctx.strokeStyle = 'rgba(0,0,0,.55)';
  const ly = Math.min(H - 40, Math.max(PV_TOP + 10,
                                      y + (dyPx < 0 ? 36 : -28)));
  const half = ctx.measureText(label).width / 2 + 10;
  const lx = Math.min(W - half, Math.max(half, x));   // 贴边时别把字切掉
  ctx.strokeText(label, lx, ly);
  ctx.fillStyle = '#ff8b7a';
  ctx.fillText(label, lx, ly);
}

/* 云带：云团轮廓（同一个 path 一次填充 → 内部无缝）+ 受光边/暗底两色。
   横向留缝按 (1-occ) 打折、并把 alpha 反向补偿，使整条带的平均透射率恰好等于
   后端该层透射率 1-0.9·occ/100 —— 所以"云缝里露出的日面"与可见度数字同源。
   缝/云团的位置按最大似然摆：occ<50 时把视线方向放进缝里（最可能是晴的），
   occ>50 时让云盖住视线（最可能被挡）—— 否则随机摆放会出现"20% 薄云把太阳
   糊成黑球"这种既不直观也不可信的画面。 */
function pvDrawClouds(ctx, W, H, bands, yC, px, sunAlt, dense, bodyX, bodyR) {
  const labels = [];
  const ordered = bands.slice().sort((a, b) => (b.dKm || 0) - (a.dKm || 0));
  for (const b of ordered) {
    if (b.occ < 4) continue;
    const hPx = Math.max(9, b.thick * px);
    const top = yC - hPx / 2, bot = yC + hPx / 2;
    if (bot < 0 || top > H) continue;
    const lit = b.lit;
    const s = clamp01(b.occ / 100);
    const dayHi = clamp01((sunAlt - 3) / 12);
    // 薄云透光、厚云压暗：逆光云的亮度随该层云量下降（82% 低云该是暗剪影，
    // 20% 高云该是发亮的薄纱），受光面用暖色，入影面用冷灰。
    const warmThin = mixHex('#ffd9a0', '#f4f8ff', dayHi);
    const warmThick = mixHex('#4a3b30', '#5c6675', dayHi);
    const warm = mixHex(warmThin, warmThick, Math.pow(s, 0.85));
    const cold = '#2a3444';
    const near = mixHex(cold, warm, lit * 0.9 + 0.05);
    const far = mixHex(cold, warm, lit * 0.7 + 0.04);
    const rim = mixHex(near, '#ffe6b8', 0.55 * lit * (1 - dayHi) + 0.1);
    const deep = mixHex(cold, '#000000', 0.2);
    // 缺口比例（打折）+ alpha 反算：g + (1-g)(1-a) = 该层透射率
    const trans = 1 - 0.9 * b.occ / 100;
    const gap = Math.min(0.55, 0.62 * (1 - b.occ / 100));
    const alpha = Math.max(0.10, Math.min(0.96,
      1 - (trans - gap) / Math.max(0.05, 1 - gap)));
    const rand = mulberry(Math.round(b.hM) + Math.round(b.occ * 7) + 13);
    const clumps = Math.max(6, Math.min(24, Math.round(W / 110)));
    const gapW = W * gap / Math.max(1, clumps - 1);
    const clearAtBody = b.occ < 50 && bodyX > -W && bodyX < 2 * W;
    // 缝是"天体附近一扇窗"：按日/月面尺寸取，不能跟着层厚一起放大
    // （层厚接近画面高度时，按层厚取缝会把整幅天都挖空、云带整个消失）
    const gapC = bodyX + (rand() - 0.5) * Math.min(hPx, 60) * 1.5;
    const gapHalf = Math.min(0.24 * W, (bodyR || 20) * 2.4 + hPx * 0.28);
    ctx.beginPath();
    let x = -W * 0.04;
    for (let c = 0; c < clumps; c++) {
      const cw = W / clumps * (1.3 + rand() * 1.6);
      const x0 = x, x1 = x + cw;
      if (clearAtBody && x1 > gapC - gapHalf && x0 < gapC + gapHalf) {
        x += cw + gapW * (0.6 + rand() * 0.9);   // 视线所在处留缝
        continue;
      }
      const lobes = 3 + Math.round(rand() * 4);
      for (let k = 0; k < lobes; k++) {
        const ex = x0 + cw * (k + 0.5) / lobes;
        const ey = yC + (rand() - 0.5) * hPx * 0.55;
        const rx = cw / lobes * (0.9 + rand() * 1.1);
        const ry = Math.max(3.5, hPx * (0.30 + rand() * 0.45));
        ctx.moveTo(ex + rx, ey);
        ctx.ellipse(ex, ey, rx, ry, 0, 0, Math.PI * 2);
      }
      x += cw + gapW * (0.6 + rand() * 0.9);
      if (x > W * 1.05) break;
    }
    // occ>50：确保视线方向上确实被云盖住
    if (!clearAtBody && bodyX > -W && bodyX < 2 * W) {
      for (let k = 0; k < 4; k++) {
        const ex = bodyX + (k - 1.5) * hPx * 0.55;
        const rx = hPx * (0.5 + rand() * 0.4);
        const ry = Math.max(3.5, hPx * (0.35 + rand() * 0.4));
        ctx.moveTo(ex + rx, yC);
        ctx.ellipse(ex, yC, rx, ry, 0, 0, Math.PI * 2);
      }
    }
    const g = ctx.createLinearGradient(0, top, 0, bot);
    g.addColorStop(0, hexA(rim, alpha * 0.85));
    g.addColorStop(0.22, hexA(near, alpha));
    g.addColorStop(0.6, hexA(far, alpha));
    g.addColorStop(1, hexA(deep, alpha * 0.8));
    ctx.fillStyle = g;
    ctx.fill();
    // 云后日光把云自己照亮一层（前向散射）：日面仍按透射率被遮，
    // 但云要亮起来——满云也不该是一片死灰。
    const gl = (1 - trans) * lit;
    if (gl > 0.06 && bodyX > -W && bodyX < 2 * W) {
      ctx.save();
      ctx.clip();
      const rr = Math.max(3 * hPx, 0.9 * px);
      const rg = ctx.createRadialGradient(bodyX, yC, 0, bodyX, yC, rr);
      // 云被照亮的光色：低日暖橘，高日偏白
      const gc = sunAlt > 6 ? '255,232,196' : '255,196,120';
      rg.addColorStop(0, `rgba(${gc},${(0.42 * gl).toFixed(2)})`);
      rg.addColorStop(0.5, `rgba(${gc},${(0.16 * gl).toFixed(2)})`);
      rg.addColorStop(1, `rgba(${gc},0)`);
      ctx.globalCompositeOperation = 'lighter';
      ctx.fillStyle = rg;
      ctx.fillRect(0, 0, W, H);
      ctx.restore();
    }
    labels.push([b, yC]);
  }
  // 标注（右缘，重叠时上下让位）
  if (!dense) return;
  labels.sort((a, b2) => a[1] - b2[1]);
  let lastY = PV_TOP;
  ctx.font = '500 17px "PingFang SC",sans-serif';
  ctx.textAlign = 'right';
  for (const [b, y] of labels) {
    let yy = Math.max(PV_TOP + 12, Math.min(H - PV_BOTTOM - 8, y));
    if (yy < lastY + 20) yy = lastY + 20;
    lastY = yy;
    const txt = `${({low: '低云', mid: '中云', high: '高云'})[b.band]} `
      + `${Math.round(b.occ)}%`
      + (b.dKm ? ` · 交点 ${Math.round(b.dKm)}km` : '');
    ctx.lineWidth = 3;
    ctx.strokeStyle = 'rgba(0,0,0,.5)';
    ctx.strokeText(txt, W - 14, yy + 5);
    ctx.fillStyle = b.lit > 0.5 ? 'rgba(255,226,186,.95)'
      : 'rgba(226,234,246,.92)';
    ctx.fillText(txt, W - 14, yy + 5);
  }
}

/* 山脊剪影：16 方位真实遮蔽角（两层做纵深），画在云与天体之上 */
function pvDrawRidge(ctx, W, H, r, azC, fovH, xOfAz, yOfAlt, horizonY, dense) {
  const pxInv = xOfAz(azC + 1) - xOfAz(azC);
  const span = pxInv ? (W / 2) / pxInv : fovH / 2;
  const a0 = azC - span, a1 = azC + span;
  const layers = [[0.42, '#3a465c'], [1.0, '#161e2e']];
  const baseY = Math.max(horizonY, yOfAlt(0));
  const steps = dense ? 96 : 48;
  let front = null;                 // 最靠前那层的地形轮廓，供"地下天体"裁剪用
  for (const [depth, color] of layers) {
    // Path2D：既用来填充，也留给调用方做 clip（只在地面区域里画半透明天体）
    const path = new Path2D();
    path.moveTo(0, H);
    for (let i = 0; i <= steps; i++) {
      const a = a0 + (a1 - a0) * i / steps;
      const ridge = horizonRidgeAlt(r, a) * depth + Math.sin(i * 0.9) * 0.02;
      const y = Math.min(baseY, yOfAlt(Math.max(-0.6, ridge)));
      path.lineTo(W * i / steps, y);
    }
    path.lineTo(W, H);
    path.closePath();
    ctx.fillStyle = color;
    ctx.fill(path);
    front = path;
  }
  return front;
}

function pvDrawWater(ctx, W, H, horizonY, sunAlt, isSun, bodyX, alt) {
  const baseY = Math.min(H, Math.max(0, horizonY));
  if (baseY >= H) return;
  const dayF = clamp01(sunAlt / 14);
  const g = ctx.createLinearGradient(0, baseY, 0, H);
  const top = isSun ? mixHex('#2a3244', '#c9a884', 0.35 + 0.5 * dayF)
                    : mixHex('#101828', '#5b6b86', 0.35 * dayF);
  g.addColorStop(0, top);
  g.addColorStop(1, '#0d1420');
  ctx.fillStyle = g;
  ctx.fillRect(0, baseY, W, H - baseY);
  // 天体下方的反光道（跟随天体方位，而不是画面中心）
  const glowF = clamp01(1 - Math.abs(sunAlt + 1.5) / 12);
  const a = Math.max(isSun ? glowF : clamp01(-sunAlt / 6) * 0.5, 0.06);
  if (a > 0.03 && alt > -3 && bodyX > -40 && bodyX < W + 40) {
    const rw = W * (0.05 + 0.06 * a);
    const rg = ctx.createLinearGradient(bodyX - rw, 0, bodyX + rw, 0);
    rg.addColorStop(0, 'rgba(0,0,0,0)');
    rg.addColorStop(0.5, isSun ? `rgba(255,206,140,${(0.5 * a).toFixed(2)})`
                               : `rgba(214,224,244,${(0.32 * a).toFixed(2)})`);
    rg.addColorStop(1, 'rgba(0,0,0,0)');
    ctx.fillStyle = rg;
    ctx.fillRect(bodyX - rw, baseY, rw * 2, H - baseY);
  }
}

/* ══════════ 五、标尺 / HUD ══════════ */

/* 高度角标尺（左缘）+ 方位刻度（底部）：刻度落在真实整度上，中性色不抢画面。
   （画布即取景框，所以刻度标的就是照片边缘的实际角度。） */
function pvDrawRulers(ctx, W, H, xOfAz, yOfAlt, azC, altC, px, dense) {
  const yTop = 0, yBot = H;
  const hi = altC + (H / 2) / px, lo = altC - (H / 2) / px;
  const span = hi - lo;
  const step = span > 40 ? 10 : span > 18 ? 5 : span > 8 ? 2
    : span > 4 ? 1 : span > 1.6 ? 0.5 : 0.2;
  const ink = 'rgba(255,255,255,.72)';
  ctx.font = `${dense ? 500 : 600} ${dense ? 16 : 18}px "PingFang SC",sans-serif`;
  ctx.textAlign = 'left';
  for (let a = Math.ceil(lo / step) * step; a <= hi + 1e-6; a += step) {
    const y = yOfAlt(a);
    if (y < yTop + 4 || y > yBot - 4) continue;
    const major = Math.abs(a / (step * 5) - Math.round(a / (step * 5))) < 1e-6;
    ctx.strokeStyle = 'rgba(255,255,255,.5)';
    ctx.lineWidth = major ? 1.6 : 1;
    ctx.beginPath();
    ctx.moveTo(0, y);
    ctx.lineTo(major ? 22 : 12, y);
    ctx.stroke();
    const txt = `${a > 0 ? '+' : ''}${a.toFixed(step < 1 ? 1 : 0)}°`;
    ctx.lineWidth = 3;
    ctx.strokeStyle = 'rgba(0,0,0,.55)';
    ctx.strokeText(txt, 26, y + 5);
    ctx.fillStyle = ink;
    ctx.fillText(txt, 26, y + 5);
  }
  // 方位刻度
  const spanAz = W / px;
  const azStep = spanAz > 60 ? 10 : spanAz > 24 ? 5 : spanAz > 9 ? 2
    : spanAz > 4 ? 1 : spanAz > 1.6 ? 0.5 : 0.2;
  ctx.textAlign = 'center';
  ctx.font = `${dense ? 500 : 600} ${dense ? 15 : 17}px "PingFang SC",sans-serif`;
  for (let a = Math.ceil((azC - spanAz / 2) / azStep) * azStep;
       a <= azC + spanAz / 2 + 1e-6; a += azStep) {
    const x = xOfAz(a);
    if (x < 8 || x > W - 8) continue;
    const major = Math.abs(a / azStep - Math.round(a / azStep)) < 1e-6;
    ctx.strokeStyle = 'rgba(255,255,255,.5)';
    ctx.lineWidth = major ? 1.6 : 1;
    ctx.beginPath();
    ctx.moveTo(x, H - 2);
    ctx.lineTo(x, H - (major ? 13 : 7));
    ctx.stroke();
    if (!major || !dense) continue;
    const txt = `${(((a % 360) + 360) % 360).toFixed(azStep < 1 ? 1 : 0)}°`;
    ctx.lineWidth = 3;
    ctx.strokeStyle = 'rgba(0,0,0,.55)';
    ctx.strokeText(txt, x, H - 20);
    ctx.fillStyle = ink;
    ctx.fillText(txt, x, H - 20);
  }
}

function pvDrawHud(ctx, W, H, w, r, isSun, alt, azDisp, sunAlt, aod, fovH, fovV,
                   vis, basis, cSum, note, exitMin, inFrame, dense,
                   ridgeBlocked, focalMm, bodyPct, dip, altC, groundLine) {
  // 上/下压暗，保证任何场景下文字可读
  const top = ctx.createLinearGradient(0, 0, 0, PV_TOP + 16);
  top.addColorStop(0, 'rgba(6,10,18,.78)');
  top.addColorStop(0.72, 'rgba(6,10,18,.45)');
  top.addColorStop(1, 'rgba(6,10,18,0)');
  ctx.fillStyle = top;
  ctx.fillRect(0, 0, W, PV_TOP + 16);
  const bot = ctx.createLinearGradient(0, H - PV_BOTTOM - 10, 0, H);
  bot.addColorStop(0, 'rgba(6,10,18,0)');
  bot.addColorStop(1, 'rgba(6,10,18,.70)');
  ctx.fillStyle = bot;
  ctx.fillRect(0, H - PV_BOTTOM - 10, W, PV_BOTTOM + 10);

  const aodTxt = aod <= 0.15 ? '通透' : aod <= 0.3 ? '较透'
    : aod <= 0.5 ? '浑浊' : '污烧';
  const periodTxt = w.period_name ? `·${w.period_name}` : '';
  const appAlt = alt + pvRefractionDeg(alt);
  const bodyTxt = isSun ? '日面' : '月面';
  ctx.textAlign = 'left';
  ctx.font = `600 ${dense ? 22 : 25}px "PingFang SC",sans-serif`;
  ctx.fillStyle = '#f0f5ff';
  ctx.fillText(`${w.type}${periodTxt} · ${minToHM(h2m((w.best_time || '00:00')
    .slice(0, 5)) + _pvOff)} · 高度 ${alt.toFixed(2)}°`
    + `（视 ${appAlt.toFixed(2)}°）· 方位 ${azDisp.toFixed(2)}°`, 16, 26);
  const cTxt = `云(${basis}) 低${Math.round(cSum.low)}/中${Math.round(cSum.mid)}`
    + `/高${Math.round(cSum.high)}%`;
  // 第二行：焦距 / 视场 / 天体在照片里的真实大小 / AOD / 云
  ctx.font = `400 ${dense ? 16 : 19}px "PingFang SC",sans-serif`;
  ctx.fillStyle = 'rgba(232,240,252,.86)';
  ctx.fillText(`等效 ${focalMm}mm（全画幅）· 视场 ${fovH.toFixed(2)}°×`
    + `${fovV.toFixed(2)}° · ${bodyTxt}占宽 ${bodyPct.toFixed(1)}% · `
    + `AOD ${aod}（${aodTxt}）· ${cTxt}`
    + (!isSun && w.illum != null ? ` · 月相照度 ${Math.round(w.illum)}%` : ''),
    16, 50);

  // 状态行：谁挡住了 / 在不在画面里 / 还能待多久 / 地平线在哪
  let foot = note;
  // 地面线（实际画出来的那条：山脊轮廓，无地形数据时是海平线略下）没进画面时
  // 明确说差多少，而不是让人以为"天就是黑的"
  if (foot.indexOf('挡住') < 0 && altC - fovV / 2 - groundLine > 0.02) {
    foot += `；地面线在画面下方 ${(altC - fovV / 2 - groundLine).toFixed(1)}°`;
  }
  ctx.font = `500 ${dense ? 16 : 18}px "PingFang SC",sans-serif`;
  ctx.fillStyle = note && (note.indexOf('挡住') >= 0 || note.indexOf('拍不到') >= 0)
    ? '#ffb4a4' : 'rgba(226,236,250,.9)';
  ctx.fillText(foot, 16, 74);

  ctx.textAlign = 'right';
  ctx.font = `700 ${dense ? 25 : 29}px "PingFang SC",sans-serif`;
  ctx.fillStyle = ridgeBlocked ? '#ff8b7a'
    : vis >= 60 ? '#6ee7a8' : vis >= 30 ? '#ffd166' : '#ff8b7a';
  // 被山脊挡住时这个数字只是"云的可见度"，不能读成"拍得到"
  ctx.fillText(`${ridgeBlocked ? '云可见度' : '可见度'} ${vis}%`, W - 16, 30);
  ctx.font = `400 ${dense ? 15 : 17}px "PingFang SC",sans-serif`;
  ctx.fillStyle = 'rgba(232,240,252,.8)';
  const sub = (w.sight && w.sight.trans != null)
    ? `视线透射率 ${Math.round(w.sight.trans * 100)}%`
    : (sunAlt < -4 ? '夜间 · 云量为机位口径' : '云遮挡估算');
  ctx.fillText(sub, W - 16, 54);
}

/* ══════════ 六、工具 ══════════ */

/* 真实月相：在离屏画布上绘制满月盘并擦出暗面（destination-out 不会破坏
   主画布的天空），再整体 drawImage 合成。凸月补回分界椭圆亮侧半边，
   月牙则擦掉亮侧半边。waxing=true 亮面在右（傍晚可见），false 亮面在左（清晨）。
   angleDeg 可选：亮面在屏幕上的朝向（0=右，90=上），给了就按它旋转。 */
function drawMoonPhase(ctx, cx, cy, R, illum, waxing, alpha, angleDeg) {
  const f = Math.max(0, Math.min(1, illum / 100));
  const litRight = waxing !== false;
  const size = Math.max(4, Math.ceil(R * 2.6));
  const off = document.createElement('canvas');
  off.width = off.height = size;
  const octx = off.getContext('2d');
  const ox = size / 2, oy = size / 2;
  octx.fillStyle = `rgba(236,240,248,${alpha.toFixed(2)})`;
  octx.beginPath();
  octx.arc(ox, oy, R, 0, Math.PI * 2);
  octx.fill();
  // 月面斑纹（静海）：让月亮不像一块塑料片
  octx.save();
  octx.beginPath();
  octx.arc(ox, oy, R, 0, Math.PI * 2);
  octx.clip();
  octx.fillStyle = `rgba(176,190,214,${(alpha * 0.32).toFixed(2)})`;
  const rr = mulberry(97);
  for (let i = 0; i < 7; i++) {
    const a = rr() * Math.PI * 2, d = Math.pow(rr(), 0.7) * R * 0.62;
    const r2 = R * (0.10 + rr() * 0.2);
    octx.beginPath();
    octx.ellipse(ox + Math.cos(a) * d, oy + Math.sin(a) * d, r2, r2 * 0.8,
                 a, 0, Math.PI * 2);
    octx.fill();
  }
  octx.restore();
  if (illum < 99.5) {
    const rx = R * Math.abs(2 * f - 1);
    octx.save();
    octx.beginPath();
    octx.arc(ox, oy, R, 0, Math.PI * 2);
    octx.clip();
    // 1) 擦掉暗侧半圆（waxing 暗在左，逆时针；waning 暗在右，顺时针）
    octx.globalCompositeOperation = 'destination-out';
    octx.fillStyle = 'rgba(0,0,0,1)';
    octx.beginPath();
    octx.arc(ox, oy, R, -Math.PI / 2, Math.PI / 2, litRight);
    octx.fill();
    // 2) 分界椭圆：凸月补回亮侧半边；月牙擦掉亮侧半边
    if (rx > 0.5) {
      const ccw = (f > 0.5) === litRight;
      octx.beginPath();
      octx.ellipse(ox, oy, rx, R, 0, -Math.PI / 2, Math.PI / 2, ccw);
      if (f > 0.5) {
        octx.globalCompositeOperation = 'source-over';
        octx.fillStyle = `rgba(236,240,248,${alpha.toFixed(2)})`;
      }
      octx.fill();
    }
    octx.restore();
  }
  if (angleDeg == null) {
    ctx.drawImage(off, cx - size / 2, cy - size / 2);
  } else {
    // 画布 y 轴向下：屏幕"上"= -y，所以按 -angle 旋转
    ctx.save();
    ctx.translate(cx, cy);
    ctx.rotate(-angleDeg * Math.PI / 180);
    ctx.drawImage(off, -size / 2, -size / 2);
    ctx.restore();
  }
}

/* 视野内最高山脊遮蔽角（决定是否把地平线纳入画面 + 遮挡提示） */
function pvRidgeMax(r, azC, fovH) {
  if (!r.site_info || !r.site_info.horizon_mask) return 0;
  let mx = 0;
  for (let i = 0; i <= 24; i++) {
    const a = azC - fovH + (2 * fovH) * i / 24;
    mx = Math.max(mx, horizonRidgeAlt(r, a));
  }
  return mx;
}

function pad2(n) { return String(n).padStart(2, '0'); }
function h2m(t) {
  return parseInt(t.slice(0, 2), 10) * 60 + parseInt(t.slice(3, 5), 10);
}
function minToHM(m) {
  m = ((Math.round(m) % 1440) + 1440) % 1440;
  return pad2(Math.floor(m / 60)) + ':' + pad2(m % 60);
}

/* 连续分钟插值（targetMin 可 <0 / >1440，跨午夜安全） */
function interpTrkMin(times, arr, targetMin) {
  if (!times || !arr || !arr.length) return null;
  const pts = [];
  for (let i = 0; i < times.length; i++) {
    const t5 = (times[i] || '').slice(0, 5);
    if (/^\d{2}:\d{2}$/.test(t5) && arr[i] != null) {
      let m = h2m(t5);
      if (pts.length && m < pts[pts.length - 1][0]) m += 1440;
      pts.push([m, arr[i]]);
    }
  }
  if (!pts.length) return null;
  if (targetMin <= pts[0][0]) return pts[0][1];
  if (targetMin >= pts[pts.length - 1][0]) return pts[pts.length - 1][1];
  for (let i = 1; i < pts.length; i++) {
    if (targetMin <= pts[i][0]) {
      const [t0m, v0] = pts[i - 1], [t1m, v1] = pts[i];
      return v0 + (v1 - v0) * (targetMin - t0m) / Math.max(1, t1m - t0m);
    }
  }
  return pts[pts.length - 1][1];
}

function interpCloud(times, arr, tKey) {
  if (!times || !arr || !arr.length) return null;
  for (let i = 0; i < times.length; i++) {
    if (key5(times[i]) === tKey) {
      const v = arr[i];
      return v == null ? null : +v;
    }
  }
  let best = null, bd = 1e9;
  for (let i = 0; i < times.length; i++) {
    const d = Math.abs(h2m(key5(times[i])) - h2m(tKey));
    if (d < bd && arr[i] != null) { bd = d; best = arr[i]; }
  }
  return best == null ? null : +best;
}
function key5(t) { return (t || '').slice(0, 5); }

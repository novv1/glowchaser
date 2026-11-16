/* 逐光者 GlowChaser 前端逻辑 */
const API = '';
let cities = [];
let curReport = null;
let curRegion = null;          // 当前报告的校准区域（'prd' 珠三角 | 其他=上海）
let mapChart = null, revChart = null, pmChart = null, csChart = null,
    hzChart = null, ncTimer = null;
let skyOffsetMin = 0;          // 霞光模拟时间偏移（分钟，相对事件时刻）
let appMode = 'glow';          // 'glow' 霞光 | 'align' 悬日月
let glowType = 'sunset';       // 晚霞 | 朝霞
let _reportReq = 0;           // 报告请求序号（丢弃过期响应）
let _alignReq = 0;            // 对准请求序号（丢弃过期响应）
// v2.36 手动查询：进入页面（含深链）、切模式、改城市/日期/事件/选点，全都只改
// 参数并标「待查询」；只有点「查询」「查询天气」「重试」这类显式动作才请求后端。
// queryPending = 参数已改、屏幕上的结果还是旧参数算出来的。
let queryPending = false;
const alignState = {           // 悬日月独立模式状态
  body: 'sun', period: 'rise',
  cam: null,                   // {lat, lon, name} 机位
  target: null,                // {lat, lon} 目标点
  az: 270, aov: 3, elev: 0,    // 目标方位/视场角/目标仰角
  data: null, key: '',
};

function clearNcTimer() {
  if (ncTimer) { clearInterval(ncTimer); ncTimer = null; }
}
let gdGeo = null;

// ---------------- HTML 转义 ----------------
// 地名/城市名来自上游（OSM Photon / Nominatim / 本地地名库），拼进 innerHTML
// 之前必须转义：文本节点和 title 这类属性值都要（引号能逃出属性，进而变成
// 真的 HTML 标签与事件处理器）。
function esc(t) {
  return String(t == null ? '' : t).replace(/[&<>"']/g, c => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  }[c]));
}

// ---------------- Tab 分组导航 ----------------
// 新增图表务必登记，否则 tab 切换 / 窗口 resize 后不会重算尺寸
const CHARTS = () => [mapChart, revChart, pmChart, csChart, hzChart,
                      window._altChart, window._soundChart,
                      window._alignTrackChart,
                      window._adrSection, window._adrTrack,
                      window._adrModel].filter(Boolean);

// 播放/暂停按钮的按下态：读屏用户需要 aria-pressed（原先只有 ▶/⏸ 符号）
function setPlayState(el, playing) {
  if (!el) return;
  el.textContent = playing ? '⏸' : '▶';
  el.setAttribute('aria-pressed', playing ? 'true' : 'false');
}

function showTab(name) {
  // M1：cinema 下走 film burn 转场（doCut 包住原生类切换；异常回落原生）
  const from = currentTab();
  const cut = () => showTabCut(name);
  if (from !== name && typeof window.switchPane === 'function'
      && typeof window.__cinemaBurnMs === 'number') {
    try { window.switchPane(from, name, cut); return; }
    catch (e) { /* 回落原生切换 */ }
  }
  cut();
}

function showTabCut(name) {
  // A单列（spec m02291）：不再切面板，导航 = 滚动到对应锚点 + 高亮
  name = normalizeTab(name);
  const anchor = document.getElementById('sec-' + name);
  document.querySelectorAll('#tabnav :is(button, a)').forEach(b => {
    const on = (b.dataset.sec || b.dataset.tab) === name;
    b.classList.toggle('active', on);
    b.setAttribute('aria-selected', on ? 'true' : 'false');
  });
  localStorage.setItem('gc_tab', name);
  openTier(name);
  if (anchor) {
    try { anchor.scrollIntoView({behavior: reduceMotion() ? 'auto' : 'smooth', block: 'start'}); }
    catch (e) { anchor.scrollIntoView(); }
  }
  // 首次进入「深看」：自动生成当前选中事件的地平线热力图
  if (name === 'deep' && !showTab._astroLoaded && cities.length) {
    showTab._astroLoaded = true;
    loadHorizon();
  }
  // 单列下所有图表常驻可见：切 tab 时统一 resize（此前隐藏容器里 init 的实例尺寸为 0）
  requestAnimationFrame(() => CHARTS().forEach(ch => {
    try { ch.resize(); } catch (e) { /* disposed */ }
  }));
  // M6：切 tab 后新 pane 的 cinema 卡片也要进 reveal 观察（M5 只扫了概览）
  requestAnimationFrame(() => updateTabInk(true));
  try { if (typeof window.__cinemaScan === 'function') window.__cinemaScan(anchor || document); } catch (e) {}
  try { revealVisibleCards(); } catch (e) {}
  writeRoute(true);
}

// ---------------- 章节折叠 ----------------
// 收起状态 `hidden` 的容器里 ECharts 会以 0 尺寸初始化，展开后必须统一 resize。
function tierBody(tid) {
  return document.getElementById('tierBody-' + tid);
}
function setTier(tid, open) {
  const body = tierBody(tid);
  const tier = body && body.closest('.tier');
  const btn = tier && tier.querySelector('.tier-toggle');
  if (!body) return;
  body.hidden = !open;
  if (tier) tier.classList.toggle('open', open);
  if (btn) {
    btn.setAttribute('aria-expanded', open ? 'true' : 'false');
    btn.textContent = open ? '收起' : '展开';
  }
  if (open) requestAnimationFrame(() => {
    CHARTS().forEach(ch => { try { ch.resize(); } catch (e) { /* disposed */ } });
    try { revealVisibleCards(); } catch (e) {}
  });
}
function openTier(tid) {
  const body = tierBody(tid);
  if (body && body.hidden) setTier(tid, true);
}
function bindTiers() {
  document.querySelectorAll('.tier').forEach(tier => {
    const btn = tier.querySelector('.tier-toggle');
    const body = tier.querySelector('.tier-body');
    if (!btn || !body) return;
    btn.onclick = () => setTier(tier.dataset.tier, body.hidden);
  });
}
function resetTiers() {
  ['evidence', 'deep', 'log'].forEach(tid => setTier(tid, false));
}

function bindTabs() {
  document.querySelectorAll('#tabnav :is(button, a)').forEach(b => {
    const name = b.dataset.sec || b.dataset.tab;
    b.onclick = (e) => {
      e.preventDefault();      // A单列：href="#sec-x" 仅作无 JS 兜底，点击交 showTab
      showTab(name);
    };
  });
  const saved = normalizeTab(localStorage.getItem('gc_tab') || 'tonight');
  showTab(saved);
  window.addEventListener('resize', () => {
    CHARTS().forEach(ch => {
      try { ch.resize(); } catch (e) { /* disposed */ }
    });
    updateTabInk(false);
  });
}

// M2：场景帧挂载 + 字幕复用 heroVerdict + timecode/帧meta（挂载失败/非 cinema 全部静默）
// M7：时间码按 24fps 真跑码（附录B#4）；reduced-motion 下退回 1s 节拍，不做高频改写
function bindSceneFrame() {
  try {
    if (!window.SceneEngine || !document.getElementById('cvSky')) return;
    if (!window.SceneEngine.mount({ sky: 'cvSky' })) return;
    paintSceneSub();
    setInterval(paintSceneSub, 1000);
    if (!reduceMotion()) setInterval(paintSceneTc, 42);
    window.__scenePaint = paintSceneSub;
  } catch (e) { /* 静态兜底：保持原生 hero 渐变 */ }
}
function paintSceneTc() {
  try {
    if (document.hidden) return;
    const tc = document.getElementById('frameTc');
    if (!tc) return;
    const SE = window.SceneEngine || {};
    const d = new Date();
    const ff = String(Math.floor(d.getMilliseconds() / 1000 * 24)).padStart(2, '0');
    tc.textContent = d.toTimeString().slice(0, 8) + ':' + ff;
  } catch (e) {}
}
function paintSceneSub() {
  try {
    const v = (document.getElementById('heroVerdict') || {}).textContent || '';
    const sub = document.getElementById('frameSub');
    if (sub && v && v !== '--') sub.textContent = '“' + v + '”';
    const SE = window.SceneEngine || {};
    const mc = document.getElementById('frameMetaC');
    if (mc && SE.params) { const p = SE.params();
      mc.textContent = String(p.genus || '--').toUpperCase() + ' · LIT ' + p.lit + '% · ALT ' + (+p.alt).toFixed(1) + '°'
        + (p.rain && p.rain !== 'none' ? ' · ' + p.rain.toUpperCase() : ''); }
    paintSceneTc();
  } catch (e) {}
}

// ---------------- 动效层 ----------------
// 所有动效统一尊重系统的「减少动态效果」设置
function reduceMotion() {
  return !!(window.matchMedia
    && window.matchMedia('(prefers-reduced-motion: reduce)').matches);
}

// Tab 指示器：单一主题下选中态由 .scenenav 的金底 ::before 承担，
// 原滑动胶囊已由主题层隐藏，这里保留函数壳供既有调用点复用。
function updateTabInk() {}

// 卡片入场：新报告到达时重置（resetReveal），随后随首屏渐次淡入
// A单列：报告流直排，用 #report-flow 作用域取代 .tabpane.active
const REVEAL_SEL = '#report-flow .card, #report-flow .dark-card, '
                 + '#report-flow .hero, #alignView > .card';

function resetReveal() {
  _heroRingVal = null;          // 下一次渲染概率环重新生长
  document.querySelectorAll('[data-rv]').forEach(el => {
    el.removeAttribute('data-rv');
    el.classList.remove('card-in');
  });
}

function revealVisibleCards() {
  if (reduceMotion()) return;
  const seen = new Set();
  let i = 0;
  document.querySelectorAll(REVEAL_SEL).forEach(el => {
    if (seen.has(el)) return;
    seen.add(el);
    // offsetParent 为 null = 处于隐藏面板/display:none，跳过
    if (!el.offsetParent || el.style.display === 'none' || el.hidden) return;
    if (el.dataset.rv === '1') return;      // 同一份报告只入场一次
    el.dataset.rv = '1';
    el.style.setProperty('--stagger', Math.min(i++, 8) * 45 + 'ms');
    el.classList.remove('card-in');
    void el.offsetWidth;                    // 强制重排以重启动画
    el.classList.add('card-in');
  });
}

// 数字滚动：仅用于未被断言读取的展示位
function countUp(el, to, suffix) {
  if (!el) return;
  if (reduceMotion() || !isFinite(to)) { el.textContent = to + suffix; return; }
  const from = Number(el.dataset.cv || 0);
  el.dataset.cv = to;
  if (from === to) { el.textContent = to + suffix; return; }
  const dur = 600, t0 = performance.now();
  const step = t => {
    const k = Math.min(1, (t - t0) / dur);
    const e = 1 - Math.pow(1 - k, 3);       // easeOutCubic
    el.textContent = Math.round(from + (to - from) * e) + suffix;
    if (k < 1) requestAnimationFrame(step);
    else el.textContent = to + suffix;
  };
  requestAnimationFrame(step);
}

// 主按钮水波纹
function bindRipple() {
  document.addEventListener('pointerdown', e => {
    const btn = e.target && e.target.closest
      && e.target.closest('button.primary');
    if (!btn || reduceMotion()) return;
    const r = btn.getBoundingClientRect();
    const size = Math.round(Math.max(r.width, r.height) * 1.6);
    const sp = document.createElement('span');
    sp.className = 'ripple';
    sp.style.width = sp.style.height = size + 'px';
    sp.style.left = (e.clientX - r.left - size / 2) + 'px';
    sp.style.top = (e.clientY - r.top - size / 2) + 'px';
    btn.appendChild(sp);
    setTimeout(() => sp.remove(), 620);
  });
}

// 导航条吸顶位置：顶栏高度随滚动收窄/换行变化，量出来写进 --nav-top，
// 免得靠写死的 top 在窄屏换行时穿帮。
let _navTop = 0;
function syncNavTop() {
  const bar = document.querySelector('.topbar');
  if (!bar) return;
  const h = bar.offsetHeight + 8;
  if (h === _navTop) return;
  _navTop = h;
  document.documentElement.style.setProperty('--nav-top', h + 'px');
}

// 顶栏滚动收窄 + 滚动进度条 + 回到顶部按钮
function bindScrollFx() {
  const bar = document.querySelector('.topbar');
  const prog = document.getElementById('scrollProgress');
  const toTop = document.getElementById('btnTop');
  let raf = 0;
  const apply = () => {
    raf = 0;
    const y = window.scrollY || document.documentElement.scrollTop || 0;
    const wasScrolled = bar && bar.classList.contains('scrolled');
    if (bar && wasScrolled !== (y > 8)) {
      bar.classList.toggle('scrolled', y > 8);
      // 顶栏高度是过渡动画，跟着量几次才能贴合
      syncNavTop();
      setTimeout(syncNavTop, 130);
      setTimeout(syncNavTop, 320);
    }
    if (prog) {
      const max = document.documentElement.scrollHeight - window.innerHeight;
      const k = max > 0 ? Math.min(1, Math.max(0, y / max)) : 0;
      prog.style.transform = 'scaleX(' + k.toFixed(4) + ')';
    }
    if (toTop) toTop.classList.toggle('on', y > 600);
  };
  const onScroll = () => { if (!raf) raf = requestAnimationFrame(apply); };
  window.addEventListener('scroll', onScroll, {passive: true});
  window.addEventListener('resize', onScroll);
  if (toTop) {
    toTop.onclick = () => window.scrollTo({
      top: 0, behavior: reduceMotion() ? 'auto' : 'smooth'});
  }
  apply();
}

// 卡片柔光跟随光标：只写两个 CSS 变量，绘制交给合成层。
// 地图卡自带视觉密度，跳过（CSS 侧也用 :has 排除了）。
function bindCardSpotlight() {
  if (reduceMotion() || !window.matchMedia
      || !window.matchMedia('(hover: hover)').matches) return;
  let raf = 0, cur = null, cx = 0, cy = 0;
  document.addEventListener('pointermove', e => {
    const el = e.target && e.target.closest && e.target.closest('.card');
    if (!el || el.querySelector('.map-box')) return;
    cur = el; cx = e.clientX; cy = e.clientY;
    if (raf) return;
    raf = requestAnimationFrame(() => {
      raf = 0;
      if (!cur) return;
      const r = cur.getBoundingClientRect();
      if (!r.width || !r.height) return;
      cur.style.setProperty('--mx', ((cx - r.left) / r.width * 100) + '%');
      cur.style.setProperty('--my', ((cy - r.top) / r.height * 100) + '%');
    });
  }, {passive: true});
}

// Hero 概率环：新报告先归零再生长，让每次查询都有一记「起色」反馈
let _heroRingVal = null;
function setHeroRing(p) {
  const ring = document.getElementById('heroRing');
  if (!ring) return;
  const C = 2 * Math.PI * 96;              // 与 index.html 里 r=96 对应
  const v = Math.max(0, Math.min(100, Number(p) || 0));
  const target = String(C * (1 - v / 100));
  ring.style.strokeDasharray = String(C);
  if (reduceMotion() || _heroRingVal === v) {
    ring.style.strokeDashoffset = target;
    _heroRingVal = v;
    return;
  }
  _heroRingVal = v;
  ring.style.strokeDashoffset = String(C);
  requestAnimationFrame(() => { ring.style.strokeDashoffset = target; });
}

// ---------------- 初始化 ----------------
async function init() {
  const sel = document.getElementById('citySel');
  try {
    const res = await fetch(API + '/api/cities');
    if (!res.ok) throw new Error('HTTP ' + res.status);
    cities = (await res.json()).cities || [];
  } catch (e) {
    console.error('init: 城市列表加载失败', e);
    if (sel) sel.innerHTML = '<option>城市列表加载失败，请刷新重试</option>';
    showError('城市列表加载失败',
      '连不上服务端，无法获取城市列表。请确认本地服务（uvicorn）已启动，然后重试。');
    // 城市表挂了也得能操作：把查询/重试/模式/章节先绑上（审计 D1）
    try {
      const g = document.getElementById('btnGo');
      if (g) g.onclick = () => reloadCurrent();
      const i = document.getElementById('btnIdleGo');
      if (i) i.onclick = () => reloadCurrent();
      const r = document.getElementById('btnErrRetry');
      if (r) r.onclick = () => reloadCurrent();
      bindTabs();
      bindTiers();
    } catch (err) { /* 绑定失败不能遮盖原错 */ }
    return;
  }
  cities.forEach((c, i) => {
    const o = document.createElement('option');
    o.value = i;
    o.textContent = c.name + (c.region ? ' · ' + c.region : '');
    sel.appendChild(o);
  });
  // 恢复"我的位置"（地图选点/手输坐标持久化）
  try {
    const myloc = JSON.parse(localStorage.getItem('gc_myloc') || 'null');
    if (myloc && myloc.lat != null) {
      cities.push({id: 'myloc', name: '我的位置', region: '自定义',
                   lat: myloc.lat, lon: myloc.lon});
      const o = document.createElement('option');
      o.value = String(cities.length - 1);
      o.textContent = '我的位置 · 自定义';
      sel.appendChild(o);
      if (localStorage.getItem('gc_myloc_active') === '1') sel.value = o.value;
    }
  } catch (e) { /* skip */ }
  window.__gcCities = cities;
  if (typeof window.__compareRenderChips === 'function') {
    window.__compareRenderChips();
  }
  document.getElementById('dateSel').value = localToday();
  // v2.36：城市 / 日期只改参数并标「待查询」——点「查询」才真正取数
  sel.onchange = () => onParamsChanged();
  document.getElementById('dateSel').onchange = () => {
    const el = document.getElementById('dateSel');
    if (!el.value) el.value = localToday();   // 清空时回退今天
    syncDateUI();
    onParamsChanged();
  };
  document.querySelectorAll('#dateSeg button').forEach(b => {
    b.onclick = () => {
      document.querySelectorAll('#dateSeg button')
        .forEach(x => x.classList.remove('active'));
      b.classList.add('active');
      document.getElementById('dateSel').value =
        b.dataset.v === 'tomorrow' ? localTomorrow() : localToday();
      syncDateUI();
      onParamsChanged();
    };
  });
  syncDateUI();
  document.getElementById('btnGo').onclick = () => reloadCurrent();
  document.getElementById('btnAlign').onclick = () => reloadCurrent();
  const idleGo = document.getElementById('btnIdleGo');
  if (idleGo) idleGo.onclick = () => reloadCurrent();
  srchBind();     // 两个搜索框：输入即出模糊匹配 + 键盘/滚轮交互
  const aClear = document.getElementById('btnAlignClear');
  if (aClear) aClear.onclick = clearAlignTarget;
  const aClearCam = document.getElementById('btnAlignClearCam');
  if (aClearCam) aClearCam.onclick = clearAlignCamera;
  ['azInput', 'aovInput'].forEach(id => {
    const el = document.getElementById(id);
    if (el) el.addEventListener('change', () => {
      if (appMode === 'align') onParamsChanged();
    });
  });
  // 目标仰角：手动改过即以手动值为准，否则由目标高度反推
  const elevEl = document.getElementById('elevInput');
  if (elevEl) {
    elevEl.addEventListener('input', () => {
      alignState.elevManual = true;
      if (appMode === 'align') onParamsChanged();
    });
  }
  ['camHeightInput', 'targetHeightInput'].forEach(id => {
    const el = document.getElementById(id);
    if (el) el.addEventListener('change', () => {
      if (appMode === 'align') onParamsChanged();
    });
  });
  const hClr = document.getElementById('btnAlignHeightClear');
  if (hClr) hClr.onclick = () => {
    ['camHeightInput', 'targetHeightInput'].forEach(id => {
      const el = document.getElementById(id);
      if (el) el.value = '';
    });
    alignState.elevManual = false;
    if (appMode === 'align') onParamsChanged();
  };
  document.querySelectorAll('#modeSeg button').forEach(b => {
    b.onclick = () => switchMode(b.dataset.v);
  });
  document.getElementById('btnHorizon').onclick = loadHorizon;
  document.getElementById('btnFeedback').onclick = submitFeedback;
  document.getElementById('btnPft').onclick = loadPft;
  bindTabs();
  bindTiers();        // 章节折叠：证据/深看/记录默认收起，展开时重算图表
  bindSceneFrame();   // M2：场景引擎挂载 + 字幕复用 heroVerdict（≤20 行钩子，失败静默）
  bindPickControls();
  bindLoadCtrl();
  bindRipple();
  bindScrollFx();
  bindCardSpotlight();
  syncNavTop();
  document.querySelectorAll('#rankSeg button').forEach(b => {
    b.onclick = () => {
      document.querySelectorAll('#rankSeg button')
        .forEach(x => x.classList.remove('active'));
      b.classList.add('active');
      if (curReport) renderRanking(curReport);
    };
  });
  document.getElementById('weekGrid').addEventListener('click', function(e) {
    const cell = e.target.closest('.week-cell');
    if (cell && cell.dataset.date) pickDate(cell.dataset.date);
  });
  // v2.37：周概览/日历格子是 div，键盘用户按 Enter/Space 等同点击
  document.addEventListener('keydown', function(e) {
    if (e.key !== 'Enter' && e.key !== ' ') return;
    const cell = e.target && e.target.closest
      ? e.target.closest('.week-cell[data-date], .sr-item[data-i]') : null;
    if (!cell) return;
    e.preventDefault();
    cell.click();
  });

  document.querySelectorAll('#mapModeSeg button').forEach(b => {
    b.onclick = () => {
      document.querySelectorAll('#mapModeSeg button').forEach(x => x.classList.remove('active'));
      b.classList.add('active');
      if (curReport) renderBurnMap(curReport);
    };
  });
  document.querySelectorAll('#fbSeg button').forEach(b => {
    b.onclick = () => {
      document.querySelectorAll('#fbSeg button').forEach(x => x.classList.remove('active'));
      b.classList.add('active');
    };
  });
  document.querySelectorAll('#hEventSeg button').forEach(b => {
    b.onclick = () => {
      const seg = b.parentElement;
      seg.querySelectorAll('button').forEach(x => x.classList.remove('active'));
      b.classList.add('active');
      // 四事件独立（月出/月落/日出/日落时刻互不相同），选中即查询
      loadHorizon();
    };
  });
  try {
    gdGeo = await (await fetch('data/guangdong.json?v=31')).json();
  } catch (e) { console.warn('geo load fail', e); }
  window.gdGeoReady = !!gdGeo;    // 供截图/回归脚本等待底图就绪
  restoreModeState();
  bindRoute();
  applyRoute();                 // URL 深链优先于 localStorage 记忆
  switchMode(appMode, true);    // v2.36：只摆界面，进入不自动查询
  enterIdle();                  // 空态 + 「点查询」引导，用户点了才发请求
}


// ---------------- URL 路由 ----------------
// 形如 #/glow/overview?city=zhuhai&date=2026-09-13&type=sunset 或 #/align?...
// 目的：刷新保持、后退可用、可把「珠海今晚晚霞」整条链接发出去。
let _routeLock = false;      // 应用路由期间抑制回写，避免自激

function currentTab() {
  const b = document.querySelector('#tabnav :is(button, a).active');
  return (b && (b.dataset.sec || b.dataset.tab)) || 'tonight';
}

// 旧深链（#/glow/maps|atmo|astro|data）落到新章节，不失效
const TAB_ALIAS = {
  overview: 'tonight', maps: 'evidence', data: 'evidence',
  atmo: 'deep', astro: 'deep', log: 'log',
};
function normalizeTab(name) {
  return TAB_ALIAS[name] || (['tonight', 'evidence', 'deep', 'log'].includes(name)
    ? name : 'tonight');
}

function routeHash() {
  const q = [];
  const c = curCity();
  if (c && c.id) q.push('city=' + encodeURIComponent(c.id));
  const d = document.getElementById('dateSel').value;
  if (d) q.push('date=' + d);
  if (glowType) q.push('type=' + glowType);
  const path = appMode === 'align' ? '#/align'
    : '#/glow/' + currentTab();
  return path + (q.length ? '?' + q.join('&') : '');
}

function writeRoute(push) {
  if (_routeLock) return;
  const h = routeHash();
  if (location.hash === h) return;
  try {
    // 首个 hash 用 replace，避免污染历史
    if (push && location.hash) history.pushState(null, '', h);
    else history.replaceState(null, '', h);
  } catch (e) { location.hash = h; }
}

function parseRoute() {
  const raw = (location.hash || '').replace(/^#\/?/, '');
  const qi = raw.indexOf('?');
  const parts = (qi < 0 ? raw : raw.slice(0, qi)).split('/').filter(Boolean);
  const out = {mode: 'glow', tab: 'tonight', city: '', date: '', type: ''};
  if (parts[0] === 'align') out.mode = 'align';
  else if (parts[0] === 'glow' && parts[1]) out.tab = normalizeTab(parts[1]);
  (qi < 0 ? '' : raw.slice(qi + 1)).split('&').forEach(kv => {
    if (!kv) return;
    const i = kv.indexOf('=');
    const k = i < 0 ? kv : kv.slice(0, i);
    const v = i < 0 ? '' : decodeURIComponent(kv.slice(i + 1));
    if (k in out) out[k] = v;
  });
  return out;
}

// 把 URL 状态应用到界面；返回是否需要重新取数（由调用方决定何时加载）
function applyRoute() {
  const st = parseRoute();
  let needLoad = false;
  _routeLock = true;
  try {
    if (st.city) {
      const i = cities.findIndex(c => c.id === st.city);
      const sel = document.getElementById('citySel');
      if (i >= 0 && String(i) !== sel.value) {
        sel.value = String(i);
        if (st.city === 'myloc') localStorage.setItem('gc_myloc_active', '1');
        needLoad = true;
      }
    }
    if (st.date && st.date !== document.getElementById('dateSel').value) {
      document.getElementById('dateSel').value = st.date;
      syncDateUI();
      needLoad = true;
    }
    if ((st.type === 'sunrise' || st.type === 'sunset')
        && st.type !== glowType) {
      glowType = st.type;
      rebuildModeUI();
      needLoad = true;
    }
    if (st.mode !== appMode) {
      switchMode(st.mode, true);        // silent：本函数返回后由调用方加载
      needLoad = true;
    }
    if (appMode === 'glow' && st.tab !== currentTab()
        && document.getElementById('sec-' + st.tab)) {
      showTab(st.tab);
    }
  } finally {
    _routeLock = false;
  }
  return needLoad;
}

function bindRoute() {
  window.addEventListener('hashchange', () => {
    if (_routeLock) return;
    // v2.36：前进/后退、粘贴深链都只切参数并提示，不自动发请求
    if (applyRoute()) onParamsChanged();
    else writeRoute(false);      // 归一化（例如非法 tab）
  });
}

// ---------------- 模式：霞光 / 悬日月 ----------------
// ---------------- v2.36 手动查询：进入即查会白烧上游额度 ----------------
// 想要的行为：进入页面/深链只摆好界面，用户选完城市·日期·事件后点「查询」
// 再发请求。理由：Open-Meteo 免费层按 IP 计 10000 次/日，一次冷报告要十几到
// 几十次调用（省网格占了约 80%），共享出口 IP（云主机/运营商大内网）下这份
// 额度是大家共用的 —— 打开页面就自动查，等于替别人也把额度烧了。
function setQueryPending(on) {
  queryPending = !!on;
  const hint = document.getElementById('queryHint');
  if (hint) {
    hint.textContent = curReport
      ? '参数已改，下方仍是上一次查询的结果 · 点「查询 / 查询天气」按新参数重新预测'
      : '参数已改 · 点「查询 / 查询天气」开始预测（改参数不会自动请求上游）';
    hint.style.display = queryPending ? 'block' : 'none';
  }
  ['btnGo', 'btnAlign'].forEach(id => {
    const b = document.getElementById(id);
    if (b) b.classList.toggle('pending', queryPending);
  });
}

// 参数变更的统一出口：只标状态 + 同步 URL，绝不发请求
function onParamsChanged() {
  setQueryPending(true);
  writeRoute(false);
}

function showIdle() {
  const el = document.getElementById('idleCard');
  if (el) el.style.display = 'block';
}

function hideIdle() {
  const el = document.getElementById('idleCard');
  if (el) el.style.display = 'none';
}

// 进入页面时的落点：摆好视图 + 空态，不请求上游
function enterIdle() {
  setQueryPending(false);
  hideError();
  hideLoading();
  showIdle();
  syncSlateStatus(null);
}

function reloadCurrent() {
  writeRoute(false);
  if (appMode === 'align') loadAlign(true);
  else loadReport(false);
}

function switchMode(mode, silent) {
  appMode = mode === 'align' ? 'align' : 'glow';
  const isGlow = appMode === 'glow';
  document.getElementById('modeSeg').querySelectorAll('button')
    .forEach(b => b.classList.toggle('active', b.dataset.v === appMode));
  document.getElementById('glowView').style.display = isGlow ? 'block' : 'none';
  document.getElementById('tabnav').style.display = isGlow ? 'flex' : 'none';
  document.getElementById('alignView').style.display = isGlow ? 'none' : 'block';
  document.getElementById('cacheBanner').style.display = 'none';
  rebuildModeUI();
  saveModeState();
  writeRoute(true);
  // M1：悬日月模式导航切「特别放映 SPECIAL」单态高亮（纯视觉）
  try { if (typeof window.__cinemaSpecial === 'function') window.__cinemaSpecial(!isGlow); } catch (e) {}
  // M1：场记板 SCENE/ROLL 重绘（纯装饰，不碰数据）
  // 悬日月的视图搭建（参数卡 + 地图）与取数解耦：不进数据也要先把界面摆好
  if (!isGlow) {
    const setup = document.getElementById('alignSetupCard');
    if (setup) setup.style.display = 'block';
    setTimeout(initAlignMap, 30);
    // M6：悬日月片场化在切模式时即挂（不等查询；查询后 paintCinema 再补日历金框/月盘）
    try { if (typeof paintAlignCinema === 'function') paintAlignCinema(); } catch (e) {}
  }
  if (silent) return;
  // v2.36：换模式也算「改参数」——只提示，不自动请求上游
  if (isGlow && curReport) { hideAll(); render(); hideIdle(); }
  else if (isGlow) showIdle();
  else hideIdle();   // 悬日月有自己的「① 机位与目标」当空态，不再摆霞光的空态卡
  setQueryPending(true);
}

// 「昼夜双色」主题靠 data-sky 区分档位：朝霞/月落→dawn，晚霞/月出→dusk
function syncSkyTheme() {
  if (typeof window.__setSky !== 'function') return;
  let sky;
  if (appMode === 'glow') {
    sky = glowType === 'sunrise' ? 'dawn' : 'dusk';
  } else {
    const isSun = alignState.body === 'sun';
    const rise = alignState.period === 'rise';
    sky = isSun === rise ? 'dawn' : 'dusk';   // 日出/月落=晨，日落/月出=昏
  }
  window.__setSky(sky);
}

function rebuildModeUI() {
  const seg = document.getElementById('subSeg');
  if (appMode === 'glow') {
    seg.innerHTML = '<button data-v="sunset">晚霞</button>'
      + '<button data-v="sunrise">朝霞</button>';
    seg.querySelectorAll('button').forEach(b => {
      b.classList.toggle('active', b.dataset.v === glowType);
      b.onclick = () => {
        if (glowType === b.dataset.v) return;
        glowType = b.dataset.v;
        rebuildModeUI();
        onParamsChanged();        // 换事件（晚霞/朝霞）也是改参数
      };
    });
    const ae = document.getElementById('alignEventSeg');
    if (ae) ae.style.display = 'none';
  } else {
    seg.innerHTML = '<button data-v="sun">☀ 悬日</button>'
      + '<button data-v="moon">🌙 悬月</button>';
    seg.querySelectorAll('button').forEach(b => {
      b.classList.toggle('active', b.dataset.v === alignState.body);
      b.onclick = () => {
        if (alignState.body === b.dataset.v) return;
        alignState.body = b.dataset.v;
        alignState.period = 'rise';
        rebuildModeUI();
        onParamsChanged();        // 换事件（悬日/悬月）也是改参数
        // 本地重绘（含天空预览）：换天体只改筛选口径，不需要向上游取数
        renderAlignPage();
      };
    });
    rebuildAlignEventSeg();
  }
  syncSkyTheme();
}

function rebuildAlignEventSeg() {
  const seg = document.getElementById('alignEventSeg');
  if (!seg) return;
  const isSun = alignState.body === 'sun';
  const opts = isSun
    ? [['rise', '☀ 日出'], ['set', '☀ 日落']]
    : [['rise', '🌙 月出'], ['set', '🌙 月落']];
  seg.style.display = 'flex';
  seg.innerHTML = opts.map(([v, t]) =>
    `<button data-v="${v}" class="${alignState.period === v ? 'active' : ''}">${t}</button>`
  ).join('');
  seg.querySelectorAll('button').forEach(b => {
    b.onclick = () => {
      alignState.period = b.dataset.v;
      rebuildAlignEventSeg();
      syncSkyTheme();
      syncHorizonEvent();
      renderAlignPage();          // 本地过滤，不重新请求
      saveModeState();
    };
  });
}

function syncAlignControls() {
  const az = document.getElementById('azInput');
  const aov = document.getElementById('aovInput');
  const elev = document.getElementById('elevInput');
  if (az) az.value = (+alignState.az).toFixed(1);
  if (aov) aov.value = (+alignState.aov).toFixed(2);
  if (elev) elev.value = (+alignState.elev).toFixed(2);
  if (typeof syncFocalUI === 'function') syncFocalUI();   // 焦距跟着视场角走
  const cam = alignState.cam;
  const city = curCity();
  const camTxt = document.getElementById('alignCamTxt');
  if (camTxt) {
    camTxt.textContent = cam
      ? `${cam.name || '选点'} · ${cam.lat.toFixed(3)}, ${cam.lon.toFixed(3)}`
      : `当前城市 ${city ? city.name : '-'}（默认）`;
  }
  const tgtTxt = document.getElementById('alignTargetTxt');
  if (tgtTxt) {
    tgtTxt.textContent = alignState.target
      ? `${alignState.target.name || '选点'} · `
        + `${alignState.target.lat.toFixed(3)}, `
        + `${alignState.target.lon.toFixed(3)}`
      : '未设置（可搜索或点地图）';
  }
  const azTxt = document.getElementById('alignAzTxt');
  if (azTxt) azTxt.textContent = `${(+alignState.az).toFixed(1)}°`;
}

// 机位/目标高度与目标距离的文字摘要（数据来自后端 observer/target）
function syncAlignHeightInfo(r) {
  const el = document.getElementById('alignHeightTxt');
  if (!el) return;
  if (!r) { el.textContent = '--'; return; }
  const ob = r.observer || {}, tg = r.target || {};
  const dist = alignTargetDistanceKm();
  const srcTxt = ob.height_src === 'terrain' ? '地形'
    : ob.height_src === 'manual' ? '手动' : '默认';
  const parts = [`机位 ${ob.height_m != null ? ob.height_m : 0} m（${srcTxt}）`];
  if (ob.horizon_dip_deg) {
    parts.push(`地平线下沉 ${ob.horizon_dip_deg}°`);
  }
  if (dist != null) parts.push(`目标距 ${dist.toFixed(2)} km`);
  if (tg.elev_geo_deg != null) {
    parts.push(`目标仰角 ${tg.elev_geo_deg}°`
      + `（视 ${tg.elev_apparent_deg}°）`);
  }
  el.textContent = parts.join(' · ');
  // 自动反推时把结果回填到目标仰角输入框，便于确认
  const elevEl = document.getElementById('elevInput');
  if (elevEl && r.geometry && r.geometry.elev_source === 'target') {
    elevEl.value = (+r.geometry.elev).toFixed(2);
    alignState.elev = +r.geometry.elev;
  }
}

// ---------------- 悬日月地图：点选机位/目标 + 地名搜索 ----------------
// 球面距离（km）：与后端 cities.haversine_km 同口径
function haversineKm(la1, lo1, la2, lo2) {
  const R = 6371.0, D = Math.PI / 180;
  const p1 = la1 * D, p2 = la2 * D;
  const dp = p2 - p1, dl = (lo2 - lo1) * D;
  const a = Math.sin(dp / 2) ** 2
    + Math.cos(p1) * Math.cos(p2) * Math.sin(dl / 2) ** 2;
  return 2 * R * Math.asin(Math.sqrt(a));
}

function bearingBetween(la1, lo1, la2, lo2) {
  const R = Math.PI / 180;
  const y = Math.sin((lo2 - lo1) * R) * Math.cos(la2 * R);
  const x = Math.cos(la1 * R) * Math.sin(la2 * R)
    - Math.sin(la1 * R) * Math.cos(la2 * R) * Math.cos((lo2 - lo1) * R);
  return (Math.atan2(y, x) / R + 360) % 360;
}

// ---------------- 悬日月地图（Leaflet + 外部标准底图） ----------------
// 底图瓦片：高德矢量/影像、Esri 卫星、OSM（均可直连，无需 key）
const ALIGN_TILES = {
  amap: {
    url: 'https://webrd0{s}.is.autonavi.com/appmaptile?lang=zh_cn&size=1'
      + '&scale=1&style=8&x={x}&y={y}&z={z}',
    subdomains: ['1', '2', '3', '4'], maxZoom: 18, attribution: '高德地图',
  },
  amap_sat: {
    url: 'https://webst0{s}.is.autonavi.com/appmaptile?style=6&x={x}&y={y}&z={z}',
    subdomains: ['1', '2', '3', '4'], maxZoom: 18, attribution: '高德影像',
  },
  esri_sat: {
    url: 'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery'
      + '/MapServer/tile/{z}/{y}/{x}',
    maxZoom: 18, attribution: 'Esri World Imagery',
  },
  osm: {
    url: 'https://tile.openstreetmap.org/{z}/{x}/{y}.png',
    maxZoom: 18, attribution: '© OpenStreetMap',
  },
};

function initAlignMap() {
  const el = document.getElementById('alignMap');
  if (!el || typeof L === 'undefined') return;
  if (window._alignMap && window._alignMap._container === el) {
    window._alignMap.invalidateSize();
    return;
  }
  const city = curCity() || {lat: 22.27, lon: 113.58};
  const map = L.map(el, {zoomControl: true, attributionControl: true})
    .setView([city.lat, city.lon], 8);
  window._alignMap = map;
  window._alignMapLayers = [];
  setAlignMapSource('amap');
  // 点击：未设机位时设机位，之后设目标（不覆盖另一端）
  map.on('click', e => {
    if (appMode !== 'align') return;
    const lat = Math.round(e.latlng.lat * 1000) / 1000;
    const lon = Math.round(e.latlng.lng * 1000) / 1000;
    if (!alignState.cam) setAlignCamera(lat, lon, '地图选点');
    else setAlignTarget(lat, lon, '地图选点');
  });
  const sel = document.getElementById('alignMapSrc');
  if (sel && !sel.__bound) {
    sel.__bound = true;
    sel.onchange = () => setAlignMapSource(sel.value);
  }
  renderAlignMap(false);
}

// 切换底图瓦片层
function setAlignMapSource(key) {
  const map = window._alignMap;
  const cfg = ALIGN_TILES[key] || ALIGN_TILES.amap;
  if (!map) return;
  if (window._alignTile) map.removeLayer(window._alignTile);
  window._alignTile = L.tileLayer(cfg.url, {
    subdomains: cfg.subdomains || 'abc',
    maxZoom: cfg.maxZoom || 18,
    attribution: cfg.attribution || '',
  }).addTo(map);
  if (window._alignTile) window._alignTile.bringToBack();
  const note = document.getElementById('alignMapSrcNote');
  if (note) note.textContent = `底图：${cfg.attribution}（外部瓦片）`;
  const sel = document.getElementById('alignMapSrc');
  if (sel && sel.value !== key) sel.value = key;
}

function renderAlignMap(recenter) {
  const map = window._alignMap;
  if (!map) return;
  (window._alignMapLayers || []).forEach(l => map.removeLayer(l));
  window._alignMapLayers = [];
  const add = l => { l.addTo(map); window._alignMapLayers.push(l); };

  // 机位未显式设置时用当前城市作为默认观测点（与后端取样原点一致）
  const cam = alignState.cam, tgt = alignState.target;
  const origin = cam || curCity();
  const bounds = [];
  if (origin) {
    add(L.circleMarker([origin.lat, origin.lon], {
      radius: 7, color: '#1f5fa8', weight: 2, fillColor: '#4a90e2',
      fillOpacity: 1,
    }).bindTooltip(cam ? '机位' : '机位(当前城市)', {permanent: true,
      direction: 'top', className: 'align-map-label'}));
    bounds.push([origin.lat, origin.lon]);
  }
  if (tgt) {
    add(L.circleMarker([tgt.lat, tgt.lon], {
      radius: 8, color: '#a85f10', weight: 2, fillColor: '#e8973a',
      fillOpacity: 1,
    }).bindTooltip('目标', {permanent: true, direction: 'top',
                            className: 'align-map-label'}));
    bounds.push([tgt.lat, tgt.lon]);
  }
  if (origin && tgt) {
    add(L.polyline([[origin.lat, origin.lon], [tgt.lat, tgt.lon]],
                   {color: '#e8973a', weight: 2, opacity: .9}));
  }
  // 视线采样点 + 视线交点（沿目标方位）
  const sp = (alignState.data || {}).sight_points || {};
  (sp.points || []).forEach(p => {
    add(L.circleMarker([p.lat, p.lon], {
      radius: 3, color: '#5a7fa8', weight: 1, fillColor: '#5a7fa8',
      fillOpacity: .9,
    }).bindTooltip(`光路采样 ${p.dist_km} km`));
  });
  const BANDS = {low: '#e8c33a', mid: '#e8973a', high: '#c9433a'};
  const bands = ((alignState.data || {}).main || {}).sight;
  if (bands && bands.bands && origin) {
    Object.keys(bands.bands).forEach(b => {
      const bd = bands.bands[b];
      if (!bd || !bd.d_km) return;
      const pt = destPoint(origin.lat, origin.lon, sp.azimuth, bd.d_km);
      add(L.circleMarker([pt.lat, pt.lon], {
        radius: 7, color: '#5a2020', weight: 2,
        fillColor: BANDS[b] || '#e8973a', fillOpacity: 1,
      }).bindTooltip(`${((bd.gh_m || 0) / 1000).toFixed(1)}km 云层交点`
                     + `（云量 ${bd.occ}%）`));
      bounds.push([pt.lat, pt.lon]);
    });
  }
  if (origin && sp.distances_km && sp.distances_km.length) {
    const far = sp.distances_km[sp.distances_km.length - 1];
    const end = destPoint(origin.lat, origin.lon, sp.azimuth, far);
    add(L.polyline([[origin.lat, origin.lon], [end.lat, end.lon]],
                   {color: '#7aa6d0', weight: 1.5, opacity: .8,
                    dashArray: '6,6'}));
    bounds.push([end.lat, end.lon]);
  }
  if (recenter && bounds.length) {
    map.fitBounds(bounds, {padding: [30, 30], maxZoom: 11});
  } else if (!map.__initialized && origin) {
    map.setView([origin.lat, origin.lon], 9);
    map.__initialized = true;
  }
}

// 球面目的地：给定起点、方位、距离(km) -> 经纬度（与后端 cities.destination_point 同口径）
function destPoint(lat, lon, bearing, distKm) {
  const R = 6371.0088, D = Math.PI / 180;
  const br = bearing * D, dr = distKm / R;
  const la1 = lat * D, lo1 = lon * D;
  const la2 = Math.asin(Math.sin(la1) * Math.cos(dr)
    + Math.cos(la1) * Math.sin(dr) * Math.cos(br));
  const lo2 = lo1 + Math.atan2(Math.sin(br) * Math.sin(dr) * Math.cos(la1),
    Math.cos(dr) - Math.sin(la1) * Math.sin(la2));
  return {lat: la2 / D, lon: ((lo2 / D) + 540) % 360 - 180};
}

// 前端 UI 版本：与服务端 meta.version 比对。uvicorn 不带 --reload 时
// Python 模块常驻内存，改完后端代码不重启就会出现「前端新、后端旧」，
// 表现为数据缺失。这里直接把原因显示出来，而不是让用户看到空数据。
const UI_VERSION = '2.38';

let _serverStale = false;

// 缺少 version 字段本身就说明服务端是旧版（v2.20 起 align 必然带该字段）
function serverStale(r) {
  const sv = r && r.meta && r.meta.version;
  return !sv || sv !== UI_VERSION;
}

function checkUiVersion(r) {
  _serverStale = serverStale(r);
  const el = document.getElementById('staleBanner');
  if (!el) return;
  if (!_serverStale) { el.style.display = 'none'; return; }
  const sv = (r.meta && r.meta.version) || '未知（旧版）';
  // 两种方向的原因完全不同，提示不能混：
  //   后端旧 -> Python 模块常驻内存，改了代码没重启（去重启）
  //   前端旧 -> 浏览器吃了缓存的旧 index.html/app.js（去强制刷新；重启服务没用）
  const svNum = parseFloat(sv);
  const uiNum = parseFloat(UI_VERSION);
  const frontOld = !isNaN(svNum) && !isNaN(uiNum) && svNum > uiNum;
  el.style.display = 'block';
  el.innerHTML = `⚠ 服务端版本 ${sv} 与前端 v${UI_VERSION} 不一致：`
    + (frontOld
      ? '**浏览器在用缓存里的旧前端**，重启服务没用。请强制刷新：'
        + '<b>Ctrl + F5</b>（Mac 上 <b>Cmd + Shift + R</b>），'
        + '或清一次本站缓存。'
      : '服务端是旧版（Python 模块常驻内存），请重启服务后刷新：'
        + '（<code>python -m uvicorn app.main:app --port 8000</code>）。')
    + ' 当前缺少新字段，会显示为数据不可用。';
}

function alignOrigin() {
  // 机位未显式设置时以当前城市为默认观测点
  return alignState.cam || curCity();
}

function recomputeAlignAzimuth() {
  // 方位 = 机位→目标 连线；两端齐备才推导
  const o = alignOrigin();
  if (!o || !alignState.target) return false;
  alignState.az = Math.round(bearingBetween(
    o.lat, o.lon, alignState.target.lat, alignState.target.lon) * 10) / 10;
  return true;
}

function setAlignCamera(lat, lon, name) {
  alignState.cam = {lat: +lat, lon: +lon, name: name || '地图选点'};
  recomputeAlignAzimuth();          // 保留已有的目标点，只更新方位
  syncAlignControls();
  renderAlignMap(true);
  if (appMode === 'align') onParamsChanged();   // v2.36：选点只标待查询
}

function setAlignTarget(lat, lon, name) {
  alignState.target = {lat: +lat, lon: +lon, name: name || '地图选点'};
  recomputeAlignAzimuth();
  syncAlignControls();
  renderAlignMap(false);
  if (appMode === 'align') onParamsChanged();
}

function clearAlignTarget() {
  alignState.target = null;
  alignState.key = '';              // 目标点变了：旧结果与缓存判定一起作废
  renderAlignMap(false);
  syncAlignControls();
  if (appMode === 'align') onParamsChanged();
}

function clearAlignCamera() {
  alignState.cam = null;            // 回到「当前城市」默认机位
  alignState.key = '';
  recomputeAlignAzimuth();
  renderAlignMap(false);
  syncAlignControls();
  if (appMode === 'align') onParamsChanged();
}

// ---------------- 地点搜索：输入即出模糊匹配（下拉面板） ----------------
const SRCH_MIN_LEN = 1;        // 最少输入字数即开始联想
const SRCH_DEBOUNCE_MS = 260;  // 输入防抖
const _srchCache = new Map();  // 'role|q' -> 结果；重复查询不再打接口
const _srchState = {};         // role -> {items, active, ctrl, timer, q}

function srchEls(role) {
  const camRole = role !== 'target';
  const wrap = document.querySelector(`.srch[data-role="${camRole ? 'cam' : 'target'}"]`);
  return {
    camRole, wrap,
    inp: document.getElementById(camRole ? 'alignCamSearch' : 'alignTargetSearch'),
    box: document.getElementById(camRole ? 'alignCamResults' : 'alignTargetResults'),
  };
}

function srchPanelShow(role, html, opts) {
  const {box} = srchEls(role);
  if (!box) return;
  box.innerHTML = html;
  box.hidden = false;
  box.style.display = 'block';
  box.scrollTop = 0;
  if (!opts || !opts.keepActive) srchSetActive(role, -1);
}

function srchPanelHide(role) {
  const {box} = srchEls(role);
  if (box) { box.hidden = true; box.style.display = 'none'; }
  const st = _srchState[role];
  if (st) st.active = -1;
}

function srchHideAll() {
  srchPanelHide('cam');
  srchPanelHide('target');
}

// 把命中的关键字包成 <mark>（大小写不敏感，支持空格分隔的多关键字）
function srchHighlight(name, q) {
  const out = esc(name);
  const toks = String(q || '').trim().split(/\s+/).filter(Boolean);
  let res = out;
  toks.forEach(t => {
    const re = new RegExp('('
      + t.replace(/[.*+?^${}()|[\]\\]/g, '\\$&') + ')', 'gi');
    res = res.replace(re, '<mark>$1</mark>');
  });
  return res;
}

function srchSetActive(role, idx) {
  const st = _srchState[role];
  const {box} = srchEls(role);
  if (!st || !box) return;
  const items = box.querySelectorAll('.sr-item[data-i]');
  items.forEach(el => el.classList.remove('active'));
  st.active = idx;
  if (idx >= 0 && items[idx]) {
    items[idx].classList.add('active');
    items[idx].scrollIntoView({block: 'nearest'});
  }
}

function srchRender(role, list, q, source) {
  const st = _srchState[role] = _srchState[role] || {};
  st.items = list;
  st.q = q;
  const srcTxt = source === 'local' ? '本地地名库' : '在线地名库';
  const head = `<div class="sr-head"><span>${list.length} 个匹配</span>`
    + `<span class="src">${srcTxt}</span></div>`;
  const rows = list.map((x, i) =>
    `<div class="sr-item" data-i="${i}" role="button" tabindex="0"`
    + ` title="${esc(x.name)}">`
    + `<span class="nm">📍 ${srchHighlight(x.name, q)}</span>`
    + (Number.isFinite(x.lat)
      ? `<span class="co">${x.lat.toFixed(3)}, ${x.lon.toFixed(3)}</span>`
      : '')
    + '</div>').join('');
  srchPanelShow(role, head + rows);
  const {box} = srchEls(role);
  box.querySelectorAll('.sr-item[data-i]').forEach(el => {
    el.onmouseenter = () => srchSetActive(role, +el.dataset.i);
    el.onclick = () => srchPick(role, +el.dataset.i);
  });
  srchSetActive(role, 0);      // 默认高亮第一条，回车即可选中
}

function srchPick(role, idx) {
  const st = _srchState[role];
  if (!st || !st.items || !st.items[idx]) return;
  const it = st.items[idx];
  const nm = (it.name || st.q || '').slice(0, 24);
  srchPanelHide(role);
  const {inp} = srchEls(role);
  if (inp) inp.blur();
  if (role !== 'target') setAlignCamera(it.lat, it.lon, nm);
  else setAlignTarget(it.lat, it.lon, nm);
}

// 输入框内容 -> 查询（防抖 + 取消过期请求 + 命中缓存）
function srchOnInput(role) {
  const {inp} = srchEls(role);
  if (!inp) return;
  const st = _srchState[role] = _srchState[role] || {};
  const q = (inp.value || '').trim();
  if (st.timer) clearTimeout(st.timer);
  if (q.length < SRCH_MIN_LEN) {
    if (st.ctrl) st.ctrl.abort();
    srchPanelHide(role);
    return;
  }
  st.timer = setTimeout(() => searchAlignPlace(role, {live: true}), SRCH_DEBOUNCE_MS);
}

// 渲染一次搜索结果：区分「上游不可用」与「确实没找到」——前者不能谎报没找到
function srchShowResult(role, r, q) {
  const list = (r && r.results) || [];
  if (!list.length) {
    if (r && r.source === 'unavailable') {
      srchPanelShow(role, `<div class="sr-note">`
        + esc(r.message || '地名搜索服务暂时不可用，可稍后重试，'
             + '或直接在地图上点选位置。') + '</div>');
    } else {
      srchPanelShow(role, `<div class="sr-note">没有找到「${esc(q)}」`
        + '　试试换个说法，或直接在地图上点选。</div>');
    }
    return false;
  }
  srchRender(role, list, q, r.source);
  return true;
}

async function searchAlignPlace(role, opts) {
  const {inp, box} = srchEls(role);
  const st = _srchState[role] = _srchState[role] || {};
  const q = (inp && inp.value || '').trim();
  if (!q || !box) return;
  const other = role !== 'target' ? 'target' : 'cam';
  srchPanelHide(other);
  const cacheKey = `${role}|${q}`;
  if (_srchCache.has(cacheKey)) {
    srchShowResult(role, _srchCache.get(cacheKey), q);
    return;
  }
  if (st.ctrl) st.ctrl.abort();
  const ctrl = ('AbortController' in window) ? new AbortController() : null;
  st.ctrl = ctrl;
  // 只在超过 150ms 还没回来时显示转圈，避免闪烁
  const spin = box.parentElement.querySelector('.srch-spin');
  const spinTimer = setTimeout(() => {
    if (spin) spin.hidden = false;
  }, 150);
  try {
    const res = await fetch(`${API}/api/geocode?q=${encodeURIComponent(q)}`,
                            ctrl ? {signal: ctrl.signal} : undefined);
    if (!res.ok) throw new Error(await res.text());
    const r = await res.json();
    if ((inp.value || '').trim() !== q) return;   // 输入已变，丢弃过期结果
    // 简单 LRU：超上限只淘汰最老的一条（原先整表清空，等于把刚查的全丢了）
    if (_srchCache.size > 40) _srchCache.delete(_srchCache.keys().next().value);
    _srchCache.set(cacheKey, r);
    srchShowResult(role, r, q);
  } catch (e) {
    if (e && e.name === 'AbortError') return;
    srchPanelShow(role, `<div class="sr-note">搜索失败：${e.message}</div>`);
  } finally {
    clearTimeout(spinTimer);
    if (spin) spin.hidden = true;
  }
}

// 键盘：↑↓ 选择、Enter 选中、Esc 关闭
function srchOnKey(role, e) {
  const st = _srchState[role] || {};
  const {box, inp} = srchEls(role);
  const n = (st.items || []).length;
  if (e.key === 'ArrowDown' && n) {
    e.preventDefault();
    srchSetActive(role, Math.min(n - 1, (st.active == null ? -1 : st.active) + 1));
  } else if (e.key === 'ArrowUp' && n) {
    e.preventDefault();
    srchSetActive(role, Math.max(0, (st.active == null ? 0 : st.active) - 1));
  } else if (e.key === 'Enter') {
    e.preventDefault();
    if (n) srchPick(role, st.active == null || st.active < 0 ? 0 : st.active);
    else searchAlignPlace(role);
  } else if (e.key === 'Escape') {
    srchPanelHide(role);
    if (inp) inp.blur();
  }
}

function srchBind() {
  [['cam', 'alignCamSearch', 'btnAlignCamSearch'],
   ['target', 'alignTargetSearch', 'btnAlignTargetSearch']].forEach(([role, inId, btnId]) => {
    const {wrap, inp} = srchEls(role);
    if (!inp || !wrap || inp.__srchBound) return;
    inp.__srchBound = true;
    const clr = wrap.querySelector('.srch-clr');
    inp.addEventListener('input', () => {
      if (clr) clr.hidden = !inp.value;
      srchOnInput(role);
    });
    inp.addEventListener('focus', () => {
      if ((inp.value || '').trim().length >= SRCH_MIN_LEN) srchOnInput(role);
    });
    inp.addEventListener('keydown', e => srchOnKey(role, e));
    if (clr) clr.onclick = () => {
      inp.value = '';
      clr.hidden = true;
      srchPanelHide(role);
      inp.focus();
    };
    const btn = document.getElementById(btnId);
    if (btn) btn.onclick = () => searchAlignPlace(role);
  });
  // 点击面板外部收起（面板内的点击由条目自身处理）
  if (!window.__srchOutsideBound) {
    window.__srchOutsideBound = true;
    document.addEventListener('mousedown', e => {
      if (!e.target.closest || !e.target.closest('.srch')) srchHideAll();
    });
  }
}

function syncHorizonEvent() {
  if (appMode !== 'align') return;
  const v = alignState.body === 'sun'
    ? (alignState.period === 'rise' ? 'sun_rise' : 'sun_set')
    : (alignState.period === 'rise' ? 'moon_rise' : 'moon_set');
  document.querySelectorAll('#hEventSeg button').forEach(b =>
    b.classList.toggle('active', b.dataset.v === v));
}

// 机位→目标 的地表距离（km），供目标仰角反推
function alignTargetDistanceKm() {
  const o = alignOrigin();
  if (!o || !alignState.target) return null;
  return haversineKm(o.lat, o.lon, alignState.target.lat, alignState.target.lon);
}

// 高度输入：空/非法 -> null（把决定权交回后端：机位取地形、仰角取手动值）
function alignHeightInput(id) {
  const el = document.getElementById(id);
  if (!el || el.value === '') return null;
  const v = +el.value;
  return Number.isFinite(v) && v >= 0 ? v : null;
}

// 请求去重 key：只以 alignState 为单一来源（测试亦复用）。
// 关键点：仰角由目标高度反推时，elev 不参与 key——否则后端回填仰角
// 会让 key 变化，每次切天体/事件都误判缓存失效并重新请求。
function alignRequestKey(cam, date) {
  const tgtD = alignTargetDistanceKm();
  const elevAuto = alignState.targetHeight != null && tgtD != null
    && !alignState.elevManual;
  const elevPart = elevAuto ? 'auto' : (+alignState.elev).toFixed(2);
  return [cam.lat, cam.lon, date, (+alignState.az).toFixed(1),
          (+alignState.aov).toFixed(2), elevPart, alignState.camHeight,
          alignState.targetHeight, elevAuto ? 'a' : 'm'].join('|');
}

async function loadAlign(force) {
  const cam = alignState.cam || curCity();
  if (!cam) return;
  const date = document.getElementById('dateSel').value || localToday();
  const num = (id, fb) => {
    const v = +document.getElementById(id).value;
    return Number.isFinite(v) ? v : fb;
  };
  alignState.az = ((num('azInput', alignState.az) % 360) + 360) % 360;
  alignState.aov = Math.max(0.5, num('aovInput', alignState.aov));
  alignState.elev = num('elevInput', alignState.elev);
  // 高度输入 -> alignState（单一来源，供 key 与请求共用）
  alignState.camHeight = alignHeightInput('camHeightInput');
  alignState.targetHeight = alignHeightInput('targetHeightInput');
  const camH = alignState.camHeight;
  const tgtH = alignState.targetHeight;
  const tgtD = alignTargetDistanceKm();
  // 目标高度齐备且仰角未被手动改过时，让后端反推仰角（elev 不传）
  const elevAuto = tgtH != null && tgtD != null && !alignState.elevManual;
  const key = alignRequestKey(cam, date);
  setQueryPending(false);       // 真在查了：撤掉「待查询」提示
  hideIdle();
  if (!force && key === alignState.key && alignState.data) {
    renderAlignPage();
    return;
  }
  const req = ++_alignReq;
  const ac = new AbortController();
  _cancelFn = () => ac.abort();
  showLoading('align');
  resetReveal();
  // 新请求开始即清空旧表，避免展示过期结果
  document.getElementById('alignTbl').innerHTML = '<tr><td>计算中…</td></tr>';
  document.getElementById('celestialWrap').style.display = 'none';
  // 旧结果卡也要一起收起（审计 D10：加载/失败时不能与旧分数同屏）
  ['alignCard', 'alignDetailCard', 'alignDirCard', 'alignWeekCard',
   'alignNowcastCard'].forEach(function (id) {
    var el = document.getElementById(id);
    if (el) el.style.display = 'none';
  });
  try {
    const r3 = v => Math.round(v * 1000) / 1000;
    let url = `${API}/api/align?lat=${cam.lat}&lon=${cam.lon}`
      + `&date=${date}&azimuth=${r3(alignState.az)}`
      + `&aov=${alignState.aov}`
      // 只评估所选日期（days=1）：跨日窗口由「下次对准」日历回答，
      // 避免把 14 天里那些"无天气数据"的行混进当日窗口表
      + `&days=1`;
    if (!elevAuto) url += `&elev=${r3(alignState.elev)}`;
    if (camH != null) url += `&cam_height=${r3(camH)}`;
    if (tgtH != null) url += `&target_height=${r3(tgtH)}`;
    if (tgtH != null && tgtD != null) url += `&target_dist_km=${r3(tgtD)}`;
    const res = await fetch(url, {signal: ac.signal});
    if (!res.ok) throw await apiError(res);
    const data = await res.json();
    if (req !== _alignReq) return;      // 已有更新的请求，丢弃过期响应
    alignState.data = data;
    alignState.key = key;
    setLoadStage('正在渲染对准窗口与视线剖面…');
    renderAlignPage();
  } catch (e) {
    if (req !== _alignReq) return;
    alignState.data = null;
    alignState.key = '';
    document.getElementById('celestialWrap').style.display = 'none';
    if (e && e.name === 'AbortError') {
      showError('查询已取消', '本次对准窗口计算已取消。可重新点「查询天气」再试。');
    } else {
      document.getElementById('alignTbl').innerHTML =
        `<tr><td style="color:#c06040">获取失败：${e.message}</td></tr>`;
      showError(failTitle(e, '对准窗口计算失败'), failMessage(e));
    }
  } finally {
    if (req === _alignReq) { hideLoading(); _cancelFn = null; }
  }
  saveModeState();
}

function alignCoords() {
  if (appMode === 'align' && alignState.cam) return alignState.cam;
  return curCity();
}

function alignWindowList() {
  const r = alignState.data;
  if (!r) return [];
  return (r.alignment || []).filter(w => w.body === alignState.body
    && ((alignState.period === 'rise' && w.rising)
        || (alignState.period === 'set' && !w.rising)));
}


// ---------------- 悬日月专业版渲染 ----------------

function renderAlignDir(dw, r, bodyName, periodName) {
  const card = document.getElementById('alignDirCard');
  if (!card) return;
  if (!dw) { card.style.display = 'none'; return; }
  card.style.display = 'block';
  const meta = r.alignment_meta || {};
  document.getElementById('alignDirTitle').textContent =
    `${r.meta.date} ${dw.time} · ${bodyName}·${periodName} · 目标 ${meta.target_az}°`;
  const scoreEl = document.getElementById('alignDirScore');
  scoreEl.textContent = dw.score;
  scoreEl.style.color = scoreColor(dw.score);
  document.getElementById('alignDirVerdict').textContent =
    `${bodyName}·${periodName} · ${dw.verdict}`;
  const sBands = (dw.sight || {}).bands || {};
  const c = (Object.keys(sBands).length)
    ? {low: (sBands.low || {}).occ || 0, mid: (sBands.mid || {}).occ || 0,
       high: (sBands.high || {}).occ || 0}
    : (dw.cloud || {});
  const basisTxt = dw.cloud_basis === '视线交点' ? '逐层' : '三层代表云';
  document.getElementById('alignDirFacts').innerHTML = [
    `📅 ${r.meta.date} ${dw.time}`,
    `🧭 目标方位 ${meta.target_az}°`,
    `☁️ ${basisTxt} 低 ${c.low}% / 中 ${c.mid}% / 高 ${c.high}%`,
    `👁 可见概率 ${dw.vis_prob}%`,
    `🌫 AOD ${dw.aod != null ? dw.aod : '-'}`,
    `🌧 降水 ${dw.precip_prob != null ? dw.precip_prob + '%' : '-'}`,
  ].join(' · ');
  document.getElementById('alignDirReason').textContent =
    (dw.reasons || []).join('、') + '（非对准窗口，方向天气参考）';
  const dims = [
    ['云况（低/中/高云遮挡）', dw.dims.cloud],
    ['通透度（AOD/PM2.5）', dw.dims.transparency],
    ['降水（概率）', dw.dims.precip],
    ['稳定性（模式一致）', dw.dims.stability],
  ];
  document.getElementById('alignDirDims').innerHTML = dims.map(([name, v]) =>
    `<div class="dim-cell">
       <div class="dim-cell-head"><span>${name}</span><b>${v}</b></div>
       <div class="dim-bar"><div style="width:${v}%;background:${scoreColor(v)}"></div></div>
     </div>`).join('');
  const models = dw.models || {};
  document.getElementById('alignDirModelTbl').innerHTML =
    `<tr><th>模式</th><th>低云</th><th>中云</th><th>高云</th>`
    + '<th>可见概率</th></tr>'
    + Object.keys(models).map(n => {
      const mm = models[n] || {}, mc = mm.cloud || {};
      return `<tr><td><b>${n}</b></td><td>${mc.low}%</td><td>${mc.mid}%</td>
        <td>${mc.high}%</td>
        <td class="prob" style="color:${probColor(mm.vis_prob || 0)}">${mm.vis_prob}%</td></tr>`;
    }).join('');
  const cons = dw.consensus || {};
  document.getElementById('alignDirModelNote').textContent =
    `多模式共识可见概率 ${cons.vis_prob != null ? cons.vis_prob + '%' : '-'}`
    + `（${cons.credibility || '-'}`
    + `${cons.basis ? ' · ' + cons.basis : ''}`
    + `${cons.vis_prob_cam != null ? '；机位口径 ' + cons.vis_prob_cam + '%' : ''}）`;
  document.getElementById('alignDirWxGrid').innerHTML = [
    wxItem('AOD 550nm', fmt(dw.aod)),
    wxItem('PM2.5', fmt(dw.pm25, '', ' μg/m³')),
    wxItem('能见度', fmt(dw.visibility_km, '', ' km')),
    wxItem('相对湿度', fmt(dw.humidity, '%')),
    wxItem('风速', fmt(dw.wind, '', ' km/h')),
    wxItem('降水概率', fmt(dw.precip_prob, '%')),
    wxItem('气温', fmt(dw.temp, '', '°C')),
    wxItem('云量', `低${c.low}/中${c.mid}/高${c.high}%`),
  ].join('');
  const risks = dw.risks || [];
  document.getElementById('alignDirRiskList').innerHTML = risks.length
    ? risks.map(x => `<li>${x}</li>`).join('')
    : '<li>暂无明显风险</li>';
  // 可视化（canvas + ECharts），渲染在文字信息之后
  renderAlignDirSky(dw, r);
  renderAlignDirTrack(dw, r);
  renderAlignDirModelChart(dw);
  renderAlignDirSection(dw, r);
}

function renderAlignHero(w, r) {
  const box = document.getElementById('alignHero');
  if (!box) return;
  if (!w) { box.style.display = 'none'; return; }
  box.style.display = 'flex';
  const pastNote = w.is_past
    ? `<span style="color:#c06040">该窗口已过（${w.date ? w.date.slice(5) + ' ' : ''}`
      + `${w.best_time}，${Math.floor(-(w.minutes_to_event || 0) / 60)}h 前）`
      + '，下方「下次对准」给未来日期</span> · '
    : '';
  const f = w.feasibility;
  if (!f) {
    // 窗口存在但超出 3 天预报：仍展示几何事实，天气待临近
    document.getElementById('alignScore').textContent = '—';
    document.getElementById('alignScore').style.color = 'var(--ink-2)';
    document.getElementById('alignVerdict').innerHTML = pastNote
      + `${w.type}·${w.period_name} · `
      + (w.reason && w.reason.indexOf('超出') >= 0
         ? '超出预报窗口' : '天气数据待更新');
    const sky0 = w.sky || {};
    document.getElementById('alignFacts').innerHTML = [
      `📅 ${(w.date || '').slice(5)} ${w.best_time}`,
      `🧭 方位 ${w.best_az}°（差 ±${w.best_daz}°）`,
      `📐 高度 ${w.best_alt}°（视 ${w.apparent_alt != null ? w.apparent_alt + "°" : "—"}）`,
      `⛰ 山脊 ${w.horizon_alt != null ? w.horizon_alt + '°' : '-'}`
        + `${w.horizon_blocked ? '（遮挡）' : ''}`,
      `🌙 ${sky0.moon_phase || '-'}`
        + `${sky0.moon_illum != null ? ' ' + sky0.moon_illum + '%' : ''}`,
    ].join(' · ');
    document.getElementById('alignReason').innerHTML = pastNote
      + (w.reason || (r.meta && r.meta.weather_note)
         || '天气预报覆盖有限，临近再评估天气可行性');
    return;
  }
  const scoreEl = document.getElementById('alignScore');
  scoreEl.textContent = f.score;
  scoreEl.style.color = w.is_past ? 'var(--ink-2)' : scoreColor(f.score);
  document.getElementById('alignVerdict').innerHTML = pastNote
    + `${w.type}·${w.period_name} · ${f.verdict}`;
  const sky = w.sky || {};
  document.getElementById('alignFacts').innerHTML = [
    `📅 ${(w.date || '').slice(5)} ${w.best_time}`,
    `🧭 方位 ${w.best_az}°（差 ±${w.best_daz}°）`,
    `📐 高度 ${w.best_alt}°（视 ${w.apparent_alt != null ? w.apparent_alt + "°" : "—"}）`,
    `⛰ 山脊 ${w.horizon_alt != null ? w.horizon_alt + '°' : '-'}${w.horizon_blocked ? '（遮挡）' : ''}`,
    `🌙 ${sky.moon_phase || '-'}${sky.moon_illum != null ? ' ' + sky.moon_illum + '%' : ''}`,
    `🕐 ${sky.twilight || ''}`,
  ].join(' · ');
  document.getElementById('alignReason').textContent =
    `可见概率 ${w.vis_prob != null ? w.vis_prob + '%' : '-'}`
    + `${f.cloud_basis === '视线交点' ? '（视线交点云况）' : '（机位云量）'} · `
    + (f.reasons || []).join('、');
}

function renderAlignSight(w, r) {
  const card = document.getElementById('alignSightCard');
  if (!card) return;
  const s = w && w.sight;
  if (!s) { card.style.display = 'none'; return; }
  card.style.display = 'block';
  const BAND = {low: '低云', mid: '中云', high: '高云'};
  const title = document.getElementById('alignSightTitle');
  const note = document.getElementById('alignSightNote');
  const tbl = document.getElementById('alignSightTbl');
  const lvlTbl = document.getElementById('alignSightLevelTbl');

  if (!s.visible) {
    title.textContent = `${w.type}·${w.period_name} ${(w.date || '').slice(5)} ${w.best_time}`;
    note.innerHTML = `天体上缘视高度角 <b>${s.limb_alt}°</b>（中心 ${s.apparent_alt}°）`
      + ` 已在地平线下，不做遮挡评估。`
      + (s.dip_deg ? `（机位 ${s.observer_height_m} m，可见地平线下沉 `
        + `${s.dip_deg}°）` : '');
    tbl.innerHTML = '';
    lvlTbl.innerHTML = '';
    return;
  }

  const hit = [];
  const bands = s.bands || {};
  ['low', 'mid', 'high'].forEach(b => {
    if (bands[b]) hit.push(BAND[b]);
  });
  const dipTxt = s.dip_deg
    ? ` · 机位 ${s.observer_height_m} m（地平线下沉 ${s.dip_deg}°）` : '';
  title.textContent = `${w.type}·${w.period_name} ${(w.date || '').slice(5)} `
    + `${w.best_time} · 视高度角 ${s.apparent_alt}°（几何 ${s.geo_alt}°）`
    + dipTxt;
  const occTxt = hit.length
    ? `视线上 ${hit.join('、')}挡在天体方向`
    : '视线上各层均无有效云量';
  // 模式交叉验证：同一套交点几何，换云量数据源（ECMWF 主判 / GFS 复核）
  const mm = s.models || {};
  const mNames = Object.keys(mm);
  let crossTxt = '';
  if (mNames.length > 1) {
    const parts = mNames.map(n => `${n} ${mm[n].vis_prob}%`).join(' / ');
    const spreadTxt = s.model_spread != null ? `，差 ${s.model_spread}%` : '';
    const warn = (s.model_spread != null && s.model_spread >= 40)
      ? ' ⚠ 分歧大，视线云况不确定' : '';
    crossTxt = `<br>模式复核（同一交点几何，换云量源）：${parts}${spreadTxt}${warn}`;
  }
  note.innerHTML = `${occTxt} · 综合透射率 <b>${Math.round((s.trans || 0) * 100)}%</b>`
    + ` · 可见概率 <b>${w.vis_prob != null ? w.vis_prob + '%' : '-'}</b>`
    + (s.path_aod != null ? ` · 光路 AOD 均值 ${s.path_aod}` : '')
    + (s.vis_path_km != null ? ` · 光路能见度 ${s.vis_path_km} km` : '')
    + (s.data_gap ? ' · <b>气压层云量缺测，已回退机位云量</b>' : '')
    + crossTxt;

  tbl.innerHTML = '<tr><th>云层</th><th>层高</th><th>遮蔽云距</th>'
    + '<th>该处云量</th><th>地平线云距</th><th>最远无云距</th><th>判读</th></tr>'
    + ['low', 'mid', 'high'].map(b => {
      const bd = bands[b];
      const env = (r.sight_points && (r.sight_points.bands || [])
        .find(x => x.band === b) || {}).envelope_km;
      if (!bd) {
        return `<tr><td>${BAND[b]}</td><td>-</td><td>-</td><td>-</td>`
          + `<td>${env ? env[0] + ' km' : '-'}</td>`
          + `<td>${env ? env[2] + ' km' : '-'}</td>`
          + '<td style="color:var(--ink-2)">未检出云层</td></tr>';
      }
      const occ = bd.occ;
      const color = occ >= 70 ? '#c93a3f' : occ >= 40 ? '#a8720a' : '#1e8a4c';
      const word = occ >= 70 ? '会挡住天体' : occ >= 40 ? '薄云削弱对比度' : '基本通透';
      const e = bd.env_km || env || [];
      return `<tr>
        <td>${BAND[b]}</td>
        <td>${(bd.gh_m / 1000).toFixed(1)} km</td>
        <td><b>${bd.d_km} km</b></td>
        <td class="prob" style="color:${color}">${occ}%</td>
        <td>${e[0] != null ? e[0] + ' km' : '-'}</td>
        <td>${e[1] != null ? e[1] + ' km' : '-'}</td>
        <td style="color:${color}">${word}</td>
      </tr>`;
    }).join('');

  const levels = s.levels || [];
  lvlTbl.innerHTML = '<tr><th>气压层</th><th>海拔高度</th><th>分档</th>'
    + '<th>交点距离</th><th>采样点</th><th>云量</th><th>来源</th></tr>'
    + (levels.length ? levels.map(lv => {
      const ccColor = lv.cc == null ? 'var(--ink-2)'
        : lv.cc >= 70 ? '#c93a3f' : lv.cc >= 40 ? '#a8720a' : '#1e8a4c';
      const src = lv.cc == null ? '缺测'
        : (lv.cc_src === 'path' ? `远端 ${lv.sample_km}km` : '机位（回退）');
      const warn = lv.gap ? ' <span title="采样点与交点距离相差较大">⚠</span>' : '';
      return `<tr>
        <td>${lv.level} hPa</td>
        <td>${(lv.gh_m / 1000).toFixed(2)} km</td>
        <td>${BAND[lv.band]}</td>
        <td>${lv.d_km} km</td>
        <td>${lv.sample_km} km${warn}</td>
        <td class="prob" style="color:${ccColor}">${lv.cc == null ? '-' : lv.cc + '%'}</td>
        <td style="font-size:12px;color:var(--ink-2)">${src}</td>
      </tr>`;
    }).join('') : '<tr><td colspan="7" style="color:var(--ink-2)">无有效气压层数据</td></tr>');
}

function renderAlignDetail(w, r) {
  const card = document.getElementById('alignDetailCard');
  if (!card) return;
  if (!w) { card.style.display = 'none'; return; }
  if (!w.feasibility) {
    // 无天气预报：只展示几何事实 + 轨迹图
    card.style.display = 'block';
    const noWxNote = (r.meta && r.meta.weather_note)
      || '该窗口超出预报范围，临近再评估云况/通透度/降水。';
    document.getElementById('alignDetailTitle').textContent =
      `${w.type}·${w.period_name} ${(w.date || '').slice(5)} ${w.best_time}`
      + '（无天气数据）';
    document.getElementById('alignDims').innerHTML =
      '<div class="dim-cell" style="grid-column:1/-1;color:var(--ink-2)">'
      + noWxNote + '</div>';
    document.getElementById('alignModelTbl').innerHTML = '';
    document.getElementById('alignModelNote').textContent = '';
    const skyN = w.sky || {};
    document.getElementById('alignGeomGrid').innerHTML = [
      wxItem('高度角速率', w.alt_rate != null ? `${w.alt_rate}°/min` : '-'),
      wxItem('方位角速率', w.az_rate != null ? `${w.az_rate}°/min` : '-'),
      wxItem('视直径', w.angular_size_deg != null ? `${w.angular_size_deg}°` : '-'),
      wxItem('大气折射', w.refraction_deg != null ? `+${w.refraction_deg}°` : '-'),
      wxItem('视高度角', w.apparent_alt != null ? `${w.apparent_alt}°` : '-'),
      wxItem('框内停留', w.time_in_frame_min != null ? `${w.time_in_frame_min} min` : '-'),
      wxItem('山脊遮蔽角', w.horizon_alt != null
        ? `${w.horizon_alt}°${w.horizon_blocked ? '（遮挡）' : ''}` : '-'),
      wxItem('天空背景', `${skyN.twilight || '-'}（太阳 ${skyN.sun_alt}°）`),
    ].join('');
    document.getElementById('alignWxGrid').innerHTML = '';
    document.getElementById('alignRiskList').innerHTML =
      '<li>暂无天气风险数据（天气未取到）</li>';
    renderAlignTrackChart(w, r);
    return;
  }
  card.style.display = 'block';
  const f = w.feasibility;
  document.getElementById('alignDetailTitle').textContent =
    `${w.type}·${w.period_name} ${(w.date || '').slice(5)} ${w.best_time}`;
  const dims = [
    ['云况（视线交点遮挡）', f.dims.cloud],
    ['通透度（AOD/PM2.5）', f.dims.transparency],
    ['降水（概率）', f.dims.precip],
    ['稳定性（模式一致）', f.dims.stability],
  ];
  document.getElementById('alignDims').innerHTML = dims.map(([name, v]) =>
    `<div class="dim-cell">
       <div class="dim-cell-head"><span>${name}</span><b>${v}</b></div>
       <div class="dim-bar"><div style="width:${v}%;background:${scoreColor(v)}"></div></div>
     </div>`).join('');
  const models = w.models || {};
  const names = Object.keys(models);
  document.getElementById('alignModelTbl').innerHTML =
    `<tr><th>模式</th><th>低云</th><th>中云</th><th>高云</th>`
    + '<th>可见概率</th></tr>'
    + names.map(n => {
      const mm = models[n] || {}, c = mm.cloud || {};
      return `<tr><td><b>${n}</b></td><td>${c.low}%</td><td>${c.mid}%</td>
        <td>${c.high}%</td>
        <td class="prob" style="color:${probColor(mm.vis_prob || 0)}">${mm.vis_prob}%</td></tr>`;
    }).join('');
  const cons = w.consensus || {};
  document.getElementById('alignModelNote').textContent =
    `多模式共识可见概率 ${cons.vis_prob != null ? cons.vis_prob + '%' : '-'}`
    + `（${cons.credibility || '-'}`
    + `${cons.basis ? ' · ' + cons.basis : ''}`
    + `${cons.vis_prob_cam != null ? '；机位口径 ' + cons.vis_prob_cam + '%' : ''}）`;
  const sky = w.sky || {};
  const ob = r.observer || {}, tg = r.target || {};
  const distNow = alignTargetDistanceKm();
  document.getElementById('alignGeomGrid').innerHTML = [
    wxItem('高度角速率', w.alt_rate != null ? `${w.alt_rate}°/min` : '-'),
    wxItem('方位角速率', w.az_rate != null ? `${w.az_rate}°/min` : '-'),
    wxItem('视直径', w.angular_size_deg != null ? `${w.angular_size_deg}°` : '-'),
    wxItem('大气折射', w.refraction_deg != null ? `+${w.refraction_deg}°` : '-'),
    wxItem('视高度角', w.apparent_alt != null ? `${w.apparent_alt}°` : '-'),
    wxItem('框内停留', w.time_in_frame_min != null ? `${w.time_in_frame_min} min` : '-'),
    wxItem('机位高度', ob.height_m != null
      ? `${ob.height_m} m${ob.height_src === 'terrain' ? '（地形）' : ''}` : '-'),
    wxItem('可见地平线', ob.horizon_dip_deg
      ? `-${ob.horizon_dip_deg}°（下沉）` : '0°'),
    wxItem('目标高度/距', tg.height_m != null
      ? `${tg.height_m} m / ${distNow != null ? distNow.toFixed(2) + ' km' : '-'}`
      : (distNow != null ? `— / ${distNow.toFixed(2)} km` : '-')),
    wxItem('目标仰角', tg.elev_geo_deg != null
      ? `${tg.elev_geo_deg}°（视 ${tg.elev_apparent_deg}°，`
        + `${(r.geometry || {}).elev_source === 'target' ? '由高度反推' : '手动'}）`
      : '-'),
    wxItem('山脊遮蔽角', w.horizon_alt != null
      ? `${w.horizon_alt}°${w.horizon_blocked ? '（遮挡）' : ''}` : '-'),
    wxItem('天空背景', `${sky.twilight || '-'}（太阳 ${sky.sun_alt}°）`),
  ].join('');
  const m = r.main || {}, wx = m.weather || {};
  document.getElementById('alignWxGrid').innerHTML = [
    wxItem('AOD 550nm', fmt(m.aod_local)),
    wxItem('PM2.5', fmt(m.pm25, '', ' μg/m³')),
    wxItem('能见度', fmt(wx.visibility_km, '', ' km')),
    wxItem('相对湿度', fmt(wx.humidity, '%')),
    wxItem('风速', fmt(wx.wind, '', ' km/h')),
    wxItem('降水概率', fmt(wx.precip_prob, '%')),
    wxItem('气温', fmt(wx.temp, '', '°C')),
    wxItem('颜色区间', (m.quality || {}).color || '-'),
  ].join('');
  const risks = w.risks || [];
  document.getElementById('alignRiskList').innerHTML = risks.length
    ? risks.map(x => `<li>${x}</li>`).join('')
    : '<li>暂无明显风险</li>';
  renderAlignTrackChart(w, r);
}

function renderAlignTrackChart(w, r) {
  const el = document.getElementById('alignTrackChart');
  if (!el || !r.tracks || typeof echarts === 'undefined') return;
  const ch = window._alignTrackChart && !window._alignTrackChart.isDisposed()
    ? window._alignTrackChart : echarts.init(el);
  window._alignTrackChart = ch;
  const trk = r.tracks;
  const isSun = w.body === 'sun';
  const altArr = isSun ? trk.sun_alt : trk.moon_alt;
  const azArr = isSun ? trk.sun_az : trk.moon_az;
  const base = h2m(w.best_time);
  const times = [], alts = [], azs = [];
  let baseIdx = 0;
  for (let off = -30; off <= 30; off += 2) {
    const m = base + off;
    const a = interpTrkMin(trk.time, altArr, m);
    const z = interpTrkMin(trk.time, azArr, m);
    if (off === 0) baseIdx = times.length;
    times.push(minToHM(m));
    alts.push(a == null ? null : +(+a).toFixed(2));
    azs.push(z == null ? null : +(+z).toFixed(2));
  }
  const ink = chartInk();
  ch.setOption({
    tooltip: chartTip({trigger: 'axis'}),
    legend: {data: ['高度角', '方位角'], top: 0,
             textStyle: {fontSize: 11, color: ink.ink2}},
    grid: {left: 48, right: 48, top: 30, bottom: 26},
    xAxis: Object.assign({type: 'category', data: times}, axisInk(ink),
            {axisLabel: {fontSize: 10, interval: 4, color: ink.ink2}}),
    yAxis: [
      Object.assign({type: 'value', name: '高度角°',
                     nameTextStyle: {fontSize: 10, color: ink.ink2}},
                    axisInk(ink), {axisLine: {show: false}}),
      Object.assign({type: 'value', name: '方位角°',
                     nameTextStyle: {fontSize: 10, color: ink.ink2}},
                    axisInk(ink), {axisLine: {show: false},
                                   splitLine: {show: false}}),
    ],
    series: [
      {name: '高度角', type: 'line', data: alts, smooth: true, showSymbol: false,
       lineStyle: {color: '#e8973a', width: 2.2},
       areaStyle: {color: areaGrad('rgba(232,151,58,.26)', 'rgba(232,151,58,0)')},
       markLine: {silent: true, symbol: 'none',
                  label: {show: false},
                  data: [
                    {yAxis: (w.best_alt || 0) + 2,
                     lineStyle: {color: '#2e9e44', type: 'dashed'}},
                    {yAxis: (w.best_alt || 0) - 2,
                     lineStyle: {color: '#2e9e44', type: 'dashed'}},
                  ]},
       markPoint: {symbolSize: 10,
                   itemStyle: {color: '#c93a3f'},
                   label: {show: true, formatter: '对准', fontSize: 10,
                           position: 'top', color: '#c93a3f'},
                   data: [{coord: [baseIdx, w.best_alt || 0]}]}},
      {name: '方位角', type: 'line', yAxisIndex: 1, data: azs, smooth: true,
       showSymbol: false, lineStyle: {color: '#3a6ea5', width: 1.5}},
    ],
  });
}

// ---------------- 方向天气分析：可视化 ----------------

// 图表配色读 CSS 变量，深浅色主题都能跟随（themechange 会触发整页重绘）
function chartInk() {
  const cs = getComputedStyle(document.documentElement);
  const v = (k, fb) => (cs.getPropertyValue(k) || '').trim() || fb;
  return {axis: v('--chart-axis', '#888'), ink: v('--ink', '#222'),
          ink2: v('--ink-2', '#777'), line: v('--line', '#ddd'),
          card: v('--card-bg', '#fff'), area: v('--chart-geo-area', '#f6f6f7'),
          border: v('--chart-geo-border', '#ccc')};
}

/* 提示框：玻璃质感 + 柔和投影，深浅主题与四套皮肤都跟随 */
function chartTip(extra) {
  const k = chartInk();
  return Object.assign({
    backgroundColor: k.card, borderColor: k.line, borderWidth: 1,
    padding: [8, 12], textStyle: {color: k.ink, fontSize: 12},
    extraCssText: 'border-radius:10px;'
      + 'box-shadow:0 14px 34px -14px rgba(0,0,0,.5);',
  }, extra || {});
}

/* 坐标轴：虚线网格 + 去刻度线，取色走主题变量 */
function axisInk(k) {
  return {
    axisLine: {lineStyle: {color: k.line}},
    axisTick: {show: false},
    axisLabel: {fontSize: 10, color: k.ink2},
    splitLine: {lineStyle: {color: k.line, type: 'dashed', opacity: .55}},
  };
}

/* 柔性面积渐变：让主曲线下方是一层光晕而不是硬边填充 */
function areaGrad(top, bottom) {
  return new echarts.graphic.LinearGradient(0, 0, 0, 1, [
    {offset: 0, color: top}, {offset: 1, color: bottom},
  ]);
}

// 天空实景预览：把事件时刻当作"伪窗口"，复用取景预览渲染器。
// 取景框中心 = 目标方位/目标仰角；天体按事件时刻真实位置绘制 -> 框外即拍不到。
function renderAlignDirSky(dw, r) {
  const cv = document.getElementById('alignDirSky');
  if (!cv) return;
  if (typeof renderEventPreview !== 'function' || !dw) {
    cv.style.display = 'none';
    return;
  }
  const meta = r.alignment_meta || {};
  const geom = r.geometry || {};
  const isSun = alignState.body === 'sun';
  const evKey = `${alignState.body}${alignState.period === 'rise' ? 'rise' : 'set'}`;
  const ev = (r.day_events || []).find(e => e.key === evKey) || null;
  const s = dw.sight || null;
  // 有逐层视线结果时，云量与"该层在何方"都用视线口径（与主判定一致）
  const cloudDir = (s && s.bands && Object.keys(s.bands).length)
    ? {low: (s.bands.low || {}).occ || 0, mid: (s.bands.mid || {}).occ || 0,
       high: (s.bands.high || {}).occ || 0}
    : (dw.cloud || null);
  const pseudo = {
    type: isSun ? '悬日' : '悬月',
    body: isSun ? 'sun' : 'moon',
    period_name: `${isSun ? '太阳' : '月亮'}`,
    // 天体位置 = 事件时刻真实方位/高度；取景框中心 = 目标方位/目标仰角。
    // 两者解耦后，"天体落在框外"就是拍不到的直接证据。
    best_az: ev ? ev.az : meta.target_az,
    best_alt: ev ? ev.alt : (geom.elev != null ? geom.elev : 0),
    frame_az: meta.target_az,
    frame_alt: (geom.elev != null ? geom.elev : 0),
    best_time: dw.time,
    date: r.meta.date,
    cloud_at: cloudDir,
    cloud_locked: true,   // 用沿方位采样的云量，别被机位逐时云覆盖
    illum: (r.astronomy || {}).moon_illum,
    sight: s,             // 逐层视线口径：云带按真实交点距离/层高落位
    vis_prob: dw.vis_prob,
  };
  // 让 AOD 用当日事件时刻的值（原渲染器读 r.main.aod_local）
  const r2 = Object.assign({}, r, {
    main: Object.assign({}, r.main || {}, {aod_local: dw.aod}),
  });
  renderEventPreview('alignDirSky', pseudo, r2);
  cv.style.display = 'block';
  const hint = document.getElementById('alignDirSkyHint');
  if (hint) {
    const inside = ev && ev.daz <= (meta.tol || 0);
    hint.innerHTML = `绿色取景框 = 目标方位 ${meta.target_az}° ± 视场 ${meta.aov}°`
      + `（框外只是参照）；${r.meta.date} ${dw.time} 的${isSun ? '太阳' : '月亮'}`
      + `按真实角径与位置绘制`
      + (ev ? `（方位 ${ev.az}°，与目标差 <b>${ev.daz}°</b>` : '')
      + (ev ? `，${inside ? '<span style="color:#1e8a4c">在框内</span>'
        : '<span style="color:#c93a3f">在框外</span>'}）` : '')
      + `；云带落在该层视线交点的真实仰角上，透明度取沿该方位的`
      + `${s ? '逐层视线' : '三层代表'}云量（`
      + `${cloudDir ? `低${cloudDir.low}/中${cloudDir.mid}/高${cloudDir.high}%` : '-'}）。`;
  }
}

// 当日轨迹 + 目标取景带：把"差多少"从数字变成一眼可见的错过
function renderAlignDirTrack(dw, r) {
  const el = document.getElementById('alignDirTrack');
  if (!el || !r.tracks || typeof echarts === 'undefined') return;
  const ch = window._adrTrack && !window._adrTrack.isDisposed()
    ? window._adrTrack : echarts.init(el);
  window._adrTrack = ch;
  const trk = r.tracks;
  const isSun = alignState.body === 'sun';
  const altArr = isSun ? trk.sun_alt : trk.moon_alt;
  const azArr = isSun ? trk.sun_az : trk.moon_az;
  const meta = r.alignment_meta || {};
  const tol = meta.tol != null ? meta.tol : 2;
  const ink = chartInk();

  // 数值时间轴（分钟 0-1440）：markPoint/markArea 坐标恒有效，
  // 不会因事件时刻（06:12）不在 10 分钟类目上而解析失败。
  const alts = [], azs = [];
  for (let m = 0; m <= 1440; m += 10) {
    const a = interpTrkMin(trk.time, altArr, m);
    const z = interpTrkMin(trk.time, azArr, m);
    alts.push([m, a == null ? null : +(+a).toFixed(2)]);
    azs.push([m, z == null ? null : +(+z).toFixed(2)]);
  }
  // 天体落在目标方位带内的时间段（az 与目标差 <= tol）
  const bands = [];
  let start = null;
  azs.forEach(([m, z]) => {
    const daz = z == null ? 999
      : Math.abs(((z - meta.target_az + 540) % 360) - 180);
    const inside = daz <= tol;
    if (inside && start === null) start = m;
    if (!inside && start !== null) {
      bands.push([{xAxis: start}, {xAxis: m - 10}]);
      start = null;
    }
  });
  if (start !== null) bands.push([{xAxis: start}, {xAxis: 1440}]);
  const marks = [];
  const ev = (r.day_events || []).find(e =>
    e.key === `${isSun ? 'sun' : 'moon'}${alignState.period === 'rise' ? 'rise' : 'set'}`);
  if (ev) marks.push({coord: [h2m(ev.time), ev.alt], value: `${ev.name} 差${ev.daz}°`,
                      itemStyle: {color: '#c93a3f'}});
  const best = (r.day_events || [])
    .filter(e => (isSun ? e.key.indexOf('sun') === 0 : e.key.indexOf('moon') === 0))
    .reduce((a, b) => (!a || b.daz < a.daz ? b : a), null);
  if (best && (!ev || best.key !== ev.key)) {
    marks.push({coord: [h2m(best.time), best.alt], value: `最近 ${best.daz}°`,
                itemStyle: {color: '#a8720a'}});
  }

  ch.setOption({
    tooltip: chartTip({trigger: 'axis',
      formatter: ps => {
        const m = ps && ps[0] ? ps[0].value[0] : 0;
        return minToHM(m) + '<br/>' + ps.map(p =>
          `${p.marker}${p.seriesName} ${p.value[1]}°`).join('<br/>');
      }}),
    legend: {data: ['高度角', '方位角'], top: 0,
             textStyle: {fontSize: 11, color: ink.ink2}},
    grid: {left: 46, right: 48, top: 28, bottom: 24},
    xAxis: {type: 'value', min: 0, max: 1440, interval: 120,
            axisLabel: {fontSize: 10, color: ink.ink2,
                        formatter: v => minToHM(v)},
            axisLine: {lineStyle: {color: ink.line}},
            splitLine: {lineStyle: {color: ink.line, opacity: .3}}},
    yAxis: [
      {type: 'value', name: '高度°', axisLabel: {fontSize: 10, color: ink.ink2},
       splitLine: {lineStyle: {color: ink.line, opacity: .4}}},
      {type: 'value', name: '方位°', min: 0, max: 360,
       axisLabel: {fontSize: 10, color: ink.ink2}, splitLine: {show: false}},
    ],
    series: [
      {name: '高度角', type: 'line', data: alts, smooth: true, showSymbol: false,
       lineStyle: {color: '#e8973a', width: 2},
       markArea: bands.length ? {silent: true,
         itemStyle: {color: 'rgba(46,158,68,.14)'},
         label: {show: true, formatter: '目标方位内', fontSize: 10, color: '#2e9e44'},
         data: bands} : undefined,
       markLine: Number.isFinite(meta.alt_lo) && Number.isFinite(meta.alt_hi)
         ? {silent: true, symbol: 'none', label: {show: false},
            data: [{yAxis: meta.alt_lo, lineStyle: {color: '#2e9e44', type: 'dashed'}},
                   {yAxis: meta.alt_hi, lineStyle: {color: '#2e9e44', type: 'dashed'}}]}
         : undefined,
       markPoint: marks.length ? {symbolSize: 8, label: {fontSize: 10,
         color: ink.ink, position: 'top'}, data: marks} : undefined},
      {name: '方位角', type: 'line', yAxisIndex: 1, data: azs, smooth: true,
       showSymbol: false, lineStyle: {color: '#3a6ea5', width: 1.4},
       markLine: Number.isFinite(meta.target_az)
         ? {silent: true, symbol: 'none',
            label: {show: true, formatter: `目标 ${meta.target_az}°`,
                    fontSize: 10, color: '#3a6ea5'},
            data: [{yAxis: meta.target_az,
                    lineStyle: {color: '#3a6ea5', type: 'dotted'}}]}
         : undefined},
    ],
  });
  const note = document.getElementById('alignDirTrackNote');
  if (note) {
    note.textContent = bands.length
      ? `绿带 = 天体落在目标方位 ±${tol}° 内的时间段`
      : `当日天体始终未进入目标方位 ±${tol}°（绿带不出现即无对准可能）`;
  }
}

// 模式一致性点图：各模式可见概率 + 共识均值/离散带 + 机位口径对照
function renderAlignDirModelChart(dw) {
  const el = document.getElementById('alignDirModelChart');
  if (!el || typeof echarts === 'undefined') return;
  const ch = window._adrModel && !window._adrModel.isDisposed()
    ? window._adrModel : echarts.init(el);
  window._adrModel = ch;
  const models = dw.models || {};
  const cons = dw.consensus || {};
  const names = Object.keys(models);
  if (!names.length) {
    ch.clear();
    return;
  }
  const ink = chartInk();
  const pts = names.map((n, i) => ({
    value: [models[n].vis_prob, i],
    name: n,
    itemStyle: {color: scoreColor(models[n].vis_prob)},
  }));
  if (cons.vis_prob_cam != null) {
    pts.push({value: [cons.vis_prob_cam, names.length],
              name: '机位口径', itemStyle: {color: ink.ink2,
              borderColor: '#fff', borderWidth: 1}});
  }
  const mu = cons.vis_prob != null ? cons.vis_prob : 0;
  const sd = cons.std != null ? cons.std : 0;
  ch.setOption({
    tooltip: chartTip({trigger: 'item',
      formatter: p => `${p.data.name}：可见概率 ${p.value[0]}%`}),
    grid: {left: 66, right: 24, top: 14, bottom: 26},
    xAxis: {type: 'value', min: 0, max: 100, name: '可见概率 %',
            nameTextStyle: {fontSize: 10, color: ink.ink2},
            axisLabel: {fontSize: 10, color: ink.ink2},
            splitLine: {lineStyle: {color: ink.line, opacity: .4}}},
    yAxis: {type: 'category',
            data: names.concat(cons.vis_prob_cam != null ? ['机位口径'] : []),
            axisLabel: {fontSize: 11, color: ink.ink2},
            axisLine: {lineStyle: {color: ink.line}}},
    series: [{
      type: 'scatter', symbolSize: 13, data: pts,
      label: {show: true, position: 'right', fontSize: 10, color: ink.ink,
              formatter: p => `${p.value[0]}%`},
      markLine: {silent: true, symbol: 'none',
        data: [{xAxis: mu, lineStyle: {color: '#3a6ea5', width: 2},
                label: {show: true, formatter: `共识 ${mu}%`, fontSize: 10,
                        color: '#3a6ea5', position: 'insideEndTop'}}]},
      markArea: sd ? {silent: true,
        itemStyle: {color: 'rgba(58,110,165,.12)'},
        data: [[{xAxis: Math.max(0, mu - sd)}, {xAxis: Math.min(100, mu + sd)}]]}
        : undefined,
    }],
  });
}

// 视线剖面断面图：横轴=沿视线的地表距离，纵轴=海拔。
// 画地球曲率地面线、视线射线、可见地平线下沉、各云层带（颜色/透明度按云量）、
// 遮挡交点，并把被云挡住的视线段高亮。无 sight 时退化为三层代表云简版。
function renderAlignDirSection(dw, r) {
  const el = document.getElementById('alignDirSection');
  const hint = document.getElementById('alignDirSectionHint');
  if (!el || typeof echarts === 'undefined') return;
  const ch = window._adrSection && !window._adrSection.isDisposed()
    ? window._adrSection : echarts.init(el);
  window._adrSection = ch;
  const s = dw.sight;
  const ob = r.observer || {};
  const camH = (ob.height_m || 0) / 1000;          // km
  const R = 6371.0;
  const ink = chartInk();
  const BAND_C = {low: '#e8c33a', mid: '#e8973a', high: '#c9433a'};
  const BAND_N = {low: '低云', mid: '中云', high: '高云'};

  // ---- 视线射线（精确球面关系，与后端 sightline 同口径）----
  const theta = (s && s.apparent_alt != null) ? s.apparent_alt
    : (alignState.body === 'sun' ? -0.3 : 0.6);
  const ray = [];
  const ground = [];
  const dMax = s && s.levels && s.levels.length
    ? Math.min(900, Math.max(120, Math.ceil(
        Math.max.apply(null, s.levels.map(x => x.d_km || 0)) * 1.25 / 50) * 50))
    : 420;
  const alt = theta * Math.PI / 180;
  const r0 = R + camH;
  // 射线在弧距 d 处相对海平面的高度：由 t = r0·sinφ/cos(φ+θ) 求交点半径，
  // φ = d/R 为地心角（与 sightline 的弦长解法等价，已验证到 0.01 km）。
  const rayHeightKm = d => {
    const phi = d / R;
    const c = Math.cos(phi + alt);
    if (c <= 1e-6) return null;
    const t = r0 * Math.sin(phi) / c;
    const rr = Math.sqrt(r0 * r0 + 2 * r0 * t * Math.sin(alt) + t * t);
    return rr - R;
  };
  for (let d = 0; d <= dMax; d += dMax / 120) {
    ground.push([+d.toFixed(1), +(camH - d * d / (2 * R)).toFixed(2)]);
    const h = rayHeightKm(d);
    ray.push([+d.toFixed(1), h == null ? null : +h.toFixed(2)]);
  }

  // ---- 云层：逐气压层（细条）或三层代表云（退化）----
  const bands = [];
  const hits = [];
  let levels = (s && s.levels) || [];
  if (levels.length) {
    levels.forEach(lv => {
      if (lv.gh_m == null) return;
      const hkm = lv.gh_m / 1000;
      const occ = lv.cc;
      const d = lv.d_km;
      if (d == null) return;
      const w = Math.max(18, d * 0.16);          // 带宽随距离略增，便于阅读
      bands.push({name: `${lv.level}hPa ${BAND_N[lv.band]}`, kind: 'level',
        lv, hkm, occ, d, from: Math.max(0, d - w / 2), to: d + w / 2});
    });
  } else {
    const dists = dw.dir_distances_km || [140, 200, 310];
    const H = {low: 1.5, mid: 3.2, high: 7.6};
    const c = dw.cloud || {};
    [['low', 0], ['mid', 1], ['high', 2]].forEach(([b, i]) => {
      bands.push({name: `${BAND_N[b]}（代表层 ${H[b]}km）`, kind: 'band',
        hkm: H[b], occ: c[b], d: dists[i],
        from: 0, to: dMax});
    });
  }
  // 分档交点（低/中/高各取该档云量最大者）
  if (s && s.bands) {
    Object.keys(s.bands).forEach(b => {
      const bd = s.bands[b];
      if (!bd || bd.d_km == null) return;
      hits.push({value: [bd.d_km, (bd.gh_m || 0) / 1000], name: BAND_N[b],
        itemStyle: {color: BAND_C[b]},
        label: {show: true, formatter: `${BAND_N[b]} ${bd.occ}%`,
                fontSize: 10, color: ink.ink, position: 'top'}});
    });
  }

  // ---- 遮挡段：视线穿过云量 >= 60% 的层高区间时高亮（用同一精确射线函数）----
  const blocks = [];
  bands.filter(b => (b.occ || 0) >= 60).forEach(b => {
    let dIn = null, dOut = null;
    for (let d = Math.max(0, b.from - 8); d <= Math.min(dMax, b.to + 8); d += 1) {
      const h = rayHeightKm(d);
      if (h == null) continue;
      if (Math.abs(h - b.hkm) < 0.25) {
        if (dIn === null) dIn = d;
        else dOut = d;
      }
    }
    if (dIn !== null) {
      blocks.push([{xAxis: dIn},
                   {xAxis: dOut == null ? Math.min(dMax, dIn + 30) : dOut}]);
    }
  });

  // 云层带与遮挡段都用 markArea 画矩形（比 custom series 稳，且每块可独立配色）
  const bandColor = b => BAND_C[b.kind === 'level' ? b.lv.band
    : ((b.name || '').indexOf('低') >= 0 ? 'low'
      : (b.name || '').indexOf('中') >= 0 ? 'mid' : 'high')];
  const areas = bands.map(b => ([
    {coord: [b.from, Math.max(0, b.hkm - 0.34)],
     itemStyle: {color: bandColor(b),
                 opacity: 0.18 + 0.6 * Math.min(1, (b.occ || 0) / 100)}},
    {coord: [b.to, b.hkm + 0.34]},
  ]));
  blocks.forEach(bk => areas.push([
    {coord: [bk[0].xAxis, 0],
     itemStyle: {color: 'rgba(201,58,63,.16)'}},
    {coord: [bk[1].xAxis, 13]},
  ]));

  const series = [
    {name: '视线', type: 'line', data: ray, showSymbol: false, z: 6,
     lineStyle: {color: '#e8973a', width: 2.4},
     markArea: areas.length ? {silent: true, data: areas} : undefined},
    {name: '地面（含曲率）', type: 'line', data: ground, showSymbol: false,
     lineStyle: {color: ink.ink2, width: 1.4, type: 'dotted'}, silent: true},
  ];
  if (hits.length) {
    series.push({name: '交点', type: 'scatter', data: hits, symbolSize: 10,
      z: 7, tooltip: {formatter: p =>
        `${p.data.name}：交点 ${p.value[0]} km / 海拔 ${p.value[1]} km`}});
  }
  if (blocks.length) {
    series[0].markArea.label = {show: true, formatter: '被云遮挡',
      fontSize: 10, color: '#c93a3f', position: 'insideTop'};
  }

  ch.setOption({
    tooltip: chartTip({trigger: 'axis', confine: true,
                       axisPointer: {type: 'cross'}}),
    legend: {data: ['视线', '地面（含曲率）'], top: 0,
             textStyle: {fontSize: 11, color: ink.ink2}},
    grid: {left: 52, right: 20, top: 30, bottom: 34},
    xAxis: {type: 'value', name: '距机位 km', min: 0, max: dMax,
            nameTextStyle: {fontSize: 10, color: ink.ink2},
            axisLabel: {fontSize: 10, color: ink.ink2},
            splitLine: {lineStyle: {color: ink.line, opacity: .35}}},
    yAxis: {type: 'value', name: '海拔 km', min: 0, max: 13,
            nameTextStyle: {fontSize: 10, color: ink.ink2},
            axisLabel: {fontSize: 10, color: ink.ink2},
            splitLine: {lineStyle: {color: ink.line, opacity: .35}}},
    series,
  });

  if (hint) {
    if (s && s.visible) {
      const bs = s.bands || {};
      const worst = Object.keys(bs).sort((a, b) => bs[b].occ - bs[a].occ)[0];
      hint.innerHTML = `视线按视高度角 ${s.apparent_alt}° 绘制（含折射），`
        + `机位 ${ob.height_m || 0} m 使可见地平线下沉 ${s.dip_deg || 0}°；`
        + `橙线为视线，虚线为含曲率的地面，色带为云层（越深云量越大）。`
        + (worst ? ` 挡得最重的是${BAND_N[worst]}：`
          + `交点 ${bs[worst].d_km} km 处云量 ${bs[worst].occ}%。` : '')
        + (dw.vis_prob_agg != null
          ? ` 逐层透射 ${s.vis_prob}%，三层代表云口径为 ${dw.vis_prob_agg}%。` : '');
    } else if (s && !s.visible) {
      hint.textContent = `天体上缘视高度角 ${s.limb_alt}° 低于可见地平线`
        + `（机位下沉 ${s.dip_deg || 0}°），当日该时刻天体还在地平线下。`;
    } else {
      hint.textContent = '逐层视线数据不可用（天气请求受限），'
        + '上图用三层代表云（层高 1.5/3.2/7.6 km）与包络距离近似绘制。';
    }
  }
}

function renderAlignWeek(r) {
  const card = document.getElementById('alignWeekCard');
  if (!card) return;
  const outlook = r.outlook || [];
  if (!outlook.length) { card.style.display = 'none'; return; }
  card.style.display = 'block';
  const key = `${alignState.body}`
    + `${alignState.period === 'rise' ? 'rise' : 'set'}`;
  const alignDates = {};
  (r.week || []).forEach(d => {
    const w = (d.windows || {})[key];
    if (w) alignDates[d.date] = w;
  });
  const meta = r.alignment_meta || {};
  const nowIso = ((r.meta || {}).now || '').replace(' ', 'T');
  document.getElementById('alignWeekNote').textContent =
    `未来 7 天方向天气 · ${alignState.body === 'sun' ? '悬日' : '悬月'}`
    + `·${alignState.period === 'rise' ? '出' : '落'} · 目标 ${meta.target_az}°`;
  document.getElementById('alignWeekGrid').innerHTML = outlook.map(d => {
    const ew = (d.events || {})[key];
    const aw = alignDates[d.date];
    const awPast = !!(aw && nowIso && `${d.date}T${aw.best_time}` < nowIso);
    const badge = aw
      ? `<div class="v" style="color:${awPast ? 'var(--ink-2)'
                                               : 'var(--accent)'}">`
        + `🎯 对准 ${aw.best_time}${awPast ? ' · 已过' : ''}</div>`
      : '';
    if (!ew) {
      return `<div class="week-cell"><div class="d">${d.date.slice(5)} ${d.weekday}</div>`
        + '<div class="v">无数据</div>' + badge + '</div>';
    }
    const color = probColor(ew.vis_prob || 0);
    return `<div class="week-cell" data-date="${d.date}" role="button"
      tabindex="0" aria-label="${d.date} ${esc(d.weekday)} 可见概率 ${ew.vis_prob}%">
      <div class="d">${d.date.slice(5)} ${d.weekday}</div>
      <div class="p" style="color:${color}">${ew.vis_prob}%</div>
      <div class="v">${ew.time} · ${ew.verdict}</div>
      ${badge}
    </div>`;
  }).join('');
  document.querySelectorAll('#alignWeekGrid .week-cell').forEach(cell => {
    if (!cell.dataset.date) return;
    cell.style.cursor = 'pointer';
    cell.onclick = () => {
      document.getElementById('dateSel').value = cell.dataset.date;
      syncDateUI();
      loadAlign(true);
    };
  });
}


let _alignNcKey = '';

async function loadAlignNowcast(w, r) {
  const card = document.getElementById('alignNowcastCard');
  if (!card) return;
  if (!w) { card.style.display = 'none'; return; }
  const ev = new Date(`${w.date}T${w.best_time}:00`);
  const mins = (ev.getTime() - Date.now()) / 60000;
  // 仅事件前后窗口内拉取卫星（-90 ~ +360 分钟）
  if (mins < -90 || mins > 360) { card.style.display = 'none'; return; }
  const key = `${w.date}|${w.best_time}|${w.best_az}|${w.body}`;
  if (key === _alignNcKey && card.dataset.loaded === '1') {
    card.style.display = 'block';
    return;
  }
  _alignNcKey = key;
  card.style.display = 'block';
  card.dataset.loaded = '0';
  document.getElementById('alignNcMeta').textContent = '正在拉取最新卫星帧…';
  try {
    const c = alignCoords();
    const type = alignState.period === 'rise' ? 'sunrise' : 'sunset';
    const res = await fetch(`${API}/api/nowcast?lat=${c.lat}&lon=${c.lon}`
      + `&date=${w.date}&type=${type}`
      + `&time=${w.best_time}&azimuth=${w.best_az}&body=${w.body}`);
    if (!res.ok) throw new Error(await res.text());
    const nc = await res.json();
    renderAlignNowcast(nc, w);
    card.dataset.loaded = '1';
  } catch (e) {
    document.getElementById('alignNcMeta').textContent =
      '卫星数据获取失败：' + e.message;
  }
}

function renderAlignNowcast(nc, w) {
  const meta = document.getElementById('alignNcMeta');
  const img = document.getElementById('alignNcImg');
  const list = document.getElementById('alignNcJudgments');
  if (!nc || !nc.available) {
    meta.textContent = `当前不可用：${(nc && nc.reason) || '卫星数据缺失'}`;
    img.style.display = 'none';
    list.innerHTML = '';
    hideSatTimeline('alignNc');
    return;
  }
  const dt = nc.minutes_to_event >= 0
    ? `距${w.period_name}还有 ${Math.floor(nc.minutes_to_event / 60)}h`
      + `${Math.round(nc.minutes_to_event % 60)}min`
    : `${w.period_name}已过 ${Math.floor(-nc.minutes_to_event / 60)}h`
      + `${Math.round(-nc.minutes_to_event % 60)}min`;
  const modeTxt = nc.mode === 'ptree5km' ? ' · P-Tree 5km 亮温' : '';
  meta.innerHTML = `卫星时次 BJT <b>${nc.sat_time_bjt}</b>`
    + `（${nc.age_min} 分钟前） · ${dt}`
    + ` · 光路云量 <b>${Math.round((nc.cloudy_frac || 0) * 100)}%</b>${modeTxt}`;
  const ptCrop = (nc.ptree && nc.ptree.crop_png) ? nc.ptree.crop_png : null;
  const src = (nc.mode === 'ptree5km' && ptCrop) ? ptCrop : nc.crop_png;
  if (src) { img.src = src; img.style.display = 'block'; }
  else img.style.display = 'none';
  list.innerHTML = (nc.judgments || []).map(j => `<li>${j}</li>`).join('');
  renderSatHint('alignNcHint', nc, w.period_name);
  const c = alignCoords();
  initSatTimeline({
    key: 'alignNc', lat: c.lat, lon: c.lon, az: w.best_az,
    latest: nc.sat_time_bjt, ageMin: nc.age_min,
    label: `${w.period_name} ${w.best_time}`,
    minutesToEvent: nc.minutes_to_event,
  });
}


// ---------------- 卫星帧时间轴（10 分钟槽位，看云带移动） ----------------
// 每拖动一帧只 1 次上游请求，且后端按时次缓存：来回拖同一帧为 0 请求。
// 帧时刻如实回报（请求的槽位可能尚未发布，后端会回退），不假装是想要的那帧。
const _satTl = {};

function hideSatTimeline(key) {
  const ctrl = document.getElementById(key + 'Ctrl');
  if (ctrl) ctrl.style.display = 'none';
  stopSatPlay(key);
  delete _satTl[key];
}

function stopSatPlay(key) {
  const st = _satTl[key];
  if (st && st.timer) { clearInterval(st.timer); st.timer = null; }
  const play = document.getElementById(key + 'Play');
  if (play) setPlayState(play, false);
}

function initSatTimeline(o) {
  const ctrl = document.getElementById(o.key + 'Ctrl');
  const slider = document.getElementById(o.key + 'Slider');
  const label = document.getElementById(o.key + 'Slot');
  const play = document.getElementById(o.key + 'Play');
  const img = document.getElementById(o.key + 'Img');
  if (!ctrl || !slider || !o.lat || !o.az) return;
  const prev = _satTl[o.key];
  const sameKey = prev && prev.lat === o.lat && prev.lon === o.lon
    && prev.az === o.az && prev.label === o.label;
  let st;
  if (sameKey) {
    // 复用同一状态对象：重新初始化不能把 req 归零，否则在途的取帧
    // 请求会被判成"过期"而静默丢弃（表现为拖了没反应）。
    st = prev;
  } else {
    stopSatPlay(o.key);
    st = {lat: o.lat, lon: o.lon, az: o.az, label: o.label, req: 0,
          timer: null, slots: null};
    _satTl[o.key] = st;
  }
  ctrl.style.display = 'flex';
  if (!sameKey) {          // 换了机位/方位/事件：回到最新帧
    slider.value = '0';
    if (label) {
      label.textContent = o.latest
        ? `最新 ${o.latest}${o.ageMin != null ? `（${o.ageMin} 分钟前）` : ''}`
        : '最新帧';
    }
  }
  const slotText = (i, nc) => {
    if (nc) {
      const d = nc.age_min != null ? `（${nc.age_min} 分钟前）` : '';
      return `BJT ${nc.slot_bjt || nc.sat_time_bjt}${d}`;
    }
    // 还没拉到时先按 10 分钟步长推算，并标明是估算
    const base = o.latest || '';
    if (!base) return `-${i * 10} 分钟`;
    const mm = base.split(':');
    let t = (+mm[0]) * 60 + (+mm[1]) - i * 10;
    t = ((t % 1440) + 1440) % 1440;
    return `≈${String(Math.floor(t / 60)).padStart(2, '0')}:`
      + `${String(t % 60).padStart(2, '0')}（估算）`;
  };
  slider.oninput = () => {
    stopSatPlay(o.key);
    const i = +slider.value;
    if (label) label.textContent = slotText(i, null);
    loadSatFrame(o, i, img, label, slotText);
  };
  if (play) {
    play.onclick = () => {
      const st = _satTl[o.key];
      if (!st) return;
      if (st.timer) { stopSatPlay(o.key); return; }
      setPlayState(play, true);
      st.timer = setInterval(() => {
        let v = +slider.value + 1;
        if (v > +slider.max) v = 0;
        slider.value = String(v);
        slider.oninput();
      }, 900);
    };
  }
}

async function loadSatFrame(o, back, img, label, slotText) {
  const st = _satTl[o.key];
  if (!st) return;
  const slider = document.getElementById(o.key + 'Slider');
  const req = ++st.req;
  try {
    const res = await fetch(`${API}/api/sat_frame?lat=${o.lat}&lon=${o.lon}`
      + `&azimuth=${o.az}&back=${back}`);
    if (!res.ok) throw new Error('HTTP ' + res.status);
    const nc = await res.json();
    if (req !== st.req || !_satTl[o.key]) return;   // 已有更新的拖动
    if (Array.isArray(nc.slots) && nc.slots.length && slider) {
      st.slots = nc.slots;
      slider.max = String(nc.slots.length - 1);
    }
    if (label) label.textContent = nc.available
      ? slotText(back, nc) : `BJT ${nc.slot_bjt || '-'} 无帧`;
    if (!nc.available) return;
    if (nc.crop_png && img) { img.src = nc.crop_png; img.style.display = 'block'; }
    renderSatHint(o.key + 'Hint', nc, o.label);
  } catch (e) {
    if (req !== st.req) return;
    if (label) label.textContent = '取帧失败：' + e.message;
  }
}

// 如实标注：分辨率、时间差、光路云况。JMA 601px 全圆盘在本地约 26km/像素，
// 说清楚才不会让人把它的细节当 5km 的 P-Tree 用。
function renderSatHint(id, nc, label) {
  const el = document.getElementById(id);
  if (!el) return;
  const parts = [];
  if (nc.back_steps != null && nc.frame_count) {
    parts.push(`时间轴第 ${nc.back_steps + 1}/${nc.frame_count} 帧`);
  }
  if (nc.published_min != null && nc.age_min != null
      && Math.abs(nc.published_min - nc.age_min) > 6) {
    parts.push(`发布 ${nc.published_min} 分钟前`);
  }
  if (nc.km_per_px) parts.push(`分辨率 ≈${nc.km_per_px}km/像素`);
  if (nc.crop_km) parts.push(`画面约 ${Math.round(nc.crop_km)}km 宽`);
  if (nc.sample_km) parts.push(`沿光路每 ${Math.round(nc.sample_km)}km 取样`);
  if (nc.threshold != null) parts.push(`云判据阈值 ${nc.threshold}`);
  const bands = (nc.cloud_bands || []).map(b => `${Math.round(b[0])}-${Math.round(b[1])}km`);
  const gaps = (nc.clear_gaps || []).map(b => `${Math.round(b[0])}-${Math.round(b[1])}km`);
  if (bands.length) parts.push(`云带 ${bands.join('、')}`);
  if (gaps.length) parts.push(`云隙 ${gaps.join('、')}`);
  // 光路逐段着色（v2.33）：红=云 绿=隙，≥60km 的段在图上直接标 C/G + 距离
  if (bands.length || gaps.length) {
    parts.push('光路红=云 绿=隙，C=云带 G=云隙');
  }
  const ev = (nc.minutes_to_event != null)
    ? (nc.minutes_to_event >= 0
       ? `距 ${label || '事件'} 还有 ${Math.round(nc.minutes_to_event)} 分钟`
       : `${label || '事件'}已过 ${Math.round(-nc.minutes_to_event)} 分钟`) : '';
  const stale = (nc.age_min != null && nc.minutes_to_event != null
                 && nc.age_min > 120)
    ? `<span style="color:#c06040">该帧比事件早 ${nc.age_min} 分钟，`
      + '云带可能已移动，请临近再看</span>' : '';
  el.innerHTML = [ev, ...parts, stale].filter(Boolean).join(' · ');
}


// 当日无对准窗口时：直接给出「当天各事件 vs 目标方位」的差距，不做未来日期搜索
function renderAlignDayGap(r, bodyName, periodName) {
  const tbl = document.getElementById('alignTbl');
  const evs = r.day_events || [];
  if (!evs.length) {
    tbl.innerHTML = r.day_events === undefined
      ? '<tr><td style="color:#c06040">服务端版本较旧，未提供当日事件数据：'
        + '请重启服务后刷新页面。</td></tr>'
      : '<tr><td style="color:#999">当日事件数据不可用</td></tr>';
    return;
  }
  const meta = r.alignment_meta || {};
  const isSun = alignState.body === 'sun';
  const keys = isSun ? ['sunrise', 'sunset'] : ['moonrise', 'moonset'];
  const mine = evs.filter(e => keys.indexOf(e.key) >= 0);
  // 只看**当前选中时段**自己的事件：日出就比日出，不要拿日落来充数
  const ownKey = isSun ? (alignState.period === 'rise' ? 'sunrise' : 'sunset')
                       : (alignState.period === 'rise' ? 'moonrise' : 'moonset');
  const own = evs.find(e => e.key === ownKey) || null;
  const other = mine.filter(e => e && e.key !== ownKey)
    .reduce((a, b) => (!a || b.daz < a.daz ? b : a), null);
  const pastTxt = e => (e.is_past
    ? ` <span style="color:var(--ink-2)">（已过）</span>` : '');
  let head = '';
  if (own) {
    if (own.daz <= meta.tol) {
      head = `当日${own.name} ${own.time}${pastTxt(own)} 方位 <b>${own.az}°</b>`
        + ` 在取景框内（差 ${own.daz}°），但高度角 ${own.alt}° `
        + `不在 ${meta.alt_lo}°~${meta.alt_hi}° 可拍范围。`;
    } else {
      head = `当日${own.name} ${own.time}${pastTxt(own)} 方位 <b>${own.az}°</b>，`
        + `与目标 ${meta.target_az}° 差 <b>${own.daz}°</b>`
        + `（视场容差 ±${meta.tol}°，还差 `
        + `${(own.daz - meta.tol).toFixed(2)}°）。`;
    }
  }
  if (other && other.daz <= meta.tol + 12) {
    head += ` ${other.name} ${other.time} 更接近：差 ${other.daz}°。`;
  }
  const rows = evs.map(e => {
    const ok = e.in_range
      ? '<span style="color:#1e8a4c">在框内</span>'
      : `<span style="color:var(--ink-2)">差 ${e.daz}°</span>`;
    const st = e.is_past === true
      ? '<span style="color:var(--ink-2)">已过</span>'
      : e.is_past === false ? '未来' : '-';
    return `<tr${e.is_past ? ' style="opacity:.6"' : ''}><td>${e.name}</td>`
      + `<td>${e.time}</td><td>${e.az}°</td>`
      + `<td>${e.daz}°</td><td>${e.alt}°</td><td>${ok}</td><td>${st}</td></tr>`;
  }).join('');
  tbl.innerHTML = `<tr><td colspan="7" style="color:var(--ink-2)">${head}`
    + ' 下方为当日该方位的天气与日月可见性。</td></tr>'
    + '<tr><td colspan="7">'
    + '<button id="btnGapCal" class="primary" type="button">看下次对准日期 →</button>'
    + (own && own.daz > meta.tol
       ? `<button id="btnGapTryAz" type="button" style="margin-left:8px">`
         + `用当日${own.name}方位 ${own.az}° 试算</button>` : '')
    + '<span style="margin-left:10px;opacity:.75">当日对不上时：'
    + '「下次对准」给未来可拍的日期；「试算」把目标方位临时改成当日'
    + `${own ? own.name : '事件'}方位，看那天的天气如何</span></td></tr>`
    + '<tr><th>当日事件</th><th>时刻</th><th>方位</th><th>与目标差</th>'
    + '<th>高度角</th><th>判定</th><th>状态</th></tr>' + rows;
  const b = document.getElementById('btnGapCal');
  if (b) {
    b.onclick = () => {
      const card = document.getElementById('alignCalCard');
      if (card) {
        card.style.display = 'block';
        card.scrollIntoView({behavior: 'smooth', block: 'start'});
      }
    };
  }
  const bt = document.getElementById('btnGapTryAz');
  if (bt && own) {
    bt.onclick = () => {
      const el = document.getElementById('azInput');
      if (el) el.value = (+own.az).toFixed(1);
      alignState.az = +own.az;
      loadAlign(true);
    };
  }
}

function renderAlignPage() {
  hideError();
  // 悬日月不走概率校准曲线（可见概率另有口径），配色阈值一律回到默认——
  // 防止上一份霞光报告的 PRD region 残留到这里
  curRegion = null;
  const r = alignState.data;
  const setup = document.getElementById('alignSetupCard');
  if (setup) setup.style.display = 'block';
  setTimeout(() => { initAlignMap(); renderAlignMap(false); }, 30);
  const card = document.getElementById('alignCard');
  if (card) card.style.display = 'block';
  if (!r) return;
  checkUiVersion(r);
  syncAlignControls();
  syncAlignHeightInfo(r);
  renderAlignMap(false);
  const list = alignWindowList();
  const isSun = alignState.body === 'sun';
  const bodyName = isSun ? '悬日' : '悬月';
  const periodName = alignState.period === 'rise'
    ? (isSun ? '日出' : '月出') : (isSun ? '日落' : '月落');
  const meta = r.alignment_meta || {};
  const title = document.getElementById('alignTitle');
  if (title) {
    const camName = (alignState.cam && alignState.cam.name) || curCity().name;
    // 只评估所选日期：不再展示多日窗口范围
    title.textContent = `${r.meta.date} · ${camName} · 目标方位 `
      + `${meta.target_az}°±${meta.tol}° · 视场 ${meta.aov}°`;
  }
  // 当日天气概况（即使没有对准窗口）
  const dwKey = `${alignState.body}`
    + `${alignState.period === 'rise' ? 'rise' : 'set'}`;
  const dw = (r.day_weather || {})[dwKey];
  const dwEl = document.getElementById('alignDayWx');
  if (dwEl) {
    if (dw) {
      const c = dw.cloud || {};
      dwEl.innerHTML = `当日天气（${periodName} ${dw.time}）：`
        + `低云 ${c.low}% / 中云 ${c.mid}% / 高云 ${c.high}%`
        + ` · 可见概率 <b>${dw.vis_prob}%</b>（${dw.verdict}）`
        + ` · AOD ${dw.aod}`
        + `${dw.precip_prob != null
             ? ' · 降水概率 ' + dw.precip_prob + '%' : ''}`
        + ' <span style="opacity:.7">（当日天气，非对准窗口）</span>';
    } else if (_serverStale) {
      dwEl.textContent = '当日天气不可用：服务端版本较旧，请重启服务后刷新';
    } else if ((r.meta || {}).weather_note) {
      dwEl.textContent = '当日天气不可用：' + r.meta.weather_note;
    } else {
      dwEl.textContent = '当日天气数据获取中…';
    }
  }
  const tbl = document.getElementById('alignTbl');
  const cwrap = document.getElementById('celestialWrap');
  // 优先未来窗口：当天早晨查傍晚时不能让"已过"的窗口当结论
  const live = alignPickLive(list);
  if (!list.length) {
    // 当日无对准窗口：给差距事实 + 当日方向天气（不做未来窗口搜索）
    renderAlignDayGap(r, bodyName, periodName);
    if (cwrap) cwrap.style.display = 'none';
  } else {
    if (cwrap) cwrap.style.display = 'block';
    renderAlignTable(list, r);
  }
  renderAlignCalendar(r);
  // 优先展示首个有天气数据的窗口：首个窗口可能超出预报范围（无天气/无视线评估）
  const best = alignPickLive(
    list.filter(x => x.feasibility || x.sight).length
      ? list.filter(x => x.feasibility || x.sight) : list) || live || null;
  setPvWindow(best, r);
  renderAlignHero(best, r);
  renderAlignSight(best, r);
  renderAlignDetail(best, r);
  renderAlignDir(list.length ? null : (r.day_weather || {})[dwKey],
                 r, bodyName, periodName);
  renderAlignWeek(r);
  loadAlignNowcast(best, r);
  syncHorizonEvent();
  revealVisibleCards();
}

function renderAlignTable(list, r) {
  const tbl = document.getElementById('alignTbl');
  const meta = r.alignment_meta || {};
  const nowState = w => {
    if (w.is_past == null) return '';
    if (w.is_past) {
      const h = Math.floor(-(w.minutes_to_event || 0) / 60);
      return `<span style="color:var(--ink-2)">已过${h >= 1 ? ' ' + h + 'h' : ''}</span>`;
    }
    const m = w.minutes_to_event || 0;
    return m < 120
      ? `<span style="color:#1e8a4c">${Math.max(1, Math.round(m))} 分钟后</span>`
      : `<span style="color:inherit">${Math.floor(m / 60)}h 后</span>`;
  };
  tbl.innerHTML = '<tr><th>日期 / 最佳时刻</th><th>状态</th><th>方位差</th>'
    + '<th>高度角</th><th>目标方位</th><th>可见概率</th><th>月相照度</th>'
    + '<th>原因</th><th>预览</th></tr>'
    + list.map((w, i) => `<tr class="${i === 0 && !w.is_past ? 'row-best' : ''}"
        ${w.is_past ? 'style="opacity:.55"' : ''}>
      <td>${(w.date || '').slice(5)} ${w.best_time}</td>
      <td>${nowState(w)}</td>
      <td>±${w.best_daz}°</td>
      <td>${w.best_alt}° <span class="q">${w.alt_quality || ''}</span></td>
      <td>${meta.target_az}°</td>
      <td class="prob" style="color:${probColor(w.vis_prob || 0)}">${w.vis_prob != null ? w.vis_prob + '%' : '-'}</td>
      <td>${w.illum != null ? w.illum + '%' : '-'}</td>
      <td>${w.reason || '-'}${w.horizon_blocked ? ' ⛰' : ''}</td>
      <td><button class="prev-btn" data-i="${i}" aria-label="预览 ${w.date || ''} ${w.time || ''} 的方位与高度" title="预览该时刻的天体位置" style="padding:4px 12px;font-size:12px;border:1px solid var(--input-line);background:var(--item-bg);color:var(--ink);border-radius:6px;cursor:pointer">👁</button></td>
    </tr>`).join('');
  tbl.querySelectorAll('.prev-btn').forEach(b => {
    b.onclick = () => {
      setPvWindow(list[+b.dataset.i], r);
      document.getElementById('celestialCanvas').scrollIntoView(
        {behavior: 'smooth', block: 'center'});
    };
  });
}

// 不确定区间 + 校准出处：把"集合离散度"从一句风险提示变成可看的区间，
// 并如实说明本模型的标定样本（555 天上海回测），不假装区域通用。
let _gcStatsCache = null;

function renderHeroUncertainty(r) {
  // 首屏只留最短的一句：集合区间。标定/回填样本数属于证据层，放回反馈卡。
  const el = document.getElementById('heroUncert');
  if (!el) return;
  const b = r.ens_band || {};
  if (b.p10 != null && b.p90 != null) {
    el.style.display = 'inline';
    el.textContent = `集合中高云 ${b.p10}~${b.p90}%`
      + (b.p50 != null ? `（中位 ${b.p50}%）` : '');
    return;
  }
  if (r.ens_spread != null) {
    el.style.display = 'inline';
    el.textContent = `集合均值 ${r.ens_spread}%`;
    return;
  }
  el.style.display = 'none';
}

// 优先展示"还没发生"的第一个窗口：当天早晨查傍晚的页面时，已过窗口
// 不能当结论（否则用户看到的是几小时前就该拍的天气）。
function alignPickLive(list) {
  if (!list.length) return null;
  return list.find(w => !w.is_past) || list[0];
}


// ---------------- 下次对准（悬日月日历） ----------------

let _alignCalKey = '';

function renderAlignCalendar(r) {
  const card = document.getElementById('alignCalCard');
  if (!card) return;
  if (!r || !r.alignment_meta) { card.style.display = 'none'; return; }
  const meta = r.alignment_meta;
  const kind = alignState.period === 'rise' ? 'rise' : 'set';
  const body = alignState.body === 'moon' ? 'moon' : 'sun';
  // 容差与窗口表同源：取后端回填的 tol（视场角推出），避免两处口径打架
  const tol = Math.max(0.5, Math.min(5, +(meta.tol || 1.0)));
  const key = [body, kind, (+meta.target_az).toFixed(1),
               tol.toFixed(2)].join('|');
  const noteEl = document.getElementById('alignCalNote');
  const nextEl = document.getElementById('alignCalNext');
  const gridEl = document.getElementById('alignCalGrid');
  const vLabel = `${alignState.body === 'moon' ? '悬月' : '悬日'}·`
    + `${alignState.period === 'rise' ? '出' : '落'}`;
  if (noteEl) {
    noteEl.textContent = `${vLabel} 目标 ${meta.target_az}°±${tol}° · 未来 13 个月`;
  }
  if (key === _alignCalKey && card.dataset.loaded === '1') {
    card.style.display = 'block';
    return;
  }
  _alignCalKey = key;
  card.dataset.loaded = '0';
  card.style.display = 'block';
  if (gridEl) gridEl.innerHTML = '<div class="week-cell"><div class="v">计算中…</div></div>';
  if (nextEl) nextEl.textContent = '正在扫描未来 13 个月的升落方位…';
  const url = `${API}/api/sun_calendar?lat=${alignCalLat()}&lon=${alignCalLon()}`
    + `&azimuth=${encodeURIComponent((+meta.target_az).toFixed(2))}`
    + `&tol=${encodeURIComponent(tol.toFixed(2))}&body=${body}&kind=${kind}`;
  fetch(url).then(res => {
    if (!res.ok) throw new Error('HTTP ' + res.status);
    return res.json();
  }).then(cal => {
    if (_alignCalKey !== key) return;          // 已切到别的方位/天体
    card.dataset.loaded = '1';
    renderAlignCalendarBody(cal, r, body, kind);
  }).catch(e => {
    if (_alignCalKey !== key) return;
    card.dataset.loaded = '1';
    if (nextEl) nextEl.textContent = '下次对准日历获取失败：' + e.message;
    if (gridEl) gridEl.innerHTML = '';
  });
}

function alignCalLat() {
  const cam = alignState.cam || curCity();
  return cam ? cam.lat : 22.27;
}

function alignCalLon() {
  const cam = alignState.cam || curCity();
  return cam ? cam.lon : 113.58;
}

function renderAlignCalendarBody(cal, r, body, kind) {
  const nextEl = document.getElementById('alignCalNext');
  const gridEl = document.getElementById('alignCalGrid');
  const evs = cal.events || [];
  const nx = cal.next;
  const selected = (r.meta || {}).date || '';
  if (nextEl) {
    if (nx) {
      const sameDay = nx.date === selected;
      nextEl.innerHTML = `下一次：<b>${nx.date.slice(5)} ${nx.time}</b>`
        + ` ${cal.kind_name} · 方位 <b>${nx.az}°</b>（差 ±${nx.daz}°）`
        + `${nx.illum != null ? ` · 月相照度 ${nx.illum}%` : ''}`
        + (sameDay ? ' <span style="color:#1e8a4c">（就是所选日期）</span>'
                   : ' <span style="opacity:.75">（点它可跳到那天）</span>');
    } else {
      nextEl.textContent = `未来 ${cal.scanned_days} 天内该方位没有对准日期`
        + '（换个方位或改用更大的视场角容差）。';
    }
  }
  if (!gridEl) return;
  if (!evs.length) { gridEl.innerHTML = ''; return; }
  // 已过的也列出来（灰显），让"今天已经过了"这件事在日历里也看得见
  const shown = evs.slice(0, 12);
  const nowIso = ((r.meta || {}).now || '').slice(0, 16) || null;
  gridEl.innerHTML = shown.map(e => {
    const past = nowIso ? (`${e.date}T${e.time}` < nowIso.replace(' ', 'T'))
                        : false;
    return `<div class="week-cell" data-date="${e.date}" role="button"
      tabindex="0" aria-label="${e.date} ${esc(e.time)} 方位 ${e.az} 度"
      style="cursor:pointer${past ? ';opacity:.5' : ''}"
      ${e.date === selected ? 'title="当前所选日期"' : ''}>
      <div class="d">${e.date.slice(5)}${e.date === selected ? ' ●' : ''}</div>
      <div class="p">${e.time}</div>
      <div class="v">方位 ${e.az}° · 差 ${e.daz}°${past ? ' · 已过' : ''}</div>
    </div>`;
  }).join('');
  gridEl.querySelectorAll('.week-cell').forEach(cell => {
    if (!cell.dataset.date) return;
    cell.onclick = () => {
      document.getElementById('dateSel').value = cell.dataset.date;
      syncDateUI();
      loadAlign(true);
    };
  });
}

function renderAlignEntry(r) {
  const card = document.getElementById('alignEntryCard');
  if (!card) return;
  card.style.display = 'block';
  const body = document.getElementById('alignEntryBody');
  const d = alignState.data;
  if (body) {
    if (d && d.alignment && d.alignment.length) {
      const list = d.alignment.slice(0, 3).map(w =>
        `${w.type}·${w.period_name} ${(w.date || '').slice(5)} ${w.best_time}（${w.best_alt}°）`
      ).join('；');
      body.textContent = `目标方位 ${alignState.az}° · 最近窗口：${list}`;
    } else {
      body.textContent = '搜索或点选目标点即自动定出方位并判断天气（机位默认当前城市，可直接改）；无需巧摄文件';
    }
  }
  const btn = document.getElementById('btnEnterAlign');
  if (btn && !btn.__bound) {
    btn.__bound = true;
    btn.onclick = () => switchMode('align');
  }
}

function saveModeState() {
  try {
    localStorage.setItem('gc_mode', JSON.stringify({
      mode: appMode, glowType,
      body: alignState.body, period: alignState.period,
      az: alignState.az, aov: alignState.aov, elev: alignState.elev,
      cam: alignState.cam, target: alignState.target,
    }));
  } catch (e) { /* ignore */ }
}

function restoreModeState() {
  try {
    const st = JSON.parse(localStorage.getItem('gc_mode') || 'null');
    if (!st) return;
    if (st.mode === 'align') appMode = 'align';
    if (st.glowType === 'sunrise' || st.glowType === 'sunset') {
      glowType = st.glowType;
    }
    if (st.body === 'moon') alignState.body = 'moon';
    if (st.period === 'set') alignState.period = 'set';
    if (st.az != null) alignState.az = +st.az || 270;
    if (st.aov != null) alignState.aov = +st.aov || 3;
    if (st.elev != null) alignState.elev = +st.elev || 0;
    if (st.cam && st.cam.lat != null) alignState.cam = st.cam;
    if (st.target && st.target.lat != null) alignState.target = st.target;
    syncAlignControls();
  } catch (e) { /* ignore */ }
}

function curCity() { return cities[+document.getElementById('citySel').value] || cities[0]; }

// ---------------- 日期状态（今天/明天/周历点选统一同步） ----------------
function localToday() {
  const d = new Date();
  return d.getFullYear() + '-' + String(d.getMonth() + 1).padStart(2, '0')
       + '-' + String(d.getDate()).padStart(2, '0');
}
function localTomorrow() {
  const d = new Date();
  d.setDate(d.getDate() + 1);
  return d.getFullYear() + '-' + String(d.getMonth() + 1).padStart(2, '0')
       + '-' + String(d.getDate()).padStart(2, '0');
}
const WEEK_CN = ['日', '一', '二', '三', '四', '五', '六'];

function syncDateUI() {
  const ds = document.getElementById('dateSel').value;
  const label = document.getElementById('dateLabel');
  if (!label || !ds) return;
  const d = new Date(ds + 'T12:00:00');
  const txt = `${d.getMonth() + 1}月${d.getDate()}日 · 周${WEEK_CN[d.getDay()]}`;
  const today = localToday(), tomorrow = localTomorrow();
  const prefix = ds === today ? '今天 ' : ds === tomorrow ? '明天 ' : '';
  label.textContent = prefix + txt;
  document.querySelectorAll('#dateSeg button').forEach(b => {
    b.classList.toggle('active',
      (b.dataset.v === 'today' && ds === today) ||
      (b.dataset.v === 'tomorrow' && ds === tomorrow));
  });
}
function curType() {
  return glowType;
}

// ---------------- 数据加载 ----------------
// ---------------- 加载态 / 错误态 ----------------
// 职责：把「空白 + 一行灰字」换成骨架屏 + 阶段文案 + 已等待计时 + 取消。
// 阶段文案只描述真实发生的事：请求在飞 / 前端在渲染；不对后端内部进度撒谎。
const LOAD_TEXT = {
  report: '正在获取多模式气象数据（GFS/ECMWF/ICON/CMA）…',
  align: '正在计算悬日月对准窗口…',
};
let _loadTimer = null, _loadStart = 0, _loadKind = 'report', _cancelFn = null;

function setLoadStage(text) {
  const el = document.getElementById('loadStage');
  if (el) el.textContent = text;
}

function showLoading(kind) {
  hideError();
  _loadKind = kind;
  _loadStart = Date.now();
  const box = document.getElementById('loading');
  if (box) box.style.display = 'block';
  setLoadStage(LOAD_TEXT[kind] || '正在加载…');
  clearInterval(_loadTimer);
  _loadTimer = setInterval(() => {
    const s = Math.round((Date.now() - _loadStart) / 1000);
    setLoadStage((LOAD_TEXT[_loadKind] || '正在加载…') + ' · 已等待 ' + s + 's');
  }, 1000);
}

function hideLoading() {
  clearInterval(_loadTimer);
  _loadTimer = null;
  const box = document.getElementById('loading');
  if (box) box.style.display = 'none';
}

function showError(title, msg) {
  hideLoading();
  const card = document.getElementById('errCard');
  if (!card) return;
  document.getElementById('errTitle').textContent = title;
  document.getElementById('errMsg').textContent = msg || '';
  card.style.display = 'block';
}

function hideError() {
  const card = document.getElementById('errCard');
  if (card) card.style.display = 'none';
}

// 把非 2xx 响应转成带原因的错误对象：要能区分「上游额度用尽」与「网络故障」，
// 否则用户看到的永远是同一句「连不上服务端」。
async function apiError(res) {
  let body = null, text = '';
  try {
    text = await res.text();
    body = JSON.parse(text);
  } catch (e) { /* 非 JSON（如反代返回的 HTML 错误页） */ }
  const err = new Error((body && body.message) || text || ('HTTP ' + res.status));
  err.status = res.status;
  err.scope = body && body.scope;
  err.upstreamLimited = res.status === 429
    || !!(body && body.source === 'open-meteo');
  return err;
}

// 缓存回退横幅：说清「为什么」+「这份是几点的」
function showCacheBanner(err) {
  const el = document.getElementById('cacheBanner');
  if (!el || !curReport) return;
  const m = curReport.meta || {};
  const why = (err && err.upstreamLimited)
    ? 'Open-Meteo 免费用量已用尽（额度按 IP 计，共享出口 IP 下会被他人一起消耗）'
    : '网络获取失败';
  const want = document.getElementById('dateSel').value || '';
  const stale = want && m.date && want !== m.date
    ? `查询日期仍是 <b>${want}</b>，网络恢复后点「查询」即重查当天；`
    : '网络恢复后点「查询」即重试；';
  el.innerHTML = `⚠ ${why}，正在显示 <b>${m.date || ''}</b> 报告`
    + `（${m.generated_at || '未知时刻'} 生成）。${stale}下方数据都是这份缓存的。`;
  el.style.display = 'block';
}

function failTitle(err, fallback) {
  return (err && err.upstreamLimited)
    ? '上游额度已用尽' : (fallback || '数据获取失败');
}

function failMessage(err) {
  if (err && err.upstreamLimited) {
    return (err.message || '上游额度已用尽。')
      + ' 本地没有该城市/日期的缓存报告可回退；换一条出口（如手机热点）也可能绕开。';
  }
  return /Failed to fetch|NetworkError|Load failed/i.test(err && err.message || '')
    ? '连不上服务端。请确认本地服务（uvicorn）仍在运行，然后重试。'
    : String((err && err.message) || err || '');
}

function bindLoadCtrl() {
  const btn = document.getElementById('btnLoadCancel');
  if (btn) btn.onclick = () => { if (_cancelFn) _cancelFn(); };
  const retry = document.getElementById('btnErrRetry');
  if (retry) retry.onclick = () => reloadCurrent();
}

async function loadReport() {
  const c = curCity();
  const date = document.getElementById('dateSel').value;
  const type = curType();
  setQueryPending(false);       // 真在查了：撤掉「待查询」提示
  hideIdle();
  skyOffsetMin = 0;
  const req = ++_reportReq;
  clearNcTimer();
  const ac = new AbortController();
  _cancelFn = () => ac.abort();
  // 有旧报告就留着看（压暗 + 顶栏状态），不要一按查询就把屏幕清空
  const keepOld = !!curReport;
  if (keepOld) document.body.classList.add('is-reloading');
  else { hideAll(); resetReveal(); }
  showLoading('report');
  document.getElementById('cacheBanner').style.display = 'none';
  syncSlateStatus(null, '取数中…');
  cinemaSeqStart();   // M5：打板 0.9s → 光束 1.2s（序列 start 相）
  try {
    const res = await fetch(
      `${API}/api/report?lat=${c.lat}&lon=${c.lon}&date=${date}&type=${type}`,
      {signal: ac.signal});
    if (!res.ok) throw await apiError(res);
    setLoadStage('正在渲染图表、地图与光路…');
    const data = await res.json();
    if (req !== _reportReq) return;     // 已有更新的请求，丢弃过期响应
    // 请求时的城市/日期随报告一起钉住：渲染时不再读「现在」的控件值，
    // 否则加载途中改城市会出现「深圳的标题 + 珠海的数据」（审计 D4）
    data.__reqCity = c;
    data.__reqDate = date;
    curReport = data;
    window.__gcCurReport = curReport;
    document.body.classList.remove('is-reloading');
    hideAll();                 // 只在拿到新数据后清场（旧报告此前一直保留）
    resetReveal();
    render();
    hideLoading();
    _cancelFn = null;
    try {
      localStorage.setItem('gc_last_' + c.id + '_' + date + '_' + type,
                           JSON.stringify(curReport));
    } catch (e) { /* 存储满忽略 */ }
  } catch (e) {
    if (req !== _reportReq) return;     // 过期请求失败不再回退覆盖
    document.body.classList.remove('is-reloading');
    if (e && e.name === 'AbortError') {
      _cancelFn = null;
      // 取消不该毁掉已有报告：有旧的就原样放回来，只改顶栏状态
      if (keepOld) {
        hideLoading();
        hideAll();
        resetReveal();
        render();
        syncSlateStatus(curReport, '已取消');
        return;
      }
      showError('查询已取消', '本次请求已取消。改选城市或日期后可重新查询。');
      return;
    }
    // 网络失败：回退本地缓存。**优先同日期**的缓存（今天查过就给今天的），
    // 否则取最近生成的。旧报告只是参考展示 —— **不改日期输入框**：
    // 输入框代表「下次查询查哪天」，被回退报告改写后，一次断网就会把
    // 日期钉死在旧报告上（实测：每次进入都停在 9 月 6 日，连「查询」
    // 都跟着查旧日期）。旧报告是哪天的看 Hero 与横幅。
    console.error('[loadReport] fallback to cache:', e.message,
                  e.stack ? String(e.stack).split('\n')[1] : '');
    let cachedSame = null, cachedSameTs = '';
    let cached = null, cachedTs = '';
    const prefix = 'gc_last_' + c.id + '_';
    for (let i = 0; i < localStorage.length; i++) {
      const k = localStorage.key(i);
      if (k && k.startsWith(prefix)) {
        try {
          const d = JSON.parse(localStorage.getItem(k));
          if (!d || !d.meta) continue;
          if (k === prefix + date + '_' + type) {
            if (!cachedSame || d.meta.generated_at > cachedSameTs) {
              cachedSame = d; cachedSameTs = d.meta.generated_at;
            }
          } else if (!cached || d.meta.generated_at > cachedTs) {
            cached = d; cachedTs = d.meta.generated_at;
          }
        } catch (err) { /* skip */ }
      }
    }
    _cancelFn = null;
    if (cachedSame || cached) {
      curReport = cachedSame || cached;
      curReport.__from_cache = true;      // render() 据此不改日期输入框
      window.__gcCurReport = curReport;
      hideAll();
      resetReveal();
      render();
      showCacheBanner(e);
      hideLoading();
      return;
    }
    // 没缓存：清场后单独立错误卡（不与报告同屏），但把「上一份」留成可恢复入口
    hideAll();
    resetReveal();
    showError(failTitle(e), failMessage(e));
    syncSlateStatus(null, '取数失败');
    const restore = document.getElementById('btnErrRestore');
    if (restore) {
      restore.style.display = curReport ? 'inline-block' : 'none';
      restore.onclick = () => {
        if (!curReport) return;
        hideError();
        hideAll();
        resetReveal();
        render();
      };
    }
  }
}

function hideAll() {
  // 清场要清干净：漏一张卡，失败态就会和新报告混排（审计 D9）
  ['idleCard','heroCard','briefCard','bestCard','wxCard','dimsCard','mapCard','revCard',
   'csCard','soundCard',
   'pmCard','modelCard','alignCard','astroCard','t48Card','riskCard',
   'weekCard','rankCard','compareCard','alignEntryCard','horizonCard',
   'nowcastCard','feedbackCard','skyCard','alignDetailCard','alignWeekCard','alignNowcastCard','alignSetupCard','alignDirCard']
    .forEach(id => {
      const el = document.getElementById(id);
      if (el) el.style.display = 'none';
    });
}

// ---------------- 今日简报（v2.33）：规则模板，结论前置 ----------------
// 所有字段来自既有口径（verdict/probability/credibility/best_window/main/risks），
// 不引入新的判断逻辑 —— Hero 是仪表盘，这里是把同一份数据讲成一段人话
// ---------------- 首屏答案层 ----------------
// 一句人话先给结论，判定词（大烧/中烧…）是后端口径，作为小字徽标保留
// 首屏答案层：一屏之内回答「今晚拍不拍」
let _lastRenderedReport = null;
const HEADLINE_BY_VERDICT = {
  '大烧': '今晚，天会烧。',
  '中烧': '值得跑一趟。',
  '小烧': '有点看头，可以碰。',
  '微烧': '碰运气的一晚。',
  '不烧': '今晚歇一歇。',
};
function heroHeadline(verdict, r) {
  if (r && r.__from_cache) return '先看这份最近成功的报告。';
  const v = String(verdict || '');
  for (const k in HEADLINE_BY_VERDICT) {
    if (v.indexOf(k) >= 0) return HEADLINE_BY_VERDICT[k];
  }
  return '今晚先看天。';
}

// 顶栏状态位：显示真正有用的新鲜度（替代原来装饰性的 SCENE/TAKE/ROLL）
function syncSlateStatus(r, note) {
  const el = document.getElementById('slStatus');
  if (!el) return;
  if (note) { el.innerHTML = '<span class="stale">' + note + '</span>'; return; }
  if (!r) { el.textContent = '未查询'; return; }
  const gen = (r.meta && r.meta.generated_at) || '';
  const hhmm = gen.length >= 16 ? gen.slice(11, 16) : '';
  const stale = r.__from_cache ? '<span class="stale">缓存 · </span>' : '';
  el.innerHTML = stale + (hhmm ? '生成于 <b>' + hhmm + '</b>' : '已生成');
}

// 决策带：几点到 / 朝哪站 / 去哪拍 —— 三个动作问题在首屏一次答完
function renderDecide(r, c) {
  const geoOk = !(r.main && r.main.geo_ok === false);
  const bw = r.best_window;
  const hasWin = !!(bw && geoOk && bw.start && bw.end);
  const win = document.getElementById('decideWindow');
  if (win) win.textContent = hasWin ? `${bw.start} – ${bw.end}` : '—';
  const wh = document.getElementById('decideWindowHint');
  if (wh) {
    wh.textContent = hasWin
      ? `事件 ${r.astronomy.key_time} · 提前 20 分钟到场`
      : '该事件没有可用窗口，换一天看看';
  }
  const dirEl = document.getElementById('decideDir');
  if (dirEl) {
    dirEl.textContent = `${azimuthName(r.astronomy.key_az)} ${r.astronomy.key_az}°`;
  }
  const spotEl = document.getElementById('decideSpot');
  const spotHint = document.getElementById('decideSpotHint');
  const spots = (c && c.spots) || [];
  if (spotEl) {
    spotEl.textContent = spots.length
      ? spots.slice(0, 2).map(s => s.name).join(' / ')
      : `${azimuthName(r.astronomy.key_az)}向开阔高处`;
  }
  if (spotHint) {
    spotHint.textContent = spots.length
      ? (spots.length > 2 ? `另有 ${spots.length - 2} 处，见地图` : '点开地图看覆盖范围')
      : '该城市暂无具体机位，选无遮挡高处';
  }
  const fmr = document.getElementById('frameMetaR');
  if (fmr) fmr.textContent = `${c ? c.name : ''} · AZ ${r.astronomy.key_az}°`;
  syncSlateStatus(r);
}

function renderBrief(r) {
  const card = document.getElementById('briefCard');
  const el = document.getElementById('briefText');
  if (!card || !el) return;
  if (r.probability == null) { card.style.display = 'none'; return; }
  const m = r.main || {};
  const cl = m.cloud || {};
  // 首屏已经把判定/概率/窗口/方位说完了，这里只留「为什么」和「小心什么」
  const basis = [];
  if (m.canvas_avg != null) basis.push(`光路受光画布 ${Math.round(m.canvas_avg)}%`);
  if (cl.low != null) {
    basis.push(`低云 ${Math.round(cl.low)}%` + (cl.low >= 45 ? '（偏多易挡）' : ''));
  }
  if (cl.high != null) basis.push(`高云 ${Math.round(cl.high)}%`);
  if (m.aod_local != null) {
    basis.push(`AOD ${m.aod_local}`
               + (m.aod_local >= 0.5 ? '（浑，污烧风险）'
                  : m.aod_local <= 0.2 ? '（通透）' : ''));
  }
  const s = [];
  if (basis.length) s.push(`依据：${basis.join('、')}。`);
  const rk = (r.risks || [])[0];
  if (rk) s.push(`风险：${rk}。`);
  if (!s.length) { card.style.display = 'none'; return; }
  el.innerHTML = s.join('');
  card.style.display = 'block';
}

// ---------------- 模式探空（v2.33）：T/Td 廓线 + 0°C 层 ----------------
function renderSound(r) {
  const card = document.getElementById('soundCard');
  const el = document.getElementById('soundChart');
  if (!card || !el) return;
  const snd = (r.main || {}).sound;
  if (!snd || !snd.levels || snd.levels.length < 2) {
    card.style.display = 'none';
    return;
  }
  card.style.display = 'block';
  const fz = snd.freezing_km;
  document.getElementById('soundDesc').textContent =
    `${r.meta.date} ${r.astronomy.key_time} ECMWF 观测点 · `
    + (fz != null ? `0°C 层 ${fz} km` : '整层冰点以上（未出现 0°C 层）');
  const ch = window._soundChart && !window._soundChart.isDisposed()
    ? window._soundChart : echarts.init(el);
  window._soundChart = ch;
  ch.clear();
  const axis = getComputedStyle(document.documentElement)
    .getPropertyValue('--chart-axis').trim() || '#888';
  ch.setOption({
    tooltip: chartTip({formatter: p =>
      `${p.seriesName}<br>高度 ${p.value[1]} km · ${p.value[0]}°C`}),
    legend: {top: 2, textStyle: {color: axis, fontSize: 11}},
    grid: {left: 44, right: 24, top: 30, bottom: 34},
    xAxis: {type: 'value', name: '°C', scale: true,
            axisLabel: {color: axis}, nameTextStyle: {color: axis}},
    yAxis: {type: 'value', name: 'km', min: 0, max: 12,
            axisLabel: {color: axis}, nameTextStyle: {color: axis}},
    series: [
      {name: '气温 T', type: 'line',
       data: snd.levels.map(d => [d.t_c, d.alt_km]),
       lineStyle: {color: '#e8602a', width: 2}, symbolSize: 5,
       itemStyle: {color: '#e8602a'},
       ...(fz != null ? {markLine: {silent: true, symbol: 'none',
         lineStyle: {color: '#5bc4b0', type: 'dashed'},
         label: {formatter: '0°C', color: '#5bc4b0', position: 'end'},
         data: [{xAxis: fz}]}} : {})},
      {name: '露点 Td', type: 'line',
       data: snd.levels.map(d => [d.td_c, d.alt_km]),
       lineStyle: {color: '#4a90c4', width: 2, type: 'dashed'},
       symbolSize: 4, itemStyle: {color: '#4a90c4'}},
    ],
  });
}

// ---------------- 渲染 ----------------
function render() {
  hideError();                 // 成功渲染即撤下错误态，避免错误卡与报告并存
  const r = curReport;
  const c = r.__reqCity || curCity();
  // 新报告到达 → 折叠层复位（每次查询都从「今晚」开始读）
  if (_lastRenderedReport !== r) { _lastRenderedReport = r; resetTiers(); }
  const idleEl = document.getElementById('idleCard');
  if (idleEl) idleEl.style.display = 'none';   // 报告在，空态必须让位
  // 校准区域（v2.35）：判定词/配色阈值与后端曲线同源分流
  curRegion = (r.meta && r.meta.region) || null;
  const typeTxt = r.meta.type === 'sunset' ? '晚霞' : '朝霞';
  // 日期输入框跟随**成功取数**的报告；缓存回退的报告不改输入框 ——
  // 输入框是「下次查询的日期」（否则一次断网就被旧报告钉死），
  // 缓存报告自己的日期看 Hero 与缓存横幅
  // 只回写「本次请求发出的那个日期」，用户在等待期间改的日期不被静默回滚（D5）
  if (!r.__from_cache && r.__reqDate
      && document.getElementById('dateSel').value === r.__reqDate
      && r.meta.date !== r.__reqDate) {
    document.getElementById('dateSel').value = r.meta.date;
  }
  syncDateUI();

  // 头部：一屏之内回答「今晚拍不拍」——人话结论先行，术语退成小字
  const hero = document.getElementById('heroCard');
  hero.style.display = 'block';
  document.getElementById('heroTitle').textContent = `${c.name}${typeTxt}预测报告`;
  document.getElementById('heroSub').textContent = `${c.name} · ${typeTxt}预测`;
  document.getElementById('heroDate').textContent =
    `${r.meta.date} (${weekday(r.meta.date)}) · ${r.meta.type === 'sunset' ? '日落' : '日出'} ${r.astronomy.key_time} · 方位 ${r.astronomy.key_az}°`;
  const p = +r.probability;
  countUp(document.getElementById('heroProb'), p, '%');
  document.getElementById('heroHeadline').textContent = heroHeadline(r.verdict, r);
  document.getElementById('heroVerdict').textContent =
    r.verdict + (r.main && r.main.quality ? ' · ' + r.main.quality.brightness : '');
  document.getElementById('heroReason').textContent = r.reason || '';
  document.getElementById('heroCred').textContent =
    `${r.credibility} · 生成于 ${String(r.meta.generated_at || '').slice(11, 16) || '—'}`
    + (r.meta.degraded ? ` · ⚠ ${r.meta.degraded}` : '');
  renderHeroUncertainty(r);
  renderDecide(r, c);
  // 判断依据（首屏已给结论，这里只留为什么）
  renderBrief(r);

  // 卫星临近校准
  renderNowcast(r);

  // 霞光模拟（sky.js 提供，缺文件时优雅跳过）：时间滑块驱动
  if (typeof renderSky === 'function') {
    document.getElementById('skyCard').style.display = 'block';
    bindSkyCtrl(r);
  }

  // 城市对比：默认隐藏，有报告才出现（空态/错误态不该留一条能出数的旁路）
  const cmp = document.getElementById('compareCard');
  if (cmp) cmp.style.display = 'block';
  // 地平线热力图：卡先出现（含「生成」按钮），否则失败后就再也没有入口（审计 D11）
  var hz = document.getElementById('horizonCard');
  if (hz) {
    hz.style.display = 'block';
    var hzm = document.getElementById('horizonMeta');
    if (hzm && !hzm.textContent.trim()) {
      hzm.textContent = '点「生成热力图」按当前城市与日期评估各地地平线可见性。';
    }
  }
  // 未来 14 天概览（v2.38：7 → 14 天）
  renderWeek(r);

  // 附近城市排行榜
  renderRanking(r);

  // 实况回填（事件已过且未回填时显示）
  renderFeedback(r);

  // 气象网格
  const m = r.main || {};
  document.getElementById('wxCard').style.display = 'block';
  document.getElementById('wxModel').textContent = r.main_model || '';
  const cl = m.cloud || {};
  const w = m.weather || {};
  document.getElementById('wxGrid').innerHTML = [
    wxItem('总云量', fmt(cl.total, '%')),
    wxItem('低/中/高云', `${fmt(cl.low)}/${fmt(cl.mid)}/${fmt(cl.high)}`),
    wxItem('降水概率', fmt(w.precip_prob, '%')),
    wxItem('气压', fmt(w.pressure, '', ' hPa')),
    wxItem('能见度', fmt(w.visibility_km, '', ' km')),
    wxItem('相对湿度', fmt(w.humidity, '%')),
    wxItem('气温', fmt(w.temp, '', '°C')),
    wxItem('PM2.5', fmt(m.pm25, '', ' μg/m³')),
    wxItem('AOD 550nm', fmt(m.aod_local, '', '')),
    wxItem('光路 AOD', fmt(m.aod_path, '', '')),
    wxItem('光路云边界', m.boundary_km != null ? m.boundary_km + ' km' : '≥580 km'),
    wxItem('等效云底高', fmt(m.eq_base_km, '', ' km')),
    wxItem('0°C 层', m.sound && m.sound.freezing_km != null
      ? m.sound.freezing_km + ' km' : '—'),
  ].join('');

  // 模式探空（T/Td 廓线 + 0°C 层）
  renderSound(r);

  // 8 维度
  document.getElementById('dimsCard').style.display = 'block';
  document.getElementById('dimsList').innerHTML = (r.dims_detail || []).map(d => {
    const s = d.score == null ? 0 : d.score;
    const detail = dimDetail(d.key, m, r);
    // A单列：右侧双打分（主分=后端维度真值；副分=光路加权视角，无对应字段时同主分）
    const pb = dimPathScore(d.key, m, r);
    const bTxt = pb == null ? (s / 10).toFixed(0) : String(pb);
    const bCls = pb == null ? ' same' : '';
    return `<div class="dim-item">
      <div class="dim-head">
        <div class="dim-name">${dimCN(d.key)} <span class="w">${d.weight}%</span></div>
        <div class="dim-score dim-dual"><span class="ds-a" title="本地维度评分（后端八维口径）">${(s / 10).toFixed(0)}</span><span class="ds-b${bCls}" title="${pb == null ? '该维度无光路对应观测，与左列同源' : '光路距离加权视角（前端派生）'}">${bTxt}</span></div>
      </div>
      <div class="dim-bar"><div style="width:${s}%"></div></div>
      <div class="dim-detail">${detail}</div>
    </div>`;
  }).join('');

  // 地图
  document.getElementById('mapCard').style.display = 'block';
  document.getElementById('mapMeta').textContent =
    `${r.meta.date} · ${r.astronomy.key_time} ${typeTxt} · 方位 ${r.astronomy.key_az}° (${azimuthName(r.astronomy.key_az)}) · 概率 ${p}%`;
  renderBurnMap(r);

  // 反烧
  document.getElementById('revCard').style.display = 'block';
  document.getElementById('revNote').textContent =
    `反烧方位 ${r.reverse_burn.az}° · 反烧条件分 ${r.reverse_burn.score}（仅高云参与，中低云不反烧）`;
  renderRevMap(r);

  // 截面
  document.getElementById('csCard').style.display = 'block';
  document.getElementById('csDesc').textContent =
    `从${c.name}沿${typeTxt}方向 (${azimuthName(r.astronomy.key_az)}) 次经过的光路城市垂直剖面，蓝色 = 相对湿度（越深越湿），按 ${c.name} km 距离排序。`;
  renderCrossSection(r);

  // PM2.5
  document.getElementById('pmCard').style.display = 'block';
  renderPmMap(r);

  // 多模型（M6：CAMERA TESTS 试镜表，最优行金底黑字；数据不动）
  document.getElementById('modelCard').style.display = 'block';
  const rows = Object.entries(r.models).map(([name, mm]) => {
    const cl2 = mm.cloud;
    const mp = mm.probability != null ? mm.probability : mm.score;
    return `<tr${name === r.main_model ? ' class="cn-best"' : ''}>
      <td><b>${name}</b></td>
      <td class="prob" style="color:${probColor(mp)}">${mp}%</td>
      <td>${verdictCN(mp)}</td>
      <td>低${cl2.low}% / 中${cl2.mid}% / 高${cl2.high}%</td>
      <td>${mm.aod_local != null ? mm.aod_local : '-'}</td>
      <td>${mm.eq_base_km != null ? mm.eq_base_km + ' km' : '-'}</td>
    </tr>`;
  }).join('');
  document.getElementById('modelTbl').innerHTML =
    `<tr><th>模式</th><th>概率</th><th>等级</th><th>云量分层（低/中/高 %）</th><th>AOD</th><th>等效云底</th></tr>` + rows;
  document.getElementById('modelNote').textContent =
    `EC=欧洲中期 GFS=美国全球 ICON=德国全球 CMA=中国全球 ENS=ECMWF 50成员集合均值（成员离散度计入翻车风险）。多模式共识概率 ${r.probability}%（${r.credibility}），本报告以 ${r.main_model} 为主模式。`;

  // 悬日月紧凑入口（完整功能在顶栏「☀ 悬日月」模式）
  renderAlignEntry(r);

  // 天文表
  document.getElementById('astroCard').style.display = 'block';
  renderAltChart(r);
  const a = r.astronomy;
  document.getElementById('astroTbl').innerHTML = `
    <tr><th>事件</th><th>时间</th><th>方位角</th></tr>
    <tr><td>日出</td><td>${a.sunrise || '-'}</td><td>${a.sunrise_az != null ? a.sunrise_az + '° ' + azimuthName(a.sunrise_az) : '-'}</td></tr>
    <tr><td>日落</td><td>${a.sunset || '-'}</td><td>${a.sunset_az != null ? a.sunset_az + '° ' + azimuthName(a.sunset_az) : '-'}</td></tr>
    <tr><td>月出</td><td>${a.moonrise || '-'}</td><td>${a.moonrise_az != null ? a.moonrise_az + '° ' + azimuthName(a.moonrise_az) : '-'}</td></tr>
    <tr><td>月落</td><td>${a.moonset || '-'}</td><td>${a.moonset_az != null ? a.moonset_az + '° ' + azimuthName(a.moonset_az) : '-'}</td></tr>
    <tr><td>蓝调（暮）</td><td>${a.blue_evening ? a.blue_evening[0] + ' - ' + a.blue_evening[1] : '-'}</td>
        <td>月相：${a.moon_phase || '-'} ${a.moon_illum != null ? '· 照度 ' + a.moon_illum + '%' : ''}</td></tr>
    <tr><td>蓝调（晨）</td><td>${a.blue_morning ? a.blue_morning[0] + ' - ' + a.blue_morning[1] : '-'}</td>
        <td>日落线速度：${r.meta.line_speed} km/min</td></tr>`;

  // 48h 表（A单列：单元格内数字 + 白色细条，口径对齐 sample §48h 云量分层表）
  document.getElementById('t48Card').style.display = 'block';
  document.getElementById('t48Tbl').innerHTML =
    `<tr><th>时间</th><th>总云</th><th>高云</th><th>中云</th><th>低云</th></tr>` +
    r.table_48h.map(t => `<tr><td>${t.time}</td>${t48Cell(t.total)}${t48Cell(t.high)}${t48Cell(t.mid)}${t48Cell(t.low)}</tr>`).join('');
  document.getElementById('wxTbl').innerHTML =
    `<tr><th>时间</th><th>气温°C</th><th>降水mm</th><th>风速km/h</th><th>能见度km</th><th>AOD</th></tr>` +
    r.weather_3h.map(t => `<tr><td>${t.time}</td><td>${fmt(t.temp)}</td><td>${fmt(t.precip)}</td><td>${fmt(t.wind)}</td><td>${fmt(t.visibility)}</td><td>${fmt(t.aod)}</td></tr>`).join('');

  // 风险（M7 抛光：无风险条目就不摆空壳卡）
  const riskItems = r.risks || [];
  document.getElementById('riskCard').style.display =
    riskItems.length ? 'block' : 'none';
  document.getElementById('riskList').innerHTML =
    riskItems.map(x => `<li>${x}</li>`).join('');
  // M4：预报→场景映射（§3，静默失败；悬日月模式下 alt 用窗口 best_alt 等效）
  try {
    const SE = window.SceneEngine;
    if (SE && SE.set && SE.mapReportToScene) {
      const c0 = (typeof curCity === 'function' && curCity()) || {};
      const input = { cityId: c0.id || c0.cityId || c0.name || '' };
      let rr = r;
      if (typeof appMode !== 'undefined' && appMode === 'align'
        && typeof alignState !== 'undefined' && alignState && alignState.data) {
        // 悬日月：云/概率沿用主报告，alt 取对准窗口 best_alt（月盘规则由引擎侧按 body 处理）
        const w = alignState.data;
        rr = Object.assign({}, r, {
          astronomy: Object.assign({}, r.astronomy, { key_time: w.best_time }),
          illumination: { sun_alt_deg: (w.best_alt != null ? w.best_alt : null) },
        });
      }
      SE.set(SE.mapReportToScene(rr, input));
      if (typeof paintSceneSub === 'function') paintSceneSub();
    }
  } catch (e) { /* 场景帧缺失不影响报告主体 */ }
  // M4：?debug=1 调参台（复用 h4 console 思想：6 参数直写引擎）
  try { if (typeof bindSceneDebug === 'function') bindSceneDebug(); } catch (e) {}
  // M5：片场化输出（字段不变，只挂 cinema 类；失败不挡主体）
  try { if (typeof paintCinema === 'function') paintCinema(r); } catch (e) {}
  revealVisibleCards();
}

// M4：?debug=1 时在 hero 帧下挂调参台（genus/cover/lit/alt/rain/seed 直调 SceneEngine.set）
function bindSceneDebug() {
  if (!/([?&])debug=1/.test(location.search)) return;
  if (document.getElementById('sceneDbg')) return;
  const SE = window.SceneEngine;
  if (!SE || !SE.set || !SE.params) return;
  const stage = document.getElementById('heroStage');
  if (!stage) return;
  const bar = document.createElement('div');
  bar.id = 'sceneDbg';
  bar.style.cssText = 'font:11px/1.6 ui-monospace,monospace;opacity:.85;padding:6px 10px;'
    + 'border:1px dashed var(--line, #888);border-radius:8px;margin:6px 0;';
  const p = SE.params();
  bar.innerHTML =
    `<b>SCENE-DBG</b> genus <input data-k="genus" value="${p.genus}" size="5">`
    + ` cover <input data-k="cover" value="${p.cover}" size="3">`
    + ` lit <input data-k="lit" value="${p.lit}" size="3">`
    + ` alt <input data-k="alt" value="${p.alt}" size="4">`
    + ` rain <input data-k="rain" value="${p.rain}" size="7">`
    + ` seed <input data-k="seed" value="${p.seed}" size="4">`
    + ` <button type="button">SET</button> <span></span>`;
  bar.querySelector('button').onclick = () => {
    const o = {};
    bar.querySelectorAll('input[data-k]').forEach(el => { o[el.dataset.k] = el.value; });
    if (o.cover != null) o.cover = +o.cover;
    if (o.lit != null) o.lit = +o.lit;
    if (o.alt != null) o.alt = +o.alt;
    if (o.seed != null) o.seed = +o.seed;
    SE.set(o);
    bar.querySelector('span').textContent = 'ok ' + JSON.stringify(SE.params());
  };
  stage.appendChild(bar);
}

// M5：片场化输出（§4.4–4.15；只加类/文案外套，字段与数据流不动）
function paintCinema(r) {
  try {
    var en = document.getElementById('cnTitleEn');
    if (en && typeof verdictCN === 'function') {
      var v = verdictCN(+r.probability);
      en.textContent = v === '大烧' ? 'Tonight, the sky burns.'
        : v === '中烧' ? 'Worth the wait.'
        : v === '小烧' ? 'A gentle glow.'
        : v === '微烧' ? 'A faint whisper.' : 'The sky rests tonight.';
    }
    var p = document.getElementById('heroProb');
    if (p) p.setAttribute('data-cn-prob', String(Math.round(+r.probability)));
    document.querySelectorAll('#weekGrid > div').forEach(function (el) {
      el.classList.add('cn-filmcell');
    });
    document.querySelectorAll('#rankList .rank-row').forEach(function (el) {
      el.classList.add('cn-alsorow');
    });
    document.querySelectorAll('#riskList li').forEach(function (li, i) {
      var t = li.textContent || '';
      li.setAttribute('data-cn-note', String(i).padStart(2, '0')
        + (/翻车|遮挡|失败/.test(t) ? '-RISK' : ''));
    });
    if (typeof window.__cinemaSeq === 'function') window.__cinemaSeq('rendered');
    if (typeof window.__cinemaScan === 'function') window.__cinemaScan(document.getElementById('report-flow'));
    // M6：悬日月模式下 renderAlignPage 后补片场化（tab 挂类 + 日历金框 + 悬月月盘）
    if (typeof paintAlignCinema === 'function') paintAlignCinema();
  } catch (e) { /* 片场化失败不挡报告主体 */ }
}

// M6：悬日月片场化（tab/日历金框/悬月月盘；只加类 + 引擎 moon 开关，数据不动）
function paintAlignCinema() {
  try {
    if (typeof window.__cinemaScan === 'function') {
      window.__cinemaScan(document.getElementById('alignView'));
    }
    // 日历可对准日金框：下次对准日期 = 金框
    var nx = document.getElementById('alignCalNext');
    var m = nx && nx.textContent.match(/(\d{2}-\d{2})/);
    if (m) document.querySelectorAll('#alignCalGrid .week-cell, #alignCalGrid .wx-item').forEach(function (cell) {
      var d = (cell.querySelector('.d') || {}).textContent || '';
      if (d.indexOf(m[1]) >= 0) cell.classList.add('cn-hit');
    });
    // 悬月档：事件 seg 含月出/月落 → 主帧开满月盘（天空按事件时刻推演由引擎 alt 接管）
    var seg = document.getElementById('alignEventSeg');
    var moonMode = seg && /月出|月落/.test(seg.textContent || '');
    var SE = window.SceneEngine;
    if (SE && SE.set) SE.set({ moon: moonMode ? 'full' : 'none' });
  } catch (e) { /* 悬日月片场化失败不挡主体 */ }
}

// M5：序列 start 相（打板 0.9s → 光束 1.2s；render 到达后 rendered 相收尾）
function cinemaSeqStart() {
  try { if (typeof window.__cinemaSeq === 'function') window.__cinemaSeq('start'); } catch (e) {}
}

// ---------------- 霞光模拟时间轴 ----------------
function skyLabelText(off, r) {
  const base = (r && r.astronomy && r.astronomy.key_time) || '';
  return `T${off > 0 ? '+' : ''}${off} min · 基准 ${base}`;
}

function bindSkyCtrl(r) {
  const slider = document.getElementById('skySlider');
  const label = document.getElementById('skyLabel');
  const play = document.getElementById('skyPlay');
  if (!slider) return;
  slider.value = String(skyOffsetMin);
  const apply = () => {
    skyOffsetMin = +slider.value;
    if (label) label.textContent = skyLabelText(skyOffsetMin, r);
    if (typeof renderSky === 'function') renderSky(r, skyOffsetMin);
  };
  const stopPlay = () => {
    if (play && play.__timer) {
      clearInterval(play.__timer);
      play.__timer = null;
      setPlayState(play, false);
    }
  };
  slider.oninput = () => { stopPlay(); apply(); };
  if (play) {
    play.onclick = () => {
      if (play.__timer) { stopPlay(); return; }
      let v = +slider.value;
      if (v >= +slider.max) { v = +slider.min; slider.value = String(v); }
      setPlayState(play, true);
      play.__timer = setInterval(() => {
        v += 2;
        if (v >= +slider.max) {
          v = +slider.max;
          slider.value = String(v);
          apply();
          stopPlay();
          return;
        }
        slider.value = String(v);
        apply();
      }, 130);
    };
  }
  apply();
}

function wxItem(k, v) {
  return `<div class="wx-item"><div class="k">${k}</div><div class="v">${v}</div></div>`;
}

function renderNowcast(r) {
  const nc = r.nowcast;
  const card = document.getElementById('nowcastCard');
  if (!nc || !nc.available) {
    if (nc && nc.reason) {
      card.style.display = 'block';
      document.getElementById('nowcastMeta').textContent =
        `当前不可用：${nc.reason}`;
      document.getElementById('nowcastImg').style.display = 'none';
      document.getElementById('nowcastJudgments').innerHTML = '';
    } else {
      card.style.display = 'none';
    }
    return;
  }
  card.style.display = 'block';
  document.getElementById('nowcastImg').style.display = 'block';
  const mt = document.getElementById('nowcastMeta');
  const dt = nc.minutes_to_event >= 0
    ? `距${r.meta.type === 'sunset' ? '日落' : '日出'}还有 ${Math.floor(nc.minutes_to_event / 60)}h${Math.round(nc.minutes_to_event % 60)}min`
    : `${r.meta.type === 'sunset' ? '日落' : '日出'}已过 ${Math.floor(-nc.minutes_to_event / 60)}h${Math.round(-nc.minutes_to_event % 60)}min`;
  const modeTxt = nc.mode === 'ptree5km' ? ' · P-Tree 5km 亮温' : '';
  mt.innerHTML = `卫星时次 BJT <b>${nc.sat_time_bjt}</b>（${nc.age_min} 分钟前） · ${dt}` +
    ` · 光路云量 <b>${Math.round((nc.cloudy_frac || 0) * 100)}%</b>${modeTxt}`;
  const img = document.getElementById('nowcastImg');
  const ptCrop = (nc.ptree && nc.ptree.crop_png) ? nc.ptree.crop_png : null;
  const src = (nc.mode === 'ptree5km' && ptCrop) ? ptCrop : nc.crop_png;
  if (src) { img.src = src; img.style.display = 'block'; }
  else img.style.display = 'none';
  document.getElementById('nowcastJudgments').innerHTML =
    (nc.judgments || []).map(j => `<li>${j}</li>`).join('');
  renderSatHint('nowcastHint', nc,
                `${r.meta.type === 'sunset' ? '日落' : '日出'}`
                + ` ${(r.astronomy || {}).key_time || ''}`);
  initSatTimeline({
    key: 'nowcast', lat: r.meta.location.lat, lon: r.meta.location.lon,
    az: (r.astronomy || {}).key_az,
    latest: nc.sat_time_bjt, ageMin: nc.age_min,
    label: r.meta.type === 'sunset' ? '日落' : '日出',
    minutesToEvent: nc.minutes_to_event,
  });
  // 自动刷新：临近窗口内每 10 分钟拉取最新卫星帧
  clearNcTimer();
  if (nc.available && nc.minutes_to_event <= 360
      && nc.minutes_to_event >= -90) {
    ncTimer = setInterval(async () => {
      try {
        const c2 = curCity();
        const res = await fetch(
          `${API}/api/nowcast?lat=${c2.lat}&lon=${c2.lon}` +
          `&date=${r.meta.date}&type=${r.meta.type}`);
        if (!res.ok) return;
        const nr = await res.json();
        if (nr && nr.available) {
          curReport.nowcast = nr;
          renderNowcast(curReport);
        }
      } catch (e) { /* 静默重试 */ }
    }, 10 * 60 * 1000);
  }
}

function renderAltChart(r) {
  const el = document.getElementById('altChart');
  const ch = window._altChart && !window._altChart.isDisposed()
    ? window._altChart : echarts.init(el);
  window._altChart = ch;
  const trk = r.tracks;
  if (!trk || !trk.time || !trk.time.length) { ch.clear(); return; }
  // 事件标记点：轨迹为 30 分钟步长，须取最近点（旧版 indexOf 精确时刻永远 -1，标记从未显示）
  const nearestIdx = t => {
    if (!t) return -1;
    const [hh, mm] = t.split(':').map(Number);
    const target = hh * 60 + mm;
    let best = -1, bd = 1e9;
    trk.time.forEach((tt, i) => {
      const [th, tm] = tt.split(':').map(Number);
      const d = Math.abs(th * 60 + tm - target);
      if (d < bd) { bd = d; best = i; }
    });
    return best;
  };
  const marks = [];
  const a = r.astronomy;
  const addMark = (t, name, color, alt) => {
    const i = nearestIdx(t);
    if (i < 0) return;
    marks.push({value: [i, alt == null ? 0 : alt], name,
      itemStyle: {color}, label: {show: true, formatter: name,
      position: 'bottom', fontSize: 9, color}});
  };
  addMark(a.sunrise, '日出', '#f2a03d');
  addMark(a.sunset, '日落', '#e8602a');
  addMark(a.moonrise, '月出', '#c9c9c9');
  addMark(a.moonset, '月落', '#9b9b9b');
  // 悬日月对准窗口（绿点标在真实高度角上）
  (r.alignment || []).forEach(w => {
    if (w.best_time) {
      addMark(w.best_time,
              `${w.type}${w.period_name ? '·' + w.period_name : ''}`,
              '#2e9e44', w.best_alt);
    }
  });
  const ink = chartInk();
  ch.setOption({
    tooltip: chartTip({trigger: 'axis',
      formatter: ps => ps.map(p => p.seriesName === '事件'
        ? `📍 ${p.name}`
        : `${p.seriesName} ${p.value}°`).join('<br>')
        + '<br>' + ps[0].axisValue}),
    legend: {data: ['太阳', '月亮'], top: 0,
             textStyle: {fontSize: 11, color: ink.ink2}},
    grid: {left: 42, right: 16, top: 30, bottom: 24},
    xAxis: Object.assign({type: 'category', data: trk.time}, axisInk(ink),
            {axisLabel: {fontSize: 10, interval: 5, color: ink.ink2}}),
    yAxis: Object.assign({type: 'value', name: '高度角°', max: 90, min: -10,
            nameTextStyle: {fontSize: 10, color: ink.ink2}}, axisInk(ink),
            {axisLine: {show: false}}),
    series: [
      {name: '太阳', type: 'line', data: trk.sun_alt, showSymbol: false,
       lineStyle: {color: '#e8973a', width: 2.2}, smooth: true,
       // 日/月曲线下方铺一层柔光，低角度段（霞光所在）更醒目
       areaStyle: {color: areaGrad('rgba(232,151,58,.28)', 'rgba(232,151,58,0)')},
       markLine: {silent: true, symbol: 'none', label: {show: false},
                  data: [{yAxis: 0}],
                  lineStyle: {color: ink.axis, type: 'dashed', opacity: .8}}},
      {name: '月亮', type: 'line', data: trk.moon_alt, showSymbol: false,
       lineStyle: {color: '#b8c4d8', width: 2}, smooth: true,
       areaStyle: {color: areaGrad('rgba(150,170,205,.22)', 'rgba(150,170,205,0)')}},
      {name: '事件', type: 'scatter', data: marks, symbolSize: 8,
       zlevel: 3},
    ],
  });
}

function renderWeek(r) {
  const card = document.getElementById('weekCard');
  const wk = r.week || [];
  if (!wk.length) { card.style.display = 'none'; return; }
  card.style.display = 'block';
  document.getElementById('weekGrid').innerHTML = wk.map(function(d) {
    const isSel = d.date === r.meta.date;
    return '<div class="week-cell ' + (isSel ? 'today' : '') + '" data-date="'
      + d.date + '" role="button" tabindex="0" aria-label="' + d.date + ' '
      + esc(d.weekday) + ' 概率 ' + d.prob + '%">' +
      '<div class="d">' + d.date.slice(5) + ' ' + d.weekday + '</div>' +
      '<div class="p" style="color:' + probColor(d.prob) + '">' + d.prob + '%</div>' +
      '<div class="v">' + d.verdict + '</div></div>';
  }).join('');
}

function pickDate(ds) {
  document.getElementById('dateSel').value = ds;
  syncDateUI();
  onParamsChanged();          // v2.36：只是选了哪一天，点「查询」才取数
}

function renderRanking(r) {
  const card = document.getElementById('rankCard');
  const list = document.getElementById('rankList');
  if (!r.ranking || !r.ranking.length) {
    card.style.display = 'none';
    return;
  }
  card.style.display = 'block';
  const mode = (document.querySelector('#rankSeg button.active') || {})
    .dataset ? document.querySelector('#rankSeg button.active').dataset.v
    : 'prob';
  const rows = [...r.ranking].sort((a, b) =>
    mode === 'quality' ? b.quality - a.quality : b.prob - a.prob);
  list.innerHTML = rows.map((it, i) => {
    const v = mode === 'quality' ? it.quality : it.prob;
    const dir = it.brg != null ? azimuthName(it.brg) : '';
    const dist = it.dist != null ? it.dist + 'km' : '';
    return `<div class="cmp-row rank-row" data-lat="${it.lat}" data-lon="${it.lon}"
                 data-name="${it.name}">
      <div class="city">${i + 1}. ${it.name}<div class="win">${dir} ${dist}</div></div>
      <div class="bar"><div style="width:${Math.min(100, v / 45 * 100)}%"></div></div>
      <div class="pct" style="color:${probColor(it.prob)}">${
        mode === 'quality' ? it.quality.toFixed(0) : it.prob + '%'}</div>
      <div class="win">${mode === 'quality' ? '质量' : it.verdict || verdictCN(it.prob)}</div>
    </div>`;
  }).join('');
  list.querySelectorAll('.rank-row').forEach(row => {
    row.style.cursor = 'pointer';
    row.onclick = () => saveMyLocation(+row.dataset.lat, +row.dataset.lon);
  });
}

function renderFeedback(r) {
  const card = document.getElementById('feedbackCard');
  if (r.outcome) {
    card.style.display = 'block';
    card.querySelector('.desc').textContent =
      `该场已回填实况：${r.outcome}。感谢记录，校准数据 +1。`;
    document.getElementById('fbSeg').style.display = 'none';
    document.getElementById('btnFeedback').style.display = 'none';
    document.getElementById('fbDone').style.display = 'none';
    return;
  }
  if (!r.awaiting_outcome) { card.style.display = 'none'; return; }
  card.style.display = 'block';
  const a = r.astronomy;
  const typeTxt = r.meta.type === 'sunset' ? '日落' : '日出';
  document.getElementById('fbText').textContent =
    `${r.meta.date} ${typeTxt}（${a.key_time}）预报为「${r.verdict} ${r.probability}%」。` +
    `拍到了吗？按实际火烧云强度回填，用于本地校准。`;
  document.getElementById('fbSeg').style.display = 'flex';
  document.getElementById('btnFeedback').style.display = 'inline-block';
  document.getElementById('fbDone').style.display = 'none';
}
function fmt(v, pct = '', suffix = '') {
  if (v === null || v === undefined || v === '') return '-';
  return (typeof v === 'number' ? Math.round(v * 10) / 10 : v) + pct + suffix;
}
function weekday(ds) {
  return '周' + '日一二三四五六'[new Date(ds + 'T12:00:00').getDay()];
}
function azimuthName(az) {
  if (az == null) return '';
  const dirs = ['北', '东北偏北', '东北', '东北偏东', '东', '东南偏东', '东南', '东南偏南',
                '南', '西南偏南', '西南', '西南偏西', '西', '西北偏西', '西北', '西北偏北'];
  return dirs[Math.round(az / 22.5) % 16];
}
function probColor(p) {
  // 阈值与校准曲线同源分流（v2.35）：PRD 概率整体高（基率 77.6%，p50=80），
  // 沿用上海阈值会全红失真；分位对齐（PRD 80/68 ≈ 上海 38/26 的相对位置）
  if (curRegion === 'prd') {
    if (p >= 80) return '#d94f1e';
    if (p >= 68) return '#e8973a';
    return '#4a8a4a';
  }
  if (p >= 38) return '#d94f1e';
  if (p >= 26) return '#e8973a';
  return '#4a8a4a';
}
// 维度分/可行性分配色：高分=好（绿）→ 低分=差（红）。
// 注意与 probColor 语义相反：那个是火烧云概率，高分=大烧=红。
function scoreColor(v) {
  const x = Number(v);
  if (!Number.isFinite(x)) return 'var(--ink-2)';
  if (x >= 65) return '#1e8a4c';
  if (x >= 45) return '#a8720a';
  return '#c93a3f';
}
function verdictCN(p) {
  // 与后端 verdict_word 同源分流：PRD 曲线语义=「有受光画布可烧」（基率 77.6%），
  // 阈值重定；上海语义=「值得记录的大烧」（基率 27.7%）
  if (curRegion === 'prd') {
    if (p < 50) return '不烧'; if (p < 65) return '微烧';
    if (p < 75) return '小烧'; if (p < 85) return '中烧'; return '大烧';
  }
  if (p < 8) return '不烧'; if (p < 18) return '微烧';
  if (p < 28) return '小烧'; if (p < 40) return '中烧'; return '大烧';
}
const DIM_CN = {dim1: '中高层云分布', dim2: '光路城市云况', dim3: '近地降水',
  dim4: '大气能见度/颗粒物', dim5: '云运动趋势', dim6: '气压变化',
  dim7: 'CAMS 霾/气溶胶', dim8: '高层湿层卷云潜力'};
function dimCN(k) { return DIM_CN[k] || k; }

// ---- A单列：48h 行内条 + 8 维双打分 ----
// 48h 单元格：数字 + 同格白色细条（条宽 = 云量%，口径同 sample 云量分层表）
function t48Cell(v) {
  const n = Math.max(0, Math.min(100, +v || 0));
  return `<td class="t48-cell"><span class="t48-v">${v}</span>`
    + `<i class="t48-bar"><b style="width:${n}%"></b></i></td>`;
}

// 光路加权：map.points 按距离衰减加权取均值（口径同 report.py map_points 的距离衰减，
// 权重 1/(1+dist/100)）。纯派生，不改后端。
let _pwKey = null, _pwVal = null;
function pathWeighted(r) {
  const pts = (r && r.map && r.map.points) || [];
  if (!pts.length) return null;
  if (r === _pwKey) return _pwVal;
  const keys = ['canvas', 'low', 'mid', 'high', 'aod', 'prob'];
  const acc = {}; keys.forEach(k => { acc[k] = 0; });
  let sw = 0;
  pts.forEach(p => {
    const w = 1 / (1 + (+p.dist || 0) / 100);
    sw += w;
    keys.forEach(k => { acc[k] += (+p[k] || 0) * w; });
  });
  if (!sw) return null;
  const out = {};
  keys.forEach(k => { out[k] = acc[k] / sw; });
  _pwKey = r; _pwVal = out;
  return out;
}

// 区间打分：理想区间中心给 10，贴边给 8，越界后每偏 3 扣 1（下限 1）。
// 早期版本「区间内一律 10」会让副分普遍顶格、看不出区分度，故改为连续衰减。
function _bandScore(v, lo, hi) {
  if (v == null || isNaN(v)) return null;
  const mid = (lo + hi) / 2, half = Math.max(1e-6, (hi - lo) / 2);
  const off = Math.abs(v - mid) - half;          // <=0 表示落在理想区间内
  const s = off <= 0 ? 10 - (Math.abs(v - mid) / half) * 3
                     : 8 - off / 3;
  return Math.max(1, Math.min(10, Math.round(s)));
}
function _cloudBandScore(low, mid, high) {
  const hi2 = _bandScore(((+mid || 0) + (+high || 0)) / 2, 30, 70);
  const lo2 = _bandScore(+low || 0, 0, 30);
  if (hi2 == null && lo2 == null) return null;
  return Math.round(((hi2 == null ? lo2 : hi2) + (lo2 == null ? hi2 : lo2)) / 2);
}
// 8 维副分：光路加权视角（无光路对应字段时返回 null → 显示同主分）
function dimPathScore(key, m, r) {
  const pw = pathWeighted(r);
  if (!pw) return null;
  const c = m.cloud || {};
  switch (key) {
    case 'dim1':   // 中高层云分布：光路加权
      return _cloudBandScore(pw.low, pw.mid, pw.high);
    case 'dim2':   // 光路城市云况：光路可达画布量
      return _bandScore(pw.canvas, 30, 80);
    case 'dim4':   // 能见度/颗粒物：光路 AOD 反推
      return _bandScore(pw.aod, 0, 0.35);
    case 'dim7':   // CAMS 霾/气溶胶：光路 AOD
      return _bandScore(pw.aod, 0, 0.3);
    case 'dim8':   // 高层湿层卷云潜力：光路高云
      return _bandScore(pw.high, 30, 100);
    default:       // dim3/5/6：光路无对应观测，取本地同值（不造数）
      return null;
  }
}
function dimDetail(key, m, r) {
  const cl = m.cloud || {};
  switch (key) {
    case 'dim1': return `低云 ${cl.low != null ? cl.low : '-'}% / 中云 ${cl.mid != null ? cl.mid : '-'}% / 高云 ${cl.high != null ? cl.high : '-'}%（理想：中高云 30-70%、低云 <30%）`;
    case 'dim2': return `光路距离加权：中高云均值 ${m.canvas_avg != null ? m.canvas_avg : '-'}%、低云均值 ${m.low_avg != null ? m.low_avg : '-'}%（中高云为火烧云画布；云边界 ${m.boundary_km != null ? m.boundary_km + ' km' : '≥580 km'}）`;
    case 'dim3': return `本地降水概率 ${m.weather && m.weather.precip_prob != null ? m.weather.precip_prob : '-'}%，光路沿线平均已计入`;
    case 'dim4': return `水平能见度 ${m.weather && m.weather.visibility_km != null ? m.weather.visibility_km + ' km' : '-'}，PM2.5≈${m.pm25 != null ? m.pm25 : '-'} μg/m³`;
    case 'dim5': return `按关键时刻前后 3h 云量变化评估：云层开合/系统过境形成云边界（回测实证：变化大更易烧）`;
    case 'dim6': return `3 小时气压变化趋势（升压趋向晴好）`;
    case 'dim7': return `CAMS AOD≈${m.aod_local != null ? m.aod_local : '-'}（本地），光路均值 ${m.aod_path != null ? m.aod_path : '-'}（≤0.15 通透，>0.5 污烧）`;
    case 'dim8': return `300-500hPa 相对湿度（高云为火烧云直接画布，湿层辅助）`;
    default: return '';
  }
}

// ---------------- ECharts 地图 ----------------
function baseMapOption() {
  const ink = chartInk();
  return {
    geo: {
      map: 'guangdong',
      roam: true,
      label: {show: true, color: ink.axis, fontSize: 10},
      itemStyle: {areaColor: ink.area, borderColor: ink.border},
      emphasis: {label: {color: ink.ink}, itemStyle: {areaColor: ink.line}},
    },
    visualMap: null,
  };
}

/* 网格格子的像素边长：后端 map.points 是 0.5° 网格，初值按容器宽度粗估
   （1° ≈ 容器宽/24 像素），geo 布局完成后再用 calibrateGrid() 按实测比例尺校准。
   用 rect 符号铺成「格子热力图」，对齐样张观感，而不是把上百个格点画成散点噪点。 */
function gridRectSize(el) {
  const w = (el && el.clientWidth) || 900;
  return Math.max(14, Math.min(52, Math.round(w / 24)));
}

/* geo 渲染完成后按实测比例尺校准格子尺寸：横向/纵向各量一格像素，分别缩到 k 倍。
   经纬度一格在等距圆柱投影下横竖不等长（纬度越高越明显），只用单一边长会横竖串格，
   所以要分开算 [w, h]。取不到像素时保持初值，不抛错。只改 series[0]。 */
function calibrateGrid(chart, data, k) {
  if (!data || !data.length || !chart) return;
  const lons = [], lats = [];
  data.forEach(p => {
    const v = p.value || [];
    if (v[0] != null) lons.push(+v[0]);
    if (v[1] != null) lats.push(+v[1]);
  });
  const stepOf = arr => {
    arr = Array.from(new Set(arr)).sort((a, b) => a - b);
    let s = Infinity;
    for (let i = 1; i < arr.length; i++) {
      const d = +(arr[i] - arr[i - 1]).toFixed(4);
      if (d > 1e-6) s = Math.min(s, d);
    }
    return isFinite(s) ? s : 0.5;
  };
  const sx = stepOf(lons), sy = stepOf(lats);
  const kk = k == null ? 0.86 : k;
  try {
    const o = chart.convertToPixel({geoIndex: 0}, [113, 23]);
    const px = chart.convertToPixel({geoIndex: 0}, [113 + sx, 23]);
    const py = chart.convertToPixel({geoIndex: 0}, [113, 23 + sy]);
    if (!o || !px || !py) return;
    const w = Math.abs(px[0] - o[0]) * kk;
    const h = Math.abs(py[1] - o[1]) * kk;
    if (!isFinite(w) || !isFinite(h) || w < 5 || h < 5 || w > 400 || h > 400) return;
    chart.setOption({series: [{symbolSize: [Math.round(w), Math.round(h)]}]});
  } catch (e) { /* 保持初值 */ }
}

/* 光路距离刻度（100-400km）：沿事件方位每 100km 打一个点并标注公里数，
   给「霞光带在多少公里外」一个可读的量纲（与后端光路采样 0-580km 同量级）。
   标签上下交替，避免密排时互相压字。 */
function distTickSeries(obs, az, color) {
  if (!obs || az == null || isNaN(+az)) return null;
  return {
    type: 'scatter', coordinateSystem: 'geo', zlevel: 2, silent: true,
    symbol: 'circle', symbolSize: 4, itemStyle: {color: color, opacity: .95},
    data: [100, 200, 300, 400].map((d, i) => {
      const g = destPoint(obs.lat, obs.lon, +az, d);
      return {value: [g.lon, g.lat], name: d + 'km',
              label: {position: i % 2 ? 'bottom' : 'top'}};
    }),
    label: {show: true, formatter: '{b}', fontSize: 10,
            color: color, opacity: .9},
    tooltip: {show: false},
  };
}

/* 地图装帧：右下/左下比例尺 + 右上指北针。比例尺按当前 roam 缩放实时换算像素
   长度，拖动缩放后重画——否则会给出错误的距离感。geo 不旋转，指北针恒定朝上。 */
function drawMapChrome(chart, el) {
  if (!chart || !el) return;
  const draw = () => {
    const H = el.clientHeight || 470;
    const W = el.clientWidth || 900;
    const ink = chartInk();
    let pxLen = 0, txt = '';
    try {
      const y0 = H - 30;
      const g0 = chart.convertFromPixel({geoIndex: 0}, [30, y0]);
      const g1 = chart.convertFromPixel({geoIndex: 0}, [130, y0]);
      const km100 = Math.abs(g1[0] - g0[0]) * 111.32
        * Math.cos((g0[1] || 23) * Math.PI / 180);
      if (isFinite(km100) && km100 > 1) {
        const nice = [25, 50, 100, 200, 300, 500].reduce((a, b) =>
          Math.abs(b - km100) < Math.abs(a - km100) ? b : a, 25);
        pxLen = Math.round(100 * nice / km100);
        txt = nice + ' km';
      }
    } catch (e) { pxLen = 0; }
    const g = [];
    if (pxLen > 8 && pxLen < 320) {
      const x0 = 30, y0 = H - 30, x1 = x0 + pxLen;
      const st = {stroke: ink.ink2, lineWidth: 2};
      g.push({type: 'line', shape: {x1: x0, y1: y0, x2: x1, y2: y0}, style: st, silent: true});
      g.push({type: 'line', shape: {x1: x0, y1: y0 - 5, x2: x0, y2: y0 + 5}, style: st, silent: true});
      g.push({type: 'line', shape: {x1: x1, y1: y0 - 5, x2: x1, y2: y0 + 5}, style: st, silent: true});
      g.push({type: 'text', left: x1 + 8, top: y0 - 9,
              style: {text: txt, fill: ink.ink2, font: '11px sans-serif'}, silent: true});
    }
    g.push({type: 'text', left: W - 14, top: 12, silent: true,
            style: {text: 'N', fill: '#e8602a', font: 'bold 13px sans-serif',
                    textAlign: 'center'}});
    g.push({type: 'polygon', silent: true,
            shape: {points: [[W - 9, 30], [W - 14, 42], [W - 4, 42]]},
            style: {fill: '#e8602a'}});
    chart.setOption({graphic: g});
  };
  if (!chart.__chromeBound) {
    chart.__chromeBound = true;
    chart.on('georoam', draw);
  }
  draw();
}
function registerMap() {
  if (!gdGeo) return false;   // geo 还没到位（或加载失败）
  echarts.registerMap('guangdong', gdGeo);
  return true;
}

/* 底图没就绪时，让这张地图卡退化成一句说明。
   否则 setOption 里 geo.map 指向未注册的地图，ECharts 会抛错，
   异常会一路冒到 loadReport 的 catch 被当成「取数失败」——一个慢半秒的
   geo 请求就能把整份报告带崩。 */
function geoFallback(elId) {
  if (gdGeo) return false;
  const el = document.getElementById(elId);
  if (el) {
    el.innerHTML = '<div class="map-fallback">地图底图未就绪（data/guangdong.json '
      + '加载中或不可用）。其余卡片不受影响，稍后重新查询即可。</div>';
  }
  return true;
}

function renderBurnMap(r) {
  registerMap();
  if (geoFallback('burnMap')) return;
  const el = document.getElementById('burnMap');
  mapChart = mapChart || echarts.init(el);
  mapChart.clear();
  const mode = document.querySelector('#mapModeSeg button.active').dataset.v;
  const geo_ = r.map.geometry || {};
  const v = Math.max(1, geo_.line_speed || r.meta.line_speed || 18);  // km/min
  const W = Math.max(50, geo_.glow_band_km || 350);                   // 霞光带宽
  const tStart = geo_.t_start_min != null ? geo_.t_start_min : 0;
  // 基础分点（扫掠前的静态值）
  const pts0 = r.map.points.map(pt => ({
    lon: pt.lon, lat: pt.lat, dist: pt.dist, brg: pt.brg,
    prob: pt.prob, quality: pt.quality, canvas: pt.canvas,
    low: pt.low, mid: pt.mid, high: pt.high, aod: pt.aod,
  }));
  window._sweep = {pts0, v, W, tStart, mode};

  const pts = pts0.map(pt => ({
    value: [pt.lon, pt.lat, sweepValue(pt, 0)],
    prob: pt.prob, quality: pt.quality, canvas: pt.canvas,
    low: pt.low, mid: pt.mid, high: pt.high, aod: pt.aod,
  }));
  const obs = r.map.observer;
  // 日落方向射线（约 500km 视觉线）
  const azRad = r.map.sun_az * Math.PI / 180;
  const dx = Math.sin(azRad) * 4.6, dy = Math.cos(azRad) * 4.6;
  const gs = gridRectSize(el);
  const ticks = distTickSeries(obs, r.map.sun_az, '#e8602a');
  // 只给「值得看」的格子标数字（约前 30%），否则上百个数字糊成一片（对齐样张的疏朗感）
  const key = mode === 'quality' ? 'quality' : 'prob';
  const sorted = pts.map(p => +p[key] || 0).sort((a, b) => a - b);
  const thr = Math.max(mode === 'quality' ? 55 : 34,
                       sorted[Math.floor(sorted.length * 0.7)] || 0);
  mapChart.setOption({
    ...baseMapOption(),
    tooltip: chartTip({
      formatter: p => {
        if (!p.data || p.data.prob === undefined) return (p.name || '');
        if (mode === 'quality') {
          return `质量分 ${p.data.quality}<br>中高云 ${p.data.canvas}% · 低云 ${p.data.low}%<br>AOD ${p.data.aod ?? '-'}`;
        }
        return `当地概率 ${p.data.prob}%<br>中高云 ${p.data.canvas}% · 低云 ${p.data.low}%<br>AOD ${p.data.aod ?? '-'}`;
      },
    }),
    visualMap: {
      min: 0, max: mode === 'quality' ? 100 : 60, show: true, seriesIndex: 0,
      text: mode === 'quality' ? ['通透', '差'] : ['烧', '不烧'],
      inRange: {color: ['#fdf6ee', '#f5c98a', '#e8602a', '#b3260e']},
      right: 16, bottom: 16, textStyle: {color: '#888', fontSize: 11},
      calculable: false,
    },
    series: [
      {
        type: 'scatter', coordinateSystem: 'geo', zlevel: 1,
        symbol: 'rect', symbolSize: [gs, gs],
        itemStyle: {borderColor: 'rgba(255,255,255,.45)', borderWidth: 1,
                    opacity: .88},
        data: pts,
        label: {
          show: true,
          formatter: p => {
            const v = mode === 'quality' ? p.data.quality : p.data.prob;
            return v >= thr ? Math.round(v) : '';
          },
          fontSize: 10, color: '#fff', fontWeight: 600,
          textBorderColor: 'rgba(0,0,0,.45)', textBorderWidth: 2,
        },
      },
      {
        type: 'lines', coordinateSystem: 'geo', zlevel: 2,
        lineStyle: {color: '#e8602a', width: 2.5, curveness: 0, type: 'dashed'},
        data: [{coords: [[obs.lon, obs.lat], [obs.lon + dx, obs.lat + dy]]}],
      },
      ...(ticks ? [ticks] : []),
      {
        type: 'effectScatter', coordinateSystem: 'geo', zlevel: 3,
        symbolSize: 13, itemStyle: {color: '#e8602a'},
        data: [{value: [obs.lon, obs.lat],
                name: `${curCity().name} ${Math.round(+r.probability)}%`}],
        label: {show: true, formatter: '{b}', position: 'bottom',
                fontSize: 11, color: '#fff', fontWeight: 700,
                backgroundColor: 'rgba(232,96,42,.92)',
                padding: [3, 7], borderRadius: 4},
        tooltip: {formatter: () => '观测点'},
      },
    ],
  });
  calibrateGrid(mapChart, pts, 0.74);
  drawMapChrome(mapChart, el);
  bindSweepCtrl(r);
  bindMapPick();
}

/* ---- 地图选点：选点模式下点击地图取经纬度 → 站点探测 ---- */
let pickMode = false;

function bindMapPick() {
  if (mapChart.__pickBound) return;   // zr 事件跨 clear/setOption 保留，只绑一次
  mapChart.__pickBound = true;
  let down = null;
  mapChart.getZr().on('mousedown', e => { down = [e.offsetX, e.offsetY]; });
  mapChart.getZr().on('click', e => {
    if (!pickMode) return;
    // roam 拖拽保护：按下-抬起位移 > 5px 视为平移而非选点
    if (down && Math.hypot(e.offsetX - down[0], e.offsetY - down[1]) > 5) return;
    const pt = mapChart.convertFromPixel({geoIndex: 0},
                                         [e.offsetX, e.offsetY]);
    if (!pt || pt[0] == null) return;   // geo 未渲染（底图加载失败）
    showProbe(Math.round(pt[1] * 1000) / 1000,
              Math.round(pt[0] * 1000) / 1000);
  });
}

async function showProbe(la, lo) {
  const box = document.getElementById('pickResult');
  if (!box) return;
  box.style.display = 'block';
  box.innerHTML = '<p class="desc">探测中（海拔 / 遮蔽角 / 光污染）…</p>';
  try {
    const res = await fetch(`${API}/api/site_probe?lat=${la}&lon=${lo}`);
    if (!res.ok) throw new Error(await res.text());
    const s = await res.json();
    const mask = s.horizon_mask || [];
    const maxH = mask.length ? Math.max(...mask) : 0;
    const maxAz = mask.length ? mask.indexOf(maxH) * 22.5 : 0;
    box.innerHTML = `
      <div class="pick-card">
        <b>📍 ${la.toFixed(3)}°N, ${lo.toFixed(3)}°E</b>
        <div>海拔 <b>${s.elevation != null ? s.elevation + ' m' : '—'}</b>
          · 光污染 <b>Bortle ${s.bortle}</b>${s.bortle_desc ? '（' + s.bortle_desc + '）' : ''}
          · 地平线最高遮蔽 <b>${maxH.toFixed(1)}°</b>（方位 ${maxAz}°）</div>
        <button id="btnSetLoc" class="primary">设为我的位置并查询</button>
      </div>`;
    document.getElementById('btnSetLoc').onclick = () => saveMyLocation(la, lo);
  } catch (e) {
    box.innerHTML = '<p class="desc">探测失败：' + e.message + '</p>';
  }
}

function saveMyLocation(la, lo) {
  localStorage.setItem('gc_myloc',
                       JSON.stringify({lat: la, lon: lo, name: '我的位置'}));
  localStorage.setItem('gc_myloc_active', '1');
  if (!cities.some(c => c.id === 'myloc')) {
    cities.push({id: 'myloc', name: '我的位置', region: '自定义',
                 lat: la, lon: lo});
  }
  const idx = cities.findIndex(c => c.id === 'myloc');
  const sel = document.getElementById('citySel');
  if (!sel.querySelector(`option[value="${idx}"]`)) {
    const o = document.createElement('option');
    o.value = idx;
    o.textContent = '我的位置 · 自定义';
    sel.appendChild(o);
  }
  sel.value = String(idx);
  window.__gcCities = cities;
  if (typeof window.__compareRenderChips === 'function') {
    window.__compareRenderChips();
  }
  loadReport(false);
}

function bindPickControls() {
  const btn = document.getElementById('btnPickMode');
  if (!btn) return;
  btn.onclick = () => {
    pickMode = !pickMode;
    btn.textContent = pickMode ? '📍 选点模式：开（点击地图）' : '📍 选点模式：关';
    btn.classList.toggle('on', pickMode);
  };
  const go = document.getElementById('btnPickGo');
  if (go) {
    go.onclick = () => {
      const la = +document.getElementById('pickLat').value;
      const lo = +document.getElementById('pickLon').value;
      if (!la || !lo) return;
      showProbe(Math.round(la * 1000) / 1000, Math.round(lo * 1000) / 1000);
    };
  }
}

/* ---- 霞光带扫掠：t 为事件后分钟 ----
   点沿日落方位投影距离 d_proj，照亮窗口 [tStart + d_proj/v, +W/v] */
function sweepValue(pt, tMin) {
  const s = window._sweep;
  if (!s) return pt.prob ?? 0;
  const daz = Math.abs(((pt.brg - (curReport.map.sun_az || 0)) + 180) % 360 - 180);
  const dProj = Math.max(0, (pt.dist || 0) * Math.cos(daz * Math.PI / 180));
  const on0 = s.tStart + dProj / s.v;
  const on1 = on0 + s.W / s.v;
  const ramp = 3;   // 分钟渐入渐出
  let f;
  if (tMin >= on0 + ramp && tMin <= on1 - ramp) f = 1;
  else if (tMin > on0 - ramp && tMin < on0 + ramp) f = (tMin - (on0 - ramp)) / (2 * ramp);
  else if (tMin > on1 - ramp && tMin < on1 + ramp) f = 1 - (tMin - (on1 - ramp)) / (2 * ramp);
  else f = 0;
  f = Math.max(0, Math.min(1, f));
  const base = s.mode === 'quality' ? (pt.quality || 0) : (pt.prob ?? pt.score ?? 0);
  return Math.round(base * (0.35 + 0.65 * f) * 10) / 10;
}

function sweepLabel(tMin) {
  const r = curReport;
  if (!r) return '';
  const [hh, mm] = (r.astronomy.key_time || '00:00').split(':').map(Number);
  const d = new Date();
  d.setHours(hh, mm + Math.round(tMin), 0, 0);
  const t = String(d.getHours()).padStart(2, '0') + ':' +
            String(d.getMinutes()).padStart(2, '0');
  const line = r.meta.type === 'sunrise' ? '日出线' : '日落线';
  return (tMin === 0 ? `${t} · ${line}` : `${t} · +${Math.round(tMin)}min`);
}

function bindSweepCtrl(r) {
  const slider = document.getElementById('sweepSlider');
  const play = document.getElementById('sweepPlay');
  const label = document.getElementById('sweepLabel');
  if (!slider || !play) return;
  const geo_ = r.map.geometry || {};
  const tEnd = Math.min(40, Math.max(15, Math.ceil((geo_.t_end_min || 10) +
    (geo_.glow_band_km || 350) / (geo_.line_speed || 18))));
  slider.max = String(tEnd);
  slider.value = '0';
  label.textContent = sweepLabel(0);

  if (window._sweepTimer) { clearInterval(window._sweepTimer); window._sweepTimer = null; }
  setPlayState(play, false);

  const applyT = t => {
    const pts = window._sweep.pts0.map(pt => ({
      value: [pt.lon, pt.lat, sweepValue(pt, t)],
      prob: pt.prob, quality: pt.quality, canvas: pt.canvas,
      low: pt.low, mid: pt.mid, high: pt.high, aod: pt.aod,
    }));
    mapChart.setOption({series: [{data: pts}]});
    label.textContent = sweepLabel(t);
  };
  slider.oninput = () => {
    if (window._sweepTimer) { clearInterval(window._sweepTimer); window._sweepTimer = null; setPlayState(play, false); }
    applyT(+slider.value);
  };
  play.onclick = () => {
    if (window._sweepTimer) {
      clearInterval(window._sweepTimer); window._sweepTimer = null;
      setPlayState(play, false);
      return;
    }
    setPlayState(play, true);
    let t = +slider.value >= +slider.max ? 0 : +slider.value;
    window._sweepTimer = setInterval(() => {
      t += 0.5;
      if (t > +slider.max) { t = 0; }
      slider.value = String(Math.round(t));
      applyT(t);
    }, 120);
  };
}

function renderRevMap(r) {
  registerMap();
  if (geoFallback('revMap')) return;
  const el = document.getElementById('revMap');
  revChart = revChart || echarts.init(el);
  revChart.clear();
  const obs = r.map.observer;
  const azRad = r.reverse_burn.az * Math.PI / 180;
  const dx = Math.sin(azRad) * 4.2, dy = Math.cos(azRad) * 4.2;
  const pts = r.map.points.map(pt => ({
    value: [pt.lon, pt.lat, pt.rev],
    rev: pt.rev, high: pt.high, aod: pt.aod,
  }));
  const rgs = gridRectSize(document.getElementById('revMap'));
  const rticks = distTickSeries(obs, r.reverse_burn.az, '#3a6ea5');
  const rvals = pts.map(p => +p.rev || 0).sort((a, b) => a - b);
  const rthr = Math.max(30, rvals[Math.floor(rvals.length * 0.7)] || 0);
  revChart.setOption({
    ...baseMapOption(),
    tooltip: chartTip({
      formatter: p => p.data && p.data.rev !== undefined
        ? `反烧分 ${p.data.rev}<br>高云 ${p.data.high}% · AOD ${p.data.aod ?? '-'}`
        : (p.name || ''),
    }),
    visualMap: {
      min: 0, max: 60, show: true, seriesIndex: 0,
      text: ['烧', '不烧'], inRange: {color: ['#f2efe9', '#9db8d8', '#3a6ea5', '#1c3f6e']},
      right: 16, bottom: 16, textStyle: {color: '#888', fontSize: 11},
    },
    series: [
      {
        type: 'scatter', coordinateSystem: 'geo', zlevel: 1,
        symbol: 'rect', symbolSize: [rgs, rgs],
        itemStyle: {borderColor: 'rgba(255,255,255,.45)', borderWidth: 1,
                    opacity: .88},
        data: pts,
        label: {
          show: true,
          formatter: p => p.data.rev >= rthr ? Math.round(p.data.rev) : '',
          fontSize: 10, color: '#fff', fontWeight: 600,
          textBorderColor: 'rgba(0,0,0,.45)', textBorderWidth: 2,
        },
      },
      {
        type: 'lines', coordinateSystem: 'geo', zlevel: 2,
        lineStyle: {color: '#3a6ea5', width: 2.5, type: 'dashed'},
        data: [{coords: [[obs.lon, obs.lat], [obs.lon + dx, obs.lat + dy]]}],
      },
      ...(rticks ? [rticks] : []),
      {
        type: 'effectScatter', coordinateSystem: 'geo', zlevel: 3,
        symbolSize: 13, itemStyle: {color: '#3a6ea5'},
        data: [{value: [obs.lon, obs.lat],
                name: `${curCity().name} ${Math.round(+r.probability)}%`}],
        label: {show: true, formatter: '{b}', position: 'bottom',
                fontSize: 11, color: '#fff', fontWeight: 700,
                backgroundColor: 'rgba(58,110,165,.92)',
                padding: [3, 7], borderRadius: 4},
      },
    ],
  });
  calibrateGrid(revChart, pts, 0.74);
  drawMapChrome(revChart, el);
}

function renderPmMap(r) {
  registerMap();
  if (geoFallback('pmMap')) return;
  const el = document.getElementById('pmMap');
  pmChart = pmChart || echarts.init(el);
  pmChart.clear();
  const data = r.pm25.map(c => ({
    name: c.name, value: [c.lon, c.lat, c.pm25 ?? 0],
  }));
  pmChart.setOption({
    ...baseMapOption(),
    tooltip: chartTip({formatter: p => `${p.name}<br>PM2.5 ${p.value[2]} μg/m³`}),
    visualMap: {
      min: 0, max: 75, seriesIndex: 0, show: true,
      inRange: {color: ['#4caf50', '#ffeb3b', '#ff9800', '#f44336']},
      text: ['75+', '0'], left: 16, bottom: 16,
      textStyle: {color: '#888', fontSize: 11},
    },
    series: [{
      type: 'scatter', coordinateSystem: 'geo', symbolSize: 18,
      data,
      label: {show: true, formatter: p => p.name, position: 'right',
              fontSize: 10, color: '#666'},
    }],
  });
}

function renderCrossSection(r) {
  const el = document.getElementById('crossSection');
  csChart = csChart || echarts.init(el);
  csChart.clear();
  const cs = r.cross_section;
  const heights = cs.levels.map(l => l.height_km);

  // 时间演变帧（后端 timeline：T-6h ~ T+1h）
  const frames = (cs.timeline && cs.timeline.length > 1) ? cs.timeline : null;
  window._csFrames = frames;
  let rh = cs.rh;
  // 首帧要带 offset_min：地影线按帧时刻的太阳高度角重算，缺了就画不出来
  let initFrame = {rh: cs.rh, offset_min: 0};
  if (frames) {
    const ctrl = document.getElementById('csCtrl');
    const slider = document.getElementById('csSlider');
    const play = document.getElementById('csPlay');
    const label = document.getElementById('csLabel');
    ctrl.style.display = 'flex';
    slider.max = String(frames.length - 1);
    // 默认帧 = 事件时刻（offset_min === 0），缺失时取最接近 0 的帧
    let defI = frames.findIndex(f => f.offset_min === 0);
    if (defI < 0) {
      defI = frames.reduce((bi, f, i) =>
        Math.abs(f.offset_min) < Math.abs(frames[bi].offset_min) ? i : bi, 0);
    }
    slider.value = String(defI);
    label.textContent = csFrameLabel(frames[defI]);
    if (window._csTimer) { clearInterval(window._csTimer); window._csTimer = null; }
    setPlayState(play, false);
    slider.oninput = () => {
      if (window._csTimer) { clearInterval(window._csTimer); window._csTimer = null; setPlayState(play, false); }
      const f = frames[+slider.value];
      label.textContent = csFrameLabel(f);
      updateCsFrame(f);
    };
    play.onclick = () => {
      if (window._csTimer) {
        clearInterval(window._csTimer); window._csTimer = null;
        setPlayState(play, false);
        return;
      }
      setPlayState(play, true);
      let i = +slider.value >= frames.length - 1 ? 0 : +slider.value;
      window._csTimer = setInterval(() => {
        i = (i + 1) % frames.length;
        slider.value = String(i);
        label.textContent = csFrameLabel(frames[i]);
        updateCsFrame(frames[i]);
      }, 900);
    };
    rh = frames[defI].rh;
    initFrame = frames[defI];
  } else {
    const ctrl = document.getElementById('csCtrl');
    if (ctrl) ctrl.style.display = 'none';
  }

  setCsBase(cs, heights,
            (r.main || {}).sound ? r.main.sound.freezing_km : null);
  updateCsFrame(initFrame);

  const pa = document.getElementById('pathAnalysis');
  if (pa) {
    pa.innerHTML = (r.path_analysis || []).map(x => `<li>${x}</li>`).join('');
  }
}

function csFrameLabel(f) {
  if (!f) return '事件时刻';
  const m = f.offset_min;
  if (m === 0) return '事件时刻';
  return `T${m > 0 ? '+' : ''}${Math.round(m / 60 * 10) / 10}h`;
}

function setCsBase(cs, heights, freezingKm) {
  csChart.setOption({
    tooltip: chartTip({formatter: p => `RH ${p.value[2]}%<br>高度 ${p.value[1]} km`}),
    grid: {left: 50, right: 80, top: 24, bottom: 70},
    xAxis: {type: 'category', data: cs.x_labels,
            axisLabel: {fontSize: 10, interval: 0, rotate: 30,
                        formatter: v => v.replace('\n', ' ')}},
    yAxis: {type: 'value', name: '高度 km', max: 10, min: 0},
    visualMap: {
      min: 15, max: 95, seriesIndex: 0, show: true,
      inRange: {color: ['#f5efdf', '#bcd2e8', '#5b8ec4', '#1c4f8b']},
      text: ['湿', '干'], right: 8, top: 'middle',
      textStyle: {color: getComputedStyle(document.documentElement)
        .getPropertyValue('--chart-axis').trim() || '#888', fontSize: 11},
    },
    series: [
      {type: 'heatmap', data: [], progressive: false},
      // 地影线（v2.26）：线下方的云已进入地球阴影，不再受光
      {name: '地影线', type: 'line', data: [], z: 5, silent: true,
       symbol: 'none', lineStyle: {color: '#7a6a5a', width: 1.4,
                                   type: 'dashed'},
       areaStyle: {color: 'rgba(40,34,30,.30)'}, tooltip: {show: false}},
      // 0°C 层（v2.33）：该高度以上云顶温度在冰点以下（冰晶主导）。
      // 事件时刻的静态值，不随时间滑块变化
      {name: '0°C 层', type: 'line', z: 4, silent: true, symbol: 'none',
       data: freezingKm != null
         ? cs.x_labels.map((_, xi) => [xi, freezingKm]) : [],
       lineStyle: {color: '#5bc4b0', width: 1.2, type: 'dashed'},
       tooltip: {show: false}},
    ],
  });
}

// 地影线高度：太阳高度角 sunAlt（度，负=地平线下）在弧距 d 处，
// 恰好受光的云顶高度 h = R(1/cos b − 1)，b = |α| − d/R（b<=0 时全柱受光）。
function csShadowHeights(cs, sunAlt) {
  const RE = 6371.0088;
  return (cs.x_dist || []).map(d => {
    const b = (-sunAlt) * Math.PI / 180 - (d / RE);
    if (b <= 0) return 0;
    if (b >= Math.PI / 2 - 1e-6) return 10;
    return Math.min(10, Math.max(0, RE * (1 / Math.cos(b) - 1)));
  });
}

function csSunAltAt(frame) {
  const tr = curReport && curReport.tracks;
  const base = (curReport && curReport.astronomy
                && curReport.astronomy.key_time) || '18:00';
  if (!tr || !frame || frame.offset_min == null
      || typeof skyTrackValue !== 'function') return null;
  const hhmm = String(base).split(':');
  const m = (+hhmm[0] || 0) * 60 + (+hhmm[1] || 0) + frame.offset_min;
  return skyTrackValue(tr, 'sun_alt', m);
}

function updateCsFrame(frame) {
  const cs = curReport.cross_section;
  const heights = cs.levels.map(l => l.height_km);
  const rh = frame.rh || cs.rh;
  const data = [];
  for (let li = 0; li < heights.length; li++) {
    for (let xi = 0; xi < cs.x_labels.length; xi++) {
      data.push([xi, heights[li], rh[li][xi]]);
    }
  }
  const fineData = bilinear(data, cs.x_labels.length, heights);
  const sunAlt = csSunAltAt(frame);
  const shadow = sunAlt == null ? []
    : csShadowHeights(cs, sunAlt).map((h, xi) => [xi, Math.round(h * 100) / 100]);
  csChart.setOption({series: [{data: fineData}, {data: shadow}]});
  renderCsIllumination(sunAlt);
}

// 逐层"变暗时刻"提示：地影线之外再给一条可读的时间轴
function renderCsIllumination(sunAlt) {
  const el = document.getElementById('csIllumNote');
  if (!el) return;
  const il = curReport && curReport.illumination;
  if (!il || !il.bands) { el.style.display = 'none'; return; }
  const BAND = {low: '低云', mid: '中云', high: '高云'};
  const parts = ['low', 'mid', 'high'].map(b => {
    const v = il.bands[b] || {};
    const tr = v.transition_min;
    if (tr == null) return `${BAND[b]}（${v.height_km || '-'}km）全程受光`;
    const side = tr >= 0 ? '事件后' : '事件前';
    return `${BAND[b]}（${v.height_km || '-'}km）${side} ${Math.abs(tr)} 分钟`
      + `变暗${v.transition_time ? `（${v.transition_time}）` : ''}`;
  });
  const now = sunAlt == null ? ''
    : `当前帧太阳高度 ${sunAlt.toFixed(1)}° · `;
  el.style.display = 'block';
  el.innerHTML = `☀ 逐层受光：${parts.join(' · ')}。<span style="opacity:.75">`
    + `${now}受光画布 ${il.canvas_lit}%（原始云量口径 ${il.canvas_raw}%）`
    + ' —— 只有仍被照亮的云才算霞光画布。</span>';
}

function bilinear(data, nx, ys) {
  // 构建网格
  const grid = {};
  data.forEach(([x, y, v]) => grid[x + '|' + y] = v);
  const out = [];
  const xs = [...Array(nx).keys()];
  const yFine = [];
  for (let i = 0; i < ys.length - 1; i++) {
    for (let f = 0; f < 4; f++) yFine.push(ys[i] + (ys[i + 1] - ys[i]) * f / 4);
  }
  yFine.push(ys[ys.length - 1]);
  const vAt = (x, y) => {
    // 找到包围 y 的两层
    let i = 0;
    while (i < ys.length - 1 && ys[i + 1] < y) i++;
    const y0 = ys[i], y1 = ys[Math.min(i + 1, ys.length - 1)];
    const v0 = grid[x + '|' + y0], v1 = grid[x + '|' + y1];
    if (v0 == null) return v1;
    if (v1 == null || y1 === y0) return v0;
    return v0 + (v1 - v0) * (y - y0) / (y1 - y0);
  };
  for (let yi = 0; yi < yFine.length; yi++) {
    for (const x of xs) {
      out.push([x, Math.round(yFine[yi] * 100) / 100,
                Math.round(vAt(x, yFine[yi]))]);
    }
  }
  return out;
}

// ---------------- 悬日月 ----------------
// 焦距 ↔ 视场角（全画幅等效 36mm 画幅宽）。和 sky_preview.js 的换算保持一致。
const PV_FOCAL_PRESETS = [24, 50, 85, 135, 200, 300, 400, 600, 840, 1200];
function focalToFov(f) { return 2 * Math.atan(18 / Math.max(1, f)) * 180 / Math.PI; }
function fovToFocal(a) {
  return Math.round(18 / Math.tan(Math.max(0.05, a) * Math.PI / 360));
}

// 焦距控件与「视场角」双向同步：改焦距 = 改视场角（并按新视场重算窗口）
function syncFocalUI() {
  const fi = document.getElementById('focalInput');
  if (fi && document.activeElement !== fi) {
    fi.value = String(fovToFocal(+alignState.aov || 3));
  }
  const box = document.getElementById('focalPresets');
  if (box && !box.__bound) {
    box.__bound = true;
    box.innerHTML = PV_FOCAL_PRESETS
      .map(f => `<button data-f="${f}">${f}</button>`).join('');
    box.onclick = e => {
      const b = e.target.closest('button');
      if (!b) return;
      applyFocal(+b.dataset.f);
    };
  }
  if (box) {
    const cur = fovToFocal(+alignState.aov || 3);
    box.querySelectorAll('button').forEach(b =>
      b.classList.toggle('on', +b.dataset.f === cur));
  }
}

function applyFocal(f) {
  if (!(f > 0)) return;
  alignState.aov = Math.max(0.4, Math.min(60, focalToFov(f)));
  const aov = document.getElementById('aovInput');
  if (aov) aov.value = (+alignState.aov).toFixed(2);
  syncFocalUI();
  saveModeState();
  // 先按新视场立刻重画（换镜头立刻看到构图），再让后端按新容差重算窗口
  if (typeof pvSetFov === 'function') pvSetFov(alignState.aov);
  if (appMode === 'align') onParamsChanged();   // v2.36：换镜头只标待查询
}

function bindFocalInput() {
  const fi = document.getElementById('focalInput');
  if (!fi || fi.__pvBound) return;
  fi.__pvBound = true;
  fi.addEventListener('change', () => applyFocal(+fi.value));
  syncFocalUI();
}

function setPvWindow(w, r) {
  if (!w || typeof renderCelestialPreview !== 'function') return;
  bindFocalInput();
  renderCelestialPreview(w, r);
  const list = alignWindowList();
  const sel = document.getElementById('pvWindowSel');
  if (sel) {
    sel.__list = list;
    sel.innerHTML = list.map((x, i) =>
      `<option value="${i}">${(x.date || '').slice(5)} ${x.best_time} · 高度 ${x.best_alt}° · 差 ±${x.best_daz}°</option>`
    ).join('');
    sel.onchange = () => {
      const nw = (sel.__list || [])[+sel.value];
      if (nw) setPvWindow(nw, r);
    };
    const li = list.indexOf(w);
    if (li >= 0) sel.value = String(li);
    sel.__cur = (li >= 0) ? list[li] : w;      // 分享图要跟这里显示的窗口一致
  }
  const idx = list.indexOf(w);
  const tbl = document.getElementById('alignTbl');
  if (tbl) {
    tbl.querySelectorAll('tr').forEach((tr, i) => {
      tr.classList.toggle('row-active', i === idx + 1);   // 第一行是表头
    });
  }
}

async function submitFeedback() {
  const active = document.querySelector('#fbSeg button.active');
  const btn = document.getElementById('btnFeedback');
  if (!active) {
    // 未选实况：给一句提示就好。原先直接读 .dataset，裸点即未捕获 TypeError。
    const t = document.getElementById('fbText');
    if (t) t.textContent = '先选一条实况（没看到 / 看到了 / 更差…），再提交。';
    return;
  }
  const outcome = active.dataset.v;
  if (btn) { btn.disabled = true; btn.textContent = '提交中…'; }
  try {
    const res = await fetch(API + '/api/feedback', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({event_key: curReport.log_key, outcome}),
    });
    if (!res.ok) throw new Error(await res.text());
    document.getElementById('fbDone').style.display = 'inline';
    document.getElementById('btnFeedback').style.display = 'none';
    document.getElementById('fbSeg').style.display = 'none';
    document.getElementById('fbText').textContent =
      '已记录，谢谢！校准数据 +1。';
  } catch (e) {
    document.getElementById('fbText').textContent = '提交失败：' + e.message;
    if (btn) { btn.disabled = false; btn.textContent = '提交实况'; }
  }
}

// ---------------- Planit .pft 机位天气评估 ----------------
async function loadPft() {
  const fileInput = document.getElementById('pftFile');
  if (!fileInput.files || !fileInput.files[0]) return;
  const tbl = document.getElementById('pftTbl');
  const btn = document.getElementById('btnPft');
  tbl.style.display = 'table';
  tbl.innerHTML = '<tr><td>解析中…</td></tr>';
  if (btn) { btn.disabled = true; btn.textContent = '评估中…'; }
  try {
    const text = await fileInput.files[0].text();
    const pft = JSON.parse(text);
    // 巧摄几何 -> 机位/目标方位/视场/仰角，并立即查询天气
    // .pft 给的是方位而非目标坐标，故清除目标点避免与推导方位冲突
    try {
      const gm = pft.map || {}, tl = pft.tools || {};
      if (gm.lat != null && tl.centerBearing != null) {
        alignState.cam = {lat: +gm.lat, lon: +gm.lng, name: '巧摄 .pft'};
        alignState.az = Math.round(+tl.centerBearing * 10) / 10;
        alignState.aov = +tl.horizontalAngleOfView || alignState.aov;
        alignState.elev = +tl.centerElevation || 0;
        alignState.target = null;
        syncAlignControls();
        setTimeout(() => { renderAlignMap(true); }, 30);
        if (appMode === 'align') loadAlign(true);
      }
    } catch (e) { /* 几何缺失时仅做 90 天扫描 */ }
    const res = await fetch(API + '/api/pft', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({pft, days: 90, weather_days: 10}),
    });
    if (!res.ok) throw new Error(await res.text());
    const r = await res.json();
    const g = r.geometry;
    const all = r.events || [];
    // 悬日月模式下按当前天体过滤（悬日只看太阳、悬月只看月亮）
    const filtered = appMode === 'align'
      ? all.filter(e => e.body === alignState.body) : all;
    const bodyTxt = alignState.body === 'moon' ? '月亮' : '太阳';
    const modeTxt = alignState.body === 'moon' ? '悬月' : '悬日';
    const filterNote = appMode === 'align'
      ? `<tr><td colspan="7" style="color:var(--ink-2);font-size:12px">已按「${modeTxt}」模式过滤：仅显示${bodyTxt}事件（${filtered.length} 条 / 原始 ${all.length} 条）</td></tr>`
      : '';
    if (!filtered.length) {
      tbl.innerHTML = filterNote
        + `<tr><td colspan="7" style="color:#999">未来 90 天无${bodyTxt}穿过该取景框</td></tr>`;
      return;
    }
    tbl.innerHTML = filterNote +
      `<tr><th>天体</th><th>时刻 (BJT)</th><th>方位</th><th>仰角</th><th>月相照度</th><th>天气判定</th><th>总云量</th></tr>` +
      filtered.map(e => {
        const w = e.weather || {};
        return `<tr>
          <td><b>${e.body === 'moon' ? '月亮' : '太阳'}</b></td>
          <td>${e.time_bjt}</td>
          <td>${e.az}°</td>
          <td>${e.alt}°</td>
          <td>${e.illum != null ? e.illum + '%' : '-'}</td>
          <td class="prob" style="color:${e.verdict === '通透可拍' ? '#4a8a4a' : e.verdict === '薄云可拍' ? '#e8973a' : '#c06040'}">${e.verdict || '-'}</td>
          <td>${w.total != null ? w.total + '%' : '-'}</td>
        </tr>`;
      }).join('');
  } catch (e) {
    tbl.innerHTML = `<tr><td style="color:#c06040">解析失败：${e.message}</td></tr>`;
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = '评估机位'; }
  }
}

// ---------------- 悬日月地平线可见性热力图 ----------------
async function loadHorizon() {
  const c = curCity();
  const d = document.getElementById('dateSel').value;
  const [body, kind] = document.querySelector('#hEventSeg button.active')
    .dataset.v.split('_');   // moon_rise -> body=moon, kind=rise
  const meta = document.getElementById('horizonMeta');
  meta.textContent = '正在评估各地地平线可见性…';
  try {
    const res = await fetch(
      `${API}/api/horizon?lat=${c.lat}&lon=${c.lon}&date=${d}&body=${body}&kind=${kind}`);
    if (!res.ok) throw new Error(await res.text());
    const r = await res.json();
    renderHorizon(r, c);
  } catch (e) {
    meta.textContent = '获取失败：' + e.message;
  }
}

function renderHorizon(r, c) {
  // 卡片必须显式打开：这行原先缺失，导致整块「悬日月地平线可见性地图」
  // 永远看不见（接口在跑、图渲染进隐藏容器）——功能等于不存在。
  const card = document.getElementById('horizonCard');
  if (card) card.style.display = 'block';
  const meta = document.getElementById('horizonMeta');
  if (geoFallback('horizonMap')) {
    if (meta) meta.textContent = '底图未就绪，稍后重新查询即可';
    return;
  }
  registerMap();
  const el = document.getElementById('horizonMap');
  hzChart = hzChart || echarts.init(el);
  hzChart.clear();
  if (r.error) {
    meta.textContent = r.error;
    return;
  }
  const moonExtra = (r.illum != null)
    ? ` · ${r.phase} 照度 ${r.illum}%` : '';
  const crossDay = r.cross_day ? '（次日凌晨）' : '';
  meta.innerHTML =
    `${r.meta.date} <b>${r.label}</b> · ${c.name}${r.event_time} · 评估时次 ${r.eval_time || r.event_time}${crossDay}` +
    ` · 方位 <b>${r.az}° ${azimuthName(r.az)}</b>${moonExtra}` +
    (r.best ? ` · 最优区: (${r.best.lat}°N,${r.best.lon}°E) ${r.best.prob}% ${r.best.quality}` : '');
  const pts = r.points.map(p => ({
    value: [p.lon, p.lat, p.prob],
    prob: p.prob, quality: p.quality, aod: p.aod,
    low: p.low, mid: p.mid, high: p.high,
  }));
  const azRad = r.az * Math.PI / 180;
  const hzInk = chartInk();
  hzChart.setOption({
    ...baseMapOption(),
    geo: {
      map: 'guangdong', roam: true, zoom: 1.55,
      center: [113.15, 22.75],
      label: {show: true, color: hzInk.axis, fontSize: 10},
      itemStyle: {areaColor: hzInk.area, borderColor: hzInk.border},
      emphasis: {label: {color: hzInk.ink}, itemStyle: {areaColor: hzInk.line}},
    },
    tooltip: chartTip({
      formatter: p => p.data && p.data.prob !== undefined
        ? `可见概率 ${p.data.prob}%（${p.data.quality}）<br>低/中/高云 ${p.data.low}/${p.data.mid}/${p.data.high}%<br>AOD ${p.data.aod ?? '-'}`
        : (p.name || ''),
    }),
    visualMap: {
      min: 0, max: 90, show: true, seriesIndex: 0,
      text: ['通透', '遮挡'],
      inRange: {color: ['#3d5a80', '#8ecae6', '#f2cc5e', '#e8602a', '#b3260e']},
      left: 16, bottom: 16, textStyle: {color: '#888', fontSize: 11},
    },
    series: [
      {
        name: '地平线可见性', type: 'scatter', coordinateSystem: 'geo',
        symbolSize: (val, params) => 7 + (params.data.prob || 0) * 0.2,
        itemStyle: {opacity: 0.75, borderColor: 'rgba(255,255,255,0.35)',
                    borderWidth: 0.5},
        data: pts,
      },
      {
        type: 'lines', coordinateSystem: 'geo', zlevel: 2,
        lineStyle: {color: '#e8602a', width: 2.5, type: 'dashed'},
        data: [{coords: [[c.lon, c.lat],
                         [c.lon + Math.sin(azRad) * 4.2,
                          c.lat + Math.cos(azRad) * 4.2]]}],
      },
      {
        type: 'effectScatter', coordinateSystem: 'geo', zlevel: 3,
        symbolSize: 12, itemStyle: {color: '#e8602a'},
        data: [{value: [c.lon, c.lat]}],
        label: {show: true, formatter: c.name, position: 'bottom',
                fontSize: 11, color: '#e8602a', fontWeight: 700},
      },
    ],
  });
}

init().catch(e => console.error('init failed', e));

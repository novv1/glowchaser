/* 逐光者 GlowChaser · cinema（M1：slate/转场/三态行为；M2 起填场景引擎）
   作用域：DOM 操作只碰 cinema 新增节点 + 现有 pane/tab 类名；数据流不动。 */
(function () {
  'use strict';

  var BURN_MS = 450;    // film burn 总时长（v2.0 M1 验收）
  var CUT_MS = 140;     // 烧场 140ms 后硬切 pane（对齐 h2 基准）
  var LOCK_MS = 300;    // 转场期间禁用 tab 点击防连点（v1.0 §5.3）
  var _burnLock = 0;

  function reduceMotion() {
    return !!(window.matchMedia
      && window.matchMedia('(prefers-reduced-motion: reduce)').matches);
  }

  function isCinema() {
    // 单一主题：片场动效常开，不再按皮肤开关
    return true;
  }

  function fireBurn() {
    var burn = document.getElementById('burn');
    if (!burn || !isCinema() || reduceMotion()) return;
    burn.classList.remove('go');
    void burn.offsetWidth;                 // 强制重排以重启动画
    burn.classList.add('go');
  }

  /* tab 切换：film burn 转场。doCut 由调用方（app.js showTab 包裹层）
     传入——原生类切换逻辑仍在 app.js，cinema 只管烧场与防连点节奏。 */
  function switchPane(from, to, doCut) {
    if (from === to) { if (doCut) doCut(); return to; }
    var now = Date.now();
    if (now < _burnLock) return from;      // 防连点：300ms 内忽略
    _burnLock = now + LOCK_MS;
    if (typeof doCut !== 'function') return to;
    if (!isCinema() || reduceMotion()) {   // 降级：即时切换
      try { doCut(); } catch (e) { /* 回落不断功能 */ }
      return to;
    }
    try {
      fireBurn();
      setTimeout(function () {
        try { doCut(); } catch (e) { /* 回落不断功能 */ }
      }, CUT_MS);
    } catch (e) {
      try { doCut(); } catch (err) { /* 回落不断功能 */ }
    }
    return to;
  }

  /* ── 场记板：SCENE=年积日 / ROLL=城市代码大写 / TAKE=session 彩蛋 ── */
  var _take = 1;

  function dayOfYear(d) {
    var start = new Date(d.getFullYear(), 0, 0);
    return Math.floor((d - start) / 86400000);
  }

  function sceneNum(dateStr) {
    var d = dateStr ? new Date(dateStr + 'T12:00:00') : new Date();
    if (isNaN(d)) d = new Date();
    return dayOfYear(d);
  }

  function rollCode(cityId) {
    if (!cityId) return '--';
    // 城市 id（zhuhai/shenzhen…）→ ROLL 代码：取字母大写去元音后 2-4 位
    var s = String(cityId).toUpperCase().replace(/[^A-Z]/g, '');
    if (!s) return '--';
    var code = s.replace(/[AEIOU]/g, '') || s;
    return code.slice(0, 4) + '-' + sceneNum();
  }

  function paintSlate() {
    var dateEl = document.getElementById('dateSel');
    var sc = document.getElementById('sceneNum');
    if (sc) sc.textContent = String(sceneNum(dateEl && dateEl.value));
    var cityEl = document.getElementById('citySel');
    var roll = document.getElementById('rollCode');
    if (roll) {
      var opt = cityEl && cityEl.options ? cityEl.options[cityEl.selectedIndex] : null;
      var cid = opt && opt.value !== undefined ? cityEl.value : '';
      // citySel 的 value 是城市索引：换算回城市 id 再编码
      var cities = window.__gcCities || [];
      var c = cities[+cid];
      roll.textContent = rollCode(c ? (c.id || c.name) : cid);
    }
    var take = document.getElementById('takeNum');
    if (take) take.textContent = String(_take).padStart(2, '0');
  }

  function bumpTake() {
    _take += 1;
    var el = document.getElementById('takeNum');
    if (!el) return _take;
    if (!isCinema() || reduceMotion()) {
      el.textContent = String(_take).padStart(2, '0');
      return _take;
    }
    el.classList.remove('flip');
    void el.offsetWidth;
    el.classList.add('flip');
    setTimeout(function () {
      el.textContent = String(_take).padStart(2, '0');
    }, 220);
    var clap = document.getElementById('clapTake');
    if (clap) clap.textContent = String(_take).padStart(2, '0');
    return _take;
  }

  /* 悬日月模式：导航整体切「特别放映 SPECIAL」单态高亮 */
  function paintSpecial(isAlign) {
    var nav = document.getElementById('tabnav');
    if (!nav) return;
    nav.classList.toggle('cn-special', !!isAlign);
  }

  /* ── 三态行为（M1 用户拍板：连行为一起做；查询链仍走 app.js 原函数） ── */
  function bindClap() {
    var go = document.getElementById('btnGo');
    if (go && !go.__cnBound) {
      go.__cnBound = true;
      go.addEventListener('click', function () {
        bumpTake(); paintSlate();
      });
    }
    var align = document.getElementById('btnAlign');
    if (align && !align.__cnBound) {
      align.__cnBound = true;
      align.addEventListener('click', function () {
        bumpTake(); paintSlate();
      });
    }
    var city = document.getElementById('citySel');
    if (city && !city.__cnBound) {
      city.__cnBound = true;
      city.addEventListener('change', paintSlate);
    }
    var date = document.getElementById('dateSel');
    if (date && !date.__cnBound) {
      date.__cnBound = true;
      date.addEventListener('change', paintSlate);
    }
  }

  // 字幕打字机：66ms/字 + 光标（v2.0 §5 ⑤；reduced-motion 直出）
  function cinemaTyping(el, text) {
    if (!el) return null;
    if (text === undefined) return el;
    try {
      if (!isCinema() || reduceMotion()) { el.textContent = text; return el; }
      var chars = String(text).split('');
      el.textContent = '';
      el.classList.add('cn-typing');
      var i = 0;
      (function tick() {
        i += 1;
        el.textContent = String(text).slice(0, i);
        if (i < chars.length) setTimeout(tick, 66);
        else setTimeout(function () { el.classList.remove('cn-typing'); }, 600);
      })();
    } catch (e) { el.textContent = text; }
    return el;
  }

  // 片名 countup：2s quart ease（v2.0 §5 ⑥；reduced-motion 直出；仅展示位）
  function cinemaCountup(el, to, suffix) {
    if (!el) return;
    try {
      var target = Number(to);
      suffix = suffix || '';
      if (!isCinema() || reduceMotion() || !isFinite(target)) {
        el.textContent = to + suffix; return;
      }
      var from = Number(el.dataset.cnCv || 0);
      el.dataset.cnCv = target;
      if (from === target) { el.textContent = target + suffix; return; }
      var t0 = performance.now(), dur = 2000;
      (function step(t) {
        var k = Math.min(1, (t - t0) / dur);
        var e = 1 - Math.pow(1 - k, 4);   // quart out
        el.textContent = Math.round(from + (target - from) * e) + suffix;
        if (k < 1) requestAnimationFrame(function () { requestAnimationFrame(step); });
        else el.textContent = target + suffix;
      })(t0);
    } catch (e) { el.textContent = to + suffix; }
  }

  // reveal 注册：IntersectionObserver threshold .15 once（v1.0 §5.4；cinema 外/降级直显）
  var _rvObs = null;
  function cinemaScan(container) {
    bindClap();
    paintSlate();
    try {
      var root = container || document;
      if (!isCinema() || reduceMotion() || !('IntersectionObserver' in window)) {
        root.querySelectorAll('.cn-reveal').forEach(function (el) {
          el.classList.add('cn-in');
        });
        return root;
      }
      if (!_rvObs) {
        _rvObs = new IntersectionObserver(function (entries) {
          entries.forEach(function (en) {
            if (en.isIntersecting) {
              en.target.classList.add('cn-in');
              _rvObs.unobserve(en.target);
              // 入场联动：金条生长（CSS 驱动）+ countup（data-cn-count）
              var c = en.target.querySelector && en.target.querySelector('[data-cn-count]');
              if (c) cinemaCountup(c, c.getAttribute('data-cn-count'), c.getAttribute('data-cn-suffix') || '');
            }
          });
        }, { threshold: 0.15 });
      }
      root.querySelectorAll('.cn-reveal:not([data-cn-rv])').forEach(function (el) {
        el.setAttribute('data-cn-rv', '1');
        _rvObs.observe(el);
      });
    } catch (e) { /* 降级：内容本就在 DOM，仅少入场动画 */ }
    return container || document;
  }

  /* 七阶段查询序列（v2.0 §5，无闪白版；时序常量供单测审计）：
     ① ACTION 0-120ms（调用方已 bumpTake）② 打板 0.9s ③ 光束 1.2s
     ④ 帧渲染 regen 后 opacity 0→1 1.1s ⑤ 字幕打字机 66ms/字 +400ms
     ⑥ 片名 countup 2s +600ms ⑦ 组件 reveal stagger +800ms */
  var SEQ = { clap: 900, beam: 1200, frame: 1100, subDelay: 400,
              typePer: 66, titleDelay: 600, countDur: 2000, compDelay: 800 };
  var _seqTimer = [];

  function seqClear() {
    _seqTimer.forEach(clearTimeout);
    _seqTimer = [];
  }

  function cinemaSeq(phase) {
    try {
      if (!isCinema() || reduceMotion()) return phase || 'idle';
      if (phase === 'start') {
        seqClear();
        var beam = document.getElementById('beam');
        if (beam) {
          beam.classList.remove('go');
          void beam.offsetWidth;
          // 打板 0.9s 后光束点亮 1.2s
          _seqTimer.push(setTimeout(function () {
            beam.classList.add('go');
          }, SEQ.clap));
        }
        var fr = document.getElementById('heroFrame');
        if (fr) fr.classList.add('cn-preframe');
      } else if (phase === 'rendered') {
        var fr2 = document.getElementById('heroFrame');
        if (fr2) {
          fr2.classList.remove('cn-preframe');
          fr2.classList.remove('cn-lit');
          void fr2.offsetWidth;
          fr2.classList.add('cn-lit');   // 帧 opacity 0→1 1.1s
        }
        // ⑤⑥⑦ 按表 stagger
        _seqTimer.push(setTimeout(function () {
          var sub = document.getElementById('frameSub');
          if (sub && sub.textContent) cinemaTyping(sub, sub.textContent);
        }, SEQ.subDelay));
        _seqTimer.push(setTimeout(function () {
          var p = document.getElementById('heroProb');
          if (p && p.getAttribute('data-cn-prob') != null) {
            cinemaCountup(p, p.getAttribute('data-cn-prob'), '%');
          }
        }, SEQ.titleDelay));
        _seqTimer.push(setTimeout(function () {
          cinemaScan(document.getElementById('report-flow'));
        }, SEQ.compDelay));
      }
    } catch (e) { /* 序列失败不挡报告主体 */ }
    return phase || 'idle';
  }

  window.switchPane = switchPane;
  window.__cinemaScan = cinemaScan;
  window.__cinemaTyping = cinemaTyping;
  window.__cinemaSeq = cinemaSeq;
  window.__cinemaCount = cinemaCountup;
  window.__cinemaSeqT = SEQ;
  window.__cinemaTake = bumpTake;
  window.__cinemaSlate = paintSlate;
  window.__cinemaSpecial = paintSpecial;
  window.__cinemaBurnMs = BURN_MS;

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', function () {
      bindClap(); paintSlate();
    });
  } else {
    bindClap(); paintSlate();
  }
})();

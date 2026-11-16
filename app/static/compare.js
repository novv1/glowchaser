/* 城市对比：多城并排概率/最佳时段/归因，回答"今晚去哪拍"。
   顺序 fetch（避免并发限流），选择记忆于 localStorage。 */

document.addEventListener('DOMContentLoaded', () => {
  const wrap = document.getElementById('compareCities');
  const btn = document.getElementById('btnCompare');
  const out = document.getElementById('compareResult');
  if (!wrap || !btn) return;

  const KEY = 'gc_compare';
  let selected = JSON.parse(localStorage.getItem(KEY) || '[]');

  // 用稳定 id 记录选择（旧版本存的是数组下标，加载时自动迁移一次）
  const cityKey = c => c.id || `${c.lat},${c.lon}`;

  function migrate(cities) {
    if (!cities.length) return;
    const ids = cities.map(cityKey);
    const mapped = selected
      .map(x => (typeof x === 'number' ? ids[x] : x))
      .filter(x => typeof x === 'string' && ids.includes(x));
    if (JSON.stringify(mapped) !== JSON.stringify(selected)) {
      selected = mapped;
      localStorage.setItem(KEY, JSON.stringify(selected));
    }
  }

  function renderChips() {
    // 城市列表由 app.js 的 init 先行加载；此处等 cities 就绪
    const cities = window.__gcCities || [];
    migrate(cities);
    const esc = (typeof window.esc === 'function') ? window.esc : (t => String(t));
    wrap.innerHTML = cities.map(c =>
      `<span class="compare-chip${selected.includes(cityKey(c)) ? ' on' : ''}"`
      + ` data-key="${cityKey(c)}" role="button" tabindex="0"`
      + ` aria-pressed="${selected.includes(cityKey(c)) ? 'true' : 'false'}">`
      + `${esc(c.name)}</span>`
    ).join('');
    wrap.querySelectorAll('.compare-chip').forEach(chip => {
      chip.onclick = () => {
        const k = chip.dataset.key;
        if (selected.includes(k)) {
          selected = selected.filter(x => x !== k);
        } else {
          if (selected.length >= 3) return;   // 最多 3 城
          selected.push(k);
        }
        localStorage.setItem(KEY, JSON.stringify(selected));
        renderChips();
        const again = wrap.querySelector(`.compare-chip[data-key="${k}"]`);
        if (again) again.focus();     // 重绘后把焦点还给同一个芯片
      };
      chip.onkeydown = ev => {
        if (ev.key === 'Enter' || ev.key === ' ') { ev.preventDefault(); chip.click(); }
      };
    });
  }
  window.__compareRenderChips = renderChips;
  renderChips();

  btn.onclick = async () => {
    const cities = window.__gcCities || [];
    const picked = selected
      .map(k => cities.find(c => cityKey(c) === k))
      .filter(Boolean);
    if (!picked.length) {
      out.innerHTML = '<p class="desc">先选择 1~3 个城市</p>';
      return;
    }
    const date = document.getElementById('dateSel').value;
    const type = (typeof curType === 'function') ? curType() : 'sunset';
    btn.disabled = true;
    btn.textContent = '查询中…（每城 1~2s）';
    out.innerHTML = '<p class="desc">正在逐城获取报告…</p>';
    try {
      const results = [];
      for (const c of picked) {
        try {
          const res = await fetch(
            `${API}/api/report?lat=${c.lat}&lon=${c.lon}&date=${date}&type=${type}`);
          if (!res.ok) throw new Error(await res.text());
          results.push({city: c, r: await res.json()});
        } catch (e) {
          results.push({city: c, err: e.message.slice(0, 80)});
        }
      }

      const ok = results.filter(x => x.r);
      const best = ok.length
        ? ok.reduce((a, b) => (+b.r.probability > +a.r.probability ? b : a))
        : null;
      out.innerHTML = ok.map(({city, r}) => {
        const p = +r.probability;
        const isBest = best && best.city.id === city.id && ok.length > 1;
        return `<div class="cmp-row">
          <div class="city">${city.name}${isBest ? '<div class="cmp-best">★ 推荐</div>' : ''}</div>
          <div class="bar"><div style="width:0%" data-w="${p}"></div></div>
          <div class="pct" style="color:${probColor(p)}">${p}%<div class="win">${r.verdict}</div></div>
          <div class="win">${r.best_window ? r.best_window.start + '–' + r.best_window.end : '—'}<br>${(r.reason || '').slice(0, 26)}${(r.reason || '').length > 26 ? '…' : ''}</div>
        </div>`;
      }).join('') + results.filter(x => x.err)
        .map(({city, err}) => `<p class="desc">${city.name}：获取失败（${err}）</p>`)
        .join('');
      // 条形动画
      requestAnimationFrame(() => {
        out.querySelectorAll('.bar > div').forEach(d => {
          d.style.width = Math.min(100, +d.dataset.w / 40 * 100) + '%';
        });
      });
    } finally {
      btn.disabled = false;
      btn.textContent = '对比选中城市';
    }
  };
});

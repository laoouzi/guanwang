const state = {
  view: 'chat',
  activeSymbol: null,
  quotes: new Map(),
  watchlist: [],
  watchEdit: false,
  portfolio: { holdings: [], watchlist: [] },
  diagnosis: null,
  analysis: null,
  range: '6m',
  messages: [],
  report: null,
  busy: false,
  reqSeq: 0,
};

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

/* ------------------------------ utils ------------------------------ */

function escapeHtml(value) {
  return String(value ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

const isNum = (v) => typeof v === 'number' && Number.isFinite(v);
const fmtPrice = (v) => {
  if (!isNum(v)) return '—';
  const digits = Math.abs(v) >= 100 ? 2 : 3;
  return v.toLocaleString('zh-CN', { minimumFractionDigits: digits, maximumFractionDigits: digits });
};
const fmtPct = (v, digits = 2) => (isNum(v) ? `${v >= 0 ? '+' : ''}${v.toFixed(digits)}%` : '—');
const fmtMoney = (v, unit = '') => {
  if (!isNum(v)) return '—';
  const abs = Math.abs(v);
  if (abs >= 1e12) return `${(v / 1e12).toFixed(2)}万亿${unit}`;
  if (abs >= 1e8) return `${(v / 1e8).toFixed(2)}亿${unit}`;
  if (abs >= 1e4) return `${(v / 1e4).toFixed(2)}万${unit}`;
  return `${v.toFixed(2)}${unit}`;
};
const currencyName = (c) => ({ CNY: '元', USD: '美元', HKD: '港元' })[c] || c || '';
const pctClass = (v) => (isNum(v) ? (v >= 0 ? 'up' : 'down') : 'muted');
const marketLabel = (m) => ({ cn: 'A 股', hk: '港股', us: '美股' })[m] || m || '';

function toast(message) {
  const el = $('#toast');
  el.textContent = message;
  el.classList.add('show');
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => el.classList.remove('show'), 2600);
}

async function api(path, options = {}) {
  const headers = { 'Content-Type': 'application/json', ...(options.headers || {}) };
  const token = localStorage.getItem('aegis_token');
  if (token) headers['x-access-token'] = token;
  const res = await fetch(path, { ...options, headers });
  if (res.status === 401) {
    showTokenModal();
    throw new Error('unauthorized');
  }
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
  return data;
}

function md(text) {
  const safe = escapeHtml(text || '');
  const inline = (s) =>
    s
      .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
      .replace(/`([^`]+)`/g, '<code>$1</code>')
      .replace(/\[(.+?)\]\((https?:\/\/[^\s)]+)\)/g, '<a href="$2" target="_blank" rel="noreferrer">$1</a>');
  const lines = safe.split('\n');
  let html = '';
  let list = null;
  const closeList = () => {
    if (list) {
      html += `</${list}>`;
      list = null;
    }
  };
  for (const raw of lines) {
    const line = raw.trimEnd();
    if (!line.trim()) {
      closeList();
      continue;
    }
    const heading = line.match(/^(#{2,6})\s+(.*)$/);
    if (heading) {
      closeList();
      const level = Math.min(6, Math.max(3, heading[1].length));
      html += `<h${level}>${inline(heading[2])}</h${level}>`;
      continue;
    }
    if (/^[-*]\s+/.test(line)) {
      if (list !== 'ul') {
        closeList();
        html += '<ul>';
        list = 'ul';
      }
      html += `<li>${inline(line.replace(/^[-*]\s+/, ''))}</li>`;
      continue;
    }
    if (/^\d+\.\s+/.test(line)) {
      if (list !== 'ol') {
        closeList();
        html += '<ol>';
        list = 'ol';
      }
      html += `<li>${inline(line.replace(/^\d+\.\s+/, ''))}</li>`;
      continue;
    }
    if (/^>\s?/.test(line)) {
      closeList();
      html += `<blockquote>${inline(line.replace(/^>\s?/, ''))}</blockquote>`;
      continue;
    }
    if (/^-{3,}$/.test(line)) {
      closeList();
      html += '<hr>';
      continue;
    }
    closeList();
    html += `<p>${inline(line)}</p>`;
  }
  closeList();
  return html;
}

/* ------------------------------ charts ------------------------------ */

function chartSize(el) {
  return { w: Math.max(el.clientWidth || 600, 280), h: Math.max(el.clientHeight || 280, 180) };
}

function svgEl(html) {
  const wrap = document.createElement('div');
  wrap.innerHTML = html.trim();
  return wrap.firstElementChild;
}

function attachHover(el, { padL, padT, plotW, plotH, count, tooltip }) {
  const tip = document.createElement('div');
  tip.className = 'chart-tip';
  tip.hidden = true;
  el.appendChild(tip);
  const svg = el.querySelector('svg');
  const line = svg.querySelector('.crosshair');
  svg.addEventListener('mousemove', (event) => {
    const rect = svg.getBoundingClientRect();
    const x = event.clientX - rect.left;
    const idx = Math.max(0, Math.min(count - 1, Math.round(((x - padL) / plotW) * (count - 1))));
    const px = padL + (idx / (count - 1 || 1)) * plotW;
    if (line) {
      line.setAttribute('x1', px);
      line.setAttribute('x2', px);
    }
    const html = tooltip(idx);
    if (!html) {
      tip.hidden = true;
      return;
    }
    tip.innerHTML = html;
    tip.hidden = false;
    const rectEl = el.getBoundingClientRect();
    const left = Math.max(70, Math.min(rectEl.width - 70, px));
    tip.style.left = `${left}px`;
    tip.style.top = `${event.clientY - rectEl.top - 10}px`;
  });
  svg.addEventListener('mouseleave', () => {
    tip.hidden = true;
    if (line) {
      line.setAttribute('x1', -10);
      line.setAttribute('x2', -10);
    }
  });
}

function renderCandles(el, data) {
  const bars = data?.bars || [];
  if (!bars.length) {
    el.innerHTML = '<p class="muted small">暂无 K 线数据</p>';
    return;
  }
  const { w, h } = chartSize(el);
  const padL = 6;
  const padR = 58;
  const padT = 12;
  const padB = 22;
  const totalH = h - padT - padB;
  const priceH = totalH * 0.77;
  const volH = totalH * 0.15;
  const gap = totalH - priceH - volH;
  const ma20 = data.ma20 || [];
  const ma60 = data.ma60 || [];
  const maVals = [...ma20, ...ma60].filter((v) => v != null);
  let min = Math.min(...bars.map((b) => b.low), ...maVals);
  let max = Math.max(...bars.map((b) => b.high), ...maVals);
  if (!Number.isFinite(min) || !Number.isFinite(max) || min === max) {
    min = (min || 1) * 0.97;
    max = (max || 1) * 1.03;
  }
  const span = max - min || 1;
  const plotW = w - padL - padR;
  const step = plotW / Math.max(1, bars.length - 1);
  const x = (i) => padL + i * step;
  const y = (v) => padT + ((max - v) / span) * priceH;
  const volMax = Math.max(...bars.map((b) => b.volume || 0), 1);
  const volTop = padT + priceH + gap;
  const volY = (v) => volTop + volH - (v / volMax) * volH;

  let grid = '';
  for (let i = 0; i <= 4; i++) {
    const val = max - (span * i) / 4;
    const gy = padT + (priceH * i) / 4;
    grid += `<line x1="${padL}" y1="${gy}" x2="${padL + plotW}" y2="${gy}" stroke="rgba(255,255,255,0.055)" stroke-width="1"/>`;
    grid += `<text x="${w - padR + 8}" y="${gy + 3}" fill="#667085" font-size="9.5" font-family="SFMono-Regular,Menlo,monospace">${fmtPrice(val)}</text>`;
  }

  const bodyW = Math.max(1, Math.min(9, step * 0.58));
  let candles = '';
  let vols = '';
  bars.forEach((b, i) => {
    const up = b.close >= b.open;
    const color = up ? '#3ddc97' : '#ff6b6b';
    const xc = x(i);
    const openY = y(b.open);
    const closeY = y(b.close);
    const top = Math.min(openY, closeY);
    const bodyH = Math.max(1, Math.abs(closeY - openY));
    candles += `<line x1="${xc}" y1="${y(b.high)}" x2="${xc}" y2="${y(b.low)}" stroke="${color}" stroke-width="1" opacity="0.75"/>`;
    candles += `<rect x="${xc - bodyW / 2}" y="${top}" width="${bodyW}" height="${bodyH}" fill="${color}" opacity="${up ? 0.9 : 0.85}"/>`;
    const vy = volY(b.volume || 0);
    vols += `<rect x="${xc - bodyW / 2}" y="${vy}" width="${bodyW}" height="${volTop + volH - vy}" fill="${color}" opacity="0.18"/>`;
  });

  const pathFor = (values) => {
    let d = '';
    let started = false;
    values.forEach((v, i) => {
      if (v == null) {
        started = false;
        return;
      }
      d += `${started ? 'L' : 'M'}${x(i).toFixed(1)},${y(v).toFixed(1)}`;
      started = true;
    });
    return d;
  };
  const ma20Path = pathFor(ma20);
  const ma60Path = pathFor(ma60);

  let labels = '';
  const ticks = [0, Math.round((bars.length - 1) / 3), Math.round(((bars.length - 1) * 2) / 3), bars.length - 1];
  ticks.forEach((i, k) => {
    const anchor = k === 0 ? 'start' : k === 3 ? 'end' : 'middle';
    labels += `<text x="${x(i)}" y="${h - 4}" text-anchor="${anchor}" fill="#667085" font-size="9" font-family="SFMono-Regular,Menlo,monospace">${bars[i]?.date?.slice(2) || ''}</text>`;
  });

  el.innerHTML = `<svg viewBox="0 0 ${w} ${h}" preserveAspectRatio="none">
    ${grid}
    ${vols}
    <line x1="${padL}" y1="${volTop + volH}" x2="${padL + plotW}" y2="${volTop + volH}" stroke="rgba(255,255,255,0.07)"/>
    ${candles}
    ${ma20Path ? `<path d="${ma20Path}" fill="none" stroke="#e6b35c" stroke-width="1.2" opacity="0.9"/>` : ''}
    ${ma60Path ? `<path d="${ma60Path}" fill="none" stroke="#6ea8fe" stroke-width="1.2" opacity="0.75"/>` : ''}
    <line class="crosshair" x1="-10" x2="-10" y1="${padT}" y2="${padT + priceH}" stroke="rgba(255,255,255,0.25)" stroke-dasharray="3 3"/>
    ${labels}
  </svg>`;
  attachHover(el, {
    padL,
    padT,
    plotW,
    plotH: priceH,
    count: bars.length,
    tooltip: (i) => {
      const b = bars[i];
      if (!b) return '';
      const prev = bars[i - 1]?.close ?? b.open;
      const chg = ((b.close / prev - 1) * 100).toFixed(2);
      const cls = Number(chg) >= 0 ? 'up' : 'down';
      return `<b>${escapeHtml(b.date)}</b><br>开 ${fmtPrice(b.open)} · 高 ${fmtPrice(b.high)}<br>低 ${fmtPrice(b.low)} · 收 ${fmtPrice(b.close)}<br><span class="${cls}">${chg}%</span>`;
    },
  });
}

function renderLineChart(el, labels, datasets, { height } = {}) {
  if (!labels.length) {
    el.innerHTML = '<p class="muted small">暂无数据</p>';
    return;
  }
  const { w, h } = chartSize(el);
  const padL = 6;
  const padR = 58;
  const padT = 14;
  const padB = 22;
  const plotW = w - padL - padR;
  const plotH = h - padT - padB;
  const all = datasets.flatMap((d) => d.values.filter((v) => v != null));
  let min = Math.min(...all);
  let max = Math.max(...all);
  if (min === max) {
    min *= 0.98;
    max *= 1.02;
  }
  const pad = (max - min) * 0.08;
  min -= pad;
  max += pad;
  const x = (i) => padL + (i / Math.max(1, labels.length - 1)) * plotW;
  const y = (v) => padT + ((max - v) / (max - min || 1)) * plotH;

  let grid = '';
  for (let i = 0; i <= 4; i++) {
    const val = max - ((max - min) * i) / 4;
    const gy = padT + (plotH * i) / 4;
    grid += `<line x1="${padL}" y1="${gy}" x2="${padL + plotW}" y2="${gy}" stroke="rgba(255,255,255,0.055)"/>`;
    grid += `<text x="${w - padR + 8}" y="${gy + 3}" fill="#667085" font-size="9.5" font-family="SFMono-Regular,Menlo,monospace">${fmtMoney(val)}</text>`;
  }
  const paths = datasets
    .map((d) => {
      let path = '';
      let started = false;
      d.values.forEach((v, i) => {
        if (v == null) {
          started = false;
          return;
        }
        path += `${started ? 'L' : 'M'}${x(i).toFixed(1)},${y(v).toFixed(1)}`;
        started = true;
      });
      return path ? `<path d="${path}" fill="none" stroke="${d.color}" stroke-width="${d.width || 1.6}" stroke-dasharray="${d.dash || 'none'}"/>` : '';
    })
    .join('');
  let labelsSvg = '';
  const ticks = [0, Math.round((labels.length - 1) / 3), Math.round(((labels.length - 1) * 2) / 3), labels.length - 1];
  ticks.forEach((i, k) => {
    const anchor = k === 0 ? 'start' : k === 3 ? 'end' : 'middle';
    labelsSvg += `<text x="${x(i)}" y="${h - 4}" text-anchor="${anchor}" fill="#667085" font-size="9" font-family="SFMono-Regular,Menlo,monospace">${escapeHtml(labels[i]?.slice(2) || '')}</text>`;
  });
  el.innerHTML = `<svg viewBox="0 0 ${w} ${h}" preserveAspectRatio="none">
    ${grid}
    ${paths}
    <line class="crosshair" x1="-10" x2="-10" y1="${padT}" y2="${padT + plotH}" stroke="rgba(255,255,255,0.25)" stroke-dasharray="3 3"/>
    ${labelsSvg}
  </svg>`;
  attachHover(el, {
    padL,
    padT,
    plotW,
    plotH,
    count: labels.length,
    tooltip: (i) =>
      `<b>${escapeHtml(labels[i] || '')}</b><br>${datasets
        .map((d) => `<span style="color:${d.color}">${escapeHtml(d.name)}</span> ${fmtMoney(d.values[i])}`)
        .join('<br>')}`,
  });
}

function sparkline(values, width = 74, height = 20) {
  const valid = values.filter((v) => isNum(v));
  if (valid.length < 2) return '';
  let min = Math.min(...valid);
  let max = Math.max(...valid);
  if (min === max) {
    max += 1;
    min -= 1;
  }
  const pts = valid
    .map((v, i) => `${((i / (valid.length - 1)) * width).toFixed(1)},${(height - ((v - min) / (max - min)) * height).toFixed(1)}`)
    .join(' ');
  const color = valid.at(-1) >= valid[0] ? '#3ddc97' : '#ff6b6b';
  return `<svg width="${width}" height="${height}" viewBox="0 0 ${width} ${height}" aria-hidden="true"><polyline points="${pts}" fill="none" stroke="${color}" stroke-width="1.3" opacity="0.8"/></svg>`;
}

/* ------------------------------ navigation ------------------------------ */

function setView(view) {
  state.view = view;
  $$('.nav-item').forEach((b) => b.classList.toggle('is-active', b.dataset.view === view));
  $$('.view').forEach((v) => v.classList.toggle('is-active', v.id === `view-${view}`));
  if (location.hash !== `#${view}`) history.replaceState(null, '', `#${view}`);
  if (view === 'market' && !state.watchlist.length) loadMarket();
  if (view === 'portfolio') loadPortfolio();
  if (view === 'report') loadReport();
  window.scrollTo({ top: 0, behavior: 'smooth' });
}

/* ------------------------------ market ------------------------------ */

async function loadIndexes() {
  try {
    const { quotes } = await api('/api/indexes');
    const strip = $('#indexStrip');
    strip.innerHTML = quotes
      .map(
        (q) => `<div class="index-card">
          <span>${escapeHtml(q.name)}</span>
          <strong>${fmtPrice(q.price)}</strong>
          <em class="${pctClass(q.pct)}">${fmtPct(q.pct)}</em>
        </div>`,
      )
      .join('');
    $('#tickerTape').innerHTML = quotes
      .map(
        (q) => `<span class="tick">${escapeHtml(q.name)} <b>${fmtPrice(q.price)}</b><i class="${pctClass(q.pct)}" style="font-style:normal">${fmtPct(q.pct)}</i></span>`,
      )
      .join('');
  } catch (err) {
    if (err.message !== 'unauthorized') toast(`指数加载失败：${err.message}`);
  }
}

async function loadMarket() {
  await Promise.all([loadIndexes(), loadWatchlist()]);
}

async function loadWatchlist() {
  try {
    const data = await api('/api/watchlist');
    state.watchlist = data.quotes || [];
    state.portfolio.watchlist = data.watchlist || [];
    state.watchlist.forEach((q) => state.quotes.set(q.symbol.toLowerCase(), q));
    renderWatchTable();
  } catch (err) {
    if (err.message !== 'unauthorized') toast(`自选股加载失败：${err.message}`);
  }
}

function renderWatchTable() {
  const body = $('#watchBody');
  const rows = state.watchlist;
  $('#watchCount').textContent = rows.length ? `${rows.length} 只标的 · 点击查看详情` : '暂无自选股';
  if (!rows.length) {
    body.innerHTML = `<tr><td colspan="6" class="muted small" style="padding:26px 8px;text-align:center">还没有自选股，在下方输入代码添加</td></tr>`;
    return;
  }
  body.innerHTML = rows
    .map(
      (q) => `<tr data-symbol="${q.symbol}" class="${state.activeSymbol === q.symbol ? 'is-active' : ''}">
        <td><span class="name"><strong>${escapeHtml(q.name)}</strong><small>${escapeHtml(q.symbol)}</small></span></td>
        <td class="num">${fmtPrice(q.price)}</td>
        <td class="num ${pctClass(q.pct)}">${fmtPct(q.pct)}</td>
        <td class="num muted" data-ret20="${q.symbol}">—</td>
        <td>${state.watchEdit ? `<button class="row-del" data-remove="${q.symbol}" title="移出自选">×</button>` : ''}</td>
      </tr>`,
    )
    .join('');
  hydrateRet20();
}

async function hydrateRet20() {
  await Promise.all(
    state.portfolio.watchlist.map(async (symbol) => {
      try {
        const { bars } = await api(`/api/kline?symbol=${encodeURIComponent(symbol)}&range=3m`);
        if (bars.length > 21) {
          const ret = (bars.at(-1).close / bars.at(-22).close - 1) * 100;
          const cell = document.querySelector(`[data-ret20="${symbol}"]`);
          if (cell) {
            cell.textContent = fmtPct(ret);
            cell.className = `num ${pctClass(ret)}`;
          }
        }
      } catch {
        /* ignore */
      }
    }),
  );
}

async function selectSymbol(symbol, { switchView = true } = {}) {
  if (!symbol) return;
  state.activeSymbol = symbol;
  if (switchView && state.view !== 'market') setView('market');
  $$('#watchBody tr').forEach((tr) => tr.classList.toggle('is-active', tr.dataset.symbol === symbol));
  $('#detailEmpty').hidden = true;
  $('#detailContent').hidden = false;
  $('#detailName').textContent = '加载中…';
  $$('#rangeTabs button').forEach((b) => b.classList.toggle('is-active', b.dataset.range === state.range));
  updateActiveChip();
  const seq = ++state.reqSeq;
  try {
    const data = await api(`/api/analysis?symbol=${encodeURIComponent(symbol)}&range=${state.range}`);
    if (seq !== state.reqSeq) return;
    state.analysis = data;
    renderDetail(data);
    if (state.quotes.has(data.symbol.toLowerCase())) state.quotes.set(data.symbol.toLowerCase(), data.quote);
  } catch (err) {
    if (err.message !== 'unauthorized') {
      $('#detailName').textContent = '加载失败';
      toast(`分析失败：${err.message}`);
    }
  }
}

function renderDetail(data) {
  const q = data.quote;
  const s = data.snapshot;
  if (!q) return;
  state.quotes.set(q.symbol.toLowerCase(), q);
  $('#detailMarket').textContent = `${marketLabel(q.market)} / ${q.isIndex ? '指数' : '个股'}`;
  $('#detailName').textContent = q.name;
  $('#detailCode').textContent = `${q.symbol} · ${q.code}`;
  $('#detailPrice').textContent = fmtPrice(q.price);
  $('#detailPct').textContent = fmtPct(q.pct);
  $('#detailPct').className = `mono ${pctClass(q.pct)}`;

  const unit = currencyName(q.currency);
  const stats = [
    ['今开', fmtPrice(q.open)],
    ['最高', fmtPrice(q.high)],
    ['最低', fmtPrice(q.low)],
    ['昨收', fmtPrice(q.prevClose)],
    ['成交额', fmtMoney(q.amount, unit)],
    ['换手率', isNum(q.turnover) ? `${q.turnover}%` : '—'],
    ['市盈率', isNum(q.pe) && q.pe > 0 ? q.pe.toFixed(1) : '—'],
    ['总市值', q.totalCap ? `${fmtMoney(q.totalCap * 1e8, unit)}` : '—'],
  ];
  $('#detailStats').innerHTML = stats
    .map(([k, v]) => `<div><span>${k}</span><strong>${escapeHtml(v)}</strong></div>`)
    .join('');

  const inds = [
    ['RSI 14', s.rsi14 ?? '—', s.rsi14 >= 70 ? '超买' : s.rsi14 <= 30 ? '超卖' : '中性'],
    ['MACD 柱', s.macd?.hist ?? '—', s.macd?.hist > 0 ? '红柱' : '绿柱'],
    ['量比', s.volRatio ?? '—', isNum(s.volRatio) ? (s.volRatio >= 1.5 ? '放量' : s.volRatio <= 0.6 ? '缩量' : '常态') : ''],
    ['20 日涨幅', fmtPct(s.ret20), s.trend],
    ['ATR 占比', isNum(s.atrPct) ? `${s.atrPct}%` : '—', '波动'],
    ['回撤', isNum(s.drawdownPct) ? `${s.drawdownPct.toFixed(1)}%` : '—', '距区间高点'],
    ['支撑', fmtPrice(s.support20), '20 日'],
    ['阻力', fmtPrice(s.resistance20), '20 日'],
  ];
  $('#indicatorGrid').innerHTML = inds
    .map(
      ([k, v, hint]) => `<div class="ind-card"><span>${k}</span><strong class="${String(v).startsWith('+') ? 'up' : String(v).startsWith('-') ? 'down' : ''}">${escapeHtml(v)}</strong><small>${escapeHtml(hint || '')}</small></div>`,
    )
    .join('');

  $('#signalList').innerHTML = (data.signals || [])
    .map((sig) => `<li class="${sig.tone}">${escapeHtml(sig.text)}</li>`)
    .join('');

  renderCandles($('#chart'), data.chart);
}

function updateActiveChip() {
  const chip = $('#activeSymbolChip');
  if (!state.activeSymbol) {
    chip.hidden = true;
    return;
  }
  const q = state.quotes.get(state.activeSymbol.toLowerCase());
  chip.hidden = false;
  $('#activeSymbolText').textContent = q ? `${q.name} ${q.symbol}` : state.activeSymbol;
}

/* search */
let searchTimer = null;
async function runSearch(q) {
  if (!q.trim()) {
    $('#searchResults').hidden = true;
    return;
  }
  try {
    const { results } = await api(`/api/search?q=${encodeURIComponent(q.trim())}`);
    const box = $('#searchResults');
    if (!results.length) {
      box.innerHTML = '<div class="search-item muted small">没有匹配结果</div>';
    } else {
      box.innerHTML = results
        .map(
          (r) => `<button class="search-item" data-symbol="${r.symbol}">
            <span><strong>${escapeHtml(r.name)}</strong> <small>${escapeHtml(r.typeLabel)}</small></span>
            <small>${escapeHtml(r.symbol)}</small>
          </button>`,
        )
        .join('');
    }
    box.hidden = false;
  } catch {
    /* ignore */
  }
}

/* ------------------------------ portfolio ------------------------------ */

async function loadPortfolio() {
  try {
    const data = await api('/api/portfolio');
    state.portfolio = data;
    await refreshDiagnosis();
  } catch (err) {
    if (err.message !== 'unauthorized') toast(`组合加载失败：${err.message}`);
  }
}

async function refreshDiagnosis() {
  try {
    const diag = await api('/api/portfolio/diagnose', { method: 'POST', body: '{}' });
    state.diagnosis = diag;
    renderPortfolio(diag);
  } catch (err) {
    if (err.message !== 'unauthorized') toast(`诊断失败：${err.message}`);
  }
}

function renderPortfolio(diag) {
  const rows = diag?.rows || [];
  const has = rows.length > 0;
  $('#pfCount').textContent = has ? `${rows.length} 只持仓` : '尚未录入持仓';
  const totals = diag?.totals;
  $('#pfValue').textContent = totals ? `¥${fmtMoney(totals.valueCny)}` : '—';
  $('#pfPnl').textContent = totals ? `${totals.pnlCny >= 0 ? '+' : ''}¥${fmtMoney(totals.pnlCny)}` : '—';
  $('#pfPnl').className = `mono ${totals ? pctClass(totals.pnlCny) : ''}`;
  $('#pfPnlSub').textContent = totals ? `${fmtPct(totals.pnlPct)} · 成本 ¥${fmtMoney(totals.costCny)}` : '录入持仓后计算';
  const top = rows[0];
  $('#pfTop').textContent = top ? `${top.weight}%` : '—';
  $('#pfTopSub').textContent = top ? top.name : '按市值计算';
  const risk = diag?.risk;
  $('#pfVol').textContent = risk ? `${risk.volatility}%` : '—';
  $('#pfVolSub').textContent = risk ? `最大回撤 ${risk.maxDrawdown}% · 夏普 ${risk.sharpe ?? '—'}` : '持仓样本不足时暂不估算';

  const body = $('#holdingsBody');
  if (!has) {
    body.innerHTML = '<tr><td colspan="7" class="muted small" style="padding:26px 8px;text-align:center">还没有持仓，使用下方表单添加</td></tr>';
  } else {
    body.innerHTML = rows
      .map(
        (r) => `<tr data-symbol="${r.symbol}">
          <td><span class="name"><strong>${escapeHtml(r.name)}</strong><small>${escapeHtml(r.symbol)}</small></span></td>
          <td class="num">${r.shares}</td>
          <td class="num">${fmtPrice(r.cost)}</td>
          <td class="num">${fmtPrice(r.price)}</td>
          <td class="num ${pctClass(r.pnlCny)}">${r.pnlPct >= 0 ? '+' : ''}${r.pnlPct}%<br><small class="muted">${r.pnlCny >= 0 ? '+' : ''}¥${fmtMoney(r.pnlCny)}</small></td>
          <td class="num hide-sm">${r.weight}%</td>
          <td><button class="row-del" data-del-holding="${escapeHtml(r.symbol)}" title="删除持仓">×</button></td>
        </tr>`,
      )
      .join('');
  }

  const allocation = $('#pfAllocation');
  if (has) {
    const marketRows = Object.entries(totals.marketWeights || {}).filter(([, v]) => v > 0);
    const marketNames = { cn: 'A 股', hk: '港股', us: '美股' };
    allocation.innerHTML = [
      ...marketRows.map(
        ([k, v]) => `<div class="alloc-row"><span><em style="font-style:normal">${marketNames[k] || k}</em><b class="mono">${v}%</b></span><div class="alloc-bar ${k}"><i style="width:${Math.min(100, v)}%"></i></div></div>`,
      ),
      `<div class="alloc-row" style="margin-top:8px"><span><em style="font-style:normal">持仓权重</em><b class="mono">Top 5</b></span></div>`,
      ...rows.slice(0, 5).map(
        (r) => `<div class="alloc-row"><span>${escapeHtml(r.name)}<b class="mono">${r.weight}%</b></span><div class="alloc-bar"><i style="width:${Math.min(100, r.weight)}%"></i></div></div>`,
      ),
    ].join('');
  } else {
    allocation.innerHTML = '<p class="muted small">录入持仓后展示市场暴露与权重分布。</p>';
  }

  const riskBox = $('#pfRisk');
  if (has && risk) {
    riskBox.innerHTML = [
      ['年化波动', `${risk.volatility}%`],
      ['最大回撤', `${risk.maxDrawdown}%`],
      ['夏普比率', risk.sharpe ?? '—'],
      ['基准相关性', risk.correlation ?? '—'],
    ]
      .map(([k, v]) => `<div class="risk-item"><span>${k}</span><strong>${escapeHtml(v)}</strong></div>`)
      .join('');
  } else {
    riskBox.innerHTML = '';
  }
  $('#pfNarrative').innerHTML = diag?.narrative ? md(diag.narrative) : '';
}

async function saveHoldings() {
  const payload = { holdings: state.portfolio.holdings, watchlist: state.portfolio.watchlist };
  await api('/api/portfolio', { method: 'PUT', body: JSON.stringify(payload) });
  await refreshDiagnosis();
  toast('组合已保存');
}

async function resolveSymbolInput(value) {
  const raw = String(value || '').trim();
  if (!raw) return null;
  try {
    const { results } = await api(`/api/search?q=${encodeURIComponent(raw)}`);
    if (results.length) return results[0];
  } catch {
    /* ignore */
  }
  const guess = /^\d{6}$/.test(raw)
    ? (/^[569]/.test(raw) ? `sh${raw}` : `sz${raw}`)
    : /^\d{5}$/.test(raw)
      ? `hk${raw}`
      : `us${raw.toUpperCase()}`;
  return { symbol: guess, name: raw };
}

/* ------------------------------ backtest ------------------------------ */

function syncStrategyFields() {
  const strategy = $('#btStrategy').value;
  const container = $('#btParams');
  if (strategy === 'ma_cross') {
    container.innerHTML = `<label>快线<input id="btFast" type="number" value="20" min="2" /></label>
      <label>慢线<input id="btSlow" type="number" value="60" min="5" /></label>`;
  } else if (strategy === 'rsi_reversion') {
    container.innerHTML = `<label>RSI 周期<input id="btFast" type="number" value="14" min="2" /></label>
      <label>超卖阈值<input id="btSlow" type="number" value="30" min="5" max="50" /></label>`;
  } else {
    container.innerHTML = `<label>回看周期<input id="btFast" type="number" value="20" min="3" /></label>`;
  }
}

async function runBacktest(event) {
  event.preventDefault();
  const symbol = $('#btSymbol').value.trim();
  if (!symbol) return;
  const strategy = $('#btStrategy').value;
  const fast = Number($('#btFast')?.value) || 20;
  const slow = Number($('#btSlow')?.value) || 60;
  const params =
    strategy === 'ma_cross'
      ? { fast, slow }
      : strategy === 'rsi_reversion'
        ? { period: fast, oversold: slow }
        : { period: fast };
  const btn = $('#btForm button[type="submit"]');
  btn.disabled = true;
  btn.textContent = '运行中…';
  try {
    const result = await api('/api/backtest', {
      method: 'POST',
      body: JSON.stringify({
        symbol,
        strategy,
        params,
        range: $('#btRange').value,
        initial: Number($('#btInitial').value) || 100000,
        fee: (Number($('#btFee').value) || 0) / 100,
      }),
    });
    renderBacktest(result);
  } catch (err) {
    if (err.message !== 'unauthorized') toast(`回测失败：${err.message}`);
  } finally {
    btn.disabled = false;
    btn.textContent = '运行回测';
  }
}

function renderBacktest(result) {
  state.lastBacktest = result;
  $('#btPlaceholder').hidden = true;
  $('#btResult').hidden = false;
  const s = result.stats;
  const stats = [
    ['累计收益', fmtPct(s.totalReturn)],
    ['年化收益', fmtPct(s.annualized)],
    ['最大回撤', `${s.maxDrawdown}%`],
    ['夏普比率', s.sharpe ?? '—'],
    ['买入持有', fmtPct(s.benchmarkReturn)],
    ['胜率', s.winRate != null ? `${s.winRate}%` : '—'],
    ['交易次数', s.trades],
    ['持仓占比', `${s.exposure}%`],
  ];
  $('#btStats').innerHTML = stats
    .map(([k, v]) => `<div class="bt-stat"><span>${k}</span><strong class="${String(v).startsWith('+') ? 'up' : String(v).startsWith('-') ? 'down' : ''}">${escapeHtml(v)}</strong></div>`)
    .join('');
  const labels = result.curve.map((c) => c.date);
  renderLineChart($('#btChart'), labels, [
    { name: '策略净值', color: '#e6b35c', values: result.curve.map((c) => c.equity), width: 1.8 },
    { name: '买入持有', color: '#667085', values: result.curve.map((c) => c.benchmark), width: 1.2, dash: '4 4' },
  ]);
  $('#btTrades').innerHTML = (result.trades || [])
    .map(
      (t) => `<tr>
        <td class="mono">${escapeHtml(t.entryDate)}</td>
        <td class="num">${fmtPrice(t.entryPrice)}</td>
        <td class="mono">${escapeHtml(t.exitDate)}</td>
        <td class="num">${fmtPrice(t.exitPrice)}</td>
        <td class="num ${pctClass(t.pnlPct)}">${fmtPct(t.pnlPct)}</td>
      </tr>`,
    )
    .join('');
  toast(`回测完成：${result.symbol} · ${result.bars} 个交易日`);
}

/* ------------------------------ report ------------------------------ */

async function loadReport(refresh = false) {
  const body = $('#reportBody');
  if (refresh) body.innerHTML = '<p class="muted">正在重新生成简报…</p>';
  try {
    const data = await api(`/api/report/daily${refresh ? '?refresh=1' : ''}`);
    state.report = data;
    $('#reportTitle').textContent = `市场简报 · ${data.date}`;
    $('#reportStamp').textContent = `${data.time} CST`;
    body.innerHTML = md(data.markdown);
    updateSched(data);
  } catch (err) {
    if (err.message !== 'unauthorized') {
      body.innerHTML = `<p class="muted">简报生成失败：${escapeHtml(err.message)}</p>`;
    }
  }
}

async function loadStatus() {
  try {
    const status = await api('/api/status');
    const badge = $('#llmBadge');
    if (status.llm.enabled) {
      badge.textContent = `模型 · ${status.llm.model || status.llm.provider}`;
      badge.classList.add('is-live');
    } else {
      badge.textContent = '内置规则引擎';
      badge.classList.remove('is-live');
    }
    $('#railStatus').textContent = status.llm.enabled ? '模型已连接' : '规则引擎在线';
    updateSched({ time: status.time?.time, date: status.time?.date, llm: status.llm });
    $('#schedTime').textContent = status.scheduler.time;
    $('#schedDays').textContent = status.scheduler.weekdaysOnly ? '交易日' : '每天';
    $('#schedLast').textContent = status.scheduler.lastReportDay || '本次启动未触发';
    $('#schedLlm').textContent = status.llm.enabled ? status.llm.provider : '内置规则';
    return status;
  } catch {
    return null;
  }
}

function updateSched(report) {
  if (report?.date) $('#reportStamp').textContent = `${report.time || ''} CST`;
}

/* ------------------------------ chat ------------------------------ */

function appendMessage(role, content, { typing = false } = {}) {
  const box = $('#chatMessages');
  const empty = $('#chatEmpty');
  if (empty) empty.remove();
  const el = document.createElement('div');
  el.className = `msg ${role}${typing ? ' typing' : ''}`;
  el.innerHTML = `<div class="avatar">${role === 'user' ? 'YOU' : 'AG'}</div>
    <div class="bubble">${typing ? '<i></i><i></i><i></i>' : role === 'user' ? `<p>${escapeHtml(content)}</p>` : md(content)}</div>`;
  box.appendChild(el);
  box.scrollTop = box.scrollHeight;
  return el;
}

async function sendChat(text) {
  if (state.busy) return;
  const content = String(text || '').trim();
  if (!content) return;
  state.busy = true;
  $('#chatSend').disabled = true;
  $('#chatInput').value = '';
  autoGrow($('#chatInput'));
  state.messages.push({ role: 'user', content });
  appendMessage('user', content);
  const typing = appendMessage('assistant', '', { typing: true });
  try {
    const data = await api('/api/chat', {
      method: 'POST',
      body: JSON.stringify({ messages: state.messages, activeSymbol: state.activeSymbol || '' }),
    });
    typing.remove();
    state.messages.push({ role: 'assistant', content: data.answer });
    appendMessage('assistant', data.answer);
    if (data.meta?.analysis?.quote) {
      const q = data.meta.analysis.quote;
      state.quotes.set(q.symbol.toLowerCase(), q);
    }
  } catch (err) {
    typing.remove();
    if (err.message !== 'unauthorized') appendMessage('assistant', `请求失败：${err.message}。请检查网络或稍后重试。`);
  } finally {
    state.busy = false;
    $('#chatSend').disabled = false;
    $('#chatInput').focus();
  }
}

function autoGrow(el) {
  el.style.height = 'auto';
  el.style.height = `${Math.min(el.scrollHeight, 180)}px`;
}

/* ------------------------------ token ------------------------------ */

function showTokenModal() {
  $('#tokenModal').hidden = false;
  setTimeout(() => $('#tokenInput').focus(), 50);
}

function initToken() {
  const url = new URL(location.href);
  const token = url.searchParams.get('token');
  if (token) {
    localStorage.setItem('aegis_token', token);
    url.searchParams.delete('token');
    history.replaceState(null, '', url.pathname + url.search + url.hash);
  }
  $('#tokenSubmit').addEventListener('click', () => {
    const value = $('#tokenInput').value.trim();
    if (!value) return;
    localStorage.setItem('aegis_token', value);
    $('#tokenModal').hidden = true;
    location.reload();
  });
  $('#tokenInput').addEventListener('keydown', (e) => {
    if (e.key === 'Enter') $('#tokenSubmit').click();
  });
}

/* ------------------------------ boot ------------------------------ */

function bindEvents() {
  $$('.nav-item').forEach((b) => b.addEventListener('click', () => setView(b.dataset.view)));

  $('#chatForm').addEventListener('submit', (e) => {
    e.preventDefault();
    sendChat($('#chatInput').value);
  });
  $('#chatInput').addEventListener('input', (e) => autoGrow(e.target));
  $('#chatInput').addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      sendChat($('#chatInput').value);
    }
  });
  $('#suggestions')?.addEventListener('click', (e) => {
    const card = e.target.closest('[data-prompt]');
    if (card) sendChat(card.dataset.prompt);
  });
  $('#clearSymbol').addEventListener('click', () => {
    state.activeSymbol = null;
    updateActiveChip();
    $$('#watchBody tr').forEach((tr) => tr.classList.remove('is-active'));
  });
  $('#askAiBtn').addEventListener('click', () => {
    if (!state.activeSymbol) return;
    setView('chat');
    sendChat(`分析 ${state.activeSymbol}`);
  });

  $('#refreshBtn').addEventListener('click', async (e) => {
    const btn = e.currentTarget;
    btn.classList.add('spinning');
    try {
      if (state.view === 'market') await loadMarket();
      else if (state.view === 'report') await loadReport(true);
      else if (state.view === 'portfolio') await refreshDiagnosis();
      else await loadIndexes();
      toast('数据已刷新');
    } finally {
      setTimeout(() => btn.classList.remove('spinning'), 500);
    }
  });

  $('#searchInput').addEventListener('input', (e) => {
    clearTimeout(searchTimer);
    const value = e.target.value;
    searchTimer = setTimeout(() => runSearch(value), 260);
  });
  $('#searchInput').addEventListener('keydown', (e) => {
    if (e.key === 'Enter') {
      e.preventDefault();
      const first = $('#searchResults .search-item[data-symbol]');
      if (first) {
        selectSymbol(first.dataset.symbol);
        $('#searchResults').hidden = true;
        $('#searchInput').blur();
      }
    }
  });
  $('#searchResults').addEventListener('click', (e) => {
    const item = e.target.closest('[data-symbol]');
    if (!item) return;
    selectSymbol(item.dataset.symbol);
    $('#searchResults').hidden = true;
    $('#searchInput').value = '';
  });
  document.addEventListener('click', (e) => {
    if (!e.target.closest('.search-wrap')) $('#searchResults').hidden = true;
  });

  $('#watchBody').addEventListener('click', async (e) => {
    const del = e.target.closest('[data-remove]');
    if (del) {
      e.stopPropagation();
      const symbol = del.dataset.remove;
      state.portfolio.watchlist = state.portfolio.watchlist.filter((s) => s !== symbol);
      try {
        await api('/api/portfolio', {
          method: 'PUT',
          body: JSON.stringify({ watchlist: state.portfolio.watchlist }),
        });
        await loadWatchlist();
        toast(`已移出自选：${symbol}`);
      } catch (err) {
        toast(`操作失败：${err.message}`);
      }
      return;
    }
    const row = e.target.closest('tr[data-symbol]');
    if (row) selectSymbol(row.dataset.symbol, { switchView: false });
  });

  $('#openWatchEditor').addEventListener('click', () => {
    state.watchEdit = !state.watchEdit;
    $('#openWatchEditor').textContent = state.watchEdit ? '完成管理' : '管理自选';
    renderWatchTable();
  });

  $('#watchForm').addEventListener('submit', async (e) => {
    e.preventDefault();
    const input = $('#watchSymbol');
    const hit = await resolveSymbolInput(input.value);
    if (!hit) return;
    if (state.portfolio.watchlist.includes(hit.symbol)) {
      toast('该标的已在自选中');
      return;
    }
    state.portfolio.watchlist = [...state.portfolio.watchlist, hit.symbol];
    try {
      await api('/api/portfolio', {
        method: 'PUT',
        body: JSON.stringify({ watchlist: state.portfolio.watchlist }),
      });
      input.value = '';
      await loadWatchlist();
      toast(`已添加：${hit.name || hit.symbol}`);
    } catch (err) {
      toast(`添加失败：${err.message}`);
    }
  });

  $('#rangeTabs').addEventListener('click', (e) => {
    const btn = e.target.closest('[data-range]');
    if (!btn) return;
    state.range = btn.dataset.range;
    $$('#rangeTabs button').forEach((b) => b.classList.toggle('is-active', b === btn));
    if (state.activeSymbol) selectSymbol(state.activeSymbol);
  });

  $('#holdingForm').addEventListener('submit', async (e) => {
    e.preventDefault();
    const hit = await resolveSymbolInput($('#hSymbol').value);
    const shares = Number($('#hShares').value);
    const cost = Number($('#hCost').value);
    if (!hit || !(shares > 0) || !(cost >= 0)) {
      toast('请填写有效的代码、股数与成本价');
      return;
    }
    state.portfolio.holdings = state.portfolio.holdings.filter((h) => h.symbol !== hit.symbol);
    state.portfolio.holdings.push({ symbol: hit.symbol, name: hit.name || '', shares, cost });
    try {
      await saveHoldings();
      $('#hSymbol').value = '';
      $('#hShares').value = '';
      $('#hCost').value = '';
      toast(`已加入：${hit.name || hit.symbol}`);
    } catch (err) {
      toast(`保存失败：${err.message}`);
    }
  });

  $('#holdingsBody').addEventListener('click', async (e) => {
    const del = e.target.closest('[data-del-holding]');
    if (!del) return;
    e.stopPropagation();
    const symbol = del.dataset.delHolding;
    state.portfolio.holdings = state.portfolio.holdings.filter((h) => h.symbol !== symbol);
    try {
      await saveHoldings();
    } catch (err) {
      toast(`删除失败：${err.message}`);
    }
  });

  $('#savePortfolio').addEventListener('click', async () => {
    try {
      await saveHoldings();
    } catch (err) {
      toast(`保存失败：${err.message}`);
    }
  });
  $('#refreshDiagnosis').addEventListener('click', () => refreshDiagnosis());

  $('#btStrategy').addEventListener('change', syncStrategyFields);
  $('#btForm').addEventListener('submit', runBacktest);

  $('#reportRefresh').addEventListener('click', () => loadReport(true));
  $('#reportPush').addEventListener('click', async () => {
    const btn = $('#reportPush');
    btn.disabled = true;
    try {
      await api('/api/report/push', { method: 'POST', body: '{}' });
      toast('已推送到飞书');
    } catch (err) {
      toast(`推送失败：${err.message}`);
    } finally {
      btn.disabled = false;
    }
  });

  window.addEventListener('resize', () => {
    if (state.view === 'market' && state.analysis) renderCandles($('#chart'), state.analysis.chart);
    if (state.view === 'backtest' && !$('#btResult').hidden) {
      const result = state.lastBacktest;
      if (result) renderLineChart($('#btChart'), result.curve.map((c) => c.date), [
        { name: '策略净值', color: '#e6b35c', values: result.curve.map((c) => c.equity), width: 1.8 },
        { name: '买入持有', color: '#667085', values: result.curve.map((c) => c.benchmark), width: 1.2, dash: '4 4' },
      ]);
    }
  });
}

async function boot() {
  initToken();
  bindEvents();
  const view = (location.hash || '#chat').slice(1);
  setView(['chat', 'market', 'portfolio', 'backtest', 'report'].includes(view) ? view : 'chat');

  const clock = () => {
    const now = new Date();
    $('#clock').textContent = new Intl.DateTimeFormat('zh-CN', {
      timeZone: 'Asia/Shanghai',
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
      hour12: false,
    }).format(now);
  };
  clock();
  setInterval(clock, 1000);

  await loadStatus();
  await loadIndexes();
  try {
    const data = await api('/api/portfolio');
    state.portfolio = data;
  } catch {
    /* ignore */
  }
  if (state.view === 'market') await loadMarket();
  if (state.view === 'portfolio') await loadPortfolio();
  if (state.view === 'report') await loadReport();
  if (state.view === 'backtest') syncStrategyFields();
}

boot();

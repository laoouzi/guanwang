import {
  annualizedVol,
  atr,
  boll,
  correlation,
  dailyReturns,
  macd,
  maxDrawdown,
  mean,
  rsi,
  sharpe,
  sma,
} from './indicators.js';
import {
  barsForRange,
  getFxRates,
  getKlines,
  getQuote,
  getQuotes,
  marketOf,
  normalizeSymbol,
  symbolLabel,
} from './providers.js';
import { fmtBig, num, pool, round } from './util.js';

const pctText = (v) => (Number.isFinite(v) ? `${v >= 0 ? '+' : ''}${v.toFixed(2)}%` : '—');
const px = (v) => (Number.isFinite(v) ? v.toFixed(2) : '—');
const currencyUnit = (c) => ({ CNY: '元', USD: '美元', HKD: '港元' })[c] || c || '';
const lastOf = (arr) => {
  for (let i = arr.length - 1; i >= 0; i--) if (Number.isFinite(arr[i])) return arr[i];
  return null;
};

export function technicalSnapshot(bars, quote = {}) {
  const closes = bars.map((b) => b.close);
  const n = closes.length;
  const last = closes[n - 1];
  const ma5 = lastOf(sma(closes, 5));
  const ma10 = lastOf(sma(closes, 10));
  const ma20 = lastOf(sma(closes, 20));
  const ma60 = lastOf(sma(closes, 60));
  const rsiSeries = rsi(closes, 14);
  const rsi14 = lastOf(rsiSeries);
  const m = macd(closes);
  const dif = lastOf(m.dif);
  const dea = lastOf(m.dea);
  const hist = lastOf(m.hist);
  const prevHist = n >= 2 ? lastOf(m.hist.slice(0, -1)) : null;
  const bb = boll(closes, 20, 2);
  const atrSeries = atr(bars, 14);
  const atr14 = lastOf(atrSeries);
  const vols = bars.map((b) => b.volume || 0);
  const avgVol20 = vols.length > 20 ? mean(vols.slice(-21, -1)) : mean(vols);
  const volRatio = avgVol20 ? vols[n - 1] / avgVol20 : null;
  const ret = (k) => (n > k ? (last / closes[n - 1 - k] - 1) * 100 : null);
  const recent = bars.slice(-20);
  const support20 = recent.length ? Math.min(...recent.map((b) => b.low)) : null;
  const resistance20 = recent.length ? Math.max(...recent.map((b) => b.high)) : null;
  const peak = Math.max(...closes);
  const drawdownPct = peak ? ((last - peak) / peak) * 100 : null;
  const high20 = n >= 20 ? Math.max(...closes.slice(-20)) : peak;
  const low20 = n >= 20 ? Math.min(...closes.slice(-20)) : Math.min(...closes);

  let trend = '震荡格局';
  if (ma20 != null && ma60 != null) {
    if (last > ma20 && ma20 > ma60) trend = '上升趋势';
    else if (last < ma20 && ma20 < ma60) trend = '下降趋势';
  }
  let alignment = null;
  if (ma5 != null && ma10 != null && ma20 != null && ma60 != null) {
    if (ma5 > ma10 && ma10 > ma20 && ma20 > ma60) alignment = '多头排列';
    else if (ma5 < ma10 && ma10 < ma20 && ma20 < ma60) alignment = '空头排列';
  }

  return {
    last: round(last, 4),
    ma5: round(ma5, 4),
    ma10: round(ma10, 4),
    ma20: round(ma20, 4),
    ma60: round(ma60, 4),
    rsi14: round(rsi14, 1),
    macd: { dif: round(dif, 4), dea: round(dea, 4), hist: round(hist, 4), prevHist: round(prevHist, 4) },
    boll: { up: round(lastOf(bb.up), 4), mid: round(lastOf(bb.mid), 4), low: round(lastOf(bb.low), 4) },
    atr14: round(atr14, 4),
    atrPct: atr14 && last ? round((atr14 / last) * 100, 2) : null,
    volRatio: round(volRatio, 2),
    avgVol20: round(avgVol20, 0),
    ret5: round(ret(5), 2),
    ret20: round(ret(20), 2),
    ret60: round(ret(60), 2),
    ret120: round(ret(120), 2),
    support20: round(support20, 4),
    resistance20: round(resistance20, 4),
    high20: round(high20, 4),
    low20: round(low20, 4),
    drawdownPct: round(drawdownPct, 2),
    trend,
    alignment,
    bars: n,
    high52: quote.high52 ?? null,
    low52: quote.low52 ?? null,
  };
}

export function buildSignals(s) {
  if (!s) return [];
  const list = [];
  const push = (tone, text) => list.push({ tone, text });

  if (s.trend === '上升趋势') push('bull', `价格位于 MA20 与 MA60 之上，均线结构呈上升趋势`);
  else if (s.trend === '下降趋势') push('bear', `价格跌破 MA20 与 MA60，均线结构偏空`);
  else push('neutral', `价格在均线附近反复，暂未形成明确趋势`);

  if (s.alignment === '多头排列') push('bull', 'MA5/10/20/60 多头排列，短中期方向一致');
  if (s.alignment === '空头排列') push('bear', 'MA5/10/20/60 空头排列，反弹需先修复均线');

  if (s.macd.hist != null && s.macd.prevHist != null) {
    if (s.macd.prevHist <= 0 && s.macd.hist > 0) push('bull', 'MACD 柱线翻红，短线动能转强');
    else if (s.macd.prevHist >= 0 && s.macd.hist < 0) push('bear', 'MACD 柱线翻绿，短线动能转弱');
    else if (s.macd.hist > 0 && s.macd.hist > s.macd.prevHist) push('bull', 'MACD 红柱放大，动能延续');
    else if (s.macd.hist < 0 && s.macd.hist < s.macd.prevHist) push('bear', 'MACD 绿柱放大，抛压仍在');
  }

  if (s.rsi14 != null) {
    if (s.rsi14 >= 70) push('bear', `RSI14 = ${s.rsi14}，进入超买区，短线追高性价比下降`);
    else if (s.rsi14 <= 30) push('bull', `RSI14 = ${s.rsi14}，进入超卖区，存在技术性反弹条件`);
    else if (s.rsi14 >= 55) push('bull', `RSI14 = ${s.rsi14}，多方略占优`);
    else if (s.rsi14 <= 45) push('bear', `RSI14 = ${s.rsi14}，空方略占优`);
    else push('neutral', `RSI14 = ${s.rsi14}，处于中性区间`);
  }

  if (s.volRatio != null && s.volRatio >= 1.5) push('bull', `成交量约为 20 日均量的 ${s.volRatio} 倍，量能明显放大`);
  else if (s.volRatio != null && s.volRatio <= 0.6) push('neutral', `成交量仅为 20 日均量的 ${s.volRatio} 倍，参与度偏低`);

  if (s.boll.up != null && s.boll.low != null) {
    if (s.last >= s.boll.up) push('bear', '价格触及布林上轨，短期波动可能加大');
    else if (s.last <= s.boll.low) push('bull', '价格触及布林下轨，短线偏离均值较远');
  }

  if (s.drawdownPct != null && s.drawdownPct <= -20) push('bear', `较近一年高点回撤 ${Math.abs(s.drawdownPct).toFixed(1)}%，仍处于修复阶段`);
  if (s.high20 != null && s.last >= s.high20) push('bull', '收盘价创 20 日新高，趋势有延续迹象');
  if (s.low20 != null && s.last <= s.low20) push('bear', '收盘价创 20 日新低，需关注止跌信号');
  return list;
}

export function narrateTechnical(quote, s, signals) {
  if (!quote || !s) return '暂无足够数据生成解读。';
  const volTxt = quote.amount ? fmtBig(quote.amount, currencyUnit(quote.currency)) : '—';
  const rsiTxt =
    s.rsi14 == null
      ? '数据不足'
      : s.rsi14 >= 70
        ? `${s.rsi14}（超买区）`
        : s.rsi14 <= 30
          ? `${s.rsi14}（超卖区）`
          : `${s.rsi14}（中性）`;
  const macdTxt =
    s.macd.hist == null
      ? '数据不足'
      : s.macd.hist > 0
        ? '红柱区间，短线动能为正'
        : '绿柱区间，短线动能为负';
  const lines = [
    `### ${quote.name} ${quote.code}`,
    `**最新价** ${px(quote.price)} ${quote.currency} · ${pctText(quote.pct)} · 成交额 ${volTxt}`,
    '',
    `**趋势结构**：当前处于${s.trend}${s.alignment ? `，均线${s.alignment}` : ''}。价格${s.ma20 != null ? `较 MA20 ${s.last >= s.ma20 ? '高' : '低'} ${Math.abs(((s.last - s.ma20) / s.ma20) * 100).toFixed(2)}%` : ''}${s.ma60 != null ? `，较 MA60 ${s.last >= s.ma60 ? '高' : '低'} ${Math.abs(((s.last - s.ma60) / s.ma60) * 100).toFixed(2)}%` : ''}。`,
    `**动能指标**：RSI14 为 ${rsiTxt}；MACD ${macdTxt}。`,
    `**波动与位置**：ATR 占价格 ${s.atrPct ?? '—'}%，20 日波动区间 ${px(s.low20)}–${px(s.high20)}；${s.drawdownPct != null ? `较近一年高点回撤 ${Math.abs(s.drawdownPct).toFixed(1)}%` : '回撤数据不足'}。`,
    `**量能**：当日成交量约为 20 日均量的 ${s.volRatio ?? '—'} 倍。`,
    `**关键价位**：短期支撑 ${px(s.support20)}，短期阻力 ${px(s.resistance20)}。`,
    '',
    '**信号清单**',
    ...signals.map((x) => `- ${x.tone === 'bull' ? '偏多' : x.tone === 'bear' ? '偏空' : '中性'} · ${x.text}`),
    '',
    '> 以上为公开行情与量化指标的技术性解读，不构成投资建议。市场有风险，决策需独立判断。',
  ];
  return lines.join('\n');
}

export async function analyzeSymbol(symbol, range = '1y') {
  const sym = normalizeSymbol(symbol);
  if (!sym) throw new Error('无法识别的代码');
  const count = barsForRange(range);
  const [quote, bars] = await Promise.all([getQuote(sym), getKlines(sym, count)]);
  if (!quote && !bars.length) throw new Error(`未找到 ${symbol} 的行情数据`);
  if (bars.length < 20) throw new Error(`标的 ${symbol} 历史数据不足`);
  const snapshot = technicalSnapshot(bars, quote || {});
  const signals = buildSignals(snapshot);
  const closes = bars.map((b) => b.close);
  const chart = {
    bars,
    ma5: sma(closes, 5).map((v) => round(v, 4)),
    ma20: sma(closes, 20).map((v) => round(v, 4)),
    ma60: sma(closes, 60).map((v) => round(v, 4)),
  };
  return {
    symbol: sym,
    quote,
    snapshot,
    signals,
    narrative: narrateTechnical(quote, snapshot, signals),
    chart,
  };
}

export async function compareSymbols(symbols, range = '6m') {
  const list = [...new Set((symbols || []).map(normalizeSymbol).filter(Boolean))].slice(0, 4);
  if (list.length < 2) throw new Error('对比至少需要两个标的');
  const count = barsForRange(range);
  const data = await pool(list, 4, async (sym) => {
    try {
      const [quote, bars] = await Promise.all([getQuote(sym), getKlines(sym, count)]);
      if (!bars.length) return null;
      const base = bars[0].close;
      const norm = bars.map((b, i) => ({ date: b.date, value: round((b.close / base) * 100, 2), i }));
      return { symbol: sym, quote, bars, norm, ret: quote?.pct ?? null };
    } catch {
      return null;
    }
  });
  const valid = data.filter(Boolean);
  if (valid.length < 2) throw new Error('可用数据不足，无法对比');
  const ranked = valid
    .map((x) => {
      const first = x.bars[0].close;
      const last = x.bars.at(-1).close;
      return {
        symbol: x.symbol,
        name: x.quote?.name || symbolLabel(x.symbol),
        total: round((last / first - 1) * 100, 2),
        last: last,
      };
    })
    .sort((a, b) => b.total - a.total);
  const lines = [
    `### 区间相对表现（${range}）`,
    ...ranked.map((r, i) => `${i + 1}. **${r.name}**（${r.symbol}） ${pctText(r.total)}`),
    '',
    `> 以区间首日收盘价归一为 100，仅比较相对强弱，不构成投资建议。`,
  ];
  return { series: valid, ranked, narrative: lines.join('\n') };
}

async function portfolioRisk(rows, marketWeights) {
  const sample = rows.filter((r) => r.weight > 0).slice(0, 12);
  const loaded = await pool(sample, 4, async (r) => {
    try {
      const bars = await getKlines(r.symbol, 140);
      if (bars.length < 45) return null;
      return { symbol: r.symbol, weight: r.weight, map: new Map(bars.map((b) => [b.date, b.close])) };
    } catch {
      return null;
    }
  });
  const valid = loaded.filter(Boolean);
  if (valid.length < 2) return null;
  const common = [...valid[0].map.keys()].filter((d) => valid.every((v) => v.map.has(d))).sort().slice(-80);
  if (common.length < 20) return null;
  const weights = valid.map((v) => v.weight);
  const total = weights.reduce((a, b) => a + b, 0) || 1;
  const rets = valid.map((v) => {
    const arr = [];
    for (let i = 1; i < common.length; i++) {
      arr.push(v.map.get(common[i]) / v.map.get(common[i - 1]) - 1);
    }
    return arr;
  });
  const port = common.slice(1).map((_, k) => rets.reduce((acc, r, i) => acc + (weights[i] / total) * r[k], 0));
  let cumulative = 1;
  const equity = port.map((r) => (cumulative *= 1 + r));
  const dominant = Object.entries(marketWeights).sort((a, b) => b[1] - a[1])[0]?.[0] || 'cn';
  const benchSymbol = dominant === 'us' ? 'us.INX' : dominant === 'hk' ? 'hkHSI' : 'sh000001';
  let benchCorr = null;
  try {
    const benchBars = await getKlines(benchSymbol, 160);
    const benchMap = new Map(benchBars.map((b) => [b.date, b.close]));
    const benchRets = [];
    for (let i = 1; i < common.length; i++) {
      if (benchMap.has(common[i]) && benchMap.has(common[i - 1])) {
        benchRets.push(benchMap.get(common[i]) / benchMap.get(common[i - 1]) - 1);
      } else {
        benchRets.push(0);
      }
    }
    benchCorr = round(correlation(port, benchRets), 2);
  } catch {
    benchCorr = null;
  }
  return {
    volatility: round(annualizedVol(port) * 100, 2),
    maxDrawdown: round(maxDrawdown(equity) * 100, 2),
    sharpe: round(sharpe(port), 2),
    correlation: benchCorr,
    benchmark: benchSymbol,
    days: common.length,
  };
}

export async function diagnosePortfolio(rawHoldings) {
  const holdings = (rawHoldings || [])
    .map((h) => ({
      symbol: normalizeSymbol(h.symbol),
      name: h.name || '',
      shares: num(h.shares, 0),
      cost: num(h.cost, 0),
    }))
    .filter((h) => h.symbol && h.shares > 0);

  if (!holdings.length) {
    return { empty: true, rows: [], totals: null, narrative: '尚未录入持仓。添加持仓后即可生成组合诊断。' };
  }

  const quotes = await getQuotes(holdings.map((h) => h.symbol));
  const quoteMap = new Map(quotes.map((q) => [q.symbol.toLowerCase(), q]));
  const fx = await getFxRates();
  const rows = holdings.map((h) => {
    const q = quoteMap.get(h.symbol.toLowerCase());
    const price = q?.price ?? h.cost;
    const currency = q?.currency || (marketOf(h.symbol) === 'cn' ? 'CNY' : marketOf(h.symbol) === 'hk' ? 'HKD' : 'USD');
    const value = price * h.shares;
    const costValue = h.cost * h.shares;
    const rate = fx[currency] || 1;
    return {
      symbol: h.symbol,
      name: q?.name || h.name || symbolLabel(h.symbol),
      market: marketOf(h.symbol),
      currency,
      shares: h.shares,
      cost: h.cost,
      price,
      pct: q?.pct ?? null,
      value: round(value, 2),
      valueCny: round(value * rate, 2),
      costCny: round(costValue * rate, 2),
      pnl: round(value - costValue, 2),
      pnlCny: round((value - costValue) * rate, 2),
      pnlPct: costValue ? round(((value - costValue) / costValue) * 100, 2) : null,
      weight: 0,
    };
  });

  const totalCny = rows.reduce((a, r) => a + r.valueCny, 0);
  rows.forEach((r) => {
    r.weight = totalCny ? round((r.valueCny / totalCny) * 100, 2) : 0;
  });
  rows.sort((a, b) => b.valueCny - a.valueCny);

  const costTotal = rows.reduce((a, r) => a + r.costCny, 0);
  const pnlTotal = rows.reduce((a, r) => a + r.pnlCny, 0);
  const marketWeights = { cn: 0, hk: 0, us: 0 };
  rows.forEach((r) => {
    marketWeights[r.market] += r.weight;
  });
  Object.keys(marketWeights).forEach((k) => {
    marketWeights[k] = round(marketWeights[k], 2);
  });
  const hhi = round(
    rows.reduce((a, r) => a + (r.weight / 100) ** 2, 0),
    4,
  );
  const top1 = rows[0];
  const top3 = round(Math.min(100, rows.slice(0, 3).reduce((a, r) => a + r.weight, 0)), 2);
  const risk = await portfolioRisk(rows, marketWeights);

  const notes = [];
  if (top1 && top1.weight >= 40) notes.push(`第一大持仓 ${top1.name} 占比 ${top1.weight}%，集中度偏高，单一标的风险会显著放大组合波动。`);
  if (rows.length >= 2 && hhi >= 0.35) notes.push(`持仓集中度指数 HHI 为 ${hhi}，组合分散度不足。`);
  const dominantMarket = Object.entries(marketWeights).sort((a, b) => b[1] - a[1])[0];
  if (dominantMarket && dominantMarket[1] >= 70) {
    const label = { cn: 'A 股', hk: '港股', us: '美股' }[dominantMarket[0]];
    notes.push(`${label}敞口占 ${dominantMarket[1]}%，地域集中度较高，需留意单一市场的系统性风险。`);
  }
  if (risk?.volatility != null && risk.volatility >= 35) notes.push(`按近 80 个交易日估算，组合年化波动约 ${risk.volatility}%，短期回撤风险不低。`);
  if (risk?.correlation != null && risk.correlation >= 0.8) notes.push(`组合与基准相关性 ${risk.correlation}，分散效果有限，资产走势较为同质。`);
  const worst = [...rows].sort((a, b) => (a.pnlPct ?? 0) - (b.pnlPct ?? 0))[0];
  if (worst && worst.pnlPct != null && worst.pnlPct <= -20) notes.push(`${worst.name} 浮亏 ${worst.pnlPct}%，建议复核原始买入逻辑是否仍然成立。`);
  if (!notes.length) notes.push('组合集中度与波动处于可接受区间，仍建议定期复核持仓逻辑与止损纪律。');

  const narrative = [
    `### 组合诊断`,
    `**总市值** ¥${fmtBig(totalCny)} · **累计盈亏** ${pnlTotal >= 0 ? '+' : ''}¥${fmtBig(pnlTotal)}（${pctText(costTotal ? (pnlTotal / costTotal) * 100 : 0)}）`,
    `**持仓数量** ${rows.length} 只 · **最大单一占比** ${top1 ? `${top1.name} ${top1.weight}%` : '—'} · **前三大合计** ${top3}%`,
    risk
      ? `**风险特征** 年化波动 ${risk.volatility}% · 最大回撤 ${risk.maxDrawdown}% · 夏普 ${risk.sharpe ?? '—'} · 与基准相关性 ${risk.correlation ?? '—'}`
      : '**风险特征** 持仓样本不足，暂无法估算组合波动。',
    '',
    '**关注项**',
    ...notes.map((n) => `- ${n}`),
    '',
    '> 组合诊断基于公开行情与历史波动估算，不构成投资建议。汇率按最新中间价折算。',
  ].join('\n');

  return {
    empty: false,
    rows,
    totals: {
      valueCny: round(totalCny, 2),
      costCny: round(costTotal, 2),
      pnlCny: round(pnlTotal, 2),
      pnlPct: costTotal ? round((pnlTotal / costTotal) * 100, 2) : 0,
      hhi,
      top3,
      marketWeights,
      fx,
    },
    risk,
    notes,
    narrative,
  };
}

export async function runScreener({ symbols, filters = {} }) {
  const list = [...new Set((symbols || []).map(normalizeSymbol).filter(Boolean))].slice(0, 25);
  if (!list.length) return { rows: [], total: 0, narrative: '股票池为空，请先添加自选股。' };
  const collected = await pool(list, 4, async (sym) => {
    try {
      const [quote, bars] = await Promise.all([getQuote(sym), getKlines(sym, 130)]);
      if (!quote || bars.length < 30) return null;
      const s = technicalSnapshot(bars, quote);
      return {
        symbol: sym,
        name: quote.name,
        market: quote.market,
        price: quote.price,
        pct: quote.pct,
        pe: quote.pe,
        rsi14: s.rsi14,
        ret20: s.ret20,
        ret60: s.ret60,
        volRatio: s.volRatio,
        aboveMa20: s.ma20 != null ? s.last > s.ma20 : null,
        aboveMa60: s.ma60 != null ? s.last > s.ma60 : null,
        drawdownPct: s.drawdownPct,
        trend: s.trend,
      };
    } catch {
      return null;
    }
  });
  let rows = collected.filter(Boolean);
  const f = filters || {};
  if (f.market) rows = rows.filter((r) => r.market === f.market);
  if (Number.isFinite(f.pctMin)) rows = rows.filter((r) => r.pct != null && r.pct >= f.pctMin);
  if (Number.isFinite(f.pctMax)) rows = rows.filter((r) => r.pct != null && r.pct <= f.pctMax);
  if (Number.isFinite(f.peMax)) rows = rows.filter((r) => r.pe != null && r.pe > 0 && r.pe <= f.peMax);
  if (Number.isFinite(f.rsiMin)) rows = rows.filter((r) => r.rsi14 != null && r.rsi14 >= f.rsiMin);
  if (Number.isFinite(f.rsiMax)) rows = rows.filter((r) => r.rsi14 != null && r.rsi14 <= f.rsiMax);
  if (Number.isFinite(f.ret20Min)) rows = rows.filter((r) => r.ret20 != null && r.ret20 >= f.ret20Min);
  if (Number.isFinite(f.volRatioMin)) rows = rows.filter((r) => r.volRatio != null && r.volRatio >= f.volRatioMin);
  if (f.aboveMa20) rows = rows.filter((r) => r.aboveMa20 === true);
  if (f.aboveMa60) rows = rows.filter((r) => r.aboveMa60 === true);
  rows.sort((a, b) => (b.ret20 ?? -999) - (a.ret20 ?? -999));
  return {
    rows,
    total: list.length,
    narrative: rows.length
      ? `在 ${list.length} 只自选股中筛选出 ${rows.length} 只符合条件，按 20 日涨幅排序。`
      : `在 ${list.length} 只自选股中没有符合全部条件的标的，可放宽条件后重试。`,
  };
}

function buildStrategySignals(closes, strategy, params) {
  const n = closes.length;
  const signal = new Array(n).fill(0);
  if (strategy === 'rsi_reversion') {
    const period = params.period || 14;
    const low = params.oversold ?? 30;
    const high = params.overbought ?? 70;
    const series = rsi(closes, period);
    let pos = 0;
    for (let i = 0; i < n; i++) {
      const v = series[i];
      if (v == null) {
        signal[i] = 0;
        continue;
      }
      if (pos === 0 && v <= low) pos = 1;
      else if (pos === 1 && v >= high) pos = 0;
      signal[i] = pos;
    }
    return signal;
  }
  if (strategy === 'breakout') {
    const period = params.period || 20;
    let pos = 0;
    for (let i = 0; i < n; i++) {
      if (i < period) {
        signal[i] = 0;
        continue;
      }
      const window = closes.slice(i - period, i);
      const hi = Math.max(...window);
      const lo = Math.min(...window);
      if (pos === 0 && closes[i] > hi) pos = 1;
      else if (pos === 1 && closes[i] < lo) pos = 0;
      signal[i] = pos;
    }
    return signal;
  }
  if (strategy === 'momentum') {
    const period = params.period || 20;
    for (let i = 0; i < n; i++) {
      signal[i] = i >= period && closes[i] > closes[i - period] ? 1 : 0;
    }
    return signal;
  }
  const fast = params.fast || 20;
  const slow = params.slow || 60;
  const fastMa = sma(closes, fast);
  const slowMa = sma(closes, slow);
  for (let i = 0; i < n; i++) {
    signal[i] = fastMa[i] != null && slowMa[i] != null && fastMa[i] > slowMa[i] ? 1 : 0;
  }
  return signal;
}

export async function backtestSymbol({ symbol, strategy = 'ma_cross', params = {}, initial = 100000, fee = 0.0005, range = '3y' }) {
  const sym = normalizeSymbol(symbol);
  if (!sym) throw new Error('无法识别的代码');
  const [quote, bars] = await Promise.all([getQuote(sym), getKlines(sym, barsForRange(range))]);
  if (bars.length < 60) throw new Error('历史数据不足，至少需要 60 个交易日');
  const closes = bars.map((b) => b.close);
  const signal = buildStrategySignals(closes, strategy, params || {});
  let equity = Number(initial) || 100000;
  const startCapital = equity;
  let pos = 0;
  let entry = null;
  const trades = [];
  const curve = [{ date: bars[0].date, equity: round(equity, 2), benchmark: round(equity, 2), position: 0 }];
  for (let i = 1; i < bars.length; i++) {
    const dayRet = closes[i] / closes[i - 1] - 1;
    if (pos === 1) equity *= 1 + dayRet;
    const target = signal[i];
    if (target !== pos) {
      equity *= 1 - fee;
      if (target === 1) {
        entry = { date: bars[i].date, price: closes[i], equity };
      } else if (entry) {
        trades.push({
          entryDate: entry.date,
          entryPrice: round(entry.price, 3),
          exitDate: bars[i].date,
          exitPrice: round(closes[i], 3),
          pnlPct: round(((closes[i] / entry.price) * (1 - fee) ** 2 - 1) * 100, 2),
          days: trades.length + 1,
        });
        entry = null;
      }
      pos = target;
    }
    const bench = (closes[i] / closes[0]) * startCapital;
    curve.push({
      date: bars[i].date,
      equity: round(equity, 2),
      benchmark: round(bench, 2),
      position: pos,
    });
  }
  if (entry) {
    const lastClose = closes.at(-1);
    trades.push({
      entryDate: entry.date,
      entryPrice: round(entry.price, 3),
      exitDate: bars.at(-1).date,
      exitPrice: round(lastClose, 3),
      pnlPct: round(((lastClose / entry.price) * (1 - fee) ** 2 - 1) * 100, 2),
      days: trades.length + 1,
    });
  }
  const equityValues = curve.map((c) => c.equity);
  const rets = dailyReturns(equityValues);
  const years = bars.length / 252;
  const totalReturn = (equity / startCapital - 1) * 100;
  const annualized = years > 0 ? ((equity / startCapital) ** (1 / years) - 1) * 100 : 0;
  const benchmarkReturn = (closes.at(-1) / closes[0] - 1) * 100;
  const wins = trades.filter((t) => t.pnlPct > 0).length;
  return {
    symbol: sym,
    name: quote?.name || symbolLabel(sym),
    strategy,
    params,
    range,
    bars: bars.length,
    curve,
    trades: trades.slice(-20),
    stats: {
      initial: round(startCapital, 2),
      final: round(equity, 2),
      totalReturn: round(totalReturn, 2),
      annualized: round(annualized, 2),
      maxDrawdown: round(maxDrawdown(equityValues) * 100, 2),
      sharpe: round(sharpe(rets), 2),
      benchmarkReturn: round(benchmarkReturn, 2),
      trades: trades.length,
      winRate: trades.length ? round((wins / trades.length) * 100, 1) : null,
      exposure: round((curve.filter((c) => c.position === 1).length / curve.length) * 100, 1),
    },
  };
}

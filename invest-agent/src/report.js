import { diagnosePortfolio } from './analysis.js';
import { llmChat, llmStatus } from './llm.js';
import { getQuotes } from './providers.js';
import { loadPortfolio, reportFile } from './store.js';
import { cacheGet, cnNow, fmtBig, memo, readJson, writeJson } from './util.js';

const INDICES = [
  { symbol: 'sh000001', label: '上证指数', market: 'cn' },
  { symbol: 'sz399001', label: '深证成指', market: 'cn' },
  { symbol: 'sz399006', label: '创业板指', market: 'cn' },
  { symbol: 'hkHSI', label: '恒生指数', market: 'hk' },
  { symbol: 'us.DJI', label: '道琼斯', market: 'us' },
  { symbol: 'us.IXIC', label: '纳斯达克', market: 'us' },
  { symbol: 'us.INX', label: '标普500', market: 'us' },
];

const sign = (v) => (Number.isFinite(v) && v >= 0 ? '+' : '');
const line = (q) => `- ${q.name}：${q.price}（${sign(q.pct)}${q.pct}%）`;

async function composeInsights(indexQuotes, movers, diagnosis) {
  const insights = [];
  const cn = indexQuotes.filter((q) => q.symbol.startsWith('sh') || q.symbol.startsWith('sz'));
  const us = indexQuotes.filter((q) => q.symbol.startsWith('us.'));
  const avg = (arr) => (arr.length ? arr.reduce((a, q) => a + (q.pct || 0), 0) / arr.length : null);
  const cnAvg = avg(cn);
  const usAvg = avg(us);
  if (cnAvg != null) {
    insights.push(
      cnAvg <= -1
        ? `A 股主要指数平均下跌 ${Math.abs(cnAvg).toFixed(2)}%，短线情绪偏弱，留意成交量能否企稳。`
        : cnAvg >= 1
          ? `A 股主要指数平均上涨 ${cnAvg.toFixed(2)}%，风险偏好有所回升，关注领涨板块的持续性。`
          : `A 股主要指数整体波动有限（均值 ${cnAvg.toFixed(2)}%），处于结构性行情。`,
    );
  }
  if (usAvg != null && Math.abs(usAvg) >= 1) {
    insights.push(`美股三大指数平均${usAvg >= 0 ? '上涨' : '下跌'} ${Math.abs(usAvg).toFixed(2)}%，海外风险偏好对次日 A 股与港股有情绪传导。`);
  }
  if (movers.gainers[0] && movers.gainers[0].pct >= 3) insights.push(`自选股中 ${movers.gainers[0].name} 领涨 ${movers.gainers[0].pct}%，可关注是否有基本面或消息面催化。`);
  if (movers.losers[0] && movers.losers[0].pct <= -3) insights.push(`自选股中 ${movers.losers[0].name} 领跌 ${movers.losers[0].pct}%，建议复核持仓逻辑是否发生变化。`);
  if (diagnosis && !diagnosis.empty) {
    insights.push(
      `组合当前浮盈亏 ${sign(diagnosis.totals.pnlPct)}${diagnosis.totals.pnlPct}%，最大单一持仓占比 ${diagnosis.rows[0]?.weight ?? 0}%，${
        diagnosis.rows[0]?.weight >= 40 ? '集中度偏高，注意单一标的风险。' : '集中度处于可控区间。'
      }`,
    );
  }
  if (!insights.length) insights.push('隔夜与盘前数据波动有限，建议以持仓逻辑与风险预算为锚，避免被短期噪音干扰。');
  return insights;
}

async function build() {
  const now = cnNow();
  const [indexRaw, portfolio] = await Promise.all([getQuotes(INDICES.map((i) => i.symbol)), loadPortfolio()]);
  const indexQuotes = INDICES.map((meta) => {
    const q = indexRaw.find((x) => x.symbol.toLowerCase() === meta.symbol.toLowerCase());
    return q ? { ...q, label: meta.label, market: meta.market } : null;
  }).filter(Boolean);

  const watchRaw = await getQuotes(portfolio.watchlist);
  const watch = watchRaw.filter((q) => !q.isIndex).sort((a, b) => (b.pct || 0) - (a.pct || 0));
  const movers = {
    gainers: watch.filter((q) => q.pct > 0).slice(0, 3),
    losers: watch.filter((q) => q.pct < 0).sort((a, b) => a.pct - b.pct).slice(0, 3),
  };
  const diagnosis = portfolio.holdings.length ? await diagnosePortfolio(portfolio.holdings) : null;
  const insights = await composeInsights(indexQuotes, movers, diagnosis);

  let llmComment = null;
  if (llmStatus().enabled) {
    llmComment = await llmChat(
      [
        { role: 'system', content: '你是投资研究助理，只基于给定数据写简报，不编造新闻，不给买卖指令。' },
        {
          role: 'user',
          content: `基于以下数据写 3 条盘前观察（每条不超过 60 字，能指出风险或需验证的点）：\n${JSON.stringify({
            index: indexQuotes.map((q) => ({ name: q.name, pct: q.pct })),
            watch: watch.slice(0, 6).map((q) => ({ name: q.name, pct: q.pct })),
            portfolio: diagnosis && !diagnosis.empty ? diagnosis.totals : null,
          })}`,
        },
      ],
      { temperature: 0.4, maxTokens: 400 },
    );
  }

  const group = (market) => indexQuotes.filter((q) => q.market === market);
  const sections = [
    { title: 'A 股', items: group('cn') },
    { title: '港股', items: group('hk') },
    { title: '美股', items: group('us') },
  ].filter((s) => s.items.length);

  const markdown = [
    `### 市场简报 · ${now.date}`,
    ...sections.map((s) => [`**${s.title}**`, ...s.items.map(line)].join('\n')),
    '',
    '**自选股异动**',
    ...(movers.gainers.length
      ? movers.gainers.map((q) => `- 领涨 ${q.name}（${q.symbol}） ${sign(q.pct)}${q.pct}%`)
      : ['- 暂无自选股数据']),
    ...(movers.losers[0] && movers.losers[0].pct < 0
      ? movers.losers.slice(0, 2).map((q) => `- 领跌 ${q.name}（${q.symbol}） ${q.pct}%`)
      : []),
    '',
    '**组合速览**',
    diagnosis && !diagnosis.empty
      ? `总市值 ¥${fmtBig(diagnosis.totals.valueCny)}，浮动盈亏 ${sign(diagnosis.totals.pnlPct)}${diagnosis.totals.pnlPct}%，最大单一持仓 ${diagnosis.rows[0]?.weight ?? 0}%。`
      : '尚未录入持仓，可在「组合」页添加后获得诊断。',
    '',
    '**今日关注**',
    ...(llmComment
      ? llmComment
          .split('\n')
          .filter(Boolean)
          .map((t) => `- ${t.replace(/^[-*\d.\s]+/, '')}`)
      : insights.map((t) => `- ${t}`)),
    '',
    '> 数据来自公开行情接口，可能存在延迟。本简报仅供研究参考，不构成投资建议。',
  ].join('\n');

  const report = {
    date: now.date,
    time: now.time,
    generatedAt: new Date().toISOString(),
    indexQuotes,
    watch,
    movers,
    portfolio: diagnosis && !diagnosis.empty ? diagnosis.totals : null,
    insights,
    markdown,
    llm: llmStatus().enabled,
  };
  try {
    await writeJson(reportFile(now.date), report);
  } catch (err) {
    console.warn('[report] persist failed:', err.message);
  }
  return report;
}

export async function buildDailyReport({ refresh = false } = {}) {
  if (refresh) return build();
  const cached = cacheGet('daily-report');
  if (cached) return cached;
  return memo('daily-report', 60 * 1000, build);
}

export async function getStoredReport(date) {
  return readJson(reportFile(date), null);
}

export async function pushReport(report) {
  const webhook = process.env.LARK_WEBHOOK || process.env.FEISHU_WEBHOOK;
  if (!webhook) throw new Error('未配置 LARK_WEBHOOK / FEISHU_WEBHOOK');
  const { fetchText } = await import('./util.js');
  const text = await fetchText(webhook, {
    method: 'POST',
    timeout: 12000,
    retries: 1,
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ msg_type: 'text', content: { text: report.markdown } }),
  });
  let parsed = null;
  try {
    parsed = JSON.parse(text);
  } catch {
    /* ignore */
  }
  if (parsed && parsed.code && parsed.code !== 0) {
    throw new Error(`推送失败：${parsed.msg || parsed.code}`);
  }
  return { ok: true, response: parsed };
}

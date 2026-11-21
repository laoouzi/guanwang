import {
  analyzeSymbol,
  backtestSymbol,
  compareSymbols,
  diagnosePortfolio,
  runScreener,
} from './analysis.js';
import { llmChat, llmStatus } from './llm.js';
import { getQuote, normalizeSymbol, symbolLabel } from './providers.js';
import { buildDailyReport } from './report.js';
import { loadPortfolio } from './store.js';
import { fmtBig } from './util.js';

const SYSTEM_PROMPT = `你是「Aegis 投研助手」，面向个人投资者，覆盖 A 股、港股、美股与全球指数。
规则：
1. 只依据提供的数据作答，不得编造价格、财务数据、新闻或研报观点。
2. 清晰区分「客观数据」与「主观推断」，推断处使用「可能」「倾向于」等措辞。
3. 不给出买入/卖出指令，不承诺收益，不做代客理财；输出研究视角与风险提示。
4. 使用简体中文，结构化、简洁，可使用 Markdown 小标题与列表。
5. 结尾固定附一句：以上内容仅供研究参考，不构成投资建议。`;

const HELP = `### 我可以帮你做这些
- **个股 / 指数解读**：「分析 600519」「NVDA 怎么样」「茅台技术面」
- **行情速查**：「AAPL 行情」「腾讯多少钱」
- **组合诊断**：先在「组合」页录入持仓，再问「诊断我的组合」
- **盘前简报**：「今日日报」「市场早报」
- **策略回测**：「回测 600519 均线 20/60」「回测 AAPL 突破 20 日」
- **自选股筛选**：「筛选自选股 RSI 小于 40」「筛 PE 小于 25 的」
也可以直接问：「美股科技股现在的风险在哪？」

> 我只做研究与风险提示，不提供买卖指令，也不代客操作。`;

const STOP_WORDS = new Set([
  'RSI', 'MACD', 'PE', 'PB', 'ETF', 'IPO', 'GDP', 'CPI', 'PPI', 'AI', 'US', 'CN', 'HK', 'MA', 'BOLL',
  'ATR', 'KDJ', 'ROE', 'EPS', 'CEO', 'FED', 'YOY', 'QOQ', 'OK', 'VS', 'PK', 'THE', 'AND', 'FOR', 'BUY',
  'SELL', 'HOLD', 'LONG', 'SHORT', 'RSI14', 'MACD12', 'SPX', 'K', 'OK',
]);

function detectIntent(text) {
  const t = String(text || '').trim();
  if (!t) return 'help';
  if (/^(hi|hello|hey|你好|您好|帮助|help|能做什么|怎么用|使用说明)[!！。.\s]*$/i.test(t)) return 'help';
  if (/日报|早报|盘前|简报|今日市场|市场概览/.test(t)) return 'report';
  if (/持仓|组合|仓位|诊断/.test(t)) return 'portfolio';
  if (/回测|策略测试|历史回测/.test(t)) return 'backtest';
  if (/筛选|选股|筛一下|股票池/.test(t)) return 'screener';
  if (/对比|比较|\bvs\b|\bpk\b/i.test(t)) return 'compare';
  if (/分析|解读|走势|技术面|基本面|估值|能不能买|可以买|怎么样|如何看|值不值得/.test(t)) return 'analyze';
  if (/价格|行情|多少钱|涨跌|现价|报价/.test(t)) return 'quote';
  return 'general';
}

function extractSymbols(text, activeSymbol) {
  const t = String(text || '');
  const found = [];
  const add = (v) => {
    const sym = normalizeSymbol(v);
    if (sym && !found.includes(sym)) found.push(sym);
  };
  for (const m of t.matchAll(/\b(?:sh|sz|bj|hk)\d{5,6}\b/gi)) add(m[0]);
  for (const m of t.matchAll(/\$([A-Za-z.]{1,10})\$/g)) add(m[1]);
  for (const m of t.matchAll(/\b\d{6}\b/g)) if (!/^(19|20)\d{4}$/.test(m[0])) add(m[0]);
  for (const m of t.matchAll(/\b\d{5}\b/g)) add(m[0]);
  for (const m of t.matchAll(/\b[A-Za-z]{1,5}(?:\.[A-Za-z]{1,3})?\b/g)) {
    const token = m[0].toUpperCase();
    if (STOP_WORDS.has(token)) continue;
    if (token.length < 2) continue;
    add(token);
  }
  if (!found.length && activeSymbol) add(activeSymbol);
  return found.slice(0, 4);
}

function marketLabel(market) {
  return { cn: 'A 股', hk: '港股', us: '美股' }[market] || market;
}

function quoteAnswer(q) {
  const unit = { CNY: '元', USD: '美元', HKD: '港元' }[q.currency] || q.currency;
  return [
    `### ${q.name} ${q.code}`,
    `**${q.price} ${q.currency}** · ${q.pct >= 0 ? '+' : ''}${q.pct}% · 成交额 ${fmtBig(q.amount, unit)}`,
    `- 今开 ${q.open ?? '—'} · 最高 ${q.high ?? '—'} · 最低 ${q.low ?? '—'} · 昨收 ${q.prevClose ?? '—'}`,
    `- 市盈率 ${q.pe ?? '—'} · 总市值 ${fmtBig((q.totalCap || 0) * 1e8, unit)} · 换手率 ${q.turnover ?? '—'}% · 振幅 ${q.amplitude ?? '—'}%`,
    `- 市场：${marketLabel(q.market)} · 更新时间：${q.time || '—'}`,
    '',
    `可以继续问「分析 ${q.code}」查看技术面与关键价位。`,
    '',
    '> 以上内容仅供研究参考，不构成投资建议。',
  ].join('\n');
}

async function withLlm(facts, instruction, fallback) {
  if (!llmStatus().enabled) return { text: fallback, usedLlm: false };
  const answer = await llmChat(
    [
      { role: 'system', content: SYSTEM_PROMPT },
      {
        role: 'user',
        content: `以下是公开行情与计算得到的客观数据（JSON）：\n${JSON.stringify(facts)}\n\n${instruction}`,
      },
    ],
    { temperature: 0.3, maxTokens: 900 },
  );
  return answer ? { text: answer, usedLlm: true } : { text: fallback, usedLlm: false };
}

function screenerFilters(text) {
  const filters = {};
  const grab = (re) => {
    const m = String(text).match(re);
    return m ? Number(m[1]) : null;
  };
  const rsi = grab(/rsi[^\d]{0,6}(\d{1,2})/i);
  if (rsi != null) {
    if (/大于|高于|>|以上/.test(text)) filters.rsiMin = rsi;
    else filters.rsiMax = rsi;
  }
  const pe = grab(/pe[^\d]{0,6}(\d{1,3})/i);
  if (pe != null) filters.peMax = pe;
  const pctMin = grab(/(?:涨幅|涨)[^\d-]{0,6}(\d{1,2})\s*%?/);
  if (pctMin != null && !/跌/.test(text)) filters.pctMin = pctMin;
  if (/站上\s*(?:ma)?\s*20|above\s*ma20/i.test(text)) filters.aboveMa20 = true;
  if (/站上\s*(?:ma)?\s*60|above\s*ma60/i.test(text)) filters.aboveMa60 = true;
  return filters;
}

export async function handleChat({ messages = [], activeSymbol = '' } = {}) {
  const history = messages
    .filter((m) => m && (m.role === 'user' || m.role === 'assistant') && typeof m.content === 'string')
    .slice(-10)
    .map((m) => ({ role: m.role, content: m.content.slice(0, 4000) }));
  const last = [...history].reverse().find((m) => m.role === 'user')?.content || '';
  const intent = detectIntent(last);
  const symbols = extractSymbols(last, activeSymbol);
  const base = { intent, symbols, llm: llmStatus() };

  try {
    if (intent === 'help') return { ...base, answer: HELP };

    if (intent === 'portfolio') {
      const portfolio = await loadPortfolio();
      const diagnosis = await diagnosePortfolio(portfolio.holdings);
      if (diagnosis.empty) {
        return { ...base, answer: '你的持仓列表还是空的。请在「组合」页面录入代码、股数和成本价，之后我就能生成集中度、盈亏与波动诊断。' };
      }
      const { text, usedLlm } = await withLlm(
        {
          totals: diagnosis.totals,
          risk: diagnosis.risk,
          holdings: diagnosis.rows.map((r) => ({
            name: r.name,
            symbol: r.symbol,
            weight: r.weight,
            pnlPct: r.pnlPct,
            currency: r.currency,
          })),
          computedNotes: diagnosis.notes,
        },
        '请基于这些数据写一份组合诊断：先概括总市值与盈亏，再分析集中度与市场暴露，然后指出 2-3 个最需要关注的风险点，最后给出可执行的研究动作（例如复核逻辑、压力测试），不要给出具体买卖指令。',
        diagnosis.narrative,
      );
      return { ...base, answer: text, meta: { data: diagnosis, usedLlm } };
    }

    if (intent === 'report') {
      const report = await buildDailyReport();
      return { ...base, answer: report.markdown, meta: { report: { date: report.date, pushedAt: report.pushedAt } } };
    }

    if (intent === 'backtest') {
      if (!symbols.length) {
        return { ...base, answer: '请告诉我要回测的标的，例如：「回测 600519 均线 20/60」或「回测 AAPL 突破 20 日」。' };
      }
      const params = {};
      const pair = last.match(/(\d{1,3})\s*[\/,，]\s*(\d{1,3})/);
      let strategy = 'ma_cross';
      if (/rsi|超买|超卖/i.test(last)) strategy = 'rsi_reversion';
      else if (/突破|breakout/i.test(last)) strategy = 'breakout';
      else if (/动量|momentum/i.test(last)) strategy = 'momentum';
      if (pair) {
        params.fast = Number(pair[1]);
        params.slow = Number(pair[2]);
      } else if (strategy === 'ma_cross') {
        params.fast = 20;
        params.slow = 60;
      }
      const result = await backtestSymbol({ symbol: symbols[0], strategy, params, range: '3y' });
      const s = result.stats;
      const answer = [
        `### 回测结果 · ${result.name || symbolLabel(result.symbol)}（${result.symbol}）`,
        `策略：\`${strategy}\` ${Object.keys(params).length ? JSON.stringify(params) : ''} · 区间：近 3 年（${result.bars} 个交易日）`,
        `- 累计收益 **${s.totalReturn}%** · 年化 ${s.annualized}% · 基准（买入持有）${s.benchmarkReturn}%`,
        `- 最大回撤 ${s.maxDrawdown}% · 夏普 ${s.sharpe ?? '—'} · 持仓时间占比 ${s.exposure}%`,
        `- 交易次数 ${s.trades} · 胜率 ${s.winRate ?? '—'}%`,
        '',
        '> 回测未考虑滑点、税费与分红再投资，历史表现不代表未来。仅供研究参考，不构成投资建议。',
      ].join('\n');
      return { ...base, answer, meta: { backtest: result } };
    }

    if (intent === 'screener') {
      const portfolio = await loadPortfolio();
      const filters = screenerFilters(last);
      const result = await runScreener({ symbols: portfolio.watchlist, filters });
      const lines = [
        `### 自选股筛选`,
        result.narrative,
        ...result.rows.slice(0, 8).map(
          (r) =>
            `- **${r.name}**（${r.symbol}） ${r.pct >= 0 ? '+' : ''}${r.pct}% · RSI ${r.rsi14 ?? '—'} · 20日 ${r.ret20 >= 0 ? '+' : ''}${r.ret20}% · ${r.trend}`,
        ),
        '',
        '> 筛选结果基于技术指标，仅供研究参考，不构成投资建议。',
      ];
      return { ...base, answer: lines.join('\n'), meta: { screener: result } };
    }

    if (intent === 'compare') {
      if (symbols.length < 2) {
        return { ...base, answer: '请给出两个标的，例如：「对比 600519 和 NVDA」。' };
      }
      const result = await compareSymbols(symbols, '6m');
      return { ...base, answer: result.narrative, meta: { compare: result } };
    }

    if (intent === 'analyze' || intent === 'quote') {
      if (!symbols.length) {
        return { ...base, answer: '请告诉我要看的标的代码或名称，例如：「分析 600519」「NVDA 怎么样」。' };
      }
      const sym = symbols[0];
      if (intent === 'quote') {
        const q = await getQuote(sym);
        if (!q) return { ...base, answer: `没有查到 ${sym} 的行情，请确认代码是否正确（A 股 6 位、港股 5 位、美股直接输入代码）。` };
        return { ...base, answer: quoteAnswer(q), meta: { quote: q } };
      }
      const result = await analyzeSymbol(sym, '1y');
      const { text, usedLlm } = await withLlm(
        {
          quote: {
            name: result.quote.name,
            code: result.quote.code,
            price: result.quote.price,
            pct: result.quote.pct,
            currency: result.quote.currency,
            amount: result.quote.amount,
            pe: result.quote.pe,
            totalCap: result.quote.totalCap,
          },
          technical: result.snapshot,
          signals: result.signals,
        },
        '请写一段技术面解读：先用一句话概括当前状态；再分「趋势」「动能」「风险」三点说明；补充关键支撑与阻力；不要编造数据，不要给出买卖指令。',
        result.narrative,
      );
      return { ...base, answer: text, meta: { analysis: result, usedLlm } };
    }

    // general
    if (llmStatus().enabled) {
      const answer = await llmChat(
        [{ role: 'system', content: `${SYSTEM_PROMPT}\n今天是 ${new Date().toISOString().slice(0, 10)}。若问题涉及具体标的价格，请提醒用户提供代码以便查询实时数据。` }, ...history],
        { temperature: 0.5, maxTokens: 900 },
      );
      if (answer) return { ...base, answer };
    }
    return {
      ...base,
      answer: [
        '这个问题需要更具体的范围，我先说明目前能直接基于数据回答的能力：',
        '',
        HELP,
      ].join('\n'),
    };
  } catch (err) {
    console.warn('[agent] failed:', err.message);
    return {
      ...base,
      answer: `处理时遇到问题：${err.message}。可以换个说法，或直接提供标的代码后重试。`,
    };
  }
}

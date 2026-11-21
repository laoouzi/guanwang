import fs from 'node:fs/promises';
import http from 'node:http';
import path from 'node:path';
import { handleChat } from './agent.js';
import { analyzeSymbol, backtestSymbol, compareSymbols, diagnosePortfolio, runScreener } from './analysis.js';
import { llmStatus } from './llm.js';
import { barsForRange, getKlines, getQuotes, searchSymbols } from './providers.js';
import { buildDailyReport, pushReport } from './report.js';
import { loadPortfolio, savePortfolio } from './store.js';
import { PUBLIC_DIR, cnNow, ensureDataDir } from './util.js';

const PORT = Number(process.env.PORT || 8787);
const ACCESS_TOKEN = process.env.ACCESS_TOKEN || '';
const REPORT_TIME = process.env.REPORT_TIME || '08:30';
const WEEKDAYS_ONLY = process.env.REPORT_WEEKDAYS_ONLY !== '0';
let lastReportDay = null;

const MIME = {
  '.html': 'text/html; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8',
  '.json': 'application/json; charset=utf-8',
  '.svg': 'image/svg+xml',
  '.png': 'image/png',
  '.ico': 'image/x-icon',
  '.woff2': 'font/woff2',
};

function json(res, status, payload) {
  const body = JSON.stringify(payload);
  res.writeHead(status, {
    'Content-Type': 'application/json; charset=utf-8',
    'Cache-Control': 'no-store',
    'Content-Length': Buffer.byteLength(body),
  });
  res.end(body);
}

async function readBody(req) {
  const chunks = [];
  let size = 0;
  for await (const chunk of req) {
    size += chunk.length;
    if (size > 1024 * 1024) throw new Error('请求体过大');
    chunks.push(chunk);
  }
  if (!chunks.length) return {};
  try {
    return JSON.parse(Buffer.concat(chunks).toString('utf-8'));
  } catch {
    throw new Error('JSON 格式错误');
  }
}

function authorized(req, url) {
  if (!ACCESS_TOKEN) return true;
  const token =
    req.headers['x-access-token'] ||
    url.searchParams.get('token') ||
    (req.headers.cookie || '').match(/(?:^|;\s*)aegis_token=([^;]+)/)?.[1];
  return token === ACCESS_TOKEN;
}

function parseSymbols(value, max = 30) {
  return String(value || '')
    .split(',')
    .map((s) => s.trim())
    .filter(Boolean)
    .slice(0, max);
}

async function serveStatic(req, res, pathname) {
  const relative = pathname === '/' ? 'index.html' : decodeURIComponent(pathname).replace(/^\/+/, '');
  const filePath = path.resolve(PUBLIC_DIR, relative);
  if (!filePath.startsWith(PUBLIC_DIR + path.sep) && filePath !== path.join(PUBLIC_DIR, 'index.html')) {
    json(res, 403, { error: 'forbidden' });
    return;
  }
  try {
    const stat = await fs.stat(filePath);
    if (!stat.isFile()) throw new Error('not file');
    const data = await fs.readFile(filePath);
    res.writeHead(200, {
      'Content-Type': MIME[path.extname(filePath)] || 'application/octet-stream',
      'Cache-Control': 'no-store',
      'Content-Length': data.length,
    });
    res.end(data);
  } catch {
    const index = await fs.readFile(path.join(PUBLIC_DIR, 'index.html'));
    res.writeHead(200, { 'Content-Type': MIME['.html'], 'Cache-Control': 'no-cache' });
    res.end(index);
  }
}

async function handleApi(req, res, url) {
  const { pathname, searchParams } = url;
  const method = req.method || 'GET';

  if (pathname === '/api/health') {
    return json(res, 200, { ok: true, time: cnNow(), uptime: process.uptime() });
  }
  if (!authorized(req, url)) {
    return json(res, 401, { error: '需要访问令牌', needToken: true });
  }

  if (pathname === '/api/status' && method === 'GET') {
    return json(res, 200, {
      llm: llmStatus(),
      time: cnNow(),
      scheduler: { time: REPORT_TIME, weekdaysOnly: WEEKDAYS_ONLY, lastReportDay },
      auth: Boolean(ACCESS_TOKEN),
      sources: ['腾讯行情 / 腾讯 K 线', '新浪汇率', '腾讯智能搜索'],
    });
  }

  if (pathname === '/api/search' && method === 'GET') {
    const results = await searchSymbols(searchParams.get('q') || '');
    return json(res, 200, { results });
  }

  if (pathname === '/api/quotes' && method === 'GET') {
    const symbols = parseSymbols(searchParams.get('symbols'));
    if (!symbols.length) return json(res, 400, { error: '缺少 symbols 参数' });
    return json(res, 200, { quotes: await getQuotes(symbols) });
  }

  if (pathname === '/api/kline' && method === 'GET') {
    const symbol = searchParams.get('symbol');
    if (!symbol) return json(res, 400, { error: '缺少 symbol 参数' });
    const range = searchParams.get('range') || '1y';
    const count = Number(searchParams.get('count')) || barsForRange(range);
    const bars = await getKlines(symbol, count);
    return json(res, 200, { symbol, range, bars });
  }

  if (pathname === '/api/analysis' && method === 'GET') {
    const symbol = searchParams.get('symbol');
    if (!symbol) return json(res, 400, { error: '缺少 symbol 参数' });
    const data = await analyzeSymbol(symbol, searchParams.get('range') || '1y');
    return json(res, 200, data);
  }

  if (pathname === '/api/indexes' && method === 'GET') {
    const list = ['sh000001', 'sz399001', 'sz399006', 'hkHSI', 'us.DJI', 'us.IXIC', 'us.INX'];
    return json(res, 200, { quotes: await getQuotes(list) });
  }

  if (pathname === '/api/watchlist' && method === 'GET') {
    const portfolio = await loadPortfolio();
    return json(res, 200, { quotes: await getQuotes(portfolio.watchlist), watchlist: portfolio.watchlist });
  }

  if (pathname === '/api/portfolio' && method === 'GET') {
    return json(res, 200, await loadPortfolio());
  }
  if (pathname === '/api/portfolio' && (method === 'PUT' || method === 'POST')) {
    const body = await readBody(req);
    return json(res, 200, await savePortfolio(body));
  }

  if (pathname === '/api/portfolio/diagnose' && method === 'POST') {
    const body = await readBody(req);
    let holdings = body.holdings;
    if (!Array.isArray(holdings)) holdings = (await loadPortfolio()).holdings;
    return json(res, 200, await diagnosePortfolio(holdings));
  }

  if (pathname === '/api/chat' && method === 'POST') {
    const body = await readBody(req);
    const messages = Array.isArray(body.messages) ? body.messages.slice(-12) : [];
    return json(res, 200, await handleChat({ messages, activeSymbol: body.activeSymbol || '' }));
  }

  if (pathname === '/api/compare' && method === 'GET') {
    const symbols = parseSymbols(searchParams.get('symbols'));
    return json(res, 200, await compareSymbols(symbols, searchParams.get('range') || '6m'));
  }

  if (pathname === '/api/screener' && method === 'POST') {
    const body = await readBody(req);
    const portfolio = await loadPortfolio();
    const symbols = Array.isArray(body.symbols) && body.symbols.length ? body.symbols : portfolio.watchlist;
    return json(res, 200, await runScreener({ symbols, filters: body.filters || {} }));
  }

  if (pathname === '/api/backtest' && method === 'POST') {
    const body = await readBody(req);
    if (!body.symbol) return json(res, 400, { error: '缺少 symbol' });
    return json(res, 200, await backtestSymbol(body));
  }

  if (pathname === '/api/report/daily' && method === 'GET') {
    const refresh = searchParams.get('refresh') === '1';
    return json(res, 200, await buildDailyReport({ refresh }));
  }

  if (pathname === '/api/report/push' && method === 'POST') {
    const report = await buildDailyReport({ refresh: true });
    const result = await pushReport(report);
    return json(res, 200, { ok: true, result, date: report.date });
  }

  return json(res, 404, { error: '接口不存在' });
}

const server = http.createServer(async (req, res) => {
  const url = new URL(req.url || '/', `http://${req.headers.host || 'localhost'}`);
  try {
    if (url.pathname.startsWith('/api/')) {
      await handleApi(req, res, url);
    } else {
      await serveStatic(req, res, url.pathname);
    }
  } catch (err) {
    if (!res.headersSent) json(res, 400, { error: err.message || '请求失败' });
    else res.end();
  }
});

async function schedulerTick() {
  const now = cnNow();
  if (now.time !== REPORT_TIME || lastReportDay === now.date) return;
  if (WEEKDAYS_ONLY && (now.weekday === 0 || now.weekday === 6)) return;
  lastReportDay = now.date;
  console.log(`[scheduler] generating daily report for ${now.date}`);
  try {
    const report = await buildDailyReport({ refresh: true });
    if (process.env.LARK_WEBHOOK || process.env.FEISHU_WEBHOOK) {
      await pushReport(report);
      console.log('[scheduler] report pushed');
    }
  } catch (err) {
    console.error('[scheduler] failed:', err.message);
  }
}

await ensureDataDir();
server.listen(PORT, '0.0.0.0', () => {
  const llm = llmStatus();
  console.log(`Aegis invest agent listening on http://0.0.0.0:${PORT}`);
  console.log(`LLM: ${llm.enabled ? `${llm.provider}/${llm.model}` : '未配置（使用内置规则引擎）'}`);
  console.log(`Daily report: ${REPORT_TIME} Asia/Shanghai${WEEKDAYS_ONLY ? ' (weekdays)' : ''}`);
});
setInterval(schedulerTick, 30 * 1000);

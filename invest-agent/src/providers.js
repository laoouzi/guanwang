import { fetchJson, fetchText, memo, num, round } from './util.js';

const QUOTE_HEADERS = { Referer: 'https://gu.qq.com/' };

const US_INDEX = {
  SPX: 'us.INX',
  'S&P500': 'us.INX',
  'S&P': 'us.INX',
  DJI: 'us.DJI',
  IXIC: 'us.IXIC',
  NDX: 'us.NDX',
};

const usKlineCache = new Map();

export function marketOf(symbol) {
  if (/^(sh|sz|bj)/.test(symbol)) return 'cn';
  if (symbol.startsWith('hk')) return 'hk';
  return 'us';
}

const INDEX_NAMES = {
  sh000001: '上证指数',
  sz399001: '深证成指',
  sz399006: '创业板指',
  sh000300: '沪深300',
  hkHSI: '恒生指数',
  hkHSCEI: '恒生国企指数',
  'us.DJI': '道琼斯工业指数',
  'us.IXIC': '纳斯达克综合指数',
  'us.INX': '标普500指数',
};

export function normalizeSymbol(input) {
  if (!input) return null;
  let s = String(input).trim().replace(/\s+/g, '');
  if (!s) return null;
  const lower = s.toLowerCase();

  if (/^(sh|sz|bj)\d{6}$/.test(lower)) return lower;
  if (/^hk[0-9a-z]+$/i.test(s)) return 'hk' + s.slice(2).toUpperCase().replace(/^HSI$/, 'HSI');

  if (/^us/i.test(s)) {
    const raw = s.slice(2).toUpperCase();
    if (US_INDEX[raw]) return US_INDEX[raw];
    return 'us' + raw;
  }
  if (/^\d{6}$/.test(s)) {
    if (/^[569]/.test(s)) return 'sh' + s;
    if (/^[0123]/.test(s)) return 'sz' + s;
    if (/^[48]/.test(s)) return 'bj' + s;
    return 'sh' + s;
  }
  if (/^\d{5}$/.test(s)) return 'hk' + s;
  if (/^[A-Za-z][A-Za-z0-9.\-]{0,11}$/.test(s)) {
    const up = s.toUpperCase();
    if (US_INDEX[up]) return US_INDEX[up];
    return 'us' + up;
  }
  return null;
}

export function symbolLabel(symbol) {
  return INDEX_NAMES[symbol] || symbol;
}

function decodeUnicode(str) {
  return str.replace(/\\u([0-9a-fA-F]{4})/g, (_, hex) => String.fromCharCode(parseInt(hex, 16)));
}

export async function searchSymbols(query) {
  const q = String(query || '').trim();
  if (!q) return [];
  const key = 'search:' + q.toLowerCase();
  return memo(key, 10 * 60 * 1000, async () => {
    try {
      const text = await fetchText(
        'https://smartbox.gtimg.cn/s3/?q=' + encodeURIComponent(q) + '&t=all',
        { encoding: 'gbk', timeout: 8000, retries: 0 },
      );
      const match = text.match(/v_hint="([^"]*)"/);
      if (!match || !match[1] || match[1] === 'N') return [];
      const typeLabel = { 'GP-A': 'A股', GP: '股票', ZS: '指数', QZ: '权证', 'GP-B': 'B股' };
      return match[1]
        .split('^')
        .map((item) => {
          const f = item.split('~');
          if (f.length < 3) return null;
          const market = f[0];
          const code = f[1];
          if (!['sh', 'sz', 'bj', 'hk', 'us'].includes(market)) return null;
          return {
            market,
            code,
            name: decodeUnicode(f[2]),
            pinyin: f[3] || '',
            type: f[4] || '',
            typeLabel: typeLabel[f[4]] || '标的',
            symbol: market === 'us' ? 'us' + code.toUpperCase() : market + code,
          };
        })
        .filter(Boolean)
        .slice(0, 10);
    } catch {
      return [];
    }
  });
}

function parseQuote(symbol, raw) {
  const p = raw.split('~');
  if (p.length < 10) return null;
  const market = marketOf(symbol);
  const price = num(p[3]);
  const prevClose = num(p[4]);
  if (!Number.isFinite(price) || price <= 0) return null;

  const change = num(p[31], prevClose ? price - prevClose : 0);
  const pct = num(p[32], prevClose ? ((price - prevClose) / prevClose) * 100 : 0);
  const isCn = market === 'cn';
  const currency = isCn ? 'CNY' : p[35] || (market === 'hk' ? 'HKD' : 'USD');
  const amountRaw = num(p[37], 0);
  const volumeRaw = num(p[6], 0);

  return {
    symbol,
    name: p[1] || symbolLabel(symbol),
    code: p[2] || symbol.replace(/^(sh|sz|bj|hk|us)/i, ''),
    market,
    price: round(price, 4),
    prevClose: round(prevClose, 4),
    open: round(num(p[5]), 4),
    high: round(num(p[33]), 4),
    low: round(num(p[34]), 4),
    change: round(change, 4),
    pct: round(pct, 2),
    volume: isCn ? volumeRaw * 100 : volumeRaw,
    amount: isCn ? amountRaw * 10000 : amountRaw,
    turnover: num(p[38]),
    pe: num(p[39]),
    amplitude: num(p[43]),
    floatCap: num(p[44]),
    totalCap: num(p[45]),
    high52: isCn ? num(p[67]) : num(p[48]),
    low52: isCn ? num(p[68]) : num(p[49]),
    currency,
    time: p[30] || '',
    isIndex: /指数|Index/i.test(p[1] || '') || /^us\./.test(symbol) || /^(sh000|sz399|hkHSI)/.test(symbol),
  };
}

export async function getQuotes(symbols) {
  const list = [...new Set((symbols || []).map(normalizeSymbol).filter(Boolean))];
  if (!list.length) return [];
  const key = 'quotes:' + list.slice().sort().join(',');
  return memo(key, 12 * 1000, async () => {
    const text = await fetchText('https://qt.gtimg.cn/q=' + list.join(','), {
      encoding: 'gbk',
      timeout: 10000,
      headers: QUOTE_HEADERS,
      retries: 1,
    });
    const found = new Map();
    for (const m of text.matchAll(/v_([A-Za-z0-9_.]+)="([^"]*)"/g)) {
      const quote = parseQuote(m[1], m[2]);
      if (quote) found.set(m[1].toLowerCase(), quote);
    }
    return list
      .map((sym) => found.get(sym.toLowerCase()) || null)
      .filter(Boolean);
  });
}

export async function getQuote(symbol) {
  const [quote] = await getQuotes([symbol]);
  return quote || null;
}

async function resolveUsKlineCode(symbol) {
  const raw = symbol.slice(2);
  if (raw.includes('.')) return symbol;
  const indexed = US_INDEX[raw.toUpperCase()];
  if (indexed) return indexed;
  if (usKlineCache.has(raw)) return usKlineCache.get(raw);
  try {
    const hits = await searchSymbols(raw);
    const hit = hits.find((h) => h.market === 'us');
    if (hit?.code) {
      const code = 'us' + hit.code.toUpperCase();
      usKlineCache.set(raw, code);
      return code;
    }
  } catch {
    /* ignore */
  }
  const guess = 'us' + raw + '.OQ';
  usKlineCache.set(raw, guess);
  return guess;
}

const RANGE_BARS = { '1m': 22, '3m': 66, '6m': 130, '1y': 250, '3y': 750, '5y': 1250, max: 1500 };

export function barsForRange(range) {
  return RANGE_BARS[String(range || '1y')] || RANGE_BARS['1y'];
}

const KLINE_HOSTS = ['https://ifzq.gtimg.cn', 'https://web.ifzq.gtimg.cn'];

async function fetchKline(pathAndQuery) {
  let lastErr;
  for (const host of KLINE_HOSTS) {
    try {
      return await fetchJson(`${host}${pathAndQuery}`, {
        timeout: 12000,
        retries: 0,
        headers: { Referer: 'https://gu.qq.com/' },
      });
    } catch (err) {
      lastErr = err;
    }
  }
  throw lastErr;
}

export async function getKlines(symbol, count = 250) {
  const sym = normalizeSymbol(symbol);
  if (!sym) return [];
  const bars = Math.min(Math.max(Number(count) || 250, 10), 1500);
  const key = `kline:${sym}:${bars}`;
  return memo(key, 5 * 60 * 1000, async () => {
    const market = marketOf(sym);
    let query;
    let dataKey = sym;
    if (market === 'us') {
      const klineCode = await resolveUsKlineCode(sym);
      dataKey = klineCode;
      query = `/appstock/app/usfqkline/get?param=${klineCode},day,,,${bars},qfq`;
    } else if (market === 'hk') {
      query = `/appstock/app/hkfqkline/get?param=${sym},day,,,${bars},qfq`;
    } else {
      query = `/appstock/app/fqkline/get?param=${sym},day,,,${bars},qfq`;
    }
    const json = await fetchKline(query);
    const node = json?.data?.[dataKey];
    const rows = node?.qfqday || node?.day || [];
    return rows
      .map((r) => ({
        date: r[0],
        open: num(r[1]),
        close: num(r[2]),
        high: num(r[3]),
        low: num(r[4]),
        volume: num(r[5], 0),
      }))
      .filter((b) => Number.isFinite(b.close) && b.close > 0);
  });
}

/** Foreign currency -> CNY. */
export async function getFxRates() {
  return memo('fx', 60 * 60 * 1000, async () => {
    const fallback = { CNY: 1, HKD: 0.91, USD: 7.1, rateTime: '' };
    try {
      const text = await fetchText(
        'https://hq.sinajs.cn/list=fx_susdcny,fx_shkdcny',
        {
          encoding: 'gbk',
          timeout: 8000,
          retries: 0,
          headers: { Referer: 'https://finance.sina.com.cn' },
        },
      );
      const pick = (name) => {
        const m = text.match(new RegExp(`hq_str_${name}="([^"]*)"`));
        if (!m) return null;
        const rate = num(m[1].split(',')[1]);
        return Number.isFinite(rate) && rate > 0 ? rate : null;
      };
      const usd = pick('fx_susdcny');
      const hkd = pick('fx_shkdcny');
      return {
        CNY: 1,
        HKD: hkd || fallback.HKD,
        USD: usd || fallback.USD,
        rateTime: new Date().toISOString(),
      };
    } catch {
      return fallback;
    }
  });
}

export async function toCny(amount, currency) {
  const fx = await getFxRates();
  return (Number(amount) || 0) * (fx[currency] || 1);
}

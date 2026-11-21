import path from 'node:path';
import { DATA_DIR, ensureDataDir, readJson, writeJson } from './util.js';
import { normalizeSymbol } from './providers.js';

const PORTFOLIO_FILE = path.join(DATA_DIR, 'portfolio.json');

export const DEFAULT_WATCHLIST = ['sh600519', 'sz300750', 'sh601318', 'hk00700', 'usAAPL', 'usNVDA', 'us.INX'];

const DEFAULTS = {
  holdings: [],
  watchlist: DEFAULT_WATCHLIST,
  settings: { benchmark: 'sh000001', currency: 'CNY' },
};

export async function loadPortfolio() {
  await ensureDataDir();
  const data = await readJson(PORTFOLIO_FILE, DEFAULTS);
  return {
    holdings: Array.isArray(data.holdings) ? data.holdings : [],
    watchlist: Array.isArray(data.watchlist) && data.watchlist.length ? data.watchlist : DEFAULT_WATCHLIST,
    settings: { ...DEFAULTS.settings, ...(data.settings || {}) },
  };
}

function sanitizeHolding(h) {
  const symbol = normalizeSymbol(h?.symbol);
  if (!symbol) return null;
  const shares = Number(h.shares);
  const cost = Number(h.cost);
  if (!Number.isFinite(shares) || shares <= 0) return null;
  if (!Number.isFinite(cost) || cost < 0) return null;
  return {
    symbol,
    name: String(h.name || '').slice(0, 40),
    shares: Math.round(shares * 10000) / 10000,
    cost: Math.round(cost * 10000) / 10000,
  };
}

export async function savePortfolio(input) {
  const current = await loadPortfolio();
  const holdings = Array.isArray(input?.holdings)
    ? input.holdings.map(sanitizeHolding).filter(Boolean).slice(0, 50)
    : current.holdings;
  const watchlist = Array.isArray(input?.watchlist)
    ? [...new Set(input.watchlist.map(normalizeSymbol).filter(Boolean))].slice(0, 40)
    : current.watchlist;
  const next = {
    holdings,
    watchlist,
    settings: { ...current.settings, ...(input?.settings || {}) },
  };
  await writeJson(PORTFOLIO_FILE, next);
  return next;
}

export function reportFile(date) {
  return path.join(DATA_DIR, 'reports', `${date}.json`);
}

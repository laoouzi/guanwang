export function sma(values, period) {
  const out = new Array(values.length).fill(null);
  let sum = 0;
  for (let i = 0; i < values.length; i++) {
    sum += values[i];
    if (i >= period) sum -= values[i - period];
    if (i >= period - 1) out[i] = sum / period;
  }
  return out;
}

export function ema(values, period) {
  const out = new Array(values.length).fill(null);
  if (!values.length) return out;
  const k = 2 / (period + 1);
  out[0] = values[0];
  for (let i = 1; i < values.length; i++) out[i] = values[i] * k + out[i - 1] * (1 - k);
  return out;
}

export function rsi(values, period = 14) {
  const out = new Array(values.length).fill(null);
  if (values.length <= period) return out;
  let gain = 0;
  let loss = 0;
  for (let i = 1; i <= period; i++) {
    const d = values[i] - values[i - 1];
    if (d >= 0) gain += d;
    else loss -= d;
  }
  let avgGain = gain / period;
  let avgLoss = loss / period;
  const calc = () => (avgLoss === 0 ? 100 : 100 - 100 / (1 + avgGain / avgLoss));
  out[period] = calc();
  for (let i = period + 1; i < values.length; i++) {
    const d = values[i] - values[i - 1];
    avgGain = (avgGain * (period - 1) + Math.max(d, 0)) / period;
    avgLoss = (avgLoss * (period - 1) + Math.max(-d, 0)) / period;
    out[i] = calc();
  }
  return out;
}

export function macd(values, fast = 12, slow = 26, signal = 9) {
  const ef = ema(values, fast);
  const es = ema(values, slow);
  const dif = values.map((_, i) => (ef[i] != null && es[i] != null ? ef[i] - es[i] : null));
  const start = dif.findIndex((v) => v != null);
  const dea = new Array(values.length).fill(null);
  if (start >= 0) {
    const slice = dif.slice(start);
    const e = ema(slice, signal);
    e.forEach((v, i) => {
      if (v != null) dea[start + i] = v;
    });
  }
  const hist = values.map((_, i) => (dif[i] != null && dea[i] != null ? (dif[i] - dea[i]) * 2 : null));
  return { dif, dea, hist };
}

export function boll(values, period = 20, mult = 2) {
  const mid = sma(values, period);
  const up = new Array(values.length).fill(null);
  const low = new Array(values.length).fill(null);
  for (let i = period - 1; i < values.length; i++) {
    const window = values.slice(i - period + 1, i + 1);
    const mean = mid[i];
    const variance = window.reduce((acc, v) => acc + (v - mean) ** 2, 0) / period;
    const sd = Math.sqrt(variance);
    up[i] = mean + mult * sd;
    low[i] = mean - mult * sd;
  }
  return { up, mid, low };
}

export function atr(bars, period = 14) {
  if (!bars.length) return new Array(0).fill(null);
  const trs = bars.map((b, i) => {
    if (i === 0) return b.high - b.low;
    const prev = bars[i - 1].close;
    return Math.max(b.high - b.low, Math.abs(b.high - prev), Math.abs(b.low - prev));
  });
  return sma(trs, period);
}

export function dailyReturns(values) {
  const out = [];
  for (let i = 1; i < values.length; i++) {
    if (values[i - 1]) out.push(values[i] / values[i - 1] - 1);
  }
  return out;
}

export function std(values) {
  if (values.length < 2) return 0;
  const mean = values.reduce((a, b) => a + b, 0) / values.length;
  const variance = values.reduce((a, b) => a + (b - mean) ** 2, 0) / (values.length - 1);
  return Math.sqrt(variance);
}

export function mean(values) {
  if (!values.length) return 0;
  return values.reduce((a, b) => a + b, 0) / values.length;
}

export function maxDrawdown(values) {
  let peak = -Infinity;
  let mdd = 0;
  for (const v of values) {
    if (v > peak) peak = v;
    if (peak > 0) mdd = Math.min(mdd, v / peak - 1);
  }
  return mdd;
}

export function annualizedVol(returns) {
  return std(returns) * Math.sqrt(252);
}

export function sharpe(returns, riskFree = 0) {
  const sd = std(returns);
  if (!sd) return null;
  return ((mean(returns) - riskFree / 252) / sd) * Math.sqrt(252);
}

export function correlation(a, b) {
  const n = Math.min(a.length, b.length);
  if (n < 3) return null;
  const x = a.slice(-n);
  const y = b.slice(-n);
  const mx = mean(x);
  const my = mean(y);
  let cov = 0;
  let vx = 0;
  let vy = 0;
  for (let i = 0; i < n; i++) {
    cov += (x[i] - mx) * (y[i] - my);
    vx += (x[i] - mx) ** 2;
    vy += (y[i] - my) ** 2;
  }
  if (!vx || !vy) return null;
  return cov / Math.sqrt(vx * vy);
}

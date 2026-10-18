// Teknik göstergeler (saf fonksiyonlar). rsi-divergence-signals/js/indicators.js'ten uyarlandı.
// Tüm fonksiyonlar girdiyle aynı uzunlukta dizi döndürür; ısınma indekslerinde değer null'dır.

/** Wilder yumuşatması (RMA): ilk değer SMA, sonrası (önceki × (n − 1) + değer) / n. */
export function rma(values, period) {
  const out = new Array(values.length).fill(null);
  if (!(period > 0) || values.length < period) return out;
  let prev = 0;
  for (let i = 0; i < period; i++) prev += values[i];
  prev /= period;
  out[period - 1] = prev;
  for (let i = period; i < values.length; i++) {
    prev = (prev * (period - 1) + values[i]) / period;
    out[i] = prev;
  }
  return out;
}

const rsiFromAverages = (g, l) => (l === 0 ? (g === 0 ? 50 : 100) : 100 - 100 / (1 + g / l));

/** RSI — Wilder yöntemi (TradingView ta.rsi ile aynı). */
export function rsi(closes, period = 14) {
  const n = closes.length;
  const out = new Array(n).fill(null);
  if (!(period > 0) || n <= period) return out;
  let gain = 0, loss = 0;
  for (let i = 1; i <= period; i++) {
    const ch = closes[i] - closes[i - 1];
    if (ch > 0) gain += ch; else loss -= ch;
  }
  let avgGain = gain / period, avgLoss = loss / period;
  out[period] = rsiFromAverages(avgGain, avgLoss);
  for (let i = period + 1; i < n; i++) {
    const ch = closes[i] - closes[i - 1];
    avgGain = (avgGain * (period - 1) + (ch > 0 ? ch : 0)) / period;
    avgLoss = (avgLoss * (period - 1) + (ch < 0 ? -ch : 0)) / period;
    out[i] = rsiFromAverages(avgGain, avgLoss);
  }
  return out;
}

/** Gerçek aralık: max(H − L, |H − önceki C|, |L − önceki C|). */
export function trueRange(high, low, close) {
  return high.map((h, i) => {
    const hl = h - low[i];
    if (i === 0) return hl;
    const pc = close[i - 1];
    return Math.max(hl, Math.abs(h - pc), Math.abs(low[i] - pc));
  });
}

/** ATR — gerçek aralığın Wilder yumuşatması. */
export function atr(high, low, close, period = 14) {
  return rma(trueRange(high, low, close), period);
}

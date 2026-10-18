// Geçmiş mumlar: OKX /market/history-candles (istek başına 100 mum, `after` imleciyle geriye sayfalanır).
import { okxGet, parseCandle, splitLive } from '../data/okx.js';

const PER = 100;
const cache = new Map(); // "inst|tf|from|to" → mumlar (sayfa açıkken aynı aralık yeniden indirilmez)

/** Aralık için gereken yaklaşık istek sayısı (süre tahmini için). */
export function estimateRequests(tf, startMs, endMs) {
  const bars = Math.max(0, (Math.min(endMs, Date.now()) - startMs) / tf.ms);
  return Math.max(1, Math.ceil(bars / PER));
}

/**
 * [startMs, endMs] aralığındaki kapanmış mumlar, eskiden yeniye.
 * @param opts { signal, onPage({ done, total }) }
 */
export async function getHistory(instId, tf, startMs, endMs, opts = {}) {
  endMs = Math.min(endMs, Date.now());
  const key = `${instId}|${tf.id}|${startMs}|${endMs}`;
  if (cache.has(key)) return cache.get(key);
  const total = estimateRequests(tf, startMs, endMs);
  const out = [];
  let after = endMs + tf.ms; // bu zamandan ESKİ mumlar gelir
  let done = 0;
  for (;;) {
    const data = await okxGet('/api/v5/market/history-candles', { instId, bar: tf.okxBar, limit: PER, after }, { ...opts, lane: 'history' });
    done++;
    opts.onPage?.({ done, total });
    if (!data.length) break;
    for (const a of data) out.push(parseCandle(a));
    const oldest = +data[data.length - 1][0];
    if (oldest <= startMs || data.length < PER) break;
    after = oldest;
  }
  const { candles } = splitLive(out);
  const res = candles.filter((c) => c.time >= startMs && c.time <= endMs);
  cache.set(key, res);
  return res;
}

const fundingCache = new Map();

/**
 * [startMs, endMs] aralığındaki funding ödemeleri, eskiden yeniye: [{ t, rate }].
 * OKX bu uç noktada yalnızca son ~3 ayı tutar; daha eskisi gelmez ve motor o işlemlerde funding'i
 * "uygulanamadı" diye işaretler (engine.js > fundingCovered).
 */
export async function getFundingHistory(instId, startMs, endMs, opts = {}) {
  const key = `${instId}|${startMs}|${endMs}`;
  if (fundingCache.has(key)) return fundingCache.get(key);
  const out = [];
  let after = null;
  for (;;) {
    const data = await okxGet('/api/v5/public/funding-rate-history', { instId, limit: 400, after }, { ...opts, lane: 'other' });
    if (!data.length) break;
    for (const r of data) out.push({ t: +r.fundingTime, rate: +r.realizedRate || +r.fundingRate });
    const oldest = +data[data.length - 1].fundingTime;
    if (oldest <= startMs || String(oldest) === String(after)) break;
    after = oldest;
  }
  const map = new Map(out.map((f) => [f.t, f]));
  const res = [...map.values()].filter((f) => f.t >= startMs && f.t <= endMs && Number.isFinite(f.rate)).sort((a, b) => a.t - b.t);
  fundingCache.set(key, res);
  return res;
}

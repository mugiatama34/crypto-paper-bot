// Tarama akışı: hacme göre coin listesi → her coin için mumlar → moda göre setup'lar (dalga-3 ya da üçgen).
import { getTopCoins, getCandles, TIMEFRAMES, instIdOf } from '../data/okx.js';
import { findWave3Setups, compareSetups, DEFAULTS } from './wave3.js';
import { findTriangles, compareTriangles } from './triangle.js';

const CONCURRENCY = 6;
// Dalga-3 son 300 mumla (canlı ve backtest aynı pencere); üçgenler aylarca sürebildiği için 600 mum indirilir.
export const WAVE_BARS = 300;
export const LONG_BARS = 600;

/** Bir coinin önbellekteki mumlarında seçilen moda göre setup'ları bulur. */
export function detect(r, opts) {
  if (opts.mode === 'triangle') return findTriangles(r.longCandles, opts, r.live);
  return findWave3Setups(r.candles, opts, r.live);
}

export function sortSetups(setups, opts) {
  setups.sort((a, b) => (opts.mode === 'triangle' ? compareTriangles(a, b) : compareSetups(a, b, opts)));
  return setups;
}

/**
 * @param settings  { mode, timeframe, topN, manualCoins: string[]|null, ...ayarlar }
 * @param hooks     { onProgress({done,total,coin}), onRetry, signal }
 * @returns {{ coins, results: {coin, instId, setups, candles, longCandles, live, error}[], setups, at }}
 */
export async function runScan(settings, hooks = {}) {
  const tf = TIMEFRAMES[settings.timeframe] || TIMEFRAMES['4H'];
  const opts = { ...DEFAULTS, ...settings };
  const net = { signal: hooks.signal, onRetry: hooks.onRetry };

  let coins;
  if (settings.manualCoins?.length) {
    coins = settings.manualCoins.map((c) => ({ coin: c, instId: instIdOf(c) }));
  } else {
    coins = await getTopCoins(settings.topN || 50, net);
  }

  const results = new Array(coins.length);
  let done = 0, next = 0;
  hooks.onProgress?.({ done, total: coins.length });
  const worker = async () => {
    while (next < coins.length) {
      const idx = next++;
      const c = coins[idx];
      try {
        const { candles: longCandles, live } = await getCandles(c.instId, tf, LONG_BARS, net);
        const r = { ...c, candles: longCandles.slice(-WAVE_BARS), longCandles, live };
        r.setups = detect(r, opts);
        results[idx] = r;
      } catch (err) {
        if (err.name === 'AbortError') throw err;
        results[idx] = { ...c, candles: [], longCandles: [], live: null, setups: [], error: err.message };
      }
      done++;
      hooks.onProgress?.({ done, total: coins.length, coin: c.coin });
    }
  };
  await Promise.all(Array.from({ length: Math.min(CONCURRENCY, coins.length) }, worker));

  const setups = results.flatMap((r) => r.setups.map((s) => ({ ...s, coin: r.coin, instId: r.instId })));
  sortSetups(setups, opts);
  return { tf, coins, results, setups, at: Date.now() };
}

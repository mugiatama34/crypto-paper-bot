// Dönem evreni (kopya değişikliği, CHANGES.md > 4): coin listesi bugünkü hacimle değil, backtest
// başlangıcından ÖNCEKİ 30 günün hacmiyle seçilir. Bugünkü sıralama, o coinlerin tam da geçmişte iyi
// gittikleri için listede olduğu bir evren verir ve long sonuçlarını şişirir.
// Sınır: aday kümesi bugün listelenen perp'lerdir; o tarihten bu yana kaldırılmış coinler dahil edilemez.
import { getTopCoins } from '../data/okx.js';
import { getHistory } from './history.js';

export const LOOKBACK_DAYS = 30;
export const CANDIDATE_FACTOR = 3;
export const MAX_CANDIDATES = 100;
const DAY = 86400e3;
const DAILY = { id: '1D', okxBar: '1Dutc', ms: DAY };

/** Saf: aday listesi + coin → günlük mumlar ⇒ dönem hacmine göre ilk n (verisi olmayan aday elenir). */
export function rankByPeriodVolume(candidates, dailyByInst, fromMs, n) {
  const start = fromMs - LOOKBACK_DAYS * DAY;
  const scored = [];
  for (const c of candidates) {
    const days = (dailyByInst.get(c.instId) || []).filter((k) => k.time >= start && k.time < fromMs);
    // Dönemin büyük kısmında işlem görmeyen coin o tarihte "ilk n" içinde sayılamaz.
    if (days.length < LOOKBACK_DAYS * 0.8) continue;
    const vol = days.reduce((s, k) => s + (Number.isFinite(k.quoteVol) ? k.quoteVol : k.volume * k.close), 0);
    scored.push({ ...c, periodVolUsd: vol });
  }
  return scored.sort((a, b) => b.periodVolUsd - a.periodVolUsd).slice(0, n);
}

/** Bugünkü ilk (CANDIDATE_FACTOR × n) perp'ten, `fromMs` öncesi 30 günün hacmine göre ilk n. */
export async function getPeriodCoins(n, fromMs, opts = {}) {
  const candidates = await getTopCoins(Math.min(MAX_CANDIDATES, n * CANDIDATE_FACTOR), opts);
  const daily = new Map();
  for (const c of candidates) {
    opts.onCandidate?.(c);
    daily.set(c.instId, await getHistory(c.instId, DAILY, fromMs - LOOKBACK_DAYS * DAY, fromMs - 1, opts));
  }
  return { coins: rankByPeriodVolume(candidates, daily, fromMs, n), candidates: candidates.length };
}

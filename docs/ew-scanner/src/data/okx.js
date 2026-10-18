// OKX Public API istemcisi (API anahtarı gerekmez). rsi-divergence-signals/js/okx.js'ten sadeleştirildi.
//  - Hız sınırı: şerit başına istekler arasında en az RATE_GAP_MS bekler.
//  - Ağ/429/5xx/meşgul hatalarında üstel bekleme ile yeniden dener.
//  - Mumlar eskiden yeniye sıralanır; kapanmamış mum (confirm = 0) ayrıca `live` döner.

const BASE = 'https://www.okx.com';
// Kopya değişikliği (CHANGES.md > 1): veri spot değil USDT lineer PERP'ten gelir — short kurulumu ancak perp'te
// açılabilir ve backtest'in funding'i (history.js) aynı enstrümandan okunur.
export const INST_TYPE = 'SWAP';
export const instIdOf = (coin) => `${coin}-USDT-SWAP`;
const RATE_GAP_MS = { candles: 80, history: 120, other: 120 }; // OKX: candles 40, history-candles 20 istek / 2 sn
const TIMEOUT_MS = 15000;
const MAX_RETRIES = 4;
const RETRYABLE_CODES = new Set(['50001', '50004', '50011', '50013', '50026', '50061']);

// Hacim listesinden çıkarılan sabit değerli (stable) tokenler.
export const STABLES = new Set([
  'USDC', 'USDT', 'DAI', 'TUSD', 'FDUSD', 'PYUSD', 'USDE', 'USDG', 'USDP', 'USD1', 'RLUSD', 'EURC', 'EURT',
  'BUSD', 'GUSD', 'USDD', 'FRAX', 'LUSD', 'SUSD', 'USDS', 'USDJ', 'UST', 'USTC', 'XSGD', 'BRZ', 'AUDF', 'AUDM',
  'BRL1', 'EURI', 'EUROC', 'USDQ', 'USDR', 'USDF', 'USDA',
]);

export const TIMEFRAMES = {
  '15m': { id: '15m', okxBar: '15m', label: '15 dakika', ms: 15 * 60e3 },
  '4H': { id: '4H', okxBar: '4H', label: '4 saat', ms: 4 * 3600e3 },
  '1D': { id: '1D', okxBar: '1Dutc', label: '1 gün', ms: 86400e3 },
};

export class OkxError extends Error {
  constructor(message, info = {}) {
    super(message);
    this.name = 'OkxError';
    this.code = info.code || null;
    this.status = info.status || null;
    this.retryable = !!info.retryable;
  }
}

const abortError = () => Object.assign(new Error('İşlem iptal edildi'), { name: 'AbortError' });

function sleep(ms, signal) {
  return new Promise((resolve, reject) => {
    if (signal?.aborted) return reject(abortError());
    const onAbort = () => { clearTimeout(timer); reject(abortError()); };
    const timer = setTimeout(() => { signal?.removeEventListener('abort', onAbort); resolve(); }, Math.max(0, ms));
    signal?.addEventListener('abort', onAbort, { once: true });
  });
}

const nextSlot = {};
function reserveSlot(lane) {
  const now = Date.now();
  const at = Math.max(now, nextSlot[lane] || 0);
  nextSlot[lane] = at + (RATE_GAP_MS[lane] || 120);
  return at - now;
}

async function fetchJson(url, signal) {
  const ctrl = new AbortController();
  let timedOut = false;
  const timer = setTimeout(() => { timedOut = true; ctrl.abort(); }, TIMEOUT_MS);
  const onAbort = () => ctrl.abort();
  signal?.addEventListener('abort', onAbort, { once: true });
  try {
    let res;
    try {
      res = await fetch(url, { signal: ctrl.signal });
    } catch {
      if (signal?.aborted) throw abortError();
      throw new OkxError(timedOut ? 'İstek zaman aşımına uğradı' : 'OKX\'e ulaşılamadı (internet bağlantısı veya CORS)', { retryable: true });
    }
    if (res.status === 429) throw new OkxError('İstek limiti aşıldı (HTTP 429)', { status: 429, retryable: true });
    if (res.status >= 500) throw new OkxError(`Sunucu hatası (HTTP ${res.status})`, { status: res.status, retryable: true });
    if (res.status === 403) throw new OkxError('Erişim reddedildi (HTTP 403) — bölgesel kısıtlama olabilir', { status: 403 });
    if (!res.ok) throw new OkxError(`Beklenmeyen yanıt (HTTP ${res.status})`, { status: res.status });
    return await res.json();
  } finally {
    clearTimeout(timer);
    signal?.removeEventListener('abort', onAbort);
  }
}

/** OKX'e GET isteği; `data` alanını döndürür. */
export async function okxGet(path, params = {}, { lane = 'other', signal, onRetry } = {}) {
  const qs = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) if (v != null) qs.set(k, String(v));
  const url = `${BASE}${path}${qs.toString() ? '?' + qs : ''}`;
  for (let attempt = 0; ; attempt++) {
    try {
      await sleep(reserveSlot(lane), signal);
      const json = await fetchJson(url, signal);
      if (json?.code === '0') return json.data || [];
      throw new OkxError(`OKX hatası ${json?.code}: ${json?.msg || 'bilinmeyen hata'}`, {
        code: json?.code, retryable: RETRYABLE_CODES.has(json?.code),
      });
    } catch (err) {
      if (err.name === 'AbortError' || !err.retryable || attempt >= MAX_RETRIES) throw err;
      const wait = Math.min(1000 * 2 ** attempt, 10000) + Math.floor(Math.random() * 300);
      onRetry?.({ path, attempt: attempt + 1, wait, error: err });
      await sleep(wait, signal);
    }
  }
}

/** OKX mum dizisi: [ts, o, h, l, c, vol, volCcy, volCcyQuote, confirm] */
export function parseCandle(a) {
  return {
    time: +a[0], open: +a[1], high: +a[2], low: +a[3], close: +a[4], volume: +a[5],
    // volCcyQuote (USDT cinsinden hacim): dönem evreni bununla sıralanır (backtest/universe.js).
    quoteVol: a.length >= 8 ? +a[7] : NaN,
    confirmed: a.length >= 9 ? String(a[8]) === '1' : true,
  };
}

/** Tekilleştirir, eskiden yeniye sıralar ve kapanmış / canlı mumları ayırır. */
export function splitLive(list) {
  const map = new Map();
  for (const c of list) map.set(c.time, c);
  const sorted = [...map.values()].sort((a, b) => a.time - b.time);
  const candles = [];
  let live = null;
  for (const c of sorted) (c.confirmed ? candles.push(c) : (live = c));
  return { candles, live };
}

/** Son `count` mum (en fazla 300 / istek; fazlası `after` ile geriye sayfalanır). */
export async function getCandles(instId, tf, count = 300, opts = {}) {
  const out = [];
  let after = null;
  while (out.length < count) {
    const limit = Math.min(300, count - out.length);
    const data = await okxGet('/api/v5/market/candles', { instId, bar: tf.okxBar, limit, after }, { ...opts, lane: 'candles' });
    if (!data.length) break;
    for (const a of data) out.push(parseCandle(a));
    after = data[data.length - 1][0];
    if (data.length < limit) break;
  }
  return splitLive(out);
}

/**
 * Tüm perp tickerlar → 24 saatlik USDT hacmine göre sıralı USDT lineer perp'ler (stablecoinler hariç).
 * cryptoIds verilirse yalnızca o kümedeki enstrümanlar alınır (tokenize hisse / altın hariç tutmak için).
 * @returns {{instId:string, coin:string, last:number, volUsd:number, change24h:number}[]}
 */
export function rankByVolume(tickers, quote = 'USDT', cryptoIds = null) {
  const out = [];
  for (const t of tickers) {
    const [base, q, kind, extra] = String(t.instId).split('-');
    if (q !== quote || kind !== 'SWAP' || extra || STABLES.has(base)) continue;
    if (cryptoIds && !cryptoIds.has(t.instId)) continue;
    const last = +t.last, open = +t.open24h;
    // Perp'te volCcy24h baz coin cinsindendir (spotun aksine); USDT hacmi = adet × son fiyat.
    const volUsd = +t.volCcy24h * last;
    if (!(last > 0) || !Number.isFinite(volUsd)) continue;
    out.push({ instId: t.instId, coin: base, last, volUsd, change24h: open > 0 ? last / open - 1 : 0 });
  }
  return out.sort((a, b) => b.volUsd - a.volUsd);
}

let cryptoPromise = null;
/** OKX instCategory '1' = kripto; '3' tokenize hisse (xStocks), '4' emtia (XAUT, PAXG). */
export function getCryptoInstIds(opts = {}) {
  if (!cryptoPromise) {
    // Paylaşılan önbellek: çağıranın iptal sinyaline bağlanmaz (iptal edilen bir tarama sonrakini bozmasın).
    // Perp'te de instCategory aynı anlamı taşır; `ctType` lineer (USDT teminatlı) olanları ayırır.
    cryptoPromise = okxGet('/api/v5/public/instruments', { instType: INST_TYPE }, { onRetry: opts.onRetry })
      .then((list) => new Set(list.filter((x) => x.state === 'live' && (x.instCategory ?? '1') === '1' && (x.ctType ?? 'linear') === 'linear').map((x) => x.instId)));
    cryptoPromise.catch(() => { cryptoPromise = null; });
  }
  return cryptoPromise;
}

export async function getTopCoins(n = 50, opts = {}) {
  const [data, crypto] = await Promise.all([
    okxGet('/api/v5/market/tickers', { instType: INST_TYPE }, opts),
    getCryptoInstIds(opts),
  ]);
  return rankByVolume(data, 'USDT', crypto).slice(0, n);
}

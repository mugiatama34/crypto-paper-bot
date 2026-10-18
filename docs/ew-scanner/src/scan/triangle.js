// Üçgen kırılımı: yükselen üçgen (düz tepeler + yükselen dipler → yukarı kırılım) ve
// alçalan üçgen (düz dipler + alçalan tepeler → aşağı kırılım). Saf fonksiyonlar; DOM ve ağ kullanmaz.
// Hedefler: ölçülü hareket (üçgen yüksekliği) ve üçgenden önceki hareketin Fibonacci seviyeleri.
import { zigzag, fillAtr } from './zigzag.js';
import { atr as atrOf } from '../data/indicators.js';

export const TRI_DEFAULTS = {
  sensitivities: [2, 3, 4.5],
  flatAtr: 0.5, // düz kenardaki tepeler (dipler) arası en büyük fark ≤ bu × ATR
  minTops: 2, // düz kenara en az dokunuş
  minBottoms: 2, // eğik kenarda en az pivot
  minWidthBars: 10,
  minHeightAtr: 1.5,
  breakBufAtr: 0.1, // kırılım: kapanış düz kenarın bu × ATR ötesinde
  triStopAtr: 0.25, // stop: eğik kenardaki son pivotun bu × ATR ötesi
  recentBars: 10, // kırılım bu kadar mum içindeyse "Kırıldı", daha eskiyse "Eski"
  nearAtr: 0.5, // fiyat düz kenara bu kadar yakınsa "Kırılıma yakın"
  triTarget: 'mm', // ana hedef: 'mm' ölçülü hareket | 'f382' 'f50' 'f618' 'f786' 'f100' önceki hareketin Fib seviyesi
  triMinRR: 1,
  triType: 'all', // 'all' | 'asc' | 'desc'
  triContext: 'all', // 'all' | 'after-down' | 'after-up'
  maxLookback: 16, // geriye taranacak en fazla pivot
};

export const FIB_RATIOS = [0.382, 0.5, 0.618, 0.786, 1];
const FIB_KEY = { f382: 0.382, f50: 0.5, f618: 0.618, f786: 0.786, f100: 1 };

export const TRI_STATE = {
  forming: 'Oluşuyor',
  near: 'Kırılıma yakın',
  broken: 'Kırıldı',
  old: 'Kırılım eski',
  failed: 'Bozuldu',
};
export const TRI_TYPE = { asc: 'Yükselen üçgen', desc: 'Alçalan üçgen' };
export const CONTEXT = { 'after-down': 'düşüş sonrası', 'after-up': 'yükseliş sonrası' };

/**
 * Pivot dizisinin `end` indeksinde biten en uzun üçgeni bulur.
 * type 'asc': tepeler düz, dipler yükselen. 'desc': dipler düz, tepeler alçalan.
 * @returns {{ start: number, end: number, flat: object[], slope: object[] } | null}
 */
export function matchTriangle(pivots, end, type, tol, opts) {
  const asc = type === 'asc';
  const flatT = asc ? 'H' : 'L';
  const s = asc ? 1 : -1; // asc: düz kenar üstte; desc: altta (fiyatlar s ile çarpılınca hep "asc" gibi düşünülür)
  let best = null;
  // Pencereyi geriye doğru büyüt; geçerli olan en geniş pencereyi al.
  for (let start = end - 3; start >= 0 && end - start < opts.maxLookback; start--) {
    const win = pivots.slice(start, end + 1);
    const flatSide = win.filter((p) => p.type === flatT);
    const slopeSide = win.filter((p) => p.type !== flatT);
    if (flatSide.length < opts.minTops || slopeSide.length < opts.minBottoms) continue;
    // Düz kenar: en uç tepe (asc) seviyeyi belirler; ona tol içinde olanlar dokunuş. Hiçbiri seviyeyi tol'dan fazla aşamaz.
    const level = s * Math.max(...flatSide.map((p) => s * p.price));
    const flat = flatSide.filter((p) => s * (level - p.price) <= tol);
    if (flat.length < opts.minTops) continue;
    // Eğik kenar: penceredeki ilk eğik pivot en uç olmalı (asc: en düşük dip) ve son eğik pivot ondan yukarıda.
    const X = slopeSide[0], Z = slopeSide[slopeSide.length - 1];
    if (slopeSide.some((p) => s * (p.price - X.price) < 0)) continue;
    if (!(s * (Z.price - X.price) > 0) || Z.i === X.i) continue;
    // Destek çizgisi (asc): X'ten çıkan ve tüm sonraki diplerin altında kalan en dik olmayan doğru
    // (grafikte elle çizilen trend çizgisi gibi; ara dipler çizginin altına sarkmaz).
    let slopeK = Infinity, Y = Z;
    for (const p of slopeSide) {
      if (p.i === X.i) continue;
      const k = (s * (p.price - X.price)) / (p.i - X.i);
      if (k < slopeK) { slopeK = k; Y = p; }
    }
    if (!(slopeK > 0)) continue;
    const lineAt = (i) => X.price + s * slopeK * (i - X.i);
    const slope = slopeSide.filter((p) => s * (p.price - lineAt(p.i)) <= 2 * tol);
    if (slope.length < opts.minBottoms) continue;
    // Eğik kenar düz kenarın içinde kalmalı
    if (slopeSide.some((p) => s * (level - p.price) <= tol)) continue;
    best = { start, end, flat, slope, level, X, Y, Z };
  }
  return best;
}

/**
 * Üçgenden önceki anlamlı hareket ve bağlam.
 * Yükselen üçgende (desc için ayna): X = eğik kenarın en düşük dibi, F = düz kenarın ilk tepesi.
 *  - Düşüş adayı: X'ten önceki en yüksek tepe → X (TRX örneği: 0,377 → 0,3086).
 *  - Yükseliş adayı: F'den önceki en düşük dip → F (yükseliş sonrası sıkışma).
 * Hangisi büyükse ve kalıp yüksekliğinin en az 2 katıysa önceki hareket odur. Arama, kalıp genişliğinin 3 katı mumla sınırlı.
 */
function priorMove(pivots, m, type, height, width) {
  const asc = type === 'asc';
  const X = asc ? m.slope.reduce((a, b) => (b.price < a.price ? b : a)) : m.slope.reduce((a, b) => (b.price > a.price ? b : a));
  const F = m.flat[0];
  const before = (idx, t) => pivots.filter((p) => p.type === t && p.i < idx && p.i >= idx - 3 * width);
  const pick = (list, hi) => (list.length ? list.reduce((a, b) => (hi ? (b.price > a.price ? b : a) : (b.price < a.price ? b : a))) : null);
  // Eğik kenarın ucuna gelen hareket (asc: düşüş) ve düz kenara gelen hareket (asc: yükseliş)
  const intoX = pick(before(X.i, asc ? 'H' : 'L'), asc);
  const intoF = pick(before(F.i, asc ? 'L' : 'H'), !asc);
  const sizeX = intoX ? Math.abs(intoX.price - X.price) : 0;
  const sizeF = intoF ? Math.abs(F.price - intoF.price) : 0;
  const cand = sizeX >= sizeF ? { from: intoX, to: X, size: sizeX } : { from: intoF, to: F, size: sizeF };
  if (!cand.from || cand.size < 2 * height) return null;
  return { from: cand.from, to: cand.to, dir: cand.from.price > cand.to.price ? 'down' : 'up' };
}

/** Bir coinin mumlarında üçgenleri bulur (tüm dereceler, tekrarsız). */
export function findTriangles(candles, options = {}, live = null) {
  const o = { ...TRI_DEFAULTS, ...options };
  if (candles.length < 40) return [];
  const highs = candles.map((c) => c.high), lows = candles.map((c) => c.low), closes = candles.map((c) => c.close);
  const atrArr = fillAtr(atrOf(highs, lows, closes, 14));
  const all = live ? [...candles, live] : candles;
  const last = all.length - 1;
  const price = all[last].close;
  const out = [];
  const seen = new Set();

  for (const k of o.sensitivities) {
    const { pivots } = zigzag(candles, atrArr, k);
    if (pivots.length < 4) continue;
    // Kalıp son pivotta ya da kırılım sonrası 1-2 pivot önce bitmiş olabilir.
    for (let end = pivots.length - 1; end >= Math.max(3, pivots.length - 3); end--) {
      for (const type of ['asc', 'desc']) {
        if (o.triType !== 'all' && o.triType !== type) continue;
        const atrE = atrArr[pivots[end].i];
        const m = matchTriangle(pivots, end, type, o.flatAtr * atrE, o);
        if (!m) continue;
        const key = `${type}|${pivots[m.start].i}|${pivots[m.end].i}`;
        if (seen.has(key)) continue;
        seen.add(key);
        const t = evaluateTriangle({ all, candles, atrArr, pivots, m, type, k, o, price, last });
        if (t) out.push(t);
      }
    }
  }
  // Aynı üçgen farklı bitiş noktası / derece ile birden çok kez bulunabilir: zamanda çakışan aynı tipten
  // yalnızca en geç biten (eşitse en çok dokunuşlu) olanı tut.
  out.sort((a, b) => b.endPivot.i - a.endPivot.i || (b.touches.flat + b.touches.slope) - (a.touches.flat + a.touches.slope));
  const kept = [];
  for (const t of out) {
    const overlaps = kept.some((x) => x.type === t.type && t.first.i <= x.endPivot.i && x.first.i <= t.endPivot.i);
    if (!overlaps) kept.push(t);
  }
  return kept;
}

function evaluateTriangle({ all, candles, atrArr, pivots, m, type, k, o, price, last }) {
  const d = type === 'asc' ? 1 : -1;
  const first = pivots[m.start], endP = pivots[m.end];
  const widthBars = endP.i - first.i;
  const atrE = atrArr[endP.i];
  const level = m.level;
  // Eğik kenar: ilk ve son eğik pivotlardan geçen doğru
  const s1 = m.X, s2 = m.Y; // destek çizgisi X–Y; stop son dibin (Z) ötesi
  if (s2.i === s1.i) return null;
  const slopeAt = (i) => s1.price + ((s2.price - s1.price) * (i - s1.i)) / (s2.i - s1.i);
  const extreme = type === 'asc' ? Math.min(...m.slope.map((x) => x.price)) : Math.max(...m.slope.map((x) => x.price));
  const height = Math.abs(level - extreme);
  if (widthBars < o.minWidthBars || height < o.minHeightAtr * atrE) return null;

  // Kalıp sonrası: kırılım ya da bozulma (kapanışa göre)
  const buf = o.breakBufAtr * atrE;
  const breakLevel = level + d * buf;
  let breakIdx = -1, failIdx = -1;
  for (let i = endP.i + 1; i <= last; i++) {
    const cl = all[i].close;
    if (d * (cl - breakLevel) > 0) { breakIdx = i; break; }
    if (d * (cl - (slopeAt(i) - d * buf)) < 0) { failIdx = i; break; }
  }
  let state;
  if (breakIdx >= 0) state = last - breakIdx <= o.recentBars ? 'broken' : 'old';
  else if (failIdx >= 0) state = 'failed';
  else state = d * (level - price) <= o.nearAtr * atrE ? 'near' : 'forming';

  // Önceki hareket ve bağlam
  const prior = priorMove(pivots, m, type, height, widthBars);
  const context = prior ? (prior.dir === 'down' ? 'after-down' : 'after-up') : null;
  if (o.triContext !== 'all' && o.triContext !== context) return null;

  // Seviyeler
  const lastSlope = m.Z;
  const stop = lastSlope.price - d * o.triStopAtr * atrE;
  const entry = state === 'broken' ? price : breakLevel;
  const targets = [{ key: 'mm', name: 'Ölçülü hareket (1× yükseklik)', price: breakLevel + d * height, kind: 'mm' }];
  let fibMode = null;
  if (prior) {
    const span = Math.abs(prior.from.price - prior.to.price);
    // Kırılım önceki hareketin tersine ise geri çekilme seviyeleri, aynı yöndeyse kırılımdan izdüşüm.
    fibMode = (prior.dir === 'down') === (d === 1) ? 'retr' : 'proj';
    for (const r of FIB_RATIOS) {
      const p = fibMode === 'retr' ? prior.to.price + d * r * span : level + d * r * span;
      targets.push({ key: Object.keys(FIB_KEY).find((x) => FIB_KEY[x] === r), name: `${fibMode === 'retr' ? 'Geri çekilme' : 'İzdüşüm'} %${+(r * 100).toFixed(1)}`, price: p, kind: 'fib', r });
    }
  }
  // Ana hedef: seçilen; giriş seviyesinin ötesinde değilse ölçülü hareket
  const want = o.triTarget === 'mm' ? targets[0] : targets.find((x) => x.r === FIB_KEY[o.triTarget]);
  const main = want && d * (want.price - entry) > 0 ? want : targets[0];
  const risk = d * (entry - stop);
  const rr = risk > 0 ? (d * (main.price - entry)) / risk : 0;

  // Skor: dokunuş sayısı, düzlük, genişlik
  const flatSpread = (Math.max(...m.flat.map((x) => x.price)) - Math.min(...m.flat.map((x) => x.price))) / atrE;
  const score = Math.min(100, Math.round(40 + 8 * (m.flat.length - 2) + 6 * (m.slope.length - 2) + 15 * (1 - Math.min(1, flatSpread / o.flatAtr)) + Math.min(15, widthBars / 4) + (context ? 10 : 0)));

  return {
    kind: 'triangle',
    type,
    typeLabel: TRI_TYPE[type],
    d,
    direction: d === 1 ? 'long' : 'short',
    sensitivity: k,
    flat: m.flat,
    slope: m.slope,
    first,
    endPivot: endP,
    level,
    slopeLine: { i1: s1.i, p1: s1.price, i2: s2.i, p2: s2.price },
    height,
    widthBars,
    touches: { flat: m.flat.length, slope: m.slope.length },
    prior,
    context,
    contextLabel: context ? CONTEXT[context] : 'bağlam yok',
    fibMode,
    state,
    stateLabel: TRI_STATE[state],
    breakIdx,
    levels: { entry, breakLevel, stop, targets, main, atr: atrE },
    rr,
    price,
    score,
  };
}

const TRI_ACTIVE = new Set(['forming', 'near', 'broken']);

/** Panelde gösterilecek üçgenler: aktif durum + min R:R; sıralı. */
export function pickTriangles(all, options = {}) {
  const o = { ...TRI_DEFAULTS, ...options };
  return all.filter((t) => TRI_ACTIVE.has(t.state) && t.rr >= o.triMinRR).sort(compareTriangles);
}

export function compareTriangles(a, b) {
  const rank = { broken: 0, near: 1, forming: 2, old: 3, failed: 4 };
  if (rank[a.state] !== rank[b.state]) return rank[a.state] - rank[b.state];
  return b.score - a.score;
}

/**
 * Kırılım sonrası en uç fiyatın (MFE) hedef birimleriyle ifadesi.
 * mfeH: ölçülü hareket katı; mfeFib: retr → önceki hareketin geri alınan oranı, proj → kırılım seviyesinden izdüşüm oranı.
 */
export function reachOf(t, mfePrice) {
  const d = t.d;
  const mfeH = (d * (mfePrice - t.levels.breakLevel)) / t.height;
  let mfeFib = null;
  if (t.prior) {
    const span = Math.abs(t.prior.from.price - t.prior.to.price);
    mfeFib = t.fibMode === 'retr' ? (d * (mfePrice - t.prior.to.price)) / span : (d * (mfePrice - t.level)) / span;
  }
  return { mfeH, mfeFib };
}

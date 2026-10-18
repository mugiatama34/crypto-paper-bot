// Dalga 3 girişi setup'ı: tamamlanmış 0-1-2 (dalga 2 = ABC) → dalga 3'e giriş.
// Saf fonksiyonlar; DOM ve ağ kullanmaz. Long ve short için aynı kod (d = +1 / −1).
import { evaluate } from '../ew/engine/analyze.js';
import { zigzag, fillAtr } from './zigzag.js';
import { atr as atrOf, rsi as rsiOf } from '../data/indicators.js';
import { zonePlan } from './zone.js';

export const DEFAULTS = {
  ruleSet: 'course',
  sensitivities: [2, 3, 4.5], // ana ZigZag eşiği (× ATR)
  subDivisors: [2, 2.5, 3, 4], // dalga 2 iç yapısı için eşik = ana eşik ÷ bölen
  retrMin: 0.382,
  retrMax: 0.886,
  idealRetr: [0.5, 0.618],
  requireAbc: true,
  abcKinds: 'both', // 'zigzag' | 'flat' | 'both'
  minTimeRatio: 1.0, // süre(2) / süre(1) bundan BÜYÜK olmalı
  minW1Atr: 3,
  w1Extend: 1, // 0 noktasını kaç iç dalgalanma geriye taşıyabilir (0 = son dönüş noktası, 99 = en geniş)
  rsiMode: 'score', // 'off' | 'score' | 'require'
  volume: false,
  stopMode: 'w2', // 'w2': dalga 2 ucu − stopAtr × ATR, 'w0': 0 noktası − stopAtr × ATR
  stopAtr: 0.25,
  targets: [1, 1.618, 2.618],
  mainTarget: 1, // R:R hesabında kullanılan hedefin indeksi (1.618)
  minRR: 2,
  direction: 'both', // 'both' | 'long' | 'short'
  entryType: 'break', // 'break': B kırılımı (onaylı) | 'zone': limit bölge (erken) | 'both'
  zoneCExt: 1.272, // bölge kenarı: C = 1,272 × A (B'den ölçülür)
  zoneRetr: 0.618, // bölge kenarı: dalga 2 = dalga 1'in %61,8'i
  zoneEntry: 'mid', // 'near' yakın kenar | 'mid' yarı yarıya kademeli | 'far' uzak kenar | 'r618' / 'c127' yalnızca o seviye
  zoneStop: 'w0', // 'w0': dalga 1'in başı (0) | 'r886': dalga 2 = %88,6
  perfectAtr: 0.5, // iki bölge kenarı arası ≤ bu × ATR ise 'Mükemmel'
  zoneFrontAtr: 0.2, // limit emir seviyenin bu × ATR önüne konur (BAT 15m: C seviyeye 0,16 ATR kala döndü)
  approachAtr: 0.5, // fiyat emre bu × ATR yaklaşınca 'Seviyeye yaklaştı'
  onlyPerfect: false, // yalnızca mükemmel çakışmalı setup'lar
  minRiskPct: 0, // giriş-stop mesafesi fiyatın bu %'sinden azsa plan aktif sayılmaz (0 = kapalı)
  feePct: 0.1, // komisyon tahmini (% / işlem tarafı) — dar stop uyarısı için
  zoneTime: 'require', // 'require': limit emir süre(2) > minTimeRatio × süre(1) olunca aktif | 'ignore'
};

/** Bölge emrinin aktif olduğu ilk mutlak mum indeksi (süre koşulu). */
const activeFromOf = (o, P0, P1) =>
  o.zoneTime === 'require' ? P1.i + Math.floor(o.minTimeRatio * (P1.i - P0.i)) + 1 : null;

const useZone = (o) => o.entryType === 'zone' || o.entryType === 'both';
const useBreak = (o) => o.entryType === 'break' || o.entryType === 'both';

/**
 * Dalga 1'in başlangıcını geriye genişletir: daha önceki aynı tip pivot daha uçtaysa (long: daha düşük dip)
 * ve aradaki ters pivot 1'i geçmiyorsa, aradaki dalgalanma dalga 1'in iç yapısı sayılır.
 * @param j0 pivots içinde P0'ın indeksi
 */
export function extendW1Start(pivots, j0, P1, steps) {
  const d = pivots[j0].type === 'L' ? 1 : -1;
  let j = j0;
  for (let n = 0; n < steps && j - 2 >= 0; n++) {
    const prev = pivots[j - 2], mid = pivots[j - 1];
    if (prev.type !== pivots[j].type) break;
    if (!(d * (prev.price - pivots[j].price) < 0)) break; // önceki dip daha aşağıda değil
    if (!(d * (mid.price - P1.price) < 0)) break; // aradaki tepe 1'i geçiyor
    j -= 2;
  }
  return pivots[j];
}

const STATE_LABEL = {
  ready: 'Hazırlanıyor',
  triggered: 'Tetiklendi',
  gone: 'Kaçtı',
  invalid: 'Geçersiz',
};
export { STATE_LABEL };

const ext = (c, type) => (type === 'H' ? c.high : c.low);
const pt = (candles, i, type) => ({ t: i, p: ext(candles[i], type) });
const mean = (a) => (a.length ? a.reduce((s, x) => s + x, 0) / a.length : 0);

/**
 * Dalga 2'nin (i1 → i2) ABC olup olmadığını bulur. Farklı alt eşiklerle iç ZigZag çalıştırır;
 * tam iki iç pivot (A, B) çıkan ve motorda zigzag/flat olarak GEÇERLİ olan ilk dereceyi döndürür.
 */
export function findAbc(candles, atrArr, i1, i2, type1, k, opts) {
  const kinds = opts.abcKinds === 'both' ? ['zigzag', 'flat'] : [opts.abcKinds];
  const tried = [];
  for (const div of opts.subDivisors) {
    const { pivots } = zigzag(candles, atrArr, k / div, { from: i1, to: i2, start: { i: i1, type: type1 } });
    const inner = pivots.filter((p) => p.i > i1 && p.i < i2);
    tried.push(inner.length);
    if (inner.length !== 2) continue;
    const [A, B] = inner;
    // A, dalga 2 yönünde (1'in tersi) olmalı; C = i2.
    if (A.type === type1) continue;
    const pts = [pt(candles, i1, type1), { t: A.i, p: A.price }, { t: B.i, p: B.price }, pt(candles, i2, A.type)];
    const evals = kinds
      .map((id) => evaluate(id, pts, { ruleSet: opts.ruleSet, logScale: true }))
      .filter((e) => e.valid)
      .sort((a, b) => b.score - a.score);
    if (evals.length) return { A, B, div, eval: evals[0], kind: evals[0].pattern };
  }
  return { none: true, tried };
}

/** Bir coinin mumları için tüm duyarlılıklarda dalga-3 setup adaylarını bulur. */
export function findWave3Setups(candles, options = {}, live = null) {
  const opts = { ...DEFAULTS, ...options };
  if (candles.length < 40) return [];
  const highs = candles.map((c) => c.high), lows = candles.map((c) => c.low), closes = candles.map((c) => c.close);
  const atrArr = fillAtr(atrOf(highs, lows, closes, 14));
  const rsiArr = rsiOf(closes, 14);
  const last = candles.length - 1;
  const price = live?.close ?? closes[last];

  const seen = new Set();
  const out = [];
  for (const k of opts.sensitivities) {
    const { pivots, tentative } = zigzag(candles, atrArr, k);
    // Tamamlanmış dalga 2: son üç onaylı pivot 0-1-2
    if (pivots.length >= 3) {
      const P1 = pivots[pivots.length - 2], P2 = pivots[pivots.length - 1];
      const P0 = extendW1Start(pivots, pivots.length - 3, P1, opts.w1Extend);
      const key = `${P0.i}-${P1.i}-${P2.i}`;
      if (!seen.has(key)) {
        seen.add(key);
        const s = evaluateSetup({ candles, atrArr, rsiArr, P0, P1, P2, k, opts, live, price });
        if (s) out.push(s);
      }
    }
    // Süren dalga 2: son iki onaylı pivot 0-1, C henüz oluşuyor (yalnızca bölge girişi için)
    if (useZone(opts) && pivots.length >= 2 && tentative) {
      const P1 = pivots[pivots.length - 1];
      const P0 = extendW1Start(pivots, pivots.length - 2, P1, opts.w1Extend);
      const key = `f${P0.i}-${P1.i}`;
      if (!seen.has(key)) {
        seen.add(key);
        const s = evaluateForming({ candles, atrArr, P0, P1, tentative, k, opts, live, price });
        if (s) out.push(s);
      }
    }
  }
  return out;
}

// Dalga 2 sürerken kısmi sonuçla kırılmayan kurallar (dalga 2 henüz bitmedi).
const PARTIAL_OK = new Set(['CRS-IMP-1']);

/** Dalga 2 sürüyor: 0-1 onaylı, iç yapıda A ve B onaylı, C oluşuyor → limit bölge planı. */
function evaluateForming({ candles, atrArr, P0, P1, tentative, k, opts, live, price }) {
  const d = P0.type === 'L' ? 1 : -1;
  const dirName = d === 1 ? 'long' : 'short';
  if (opts.direction !== 'both' && opts.direction !== dirName) return null;
  if (tentative.type !== P0.type) return null;
  const last = candles.length - 1;
  const cur = { i: tentative.i, price: tentative.price, type: tentative.type };
  const reject = [];

  const L1 = Math.abs(Math.log(P1.price) - Math.log(P0.price));
  const retrNow = Math.abs(Math.log(P1.price) - Math.log(cur.price)) / L1;
  const w1Atr = Math.abs(P1.price - P0.price) / atrArr[P1.i];
  if (w1Atr < opts.minW1Atr) reject.push(`Dalga 1 küçük (${w1Atr.toFixed(1)}×ATR)`);
  if (retrNow > opts.retrMax) reject.push(`Dalga 2 geri çekilme %${(retrNow * 100).toFixed(1)} bant dışı`);

  const imp = evaluate('impulse', [P0, P1, cur].map((p) => ({ t: p.i, p: p.price })), { ruleSet: opts.ruleSet, logScale: true });
  const hardFails = imp.results.filter((r) => r.status === 'fail' && !PARTIAL_OK.has(r.rule.id));
  if (hardFails.length) reject.push('İtki kuralı ihlali');

  // İç yapı: P1'den bugüne alt derecede tam iki onaylı pivot (A, B), C oluşuyor.
  const kinds = opts.abcKinds === 'both' ? ['zigzag', 'flat'] : [opts.abcKinds];
  let ab = null;
  for (const div of opts.subDivisors) {
    const { pivots: inner, tentative: t } = zigzag(candles, atrArr, k / div, { from: P1.i, to: last, start: { i: P1.i, type: P1.type } });
    const ins = inner.filter((p) => p.i > P1.i);
    if (ins.length !== 2 || ins[0].type === P1.type || !t || t.type !== ins[0].type) continue;
    const [A, B] = ins;
    const pts = [{ t: P1.i, p: P1.price }, { t: A.i, p: A.price }, { t: B.i, p: B.price }];
    const ev = kinds.map((id) => evaluate(id, pts, { ruleSet: opts.ruleSet, logScale: true })).filter((e) => e.valid).sort((a, b) => b.score - a.score);
    if (ev.length) { ab = { A, B, C: t, eval: ev[0] }; break; }
  }
  if (!ab) reject.push('Dalga 2 ABC değil');

  if (reject.length) {
    opts.diagnostics?.push({ direction: dirName, sensitivity: k, phase: 'forming', reasons: reject });
    return null;
  }

  const path = candles.slice(ab.B.i + 1);
  if (live) path.push(live);
  const zone = zonePlan({
    d, p0: P0.price, p1: P1.price, A: ab.A.price, B: ab.B.price, atr: atrArr[last], l1Log: L1, base: null,
    pathStart: ab.B.i + 1, activeFrom: activeFromOf(opts, P0, P1),
  }, path, false, opts);
  if (!zone || zone.invalid) {
    opts.diagnostics?.push({ direction: dirName, sensitivity: k, phase: 'forming', reasons: [zone?.reason || 'Bölge hesaplanamadı'] });
    return null;
  }

  const D1 = P1.i - P0.i, D2 = last - P1.i;
  const timeRatio = D2 / (D1 || 1);
  const strengthPts = { 'Mükemmel': 15, 'Güçlü': 10, 'Orta': 5, 'Zayıf': 0 }[zone.strength] + Math.min(zone.inside, 3) * 2;
  const score = Math.min(100, Math.round(0.35 * imp.score + 0.35 * ab.eval.score + strengthPts));

  return {
    phase: 'forming',
    direction: dirName,
    d,
    sensitivity: k,
    points: { p0: P0, p1: P1, p2: null, cNow: { i: ab.C.i, price: ab.C.price, type: ab.C.type } },
    abc: { kind: ab.eval.pattern, label: ab.eval.label, A: ab.A, B: ab.B, results: ab.eval.results, score: ab.eval.score },
    impulse: { label: imp.label, results: imp.results, score: imp.score },
    retr: retrNow,
    timeRatio,
    timeOk: timeRatio > opts.minTimeRatio,
    durations: { D1, D2 },
    rsi: { divergence: null, w1Momentum: null },
    volume: null,
    levels: null,
    zone,
    price,
    rr: 0,
    state: 'forming',
    stateLabel: 'Dalga 2 sürüyor',
    barsSince2: null,
    score,
  };
}

function evaluateSetup({ candles, atrArr, rsiArr, P0, P1, P2, k, opts, live, price }) {
  const d = P0.type === 'L' ? 1 : -1; // long: 0 dip, 1 tepe, 2 dip
  const dirName = d === 1 ? 'long' : 'short';
  if (opts.direction !== 'both' && opts.direction !== dirName) return null;
  const reject = [];

  const L1 = Math.abs(Math.log(P1.price) - Math.log(P0.price));
  const L2 = Math.abs(Math.log(P1.price) - Math.log(P2.price));
  const retr = L2 / L1;
  const D1 = P1.i - P0.i, D2 = P2.i - P1.i;
  const timeRatio = D2 / (D1 || 1);
  const w1Atr = Math.abs(P1.price - P0.price) / atrArr[P1.i];

  if (w1Atr < opts.minW1Atr) reject.push(`Dalga 1 küçük (${w1Atr.toFixed(1)}×ATR)`);
  if (retr < opts.retrMin || retr > opts.retrMax) reject.push(`Dalga 2 geri çekilme %${(retr * 100).toFixed(1)} bant dışı`);
  if (!(timeRatio > opts.minTimeRatio)) reject.push(`Süre(2)/Süre(1) = ${timeRatio.toFixed(2)}× (> ${opts.minTimeRatio} olmalı)`);

  // Kural motoru: 0-1-2 itki başlangıcı
  const pts = [P0, P1, P2].map((p) => ({ t: p.i, p: p.price }));
  const imp = evaluate('impulse', pts, { ruleSet: opts.ruleSet, logScale: true });
  if (!imp.valid) reject.push('İtki kuralı ihlali');

  // Dalga 2 iç yapısı
  const abc = findAbc(candles, atrArr, P1.i, P2.i, P1.type, k, opts);
  if (opts.requireAbc && abc.none) reject.push('Dalga 2 ABC değil');

  if (reject.length) {
    opts.diagnostics?.push({ direction: dirName, sensitivity: k, reasons: reject });
    return null;
  }

  // RSI (Ders 3): C'de A'ya göre uyumsuzluk + dalga 1 momentumu
  const rsiInfo = { divergence: null, w1Momentum: null };
  if (!abc.none) {
    const rA = rsiArr[abc.A.i], rC = rsiArr[P2.i];
    if (rA != null && rC != null) rsiInfo.divergence = { ok: d * (rC - rA) > 0, rA, rC };
  }
  const r1 = rsiArr[P1.i];
  if (r1 != null) rsiInfo.w1Momentum = { ok: d === 1 ? r1 >= 60 : r1 <= 40, r1 };
  const rsiPts = (rsiInfo.divergence?.ok ? 0.5 : 0) + (rsiInfo.w1Momentum?.ok ? 0.5 : 0);
  if (opts.rsiMode === 'require' && rsiPts === 0) return null;

  // Hacim: dalga 1 ortalama hacmi > dalga 2 ortalama hacmi
  const v1 = mean(candles.slice(P0.i + 1, P1.i + 1).map((c) => c.volume));
  const v2 = mean(candles.slice(P1.i + 1, P2.i + 1).map((c) => c.volume));
  const volOk = v1 > v2;

  // Seviyeler
  const atrNow = atrArr[candles.length - 1];
  const lg = (x) => Math.log(x);
  const targets = opts.targets.map((m) => ({ k: m, price: Math.exp(lg(P2.price) + d * m * L1) }));
  const trigger = !abc.none
    ? (d === 1 ? Math.min(abc.B.price, P1.price) : Math.max(abc.B.price, P1.price))
    : P1.price;
  const stopBase = opts.stopMode === 'w0' ? P0.price : P2.price;
  const stop = stopBase - d * opts.stopAtr * atrArr[P2.i];

  // Durum: 2'den sonraki mumlar (+ canlı mum)
  const after = candles.slice(P2.i + 1);
  if (live) after.push(live);
  const best = after.length ? (d === 1 ? Math.max(...after.map((c) => c.high)) : Math.min(...after.map((c) => c.low))) : P2.price;
  const worst = after.length ? (d === 1 ? Math.min(...after.map((c) => c.low)) : Math.max(...after.map((c) => c.high))) : P2.price;
  let state;
  if (d * (worst - P2.price) < 0) state = 'invalid';
  else if (d * (best - targets[0].price) >= 0) state = 'gone';
  else if (d * (best - trigger) >= 0) state = 'triggered';
  else state = 'ready';

  const entry = state === 'triggered' ? price : trigger;
  const main = targets[Math.min(opts.mainTarget, targets.length - 1)].price;
  const risk = d * (entry - stop);
  const rr = risk > 0 ? (d * (main - entry)) / risk : 0;

  // Skor
  const ideal = retr >= opts.idealRetr[0] - 0.02 && retr <= opts.idealRetr[1] + 0.02;
  let score = 0.35 * imp.score + 0.35 * (abc.none ? 0 : abc.eval.score) + (ideal ? 10 : 0);
  if (opts.rsiMode !== 'off') score += 10 * rsiPts;
  if (opts.volume && volOk) score += 10;
  score = Math.min(100, Math.round(score));

  // Limit bölge planı (geriye dönük: C oluşurken bölge dolmuş muydu?)
  let zone = null;
  if ((useZone(opts) || opts.onlyPerfect) && !abc.none) {
    const zpath = candles.slice(abc.B.i + 1);
    if (live) zpath.push(live);
    zone = zonePlan({
      d, p0: P0.price, p1: P1.price, A: abc.A.price, B: abc.B.price, atr: atrArr[P2.i], l1Log: L1, base: P2.price,
      pathStart: abc.B.i + 1, activeFrom: activeFromOf(opts, P0, P1),
    }, zpath, true, opts);
    if (zone?.invalid) zone = null;
    if (zone?.perfect) score = Math.min(100, score + 10);
  }

  return {
    phase: 'complete',
    direction: dirName,
    d,
    sensitivity: k,
    points: { p0: P0, p1: P1, p2: P2 },
    abc: abc.none ? null : {
      kind: abc.kind,
      label: abc.eval.label,
      A: abc.A,
      B: abc.B,
      results: abc.eval.results,
      score: abc.eval.score,
    },
    impulse: { label: imp.label, results: imp.results, score: imp.score },
    retr,
    timeRatio,
    durations: { D1, D2 },
    rsi: rsiInfo,
    volume: { v1, v2, ok: volOk },
    levels: { trigger, entry, stop, invalidation: P0.price, targets, atr: atrNow },
    price,
    rr,
    state,
    stateLabel: STATE_LABEL[state],
    barsSince2: candles.length - 1 - P2.i,
    timeOk: true,
    zone,
    score,
  };
}

const ZONE_ACTIVE = new Set(['waiting', 'approaching', 'filled']);

/** Giriş ile stop arası, fiyatın yüzdesi olarak. */
export const riskPct = (entry, stop) => (Math.abs(entry - stop) / entry) * 100;
/** Giriş + çıkış komisyonunun R cinsinden yaklaşık maliyeti (çıkış ≈ giriş varsayımıyla). */
export const feeR = (entry, stop, feePct) => ((feePct / 100) * 2 * entry) / (Math.abs(entry - stop) || Infinity);
/** Planın stop mesafesi eşiği geçiyor mu (minRiskPct = 0 → her zaman). */
export function riskOk(entry, stop, o) {
  return !(o.minRiskPct > 0) || riskPct(entry, stop) >= o.minRiskPct;
}

/** Seçilen giriş tipine göre bu setup'ın hâlâ geçerli (aktif) planları. */
export function activePlans(s, opts = {}) {
  const o = { ...DEFAULTS, ...opts };
  const out = [];
  if (o.onlyPerfect && !s.zone?.perfect) return out; // yalnızca çakışmalı (mükemmel) setup'lar
  if (useBreak(o) && s.phase === 'complete' && (s.state === 'ready' || s.state === 'triggered') && s.rr >= o.minRR
    && riskOk(s.levels.entry, s.levels.stop, o)) {
    out.push({ type: 'break', rr: s.rr });
  }
  if (useZone(o) && s.zone && ZONE_ACTIVE.has(s.zone.state) && s.zone.rr >= o.minRR && riskOk(s.zone.entry, s.zone.stop, o)) {
    out.push({ type: 'zone', rr: s.zone.rr });
  }
  return out;
}

/** Panelde gösterilecek setup'lar: en az bir aktif planı olanlar, sıralı. */
export function pickSetups(all, opts = {}) {
  return all.filter((s) => activePlans(s, opts).length).sort((a, b) => compareSetups(a, b, opts));
}

const bestRR = (s, o) => Math.max(0, ...activePlans(s, o).map((p) => p.rr));

export function compareSetups(a, b, opts = {}) {
  const act = (s) => (activePlans(s, opts).length ? 0 : 1);
  if (act(a) !== act(b)) return act(a) - act(b);
  const perf = (s) => (s.zone?.perfect ? 0 : 1);
  if (perf(a) !== perf(b)) return perf(a) - perf(b);
  if (b.score !== a.score) return b.score - a.score;
  return bestRR(b, opts) - bestRR(a, opts);
}

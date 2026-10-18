// Limit bölge (erken) giriş planı: C = 1,272 × A ile dalga 2 = %61,8 geri çekilme arası.
// Fiyat seviyeleri TradingView Fibonacci araçlarıyla aynı olsun diye doğrusal ölçekte hesaplanır.

export const ZONE_LABEL = {
  waiting: 'Bölge bekleniyor',
  filled: 'Bölgeden doldu',
  missed: 'Bölgeye gelmedi',
  stopped: 'Stop oldu',
  passed: 'Stop seviyesi geçildi',
  target: 'Hedef 1 görüldü',
  approaching: 'Seviyeye yaklaştı',
};

const C_LEVELS = [1, 1.272, 1.382, 1.618];
const R_LEVELS = [0.5, 0.618, 0.786, 0.886];

/**
 * @param z  { d, p0, p1, A, B (fiyatlar), atr, l1Log (log dalga 1 boyu), base (hedef tabanı; null = planlanan giriş) }
 * @param path  B'den sonraki mumlar (+ canlı mum), eskiden yeniye; path[j] mutlak indeksi = z.pathStart + j
 *              z.activeFrom: emir bu mutlak indeksten itibaren aktif (süre koşulu); öncesinde dolum sayılmaz
 * @param complete  dalga 2 bitti mi (C onaylandı) — dolmamışsa "kaçtı" sayılır
 */
export function zonePlan(z, path, complete, opts) {
  const { d, p0, p1, A, B, atr } = z;
  const w1 = d * (p1 - p0);
  const aLen = d * (p1 - A);
  if (!(w1 > 0) || !(aLen > 0)) return null;
  const cExt = (k) => B - d * k * aLen;
  const retr = (r) => p1 - d * r * w1;

  const lvC = cExt(opts.zoneCExt);
  const lvR = retr(opts.zoneRetr);
  // Yakın kenar: fiyata (B'ye) daha yakın olan; uzak kenar: diğeri.
  const near = d === 1 ? Math.max(lvC, lvR) : Math.min(lvC, lvR);
  const far = d === 1 ? Math.min(lvC, lvR) : Math.max(lvC, lvR);
  const lo = Math.min(near, far), hi = Math.max(near, far);
  const tol = 0.1 * atr;

  const levels = [
    ...C_LEVELS.map((k) => ({ name: `C = ${String(k).replace('.', ',')} × A`, price: cExt(k), edge: k === opts.zoneCExt })),
    ...R_LEVELS.map((r) => ({ name: `Dalga 2 = %${String(+(r * 100).toFixed(1)).replace('.', ',')}`, price: retr(r), edge: r === opts.zoneRetr })),
  ].map((l) => ({ ...l, inside: l.price >= lo - tol && l.price <= hi + tol }));
  // Çakışma: iki kenar dışında bölgeye düşen seviye sayısı. Güç yalnızca kenarların yakınlığına bakar
  // (bölge genişledikçe içine daha çok seviye düşer; bu tek başına güç göstergesi değildir).
  const inside = levels.filter((l) => l.inside && !l.edge).length;
  const widthAtr = (hi - lo) / atr;
  const widthPct = (hi - lo) / ((hi + lo) / 2);
  // Mükemmel: iki kenar (ör. %61,8 ve 1,272A) neredeyse aynı fiyatta → tek çakışma seviyesi.
  const perfect = widthAtr <= opts.perfectAtr;
  const strength = perfect ? 'Mükemmel' : widthAtr <= 1 ? 'Güçlü' : widthAtr <= 2.5 ? 'Orta' : 'Zayıf';
  const confluence = (lvC + lvR) / 2;

  const buffer = opts.stopAtr * atr;
  const stopBase = opts.zoneStop === 'r886' ? retr(0.886) : p0;
  const stop = stopBase - d * buffer;
  // Giriş: yakın / orta (yarı yarıya kademeli) / uzak kenar; mükemmelde tek seviye.
  // Emirler fiyat yönüne doğru zoneFrontAtr × ATR öne alınır: seviyeye birkaç tik kala dönen fiyat da doldurur.
  // r618 / c127: kenar yakın mı uzak mı olduğuna bakmadan yalnızca o Fibonacci seviyesi.
  const raw = perfect ? [confluence]
    : opts.zoneEntry === 'r618' ? [lvR]
      : opts.zoneEntry === 'c127' ? [lvC]
        : opts.zoneEntry === 'near' ? [near] : opts.zoneEntry === 'far' ? [far] : [near, far];
  // Emir verilecek en derin seviye stop'un ötesindeyse plan anlamsız.
  const deepest = d === 1 ? Math.min(...raw) : Math.max(...raw);
  if (d * (deepest - stop) <= 0) return { invalid: true, reason: 'Bölge stop seviyesinin ötesinde', near, far, stop, levels };
  const front = (opts.zoneFrontAtr || 0) * atr;
  const legs = raw.map((lv) => lv + d * front);
  const plannedEntry = legs.reduce((s, x) => s + x, 0) / legs.length;

  // Dolum simülasyonu: B'den sonraki mumlar sırayla.
  const filledLegs = new Set();
  let state = 'waiting', fillIdx = -1;
  const t1Of = (entry) => Math.exp(Math.log(z.base ?? entry) + d * opts.targets[0] * z.l1Log);
  const activeFrom = z.activeFrom ?? -Infinity;
  let pendingTime = false;
  for (let j = 0; j < path.length; j++) {
    const c = path[j];
    if ((z.pathStart ?? 0) + j < activeFrom) { pendingTime = true; continue; }
    pendingTime = false;
    const ext = d === 1 ? c.low : c.high;
    const wasFilled = filledLegs.size > 0;
    legs.forEach((lv, li) => { if (d * (ext - lv) <= 0) filledLegs.add(li); });
    if (filledLegs.size && fillIdx < 0) fillIdx = j;
    if (filledLegs.size) {
      if (d * (ext - stop) <= 0) { state = 'stopped'; break; }
      const fav = d === 1 ? c.high : c.low;
      // Dolum mumunda hedefe gidişi sayma (sıra bilinmez); sonraki mumlardan itibaren.
      if (wasFilled && d * (fav - t1Of(entryOf())) >= 0) { state = 'target'; break; }
      state = 'filled';
    }
  }
  function entryOf() {
    const got = legs.filter((_, li) => filledLegs.has(li));
    return got.length ? got.reduce((s, x) => s + x, 0) / got.length : plannedEntry;
  }
  if (state === 'waiting' && path.some((c) => d * ((d === 1 ? c.low : c.high) - stop) <= 0)) state = 'passed';
  if (state === 'waiting' && complete) state = 'missed';
  // Yaklaşma: dolmadı ama son mumun ucu ilk emir seviyesine approachAtr × ATR'den yakın.
  if (state === 'waiting' && path.length) {
    const lastC = path[path.length - 1];
    const gap = d * ((d === 1 ? lastC.low : lastC.high) - legs[0]);
    if (opts.approachAtr > 0 && gap <= opts.approachAtr * atr) state = 'approaching';
  }

  const entry = entryOf();
  const base = z.base ?? entry;
  const targets = opts.targets.map((m) => ({ k: m, price: Math.exp(Math.log(base) + d * m * z.l1Log) }));
  const main = targets[Math.min(opts.mainTarget, targets.length - 1)].price;
  const risk = d * (entry - stop);
  const rr = risk > 0 ? (d * (main - entry)) / risk : 0;

  return {
    type: 'zone',
    state,
    stateLabel: ZONE_LABEL[state],
    near, far, lvC, lvR, cExtK: opts.zoneCExt, retrR: opts.zoneRetr,
    perfect, confluence, front, atr,
    entry,
    legs,
    filledLegs: filledLegs.size,
    stop,
    stopMode: opts.zoneStop,
    targets,
    rr,
    levels,
    inside,
    widthAtr,
    widthPct,
    strength,
    // Süre koşulu henüz dolmadıysa emrin aktif olacağı mutlak mum indeksi
    activeFrom: Number.isFinite(activeFrom) ? activeFrom : null,
    pendingTime: (state === 'waiting' || state === 'approaching') && (pendingTime || path.length === 0) && Number.isFinite(activeFrom),
  };
}

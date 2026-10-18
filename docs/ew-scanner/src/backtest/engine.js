// Backtest motoru (saf, DOM'suz; worker'da ve Node testlerinde çalışır).
// Her kapanan mumda canlı tarayıcının yaptığını yapar: son `window` mumla findWave3Setups.
// Yeni çıkan setup'tan emir oluşturur ve sonraki mumlarda simüle eder. Gelecek veri kullanılmaz:
// t mumunda görülen setup'ın emri en erken t+1 mumunda dolabilir (tetiklenmişse t kapanışından piyasa emri).
import { findWave3Setups, DEFAULTS, riskOk } from '../scan/wave3.js';
import { findTriangles, TRI_DEFAULTS, reachOf } from '../scan/triangle.js';

// Üçgen modunda canlı tarayıcıyla aynı uzun pencere (bkz. scanner.js LONG_BARS)
export const TRI_WINDOW = 600;

export const BT_DEFAULTS = {
  window: 300, // canlı taramadaki mum sayısı
  exitTarget: 1, // 0: hedef 1, 1: hedef 2, 2: hedef 3
  feePct: 0.1, // işlem başına komisyon (%), giriş + çıkış
  // Kopya değişikliği (CHANGES.md > 3): piyasa/stop dolumlarında aleyhe kayma (%). Limit dolumları (bölge girişi,
  // hedef) kaymasızdır — limit emir fiyatından daha kötüye dolmaz.
  slipPct: 0.05,
  maxPending: 50, // dolmayan emir kaç mum sonra iptal
  maxHold: 200, // açık pozisyon kaç mum sonra kapanışla kapatılır
};

const sideExt = (c, d) => ({ adverse: d === 1 ? c.low : c.high, fav: d === 1 ? c.high : c.low });

/**
 * @param candles  kapanmış mumlar (eskiden yeniye), [from, to] aralığı + öncesinde ısınma
 * @param cfg      { from, to (ms), opts: tarayıcı ayarları, ...BT_DEFAULTS }
 * @returns {{ trades: object[], stats: { orders, cancelled: {reason: n} } }}
 */
export function backtestCoin(candles, cfg) {
  const c = { ...BT_DEFAULTS, ...cfg };
  // Funding (CHANGES.md > 2): [{ t, rate }] eskiden yeniye. Boş dizi = bu coin için funding kaydı yok.
  const funding = c.funding || [];
  const fundingFrom = funding.length ? funding[0].t : Infinity;
  const slip = (px, d, adverse) => (adverse ? px * (1 + d * c.slipPct / 100) : px);
  const opts = { ...TRI_DEFAULTS, ...DEFAULTS, ...c.opts, diagnostics: undefined };
  const triMode = opts.mode === 'triangle';
  if (triMode) c.window = Math.max(c.window, TRI_WINDOW);
  const trackers = []; // üçgen: kırılım sonrası en uç fiyatı (stop görülene kadar) izler
  const useBreak = opts.entryType !== 'zone';
  const useZone = opts.entryType !== 'break';
  const n = candles.length;
  const startT = Math.max(60, candles.findIndex((x) => x.time >= c.from));
  if (startT < 0 || startT >= n) return { trades: [], stats: { orders: 0, cancelled: {} } };

  const seen = new Set();
  const orders = []; // bekleyen emirler + açık pozisyonlar
  const trades = [];
  const cancelled = {};
  let orderCount = 0;
  const cancel = (o, reason) => { o.done = true; cancelled[o.type + ':' + reason] = (cancelled[o.type + ':' + reason] || 0) + 1; };

  for (let t = startT; t < n; t++) {
    const bar = candles[t];
    const inRange = bar.time <= c.to;

    // 1) Önceki mumlarda oluşan emir / pozisyonları bu mumla ilerlet
    for (const o of orders) if (!o.done && o.created < t) step(o, bar, t);
    for (const k of trackers) if (!k.done && k.start < t) track(k, bar, t);

    if (!inRange) {
      if (!orders.some((o) => !o.done) && !trackers.some((k) => !k.done)) break;
      continue; // aralık bitti: yeni emir yok, açık olanlar kapanana kadar devam
    }

    // 2) Bu mum kapandı: canlı tarayıcı ne görürdü?
    const off = Math.max(0, t + 1 - c.window);
    const win = candles.slice(off, t + 1);
    if (triMode) {
      triangleStep(win, off, bar, t);
      continue;
    }
    const setups = findWave3Setups(win, opts, null);
    for (const s of setups) {
      if (opts.onlyPerfect && !s.zone?.perfect) continue;
      const abs = (p) => candles[p.i + off].time;
      const base = `${s.direction}|${abs(s.points.p0)}|${abs(s.points.p1)}`;
      if (useBreak && s.phase === 'complete' && (s.state === 'ready' || s.state === 'triggered') && s.rr >= opts.minRR
        && riskOk(s.state === 'triggered' ? bar.close : s.levels.trigger, s.levels.stop, opts)) {
        const key = `B|${base}|${abs(s.points.p2)}`;
        if (!seen.has(key)) {
          seen.add(key);
          orderCount++;
          const L = s.levels;
          const o = {
            type: 'break', d: s.d, created: t, key,
            legs: [{ level: s.state === 'triggered' ? bar.close : L.trigger, w: 1, market: s.state === 'triggered' }],
            stop: L.stop, target: L.targets[Math.min(c.exitTarget, L.targets.length - 1)].price,
            t1: L.targets[0].price, cancelBeyond: s.points.p2.price, cancelFav: null,
            meta: meta(s),
          };
          if (o.legs[0].market) fillLeg(o, o.legs[0], bar.close, t);
          orders.push(o);
        }
      }
      if (useZone && s.phase === 'forming' && s.zone && s.zone.rr >= opts.minRR && riskOk(s.zone.entry, s.zone.stop, opts)) {
        const key = `Z|${base}|${abs(s.abc.A)}|${abs(s.abc.B)}`;
        if (seen.has(key)) continue;
        seen.add(key);
        if (s.zone.state !== 'waiting' && s.zone.state !== 'approaching') { cancel({ type: 'zone' }, 'geç algılandı'); orderCount++; continue; }
        orderCount++;
        const z = s.zone;
        const w = 1 / z.legs.length;
        orders.push({
          type: 'zone', d: s.d, created: t, key,
          legs: z.legs.map((level) => ({ level, w, market: false })),
          activeFrom: z.activeFrom != null ? z.activeFrom + off : -Infinity,
          stop: z.stop, target: z.targets[Math.min(c.exitTarget, z.targets.length - 1)].price,
          t1: z.targets[0].price, cancelBeyond: null, cancelFav: s.abc.B.price,
          meta: { ...meta(s), strength: z.strength, inside: z.inside },
        });
      }
    }
  }

  // Veri bitti: açık pozisyonlar son kapanıştan "açık" olarak raporlanır, bekleyen emirler iptal.
  const last = candles[n - 1];
  for (const o of orders) {
    if (o.done) continue;
    if (o.legs.some((l) => l.filled)) close(o, last.close, n - 1, 'open');
    else cancel(o, 'veri bitti');
  }
  for (const k of trackers) if (!k.done) finishTrack(k, true);
  return { trades, stats: { orders: orderCount, cancelled } };

  // ---------------------------------------------------------------- üçgen kırılımı
  // Kalıp bu mumun kapanışıyla kırıldıysa (breakIdx = son mum) kapanıştan piyasa emriyle girilir.
  function triangleStep(win, off, bar, t) {
    const last = win.length - 1;
    for (const tr of findTriangles(win, opts, null)) {
      if (tr.state !== 'broken' || tr.breakIdx !== last) continue;
      const key = `T|${tr.type}|${candles[tr.first.i + off].time}|${tr.level}`;
      if (seen.has(key)) continue;
      seen.add(key);
      orderCount++;
      const L = tr.levels;
      if (tr.rr < opts.triMinRR) { cancel({ type: 'triangle' }, 'R:R düşük'); continue; }
      if (!riskOk(bar.close, L.stop, opts)) { cancel({ type: 'triangle' }, 'stop çok yakın'); continue; }
      const o = {
        type: 'triangle', d: tr.d, created: t, key,
        legs: [{ level: bar.close, w: 1, market: true }],
        stop: L.stop, target: L.main.price, t1: L.main.price, cancelBeyond: null, cancelFav: null,
        meta: {
          sensitivity: tr.sensitivity, score: tr.score, pattern: tr.type, context: tr.context,
          // Kırılım seviyesinin kendisi önceki hareketin hangi Fib oranında (bundan gerideki seviyeler 'zaten geçilmiş' sayılır)
          startFib: reachOf(tr, L.breakLevel).mfeFib,
          strength: null, abc: null, retr: null, timeRatio: null,
        },
      };
      fillLeg(o, o.legs[0], bar.close, t);
      orders.push(o);
      // Nereye kadar gitti: hedeften çıkılsa bile stop görülene (ya da maxHold dolana) kadar izlenir.
      trackers.push({ o, tr, start: t, mfe: bar.close, done: false });
    }
  }

  function track(k, b, t) {
    const { adverse, fav } = sideExt(b, k.tr.d);
    // Stop mumunda fav sayılmaz (mum içi sıra bilinmez)
    if (k.tr.d * (adverse - k.tr.levels.stop) <= 0) return finishTrack(k);
    if (k.tr.d * (fav - k.mfe) > 0) k.mfe = fav;
    if (t - k.start >= c.maxHold) finishTrack(k);
  }

  function finishTrack(k) {
    k.done = true;
    const r = reachOf(k.tr, k.mfe);
    const trade = k.o.trade;
    if (trade) { trade.mfeH = r.mfeH; trade.mfeFib = r.mfeFib; }
    else k.o.reach = r; // pozisyon henüz kapanmadı: close() bunu işleme yazar
  }

  // ---------------------------------------------------------------- yardımcılar
  function meta(s) {
    return {
      sensitivity: s.sensitivity, score: s.score, retr: s.retr, timeRatio: s.timeRatio, abc: s.abc?.kind || null,
      widthAtr: s.zone ? s.zone.widthAtr : null, perfect: s.zone ? s.zone.perfect : null,
    };
  }

  function fillLeg(o, leg, price, t) {
    leg.filled = true;
    // B kırılımı (stop emri) ve piyasa girişleri kayar; bölge bacakları limit emridir.
    leg.price = o.type === 'zone' ? price : slip(price, o.d, true);
    leg.t = t;
    if (o.entryT == null) o.entryT = t;
  }

  function step(o, b, t) {
    const d = o.d;
    const { adverse, fav } = sideExt(b, d);
    const open = o.legs.some((l) => l.filled);
    const fillsNow = [];

    // Bekleyen bacaklar
    for (const leg of o.legs) {
      if (leg.filled) continue;
      if (o.type === 'break') {
        if (d * (adverse - o.cancelBeyond) < 0) { cancel(o, 'dalga 2 uzadı'); return; }
        if (d * (fav - leg.level) >= 0) {
          const px = d === 1 ? Math.max(leg.level, b.open) : Math.min(leg.level, b.open);
          fillLeg(o, leg, px, t);
          fillsNow.push(leg);
        }
      } else if (t >= o.activeFrom) {
        // Limit emir ancak fiyat seviyeyi KESİN geçerse dolar (CHANGES.md > 3): dokunuşta kuyruktaki öndekiler dolar.
        if (d * (adverse - leg.level) < 0) {
          const px = d === 1 ? Math.min(leg.level, b.open) : Math.max(leg.level, b.open);
          // Emir aktif olduğunda fiyat zaten stop'un ötesindeyse (süre koşulu beklenirken geçildi) setup bozulmuştur:
          // gerçekte bu emir verilmez. Hiç dolum yoksa iptal, varsa bu bacak atlanır.
          if (d * (px - o.stop) <= 0) {
            if (!o.legs.some((l) => l.filled)) { cancel(o, 'stop seviyesi geçildi'); return; }
            continue;
          }
          fillLeg(o, leg, px, t);
          fillsNow.push(leg);
        }
      }
    }

    const nowOpen = o.legs.some((l) => l.filled);
    if (!nowOpen) {
      if (o.cancelFav != null && d * (fav - o.cancelFav) > 0) return cancel(o, 'B kırıldı, bölgeye gelmedi');
      if (d * (fav - o.t1) >= 0) return cancel(o, 'hedef 1 dolmadan görüldü');
      if (t - o.created > c.maxPending) return cancel(o, 'süre aşımı');
      return;
    }

    // Pozisyon yönetimi: önce stop (muhafazakâr), sonra hedef (dolum mumunda hedef sayılmaz)
    if (d * (adverse - o.stop) <= 0) {
      const px = d === 1 ? Math.min(o.stop, b.open) : Math.max(o.stop, b.open);
      return close(o, slip(px, -d, true), t, 'stop');
    }
    if (open && !fillsNow.length && d * (fav - o.target) >= 0) {
      const px = d === 1 ? Math.max(o.target, b.open) : Math.min(o.target, b.open);
      return close(o, px, t, 'hedef');
    }
    if (t - o.entryT >= c.maxHold) return close(o, slip(b.close, -d, true), t, 'süre');
  }

  /** Fiyat birimi cinsinden toplam funding (pozitif = maliyet); fiyat, ödeme anını içeren son kapanmış mumun kapanışı. */
  function fundingCost(d, fromT, toT) {
    let sum = 0;
    for (const f of funding) {
      if (f.t <= fromT) continue;
      if (f.t > toT) break;
      sum += d * f.rate * priceAt(f.t);
    }
    return sum;
  }

  function priceAt(time) {
    let lo = 0, hi = n - 1;
    while (lo < hi) { const m = (lo + hi + 1) >> 1; if (candles[m].time <= time) lo = m; else hi = m - 1; }
    return candles[lo].close;
  }

  function close(o, exit, t, outcome) {
    o.done = true;
    const d = o.d;
    let R = 0, risk = 0, fundingR = 0;
    for (const leg of o.legs) {
      if (!leg.filled) continue;
      const r = d * (leg.level - o.stop); // planlanan risk (1R)
      if (!(r > 0)) continue;
      const fee = (c.feePct / 100) * (leg.price + exit);
      // Funding: dolumdan sonra, kapanışa kadar (dahil) her ödeme anında long öder / short alır (oran > 0 iken).
      const legFunding = fundingCost(d, candles[leg.t].time, candles[t].time);
      R += leg.w * ((d * (exit - leg.price) - fee - legFunding) / r);
      fundingR -= leg.w * legFunding / r;
      risk += leg.w;
    }
    const filled = o.legs.filter((l) => l.filled);
    const trade = {
      type: o.type,
      dir: d === 1 ? 'long' : 'short',
      created: candles[o.created].time,
      entryTime: candles[o.entryT].time,
      exitTime: candles[t].time,
      entry: filled.reduce((s, l) => s + l.price * l.w, 0) / (risk || 1),
      exit,
      stop: o.stop,
      target: o.target,
      filledLegs: filled.length,
      legs: o.legs.length,
      R,
      fundingR,
      // Pozisyonun dolum anı funding kaydının başladığı andan önceyse funding UYGULANAMADI (0 değil, bilinmiyor).
      fundingCovered: candles[o.entryT].time >= fundingFrom,
      outcome,
      bars: t - o.entryT,
      ...o.meta,
      ...(o.reach ? { mfeH: o.reach.mfeH, mfeFib: o.reach.mfeFib } : {}),
    };
    o.trade = trade;
    trades.push(trade);
  }
}

/** İşlem listesinden özet istatistik. */
export function summarize(trades) {
  const closed = trades.filter((t) => t.outcome !== 'open');
  const wins = closed.filter((t) => t.R > 0);
  const losses = closed.filter((t) => t.R <= 0);
  const gw = wins.reduce((s, t) => s + t.R, 0);
  const gl = -losses.reduce((s, t) => s + t.R, 0);
  let eq = 0, peak = 0, maxDD = 0;
  for (const t of [...closed].sort((a, b) => a.exitTime - b.exitTime)) {
    eq += t.R;
    peak = Math.max(peak, eq);
    maxDD = Math.max(maxDD, peak - eq);
  }
  return {
    n: closed.length,
    open: trades.length - closed.length,
    winRate: closed.length ? wins.length / closed.length : 0,
    avgR: closed.length ? (gw - gl) / closed.length : 0,
    totalR: gw - gl,
    pf: gl > 0 ? gw / gl : gw > 0 ? Infinity : 0,
    maxDD,
    targets: closed.filter((t) => t.outcome === 'hedef').length,
    fundingCovered: closed.filter((t) => t.fundingCovered).length,
    stops: closed.filter((t) => t.outcome === 'stop').length,
    timeouts: closed.filter((t) => t.outcome === 'süre').length,
  };
}

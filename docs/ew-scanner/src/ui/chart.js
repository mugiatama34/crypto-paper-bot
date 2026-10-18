// Lightweight Charts 5 ile setup grafiği: mumlar, 0-1-A-B-2 dalga yolu, etiketler ve seviye çizgileri.
import { decimals } from './format.js';

const C = {
  bg: '#121822', grid: '#1c2430', text: '#8f99aa',
  up: '#26a69a', down: '#ef5350', wave: '#f0c75e',
  entry: '#3987e5', stop: '#ef5350', inval: '#b06cf0', target: '#7cc4a8',
  zone: '#ff9f43', zoneStop: '#ff6b81',
};

// LWC zamanı UTC gösterir; yerel saat görünsün diye kaydırılır.
const TZ = -new Date().getTimezoneOffset() * 60;
const sec = (ms) => Math.floor(ms / 1000) + TZ;

let current = null;

export function destroyChart() {
  if (current) {
    current.ro?.disconnect();
    current.chart.remove();
    current = null;
  }
}

/**
 * @param el     grafik kabı
 * @param candles kapanmış mumlar (+ canlı mum eklenebilir)
 * @param s      setup (wave3.js çıktısı)
 * @param show   { showBreak, showZone } hangi giriş planlarının çizileceği
 */
export function drawSetup(el, candles, live, s, show = { showBreak: true, showZone: false }) {
  destroyChart();
  const LW = window.LightweightCharts;
  if (!LW) {
    el.innerHTML = '<p class="error">Grafik kütüphanesi yüklenemedi.</p>';
    return;
  }
  const prec = decimals(s.price);
  const chart = LW.createChart(el, {
    width: el.clientWidth,
    height: el.clientHeight,
    layout: { background: { type: 'solid', color: C.bg }, textColor: C.text, fontSize: 11, fontFamily: getComputedStyle(document.body).fontFamily },
    grid: { vertLines: { color: C.grid }, horzLines: { color: C.grid } },
    rightPriceScale: { borderColor: C.grid },
    timeScale: { borderColor: C.grid, timeVisible: true, secondsVisible: false, rightOffset: 6 },
    crosshair: { mode: LW.CrosshairMode.Normal },
    localization: { locale: 'tr-TR' },
  });
  const series = chart.addSeries(LW.CandlestickSeries, {
    upColor: C.up, downColor: C.down, borderUpColor: C.up, borderDownColor: C.down, wickUpColor: C.up, wickDownColor: C.down,
    priceFormat: { type: 'price', precision: prec, minMove: 10 ** -prec },
  });
  const all = live ? [...candles, live] : candles;
  series.setData(all.map((c) => ({ time: sec(c.time), open: c.open, high: c.high, low: c.low, close: c.close })));

  // Dalga yolu ve etiketler
  const { p0, p1 } = s.points;
  const p2 = s.points.p2 || s.points.cNow;
  const pts = [
    { p: p0, label: '0' },
    { p: p1, label: '1' },
    ...(s.abc ? [{ p: s.abc.A, label: 'A' }, { p: s.abc.B, label: 'B' }] : []),
    { p: p2, label: s.phase === 'forming' ? 'C?' : s.abc ? '2 (C)' : '2' },
  ];
  const path = chart.addSeries(LW.LineSeries, {
    color: C.wave, lineWidth: 2, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false,
    priceFormat: { type: 'price', precision: prec, minMove: 10 ** -prec },
  });
  path.setData(pts.map(({ p }) => ({ time: sec(candles[p.i].time), value: p.price })));
  LW.createSeriesMarkers(series, pts.map(({ p, label }) => ({
    time: sec(candles[p.i].time),
    position: p.type === 'H' ? 'aboveBar' : 'belowBar',
    shape: p.type === 'H' ? 'arrowDown' : 'arrowUp',
    color: C.wave,
    text: label,
  })));

  // Seviyeler
  const L = s.levels;
  const line = (price, color, title, style = LW.LineStyle.Dashed) =>
    series.createPriceLine({ price, color, lineWidth: 1, lineStyle: style, axisLabelVisible: true, title });
  line(p0.price, C.inval, '0 / geçersiz', LW.LineStyle.Dotted);
  if (show.showBreak && L) {
    line(L.entry, C.entry, s.state === 'triggered' ? 'Giriş (güncel)' : 'Tetik B', LW.LineStyle.Solid);
    line(L.stop, C.stop, 'Stop');
  } else if (show.showBreak && s.abc) {
    line(s.abc.B.price, C.entry, 'Tetik B', LW.LineStyle.Solid);
  }
  const Z = show.showZone ? s.zone : null;
  if (Z) {
    const nm = (v) => (v === Z.lvC ? `C=${String(Z.cExtK).replace('.', ',')}A` : `%${String(+(Z.retrR * 100).toFixed(1)).replace('.', ',')}`);
    if (Z.perfect) {
      line(Z.confluence, C.zone, `★ ${nm(Z.lvR)} ≈ ${nm(Z.lvC)}`, LW.LineStyle.Solid);
    } else {
      line(Z.near, C.zone, `Bölge ${nm(Z.near)}`, LW.LineStyle.Solid);
      line(Z.far, C.zone, `Bölge ${nm(Z.far)}`, LW.LineStyle.Solid);
    }
    if (Z.front > 0) Z.legs.forEach((lv) => line(lv, C.zone, 'Emir', LW.LineStyle.Dotted));
    line(Z.stop, C.zoneStop, 'Stop (bölge)');
  }
  (L?.targets || Z?.targets || []).forEach((t, i) => line(t.price, C.target, `H${i + 1} ${t.k}`));

  // Görünüm: 0 noktasının biraz öncesinden sona
  const from = Math.max(0, p0.i - Math.round((all.length - p0.i) * 0.25));
  chart.timeScale().setVisibleLogicalRange({ from, to: all.length + 6 });

  const ro = new ResizeObserver(([e]) => {
    const r = e.contentRect;
    if (r.width > 0 && r.height > 0) chart.resize(Math.floor(r.width), Math.floor(r.height));
  });
  ro.observe(el);
  current = { chart, ro };
}

// [etiket, renk, çizgi sınıfı, plan ('' = her zaman)]
export const LEGEND = [
  ['Dalga yolu 0-1-A-B-2', C.wave, '', ''],
  ['Tetik B / giriş', C.entry, '', 'break'],
  ['Stop (B kırılımı)', C.stop, 'dash', 'break'],
  ['Limit bölge kenarları', C.zone, '', 'zone'],
  ['Stop (bölge)', C.zoneStop, 'dash', 'zone'],
  ['0 noktası (geçersizlik)', C.inval, 'dash', ''],
  ['Hedefler', C.target, 'dash', ''],
];

// ---------------------------------------------------------------- üçgen kırılımı
const T = { flat: '#f0c75e', slope: '#5299ec', prior: '#8f99aa', entry: '#3987e5', stop: '#ef5350', mm: '#b06cf0', fib: '#7cc4a8' };

/** Üçgen: düz kenar, eğik kenar (sona uzatılmış), dokunuş işaretleri, önceki hareket, giriş / stop / hedef çizgileri. */
export function drawTriangle(el, candles, live, t) {
  destroyChart();
  const LW = window.LightweightCharts;
  if (!LW) {
    el.innerHTML = '<p class="error">Grafik kütüphanesi yüklenemedi.</p>';
    return;
  }
  const prec = decimals(t.price);
  const pf = { type: 'price', precision: prec, minMove: 10 ** -prec };
  const chart = LW.createChart(el, {
    width: el.clientWidth,
    height: el.clientHeight,
    layout: { background: { type: 'solid', color: C.bg }, textColor: C.text, fontSize: 11, fontFamily: getComputedStyle(document.body).fontFamily },
    grid: { vertLines: { color: C.grid }, horzLines: { color: C.grid } },
    rightPriceScale: { borderColor: C.grid },
    timeScale: { borderColor: C.grid, timeVisible: true, secondsVisible: false, rightOffset: 6 },
    crosshair: { mode: LW.CrosshairMode.Normal },
    localization: { locale: 'tr-TR' },
  });
  const series = chart.addSeries(LW.CandlestickSeries, {
    upColor: C.up, downColor: C.down, borderUpColor: C.up, borderDownColor: C.down, wickUpColor: C.up, wickDownColor: C.down,
    priceFormat: pf,
  });
  const all = live ? [...candles, live] : candles;
  series.setData(all.map((c) => ({ time: sec(c.time), open: c.open, high: c.high, low: c.low, close: c.close })));
  const tm = (i) => sec(all[Math.min(i, all.length - 1)].time);
  const lastI = all.length - 1;
  const lineOpts = (color, style = LW.LineStyle.Solid, width = 2) => ({
    color, lineWidth: width, lineStyle: style, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false, priceFormat: pf,
  });

  // Düz kenar: kalıbın başından bugüne (ya da kırılıma)
  const endI = t.breakIdx >= 0 ? t.breakIdx : lastI;
  chart.addSeries(LW.LineSeries, lineOpts(T.flat)).setData([{ time: tm(t.first.i), value: t.level }, { time: tm(endI), value: t.level }]);
  // Eğik kenar: X'ten aynı eğimle bugüne
  const { i1, p1, i2, p2 } = t.slopeLine;
  const at = (i) => p1 + ((p2 - p1) * (i - i1)) / (i2 - i1);
  chart.addSeries(LW.LineSeries, lineOpts(T.slope)).setData([{ time: tm(i1), value: p1 }, { time: tm(endI), value: at(endI) }]);
  // Önceki hareket
  if (t.prior) {
    chart.addSeries(LW.LineSeries, lineOpts(T.prior, LW.LineStyle.Dashed, 1))
      .setData([{ time: tm(t.prior.from.i), value: t.prior.from.price }, { time: tm(t.prior.to.i), value: t.prior.to.price }]);
  }
  // Dokunuş işaretleri
  const marks = [
    ...t.flat.map((p) => ({ p, text: '' })),
    ...t.slope.map((p) => ({ p, text: '' })),
    ...(t.prior ? [{ p: t.prior.from, text: 'başlangıç' }] : []),
  ].sort((a, b) => a.p.i - b.p.i);
  const seenT = new Set();
  LW.createSeriesMarkers(series, marks.filter(({ p }) => !seenT.has(p.i) && seenT.add(p.i)).map(({ p, text }) => ({
    time: tm(p.i),
    position: p.type === 'H' ? 'aboveBar' : 'belowBar',
    shape: 'circle',
    color: p.type === (t.type === 'asc' ? 'H' : 'L') ? T.flat : T.slope,
    text,
  })));

  const L = t.levels;
  const line = (price, color, title, style = LW.LineStyle.Dashed) =>
    series.createPriceLine({ price, color, lineWidth: 1, lineStyle: style, axisLabelVisible: true, title });
  line(L.entry, T.entry, t.state === 'broken' ? 'Giriş (güncel)' : 'Kırılım', LW.LineStyle.Solid);
  line(L.stop, T.stop, 'Stop');
  for (const x of L.targets) {
    if (t.d * (x.price - L.entry) <= 0) continue; // girişin gerisindeki hedefleri çizme
    line(x.price, x.kind === 'mm' ? T.mm : T.fib, x.kind === 'mm' ? 'Ölçülü' : x.name.replace('Geri çekilme ', '').replace('İzdüşüm ', 'İzd. '));
  }

  const from = Math.max(0, (t.prior ? t.prior.from.i : t.first.i) - 10);
  chart.timeScale().setVisibleLogicalRange({ from, to: all.length + 6 });
  const ro = new ResizeObserver(([e]) => {
    const r = e.contentRect;
    if (r.width > 0 && r.height > 0) chart.resize(Math.floor(r.width), Math.floor(r.height));
  });
  ro.observe(el);
  current = { chart, ro };
}

export const TRI_LEGEND = [
  ['Düz kenar', T.flat, ''],
  ['Eğik kenar', T.slope, ''],
  ['Önceki hareket', T.prior, 'dash'],
  ['Kırılım / giriş', T.entry, ''],
  ['Stop', T.stop, 'dash'],
  ['Ölçülü hedef', T.mm, 'dash'],
  ['Fib hedefleri', T.fib, 'dash'],
];

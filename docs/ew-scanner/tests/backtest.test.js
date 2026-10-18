import { test } from 'node:test';
import assert from 'node:assert/strict';
import { build } from './helpers.js';
import { backtestCoin, summarize } from '../src/backtest/engine.js';
import { extendW1Start } from '../src/scan/wave3.js';
import { packTrades, unpackTrades } from '../src/backtest/store.js';

// 0 = 99,7 · 1 = 130,3 · A = 114,7 · B = 122,3 · C = 109,7 (bkz. zone.test.js), sonrası senaryoya göre.
const HEAD = [[0, 120], [20, 100], [40, 130], [52, 115], [60, 122], [70, 110]];
const OPTS = { sensitivities: [4.5], subDivisors: [2], minRR: 0 };
const run = (path, extra = {}) => {
  const candles = build(path);
  return backtestCoin(candles, { from: candles[0].time, to: candles[candles.length - 1].time, feePct: 0, slipPct: 0, ...extra, opts: { ...OPTS, ...extra.opts } });
};

test('B kırılımı: dalga 3 hedefe gider → kârlı işlem, giriş setup görüldükten sonra', () => {
  const r = run([...HEAD, [76, 119], [110, 165]], { exitTarget: 0, opts: { entryType: 'break' } });
  const b = r.trades.filter((t) => t.type === 'break');
  assert.equal(b.length, 1);
  assert.equal(b[0].outcome, 'hedef');
  assert.ok(b[0].R > 1);
  assert.ok(b[0].entryTime >= b[0].created);
});

test('limit bölge: C yakın kenarı doldurur, çöküşte uzak kenar da dolar → stop = −1R', () => {
  const r = run([...HEAD, [76, 119], [100, 85]], { opts: { entryType: 'zone' } });
  const z = r.trades.filter((t) => t.type === 'zone');
  assert.equal(z.length, 1);
  assert.equal(z[0].outcome, 'stop');
  assert.equal(z[0].filledLegs, 2);
  assert.equal(z[0].legs, 2);
  assert.ok(Math.abs(z[0].R + 1) < 1e-9, `R = ${z[0].R}`);
  assert.ok(z[0].entry < 111.4 && z[0].entry > 102.4); // iki bacağın ortalaması
  // Emir ancak dalga 2 sürerken (B onaylandıktan sonra) verilmiş olmalı; dolum sonra gelir
  assert.ok(z[0].entryTime > z[0].created);
});

test('komisyon R\'den düşülür', () => {
  const free = run([...HEAD, [76, 119], [100, 85]], { opts: { entryType: 'zone' } }).trades[0];
  const paid = run([...HEAD, [76, 119], [100, 85]], { feePct: 0.1, opts: { entryType: 'zone' } }).trades[0];
  assert.ok(paid.R < free.R);
});

test('aralık dışı: başlangıçtan önce oluşan setup işlem açmaz', () => {
  const candles = build([...HEAD, [76, 119], [110, 165]]);
  const r = backtestCoin(candles, { from: candles[100].time, to: candles[candles.length - 1].time, feePct: 0, slipPct: 0, opts: { ...OPTS, entryType: 'both' } });
  assert.equal(r.trades.length, 0);
});

test('summarize: kazanma oranı, kâr faktörü, düşüş', () => {
  const s = summarize([
    { R: 2, outcome: 'hedef', exitTime: 1 }, { R: -1, outcome: 'stop', exitTime: 2 }, { R: -1, outcome: 'stop', exitTime: 3 },
    { R: 3, outcome: 'hedef', exitTime: 4 }, { R: 5, outcome: 'open', exitTime: 5 },
  ]);
  assert.equal(s.n, 4);
  assert.equal(s.open, 1);
  assert.equal(s.winRate, 0.5);
  assert.equal(s.pf, 2.5);
  assert.equal(s.maxDD, 2);
  assert.equal(s.totalR, 3);
});

test('extendW1Start: iç dalgalanma dalga 1\'e dahil edilir, 1\'i geçen tepede durur', () => {
  const pv = (type, price) => ({ type, price });
  // L 50 · H 73 · L 60 · H 88 (=1)
  const piv = [pv('H', 95), pv('L', 50), pv('H', 73), pv('L', 60), pv('H', 88)];
  assert.equal(extendW1Start(piv, 3, piv[4], 0).price, 60);
  assert.equal(extendW1Start(piv, 3, piv[4], 1).price, 50);
  // Bir adım daha: önceki tepe 95 > 88 olduğu için zaten H→L sırası bozulur (j−2 = H değil L değil) → 50'de kalır
  assert.equal(extendW1Start(piv, 3, piv[4], 99).price, 50);
  // Önceki dip daha yüksekse genişletme yapılmaz
  const piv2 = [pv('L', 65), pv('H', 73), pv('L', 60), pv('H', 88)];
  assert.equal(extendW1Start(piv2, 2, piv2[3], 99).price, 60);
});

test('kayıt biçimi: işlemler sıkıştırılıp geri açılır', () => {
  const t = { coin: 'BTC', type: 'zone', dir: 'long', created: 1, entryTime: 2, exitTime: 3, entry: 100.123456789, exit: 90, stop: 89, target: 130, filledLegs: 1, legs: 2, R: -0.5, outcome: 'stop', bars: 4, sensitivity: 3, score: 60, retr: 0.6, timeRatio: 1.2, abc: 'zigzag', strength: 'Orta', inside: 2 };
  const [u] = unpackTrades(packTrades([t]));
  assert.equal(u.coin, 'BTC');
  assert.equal(u.strength, 'Orta');
  assert.ok(Math.abs(u.entry - t.entry) < 1e-3);
});

test('limit bölge: sadece yakın kenar doldu, dalga 3 hedefe gitti → kademeli girişte yarım pozisyon kârı', () => {
  const r = run([...HEAD, [76, 119], [110, 165]], { exitTarget: 0, opts: { entryType: 'zone' } });
  const z = r.trades.filter((t) => t.type === 'zone');
  assert.equal(z.length, 1);
  assert.equal(z[0].outcome, 'hedef');
  assert.equal(z[0].filledLegs, 1);
  assert.ok(z[0].R > 0);
});

test('limit emir aktifleştiğinde fiyat stop\'un ötesindeyse emir verilmez (işlem açılmaz)', () => {
  // Bölge emri B onaylanınca oluşur ama süre koşulu (minTimeRatio 3 → bar ~100) dolana kadar bekler;
  // bu sırada fiyat 0 noktasının (stop) altına iner ve orada kalır.
  const c = build([[0, 120], [20, 100], [40, 130], [52, 115], [60, 122], [66, 112], [75, 90], [130, 92]]);
  const r = backtestCoin(c, { from: c[0].time, to: c[c.length - 1].time, feePct: 0, slipPct: 0, maxPending: 80,
    opts: { sensitivities: [4.5], subDivisors: [2], minRR: 0, entryType: 'zone', minTimeRatio: 3 } });
  assert.equal(r.trades.length, 0);
  assert.ok(r.stats.cancelled['zone:stop seviyesi geçildi'] >= 1, JSON.stringify(r.stats.cancelled));
});

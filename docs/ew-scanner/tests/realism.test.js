// Kopya değişiklikleri (CHANGES.md > 2–4): funding, kayma, limitte "seviyeyi geçme" şartı, dönem evreni.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { build } from './helpers.js';
import { backtestCoin } from '../src/backtest/engine.js';
import { rankByPeriodVolume, LOOKBACK_DAYS } from '../src/backtest/universe.js';

// backtest.test.js ile aynı senaryolar: B kırılımı hedefe gider / limit bölge iki bacakla stop olur.
const HEAD = [[0, 120], [20, 100], [40, 130], [52, 115], [60, 122], [70, 110]];
const OPTS = { sensitivities: [4.5], subDivisors: [2], minRR: 0 };
const run = (candles, extra = {}) =>
  backtestCoin(candles, { from: candles[0].time, to: candles[candles.length - 1].time, feePct: 0, slipPct: 0, ...extra, opts: { ...OPTS, ...extra.opts } });
const breakWin = () => build([...HEAD, [76, 119], [110, 165]]);
const zoneStop = () => build([...HEAD, [76, 119], [100, 85]]);

test('kayma: piyasa/stop dolumlarında R düşer, limit bölge girişi kaymaz', () => {
  const b0 = run(breakWin(), { exitTarget: 0, opts: { entryType: 'break' } }).trades[0];
  const b1 = run(breakWin(), { exitTarget: 0, slipPct: 0.1, opts: { entryType: 'break' } }).trades[0];
  assert.ok(b1.entry > b0.entry, 'long B kırılımı girişi yukarı kaymalı');
  assert.ok(b1.R < b0.R);
  assert.equal(b1.exit, b0.exit, 'hedef limit emirdir, kaymaz');

  const z0 = run(zoneStop(), { opts: { entryType: 'zone' } }).trades[0];
  const z1 = run(zoneStop(), { slipPct: 0.1, opts: { entryType: 'zone' } }).trades[0];
  assert.equal(z1.entry, z0.entry, 'bölge bacakları limit emirdir');
  assert.ok(z1.exit < z0.exit, 'long stop çıkışı aşağı kaymalı');
  assert.ok(z1.R < -1 + 1e-9);
});

test('limit bölge: tam seviyede dönen fiyat emri doldurmaz, seviyeyi geçen doldurur', () => {
  // Tek bacak (yakın kenar), ön pay yok: dolum fiyatı tam olarak emir seviyesidir.
  const opts = { entryType: 'zone', zoneEntry: 'near', zoneFrontAtr: 0 };
  const ref = run(zoneStop(), { opts }).trades.find((t) => t.type === 'zone');
  assert.ok(ref, 'referans senaryoda bölge işlemi olmalı');
  const level = ref.entry;
  const fillIdx = zoneStop().findIndex((k) => k.time === ref.entryTime);
  // Dolum mumundan itibaren dipler `floor`un altına inmez.
  const clip = (floor) => zoneStop().map((k, i) => (i < fillIdx ? k : {
    ...k, low: Math.max(k.low, floor), open: Math.max(k.open, floor), close: Math.max(k.close, floor), high: Math.max(k.high, floor),
  }));
  const atLevel = (r) => r.trades.filter((t) => t.type === 'zone' && Math.abs(t.entry - level) < 1e-9);
  assert.equal(atLevel(run(clip(level), { opts })).length, 0, 'dokunuş dolum değildir');
  assert.equal(atLevel(run(clip(level - 1e-6), { opts })).length, 1, 'seviyenin altına inen fiyat doldurur');
});

test('funding: long öder, kapsam dışı işlem "uygulanamadı" diye işaretlenir', () => {
  const c = breakWin();
  const t0 = run(c, { exitTarget: 0, opts: { entryType: 'break' } }).trades[0];
  const during = c.filter((k) => k.time > t0.entryTime && k.time <= t0.exitTime).map((k) => ({ t: k.time, rate: 0.001 }));
  const paid = run(c, { exitTarget: 0, funding: during, opts: { entryType: 'break' } }).trades[0];
  assert.ok(paid.fundingR < 0, 'pozitif oranda long funding öder');
  assert.ok(Math.abs(paid.R - (t0.R + paid.fundingR)) < 1e-9, 'R = funding\'siz R + fundingR');
  assert.equal(paid.fundingCovered, during[0].t <= paid.entryTime ? true : false);

  const covered = [{ t: c[0].time, rate: 0 }, ...during];
  assert.equal(run(c, { exitTarget: 0, funding: covered, opts: { entryType: 'break' } }).trades[0].fundingCovered, true);
  assert.equal(run(c, { exitTarget: 0, opts: { entryType: 'break' } }).trades[0].fundingCovered, false, 'kayıt yoksa uygulandı sayılmaz');
});

test('rankByPeriodVolume: dönem hacmine göre sıralar, dönemde işlem görmeyeni eler', () => {
  const DAY = 86400e3, from = 100 * DAY;
  const days = (vol, count = LOOKBACK_DAYS) => Array.from({ length: count }, (_, i) => ({ time: from - (i + 1) * DAY, quoteVol: vol, volume: 0, close: 1 }));
  const cands = ['A', 'B', 'C', 'D'].map((coin) => ({ coin, instId: `${coin}-USDT-SWAP` }));
  const daily = new Map([
    ['A-USDT-SWAP', days(10)],
    ['B-USDT-SWAP', days(50)],
    ['C-USDT-SWAP', days(999, 5)], // dönemin çoğunda yok (yeni listelenmiş)
    ['D-USDT-SWAP', [...days(20), { time: from, quoteVol: 1e12, volume: 0, close: 1 }]], // başlangıç günü sayılmaz
  ]);
  const out = rankByPeriodVolume(cands, daily, from, 2);
  assert.deepEqual(out.map((x) => x.coin), ['B', 'D']);
  assert.equal(out[1].periodVolUsd, 20 * LOOKBACK_DAYS);
});

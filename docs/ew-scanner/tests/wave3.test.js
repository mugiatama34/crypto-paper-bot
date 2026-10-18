import { test } from 'node:test';
import assert from 'node:assert/strict';
import { build, mirror } from './helpers.js';
import { zigzag, fillAtr } from '../src/scan/zigzag.js';
import { atr } from '../src/data/indicators.js';
import { findWave3Setups, pickSetups } from '../src/scan/wave3.js';

// 0=100 (bar 20), 1=130 (bar 40), A=115 (52), B=122 (60), C=2=110 (70), sonra 119'a tepki.
// Dalga 1: 20 mum, dalga 2: 30 mum → süre oranı 1,5×. Geri çekilme ≈ %67.
const BASE = [[0, 120], [20, 100], [40, 130], [52, 115], [60, 122], [70, 110], [76, 119]];
const OPTS = { sensitivities: [4.5] };

const atrOf = (c) => fillAtr(atr(c.map((x) => x.high), c.map((x) => x.low), c.map((x) => x.close)));

test('zigzag: dönüş noktalarını sırayla ve doğru yerde bulur', () => {
  const c = build(BASE);
  const { pivots } = zigzag(c, atrOf(c), 2);
  assert.deepEqual(pivots.map((p) => p.type + p.i), ['H0', 'L19', 'H39', 'L51', 'H59', 'L69']);
});

test('long dalga-3 setup: ABC zigzag, süre oranı, seviyeler', () => {
  const c = build(BASE);
  const [s] = findWave3Setups(c, OPTS);
  assert.ok(s, 'setup bulunmalı');
  assert.equal(s.direction, 'long');
  assert.equal(s.abc.kind, 'zigzag');
  assert.equal(s.state, 'ready');
  assert.equal(s.durations.D1, 20);
  assert.equal(s.durations.D2, 30);
  assert.ok(Math.abs(s.timeRatio - 1.5) < 1e-9);
  const { p0, p1, p2 } = s.points;
  // Hedef 1 = dalga 2 ucu × (dalga 1 oranı), log ölçek
  assert.ok(Math.abs(s.levels.targets[0].price - p2.price * (p1.price / p0.price)) < 1e-9);
  assert.equal(s.levels.trigger, s.abc.B.price);
  assert.ok(s.levels.stop < p2.price);
  assert.ok(s.rr > 2);
  assert.equal(pickSetups([s]).length, 1);
});

test('short setup aynı yapının aynası', () => {
  const c = mirror(build(BASE), 260);
  const [s] = findWave3Setups(c, OPTS);
  assert.ok(s);
  assert.equal(s.direction, 'short');
  assert.equal(s.abc.kind, 'zigzag');
  assert.ok(s.levels.stop > s.points.p2.price);
  assert.ok(s.levels.targets[0].price < s.points.p2.price);
  assert.equal(findWave3Setups(c, { ...OPTS, direction: 'long' }).length, 0);
});

test('dalga 2 dalga 1\'den kısa sürerse elenir', () => {
  // Dalga 1 = 40 mum, dalga 2 = 30 mum → oran 0,75
  const c = build([[0, 120], [20, 100], [60, 130], [72, 115], [80, 122], [90, 110], [98, 121]]);
  const O = { sensitivities: [6] }; // dalga 1 yavaş → ATR küçük; B ana pivot olmasın diye daha kaba derece
  assert.equal(findWave3Setups(c, O).length, 0);
  // Süre koşulu gevşetilirse aynı yapı geçer (elemenin sebebi süre)
  assert.equal(findWave3Setups(c, { ...O, minTimeRatio: 0.5 }).length, 1);
});

test('dalga 2 ABC değilse (tek bacak) elenir', () => {
  const c = build([[0, 120], [20, 100], [40, 130], [70, 110], [76, 120]]);
  assert.equal(findWave3Setups(c, OPTS).length, 0);
  const [s] = findWave3Setups(c, { ...OPTS, requireAbc: false });
  assert.ok(s);
  assert.equal(s.abc, null);
});

test('fiyat dalga 2 ucunu kırarsa geçersiz, hedef 1 aşılırsa kaçtı', () => {
  const c = build(BASE);
  const live = { ...c[c.length - 1], low: 105, high: 119, close: 106, confirmed: false };
  assert.equal(findWave3Setups(c, OPTS, live)[0].state, 'invalid');
  const live2 = { ...c[c.length - 1], low: 118, high: 150, close: 149, confirmed: false };
  assert.equal(findWave3Setups(c, OPTS, live2)[0].state, 'gone');
  const live3 = { ...c[c.length - 1], low: 118, high: 124, close: 123, confirmed: false };
  const s = findWave3Setups(c, OPTS, live3)[0];
  assert.equal(s.state, 'triggered');
  assert.equal(s.levels.entry, 123); // tetiklenince giriş = güncel fiyat
});

test('geri çekilme bandı dışındaysa elenir', () => {
  const c = build(BASE);
  assert.equal(findWave3Setups(c, { ...OPTS, retrMax: 0.6 }).length, 0);
});

test('R:R filtresi pickSetups ile uygulanır', () => {
  const c = build(BASE);
  const all = findWave3Setups(c, OPTS);
  assert.equal(pickSetups(all, { minRR: 50 }).length, 0);
});

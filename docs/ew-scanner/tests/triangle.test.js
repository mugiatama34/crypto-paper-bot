import { test } from 'node:test';
import assert from 'node:assert/strict';
import { build, mirror } from './helpers.js';
import { findTriangles, pickTriangles, reachOf } from '../src/scan/triangle.js';

// Düşüş 130 → 100, sonra yükselen üçgen: tepeler ≈112, dipler 100 → 104 → 107 (±0,3 fitil).
const FORMING = [[0, 130], [30, 100], [45, 112], [60, 104], [75, 112.2], [90, 107], [100, 111.9], [108, 109]];
const O = { sensitivities: [2] };
const one = (path, opts = O) => {
  const r = findTriangles(build(path), opts);
  assert.equal(r.length, 1, `tek üçgen beklenirdi, ${r.length} bulundu`);
  return r[0];
};

test('yükselen üçgen: düz tepeler + yükselen dipler, düşüş sonrası bağlam', () => {
  const t = one(FORMING);
  assert.equal(t.type, 'asc');
  assert.equal(t.direction, 'long');
  assert.equal(t.state, 'forming');
  assert.ok(Math.abs(t.level - 112.5) < 0.31); // en yüksek tepe (fitil dahil)
  assert.ok(t.touches.flat >= 2 && t.touches.slope >= 2);
  assert.equal(t.context, 'after-down');
  assert.equal(t.fibMode, 'retr');
  assert.ok(Math.abs(t.prior.from.price - 130.3) < 1e-9 && Math.abs(t.prior.to.price - 99.7) < 1e-9);
  // Stop son dibin altında, giriş düz kenarın üstünde
  assert.ok(t.levels.stop < 107 && t.levels.entry > t.level);
});

test('hedefler: ölçülü hareket = kırılım + yükseklik; Fib = önceki düşüşün geri çekilmeleri', () => {
  const t = one(FORMING);
  const L = Object.fromEntries(t.levels.targets.map((x) => [x.key, x.price]));
  assert.ok(Math.abs(L.mm - (t.levels.breakLevel + t.height)) < 1e-9);
  assert.ok(Math.abs(L.f618 - (99.7 + 0.618 * 30.6)) < 1e-9);
  assert.ok(Math.abs(L.f100 - 130.3) < 1e-9);
  // Ana hedef seçimi
  const f618 = one(FORMING, { ...O, triTarget: 'f618' });
  assert.equal(f618.levels.main.key, 'f618');
});

test('kırılım ve bozulma durumları', () => {
  assert.equal(one([...FORMING, [116, 121]]).state, 'broken');
  assert.equal(one([...FORMING, [116, 100]]).state, 'failed');
  // Kırılımdan sonra recentBars'tan uzun süre geçtiyse "eski"
  assert.equal(one([...FORMING, [116, 121], [160, 125]]).state, 'old');
});

test('alçalan üçgen aynası: düz dipler + alçalan tepeler, yükseliş sonrası, aşağı kırılım', () => {
  const r = findTriangles(mirror(build([...FORMING, [116, 121]]), 240), O);
  assert.equal(r.length, 1);
  assert.equal(r[0].type, 'desc');
  assert.equal(r[0].direction, 'short');
  assert.equal(r[0].state, 'broken');
  assert.equal(r[0].context, 'after-up');
  assert.ok(r[0].levels.stop > r[0].level);
});

test('düz olmayan tepeler ya da alçalan dipler üçgen değildir', () => {
  // Tepeler yükseliyor (kanal)
  assert.equal(findTriangles(build([[0, 130], [30, 100], [45, 112], [60, 104], [75, 117], [90, 107], [100, 122], [108, 112]]), O)
    .filter((t) => t.type === 'asc').length, 0);
  // Dipler alçalıyor
  assert.equal(findTriangles(build([[0, 130], [30, 100], [45, 112], [60, 97], [75, 112.2], [90, 94], [100, 111.9], [108, 105]]), O)
    .filter((t) => t.type === 'asc').length, 0);
});

test('panel listesi: aktif durumlar ve R:R filtresi', () => {
  const t = one(FORMING);
  assert.equal(pickTriangles([t]).length, 1);
  assert.equal(pickTriangles([t], { triMinRR: 99 }).length, 0);
  assert.equal(pickTriangles([{ ...t, state: 'failed' }]).length, 0);
});

test('nereye kadar gitti: ölçülü hareket katı ve önceki düşüşün geri alınan oranı', () => {
  const t = one(FORMING);
  const r = reachOf(t, 99.7 + 0.618 * 30.6);
  assert.ok(Math.abs(r.mfeFib - 0.618) < 1e-9);
  const r2 = reachOf(t, t.levels.breakLevel + 2 * t.height);
  assert.ok(Math.abs(r2.mfeH - 2) < 1e-9);
});

import { backtestCoin } from '../src/backtest/engine.js';

test('backtest üçgen modu: kırılım kapanışında giriş, stop görülene kadar en uç noktayı kaydeder', () => {
  // Yükselen üçgen kırılır, 126'ya kadar gider, sonra 100'e çöker (stop).
  const c = build([...FORMING, [116, 121], [130, 126], [160, 100]]);
  const r = backtestCoin(c, { from: c[0].time, to: c[c.length - 1].time, feePct: 0, exitTarget: 0,
    opts: { mode: 'triangle', sensitivities: [2], triMinRR: 0 } });
  const tr = r.trades.filter((t) => t.type === 'triangle');
  assert.equal(tr.length, 1, JSON.stringify(r.stats));
  const t = tr[0];
  assert.equal(t.dir, 'long');
  assert.equal(t.pattern, 'asc');
  assert.equal(t.context, 'after-down');
  // En uç nokta ≈ 126,3 (fitil): önceki düşüşün (130,3 → 99,7) geri alınan oranı ≈ 0,87
  assert.ok(Math.abs(t.mfeFib - (126.3 - 99.7) / 30.6) < 0.02, `mfeFib ${t.mfeFib}`);
  assert.ok(t.mfeH > 1); // ölçülü hareketi (1× yükseklik) aştı
  // Giriş, kırılım mumundan önce olamaz
  assert.ok(t.entryTime >= t.created);
});

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { build } from './helpers.js';
import { findWave3Setups, activePlans } from '../src/scan/wave3.js';

// Pivotlar (±0,3 fitil): 0 = 99,7 (bar 19), 1 = 130,3 (bar 39), A = 114,7, B = 122,3, C = 109,7.
// Bölge (doğrusal): %61,8 = 130,3 − 0,618 × 30,6 ≈ 111,39 (yakın) · C = 1,272A = 122,3 − 1,272 × 15,6 ≈ 102,46 (uzak)
const BASE = [[0, 120], [20, 100], [40, 130], [52, 115], [60, 122], [70, 110], [76, 119]];
// Ön pay ve yaklaşma uyarısı kapalı: seviyeler tam fiyattan test edilir (ayrı testler aşağıda).
const OPTS = { sensitivities: [4.5], entryType: 'both', zoneFrontAtr: 0, approachAtr: 0 };
const NEAR = 130.3 - 0.618 * 30.6;
const FAR = 122.3 - 1.272 * 15.6;

test('bölge kenarları: C = 1,272A ve dalga 2 = %61,8; yakın/uzak sırası', () => {
  const s = findWave3Setups(build(BASE), OPTS).find((x) => x.phase === 'complete');
  assert.ok(s?.zone);
  assert.ok(Math.abs(s.zone.near - NEAR) < 1e-9);
  assert.ok(Math.abs(s.zone.far - FAR) < 1e-9);
  assert.equal(s.zone.lvR, s.zone.near);
  // Kenar dışındaki çakışan seviyeler sayılır (ör. C = 1A, %78,6, %88,6 bölge içinde)
  assert.ok(s.zone.inside >= 2);
});

test('tamamlanmış dalga 2: C yakın kenarı geçti → kademeli girişin yarısı doldu', () => {
  const s = findWave3Setups(build(BASE), OPTS).find((x) => x.phase === 'complete');
  assert.equal(s.zone.state, 'filled');
  assert.equal(s.zone.filledLegs, 1);
  assert.ok(Math.abs(s.zone.entry - NEAR) < 1e-9);
  // Stop varsayılanı: dalga 1 başı (0) − tampon
  assert.ok(s.zone.stop < 99.7 && s.zone.stop > 99);
  assert.ok(s.zone.rr > 0);
  assert.deepEqual(activePlans(s, OPTS).map((p) => p.type).sort(), ['break', 'zone']);
  // Uzak kenar girişi seçilirse emir dolmaz → bölge kaçtı
  const far = findWave3Setups(build(BASE), { ...OPTS, zoneEntry: 'far' }).find((x) => x.phase === 'complete');
  assert.equal(far.zone.state, 'missed');
  assert.deepEqual(activePlans(far, OPTS).map((p) => p.type), ['break']);
});

test('B kırılımı modunda bölge hesaplanmaz, bölge modunda B planı aktif sayılmaz', () => {
  const b = findWave3Setups(build(BASE), { sensitivities: [4.5], entryType: 'break' });
  assert.equal(b.length, 1);
  assert.equal(b[0].zone, null);
  const z = findWave3Setups(build(BASE), { sensitivities: [4.5], entryType: 'zone' }).find((x) => x.phase === 'complete');
  assert.deepEqual(activePlans(z, { entryType: 'zone' }).map((p) => p.type), ['zone']);
});

test('%88,6 stop seçilirse uzak kenar stop\'un ötesinde kalır → bölge planı yok', () => {
  const s = findWave3Setups(build(BASE), { ...OPTS, zoneStop: 'r886' }).find((x) => x.phase === 'complete');
  assert.equal(s.zone, null);
});

test('dalga 2 sürüyor: C oluşurken bölge bekleniyor, yakın kenara gelince doldu', () => {
  const waiting = findWave3Setups(build([[0, 120], [20, 100], [40, 130], [52, 115], [60, 122], [66, 112]]), OPTS)
    .find((x) => x.phase === 'forming');
  assert.ok(waiting, 'süren dalga 2 bulunmalı');
  assert.equal(waiting.direction, 'long');
  assert.equal(waiting.zone.state, 'waiting');
  assert.equal(waiting.points.p2, null);
  assert.ok(Math.abs(waiting.zone.near - NEAR) < 1e-9);

  const filled = findWave3Setups(build([[0, 120], [20, 100], [40, 130], [52, 115], [60, 122], [68, 110.5]]), OPTS)
    .find((x) => x.phase === 'forming');
  assert.equal(filled.zone.state, 'filled');
});

test('süre koşulu: süre(2) > süre(1) dolmadan bölgeye gelinirse emir sayılmaz', () => {
  const path = [[0, 120], [20, 100], [40, 130], [52, 115], [60, 122], [68, 110.5]];
  // minTimeRatio 1,5 → emir bar 39 + 30 + 1 = 70'ten itibaren aktif; dip bar 67'de
  const req = findWave3Setups(build(path), { ...OPTS, minTimeRatio: 1.5 }).find((x) => x.phase === 'forming');
  assert.equal(req.zone.state, 'waiting');
  assert.equal(req.zone.pendingTime, true);
  assert.equal(req.zone.activeFrom, 70);
  const ign = findWave3Setups(build(path), { ...OPTS, minTimeRatio: 1.5, zoneTime: 'ignore' }).find((x) => x.phase === 'forming');
  assert.equal(ign.zone.state, 'filled');
});

// ---------------------------------------------------------------- mükemmel çakışma (BAT benzeri)
// 0 = 99,7 · 1 = 130,3 · A = 118,3 · B = 126,65 → %61,8 = 111,389 ve C = 1,272A = 111,386 (fark ≈ 0,002 ATR).
// C 111,57'de dönüyor: seviyeye ~0,1 ATR kala.
import { compareSetups } from '../src/scan/wave3.js';
import { backtestCoin } from '../src/backtest/engine.js';
const PERFECT = [[0, 120], [20, 100], [40, 130], [50, 118.6], [58, 126.35], [70, 111.87], [80, 128]];
const P_OPTS = { sensitivities: [8], entryType: 'both' };

test('mükemmel: iki kenar ≤ 0,5 ATR → tek çakışma seviyesi, tek emir bacağı', () => {
  const s = findWave3Setups(build(PERFECT), P_OPTS).find((x) => x.phase === 'complete');
  assert.equal(s.zone.perfect, true);
  assert.equal(s.zone.strength, 'Mükemmel');
  assert.ok(s.zone.widthAtr < 0.01);
  assert.equal(s.zone.legs.length, 1);
  assert.ok(Math.abs(s.zone.confluence - (s.zone.lvC + s.zone.lvR) / 2) < 1e-12);
  // Eşik küçültülürse aynı yapı mükemmel sayılmaz
  const strict = findWave3Setups(build(PERFECT), { ...P_OPTS, perfectAtr: 0.001 }).find((x) => x.phase === 'complete');
  assert.equal(strict.zone.perfect, false);
});

test('ön pay: C seviyeye birkaç tik kala dönünce ön paylı emir dolar, tam seviyedeki dolmaz', () => {
  const exact = findWave3Setups(build(PERFECT), { ...P_OPTS, zoneFrontAtr: 0 }).find((x) => x.phase === 'complete');
  assert.equal(exact.zone.state, 'missed');
  const front = findWave3Setups(build(PERFECT), { ...P_OPTS, zoneFrontAtr: 0.15 }).find((x) => x.phase === 'complete');
  assert.equal(front.zone.state, 'filled');
  // Long'da emir seviyenin üstünde (fiyat yönüne doğru)
  assert.ok(front.zone.legs[0] > front.zone.confluence);
  assert.ok(Math.abs(front.zone.legs[0] - front.zone.confluence - 0.15 * front.zone.atr) < 1e-9);
});

test('yaklaşma: dalga 2 sürerken fiyat emre 0,5 ATR\'den yakınsa "Seviyeye yaklaştı"', () => {
  const path = [[0, 120], [20, 100], [40, 130], [50, 118.6], [58, 126.35], [70, 112.3]];
  const s = findWave3Setups(build(path), P_OPTS).find((x) => x.phase === 'forming');
  assert.ok(s, 'süren dalga 2 bulunmalı');
  assert.equal(s.zone.perfect, true);
  assert.equal(s.zone.state, 'approaching');
  assert.deepEqual(activePlans(s, P_OPTS).map((p) => p.type), ['zone']);
  const off = findWave3Setups(build(path), { ...P_OPTS, approachAtr: 0 }).find((x) => x.phase === 'forming');
  assert.equal(off.zone.state, 'waiting');
});

test('sıralama ve filtre: aktif mükemmel setup önde; "sadece mükemmel" diğerlerini gizler', () => {
  const perfect = findWave3Setups(build(PERFECT), P_OPTS).find((x) => x.phase === 'complete');
  const normal = findWave3Setups(build(BASE), OPTS).find((x) => x.phase === 'complete');
  const o = { entryType: 'both', minRR: 0 };
  assert.ok(compareSetups(perfect, { ...normal, score: 100 }, o) < 0);
  assert.equal(activePlans(normal, { ...o, onlyPerfect: true }).length, 0);
  assert.ok(activePlans(perfect, { ...o, onlyPerfect: true }).length > 0);
});

test('backtest: sadece mükemmel modunda yalnızca çakışmalı setup işlem açar ve işlem bunu kaydeder', () => {
  const bt = (path, opts) => {
    const c = build(path);
    return backtestCoin(c, { from: c[0].time, to: c[c.length - 1].time, feePct: 0, opts: { subDivisors: [2, 2.5, 3, 4], minRR: 0, entryType: 'zone', onlyPerfect: true, ...opts } });
  };
  assert.equal(bt([...BASE.slice(0, 6), [76, 119], [100, 85]], { sensitivities: [4.5] }).trades.length, 0);
  const r = bt([...PERFECT.slice(0, 5), [70, 111.87], [80, 128], [110, 170]], { sensitivities: [8] });
  const z = r.trades.filter((t) => t.type === 'zone');
  assert.equal(z.length, 1);
  assert.equal(z[0].perfect, true);
  assert.ok(z[0].widthAtr < 0.5);
  assert.ok(z[0].R > 0);
});

test('seviye bazlı giriş: sadece %61,8 ya da sadece C = 1,272A', () => {
  const r618 = findWave3Setups(build(BASE), { ...OPTS, zoneEntry: 'r618' }).find((x) => x.phase === 'complete');
  assert.deepEqual(r618.zone.legs.map((v) => +v.toFixed(6)), [+NEAR.toFixed(6)]);
  const c127 = findWave3Setups(build(BASE), { ...OPTS, zoneEntry: 'c127' }).find((x) => x.phase === 'complete');
  assert.deepEqual(c127.zone.legs.map((v) => +v.toFixed(6)), [+FAR.toFixed(6)]);
  // %88,6 stop: 1,272A seviyesi stop'un ötesinde → o plan yok, ama %61,8 planı geçerli
  assert.equal(findWave3Setups(build(BASE), { ...OPTS, zoneEntry: 'c127', zoneStop: 'r886' }).find((x) => x.phase === 'complete').zone, null);
  assert.ok(findWave3Setups(build(BASE), { ...OPTS, zoneEntry: 'r618', zoneStop: 'r886' }).find((x) => x.phase === 'complete').zone);
});

test('min. stop mesafesi: eşiğin altındaki plan aktif değil, backtest emir açmaz; 0 = kapalı', () => {
  const s = findWave3Setups(build(BASE), OPTS).find((x) => x.phase === 'complete');
  const o = { entryType: 'break', minRR: 0 };
  const pct = (Math.abs(s.levels.entry - s.levels.stop) / s.levels.entry) * 100; // ≈ %10,7
  assert.equal(activePlans(s, { ...o, minRiskPct: pct + 1 }).length, 0);
  assert.equal(activePlans(s, { ...o, minRiskPct: pct - 1 }).length, 1);
  assert.equal(activePlans(s, { ...o, minRiskPct: 0 }).length, 1);
  const c = build([...BASE, [110, 165]]);
  const bt = (minRiskPct) => backtestCoin(c, { from: c[0].time, to: c[c.length - 1].time, feePct: 0, opts: { sensitivities: [4.5], minRR: 0, entryType: 'break', minRiskPct } }).trades.length;
  assert.equal(bt(0), 1);
  assert.equal(bt(50), 0);
});

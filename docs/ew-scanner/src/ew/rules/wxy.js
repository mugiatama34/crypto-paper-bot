// WXY (çift düzeltme) kuralları. Noktalar: 0-W-X-Y.
// Varyant: shape = 'double-zigzag' (keskin) | 'combination' (yatay / double three).
import { pass, fail, warn, info, pct, times, inBand, nearest, ratio } from '../engine/helpers.js';

const ALL = ['fp', 'neely', 'course'];
const FP = 'Frost & Prechter (EWP) Ders 6, 9';
const TD = 'tradedevils EW Patterns';
const COURSE = 'Rules.docx (kurs)';
const COURSE_G = 'Guidelines.docx (kurs)';
const isDZ = (c) => c.variant.shape === 'double-zigzag';

export const wxyRules = [
  {
    id: 'WXY-R1', type: 'rule', patterns: ['wxy'], variants: ['double-zigzag'], sets: ALL, need: 3,
    name: "Çift zigzag: X, W'nin başlangıcını geçemez",
    description: "Keskin çift zigzag'da X dalgası W'nin tamamını geri alamaz.",
    source: `${TD}; ${COURSE}`,
    check(c) {
      const r = c.L(2) / c.L(1);
      return c.past(2, 0) > c.eps ? pass(`X, W'yi ${pct(r)} oranında geri aldı.`) : fail(`X, W'nin başlangıcını aştı (${pct(r)}). Kombinasyon olabilir.`);
    },
  },
  {
    id: 'WXY-R2', type: 'rule', patterns: ['wxy'], sets: ALL, need: 4,
    name: 'Y, W ile aynı yönde ilerler',
    description: 'Y dalgası W ile aynı yönde hareket eder ve X\'ten kısa olamaz (Y üçgen değilse).',
    source: `${FP}; ${COURSE}`,
    check(c) {
      return c.L(3) >= c.L(2) * 0.999 ? pass(`Y = ${times(c.L(3) / c.L(2))} X.`) : fail("Y, X'ten kısa kaldı.");
    },
  },
  {
    id: 'WXY-G1', type: 'guideline', patterns: ['wxy'], sets: ALL, need: 3,
    name: "X: çift zigzag'da W'nin %38,2–50'si, kombinasyonda en az %90'ı (en fazla 2W)",
    description: "Keskin çift zigzag'da X genellikle W'nin %38,2–50'sini geri alır. Yatay kombinasyonda X, W'nin en az %90'ı ve en fazla iki katı olmalıdır.",
    source: TD,
    check(c) {
      const r = c.L(2) / c.L(1);
      if (isDZ(c)) {
        const row = ratio('X / W', r, '%38,2–50', inBand(r, 0.382, 0.5, 0.04));
        return row.ok ? pass(`X = ${pct(r)}.`, row) : warn(`X = ${pct(r)}; tipik %38,2–50.`, row, inBand(r, 0.3, 0.786));
      }
      const row = ratio('X / W', r, '%90–200', inBand(r, 0.9, 2));
      return row.ok ? pass(`X = ${pct(r)}.`, row) : warn(`X = ${pct(r)}; kombinasyon için %90–200 beklenir.`, row);
    },
  },
  {
    id: 'WXY-G2', type: 'guideline', patterns: ['wxy'], sets: ALL, need: 4,
    name: 'Y = W veya 0,618W (çift zigzag\'da 1,272 / 1,618W de olur)',
    description: 'Ana dalgalar (W ve Y) genellikle 1:1 ya da 1:0,618 oranındadır. Keskin çift zigzag\'da 1,272 ve 1,618 de sık görülür.',
    source: `${TD}; ${COURSE_G}`,
    weight: 1.5,
    check(c) {
      const r = c.L(3) / c.L(1);
      const targets = isDZ(c) ? [1, 1.272, 1.618, 0.618, 0.786] : [1, 0.618];
      const m = nearest(r, targets, 0.07);
      const row = ratio('Y / W', r, isDZ(c) ? '1× / 1,272× / 1,618× / 0,618×' : '1× / 0,618×', !!m, false);
      return m ? pass(`Y = ${times(r)} W (≈ ${times(m.target)}).`, row) : warn(`Y = ${times(r)} W; tipik ilişkiye uzak.`, row, true);
    },
  },
  {
    id: 'WXY-G3', type: 'guideline', patterns: ['wxy'], sets: ALL, need: 4,
    name: 'Çift zigzag ilerler, kombinasyon yatay kalır',
    description: "Çift zigzag'da Y, W'nin bitişini belirgin şekilde aşar. Kombinasyonlar yataydır ve ana trende karşı eğim göstermez (F&P: aksi bir örnek hiç görülmedi).",
    source: `${FP} Ders 9; ${TD}`,
    check(c) {
      const prog = c.past(3, 1) / c.L(1);
      if (isDZ(c)) return prog > 0.1 ? pass(`Y, W'nin bitişini W'nin ${pct(prog)} kadarı aştı.`) : warn("Y, W'nin bitişini belirgin şekilde aşmadı; çift zigzag için zayıf.");
      return Math.abs(prog) <= 0.5 ? pass(`Yapı yatay (Y, W bitişinden W'nin ${pct(prog)} kadarı uzakta).`) : warn(`Kombinasyon eğimli görünüyor (Y, W bitişinden W'nin ${pct(prog)} kadarı uzakta).`);
    },
  },

  // ---------- Kurs ----------
  {
    id: 'CRS-WXY-DZ', type: 'rule', patterns: ['wxy'], variants: ['double-zigzag'], sets: ['course'], need: 3,
    name: "Çift zigzag: X W'nin %20'si ile %100'ü arasında, Y W'nin %90'ı ile 5 katı arasında",
    description: "X < W ve X ≥ 0,2W olmalı; X'in süresi W'nin 5 katını aşamaz. Y > 0,9W ve Y < 5W olmalı (fiyat ve zaman).",
    source: COURSE,
    check(c) {
      const x = c.L(2) / c.L(1);
      if (x < 0.2 || x >= 1) return fail(`X = ${pct(x)}; %20–100 arası olmalı.`);
      if (c.D(2) / (c.D(1) || 1) > 5) return fail("X süresi W'nin 5 katını aşıyor.");
      if (c.n >= 4) {
        const y = c.L(3) / c.L(1);
        if (y <= 0.9 || y >= 5) return fail(`Y = ${times(y)} W; 0,9–5× olmalı.`);
        if (c.D(3) / (c.D(1) || 1) > 5) return fail("Y süresi W'nin 5 katını aşıyor.");
      }
      return pass('Kurs çift zigzag sınırları sağlandı.');
    },
  },
  {
    id: 'CRS-WXY-CB', type: 'rule', patterns: ['wxy'], variants: ['combination'], sets: ['course'], need: 3,
    name: "Kombinasyon: X W'nin %70'i ile %150'si arasında, Y ≤ 5W ve ≤ 5X",
    description: "Double flat ailesinde X, W'nin en az %70'ini geri alır ve %150'sini aşamaz; süresi W'nin 10 katını geçmez. Y, W ve X'in 5 katından büyük olamaz (fiyat ve zaman).",
    source: COURSE,
    check(c) {
      const x = c.L(2) / c.L(1);
      if (x < 0.7 || x > 1.5) return fail(`X = ${pct(x)}; %70–150 arası olmalı.`);
      if (c.D(2) / (c.D(1) || 1) > 10) return fail("X süresi W'nin 10 katını aşıyor.");
      if (c.n >= 4) {
        if (c.L(3) > 5 * c.L(1) || c.L(3) > 5 * c.L(2)) return fail("Y, W veya X'in 5 katını aşıyor.");
        if (c.D(3) > 5 * (c.D(1) || 1) || c.D(3) > 5 * (c.D(2) || 1)) return fail("Y süresi W veya X'in 5 katını aşıyor.");
      }
      return pass('Kurs kombinasyon sınırları sağlandı.');
    },
  },
  {
    id: 'CRS-WXY-G1', type: 'guideline', patterns: ['wxy'], sets: ['course'], need: 3,
    name: "Çift zigzag'da X W'nin %30–70'i; kombinasyonda %95–140'ı",
    description: 'Kurs kılavuzlarına göre tipik X oranları.',
    source: COURSE_G,
    check(c) {
      const x = c.L(2) / c.L(1);
      const [lo, hi] = isDZ(c) ? [0.3, 0.7] : [0.95, 1.4];
      return inBand(x, lo, hi) ? pass(`X = ${pct(x)}.`) : warn(`X = ${pct(x)}; tipik ${pct(lo, 0)}–${pct(hi, 0)}.`, null, true);
    },
  },
  {
    id: 'WXY-H1', type: 'reminder', patterns: ['wxy'], sets: ALL, need: 3,
    name: 'İç yapı ve sınırlar',
    description: "W ve Y'nin her biri basit bir düzeltmedir (çift zigzag'da ikisi de zigzag). X herhangi bir düzeltme olabilir. Üçgen yalnızca son konumda (Y veya Z) görülür. Bu araç triple (Z) yapıları doğrudan saymaz.",
    source: `${FP}; ${TD}; ${COURSE}`,
    check: (c) => info(isDZ(c)
      ? "Çift zigzag: W ve Y'nin ikisi de zigzag (5-3-5) olmalı. X'in, W'nin başlangıcından W'nin B'sine çekilen çizgiyi kırması beklenir."
      : "Kombinasyon: W ve Y farklı tipte olmalı (W zigzag ise Y zigzag olamaz). Üçgen yalnızca Y konumunda olabilir. Bu yapılar devam formasyonudur."),
  },
];

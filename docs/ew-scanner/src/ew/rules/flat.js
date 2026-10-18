// Flat (3-3-5) kuralları. Noktalar: 0-A-B-C.
// Alt tür (regular / expanded / running) patterns.js içindeki classify ile belirlenir.
import { pass, fail, warn, info, pct, times, inBand, nearest, ratio } from '../engine/helpers.js';

const ALL = ['fp', 'neely', 'course'];
const FP = 'Frost & Prechter (EWP) Ders 7';
const TD = 'tradedevils EW Patterns';
const COURSE = 'Rules.docx (kurs)';
const COURSE_G = 'Guidelines.docx (kurs)';

export function flatSubtype(c) {
  if (c.n < 4) return c.L(2) / c.L(1) > 1.05 ? 'expanded?' : 'regular?';
  const b = c.L(2) / c.L(1);
  const cPastA = c.past(3, 1) > c.eps;
  if (b > 1.05 && cPastA) return 'expanded';
  if (b >= 1.0 && !cPastA) return 'running';
  return 'regular';
}

const bMin = (min, set, src) => ({
  id: `FL-R1-${set.toUpperCase()}`, type: 'rule', patterns: ['flat'], sets: [set], need: 3,
  name: `B, A'nın en az ${pct(min)} kadarını geri alır`,
  description: `Flat'te B dalgası A'yı neredeyse tamamen geri alır. Bu kural setinde alt sınır ${pct(min)}.`,
  source: src,
  check(c) {
    const r = c.L(2) / c.L(1);
    return r >= min - 0.0005 ? pass(`B = A'nın ${pct(r)} kadarı.`) : fail(`B = A'nın yalnızca ${pct(r)} kadarı; flat için en az ${pct(min)}. Zigzag dene.`);
  },
});

export const flatRules = [
  bMin(0.9, 'fp', `${FP} (B en az %90)`),
  bMin(0.618, 'neely', 'Neely, Mastering Elliott Wave 5-18 (b ≥ %61,8)'),
  bMin(0.7, 'course', COURSE),
  {
    id: 'FL-G1', type: 'guideline', patterns: ['flat'], sets: ALL, need: 3,
    name: "B, A'nın %100–138,2'si (2 katını geçmez)",
    description: "B genellikle A'nın %100–138,2'si kadardır ve A'nın iki katını aşmaz.",
    source: `${TD}; ${COURSE_G}`,
    check(c) {
      const r = c.L(2) / c.L(1);
      const row = ratio('B / A', r, '%90–138,2', inBand(r, 0.9, 1.382, 0.02));
      if (row.ok) return pass(`B = ${pct(r)}.`, row);
      if (r > 2) return warn(`B = ${pct(r)}: A'nın 2 katını aştı, flat yorumu zayıf.`, row);
      return warn(`B = ${pct(r)}: tipik bandın dışında.`, row, true);
    },
  },
  {
    id: 'FL-G2', type: 'guideline', patterns: ['flat'], sets: ALL, need: 4,
    name: 'Regular flat\'te C ≈ A, expanded flat\'te C ≈ 1,618A',
    description: "Regular flat'te C dalgası A'ya yaklaşık eşittir. Expanded flat'te C genellikle A'nın 1,618 katıdır (bazen 1,272 veya 2,618).",
    source: `${TD}; Elliott Wave Handbook`,
    weight: 1.5,
    check(c) {
      const r = c.L(3) / c.L(1);
      const st = flatSubtype(c);
      const targets = st === 'expanded' ? [1.618, 1.272, 2.618] : [1, 1.272, 0.618];
      const m = nearest(r, targets, 0.08);
      const row = ratio('C / A', r, st === 'expanded' ? '1,618× (1,272 / 2,618)' : '1× (1,272 / 0,618)', !!m, false);
      return m ? pass(`C = ${times(r)} A (≈ ${times(m.target)}, ${st}).`, row) : warn(`C = ${times(r)} A; ${st} flat için tipik değil.`, row, true);
    },
  },
  {
    id: 'FL-G3', type: 'guideline', patterns: ['flat'], sets: ALL, need: 4,
    name: "C, A'nın bitişine ulaşır",
    description: "C normalde A'nın bitiş seviyesine ulaşır ya da biraz geçer. Ulaşamıyorsa running flat olur. Running flat nadirdir: erken etiketlenirse on seferin dokuzunda yanlış çıkar.",
    source: `${FP}; ${COURSE_G}`,
    check(c) {
      const gap = c.past(3, 1) / c.L(1);
      if (gap >= -0.05) return pass(gap >= 0 ? "C, A'nın bitişini aştı." : "C, A'nın bitişine yakın bitti.");
      return flatSubtype(c) === 'running'
        ? warn(`C, A'nın bitişine ulaşmadı: running flat. Nadir görülür; erken etiketlemek çoğu zaman yanıltır.`)
        : warn(`C, A'nın bitişine ulaşmadı (eksik: A'nın ${pct(-gap)} kadarı).`, null, true);
    },
  },
  {
    id: 'FL-CLS', type: 'reminder', patterns: ['flat'], sets: ALL, need: 3,
    name: 'Alt tür sınıflaması (regular / expanded / running)',
    description: "B, A'nın %105'inden fazlasını alıp C de A'nın bitişini geçerse expanded flat. B en az %100 olup C, A aralığında kalırsa running flat. Diğer durumlar regular flat.",
    source: TD,
    check(c) {
      const st = flatSubtype(c);
      const tr = { regular: 'Regular flat', expanded: 'Expanded flat', running: 'Running flat', 'regular?': 'Regular flat (C bekleniyor)', 'expanded?': 'Expanded flat adayı (C bekleniyor)' }[st];
      let neely = '';
      if (c.set === 'neely') {
        const b = c.L(2) / c.L(1);
        neely = b < 0.81 ? ' Neely: zayıf B.' : b <= 1.0 ? ' Neely: normal B.' : ' Neely: güçlü B (irregular).';
        if (c.n >= 4 && c.L(3) < c.L(2)) neely += ' C, B\'yi tamamen geri almadı (C-failure).';
      }
      return info(`${tr}.${neely}`);
    },
  },
  {
    id: 'CRS-FL-1', type: 'rule', patterns: ['flat'], sets: ['course'], need: 3,
    name: "B, A'nın 2 katından büyük olamaz; süresi en fazla 10 katı",
    description: "B ≤ 2 × A olmalıdır. B'nin süresi A'nın süresinin 10 katını aşamaz.",
    source: COURSE,
    check(c) {
      const r = c.L(2) / c.L(1);
      const tr = c.D(2) / (c.D(1) || 1);
      if (r > 2) return fail(`B = ${times(r)} A; en fazla 2 kat.`);
      if (tr > 10) return fail(`B süresi A'nın ${times(tr)}; en fazla 10 kat.`);
      return pass(`B = ${times(r)} A, süre ${times(tr)}.`);
    },
  },
  {
    id: 'CRS-FL-2', type: 'rule', patterns: ['flat'], sets: ['course'], need: 4,
    name: "C, A bölgesine girmeli; C ≤ 2A, ≤ 2B ve ≤ 3A",
    description: "C dalgasının A'nın fiyat aralığıyla ortak noktası olmalıdır. C, A ve B'nin 2 katından, A'nın 3 katından büyük olamaz. Fiyat ve zaman olarak 10 kat sınırı da vardır.",
    source: COURSE,
    check(c) {
      const inA = c.past(2, 0) > 0 || c.past(3, 0) > 0;
      if (!inA) return fail("C, A'nın fiyat aralığına hiç girmedi.");
      const ca = c.L(3) / c.L(1), cb = c.L(3) / c.L(2);
      if (ca > 2 || cb > 2) return fail(`C = ${times(ca)} A, ${times(cb)} B; ikisinin de 2 katını aşamaz.`);
      const ta = c.D(3) / (c.D(1) || 1), tb = c.D(3) / (c.D(2) || 1);
      if (ta > 10 || tb > 10) return fail('C süresi A veya B\'nin 10 katını aşıyor.');
      return pass(`C = ${times(ca)} A, ${times(cb)} B.`);
    },
  },
  {
    id: 'CRS-FL-G1', type: 'guideline', patterns: ['flat'], sets: ['course'], need: 3,
    name: "B genellikle A'nın %95'inden fazla, %140'ından az",
    description: "Kurs kılavuzuna göre B dalgası genellikle A'nın %95 ile %140'ı arasındadır.",
    source: COURSE_G,
    check(c) {
      const r = c.L(2) / c.L(1);
      return inBand(r, 0.95, 1.4) ? pass(`B = ${pct(r)}.`) : warn(`B = ${pct(r)}; tipik %95–140.`, null, true);
    },
  },
  {
    id: 'FL-H1', type: 'reminder', patterns: ['flat'], sets: ALL, need: 3,
    name: 'İç yapı: 3-3-5',
    description: "A ve B üçlü (A asla üçgen olamaz), C beşli (itki veya ending diagonal) olmalıdır.",
    source: `${FP}; ${TD}`,
    check: () => info("Alt zaman diliminde A ve B'nin üçlü (A üçgen olamaz), C'nin beşli olduğunu teyit et (3-3-5). Flat'ler genellikle uzamış dalgaların öncesinde veya sonrasında görülür."),
  },
];

// Zigzag (5-3-5) kuralları. Noktalar: 0-A-B-C (dalga 1=A, 2=B, 3=C).
import { pass, fail, warn, info, pct, times, inBand, nearest, ratio } from '../engine/helpers.js';

const ALL = ['fp', 'neely', 'course'];
const FP = 'Frost & Prechter (EWP) Ders 6';
const TD = 'tradedevils EW Patterns';
const COURSE = 'Rules.docx (kurs)';
const COURSE_G = 'Guidelines.docx (kurs)';

export const zigzagRules = [
  {
    id: 'ZZ-R1', type: 'rule', patterns: ['zigzag'], sets: ALL, need: 3,
    name: "B, A'nın başlangıcını geçemez",
    description: "Zigzag'da B dalgası A dalgasının tamamını geri alamaz.",
    source: `${FP}; ${TD}`,
    check(c) {
      const r = c.L(2) / c.L(1);
      return c.past(2, 0) > c.eps ? pass(`B, A'yı ${pct(r)} oranında geri aldı.`) : fail(`B, A'nın başlangıcını aştı (${pct(r)}). Flat dene.`);
    },
  },
  {
    id: 'NEE-ZZ-1', type: 'rule', patterns: ['zigzag'], sets: ['neely'], need: 3,
    name: "B, A'nın en fazla %61,8'i",
    description: "Neely'ye göre zigzag'da b dalgası a dalgasının %61,8'inden fazlasını geri alamaz; daha derin B flat ailesine işaret eder.",
    source: 'Neely, Mastering Elliott Wave 5-20',
    check(c) {
      const r = c.L(2) / c.L(1);
      return r <= 0.618 + 0.005 ? pass(`B = ${pct(r)}.`) : fail(`B = ${pct(r)}; Neely zigzag'ında en fazla %61,8.`);
    },
  },
  {
    id: 'ZZ-G1', type: 'guideline', patterns: ['zigzag'], sets: ALL, need: 3,
    name: "B, A'nın %38,2–78,6'sını geri alır",
    description: "B genellikle %38,2, %50, %61,8 veya %78,6 seviyelerinde biter ve A'nın başlangıcına yakın bitmez. B üçgense %38–50, zigzag ise %50–78,6 olur.",
    source: `${TD}; ${COURSE_G}`,
    check(c) {
      const r = c.L(2) / c.L(1);
      const row = ratio('B / A', r, '%38,2–78,6', inBand(r, 0.382, 0.786, 0.03));
      if (row.ok) return pass(`B = A'nın ${pct(r)} kadarı: tipik aralıkta.`, row);
      if (r > 0.9) return warn(`B = A'nın ${pct(r)} kadarı: başlangıca çok yakın, flat olabilir.`, row);
      return warn(`B = A'nın ${pct(r)} kadarı: tipik aralığın dışında.`, row, r >= 0.3);
    },
  },
  {
    id: 'ZZ-G2', type: 'guideline', patterns: ['zigzag'], sets: ALL, need: 4,
    name: 'C = A, 1,272A veya 1,618A (bazen 0,618A)',
    description: "C dalgası en sık A'ya eşit olur. Sonraki olası ilişkiler 1,272, 1,618 ve 0,618 kattır.",
    source: `${TD}; Elliott Wave Handbook; ${COURSE_G}`,
    weight: 1.5,
    check(c) {
      const r = c.L(3) / c.L(1);
      const m = nearest(r, [1, 1.272, 1.618, 0.618], 0.07);
      const row = ratio('C / A', r, '1× / 1,272× / 1,618× / 0,618×', !!m, false);
      return m ? pass(`C = ${times(r)} A (≈ ${times(m.target)}).`, row) : warn(`C = ${times(r)} A; tipik Fibonacci ilişkilerinden uzak.`, row, inBand(r, 0.618, 1.618));
    },
  },
  {
    id: 'ZZ-G3', type: 'guideline', patterns: ['zigzag'], sets: ALL, need: 4,
    name: "C, A'nın sonunu geçer",
    description: "C dalgası neredeyse her zaman A'nın bitişini aşar. Aşamazsa kesik zigzag olur (Neely: C, A'nın %38,2–61,8'i ise truncated).",
    source: `${FP}; Neely 5-20`,
    check(c) {
      if (c.past(3, 1) > c.eps) return pass("C, A'nın bitişini aştı.");
      const r = c.L(3) / c.L(1);
      return warn(`C, A'nın sonunu aşamadı (C = ${times(r)} A): kesik zigzag.`);
    },
  },
  {
    id: 'ZZ-G4', type: 'guideline', patterns: ['zigzag'], sets: ALL, need: 4,
    name: "C > 1,618A ise yeni bir itki olabilir",
    description: "C, A'nın 1,618 katını belirgin şekilde aşıyorsa yapı bir zigzag değil, yeni bir trendin başlangıcı (itki) olabilir.",
    source: `${COURSE_G}; Neely (elongated zigzag)`,
    check(c) {
      const r = c.L(3) / c.L(1);
      return r <= 1.7 ? pass(`C = ${times(r)} A.`) : warn(`C = ${times(r)} A: uzamış zigzag ya da yeni itki başlangıcı olabilir.`);
    },
  },
  {
    id: 'CRS-ZZ-1', type: 'rule', patterns: ['zigzag'], sets: ['course'], need: 3,
    name: "B, A'nın en az %20'si ve süresi en fazla 10 katı",
    description: "B dalgası A'nın en az %20'si kadar olmalıdır. B'nin süresi A'nın süresinin 10 katını aşamaz.",
    source: COURSE,
    check(c) {
      const r = c.L(2) / c.L(1);
      const tr = c.D(2) / (c.D(1) || 1);
      if (r < 0.2) return fail(`B = ${pct(r)}; en az %20.`);
      if (tr > 10) return fail(`B süresi A'nın ${times(tr)}; en fazla 10 kat.`);
      return pass(`B = ${pct(r)}, süre ${times(tr)}.`);
    },
  },
  {
    id: 'CRS-ZZ-2', type: 'rule', patterns: ['zigzag'], sets: ['course'], need: 4,
    name: "C, B'nin %90'ı ile 5 katı arasında; A ve B'nin 10 katını aşmaz",
    description: "C > 0,9 × B ve C < 5 × B olmalıdır. C, fiyat ve zaman olarak A'nın veya B'nin 10 katından büyük olamaz.",
    source: COURSE,
    check(c) {
      const cb = c.L(3) / c.L(2);
      if (cb <= 0.9) return fail(`C = ${times(cb)} B; %90'dan büyük olmalı.`);
      if (cb >= 5) return fail(`C = ${times(cb)} B; 5 kattan küçük olmalı.`);
      const ca = c.L(3) / c.L(1);
      const ta = c.D(3) / (c.D(1) || 1), tb = c.D(3) / (c.D(2) || 1);
      if (ca > 10 || ta > 10 || tb > 10) return fail('C, A veya B\'nin 10 katını aşıyor (fiyat veya zaman).');
      return pass(`C = ${times(cb)} B.`);
    },
  },
  {
    id: 'CRS-ZZ-G1', type: 'guideline', patterns: ['zigzag'], sets: ['course'], need: 3,
    name: "B'nin süresi A'nın 0,618–1,618 katı",
    description: "B dalgası genellikle A'nın 0,618 ile 1,618 katı kadar sürer.",
    source: COURSE_G,
    check(c) {
      const r = c.D(2) / (c.D(1) || 1);
      return inBand(r, 0.618, 1.618, 0.05) ? pass(`Süre oranı ${times(r)}.`) : warn(`Süre oranı ${times(r)}; tipik 0,618–1,618×.`, null, true);
    },
  },
  {
    id: 'ZZ-H1', type: 'reminder', patterns: ['zigzag'], sets: ALL, need: 3,
    name: 'İç yapı: 5-3-5',
    description: "A beşli (itki veya leading diagonal), B üçlü, C beşli (itki veya ending diagonal) olmalıdır.",
    source: `${FP}; ${TD}`,
    check: () => info("Alt zaman diliminde A ve C'nin beşli, B'nin üçlü olduğunu teyit et (5-3-5). A leading diagonal ise C ending diagonal olamaz."),
  },
];

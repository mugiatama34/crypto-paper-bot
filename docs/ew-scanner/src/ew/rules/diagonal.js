// Diagonal (leading / ending) kuralları.
// Varyant: kind = 'leading' | 'ending', shape = 'contracting' | 'expanding'.
import { pass, fail, warn, info, pct, times, inBand, nearest, ratio } from '../engine/helpers.js';

const ALL = ['fp', 'neely', 'course'];
const FP = 'Frost & Prechter (EWP) Ders 5';
const TD = 'tradedevils EW Patterns';
const COURSE = 'Rules.docx (kurs)';
const COURSE_G = 'Guidelines.docx (kurs)';
const isC = (c) => c.variant.shape === 'contracting';
const shapeTr = (c) => (isC(c) ? 'daralan' : 'genişleyen');

export const diagonalRules = [
  {
    id: 'DIA-R1', type: 'rule', patterns: ['diagonal'], sets: ALL, need: 3,
    name: "Dalga 2, dalga 1'in başlangıcını geçemez",
    description: "Diagonal'de de dalga 2, dalga 1'in tamamını geri alamaz.",
    source: `${FP}; ${TD}`,
    check(c) {
      const r = c.L(2) / c.L(1);
      return c.past(2, 0) > c.eps ? pass(`Dalga 2, dalga 1'i ${pct(r)} oranında geri aldı.`) : fail(`Dalga 2, dalga 1'in başlangıcını aştı (${pct(r)}).`);
    },
  },
  {
    id: 'DIA-R2', type: 'rule', patterns: ['diagonal'], sets: ALL, need: 4,
    name: "Dalga 3, dalga 1'in sonunu geçmeli",
    description: 'Dalga 3 dalga 1\'in bitiş seviyesini aşmak zorundadır.',
    source: `${TD}; ${COURSE}`,
    check(c) {
      return c.past(3, 1) > c.eps ? pass("Dalga 3, dalga 1'i aştı.") : fail("Dalga 3, dalga 1'in sonunu aşamadı.");
    },
  },
  {
    id: 'DIA-R3', type: 'rule', patterns: ['diagonal'], sets: ALL, need: 6,
    name: 'Dalga 3 en kısa dalga olamaz',
    description: 'Diagonal\'de de 1, 3 ve 5 arasında dalga 3 en kısa olamaz.',
    source: `${FP}; ${COURSE}`,
    check(c) {
      const l1 = c.L(1), l3 = c.L(3), l5 = c.L(5);
      return l3 < l1 && l3 < l5 ? fail('Dalga 3 en kısa itki dalgası.') : pass('Dalga 3 en kısa değil.');
    },
  },
  {
    id: 'DIA-R4', type: 'rule', patterns: ['diagonal'], sets: ALL, need: 5,
    name: "Dalga 4, dalga 2'nin sonunu geçemez",
    description: "Dalga 4, dalga 2'nin bitişinin ötesine geçemez; dalga 2'nin fiyat aralığında biter.",
    source: TD,
    check(c) {
      return c.past(4, 2) > c.eps ? pass("Dalga 4, dalga 2'nin bitişini aşmadı.") : fail("Dalga 4, dalga 2'nin bitişinin ötesine geçti.");
    },
  },
  {
    id: 'DIA-R5', type: 'rule', patterns: ['diagonal'], sets: ALL, need: 4,
    name: 'Kama geometrisi: daralan tipte 1>3>5 ve 2>4, genişleyen tipte 1<3<5 ve 2<4',
    description: 'Daralan diagonal\'de dalgalar giderek kısalır ve 1-3 ile 2-4 çizgileri yakınsar. Genişleyen diagonal\'de dalgalar uzar ve çizgiler ıraksar. Karma bir yapı olamaz.',
    source: `${TD}; ${FP}`,
    check(c) {
      const C = isC(c);
      const cmp = (a, b) => (C ? c.L(a) < c.L(b) : c.L(a) > c.L(b));
      const bad = [];
      if (!cmp(3, 1)) bad.push(C ? '3 < 1 değil' : '3 > 1 değil');
      if (c.n >= 5 && !cmp(4, 2)) bad.push(C ? '4 < 2 değil' : '4 > 2 değil');
      if (c.n >= 6 && !cmp(5, 3)) bad.push(C ? '5 < 3 değil' : '5 > 3 değil');
      if (c.n >= 5) {
        const tx = c.intersect(1, 3, 2, 4);
        const conv = tx !== null && tx > c.t[4];
        const div = tx !== null && tx < c.t[1];
        if (C && !conv) bad.push('1-3 ve 2-4 çizgileri yakınsamıyor');
        if (!C && !div) bad.push('1-3 ve 2-4 çizgileri ıraksamıyor');
      }
      return bad.length
        ? fail(`${shapeTr(c)} diagonal geometrisi bozuk: ${bad.join(', ')}.`)
        : pass(`${shapeTr(c)} diagonal geometrisine uyuyor.`);
    },
  },
  {
    id: 'DIA-R6', type: 'rule', patterns: ['diagonal'], sets: ALL, need: 6,
    name: "Dalga 5, dalga 3'ün sonunu geçmeli",
    description: "Dalga 5 dalga 3'ü aşmalıdır. Ending diagonal'in daralan tipinde nadiren kesik kalabilir; bu durumda uyarı verilir.",
    source: TD,
    check(c) {
      if (c.past(5, 3) > c.eps) return pass("Dalga 5, dalga 3'ü aştı.");
      if (c.variant.kind === 'ending' && isC(c)) return warn("Dalga 5, dalga 3'ü aşamadı. Daralan ending diagonal'de nadiren olur.");
      return fail("Dalga 5, dalga 3'ün sonunu aşamadı.");
    },
  },
  {
    id: 'DIA-G1', type: 'guideline', patterns: ['diagonal'], sets: ALL, need: 5,
    name: 'Dalga 4 genellikle dalga 1 bölgesine girer (örtüşme)',
    description: "Diagonal'de dalga 4 neredeyse her zaman dalga 1'in fiyat bölgesiyle örtüşür. Örtüşme yoksa yapı büyük olasılıkla bir itkidir.",
    source: `${FP}; ${COURSE_G}`,
    weight: 2,
    check(c) {
      if (c.past(4, 1) < -c.eps) return pass(`Dalga 4, dalga 1 bölgesine girdi (örtüşme: dalga 1'in ${pct(-c.past(4, 1) / c.L(1))} kadarı).`);
      return warn('Örtüşme yok: bu yapı diagonal yerine itki olabilir.');
    },
  },
  {
    id: 'DIA-G2', type: 'guideline', patterns: ['diagonal'], sets: ALL, need: 3,
    name: "Dalga 2 dalga 1'in %61,8, %78,6 veya %88,7'sini geri alır",
    description: "Diagonal'de dalga 2 genellikle derin bir geri çekilmedir.",
    source: TD,
    check(c) {
      const r = c.L(2) / c.L(1);
      const m = nearest(r, [0.618, 0.786, 0.887], 0.06);
      const row = ratio('Dalga 2 / Dalga 1', r, '%61,8 / 78,6 / 88,7', !!m);
      if (m) return pass(`Dalga 2 = ${pct(r)} (≈ ${pct(m.target)}).`, row);
      return inBand(r, 0.35, 0.95) ? warn(`Dalga 2 = ${pct(r)}; tipik oranlara uzak ama olası.`, row, true) : warn(`Dalga 2 = ${pct(r)}; tipik değil.`, row);
    },
  },
  {
    id: 'DIA-G3', type: 'guideline', patterns: ['diagonal'], sets: ALL, need: 5,
    name: "Dalga 4, (0→3)'ün %38–50'si (daralan) veya %50–61,8'i (genişleyen)",
    description: "Dalga 4'ün derinliği 0'dan 3'e kadar olan mesafeye göre ölçülür.",
    source: TD,
    check(c) {
      const r = c.L(4) / Math.abs(c.v[3] - c.v[0]);
      const [lo, hi, txt] = isC(c) ? [0.382, 0.5, '%38,2–50'] : [0.5, 0.618, '%50–61,8'];
      const row = ratio('Dalga 4 / (0→3)', r, txt, inBand(r, lo, hi, 0.04));
      return row.ok ? pass(`Dalga 4 = (0→3)'ün ${pct(r)} kadarı.`, row) : warn(`Dalga 4 = (0→3)'ün ${pct(r)} kadarı; tipik ${txt}.`, row, true);
    },
  },
  {
    id: 'DIA-G4', type: 'guideline', patterns: ['diagonal'], sets: ALL, need: 6,
    name: 'Dalga 5 ve 1-3 çizgisi',
    description: "Ending diagonal'in 5. dalgası genellikle 1-3 çizgisini kısa süreli aşar (throw-over). Leading diagonal'de ve genişleyen tipte 5. dalga genellikle bu çizgiye ulaşmadan biter.",
    source: `${TD}; ${COURSE_G}`,
    check(c) {
      const line = c.lineAt(1, 3, c.t[5]);
      const over = c.d * (c.v[5] - line) > 0;
      const expectOver = c.variant.kind === 'ending' && isC(c);
      if (over === expectOver) return pass(over ? 'Dalga 5, 1-3 çizgisini aştı (throw-over).' : 'Dalga 5, 1-3 çizgisine ulaşmadan bitti.');
      return warn(over ? 'Dalga 5, 1-3 çizgisini aştı (bu tipte beklenmez).' : 'Dalga 5, 1-3 çizgisine ulaşmadı (daralan ending diagonal\'de throw-over beklenir).', null, true);
    },
  },

  // ---------- Kurs ----------
  {
    id: 'CRS-DIA-1', type: 'rule', patterns: ['diagonal'], sets: ['course'], need: 5,
    name: "Dalga 4, dalga 3'ün en az %10'u kadar örtüşme yapar",
    description: "Kurs kuralına göre dalga 4'ün dalga 1 bölgesine girme miktarı en az dalga 3'ün %10'u kadar olmalıdır.",
    source: COURSE,
    check(c) {
      const ov = -c.past(4, 1);
      const r = ov / c.L(3);
      return r >= 0.1 ? pass(`Örtüşme dalga 3'ün ${pct(r)} kadarı.`) : fail(`Örtüşme dalga 3'ün yalnızca ${pct(Math.max(r, 0))} kadarı; en az %10 gerekli.`);
    },
  },
  {
    id: 'CRS-DIA-2', type: 'rule', patterns: ['diagonal'], sets: ['course'], need: 5,
    name: "Dalga 4'ün süresi dalga 2'nin %10'u ile 10 katı arasında",
    description: "Dalga 4'ün süresi, dalga 2 süresinin %10'u ile %1000'i arasında olmalıdır.",
    source: COURSE,
    check(c) {
      const r = c.D(4) / (c.D(2) || 1);
      return r >= 0.1 && r <= 10 ? pass(`Süre oranı ${times(r)}.`) : fail(`Süre oranı ${times(r)}; 0,1–10× olmalı.`);
    },
  },
  {
    id: 'CRS-DIA-3', type: 'rule', patterns: ['diagonal'], sets: ['course'], need: 6,
    name: "Dalga 5, dalga 4'ün en az %80'i ve her zaman dalga 3'ten kısa",
    description: "Dalga 5 ≥ 0,8 × dalga 4 olmalı, dalga 5 her zaman dalga 3'ten kısa olmalı ve 1, 3, 5 arasında en uzunu olmamalıdır.",
    source: COURSE,
    check(c) {
      const a = c.L(5) / c.L(4);
      if (a < 0.8) return fail(`Dalga 5 = ${times(a)} dalga 4; en az 0,8 olmalı.`);
      if (c.L(5) >= c.L(3)) return fail("Dalga 5, dalga 3'ten kısa olmalı (kurs kuralı; genişleyen diagonal bu sette geçersizdir).");
      if (c.L(5) >= c.L(1)) return fail('Dalga 5, 1-3-5 arasında en uzun olamaz.');
      return pass(`Dalga 5 = ${times(a)} dalga 4, dalga 3'ten kısa.`);
    },
  },
  {
    id: 'CRS-DIA-4', type: 'rule', patterns: ['diagonal'], sets: ['course'], need: 5,
    name: 'Trend çizgileri daralmalı ve yapı bittikten sonra kesişmeli',
    description: 'Dip ve tepelerden çekilen çizgiler daralmalı ve yapı tamamlandıktan sonra kesişmelidir.',
    source: COURSE,
    check(c) {
      const tx = c.intersect(1, 3, 2, 4);
      const end = c.t[c.n - 1];
      if (tx === null) return fail('Çizgiler paralel; kesişmiyor.');
      return tx > end ? pass('Çizgiler yapı bittikten sonra kesişiyor.') : fail('Çizgiler yapı bitmeden önce kesişiyor ya da ıraksıyor.');
    },
  },
  {
    id: 'CRS-DIA-5', type: 'rule', patterns: ['diagonal'], sets: ['course'], need: 6,
    name: 'Yapı kanal içinde kalmalı (en fazla %10 sapma)',
    description: '0 ve 5 noktaları, 1-3 ve 2-4 çizgilerinin oluşturduğu kanalın dışına kanal genişliğinin %10\'undan fazla taşamaz.',
    source: COURSE,
    check(c) {
      const w0 = Math.abs(c.lineAt(1, 3, c.t[0]) - c.lineAt(2, 4, c.t[0]));
      const w5 = Math.abs(c.lineAt(1, 3, c.t[5]) - c.lineAt(2, 4, c.t[5]));
      const out0 = Math.max(0, -c.d * (c.v[0] - c.lineAt(2, 4, c.t[0]))) / (w0 || 1);
      const out5 = Math.max(0, c.d * (c.v[5] - c.lineAt(1, 3, c.t[5]))) / (w5 || 1);
      const worst = Math.max(out0, out5);
      return worst <= 0.1 ? pass(`En büyük taşma ${pct(worst)}.`) : fail(`Kanal dışına ${pct(worst)} taşma; en fazla %10.`);
    },
  },

  {
    id: 'DIA-H1', type: 'reminder', patterns: ['diagonal'], sets: ALL, need: 3,
    name: 'Konum ve iç yapı',
    description: "Leading diagonal 1. veya A dalgası konumunda görülür ve iç yapısı 5-3-5-3-5 ya da 3-3-3-3-3'tür. Ending diagonal 5. veya C dalgası konumunda görülür ve iç yapısı 3-3-3-3-3'tür.",
    source: `${FP}; ${TD}`,
    check(c) {
      return c.variant.kind === 'ending'
        ? info("Ending diagonal: 5. veya C dalgası konumunda olmalı, alt dalgaların hepsi zigzag ailesinden olmalı (3-3-3-3-3). Bitince genellikle hızla başlangıç seviyesine kadar geri alınır.")
        : info("Leading diagonal: 1. veya A dalgası konumunda olmalı (5-3-5-3-5 ya da 3-3-3-3-3, karma olmaz). Ardından derin bir dalga 2 ve uzayan bir dalga 3 beklenir.");
    },
  },
];

// Üçgen (3-3-3-3-3) kuralları. Noktalar: 0-A-B-C-D-E (dalga 1..5).
// Varyant: shape = 'contracting' | 'expanding'.
import { pass, fail, warn, info, pct, times, inBand, nearest, ratio } from '../engine/helpers.js';

const ALL = ['fp', 'neely', 'course'];
const FP = 'Frost & Prechter (EWP) Ders 8';
const TD = 'tradedevils EW Patterns';
const COURSE = 'Rules.docx (kurs)';
const COURSE_G = 'Guidelines.docx (kurs)';
const isC = (c) => c.variant.shape === 'contracting';
const LBL = ['0', 'A', 'B', 'C', 'D', 'E'];

export const triangleRules = [
  {
    id: 'TRI-R1', type: 'rule', patterns: ['triangle'], variants: ['contracting'], sets: ALL, need: 4,
    name: "Daralan: C A'nın, D B'nin, E C'nin sonunu geçemez",
    description: 'Daralan üçgende her bacak, aynı yöndeki bir önceki bacağın bitişini aşamaz.',
    source: `${TD}; ${FP}`,
    check(c) {
      const bad = [];
      if (c.past(3, 1) > c.eps) bad.push("C, A'nın sonunu geçti");
      if (c.n >= 5 && c.past(4, 2) < -c.eps) bad.push("D, B'nin sonunu geçti");
      if (c.n >= 6 && c.past(5, 3) > c.eps) bad.push("E, C'nin sonunu geçti");
      return bad.length ? fail(bad.join('; ') + '.') : pass('Her bacak bir önceki aynı yönlü bacağın içinde kaldı.');
    },
  },
  {
    id: 'TRI-R2', type: 'rule', patterns: ['triangle'], variants: ['expanding'], sets: ALL, need: 4,
    name: 'Genişleyen: C, D ve E bir önceki aynı yönlü dalgayı geçer',
    description: 'Genişleyen üçgende C, D ve E sırasıyla A, B ve C\'nin bitişini aşmak zorundadır.',
    source: TD,
    check(c) {
      const bad = [];
      if (!(c.past(3, 1) > c.eps)) bad.push("C, A'yı geçmedi");
      if (c.n >= 5 && !(c.past(4, 2) < -c.eps)) bad.push("D, B'yi geçmedi");
      if (c.n >= 6 && !(c.past(5, 3) > c.eps)) bad.push("E, C'yi geçmedi");
      return bad.length ? fail(bad.join('; ') + '.') : pass('Her bacak bir öncekini aştı.');
    },
  },
  {
    id: 'TRI-R2B', type: 'rule', patterns: ['triangle'], variants: ['expanding'], sets: ['fp', 'neely'], need: 3,
    name: 'Genişleyen: B, C ve D bir önceki dalganın %100–150\'si',
    description: 'Genişleyen üçgende B, C ve D, kendinden önceki dalganın en az %100\'ünü, en fazla %150\'sini geri alır.',
    source: TD,
    check(c) {
      const bad = [];
      for (let i = 2; i <= Math.min(4, c.n - 1); i++) {
        const r = c.L(i) / c.L(i - 1);
        if (r < 1 - 0.005 || r > 1.5 + 0.005) bad.push(`${LBL[i]}/${LBL[i - 1]} = ${pct(r)}`);
      }
      return bad.length ? fail(`Aralık dışı: ${bad.join(', ')}.`) : pass('Oranlar %100–150 aralığında.');
    },
  },
  {
    id: 'TRI-R3', type: 'rule', patterns: ['triangle'], sets: ALL, need: 5,
    name: 'A-C ve B-D çizgileri: daralanda yakınsar, genişleyende ıraksar',
    description: 'A-C ve B-D çizgileri paralel olamaz. Daralan üçgende yapıdan sonra, genişleyen üçgende yapının başlangıcından önce kesişirler.',
    source: `${FP}; ${COURSE}`,
    check(c) {
      const tx = c.intersect(1, 3, 2, 4);
      if (tx === null) return fail('A-C ve B-D çizgileri paralel.');
      if (isC(c)) return tx > c.t[4] ? pass('Çizgiler ileride kesişiyor (yakınsak).') : fail('Çizgiler yakınsamıyor.');
      return tx < c.t[1] ? pass('Çizgiler geride kesişiyor (ıraksak).') : fail('Çizgiler ıraksamıyor.');
    },
  },
  {
    id: 'TRI-G1', type: 'guideline', patterns: ['triangle'], variants: ['contracting'], sets: ALL, need: 3,
    name: "Her bacak bir öncekinin en az %55'ini geri alır",
    description: 'Daralan üçgende her dalga, bir önceki dalganın genellikle en az %55\'ini geri alır.',
    source: TD,
    check(c) {
      const low = [];
      for (let i = 2; i < c.n; i++) {
        const r = c.L(i) / c.L(i - 1);
        if (r < 0.55) low.push(`${LBL[i]}/${LBL[i - 1]} = ${pct(r)}`);
      }
      return low.length ? warn(`Sığ bacak: ${low.join(', ')}.`, null, true) : pass('Tüm bacaklar bir öncekinin en az %55\'i.');
    },
  },
  {
    id: 'TRI-G2', type: 'guideline', patterns: ['triangle'], sets: ALL, need: 4,
    name: 'Aynı yönlü bacaklar Fibonacci oranıyla ilişkili',
    description: 'Daralan üçgende aynı yöndeki bacaklar genellikle 0,618, 0,382 veya 0,786 oranındadır. Genişleyen üçgende C, D veya E\'den en az biri öncekinin 1,618 katıdır.',
    source: `${TD}; ${COURSE_G}`,
    check(c) {
      const pairs = [[3, 1], [4, 2], [5, 3]].filter(([a]) => a < c.n);
      const targets = isC(c) ? [0.618, 0.382, 0.786] : [1.618];
      const hits = pairs.map(([a, b]) => ({ a, b, r: c.L(a) / c.L(b) })).filter((x) => nearest(x.r, targets, 0.07));
      if (hits.length) return pass(hits.map((h) => `${LBL[h.a]}/${LBL[h.b]} = ${times(h.r)}`).join(', ') + ' (Fibonacci).');
      return warn('Aynı yönlü bacaklar arasında tipik Fibonacci ilişkisi bulunamadı.', null, true);
    },
  },
  {
    id: 'TRI-CLS', type: 'reminder', patterns: ['triangle'], sets: ALL, need: 3,
    name: 'Barrier / running üçgen',
    description: "B ve D aynı seviyede bitiyorsa barrier üçgen olur. B, A'nın başlangıcını aşıyorsa running üçgen olur.",
    source: `${TD}; Elliott Wave Handbook`,
    check(c) {
      const tags = [];
      if (c.past(2, 0) < -c.eps) tags.push("Running üçgen: B, A'nın başlangıcını aştı");
      if (c.n >= 5 && Math.abs(c.v[4] - c.v[2]) <= 0.1 * c.L(2)) tags.push('Barrier üçgen: B ve D yaklaşık aynı seviyede');
      return tags.length ? info(tags.join('. ') + '.') : null;
    },
  },

  // ---------- Kurs ----------
  {
    id: 'CRS-TRI-1', type: 'rule', patterns: ['triangle'], variants: ['contracting'], sets: ['course'], need: 3,
    name: "B, C ve D bir öncekinin %50'si ile %105'i arasında; C < B",
    description: "Daralan üçgende B, C ve D, kendinden önceki dalganın en az %50'si, en fazla %105'i kadar olmalıdır. Ayrıca C, B'den küçüktür.",
    source: COURSE,
    check(c) {
      const bad = [];
      for (let i = 2; i <= Math.min(4, c.n - 1); i++) {
        const r = c.L(i) / c.L(i - 1);
        if (r < 0.5 || r >= 1.05) bad.push(`${LBL[i]}/${LBL[i - 1]} = ${pct(r)}`);
      }
      if (c.n >= 4 && c.L(3) >= c.L(2)) bad.push('C ≥ B');
      return bad.length ? fail(`Aralık dışı: ${bad.join(', ')}.`) : pass('Oranlar kurs sınırları içinde.');
    },
  },
  {
    id: 'CRS-TRI-2', type: 'rule', patterns: ['triangle'], variants: ['contracting'], sets: ['course'], need: 6,
    name: "E > %25 D, E A'nın fiyat aralığında, A veya B en uzun",
    description: "E, D'nin %25'inden büyük olmalı ve A ile ortak fiyat aralığında bitmeli. Üçgenin en uzun dalgası A veya B'dir.",
    source: COURSE,
    check(c) {
      if (c.L(5) <= 0.25 * c.L(4)) return fail("E, D'nin %25'inden küçük.");
      const lo = Math.min(c.v[0], c.v[1]), hi = Math.max(c.v[0], c.v[1]);
      if (c.v[5] < lo || c.v[5] > hi) return fail("E, A'nın fiyat aralığının dışında bitti.");
      const ls = [1, 2, 3, 4, 5].map((i) => c.L(i));
      const maxI = ls.indexOf(Math.max(...ls)) + 1;
      if (maxI > 2) return fail(`En uzun dalga ${LBL[maxI]}; A veya B olmalı.`);
      return pass('E ve en uzun dalga koşulları sağlandı.');
    },
  },
  {
    id: 'CRS-TRI-3', type: 'rule', patterns: ['triangle'], variants: ['expanding'], sets: ['course'], need: 3,
    name: "Genişleyen: B, C ve D bir öncekinden en az %50 büyük; E > D > %50 E",
    description: "Genişleyen üçgende B, C ve D kendinden önceki dalganın en az 1,5 katıdır. E, D'den büyüktür ama D de E'nin %50'sinden büyük olmalıdır. En kısa dalga A veya B'dir.",
    source: COURSE,
    check(c) {
      const bad = [];
      for (let i = 2; i <= Math.min(4, c.n - 1); i++) {
        const r = c.L(i) / c.L(i - 1);
        if (r < 1.5) bad.push(`${LBL[i]}/${LBL[i - 1]} = ${pct(r)}`);
      }
      if (c.n >= 6) {
        if (c.L(5) <= c.L(4)) bad.push('E ≤ D');
        if (c.L(4) <= 0.5 * c.L(5)) bad.push('D ≤ %50 E');
        const ls = [1, 2, 3, 4, 5].map((i) => c.L(i));
        const minI = ls.indexOf(Math.min(...ls)) + 1;
        if (minI > 2) bad.push(`en kısa dalga ${LBL[minI]}`);
      }
      return bad.length ? fail(bad.join(', ') + '.') : pass('Kurs genişleyen üçgen koşulları sağlandı.');
    },
  },
  {
    id: 'CRS-TRI-4', type: 'rule', patterns: ['triangle'], sets: ['course'], need: 5,
    name: "D ve E'nin süresi C'nin en fazla 4 katı",
    description: "D ve E dalgalarının her biri zaman olarak C'nin 4 katını aşamaz.",
    source: COURSE,
    check(c) {
      const d = c.D(4) / (c.D(3) || 1);
      const e = c.n >= 6 ? c.D(5) / (c.D(3) || 1) : 0;
      return d <= 4 && e <= 4 ? pass(`D/C süre ${times(d)}${c.n >= 6 ? `, E/C ${times(e)}` : ''}.`) : fail('D veya E süresi C\'nin 4 katını aşıyor.');
    },
  },
  {
    id: 'TRI-H1', type: 'reminder', patterns: ['triangle'], sets: ALL, need: 3,
    name: 'Konum ve iç yapı: 3-3-3-3-3',
    description: 'Üçgen yalnızca 4., B, X veya kombinasyonun son dalgası konumunda görülür. Beş bacağın hepsi üçlüdür ve yalnızca biri karmaşık olabilir. Beşinci bacak bitmeden üçgen etiketlemek sık yapılan bir hatadır.',
    source: `Elliott Ch.5; ${FP}; ${TD}`,
    check: (c) => info(`Üçgen sadece 4., B veya X konumunda (ya da kombinasyonun sonunda) olur. Bacakların hepsi üçlü olmalı.${c.n < 6 ? ' E bitmeden üçgen etiketleme: en sık yapılan hata.' : ''}`),
  },
];

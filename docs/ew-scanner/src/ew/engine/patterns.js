// Kalıp tanımları: etiketler, varyantlar, geçersizlik seviyeleri ve sonraki dalga hedefleri.
import { pct, num } from './helpers.js';
import { flatSubtype } from '../rules/flat.js';

const T = (c, v, label) => ({ price: c.toPrice(v), label });
const kTxt = (k) => num(k, 3);

// Sıradaki dalganın (index n) yönü: tek dalgalar trend yönünde (d), çiftler tersinde.
const dirOf = (c, i) => (i % 2 === 1 ? c.d : -c.d);

export const PATTERNS = {
  impulse: {
    id: 'impulse',
    name: 'İtki',
    maxPoints: 6,
    labels: ['0', '1', '2', '3', '4', '5'],
    firstWave: "Dalga 1'in",
    variants: () => [{}],
    label: () => 'İtki (impulse)',
    invalidation(c) {
      const out = [];
      if (c.n === 3) out.push(T(c, c.v[0], "Fiyat 0 noktasının ötesine geçerse dalga 2 başlangıcı aşmış olur; sayım iptal."));
      if (c.n === 4 || c.n === 5) out.push(T(c, c.v[1], 'Dalga 4, dalga 1 bitişinin ötesine geçerse itki sayımı iptal (diagonal hariç).'));
      if (c.n === 5 && c.L(3) < c.L(1)) {
        out.push(T(c, c.v[4] + c.d * c.L(3), 'Dalga 5 bu seviyeyi aşarsa dalga 3 en kısa dalga olur; sayım bozulur.'));
      }
      if (c.n === 6) out.push(T(c, c.v[5], "Kalıp tamamlandı. Bu seviye aşılırsa dalga 5 uzuyor demektir; sayımı güncelle. Düzeltmede dalga 4 bölgesi beklenir."));
      return out;
    },
    targets(c) {
      const n = c.n;
      if (n === 3) {
        return { title: 'Dalga 3 hedefleri', items: [1, 1.618, 2.618].map((k) => T(c, c.v[2] + c.d * c.L(1) * k, `Dalga 3 = ${kTxt(k)} × dalga 1`)) };
      }
      if (n === 4) {
        return {
          title: 'Dalga 4 geri çekilme bölgesi',
          items: [0.236, 0.382, 0.5].map((k) => {
            const v = c.v[3] - c.d * c.L(3) * k;
            const bad = c.d * (v - c.v[1]) <= 0;
            return T(c, v, `Dalga 4: dalga 3'ün ${pct(k)} geri çekilmesi${bad ? ' (dalga 1 bölgesine girer, geçersiz)' : ''}`);
          }),
        };
      }
      if (n === 5) {
        const s03 = Math.abs(c.v[3] - c.v[0]);
        return {
          title: 'Dalga 5 hedefleri',
          items: [
            T(c, c.v[4] + c.d * c.L(1) * 0.618, 'Dalga 5 = 0,618 × dalga 1'),
            T(c, c.v[4] + c.d * c.L(1), 'Dalga 5 = dalga 1'),
            T(c, c.v[4] + c.d * s03 * 0.618, "Dalga 5 = 0,618 × (0→3)"),
            T(c, c.v[0] + c.d * s03 * 1.618, "Uzarsa: tepe = 0 + 1,618 × (0→3)"),
          ],
        };
      }
      const s05 = Math.abs(c.v[5] - c.v[0]);
      return {
        title: 'Beklenen düzeltme (A-B-C) bölgesi',
        items: [
          ...[0.382, 0.5, 0.618].map((k) => T(c, c.v[5] - c.d * s05 * k, `İtkinin ${pct(k)} geri çekilmesi`)),
          T(c, c.v[4], 'Dalga 4 bitişi (düzeltmenin tipik hedefi)'),
        ],
      };
    },
  },

  diagonal: {
    id: 'diagonal',
    name: 'Diagonal',
    maxPoints: 6,
    labels: ['0', '1', '2', '3', '4', '5'],
    firstWave: "Dalga 1'in",
    variants(opts) {
      const kinds = opts.diagonalKind && opts.diagonalKind !== 'auto' ? [opts.diagonalKind] : ['ending', 'leading'];
      return kinds.flatMap((kind) => [{ kind, shape: 'contracting' }, { kind, shape: 'expanding' }]);
    },
    label: (v) => `${v.kind === 'leading' ? 'Leading' : 'Ending'} diagonal (${v.shape === 'contracting' ? 'daralan' : 'genişleyen'})`,
    invalidation(c) {
      const out = [];
      const C = c.variant.shape === 'contracting';
      if (c.n === 3) out.push(T(c, c.v[0], 'Dalga 2 başlangıcı aşarsa sayım iptal.'));
      if (c.n === 4 || c.n === 5) out.push(T(c, c.v[2], "Dalga 4, dalga 2 bitişinin ötesine geçerse diagonal iptal."));
      if (c.n === 5 && C) out.push(T(c, c.v[4] + c.d * c.L(3), "Daralan diagonal'de dalga 5 bu seviyeyi aşamaz (dalga 3'ten kısa kalmalı)."));
      if (c.n === 6) {
        out.push(T(c, c.v[5], c.variant.kind === 'ending'
          ? 'Ending diagonal tamamlandı. Bu seviye aşılırsa sayım hatalı; normalde hızlı bir dönüş beklenir.'
          : 'Leading diagonal tamamlandı. Bu seviye aşılmadan derin bir dalga 2 beklenir.'));
      }
      return out;
    },
    targets(c) {
      const C = c.variant.shape === 'contracting';
      const s03 = Math.abs(c.v[3] - c.v[0]);
      if (c.n === 3) return { title: 'Dalga 3 hedefleri', items: (C ? [0.618, 0.786] : [1.272, 1.618]).map((k) => T(c, c.v[2] + c.d * c.L(1) * k, `Dalga 3 = ${kTxt(k)} × dalga 1`)) };
      if (c.n === 4) return { title: 'Dalga 4 bölgesi', items: (C ? [0.382, 0.5] : [0.5, 0.618]).map((k) => T(c, c.v[3] - c.d * s03 * k, `Dalga 4 = (0→3)'ün ${pct(k)} kadarı`)) };
      if (c.n === 5) return { title: 'Dalga 5 hedefleri', items: (C ? [0.618, 0.786] : [1.272, 1.618]).map((k) => T(c, c.v[4] + c.d * c.L(3) * k, `Dalga 5 = ${kTxt(k)} × dalga 3`)) };
      const s05 = Math.abs(c.v[5] - c.v[0]);
      if (c.variant.kind === 'ending') {
        return { title: 'Ending diagonal sonrası', items: [T(c, c.v[5] - c.d * s05 * 0.618, "Diagonal'in %61,8 geri çekilmesi"), T(c, c.v[0], 'Diagonal başlangıcı (tipik hızlı dönüş hedefi)')] };
      }
      return { title: 'Leading diagonal sonrası dalga 2', items: [0.618, 0.786].map((k) => T(c, c.v[5] - c.d * s05 * k, `Dalga 2 = diagonal'in ${pct(k)} kadarı`)) };
    },
  },

  zigzag: {
    id: 'zigzag',
    name: 'Zigzag',
    maxPoints: 4,
    labels: ['0', 'A', 'B', 'C'],
    firstWave: 'A dalgasının',
    variants: () => [{}],
    label: () => 'Zigzag (5-3-5)',
    invalidation(c) {
      if (c.n === 3) return [T(c, c.v[0], "B, A'nın başlangıcını aşarsa zigzag iptal (flat olabilir).")];
      return [T(c, c.v[3], 'C bu seviyenin ötesine uzarsa sayımı güncelle (C > 1,618A ise yeni itki olabilir).')];
    },
    targets(c) {
      if (c.n === 3) return { title: 'C dalgası hedefleri', items: [0.618, 1, 1.272, 1.618].map((k) => T(c, c.v[2] + c.d * c.L(1) * k, `C = ${kTxt(k)} × A`)) };
      return { title: 'Düzeltme sonrası', items: [T(c, c.v[0], 'Zigzag başlangıcı: ana trend devam ederse ilk referans')] };
    },
  },

  flat: {
    id: 'flat',
    name: 'Flat',
    maxPoints: 4,
    labels: ['0', 'A', 'B', 'C'],
    firstWave: 'A dalgasının',
    variants: () => [{}],
    label: (v, c) => {
      const st = flatSubtype(c);
      const tr = { regular: 'regular', expanded: 'expanded', running: 'running', 'regular?': 'regular?', 'expanded?': 'expanded?' }[st];
      return `Flat (3-3-5, ${tr})`;
    },
    invalidation(c) {
      if (c.n === 3) return [T(c, c.v[1] - c.d * 2 * c.L(1), "B bu seviyeyi aşarsa (B > 2A) flat yorumu bozulur.")];
      return [T(c, c.v[3], 'C bu seviyenin ötesine uzarsa sayımı güncelle.')];
    },
    targets(c) {
      if (c.n === 3) {
        return {
          title: 'C dalgası hedefleri',
          items: [T(c, c.v[1], 'A bitişi (C normalde buraya ulaşır)'), ...[1, 1.272, 1.618].map((k) => T(c, c.v[2] + c.d * c.L(1) * k, `C = ${kTxt(k)} × A`))],
        };
      }
      return { title: 'Düzeltme sonrası', items: [T(c, c.v[2], 'B bitişi: ana trend devam ederse ilk referans'), T(c, c.v[0], 'Flat başlangıcı')] };
    },
  },

  triangle: {
    id: 'triangle',
    name: 'Üçgen',
    maxPoints: 6,
    labels: ['0', 'A', 'B', 'C', 'D', 'E'],
    firstWave: 'A dalgasının',
    variants: () => [{ shape: 'contracting' }, { shape: 'expanding' }],
    label: (v) => `Üçgen (${v.shape === 'contracting' ? 'daralan' : 'genişleyen'})`,
    invalidation(c) {
      const C = c.variant.shape === 'contracting';
      const names = ['', 'A', 'B', 'C', 'D', 'E'];
      if (!C) return [{ price: null, label: 'Genişleyen üçgende sabit bir geçersizlik seviyesi yok: her bacak bir öncekini aşmalı.' }];
      if (c.n < 6) {
        const ref = c.n - 2; // bir sonraki dalga, aynı yönlü önceki dalganın bitişini geçemez
        return [T(c, c.v[ref], `${names[c.n]} dalgası, ${names[ref]} bitişinin ötesine geçerse daralan üçgen iptal.`)];
      }
      return [T(c, c.v[3], "E, C'nin bitişini geçerse üçgen iptal. Normalde E'den sonra thrust beklenir.")];
    },
    targets(c) {
      const C = c.variant.shape === 'contracting';
      const names = ['', 'A', 'B', 'C', 'D', 'E'];
      if (c.n < 6) {
        const dir = dirOf(c, c.n);
        const ks = C ? [0.618, 0.786] : [1.272, 1.5];
        return { title: `${names[c.n]} dalgası hedefleri`, items: ks.map((k) => T(c, c.v[c.n - 1] + dir * c.L(c.n - 1) * k, `${names[c.n]} = ${kTxt(k)} × ${names[c.n - 1]}`)) };
      }
      if (!C) return { title: 'Thrust', items: [], note: 'Genişleyen üçgen için thrust ölçümü yoktur.' };
      const w = Math.max(c.L(1), c.L(2));
      return { title: 'Üçgen sonrası thrust', items: [0.75, 1, 1.25].map((k) => T(c, c.v[5] - c.d * w * k, `Thrust = en geniş bacağın ${pct(k, 0)} kadarı`)) };
    },
  },

  wxy: {
    id: 'wxy',
    name: 'WXY',
    maxPoints: 4,
    labels: ['0', 'W', 'X', 'Y'],
    firstWave: 'W dalgasının',
    variants: () => [{ shape: 'double-zigzag' }, { shape: 'combination' }],
    label: (v) => (v.shape === 'double-zigzag' ? 'WXY: çift zigzag (keskin)' : 'WXY: kombinasyon / double three (yatay)'),
    invalidation(c) {
      if (c.n === 3) {
        return c.variant.shape === 'double-zigzag'
          ? [T(c, c.v[0], "X, W'nin başlangıcını aşarsa çift zigzag iptal (kombinasyon olabilir).")]
          : [T(c, c.v[1] - c.d * 2 * c.L(1), 'X bu seviyeyi aşarsa (X > 2W) kombinasyon yorumu bozulur.')];
      }
      return [T(c, c.v[3], 'Y bu seviyenin ötesine uzarsa sayımı güncelle (triple / Z olabilir).')];
    },
    targets(c) {
      if (c.n === 3) {
        const ks = c.variant.shape === 'double-zigzag' ? [0.618, 1, 1.272, 1.618] : [0.618, 1];
        return { title: 'Y dalgası hedefleri', items: ks.map((k) => T(c, c.v[2] + c.d * c.L(1) * k, `Y = ${kTxt(k)} × W`)) };
      }
      return { title: 'Düzeltme sonrası', items: [T(c, c.v[0], 'Yapının başlangıcı: ana trend devam ederse ilk referans')] };
    },
  },
};

export const PATTERN_ORDER = ['impulse', 'diagonal', 'zigzag', 'flat', 'triangle', 'wxy'];

export const PATTERN_CHOICES = [
  { id: 'auto', name: 'Bilmiyorum: hepsini dene' },
  { id: 'impulse', name: 'İtki (0-1-2-3-4-5)' },
  { id: 'diagonal', name: 'Diagonal (0-1-2-3-4-5)' },
  { id: 'zigzag', name: 'Zigzag (0-A-B-C)' },
  { id: 'flat', name: 'Flat (0-A-B-C)' },
  { id: 'triangle', name: 'Üçgen (0-A-B-C-D-E)' },
  { id: 'wxy', name: 'WXY (0-W-X-Y)' },
];

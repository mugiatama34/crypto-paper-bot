// İtki (impulse) kuralları ve kılavuzları.
// Noktalar: 0-1-2-3-4-5. Dalga i = (i-1 → i).
import { pass, fail, warn, info, pct, times, num, inBand, nearest, ratio } from '../engine/helpers.js';

const ALL = ['fp', 'neely', 'course'];
const FP = 'Frost & Prechter (EWP)';
const TD = 'tradedevils EW Patterns';
const COURSE = 'Rules.docx (kurs)';
const COURSE_G = 'Guidelines.docx (kurs)';

export const impulseRules = [
  // ---------- Kırılmaz kurallar ----------
  {
    id: 'IMP-R1', type: 'rule', patterns: ['impulse'], sets: ALL, need: 3,
    name: "Dalga 2, dalga 1'in başlangıcını geçemez",
    description: "Dalga 2 hiçbir zaman dalga 1'in %100'ünden fazlasını geri alamaz.",
    source: `${FP} Ders 4; ${TD}; ${COURSE}`,
    check(c) {
      const r = c.L(2) / c.L(1);
      return c.past(2, 0) > c.eps
        ? pass(`Dalga 2, dalga 1'i ${pct(r)} oranında geri aldı; başlangıç korunuyor.`)
        : fail(`Dalga 2, dalga 1'i ${pct(r)} oranında geri aldı; 0 noktası aşıldı.`);
    },
  },
  {
    id: 'IMP-R2', type: 'rule', patterns: ['impulse'], sets: ALL, need: 4,
    name: "Dalga 3, dalga 1'in sonunu geçmeli",
    description: 'Dalga 3 en az dalga 1 tepesini (düşüşte dibini) aşmak zorundadır.',
    source: `${TD}; ${COURSE}`,
    check(c) {
      return c.past(3, 1) > c.eps
        ? pass("Dalga 3, dalga 1'in bitişini aştı.")
        : fail("Dalga 3, dalga 1'in bitişini aşamadı.");
    },
  },
  {
    id: 'IMP-R3', type: 'rule', patterns: ['impulse'], sets: ALL, need: 4,
    name: 'Dalga 3 en kısa itki dalgası olamaz',
    description: '1, 3 ve 5 arasında dalga 3 hem 1\'den hem 5\'ten kısa olamaz.',
    source: `Elliott, Nature's Law Ch.6; ${FP} Ders 4`,
    check(c) {
      const l1 = c.L(1), l3 = c.L(3);
      if (c.n < 6) {
        if (l3 < l1) {
          return warn(`Dalga 3 (${times(l3 / l1)} dalga 1) dalga 1'den kısa. Dalga 5, dalga 3'ten uzun olursa sayım bozulur.`, null, true);
        }
        return pass('Dalga 3 dalga 1\'den uzun; dalga 5 henüz yok.');
      }
      const l5 = c.L(5);
      return l3 < l1 && l3 < l5
        ? fail(`Dalga 3 en kısa: 1=${num(l1, 4)}, 3=${num(l3, 4)}, 5=${num(l5, 4)}.`)
        : pass('Dalga 3 en kısa dalga değil.');
    },
  },
  {
    id: 'IMP-R4', type: 'rule', patterns: ['impulse'], sets: ALL, need: 5,
    name: 'Dalga 4, dalga 1 bölgesine giremez',
    description: "Dalga 4, dalga 1'in fiyat bölgesiyle örtüşemez (bu örtüşme sadece diagonal'de serbesttir). Kurs setinde kaldıraçlı piyasa seçeneği açıksa en fazla %15 örtüşme tolere edilir.",
    source: `${FP} Ders 4-5; Elliott Ch.5; ${COURSE} (kaldıraç toleransı)`,
    check(c) {
      if (c.past(4, 1) > c.eps) return pass('Dalga 4, dalga 1 bölgesinin dışında kaldı.');
      const overlap = -c.past(4, 1) / c.L(1);
      if (c.set === 'course' && c.opts.leverage && overlap <= 0.15) {
        return warn(`Dalga 4, dalga 1 bölgesine dalga 1'in ${pct(overlap)} kadarı girdi; kaldıraç toleransı (maks. %15, maks. 2 gün) içinde.`);
      }
      return fail(`Dalga 4, dalga 1 bölgesine girdi (örtüşme: dalga 1'in ${pct(overlap)} kadarı). Örtüşme serbest olan diagonal'i dene.`);
    },
  },

  // ---------- Kılavuzlar ----------
  {
    id: 'IMP-G1', type: 'guideline', patterns: ['impulse'], sets: ALL, need: 3,
    name: "Dalga 2 tipik olarak dalga 1'in %50–61,8'ini geri alır",
    description: 'Keskin düzeltmeler çoğunlukla %50–61,8 bandında biter. Kurs kılavuzlarında geniş bant %30–80 olarak geçer.',
    source: `Elliott Wave Handbook; ${COURSE_G}; ${TD}`,
    check(c) {
      const r = c.L(2) / c.L(1);
      const row = ratio("Dalga 2 / Dalga 1", r, '%50–61,8', inBand(r, 0.5, 0.618, 0.03));
      if (row.ok) return pass(`Dalga 2, dalga 1'in ${pct(r)} kadarı: tipik aralıkta.`, row);
      return inBand(r, 0.3, 0.8)
        ? warn(`Dalga 2, dalga 1'in ${pct(r)} kadarı: ideal bandın dışında ama geniş bant (%30–80) içinde.`, row, true)
        : warn(`Dalga 2, dalga 1'in ${pct(r)} kadarı: tipik bantların dışında.`, row);
    },
  },
  {
    id: 'IMP-G2', type: 'guideline', patterns: ['impulse'], sets: ALL, need: 4,
    name: "Dalga 3 genellikle dalga 1'in 1,618–2,618 katıdır",
    description: 'Dalga 3 çoğu zaman en uzun ve en güçlü dalgadır. Kurs kılavuzunda bu bant 1,5–3,5 kat olarak verilir.',
    source: `Elliott Wave Handbook; ${TD}; ${COURSE_G}`,
    check(c) {
      const r = c.L(3) / c.L(1);
      const row = ratio('Dalga 3 / Dalga 1', r, '1,618–2,618×', inBand(r, 1.618, 2.618, 0.06), false);
      if (row.ok) return pass(`Dalga 3 = ${times(r)} dalga 1: tipik aralıkta.`, row);
      return inBand(r, 1.0, 3.5)
        ? warn(`Dalga 3 = ${times(r)} dalga 1: ideal bandın dışında ama olası aralıkta.`, row, true)
        : warn(`Dalga 3 = ${times(r)} dalga 1: tipik aralığın dışında.`, row);
    },
  },
  {
    id: 'IMP-G3', type: 'guideline', patterns: ['impulse'], sets: ALL, need: 5,
    name: "Dalga 4 tipik olarak dalga 3'ün %23,6–38,2'sini geri alır",
    description: 'Dalga 4 genellikle sığdır, %38,2 civarında biter ve nadiren %50\'yi geçer.',
    source: `${TD}; ${COURSE_G}; WyzFi S11`,
    check(c) {
      const r = c.L(4) / c.L(3);
      const row = ratio('Dalga 4 / Dalga 3', r, '%23,6–38,2', inBand(r, 0.236, 0.382, 0.03));
      if (row.ok) return pass(`Dalga 4, dalga 3'ün ${pct(r)} kadarı: tipik aralıkta.`, row);
      return r <= 0.5
        ? warn(`Dalga 4, dalga 3'ün ${pct(r)} kadarı: %50'nin altında, kabul edilebilir.`, row, true)
        : warn(`Dalga 4, dalga 3'ün ${pct(r)} kadarı: %50'yi aşan derin bir dalga 4.`, row);
    },
  },
  {
    id: 'IMP-G4', type: 'guideline', patterns: ['impulse'], sets: ALL, need: 6,
    name: "Dalga 5 = dalga 1 veya (0→3)'ün 0,618 katı",
    description: "Dalga 5 çoğu zaman dalga 1'e eşittir ya da 0'dan 3'e kadar olan mesafenin 0,618 (bazen 0,382) katıdır. Uzarsa (0→3)'ün 1,618 katına kadar gidebilir.",
    source: `${TD}; Elliott Wave Handbook; ${COURSE_G}`,
    check(c) {
      const l5 = c.L(5);
      const span03 = Math.abs(c.v[3] - c.v[0]);
      const cands = [
        { label: 'dalga 1', base: c.L(1), k: 1 },
        { label: "(0→3)'ün 0,618 katı", base: span03, k: 0.618 },
        { label: "(0→3)'ün 0,382 katı", base: span03, k: 0.382 },
        { label: "(0→3)'ün 1,618 katı", base: span03, k: 1.618 },
        { label: "dalga 1'in 0,618 katı", base: c.L(1), k: 0.618 },
        { label: "dalga 1'in 1,618 katı", base: c.L(1), k: 1.618 },
      ];
      let best = null;
      for (const x of cands) {
        const err = Math.abs(l5 - x.base * x.k) / (x.base * x.k);
        if (!best || err < best.err) best = { ...x, err };
      }
      const r1 = l5 / c.L(1);
      const ok = best.err <= 0.1;
      const row = ratio('Dalga 5 / Dalga 1', r1, '1× ya da 0,618×(0→3)', ok, false);
      return ok
        ? pass(`Dalga 5 ≈ ${best.label} (sapma ${pct(best.err)}).`, row)
        : warn(`Dalga 5 tipik ilişkilerin hiçbirine yakın değil; en yakını ${best.label}, sapma ${pct(best.err)}.`, row, best.err <= 0.2);
    },
  },
  {
    id: 'IMP-G5', type: 'guideline', patterns: ['impulse'], sets: ALL, need: 5,
    name: 'Alternasyon: dalga 2 ile dalga 4 farklı karakterde olmalı',
    description: 'Dalga 2 keskin, derin ve kısa sürerse dalga 4 sığ, yatay ve uzun olur; tersi de geçerlidir. Burada derinlik (geri alma oranı) ve süre karşılaştırılır.',
    source: `Elliott Ch.8; ${FP} Ders 10; Neely (5 boyutlu alternasyon)`,
    weight: 1.5,
    check(c) {
      const r2 = c.L(2) / c.L(1);
      const r4 = c.L(4) / c.L(3);
      const tr = c.D(4) / (c.D(2) || 1);
      const depthDiff = Math.abs(r2 - r4);
      const timeDiff = tr >= 1.5 || tr <= 1 / 1.5;
      const txt = `Derinlik: dalga 2 ${pct(r2)}, dalga 4 ${pct(r4)}. Süre: dalga 4 / dalga 2 = ${times(tr)}.`;
      if (depthDiff >= 0.15 || timeDiff) return pass(`Alternasyon var. ${txt}`);
      return warn(`Dalga 2 ve 4 derinlik ve süre bakımından birbirine çok benziyor, alternasyon zayıf. ${txt}`);
    },
  },
  {
    id: 'IMP-G6', type: 'guideline', patterns: ['impulse'], sets: ALL, need: 6,
    name: 'Kanal: dalga 5, 2-4 tabanına paralel üst çizgi civarında biter',
    description: "2 ve 4 noktalarından geçen çizgiye dalga 3'ten bir paralel çizilir. Dalga 5 genellikle bu paralelin yakınında biter; kısa süreli taşmasına throw-over denir.",
    source: `${FP} Ders 12; Elliott Wave Handbook (Channeling)`,
    check(c) {
      const width = c.d * (c.v[3] - c.lineAt(2, 4, c.t[3]));
      if (!Number.isFinite(width) || width <= 0) return warn('Kanal çizilemedi (2-4 çizgisi dalga 3\'ün üzerinde).', null, true);
      const upper = c.lineAt(2, 4, c.t[5]) + c.d * width;
      const dev = (c.d * (c.v[5] - upper)) / width;
      if (Math.abs(dev) <= 0.25) return pass(`Dalga 5, kanal üst çizgisine yakın bitti (sapma: kanal genişliğinin ${pct(dev)} kadarı).`);
      return dev > 0
        ? warn(`Dalga 5 kanalı kanal genişliğinin ${pct(dev)} kadarı aştı (throw-over). Uzayan 5 ya da log ölçek gerekebilir.`, null, true)
        : warn(`Dalga 5 kanal üst çizgisine ulaşmadan bitti (eksik: kanal genişliğinin ${pct(-dev)} kadarı).`, null, true);
    },
  },
  {
    id: 'IMP-G7', type: 'guideline', patterns: ['impulse'], sets: ['fp', 'course'], need: 6,
    name: 'Uzama yalnızca bir dalgada olur',
    description: 'İtki dalgalarından (1, 3, 5) genellikle yalnızca biri uzar, yani diğerlerinin belirgin şekilde (≈1,618 kat) üstüne çıkar. Çift uzama alışılmadık bir durumdur.',
    source: `Elliott Ch.6; ${FP} Ders 4; ${COURSE_G}`,
    check(c) {
      const ls = [[1, c.L(1)], [3, c.L(3)], [5, c.L(5)]].sort((a, b) => b[1] - a[1]);
      const ext = ls[0][1] / ls[1][1] >= 1.618;
      const doubleExt = ls[1][1] / ls[2][1] >= 1.618 && ext;
      if (doubleExt) return warn(`Hem dalga ${ls[0][0]} hem dalga ${ls[1][0]} uzamış görünüyor (çift uzama alışılmadık).`);
      if (ext) return pass(`Dalga ${ls[0][0]} uzamış (${times(ls[0][1] / ls[1][1])} bir sonraki en uzun dalga).`);
      return warn('Belirgin bir uzama yok; üç itki dalgası birbirine yakın uzunlukta.', null, true);
    },
  },
  {
    id: 'IMP-G8', type: 'guideline', patterns: ['impulse'], sets: ALL, need: 6,
    name: "Dalga 5 dalga 3'ü geçmelidir (geçmezse kesik 5)",
    description: "Dalga 5'in dalga 3'ün sonunu aşamamasına 'truncation' (kesik 5) denir. Geçerlidir ama nadirdir ve genellikle güçlü bir dalga 3'ün ardından görülür.",
    source: `${FP} Ders 4; ${TD}`,
    check(c) {
      return c.past(5, 3) > c.eps
        ? pass("Dalga 5, dalga 3'ün bitişini aştı.")
        : warn("Dalga 5, dalga 3'ü aşamadı: kesik 5 (truncation). Nadir görülür, dikkatli ol.");
    },
  },

  // ---------- Neely ----------
  {
    id: 'NEE-IMP-EXT', type: 'rule', patterns: ['impulse'], sets: ['neely'], need: 6,
    name: 'Neely Litmus testi: bir dalga bir sonraki en uzunun ≥%161,8\'i olmalı',
    description: "Neely'ye göre bir itkide 1, 3 veya 5'ten biri uzamış olmak zorundadır: bir sonraki en uzun dalganın en az %161,8'i kadar.",
    source: 'Neely, Mastering Elliott Wave 5-4 (Extension Rule)',
    check(c) {
      const ls = [[1, c.L(1)], [3, c.L(3)], [5, c.L(5)]].sort((a, b) => b[1] - a[1]);
      const r = ls[0][1] / ls[1][1];
      return r >= 1.618 - 0.005
        ? pass(`Dalga ${ls[0][0]} uzamış: ${pct(r)} (eşik %161,8).`)
        : fail(`En uzun dalga (${ls[0][0]}) bir sonrakinin yalnızca ${pct(r)} kadarı; Neely eşiği %161,8.`);
    },
  },
  {
    id: 'NEE-IMP-POST', type: 'reminder', patterns: ['impulse'], sets: ['neely'], need: 6,
    name: 'Uzayan 5. dalgadan sonra en az %61,8 geri çekilme',
    description: "Neely'ye göre dalga 5 uzadıysa ardından gelen düzeltme dalga 5'in en az %61,8'ini geri almalıdır (Elliott bunu 'genellikle' der).",
    source: 'Neely, Basic Rules of Logic 6-2',
    check(c) {
      if (c.L(5) / Math.max(c.L(1), c.L(3)) >= 1.618) {
        return info("Dalga 5 uzamış görünüyor. Neely'ye göre sonraki düzeltme dalga 5'in en az %61,8'ini geri almalı.");
      }
      return null;
    },
  },

  // ---------- Kurs (Rules.docx) ----------
  {
    id: 'CRS-IMP-1', type: 'rule', patterns: ['impulse'], sets: ['course'], need: 3,
    name: 'Dalga 2 en az %20 düzeltme yapmalı',
    description: "Dalga 2, dalga 1'in en az %20'sini geri almalıdır.",
    source: COURSE,
    check(c) {
      const r = c.L(2) / c.L(1);
      return r >= 0.2 ? pass(`Dalga 2 = ${pct(r)}.`) : fail(`Dalga 2 yalnızca ${pct(r)}; en az %20 olmalı.`);
    },
  },
  {
    id: 'CRS-IMP-2', type: 'rule', patterns: ['impulse'], sets: ['course'], need: 3,
    name: "Dalga 2'nin süresi dalga 1'in en fazla 9 katı",
    description: "Dalga 2'nin süresi, dalga 1'in süresinin 9 katını aşamaz.",
    source: COURSE,
    check(c) {
      const r = c.D(2) / (c.D(1) || 1);
      return r <= 9 ? pass(`Süre oranı ${times(r)}.`) : fail(`Dalga 2 süresi dalga 1'in ${times(r)}; en fazla 9 kat olmalı.`);
    },
  },
  {
    id: 'CRS-IMP-3', type: 'rule', patterns: ['impulse'], sets: ['course'], need: 4,
    name: "Dalga 3, dalga 1'in 1/3'ü ile 7 katı arasında (fiyat ve zaman)",
    description: "Dalga 3, dalga 1'in 1/3'ünden kısa olamaz. Fiyat ve zaman olarak 7 katından büyük de olamaz.",
    source: COURSE,
    check(c) {
      const r = c.L(3) / c.L(1);
      const tr = c.D(3) / (c.D(1) || 1);
      if (r < 1 / 3) return fail(`Dalga 3 = ${times(r)} dalga 1; en az 1/3 olmalı.`);
      if (r > 7) return fail(`Dalga 3 = ${times(r)} dalga 1; fiyat olarak 7 katı aşamaz.`);
      if (tr > 7) return fail(`Dalga 3 süresi dalga 1'in ${times(tr)}; 7 katı aşamaz.`);
      return pass(`Fiyat ${times(r)}, süre ${times(tr)}.`);
    },
  },
  {
    id: 'CRS-IMP-4', type: 'rule', patterns: ['impulse'], sets: ['course'], need: 5,
    name: "Dalga 4, dalga 2'nin 1/3'ü ile 3 katı arasında",
    description: "Dalga 4, dalga 2'nin 1/3'ünden küçük, 3 katından büyük olamaz.",
    source: COURSE,
    check(c) {
      const r = c.L(4) / c.L(2);
      return r >= 1 / 3 && r < 3 ? pass(`Dalga 4 = ${times(r)} dalga 2.`) : fail(`Dalga 4 = ${times(r)} dalga 2; 1/3–3 arasında olmalı.`);
    },
  },
  {
    id: 'CRS-IMP-5', type: 'rule', patterns: ['impulse'], sets: ['course'], need: 5,
    name: "Dalga 4'ün süresi dalga 3'ün en fazla 2 katı",
    description: "Dalga 4'ün süresi, dalga 3'ün süresinin 2 katını aşamaz.",
    source: COURSE,
    check(c) {
      const r = c.D(4) / (c.D(3) || 1);
      return r <= 2 ? pass(`Süre oranı ${times(r)}.`) : fail(`Dalga 4 süresi dalga 3'ün ${times(r)}; en fazla 2 kat.`);
    },
  },
  {
    id: 'CRS-IMP-6', type: 'rule', patterns: ['impulse'], sets: ['course'], need: 6,
    name: "Dalga 5, dalga 4'ün %70'inden büyük ve dalga 2'nin en fazla 6 katı",
    description: "Dalga 5 > 0,7 × dalga 4 ve dalga 5 ≤ 6 × dalga 2 olmalıdır.",
    source: COURSE,
    check(c) {
      const a = c.L(5) / c.L(4);
      const b = c.L(5) / c.L(2);
      if (a <= 0.7) return fail(`Dalga 5 = ${times(a)} dalga 4; %70'ten büyük olmalı.`);
      if (b > 6) return fail(`Dalga 5 = ${times(b)} dalga 2; en fazla 6 kat.`);
      return pass(`Dalga 5 = ${times(a)} dalga 4 ve ${times(b)} dalga 2.`);
    },
  },
  {
    id: 'CRS-IMP-G1', type: 'guideline', patterns: ['impulse'], sets: ['course'], need: 3,
    name: "Dalga 2'nin süresi dalga 1'in %10'undan fazladır",
    description: 'Dalga 2 genellikle dalga 1\'e göre kısa sürer ama süresi dalga 1\'in %10\'unun altına düşmez.',
    source: COURSE_G,
    check(c) {
      const r = c.D(2) / (c.D(1) || 1);
      return r > 0.1 ? pass(`Süre oranı ${times(r)}.`) : warn(`Dalga 2 süresi dalga 1'in yalnızca ${pct(r)} kadarı.`);
    },
  },
  {
    id: 'CRS-IMP-G2', type: 'guideline', patterns: ['impulse'], sets: ['course'], need: 4,
    name: "Dalga 3'ün süresi dalga 1'in 1–4 katı",
    description: "Dalga 3 genellikle zaman olarak dalga 1'in 1 ile 4 katı arasında sürer.",
    source: COURSE_G,
    check(c) {
      const r = c.D(3) / (c.D(1) || 1);
      return inBand(r, 1, 4, 0.05) ? pass(`Süre oranı ${times(r)}.`) : warn(`Süre oranı ${times(r)}; tipik 1–4×.`, null, true);
    },
  },
  {
    id: 'CRS-IMP-G3', type: 'guideline', patterns: ['impulse'], sets: ['course'], need: 5,
    name: "Dalga 4'ün süresi dalga 2'nin %100–270'i",
    description: "Dalga 4 genellikle dalga 2'den daha uzun sürer: dalga 2 süresinin %100–270'i kadar.",
    source: COURSE_G,
    check(c) {
      const r = c.D(4) / (c.D(2) || 1);
      return inBand(r, 1, 2.7, 0.05) ? pass(`Süre oranı ${times(r)}.`) : warn(`Süre oranı ${times(r)}; tipik 1–2,7×.`, null, true);
    },
  },

  // ---------- Hatırlatmalar ----------
  {
    id: 'IMP-H1', type: 'reminder', patterns: ['impulse'], sets: ALL, need: 3,
    name: 'İç yapı: 5-3-5-3-5',
    description: 'Dalga 1, 3 ve 5 beş alt dalgalı olmalıdır (dalga 3 asla diagonal olamaz). Dalga 2 ve 4 üç alt dalgalıdır; dalga 2 üçgen olamaz.',
    source: `${FP} Ders 4; ${TD}; ${COURSE}`,
    check: () => info("İç yapıyı alt zaman diliminde kontrol et: 1, 3 ve 5 beşli olmalı (3 asla diagonal olamaz), 2 ve 4 üçlü olmalı (2 üçgen olamaz)."),
  },
];

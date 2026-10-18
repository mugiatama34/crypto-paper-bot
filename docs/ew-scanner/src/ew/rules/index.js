// Tüm kuralların tek listesi ve kural seti tanımları.
import { commonRules } from './common.js';
import { impulseRules } from './impulse.js';
import { diagonalRules } from './diagonal.js';
import { zigzagRules } from './zigzag.js';
import { flatRules } from './flat.js';
import { triangleRules } from './triangle.js';
import { wxyRules } from './wxy.js';

export const RULES = [
  ...commonRules,
  ...impulseRules,
  ...diagonalRules,
  ...zigzagRules,
  ...flatRules,
  ...triangleRules,
  ...wxyRules,
];

export const RULE_SETS = {
  fp: {
    id: 'fp',
    name: 'Frost & Prechter (varsayılan)',
    short: 'F&P',
    description: "Elliott Wave Principle'ın klasik kural/kılavuz ayrımı. tradedevils EW Patterns ve Elliott Wave Handbook'un oran tablolarıyla desteklenir.",
  },
  neely: {
    id: 'neely',
    name: 'Neely (NEoWave)',
    short: 'Neely',
    description: "Klasik kurallara Neely'nin sayısal eşiklerini ekler: Litmus testi (uzama ≥%161,8), zigzag'da B ≤ %61,8, flat'te B ≥ %61,8 ve Time Rule.",
  },
  course: {
    id: 'course',
    name: 'Kurs (Rules.docx + Guidelines.docx)',
    short: 'Kurs',
    description: 'Elliott Ders 1-8 kursunun Türkçe kural ve kılavuz föyleri. Neredeyse her ilişki için sayısal fiyat ve süre sınırı koyar; kaldıraçlı piyasa toleransı seçeneği vardır.',
  },
};

export const TYPE_LABELS = { rule: 'Kırılmaz kural', guideline: 'Kılavuz', reminder: 'Hatırlatma' };

export function rulesFor(pattern, set, variant = {}) {
  return RULES.filter(
    (r) =>
      (r.patterns.includes('*') || r.patterns.includes(pattern)) &&
      r.sets.includes(set) &&
      (!r.variants || r.variants.includes(variant.shape)),
  );
}

// Kimlik benzersizliği (geliştirme sırasında hatayı erken yakalamak için).
const seen = new Set();
for (const r of RULES) {
  if (seen.has(r.id)) throw new Error(`Yinelenen kural kimliği: ${r.id}`);
  seen.add(r.id);
}

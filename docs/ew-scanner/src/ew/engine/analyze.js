// Kural motoru: noktaları seçilen (veya tüm) kalıplara karşı test eder, skorlar ve sıralar.
// Saf fonksiyon; DOM veya global durum kullanmaz.
import { makeContext } from './context.js';
import { PATTERNS, PATTERN_ORDER } from './patterns.js';
import { rulesFor } from '../rules/index.js';
import { na } from './helpers.js';

export const MIN_POINTS = 3;

function runRule(rule, c) {
  if (c.n < rule.need) {
    return rule.type === 'reminder' ? null : na(`Bu kontrol için en az ${rule.need} nokta gerekli.`);
  }
  try {
    return rule.check(c);
  } catch (e) {
    return na(`Kontrol hesaplanamadı: ${e.message}`);
  }
}

function scoreOf(results, n, maxPoints, valid) {
  if (!valid) return 0;
  let got = 0, total = 0;
  for (const r of results) {
    if (r.rule.type !== 'guideline' || (r.status !== 'pass' && r.status !== 'warn')) continue;
    const w = r.rule.weight || 1;
    total += w;
    if (r.status === 'pass') got += w;
    else if (r.soft) got += w * 0.5;
  }
  const g = total ? got / total : 0.5;
  return Math.round(100 * (0.8 * g + 0.2 * (n / maxPoints)));
}

export function evaluate(patternId, points, opts, variant = {}) {
  const pattern = PATTERNS[patternId];
  const fullVariant = { ...variant, maxPoints: pattern.maxPoints, firstWave: pattern.firstWave };
  const c = makeContext(points, opts, fullVariant);
  const rules = rulesFor(patternId, c.set, variant);
  const results = [];
  for (const rule of rules.filter((r) => r.phase !== 'post')) {
    const res = runRule(rule, c);
    if (res) results.push({ rule, ...res });
  }
  c.results = results;
  for (const rule of rules.filter((r) => r.phase === 'post')) {
    const res = runRule(rule, c);
    if (res) results.push({ rule, ...res });
  }
  const fails = results.filter((r) => r.status === 'fail').length;
  const valid = fails === 0;
  const warns = results.filter((r) => r.status === 'warn').length;
  const score = scoreOf(results, c.n, pattern.maxPoints, valid);
  const targets = valid ? pattern.targets(c) : null;
  return {
    pattern: patternId,
    variant,
    label: pattern.label(variant, c),
    labels: pattern.labels.slice(0, c.n),
    complete: c.n === pattern.maxPoints,
    valid,
    fails,
    warns,
    score,
    results,
    ratios: results.filter((r) => r.ratio).map((r) => ({ ...r.ratio, ruleId: r.rule.id })),
    invalidation: pattern.invalidation(c),
    targets,
    noteId: patternId,
  };
}

const better = (a, b) => {
  if (a.valid !== b.valid) return a.valid ? -1 : 1;
  if (a.fails !== b.fails) return a.fails - b.fails;
  return b.score - a.score;
};

export function validatePoints(points, opts = {}) {
  if (!Array.isArray(points) || points.length < MIN_POINTS) {
    return `En az ${MIN_POINTS} dönüş noktası gerekli (${points?.length || 0} nokta var).`;
  }
  for (const [i, q] of points.entries()) {
    if (!Number.isFinite(q.p)) return `${i}. noktanın fiyatı geçersiz.`;
    if (opts.logScale && q.p <= 0) return 'Log ölçekte tüm fiyatlar pozitif olmalı.';
  }
  return null;
}

/**
 * @param {{t:number, p:number}[]} points  dönüş noktaları (t: zaman/x, p: fiyat)
 * @param {object} options  pattern ('auto' | kalıp id), ruleSet ('fp'|'neely'|'course'),
 *                          logScale, leverage, diagonalKind ('auto'|'leading'|'ending')
 */
export function analyze(points, options = {}) {
  const opts = { pattern: 'auto', ruleSet: 'fp', logScale: false, leverage: false, diagonalKind: 'auto', ...options };
  const err = validatePoints(points, opts);
  if (err) return { ok: false, error: err, candidates: [] };

  const n = points.length;
  let ids;
  if (opts.pattern === 'auto') {
    ids = PATTERN_ORDER.filter((id) => PATTERNS[id].maxPoints >= n);
    if (!ids.length) return { ok: false, error: `En fazla 6 nokta desteklenir (${n} nokta var).`, candidates: [] };
  } else {
    const p = PATTERNS[opts.pattern];
    if (!p) return { ok: false, error: `Bilinmeyen kalıp: ${opts.pattern}`, candidates: [] };
    if (n > p.maxPoints) return { ok: false, error: `${p.name} en fazla ${p.maxPoints} nokta alır (${n} nokta var).`, candidates: [] };
    ids = [opts.pattern];
  }

  const candidates = ids.map((id) => {
    const evals = PATTERNS[id].variants(opts).map((v) => evaluate(id, points, opts, v));
    evals.sort(better);
    const best = evals[0];
    best.alternatives = evals.slice(1).filter((e) => e.valid).map((e) => e.label);
    return best;
  });
  candidates.sort(better);
  return { ok: true, n, options: opts, candidates, best: candidates[0] };
}

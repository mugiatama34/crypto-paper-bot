// Analiz bağlamı: noktalar → dalga uzunlukları, süreler, yön ve geometri yardımcıları.
// Log ölçekte uzunluklar ln(fiyat) farkıyla (yani yüzde bazında) ölçülür.

export function makeContext(points, opts = {}, variant = {}) {
  const logScale = !!opts.logScale;
  const p = points.map((q) => q.p);
  const t = points.map((q, i) => (Number.isFinite(q.t) ? q.t : i));
  const v = p.map((x) => (logScale ? Math.log(x) : x));
  const n = v.length;
  const d = Math.sign(v[1] - v[0]) || 1;
  const scale = Math.max(...v.map(Math.abs), 1e-12);
  const eps = scale * 1e-9;

  // i. dalganın (i-1 → i) fiyat uzunluğu ve süresi
  const L = (i) => Math.abs(v[i] - v[i - 1]);
  const D = (i) => Math.abs(t[i] - t[i - 1]);
  // i noktasının j noktasına göre trend yönündeki işaretli uzaklığı
  const past = (i, j) => d * (v[i] - v[j]);
  // i→j doğrusunun tt anındaki değeri
  const lineAt = (i, j, tt) => {
    if (t[j] === t[i]) return NaN;
    return v[i] + ((v[j] - v[i]) * (tt - t[i])) / (t[j] - t[i]);
  };
  // (i,j) ve (k,l) doğrularının kesiştiği an; paralelse null
  const intersect = (i, j, k, l) => {
    const s1 = (v[j] - v[i]) / (t[j] - t[i]);
    const s2 = (v[l] - v[k]) / (t[l] - t[k]);
    if (!Number.isFinite(s1) || !Number.isFinite(s2)) return null;
    const ds = s1 - s2;
    const spanT = Math.max(...t) - Math.min(...t) || 1;
    const spanV = Math.max(...v) - Math.min(...v) || 1;
    if (Math.abs(ds) * spanT < spanV * 0.01) return null; // pratikte paralel
    return (v[k] - s2 * t[k] - (v[i] - s1 * t[i])) / ds;
  };
  const toPrice = (x) => (logScale ? Math.exp(x) : x);

  return {
    n, p, t, v, d, eps, logScale, L, D, past, lineAt, intersect, toPrice,
    set: opts.ruleSet || 'fp',
    opts,
    variant,
  };
}

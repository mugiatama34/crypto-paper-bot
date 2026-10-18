// Saf yardımcı fonksiyonlar: sonuç nesneleri, biçimlendirme, oran bantları.
// Bu dosya DOM'a dokunmaz; hem tarayıcıda hem Node testlerinde çalışır.

export const pass = (detail, ratio) => ({ status: 'pass', detail, ratio });
export const fail = (detail, ratio) => ({ status: 'fail', detail, ratio });
export const warn = (detail, ratio, soft = false) => ({ status: 'warn', detail, ratio, soft });
export const info = (detail) => ({ status: 'info', detail });
export const na = (detail) => ({ status: 'na', detail });

// Sayıyı Türkçe ondalık virgülle yaz (ICU'ya bağımlı olmadan).
export function num(x, digits = 2) {
  if (!Number.isFinite(x)) return '—';
  const neg = x < 0;
  const [int, frac] = Math.abs(x).toFixed(digits).split('.');
  const grouped = int.replace(/\B(?=(\d{3})+(?!\d))/g, '.');
  const trimmed = frac ? frac.replace(/0+$/, '') : '';
  return (neg ? '-' : '') + grouped + (trimmed ? ',' + trimmed : '');
}

export const pct = (r, digits = 1) => '%' + num(r * 100, digits);
export const times = (r) => num(r, 3) + '×';

// Fiyatı büyüklüğüne göre okunur biçimde yaz.
export function price(p) {
  if (!Number.isFinite(p)) return '—';
  const a = Math.abs(p);
  if (a >= 1000) return num(p, 0);
  if (a >= 100) return num(p, 1);
  if (a >= 1) return num(p, 3);
  if (a >= 0.01) return num(p, 4);
  return num(p, 6);
}

export const inBand = (x, lo, hi, tol = 0) => x >= lo - tol && x <= hi + tol;

// x, verilen oranlardan birine göreli tol içinde yakınsa o oranı döndür.
export function nearest(x, targets, relTol = 0.07) {
  let best = null;
  for (const t of targets) {
    const err = Math.abs(x - t) / t;
    if (err <= relTol && (!best || err < best.err)) best = { target: t, err };
  }
  return best;
}

// Oran tablosu satırı.
export const ratio = (label, value, typical, ok, asPct = true) => ({
  label,
  value,
  display: asPct ? pct(value) : times(value),
  typical,
  ok,
});

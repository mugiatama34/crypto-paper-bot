// Biçimlendirme yardımcıları.

/** Fiyatın büyüklüğüne göre ondalık hane sayısı (PEPE gibi küçük fiyatlar için daha fazla). */
export function decimals(p) {
  const a = Math.abs(p);
  if (!(a > 0)) return 2;
  if (a >= 1000) return 2;
  if (a >= 1) return 4;
  return Math.min(10, Math.ceil(-Math.log10(a)) + 3);
}

export function fmtPrice(p, ref = p) {
  if (!Number.isFinite(p)) return '–';
  return p.toLocaleString('tr-TR', { minimumFractionDigits: decimals(ref), maximumFractionDigits: decimals(ref) });
}

export const fmtPct = (r, d = 1) => `%${(r * 100).toLocaleString('tr-TR', { minimumFractionDigits: d, maximumFractionDigits: d })}`;
export const fmtX = (r, d = 2) => `${r.toLocaleString('tr-TR', { minimumFractionDigits: d, maximumFractionDigits: d })}×`;

export function fmtTime(ms) {
  return new Date(ms).toLocaleString('tr-TR', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' });
}

export const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

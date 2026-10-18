// Sentetik mum üretici: [[bar, fiyat], ...] dönüş noktaları arasında doğrusal yol.
// Her mumun high/low'u orta fiyatın ±spread kadarıdır; hacim isteğe bağlı segment başına verilir.
export function build(path, { spread = 0.3, volumes = null } = {}) {
  const candles = [];
  for (let s = 0; s < path.length - 1; s++) {
    const [b0, p0] = path[s], [b1, p1] = path[s + 1];
    for (let b = b0; b < b1; b++) {
      const a = p0 + ((p1 - p0) * (b - b0)) / (b1 - b0);
      const z = p0 + ((p1 - p0) * (b + 1 - b0)) / (b1 - b0);
      candles.push({
        time: b * 3600e3, open: a, close: z,
        high: Math.max(a, z) + spread, low: Math.min(a, z) - spread,
        volume: volumes ? volumes[s] : 100, confirmed: true,
      });
    }
  }
  return candles;
}

export const mirror = (candles, axis) =>
  candles.map((c) => ({ ...c, open: axis - c.open, close: axis - c.close, high: axis - c.low, low: axis - c.high }));

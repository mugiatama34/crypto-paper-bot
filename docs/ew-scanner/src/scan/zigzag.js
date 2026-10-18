// ATR tabanlı ZigZag: fiyat son uç noktadan ters yönde (k × ATR) kadar döndüğünde o uç bir pivot olur.
// Saf fonksiyon; mumlar eskiden yeniye { high, low } dizisi.

/** ATR dizisindeki null (ısınma) değerlerini ilk geçerli değerle doldurur. */
export function fillAtr(atrArr) {
  const first = atrArr.find((x) => x != null && x > 0) ?? 0;
  return atrArr.map((x) => (x != null && x > 0 ? x : first));
}

/**
 * @param candles  {high, low}[]
 * @param atrArr   her mum için ATR (fillAtr ile doldurulmuş)
 * @param k        dönüş eşiği çarpanı (k × ATR)
 * @param range    { from, to, start: { i, type: 'H'|'L' } } — aralık ve zorunlu başlangıç pivotu (isteğe bağlı)
 * @returns {{ pivots: {i:number, price:number, type:'H'|'L'}[], tentative: {i, price, type}|null }}
 *   pivots: onaylanmış dönüş noktaları; tentative: henüz onaylanmamış son uç
 */
export function zigzag(candles, atrArr, k, range = {}) {
  const from = range.from ?? 0;
  const to = Math.min(range.to ?? candles.length - 1, candles.length - 1);
  const pivots = [];
  let dir = 0; // 1: yükselişte (tepe aranıyor), -1: düşüşte (dip aranıyor)
  let ext = null; // { i, price }

  let hiI = from, loI = from;
  if (range.start) {
    const s = range.start;
    pivots.push({ i: s.i, price: s.type === 'H' ? candles[s.i].high : candles[s.i].low, type: s.type });
    dir = s.type === 'H' ? -1 : 1;
    ext = null;
  }

  for (let i = from; i <= to; i++) {
    const c = candles[i];
    const thr = k * atrArr[i];
    if (dir === 0) {
      if (c.high > candles[hiI].high) hiI = i;
      if (c.low < candles[loI].low) loI = i;
      if (candles[hiI].high - candles[loI].low >= thr && hiI !== loI) {
        if (loI < hiI) {
          pivots.push({ i: loI, price: candles[loI].low, type: 'L' });
          dir = 1; ext = { i: hiI, price: candles[hiI].high };
        } else {
          pivots.push({ i: hiI, price: candles[hiI].high, type: 'H' });
          dir = -1; ext = { i: loI, price: candles[loI].low };
        }
      }
      continue;
    }
    if (range.start && i === range.start.i) continue;
    if (dir === 1) {
      if (!ext || c.high > ext.price) ext = { i, price: c.high };
      else if (ext.price - c.low >= thr) {
        pivots.push({ i: ext.i, price: ext.price, type: 'H' });
        dir = -1; ext = { i, price: c.low };
      }
    } else {
      if (!ext || c.low < ext.price) ext = { i, price: c.low };
      else if (c.high - ext.price >= thr) {
        pivots.push({ i: ext.i, price: ext.price, type: 'L' });
        dir = 1; ext = { i, price: c.high };
      }
    }
  }
  const tentative = ext ? { ...ext, type: dir === 1 ? 'H' : 'L' } : null;
  return { pivots, tentative };
}

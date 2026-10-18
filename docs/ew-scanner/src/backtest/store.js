// Kayıtlı backtest'ler: tarayıcıda (localStorage) sıkıştırılmış biçimde saklanır.
// İşlemler dizi satırları olarak tutulur; tipik bir çalışma 10–60 KB yer kaplar.
// v2: kopya perp verisi, kayma ve funding ile koşar (CHANGES.md); eski spot çalışmaları aynı listeye karışmasın.
const KEY = 'ew-setup-scanner:backtests:v2';
export const MAX_BYTES = 3.5 * 1024 * 1024; // localStorage ~5 MB; ayarlar vb. için pay bırak

// Sıkıştırılmış işlem satırı alanları (sıra önemli)
const FIELDS = ['coin', 'type', 'dir', 'created', 'entryTime', 'exitTime', 'entry', 'exit', 'stop', 'target', 'filledLegs', 'legs', 'R', 'outcome', 'bars', 'sensitivity', 'score', 'retr', 'timeRatio', 'abc', 'strength', 'inside', 'widthAtr', 'perfect', 'pattern', 'context', 'mfeFib', 'mfeH', 'startFib', 'fundingR', 'fundingCovered'];
const round = (x) => (typeof x === 'number' && !Number.isInteger(x) ? +x.toPrecision(6) : x);

export function packTrades(trades) {
  return trades.map((t) => FIELDS.map((f) => round(t[f] ?? null)));
}
export function unpackTrades(rows) {
  return rows.map((r) => Object.fromEntries(FIELDS.map((f, i) => [f, r[i] ?? null])));
}

export function loadRuns() {
  try {
    const raw = localStorage.getItem(KEY);
    return raw ? JSON.parse(raw) : [];
  } catch {
    return [];
  }
}

export function usedBytes(runs = loadRuns()) {
  return new Blob([JSON.stringify(runs)]).size;
}

/** Çalışmayı ekler. Yer yetmezse en eski çalışmalar silinir; silinenlerin sayısını döndürür. */
export function saveRun(run) {
  let runs = [run, ...loadRuns().filter((r) => r.id !== run.id)];
  let dropped = 0;
  while (runs.length > 1 && usedBytes(runs) > MAX_BYTES) {
    runs.pop();
    dropped++;
  }
  try {
    localStorage.setItem(KEY, JSON.stringify(runs));
  } catch {
    return { ok: false, dropped };
  }
  return { ok: true, dropped };
}

export function deleteRun(id) {
  const runs = loadRuns().filter((r) => r.id !== id);
  try { localStorage.setItem(KEY, JSON.stringify(runs)); } catch { /* yoksay */ }
  return runs;
}

/** İçe aktarma: aynı kimlikli çalışmaların üzerine yazar. */
export function importRuns(list) {
  if (!Array.isArray(list)) throw new Error('Geçersiz dosya: dizi bekleniyordu');
  const valid = list.filter((r) => r && r.id && r.params && Array.isArray(r.trades));
  let runs = loadRuns();
  for (const r of valid) runs = [r, ...runs.filter((x) => x.id !== r.id)];
  runs.sort((a, b) => b.at - a.at);
  localStorage.setItem(KEY, JSON.stringify(runs));
  return valid.length;
}

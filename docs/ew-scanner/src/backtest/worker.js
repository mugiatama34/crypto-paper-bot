// Backtest hesaplama işçisi: arayüzü dondurmadan coin başına simülasyon.
import { backtestCoin } from './engine.js';

self.onmessage = (e) => {
  const { id, candles, cfg } = e.data;
  try {
    self.postMessage({ id, result: backtestCoin(candles, cfg) });
  } catch (err) {
    self.postMessage({ id, error: err.message || String(err) });
  }
};

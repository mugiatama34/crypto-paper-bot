#!/usr/bin/env python3
"""Strateji bahçesinin AİLE KURALLARI — tek kopya (docs/backtest.md > 6q > 5, 6r > 16.1). Ölçümün parçası DEĞİL.

İki ölçüm aynı aileleri okur: model momentumu (§6q, `scripts/measure_model_momentum.py`) ve doku
rejimi (§6r, `scripts/measure_texture_regime.py`). Kurallar burada BAR çerçevesinden bağımsız
saf fonksiyonlardır: girdi bir sembolün bar serisi (açılış damgasıyla dizili), çıktı aynı
dizinde durum `s ∈ {−1, 0, +1}`. Hangi barın hangi zaman diliminden geldiği ve durumun hangi
yürütme ızgarasına eşlendiği ÇAĞIRANIN işidir (`to_grid`).

Aileler iki yerde yazılsaydı §6r'nin "bahçenin aileleri" dediği şey §6q'nun ölçtüğünden sessizce
ayrışabilirdi. Taşıma §6q'nun ağırlıklarını DEĞİŞTİRMEDİ: taban başına ağırlık özeti taşımadan
önceki değerlere bir altın değer testiyle bağlıdır (`tests/test_zoo_families.py`).

Göstergeler `core/indicators.py`den; Bollinger ve Donchian'ın seri hâli oradaki tanımla testle
eşlenir.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from core.indicators import ema_series, rsi_series  # noqa: E402
from core.price_text import truncates_to  # noqa: E402

# §6q > 3 + TADİLAT-3: ince ↔ kaba zaman dilimi tutarlılık kapısı.
CONSISTENCY_REL = 1e-9
CONSISTENCY_MAX_SHARE = 0.001
# OKX 4H geçmişinin bilinen eksik-ondalık penceresi (§6p > TADİLAT-4). Kapı DEĞİL: dışında kalan
# kesinlik farkı başka bir veri özelliği demektir ve ayrı satırda listelenir.
KNOWN_PRECISION_WINDOW = (pd.Timestamp("2022-04-23T00:00:00Z"), pd.Timestamp("2022-06-02T00:00:00Z"))


# --------------------------------------------------------------------------- #
# Zaman
# --------------------------------------------------------------------------- #
def calendar(frame: pd.DataFrame, step: pd.Timedelta) -> pd.DataFrame:
    """Zamana göre kaydırma için tam takvim (eksik bar NaN; §6q > TADİLAT-2 > 3)."""
    if frame.empty:
        return frame
    full = pd.date_range(frame.index[0], frame.index[-1], freq=step, tz="UTC")
    return frame.reindex(full)


def to_grid(state: pd.Series, *, effective_lag: pd.Timedelta, grid: pd.DatetimeIndex) -> np.ndarray:
    """Bar açılışına göre dizili durum → açılışı `≥ kapanış` olan ızgara noktaları; yeni sinyale kadar korunur.

    Kural 12/13: `bar açılışı + lag` (= kapanış) anından itibaren geçerli. Değer hiç yoksa 0.
    """
    if state.empty:
        return np.zeros(len(grid))
    shifted = pd.Series(state.to_numpy(dtype=float), index=state.index + effective_lag).sort_index()
    shifted = shifted[~shifted.index.duplicated(keep="last")]
    return shifted.reindex(grid, method="ffill").fillna(0.0).to_numpy(dtype=float)


def _sign(values: pd.Series) -> pd.Series:
    return np.sign(values).fillna(0.0)


# --------------------------------------------------------------------------- #
# Aileler (§6q > 5). Girdi takvimli ya da ham bar serisi; çıktı bar dizinli durum.
# --------------------------------------------------------------------------- #
def tsmom_rule(closes: pd.Series, bars: int, *, long_only: bool) -> pd.Series:
    """F1: `bars` geriye bakış getirisinin işareti. `closes` TAKVİMLİ (eksik bar kaydırmayı bozmaz)."""
    ret = closes / closes.shift(bars) - 1.0
    return (ret > 0).astype(float).where(ret.notna(), 0.0) if long_only else _sign(ret)


def st_rev_rule(closes: pd.Series, bars: int, *, long_only: bool) -> pd.Series:
    """F4: son `bars` getirisinin TERSİ. `closes` TAKVİMLİ."""
    ret = closes / closes.shift(bars) - 1.0
    return (ret < 0).astype(float).where(ret.notna(), 0.0) if long_only else -_sign(ret)


def ema_stack_rule(close: pd.Series, periods: tuple[int, int, int]) -> pd.Series:
    """F2: `a > b > c` → +1, `a < b < c` → −1, diğer → 0."""
    a, b, c = (ema_series(close, p) for p in periods)
    state = pd.Series(0.0, index=close.index)
    state[(a > b) & (b > c)] = 1.0
    state[(a < b) & (b < c)] = -1.0
    return state


def ma_cross_rule(close: pd.Series, fast: int, slow: int, *, long_only: bool) -> pd.Series:
    """F3: DURUM, olay değil — EMA(hızlı) > EMA(yavaş)."""
    f, s = ema_series(close, fast), ema_series(close, slow)
    defined = f.notna() & s.notna()
    above = (f > s).astype(float)
    state = above if long_only else above * 2.0 - 1.0
    return state.where(defined, 0.0)


def donchian_bands(frame: pd.DataFrame, period: int) -> tuple[pd.Series, pd.Series]:
    """Mevcut barı HARİÇ tutan `period` barlık kanal (`core/indicators.py::donchian`in seri hâli)."""
    upper = frame["high"].astype(float).rolling(period, min_periods=period).max().shift(1)
    lower = frame["low"].astype(float).rolling(period, min_periods=period).min().shift(1)
    return upper, lower


def donchian_rule(frame: pd.DataFrame, period: int, *, long_only: bool) -> pd.Series:
    """F5: kırılım; arada durum KORUNUR (başlangıç 0)."""
    upper, lower = donchian_bands(frame, period)
    close = frame["close"].astype(float)
    event = pd.Series(np.nan, index=frame.index)
    event[close > upper] = 1.0
    event[close < lower] = 0.0 if long_only else -1.0
    return event.ffill().fillna(0.0)


def bollinger_bands(close: pd.Series, period: int, num_std: float) -> tuple[pd.Series, pd.Series, pd.Series]:
    """`core/indicators.py::bollinger`in seri hâli: SMA ± k × popülasyon sapması (ddof=0)."""
    values = close.astype(float)
    middle = values.rolling(period, min_periods=period).mean()
    deviation = values.rolling(period, min_periods=period).std(ddof=0) * num_std
    return middle + deviation, middle, middle - deviation


def extreme_machine(value: np.ndarray, *, long_entry: np.ndarray, short_entry: np.ndarray,
                    long_exit: np.ndarray, short_exit: np.ndarray) -> np.ndarray:
    """F7 durum makinesi (§6q > TADİLAT-2 > 3): giriş > çıkış > koru."""
    out = np.zeros(len(value))
    state = 0.0
    for t in range(len(value)):
        if long_entry[t]:
            state = 1.0
        elif short_entry[t]:
            state = -1.0
        elif state > 0 and long_exit[t]:
            state = 0.0
        elif state < 0 and short_exit[t]:
            state = 0.0
        out[t] = state
    return out


def rsi_extreme_rule(close: pd.Series, period: int, low: float, high: float) -> pd.Series:
    """F7 (RSI): `RSI < alt` long, `> üst` short; 50'yi geçince çıkış."""
    rsi = rsi_series(close, period).to_numpy(dtype=float)
    ok = np.isfinite(rsi)
    state = extreme_machine(rsi, long_entry=ok & (rsi < low), short_entry=ok & (rsi > high),
                            long_exit=ok & (rsi >= 50.0), short_exit=ok & (rsi <= 50.0))
    return pd.Series(state, index=close.index)


def bollinger_extreme_rule(close: pd.Series, num_std: float, period: int = 20) -> pd.Series:
    """F7 (Bollinger): alt bandın altında long, üstünde short; orta bandı geçince çıkış."""
    close = close.astype(float)
    upper, middle, lower = bollinger_bands(close, period, num_std)
    c, u, m, lo = (x.to_numpy(dtype=float) for x in (close, upper, middle, lower))
    ok = np.isfinite(m)
    state = extreme_machine(c, long_entry=ok & (c < lo), short_entry=ok & (c > u),
                            long_exit=ok & (c >= m), short_exit=ok & (c <= m))
    return pd.Series(state, index=close.index)


# --------------------------------------------------------------------------- #
# Ağırlıklar (§6q > 4)
# --------------------------------------------------------------------------- #
def ts_weights(states: np.ndarray, eligible: np.ndarray) -> np.ndarray:
    """`w_i = s_i / |E|` — zaman serisi aileleri; uygun olmayan sembol 0."""
    count = eligible.sum(axis=1).astype(float)
    scale = np.divide(1.0, count, out=np.zeros_like(count), where=count > 0)
    return np.where(eligible, states, 0.0) * scale[:, None]


def xsec_target(row: np.ndarray, ok: np.ndarray, symbols: Sequence[str], *, k: int, long_only: bool) -> np.ndarray:
    """F6 tek bar: getiriye göre azalan sıra, eşitlikte sembol adı; `|uygun| < 2k` → nakit."""
    width = len(symbols)
    members = [j for j in range(width) if ok[j]]
    target = np.zeros(width)
    if len(members) >= 2 * k:
        ordered = sorted(members, key=lambda j: (-row[j], symbols[j]))
        if long_only:
            target[ordered[:k]] = 1.0 / k
        else:
            target[ordered[:k]] = 1.0 / (2 * k)
            target[ordered[-k:]] = -1.0 / (2 * k)
    return target


def xsec_weights(closes: pd.DataFrame, *, bars: int, k: int, long_only: bool, effective_lag: pd.Timedelta,
                 grid: pd.DatetimeIndex, eligible: np.ndarray, symbols: Sequence[str]) -> np.ndarray:
    """F6: her bar kapanışında `bars` geriye bakış getirisine göre sırala; yeni sıralamaya kadar tut.

    `closes` TAKVİMLİ bar × sembol kapanış tablosu. Sıralamaya yalnızca kapanıştan sonraki İLK
    ızgara noktasında uygun ve getirisi tanımlı semboller girer; sonra uygunluğunu yitiren
    sembolün ağırlığı 0 olur, kalanlar yeniden ölçeklenmez (§6q > TADİLAT-2 > 3).
    """
    width = len(symbols)
    if closes.empty:
        return np.zeros((len(grid), width))
    ret = closes / closes.shift(bars) - 1.0
    effective = closes.index + effective_lag
    positions = pd.Index(grid).get_indexer(effective)
    values = ret.to_numpy(dtype=float)
    rows: dict[pd.Timestamp, np.ndarray] = {}
    for t, (when, pos) in enumerate(zip(effective, positions)):
        if pos < 0:
            continue
        row = values[t]
        ok = eligible[pos] & np.isfinite(row)
        rows[when] = xsec_target(row, ok, symbols, k=k, long_only=long_only)
    if not rows:
        return np.zeros((len(grid), width))
    table = pd.DataFrame.from_dict(rows, orient="index").sort_index()
    held = table.reindex(grid, method="ffill").fillna(0.0).to_numpy(dtype=float)
    return np.where(eligible, held, 0.0)


# --------------------------------------------------------------------------- #
# İnce ↔ kaba zaman dilimi tutarlılık kapısı (§6q > 3 + TADİLAT-3)
# --------------------------------------------------------------------------- #
def consistency(fine: pd.DataFrame, coarse: pd.DataFrame, coarse_text: pd.DataFrame | None, *,
                last_sub_offset: pd.Timedelta) -> dict[str, Any]:
    """Kaba barın kapanışı = son ince alt barın kapanışı (göreli ≤ 1e-9).

    Tolerans dışı bir bar, ince kapanış kaba kapanışın HAM METNİNDEKİ ondalık sayısına
    KESİLDİĞİNDE ona tam eşitse "kesinlik farkı"dır (`core/price_text.py`): %0.1 payına
    girmez, ayrı raporlanır. Ham metni olmayan ya da metni float değerle tutmayan bar
    sınıflandırılamaz ve UYUŞMAZLIK sayılır (muhafazakâr taraf).
    """
    if fine.empty or coarse.empty:
        return {"compared": 0, "mismatch": 0, "missing_sub_bar": 0, "share": None, "ok": False}
    sub = fine["close"].reindex(coarse.index + last_sub_offset)
    both = sub.notna().to_numpy()
    index = coarse.index[both]
    c4 = coarse["close"].to_numpy(dtype=float)[both]
    c1 = sub.to_numpy(dtype=float)[both]
    rel = np.abs(c4 - c1) / np.abs(c4)
    off = rel > CONSISTENCY_REL
    texts = coarse_text["close"] if coarse_text is not None and not coarse_text.empty else pd.Series(dtype=object)
    precision = np.zeros(len(index), dtype=bool)
    unclassified = 0
    for k in np.flatnonzero(off):
        text = texts.get(index[k])
        if text is None or float(text) != c4[k]:
            unclassified += 1
            continue
        precision[k] = truncates_to(c1[k], text)
    bad = off & ~precision
    mismatch = int(bad.sum())
    compared = int(both.sum())
    share = mismatch / compared if compared else None
    # Nerede ve ne büyüklükte: bir kapı kararının (sembol dışlama) nedenini okunur kılar.
    stamps = index[bad]
    p_stamps = index[precision]
    lo, hi = KNOWN_PRECISION_WINDOW
    outside = [str(t) for t in p_stamps if not (lo <= t < hi)]
    return {"compared": compared, "mismatch": mismatch, "missing_sub_bar": int((~both).sum()),
            "share": share, "ok": bool(compared and share is not None and share <= CONSISTENCY_MAX_SHARE),
            "first_mismatch": str(stamps[0]) if mismatch else None,
            "last_mismatch": str(stamps[-1]) if mismatch else None,
            "max_rel_diff": float(rel[bad].max()) if mismatch else None,
            "mismatch_bars": [str(t) for t in stamps] if mismatch <= 10 else None,
            "unclassified_no_text": unclassified,
            "precision_differences": {
                "n": int(precision.sum()),
                "first": str(p_stamps[0]) if len(p_stamps) else None,
                "last": str(p_stamps[-1]) if len(p_stamps) else None,
                "max_rel_diff": float(rel[precision].max()) if len(p_stamps) else None,
                "outside_known_window": outside,
            }}

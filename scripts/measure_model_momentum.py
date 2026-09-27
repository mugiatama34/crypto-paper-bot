#!/usr/bin/env python3
"""Model momentumu ölçümü (docs/backtest.md > 6q, TADİLAT-1, TADİLAT-2). Ölçümün parçası DEĞİL.

Ön-kayıt (`ec0d01a`, TADİLAT-1 `551fa67`, TADİLAT-2 `f56f269`) bu betikten ÖNCE, hiçbir veri
görülmeden commit edildi. Betik o metni MEKANİK olarak uygular: 88 taban + tersleri (bahçe),
1H açılıştan açılışa yürütme ızgarası, net maruziyetin eşit ağırlıklı sepetle hedge'lenmesi,
dönem çiftleri üzerinde Spearman IC ve üst − alt beşte bir spread, iki bloklu bootstrap,
BH (q = 0.05, m = 2), A'da ölç B'de doğrula. KAPI hedge'li getiridedir (TADİLAT-1); ham getiri
aynı kurallarla BETİMSEL hesaplanır. Hiçbir sayı burada SEÇİLMEZ.

**Model DEĞİL, ikinci bir backtest DEĞİL** (§6q > 1): bahçe nakit durumlu, stop'suz, vektörel
bir getiri serisi hesabıdır; kural 11'in boyutlandırması onu ifade edemez. Göstergeler
`core/indicators.py`den, günlük kapanış `measure_regime.py::daily_closes`ten, yüzdelik
`backtest_dc.py::_percentiles`ten, dönem A `backtest_ema.py`den, kesim `vault.py`den gelir.

**Salt okunur:** deftere, config'e ve `data/cache/`e yazmaz; KASAYA ait tek bar çekmez (§7.8).

İKİ AŞAMA: `preflight` HİÇBİR getiri, IC ya da sıralama üretmez — kapsam, tutarlılık kapısı,
uygunluk ve çift kuralının sonucu (pozisyon serilerinden); tekrarlanabilir. `measure` tek
seferliktir (§6q > 12).

Çıkış kodları: 0 = rapor yazıldı; 3 = veri kapısı (ölçülecek sembol ya da dönem çifti yok →
rapor YAZILMAZ); 2 = kullanım hatası. Tetikleyicisi `.github/workflows/measure-model-momentum.yml`.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import logging
import math
import random
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from core.config import get_setting, load_config  # noqa: E402
from core.data import fetch_ohlcv, fetch_ohlcv_text  # noqa: E402
from core.indicators import ema_series, rsi_series  # noqa: E402
from core.layers import resolve_layer  # noqa: E402
from core.price_text import truncates_to  # noqa: E402
from scripts.backtest_dc import MIN_CLUSTERS, _percentiles  # noqa: E402
from scripts.backtest_ema import PERIOD_A_CUTOFF, PERIOD_A_START  # noqa: E402
from scripts.measure_regime import daily_closes  # noqa: E402
from scripts.vault import KASA_START, assert_before_vault, vault_now  # noqa: E402

logger = logging.getLogger("measure_model_momentum")

# --- Ön-kayıtlı sayılar (§6q > 3, 7, 8, 9). Hiçbiri CLI girdisi DEĞİLDİR. ---
DATA_START = pd.Timestamp("2021-10-01T00:00:00Z")
ELIGIBLE_AGE = pd.Timedelta(days=60)
QUANTILE = 0.2
HORIZONS = ("month", "week")          # aylık BİRİNCİL (O12), haftalık ikincil — aynı BH ailesi
BLOCKS = {"month": (1, 2), "week": (1, 4)}
BH_Q = 0.05
MDE_Z = 2.802                         # §6j > 9
CONSISTENCY_REL = 1e-9                # §6q > 3
CONSISTENCY_MAX_SHARE = 0.001
# TADİLAT-3: OKX 4H geçmişinin bilinen eksik-ondalık penceresi (§6p > TADİLAT-4). Kapı DEĞİL:
# dışında kalan kesinlik farkı başka bir veri özelliği demektir ve ayrı satırda listelenir.
KNOWN_PRECISION_WINDOW = (pd.Timestamp("2022-04-23T00:00:00Z"), pd.Timestamp("2022-06-02T00:00:00Z"))
HOUR = pd.Timedelta(hours=1)
FOUR = pd.Timedelta(hours=4)
DAY = pd.Timedelta(days=1)

VERIFIED = "DOĞRULANDI"
NOT_VERIFIED = "DOĞRULANMADI"
PASSED = "GEÇTİ"
FAILED = "GEÇMEDİ"
NOT_EVALUABLE = "DEĞERLENDİRİLEMEZ"

# TADİLAT-1 > 7: (hedge'li, ham) -> cümle.
SENTENCES = {
    (VERIFIED, VERIFIED): "model momentumu var — seçim becerisi kalıcı",
    (VERIFIED, NOT_VERIFIED): "seçim becerisi kalıcı; ham testte piyasa hareketi örtüyor",
    (NOT_VERIFIED, VERIFIED): "seçici değil, piyasa zamanlaması",
    (NOT_VERIFIED, NOT_VERIFIED): "ayırt edilemedi",
}


class DataGateError(RuntimeError):
    """Rapor yazılamaz: ölçülecek şey yok."""


@dataclass(frozen=True)
class Settings:
    iterations: int
    alpha: float
    seed: int
    cost_rate: float


# --------------------------------------------------------------------------- #
# Piyasa bağlamı (§6q > 3, 4)
# --------------------------------------------------------------------------- #
@dataclass
class Market:
    hours: pd.DatetimeIndex
    symbols: list[str]
    h1: dict[str, pd.DataFrame]
    h4: dict[str, pd.DataFrame]
    eligible: np.ndarray               # saat × sembol, E_h
    returns: np.ndarray                # saat × sembol, açılıştan açılışa; eksik → 0
    missing: np.ndarray                # uygun ama getirisi tanımsız saatler
    daily: pd.DataFrame = field(default_factory=pd.DataFrame)   # gün × sembol, C_d

    @property
    def count(self) -> np.ndarray:
        return self.eligible.sum(axis=1)


def hour_grid(start: pd.Timestamp, end: pd.Timestamp) -> pd.DatetimeIndex:
    """`start` … `end`'den önceki son saat (açılış damgaları)."""
    return pd.date_range(start, end - HOUR, freq="h", tz="UTC")


def build_market(h1: Mapping[str, pd.DataFrame], h4: Mapping[str, pd.DataFrame], symbols: Sequence[str],
                 *, start: pd.Timestamp = DATA_START, end: pd.Timestamp = KASA_START) -> Market:
    hours = hour_grid(start, end)
    width = len(symbols)
    eligible = np.zeros((len(hours), width), dtype=bool)
    returns = np.zeros((len(hours), width))
    missing = np.zeros((len(hours), width), dtype=bool)
    daily = {}
    for j, symbol in enumerate(symbols):
        f1, f4 = h1[symbol], h4[symbol]
        if f1.empty or f4.empty:
            continue
        opens = f1["open"].reindex(hours).to_numpy(dtype=float)
        nxt = np.append(opens[1:], np.nan)
        r = nxt / opens - 1.0
        has1 = pd.Series(True, index=f1.index).reindex(hours - HOUR).fillna(False).to_numpy(dtype=bool)
        has4 = pd.Series(True, index=f4.index).reindex(hours.floor("4h") - FOUR).fillna(False).to_numpy(dtype=bool)
        aged = hours >= f4.index[0] + ELIGIBLE_AGE
        elig = aged & has1 & has4
        eligible[:, j] = elig
        undefined = ~np.isfinite(r)
        missing[:, j] = elig & undefined
        returns[:, j] = np.where(undefined, 0.0, r)
        daily[symbol] = daily_closes(f4)
    frame = pd.DataFrame(daily).reindex(columns=list(symbols))
    if not frame.empty:
        frame = frame.reindex(pd.date_range(frame.index.min(), frame.index.max(), freq="D", tz="UTC"))
    return Market(hours=hours, symbols=list(symbols), h1=dict(h1), h4=dict(h4), eligible=eligible,
                  returns=returns, missing=missing, daily=frame)


def to_hours(state: pd.Series, *, effective_lag: pd.Timedelta, hours: pd.DatetimeIndex) -> np.ndarray:
    """Bar açılışına göre dizili durum → açılışı `≥ kapanış` olan saatler; yeni sinyale kadar korunur.

    Kural 12/13: `bar açılışı + lag` (= kapanış) anından itibaren geçerli. Değer hiç yoksa 0.
    """
    if state.empty:
        return np.zeros(len(hours))
    shifted = pd.Series(state.to_numpy(dtype=float), index=state.index + effective_lag).sort_index()
    shifted = shifted[~shifted.index.duplicated(keep="last")]
    return shifted.reindex(hours, method="ffill").fillna(0.0).to_numpy(dtype=float)


def _sign(values: pd.Series) -> pd.Series:
    return np.sign(values).fillna(0.0)


def _calendar(frame: pd.DataFrame, step: pd.Timedelta) -> pd.DataFrame:
    """Zamana göre kaydırma için tam takvim (eksik bar NaN; §6q > TADİLAT-2 > 3)."""
    if frame.empty:
        return frame
    full = pd.date_range(frame.index[0], frame.index[-1], freq=step, tz="UTC")
    return frame.reindex(full)


# --------------------------------------------------------------------------- #
# Aileler (§6q > 5). Her kurucu: (piyasa, sembol) -> saatlik durum s_i.
# --------------------------------------------------------------------------- #
def _lag(tau: str) -> pd.Timedelta:
    return {"1H": HOUR, "4H": FOUR, "1D": DAY}[tau]


def _frame(market: Market, symbol: str, tau: str) -> pd.DataFrame:
    return market.h1[symbol] if tau == "1H" else market.h4[symbol]


def tsmom_state(market: Market, symbol: str, *, days: int, long_only: bool) -> np.ndarray:
    closes = _calendar(market.h4[symbol][["close"]], FOUR)["close"]
    ret = closes / closes.shift(6 * days) - 1.0
    state = (ret > 0).astype(float).where(ret.notna(), 0.0) if long_only else _sign(ret)
    return to_hours(state, effective_lag=FOUR, hours=market.hours)


def ema_stack_state(market: Market, symbol: str, *, periods: tuple[int, int, int], tau: str) -> np.ndarray:
    close = _frame(market, symbol, tau)["close"]
    a, b, c = (ema_series(close, p) for p in periods)
    state = pd.Series(0.0, index=close.index)
    state[(a > b) & (b > c)] = 1.0
    state[(a < b) & (b < c)] = -1.0
    return to_hours(state, effective_lag=_lag(tau), hours=market.hours)


def ma_cross_state(market: Market, symbol: str, *, fast: int, slow: int, tau: str, long_only: bool) -> np.ndarray:
    close = _frame(market, symbol, tau)["close"]
    f, s = ema_series(close, fast), ema_series(close, slow)
    defined = f.notna() & s.notna()
    above = (f > s).astype(float)
    state = above if long_only else above * 2.0 - 1.0
    state = state.where(defined, 0.0)
    return to_hours(state, effective_lag=_lag(tau), hours=market.hours)


def st_rev_state(market: Market, symbol: str, *, horizon: str, tau: str, long_only: bool) -> np.ndarray:
    if tau == "1D":
        closes = market.daily[symbol] if symbol in market.daily else pd.Series(dtype=float)
        ret = closes / closes.shift(1) - 1.0
    else:
        step = HOUR if tau == "1H" else FOUR
        closes = _calendar(_frame(market, symbol, tau)[["close"]], step)["close"]
        bars = int(pd.Timedelta(horizon) / step)
        ret = closes / closes.shift(bars) - 1.0
    state = (ret < 0).astype(float).where(ret.notna(), 0.0) if long_only else -_sign(ret)
    return to_hours(state, effective_lag=_lag(tau), hours=market.hours)


def donchian_bands(frame: pd.DataFrame, period: int) -> tuple[pd.Series, pd.Series]:
    """Mevcut barı HARİÇ tutan `period` barlık kanal (`core/indicators.py::donchian`in seri hâli)."""
    upper = frame["high"].astype(float).rolling(period, min_periods=period).max().shift(1)
    lower = frame["low"].astype(float).rolling(period, min_periods=period).min().shift(1)
    return upper, lower


def donchian_state(market: Market, symbol: str, *, period: int, tau: str, long_only: bool) -> np.ndarray:
    frame = _frame(market, symbol, tau)
    upper, lower = donchian_bands(frame, period)
    close = frame["close"].astype(float)
    event = pd.Series(np.nan, index=frame.index)
    event[close > upper] = 1.0
    event[close < lower] = 0.0 if long_only else -1.0
    state = event.ffill().fillna(0.0)
    return to_hours(state, effective_lag=_lag(tau), hours=market.hours)


def bollinger_bands(close: pd.Series, period: int, num_std: float) -> tuple[pd.Series, pd.Series, pd.Series]:
    """`core/indicators.py::bollinger`in seri hâli: SMA ± k × popülasyon sapması (ddof=0)."""
    values = close.astype(float)
    middle = values.rolling(period, min_periods=period).mean()
    deviation = values.rolling(period, min_periods=period).std(ddof=0) * num_std
    return middle + deviation, middle, middle - deviation


def extreme_machine(value: np.ndarray, *, long_entry: np.ndarray, short_entry: np.ndarray,
                    long_exit: np.ndarray, short_exit: np.ndarray) -> np.ndarray:
    """F7 durum makinesi (TADİLAT-2 > 3): giriş > çıkış > koru."""
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


def rsi_extreme_state(market: Market, symbol: str, *, period: int, low: float, high: float, tau: str) -> np.ndarray:
    close = _frame(market, symbol, tau)["close"]
    rsi = rsi_series(close, period).to_numpy(dtype=float)
    ok = np.isfinite(rsi)
    state = extreme_machine(rsi, long_entry=ok & (rsi < low), short_entry=ok & (rsi > high),
                            long_exit=ok & (rsi >= 50.0), short_exit=ok & (rsi <= 50.0))
    return to_hours(pd.Series(state, index=close.index), effective_lag=_lag(tau), hours=market.hours)


def bollinger_extreme_state(market: Market, symbol: str, *, num_std: float, tau: str) -> np.ndarray:
    close = _frame(market, symbol, tau)["close"].astype(float)
    upper, middle, lower = bollinger_bands(close, 20, num_std)
    c, u, m, lo = (x.to_numpy(dtype=float) for x in (close, upper, middle, lower))
    ok = np.isfinite(m)
    state = extreme_machine(c, long_entry=ok & (c < lo), short_entry=ok & (c > u),
                            long_exit=ok & (c >= m), short_exit=ok & (c <= m))
    return to_hours(pd.Series(state, index=close.index), effective_lag=_lag(tau), hours=market.hours)


# --------------------------------------------------------------------------- #
# Bahçe
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Base:
    id: str
    family: str
    build: Callable[[Market], np.ndarray]     # saat × sembol ağırlık matrisi w


def ts_weights(market: Market, state_fn: Callable[[Market, str], np.ndarray]) -> np.ndarray:
    """`w_i = s_i / |E_h|` (§6q > 4); uygun olmayan sembol 0."""
    states = np.column_stack([state_fn(market, s) for s in market.symbols]) if market.symbols else \
        np.zeros((len(market.hours), 0))
    count = market.count.astype(float)
    scale = np.divide(1.0, count, out=np.zeros_like(count), where=count > 0)
    return np.where(market.eligible, states, 0.0) * scale[:, None]


def xsec_weights(market: Market, *, days: int, k: int, long_only: bool) -> np.ndarray:
    """F6: gün kapanışında L günlük getiriye göre sırala; ağırlık o günün 24 saati (§6q > 5)."""
    daily = market.daily
    width = len(market.symbols)
    if daily.empty:
        return np.zeros((len(market.hours), width))
    ret = daily / daily.shift(days) - 1.0
    effective = daily.index + DAY
    hour_pos = pd.Index(market.hours)
    rows = {}
    for day, when in zip(daily.index, effective):
        pos = hour_pos.get_indexer([when])[0]
        if pos < 0:
            continue
        row = ret.loc[day].to_numpy(dtype=float)
        ok = market.eligible[pos] & np.isfinite(row)
        members = [j for j in range(width) if ok[j]]
        target = np.zeros(width)
        if len(members) >= 2 * k:
            ordered = sorted(members, key=lambda j: (-row[j], market.symbols[j]))
            if long_only:
                target[ordered[:k]] = 1.0 / k
            else:
                target[ordered[:k]] = 1.0 / (2 * k)
                target[ordered[-k:]] = -1.0 / (2 * k)
        rows[when] = target
    if not rows:
        return np.zeros((len(market.hours), width))
    table = pd.DataFrame.from_dict(rows, orient="index").sort_index()
    held = table.reindex(market.hours, method="ffill").fillna(0.0).to_numpy(dtype=float)
    return np.where(market.eligible, held, 0.0)


def zoo() -> list[Base]:
    """88 taban, §6q > 5'in tablosu ve SIRASI (çift kuralı sıraya bakar)."""
    bases: list[Base] = []

    def ts(bid: str, family: str, fn: Callable[[Market, str], np.ndarray]) -> None:
        bases.append(Base(bid, family, lambda m, fn=fn: ts_weights(m, fn)))

    for days in (1, 3, 7, 14, 30):
        for lo in (False, True):
            ts(f"tsmom_{days}g_{'lo' if lo else 'ls'}", "F1",
               lambda m, s, d=days, lo=lo: tsmom_state(m, s, days=d, long_only=lo))
    for periods in ((5, 21, 50), (8, 21, 55), (10, 30, 100)):
        for tau in ("1H", "4H"):
            ts(f"ema_stack_{'-'.join(map(str, periods))}_{tau}", "F2",
               lambda m, s, p=periods, t=tau: ema_stack_state(m, s, periods=p, tau=t))
    for fast, slow in ((5, 20), (10, 30), (20, 50), (21, 55), (50, 200)):
        for tau in ("1H", "4H"):
            for lo in (False, True):
                ts(f"ma_cross_{fast}-{slow}_{tau}_{'lo' if lo else 'ls'}", "F3",
                   lambda m, s, f=fast, sl=slow, t=tau, lo=lo: ma_cross_state(m, s, fast=f, slow=sl, tau=t,
                                                                               long_only=lo))
    for horizon, tau in (("1h", "1H"), ("4h", "1H"), ("4h", "4H"), ("1D", "1D")):
        for lo in (False, True):
            ts(f"st_rev_{horizon}_{tau}_{'lo' if lo else 'ls'}", "F4",
               lambda m, s, h=horizon, t=tau, lo=lo: st_rev_state(m, s, horizon=h, tau=t, long_only=lo))
    for period in (20, 55, 120):
        for tau in ("1H", "4H"):
            for lo in (False, True):
                ts(f"donchian_{period}_{tau}_{'lo' if lo else 'ls'}", "F5",
                   lambda m, s, n=period, t=tau, lo=lo: donchian_state(m, s, period=n, tau=t, long_only=lo))
    for days in (1, 3, 7, 14, 30):
        for k in (2, 4):
            for lo in (False, True):
                bases.append(Base(f"xsec_{days}g_k{k}_{'lo' if lo else 'ls'}", "F6",
                                  lambda m, d=days, k=k, lo=lo: xsec_weights(m, days=d, k=k, long_only=lo)))
    for period, low, high in ((2, 10.0, 90.0), (2, 25.0, 75.0), (14, 30.0, 70.0), (14, 20.0, 80.0)):
        for tau in ("1H", "4H"):
            ts(f"rsi_{period}_{int(low)}-{int(high)}_{tau}", "F7",
               lambda m, s, p=period, a=low, b=high, t=tau: rsi_extreme_state(m, s, period=p, low=a, high=b, tau=t))
    for k in (2.0, 2.5):
        for tau in ("1H", "4H"):
            ts(f"bollinger_20_{k}_{tau}", "F7",
               lambda m, s, k=k, t=tau: bollinger_extreme_state(m, s, num_std=k, tau=t))
    return bases


def cash_share(weights: np.ndarray, market: Market) -> dict[str, float | None]:
    """Dönem başına, evrende en az bir uygun sembol varken HİÇ pozisyon taşınmayan saat payı.

    Yalnızca ağırlıklardan (pozisyonlardan) gelir, getiri görmez — preflight'ta raporlanır
    (§6q > TADİLAT-3): F6'nın k=4 kolu gibi evren darken fiilen nakitte kalan bir taban aile
    kırılımında görünmez biçimde boş kalırdı.
    """
    flat = ~np.any(weights != 0.0, axis=1)
    live = market.count > 0
    out: dict[str, float | None] = {}
    for dn, (lo, hi) in period_bounds().items():
        mask = live & (market.hours >= lo) & (market.hours < hi)
        out[dn] = float(flat[mask].mean()) if mask.any() else None
    return out


def eligibility_by_month(market: Market) -> dict[str, dict[str, float]]:
    """UTC ayı başına uygun sembol sayısı (en az / medyan / en çok), dönem A'dan itibaren."""
    series = pd.Series(market.count, index=market.hours)
    series = series[series.index >= period_bounds()["A"][0]]
    grouped = series.groupby(series.index.strftime("%Y-%m"))
    return {month: {"min": int(v.min()), "median": float(v.median()), "max": int(v.max())}
            for month, v in grouped}


def weight_digest(weights: np.ndarray) -> str:
    """Birebir eşitlik için özet; `-0.0` ile `0.0` aynı sayılır."""
    return hashlib.sha256(np.ascontiguousarray(weights + 0.0).tobytes()).hexdigest()


def deduplicate(digests: Sequence[tuple[str, str, str, bool]]) -> dict[str, Any]:
    """TADİLAT-2 > 3: taban düzeyinde çift kuralı. Girdi: (id, özet(w), özet(−w), hepsi sıfır)."""
    seen: dict[str, str] = {}
    kept, dropped, zero = [], [], []
    for bid, pos, neg, all_zero in digests:
        if pos in seen or neg in seen:
            negated = pos not in seen
            dropped.append({"id": bid, "twin_of": seen[neg] if negated else seen[pos], "negated": negated})
            continue
        seen[pos] = bid
        kept.append(bid)
        if all_zero:
            zero.append(bid)
    n = 2 * len(kept) - len(zero)
    return {"kept": kept, "dropped": dropped, "all_zero": zero, "n": n}


# --------------------------------------------------------------------------- #
# Saatlik getiri ve dönemler (§6q > 4, 7, 10)
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Hourly:
    gross: np.ndarray
    hedged: np.ndarray
    cost: np.ndarray
    cost_hedged: np.ndarray
    exposure: np.ndarray
    turnover: np.ndarray


def hedge(weights: np.ndarray, eligible: np.ndarray) -> np.ndarray:
    """TADİLAT-1 > 2: `v_i = w_i − e / |E|` (i ∈ E)."""
    count = eligible.sum(axis=1).astype(float)
    exposure = weights.sum(axis=1)
    per = np.divide(exposure, count, out=np.zeros_like(exposure), where=count > 0)
    return weights - np.where(eligible, per[:, None], 0.0)


def turnover(weights: np.ndarray) -> np.ndarray:
    previous = np.vstack([np.zeros((1, weights.shape[1])), weights[:-1]])
    return np.abs(weights - previous).sum(axis=1)


def hourly(weights: np.ndarray, market: Market, *, cost_rate: float) -> Hourly:
    v = hedge(weights, market.eligible)
    turn = turnover(weights)
    return Hourly(
        gross=(weights * market.returns).sum(axis=1),
        hedged=(v * market.returns).sum(axis=1),
        cost=cost_rate * turn,
        cost_hedged=cost_rate * turnover(v),
        exposure=weights.sum(axis=1),
        turnover=turn,
    )


def basket(market: Market) -> np.ndarray:
    count = market.count.astype(float)
    total = np.where(market.eligible, market.returns, 0.0).sum(axis=1)
    return np.divide(total, count, out=np.zeros_like(total), where=count > 0)


@dataclass(frozen=True)
class PeriodTable:
    horizon: str
    labels: list[str]
    dones: list[str]                  # her dönemin ait olduğu ölçüm dönemi (A/B)
    ids: np.ndarray                   # saat -> dönem indeksi, tam olmayan saatler −1
    pairs: dict[str, list[tuple[int, int]]]


def period_bounds() -> dict[str, tuple[pd.Timestamp, pd.Timestamp]]:
    a_start = pd.Timestamp(PERIOD_A_START).tz_convert("UTC")
    cutoff = pd.Timestamp(PERIOD_A_CUTOFF).tz_convert("UTC")
    return {"A": (a_start, cutoff), "B": (cutoff, KASA_START)}


def period_table(hours: pd.DatetimeIndex, horizon: str) -> PeriodTable:
    """Tam dönemler; ikisi de AYNI ölçüm döneminde olan ardışık çiftler (§6q > 7, 10)."""
    naive = hours.tz_convert("UTC").tz_localize(None)
    if horizon == "month":
        starts = naive.to_period("M").start_time
        ends = (naive.to_period("M") + 1).start_time
    elif horizon == "week":
        starts = naive.normalize() - pd.to_timedelta(naive.dayofweek, unit="D")
        ends = starts + pd.Timedelta(days=7)
    else:
        raise ValueError(f"bilinmeyen ufuk: {horizon}")
    starts = pd.DatetimeIndex(starts).tz_localize("UTC")
    ends = pd.DatetimeIndex(ends).tz_localize("UTC")
    bounds = period_bounds()
    labels: list[str] = []
    dones: list[str] = []
    ids = np.full(len(hours), -1)
    index_of: dict[pd.Timestamp, int] = {}
    for position, (s, e) in enumerate(zip(starts, ends)):
        dn = next((k for k, (lo, hi) in bounds.items() if lo <= s and e <= hi), None)
        if dn is None:
            continue
        key = s
        if key not in index_of:
            index_of[key] = len(labels)
            labels.append(_label(s, horizon))
            dones.append(dn)
        ids[position] = index_of[key]
    starts_of = sorted(index_of)
    pairs: dict[str, list[tuple[int, int]]] = {"A": [], "B": []}
    for prev, cur in zip(starts_of, starts_of[1:]):
        i, j = index_of[prev], index_of[cur]
        if _next_start(prev, horizon) == cur and dones[i] == dones[j]:
            pairs[dones[i]].append((i, j))
    return PeriodTable(horizon=horizon, labels=labels, dones=dones, ids=ids, pairs=pairs)


def _next_start(start: pd.Timestamp, horizon: str) -> pd.Timestamp:
    if horizon == "week":
        return start + pd.Timedelta(days=7)
    return (start.tz_localize(None).to_period("M") + 1).start_time.tz_localize("UTC")


def _label(start: pd.Timestamp, horizon: str) -> str:
    if horizon == "month":
        return start.strftime("%Y-%m")
    iso = start.isocalendar()
    return f"{iso[0]}-W{iso[1]:02d}"


def period_sums(values: np.ndarray, table: PeriodTable) -> np.ndarray:
    mask = table.ids >= 0
    return np.bincount(table.ids[mask], weights=values[mask], minlength=len(table.labels))


def period_means(values: np.ndarray, table: PeriodTable) -> np.ndarray:
    mask = table.ids >= 0
    counts = np.bincount(table.ids[mask], minlength=len(table.labels)).astype(float)
    sums = np.bincount(table.ids[mask], weights=values[mask], minlength=len(table.labels))
    return np.divide(sums, counts, out=np.full_like(sums, np.nan), where=counts > 0)


# --------------------------------------------------------------------------- #
# İstatistik (§6q > 7, 8, 9)
# --------------------------------------------------------------------------- #
def spearman(x: np.ndarray, y: np.ndarray) -> float:
    if len(x) < 3:
        return float("nan")
    rx = pd.Series(x).rank(method="average").to_numpy()
    ry = pd.Series(y).rank(method="average").to_numpy()
    if rx.std() == 0.0 or ry.std() == 0.0:
        return float("nan")
    return float(np.corrcoef(rx, ry)[0, 1])


def quintile_size(n: int) -> int:
    return max(1, int(math.floor(QUANTILE * n)))


def ranked(previous: np.ndarray, ids: Sequence[str]) -> np.ndarray:
    """Azalan geçen dönem getirisi; eşitlikte strateji kimliği (artan)."""
    return np.array(sorted(range(len(ids)), key=lambda i: (-previous[i], ids[i])))


def pair_stats(prev: np.ndarray, cur: np.ndarray, ids: Sequence[str]) -> tuple[float, float, np.ndarray, np.ndarray]:
    """(IC_t, spread_t, üst indeksler, alt indeksler)."""
    order = ranked(prev, ids)
    nq = quintile_size(len(ids))
    top, bottom = order[:nq], order[-nq:]
    return spearman(prev, cur), float(cur[top].mean() - cur[bottom].mean()), top, bottom


def two_sided_p(draws: Sequence[float]) -> float:
    """§6l > 6: `min(1, 2·min(#≤0+1, #≥0+1)/(B+1))`."""
    finite = [d for d in draws if math.isfinite(d)]
    b = len(finite)
    if b == 0:
        return 1.0
    below = sum(1 for d in finite if d <= 0.0)
    above = sum(1 for d in finite if d >= 0.0)
    return min(1.0, 2.0 * min(below + 1, above + 1) / (b + 1))


def block_bootstrap(ic: np.ndarray, spread: np.ndarray, *, block: int, iterations: int,
                    seed: str, alpha: float) -> dict[str, Any]:
    """Ardışık sabit bloklar, yerine koyarak; IC ve spread AYNI çekilişten (§6q > 8)."""
    n = len(ic)
    groups = [np.arange(start, min(start + block, n)) for start in range(0, n, block)]
    entry: dict[str, Any] = {"block": block, "clusters": len(groups), "evaluable": len(groups) >= MIN_CLUSTERS}
    if n == 0:
        entry.update({"ic": {"low": None, "high": None, "p": 1.0, "se": None},
                      "spread": {"low": None, "high": None, "p": 1.0, "se": None}})
        return entry
    rng = random.Random(seed)
    draws_ic, draws_sp = [], []
    for _ in range(int(iterations)):
        idx = np.concatenate([groups[rng.randrange(len(groups))] for _ in groups])
        draws_ic.append(float(np.nanmean(ic[idx])))
        draws_sp.append(float(np.nanmean(spread[idx])))
    for key, draws in (("ic", draws_ic), ("spread", draws_sp)):
        finite = [d for d in draws if math.isfinite(d)]
        low, high = _percentiles(finite, alpha) if finite else (None, None)
        entry[key] = {"low": low, "high": high, "p": two_sided_p(draws),
                      "se": float(np.std(finite, ddof=1)) if len(finite) > 1 else None}
    return entry


def binding(blocks: Mapping[str, Mapping[str, Any]], key: str) -> tuple[float | None, float]:
    """(iki alt sınırın MİNİMUMU, iki p'nin MAKSİMUMU); değerlendirilemeyen blok → (None, 1)."""
    if not all(b["evaluable"] for b in blocks.values()):
        return None, 1.0
    lows = [b[key]["low"] for b in blocks.values()]
    if any(low is None for low in lows):
        return None, 1.0
    return min(lows), max(b[key]["p"] for b in blocks.values())


def measure_series(returns: np.ndarray, ids: Sequence[str], pairs: Sequence[tuple[int, int]], *,
                   horizon: str, seed: str, settings: Settings) -> dict[str, Any]:
    """Bir (büyüklük, ufuk, dönem): çift serisi, ortalamalar, iki bloklu aralıklar, bağlayıcılar."""
    ic, sp = [], []
    for i, j in pairs:
        a, b, _, _ = pair_stats(returns[:, i], returns[:, j], ids)
        ic.append(a)
        sp.append(b)
    ic_arr, sp_arr = np.array(ic, dtype=float), np.array(sp, dtype=float)
    blocks = {f"b{k}": block_bootstrap(ic_arr, sp_arr, block=k, iterations=settings.iterations,
                                       seed=f"{seed}:b{k}", alpha=settings.alpha)
              for k in BLOCKS[horizon]}
    low_ic, p_ic = binding(blocks, "ic")
    low_sp, p_sp = binding(blocks, "spread")
    mean_ic = float(np.nanmean(ic_arr)) if len(ic_arr) else float("nan")
    mean_sp = float(np.nanmean(sp_arr)) if len(sp_arr) else float("nan")
    sd_ic = float(np.nanstd(ic_arr, ddof=1)) if len(ic_arr) > 1 else float("nan")
    return {
        "pairs": len(pairs),
        "mean_ic": mean_ic,
        "mean_spread": mean_sp,
        "blocks": blocks,
        "binding": {"ic_low": low_ic, "spread_low": low_sp, "p_ic": p_ic, "p_spread": p_sp,
                    "p": max(p_ic, p_sp)},
        "conditions_ab": bool(len(pairs) and mean_ic > 0 and mean_sp > 0 and low_ic is not None
                              and low_sp is not None and low_ic > 0 and low_sp > 0),
        "precision": {"sd_ic": sd_ic, "mde_ic_iid": MDE_Z * sd_ic / math.sqrt(len(ic_arr))
                      if len(ic_arr) > 1 else None},
        "series": [{"ic": a, "spread": b} for a, b in zip(ic, sp)],
    }


def bh_accept(pvalues: Mapping[str, float], q: float = BH_Q) -> set[str]:
    """Benjamini–Hochberg adım-yukarı: `p₍ᵢ₎ ≤ (i/m)·q` olan en büyük i'ye kadar."""
    m = len(pvalues)
    if m == 0:
        return set()
    ordered = sorted(pvalues.items(), key=lambda kv: (kv[1], kv[0]))
    cut = 0
    for rank, (_, p) in enumerate(ordered, start=1):
        if p <= rank / m * q:
            cut = rank
    return {name for name, _ in ordered[:cut]}


def decide(results: Mapping[str, Mapping[str, Mapping[str, Any]]]) -> dict[str, Any]:
    """§6q > 9: A'da (a)–(c), m = 2; B'de yalnızca A'da geçenler, m_B = sayıları.

    `results[dönem][ufuk]` = `measure_series` çıktısı.
    """
    out: dict[str, Any] = {}
    a_ok = {h for h in HORIZONS if results["A"][h]["conditions_ab"]}
    a_bh = bh_accept({h: results["A"][h]["binding"]["p"] for h in HORIZONS})
    passed_a = sorted(a_ok & a_bh)
    b_family = {h: results["B"][h]["binding"]["p"] for h in passed_a}
    b_bh = bh_accept(b_family)
    passed_b = sorted({h for h in passed_a if results["B"][h]["conditions_ab"]} & b_bh)
    for h in HORIZONS:
        out[h] = {
            "A": PASSED if h in passed_a else FAILED,
            "B": (PASSED if h in passed_b else FAILED) if h in passed_a else "bilgi — doğrulama değil",
            "verdict": VERIFIED if h in passed_b else NOT_VERIFIED,
        }
    out["m_A"] = len(HORIZONS)
    out["m_B"] = len(passed_a)
    out["overall"] = VERIFIED if passed_b else NOT_VERIFIED
    return out


def reading(hedged: str, raw: str) -> str:
    return SENTENCES[(hedged, raw)]


# --------------------------------------------------------------------------- #
# Ölçüm
# --------------------------------------------------------------------------- #
@dataclass
class ZooSums:
    """Her ufuk için strateji × dönem matrisleri (tersler dâhil)."""
    ids: list[str]
    families: list[str]
    tables: dict[str, PeriodTable]
    gross: dict[str, np.ndarray]
    hedged: dict[str, np.ndarray]
    net: dict[str, np.ndarray]
    net_hedged: dict[str, np.ndarray]
    exposure: dict[str, np.ndarray]
    market: dict[str, np.ndarray]
    turnover_per_day: dict[str, float]
    cost_per_day: dict[str, float]


def build_zoo(market: Market, bases: Sequence[Base], *, settings: Settings,
              with_returns: bool) -> tuple[dict[str, Any], ZooSums | None]:
    """Ağırlıkları kur, çift kuralını uygula; `with_returns` False iken HİÇBİR getiri hesaplanmaz."""
    a_start = period_bounds()["A"][0]
    from_a = market.hours >= a_start
    tables = {h: period_table(market.hours, h) for h in HORIZONS} if with_returns else {}
    digests = []
    cash: dict[str, dict[str, float | None]] = {}
    per_base: dict[str, dict[str, Any]] = {}
    for base in bases:
        w = base.build(market)
        sub = w[from_a]
        digests.append((base.id, weight_digest(sub), weight_digest(-sub), bool(not np.any(sub))))
        cash[base.id] = cash_share(w, market)
        if with_returns:
            hr = hourly(w, market, cost_rate=settings.cost_rate)
            per_base[base.id] = {
                "turnover_day": float(hr.turnover[from_a].mean() * 24.0),
                "cost_day": float(hr.cost[from_a].mean() * 24.0),
                **{h: {"g": period_sums(hr.gross, t), "a": period_sums(hr.hedged, t),
                       "c": period_sums(hr.cost, t), "ca": period_sums(hr.cost_hedged, t),
                       "e": period_means(hr.exposure, t)} for h, t in tables.items()},
            }
        del w
    dedup = deduplicate(digests)
    families = {b.id: b.family for b in bases}
    dedup["cash_share"] = {bid: {"family": families[bid], **v} for bid, v in cash.items()}
    if not with_returns:
        return dedup, None

    m_hour = basket(market)
    keep = set(dedup["kept"])
    zero = set(dedup["all_zero"])
    ids: list[str] = []
    families: list[str] = []
    cols: dict[str, dict[str, list[np.ndarray]]] = {h: {k: [] for k in ("g", "a", "n", "na", "e")} for h in HORIZONS}
    turn_day: dict[str, float] = {}
    cost_day: dict[str, float] = {}
    for base in bases:
        if base.id not in keep:
            continue
        data = per_base[base.id]
        variants = [(base.id, 1.0)] + ([] if base.id in zero else [(f"{base.id}~ters", -1.0)])
        for sid, sign in variants:
            ids.append(sid)
            families.append(base.family)
            turn_day[sid] = data["turnover_day"]
            cost_day[sid] = data["cost_day"]
            for h in HORIZONS:
                d = data[h]
                g, a = sign * d["g"], sign * d["a"]
                cols[h]["g"].append(g)
                cols[h]["a"].append(a)
                cols[h]["n"].append(g - d["c"])
                cols[h]["na"].append(a - d["ca"])
                cols[h]["e"].append(sign * d["e"])
    sums = ZooSums(
        ids=ids, families=families, tables=tables,
        gross={h: np.vstack(cols[h]["g"]) for h in HORIZONS},
        hedged={h: np.vstack(cols[h]["a"]) for h in HORIZONS},
        net={h: np.vstack(cols[h]["n"]) for h in HORIZONS},
        net_hedged={h: np.vstack(cols[h]["na"]) for h in HORIZONS},
        exposure={h: np.vstack(cols[h]["e"]) for h in HORIZONS},
        market={h: period_sums(m_hour, tables[h]) for h in HORIZONS},
        turnover_per_day=turn_day, cost_per_day=cost_day,
    )
    return dedup, sums


def evaluate(sums: ZooSums, *, settings: Settings) -> dict[str, Any]:
    measures = {"hedged": sums.hedged, "raw": sums.gross}
    suffix = {"hedged": "", "raw": ":ham"}
    per_measure: dict[str, Any] = {}
    for name, mats in measures.items():
        results: dict[str, dict[str, Any]] = {"A": {}, "B": {}}
        for dn in ("A", "B"):
            for h in HORIZONS:
                seed = f"{settings.seed}:mmom:{h}:{dn}{suffix[name]}"
                results[dn][h] = measure_series(mats[h], sums.ids, sums.tables[h].pairs[dn],
                                                horizon=h, seed=seed, settings=settings)
        per_measure[name] = {"periods": results, "decision": decide(results)}
    labels = {h: reading(per_measure["hedged"]["decision"][h]["verdict"],
                         per_measure["raw"]["decision"][h]["verdict"]) for h in HORIZONS}
    overall = reading(per_measure["hedged"]["decision"]["overall"], per_measure["raw"]["decision"]["overall"])
    return {
        "gate": "hedged",
        "measures": per_measure,
        "reading": {"per_horizon": labels, "overall": overall},
        "selector_thesis_allowed": per_measure["hedged"]["decision"]["overall"] == VERIFIED,
        "families": family_breakdown(sums, settings=settings),
        "composition": composition(sums),
        "net": net_report(sums),
        "diagnostics": diagnostics(sums),
        "turnover": {"per_strategy_per_day": sums.turnover_per_day,
                     "cost_drag_per_strategy_per_day": sums.cost_per_day,
                     "per_family": _family_mean(sums, sums.turnover_per_day),
                     "cost_per_family": _family_mean(sums, sums.cost_per_day)},
    }


def _family_mean(sums: ZooSums, values: Mapping[str, float]) -> dict[str, float]:
    out: dict[str, list[float]] = {}
    for sid, fam in zip(sums.ids, sums.families):
        out.setdefault(fam, []).append(values[sid])
    return {fam: float(np.mean(v)) for fam, v in sorted(out.items())}


def family_breakdown(sums: ZooSums, *, settings: Settings) -> dict[str, Any]:
    """BETİMSEL (§6q > 9): aile-içi IC ve spread; kapı yok, BH yok."""
    out: dict[str, Any] = {"note": "betimsel — kapı değil"}
    for fam in sorted(set(sums.families)):
        rows = [i for i, f in enumerate(sums.families) if f == fam]
        ids = [sums.ids[i] for i in rows]
        fam_out: dict[str, Any] = {"strategies": len(rows)}
        for name, mats, suffix in (("hedged", sums.hedged, ""), ("raw", sums.gross, ":ham")):
            fam_out[name] = {
                dn: {h: _trim(measure_series(mats[h][rows], ids, sums.tables[h].pairs[dn], horizon=h,
                                             seed=f"{settings.seed}:mmom:{h}:{dn}{suffix}:aile:{fam}",
                                             settings=settings))
                     for h in HORIZONS}
                for dn in ("A", "B")
            }
        out[fam] = fam_out
    return out


def _trim(block: Mapping[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in block.items() if k not in ("series", "conditions_ab")}


def composition(sums: ZooSums) -> dict[str, Any]:
    """Üst ve alt beşte birin aile payı ↔ bahçedeki payı (hedge'li sıralama)."""
    families = sorted(set(sums.families))
    zoo_share = {f: sums.families.count(f) / len(sums.families) for f in families}
    out: dict[str, Any] = {"zoo_share": zoo_share}
    for h in HORIZONS:
        for dn in ("A", "B"):
            top_counts = {f: 0 for f in families}
            bottom_counts = {f: 0 for f in families}
            pairs = sums.tables[h].pairs[dn]
            nq = quintile_size(len(sums.ids))
            for i, j in pairs:
                _, _, top, bottom = pair_stats(sums.hedged[h][:, i], sums.hedged[h][:, j], sums.ids)
                for k in top:
                    top_counts[sums.families[k]] += 1
                for k in bottom:
                    bottom_counts[sums.families[k]] += 1
            denom = max(1, len(pairs) * nq)
            out[f"{h}:{dn}"] = {"top": {f: c / denom for f, c in top_counts.items()},
                                "bottom": {f: c / denom for f, c in bottom_counts.items()}}
    return out


def net_report(sums: ZooSums) -> dict[str, Any]:
    """Kârlılık — BETİMSEL (§6q > 7): üst/alt beşte birin NET sonraki dönem getirisi, bahçenin neti."""
    out: dict[str, Any] = {}
    for name, rank_mats, net_mats in (("hedged", sums.hedged, sums.net_hedged), ("raw", sums.gross, sums.net)):
        per: dict[str, Any] = {}
        for h in HORIZONS:
            for dn in ("A", "B"):
                tops, bottoms, zoo_means = [], [], []
                for i, j in sums.tables[h].pairs[dn]:
                    _, _, top, bottom = pair_stats(rank_mats[h][:, i], rank_mats[h][:, j], sums.ids)
                    tops.append(float(net_mats[h][top, j].mean()))
                    bottoms.append(float(net_mats[h][bottom, j].mean()))
                    zoo_means.append(float(net_mats[h][:, j].mean()))
                per[f"{h}:{dn}"] = {
                    "top_net": _mean(tops), "bottom_net": _mean(bottoms),
                    "net_spread": _mean([a - b for a, b in zip(tops, bottoms)]), "zoo_net": _mean(zoo_means),
                }
        out[name] = per
    return out


def diagnostics(sums: ZooSums) -> dict[str, Any]:
    """TADİLAT-1 > 5: piyasa tekrarı ayrımı ve beta sızıntısı (hedge'li büyüklük için); betimsel."""
    out: dict[str, Any] = {}
    for h in HORIZONS:
        for dn in ("A", "B"):
            same, differ, leak, leak_signed = [], [], [], []
            mkt = sums.market[h]
            for i, j in sums.tables[h].pairs[dn]:
                ic = spearman(sums.hedged[h][:, i], sums.hedged[h][:, j])
                (same if np.sign(mkt[i]) == np.sign(mkt[j]) else differ).append(ic)
                rho = spearman(sums.exposure[h][:, i], sums.hedged[h][:, j])
                leak.append(rho)
                leak_signed.append(rho * float(np.sign(mkt[j])))
            out[f"{h}:{dn}"] = {
                "market_repeat": {"same_sign_pairs": len(same), "mean_ic_same": _mean(same),
                                  "differ_sign_pairs": len(differ), "mean_ic_differ": _mean(differ)},
                "beta_leak": {"mean_rho_exposure_vs_hedged": _mean(leak),
                              "mean_rho_times_market_sign": _mean(leak_signed)},
            }
    return out


def _mean(values: Sequence[float]) -> float | None:
    finite = [v for v in values if v is not None and math.isfinite(v)]
    return float(np.mean(finite)) if finite else None


def period_matrix(sums: ZooSums) -> pd.DataFrame:
    """Sabitlenen strateji × dönem tablosu (§6q > 16)."""
    rows = []
    for h in HORIZONS:
        table = sums.tables[h]
        for p, label in enumerate(table.labels):
            for k, sid in enumerate(sums.ids):
                rows.append({"horizon": h, "period": label, "dönem": table.dones[p], "strategy": sid,
                             "family": sums.families[k], "gross": sums.gross[h][k, p],
                             "hedged": sums.hedged[h][k, p], "net": sums.net[h][k, p],
                             "net_hedged": sums.net_hedged[h][k, p], "exposure_mean": sums.exposure[h][k, p]})
    return pd.DataFrame(rows)


# --------------------------------------------------------------------------- #
# Veri
# --------------------------------------------------------------------------- #
def fetch_all(config: Mapping[str, Any], symbols: Sequence[str], *, timeframe: str,
              cache_dir: str) -> dict[str, pd.DataFrame]:
    """Kasa başlangıcında KAPANMIŞ barlar; kasaya ait tek bar çekilmez (§7.8)."""
    now = vault_now()
    step = HOUR if timeframe == "1H" else FOUR
    bars = int(math.ceil((now - DATA_START) / step)) + 12
    run_config = copy.deepcopy(dict(config))
    run_config["timeframe"] = timeframe
    run_config["data"] = {**run_config["data"], "history_bars": bars, "cache_dir": cache_dir}
    out: dict[str, pd.DataFrame] = {}
    for symbol in symbols:
        frame = fetch_ohlcv(run_config, symbol, now=now)
        frame = frame[frame.index >= DATA_START] if not frame.empty else frame
        if not frame.empty:
            assert_before_vault(frame.index[-1] + step, what=f"{symbol} {timeframe} son bar kapanışı")
        out[symbol] = frame
    return out


def fetch_all_text(config: Mapping[str, Any], symbols: Sequence[str], *, timeframe: str) -> dict[str, pd.DataFrame]:
    """Ham METİN mumlar (TADİLAT-3); önbelleğe dokunmaz, kasaya ait bar çekmez (§7.8)."""
    now = vault_now()
    step = HOUR if timeframe == "1H" else FOUR
    run_config = copy.deepcopy(dict(config))
    run_config["timeframe"] = timeframe
    run_config["data"] = {**run_config["data"],
                          "history_bars": int(math.ceil((now - DATA_START) / step)) + 12}
    out: dict[str, pd.DataFrame] = {}
    for symbol in symbols:
        frame = fetch_ohlcv_text(run_config, symbol, now=now)
        frame = frame[frame.index >= DATA_START] if not frame.empty else frame
        if not frame.empty:
            assert_before_vault(frame.index[-1] + step, what=f"{symbol} {timeframe} ham metin son bar kapanışı")
        out[symbol] = frame
    return out


def consistency(h1: pd.DataFrame, h4: pd.DataFrame, h4_text: pd.DataFrame | None = None) -> dict[str, Any]:
    """§6q > 3 + TADİLAT-3: 4H kapanışı = son 1H alt barının kapanışı (göreli ≤ 1e-9).

    Tolerans dışı bir bar, 1H kapanışı 4H kapanışının HAM METNİNDEKİ ondalık sayısına
    KESİLDİĞİNDE ona tam eşitse "kesinlik farkı"dır (`core/price_text.py`): %0.1 payına
    girmez, ayrı raporlanır. Ham metni olmayan ya da metni float değerle tutmayan bar
    sınıflandırılamaz ve UYUŞMAZLIK sayılır (muhafazakâr taraf).
    """
    if h1.empty or h4.empty:
        return {"compared": 0, "mismatch": 0, "missing_sub_bar": 0, "share": None, "ok": False}
    sub = h1["close"].reindex(h4.index + pd.Timedelta(hours=3))
    both = sub.notna().to_numpy()
    index = h4.index[both]
    c4 = h4["close"].to_numpy(dtype=float)[both]
    c1 = sub.to_numpy(dtype=float)[both]
    rel = np.abs(c4 - c1) / np.abs(c4)
    off = rel > CONSISTENCY_REL
    texts = h4_text["close"] if h4_text is not None and not h4_text.empty else pd.Series(dtype=object)
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


def coverage(frame: pd.DataFrame, step: pd.Timedelta) -> dict[str, Any]:
    if frame.empty:
        return {"bars": 0}
    full = pd.date_range(frame.index[0], frame.index[-1], freq=step, tz="UTC")
    return {"bars": int(len(frame)), "first_bar": str(frame.index[0]), "last_bar": str(frame.index[-1]),
            "missing_inside": int(len(full) - len(frame))}


def settings_from(config: Mapping[str, Any]) -> Settings:
    acceptance = get_setting(dict(config), "acceptance")
    return Settings(
        iterations=int(acceptance["bootstrap_samples"]),
        alpha=float(acceptance["edge_ci_alpha"]),
        seed=int(get_setting(dict(config), "random_seed")),
        cost_rate=float(get_setting(dict(config), "fee_rate")) + float(get_setting(dict(config), "slippage_base")),
    )


def prepare(config: Mapping[str, Any], symbols: Sequence[str], cache_dir: str) -> tuple[Market, dict[str, Any]]:
    h1 = fetch_all(config, symbols, timeframe="1H", cache_dir=cache_dir)
    h4 = fetch_all(config, symbols, timeframe="4H", cache_dir=cache_dir)
    h4_text = fetch_all_text(config, symbols, timeframe="4H")
    report: dict[str, Any] = {"symbols": {}}
    accepted = []
    for symbol in symbols:
        check = consistency(h1[symbol], h4[symbol], h4_text.get(symbol))
        report["symbols"][symbol] = {"1H": coverage(h1[symbol], HOUR), "4H": coverage(h4[symbol], FOUR),
                                     "consistency": check}
        if check["ok"]:
            accepted.append(symbol)
        else:
            report["symbols"][symbol]["excluded"] = "1H ↔ 4H tutarlılık kapısı (§6q > 3)"
    report["universe"] = accepted
    if not accepted:
        raise DataGateError("tutarlılık kapısından geçen sembol yok")
    market = build_market(h1, h4, accepted)
    first_eligible = {}
    for j, symbol in enumerate(accepted):
        hits = np.flatnonzero(market.eligible[:, j])
        first_eligible[symbol] = str(market.hours[hits[0]]) if len(hits) else None
    report["first_eligible_hour"] = first_eligible
    report["eligible_hours_missing_return"] = {s: int(market.missing[:, j].sum()) for j, s in enumerate(accepted)}
    return market, report


def run(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    layer = resolve_layer(config, "ema")
    symbols = list(layer.symbols or [])
    if not symbols:
        raise DataGateError("ema evreni boş")
    cache_dir = args.cache_dir or tempfile.mkdtemp(prefix="mmom-")
    market, data_report = prepare(layer.config, symbols, cache_dir)
    bounds = {k: [str(a), str(b)] for k, (a, b) in period_bounds().items()}
    settings = settings_from(layer.config)
    bases = zoo()

    if args.stage == "preflight":
        dedup, _ = build_zoo(market, bases, settings=settings, with_returns=False)
        tables = {h: period_table(market.hours, h) for h in HORIZONS}
        report = {
            "stage": "preflight", "kasa_start": str(KASA_START), "data_start": str(DATA_START),
            "periods": bounds, "bases": len(bases), "dedup": dedup, **data_report,
            "pairs": {h: {dn: len(t.pairs[dn]) for dn in ("A", "B")} for h, t in tables.items()},
            "eligible_by_month": eligibility_by_month(market),
        }
        print("=== PREFLIGHT BEGIN ===")
        print(json.dumps(clean(report), indent=2, ensure_ascii=False, default=_json, allow_nan=False))
        print("=== PREFLIGHT END ===")
        return 0

    dedup, sums = build_zoo(market, bases, settings=settings, with_returns=True)
    assert sums is not None
    empty = [f"{h}/{dn}" for h in HORIZONS for dn in ("A", "B") if not sums.tables[h].pairs[dn]]
    if empty:
        raise DataGateError(f"dönem çifti yok: {', '.join(empty)}")
    result = evaluate(sums, settings=settings)
    payload = {
        "preregistration": "docs/backtest.md > 6q (ec0d01a, TADİLAT-1 551fa67, TADİLAT-2 f56f269)",
        "parameters": {"data_start": str(DATA_START), "kasa_start": str(KASA_START),
                       "eligible_age_days": ELIGIBLE_AGE.days, "quantile": QUANTILE,
                       "horizons": list(HORIZONS), "blocks": {h: list(v) for h, v in BLOCKS.items()},
                       "bh_q": BH_Q, "min_clusters": MIN_CLUSTERS, "iterations": settings.iterations,
                       "alpha": settings.alpha, "seed": settings.seed, "cost_rate": settings.cost_rate},
        "periods": bounds,
        "zoo": {"bases": len(bases), "n": len(sums.ids), "dedup": dedup,
                "quintile": quintile_size(len(sums.ids))},
        "data": data_report,
        **result,
        "period_file": "model_momentum_periods.csv",
    }
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    text = json.dumps(clean(payload), indent=2, ensure_ascii=False, default=_json, allow_nan=False)
    (out / "model_momentum.json").write_text(text, encoding="utf-8")
    period_matrix(sums).to_csv(out / "model_momentum_periods.csv", index=False)
    print("=== MODEL_MOMENTUM.JSON BEGIN ===")
    print(text)
    print("=== MODEL_MOMENTUM.JSON END ===")
    return 0


def clean(value: Any) -> Any:
    """JSON'a güvenli: NaN/inf → null (`json.dumps` onları geçersiz `NaN` olarak yazardı)."""
    if isinstance(value, Mapping):
        return {str(k): clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [clean(v) for v in value]
    if isinstance(value, (np.floating, np.integer, np.bool_)):
        value = value.item()
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def _json(value: Any) -> Any:
    if isinstance(value, (np.floating, np.integer)):
        value = value.item()
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, (np.bool_,)):
        return bool(value)
    return value if isinstance(value, (int, float)) else str(value)


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Model momentumu ölçümü (docs/backtest.md > 6q).")
    parser.add_argument("--stage", choices=("preflight", "measure"), required=True,
                        help="preflight: kapsam + çift kuralı, getiri YOK (tekrarlanabilir); measure: TEK SEFER")
    parser.add_argument("--config", default=None)
    parser.add_argument("--cache-dir", default="", help="koşuya özel önbellek (boş = geçici dizin)")
    parser.add_argument("--out-dir", default="backtests/model_momentum")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    try:
        return run(args)
    except DataGateError as exc:
        logger.error("VERİ KAPISI: %s", exc)
        return 3


if __name__ == "__main__":
    sys.exit(main())

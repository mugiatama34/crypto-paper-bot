#!/usr/bin/env python3
"""Doku rejimi ölçümü (docs/backtest.md > 6r, TADİLAT-1). Ölçümün parçası DEĞİL.

Ön-kayıt (`456ce51`, TADİLAT-1 `79167b3`) bu betikten ÖNCE, hiçbir veri görülmeden commit edildi.
Betik o metni MEKANİK olarak uygular: günlük doku (14g Kaufman verimliliği medyanı × 7g getirilerin
kesitsel std'si, eşikler önceki 365 günün medyanı), §6q ailelerine önceden yazılmış aç/kapa eşlemesi,
üç kol (1D, 4H, 15m) kendi barlarında, aile eşit ağırlıklı sabit slotlu portföy. Ölçü
`Δ = N(eşlemeli) − N(her zaman açık)`; plasebo etiket serisinin DAİRESEL KAYDIRMALARIDIR (tüm
kaydırmalar, her iki yönde ≥ 30 gün). KAPI net maruziyeti hedge'lenmiş NET getiridedir; ham net
getiri aynı kurallarla BETİMSEL. %95 dilimi ∧ BH (q = 0.05, m = 3), A'da ölç B'de doğrula. Hiçbir
sayı burada SEÇİLMEZ.

**Model DEĞİL** (§6r > 1): hiçbir modele rejim kapısı eklenmez. Aile kuralları
`scripts/zoo_families.py`den (tek kopya), günlük kapanış `measure_regime.py::daily_closes`ten,
yüzdelik `backtest_dc.py::_percentiles`ten, dönem sınırları `measure_model_momentum.py::
period_bounds`tan (A `backtest_ema`den, B'nin sonu kasa), kesim `vault.py`den gelir.

**Salt okunur:** deftere, config'e ve `data/cache/`e yazmaz; KASAYA ait tek bar çekmez (§7.8);
§6q'nun strateji × dönem matrisini (`model_momentum_periods.csv`) OKUMAZ (§6r > 2).

İKİ AŞAMA: `preflight` HİÇBİR getiri, doku değeri, eşik, etiket ya da rejim payı üretmez — kapsam,
15m ↔ 4H tutarlılık kapısı, kol başına uygunluk ve etiketin TANIMLI olup olmayacağı (yalnızca
sayımlardan); tekrarlanabilir. `measure` tek seferliktir (§6r > 12).

Çıkış kodları: 0 = rapor yazıldı; 3 = veri kapısı (rapor YAZILMAZ); 2 = kullanım hatası.
Tetikleyicisi `.github/workflows/measure-texture-regime.yml`.
"""

from __future__ import annotations

import argparse
import copy
import json
import logging
import math
import random
import sys
import tempfile
import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from core.config import get_setting, load_config  # noqa: E402
from core.data import fetch_ohlcv  # noqa: E402
from core.layers import resolve_layer  # noqa: E402
from scripts import zoo_families as zf  # noqa: E402
from scripts.backtest_dc import _percentiles  # noqa: E402
from scripts.measure_model_momentum import (  # noqa: E402
    DataGateError, bh_accept, clean, coverage, fetch_all_text, hedge, period_bounds,
)
from scripts.measure_regime import daily_closes  # noqa: E402
from scripts.vault import KASA_START, assert_before_vault, vault_now  # noqa: E402

logger = logging.getLogger("measure_texture_regime")

# --- Ön-kayıtlı sayılar (§6r > 3, 4, 7, 8; TADİLAT-1). Hiçbiri CLI girdisi DEĞİLDİR. ---
DATA_START_4H = pd.Timestamp("2020-11-01T00:00:00Z")
DATA_START_15M = pd.Timestamp("2021-10-01T00:00:00Z")
DATA_END = KASA_START                  # ızgaranın sonu: kasa başlangıcı (§7.8)
ELIGIBLE_AGE = pd.Timedelta(days=60)
ER_DAYS = 14
DISPERSION_DAYS = 7
THRESHOLD_DAYS = 365
THRESHOLD_MIN_DEFINED = 300
MIN_SYMBOLS = 5
MIN_SHIFT_DAYS = 30
BLOCK_PERMUTATIONS = 1000
BH_Q = 0.05
GATE_ALPHA = 0.10            # `_percentiles(Δ*, 0.10)` üst ucu = %95 dilimi
DAY = pd.Timedelta(days=1)
WEEK = pd.Timedelta(days=7)

ARMS: dict[str, pd.Timedelta] = {"1D": DAY, "4H": pd.Timedelta(hours=4), "15m": pd.Timedelta(minutes=15)}
FAMILIES = ("F1", "F2", "F3", "F4", "F5", "F6", "F7")
TREND_FAMILIES = frozenset({"F1", "F2", "F3", "F5"})     # yüksek verimlilikte açık
RANGE_FAMILIES = frozenset({"F4", "F7"})                 # düşük verimlilikte açık
DISPERSION_FAMILIES = frozenset({"F6"})                  # yüksek dağılımda açık
# Rejim kodu r = 2·[yüksek V] + [yüksek D].
REGIMES = {0: "DV-DD", 1: "DV-YD", 2: "YV-DD", 3: "YV-YD"}
UNDEFINED = -1

VERIFIED = "DOĞRULANDI"
NOT_VERIFIED = "DOĞRULANMADI"
PASSED = "GEÇTİ"
FAILED = "GEÇMEDİ"
NOT_EVALUABLE = "DEĞERLENDİRİLEMEZ"


def is_open(family: str, regime: int) -> bool:
    """§6r > 5'in eşlemesi — kullanıcının hipotezi, veri görülmeden, DEĞİŞMEZ."""
    high_v, high_d = bool(regime & 2), bool(regime & 1)
    if family in TREND_FAMILIES:
        return high_v
    if family in RANGE_FAMILIES:
        return not high_v
    if family in DISPERSION_FAMILIES:
        return high_d
    raise ValueError(f"bilinmeyen aile: {family}")


OPEN = np.array([[is_open(f, r) for f in FAMILIES] for r in range(4)], dtype=float)


@dataclass(frozen=True)
class Settings:
    seed: int
    cost_rate: float


# --------------------------------------------------------------------------- #
# Dönemler — tam ISO haftaları (§6r > 9)
# --------------------------------------------------------------------------- #
def measurement_weeks() -> dict[str, tuple[pd.Timestamp, pd.Timestamp]]:
    """Dönem sınırlarının İÇİNDE kalan tam haftalar: [ilk Pazartesi, son tam haftanın bitişi)."""
    out = {}
    for name, (lo, hi) in period_bounds().items():
        start = lo.normalize()
        start = start + pd.Timedelta(days=(7 - start.dayofweek) % 7)
        if start < lo:
            start = start + WEEK
        weeks = int((hi - start) // WEEK)
        out[name] = (start, start + weeks * WEEK)
    return out


def period_days(period: str) -> pd.DatetimeIndex:
    lo, hi = measurement_weeks()[period]
    return pd.date_range(lo, hi - DAY, freq="D", tz="UTC")


def admissible_shifts(days: int) -> list[int]:
    """TADİLAT-1 > 2: `k ∈ {30, …, D − 30}` — her iki yönde 30 günden kısa kaydırmalar hariç."""
    return list(range(MIN_SHIFT_DAYS, days - MIN_SHIFT_DAYS + 1))


def circular_shift(labels: np.ndarray, k: int) -> np.ndarray:
    """`L^k_d = L_{(d − k) mod D}`."""
    return np.roll(labels, k)


def block_permutation(labels: np.ndarray, rng: random.Random) -> np.ndarray:
    """Betimsel plasebo: haftaları yerine koymadan karıştır, her hafta kendi 7 gününü taşır."""
    weeks = labels.reshape(-1, 7)
    order = list(range(len(weeks)))
    rng.shuffle(order)
    return weeks[order].reshape(-1)


def transitions(labels: np.ndarray) -> int:
    return int(np.count_nonzero(labels[1:] != labels[:-1]))


# --------------------------------------------------------------------------- #
# DOKU (§6r > 4)
# --------------------------------------------------------------------------- #
def daily_table(h4: Mapping[str, pd.DataFrame], symbols: Sequence[str]) -> pd.DataFrame:
    """Gün × sembol `C_d` (tam takvim; 20:00 barı olmayan gün NaN)."""
    frame = pd.DataFrame({s: daily_closes(h4[s]) for s in symbols if not h4[s].empty}).reindex(columns=list(symbols))
    if frame.empty:
        return frame
    return frame.reindex(pd.date_range(frame.index.min(), frame.index.max(), freq="D", tz="UTC"))


def texture_eligibility(daily: pd.DataFrame, first_4h: Mapping[str, pd.Timestamp]) -> pd.DataFrame:
    """Gün `d` (00:00) için uygunluk: sembolün ilk 4H barı `d − 60 gün`den önce (§6r > 3)."""
    days = daily.index + DAY
    out = {}
    for s in daily.columns:
        first = first_4h.get(s)
        out[s] = (days >= first + ELIGIBLE_AGE) if first is not None else np.zeros(len(days), dtype=bool)
    return pd.DataFrame(out, index=days)


def texture_counts(daily: pd.DataFrame, first_4h: Mapping[str, pd.Timestamp]) -> pd.DataFrame:
    """PREFLIGHT için: yalnızca SAYIMLAR (hiçbir fiyat oranı hesaplanmaz).

    Gün `d` başına V ve D'ye girebilecek sembol sayısı (pencerenin kapanışları tanımlı ve uygun)
    ve eşiğin tanımlı olacağı. Gerçek V'de yol uzunluğu 0 olan sembol ayrıca düşer; bu yaklaşıklık
    yalnızca preflight'ın tahminidir, `measure` kendi sayımını yeniden yapar.
    """
    elig = texture_eligibility(daily, first_4h).to_numpy()
    finite = daily.notna()
    window_v = finite.rolling(ER_DAYS + 1, min_periods=ER_DAYS + 1).sum().eq(ER_DAYS + 1).to_numpy()
    window_d = (finite & finite.shift(DISPERSION_DAYS, fill_value=False)).to_numpy()
    n_v = (window_v & elig).sum(axis=1)
    n_d = (window_d & elig).sum(axis=1)
    days = daily.index + DAY
    defined_v = pd.Series(n_v >= MIN_SYMBOLS, index=days)
    defined_d = pd.Series(n_d >= MIN_SYMBOLS, index=days)
    thr_v = defined_v.astype(float).shift(1).rolling(THRESHOLD_DAYS, min_periods=1).sum()
    thr_d = defined_d.astype(float).shift(1).rolling(THRESHOLD_DAYS, min_periods=1).sum()
    return pd.DataFrame({"n_v": n_v, "n_d": n_d, "defined_v": defined_v, "defined_d": defined_d,
                         "thr_v_defined": thr_v >= THRESHOLD_MIN_DEFINED,
                         "thr_d_defined": thr_d >= THRESHOLD_MIN_DEFINED}, index=days)


def texture(daily: pd.DataFrame, first_4h: Mapping[str, pd.Timestamp]) -> pd.DataFrame:
    """Gün `d` (00:00 UTC) → V, D, eşikler, etiket. Yalnızca `C_{d−1}` ve öncesi (§6r > 4).

    `daily` gün `x` satırında `C_x`'i taşır; gün `d`'nin girdisi `x = d − 1` satırıdır. Eşik gün
    `d`'yi DIŞLAR: `V_{d−365} … V_{d−1}`'in medyanı, tanımlı değer < 300 ise tanımsız.
    """
    closes = daily.astype(float)
    elig = texture_eligibility(daily, first_4h).to_numpy()
    path = (closes - closes.shift(1)).abs().rolling(ER_DAYS, min_periods=ER_DAYS).sum()
    net = (closes - closes.shift(ER_DAYS)).abs()
    er = (net / path.where(path > 0)).to_numpy()
    er = np.where(elig & np.isfinite(er), er, np.nan)
    lr = np.log(closes / closes.shift(DISPERSION_DAYS)).to_numpy()
    lr = np.where(elig & np.isfinite(lr), lr, np.nan)
    n_v = np.isfinite(er).sum(axis=1)
    n_d = np.isfinite(lr).sum(axis=1)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)      # tamamen NaN satır: medyan tanımsız
        v = np.where(n_v >= MIN_SYMBOLS, np.nanmedian(er, axis=1), np.nan)
    d = np.where(n_d >= MIN_SYMBOLS, _row_std(lr), np.nan)
    days = daily.index + DAY
    out = pd.DataFrame({"V": v, "D": d, "n_v": n_v, "n_d": n_d}, index=days)
    out["thr_V"] = out["V"].shift(1).rolling(THRESHOLD_DAYS, min_periods=THRESHOLD_MIN_DEFINED).median()
    out["thr_D"] = out["D"].shift(1).rolling(THRESHOLD_DAYS, min_periods=THRESHOLD_MIN_DEFINED).median()
    defined = out[["V", "D", "thr_V", "thr_D"]].notna().all(axis=1)
    high_v = (out["V"] > out["thr_V"]).astype(int)
    high_d = (out["D"] > out["thr_D"]).astype(int)
    out["label"] = np.where(defined, 2 * high_v + high_d, UNDEFINED).astype(int)
    return out


def _row_std(values: np.ndarray) -> np.ndarray:
    """Satır başına kesitsel std (ddof = 1), NaN'lar dışarıda."""
    out = np.full(values.shape[0], np.nan)
    for i, row in enumerate(values):
        finite = row[np.isfinite(row)]
        if len(finite) > 1:
            out[i] = float(np.std(finite, ddof=1))
    return out


# --------------------------------------------------------------------------- #
# Kollar (§6r > 5, 6)
# --------------------------------------------------------------------------- #
@dataclass
class Arm:
    name: str
    step: pd.Timedelta
    grid: pd.DatetimeIndex             # bar açılışları (yürütme ızgarası)
    symbols: list[str]
    frames: dict[str, pd.DataFrame]    # kolun KENDİ barları
    eligible: np.ndarray               # bar × sembol
    returns: np.ndarray                # açılıştan açılışa; eksik → 0
    missing: np.ndarray

    @property
    def count(self) -> np.ndarray:
        return self.eligible.sum(axis=1)


def daily_ohlc(h4: pd.DataFrame) -> pd.DataFrame:
    """1D barı 4H'den (O7, 00:00 UTC hizası): açılış 00:00 barının açılışı, H/L günün 4H barlarının
    maks/min'i, kapanış `C_d` (`daily_closes`). Kapanışı olmayan gün bar DEĞİLDİR."""
    if h4.empty:
        return pd.DataFrame(columns=["open", "high", "low", "close"])
    index = pd.DatetimeIndex(h4.index)
    day = index.floor("D")
    grouped = h4.groupby(day)
    frame = pd.DataFrame({"high": grouped["high"].max(), "low": grouped["low"].min()})
    opens = h4["open"][index.hour == 0]
    frame["open"] = pd.Series(opens.to_numpy(dtype=float), index=opens.index.floor("D")).reindex(frame.index)
    frame["close"] = daily_closes(h4).reindex(frame.index)
    frame = frame[frame["close"].notna()]
    frame.index = pd.DatetimeIndex(frame.index).tz_convert("UTC") if frame.index.tz else frame.index.tz_localize("UTC")
    return frame[["open", "high", "low", "close"]]


def build_arm(name: str, frames: Mapping[str, pd.DataFrame], first_4h: Mapping[str, pd.Timestamp],
              symbols: Sequence[str], *, start: pd.Timestamp, end: pd.Timestamp | None = None) -> Arm:
    step = ARMS[name]
    end = DATA_END if end is None else end
    assert_before_vault(end, what=f"{name} ızgara sonu")
    grid = pd.date_range(start, end - step, freq=step, tz="UTC")
    width = len(symbols)
    eligible = np.zeros((len(grid), width), dtype=bool)
    returns = np.zeros((len(grid), width))
    missing = np.zeros((len(grid), width), dtype=bool)
    for j, s in enumerate(symbols):
        frame = frames[s]
        if frame.empty or s not in first_4h:
            continue
        opens = frame["open"].reindex(grid).to_numpy(dtype=float)
        r = np.append(opens[1:], np.nan) / opens - 1.0
        has_closed = pd.Series(True, index=frame.index).reindex(grid - step).fillna(False).to_numpy(dtype=bool)
        aged = grid >= first_4h[s] + ELIGIBLE_AGE
        elig = aged & has_closed
        undefined = ~np.isfinite(r)
        eligible[:, j] = elig
        missing[:, j] = elig & undefined
        returns[:, j] = np.where(undefined, 0.0, r)
    return Arm(name=name, step=step, grid=grid, symbols=list(symbols), frames={s: frames[s] for s in symbols},
               eligible=eligible, returns=returns, missing=missing)


@dataclass(frozen=True)
class Base:
    id: str
    family: str
    build: Callable[[Arm], np.ndarray]     # bar × sembol ağırlık matrisi w


def _ts(arm: Arm, rule: Callable[[pd.DataFrame], pd.Series]) -> np.ndarray:
    states = [zf.to_grid(rule(arm.frames[s]), effective_lag=arm.step, grid=arm.grid) if not arm.frames[s].empty
              else np.zeros(len(arm.grid)) for s in arm.symbols]
    matrix = np.column_stack(states) if states else np.zeros((len(arm.grid), 0))
    return zf.ts_weights(matrix, arm.eligible)


def _closes(arm: Arm, frame: pd.DataFrame) -> pd.Series:
    return zf.calendar(frame[["close"]], arm.step)["close"]


def _xsec(arm: Arm, *, bars: int, k: int, long_only: bool) -> np.ndarray:
    table = pd.DataFrame({s: _closes(arm, arm.frames[s]) for s in arm.symbols if not arm.frames[s].empty})
    table = table.reindex(columns=arm.symbols)
    if not table.empty:
        table = table.reindex(pd.date_range(table.index.min(), table.index.max(), freq=arm.step, tz="UTC"))
    return zf.xsec_weights(table, bars=bars, k=k, long_only=long_only, effective_lag=arm.step, grid=arm.grid,
                           eligible=arm.eligible, symbols=arm.symbols)


def bases() -> list[Base]:
    """59 taban / kol (§6r > 5, O1): parametreler kolun BARI cinsinden, üç kolda aynı; ters YOK."""
    out: list[Base] = []

    def ts(bid: str, family: str, rule: Callable[[Arm, pd.DataFrame], pd.Series]) -> None:
        out.append(Base(bid, family, lambda arm, rule=rule: _ts(arm, lambda f: rule(arm, f))))

    def tag(lo: bool) -> str:
        return "lo" if lo else "ls"

    for bars in (6, 18, 42, 84, 180):
        for lo in (False, True):
            ts(f"tsmom_{bars}_{tag(lo)}", "F1",
               lambda arm, f, b=bars, lo=lo: zf.tsmom_rule(_closes(arm, f), b, long_only=lo))
    for periods in ((5, 21, 50), (8, 21, 55), (10, 30, 100)):
        ts(f"ema_stack_{'-'.join(map(str, periods))}", "F2",
           lambda arm, f, p=periods: zf.ema_stack_rule(f["close"], p))
    for fast, slow in ((5, 20), (10, 30), (20, 50), (21, 55), (50, 200)):
        for lo in (False, True):
            ts(f"ma_cross_{fast}-{slow}_{tag(lo)}", "F3",
               lambda arm, f, a=fast, b=slow, lo=lo: zf.ma_cross_rule(f["close"], a, b, long_only=lo))
    for bars in (1, 4):
        for lo in (False, True):
            ts(f"st_rev_{bars}_{tag(lo)}", "F4",
               lambda arm, f, b=bars, lo=lo: zf.st_rev_rule(_closes(arm, f), b, long_only=lo))
    for period in (20, 55, 120):
        for lo in (False, True):
            ts(f"donchian_{period}_{tag(lo)}", "F5",
               lambda arm, f, n=period, lo=lo: zf.donchian_rule(f, n, long_only=lo))
    for bars in (1, 3, 7, 14, 30):
        for k in (2, 4):
            for lo in (False, True):
                out.append(Base(f"xsec_{bars}_k{k}_{tag(lo)}", "F6",
                                lambda arm, b=bars, k=k, lo=lo: _xsec(arm, bars=b, k=k, long_only=lo)))
    for period, low, high in ((2, 10.0, 90.0), (2, 25.0, 75.0), (14, 30.0, 70.0), (14, 20.0, 80.0)):
        ts(f"rsi_{period}_{int(low)}-{int(high)}", "F7",
           lambda arm, f, p=period, a=low, b=high: zf.rsi_extreme_rule(f["close"], p, a, b))
    for k in (2.0, 2.5):
        ts(f"bollinger_20_{k}", "F7", lambda arm, f, k=k: zf.bollinger_extreme_rule(f["close"], k))
    return out


def family_weights(arm: Arm, base_list: Sequence[Base], rows: slice | None = None) -> dict[str, np.ndarray]:
    """Aile başına EŞİT AĞIRLIKLI taban ortalaması `W_F` (1/n_F); portföy `(1/7) Σ a_F W_F` (O2)."""
    sums: dict[str, np.ndarray] = {}
    counts: dict[str, int] = {}
    for base in base_list:
        w = base.build(arm)
        w = w[rows] if rows is not None else w
        sums[base.family] = sums.get(base.family, 0.0) + w
        counts[base.family] = counts.get(base.family, 0) + 1
    return {f: sums[f] / counts[f] for f in FAMILIES}


# --------------------------------------------------------------------------- #
# Portföy muhasebesi — rejim başına ön hesap, etiket serisi başına O(gün) (§6r > 6, TADİLAT-1 > 2, 3)
# --------------------------------------------------------------------------- #
def turnover_between(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    return np.abs(a - b).sum(axis=1)


@dataclass
class Ledger:
    """Bir kolun bir dönemi için, bir ağırlık türünde (ham ya da hedge'li) gün × rejim tabloları.

    `gross[d, r]`: gün d'nin barlarında rejim r'nin portföyünün brüt getirisi. `inner[d, r]`: gün
    d'nin İLK barı dışındaki barların devri (rejim sabit). `first[d, r1, r2]`: gün d'nin ilk barında
    önceki bar r1, bu bar r2 iken devir. `always_*`: her zaman açık portföy `P⁰`.
    """
    gross: np.ndarray
    inner: np.ndarray
    first: np.ndarray
    always_gross: float
    always_turnover: float
    days: int

    def evaluate(self, labels: np.ndarray, cost_rate: float) -> dict[str, float]:
        d = np.arange(self.days)
        prev = np.r_[labels[0], labels[:-1]]          # dönem girişi: öncesi = ilk günün etiketi
        gross = float(self.gross[d, labels].sum())
        turn = float(self.inner[d, labels].sum() + self.first[d, prev, labels].sum())
        switch = float(self.first[d, prev, labels][prev != labels].sum())
        return {"gross": gross, "turnover": turn, "net": gross - cost_rate * turn, "switch_turnover": switch}


def build_ledger(family_w: Mapping[str, np.ndarray], returns: np.ndarray, eligible: np.ndarray,
                 day_of_bar: np.ndarray, days: int, *, hedged: bool) -> Ledger:
    """`family_w` dönemin barları + BİR önceki bar (satır 0) üzerindedir; `day_of_bar` satır 1'den itibaren."""
    stacked = np.stack([family_w[f] for f in FAMILIES])          # F × (1+n) × S
    regime_w = np.einsum("rf,fbs->rbs", OPEN, stacked) / len(FAMILIES)
    always = stacked.sum(axis=0) / len(FAMILIES)
    if hedged:
        regime_w = np.stack([hedge(w, eligible) for w in regime_w])
        always = hedge(always, eligible)
    ret = returns[1:]
    gross_bar = np.einsum("rbs,bs->rb", regime_w[:, 1:], ret)    # 4 × n
    turn_bar = np.stack([turnover_between(w[1:], w[:-1]) for w in regime_w])   # 4 × n
    first_pos = np.r_[0, np.flatnonzero(np.diff(day_of_bar)) + 1]
    first_mask = np.zeros(len(day_of_bar), dtype=bool)
    first_mask[first_pos] = True
    gross = np.zeros((days, 4))
    inner = np.zeros((days, 4))
    for r in range(4):
        gross[:, r] = np.bincount(day_of_bar, weights=gross_bar[r], minlength=days)
        inner[:, r] = np.bincount(day_of_bar[~first_mask], weights=turn_bar[r][~first_mask], minlength=days)
    first = np.zeros((days, 4, 4))
    for r1 in range(4):
        for r2 in range(4):
            cur = regime_w[r2][first_pos + 1]
            before = regime_w[r1][first_pos]
            first[day_of_bar[first_pos], r1, r2] = np.abs(cur - before).sum(axis=1)
    always_gross = float((always[1:] * ret).sum())
    always_turn = float(turnover_between(always[1:], always[:-1]).sum())
    return Ledger(gross=gross, inner=inner, first=first, always_gross=always_gross, always_turnover=always_turn,
                  days=days)


def span_rows(arm: Arm) -> slice:
    """A'nın ilk barından bir önceki bar … B'nin son barı: ağırlıklar bir kez, bu aralıkta kurulur."""
    weeks = measurement_weeks()
    start = int(arm.grid.searchsorted(weeks["A"][0]))
    stop = int(arm.grid.searchsorted(weeks["B"][1]))
    if start == 0:
        raise DataGateError(f"{arm.name}: dönem A'dan önce bar yok")
    return slice(start - 1, stop)


def period_rows(arm: Arm, period: str) -> tuple[slice, np.ndarray, int]:
    """Dönemin barları + bir önceki bar; bar → gün indeksi."""
    lo, hi = measurement_weeks()[period]
    start = int(arm.grid.searchsorted(lo))
    stop = int(arm.grid.searchsorted(hi))
    if start == 0 or arm.grid[start] != lo:
        raise DataGateError(f"{arm.name}: {period} dönem başı ızgarada yok")
    day_of_bar = ((arm.grid[start:stop] - lo) // DAY).to_numpy().astype(int)
    return slice(start - 1, stop), day_of_bar, int((hi - lo) // DAY)


# --------------------------------------------------------------------------- #
# Plasebo testi ve karar (§6r > 8; TADİLAT-1 > 2, 3)
# --------------------------------------------------------------------------- #
def p_upper(real: float, placebo: Sequence[float]) -> float:
    return (1 + sum(1 for x in placebo if x >= real)) / (len(placebo) + 1)


def p_lower(real: float, placebo: Sequence[float]) -> float:
    return (1 + sum(1 for x in placebo if x <= real)) / (len(placebo) + 1)


def placebo_test(ledger: Ledger, labels: np.ndarray, cost_rate: float, *, seed: str) -> dict[str, Any]:
    """Gerçek etiket, TÜM uygun dairesel kaydırmalar (bağlayıcı) ve blok permütasyonu (betimsel)."""
    always_net = ledger.always_gross - cost_rate * ledger.always_turnover

    def delta(lab: np.ndarray) -> dict[str, float]:
        v = ledger.evaluate(lab, cost_rate)
        return {**v, "delta": v["net"] - always_net, "delta_gross": v["gross"] - ledger.always_gross,
                "transitions": transitions(lab)}

    real = delta(labels)
    shifts = admissible_shifts(len(labels))
    shifted = [{"k": k, **delta(circular_shift(labels, k))} for k in shifts]
    rng = random.Random(seed)
    blocks = [delta(block_permutation(labels, rng)) for _ in range(BLOCK_PERMUTATIONS)]

    def summary(rows: Sequence[Mapping[str, float]]) -> dict[str, Any]:
        d = [r["delta"] for r in rows]
        g = [r["delta_gross"] for r in rows]
        _, q95 = _percentiles(d, GATE_ALPHA)
        q05, _ = _percentiles(d, GATE_ALPHA)
        return {"n": len(rows), "p": p_upper(real["delta"], d), "p_lower": p_lower(real["delta"], d),
                "q95": q95, "q05": q05, "mean": float(np.mean(d)), "std": float(np.std(d, ddof=1)),
                "above_q95": bool(real["delta"] > q95),
                "gross_p": p_upper(real["delta_gross"], g), "gross_q95": _percentiles(g, GATE_ALPHA)[1],
                "transitions_mean": float(np.mean([r["transitions"] for r in rows])),
                "turnover_mean": float(np.mean([r["turnover"] for r in rows])),
                "switch_turnover_mean": float(np.mean([r["switch_turnover"] for r in rows]))}

    return {"real": real, "always_open": {"gross": ledger.always_gross, "turnover": ledger.always_turnover,
                                          "net": always_net},
            "circular": summary(shifted), "block": summary(blocks),
            "draws": {"circular": shifted, "block": blocks}}


def decide(tests: Mapping[str, Mapping[str, Mapping[str, Any]]], evaluable: Mapping[str, Mapping[str, bool]]
           ) -> dict[str, Any]:
    """`tests[kol][dönem]` = `placebo_test`; dairesel plasebo bağlayıcı. A: %95 ∧ BH m = 3; B: A'da geçenler."""
    arms = list(ARMS)

    def ok(arm: str, period: str) -> bool:
        return evaluable[arm][period] and tests[arm][period]["circular"]["above_q95"]

    def p(arm: str, period: str) -> float:
        return tests[arm][period]["circular"]["p"] if evaluable[arm][period] else 1.0

    passed_a = sorted({a for a in arms if ok(a, "A")} & bh_accept({a: p(a, "A") for a in arms}, BH_Q))
    passed_b = sorted({a for a in passed_a if ok(a, "B")} & bh_accept({a: p(a, "B") for a in passed_a}, BH_Q))
    out: dict[str, Any] = {}
    for a in arms:
        out[a] = {
            "A": (PASSED if a in passed_a else FAILED) if evaluable[a]["A"] else NOT_EVALUABLE,
            "B": ((PASSED if a in passed_b else FAILED) if a in passed_a else "bilgi — doğrulama değil"),
            "verdict": VERIFIED if a in passed_b else NOT_VERIFIED,
        }
    out["m_A"] = len(arms)
    out["m_B"] = len(passed_a)
    return out


def reading(hedged: str, raw: str, beats_always_open: bool) -> str:
    """TADİLAT-1 > 3'ün okuma tablosu (MEKANİK; kapı değil)."""
    if hedged == VERIFIED:
        tail = "eşleme her zaman açıktan iyi" if beats_always_open else "ama eşleme yine de kaybettiriyor"
        return f"doku stratejilerin performansını yönden bağımsız öngörüyor — {tail}"
    if raw == VERIFIED:
        return "doku yönü öngörüyor — dokuz önceki ölçüme ters düşer; şüpheyle okunur, tez açmaz"
    if beats_always_open:
        return "eşleme mekanik olarak iyi, rejim bir şey bilmiyor"
    return "ayırt edilemedi"


# --------------------------------------------------------------------------- #
# Betimsel (§6r > 10)
# --------------------------------------------------------------------------- #
def regime_stats(labels: np.ndarray) -> dict[str, Any]:
    runs: list[int] = []
    length = 1
    for prev, cur in zip(labels[:-1], labels[1:]):
        if cur == prev:
            length += 1
        else:
            runs.append(length)
            length = 1
    runs.append(length)
    counts = np.bincount(labels, minlength=4)
    return {"days": int(len(labels)),
            "share": {REGIMES[r]: float(counts[r] / len(labels)) for r in range(4)},
            "high_v_share": float(np.mean(labels >= 2)), "high_d_share": float(np.mean(labels % 2 == 1)),
            "episodes": len(runs), "transitions": transitions(labels),
            "transitions_per_day": transitions(labels) / max(1, len(labels) - 1),
            "duration_days": {"median": float(np.median(runs)), "p90": float(np.percentile(runs, 90)),
                              "max": int(max(runs))}}


def threshold_margins(tex: pd.DataFrame, days: pd.DatetimeIndex) -> dict[str, Any]:
    sub = tex.loc[days]
    qs = (5, 25, 50, 75, 95)
    v = (sub["V"] - sub["thr_V"]).to_numpy(dtype=float)
    d = np.log(sub["D"] / sub["thr_D"]).to_numpy(dtype=float)
    return {"V_minus_threshold": {f"p{q}": float(np.percentile(v, q)) for q in qs},
            "log_D_over_threshold": {f"p{q}": float(np.percentile(d, q)) for q in qs}}


def family_by_regime(family_w: Mapping[str, np.ndarray], arm: Arm, rows: slice, day_of_bar: np.ndarray,
                     labels: np.ndarray, cost_rate: float) -> dict[str, Any]:
    """Aile başına, her zaman açık aile portföyünün rejim içindeki brüt/hedge'li/net getirisi."""
    ret = arm.returns[rows][1:]
    elig = arm.eligible[rows]
    regime_of_bar = labels[day_of_bar]
    bars_per_day = int(DAY / arm.step)
    out: dict[str, Any] = {}
    for f in FAMILIES:
        w = family_w[f]
        v = hedge(w, elig)
        g = (w[1:] * ret).sum(axis=1)
        h = (v[1:] * ret).sum(axis=1)
        net = g - cost_rate * turnover_between(w[1:], w[:-1])
        net_h = h - cost_rate * turnover_between(v[1:], v[:-1])
        per: dict[str, Any] = {}
        for r in range(4):
            m = regime_of_bar == r
            n = int(m.sum())
            per[REGIMES[r]] = {"bars": n, "open": bool(OPEN[r, FAMILIES.index(f)]),
                               "gross": float(g[m].sum()), "hedged": float(h[m].sum()),
                               "net": float(net[m].sum()), "net_hedged": float(net_h[m].sum()),
                               "net_per_day": float(net[m].mean() * bars_per_day) if n else None,
                               "net_hedged_per_day": float(net_h[m].mean() * bars_per_day) if n else None}
        out[f] = per
    return out


# --------------------------------------------------------------------------- #
# Veri
# --------------------------------------------------------------------------- #
def fetch_frames(config: Mapping[str, Any], symbols: Sequence[str], *, timeframe: str, start: pd.Timestamp,
                 cache_dir: str) -> dict[str, pd.DataFrame]:
    """Kasa başlangıcında KAPANMIŞ barlar; kasaya ait tek bar çekilmez (§7.8)."""
    now = vault_now()
    step = ARMS["15m"] if timeframe == "15m" else ARMS["4H"]
    run_config = copy.deepcopy(dict(config))
    run_config["timeframe"] = timeframe
    run_config["data"] = {**run_config["data"], "history_bars": int(math.ceil((now - start) / step)) + 12,
                          "cache_dir": cache_dir}
    out: dict[str, pd.DataFrame] = {}
    for symbol in symbols:
        frame = fetch_ohlcv(run_config, symbol, now=now)
        frame = frame[frame.index >= start] if not frame.empty else frame
        if not frame.empty:
            assert_before_vault(frame.index[-1] + step, what=f"{symbol} {timeframe} son bar kapanışı")
        out[symbol] = frame
    return out


@dataclass
class Data:
    symbols: list[str]
    h4: dict[str, pd.DataFrame]
    m15: dict[str, pd.DataFrame]
    first_4h: dict[str, pd.Timestamp]
    report: dict[str, Any]


def prepare(config: Mapping[str, Any], symbols: Sequence[str], cache_dir: str) -> Data:
    h4 = fetch_frames(config, symbols, timeframe="4H", start=DATA_START_4H, cache_dir=cache_dir)
    m15 = fetch_frames(config, symbols, timeframe="15m", start=DATA_START_15M, cache_dir=cache_dir)
    h4_text = fetch_all_text(config, symbols, timeframe="4H")
    report: dict[str, Any] = {"symbols": {}}
    accepted = []
    for s in symbols:
        check = zf.consistency(m15[s], h4[s], h4_text.get(s), last_sub_offset=pd.Timedelta(hours=3, minutes=45))
        report["symbols"][s] = {"4H": coverage(h4[s], ARMS["4H"]), "15m": coverage(m15[s], ARMS["15m"]),
                                "consistency": check}
        if check["ok"]:
            accepted.append(s)
        else:
            report["symbols"][s]["excluded"] = "15m ↔ 4H tutarlılık kapısı (§6r > 3)"
    report["universe"] = accepted
    if not accepted:
        raise DataGateError("tutarlılık kapısından geçen sembol yok")
    first = {s: pd.Timestamp(h4[s].index[0]) for s in accepted if not h4[s].empty}
    return Data(symbols=accepted, h4={s: h4[s] for s in accepted}, m15={s: m15[s] for s in accepted},
                first_4h=first, report=report)


def arm_frames(data: Data, name: str) -> tuple[dict[str, pd.DataFrame], pd.Timestamp]:
    """Kolun kendi barları ve ızgara başı (§6r > 3, 5)."""
    if name == "15m":
        return data.m15, DATA_START_15M
    if name == "4H":
        return data.h4, DATA_START_4H
    return {s: daily_ohlc(f) for s, f in data.h4.items()}, DATA_START_4H


def arm_evaluable(arm: Arm) -> dict[str, Any]:
    """Kol, dönemin ilk barında ≥ 5 uygun sembol taşımıyorsa o dönemde değerlendirilemez (§6r > 3)."""
    out: dict[str, Any] = {}
    for period, (lo, _) in measurement_weeks().items():
        pos = int(arm.grid.searchsorted(lo))
        n = int(arm.count[pos]) if pos < len(arm.grid) and arm.grid[pos] == lo else 0
        out[period] = {"eligible_at_start": n, "evaluable": n >= MIN_SYMBOLS}
    first = np.flatnonzero(arm.eligible.any(axis=0))
    out["first_eligible_bar"] = {s: (str(arm.grid[np.flatnonzero(arm.eligible[:, j])[0]])
                                     if arm.eligible[:, j].any() else None)
                                 for j, s in enumerate(arm.symbols)}
    out["symbols_ever_eligible"] = int(len(first))
    return out


# --------------------------------------------------------------------------- #
# Aşamalar
# --------------------------------------------------------------------------- #
def settings_from(config: Mapping[str, Any]) -> Settings:
    return Settings(seed=int(get_setting(dict(config), "random_seed")),
                    cost_rate=float(get_setting(dict(config), "fee_rate"))
                    + float(get_setting(dict(config), "slippage_base")))


def preflight(data: Data) -> dict[str, Any]:
    """Getiri, doku değeri, eşik, etiket ya da rejim payı ÜRETMEZ (test)."""
    daily = daily_table(data.h4, data.symbols)
    counts = texture_counts(daily, data.first_4h)
    labels_ok: dict[str, Any] = {}
    for period in ("A", "B"):
        days = period_days(period)
        sub = counts.reindex(days)
        undefined = ~(sub["defined_v"].fillna(False) & sub["defined_d"].fillna(False)
                      & sub["thr_v_defined"].fillna(False) & sub["thr_d_defined"].fillna(False))
        labels_ok[period] = {"days": int(len(days)), "undefined_days_estimate": int(undefined.sum()),
                             "min_symbols_V": int(sub["n_v"].min()) if sub["n_v"].notna().any() else 0,
                             "min_symbols_D": int(sub["n_d"].min()) if sub["n_d"].notna().any() else 0}
    arms = {}
    for name in ARMS:
        frames, start = arm_frames(data, name)
        arm = build_arm(name, frames, data.first_4h, data.symbols, start=start)
        arms[name] = {"bars": int(len(arm.grid)), **arm_evaluable(arm)}
    weeks = measurement_weeks()
    return {"stage": "preflight", "kasa_start": str(KASA_START),
            "weeks": {p: {"start": str(lo), "end": str(hi), "weeks": int((hi - lo) // WEEK),
                          "days": int((hi - lo) // DAY), "shifts": len(admissible_shifts(int((hi - lo) // DAY)))}
                      for p, (lo, hi) in weeks.items()},
            "texture_definedness": labels_ok, "arms": arms, "bases_per_arm": len(bases()), **data.report}


def measure(data: Data, settings: Settings) -> tuple[dict[str, Any], pd.DataFrame, pd.DataFrame]:
    daily = daily_table(data.h4, data.symbols)
    tex = texture(daily, data.first_4h)
    labels: dict[str, np.ndarray] = {}
    for period in ("A", "B"):
        days = period_days(period)
        lab = tex["label"].reindex(days).fillna(UNDEFINED).to_numpy(dtype=int)
        if (lab == UNDEFINED).any():
            raise DataGateError(f"dönem {period}: {int((lab == UNDEFINED).sum())} günün doku etiketi tanımsız (§6r > 4)")
        labels[period] = lab
    base_list = bases()
    tests: dict[str, dict[str, Any]] = {"hedged": {}, "raw": {}}
    evaluable: dict[str, dict[str, bool]] = {}
    arms_out: dict[str, Any] = {}
    placebo_rows = []
    for name in ARMS:
        frames, start = arm_frames(data, name)
        arm = build_arm(name, frames, data.first_4h, data.symbols, start=start)
        ev = arm_evaluable(arm)
        evaluable[name] = {p: ev[p]["evaluable"] for p in ("A", "B")}
        per_period: dict[str, Any] = {}
        for measure_name in ("hedged", "raw"):
            tests[measure_name][name] = {}
        span = span_rows(arm)
        fw_span = family_weights(arm, base_list, span)
        for period in ("A", "B"):
            rows, day_of_bar, n_days = period_rows(arm, period)
            local = slice(rows.start - span.start, rows.stop - span.start)
            fw = {f: w[local] for f, w in fw_span.items()}
            elig = arm.eligible[rows]
            ret = arm.returns[rows]
            entry: dict[str, Any] = {}
            for measure_name, hedged in (("hedged", True), ("raw", False)):
                ledger = build_ledger(fw, ret, elig, day_of_bar, n_days, hedged=hedged)
                seed = f"{settings.seed}:doku:{period}:blok"
                test = placebo_test(ledger, labels[period], settings.cost_rate, seed=seed)
                for kind in ("circular", "block"):
                    for i, row in enumerate(test["draws"][kind]):
                        placebo_rows.append({"arm": name, "period": period, "measure": measure_name,
                                             "placebo": kind, "k": row.get("k", i), "delta": row["delta"],
                                             "delta_gross": row["delta_gross"], "transitions": row["transitions"],
                                             "turnover": row["turnover"]})
                test.pop("draws")
                tests[measure_name][name][period] = test
                entry[measure_name] = test
            bars_per_day = int(DAY / arm.step)
            n_bars = len(day_of_bar)
            entry["turnover_per_day"] = {
                m: {"mapped": entry[m]["real"]["turnover"] / n_days, "always_open": entry[m]["always_open"]["turnover"]
                    / n_days, "placebo_mean": entry[m]["circular"]["turnover_mean"] / n_days,
                    "switch_share_mapped": (entry[m]["real"]["switch_turnover"] / entry[m]["real"]["turnover"]
                                            if entry[m]["real"]["turnover"] else None)}
                for m in ("hedged", "raw")}
            entry["cost_drag_per_day"] = {m: {k: v * settings.cost_rate for k, v in entry["turnover_per_day"][m].items()
                                              if k != "switch_share_mapped"} for m in ("hedged", "raw")}
            entry["families_by_regime"] = family_by_regime(fw, arm, rows, day_of_bar, labels[period],
                                                           settings.cost_rate)
            entry["bars"] = n_bars
            entry["bars_per_day"] = bars_per_day
            entry["missing_returns"] = int(arm.missing[rows][1:].sum())
            per_period[period] = entry
        del fw_span
        arms_out[name] = {"evaluability": ev, "periods": per_period}
        del arm

    decisions = {m: decide(tests[m], evaluable) for m in ("hedged", "raw")}
    readings = {}
    for name in ARMS:
        beats = all(tests["hedged"][name][p]["real"]["delta"] > 0 for p in ("A", "B"))
        readings[name] = reading(decisions["hedged"][name]["verdict"], decisions["raw"][name]["verdict"], beats)
    regimes = {p: {**regime_stats(labels[p]), **threshold_margins(tex, period_days(p))} for p in ("A", "B")}
    crosstab = {p: {REGIMES[r]: int(np.count_nonzero(labels[p] == r)) for r in range(4)} for p in ("A", "B")}
    result = {
        "gate": "hedged net, circular shift placebo",
        "decision": decisions,
        "reading": readings,
        "arms": arms_out,
        "regimes": regimes,
        "regime_counts": crosstab,
    }
    days_out = tex.copy()
    days_out.index.name = "day"
    days_out["regime"] = [REGIMES.get(int(x), "tanımsız") for x in days_out["label"]]
    return result, days_out.reset_index(), pd.DataFrame(placebo_rows)


def _json(value: Any) -> Any:
    if isinstance(value, (np.floating, np.integer)):
        value = value.item()
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, np.bool_):
        return bool(value)
    return value if isinstance(value, (int, float)) else str(value)


def run(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    layer = resolve_layer(config, "ema")
    symbols = list(layer.symbols or [])
    if not symbols:
        raise DataGateError("ema evreni boş")
    cache_dir = args.cache_dir or tempfile.mkdtemp(prefix="doku-")
    data = prepare(layer.config, symbols, cache_dir)
    settings = settings_from(layer.config)
    if args.stage == "preflight":
        report = preflight(data)
        print("=== PREFLIGHT BEGIN ===")
        print(json.dumps(clean(report), indent=2, ensure_ascii=False, default=_json, allow_nan=False))
        print("=== PREFLIGHT END ===")
        return 0
    result, days, placebo = measure(data, settings)
    payload = {
        "preregistration": "docs/backtest.md > 6r (456ce51, TADİLAT-1 79167b3)",
        "parameters": {"data_start_4h": str(DATA_START_4H), "data_start_15m": str(DATA_START_15M),
                       "kasa_start": str(KASA_START), "eligible_age_days": ELIGIBLE_AGE.days, "er_days": ER_DAYS,
                       "dispersion_days": DISPERSION_DAYS, "threshold_days": THRESHOLD_DAYS,
                       "threshold_min_defined": THRESHOLD_MIN_DEFINED, "min_symbols": MIN_SYMBOLS,
                       "min_shift_days": MIN_SHIFT_DAYS, "block_permutations": BLOCK_PERMUTATIONS, "bh_q": BH_Q,
                       "seed": settings.seed, "cost_rate": settings.cost_rate,
                       "mapping": {REGIMES[r]: [f for f in FAMILIES if OPEN[r, FAMILIES.index(f)]] for r in range(4)}},
        "weeks": {p: [str(lo), str(hi)] for p, (lo, hi) in measurement_weeks().items()},
        "bases_per_arm": len(bases()),
        "data": data.report,
        **result,
        "day_file": "texture_regime_days.csv",
        "placebo_file": "texture_regime_placebo.csv",
    }
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    text = json.dumps(clean(payload), indent=2, ensure_ascii=False, default=_json, allow_nan=False)
    (out / "texture_regime.json").write_text(text, encoding="utf-8")
    days.to_csv(out / "texture_regime_days.csv", index=False)
    placebo.to_csv(out / "texture_regime_placebo.csv", index=False)
    print("=== TEXTURE_REGIME.JSON BEGIN ===")
    print(text)
    print("=== TEXTURE_REGIME.JSON END ===")
    return 0


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Doku rejimi ölçümü (docs/backtest.md > 6r).")
    parser.add_argument("--stage", choices=("preflight", "measure"), required=True,
                        help="preflight: kapsam + etiket tanımlılığı, getiri/doku YOK; measure: TEK SEFER")
    parser.add_argument("--config", default=None)
    parser.add_argument("--cache-dir", default="", help="koşuya özel önbellek (boş = geçici dizin)")
    parser.add_argument("--out-dir", default="backtests/texture_regime")
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

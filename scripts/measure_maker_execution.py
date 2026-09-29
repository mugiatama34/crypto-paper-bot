#!/usr/bin/env python3
"""Maker yürütme ölçümü (docs/backtest.md > 6s, TADİLAT-1). Ölçümün parçası DEĞİL.

Ön-kayıt (`e06cf1e`, TADİLAT-1 `2125686`) bu betikten ÖNCE, hiçbir veri görülmeden commit edildi.
Betik o metni MEKANİK olarak uygular: §6r'nin kol bahçesi (59 taban × 1D/4H/15m, ters yok) üç
biçimde yürütülür — TAKER (referans: hedef bir sonraki açılışta), MELEZ (birincil: limit = sinyal
barının kapanışı, N bar içinde dolmazsa taker) ve SAF MAKER (betimsel: dolmazsa iptal). Sinyaller
(hedef ağırlıklar) üç varyantta BİREBİR aynıdır; değişen yalnızca yürütmedir.

Ölçüler (§6s > 6): (1) kol başına bahçe ortalaması `melez − taker` (ham), hafta kümeli bootstrap
CI alt sınırı > 0, A ∧ B, kol başına düzeltmesiz; (2) aile × kol = 21 hücre HEDGE'Lİ melez net > 0,
BH q = 0.05 m = 21 ∧ CI alt > 0, A ∧ B — geçse bile kasada sınanmadan kabul edilmez; (3) betimsel.

**Model DEĞİL** (§6s > 1). Bahçe `scripts/measure_texture_regime.py`den (kol kurulumu, tabanlar,
veri hazırlığı; aile kuralları onun üzerinden `scripts/zoo_families.py`den) İTHAL edilir; bootstrap
ve yüzdelik `backtest_dc.py`den, BH `measure_model_momentum.py`den, kesim `vault.py`den.

**Salt okunur:** deftere, config'e ve `data/cache/`e yazmaz; kasaya ait tek bar çekmez (§7.8);
`texture_regime*` ve `model_momentum*` çıktılarını OKUMAZ (§6s > 2).

İKİ AŞAMA: `preflight` HİÇBİR dolum oranı, getiri, kaçırma bedeli ya da fiyat sonrası bilgi
üretmez — kapsam, tutarlılık kapısı, uygunluk, strateji sayısı ve DOĞAN EMİR sayısı (yalnızca
hedefin değişimlerinden); tekrarlanabilir. `measure` tek seferliktir (§6s > 9).

Çıkış kodları: 0 = rapor yazıldı; 3 = veri kapısı (rapor YAZILMAZ); 2 = kullanım hatası.
Tetikleyicisi `.github/workflows/measure-maker-execution.yml`.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from core.config import get_setting, load_config  # noqa: E402
from core.layers import resolve_layer  # noqa: E402
from scripts import zoo_families as zf  # noqa: E402
from scripts.backtest_dc import _percentiles, cluster_mean_draws  # noqa: E402
from scripts.measure_model_momentum import DataGateError, bh_accept, clean  # noqa: E402
from scripts.measure_texture_regime import (  # noqa: E402
    ARMS, FAMILIES, MIN_SYMBOLS, Arm, Base, Data, arm_evaluable, arm_frames, bases, build_arm, measurement_weeks,
    prepare,
)
from scripts.vault import KASA_START  # noqa: E402

logger = logging.getLogger("measure_maker_execution")

# --- Ön-kayıtlı sayılar (§6s > 4, 5, 6, 7; TADİLAT-1). Hiçbiri CLI girdisi DEĞİLDİR. ---
TAKER_FEE = 0.0005             # OKX perpetual, standart kademe (kullanıcı); `fee_rate` KULLANILMAZ (O9)
MAKER_FEE = 0.0002             # kaymasız
WINDOW_BARS: dict[str, int] = {"1D": 1, "4H": 2, "15m": 4}
WARMUP = pd.Timedelta(weeks=4)  # A'nın ilk haftasından önce, `P = T` ile (O11)
ITERATIONS = 10_000
CI_ALPHA = 0.05                 # %95 iki yönlü yüzdelik aralık
BH_Q = 0.05
MIN_WEEKS = 10
PERIODS = ("A", "B")
VARIANTS = ("taker", "melez", "maker")
PRECISION_WINDOW = zf.KNOWN_PRECISION_WINDOW
KNOWN_DAY = (pd.Timestamp("2022-12-18T00:00:00Z"), pd.Timestamp("2022-12-19T00:00:00Z"))

VERIFIED = "DOĞRULANDI"
NOT_VERIFIED = "DOĞRULANMADI"
PASSED = "GEÇTİ"
FAILED = "GEÇMEDİ"
NOT_EVALUABLE = "DEĞERLENDİRİLEMEZ"
INFO_ONLY = "bilgi — doğrulama değil"

# Emir düzeyinde betimsel sayaçlar (§6s > 6 > 3); hepsi hafta × taban, olayın barının haftasına yazılır.
ORDER_STATS = ("orders", "orders_abs", "fills", "fills_abs", "first_bar_fills", "fallbacks", "fallbacks_abs",
               "cancels", "cancels_abs", "superseded", "fee_saving", "miss_move", "forgone", "adverse_filled",
               "adverse_filled_abs", "move_all", "move_all_abs")


@dataclass(frozen=True)
class Settings:
    seed: int
    taker_rate: float           # τ = TAKER_FEE + slippage_base
    maker_rate: float           # μ = MAKER_FEE


def settings_from(config: Mapping[str, Any]) -> Settings:
    return Settings(seed=int(get_setting(dict(config), "random_seed")),
                    taker_rate=TAKER_FEE + float(get_setting(dict(config), "slippage_base")),
                    maker_rate=MAKER_FEE)


# --------------------------------------------------------------------------- #
# Izgara: hafta indeksi, fiyat matrisleri
# --------------------------------------------------------------------------- #
@dataclass
class Prices:
    """Kolun KENDİ barları, ızgara satırına hizalı (eksik bar NaN)."""
    open: np.ndarray
    high: np.ndarray
    low: np.ndarray
    close: np.ndarray
    returns: np.ndarray        # açılıştan açılışa, eksik → 0 (§6r > 6)
    eligible: np.ndarray

    @property
    def market(self) -> np.ndarray:
        """Eşit ağırlıklı sepet getirisi `m_j` (uygun semboller)."""
        count = self.eligible.sum(axis=1).astype(float)
        total = np.where(self.eligible, self.returns, 0.0).sum(axis=1)
        return np.divide(total, count, out=np.zeros_like(total), where=count > 0)


def prices_of(arm: Arm) -> Prices:
    def col(name: str) -> np.ndarray:
        return np.column_stack([arm.frames[s][name].reindex(arm.grid).to_numpy(dtype=float)
                                if not arm.frames[s].empty else np.full(len(arm.grid), np.nan)
                                for s in arm.symbols])
    return Prices(open=col("open"), high=col("high"), low=col("low"), close=col("close"),
                  returns=arm.returns, eligible=arm.eligible)


def week_index(grid: pd.DatetimeIndex) -> tuple[np.ndarray, dict[str, slice]]:
    """Satır → A ∪ B hafta sırası (dışarıda −1); dönem başına hafta dilimi."""
    weeks = measurement_weeks()
    out = np.full(len(grid), -1, dtype=int)
    slices: dict[str, slice] = {}
    offset = 0
    for period in PERIODS:
        lo, hi = weeks[period]
        n = int((hi - lo) // pd.Timedelta(weeks=1))
        inside = (grid >= lo) & (grid < hi)
        out[inside] = offset + ((grid[inside] - lo) // pd.Timedelta(weeks=1)).to_numpy().astype(int)
        slices[period] = slice(offset, offset + n)
        offset += n
    return out, slices


def precision_weeks(period: str) -> np.ndarray:
    """Dönemin haftalarından bilinen kesinlik penceresine ya da 2022-12-18'e değenler (TADİLAT-2).

    Kesme 4H H/L'sini aşağı çeker: pencerede alış dolumu kolaylaşır, satış zorlaşır. Bu
    haftaları dışarıda bırakan satır yalnızca BETİMSELDİR ve kapıyı değiştirmez.
    """
    lo, hi = measurement_weeks()[period]
    starts = pd.date_range(lo, hi - pd.Timedelta(weeks=1), freq="7D", tz="UTC")
    ends = starts + pd.Timedelta(weeks=1)
    out = np.zeros(len(starts), dtype=bool)
    for a, b in (PRECISION_WINDOW, KNOWN_DAY):
        out |= (starts < b) & (ends > a)
    return out


def start_row(grid: pd.DatetimeIndex) -> int:
    lo = measurement_weeks()["A"][0] - WARMUP
    pos = int(grid.searchsorted(lo))
    if pos == 0 or pos >= len(grid):
        raise DataGateError("ızgara ısınma başlangıcını (A − 4 hafta) kapsamıyor")
    return pos


# --------------------------------------------------------------------------- #
# Yürütme (§6s > 5) — şerit = (taban, sembol); tüm şeritler aynı anda, bar bar
# --------------------------------------------------------------------------- #
@dataclass
class Result:
    """Hafta × taban toplamları, varyant başına."""
    net: dict[str, np.ndarray]           # ham net
    hedged: dict[str, np.ndarray]        # hedge'li net (hedge bacağı taker, netleşmesiz)
    turnover: dict[str, np.ndarray]      # yürütülen |ΔP|
    stats: dict[str, dict[str, np.ndarray]] = field(default_factory=dict)   # yalnızca melez/maker


def _finite(x: np.ndarray) -> np.ndarray:
    return np.where(np.isfinite(x), x, 0.0)


def run_taker(w: np.ndarray, px: Prices, week: np.ndarray, n_weeks: int, start: int, tau: float
              ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Referans: hedef `w[t]` bar `t`'nin açılışında, `τ·|Δw|` maliyetle (§6r > 6'nın mekaniği)."""
    n, n_bases, _ = w.shape
    rows = np.arange(start + 1, n)
    rows = rows[week[rows] >= 0]
    gross = np.einsum("tbs,ts->tb", w[rows], px.returns[rows])
    dw = np.abs(w[rows] - w[rows - 1]).sum(axis=2)
    e = w[rows].sum(axis=2)
    e_prev = w[rows - 1].sum(axis=2)
    net = gross - tau * dw
    hedged = net - e * px.market[rows][:, None] - tau * np.abs(e - e_prev)
    out = [np.zeros((n_weeks, n_bases)) for _ in range(3)]
    for arr, vals in zip(out, (net, hedged, dw)):
        np.add.at(arr, week[rows], vals)
    return out[0], out[1], out[2]


def run_maker(kind: str, w: np.ndarray, px: Prices, week: np.ndarray, n_weeks: int, start: int, *,
              window: int, tau: float, mu: float) -> tuple[np.ndarray, np.ndarray, np.ndarray, dict[str, np.ndarray]]:
    """`kind` ∈ {"melez", "maker"}: §6s > 5'in emir yaşam döngüsü.

    Bar `t` (satır `t`) için sıra: (i) açılışta — hedef değiştiyse bekleyen emir iptal edilir ve yeni
    emir `Q = T − P`, limit `C_{t−1}` doğar (öncelik, O12); değişmediyse ve emir N barda dolmadıysa
    melez taker'a düşer (`O_t`), saf maker iptal eder ve hedef tekrar değişene kadar yenilemez (O1);
    (ii) bar boyunca — tutulan `P` açılıştan açılışa getiri alır; bekleyen emir bu barın KENDİ H/L'si
    limiti KESİN geçerse `ℓ`'den ya hep ya hiç dolar ve `O_{t+1}`'e kadar `O_{t+1}/ℓ − 1` alır.
    İleriye bakış: hiçbir karar `t`'den sonraki barı okumaz; yalnızca betimsel sayaçlar
    (`adverse_*`, `move_all*`, `forgone`) sonraki açılışlara bakar ve hiçbir karara girmez.
    """
    if kind not in ("melez", "maker"):
        raise ValueError(kind)
    n, n_bases, n_sym = w.shape
    shape = (n_bases, n_sym)
    P = w[start].copy()
    active = np.zeros(shape, dtype=bool)
    Q = np.zeros(shape)
    lim = np.zeros(shape)
    age = np.zeros(shape, dtype=int)
    o0 = np.zeros(shape)
    miss_open = np.zeros(shape, dtype=bool)
    miss_q = np.zeros(shape)
    miss_t0 = np.zeros(shape, dtype=int)
    cols = np.arange(n_sym)
    cum = np.vstack([np.zeros(n_sym), np.cumsum(px.returns, axis=0)])     # cum[t] = Σ r[0..t−1]
    net = np.zeros((n_weeks, n_bases))
    hedged = np.zeros((n_weeks, n_bases))
    turn = np.zeros((n_weeks, n_bases))
    stats = {k: np.zeros((n_weeks, n_bases)) for k in ORDER_STATS}
    market = px.market
    e_prev = P.sum(axis=1)
    last = n - 1
    # Bölmeler bütün şeritlerde yapılıp maskelenir: dolmamış şeridin limiti 0/NaN olabilir.
    with np.errstate(divide="ignore", invalid="ignore"):
        for t in range(start + 1, n):
            wk = int(week[t])
            acc = wk >= 0
            wt = w[t]
            changed = wt != w[t - 1]
            o_t = px.open[t]
            cost = np.zeros(n_bases)
            traded = np.zeros(n_bases)
            # (i) açılış: vade dolan emir
            due = active & ~changed & (age >= window)
            if due.any():
                q_due = np.where(due, Q, 0.0)
                if kind == "melez":
                    P = P + q_due
                    cost += tau * np.abs(q_due).sum(axis=1)
                    traded += np.abs(q_due).sum(axis=1)
                    if acc:
                        stats["fallbacks"][wk] += due.sum(axis=1)
                        stats["fallbacks_abs"][wk] += np.abs(q_due).sum(axis=1)
                        stats["miss_move"][wk] += _finite(q_due * (o_t / o0 - 1.0)).sum(axis=1)
                else:
                    miss_open |= due
                    miss_q = np.where(due, Q, miss_q)
                    miss_t0 = np.where(due, t - age, miss_t0)
                    if acc:
                        stats["cancels"][wk] += due.sum(axis=1)
                        stats["cancels_abs"][wk] += np.abs(q_due).sum(axis=1)
                active &= ~due
            if changed.any():
                if kind == "maker":
                    closing = miss_open & changed
                    if closing.any():
                        forgone = np.where(closing, miss_q * (cum[t] - cum[miss_t0, cols]), 0.0)
                        if acc:
                            stats["forgone"][wk] += forgone.sum(axis=1)
                        miss_open &= ~changed
                if acc:
                    stats["superseded"][wk] += (active & changed).sum(axis=1)
                new_q = wt - P
                born = changed & (new_q != 0.0)
                active = np.where(changed, born, active)
                Q = np.where(born, new_q, Q)
                lim = np.where(born, px.close[t - 1], lim)
                age = np.where(born, 0, age)
                o0 = np.where(born, o_t, o0)
                if acc and born.any():
                    q_born = np.where(born, Q, 0.0)
                    stats["orders"][wk] += born.sum(axis=1)
                    stats["orders_abs"][wk] += np.abs(q_born).sum(axis=1)
                    ahead = px.open[min(t + window, last)]
                    move = q_born * (ahead / o_t - 1.0)
                    ok = born & np.isfinite(move)
                    stats["move_all"][wk] += np.where(ok, move, 0.0).sum(axis=1)
                    stats["move_all_abs"][wk] += np.where(ok, np.abs(q_born), 0.0).sum(axis=1)
            # (ii) bar boyunca
            r_t = px.returns[t]
            e = P.sum(axis=1)
            gross = (P * r_t).sum(axis=1)
            if active.any():
                filled = active & (((Q > 0) & (px.low[t] < lim)) | ((Q < 0) & (px.high[t] > lim)))
                if filled.any():
                    q_f = np.where(filled, Q, 0.0)
                    gross += _finite(q_f * ((1.0 + r_t) * o_t / lim - 1.0)).sum(axis=1)
                    cost += mu * np.abs(q_f).sum(axis=1)
                    traded += np.abs(q_f).sum(axis=1)
                    if acc:
                        stats["fills"][wk] += filled.sum(axis=1)
                        stats["fills_abs"][wk] += np.abs(q_f).sum(axis=1)
                        stats["first_bar_fills"][wk] += (filled & (age == 0)).sum(axis=1)
                        stats["fee_saving"][wk] += (tau - mu) * np.abs(q_f).sum(axis=1)
                        post = q_f * (px.open[min(t + 1 + window, last)] / lim - 1.0)
                        ok = filled & np.isfinite(post)
                        stats["adverse_filled"][wk] += np.where(ok, post, 0.0).sum(axis=1)
                        stats["adverse_filled_abs"][wk] += np.where(ok, np.abs(q_f), 0.0).sum(axis=1)
                    P = P + q_f
                    active &= ~filled
                age = np.where(active, age + 1, age)
            if acc:
                row_net = gross - cost
                net[wk] += row_net
                hedged[wk] += row_net - e * market[t] - tau * np.abs(e - e_prev)
                turn[wk] += traded
            e_prev = e
    return net, hedged, turn, stats


def simulate(w: np.ndarray, px: Prices, week: np.ndarray, n_weeks: int, start: int, *, window: int,
             settings: Settings) -> Result:
    tn, th, tt = run_taker(w, px, week, n_weeks, start, settings.taker_rate)
    res = Result(net={"taker": tn}, hedged={"taker": th}, turnover={"taker": tt})
    for kind in ("melez", "maker"):
        n_, h_, t_, s_ = run_maker(kind, w, px, week, n_weeks, start, window=window, tau=settings.taker_rate,
                                   mu=settings.maker_rate)
        res.net[kind], res.hedged[kind], res.turnover[kind], res.stats[kind] = n_, h_, t_, s_
    return res


def base_weights(arm: Arm, chunk: Sequence[Base]) -> np.ndarray:
    """Tabanların hedef ağırlıkları, satır × taban × sembol — sinyaller §6r'dekiyle birebir."""
    return np.stack([b.build(arm) for b in chunk], axis=1)


# --------------------------------------------------------------------------- #
# İstatistik (§6s > 6)
# --------------------------------------------------------------------------- #
def bootstrap(values: np.ndarray, *, seed: str) -> dict[str, Any]:
    """Hafta kümeli bootstrap: haftalar YERİNE KOYARAK, hafta başına tek değer (`cluster_mean_draws`)."""
    values = np.asarray(values, dtype=float)
    weeks = len(values)
    if weeks < MIN_WEEKS:
        return {"weeks": weeks, "mean": float(values.mean()) if weeks else None, "sum": float(values.sum()),
                "low": None, "high": None, "p": 1.0, "evaluable": False}
    draws = cluster_mean_draws({f"{i:04d}": [float(v)] for i, v in enumerate(values)}, iterations=ITERATIONS,
                               seed=seed)
    low, high = _percentiles(draws, CI_ALPHA)
    p = (1 + sum(1 for d in draws if d <= 0.0)) / (len(draws) + 1)
    return {"weeks": weeks, "mean": float(values.mean()), "sum": float(values.sum()), "std": float(values.std(ddof=1)),
            "low": low, "high": high, "p": p, "evaluable": True}


def decide_improvement(stats: Mapping[str, Mapping[str, Mapping[str, Any]]],
                       evaluable: Mapping[str, Mapping[str, bool]]) -> dict[str, Any]:
    """(1): kol başına düzeltmesiz (O7) — A'da CI alt > 0; B yalnızca A'da geçen kolda."""
    out: dict[str, Any] = {}
    for arm, per in stats.items():
        def ok(period: str) -> bool:
            s = per[period]
            return bool(evaluable[arm][period] and s["evaluable"] and s["low"] > 0.0)
        a = ok("A")
        b = ok("B") if a else None
        worse = bool(evaluable[arm]["A"] and per["A"]["evaluable"] and per["A"]["high"] < 0.0)
        if a and b:
            reading = "melez maker yürütme bu bahçede taker'dan iyi"
        elif a:
            reading = "doğrulanamadı"
        elif worse:
            reading = "melez maker KÖTÜ — kaçırma bedeli ücret tasarrufunu aşıyor"
        else:
            reading = "ayırt edilemedi"
        out[arm] = {"A": (PASSED if a else FAILED) if evaluable[arm]["A"] else NOT_EVALUABLE,
                    "B": (PASSED if b else FAILED) if a else INFO_ONLY,
                    "verdict": VERIFIED if (a and b) else NOT_VERIFIED, "reading": reading}
    return out


def decide_profit(stats: Mapping[str, Mapping[str, Mapping[str, Any]]],
                  evaluable: Mapping[str, Mapping[str, bool]]) -> dict[str, Any]:
    """(2): hücre = `kol/aile`; A'da BH (m = hücre sayısı) ∧ CI alt > 0; B'de m_B = A'da geçenler."""
    def arm_of(cell: str) -> str:
        return cell.split("/")[0]

    def p(cell: str, period: str) -> float:
        s = stats[cell][period]
        return s["p"] if evaluable[arm_of(cell)][period] and s["evaluable"] else 1.0

    def low_ok(cell: str, period: str) -> bool:
        s = stats[cell][period]
        return bool(evaluable[arm_of(cell)][period] and s["evaluable"] and s["low"] > 0.0)

    cells = sorted(stats)
    passed_a = sorted({c for c in cells if low_ok(c, "A")} & bh_accept({c: p(c, "A") for c in cells}, BH_Q))
    passed_b = sorted({c for c in passed_a if low_ok(c, "B")} & bh_accept({c: p(c, "B") for c in passed_a}, BH_Q))
    out: dict[str, Any] = {"m_A": len(cells), "m_B": len(passed_a), "cells": {}}
    for c in cells:
        out["cells"][c] = {
            "A": (PASSED if c in passed_a else FAILED) if evaluable[arm_of(c)]["A"] else NOT_EVALUABLE,
            "B": (PASSED if c in passed_b else FAILED) if c in passed_a else INFO_ONLY,
            "verdict": VERIFIED if c in passed_b else NOT_VERIFIED,
        }
    return out


def combined_reading(improvement: str, profitable_cells: Sequence[str]) -> str:
    """§6s > 10'un tablosu (MEKANİK)."""
    if improvement == VERIFIED:
        if profitable_cells:
            return "maker yürütmeyle net pozitif aile(ler) var — KASA TESTİ BEKLENİYOR, kabul edilmedi"
        return "maker yürütme maliyeti düşürüyor ama hiçbir aileyi kârlı yapmıyor"
    if profitable_cells:
        return "aile maker'la net pozitif, ama iyileşme bahçe genelinde ayırt edilemedi"
    return "ayırt edilemedi"


def bh_descriptive(pvalues: Mapping[str, float]) -> list[str]:
    return sorted(bh_accept(dict(pvalues), BH_Q))


# --------------------------------------------------------------------------- #
# Kol işleme
# --------------------------------------------------------------------------- #
@dataclass
class ArmOutput:
    name: str
    base_ids: list[str]
    families: list[str]
    result: Result
    week_slices: dict[str, slice]
    evaluability: dict[str, Any]
    days: dict[str, int]


def _merge(parts: Sequence[Result]) -> Result:
    cat = lambda xs: np.concatenate(xs, axis=1)  # noqa: E731
    return Result(net={v: cat([p.net[v] for p in parts]) for v in VARIANTS},
                  hedged={v: cat([p.hedged[v] for p in parts]) for v in VARIANTS},
                  turnover={v: cat([p.turnover[v] for p in parts]) for v in VARIANTS},
                  stats={k: {s: cat([p.stats[k][s] for p in parts]) for s in ORDER_STATS} for k in ("melez", "maker")})


def run_arm(name: str, data: Data, settings: Settings, base_list: Sequence[Base]) -> ArmOutput:
    frames, start_ts = arm_frames(data, name)
    arm = build_arm(name, frames, data.first_4h, data.symbols, start=start_ts)
    px = prices_of(arm)
    week, slices = week_index(arm.grid)
    n_weeks = max(s.stop for s in slices.values())
    start = start_row(arm.grid)
    parts = []
    for family in FAMILIES:                       # bellek: aile başına bir ağırlık bloğu
        chunk = [b for b in base_list if b.family == family]
        w = base_weights(arm, chunk)
        parts.append(simulate(w, px, week, n_weeks, start, window=WINDOW_BARS[name], settings=settings))
        del w
        logger.info("%s %s: %d taban yürütüldü", name, family, len(chunk))
    weeks = measurement_weeks()
    return ArmOutput(name=name, base_ids=[b.id for f in FAMILIES for b in base_list if b.family == f],
                     families=[f for f in FAMILIES for b in base_list if b.family == f],
                     result=_merge(parts), week_slices=slices, evaluability=arm_evaluable(arm),
                     days={p: int((hi - lo) // pd.Timedelta(days=1)) for p, (lo, hi) in weeks.items()})


def _ratio(num: float, den: float, scale: float = 1.0) -> float | None:
    return float(num / den * scale) if den else None


def describe(out: ArmOutput, period: str) -> dict[str, Any]:
    """§6s > 6 > 3: aile × varyant betimsel tablo; Δ ayrışması (bahçe ortalaması)."""
    sl = out.week_slices[period]
    fam = np.array(out.families)
    res = out.result
    days = out.days[period]
    table: dict[str, Any] = {}
    for f in (*FAMILIES, "bahçe"):
        cols = np.ones(len(fam), dtype=bool) if f == "bahçe" else fam == f
        entry: dict[str, Any] = {"bases": int(cols.sum())}
        for v in VARIANTS:
            mean_net = res.net[v][sl][:, cols].sum(axis=0).mean()
            mean_h = res.hedged[v][sl][:, cols].sum(axis=0).mean()
            entry[v] = {"net": float(mean_net), "net_hedged": float(mean_h),
                        "turnover_per_day": float(res.turnover[v][sl][:, cols].sum(axis=0).mean() / days)}
        for v in ("melez", "maker"):
            s = {k: float(res.stats[v][k][sl][:, cols].sum()) for k in ORDER_STATS}
            missed_abs = s["fallbacks_abs"] if v == "melez" else s["cancels_abs"]
            entry[v].update({
                "orders": int(s["orders"]),
                "fill_rate": _ratio(s["fills"], s["orders"]),
                "fill_rate_abs": _ratio(s["fills_abs"], s["orders_abs"]),
                "first_bar_fill_rate": _ratio(s["first_bar_fills"], s["orders"]),
                "fallback_rate": _ratio(s["fallbacks"], s["orders"]) if v == "melez" else None,
                "cancel_rate": _ratio(s["cancels"], s["orders"]) if v == "maker" else None,
                "superseded_rate": _ratio(s["superseded"], s["orders"]),
                "miss_cost_bp": (_ratio(s["miss_move"], missed_abs, 1e4) if v == "melez"
                                 else _ratio(s["forgone"], missed_abs, 1e4)),
                "adverse_filled_bp": _ratio(s["adverse_filled"], s["adverse_filled_abs"], 1e4),
                "all_orders_bp": _ratio(s["move_all"], s["move_all_abs"], 1e4),
                "selection_bp": (None if not (s["adverse_filled_abs"] and s["move_all_abs"]) else
                                 1e4 * (s["adverse_filled"] / s["adverse_filled_abs"]
                                        - s["move_all"] / s["move_all_abs"])),
            })
        table[f] = entry
    n_bases = len(fam)
    melez = res.stats["melez"]
    delta = float((res.net["melez"][sl] - res.net["taker"][sl]).sum() / n_bases)
    saving = float(melez["fee_saving"][sl].sum() / n_bases)
    miss = float(-melez["miss_move"][sl].sum() / n_bases)
    decomposition = {"delta": delta, "fee_saving": saving, "miss_cost": miss, "residual": delta - saving - miss}
    return {"families": table, "decomposition_melez": decomposition}


def order_counts(arm: Arm, base_list: Sequence[Base]) -> dict[str, Any]:
    """PREFLIGHT: doğan emir sayısı = hedefin değiştiği (satır, taban, sembol) sayısı.

    Fiyat sonrası bilgi YOK: yalnızca hedef ağırlıkların (sinyallerin) değişimi sayılır.
    """
    week, slices = week_index(arm.grid)
    start = start_row(arm.grid)
    grid = arm.grid
    in_window = (grid > PRECISION_WINDOW[0]) & (grid <= PRECISION_WINDOW[1])
    on_day = (grid > KNOWN_DAY[0]) & (grid <= KNOWN_DAY[1])
    weeks = measurement_weeks()
    out: dict[str, Any] = {p: {} for p in PERIODS}
    special = {"precision_window": 0, "2022-12-18": 0}
    for base in base_list:
        w = base.build(arm)
        changed = (w[1:] != w[:-1]).sum(axis=1)             # satır t (1..) için değişen sembol sayısı
        rows = np.arange(1, len(grid))
        keep = rows > start
        for p in PERIODS:
            m = keep & (week[rows] >= slices[p].start) & (week[rows] < slices[p].stop)
            fam = out[p].setdefault(base.family, 0)
            out[p][base.family] = fam + int(changed[m].sum())
        special["precision_window"] += int(changed[in_window[1:]].sum())
        special["2022-12-18"] += int(changed[on_day[1:]].sum())
    result: dict[str, Any] = {}
    for p in PERIODS:
        lo, hi = weeks[p]
        days = int((hi - lo) // pd.Timedelta(days=1))
        total = sum(out[p].values())
        result[p] = {"orders": total, "orders_per_day": total / days,
                     "orders_per_base_per_day": total / days / len(base_list),
                     "by_family": out[p]}
    result["orders_in_known_precision_window"] = special["precision_window"]
    result["orders_on_2022_12_18"] = special["2022-12-18"]
    return result


# --------------------------------------------------------------------------- #
# Aşamalar
# --------------------------------------------------------------------------- #
def preflight(data: Data) -> dict[str, Any]:
    """Dolum oranı, getiri, kaçırma bedeli ya da fiyat sonrası bilgi ÜRETMEZ (test)."""
    base_list = bases()
    arms: dict[str, Any] = {}
    for name in ARMS:
        frames, start = arm_frames(data, name)
        arm = build_arm(name, frames, data.first_4h, data.symbols, start=start)
        arms[name] = {"bars": int(len(arm.grid)), "window_bars": WINDOW_BARS[name], **arm_evaluable(arm),
                      "order_counts": order_counts(arm, base_list)}
    weeks = measurement_weeks()
    return {"stage": "preflight", "kasa_start": str(KASA_START),
            "weeks": {p: {"start": str(lo), "end": str(hi), "weeks": int((hi - lo) // pd.Timedelta(weeks=1))}
                      for p, (lo, hi) in weeks.items()},
            "bases_per_arm": len(base_list), "strategies": len(base_list) * len(ARMS), "arms": arms, **data.report}


def measure(data: Data, settings: Settings) -> tuple[dict[str, Any], pd.DataFrame]:
    base_list = bases()
    outputs = {name: run_arm(name, data, settings, base_list) for name in ARMS}
    evaluable = {name: {p: bool(o.evaluability[p]["evaluable"]) for p in PERIODS} for name, o in outputs.items()}

    improvement: dict[str, dict[str, Any]] = {}
    improvement_desc: dict[str, Any] = {}
    profit: dict[str, dict[str, Any]] = {}
    profit_raw: dict[str, Any] = {}
    describe_out: dict[str, Any] = {}
    week_rows = []
    for name, o in outputs.items():
        res = o.result
        fam = np.array(o.families)
        improvement[name] = {}
        improvement_desc[name] = {}
        describe_out[name] = {}
        for p in PERIODS:
            sl = o.week_slices[p]
            d = res.net["melez"][sl] - res.net["taker"][sl]
            improvement[name][p] = bootstrap(d.mean(axis=1), seed=f"{settings.seed}:maker:iyilesme:{name}:{p}")
            fam_eq = np.mean([d[:, fam == f].mean(axis=1) for f in FAMILIES], axis=0)
            improvement_desc[name][p] = {
                "maker_minus_taker": bootstrap((res.net["maker"][sl] - res.net["taker"][sl]).mean(axis=1),
                                               seed=f"{settings.seed}:maker:iyilesme-b:{name}:{p}"),
                "family_equal_weight": bootstrap(fam_eq, seed=f"{settings.seed}:maker:iyilesme-aile:{name}:{p}"),
                "hedged": bootstrap((res.hedged["melez"][sl] - res.hedged["taker"][sl]).mean(axis=1),
                                    seed=f"{settings.seed}:maker:iyilesme-h:{name}:{p}"),
            }
            keep = ~precision_weeks(p)
            if not keep.all():
                improvement_desc[name][p]["excluding_precision_weeks"] = {
                    "weeks_excluded": int((~keep).sum()),
                    **bootstrap(d.mean(axis=1)[keep], seed=f"{settings.seed}:maker:iyilesme-kesinlik:{name}:{p}"),
                }
            for f in FAMILIES:
                cell = f"{name}/{f}"
                cols = fam == f
                profit.setdefault(cell, {})[p] = bootstrap(
                    res.hedged["melez"][sl][:, cols].mean(axis=1),
                    seed=f"{settings.seed}:maker:karlilik:{name}:{f}:{p}")
                profit_raw.setdefault(cell, {})[p] = {
                    "melez_raw": bootstrap(res.net["melez"][sl][:, cols].mean(axis=1),
                                           seed=f"{settings.seed}:maker:karlilik-ham:{name}:{f}:{p}"),
                    "maker_hedged": bootstrap(res.hedged["maker"][sl][:, cols].mean(axis=1),
                                              seed=f"{settings.seed}:maker:karlilik-b:{name}:{f}:{p}"),
                }
                if not keep.all():
                    profit_raw[cell][p]["excluding_precision_weeks"] = bootstrap(
                        res.hedged["melez"][sl][:, cols].mean(axis=1)[keep],
                        seed=f"{settings.seed}:maker:karlilik-kesinlik:{name}:{f}:{p}")
            describe_out[name][p] = describe(o, p)
        lo_weeks = {p: measurement_weeks()[p][0] for p in PERIODS}
        for p in PERIODS:
            sl = o.week_slices[p]
            for i in range(sl.stop - sl.start):
                wk = sl.start + i
                start = lo_weeks[p] + pd.Timedelta(weeks=i)
                for j, bid in enumerate(o.base_ids):
                    row = {"arm": name, "period": p, "week": str(start.date()), "base": bid, "family": o.families[j]}
                    for v in VARIANTS:
                        row[v] = res.net[v][wk, j]
                        row[f"{v}_hedged"] = res.hedged[v][wk, j]
                    week_rows.append(row)

    dec_improvement = decide_improvement(improvement, evaluable)
    dec_profit = decide_profit(profit, evaluable)
    readings = {}
    for name in ARMS:
        cells = [c for c, v in dec_profit["cells"].items() if c.startswith(f"{name}/") and v["verdict"] == VERIFIED]
        readings[name] = combined_reading(dec_improvement[name]["verdict"], cells)
    bh_improvement = {p: bh_descriptive({a: improvement[a][p]["p"] for a in ARMS}) for p in PERIODS}
    result = {
        "improvement": {"gate": "melez − taker (ham), taban eşit ağırlık, hafta kümeli bootstrap, CI alt > 0",
                        "stats": improvement, "decision": dec_improvement,
                        "bh_m3_descriptive": bh_improvement, "descriptive": improvement_desc},
        "profit": {"gate": "melez HEDGE'Lİ net, aile × kol, BH q=0.05 m=21 ∧ CI alt > 0; kasasız kabul YOK",
                   "stats": profit, "decision": dec_profit, "descriptive": profit_raw},
        "reading": readings,
        "describe": describe_out,
        "evaluability": {name: o.evaluability for name, o in outputs.items()},
    }
    return result, pd.DataFrame(week_rows)


def run(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    layer = resolve_layer(config, "ema")
    symbols = list(layer.symbols or [])
    if not symbols:
        raise DataGateError("ema evreni boş")
    cache_dir = args.cache_dir or tempfile.mkdtemp(prefix="maker-")
    data = prepare(layer.config, symbols, cache_dir)
    settings = settings_from(layer.config)
    if args.stage == "preflight":
        report = preflight(data)
        print("=== PREFLIGHT BEGIN ===")
        print(json.dumps(clean(report), indent=2, ensure_ascii=False, default=str, allow_nan=False))
        print("=== PREFLIGHT END ===")
        return 0
    result, weeks = measure(data, settings)
    for name, ev in result["evaluability"].items():
        if not any(ev[p]["eligible_at_start"] >= MIN_SYMBOLS for p in PERIODS):
            logger.warning("%s: hiçbir dönemde değerlendirilebilir değil", name)
    payload = {
        "preregistration": "docs/backtest.md > 6s (e06cf1e, TADİLAT-1 2125686)",
        "parameters": {"taker_fee": TAKER_FEE, "maker_fee": MAKER_FEE, "taker_rate": settings.taker_rate,
                       "maker_rate": settings.maker_rate, "window_bars": WINDOW_BARS, "warmup": str(WARMUP),
                       "iterations": ITERATIONS, "ci_alpha": CI_ALPHA, "bh_q": BH_Q, "seed": settings.seed,
                       "kasa_start": str(KASA_START)},
        "weeks": {p: [str(lo), str(hi)] for p, (lo, hi) in measurement_weeks().items()},
        "bases_per_arm": len(bases()),
        "data": data.report,
        **result,
        "week_file": "maker_execution_weeks.csv",
    }
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    text = json.dumps(clean(payload), indent=2, ensure_ascii=False, default=str, allow_nan=False)
    (out / "maker_execution.json").write_text(text, encoding="utf-8")
    weeks.to_csv(out / "maker_execution_weeks.csv", index=False)
    print("=== MAKER_EXECUTION.JSON BEGIN ===")
    print(text)
    print("=== MAKER_EXECUTION.JSON END ===")
    return 0


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Maker yürütme ölçümü (docs/backtest.md > 6s).")
    parser.add_argument("--stage", choices=("preflight", "measure"), required=True,
                        help="preflight: kapsam + emir sayısı, dolum/getiri YOK; measure: TEK SEFER")
    parser.add_argument("--config", default=None)
    parser.add_argument("--cache-dir", default="", help="koşuya özel önbellek (boş = geçici dizin)")
    parser.add_argument("--out-dir", default="backtests/maker_execution")
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

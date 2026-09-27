#!/usr/bin/env python3
"""Oynaklık hedefleme ölçümü (docs/backtest.md > 6o, TADİLAT-1). Ölçümün parçası DEĞİL.

Ön-kayıt (`5340b33`, TADİLAT-1 `b7458bf`) bu betikten ÖNCE, hiçbir veri görülmeden commit
edildi. Betik o metni MEKANİK olarak uygular: σ̂/σ_hedef/w kuralı, üç strateji (S, U, F),
devir × maliyet, eşleştirilmiş iki bloklu bootstrap, geçme kuralı (iki alt sınırın minimumu,
A ∧ B, varlık başına) ve mekanizma ölçümü (σ̂ → sonraki 30 günün oynaklığı). Hiçbir sayı
burada SEÇİLMEZ.

**Model DEĞİL, ikinci bir backtest DEĞİL.** Maruziyet "özsermayenin `w` kesri, stop'suz,
günlük yeniden dengeleme"dir ve motorun boyutlandırması (kural 11) bunu ifade edemez
(§6o > 1). Burada kurulan tek şey pasif bir getiri serisidir. Sharpe ve max drawdown
`core/metrics.py::account_stats`ten, günlük kapanış `measure_regime.py::daily_closes`ten,
yüzdelik `backtest_dc.py::_percentiles`ten, pencereler `backtest_ema.py`den gelir.

**Salt okunur:** deftere, config'e ve `data/cache/`e yazmaz (koşuya özel geçici önbellek).
Kullanılan günlük kapanışlar ve günlük ağırlıklar sonuç yüküne SABİTLENİR.

İKİ AŞAMA: `preflight` HİÇBİR getiri, σ̂, ağırlık ya da Sharpe üretmez — yalnızca serilerin
KAPSAMINI (ilk/son bar, eksik gün) raporlar ve tekrarlanabilir; `measure` tek seferliktir
(§6o > 12).

Çıkış kodları: 0 = rapor yazıldı, dört (varlık, dönem) de ölçüldü; 3 = veri kapısı (BTC serisi
boş → rapor YAZILMAZ; ya da bir (varlık, dönem) tanımsız gün yüzünden ölçülemedi → rapor
yazılır, sebebiyle); 2 = kullanım hatası. Tetikleyicisi `.github/workflows/measure-vol-target.yml`.
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
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from core.config import get_setting, load_config  # noqa: E402
from core.data import fetch_ohlcv  # noqa: E402
from core.layers import resolve_layer  # noqa: E402
from core.metrics import account_stats  # noqa: E402
from scripts.backtest_dc import MIN_CLUSTERS, _percentiles  # noqa: E402
from scripts.backtest_ema import PERIOD_A_CUTOFF, PERIOD_A_START  # noqa: E402
from scripts.measure_regime import daily_closes  # noqa: E402

logger = logging.getLogger("measure_vol_target")

# --- Ön-kayıtlı sayılar (§6o > 5, 7, 10; TADİLAT-1). Hiçbiri CLI girdisi DEĞİLDİR. ---
VOL_DAYS = 30
TARGET_DAYS = 365
WEIGHT_CAP = 1.0
DAYS_PER_YEAR = 365.0
FORWARD_DAYS = 30                 # TADİLAT-1 > 4: hedef penceresi, t DÂHİL sonraki 30 gün
BLOCK_WEEKS = 4                   # TADİLAT-1 > 3: ikinci blok tanımı
MDE_Z = 2.802                     # §6j > 9: z₀.₉₇₅ + z₀.₈₀
# Isınma A'nın ÖNCESİNDEN (§6o > 4: ≥ 396 gün); §6l'nin veri başlangıcıyla aynı gün.
DATA_START = pd.Timestamp("2020-11-01T00:00:00Z")

ASSETS = ("btc", "basket")        # (a), (b)
STRATEGIES = ("S", "U", "F")      # ölçeklenmiş, ölçeklenmemiş, sabit ağırlık
BLOCKS = ("week", "4week")

PASSED = "GEÇTİ"
FAILED = "GEÇMEDİ"
NOT_EVALUABLE = "DEĞERLENDİRİLEMEZ"
PREMISE_HELD = "ÖNCÜL TUTTU"
PREMISE_FAILED = "ÖNCÜL TUTMADI"

# TADİLAT-1 > 4'ün okuma tablosu: (geçme kararı, öncül) -> cümle.
SENTENCES = {
    (FAILED, PREMISE_HELD): "tez doğrulanamadı, dayanağı sağlam",
    (FAILED, PREMISE_FAILED): "tez dayanaksız",
    (PASSED, PREMISE_FAILED): "Sharpe farkı var ama önerilen mekanizmadan gelmiyor — kaynağı açıklanmadı",
    (PASSED, PREMISE_HELD): "doğrulandı",
}


class DataGateError(RuntimeError):
    """Rapor yazılamaz: ölçülecek seri yok."""


@dataclass(frozen=True)
class Settings:
    iterations: int
    alpha: float
    seed: int
    cost_rate: float


@dataclass(frozen=True)
class Period:
    name: str
    first: pd.Timestamp           # ilk getiri günü (UTC gün başı)
    last: pd.Timestamp            # son getiri günü

    def days(self) -> pd.DatetimeIndex:
        return pd.date_range(self.first, self.last, freq="D", tz="UTC")


def periods(now: pd.Timestamp) -> tuple[Period, Period]:
    """A: `PERIOD_A_START` → kesimden önceki gün; B: kesim günü → `now`dan önce kapanmış son gün."""
    a_first = pd.Timestamp(PERIOD_A_START).tz_convert("UTC").floor("D")
    cutoff = pd.Timestamp(PERIOD_A_CUTOFF).tz_convert("UTC").floor("D")
    b_last = last_closed_day(now)
    return Period("A", a_first, cutoff - pd.Timedelta(days=1)), Period("B", cutoff, b_last)


def last_closed_day(now: pd.Timestamp) -> pd.Timestamp:
    """`now` anında KAPANMIŞ son UTC günü: günün 24:00'ı ≤ now."""
    stamp = now.tz_convert("UTC") if now.tzinfo else now.tz_localize("UTC")
    return (stamp - pd.Timedelta(days=1)).floor("D")


# --------------------------------------------------------------------------- #
# Kural (§6o > 3, 5)
# --------------------------------------------------------------------------- #
def closes_frame(bars: Mapping[str, pd.DataFrame], symbols: Sequence[str]) -> pd.DataFrame:
    """Gün × sembol günlük kapanış; takvim TAM, eksik gün NaN (`daily_closes`in kuralı)."""
    series = {s: daily_closes(bars[s]) for s in symbols if s in bars and not bars[s].empty}
    frame = pd.DataFrame(series)
    if frame.empty:
        return frame.reindex(columns=list(symbols))
    full = pd.date_range(frame.index.min(), frame.index.max(), freq="D", tz="UTC")
    return frame.reindex(full).reindex(columns=list(symbols))


def eligibility(closes: pd.DataFrame) -> pd.DataFrame:
    """`E_t`: `t−30 … t−1` günlerinin TAMAMINDA kapanış var. `C_t`nin varlığı GİRMEZ."""
    present = closes.notna().astype(float)
    return present.rolling(VOL_DAYS, min_periods=VOL_DAYS).sum().shift(1).eq(VOL_DAYS)


def basket_returns(closes: pd.DataFrame, eligible: pd.DataFrame) -> tuple[pd.DataFrame, pd.Series, pd.DataFrame]:
    """`(sembol getirileri, sepet getirisi R^B, eksik-kapanış maskesi)`.

    Uygun bir sembolün `C_t`si eksikse o gün 0 getiri alır (nakit gibi) ve maske SAYAR.
    `E_t` boşsa sepet getirisi tanımsızdır (NaN).
    """
    raw = closes / closes.shift(1) - 1.0
    missing = eligible & raw.isna()
    returns = raw.where(eligible).fillna(0.0).where(eligible)
    count = eligible.sum(axis=1)
    basket = returns.sum(axis=1, min_count=1) / count.where(count > 0)
    return returns, basket, missing


def weight_table(simple_returns: pd.Series) -> pd.DataFrame:
    """σ̂_t, σ_hedef,t ve w_t — yalnızca `t−1`e kadar kapanmış veriyle (§6o > 5)."""
    log_returns = np.log1p(simple_returns)
    sigma_hat = log_returns.rolling(VOL_DAYS, min_periods=VOL_DAYS).std(ddof=1).shift(1) * math.sqrt(DAYS_PER_YEAR)
    # ÖNCEKİ 365 günün σ̂'ları, σ̂_t HARİÇ.
    target = sigma_hat.rolling(TARGET_DAYS, min_periods=TARGET_DAYS).median().shift(1)
    ratio = target / sigma_hat
    zero = sigma_hat.eq(0.0) & target.notna()
    weight = ratio.clip(upper=WEIGHT_CAP).where(~zero, WEIGHT_CAP)
    weight = weight.where(sigma_hat.notna() & target.notna())
    return pd.DataFrame({"sigma_hat": sigma_hat, "sigma_target": target, "w": weight,
                         "sigma_zero": zero})


def forward_vol(simple_returns: pd.Series) -> pd.Series:
    """σ_ileri,t = std(g_t … g_{t+29}) × √365 — t DÂHİL, öngörücünün penceresiyle örtüşmez."""
    log_returns = np.log1p(simple_returns)
    return (log_returns.rolling(FORWARD_DAYS, min_periods=FORWARD_DAYS).std(ddof=1)
            .shift(-(FORWARD_DAYS - 1)) * math.sqrt(DAYS_PER_YEAR))


# --------------------------------------------------------------------------- #
# Getiri, maliyet (§6o > 6)
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class SimPath:
    net: np.ndarray               # günlük net getiri
    turnover: np.ndarray
    cost: np.ndarray


def simulate(exposure: np.ndarray, returns: np.ndarray, eligible: np.ndarray, *, cost_rate: float) -> SimPath:
    """Günlük yeniden dengeleme; dönem NAKİTLE başlar.

    `exposure[t]` toplam hedef ağırlık, `returns[t, i]` sembol getirisi, `eligible[t, i]`
    `E_t`. Hedef sembol başına `exposure / |E_t|`; devir hedef ile ÖNCEKİ günün kayan
    ağırlığı arasındaki mutlak farkların toplamıdır (§6o > 6; tek varlıkta `|w_t − w̃_{t−1}|`).
    """
    days, width = returns.shape
    held = np.zeros(width)
    net = np.empty(days)
    turnover = np.empty(days)
    cost = np.empty(days)
    for t in range(days):
        members = eligible[t]
        count = int(members.sum())
        target = np.where(members, exposure[t] / count, 0.0) if count else np.zeros(width)
        turn = float(np.abs(target - held).sum())
        r = np.where(members, returns[t], 0.0)
        gross = float((target * r).sum())
        turnover[t] = turn
        cost[t] = turn * cost_rate
        net[t] = gross - cost[t]
        # Kayan ağırlık (maliyet ÖNCESİ, §6o > 6'nın formülü).
        held = target * (1.0 + r) / (1.0 + gross)
    return SimPath(net=net, turnover=turnover, cost=cost)


def equity_rows(net: Sequence[float], first_day: pd.Timestamp | None = None) -> list[dict[str, Any]]:
    rows = [{"equity": 1.0, "ts": first_day}]
    value = 1.0
    for index, r in enumerate(net):
        value *= 1.0 + float(r)
        rows.append({"equity": value,
                     "ts": first_day + pd.Timedelta(days=index + 1) if first_day is not None else None})
    return rows


def account(net: Sequence[float], first_day: pd.Timestamp | None = None) -> dict[str, float]:
    """Sharpe, MDD, toplam getiri: `core/metrics.py::account_stats`in TEK tanımı."""
    stats = account_stats(equity_rows(net, first_day), initial_capital=1.0, periods_per_year=DAYS_PER_YEAR)
    return {"sharpe": stats.sharpe, "max_drawdown": stats.max_drawdown, "total_return": stats.total_return}


# --------------------------------------------------------------------------- #
# Kümeler ve bootstrap (§6o > 8, TADİLAT-1 > 3)
# --------------------------------------------------------------------------- #
def cluster_ids(days: pd.DatetimeIndex, block: str) -> np.ndarray:
    """ISO hafta ya da dönemin ilk ISO haftasından başlayan ardışık 4 ISO haftalık gruplar."""
    iso = days.isocalendar()
    weeks = [f"{y}-{w:02d}" for y, w in zip(iso["year"], iso["week"])]
    order = {week: index for index, week in enumerate(dict.fromkeys(weeks))}
    if block == "week":
        return np.array([order[w] for w in weeks])
    if block == "4week":
        return np.array([order[w] // BLOCK_WEEKS for w in weeks])
    raise ValueError(f"bilinmeyen blok: {block}")


def block_draws(clusters: np.ndarray, *, iterations: int, seed: str) -> list[np.ndarray]:
    """Her iterasyonda C kümeden C tanesi YERİNE KOYARAK; dönen: çekilen günlerin indeksleri."""
    ids = sorted(set(int(c) for c in clusters))
    members = {c: np.flatnonzero(clusters == c) for c in ids}
    rng = random.Random(seed)
    draws = []
    for _ in range(int(iterations)):
        picked = [members[ids[rng.randrange(len(ids))]] for _ in ids]
        draws.append(np.concatenate(picked))
    return draws


def spearman(x: np.ndarray, y: np.ndarray) -> float:
    if len(x) < 3:
        return float("nan")
    rx = pd.Series(x).rank(method="average").to_numpy()
    ry = pd.Series(y).rank(method="average").to_numpy()
    if rx.std() == 0.0 or ry.std() == 0.0:
        return float("nan")
    return float(np.corrcoef(rx, ry)[0, 1])


def interval(values: Sequence[float], alpha: float) -> tuple[float | None, float | None]:
    finite = [v for v in values if math.isfinite(v)]
    if not finite:
        return None, None
    return _percentiles(finite, alpha)


def binding_low(blocks: Mapping[str, Mapping[str, Any]]) -> float | None:
    """TADİLAT-1 > 3: iki alt sınırın MİNİMUMU; biri değerlendirilemezse bağlayıcı yok."""
    lows = [b.get("low") for b in blocks.values()]
    if any(low is None for low in lows) or not all(b.get("evaluable") for b in blocks.values()):
        return None
    return min(lows)


# --------------------------------------------------------------------------- #
# Ölçüm
# --------------------------------------------------------------------------- #
def measure_period(
    *,
    asset: str,
    period: Period,
    returns: pd.DataFrame,
    eligible: pd.DataFrame,
    single_returns: pd.Series,
    weights: pd.DataFrame,
    settings: Settings,
) -> dict[str, Any]:
    """Bir (varlık, dönem): S, U, F; birincil ve ikincil farklar; iki bloklu aralıklar."""
    days = period.days()
    w = weights["w"].reindex(days)
    r_single = single_returns.reindex(days)
    undefined = int((w.isna() | r_single.isna()).sum())
    block: dict[str, Any] = {"first_day": str(days[0].date()), "last_day": str(days[-1].date()),
                             "days": int(len(days)), "undefined_days": undefined}
    if undefined:
        block.update({"measured": False,
                      "reason": f"{undefined} tanımsız gün (σ̂, σ_hedef ya da getiri) — §6o > 5"})
        return block

    ret = returns.reindex(days).fillna(0.0).to_numpy()
    elig = eligible.reindex(days).fillna(False).to_numpy(dtype=bool)
    w_arr = w.to_numpy(dtype=float)
    w_bar = float(w_arr.mean())
    exposures = {"S": w_arr, "U": np.ones(len(days)), "F": np.full(len(days), w_bar)}
    paths = {k: simulate(v, ret, elig, cost_rate=settings.cost_rate) for k, v in exposures.items()}
    points = {k: account(p.net, days[0]) for k, p in paths.items()}

    def diffs(stats: Mapping[str, Mapping[str, float]]) -> dict[str, float]:
        return {
            "d_sharpe": stats["S"]["sharpe"] - stats["U"]["sharpe"],
            "d_sharpe_f": stats["S"]["sharpe"] - stats["F"]["sharpe"],
            "d_mdd": stats["S"]["max_drawdown"] - stats["F"]["max_drawdown"],
            "d_return": stats["S"]["total_return"] - stats["F"]["total_return"],
        }

    point_diffs = diffs(points)
    net = {k: p.net for k, p in paths.items()}
    rho = float(np.corrcoef(net["S"], net["U"])[0, 1])
    se_jk = math.sqrt(DAYS_PER_YEAR) * math.sqrt(max(0.0, 2.0 * (1.0 - rho)) / len(days))

    intervals: dict[str, dict[str, Any]] = {}
    for blk in BLOCKS:
        clusters = cluster_ids(days, blk)
        seed = f"{settings.seed}:voltarget:{asset}:{period.name}" + (":4w" if blk == "4week" else "")
        draws = block_draws(clusters, iterations=settings.iterations, seed=seed)
        collected: dict[str, list[float]] = {key: [] for key in point_diffs}
        for idx in draws:
            stats = {k: account(net[k][idx]) for k in STRATEGIES}
            for key, value in diffs(stats).items():
                collected[key].append(value)
        n_clusters = len(set(int(c) for c in clusters))
        entry: dict[str, Any] = {"clusters": n_clusters, "evaluable": n_clusters >= MIN_CLUSTERS}
        for key, values in collected.items():
            low, high = interval(values, settings.alpha)
            finite = [v for v in values if math.isfinite(v)]
            entry[key] = {"low": low, "high": high,
                          "se": float(np.std(finite, ddof=1)) if len(finite) > 1 else None}
        entry["d_mdd"]["note"] = "yaklaşık — MDD bir yol metriğidir (§6o > 8)"
        se = entry["d_sharpe"]["se"]
        entry["mde_d_sharpe"] = MDE_Z * se if se is not None else None
        entry["deff_vs_jobson_korkie"] = (se / se_jk) ** 2 if se and se_jk > 0 else None
        intervals[blk] = entry

    primary_blocks = {blk: {"low": intervals[blk]["d_sharpe"]["low"], "evaluable": intervals[blk]["evaluable"]}
                      for blk in BLOCKS}
    low = binding_low(primary_blocks)

    realized_vol = {k: float(np.std(v, ddof=1) * math.sqrt(DAYS_PER_YEAR)) for k, v in net.items()}
    years = len(days) / DAYS_PER_YEAR
    counts = elig.sum(axis=1)
    block.update({
        "measured": True,
        "strategies": {
            k: {**points[k],
                "annualized_return": (1.0 + points[k]["total_return"]) ** (1.0 / years) - 1.0,
                "realized_vol": realized_vol[k],
                "turnover_per_day": float(paths[k].turnover.mean()),
                "cost_drag_per_day": float(paths[k].cost.mean())}
            for k in STRATEGIES
        },
        "weights": {
            "w_bar": w_bar,
            "share_below_cap": float((w_arr < WEIGHT_CAP).mean()),
            "min": float(w_arr.min()),
            "p10": float(np.percentile(w_arr, 10)),
            "median": float(np.median(w_arr)),
            "sigma_zero_days": int(weights["sigma_zero"].reindex(days).fillna(False).sum()),
        },
        "eligible_count": {"min": int(counts.min()), "median": float(np.median(counts)), "max": int(counts.max())},
        "point": point_diffs,
        "intervals": intervals,
        "binding_low": low,
        "verdict": NOT_EVALUABLE if low is None else (PASSED if low > 0.0 else FAILED),
        "precision": {"rho_s_u": rho, "se_jobson_korkie": se_jk,
                      "mde_jobson_korkie": MDE_Z * se_jk},
    })
    return block


def measure_mechanism(*, asset: str, period: Period, single_returns: pd.Series,
                      weights: pd.DataFrame, settings: Settings) -> dict[str, Any]:
    """TADİLAT-1 > 4: σ̂_t ↔ σ_ileri,t Spearman; hedef penceresi dönemden TAŞMAZ."""
    days = period.days()
    eligible_days = days[: max(0, len(days) - (FORWARD_DAYS - 1))]
    fwd = forward_vol(single_returns)
    frame = pd.DataFrame({"x": weights["sigma_hat"].reindex(eligible_days),
                          "y": fwd.reindex(eligible_days)})
    # İleri pencere dönem sonunu aşmasın: son 29 gün yapısal olarak düştü; burada ayrıca
    # tanımsız (eksik veri) günler düşer ve SAYILIR.
    undefined = int(frame.isna().any(axis=1).sum())
    frame = frame.dropna()
    block: dict[str, Any] = {"days_used": int(len(frame)), "dropped_tail_days": int(len(days) - len(eligible_days)),
                             "undefined_days": undefined}
    if len(frame) < 3:
        block.update({"rho": None, "verdict_inputs_ok": False})
        return block
    x = frame["x"].to_numpy()
    y = frame["y"].to_numpy()
    block["rho"] = spearman(x, y)
    intervals = {}
    for blk in BLOCKS:
        clusters = cluster_ids(pd.DatetimeIndex(frame.index), blk)
        seed = f"{settings.seed}:voltarget:{asset}:{period.name}:mech" + (":4w" if blk == "4week" else "")
        values = [spearman(x[idx], y[idx])
                  for idx in block_draws(clusters, iterations=settings.iterations, seed=seed)]
        low, high = interval(values, settings.alpha)
        n_clusters = len(set(int(c) for c in clusters))
        intervals[blk] = {"low": low, "high": high, "clusters": n_clusters,
                          "evaluable": n_clusters >= MIN_CLUSTERS}
    block["intervals"] = intervals
    block["binding_low"] = binding_low(intervals)
    step_days = non_overlapping_days(pd.DatetimeIndex(frame.index))
    sub = frame.loc[step_days]
    block["non_overlapping"] = {"n": int(len(sub)),
                                "rho": spearman(sub["x"].to_numpy(), sub["y"].to_numpy())}
    block["verdict_inputs_ok"] = True
    return block


def non_overlapping_days(days: pd.DatetimeIndex) -> pd.DatetimeIndex:
    """İlk uygun günden başlayarak her 30. TAKVİM günü (t₀, t₀+30, …), uygun olanlar."""
    if len(days) == 0:
        return days
    first = days[0]
    wanted = pd.date_range(first, days[-1], freq=f"{FORWARD_DAYS}D", tz="UTC")
    return wanted.intersection(days)


def premise_verdict(blocks: Mapping[str, Mapping[str, Any]]) -> str:
    """TADİLAT-1 > 4: ρ > 0, iki alt sınırın minimumu > 0 VE örtüşmeyen nokta > 0 — A ve B'de."""
    for period in ("A", "B"):
        b = blocks.get(period) or {}
        if not b.get("verdict_inputs_ok") or b.get("binding_low") is None:
            return NOT_EVALUABLE
    for period in ("A", "B"):
        b = blocks[period]
        nonover = (b.get("non_overlapping") or {}).get("rho")
        if not (b["rho"] > 0.0 and b["binding_low"] > 0.0 and nonover is not None and nonover > 0.0):
            return PREMISE_FAILED
    return PREMISE_HELD


def asset_verdict(blocks: Mapping[str, Mapping[str, Any]]) -> str:
    """§6o > 10 + TADİLAT-1 > 3: iki alt sınırın minimumu > 0, A ∧ B."""
    verdicts = [(blocks.get(p) or {}).get("verdict") for p in ("A", "B")]
    if any(v in (None, NOT_EVALUABLE) for v in verdicts):
        return NOT_EVALUABLE
    return PASSED if all(v == PASSED for v in verdicts) else FAILED


def reading(verdict: str, premise: str) -> str:
    return SENTENCES.get((verdict, premise), "okunamaz — bir taraf DEĞERLENDİRİLEMEZ")


def evaluate(closes: pd.DataFrame, *, btc_symbol: str, now: pd.Timestamp, settings: Settings) -> dict[str, Any]:
    eligible = eligibility(closes)
    returns, basket, missing = basket_returns(closes, eligible)
    btc = closes[[btc_symbol]]
    btc_returns = btc[btc_symbol] / btc[btc_symbol].shift(1) - 1.0
    btc_eligible = pd.DataFrame({btc_symbol: pd.Series(True, index=closes.index)})
    series = {
        "btc": (btc_returns.to_frame(btc_symbol), btc_eligible, btc_returns),
        "basket": (returns, eligible, basket),
    }
    weights = {asset: weight_table(single) for asset, (_, _, single) in series.items()}
    result: dict[str, Any] = {"assets": {}}
    for asset, (rets, elig, single) in series.items():
        per: dict[str, Any] = {"periods": {}, "mechanism": {}}
        for period in periods(now):
            per["periods"][period.name] = measure_period(
                asset=asset, period=period, returns=rets, eligible=elig, single_returns=single,
                weights=weights[asset], settings=settings)
            if asset == "basket":
                days = period.days()
                per["periods"][period.name]["missing_closes_zero_return"] = int(
                    missing.reindex(days).fillna(False).to_numpy().sum())
            per["mechanism"][period.name] = measure_mechanism(
                asset=asset, period=period, single_returns=single, weights=weights[asset], settings=settings)
        per["verdict"] = asset_verdict(per["periods"])
        per["premise"] = premise_verdict(per["mechanism"])
        per["reading"] = reading(per["verdict"], per["premise"])
        result["assets"][asset] = per
    verdicts = [result["assets"][a]["verdict"] for a in ASSETS]
    result["general_sentence_allowed"] = all(v == PASSED for v in verdicts)
    days_table = pd.DataFrame({
        "btc_ret": btc_returns, "btc_sigma_hat": weights["btc"]["sigma_hat"],
        "btc_sigma_target": weights["btc"]["sigma_target"], "btc_w": weights["btc"]["w"],
        "basket_ret": basket, "basket_n": eligible.sum(axis=1),
        "basket_sigma_hat": weights["basket"]["sigma_hat"],
        "basket_sigma_target": weights["basket"]["sigma_target"], "basket_w": weights["basket"]["w"],
    })
    return {"result": result, "days_table": days_table}


# --------------------------------------------------------------------------- #
# Veri
# --------------------------------------------------------------------------- #
def fetch_all(config: Mapping[str, Any], symbols: Sequence[str], *, now: pd.Timestamp,
              cache_dir: str) -> dict[str, pd.DataFrame]:
    bars = int(math.ceil((now - DATA_START) / pd.Timedelta(hours=4))) + 12
    run_config = copy.deepcopy(dict(config))
    run_config["data"] = {**run_config["data"], "history_bars": bars, "cache_dir": cache_dir}
    out: dict[str, pd.DataFrame] = {}
    for symbol in symbols:
        frame = fetch_ohlcv(run_config, symbol, now=now)
        out[symbol] = frame[frame.index >= DATA_START] if not frame.empty else frame
    return out


def coverage(bars: Mapping[str, pd.DataFrame], closes: pd.DataFrame) -> dict[str, Any]:
    """Salt KAPSAM: hiçbir getiri/σ̂/ağırlık üretmez."""
    report = {}
    for symbol in closes.columns:
        frame = bars.get(symbol)
        column = closes[symbol]
        present = column.dropna()
        if frame is None or frame.empty or present.empty:
            report[symbol] = {"bars": 0}
            continue
        inner = column.loc[present.index[0]:present.index[-1]]
        report[symbol] = {
            "bars": int(len(frame)),
            "first_bar": str(frame.index[0]),
            "last_bar": str(frame.index[-1]),
            "first_day": str(present.index[0].date()),
            "last_day": str(present.index[-1].date()),
            "missing_days_inside": int(inner.isna().sum()),
        }
    return report


def settings_from(config: Mapping[str, Any]) -> Settings:
    acceptance = get_setting(dict(config), "acceptance")
    return Settings(
        iterations=int(acceptance["bootstrap_samples"]),
        alpha=float(acceptance["edge_ci_alpha"]),
        seed=int(get_setting(dict(config), "random_seed")),
        cost_rate=float(get_setting(dict(config), "fee_rate")) + float(get_setting(dict(config), "slippage_base")),
    )


def run(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    layer = resolve_layer(config, "ema")
    symbols = list(layer.symbols or [])
    btc_symbol = str(get_setting(layer.config, "exchange.btc_reference"))
    if btc_symbol not in symbols:
        raise DataGateError(f"{btc_symbol} ema evreninde yok")
    # B'nin sonu SEÇİLMEZ: koşu anından önce kapanmış son UTC günü (§6o > 9).
    now = _now()
    cache_dir = args.cache_dir or tempfile.mkdtemp(prefix="voltarget-")
    bars = fetch_all(layer.config, symbols, now=now, cache_dir=cache_dir)
    if bars.get(btc_symbol) is None or bars[btc_symbol].empty:
        raise DataGateError("BTC serisi boş")
    closes = closes_frame(bars, symbols)
    period_a, period_b = periods(now)

    if args.stage == "preflight":
        report = {
            "stage": "preflight",
            "now": str(now),
            "data_start": str(DATA_START),
            "periods": {p.name: [str(p.first.date()), str(p.last.date())] for p in (period_a, period_b)},
            "btc_reaches_data_start": bool(bars[btc_symbol].index[0] <= DATA_START + pd.Timedelta(days=1)),
            "coverage": coverage(bars, closes),
        }
        print("=== PREFLIGHT BEGIN ===")
        print(json.dumps(report, indent=2, ensure_ascii=False))
        print("=== PREFLIGHT END ===")
        return 0

    settings = settings_from(layer.config)
    evaluated = evaluate(closes, btc_symbol=btc_symbol, now=now, settings=settings)
    result = evaluated["result"]
    payload = {
        "preregistration": "docs/backtest.md > 6o (5340b33, TADİLAT-1 b7458bf)",
        "parameters": {"vol_days": VOL_DAYS, "target_days": TARGET_DAYS, "weight_cap": WEIGHT_CAP,
                       "days_per_year": DAYS_PER_YEAR, "forward_days": FORWARD_DAYS,
                       "blocks": list(BLOCKS), "block_weeks": BLOCK_WEEKS, "min_clusters": MIN_CLUSTERS,
                       "iterations": settings.iterations, "alpha": settings.alpha, "seed": settings.seed,
                       "cost_rate": settings.cost_rate, "data_start": str(DATA_START)},
        "now": str(now),
        "periods": {p.name: [str(p.first.date()), str(p.last.date())] for p in (period_a, period_b)},
        "universe": symbols,
        "coverage": coverage(bars, closes),
        **result,
        "days_file": "vol_target_days.csv",
        "closes_file": "vol_target_closes.csv",
    }
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / "vol_target.json").write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=_json),
                                         encoding="utf-8")
    evaluated["days_table"].to_csv(out / "vol_target_days.csv", index_label="day")
    closes.to_csv(out / "vol_target_closes.csv", index_label="day")
    print("=== VOL_TARGET.JSON BEGIN ===")
    print(json.dumps(payload, indent=2, ensure_ascii=False, default=_json))
    print("=== VOL_TARGET.JSON END ===")
    unmeasured = [f"{a}/{p}" for a in ASSETS for p, b in result["assets"][a]["periods"].items()
                  if not b.get("measured")]
    if unmeasured:
        logger.error("ölçülemeyen (varlık, dönem): %s", ", ".join(unmeasured))
        return 3
    return 0


def _now() -> pd.Timestamp:
    return pd.Timestamp.now(tz="UTC")


def _json(value: Any) -> Any:
    if isinstance(value, (np.floating, np.integer)):
        return value.item()
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return str(value)


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Oynaklık hedefleme ölçümü (docs/backtest.md > 6o).")
    parser.add_argument("--stage", choices=("preflight", "measure"), required=True,
                        help="preflight: yalnızca kapsam (tekrarlanabilir); measure: TEK SEFER")
    parser.add_argument("--config", default=None)
    parser.add_argument("--cache-dir", default="", help="koşuya özel önbellek (boş = geçici dizin)")
    parser.add_argument("--out-dir", default="backtests/vol_target")
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

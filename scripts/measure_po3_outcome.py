#!/usr/bin/env python3
"""PO3 / AMD Varyant A2 — ön-kayıtlı SONUÇ ölçümü (docs/backtest.md > 6u). SALT OKUNUR.

**MODEL DEĞİL.** Deftere, `config.yaml`a, `strategies/`e ve `data/cache/`e YAZMAZ (mum önbelleği
koşuya özel dizindedir). `strategies/*` ve `core/portfolio|engine|ledger|metrics` import
EDİLMEZ (test): tek bir işlemin R'si burada, §6u > 3'ün tanımıyla hesaplanır.

**Kurulum tanımı İKİNCİ KEZ YAZILMAZ:** gün sınıflandırması, Varyant A2 filtresi ve 4H yapı
etiketi (bozulma kuralı dâhil) `scripts/measure_po3.py`den İTHAL EDİLİR. Küme yüzdelikleri,
bağlayıcı alt sınır, `MIN_CLUSTERS` ve MDE'nin z katsayısı `scripts/backtest_dc.py`den gelir.
İki gruplu (köken başına) EŞİT AĞIRLIKLI çekiliş `backtest_dc`de yoktur; burada aynı RNG
sözleşmesiyle (`random.Random(seed)`, küme başına `randrange`) yazılır.

## İki bacak (§6u > 3)

Her birincil kurulum bir ÇİFT üretir, ikisi de giriş barının AÇILIŞINDAN girer:
- **PO3**: süpürmenin tersi; stop süpürme ucu (mesafe `d_s`), hedef Asya aralığının karşı
  tarafı (mesafe `d_t`).
- **devam**: süpürmenin yönü; stop ve hedef girişin öbür tarafına YANSITILIR (aynı mesafeler).

Yol giriş barı DÂHİL her 15m barında `high`/`low` ile yürünür; son bar aynı günün 23:45 barıdır
ve zaman çıkışı onun KAPANIŞINDADIR (O2). Kural 13: aynı barda stop ve hedef → STOP. Stop dolumu
`core/portfolio.py`nin sözleşmesiyle: referans stop ile barın açılışının ALEYHTE olanı; hedef
seviyeden (lehte boşluk yazılmaz). Kayma aleyhe: giriş ve her çıkış `slippage_base`, short'un
stop çıkışı `slippage_short_stop`; komisyon her dolumda `fee_rate × fiyat`. Fonlama HARİÇ (S2).

`R = işaretli net PnL / |giriş − stop|` (birim başına, kaymasız referans giriş ve stop).
Brüt R kayma ve komisyonsuzdur. Yol barları kesintisiz değilse kurulum ÖLÇÜLEMEZ ve iki
bacak birlikte düşer (sayılır).

## Aşamalar ve çıkış kodları (§6u > 10)

- `preflight`: dönem A'nın kapsamı, köken başına kurulum ve küme SAYILARI, yolu kesintisiz
  olmayan kurulum sayısı (yalnızca zaman damgası). Fiyat yolu, R ya da getiri ÜRETMEZ (test).
- `measure`: TEK SEFER. A ölçülür; A GEÇERSE B'nin mumları çekilir ve B ölçülür.
- `0` ölçüm yazıldı · `2` kullanım hatası · `3` veri kapısı (hiçbir kurulum ölçülemedi).
"""

from __future__ import annotations

import argparse
import copy
import dataclasses
import json
import logging
import math
import random
import statistics
import sys
import tempfile
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from core.config import get_setting, load_config  # noqa: E402
from core.layers import resolve_layer  # noqa: E402
from scripts.backtest_dc import MIN_CLUSTERS, _percentiles, _z  # noqa: E402
from scripts.backtest_ema import PERIOD_A_CUTOFF, PERIOD_A_START  # noqa: E402
from scripts.measure_po3 import (  # noqa: E402
    BAR,
    LAYER,
    TIMEFRAME,
    VARIANT_A2_MIN_STOP,
    Setup,
    SymbolScan,
    load_frames,
    scan_symbol,
)
from scripts.vault import KASA_START, assert_before_vault  # noqa: E402

logger = logging.getLogger("measure-po3-outcome")

# ÖN-KAYITLI SABİTLER (§6u) — girdi DEĞİLDİR.
PERIOD_B_START = "2024-07-01T00:00:00+00:00"
EDGE_MARGIN_HALF_R = 0.15      # E: ½·ΔR ≥ 0.15R
ALPHA = 0.05
ITERATIONS = 10_000
CLUSTER_DEFINITIONS = ("day", "week")
ORIGINS = ("U", "L")           # U: üst süpürme (PO3 short), L: alt süpürme (PO3 long)
LAST_BAR = pd.Timedelta(hours=23, minutes=45)
WARMUP = pd.Timedelta(days=30)  # 4H yapı etiketinin ısınması; ölçüme GİRMEZ
LEGS = ("po3", "cont")


# --------------------------------------------------------------------------- #
# Bacak simülasyonu
# --------------------------------------------------------------------------- #
@dataclass(frozen=True, kw_only=True)
class Costs:
    fee: float
    slip: float
    short_stop_slip: float


def costs_of(config: Mapping[str, Any]) -> Costs:
    return Costs(
        fee=float(get_setting(config, "fee_rate")),
        slip=float(get_setting(config, "slippage_base")),
        short_stop_slip=float(get_setting(config, "slippage_short_stop")),
    )


@dataclass(frozen=True, kw_only=True)
class Leg:
    direction: str
    r_net: float
    r_gross: float
    exit_reason: str      # target | stop | time
    exit_bar: pd.Timestamp


def simulate_leg(path: pd.DataFrame, *, direction: str, entry: float, stop: float, target: float,
                 costs: Costs) -> Leg:
    """`path`: giriş barından 23:45 barına kadar (dâhil) kesintisiz 15m barları."""
    long = direction == "long"
    risk = abs(entry - stop)
    exit_ref = float(path["close"].iloc[-1])
    reason = "time"
    exit_bar = path.index[-1]
    for ts, bar in zip(path.index, path.itertuples(index=False)):
        hit_stop = bar.low <= stop if long else bar.high >= stop
        if hit_stop:  # kural 13: aynı barda hedef de olsa STOP
            exit_ref = min(stop, bar.open) if long else max(stop, bar.open)
            reason, exit_bar = "stop", ts
            break
        if (bar.high >= target) if long else (bar.low <= target):
            exit_ref, reason, exit_bar = target, "target", ts
            break
    sign = 1.0 if long else -1.0
    exit_slip = costs.short_stop_slip if (not long and reason == "stop") else costs.slip
    entry_fill = entry * (1.0 + sign * costs.slip)
    exit_fill = exit_ref * (1.0 - sign * exit_slip)
    pnl = sign * (exit_fill - entry_fill) - costs.fee * (entry_fill + exit_fill)
    return Leg(
        direction=direction,
        r_net=pnl / risk,
        r_gross=sign * (exit_ref - entry) / risk,
        exit_reason=reason,
        exit_bar=exit_bar,
    )


@dataclass(frozen=True, kw_only=True)
class Pair:
    setup: Setup
    po3: Leg
    cont: Leg

    @property
    def origin(self) -> str:
        return "U" if self.setup.side == "up" else "L"

    @property
    def d_net(self) -> float:
        return self.po3.r_net - self.cont.r_net

    @property
    def d_gross(self) -> float:
        return self.po3.r_gross - self.cont.r_gross

    @property
    def both_stopped_same_bar(self) -> bool:
        return (self.po3.exit_reason == "stop" and self.cont.exit_reason == "stop"
                and self.po3.exit_bar == self.cont.exit_bar)


def leg_path(frame: pd.DataFrame, setup: Setup) -> pd.DataFrame | None:
    """Giriş barından aynı günün 23:45 barına kadar; kesintisiz değilse None."""
    last = setup.day + LAST_BAR
    path = frame.loc[(frame.index >= setup.entry_bar) & (frame.index <= last)]
    expected = int((last - setup.entry_bar) / BAR) + 1
    if len(path) != expected or path.index[0] != setup.entry_bar or path.index[-1] != last:
        return None
    return path


def evaluate_setup(frame: pd.DataFrame, setup: Setup, costs: Costs) -> Pair | None:
    path = leg_path(frame, setup)
    if path is None:
        return None
    entry = setup.entry
    d_s = abs(entry - setup.extreme)
    d_t = abs(setup.target - entry)
    po3_dir = setup.direction
    cont_dir = "long" if po3_dir == "short" else "short"
    sign = 1.0 if cont_dir == "long" else -1.0
    po3 = simulate_leg(path, direction=po3_dir, entry=entry, stop=setup.extreme, target=setup.target, costs=costs)
    cont = simulate_leg(path, direction=cont_dir, entry=entry, stop=entry - sign * d_s,
                        target=entry + sign * d_t, costs=costs)
    return Pair(setup=setup, po3=po3, cont=cont)


# --------------------------------------------------------------------------- #
# İstatistik — köken başına EŞİT AĞIRLIK, çekilişin İÇİNDE
# --------------------------------------------------------------------------- #
def cluster_key(setup: Setup, definition: str) -> str:
    if definition == "day":
        return setup.day.strftime("%Y-%m-%d")
    iso = setup.day.isocalendar()
    return f"{iso.year}-W{iso.week:02d}"


Metric = Callable[[Pair], float]


def _cluster_table(pairs: Sequence[Pair], metric: Metric, definition: str
                   ) -> tuple[list[str], dict[str, list[float]], dict[str, list[int]]]:
    ids = sorted({cluster_key(p.setup, definition) for p in pairs})
    pos = {cid: k for k, cid in enumerate(ids)}
    sums = {o: [0.0] * len(ids) for o in ORIGINS}
    counts = {o: [0] * len(ids) for o in ORIGINS}
    for p in pairs:
        k = pos[cluster_key(p.setup, definition)]
        sums[p.origin][k] += metric(p)
        counts[p.origin][k] += 1
    return ids, sums, counts


def equal_weight(pairs: Sequence[Pair], metric: Metric) -> float | None:
    means = []
    for origin in ORIGINS:
        values = [metric(p) for p in pairs if p.origin == origin]
        if not values:
            return None
        means.append(sum(values) / len(values))
    return 0.5 * (means[0] + means[1])


def pooled(pairs: Sequence[Pair], metric: Metric) -> float | None:
    return sum(metric(p) for p in pairs) / len(pairs) if pairs else None


def equal_weight_draws(pairs: Sequence[Pair], metric: Metric, *, definition: str, iterations: int,
                       seed: str) -> tuple[list[float], int]:
    """Kümeler BİRLİKTE çekilir; her çekilişte iki kökenin ortalaması, sonra eşit ağırlık.

    Bir kökeni boş kalan çekiliş atılır ve SAYILIR (`backtest_dc.cluster_diff_draws`in kuralı).
    """
    ids, sums, counts = _cluster_table(pairs, metric, definition)
    rng = random.Random(seed)
    draws: list[float] = []
    dropped = attempts = 0
    while len(draws) < iterations and attempts < 4 * iterations:
        attempts += 1
        su = nu = sl = nl = 0.0
        for _ in ids:
            k = rng.randrange(len(ids))
            su += sums["U"][k]
            nu += counts["U"][k]
            sl += sums["L"][k]
            nl += counts["L"][k]
        if nu == 0 or nl == 0:
            dropped += 1
            continue
        draws.append(0.5 * (su / nu + sl / nl))
    return draws, dropped


def origin_clusters(pairs: Sequence[Pair], definition: str) -> dict[str, int]:
    return {o: len({cluster_key(p.setup, definition) for p in pairs if p.origin == o}) for o in ORIGINS}


def interval(pairs: Sequence[Pair], metric: Metric, *, definition: str, seed: str) -> dict[str, Any]:
    clusters = origin_clusters(pairs, definition)
    evaluable = all(n >= MIN_CLUSTERS for n in clusters.values())
    out: dict[str, Any] = {"definition": definition, "clusters": clusters, "evaluable": evaluable,
                           "low": None, "high": None, "dropped_draws": 0}
    if not all(clusters.values()):
        out["evaluable"] = False
        return out
    draws, dropped = equal_weight_draws(pairs, metric, definition=definition, iterations=ITERATIONS, seed=seed)
    out["dropped_draws"] = dropped
    if len(draws) < ITERATIONS:
        out["evaluable"] = False
        return out
    out["low"], out["high"] = _percentiles(draws, ALPHA)
    out["se_bootstrap"] = statistics.stdev(draws)
    return out


def binding_low(intervals: Sequence[Mapping[str, Any]]) -> float | None:
    if not intervals or not all(ci["evaluable"] and ci["low"] is not None for ci in intervals):
        return None
    return min(ci["low"] for ci in intervals)


def weighted_precision(pairs: Sequence[Pair], metric: Metric, definition: str) -> dict[str, Any]:
    """Eşit ağırlıklı tahmincinin kesinliği: SE_küme (doğrusallaştırma), SE_iid, DEFF, n_etkin, MDE."""
    groups = {o: [p for p in pairs if p.origin == o] for o in ORIGINS}
    if any(len(g) < 2 for g in groups.values()):
        return {"evaluable": False}
    means = {o: sum(metric(p) for p in g) / len(g) for o, g in groups.items()}
    contrib: dict[str, float] = defaultdict(float)
    for o, g in groups.items():
        for p in g:
            contrib[cluster_key(p.setup, definition)] += 0.5 * (metric(p) - means[o]) / len(g)
    se_cluster = math.sqrt(sum(c * c for c in contrib.values()))
    se_iid = 0.5 * math.sqrt(sum(statistics.variance([metric(p) for p in g]) / len(g) for g in groups.values()))
    deff = (se_cluster / se_iid) ** 2 if se_iid > 0 else float("nan")
    n = len(pairs)
    return {
        "definition": definition,
        "n": n,
        "clusters": len(contrib),
        "se_iid": se_iid,
        "se_cluster": se_cluster,
        "deff": deff,
        "n_effective": n / deff if deff and deff == deff else float("nan"),
        "mde": _z() * se_cluster,
        "evaluable": all(v >= MIN_CLUSTERS for v in origin_clusters(pairs, definition).values()),
    }


# --------------------------------------------------------------------------- #
# Kapılar (§6u > 4)
# --------------------------------------------------------------------------- #
def r_po3(p: Pair) -> float:
    return p.po3.r_net


def d_net(p: Pair) -> float:
    return p.d_net


def d_gross(p: Pair) -> float:
    return p.d_gross


def evaluate_period(pairs: Sequence[Pair], *, seed_base: str) -> dict[str, Any]:
    c1_ci = [interval(pairs, r_po3, definition=d, seed=f"{seed_base}:c1:{d}") for d in CLUSTER_DEFINITIONS]
    e_ci = [interval(pairs, d_net, definition=d, seed=f"{seed_base}:e:{d}") for d in CLUSTER_DEFINITIONS]
    c1_mean = equal_weight(pairs, r_po3)
    delta = equal_weight(pairs, d_net)
    c1_low = binding_low(c1_ci)
    e_low = binding_low(e_ci)
    c1 = c1_mean is not None and c1_mean > 0 and c1_low is not None and c1_low > 0
    e = delta is not None and 0.5 * delta >= EDGE_MARGIN_HALF_R and e_low is not None and e_low > 0
    evaluable = c1_low is not None and e_low is not None
    precision = {d: weighted_precision(pairs, d_net, d) for d in CLUSTER_DEFINITIONS}
    mdes = [precision[d].get("mde") for d in CLUSTER_DEFINITIONS]
    return {
        "n": len(pairs),
        "n_by_origin": {o: sum(1 for p in pairs if p.origin == o) for o in ORIGINS},
        "c1": {"mean_equal_weight": c1_mean, "intervals": c1_ci, "binding_low": c1_low, "passed": c1},
        "e": {"delta_equal_weight": delta, "half_delta": None if delta is None else 0.5 * delta,
              "margin_half_r": EDGE_MARGIN_HALF_R, "intervals": e_ci, "binding_low": e_low, "passed": e},
        "evaluable": evaluable,
        "passed": bool(evaluable and c1 and e),
        "precision_delta": precision,
        "mde_delta_binding": max((m for m in mdes if isinstance(m, float)), default=None),
        "descriptive": describe(pairs),
    }


# --------------------------------------------------------------------------- #
# Betimsel (§6u > 7) — kapı DEĞİL
# --------------------------------------------------------------------------- #
def _mean(values: Sequence[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _subset(pairs: Sequence[Pair], key: Callable[[Pair], Any]) -> dict[str, dict[str, Any]]:
    groups: dict[str, list[Pair]] = defaultdict(list)
    for p in pairs:
        groups[str(key(p))].append(p)
    return {
        name: {
            "n": len(g),
            "days": len({p.setup.day for p in g}),
            "delta_pooled": _mean([p.d_net for p in g]),
            "delta_equal_weight": equal_weight(g, d_net),
            "r_po3_pooled": _mean([p.po3.r_net for p in g]),
            "r_po3_equal_weight": equal_weight(g, r_po3),
        }
        for name, g in sorted(groups.items())
    }


def describe(pairs: Sequence[Pair]) -> dict[str, Any]:
    by_origin = {}
    for o in ORIGINS:
        g = [p for p in pairs if p.origin == o]
        by_origin[o] = {
            "n": len(g),
            "po3_direction": "short" if o == "U" else "long",
            "delta": _mean([p.d_net for p in g]),
            "delta_gross": _mean([p.d_gross for p in g]),
            "r_po3": _mean([p.po3.r_net for p in g]),
            "r_cont": _mean([p.cont.r_net for p in g]),
            "median_stop_distance": float(np.median([p.setup.stop_distance for p in g])) if g else None,
            "exit_reasons": {
                leg: dict(Counter(getattr(p, leg).exit_reason for p in g)) for leg in LEGS
            },
        }
    both = sum(1 for p in pairs if p.both_stopped_same_bar)
    return {
        "delta_gross_equal_weight": equal_weight(pairs, d_gross),
        "delta_pooled": pooled(pairs, d_net),
        "delta_gross_pooled": pooled(pairs, d_gross),
        "r_po3_pooled": pooled(pairs, r_po3),
        "r_cont_equal_weight": equal_weight(pairs, lambda p: p.cont.r_net),
        "by_origin": by_origin,
        "both_stopped_same_bar": {"n": both, "share": both / len(pairs) if pairs else None},
        "reversal_bar": _subset(pairs, lambda p: "same" if p.setup.reversal_bar == p.setup.sweep_bar else "later"),
        "structure_alignment": _subset(pairs, lambda p: p.setup.alignment),
        "year": _subset(pairs, lambda p: p.setup.day.year),
        "symbol": _subset(pairs, lambda p: p.setup.symbol),
    }


# --------------------------------------------------------------------------- #
# Tarama
# --------------------------------------------------------------------------- #
@dataclass(frozen=True, kw_only=True)
class PeriodScan:
    name: str
    start: pd.Timestamp
    end: pd.Timestamp
    scans: tuple[SymbolScan, ...]
    frames: Mapping[str, pd.DataFrame]

    @property
    def primary(self) -> list[Setup]:
        return [s for scan in self.scans for s in scan.primary]


def scan_period(config: Mapping[str, Any], symbols: Sequence[str], *, name: str, start: pd.Timestamp,
                end: pd.Timestamp, cache_dir: str) -> PeriodScan:
    """Mumlar `now = end` ile çekilir; ısınma yalnızca yapı etiketi içindir."""
    assert_before_vault(end, what=f"dönem {name} sonu")
    run_config = copy.deepcopy(dict(config))
    run_config["timeframe"] = TIMEFRAME
    run_config["data"] = {
        **run_config["data"],
        "history_bars": int(math.ceil((end - start + WARMUP) / BAR)) + 96,
        "cache_dir": cache_dir,
    }
    loaded = load_frames(run_config, symbols, start=start, end=end)
    scans, frames = [], {}
    for symbol in symbols:
        frame = loaded[symbol]
        if isinstance(frame, Exception):
            scans.append(SymbolScan(symbol=symbol, skipped=f"veri çekilemedi: {frame}", min_stop=VARIANT_A2_MIN_STOP))
            continue
        frames[symbol] = frame
        scans.append(scan_symbol(symbol, frame, start=start, end=end, min_stop=VARIANT_A2_MIN_STOP))
    return PeriodScan(name=name, start=start, end=end, scans=tuple(scans), frames=frames)


def preflight_report(period: PeriodScan) -> dict[str, Any]:
    """Yalnızca SAYIM ve zaman damgası — fiyat yolu, R, getiri YOK (test)."""
    primary = period.primary
    measurable = [s for s in primary if leg_path_index_ok(period.frames[s.symbol].index, s)]
    return {
        "period": period.name,
        "start": period.start.isoformat(),
        "end": period.end.isoformat(),
        "symbols": {scan.symbol: {"primary": len(scan.primary), "skipped": scan.skipped} for scan in period.scans},
        "primary": len(primary),
        "measurable": len(measurable),
        "by_origin": {o: sum(1 for s in measurable if (s.side == "up") == (o == "U")) for o in ORIGINS},
        "clusters": {
            d: {o: len({cluster_key(s, d) for s in measurable if (s.side == "up") == (o == "U")}) for o in ORIGINS}
            for d in CLUSTER_DEFINITIONS
        },
    }


def leg_path_index_ok(index: pd.DatetimeIndex, setup: Setup) -> bool:
    last = setup.day + LAST_BAR
    stamps = index[(index >= setup.entry_bar) & (index <= last)]
    expected = int((last - setup.entry_bar) / BAR) + 1
    return len(stamps) == expected and len(stamps) > 0 and stamps[0] == setup.entry_bar and stamps[-1] == last


def measure_period(period: PeriodScan, costs: Costs, *, seed: int) -> tuple[list[Pair], dict[str, Any]]:
    pairs: list[Pair] = []
    unmeasurable = 0
    for setup in period.primary:
        pair = evaluate_setup(period.frames[setup.symbol], setup, costs)
        if pair is None:
            unmeasurable += 1
        else:
            pairs.append(pair)
    result = evaluate_period(pairs, seed_base=f"{seed}:po3-6u:{period.name}") if pairs else {"n": 0, "passed": False,
                                                                                             "evaluable": False}
    result.update({"period": period.name, "start": period.start.isoformat(), "end": period.end.isoformat(),
                   "primary": len(period.primary), "unmeasurable": unmeasurable})
    return pairs, result


# --------------------------------------------------------------------------- #
# Rapor
# --------------------------------------------------------------------------- #
def _f(value: Any, digits: int = 3) -> str:
    return "—" if value is None or (isinstance(value, float) and not math.isfinite(value)) else f"{value:+.{digits}f}"


def format_period(result: Mapping[str, Any]) -> str:
    lines = [f"--- DÖNEM {result['period']}: {result['start'][:10]} → {result['end'][:10]} ---",
             f"birincil {result['primary']}, ölçülen {result['n']}, ölçülemeyen {result['unmeasurable']}"]
    if not result.get("n"):
        return "\n".join(lines + ["ölçülen kurulum yok"])
    lines.append(f"köken: U (PO3 short) {result['n_by_origin']['U']}, L (PO3 long) {result['n_by_origin']['L']}")
    c1, e = result["c1"], result["e"]
    for label, block, value in (("C-1 R̄_PO3 (eşit ağ.)", c1, c1["mean_equal_weight"]),
                                ("E   ΔR (eşit ağ.)", e, e["delta_equal_weight"])):
        cis = ", ".join(
            f"{ci['definition']} [{_f(ci['low'])}, {_f(ci['high'])}] küme U/L {ci['clusters']['U']}/{ci['clusters']['L']}"
            + ("" if ci["evaluable"] else " DEĞERLENDİRİLEMEZ")
            for ci in block["intervals"]
        )
        lines.append(f"{label}: {_f(value)}  bağlayıcı alt {_f(block['binding_low'])}  ({cis})")
    lines.append(f"½·ΔR {_f(e['half_delta'])} (marj ≥ {EDGE_MARGIN_HALF_R})")
    lines.append(f"[{'GEÇTİ' if c1['passed'] else 'KALDI'}] C-1   [{'GEÇTİ' if e['passed'] else 'KALDI'}] E"
                 f"   → {'GEÇTİ' if result['passed'] else ('KALDI' if result['evaluable'] else 'DEĞERLENDİRİLEMEZ')}")
    for d, prec in result["precision_delta"].items():
        if prec.get("evaluable") is not None and "mde" in prec:
            lines.append(f"kesinlik ΔR [{d}]: SE_küme {prec['se_cluster']:.3f}, DEFF {prec['deff']:.2f},"
                         f" n_etkin {prec['n_effective']:.0f}, MDE {prec['mde']:.3f} (½·ΔR ölçeğinde {prec['mde'] / 2:.3f})")
    desc = result["descriptive"]
    lines.append("BETİMSEL (kapı değil):")
    lines.append(f"  brüt ΔR eşit ağ. {_f(desc['delta_gross_equal_weight'])} | net ΔR havuz {_f(desc['delta_pooled'])}"
                 f" | brüt ΔR havuz {_f(desc['delta_gross_pooled'])} | R̄_PO3 havuz {_f(desc['r_po3_pooled'])}"
                 f" | R̄_devam eşit ağ. {_f(desc['r_cont_equal_weight'])}")
    for o, b in desc["by_origin"].items():
        lines.append(f"  köken {o} (PO3 {b['po3_direction']}) n={b['n']}: ΔR {_f(b['delta'])} (brüt {_f(b['delta_gross'])}),"
                     f" R̄_PO3 {_f(b['r_po3'])}, R̄_devam {_f(b['r_cont'])}, medyan stop {100 * (b['median_stop_distance'] or 0):.2f}%")
        for leg in LEGS:
            lines.append(f"      çıkış {leg}: {b['exit_reasons'][leg]}")
    both = desc["both_stopped_same_bar"]
    lines.append(f"  kural 13 — iki bacak AYNI barda stop: {both['n']} ({100 * (both['share'] or 0):.1f}%)")
    for title, key in (("geri dönüş barı", "reversal_bar"), ("4H yapı hizası", "structure_alignment"),
                       ("yıl", "year"), ("sembol", "symbol")):
        lines.append(f"  {title}:")
        for name, g in desc[key].items():
            lines.append(f"      {name:<18} n={g['n']:>4} gün={g['days']:>4}  ΔR havuz {_f(g['delta_pooled'])}"
                         f"  eşit ağ. {_f(g['delta_equal_weight'])}  R̄_PO3 havuz {_f(g['r_po3_pooled'])}")
    return "\n".join(lines)


def pairs_frame(pairs: Sequence[Pair], period: str) -> pd.DataFrame:
    return pd.DataFrame([{
        "period": period, "symbol": p.setup.symbol, "day": p.setup.day.strftime("%Y-%m-%d"), "origin": p.origin,
        "entry_bar": p.setup.entry_bar.isoformat(), "entry": p.setup.entry, "extreme": p.setup.extreme,
        "target": p.setup.target, "stop_distance": p.setup.stop_distance, "reward_risk": p.setup.reward_risk,
        "same_bar": p.setup.reversal_bar == p.setup.sweep_bar, "alignment": p.setup.alignment,
        "po3_r_net": p.po3.r_net, "po3_r_gross": p.po3.r_gross, "po3_exit": p.po3.exit_reason,
        "cont_r_net": p.cont.r_net, "cont_r_gross": p.cont.r_gross, "cont_exit": p.cont.exit_reason,
    } for p in pairs])


def _json_default(value: Any) -> Any:
    if isinstance(value, (np.floating, np.integer)):
        return value.item()
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    raise TypeError(type(value))


# --------------------------------------------------------------------------- #
# Giriş noktası
# --------------------------------------------------------------------------- #
def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    config = load_config(args.config)
    layer = resolve_layer(config, LAYER)
    symbols = list(layer.symbols or [])
    if not symbols:
        logger.error("evren yok")
        return 2
    costs = costs_of(layer.config)
    seed = int(get_setting(layer.config, "random_seed"))
    cache_dir = args.cache_dir or tempfile.mkdtemp(prefix="po3o-")
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    a_start, a_end = pd.Timestamp(PERIOD_A_START), pd.Timestamp(PERIOD_A_CUTOFF)
    period_a = scan_period(layer.config, symbols, name="A", start=a_start, end=a_end, cache_dir=cache_dir)

    if args.stage == "preflight":
        report = preflight_report(period_a)
        print(json.dumps(report, indent=2, ensure_ascii=False))
        (out_dir / "po3_outcome_preflight.json").write_text(json.dumps(report, indent=2, ensure_ascii=False))
        return 0 if report["measurable"] else 3

    pairs_a, result_a = measure_period(period_a, costs, seed=seed)
    if not pairs_a:
        logger.error("dönem A'da hiçbir kurulum ölçülemedi — veri kapısı")
        return 3
    print()
    print(format_period(result_a))
    results = {"A": result_a}
    frames = [pairs_frame(pairs_a, "A")]

    if result_a["passed"]:
        b_start, b_end = pd.Timestamp(PERIOD_B_START), KASA_START
        period_b = scan_period(layer.config, symbols, name="B", start=b_start, end=b_end, cache_dir=cache_dir)
        pairs_b, result_b = measure_period(period_b, costs, seed=seed)
        print()
        print(format_period(result_b))
        results["B"] = result_b
        frames.append(pairs_frame(pairs_b, "B"))
        passed = result_b["passed"]
        verdict = "GEÇTİ — A ∧ B" if passed else "KALDI — B'de doğrulanmadı"
    else:
        passed = False
        verdict = "KALDI — dönem A'da (B KOŞULMADI)" if result_a["evaluable"] else "DEĞERLENDİRİLEMEZ — dönem A"
    print()
    print(f"=== KARAR (§6u): {verdict} ===")

    payload = {
        "preregistration": "docs/backtest.md > 6u",
        "variant": "A2",
        "min_stop": dict(VARIANT_A2_MIN_STOP),
        "costs": dataclasses.asdict(costs),
        "iterations": ITERATIONS,
        "alpha": ALPHA,
        "periods": results,
        "passed": passed,
        "verdict": verdict,
    }
    (out_dir / "po3_outcome.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False, default=_json_default), encoding="utf-8")
    pd.concat(frames).to_csv(out_dir / "po3_outcome_pairs.csv", index=False)
    logger.info("yük yazıldı: %s", out_dir)
    return 0


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="PO3 Varyant A2 sonuç ölçümü (§6u; salt okunur, model değil).")
    parser.add_argument("--stage", choices=["preflight", "measure"], required=True)
    parser.add_argument("--config", default=None)
    parser.add_argument("--cache-dir", default=None)
    parser.add_argument("--out-dir", default="backtests/po3_outcome")
    return parser.parse_args(argv)


if __name__ == "__main__":
    raise SystemExit(main())

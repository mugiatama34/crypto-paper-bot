#!/usr/bin/env python3
"""`vwap_managed` zaman stop'u varyantı — ön-kayıtlı ölçüm (docs/backtest.md > 6x). SALT OKUNUR.

**Canlı model DEĞİŞMEZ.** Deftere, `config.yaml`a, `strategies/`e ve `REGISTRY`e YAZMAZ;
backtest defterleri koşuya özel dizindedir (`--out-dir`).

**İkinci motor YOK** (§6x > 2). İki adım:

1. **Giriş kümesi P:** mevcut modelin (16 bar) PORTFÖY koşusu, `scripts/backtest.py::
   run_backtest` ile. P = o koşunun DOLAN pozisyonları.
2. **Yeniden oynatma:** P'nin her pozisyonu, motorun KENDİSİYLE (`core/engine.py` +
   `core/portfolio.py`) tek pozisyonluk izole bir hesapta iki kez koşulur — `scalp.
   time_stop_bars` 16 ve 32. Model `main.py::build_strategies` ile kurulur (canlıdaki
   kurulumun aynısı) ve yalnızca sinyal barında sinyal üretmesine izin verilir; o barın
   anlık görüntüsü yalnızca pozisyonun sembolünü taşır, yani model aynı adayı aynı
   geometriyle yeniden kurar. Fiyat yolu, stop hareketi, kısmi dolum, kural 13 ve
   likidasyon motorun kodudur.

**Parite kapısı (ön-kontrol, TADİLAT-1 > 4):** iki ayrı sayım. *Oynatılamayan* (gereken bir
bar eksik) pozisyon iki koldan da çıkarılır; pay ≤ %1 tolere edilir. *Uyuşmazlık* (oynatıldı,
R portföyden farklı — göreli 1e-6 sayısal hassasiyetin ötesinde — ya da başka bir pozisyon
açıldı) için tolerans YOKTUR: tek biri dönemi durdurur.
**Sağlama:** 16 barlık kolda zaman stop'uyla KAPANMAYAN her pozisyonda iki kol birebir
aynıdır (`ΔR = 0` TAM). Tek ihlal → alet hatası.

## Kapılar (§6x > 3, TADİLAT-1)

- ΔR: `ΔR̄ > 0` ∧ küme bootstrap bağlayıcı alt sınırı > 0.
- C-1: varyantın `R̄(32) > 0` ∧ küme bootstrap bağlayıcı alt sınırı > 0.
- Küme: takvim günü (UTC) ve ISO hafta, `opened_at`tan; bağlayıcı alt sınır KÜÇÜĞÜ;
  < 10 küme → değerlendirilemez. Çekilişler `scripts/backtest_dc.py`den.
- **B yalnızca A'da İKİ kapı birlikte geçerse koşar; aksi hâlde B'nin verisi ÇEKİLMEZ.**

## Aşamalar ve çıkış kodları (§6x > 11, TADİLAT-1)

- `preflight`: YALNIZCA dönem A — kapsam, P'nin büyüklüğü, küme SAYILARI, parite ve sağlama
  ihlal SAYILARI, koşu süresi. Hiçbir ΔR, R ortalaması, MFE ya da çıkış dağılımı ÜRETMEZ (test).
- `measure`: TEK SEFER, YALNIZCA dönem A (TADİLAT-1 > 9). Yük `vwap_time_stop_a.json` ve
  onun SHA256'sı yazılır; workflow ikisini `docs/data/`a pin'ler.
- `measure-b`: pin'lenmiş A yükünü okur ve hash'ini doğrular; ΔR kapısı ∧ C-1 A'da birlikte
  geçmediyse (programatik, `b_decision`) hiçbir veri çekmeden "B koşulmadı" yazar.
- `0` rapor yazıldı · `2` kullanım hatası · `3` veri/alet kapısı (hash tutmazsa da).
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import logging
import math
import sys
import tempfile
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd  # noqa: E402

from core.config import PROJECT_ROOT, get_setting, load_config  # noqa: E402
from core.data import bar_duration, load_market_data  # noqa: E402
from core.engine import Engine  # noqa: E402
from core.layers import resolve_layer  # noqa: E402
from core.ledger import Ledger  # noqa: E402
from core.metrics import (  # noqa: E402
    _percentile, exit_rule_of, merge_fills, r_multiple,
)
from core.portfolio import Portfolio  # noqa: E402
from main import build_strategies  # noqa: E402
from scripts.backtest import WindowCoverageError, run_backtest  # noqa: E402
from scripts.backtest_dc import (  # noqa: E402
    MIN_CLUSTERS, ClusterCI, binding_low, cluster_mean_ci, cluster_mean_draws, precision,
)
from scripts.backtest_ema import PERIOD_A_CUTOFF, PERIOD_A_START  # noqa: E402
from scripts.diagnose_ema_exits import buckets, distribution, position_paths  # noqa: E402
from scripts.vault import assert_before_vault  # noqa: E402
from strategies.base import MarketData, Signal, Strategy  # noqa: E402
from strategies.time_stop import CONFIG_KEY as TIME_STOP_KEY, EXIT_RULE as TIME_STOP_RULE  # noqa: E402

logger = logging.getLogger("measure-vwap-time-stop")

# --------------------------------------------------------------------------- #
# ÖN-KAYITLI SABİTLER (§6x) — girdi DEĞİLDİR
# --------------------------------------------------------------------------- #
LAYER = "scalp"
MODEL = "vwap_managed"
BASE_BARS = 16
VARIANT_BARS = 32
ARMS: tuple[int, int] = (BASE_BARS, VARIANT_BARS)

PERIOD_B_START = "2024-07-01T00:00:00+00:00"
# B'nin sonu kasa DEĞİL: 2026-07-19 → kasa bu model için görülmüş veri (§6x > 5).
PERIOD_B_CUTOFF = "2026-07-19T00:00:00+00:00"
# Kesimden sonra pozisyonların kapanması için işlenen kuyruk: yeni sinyal YOK, yalnızca
# kesimden önce açılmış pozisyonlar kapanır. Varyantın azami tutuşu 33 bardır (~8.25 saat);
# 12 saat yeter ve B'nin kuyruğunun görülmüş pencereye (2026-07-19 →) taşmasını en aza indirir.
TAIL = pd.Timedelta("12h")

CLUSTER_DEFINITIONS: tuple[str, ...] = ("day", "week")
ALPHA = 0.05
ITERATIONS = 10_000
# Parite iki AYRI olguyu sayar (TADİLAT-1 > 4): yeniden OYNATILAMAYAN (veri eksik) pozisyon
# ≤ %1 tolere edilir ve iki koldan da çıkarılır; oynatılıp R'si FARKLI çıkan pozisyonda
# tolerans YOKTUR — tek bir uyuşmazlık ölçümü durdurur. `PARITY_RTOL` bir tolerans değil
# sayısal hassasiyettir (defter CSV'si kayan noktayı ~1e-9 göreli ile yuvarlar).
PARITY_RTOL = 1e-6
UNREPLAYABLE_MAX_SHARE = 0.01
# Yeniden oynatma penceresi: sinyal barından ÖNCE görülen bar sayısı. Modelin göstergeleri
# gün-çapalı VWAP (≤ 96 bar) ve ATR(14)'tür; fazlası sinyali değiştirmez — değiştirseydi
# parite kapısı yakalardı.
REPLAY_LOOKBACK_BARS = 200
# Sinyal barından sonra işlenen bar sayısı: dolum (1) + azami tutuş + kapanış dolumu + pay.
REPLAY_TAIL_BARS = VARIANT_BARS + 6
# MFE kovaları (R): breakeven 1R ve kısmi 1.5R'ye hizalı (§6x > 7).
MFE_EDGES: tuple[float, ...] = (0.5, 1.0, 1.5)

# Pin'lenmiş A yükü (TADİLAT-1 > 9): `measure` yazar, workflow commit eder, `measure-b` okur.
PINNED_A = PROJECT_ROOT / "docs" / "data" / "vwap_time_stop_a.json"
PINNED_A_SHA = PROJECT_ROOT / "docs" / "data" / "vwap_time_stop_a.json.sha256"
STAGE_FILES = {
    "preflight": "vwap_time_stop_preflight",
    "measure": "vwap_time_stop_a",
    "measure-b": "vwap_time_stop_b",
}

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_GATE = 3


class GateError(RuntimeError):
    """Veri/alet kapısı: dönem ölçülmez (çıkış 3)."""


def replay_data_gap(
    position: "PortfolioPosition", *, market: MarketData, step: pd.Timedelta
) -> str | None:
    """Yeniden oynatmanın İHTİYAÇ DUYDUĞU barlardan eksik olan varsa sebebi, yoksa None.

    Gereken: sembolün ve çıpanın sinyal barı ve dolumdan varyantın kapanış dolumuna kadar
    (`opened_at` … `opened_at + (VARIANT_BARS + 1) × step`) HER bar. Eksik bar varken
    koşulan bir oynatma ölçmediği bir yolu ölçmüş gibi görünürdü; bu pozisyon "oynatılamaz"
    sayılır (TADİLAT-1 > 4), uyuşmazlık DEĞİL.
    """
    signal_bar = position.opened_at - step
    needed = pd.date_range(
        position.opened_at, position.opened_at + (VARIANT_BARS + 1) * step, freq=step
    ).append(pd.DatetimeIndex([signal_bar]))
    frame = market.ohlcv.get(position.symbol)
    if frame is None:
        return f"{position.symbol}: mum serisi yok"
    for name, index in ((position.symbol, frame.index), ("çıpa", market.btc.index)):
        missing = needed.difference(index)
        if len(missing):
            return f"{name}: {len(missing)} bar eksik (ilk {missing[0].isoformat()})"
    return None


# --------------------------------------------------------------------------- #
# Dönemler
# --------------------------------------------------------------------------- #
@dataclass(frozen=True, kw_only=True)
class Period:
    name: str
    start: pd.Timestamp
    cutoff: pd.Timestamp

    @property
    def end(self) -> pd.Timestamp:
        return self.cutoff + TAIL


def periods() -> dict[str, Period]:
    out = {
        "A": Period(name="A", start=pd.Timestamp(PERIOD_A_START), cutoff=pd.Timestamp(PERIOD_A_CUTOFF)),
        "B": Period(name="B", start=pd.Timestamp(PERIOD_B_START), cutoff=pd.Timestamp(PERIOD_B_CUTOFF)),
    }
    for period in out.values():
        assert_before_vault(period.end, what=f"dönem {period.name} sonu")
    return out


# --------------------------------------------------------------------------- #
# Config
# --------------------------------------------------------------------------- #
def layer_config() -> dict[str, Any]:
    return copy.deepcopy(dict(resolve_layer(load_config(), LAYER).config))


def with_time_stop(config: Mapping[str, Any], bars: int) -> dict[str, Any]:
    """Config'in kopyası; değişen TEK anahtar `scalp.time_stop_bars` (§6x > 1)."""
    out = copy.deepcopy(dict(config))
    out["scalp"] = {**out["scalp"], "time_stop_bars": int(bars)}
    return out


def history_bars_for(period: Period, config: Mapping[str, Any]) -> int:
    """Pencere + canlı ısınma: `run_backtest`in pencere kapısı (karar 59) bunu ister."""
    step = bar_duration(str(get_setting(config, "timeframe")))
    window = int((period.end - period.start) / step)
    return window + int(get_setting(config, "data.history_bars")) + 10


# --------------------------------------------------------------------------- #
# Pozisyonlar
# --------------------------------------------------------------------------- #
@dataclass(frozen=True, kw_only=True)
class PortfolioPosition:
    symbol: str
    direction: str
    opened_at: pd.Timestamp
    entry_price: float
    r: float
    row: Mapping[str, Any]


def portfolio_positions(rows: Sequence[Mapping[str, Any]]) -> list[PortfolioPosition]:
    """Defter satırlarından pozisyon kümesi P (birim `merge_fills`)."""
    out: list[PortfolioPosition] = []
    for row in merge_fills(rows):
        r = r_multiple(row)
        if r is None:
            raise GateError(f"{row.get('symbol')} {row.get('opened_at')}: R hesaplanamadı")
        out.append(
            PortfolioPosition(
                symbol=str(row["symbol"]),
                direction=str(row["direction"]),
                opened_at=_utc(row["opened_at"]),
                entry_price=float(row["entry_price"]),
                r=float(r),
                row=dict(row),
            )
        )
    out.sort(key=lambda p: (p.opened_at, p.symbol, p.direction))
    return out


# --------------------------------------------------------------------------- #
# Yeniden oynatma — motorun kendisiyle
# --------------------------------------------------------------------------- #
StrategyFactory = Callable[[Mapping[str, Any]], Strategy]


def build_model(config: Mapping[str, Any]) -> Strategy:
    strategies, failures = build_strategies([MODEL], config)
    if not strategies:
        raise GateError(f"{MODEL} kurulamadı: {failures}")
    return strategies[0]


@dataclass(frozen=True, kw_only=True)
class Replay:
    rows: tuple[Mapping[str, Any], ...]
    merged: Mapping[str, Any]
    r: float
    gross_r: float
    ambiguous_stop_exits: int
    stop_exits: int


def replay_market(
    market: MarketData, *, symbol: str, signal_bar: pd.Timestamp, step: pd.Timedelta,
) -> MarketData:
    """Pozisyonun sembolünü taşıyan kısa anlık görüntü: sinyal barından önce LOOKBACK, sonra TAIL."""
    lo = signal_bar - REPLAY_LOOKBACK_BARS * step
    hi = signal_bar + REPLAY_TAIL_BARS * step
    frame = market.ohlcv[symbol]
    btc = market.btc.loc[(market.btc.index >= lo) & (market.btc.index <= hi)]
    if btc.empty:
        raise GateError(f"{symbol} {signal_bar}: yeniden oynatma penceresinde çıpa barı yok")
    funding = market.funding.get(symbol)
    return MarketData(
        ohlcv={symbol: frame.loc[(frame.index >= lo) & (frame.index <= hi)]},
        btc=btc,
        funding={} if funding is None else {symbol: funding.loc[: btc.index[-1]]},
        as_of=btc.index[-1],
    )


def replay_position(
    position: PortfolioPosition,
    *,
    market: MarketData,
    config: Mapping[str, Any],
    bars: int,
    workdir: Path,
    factory: StrategyFactory = build_model,
) -> Replay:
    """P'nin tek pozisyonunu `bars` barlık zaman stop'uyla, izole bir hesapta, motorla koşar."""
    step = bar_duration(str(get_setting(config, "timeframe")))
    signal_bar = position.opened_at - step  # kural 13: dolum sinyal barının ertesinde
    run_config = with_time_stop(config, bars)
    run_config["signals_per_bar"] = True
    strategy = factory(run_config)
    original = strategy.generate_signals

    def only_at_signal_bar(
        snapshot: MarketData, peer_signals: Mapping[str, tuple[Signal, ...]] | None = None
    ) -> list[Signal]:
        if snapshot.as_of != signal_bar:
            return []
        return [s for s in original(snapshot, peer_signals) if s.symbol == position.symbol]

    strategy.generate_signals = only_at_signal_bar  # type: ignore[method-assign]

    snapshot = replay_market(market, symbol=position.symbol, signal_bar=signal_bar, step=step)
    ledger = Ledger(workdir)
    state = ledger.initialize_model(strategy.name, initial_capital=float(run_config["initial_capital"]))
    state["last_processed_bar"] = (signal_bar - step).isoformat()
    ledger.write_state(strategy.name, state)

    report = Engine(
        [strategy], config=run_config, ledger=ledger, portfolio=Portfolio(run_config)
    ).run_round(snapshot)

    final = ledger.load_state(strategy.name) or {}
    if final.get("positions") or final.get("pending_orders"):
        raise GateError(
            f"{position.symbol} {position.opened_at}: yeniden oynatma penceresi sonunda "
            f"pozisyon/emir açık kaldı ({bars} bar)"
        )
    rows = tuple(ledger.read_trades(strategy.name))
    merged = merge_fills(rows)
    if len(merged) != 1:
        raise GateError(
            f"{position.symbol} {position.opened_at}: yeniden oynatma {len(merged)} pozisyon üretti"
        )
    only = merged[0]
    if _utc(only["opened_at"]) != position.opened_at or str(only["direction"]) != position.direction:
        raise GateError(
            f"{position.symbol} {position.opened_at}: yeniden oynatma başka bir pozisyon açtı "
            f"({only['opened_at']} {only['direction']})"
        )
    r = r_multiple(only)
    if r is None:
        raise GateError(f"{position.symbol} {position.opened_at}: yeniden oynatmada R yok")
    model_report = report.by_model(strategy.name)
    return Replay(
        rows=rows,
        merged=only,
        r=float(r),
        gross_r=gross_r(only),
        ambiguous_stop_exits=0 if model_report is None else int(model_report.ambiguous_stop_exits),
        stop_exits=0 if model_report is None else int(model_report.stop_exits),
    )


def gross_r(row: Mapping[str, Any]) -> float:
    """Maliyetsiz R: komisyon, kayma ve fonlama geri eklenir (§6x > 6.4)."""
    risk = float(row["risk_amount"])
    pnl = float(row["pnl"]) + float(row["fee"]) + float(row["slippage_cost"]) - float(row["funding"])
    return pnl / risk


# --------------------------------------------------------------------------- #
# Eşleştirme, parite, sağlama
# --------------------------------------------------------------------------- #
@dataclass(frozen=True, kw_only=True)
class Pair:
    position: PortfolioPosition
    base: Replay
    variant: Replay

    @property
    def delta(self) -> float:
        return self.variant.r - self.base.r

    @property
    def open_at_16(self) -> bool:
        """16 barlık kolda pozisyonu zaman stop'u kapattı mı (`ΔR ≠ 0` olabilen tek kısım)."""
        return exit_rule_of(self.base.merged).endswith(f":{TIME_STOP_RULE}")


def parity_ok(position: PortfolioPosition, base: Replay) -> bool:
    return abs(base.r - position.r) <= PARITY_RTOL * max(1.0, abs(position.r))


def sanity_violation(pair: Pair) -> bool:
    """16 barlık kolda zaman stop'u görmemiş pozisyonda iki kol birebir aynı olmalı."""
    if pair.open_at_16:
        return False
    return pair.delta != 0.0 or str(pair.base.merged["closed_at"]) != str(pair.variant.merged["closed_at"])


@dataclass(kw_only=True)
class PeriodRun:
    period: Period
    positions: list[PortfolioPosition]
    pairs: list[Pair] = field(default_factory=list)
    unreplayable: list[dict[str, Any]] = field(default_factory=list)
    mismatches: list[dict[str, Any]] = field(default_factory=list)
    sanity: list[dict[str, Any]] = field(default_factory=list)
    portfolio: Mapping[str, Any] = field(default_factory=dict)
    coverage: Mapping[str, Any] = field(default_factory=dict)
    funding_coverage: Mapping[str, Any] = field(default_factory=dict)
    seconds: Mapping[str, float] = field(default_factory=dict)

    @property
    def unreplayable_share(self) -> float:
        return len(self.unreplayable) / len(self.positions) if self.positions else float("nan")

    @property
    def gate_failures(self) -> list[str]:
        out: list[str] = []
        if not self.positions:
            out.append("P boş: dönemde hiç pozisyon yok")
        if self.positions and self.unreplayable_share > UNREPLAYABLE_MAX_SHARE:
            out.append(
                f"oynatılamayan pay {self.unreplayable_share:.4f} > {UNREPLAYABLE_MAX_SHARE}"
            )
        if self.mismatches:
            out.append(f"parite uyuşmazlığı: {len(self.mismatches)} pozisyon (tolerans yok)")
        if self.sanity:
            out.append(f"sağlama: {len(self.sanity)} pozisyonda ΔR ≠ 0 (zaman stop'u görmeden)")
        return out


def pair_positions(
    positions: Sequence[PortfolioPosition],
    *,
    market: MarketData,
    config: Mapping[str, Any],
    workdir: Path,
    factory: StrategyFactory = build_model,
) -> tuple[list[Pair], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Her pozisyon iki kolda: `(eşler, oynatılamayan, uyuşmazlık, sağlama)`, SEBEPLERİYLE.

    Oynatılamayan (veri eksik) pozisyon iki koldan da ÇIKARILIR ve sayılır. Oynatılıp
    portföy R'sini üretmeyen ya da başka bir pozisyon açan oynatma bir UYUŞMAZLIKTIR;
    eşleştirmeye girmez ve tek başına dönemi durdurur (TADİLAT-1 > 4).
    """
    step = bar_duration(str(get_setting(config, "timeframe")))
    pairs: list[Pair] = []
    unreplayable: list[dict[str, Any]] = []
    mismatches: list[dict[str, Any]] = []
    sanity: list[dict[str, Any]] = []
    for i, position in enumerate(positions):
        gap = replay_data_gap(position, market=market, step=step)
        if gap is not None:
            unreplayable.append(
                {
                    "symbol": position.symbol,
                    "direction": position.direction,
                    "opened_at": position.opened_at.isoformat(),
                    "reason": gap,
                }
            )
            continue
        try:
            replays = {
                bars: replay_position(
                    position, market=market, config=config, bars=bars,
                    workdir=workdir / f"{i:06d}_{bars}", factory=factory,
                )
                for bars in ARMS
            }
        except GateError as exc:
            # Veri tamken oynatma aynı pozisyonu kuramadı: bu bir UYUŞMAZLIKTIR.
            mismatches.append(
                {
                    "symbol": position.symbol,
                    "direction": position.direction,
                    "opened_at": position.opened_at.isoformat(),
                    "replay_error": str(exc),
                }
            )
            continue
        base = replays[BASE_BARS]
        if not parity_ok(position, base):
            mismatches.append(
                {
                    "symbol": position.symbol,
                    "direction": position.direction,
                    "opened_at": position.opened_at.isoformat(),
                    "leverage_portfolio": _opt_float(position.row.get("leverage")),
                    "leverage_replay": _opt_float(base.merged.get("leverage")),
                    "closed_at_portfolio": str(position.row.get("closed_at")),
                    "closed_at_replay": str(base.merged.get("closed_at")),
                    "exit_portfolio": exit_rule_of(position.row),
                    "exit_replay": exit_rule_of(base.merged),
                }
            )
            continue
        pair = Pair(position=position, base=base, variant=replays[VARIANT_BARS])
        if sanity_violation(pair):
            sanity.append(
                {
                    "symbol": position.symbol,
                    "opened_at": position.opened_at.isoformat(),
                    "exit_base": exit_rule_of(base.merged),
                    "exit_variant": exit_rule_of(pair.variant.merged),
                }
            )
        pairs.append(pair)
    return pairs, unreplayable, mismatches, sanity


# --------------------------------------------------------------------------- #
# İstatistik
# --------------------------------------------------------------------------- #
def cluster_id(opened_at: pd.Timestamp, definition: str) -> str:
    stamp = _utc(opened_at)
    if definition == "day":
        return stamp.strftime("%Y-%m-%d")
    if definition == "week":
        year, week, _ = stamp.isocalendar()
        return f"{year:04d}-W{week:02d}"
    raise ValueError(f"tanınmayan küme tanımı: {definition!r}")


def grouped(items: Sequence[tuple[pd.Timestamp, float]], definition: str) -> dict[str, list[float]]:
    groups: dict[str, list[float]] = defaultdict(list)
    for opened_at, value in items:
        groups[cluster_id(opened_at, definition)].append(value)
    return dict(groups)


def gate_stats(items: Sequence[tuple[pd.Timestamp, float]], *, seed: str) -> dict[str, Any]:
    """Ortalama, iki küme tanımında aralık + tek yönlü p, bağlayıcı alt sınır ve MDE."""
    values = [v for _, v in items]
    n = len(values)
    mean = sum(values) / n if n else float("nan")
    per_definition: dict[str, Any] = {}
    intervals: list[ClusterCI] = []
    mdes: list[float] = []
    for definition in CLUSTER_DEFINITIONS:
        groups = grouped(items, definition)
        definition_seed = f"{seed}:{definition}"
        ci = cluster_mean_ci(
            groups, definition=definition, alpha=ALPHA, iterations=ITERATIONS, seed=definition_seed,
        )
        draws = cluster_mean_draws(groups, iterations=ITERATIONS, seed=definition_seed) if groups else []
        p_one_sided = sum(1 for d in draws if d <= 0.0) / len(draws) if draws else float("nan")
        prec = precision(groups)
        if prec.get("evaluable"):
            mdes.append(float(prec["mde"]))
        intervals.append(ci)
        per_definition[definition] = {
            "clusters": ci.clusters,
            "low": ci.low,
            "high": ci.high,
            "evaluable": ci.evaluable,
            "p_one_sided": p_one_sided,
            "precision": prec,
        }
    low = binding_low(intervals)
    return {
        "n": n,
        "mean": mean,
        "by_definition": per_definition,
        "binding_low": low,
        "evaluable": low is not None,
        "passed": low is not None and mean > 0.0 and low > 0.0,
        # MDE'nin bağlayıcı okuması BÜYÜK olanıdır (backtest_dc'nin minimum kuralının güç tarafı).
        "mde": max(mdes) if len(mdes) == len(CLUSTER_DEFINITIONS) else None,
    }


def cluster_counts(positions: Sequence[PortfolioPosition]) -> dict[str, int]:
    return {
        definition: len({cluster_id(p.opened_at, definition) for p in positions})
        for definition in CLUSTER_DEFINITIONS
    }


def gates(run: PeriodRun, *, seed: int) -> dict[str, Any]:
    items_delta = [(p.position.opened_at, p.delta) for p in run.pairs]
    items_r32 = [(p.position.opened_at, p.variant.r) for p in run.pairs]
    items_r16 = [(p.position.opened_at, p.base.r) for p in run.pairs]
    c1 = gate_stats(items_r32, seed=f"{seed}:vwap_time_stop:{run.period.name}:r32")
    delta = gate_stats(items_delta, seed=f"{seed}:vwap_time_stop:{run.period.name}:delta")
    base = gate_stats(items_r16, seed=f"{seed}:vwap_time_stop:{run.period.name}:r16")
    base["passed"] = None  # betimsel: mevcut kol kapı almaz (§6x > 3)
    opened = [p for p in run.pairs if p.open_at_16]
    n = len(run.pairs)
    conditional = sum(p.delta for p in opened) / len(opened) if opened else float("nan")
    share = len(opened) / n if n else float("nan")
    identity_residual = (
        delta["mean"] - share * conditional if opened else delta["mean"]
    )
    return {
        "c1": c1,
        "delta": delta,
        "base_r16": base,
        "open_at_16": {
            "n": len(opened),
            "share": share,
            "conditional_delta_mean": conditional,
            "identity_residual": identity_residual,
        },
        "passed": bool(c1["passed"] and delta["passed"]),
    }


# --------------------------------------------------------------------------- #
# Betimsel
# --------------------------------------------------------------------------- #
def exit_breakdown(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Çıkış kuralı dağılımı; birim DİLİM (CLAUDE.md > kırılımlar)."""
    groups: dict[str, list[float]] = defaultdict(list)
    for row in rows:
        r = r_multiple(row)
        groups[exit_rule_of(row)].append(float("nan") if r is None else r)
    total = sum(len(v) for v in groups.values())
    return {
        rule: {
            "count": len(values),
            "share": len(values) / total if total else float("nan"),
            "avg_r": _mean([v for v in values if not math.isnan(v)]),
        }
        for rule, values in sorted(groups.items())
    }


def descriptive(run: PeriodRun) -> dict[str, Any]:
    base_rows = [row for p in run.pairs for row in p.base.rows]
    variant_rows = [row for p in run.pairs for row in p.variant.rows]
    opened = [p for p in run.pairs if p.open_at_16]
    resolved = [
        p for p in opened if not exit_rule_of(p.variant.merged).endswith(f":{TIME_STOP_RULE}")
    ]
    by_direction: dict[str, Any] = {}
    for direction in ("long", "short"):
        subset = [(p.position.opened_at, p.delta) for p in run.pairs if p.position.direction == direction]
        by_direction[direction] = (
            gate_stats(subset, seed=f"desc:{run.period.name}:{direction}") if subset else {"n": 0}
        )
        by_direction[direction].pop("passed", None)
    by_year: dict[str, Any] = defaultdict(lambda: {"n": 0, "delta_sum": 0.0})
    by_symbol: dict[str, Any] = defaultdict(lambda: {"n": 0, "delta_sum": 0.0})
    for p in run.pairs:
        for bucket, key in ((by_year, str(p.position.opened_at.year)), (by_symbol, p.position.symbol)):
            bucket[key]["n"] += 1
            bucket[key]["delta_sum"] += p.delta
    return {
        "exit_rules": {"16": exit_breakdown(base_rows), "32": exit_breakdown(variant_rows)},
        "open_at_16_subset": {
            "n": len(opened),
            "resolved_17_to_33": len(resolved),
            "time_stop_at_32": len(opened) - len(resolved),
            "variant_exit_rules": dict(Counter(exit_rule_of(p.variant.merged) for p in opened)),
            "mean_r16": _mean([p.base.r for p in opened]),
            "mean_r32": _mean([p.variant.r for p in opened]),
            "mean_delta": _mean([p.delta for p in opened]),
            "resolved_mean_r16": _mean([p.base.r for p in resolved]),
            "resolved_mean_r32": _mean([p.variant.r for p in resolved]),
        },
        "ambiguous_stop_exits": {
            "16": sum(p.base.ambiguous_stop_exits for p in run.pairs),
            "32": sum(p.variant.ambiguous_stop_exits for p in run.pairs),
            "stop_exits_16": sum(p.base.stop_exits for p in run.pairs),
            "stop_exits_32": sum(p.variant.stop_exits for p in run.pairs),
        },
        "gross": {
            "mean_gross_r16": _mean([p.base.gross_r for p in run.pairs]),
            "mean_gross_r32": _mean([p.variant.gross_r for p in run.pairs]),
            "mean_gross_delta": _mean([p.variant.gross_r - p.base.gross_r for p in run.pairs]),
        },
        "by_direction": by_direction,
        "by_year": {k: {**v, "mean_delta": v["delta_sum"] / v["n"]} for k, v in sorted(by_year.items())},
        "by_symbol": {k: {**v, "mean_delta": v["delta_sum"] / v["n"]} for k, v in sorted(by_symbol.items())},
    }


def mfe_diagnosis(run: PeriodRun, market: MarketData, step: pd.Timedelta) -> dict[str, Any]:
    """§6x > 7: mevcut kol (16), yalnızca A; tanımlar `position_paths`ten."""
    by_rule: dict[str, list[dict[str, Any]]] = defaultdict(list)
    all_paths: list[dict[str, Any]] = []
    for pair in run.pairs:
        row = dict(pair.base.merged)
        # Satır satır: `position_paths` ölçemediği satırı ATLAR ve toplu çağrıda satır ↔
        # yol eşlemesi kayardı.
        for path in position_paths([row], market.ohlcv):
            by_rule[exit_rule_of(row)].append(path)
            all_paths.append(path)

    def summary(items: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
        prior = [float(x["mfe_r_prior"]) for x in items]
        return {
            "mfe_prior": distribution(prior),
            "mfe_incl": distribution([float(x["mfe_r"]) for x in items]),
            "mae_prior": distribution([float(x["mae_r_prior"]) for x in items]),
            "mfe_prior_p25": _quantile(prior, 0.25),
            "mfe_prior_buckets": buckets(prior, MFE_EDGES),
        }

    time_stopped = [p for p in run.pairs if p.open_at_16]
    late = [late_excursion(p, market, step) for p in time_stopped]
    return {
        "all": summary(all_paths),
        "by_exit_rule": {rule: summary(items) for rule, items in sorted(by_rule.items())},
        "time_stopped": {
            "n": len(time_stopped),
            "r_at_16": distribution([p.base.r for p in time_stopped]),
            "mfe_bars_17_32": distribution([x for x in late if not math.isnan(x)]),
        },
    }


def late_excursion(pair: Pair, market: MarketData, step: pd.Timedelta) -> float:
    """Zaman stop'una düşen pozisyonda 17–32. barlardaki en lehte hareket (R, yöne göre)."""
    row = pair.base.merged
    entry, stop = float(row["entry_price"]), float(row["stop_price"])
    unit = abs(entry - stop)
    frame = market.ohlcv.get(str(row["symbol"]))
    if frame is None or unit <= 0.0:
        return float("nan")
    opened = _utc(row["opened_at"])
    window = frame.loc[(frame.index >= opened + 17 * step) & (frame.index <= opened + 32 * step)]
    if window.empty:
        return float("nan")
    if str(row["direction"]) == "long":
        return (float(window["high"].max()) - entry) / unit
    return (entry - float(window["low"].min())) / unit


# --------------------------------------------------------------------------- #
# Portföy koşuları
# --------------------------------------------------------------------------- #
def portfolio_summary(result: Any) -> dict[str, Any]:
    metrics = next(m for m in result.metrics if m.model == MODEL)
    report = result.report.by_model(MODEL)
    return {
        "positions": metrics.total.trades,
        "avg_r": metrics.total.avg_r,
        "total_return": metrics.account.total_return,
        "max_drawdown": metrics.account.max_drawdown,
        "signals": None if report is None else report.signals,
        "filled": None if report is None else report.filled,
        "rejections": {} if report is None else dict(report.rejections),
    }


def run_portfolio(
    period: Period, *, out_dir: Path, config: Mapping[str, Any], bars: int,
) -> tuple[Any, list[dict[str, Any]]]:
    """`run_backtest` — yalnızca `vwap_managed`, `bars` barlık zaman stop'uyla."""
    config_path: str | None = None
    if bars != BASE_BARS:
        raw = load_config()
        raw["scalp"] = {**raw["scalp"], "time_stop_bars": int(bars)}
        config_path = str(out_dir / f"config_time_stop_{bars}.yaml")
        out_dir.mkdir(parents=True, exist_ok=True)
        Path(config_path).write_text(_yaml_dump(raw), encoding="utf-8")
    result = run_backtest(
        layer_name=LAYER,
        start=period.start,
        end=period.end,
        out_dir=out_dir / f"portfolio_{period.name}_{bars}",
        models=[MODEL],
        config_path=config_path,
        history_bars=history_bars_for(period, config),
        signal_cutoff=period.cutoff,
    )
    rows = Ledger(result.out_dir / "ledger").read_trades(MODEL)
    return result, rows


def load_period_market(period: Period, config: Mapping[str, Any]) -> MarketData:
    """`run_backtest`in kurduğu anlık görüntünün aynısı (aynı önbellek, aynı `now`)."""
    deep = copy.deepcopy(dict(config))
    deep["data"] = {**deep["data"], "history_bars": history_bars_for(period, config)}
    universe = resolve_layer(load_config(), LAYER).symbols
    return load_market_data(deep, symbols=None if universe is None else list(universe), now=period.end)


def run_period(period: Period, *, out_dir: Path, config: Mapping[str, Any], full: bool) -> tuple[PeriodRun, MarketData]:
    t0 = time.monotonic()
    result, rows = run_portfolio(period, out_dir=out_dir, config=config, bars=BASE_BARS)
    t1 = time.monotonic()
    positions = portfolio_positions(rows)
    market = load_period_market(period, config)
    run = PeriodRun(
        period=period,
        positions=positions,
        coverage=dict(result.coverage),
        funding_coverage=dict(result.funding_coverage),
    )
    with tempfile.TemporaryDirectory(prefix="vwap-time-stop-") as tmp:
        run.pairs, run.unreplayable, run.mismatches, run.sanity = pair_positions(
            positions, market=market, config=config, workdir=Path(tmp),
        )
    t2 = time.monotonic()
    seconds = {"portfolio_16": t1 - t0, "replays": t2 - t1}
    if full and not run.gate_failures:
        variant_result, _ = run_portfolio(period, out_dir=out_dir, config=config, bars=VARIANT_BARS)
        run.portfolio = {"16": portfolio_summary(result), "32": portfolio_summary(variant_result)}
        seconds["portfolio_32"] = time.monotonic() - t2
    run.seconds = seconds
    return run, market


# --------------------------------------------------------------------------- #
# Yükler
# --------------------------------------------------------------------------- #
def precheck_payload(run: PeriodRun) -> dict[str, Any]:
    """Ön-kontrol: SAYILAR. Hiçbir R, ΔR, MFE ya da çıkış dağılımı taşımaz (test)."""
    return {
        "period": run.period.name,
        "start": run.period.start.isoformat(),
        "signal_cutoff": run.period.cutoff.isoformat(),
        "end": run.period.end.isoformat(),
        "positions": len(run.positions),
        "clusters": cluster_counts(run.positions),
        "unreplayable": len(run.unreplayable),
        "unreplayable_share": run.unreplayable_share,
        "unreplayable_detail": run.unreplayable,
        "parity_mismatches": len(run.mismatches),
        "parity_mismatch_detail": run.mismatches,
        "sanity_violations": len(run.sanity),
        "sanity_detail": run.sanity,
        "paired": len(run.pairs),
        "gate_failures": run.gate_failures,
        "coverage": run.coverage,
        "funding_coverage": run.funding_coverage,
        "seconds": run.seconds,
    }


def preflight(out_dir: Path) -> tuple[int, dict[str, Any]]:
    config = layer_config()
    period = periods()["A"]
    try:
        run, _ = run_period(period, out_dir=out_dir, config=config, full=False)
    except (GateError, WindowCoverageError) as exc:
        return EXIT_GATE, {"stage": "preflight", "error": str(exc)}
    payload = {"stage": "preflight", "A": precheck_payload(run)}
    return (EXIT_GATE if run.gate_failures else EXIT_OK), payload


def _measured_section(
    run: PeriodRun, market: MarketData, *, seed: int, step: pd.Timedelta, with_mfe: bool
) -> dict[str, Any]:
    section: dict[str, Any] = {
        "gates": gates(run, seed=seed),
        "descriptive": descriptive(run),
        "portfolio": run.portfolio,
    }
    if with_mfe:
        section["mfe"] = mfe_diagnosis(run, market, step)
    return section


def measure_a(out_dir: Path) -> tuple[int, dict[str, Any]]:
    """`measure` aşaması: YALNIZCA dönem A (TADİLAT-1 > 9). B ayrı aşamadır (`measure-b`)."""
    config = layer_config()
    seed = int(get_setting(config, "random_seed"))
    step = bar_duration(str(get_setting(config, "timeframe")))
    payload: dict[str, Any] = {"stage": "measure", "periods": {}}
    try:
        run, market = run_period(periods()["A"], out_dir=out_dir, config=config, full=True)
    except (GateError, WindowCoverageError) as exc:
        payload["periods"]["A"] = {"error": str(exc)}
        payload["b_decision"] = b_decision(payload)
        return EXIT_GATE, payload
    section: dict[str, Any] = {"precheck": precheck_payload(run)}
    payload["periods"]["A"] = section
    if not run.gate_failures:
        section.update(_measured_section(run, market, seed=seed, step=step, with_mfe=True))
    payload["b_decision"] = b_decision(payload)
    return (EXIT_GATE if run.gate_failures else EXIT_OK), payload


def b_decision(payload_a: Mapping[str, Any]) -> dict[str, Any]:
    """B koşar mı — PROGRAMATİK karar (TADİLAT-1 > 9): A'da ΔR kapısı ∧ C-1, ikisi de geçmiş olmalı.

    Eksik/bozuk bir A yükü (hata, kapı hatası, kapı alanı yok) "geçti" sayılmaz.
    """
    gates_a = ((payload_a.get("periods") or {}).get("A") or {}).get("gates") or {}
    c1 = bool((gates_a.get("c1") or {}).get("passed") is True)
    delta = bool((gates_a.get("delta") or {}).get("passed") is True)
    eligible = payload_a.get("stage") == "measure" and c1 and delta and gates_a.get("passed") is True
    return {
        "eligible": eligible,
        "c1_passed": c1,
        "delta_passed": delta,
        "reason": (
            "A'da ΔR kapısı ve C-1 birlikte geçti" if eligible
            else "A'da ΔR kapısı ve C-1 birlikte geçmedi (ya da A yükü eksik)"
        ),
    }


def sha256_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_pinned_a(pinned: Path, sha_file: Path) -> tuple[dict[str, Any], str]:
    """Pin'lenmiş A yükü ve hash'i; hash TUTMAZSA `GateError` (B'nin verisi çekilmeden)."""
    if not pinned.is_file() or not sha_file.is_file():
        raise GateError(f"pin'lenmiş A yükü ya da hash'i yok: {pinned} / {sha_file}")
    expected = sha_file.read_text(encoding="utf-8").split()[0].strip().lower()
    actual = sha256_of(pinned)
    if actual != expected:
        raise GateError(f"A yükünün hash'i tutmuyor: {actual} ≠ {expected}")
    return json.loads(pinned.read_text(encoding="utf-8")), actual


def measure_b(
    out_dir: Path, *, pinned: Path | None = None, sha_file: Path | None = None,
    run_b: Callable[[Path], tuple[int, dict[str, Any]]] | None = None,
) -> tuple[int, dict[str, Any]]:
    """`measure-b` aşaması: pin'lenmiş A'yı okur, hash'i doğrular, B'yi YALNIZCA A geçtiyse koşar.

    Karar `b_decision`dandır ve A yükünün kendisinden yeniden kurulur — A'nın yazdığı bayrağa
    güvenilmez. B koşmazsa hiçbir portföy koşusu yapılmaz ve hiçbir mum indirilmez.
    """
    pinned = pinned if pinned is not None else PINNED_A
    sha_file = sha_file if sha_file is not None else PINNED_A_SHA
    payload: dict[str, Any] = {"stage": "measure-b", "periods": {}}
    try:
        payload_a, digest = read_pinned_a(pinned, sha_file)
    except (GateError, ValueError) as exc:
        payload["periods"]["B"] = {"error": str(exc)}
        return EXIT_GATE, payload
    decision = b_decision(payload_a)
    payload["a_source"] = {"path": str(pinned), "sha256": digest, "decision": decision}
    if not decision["eligible"]:
        payload["periods"]["B"] = {"skipped": f"B koşulmadı: {decision['reason']}"}
        return EXIT_OK, payload
    code, section = (run_b or _run_period_b)(out_dir)
    payload["periods"]["B"] = section
    return code, payload


def _run_period_b(out_dir: Path) -> tuple[int, dict[str, Any]]:
    config = layer_config()
    seed = int(get_setting(config, "random_seed"))
    step = bar_duration(str(get_setting(config, "timeframe")))
    try:
        run, market = run_period(periods()["B"], out_dir=out_dir, config=config, full=True)
    except (GateError, WindowCoverageError) as exc:
        return EXIT_GATE, {"error": str(exc)}
    section: dict[str, Any] = {"precheck": precheck_payload(run)}
    if run.gate_failures:
        return EXIT_GATE, section
    section.update(_measured_section(run, market, seed=seed, step=step, with_mfe=False))
    return EXIT_OK, section


# --------------------------------------------------------------------------- #
# Rapor metni — sıra TADİLAT-1'de sabit
# --------------------------------------------------------------------------- #
def format_report(payload: Mapping[str, Any]) -> str:
    lines: list[str] = [f"# vwap_managed zaman stop'u 16 → 32 bar (§6x) — {payload.get('stage')}", ""]
    if payload.get("stage") == "preflight":
        lines += _format_precheck(payload.get("A") or payload)
        return "\n".join(lines)
    periods_payload = payload.get("periods", {})
    a = periods_payload.get("A", {})
    lines += ["## 1. Ön-kontrol (16 bar birebir) — A"] + _format_precheck(a.get("precheck") or a)
    if "gates" in a:
        g = a["gates"]
        lines += ["", "## 2. C-1 — A (varyantın kendi ortalama R'si)"] + _format_gate(g["c1"])
        lines += ["", "mevcut kol (16, betimsel): " + _fmt(g["base_r16"]["mean"])]
        lines += ["", "## 3. ΔR — A"] + _format_gate(g["delta"])
        o = g["open_at_16"]
        lines += [
            f"16. barda hâlâ açık: {o['n']} / {g['delta']['n']} (pay {_fmt(o['share'])}), "
            f"koşullu ΔR̄ {_fmt(o['conditional_delta_mean'])}, özdeşlik artığı {_fmt(o['identity_residual'])}",
            f"A sonucu: C-1 {'GEÇTİ' if g['c1']['passed'] else 'GEÇMEDİ'} · "
            f"ΔR {'GEÇTİ' if g['delta']['passed'] else 'GEÇMEDİ'} → "
            f"{'B koşulur' if g['passed'] else 'B KOŞULMAZ'}",
        ]
        d = a["descriptive"]
        lines += [
            "", "yön / yıl / sembol (betimsel):",
            *[f"  {k}: n={v.get('n')} ΔR̄={_fmt(v.get('mean'))} alt={_fmt(v.get('binding_low'))}"
              for k, v in d["by_direction"].items()],
            *[f"  {k}: n={v['n']} ΔR̄={_fmt(v['mean_delta'])}" for k, v in d["by_year"].items()],
            *[f"  {k}: n={v['n']} ΔR̄={_fmt(v['mean_delta'])}" for k, v in d["by_symbol"].items()],
            f"brüt: R̄16 {_fmt(d['gross']['mean_gross_r16'])} · R̄32 {_fmt(d['gross']['mean_gross_r32'])}"
            f" · ΔR̄ {_fmt(d['gross']['mean_gross_delta'])}",
        ]
        lines += ["", "## 4. Çıkış sebepleri — A (birim: dilim)"]
        for arm in ("16", "32"):
            lines.append(f"  kol {arm}:")
            lines += [f"    {rule}: {v['count']} (pay {_fmt(v['share'])}) R̄ {_fmt(v['avg_r'])}"
                      for rule, v in d["exit_rules"][arm].items()]
        s = d["open_at_16_subset"]
        lines += [
            f"  16–32 alt kümesi: n={s['n']}, 17–33. barda çözülen {s['resolved_17_to_33']}, "
            f"32'de de zaman stop'u {s['time_stop_at_32']}; R̄16 {_fmt(s['mean_r16'])} → R̄32 {_fmt(s['mean_r32'])}",
            f"    varyant çıkışları: {s['variant_exit_rules']}",
            f"  kural 13 belirsizliği: {d['ambiguous_stop_exits']}",
        ]
        m = a.get("mfe", {})
        lines += ["", "## 5. MFE teşhisi — A, mevcut kol (bağımsız, kapı DEĞİL)"]
        if m:
            lines.append(f"  tümü: MFE* {_dist(m['all']['mfe_prior'])}")
            lines += [f"  {rule}: MFE* {_dist(v['mfe_prior'])}" for rule, v in m["by_exit_rule"].items()]
            lines.append("  kovalar (tümü): " + ", ".join(
                f"[{b['low']},{b['high']}): {b['count']}" for b in m["all"]["mfe_prior_buckets"]))
            t = m["time_stopped"]
            lines.append(f"  zaman stop'u (n={t['n']}): R@16 {_dist(t['r_at_16'])}; MFE 17–32 {_dist(t['mfe_bars_17_32'])}")
        p = a.get("portfolio", {})
        lines += ["", "## 6. Portföy etkisi — A (eşleştirilmemiş)"]
        for arm in ("16", "32"):
            if arm in p:
                v = p[arm]
                lines.append(
                    f"  kol {arm}: pozisyon {v['positions']} · R̄ {_fmt(v['avg_r'])} · getiri "
                    f"{_fmt(v['total_return'])} · maxDD {_fmt(v['max_drawdown'])} · ret {v['rejections']}"
                )
    decision = payload.get("b_decision")
    if decision is not None:
        lines += [
            "", "## 7. Dönem B — ayrı aşama (`vts-measure-b`)",
            f"  programatik karar: {'B KOŞULACAK' if decision['eligible'] else 'B KOŞULMAYACAK'} — {decision['reason']}",
        ]
    b = periods_payload.get("B")
    if b is not None:
        lines += ["", "## 7. Dönem B"]
        if "skipped" in b:
            lines.append(f"  KOŞULMADI — {b['skipped']} (B verisi çekilmedi)")
        else:
            lines += _format_precheck(b.get("precheck") or b)
            if "gates" in b:
                g = b["gates"]
                lines += ["  C-1:"] + _format_gate(g["c1"]) + ["  ΔR:"] + _format_gate(g["delta"])
                o = g["open_at_16"]
                lines.append(f"  16. barda hâlâ açık: {o['n']} (pay {_fmt(o['share'])})")
                lines.append(f"  B sonucu: {'İKİ KAPI GEÇTİ' if g['passed'] else 'GEÇMEDİ'}")
    return "\n".join(lines)


def _format_precheck(pre: Mapping[str, Any]) -> list[str]:
    if "error" in pre:
        return [f"  HATA: {pre['error']}"]
    return [
        f"  pencere ({pre.get('start')}, {pre.get('end')}], sinyal kesimi {pre.get('signal_cutoff')}",
        f"  P = {pre.get('positions')} pozisyon; küme {pre.get('clusters')}",
        f"  oynatılamayan (veri eksik, iki koldan çıkarıldı): {pre.get('unreplayable')} "
        f"(pay {_fmt(pre.get('unreplayable_share'))}, sınır {UNREPLAYABLE_MAX_SHARE})",
        f"  parite uyuşmazlığı (tolerans yok): {pre.get('parity_mismatches')}; "
        f"sağlama ihlali {pre.get('sanity_violations')}; eşleşen {pre.get('paired')}",
        f"  kapı hataları: {pre.get('gate_failures') or 'yok'}",
        f"  süre (sn): {pre.get('seconds')}",
    ]


def _format_gate(g: Mapping[str, Any]) -> list[str]:
    defs = g.get("by_definition", {})
    return [
        f"  n={g['n']} ort {_fmt(g['mean'])} · bağlayıcı alt {_fmt(g['binding_low'])} · MDE {_fmt(g.get('mde'))}"
        + ("" if g.get("passed") is None else f" · {'GEÇTİ' if g['passed'] else ('GEÇMEDİ' if g['evaluable'] else 'DEĞERLENDİRİLEMEZ')}"),
        *[f"    {k}: küme {v['clusters']} [{_fmt(v['low'])}, {_fmt(v['high'])}] p₁ {_fmt(v['p_one_sided'])}"
          for k, v in defs.items()],
    ]


def _dist(d: Mapping[str, Any]) -> str:
    return f"n={d.get('n')} med {_fmt(d.get('median'))} p75 {_fmt(d.get('p75'))} p90 {_fmt(d.get('p90'))}"


# --------------------------------------------------------------------------- #
# Yardımcılar
# --------------------------------------------------------------------------- #
def _utc(value: Any) -> pd.Timestamp:
    stamp = pd.Timestamp(value)
    return stamp.tz_localize("UTC") if stamp.tzinfo is None else stamp.tz_convert("UTC")


def _mean(values: Sequence[float]) -> float:
    return sum(values) / len(values) if values else float("nan")


def _quantile(values: Sequence[float], q: float) -> float:
    """Yüzdelik `core/metrics.py::_percentile`in tek tanımıyla."""
    clean = sorted(v for v in values if not math.isnan(v))
    return _percentile(clean, q) if clean else float("nan")


def _opt_float(value: Any) -> float | None:
    try:
        return None if value in (None, "") else float(value)
    except (TypeError, ValueError):
        return None


def _fmt(value: Any) -> str:
    if value is None:
        return "—"
    if isinstance(value, float):
        return "—" if math.isnan(value) else f"{value:+.4f}"
    return str(value)


def _yaml_dump(raw: Mapping[str, Any]) -> str:
    import yaml

    return yaml.safe_dump(dict(raw), allow_unicode=True, sort_keys=False)


def _jsonable(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, float):
        return None if math.isnan(value) or math.isinf(value) else value
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    return value


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--stage", required=True, choices=tuple(STAGE_FILES))
    parser.add_argument("--out-dir", required=True, type=Path)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    logger.setLevel(logging.INFO)

    args.out_dir.mkdir(parents=True, exist_ok=True)
    stage_fn = {"preflight": preflight, "measure": measure_a, "measure-b": measure_b}[args.stage]
    code, payload = stage_fn(args.out_dir)
    name = STAGE_FILES[args.stage]
    json_path = args.out_dir / f"{name}.json"
    json_path.write_text(json.dumps(_jsonable(payload), ensure_ascii=False, indent=2), encoding="utf-8")
    if args.stage == "measure":
        # Hash yazılan BAYTLARIN hash'idir; `measure-b` pin'lenmiş kopyayı buna karşı doğrular.
        (args.out_dir / f"{name}.json.sha256").write_text(
            f"{sha256_of(json_path)}  {json_path.name}\n", encoding="utf-8"
        )
    text = format_report(payload)
    (args.out_dir / f"{name}.txt").write_text(text, encoding="utf-8")
    print(text)
    return code


if __name__ == "__main__":
    sys.exit(main())

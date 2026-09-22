"""`scripts/measure_death_cross.py`nin SAF mantığı: ağ erişimi yok, defter yok.

Sınanan şey aracın beş sözleşmesidir:

1. **EMA'nın tek tanımı** — bar bazlı seri, `core/indicators.py::ema`nın nokta değeriyle
   birebir aynı sayıyı verir. İkinci bir uygulama ayrışabilirdi; testin işi o ayrışmanın
   sessiz kalmamasıdır.
2. **Kesişim bir OLAYDIR** — `fast < slow` durumu her barda tetiklemez.
3. **Küme tek sayılır** — ardışık kurulum barları tek BİRİNCİL kuruluma iner ve zincir
   serinin tamamında kurulur.
4. **Isınma dönem A'nın İÇİNDEN yenmez** — geçmişi yetmeyen sembolün sayımı ısınmadan
   sonra başlar ve bu satırın kendisinde görünür.
5. **Dönem A kesimi bir KAPIDIR** — aşan bir `--end` hata koduyla biter; ve araç ölçümün
   parçası değildir (R/PnL üreten modülleri import etmez, yük onların alanlarını taşımaz).
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from core.indicators import ema as ema_point
from scripts.measure_death_cross import (
    FAST_PERIOD,
    MIN_GAP_BARS,
    SLOW_PERIOD,
    Setup,
    collapse_clusters,
    death_crosses,
    ema_series,
    first_per_regime,
    geometry,
    main,
    regime_starts,
    scan_symbol,
    setup_bars,
)

BAR = "4h"


def _frame(closes, *, highs=None, opens=None, start="2021-01-01T00:00:00+00:00") -> pd.DataFrame:
    index = pd.date_range(start=start, periods=len(closes), freq=BAR, tz="UTC")
    index.name = "ts"
    close = np.asarray(closes, dtype="float64")
    return pd.DataFrame(
        {
            "open": np.asarray(opens, dtype="float64") if opens is not None else close + 1.0,
            "high": np.asarray(highs, dtype="float64") if highs is not None else close + 2.0,
            "low": close - 2.0,
            "close": close,
            "volume": np.full(len(close), 1000.0),
        },
        index=index,
    )


# --------------------------------------------------------------------------- #
# 1. EMA: tek tanım
# --------------------------------------------------------------------------- #
def test_ema_series_matches_the_single_definition_bar_by_bar():
    rng = np.random.default_rng(7)
    closes = 100.0 + np.cumsum(rng.normal(0.0, 1.0, size=400))
    frame = _frame(closes)
    series = ema_series(frame["close"], 50)
    for position in (49, 50, 123, 399):
        expected = ema_point(frame["close"].iloc[: position + 1], 50)
        assert series.iloc[position] == pytest.approx(expected, rel=1e-12, abs=1e-12)


def test_ema_series_is_nan_before_the_seed_is_defined():
    frame = _frame(np.linspace(100.0, 120.0, 60))
    series = ema_series(frame["close"], 50)
    assert series.iloc[:49].isna().all()
    assert not np.isnan(series.iloc[49])


# --------------------------------------------------------------------------- #
# 2. Kesişim bir OLAY, rejim bir DURUM
# --------------------------------------------------------------------------- #
def test_death_cross_fires_once_not_on_every_bar_below():
    index = pd.date_range("2022-01-01", periods=6, freq=BAR, tz="UTC")
    fast = pd.Series([10.0, 10.0, 9.0, 8.0, 7.0, 11.0], index=index)
    slow = pd.Series([10.0, 10.0, 10.0, 10.0, 10.0, 10.0], index=index)
    flags = death_crosses(fast, slow)
    assert list(flags) == [False, False, True, False, False, False]


def test_regime_stays_active_until_fast_climbs_back():
    index = pd.date_range("2022-01-01", periods=6, freq=BAR, tz="UTC")
    fast = pd.Series([10.0, 9.0, 8.0, 7.0, 11.0, 12.0], index=index)
    slow = pd.Series([10.0] * 6, index=index)
    starts = regime_starts(death_crosses(fast, slow), fast < slow)
    assert starts.iloc[0] is None
    assert list(starts.iloc[1:4]) == [index[1]] * 3
    assert starts.iloc[4] is None and starts.iloc[5] is None


def test_regime_needs_an_observed_cross_not_just_fast_below_slow():
    """Serinin başında zaten `fast < slow` ise rejim AKTİF DEĞİLDİR."""
    index = pd.date_range("2022-01-01", periods=4, freq=BAR, tz="UTC")
    fast = pd.Series([8.0, 8.0, 8.0, 8.0], index=index)
    slow = pd.Series([10.0] * 4, index=index)
    starts = regime_starts(death_crosses(fast, slow), fast < slow)
    assert all(value is None for value in starts)


def test_setup_bar_needs_all_three_conditions():
    index = pd.date_range("2022-01-01", periods=4, freq=BAR, tz="UTC")
    fast = pd.Series([100.0] * 4, index=index)
    frame = pd.DataFrame(
        {
            # 0: dokundu, altında kapandı, ayı mumu   -> kurulum
            # 1: dokunmadı                            -> hayır
            # 2: dokundu ama EMA'nın ÜSTÜNDE kapandı  -> hayır
            # 3: dokundu, altında kapandı, BOĞA mumu  -> hayır
            "open": [100.0, 98.0, 100.0, 96.0],
            "high": [101.0, 99.0, 102.0, 101.0],
            "low": [96.0, 96.0, 96.0, 96.0],
            "close": [97.0, 98.5, 101.0, 99.0],
        },
        index=index,
    )
    assert list(setup_bars(frame, fast)) == [True, False, False, False]


# --------------------------------------------------------------------------- #
# 3. Küme tek sayılır
# --------------------------------------------------------------------------- #
def test_consecutive_setup_bars_collapse_to_one_primary():
    index = pd.date_range("2022-01-01", periods=20, freq=BAR, tz="UTC")
    stamps = [index[3], index[4], index[5], index[6]]
    assert collapse_clusters(stamps, index, min_gap=MIN_GAP_BARS) == [index[3]]


def test_gap_of_exactly_six_bars_is_a_new_primary():
    index = pd.date_range("2022-01-01", periods=20, freq=BAR, tz="UTC")
    assert collapse_clusters([index[0], index[5], index[6]], index, min_gap=6) == [index[0], index[6]]


def test_first_per_regime_is_the_regimes_own_first_not_the_first_we_saw():
    index = pd.date_range("2022-01-01", periods=10, freq=BAR, tz="UTC")
    regimes = pd.Series([index[0]] * 5 + [index[5]] * 5, index=index, dtype="object")
    firsts = first_per_regime([index[1], index[3], index[6], index[8]], regimes)
    assert firsts == {index[0]: index[1], index[5]: index[6]}


# --------------------------------------------------------------------------- #
# 4. Isınma ve sayım penceresi
# --------------------------------------------------------------------------- #
def _crossing_frame(periods: int = 900, start: str = "2021-01-01T00:00:00+00:00") -> pd.DataFrame:
    """Yükselip sonra düşen bir seri: EMA50 EMA200'ü aşağı keser ve geri çekilmeler olur."""
    rise = np.linspace(100.0, 200.0, periods // 2)
    fall = np.linspace(200.0, 90.0, periods - periods // 2)
    closes = np.concatenate([rise, fall])
    # Her barın fitili EMA'ya dokunsun, kapanış altında ve ayı mumu olsun diye
    # düşüş bacağında open/high yukarı itilir.
    opens = closes + 3.0
    highs = closes + 8.0
    return _frame(closes, highs=highs, opens=opens, start=start)


def test_counting_starts_after_warmup_when_history_begins_inside_the_period():
    frame = _crossing_frame(periods=900, start="2022-03-01T00:00:00+00:00")
    scan = scan_symbol(
        "SUI-USDT-SWAP",
        frame,
        start=pd.Timestamp("2022-01-01T00:00:00+00:00"),
        end=pd.Timestamp("2024-06-30T00:00:00+00:00"),
        atr_period=14,
        warmup_bars=600,
        min_gap_bars=MIN_GAP_BARS,
    )
    assert scan.warmup_from_inside is True
    assert scan.count_from == frame.index[600]
    assert all(setup.bar >= scan.count_from for setup in scan.raw)


def test_short_history_is_skipped_not_counted_with_a_partial_ema():
    frame = _crossing_frame(periods=300)
    scan = scan_symbol(
        "ETHFI-USDT-SWAP",
        frame,
        start=pd.Timestamp("2022-01-01T00:00:00+00:00"),
        end=pd.Timestamp("2024-06-30T00:00:00+00:00"),
        atr_period=14,
        warmup_bars=600,
        min_gap_bars=MIN_GAP_BARS,
    )
    assert scan.skipped is not None
    assert scan.raw == () and scan.primary == ()


def test_primary_is_a_subset_of_raw_and_respects_the_gap():
    frame = _crossing_frame(periods=1200)
    scan = scan_symbol(
        "BTC-USDT-SWAP",
        frame,
        start=pd.Timestamp("2021-01-01T00:00:00+00:00"),
        end=pd.Timestamp("2024-06-30T00:00:00+00:00"),
        atr_period=14,
        warmup_bars=600,
        min_gap_bars=MIN_GAP_BARS,
    )
    assert scan.raw, "kurgu seri hiç kurulum üretmedi — test kendini sınayamaz"
    assert len(scan.primary) <= len(scan.raw)
    positions = {stamp: number for number, stamp in enumerate(frame.index)}
    gaps = [
        positions[b.bar] - positions[a.bar]
        for a, b in zip(scan.primary, scan.primary[1:])
    ]
    assert all(gap >= MIN_GAP_BARS for gap in gaps)
    # Kurulum yalnızca rejim aktifken sayılır: EMA50 her satırda EMA200'ün ALTINDA.
    assert all(setup.ema_fast < setup.ema_slow for setup in scan.raw)


# --------------------------------------------------------------------------- #
# Geometri: mesafe, getiri değil
# --------------------------------------------------------------------------- #
def _setup(multiple: float | None) -> Setup:
    stamp = pd.Timestamp("2022-06-01T00:00:00+00:00")
    atr = None if multiple is None else 2.0
    close = 100.0
    slow = close if multiple is None else close + multiple * 2.0
    return Setup(
        symbol="BTC-USDT-SWAP", bar=stamp, close=close, ema_fast=99.0, ema_slow=slow,
        atr=atr, regime_start=stamp, on_cross_bar=False,
    )


def test_geometry_reports_distance_and_the_share_above_each_ceiling():
    stats = geometry([_setup(1.0), _setup(2.0), _setup(4.0), _setup(8.0)])
    assert stats.measured == 4
    assert stats.median == pytest.approx(3.0)
    assert stats.maximum == pytest.approx(8.0)
    assert stats.above[3.0] == 2 and stats.above[6.0] == 1
    assert stats.share(3.0) == pytest.approx(50.0)


def test_geometry_counts_unmeasurable_setups_separately_instead_of_inventing_a_distance():
    stats = geometry([_setup(2.0), _setup(None)])
    assert stats.measured == 1 and stats.unmeasured == 1
    assert stats.median == pytest.approx(2.0)


def test_geometry_of_nothing_is_none_not_zero():
    stats = geometry([])
    assert stats.measured == 0
    assert stats.median is None and stats.share(3.0) is None


# --------------------------------------------------------------------------- #
# 5. Kapsam kapısı ve ölçüm ayrımı
# --------------------------------------------------------------------------- #
def test_end_beyond_period_a_cutoff_is_refused():
    assert main(["--end", "2025-01-01T00:00:00+00:00"]) == 2


def test_inverted_window_is_refused():
    assert main(["--start", "2024-01-01T00:00:00+00:00", "--end", "2023-01-01T00:00:00+00:00"]) == 2


def test_symbol_outside_the_layer_universe_is_refused():
    assert main(["--symbols", "NOTALISTED-USDT-SWAP"]) == 2


def test_module_does_not_import_measurement_or_strategy_modules():
    """Araç ölçümün parçası değildir: R/PnL üreten modüllere erişimi hiç yoktur."""
    text = Path("scripts/measure_death_cross.py").read_text(encoding="utf-8")
    for banned in ("core.portfolio", "core.metrics", "core.ledger", "strategies."):
        assert f"import {banned}" not in text and f"from {banned}" not in text


def test_payload_carries_no_performance_fields():
    """Yükte getiri/R/PnL alanı YOKTUR: sayım bir koşu değildir."""
    frame = _crossing_frame(periods=1200)
    scan = scan_symbol(
        "BTC-USDT-SWAP",
        frame,
        start=pd.Timestamp("2021-01-01T00:00:00+00:00"),
        end=pd.Timestamp("2024-06-30T00:00:00+00:00"),
        atr_period=14,
        warmup_bars=600,
        min_gap_bars=MIN_GAP_BARS,
    )
    from scripts.measure_death_cross import payload

    text = repr(
        payload(
            [scan],
            start=pd.Timestamp("2022-01-01T00:00:00+00:00"),
            end=pd.Timestamp("2024-06-30T00:00:00+00:00"),
            warmup_bars=600,
            min_gap_bars=MIN_GAP_BARS,
            atr_period=14,
            timeframe="4H",
        )
    ).lower()
    for banned in ("pnl", "avg_r", "win_rate", "return", "profit", "drawdown", "sharpe"):
        assert banned not in text


def test_periods_are_the_candidate_definition_not_the_live_model():
    """50/200 aday tanımdır; `ema_trend`in 21/55'i ile karıştırılmaz."""
    assert (FAST_PERIOD, SLOW_PERIOD) == (50, 200)

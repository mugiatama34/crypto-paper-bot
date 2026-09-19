"""`scripts/measure_funding.py`nin SAF mantığı: ağ erişimi yok, defter yok.

Sınanan şey aracın üç sözleşmesidir: (1) ısınma penceresi cari kaydı dışlar ve dönem
başından önce alınır, (2) kümeleme kuralı ardışık damgaları tek olaya indirger,
(3) dönem A kesimi bir KAPIDIR — aşan bir `--end` hata koduyla biter.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from scripts.measure_funding import (
    BARS_PER_PERIOD,
    COOLDOWN_PERIODS,
    FUNDING_INTERVAL_HOURS,
    PERIODS_PER_YEAR,
    annualize,
    cluster,
    count_events,
    coverage,
    deannualize,
    distribution,
    flag_absolute,
    flag_relative,
    main,
    measure,
    monthly_histogram,
    rolling_quantile_threshold,
)


def _series(values, *, start="2022-01-01T00:00:00+00:00", name="BTC-USDT-SWAP") -> pd.Series:
    index = pd.date_range(start=start, periods=len(values), freq=f"{FUNDING_INTERVAL_HOURS}h", tz="UTC")
    index.name = "ts"
    return pd.Series(list(values), index=index, dtype="float64", name=name)


# --------------------------------------------------------------------------- #
# Yıllıklandırma
# --------------------------------------------------------------------------- #
def test_annualize_is_simple_scaling_not_compounding():
    assert annualize(0.0001) == pytest.approx(0.0001 * PERIODS_PER_YEAR)
    assert deannualize(annualize(0.00037)) == pytest.approx(0.00037)


def test_absolute_threshold_matches_annual_level():
    # %50 yıllık = 0.50 / 1095 periyot başına
    series = _series([deannualize(0.50) * 1.01, deannualize(0.50) * 0.99])
    flags = flag_absolute(series, annual=0.50, side="positive")
    assert list(flags) == [True, False]


# --------------------------------------------------------------------------- #
# (a) Kapsam
# --------------------------------------------------------------------------- #
def test_coverage_counts_from_symbols_own_first_record():
    """Geç listeleme bir veri boşluğu DEĞİLDİR: beklenen sayı ilk kayıttan sayılır."""
    series = _series([0.0001] * 10, start="2023-06-01T00:00:00+00:00")
    item = coverage(
        series, start=pd.Timestamp("2022-01-01T00:00:00+00:00"), end=pd.Timestamp("2024-06-30T00:00:00+00:00")
    )
    assert item.observed == 10
    assert item.expected == 10  # dönem başından değil, 2023-06-01'den
    assert item.missing == 0
    assert item.gaps == ()


def test_coverage_reports_interior_gap():
    series = _series([0.0001] * 6)
    series = series.drop(series.index[2:4])  # iki periyot eksik
    item = coverage(
        series, start=pd.Timestamp("2022-01-01T00:00:00+00:00"), end=pd.Timestamp("2024-06-30T00:00:00+00:00")
    )
    assert item.observed == 4
    assert item.expected == 6
    assert item.missing == 2
    assert len(item.gaps) == 1
    assert item.gaps[0][2] == 2


def test_coverage_empty_window_is_zero_not_error():
    series = _series([0.0001] * 4, start="2025-01-01T00:00:00+00:00")
    item = coverage(
        series, start=pd.Timestamp("2022-01-01T00:00:00+00:00"), end=pd.Timestamp("2024-06-30T00:00:00+00:00")
    )
    assert item.observed == 0 and item.first is None


# --------------------------------------------------------------------------- #
# (b) Dağılım
# --------------------------------------------------------------------------- #
def test_distribution_splits_tails_and_keeps_zero_separate():
    stats = distribution([0.001, 0.002, -0.003, 0.0, -0.001], label="x")
    assert (stats.positive, stats.negative, stats.zero) == (2, 2, 1)
    assert stats.positive_mean == pytest.approx(0.0015)
    assert stats.negative_mean == pytest.approx(-0.002)
    assert stats.max == pytest.approx(0.002)
    assert stats.min == pytest.approx(-0.003)


def test_distribution_empty_is_nan_not_zero():
    stats = distribution([], label="x")
    assert stats.count == 0
    assert stats.mean != stats.mean  # nan — "işlem yaptı ve sıfır çıktı" DEĞİL


# --------------------------------------------------------------------------- #
# (c) Göreli eşik: pencere cari kaydı DIŞLAR, dolmadan eşik üretmez
# --------------------------------------------------------------------------- #
def test_rolling_threshold_excludes_current_record():
    series = _series([1.0, 2.0, 3.0, 99.0])
    threshold = rolling_quantile_threshold(series, quantile=0.5, window=3)
    assert threshold.iloc[:2].isna().all()  # pencere dolmadı
    assert threshold.iloc[3] == pytest.approx(2.0)  # 99 kendi eşiğine GİRMEDİ


def test_partial_window_yields_no_event():
    series = _series([0.001] * 5)
    flags = flag_relative(series, quantile=0.9, window=10, side="positive")
    assert not flags.any()


def test_spike_cannot_define_its_own_threshold():
    """shift(1) olmasaydı yeterince büyük her sıçrama kendi p90'ını aşardı."""
    series = _series([0.0001] * 10 + [0.05])
    flags = flag_relative(series, quantile=0.9, window=10, side="positive")
    assert bool(flags.iloc[-1]) is True
    assert flags.iloc[:-1].sum() == 0


def test_negative_tail_is_mirrored_not_absolute():
    series = _series([0.0001] * 10 + [-0.05])
    positive = flag_relative(series, quantile=0.9, window=10, side="positive")
    negative = flag_relative(series, quantile=0.9, window=10, side="negative")
    assert bool(negative.iloc[-1]) is True
    assert not positive.any()  # negatif ekstrem pozitif kuyruğa sayılmaz


def test_unknown_side_raises():
    series = _series([0.0001] * 3)
    with pytest.raises(ValueError):
        flag_relative(series, quantile=0.9, window=2, side="both")
    with pytest.raises(ValueError):
        flag_absolute(series, annual=0.5, side="both")


# --------------------------------------------------------------------------- #
# Kümeleme
# --------------------------------------------------------------------------- #
def test_adjacent_stamps_collapse_into_one_episode():
    index = pd.date_range("2022-03-01T00:00:00+00:00", periods=3, freq="8h", tz="UTC")
    episodes = cluster(list(index), cooldown_periods=1)
    assert len(episodes) == 1
    assert episodes[0].stamps == 3
    assert episodes[0].bars == 3 * BARS_PER_PERIOD


def test_gap_splits_episodes_under_adjacent_rule():
    stamps = [
        pd.Timestamp("2022-03-01T00:00:00+00:00"),
        pd.Timestamp("2022-03-01T08:00:00+00:00"),
        pd.Timestamp("2022-03-02T08:00:00+00:00"),  # 24 saat sonra
    ]
    assert len(cluster(stamps, cooldown_periods=1)) == 2
    assert len(cluster(stamps, cooldown_periods=COOLDOWN_PERIODS)) == 1


def test_cooldown_must_be_at_least_one():
    with pytest.raises(ValueError):
        cluster([pd.Timestamp("2022-03-01T00:00:00+00:00")], cooldown_periods=0)


def test_clustering_is_per_symbol_not_pooled():
    """İki sembolde aynı saatteki ekstrem, aynı epizodun iki ayağı değildir."""
    stamp = pd.date_range("2022-03-01T00:00:00+00:00", periods=1, freq="8h", tz="UTC")
    flags = {
        "BTC-USDT-SWAP": pd.Series([True], index=stamp),
        "ETH-USDT-SWAP": pd.Series([True], index=stamp),
    }
    counts = count_events(flags, threshold="t", side="positive")
    assert counts.stamps == 2
    assert counts.episodes_adjacent == 2
    assert len(counts.per_symbol) == 2


def test_monthly_histogram_groups_by_calendar_month():
    stamps = [
        pd.Timestamp("2022-03-31T16:00:00+00:00"),
        pd.Timestamp("2022-04-01T00:00:00+00:00"),
        pd.Timestamp("2022-04-30T16:00:00+00:00"),
    ]
    assert monthly_histogram(stamps) == {"2022-03": 1, "2022-04": 2}


# --------------------------------------------------------------------------- #
# Isınma penceresi dönem A'nın ÖNCESİNDEN alınır, İÇİNDEN yenmez
# --------------------------------------------------------------------------- #
def test_warmup_comes_from_before_the_period_not_from_inside_it():
    start = pd.Timestamp("2022-01-01T00:00:00+00:00")
    end = pd.Timestamp("2022-01-02T00:00:00+00:00")
    # 10 ısınma kaydı dönem ÖNCESİNDE, sonra dönem içinde 3 kayıt (biri sıçrama)
    values = [0.0001] * 10 + [0.0001, 0.05, 0.0001]
    series = _series(values, start="2021-12-28T08:00:00+00:00")
    _, _, counts = measure({"BTC-USDT-SWAP": series}, start=start, end=end, window=10)
    relative = [c for c in counts if c.threshold.startswith("göreli p90") and c.side == "positive"]
    assert relative and relative[0].stamps == 1  # sıçrama sayıldı, ısınma kayıtları değil


def test_events_outside_the_period_are_not_counted():
    start = pd.Timestamp("2022-01-01T00:00:00+00:00")
    end = pd.Timestamp("2022-01-02T00:00:00+00:00")
    series = _series([0.0001] * 10 + [0.05] * 5, start="2021-12-20T00:00:00+00:00")
    _, _, counts = measure({"BTC-USDT-SWAP": series}, start=start, end=end, window=10)
    assert all(c.stamps == 0 for c in counts)  # sıçramaların hepsi dönem dışında


# --------------------------------------------------------------------------- #
# Dönem B KAPISI
# --------------------------------------------------------------------------- #
def test_end_beyond_period_a_cutoff_is_refused():
    code = main(["--end", "2025-01-01T00:00:00+00:00"])
    assert code == 2


def test_inverted_window_is_refused():
    code = main(["--start", "2024-01-01T00:00:00+00:00", "--end", "2023-01-01T00:00:00+00:00"])
    assert code == 2


def test_symbol_outside_universe_is_refused():
    code = main(["--symbols", "NOTALISTED-USDT-SWAP"])
    assert code == 2


# --------------------------------------------------------------------------- #
# Araç ölçümün parçası DEĞİL: yasak import yok
# --------------------------------------------------------------------------- #
def test_module_does_not_import_measurement_or_strategy_modules():
    """Araç ölçümün parçası değildir: R/PnL üreten modüllere erişimi hiç yoktur."""
    text = Path("scripts/measure_funding.py").read_text(encoding="utf-8")
    for banned in ("core.portfolio", "core.metrics", "core.ledger", "strategies."):
        assert f"import {banned}" not in text and f"from {banned}" not in text

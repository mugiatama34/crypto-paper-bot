"""core/metrics.py: R birinci sınıf mı, long/short ayrışıyor mu, tanımsız metrik sessizce sıfır mı oluyor.

Projenin ana sorusu long/short karşılaştırması olduğu için ayrıştırmanın ve R'nin
bileşiklenmeden bağımsızlığının testi, "metrik hesaplandı mı" testinden daha önemlidir.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import pytest

from core.config import load_config
from core.ledger import TRADE_COLUMNS, Ledger
from core.metrics import (
    acceptance_flags,
    account_stats,
    compare,
    direction_stats,
    cost_per_r,
    format_report,
    model_metrics,
    periods_per_year,
    pooled_direction_stats,
    r_multiple,
    return_correlation,
    stop_distance_pct,
)


def _trade(
    *, direction: str = "long", pnl: float, risk: float | str = 100.0, **overrides: Any
) -> dict[str, Any]:
    row: dict[str, Any] = {column: "" for column in TRADE_COLUMNS}
    row.update(
        strategy="m",
        symbol="BTC-USDT-SWAP",
        direction=direction,
        qty=1.0,
        notional=100.0,
        stop_price=95.0,
        risk_amount=risk,
        leverage=1.0,
        margin=100.0,
        fee=0.2,
        funding=0.0,
        pnl=pnl,
        exit_reason="stop",
    )
    row.update(overrides)
    return row


def _equity(*values: float) -> list[dict[str, Any]]:
    return [
        {"ts": f"2026-01-0{i + 1}T00:00:00+00:00", "cash": value, "margin_used": 0.0,
         "unrealized_pnl": 0.0, "equity": value, "open_positions": 0}
        for i, value in enumerate(values)
    ]


# --------------------------------------------------------------------------- #
# R katsayısı
# --------------------------------------------------------------------------- #
def test_r_is_pnl_over_the_risk_taken_at_entry() -> None:
    assert r_multiple(_trade(pnl=250.0, risk=100.0)) == pytest.approx(2.5)
    assert r_multiple(_trade(pnl=-100.0, risk=100.0)) == pytest.approx(-1.0)


def test_liquidation_costs_more_than_one_r() -> None:
    """Likidasyon marjın tamamını götürür: R -1'in altına iner ve bu görünür olmalı."""
    trade = _trade(pnl=-320.0, risk=100.0, exit_reason="liquidation")
    assert r_multiple(trade) == pytest.approx(-3.2)
    stats = direction_stats([trade])
    assert stats.liquidations == 1
    assert stats.avg_r == pytest.approx(-3.2)


def test_trade_without_risk_amount_is_counted_not_silently_zeroed() -> None:
    stats = direction_stats([_trade(pnl=50.0, risk=""), _trade(pnl=50.0, risk=100.0)])
    assert stats.trades == 2
    assert stats.unmeasured == 1
    assert stats.avg_r == pytest.approx(0.5)  # ölçülemeyen satır ortalamayı aşağı çekmez
    assert r_multiple(_trade(pnl=50.0, risk=0.0)) is None


# --------------------------------------------------------------------------- #
# Bileşiklenmeden bağımsızlık — R'nin birinci sınıf olma gerekçesi
# --------------------------------------------------------------------------- #
def test_average_r_is_independent_of_compounding() -> None:
    """Aynı sinyal kalitesi, farklı bileşiklenme: ortalama R aynı, toplam PnL farklı.

    Bileşiklenen model (hesabı büyüdüğü için pozisyonu da büyüyen) toplam getiride öne
    geçer; ölçmek istediğimiz sinyal kalitesi ise ikisinde de birebir aynıdır.
    """
    compounding = [
        _trade(pnl=50.0, risk=100.0),
        _trade(pnl=105.0, risk=210.0),   # hesap büyüdü, riske edilen tutar da büyüdü
        _trade(pnl=-220.0, risk=220.0),
    ]
    flat = [
        _trade(pnl=50.0, risk=100.0),
        _trade(pnl=50.0, risk=100.0),
        _trade(pnl=-100.0, risk=100.0),
    ]

    compounding_stats = direction_stats(compounding)
    flat_stats = direction_stats(flat)

    assert compounding_stats.avg_r == pytest.approx(flat_stats.avg_r)
    assert compounding_stats.total_r == pytest.approx(flat_stats.total_r)
    assert compounding_stats.pnl != pytest.approx(flat_stats.pnl)  # getiri ayrışır, R ayrışmaz


# --------------------------------------------------------------------------- #
# Long/short ayrıştırması (CLAUDE.md: opsiyonel değil)
# --------------------------------------------------------------------------- #
def test_long_and_short_are_measured_separately() -> None:
    trades = [
        _trade(direction="long", pnl=-100.0, risk=100.0),
        _trade(direction="long", pnl=-100.0, risk=100.0),
        _trade(direction="short", pnl=300.0, risk=100.0),
    ]
    metrics = model_metrics(
        "m", trades=trades, equity_rows=_equity(10_000.0, 10_100.0),
        initial_capital=10_000.0, periods_per_year=2190.0,
    )

    assert metrics.long.trades == 2 and metrics.long.avg_r == pytest.approx(-1.0)
    assert metrics.short.trades == 1 and metrics.short.avg_r == pytest.approx(3.0)
    # Toplam, ayrışmanın yerine geçmez: birleşik değer shortun başarısını gizlerdi.
    assert metrics.total.trades == 3
    assert metrics.total.avg_r == pytest.approx(1.0 / 3.0)


def test_win_rate_and_payoff_are_split_per_direction() -> None:
    trades = [
        _trade(direction="short", pnl=200.0, risk=100.0),
        _trade(direction="short", pnl=-100.0, risk=100.0),
        _trade(direction="long", pnl=-100.0, risk=100.0),
    ]
    short = direction_stats(trades, direction="short")
    assert short.win_rate == pytest.approx(0.5)
    assert short.avg_win_r == pytest.approx(2.0)
    assert short.avg_loss_r == pytest.approx(-1.0)
    assert short.profit_factor == pytest.approx(2.0)

    long_stats = direction_stats(trades, direction="long")
    assert long_stats.win_rate == pytest.approx(0.0)
    assert math.isnan(long_stats.avg_win_r)  # kazanan işlem yok: uydurulmuş 0.0 değil


def test_max_drawdown_r_follows_the_cumulative_r_curve() -> None:
    stats = direction_stats([
        _trade(pnl=200.0, risk=100.0),   # +2R, zirve
        _trade(pnl=-100.0, risk=100.0),  # -1R
        _trade(pnl=-50.0, risk=100.0),   # -0.5R -> zirveden 1.5R düşüş
        _trade(pnl=100.0, risk=100.0),
    ])
    assert stats.max_drawdown_r == pytest.approx(-1.5)
    assert stats.total_r == pytest.approx(1.5)


# --------------------------------------------------------------------------- #
# Tanımsız metrikler
# --------------------------------------------------------------------------- #
def test_empty_history_reports_nan_not_zero() -> None:
    stats = direction_stats([])
    assert stats.trades == 0
    for value in (stats.avg_r, stats.total_r, stats.win_rate, stats.r_sharpe):
        assert math.isnan(value), "ölçülemedi ile sıfır çıktı aynı sayıya indirgenmemeli"


def test_single_trade_has_no_r_sharpe() -> None:
    assert math.isnan(direction_stats([_trade(pnl=100.0)]).r_sharpe)


# --------------------------------------------------------------------------- #
# Hesap düzeyi
# --------------------------------------------------------------------------- #
def test_account_stats_from_the_equity_curve() -> None:
    stats = account_stats(
        _equity(10_000.0, 11_000.0, 8_800.0, 9_900.0),
        initial_capital=10_000.0,
        periods_per_year=2190.0,
    )
    assert stats.final_equity == pytest.approx(9_900.0)
    assert stats.total_return == pytest.approx(-0.01)
    assert stats.max_drawdown == pytest.approx(-0.2)  # 11_000 -> 8_800
    assert stats.bars == 4


def test_account_stats_without_history_is_undefined_not_zero() -> None:
    stats = account_stats([], initial_capital=10_000.0, periods_per_year=2190.0)
    assert stats.bars == 0 and math.isnan(stats.sharpe) and math.isnan(stats.total_return)


def test_periods_per_year_follows_the_configured_timeframe() -> None:
    assert periods_per_year(load_config()) == pytest.approx(365 * 24 / 4)


# --------------------------------------------------------------------------- #
# Rapor
# --------------------------------------------------------------------------- #
def _metrics(model: str, trades: list[dict[str, Any]], final: float) -> Any:
    return model_metrics(
        model, trades=trades, equity_rows=_equity(10_000.0, final),
        initial_capital=10_000.0, periods_per_year=2190.0,
    )


def test_report_puts_r_before_total_return() -> None:
    report = format_report([_metrics("m", [_trade(pnl=100.0)], 10_100.0)])
    assert report.index("ort.R") < report.index("PnL(USDT)")
    assert "long" in report and "short" in report and "TOPLAM" in report
    assert "getiri" in report  # toplam getiri atılmaz, ikinci sırada durur


def test_report_ranks_by_average_r_not_by_total_return() -> None:
    """Toplam getiriye göre sıralamak, ayıklamaya çalıştığımız bileşiklenme etkisini geri sokardı."""
    sharp = _metrics("keskin", [_trade(pnl=300.0, risk=100.0)], 10_300.0)          # 3.0R
    lucky = _metrics("bileşiklenen", [_trade(pnl=1_000.0, risk=2_000.0)], 11_000.0)  # 0.5R
    report = format_report([lucky, sharp])
    assert report.index("keskin") < report.index("bileşiklenen")


def test_report_warns_about_unmeasurable_trades() -> None:
    report = format_report([_metrics("m", [_trade(pnl=100.0, risk="")], 10_100.0)])
    assert "risk_amount yok" in report


def test_report_renders_undefined_metrics_as_a_dash() -> None:
    assert "—" in format_report([_metrics("m", [], 10_000.0)])


# --------------------------------------------------------------------------- #
# Defterden okuma (salt okunur)
# --------------------------------------------------------------------------- #
def test_compare_reads_the_ledger_without_changing_it(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path)
    ledger.initialize_model("alpha", initial_capital=10_000.0)
    ledger.append_trades("alpha", [_trade(direction="short", pnl=150.0, risk=100.0)])
    ledger.append_equity("alpha", _equity(10_000.0, 10_150.0))
    before = (tmp_path / "alpha" / "trades.csv").read_text()

    (metrics,) = compare(["alpha"], ledger=ledger, config=load_config())

    assert metrics.model == "alpha"
    assert metrics.short.avg_r == pytest.approx(1.5)
    assert metrics.long.trades == 0
    assert metrics.account.final_equity == pytest.approx(10_150.0)
    assert (tmp_path / "alpha" / "trades.csv").read_text() == before


def test_compare_handles_a_model_that_never_traded(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path)
    ledger.initialize_model("sessiz", initial_capital=10_000.0)
    (metrics,) = compare(["sessiz"], ledger=ledger, config=load_config())
    assert metrics.total.trades == 0 and math.isnan(metrics.total.avg_r)


# --------------------------------------------------------------------------- #
# Maliyet ölçeği kolonları (CLAUDE.md > Rapor Kolonları)
# --------------------------------------------------------------------------- #
def test_stop_distance_and_cost_per_r_are_computed_per_trade() -> None:
    row = _trade(pnl=100.0, risk=100.0, entry_price=100.0, stop_price=95.0,
                 fee=2.0, slippage_cost=1.5)
    assert stop_distance_pct(row) == pytest.approx(5.0)
    assert cost_per_r(row) == pytest.approx(0.035)


def test_cost_per_r_exposes_the_tight_stop_penalty() -> None:
    """Dar stop kuran model aynı 1R'yi daha büyük notional ile taşır, R başına daha çok öder.

    Ana soruyu kirleten etki tam olarak budur: iki model eşit R üretse bile, stopu dar olan
    maliyet yüzünden geride kalır — bu bir sinyal farkı değildir ve tabloda görünmelidir.
    """
    wide = direction_stats([
        _trade(pnl=0.0, risk=100.0, entry_price=100.0, stop_price=95.0, fee=2.0, slippage_cost=1.0)
    ])
    tight = direction_stats([
        _trade(pnl=0.0, risk=100.0, entry_price=100.0, stop_price=99.0, fee=10.0, slippage_cost=5.0)
    ])
    assert wide.avg_stop_distance_pct == pytest.approx(5.0)
    assert tight.avg_stop_distance_pct == pytest.approx(1.0)
    assert tight.cost_per_r > wide.cost_per_r * 4


def test_cost_columns_are_split_per_direction() -> None:
    trades = [
        _trade(direction="long", pnl=0.0, risk=100.0, entry_price=100.0, stop_price=90.0,
               fee=1.0, slippage_cost=0.0),
        _trade(direction="short", pnl=0.0, risk=100.0, entry_price=100.0, stop_price=102.0,
               fee=5.0, slippage_cost=0.0),
    ]
    assert direction_stats(trades, direction="long").avg_stop_distance_pct == pytest.approx(10.0)
    assert direction_stats(trades, direction="short").avg_stop_distance_pct == pytest.approx(2.0)
    assert direction_stats(trades, direction="long").cost_per_r == pytest.approx(0.01)
    assert direction_stats(trades, direction="short").cost_per_r == pytest.approx(0.05)


def test_a_direction_with_no_trades_reports_nan_cost_not_zero() -> None:
    """0.0 yazmak, hiç short açmamış modeli 'maliyetsiz short yapan model' gibi gösterirdi."""
    stats = direction_stats([_trade(direction="long", pnl=50.0)], direction="short")
    assert stats.trades == 0
    assert math.isnan(stats.cost_per_r) and math.isnan(stats.avg_stop_distance_pct)


def test_report_shows_the_cost_scale_columns() -> None:
    report = format_report([_metrics("m", [
        _trade(pnl=100.0, risk=100.0, entry_price=100.0, stop_price=95.0,
               fee=2.0, slippage_cost=1.0)
    ], 10_100.0)])
    assert "stopMes.%" in report and "maliyet/R" in report
    assert report.index("maliyet/R") < report.index("PnL(USDT)")  # maliyet ölçeği getiriden önce


def test_direction_r_series_follows_close_order() -> None:
    """Yön bazlı R-Sharpe kapanış sırasına göre dizilmiş R dizisinden gelir."""
    late = _trade(pnl=100.0, risk=100.0, closed_at="2026-01-02T00:00:00+00:00")
    early = _trade(pnl=-50.0, risk=100.0, closed_at="2026-01-01T00:00:00+00:00")
    shuffled = direction_stats([late, early])
    ordered = direction_stats([early, late])
    assert shuffled.max_drawdown_r == pytest.approx(ordered.max_drawdown_r)
    assert ordered.max_drawdown_r == pytest.approx(-0.5)  # önce -0.5R, sonra toparlıyor


# --------------------------------------------------------------------------- #
# Referans (benchmark) satırı (CLAUDE.md kural 15)
# --------------------------------------------------------------------------- #
def _benchmark_metrics(**overrides: Any) -> Any:
    """Stop'suz, R'siz bir referans modelin metrikleri."""
    trades = [
        _trade(direction="long", pnl=500.0, risk="", stop_price="", entry_price=100.0),
    ]
    payload: dict[str, Any] = dict(
        trades=trades,
        equity_rows=[{"equity": 10000.0}, {"equity": 10500.0}],
        initial_capital=10000.0,
        periods_per_year=periods_per_year(load_config()),
        is_benchmark=True,
    )
    payload.update(overrides)
    return model_metrics("buyhold", **payload)


def test_benchmark_cost_columns_are_nan() -> None:
    stats = _benchmark_metrics().total
    assert math.isnan(stats.avg_stop_distance_pct)
    assert math.isnan(stats.cost_per_r)


def test_benchmark_cost_columns_stay_nan_even_with_a_stopped_row() -> None:
    """Garanti defterin içeriğine bırakılmaz: bayrak koşulsuz kazanır."""
    stats = _benchmark_metrics(
        trades=[_trade(direction="long", pnl=50.0, risk=100.0, entry_price=100.0)]
    ).total
    assert math.isnan(stats.avg_stop_distance_pct)
    assert math.isnan(stats.cost_per_r)


def test_competitor_cost_columns_are_still_measured() -> None:
    """Bayrak yalnızca referansı susturur, yarışmacıyı değil.

    Aynı stop'lu satır: is_benchmark=True iken nan, False iken ölçülür.
    """
    stopped = [_trade(direction="long", pnl=50.0, risk=100.0, entry_price=100.0)]
    competitor = _benchmark_metrics(trades=stopped, is_benchmark=False).total
    reference = _benchmark_metrics(trades=stopped, is_benchmark=True).total

    assert competitor.avg_stop_distance_pct == pytest.approx(5.0)  # |100-95|/100
    assert not math.isnan(competitor.cost_per_r)
    assert math.isnan(reference.avg_stop_distance_pct)


def test_benchmark_account_return_is_still_reported() -> None:
    """Çıpanın tek işi budur: hesap getirisi zemin olarak durmalı."""
    account = _benchmark_metrics().account
    assert account.total_return == pytest.approx(0.05)


def test_benchmark_is_reported_outside_the_ranking() -> None:
    competitor = model_metrics(
        "model_a",
        trades=[_trade(direction="long", pnl=50.0)],
        equity_rows=[{"equity": 10000.0}, {"equity": 10050.0}],
        initial_capital=10000.0,
        periods_per_year=periods_per_year(load_config()),
    )
    report = format_report([_benchmark_metrics(), competitor])

    assert "REFERANS" in report
    # Referans, ortalama R'si nan olmasına rağmen yarışmacının üstüne çıkmaz.
    assert report.index("model_a") < report.index("REFERANS") < report.index("buyhold")


def test_benchmark_has_no_unmeasured_warning() -> None:
    """Referansta R'siz satır beklenendir; uyarı gerçek anomaliler için saklanır."""
    assert "UYARI" not in format_report([_benchmark_metrics()])


def test_competitor_keeps_the_unmeasured_warning() -> None:
    report = format_report([_benchmark_metrics(is_benchmark=False)])
    assert "UYARI" in report


def test_compare_marks_named_benchmarks(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path)
    for model in ("buyhold", "model_a"):
        ledger.reset_model(model, initial_capital=10000.0)
        ledger.append_trades(model, [_trade(direction="long", pnl=50.0)])

    results = compare(
        ["buyhold", "model_a"], ledger=ledger, config=load_config(), benchmarks=["buyhold"]
    )
    marked = {item.model: item.is_benchmark for item in results}
    assert marked == {"buyhold": True, "model_a": False}
    assert math.isnan(results[0].total.cost_per_r)
    assert not math.isnan(results[1].total.cost_per_r)


# --------------------------------------------------------------------------- #
# Havuzlanmış yön karşılaştırması (dashboard'un üst paneli)
# --------------------------------------------------------------------------- #
def test_pooled_stats_weight_every_trade_not_every_model() -> None:
    """Havuz işleme oy verir: 1 işlemlik bir model 10 işlemlik bir modeli dengeleyemez."""
    pooled = pooled_direction_stats({
        "az": [_trade(direction="long", pnl=1000.0, risk=100.0)],           # +10R
        "cok": [_trade(direction="long", pnl=-100.0, risk=100.0) for _ in range(10)],  # 10 × -1R
    })
    # Model ortalamalarının ortalaması (+10 ile -1) +4.5 olurdu; havuz 11 işleme bakar.
    assert pooled["long"].trades == 11
    assert pooled["long"].avg_r == pytest.approx((10.0 - 10.0) / 11.0)


def test_pooled_stats_keep_the_directions_apart() -> None:
    pooled = pooled_direction_stats({
        "a": [_trade(direction="long", pnl=100.0, risk=100.0, funding=-3.0)],
        "b": [_trade(direction="short", pnl=-50.0, risk=100.0, funding=5.0)],
    })
    assert pooled["long"].avg_r == pytest.approx(1.0)
    assert pooled["short"].avg_r == pytest.approx(-0.5)
    assert pooled["long"].funding == pytest.approx(-3.0)
    assert pooled["short"].funding == pytest.approx(5.0)
    assert pooled["total"].trades == 2


def test_pooled_stats_with_no_trades_are_nan_not_zero() -> None:
    pooled = pooled_direction_stats({"a": []})
    assert math.isnan(pooled["short"].avg_r)
    assert pooled["short"].trades == 0


# --------------------------------------------------------------------------- #
# Kabul çıtası (üç bayrak)
# --------------------------------------------------------------------------- #
def _competitor(
    model: str, *, avg_r_trades: list[dict[str, Any]], final: float = 10_100.0
) -> Any:
    return model_metrics(
        model, trades=avg_r_trades, equity_rows=_equity(10_000.0, final),
        initial_capital=10_000.0, periods_per_year=2190.0,
    )


def _winner(model: str, *, n: int = 40, pnl: float = 50.0, final: float = 12_000.0) -> Any:
    return _competitor(
        model,
        avg_r_trades=[
            _trade(pnl=pnl, risk=100.0, entry_price=100.0, stop_price=97.0,
                   closed_at=f"2026-01-{index + 1:02d}T00:00:00+00:00")
            for index in range(n)
        ],
        final=final,
    )


def _flags(metrics: list[Any], **overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = dict(
        min_trades=30, stop_band_ratio=2.5, control_model="ctrl", edge_margin_r=0.15
    )
    payload.update(overrides)
    return {item.model: item for item in acceptance_flags(metrics, **payload)}


def test_acceptance_needs_both_gates() -> None:
    flags = _flags([
        _winner("good"),
        _competitor("ctrl", avg_r_trades=[
            _trade(pnl=-10.0, risk=100.0, entry_price=100.0, stop_price=97.0) for _ in range(30)
        ], final=9_800.0),
        model_metrics("bench", trades=[], equity_rows=_equity(10_000.0, 10_500.0),
                      initial_capital=10_000.0, periods_per_year=2190.0, is_benchmark=True),
    ])
    assert flags["good"].sample and flags["good"].edge
    assert flags["good"].passed
    # Kontrol grubu kendini geçemez: kendisiyle arasındaki fark 0, gereken marj 0.15.
    assert not flags["ctrl"].edge


def test_small_sample_fails_even_with_a_great_average() -> None:
    flags = _flags([_winner("tiny", n=3, final=15_000.0)])
    assert not flags["tiny"].sample
    assert not flags["tiny"].passed
    assert flags["tiny"].measured_trades == 3


def test_sample_gate_uses_the_configured_threshold() -> None:
    """Eşik config'ten gelir; 29 işlem 30'luk çıtayı geçmez, 30 geçer."""
    assert not _flags([_winner("m", n=29)])["m"].sample
    assert _flags([_winner("m", n=30)])["m"].sample


def test_benchmarks_get_no_acceptance_row() -> None:
    """Çıpa yarışmacı değildir (kural 15): ölçmediği bir yarışta not almaz."""
    flags = _flags([
        _winner("good"),
        model_metrics("bench", trades=[], equity_rows=_equity(10_000.0, 10_500.0),
                      initial_capital=10_000.0, periods_per_year=2190.0, is_benchmark=True),
    ])
    assert "bench" not in flags


def test_band_warns_about_a_model_outside_the_stop_scale() -> None:
    """Bandın çapası yarışmacı medyanıdır; çok dar stop kuran model bandın dışına düşer."""
    wide = [
        _competitor(f"wide{index}", avg_r_trades=[
            _trade(pnl=10.0, risk=100.0, entry_price=100.0, stop_price=97.0)
        ]) for index in range(3)
    ]
    tight = _competitor("tight", avg_r_trades=[
        _trade(pnl=10.0, risk=100.0, entry_price=100.0, stop_price=99.9)  # %0.1 stop
    ])
    flags = _flags([*wide, tight])
    assert flags["wide0"].band
    assert not flags["tight"].band
    assert flags["tight"].band_low == pytest.approx(3.0 / math.sqrt(2.5))


def test_band_is_a_warning_not_a_gate() -> None:
    """Bandın dışında kalmak bir KUSUR değil kıyas koşuludur: doğrulamayı engellemez."""
    wide = [
        _competitor(f"wide{index}", avg_r_trades=[
            _trade(pnl=10.0, risk=100.0, entry_price=100.0, stop_price=97.0)
        ]) for index in range(3)
    ]
    # İki kapıyı da geçen ama stop'u bandın çok dışında (çok dar) bir model.
    tight = _competitor("tight", avg_r_trades=[
        _trade(pnl=50.0, risk=100.0, entry_price=100.0, stop_price=99.9,
               closed_at=f"2026-01-{index + 1:02d}T00:00:00+00:00")
        for index in range(40)
    ], final=12_000.0)
    flags = _flags([*wide, tight])
    assert flags["tight"].band is False
    assert flags["tight"].sample and flags["tight"].edge
    assert flags["tight"].passed  # band `passed`'a GİRMEZ


def test_edge_needs_the_configured_margin_over_the_control() -> None:
    """Kontrolü kıl payı geçmek yetmez: çekilişin kendi gürültüsü o farkı üretebilir."""
    control = _competitor("ctrl", avg_r_trades=[
        _trade(pnl=20.0, risk=100.0, entry_price=100.0, stop_price=97.0,
               closed_at=f"2026-01-{index + 1:02d}T00:00:00+00:00")
        for index in range(40)
    ], final=10_800.0)                      # kontrolün ort. R'si +0.20
    barely = _winner("barely", pnl=30.0)    # +0.30 → fark 0.10, marj 0.15
    clearly = _winner("clearly", pnl=40.0)  # +0.40 → fark 0.20

    flags = _flags([control, barely, clearly])
    assert flags["barely"].control_avg_r == pytest.approx(0.20)
    assert not flags["barely"].edge
    assert flags["clearly"].edge
    assert flags["barely"].edge_margin_r == pytest.approx(0.15)


def test_edge_margin_is_a_minimum_not_a_strict_excess() -> None:
    """Eşik "en az bu kadar"dır (`>=`), "bundan fazla" değil.

    Sınırın ULP düzeyinde test edilmesi anlamsız olurdu: karşılaştırma iki kayan noktalı
    ORTALAMANIN farkı üzerinden yapılır ve 0.15 ile 0.1499999999999999 arasındaki ayrım
    gürültünün altındadır. Test bu yüzden eşiğin iki yanını açıkça ayrı noktalardan
    yoklar; koda yapay bir tolerans eklemek, olmayan bir hassasiyeti iddia etmek olurdu.
    """
    control = _competitor("ctrl", avg_r_trades=[
        _trade(pnl=10.0, risk=100.0, entry_price=100.0, stop_price=97.0,
               closed_at=f"2026-01-{index + 1:02d}T00:00:00+00:00")
        for index in range(40)
    ], final=10_400.0)                                    # kontrol +0.10
    flags = _flags([control, _winner("uzak", pnl=26.0)])   # +0.26 → fark 0.16 >= 0.15
    assert flags["uzak"].edge
    flags = _flags([control, _winner("yakin", pnl=24.0)])  # +0.24 → fark 0.14 < 0.15
    assert not flags["yakin"].edge


def test_edge_requires_beating_the_benchmark_return() -> None:
    """Ortalama R pozitif ama piyasa daha çok kazandırdıysa edge yanmaz (kural 15)."""
    flags = _flags([
        _winner("beaten", final=10_100.0),
        model_metrics("bench", trades=[], equity_rows=_equity(10_000.0, 13_000.0),
                      initial_capital=10_000.0, periods_per_year=2190.0, is_benchmark=True),
    ])
    assert flags["beaten"].sample and flags["beaten"].band
    assert not flags["beaten"].edge
    assert flags["beaten"].benchmark_return == pytest.approx(0.30)


def test_missing_control_is_warned_not_silently_passed(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level("WARNING"):
        flags = _flags([_winner("solo")], control_model="yok")
    assert math.isnan(flags["solo"].control_avg_r)
    assert "yok" in caplog.text


def test_control_with_no_trades_does_not_block_the_margin() -> None:
    """Kontrolün ölçülebilir R'si yoksa marj uygulanamaz; koşul düşer ama sessizce değil."""
    flags = _flags([
        _winner("m"),
        _competitor("ctrl", avg_r_trades=[]),
    ])
    assert math.isnan(flags["m"].control_avg_r)
    assert flags["m"].edge


def test_highest_benchmark_sets_the_floor() -> None:
    """Birden çok çıpa varsa en yükseği zemindir: kolay olanı seçmek çıtayı indirirdi."""
    flags = _flags([
        _winner("m", final=11_000.0),
        model_metrics("low", trades=[], equity_rows=_equity(10_000.0, 10_050.0),
                      initial_capital=10_000.0, periods_per_year=2190.0, is_benchmark=True),
        model_metrics("high", trades=[], equity_rows=_equity(10_000.0, 12_000.0),
                      initial_capital=10_000.0, periods_per_year=2190.0, is_benchmark=True),
    ])
    assert flags["m"].benchmark_return == pytest.approx(0.20)
    assert not flags["m"].edge


# --------------------------------------------------------------------------- #
# Modeller arası getiri korelasyonu
# --------------------------------------------------------------------------- #
def test_identical_curves_correlate_perfectly() -> None:
    result = return_correlation({"a": _equity(100.0, 110.0, 99.0, 120.0),
                                 "b": _equity(100.0, 110.0, 99.0, 120.0)})
    assert result["models"] == ["a", "b"]
    assert result["matrix"][0][1] == pytest.approx(1.0)


def test_mirrored_curves_correlate_negatively() -> None:
    # Getiriler birebir zıt: +10/-10/+20% ile -10/+10/-20%.
    result = return_correlation({"up": _equity(100.0, 110.0, 99.0, 118.8),
                                 "down": _equity(100.0, 90.0, 99.0, 79.2)})
    assert result["matrix"][0][1] == pytest.approx(-1.0)


def test_short_overlap_is_nan_not_zero() -> None:
    """0.0 'ilişkisiz' demektir; ölçülemeyen bir ilişkiyi öyle göstermek yanıltır."""
    result = return_correlation({"a": _equity(100.0, 110.0), "b": _equity(100.0, 90.0)})
    index = result["models"].index("a")
    other = result["models"].index("b")
    assert math.isnan(result["matrix"][index][other])
    assert result["overlap"][index][other] == 1


def test_overlap_is_computed_pairwise_not_globally() -> None:
    """Yeni eklenen kısa geçmişli bir model, DİĞER çiftlerin örneklemini kırpmaz."""
    long_rows = _equity(100.0, 102.0, 104.0, 103.0, 106.0)
    result = return_correlation({
        "a": long_rows,
        "b": long_rows,
        "yeni": long_rows[-2:],
    })
    a, b, yeni = (result["models"].index(name) for name in ("a", "b", "yeni"))
    assert result["overlap"][a][b] == 4
    assert result["overlap"][a][yeni] < result["overlap"][a][b]


def test_flat_curve_has_no_correlation() -> None:
    result = return_correlation({"flat": _equity(100.0, 100.0, 100.0, 100.0),
                                 "moving": _equity(100.0, 110.0, 99.0, 120.0)})
    index = result["models"].index("flat")
    assert math.isnan(result["matrix"][index][result["models"].index("moving")])

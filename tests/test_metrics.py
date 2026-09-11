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
    account_stats,
    compare,
    direction_stats,
    format_report,
    model_metrics,
    periods_per_year,
    r_multiple,
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

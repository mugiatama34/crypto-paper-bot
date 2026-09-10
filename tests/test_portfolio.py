"""core/portfolio.py: boyutlandırma, kaldıraç tavanı, likidasyon sırası ve nakit muhasebesi.

Bu testler ölçümün adilliğini koruyan sayısal kuralları çiviler: %1 risk gerçekten %1 mi,
kaldıraç tavanı gerçekten aşılmıyor mu, likidasyon gerçekten stop'tan önce mi geliyor.
"""

from __future__ import annotations

from typing import Any

import pandas as pd
import pytest

from core.config import load_config
from core.portfolio import Bar, Portfolio, liquidation_price, size_position
from strategies.base import TakeProfit

TS = pd.Timestamp("2026-01-01 00:00:00", tz="UTC")
SYMBOL = "BTC-USDT-SWAP"


def _config(**overrides: Any) -> dict[str, Any]:
    """Depo config'i + testin izole etmek istediği alanlar.

    Maliyetleri sıfırlamak sayıyı elle doğrulanabilir kılar; maliyetlerin kendisi ayrı
    testlerde (gerçek config değerleriyle) sınanır.
    """
    config = load_config()
    config.update(overrides)
    return config


def _frictionless(**overrides: Any) -> dict[str, Any]:
    return _config(fee_rate=0.0, slippage_long=0.0, slippage_short_stop=0.0, **overrides)


def _bar(open_: float, high: float, low: float, close: float) -> Bar:
    return Bar(open=open_, high=high, low=low, close=close)


# --------------------------------------------------------------------------- #
# Boyutlandırma (CLAUDE.md kural 11)
# --------------------------------------------------------------------------- #
def test_long_position_risks_exactly_risk_per_trade() -> None:
    portfolio = Portfolio(_frictionless())
    result = portfolio.open_position(
        "m", symbol=SYMBOL, direction="long", stop_price=95.0,
        reference_price=100.0, ts=TS, marks={SYMBOL: 100.0},
    )
    position = result.position
    assert position is not None
    # 10000 × %1 = 100 birim risk, 5 birimlik stop mesafesi -> 20 adet
    assert position.qty == pytest.approx(20.0)

    trades = portfolio.process_bar("m", ts=TS, bars={SYMBOL: _bar(100.0, 101.0, 95.0, 96.0)})
    assert [trade.exit_reason for trade in trades] == ["stop"]
    assert trades[0].pnl == pytest.approx(-100.0)
    assert portfolio.cash("m") == pytest.approx(9900.0)


def test_short_position_risks_exactly_risk_per_trade() -> None:
    portfolio = Portfolio(_frictionless())
    result = portfolio.open_position(
        "m", symbol=SYMBOL, direction="short", stop_price=105.0,
        reference_price=100.0, ts=TS, marks={SYMBOL: 100.0},
    )
    position = result.position
    assert position is not None
    assert position.qty == pytest.approx(20.0)

    trades = portfolio.process_bar("m", ts=TS, bars={SYMBOL: _bar(100.0, 105.0, 99.0, 104.0)})
    assert [trade.exit_reason for trade in trades] == ["stop"]
    assert trades[0].pnl == pytest.approx(-100.0)
    assert portfolio.cash("m") == pytest.approx(9900.0)


def test_long_and_short_are_sized_symmetrically() -> None:
    """Aynı stop mesafesinde long ve short aynı boyutu alır — ana sorunun ön koşulu."""
    portfolio = Portfolio(_frictionless())
    long_result = portfolio.open_position(
        "m", symbol=SYMBOL, direction="long", stop_price=98.0,
        reference_price=100.0, ts=TS, marks={SYMBOL: 100.0},
    )
    short_result = portfolio.open_position(
        "m", symbol="ETH-USDT-SWAP", direction="short", stop_price=102.0,
        reference_price=100.0, ts=TS, marks={SYMBOL: 100.0},
    )
    assert long_result.position is not None and short_result.position is not None
    assert long_result.position.qty == pytest.approx(short_result.position.qty)


def test_risk_follows_equity_not_initial_capital() -> None:
    portfolio = Portfolio(_frictionless())
    account = portfolio.account("m")
    account.cash = 5000.0  # hesap yarıya indi
    result = portfolio.open_position(
        "m", symbol=SYMBOL, direction="long", stop_price=95.0,
        reference_price=100.0, ts=TS, marks={SYMBOL: 100.0},
    )
    assert result.position is not None
    assert result.position.qty == pytest.approx(10.0)  # 5000 × %1 / 5


# --------------------------------------------------------------------------- #
# Kaldıraç tavanı
# --------------------------------------------------------------------------- #
def test_sizing_never_exceeds_leverage_cap() -> None:
    sizing = size_position(
        equity=10_000.0, free_cash=10_000.0, entry_price=100.0, stop_price=99.9,
        risk_per_trade=0.01, leverage_cap=5.0,
    )
    # Kırpılmasa gereken notional 100_000 (kaldıraç 10) olurdu.
    assert sizing.clipped is True
    assert sizing.leverage == pytest.approx(5.0)
    assert sizing.notional == pytest.approx(50_000.0)
    assert sizing.qty == pytest.approx(500.0)
    assert sizing.margin == pytest.approx(10_000.0)
    assert "leverage_cap" in sizing.note


def test_position_is_shrunk_not_skipped_when_cap_binds() -> None:
    """Kural 11: tavana takılan işlem ATLANMAZ, küçültülür ve deftere not düşülür."""
    portfolio = Portfolio(_frictionless())
    result = portfolio.open_position(
        "m", symbol=SYMBOL, direction="long", stop_price=99.9,
        reference_price=100.0, ts=TS, marks={SYMBOL: 100.0}, reason="dar stop",
    )
    position = result.position
    assert position is not None, result.rejected
    assert position.qty * position.entry_price <= 5.0 * 10_000.0 + 1e-9
    assert position.leverage == pytest.approx(5.0)
    assert "leverage_cap" in position.notes

    trades = portfolio.process_bar("m", ts=TS, bars={SYMBOL: _bar(100.0, 100.2, 99.9, 100.0)})
    assert trades[0].notes == position.notes  # not defterdeki satıra taşınır


def test_leverage_cap_holds_with_real_costs() -> None:
    portfolio = Portfolio(_config())
    result = portfolio.open_position(
        "m", symbol=SYMBOL, direction="short", stop_price=100.05,
        reference_price=100.0, ts=TS, marks={SYMBOL: 100.0},
    )
    position = result.position
    assert position is not None, result.rejected
    assert position.leverage <= 5.0
    assert position.qty * position.entry_price <= 5.0 * 10_000.0 + 1e-9
    assert portfolio.cash("m") >= 0.0  # marj + komisyon nakdi aşamaz


def test_open_rejected_when_no_cash_left() -> None:
    portfolio = Portfolio(_frictionless())
    portfolio.account("m").cash = 0.0
    result = portfolio.open_position(
        "m", symbol=SYMBOL, direction="long", stop_price=95.0,
        reference_price=100.0, ts=TS, marks={},
    )
    assert result.position is None
    assert "boyut sıfır" in result.rejected


def test_sizing_rejects_zero_stop_distance() -> None:
    with pytest.raises(ValueError, match="sıfıra bölün"):
        size_position(
            equity=10_000.0, free_cash=10_000.0, entry_price=100.0, stop_price=100.0,
            risk_per_trade=0.01, leverage_cap=5.0,
        )


# --------------------------------------------------------------------------- #
# Likidasyon: stop'tan ÖNCE
# --------------------------------------------------------------------------- #
def test_liquidation_triggers_before_stop_in_the_same_bar() -> None:
    """Aynı mumda hem likidasyon hem stop seviyesi vurulduysa likidasyon kazanır.

    Senaryo bilerek "stop daha yakın" kurulur (long stop 99.9, likidasyon 80.5): fiyat
    yolunda önce stop'a değmiş olsa bile kural gereği likidasyon gerçekleşmiş sayılır —
    tersi, yüksek kaldıraçlı modellere gerçekte olmayan bir kurtulma şansı verirdi.
    """
    portfolio = Portfolio(_frictionless())
    result = portfolio.open_position(
        "m", symbol=SYMBOL, direction="long", stop_price=99.9,
        reference_price=100.0, ts=TS, marks={SYMBOL: 100.0},
    )
    position = result.position
    assert position is not None
    assert position.leverage == pytest.approx(5.0)
    assert position.liq_price == pytest.approx(80.5)
    assert position.stop_price > position.liq_price

    trades = portfolio.process_bar("m", ts=TS, bars={SYMBOL: _bar(100.0, 100.0, 80.0, 85.0)})
    assert [trade.exit_reason for trade in trades] == ["liquidation"]
    # Likide olan pozisyon marjın tamamını kaybeder: hesapta nakit kalmaz.
    assert trades[0].pnl == pytest.approx(-10_000.0)
    assert portfolio.cash("m") == pytest.approx(0.0)


def test_short_liquidation_uses_bar_high() -> None:
    portfolio = Portfolio(_frictionless())
    result = portfolio.open_position(
        "m", symbol=SYMBOL, direction="short", stop_price=100.1,
        reference_price=100.0, ts=TS, marks={SYMBOL: 100.0},
    )
    position = result.position
    assert position is not None
    assert position.liq_price == pytest.approx(119.5)

    # Kapanış girişin altında (kâğıt üzerinde kârda) ama mum içi zirve likidasyonu vurdu.
    trades = portfolio.process_bar("m", ts=TS, bars={SYMBOL: _bar(100.0, 120.0, 99.0, 99.5)})
    assert [trade.exit_reason for trade in trades] == ["liquidation"]
    assert portfolio.cash("m") == pytest.approx(0.0)


def test_unleveraged_position_is_practically_never_liquidated() -> None:
    price = liquidation_price(
        direction="long", entry_price=100.0, qty=1.0, margin=100.0, maintenance_margin=0.005
    )
    assert price == pytest.approx(0.5)  # marjın tamamı yatırılmışsa eşik sıfıra yakındır


# --------------------------------------------------------------------------- #
# Stop / TP / kısmi çıkış
# --------------------------------------------------------------------------- #
def test_stop_wins_when_stop_and_take_profit_share_a_bar() -> None:
    """Mum içi sıralama bilinemez: kötü olan (stop) gerçekleşmiş varsayılır (kural 13)."""
    portfolio = Portfolio(_frictionless())
    portfolio.open_position(
        "m", symbol=SYMBOL, direction="long", stop_price=95.0,
        reference_price=100.0, ts=TS, marks={SYMBOL: 100.0},
        take_profits=(TakeProfit(price=110.0, fraction=1.0),),
    )
    trades = portfolio.process_bar("m", ts=TS, bars={SYMBOL: _bar(100.0, 115.0, 94.0, 112.0)})
    assert [trade.exit_reason for trade in trades] == ["stop"]


def test_stop_fills_at_the_open_when_the_bar_gaps_through_it() -> None:
    portfolio = Portfolio(_frictionless())
    portfolio.open_position(
        "m", symbol=SYMBOL, direction="long", stop_price=95.0,
        reference_price=100.0, ts=TS, marks={SYMBOL: 100.0},
    )
    trades = portfolio.process_bar("m", ts=TS, bars={SYMBOL: _bar(90.0, 92.0, 88.0, 91.0)})
    assert trades[0].exit_price == pytest.approx(90.0)  # stop'ta değil, boşluklu açılışta


def test_short_stop_uses_the_worse_slippage() -> None:
    """config yalnızca short stop'lar için ayrı (daha kötü) kayma tanımlar."""
    portfolio = Portfolio(_config(fee_rate=0.0))
    portfolio.open_position(
        "m", symbol=SYMBOL, direction="short", stop_price=105.0,
        reference_price=100.0, ts=TS, marks={SYMBOL: 100.0},
    )
    trades = portfolio.process_bar("m", ts=TS, bars={SYMBOL: _bar(99.9, 105.0, 99.0, 104.0)})
    assert trades[0].exit_price == pytest.approx(105.0 * (1 + 0.0015))


def test_partial_take_profits_close_fractions_of_the_initial_size() -> None:
    portfolio = Portfolio(_frictionless())
    result = portfolio.open_position(
        "m", symbol=SYMBOL, direction="long", stop_price=95.0,
        reference_price=100.0, ts=TS, marks={SYMBOL: 100.0},
        take_profits=(
            TakeProfit(price=105.0, fraction=0.5),
            TakeProfit(price=110.0, fraction=0.25),
        ),
    )
    position = result.position
    assert position is not None
    trades = portfolio.process_bar("m", ts=TS, bars={SYMBOL: _bar(100.0, 111.0, 99.0, 110.5)})

    assert [trade.exit_reason for trade in trades] == ["tp", "tp"]
    assert [trade.exit_price for trade in trades] == [pytest.approx(105.0), pytest.approx(110.0)]
    assert [trade.qty for trade in trades] == [pytest.approx(10.0), pytest.approx(5.0)]
    assert position.qty == pytest.approx(5.0)  # kalan dilim taşınır
    # margin/qty oranı korunur, dolayısıyla likidasyon fiyatı kısmi çıkıştan etkilenmez
    assert position.liq_price == pytest.approx(
        liquidation_price(direction="long", entry_price=100.0, qty=position.qty,
                          margin=position.margin, maintenance_margin=0.005)
    )


def test_closed_trade_pnl_sums_to_the_cash_change() -> None:
    """Defterin denetlenebilirliği: kapanan işlemlerin pnl toplamı = bakiye değişimi."""
    portfolio = Portfolio(_config())
    start = portfolio.cash("m")
    portfolio.open_position(
        "m", symbol=SYMBOL, direction="long", stop_price=95.0,
        reference_price=100.0, ts=TS, marks={SYMBOL: 100.0},
        take_profits=(TakeProfit(price=105.0, fraction=0.5),),
    )
    trades = portfolio.process_bar("m", ts=TS, bars={SYMBOL: _bar(100.0, 106.0, 99.0, 105.5)})
    trades.append(
        portfolio.close_position(
            "m", symbol=SYMBOL, direction="long", reference_price=104.0, ts=TS
        )
    )
    assert portfolio.cash("m") - start == pytest.approx(sum(trade.pnl for trade in trades))
    assert portfolio.positions("m") == ()


# --------------------------------------------------------------------------- #
# Kontenjanlar
# --------------------------------------------------------------------------- #
def _open(portfolio: Portfolio, symbol: str, direction: str) -> Any:
    stop = 95.0 if direction == "long" else 105.0
    return portfolio.open_position(
        "m", symbol=symbol, direction=direction, stop_price=stop,  # type: ignore[arg-type]
        reference_price=100.0, ts=TS, marks={},
    )


def test_second_position_in_the_same_symbol_and_direction_is_rejected() -> None:
    portfolio = Portfolio(_frictionless())
    assert _open(portfolio, SYMBOL, "long").position is not None
    rejected = _open(portfolio, SYMBOL, "long")
    assert rejected.position is None
    assert "zaten açık" in rejected.rejected
    # Ters yön ayrı bir pozisyondur, engellenmez
    assert _open(portfolio, SYMBOL, "short").position is not None


def test_max_positions_and_max_short_positions_are_enforced() -> None:
    portfolio = Portfolio(_frictionless())
    for index in range(3):
        assert _open(portfolio, f"S{index}-USDT-SWAP", "short").position is not None
    blocked = _open(portfolio, "S3-USDT-SWAP", "short")
    assert blocked.position is None and "max_short_positions" in blocked.rejected

    for index in range(2):
        assert _open(portfolio, f"L{index}-USDT-SWAP", "long").position is not None
    full = _open(portfolio, "L2-USDT-SWAP", "long")
    assert full.position is None and "max_positions" in full.rejected


def test_gapped_entry_past_the_stop_is_rejected() -> None:
    portfolio = Portfolio(_frictionless())
    result = portfolio.open_position(
        "m", symbol=SYMBOL, direction="long", stop_price=95.0,
        reference_price=94.0, ts=TS, marks={},  # dolum stop'un altında açtı
    )
    assert result.position is None
    assert "boşluklu açılış" in result.rejected


# --------------------------------------------------------------------------- #
# Durum taşıma ve trailing
# --------------------------------------------------------------------------- #
def test_state_round_trip_preserves_positions() -> None:
    portfolio = Portfolio(_config())
    portfolio.open_position(
        "m", symbol=SYMBOL, direction="short", stop_price=105.0,
        reference_price=100.0, ts=TS, marks={SYMBOL: 100.0}, trailing_atr=2.0,
        take_profits=(TakeProfit(price=90.0, fraction=0.5),), reason="test",
    )
    state = portfolio.to_state("m")

    restored = Portfolio(_config())
    restored.load_state("m", state)
    before, after = portfolio.positions("m")[0], restored.positions("m")[0]
    assert after.as_state() == before.as_state()
    assert restored.cash("m") == pytest.approx(portfolio.cash("m"))


def test_stop_only_moves_in_the_tightening_direction() -> None:
    portfolio = Portfolio(_frictionless())
    portfolio.open_position(
        "m", symbol=SYMBOL, direction="long", stop_price=95.0,
        reference_price=100.0, ts=TS, marks={},
    )
    assert portfolio.set_stop_price("m", symbol=SYMBOL, direction="long", stop_price=97.0) is True
    assert portfolio.set_stop_price("m", symbol=SYMBOL, direction="long", stop_price=90.0) is False
    assert portfolio.positions("m")[0].stop_price == pytest.approx(97.0)


def test_position_without_bar_data_is_left_untouched() -> None:
    portfolio = Portfolio(_frictionless())
    portfolio.open_position(
        "m", symbol=SYMBOL, direction="long", stop_price=95.0,
        reference_price=100.0, ts=TS, marks={},
    )
    assert portfolio.process_bar("m", ts=TS, bars={}) == []
    assert len(portfolio.positions("m")) == 1


def test_full_lifecycle_reconciles_with_real_costs() -> None:
    """Kısmi TP + funding + komisyon + kayma birlikte: defter sonunda hesap kapanmalı.

    Denetlenebilirliğin tanımı bu: son özsermaye = başlangıç + kapanan işlemlerin pnl'i.
    """
    from core.funding import FundingCharge

    portfolio = Portfolio(_config())
    result = portfolio.open_position(
        "m", symbol=SYMBOL, direction="short", stop_price=104.0,
        reference_price=100.0, ts=TS, marks={SYMBOL: 100.0},
        take_profits=(TakeProfit(price=96.0, fraction=0.5),), reason="kısmi çıkış",
    )
    assert result.position is not None
    trades = []

    portfolio.apply_funding(
        "m",
        [FundingCharge(symbol=SYMBOL, direction="short", ts=TS, rate=0.0001,
                       notional=result.position.qty * 100.0, amount=0.05)],
    )
    trades += portfolio.process_bar("m", ts=TS, bars={SYMBOL: _bar(100.0, 101.0, 95.5, 96.0)})
    trades += portfolio.process_bar("m", ts=TS, bars={SYMBOL: _bar(96.0, 104.5, 95.0, 104.0)})

    assert [trade.exit_reason for trade in trades] == ["tp", "stop"]
    assert portfolio.positions("m") == ()
    # Funding nakde tahakkuk anında girdi; trade.pnl'de RAPORLANIR ama ikinci kez eklenmez.
    assert portfolio.cash("m") == pytest.approx(10_000.0 + sum(trade.pnl for trade in trades))
    assert portfolio.equity("m", {}) == pytest.approx(portfolio.cash("m"))
    assert sum(trade.funding for trade in trades) == pytest.approx(0.05)

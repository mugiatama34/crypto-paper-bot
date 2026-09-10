import pandas as pd
import pytest

from strategies.base import (
    ExitInstruction,
    MarketData,
    Position,
    Signal,
    Strategy,
    TakeProfit,
)


def _market() -> MarketData:
    df = pd.DataFrame({"open": [1.0], "high": [1.0], "low": [1.0], "close": [1.0]})
    return MarketData(ohlcv={"BTCUSDT": df}, btc=df, funding_rates={"BTCUSDT": 0.0})


def test_strategy_cannot_be_instantiated_without_generate_signals() -> None:
    with pytest.raises(TypeError):
        Strategy()  # type: ignore[abstract]


def test_manage_positions_default_returns_empty_list() -> None:
    class OnlyOpens(Strategy):
        name = "only_opens"
        allowed_directions = ["long"]

        def generate_signals(self, market: MarketData) -> list[Signal]:
            return [Signal(symbol="BTCUSDT", direction="long", stop_price=90.0)]

    strategy = OnlyOpens()
    market = _market()

    signals = strategy.generate_signals(market)
    assert signals[0].reason == ""

    position = Position(
        symbol="BTCUSDT",
        direction="long",
        entry_price=100.0,
        stop_price=90.0,
        take_profits=[TakeProfit(price=110.0, fraction=1.0)],
        trailing_atr=None,
        opened_at=pd.Timestamp("2024-01-01"),
    )
    assert strategy.manage_positions(market, [position]) == []


def test_manage_positions_can_be_overridden() -> None:
    class ClosesEverything(Strategy):
        name = "closes_everything"
        allowed_directions = ["long", "short"]

        def generate_signals(self, market: MarketData) -> list[Signal]:
            return []

        def manage_positions(
            self, market: MarketData, positions: list[Position]
        ) -> list[ExitInstruction]:
            return [ExitInstruction(symbol=p.symbol, action="close") for p in positions]

    position = Position(
        symbol="ETHUSDT",
        direction="short",
        entry_price=100.0,
        stop_price=110.0,
        take_profits=[],
        trailing_atr=1.5,
        opened_at=pd.Timestamp("2024-01-01"),
    )
    exits = ClosesEverything().manage_positions(_market(), [position])
    assert exits == [ExitInstruction(symbol="ETHUSDT", action="close")]

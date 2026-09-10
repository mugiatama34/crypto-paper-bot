import pytest

from core.validate import validate_signal
from strategies.base import Signal, TakeProfit

UNIVERSE = ["BTCUSDT", "ETHUSDT"]


def _signal(**overrides: object) -> Signal:
    defaults: dict[str, object] = dict(symbol="BTCUSDT", direction="long", stop_price=90.0)
    defaults.update(overrides)
    return Signal(**defaults)  # type: ignore[arg-type]


def test_valid_long_signal_passes() -> None:
    validate_signal(
        _signal(),
        entry_price=100.0,
        allowed_directions=["long"],
        symbol_universe=UNIVERSE,
    )


def test_limit_entry_type_not_implemented() -> None:
    with pytest.raises(NotImplementedError):
        validate_signal(
            _signal(entry_type="limit"),
            entry_price=100.0,
            allowed_directions=["long"],
            symbol_universe=UNIVERSE,
        )


def test_symbol_outside_universe_rejected() -> None:
    with pytest.raises(ValueError, match="sembol evreninde"):
        validate_signal(
            _signal(symbol="DOGEUSDT"),
            entry_price=100.0,
            allowed_directions=["long"],
            symbol_universe=UNIVERSE,
        )


def test_direction_not_allowed_rejected() -> None:
    with pytest.raises(ValueError, match="izinli değil"):
        validate_signal(
            _signal(direction="short", stop_price=110.0),
            entry_price=100.0,
            allowed_directions=["long"],
            symbol_universe=UNIVERSE,
        )


def test_stop_equal_entry_rejected() -> None:
    with pytest.raises(ValueError, match="sıfıra bölme"):
        validate_signal(
            _signal(stop_price=100.0),
            entry_price=100.0,
            allowed_directions=["long"],
            symbol_universe=UNIVERSE,
        )


def test_long_stop_on_wrong_side_rejected() -> None:
    with pytest.raises(ValueError, match="altında olmalı"):
        validate_signal(
            _signal(stop_price=110.0),
            entry_price=100.0,
            allowed_directions=["long"],
            symbol_universe=UNIVERSE,
        )


def test_short_stop_on_wrong_side_rejected() -> None:
    with pytest.raises(ValueError, match="üzerinde olmalı"):
        validate_signal(
            _signal(direction="short", stop_price=90.0),
            entry_price=100.0,
            allowed_directions=["short"],
            symbol_universe=UNIVERSE,
        )


def test_take_profit_on_wrong_side_rejected() -> None:
    with pytest.raises(ValueError, match="giriş fiyatının üzerinde olmalı"):
        validate_signal(
            _signal(take_profits=[TakeProfit(price=95.0, fraction=1.0)]),
            entry_price=100.0,
            allowed_directions=["long"],
            symbol_universe=UNIVERSE,
        )


def test_take_profit_fraction_sum_over_one_rejected() -> None:
    with pytest.raises(ValueError, match="fraction toplamı"):
        validate_signal(
            _signal(
                take_profits=[
                    TakeProfit(price=110.0, fraction=0.6),
                    TakeProfit(price=120.0, fraction=0.6),
                ]
            ),
            entry_price=100.0,
            allowed_directions=["long"],
            symbol_universe=UNIVERSE,
        )

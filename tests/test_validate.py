import pytest

from core.validate import validate_signal
from strategies.base import Signal, TakeProfit

UNIVERSE = ["BTC-USDT-SWAP", "ETH-USDT-SWAP"]


def _signal(**overrides: object) -> Signal:
    defaults: dict[str, object] = dict(symbol="BTC-USDT-SWAP", direction="long", stop_price=90.0)
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
            _signal(symbol="DOGE-USDT-SWAP"),
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
            _signal(take_profits=(TakeProfit(price=95.0, fraction=1.0),)),
            entry_price=100.0,
            allowed_directions=["long"],
            symbol_universe=UNIVERSE,
        )


def test_take_profit_fraction_sum_over_one_rejected() -> None:
    with pytest.raises(ValueError, match="fraction toplamı"):
        validate_signal(
            _signal(
                take_profits=(
                    TakeProfit(price=110.0, fraction=0.6),
                    TakeProfit(price=120.0, fraction=0.6),
                )
            ),
            entry_price=100.0,
            allowed_directions=["long"],
            symbol_universe=UNIVERSE,
        )


# --------------------------------------------------------------------------- #
# Boyutlandırma modu kapısı (CLAUDE.md kural 15)
# --------------------------------------------------------------------------- #
def _benchmark_signal(**overrides: object) -> Signal:
    defaults: dict[str, object] = dict(
        symbol="BTC-USDT-SWAP",
        direction="long",
        sizing="notional_fraction",
        notional_fraction=0.5,
    )
    defaults.update(overrides)
    return Signal(**defaults)  # type: ignore[arg-type]


def test_notional_fraction_allowed_for_benchmark() -> None:
    validate_signal(
        _benchmark_signal(),
        entry_price=100.0,
        allowed_directions=["long"],
        symbol_universe=UNIVERSE,
        is_benchmark=True,
    )


def test_notional_fraction_rejected_for_competitor() -> None:
    """Yarışmacı modelin kendi boyutunu belirlemesi kural 3/11'i deler."""
    with pytest.raises(ValueError, match="is_benchmark=True"):
        validate_signal(
            _benchmark_signal(),
            entry_price=100.0,
            allowed_directions=["long"],
            symbol_universe=UNIVERSE,
            is_benchmark=False,
        )


def test_is_benchmark_defaults_to_restrictive() -> None:
    """Varsayılan, muafiyeti KAPALI olandır: çağıran unutursa kural sıkı tarafa düşer."""
    with pytest.raises(ValueError, match="is_benchmark=True"):
        validate_signal(
            _benchmark_signal(),
            entry_price=100.0,
            allowed_directions=["long"],
            symbol_universe=UNIVERSE,
        )


def test_notional_fraction_with_stop_price_rejected() -> None:
    """Dolu stop sessizce yok sayılmaz: risk_amount ve cost_per_r ondan türer."""
    with pytest.raises(ValueError, match="stop_price None olmalıdır"):
        validate_signal(
            _benchmark_signal(stop_price=90.0),
            entry_price=100.0,
            allowed_directions=["long"],
            symbol_universe=UNIVERSE,
            is_benchmark=True,
        )


def test_notional_fraction_requires_fraction() -> None:
    with pytest.raises(ValueError, match="notional_fraction zorunludur"):
        validate_signal(
            _benchmark_signal(notional_fraction=None),
            entry_price=100.0,
            allowed_directions=["long"],
            symbol_universe=UNIVERSE,
            is_benchmark=True,
        )


@pytest.mark.parametrize("fraction", [0.0, -0.5, 1.5])
def test_notional_fraction_out_of_range_rejected(fraction: float) -> None:
    with pytest.raises(ValueError, match="0 ile 1.0 arasında"):
        validate_signal(
            _benchmark_signal(notional_fraction=fraction),
            entry_price=100.0,
            allowed_directions=["long"],
            symbol_universe=UNIVERSE,
            is_benchmark=True,
        )


def test_risk_sizing_requires_stop_price() -> None:
    with pytest.raises(ValueError, match="stop_price zorunludur"):
        validate_signal(
            _signal(stop_price=None),
            entry_price=100.0,
            allowed_directions=["long"],
            symbol_universe=UNIVERSE,
        )


def test_risk_sizing_rejects_notional_fraction() -> None:
    """Muafiyet bir referans modelde bile modlar arası karışmaya açılmaz."""
    with pytest.raises(ValueError, match="notional_fraction dolu olamaz"):
        validate_signal(
            _signal(notional_fraction=0.5),
            entry_price=100.0,
            allowed_directions=["long"],
            symbol_universe=UNIVERSE,
            is_benchmark=True,
        )


def test_unknown_sizing_mode_rejected() -> None:
    with pytest.raises(ValueError, match="bilinmeyen sizing modu"):
        validate_signal(
            _signal(sizing="kelly"),
            entry_price=100.0,
            allowed_directions=["long"],
            symbol_universe=UNIVERSE,
            is_benchmark=True,
        )


def test_benchmark_still_bound_by_allowed_directions() -> None:
    """Muafiyet YALNIZCA boyutlandırmadadır; yön kuralı referans modelde de geçerli."""
    with pytest.raises(ValueError, match="izinli değil"):
        validate_signal(
            _benchmark_signal(direction="short"),
            entry_price=100.0,
            allowed_directions=["long"],
            symbol_universe=UNIVERSE,
            is_benchmark=True,
        )


def test_benchmark_still_bound_by_universe() -> None:
    with pytest.raises(ValueError, match="sembol evreninde"):
        validate_signal(
            _benchmark_signal(symbol="DOGE-USDT-SWAP"),
            entry_price=100.0,
            allowed_directions=["long"],
            symbol_universe=UNIVERSE,
            is_benchmark=True,
        )


def test_benchmark_take_profit_geometry_still_checked() -> None:
    with pytest.raises(ValueError, match="take profit"):
        validate_signal(
            _benchmark_signal(take_profits=(TakeProfit(price=90.0, fraction=0.5),)),
            entry_price=100.0,
            allowed_directions=["long"],
            symbol_universe=UNIVERSE,
            is_benchmark=True,
        )

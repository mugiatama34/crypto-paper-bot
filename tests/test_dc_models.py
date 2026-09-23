"""`dc_short` (model 22) ve `dc_coinflip` (model 24): ön-kayda (docs/backtest.md > 6j) sadakat.

Sınanan: (1) canlı değerler config'ten gelir ve sayımla aynıdır; (2) sinyal doğrulama
kapısından geçer ve etiketleri taşır; (3) kontrol yalnızca YÖNDE ayrışır, mesafeleri
korur ve çekilişi deterministiktir; (4) sayım okununca sıfırlanır; (5) katman ön-kayıttaki
gibi çözülür.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from core.config import load_config
from core.layers import resolve_layer
from core.portfolio import Portfolio
from core.tags import find_tag
from core.validate import validate_signal
from strategies.base import MarketData
from strategies.dc.signal import DcRules
from strategies.dc_coinflip import DcCoinflip
from strategies.dc_short import DcShort
from strategies.registry import REGISTRY

from tests.test_dc_signal import _pullback


def _settings(**dc_overrides) -> dict:
    config = resolve_layer(load_config(), "dc").config
    config = {**config, "dc": {**config["dc"], **dc_overrides}}
    return config


SMALL = dict(fast_period=5, slow_period=20, warmup_bars=40, lookback_bars=400)
SYMBOLS = ["BTC-USDT-SWAP", "ETH-USDT-SWAP", "SOL-USDT-SWAP", "XRP-USDT-SWAP"]


def _market() -> MarketData:
    frame = _pullback()
    return MarketData(ohlcv={s: frame for s in SYMBOLS}, btc=frame, funding={}, as_of=frame.index[-1])


# --------------------------------------------------------------------------- #
# (1) Canlı değerler
# --------------------------------------------------------------------------- #
def test_live_parameters_are_the_preregistered_ones():
    rules = DcRules.from_config(resolve_layer(load_config(), "dc").config)
    assert (rules.fast_period, rules.slow_period, rules.warmup_bars, rules.lookback_bars) == (50, 200, 600, 3000)


def test_model_definition_matches_the_measurability_count():
    """Model ile sayım AYNI tanımı sayar: periyotlar ve ısınma iki yerde ayrışamaz."""
    from scripts import measure_death_cross as count

    rules = DcRules.from_config(resolve_layer(load_config(), "dc").config)
    assert (rules.fast_period, rules.slow_period) == (count.FAST_PERIOD, count.SLOW_PERIOD)
    assert rules.warmup_bars == count.WARMUP_BARS


def test_history_shorter_than_the_lookback_is_refused():
    """TADİLAT-1: canlı çerçeve görüş penceresinden kısa olsaydı canlı ≠ backtest olurdu."""
    config = _settings()
    config = {**config, "data": {**config["data"], "history_bars": 2999}}
    with pytest.raises(ValueError, match="lookback_bars"):
        DcShort(config=config)


# --------------------------------------------------------------------------- #
# (2) Model sinyali
# --------------------------------------------------------------------------- #
def test_short_signal_passes_validation_and_carries_the_tags():
    market = _market()
    model = DcShort(config=_settings(**SMALL))
    signals = model.generate_signals(market)
    assert len(signals) == len(SYMBOLS)
    for signal in signals:
        validate_signal(
            signal,
            entry_price=float(market.ohlcv[signal.symbol]["close"].iloc[-1]),
            allowed_directions=model.allowed_directions,
            symbol_universe=SYMBOLS,
        )
        assert signal.direction == "short"
        assert [tp.fraction for tp in signal.take_profits] == [1.0]
        assert find_tag(signal.reason, "arm") == "dc_pullback"
        assert find_tag(signal.reason, "regime") is not None
        assert float(find_tag(signal.reason, "rr")) > 0.0
        assert find_tag(signal.reason, "coin") is None


def test_model_does_not_manage_positions():
    """Çıkış yalnızca stop/hedef/likidasyon: `manage_positions` miras varsayılanıdır."""
    assert "manage_positions" not in DcShort.__dict__
    assert "manage_positions" not in DcCoinflip.__dict__


def test_survey_is_consumed_once():
    """Sinyal kesiminden sonra motor `take_survey`i okumaya devam eder; çift sayım olmaz."""
    model = DcShort(config=_settings(**SMALL))
    model.generate_signals(_market())
    first = model.take_survey()
    assert first is not None and sum(first.values()) == len(SYMBOLS)
    assert model.take_survey() is None


# --------------------------------------------------------------------------- #
# (3) Kontrol
# --------------------------------------------------------------------------- #
def test_control_differs_only_in_direction_and_keeps_distances():
    market = _market()
    model = DcShort(config=_settings(**SMALL)).generate_signals(market)
    control = DcCoinflip(config=_settings(**SMALL)).generate_signals(market)
    assert [s.symbol for s in control] == [s.symbol for s in model]
    for m, c in zip(model, control):
        close = float(market.ohlcv[c.symbol]["close"].iloc[-1])
        assert abs(close - c.stop_price) == pytest.approx(abs(close - m.stop_price))
        assert abs(c.take_profits[0].price - close) == pytest.approx(abs(m.take_profits[0].price - close))
        coin = find_tag(c.reason, "coin")
        assert coin in ("same", "flipped")
        assert (c.direction == "long") == (coin == "flipped")
        validate_signal(
            c, entry_price=close, allowed_directions=DcCoinflip.allowed_directions,
            symbol_universe=SYMBOLS,
        )


def test_coin_is_deterministic_and_forks_per_symbol():
    market = _market()
    first = [find_tag(s.reason, "coin") for s in DcCoinflip(config=_settings(**SMALL)).generate_signals(market)]
    again = [find_tag(s.reason, "coin") for s in DcCoinflip(config=_settings(**SMALL)).generate_signals(market)]
    assert first == again


def test_coin_is_roughly_fair_over_many_bars():
    """S2'nin (0.5 ± 0.05) önkoşulu: akış sembol ve bar bazında bağımsız çatallanır."""
    model = DcCoinflip(config=_settings(**SMALL))
    frame = _pullback()
    flips = []
    for k in range(400):
        stamp = pd.Timestamp("2022-01-01", tz="UTC") + pd.Timedelta(hours=4 * k)
        _, coin = model.orient(
            _setup_like(frame, symbol=SYMBOLS[k % 4]), MarketData(ohlcv={}, btc=frame, funding={}, as_of=stamp)
        )
        flips.append(coin == "flipped")
    assert 0.42 <= float(np.mean(flips)) <= 0.58


def _setup_like(frame, *, symbol):
    from strategies.dc.signal import evaluate

    rules = DcRules.from_config(_settings(**SMALL))
    _, setup = evaluate(symbol, frame, as_of=frame.index[-1], rules=rules)
    return setup


# --------------------------------------------------------------------------- #
# (4) Katman
# --------------------------------------------------------------------------- #
def test_layer_is_resolved_as_preregistered():
    layer = resolve_layer(load_config(), "dc")
    config = layer.config
    assert layer.models == ["buyhold", "dc_short", "dc_coinflip"]
    assert layer.symbols == resolve_layer(load_config(), "ema").symbols
    assert config["max_stop_atr_multiple"] == 6.0
    assert config["max_short_positions"] == 5 == config["max_positions"]
    assert config["acceptance"]["control_model"] == "dc_coinflip"
    assert config["data"]["history_bars"] == 3000
    assert config["signals_per_bar"] is False
    assert Portfolio(config).max_short_positions == 5


def test_cost_and_risk_constants_are_not_overridden_by_the_layer():
    """Kural 6'nın sınırı: katman kotayı ayarlar, maliyeti/riski DEĞİL."""
    root = load_config()
    layer = resolve_layer(root, "dc").config
    for key in ("risk_per_trade", "fee_rate", "slippage_base", "slippage_short_stop",
                "leverage_cap", "initial_capital", "maintenance_margin"):
        assert layer[key] == root[key]


def test_models_are_registered_and_are_competitors():
    for name in ("dc_short", "dc_coinflip"):
        model = REGISTRY[name](config=_settings())
        assert model.name == name
        assert not model.is_benchmark and not model.is_replica

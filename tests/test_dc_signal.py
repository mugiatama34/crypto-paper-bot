"""`strategies/dc/signal.py`: ön-kayıtlı tanımların (docs/backtest.md > 6j > 3) sınaması.

Gösterge ölçekleri testte KÜÇÜLTÜLÜR (EMA5/EMA20, ısınma 40) — tanımın kendisi periyottan
bağımsızdır ve 50/200'lük bir kurgu binlerce bar isterdi. Canlı değerler (50/200/600/3000)
`tests/test_dc_models.py`de config'ten sınanır.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from core.indicators import ema_series
from strategies.base import MarketData
from strategies.dc.signal import (
    CANDLE_FAILS,
    CROSS_NOT_VISIBLE,
    NO_DATA,
    NO_REGIME,
    SETUP,
    SURVEY_CODES,
    TARGET_PASSED,
    DcRules,
    _cross_index,
    evaluate,
    reflect,
    scan,
)

RULES = DcRules(fast_period=5, slow_period=20, warmup_bars=40, lookback_bars=400)
BASE = list(np.linspace(100.0, 200.0, 150)) + list(np.linspace(200.0, 150.0, 100))


def _frame(closes: list[float]) -> pd.DataFrame:
    c = np.asarray(closes, dtype="float64")
    index = pd.date_range("2022-01-01", periods=len(c), freq="4h", tz="UTC", name="ts")
    return pd.DataFrame(
        {"open": c + 0.1, "high": c + 0.5, "low": c - 0.5, "close": c, "volume": 1.0}, index=index
    )


def _set(frame: pd.DataFrame, column: str, value: float) -> None:
    frame.iloc[-1, frame.columns.get_loc(column)] = value


def _pullback(close_shift: float = 0.4) -> pd.DataFrame:
    """Düşüş rejimi + iki barlık geri çekilme; son bar EMA5'e dokunup altında, ayı mumu."""
    frame = _frame(BASE + [BASE[-1] + 0.2, BASE[-1] + close_shift])
    fast = ema_series(frame["close"], RULES.fast_period).iloc[-1]
    _set(frame, "high", fast + 0.2)
    _set(frame, "open", float(frame["close"].iloc[-1]) + 0.3)
    return frame


def _evaluate(frame: pd.DataFrame, rules: DcRules = RULES):
    return evaluate("BTC-USDT-SWAP", frame, as_of=frame.index[-1], rules=rules)


def test_setup_geometry_is_the_preregistered_one():
    frame = _pullback()
    code, setup = _evaluate(frame)
    assert code == SETUP and setup is not None
    slow = ema_series(frame["close"], RULES.slow_period)
    fast = ema_series(frame["close"], RULES.fast_period)
    below = (fast < slow).to_numpy()
    cross = int(np.flatnonzero(~below)[-1]) + 1
    assert setup.direction == "short"
    # Stop = kurulum barındaki EMA200 (burada EMA20).
    assert setup.stop_price == pytest.approx(float(slow.iloc[-1]))
    # Hedef = min(low[c .. t−1]) — kurulum barı HARİÇ.
    assert setup.target_price == pytest.approx(float(frame["low"].iloc[cross:-1].min()))
    assert setup.cross_bar == frame.index[cross]
    assert setup.target_price < setup.close < setup.stop_price


def test_target_excludes_the_setup_bar_itself():
    """Kurulum barının kendi low'u ne kadar derin olursa olsun hedefe girmez (§6j > 3)."""
    frame = _pullback()
    _, reference = _evaluate(frame)
    _set(frame, "low", 100.0)
    _, deep = _evaluate(frame)
    assert reference is not None and deep is not None
    assert deep.target_price == pytest.approx(reference.target_price)


def test_target_already_passed_produces_no_signal():
    frame = _pullback()
    _, setup = _evaluate(frame)
    assert setup is not None
    below_target = setup.target_price - 0.3
    _set(frame, "close", below_target)
    fast = ema_series(frame["close"], RULES.fast_period).iloc[-1]
    _set(frame, "high", fast + 0.2)
    _set(frame, "open", below_target + 1.0)
    code, found = _evaluate(frame)
    assert (code, found) == (TARGET_PASSED, None)


def test_candle_must_touch_the_fast_ema():
    frame = _pullback()
    fast = ema_series(frame["close"], RULES.fast_period).iloc[-1]
    _set(frame, "high", fast - 0.01)  # fitil EMA5'e DEĞMİYOR
    assert _evaluate(frame) == (CANDLE_FAILS, None)


def test_bullish_candle_is_not_a_setup():
    frame = _pullback()
    _set(frame, "open", float(frame["close"].iloc[-1]) - 0.3)  # close > open
    assert _evaluate(frame)[0] == CANDLE_FAILS


def test_no_regime_when_fast_is_above_slow():
    frame = _frame(BASE[:150])  # yalnızca yükseliş
    assert _evaluate(frame) == (NO_REGIME, None)


def test_short_history_is_no_data_not_a_partial_ema():
    frame = _frame(BASE[:RULES.warmup_bars + 1])
    assert _evaluate(frame) == (NO_DATA, None)


def test_cross_before_the_warmup_boundary_is_not_visible():
    """Görüş penceresi daraltılınca kesişim ısınmanın içinde kalır: sinyal YOK, sayılır."""
    narrow = DcRules(fast_period=5, slow_period=20, warmup_bars=40, lookback_bars=120)
    assert _evaluate(_pullback(), narrow) == (CROSS_NOT_VISIBLE, None)


def test_lookback_window_is_applied_by_the_model_not_the_data():
    """Veri ne kadar eskiden başlarsa başlasın model son `lookback_bars` barı görür (TADİLAT-1)."""
    frame = _pullback()
    deep = pd.concat([_frame([100.0] * 500).set_index(
        pd.date_range(end=frame.index[0] - pd.Timedelta("4h"), periods=500, freq="4h", tz="UTC", name="ts")
    ), frame])
    narrow = DcRules(fast_period=5, slow_period=20, warmup_bars=40, lookback_bars=len(frame))
    assert _evaluate(deep, narrow) == _evaluate(frame, narrow)


def test_cross_on_the_setup_bar_leaves_the_target_undefined():
    fast = np.array([10.0] * 50 + [9.0])
    slow = np.array([9.5] * 51)
    assert _cross_index(fast, slow, t=50, warmup=40) == 50  # c == t -> target_undefined


def test_cross_index_is_the_bar_after_the_last_non_below_bar():
    fast = np.array([10.0] * 45 + [9.0] * 6)
    slow = np.array([9.5] * 51)
    assert _cross_index(fast, slow, t=50, warmup=40) == 45
    assert _cross_index(fast, slow, t=50, warmup=46) is None


def test_reflect_mirrors_distances_around_the_close():
    _, setup = _evaluate(_pullback())
    assert setup is not None
    flipped = reflect(setup)
    assert flipped.direction == "long"
    assert flipped.stop_distance == pytest.approx(setup.stop_distance)
    assert flipped.target_distance == pytest.approx(setup.target_distance)
    assert flipped.stop_price < flipped.close < flipped.target_price
    assert flipped.reward_risk == pytest.approx(setup.reward_risk)


def test_scan_counts_every_symbol_exactly_once_in_universe_order():
    setup_frame = _pullback()
    as_of = setup_frame.index[-1]
    flat = _frame([100.0] * len(setup_frame)).set_index(setup_frame.index)
    market = MarketData(
        ohlcv={"ETH-USDT-SWAP": setup_frame, "BTC-USDT-SWAP": flat, "SOL-USDT-SWAP": setup_frame},
        btc=flat, funding={}, as_of=as_of,
    )
    result = scan(market, RULES)
    assert result.examined == 3
    assert set(result.counts) <= set(SURVEY_CODES)
    assert [s.symbol for s in result.setups] == ["ETH-USDT-SWAP", "SOL-USDT-SWAP"]


def test_rules_refuse_a_window_shorter_than_the_warmup():
    config = {"dc": {"fast_period": 50, "slow_period": 200, "warmup_bars": 600, "lookback_bars": 600}}
    with pytest.raises(ValueError):
        DcRules.from_config(config)

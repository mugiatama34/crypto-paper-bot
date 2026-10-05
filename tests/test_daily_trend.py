"""`daily_trend` (27) ve eşlenmiş kontrolü `daily_trend_random` (28): `daily` katmanı, §6y.

Sabitlenen şeyler: kırılım kapısı (kanal o günü HARİÇ tutar), yalnızca long, 3×ATR stop +
3×ATR trailing isteği, kontrolün AYNI geometriyi modelin KENDİ sabitlerinden okuması ve
çekilişinin yalnızca long olması, ve OKX'in UTC çapalı günlük bar kodunun süresi.
"""

from __future__ import annotations

import pandas as pd
import pytest

from core.config import load_config
from core.data import bar_duration
from core.indicators import average_true_range
from core.layers import resolve_layer
from core.validate import validate_signal
from strategies import daily_trend
from strategies.daily_trend import DailyTrend
from strategies.daily_trend_random import DailyTrendRandom
from tests.helpers_market import falling, frame, market, rising

DAILY = resolve_layer(load_config(), "daily").config
SYMBOL = "BTC-USDT-SWAP"


def _frame(closes: list[float]) -> pd.DataFrame:
    return frame(closes, freq="1D")


def _signals(closes: list[float]):
    return DailyTrend(config=DAILY).generate_signals(market({SYMBOL: _frame(closes)}))


def test_utc_daily_bar_code_lasts_one_day() -> None:
    assert bar_duration("1Dutc") == pd.Timedelta("1D")
    assert bar_duration("1D") == pd.Timedelta("1D")
    assert bar_duration("4H") == pd.Timedelta("4h")


def test_contract_is_a_long_only_competitor() -> None:
    model = DailyTrend(config=DAILY)
    assert model.name == "daily_trend"
    assert model.allowed_directions == ["long"]
    assert not model.is_benchmark and not model.is_replica and not model.is_meta


def test_registry_resolves_both_models() -> None:
    from strategies.registry import build

    assert isinstance(build("daily_trend", config=DAILY), DailyTrend)
    assert isinstance(build("daily_trend_random", config=DAILY), DailyTrendRandom)


def test_close_above_the_twenty_day_high_opens_a_long() -> None:
    (signal,) = _signals(rising(60))
    assert signal.direction == "long"
    assert signal.symbol == SYMBOL


def test_close_inside_the_channel_is_not_a_signal() -> None:
    closes = rising(60)
    closes[-1] = closes[-2] + 0.2  # önceki barın tepesi (kapanış + 0.5) aşılmıyor
    assert _signals(closes) == []


def test_a_breakdown_never_opens_a_short() -> None:
    assert _signals(falling(60)) == []


def test_too_short_history_is_not_a_signal() -> None:
    assert _signals(rising(daily_trend.DONCHIAN_PERIOD)) == []


def test_stop_is_three_atr_below_the_close_and_trailing_is_requested() -> None:
    closes = rising(60)
    atr = average_true_range(_frame(closes), 14)
    assert atr is not None
    (signal,) = _signals(closes)
    assert signal.stop_price == pytest.approx(closes[-1] - 3.0 * atr)
    assert signal.trailing_atr == pytest.approx(3.0)
    assert signal.sizing == "risk" and signal.take_profits == ()
    assert abs(closes[-1] - signal.stop_price) / atr <= float(DAILY["max_stop_atr_multiple"])


def test_signal_passes_the_validation_gate() -> None:
    closes = rising(60)
    (signal,) = _signals(closes)
    validate_signal(
        signal,
        entry_price=closes[-1],
        allowed_directions=DailyTrend(config=DAILY).allowed_directions,
        symbol_universe=[SYMBOL],
    )


def _control_markets():
    frames = {
        f"S{i}-USDT-SWAP": _frame([100.0 + ((i + 1) * k) % 7 for k in range(60)]) for i in range(5)
    }
    for k in range(30, 60):
        yield market({symbol: f.iloc[:k] for symbol, f in frames.items()})


def test_control_draws_only_longs_with_the_models_geometry() -> None:
    seen = []
    for snapshot in _control_markets():
        (signal,) = DailyTrendRandom(config=DAILY).generate_signals(snapshot)
        candles = snapshot.ohlcv[signal.symbol]
        atr = average_true_range(candles, 14)
        assert atr is not None
        close = float(candles["close"].iloc[-1])
        assert signal.direction == "long"
        assert signal.stop_price == pytest.approx(close - daily_trend.STOP_ATR_MULTIPLE * atr)
        assert signal.trailing_atr == pytest.approx(daily_trend.TRAILING_ATR_MULTIPLE)
        seen.append(signal.symbol)
    assert len(set(seen)) > 1


def test_control_is_reproducible() -> None:
    first = [s.symbol for m in _control_markets() for s in DailyTrendRandom(config=DAILY).generate_signals(m)]
    again = [s.symbol for m in _control_markets() for s in DailyTrendRandom(config=DAILY).generate_signals(m)]
    assert first == again

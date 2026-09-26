"""Bilgisiz giriş gövdesi (`strategies/random_entry.py`) ve eşlenmiş kontroller (karar 65, §6n).

Üç söz sınanır:
1. `random_ctrl` gövdeye taşındıktan sonra BİREBİR aynı sinyali üretir (defter tarihli
   bölünmez — karar 25). Altın değerler TAŞIMADAN ÖNCEKİ koddan alındı.
2. Kontroller ölçtükleri modelin ÇIKIŞ geometrisini modelin KENDİ sabitlerinden okur.
3. Çekiliş akışları ayrıdır ve aynı `as_of` aynı sinyali verir.
"""

from __future__ import annotations

import random

import pandas as pd
import pytest

from core.config import load_config
from core.indicators import average_true_range, bollinger
from strategies import meanrev, trend
from strategies.meanrev_random import MeanrevRandom
from strategies.random_ctrl import RandomControl
from strategies.trend_random import TrendRandom
from tests.helpers_market import frame, market

CONFIG = load_config()
SYMBOLS = [f"S{i}-USDT-SWAP" for i in range(6)]

# `random_ctrl`in TAŞIMADAN ÖNCEKİ çıktısı (as_of, sembol, yön, stop) — aynı fikstürle.
GOLDEN = [
    ("2026-01-04 04:00", "S0-USDT-SWAP", "short", 101.52752883205312),
    ("2026-01-04 16:00", "S5-USDT-SWAP", "long", 110.93332735552882),
    ("2026-01-05 04:00", "S3-USDT-SWAP", "short", 102.98926019467831),
    ("2026-01-05 16:00", "S5-USDT-SWAP", "long", 112.35836864864889),
    ("2026-01-06 04:00", "S4-USDT-SWAP", "long", 100.44506272087774),
    ("2026-01-06 16:00", "S1-USDT-SWAP", "short", 108.41661655438408),
    ("2026-01-07 04:00", "S1-USDT-SWAP", "long", 102.2994712478889),
    ("2026-01-07 16:00", "S5-USDT-SWAP", "short", 119.4118370354358),
    ("2026-01-08 04:00", "S5-USDT-SWAP", "short", 116.40982546165722),
    ("2026-01-08 16:00", "S2-USDT-SWAP", "long", 98.77791846811107),
    ("2026-01-09 04:00", "S5-USDT-SWAP", "long", 110.41869866898645),
    ("2026-01-09 16:00", "S2-USDT-SWAP", "long", 102.58111141294567),
    ("2026-01-10 04:00", "S1-USDT-SWAP", "long", 108.47992865142108),
    ("2026-01-10 16:00", "S3-USDT-SWAP", "long", 87.83203070735458),
    ("2026-01-11 04:00", "S5-USDT-SWAP", "short", 113.78308053066107),
    ("2026-01-11 16:00", "S4-USDT-SWAP", "short", 108.95870094280882),
    ("2026-01-12 04:00", "S2-USDT-SWAP", "long", 107.02099602125485),
    ("2026-01-12 16:00", "S3-USDT-SWAP", "long", 90.03379211956847),
    ("2026-01-13 04:00", "S0-USDT-SWAP", "long", 88.89470497167191),
    ("2026-01-13 16:00", "S2-USDT-SWAP", "long", 104.2005663123083),
]
GOLDEN_FIRST_REASON = 'KONTROL GRUBU: bilgisiz çekiliş — 6 uygun sembol arasından S0-USDT-SWAP, yön short; tohum 20240217 + 2026-01-04 04:00 UTC; stop 2×ATR(14)=3.685 uzakta (101.528)'


def _frames() -> dict[str, pd.DataFrame]:
    rng = random.Random(7)
    series = {symbol: [100.0] for symbol in SYMBOLS}
    for symbol in SYMBOLS:
        for _ in range(80):
            series[symbol].append(series[symbol][-1] + rng.gauss(0, 1))
    return {symbol: frame(values, spread=0.8) for symbol, values in series.items()}


def _markets():
    frames = _frames()
    for k in range(20, 80, 3):
        yield market({symbol: f.iloc[:k] for symbol, f in frames.items()})


def test_random_ctrl_is_bit_identical_after_the_move() -> None:
    model = RandomControl(config=CONFIG)
    produced = []
    first_reason = None
    for snapshot in _markets():
        for signal in model.generate_signals(snapshot):
            produced.append((f"{snapshot.as_of:%Y-%m-%d %H:%M}", signal.symbol,
                             signal.direction, signal.stop_price))
            first_reason = first_reason or signal.reason
            assert signal.take_profits == () and signal.trailing_atr is None
    assert produced == GOLDEN
    assert first_reason == GOLDEN_FIRST_REASON


def test_trend_random_carries_trends_exit_geometry() -> None:
    snapshot = next(_markets())
    (signal,) = TrendRandom(config=CONFIG).generate_signals(snapshot)
    f = snapshot.ohlcv[signal.symbol]
    close = float(f["close"].iloc[-1])
    atr = average_true_range(f, int(CONFIG["trailing"]["atr_period"]))
    side = -1 if signal.direction == "long" else 1
    assert signal.stop_price == pytest.approx(close + side * trend.STOP_ATR_MULTIPLE * atr)
    assert signal.trailing_atr == trend.TRAILING_ATR_MULTIPLE
    assert signal.take_profits == ()
    assert signal.sizing == "risk"


def test_meanrev_random_target_keeps_the_band_distance_on_the_drawn_side() -> None:
    directions = set()
    for snapshot in _markets():
        for signal in MeanrevRandom(config=CONFIG).generate_signals(snapshot):
            f = snapshot.ohlcv[signal.symbol]
            close = float(f["close"].iloc[-1])
            atr = average_true_range(f, int(CONFIG["trailing"]["atr_period"]))
            bands = bollinger(f["close"], meanrev.BOLLINGER_PERIOD, meanrev.BOLLINGER_STD)
            distance = abs(bands.middle - close)
            side = 1 if signal.direction == "long" else -1
            assert signal.stop_price == pytest.approx(close - side * meanrev.STOP_ATR_MULTIPLE * atr)
            (tp,) = signal.take_profits
            assert tp.fraction == 1.0
            # MESAFE korunur, TARAF yönden gelir — orta bandın kendisi DEĞİL.
            assert tp.price == pytest.approx(close + side * distance)
            assert signal.trailing_atr is None
            directions.add(signal.direction)
    assert directions == {"long", "short"}


def test_meanrev_random_skips_a_symbol_sitting_on_its_middle_band() -> None:
    flat = frame([100.0] * 40, spread=0.5)            # SMA20 == kapanış -> mesafe 0
    moving = frame([100.0 + i for i in range(40)], spread=0.5)
    snapshot = market({"FLAT-USDT-SWAP": flat, "MOVE-USDT-SWAP": moving})
    for _ in range(3):
        (signal,) = MeanrevRandom(config=CONFIG).generate_signals(snapshot)
        assert signal.symbol == "MOVE-USDT-SWAP"


def test_rng_streams_are_separate_and_reproducible() -> None:
    draws = {}
    for model_cls in (RandomControl, TrendRandom, MeanrevRandom):
        seq = [
            (s.symbol, s.direction)
            for snapshot in _markets()
            for s in model_cls(config=CONFIG).generate_signals(snapshot)
        ]
        again = [
            (s.symbol, s.direction)
            for snapshot in _markets()
            for s in model_cls(config=CONFIG).generate_signals(snapshot)
        ]
        assert seq == again
        draws[model_cls.name] = seq
    assert draws["trend_random"] != draws["random_ctrl"]
    assert draws["meanrev_random"] != draws["trend_random"]


def test_controls_are_competitors_not_references() -> None:
    for model_cls in (TrendRandom, MeanrevRandom):
        model = model_cls(config=CONFIG)
        assert not model.is_benchmark and not model.is_replica and not model.is_meta
        assert model.allowed_directions == ["long", "short"]

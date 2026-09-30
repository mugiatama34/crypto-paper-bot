"""`scripts/measure_po3.py`nin SAF mantığı: ağ yok, defter yok.

Sınanan sözleşmeler: gün sınıflandırması (her gün tek sınıf), "süpürmeden SONRA" okuması
(süpürme barının kendisi geri dönüş değildir), belirsiz gün, karşı sayım, giriş anındaki
geometri, eksik veri, kapının mekaniği, dönem A kesimi ve aracın ölçüm modüllerini import
etmemesi.
"""

from __future__ import annotations

import ast
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from scripts.measure_po3 import (
    GATE_MAX_MEDIAN_COST_PER_R,
    GATE_MIN_DAYS,
    GATE_MIN_SETUPS,
    PREREGISTERED_ROUND_TRIP,
    Setup,
    classify_day,
    evaluate_gate,
    main,
    period_days,
    round_trip_cost,
)
from core.config import load_config

DAY = pd.Timestamp("2022-03-01T00:00:00Z")


def _day_frame(window_bars: list[tuple[float, float, float, float]], *, extra: int = 4) -> pd.DataFrame:
    """Asya: 32 bar, aralık [99, 101]; ardından verilen pencere barları (o, h, l, c)."""
    rows = [(100.0, 101.0, 99.0, 100.0)] * 32 + list(window_bars)
    rows += [(100.0, 100.5, 99.5, 100.0)] * (20 - len(window_bars)) + [(100.0, 100.5, 99.5, 100.0)] * extra
    index = pd.date_range(DAY, periods=len(rows), freq="15min")
    return pd.DataFrame(rows, index=index, columns=["open", "high", "low", "close"]).assign(volume=1.0)


def test_up_sweep_then_close_inside_is_a_short_setup_with_known_geometry():
    frame = _day_frame([
        (100.5, 102.0, 100.4, 101.5),  # süpürme, dışarıda kapanış
        (101.5, 102.5, 100.2, 100.6),  # geri dönüş (içeride), uç 102.5
        (100.4, 100.9, 100.0, 100.5),  # giriş barı: açılış 100.4
    ])
    result = classify_day("X", DAY, frame)
    assert result.kind == "setup"
    setup = result.setup
    assert setup.direction == "short"
    assert setup.extreme == 102.5
    assert setup.entry == 100.4
    assert setup.stop_distance == pytest.approx((102.5 - 100.4) / 100.4)
    assert setup.reward_risk == pytest.approx((100.4 - 99.0) / (102.5 - 100.4))
    assert setup.cost_per_r(0.0021) == pytest.approx(0.0021 / setup.stop_distance)


def test_sweep_bar_closing_inside_is_not_itself_the_reversal():
    frame = _day_frame([
        (100.5, 102.0, 100.4, 100.8),  # süpürme barı içeride kapanıyor
        (100.8, 100.9, 100.1, 100.3),  # geri dönüş = BU bar
        (100.2, 100.5, 100.0, 100.1),
    ])
    result = classify_day("X", DAY, frame)
    assert result.kind == "setup"
    assert result.sweep_bar_closed_inside
    assert result.setup.reversal_bar == DAY + pd.Timedelta(hours=8, minutes=15)
    assert result.setup.entry == 100.2


def test_sweep_without_reversal_is_the_continuation_count():
    frame = _day_frame([(100.5, 102.0, 100.4, 101.5)] + [(101.5, 103.0, 101.2, 102.0)] * 19)
    result = classify_day("X", DAY, frame)
    assert result.kind == "continuation"
    assert result.side == "up"


def test_both_sides_swept_is_ambiguous_and_produces_no_setup():
    frame = _day_frame([(100.0, 101.5, 100.0, 100.2), (100.2, 100.3, 98.5, 100.0)])
    result = classify_day("X", DAY, frame)
    assert result.kind == "ambiguous"
    assert result.setup is None


def test_no_sweep_and_incomplete_day():
    assert classify_day("X", DAY, _day_frame([])).kind == "none"
    frame = _day_frame([]).drop(DAY + pd.Timedelta(hours=3))
    assert classify_day("X", DAY, frame).kind == "incomplete"


def test_down_sweep_is_long_and_invalid_geometry_is_not_primary():
    # Süpürme 12:30, geri dönüş 12:45; giriş 13:00 barı (pencere DIŞI) hedefin (101) üstünde açılıyor.
    frame = _day_frame([(100.0, 100.5, 99.5, 100.0)] * 18 + [
        (99.5, 99.6, 98.0, 98.5),
        (98.5, 99.8, 98.2, 99.5),
    ])
    frame.iloc[52] = [101.2, 101.3, 100.0, 100.5, 1.0]
    result = classify_day("X", DAY, frame)
    assert result.setup.entry_bar == DAY + pd.Timedelta(hours=13)
    assert result.setup.direction == "long"
    assert result.setup.target_distance < 0
    assert not result.setup.valid


def test_period_days_never_reach_past_the_end():
    days = period_days(pd.Timestamp("2024-06-27T00:00:00Z"), pd.Timestamp("2024-06-30T00:00:00Z"))
    assert days[-1] == pd.Timestamp("2024-06-29T00:00:00Z")


def _setup(day: str, stop: float) -> Setup:
    return Setup(
        symbol="X", day=pd.Timestamp(day), side="up", sweep_bar=pd.Timestamp(day),
        reversal_bar=pd.Timestamp(day), entry_bar=pd.Timestamp(day), entry=100.0,
        extreme=100.0 * (1 + stop), target=90.0, high_a=101.0, low_a=90.0, asia_close=100.0,
    )


def test_gate_is_mechanical_and_needs_all_three():
    days = pd.date_range("2022-01-01", periods=GATE_MIN_DAYS, freq="D", tz="UTC")
    wide = [_setup(str(days[i % len(days)]), 0.02) for i in range(GATE_MIN_SETUPS)]
    assert evaluate_gate(wide, round_trip=0.0021).passed
    narrow = [_setup(str(days[i % len(days)]), 0.005) for i in range(GATE_MIN_SETUPS)]
    assert not evaluate_gate(narrow, round_trip=0.0021).passed
    few_days = [_setup("2022-01-01", 0.02) for _ in range(GATE_MIN_SETUPS)]
    assert not evaluate_gate(few_days, round_trip=0.0021).passed
    assert GATE_MAX_MEDIAN_COST_PER_R == 0.15


def test_round_trip_matches_preregistration():
    assert round_trip_cost(load_config(None)) == pytest.approx(PREREGISTERED_ROUND_TRIP)


def test_end_beyond_period_a_cutoff_is_rejected():
    assert main(["--end", "2024-07-01T00:00:00+00:00"]) == 2


def test_does_not_import_measurement_modules():
    tree = ast.parse(Path("scripts/measure_po3.py").read_text(encoding="utf-8"))
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    forbidden = {"core.metrics", "core.portfolio", "core.ledger", "core.engine"}
    assert not names & forbidden
    assert not any(name.startswith("strategies") for name in names)

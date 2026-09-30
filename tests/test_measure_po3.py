"""`scripts/measure_po3.py`nin SAF mantığı: ağ yok, defter yok.

Sınanan sözleşmeler: gün sınıflandırması (her gün tek sınıf), "süpürmeden SONRA" okuması
(süpürme barının kendisi geri dönüş değildir), belirsiz gün, karşı sayım, giriş anındaki
geometri, eksik veri, kapının mekaniği, dönem A kesimi ve aracın ölçüm modüllerini import
etmemesi.
"""

from __future__ import annotations

import ast
import dataclasses
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from scripts.measure_po3 import (
    GATE_MAX_MEDIAN_COST_PER_R,
    GATE_MIN_DAYS,
    GATE_MIN_SETUPS,
    PREREGISTERED_ROUND_TRIP,
    VARIANT_A_MIN_DAYS,
    VARIANT_A_MIN_SETUPS,
    VARIANT_A_MIN_STOP,
    VARIANT_A2_MIN_STOP,
    find_pivots,
    structure_at,
    to_h4,
    Setup,
    SymbolScan,
    DayResult,
    classify_day,
    evaluate_gate,
    evaluate_gate_variant_a,
    variant_a_thresholds_match,
    wide_enough,
    main,
    period_days,
    round_trip_cost,
    stop_costs,
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


def test_sweep_bar_closing_inside_is_itself_the_reversal():
    frame = _day_frame([
        (100.5, 102.0, 100.4, 100.8),  # süpürme barı içeride kapanıyor → geri dönüş BU bar
        (100.8, 100.9, 100.1, 100.3),  # giriş barı: açılış 100.8
        (100.2, 100.5, 100.0, 100.1),
    ])
    result = classify_day("X", DAY, frame)
    assert result.kind == "setup"
    assert result.same_bar
    assert result.setup.reversal_bar == result.setup.sweep_bar == DAY + pd.Timedelta(hours=8)
    assert result.setup.extreme == 102.0
    assert result.setup.entry == 100.8


def test_later_bar_reversal_is_not_same_bar():
    frame = _day_frame([
        (100.5, 102.0, 100.4, 101.5),
        (101.5, 102.5, 100.2, 100.6),
    ])
    assert not classify_day("X", DAY, frame).same_bar


def test_stop_slippage_cost_line_is_descriptive_and_direction_aware():
    costs = stop_costs(load_config(None))
    assert costs.short == pytest.approx(0.0031)
    assert costs.long == pytest.approx(0.0021)
    setup = _setup("2022-01-01", 0.02)
    assert setup.stop_cost_per_r(costs) == pytest.approx(0.0031 / 0.02)
    assert setup.cost_per_r(0.0021) == pytest.approx(0.0021 / 0.02)


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


def _setup(day: str, stop: float, side: str = "up") -> Setup:
    sign = 1 if side == "up" else -1
    return Setup(
        symbol="X", day=pd.Timestamp(day), side=side, sweep_bar=pd.Timestamp(day),
        reversal_bar=pd.Timestamp(day), entry_bar=pd.Timestamp(day), entry=100.0,
        extreme=100.0 * (1 + sign * stop), target=100.0 - sign * 10.0,
        high_a=101.0, low_a=90.0, asia_close=100.0,
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


# --------------------------------------------------------------------------- #
# VARYANT A — yalnızca geniş süpürmeler
# --------------------------------------------------------------------------- #
def test_variant_a_thresholds_are_preregistered_and_match_config():
    assert VARIANT_A_MIN_STOP == {"long": 0.0140, "short": 0.0207}
    assert (VARIANT_A_MIN_SETUPS, VARIANT_A_MIN_DAYS) == (300, 150)
    costs = stop_costs(load_config(None))
    assert variant_a_thresholds_match(costs)
    # Eşik türeyenden DAR olsaydı maliyet koşulu delinirdi.
    for direction, threshold in VARIANT_A_MIN_STOP.items():
        assert costs.for_direction(direction) / threshold <= 0.15 + 1e-12


def test_variant_a_filter_is_direction_aware():
    assert wide_enough(_setup("2022-01-01", 0.0208, side="up"), VARIANT_A_MIN_STOP)
    assert not wide_enough(_setup("2022-01-01", 0.0180, side="up"), VARIANT_A_MIN_STOP)
    assert wide_enough(_setup("2022-01-01", 0.0180, side="down"), VARIANT_A_MIN_STOP)
    assert not wide_enough(_setup("2022-01-01", 0.0139, side="down"), VARIANT_A_MIN_STOP)
    assert wide_enough(_setup("2022-01-01", 0.001), None)


def test_variant_a_primary_excludes_narrow_but_keeps_them_countable():
    setups = [_setup("2022-01-01", 0.03), _setup("2022-01-02", 0.01), _setup("2022-01-03", 0.015, side="down")]
    days = tuple(DayResult(symbol="X", day=s.day, kind="setup", side=s.side, setup=s) for s in setups)
    plain = SymbolScan(symbol="X", days=days)
    wide = SymbolScan(symbol="X", days=days, min_stop=VARIANT_A_MIN_STOP)
    assert len(plain.primary) == 3 and plain.narrow == []
    assert [s.day for s in wide.primary] == [setups[0].day, setups[2].day]
    assert wide.narrow == [setups[1]]


def test_variant_a_gate_needs_count_and_days_cost_is_a_sanity_check():
    days = pd.date_range("2022-01-01", periods=VARIANT_A_MIN_DAYS, freq="D", tz="UTC")
    costs = stop_costs(load_config(None))
    ok = [_setup(str(days[i % len(days)]), 0.0207) for i in range(VARIANT_A_MIN_SETUPS)]
    assert evaluate_gate_variant_a(ok, costs=costs).passed
    assert not evaluate_gate_variant_a(ok[:-1], costs=costs).passed
    few_days = [_setup("2022-01-01", 0.03) for _ in range(VARIANT_A_MIN_SETUPS)]
    assert not evaluate_gate_variant_a(few_days, costs=costs).passed
    assert not evaluate_gate_variant_a([], costs=costs).passed


# --------------------------------------------------------------------------- #
# VARYANT A2 — tek eşik
# --------------------------------------------------------------------------- #
def test_variant_a2_uses_the_long_threshold_in_both_directions():
    assert VARIANT_A2_MIN_STOP == {"long": 0.0140, "short": 0.0140}
    assert wide_enough(_setup("2022-01-01", 0.0150, side="up"), VARIANT_A2_MIN_STOP)
    assert not wide_enough(_setup("2022-01-01", 0.0130, side="up"), VARIANT_A2_MIN_STOP)


def test_variant_a2_gate_checks_cost_only_on_long():
    days = pd.date_range("2022-01-01", periods=VARIANT_A_MIN_DAYS, freq="D", tz="UTC")
    costs = stop_costs(load_config(None))
    shorts = [_setup(str(days[i % len(days)]), 0.0150, side="up") for i in range(VARIANT_A_MIN_SETUPS)]
    assert shorts[0].stop_cost_per_r(costs) > 0.15
    assert not evaluate_gate_variant_a(shorts, costs=costs).passed
    assert evaluate_gate_variant_a(shorts + [_setup("2022-01-01", 0.0150, side="down")], costs=costs,
                                   sanity_directions=("long",)).passed


# --------------------------------------------------------------------------- #
# 4H yapı (betimsel)
# --------------------------------------------------------------------------- #
def _h4_frame(highs: list[float], lows: list[float]) -> pd.DataFrame:
    index = pd.date_range("2022-01-01", periods=len(highs), freq="4h", tz="UTC")
    return pd.DataFrame({"open": lows, "high": highs, "low": lows, "close": highs}, index=index)


def test_to_h4_aggregates_and_drops_incomplete_bars():
    index = pd.date_range("2022-01-01", periods=16 * 2, freq="15min", tz="UTC")
    frame = pd.DataFrame({"open": 1.0, "high": np.arange(32.0), "low": -np.arange(32.0), "close": 2.0}, index=index)
    h4 = to_h4(frame.drop(index[20]))
    assert list(h4.index) == [index[0]]
    assert h4.iloc[0]["high"] == 15.0 and h4.iloc[0]["low"] == -15.0


def test_pivot_is_known_only_after_two_more_bars_close():
    frame = _h4_frame([1, 2, 5, 2, 1, 1, 1], [0, 0, 0, 0, 0, 0, 0])
    pivots = find_pivots(frame)
    assert list(pivots.high_values) == [5.0]
    confirmed = frame.index[4] + pd.Timedelta(hours=4)
    assert pivots.high_confirmed[0] == confirmed.value
    assert len(pivots.low_values) == 0  # eşitlik tepe/dip değildir


def test_structure_up_down_mixed_and_undefined():
    # tepeler 5 → 7 (HH), dipler 1 → 3 (HL): yukarı
    highs = [2, 3, 5, 3, 2, 4, 7, 4, 3, 3, 3]
    lows = [3, 2, 1, 2, 3, 3, 3.4, 3.5, 3, 3.6, 3.7]
    frame = _h4_frame(highs, lows)
    pivots = find_pivots(frame)
    after = frame.index[-1] + pd.Timedelta(hours=4)
    assert list(pivots.high_values) == [5.0, 7.0]
    assert list(pivots.low_values) == [1.0, 3.0]
    assert structure_at(pivots, after) == "up"
    assert structure_at(pivots, frame.index[0]) == "undefined"
    down = find_pivots(_h4_frame([-h for h in lows], [-h for h in highs]))
    assert structure_at(down, after) == "down"
    mixed = find_pivots(_h4_frame(highs, [3, 2, 1, 2, 3, 3, 3.4, 3.5, 0.5, 3.6, 3.7]))
    assert structure_at(mixed, after) == "mixed"


def test_alignment_is_relative_to_the_po3_direction():
    short = dataclasses.replace(_setup("2022-01-01", 0.02, side="up"), structure="down")
    long = dataclasses.replace(_setup("2022-01-01", 0.02, side="down"), structure="down")
    assert short.alignment == "aligned"
    assert long.alignment == "against"
    assert dataclasses.replace(long, structure="mixed").alignment == "mixed"


def test_break_below_last_low_turns_up_into_mixed_until_a_new_pair_confirms():
    highs = [2, 3, 5, 3, 2, 4, 7, 4, 3, 3, 3]
    lows = [3, 2, 1, 2, 3, 3, 3.4, 3.5, 3, 3.6, 3.7]
    base = _h4_frame(highs, lows)
    step = pd.Timedelta(hours=4)
    after = base.index[-1] + step
    assert structure_at(find_pivots(base), after) == "up"
    # 4H kapanışı son onaylı dibin (3) altında: kırılma → mixed.
    broken = pd.concat([base, pd.DataFrame(
        {"open": [3.2], "high": [3.3], "low": [2.4], "close": [2.5]}, index=[after])])
    pivots = find_pivots(broken)
    assert len(pivots.break_times) == 1
    assert structure_at(pivots, after + step) == "mixed"
    # Kırılmadan ÖNCEKİ bir an etkilenmez (ileriye bakış yok).
    assert structure_at(pivots, after) == "up"
    # Kırılmadan sonra yalnız YENİ DİP onaylanırsa hâlâ mixed; yeni tepe de gelince taban duruma döner.
    lows_only = broken.copy()
    for i, (h, l) in enumerate([(4.0, 3.0), (4.1, 2.0), (4.2, 3.1), (4.3, 3.2)]):
        lows_only.loc[after + (i + 1) * step] = [h, h, l, h]
    p2 = find_pivots(lows_only)
    t2 = lows_only.index[-1] + step
    assert p2.low_confirmed[-1] > p2.break_times[0]
    assert not (p2.high_confirmed > p2.break_times[0]).any()
    assert structure_at(p2, t2) == "mixed"

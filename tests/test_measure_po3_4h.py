"""`scripts/measure_po3_4h.py` (§6v): 4H mum sınıflandırması, eşik, etiketler ve güç kapısı.

Ağ yok, defter yok. Sınanan sözleşmeler: kutu = ilk 4 bar, süpürme barı DÂHİL geri dönüş, b16'da
geri dönüş = giriş barı yok, konum şartı, sabit tampon, iki yönde tek eşiğin config'ten
çözülmesi, FVG ve limit dolumu, güç formülünün A2'yi yeniden üretmesi, getiri/R üretilmemesi ve
ölçüm modüllerinin import edilmemesi.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pandas as pd
import pytest

from scripts.measure_po3 import StopCosts, stop_costs
from scripts.measure_po3_4h import (
    A2_DEFF,
    MAX_MDE_HALF_DELTA,
    PREREGISTERED_THRESHOLD,
    Setup,
    classify_candle,
    derive_threshold,
    evaluate_gate,
    label_context,
    payload,
    period_candles,
    projected_deff,
    projected_mde,
    scan_symbol,
    verify_sigma,
)
from scripts.measure_po3 import find_pivots, to_h4

START = pd.Timestamp("2022-03-01T04:00:00Z")
COSTS = StopCosts(short=0.0031, long=0.0021)


def _candle(rows: list[tuple[float, float, float, float]], start: pd.Timestamp = START) -> pd.DataFrame:
    assert len(rows) == 16
    index = pd.date_range(start, periods=16, freq="15min")
    return pd.DataFrame(rows, index=index, columns=["open", "high", "low", "close"])


BOX = [(100, 101, 99, 100)] * 4  # H_K 101, L_K 99, O_4H 100, genişlik 2
QUIET = (100, 100.5, 99.5, 100)


def test_threshold_is_derived_from_config_and_is_two_point_zero_seven():
    from core.config import load_config
    assert derive_threshold(stop_costs(load_config(None))) == PREREGISTERED_THRESHOLD == 0.0207
    assert derive_threshold(StopCosts(short=0.0021, long=0.0021)) == 0.0140  # %1.40'ta short da geçerse
    assert derive_threshold(StopCosts(short=0.0040, long=0.0021)) != 0.0207  # 0.0207 artık türeyen değil


def test_up_sweep_with_reversal_on_sweep_bar_is_a_short_setup():
    # b5 süpürür ve içeride kapanır; giriş b6 açılışı 100.8 > O_4H; uç 104 → stop 104.2.
    rows = BOX + [(100.5, 104, 100, 100.6), (100.8, 101, 100, 100.2)] + [QUIET] * 10
    candle = classify_candle("X", START, _candle(rows), threshold=0.0207)
    assert candle.kind == "primary"
    setup = candle.setup
    assert (setup.direction, setup.sweep, setup.reversal, setup.delay) == ("short", 4, 4, 0)
    assert setup.stop == pytest.approx(104.2)
    assert setup.target == 99
    assert setup.stop_distance == pytest.approx(3.4 / 100.8)
    assert setup.bars_left == 11


def test_bars_may_stay_outside_and_reversal_on_b16_has_no_entry():
    outside = (101.5, 102, 101.2, 101.6)
    late = BOX + [(100.5, 104, 100, 101.5)] + [outside] * 10 + [(101.5, 101.6, 100, 100.5)]
    assert classify_candle("X", START, _candle(late), threshold=0.0207).kind == "no_entry_bar"
    on_b15 = BOX + [(100.5, 104, 100, 101.5)] + [outside] * 9 + [(101.5, 101.6, 100, 100.5), QUIET]
    candle = classify_candle("X", START, _candle(on_b15), threshold=0.0207)
    assert candle.setup.reversal == 14 and candle.setup.bars_left == 1 and candle.setup.delay == 10


def test_classes_ambiguous_continuation_off_side_narrow_flat_incomplete():
    both = BOX + [(100.5, 102, 100, 100.5), (100.5, 100.6, 98, 100.5)] + [QUIET] * 10
    assert classify_candle("X", START, _candle(both), threshold=0.0207).kind == "ambiguous"
    cont = BOX + [(100.5, 102, 100, 101.5)] + [(101.5, 102, 101.2, 101.6)] * 11
    assert classify_candle("X", START, _candle(cont), threshold=0.0207).kind == "continuation"
    # Üst süpürme ama giriş O_4H'nin ALTINDA → short konum dışı.
    off = BOX + [(100.5, 104, 99.5, 99.8), (99.7, 100, 99.5, 99.8)] + [QUIET] * 10
    assert classify_candle("X", START, _candle(off), threshold=0.0207).kind == "off_side"
    # Dar süpürme: uç 101.2 → stop 101.4, giriş 100.8 → %0.6 < %2.07.
    narrow = BOX + [(100.5, 101.2, 100, 100.6), (100.8, 101, 100, 100.2)] + [QUIET] * 10
    assert classify_candle("X", START, _candle(narrow), threshold=0.0207).kind == "narrow"
    assert classify_candle("X", START, _candle([QUIET] * 16), threshold=0.0207).kind == "none"
    flat = [(100, 100, 100, 100)] * 4 + [QUIET] * 12
    assert classify_candle("X", START, _candle(flat), threshold=0.0207).kind == "flat"
    assert classify_candle("X", START, _candle(BOX + [QUIET] * 12).iloc[:-1], threshold=0.0207).kind == "incomplete"


def test_fvg_and_limit_fill_use_only_the_candles_own_bars():
    # Alt süpürme → long (kutu tepesi 103, iki taraf süpürülmez). r = 4; low[r+2] 101.5 > high[r] 101.
    box = [(100, 103, 99, 100)] * 4
    base = box + [(99.5, 101, 95, 100), (99.8, 101.4, 99.6, 101.3), (101.6, 102, 101.5, 101.8)]
    filled = classify_candle("X", START, _candle(base + [(101.8, 102, 101.4, 101.7)] + [QUIET] * 8), threshold=0.0207)
    assert filled.kind == "primary" and filled.setup.direction == "long"
    assert filled.setup.stop == pytest.approx(94.6)
    assert (filled.setup.fvg, filled.setup.fvg_limit, filled.setup.fvg_fill) == ("var", 101.5, "dolar")
    assert filled.setup.fvg_cost_per_r(COSTS) == pytest.approx(0.0021 / ((101.5 - 94.6) / 101.5))
    away = [(102, 102.9, 101.9, 102.5)] * 9
    missed = classify_candle("X", START, _candle(base + away), threshold=0.0207)
    assert missed.setup.fvg_fill == "dolmaz"
    assert missed.setup.fvg_cost_per_r(COSTS) is None


def _setup(**kw) -> Setup:
    fields = dict(symbol="X", candle=START, side="up", sweep=4, reversal=4, entry=100.8, open_4h=100.0,
                  high_k=101.0, low_k=99.0, extreme=104.0)
    fields.update(kw)
    return Setup(**fields)


def test_swept_level_and_opposite_liquidity_use_previous_day_and_4h():
    index = pd.date_range(START - pd.Timedelta(days=2), START, freq="15min", inclusive="left")
    frame = pd.DataFrame({"open": 100.0, "high": 102.0, "low": 96.0, "close": 100.0}, index=index)
    prev_h4 = (frame.index >= START - pd.Timedelta(hours=4))
    frame.loc[prev_h4, ["high", "low"]] = [101.5, 98.5]
    pivots = find_pivots(to_h4(frame))
    labelled = label_context(_setup(extreme=104.0), frame, pivots)
    assert labelled.swept_level == "önceki gün"
    # Short: hedef tarafı dipler; en yakın 98.5 → 2.3 / 100.8 ≥ 2 × stop? stop %3.37 → hayır.
    assert labelled.liquidity == "hayır"
    assert label_context(_setup(extreme=101.8), frame, pivots).swept_level == "önceki 4H"
    assert label_context(_setup(extreme=101.2), frame, pivots).swept_level == "hiçbiri"
    assert label_context(_setup(), frame.iloc[:10], pivots).swept_level == "tanımsız"


def test_power_formula_reproduces_a2_and_the_projection_is_monotone():
    deff_day = projected_deff("day", A2_DEFF["day"][1])
    assert deff_day == pytest.approx(1.860)
    assert projected_mde(287, 306, deff_day) == pytest.approx(0.186, abs=5e-4)
    assert projected_deff("week", 1.0) == 1.0
    assert projected_deff("day", 3.0) > deff_day
    assert projected_mde(0, 500, 1.0) == float("inf")
    assert projected_mde(455, 455, 1.0) < MAX_MDE_HALF_DELTA < projected_mde(150, 150, 1.0)


def test_verify_sigma_checks_the_pinned_pairs_file(tmp_path):
    assert verify_sigma() is None
    copy = tmp_path / "pairs.csv"
    copy.write_text(Path("docs/data/po3_outcome_pairs.csv").read_text(encoding="utf-8") + "\n", encoding="utf-8")
    assert "özet" in verify_sigma(copy)


def _primary(n: int, days: int, side: str = "up") -> list[Setup]:
    stamps = pd.date_range("2022-03-01", periods=days, freq="D", tz="UTC")
    return [_setup(candle=stamps[i % days] + pd.Timedelta(hours=4 * (i // days % 6)), side=side,
                   extreme=104.0 if side == "up" else 97.0, entry=100.8 if side == "up" else 99.5)
            for i in range(n)]


def test_gate_fails_on_sample_and_power_and_never_changes_the_threshold():
    small = _primary(100, 80) + _primary(100, 80, side="down")
    gate = evaluate_gate(small, costs=COSTS)
    assert not gate.passed and not gate.sample_passed
    big = _primary(1000, 1000) + _primary(1000, 1000, side="down")
    assert evaluate_gate(big, costs=COSTS).passed


def test_period_candles_are_4h_aligned_and_inside_the_window():
    candles = period_candles(pd.Timestamp("2022-01-01T01:00:00Z"), pd.Timestamp("2022-01-02T00:00:00Z"))
    assert candles[0] == pd.Timestamp("2022-01-01T04:00:00Z")
    assert candles[-1] == pd.Timestamp("2022-01-01T20:00:00Z")


def test_payload_carries_no_return_fields():
    rows = BOX + [(100.5, 104, 100, 100.6), (100.8, 101, 100, 100.2)] + [QUIET] * 10
    scan = scan_symbol("X", _candle(rows), start=START, end=START + pd.Timedelta(hours=4), threshold=0.0207)
    gate = evaluate_gate(scan.primary, costs=COSTS)
    body = json.dumps(payload([scan], start=START, end=START + pd.Timedelta(hours=4), threshold=0.0207,
                              costs=COSTS, gate=gate))
    for word in ("pnl", "r_net", "r_gross", "win_rate", "return", "avg_r"):
        assert f'"{word}' not in body
    assert scan.count("primary") == 1


def test_does_not_import_measurement_modules():
    tree = ast.parse(Path("scripts/measure_po3_4h.py").read_text(encoding="utf-8"))
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    assert not names & {"core.metrics", "core.portfolio", "core.ledger", "core.engine",
                        "scripts.measure_po3_outcome"}
    assert not any(name.startswith("strategies") for name in names)

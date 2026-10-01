"""`scripts/measure_po3_outcome.py` (§6u): bacak simülasyonu, yansıtma, eşit ağırlık, kapılar.

Ağ yok, defter yok. Sınanan sözleşmeler: kural 13 (aynı barda stop + hedef → stop), boşluklu
stop dolumu, kayma/komisyon ve short stop kayması, zaman çıkışı 23:45 kapanışı, devam bacağının
mesafeleri koruyan yansıtması, köken başına EŞİT AĞIRLIK (çekilişin içinde), ½·ΔR marjı,
preflight'ın R üretmemesi ve ölçüm modüllerinin import edilmemesi.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pandas as pd
import pytest

from scripts.measure_po3 import Setup
from scripts.measure_po3_outcome import (
    EDGE_MARGIN_HALF_R,
    LAST_BAR,
    Costs,
    Leg,
    Pair,
    equal_weight,
    equal_weight_draws,
    evaluate_period,
    evaluate_setup,
    leg_path,
    simulate_leg,
)

DAY = pd.Timestamp("2022-03-01T00:00:00Z")
NO_COST = Costs(fee=0.0, slip=0.0, short_stop_slip=0.0)
COSTS = Costs(fee=0.00055, slip=0.0005, short_stop_slip=0.0015)


def _path(rows: list[tuple[float, float, float, float]], start: pd.Timestamp) -> pd.DataFrame:
    index = pd.date_range(start, periods=len(rows), freq="15min")
    return pd.DataFrame(rows, index=index, columns=["open", "high", "low", "close"])


def test_same_bar_stop_and_target_is_a_stop():
    path = _path([(100, 103, 98, 100)], DAY)
    leg = simulate_leg(path, direction="long", entry=100, stop=99, target=102, costs=NO_COST)
    assert leg.exit_reason == "stop"
    assert leg.r_net == pytest.approx(-1.0)


def test_target_time_and_gap_stop():
    target = simulate_leg(_path([(100, 101, 99.5, 100.5), (100.5, 102.5, 100, 102)], DAY),
                          direction="long", entry=100, stop=99, target=102, costs=NO_COST)
    assert (target.exit_reason, target.r_gross) == ("target", pytest.approx(2.0))
    timed = simulate_leg(_path([(100, 101, 99.5, 100.5), (100.5, 101, 100, 100.7)], DAY),
                         direction="short", entry=100, stop=101.5, target=98, costs=NO_COST)
    assert timed.exit_reason == "time"
    assert timed.r_gross == pytest.approx(-0.7 / 1.5)
    gap = simulate_leg(_path([(100, 100.5, 99.8, 100), (98, 98.5, 97.5, 98)], DAY),
                       direction="long", entry=100, stop=99, target=102, costs=NO_COST)
    assert gap.exit_reason == "stop"
    assert gap.r_gross == pytest.approx(-2.0)  # açılış stop'un ötesinde: aleyhte olan


def test_costs_and_short_stop_slippage():
    path = _path([(100, 102, 99.9, 101)], DAY)
    leg = simulate_leg(path, direction="short", entry=100, stop=101, target=98, costs=COSTS)
    entry_fill = 100 * (1 - COSTS.slip)
    exit_fill = 101 * (1 + COSTS.short_stop_slip)
    expected = -(exit_fill - entry_fill) - COSTS.fee * (entry_fill + exit_fill)
    assert leg.r_net == pytest.approx(expected / 1.0)
    assert leg.r_gross == pytest.approx(-1.0)
    long_leg = simulate_leg(_path([(100, 100.2, 98.5, 99)], DAY), direction="long", entry=100, stop=99,
                            target=102, costs=COSTS)
    long_exit = 99 * (1 - COSTS.slip)  # long'un stop'u slippage_base
    assert long_leg.r_net == pytest.approx(
        (long_exit - 100 * (1 + COSTS.slip)) - COSTS.fee * (100 * (1 + COSTS.slip) + long_exit))


def _setup(side: str = "up", *, entry_bar: pd.Timestamp | None = None, day: pd.Timestamp = DAY) -> Setup:
    entry_bar = entry_bar or day + pd.Timedelta(hours=9)
    up = side == "up"
    return Setup(
        symbol="X", day=day, side=side, sweep_bar=entry_bar - pd.Timedelta(minutes=30),
        reversal_bar=entry_bar - pd.Timedelta(minutes=15), entry_bar=entry_bar, entry=100.0,
        extreme=102.0 if up else 98.0, target=97.0 if up else 103.0,
        high_a=101.0, low_a=97.0 if up else 99.0, asia_close=100.0,
    )


def _day_frame(day: pd.Timestamp = DAY) -> pd.DataFrame:
    index = pd.date_range(day, day + LAST_BAR + pd.Timedelta(hours=1), freq="15min")
    return pd.DataFrame({"open": 100.0, "high": 100.5, "low": 99.5, "close": 100.0}, index=index)


def test_continuation_leg_mirrors_distances_and_time_exit_is_the_2345_close():
    frame = _day_frame()
    frame.loc[DAY + LAST_BAR, "close"] = 101.0
    pair = evaluate_setup(frame, _setup("up"), NO_COST)
    assert pair.po3.direction == "short" and pair.cont.direction == "long"
    assert pair.po3.exit_reason == pair.cont.exit_reason == "time"
    assert pair.po3.exit_bar == DAY + LAST_BAR
    assert pair.po3.r_gross == pytest.approx(-0.5)   # 1.0 aleyhe / d_s 2.0
    assert pair.cont.r_gross == pytest.approx(+0.5)
    assert pair.d_gross == pytest.approx(-1.0)


def test_missing_path_bar_makes_the_setup_unmeasurable():
    frame = _day_frame().drop(DAY + pd.Timedelta(hours=15))
    assert leg_path(frame, _setup()) is None
    assert evaluate_setup(frame, _setup(), NO_COST) is None


def _pair(origin: str, po3: float, cont: float, day: pd.Timestamp) -> Pair:
    setup = _setup("up" if origin == "U" else "down", day=day)
    stamp = day + pd.Timedelta(hours=10)
    return Pair(
        setup=setup,
        po3=Leg(direction="short" if origin == "U" else "long", r_net=po3, r_gross=po3, exit_reason="time", exit_bar=stamp),
        cont=Leg(direction="long" if origin == "U" else "short", r_net=cont, r_gross=cont, exit_reason="time", exit_bar=stamp),
    )


def _days(n: int, freq: str = "D") -> list[pd.Timestamp]:
    return list(pd.date_range("2022-01-03", periods=n, freq=freq, tz="UTC"))


def test_equal_weight_is_origin_balanced_not_pooled():
    days = _days(4)
    pairs = [_pair("U", 1.0, 0.0, d) for d in days[:3]] + [_pair("L", -1.0, 0.0, days[3])]
    assert equal_weight(pairs, lambda p: p.d_net) == pytest.approx(0.0)
    pooled = sum(p.d_net for p in pairs) / len(pairs)
    assert pooled == pytest.approx(0.5)


def test_equal_weight_draws_are_reproducible_and_centered():
    days = _days(40)
    pairs = [_pair("U" if i % 2 else "L", 0.4 + 0.01 * i, 0.0, d) for i, d in enumerate(days)]
    a, _ = equal_weight_draws(pairs, lambda p: p.d_net, definition="day", iterations=500, seed="s")
    b, _ = equal_weight_draws(pairs, lambda p: p.d_net, definition="day", iterations=500, seed="s")
    assert a == b
    assert sum(a) / len(a) == pytest.approx(equal_weight(pairs, lambda p: p.d_net), abs=0.02)


def test_gate_uses_half_delta_margin(monkeypatch):
    import scripts.measure_po3_outcome as mod
    monkeypatch.setattr(mod, "ITERATIONS", 300)
    days = _days(60, "7D")  # hafta kümeleri de köken başına ≥ 10 olsun
    # ΔR = 0.40 → ½·ΔR = 0.20 ≥ 0.15: geçer. ΔR = 0.25 → ½·ΔR = 0.125: marjdan kalır.
    wide = [_pair("U" if i % 2 else "L", 0.30 + 0.001 * i, -0.10, d) for i, d in enumerate(days)]
    narrow = [_pair("U" if i % 2 else "L", 0.20 + 0.001 * i, -0.05, d) for i, d in enumerate(days)]
    ok = evaluate_period(wide, seed_base="t")
    assert ok["e"]["half_delta"] >= EDGE_MARGIN_HALF_R and ok["e"]["passed"] and ok["c1"]["passed"]
    assert ok["passed"]
    low = evaluate_period(narrow, seed_base="t")
    assert low["e"]["delta_equal_weight"] > EDGE_MARGIN_HALF_R  # tam ΔR 0.15'i geçse de
    assert not low["e"]["passed"]


def test_fewer_than_ten_clusters_in_an_origin_is_not_evaluable(monkeypatch):
    import scripts.measure_po3_outcome as mod
    monkeypatch.setattr(mod, "ITERATIONS", 200)
    days = _days(30)
    pairs = [_pair("U", 1.0, -1.0, d) for d in days] + [_pair("L", 1.0, -1.0, d) for d in days[:5]]
    result = evaluate_period(pairs, seed_base="t")
    assert not result["evaluable"]
    assert not result["passed"]


def _tree() -> ast.Module:
    return ast.parse(Path("scripts/measure_po3_outcome.py").read_text(encoding="utf-8"))


def test_does_not_import_measurement_modules():
    names = set()
    for node in ast.walk(_tree()):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    assert not names & {"core.metrics", "core.portfolio", "core.ledger", "core.engine"}
    assert not any(name.startswith("strategies") for name in names)


def test_preflight_never_simulates_a_leg():
    tree = _tree()
    fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "preflight_report")
    called = {n.func.id for n in ast.walk(fn) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
    assert not called & {"simulate_leg", "evaluate_setup", "measure_period", "leg_path"}

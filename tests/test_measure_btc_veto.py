"""`scripts/measure_btc_veto.py` (docs/backtest.md > 6o).

Sınananlar: (1) BTC durumu ileriye bakmaz ve cari getiri kendi ölçeğine girmez; (2) sınıf
tablosu ve |z| = 1 sınırı; (3) 15m işlemin çapası son tam saattir; (4) ileri pencere T'de
AÇILAN bardan başlar, T + H ≤ dönem sonu; (5) D sabit sürüklenmeyi götürür, havuz götürmez
(TADİLAT-2'nin gerekçesi); (6) BH; (7) random_ctrl hiçbir yolda yok; (8) import yasağı;
(9) SHA ve parite kapıları; (10) uçtan uca: preflight R OKUMAZ, measure yükü yazar.
"""

from __future__ import annotations

import ast
import hashlib
import math
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from scripts import measure_btc_veto as bv

SOURCE = Path(bv.__file__).read_text(encoding="utf-8")
H = pd.Timedelta(hours=1)


def _hourly(start: str, closes: list[float]) -> pd.DataFrame:
    idx = pd.date_range(pd.Timestamp(start, tz="UTC"), periods=len(closes), freq="h")
    c = np.asarray(closes, dtype="float64")
    o = np.concatenate([[c[0]], c[:-1]])
    return pd.DataFrame({"open": o, "high": np.maximum(o, c), "low": np.minimum(o, c), "close": c}, index=idx)


def _random_walk(n: int, seed: int, drift: float = 0.0) -> list[float]:
    rng = np.random.default_rng(seed)
    return list(100.0 * np.exp(np.cumsum(drift + 0.01 * rng.standard_normal(n))))


# (1) ------------------------------------------------------------------------------------
def test_state_does_not_look_ahead_and_excludes_current_return():
    frame = _hourly("2022-01-01", _random_walk(2600, 1))
    closes = bv.close_series(frame)
    base = bv.btc_states(closes)
    t = closes.index[2400]
    later = closes.copy()
    later.loc[later.index > t] *= 3.0          # T'den SONRA kapanan her şey değişir
    moved = bv.btc_states(later)
    for h in bv.HORIZONS:
        assert base[h].at[t, "z"] == pytest.approx(moved[h].at[t, "z"])
    g = base["1h"]["g"]
    window = g.loc[(g.index >= t - pd.Timedelta(hours=bv.SCALE_HOURS)) & (g.index < t)]
    assert base["1h"].at[t, "sigma"] == pytest.approx(window.std(ddof=1))


def test_scale_needs_ninety_percent_coverage():
    frame = _hourly("2022-01-01", _random_walk(1900, 2))
    states = bv.btc_states(bv.close_series(frame))
    assert states["1h"]["z"].isna().all()


# (2) ------------------------------------------------------------------------------------
@pytest.mark.parametrize("z,direction,expected", [
    (1.5, "short", bv.KARSI), (1.5, "long", bv.YANINDA),
    (-1.5, "long", bv.KARSI), (-1.5, "short", bv.YANINDA),
    (1.0, "short", bv.NOTR), (-1.0, "long", bv.NOTR), (0.2, "long", bv.NOTR),
    (None, "long", bv.TANIMSIZ), (float("nan"), "short", bv.TANIMSIZ),
])
def test_classification_table(z, direction, expected):
    assert bv.classify(z, direction) == expected


# (3) ------------------------------------------------------------------------------------
def test_fifteen_minute_trade_anchors_to_last_full_hour():
    idx = pd.date_range("2022-01-01T00:00Z", periods=4, freq="h")
    states = pd.DataFrame({"z": [0.1, 0.2, 0.3, 0.4]}, index=idx)
    assert bv.state_at(states, pd.Timestamp("2022-01-01T02:45Z")) == pytest.approx(0.3)
    assert bv.state_at(states, pd.Timestamp("2022-01-01T02:00Z")) == pytest.approx(0.3)


# (4) ------------------------------------------------------------------------------------
def test_forward_window_starts_at_the_open_of_the_next_bar(monkeypatch):
    anchor = pd.Timestamp("2022-03-01T10:00Z")
    states = pd.DataFrame({"z": [2.0]}, index=[anchor])
    frame = _hourly("2022-03-01T00:00Z", [100.0 + i for i in range(30)])
    frame.loc[anchor, "open"] = 50.0          # T'de AÇILAN barın açılışı
    closes = {s: bv.close_series(frame) for s in bv.ALTCOINS}
    opens = {s: bv.open_series(frame) for s in bv.ALTCOINS}
    monkeypatch.setitem(bv.PRICE_PERIODS, "A", (pd.Timestamp("2022-03-01T00:00Z"), pd.Timestamp("2022-03-02T00:00Z")))
    obs, info = bv.price_observations(states, 4, "A", closes, opens, with_values=True)
    assert len(obs) == len(bv.ALTCOINS) and info["strong_up_hours"] == 1
    expected_close = frame.loc[anchor + 3 * H, "close"]        # T + 4h'de KAPANAN bar
    assert obs[0].forward == pytest.approx(math.log(expected_close / 50.0))
    # T + H dönem sonunu aşarsa gözlem yok
    monkeypatch.setitem(bv.PRICE_PERIODS, "A", (pd.Timestamp("2022-03-01T00:00Z"), anchor + 3 * H))
    obs, _ = bv.price_observations(states, 4, "A", closes, opens, with_values=True)
    assert obs == []
    # preflight: değer HESAPLANMAZ
    monkeypatch.setitem(bv.PRICE_PERIODS, "A", (pd.Timestamp("2022-03-01T00:00Z"), pd.Timestamp("2022-03-02T00:00Z")))
    obs, _ = bv.price_observations(states, 4, "A", closes, opens, with_values=False)
    assert obs and all(o.forward is None for o in obs)


# (5) ------------------------------------------------------------------------------------
def test_D_removes_constant_drift_that_the_pooled_mean_keeps():
    rng = np.random.default_rng(3)
    drift = 20.0
    days = pd.date_range("2022-01-01T00:00Z", periods=200, freq="D")
    up_t, up_f, down_t, down_f = [], [], [], []
    for i, day in enumerate(days):
        n_up = 9 if i % 2 else 7                                  # yukarı durum DAHA SIK
        up_t += [day] * n_up
        up_f += list(drift + rng.standard_normal(n_up))
        down_t += [day] * 3
        down_f += list(drift + rng.standard_normal(3))
    d = bv.contrast(up_t, up_f, down_t, down_f, definitions=("day", "week"), iterations=300,
                    alpha=0.05, seed="t", scale=0.5)
    pooled = np.mean(up_f + [-f for f in down_f])
    assert abs(d["estimate"]) < 0.2
    assert pooled > 5.0
    assert d["evaluable"] and d["low_binding"] < 0 < d["high_binding"]


def test_binding_reading_takes_the_conservative_side():
    days = pd.date_range("2022-01-01T00:00Z", periods=120, freq="D")
    rng = np.random.default_rng(4)
    a = list(1.0 + rng.standard_normal(120))
    b = list(rng.standard_normal(120))
    c = bv.contrast(list(days), a, list(days), b, definitions=("day", "week"), iterations=300,
                    alpha=0.05, seed="t")
    lows = [c["definitions"][k]["low"] for k in ("day", "week")]
    highs = [c["definitions"][k]["high"] for k in ("day", "week")]
    assert c["low_binding"] == min(lows) and c["high_binding"] == max(highs)
    assert c["p_binding"] == max(c["definitions"][k]["p"] for k in ("day", "week"))


def test_small_side_is_not_evaluable():
    days = list(pd.date_range("2022-01-01T00:00Z", periods=40, freq="D"))
    c = bv.contrast(days[:20], [1.0] * 20, days, [0.0] * 40, definitions=("day",), iterations=50,
                    alpha=0.05, seed="t")
    assert not c["evaluable"] and c["p_binding"] == 1.0


# (6) ------------------------------------------------------------------------------------
def test_bh():
    assert bv.bh_reject({"1h": 0.01, "4h": 0.04, "1d": 0.5}) == {"1h": True, "4h": False, "1d": False}
    assert bv.bh_reject({"1h": 0.02, "4h": 0.03, "1d": 0.049}) == {"1h": True, "4h": True, "1d": True}
    assert bv.bh_reject({"1h": 0.02, "4h": 0.9, "1d": 0.9}) == {"1h": False, "4h": False, "1d": False}
    assert bv.bh_reject({}) == {}


# (7) ------------------------------------------------------------------------------------
def test_broken_control_is_nowhere():
    assert all(u.model != "random_ctrl" for u in (*bv.CONTROL_UNITS, *bv.MODEL_UNITS))
    assert {u.model for u in bv.CONTROL_UNITS if u.role == "primary"} == {"dc_coinflip", "xsec_random"}
    assert bv.BTC not in bv.ALTCOINS and len(bv.ALTCOINS) == 12


# (8) ------------------------------------------------------------------------------------
def test_does_not_import_strategies_or_writers():
    modules = set()
    for node in ast.walk(ast.parse(SOURCE)):
        if isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
        elif isinstance(node, ast.Import):
            modules.update(a.name for a in node.names)
    assert not any(m.startswith("strategies") for m in modules)
    assert not {"core.portfolio", "core.engine", "core.ledger"} & modules


def test_preregistered_constants():
    assert bv.PRICE_PERIODS["A"] == (pd.Timestamp("2022-01-01T00:00Z"), pd.Timestamp("2024-06-30T00:00Z"))
    assert bv.PRICE_PERIODS["B"] == (pd.Timestamp("2024-07-01T00:00Z"), pd.Timestamp("2026-09-18T12:00Z"))
    assert bv.HORIZONS == {"1h": 1, "4h": 4, "1d": 24} and bv.SCALE_HOURS == 2160
    assert bv.STRONG_Z == 1.0 and bv.BH_Q == 0.05 and bv.MIN_N == 30
    assert bv.PRICE_DEFINITIONS == ("day", "week") and bv.TRADE_DEFINITIONS == ("month", "week")


# (9) ------------------------------------------------------------------------------------
def test_parity_gate(tmp_path):
    frame = _hourly("2022-01-01", [100.0 + i for i in range(12)])
    rows = ["bar,symbol,ts,open,close",
            f"4H,{bv.BTC},2022-01-01T00:00:00+00:00,{frame['open'].iloc[0]},{frame['close'].iloc[3]}",
            f"4H,{bv.BTC},2022-01-01T04:00:00+00:00,{frame['open'].iloc[4]},{frame['close'].iloc[7]}",
            f"15m,{bv.BTC},2022-01-01T00:00:00+00:00,1,1"]
    path = tmp_path / "prices.csv"
    path.write_text("\n".join(rows) + "\n")
    frames = {s: frame for s in bv.SYMBOLS}
    gate = bv.parity_gate(frames, path)
    assert gate["passed"] and gate["per_symbol"][bv.BTC]["checked"] == 2
    rows[2] = rows[2].rsplit(",", 1)[0] + ",999"
    path.write_text("\n".join(rows) + "\n")
    assert not bv.parity_gate(frames, path)["passed"]


# (10) -----------------------------------------------------------------------------------
@pytest.fixture()
def world(tmp_path, monkeypatch):
    n = 2160 + 700
    start = "2022-01-01T00:00Z"
    frames = {s: _hourly(start, _random_walk(n, i + 10)) for i, s in enumerate(bv.SYMBOLS)}
    monkeypatch.setattr(bv, "SNAPSHOT_START", pd.Timestamp(start))
    bv.write_pins(frames, tmp_path / "pins", end=pd.Timestamp(start) + n * H, run="#test")
    t0 = pd.Timestamp(start) + 2170 * H
    monkeypatch.setitem(bv.PRICE_PERIODS, "A", (t0, t0 + 300 * H))
    monkeypatch.setitem(bv.PRICE_PERIODS, "B", (t0 + 301 * H, t0 + 650 * H))
    rng = np.random.default_rng(5)
    lines = ["source,period,model,symbol,direction,opened_at,closed_at,r,pnl,coin_ret,btc_ret,aligned_btc,aligned_coin"]
    for k in range(400):
        unit = bv.CONTROL_UNITS[k % 2]
        sym = bv.SYMBOLS[k % 13]
        opened = (t0 + int(rng.integers(0, 600)) * H).isoformat()
        direction = "long" if rng.random() < 0.5 else "short"
        lines.append(f"{unit.source},{unit.period},{unit.model},{sym},{direction},{opened},{opened},"
                     f"{rng.standard_normal():.4f},0,0,0,aligned,aligned")
    trades = tmp_path / "trades.csv"
    trades.write_text("\n".join(lines) + "\n")
    monkeypatch.setattr(bv, "TRADES_SHA256", hashlib.sha256(trades.read_bytes()).hexdigest())
    prices = tmp_path / "prices.csv"
    prices.write_text("bar,symbol,ts,open,close\n")
    monkeypatch.setattr(bv, "SCALE_MIN_SHARE", 0.9)
    return {"pins": tmp_path / "pins", "trades": trades, "prices": prices, "out": tmp_path / "out"}


def _argv(world, stage):
    return ["--stage", stage, "--pins", str(world["pins"]), "--trades", str(world["trades"]),
            "--prices", str(world["prices"]), "--out-dir", str(world["out"])]


def test_coverage_gate_trips_on_short_btc_history(world):
    assert bv.main(_argv(world, "preflight")) == 3      # 91 günlük geriye bakış yok


def test_end_to_end_preflight_reads_no_r_and_measure_writes(world, monkeypatch):
    monkeypatch.setattr(bv, "coverage_gate", lambda btc, earliest: {"passed": True})
    # preflight R OKUMAZ: R kolonu bozuk olsa bile geçer
    text = world["trades"].read_text().splitlines()
    broken = [text[0]] + [",".join(line.split(",")[:7] + ["x"] + line.split(",")[8:]) for line in text[1:]]
    world["trades"].write_text("\n".join(broken) + "\n")
    monkeypatch.setattr(bv, "TRADES_SHA256", hashlib.sha256(world["trades"].read_bytes()).hexdigest())
    assert bv.main(_argv(world, "preflight")) == 0
    pre = (world["out"] / "btc_veto_preflight.json").read_text()
    assert '"mean"' not in pre and "D_bp" not in pre and "mean_r" not in pre

    world["trades"].write_text("\n".join(text) + "\n")
    monkeypatch.setattr(bv, "TRADES_SHA256", hashlib.sha256(world["trades"].read_bytes()).hexdigest())
    assert bv.main(_argv(world, "measure")) == 0
    import json
    report = json.loads((world["out"] / "btc_veto_results.json").read_text())
    assert set(report["price_verdict"]["confirmed"]) == set(bv.HORIZONS)
    assert report["price_test"]["A"]["1h"]["D_bp"]["n_a"] > 0
    primary = [u for u in report["trade_level"] if u["role"] == "primary"]
    assert all("decision" in u["horizons"]["1h"] for u in primary)
    assert all(u["horizons"]["1h"]["decision"]["reading"].startswith(("betimsel", "koşullu")) for u in primary)


def test_trades_sha_gate(world):
    world["trades"].write_text(world["trades"].read_text() + "\n")
    assert bv.main(_argv(world, "preflight")) == 3
    assert bv.main(_argv(world, "snapshot")) == 3


# (11) TADİLAT-3 --------------------------------------------------------------------------
def test_combo_draws_and_precision_reduce_to_the_two_group_originals():
    from scripts.backtest_dc import cluster_diff_draws, precision_diff
    rng = np.random.default_rng(7)
    a = {f"c{i}": list(rng.standard_normal(int(rng.integers(1, 6)))) for i in range(25)}
    b = {f"c{i}": list(rng.standard_normal(int(rng.integers(1, 6)))) for i in range(5, 32)}
    original, dropped = cluster_diff_draws(a, b, iterations=200, seed="s")
    combo, dropped_c = bv.combo_draws([(1.0, a), (-1.0, b)], iterations=200, seed="s")
    assert combo == pytest.approx(original) and dropped_c == dropped
    assert bv.precision_combo([(1.0, a), (-1.0, b)])["se_cluster"] == pytest.approx(precision_diff(a, b)["se_cluster"])


def test_balanced_contrast_removes_constant_drift():
    rng = np.random.default_rng(8)
    drift = 0.5
    days = pd.date_range("2022-01-01T00:00Z", periods=420, freq="D")      # ≥ 10 ay kümesi
    rows, cls = [], []
    for i, day in enumerate(days):
        for _ in range(4 if i % 3 else 1):                     # yukarı durum DAHA SIK
            for direction in ("long", "short"):
                sign = 1 if direction == "long" else -1
                rows.append({"opened_at": day, "direction": direction, "r": sign * drift + rng.standard_normal()})
                cls.append(bv.YANINDA if direction == "long" else bv.KARSI)       # yukarı durum
        for direction in ("long", "short"):
            sign = 1 if direction == "long" else -1
            rows.append({"opened_at": day, "direction": direction, "r": sign * drift + rng.standard_normal()})
            cls.append(bv.KARSI if direction == "long" else bv.YANINDA)           # aşağı durum
    frame = pd.DataFrame(rows)
    classes = pd.Series(cls, index=frame.index)
    old = frame.loc[classes == bv.KARSI, "r"].mean() - frame.loc[classes != bv.KARSI, "r"].mean()
    res = bv.combo_contrast(bv.balanced_cells(frame, classes), definitions=("month", "week"),
                            iterations=300, alpha=0.05, seed="t")
    assert old < -0.3                                           # eski karşıtlık sürüklenmeyi taşır
    assert abs(res["estimate"]) < 0.15 and res["evaluable"]
    assert res["low_binding"] < 0 < res["high_binding"]


def test_direction_class_is_the_models_own_declaration():
    from strategies.registry import REGISTRY
    for model, klass in bv.DIRECTION_CLASS.items():
        declared = set(REGISTRY[model].allowed_directions)
        assert declared == ({"long", "short"} if klass == "both" else {klass}), model
    assert {u.model for u in (*bv.CONTROL_UNITS, *bv.MODEL_UNITS)} <= set(bv.DIRECTION_CLASS)

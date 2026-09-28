"""Maker yürütme ölçümü (docs/backtest.md > 6s, TADİLAT-1) — ağsız testler."""

from __future__ import annotations

import re
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from scripts import measure_maker_execution as mx
from scripts import measure_texture_regime as tx

SCRIPT = Path(mx.__file__)
TAU = 0.001
MU = 0.0002
SETTINGS = mx.Settings(seed=7, taker_rate=TAU, maker_rate=MU)


def _prices(opens, highs, lows, closes) -> mx.Prices:
    o = np.asarray(opens, dtype=float)[:, None]
    h = np.asarray(highs, dtype=float)[:, None]
    lo = np.asarray(lows, dtype=float)[:, None]
    c = np.asarray(closes, dtype=float)[:, None]
    r = np.append(o[1:, 0], np.nan) / o[:, 0] - 1.0
    r = np.where(np.isfinite(r), r, 0.0)[:, None]
    return mx.Prices(open=o, high=h, low=lo, close=c, returns=r, eligible=np.ones_like(o, dtype=bool))


def _run(kind, target, px, *, window=2):
    """Tek taban, tek sembol; bütün satırlar tek haftada (satır 0 ısınma)."""
    w = np.asarray(target, dtype=float)[:, None, None]
    week = np.zeros(len(target), dtype=int)
    week[0] = -1
    if kind == "taker":
        net, hedged, turn = mx.run_taker(w, px, week, 1, 0, TAU)
        return net, hedged, turn, None
    return mx.run_maker(kind, w, px, week, 1, 0, window=window, tau=TAU, mu=MU)


# --------------------------------------------------------------------------- #
# Ön-kayıtlı sabitler
# --------------------------------------------------------------------------- #
def test_constants_are_preregistered():
    assert mx.TAKER_FEE == 0.0005 and mx.MAKER_FEE == 0.0002
    assert mx.WINDOW_BARS == {"1D": 1, "4H": 2, "15m": 4}
    assert mx.ITERATIONS == 10_000 and mx.CI_ALPHA == 0.05 and mx.BH_Q == 0.05
    assert mx.WARMUP == pd.Timedelta(weeks=4)
    assert set(mx.WINDOW_BARS) == set(tx.ARMS)


def test_settings_use_okx_fees_not_fee_rate():
    s = mx.settings_from({"random_seed": 1, "slippage_base": 0.0005, "fee_rate": 0.00055})
    assert s.taker_rate == pytest.approx(0.001)
    assert s.maker_rate == pytest.approx(0.0002)


def test_bases_are_texture_bases_single_copy():
    assert mx.bases is tx.bases
    assert len(mx.bases()) == 59


def test_no_cli_inputs_for_periods_or_parameters():
    args = mx._parse_args(["--stage", "preflight"])
    assert set(vars(args)) == {"stage", "config", "cache_dir", "out_dir"}


def test_does_not_import_engine_or_read_other_outputs():
    text = SCRIPT.read_text(encoding="utf-8")
    for forbidden in ("strategies", "core.portfolio", "core.engine", "core.ledger", "core.metrics",
                      "texture_regime.json", "texture_regime_", "model_momentum_periods", "model_momentum.json"):
        assert not re.search(rf"(import|from)\s+{re.escape(forbidden)}", text), forbidden
    assert "texture_regime_days" not in text and "model_momentum_periods" not in text


# --------------------------------------------------------------------------- #
# Emir yaşam döngüsü (§6s > 5)
# --------------------------------------------------------------------------- #
def test_buy_fill_requires_strict_trade_through():
    # satır 1'de hedef 0 → 1: limit = C_0 = 100. Satır 1'in low'u tam 100: DOLMAZ; satır 2'de 99.9: dolar.
    target = [0, 1, 1, 1, 1]
    px = _prices([100, 100, 100, 101, 102], [101, 101, 101, 102, 103], [99, 100, 99.9, 100.5, 101],
                 [100, 100, 101, 102, 102])
    *_, stats = _run("melez", target, px)
    assert stats["fills"].sum() == 1
    assert stats["first_bar_fills"].sum() == 0
    assert stats["fallbacks"].sum() == 0


def test_sell_fill_is_symmetric():
    target = [1, 0, 0, 0]
    px = _prices([100, 100, 100, 100], [100, 100.5, 101, 101], [99, 99, 99, 99], [100, 100, 100, 100])
    *_, stats = _run("melez", target, px)
    assert stats["fills"].sum() == 1 and stats["first_bar_fills"].sum() == 1
    px_touch = _prices([100, 100, 100, 100], [100, 100, 100, 100], [99, 99, 99, 99], [100, 100, 100, 100])
    *_, stats = _run("melez", target, px_touch)
    assert stats["fills"].sum() == 0 and stats["fallbacks"].sum() == 1


def test_fill_accounting_is_marked_to_next_open():
    # alış limiti 100, satır 1'de dolar; O_2 = 103 → katkı (103/100 − 1) − μ.
    target = [0, 1, 1]
    px = _prices([100, 100, 103], [101, 101, 104], [99, 99.5, 102], [100, 102, 103])
    net, *_ = _run("melez", target, px)
    assert net.sum() == pytest.approx(0.03 - MU)


def test_melez_falls_back_to_taker_after_n_bars():
    # N = 2: satır 1 ve 2'de dolmaz (low ≥ 100) → satır 3'ün açılışında (O_3 = 104) taker.
    target = [0, 1, 1, 1, 1]
    px = _prices([100, 100, 102, 104, 105], [101, 102, 104, 105, 106], [100, 100, 101, 103, 104],
                 [100, 102, 104, 105, 105])
    net, _, turn, stats = _run("melez", target, px, window=2)
    assert stats["fallbacks"].sum() == 1 and stats["fills"].sum() == 0
    assert stats["miss_move"].sum() == pytest.approx(104 / 100 - 1)
    assert net.sum() == pytest.approx((105 / 104 - 1) - TAU)
    assert turn.sum() == pytest.approx(1.0)


def test_pure_maker_cancels_and_does_not_reorder():
    target = [0, 1, 1, 1, 1, 1, 0]
    px = _prices([100, 100, 102, 104, 103, 101, 100], [101, 102, 104, 105, 104, 102, 101],
                 [100, 100, 101, 103, 102.5, 100, 99], [100, 102, 104, 104, 102, 100, 100])
    net, _, turn, stats = _run("maker", target, px, window=2)
    assert stats["cancels"].sum() == 1 and stats["orders"].sum() == 1
    assert stats["fills"].sum() == 0
    assert turn.sum() == 0.0
    assert net.sum() == 0.0
    # kaçırılan getiri: taker'ın satır 1 … 5 boyunca tuttuğu (hedef satır 6'da değişir)
    assert stats["forgone"].sum() == pytest.approx(px.returns[1:6, 0].sum())


def test_target_change_inside_window_supersedes_without_fallback():
    # satır 1'de 0 → 1, satır 2'de 1 → 0 (hiç dolmadan): eski emir iptal, P = 0, yeni emir yok.
    target = [0, 1, 0, 0, 0]
    px = _prices([100, 100, 102, 104, 105], [101, 102, 104, 105, 106], [100, 100, 101, 103, 104],
                 [100, 102, 104, 105, 105])
    net, _, turn, stats = _run("melez", target, px, window=2)
    assert stats["superseded"].sum() == 1
    assert stats["fallbacks"].sum() == 0
    assert stats["orders"].sum() == 1
    assert turn.sum() == 0.0 and net.sum() == 0.0


def test_taker_matches_formula():
    target = [0, 1, 1, -1, 0]
    px = _prices([100, 101, 103, 102, 100], [101] * 5, [99] * 5, [101, 103, 102, 100, 100])
    net, hedged, turn, _ = _run("taker", target, px)
    w = np.asarray(target, dtype=float)
    r = px.returns[:, 0]
    expected = sum(w[t] * r[t] - TAU * abs(w[t] - w[t - 1]) for t in range(1, 5))
    assert net.sum() == pytest.approx(expected)
    assert turn.sum() == pytest.approx(sum(abs(w[t] - w[t - 1]) for t in range(1, 5)))
    # tek sembollü sepet: hedge maruziyeti birebir siler, kalan yalnızca iki bacağın maliyeti
    exposure_cost = sum(TAU * abs(w[t] - w[t - 1]) for t in range(1, 5))
    assert hedged.sum() == pytest.approx(-2 * exposure_cost)


def test_decomposition_identity_without_gaps():
    """`O_{b+1} = C_b` ve her emir ilk barda dolarsa: melez − taker = (τ − μ)·Σ|Q| (ε = 0)."""
    rng = np.random.default_rng(3)
    n = 200
    closes = 100 * np.exp(np.cumsum(rng.normal(0, 0.01, n)))
    opens = np.r_[100.0, closes[:-1]]
    highs = np.maximum(opens, closes) * 1.01
    lows = np.minimum(opens, closes) * 0.99
    px = _prices(opens, highs, lows, closes)
    target = np.sign(rng.normal(size=n))
    tn, *_ = _run("taker", target, px)
    mn, _, _, stats = _run("melez", target, px)
    assert stats["fills"].sum() == stats["orders"].sum() == stats["first_bar_fills"].sum()
    assert (mn - tn).sum() == pytest.approx(stats["fee_saving"].sum())


def test_decisions_do_not_look_ahead():
    """`b+N`'den sonraki barları bozmak, `b`'de doğan emrin dolum kararını değiştirmez."""
    target = [0, 1, 1, 1, 1, 1, 1]
    base = dict(opens=[100, 100, 100, 100, 100, 100, 100], highs=[101] * 7,
                lows=[100, 100, 100, 99, 99, 99, 99], closes=[100] * 7)
    px1 = _prices(**base)
    changed = dict(base, lows=[100, 100, 100, 50, 50, 50, 50], highs=[101, 101, 101, 300, 300, 300, 300])
    px2 = _prices(**changed)
    s1 = _run("melez", target, px1, window=2)[3]
    s2 = _run("melez", target, px2, window=2)[3]
    for key in ("orders", "fills", "fallbacks", "first_bar_fills"):
        assert s1[key].sum() == s2[key].sum(), key


def test_signals_identical_across_variants():
    """Hedef matrisi yürütmeye girdi olarak verilir ve hiçbir varyant onu değiştirmez."""
    rng = np.random.default_rng(1)
    w = np.sign(rng.normal(size=(50, 2, 3))) / 3
    before = w.copy()
    opens = 100 + np.cumsum(rng.normal(0, 1, (50, 3)), axis=0)
    px = mx.Prices(open=opens, high=opens + 1, low=opens - 1, close=opens + 0.1,
                   returns=np.vstack([opens[1:] / opens[:-1] - 1, np.zeros((1, 3))]),
                   eligible=np.ones((50, 3), dtype=bool))
    week = np.r_[-1, np.zeros(49, dtype=int)]
    mx.simulate(w, px, week, 1, 0, window=2, settings=SETTINGS)
    assert np.array_equal(w, before)


# --------------------------------------------------------------------------- #
# İstatistik ve karar
# --------------------------------------------------------------------------- #
def test_bootstrap_deterministic_and_p():
    values = np.linspace(0.001, 0.003, 30)
    a = mx.bootstrap(values, seed="x")
    b = mx.bootstrap(values, seed="x")
    assert a == b and a["low"] > 0 and a["p"] == pytest.approx(1 / (mx.ITERATIONS + 1))
    assert not mx.bootstrap(values[:9], seed="x")["evaluable"]


def test_decide_improvement_readings():
    ok = {"evaluable": True, "low": 0.1, "high": 0.2}
    zero = {"evaluable": True, "low": -0.1, "high": 0.1}
    worse = {"evaluable": True, "low": -0.3, "high": -0.1}
    ev = {a: {"A": True, "B": True} for a in ("x", "y", "z", "q")}
    out = mx.decide_improvement({"x": {"A": ok, "B": ok}, "y": {"A": ok, "B": zero},
                                 "z": {"A": worse, "B": ok}, "q": {"A": zero, "B": ok}}, ev)
    assert out["x"]["verdict"] == mx.VERIFIED
    assert out["y"]["reading"] == "doğrulanamadı" and out["y"]["verdict"] == mx.NOT_VERIFIED
    assert out["z"]["reading"].startswith("melez maker KÖTÜ")
    assert out["q"]["reading"] == "ayırt edilemedi" and out["q"]["B"] == mx.INFO_ONLY


def test_decide_profit_bh_and_b_confirmation():
    good = {"evaluable": True, "low": 0.01, "p": 0.0001}
    weak = {"evaluable": True, "low": 0.01, "p": 0.04}          # BH m=21'de düşer
    neg = {"evaluable": True, "low": -0.01, "p": 0.5}
    stats = {f"1D/F{i}": {"A": neg, "B": good} for i in range(1, 8)}
    stats.update({f"4H/F{i}": {"A": neg, "B": good} for i in range(1, 8)})
    stats.update({f"15m/F{i}": {"A": neg, "B": good} for i in range(1, 8)})
    stats["15m/F4"] = {"A": good, "B": good}
    stats["15m/F7"] = {"A": weak, "B": good}
    stats["4H/F4"] = {"A": good, "B": neg}
    ev = {a: {"A": True, "B": True} for a in ("1D", "4H", "15m")}
    out = mx.decide_profit(stats, ev)
    assert out["m_A"] == 21 and out["m_B"] == 2
    assert out["cells"]["15m/F4"]["verdict"] == mx.VERIFIED
    assert out["cells"]["15m/F7"]["A"] == mx.FAILED
    assert out["cells"]["4H/F4"]["A"] == mx.PASSED and out["cells"]["4H/F4"]["verdict"] == mx.NOT_VERIFIED


def test_combined_reading_table():
    assert "KASA TESTİ" in mx.combined_reading(mx.VERIFIED, ["15m/F4"])
    assert mx.combined_reading(mx.VERIFIED, []).startswith("maker yürütme maliyeti düşürüyor")
    assert mx.combined_reading(mx.NOT_VERIFIED, ["x"]).startswith("aile maker'la net pozitif")
    assert mx.combined_reading(mx.NOT_VERIFIED, []) == "ayırt edilemedi"


def test_week_index_matches_texture_weeks():
    grid = pd.date_range("2021-11-01", "2026-09-27", freq="1D", tz="UTC", inclusive="left")
    week, slices = mx.week_index(grid)
    assert slices["A"] == slice(0, 129) and slices["B"] == slice(129, 245)
    lo_a, _ = tx.measurement_weeks()["A"]
    assert week[grid.get_loc(lo_a)] == 0 and week[grid.get_loc(lo_a) - 1] == -1
    assert mx.start_row(grid) == grid.get_loc(lo_a - pd.Timedelta(weeks=4))


# --------------------------------------------------------------------------- #
# Sentetik evrenle uçtan uca (1D ve 4H; ağsız)
# --------------------------------------------------------------------------- #
@pytest.fixture(scope="module")
def synthetic_data() -> tx.Data:
    rng = np.random.default_rng(11)
    index = pd.date_range(tx.DATA_START_4H, mx.KASA_START, freq="4h", tz="UTC", inclusive="left")
    symbols = [f"S{i}-USDT-SWAP" for i in range(6)]
    h4 = {}
    for s in symbols:
        close = 100 * np.exp(np.cumsum(rng.normal(0, 0.01, len(index))))
        open_ = np.r_[close[0], close[:-1]]
        spread = np.abs(rng.normal(0, 0.004, len(index)))
        h4[s] = pd.DataFrame({"open": open_, "high": np.maximum(open_, close) * (1 + spread),
                              "low": np.minimum(open_, close) * (1 - spread), "close": close}, index=index)
    return tx.Data(symbols=symbols, h4=h4, m15={s: pd.DataFrame() for s in symbols},
                   first_4h={s: index[0] for s in symbols}, report={})


def test_preflight_order_counts_have_no_outcome_fields(synthetic_data):
    frames, start = tx.arm_frames(synthetic_data, "1D")
    arm = tx.build_arm("1D", frames, synthetic_data.first_4h, synthetic_data.symbols, start=start)
    counts = mx.order_counts(arm, mx.bases())
    text = repr(counts).lower()
    for forbidden in ("fill", "return", "net", "miss", "adverse", "pnl", "gross"):
        assert forbidden not in text, forbidden
    assert counts["A"]["orders"] > 0 and set(counts["A"]["by_family"]) == set(tx.FAMILIES)


def test_end_to_end_1d_and_4h(synthetic_data):
    for name in ("1D", "4H"):
        out = mx.run_arm(name, synthetic_data, SETTINGS, mx.bases())
        assert out.result.net["taker"].shape == (245, 59)
        assert out.base_ids == [b.id for f in tx.FAMILIES for b in mx.bases() if b.family == f]
        d = mx.describe(out, "A")
        dec = d["decomposition_melez"]
        assert dec["delta"] == pytest.approx(dec["fee_saving"] + dec["miss_cost"] + dec["residual"])
        melez = d["families"]["bahçe"]["melez"]
        assert 0.0 < melez["fill_rate"] <= 1.0
        assert melez["orders"] > 0


def test_precision_weeks_cover_known_window_and_day_only_in_a():
    a = mx.precision_weeks("A")
    b = mx.precision_weeks("B")
    assert not b.any()
    lo, _ = tx.measurement_weeks()["A"]
    starts = pd.date_range(lo, periods=len(a), freq="7D", tz="UTC")
    hit = set(starts[a].strftime("%Y-%m-%d"))
    # 2022-04-23 (Cmt) → 06-02: 04-18 haftasından 05-30 haftasına 7 hafta; 2022-12-18 (Paz) → 12-12 haftası
    assert hit == {"2022-04-18", "2022-04-25", "2022-05-02", "2022-05-09", "2022-05-16", "2022-05-23",
                   "2022-05-30", "2022-12-12"}

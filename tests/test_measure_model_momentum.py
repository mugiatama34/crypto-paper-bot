"""`scripts/measure_model_momentum.py`: ön-kayda (docs/backtest.md > 6q, TADİLAT-1, TADİLAT-2) MEKANİK sadakat.

Sınanan: (1) sabitler ve dönemler girdi değildir, çift sayıları ön-kayıttaki gibidir; (2) kasa: hiçbir
bar kasaya taşmaz; (3) bahçe 88 tabandır ve hiçbir aile İLERİYE BAKMAZ; (4) tersler: saatlik ve
dönem getirisi birebir negatif, bahçe ortalaması her dönemde 0 (ham ve hedge'li); (5) hedge özdeşliği
`R^art = R − e·m` ve F6 LS'de `v = w`; (6) vektörel Bollinger/Donchian ↔ `core/indicators.py`;
(7) devir/maliyet aritmetiği; (8) çift kuralı; (9) sıralama, beşte bir, bootstrap determinizmi, p,
BH, karar ve okuma tabloları; (10) preflight hiçbir getiri üretmez; (11) import yasağı.
"""

from __future__ import annotations

import ast
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from core import indicators
from scripts import measure_model_momentum as mm
from scripts import vault

SOURCE = Path("scripts/measure_model_momentum.py").read_text(encoding="utf-8")
SETTINGS = mm.Settings(iterations=200, alpha=0.05, seed=7, cost_rate=0.00105)


def synthetic(symbols, start, end, *, seed=0):
    rng = np.random.default_rng(seed)
    idx = pd.date_range(start, end - pd.Timedelta(hours=1), freq="h", tz="UTC")
    h1, h4 = {}, {}
    for s in symbols:
        close = 100.0 * np.exp(rng.normal(0.0, 0.006, len(idx)).cumsum())
        opens = np.r_[100.0, close[:-1]]
        frame = pd.DataFrame({"open": opens, "high": np.maximum(opens, close) * 1.001,
                              "low": np.minimum(opens, close) * 0.999, "close": close, "volume": 1.0}, index=idx)
        h1[s] = frame
        h4[s] = to_4h(frame)
    return h1, h4


def to_4h(frame):
    return frame.resample("4h", label="left", closed="left").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}).dropna()


SYMBOLS = [f"S{i}" for i in range(9)]
START = pd.Timestamp("2022-01-01T00:00:00Z")
END = pd.Timestamp("2022-05-01T00:00:00Z")


@pytest.fixture(scope="module")
def small():
    h1, h4 = synthetic(SYMBOLS, START, END)
    return h1, h4, mm.build_market(h1, h4, SYMBOLS, start=START, end=END)


# (1) --------------------------------------------------------------------------
def test_preregistered_constants():
    assert mm.QUANTILE == 0.2 and mm.BH_Q == 0.05 and mm.HORIZONS == ("month", "week")
    assert mm.BLOCKS == {"month": (1, 2), "week": (1, 4)}
    assert mm.ELIGIBLE_AGE == pd.Timedelta(days=60)
    assert mm.DATA_START <= pd.Timestamp("2022-01-01", tz="UTC") - pd.Timedelta(days=90)
    assert mm.quintile_size(176) == 35


def test_period_bounds_are_not_cli_inputs():
    args = mm._parse_args(["--stage", "measure"])
    for name in ("now", "b_end", "a_start", "start", "end", "quantile", "horizon", "q"):
        assert not hasattr(args, name)


def test_pairs_match_preregistration():
    hours = mm.hour_grid(mm.DATA_START, mm.KASA_START)
    month, week = mm.period_table(hours, "month"), mm.period_table(hours, "week")
    assert {k: len(v) for k, v in month.pairs.items()} == {"A": 28, "B": 25}
    assert {k: len(v) for k, v in week.pairs.items()} == {"A": 128, "B": 115}
    labels = month.labels
    assert "2024-06" not in labels and "2026-09" not in labels
    assert month.labels[month.pairs["A"][0][0]] == "2022-01" and month.labels[month.pairs["A"][-1][1]] == "2024-05"
    assert month.labels[month.pairs["B"][0][0]] == "2024-07" and month.labels[month.pairs["B"][-1][1]] == "2026-08"
    last_week_hour = hours[week.ids == week.pairs["B"][-1][1]][-1]
    assert last_week_hour == pd.Timestamp("2026-09-20T23:00:00Z")


# (2) --------------------------------------------------------------------------
def test_vault_cutoff_is_single_constant():
    assert mm.KASA_START is vault.KASA_START
    assert vault.KASA_START == pd.Timestamp("2026-09-27T00:00:00Z")
    vault.assert_before_vault(vault.KASA_START)
    with pytest.raises(vault.VaultError):
        vault.assert_before_vault(vault.KASA_START + pd.Timedelta(seconds=1))


def test_fetch_uses_vault_now_and_rejects_vault_bars(monkeypatch):
    seen = []

    def fake(config, symbol, *, now):
        seen.append((config["timeframe"], now))
        idx = pd.date_range(mm.DATA_START, now - pd.Timedelta(hours=1), freq="h", tz="UTC")[-5:]
        return pd.DataFrame({"open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0, "volume": 1.0}, index=idx)

    monkeypatch.setattr(mm, "fetch_ohlcv", fake)
    config = {"timeframe": "4H", "data": {"history_bars": 10, "cache_dir": "x"}}
    mm.fetch_all(config, ["A"], timeframe="1H", cache_dir="c")
    assert seen == [("1H", vault.KASA_START)]

    def leaking(config, symbol, *, now):
        idx = pd.DatetimeIndex([vault.KASA_START], tz="UTC")
        return pd.DataFrame({"open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0, "volume": 1.0}, index=idx)

    monkeypatch.setattr(mm, "fetch_ohlcv", leaking)
    with pytest.raises(vault.VaultError):
        mm.fetch_all(config, ["A"], timeframe="1H", cache_dir="c")


def test_hour_grid_ends_before_vault():
    hours = mm.hour_grid(mm.DATA_START, mm.KASA_START)
    assert hours[-1] + pd.Timedelta(hours=1) == vault.KASA_START


# (3) --------------------------------------------------------------------------
def test_zoo_has_88_unique_bases_in_family_order():
    bases = mm.zoo()
    assert len(bases) == 88
    assert len({b.id for b in bases}) == 88
    counts = pd.Series([b.family for b in bases]).value_counts().to_dict()
    assert counts == {"F1": 10, "F2": 6, "F3": 20, "F4": 8, "F5": 12, "F6": 20, "F7": 12}
    families = [b.family for b in bases]
    assert families == sorted(families)


def test_no_family_looks_ahead(small):
    h1, h4, market = small
    cut = pd.Timestamp("2022-03-20T13:00:00Z")
    rng = np.random.default_rng(3)
    h1b = {}
    for s, f in h1.items():
        g = f.copy()
        later = g.index >= cut
        g.loc[later, ["open", "high", "low", "close"]] *= rng.uniform(0.5, 1.5, (int(later.sum()), 1))
        h1b[s] = g
    h4b = {s: to_4h(f) for s, f in h1b.items()}
    other = mm.build_market(h1b, h4b, SYMBOLS, start=START, end=END)
    upto = market.hours <= cut
    for base in mm.zoo():
        before, after = base.build(market), base.build(other)
        np.testing.assert_array_equal(before[upto], after[upto], err_msg=base.id)


def test_weights_respect_eligibility_and_gross_cap(small):
    _, _, market = small
    for base in mm.zoo():
        w = base.build(market)
        assert np.all(w[~market.eligible] == 0.0), base.id
        assert np.all(np.abs(w).sum(axis=1) <= 1.0 + 1e-12), base.id
    # Uygunluk 60 günden önce başlamaz.
    first = market.hours[np.flatnonzero(market.eligible.any(axis=1))[0]]
    assert first >= START + pd.Timedelta(days=60)


# (4) (5) ----------------------------------------------------------------------
def test_inverse_and_hedge_identities(small):
    _, _, market = small
    m = mm.basket(market)
    for base in mm.zoo()[::7]:
        w = base.build(market)
        hr, inv = mm.hourly(w, market, cost_rate=0.001), mm.hourly(-w, market, cost_rate=0.001)
        np.testing.assert_array_equal(inv.gross, -hr.gross)
        np.testing.assert_array_equal(inv.hedged, -hr.hedged)
        np.testing.assert_array_equal(inv.cost, hr.cost)
        np.testing.assert_allclose(hr.hedged, hr.gross - hr.exposure * m, atol=1e-15)


def test_xsec_ls_is_its_own_hedge(small):
    _, _, market = small
    w = mm.xsec_weights(market, days=3, k=2, long_only=False)
    np.testing.assert_allclose(w.sum(axis=1), 0.0, atol=1e-15)
    np.testing.assert_allclose(mm.hedge(w, market.eligible), w, atol=1e-15)


def test_zoo_mean_is_exactly_zero_every_period():
    h1, h4 = synthetic(SYMBOLS, mm.DATA_START, pd.Timestamp("2022-07-01T00:00:00Z"), seed=5)
    market = mm.build_market(h1, h4, SYMBOLS, end=pd.Timestamp("2022-07-01T00:00:00Z"))
    dedup, sums = mm.build_zoo(market, mm.zoo(), settings=SETTINGS, with_returns=True)
    assert sums is not None and len(sums.ids) == dedup["n"]
    for h in mm.HORIZONS:
        np.testing.assert_allclose(sums.gross[h].mean(axis=0), 0.0, atol=1e-15)
        np.testing.assert_allclose(sums.hedged[h].mean(axis=0), 0.0, atol=1e-15)
        assert np.all(sums.net[h].mean(axis=0) <= 1e-15)   # net bahçe simetrik DEĞİL
    k = sums.ids.index("tsmom_7g_ls")
    assert sums.ids[k + 1] == "tsmom_7g_ls~ters"
    np.testing.assert_array_equal(sums.hedged["week"][k + 1], -sums.hedged["week"][k])


# (6) --------------------------------------------------------------------------
def test_vectorized_bands_match_core_indicators(small):
    h1, _, _ = small
    frame = h1["S0"]
    upper, lower = mm.donchian_bands(frame, 20)
    bu, bm, bl = mm.bollinger_bands(frame["close"], 20, 2.5)
    for t in np.random.default_rng(1).integers(30, len(frame), 25):
        ref = indicators.donchian(frame.iloc[: t + 1], 20)
        assert (upper.iloc[t], lower.iloc[t]) == pytest.approx((ref.upper, ref.lower), rel=0, abs=0)
        band = indicators.bollinger(frame["close"].iloc[: t + 1], 20, 2.5)
        assert (bu.iloc[t], bm.iloc[t], bl.iloc[t]) == pytest.approx((band.upper, band.middle, band.lower), rel=1e-12)


def test_extreme_machine_entry_beats_exit():
    z = np.zeros(5, dtype=bool)
    out = mm.extreme_machine(np.zeros(5), long_entry=np.array([1, 0, 0, 0, 0], bool),
                             short_entry=np.array([0, 0, 1, 0, 0], bool),
                             long_exit=np.array([0, 0, 1, 0, 0], bool), short_exit=np.array([0, 0, 0, 1, 0], bool))
    assert out.tolist() == [1.0, 1.0, -1.0, 0.0, 0.0]
    assert mm.extreme_machine(np.zeros(5), long_entry=z, short_entry=z, long_exit=z, short_exit=z).tolist() == [0.0] * 5


# (7) --------------------------------------------------------------------------
def test_turnover_and_cost_by_hand():
    hours = pd.date_range("2022-01-01", periods=3, freq="h", tz="UTC")
    market = mm.Market(hours=hours, symbols=["A", "B"], h1={}, h4={},
                       eligible=np.ones((3, 2), bool), returns=np.array([[0.01, -0.02], [0.0, 0.0], [0.03, 0.01]]),
                       missing=np.zeros((3, 2), bool))
    w = np.array([[0.5, -0.5], [0.5, 0.0], [0.0, 0.0]])
    hr = mm.hourly(w, market, cost_rate=0.001)
    np.testing.assert_allclose(hr.turnover, [1.0, 0.5, 0.5])
    np.testing.assert_allclose(hr.cost, [0.001, 0.0005, 0.0005])
    np.testing.assert_allclose(hr.gross, [0.015, 0.0, 0.0])
    # v = w − e/|E|: [[0.5,-0.5],[0.25,-0.25],[0,0]] → hedge'li devir birleşik ağırlıktan.
    np.testing.assert_allclose(hr.cost_hedged, [0.001, 0.0005, 0.0005])


# (8) --------------------------------------------------------------------------
def test_dedup_is_base_level():
    rows = [("a", "p1", "n1", False), ("b", "n1", "p1", False), ("c", "p1", "n1", False),
            ("d", "p2", "n2", False), ("z", "p0", "p0", True)]
    out = mm.deduplicate(rows)
    assert out["kept"] == ["a", "d", "z"]
    assert [d["id"] for d in out["dropped"]] == ["b", "c"]
    assert out["dropped"][0]["negated"] is True and out["dropped"][1]["negated"] is False
    assert out["all_zero"] == ["z"] and out["n"] == 5


# (9) --------------------------------------------------------------------------
def test_ranking_ties_break_by_id_and_quintiles():
    ids = [f"s{i}" for i in range(10)]
    prev = np.array([1, 1, 0, 0, 0, 0, 0, 0, -1, -1], dtype=float)
    cur = np.arange(10, dtype=float)
    ic, spread, top, bottom = mm.pair_stats(prev, cur, ids)
    assert top.tolist() == [0, 1] and bottom.tolist() == [8, 9]
    assert spread == pytest.approx(0.5 - 8.5)
    assert ic == pytest.approx(mm.spearman(prev, cur))


def test_bootstrap_is_deterministic_and_paired():
    ic = np.linspace(-0.2, 0.4, 30)
    sp = ic * 2
    a = mm.block_bootstrap(ic, sp, block=2, iterations=300, seed="x", alpha=0.05)
    b = mm.block_bootstrap(ic, sp, block=2, iterations=300, seed="x", alpha=0.05)
    assert a == b and a["clusters"] == 15 and a["evaluable"]
    assert a["spread"]["low"] == pytest.approx(2 * a["ic"]["low"])
    assert mm.block_bootstrap(ic[:18], sp[:18], block=2, iterations=10, seed="x", alpha=0.05)["evaluable"] is False


def test_two_sided_p_and_bh():
    assert mm.two_sided_p([1.0] * 99) == pytest.approx(2 / 100)
    assert mm.two_sided_p([-1.0, 1.0]) == 1.0
    assert mm.bh_accept({"month": 0.04, "week": 0.03}) == {"month", "week"}
    assert mm.bh_accept({"month": 0.06, "week": 0.024}) == {"week"}
    assert mm.bh_accept({"month": 0.06, "week": 0.026}) == set()


def _series(ok: bool, p: float) -> dict:
    return {"conditions_ab": ok, "binding": {"p": p}}


def test_decide_measures_in_a_and_verifies_in_b():
    results = {"A": {"month": _series(True, 0.01), "week": _series(True, 0.2)},
               "B": {"month": _series(True, 0.04), "week": _series(True, 0.001)}}
    out = mm.decide(results)
    assert out["month"]["verdict"] == mm.VERIFIED and out["week"]["verdict"] == mm.NOT_VERIFIED
    assert out["week"]["B"] == "bilgi — doğrulama değil" and out["m_B"] == 1
    assert out["overall"] == mm.VERIFIED
    results["B"]["month"] = _series(True, 0.06)
    assert mm.decide(results)["overall"] == mm.NOT_VERIFIED


def test_binding_takes_conservative_block():
    blocks = {"b1": {"evaluable": True, "ic": {"low": 0.02, "p": 0.01}},
              "b2": {"evaluable": True, "ic": {"low": -0.01, "p": 0.08}}}
    assert mm.binding(blocks, "ic") == (-0.01, 0.08)
    blocks["b2"]["evaluable"] = False
    assert mm.binding(blocks, "ic") == (None, 1.0)


def test_reading_table_gate_is_hedged():
    assert mm.reading(mm.NOT_VERIFIED, mm.VERIFIED) == "seçici değil, piyasa zamanlaması"
    assert mm.reading(mm.VERIFIED, mm.NOT_VERIFIED).startswith("seçim becerisi kalıcı")
    assert len(mm.SENTENCES) == 4


def test_evaluate_end_to_end_small():
    h1, h4 = synthetic(SYMBOLS, mm.DATA_START, pd.Timestamp("2022-09-01T00:00:00Z"), seed=9)
    market = mm.build_market(h1, h4, SYMBOLS, end=pd.Timestamp("2022-09-01T00:00:00Z"))
    _, sums = mm.build_zoo(market, mm.zoo(), settings=SETTINGS, with_returns=True)
    # Kısa pencerede B yoktur; değerlendirme yine de çökmeden etiket üretmeli.
    out = mm.evaluate(sums, settings=SETTINGS)
    assert out["gate"] == "hedged"
    assert out["reading"]["overall"] in mm.SENTENCES.values()
    json.dumps(mm.clean(out), allow_nan=False, default=mm._json)
    assert set(out["families"]) - {"note"} == {"F1", "F2", "F3", "F4", "F5", "F6", "F7"}


# (10) -------------------------------------------------------------------------
def test_preflight_computes_no_returns(small, monkeypatch):
    _, _, market = small

    def boom(*args, **kwargs):
        raise AssertionError("preflight getiri hesapladı")

    monkeypatch.setattr(mm, "hourly", boom)
    monkeypatch.setattr(mm, "period_sums", boom)
    monkeypatch.setattr(mm, "basket", boom)
    dedup, sums = mm.build_zoo(market, mm.zoo(), settings=SETTINGS, with_returns=False)
    assert sums is None and dedup["n"] > 0


def test_clean_turns_nan_into_null():
    assert mm.clean({"a": float("nan"), "b": [np.float64(1.5), np.inf]}) == {"a": None, "b": [1.5, None]}


# (11) -------------------------------------------------------------------------
def test_forbidden_imports():
    tree = ast.parse(SOURCE)
    modules = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    forbidden = {"core.portfolio", "core.engine", "core.ledger", "core.metrics"}
    assert not modules & forbidden
    assert not any(m.startswith("strategies") for m in modules)
    assert "scripts.vault" in modules

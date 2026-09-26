"""`scripts/measure_vol_target.py`: ön-kayda (docs/backtest.md > 6o, TADİLAT-1) MEKANİK sadakat.

Sınanan: (1) sabitler girdi değildir; (2) σ̂/σ_hedef/w İLERİYE BAKMAZ ve tanıma uyar; (3) uygunluk
`C_t`yi görmez, eksik kapanış 0 getiri alır ve sayılır; (4) devir/maliyet aritmetiği elle hesaplanmış
örnekle; (5) F'nin w̄'ı ve Sharpe'ın `account_stats` tanımı; (6) iki blok, deterministik çekiliş,
iki alt sınırın minimumu; (7) mekanizma: ileri pencere, dönem sınırı, örtüşmeyen adım; (8) karar ve
okuma tabloları; (9) import yasağı; (10) uçtan uca sentetik veri — preflight hiçbir getiri üretmez.
"""

from __future__ import annotations

import ast
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from scripts import measure_vol_target as vt

SOURCE = Path("scripts/measure_vol_target.py").read_text(encoding="utf-8")


def days(start: str, n: int) -> pd.DatetimeIndex:
    return pd.date_range(pd.Timestamp(start, tz="UTC"), periods=n, freq="D")


# (1) --------------------------------------------------------------------------
def test_preregistered_constants():
    assert (vt.VOL_DAYS, vt.TARGET_DAYS, vt.WEIGHT_CAP, vt.DAYS_PER_YEAR) == (30, 365, 1.0, 365.0)
    assert (vt.FORWARD_DAYS, vt.BLOCK_WEEKS, vt.BLOCKS) == (30, 4, ("week", "4week"))
    assert vt.DATA_START <= pd.Timestamp("2022-01-01", tz="UTC") - pd.Timedelta(days=396)


def test_period_bounds_are_not_cli_inputs():
    args = vt._parse_args(["--stage", "measure"])
    for name in ("now", "b_end", "a_start", "start", "end", "vol_days", "target_days", "cap"):
        assert not hasattr(args, name)


def test_periods_follow_backtest_ema_and_close_b_on_last_closed_day():
    a, b = vt.periods(pd.Timestamp("2026-09-27T03:00:00Z"))
    assert (str(a.first.date()), str(a.last.date())) == ("2022-01-01", "2024-06-29")
    assert len(a.days()) == 911
    assert str(b.first.date()) == "2024-06-30" and str(b.last.date()) == "2026-09-26"
    assert str(vt.last_closed_day(pd.Timestamp("2026-09-27T00:00:00Z")).date()) == "2026-09-26"


# (2) --------------------------------------------------------------------------
def _returns(n: int = 500, seed: int = 1) -> pd.Series:
    rng = np.random.default_rng(seed)
    vol = np.where(np.arange(n) % 120 < 60, 0.02, 0.05)
    return pd.Series(rng.normal(0, 1, n) * vol, index=days("2021-01-01", n))


def test_sigma_hat_uses_only_previous_30_days():
    r = _returns()
    table = vt.weight_table(r)
    t = 400
    g = np.log1p(r.to_numpy())
    expected = np.std(g[t - 30:t], ddof=1) * math.sqrt(365)
    assert table["sigma_hat"].iloc[t] == pytest.approx(expected)
    target = np.median(table["sigma_hat"].to_numpy()[t - 365:t])   # σ̂_t HARİÇ
    assert table["sigma_target"].iloc[t] == pytest.approx(target)
    assert table["w"].iloc[t] == pytest.approx(min(1.0, target / expected))


def test_weight_does_not_see_day_t_or_later():
    r = _returns()
    base = vt.weight_table(r)
    t = 420
    shocked = r.copy()
    shocked.iloc[t:] = 0.5
    assert base["w"].iloc[: t + 1].equals(vt.weight_table(shocked)["w"].iloc[: t + 1])


def test_weight_is_capped_and_undefined_during_warmup():
    table = vt.weight_table(_returns())
    w = table["w"].dropna()
    assert (w <= 1.0).all() and (w > 0).all()
    assert table["w"].iloc[: 30 + 365].isna().all()
    assert table["w"].iloc[30 + 365:].notna().all()


# (3) --------------------------------------------------------------------------
def test_eligibility_needs_30_prior_closes_and_ignores_today():
    idx = days("2022-01-01", 40)
    closes = pd.DataFrame({"X": 1.0, "Y": 1.0}, index=idx)
    closes.iloc[:5, 1] = np.nan          # Y 5 gün geç listelendi
    closes.iloc[35, 0] = np.nan          # X'in 35. gün kapanışı eksik
    elig = vt.eligibility(closes)
    assert not elig["X"].iloc[29] and elig["X"].iloc[30]
    assert elig["X"].iloc[35]            # C_t'nin eksikliği uygunluğa girmez
    assert not elig["X"].iloc[36]        # ertesi gün 30 günlük pencere delindi
    assert not elig["Y"].iloc[34] and elig["Y"].iloc[35]


def test_missing_close_of_eligible_symbol_earns_zero_and_is_counted():
    idx = days("2022-01-01", 40)
    closes = pd.DataFrame({"X": np.linspace(100, 139, 40), "Y": 50.0}, index=idx)
    closes.iloc[35, 0] = np.nan
    elig = vt.eligibility(closes)
    rets, basket, missing = vt.basket_returns(closes, elig)
    assert rets["X"].iloc[35] == 0.0 and bool(missing["X"].iloc[35])
    assert int(missing.to_numpy().sum()) == 1
    assert basket.iloc[35] == pytest.approx(0.0)
    assert np.isnan(basket.iloc[10])     # E_t boş → tanımsız


# (4) --------------------------------------------------------------------------
def test_single_asset_turnover_and_cost_by_hand():
    path = vt.simulate(np.array([0.5, 1.0]), np.array([[0.1], [-0.2]]), np.array([[True], [True]]),
                       cost_rate=0.001)
    drift = 0.5 * 1.1 / 1.05
    assert path.turnover == pytest.approx([0.5, 1.0 - drift])
    assert path.net == pytest.approx([0.05 - 0.0005, -0.2 - (1.0 - drift) * 0.001])


def test_unscaled_basket_still_pays_rebalancing():
    ret = np.array([[0.1, -0.1], [0.0, 0.0]])
    path = vt.simulate(np.ones(2), ret, np.ones((2, 2), dtype=bool), cost_rate=0.001)
    # gün 0 sonu kayan ağırlıklar 0.55 / 0.45 → gün 1 devri 0.1
    assert path.turnover[1] == pytest.approx(0.1)
    single = vt.simulate(np.ones(2), np.array([[0.1], [0.0]]), np.ones((2, 1), dtype=bool), cost_rate=0.001)
    assert single.turnover[1] == pytest.approx(0.0)


def test_symbol_leaving_the_basket_is_sold():
    elig = np.array([[True, True], [True, False]])
    path = vt.simulate(np.ones(2), np.zeros((2, 2)), elig, cost_rate=0.0)
    assert path.turnover[1] == pytest.approx(1.0)   # Y'nin 0.5'i satılır, X'e 0.5 alınır


# (5) --------------------------------------------------------------------------
def test_account_is_core_metrics_sharpe():
    rng = np.random.default_rng(3)
    r = rng.normal(0.001, 0.02, 300)
    got = vt.account(r)
    assert got["sharpe"] == pytest.approx(r.mean() / r.std(ddof=1) * math.sqrt(365), rel=1e-9)
    assert got["total_return"] == pytest.approx(np.prod(1 + r) - 1)
    assert "account_stats" in SOURCE


def test_constant_weight_sharpe_equals_unscaled_without_cost():
    rng = np.random.default_rng(4)
    ret = rng.normal(0, 0.03, (400, 1))
    elig = np.ones((400, 1), dtype=bool)
    u = vt.simulate(np.ones(400), ret, elig, cost_rate=0.0)
    f = vt.simulate(np.full(400, 0.6), ret, elig, cost_rate=0.0)
    assert vt.account(f.net)["sharpe"] == pytest.approx(vt.account(u.net)["sharpe"], rel=1e-9)


# (6) --------------------------------------------------------------------------
def test_cluster_ids_week_and_four_week():
    idx = days("2024-01-01", 60)                       # Pazartesi
    week = vt.cluster_ids(idx, "week")
    four = vt.cluster_ids(idx, "4week")
    assert week[0] == week[6] != week[7]
    assert four[0] == four[27] != four[28]
    assert len(set(four)) == math.ceil(len(set(week)) / 4)
    with pytest.raises(ValueError):
        vt.cluster_ids(idx, "month")


def test_block_draws_are_deterministic_and_whole_clusters():
    clusters = np.repeat(np.arange(12), 7)
    a = vt.block_draws(clusters, iterations=20, seed="s")
    b = vt.block_draws(clusters, iterations=20, seed="s")
    assert all(np.array_equal(x, y) for x, y in zip(a, b))
    for draw in a:
        assert len(draw) == len(clusters)
        assert all(len(set(clusters[draw[i:i + 7]])) == 1 for i in range(0, len(draw), 7))


def test_binding_low_is_the_minimum_and_needs_both_blocks():
    assert vt.binding_low({"week": {"low": 0.2, "evaluable": True},
                           "4week": {"low": -0.1, "evaluable": True}}) == -0.1
    assert vt.binding_low({"week": {"low": 0.2, "evaluable": True},
                           "4week": {"low": 0.3, "evaluable": False}}) is None
    assert vt.binding_low({"week": {"low": None, "evaluable": True},
                           "4week": {"low": 0.3, "evaluable": True}}) is None


# (7) --------------------------------------------------------------------------
def test_forward_vol_includes_t_and_next_29():
    r = _returns(200)
    fwd = vt.forward_vol(r)
    g = np.log1p(r.to_numpy())
    assert fwd.iloc[50] == pytest.approx(np.std(g[50:80], ddof=1) * math.sqrt(365))
    assert fwd.iloc[-30:].isna().sum() == 29


def test_mechanism_target_window_does_not_leave_the_period():
    r = _returns(900)
    table = vt.weight_table(r)
    period = vt.Period("A", r.index[500], r.index[700])
    settings = vt.Settings(iterations=20, alpha=0.05, seed=1, cost_rate=0.0)
    block = vt.measure_mechanism(asset="btc", period=period, single_returns=r, weights=table, settings=settings)
    assert block["dropped_tail_days"] == 29
    assert block["days_used"] == 201 - 29
    # dönem dışındaki veri bozulsa sonuç değişmez
    shocked = r.copy()
    shocked.iloc[701:] = 0.3
    again = vt.measure_mechanism(asset="btc", period=period, single_returns=shocked, weights=table,
                                 settings=settings)
    assert again["rho"] == block["rho"]


def test_non_overlapping_step_is_thirty_calendar_days():
    idx = days("2022-01-01", 100)
    picked = vt.non_overlapping_days(idx)
    assert list(picked) == [idx[0], idx[30], idx[60], idx[90]]


# (8) --------------------------------------------------------------------------
def test_asset_verdict_needs_both_periods():
    assert vt.asset_verdict({"A": {"verdict": vt.PASSED}, "B": {"verdict": vt.PASSED}}) == vt.PASSED
    assert vt.asset_verdict({"A": {"verdict": vt.PASSED}, "B": {"verdict": vt.FAILED}}) == vt.FAILED
    assert vt.asset_verdict({"A": {"verdict": vt.PASSED}, "B": {"measured": False}}) == vt.NOT_EVALUABLE


def _mech(rho, low, nonover):
    return {"verdict_inputs_ok": True, "rho": rho, "binding_low": low, "non_overlapping": {"rho": nonover}}


def test_premise_verdict_rule():
    good = _mech(0.5, 0.2, 0.4)
    assert vt.premise_verdict({"A": good, "B": good}) == vt.PREMISE_HELD
    assert vt.premise_verdict({"A": good, "B": _mech(0.5, -0.01, 0.4)}) == vt.PREMISE_FAILED
    assert vt.premise_verdict({"A": good, "B": _mech(0.5, 0.2, -0.1)}) == vt.PREMISE_FAILED
    assert vt.premise_verdict({"A": good, "B": {"verdict_inputs_ok": False}}) == vt.NOT_EVALUABLE


def test_reading_table_matches_tadilat_1():
    assert vt.reading(vt.FAILED, vt.PREMISE_HELD) == "tez doğrulanamadı, dayanağı sağlam"
    assert vt.reading(vt.FAILED, vt.PREMISE_FAILED) == "tez dayanaksız"
    assert vt.reading(vt.PASSED, vt.PREMISE_HELD) == "doğrulandı"
    assert "mekanizmadan gelmiyor" in vt.reading(vt.PASSED, vt.PREMISE_FAILED)
    assert "okunamaz" in vt.reading(vt.NOT_EVALUABLE, vt.PREMISE_HELD)


# (9) --------------------------------------------------------------------------
def test_does_not_import_strategies_or_writers():
    banned = ("strategies", "core.portfolio", "core.engine", "core.ledger")
    for node in ast.walk(ast.parse(SOURCE)):
        names = []
        if isinstance(node, ast.ImportFrom) and node.module:
            names = [node.module]
        elif isinstance(node, ast.Import):
            names = [a.name for a in node.names]
        for name in names:
            assert not any(name == b or name.startswith(b + ".") for b in banned), name


# (10) -------------------------------------------------------------------------
NOW = pd.Timestamp("2026-09-27T03:00:00Z")


def _bars(symbol_index: int, start: pd.Timestamp) -> pd.DataFrame:
    idx = pd.date_range(start, NOW - pd.Timedelta(hours=4), freq="4h", tz="UTC")
    rng = np.random.default_rng(100 + symbol_index)
    regime = np.where((np.arange(len(idx)) // 400) % 2 == 0, 0.006, 0.02)
    close = 100 * np.exp(np.cumsum(rng.normal(0, 1, len(idx)) * regime))
    return pd.DataFrame({"open": close, "high": close, "low": close, "close": close, "volume": 1.0}, index=idx)


@pytest.fixture
def synthetic(monkeypatch):
    def fake_fetch(config, symbols, *, now, cache_dir):
        out = {}
        for i, s in enumerate(symbols):
            start = vt.DATA_START if i < 8 else pd.Timestamp("2023-06-01", tz="UTC")
            out[s] = _bars(i, start)
        return out

    real_settings = vt.settings_from
    monkeypatch.setattr(vt, "fetch_all", fake_fetch)
    monkeypatch.setattr(vt, "_now", lambda: NOW)
    monkeypatch.setattr(vt, "settings_from",
                        lambda cfg: vt.Settings(**{**real_settings(cfg).__dict__, "iterations": 40}))


def test_preflight_produces_no_returns_or_weights(synthetic, tmp_path, capsys):
    assert vt.main(["--stage", "preflight", "--out-dir", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    body = out.split("=== PREFLIGHT BEGIN ===")[1].split("=== PREFLIGHT END ===")[0]
    report = json.loads(body)
    assert report["btc_reaches_data_start"]
    text = json.dumps(report)
    for word in ("sharpe", "sigma", "\"w\"", "return", "weight", "drawdown", "rho"):
        assert word not in text
    assert not any(tmp_path.iterdir())


def test_end_to_end_measure(synthetic, tmp_path):
    assert vt.main(["--stage", "measure", "--out-dir", str(tmp_path)]) == 0
    payload = json.loads((tmp_path / "vol_target.json").read_text(encoding="utf-8"))
    assert payload["periods"]["B"][1] == "2026-09-26"
    for asset in vt.ASSETS:
        block = payload["assets"][asset]
        assert block["verdict"] in (vt.PASSED, vt.FAILED)
        assert block["premise"] in (vt.PREMISE_HELD, vt.PREMISE_FAILED)
        for period in ("A", "B"):
            p = block["periods"][period]
            assert p["measured"] and p["undefined_days"] == 0
            assert p["weights"]["w_bar"] <= 1.0
            assert p["binding_low"] == min(p["intervals"][b]["d_sharpe"]["low"] for b in vt.BLOCKS)
            assert block["mechanism"][period]["dropped_tail_days"] == 29
    # geç listelenen semboller sepete 30 gün sonra girer
    table = pd.read_csv(tmp_path / "vol_target_days.csv", index_col="day")
    table.index = table.index.str[:10]
    # ilk kapanış 2023-06-01 → 30 kapanış 06-30'da tamam → uygunluk 07-01
    assert table.loc["2023-06-30", "basket_n"] == 8
    assert table.loc["2023-07-01", "basket_n"] == 13
    assert (tmp_path / "vol_target_closes.csv").is_file()

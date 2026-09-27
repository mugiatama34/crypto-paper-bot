"""`scripts/measure_texture_regime.py`: ön-kayda (docs/backtest.md > 6r, TADİLAT-1) MEKANİK sadakat.

Sınanan: (1) sabitler, eşleme ve dönemler girdi değildir, hafta/kaydırma sayıları ön-kayıttaki gibidir;
(2) kasa: hiçbir bar kasaya taşmaz; (3) doku İLERİYE BAKMAZ, eşik gün `d`'yi dışlar, ER ve kesitsel std
elle hesaplanan değere eşittir; (4) dairesel kaydırma sıklığı birebir, geçiş sayısını ±1 içinde korur,
kaydırma kümesi `{30 … D−30}`; (5) kollar 59 tabandır, hiçbir taban ileriye bakmaz; (6) gün × rejim
muhasebesi, etiketlerden DOĞRUDAN kurulan portföyle birebir (ham ve hedge'li, maliyet ve dönem girişi
dâhil), hedge özdeşliği; (7) p, %95, BH, karar ve okuma tablosu; (8) preflight hiçbir getiri/doku/etiket
üretmez; (9) import yasağı ve §6q matrisinin okunmaması; (10) uçtan uca küçük koşu.
"""

from __future__ import annotations

import ast
import random
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from scripts import measure_texture_regime as tr
from scripts import vault

SOURCE = Path("scripts/measure_texture_regime.py").read_text(encoding="utf-8")
SYMBOLS = [f"S{i}" for i in range(7)]
START = pd.Timestamp("2022-01-01T00:00:00Z")
END = pd.Timestamp("2022-07-04T00:00:00Z")


def synthetic(symbols, start, end, *, seed=0):
    """15m rastgele yürüyüş ve ondan 4H (kapanış = son 15m kapanışı, tutarlılık birebir)."""
    rng = np.random.default_rng(seed)
    idx = pd.date_range(start, end - pd.Timedelta(minutes=15), freq="15min", tz="UTC")
    m15, h4 = {}, {}
    for k, s in enumerate(symbols):
        drift = 0.00004 * (k - 3)
        close = 100.0 * np.exp((rng.normal(drift, 0.003, len(idx))).cumsum())
        opens = np.r_[100.0, close[:-1]]
        frame = pd.DataFrame({"open": opens, "high": np.maximum(opens, close) * 1.0005,
                              "low": np.minimum(opens, close) * 0.9995, "close": close, "volume": 1.0}, index=idx)
        m15[s] = frame
        h4[s] = frame.resample("4h", label="left", closed="left").agg(
            {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}).dropna()
    return m15, h4


@pytest.fixture()
def small(monkeypatch):
    """Kısa pencere: sabitler yalnızca testte küçültülür (dönem uzunluğu, eşik penceresi, yaş, kaydırma)."""
    monkeypatch.setattr(tr, "DATA_START_4H", START)
    monkeypatch.setattr(tr, "DATA_START_15M", START)
    monkeypatch.setattr(tr, "DATA_END", END)
    monkeypatch.setattr(tr, "ELIGIBLE_AGE", pd.Timedelta(days=10))
    monkeypatch.setattr(tr, "THRESHOLD_DAYS", 40)
    monkeypatch.setattr(tr, "THRESHOLD_MIN_DEFINED", 30)
    monkeypatch.setattr(tr, "MIN_SHIFT_DAYS", 7)
    monkeypatch.setattr(tr, "BLOCK_PERMUTATIONS", 50)
    weeks = {"A": (pd.Timestamp("2022-03-07T00:00Z"), pd.Timestamp("2022-05-02T00:00Z")),
             "B": (pd.Timestamp("2022-05-02T00:00Z"), pd.Timestamp("2022-06-27T00:00Z"))}
    monkeypatch.setattr(tr, "measurement_weeks", lambda: weeks)
    m15, h4 = synthetic(SYMBOLS, START, END)
    first = {s: h4[s].index[0] for s in SYMBOLS}
    return tr.Data(symbols=list(SYMBOLS), h4=h4, m15=m15, first_4h=first, report={})


SETTINGS = tr.Settings(seed=7, cost_rate=0.00105)


# (1) --------------------------------------------------------------------------
def test_preregistered_constants():
    assert (tr.ER_DAYS, tr.DISPERSION_DAYS, tr.THRESHOLD_DAYS) == (14, 7, 365)
    assert tr.THRESHOLD_MIN_DEFINED == 300 and tr.MIN_SYMBOLS == 5
    assert tr.MIN_SHIFT_DAYS == 30 and tr.BLOCK_PERMUTATIONS == 1000 and tr.BH_Q == 0.05
    assert tr.ELIGIBLE_AGE == pd.Timedelta(days=60)
    assert tr.DATA_START_4H == pd.Timestamp("2020-11-01T00:00Z")
    assert tr.DATA_START_15M == pd.Timestamp("2021-10-01T00:00Z")
    assert list(tr.ARMS) == ["1D", "4H", "15m"]


def test_mapping_is_the_users_hypothesis():
    mapping = {tr.REGIMES[r]: {f for f in tr.FAMILIES if tr.OPEN[r, tr.FAMILIES.index(f)]} for r in range(4)}
    assert mapping == {"YV-YD": {"F1", "F2", "F3", "F5", "F6"}, "YV-DD": {"F1", "F2", "F3", "F5"},
                       "DV-YD": {"F4", "F7", "F6"}, "DV-DD": {"F4", "F7"}}


def test_weeks_and_shifts_match_preregistration():
    weeks = tr.measurement_weeks()
    assert weeks["A"] == (pd.Timestamp("2022-01-03T00:00Z"), pd.Timestamp("2024-06-24T00:00Z"))
    assert weeks["B"] == (pd.Timestamp("2024-07-01T00:00Z"), pd.Timestamp("2026-09-21T00:00Z"))
    assert len(tr.period_days("A")) == 903 and len(tr.period_days("B")) == 812
    assert len(tr.admissible_shifts(903)) == 844 and len(tr.admissible_shifts(812)) == 753
    assert all(d.dayofweek == 0 for d in (weeks["A"][0], weeks["B"][0]))


def test_period_bounds_are_not_cli_inputs():
    args = tr._parse_args(["--stage", "preflight"])
    assert set(vars(args)) == {"stage", "config", "cache_dir", "out_dir"}


# (2) --------------------------------------------------------------------------
def test_grid_ends_before_vault_and_fetch_uses_vault_now(monkeypatch):
    assert tr.DATA_END is vault.KASA_START
    with pytest.raises(vault.VaultError):
        tr.build_arm("4H", {}, {}, [], start=START, end=vault.KASA_START + pd.Timedelta(hours=4))
    seen = []

    def fake(config, symbol, *, now):
        seen.append((config["timeframe"], now))
        idx = pd.date_range(now - pd.Timedelta(hours=1), periods=4, freq="15min", tz="UTC")
        return pd.DataFrame({"open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0, "volume": 1.0}, index=idx)

    monkeypatch.setattr(tr, "fetch_ohlcv", fake)
    config = {"timeframe": "4H", "data": {"history_bars": 10, "cache_dir": "x"}}
    tr.fetch_frames(config, ["A"], timeframe="15m", start=tr.DATA_START_15M, cache_dir="c")
    assert seen == [("15m", vault.KASA_START)]

    def leaking(config, symbol, *, now):
        idx = pd.DatetimeIndex([vault.KASA_START], tz="UTC")
        return pd.DataFrame({"open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0, "volume": 1.0}, index=idx)

    monkeypatch.setattr(tr, "fetch_ohlcv", leaking)
    with pytest.raises(vault.VaultError):
        tr.fetch_frames(config, ["A"], timeframe="15m", start=tr.DATA_START_15M, cache_dir="c")


# (3) --------------------------------------------------------------------------
def _daily(seed=1, days=500, width=7):
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2021-01-01", periods=days, freq="D", tz="UTC")
    data = 100.0 * np.exp(rng.normal(0, 0.03, (days, width)).cumsum(axis=0))
    return pd.DataFrame(data, index=idx, columns=[f"S{i}" for i in range(width)])


def test_texture_by_hand():
    daily = _daily()
    first = {s: daily.index[0] - pd.Timedelta(days=100) for s in daily.columns}
    tex = tr.texture(daily, first)
    d = pd.Timestamp("2022-03-01T00:00Z")
    c = daily.loc[:d - pd.Timedelta(days=1)]
    er = [abs(c[s].iloc[-1] - c[s].iloc[-15]) / c[s].diff().abs().iloc[-14:].sum() for s in daily.columns]
    lr = [np.log(c[s].iloc[-1] / c[s].iloc[-8]) for s in daily.columns]
    assert tex.loc[d, "V"] == pytest.approx(np.median(er), rel=1e-12)
    assert tex.loc[d, "D"] == pytest.approx(np.std(lr, ddof=1), rel=1e-12)
    window = tex["V"].loc[d - pd.Timedelta(days=365):d - pd.Timedelta(days=1)]
    assert tex.loc[d, "thr_V"] == pytest.approx(window.median(), rel=1e-12)   # gün d HARİÇ
    high_v = tex.loc[d, "V"] > tex.loc[d, "thr_V"]
    high_d = tex.loc[d, "D"] > tex.loc[d, "thr_D"]
    assert tex.loc[d, "label"] == 2 * int(high_v) + int(high_d)


def test_texture_does_not_look_ahead():
    daily = _daily()
    first = {s: daily.index[0] - pd.Timedelta(days=100) for s in daily.columns}
    cut = pd.Timestamp("2022-02-10T00:00Z")               # C_x, x ≥ cut bozulur
    other = daily.copy()
    other.loc[other.index >= cut] *= np.random.default_rng(9).uniform(0.5, 1.5, other.loc[other.index >= cut].shape)
    a, b = tr.texture(daily, first), tr.texture(other, first)
    upto = a.index <= cut                                   # gün d ≤ cut yalnızca C_{≤ d−1} okur
    pd.testing.assert_frame_equal(a.loc[upto], b.loc[upto])
    assert not a.loc[a.index > cut, "V"].equals(b.loc[b.index > cut, "V"])


def test_texture_needs_five_symbols_and_threshold_history():
    daily = _daily(days=420, width=6)
    first = {s: daily.index[0] - pd.Timedelta(days=100) for s in daily.columns}
    first["S5"] = first["S4"] = daily.index[-1]            # hiç uygun değil → 4 sembol
    tex = tr.texture(daily, first)
    assert tex["V"].isna().all() and (tex["label"] == tr.UNDEFINED).all()
    daily = _daily(days=420)
    first = {s: daily.index[0] - pd.Timedelta(days=100) for s in daily.columns}
    tex = tr.texture(daily, first)
    early = tex.index < daily.index[0] + pd.Timedelta(days=15 + 300)
    assert (tex.loc[early, "label"] == tr.UNDEFINED).all()


def test_preflight_counts_agree_with_texture_definedness():
    daily = _daily(days=460)
    first = {s: daily.index[0] + pd.Timedelta(days=30 * i) for i, s in enumerate(daily.columns)}
    counts = tr.texture_counts(daily, first)
    tex = tr.texture(daily, first)
    assert (counts["n_v"].to_numpy() == tex["n_v"].to_numpy()).all()
    assert (counts["n_d"].to_numpy() == tex["n_d"].to_numpy()).all()
    predicted = counts["defined_v"] & counts["defined_d"] & counts["thr_v_defined"] & counts["thr_d_defined"]
    assert (predicted.to_numpy() == (tex["label"] != tr.UNDEFINED).to_numpy()).all()


# (4) --------------------------------------------------------------------------
def test_circular_shift_preserves_frequency_and_transitions():
    rng = np.random.default_rng(4)
    labels = np.repeat(rng.integers(0, 4, 60), rng.integers(3, 20, 60))[:903]
    assert np.array_equal(tr.circular_shift(labels, 0), labels)
    for k in tr.admissible_shifts(len(labels)):
        shifted = tr.circular_shift(labels, k)
        assert np.array_equal(np.bincount(shifted, minlength=4), np.bincount(labels, minlength=4))
        assert abs(tr.transitions(shifted) - tr.transitions(labels)) <= 1
        assert shifted[k] == labels[0]
    shifts = tr.admissible_shifts(100)
    assert shifts[0] == 30 and shifts[-1] == 70


def test_block_permutation_keeps_weeks_and_is_deterministic():
    labels = np.arange(70) % 4
    a = tr.block_permutation(labels, random.Random("s"))
    b = tr.block_permutation(labels, random.Random("s"))
    assert np.array_equal(a, b)
    assert sorted(map(tuple, a.reshape(-1, 7))) == sorted(map(tuple, labels.reshape(-1, 7)))


# (5) --------------------------------------------------------------------------
def test_59_bases_in_family_order():
    base_list = tr.bases()
    ids = [b.id for b in base_list]
    assert len(ids) == len(set(ids)) == 59
    counts = {f: sum(b.family == f for b in base_list) for f in tr.FAMILIES}
    assert counts == {"F1": 10, "F2": 3, "F3": 10, "F4": 4, "F5": 6, "F6": 20, "F7": 6}
    assert not any("ters" in i for i in ids)


def test_daily_bar_from_4h():
    _, h4 = synthetic(["X"], START, pd.Timestamp("2022-01-05T00:00Z"))
    day = tr.daily_ohlc(h4["X"])
    first = h4["X"].loc["2022-01-02"]
    row = day.loc[pd.Timestamp("2022-01-02T00:00Z")]
    assert row["open"] == first["open"].iloc[0] and row["close"] == first["close"].iloc[-1]
    assert row["high"] == first["high"].max() and row["low"] == first["low"].min()


@pytest.mark.parametrize("arm_name", ["1D", "4H"])
def test_no_base_looks_ahead(small, arm_name):
    data = small
    frames, start = tr.arm_frames(data, arm_name)
    arm = tr.build_arm(arm_name, frames, data.first_4h, data.symbols, start=start)
    cut = pd.Timestamp("2022-04-12T00:00Z")
    rng = np.random.default_rng(3)
    m15b = {}
    for s, f in data.m15.items():
        g = f.copy()
        later = g.index >= cut
        g.loc[later, ["open", "high", "low", "close"]] *= rng.uniform(0.5, 1.5, (int(later.sum()), 1))
        m15b[s] = g
    h4b = {s: f.resample("4h", label="left", closed="left").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}).dropna()
        for s, f in m15b.items()}
    other_data = tr.Data(symbols=data.symbols, h4=h4b, m15=m15b, first_4h=data.first_4h, report={})
    frames_b, _ = tr.arm_frames(other_data, arm_name)
    other = tr.build_arm(arm_name, frames_b, data.first_4h, data.symbols, start=start)
    upto = arm.grid <= cut
    for base in tr.bases():
        np.testing.assert_array_equal(base.build(arm)[upto], base.build(other)[upto], err_msg=base.id)


# (6) --------------------------------------------------------------------------
def _direct(fw, ret, elig, day_of_bar, labels, *, hedged, cost_rate):
    """Portföyü etiketlerden DOĞRUDAN kur (ön hesap yok) — muhasebenin bağımsız karşılığı."""
    bar_labels = np.r_[labels[day_of_bar[0]], labels[day_of_bar]]     # dönem girişi: öncesi = ilk gün
    w = np.zeros_like(fw["F1"])
    for b, lab in enumerate(bar_labels):
        for f in tr.FAMILIES:
            if tr.is_open(f, int(lab)):
                w[b] += fw[f][b] / 7.0
    if hedged:
        w = tr.hedge(w, elig)
    gross = float((w[1:] * ret[1:]).sum())
    turn = float(np.abs(w[1:] - w[:-1]).sum())
    return gross - cost_rate * turn, gross


@pytest.mark.parametrize("arm_name", ["1D", "4H", "15m"])
def test_ledger_matches_direct_portfolio(small, arm_name):
    data = small
    frames, start = tr.arm_frames(data, arm_name)
    arm = tr.build_arm(arm_name, frames, data.first_4h, data.symbols, start=start)
    rows, day_of_bar, n_days = tr.period_rows(arm, "A")
    fw = tr.family_weights(arm, tr.bases(), rows)
    elig, ret = arm.eligible[rows], arm.returns[rows]
    rng = np.random.default_rng(11)
    labels = np.repeat(rng.integers(0, 4, n_days), 1)[:n_days]
    for hedged in (False, True):
        ledger = tr.build_ledger(fw, ret, elig, day_of_bar, n_days, hedged=hedged)
        got = ledger.evaluate(labels, 0.00105)
        net, gross = _direct(fw, ret, elig, day_of_bar, labels, hedged=hedged, cost_rate=0.00105)
        assert got["net"] == pytest.approx(net, rel=1e-10, abs=1e-12)
        assert got["gross"] == pytest.approx(gross, rel=1e-10, abs=1e-12)
        # Her zaman açık portföy
        always = sum(fw[f] for f in tr.FAMILIES) / 7.0
        always = tr.hedge(always, elig) if hedged else always
        assert ledger.always_gross == pytest.approx(float((always[1:] * ret[1:]).sum()), rel=1e-10, abs=1e-12)


def test_hedge_nets_exposure_to_zero(small):
    data = small
    frames, start = tr.arm_frames(data, "4H")
    arm = tr.build_arm("4H", frames, data.first_4h, data.symbols, start=start)
    rows, _, _ = tr.period_rows(arm, "A")
    fw = tr.family_weights(arm, tr.bases(), rows)
    w = sum(fw[f] for f in ("F1", "F3", "F5")) / 7.0
    v = tr.hedge(w, arm.eligible[rows])
    assert np.allclose(v.sum(axis=1), 0.0, atol=1e-14)
    assert np.all(v[~arm.eligible[rows]] == 0.0)


def test_closed_family_is_cash_and_switch_costs(small):
    data = small
    frames, start = tr.arm_frames(data, "1D")
    arm = tr.build_arm("1D", frames, data.first_4h, data.symbols, start=start)
    rows, day_of_bar, n_days = tr.period_rows(arm, "A")
    fw = tr.family_weights(arm, tr.bases(), rows)
    ledger = tr.build_ledger(fw, arm.returns[rows], arm.eligible[rows], day_of_bar, n_days, hedged=False)
    flat = np.full(n_days, 2)
    switched = flat.copy()
    switched[n_days // 2:] = 0
    a, b = ledger.evaluate(flat, 0.001), ledger.evaluate(switched, 0.001)
    assert a["switch_turnover"] == 0.0 and b["switch_turnover"] > 0.0


# (7) --------------------------------------------------------------------------
def test_p_values_by_hand():
    placebo = [0.1, 0.2, 0.3, 0.4]
    assert tr.p_upper(0.25, placebo) == pytest.approx(3 / 5)
    assert tr.p_upper(0.5, placebo) == pytest.approx(1 / 5)
    assert tr.p_lower(0.25, placebo) == pytest.approx(3 / 5)


def _test(p, above):
    return {"circular": {"p": p, "above_q95": above}}


def test_decide_bh_three_arms_and_b_confirmation():
    ev = {a: {"A": True, "B": True} for a in tr.ARMS}
    tests = {"1D": {"A": _test(0.01, True), "B": _test(0.02, True)},
             "4H": {"A": _test(0.03, True), "B": _test(0.9, False)},
             "15m": {"A": _test(0.04, True), "B": _test(0.01, True)}}
    out = tr.decide(tests, ev)
    # BH m=3: eşikler 0.0167/0.033/0.05 → 0.01, 0.03, 0.04 hepsi geçer (adım-yukarı).
    assert (out["1D"]["A"], out["4H"]["A"], out["15m"]["A"]) == (tr.PASSED,) * 3
    assert out["m_B"] == 3
    assert out["1D"]["verdict"] == tr.VERIFIED and out["4H"]["verdict"] == tr.NOT_VERIFIED
    tests["1D"]["A"] = _test(0.01, False)                 # %95 diliminin altında → BH yetmez
    assert tr.decide(tests, ev)["1D"]["A"] == tr.FAILED
    ev["15m"]["A"] = False
    assert tr.decide(tests, ev)["15m"]["A"] == tr.NOT_EVALUABLE


def test_reading_table():
    v, n = tr.VERIFIED, tr.NOT_VERIFIED
    assert tr.reading(v, n, True).endswith("her zaman açıktan iyi")
    assert tr.reading(v, v, False).endswith("yine de kaybettiriyor")
    assert tr.reading(n, v, True).startswith("doku yönü öngörüyor")
    assert tr.reading(n, n, True) == "eşleme mekanik olarak iyi, rejim bir şey bilmiyor"
    assert tr.reading(n, n, False) == "ayırt edilemedi"


# (8) --------------------------------------------------------------------------
def test_preflight_produces_no_returns_texture_or_labels(small, monkeypatch):
    def boom(*args, **kwargs):
        raise AssertionError("preflight bir sonuç büyüklüğü hesapladı")

    for name in ("texture", "build_ledger", "placebo_test", "family_weights", "family_by_regime"):
        monkeypatch.setattr(tr, name, boom)
    report = tr.preflight(small)
    text = repr(report)
    for forbidden in ("'V'", "'D'", "thr_V", "label", "share", "gross", "delta", "YV-", "DV-"):
        assert forbidden not in text, forbidden
    assert report["bases_per_arm"] == 59
    assert set(report["arms"]) == set(tr.ARMS)


# (9) --------------------------------------------------------------------------
def test_forbidden_imports_and_no_6q_matrix():
    tree = ast.parse(SOURCE)
    modules = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    assert not modules & {"core.portfolio", "core.engine", "core.ledger", "core.metrics"}
    assert not any(m.startswith("strategies") for m in modules)
    assert "scripts.vault" in modules and "from scripts import zoo_families" in SOURCE
    # §6q'nun strateji × dönem matrisi hiçbir KOD dizgesinde geçmez (docstring'deki yasak beyanı hariç).
    docstring = ast.get_docstring(tree, clean=False)
    literals = [n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str)
                and n.value != docstring]
    assert not any("model_momentum" in text for text in literals)


# (10) -------------------------------------------------------------------------
def test_measure_end_to_end_small(small):
    result, days, placebo = tr.measure(small, SETTINGS)
    assert set(result["decision"]["hedged"]) >= set(tr.ARMS)
    assert result["gate"].startswith("hedged")
    for arm in tr.ARMS:
        for period in ("A", "B"):
            entry = result["arms"][arm]["periods"][period]
            assert entry["hedged"]["circular"]["n"] == len(tr.admissible_shifts(56))
            assert entry["hedged"]["block"]["n"] == 50
            assert 0.0 < entry["hedged"]["circular"]["p"] <= 1.0
        assert result["reading"][arm]
    assert len(placebo) == 3 * 2 * 2 * (len(tr.admissible_shifts(56)) + 50)
    assert {"V", "D", "thr_V", "thr_D", "label", "regime"} <= set(days.columns)
    # Aynı girdi → aynı sonuç (tohum sabit, kaydırmalar sayılır).
    again, _, _ = tr.measure(small, SETTINGS)
    assert again["decision"] == result["decision"]
    assert again["arms"]["4H"]["periods"]["A"]["hedged"]["block"] == result["arms"]["4H"]["periods"]["A"]["hedged"]["block"]


def test_undefined_label_is_a_data_gate(small, monkeypatch):
    monkeypatch.setattr(tr, "ELIGIBLE_AGE", pd.Timedelta(days=60))       # V ancak Mart'ta başlar → A'da eşik yok
    with pytest.raises(tr.DataGateError):
        tr.measure(small, SETTINGS)

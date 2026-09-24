"""`scripts/measure_regime.py`: ön-kayda (docs/backtest.md > 6l, TADİLAT-1) MEKANİK sadakat.

Sınanan: (1) parametreler sabittir ve girdi değildir; (2) rejim tanımı (20:00 barı, pencereler
d dâhil, eşitlik aşağı/düşük, eksik gün tanımsızlaştırır); (3) atama İLERİYE BAKMAZ; (4) p ve
BH; (5) iki koşul birlikte; (6) etiket kuralı + TADİLAT-1'in iki kuralı; (7) DiD çekilişleri;
(8) kapılar; (9) `backtest_dc` çekiliş ayrıştırması aralığı DEĞİŞTİRMEDİ; (10) uçtan uca
sentetik defterle A → B akışı.
"""

from __future__ import annotations

import ast
import random
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from core.ledger import TRADE_COLUMNS, Ledger
from scripts import measure_regime as mr
from scripts.backtest_dc import (
    _percentiles,
    cluster_diff_ci,
    cluster_diff_draws,
    cluster_mean_ci,
    cluster_mean_draws,
)

SOURCE = Path("scripts/measure_regime.py").read_text(encoding="utf-8")
UTC = "UTC"


# --------------------------------------------------------------------------- #
# (1) Parametreler
# --------------------------------------------------------------------------- #
def test_preregistered_constants():
    assert (mr.SMA_DAYS, mr.VOL_DAYS, mr.VOL_MEDIAN_DAYS) == (200, 30, 365)
    assert mr.BH_Q == 0.05
    assert mr.FAMILY_A == ("H1a", "H1b", "H2")


def test_parameters_are_not_cli_inputs():
    args = mr._parse_args(["--stage", "measure", "--ema-dir", "a", "--dc-dir", "b", "--xsec-dir", "c"])
    for name in ("sma_days", "vol_days", "vol_median_days", "q", "bh_q", "b_end", "now", "start"):
        assert not hasattr(args, name)


def test_units_match_preregistration():
    units = {u.key: u for u in mr.UNITS}
    assert units["H1a"].favoured == mr.UP and units["H1a"].primary == "ema-singles"
    assert units["H1a"].control_broken
    assert units["H1b"].primary == "xsec-portfolio" and units["H1b"].did_source == "xsec-portfolio"
    assert units["H2"].favoured == mr.DOWN and units["H2"].primary == "dc-singles"
    # TADİLAT-1 > 2: dc'nin DiD'i portföy ↔ portföy (kontrolün tek-sembollü koşusu yok).
    assert units["H2"].did_source == "dc-portfolio"
    assert units["H3a"].favoured == mr.LOW and units["H3b"].arm == "rsi2_reversal"


# --------------------------------------------------------------------------- #
# (2) Rejim tanımı
# --------------------------------------------------------------------------- #
def _bars(days: int, closes_20: list[float] | None = None, start: str = "2021-01-01") -> pd.DataFrame:
    index = pd.date_range(start, periods=days * 6, freq="4h", tz=UTC)
    close = np.arange(len(index), dtype="float64") + 100.0
    frame = pd.DataFrame({"open": close, "high": close, "low": close, "close": close, "volume": 1.0},
                         index=index)
    if closes_20 is not None:
        mask = frame.index.hour == 20
        frame.loc[mask, "close"] = closes_20
    return frame


def test_daily_close_is_the_2000_bar():
    frame = _bars(3)
    closes = mr.daily_closes(frame)
    assert list(closes.index) == list(pd.date_range("2021-01-01", periods=3, freq="D", tz=UTC))
    expected = frame[frame.index.hour == 20]["close"].to_list()
    assert closes.to_list() == expected


def test_missing_day_is_nan_not_skipped():
    frame = _bars(5)
    frame = frame.drop(pd.Timestamp("2021-01-03T20:00Z"))
    closes = mr.daily_closes(frame)
    assert len(closes) == 5 and np.isnan(closes.loc[pd.Timestamp("2021-01-03", tz=UTC)])


def test_regime_windows_include_the_day_and_ties_go_down_low():
    rng = np.random.default_rng(0)
    values = 100 * np.exp(np.cumsum(rng.normal(0, 0.02, 700)))
    closes = pd.Series(values, index=pd.date_range("2020-01-01", periods=700, freq="D", tz=UTC))
    table = mr.regime_table(closes)
    d = closes.index[650]
    sma = closes.iloc[451:651].mean()
    assert table.loc[d, "sma200"] == pytest.approx(sma)
    logret = np.log(closes / closes.shift(1))
    vol = logret.iloc[621:651].std(ddof=1)
    assert table.loc[d, "vol30"] == pytest.approx(vol)
    vols = logret.rolling(30).std(ddof=1)
    assert table.loc[d, "vol_median365"] == pytest.approx(vols.iloc[286:651].median())
    assert table.loc[d, "direction"] == (mr.UP if closes.iloc[650] > sma else mr.DOWN)
    # Tanımsız başlangıç: yön 200. günde, oynaklık 395. günde (30 getiri + 365 σ) başlar — §6l > 4.
    assert not isinstance(table["direction"].iloc[198], str) and isinstance(table["direction"].iloc[199], str)
    assert not isinstance(table["vol"].iloc[393], str) and isinstance(table["vol"].iloc[394], str)

    flat = pd.Series(100.0, index=pd.date_range("2020-01-01", periods=400, freq="D", tz=UTC))
    tied = mr.regime_table(flat)
    assert tied["direction"].iloc[-1] == mr.DOWN     # close == SMA → aşağı
    assert tied["vol"].iloc[-1] == mr.LOW            # σ == medyan → düşük


# --------------------------------------------------------------------------- #
# (3) Atama ileriye bakmaz
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "opened, day",
    [
        ("2024-01-02T00:00Z", "2024-01-01"),   # 00:00 dolum önceki günün kapanışını görür
        ("2024-01-02T04:00Z", "2024-01-01"),
        ("2024-01-02T20:00Z", "2024-01-01"),   # 20:00 barının kapanışı henüz yok
        ("2024-01-03T00:00Z", "2024-01-02"),
    ],
)
def test_regime_day_is_the_last_closed_day(opened, day):
    assert mr.regime_day(pd.Timestamp(opened)) == pd.Timestamp(day, tz=UTC)


def test_changing_the_future_does_not_change_the_label():
    rng = np.random.default_rng(1)
    values = 100 * np.exp(np.cumsum(rng.normal(0, 0.02, 600)))
    closes = pd.Series(values, index=pd.date_range("2020-01-01", periods=600, freq="D", tz=UTC))
    opened = closes.index[500] + pd.Timedelta(hours=4)       # gün 500'ün içinde → gün 499'u görür
    before = mr.regime_at(mr.regime_table(closes), opened)
    shocked = closes.copy()
    shocked.iloc[500:] *= 10.0                                 # açılış gününün kendisi ve sonrası
    after = mr.regime_at(mr.regime_table(shocked), opened)
    assert before is not None and before == after


def test_undefined_regime_returns_none():
    closes = pd.Series(100.0, index=pd.date_range("2020-01-01", periods=50, freq="D", tz=UTC))
    assert mr.regime_at(mr.regime_table(closes), pd.Timestamp("2020-02-10T00:00Z")) is None


# --------------------------------------------------------------------------- #
# (4) p ve BH
# --------------------------------------------------------------------------- #
def test_bootstrap_p_formula():
    assert mr.bootstrap_p([1.0] * 1999) == pytest.approx(2 * 1 / 2000)
    assert mr.bootstrap_p([-1.0] * 50 + [1.0] * 50) == 1.0
    assert mr.bootstrap_p([-1.0] * 10 + [1.0] * 90) == pytest.approx(2 * 11 / 101)


def test_bh_step_up():
    # m = 3, q = 0.05: eşikler 0.0167 / 0.0333 / 0.05.
    assert mr.bh_rejected({"a": 0.01, "b": 0.03, "c": 0.9}, q=0.05) == {"a", "b"}
    assert mr.bh_rejected({"a": 0.02, "b": 0.03, "c": 0.9}, q=0.05) == {"a", "b"}   # step-up
    assert mr.bh_rejected({"a": 0.02, "b": 0.04, "c": 0.9}, q=0.05) == set()
    assert mr.bh_rejected({"a": 0.04, "b": 0.045, "c": 0.05}, q=0.05) == {"a", "b", "c"}


def test_where_q_binds_on_two_sided_p():
    """§6l > TADİLAT-2: iki yönlü p ile q = 0.10 süs DEĞİLDİ, ama yalnızca dar bir bantta bağlardı.

    %95 aralık şartı p < 0.05 demektir. m = 3'te q = 0.10'un eşikleri 0.033 / 0.067 / 0.10:
    yalnızca 2. ve 3. sıra hiç bağlamaz, 1. sıra (0.033, 0.05) bandında bağlar. q = 0.05'in
    eşikleri 0.0167 / 0.033 / 0.05: üç sırada da bağlar.
    """
    alone = lambda p, q: mr.bh_rejected({"a": p, "b": 1.0, "c": 1.0}, q=q)  # noqa: E731
    assert alone(0.04, 0.10) == set() and alone(0.03, 0.10) == {"a"}
    assert alone(0.03, 0.05) == set() and alone(0.015, 0.05) == {"a"}
    # 2. sıra: q = 0.10'da p < 0.05 olan iki birim her zaman birlikte geçer.
    assert mr.bh_rejected({"a": 0.049, "b": 0.049, "c": 1.0}, q=0.10) == {"a", "b"}
    assert mr.bh_rejected({"a": 0.049, "b": 0.049, "c": 1.0}, q=0.05) == set()


# --------------------------------------------------------------------------- #
# (5) İki koşul birlikte
# --------------------------------------------------------------------------- #
def _block(point, evaluable=True, fav_low=0.1, fav_eval=True):
    return {"contrast": {"point": point, "evaluable": evaluable},
            "favoured": {"ci_low": fav_low, "ci_evaluable": fav_eval}}


def test_unit_passes_needs_both_conditions():
    assert mr.unit_passes(_block(0.3), rejected=True)
    assert not mr.unit_passes(_block(0.3), rejected=False)               # (a) BH
    assert not mr.unit_passes(_block(-0.3), rejected=True)               # yön ters
    assert not mr.unit_passes(_block(0.3, fav_low=-0.01), rejected=True)  # (b)
    assert not mr.unit_passes(_block(0.3, fav_eval=False), rejected=True)
    assert not mr.unit_passes(_block(0.3, evaluable=False), rejected=True)


# --------------------------------------------------------------------------- #
# (6) Etiketler
# --------------------------------------------------------------------------- #
UNITS = {u.key: u for u in mr.UNITS}


def _did(low, high, evaluable=True):
    return {"ci_low": low, "ci_high": high, "evaluable": evaluable}


def _ctrl(low, evaluable=True):
    return {"contrast": {"ci_low": low, "evaluable": evaluable}}


def test_label_rules():
    h2 = UNITS["H2"]
    assert mr.control_label(h2, _did(0.1, 0.5), _ctrl(-0.2)) == mr.LABEL_MODEL
    assert mr.control_label(h2, _did(-0.1, 0.5), _ctrl(0.05)) == mr.LABEL_MARKET
    assert mr.control_label(h2, _did(-0.1, 0.5), _ctrl(-0.05)) == mr.LABEL_UNKNOWN
    assert mr.control_label(h2, _did(0.1, 0.5, evaluable=False), _ctrl(0.05)) == mr.LABEL_UNKNOWN


def test_h1a_can_never_be_model_specific():
    """TADİLAT-1 > 1: kontrol bozuk, DiD ne derse desin etiket en fazla AYIRT EDİLEMEDİ."""
    assert mr.control_label(UNITS["H1a"], _did(5.0, 9.0), _ctrl(-1.0)) == mr.LABEL_UNKNOWN
    assert mr.control_label(UNITS["H1a"], None, None) == mr.LABEL_UNKNOWN


# --------------------------------------------------------------------------- #
# (7) DiD çekilişleri
# --------------------------------------------------------------------------- #
def test_did_is_deterministic_and_centred():
    rng = random.Random(3)
    groups = [{f"m{i}": [rng.gauss(mu, 1) for _ in range(5)] for i in range(20)} for mu in (1, 0, 0.5, 0)]
    a, dropped = mr.did_draws(*groups, iterations=500, seed="s")
    b, _ = mr.did_draws(*groups, iterations=500, seed="s")
    assert a == b and dropped == 0 and len(a) == 500
    point = lambda g: sum(map(sum, g.values())) / sum(map(len, g.values()))  # noqa: E731
    expected = (point(groups[0]) - point(groups[1])) - (point(groups[2]) - point(groups[3]))
    assert np.median(a) == pytest.approx(expected, abs=0.15)


def test_did_drops_draws_with_an_empty_group():
    groups = [{"m1": [1.0]}, {"m2": [0.0]}, {"m1": [0.5]}, {"m2": [0.0]}]
    draws, dropped = mr.did_draws(*groups, iterations=100, seed="x")
    assert dropped > 0
    assert all(d == pytest.approx(0.5) for d in draws)


# --------------------------------------------------------------------------- #
# (8) Kapılar
# --------------------------------------------------------------------------- #
def _xsec(model_n, model_r, ctrl_n, ctrl_r):
    return {"periods": {"A": {"model": {"trades": model_n, "avg_r": model_r},
                              "control": {"trades": ctrl_n, "avg_r": ctrl_r}}}}


def test_xsec_gate():
    original = _xsec(183, 0.1181234, 314, 0.0391234)
    assert mr.xsec_gate(_xsec(183, 0.1181234, 314, 0.0391234), original)[0]
    assert not mr.xsec_gate(_xsec(183, 0.1181235, 314, 0.0391234), original)[0]
    assert not mr.xsec_gate(_xsec(184, 0.1181234, 314, 0.0391234), original)[0]
    # orijinal yoksa yayımlanan hassasiyet
    assert mr.xsec_gate(_xsec(183, 0.1181, 314, 0.0391), None)[0]
    assert not mr.xsec_gate(_xsec(183, 0.1201, 314, 0.0391), None)[0]
    assert not mr.xsec_gate(None, original)[0]


def test_check_recorded():
    ok = {key: (n, avg) for key, (n, avg, _) in mr.RECORDED.items()}
    assert mr.check_recorded(ok) == []
    broken = dict(ok)
    broken[("dc-portfolio", "A", "dc_short")] = (335, -0.035)
    assert len(mr.check_recorded(broken)) == 1


def test_recorded_match_is_half_a_digit():
    assert mr.matches_recorded(-0.04855, -0.0486, 4)
    assert mr.matches_recorded(-0.048649, -0.0486, 4)
    assert not mr.matches_recorded(-0.04866, -0.0486, 4)
    assert mr.matches_recorded(-0.0014889765, -0.001489, 6)
    assert not mr.matches_recorded(None, 0.1, 3)


def test_regime_now_comes_from_rerun_records():
    assert mr.regime_now() == pd.Timestamp("2026-09-23T08:00:00Z")


# --------------------------------------------------------------------------- #
# (9) backtest_dc çekiliş ayrıştırması aralığı değiştirmedi
# --------------------------------------------------------------------------- #
def test_ci_is_the_percentile_of_the_exposed_draws():
    rng = random.Random(1)
    g = {f"m{i}": [rng.gauss(0, 1) for _ in range(3)] for i in range(15)}
    h = {f"m{i}": [rng.gauss(0.3, 1) for _ in range(2)] for i in range(15)}
    ci = cluster_mean_ci(g, definition="month", alpha=0.05, iterations=400, seed="a")
    assert (ci.low, ci.high) == _percentiles(cluster_mean_draws(g, iterations=400, seed="a"), 0.05)
    diff = cluster_diff_ci(g, h, definition="month", alpha=0.05, iterations=400, seed="b")
    draws, _ = cluster_diff_draws(g, h, iterations=400, seed="b")
    assert (diff.low, diff.high) == _percentiles(draws, 0.05)


def test_no_second_r_or_bootstrap_definition():
    tree = ast.parse(SOURCE)
    defined = {n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
    assert not {"r_multiple", "merge_fills", "cluster_mean_ci", "cluster_diff_ci", "precision"} & defined
    imported = {a.name for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) for a in n.names}
    assert {"r_multiple", "merge_fills", "cluster_diff_draws", "precision", "precision_diff"} <= imported
    for forbidden in ("core.portfolio", "strategies"):
        assert forbidden not in SOURCE.split('"""', 2)[2].split("logger =")[0]


# --------------------------------------------------------------------------- #
# (10) Uçtan uca: sentetik defter + sentetik rejim
# --------------------------------------------------------------------------- #
def _row(model, symbol, opened, r):
    return {c: "" for c in TRADE_COLUMNS} | {
        "strategy": model, "symbol": symbol, "direction": "long",
        "opened_at": opened.isoformat(), "closed_at": (opened + pd.Timedelta(hours=8)).isoformat(),
        "entry_price": 100, "exit_price": 100, "qty": 1, "notional": 100, "stop_price": 99,
        "risk_amount": 1.0, "pnl": r, "fee": 0, "slippage_cost": 0, "funding": 0,
        "exit_reason": "tp", "signal_reason": "x | arm=rsi2_reversal", "notes": "",
    }


def _table(start: str, days: int) -> pd.DataFrame:
    """Aylık dönüşümlü yön/oynaklık — sentetik, `regime_table`dan bağımsız bir etiket takvimi."""
    index = pd.date_range(start, periods=days, freq="D", tz=UTC)
    direction = [mr.UP if (d.month % 2 == 0) else mr.DOWN for d in index]
    vol = [mr.HIGH if (d.day <= 15) else mr.LOW for d in index]
    return pd.DataFrame({"close": 1.0, "sma200": 1.0, "vol30": 1.0, "vol_median365": 1.0,
                         "direction": direction, "vol": vol}, index=index)


def _write(root: Path, model: str, rows):
    Ledger(root).append_trades(model, rows)


def _positions(model, start, end, r_up, r_down, *, symbol="BTC-USDT-SWAP", seed=0):
    rng = random.Random(seed)
    rows = []
    for opened in pd.date_range(start, end, freq="3D", tz=UTC):
        up = (mr.regime_day(opened).month % 2 == 0)
        rows.append(_row(model, symbol, opened, (r_up if up else r_down) + rng.gauss(0, 0.3)))
    return rows


@pytest.fixture()
def world(tmp_path):
    symbols = {"ema": ["BTC-USDT-SWAP", "ETH-USDT-SWAP"], "dc": ["BTC-USDT-SWAP", "ETH-USDT-SWAP"]}
    windows = {"A": ("2022-01-05", "2024-06-01"), "B": ("2024-09-05", "2026-08-01")}
    for period, (s, e) in windows.items():
        for sym in symbols["ema"]:
            _write(tmp_path / "ema" / f"{period}-{sym}" / "ledger", "ema_trend",
                   _positions("ema_trend", s, e, 1.0, -0.5, symbol=sym))
        _write(tmp_path / "ema" / f"{period}-portfolio" / "ledger", "ema_trend",
               _positions("ema_trend", s, e, 1.0, -0.5))
        for sym in symbols["dc"]:
            _write(tmp_path / "dc" / f"{period}-{sym}" / "ledger", "dc_short",
                   _positions("dc_short", s, e, 0.0, 0.0, symbol=sym))
        _write(tmp_path / "dc" / f"{period}-portfolio" / "ledger", "dc_short",
               _positions("dc_short", s, e, 0.0, 0.0))
        _write(tmp_path / "dc" / f"{period}-portfolio" / "ledger", "dc_coinflip",
               _positions("dc_coinflip", s, e, 0.0, 0.0, seed=1))
        _write(tmp_path / "xsec" / period / "ledger", "xsec_mom", _positions("xsec_mom", s, e, 1.0, -0.5))
        _write(tmp_path / "xsec" / period / "ledger", "xsec_random",
               _positions("xsec_random", s, e, 0.0, 0.0, seed=2))
    live = pd.Timestamp("2026-09-12", tz=UTC)
    _write(tmp_path / "live" / "base", "meanrev",
           [_row("meanrev", "BTC-USDT-SWAP", live + pd.Timedelta(days=i), 0.1) for i in range(5)])
    _write(tmp_path / "live" / "scalp", "scalp_fixed",
           [_row("scalp_fixed", "BTC-USDT-SWAP", live + pd.Timedelta(days=i), 0.1) for i in range(5)])
    _write(tmp_path / "live" / "scalp", "scalp_coinflip",
           [_row("scalp_coinflip", "BTC-USDT-SWAP", live + pd.Timedelta(days=i), 0.0) for i in range(5)])
    dirs = {"ema": tmp_path / "ema", "dc": tmp_path / "dc", "xsec": tmp_path / "xsec",
            "live-base": tmp_path / "live" / "base", "live-scalp": tmp_path / "live" / "scalp"}
    return dirs, symbols


SETTINGS = mr.Settings(iterations=300, alpha=0.05, seed=7, min_trades=30)


def test_end_to_end_flow(world):
    dirs, symbols = world
    result = mr.evaluate(table=_table("2021-06-01", 2000), dirs=dirs, symbols=symbols,
                         settings=SETTINGS, xsec_ok=True, xsec_reason="test")
    a, b, summary = result["periods"]["A"], result["periods"]["B"], result["summary"]
    # Güçlü, yönü doğru etki: ema ve xsec geçer; dc'de etki yok → geçmez.
    assert a["H1a"]["verdict"] == "GEÇTİ" and a["H1b"]["verdict"] == "GEÇTİ"
    assert a["H2"]["verdict"] == "GEÇMEDİ"
    assert a["H1a"]["bh"]["m"] == 3
    # B yalnızca A'da geçenleri doğrular; ailesi m_B = 2.
    assert b["H1b"]["bh"]["m"] == 2 and b["H2"]["verdict"].startswith("bilgi")
    assert summary["H1b"]["verdict"] == "DOĞRULANDI"
    # xsec kontrolü etkisiz → DiD > 0 → MODELDEN; H1a'nın etiketi zorunlu olarak AYIRT EDİLEMEDİ.
    assert summary["H1b"]["label_A"] == mr.LABEL_MODEL and summary["H1b"]["model_specific_claim"]
    assert summary["H1a"]["label_A"] == mr.LABEL_UNKNOWN and not summary["H1a"]["model_specific_claim"]
    assert "HAYIR" in summary["H1a"]["veto_input"]
    assert a["H1a"]["control"] == mr.CONTROL_BROKEN
    # İkincil kaynak raporlanır, karar birincilden.
    assert a["H2"]["secondary"]["source"] == "dc-portfolio"
    # H3: tek ay kümesi → değerlendirilemez; aileye ve B'ye girmez.
    assert a["H3a"]["verdict"] == "DEĞERLENDİRİLEMEZ" and "H3a" not in b
    assert a["H3b"]["primary"]["positions"] == 5
    # Dört hücre her zaman raporlanır.
    assert set(a["H1a"]["primary"]["cells"]) == {"yukari|yuksek", "yukari|dusuk", "asagi|yuksek", "asagi|dusuk"}
    assert a["H1a"]["primary"]["undefined_regime"] == 0


def test_xsec_gate_failure_keeps_m_at_three(world):
    dirs, symbols = world
    result = mr.evaluate(table=_table("2021-06-01", 2000), dirs=dirs, symbols=symbols,
                         settings=SETTINGS, xsec_ok=False, xsec_reason="A farklı")
    a = result["periods"]["A"]
    assert a["H1b"]["measured"] is False and a["H1b"]["verdict"].startswith("ÖLÇÜLMEDİ")
    assert a["H1b"]["bh"]["p"] == 1.0 and a["H1a"]["bh"]["m"] == 3


def test_market_label_when_control_moves_too(world, tmp_path):
    """Kontrol aynı rejim farkını taşıyorsa: model geçer ama etiket MODELDEN olamaz."""
    dirs, symbols = world
    for period, (s, e) in {"A": ("2022-01-05", "2024-06-01"), "B": ("2024-09-05", "2026-08-01")}.items():
        path = dirs["xsec"] / period / "ledger" / "xsec_random" / "trades.csv"
        path.unlink()
        _write(dirs["xsec"] / period / "ledger", "xsec_random",
               _positions("xsec_random", s, e, 1.0, -0.5, seed=5))
    result = mr.evaluate(table=_table("2021-06-01", 2000), dirs=dirs, symbols=symbols,
                         settings=SETTINGS, xsec_ok=True, xsec_reason="test")
    assert result["periods"]["A"]["H1b"]["verdict"] == "GEÇTİ"
    assert result["summary"]["H1b"]["label_A"] == mr.LABEL_MARKET
    assert not result["summary"]["H1b"]["model_specific_claim"]


def test_early_positions_without_regime_are_counted(world):
    dirs, symbols = world
    result = mr.evaluate(table=_table("2023-01-01", 1400), dirs=dirs, symbols=symbols,
                         settings=SETTINGS, xsec_ok=True, xsec_reason="test")
    assert result["periods"]["A"]["H1b"]["primary"]["undefined_regime"] > 0


def test_preflight_reads_no_ledger_content(monkeypatch, world, tmp_path):
    """Ön-kontrol dosya VARLIĞINA bakar; defter satırı, R ya da rejim ataması okumaz."""
    dirs, symbols = world
    monkeypatch.setattr(mr, "load_ledger", lambda *a, **k: pytest.fail("preflight defter okudu"))
    monkeypatch.setattr(mr, "load_source", lambda *a, **k: pytest.fail("preflight kaynak okudu"))
    monkeypatch.setattr(mr, "fetch_btc", lambda *a, **k: _bars(500, start="2020-10-01"))
    original = tmp_path / "orig.json"
    original.write_text("{}", encoding="utf-8")
    args = mr._parse_args(["--stage", "preflight", "--ema-dir", str(dirs["ema"]), "--dc-dir",
                           str(dirs["dc"]), "--xsec-dir", "x", "--xsec-original", str(original)])
    assert mr.preflight(args, dirs=dirs, symbols=symbols, config={}) == 0
    (dirs["dc"] / "B-portfolio" / "ledger" / "dc_coinflip" / "trades.csv").unlink()
    assert mr.preflight(args, dirs=dirs, symbols=symbols, config={}) == 3

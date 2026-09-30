"""`scripts/measure_funding_carry.py` (docs/backtest.md > 6t, TADİLAT-1).

Sınananlar: (1) eşikler maliyetten türer; (2) sinyal zamanla tanımlıdır (4 ve 8 saatlik ızgara),
boşlukta/eksik başlangıçta tanımsızdır ve ileriye bakmaz; (3) evren: ciro sıralaması, U1/U2,
ilk N; (4) dolum T + 1h, fonlama yalnızca giriş < t < çıkış; (5) histerezis, kapasite, takas
yok; (6) üç bileşen nete eşit, günlük getiri toplamı nete eşit; (7) likidasyon teminatın
tamamı; (8) karar tablosu ve değerlendirilemezlik; (9) arşiv: kasa satırları okunmaz,
preflight oran AYRIŞTIRMAZ; (10) snapshot `now` = kasa; (11) import yasağı; (12) uçtan uca.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from core.config import load_config
from scripts import measure_funding_carry as fc

SOURCE = Path(fc.__file__).read_text(encoding="utf-8")
H = pd.Timedelta(hours=1)
CONFIG = load_config()
COSTS = fc.scenario_costs(CONFIG)
TAKER = COSTS["taker"]


def _series(symbol: str, start: str, step_h: int, rate, end: pd.Timestamp = fc.DEV_END) -> fc.FundingSeries:
    times = pd.date_range(pd.Timestamp(start, tz="UTC"), end - H, freq=f"{step_h}h")
    rates = [rate(t) if callable(rate) else rate for t in times]
    return fc.FundingSeries(symbol=symbol, times=times.as_unit("ns").asi8.astype("int64"), rates=np.asarray(rates, dtype="float64"))


def _flat(price: float, start: pd.Timestamp = fc.SNAPSHOT_START, end: pd.Timestamp = fc.DEV_END) -> pd.DataFrame:
    idx = pd.date_range(start, end - H, freq="h")
    return pd.DataFrame({"open": price, "high": price, "low": price, "close": price}, index=idx)


def _run(symbols, funding, perp=None, spot=None, threshold=None):
    perp = perp or {s: _flat(100.0) for s in symbols}
    spot = spot or {s: _flat(100.0) for s in symbols}
    return fc.simulate(symbols, funding, perp, spot, threshold=TAKER.entry_threshold if threshold is None else threshold,
                       costs=TAKER, taker=TAKER, mm=0.005)


HIGH = 0.001   # 8 saatte %0.1 → günlük %0.3, taker eşiğinin (~%0.071) çok üstü


# (1) ------------------------------------------------------------------------------------
def test_thresholds_follow_costs():
    assert TAKER.round_trip == pytest.approx(0.005)
    assert TAKER.entry_threshold == pytest.approx(2 * 0.005 / 14)
    assert COSTS["maker"].round_trip == pytest.approx(0.002)
    assert COSTS["maker"].entry_threshold == pytest.approx(2 * 0.002 / 14)
    assert fc.MEASURE_START == pd.Timestamp("2026-06-29T00:00Z")
    assert fc.DEV_END == pd.Timestamp("2026-09-27T00:00Z")


# (2) ------------------------------------------------------------------------------------
@pytest.mark.parametrize("step,count", [(8, 21), (4, 42)])
def test_signal_is_time_based(step, count):
    start = "2026-06-22T08:00Z" if step == 8 else "2026-06-22T04:00Z"
    s = _series("X", start, step, 0.0001)
    assert fc.signal_at(s, fc.MEASURE_START) == pytest.approx(count * 0.0001 / 7)


def test_signal_undefined_on_gap_and_late_start():
    s = _series("X", "2026-06-22T08:00Z", 8, 0.0001)
    t = pd.Timestamp("2026-07-10T00:00Z")
    k = int(np.searchsorted(s.times, (t - pd.Timedelta(days=3)).value))
    gapped = fc.FundingSeries(symbol="X", times=np.delete(s.times, k), rates=np.delete(s.rates, k))
    assert fc.signal_at(gapped, t) is None
    late = _series("X", "2026-06-22T16:00Z", 8, 0.0001)
    assert fc.signal_at(late, fc.MEASURE_START) is None
    assert fc.signal_at(late, fc.MEASURE_START + 8 * H) is not None


def test_signal_does_not_look_ahead():
    s = _series("X", "2026-06-22T08:00Z", 8, 0.0001)
    t = pd.Timestamp("2026-07-10T00:00Z")
    later = fc.FundingSeries(symbol="X", times=s.times, rates=np.where(s.times > t.value, 9.0, s.rates))
    assert fc.signal_at(later, t) == pytest.approx(fc.signal_at(s, t))


# (3) ------------------------------------------------------------------------------------
def test_ranking_sums_volume_and_breaks_ties_by_name():
    ranked = fc.rank_perps(["B-USDT-SWAP", "A-USDT-SWAP", "C-USDT-SWAP"],
                           {"A-USDT-SWAP": {"d1": 5.0, "d2": 5.0}, "B-USDT-SWAP": {"d1": 10.0}})
    assert ranked == [("A-USDT-SWAP", 10.0), ("B-USDT-SWAP", 10.0), ("C-USDT-SWAP", 0.0)]


def test_identity_gate():
    assert fc.identity_check(_flat(100.5), _flat(100.0))[0]
    assert not fc.identity_check(_flat(110.0), _flat(100.0))[0]
    short = _flat(100.0, start=fc.DEV_END - 10 * H)
    assert fc.identity_check(short, short) == (False, 10)


def test_select_takes_first_n_eligible(monkeypatch):
    monkeypatch.setattr(fc, "N_UNIVERSE", 2)
    frames = {"A-USDT-SWAP": _flat(1.0), "A-USDT": _flat(1.0), "C-USDT-SWAP": _flat(2.0), "C-USDT": _flat(1.0),
              "D-USDT-SWAP": _flat(1.0), "D-USDT": _flat(1.0), "E-USDT-SWAP": _flat(1.0), "E-USDT": _flat(1.0)}
    ranked = [("A-USDT-SWAP", 4.0), ("B-USDT-SWAP", 3.0), ("C-USDT-SWAP", 2.0), ("D-USDT-SWAP", 1.0), ("E-USDT-SWAP", 0.5)]
    sel = fc.select_universes(ranked, {"A-USDT", "C-USDT", "D-USDT", "E-USDT"}, lambda i: frames[i],
                              archive_symbols=["C-USDT-SWAP", "E-USDT-SWAP"], ema_symbols=["A-USDT-SWAP"])
    assert sel["primary"] == ["A-USDT-SWAP", "D-USDT-SWAP"]
    assert [w["status"] for w in sel["walk"]] == ["ok", "no_spot", "identity", "ok"]
    assert sel["today"] == ["E-USDT-SWAP"] and sel["excluded"]["today"] == {"C-USDT-SWAP": "identity"}


# (4) (6) ---------------------------------------------------------------------------------
def test_fill_next_hour_and_strict_funding_accrual():
    funding = {"X": _series("X", "2026-06-15T00:00Z", 1, HIGH / 8)}   # saatlik ızgara: uçlar tam dolum anında
    pos = _run(["X"], funding)["positions"]
    assert len(pos) == 1
    p = pos[0]
    assert p.entry == fc.MEASURE_START + H and p.exit == fc.DEV_END - H and p.exit_reason == "period_end"
    hours_strictly_inside = int((p.exit - p.entry) / H) - 1
    assert len(p.funding) == hours_strictly_inside
    assert all(p.entry < t < p.exit for t, _ in p.funding)


def test_components_sum_and_daily_returns_sum_to_net():
    funding = {"X": _series("X", "2026-06-22T08:00Z", 8, HIGH)}
    perp = {"X": _flat(100.0).assign(open=lambda f: 100.0 + 0.001 * np.arange(len(f)))}
    spot = {"X": _flat(99.0).assign(open=lambda f: 99.0 + 0.0012 * np.arange(len(f)))}
    res = _run(["X"], funding, perp, spot)
    p = res["positions"][0]
    assert p.net == pytest.approx(p.funding_total + p.basis - p.cost)
    assert p.basis == pytest.approx(1.0 * (p.s1 / p.s0 - p.p1 / p.p0))
    summary = fc.summarize(res, perp, spot, capital=10.0, seed_base="1", alpha=0.05, with_ci=False)
    assert sum(r["return"] for r in summary["daily"]) * 10.0 == pytest.approx(p.net)
    assert summary["components"]["net"] * 10.0 == pytest.approx(p.net)


# (5) ------------------------------------------------------------------------------------
def test_exit_on_negative_and_hysteresis_band():
    flip = pd.Timestamp("2026-08-01T00:00Z")
    band = TAKER.entry_threshold * 7 / 21 / 2           # 0 < s < eşik: tutulur, girilmez
    funding = {
        "X": _series("X", "2026-06-22T08:00Z", 8, lambda t: HIGH if t < flip else -HIGH),
        "Y": _series("Y", "2026-06-22T08:00Z", 8, lambda t: HIGH if t < flip else band),
    }
    pos = {p.symbol: p for p in _run(["X", "Y"], funding)["positions"]}
    assert pos["X"].exit_reason == "signal" and flip < pos["X"].exit < flip + pd.Timedelta(days=4)
    assert pos["Y"].exit_reason == "period_end"


def test_capacity_ranks_by_signal_and_never_swaps():
    late = pd.Timestamp("2026-08-01T00:00Z")
    funding = {f"S{i}": _series(f"S{i}", "2026-06-22T08:00Z", 8, HIGH * (1 + i / 10)) for i in range(6)}
    funding["LATE"] = _series("LATE", "2026-06-22T08:00Z", 8, lambda t: HIGH * 5 if t >= late else 0.0)
    pos = _run(sorted(funding), funding)["positions"]
    held = sorted(p.symbol for p in pos)
    assert held == ["S1", "S2", "S3", "S4", "S5"]            # en yüksek 5; S0 dışarıda, LATE takas ETMEZ


# (7) ------------------------------------------------------------------------------------
def test_liquidation_loses_whole_margin():
    funding = {"X": _series("X", "2026-06-22T08:00Z", 8, HIGH)}
    perp = _flat(100.0)
    spike = pd.Timestamp("2026-07-15T10:00Z")
    perp.loc[spike, "high"] = 250.0
    res = _run(["X"], funding, {"X": perp}, {"X": _flat(100.0)})
    p = res["positions"][0]
    assert p.exit_reason == "liquidation" and p.exit == spike + H and res["counters"]["liquidations"] == 1
    perp_leg = p.q_perp * (p.p0 - p.p1) - 0.005 * p.q_perp * p.p0
    assert perp_leg == pytest.approx(-1.0)


def test_liquidation_never_books_favourable_basis():
    """TADİLAT-2: spot bar içinde P_liq'i aşıp YUKARIDA kapansa da çıkışı `P_liq × S₀/P₀`'la sınırlıdır."""
    funding = {"X": _series("X", "2026-06-22T08:00Z", 8, HIGH)}
    perp, spot = _flat(100.0), _flat(100.5)
    spike = pd.Timestamp("2026-07-15T10:00Z")
    perp.loc[spike, "high"] = 250.0
    spot.loc[spike, ["high", "close"]] = [260.0, 240.0]
    res = _run(["X"], funding, {"X": perp}, {"X": spot})
    p = res["positions"][0]
    assert p.exit_reason == "liquidation"
    liq = p.p0 * (2.0 - 0.005)
    assert p.s1 == pytest.approx(liq * p.s0 / p.p0)
    assert p.basis == pytest.approx(0.0, abs=1e-12)
    # bar kapanışı sınırın altındaysa kapanış kullanılır (baz yalnızca aleyhe olabilir)
    spot.loc[spike, "close"] = 150.0
    q = _run(["X"], funding, {"X": perp}, {"X": spot})["positions"][0]
    assert q.s1 == pytest.approx(150.0) and q.basis < 0


# (8) ------------------------------------------------------------------------------------
def _primary(pos_days, entries, low, weeks=13):
    return {"position_days": pos_days, "entries": entries, "m2": {"binding_low": low, "weeks": weeks}}


def test_decision_table():
    assert fc.decide(_primary(69.9, 20, 0.1), {"persistent": True})["m2"] == "DEĞERLENDİRİLEMEZ"
    assert fc.decide(_primary(100, 9, 0.1), {"persistent": True})["m2"] == "DEĞERLENDİRİLEMEZ"
    assert fc.decide(_primary(100, 20, 0.1, weeks=9), {"persistent": True})["m2"] == "DEĞERLENDİRİLEMEZ"
    ok = fc.decide(_primary(100, 20, 0.001), {"persistent": True})
    assert ok["m2"] == "GEÇTİ" and ok["reading"].startswith("kasaya aday")
    assert fc.decide(_primary(100, 20, 0.001), {"persistent": False})["reading"].startswith("mekanizmasız")
    assert fc.decide(_primary(100, 20, -0.001), {"persistent": True})["m2"] == "GEÇMEDİ"


# (9) ------------------------------------------------------------------------------------
def _write_archive(root: Path, symbol: str, series: fc.FundingSeries, *, garbage: bool = False,
                   extra_vault_rows: int = 3) -> None:
    root.mkdir(parents=True, exist_ok=True)
    lines = ["inst_id,funding_time_ms,funding_time_utc,funding_rate,realized_rate,method,archived_at"]
    times = list(series.times) + [(fc.KASA_START + i * 8 * H).value for i in range(extra_vault_rows)]
    rates = list(series.rates) + [0.5] * extra_vault_rows
    for t, r in zip(times, rates):
        ts = pd.Timestamp(int(t), tz="UTC")
        rate = "XXX" if garbage else repr(float(r))
        lines.append(f"{symbol},{int(t) // 1_000_000},{ts.isoformat()},{rate},{rate},current_period,2026-09-28T00:00:00Z")
    (root / f"{symbol}.csv").write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_archive_excludes_vault_and_conflicts(tmp_path):
    s = _series("A-USDT-SWAP", "2026-06-22T08:00Z", 8, 0.0001)
    _write_archive(tmp_path, "A-USDT-SWAP", s)
    conflict_ms = int(s.times[5]) // 1_000_000
    (tmp_path / "_conflicts.csv").write_text(
        "inst_id,funding_time_ms,funding_time_utc,field,archived_value,observed_value,detected_at\n"
        f"A-USDT-SWAP,{conflict_ms},x,realized_rate,1,2,x\n", encoding="utf-8")
    series, meta = fc.read_archive(tmp_path, with_rates=True)
    got = series["A-USDT-SWAP"]
    assert got.times.max() < fc.KASA_START.value
    assert len(got.times) == len(s.times) - 1 and meta["files"]["A-USDT-SWAP"]["conflicts_dropped"] == 1


def test_preflight_never_parses_rates(tmp_path):
    _write_archive(tmp_path, "A-USDT-SWAP", _series("A-USDT-SWAP", "2026-06-22T08:00Z", 8, 0.0), garbage=True)
    series, _ = fc.read_archive(tmp_path, with_rates=False)
    assert series["A-USDT-SWAP"].rates is None
    with pytest.raises(ValueError):
        fc.read_archive(tmp_path, with_rates=True)
    with pytest.raises(RuntimeError):
        fc.signal_at(series["A-USDT-SWAP"], fc.MEASURE_START)


# (10) -----------------------------------------------------------------------------------
def test_hourly_fetch_uses_vault_now_and_trims():
    seen = {}

    def fetcher(config, inst, *, now):
        seen["now"] = now
        seen["tf"] = config["timeframe"]
        return _flat(1.0, end=fc.DEV_END + 48 * H)

    frame = fc.fetch_hourly(CONFIG, "A-USDT", cache_dir="/nonexistent", fetcher=fetcher)
    assert seen == {"now": fc.KASA_START, "tf": "1H"}
    assert frame.index.max() + H <= fc.KASA_START and frame.index.min() == fc.SNAPSHOT_START


# (11) -----------------------------------------------------------------------------------
def test_import_ban():
    banned = ("strategies", "core.portfolio", "core.engine", "core.ledger", "core.metrics")
    for node in ast.walk(ast.parse(SOURCE)):
        names = []
        if isinstance(node, ast.Import):
            names = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            names = [node.module or ""]
        for name in names:
            assert not any(name == b or name.startswith(b + ".") for b in banned), name


# (12) -----------------------------------------------------------------------------------
class FakeClient:
    SWAPS = {"A-USDT-SWAP": 300.0, "B-USDT-SWAP": 200.0, "C-USDT-SWAP": 100.0, "D-USDT-SWAP": 50.0}
    SPOTS = ("A-USDT", "C-USDT", "D-USDT")

    def get(self, path, params):
        if path.endswith("instruments"):
            if params["instType"] == "SWAP":
                return [{"instId": s, "settleCcy": "USDT", "ctType": "linear"} for s in self.SWAPS]
            return [{"instId": s, "quoteCcy": "USDT"} for s in self.SPOTS]
        assert int(params["after"]) == fc._ms(fc.VOLUME_END) and params["bar"] == "1Dutc"
        days = pd.date_range(fc.VOLUME_START - 5 * pd.Timedelta(days=1), fc.VOLUME_END - pd.Timedelta(days=1), freq="D")
        v = self.SWAPS[params["instId"]]
        return [[str(d.value // 1_000_000), "1", "1", "1", "1", "0", "0", str(v), "1"] for d in days]


def _fetcher(config, inst, *, now):
    price = 2.0 if inst == "C-USDT-SWAP" else 100.0
    return _flat(price, end=now + 24 * H)


def test_end_to_end(tmp_path, monkeypatch):
    monkeypatch.setattr(fc, "N_UNIVERSE", 2)
    monkeypatch.setattr(fc, "ITERATIONS", 200)
    monkeypatch.setattr(fc, "ema_symbols", lambda config: ["A-USDT-SWAP"])
    archive = tmp_path / "archive"
    _write_archive(archive, "A-USDT-SWAP", _series("A-USDT-SWAP", "2026-06-22T08:00Z", 8, HIGH))
    args = SimpleNamespace(archive=str(archive), out_dir=str(tmp_path / "snap"), run="#test",
                           pins=str(tmp_path / "snap" / "funding_carry"))
    assert fc.run_snapshot(args, CONFIG, client=FakeClient(), fetcher=_fetcher) == 0
    manifest = json.loads((tmp_path / "snap" / "funding_carry" / "MANIFEST.json").read_text())
    assert manifest["selection"]["primary"] == ["A-USDT-SWAP", "D-USDT-SWAP"]

    args.out_dir = str(tmp_path / "out")
    assert fc.run_analysis(args, CONFIG, measure=False) == 0
    pre = json.loads((tmp_path / "out" / "funding_carry_preflight.json").read_text())
    assert pre["universe"]["measured"] == ["A-USDT-SWAP"] and pre["universe"]["unmeasured"] == ["D-USDT-SWAP"]
    assert pre["universe"]["measured_share"] == 0.5 and pre["universe"]["measured_note"] == fc.UNMEASURED_SENTENCE
    assert not {"m1", "primary_taker", "decision"} & set(pre)

    assert fc.run_analysis(args, CONFIG, measure=True) == 0
    res = json.loads((tmp_path / "out" / "funding_carry.json").read_text())
    assert res["decision"]["m2"] == "DEĞERLENDİRİLEMEZ"          # tek giriş < 10
    assert res["primary_taker"]["entries"] == 1
    assert (tmp_path / "out" / "funding_carry_positions.csv").is_file()

    # MANIFEST'le tutmayan seçim bir veri kapısıdır
    manifest["selection"]["primary"] = ["D-USDT-SWAP", "A-USDT-SWAP"]
    (tmp_path / "snap" / "funding_carry" / "MANIFEST.json").write_text(json.dumps(manifest))
    assert fc.run_analysis(args, CONFIG, measure=False) == 3

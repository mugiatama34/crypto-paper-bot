"""`scripts/measure_market_direction.py`: ön-kayda (docs/backtest.md > 6m) MEKANİK sadakat.

Sınanan: (1) sabitler ön-kayıtlıdır ve girdi değildir; (2) pencere kuralı `core/metrics.py::
_market_context`in kuralıdır — BTC hizasının işaretli getirisi `market_tailwind_pct` ile BİREBİR;
(3) pencere fiyatı İLERİYE BAKMAZ; (4) hiza ve nötr; (5) fiyat kapısı (kayma geri çıkarılarak,
%1 eşiği); (6) pins SHA256 doğrulaması; (7) OLS ve hafta bootstrap'ı (deterministik, < 10 hafta
değerlendirilemez); (8) M2 gün penceresi (kesim + son kapanış) ve pozisyonsuz günlerin dâhil
olması; (9) olay kümesi ve ICC; (10) hizalı pay farkı `cluster_diff_draws`in ortalamasıdır;
(11) import yasağı ve çıktıda kapı/etiket olmaması; (12) uçtan uca sentetik defter.
Hiçbir test gerçek bir defteri OKUMAZ.
"""

from __future__ import annotations

import ast
import gzip
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from core.ledger import EQUITY_COLUMNS, TRADE_COLUMNS
from core.metrics import _market_context
from scripts import measure_market_direction as md

SOURCE = Path("scripts/measure_market_direction.py").read_text(encoding="utf-8")


def ts(value: str) -> pd.Timestamp:
    return pd.Timestamp(value, tz="UTC")


def candles(start: str, periods: int, freq: str = "4h", *, drift: float = 0.01, base: float = 100.0) -> pd.DataFrame:
    index = pd.date_range(ts(start), periods=periods, freq=freq)
    closes = base * np.cumprod(np.full(periods, 1.0 + drift))
    opens = np.concatenate([[base], closes[:-1]])
    return pd.DataFrame({"open": opens, "close": closes}, index=index)


def pos(symbol: str, direction: str, opened: str, closed: str, *, r: float | None = 0.5,
        pnl: float = 1.0, entry: float = 100.0, model: str = "m") -> md.Pos:
    return md.Pos(model=model, symbol=symbol, direction=direction, opened_at=ts(opened), closed_at=ts(closed),
                  entry_price=entry, r=r, pnl=pnl)


# --------------------------------------------------------------------------- #
# (1) Sabitler
# --------------------------------------------------------------------------- #
def test_preregistered_constants():
    assert md.NEUTRAL_EPS == 1e-12
    assert md.PRICE_TOL == 1e-6
    assert md.PRICE_GATE_MAX_FAIL == 0.01
    assert md.DEFINITIONS == ("btc", "coin")
    assert md.MIN_CLUSTERS == 10


def test_parameters_are_not_cli_inputs():
    args = md._parse_args(["--stage", "measure"])
    for name in ("neutral_eps", "price_tol", "tolerance", "iterations", "alpha", "min_clusters",
                 "start", "end", "b_end", "models", "period"):
        assert not hasattr(args, name)


# --------------------------------------------------------------------------- #
# (2) Pencere kuralı = core/metrics.py::_market_context
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("direction", ["long", "short"])
@pytest.mark.parametrize("opened,closed", [
    ("2024-01-01T04:00:00", "2024-01-02T08:00:00"),
    ("2024-01-01T06:30:00", "2024-01-03T01:00:00"),   # bar sınırında olmayan damgalar
])
def test_btc_window_return_equals_market_tailwind(direction, opened, closed):
    btc = candles("2023-12-30", 60, drift=0.003)
    book = md.PriceBook({("4H", "BTC"): btc})
    got = md.window_return(book, "4H", "BTC", ts(opened), ts(closed))
    row = {"opened_at": ts(opened).isoformat(), "closed_at": ts(closed).isoformat(), "direction": direction,
           "entry_price": 100.0, "stop_price": 95.0}
    tailwind, _, count = _market_context([row], reference=btc["close"])
    sign = 1.0 if direction == "long" else -1.0
    assert count == 1
    assert sign * got * 100.0 == pytest.approx(tailwind, rel=0, abs=1e-12)


# --------------------------------------------------------------------------- #
# (3) İleriye bakış yok
# --------------------------------------------------------------------------- #
def test_window_price_never_reads_a_later_bar():
    frame = candles("2024-01-01", 10)
    book = md.PriceBook({("4H", "X"): frame})
    when = ts("2024-01-01T09:59:00")   # 08:00 barı en son bilinen
    assert book.close_at_or_before("4H", "X", when) == pytest.approx(frame["close"].iloc[2])
    assert all(stamp <= when for (_, _, stamp) in book.used)
    assert book.close_at_or_before("4H", "X", ts("2023-12-31T00:00:00")) is None


# --------------------------------------------------------------------------- #
# (4) Hiza
# --------------------------------------------------------------------------- #
def test_alignment_labels():
    assert md.alignment(0.02, "long") == "aligned"
    assert md.alignment(-0.02, "long") == "misaligned"
    assert md.alignment(-0.02, "short") == "aligned"
    assert md.alignment(0.02, "short") == "misaligned"
    assert md.alignment(0.0, "long") == "neutral"
    assert md.alignment(1e-13, "short") == "neutral"
    assert md.alignment(None, "long") is None


def test_m1_share_excludes_neutral_and_splits_groups():
    items = [pos("A", "long", "2024-01-01", "2024-01-02", r=1.0, pnl=5),
             pos("A", "long", "2024-01-03", "2024-01-04", r=-1.0, pnl=-5),
             pos("A", "short", "2024-01-05", "2024-01-06", r=0.5, pnl=2),
             pos("A", "short", "2024-01-07", "2024-01-08", r=-0.2, pnl=-1)]
    for p, ret in zip(items, (0.03, -0.02, -0.01, 0.0)):
        p.btc_ret = ret
        p.coin_ret = ret
    block = md.m1_alignment(items)["btc"]
    assert block["all"]["aligned"] == 2 and block["all"]["misaligned"] == 1 and block["all"]["neutral"] == 1
    assert block["all"]["aligned_share"] == pytest.approx(2 / 3)
    assert block["all"]["aligned_group"]["avg_r"] == pytest.approx(0.75)
    assert block["all"]["misaligned_group"]["win_rate"] == 0.0
    assert block["short"]["aligned_share"] == 1.0


# --------------------------------------------------------------------------- #
# (5) Fiyat kapısı
# --------------------------------------------------------------------------- #
def test_price_gate_reconstructs_open_through_slippage():
    frame = candles("2024-01-01", 10)
    book = md.PriceBook({("4H", "X"): frame})
    s = 0.0001
    opened = frame.index[3]
    open_price = float(frame["open"].iloc[3])
    good = [pos("X", "long", str(opened.tz_convert(None)), "2024-01-02", entry=open_price * (1 + s)),
            pos("X", "short", str(opened.tz_convert(None)), "2024-01-02", entry=open_price * (1 - s))]
    gate = md.price_gate(good, book, "4H", s)
    assert gate["passed"] and gate["failed"] == 0 and gate["checked"] == 2
    bad = good + [pos("X", "long", str(opened.tz_convert(None)), "2024-01-02", entry=open_price * 1.01)]
    assert not md.price_gate(bad, book, "4H", s)["passed"]


def test_price_gate_threshold_is_one_percent():
    frame = candles("2024-01-01", 400, freq="15min")
    book = md.PriceBook({("15m", "X"): frame})
    items = []
    for i in range(200):
        stamp = frame.index[i]
        entry = float(frame["open"].iloc[i]) * (1.0005 if i >= 2 else 1.05)   # 2/200 = %1 tutmuyor
        items.append(pos("X", "long", str(stamp.tz_convert(None)), "2024-01-05", entry=entry))
    assert md.price_gate(items, book, "15m", 0.0005)["passed"]           # tam %1: AŞMIYOR
    items.append(pos("X", "long", str(frame.index[300].tz_convert(None)), "2024-01-05", entry=1.0))
    assert not md.price_gate(items, book, "15m", 0.0005)["passed"]       # 3/201 > %1


# --------------------------------------------------------------------------- #
# (6) Pins
# --------------------------------------------------------------------------- #
def _write_pins(root: Path, files: dict[str, bytes]) -> None:
    root.mkdir(parents=True)
    lines = []
    for rel, raw in files.items():
        target = root / f"{rel}.gz"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(gzip.compress(raw))
        lines.append(f"{hashlib.sha256(raw).hexdigest()}  {rel}\n")
    (root / "SHA256SUMS").write_text("".join(lines))


def test_pins_verified_and_tamper_detected(tmp_path):
    pins = tmp_path / "pins"
    _write_pins(pins, {"ema/A-portfolio/ledger/x/trades.csv": b"a,b\n1,2\n"})
    assert md.verify_and_unpack_pins(pins, tmp_path / "out") == []
    empty = tmp_path / "empty"
    empty.mkdir()
    (empty / "SHA256SUMS").write_text("")
    assert md.verify_and_unpack_pins(empty, tmp_path / "out3")   # boş liste "doğrulandı" DEĞİL
    assert (tmp_path / "out/ema/A-portfolio/ledger/x/trades.csv").read_bytes() == b"a,b\n1,2\n"
    (pins / "ema/A-portfolio/ledger/x/trades.csv.gz").write_bytes(gzip.compress(b"a,b\n1,3\n"))
    problems = md.verify_and_unpack_pins(pins, tmp_path / "out2")
    assert problems and "SHA256" in problems[0]
    assert not (tmp_path / "out2/ema/A-portfolio/ledger/x/trades.csv").exists()


# --------------------------------------------------------------------------- #
# (7) OLS ve hafta bootstrap'ı
# --------------------------------------------------------------------------- #
def test_ols_known_line():
    x = np.array([0.01, -0.02, 0.03, 0.0, 0.015])
    alpha, beta, r2 = md.ols(x, 0.001 + 2.0 * x)
    assert beta == pytest.approx(2.0) and alpha == pytest.approx(0.001) and r2 == pytest.approx(1.0)
    assert md.ols(np.zeros(5), x) is None


def _daily(days: int, seed: int = 1) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    index = pd.date_range(ts("2024-01-01"), periods=days, freq="D")
    x = rng.normal(0, 0.02, days)
    return pd.DataFrame({"r_model": 0.5 * x + rng.normal(0, 0.01, days), "r_btc": x,
                         "exposed": True}, index=index)


def test_m2_bootstrap_is_deterministic_and_gated_by_weeks():
    frame = _daily(120)
    counts = {"days_total": 120, "days_used": 120, "days_missing_model": 0, "days_missing_btc": 0, "days_exposed": 60}
    a = md.m2_regression(frame, counts, iterations=300, alpha=0.05, seed="s")
    b = md.m2_regression(frame, counts, iterations=300, alpha=0.05, seed="s")
    assert a["ci_evaluable"] and a["beta_ci"] == b["beta_ci"]
    assert a["beta_ci"][0] < a["beta"] < a["beta_ci"][1]
    assert a["exposure_share"] == 0.5
    short = md.m2_regression(frame.iloc[:60], counts, iterations=300, alpha=0.05, seed="s")   # 9 hafta
    assert short["weeks"] < 10 and not short["ci_evaluable"] and short["beta"] is not None


def test_iso_week_starts_monday():
    assert md.iso_week(ts("2026-09-22T12:45:00")) == ts("2026-09-21")   # Salı -> Pazartesi
    assert md.iso_week(ts("2026-09-28")) == ts("2026-09-28")


# --------------------------------------------------------------------------- #
# (8) M2 gün penceresi ve pozisyonsuz günler
# --------------------------------------------------------------------------- #
def _equity(days: int, start: str = "2024-01-01") -> pd.DataFrame:
    index = pd.date_range(ts(start), periods=days * 6, freq="4h")
    return pd.DataFrame({"equity": np.linspace(10000, 11000, len(index)),
                         "open_positions": [1 if i < 12 else 0 for i in range(len(index))]}, index=index)


def test_day_window_ends_at_cutoff_or_last_close():
    equity = _equity(200)
    source = md.Source(key="ema", period="A", root=Path("."), models=[], pairs=[], layer="ema", slippage=0.0,
                       day_start=ts("2024-01-01"), day_end=ts("2024-03-01"), last_close_extends=True)
    assert md.day_window(source, equity, [])[1] == ts("2024-03-01")
    late = [pos("X", "long", "2024-02-20", "2024-04-10T08:00:00")]
    assert md.day_window(source, equity, late)[1] == ts("2024-04-10")
    live = md.Source(key="live-base", period="live", root=Path("."), models=[], pairs=[], layer="base", slippage=0.0)
    assert md.day_window(live, equity, late) == (ts("2024-01-01"), equity.index[-1].floor("D"))


def test_flat_days_are_included_and_exposure_counted():
    equity = _equity(30)
    btc = pd.Series(np.linspace(100, 130, 40), index=pd.date_range(ts("2023-12-25"), periods=40, freq="D"))
    frame, counts = md.daily_frame(equity, btc, (ts("2024-01-02"), ts("2024-01-30")))
    assert counts["days_total"] == 29 and counts["days_used"] == 29
    assert counts["days_exposed"] == 1   # yalnızca 01-02 (ilk 12 satır 01-01..01-02)
    assert len(frame) == 29


# --------------------------------------------------------------------------- #
# (9) Olaylar ve ICC
# --------------------------------------------------------------------------- #
def test_events_are_connected_components():
    items = [pos("A", "long", "2024-01-01T00:00", "2024-01-01T12:00"),
             pos("B", "short", "2024-01-01T08:00", "2024-01-02T00:00"),
             pos("C", "long", "2024-01-01T20:00", "2024-01-02T04:00"),    # B üzerinden A'ya bağlı
             pos("D", "long", "2024-01-02T04:00", "2024-01-02T08:00")]    # C kapanırken açılır: örtüşmez
    sizes = sorted(len(e) for e in md.events(items))
    assert sizes == [1, 3]


def test_icc_known_values():
    assert md.icc_oneway([[1.0, 1.0], [-1.0, -1.0], [0.5, 0.5]]) == pytest.approx(1.0)
    assert md.icc_oneway([[1.0]]) is None
    low = md.icc_oneway([[1.0, -1.0], [1.0, -1.0], [1.0, -1.0]])
    assert low is not None and low < 0


def test_m4_pair_correlation_uses_overlap_window():
    x = candles("2024-01-01", 60, drift=0.01)
    y = candles("2024-01-01", 60, drift=0.01)
    book = md.PriceBook({("4H", "X"): x, ("4H", "Y"): y})
    items = [pos("X", "long", "2024-01-01T00:00", "2024-01-05T00:00"),
             pos("Y", "short", "2024-01-02T00:00", "2024-01-03T00:00")]
    out = md.m4_concurrency(items, book, "4H", pd.Timedelta(hours=4))
    assert out["overlap_pairs"] == 1 and out["pairs_used"] == 1 and out["same_direction_share"] == 0.0
    assert out["events"] == 1


# --------------------------------------------------------------------------- #
# (10) Hizalı pay farkı
# --------------------------------------------------------------------------- #
def test_aligned_share_diff_point_is_plain_share_difference():
    model, control = [], []
    for week in range(12):
        day = ts("2024-01-01") + pd.Timedelta(weeks=week)
        for k, ret in enumerate((0.02, 0.01, -0.01)):
            p = pos("A", "long", str((day + pd.Timedelta(hours=k)).tz_convert(None)),
                    str((day + pd.Timedelta(hours=k + 1)).tz_convert(None)))
            p.btc_ret = ret
            model.append(p)
        c = pos("A", "long", str(day.tz_convert(None)), str((day + pd.Timedelta(hours=1)).tz_convert(None)))
        c.btc_ret = -0.01 if week % 2 else 0.01
        control.append(c)
    out = md.aligned_share_diff(model, control, "btc", iterations=200, alpha=0.05, seed="x")
    assert out["diff"] == pytest.approx(2 / 3 - 0.5)
    assert out["ci_evaluable"] and out["ci"][0] <= out["diff"] <= out["ci"][1]


# --------------------------------------------------------------------------- #
# (11) İmport yasağı; çıktıda kapı/etiket yok
# --------------------------------------------------------------------------- #
def test_does_not_import_strategies_or_writers():
    tree = ast.parse(SOURCE)
    modules = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
        elif isinstance(node, ast.Import):
            modules.update(a.name for a in node.names)
    assert not any(m.startswith("strategies") for m in modules)
    assert "core.portfolio" not in modules and "core.engine" not in modules
    assert "append_trades" not in SOURCE and "write_state" not in SOURCE


def test_output_carries_no_gate_or_label():
    for token in ('"passed_edge"', "LABEL_MODEL", "MODELDEN", "PİYASADAN\"", '"verdict"'):
        assert token not in SOURCE


# --------------------------------------------------------------------------- #
# (12) Uçtan uca (sentetik defter, sahte mum çekici)
# --------------------------------------------------------------------------- #
def _ledger(root: Path, model: str, trades: list[dict], equity: pd.DataFrame) -> None:
    directory = root / model
    directory.mkdir(parents=True)
    lines = [",".join(TRADE_COLUMNS)]
    for t in trades:
        lines.append(",".join(str(t.get(c, "")) for c in TRADE_COLUMNS))
    (directory / "trades.csv").write_text("\n".join(lines) + "\n")
    eq_lines = [",".join(EQUITY_COLUMNS)]
    for stamp, row in equity.iterrows():
        eq_lines.append(f"{stamp.isoformat()},{row['equity']},0,0,{row['equity']},{int(row['open_positions'])}")
    (directory / "equity.csv").write_text("\n".join(eq_lines) + "\n")


def test_end_to_end_live_source(tmp_path, monkeypatch):
    frames = {"BTC-USDT-SWAP": candles("2024-01-01", 24 * 4 * 30, freq="15min", drift=0.0001),
              "ETH-USDT-SWAP": candles("2024-01-01", 24 * 4 * 30, freq="15min", drift=-0.0001)}
    frames4h = {"BTC-USDT-SWAP": candles("2021-11-01", 6 * 1100, drift=0.0002)}

    def fake_fetch(config, symbol, now=None):
        bar = config["timeframe"]
        return frames[symbol] if bar == "15m" else frames4h[symbol]

    monkeypatch.setattr(md, "fetch_ohlcv", fake_fetch)
    live = tmp_path / "ledgers_scalp"
    eth = frames["ETH-USDT-SWAP"]
    trades = []
    for i in range(6):
        opened = eth.index[10 + i * 200]
        closed = eth.index[60 + i * 200]
        entry = float(eth.loc[opened, "open"]) * (1 - 0.0005)
        trades.append({"strategy": "scalp_patient", "symbol": "ETH-USDT-SWAP", "direction": "short",
                       "opened_at": opened.isoformat(), "closed_at": closed.isoformat(),
                       "entry_price": entry, "exit_price": entry * 0.99, "qty": 1, "notional": entry,
                       "stop_price": entry * 1.05, "risk_amount": 100, "pnl": 10 if i % 2 else -5,
                       "exit_reason": "signal", "signal_reason": "x | arm=rsi2_reversal"})
    equity = pd.DataFrame({"equity": 10000.0, "open_positions": 0},
                          index=pd.date_range(eth.index[0], eth.index[-1], freq="15min"))
    _ledger(live, "scalp_patient", trades, equity)
    _ledger(live, "scalp_coinflip", trades[:3], equity)
    empty_pins = tmp_path / "pins"   # yok -> pins kapısı düşer, backtest kaynakları kurulmaz
    out = tmp_path / "out"
    code = md.main(["--stage", "measure", "--pins-dir", str(empty_pins), "--xsec-dir", str(tmp_path / "noxsec"),
                    "--live-base", str(tmp_path / "none"), "--live-scalp", str(live),
                    "--now", "2024-01-31T00:00:00Z", "--out-dir", str(out), "--live-commit", "abc"])
    payload = json.loads((out / "market_direction.json").read_text())
    scalp = payload["results"]["live-scalp|live"]
    assert scalp["measured"] and scalp["price_gate"]["passed"]
    m1 = scalp["models"]["scalp_patient"]["m1"]["btc"]["all"]
    assert m1["aligned"] + m1["misaligned"] + m1["neutral"] + m1["unmeasured"] == 6
    assert scalp["models"]["scalp_patient"]["m2"]["ci_evaluable"] is False   # < 10 hafta
    assert scalp["pairs"][0]["control"] == "scalp_coinflip"
    assert (out / "market_direction_trades.csv").read_text().count("\n") == 1 + 6 + 3
    assert (out / "market_direction_prices.csv").stat().st_size > 0
    # xsec ölçülmedi (determinizm kapısı: yeniden üretim yok) -> çıkış 3, ama canlı rapor YAZILDI
    assert code == 3 and payload["inputs"]["xsec"]["measured"] is False

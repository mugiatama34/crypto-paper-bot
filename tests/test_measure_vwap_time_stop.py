"""scripts/measure_vwap_time_stop.py (docs/backtest.md > 6x): eşleştirme motorla mı kuruluyor.

Testler SONUCU değil aleti ölçer: yeniden oynatma portföy koşusunu birebir üretiyor mu
(parite), zaman stop'u görmeyen pozisyonda iki kol aynı mı (sağlama), ön-kontrol R taşıyor
mu, B yalnızca A'da iki kapı geçince mi koşuyor, rapor sırası sabit mi.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pytest

from core.engine import Engine
from core.ledger import Ledger
from core.portfolio import Portfolio
from scripts import measure_vwap_time_stop as m
from scripts.diagnose_ema_exits import position_paths
from scripts.vault import KASA_START
from strategies.base import MarketData

SYMBOLS = ("AAA-USDT-SWAP", "BBB-USDT-SWAP", "CCC-USDT-SWAP")
START = pd.Timestamp("2025-01-01", tz="UTC")


def _walk(seed: int, n: int) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    index = pd.date_range(START, periods=n, freq="15min", tz="UTC", name="ts")
    close = 100.0 * np.exp(np.cumsum(rng.normal(0.0, 0.004, n)))
    open_ = np.r_[close[0], close[:-1]]
    high = np.maximum(open_, close) * (1.0 + np.abs(rng.normal(0.0, 0.002, n)))
    low = np.minimum(open_, close) * (1.0 - np.abs(rng.normal(0.0, 0.002, n)))
    volume = rng.uniform(50.0, 150.0, n)
    return pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close, "volume": volume}, index=index
    )


@pytest.fixture(scope="module")
def portfolio_run() -> dict[str, Any]:
    """Gerçek `vwap_managed` ile sentetik bir portföy koşusu ve onun yeniden oynatması."""
    n = 2500
    market = MarketData(
        ohlcv={s: _walk(i + 1, n) for i, s in enumerate(SYMBOLS)},
        btc=_walk(99, n),
        funding={},
        as_of=_walk(99, n).index[-1],
    )
    config = m.layer_config()
    config["signals_per_bar"] = True
    tmp = Path(tempfile.mkdtemp())
    ledger = Ledger(tmp / "portfolio")
    strategy = m.build_model(m.with_time_stop(config, m.BASE_BARS))
    state = ledger.initialize_model(strategy.name, initial_capital=float(config["initial_capital"]))
    state["last_processed_bar"] = market.btc.index[300].isoformat()
    ledger.write_state(strategy.name, state)
    Engine([strategy], config=config, ledger=ledger, portfolio=Portfolio(config)).run_round(market)
    # Gerçek koşuda sinyal kesimi + kuyruk her pozisyona 32 barı garanti eder; sentetik
    # serinin sonuna yakın açılanlar aynı garantiyi taşımaz.
    horizon = market.as_of - (m.REPLAY_TAIL_BARS + 2) * pd.Timedelta("15min")
    positions = [
        p for p in m.portfolio_positions(ledger.read_trades(strategy.name)) if p.opened_at < horizon
    ]
    pairs, unreplayable, mismatches, sanity = m.pair_positions(
        positions, market=market, config=config, workdir=tmp / "replay"
    )
    return {
        "market": market, "positions": positions, "pairs": pairs,
        "unreplayable": unreplayable, "mismatches": mismatches, "sanity": sanity,
    }


# --------------------------------------------------------------------------- #
# Parite ve sağlama — yöntemin kendisi
# --------------------------------------------------------------------------- #
def test_replay_reproduces_the_portfolio_run_exactly(portfolio_run: dict[str, Any]) -> None:
    """16 barlık yeniden oynatma portföy koşusunun R'sini BİREBİR üretir (§6x > 2)."""
    assert len(portfolio_run["positions"]) >= 10, "sentetik koşu yeterli pozisyon üretmedi"
    assert portfolio_run["mismatches"] == []
    assert portfolio_run["unreplayable"] == []
    for pair in portfolio_run["pairs"]:
        assert m.parity_ok(pair.position, pair.base)
        assert str(pair.base.merged["closed_at"]) == str(pair.position.row["closed_at"])


def test_positions_without_a_time_stop_are_identical_in_both_arms(portfolio_run: dict[str, Any]) -> None:
    """Sağlama: 16'lık kolda zaman stop'u görmeyen her pozisyonda ΔR TAM sıfır."""
    assert portfolio_run["sanity"] == []
    closed_early = [p for p in portfolio_run["pairs"] if not p.open_at_16]
    assert closed_early, "sentetik koşuda zaman stop'undan önce kapanan pozisyon yok"
    assert all(p.delta == 0.0 for p in closed_early)


def test_the_variant_really_holds_longer(portfolio_run: dict[str, Any]) -> None:
    """Varyant kolunda zaman stop'u 32. barda; 16'lık kolda zaman stop'una düşenler uzar."""
    opened = [p for p in portfolio_run["pairs"] if p.open_at_16]
    assert opened
    step = pd.Timedelta("15min")
    for pair in opened:
        base_close = pd.Timestamp(pair.base.merged["closed_at"])
        variant_close = pd.Timestamp(pair.variant.merged["closed_at"])
        assert base_close == pair.position.opened_at + (m.BASE_BARS + 1) * step
        assert variant_close >= base_close  # 17. barın içinde stop: aynı damga
        assert variant_close <= pair.position.opened_at + (m.VARIANT_BARS + 1) * step


def test_missing_data_makes_a_position_unreplayable_not_a_mismatch(
    portfolio_run: dict[str, Any], tmp_path: Path
) -> None:
    """Veri eksik pozisyon OYNATILAMAZ: iki koldan çıkarılır, uyuşmazlık sayılmaz."""
    position = portfolio_run["positions"][0]
    market = portfolio_run["market"]
    broken = MarketData(
        ohlcv={s: f for s, f in market.ohlcv.items() if s != position.symbol} | {
            position.symbol: market.ohlcv[position.symbol].iloc[:0]
        },
        btc=market.btc, funding={}, as_of=market.as_of,
    )
    pairs, unreplayable, mismatches, _ = m.pair_positions(
        [position], market=broken, config=m.layer_config(), workdir=tmp_path
    )
    assert pairs == [] and mismatches == []
    assert len(unreplayable) == 1 and "eksik" in unreplayable[0]["reason"]


def test_a_single_gap_bar_inside_the_variant_window_is_unreplayable(
    portfolio_run: dict[str, Any], tmp_path: Path
) -> None:
    """Varyantın penceresindeki tek bir eksik bar da oynatmayı geçersiz kılar."""
    position = portfolio_run["positions"][0]
    market = portfolio_run["market"]
    frame = market.ohlcv[position.symbol]
    hole = position.opened_at + 25 * pd.Timedelta("15min")
    gapped = MarketData(
        ohlcv={**market.ohlcv, position.symbol: frame.drop(index=hole)},
        btc=market.btc, funding={}, as_of=market.as_of,
    )
    gap = m.replay_data_gap(position, market=gapped, step=pd.Timedelta("15min"))
    assert gap is not None and hole.isoformat() in gap


def _run_with(n: int, *, unreplayable: int = 0, mismatches: int = 0) -> m.PeriodRun:
    run = m.PeriodRun(period=m.periods()["A"], positions=[object()] * n)  # type: ignore[list-item]
    run.unreplayable = [{}] * unreplayable
    run.mismatches = [{}] * mismatches
    return run


def test_unreplayable_is_tolerated_up_to_one_percent() -> None:
    assert _run_with(1000, unreplayable=10).gate_failures == []
    assert _run_with(1000, unreplayable=11).gate_failures


def test_a_single_parity_mismatch_stops_the_measurement() -> None:
    failures = _run_with(1000, mismatches=1).gate_failures
    assert failures and "uyuşmazlığı" in failures[0]


def test_only_the_time_stop_key_changes() -> None:
    config = m.layer_config()
    variant = m.with_time_stop(config, m.VARIANT_BARS)
    assert variant["scalp"]["time_stop_bars"] == 32
    assert config["scalp"]["time_stop_bars"] == 16
    variant["scalp"].pop("time_stop_bars")
    base = dict(config["scalp"])
    base.pop("time_stop_bars")
    assert variant["scalp"] == base
    assert {k: v for k, v in variant.items() if k != "scalp"} == {
        k: v for k, v in config.items() if k != "scalp"
    }
    assert m.build_model(m.with_time_stop(config, 32))._time_stop.bars == 32


# --------------------------------------------------------------------------- #
# Dönemler ve kasa
# --------------------------------------------------------------------------- #
def test_periods_end_before_the_seen_window_and_the_vault() -> None:
    periods = m.periods()
    assert periods["A"].start == pd.Timestamp("2022-01-01T00:00:00+00:00")
    assert periods["A"].cutoff == pd.Timestamp("2024-06-30T00:00:00+00:00")
    assert periods["B"].start == pd.Timestamp("2024-07-01T00:00:00+00:00")
    assert periods["B"].cutoff == pd.Timestamp("2026-07-19T00:00:00+00:00")
    assert periods["B"].end == periods["B"].cutoff + pd.Timedelta("12h")
    assert periods["B"].end < KASA_START


# --------------------------------------------------------------------------- #
# İstatistik
# --------------------------------------------------------------------------- #
def _items(values: list[float], days: int) -> list[tuple[pd.Timestamp, float]]:
    return [(START + pd.Timedelta(days=i % days), v) for i, v in enumerate(values)]


def test_cluster_ids() -> None:
    stamp = pd.Timestamp("2024-12-30T10:00:00Z")  # ISO yılı 2025'in 1. haftası
    assert m.cluster_id(stamp, "day") == "2024-12-30"
    assert m.cluster_id(stamp, "week") == "2025-W01"


def test_gate_passes_only_with_positive_mean_and_positive_binding_low() -> None:
    clear = m.gate_stats(_items([0.5 + 0.01 * (i % 7) for i in range(400)], 100), seed="t")
    assert clear["passed"] and clear["binding_low"] > 0.0
    assert clear["binding_low"] == min(
        clear["by_definition"][d]["low"] for d in m.CLUSTER_DEFINITIONS
    )
    noisy = m.gate_stats(_items([(-1.0) ** i for i in range(400)], 100), seed="t")
    assert not noisy["passed"]


def test_too_few_clusters_is_not_evaluable() -> None:
    few = m.gate_stats(_items([1.0] * 50, 5), seed="t")
    assert few["evaluable"] is False and few["passed"] is False


# --------------------------------------------------------------------------- #
# Ön-kontrol R taşımaz; B yalnızca A'da iki kapı geçince
# --------------------------------------------------------------------------- #
def test_precheck_carries_no_outcome(portfolio_run: dict[str, Any]) -> None:
    run = m.PeriodRun(
        period=m.periods()["A"],
        positions=portfolio_run["positions"],
        pairs=portfolio_run["pairs"],
    )
    payload = m.precheck_payload(run)
    flat = repr(payload).lower()
    for forbidden in ("avg_r", "mean", "delta", "mfe", "exit_rules", "'r'", "r16", "r32"):
        assert forbidden not in flat, forbidden


def _fake_run(name: str, passed: bool) -> tuple[m.PeriodRun, MarketData]:
    run = m.PeriodRun(period=m.periods()[name], positions=[object()], pairs=[])  # type: ignore[list-item]
    run._passed = passed  # type: ignore[attr-defined]
    return run, None  # type: ignore[return-value]


def test_measure_stage_runs_only_period_a(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """`measure` yalnızca A'yı koşar (TADİLAT-1 > 9); B'nin kararını YÜKE yazar, B'yi koşmaz."""
    called: list[str] = []

    def fake_run_period(period: m.Period, **_: Any) -> tuple[m.PeriodRun, MarketData]:
        called.append(period.name)
        return _fake_run(period.name, True)

    monkeypatch.setattr(m, "run_period", fake_run_period)
    monkeypatch.setattr(m, "precheck_payload", lambda run: {"period": run.period.name})
    monkeypatch.setattr(m, "gates", lambda run, seed: _gates(True, True))
    monkeypatch.setattr(m, "descriptive", lambda run: {})
    monkeypatch.setattr(m, "mfe_diagnosis", lambda run, market, step: {})
    code, payload = m.measure_a(tmp_path)
    assert code == m.EXIT_OK and called == ["A"]
    assert "B" not in payload["periods"]
    assert payload["b_decision"]["eligible"] is True


def _gates(c1: bool, delta: bool) -> dict[str, Any]:
    return {"c1": {"passed": c1}, "delta": {"passed": delta}, "passed": c1 and delta}


def _pin(tmp_path: Path, payload: dict[str, Any]) -> tuple[Path, Path]:
    pinned = tmp_path / "vwap_time_stop_a.json"
    pinned.write_text(json.dumps(payload), encoding="utf-8")
    sha = tmp_path / "vwap_time_stop_a.json.sha256"
    sha.write_text(f"{m.sha256_of(pinned)}  {pinned.name}\n", encoding="utf-8")
    return pinned, sha


@pytest.mark.parametrize(
    ("c1", "delta", "runs"),
    [(True, True, True), (True, False, False), (False, True, False), (False, False, False)],
)
def test_measure_b_runs_only_when_both_gates_passed_in_pinned_a(
    tmp_path: Path, c1: bool, delta: bool, runs: bool
) -> None:
    """Karar programatik: ΔR ∧ C-1 A'da birlikte geçmediyse B'nin koşucusu HİÇ çağrılmaz."""
    pinned, sha = _pin(tmp_path, {"stage": "measure", "periods": {"A": {"gates": _gates(c1, delta)}}})
    called: list[Path] = []

    def run_b(out_dir: Path) -> tuple[int, dict[str, Any]]:
        called.append(out_dir)
        return m.EXIT_OK, {"gates": {}}

    code, payload = m.measure_b(tmp_path, pinned=pinned, sha_file=sha, run_b=run_b)
    assert code == m.EXIT_OK
    assert bool(called) is runs
    assert payload["a_source"]["sha256"] == m.sha256_of(pinned)
    if not runs:
        assert payload["periods"]["B"]["skipped"].startswith("B koşulmadı")


def test_measure_b_refuses_when_the_pinned_hash_does_not_match(tmp_path: Path) -> None:
    pinned, sha = _pin(tmp_path, {"stage": "measure", "periods": {"A": {"gates": _gates(True, True)}}})
    pinned.write_text(pinned.read_text(encoding="utf-8") + " ", encoding="utf-8")  # kurcalanmış

    def run_b(out_dir: Path) -> tuple[int, dict[str, Any]]:
        raise AssertionError("hash tutmazken B koşmamalı")

    code, payload = m.measure_b(tmp_path, pinned=pinned, sha_file=sha, run_b=run_b)
    assert code == m.EXIT_GATE and "hash" in payload["periods"]["B"]["error"]


def test_measure_b_does_not_trust_an_incomplete_a_payload(tmp_path: Path) -> None:
    """Kapı alanı olmayan (hata vermiş) bir A yükü "geçti" sayılmaz."""
    pinned, sha = _pin(tmp_path, {"stage": "measure", "periods": {"A": {"error": "x"}}})
    code, payload = m.measure_b(tmp_path, pinned=pinned, sha_file=sha, run_b=lambda d: (0, {}))
    assert code == m.EXIT_OK and "skipped" in payload["periods"]["B"]


def test_report_sections_follow_the_preregistered_order() -> None:
    gate = {
        "n": 40, "mean": 0.1, "binding_low": -0.1, "evaluable": True, "passed": False, "mde": 0.2,
        "by_definition": {d: {"clusters": 20, "low": -0.1, "high": 0.3, "p_one_sided": 0.2}
                          for d in m.CLUSTER_DEFINITIONS},
    }
    payload = {
        "stage": "measure",
        "periods": {
            "A": {
                "precheck": {"positions": 40},
                "gates": {
                    "c1": gate, "delta": gate, "base_r16": {**gate, "passed": None},
                    "open_at_16": {"n": 10, "share": 0.25, "conditional_delta_mean": 0.4,
                                   "identity_residual": 0.0},
                    "passed": False,
                },
                "descriptive": {
                    "by_direction": {}, "by_year": {}, "by_symbol": {},
                    "gross": {"mean_gross_r16": 0.0, "mean_gross_r32": 0.0, "mean_gross_delta": 0.0},
                    "exit_rules": {"16": {}, "32": {}},
                    "open_at_16_subset": {"n": 10, "resolved_17_to_33": 5, "time_stop_at_32": 5,
                                          "mean_r16": 0.0, "mean_r32": 0.0, "variant_exit_rules": {}},
                    "ambiguous_stop_exits": {},
                },
                "mfe": {},
                "portfolio": {},
            },
            "B": {"skipped": "A'da ΔR kapısı ve C-1 birlikte geçmedi"},
        },
    }
    text = m.format_report(payload)
    order = ["## 1. Ön-kontrol (16 bar birebir)", "## 2. C-1 — A", "## 3. ΔR — A",
             "16. barda hâlâ açık", "## 4. Çıkış sebepleri", "## 5. MFE", "## 6. Portföy etkisi",
             "## 7. Dönem B"]
    positions = [text.index(marker) for marker in order]
    assert positions == sorted(positions)
    assert "B verisi çekilmedi" in text


# --------------------------------------------------------------------------- #
# Yol istatistiği yöne çevrilir (long değişmez)
# --------------------------------------------------------------------------- #
def _path_row(direction: str) -> dict[str, Any]:
    return {
        "symbol": "X", "direction": direction, "opened_at": "2025-01-01T00:00:00+00:00",
        "closed_at": "2025-01-01T00:30:00+00:00", "entry_price": 100.0,
        "stop_price": 98.0 if direction == "long" else 102.0, "exit_price": 101.0,
        "pnl": 1.0, "risk_amount": 2.0, "exit_reason": "tp",
    }


def test_position_paths_measure_excursions_in_the_trade_direction() -> None:
    index = pd.date_range(START, periods=6, freq="15min", tz="UTC")
    frame = pd.DataFrame(
        {"open": 100.0, "high": [101, 104, 103, 102, 102, 102], "low": [99, 97, 98, 99, 99, 99],
         "close": 100.0},
        index=index,
    )
    long_path = position_paths([_path_row("long")], {"X": frame}, horizons=(1,))[0]
    short_path = position_paths([_path_row("short")], {"X": frame}, horizons=(1,))[0]
    assert long_path["mfe_r"] == pytest.approx(2.0)      # (104 − 100) / 2
    assert long_path["mae_r"] == pytest.approx(-1.5)     # (97 − 100) / 2
    assert short_path["mfe_r"] == pytest.approx(1.5)     # (100 − 97) / 2
    assert short_path["mae_r"] == pytest.approx(-2.0)    # (100 − 104) / 2
    assert long_path["mfe_r_prior"] == pytest.approx(2.0)
    assert short_path["mfe_r_prior"] == pytest.approx(1.5)

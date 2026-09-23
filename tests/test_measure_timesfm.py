"""`scripts/measure_timesfm.py`nin sözleşmeleri (docs/backtest.md > 6k): ağ yok, TimesFM yok.

Sınanan şey ön-kaydın mekanik kurallarıdır — bir sayının iyi çıkması değil:

1. **Körlük** — hemstir'den yalnızca `docs/forecasts.json` okunur; sonuç dosyalarının adı
   betikte hiç geçmez ve ölçüm/strateji modülleri import edilmez.
2. **Parite** — P0 toleransı, `>=` yön kuralı, istisnanın %5 TAVANI; P1'de son bar HARİÇ.
3. **Izgara ve dönemler** — faz 00:00Z, B = A + 48s, `C_start` ileri kayar geri kaymaz.
4. **Gözlem** — eksik veri DÜŞER ve SAYILIR; sıfır hareket dört kuraldan birden düşer.
5. **Kapı** — üç kurala karşı AYRI AYRI; < 10 küme = GEÇMEDİ; A düşerse B/C'nin verisi
   HİÇ çekilmez.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

import scripts.measure_timesfm as mt
from scripts.measure_timesfm import (
    CommittedSeries,
    Observation,
    coin_up,
    collect,
    confidence_band,
    evaluate_period,
    grid_anchors,
    is_up,
    p0_series_check,
    p0_verdict,
    p1_check,
    read_hemstir,
    universe_of,
)

SCRIPT = Path("scripts/measure_timesfm.py")


# --------------------------------------------------------------------------- #
# 1. Körlük ve ayrım
# --------------------------------------------------------------------------- #
def test_script_never_names_hemstir_result_files():
    text = SCRIPT.read_text(encoding="utf-8")
    for banned in (
        "evaluation.json", "backtest.json", "research_dev", "research_locked",
        "locked_test_run", "diagnostics.json", "README", "docs/history",
    ):
        assert banned not in text, banned
    assert mt.FORECASTS_PATH == "docs/forecasts.json"


def test_module_does_not_import_measurement_or_strategy_modules():
    text = SCRIPT.read_text(encoding="utf-8")
    for banned in ("core.portfolio", "core.metrics", "core.ledger", "strategies."):
        assert f"import {banned}" not in text and f"from {banned}" not in text


def test_timesfm_is_imported_lazily():
    """Bağımlılık yalnızca workflow'da: modül timesfm olmadan ithal edilebilmeli."""
    text = SCRIPT.read_text(encoding="utf-8")
    assert "\nimport timesfm" not in text and "\nfrom timesfm" not in text


def _commit(repo: Path, files: dict[str, object], message: str) -> None:
    for name, content in files.items():
        path = repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(content))
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", message],
        check=True,
    )


def _forecasts(coins: dict[str, str]) -> dict:
    return {
        "generated_at": "2026-09-13T00:10:00Z",
        "coins": {
            coin: {
                "inst_id": inst,
                "history": [{"ts": f"2026-09-{1 + i // 24:02d}T{i % 24:02d}:00:00Z", "close": 100.0 + i} for i in range(3)],
                "forecast": [{"ts": "x", "value": 101.0, "lower": 99.0, "upper": 103.0}],
                "signal": {"side": "BUY"},
            }
            for coin, inst in coins.items()
        },
    }


def test_reader_reads_only_forecasts_history_up_to_the_pin(tmp_path):
    repo = tmp_path / "hemstir"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    _commit(repo, {"docs/forecasts.json": _forecasts({"BTC": "BTC-USDT"})}, "1")
    _commit(repo, {"docs/evaluation.json": {"hit_rate": 0.99}}, "sonuç")
    _commit(repo, {"docs/forecasts.json": _forecasts({"BTC": "BTC-USDT-SWAP", "ETH": "ETH-USDT-SWAP"})}, "2")
    pin = subprocess.check_output(["git", "-C", str(repo), "rev-parse", "HEAD"], text=True).strip()
    _commit(repo, {"docs/forecasts.json": _forecasts({"SOL": "SOL-USDT-SWAP"})}, "pin sonrası")

    snapshots = read_hemstir(repo, pin)

    assert [len(s.series) for s in snapshots] == [1, 2]
    assert [s.coin for s in snapshots[1].series] == ["BTC", "ETH"]  # sıra korunur


def test_preregistered_counts_are_pinned():
    assert (mt.EXPECTED_COMMITS, mt.EXPECTED_SERIES) == (54, 713)
    assert mt.HEMSTIR_PIN.startswith("f6ae0f4")


def test_universe_is_the_eleven_symbol_intersection():
    from core.config import load_config
    from core.layers import resolve_layer

    universe = universe_of(resolve_layer(load_config(), "ema").symbols)
    assert sorted(universe) == sorted(
        f"{c}-USDT-SWAP" for c in ("BTC", "ETH", "SOL", "XRP", "DOGE", "BNB", "AVAX", "LINK", "ADA", "NEAR", "ETHFI")
    )


def test_inference_config_mirrors_hemstir():
    assert mt.CHECKPOINT == "google/timesfm-2.5-200m-pytorch"
    assert mt.TIMESFM_PIN == "2.0.2"
    assert dict(mt.FORECAST_CONFIG) == {
        "max_context": 300, "max_horizon": 48, "normalize_inputs": True,
        "use_continuous_quantile_head": True, "force_flip_invariance": True,
        "infer_is_positive": True, "fix_quantile_crossing": True,
    }


# --------------------------------------------------------------------------- #
# 2. Parite
# --------------------------------------------------------------------------- #
def test_direction_rule_is_greater_or_equal():
    assert is_up(100.0, 100.0) and is_up(100.1, 100.0) and not is_up(99.9, 100.0)


def test_band_skips_the_mean_column():
    assert confidence_band([50.0, 1.0, 3.0, 2.0]) == (1.0, 3.0)


def _series(*, entry=100.0, end=101.0, side="BUY") -> CommittedSeries:
    value = np.full(48, end)
    return CommittedSeries(
        commit="abc1234", generated_at="g", coin="BTC", inst_id="BTC-USDT-SWAP",
        history_ts=tuple(pd.date_range("2026-09-01", periods=300, freq="1h", tz="UTC")),
        closes=np.full(300, entry), value=value, lower=value - 1, upper=value + 1, side=side,
    )


def _quantiles(values: np.ndarray) -> np.ndarray:
    return np.stack([np.stack([v, v - 1, v, v + 1]) for v in values])


def test_p0_accepts_reproduction_within_tolerance_and_rejects_beyond():
    s = _series()
    close = s.value * (1 + 5e-5)
    far = s.value * (1 + 5e-4)
    assert p0_series_check(s, close, _quantiles(close))["passed"]
    assert not p0_series_check(s, far, _quantiles(far))["passed"]


def test_p0_side_flip_only_excused_for_tiny_moves():
    big = _series(end=101.0, side="SELL")  # hareket %1, yön tutmuyor
    assert not p0_series_check(big, big.value, _quantiles(big.value))["passed"]
    tiny = _series(end=100.005, side="SELL")  # %0.005 < 1e-4
    check = p0_series_check(tiny, tiny.value, _quantiles(tiny.value))
    assert check["exception_eligible"] and check["passed"]


def test_p0_cap_counts_only_excused_series_not_all_eligible():
    """Tavan DAR: yalnızca istisna SAYESİNDE geçenler (yön ters + küçük hareket) sayılır."""
    normal = _series()
    ok = p0_series_check(normal, normal.value, _quantiles(normal.value))
    agree = _series(end=100.005, side="BUY")    # uygun ama yön tutuyor: affa ihtiyaç yok
    flip = _series(end=100.005, side="SELL")    # uygun VE yön ters: affedilen
    agreeing = p0_series_check(agree, agree.value, _quantiles(agree.value))
    excused = p0_series_check(flip, flip.value, _quantiles(flip.value))

    many_agreeing = p0_verdict([ok] * 50 + [agreeing] * 50)
    assert many_agreeing["passed"] and many_agreeing["exception_eligible"] == 50
    assert many_agreeing["exception_excused_side_mismatch"] == 0

    assert p0_verdict([ok] * 95 + [excused] * 5)["passed"]
    assert not p0_verdict([ok] * 94 + [excused] * 6)["passed"]


def test_p0_reports_failures_by_commit_with_timestamp():
    s = _series()
    far = s.value * (1 + 5e-4)
    verdict = p0_verdict([
        p0_series_check(s, s.value, _quantiles(s.value)),
        p0_series_check(s, far, _quantiles(far)),
    ])
    assert verdict["by_commit"] == {"abc1234": {"generated_at": "g", "series": 2, "failed": 1}}


def test_p0_of_nothing_is_not_a_pass():
    assert not p0_verdict([])["passed"]


def test_p1_excludes_the_unclosed_last_bar():
    s = _series()
    ours = pd.Series(100.0, index=list(s.history_ts))
    ours.iloc[-1] = 555.0  # oluşmakta olan barın farkı P1'i düşürmez
    assert p1_check([s], {"BTC-USDT-SWAP": ours})["passed"]
    ours.iloc[-2] = 100.00000002
    assert not p1_check([s], {"BTC-USDT-SWAP": ours})["passed"]
    assert not p1_check([s], {"BTC-USDT-SWAP": ours.drop(ours.index[5])})["passed"]


def test_p1_candidates_skip_spot_and_out_of_universe():
    spot = CommittedSeries(**{**_series().__dict__, "inst_id": "BTC-USDT"})
    dot = CommittedSeries(**{**_series().__dict__, "inst_id": "DOT-USDT-SWAP"})
    snap = mt.Snapshot(commit="c", series=(spot, dot, _series()))
    assert [s.inst_id for s in mt.p1_candidates([snap], ["BTC-USDT-SWAP"])] == ["BTC-USDT-SWAP"]


# --------------------------------------------------------------------------- #
# 3. Izgara ve dönemler
# --------------------------------------------------------------------------- #
def test_grid_is_fixed_to_midnight_every_48_hours():
    anchors = grid_anchors(pd.Timestamp("2022-01-02T05:00Z"), pd.Timestamp("2022-01-09T00:00Z"))
    assert [a.isoformat() for a in anchors] == [
        "2022-01-03T00:00:00+00:00", "2022-01-05T00:00:00+00:00",
        "2022-01-07T00:00:00+00:00", "2022-01-09T00:00:00+00:00",
    ]


def test_period_bounds_are_bounds_not_anchors():
    """Izgara 00:00Z'den 48 saatte bir; ön-kayıtlı sınırlar ızgaranın üstüne düşmeyebilir.

    2022-01-01 → 2024-06-30 911 gündür (tek): A'nın son çapası 06-29 (hedefi 07-01),
    B'nin sınırı A kesimi + 48s = 07-02, ilk çapası 07-03 — hedefler ÇAKIŞMAZ.
    """
    a_end = pd.Timestamp(mt.PERIOD_A_CUTOFF)
    a = grid_anchors(pd.Timestamp(mt.PERIOD_A_START), a_end)
    b = grid_anchors(a_end + mt.HORIZON_DELTA, pd.Timestamp("2024-07-10T00:00Z"))
    assert len(a) == 456
    assert a[-1] == pd.Timestamp("2024-06-29T00:00Z")
    assert b[0] == pd.Timestamp("2024-07-03T00:00Z")
    assert a[-1] + mt.HORIZON_DELTA < b[0]


def test_c_start_moves_later_never_earlier():
    assert mt.c_start(pd.Timestamp("2025-01-01T00:00Z")) == mt.C_START_OFFICIAL
    later = pd.Timestamp("2025-10-03T12:00Z")
    assert mt.c_start(later) == later
    assert grid_anchors(mt.c_start(None), pd.Timestamp("2025-09-20T00:00Z"))[0].isoformat() == "2025-09-16T00:00:00+00:00"


def test_matured_anchor_needs_the_target_bar_closed():
    now = pd.Timestamp("2026-09-23T07:00Z")
    assert mt.last_matured_anchor(now) == pd.Timestamp("2026-09-21T06:00Z")


# --------------------------------------------------------------------------- #
# 4. Gözlemler ve kurallar
# --------------------------------------------------------------------------- #
def _hourly(start: str, values) -> pd.Series:
    index = pd.date_range(start, periods=len(values), freq="1h", tz="UTC")
    return pd.Series(np.asarray(values, dtype="float64"), index=index)


def test_collect_drops_and_counts_instead_of_filling():
    anchor = pd.Timestamp("2022-01-20T00:00Z")
    start = anchor - 400 * mt.HOUR
    full = _hourly(start.isoformat(), np.linspace(100, 200, 500))
    gap = full.drop(anchor - 10 * mt.HOUR)
    late = full[full.index > anchor - 100 * mt.HOUR]
    flat = full.copy()
    flat[anchor + mt.HORIZON_DELTA] = flat[anchor]

    out = collect([anchor], {"A": full, "B": gap, "C": late, "D": flat})

    assert [p.symbol for p in out.pending] == ["A"]
    assert out.dropped == {"eksik_bar": 1, "listelenmemis": 1, "sifir_hareket": 1}
    p = out.pending[0]
    assert len(p.context) == 300 and p.last_close == full[anchor]
    assert p.target_close == full[anchor + mt.HORIZON_DELTA]
    assert p.momentum_up == (full[anchor] >= full[anchor - mt.HORIZON_DELTA])


def test_coin_is_deterministic_and_forks_per_symbol_and_anchor():
    t = pd.Timestamp("2022-01-01T00:00Z")
    assert coin_up(7, t, "BTC") == coin_up(7, t, "BTC")
    draws = [coin_up(7, t + k * mt.HORIZON_DELTA, s) for k in range(200) for s in ("BTC", "ETH")]
    assert 0.4 < np.mean(draws) < 0.6


class _Fake:
    def __init__(self, bias: float) -> None:
        self.bias = bias
        self.calls = 0

    def forecast(self, inputs, *, batch_size):
        self.calls += 1
        points = np.stack([np.full(48, x[-1] * (1 + self.bias)) for x in inputs])
        return points, None


def test_predict_uses_last_horizon_point_against_last_close():
    anchor = pd.Timestamp("2022-01-20T00:00Z")
    series = _hourly((anchor - 400 * mt.HOUR).isoformat(), np.linspace(100, 200, 500))
    pending = collect([anchor], {"X": series}).pending
    up = mt.predict(pending, _Fake(0.01), seed=1)[0]
    down = mt.predict(pending, _Fake(-0.01), seed=1)[0]
    assert up.predictions["timesfm"] and not down.predictions["timesfm"]
    assert up.predictions["always_up"] and up.realized_up


# --------------------------------------------------------------------------- #
# 5. Kapı
# --------------------------------------------------------------------------- #
def _obs(anchors: int, *, tfm_right: float, symbols=("A", "B", "C"), seed=0) -> list[Observation]:
    rng = np.random.default_rng(seed)
    out = []
    for k in range(anchors):
        t = mt.GRID_ORIGIN + k * mt.HORIZON_DELTA
        for s in symbols:
            realized = bool(rng.random() < 0.5)
            right = bool(rng.random() < tfm_right)
            out.append(Observation(
                anchor=t, symbol=s, realized_up=realized,
                predictions={
                    "timesfm": realized if right else not realized,
                    "always_up": True,
                    "momentum": bool(rng.random() < 0.5),
                    "coin": bool(rng.random() < 0.5),
                },
            ))
    return out


def test_strong_forecaster_passes_all_three_rules():
    result = evaluate_period("A", _obs(200, tfm_right=0.8), alpha=0.05, samples=500, seed=1)
    assert result["passed"]
    assert set(result["comparisons"]) == {"always_up", "momentum", "coin"}


def test_identical_to_a_rule_is_not_a_pass():
    obs = [
        Observation(anchor=o.anchor, symbol=o.symbol, realized_up=o.realized_up,
                    predictions={**o.predictions, "timesfm": True})
        for o in _obs(200, tfm_right=0.5)
    ]
    result = evaluate_period("A", obs, alpha=0.05, samples=500, seed=1)
    assert result["comparisons"]["always_up"]["delta"] == 0.0
    assert not result["passed"]


def test_too_few_clusters_is_a_fail_not_a_pass():
    result = evaluate_period("C", _obs(9, tfm_right=1.0), alpha=0.05, samples=200, seed=1)
    assert not result["evaluable"] and not result["passed"]
    assert not any(c["passed"] for c in result["comparisons"].values())


def test_bootstrap_is_deterministic():
    obs = _obs(50, tfm_right=0.6)
    a = evaluate_period("A", obs, alpha=0.05, samples=300, seed=3)
    b = evaluate_period("A", obs, alpha=0.05, samples=300, seed=3)
    assert a["comparisons"] == b["comparisons"]


def test_period_a_failure_never_fetches_b(monkeypatch):
    calls: list[pd.Timestamp] = []

    def loader(config, symbols, *, since, now):
        calls.append(now)
        start = pd.Timestamp(mt.PERIOD_A_START) - 400 * mt.HOUR
        n = int((now - start) / mt.HOUR)
        rng = np.random.default_rng(0)
        values = 100 * np.exp(np.cumsum(rng.normal(0, 0.01, n)))
        return {s: _hourly(start.isoformat(), values) for s in symbols}

    ctx = mt.Context(
        config={}, universe=["BTC-USDT-SWAP"], snapshots=[], forecaster=_Fake(0.0),
        load=loader, now=pd.Timestamp("2026-09-23T07:00Z"), hf_last_modified=None,
        alpha=0.05, samples=200, seed=1,
    )
    result = mt.evaluate(ctx)

    assert not result["passed"] and result["skipped"] == ["B", "C"]
    assert calls == [pd.Timestamp(mt.PERIOD_A_CUTOFF) + mt.HORIZON_DELTA + mt.HOUR]


def test_verdict_reports_parity_failure_first():
    assert mt.verdict({"parity": {"passed": False}}).startswith("PARITE_DUSTU")
    assert mt.verdict({"parity": {"passed": True}}).startswith("PARITE_GECTI")


def test_count_mismatch_is_a_usage_error(tmp_path):
    repo = tmp_path / "h"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    _commit(repo, {"docs/forecasts.json": _forecasts({"BTC": "BTC-USDT-SWAP"})}, "1")
    pin = subprocess.check_output(["git", "-C", str(repo), "rev-parse", "HEAD"], text=True).strip()
    assert mt.check_counts(read_hemstir(repo, pin)) is not None


def test_icc_is_one_when_every_coin_moves_together_and_near_zero_when_independent():
    together = []
    for k in range(40):
        t = mt.GRID_ORIGIN + k * mt.HORIZON_DELTA
        up = k % 2 == 0
        together += [Observation(anchor=t, symbol=s, realized_up=up, predictions={}) for s in "ABCDE"]
    assert mt.anchor_icc(together) == pytest.approx(1.0)
    assert abs(mt.anchor_icc(_obs(400, tfm_right=0.5, symbols=tuple("ABCDEFGHIJ")))) < 0.05


def test_report_carries_effective_sample_and_implied_rho():
    result = evaluate_period("A", _obs(100, tfm_right=0.6), alpha=0.05, samples=300, seed=2)
    assert result["mean_cluster_size"] == 3
    for c in result["comparisons"].values():
        assert c["n_eff"] > 0 and "rho_implied" in c

"""`scripts/backtest_dc.py`: ön-kayda (docs/backtest.md > 6i) MEKANİK sadakat.

Sınanan: (1) pencereler ve embargo yöntemi İTHAL EDİLİR; (2) küme bootstrap'ı i.i.d.
aralıktan geniştir, deterministiktir ve < 10 kümede DEĞERLENDİRİLEMEZ; (3) bağlayıcı alt
sınır iki tanımın MİNİMUMUDUR; (4) fark aralığı EŞLEŞTİRİLMİŞTİR; (5) MDE formülü; (6) karar
sırası ve çıpa istisnası; (7) uçtan uca: sentetik veriyle ağsız tam koşu.
"""

from __future__ import annotations

import ast
import math
import random
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from scripts import backtest_dc as harness
from scripts.backtest_dc import (
    ClusterCI,
    Position,
    binding_low,
    cluster_diff_ci,
    cluster_key,
    cluster_mean_ci,
    evaluate_gates,
    precision,
    precision_diff,
    rr_distribution,
)

SOURCE = Path("scripts/backtest_dc.py").read_text(encoding="utf-8")


# --------------------------------------------------------------------------- #
# (1) Pencereler ve yöntem İTHAL EDİLİR
# --------------------------------------------------------------------------- #
def test_windows_and_embargo_are_imported_not_redefined():
    tree = ast.parse(SOURCE)
    assigned = {
        target.id
        for node in ast.walk(tree) if isinstance(node, ast.Assign)
        for target in node.targets if isinstance(target, ast.Name)
    }
    assert not {"PERIOD_A_START", "PERIOD_A_CUTOFF", "PERIOD_A_TAIL_END"} & assigned
    assert harness.measured_embargo_bars.__module__ == "scripts.backtest_ema"
    assert harness.run_backtest.__module__ == "scripts.backtest"


def test_period_bounds_are_not_cli_inputs():
    """Dönem sınırları girdi DEĞİLDİR; yalnızca B'nin sonu açıktır (§6i > 12)."""
    args = harness._parse_args([])
    assert not hasattr(args, "a_start") and not hasattr(args, "a_cutoff")
    assert not hasattr(args, "history_bars")


# --------------------------------------------------------------------------- #
# (2) Küme bootstrap
# --------------------------------------------------------------------------- #
def _clustered(clusters: int, per: int, *, seed: int = 3) -> dict[str, list[float]]:
    """Küme içi güçlü ortak bileşen: i.i.d. aralığın sahte darlığını üreten yapı."""
    rng = random.Random(seed)
    groups = {}
    for c in range(clusters):
        shared = rng.gauss(0.0, 1.0)
        groups[f"c{c:02d}"] = [shared + rng.gauss(0.0, 0.2) for _ in range(per)]
    return groups


def _iid_width(groups, *, iterations=2000, alpha=0.05, seed=1) -> float:
    from core.metrics import bootstrap_mean_ci

    values = [r for rs in groups.values() for r in rs]
    low, high = bootstrap_mean_ci(values, alpha=alpha, iterations=iterations, seed=seed)
    return high - low


def test_cluster_interval_is_wider_than_iid_on_clustered_data():
    groups = _clustered(20, 15)
    ci = cluster_mean_ci(groups, definition="regime", alpha=0.05, iterations=2000, seed="s")
    assert ci.evaluable and ci.width is not None
    assert ci.width > 1.5 * _iid_width(groups)


def test_cluster_interval_is_deterministic():
    groups = _clustered(20, 5)
    a = cluster_mean_ci(groups, definition="month", alpha=0.05, iterations=500, seed="x")
    b = cluster_mean_ci(groups, definition="month", alpha=0.05, iterations=500, seed="x")
    assert (a.low, a.high) == (b.low, b.high)


def test_fewer_than_ten_clusters_is_not_evaluable():
    groups = _clustered(harness.MIN_CLUSTERS - 1, 10)
    ci = cluster_mean_ci(groups, definition="month", alpha=0.05, iterations=200, seed="s")
    assert not ci.evaluable


def test_regime_cluster_needs_the_tag():
    position = Position(symbol="BTC", opened_at=pd.Timestamp("2022-01-01", tz="UTC"),
                        r=1.0, regime=None, rr=1.0, coin=None)
    with pytest.raises(ValueError, match="regime"):
        cluster_key(position, "regime")
    assert cluster_key(position, "month") == "2022-01"


# --------------------------------------------------------------------------- #
# (3) Bağlayıcı alt sınır = MİNİMUM
# --------------------------------------------------------------------------- #
def _ci(low, high, *, evaluable=True, definition="regime"):
    return ClusterCI(definition=definition, low=low, high=high, clusters=20, n=100, evaluable=evaluable)


def test_binding_low_is_the_minimum_not_the_widest_interval():
    """Geniş ama yukarı kaymış bir aralık kuralı GEVŞETEMEZ (§6i > 8, düzeltme kaydı)."""
    narrow_low = _ci(0.05, 0.10, definition="regime")    # dar, alt sınırı düşük
    wide_high = _ci(0.20, 0.90, definition="month")      # geniş, alt sınırı yüksek
    assert wide_high.width > narrow_low.width
    assert binding_low([narrow_low, wide_high]) == pytest.approx(0.05)


def test_binding_low_is_none_when_any_definition_is_not_evaluable():
    assert binding_low([_ci(0.1, 0.2), _ci(0.1, 0.3, evaluable=False)]) is None


# --------------------------------------------------------------------------- #
# (4) Eşleştirilmiş fark
# --------------------------------------------------------------------------- #
def test_paired_difference_of_identical_samples_is_zero():
    groups = _clustered(15, 6)
    ci = cluster_diff_ci(groups, groups, definition="regime", alpha=0.05, iterations=500, seed="d")
    assert ci.low == pytest.approx(0.0) and ci.high == pytest.approx(0.0)


def test_pairing_removes_the_shared_component():
    """Ortak küme bileşeni farkta birbirini götürür: eşleştirilmiş aralık, bağımsız
    yeniden örneklemenin vereceğinden belirgin DARDIR (kovaryans atılmaz)."""
    rng = random.Random(7)
    model, control = {}, {}
    for c in range(20):
        shared = rng.gauss(0.0, 1.0)
        model[f"c{c}"] = [shared + 0.1 + rng.gauss(0, 0.1) for _ in range(5)]
        control[f"c{c}"] = [shared + rng.gauss(0, 0.1) for _ in range(5)]
    paired = cluster_diff_ci(model, control, definition="regime", alpha=0.05, iterations=1000, seed="p")
    from core.metrics import bootstrap_diff_ci

    flat = lambda g: [r for rs in g.values() for r in rs]  # noqa: E731
    low, high = bootstrap_diff_ci(flat(model), flat(control), alpha=0.05, iterations=1000, seed=1)
    assert paired.width < 0.5 * (high - low)
    assert paired.low > 0.0


# --------------------------------------------------------------------------- #
# (5) Kesinlik
# --------------------------------------------------------------------------- #
def test_singleton_clusters_give_a_design_effect_near_one():
    values = [random.Random(k).gauss(0, 1) for k in range(200)]
    stats = precision({str(k): [v] for k, v in enumerate(values)})
    # SE_küme n'e, SE_iid n−1'e bölünen varyanstan gelir: DEFF = (n−1)/n.
    assert stats["deff"] == pytest.approx((len(values) - 1) / len(values))


def test_mde_is_z_sum_times_the_cluster_se():
    stats = precision(_clustered(20, 5))
    z = 1.959963984540054 + 0.8416212335729143
    assert stats["mde"] == pytest.approx(z * stats["se_cluster"])
    assert stats["n_effective"] == pytest.approx(stats["n"] / stats["deff"])


def test_diff_precision_uses_the_cluster_formula():
    model = {"a": [1.0, 3.0], "b": [2.0]}
    control = {"a": [0.0], "b": [1.0, 2.0]}
    m_mean, c_mean = 2.0, 1.0
    expected = math.sqrt(
        (((1 - m_mean) + (3 - m_mean)) / 3 - (0 - c_mean) / 3) ** 2
        + ((2 - m_mean) / 3 - ((1 - c_mean) + (2 - c_mean)) / 3) ** 2
    )
    assert precision_diff(model, control)["se_cluster"] == pytest.approx(expected)


# --------------------------------------------------------------------------- #
# Geometri
# --------------------------------------------------------------------------- #
def test_rr_distribution_counts_targets_below_one_r():
    stamp = pd.Timestamp("2022-01-01", tz="UTC")
    positions = [
        Position(symbol="X", opened_at=stamp, r=0.0, regime="r", rr=rr, coin=None)
        for rr in (0.4, 0.8, 1.2, 2.0)
    ]
    dist = rr_distribution(positions)
    assert dist["below_1r"] == 2 and dist["below_1r_share"] == pytest.approx(0.5)
    assert dist["median"] == pytest.approx(1.0)


# --------------------------------------------------------------------------- #
# (6) Kapılar ve karar
# --------------------------------------------------------------------------- #
def _period(**overrides):
    flag = {
        "sample": True, "avg_r": 0.4, "control_avg_r": 0.0, "edge_margin_r": 0.15,
        "control_trades": 100, "control_min_trades": 30,
        "total_return": 0.5, "benchmark_return": 0.2,
    }
    flag.update(overrides.pop("flag", {}))
    stats = {
        "model_mean": {"binding_low": overrides.pop("model_low", 0.1)},
        "diff": {"binding_low": overrides.pop("diff_low", 0.1)},
    }
    return {
        "acceptance_model_iid": flag,
        "statistics": stats,
        "consistency": {"S1_stop_distance_gap": {"holds": overrides.pop("s1", True)}},
        "model": {"max_drawdown_pct": overrides.pop("drawdown", -10.0), "trades": 100},
        "validity_B2": overrides.pop("validity", {}),
    }


def _payload(a=None, b=None, *, k1_coins=8):
    per_coin_b = {f"S{k}": {"profit_factor": 1.5 if k < k1_coins else 0.9} for k in range(13)}
    return {"periods": {"A": a or _period(), "B": b or _period()}, "per_coin": {"A": {}, "B": per_coin_b}}


def test_all_gates_green_passes():
    assert evaluate_gates(_payload())["verdict"].startswith("GEÇTİ")


def test_only_anchor_failing_stops_for_the_user():
    below = {"flag": {"total_return": 0.1, "benchmark_return": 0.9}}
    verdict = evaluate_gates(_payload(_period(**below), _period(**below)))["verdict"]
    assert verdict.startswith("DUR")


def test_anchor_plus_anything_else_blocks():
    a = _period(flag={"total_return": 0.1, "benchmark_return": 0.9})
    assert evaluate_gates(_payload(a, k1_coins=3))["verdict"].startswith("BLOKE")


def test_cluster_ci_below_zero_blocks_even_if_iid_would_pass():
    verdict = evaluate_gates(_payload(_period(model_low=-0.01)))["verdict"]
    assert verdict.startswith("BLOKE") and "CI_cluster_low_positive" in verdict


def test_margin_is_needed_on_top_of_the_ci():
    verdict = evaluate_gates(_payload(_period(flag={"avg_r": 0.10, "control_avg_r": 0.0})))["verdict"]
    assert "E_edge" in verdict


def test_s1_breach_makes_e_unreadable_not_passed():
    verdict = evaluate_gates(_payload(_period(s1=False)))["verdict"]
    assert verdict.startswith("BLOKE") and "E OKUNMADI" in verdict


def test_missing_cluster_floor_is_not_evaluable():
    verdict = evaluate_gates(_payload(b=_period(diff_low=None)))["verdict"]
    assert verdict.startswith("DEĞERLENDİRİLEMEZ")


def test_k1_needs_six_coins_in_period_b():
    assert evaluate_gates(_payload(k1_coins=6))["K1_per_coin_pf"]["passed"]
    assert not evaluate_gates(_payload(k1_coins=5))["K1_per_coin_pf"]["passed"]


def test_k1_counts_an_infinite_profit_factor_but_not_nan():
    payload = _payload(k1_coins=5)
    payload["per_coin"]["B"]["S12"] = {"profit_factor": float("inf")}
    payload["per_coin"]["B"]["S11"] = {"profit_factor": float("nan")}
    k1 = evaluate_gates(payload)["K1_per_coin_pf"]
    assert "S12" in k1["passing"] and "S11" not in k1["passing"]
    assert k1["passed"]  # 5 sonlu + 1 sonsuz = 6 coin


def test_k2_is_reported_but_never_binding():
    gates = evaluate_gates(_payload())
    assert gates["K2_total_trades_REPORTED_ONLY"]["binding"] is False
    assert "passed" not in gates["K2_total_trades_REPORTED_ONLY"]


def test_drawdown_over_the_limit_blocks():
    assert "K3_drawdown" in evaluate_gates(_payload(_period(drawdown=-30.0)))["verdict"]


# --------------------------------------------------------------------------- #
# (7) Uçtan uca — ağsız, sentetik veri
# --------------------------------------------------------------------------- #
def test_end_to_end_run_on_synthetic_data(monkeypatch, tmp_path):
    """Tek gerçek koşu tek seferliktir; tesisat onu HARCAMADAN burada sınanır."""
    from core.config import load_config
    from core.layers import resolve_layer
    from strategies.base import MarketData
    import scripts.backtest as bt

    universe = resolve_layer(load_config(), "dc").symbols
    bars = 3600
    index = pd.date_range("2021-01-01", periods=bars, freq="4h", tz="UTC", name="ts")

    def series(seed: int) -> pd.DataFrame:
        rng = np.random.default_rng(seed)
        t = np.arange(bars)
        close = np.maximum(200 + 40 * np.sin(2 * np.pi * t / 700 + seed) + np.cumsum(rng.normal(0, 0.8, bars)), 20)
        opn = np.r_[close[0], close[:-1]] + rng.normal(0, 0.4, bars)
        high = np.maximum(opn, close) + np.abs(rng.normal(0, 1.2, bars))
        low = np.minimum(opn, close) - np.abs(rng.normal(0, 1.2, bars))
        return pd.DataFrame({"open": opn, "high": high, "low": low, "close": close, "volume": 1.0}, index=index)

    frames = {symbol: series(k) for k, symbol in enumerate(universe)}

    def stub(config, *, symbols=None, now=None, **_):
        depth = int(config["data"]["history_bars"])
        chosen = list(symbols) if symbols is not None else list(universe)
        cut = lambda f: f.loc[:now].tail(depth)  # noqa: E731
        btc = cut(frames["BTC-USDT-SWAP"])
        return MarketData(ohlcv={s: cut(frames[s]) for s in chosen}, btc=btc, funding={}, as_of=btc.index[-1])

    monkeypatch.setattr(bt, "load_market_data", stub)
    payload = harness.run(
        out_root=tmp_path, config_path=None, b_end=index[-1], runner=bt.run_backtest,
        history_bars=bars, funding_periods=180,
        a_start=index[3100], a_cutoff=index[3300], a_tail_end=index[3400],
    )
    a = payload["periods"]["A"]
    assert a["funnel"]["available"] and a["funnel"]["setup_bars"] >= a["funnel"]["signals_generated"]
    assert set(a["statistics"]["model_mean"]) >= {"regime", "month", "binding_low"}
    assert set(payload["per_coin"]["B"]) == set(universe)
    assert payload["gates"]["verdict"]
    assert payload["embargo_bars"] >= 0

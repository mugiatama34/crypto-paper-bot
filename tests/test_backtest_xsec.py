"""`scripts/backtest_xsec.py`nin SAF mantığı: ağ yok, koşu yok.

Sınanan şey harness'ın ön-kayda (docs/backtest.md > 6g) sadakatidir:
(1) pencereler `backtest_ema`den İTHAL EDİLİR — ikinci bir kopya yok;
(2) metrik BURADA hesaplanmaz (kural 7);
(3) K-2 raporlanır ama `passed` ÜRETMEZ (TADİLAT-1);
(4) K-1 hiç yok (portföy modeli — yapısal);
(5) çıpa istisnası yalnızca DUR üretir, asla otomatik GEÇTİ;
(6) coin başına koşu yok.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from scripts import backtest_xsec as harness
from scripts.backtest_ema import PERIOD_A_CUTOFF, PERIOD_A_START

SOURCE = Path("scripts/backtest_xsec.py").read_text(encoding="utf-8")


def _flag(**overrides):
    base = {
        "model": harness.MODEL, "sample": True, "passed": False,
        "avg_r": 0.5, "control_avg_r": 0.1, "edge_margin_r": 0.15,
        "edge_diff_ci_low": 0.05, "total_return": 0.2, "benchmark_return": 0.9,
    }
    base.update(overrides)
    return base


def _gates(flag, *, k3_passed=True):
    return {"repo_acceptance": {"A": flag, "B": flag},
            "K3_max_drawdown": {"passed": k3_passed}}


# --------------------------------------------------------------------------- #
# (1) Pencereler İTHAL EDİLİR
# --------------------------------------------------------------------------- #
def test_windows_are_imported_not_redefined():
    """İki yerde yazılı bir pencere bir gün ayrışır ve iki model farklı geçmiş görür."""
    parser_defaults = harness._parse_args([])
    assert parser_defaults.a_start == PERIOD_A_START
    assert parser_defaults.a_cutoff == PERIOD_A_CUTOFF
    assert "from scripts.backtest_ema import" in SOURCE


def test_no_local_period_constants():
    tree = ast.parse(SOURCE)
    assigned = {
        target.id
        for node in ast.walk(tree) if isinstance(node, ast.Assign)
        for target in node.targets if isinstance(target, ast.Name)
    }
    assert "PERIOD_A_START" not in assigned
    assert "PERIOD_A_CUTOFF" not in assigned


# --------------------------------------------------------------------------- #
# (2) Metrik BURADA hesaplanmaz (kural 7)
# --------------------------------------------------------------------------- #
def test_harness_calls_run_backtest_instead_of_reimplementing_it():
    assert "from scripts.backtest import" in SOURCE
    assert "run_backtest(" in SOURCE


def test_embargo_method_is_the_shared_one():
    assert "measured_embargo_bars" in SOURCE
    assert harness.measured_embargo_bars.__module__ == "scripts.backtest_ema"


# --------------------------------------------------------------------------- #
# (3) K-2 raporlanır, BAĞLAMAZ
# --------------------------------------------------------------------------- #
def test_k2_reports_but_never_passes():
    payload = {
        "periods": {
            "A": {"model": {"trades": 40, "max_drawdown_pct": 3.0}, "exit_mix": {"share": {}}},
            "B": {"model": {"trades": 30, "max_drawdown_pct": 3.0}},
        }
    }
    gates = harness.evaluate_gates(payload)
    k2 = gates["K2_total_trades_REPORTED_ONLY"]
    assert k2["binding"] is False
    assert "passed" not in k2, "bağlayıcı olmayan bir kapı `passed` ÜRETMEMELİ"
    assert k2["measured"]["A+B"] == 70


def test_a_tiny_sample_does_not_block():
    """195 işlemle geçen bir model CI kapısında zaten daha büyük etki göstermek zorunda."""
    payload = {
        "periods": {
            "A": {"model": {"trades": 5, "max_drawdown_pct": 1.0},
                  "acceptance_model": _flag(passed=True), "exit_mix": {"share": {}}},
            "B": {"model": {"trades": 4, "max_drawdown_pct": 1.0},
                  "acceptance_model": _flag(passed=True)},
        }
    }
    assert harness.evaluate_gates(payload)["verdict"].startswith("GEÇTİ")


# --------------------------------------------------------------------------- #
# (4) K-1 yok, coin başına koşu yok
# --------------------------------------------------------------------------- #
def test_no_k1_gate_anywhere():
    assert "K1" not in SOURCE and "K1_" not in SOURCE
    assert "profit_factor > " not in SOURCE


def test_no_per_coin_runs():
    """Top-3 seçimi tüm evrene bakar; tek sembollü koşu başka bir model ölçerdi."""
    assert "per_coin" not in SOURCE
    assert "singles" not in SOURCE


# --------------------------------------------------------------------------- #
# (5) Çıpa istisnası: yalnızca DUR
# --------------------------------------------------------------------------- #
def test_only_anchor_failure_stops_instead_of_passing():
    verdict = harness._verdict(_gates(_flag()))
    assert verdict.startswith("DUR")
    assert "GEÇTİ" not in verdict


@pytest.mark.parametrize("override", [
    {"avg_r": -0.1},                 # C-1 düştü
    {"control_avg_r": 0.45},         # marj yetmiyor
    {"edge_diff_ci_low": -0.02},     # CI alt sınırı sıfırın altında
    {"sample": False},               # örneklem kapısı
])
def test_any_other_failure_blocks(override):
    assert harness._verdict(_gates(_flag(**override))).startswith("BLOKE")


def test_k3_breach_blocks_before_anything_else():
    assert harness._verdict(_gates(_flag(passed=True), k3_passed=False)).startswith("BLOKE")


def test_missing_flag_is_inconclusive_not_a_pass():
    gates = {"repo_acceptance": {"A": None, "B": _flag(passed=True)},
             "K3_max_drawdown": {"passed": True}}
    assert harness._verdict(gates).startswith("DEĞERLENDİRİLEMEZ")


def test_k3_uses_account_drawdown():
    payload = {
        "periods": {
            "A": {"model": {"trades": 100, "max_drawdown_pct": 30.0},
                  "acceptance_model": _flag(passed=True), "exit_mix": {"share": {}}},
            "B": {"model": {"trades": 100, "max_drawdown_pct": 5.0},
                  "acceptance_model": _flag(passed=True)},
        }
    }
    gates = harness.evaluate_gates(payload)
    assert gates["K3_max_drawdown"]["passed"] is False
    assert gates["verdict"].startswith("BLOKE — K-3")


# --------------------------------------------------------------------------- #
# P1 bir TAHMİN, kapı değil
# --------------------------------------------------------------------------- #
def test_p1_is_a_prediction_not_a_gate():
    payload = {
        "periods": {
            "A": {"model": {"trades": 100, "max_drawdown_pct": 2.0},
                  "acceptance_model": _flag(passed=True),
                  "exit_mix": {"share": {"signal:rebalance": 0.4, "stop": 0.6}}},
            "B": {"model": {"trades": 90, "max_drawdown_pct": 2.0},
                  "acceptance_model": _flag(passed=True)},
        }
    }
    gates = harness.evaluate_gates(payload)
    p1 = gates["P1_exit_mix_PREDICTION"]
    assert p1["binding"] is False and p1["holds"] is False
    # Tahminin düşmesi kararı DEĞİŞTİRMEZ.
    assert gates["verdict"].startswith("GEÇTİ")


def test_p1_threshold_matches_the_preregistration():
    assert harness.P1_MIN_REBALANCE_SHARE == 0.70

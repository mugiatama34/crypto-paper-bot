"""`scripts/backtest_wave.py`nin SAF mantığı: ağ yok, koşu yok.

Sınanan şey harness'ın ön-kayda (docs/backtest.md > 6h) sadakatidir:

1. **Hold-out kapalı.** Dönem B'ye onaysız, görülmüş veriye HİÇ dokunulmaz.
2. **Pencereler ön-kayıtlıdır** ve varsayılanlar onlarla birebir aynıdır.
3. **Metrik BURADA hesaplanmaz** (kural 7): harness `run_backtest`i çağırır, kırılımları
   `core/metrics.py::breakdown`dan okur ve yüzdelikleri `core/metrics.py::_percentile`dan.
4. **Kapsam bir KAPIDIR** ve düşerse pencere kaydırılmaz, koşu durur.
5. **P1–P4 mekanik okunur**; `nan` bir tahmini ne tutmuş ne düşmüş sayar.
6. **P4 bağlayıcıdır**, diğer üçü tahmin.
"""

from __future__ import annotations

import ast
import math
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from scripts import backtest_wave as harness

SOURCE = Path("scripts/backtest_wave.py").read_text(encoding="utf-8")


class _StubModelReport:
    def __init__(self, *, missing_bars=0, unchecked_position_bars=0, skipped_signals=0):
        self.model = harness.MODEL
        self.missing_bars = missing_bars
        self.unchecked_position_bars = unchecked_position_bars
        self.skipped_signals = skipped_signals


class _StubRoundReport:
    def __init__(self, model_report):
        self._model = model_report
        self.models = (model_report,)

    def by_model(self, model):
        return self._model if model == harness.MODEL else None


class _StubResult:
    """`BacktestResult`ın kapsam/rapor yüzeyi — koşu yapılmadan sınanabilsin diye."""

    def __init__(self, coverage, *, model_report=None):
        self.coverage = coverage
        self.report = _StubRoundReport(model_report or _StubModelReport())
        self.out_dir = Path("backtests/wave/A")
        self.holding = {}
        self.metrics = ()
        self.acceptance = ()
        self.breakdowns = {}
        self.buy_hold = {}
        self.deviations = {}


# --------------------------------------------------------------------------- #
# (1) Hold-out koruması
# --------------------------------------------------------------------------- #
def _guard(cutoff: str, tail: str, *, confirm: bool = False) -> None:
    harness.guard_window(
        signal_cutoff=pd.Timestamp(cutoff),
        tail_end=pd.Timestamp(tail),
        confirm_holdout=confirm,
    )


def test_the_default_run_passes_its_own_guard():
    """Varsayılan çağrı kendi kapısına çarpmamalı — ilk hâli tam olarak buna çarpıyordu."""
    args = harness._parse_args([])
    assert args.confirm_holdout is False
    _guard(args.a_cutoff, args.a_tail_end)


def test_period_b_is_opened_by_the_signal_cutoff_not_by_the_tail():
    """B'yi açan şey barların işlenmesi DEĞİL, yeni SİNYAL üretilmesidir.

    A'nın kuyruğu tanımı gereği B ile zaman olarak örtüşür (kural 13) ve orada tek bir
    sinyal üretilmez; ölçütü pencerenin ucu yapmak varsayılan koşuyu reddetmek olurdu.
    """
    # Kuyruk B'nin içine uzanıyor ama kesim A'da: GEÇER.
    _guard(harness.PERIOD_A_CUTOFF, harness.PERIOD_A_TAIL_END)
    # Kesim B'ye taşındı: onay ister.
    beyond = str(pd.Timestamp(harness.PERIOD_A_CUTOFF) + pd.Timedelta(days=1))
    with pytest.raises(harness.HoldoutError, match="--confirm-holdout"):
        _guard(beyond, harness.PERIOD_A_TAIL_END)
    _guard(beyond, harness.PERIOD_A_TAIL_END, confirm=True)  # Aşama 2 bunu kullanacak


def test_seen_data_cannot_be_opened_by_any_flag():
    """Eylül 2026 bir hold-out DEĞİL, kirlenmiş bir pencere: bayrak onu açmaz.

    Ölçüt burada pencerenin UCUDUR: o barların bir pozisyon yönetimi için bile işlenmesi,
    ölçümü görülmüş fiyatlara bağlardı.
    """
    for confirm in (False, True):
        with pytest.raises(harness.HoldoutError, match="görülmüş veri"):
            _guard(harness.PERIOD_A_CUTOFF, harness.SEEN_DATA_START, confirm=confirm)


def test_default_tail_end_stays_clear_of_seen_data():
    args = harness._parse_args([])
    assert pd.Timestamp(args.a_tail_end) < pd.Timestamp(harness.SEEN_DATA_START)
    assert pd.Timestamp(args.a_tail_end) > pd.Timestamp(harness.PERIOD_A_CUTOFF), (
        "kuyruk olmadan A'nın son kurulumları hiç kapanmazdı"
    )


def test_workflow_never_exposes_the_holdout_flag():
    """Girdi olarak açılsaydı hold-out'a bakmak bir tık meselesi olurdu."""
    workflow = Path(".github/workflows/backtest-wave.yml").read_text(encoding="utf-8")
    commands = [
        line for line in workflow.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    body = "\n".join(commands)
    assert "--confirm-holdout" not in body, "bayrak komuta geçirilmiş"
    assert "confirm_holdout" not in body, "bayrak workflow GİRDİSİ olarak açılmış"
    for boundary in ("a_start", "a_cutoff", "a-start", "a-cutoff", "a_tail_end"):
        assert boundary not in body, (
            f"dönem sınırı ({boundary}) workflow girdisi/komutu olmamalı (§7.3)"
        )
    assert "schedule:" not in body, "cron YOKTUR (§7)"
    assert "contents: read" in body


# --------------------------------------------------------------------------- #
# (2) Pencereler ön-kayıtlı
# --------------------------------------------------------------------------- #
def test_windows_match_the_preregistration():
    assert harness.PERIOD_A_START.startswith("2025-03-01")
    assert harness.PERIOD_A_CUTOFF.startswith("2025-12-31")
    assert harness.PERIOD_B_END.startswith("2026-08-31")
    assert harness.SEEN_DATA_START.startswith("2026-09-01")
    assert harness.PERIOD_B_START == harness.PERIOD_A_CUTOFF, (
        "B, A'nın kesiminden + embargo ile başlar (§6h > 5)"
    )


def test_thresholds_match_the_preregistration():
    assert harness.P1_MAX_NET_AVG_R == 0.0
    assert harness.P2_MIN_COST_PER_R_MEDIAN == 0.08
    assert harness.P3_MAX_GROSS_AVG_R == 0.15
    assert harness.P4_MIN_TRADES == 300


def test_cost_overrides_are_not_available():
    """§6h > 6: bu koşu CANLI maliyetle ölçülür; override bayrağı YOKTUR."""
    args = vars(harness._parse_args([]))
    assert "fee_rate" not in args and "slippage_base" not in args
    assert "--fee-rate" not in SOURCE and "--slippage-base" not in SOURCE


# --------------------------------------------------------------------------- #
# (3) Metrik BURADA hesaplanmaz (kural 7)
# --------------------------------------------------------------------------- #
def test_harness_calls_run_backtest_instead_of_reimplementing_it():
    assert "run_backtest(" in SOURCE
    for forbidden in ("class Portfolio", "def process_bar", "def direction_stats"):
        assert forbidden not in SOURCE


def test_percentiles_and_breakdowns_come_from_core_metrics():
    """İkinci bir yüzdelik ya da kırılım uygulaması, aynı defterin iki cevabı demekti."""
    tree = ast.parse(SOURCE)
    imported = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module == "core.metrics"
        for alias in node.names
    }
    assert {"_percentile", "_median", "breakdown", "merge_fills"} <= imported
    # Harness kendi yüzdelik/ortalama-R tanımını YAZMAZ.
    defined = {n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
    assert "_percentile" not in defined and "_median" not in defined
    assert "avg_r" not in defined


def test_distribution_reports_the_preregistered_percentiles():
    """§6h > 10.2 p5/p25/p50/p75/p95 istiyor; eksik bir yüzdelik sessiz kalırdı."""
    summary = harness.distribution([1.0, 2.0, 3.0, 4.0, 5.0])
    assert set(summary) >= {"n", "p5", "p25", "median", "p75", "p95", "min", "max", "mean"}
    assert summary["n"] == 5
    assert summary["median"] == pytest.approx(3.0)


def test_distribution_of_nothing_is_nan_not_zero():
    """`nan` = ölçülmedi. `0.0` "ölçtük, sıfır çıktı" demektir (CLAUDE.md'nin kuralı)."""
    summary = harness.distribution([])
    assert summary["n"] == 0
    assert all(math.isnan(summary[key]) for key in ("median", "p5", "p95", "mean"))


# --------------------------------------------------------------------------- #
# (4) Kapsam KAPISI
# --------------------------------------------------------------------------- #
def _coverage(first_bars: dict[str, str | None]) -> dict[str, Any]:
    return {
        symbol: {"bars": 100 if first else 0, "first_bar": first, "last_bar": "2025-12-31T00:00:00+00:00"}
        for symbol, first in first_bars.items()
    }


def test_coverage_gate_passes_when_every_symbol_reaches_the_start():
    result = _StubResult(_coverage({"BTC-USDT-SWAP": "2025-03-01T00:00:00+00:00"}))
    gate = harness.coverage_gate(
        result, universe=["BTC-USDT-SWAP"], period_start=pd.Timestamp(harness.PERIOD_A_START)
    )
    assert gate["passed"] is True
    assert gate["symbols_not_reaching_start"] == []


def test_coverage_gate_fails_when_a_symbol_starts_late():
    result = _StubResult(
        _coverage({
            "BTC-USDT-SWAP": "2025-03-01T00:00:00+00:00",
            "ETHFI-USDT-SWAP": "2025-08-01T00:00:00+00:00",
        })
    )
    gate = harness.coverage_gate(
        result,
        universe=["BTC-USDT-SWAP", "ETHFI-USDT-SWAP"],
        period_start=pd.Timestamp(harness.PERIOD_A_START),
    )
    assert gate["passed"] is False
    assert gate["symbols_not_reaching_start"] == ["ETHFI-USDT-SWAP"]


def test_coverage_gate_fails_when_a_symbol_has_no_bars_at_all():
    result = _StubResult(_coverage({"BTC-USDT-SWAP": None}))
    gate = harness.coverage_gate(
        result, universe=["BTC-USDT-SWAP"], period_start=pd.Timestamp(harness.PERIOD_A_START)
    )
    assert gate["passed"] is False


def test_coverage_gate_reads_b2_from_the_model_report():
    """B-2 sayaçları `ModelReport`tadır; `RoundReport`ta ARANMAZ (orada yok)."""
    result = _StubResult(
        _coverage({"BTC-USDT-SWAP": "2025-03-01T00:00:00+00:00"}),
        model_report=_StubModelReport(missing_bars=3),
    )
    gate = harness.coverage_gate(
        result, universe=["BTC-USDT-SWAP"], period_start=pd.Timestamp(harness.PERIOD_A_START)
    )
    assert gate["integrity_B2"]["missing_bars"] == 3
    assert gate["passed"] is False, "B-2 > 0 iken satır OKUNMAZ (§3)"


def test_unchecked_position_bars_also_block():
    result = _StubResult(
        _coverage({"BTC-USDT-SWAP": "2025-03-01T00:00:00+00:00"}),
        model_report=_StubModelReport(unchecked_position_bars=1),
    )
    gate = harness.coverage_gate(
        result, universe=["BTC-USDT-SWAP"], period_start=pd.Timestamp(harness.PERIOD_A_START)
    )
    assert gate["passed"] is False


def test_gate_failure_does_not_shift_the_window():
    """Kaydırma kodu HİÇ YOKTUR: pencere sonuca ya da kapsama göre seçilemez (§7.3)."""
    assert "period_start -" not in SOURCE
    assert "KAYDIRILMAZ" in SOURCE
    # Kapsam düşerse çıkış kodu 3'tür (veri kapısı, karar 51'in kuralı).
    assert "return 3" in SOURCE


# --------------------------------------------------------------------------- #
# (5) / (6) Ön-kayıtlı tahminler
# --------------------------------------------------------------------------- #
def test_predictions_are_read_mechanically():
    verdict = harness.evaluate_predictions(
        metrics_row={"trades": 350, "avg_r": -0.04},
        cost_per_r_median=0.081,
        gross_avg_r=0.06,
    )
    assert verdict["P1_net_avg_r"]["holds"] is True
    assert verdict["P2_cost_per_r_median"]["holds"] is True
    assert verdict["P3_gross_avg_r"]["holds"] is True
    assert verdict["P4_sample"]["holds"] is True


@pytest.mark.parametrize(
    "row,cost,gross,expected",
    [
        ({"trades": 350, "avg_r": 0.10}, 0.081, 0.06, "P1_net_avg_r"),
        ({"trades": 350, "avg_r": -0.04}, 0.02, 0.06, "P2_cost_per_r_median"),
        ({"trades": 350, "avg_r": -0.04}, 0.081, 0.30, "P3_gross_avg_r"),
        ({"trades": 120, "avg_r": -0.04}, 0.081, 0.06, "P4_sample"),
    ],
)
def test_each_prediction_can_fall_independently(row, cost, gross, expected):
    verdict = harness.evaluate_predictions(
        metrics_row=row, cost_per_r_median=cost, gross_avg_r=gross
    )
    assert verdict[expected]["holds"] is False
    others = [k for k in verdict if k != expected]
    assert all(verdict[k]["holds"] is True for k in others), others


def test_p4_is_the_only_binding_prediction():
    """§6h > 7: P4 hem tahmin hem okuma kapısı; diğer üçü yalnızca tahmin."""
    verdict = harness.evaluate_predictions(
        metrics_row={"trades": 350, "avg_r": -0.04}, cost_per_r_median=0.081, gross_avg_r=0.06
    )
    assert verdict["P4_sample"]["binding"] is True
    for key in ("P1_net_avg_r", "P2_cost_per_r_median", "P3_gross_avg_r"):
        assert "binding" not in verdict[key]


def test_an_unmeasurable_prediction_neither_holds_nor_falls():
    """`nan` bir dalı TETİKLEMEZ: ölçülemeyen bir tahmin tutmuş da düşmüş de sayılmaz."""
    verdict = harness.evaluate_predictions(
        metrics_row={"trades": 0, "avg_r": float("nan")},
        cost_per_r_median=float("nan"),
        gross_avg_r=float("nan"),
    )
    for key in ("P1_net_avg_r", "P2_cost_per_r_median", "P3_gross_avg_r"):
        assert verdict[key]["holds"] is None
    assert verdict["P4_sample"]["holds"] is False, "örneklem HER ZAMAN ölçülebilir (0 bile)"


def test_p1_and_p2_declare_that_they_came_from_seen_data():
    """Görülmüş veriden türeyen bir tahminin tutması bir doğrulama DEĞİLDİR."""
    verdict = harness.evaluate_predictions(
        metrics_row={"trades": 350, "avg_r": -0.04}, cost_per_r_median=0.081, gross_avg_r=0.06
    )
    for key in ("P1_net_avg_r", "P2_cost_per_r_median"):
        assert "GÖRÜLMÜŞ" in verdict[key]["source"]


# --------------------------------------------------------------------------- #
# Brüt R, cost/R medyanı ve R:R — defterin kolonlarından
# --------------------------------------------------------------------------- #
def _fill(**overrides) -> dict[str, Any]:
    row = {
        "strategy": harness.MODEL,
        "symbol": "BTC-USDT-SWAP",
        "direction": "long",
        "opened_at": "2025-04-01T00:00:00+00:00",
        "closed_at": "2025-04-01T04:00:00+00:00",
        "entry_price": 100.0,
        "exit_price": 104.0,
        "stop_price": 98.0,
        "risk_amount": 100.0,
        "fee": 4.0,
        "slippage_cost": 2.0,
        "funding": 0.0,
        "pnl": 10.0,
        "exit_reason": "tp",
        "signal_reason": "x | arm=wave3_src | combo=dev1.2_tp1.618 | target=106",
        "notes": "",
    }
    row.update(overrides)
    return row


def test_gross_avg_r_adds_friction_back_to_the_numerator_only():
    """Payda DEĞİŞMEZ: iki sayı aynı birimde okunsun diye (§6h > 8, P3)."""
    rows = [_fill(), _fill(opened_at="2025-04-02T00:00:00+00:00",
                          closed_at="2025-04-02T04:00:00+00:00", pnl=-100.0)]
    # net: (10 − 100) / 200 = −0.45 ; brüt: (−90 + 12) / 200 = −0.39
    assert harness.gross_avg_r(rows) == pytest.approx((-90.0 + 12.0) / 200.0)


def test_cost_per_r_median_is_a_median_not_a_mean():
    """§6h > 8: dağılım sağa çarpık, ortalama tipik friksiyonu anlatmaz."""
    rows = [
        _fill(fee=1.0, slippage_cost=0.0),                                    # 0.01
        _fill(opened_at="2025-04-02T00:00:00+00:00", fee=2.0, slippage_cost=0.0),  # 0.02
        _fill(opened_at="2025-04-03T00:00:00+00:00", fee=90.0, slippage_cost=0.0), # 0.90
    ]
    assert harness.cost_per_r_median(rows) == pytest.approx(0.02)


def test_cost_per_r_median_of_nothing_is_nan():
    assert math.isnan(harness.cost_per_r_median([]))


def test_reward_risk_reads_the_target_tag_and_the_initial_stop():
    """TP kolonu YOKTUR (kural 13c); hedef `target=` etiketinden okunur."""
    profile = harness.reward_risk_profile([_fill()])
    # |106 − 100| / |100 − 98| = 3.0
    assert profile["distribution"]["median"] == pytest.approx(3.0)
    assert profile["unit"] == "position"


def test_reward_risk_without_a_target_tag_is_nan_not_zero():
    profile = harness.reward_risk_profile([_fill(signal_reason="x | arm=wave3_src")])
    assert profile["distribution"]["n"] == 0


def test_stop_distance_buckets_come_from_geometry_not_data():
    """Kenarları veriden türetmek, kovayı sonuca bakarak seçmek olurdu (§7.2)."""
    assert harness.STOP_DISTANCE_BUCKET_EDGES == (1.0, 2.0, 4.0)
    assert harness._stop_bucket(0.5) == "[0%, 1%)"
    assert harness._stop_bucket(1.5) == "[1%, 2%)"
    assert harness._stop_bucket(9.0) == "≥4%"
    assert harness._stop_bucket(float("nan")) == "ölçülemedi"


def test_stop_distance_profile_is_per_position_and_uses_core_breakdown():
    profile = harness.stop_distance_profile([_fill()])
    assert profile["unit"] == "position"
    # |100 − 98| / 100 = %2 -> "[2%, 4%)" kovası
    assert profile["distribution_pct"]["median"] == pytest.approx(2.0)
    assert "[2%, 4%)" in profile["buckets"]
    assert profile["buckets"]["[2%, 4%)"]["trades"] == 1


def test_combo_grid_carries_the_multiple_comparison_warning():
    """Uyarı raporun İÇİNDE durur, okuyucunun hafızasında değil (§6h > 10.4)."""
    grid = harness.combo_grid([_fill()])
    assert "BH düzeltmesi" in grid["warning"]
    assert "dev1.2_tp1.618" in grid["grid"]
    assert grid["grid"]["dev1.2_tp1.618"]["trades"] == 1


def test_exit_mix_declares_that_its_unit_is_a_fill():
    """Kısmi çıkışlı pozisyon İKİ gruba düşer; sessiz kalırsa toplam tutarsız görünür."""
    result = _StubResult({})
    result.breakdowns = {"exit_rule": {harness.MODEL: {
        "stop": {"trades": 7, "avg_r": -1.0},
        "tp": {"trades": 3, "avg_r": 2.0},
    }}}
    mix = harness.exit_mix(result)
    assert mix["unit"] == "fill"
    assert mix["share"]["stop"] == pytest.approx(0.7)
    assert mix["avg_r"]["tp"] == pytest.approx(2.0)


def test_per_symbol_runs_are_labelled_informational():
    """Coin başına koşu bir KAPI üretmez; adı bunu söylemeli (§6h > 7, 9).

    Kapı yokluğu ADIYLA değil, `passed`/`holds` alanı üretmemesiyle sınanır: bir metin
    araması "EK-1" gibi masum dizelerde yanlış alarm verir (ilk hâli tam olarak buna
    düştü) ve daha kötüsü, gerçek bir kapı başka bir adla eklenirse sessiz kalırdı.
    """
    assert "per_symbol_INFORMATIONAL" in SOURCE
    tree = ast.parse(SOURCE)
    defined = {n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
    # Coin başına koşudan kapı türeten bir fonksiyon YOKTUR.
    assert not {n for n in defined if "k1" in n.lower() or "per_symbol_gate" in n.lower()}
    # Yükün coin başına bölümü yalnızca metrik ve tutuş süresi taşır.
    per_symbol_keys = {"metrics", "holding"}
    assert '"metrics": _metrics_row(single),' in SOURCE
    assert all(f'"{key}"' in SOURCE for key in per_symbol_keys)

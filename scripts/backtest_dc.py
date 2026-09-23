#!/usr/bin/env python3
"""`dc_short`un ÖN-KAYITLI koşusu (docs/backtest.md > 6i). Ölçümün parçası DEĞİL.

Ön-kayıt `03e9e2e` (TADİLAT-1 `5ad7653`) bu betikten ÖNCE commit edildi. Betik o metni
MEKANİK olarak uygular: sıra (A → embargo ölçümü → B), kapılar, küme bootstrap'ı, karar.
Hiçbir eşik burada SEÇİLMEZ; her sayı ön-kayıtta yazılı.

**İkinci bir backtest DEĞİLDİR** — `scripts/backtest.py::run_backtest`i çağırır. Pozisyon,
R, ortalama R, kâr faktörü, drawdown, kırılımlar ve i.i.d. aralıklar `core/metrics.py`den
gelir (kural 7). Burada hesaplanan YALNIZCA üç şey vardır ve üçü de ön-kayıtta bu betiğe
verilmiştir:

1. **Küme bootstrap aralıkları ve kesinlik** (§6i > 8-9). `core/metrics.py`nin canlı yolu
   DEĞİŞMEZ (karar 54): canlı tablo i.i.d. aralıkla okunmaya devam eder; bu koşunun
   bağlayıcı aralığı buradadır. R'nin tanımı yine `merge_fills` + `r_multiple`tır.
2. **Hedef-R dağılımı** (`rr=` etiketinden) — bir getiri değil, bir GEOMETRİ.
3. **Huni** (§6i > 6) — tur raporundaki sayaçların (`survey`, `skipped_signals`,
   `rejections`) dizilişi.

**Pencereler `scripts/backtest_ema.py`den İTHAL EDİLİR** (sayımla, `ema_trend`le ve
`xsec_mom`la aynı). **Dönem B, A koşulup embargo ÖLÇÜLMEDEN başlatılamaz** ve betik tek bir
çağrıda ikisini de koşar — `--only` yoktur: tek tetikleme, tek sonuç dosyası (§6i > 12).

**Derinlik** (`--history-bars 12000`) §5b'nin 1. sınıfıdır: modelin gördüğü pencere
`dc.lookback_bars`tır (TADİLAT-1), veri ne kadar eskiden başlarsa başlasın.

Tetikleyicisi `.github/workflows/backtest-dc.yml` (cron YOK — §7).
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import random
import statistics
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from core.config import get_setting, load_config  # noqa: E402
from core.layers import resolve_layer  # noqa: E402
from core.ledger import Ledger  # noqa: E402
from core.metrics import breakdown, merge_fills, r_multiple  # noqa: E402
from core.tags import find_tag  # noqa: E402
from scripts.backtest import BacktestResult, check_validity, run_backtest  # noqa: E402
from scripts.backtest_ema import (  # noqa: E402
    PERIOD_A_CUTOFF,
    PERIOD_A_START,
    PERIOD_A_TAIL_END,
    measured_embargo_bars,
)
from strategies.dc.signal import CROSS_NOT_VISIBLE, SETUP_BAR_CODES  # noqa: E402

logger = logging.getLogger("backtest_dc")

LAYER = "dc"
MODEL = "dc_short"
CONTROL = "dc_coinflip"

# --- Ön-kayıtlı sayılar (docs/backtest.md > 6i). Hiçbiri koşu sonucuna göre değişmez. ---
K1_MIN_COINS = 6              # §6i > 10: dönem B'de en az bu kadar coinde
K1_MIN_PROFIT_FACTOR = 1.1    # ... PF bunun ÜSTÜNDE
K3_MAX_DRAWDOWN_PCT = 25.0    # §6i > 10
K2_REFERENCE_TRADES = 300     # RAPORLANIR, bağlayıcı değil (§6g > TADİLAT-1'in gerekçesi)
MIN_CLUSTERS = 10             # §6i > 8: altında aralık DEĞERLENDİRİLEMEZ
CROSS_NOT_VISIBLE_FINDING = 0.05   # §6i > 6: aşarsa BULGU (pencere değiştirilmez)
S1_MAX_RELATIVE_GAP = 0.10    # §6i > 10: aşarsa E kapısı OKUNMAZ
S2_CENTER, S2_TOLERANCE = 0.5, 0.05
P3_MIN_RATIO = 1.5            # §6i > 11
POWER = 0.80                  # §6i > 9
COUNT_REFERENCE = {"raw": 1773, "primary": 1090}   # §6i > 2, commit f5df1bb
CLUSTER_DEFINITIONS = ("regime", "month")
HISTORY_BARS = 12000          # §6i > 12 (TADİLAT-1): §5b 1. sınıf derinlik
FUNDING_PERIODS = 6000        # `ema_trend`in koşusuyla aynı; derinleştirir, kısaltmaz


# --------------------------------------------------------------------------- #
# Pozisyonlar — R'nin TEK tanımı core/metrics.py'dir
# --------------------------------------------------------------------------- #
@dataclass(frozen=True, kw_only=True)
class Position:
    symbol: str
    opened_at: pd.Timestamp
    r: float
    regime: str | None
    rr: float | None
    coin: str | None


def positions_of(rows: Iterable[Mapping[str, Any]]) -> list[Position]:
    """Defter satırları -> pozisyonlar (`merge_fills`), R'si bilinenler."""
    out: list[Position] = []
    for row in merge_fills(rows):
        r = r_multiple(row)
        if r is None:
            continue
        reason = str(row.get("signal_reason", ""))
        rr = find_tag(reason, "rr")
        out.append(
            Position(
                symbol=str(row["symbol"]),
                opened_at=pd.Timestamp(row["opened_at"]),
                r=float(r),
                regime=find_tag(reason, "regime"),
                rr=float(rr) if rr is not None else None,
                coin=find_tag(reason, "coin"),
            )
        )
    return out


def read_rows(result: BacktestResult, model: str) -> list[dict[str, str]]:
    return Ledger(result.out_dir / "ledger").read_trades(model)


def cluster_key(position: Position, definition: str) -> str:
    """Küme kimliği (§6i > 8). `regime`: sembol + kesişim barı; `month`: `opened_at` ayı (UTC).

    `regime` etiketi YOKSA hata fırlatılır: etiketsiz bir pozisyonu tek başına bir küme
    saymak, bağımlılığı tam da ölçülmek istenen yerde yok sayardı (`core/tags.py`nin
    "eksik etiket sessizce atlanmaz" kuralı).
    """
    if definition == "regime":
        if position.regime is None:
            raise ValueError(f"{position.symbol} {position.opened_at}: `regime=` etiketi yok")
        return f"{position.symbol}|{position.regime}"
    if definition == "month":
        stamp = position.opened_at.tz_convert("UTC") if position.opened_at.tzinfo else position.opened_at
        return f"{stamp.year:04d}-{stamp.month:02d}"
    raise ValueError(f"tanınmayan küme tanımı: {definition!r}")


def group(positions: Sequence[Position], definition: str) -> dict[str, list[float]]:
    groups: dict[str, list[float]] = {}
    for position in positions:
        groups.setdefault(cluster_key(position, definition), []).append(position.r)
    return groups


# --------------------------------------------------------------------------- #
# Küme bootstrap (§6i > 8)
# --------------------------------------------------------------------------- #
@dataclass(frozen=True, kw_only=True)
class ClusterCI:
    definition: str
    low: float | None
    high: float | None
    clusters: int
    n: int
    evaluable: bool
    dropped_draws: int = 0

    @property
    def width(self) -> float | None:
        if self.low is None or self.high is None:
            return None
        return self.high - self.low


def _percentiles(values: Sequence[float], alpha: float) -> tuple[float, float]:
    """Yüzdelik aralık, DOĞRUSAL ara değerleme — `core/metrics.py::_percentile`in tanımı."""
    ordered = np.sort(np.asarray(values, dtype="float64"))
    return (
        float(np.percentile(ordered, 100.0 * alpha / 2.0)),
        float(np.percentile(ordered, 100.0 * (1.0 - alpha / 2.0))),
    )


def cluster_mean_ci(
    groups: Mapping[str, Sequence[float]],
    *,
    definition: str,
    alpha: float,
    iterations: int,
    seed: str,
) -> ClusterCI:
    """Ortalama R'nin küme bootstrap aralığı: C kümeden C tanesi YERİNE KOYARAK çekilir."""
    ids = sorted(groups)
    n = sum(len(values) for values in groups.values())
    evaluable = len(ids) >= MIN_CLUSTERS and iterations > 0
    if not ids or iterations <= 0:
        return ClusterCI(definition=definition, low=None, high=None, clusters=len(ids), n=n, evaluable=False)
    sums = [float(sum(groups[i])) for i in ids]
    counts = [len(groups[i]) for i in ids]
    rng = random.Random(seed)
    means: list[float] = []
    for _ in range(int(iterations)):
        total = count = 0.0
        for _ in ids:
            k = rng.randrange(len(ids))
            total += sums[k]
            count += counts[k]
        means.append(total / count)
    low, high = _percentiles(means, alpha)
    return ClusterCI(definition=definition, low=low, high=high, clusters=len(ids), n=n, evaluable=evaluable)


def cluster_diff_ci(
    model: Mapping[str, Sequence[float]],
    control: Mapping[str, Sequence[float]],
    *,
    definition: str,
    alpha: float,
    iterations: int,
    seed: str,
) -> ClusterCI:
    """`ort(model) − ort(kontrol)` — EŞLEŞTİRİLMİŞ küme bootstrap'ı (§6i > 8).

    Kümeler iki modelde ORTAKTIR (kontrol aynı kurulum barlarında yazı-tura atar), bu
    yüzden her iterasyonda küme etiketleri BİR KEZ, iki modelin kümelerinin BİRLEŞİMİNDEN
    çekilir. `core/metrics.py::bootstrap_diff_ci`ın bağımsız yeniden örneklemesi burada
    var olan bir kovaryansı atmak olurdu. Bir tarafı boş kalan çekiliş atılır ve SAYILIR.
    """
    ids = sorted(set(model) | set(control))
    n = sum(len(v) for v in model.values()) + sum(len(v) for v in control.values())
    evaluable = len(ids) >= MIN_CLUSTERS and iterations > 0
    if not model or not control or iterations <= 0:
        return ClusterCI(definition=definition, low=None, high=None, clusters=len(ids), n=n, evaluable=False)
    m_sum = [float(sum(model.get(i, ()))) for i in ids]
    m_cnt = [len(model.get(i, ())) for i in ids]
    c_sum = [float(sum(control.get(i, ()))) for i in ids]
    c_cnt = [len(control.get(i, ())) for i in ids]
    rng = random.Random(seed)
    diffs: list[float] = []
    dropped = 0
    attempts = 0
    # Atılan çekilişler yerine yenisi çekilir, ama sonsuz döngü olmasın diye tavanlı:
    # tavana varılırsa aralık DEĞERLENDİRİLEMEZ.
    while len(diffs) < iterations and attempts < 4 * iterations:
        attempts += 1
        ms = mc = cs = cc = 0.0
        for _ in ids:
            k = rng.randrange(len(ids))
            ms += m_sum[k]
            mc += m_cnt[k]
            cs += c_sum[k]
            cc += c_cnt[k]
        if mc == 0 or cc == 0:
            dropped += 1
            continue
        diffs.append(ms / mc - cs / cc)
    if len(diffs) < iterations:
        return ClusterCI(
            definition=definition, low=None, high=None, clusters=len(ids), n=n,
            evaluable=False, dropped_draws=dropped,
        )
    low, high = _percentiles(diffs, alpha)
    return ClusterCI(
        definition=definition, low=low, high=high, clusters=len(ids), n=n,
        evaluable=evaluable, dropped_draws=dropped,
    )


def binding_low(intervals: Sequence[ClusterCI]) -> float | None:
    """BAĞLAYICI alt sınır: iki tanımın alt sınırlarının MİNİMUMU (§6i > 8).

    Tanımlardan biri değerlendirilemezse (küme < 10 ya da hesaplanamadı) bağlayıcı sınır
    YOKTUR ve kapı geçilmiş sayılmaz — eksik bir çıta geçilmiş çıta gibi görünmemeli.
    """
    if not intervals or not all(ci.evaluable and ci.low is not None for ci in intervals):
        return None
    return min(ci.low for ci in intervals if ci.low is not None)


# --------------------------------------------------------------------------- #
# Kesinlik ve MDE (§6i > 9) — formül ön-kayıtlı, sayı koşudan
# --------------------------------------------------------------------------- #
def _z() -> float:
    normal = statistics.NormalDist()
    return normal.inv_cdf(0.975) + normal.inv_cdf(POWER)


def precision(groups: Mapping[str, Sequence[float]]) -> dict[str, Any]:
    """Tek örneklem: SE_küme, SE_iid, DEFF, n_etkin, MDE. Bir KESİNLİK beyanıdır."""
    values = [r for rs in groups.values() for r in rs]
    n = len(values)
    if n < 2:
        return {"n": n, "clusters": len(groups), "evaluable": False}
    mean = sum(values) / n
    sd = statistics.stdev(values)
    se_iid = sd / math.sqrt(n)
    se_cluster = math.sqrt(sum(sum(r - mean for r in rs) ** 2 for rs in groups.values())) / n
    deff = (se_cluster / se_iid) ** 2 if se_iid > 0 else float("nan")
    return {
        "n": n,
        "clusters": len(groups),
        "sd": sd,
        "se_iid": se_iid,
        "se_cluster": se_cluster,
        "deff": deff,
        "n_effective": n / deff if deff and deff == deff else float("nan"),
        "mde": _z() * se_cluster,
        "evaluable": len(groups) >= MIN_CLUSTERS,
    }


def precision_diff(
    model: Mapping[str, Sequence[float]], control: Mapping[str, Sequence[float]]
) -> dict[str, Any]:
    """Fark: `d_g = Σ(r−r̄_m)/n_m − Σ(r−r̄_c)/n_c`, `SE = sqrt(Σ d_g²)` (§6i > 9)."""
    m_values = [r for rs in model.values() for r in rs]
    c_values = [r for rs in control.values() for r in rs]
    n_m, n_c = len(m_values), len(c_values)
    ids = set(model) | set(control)
    if n_m < 2 or n_c < 2:
        return {"n_model": n_m, "n_control": n_c, "clusters": len(ids), "evaluable": False}
    m_mean, c_mean = sum(m_values) / n_m, sum(c_values) / n_c
    se_cluster = math.sqrt(
        sum(
            (
                sum(r - m_mean for r in model.get(i, ())) / n_m
                - sum(r - c_mean for r in control.get(i, ())) / n_c
            ) ** 2
            for i in ids
        )
    )
    se_iid = math.sqrt(statistics.variance(m_values) / n_m + statistics.variance(c_values) / n_c)
    deff = (se_cluster / se_iid) ** 2 if se_iid > 0 else float("nan")
    return {
        "n_model": n_m,
        "n_control": n_c,
        "clusters": len(ids),
        "se_iid": se_iid,
        "se_cluster": se_cluster,
        "deff": deff,
        "mde": _z() * se_cluster,
        "evaluable": len(ids) >= MIN_CLUSTERS,
    }


# --------------------------------------------------------------------------- #
# Geometri ve huni — hesap değil, dizilim
# --------------------------------------------------------------------------- #
def rr_distribution(positions: Sequence[Position]) -> dict[str, Any]:
    """Hedef-R çarpanı (`rr=`): medyan, p25, p75, p90 ve `rr < 1.0` SAYISI/PAYI (§6i > 13)."""
    values = [p.rr for p in positions if p.rr is not None]
    missing = sum(1 for p in positions if p.rr is None)
    if not values:
        return {"n": 0, "missing_tag": missing}
    below = sum(1 for v in values if v < 1.0)
    return {
        "n": len(values),
        "missing_tag": missing,
        "p25": float(np.percentile(values, 25)),
        "median": float(np.percentile(values, 50)),
        "p75": float(np.percentile(values, 75)),
        "p90": float(np.percentile(values, 90)),
        "below_1r": below,
        "below_1r_share": below / len(values),
    }


def funnel(result: BacktestResult, model: str = MODEL) -> dict[str, Any]:
    """Sayım ↔ backtest hunisi (§6i > 6). Sayaçlar tur raporundandır; burada dizilir."""
    report = result.report.by_model(model)
    if report is None:
        return {"available": False}
    survey = dict(report.survey)
    setup_bars = sum(int(survey.get(code, 0)) for code in SETUP_BAR_CODES)
    hidden = int(survey.get(CROSS_NOT_VISIBLE, 0))
    share = hidden / setup_bars if setup_bars else None
    return {
        "available": True,
        "count_reference": COUNT_REFERENCE,
        "survey": survey,
        "setup_bars": setup_bars,
        "cross_not_visible": hidden,
        "cross_not_visible_share": share,
        "cross_not_visible_finding": share is not None and share > CROSS_NOT_VISIBLE_FINDING,
        "target_undefined": int(survey.get("target_undefined", 0)),
        "target_passed": int(survey.get("target_passed", 0)),
        "signals_generated": int(survey.get("setup", 0)),
        "skipped_by_ceiling": int(report.skipped_signals),
        "emitted": len(report.emitted),
        "rejections": dict(report.rejections),
        "filled": int(report.filled),
    }


def exit_mix(result: BacktestResult, model: str) -> dict[str, Any]:
    groups = (result.breakdowns or {}).get("exit_rule") or {}
    rows = groups.get(model) or {}
    counts = {key: int(row["trades"]) for key, row in rows.items() if row.get("trades")}
    total = sum(counts.values())
    return {
        "counts": counts,
        "share": {key: value / total for key, value in counts.items()} if total else {},
        "unit": "fill",
    }


def symbol_breakdown(result: BacktestResult, model: str) -> dict[str, Any]:
    return dict(((result.breakdowns or {}).get("symbol") or {}).get(model) or {})


def year_breakdown(result: BacktestResult, model: str) -> dict[str, Any]:
    """Yıl kırılımı — `core/metrics.py::breakdown`, anahtar `opened_at`in yılı."""
    groups = breakdown(read_rows(result, model), key=lambda row: str(pd.Timestamp(row["opened_at"]).year))
    return {
        year: {
            "trades": stats.trades,
            "avg_r": stats.avg_r,
            "win_rate": stats.win_rate,
            "profit_factor": stats.profit_factor,
        }
        for year, stats in sorted(groups.items())
    }


# --------------------------------------------------------------------------- #
# Dönem yükü
# --------------------------------------------------------------------------- #
def _flag(result: BacktestResult, model: str) -> dict[str, Any] | None:
    return next((dict(asdict(f)) for f in result.acceptance if f.model == model), None)


def _metrics_row(result: BacktestResult, model: str) -> dict[str, Any] | None:
    for item in result.metrics:
        if item.model != model:
            continue
        return {
            "trades": item.total.trades,
            "avg_r": item.total.avg_r,
            "avg_r_ci_low_iid": item.total.avg_r_ci_low,
            "avg_r_ci_high_iid": item.total.avg_r_ci_high,
            "win_rate": item.total.win_rate,
            "profit_factor": item.total.profit_factor,
            "avg_stop_distance_pct": item.total.avg_stop_distance_pct,
            "cost_per_r": item.total.cost_per_r,
            "max_drawdown_pct": _pct(item.account.max_drawdown),
            "total_return_pct": _pct(item.account.total_return),
        }
    return None


def _pct(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return None if number != number else number * 100.0


def statistics_block(
    model_positions: Sequence[Position],
    control_positions: Sequence[Position],
    *,
    period: str,
    alpha: float,
    iterations: int,
    seed: int,
) -> dict[str, Any]:
    """İki tanımın aralıkları, bağlayıcı sınırlar, kesinlik — model, kontrol ve fark."""
    out: dict[str, Any] = {"model_mean": {}, "control_mean": {}, "diff": {}, "precision": {}}
    model_cis: list[ClusterCI] = []
    control_cis: list[ClusterCI] = []
    diff_cis: list[ClusterCI] = []
    for definition in CLUSTER_DEFINITIONS:
        m_groups = group(model_positions, definition)
        c_groups = group(control_positions, definition)
        m_ci = cluster_mean_ci(
            m_groups, definition=definition, alpha=alpha, iterations=iterations,
            seed=f"{seed}:{MODEL}:{definition}:{period}",
        )
        c_ci = cluster_mean_ci(
            c_groups, definition=definition, alpha=alpha, iterations=iterations,
            seed=f"{seed}:{CONTROL}:{definition}:{period}",
        )
        d_ci = cluster_diff_ci(
            m_groups, c_groups, definition=definition, alpha=alpha, iterations=iterations,
            seed=f"{seed}:{MODEL}-{CONTROL}:{definition}:{period}",
        )
        model_cis.append(m_ci)
        control_cis.append(c_ci)
        diff_cis.append(d_ci)
        out["model_mean"][definition] = asdict(m_ci) | {"width": m_ci.width}
        out["control_mean"][definition] = asdict(c_ci) | {"width": c_ci.width}
        out["diff"][definition] = asdict(d_ci) | {"width": d_ci.width}
        out["precision"][definition] = {
            "model": precision(m_groups),
            "diff": precision_diff(m_groups, c_groups),
        }
    out["model_mean"]["binding_low"] = binding_low(model_cis)
    out["control_mean"]["binding_low"] = binding_low(control_cis)
    out["diff"]["binding_low"] = binding_low(diff_cis)
    # MDE'nin bağlayıcı okuması BÜYÜK olanıdır (minimum kuralının güç tarafı, §6i > 9).
    mdes = [out["precision"][d]["model"].get("mde") for d in CLUSTER_DEFINITIONS]
    diff_mdes = [out["precision"][d]["diff"].get("mde") for d in CLUSTER_DEFINITIONS]
    out["mde_binding"] = {
        "model": max((m for m in mdes if isinstance(m, float)), default=None),
        "diff": max((m for m in diff_mdes if isinstance(m, float)), default=None),
    }
    return out


def consistency(
    payload: Mapping[str, Any], model_positions: Sequence[Position], control_positions: Sequence[Position]
) -> dict[str, Any]:
    """S1 / S2 / M1 (§6i > 10): hipotez değil, kıyasın okunabilirliği."""
    model_row = payload.get("model") or {}
    control_row = payload.get("control") or {}
    a, b = model_row.get("avg_stop_distance_pct"), control_row.get("avg_stop_distance_pct")
    gap = abs(a - b) / a if _finite(a) and _finite(b) and a else None
    coins = [p.coin for p in control_positions]
    flipped = sum(1 for c in coins if c == "flipped")
    tagged = sum(1 for c in coins if c in ("same", "flipped"))
    share = flipped / tagged if tagged else None
    cost = control_row.get("cost_per_r")
    control_cis = payload["statistics"]["control_mean"]
    covers = {
        d: (
            _finite(cost) and control_cis[d]["low"] is not None
            and control_cis[d]["low"] <= -cost <= control_cis[d]["high"]
        )
        for d in CLUSTER_DEFINITIONS
    }
    return {
        "S1_stop_distance_gap": {
            "relative_gap": gap,
            "tolerance": S1_MAX_RELATIVE_GAP,
            "holds": gap is not None and gap < S1_MAX_RELATIVE_GAP,
        },
        "S2_flipped_share": {
            "share": share,
            "tagged": tagged,
            "untagged": len(coins) - tagged,
            "holds": share is not None and abs(share - S2_CENTER) <= S2_TOLERANCE,
        },
        "M1_control_covers_minus_cost": {
            "minus_cost_per_r": -cost if _finite(cost) else None,
            "covered_by": covers,
            "holds": all(covers.values()),
        },
    }


def _finite(value: Any) -> bool:
    return isinstance(value, (int, float)) and value == value and not math.isinf(value)


def period_payload(
    result: BacktestResult,
    *,
    start: pd.Timestamp,
    end: pd.Timestamp,
    period: str,
    alpha: float,
    iterations: int,
    seed: int,
) -> dict[str, Any]:
    model_positions = positions_of(read_rows(result, MODEL))
    control_positions = positions_of(read_rows(result, CONTROL))
    payload: dict[str, Any] = {
        "start": str(start),
        "end": str(end),
        "model": _metrics_row(result, MODEL),
        "control": _metrics_row(result, CONTROL),
        "acceptance_model_iid": _flag(result, MODEL),
        "validity_B2": check_validity(result.report),
        "exit_mix": exit_mix(result, MODEL),
        "holding": dict(result.holding.get(MODEL) or {}),
        "rr": rr_distribution(model_positions),
        "funnel": funnel(result, MODEL),
        "by_symbol": symbol_breakdown(result, MODEL),
        "by_year": year_breakdown(result, MODEL),
        "coverage": dict(result.coverage),
        "statistics": statistics_block(
            model_positions, control_positions, period=period,
            alpha=alpha, iterations=iterations, seed=seed,
        ),
    }
    payload["consistency"] = consistency(payload, model_positions, control_positions)
    return payload


# --------------------------------------------------------------------------- #
# Kapılar ve karar — MEKANİK (§6i > 10)
# --------------------------------------------------------------------------- #
def period_gates(period: Mapping[str, Any]) -> dict[str, Any]:
    """Bir dönemin bağlayıcı kapıları. i.i.d. dışı sayılar `acceptance_flags`ten okunur."""
    flag = period.get("acceptance_model_iid") or {}
    stats = period["statistics"]
    s1 = period["consistency"]["S1_stop_distance_gap"]["holds"]
    avg_r, control = flag.get("avg_r"), flag.get("control_avg_r")
    margin = flag.get("edge_margin_r")
    model_low = stats["model_mean"]["binding_low"]
    diff_low = stats["diff"]["binding_low"]
    control_sampled = (flag.get("control_trades") or 0) >= (flag.get("control_min_trades") or 0)
    drawdown = (period.get("model") or {}).get("max_drawdown_pct")
    validity = MODEL not in (period.get("validity_B2") or {}) and bool(flag.get("sample"))
    return {
        "validity": validity,
        "cluster_floor": model_low is not None and diff_low is not None,
        "C1_avg_r_positive": _finite(avg_r) and avg_r > 0.0,
        "CI_cluster_low_positive": model_low is not None and model_low > 0.0,
        "E_edge": (
            s1 and control_sampled and _finite(avg_r) and _finite(control) and _finite(margin)
            and (avg_r - control) >= margin and diff_low is not None and diff_low > 0.0
        ),
        "E_readable_S1": s1,
        "C3_anchor": (
            _finite(flag.get("total_return")) and _finite(flag.get("benchmark_return"))
            and flag["total_return"] > flag["benchmark_return"]
        ),
        "K3_drawdown": _finite(drawdown) and abs(drawdown) <= K3_MAX_DRAWDOWN_PCT,
    }


def k1_gate(per_coin_b: Mapping[str, Any]) -> dict[str, Any]:
    passing = sorted(
        symbol for symbol, row in per_coin_b.items()
        # Kaybı olmayan bir coinde PF sonsuzdur ve eşiği GEÇER; yalnızca `nan` (işlem yok)
        # ölçülemedi demektir. `_finite` burada yanlış araç olurdu.
        if isinstance(row, Mapping) and isinstance(row.get("profit_factor"), (int, float))
        and row["profit_factor"] == row["profit_factor"]
        and row["profit_factor"] > K1_MIN_PROFIT_FACTOR
    )
    return {
        "threshold_pf": K1_MIN_PROFIT_FACTOR,
        "min_coins": K1_MIN_COINS,
        "passing": passing,
        "passed": len(passing) >= K1_MIN_COINS,
    }


_BINDING = ("C1_avg_r_positive", "CI_cluster_low_positive", "E_edge", "C3_anchor", "K3_drawdown")


def evaluate_gates(payload: Mapping[str, Any]) -> dict[str, Any]:
    periods = {name: period_gates(payload["periods"][name]) for name in ("A", "B")}
    k1 = k1_gate(payload["per_coin"]["B"])
    failures: list[str] = []
    for name, gates in periods.items():
        failures += [f"{name}:{gate}" for gate in _BINDING if not gates[gate]]
    if not k1["passed"]:
        failures.append("B:K1_per_coin_pf")
    gates: dict[str, Any] = {
        "periods": periods,
        "K1_per_coin_pf": k1,
        "failures": failures,
        "K2_total_trades_REPORTED_ONLY": {
            "binding": False,
            "reference_threshold": K2_REFERENCE_TRADES,
            "measured": {
                name: ((payload["periods"][name].get("model") or {}).get("trades") or 0)
                for name in ("A", "B")
            },
        },
    }
    gates["verdict"] = _verdict(periods, failures)
    return gates


def _verdict(periods: Mapping[str, Mapping[str, bool]], failures: Sequence[str]) -> str:
    """Karar sırası (§6i > 10): geçerlilik → küme tabanı → S1 → kapılar → çıpa istisnası."""
    invalid = [name for name, g in periods.items() if not g["validity"]]
    if invalid:
        return f"DEĞERLENDİRİLEMEZ — geçerlilik kapısı (B-1/B-2) düştü: {', '.join(invalid)}"
    floor = [name for name, g in periods.items() if not g["cluster_floor"]]
    if floor:
        return f"DEĞERLENDİRİLEMEZ — küme tabanı (< {MIN_CLUSTERS} küme ya da aralık yok): {', '.join(floor)}"
    if not failures:
        return "GEÇTİ — tüm bağlayıcı kapılar yeşil"
    if all(f.endswith(":C3_anchor") for f in failures):
        return "DUR — yalnızca çıpa koşulundan kalıyor; karar kullanıcıya gider (otomatik geçiş YOK)"
    unreadable = [name for name, g in periods.items() if not g["E_readable_S1"]]
    note = f" (S1 aşıldı, E OKUNMADI: {', '.join(unreadable)})" if unreadable else ""
    return f"BLOKE — çıpa dışında en az bir kapıdan kalıyor: {', '.join(failures)}{note}"


def predictions(payload: Mapping[str, Any]) -> dict[str, Any]:
    a, b = payload["periods"]["A"], payload["periods"]["B"]
    a_r = (a.get("model") or {}).get("avg_r")
    b_r = (b.get("model") or {}).get("avg_r")
    iid = (a.get("model") or {})
    iid_width = (
        iid["avg_r_ci_high_iid"] - iid["avg_r_ci_low_iid"]
        if _finite(iid.get("avg_r_ci_high_iid")) and _finite(iid.get("avg_r_ci_low_iid")) else None
    )
    widths = [a["statistics"]["model_mean"][d]["width"] for d in CLUSTER_DEFINITIONS]
    ratios = {
        d: (w / iid_width if _finite(w) and iid_width else None)
        for d, w in zip(CLUSTER_DEFINITIONS, widths)
    }
    finite_ratios = [r for r in ratios.values() if _finite(r)]
    flag = a.get("acceptance_model_iid") or {}
    return {
        "P1_A_avg_r_positive": {"measured": a_r, "holds": _finite(a_r) and a_r > 0.0},
        "P2_B_below_A": {"A": a_r, "B": b_r, "holds": _finite(a_r) and _finite(b_r) and b_r < a_r},
        "P3_cluster_over_iid_width": {
            "ratios": ratios,
            "threshold": P3_MIN_RATIO,
            "holds": bool(finite_ratios) and max(finite_ratios) > P3_MIN_RATIO,
        },
        "P4_A_below_anchor": {
            "total_return": flag.get("total_return"),
            "benchmark_return": flag.get("benchmark_return"),
            "holds": (
                _finite(flag.get("total_return")) and _finite(flag.get("benchmark_return"))
                and flag["total_return"] <= flag["benchmark_return"]
            ),
        },
    }


# --------------------------------------------------------------------------- #
# Koşu
# --------------------------------------------------------------------------- #
Runner = Callable[..., BacktestResult]


def run_portfolio(runner: Runner, *, name: str, out_root: Path, **kwargs: Any) -> BacktestResult:
    logger.info("[%s] portföy koşusu", name)
    return runner(layer_name=LAYER, out_dir=out_root / f"{name}-portfolio", **kwargs)


def run_singles(
    runner: Runner, *, name: str, out_root: Path, symbols: Sequence[str], **kwargs: Any
) -> dict[str, Any]:
    """Tek-sembollü koşular (K-1). Düşen sembol SESSİZ geçmez: satırı `failed` taşır."""
    rows: dict[str, Any] = {}
    for symbol in symbols:
        try:
            result = runner(
                layer_name=LAYER, out_dir=out_root / f"{name}-{symbol}",
                models=[MODEL], symbols=[symbol], **kwargs,
            )
        except Exception as exc:  # noqa: BLE001
            logger.error("[%s] %s koşulamadı: %s", name, symbol, exc)
            rows[symbol] = {"failed": str(exc)}
            continue
        rows[symbol] = _metrics_row(result, MODEL) or {"failed": "metrik satırı yok"}
    return rows


def run(
    *,
    out_root: Path,
    config_path: str | None,
    b_end: pd.Timestamp,
    runner: Runner = run_backtest,
    history_bars: int = HISTORY_BARS,
    funding_periods: int = FUNDING_PERIODS,
    a_start: pd.Timestamp = pd.Timestamp(PERIOD_A_START),
    a_cutoff: pd.Timestamp = pd.Timestamp(PERIOD_A_CUTOFF),
    a_tail_end: pd.Timestamp = pd.Timestamp(PERIOD_A_TAIL_END),
) -> dict[str, Any]:
    """Ön-kayıtlı SIRA: A (portföy + tek-sembollüler) → embargo ÖLÇÜLÜR → B (aynısı)."""
    config = load_config(config_path)
    layer = resolve_layer(config, LAYER)
    symbols = list(layer.symbols or [])
    alpha = float(get_setting(layer.config, "acceptance.edge_ci_alpha"))
    iterations = int(get_setting(layer.config, "acceptance.bootstrap_samples"))
    seed = int(get_setting(layer.config, "random_seed"))
    shared = dict(history_bars=history_bars, funding_periods=funding_periods, config_path=config_path)

    period_a = run_portfolio(
        runner, name="A", out_root=out_root, start=a_start, end=a_tail_end,
        signal_cutoff=a_cutoff, **shared,
    )
    singles_a = run_singles(
        runner, name="A", out_root=out_root, symbols=symbols, start=a_start, end=a_tail_end,
        signal_cutoff=a_cutoff, **shared,
    )

    embargo = measured_embargo_bars(period_a, model=MODEL)
    logger.info("embargo dönem A'dan ÖLÇÜLDÜ: %d bar", embargo)

    period_b = run_portfolio(
        runner, name="B", out_root=out_root, start=a_cutoff, end=b_end, embargo_bars=embargo, **shared,
    )
    singles_b = run_singles(
        runner, name="B", out_root=out_root, symbols=symbols, start=a_cutoff, end=b_end,
        embargo_bars=embargo, **shared,
    )

    payload: dict[str, Any] = {
        "layer": LAYER,
        "model": MODEL,
        "control": CONTROL,
        "preregistration": "docs/backtest.md > 6i (03e9e2e, TADİLAT-1 5ad7653)",
        "history_bars": history_bars,
        "embargo_bars": embargo,
        "periods": {
            "A": period_payload(
                period_a, start=a_start, end=a_cutoff, period="A",
                alpha=alpha, iterations=iterations, seed=seed,
            ),
            "B": period_payload(
                period_b, start=period_b.start, end=period_b.end, period="B",
                alpha=alpha, iterations=iterations, seed=seed,
            ),
        },
        "per_coin": {"A": singles_a, "B": singles_b},
    }
    payload["gates"] = evaluate_gates(payload)
    payload["predictions"] = predictions(payload)
    return payload


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    out_root = Path(args.out_dir)
    b_end = pd.Timestamp(args.b_end) if args.b_end else pd.Timestamp.now(tz="UTC").floor("h")
    payload = run(out_root=out_root, config_path=args.config, b_end=b_end)

    out_root.mkdir(parents=True, exist_ok=True)
    results = out_root / "results.json"
    results.write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    logger.info("sonuç yazıldı: %s", results)
    print(json.dumps(
        {"gates": payload["gates"], "predictions": payload["predictions"]},
        indent=2, ensure_ascii=False, default=str,
    ))
    return 0


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="dc_short ön-kayıtlı koşusu (docs/backtest.md > 6i)")
    parser.add_argument("--out-dir", default="backtests/dc")
    parser.add_argument("--config", default=None)
    # Dönem sınırları ve derinlik GİRDİ DEĞİLDİR: ön-kayıtlıdır ve ithal edilir. Yalnızca
    # B'nin sonu açıktır — varsayılanı koşu anıdır.
    parser.add_argument("--b-end", default=None, help="dönem B sonu (ISO); varsayılan: koşu anı")
    return parser.parse_args(argv)


if __name__ == "__main__":
    raise SystemExit(main())

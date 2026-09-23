#!/usr/bin/env python3
"""TimesFM 2.5 (zero-shot) 48 saatlik YÖN İSABETİ — salt okunur araştırma (docs/backtest.md > 6k).

Cevapladığı soru tek: *TimesFM'in 48 saatlik yön tahmini, aynı noktalarda üç basit
kuraldan (hep yukarı, momentum, yazı-tura) daha isabetli mi?*

**Bu bir model DEĞİLDİR.** Hiçbir modeli `REGISTRY`e, hiçbir katmanın `models` listesine,
hiçbir deftere sokmaz; deftere, `config.yaml`a, `strategies/`e ve `data/cache/`e YAZMAZ
(mum önbelleği koşuya özel geçici dizindedir — `measure_death_cross.py`nin gerekçesi).
`core/metrics.py`, `core/portfolio.py`, `core/ledger.py` ve `strategies/*` modüllerini
import ETMEZ (test). İsabet bir R değildir; bu betik R/PnL üretmez.

## Sıra ön-kayıtlıdır ve betiğin İÇİNDEDİR (§6k > 8)

`--stage parity`: P0 (model paritesi) + P1 (veri paritesi). Değerlendirme yok.
`--stage evaluate`: P0 → P1 → dönem A → (A geçerse) dönem B ve Katman C. Parite düşerse
hiçbir isabet hesaplanmaz; A düşerse B/C'nin verisi HİÇ ÇEKİLMEZ — "B'ye dokunma" bir
bayrak kontrolü değil, isteğin kendisidir (dönem A'nın mumları `now = A sonu` ile çekilir).

## hemstir'den okunan TEK dosya

`docs/forecasts.json`'ın git geçmişi, ön-kayıtta sabitlenen commit'e (`HEMSTIR_PIN`)
kadar. hemstir'in sonuç dosyaları ve belgeleri bizim sonucumuz kayda geçene kadar
AÇILMAZ (§6k > 2); betik yalnızca `FORECASTS_PATH`i okur (test).

## Çıkış kodları

- `0` — aşama koştu (parite ya da hipotez DÜŞSE de: karar `results.json > verdict`te).
- `1` — belirsiz dış hata (HF revizyonu okunamadı, git okunamadı): sessizce bir varsayılana
  DÜŞMEZ — `C_start`ı sessizce 2025-09-15'e sabitlemek sızıntı kapısını gevşetebilirdi.
- `2` — kullanım hatası (hemstir sabitlenen commit'i taşımıyor, sayım ön-kayıtla uyuşmuyor).
- `3` — VERİ KAPISI: hiçbir sembol için veri yok (karar 51: boş rapor yeşil dönmez).

Kullanım (depo kökünden):
    python scripts/measure_timesfm.py --stage parity --hemstir /path/to/hemstir --out-dir out
    python scripts/measure_timesfm.py --stage evaluate --hemstir /path/to/hemstir --out-dir out
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import platform
import subprocess
import sys
import tempfile
import zlib
from dataclasses import dataclass, field
from importlib import metadata
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from core.config import get_setting, load_config  # noqa: E402
from core.data import OKXClient, fetch_ohlcv  # noqa: E402
from core.layers import resolve_layer  # noqa: E402
from scripts.backtest_ema import PERIOD_A_CUTOFF, PERIOD_A_START  # noqa: E402

logger = logging.getLogger("measure-timesfm")

# --------------------------------------------------------------------------- #
# Ön-kayıtlı sabitler (§6k). Süpürülmez, girdi DEĞİLDİR.
# --------------------------------------------------------------------------- #
LAYER = "ema"
BAR = "1H"
HOUR = pd.Timedelta(hours=1)
CONTEXT = 300
HORIZON = 48
HORIZON_DELTA = pd.Timedelta(hours=HORIZON)

CHECKPOINT = "google/timesfm-2.5-200m-pytorch"
TIMESFM_PIN = "2.0.2"
FORECAST_CONFIG: Mapping[str, Any] = {
    "max_context": CONTEXT,
    "max_horizon": HORIZON,
    "normalize_inputs": True,
    "use_continuous_quantile_head": True,
    "force_flip_invariance": True,
    "infer_is_positive": True,
    "fix_quantile_crossing": True,
}

# hemstir: yalnızca bu dosya, yalnızca bu commit'e kadar (§6k > 3).
HEMSTIR_PIN = "f6ae0f4f3bf47909fbd1775c320cfc86b1e23178"
FORECASTS_PATH = "docs/forecasts.json"
EXPECTED_COMMITS = 54
EXPECTED_SERIES = 713
# hemstir'in tüm-zaman listesi (§6k > 4); `ema` evreniyle KESİŞİMİ ölçülür.
HEMSTIR_COINS = (
    "BTC", "ETH", "SOL", "XRP", "ADA", "AVAX", "DOGE", "DOT", "LINK", "LTC",
    "ETHFI", "CRV", "NEAR", "BNB",
)

# Parite (§6k > 5).
PARITY_REL_TOL = 1e-4
PARITY_MOVE_EXCEPTION = 1e-4
PARITY_EXCEPTION_CAP = 0.05

# Izgara ve dönemler (§6k > 4, 7).
GRID_ORIGIN = pd.Timestamp("2022-01-01T00:00:00+00:00")
C_START_OFFICIAL = pd.Timestamp("2025-09-15T00:00:00+00:00")  # resmi yayın notu: google-research/timesfm, "Update - Sept. 15, 2025"
MIN_CLUSTERS = 10
MDE_FACTOR = 2.802  # %80 güç, α 0.05 iki yanlı

RULES = ("always_up", "momentum", "coin")


# --------------------------------------------------------------------------- #
# Tahminci arayüzü: gerçek TimesFM yalnızca workflow'da kurulur.
# --------------------------------------------------------------------------- #
class Forecaster(Protocol):
    def forecast(
        self, inputs: Sequence[np.ndarray], *, batch_size: int
    ) -> tuple[np.ndarray, np.ndarray | None]:
        """(nokta (n, 48), kantil (n, 48, q) ya da None)."""


class TimesFMForecaster:
    """hemstir'in `load_model`/`run_forecast` çağrısının birebir karşılığı.

    `timesfm` TEMBEL ithal edilir: bağımlılık yalnızca workflow'da kurulur,
    `requirements.txt` değişmez (§6k > 4).
    """

    def __init__(self) -> None:
        import timesfm  # noqa: PLC0415

        self._timesfm = timesfm
        self._model = timesfm.TimesFM_2p5_200M_torch.from_pretrained(CHECKPOINT, torch_compile=False)
        self._batch: int | None = None

    def forecast(
        self, inputs: Sequence[np.ndarray], *, batch_size: int
    ) -> tuple[np.ndarray, np.ndarray | None]:
        if batch_size != self._batch:
            self._model.compile(
                self._timesfm.ForecastConfig(per_core_batch_size=batch_size, **FORECAST_CONFIG)
            )
            self._batch = batch_size
        points, quantiles = self._model.forecast(
            horizon=HORIZON, inputs=[np.asarray(x, dtype=np.float64) for x in inputs]
        )
        return np.asarray(points), (None if quantiles is None else np.asarray(quantiles))


def confidence_band(quantile_row: Any) -> tuple[float | None, float | None]:
    """hemstir'in `confidence_band`ı: ilk kolon (ortalama) HARİÇ en küçük / en büyük."""
    if quantile_row is None or len(quantile_row) < 2:
        return None, None
    tail = np.asarray(quantile_row)[1:]
    return float(np.min(tail)), float(np.max(tail))


def is_up(forecast_end: float, last_close: float) -> bool:
    """hemstir'in yön kuralı: `>=` (eşitlik YUKARI), parite gereği."""
    return float(forecast_end) - float(last_close) >= 0.0


# --------------------------------------------------------------------------- #
# hemstir geçmişi (YALNIZCA `FORECASTS_PATH`)
# --------------------------------------------------------------------------- #
@dataclass(frozen=True, kw_only=True)
class CommittedSeries:
    commit: str
    generated_at: str
    coin: str
    inst_id: str
    history_ts: tuple[pd.Timestamp, ...]
    closes: np.ndarray
    value: np.ndarray
    lower: np.ndarray
    upper: np.ndarray
    side: str | None


@dataclass(frozen=True, kw_only=True)
class Snapshot:
    commit: str
    series: tuple[CommittedSeries, ...]


def _git(repo: Path, *args: str) -> str:
    return subprocess.check_output(["git", "-C", str(repo), *args], text=True)


def _array(points: Sequence[Mapping[str, Any]], key: str) -> np.ndarray:
    return np.asarray([np.nan if p.get(key) is None else float(p[key]) for p in points], dtype="float64")


def parse_snapshot(commit: str, payload: Mapping[str, Any]) -> Snapshot:
    """Bir commit'in `forecasts.json`u. Coin SIRASI korunur: hemstir tek batch'te o sırayla koşar."""
    series = []
    for coin, entry in payload["coins"].items():
        history = entry["history"]
        forecast = entry["forecast"]
        signal = entry.get("signal")
        series.append(
            CommittedSeries(
                commit=commit,
                generated_at=str(payload.get("generated_at")),
                coin=coin,
                inst_id=str(entry["inst_id"]),
                history_ts=tuple(pd.Timestamp(h["ts"]) for h in history),
                closes=np.asarray([float(h["close"]) for h in history], dtype="float64"),
                value=_array(forecast, "value"),
                lower=_array(forecast, "lower"),
                upper=_array(forecast, "upper"),
                side=(signal or {}).get("side"),
            )
        )
    return Snapshot(commit=commit, series=tuple(series))


def read_hemstir(repo: Path, pin: str = HEMSTIR_PIN) -> list[Snapshot]:
    """`FORECASTS_PATH`in `pin`e kadarki her sürümü, eskiden yeniye. Başka dosya OKUNMAZ."""
    commits = _git(repo, "rev-list", "--reverse", pin, "--", FORECASTS_PATH).split()
    return [
        parse_snapshot(commit, json.loads(_git(repo, "show", f"{commit}:{FORECASTS_PATH}")))
        for commit in commits
    ]


# --------------------------------------------------------------------------- #
# P0 — model paritesi
# --------------------------------------------------------------------------- #
def _rel_err(ours: np.ndarray, theirs: np.ndarray) -> float:
    mask = ~np.isnan(theirs)
    if not mask.any():
        return 0.0
    denom = np.maximum(np.abs(theirs[mask]), 1e-12)
    return float(np.max(np.abs(ours[mask] - theirs[mask]) / denom))


def p0_series_check(
    series: CommittedSeries, points: np.ndarray, quantiles: np.ndarray | None
) -> dict[str, Any]:
    lower = np.full(HORIZON, np.nan)
    upper = np.full(HORIZON, np.nan)
    if quantiles is not None:
        for h in range(HORIZON):
            lo, hi = confidence_band(quantiles[h])
            lower[h] = np.nan if lo is None else lo
            upper[h] = np.nan if hi is None else hi
    entry = float(series.closes[-1])
    committed_move = float(series.value[-1]) - entry
    eligible = abs(committed_move) / abs(entry) < PARITY_MOVE_EXCEPTION
    ours_side = "BUY" if is_up(points[-1], entry) else "SELL"
    side_match = series.side is None or ours_side == series.side
    errors = {
        "value": _rel_err(np.asarray(points, dtype="float64"), series.value),
        "lower": _rel_err(lower, series.lower),
        "upper": _rel_err(upper, series.upper),
    }
    values_ok = all(err <= PARITY_REL_TOL for err in errors.values())
    return {
        "commit": series.commit[:7],
        "coin": series.coin,
        "max_rel_err": errors,
        "values_ok": values_ok,
        "side_checked": series.side is not None,
        "side_match": side_match,
        "exception_eligible": eligible,
        "passed": values_ok and (side_match or eligible),
    }


def p0_verdict(checks: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    total = len(checks)
    eligible = sum(1 for c in checks if c["exception_eligible"])
    excused = sum(1 for c in checks if c["exception_eligible"] and not c["side_match"])
    failures = [c for c in checks if not c["passed"]]
    share = eligible / total if total else float("nan")
    cap_ok = total > 0 and share <= PARITY_EXCEPTION_CAP
    worst = {
        key: max((c["max_rel_err"][key] for c in checks), default=float("nan"))
        for key in ("value", "lower", "upper")
    }
    return {
        "series": total,
        "value_failures": sum(1 for c in checks if not c["values_ok"]),
        "side_failures": sum(1 for c in checks if not c["side_match"] and not c["exception_eligible"]),
        "exception_eligible": eligible,
        "exception_eligible_share": share,
        "exception_excused_side_mismatch": excused,
        "exception_cap": PARITY_EXCEPTION_CAP,
        "worst_rel_err": worst,
        "failures_sample": failures[:20],
        "passed": total > 0 and not failures and cap_ok,
    }


def run_p0(snapshots: Sequence[Snapshot], forecaster: Forecaster) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    for snapshot in snapshots:
        inputs = [s.closes for s in snapshot.series]
        points, quantiles = forecaster.forecast(inputs, batch_size=len(inputs))
        for index, series in enumerate(snapshot.series):
            checks.append(
                p0_series_check(
                    series, points[index], None if quantiles is None else quantiles[index]
                )
            )
    return p0_verdict(checks)


# --------------------------------------------------------------------------- #
# P1 — veri paritesi
# --------------------------------------------------------------------------- #
def p1_candidates(snapshots: Sequence[Snapshot], universe: Sequence[str]) -> list[CommittedSeries]:
    return [
        s for snap in snapshots for s in snap.series
        if s.inst_id.endswith("-USDT-SWAP") and s.inst_id in universe
    ]


def p1_check(series: Sequence[CommittedSeries], closes: Mapping[str, pd.Series]) -> dict[str, Any]:
    """İlk 299 bar BİREBİR; son bar (indeks 299) kapanmamış olduğu için HARİÇ (S1)."""
    compared = 0
    mismatched = 0
    sample: list[dict[str, Any]] = []
    for s in series:
        ours = closes.get(s.inst_id)
        for ts, theirs in zip(s.history_ts[:-1], s.closes[:-1]):
            compared += 1
            value = None if ours is None else ours.get(ts)
            if value is not None and math.isfinite(value) and round(float(value), 8) == float(theirs):
                continue
            mismatched += 1
            if len(sample) < 20:
                sample.append({
                    "commit": s.commit[:7], "inst_id": s.inst_id, "ts": ts.isoformat(),
                    "reason": "eksik_bar" if value is None else "fark",
                })
    return {
        "series": len(series),
        "bars_compared": compared,
        "mismatches": mismatched,
        "mismatch_sample": sample,
        "passed": compared > 0 and mismatched == 0,
    }


# --------------------------------------------------------------------------- #
# Değerlendirme: ızgara, gözlemler, kurallar
# --------------------------------------------------------------------------- #
def grid_anchors(start: pd.Timestamp, end: pd.Timestamp) -> list[pd.Timestamp]:
    """`GRID_ORIGIN + k × 48s` ∩ [start, end]. Faz 00:00Z'ye SABİTTİR (S2)."""
    if end < start:
        return []
    first = max(0, math.ceil((start - GRID_ORIGIN) / HORIZON_DELTA))
    last = math.floor((end - GRID_ORIGIN) / HORIZON_DELTA)
    return [GRID_ORIGIN + k * HORIZON_DELTA for k in range(first, last + 1)]


def last_matured_anchor(now: pd.Timestamp) -> pd.Timestamp:
    """Hedef barı (açılış T+48s) `now`dan önce KAPANMIŞ olan en geç T."""
    return now - HOUR - HORIZON_DELTA


def c_start(hf_last_modified: pd.Timestamp | None) -> pd.Timestamp:
    """max(resmi yayın, HF revizyon tarihi) — ileri kayabilir, geri KAYMAZ (§6k > 7)."""
    if hf_last_modified is None:
        return C_START_OFFICIAL
    return max(C_START_OFFICIAL, pd.Timestamp(hf_last_modified).tz_convert("UTC"))


def coin_up(seed: int, anchor: pd.Timestamp, symbol: str) -> bool:
    """(c) yazı-tura: (sembol, çapa) başına bağımsız, koşudan koşuya aynı."""
    key = f"{seed}:{anchor.isoformat()}:{symbol}:timesfm_coin"
    return bool(np.random.default_rng(zlib.crc32(key.encode())).random() < 0.5)


@dataclass(frozen=True, kw_only=True)
class Observation:
    anchor: pd.Timestamp
    symbol: str
    realized_up: bool
    predictions: Mapping[str, bool]


@dataclass(kw_only=True)
class Pending:
    anchor: pd.Timestamp
    symbol: str
    context: np.ndarray
    last_close: float
    target_close: float
    momentum_up: bool


@dataclass(kw_only=True)
class Collected:
    pending: list[Pending] = field(default_factory=list)
    dropped: dict[str, int] = field(default_factory=dict)

    def drop(self, reason: str) -> None:
        self.dropped[reason] = self.dropped.get(reason, 0) + 1


def collect(anchors: Sequence[pd.Timestamp], closes: Mapping[str, pd.Series]) -> Collected:
    """(çapa, sembol) çiftleri; eksik veride DÜŞER ve SAYILIR — doldurma yok."""
    out = Collected()
    for symbol, series in closes.items():
        first = series.index.min() if len(series) else None
        for anchor in anchors:
            context_start = anchor - (CONTEXT - 1) * HOUR
            if first is None or first > context_start:
                out.drop("listelenmemis")
                continue
            window = series.reindex(pd.date_range(context_start, anchor, freq="1h"))
            target = series.get(anchor + HORIZON_DELTA)
            if window.isna().any() or target is None or not math.isfinite(target):
                out.drop("eksik_bar")
                continue
            last_close = float(window.iloc[-1])
            if float(target) == last_close:
                out.drop("sifir_hareket")
                continue
            out.pending.append(
                Pending(
                    anchor=anchor,
                    symbol=symbol,
                    context=window.to_numpy(dtype="float64"),
                    last_close=last_close,
                    target_close=float(target),
                    momentum_up=last_close >= float(window.iloc[-1 - HORIZON]),
                )
            )
    return out


def predict(
    pending: Sequence[Pending], forecaster: Forecaster, *, seed: int, batch_size: int = 128
) -> list[Observation]:
    observations: list[Observation] = []
    for offset in range(0, len(pending), batch_size):
        chunk = pending[offset: offset + batch_size]
        points, _ = forecaster.forecast([p.context for p in chunk], batch_size=batch_size)
        for index, item in enumerate(chunk):
            observations.append(
                Observation(
                    anchor=item.anchor,
                    symbol=item.symbol,
                    realized_up=item.target_close > item.last_close,
                    predictions={
                        "timesfm": is_up(points[index][HORIZON - 1], item.last_close),
                        "always_up": True,
                        "momentum": item.momentum_up,
                        "coin": coin_up(seed, item.anchor, item.symbol),
                    },
                )
            )
    return observations


# --------------------------------------------------------------------------- #
# Küme bootstrap (küme = çapa damgası), EŞLEŞTİRİLMİŞ
# --------------------------------------------------------------------------- #
def _clusters(
    observations: Sequence[Observation], value: Callable[[Observation], float]
) -> tuple[np.ndarray, np.ndarray]:
    sums: dict[pd.Timestamp, float] = {}
    counts: dict[pd.Timestamp, int] = {}
    for obs in observations:
        sums[obs.anchor] = sums.get(obs.anchor, 0.0) + value(obs)
        counts[obs.anchor] = counts.get(obs.anchor, 0) + 1
    keys = sorted(sums)
    return np.asarray([sums[k] for k in keys]), np.asarray([counts[k] for k in keys], dtype="float64")


def cluster_ratio_ci(
    sums: np.ndarray, counts: np.ndarray, *, alpha: float, samples: int, seed: Sequence[int]
) -> dict[str, float]:
    """Σdeğer / Σgözlem oranının küme bootstrap'ı: çapalar yerine koymalı örneklenir."""
    estimate = float(sums.sum() / counts.sum()) if counts.sum() else float("nan")
    if len(sums) == 0:
        return {"estimate": estimate, "low": float("nan"), "high": float("nan"), "se": float("nan")}
    rng = np.random.default_rng(list(seed))
    draws = rng.integers(0, len(sums), size=(samples, len(sums)))
    stats = sums[draws].sum(axis=1) / counts[draws].sum(axis=1)
    low, high = np.percentile(stats, [100 * alpha / 2, 100 * (1 - alpha / 2)])
    return {"estimate": estimate, "low": float(low), "high": float(high), "se": float(stats.std(ddof=1))}


def _hit(obs: Observation, rule: str) -> float:
    return 1.0 if obs.predictions[rule] == obs.realized_up else 0.0


def evaluate_period(
    name: str,
    observations: Sequence[Observation],
    *,
    alpha: float,
    samples: int,
    seed: int,
) -> dict[str, Any]:
    """Üç kurala karşı AYRI AYRI; < MIN_CLUSTERS küme = değerlendirilemez = GEÇMEDİ."""
    anchors = {o.anchor for o in observations}
    n = len(observations)
    evaluable = len(anchors) >= MIN_CLUSTERS
    accuracy = {}
    for rule in ("timesfm", *RULES):
        sums, counts = _clusters(observations, lambda o, r=rule: _hit(o, r))
        accuracy[rule] = cluster_ratio_ci(
            sums, counts, alpha=alpha, samples=samples,
            seed=(seed, zlib.crc32(f"{name}:acc:{rule}".encode())),
        )
    comparisons = {}
    for rule in RULES:
        sums, counts = _clusters(observations, lambda o, r=rule: _hit(o, "timesfm") - _hit(o, r))
        ci = cluster_ratio_ci(
            sums, counts, alpha=alpha, samples=samples,
            seed=(seed, zlib.crc32(f"{name}:diff:{rule}".encode())),
        )
        diffs = np.asarray([_hit(o, "timesfm") - _hit(o, rule) for o in observations])
        se_iid = float(diffs.std(ddof=1) / math.sqrt(n)) if n > 1 else float("nan")
        discordance = float(np.mean([o.predictions["timesfm"] != o.predictions[rule] for o in observations])) if n else float("nan")
        passed = evaluable and ci["estimate"] > 0 and ci["low"] > 0
        comparisons[rule] = {
            "delta": ci["estimate"],
            "ci_low": ci["low"],
            "ci_high": ci["high"],
            "se_cluster": ci["se"],
            "mde": MDE_FACTOR * ci["se"],
            "deff": (ci["se"] / se_iid) ** 2 if se_iid and se_iid > 0 else float("nan"),
            "discordance": discordance,
            "passed": bool(passed),
        }
    return {
        "period": name,
        "clusters": len(anchors),
        "observations": n,
        "evaluable": evaluable,
        "accuracy": accuracy,
        "up_share": {
            "timesfm_predicted": float(np.mean([o.predictions["timesfm"] for o in observations])) if n else float("nan"),
            "realized": float(np.mean([o.realized_up for o in observations])) if n else float("nan"),
        },
        "per_symbol_observations": dict(sorted(
            (s, sum(1 for o in observations if o.symbol == s)) for s in {o.symbol for o in observations}
        )),
        "comparisons": comparisons,
        "passed": bool(evaluable and all(c["passed"] for c in comparisons.values())),
    }


# --------------------------------------------------------------------------- #
# Evren ve veri
# --------------------------------------------------------------------------- #
def universe_of(ema_symbols: Sequence[str]) -> list[str]:
    hemstir = {f"{coin}-USDT-SWAP" for coin in HEMSTIR_COINS}
    return [s for s in ema_symbols if s in hemstir]


def load_closes(
    config: dict[str, Any], symbols: Sequence[str], *, since: pd.Timestamp, now: pd.Timestamp,
    client: OKXClient | None = None,
) -> dict[str, pd.Series]:
    """Kapanmış 1H kapanışları, `now`dan İLERİSİ ÇEKİLMEZ (dönem kapısı isteğin kendisi)."""
    bars = int((now - since) / HOUR) + 2
    run_config = {**config, "timeframe": BAR, "data": {**config["data"], "history_bars": bars}}
    active = client if client is not None else OKXClient.from_config(run_config)
    closes: dict[str, pd.Series] = {}
    for symbol in symbols:
        frame = fetch_ohlcv(run_config, symbol, client=active, now=now)
        closes[symbol] = frame["close"].astype("float64") if not frame.empty else pd.Series(dtype="float64")
    return closes


# --------------------------------------------------------------------------- #
# Orkestrasyon
# --------------------------------------------------------------------------- #
def versions() -> dict[str, str]:
    out = {"python": platform.python_version()}
    for package in ("timesfm", "torch", "numpy", "pandas", "huggingface_hub"):
        try:
            out[package] = metadata.version(package)
        except metadata.PackageNotFoundError:
            out[package] = "yok"
    return out


def checkpoint_revision() -> tuple[str, pd.Timestamp]:
    from huggingface_hub import HfApi  # noqa: PLC0415

    info = HfApi().model_info(CHECKPOINT)
    return str(info.sha), pd.Timestamp(info.last_modified).tz_convert("UTC")


@dataclass(kw_only=True)
class Context:
    config: dict[str, Any]
    universe: list[str]
    snapshots: list[Snapshot]
    forecaster: Forecaster
    load: Callable[..., dict[str, pd.Series]]
    now: pd.Timestamp
    hf_last_modified: pd.Timestamp | None
    alpha: float
    samples: int
    seed: int


def parity(ctx: Context) -> dict[str, Any]:
    p0 = run_p0(ctx.snapshots, ctx.forecaster)
    candidates = p1_candidates(ctx.snapshots, ctx.universe)
    since = min((s.history_ts[0] for s in candidates), default=ctx.now)
    closes = ctx.load(ctx.config, sorted({s.inst_id for s in candidates}), since=since, now=ctx.now)
    p1 = p1_check(candidates, closes)
    logger.info("P0 %s | P1 %s", "GEÇTİ" if p0["passed"] else "DÜŞTÜ", "GEÇTİ" if p1["passed"] else "DÜŞTÜ")
    return {"P0": p0, "P1": p1, "passed": bool(p0["passed"] and p1["passed"])}


def run_period(
    ctx: Context, name: str, start: pd.Timestamp, end: pd.Timestamp, closes: Mapping[str, pd.Series]
) -> dict[str, Any]:
    anchors = grid_anchors(start, end)
    collected = collect(anchors, closes)
    observations = predict(collected.pending, ctx.forecaster, seed=ctx.seed)
    result = evaluate_period(name, observations, alpha=ctx.alpha, samples=ctx.samples, seed=ctx.seed)
    result.update({
        "start": start.isoformat(),
        "end": end.isoformat(),
        "grid_anchors": len(anchors),
        "dropped": dict(sorted(collected.dropped.items())),
    })
    logger.info(
        "dönem %s: %d çapa, %d küme, %d gözlem -> %s",
        name, len(anchors), result["clusters"], result["observations"],
        "GEÇTİ" if result["passed"] else "GEÇMEDİ",
    )
    return result


def evaluate(ctx: Context) -> dict[str, Any]:
    """A → (A geçerse) B ve C. A düşerse B/C'nin verisi ÇEKİLMEZ."""
    a_start = pd.Timestamp(PERIOD_A_START)
    a_end = pd.Timestamp(PERIOD_A_CUTOFF)
    a_now = a_end + HORIZON_DELTA + HOUR
    closes_a = ctx.load(ctx.config, ctx.universe, since=a_start - CONTEXT * HOUR, now=a_now)
    if not any(len(s) for s in closes_a.values()):
        raise DataGate("dönem A için hiçbir sembolde veri yok")
    periods = {"A": run_period(ctx, "A", a_start, a_end, closes_a)}
    if not periods["A"]["passed"]:
        return {"periods": periods, "skipped": ["B", "C"], "passed": False}

    b_start = a_end + HORIZON_DELTA
    end = last_matured_anchor(ctx.now)
    closes_b = ctx.load(ctx.config, ctx.universe, since=b_start - CONTEXT * HOUR, now=ctx.now)
    periods["B"] = run_period(ctx, "B", b_start, end, closes_b)
    periods["C"] = run_period(ctx, "C", c_start(ctx.hf_last_modified), end, closes_b)
    return {
        "periods": periods,
        "skipped": [],
        "passed": all(p["passed"] for p in periods.values()),
    }


class DataGate(RuntimeError):
    """Karar 51: ölçemediğimiz şeyi boş bir raporla yeşil döndürmeyiz."""


def verdict(payload: Mapping[str, Any]) -> str:
    par = payload.get("parity")
    if par is not None and not par["passed"]:
        return "PARITE_DUSTU — hiçbir sayı okunmaz"
    ev = payload.get("evaluation")
    if ev is None:
        return "PARITE_GECTI — değerlendirme koşulmadı"
    if ev["passed"]:
        return "GECTI — A ∧ B ∧ C, üç kurala karşı ayrı ayrı"
    if ev["skipped"]:
        return "DUSTU — dönem A geçilmedi; B ve C koşulmadı"
    failed = [name for name, p in ev["periods"].items() if not p["passed"]]
    return f"DUSTU — geçilmeyen: {', '.join(failed)}"


def check_counts(snapshots: Sequence[Snapshot]) -> str | None:
    commits = len(snapshots)
    series = sum(len(s.series) for s in snapshots)
    if (commits, series) != (EXPECTED_COMMITS, EXPECTED_SERIES):
        return (
            f"hemstir geçmişi ön-kayıtla uyuşmuyor: {commits} commit / {series} seri "
            f"(beklenen {EXPECTED_COMMITS} / {EXPECTED_SERIES})"
        )
    return None


def main(
    argv: Sequence[str] | None = None,
    *,
    forecaster_factory: Callable[[], Forecaster] | None = None,
    loader: Callable[..., dict[str, pd.Series]] | None = None,
    revision: Callable[[], tuple[str, pd.Timestamp]] | None = None,
) -> int:
    args = _parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    config = load_config(args.config)
    layer = resolve_layer(config, LAYER)
    universe = universe_of(layer.symbols or [])
    cache_dir = args.cache_dir or tempfile.mkdtemp(prefix="timesfm-")
    run_config = {**layer.config, "data": {**layer.config["data"], "cache_dir": str(cache_dir)}}

    try:
        snapshots = read_hemstir(Path(args.hemstir))
    except (subprocess.CalledProcessError, FileNotFoundError, KeyError, json.JSONDecodeError) as exc:
        logger.error("hemstir geçmişi okunamadı: %s", exc)
        return 2
    problem = check_counts(snapshots)
    if problem:
        logger.error(problem)
        return 2

    try:
        sha, modified = (revision or checkpoint_revision)()
    except Exception as exc:  # noqa: BLE001
        logger.error("checkpoint revizyonu okunamadı (C_start belirlenemez): %s", exc)
        return 1

    ctx = Context(
        config=run_config,
        universe=universe,
        snapshots=snapshots,
        forecaster=(forecaster_factory or TimesFMForecaster)(),
        load=loader or load_closes,
        now=pd.Timestamp(args.now) if args.now else pd.Timestamp.now(tz="UTC").floor("h"),
        hf_last_modified=modified,
        alpha=float(get_setting(config, "acceptance.edge_ci_alpha")),
        samples=int(get_setting(config, "acceptance.bootstrap_samples")),
        seed=int(get_setting(config, "random_seed")),
    )
    manifest = {
        "stage": args.stage,
        "preregistration": "docs/backtest.md > 6k",
        "hemstir_pin": HEMSTIR_PIN,
        "hemstir_commits": len(snapshots),
        "hemstir_series": sum(len(s.series) for s in snapshots),
        "checkpoint": CHECKPOINT,
        "checkpoint_sha": sha,
        "checkpoint_last_modified": modified.isoformat(),
        "c_start": c_start(modified).isoformat(),
        "forecast_config": dict(FORECAST_CONFIG),
        "versions": versions(),
        "universe": universe,
        "now": ctx.now.isoformat(),
        "cache_dir": str(cache_dir),
    }
    if manifest["versions"].get("timesfm") not in (TIMESFM_PIN, "yok"):
        logger.warning("timesfm sürümü ön-kayıttan farklı: %s", manifest["versions"]["timesfm"])

    payload: dict[str, Any] = {"manifest": manifest}
    try:
        payload["parity"] = parity(ctx)
        if args.stage == "evaluate" and payload["parity"]["passed"]:
            after_sha, _ = (revision or checkpoint_revision)()
            if after_sha != sha:
                logger.error("checkpoint koşu sırasında değişti: %s -> %s", sha, after_sha)
                return 1
            payload["evaluation"] = evaluate(ctx)
    except DataGate as exc:
        logger.error("VERİ KAPISI: %s", exc)
        return 3
    payload["verdict"] = verdict(payload)
    logger.info("KARAR: %s", payload["verdict"])

    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / "results.json").write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str))
    return 0


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--stage", choices=("parity", "evaluate"), required=True)
    parser.add_argument("--hemstir", required=True, help="klonnist/hemstir klonu (HEMSTIR_PIN'i taşımalı)")
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--config", default=None)
    parser.add_argument("--cache-dir", default=None, help="varsayılan: koşuya özel geçici dizin")
    parser.add_argument("--now", default=None, help="yeniden üretim için; varsayılan: koşu anı")
    return parser.parse_args(argv)


if __name__ == "__main__":
    raise SystemExit(main())

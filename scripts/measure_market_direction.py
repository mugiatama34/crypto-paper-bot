#!/usr/bin/env python3
"""Piyasa yönü teşhisi (docs/backtest.md > 6m). TEŞHİS, KARAR DEĞİL; ölçümün parçası DEĞİL.

Ön-kayıt (`71dfe3f8`) bu betikten ÖNCE, hiçbir veri görülmeden commit edildi. Betik o metni
MEKANİK uygular: kaynaklar, işlem başına hiza, M1–M5, hafta kümeli bootstrap, kontrol
çiftleri. Hiçbir sayı burada SEÇİLMEZ ve çıktı hiçbir satırda `passed`, kapı ya da etiket
ÜRETMEZ (§6m > 1).

**İkinci bir tanım YOK:** pozisyon ve R `core/metrics.py::merge_fills` + `r_multiple`tan,
pencere fiyatı `core/metrics.py::_price_at_or_before`dan (yani `_market_context`in kuralı),
günlük BTC kapanışı `scripts/measure_regime.py::daily_closes`tan, eşleştirilmiş küme
çekilişleri `scripts/backtest_dc.py::cluster_diff_draws`tan gelir. Burada hesaplanan yalnızca
§6m'nin kendi ölçüleri: hiza, günlük OLS ve hafta bootstrap'ı, haftalık tablo, olay kümesi,
ICC ve çift korelasyonu.

**Salt okunur:** deftere, config'e ve `data/cache/`e yazmaz (mumlar koşuya özel geçici
önbellekten). Kullanılan her fiyat ve günlük BTC serisi çıktıya SABİTLENİR.

İKİ AŞAMA: `preflight` pins SHA256'sını, defterlerin varlığını, mum kapsamını ve FİYAT
KAPISINI (§6m > 4; yalnızca giriş fiyatı ↔ açılış) raporlar — hiçbir R, getiri, hiza ya da
beta üretmez ve tekrarlanabilir. `measure` tek seferliktir (§6m > 6).

Çıkış kodları: 0 = rapor yazıldı, hiçbir kapı düşmedi; 3 = en az bir (kaynak, dönem) bir
veri kapısından düştü (pins SHA, fiyat kapısı, eksik defter/mum) — DÜŞMEYENLERİN raporu yine
yazılır, düşenler raporda sebebiyle durur (§6m > 3: bir kaynağın kapısı ötekileri rehin
almaz); 2 = kullanım hatası. Tetikleyicisi `.github/workflows/measure-market-direction.yml`.
"""

from __future__ import annotations

import argparse
import copy
import csv
import gzip
import hashlib
import json
import logging
import math
import random
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from core.config import get_setting, load_config  # noqa: E402
from core.data import bar_duration, fetch_ohlcv, okx_bar  # noqa: E402
from core.layers import resolve_layer  # noqa: E402
from core.ledger import Ledger  # noqa: E402
from core.metrics import _price_at_or_before, merge_fills, normalize_reference, r_multiple  # noqa: E402
from scripts.backtest_dc import MIN_CLUSTERS, _percentiles, cluster_diff_draws  # noqa: E402
from scripts.measure_regime import daily_closes  # noqa: E402

logger = logging.getLogger("measure_market_direction")

# --- Ön-kayıtlı sayılar (§6m). Hiçbiri CLI girdisi DEĞİLDİR. -------------------------------
NEUTRAL_EPS = 1e-12            # §6m > 4: |getiri| bunun altındaysa pozisyon NÖTR
PRICE_TOL = 1e-6               # §6m > 4: fiyat kapısının göreli toleransı
PRICE_GATE_MAX_FAIL = 0.01     # §6m > 4: tutmayan pozisyon oranı bunu AŞARSA (kaynak, dönem) düşer
EARLIEST_BACKTEST_DAY = pd.Timestamp("2021-12-01T00:00:00Z")   # dönem A'nın ilk haftasının öncesi
PERIODS = ("A", "B")
DIRECTIONS = ("long", "short", "all")
DEFINITIONS = ("btc", "coin")   # okumada ağırlığı `btc` taşır (§6m > 4)

CONTROL_BROKEN_NOTE = "⚠ karar 60: kontrolün çıkışı yalnızca stop, R'si sansürlü"


class DataGateError(RuntimeError):
    """Bir (kaynak, dönem) ölçülemiyor: o kaynağın raporu yazılmaz, çıkış 3 (karar 51)."""


# --------------------------------------------------------------------------- #
# Kaynaklar (§6m > 3)
# --------------------------------------------------------------------------- #
@dataclass(frozen=True, kw_only=True)
class Pair:
    model: str
    control: str
    note: str = ""


@dataclass(kw_only=True)
class Source:
    key: str                          # "ema" | "dc" | "xsec" | "live-base" | "live-scalp"
    period: str                       # "A" | "B" | "live"
    root: Path                        # defter kökü (içinde <model>/trades.csv)
    models: list[str]
    pairs: list[Pair]
    layer: str                        # mumların barını veren katman
    slippage: float                   # fiyat kapısının kullandığı giriş kayması
    day_start: pd.Timestamp | None = None   # None = defterin ilk equity günü
    day_end: pd.Timestamp | None = None     # None = defterin son equity günü
    last_close_extends: bool = False        # §6m > 5 M2: pencere son closed_at gününe uzar
    labels: dict[str, str] = field(default_factory=dict)   # model -> "kopya" gibi açıklama
    gate_failures: list[str] = field(default_factory=list)

    @property
    def name(self) -> str:
        return f"{self.key}|{self.period}"


def verify_and_unpack_pins(pins_dir: Path, dest: Path) -> list[str]:
    """SHA256SUMS'taki her dosyayı açar ve SIKIŞTIRILMAMIŞ içeriğin özetini doğrular.

    Dönen liste tutmayan/eksik dosyalardır (boş = hepsi tuttu). Tutmayan dosya diske
    yazılmaz: yanlış bir defteri okumaktansa o kaynak ölçülmez.
    """
    problems: list[str] = []
    verified = 0
    sums = pins_dir / "SHA256SUMS"
    if not sums.is_file():
        return [f"{sums} yok"]
    for line in sums.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        digest, rel = line.split(None, 1)
        rel = rel.strip()
        gz = pins_dir / f"{rel}.gz"
        if not gz.is_file():
            problems.append(f"{rel}: dosya yok")
            continue
        raw = gzip.decompress(gz.read_bytes())
        if hashlib.sha256(raw).hexdigest() != digest:
            problems.append(f"{rel}: SHA256 tutmuyor")
            continue
        target = dest / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)
        verified += 1
    if verified == 0 and not problems:
        problems.append(f"{sums}: hiçbir dosya listelenmemiş")
    return problems


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _stamp(value: Any) -> pd.Timestamp | None:
    if value in (None, ""):
        return None
    stamp = pd.Timestamp(value)
    return stamp.tz_localize("UTC") if stamp.tzinfo is None else stamp.tz_convert("UTC")


def backtest_source(key: str, period: str, run_dir: Path, *, layer: str, models: list[str],
                    pairs: list[Pair], config_slippage: float) -> Source:
    """Bir backtest koşusu -> Source. Pencere ve kayma koşunun KENDİ manifest'inden okunur."""
    manifest = _read_json(run_dir / "manifest.json") or {}
    window = manifest.get("window") or {}
    deviations = manifest.get("deviations") or {}
    costs = deviations.get("costs") or {}
    slippage = costs.get("run_slippage_base", config_slippage)
    start = _stamp(window.get("start"))
    cutoff = _stamp(deviations.get("signal_cutoff"))
    end = cutoff if cutoff is not None else _stamp(window.get("end"))
    return Source(
        key=key, period=period, root=run_dir / "ledger", models=models, pairs=pairs, layer=layer,
        slippage=float(slippage),
        day_start=start.floor("D") if start is not None else None,
        day_end=end.floor("D") if end is not None else None,
        last_close_extends=True,
    )


def live_models(root: Path) -> list[str]:
    """Kapanmış satırı olan HER defter (emekliler dâhil); sıra adla sabit."""
    out = []
    for directory in sorted(p for p in root.iterdir() if p.is_dir()):
        trades = directory / "trades.csv"
        if trades.is_file() and len(trades.read_text(encoding="utf-8").splitlines()) > 1:
            out.append(directory.name)
    return out


def build_sources(args: argparse.Namespace, config: Mapping[str, Any], pins_root: Path | None,
                  *, include_xsec: bool) -> list[Source]:
    slippage = float(get_setting(config, "slippage_base"))
    sources: list[Source] = []
    if pins_root is not None:
        for period in PERIODS:
            sources.append(backtest_source(
                "ema", period, pins_root / "ema" / f"{period}-portfolio", layer="ema",
                models=["ema_trend", "trend", "random_ctrl"],
                pairs=[Pair(model="ema_trend", control="random_ctrl", note=CONTROL_BROKEN_NOTE),
                       Pair(model="trend", control="random_ctrl", note=CONTROL_BROKEN_NOTE)],
                config_slippage=slippage))
            sources.append(backtest_source(
                "dc", period, pins_root / "dc" / f"{period}-portfolio", layer="dc",
                models=["dc_short", "dc_coinflip"],
                pairs=[Pair(model="dc_short", control="dc_coinflip")], config_slippage=slippage))
    if include_xsec:
        for period in PERIODS:
            sources.append(backtest_source(
                "xsec", period, Path(args.xsec_dir) / period, layer="xsec",
                models=["xsec_mom", "xsec_random"],
                pairs=[Pair(model="xsec_mom", control="xsec_random")], config_slippage=slippage))
    live_base = Path(args.live_base)
    if live_base.is_dir():
        models = live_models(live_base)
        pairs = [Pair(model="trend", control="random_ctrl", note=CONTROL_BROKEN_NOTE)] \
            if {"trend", "random_ctrl"} <= set(models) else []
        sources.append(Source(key="live-base", period="live", root=live_base, models=models,
                              pairs=pairs, layer="base", slippage=slippage))
    live_scalp = Path(args.live_scalp)
    if live_scalp.is_dir():
        models = live_models(live_scalp)
        control = "scalp_coinflip"
        pairs = [Pair(model=m, control=control) for m in models if m != control] \
            if control in models else []
        sources.append(Source(key="live-scalp", period="live", root=live_scalp, models=models,
                              pairs=pairs, layer="scalp", slippage=slippage,
                              labels={"vwap_clone": "kopya (is_replica)"}))
    return sources


# --------------------------------------------------------------------------- #
# Pozisyonlar ve mumlar
# --------------------------------------------------------------------------- #
@dataclass(kw_only=True)
class Pos:
    model: str
    symbol: str
    direction: str
    opened_at: pd.Timestamp
    closed_at: pd.Timestamp
    entry_price: float
    r: float | None
    pnl: float
    coin_ret: float | None = None
    btc_ret: float | None = None

    @property
    def sign(self) -> float:
        return 1.0 if self.direction == "long" else -1.0


def load_positions(root: Path, model: str) -> list[Pos]:
    ledger = Ledger(root)
    out: list[Pos] = []
    for row in merge_fills(ledger.read_trades(model)):
        opened, closed = _stamp(row.get("opened_at")), _stamp(row.get("closed_at"))
        if opened is None or closed is None:
            continue
        out.append(Pos(
            model=model, symbol=str(row["symbol"]), direction=str(row["direction"]),
            opened_at=opened, closed_at=closed, entry_price=float(row["entry_price"]),
            r=r_multiple(row), pnl=float(row.get("pnl") or 0.0),
        ))
    out.sort(key=lambda p: (p.opened_at, p.symbol, p.direction))
    return out


def load_equity(root: Path, model: str) -> pd.DataFrame:
    rows = Ledger(root).read_equity(model)
    if not rows:
        return pd.DataFrame(columns=["equity", "open_positions"])
    frame = pd.DataFrame(rows)
    index = pd.DatetimeIndex(pd.to_datetime(frame["ts"], utc=True))
    return pd.DataFrame(
        {"equity": pd.to_numeric(frame["equity"]).to_numpy(),
         "open_positions": pd.to_numeric(frame["open_positions"]).to_numpy()},
        index=index,
    ).sort_index()


class PriceBook:
    """(katman barı, sembol) -> açılış/kapanış serisi; okunan HER fiyat kaydedilir (sabitleme)."""

    def __init__(self, frames: Mapping[tuple[str, str], pd.DataFrame]) -> None:
        self._opens: dict[tuple[str, str], pd.Series] = {}
        self._closes: dict[tuple[str, str], pd.Series] = {}
        for key, frame in frames.items():
            if frame is None or frame.empty:
                continue
            self._opens[key] = normalize_reference(frame["open"])
            self._closes[key] = normalize_reference(frame["close"])
        self.used: set[tuple[str, str, pd.Timestamp]] = set()

    def has(self, bar: str, symbol: str) -> bool:
        return (bar, symbol) in self._closes

    def closes(self, bar: str, symbol: str) -> pd.Series | None:
        return self._closes.get((bar, symbol))

    def close_at_or_before(self, bar: str, symbol: str, when: pd.Timestamp) -> float | None:
        series = self._closes.get((bar, symbol))
        if series is None:
            return None
        position = series.index.searchsorted(when, side="right") - 1
        if position >= 0:
            self.used.add((bar, symbol, series.index[position]))
        return _price_at_or_before(series, when)

    def open_at(self, bar: str, symbol: str, when: pd.Timestamp) -> float | None:
        series = self._opens.get((bar, symbol))
        if series is None or when not in series.index:
            return None
        self.used.add((bar, symbol, when))
        return float(series.loc[when])

    def rows(self) -> list[dict[str, Any]]:
        out = []
        for bar, symbol, stamp in sorted(self.used, key=lambda t: (t[0], t[1], t[2])):
            out.append({"bar": bar, "symbol": symbol, "ts": stamp.isoformat(),
                        "open": float(self._opens[(bar, symbol)].loc[stamp]),
                        "close": float(self._closes[(bar, symbol)].loc[stamp])})
        return out


def window_return(book: PriceBook, bar: str, symbol: str,
                  start: pd.Timestamp, end: pd.Timestamp) -> float | None:
    """`_market_context`in kuralı: kapanış(≤ start) -> kapanış(≤ end), ham getiri."""
    first = book.close_at_or_before(bar, symbol, start)
    last = book.close_at_or_before(bar, symbol, end)
    if first is None or last is None or first <= 0.0:
        return None
    return last / first - 1.0


def price_gate(positions: Sequence[Pos], book: PriceBook, bar: str, slippage: float) -> dict[str, Any]:
    """Giriş fiyatı ↔ dolum barının açılışı (kayma geri çıkarılarak). R'ye BAKMAZ."""
    checked = failed = missing = 0
    examples: list[str] = []
    for pos in positions:
        reference = book.open_at(bar, pos.symbol, pos.opened_at)
        if reference is None:
            missing += 1
            if len(examples) < 5:
                examples.append(f"{pos.symbol} {pos.opened_at.isoformat()}: açılış mumu yok")
            continue
        implied = pos.entry_price / (1.0 + slippage) if pos.direction == "long" \
            else pos.entry_price / (1.0 - slippage)
        checked += 1
        if abs(implied - reference) > PRICE_TOL * abs(reference):
            failed += 1
            if len(examples) < 5:
                examples.append(f"{pos.symbol} {pos.opened_at.isoformat()}: defter {implied:.10g} ↔ mum {reference:.10g}")
    total = checked + missing
    share = (failed + missing) / total if total else 0.0
    return {"positions": total, "checked": checked, "failed": failed, "missing_candle": missing,
            "fail_share": share, "passed": share <= PRICE_GATE_MAX_FAIL, "slippage": slippage,
            "examples": examples}


# --------------------------------------------------------------------------- #
# M1 — hiza (§6m > 5)
# --------------------------------------------------------------------------- #
def alignment(ret: float | None, direction: str) -> str | None:
    """"aligned" | "misaligned" | "neutral"; getiri yoksa None (ölçülmedi)."""
    if ret is None:
        return None
    if abs(ret) < NEUTRAL_EPS:
        return "neutral"
    return "aligned" if (ret > 0.0) == (direction == "long") else "misaligned"


def _mean(values: Sequence[float]) -> float | None:
    return float(sum(values) / len(values)) if values else None


def _group_stats(positions: Sequence[Pos]) -> dict[str, Any]:
    rs = [p.r for p in positions if p.r is not None]
    return {"n": len(positions),
            "win_rate": _mean([1.0 if p.pnl > 0.0 else 0.0 for p in positions]),
            "avg_r": _mean(rs)}


def m1_alignment(positions: Sequence[Pos]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for definition in DEFINITIONS:
        block: dict[str, Any] = {}
        for direction in DIRECTIONS:
            subset = [p for p in positions if direction == "all" or p.direction == direction]
            groups: dict[str, list[Pos]] = {"aligned": [], "misaligned": [], "neutral": []}
            unmeasured = 0
            for pos in subset:
                ret = pos.btc_ret if definition == "btc" else pos.coin_ret
                label = alignment(ret, pos.direction)
                if label is None:
                    unmeasured += 1
                else:
                    groups[label].append(pos)
            decided = len(groups["aligned"]) + len(groups["misaligned"])
            block[direction] = {
                "aligned": len(groups["aligned"]),
                "misaligned": len(groups["misaligned"]),
                "neutral": len(groups["neutral"]),
                "unmeasured": unmeasured,
                "aligned_share": len(groups["aligned"]) / decided if decided else None,
                "aligned_group": _group_stats(groups["aligned"]),
                "misaligned_group": _group_stats(groups["misaligned"]),
            }
        out[definition] = block
    return out


# --------------------------------------------------------------------------- #
# M2 — günlük beta, R², alfa (§6m > 5)
# --------------------------------------------------------------------------- #
def iso_week(day: pd.Timestamp) -> pd.Timestamp:
    """ISO haftanın başı: Pazartesi 00:00 UTC."""
    day = day.tz_convert("UTC") if day.tzinfo else day.tz_localize("UTC")
    return (day - pd.Timedelta(days=day.weekday())).floor("D")


def day_window(source: Source, equity: pd.DataFrame, positions: Sequence[Pos]) -> tuple[pd.Timestamp, pd.Timestamp] | None:
    if equity.empty:
        return None
    first = equity.index[0].floor("D")
    last = equity.index[-1].floor("D")
    start = max(first, source.day_start) if source.day_start is not None else first
    end = source.day_end if source.day_end is not None else last
    if source.last_close_extends and positions:
        end = max(end, max(p.closed_at for p in positions).floor("D"))
    end = min(end, last)
    return (start, end) if start <= end else None


def daily_frame(equity: pd.DataFrame, btc_daily: pd.Series,
                window: tuple[pd.Timestamp, pd.Timestamp]) -> tuple[pd.DataFrame, dict[str, int]]:
    """Gün başına (r_model, r_btc, pozisyonda mı). Pozisyonsuz günler DÂHİL (§6m > 5)."""
    start, end = window
    days = pd.date_range(start, end, freq="D", tz="UTC")
    eq_day = equity["equity"].groupby(equity.index.floor("D")).last()
    exposed = (equity["open_positions"] > 0).groupby(equity.index.floor("D")).any()
    eq_full = eq_day.reindex(pd.date_range(start - pd.Timedelta(days=1), end, freq="D", tz="UTC"))
    r_model = (eq_full / eq_full.shift(1) - 1.0).reindex(days)
    btc_full = btc_daily.reindex(pd.date_range(start - pd.Timedelta(days=1), end, freq="D", tz="UTC"))
    r_btc = (btc_full / btc_full.shift(1) - 1.0).reindex(days)
    frame = pd.DataFrame({"r_model": r_model, "r_btc": r_btc,
                          "exposed": exposed.reindex(days).fillna(False).astype(bool)}, index=days)
    usable = frame.dropna(subset=["r_model", "r_btc"])
    counts = {"days_total": int(len(frame)), "days_used": int(len(usable)),
              "days_missing_model": int(frame["r_model"].isna().sum()),
              "days_missing_btc": int(frame["r_btc"].isna().sum()),
              "days_exposed": int(frame["exposed"].sum())}
    return usable, counts


def ols(x: np.ndarray, y: np.ndarray) -> tuple[float, float, float] | None:
    """(alfa, beta, R²); x'in varyansı sıfırsa None."""
    if len(x) < 3:
        return None
    xm, ym = float(x.mean()), float(y.mean())
    sxx = float(((x - xm) ** 2).sum())
    if sxx <= 0.0:
        return None
    sxy = float(((x - xm) * (y - ym)).sum())
    syy = float(((y - ym) ** 2).sum())
    beta = sxy / sxx
    alpha = ym - beta * xm
    r2 = (sxy * sxy) / (sxx * syy) if syy > 0.0 else float("nan")
    return alpha, beta, r2


def _week_groups(frame: pd.DataFrame) -> dict[pd.Timestamp, np.ndarray]:
    weeks = frame.index.map(iso_week)
    return {week: np.flatnonzero(weeks == week) for week in sorted(set(weeks))}


def m2_regression(frame: pd.DataFrame, counts: Mapping[str, int], *, iterations: int, alpha: float,
                  seed: str) -> dict[str, Any]:
    point = ols(frame["r_btc"].to_numpy(), frame["r_model"].to_numpy()) if len(frame) else None
    groups = _week_groups(frame) if len(frame) else {}
    out: dict[str, Any] = {
        **counts,
        "exposure_share": counts["days_exposed"] / counts["days_total"] if counts["days_total"] else None,
        "weeks": len(groups),
        "alpha_daily_pct": point[0] * 100.0 if point else None,
        "alpha_annual_pct": point[0] * 365.0 * 100.0 if point else None,
        "beta": point[1] if point else None,
        "r2": point[2] if point else None,
        "ci_evaluable": False,
    }
    if point is None or len(groups) < MIN_CLUSTERS:
        return out
    ids = list(groups)
    x, y = frame["r_btc"].to_numpy(), frame["r_model"].to_numpy()
    rng = random.Random(seed)
    draws: list[tuple[float, float, float]] = []
    dropped = 0
    for _ in range(iterations):
        rows = np.concatenate([groups[ids[rng.randrange(len(ids))]] for _ in ids])
        fit = ols(x[rows], y[rows])
        if fit is None or math.isnan(fit[2]):
            dropped += 1
            continue
        draws.append(fit)
    if len(draws) < iterations // 2:
        out["dropped_draws"] = dropped
        return out
    a_lo, a_hi = _percentiles([d[0] for d in draws], alpha)
    b_lo, b_hi = _percentiles([d[1] for d in draws], alpha)
    r_lo, r_hi = _percentiles([d[2] for d in draws], alpha)
    out.update({
        "ci_evaluable": True, "dropped_draws": dropped,
        "alpha_daily_pct_ci": [a_lo * 100.0, a_hi * 100.0],
        "alpha_annual_pct_ci": [a_lo * 36500.0, a_hi * 36500.0],
        "beta_ci": [b_lo, b_hi],
        "r2_ci": [r_lo, r_hi],
    })
    return out


def paired_regression_diff(model: pd.DataFrame, control: pd.DataFrame, *, iterations: int,
                           alpha: float, seed: str) -> dict[str, Any]:
    """β ve α farkı (model − kontrol), EŞLEŞTİRİLMİŞ hafta bootstrap'ı: haftalar birlikte çekilir."""
    mg, cg = _week_groups(model), _week_groups(control)
    ids = sorted(set(mg) | set(cg))
    out: dict[str, Any] = {"weeks": len(ids), "ci_evaluable": False}
    pm = ols(model["r_btc"].to_numpy(), model["r_model"].to_numpy()) if len(model) else None
    pc = ols(control["r_btc"].to_numpy(), control["r_model"].to_numpy()) if len(control) else None
    if pm is None or pc is None:
        return out
    out.update({"beta_diff": pm[1] - pc[1], "alpha_daily_pct_diff": (pm[0] - pc[0]) * 100.0})
    if len(ids) < MIN_CLUSTERS:
        return out
    mx, my = model["r_btc"].to_numpy(), model["r_model"].to_numpy()
    cx, cy = control["r_btc"].to_numpy(), control["r_model"].to_numpy()
    empty = np.array([], dtype=int)
    rng = random.Random(seed)
    betas: list[float] = []
    alphas: list[float] = []
    dropped = 0
    for _ in range(iterations):
        picks = [ids[rng.randrange(len(ids))] for _ in ids]
        m_rows = np.concatenate([mg.get(w, empty) for w in picks])
        c_rows = np.concatenate([cg.get(w, empty) for w in picks])
        fm, fc = ols(mx[m_rows], my[m_rows]), ols(cx[c_rows], cy[c_rows])
        if fm is None or fc is None:
            dropped += 1
            continue
        betas.append(fm[1] - fc[1])
        alphas.append((fm[0] - fc[0]) * 100.0)
    if len(betas) < iterations // 2:
        out["dropped_draws"] = dropped
        return out
    out.update({"ci_evaluable": True, "dropped_draws": dropped,
                "beta_diff_ci": list(_percentiles(betas, alpha)),
                "alpha_daily_pct_diff_ci": list(_percentiles(alphas, alpha))})
    return out


# --------------------------------------------------------------------------- #
# M3 — haftalık tablo (§6m > 5)
# --------------------------------------------------------------------------- #
def _period_return(series: pd.Series, week: pd.Timestamp, *, fallback: float | None = None) -> float | None:
    """Haftanın son değeri / haftadan önceki son değer − 1.

    Haftadan önce değer yoksa (defterin ilk haftası) `fallback` — equity'de BAŞLANGIÇ
    bakiyesi, yani ilk satır — kullanılır; ilk günün kapanışını taban almak ilk günün
    hareketini haftadan düşürürdü.
    """
    end = week + pd.Timedelta(days=7)
    inside = series[(series.index >= week) & (series.index < end)].dropna()
    if inside.empty:
        return None
    before = series[series.index < week].dropna()
    if not before.empty:
        base = float(before.iloc[-1])
    elif fallback is not None:
        base = float(fallback)
    else:
        return None
    return float(inside.iloc[-1]) / base - 1.0 if base > 0.0 else None


def m3_weekly(positions: Sequence[Pos], equity: pd.DataFrame, btc_daily: pd.Series,
              window: tuple[pd.Timestamp, pd.Timestamp]) -> list[dict[str, Any]]:
    start, end = window
    weeks = pd.date_range(iso_week(start), iso_week(end), freq="7D", tz="UTC")
    eq_day = equity["equity"].groupby(equity.index.floor("D")).last()
    first_equity = float(equity["equity"].iloc[0]) if not equity.empty else None
    rows = []
    for week in weeks:
        opened = [p for p in positions if iso_week(p.opened_at) == week]
        n = len(opened)
        rs = [p.r for p in opened if p.r is not None]
        rows.append({
            "week_start": str(week.date()),
            "n_opened": n,
            "long_share": sum(1 for p in opened if p.direction == "long") / n if n else None,
            "short_share": sum(1 for p in opened if p.direction == "short") / n if n else None,
            "btc_week_ret": _period_return(btc_daily, week),
            "model_week_ret": _period_return(eq_day, week, fallback=first_equity),
            "avg_r": _mean(rs),
        })
    return rows


# --------------------------------------------------------------------------- #
# M4 — eşzamanlılık (§6m > 5)
# --------------------------------------------------------------------------- #
def overlaps(a: Pos, b: Pos) -> bool:
    return a.opened_at < b.closed_at and b.opened_at < a.closed_at


def events(positions: Sequence[Pos]) -> list[list[Pos]]:
    """Tutuş pencereleri örtüşen pozisyonların bağlı bileşenleri (sembolden bağımsız)."""
    ordered = sorted(positions, key=lambda p: (p.opened_at, p.closed_at))
    out: list[list[Pos]] = []
    current: list[Pos] = []
    reach: pd.Timestamp | None = None
    for pos in ordered:
        if current and reach is not None and pos.opened_at < reach:
            current.append(pos)
            reach = max(reach, pos.closed_at)
        else:
            if current:
                out.append(current)
            current, reach = [pos], pos.closed_at
    if current:
        out.append(current)
    return out


def icc_oneway(groups: Sequence[Sequence[float]]) -> float | None:
    """Tek yönlü ANOVA ICC(1), eşit olmayan grup boyu için n₀ düzeltmesi. Negatif olduğu gibi."""
    groups = [list(g) for g in groups if g]
    k = len(groups)
    total = sum(len(g) for g in groups)
    if k < 2 or total - k < 1:
        return None
    grand = sum(sum(g) for g in groups) / total
    msb = sum(len(g) * (sum(g) / len(g) - grand) ** 2 for g in groups) / (k - 1)
    msw = sum(sum((v - sum(g) / len(g)) ** 2 for v in g) for g in groups) / (total - k)
    n0 = (total - sum(len(g) ** 2 for g in groups) / total) / (k - 1)
    denominator = msb + (n0 - 1.0) * msw
    if denominator <= 0.0:
        return None
    return (msb - msw) / denominator


def _pearson(x: Sequence[float], y: Sequence[float]) -> float | None:
    if len(x) < 3:
        return None
    xa, ya = np.asarray(x, dtype="float64"), np.asarray(y, dtype="float64")
    if xa.std() == 0.0 or ya.std() == 0.0:
        return None
    return float(np.corrcoef(xa, ya)[0, 1])


def m4_concurrency(positions: Sequence[Pos], book: PriceBook, bar: str, bar_length: pd.Timedelta) -> dict[str, Any]:
    clusters = events(positions)
    sizes = [len(c) for c in clusters]
    measured = [p for p in positions if p.r is not None]
    icc = icc_oneway([[p.r for p in c if p.r is not None] for c in clusters])
    n = len(measured)
    mean_size = (sum(sizes) / len(sizes)) if sizes else None
    n_eff = (n / (1.0 + (mean_size - 1.0) * max(icc, 0.0))) if (icc is not None and mean_size) else None

    ordered = sorted(positions, key=lambda p: p.opened_at)
    xs: list[float] = []
    ys: list[float] = []
    pairs = short_overlap = unpriced = same_direction = same_symbol = 0
    for i, a in enumerate(ordered):
        for b in ordered[i + 1:]:
            if b.opened_at >= a.closed_at:
                break
            if not overlaps(a, b):
                continue
            pairs += 1
            start, end = max(a.opened_at, b.opened_at), min(a.closed_at, b.closed_at)
            if end - start < bar_length:
                short_overlap += 1
                continue
            ra = window_return(book, bar, a.symbol, start, end)
            rb = window_return(book, bar, b.symbol, start, end)
            if ra is None or rb is None:
                unpriced += 1
                continue
            xs.append(a.sign * ra)
            ys.append(b.sign * rb)
            same_direction += int(a.direction == b.direction)
            same_symbol += int(a.symbol == b.symbol)
    used = len(xs)
    return {
        "positions": len(positions), "events": len(clusters),
        "mean_event_size": mean_size, "max_event_size": max(sizes) if sizes else None,
        "icc_r": icc, "n_effective": n_eff,
        "overlap_pairs": pairs, "pairs_short_overlap": short_overlap, "pairs_unpriced": unpriced,
        "pairs_used": used, "pair_corr": _pearson(xs, ys),
        "same_direction_share": same_direction / used if used else None,
        "same_symbol_pairs": same_symbol,
    }


# --------------------------------------------------------------------------- #
# M5 — kontrol farkı: hizalı pay (§6m > 5)
# --------------------------------------------------------------------------- #
def aligned_week_groups(positions: Sequence[Pos], definition: str) -> dict[str, list[float]]:
    """Hafta -> hizalı göstergeleri (1/0); nötr ve ölçülmeyen dışarıda. Ortalama = hizalı pay."""
    groups: dict[str, list[float]] = {}
    for pos in positions:
        ret = pos.btc_ret if definition == "btc" else pos.coin_ret
        label = alignment(ret, pos.direction)
        if label in ("aligned", "misaligned"):
            groups.setdefault(str(iso_week(pos.opened_at).date()), []).append(1.0 if label == "aligned" else 0.0)
    return groups


def aligned_share_diff(model: Sequence[Pos], control: Sequence[Pos], definition: str, *,
                       iterations: int, alpha: float, seed: str) -> dict[str, Any]:
    mg, cg = aligned_week_groups(model, definition), aligned_week_groups(control, definition)
    ids = set(mg) | set(cg)
    m_all = [v for vs in mg.values() for v in vs]
    c_all = [v for vs in cg.values() for v in vs]
    out: dict[str, Any] = {"weeks": len(ids), "ci_evaluable": False,
                           "diff": (_mean(m_all) - _mean(c_all)) if m_all and c_all else None}
    if not m_all or not c_all or len(ids) < MIN_CLUSTERS:
        return out
    draws, dropped = cluster_diff_draws(mg, cg, iterations=iterations, seed=seed)
    if len(draws) < iterations:
        out["dropped_draws"] = dropped
        return out
    out.update({"ci_evaluable": True, "dropped_draws": dropped, "ci": list(_percentiles(draws, alpha))})
    return out


# --------------------------------------------------------------------------- #
# Koşu
# --------------------------------------------------------------------------- #
@dataclass(frozen=True, kw_only=True)
class Settings:
    iterations: int
    alpha: float
    seed: int
    min_trades: int


def seed_for(settings: Settings, source: Source, model: str, metric: str) -> str:
    return f"{settings.seed}:mdir:{source.key}:{source.period}:{model}:{metric}"


def layer_bar(config: Mapping[str, Any], layer: str) -> str:
    return okx_bar(resolve_layer(config, layer).config)


def fetch_prices(config: Mapping[str, Any], needs: Mapping[tuple[str, str], pd.Timestamp],
                 bar_layers: Mapping[str, str], *, now: pd.Timestamp, cache_dir: str,
                 fetcher: Callable[..., pd.DataFrame] | None = None) -> dict[tuple[str, str], pd.DataFrame]:
    """(bar, sembol) -> mumlar; derinlik istenen en erken damgadan türetilir, SEÇİLMEZ.

    Anahtar KATMAN değil BARDIR: 4H'lik üç katman aynı seriyi okur ve katman başına ayrı
    çekim, sığ bir çekimin derin olanı ezmesi demekti.
    """
    fetch = fetcher if fetcher is not None else fetch_ohlcv
    frames: dict[tuple[str, str], pd.DataFrame] = {}
    for (bar, symbol), earliest in sorted(needs.items()):
        layer_config = copy.deepcopy(dict(resolve_layer(config, bar_layers[bar]).config))
        bars = int(math.ceil((now - earliest) / bar_duration(bar))) + 12
        layer_config["data"] = {**layer_config["data"], "history_bars": bars, "cache_dir": cache_dir}
        try:
            frames[(bar, symbol)] = fetch(layer_config, symbol, now=now)
        except Exception as exc:  # noqa: BLE001 — mumu olmayan sembolün pozisyonları ölçülmez, SAYILIR
            logger.error("%s %s mumları çekilemedi: %s", bar, symbol, exc)
            frames[(bar, symbol)] = pd.DataFrame()
    return frames


def price_needs(sources: Sequence[Source], positions: Mapping[tuple[str, str], list[Pos]],
                config: Mapping[str, Any], btc_symbol: str) -> tuple[dict[tuple[str, str], pd.Timestamp], dict[str, str]]:
    needs: dict[tuple[str, str], pd.Timestamp] = {}
    bar_layers: dict[str, str] = {}

    def want(layer: str, symbol: str, stamp: pd.Timestamp) -> None:
        bar = layer_bar(config, layer)
        bar_layers.setdefault(bar, layer)
        key = (bar, symbol)
        needs[key] = min(needs.get(key, stamp), stamp)

    for source in sources:
        for model in source.models:
            for pos in positions.get((source.name, model), []):
                margin = pos.opened_at - pd.Timedelta(days=2)
                want(source.layer, pos.symbol, margin)
                want(source.layer, btc_symbol, margin)
    # Günlük BTC serisi (M2/M3) her zaman 4H'den: dönem A'nın ilk haftasının öncesinden.
    want("ema", btc_symbol, EARLIEST_BACKTEST_DAY)
    return needs, bar_layers


def run(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    acceptance = get_setting(config, "acceptance")
    settings = Settings(iterations=int(acceptance["bootstrap_samples"]),
                        alpha=float(acceptance["edge_ci_alpha"]),
                        seed=int(get_setting(config, "random_seed")),
                        min_trades=int(acceptance["min_trades"]))
    btc_symbol = str(get_setting(config, "exchange.btc_reference"))
    workdir = Path(tempfile.mkdtemp(prefix="mdir-"))
    exit_code = 0

    # --- pins: SHA256 doğrulaması (§6m > 3) -----------------------------------
    pins_root: Path | None = workdir / "pins"
    pin_problems = verify_and_unpack_pins(Path(args.pins_dir), pins_root)
    if pin_problems:
        for problem in pin_problems:
            logger.error("pins: %s", problem)
        pins_root = None
        exit_code = 3

    # --- xsec determinizm kapısı (yalnızca measure; §6l'nin kapısı) ------------
    xsec_status: dict[str, Any] = {"measured": False, "reason": "preflight: xsec yeniden üretilmez"}
    include_xsec = False
    if args.stage == "measure":
        from scripts.measure_regime import xsec_gate  # noqa: PLC0415 — yalnızca bu aşamada gerekir
        regenerated = _read_json(Path(args.xsec_dir) / "results.json")
        original = _read_json(Path(args.xsec_original)) if args.xsec_original else None
        ok, reason = xsec_gate(regenerated, original)
        xsec_status = {"measured": ok, "reason": reason}
        include_xsec = ok
        if not ok:
            logger.error("xsec determinizm kapısı düştü: %s — xsec satırları ölçülmez", reason)
            exit_code = 3

    sources = build_sources(args, config, pins_root, include_xsec=include_xsec)

    # --- defterler -------------------------------------------------------------
    positions: dict[tuple[str, str], list[Pos]] = {}
    equities: dict[tuple[str, str], pd.DataFrame] = {}
    for source in sources:
        for model in list(source.models):
            if not (source.root / model / "trades.csv").is_file():
                source.gate_failures.append(f"{model}: defter yok ({source.root / model})")
                continue
            positions[(source.name, model)] = load_positions(source.root, model)
            equities[(source.name, model)] = load_equity(source.root, model)

    # --- mumlar ------------------------------------------------------------------
    now = pd.Timestamp(args.now) if args.now else pd.Timestamp.now(tz="UTC").floor("h")
    now = now.tz_localize("UTC") if now.tzinfo is None else now
    cache_dir = args.cache_dir or str(workdir / "cache")
    needs, bar_layers = price_needs(sources, positions, config, btc_symbol)
    book = PriceBook(fetch_prices(config, needs, bar_layers, now=now, cache_dir=cache_dir))
    btc_4h = book.closes(layer_bar(config, "ema"), btc_symbol)
    btc_daily = daily_closes(btc_4h.to_frame("close")) if btc_4h is not None else pd.Series(dtype="float64")

    # --- fiyat kapısı (kaynak, dönem başına) ------------------------------------
    gates: dict[str, Any] = {}
    for source in sources:
        bar = layer_bar(config, source.layer)
        all_positions = [p for m in source.models for p in positions.get((source.name, m), [])]
        gate = price_gate(all_positions, book, bar, source.slippage)
        gates[source.name] = gate
        if not gate["passed"]:
            source.gate_failures.append(
                f"fiyat kapısı: {gate['failed'] + gate['missing_candle']}/{gate['positions']} "
                f"(pay {gate['fail_share']:.4f} > {PRICE_GATE_MAX_FAIL})")
        if not book.has(bar, btc_symbol):
            source.gate_failures.append(f"{bar} {btc_symbol} mumu yok")
    if btc_daily.empty:
        for source in sources:
            source.gate_failures.append("günlük BTC serisi yok")
    for source in sources:
        if source.gate_failures:
            exit_code = 3
            for failure in source.gate_failures:
                logger.error("%s: %s", source.name, failure)

    live_commit = args.live_commit or _git_head()
    coverage: dict[str, Any] = {"now": now.isoformat(), "series": {}}
    for (bar, symbol) in sorted(needs):
        series = book.closes(bar, symbol)
        coverage["series"][f"{bar}|{symbol}"] = (
            {"bars": int(len(series)), "first": series.index[0].isoformat(), "last": series.index[-1].isoformat(),
             "needed_from": needs[(bar, symbol)].isoformat()}
            if series is not None else {"bars": 0, "needed_from": needs[(bar, symbol)].isoformat()})

    if args.stage == "preflight":
        report = {
            "stage": "preflight",
            "pins": {"problems": pin_problems, "verified": not pin_problems},
            "live_commit": live_commit,
            "sources": {s.name: {"models": s.models, "root": str(s.root), "slippage": s.slippage,
                                 "day_start": str(s.day_start), "day_end": str(s.day_end),
                                 "positions": {m: len(positions.get((s.name, m), [])) for m in s.models},
                                 "gate_failures": s.gate_failures} for s in sources},
            "price_gate": gates,
            "coverage": coverage,
            "btc_daily": {"days": int(len(btc_daily)),
                          "first": str(btc_daily.index[0].date()) if len(btc_daily) else None,
                          "last": str(btc_daily.index[-1].date()) if len(btc_daily) else None,
                          "missing_days": int(btc_daily.isna().sum())},
        }
        print("=== PREFLIGHT BEGIN ===")
        print(json.dumps(report, indent=2, ensure_ascii=False, default=str))
        print("=== PREFLIGHT END ===")
        return exit_code

    # --- measure -----------------------------------------------------------------
    results: dict[str, Any] = {}
    trade_rows: list[dict[str, Any]] = []
    weekly_rows: list[dict[str, Any]] = []
    for source in sources:
        entry: dict[str, Any] = {"key": source.key, "period": source.period, "layer": source.layer,
                                 "price_gate": gates.get(source.name), "models": {}, "pairs": []}
        results[source.name] = entry
        if source.gate_failures:
            entry["measured"] = False
            entry["reason"] = source.gate_failures
            continue
        entry["measured"] = True
        bar = layer_bar(config, source.layer)
        bar_length = bar_duration(bar)
        frames: dict[str, pd.DataFrame] = {}
        for model in source.models:
            pos_list = positions.get((source.name, model), [])
            for pos in pos_list:
                pos.coin_ret = window_return(book, bar, pos.symbol, pos.opened_at, pos.closed_at)
                pos.btc_ret = window_return(book, bar, btc_symbol, pos.opened_at, pos.closed_at)
                trade_rows.append({
                    "source": source.key, "period": source.period, "model": model, "symbol": pos.symbol,
                    "direction": pos.direction, "opened_at": pos.opened_at.isoformat(),
                    "closed_at": pos.closed_at.isoformat(), "r": pos.r, "pnl": pos.pnl,
                    "coin_ret": pos.coin_ret, "btc_ret": pos.btc_ret,
                    "aligned_btc": alignment(pos.btc_ret, pos.direction),
                    "aligned_coin": alignment(pos.coin_ret, pos.direction),
                })
            equity = equities.get((source.name, model), pd.DataFrame())
            window = day_window(source, equity, pos_list)
            block: dict[str, Any] = {
                "label": source.labels.get(model),
                "positions": len(pos_list),
                "unmeasured_r": sum(1 for p in pos_list if p.r is None),
                "sample_flag_O": len([p for p in pos_list if p.r is not None]) < settings.min_trades,
                "day_window": [str(window[0].date()), str(window[1].date())] if window else None,
                "m1": m1_alignment(pos_list),
            }
            if window is not None:
                frame, counts = daily_frame(equity, btc_daily, window)
                frames[model] = frame
                block["m2"] = m2_regression(frame, counts, iterations=settings.iterations,
                                            alpha=settings.alpha, seed=seed_for(settings, source, model, "m2"))
                for row in m3_weekly(pos_list, equity, btc_daily, window):
                    weekly_rows.append({"source": source.key, "period": source.period, "model": model, **row})
            else:
                block["m2"] = None
            block["m4"] = m4_concurrency(pos_list, book, bar, bar_length)
            entry["models"][model] = block
        for pair in source.pairs:
            if pair.model not in entry["models"] or pair.control not in entry["models"]:
                continue
            mp = positions.get((source.name, pair.model), [])
            cp = positions.get((source.name, pair.control), [])
            pair_block: dict[str, Any] = {"model": pair.model, "control": pair.control, "note": pair.note or None,
                                          "aligned_share_diff": {}}
            for definition in DEFINITIONS:
                pair_block["aligned_share_diff"][definition] = aligned_share_diff(
                    mp, cp, definition, iterations=settings.iterations, alpha=settings.alpha,
                    seed=seed_for(settings, source, f"{pair.model}-{pair.control}", f"m5-{definition}"))
            if pair.model in frames and pair.control in frames:
                pair_block["regression_diff"] = paired_regression_diff(
                    frames[pair.model], frames[pair.control], iterations=settings.iterations,
                    alpha=settings.alpha, seed=seed_for(settings, source, f"{pair.model}-{pair.control}", "m5-m2"))
            entry["pairs"].append(pair_block)

    payload = {
        "preregistration": "docs/backtest.md > 6m (71dfe3f8)",
        "status": "TEŞHİS, KARAR DEĞİL — hiçbir model, kapı ya da karar bu sonuçla değişmez",
        "parameters": {"neutral_eps": NEUTRAL_EPS, "price_tol": PRICE_TOL,
                       "price_gate_max_fail": PRICE_GATE_MAX_FAIL, "min_clusters": MIN_CLUSTERS,
                       "min_trades": settings.min_trades, "iterations": settings.iterations,
                       "alpha": settings.alpha, "seed": settings.seed, "cluster": "ISO hafta (Pzt 00:00 UTC)"},
        "inputs": {
            "pins": {"dir": str(args.pins_dir), "verified": not pin_problems, "problems": pin_problems},
            "xsec": {**xsec_status, "original_run": args.xsec_run},
            "live_commit": live_commit,
            "prices_now": now.isoformat(),
        },
        "coverage": coverage,
        "results": results,
    }
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / "market_direction.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False, default=_json_default) + "\n", encoding="utf-8")
    _write_csv(out / "market_direction_trades.csv", trade_rows)
    _write_csv(out / "market_direction_weekly.csv", weekly_rows)
    _write_csv(out / "market_direction_prices.csv", book.rows())
    btc_frame = pd.DataFrame({"day": [str(d.date()) for d in btc_daily.index], "close": btc_daily.to_numpy()})
    btc_frame.to_csv(out / "market_direction_btc_daily.csv", index=False)
    logger.info("rapor yazıldı: %s (çıkış kodu %d)", out, exit_code)
    return exit_code


def _json_default(value: Any) -> Any:
    if isinstance(value, (np.floating, np.integer)):
        return value.item()
    if isinstance(value, (pd.Timestamp,)):
        return value.isoformat()
    return str(value)


def _clean(value: Any) -> Any:
    if value is None:
        return ""
    if isinstance(value, float) and math.isnan(value):
        return ""
    return value


def _write_csv(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        if not rows:
            handle.write("")
            return
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        for row in rows:
            writer.writerow({k: _clean(v) for k, v in row.items()})


def _git_head() -> str | None:
    try:
        return subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True,
                              check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Piyasa yönü teşhisi (docs/backtest.md > 6m). TEŞHİS, KARAR DEĞİL.")
    parser.add_argument("--stage", choices=("preflight", "measure"), required=True,
                        help="preflight: kapsam + fiyat kapısı (tekrarlanabilir); measure: TEK SEFER")
    parser.add_argument("--config", default=None)
    parser.add_argument("--pins-dir", default="docs/data/pins/decision59")
    parser.add_argument("--xsec-dir", default="backtests/xsec", help="yeniden üretilmiş xsec koşusunun dizini")
    parser.add_argument("--xsec-original", default="", help="#35981642832'nin results.json'ı")
    parser.add_argument("--xsec-run", default="#35981642832")
    parser.add_argument("--live-base", default="ledgers")
    parser.add_argument("--live-scalp", default="ledgers_scalp")
    parser.add_argument("--live-commit", default="", help="canlı defterlerin okunduğu commit (boş = HEAD)")
    parser.add_argument("--now", default="", help="mumların kesim anı (boş = şimdi, saate yuvarlanmış)")
    parser.add_argument("--cache-dir", default="", help="koşuya özel mum önbelleği (boş = geçici dizin)")
    parser.add_argument("--out-dir", default="backtests/market_direction")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    try:
        return run(args)
    except DataGateError as exc:
        logger.error("VERİ KAPISI: %s", exc)
        return 3


if __name__ == "__main__":
    sys.exit(main())

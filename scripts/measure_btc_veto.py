#!/usr/bin/env python3
"""BTC momentum vetosu ölçümü (docs/backtest.md > 6o). ÖLÇÜM, yeni model YOK.

Ön-kayıt (`1522e44`, TADİLAT-1 `ebd6007`, TADİLAT-2 `5bd4da4`) bu betikten ÖNCE, hiçbir veri
görülmeden commit edildi. Betik o metni MEKANİK uygular; hiçbir sayı burada SEÇİLMEZ ve
hiçbir sabit CLI girdisi değildir.

- **BİRİNCİL (§6o > 4b):** güçlü BTC durumlarında (|z| > 1) 12 altcoinin sonraki H saatteki
  getirisi; bağlayıcı ölçü `D = ½(ȳ₊ + ȳ₋)` (TADİLAT-2), havuzlanmış ortalama betimsel.
- **İKİNCİL-1 (§6o > 5–7):** yönü rastgele kontrollerde KARŞI − (YANINDA ∪ NÖTR) ort. R;
  okunuşu fiyat testine KOŞULLU.
- **İKİNCİL-2 (§6o > 8):** modellerde aynı karşıtlık + vetolu alt küme; bilgi.

**İkinci bir uygulama YOK:** küme çekilişleri, yüzdelik aralık ve hassasiyet
`scripts/backtest_dc.py`den (`cluster_diff_draws`, `cluster_mean_draws`, `_percentiles`,
`precision`, `precision_diff`), dönem A sınırları `scripts/backtest_ema.py`den, mum çekimi
`core/data.py::fetch_ohlcv`tan gelir. R §6m'nin sabitlediği satırlardan okunur
(`core/metrics.py::merge_fills` sonrası), burada yeniden tanımlanmaz.

**Salt okunur:** deftere, config'e ve `data/cache/`e yazmaz (snapshot koşuya özel geçici
önbellek kullanır).

ÜÇ AŞAMA: `snapshot` 13 sembolün 1H mumlarını çekip gzip + SHA256SUMS + MANIFEST olarak yazar,
HİÇBİR ŞEY hesaplamaz. `preflight` SHA256'ları, iki veri kapısını (kapsam, parite), BTC
durumunun tanımsız sayılarını ve hücre/küme SAYILARINI raporlar — ne R ne ileri getiri
okur; tekrarlanabilir. `measure` tek seferliktir.

Çıkış kodları (karar 51): 0 = yazıldı; 3 = veri kapısı (SHA, kapsam, parite, eksik pin);
2 = kullanım hatası. Tetikleyicisi `.github/workflows/measure-btc-veto.yml`.
"""

from __future__ import annotations

import argparse
import copy
import csv
import gzip
import hashlib
import io
import json
import logging
import math
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from core.config import get_setting, load_config  # noqa: E402
from core.data import fetch_ohlcv  # noqa: E402
from scripts.backtest_dc import (  # noqa: E402
    MIN_CLUSTERS,
    _percentiles,
    cluster_diff_draws,
    cluster_mean_draws,
    precision,
    precision_diff,
)
from scripts.backtest_ema import PERIOD_A_CUTOFF, PERIOD_A_START  # noqa: E402

logger = logging.getLogger("measure_btc_veto")

# --- Ön-kayıtlı sayılar (§6o). Hiçbiri CLI girdisi DEĞİLDİR. ------------------------------
TRADES_CSV = Path("docs/data/market_direction_trades.csv")
TRADES_SHA256 = "3147e7499128edfa888431ad056659826478ed6a36f59fbcd4e23b58fa16632c"   # §6o > 2
PRICES_CSV = Path("docs/data/market_direction_prices.csv")
PINS_DIR = Path("docs/data/pins/btc_veto")
BTC = "BTC-USDT-SWAP"
ALTCOINS = (   # §6o > 3: layers.ema.universe, BTC hariç — sıra sabit
    "ETH-USDT-SWAP", "SOL-USDT-SWAP", "XRP-USDT-SWAP", "DOGE-USDT-SWAP", "BNB-USDT-SWAP",
    "AVAX-USDT-SWAP", "LINK-USDT-SWAP", "ADA-USDT-SWAP", "SUI-USDT-SWAP", "NEAR-USDT-SWAP",
    "PENGU-USDT-SWAP", "ETHFI-USDT-SWAP",
)
SYMBOLS = (BTC, *ALTCOINS)
SNAPSHOT_START = pd.Timestamp("2021-10-01T00:00:00Z")          # §6o > 3
HOUR = pd.Timedelta(hours=1)
HORIZONS = {"1h": 1, "4h": 4, "1d": 24}                        # §6o > 4: saat
SCALE_HOURS = 90 * 24                                          # §6o > 4: 90 gün
SCALE_MIN_SHARE = 0.90                                         # §6o > 4: %90 kapsam
STRONG_Z = 1.0                                                 # §6o > 4: |z| > 1
PARITY_TOL = 1e-6                                              # §6o > 3
PARITY_MAX_FAIL = 0.01                                         # §6o > 3
MIN_N = 30                                                     # §6o > 6, 4b
BH_Q = 0.05                                                    # §6o > 7, 4b
PRICE_PERIODS = {                                              # §6o > 4b (TADİLAT-1)
    "A": (pd.Timestamp(PERIOD_A_START), pd.Timestamp(PERIOD_A_CUTOFF)),
    "B": (pd.Timestamp("2024-07-01T00:00:00Z"), pd.Timestamp("2026-09-18T12:00:00Z")),
}
PRICE_DEFINITIONS = ("day", "week")        # §6o > 4b
TRADE_DEFINITIONS = ("month", "week")      # §6o > 6

KARSI, YANINDA, NOTR, TANIMSIZ = "KARŞI", "YANINDA", "NÖTR", "TANIMSIZ"


@dataclass(frozen=True, kw_only=True)
class Unit:
    source: str
    period: str
    model: str
    role: str            # "primary" (İKİNCİL-1'in bağlayıcı birimi) | "descriptive" | "model"
    label: str = ""


# §6o > 5 (İKİNCİL-1) ve 8 (İKİNCİL-2). random_ctrl hiçbir yolda yok (karar 60, 63).
CONTROL_UNITS = (
    Unit(source="dc", period="A", model="dc_coinflip", role="primary"),
    Unit(source="dc", period="B", model="dc_coinflip", role="primary"),
    Unit(source="xsec", period="A", model="xsec_random", role="primary"),
    Unit(source="xsec", period="B", model="xsec_random", role="primary"),
    Unit(source="live-scalp", period="live", model="scalp_coinflip", role="descriptive"),
)
MODEL_UNITS = tuple(
    [Unit(source=s, period=p, model=m, role="model")
     for s, m in (("dc", "dc_short"), ("xsec", "xsec_mom"), ("ema", "ema_trend"), ("ema", "trend"))
     for p in ("A", "B")]
    + [Unit(source="live-base", period="live", model="trend", role="model")]
    + [Unit(source="live-scalp", period="live", model=m, role="model",
            label="kopya" if m == "vwap_clone" else "")
       for m in ("scalp_fixed", "scalp_patient", "scalp_bandit", "scalp_managed",
                 "vwap_managed", "vwap_clone")]
)
COINFLIP_MODELS = {"dc_coinflip", "scalp_coinflip"}   # §6o > 5: ek karşıtlık yalnızca bunlarda


class DataGateError(RuntimeError):
    """Veri kapısı düştü: rapor yazılmaz, çıkış 3 (karar 51)."""


# --------------------------------------------------------------------------- #
# Yardımcılar
# --------------------------------------------------------------------------- #
def _utc(value: Any) -> pd.Timestamp:
    stamp = pd.Timestamp(value)
    return stamp.tz_localize("UTC") if stamp.tzinfo is None else stamp.tz_convert("UTC")


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def iso_week_key(stamp: pd.Timestamp) -> str:
    year, week, _ = stamp.isocalendar()
    return f"{year:04d}-W{week:02d}"


def cluster_of(stamp: pd.Timestamp, definition: str) -> str:
    """Küme kimliği. `month`/`day`/`week` — hepsi UTC, damga `opened_at` ya da T."""
    stamp = _utc(stamp)
    if definition == "month":
        return f"{stamp.year:04d}-{stamp.month:02d}"
    if definition == "day":
        return stamp.strftime("%Y-%m-%d")
    if definition == "week":
        return iso_week_key(stamp)
    raise ValueError(f"tanınmayan küme tanımı: {definition!r}")


def bootstrap_p(draws: Sequence[float]) -> float:
    """İki yönlü yüzdelik bootstrap p'si (§6l > 6'nın formülü, §6o > 4b/6)."""
    b = len(draws)
    le = sum(1 for d in draws if d <= 0.0)
    ge = sum(1 for d in draws if d >= 0.0)
    return min(1.0, 2.0 * min(le + 1, ge + 1) / (b + 1))


def bh_reject(pvalues: Mapping[str, float], q: float = BH_Q) -> dict[str, bool]:
    """Benjamini-Hochberg: `p₍ᵢ₎ ≤ (i/m)·q` sağlayan en büyük i'ye kadar reddedilir."""
    m = len(pvalues)
    if m == 0:
        return {}
    ordered = sorted(pvalues.items(), key=lambda kv: (kv[1], kv[0]))
    cutoff = 0
    for i, (_, p) in enumerate(ordered, start=1):
        if p <= i / m * q:
            cutoff = i
    return {key: rank <= cutoff for rank, (key, _) in enumerate(ordered, start=1)}


# --------------------------------------------------------------------------- #
# Mumlar: snapshot ve pins
# --------------------------------------------------------------------------- #
def snapshot_end(trades: pd.DataFrame) -> pd.Timestamp:
    """Snapshot'ın `now`u: CSV'nin son `opened_at`i ve fiyat testinin B sonu — SEÇİLMEZ."""
    last = max(_utc(trades["opened_at"].max()).floor("h"), PRICE_PERIODS["B"][1])
    return last + 2 * HOUR


def fetch_hourly(config: Mapping[str, Any], symbol: str, *, now: pd.Timestamp, cache_dir: str,
                 fetcher: Callable[..., pd.DataFrame] | None = None) -> pd.DataFrame:
    fetch = fetcher if fetcher is not None else fetch_ohlcv
    local = copy.deepcopy(dict(config))
    local["timeframe"] = "1H"
    bars = int(math.ceil((now - SNAPSHOT_START) / HOUR)) + 2
    local["data"] = {**local["data"], "history_bars": bars, "cache_dir": cache_dir}
    frame = fetch(local, symbol, now=now)
    return frame.loc[frame.index >= SNAPSHOT_START]


def write_pins(frames: Mapping[str, pd.DataFrame], out: Path, *, end: pd.Timestamp,
               run: str | None) -> dict[str, Any]:
    out.mkdir(parents=True, exist_ok=True)
    files = []
    for symbol in SYMBOLS:
        frame = frames.get(symbol, pd.DataFrame())
        lines = ["ts,open,high,low,close"]
        for ts, row in frame.iterrows():
            # repr(float) = en kısa kayıpsız ondalık; numpy skaleri kendi repr'ini yazardı.
            values = ",".join(repr(float(row[c])) for c in ("open", "high", "low", "close"))
            lines.append(f"{_utc(ts).isoformat()},{values}")
        raw = ("\n".join(lines) + "\n").encode("utf-8")
        name = f"{symbol}_1H.csv"
        (out / f"{name}.gz").write_bytes(gzip.compress(raw, compresslevel=9, mtime=0))
        files.append({
            "path": name, "bytes": len(raw), "bars": len(frame),
            "first": _utc(frame.index[0]).isoformat() if len(frame) else None,
            "last": _utc(frame.index[-1]).isoformat() if len(frame) else None,
            "sha256": hashlib.sha256(raw).hexdigest(),
        })
    manifest = {
        "purpose": "OKX 1H mumları, 13 sembol — docs/backtest.md > 6o > 3; hiçbir şey hesaplanmadı",
        "start": SNAPSHOT_START.isoformat(), "now": end.isoformat(), "snapshot_run": run,
        "note": "ts = bar AÇILIŞI (UTC); sha256 SIKIŞTIRILMAMIŞ içeriğe aittir",
        "files": files,
    }
    (out / "MANIFEST.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n")
    (out / "SHA256SUMS").write_text("".join(f"{f['sha256']}  {f['path']}\n" for f in files))
    return manifest


def load_pins(pins: Path) -> tuple[dict[str, pd.DataFrame], list[str]]:
    """SHA256SUMS'u doğrular; tutmayan dosya OKUNMAZ. Dönen: (sembol -> çerçeve, sorunlar)."""
    problems: list[str] = []
    frames: dict[str, pd.DataFrame] = {}
    sums = pins / "SHA256SUMS"
    if not sums.is_file():
        return frames, [f"{sums} yok"]
    for line in sums.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        digest, rel = line.split(None, 1)
        rel = rel.strip()
        gz = pins / f"{rel}.gz"
        if not gz.is_file():
            problems.append(f"{rel}: dosya yok")
            continue
        raw = gzip.decompress(gz.read_bytes())
        if hashlib.sha256(raw).hexdigest() != digest:
            problems.append(f"{rel}: SHA256 tutmuyor")
            continue
        frame = pd.read_csv(io.BytesIO(raw))
        frame["ts"] = pd.to_datetime(frame["ts"], utc=True)
        frames[rel.removesuffix("_1H.csv")] = frame.set_index("ts").sort_index()
    for symbol in SYMBOLS:
        if symbol not in frames and not any(p.startswith(f"{symbol}_") for p in problems):
            problems.append(f"{symbol}: pins içinde yok")
    return frames, problems


def close_series(frame: pd.DataFrame) -> pd.Series:
    """KAPANIŞ ANINA damgalı kapanış: C(T) = açılışı T − 1h olan barın kapanışı."""
    if frame.empty:
        return pd.Series(dtype="float64")
    return pd.Series(frame["close"].to_numpy(dtype="float64"), index=frame.index + HOUR)


def open_series(frame: pd.DataFrame) -> pd.Series:
    """O(T) = T'de AÇILAN barın açılışı."""
    if frame.empty:
        return pd.Series(dtype="float64")
    return pd.Series(frame["open"].to_numpy(dtype="float64"), index=frame.index)


# --------------------------------------------------------------------------- #
# Veri kapıları (§6o > 3)
# --------------------------------------------------------------------------- #
def coverage_gate(btc: pd.DataFrame, earliest_open: pd.Timestamp) -> dict[str, Any]:
    need = min(earliest_open - pd.Timedelta(days=91), PRICE_PERIODS["A"][0] - pd.Timedelta(days=91))
    first = _utc(btc.index[0]) if len(btc) else None
    return {"btc_first_bar": first.isoformat() if first else None, "required": need.isoformat(),
            "passed": first is not None and first <= need}


def parity_gate(frames: Mapping[str, pd.DataFrame], prices_csv: Path) -> dict[str, Any]:
    """Pinlenmiş 4H barları 1H seriye sınır noktalarında birebir inmeli (§6o > 3)."""
    checked: dict[str, int] = {s: 0 for s in SYMBOLS}
    failed: dict[str, int] = {s: 0 for s in SYMBOLS}
    with prices_csv.open(encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            if row["bar"] != "4H" or row["symbol"] not in checked:
                continue
            symbol, start = row["symbol"], _utc(row["ts"])
            frame = frames.get(symbol)
            checked[symbol] += 1
            ok = False
            if frame is not None and start in frame.index and (start + 3 * HOUR) in frame.index:
                o1 = float(frame.at[start, "open"])
                c1 = float(frame.at[start + 3 * HOUR, "close"])
                o4, c4 = float(row["open"]), float(row["close"])
                ok = abs(o1 - o4) <= PARITY_TOL * abs(o4) and abs(c1 - c4) <= PARITY_TOL * abs(c4)
            if not ok:
                failed[symbol] += 1
    per_symbol = {
        s: {"checked": checked[s], "failed": failed[s],
            "passed": checked[s] == 0 or failed[s] / checked[s] <= PARITY_MAX_FAIL}
        for s in SYMBOLS
    }
    return {"per_symbol": per_symbol, "passed": all(v["passed"] for v in per_symbol.values())}


# --------------------------------------------------------------------------- #
# BTC durumu (§6o > 4)
# --------------------------------------------------------------------------- #
def btc_states(btc_close: pd.Series) -> dict[str, pd.DataFrame]:
    """Ufuk başına saatlik ızgarada g, σ, z. Satır T'nin değerleri yalnızca ≤ T kapananlardan."""
    if btc_close.empty:
        return {h: pd.DataFrame(columns=["g", "sigma", "z"]) for h in HORIZONS}
    grid = pd.date_range(btc_close.index[0], btc_close.index[-1], freq="h")
    log_close = np.log(btc_close.reindex(grid))
    min_periods = int(math.ceil(SCALE_MIN_SHARE * SCALE_HOURS))
    out: dict[str, pd.DataFrame] = {}
    for name, hours in HORIZONS.items():
        g = log_close - log_close.shift(hours)
        # τ ∈ [T − 90g, T): cari getiri kendi ölçeğine GİRMEZ (shift(1)).
        sigma = g.rolling(SCALE_HOURS, min_periods=min_periods).std(ddof=1).shift(1)
        z = g / sigma
        out[name] = pd.DataFrame({"g": g, "sigma": sigma, "z": z})
    return out


def classify(z: float | None, direction: str) -> str:
    if z is None or not np.isfinite(z):
        return TANIMSIZ
    if abs(z) <= STRONG_Z:
        return NOTR
    btc_up = z > 0
    if direction == "long":
        return YANINDA if btc_up else KARSI
    if direction == "short":
        return KARSI if btc_up else YANINDA
    raise ValueError(f"tanınmayan yön: {direction!r}")


def state_at(states: pd.DataFrame, opened_at: pd.Timestamp) -> float | None:
    """Çapa T = opened_at'e eşit ya da ondan önceki son TAM SAAT (§6o > 4)."""
    anchor = _utc(opened_at).floor("h")
    if anchor not in states.index:
        return None
    value = states.at[anchor, "z"]
    return float(value) if np.isfinite(value) else None


# --------------------------------------------------------------------------- #
# İşlem satırları
# --------------------------------------------------------------------------- #
def read_trades(path: Path) -> pd.DataFrame:
    """Yalnızca gereken kolonlar; `r` yalnızca `measure` aşamasında okunur."""
    frame = pd.read_csv(path, dtype={"source": str, "period": str, "model": str})
    frame["opened_at"] = pd.to_datetime(frame["opened_at"], utc=True)
    return frame


def unit_rows(trades: pd.DataFrame, unit: Unit) -> pd.DataFrame:
    mask = (trades["source"] == unit.source) & (trades["period"] == unit.period) & (trades["model"] == unit.model)
    return trades.loc[mask]


def classify_rows(rows: pd.DataFrame, states: Mapping[str, pd.DataFrame]) -> pd.DataFrame:
    out = rows[["symbol", "direction", "opened_at"]].copy()
    for horizon, frame in states.items():
        out[horizon] = [classify(state_at(frame, t), d) for t, d in zip(rows["opened_at"], rows["direction"])]
    return out


def _groups(stamps: Sequence[pd.Timestamp], values: Sequence[float], definition: str) -> dict[str, list[float]]:
    groups: dict[str, list[float]] = {}
    for stamp, value in zip(stamps, values):
        groups.setdefault(cluster_of(stamp, definition), []).append(float(value))
    return groups


def cell_counts(classes: pd.DataFrame, horizon: str) -> dict[str, Any]:
    """Preflight'ın hücre tablosu: n ve küme sayıları — R OKUNMAZ."""
    out: dict[str, Any] = {}
    cells = {KARSI: [KARSI], YANINDA: [YANINDA], NOTR: [NOTR], "YANINDA∪NÖTR": [YANINDA, NOTR]}
    for name, members in cells.items():
        sub = classes.loc[classes[horizon].isin(members)]
        out[name] = {"n": int(len(sub)),
                     **{f"clusters_{d}": len({cluster_of(t, d) for t in sub["opened_at"]}) for d in TRADE_DEFINITIONS}}
    out[TANIMSIZ] = int((classes[horizon] == TANIMSIZ).sum())
    return out


def _ci(draws: Sequence[float], alpha: float) -> tuple[float | None, float | None]:
    if not draws:
        return None, None
    return _percentiles(draws, alpha)


def contrast(a_stamps: Sequence[pd.Timestamp], a_values: Sequence[float],
             b_stamps: Sequence[pd.Timestamp], b_values: Sequence[float], *,
             definitions: Sequence[str], iterations: int, alpha: float, seed: str,
             scale: float = 1.0) -> dict[str, Any]:
    """`scale · (ort(a) − ort(b))`, küme-EŞLEŞTİRİLMİŞ bootstrap, iki tanım, muhafazakâr okuma.

    `scale = ½` ve b = aşağı-durum ham getirisi → TADİLAT-2'nin D'si; `scale = 1` → karşıtlık.
    """
    n_a, n_b = len(a_values), len(b_values)
    point = scale * (float(np.mean(a_values)) - float(np.mean(b_values))) if n_a and n_b else None
    per_def: dict[str, Any] = {}
    evaluable = n_a >= MIN_N and n_b >= MIN_N
    for definition in definitions:
        ga = _groups(a_stamps, a_values, definition)
        gb = _groups(b_stamps, b_values, definition)
        entry: dict[str, Any] = {"clusters_a": len(ga), "clusters_b": len(gb)}
        if n_a >= 2 and n_b >= 2:
            raw, dropped = cluster_diff_draws(ga, gb, iterations=iterations, seed=f"{seed}:{definition}")
            draws = [scale * d for d in raw]
            low, high = _ci(draws, alpha)
            prec = precision_diff(ga, gb)
            se = prec.get("se_cluster")
            entry.update({
                "low": low, "high": high, "p": bootstrap_p(draws) if draws else 1.0,
                "draws": len(draws), "dropped_draws": dropped,
                "se_cluster": scale * se if se is not None else None,
                "mde": scale * prec["mde"] if "mde" in prec else None,
                "deff": prec.get("deff"),
                "n_effective": (n_a + n_b) / prec["deff"] if prec.get("deff") else None,
            })
            if len(draws) < iterations:
                evaluable = False
        else:
            entry.update({"low": None, "high": None, "p": 1.0})
            evaluable = False
        if len(ga) < MIN_CLUSTERS or len(gb) < MIN_CLUSTERS:
            evaluable = False
        per_def[definition] = entry
    lows = [e["low"] for e in per_def.values()]
    highs = [e["high"] for e in per_def.values()]
    return {
        "n_a": n_a, "n_b": n_b, "estimate": point, "definitions": per_def,
        "evaluable": evaluable,
        # Muhafazakâr okuma: iki tanımın GENİŞ tarafı; p'nin büyüğü.
        "low_binding": min(lows) if evaluable and None not in lows else None,
        "high_binding": max(highs) if evaluable and None not in highs else None,
        "p_binding": max(e["p"] for e in per_def.values()) if evaluable else 1.0,
        "mde_binding": max((e.get("mde") or 0.0) for e in per_def.values()) if evaluable else None,
    }


def mean_ci(stamps: Sequence[pd.Timestamp], values: Sequence[float], *, definitions: Sequence[str],
            iterations: int, alpha: float, seed: str) -> dict[str, Any]:
    n = len(values)
    out: dict[str, Any] = {"n": n, "mean": float(np.mean(values)) if n else None, "definitions": {}}
    for definition in definitions:
        groups = _groups(stamps, values, definition)
        entry: dict[str, Any] = {"clusters": len(groups)}
        if n >= 2:
            draws = cluster_mean_draws(groups, iterations=iterations, seed=f"{seed}:{definition}")
            entry["low"], entry["high"] = _ci(draws, alpha)
            prec = precision(groups)
            entry.update({"mde": prec.get("mde"), "deff": prec.get("deff"), "n_effective": prec.get("n_effective")})
        out["definitions"][definition] = entry
    out["flag"] = "Ö" if n < MIN_N else ""
    return out


# --------------------------------------------------------------------------- #
# BİRİNCİL — fiyat düzeyi testi (§6o > 4b)
# --------------------------------------------------------------------------- #
@dataclass(frozen=True, kw_only=True)
class PriceObs:
    anchor: pd.Timestamp
    symbol: str
    sign: int
    forward: float | None        # ln(C(T+H)/O(T)); preflight'ta HESAPLANMAZ (None)


def price_observations(states: pd.DataFrame, hours: int, period: str,
                       closes: Mapping[str, pd.Series], opens: Mapping[str, pd.Series], *,
                       with_values: bool) -> tuple[list[PriceObs], dict[str, int]]:
    """Güçlü saatlerde (a, T) gözlemleri. `with_values=False` yalnızca VARLIĞI sınar."""
    start, end = PRICE_PERIODS[period]
    horizon = pd.Timedelta(hours=hours)
    frame = states.loc[(states.index >= start) & (states.index + horizon <= end)]
    undefined = int(frame["z"].isna().sum())
    strong = frame.loc[frame["z"].abs() > STRONG_Z]
    obs: list[PriceObs] = []
    missing = {s: 0 for s in ALTCOINS}
    for anchor, z in strong["z"].items():
        sign = 1 if z > 0 else -1
        for symbol in ALTCOINS:
            o = opens[symbol].get(anchor)
            c = closes[symbol].get(anchor + horizon)
            if o is None or c is None or not (np.isfinite(o) and np.isfinite(c)) or o <= 0 or c <= 0:
                missing[symbol] += 1
                continue
            obs.append(PriceObs(anchor=anchor, symbol=symbol, sign=sign,
                                forward=float(math.log(c / o)) if with_values else None))
    return obs, {"hours_in_period": int(len(frame)), "undefined_state_hours": undefined,
                 "strong_up_hours": int((strong["z"] > 0).sum()),
                 "strong_down_hours": int((strong["z"] < 0).sum()),
                 "missing_forward": missing}


def price_counts(obs: Sequence[PriceObs]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for label, sign in (("up", 1), ("down", -1)):
        sub = [o for o in obs if o.sign == sign]
        out[label] = {"n": len(sub),
                      **{f"clusters_{d}": len({cluster_of(o.anchor, d) for o in sub}) for d in PRICE_DEFINITIONS}}
    return out


def price_test(obs: Sequence[PriceObs], *, period: str, horizon: str, seed_base: str, iterations: int,
               alpha: float, drift: Sequence[float]) -> dict[str, Any]:
    up = [o for o in obs if o.sign == 1]
    down = [o for o in obs if o.sign == -1]
    bp = 1e4
    seed = f"{seed_base}:btcveto:price:{period}:{horizon}"
    # D = ½(ȳ₊ + ȳ₋) = ½(ort f₊ − ort f₋) (TADİLAT-2) — bp cinsinden.
    d = contrast([o.anchor for o in up], [o.forward * bp for o in up],
                 [o.anchor for o in down], [o.forward * bp for o in down],
                 definitions=PRICE_DEFINITIONS, iterations=iterations, alpha=alpha, seed=seed, scale=0.5)
    pooled_y = [o.sign * o.forward * bp for o in obs]
    pooled = mean_ci([o.anchor for o in obs], pooled_y, definitions=PRICE_DEFINITIONS,
                     iterations=iterations, alpha=alpha, seed=f"{seed_base}:btcveto:price:pooled:{period}:{horizon}")
    y_up = mean_ci([o.anchor for o in up], [o.forward * bp for o in up], definitions=PRICE_DEFINITIONS,
                   iterations=iterations, alpha=alpha, seed=f"{seed}:y_up")
    y_down = mean_ci([o.anchor for o in down], [-o.forward * bp for o in down], definitions=PRICE_DEFINITIONS,
                     iterations=iterations, alpha=alpha, seed=f"{seed}:y_down")
    per_symbol = {}
    for symbol in ALTCOINS:
        su = [o.forward * bp for o in up if o.symbol == symbol]
        sd = [-o.forward * bp for o in down if o.symbol == symbol]
        per_symbol[symbol] = {
            "n_up": len(su), "n_down": len(sd),
            "D": 0.5 * (float(np.mean(su)) + float(np.mean(sd))) if su and sd else None,
            "pooled_y": float(np.mean(su + sd)) if su or sd else None,
            "flag": "Ö" if min(len(su), len(sd)) < MIN_N else "",
        }
    return {
        "D_bp": d, "pooled_y_bp": pooled, "y_up_bp": y_up, "y_down_bp": y_down,
        "drift_bp": {"n": len(drift), "mean": float(np.mean(drift)) * bp if drift else None},
        "per_symbol": per_symbol,
    }


def drift_values(hours: int, period: str, closes: Mapping[str, pd.Series],
                 opens: Mapping[str, pd.Series], grid: pd.DatetimeIndex) -> list[float]:
    """Dönemin bütün saatlerinin (güçlü ya da değil) ileri getirisi — bilgi (§6o > 4b)."""
    start, end = PRICE_PERIODS[period]
    horizon = pd.Timedelta(hours=hours)
    anchors = grid[(grid >= start) & (grid + horizon <= end)]
    out: list[float] = []
    for symbol in ALTCOINS:
        o = opens[symbol].reindex(anchors).to_numpy()
        c = closes[symbol].reindex(anchors + horizon).to_numpy()
        ok = np.isfinite(o) & np.isfinite(c) & (o > 0) & (c > 0)
        out.extend(np.log(c[ok] / o[ok]).tolist())
    return out


def decide_price(results: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    """A'da BH (m = 3) + D > 0 + AS_bağ > 0; B yalnızca A'da geçenler; DOĞRULANDI = A ∧ B."""
    verdict: dict[str, Any] = {}
    for period in ("A", "B"):
        tests = results.get(period, {})
        family = list(HORIZONS) if period == "A" else [h for h in HORIZONS if verdict["A"][h]["passed"]]
        pvals = {h: tests[h]["D_bp"]["p_binding"] for h in family if h in tests}
        rejected = bh_reject(pvals)
        verdict[period] = {}
        for h in HORIZONS:
            d = tests.get(h, {}).get("D_bp", {})
            in_family = h in family
            passed = bool(in_family and d.get("evaluable") and rejected.get(h)
                          and (d.get("estimate") or 0) > 0 and (d.get("low_binding") or 0) > 0)
            reverse = bool(d.get("evaluable") and (d.get("estimate") or 0) < 0
                           and d.get("high_binding") is not None and d["high_binding"] < 0)
            verdict[period][h] = {
                "in_family": in_family, "m": len(family), "bh_rejected": bool(rejected.get(h)),
                "evaluable": bool(d.get("evaluable")), "passed": passed,
                "reverse_seen": reverse,
                "note": "" if in_family else "bilgi — doğrulama değil",
            }
    verdict["confirmed"] = {h: verdict["A"][h]["passed"] and verdict["B"][h]["passed"] for h in HORIZONS}
    return verdict


# --------------------------------------------------------------------------- #
# İKİNCİL — işlem düzeyi (§6o > 5–8)
# --------------------------------------------------------------------------- #
def trade_unit(rows: pd.DataFrame, classes: pd.DataFrame, unit: Unit, *, seed_base: str,
               iterations: int, alpha: float) -> dict[str, Any]:
    out: dict[str, Any] = {"source": unit.source, "period": unit.period, "model": unit.model,
                           "role": unit.role, "label": unit.label, "n": int(len(rows)), "horizons": {}}
    for horizon in HORIZONS:
        cls = classes[horizon]
        seed = f"{seed_base}:btcveto:{unit.source}:{unit.period}:{unit.model}:{horizon}"
        sel = {name: rows.loc[cls.isin(members)] for name, members in
               ((KARSI, [KARSI]), (YANINDA, [YANINDA]), (NOTR, [NOTR]), ("YANINDA∪NÖTR", [YANINDA, NOTR]))}
        cells = {name: mean_ci(list(sub["opened_at"]), list(sub["r"]), definitions=TRADE_DEFINITIONS,
                               iterations=iterations, alpha=alpha, seed=f"{seed}:cell:{name}")
                 for name, sub in sel.items()}
        entry: dict[str, Any] = {"cells": cells, "undefined": int((cls == TANIMSIZ).sum())}
        if unit.role == "descriptive":
            entry["contrast"] = {"estimate": (cells[KARSI]["mean"] - cells["YANINDA∪NÖTR"]["mean"])
                                 if cells[KARSI]["n"] and cells["YANINDA∪NÖTR"]["n"] else None,
                                 "note": "betimsel — aralık ve p hesaplanmaz (§6o > 5)"}
        else:
            k, r = sel[KARSI], sel["YANINDA∪NÖTR"]
            entry["contrast"] = contrast(list(k["opened_at"]), list(k["r"]), list(r["opened_at"]), list(r["r"]),
                                         definitions=TRADE_DEFINITIONS, iterations=iterations, alpha=alpha,
                                         seed=seed)
        if unit.model in COINFLIP_MODELS and unit.role != "descriptive":
            y = sel[YANINDA]
            entry["karsi_minus_yaninda"] = contrast(
                list(k["opened_at"]), list(k["r"]), list(y["opened_at"]), list(y["r"]),
                definitions=TRADE_DEFINITIONS, iterations=iterations, alpha=alpha, seed=f"{seed}:karsi_yaninda")
            entry["karsi_minus_yaninda"]["note"] = "bilgi — BH'ye ve geçme kararına girmez (§6o > 5)"
        if unit.role == "model":
            defined = rows.loc[cls != TANIMSIZ]
            kept = rows.loc[cls.isin([YANINDA, NOTR])]
            entry["vetolu_alt_kume"] = {
                "all_defined": mean_ci(list(defined["opened_at"]), list(defined["r"]), definitions=TRADE_DEFINITIONS,
                                       iterations=iterations, alpha=alpha, seed=f"{seed}:all"),
                "without_karsi": mean_ci(list(kept["opened_at"]), list(kept["r"]), definitions=TRADE_DEFINITIONS,
                                         iterations=iterations, alpha=alpha, seed=f"{seed}:vetolu"),
                "removed_share": (len(sel[KARSI]) / len(defined)) if len(defined) else None,
                "note": "veto SİMÜLASYONU DEĞİLDİR — boşalan kota/nakit başka işleme gidebilirdi (§6o > 9-b)",
            }
        out["horizons"][horizon] = entry
    return out


def decide_trades(units: Sequence[Mapping[str, Any]], confirmed: Mapping[str, bool]) -> None:
    """İKİNCİL-1'in A → B kuralı (§6o > 7), okunuşu fiyat testine KOŞULLU — yerinde yazar."""
    by_key = {(u["source"], u["period"], u["model"]): u for u in units}
    for source, model in (("dc", "dc_coinflip"), ("xsec", "xsec_random")):
        passed_a: dict[str, bool] = {}
        for period in ("A", "B"):
            unit = by_key.get((source, period, model))
            if unit is None:
                continue
            family = list(HORIZONS) if period == "A" else [h for h in HORIZONS if passed_a.get(h)]
            pvals = {h: unit["horizons"][h]["contrast"]["p_binding"] for h in family}
            rejected = bh_reject(pvals)
            for h in HORIZONS:
                c = unit["horizons"][h]["contrast"]
                in_family = h in family
                passed = bool(in_family and c["evaluable"] and rejected.get(h)
                              and (c["estimate"] or 0) < 0 and c["high_binding"] is not None and c["high_binding"] < 0)
                # §6o > 7: Δ > 0 ve muhafazakâr alt sınır min(AS_ay, AS_hafta) > 0.
                reverse = bool(c["evaluable"] and (c["estimate"] or 0) > 0
                               and c["low_binding"] is not None and c["low_binding"] > 0)
                if period == "A":
                    passed_a[h] = passed
                reading = ("koşullu — fiyat testi DOĞRULANDI" if confirmed.get(h)
                           else "betimsel (fiyat testi doğrulanmadı)")
                unit["horizons"][h]["decision"] = {
                    "in_family": in_family, "m": len(family), "bh_rejected": bool(rejected.get(h)),
                    "passed_own_rule": passed, "reverse_seen": reverse, "reading": reading,
                    "note": "" if in_family else "bilgi — doğrulama değil",
                }
        unit_b = by_key.get((source, "B", model))
        if unit_b is not None:
            for h in HORIZONS:
                unit_b["horizons"][h]["decision"]["confirmed_own_rule"] = bool(
                    passed_a.get(h) and unit_b["horizons"][h]["decision"]["passed_own_rule"])


# --------------------------------------------------------------------------- #
# Aşamalar
# --------------------------------------------------------------------------- #
def run_snapshot(args: argparse.Namespace, config: Mapping[str, Any],
                 fetcher: Callable[..., pd.DataFrame] | None = None) -> int:
    digest = sha256_file(Path(args.trades))
    if digest != TRADES_SHA256:
        logger.error("VERİ KAPISI: %s SHA256 tutmuyor (%s)", args.trades, digest)
        return 3
    trades = pd.read_csv(args.trades, usecols=["opened_at"])   # yalnızca damga: `now` buradan
    end = snapshot_end(trades)
    frames: dict[str, pd.DataFrame] = {}
    with tempfile.TemporaryDirectory() as cache:
        for symbol in SYMBOLS:
            try:
                frames[symbol] = fetch_hourly(config, symbol, now=end, cache_dir=cache, fetcher=fetcher)
            except Exception as exc:  # noqa: BLE001 — eksik sembol SAYILIR, kapı preflight'tadır
                logger.error("%s 1H çekilemedi: %s", symbol, exc)
                frames[symbol] = pd.DataFrame(columns=["open", "high", "low", "close"])
            logger.info("%s: %d bar", symbol, len(frames[symbol]))
    manifest = write_pins(frames, Path(args.out_dir) / "btc_veto", end=end, run=args.run)
    print(json.dumps({"files": [{k: f[k] for k in ("path", "bars", "first", "last")} for f in manifest["files"]]},
                     indent=2, ensure_ascii=False))
    if not len(frames.get(BTC, [])):
        logger.error("BTC serisi boş — snapshot geçersiz")
        return 3
    return 0


def load_inputs(args: argparse.Namespace) -> tuple[pd.DataFrame, dict[str, pd.DataFrame], dict[str, Any]]:
    gates: dict[str, Any] = {}
    digest = sha256_file(Path(args.trades))
    gates["trades_sha256"] = {"expected": TRADES_SHA256, "actual": digest, "passed": digest == TRADES_SHA256}
    if digest != TRADES_SHA256:
        raise DataGateError(f"{args.trades}: SHA256 tutmuyor ({digest})")
    frames, problems = load_pins(Path(args.pins))
    gates["pins"] = {"problems": problems, "passed": not problems}
    if problems:
        raise DataGateError("pins: " + "; ".join(problems))
    trades = read_trades(Path(args.trades))
    coverage = coverage_gate(frames[BTC], trades["opened_at"].min())
    gates["coverage"] = coverage
    gates["altcoin_coverage"] = {s: {"first": _utc(frames[s].index[0]).isoformat() if len(frames[s]) else None,
                                     "bars": int(len(frames[s]))} for s in ALTCOINS}
    if not coverage["passed"]:
        raise DataGateError(f"BTC kapsamı yetersiz: {coverage}")
    parity = parity_gate(frames, Path(args.prices))
    gates["parity"] = parity
    if not parity["passed"]:
        bad = [s for s, v in parity["per_symbol"].items() if not v["passed"]]
        raise DataGateError(f"parite kapısı düştü: {', '.join(bad)}")
    return trades, frames, gates


def run_analysis(args: argparse.Namespace, config: Mapping[str, Any], *, measure: bool) -> int:
    try:
        trades, frames, gates = load_inputs(args)
    except DataGateError as exc:
        logger.error("VERİ KAPISI: %s", exc)
        return 3
    iterations = int(get_setting(config, "acceptance.bootstrap_samples"))
    alpha = float(get_setting(config, "acceptance.edge_ci_alpha"))
    seed_base = str(get_setting(config, "random_seed"))

    states = btc_states(close_series(frames[BTC]))
    closes = {s: close_series(frames[s]) for s in ALTCOINS}
    opens = {s: open_series(frames[s]) for s in ALTCOINS}
    report: dict[str, Any] = {
        "stage": "measure" if measure else "preflight",
        "preregistration": "docs/backtest.md > 6o (1522e44, TADİLAT-1 ebd6007, TADİLAT-2 5bd4da4)",
        "gates": gates,
        "state_undefined": {h: int(states[h]["z"].isna().sum()) for h in HORIZONS},
    }

    # BİRİNCİL — fiyat testi
    price: dict[str, Any] = {}
    grid = states[next(iter(HORIZONS))].index
    for period in PRICE_PERIODS:
        price[period] = {}
        for horizon, hours in HORIZONS.items():
            obs, info = price_observations(states[horizon], hours, period, closes, opens, with_values=measure)
            entry = {"counts": price_counts(obs), **info}
            if measure:
                entry.update(price_test(obs, period=period, horizon=horizon, seed_base=seed_base,
                                        iterations=iterations, alpha=alpha,
                                        drift=drift_values(hours, period, closes, opens, grid)))
            price[period][horizon] = entry
    report["price_test"] = price
    confirmed: dict[str, bool] = {h: False for h in HORIZONS}
    if measure:
        verdict = decide_price(price)
        report["price_verdict"] = verdict
        confirmed = verdict["confirmed"]

    # İKİNCİL — işlem düzeyi. BTC'nin kendi işlemleri ayrı raporlanır.
    secondary: list[dict[str, Any]] = []
    btc_rows: list[dict[str, Any]] = []
    missing_r: dict[str, int] = {}
    for unit in (*CONTROL_UNITS, *MODEL_UNITS):
        rows = unit_rows(trades, unit)
        if measure:
            # R'si bilinmeyen pozisyon ölçüme girmez ve SAYILIR (core/metrics.py: None, 0.0 değil).
            r = pd.to_numeric(rows["r"], errors="coerce")
            missing_r[f"{unit.source}|{unit.period}|{unit.model}"] = int((~np.isfinite(r)).sum())
            rows = rows.assign(r=r).loc[np.isfinite(r)]
        for subset, target in ((rows.loc[rows["symbol"] != BTC], secondary), (rows.loc[rows["symbol"] == BTC], btc_rows)):
            classes = classify_rows(subset, states)
            if not measure:
                target.append({"source": unit.source, "period": unit.period, "model": unit.model,
                               "role": unit.role, "n": int(len(subset)),
                               "cells": {h: cell_counts(classes, h) for h in HORIZONS}})
                continue
            if target is btc_rows:
                cells = {}
                for h in HORIZONS:
                    cells[h] = {name: {"n": int((classes[h] == name).sum()),
                                       "mean_r": float(subset.loc[classes[h] == name, "r"].mean())
                                       if (classes[h] == name).any() else None}
                                for name in (KARSI, YANINDA, NOTR, TANIMSIZ)}
                target.append({"source": unit.source, "period": unit.period, "model": unit.model,
                               "n": int(len(subset)), "cells": cells, "note": "BTC — bilgi"})
                continue
            target.append(trade_unit(subset, classes, unit, seed_base=seed_base,
                                     iterations=iterations, alpha=alpha))
    if measure:
        decide_trades([u for u in secondary if u["role"] == "primary"], confirmed)
    report["trade_level"] = secondary
    if measure:
        report["rows_without_r"] = missing_r
    report["btc_own_trades"] = btc_rows

    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    name = "btc_veto_results.json" if measure else "btc_veto_preflight.json"
    (out / name).write_text(json.dumps(_clean(report), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(_clean({k: report[k] for k in ("stage", "gates", "state_undefined")}), indent=2, ensure_ascii=False))
    if measure:
        print(json.dumps(_clean(report["price_verdict"]), indent=2, ensure_ascii=False))
    logger.info("yazıldı: %s", out / name)
    return 0


def _clean(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_clean(v) for v in value]
    if isinstance(value, (np.floating, float)):
        return None if not np.isfinite(value) else float(value)
    if isinstance(value, np.integer):
        return int(value)
    if isinstance(value, np.bool_):
        return bool(value)
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    return value


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--stage", required=True, choices=("snapshot", "preflight", "measure"))
    parser.add_argument("--trades", default=str(TRADES_CSV))
    parser.add_argument("--prices", default=str(PRICES_CSV))
    parser.add_argument("--pins", default=str(PINS_DIR))
    parser.add_argument("--out-dir", default="backtests/btc_veto")
    parser.add_argument("--run", default=None, help="snapshot koşusunun kimliği (manifest'e yazılır)")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    try:
        args = _parse_args(argv)
    except SystemExit as exc:
        return int(exc.code or 0) if exc.code in (0, None) else 2
    config = load_config()
    if args.stage == "snapshot":
        return run_snapshot(args, config)
    return run_analysis(args, config, measure=args.stage == "measure")


if __name__ == "__main__":
    sys.exit(main())

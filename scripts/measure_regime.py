#!/usr/bin/env python3
"""Rejim koşullu performans ölçümü (docs/backtest.md > 6l, TADİLAT-1). Ölçümün parçası DEĞİL.

Ön-kayıt (`5312a95`, TADİLAT-1 `6efc1a7`) bu betikten ÖNCE, hiçbir veri görülmeden commit
edildi. Betik o metni MEKANİK olarak uygular: rejim tanımı, atama, karşıtlıklar, BH, asgari
örneklem, A → B doğrulama, kontrol etiketi. Hiçbir sayı burada SEÇİLMEZ.

**Yeni model YOK, ikinci bir backtest YOK.** İşlem satırları karar 59'un düzeltilmiş
koşularından OKUNUR (ema, dc: artifact'ler) ya da onların yolu çağrılarak bir kez yeniden
üretilir (xsec: `scripts/backtest_xsec.py`, workflow'un ayrı adımında). Pozisyon ve R
`core/metrics.py::merge_fills` + `r_multiple`tan gelir; küme bootstrap'ı ve MDE
`scripts/backtest_dc.py`nin fonksiyonlarıdır. Burada hesaplanan YALNIZCA: rejim etiketi,
fark-içinde-fark (DiD) çekilişleri, bootstrap p değeri ve BH.

**Salt okunur:** deftere, config'e ve `data/cache/`e yazmaz (BTC serisi koşuya özel geçici
önbellekten okunur). Kullanılan günlük BTC serisi ve etiketleri sonuç yüküne SABİTLENİR.

İKİ AŞAMA: `preflight` HİÇBİR R, pozisyon sayısı ya da işlem-rejim ataması üretmez — yalnızca
artifact'lerin erişilebilirliğini (dosya varlığı) ve BTC serisinin KAPSAMINI (ilk/son bar, eksik
gün, ilk tanımlı gün) raporlar ve tekrarlanabilir; `measure` tek seferliktir (§6l > 7). Ayrım,
bir altyapı aksiliğinin tek seferlik ölçümü yakmaması içindir.

Çıkış kodları: 0 = rapor yazıldı; 3 = veri kapısı (artifact kaydı tutmuyor, BTC verisi yok —
rapor YAZILMAZ); 2 = kullanım hatası. Tetikleyicisi `.github/workflows/measure-regime.yml`.
"""

from __future__ import annotations

import argparse
import copy
import json
import logging
import math
import random
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from core.config import get_setting, load_config  # noqa: E402
from core.data import fetch_ohlcv  # noqa: E402
from core.layers import resolve_layer  # noqa: E402
from core.ledger import Ledger  # noqa: E402
from core.metrics import merge_fills, r_multiple  # noqa: E402
from core.tags import find_tag  # noqa: E402
from scripts.backtest_dc import (  # noqa: E402
    MIN_CLUSTERS,
    _percentiles,
    cluster_diff_draws,
    cluster_mean_ci,
    precision,
    precision_diff,
)
from scripts.rerun_record import parse_record  # noqa: E402

logger = logging.getLogger("measure_regime")

# --- Ön-kayıtlı sayılar (§6l > 4, 6). Hiçbiri CLI girdisi DEĞİLDİR ve sonuca göre değişmez. ---
SMA_DAYS = 200
VOL_DAYS = 30
VOL_MEDIAN_DAYS = 365
BH_Q = 0.05                      # §6l > 6 (TADİLAT-1 onayı): aileye özgü, §6c'nin 0.10'u değil
FAMILY_A = ("H1a", "H1b", "H2")  # m = 3; H3 aileye girmez (§6l > 6)
REGIME_DATA_START = pd.Timestamp("2020-11-01T00:00:00Z")   # §6l > 4: A'dan ≥ 395 gün önce
DAY_CLOSE_BAR_HOUR = 20          # §6l > 4: UTC gününün son 4H barı (20:00 açılışlı)

UP, DOWN = "yukari", "asagi"
HIGH, LOW = "yuksek", "dusuk"

LABEL_MODEL = "MODELDEN"
LABEL_MARKET = "PİYASADAN"
LABEL_UNKNOWN = "KAYNAĞI AYIRT EDİLEMEDİ"
CONTROL_BROKEN = "DEĞERLENDİRİLEMEZ — kontrol bozuk (karar 60)"

RERUN_RECORDS = {
    "ema": (".github/triggers/rerun-59-backtest-ema.run", "backtest-ema.yml"),
    "dc": (".github/triggers/rerun-59-backtest-dc.run", "backtest-dc.yml"),
    "xsec": (".github/triggers/rerun-59-backtest-xsec.run", "backtest-xsec.yml"),
}

# Karar 59'un KAYDI (artifact'in doğru koşuya ait olduğunun sınaması, §6l > 7). Sayı
# kayıttaki hassasiyette karşılaştırılır; kayıtta olmayan bir değer burada YOKTUR.
#   (kaynak, dönem, model) -> (pozisyon, ort. R, ondalık basamak)
RECORDED = {
    ("ema-portfolio", "A", "ema_trend"): (369, -0.001489, 6),   # karar 59 > sıra 4 (#35983894505, A birebir)
    ("dc-portfolio", "A", "dc_short"): (336, -0.0486, 4),       # karar 59 > sıra 2
    ("dc-portfolio", "A", "dc_coinflip"): (407, -0.0937, 4),
    ("dc-portfolio", "B", "dc_short"): (246, 0.0516, 4),
}
# xsec A determinizm kapısı (§6l > 7): orijinal `results.json` yoksa yayımlanan hassasiyet.
XSEC_RECORDED_A = {"model": (183, 0.118, 3), "control": (314, 0.039, 3)}


def matches_recorded(value: float | None, recorded: float, digits: int) -> bool:
    """Kayıttaki hassasiyette eşit mi: |fark| ≤ yarım son basamak (yuvarlama yönünden bağımsız)."""
    return value is not None and abs(float(value) - recorded) <= 0.5 * 10.0 ** (-digits) + 1e-12


class DataGateError(RuntimeError):
    """Ölçülmek istenen veri ölçülemiyor: rapor yazılmaz, çıkış 3 (karar 51)."""


# --------------------------------------------------------------------------- #
# Birimler (§6l > 5)
# --------------------------------------------------------------------------- #
@dataclass(frozen=True, kw_only=True)
class Unit:
    key: str
    model: str
    axis: str                      # "direction" | "vol"
    favoured: str                  # lehte marjinal
    against: str                   # aleyhte marjinal
    primary: str                   # birincil kaynak
    secondary: str | None = None   # ikincil (bağlayıcı DEĞİL)
    control: str | None = None     # kontrol modeli (DiD kaynağında)
    did_source: str | None = None  # DiD'in kurulduğu kaynak (model ↔ kontrol AYNI koşu tipi)
    control_broken: bool = False
    arm: str | None = None


UNITS: tuple[Unit, ...] = (
    Unit(key="H1a", model="ema_trend", axis="direction", favoured=UP, against=DOWN,
         primary="ema-singles", secondary="ema-portfolio", control="random_ctrl",
         control_broken=True),
    Unit(key="H1b", model="xsec_mom", axis="direction", favoured=UP, against=DOWN,
         primary="xsec-portfolio", control="xsec_random", did_source="xsec-portfolio"),
    Unit(key="H2", model="dc_short", axis="direction", favoured=DOWN, against=UP,
         primary="dc-singles", secondary="dc-portfolio", control="dc_coinflip",
         did_source="dc-portfolio"),
    Unit(key="H3a", model="meanrev", axis="vol", favoured=LOW, against=HIGH,
         primary="live-base", control="random_ctrl", control_broken=True),
    Unit(key="H3b", model="scalp_fixed", axis="vol", favoured=LOW, against=HIGH,
         primary="live-scalp", control="scalp_coinflip", did_source="live-scalp",
         arm="rsi2_reversal"),
)
LIVE_SOURCES = {"live-base", "live-scalp"}


# --------------------------------------------------------------------------- #
# Rejim (§6l > 4)
# --------------------------------------------------------------------------- #
def daily_closes(btc: pd.DataFrame) -> pd.Series:
    """UTC günü -> o günün 20:00 açılışlı 4H barının kapanışı (= günün 24:00 fiyatı).

    Takvim TAM kurulur: 20:00 barı olmayan gün NaN kalır ve pencere hesaplarında o günü
    kapsayan her değer tanımsızlaşır — eksik günü atlayıp "200 gün" demek, 200 günden uzun
    bir takvimi ortalamak olurdu.
    """
    index = pd.DatetimeIndex(btc.index)
    if index.tz is None:
        index = index.tz_localize("UTC")
    else:
        index = index.tz_convert("UTC")
    closes = pd.Series(btc["close"].to_numpy(dtype="float64"), index=index)
    last_bars = closes[index.hour == DAY_CLOSE_BAR_HOUR]
    days = last_bars.index.floor("D")
    series = pd.Series(last_bars.to_numpy(), index=days)
    if series.empty:
        return series
    full = pd.date_range(series.index[0], series.index[-1], freq="D", tz="UTC")
    return series.reindex(full)


def regime_table(closes: pd.Series) -> pd.DataFrame:
    """Gün başına yön ve oynaklık etiketi. Pencereler d'yi DÂHİL eder; eşitlik aşağı/düşük."""
    sma = closes.rolling(SMA_DAYS, min_periods=SMA_DAYS).mean()
    log_returns = np.log(closes / closes.shift(1))
    vol = log_returns.rolling(VOL_DAYS, min_periods=VOL_DAYS).std(ddof=1)
    vol_median = vol.rolling(VOL_MEDIAN_DAYS, min_periods=VOL_MEDIAN_DAYS).median()
    direction = pd.Series(None, index=closes.index, dtype="object")
    defined = closes.notna() & sma.notna()
    direction[defined] = np.where(closes[defined] > sma[defined], UP, DOWN)
    vol_label = pd.Series(None, index=closes.index, dtype="object")
    defined_vol = vol.notna() & vol_median.notna()
    vol_label[defined_vol] = np.where(vol[defined_vol] > vol_median[defined_vol], HIGH, LOW)
    return pd.DataFrame(
        {
            "close": closes,
            "sma200": sma,
            "vol30": vol,
            "vol_median365": vol_median,
            "direction": direction,
            "vol": vol_label,
        }
    )


def regime_day(opened_at: pd.Timestamp) -> pd.Timestamp:
    """`opened_at` anında KAPANMIŞ son UTC günü: max{d : d+1 00:00 ≤ opened_at}."""
    stamp = opened_at.tz_convert("UTC") if opened_at.tzinfo else opened_at.tz_localize("UTC")
    return (stamp - pd.Timedelta(days=1)).floor("D")


def regime_at(table: pd.DataFrame, opened_at: pd.Timestamp) -> tuple[str, str] | None:
    day = regime_day(opened_at)
    if day not in table.index:
        return None
    row = table.loc[day]
    if not isinstance(row["direction"], str) or not isinstance(row["vol"], str):
        return None
    return row["direction"], row["vol"]


# --------------------------------------------------------------------------- #
# Pozisyonlar — R'nin TEK tanımı core/metrics.py
# --------------------------------------------------------------------------- #
@dataclass(frozen=True, kw_only=True)
class Pos:
    symbol: str
    opened_at: pd.Timestamp
    r: float


@dataclass(kw_only=True)
class Loaded:
    positions: list[Pos] = field(default_factory=list)
    unmeasured: int = 0            # R'si bilinmeyen (risk yok) pozisyon
    missing_ledgers: list[str] = field(default_factory=list)


def positions_of(rows: Iterable[Mapping[str, Any]], *, arm: str | None = None) -> tuple[list[Pos], int]:
    out: list[Pos] = []
    unmeasured = 0
    for row in merge_fills(rows):
        if arm is not None and find_tag(str(row.get("signal_reason", "")), "arm") != arm:
            continue
        r = r_multiple(row)
        if r is None:
            unmeasured += 1
            continue
        out.append(Pos(symbol=str(row["symbol"]), opened_at=pd.Timestamp(row["opened_at"]), r=float(r)))
    return out, unmeasured


def load_ledger(root: Path, model: str, *, arm: str | None = None) -> Loaded:
    loaded = Loaded()
    if not (root / model / "trades.csv").is_file():
        loaded.missing_ledgers.append(str(root / model))
        return loaded
    loaded.positions, loaded.unmeasured = positions_of(Ledger(root).read_trades(model), arm=arm)
    return loaded


def load_source(
    source: str, period: str, model: str, *, dirs: Mapping[str, Path], symbols: Mapping[str, Sequence[str]],
    arm: str | None = None,
) -> Loaded:
    """Kaynak adı -> defter kök(ler)i. Tek-sembollü kaynak 13 defterin BİRLEŞİMİDİR."""
    family, kind = source.split("-", 1)
    if family == "live":
        root = dirs["live-base"] if kind == "base" else dirs["live-scalp"]
        return load_ledger(root, model, arm=arm)
    base = dirs[family]
    if family == "xsec":
        return load_ledger(base / period / "ledger", model, arm=arm)
    if kind == "portfolio":
        return load_ledger(base / f"{period}-portfolio" / "ledger", model, arm=arm)
    merged = Loaded()
    for symbol in symbols[family]:
        part = load_ledger(base / f"{period}-{symbol}" / "ledger", model, arm=arm)
        merged.positions.extend(part.positions)
        merged.unmeasured += part.unmeasured
        merged.missing_ledgers.extend(part.missing_ledgers)
    return merged


# --------------------------------------------------------------------------- #
# İstatistik (§6l > 6)
# --------------------------------------------------------------------------- #
def month_of(stamp: pd.Timestamp) -> str:
    stamp = stamp.tz_convert("UTC") if stamp.tzinfo else stamp
    return f"{stamp.year:04d}-{stamp.month:02d}"   # backtest_dc.cluster_key(…, "month") ile aynı


def month_groups(positions: Iterable[Pos]) -> dict[str, list[float]]:
    groups: dict[str, list[float]] = {}
    for pos in positions:
        groups.setdefault(month_of(pos.opened_at), []).append(pos.r)
    return groups


def bootstrap_p(draws: Sequence[float]) -> float:
    """İki yönlü yüzdelik bootstrap p'si: min(1, 2·min(#≤0 + 1, #≥0 + 1) / (B + 1))."""
    below = sum(1 for d in draws if d <= 0.0)
    above = sum(1 for d in draws if d >= 0.0)
    return min(1.0, 2.0 * min(below + 1, above + 1) / (len(draws) + 1))


def bh_rejected(pvalues: Mapping[str, float], *, q: float) -> set[str]:
    """Benjamini-Hochberg step-up: `p₍ᵢ₎ ≤ (i/m)·q` sağlayan en büyük i'ye kadar reddedilir."""
    ordered = sorted(pvalues.items(), key=lambda kv: (kv[1], kv[0]))
    m = len(ordered)
    cutoff = 0
    for i, (_, p) in enumerate(ordered, start=1):
        if p <= i / m * q:
            cutoff = i
    return {key for key, _ in ordered[:cutoff]}


def did_draws(
    model_fav: Mapping[str, Sequence[float]],
    model_against: Mapping[str, Sequence[float]],
    ctrl_fav: Mapping[str, Sequence[float]],
    ctrl_against: Mapping[str, Sequence[float]],
    *,
    iterations: int,
    seed: str,
) -> tuple[list[float], int]:
    """`(Δ_model − Δ_kontrol)` ay-EŞLEŞTİRİLMİŞ çekilişleri; `cluster_diff_draws`ın dört gruplu hâli.

    Ay etiketleri her iterasyonda dört grubun aylarının BİRLEŞİMİNDEN bir kez çekilir; bir
    grubu boş kalan çekiliş atılır ve SAYILIR (tavan 4 × tekrar).
    """
    groups = (model_fav, model_against, ctrl_fav, ctrl_against)
    ids = sorted(set().union(*groups))
    sums = [[float(sum(g.get(i, ()))) for i in ids] for g in groups]
    counts = [[len(g.get(i, ())) for i in ids] for g in groups]
    rng = random.Random(seed)
    draws: list[float] = []
    dropped = attempts = 0
    while ids and len(draws) < iterations and attempts < 4 * iterations:
        attempts += 1
        s = [0.0] * 4
        c = [0] * 4
        for _ in ids:
            k = rng.randrange(len(ids))
            for g in range(4):
                s[g] += sums[g][k]
                c[g] += counts[g][k]
        if min(c) == 0:
            dropped += 1
            continue
        draws.append((s[0] / c[0] - s[1] / c[1]) - (s[2] / c[2] - s[3] / c[3]))
    return draws, dropped


def _mean(values: Sequence[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _flat(groups: Mapping[str, Sequence[float]]) -> list[float]:
    return [r for rs in groups.values() for r in rs]


@dataclass(frozen=True, kw_only=True)
class Settings:
    iterations: int
    alpha: float
    seed: int
    min_trades: int


def describe(groups: Mapping[str, Sequence[float]], *, settings: Settings, seed: str) -> dict[str, Any]:
    """Bir hücre ya da marjinal: n, ay, ort. R, %95 küme aralığı, MDE (§6l > 6 > Rapor)."""
    values = _flat(groups)
    ci = cluster_mean_ci(groups, definition="month", alpha=settings.alpha,
                         iterations=settings.iterations, seed=seed) if groups else None
    prec = precision(groups) if groups else {"n": 0, "clusters": 0, "evaluable": False}
    return {
        "n": len(values),
        "months": len(groups),
        "avg_r": _mean(values),
        "ci_low": ci.low if ci else None,
        "ci_high": ci.high if ci else None,
        "ci_evaluable": bool(ci and ci.evaluable),
        "mde": prec.get("mde"),
        "sd": prec.get("sd"),
        "deff": prec.get("deff"),
        "n_effective": prec.get("n_effective"),
    }


def sample_ok(groups: Mapping[str, Sequence[float]], *, settings: Settings) -> bool:
    """Asgari örneklem (§6l > 6): ≥ 10 ay kümesi VE ≥ `acceptance.min_trades` pozisyon."""
    return len(groups) >= MIN_CLUSTERS and len(_flat(groups)) >= settings.min_trades


def contrast_block(
    positions: Sequence[Pos], table: pd.DataFrame, unit: Unit, *, settings: Settings, seed: str,
) -> dict[str, Any]:
    """Hücreler, marjinaller, karşıtlık (lehte − aleyhte), lehte aralığı, p, MDE."""
    cells: dict[str, list[Pos]] = {}
    undefined = 0
    for pos in positions:
        regime = regime_at(table, pos.opened_at)
        if regime is None:
            undefined += 1
            continue
        cells.setdefault(f"{regime[0]}|{regime[1]}", []).append(pos)
    axis_index = 0 if unit.axis == "direction" else 1
    fav = [p for key, ps in cells.items() for p in ps if key.split("|")[axis_index] == unit.favoured]
    against = [p for key, ps in cells.items() for p in ps if key.split("|")[axis_index] == unit.against]
    fav_g, against_g = month_groups(fav), month_groups(against)

    block: dict[str, Any] = {
        "positions": len(positions),
        "undefined_regime": undefined,
        "cells": {
            key: describe(month_groups(cells.get(key, [])), settings=settings, seed=f"{seed}:cell:{key}")
            for key in (f"{d}|{v}" for d in (UP, DOWN) for v in (HIGH, LOW))
        },
        "favoured": {"label": unit.favoured, **describe(fav_g, settings=settings, seed=f"{seed}:lehte")},
        "against": {"label": unit.against, **describe(against_g, settings=settings, seed=f"{seed}:aleyhte")},
    }
    point = (
        _mean(_flat(fav_g)) - _mean(_flat(against_g))
        if fav_g and against_g else None
    )
    draws, dropped = (
        cluster_diff_draws(fav_g, against_g, iterations=settings.iterations, seed=seed)
        if fav_g and against_g else ([], 0)
    )
    complete = len(draws) == settings.iterations and settings.iterations > 0
    low, high = _percentiles(draws, settings.alpha) if complete else (None, None)
    minimum = sample_ok(fav_g, settings=settings) and sample_ok(against_g, settings=settings)
    evaluable = minimum and complete
    prec = precision_diff(fav_g, against_g) if fav_g and against_g else {}
    block["contrast"] = {
        "point": point,
        "ci_low": low,
        "ci_high": high,
        "p": bootstrap_p(draws) if complete else 1.0,
        "dropped_draws": dropped,
        "months": len(set(fav_g) | set(against_g)),
        "mde": prec.get("mde"),
        "deff": prec.get("deff"),
        "minimum_sample": minimum,
        "evaluable": evaluable,
    }
    block["_groups"] = (fav_g, against_g)   # DiD için; yüke yazılmaz
    return block


def _strip(block: Mapping[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in block.items() if not k.startswith("_")}


def unit_passes(block: Mapping[str, Any], *, rejected: bool) -> bool:
    """(a) karşıtlık > 0 ∧ BH-reddedildi; (b) lehte marjinal %95 alt sınırı > 0."""
    contrast = block["contrast"]
    fav = block["favoured"]
    cond_a = bool(contrast["evaluable"] and contrast["point"] is not None and contrast["point"] > 0 and rejected)
    cond_b = bool(fav["ci_evaluable"] and fav["ci_low"] is not None and fav["ci_low"] > 0)
    return cond_a and cond_b


def control_label(unit: Unit, did: Mapping[str, Any] | None, control: Mapping[str, Any] | None) -> str:
    """§6l > 8 + TADİLAT-1. H1a hiçbir koşulda MODELDEN olamaz; dc'nin etiketi portföy DiD'inden."""
    if unit.control_broken:
        return LABEL_UNKNOWN
    if did is None or control is None or not did.get("evaluable"):
        return LABEL_UNKNOWN
    if did["ci_low"] > 0:
        return LABEL_MODEL
    ctrl = control["contrast"]
    if (did["ci_low"] <= 0 <= did["ci_high"] and ctrl["evaluable"]
            and ctrl["ci_low"] is not None and ctrl["ci_low"] > 0):
        return LABEL_MARKET
    return LABEL_UNKNOWN


def did_block(model_block: Mapping[str, Any], control_block: Mapping[str, Any], *,
              settings: Settings, seed: str) -> dict[str, Any]:
    mf, ma = model_block["_groups"]
    cf, ca = control_block["_groups"]
    draws, dropped = did_draws(mf, ma, cf, ca, iterations=settings.iterations, seed=seed)
    complete = len(draws) == settings.iterations and settings.iterations > 0
    months = len(set(mf) | set(ma) | set(cf) | set(ca))
    evaluable = bool(complete and months >= MIN_CLUSTERS
                     and model_block["contrast"]["minimum_sample"]
                     and control_block["contrast"]["minimum_sample"])
    low, high = _percentiles(draws, settings.alpha) if complete else (None, None)
    mc, cc = model_block["contrast"]["point"], control_block["contrast"]["point"]
    return {
        "point": (mc - cc) if mc is not None and cc is not None else None,
        "ci_low": low,
        "ci_high": high,
        "dropped_draws": dropped,
        "months": months,
        "evaluable": evaluable,
    }


# --------------------------------------------------------------------------- #
# Kapılar: artifact kaydı ve xsec determinizmi (§6l > 7)
# --------------------------------------------------------------------------- #
def check_recorded(summaries: Mapping[tuple[str, str, str], tuple[int, float | None]]) -> list[str]:
    """Okunan defterin özeti karar 59'un kaydıyla tutmuyorsa sebep listesi (boş = tuttu)."""
    problems: list[str] = []
    for key, (n, avg, digits) in RECORDED.items():
        got = summaries.get(key)
        if got is None:
            problems.append(f"{key}: okunmadı")
            continue
        got_n, got_avg = got
        if got_n != n or not matches_recorded(got_avg, avg, digits):
            problems.append(f"{key}: kayıt {n} / {avg} ↔ okunan {got_n} / {got_avg}")
    return problems


def xsec_gate(regenerated: Mapping[str, Any] | None, original: Mapping[str, Any] | None) -> tuple[bool, str]:
    """Dönem A birebir mi? Orijinal `results.json` varsa alan bazında, yoksa kayıttaki hassasiyette."""
    if regenerated is None:
        return False, "yeniden üretilmiş xsec results.json yok"
    reg_a = regenerated["periods"]["A"]
    if original is not None:
        org_a = original["periods"]["A"]
        for role in ("model", "control"):
            for field_name in ("trades", "avg_r"):
                a, b = reg_a[role][field_name], org_a[role][field_name]
                if a is None or b is None or not math.isclose(float(a), float(b), rel_tol=0.0, abs_tol=1e-12):
                    return False, f"A {role}.{field_name}: orijinal {b} ↔ yeniden {a}"
        return True, "A birebir (orijinal results.json ile alan bazında)"
    for role, (n, avg, digits) in XSEC_RECORDED_A.items():
        row = reg_a[role]
        if row["trades"] != n or not matches_recorded(row["avg_r"], avg, digits):
            return False, f"A {role}: kayıt {n} / {avg} ↔ yeniden {row['trades']} / {row['avg_r']}"
    return True, "A kayıtla uyumlu (yayımlanan hassasiyette; orijinal results.json okunamadı)"


# --------------------------------------------------------------------------- #
# BTC verisi
# --------------------------------------------------------------------------- #
def regime_now(records: Mapping[str, tuple[str, str]] = RERUN_RECORDS) -> pd.Timestamp:
    """Seri sonu SEÇİLMEZ: üç yeniden koşu kaydının B sonlarının en geç olanı."""
    return max(parse_record(Path(path), workflow=wf) for path, wf in records.values())


def fetch_btc(config: Mapping[str, Any], *, now: pd.Timestamp, cache_dir: str) -> pd.DataFrame:
    bars = int(math.ceil((now - REGIME_DATA_START) / pd.Timedelta(hours=4))) + 12
    run_config = copy.deepcopy(dict(config))
    run_config["data"] = {**run_config["data"], "history_bars": bars, "cache_dir": cache_dir}
    symbol = str(get_setting(run_config, "exchange.btc_reference"))
    return fetch_ohlcv(run_config, symbol, now=now)


# --------------------------------------------------------------------------- #
# Koşu
# --------------------------------------------------------------------------- #
def evaluate(
    *,
    table: pd.DataFrame,
    dirs: Mapping[str, Path],
    symbols: Mapping[str, Sequence[str]],
    settings: Settings,
    xsec_ok: bool,
    xsec_reason: str,
) -> dict[str, Any]:
    """Tüm birimler, iki dönem, BH, etiketler. Salt hesap — ağ ve disk yazımı yok."""
    periods: dict[str, dict[str, Any]] = {"A": {}, "B": {}}
    raw: dict[tuple[str, str, str], dict[str, Any]] = {}

    def block_for(unit: Unit, source: str, period: str, model: str) -> dict[str, Any]:
        cache_key = (source, period, model)
        if cache_key not in raw:
            loaded = load_source(source, period, model, dirs=dirs, symbols=symbols, arm=unit.arm)
            seed = f"{settings.seed}:regime:{unit.key}:{source}:{period}"
            if model != unit.model:
                seed += f":kontrol:{model}"
            block = contrast_block(loaded.positions, table, unit, settings=settings, seed=seed)
            block["unmeasured"] = loaded.unmeasured
            block["missing_ledgers"] = loaded.missing_ledgers
            raw[cache_key] = block
        return raw[cache_key]

    for period in ("A", "B"):
        for unit in UNITS:
            entry: dict[str, Any] = {"model": unit.model, "axis": unit.axis,
                                     "favoured": unit.favoured, "against": unit.against}
            if unit.primary in LIVE_SOURCES:
                if period == "B":
                    continue
                entry["note"] = "canlı defter: dönem A/B yok, aileye girmez (§6l > 6)"
            if unit.key == "H1b" and not xsec_ok:
                entry["measured"] = False
                entry["reason"] = f"xsec determinizm kapısı: {xsec_reason}"
                periods[period][unit.key] = entry
                continue
            entry["measured"] = True
            primary = block_for(unit, unit.primary, period, unit.model)
            entry["primary"] = {"source": unit.primary, **_strip(primary)}
            if unit.secondary:
                entry["secondary"] = {"source": unit.secondary,
                                      **_strip(block_for(unit, unit.secondary, period, unit.model))}
            if unit.control_broken:
                entry["control"] = CONTROL_BROKEN
            elif unit.control and unit.did_source:
                model_did = block_for(unit, unit.did_source, period, unit.model)
                ctrl = block_for(unit, unit.did_source, period, unit.control)
                did = did_block(model_did, ctrl, settings=settings,
                                seed=f"{settings.seed}:regime:{unit.key}:{unit.did_source}:{period}:did")
                entry["control"] = {"model": unit.control, "source": unit.did_source, **_strip(ctrl)}
                entry["did"] = did
            periods[period][unit.key] = entry

    # --- Dönem A: aile m = 3, BH q = 0.05 ------------------------------------
    p_a = {}
    for key in FAMILY_A:
        entry = periods["A"][key]
        p_a[key] = entry["primary"]["contrast"]["p"] if entry.get("measured") and entry["primary"]["contrast"]["evaluable"] else 1.0
    rejected_a = bh_rejected(p_a, q=BH_Q)
    passed_a = []
    for key in FAMILY_A:
        entry = periods["A"][key]
        passed = bool(entry.get("measured")) and unit_passes(entry["primary"], rejected=key in rejected_a)
        entry["bh"] = {"p": p_a[key], "m": len(FAMILY_A), "q": BH_Q, "rejected": key in rejected_a}
        if not entry.get("measured"):
            entry["verdict"] = "ÖLÇÜLMEDİ = GEÇMEDİ"   # p = 1 ile aileye girdi; m küçültülmez
        elif not entry["primary"]["contrast"]["evaluable"]:
            entry["verdict"] = "DEĞERLENDİRİLEMEZ = GEÇMEDİ"
        else:
            entry["verdict"] = "GEÇTİ" if passed else "GEÇMEDİ"
        if passed:
            passed_a.append(key)

    # --- Dönem B: yalnızca A'da geçenler doğrulanır, m_B = |geçen| -------------
    p_b = {key: periods["B"][key]["primary"]["contrast"]["p"]
           if periods["B"][key]["primary"]["contrast"]["evaluable"] else 1.0 for key in passed_a}
    rejected_b = bh_rejected(p_b, q=BH_Q) if p_b else set()
    for key in FAMILY_A:
        entry = periods["B"][key]
        if key not in passed_a:
            entry["verdict"] = "bilgi — doğrulama değil (A'da geçmedi)"
            continue
        passed = unit_passes(entry["primary"], rejected=key in rejected_b)
        entry["bh"] = {"p": p_b[key], "m": len(passed_a), "q": BH_Q, "rejected": key in rejected_b}
        entry["verdict"] = "GEÇTİ" if passed else "GEÇMEDİ"

    # --- H3: canlı, aileye girmez; asgari örneklem kuralı mekanik uygulanır ----
    for key in ("H3a", "H3b"):
        entry = periods["A"][key]
        contrast = entry["primary"]["contrast"]
        entry["verdict"] = (
            "DEĞERLENDİRİLEMEZ" if not contrast["evaluable"]
            else "ölçüldü ama AİLE DIŞI — karar üretmez (§6l > 6)")

    # --- Etiketler ve nihai hüküm ---------------------------------------------
    summary: dict[str, Any] = {}
    for key in FAMILY_A:
        unit = next(u for u in UNITS if u.key == key)
        labels = {}
        for period in ("A", "B"):
            entry = periods[period][key]
            if not entry.get("measured"):
                labels[period] = None
                continue
            ctrl = entry.get("control") if isinstance(entry.get("control"), dict) else None
            labels[period] = control_label(unit, entry.get("did"), ctrl)
            entry["label"] = labels[period]
        a_ok = periods["A"][key].get("verdict") == "GEÇTİ"
        b_ok = periods["B"][key].get("verdict") == "GEÇTİ"
        verdict = ("DOĞRULANDI" if a_ok and b_ok else "DOĞRULANMADI" if a_ok
                   else periods["A"][key].get("verdict"))
        summary[key] = {
            "model": unit.model,
            "verdict": verdict,
            "label_A": labels["A"],
            "label_B": labels["B"],
            # Ön-kayıt "DOĞRULANDI ∧ MODELDEN" der; iki dönemin etiketi AYRI raporlanır ve
            # cümle yalnızca İKİSİ DE MODELDEN iken yazılabilir (dar okuma).
            "model_specific_claim": verdict == "DOĞRULANDI" and labels["A"] == labels["B"] == LABEL_MODEL,
        }
        if unit.control_broken:
            # TADİLAT-1: kontrolsüz bir karşıtlık bir mekanizmanın kanıtı sayılmaz.
            summary[key]["veto_input"] = "HAYIR — kontrol bozuk (TADİLAT-1)"
    return {"periods": periods, "summary": summary}


def required_ledgers(dirs: Mapping[str, Path], symbols: Mapping[str, Sequence[str]]) -> dict[str, list[Path]]:
    """Ölçümün okuyacağı defter dosyaları, ZORUNLU (portföy) ve tek-sembollü olarak."""
    portfolio: list[Path] = []
    singles: list[Path] = []
    for period in ("A", "B"):
        portfolio.append(dirs["ema"] / f"{period}-portfolio" / "ledger" / "ema_trend" / "trades.csv")
        for model in ("dc_short", "dc_coinflip"):
            portfolio.append(dirs["dc"] / f"{period}-portfolio" / "ledger" / model / "trades.csv")
        for symbol in symbols["ema"]:
            singles.append(dirs["ema"] / f"{period}-{symbol}" / "ledger" / "ema_trend" / "trades.csv")
        for symbol in symbols["dc"]:
            singles.append(dirs["dc"] / f"{period}-{symbol}" / "ledger" / "dc_short" / "trades.csv")
    return {"portfolio": portfolio, "singles": singles}


def preflight(args: argparse.Namespace, *, dirs: Mapping[str, Path],
              symbols: Mapping[str, Sequence[str]], config: Mapping[str, Any]) -> int:
    """Salt KAPSAM: dosya varlığı + BTC serisinin sınırları. Hiçbir R/pozisyon/etiket okunmaz."""
    required = required_ledgers(dirs, symbols)
    missing = {kind: [str(p) for p in paths if not p.is_file()] for kind, paths in required.items()}
    xsec_original = bool(args.xsec_original) and Path(args.xsec_original).is_file()
    now = regime_now()
    btc = fetch_btc(config, now=now, cache_dir=args.cache_dir or tempfile.mkdtemp(prefix="regime-"))
    report: dict[str, Any] = {
        "stage": "preflight",
        "ledgers": {kind: {"required": len(paths), "missing": missing[kind]} for kind, paths in required.items()},
        "xsec_original_results": xsec_original,
        "btc": {"bars": int(len(btc)), "series_end": str(now)},
    }
    if not btc.empty:
        table = regime_table(daily_closes(btc))
        report["btc"].update({
            "first_bar": str(btc.index[0]),
            "last_bar": str(btc.index[-1]),
            "reaches_regime_data_start": bool(btc.index[0] <= REGIME_DATA_START),
            "missing_days": int(table["close"].isna().sum()),
            "first_defined_day": _first_defined(table),
        })
    print("=== PREFLIGHT BEGIN ===")
    print(json.dumps(report, indent=2, ensure_ascii=False))
    print("=== PREFLIGHT END ===")
    blocking = bool(missing["portfolio"]) or btc.empty or not xsec_original
    return 3 if blocking else 0


def run(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    acceptance = get_setting(config, "acceptance")
    settings = Settings(
        iterations=int(acceptance["bootstrap_samples"]),
        alpha=float(acceptance["edge_ci_alpha"]),
        seed=int(get_setting(config, "random_seed")),
        min_trades=int(acceptance["min_trades"]),
    )
    ema_layer = resolve_layer(config, "ema")
    dc_layer = resolve_layer(config, "dc")
    symbols = {"ema": list(ema_layer.symbols or []), "dc": list(dc_layer.symbols or [])}
    dirs = {
        "ema": Path(args.ema_dir),
        "dc": Path(args.dc_dir),
        "xsec": Path(args.xsec_dir),
        "live-base": Path(args.live_base),
        "live-scalp": Path(args.live_scalp),
    }

    if args.stage == "preflight":
        return preflight(args, dirs=dirs, symbols=symbols, config=ema_layer.config)

    # --- Kapı 1: artifact'ler karar 59'un koşularına mı ait? --------------------
    summaries: dict[tuple[str, str, str], tuple[int, float | None]] = {}
    for (source, period, model) in RECORDED:
        loaded = load_source(source, period, model, dirs=dirs, symbols=symbols)
        rs = [p.r for p in loaded.positions]
        summaries[(source, period, model)] = (len(rs), _mean(rs))
    problems = check_recorded(summaries)
    if problems:
        for problem in problems:
            logger.error("artifact kaydı tutmuyor: %s", problem)
        raise DataGateError("artifact'ler karar 59'un kaydıyla tutmuyor — rapor yazılmaz")

    # --- Kapı 2: xsec A determinizmi -------------------------------------------
    regenerated = _read_json(Path(args.xsec_dir) / "results.json")
    original = _read_json(Path(args.xsec_original)) if args.xsec_original else None
    xsec_ok, xsec_reason = xsec_gate(regenerated, original)
    (logger.info if xsec_ok else logger.error)("xsec determinizm kapısı: %s", xsec_reason)

    # --- BTC rejimi --------------------------------------------------------------
    now = regime_now()
    cache_dir = args.cache_dir or tempfile.mkdtemp(prefix="regime-")
    btc = fetch_btc(ema_layer.config, now=now, cache_dir=cache_dir)
    if btc.empty:
        raise DataGateError("BTC serisi boş")
    table = regime_table(daily_closes(btc))
    missing_days = int(table["close"].isna().sum())

    result = evaluate(table=table, dirs=dirs, symbols=symbols, settings=settings,
                      xsec_ok=xsec_ok, xsec_reason=xsec_reason)
    payload = {
        "preregistration": "docs/backtest.md > 6l (5312a95, TADİLAT-1 6efc1a7)",
        "parameters": {"sma_days": SMA_DAYS, "vol_days": VOL_DAYS, "vol_median_days": VOL_MEDIAN_DAYS,
                       "bh_q": BH_Q, "family_a": list(FAMILY_A), "min_clusters": MIN_CLUSTERS,
                       "min_trades": settings.min_trades, "iterations": settings.iterations,
                       "alpha": settings.alpha, "seed": settings.seed},
        "sources": {
            "ema": {"run": args.ema_run, "artifact": args.ema_artifact},
            "dc": {"run": args.dc_run, "artifact": args.dc_artifact},
            "xsec": {"regenerated": True, "original_run": args.xsec_run, "gate": xsec_reason,
                     "gate_passed": xsec_ok,
                     "periods_regenerated": _periods_digest(regenerated),
                     "periods_original": _periods_digest(original)},
            "recorded_check": {"|".join(k): list(v) for k, v in summaries.items()},
        },
        "regime": {
            "series_end": str(now),
            "first_bar": str(btc.index[0]),
            "last_bar": str(btc.index[-1]),
            "first_day": str(table.index[0].date()),
            "last_day": str(table.index[-1].date()),
            "missing_days": missing_days,
            "first_defined_day": _first_defined(table),
            "days_file": "regime_days.csv",
        },
        **result,
    }
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / "results.json").write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str),
                                      encoding="utf-8")
    table.to_csv(out / "regime_days.csv", index_label="day")
    # Yük ve seri log'a da basılır: artifact deposu her ağ politikasından indirilemiyor.
    print("=== RESULTS.JSON BEGIN ===")
    print(json.dumps(payload, indent=2, ensure_ascii=False, default=str))
    print("=== RESULTS.JSON END ===")
    print("=== REGIME_DAYS.CSV BEGIN ===")
    print((out / "regime_days.csv").read_text(encoding="utf-8"), end="")
    print("=== REGIME_DAYS.CSV END ===")
    return 0


def _first_defined(table: pd.DataFrame) -> str | None:
    defined = table[table["direction"].map(lambda v: isinstance(v, str))
                    & table["vol"].map(lambda v: isinstance(v, str))]
    return str(defined.index[0].date()) if not defined.empty else None


def _periods_digest(payload: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if payload is None:
        return None
    return {
        period: {role: {k: (block.get(role) or {}).get(k) for k in ("trades", "avg_r")}
                 for role in ("model", "control")}
        for period, block in payload.get("periods", {}).items()
    }


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Rejim koşullu performans (docs/backtest.md > 6l).")
    parser.add_argument("--stage", choices=("preflight", "measure"), required=True,
                        help="preflight: yalnızca kapsam (tekrarlanabilir); measure: TEK SEFER")
    parser.add_argument("--config", default=None)
    parser.add_argument("--ema-dir", required=True, help="backtest-ema artifact'inin açıldığı dizin")
    parser.add_argument("--dc-dir", required=True, help="backtest-dc artifact'inin açıldığı dizin")
    parser.add_argument("--xsec-dir", required=True, help="yeniden üretilmiş xsec koşusunun dizini")
    parser.add_argument("--xsec-original", default="", help="#35981642832'nin results.json'ı")
    parser.add_argument("--live-base", default="ledgers")
    parser.add_argument("--live-scalp", default="ledgers_scalp")
    parser.add_argument("--ema-run", default="#35975935993")
    parser.add_argument("--ema-artifact", default="10799037756")
    parser.add_argument("--dc-run", default="#35981639682")
    parser.add_argument("--dc-artifact", default="10801320478")
    parser.add_argument("--xsec-run", default="#35981642832")
    parser.add_argument("--cache-dir", default="", help="koşuya özel BTC önbelleği (boş = geçici dizin)")
    parser.add_argument("--out-dir", default="backtests/regime")
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

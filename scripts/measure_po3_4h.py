#!/usr/bin/env python3
"""4H DÖNGÜ PO3 ölçülebilirlik SAYIMI (docs/backtest.md > 6v). SALT OKUNUR.

Cevapladığı soru tek: *her 4 saatlik mumun ilk saati (kutu) süpürülüp içeri kapandığında
tersine girmek, dönem A'da ölçülebilir SAYIDA ve maliyete yenilmeyecek bir GEOMETRİYLE
oluşuyor mu — ve sonuç ölçümü marj düzeyindeki etkiyi görecek GÜÇTE olur mu?*

**Bu bir strateji koşusu DEĞİLDİR.** Getiri, R, PnL, kazanma oranı burada ne hesaplanır ne
raporlanır; araç `core/metrics.py`, `core/portfolio.py`, `core/ledger.py`, `core/engine.py`
ve `strategies/*` modüllerini import ETMEZ (test: `tests/test_measure_po3_4h.py`). Gerekçe
`scripts/measure_po3.py`nin aynısıdır: kurulumun kaç kez oluştuğunu ve geometrisini görmek eşik
seçimini kirletmez, ne kazandırdığını görmek kirletirdi. Güç kapısının tek girdisi §6u'nun
(A2) zaten kayda geçmiş D varyansıdır; o dosya yalnızca özeti doğrulanmak ve σ_D'yi yeniden
üretmek için okunur — bu sayımın kurulumlarına hiçbir getiri bağlanmaz.

Deftere, `config.yaml`a, `strategies/`e ve `data/cache/`e YAZMAZ: mum önbelleği koşuya özel
bir dizine düşer.

## Kapsam bir KAPIDIR: yalnızca dönem A, `ema` katmanının 13 sembolü, 15m

Sınırlar `scripts/backtest_ema.py`den İTHAL EDİLİR. `--end` dönem A kesimini aşarsa çıkış 2;
mumlar kesimden ileriye hiç çekilmez (`now = --end`). Kasa (`scripts/vault.py`) erişimde değildir.
Isınma (yapı etiketi ve önceki gün/4H referansları) dönemden ÖNCEKİ 30 günden alınır.

## Tanımlar (§6v > 2; bu betik onları DEĞİŞTİRMEZ)

4H mumları 00:00 UTC hizalıdır; her biri 16 adet 15m bar (b1–b16).

- **Kutu**: b1–b4; `H_K = max(high)`, `L_K = min(low)`, `O_4H` = b1 açılışı. 16 barın biri
  eksikse `incomplete`, `H_K ≤ L_K` ise `flat`.
- **Pencere**: b5–b16. **Süpürme**: pencerede `high > H_K` / `low < L_K` olan İLK bar; iki taraf
  da süpürüldüyse `ambiguous`.
- **Geri dönüş**: süpürme barı DÂHİL, kapanışı `[L_K, H_K]` içinde olan ilk bar; aradaki dışarıda
  kalan bar sayısı sınırsız. Hiç yoksa `continuation`; b16'da ise giriş barı mumun içinde
  kalmaz → `no_entry_bar`.
- **Giriş**: geri dönüş barından sonraki barın açılışı, süpürmenin TERSİ (üst → short,
  alt → long). **Konum şartı**: long yalnızca giriş `< O_4H`, short yalnızca `> O_4H`
  (aksi `off_side`).
- **Stop**: süpürme ucunun (süpürme → geri dönüş barları arası uç) ötesinde
  `0.1 × (H_K − L_K)` (karar O1). **Hedef (GEÇİCİ)**: kutunun karşı tarafı (karar O2).
  Stop girişin yanlış tarafında ya da hedef girişte geçilmişse `invalid`.
- **Maliyet filtresi** (karar O3): iki yönde TEK eşik; %1.40'ta short'un stop kaymalı
  maliyet/R'si ≤ 0.15 ise %1.40, değilse %2.07. Kural config'ten çözülür (`derive_threshold`)
  ve ön-kayıtlı sonuçla (%2.07) ayrışırsa betik durur (çıkış 2). Eşiğin altı `narrow`,
  üstü `primary`.

Her 4H mum tam BİR sınıfa düşer (`CANDLE_KINDS`).

## Kapılar (§6v > 3; sabit, girdi DEĞİL)

1. Maliyet — sağlama: birincilin azami stop kaymalı maliyet/R'si ≤ 0.15 (tanım gereği).
2. Örneklem: birincil ≥ 300 **VE** ≥ 150 farklı takvim günü.
3. Güç: `MDE_proj(½·ΔR) = ½ · 2.802 · σ_D · ½·√(1/n_U + 1/n_L) · √DEFF_proj,t ≤ 0.15`,
   `DEFF_proj,t = max(1, 1 + (DEFF_A2,t − 1)(m̄_t − 1)/(m̄_A2,t − 1))`, t ∈ {gün, ISO hafta};
   bağlayıcı olan BÜYÜĞÜ. σ_D = 2.370 (`docs/data/po3_outcome_pairs.csv`; özet ve σ_D betik
   içinde doğrulanır, ayrışırsa çıkış 2).

Kapı kalırsa eşik ve kutu DEĞİŞTİRİLMEZ; sonuç "ölçülemez" diye kaydedilir ve PO3 ailesi park
edilir (§6v > 3, kullanıcı kararı). Betik yalnızca kapının mekanik sonucunu basar.

## Betimsel etiketler (§6v > 5; kapı DEĞİL)

(a) 4H yapı + hiza (`measure_po3.structure_at`, giriş barının açılışında); (b) süpürülen seviye
(önceki gün > önceki 4H > hiçbiri); (c) geri dönüş gecikmesi (bar); (d) r, r+1, r+2'de giriş
yönünde FVG; (e) FVG limit dolumu b16'ya kadar — tek istisna: girişten SONRAKİ barların yalnızca
high/low'una bakar, bir dolum olgusudur, R hesaplanmaz; (f) piyasa ve FVG limit girişi için
stop kaymalı maliyet/R (ikisi de taker); (g) karşı likidite ≥ 2R; (h) girişte kalan bar (1–11).
Referansı eksik olan etiket (önceki gün/4H tam değil) `tanımsız` yazılır.

## Çıkış kodları (karar 51: boş rapor yeşil dönmez)

- `0` — sayım yapıldı (kapının sonucu ne olursa olsun).
- `2` — kullanım hatası ya da ön-kayıt sabiti ayrıştı (eşik, σ_D özeti).
- `3` — VERİ KAPISI: hiçbir 4H mum sayılamadı.

Kullanım (depo kökünden):
    python scripts/measure_po3_4h.py --results-json out/po3_4h.json
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import logging
import math
import sys
import tempfile
from collections import Counter
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from core.config import load_config  # noqa: E402
from core.layers import resolve_layer  # noqa: E402
from scripts.backtest_ema import PERIOD_A_CUTOFF, PERIOD_A_START  # noqa: E402
from scripts.measure_po3 import (  # noqa: E402
    ALIGNMENTS,
    STRUCTURES,
    Dist,
    Pivots,
    StopCosts,
    dist,
    find_pivots,
    load_frames,
    stop_costs,
    structure_at,
    to_h4,
)
from scripts.vault import assert_before_vault  # noqa: E402

logger = logging.getLogger("measure-po3-4h")

LAYER = "ema"
TIMEFRAME = "15m"
BAR = pd.Timedelta(minutes=15)
H4 = pd.Timedelta(hours=4)
DAY = pd.Timedelta(days=1)
CANDLE_BARS = 16
DAY_BARS = 96
BOX_BARS = 4
LAST_REVERSAL = CANDLE_BARS - 2  # b15 (0 tabanlı 14): giriş barı mumun içinde kalmalı
WARMUP = pd.Timedelta(days=30)

# Ön-kayıtlı sabitler (§6v); girdi DEĞİLDİR.
BUFFER_FRACTION = 0.1
MAX_COST_PER_R = 0.15
NARROW_THRESHOLD = 0.0140
WIDE_THRESHOLD = 0.0207
PREREGISTERED_THRESHOLD = 0.0207
MIN_SETUPS = 300
MIN_DAYS = 150
MAX_MDE_HALF_DELTA = 0.15
Z_SUM = 2.802  # z(0.975) + z(0.80)
SIGMA_D = 2.370
PAIRS_PATH = Path(__file__).resolve().parent.parent / "docs" / "data" / "po3_outcome_pairs.csv"
PAIRS_SHA256 = "81ecfeb1bae93eda349a9e7a56e8ce3eaefd5a84e405ca60b1aac918129c2933"
# A2 (§6u): küme tanımı -> (DEFF, küme başına ortalama kurulum).
A2_DEFF: Mapping[str, tuple[float, float]] = {"day": (1.860, 1.797), "week": (1.559, 4.983)}

CANDLE_KINDS = (
    "incomplete", "flat", "none", "ambiguous", "continuation", "no_entry_bar",
    "off_side", "invalid", "narrow", "primary",
)
SWEPT_LEVELS = ("önceki gün", "önceki 4H", "hiçbiri", "tanımsız")
FVG_STATES = ("var", "yok", "değerlendirilemez")
FILL_STATES = ("dolar", "dolmaz")
LIQUIDITY_STATES = ("evet", "hayır", "yok", "tanımsız")


# --------------------------------------------------------------------------- #
# Birimler
# --------------------------------------------------------------------------- #
@dataclass(frozen=True, kw_only=True)
class Setup:
    """Bir kurulum. Getiri/R taşımaz — kimlik, giriş anındaki GEOMETRİ ve betimsel etiketler."""

    symbol: str
    candle: pd.Timestamp  # 4H mumun açılışı
    side: str  # süpürülen taraf: "up" | "down"
    sweep: int  # mum içi indeks (0 tabanlı; b1 = 0)
    reversal: int
    entry: float
    open_4h: float
    high_k: float
    low_k: float
    extreme: float
    structure: str | None = None
    swept_level: str = "tanımsız"
    fvg: str = "değerlendirilemez"
    fvg_fill: str | None = None
    fvg_limit: float | None = None
    liquidity: str = "tanımsız"

    @property
    def direction(self) -> str:
        return "short" if self.side == "up" else "long"

    @property
    def origin(self) -> str:
        return "U" if self.side == "up" else "L"

    @property
    def day(self) -> pd.Timestamp:
        return self.candle.normalize()

    @property
    def week(self) -> tuple[int, int]:
        iso = self.candle.isocalendar()
        return int(iso[0]), int(iso[1])

    @property
    def slot(self) -> int:
        return int(self.candle.hour)

    @property
    def entry_bar(self) -> pd.Timestamp:
        return self.candle + (self.reversal + 1) * BAR

    @property
    def width(self) -> float:
        return self.high_k - self.low_k

    @property
    def stop(self) -> float:
        buffer = BUFFER_FRACTION * self.width
        return self.extreme + buffer if self.side == "up" else self.extreme - buffer

    @property
    def target(self) -> float:
        return self.low_k if self.side == "up" else self.high_k

    @property
    def stop_distance(self) -> float:
        """Göreli; ≤ 0 ise stop girişin yanlış tarafında."""
        return distance(self.entry, self.stop, self.direction, adverse=True)

    @property
    def target_distance(self) -> float:
        """Göreli; ≤ 0 ise hedef girişte geçilmiş."""
        return distance(self.entry, self.target, self.direction, adverse=False)

    @property
    def on_side(self) -> bool:
        return self.entry < self.open_4h if self.direction == "long" else self.entry > self.open_4h

    @property
    def valid(self) -> bool:
        return self.stop_distance > 0.0 and self.target_distance > 0.0

    @property
    def reward_risk(self) -> float:
        return self.target_distance / self.stop_distance

    @property
    def delay(self) -> int:
        return self.reversal - self.sweep

    @property
    def bars_left(self) -> int:
        """Giriş barından b16'ya kadar, ikisi dâhil (1–11)."""
        return CANDLE_BARS - (self.reversal + 1)

    @property
    def alignment(self) -> str | None:
        if self.structure is None or self.structure in ("mixed", "undefined"):
            return self.structure
        return "aligned" if (self.direction == "long") == (self.structure == "up") else "against"

    def cost_per_r(self, costs: StopCosts) -> float:
        return costs.for_direction(self.direction) / self.stop_distance

    def fvg_cost_per_r(self, costs: StopCosts) -> float | None:
        if self.fvg_fill != "dolar" or self.fvg_limit is None:
            return None
        stop_distance = distance(self.fvg_limit, self.stop, self.direction, adverse=True)
        return costs.for_direction(self.direction) / stop_distance if stop_distance > 0 else None


def distance(entry: float, level: float, direction: str, *, adverse: bool) -> float:
    """Girişten seviyeye göreli mesafe; stop (adverse) ya da hedef tarafı pozitif."""
    raw = (level - entry) / entry
    against = raw if direction == "short" else -raw
    return against if adverse else -against


@dataclass(frozen=True, kw_only=True)
class Candle:
    """Tek sembol-4H mumun sınıfı. Her mum tam BİR sınıfa düşer."""

    symbol: str
    start: pd.Timestamp
    kind: str
    setup: Setup | None = None


@dataclass(frozen=True, kw_only=True)
class SymbolScan:
    symbol: str
    bars: int = 0
    first_bar: pd.Timestamp | None = None
    last_bar: pd.Timestamp | None = None
    candles: tuple[Candle, ...] = ()
    skipped: str | None = None

    def count(self, kind: str) -> int:
        return sum(1 for candle in self.candles if candle.kind == kind)

    @property
    def primary(self) -> list[Setup]:
        return [c.setup for c in self.candles if c.kind == "primary" and c.setup is not None]


# --------------------------------------------------------------------------- #
# Eşik ve sınıflandırma
# --------------------------------------------------------------------------- #
def derive_threshold(costs: StopCosts) -> float:
    """Karar O3: %1.40'ta short da maliyet/R ≤ 0.15'i sağlıyorsa %1.40, değilse %2.07.

    %2.07 kendisi de config'ten türeyenle (short gidiş-dönüş / 0.15) uyuşmalı ve ondan DAR
    olmamalı; uyuşmazsa NaN (betik durur).
    """
    if costs.short / NARROW_THRESHOLD <= MAX_COST_PER_R + 1e-12:
        return NARROW_THRESHOLD
    derived = costs.short / MAX_COST_PER_R
    if WIDE_THRESHOLD < derived - 1e-12 or WIDE_THRESHOLD - derived >= 1e-4:
        return math.nan
    return WIDE_THRESHOLD


def classify_candle(symbol: str, start: pd.Timestamp, frame: pd.DataFrame, *,
                    threshold: float) -> Candle:
    """`frame` sembolün TÜM barlarıdır; mum `start`tan itibaren 16 bar olarak dilimlenir."""
    bars = frame.loc[(frame.index >= start) & (frame.index < start + H4)]
    if len(bars) != CANDLE_BARS:
        return Candle(symbol=symbol, start=start, kind="incomplete")
    highs = bars["high"].to_numpy(dtype="float64")
    lows = bars["low"].to_numpy(dtype="float64")
    closes = bars["close"].to_numpy(dtype="float64")
    high_k, low_k = float(highs[:BOX_BARS].max()), float(lows[:BOX_BARS].min())
    if not high_k > low_k:
        return Candle(symbol=symbol, start=start, kind="flat")

    up = highs[BOX_BARS:] > high_k
    down = lows[BOX_BARS:] < low_k
    if up.any() and down.any():
        return Candle(symbol=symbol, start=start, kind="ambiguous")
    if not up.any() and not down.any():
        return Candle(symbol=symbol, start=start, kind="none")
    side = "up" if up.any() else "down"
    sweep = BOX_BARS + int(np.argmax(up if side == "up" else down))
    inside = np.flatnonzero((closes[sweep:] >= low_k) & (closes[sweep:] <= high_k))
    if inside.size == 0:
        return Candle(symbol=symbol, start=start, kind="continuation")
    reversal = sweep + int(inside[0])
    if reversal > LAST_REVERSAL:
        return Candle(symbol=symbol, start=start, kind="no_entry_bar")

    span = slice(sweep, reversal + 1)
    setup = Setup(
        symbol=symbol, candle=start, side=side, sweep=sweep, reversal=reversal,
        entry=float(bars["open"].iloc[reversal + 1]), open_4h=float(bars["open"].iloc[0]),
        high_k=high_k, low_k=low_k,
        extreme=float(highs[span].max()) if side == "up" else float(lows[span].min()),
    )
    if not setup.on_side:
        kind = "off_side"
    elif not setup.valid:
        kind = "invalid"
    elif setup.stop_distance < threshold:
        kind = "narrow"
    else:
        kind = "primary"
    if kind != "off_side":
        setup = label_bars(setup, highs, lows)
    return Candle(symbol=symbol, start=start, kind=kind, setup=setup)


def label_bars(setup: Setup, highs: np.ndarray, lows: np.ndarray) -> Setup:
    """(d) FVG ve (e) limit dolumu: yalnızca mumun KENDİ barları."""
    r = setup.reversal
    if r + 2 > CANDLE_BARS - 1:
        return replace(setup, fvg="değerlendirilemez")
    long = setup.direction == "long"
    gap = lows[r + 2] > highs[r] if long else highs[r + 2] < lows[r]
    if not gap:
        return replace(setup, fvg="yok")
    limit = float(lows[r + 2] if long else highs[r + 2])
    after = slice(r + 3, CANDLE_BARS)
    touched = bool((lows[after] <= limit).any()) if long else bool((highs[after] >= limit).any())
    return replace(setup, fvg="var", fvg_limit=limit, fvg_fill="dolar" if touched else "dolmaz")


def _complete_extremes(frame: pd.DataFrame, start: pd.Timestamp, span: pd.Timedelta,
                       bars: int) -> tuple[float, float] | None:
    chunk = frame.loc[(frame.index >= start) & (frame.index < start + span)]
    if len(chunk) != bars:
        return None
    return float(chunk["high"].max()), float(chunk["low"].min())


def label_context(setup: Setup, frame: pd.DataFrame, pivots: Pivots) -> Setup:
    """(a) yapı, (b) süpürülen seviye, (g) karşı likidite — girişten ÖNCE bilinen barlardan."""
    prev_day = _complete_extremes(frame, setup.day - DAY, DAY, DAY_BARS)
    prev_h4 = _complete_extremes(frame, setup.candle - H4, H4, CANDLE_BARS)
    up = setup.side == "up"

    if prev_day is None or prev_h4 is None:
        swept = "tanımsız"
    else:
        beyond = (lambda ref: setup.extreme > ref[0]) if up else (lambda ref: setup.extreme < ref[1])
        swept = "önceki gün" if beyond(prev_day) else "önceki 4H" if beyond(prev_h4) else "hiçbiri"

    refs = [ref for ref in (prev_day, prev_h4) if ref is not None]
    if not refs:
        liquidity = "tanımsız"
    else:
        # Hedef tarafı: long → yukarıdaki tepeler, short → aşağıdaki dipler.
        levels = [ref[0] for ref in refs if ref[0] > setup.entry] if setup.direction == "long" \
            else [ref[1] for ref in refs if ref[1] < setup.entry]
        if not levels:
            liquidity = "yok"
        else:
            nearest = min(levels, key=lambda level: abs(level - setup.entry))
            far = abs(nearest - setup.entry) / setup.entry >= 2.0 * setup.stop_distance
            liquidity = "evet" if far else "hayır"

    return replace(setup, structure=structure_at(pivots, setup.entry_bar),
                   swept_level=swept, liquidity=liquidity)


def period_candles(start: pd.Timestamp, end: pd.Timestamp) -> list[pd.Timestamp]:
    """00:00 hizalı, tamamı [start, end) içinde kalan 4H mumların açılışları."""
    first = start.ceil(H4)
    return list(pd.date_range(first, end - H4, freq=H4)) if first + H4 <= end else []


def scan_symbol(symbol: str, frame: pd.DataFrame, *, start: pd.Timestamp, end: pd.Timestamp,
                threshold: float) -> SymbolScan:
    if frame.empty:
        return SymbolScan(symbol=symbol, skipped="hiç bar yok")
    full = frame.loc[frame.index < end]  # etiketlerin ısınması dönemden ÖNCEKİ barları da okur
    window = full.loc[full.index >= start]
    if window.empty:
        return SymbolScan(symbol=symbol, skipped="dönem A'da bar yok")
    pivots = find_pivots(to_h4(full))
    listed = window.index[0].floor(H4)
    candles = []
    for opened in period_candles(start, end):
        if opened < listed:
            continue
        candle = classify_candle(symbol, opened, window, threshold=threshold)
        if candle.setup is not None and candle.kind != "off_side":
            candle = replace(candle, setup=label_context(candle.setup, full, pivots))
        candles.append(candle)
    return SymbolScan(symbol=symbol, bars=len(window), first_bar=window.index[0],
                      last_bar=window.index[-1], candles=tuple(candles))


# --------------------------------------------------------------------------- #
# Güç kapısı
# --------------------------------------------------------------------------- #
def projected_deff(definition: str, mean_cluster_size: float) -> float:
    deff_a2, m_a2 = A2_DEFF[definition]
    return max(1.0, 1.0 + (deff_a2 - 1.0) * (mean_cluster_size - 1.0) / (m_a2 - 1.0))


def projected_mde(n_u: int, n_l: int, deff: float) -> float:
    if n_u == 0 or n_l == 0:
        return math.inf
    return 0.5 * Z_SUM * SIGMA_D * 0.5 * math.sqrt(1.0 / n_u + 1.0 / n_l) * math.sqrt(deff)


CLUSTER_KEYS: Mapping[str, Callable[[Setup], Any]] = {
    "day": lambda setup: setup.day,
    "week": lambda setup: setup.week,
}


def power(primary: Sequence[Setup]) -> dict[str, Any]:
    n_u = sum(1 for s in primary if s.origin == "U")
    n_l = sum(1 for s in primary if s.origin == "L")
    rows: dict[str, Any] = {}
    for definition, key in CLUSTER_KEYS.items():
        clusters = len({key(s) for s in primary})
        mean = len(primary) / clusters if clusters else math.nan
        deff = projected_deff(definition, mean) if clusters else math.nan
        rows[definition] = {
            "clusters": clusters, "mean_cluster_size": mean, "deff_proj": deff,
            "mde_half_delta": projected_mde(n_u, n_l, deff) if clusters else math.inf,
        }
    binding = max(row["mde_half_delta"] for row in rows.values())
    return {"n_u": n_u, "n_l": n_l, "by_definition": rows, "binding": binding}


def verify_sigma(path: Path = PAIRS_PATH) -> str | None:
    """Özet ve köken içi havuzlanmış σ_D (n − 2) ön-kayıtla uyuşmalı; değilse hata metni."""
    if not path.exists():
        return f"{path} yok"
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if digest != PAIRS_SHA256:
        return f"özet ayrıştı: {digest}"
    pairs = pd.read_csv(path)
    pairs = pairs.loc[pairs["period"] == "A"]
    d = pairs["po3_r_net"] - pairs["cont_r_net"]
    within = sum(float(((g - g.mean()) ** 2).sum()) for _, g in d.groupby(pairs["origin"]))
    sigma = math.sqrt(within / (len(d) - 2))
    if round(sigma, 3) != SIGMA_D:
        return f"σ_D ayrıştı: {sigma:.4f} ≠ {SIGMA_D}"
    return None


@dataclass(frozen=True, kw_only=True)
class GateResult:
    setups: int
    days: int
    power: Mapping[str, Any]
    checks: Mapping[str, bool]

    @property
    def passed(self) -> bool:
        return bool(self.checks) and all(self.checks.values())

    @property
    def sample_passed(self) -> bool:
        return self.setups >= MIN_SETUPS and self.days >= MIN_DAYS


def evaluate_gate(primary: Sequence[Setup], *, costs: StopCosts) -> GateResult:
    days = len({s.day for s in primary})
    worst = max((s.cost_per_r(costs) for s in primary), default=None)
    stats = power(primary)
    return GateResult(
        setups=len(primary), days=days, power=stats,
        checks={
            f"sağlama: azami stop kaymalı maliyet/R <= {MAX_COST_PER_R}": (
                worst is not None and worst <= MAX_COST_PER_R + 1e-12
            ),
            f"birincil kurulum >= {MIN_SETUPS}": len(primary) >= MIN_SETUPS,
            f"farklı takvim günü >= {MIN_DAYS}": days >= MIN_DAYS,
            f"güç: bağlayıcı MDE_proj(½·ΔR) <= {MAX_MDE_HALF_DELTA}": stats["binding"] <= MAX_MDE_HALF_DELTA,
        },
    )


# --------------------------------------------------------------------------- #
# Rapor
# --------------------------------------------------------------------------- #
def _num(value: float | None, *, pct: bool = False, digits: int = 2) -> str:
    if value is None or (isinstance(value, float) and not math.isfinite(value)):
        return "—" if value is None or math.isnan(value) else "∞"
    return f"{100.0 * value:.{digits}f}%" if pct else f"{value:.{digits}f}"


def _stamp(value: pd.Timestamp | None) -> str:
    return "—" if value is None else value.strftime("%Y-%m-%d %H:%M")


def _share(part: int, whole: int) -> str:
    return f"{100.0 * part / whole:.1f}%" if whole else "—"


def format_coverage(scans: Sequence[SymbolScan]) -> str:
    header = f"{'sembol':<18}{'bar':>8}{'ilk bar':>20}{'son bar':>20}{'4H mum':>8}{'eksik':>7}{'düz':>5}  not"
    lines = [header, "-" * len(header)]
    for scan in scans:
        lines.append(
            f"{scan.symbol:<18}{scan.bars:>8}{_stamp(scan.first_bar):>20}{_stamp(scan.last_bar):>20}"
            f"{len(scan.candles):>8}{scan.count('incomplete'):>7}{scan.count('flat'):>5}  {scan.skipped or ''}"
        )
    return "\n".join(lines)


_COUNT_COLUMNS = (("none", "yok"), ("ambiguous", "belirsiz"), ("continuation", "devam"),
                  ("no_entry_bar", "giriş-yok"), ("off_side", "konum-dışı"), ("invalid", "geom-yok"),
                  ("narrow", "maliyet"), ("primary", "BİRİNCİL"))


def _count_row(label: str, scans: Sequence[SymbolScan]) -> str:
    counted = sum(len(s.candles) - s.count("incomplete") - s.count("flat") for s in scans)
    primary = [p for s in scans for p in s.primary]
    return (f"{label:<18}{counted:>8}" + "".join(f"{sum(s.count(k) for s in scans):>{len(n) + 2}}"
                                                 for k, n in _COUNT_COLUMNS)
            + f"{sum(1 for p in primary if p.direction == 'short'):>7}"
            + f"{sum(1 for p in primary if p.direction == 'long'):>6}"
            + f"{len({p.day for p in primary}):>8}")


def format_counts(scans: Sequence[SymbolScan]) -> str:
    header = (f"{'sembol':<18}{'s-mum':>8}" + "".join(f"{n:>{len(n) + 2}}" for _, n in _COUNT_COLUMNS)
              + f"{'short':>7}{'long':>6}{'t.gün':>8}")
    lines = [header, "-" * len(header)]
    live = [scan for scan in scans if scan.skipped is None]
    lines += [_count_row(scan.symbol, [scan]) for scan in live]
    lines += ["-" * len(header), _count_row("TOPLAM", live)]
    return "\n".join(lines)


def format_distribution(primary: Sequence[Setup]) -> str:
    lines = ["yön: " + ", ".join(
        f"{d} {sum(1 for s in primary if s.direction == d)}" for d in ("short", "long"))
        + f"  (köken U {sum(1 for s in primary if s.origin == 'U')}, L {sum(1 for s in primary if s.origin == 'L')})"]
    years = Counter(s.day.year for s in primary)
    lines.append("yıl: " + ", ".join(f"{y} {years[y]} ({len({s.day for s in primary if s.day.year == y})} gün)"
                                     for y in sorted(years)))
    slots = Counter(s.slot for s in primary)
    lines.append("4H dilimi (UTC): " + ", ".join(f"{h:02d} {slots.get(h, 0)}" for h in range(0, 24, 4)))
    for name, key in CLUSTER_KEYS.items():
        per = Counter(key(s) for s in primary)
        hist = Counter(per.values())
        label = "gün" if name == "day" else "ISO hafta"
        lines.append(f"{label} başına kurulum: {len(per)} küme, ort. "
                     f"{_num(len(primary) / len(per) if per else None)}; histogram "
                     + ", ".join(f"{k}:{hist[k]}" for k in sorted(hist)))
    return "\n".join(lines)


def _dist_line(label: str, stats: Dist, *, pct: bool) -> str:
    digits = 3 if pct else 2
    return (f"{label:<40}{stats.n:>7}{_num(stats.p25, pct=pct, digits=digits):>10}"
            f"{_num(stats.median, pct=pct, digits=digits):>10}{_num(stats.p75, pct=pct, digits=digits):>10}")


def format_geometry(primary: Sequence[Setup], *, costs: StopCosts) -> str:
    header = f"{'ölçü':<40}{'n':>7}{'p25':>10}{'medyan':>10}{'p75':>10}"
    lines = [header, "-" * len(header)]
    for direction in ("short", "long", None):
        chosen = [s for s in primary if direction is None or s.direction == direction]
        tag = direction or "toplam"
        lines.append(_dist_line(f"[{tag}] stop mesafesi", dist([s.stop_distance for s in chosen]), pct=True))
        lines.append(_dist_line(f"[{tag}] maliyet/R (stop kaymalı)", dist([s.cost_per_r(costs) for s in chosen]),
                                pct=False))
        lines.append(_dist_line(f"[{tag}] geçici hedef mesafesi", dist([s.target_distance for s in chosen]),
                                pct=True))
        lines.append(_dist_line(f"[{tag}] planlanan R/R", dist([s.reward_risk for s in chosen]), pct=False))
        below = sum(1 for s in chosen if s.reward_risk < 1.0)
        lines.append(f"  [{tag}] R/R < 1 payı: {below} / {len(chosen)} ({_share(below, len(chosen))})")
    return "\n".join(lines)


def _tally(primary: Sequence[Setup], label: str, values: Sequence[str],
           key: Callable[[Setup], Any]) -> str:
    rows = []
    for direction in ("short", "long"):
        chosen = [s for s in primary if s.direction == direction]
        rows.append(f"  {direction:<6}" + ", ".join(f"{v} {sum(1 for s in chosen if key(s) == v)}" for v in values))
    return f"{label}\n" + "\n".join(rows)


def format_labels(primary: Sequence[Setup], *, costs: StopCosts) -> str:
    delays = Counter(s.delay for s in primary)
    left = Counter(s.bars_left for s in primary)
    fvg = [s for s in primary if s.fvg == "var"]
    parts = [
        _tally(primary, "(a) 4H yapı:", STRUCTURES, lambda s: s.structure),
        "    hiza: " + ", ".join(f"{a} {sum(1 for s in primary if s.alignment == a)}" for a in ALIGNMENTS),
        _tally(primary, "(b) süpürülen seviye:", SWEPT_LEVELS, lambda s: s.swept_level),
        "(c) geri dönüş gecikmesi (bar): " + ", ".join(f"{k}:{delays[k]}" for k in sorted(delays)),
        _tally(primary, "(d) r..r+2 FVG:", FVG_STATES, lambda s: s.fvg),
        _tally(fvg, "(e) FVG limit dolumu (FVG'li kurulumlar):", FILL_STATES, lambda s: s.fvg_fill),
        _dist_line("(f) maliyet/R — piyasa girişi", dist([s.cost_per_r(costs) for s in primary]), pct=False),
        _dist_line("(f) maliyet/R — FVG limit (dolanlar)", dist([s.fvg_cost_per_r(costs) for s in fvg]), pct=False),
        _tally(primary, "(g) karşı likidite ≥ 2R:", LIQUIDITY_STATES, lambda s: s.liquidity),
        "(h) girişte kalan bar: " + ", ".join(f"{k}:{left[k]}" for k in sorted(left)),
    ]
    return "\n".join(parts)


def format_gate(gate: GateResult) -> str:
    lines = [f"  [{'GEÇTİ' if ok else 'KALDI'}] {name}" for name, ok in gate.checks.items()]
    stats = gate.power
    lines.append(f"  → birincil {gate.setups} (U {stats['n_u']}, L {stats['n_l']}), takvim günü {gate.days}")
    for definition, row in stats["by_definition"].items():
        lines.append(f"  → {definition:<5} küme {row['clusters']}, m̄ {_num(row['mean_cluster_size'], digits=3)},"
                     f" DEFF_proj {_num(row['deff_proj'], digits=3)}, MDE_proj(½·ΔR) {_num(row['mde_half_delta'], digits=3)}")
    lines.append(f"  → bağlayıcı MDE_proj(½·ΔR) {_num(stats['binding'], digits=3)} (eşik {MAX_MDE_HALF_DELTA})")
    lines.append("  KARAR: " + (
        "GEÇTİ — sonuç ölçümünün ön-kaydına geçilebilir" if gate.passed
        else "KALDI — ÖLÇÜLEMEZ; eşik ve kutu DEĞİŞMEZ, PO3 ailesi PARK EDİLİR (§6v > 3)"))
    return "\n".join(lines)


def _dist_payload(stats: Dist) -> dict[str, Any]:
    return {"n": stats.n, "p25": stats.p25, "median": stats.median, "p75": stats.p75}


def _finite(value: float) -> float | None:
    return value if math.isfinite(value) else None


def payload(scans: Sequence[SymbolScan], *, start: pd.Timestamp, end: pd.Timestamp, threshold: float,
            costs: StopCosts, gate: GateResult) -> dict[str, Any]:
    """Makine okunur sayım. Getiri/R/PnL alanı YOKTUR (test)."""
    primary = [s for scan in scans for s in scan.primary]
    by_dir = {d: [s for s in primary if s.direction == d] for d in ("short", "long")}

    def geometry(chosen: Sequence[Setup]) -> dict[str, Any]:
        return {
            "stop_distance": _dist_payload(dist([s.stop_distance for s in chosen])),
            "cost_per_r_with_stop_slippage": _dist_payload(dist([s.cost_per_r(costs) for s in chosen])),
            "target_distance": _dist_payload(dist([s.target_distance for s in chosen])),
            "planned_reward_risk": _dist_payload(dist([s.reward_risk for s in chosen])),
            "reward_risk_below_1": sum(1 for s in chosen if s.reward_risk < 1.0),
        }

    def tally(values: Sequence[str], key: Callable[[Setup], Any], chosen: Sequence[Setup]) -> dict[str, Any]:
        return {d: {v: sum(1 for s in chosen if s.direction == d and key(s) == v) for v in values}
                for d in ("short", "long")}

    fvg = [s for s in primary if s.fvg == "var"]
    power_rows = {
        name: {k: _finite(v) if isinstance(v, float) else v for k, v in row.items()}
        for name, row in gate.power["by_definition"].items()
    }
    return {
        "preregistration": "docs/backtest.md > 6v",
        "scope": {
            "layer": LAYER, "timeframe": TIMEFRAME, "period": "A",
            "start": start.isoformat(), "end": end.isoformat(),
            "box": "b1-b4", "window": "b5-b16", "buffer_fraction": BUFFER_FRACTION,
            "threshold": threshold, "stop_round_trip_cost": {"short": costs.short, "long": costs.long},
        },
        "symbols": [
            {"symbol": scan.symbol, "bars": scan.bars, "skipped": scan.skipped,
             "first_bar": None if scan.first_bar is None else scan.first_bar.isoformat(),
             "candles": {kind: scan.count(kind) for kind in CANDLE_KINDS},
             "primary_short": sum(1 for s in scan.primary if s.direction == "short"),
             "primary_long": sum(1 for s in scan.primary if s.direction == "long")}
            for scan in scans
        ],
        "candles": {kind: sum(scan.count(kind) for scan in scans) for kind in CANDLE_KINDS},
        "primary_by_direction": {d: len(v) for d, v in by_dir.items()},
        "primary_by_year": {str(k): v for k, v in sorted(Counter(s.day.year for s in primary).items())},
        "primary_by_slot": {f"{h:02d}": sum(1 for s in primary if s.slot == h) for h in range(0, 24, 4)},
        "calendar_days": len({s.day for s in primary}),
        "setups_per_day": {str(k): v for k, v in sorted(Counter(Counter(s.day for s in primary).values()).items())},
        "setups_per_week": {str(k): v for k, v in sorted(Counter(Counter(s.week for s in primary).values()).items())},
        "geometry": {"all": geometry(primary), **{d: geometry(v) for d, v in by_dir.items()}},
        "labels": {
            "structure": tally(STRUCTURES, lambda s: s.structure, primary),
            "alignment": {a: sum(1 for s in primary if s.alignment == a) for a in ALIGNMENTS},
            "swept_level": tally(SWEPT_LEVELS, lambda s: s.swept_level, primary),
            "reversal_delay": {str(k): v for k, v in sorted(Counter(s.delay for s in primary).items())},
            "fvg": tally(FVG_STATES, lambda s: s.fvg, primary),
            "fvg_fill": tally(FILL_STATES, lambda s: s.fvg_fill, fvg),
            "cost_per_r_market": _dist_payload(dist([s.cost_per_r(costs) for s in primary])),
            "cost_per_r_fvg_limit": _dist_payload(dist([s.fvg_cost_per_r(costs) for s in fvg])),
            "opposite_liquidity_2r": tally(LIQUIDITY_STATES, lambda s: s.liquidity, primary),
            "bars_left": {str(k): v for k, v in sorted(Counter(s.bars_left for s in primary).items())},
        },
        "gate": {
            "checks": dict(gate.checks), "passed": gate.passed,
            "setups": gate.setups, "days": gate.days,
            "n_u": gate.power["n_u"], "n_l": gate.power["n_l"],
            "power": power_rows, "mde_binding": _finite(gate.power["binding"]),
            "sigma_d": SIGMA_D, "pairs_sha256": PAIRS_SHA256,
            "verdict": "geçti" if gate.passed else "ölçülemez",
        },
    }


# --------------------------------------------------------------------------- #
# Giriş noktası
# --------------------------------------------------------------------------- #
def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

    start = pd.Timestamp(args.start)
    end = pd.Timestamp(args.end)
    if start >= end:
        logger.error("ters pencere: %s >= %s", start, end)
        return 2
    if end > pd.Timestamp(PERIOD_A_CUTOFF):
        logger.error("--end dönem A kesimini aşıyor: %s > %s", end, PERIOD_A_CUTOFF)
        return 2
    assert_before_vault(end, what="--end")

    config = load_config(args.config)
    layer = resolve_layer(config, LAYER)
    universe = layer.symbols or []
    symbols = list(args.symbols) if args.symbols else list(universe)
    outside = [symbol for symbol in symbols if symbol not in universe]
    if not universe or outside:
        logger.error("kapsam dışı sembol: %s", ", ".join(outside) or "(evren yok)")
        return 2

    costs = stop_costs(layer.config)
    threshold = derive_threshold(costs)
    if not threshold == PREREGISTERED_THRESHOLD:
        logger.error("eşik ön-kayıttan ayrıştı: config'ten %s ≠ %s (short gidiş-dönüş %s)",
                     threshold, PREREGISTERED_THRESHOLD, costs.short)
        return 2
    problem = verify_sigma()
    if problem is not None:
        logger.error("güç kapısının girdisi doğrulanamadı: %s", problem)
        return 2

    cache_dir = args.cache_dir or tempfile.mkdtemp(prefix="po3h4-")
    run_config = copy.deepcopy(dict(layer.config))
    run_config["timeframe"] = TIMEFRAME
    run_config["data"] = {
        **run_config["data"],
        "history_bars": int(math.ceil((end - start + WARMUP) / BAR)) + DAY_BARS,
        "cache_dir": str(cache_dir),
    }
    logger.info("kapsam: %s %s, dönem A %s → %s, %d sembol, eşik %.2f%%, önbellek %s",
                LAYER, TIMEFRAME, start, end, len(symbols), 100 * threshold, cache_dir)

    frames = load_frames(run_config, symbols, start=start, end=end)
    scans = [
        SymbolScan(symbol=symbol, skipped=f"veri çekilemedi: {frames[symbol]}")
        if isinstance(frames[symbol], Exception)
        else scan_symbol(symbol, frames[symbol], start=start, end=end, threshold=threshold)
        for symbol in symbols
    ]
    counted = sum(len(s.candles) - s.count("incomplete") - s.count("flat") for s in scans)
    if counted == 0:
        logger.error("hiçbir 4H mum sayılamadı — sayım YAPILMADI (veri kapısı)")
        print(format_coverage(scans))
        return 3

    primary = [s for scan in scans for s in scan.primary]
    gate = evaluate_gate(primary, costs=costs)

    print()
    print(f"*** 4H DÖNGÜ PO3 (§6v) — iki yönde stop ≥ {100 * threshold:.2f}% ***")
    print()
    print("=== 0. KAPSAM ===")
    print(format_coverage(scans))
    print()
    print("=== 1. 4H MUM SINIFLARI (s-mum = tam veri, düz olmayan) ===")
    print(format_counts(scans))
    print()
    print("=== 2. DAĞILIM — BİRİNCİL ===")
    print(format_distribution(primary))
    print()
    print("=== 3. GEOMETRİ (giriş anında bilinen; getiri DEĞİL) — BİRİNCİL ===")
    print(format_geometry(primary, costs=costs))
    print()
    print("=== 4. BETİMSEL ETİKETLER (kapı DEĞİL) — BİRİNCİL ===")
    print(format_labels(primary, costs=costs))
    print()
    print("=== KAPILAR (ön-kayıtlı, §6v > 3) ===")
    print(format_gate(gate))

    if args.results_json:
        path = Path(args.results_json)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload(scans, start=start, end=end, threshold=threshold,
                                           costs=costs, gate=gate), indent=2, ensure_ascii=False),
                        encoding="utf-8")
        logger.info("yük yazıldı: %s", path)
    return 0


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="4H döngü PO3 ölçülebilirlik sayımı, §6v (SALT OKUNUR; getiri/R hesaplanmaz)."
    )
    parser.add_argument("--config", default=None)
    parser.add_argument("--symbols", nargs="*", default=None, help=f"varsayılan: {LAYER} evreninin tamamı")
    parser.add_argument("--start", default=PERIOD_A_START, help="dönem A başlangıcı (ithal)")
    parser.add_argument("--end", default=PERIOD_A_CUTOFF, help="dönem A kesimi — AŞILAMAZ (çıkış 2)")
    parser.add_argument("--cache-dir", default=None)
    parser.add_argument("--results-json", default=None)
    return parser.parse_args(argv)


if __name__ == "__main__":
    raise SystemExit(main())

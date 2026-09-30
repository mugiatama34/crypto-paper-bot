#!/usr/bin/env python3
"""Aday "PO3 / AMD" kurulumunun (Asya aralığı süpürme + geri dönüş) ÖLÇÜLEBİLİRLİK SAYIMI.
SALT OKUNUR.

Cevapladığı soru tek: *bu kurulum dönem A'da ölçülebilir SAYIDA ve maliyete yenilmeyecek
bir GEOMETRİYLE oluşuyor mu?*

**Bu bir strateji koşusu DEĞİLDİR.** Getiri, R, PnL, kazanma oranı burada ne hesaplanır ne
raporlanır; araç `core/metrics.py`, `core/portfolio.py`, `core/ledger.py`, `core/engine.py`
ve `strategies/*` modüllerini import ETMEZ (test: `tests/test_measure_po3.py`). Gerekçe
`scripts/measure_death_cross.py`nin aynısıdır (docs/backtest.md > 7): bir kurulumun kaç kez
oluştuğunu ve GİRİŞ ANINDA bilinen geometrisini görmek eşik seçimini kirletmez, ne
kazandırdığını görmek kirletirdi. Geometri yalnızca giriş anında bilinen fiyatlardan kurulur
(Asya aralığı, süpürme ucu, giriş barının AÇILIŞI); giriş barının açılışından sonraki hiçbir
fiyat okunmaz.

Deftere, `config.yaml`a, `strategies/`e ve `data/cache/`e YAZMAZ: mum önbelleği koşuya özel
bir dizine düşer (`measure_death_cross.py`nin aynı gerekçesi).

## Kapsam bir KAPIDIR: yalnızca dönem A, `ema` katmanının 13 sembolü, 15m

Sınırlar `scripts/backtest_ema.py`den İTHAL EDİLİR. `--end` dönem A kesimini aşarsa çıkış 2;
mumlar kesimden ileriye hiç çekilmez (`now = --end`). Dönem B ve kasa (`scripts/vault.py`)
bu betiğin erişiminde değildir.

## Tanımlar (kullanıcının ön-kaydı, UTC; bu betik onları DEĞİŞTİRMEZ)

- **Birikim**: açılışı [00:00, 08:00) olan 32 bar; `high_A = max(high)`, `low_A = min(low)`.
- **Manipülasyon penceresi**: açılışı [08:00, 13:00) olan 20 bar.
- **Süpürme**: pencerede `high > high_A` (üst) ya da `low < low_A` (alt) olan ilk bar.
- **Geri dönüş**: süpürme barından SONRAKİ (süpürme barının kendisi DEĞİL — "süpürmeden
  sonra" kelimesi kelimesine), yine pencerede, kapanışı `[low_A, high_A]` içinde olan ilk bar.
  Süpürme barının kendisinin içeride kapanması ayrıca SAYILIR ama kurulum üretmez; okuma
  tercihi böylece raporda görünür.
- **Kurulum**: süpürme + geri dönüş. Yön süpürmenin TERSİ (üst → short, alt → long).
- **Giriş**: geri dönüş barından sonraki barın açılışı (kural 13). 12:45'teki bir geri
  dönüşün girişi 13:00 barıdır; o bar yoksa kurulum "giriş barı yok" sayılır.
- **Stop**: süpürmenin uç noktası = süpürme barından geri dönüş barına kadarki uç
  (üstte `max(high)`, altta `min(low)`).
- **Hedef**: aralığın karşı tarafı (short → `low_A`, long → `high_A`).
- **Belirsiz gün**: pencerede İKİ taraf da süpürüldü → ayrı sayılır, birincil sayıma girmez.

**BİRİNCİL kurulum** = belirsiz olmayan bir günün kurulumu VE geometrisi kurulabilir
(stop girişin doğru tarafında, hedef henüz geçilmemiş). Kurulamayan geometri ayrıca
sayılır; birincile girmemesi yalnızca sayıyı DÜŞÜRÜR (muhafazakâr yön).

Eksik veri günü (Asya 32 ya da pencere 20 bar değil) ve düz aralık (`high_A <= low_A`)
sayılmaz, ayrıca sayılır.

## Geometri (giriş anında bilinen — getiri DEĞİL)

- Asya genişliği / fiyat = `(high_A − low_A) / close(07:45 barı)`
- stop mesafesi = `|giriş − süpürme ucu| / giriş`
- planlanan R/R = `|hedef − giriş| / |giriş − süpürme ucu|`
- maliyet/R = gidiş-dönüş maliyeti / stop mesafesi; gidiş-dönüş = `2 × (fee_rate +
  slippage_base)` config'ten okunur (bugün %0.21). Short stop'un ek kayması
  (`slippage_short_stop`) DÂHİL DEĞİLDİR — bir çıkışın hangi yoldan olacağını varsaymak
  gerekirdi; kapı bu yüzden maliyeti hafife alan yönde değil, ön-kayıttaki %0.21 ile koşar.

Yüzdelikler `numpy.percentile`ın doğrusal aradeğerlemesidir.

## KARAR KAPISI (sayı görülmeden yazıldı; sabitler aşağıda, girdi DEĞİL)

Birincil kurulum ≥ 300 **VE** ≥ 150 farklı takvim günü **VE** medyan maliyet/R ≤ 0.15.
Üçü de sağlanmazsa model kurulmaz. Kapı MEKANİK uygulanır (`evaluate_gate`) ve sonucu
log'a basılır.

## Çıkış kodları (karar 51: boş rapor yeşil dönmez)

- `0` — sayım yapıldı (kapının sonucu ne olursa olsun).
- `2` — kullanım hatası (kapsam dışı sembol, ters pencere, kesimi aşan `--end`).
- `3` — VERİ KAPISI: hiçbir sembol-gün sayılamadı.

Kullanım (depo kökünden):
    python scripts/measure_po3.py
    python scripts/measure_po3.py --symbols BTC-USDT-SWAP --results-json out/po3.json
"""

from __future__ import annotations

import argparse
import copy
import json
import logging
import math
import sys
import tempfile
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from core.config import get_setting, load_config  # noqa: E402
from core.data import fetch_ohlcv  # noqa: E402
from core.layers import resolve_layer  # noqa: E402
from scripts.backtest_ema import PERIOD_A_CUTOFF, PERIOD_A_START  # noqa: E402

logger = logging.getLogger("measure-po3")

LAYER = "ema"
TIMEFRAME = "15m"
BAR = pd.Timedelta(minutes=15)

ASIA_START = pd.Timedelta(hours=0)
ASIA_END = pd.Timedelta(hours=8)
WINDOW_START = pd.Timedelta(hours=8)
WINDOW_END = pd.Timedelta(hours=13)
ASIA_BARS = int((ASIA_END - ASIA_START) / BAR)  # 32
WINDOW_BARS = int((WINDOW_END - WINDOW_START) / BAR)  # 20

# KARAR KAPISI — ön-kayıtlı, sayı görülmeden yazıldı. Girdi DEĞİLDİR.
GATE_MIN_SETUPS = 300
GATE_MIN_DAYS = 150
GATE_MAX_MEDIAN_COST_PER_R = 0.15

# Ön-kayıttaki gidiş-dönüş maliyeti; config'ten türeyenle eşleşmezse betik durur.
PREREGISTERED_ROUND_TRIP = 0.0021


# --------------------------------------------------------------------------- #
# Birimler
# --------------------------------------------------------------------------- #
@dataclass(frozen=True, kw_only=True)
class Setup:
    """Bir kurulum. Getiri/R taşımaz — yalnızca kimlik ve giriş anındaki GEOMETRİ."""

    symbol: str
    day: pd.Timestamp
    side: str  # süpürülen taraf: "up" | "down"
    sweep_bar: pd.Timestamp
    reversal_bar: pd.Timestamp
    entry_bar: pd.Timestamp
    entry: float
    extreme: float
    target: float
    high_a: float
    low_a: float
    asia_close: float

    @property
    def direction(self) -> str:
        return "short" if self.side == "up" else "long"

    @property
    def stop_distance(self) -> float:
        """Göreli stop mesafesi; ≤ 0 ise giriş stop'un ötesinde (geometri kurulamaz)."""
        if self.side == "up":
            return (self.extreme - self.entry) / self.entry
        return (self.entry - self.extreme) / self.entry

    @property
    def target_distance(self) -> float:
        """Göreli hedef mesafesi; ≤ 0 ise hedef girişte zaten geçilmiş."""
        if self.side == "up":
            return (self.entry - self.target) / self.entry
        return (self.target - self.entry) / self.entry

    @property
    def valid(self) -> bool:
        return self.stop_distance > 0.0 and self.target_distance > 0.0

    @property
    def reward_risk(self) -> float:
        return self.target_distance / self.stop_distance

    def cost_per_r(self, round_trip: float) -> float:
        return round_trip / self.stop_distance

    @property
    def asia_width(self) -> float:
        return (self.high_a - self.low_a) / self.asia_close


@dataclass(frozen=True, kw_only=True)
class DayResult:
    """Tek sembol-günün sınıfı. Her gün tam BİR sınıfa düşer."""

    symbol: str
    day: pd.Timestamp
    kind: str  # incomplete | flat | none | ambiguous | continuation | setup | no_entry_bar
    side: str | None = None
    asia_width: float | None = None
    sweep_bar_closed_inside: bool = False
    sweep_on_last_bar: bool = False
    setup: Setup | None = None


DAY_KINDS = ("incomplete", "flat", "none", "ambiguous", "continuation", "no_entry_bar", "setup")


@dataclass(frozen=True, kw_only=True)
class SymbolScan:
    symbol: str
    bars: int = 0
    first_bar: pd.Timestamp | None = None
    last_bar: pd.Timestamp | None = None
    days: tuple[DayResult, ...] = ()
    skipped: str | None = None

    def count(self, kind: str) -> int:
        return sum(1 for day in self.days if day.kind == kind)

    @property
    def counted_days(self) -> list[DayResult]:
        return [day for day in self.days if day.kind not in ("incomplete", "flat")]

    @property
    def setups(self) -> list[Setup]:
        return [day.setup for day in self.days if day.setup is not None]

    @property
    def primary(self) -> list[Setup]:
        return [setup for setup in self.setups if setup.valid]


# --------------------------------------------------------------------------- #
# Gün sınıflandırması
# --------------------------------------------------------------------------- #
def classify_day(symbol: str, day: pd.Timestamp, frame: pd.DataFrame) -> DayResult:
    """`frame` sembolün TÜM barlarıdır; gün `day` (00:00 UTC) için dilimlenir."""
    asia = frame.loc[(frame.index >= day + ASIA_START) & (frame.index < day + ASIA_END)]
    window = frame.loc[(frame.index >= day + WINDOW_START) & (frame.index < day + WINDOW_END)]
    if len(asia) != ASIA_BARS or len(window) != WINDOW_BARS:
        return DayResult(symbol=symbol, day=day, kind="incomplete")

    high_a = float(asia["high"].max())
    low_a = float(asia["low"].min())
    asia_close = float(asia["close"].iloc[-1])
    if not high_a > low_a or asia_close <= 0.0:
        return DayResult(symbol=symbol, day=day, kind="flat")
    width = (high_a - low_a) / asia_close

    up = (window["high"] > high_a).to_numpy()
    down = (window["low"] < low_a).to_numpy()
    if up.any() and down.any():
        return DayResult(symbol=symbol, day=day, kind="ambiguous", asia_width=width)
    if not up.any() and not down.any():
        return DayResult(symbol=symbol, day=day, kind="none", asia_width=width)

    side = "up" if up.any() else "down"
    hits = up if side == "up" else down
    sweep = int(np.argmax(hits))
    closes = window["close"].to_numpy(dtype="float64")
    inside = (closes >= low_a) & (closes <= high_a)
    sweep_inside = bool(inside[sweep])
    on_last = sweep == WINDOW_BARS - 1

    later = np.flatnonzero(inside[sweep + 1:])
    if later.size == 0:
        return DayResult(
            symbol=symbol, day=day, kind="continuation", side=side, asia_width=width,
            sweep_bar_closed_inside=sweep_inside, sweep_on_last_bar=on_last,
        )
    reversal = sweep + 1 + int(later[0])
    reversal_bar = window.index[reversal]
    entry_bar = reversal_bar + BAR
    if entry_bar not in frame.index:
        return DayResult(
            symbol=symbol, day=day, kind="no_entry_bar", side=side, asia_width=width,
            sweep_bar_closed_inside=sweep_inside,
        )

    span = window.iloc[sweep: reversal + 1]
    extreme = float(span["high"].max()) if side == "up" else float(span["low"].min())
    setup = Setup(
        symbol=symbol,
        day=day,
        side=side,
        sweep_bar=window.index[sweep],
        reversal_bar=reversal_bar,
        entry_bar=entry_bar,
        entry=float(frame.at[entry_bar, "open"]),
        extreme=extreme,
        target=low_a if side == "up" else high_a,
        high_a=high_a,
        low_a=low_a,
        asia_close=asia_close,
    )
    return DayResult(
        symbol=symbol, day=day, kind="setup", side=side, asia_width=width,
        sweep_bar_closed_inside=sweep_inside, setup=setup,
    )


def period_days(start: pd.Timestamp, end: pd.Timestamp) -> list[pd.Timestamp]:
    """Penceresi (ve olası 13:00 giriş barının AÇILIŞI) `end`i aşmayan günler."""
    first = start.normalize()
    if first < start:
        first += pd.Timedelta(days=1)
    days: list[pd.Timestamp] = []
    day = first
    while day + WINDOW_END + BAR <= end:
        days.append(day)
        day += pd.Timedelta(days=1)
    return days


def scan_symbol(symbol: str, frame: pd.DataFrame, *, start: pd.Timestamp, end: pd.Timestamp) -> SymbolScan:
    if frame.empty:
        return SymbolScan(symbol=symbol, skipped="hiç bar yok")
    frame = frame.loc[(frame.index >= start) & (frame.index < end)]
    if frame.empty:
        return SymbolScan(symbol=symbol, skipped="dönem A'da bar yok")
    listed = frame.index[0].normalize()
    days = tuple(
        classify_day(symbol, day, frame) for day in period_days(start, end) if day >= listed
    )
    return SymbolScan(
        symbol=symbol, bars=len(frame), first_bar=frame.index[0], last_bar=frame.index[-1], days=days,
    )


# --------------------------------------------------------------------------- #
# Dağılım ve kapı
# --------------------------------------------------------------------------- #
@dataclass(frozen=True, kw_only=True)
class Dist:
    n: int
    p25: float | None
    median: float | None
    p75: float | None


def dist(values: Sequence[float]) -> Dist:
    data = np.asarray([value for value in values if value is not None and math.isfinite(value)], dtype="float64")
    if data.size == 0:
        return Dist(n=0, p25=None, median=None, p75=None)
    return Dist(
        n=int(data.size),
        p25=float(np.percentile(data, 25)),
        median=float(np.percentile(data, 50)),
        p75=float(np.percentile(data, 75)),
    )


@dataclass(frozen=True, kw_only=True)
class GateResult:
    setups: int
    days: int
    median_cost_per_r: float | None
    checks: Mapping[str, bool] = field(default_factory=dict)

    @property
    def passed(self) -> bool:
        return bool(self.checks) and all(self.checks.values())


def evaluate_gate(primary: Sequence[Setup], *, round_trip: float) -> GateResult:
    days = len({setup.day for setup in primary})
    cost = dist([setup.cost_per_r(round_trip) for setup in primary]).median
    return GateResult(
        setups=len(primary),
        days=days,
        median_cost_per_r=cost,
        checks={
            f"birincil kurulum >= {GATE_MIN_SETUPS}": len(primary) >= GATE_MIN_SETUPS,
            f"farklı takvim günü >= {GATE_MIN_DAYS}": days >= GATE_MIN_DAYS,
            f"medyan maliyet/R <= {GATE_MAX_MEDIAN_COST_PER_R}": (
                cost is not None and cost <= GATE_MAX_MEDIAN_COST_PER_R
            ),
        },
    )


def round_trip_cost(config: Mapping[str, Any]) -> float:
    return 2.0 * (float(get_setting(config, "fee_rate")) + float(get_setting(config, "slippage_base")))


# --------------------------------------------------------------------------- #
# Rapor
# --------------------------------------------------------------------------- #
def _num(value: float | None, *, pct: bool = False, digits: int = 2) -> str:
    if value is None:
        return "—"
    return f"{100.0 * value:.{digits}f}%" if pct else f"{value:.{digits}f}"


def _stamp(value: pd.Timestamp | None) -> str:
    return "—" if value is None else value.strftime("%Y-%m-%d %H:%M")


def format_coverage(scans: Sequence[SymbolScan]) -> str:
    header = f"{'sembol':<18}{'bar':>8}{'ilk bar':>20}{'son bar':>20}{'gün':>6}{'eksik':>7}{'düz':>5}  not"
    lines = [header, "-" * len(header)]
    for scan in scans:
        lines.append(
            f"{scan.symbol:<18}{scan.bars:>8}{_stamp(scan.first_bar):>20}{_stamp(scan.last_bar):>20}"
            f"{len(scan.days):>6}{scan.count('incomplete'):>7}{scan.count('flat'):>5}  {scan.skipped or ''}"
        )
    return "\n".join(lines)


def _day_row(label: str, scans: Sequence[SymbolScan]) -> str:
    days = [day for scan in scans for day in scan.counted_days]
    up = sum(1 for day in days if day.side == "up" and day.kind != "ambiguous")
    down = sum(1 for day in days if day.side == "down" and day.kind != "ambiguous")
    setups = [setup for scan in scans for setup in scan.setups]
    primary = [setup for setup in setups if setup.valid]
    return (
        f"{label:<18}{len(days):>7}{sum(1 for d in days if d.kind == 'none'):>7}{up:>6}{down:>6}"
        f"{sum(1 for d in days if d.kind == 'ambiguous'):>8}"
        f"{sum(1 for d in days if d.kind == 'continuation'):>9}"
        f"{len(setups):>9}{len(primary):>10}"
        f"{sum(1 for s in primary if s.direction == 'short'):>7}"
        f"{sum(1 for s in primary if s.direction == 'long'):>6}"
        f"{len({s.day for s in primary}):>8}"
    )


def format_counts(scans: Sequence[SymbolScan]) -> str:
    header = (
        f"{'sembol':<18}{'s-gün':>7}{'yok':>7}{'üst':>6}{'alt':>6}{'belirsiz':>8}{'devam':>9}"
        f"{'kurulum':>9}{'BİRİNCİL':>10}{'short':>7}{'long':>6}{'t.gün':>8}"
    )
    lines = [header, "-" * len(header)]
    for scan in scans:
        if scan.skipped is None:
            lines.append(_day_row(scan.symbol, [scan]))
    lines.append("-" * len(header))
    lines.append(_day_row("TOPLAM", [scan for scan in scans if scan.skipped is None]))
    return "\n".join(lines)


def format_diagnostics(scans: Sequence[SymbolScan]) -> str:
    days = [day for scan in scans for day in scan.days]
    setups = [setup for scan in scans for setup in scan.setups]
    one_sided = [day for day in days if day.kind in ("continuation", "setup", "no_entry_bar")]
    continuation = [day for day in days if day.kind == "continuation"]
    return "\n".join([
        f"tek taraflı süpürme günü: {len(one_sided)}"
        f"  (üst {sum(1 for d in one_sided if d.side == 'up')}, alt {sum(1 for d in one_sided if d.side == 'down')})",
        f"  → kurulum (geri dönüş var): {sum(1 for d in one_sided if d.kind == 'setup')}",
        f"  → KARŞI SAYIM, geri dönüş YOK (kırılım devam): {len(continuation)}"
        f"  (üst {sum(1 for d in continuation if d.side == 'up')}, alt {sum(1 for d in continuation if d.side == 'down')};"
        f" süpürme pencerenin SON barında, şansı yoktu: {sum(1 for d in continuation if d.sweep_on_last_bar)})",
        f"  → geri dönüş var ama giriş barı yok: {sum(1 for d in one_sided if d.kind == 'no_entry_bar')}",
        f"süpürme barının KENDİSİ içeride kapandı (tek taraflı günlerde): "
        f"{sum(1 for d in one_sided if d.sweep_bar_closed_inside)}  — kurulum ÜRETMEZ, okuma tercihinin izi",
        f"kurulum ama geometri kurulamaz: {sum(1 for s in setups if not s.valid)}"
        f"  (giriş stop'un ötesinde {sum(1 for s in setups if s.stop_distance <= 0)},"
        f" hedef girişte geçilmiş {sum(1 for s in setups if s.stop_distance > 0 and s.target_distance <= 0)})",
    ])


def format_years(scans: Sequence[SymbolScan]) -> str:
    primary = [setup for scan in scans for setup in scan.primary]
    ambiguous = [day for scan in scans for day in scan.days if day.kind == "ambiguous"]
    continuation = [day for scan in scans for day in scan.days if day.kind == "continuation"]
    counted = [day for scan in scans for day in scan.counted_days]
    years = sorted({day.day.year for day in counted})
    header = f"{'yıl':<6}{'s-gün':>8}{'BİRİNCİL':>10}{'t.gün':>8}{'belirsiz':>10}{'devam':>8}"
    lines = [header, "-" * len(header)]
    for year in years:
        chosen = [s for s in primary if s.day.year == year]
        lines.append(
            f"{year:<6}{sum(1 for d in counted if d.day.year == year):>8}{len(chosen):>10}"
            f"{len({s.day for s in chosen}):>8}"
            f"{sum(1 for d in ambiguous if d.day.year == year):>10}"
            f"{sum(1 for d in continuation if d.day.year == year):>8}"
        )
    return "\n".join(lines)


def format_clustering(primary: Sequence[Setup]) -> str:
    per_day = Counter(setup.day for setup in primary)
    histogram = Counter(per_day.values())
    lines = [
        f"birincil kurulum {len(primary)} → {len(per_day)} farklı takvim günü"
        f" (gün başına ort. {len(primary) / len(per_day):.2f})" if per_day else "birincil kurulum yok",
    ]
    for size in sorted(histogram):
        lines.append(f"  {size:>2} kurulumlu gün: {histogram[size]}")
    return "\n".join(lines)


def format_geometry(scans: Sequence[SymbolScan], *, round_trip: float) -> str:
    counted = [day for scan in scans for day in scan.counted_days]
    primary = [setup for scan in scans for setup in scan.primary]
    rows = [
        ("Asya genişliği/fiyat (tüm s-günler)", dist([d.asia_width for d in counted]), True),
        ("Asya genişliği/fiyat (birincil)", dist([s.asia_width for s in primary]), True),
        ("stop mesafesi", dist([s.stop_distance for s in primary]), True),
        ("planlanan R/R", dist([s.reward_risk for s in primary]), False),
        (f"maliyet/R (gidiş-dönüş {100 * round_trip:.2f}%)", dist([s.cost_per_r(round_trip) for s in primary]), False),
    ]
    header = f"{'ölçü':<40}{'n':>7}{'p25':>10}{'medyan':>10}{'p75':>10}"
    lines = [header, "-" * len(header)]
    for label, stats, pct in rows:
        lines.append(
            f"{label:<40}{stats.n:>7}{_num(stats.p25, pct=pct, digits=3 if pct else 2):>10}"
            f"{_num(stats.median, pct=pct, digits=3 if pct else 2):>10}"
            f"{_num(stats.p75, pct=pct, digits=3 if pct else 2):>10}"
        )
    for direction in ("short", "long"):
        chosen = [s for s in primary if s.direction == direction]
        stop = dist([s.stop_distance for s in chosen])
        cost = dist([s.cost_per_r(round_trip) for s in chosen])
        rr = dist([s.reward_risk for s in chosen])
        lines.append(
            f"  {direction:<6} n={stop.n}: stop medyan {_num(stop.median, pct=True, digits=3)},"
            f" R/R medyan {_num(rr.median)}, maliyet/R medyan {_num(cost.median)}"
        )
    share = [s for s in primary if s.cost_per_r(round_trip) <= GATE_MAX_MEDIAN_COST_PER_R]
    if primary:
        lines.append(
            f"maliyet/R ≤ {GATE_MAX_MEDIAN_COST_PER_R} olan birincil: {len(share)} / {len(primary)}"
            f" ({100.0 * len(share) / len(primary):.0f}%)"
        )
    return "\n".join(lines)


def format_gate(gate: GateResult) -> str:
    lines = [f"  [{'GEÇTİ' if ok else 'KALDI'}] {name}" for name, ok in gate.checks.items()]
    lines.append(
        f"  → birincil {gate.setups}, takvim günü {gate.days}, medyan maliyet/R {_num(gate.median_cost_per_r)}"
    )
    lines.append(f"  KARAR: {'GEÇTİ — ön-kayda geçilebilir' if gate.passed else 'KALDI — model KURULMAZ'}")
    return "\n".join(lines)


def payload(scans: Sequence[SymbolScan], *, start: pd.Timestamp, end: pd.Timestamp,
            round_trip: float, gate: GateResult) -> dict[str, Any]:
    """Makine okunur sayım. Getiri/R/PnL alanı YOKTUR (test)."""
    primary = [setup for scan in scans for setup in scan.primary]

    def dist_payload(stats: Dist) -> dict[str, Any]:
        return {"n": stats.n, "p25": stats.p25, "median": stats.median, "p75": stats.p75}

    return {
        "scope": {
            "layer": LAYER, "timeframe": TIMEFRAME, "period": "A",
            "start": start.isoformat(), "end": end.isoformat(),
            "asia_utc": "00:00-08:00", "window_utc": "08:00-13:00",
            "reversal": "süpürme barından SONRAKİ ilk içeride kapanış",
            "round_trip_cost": round_trip,
        },
        "symbols": [
            {
                "symbol": scan.symbol, "bars": scan.bars,
                "first_bar": None if scan.first_bar is None else scan.first_bar.isoformat(),
                "skipped": scan.skipped,
                "days": {kind: scan.count(kind) for kind in DAY_KINDS},
                "primary": len(scan.primary),
            }
            for scan in scans
        ],
        "primary_by_year": dict(sorted(Counter(s.day.year for s in primary).items())),
        "calendar_days": len({s.day for s in primary}),
        "geometry": {
            "asia_width": dist_payload(dist([d.asia_width for scan in scans for d in scan.counted_days])),
            "stop_distance": dist_payload(dist([s.stop_distance for s in primary])),
            "planned_reward_risk": dist_payload(dist([s.reward_risk for s in primary])),
            "cost_per_r": dist_payload(dist([s.cost_per_r(round_trip) for s in primary])),
        },
        "gate": {
            "checks": dict(gate.checks), "passed": gate.passed,
            "setups": gate.setups, "days": gate.days, "median_cost_per_r": gate.median_cost_per_r,
        },
    }


# --------------------------------------------------------------------------- #
# Giriş noktası
# --------------------------------------------------------------------------- #
def load_frames(config: Mapping[str, Any], symbols: Sequence[str], *, start: pd.Timestamp,
                end: pd.Timestamp) -> dict[str, pd.DataFrame | Exception]:
    """`now = end`: mumlar dönem A kesiminden ileriye hiç çekilmez."""
    frames: dict[str, pd.DataFrame | Exception] = {}
    for symbol in symbols:
        try:
            frames[symbol] = fetch_ohlcv(dict(config), symbol, now=end)
        except Exception as exc:  # noqa: BLE001
            logger.error("%s çekilemedi: %s", symbol, exc)
            frames[symbol] = exc
    return frames


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

    start = pd.Timestamp(args.start)
    end = pd.Timestamp(args.end)
    cutoff = pd.Timestamp(PERIOD_A_CUTOFF)
    if start >= end:
        logger.error("ters pencere: %s >= %s", start, end)
        return 2
    if end > cutoff:
        logger.error("--end dönem A kesimini aşıyor: %s > %s", end, cutoff)
        return 2

    config = load_config(args.config)
    layer = resolve_layer(config, LAYER)
    universe = layer.symbols or []
    symbols = list(args.symbols) if args.symbols else list(universe)
    outside = [symbol for symbol in symbols if symbol not in universe]
    if not universe or outside:
        logger.error("kapsam dışı sembol: %s", ", ".join(outside) or "(evren yok)")
        return 2

    round_trip = round_trip_cost(layer.config)
    if not math.isclose(round_trip, PREREGISTERED_ROUND_TRIP, rel_tol=1e-9):
        logger.error("gidiş-dönüş maliyeti ön-kayıttan ayrıştı: %s != %s", round_trip, PREREGISTERED_ROUND_TRIP)
        return 2

    cache_dir = args.cache_dir or tempfile.mkdtemp(prefix="po3-")
    run_config = copy.deepcopy(dict(layer.config))
    run_config["timeframe"] = TIMEFRAME
    run_config["data"] = {
        **run_config["data"],
        "history_bars": int(math.ceil((end - start) / BAR)) + 96,
        "cache_dir": str(cache_dir),
    }
    logger.info("kapsam: %s %s, dönem A %s → %s, %d sembol, önbellek %s",
                LAYER, TIMEFRAME, start, end, len(symbols), cache_dir)

    frames = load_frames(run_config, symbols, start=start, end=end)
    scans = [
        SymbolScan(symbol=symbol, skipped=f"veri çekilemedi: {frames[symbol]}")
        if isinstance(frames[symbol], Exception)
        else scan_symbol(symbol, frames[symbol], start=start, end=end)
        for symbol in symbols
    ]

    if not any(scan.counted_days for scan in scans):
        logger.error("hiçbir sembol-gün sayılamadı — sayım YAPILMADI (veri kapısı)")
        print(format_coverage(scans))
        return 3

    primary = [setup for scan in scans for setup in scan.primary]
    gate = evaluate_gate(primary, round_trip=round_trip)

    print()
    print("=== 0. KAPSAM ===")
    print(format_coverage(scans))
    print()
    print("=== 1. SAYIM: sembol bazında (s-gün = tam veri, düz olmayan sembol-gün) ===")
    print(format_counts(scans))
    print()
    print("=== 2. SÜPÜRME SONRASI: kurulum ↔ KARŞI SAYIM ===")
    print(format_diagnostics(scans))
    print()
    print("=== 3. YIL DAĞILIMI ve TAKVİM GÜNÜ YOĞUNLAŞMASI ===")
    print(format_years(scans))
    print()
    print(format_clustering(primary))
    print()
    print("=== 4. GEOMETRİ (giriş anında bilinen; getiri DEĞİL) — BİRİNCİL kurulumlar ===")
    print(format_geometry(scans, round_trip=round_trip))
    print()
    print("=== KARAR KAPISI (ön-kayıtlı) ===")
    print(format_gate(gate))

    if args.results_json:
        path = Path(args.results_json)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(payload(scans, start=start, end=end, round_trip=round_trip, gate=gate),
                       indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        logger.info("yük yazıldı: %s", path)
    return 0


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="PO3/AMD (Asya aralığı süpürme + geri dönüş) ölçülebilirlik sayımı (SALT OKUNUR; getiri/R hesaplanmaz)."
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

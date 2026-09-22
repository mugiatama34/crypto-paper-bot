#!/usr/bin/env python3
"""Aday "ölüm kesişimi + geri çekilme" short'unun ÖLÇÜLEBİLİRLİK SAYIMI. SALT OKUNUR.

Cevapladığı soru tek: *bu kurulum dönem A'da ölçülebilir SAYIDA oluşuyor mu?*

**Bu bir strateji koşusu DEĞİLDİR.** Getiri, R, PnL, kazanma oranı, kâr faktörü ve
drawdown burada ne hesaplanır ne raporlanır; araç `core/metrics.py`, `core/portfolio.py`,
`core/ledger.py` ve `strategies/*` modüllerini import ETMEZ (test:
`tests/test_measure_death_cross.py`). Ayrım kasıtlıdır ve `scripts/measure_funding.py`nin
aynı gerekçesidir (docs/backtest.md > 7): **bir kurulumun kaç kez oluştuğunu görmek eşik
seçimini kirletmez, ne kazandırdığını görmek kirletirdi.** Sayım bir modelin ön-kaydından
ÖNCE gelir; sonucu gördükten sonra tanımı oynatmak, kapatmak için var olduğu serbestliği
geri açardı.

Sayım ayrıca deftere, `config.yaml`a, `strategies/`e ve `data/cache/`e YAZMAZ: mum
önbelleği koşuya özel bir dizine (`--cache-dir`, varsayılan geçici dizin) düşer. Gerekçe
`measure_funding.py`nin aynısıdır — canlı turun önbelleği `data.history_bars` penceresiyle
yaşar, bu sayım ise yıllar ister; aynı dizine iki saklama kuralıyla yazmak canlı turun
önbelleğini bu aracın penceresine bağlardı.

## Kapsam bir KAPIDIR: yalnızca dönem A

Sınırlar `scripts/backtest_ema.py`den İTHAL EDİLİR (`PERIOD_A_START`, `PERIOD_A_CUTOFF`) —
iki yerde yazılı bir pencere bir gün ayrışır ve sayım, karara giren koşudan başka bir
piyasa geçmişini anlatmaya başlar. `--end` kesimi aşarsa betik HATA KODUYLA biter: dönem B
bir sonraki tezin OOS penceresidir ve bir sayım uğruna açılmaz. Mumlar da kesimden ileriye
hiç ÇEKİLMEZ (`now = --end`), yani dönem B'ye bakılmış olma ihtimali koda gömülüdür.

## Isınma dönem A'nın İÇİNDEN yenmez

EMA özyinelemelidir (`core/indicators.py::ema`, tohum ilk `period` barın SMA'sı), yani
2022-01-01'deki EMA200 ancak öncesinden gelen barlarla tanımlıdır. Bu yüzden her sembol
için sayılan ilk bar `ilk bar + warmup_bars`tan ÖNCE olamaz:

- Geçmişi dönem A'dan eskiye giden sembollerde (BTC, ETH, …) ısınma tamamen dönem A'nın
  ÖNCESİNDEN gelir ve sayım 2022-01-01'de başlar.
- Dönem A'nın İÇİNDE listelenmiş bir sembolde (SUI, PENGU, ETHFI) ısınma başka bir yerden
  gelemez; o sembolün sayımı ısınma bittikten SONRA başlar ve bu, satırın kendi
  `sayım_başı` kolonunda GÖRÜNÜR. Kısmi pencereyle hesaplanmış bir EMA200 ile sayılan bir
  kurulum, ölçülmemiş bir sayıyı ölçülmüş gibi gösterirdi.

## Tanımlar (verildiği gibi; bu betik onları DEĞİŞTİRMEZ)

- `EMA50`, `EMA200`: kapanış üzerinden, katmanın barında (4H).
- **Ölüm kesişimi**: bar t'de `EMA50 < EMA200` ve bar t−1'de `EMA50 >= EMA200`.
- **Rejim**: ölüm kesişiminden sonra `EMA50 < EMA200` kaldığı sürece aktif. Kesişimin
  KENDİ barı rejimin ilk barıdır (o barda koşul zaten sağlanıyor) — okumanın bedeli
  raporda ayrıca yazılır (`kesişim barında kurulum` satırı), gizlenmez.
- **Kurulum barı** (rejim aktifken): `high >= EMA50` VE `close < EMA50` VE `close < open`.
- ChopZone bu sayıma DÂHİL DEĞİLDİR (tanımı henüz seçilmedi). Filtresiz sayım bir ÜST
  SINIRDIR: ChopZone ancak düşürebilir.

Kesişim bir OLAYDIR ve rejim bir DURUMDUR: rejim durumu ısınma barlarından taşınabilir
(sayımın ilk barında rejim zaten aktif olabilir), ama RAPORLANAN kesişim sayısı yalnızca
sayım penceresine düşen kesişimleri sayar. Karışımın bedeli tek bir satırda görünür
(`taşınan rejim`).

## Üç sayım biçimi ve neden üçü de gerekir

- **ham**: koşulu sağlayan TÜM barlar. Üst sınır.
- **BİRİNCİL**: bir önceki BİRİNCİL kurulumdan en az `--min-gap-bars` (6 bar = 24 saat)
  sonra gelenler; kümelenmiş ardışık barlar tek sayılır. Çıpa son KABUL EDİLEN kurulumdur,
  son ham kurulum değil — "küme tek sayılır" şartının tanımı budur. **Karar kapısı bu
  sayıya bakar.**
- **rejim başına ilk**: her rejim epizodunun İLK kurulumu. Epizodun ilk kurulumu ısınma
  penceresine düşüyorsa o epizod sayılmaz (sayılsaydı "rejimin ilki" aslında "gördüğümüz
  ilki" olurdu).

## Stop geometrisi: mesafe, getiri DEĞİL

Her BİRİNCİL kurulumda `(EMA200 − close) / ATR(14, simple)`. Bu bir getiri değil bir
MESAFEDİR ve tam olarak kural 14'ün sorusunu sorar: bu kurulumun stop'u ölçüm tavanına
sığıyor mu? ATR projenin ortak tanımından okunur (`core/indicators.py`, periyot
`trailing.atr_period`, yumuşatma `simple` — `ema` katmanının motoru tavanı bununla ölçer;
`ema_trend`in `wilder`ı o modelin dış sistem paritesiydi, bu adayın öyle bir referansı
yok). Yüzdelikler `numpy.percentile`ın doğrusal aradeğerlemesidir ve bu tek tanım burada
yazılıdır — bir PERFORMANS metriği olmadığı için `core/metrics.py`de karşılığı yoktur
(aynı gerekçe `diagnose_ema_exits.py`nin yol istatistiği).

## Çıkış kodları (karar 51: boş rapor yeşil dönmez)

- `0` — sayım yapıldı.
- `2` — kullanım hatası (kapsam dışı sembol/katman, ters pencere, dönem A kesimini aşan
  `--end`).
- `3` — VERİ KAPISI: hiçbir sembol taranamadı (hiç bar yok ya da hepsi ısınmaya yetmedi).
  Boş bir tablo sıfır kodla dönseydi okuyucu "kurulum oluşmuyor" derdi, oysa doğru cevap
  "ölçemedik".

Kullanım (depo kökünden):
    python scripts/measure_death_cross.py
    python scripts/measure_death_cross.py --symbols BTC-USDT-SWAP ETH-USDT-SWAP
    python scripts/measure_death_cross.py --results-json out/death_cross.json
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from core.config import get_setting, load_config  # noqa: E402
from core.data import OKXClient, fetch_ohlcv  # noqa: E402
from core.indicators import average_true_range  # noqa: E402
from core.layers import resolve_layer  # noqa: E402
from scripts.backtest_ema import PERIOD_A_CUTOFF, PERIOD_A_START  # noqa: E402

logger = logging.getLogger("measure-death-cross")

LAYER = "ema"

# Aday modelin tanımı (verildiği gibi). Süpürülmez: bu betik bir parametre araması değil,
# tek bir tanımın sayımıdır.
FAST_PERIOD = 50
SLOW_PERIOD = 200

# Kümeleme kuralı: 6 bar = 24 saat (4H). Sayının kendisi kapının bir parçasıdır.
MIN_GAP_BARS = 6

# Isınma: EMA200'ün tohumu (ilk 200 barın SMA'sı) + sönümlenme payı. Tohumun ağırlığı
# (1 − 2/201)^n ile söner; 400 bar sonra ~%1.4'e iner. 600 bar ≈ 100 gün.
WARMUP_BARS = 600

# Stop geometrisi eşikleri: ilki `ema` katmanının tavanı (`max_stop_atr_multiple`), ikincisi
# `xsec` katmanının tavanının da üstü — "hangi tavan bu kurulumu kurtarır" sorusu için.
ATR_THRESHOLDS = (3.0, 6.0)


# --------------------------------------------------------------------------- #
# Göstergeler
# --------------------------------------------------------------------------- #
def ema_series(values: pd.Series, period: int) -> pd.Series:
    """Her bar için EMA; ilk `period − 1` barda NaN.

    `core/indicators.py::ema`nın BAR BAZLI hâlidir ve ondan AYRI bir tanım DEĞİLDİR: aynı
    tohum (ilk `period` barın SMA'sı) ve aynı özyineleme. Tek bir ileri geçişte
    hesaplanmasının sebebi maliyettir — her bar için `ema(series[:t+1])` çağırmak
    sembol başına milyonlarca işlem demekti. Eşitlik VARSAYILMAZ, `--verify` ile rastgele
    barlarda `core/indicators.py::ema`ya karşı SINANIR (aynı gerekçe
    `measure_vwap_signal.py::--verify`).
    """
    if period <= 0:
        raise ValueError(f"period pozitif olmalı: {period}")
    data = values.to_numpy(dtype="float64")
    out = np.full(len(data), np.nan, dtype="float64")
    if len(data) < period:
        return pd.Series(out, index=values.index, dtype="float64")
    alpha = 2.0 / (period + 1.0)
    current = float(data[:period].mean())
    out[period - 1] = current
    for position in range(period, len(data)):
        current = alpha * float(data[position]) + (1.0 - alpha) * current
        out[position] = current
    return pd.Series(out, index=values.index, dtype="float64")


# --------------------------------------------------------------------------- #
# Sayım birimleri
# --------------------------------------------------------------------------- #
@dataclass(frozen=True, kw_only=True)
class Setup:
    """Bir kurulum barı. Getiri/R taşımaz — yalnızca kimlik ve GEOMETRİ."""

    symbol: str
    bar: pd.Timestamp
    close: float
    ema_fast: float
    ema_slow: float
    atr: float | None
    regime_start: pd.Timestamp
    on_cross_bar: bool

    @property
    def stop_distance(self) -> float:
        """EMA200 − kapanış. Bir MESAFEDİR; bir getiri değil."""
        return self.ema_slow - self.close

    @property
    def atr_multiple(self) -> float | None:
        if self.atr is None or self.atr <= 0.0:
            return None
        return self.stop_distance / self.atr


@dataclass(frozen=True, kw_only=True)
class SymbolScan:
    """Tek sembolün sayımı. Başarısız sembol de bir SONUÇTUR (sessizce düşmez)."""

    symbol: str
    bars: int = 0
    first_bar: pd.Timestamp | None = None
    last_bar: pd.Timestamp | None = None
    count_from: pd.Timestamp | None = None
    counted_bars: int = 0
    warmup_from_inside: bool = False
    regime_carried_in: bool = False
    crosses: tuple[pd.Timestamp, ...] = ()
    raw: tuple[Setup, ...] = ()
    primary: tuple[Setup, ...] = ()
    first_of_regime: tuple[Setup, ...] = ()
    skipped: str | None = None


def death_crosses(fast: pd.Series, slow: pd.Series) -> pd.Series:
    """Bar t'de `fast < slow` ve bar t−1'de `fast >= slow` olan barlar (bool seri).

    NaN taşıyan barlar (ısınma) hiçbir yöne sayılmaz: `NaN >= NaN` False'tur ve
    karşılaştırma zaten False döndürür, ama koşul AÇIKÇA yazılır — sessiz bir NaN
    davranışına yaslanmak, ilk tanımlı barı "kesişim" göstermeye bir adım uzaktır.
    """
    below = fast < slow
    defined = fast.notna() & slow.notna()
    previous_defined = defined.shift(1, fill_value=False)
    previous_not_below = ~below.shift(1, fill_value=False)
    return below & defined & previous_defined & previous_not_below


def regime_starts(crosses: pd.Series, below: pd.Series) -> pd.Series:
    """Her bar için o barda aktif olan rejimin BAŞLANGIÇ damgası (aktif değilse NaT).

    Rejim bir DURUMDUR: kesişimde başlar, `fast < slow` bozulduğunda biter. Kesişim
    görülmeden `fast < slow` olan barlar (serinin en başı) rejim sayılmaz — tanım
    "ölüm kesişiminden SONRA" der ve gözlemlenmemiş bir kesişimi varsaymak, ısınma
    penceresinin bilmediği bir olayı ölçüme sokmak olurdu.
    """
    starts: list[pd.Timestamp | None] = []
    active: pd.Timestamp | None = None
    for stamp, is_cross, is_below in zip(crosses.index, crosses.to_numpy(), below.to_numpy()):
        if bool(is_cross):
            active = stamp
        elif not bool(is_below):
            active = None
        starts.append(active)
    return pd.Series(starts, index=crosses.index, dtype="object")


def setup_bars(frame: pd.DataFrame, fast: pd.Series) -> pd.Series:
    """`high >= EMA50` VE `close < EMA50` VE `close < open` (bool seri).

    Rejim şartı BURADA yoktur: tanımın iki parçası (rejim + mum) ayrı tutulur ki
    "rejim yok" ile "mum tutmadı" ayrı sayılabilsin.
    """
    touched = frame["high"] >= fast
    closed_below = frame["close"] < fast
    bearish = frame["close"] < frame["open"]
    return touched & closed_below & bearish & fast.notna()


def collapse_clusters(stamps: Sequence[pd.Timestamp], index: pd.DatetimeIndex, *, min_gap: int) -> list[pd.Timestamp]:
    """BİRİNCİL kurulumlar: bir öncekinden en az `min_gap` BAR sonra gelenler.

    Çıpa son KABUL EDİLEN kurulumdur. Son HAM kurulumu çıpa almak, 7 bar süren bir
    kümeyi (her bar bir kurulum) yedi ayrı birincil kurulum gibi gösterirdi — tam da
    "kümelenmiş ardışık barlar tek sayılır" şartının engellediği şey.

    Zincir SERİNİN TAMAMI üzerinde kurulur, sonra sayım penceresine kırpılır: küme bir
    barın değil SERİNİN özelliğidir ve pencerenin kenarına düşen bir kümenin ilk barı,
    zincir pencerede başlatılsaydı ikinci kez "birincil" sayılırdı.

    Mesafe BAR cinsindendir, takvim cinsinden değil: bir veri boşluğu takvim farkını
    büyütür ama araya giren bar yoktur, yani kurulum hâlâ aynı kümenin parçasıdır.
    """
    positions = {stamp: number for number, stamp in enumerate(index)}
    accepted: list[pd.Timestamp] = []
    last: int | None = None
    for stamp in stamps:
        position = positions[stamp]
        if last is None or position - last >= min_gap:
            accepted.append(stamp)
            last = position
    return accepted


def first_per_regime(stamps: Sequence[pd.Timestamp], regimes: pd.Series) -> dict[pd.Timestamp, pd.Timestamp]:
    """Rejim başlangıcı -> o rejimin İLK kurulum barı (serinin TAMAMI üzerinde).

    Serinin tamamına bakmak şarttır: ilk kurulumu ısınma penceresine düşen bir rejimin
    sayım penceresindeki ilk satırı o rejimin ilki DEĞİLDİR. İkisini ayırmadan saymak,
    "rejimin ilki" yerine "gördüğümüz ilki"ni raporlamak olurdu.
    """
    firsts: dict[pd.Timestamp, pd.Timestamp] = {}
    for stamp in stamps:
        start = regimes.loc[stamp]
        if start not in firsts:
            firsts[start] = stamp
    return firsts


# --------------------------------------------------------------------------- #
# Sembol taraması
# --------------------------------------------------------------------------- #
def scan_symbol(
    symbol: str,
    frame: pd.DataFrame,
    *,
    start: pd.Timestamp,
    end: pd.Timestamp,
    atr_period: int,
    warmup_bars: int,
    min_gap_bars: int,
) -> SymbolScan:
    """Tek sembolün üç sayımı + birincil kurulumların geometrisi."""
    if frame.empty:
        return SymbolScan(symbol=symbol, skipped="hiç bar yok")

    frame = frame.loc[:end]
    if frame.empty:
        return SymbolScan(symbol=symbol, skipped="pencerede bar yok")

    first_bar = frame.index[0]
    last_bar = frame.index[-1]

    if len(frame) <= warmup_bars:
        return SymbolScan(
            symbol=symbol,
            bars=len(frame),
            first_bar=first_bar,
            last_bar=last_bar,
            skipped=f"ısınmaya yetmiyor ({len(frame)} bar <= {warmup_bars})",
        )

    # Isınma DÖNEM A'NIN ÖNCESİNDEN alınır; sembolün geçmişi oraya yetişmiyorsa sayım
    # ısınma bittikten sonra başlar ve bu satırın kendisinde görünür.
    warmup_end = frame.index[warmup_bars]
    count_from = max(start, warmup_end)
    if count_from > last_bar:
        return SymbolScan(
            symbol=symbol,
            bars=len(frame),
            first_bar=first_bar,
            last_bar=last_bar,
            skipped="ısınma penceresi sayım penceresini yutuyor",
        )

    close = frame["close"]
    fast = ema_series(close, FAST_PERIOD)
    slow = ema_series(close, SLOW_PERIOD)
    below = (fast < slow) & fast.notna() & slow.notna()
    crosses = death_crosses(fast, slow)
    starts = regime_starts(crosses, below)
    candles = setup_bars(frame, fast)

    window = (frame.index >= count_from) & (frame.index <= end)

    # Kurulumlar SERİNİN TAMAMINDA bulunur (küme zinciri ve "rejimin ilki" serinin
    # özellikleridir), RAPORLANAN sayı ise yalnızca sayım penceresine düşenlerdir.
    in_regime = starts.notna()
    all_stamps = list(frame.index[(candles & in_regime).to_numpy()])
    primary_stamps = set(collapse_clusters(all_stamps, frame.index, min_gap=min_gap_bars))
    regime_firsts = set(first_per_regime(all_stamps, starts).values())

    setups: list[Setup] = []
    for stamp in all_stamps:
        if stamp < count_from or stamp > end:
            continue
        setups.append(
            Setup(
                symbol=symbol,
                bar=stamp,
                close=float(frame.at[stamp, "close"]),
                ema_fast=float(fast.loc[stamp]),
                ema_slow=float(slow.loc[stamp]),
                atr=average_true_range(frame.loc[:stamp], atr_period, smoothing="simple"),
                regime_start=starts.loc[stamp],
                on_cross_bar=bool(crosses.loc[stamp]),
            )
        )

    primary = [setup for setup in setups if setup.bar in primary_stamps]
    firsts = [setup for setup in setups if setup.bar in regime_firsts]

    counted_crosses = tuple(stamp for stamp in crosses.index[crosses.to_numpy()] if stamp >= count_from)
    carried = starts.loc[count_from] if count_from in starts.index else None

    return SymbolScan(
        symbol=symbol,
        bars=len(frame),
        first_bar=first_bar,
        last_bar=last_bar,
        count_from=count_from,
        counted_bars=int(window.sum()),
        warmup_from_inside=warmup_end > start,
        regime_carried_in=carried is not None and not pd.isna(carried),
        crosses=counted_crosses,
        raw=tuple(setups),
        primary=tuple(primary),
        first_of_regime=tuple(firsts),
    )


# --------------------------------------------------------------------------- #
# Geometri dağılımı
# --------------------------------------------------------------------------- #
@dataclass(frozen=True, kw_only=True)
class Geometry:
    """Birincil kurulumların stop MESAFESİ dağılımı (ATR katı). Getiri yok."""

    measured: int
    unmeasured: int
    median: float | None
    p75: float | None
    p90: float | None
    maximum: float | None
    above: Mapping[float, int] = field(default_factory=dict)

    def share(self, threshold: float) -> float | None:
        if not self.measured:
            return None
        return 100.0 * self.above.get(threshold, 0) / self.measured


def geometry(setups: Sequence[Setup], *, thresholds: Sequence[float] = ATR_THRESHOLDS) -> Geometry:
    """`(EMA200 − close) / ATR` dağılımı.

    ATR hesaplanamayan kurulum (yeterli bar yok, sıfır ATR) `unmeasured` sayılır ve
    dağılıma GİRMEZ — uydurma bir mesafe, "ölçemedik" ile "yakındı"yı aynı hücreye
    yazardı (`core/metrics.py`nin `nan` kuralının buradaki hâli).
    """
    values = [setup.atr_multiple for setup in setups]
    measured = np.array([value for value in values if value is not None], dtype="float64")
    unmeasured = sum(1 for value in values if value is None)
    if measured.size == 0:
        return Geometry(measured=0, unmeasured=unmeasured, median=None, p75=None, p90=None, maximum=None)
    return Geometry(
        measured=int(measured.size),
        unmeasured=unmeasured,
        median=float(np.percentile(measured, 50)),
        p75=float(np.percentile(measured, 75)),
        p90=float(np.percentile(measured, 90)),
        maximum=float(measured.max()),
        above={float(threshold): int((measured > threshold).sum()) for threshold in thresholds},
    )


# --------------------------------------------------------------------------- #
# Kırılımlar
# --------------------------------------------------------------------------- #
def by_year(stamps: Sequence[pd.Timestamp]) -> dict[int, int]:
    counts: dict[int, int] = {}
    for stamp in stamps:
        counts[int(stamp.year)] = counts.get(int(stamp.year), 0) + 1
    return dict(sorted(counts.items()))


# --------------------------------------------------------------------------- #
# Veri
# --------------------------------------------------------------------------- #
def load_frames(
    config: dict[str, Any],
    symbols: Sequence[str],
    *,
    end: pd.Timestamp,
    client: OKXClient | None = None,
) -> dict[str, pd.DataFrame | Exception]:
    """Sembol -> mum çerçevesi. Düşen sembol SESSİZCE atlanmaz, istisnasıyla döner.

    `now = end`: mumlar dönem A kesiminden ileriye hiç çekilmez. Kapsam kapısı bir
    bayrak kontrolü değil, isteğin kendisidir.
    """
    frames: dict[str, pd.DataFrame | Exception] = {}
    active = client if client is not None else OKXClient.from_config(config)
    for symbol in symbols:
        try:
            frames[symbol] = fetch_ohlcv(config, symbol, client=active, now=end)
        except Exception as exc:  # noqa: BLE001
            logger.error("%s çekilemedi: %s", symbol, exc)
            frames[symbol] = exc
    return frames


# --------------------------------------------------------------------------- #
# Doğrulama: EMA'nın tek tanımı
# --------------------------------------------------------------------------- #
def verify_ema(frame: pd.DataFrame, *, period: int, samples: int, seed: int) -> list[str]:
    """`ema_series` ile `core/indicators.py::ema` rastgele barlarda AYNI sayıyı vermeli."""
    from core.indicators import ema as ema_point  # gecikmeli: tek tanım core'da

    close = frame["close"]
    series = ema_series(close, period)
    rng = np.random.default_rng(seed)
    positions = [int(value) for value in rng.integers(period - 1, len(close), size=min(samples, len(close)))]
    problems: list[str] = []
    for position in sorted(set(positions)):
        expected = ema_point(close.iloc[: position + 1], period)
        actual = float(series.iloc[position])
        if expected is None or not np.isclose(expected, actual, rtol=1e-12, atol=1e-12):
            problems.append(f"{close.index[position]}: {actual!r} != {expected!r}")
    return problems


# --------------------------------------------------------------------------- #
# Rapor
# --------------------------------------------------------------------------- #
def format_coverage(scans: Sequence[SymbolScan]) -> str:
    header = f"{'sembol':<18}{'bar':>7}{'ilk bar':>22}{'sayım başı':>22}{'sayılan bar':>13}  not"
    lines = [header, "-" * len(header)]
    for scan in scans:
        note = scan.skipped or ""
        if not note:
            flags = []
            if scan.warmup_from_inside:
                flags.append("ısınma dönem A'nın İÇİNDEN")
            if scan.regime_carried_in:
                flags.append("rejim taşındı")
            note = "; ".join(flags)
        lines.append(
            f"{scan.symbol:<18}{scan.bars:>7}"
            f"{_stamp(scan.first_bar):>22}{_stamp(scan.count_from):>22}"
            f"{scan.counted_bars:>13}  {note}"
        )
    return "\n".join(lines)


def format_counts(scans: Sequence[SymbolScan]) -> str:
    header = f"{'sembol':<18}{'kesişim':>9}{'ham':>7}{'BİRİNCİL':>10}{'rejim-ilk':>11}"
    lines = [header, "-" * len(header)]
    for scan in scans:
        lines.append(
            f"{scan.symbol:<18}{len(scan.crosses):>9}{len(scan.raw):>7}"
            f"{len(scan.primary):>10}{len(scan.first_of_regime):>11}"
        )
    lines.append("-" * len(header))
    lines.append(
        f"{'TOPLAM':<18}"
        f"{sum(len(scan.crosses) for scan in scans):>9}"
        f"{sum(len(scan.raw) for scan in scans):>7}"
        f"{sum(len(scan.primary) for scan in scans):>10}"
        f"{sum(len(scan.first_of_regime) for scan in scans):>11}"
    )
    return "\n".join(lines)


def format_years(scans: Sequence[SymbolScan]) -> str:
    crosses = by_year([stamp for scan in scans for stamp in scan.crosses])
    raw = by_year([setup.bar for scan in scans for setup in scan.raw])
    primary = by_year([setup.bar for scan in scans for setup in scan.primary])
    firsts = by_year([setup.bar for scan in scans for setup in scan.first_of_regime])
    years = sorted(set(crosses) | set(raw) | set(primary) | set(firsts))
    header = f"{'yıl':<8}{'kesişim':>9}{'ham':>7}{'BİRİNCİL':>10}{'rejim-ilk':>11}"
    lines = [header, "-" * len(header)]
    for year in years:
        lines.append(
            f"{year:<8}{crosses.get(year, 0):>9}{raw.get(year, 0):>7}"
            f"{primary.get(year, 0):>10}{firsts.get(year, 0):>11}"
        )
    return "\n".join(lines)


def format_geometry(scans: Sequence[SymbolScan]) -> str:
    header = (
        f"{'sembol':<18}{'n':>6}{'medyan':>9}{'p75':>9}{'p90':>9}{'azami':>9}"
        + "".join(f"{'>' + f'{threshold:g}':>9}" for threshold in ATR_THRESHOLDS)
        + f"{'ölçülemedi':>12}"
    )
    lines = [header, "-" * len(header)]
    for scan in scans:
        if not scan.primary:
            continue
        lines.append(_geometry_row(scan.symbol, geometry(scan.primary)))
    pooled = geometry([setup for scan in scans for setup in scan.primary])
    lines.append("-" * len(header))
    lines.append(_geometry_row("HAVUZ", pooled))
    return "\n".join(lines)


def _geometry_row(label: str, stats: Geometry) -> str:
    cells = "".join(
        f"{_share(stats, threshold):>9}" for threshold in ATR_THRESHOLDS
    )
    return (
        f"{label:<18}{stats.measured:>6}{_num(stats.median):>9}{_num(stats.p75):>9}"
        f"{_num(stats.p90):>9}{_num(stats.maximum):>9}{cells}{stats.unmeasured:>12}"
    )


def _share(stats: Geometry, threshold: float) -> str:
    count = stats.above.get(threshold, 0)
    share = stats.share(threshold)
    return "—" if share is None else f"{count} ({share:.0f}%)"


def _num(value: float | None) -> str:
    return "—" if value is None else f"{value:.2f}"


def _stamp(value: pd.Timestamp | None) -> str:
    return "—" if value is None else value.strftime("%Y-%m-%d %H:%M")


# --------------------------------------------------------------------------- #
# Yük
# --------------------------------------------------------------------------- #
def payload(
    scans: Sequence[SymbolScan],
    *,
    start: pd.Timestamp,
    end: pd.Timestamp,
    warmup_bars: int,
    min_gap_bars: int,
    atr_period: int,
    timeframe: str,
) -> dict[str, Any]:
    """Makine okunur sayım. Getiri/R/PnL alanı YOKTUR ve eklenemez (test)."""
    pooled = geometry([setup for scan in scans for setup in scan.primary])
    return {
        "scope": {
            "layer": LAYER,
            "timeframe": timeframe,
            "period": "A",
            "start": start.isoformat(),
            "end": end.isoformat(),
            "warmup_bars": warmup_bars,
            "min_gap_bars": min_gap_bars,
            "atr_period": atr_period,
            "atr_smoothing": "simple",
            "fast_period": FAST_PERIOD,
            "slow_period": SLOW_PERIOD,
            "chopzone": "DÂHİL DEĞİL (tanım seçilmedi); sayım bir ÜST SINIRDIR",
        },
        "symbols": [
            {
                "symbol": scan.symbol,
                "bars": scan.bars,
                "first_bar": _iso(scan.first_bar),
                "last_bar": _iso(scan.last_bar),
                "count_from": _iso(scan.count_from),
                "counted_bars": scan.counted_bars,
                "warmup_from_inside": scan.warmup_from_inside,
                "regime_carried_in": scan.regime_carried_in,
                "crosses": len(scan.crosses),
                "raw": len(scan.raw),
                "primary": len(scan.primary),
                "first_of_regime": len(scan.first_of_regime),
                "on_cross_bar": sum(1 for setup in scan.primary if setup.on_cross_bar),
                "geometry": _geometry_payload(geometry(scan.primary)),
                "skipped": scan.skipped,
            }
            for scan in scans
        ],
        "totals": {
            "crosses": sum(len(scan.crosses) for scan in scans),
            "raw": sum(len(scan.raw) for scan in scans),
            "primary": sum(len(scan.primary) for scan in scans),
            "first_of_regime": sum(len(scan.first_of_regime) for scan in scans),
            "on_cross_bar": sum(1 for scan in scans for setup in scan.primary if setup.on_cross_bar),
        },
        "by_year": {
            "crosses": by_year([stamp for scan in scans for stamp in scan.crosses]),
            "raw": by_year([setup.bar for scan in scans for setup in scan.raw]),
            "primary": by_year([setup.bar for scan in scans for setup in scan.primary]),
            "first_of_regime": by_year([setup.bar for scan in scans for setup in scan.first_of_regime]),
        },
        "geometry_pooled": _geometry_payload(pooled),
    }


def _geometry_payload(stats: Geometry) -> dict[str, Any]:
    return {
        "measured": stats.measured,
        "unmeasured": stats.unmeasured,
        "median_atr": stats.median,
        "p75_atr": stats.p75,
        "p90_atr": stats.p90,
        "max_atr": stats.maximum,
        "above": {f"{threshold:g}": stats.above.get(threshold, 0) for threshold in ATR_THRESHOLDS},
        "share_pct": {
            f"{threshold:g}": stats.share(threshold) for threshold in ATR_THRESHOLDS
        },
    }


def _iso(value: pd.Timestamp | None) -> str | None:
    return None if value is None else value.isoformat()


# --------------------------------------------------------------------------- #
# Giriş noktası
# --------------------------------------------------------------------------- #
def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    start = pd.Timestamp(args.start)
    end = pd.Timestamp(args.end)
    cutoff = pd.Timestamp(PERIOD_A_CUTOFF)

    if start >= end:
        logger.error("ters pencere: %s >= %s", start, end)
        return 2
    if end > cutoff:
        # Dönem A kesimi bir KAPIDIR (docs/backtest.md > 7): dönem B bir sonraki tezin
        # OOS penceresidir ve bir ölçülebilirlik sayımı için açılmaz.
        logger.error("--end dönem A kesimini aşıyor: %s > %s", end, cutoff)
        return 2

    config = load_config(args.config)
    layer = resolve_layer(config, LAYER)
    universe = layer.symbols or []
    if not universe:
        logger.error("katmanın sabit evreni yok: %s", LAYER)
        return 2

    symbols = list(args.symbols) if args.symbols else list(universe)
    outside = [symbol for symbol in symbols if symbol not in universe]
    if outside:
        logger.error("kapsam dışı sembol: %s", ", ".join(outside))
        return 2

    # Önbellek koşuya özeldir: canlı turun `data/cache`i bu aracın penceresiyle
    # yeniden yazılmaz (aynı gerekçe `measure_funding.py`).
    cache_dir = args.cache_dir or tempfile.mkdtemp(prefix="death-cross-")
    run_config = {
        **layer.config,
        "data": {
            **layer.config["data"],
            "history_bars": int(args.history_bars),
            "cache_dir": str(cache_dir),
        },
    }
    atr_period = int(get_setting(run_config, "trailing.atr_period"))
    timeframe = str(get_setting(run_config, "timeframe"))

    logger.info(
        "kapsam: katman=%s bar=%s dönem A %s → %s | ısınma %d bar | küme %d bar | önbellek %s",
        LAYER, timeframe, start.date(), end.date(), args.warmup_bars, args.min_gap_bars, cache_dir,
    )

    frames = load_frames(run_config, symbols, end=end)

    scans: list[SymbolScan] = []
    problems: list[str] = []
    for symbol in symbols:
        frame = frames[symbol]
        if isinstance(frame, Exception):
            scans.append(SymbolScan(symbol=symbol, skipped=f"veri çekilemedi: {frame}"))
            continue
        if args.verify and not frame.empty:
            problems.extend(
                f"{symbol} EMA{period}: {problem}"
                for period in (FAST_PERIOD, SLOW_PERIOD)
                for problem in verify_ema(
                    frame, period=period, samples=args.verify, seed=int(get_setting(run_config, "random_seed"))
                )
            )
        scans.append(
            scan_symbol(
                symbol,
                frame,
                start=start,
                end=end,
                atr_period=atr_period,
                warmup_bars=int(args.warmup_bars),
                min_gap_bars=int(args.min_gap_bars),
            )
        )

    if args.verify:
        if problems:
            logger.error("EMA doğrulaması DÜŞTÜ (%d sapma):\n%s", len(problems), "\n".join(problems[:20]))
            return 1
        logger.info("EMA doğrulaması geçti: bar bazlı seri = core/indicators.py::ema")

    scanned = [scan for scan in scans if scan.skipped is None]
    if not scanned:
        # VERİ KAPISI (karar 51): boş bir tablo yeşil dönerse okuyucu "kurulum oluşmuyor"
        # der; doğru cevap "ölçemedik".
        logger.error("hiçbir sembol taranamadı — sayım YAPILMADI")
        print(format_coverage(scans))
        return 3

    report = payload(
        scans,
        start=start,
        end=end,
        warmup_bars=int(args.warmup_bars),
        min_gap_bars=int(args.min_gap_bars),
        atr_period=atr_period,
        timeframe=timeframe,
    )

    print()
    print("=== 0. KAPSAM (bu sayım neye dayanıyor) ===")
    print(format_coverage(scans))
    print()
    print("=== 1-2. SAYIM: sembol bazında ===")
    print(format_counts(scans))
    print()
    print("=== 2. SAYIM: yıl bazında ===")
    print(format_years(scans))
    print()
    print("=== 3. STOP GEOMETRİSİ: (EMA200 − close) / ATR(%d, simple), BİRİNCİL kurulumlar ===" % atr_period)
    print(format_geometry(scans))
    print()
    print(
        f"BİRİNCİL TOPLAM (dönem A, ChopZone YOK): {report['totals']['primary']}"
        f"  |  kesişim barında: {report['totals']['on_cross_bar']}"
    )

    if args.results_json:
        path = Path(args.results_json)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        logger.info("yük yazıldı: %s", path)

    return 0


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Aday 'ölüm kesişimi + geri çekilme' short'unun ölçülebilirlik sayımı "
            "(SALT OKUNUR; getiri/R/PnL hesaplanmaz)."
        )
    )
    parser.add_argument("--config", default=None, help="config.yaml yolu (varsayılan: depo kökü)")
    parser.add_argument(
        "--symbols", nargs="*", default=None, help=f"taranacak semboller (varsayılan: {LAYER} evreninin tamamı)"
    )
    parser.add_argument("--start", default=PERIOD_A_START, help="dönem A başlangıcı (ithal edilir)")
    parser.add_argument(
        "--end", default=PERIOD_A_CUTOFF, help="dönem A kesimi — AŞILAMAZ (aşan değer hata kodu 2)"
    )
    parser.add_argument(
        "--warmup-bars", type=int, default=WARMUP_BARS,
        help="sayımdan ÖNCE gereken bar sayısı (EMA200 tohumu + sönümlenme)",
    )
    parser.add_argument(
        "--min-gap-bars", type=int, default=MIN_GAP_BARS,
        help="BİRİNCİL kurulumlar arası asgari bar (6 bar = 24 saat)",
    )
    parser.add_argument(
        "--history-bars", type=int, default=8000,
        help="çekilecek azami bar (dönem A + ısınma; kesimden İLERİ gidilmez)",
    )
    parser.add_argument("--cache-dir", default=None, help="mum önbelleği (varsayılan: geçici dizin)")
    parser.add_argument("--results-json", default=None, help="makine okunur yük dosyası")
    parser.add_argument(
        "--verify", type=int, default=0, metavar="N",
        help="bar bazlı EMA'yı core/indicators.py::ema ile N rastgele barda karşılaştır",
    )
    parser.add_argument("--verbose", action="store_true")
    return parser.parse_args(argv)


if __name__ == "__main__":
    raise SystemExit(main())

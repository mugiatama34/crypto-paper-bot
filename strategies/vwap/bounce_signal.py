"""**Mod B'nin sinyali: trend gününde VWAP'e DÖNÜŞTE trend yönünde giriş.**

Klasik VWAP masası fade ile bitmez. Denge gününde fiyat VWAP etrafında salınır ve uçtan
karşıya yatmak çalışır (Mod A, `strategies/vwap_scored.py`); TREND gününde aynı kurulum
ölür — fiyat bandın dışında saatlerce kalır ve her dönüş denemesi trendin devamıyla
ezilir. Mod B o günlerde fade AÇMAZ, tersini yapar: fiyat VWAP'e geri geldiğinde trendin
YÖNÜNDE girer.

**Bu bir seçim daraltması DEĞİL, ölü bir kurulumun yerine başkasını koymaktır.** Mod A
zaten ADX tavanının üstünde işlem açmıyor; o barlar Mod A için boş geçiyordu. Mod B tam
olarak orada devreye girer, yani ikisi birbirinin işlemini ÇALMAZ.

**Üç şart birlikte aranır ve üçü de aynı seansın VWAP'inden okunur:**

1. **Trend var:** ADX eşiğin üstünde VE seans VWAP'inin EĞİMİ aynı yönde (σ biriminde).
   Tek başına ADX yön söylemez; tek başına eğim, yatay bir testerede de küçük bir sayı
   üretir. İkisi birlikte "bu seans bir yere gidiyor" der.
2. **Geri çekilme:** bar VWAP'e DEĞDİ (yükselişte barın en düşüğü VWAP bandına indi).
3. **Reddediliş:** bar VWAP'in trend tarafında KAPANDI ve gövdesi trend yönünde
   (yükselişte `close > open`). Değip geri alınmayan bir VWAP, geri çekilme değil
   KIRILMADIR ve bu kolun ölçtüğü şey değildir.

**Stop VWAP'in ÖTESİNDEDİR**, girişin altında sabit bir mesafede değil: tezin çürüdüğü
yer VWAP'in kırılmasıdır ve stop o tezin yanlışlandığı fiyata konur. **Hedef, projeksiyon
ile bir önceki UCUN yakın olanıdır** (ev kuralının aynısı: bilinen bir seviyenin ötesini
hedeflemek, o seviyenin orada olmadığını varsaymak olurdu).

Rollere dikkat: boyut/komisyon/bakiye hesaplanmaz (kural 1/2/3/7), deftere yazılmaz,
rastgelelik kullanılmaz, gösterge matematiği `core/indicators.py`dedir.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Mapping

import pandas as pd

from core.indicators import (
    anchored_vwap,
    average_true_range,
    bars_until,
    session_vwap_series,
)
from strategies.base import Direction

# Kolun ADI — Mod A'nın (`vwap_revert_session`) etiketinden AYRIDIR. İkisi aynı defterde
# DEĞİLDİR (ayrı modeller, ayrı defter) ama kol kırılımı havuzda yan yana okunur ve tek
# bir ad, iki farklı kurulumu aynı kolmuş gibi gösterirdi.
ARM_NAME = "vwap_bounce"

NO_BAR = "bar_yok"
NO_BANDS = "band_yok"          # seans çapası kısa ya da σ/ATR hesaplanamıyor
NO_TREND = "trend_yok"         # ADX ya da VWAP eğimi eşiğin altında
NO_PULLBACK = "geri_cekilme_yok"  # bar VWAP'e hiç değmedi
NO_REJECTION = "red_yok"       # değdi ama trend tarafında kapanmadı (kırılma)
BAD_GEOMETRY = "gecersiz_geometri"
SETUP = "kurulum"

REASONS: tuple[str, ...] = (
    SETUP, NO_TREND, NO_PULLBACK, NO_REJECTION, BAD_GEOMETRY, NO_BANDS, NO_BAR,
)


@dataclass(frozen=True, kw_only=True)
class BounceParams:
    """Mod B'nin sayıları; hepsi `config.yaml > vwap.bounce` altından gelir."""

    min_slope_sigma: float      # seans VWAP'i `slope_bars` barda kaç σ yol aldı
    slope_bars: int
    touch_sigma: float          # VWAP'e "değdi" sayılan bant (σ cinsinden)
    stop_sigma: float           # stop VWAP'in kaç σ ötesinde
    target_reward_risk: float   # hedef PROJEKSİYONU
    extreme_lookback: int       # "bir önceki uç" kaç barda aranır
    min_bars: int               # seans çapasından beri en az bu kadar bar


@dataclass(frozen=True, kw_only=True)
class BounceCandidate:
    """Kurulumun yeri ve seviyeleri. Alan adları `SessionCandidate` ile UYUMLUDUR.

    Uyumluluk bir zarafet değil, ölçümün şartı: Mod A ve Mod B aynı gövdeyi
    (`strategies/vwap_scored.py`) paylaşır ve gövde iki adayı aynı sözleşmeyle okur —
    ikisi ayrışsaydı boyut, dolum ve zaman stop'u iki ayrı uygulamaya bölünürdü.
    """

    symbol: str
    direction: Direction
    entry_price: float
    stop_price: float
    target_price: float
    vwap: float
    std: float
    z: float           # girişin VWAP'e σ cinsinden uzaklığı (bounce'ta küçüktür)
    bars: int
    slope_sigma: float  # seans VWAP'inin eğimi (σ/`slope_bars` bar)

    @property
    def reward_risk(self) -> float:
        risk = abs(self.entry_price - self.stop_price)
        return 0.0 if risk <= 0.0 else abs(self.target_price - self.entry_price) / risk

    def detail(self) -> str:
        return (
            f"VWAP bounce (trend günü): seans VWAP={self.vwap:.6g} ({self.bars} bar), "
            f"σ={self.std:.6g}, eğim {self.slope_sigma:+.2f}σ; fiyat VWAP'e döndü "
            f"({self.z:+.2f}σ) ve {self.direction} yönünde reddedildi"
        )


@dataclass(frozen=True, kw_only=True)
class Survey:
    counts: Mapping[str, int]

    @property
    def examined(self) -> int:
        return sum(self.counts.values())

    def describe(self) -> str:
        reasons = " ".join(
            f"{reason}={self.counts[reason]}" for reason in REASONS if self.counts.get(reason)
        )
        return f"{self.examined} sembol; {reasons or 'sayım yok'}"


def empty_counts() -> dict[str, int]:
    return {reason: 0 for reason in REASONS}


def detect(
    frame: pd.DataFrame | None,
    *,
    symbol: str,
    as_of: pd.Timestamp,
    params: BounceParams,
    atr_period: int,
) -> tuple[BounceCandidate | None, str]:
    """Aday ve ELEME SEBEBİ. Trend YÖNÜ eğimden, gücü çağıranın ADX'inden gelir.

    ADX burada okunmaz: gövde (`strategies/vwap_bounce.py`) onu zaten boyut kademesi için
    hesaplar ve iki yerde hesaplamak, aynı barda iki farklı trend gücü demekti.
    """
    if frame is None or frame.empty:
        return None, NO_BAR
    window = bars_until(frame, as_of)
    if window.empty or window.index[-1] != as_of or len(window) < params.slope_bars + 2:
        return None, NO_BAR

    session = anchored_vwap(window, anchor=as_of.normalize())
    if session is None or session.bars < params.min_bars or session.deviation <= 0.0:
        return None, NO_BANDS
    if not (math.isfinite(session.value) and math.isfinite(session.deviation)):
        return None, NO_BANDS

    series = session_vwap_series(window)
    if len(series) <= params.slope_bars:
        return None, NO_BANDS
    earlier = float(series.iloc[-1 - params.slope_bars])
    if not math.isfinite(earlier):
        return None, NO_BANDS
    slope_sigma = (session.value - earlier) / session.deviation
    if abs(slope_sigma) < params.min_slope_sigma:
        return None, NO_TREND

    direction: Direction = "long" if slope_sigma > 0.0 else "short"
    bar = window.iloc[-1]
    close = float(bar["close"])
    open_ = float(bar["open"])
    touch = params.touch_sigma * session.deviation

    if direction == "long":
        reached = float(bar["low"]) <= session.value + touch
        rejected = close > session.value and close > open_
    else:
        reached = float(bar["high"]) >= session.value - touch
        rejected = close < session.value and close < open_
    if not reached:
        return None, NO_PULLBACK
    if not rejected:
        # Değip geri alınmayan VWAP bir geri çekilme değil KIRILMADIR; bu kolun ölçtüğü
        # şey değildir ve ayrı sayılır.
        return None, NO_REJECTION

    sign = 1.0 if direction == "long" else -1.0
    stop = session.value - sign * params.stop_sigma * session.deviation
    risk = abs(close - stop)
    if risk <= 0.0:
        return None, BAD_GEOMETRY
    projected = close + sign * risk * params.target_reward_risk
    extreme = _previous_extreme(window, direction=direction, lookback=params.extreme_lookback)
    # Önceki uç yalnızca ÖNDEYSE bir engeldir. Fiyat zaten onun ötesindeyse (trendin yeni
    # ucundayız) yolda engel yoktur ve hedef projeksiyondur — `strategies/vwap/signal.py::
    # nearest_target`ın VWAP için kurduğu kuralın aynısı. Aksi hâlde hedef girişin dibine
    # çakılır ve R:R kapısı her kurulumu elerdi.
    ahead = extreme is not None and sign * (extreme - close) > 0.0
    target = projected
    if ahead:
        target = min(projected, extreme) if direction == "long" else max(projected, extreme)
    if not _ordered(direction, stop=stop, entry=close, target=target):
        return None, BAD_GEOMETRY

    return (
        BounceCandidate(
            symbol=symbol,
            direction=direction,
            entry_price=close,
            stop_price=stop,
            target_price=target,
            vwap=session.value,
            std=session.deviation,
            z=(close - session.value) / session.deviation,
            bars=session.bars,
            slope_sigma=slope_sigma,
        ),
        SETUP,
    )


def _previous_extreme(
    window: pd.DataFrame, *, direction: Direction, lookback: int
) -> float | None:
    """Son barı HARİÇ, `lookback` barlık en yüksek zirve / en düşük dip.

    Son bar dışlanır (`core/indicators.py::donchian` ile aynı gerekçe): kurulumun kendi
    barını "önceki uç" saymak, hedefi girişin bir adım ötesine koyup R:R kapısını ölü
    koda çevirirdi.

    Dönen değer bir ENGEL ADAYIDIR, hedefin kendisi değil: çağıran taraf onu yalnızca
    girişin ÖNÜNDE duruyorsa kullanır.
    """
    if len(window) < lookback + 1:
        return None
    body = window.iloc[-(lookback + 1):-1]
    value = float(body["high"].max()) if direction == "long" else float(body["low"].min())
    return value if math.isfinite(value) else None


def _ordered(direction: Direction, *, stop: float, entry: float, target: float) -> bool:
    if not all(math.isfinite(value) for value in (stop, entry, target)):
        return False
    if direction == "long":
        return stop < entry < target
    return target < entry < stop

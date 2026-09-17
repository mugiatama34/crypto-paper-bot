"""**F0'ın sinyali:** kaynak sistemin kuralları, TEK bir birim düzeltmesiyle.

Bu modül `clone_signal.py`nin (model 13) kural kural aynısıdır — bant karşılaştırması,
dönüş şartı, stop ve hedef geometrisi, eleme sebepleri — ve TEK bir yerde ayrışır:

    clone_signal.py (model 13)              session_signal.py (F0)
    VWAP = son 300 barın KÜMÜLATİFİ         VWAP = SEANSIN (UTC gün) VWAP'i
    σ = sapmanın rolling(20) örneklem sd'si σ = seansın HACİM AĞIRLIKLI σ'su

**Neden bu ayrım tek başına bir hipotez.** Kaynağın çapası her barda kayar: "ortalama
işlem maliyeti" hiçbir seansa ait değildir ve σ, 20 barlık bir pencerenin dar
dağılımıdır — bu ikisi birlikte stop'u (band × sl_mult × σ) çok dar bırakır, notional'ı
şişirir ve kopyanın defterinde görülen tabloyu üretir (54 günde 2903 pozisyon, ort.
−0.70R, hesap −%98). Seans çapası ve seans σ'su aynı kuralları GENİŞ bir ölçekte
uygular: daha az kurulum, daha geniş stop, daha düşük ciro.

**Kasıtlı olarak BAŞKA HİÇBİR ŞEY değişmez.** Rejim kapısı, tükenme şartı, hedef/stop
çıtası, stop tabanı, zaman stop'u, sembol elemesi, skorlama — hiçbiri YOKTUR. Gerekçe
karar 45'in dersidir: altı kapıyı aynı anda takmak, 54 günde 8 pozisyon bırakıp ölçümü
imkânsız kıldı. Önce TEK değişkenin etkisi ölçülür; kapılar ancak bu satır okunabilir
olduktan sonra, kendi ön-kayıtlarıyla eklenir (`docs/backtest.md > 6f`, F1/F2).

**Öğrenme de YOKTUR.** Kaynak bant ve hedef çarpanını öğrenir; burada ikisi de SABİTTİR
ve grid'in ORTASINDAN alınır (`band_mult` 2.0, `tp_mult` 0.75). Süpürülmediler: orta
değer, sonucu görmeden seçilebilen tek değerdir. Sabitlemenin sebebi ölçümün kendisidir
— öğrenen bir model, birim değişikliğinin etkisini kendi keşif payıyla karıştırır.

Rollere dikkat: bu modül boyut/komisyon/bakiye hesaplamaz (kural 1/2/3/7), deftere
yazmaz, rastgelelik kullanmaz ve kendi gösterge matematiğini yazmaz.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Mapping

import pandas as pd

from core.indicators import anchored_vwap, bars_until, typical_price
from strategies.base import Direction

# Kolun ADI. Model 13'ün (`vwap_revert_src`) ve model 14'ün (`vwap_revert`) etiketinden
# AYRIDIR: tek bir ad, farklı kural kümelerini aynı kolmuş gibi gösterir ve kol
# kırılımını (core/metrics.py::breakdown) okunamaz kılardı.
ARM_NAME = "vwap_revert_session"

# Eleme sebepleri `clone_signal.py` ile BİREBİR aynıdır ve bilinçli olarak öyledir: iki
# modelin survey satırları ancak aynı sebep kümesiyle yan yana okunabilir.
NO_BAR = "bar_yok"
NO_BANDS = "band_yok"
INSIDE_BAND = "bant_ici"
NO_TURN = "donus_yok"
BAD_GEOMETRY = "gecersiz_geometri"
SETUP = "kurulum"

REASONS: tuple[str, ...] = (SETUP, INSIDE_BAND, NO_TURN, BAD_GEOMETRY, NO_BANDS, NO_BAR)


@dataclass(frozen=True, kw_only=True)
class SessionParams:
    """Sabit çarpanlar. Kaynakta bunlar ÖĞRENİLİR; burada config'ten sabit okunur."""

    band_mult: float
    tp_mult: float
    sl_mult: float


@dataclass(frozen=True, kw_only=True)
class SessionCandidate:
    """Kurulumun YERİ ve SEVİYELERİ — `clone_signal.CloneCandidate` ile aynı sözleşme.

    Stop ve hedef BURADA kurulur çünkü kaynakta geometri kontrolü (`sl < entry < tp`)
    onlara bakar: seviyeler olmadan "bu bir kurulum mu" sorusu cevaplanamaz.
    """

    symbol: str
    direction: Direction
    entry_price: float  # `as_of` kapanışı — dolum bir sonraki barın açılışındadır (kural 13)
    stop_price: float
    target_price: float
    vwap: float
    std: float
    z: float
    bars: int  # seans çapasından beri geçen bar sayısı

    def detail(self) -> str:
        return (
            f"VWAP sapma-dönüş (seans birimi): seans VWAP={self.vwap:.6g} "
            f"({self.bars} bar), σ={self.std:.6g}; bu bar {self.z:+.2f}σ ile bandın "
            f"dışında kapandı ve kapanış {self.direction} yönünde döndü"
        )


@dataclass(frozen=True, kw_only=True)
class Survey:
    """Bir BARDA kolun ne gördüğü: sebep -> sembol sayısı (kural 15'in denetim izi)."""

    counts: Mapping[str, int]

    @property
    def examined(self) -> int:
        return sum(self.counts.values())

    @property
    def candidates(self) -> int:
        return self.counts.get(SETUP, 0)

    def describe(self) -> str:
        reasons = " ".join(
            f"{reason}={self.counts[reason]}"
            for reason in REASONS
            if self.counts.get(reason)
        )
        return f"{self.examined} sembol; {reasons or 'sayım yok'}"


def empty_counts() -> dict[str, int]:
    return {reason: 0 for reason in REASONS}


def detect(
    frame: pd.DataFrame | None,
    *,
    symbol: str,
    as_of: pd.Timestamp,
    params: SessionParams,
    min_bars: int,
) -> tuple[SessionCandidate | None, str]:
    """Aday VE eleme sebebi — `clone_signal.detect` ile aynı akış, farklı ÇAPA.

    `min_bars` burada SEANS barı sayar (kaynakta 300 barlık pencerenin doluluğuydu):
    günün ilk birkaç barında VWAP tek bir mumun etrafındadır ve "kaç σ uzakta" sorusunun
    cevabı yoktur. Sınırın kendisi model 14'ün `min_vwap_bars` değeriyle aynı sayıdır;
    iki farklı sayı, aynı çapanın iki farklı tanımı demekti.
    """
    if frame is None or frame.empty:
        return None, NO_BAR
    window = bars_until(frame, as_of)
    if window.empty or window.index[-1] != as_of or len(window) < 2:
        # Sembol `as_of` barını taşımıyor: o tur evrenin dışındadır (core/data.py aynı
        # kuralı uygular).
        return None, NO_BAR

    session = anchored_vwap(window, anchor=as_of.normalize())
    if session is None or session.bars < int(min_bars) or session.deviation <= 0.0:
        return None, NO_BANDS
    if not (math.isfinite(session.value) and math.isfinite(session.deviation)):
        return None, NO_BANDS

    entry = float(window["close"].iloc[-1])
    previous = float(window["close"].iloc[-2])
    # z TİPİK FİYATTAN okunur, dönüş KAPANIŞTAN — kaynakta da böyledir ve tutarsızlık
    # kasıtlı olarak korunur: burada ölçülen şey çapa/σ birimidir, kaynağın okuma
    # noktalarını "düzeltmek" ikinci bir değişken eklerdi.
    distance = float(typical_price(window).iloc[-1]) - session.value
    z = distance / session.deviation

    if z <= -params.band_mult:
        direction: Direction = "long"
    elif z >= params.band_mult:
        direction = "short"
    else:
        return None, INSIDE_BAND

    turned = entry > previous if direction == "long" else entry < previous
    if not turned:
        return None, NO_TURN

    sign = 1.0 if direction == "long" else -1.0
    stop = entry - sign * session.deviation * params.band_mult * params.sl_mult
    # Hedef VWAP'e olan mesafenin KESRİDİR (`tp_mult ≤ 1`), yani VWAP'i asla aşmaz.
    # VWAP girişin gerisinde kaldıysa mesafe 0'a kırpılır ve geometri kontrolü o
    # kurulumu düşürür — kaynakta "VWAP geçildi" için ayrı bir eleme yoktur.
    gap = max(sign * (session.value - entry), 0.0) * params.tp_mult
    target = entry + sign * gap

    if not _ordered(direction, stop=stop, entry=entry, target=target):
        return None, BAD_GEOMETRY

    return (
        SessionCandidate(
            symbol=symbol,
            direction=direction,
            entry_price=entry,
            stop_price=stop,
            target_price=target,
            vwap=session.value,
            std=session.deviation,
            z=z,
            bars=session.bars,
        ),
        SETUP,
    )


def _ordered(direction: Direction, *, stop: float, entry: float, target: float) -> bool:
    """Kaynağın geometri kontrolü: long'da `sl < entry < tp`, short'ta `tp < entry < sl`.

    Eşitlik GEÇMEZ: hedefi girişe eşit bir kurulum, fiyat hiç hareket etmeden "hedefe
    ulaştı" diye kapanır ve deftere kârmış gibi yazılırdı.
    """
    if not all(math.isfinite(value) for value in (stop, entry, target)):
        return False
    if direction == "long":
        return stop < entry < target
    return target < entry < stop

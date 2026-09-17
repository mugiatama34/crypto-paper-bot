"""MODEL 18'İN (`vwap_guarded`) sinyali: VWAP sapma-dönüşü + ÜÇ REJİM KAPISI.

Bu modülü YALNIZCA model 18 okur. Model 13 (`clone_signal.py`) ve model 14 (`signal.py`)
kendi modüllerini okur ve bu ayrım bilinçlidir (karar 23'ün aynı gerekçesi): üç modül aynı
fikri (VWAP'ten sapıp dönen fiyat) BAŞKA kurallarla ölçer; tek modülde dallanma olarak
yazılsalardı "kapıların katkısı" ekseninin tanımı, o dallanmaların hangi model için açık
olduğuna bağlı bir şeye dönüşürdü. Ortak olan yalnızca `core/indicators.py`
YARDIMCILARIDIR: kural mantığı paylaşılmaz, aritmetik paylaşılır.

**Tez.** Kopyanın (model 13) sinyali üç yerde savunmasızdı ve üçü de bu modülde kapatılır:

1. **Çapa.** Kaynak sistem VWAP'i son 300 barın KÜMÜLATİFİ olarak kurar; çapa her barda
   kayar, yani "ortalama işlem maliyeti" hiçbir seansa ait değildir. Burada çapa GÜNÜN
   (UTC) açılışıdır: VWAP o seansın hacim ağırlıklı ortalama maliyetidir ve sapma o
   seansın kendi hacim ağırlıklı σ'sıyla ölçülür (`core/indicators.py::anchored_vwap`).
2. **Bant.** 1.5σ bu geometride gürültüdür — 15 dakikalık barda fiyat gün içi VWAP'in
   1.5σ dışına sürekli çıkar. Taban 2.5σ'dır ve LONG tarafı DAHA GENİŞ bir bant ister
   (`band.long`): düşen bir piyasada "ucuz" görünen her bar bir dönüş adayı değildir ve
   ölçülen defterde beklenti farkı tam olarak bu tarafta duruyordu.
3. **Rejim.** Ortalamaya dönüş tezinin geçersiz olduğu tek durum TRENDDİR. Üç kapı bunu
   keser: sembolün kendi ADX'i, sembolün EMA eğimi ve BTC'nin saatlik yönü. Üçü de
   "fiyat uzaklaştı" ile "fiyat gidiyor"u ayırmak içindir.

**Dönüş şartı zorunludur ve sapmadan AYRIDIR** (model 14 ile aynı gerekçe): yalnızca
"bant dışında" olmak güçlü bir trendde her barda aynı sinyali üretirdi. Önceki bar bandın
DIŞINDA kapanmış, bu bar VWAP'e doğru bir adım atmış ve HÂLÂ aynı tarafta olmalıdır.

**Tükenme şartı (kural: uçta hacim tükenmesi ya da red mumu).** Dönüşün kendisi bir niyet
beyanıdır; tükenme onun kanıtıdır. İkisinden BİRİ yeterlidir çünkü aynı olayın iki farklı
izidir: satıcı bitmişse ya hacim zirve yapıp söner (klimaks) ya da mum uzun bir fitille
reddedilir. İkisini birden istemek kurulum sayısını ölçülemeyecek kadar düşürürdü
(`acceptance.min_trades` = 30 kapısı bu katmanda zaten zor geçiliyor).

**Kurulumun YERİ burada, stop ve hedef modelde** (model 14 ile aynı ayrım): bu modül
sembol, yön, giriş, ATR, VWAP ve sapmayı verir; stop (sabit ATR katı), hedef (projeksiyon
ile VWAP'in yakın olanı), %1 stop tabanı ve 1.5R kapısı `strategies/vwap_guarded.py`dedir.

**Aday bulunamayan bar da kaydedilir (`Survey`).** Kapı sayısı arttıkça bu zorunlu hâle
gelir: altı ayrı eleme sebebi tek bir "aday yok" sayısına çökerse, hangi kapının modeli
susturduğu ölçülemez — ve o soru tam olarak bu modelin ölçtüğü şeydir. Sayım salt denetim
izidir (kural 15): hangi adayın üretileceğini ve sıralarını HİÇ etkilemez.

Rollere dikkat: bu modül boyut/komisyon/bakiye hesaplamaz (kural 1/2/3/7), deftere yazmaz,
rastgelelik kullanmaz ve kendi gösterge matematiğini yazmaz.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import Collection, Literal, Mapping

import pandas as pd

from core.indicators import (
    adx,
    anchored_vwap,
    average_true_range,
    bars_until,
    ema,
    resample_ohlcv,
)
from strategies.base import Direction, MarketData
from strategies.scalp.arms import SymbolView, symbol_views

logger = logging.getLogger(__name__)

# Kolun ADI. Model 13'ün (`vwap_revert_src`) ve model 14'ün (`vwap_revert`) etiketinden
# AYRIDIR: tek bir ad, üç farklı kural kümesini aynı kolmuş gibi gösterir ve kol kırılımını
# (core/metrics.py::breakdown) okunamaz kılardı.
ARM_NAME = "vwap_revert_guard"

# Bir sembolün o barda neden aday OLAMADIĞI. Sebepler AYRIKTIR: her incelenen sembol tam
# olarak bir tane alır, yani Σcounts = incelenen sembol sayısı.
SETUP = "kurulum"
NO_VWAP = "vwap_yok"              # seans çapası yok/kısa ya da σ ≤ 0
INSIDE_BAND = "bant_ici"          # |z_prev| < yönün bandı
STILL_EXTENDING = "donus_yok"     # bant dışı, hâlâ uzaklaşıyor
CROSSED = "vwap_gecildi"          # dönüş kaçırılmış, fiyat VWAP'in öte yanında
TRENDING = "trend_guclu"          # ADX ya da EMA eğimi: ortalamaya dönüş tezi geçersiz
AGAINST_BTC = "btc_trendine_karsi"  # BTC saatlik yönüne karşı fade
NO_EXHAUSTION = "tukenme_yok"     # uçta ne klimaks hacmi ne red mumu var

REASONS: tuple[str, ...] = (
    SETUP, INSIDE_BAND, STILL_EXTENDING, CROSSED, TRENDING, AGAINST_BTC, NO_EXHAUSTION,
    NO_VWAP,
)

# BTC'nin saatlik yönü. "bilinmiyor" ayrı bir değerdir ve "yatay" ile aynı hücreye
# yazılmaz: yatay = ölçüldü ve trend yok, bilinmiyor = ölçülemedi. İkisini birleştirmek,
# veri boşluğunu bir rejim kararıymış gibi gösterirdi (model 17'nin aynı gerekçesi).
Bias = Literal["up", "down", "flat", "unknown"]


@dataclass(frozen=True, kw_only=True)
class GuardParams:
    """Kapıların tüm sayıları; hepsi `config.yaml > vwap.guarded` altından gelir.

    Tek bir dataclass olmasının nedeni denetlenebilirliktir: kapı sayısı altıdır ve altı
    ayrı argüman, bir gün birinin çağrı yerinde unutulması demekti — unutulan kapı ise
    sessizce açık kalırdı.
    """

    band_long: float
    band_short: float
    min_vwap_bars: int
    adx_period: int
    adx_max: float
    ema_period: int
    slope_bars: int
    max_slope_atr: float
    exhaustion_lookback: int
    climax_mult: float
    rejection_wick_ratio: float

    def band_for(self, direction: Direction) -> float:
        return self.band_long if direction == "long" else self.band_short


@dataclass(frozen=True, kw_only=True)
class GuardedCandidate:
    """Kapılardan GEÇMİŞ bir sapma-dönüş kurulumunun yeri. Stop/hedef henüz YOKTUR."""

    symbol: str
    direction: Direction
    entry_price: float  # `as_of` kapanışı — dolum bir sonraki barın açılışındadır (kural 13)
    atr: float
    vwap: float
    deviation: float
    z_prev: float
    z_now: float
    vwap_bars: int
    adx: float
    exhaustion: str  # hangi tükenme izi tetikledi: klimaks / red mumu
    btc_bias: Bias

    @property
    def extension(self) -> float:
        return abs(self.z_prev)

    def detail(self) -> str:
        return (
            f"VWAP sapma-dönüş (kapılı): seans VWAP={self.vwap:.6g} ({self.vwap_bars} bar), "
            f"σ={self.deviation:.6g}; önceki bar {self.z_prev:+.2f}σ ile bandın dışındaydı, "
            f"bu bar {self.z_now:+.2f}σ'ya döndü; ADX={self.adx:.1f}, "
            f"tükenme={self.exhaustion}, BTC 1s yönü={self.btc_bias}"
        )


@dataclass(frozen=True, kw_only=True)
class Survey:
    """Bir BARDA kolun ne gördüğü: sebep -> sembol sayısı (kural 15'in denetim izi)."""

    counts: Mapping[str, int]
    btc_bias: Bias

    @property
    def examined(self) -> int:
        return sum(self.counts.values())

    @property
    def candidates(self) -> int:
        return self.counts.get(SETUP, 0)

    def report(self) -> dict[str, int]:
        """Tur raporuna düşen sayım (`Strategy.take_survey` sözleşmesi: Mapping[str, int]).

        BTC yönü sayıma KARIŞMAZ: o bir sayaç değil, o barın rejim okumasıdır ve
        sayaçların arasına bir kod olarak sıkıştırmak okuyanın onu da bir sayım sanmasına
        kapı bırakırdı. Yönün kendisi loga ve sinyalin `reason` kuyruğuna yazılır.
        """
        return dict(self.counts)

    def describe(self) -> str:
        reasons = " ".join(
            f"{reason}={self.counts[reason]}"
            for reason in REASONS
            if self.counts.get(reason)
        )
        return f"{self.examined} sembol; {reasons or 'sayım yok'}; BTC 1s yönü={self.btc_bias}"


def empty_counts() -> dict[str, int]:
    """Sıfırlanmış sayaç. Sebep kümesi tek yerde durur ki sayım eksik başlamasın."""
    return {reason: 0 for reason in REASONS}


def btc_bias(
    market: MarketData,
    *,
    timeframe: str,
    ema_period: int,
    slope_bars: int,
    min_slope_atr: float,
    atr_period: int,
) -> Bias:
    """BTC'nin ÜST zaman dilimindeki yönü; ölçülemezse `"unknown"`.

    Neden BTC: bu evrende altcoin'ler BTC ile yüksek korelasyonludur ve "altcoin'i BTC
    trendine karşı fade etme" kuralının referansı zaten projenin çapasıdır
    (`exchange.btc_reference`, `as_of`'u da o belirler). Bir sepet seçmek ağırlık seçmek,
    yani serbest bir parametre açmak demekti.

    Neden üst zaman dilimi: 15 dakikalık bir EMA eğimi tam da fade edilmek istenen
    hareketin kendisidir; kapının sorusu "bu sapma daha büyük bir trendin parçası mı"dır
    ve o soru ancak daha yavaş bir barda sorulabilir. Toplama `resample_ohlcv` ile aynı
    anlık görüntüden yapılır — ikinci bir veri çekimi iki modelin aynı barda farklı veri
    görmesine kapı açardı (kural 5).

    Eşik ATR birimindedir, yüzde değil: sabit bir yüzde eşiği BTC'nin oynaklığı
    değiştiğinde başka bir kapıya dönüşürdü.
    """
    frame = bars_until(market.btc, market.as_of)
    if frame.empty or len(frame.index) < 2:
        return "unknown"
    hourly = resample_ohlcv(frame, timeframe)
    if len(hourly) < ema_period + slope_bars:
        return "unknown"
    now = ema(hourly["close"], ema_period)
    before = ema(hourly["close"].iloc[:-slope_bars], ema_period)
    atr = average_true_range(hourly, atr_period)
    if now is None or before is None or atr is None or atr <= 0.0:
        return "unknown"
    slope = (now - before) / atr
    if slope >= min_slope_atr:
        return "up"
    if slope <= -min_slope_atr:
        return "down"
    return "flat"


def scan(
    market: MarketData,
    *,
    atr_period: int,
    params: GuardParams,
    bias: Bias,
    symbols: Collection[str] | None = None,
) -> tuple[list[GuardedCandidate], Survey]:
    """O barın adayları (GÜÇ SIRASINDA) ve eleme sayımı.

    `bias` dışarıdan verilir, burada hesaplanmaz: bar başına TEK bir BTC okuması olmalıdır
    ve sembol döngüsünün içinde hesaplamak aynı sayıyı 13 kez üretip aralarında sessizce
    ayrışabilecek bir yol açardı.

    Sıra sözlük sırasına bırakılmaz (`strategies/scalp/arms.py::symbol_views` ile aynı
    gerekçe): model tek bir kurulum oynar ve seçimin tekrarlanabilir olması sıranın
    belirli olmasına bağlıdır.
    """
    allowed = None if symbols is None else set(symbols)
    counts = empty_counts()
    candidates: list[GuardedCandidate] = []

    for view in symbol_views(market, atr_period=atr_period):
        if allowed is not None and view.symbol not in allowed:
            continue
        candidate, reason = _evaluate(view, params=params, bias=bias)
        counts[reason] += 1
        if candidate is not None:
            candidates.append(candidate)

    candidates.sort(key=lambda item: (-item.extension, item.symbol))
    return candidates, Survey(counts=counts, btc_bias=bias)


def _evaluate(
    view: SymbolView, *, params: GuardParams, bias: Bias
) -> tuple[GuardedCandidate | None, str]:
    """Tek sembolün değerlendirmesi: aday ve ELEME SEBEBİ.

    Sebep ile birlikte döner çünkü `None` tek başına "neden olmadığını" söylemez ve altı
    kapılı bir modelde tam da ayırt edilmek istenen şey odur.

    Kapı sırası ucuzdan pahalıya DEĞİL, ANLAMA göre dizilidir: önce "kurulum var mı"
    (VWAP, bant, dönüş), sonra "bu kurulum oynanır mı" (rejim, BTC, tükenme). Sıra
    sayımın anlamını belirler — ADX'i en başa koymak, bandın dışına hiç çıkmamış bir
    sembolü "trend güçlü" diye saymak olurdu.
    """
    frame = view.frame
    if len(frame) < 2:
        return None, NO_VWAP
    vwap = anchored_vwap(frame, anchor=view.as_of.normalize())
    if vwap is None or vwap.bars < params.min_vwap_bars or vwap.deviation <= 0.0:
        return None, NO_VWAP

    previous = float(frame["close"].iloc[-2])
    z_prev = (previous - vwap.value) / vwap.deviation
    z_now = (view.close - vwap.value) / vwap.deviation

    direction: Direction = "long" if z_prev < 0.0 else "short"
    band = params.band_for(direction)
    if -band < z_prev < band:
        return None, INSIDE_BAND

    turned = (
        z_prev < z_now < 0.0 if direction == "long" else 0.0 < z_now < z_prev
    )
    if not turned:
        # Bant dışında kalan bar ya hâlâ uzaklaşıyordur ya da VWAP'i geçmiştir; ikisi
        # farklı şeyler söyler (trend ↔ kaçırılmış dönüş) ve tek sayıya indirgemek kolun
        # neyi kaçırdığını gizlerdi.
        crossed = z_now >= 0.0 if direction == "long" else z_now <= 0.0
        return None, CROSSED if crossed else STILL_EXTENDING

    strength = _trend_strength(view, params=params)
    if strength is None or strength.trending:
        # ADX hesaplanamıyorsa da kurulum ALINMAZ. Model 17'nin "veri boşluğunu rejim
        # kararı sayma" kuralının tersi DEĞİL, aynısıdır: orada eşik evrenin medyanıydı
        # ve boşlukta medyan tanımsızdı; burada eşik sabittir ve ölçülemeyen tek şey
        # sembolün kendi trend gücüdür — yani kapının cevaplaması gereken soru
        # cevapsız kalır. Cevapsız bir rejim sorusunda işlem açmak, kapıyı hiç
        # koymamakla aynı şeydir.
        return None, TRENDING

    if _fades_btc(direction, bias):
        return None, AGAINST_BTC

    exhaustion = _exhaustion(view, direction=direction, params=params)
    if exhaustion is None:
        return None, NO_EXHAUSTION

    return (
        GuardedCandidate(
            symbol=view.symbol,
            direction=direction,
            entry_price=view.close,
            atr=view.atr,
            vwap=vwap.value,
            deviation=vwap.deviation,
            z_prev=z_prev,
            z_now=z_now,
            vwap_bars=vwap.bars,
            adx=strength.adx,
            exhaustion=exhaustion,
            btc_bias=bias,
        ),
        SETUP,
    )


@dataclass(frozen=True, kw_only=True)
class TrendStrength:
    adx: float
    slope_atr: float
    trending: bool


def _trend_strength(view: SymbolView, *, params: GuardParams) -> TrendStrength | None:
    """Sembolün kendi trend gücü: ADX ve EMA eğimi. İkisinden BİRİ eşiği aşarsa trend.

    İki ölçü birlikte durur çünkü aynı şeyi ölçmezler: ADX yönden bağımsız bir HAREKET
    GÜCÜDÜR ve yatay bir testere hareketinde düşük kalır; EMA eğimi ise yönlü bir
    SÜRÜKLENMEDİR ve ADX'in henüz yükselmediği yeni bir trendde erken yanar. "Veya"
    olmasının bedeli daha az kurulum, kazancı ise ortalamaya dönüş tezinin geçerli
    olmadığı iki ayrı rejimin de kesilmesidir.

    Eğim ATR birimindedir (yüzde değil): sabit bir yüzde eşiği, sembolün oynaklığı
    değiştiğinde başka bir kapıya dönüşürdü — aynı gerekçe `btc_bias`ta da geçerli.
    """
    frame = view.frame
    strength = adx(frame, params.adx_period)
    if strength is None:
        return None
    closes = frame["close"]
    if len(closes) < params.ema_period + params.slope_bars:
        return None
    now = ema(closes, params.ema_period)
    before = ema(closes.iloc[:-params.slope_bars], params.ema_period)
    if now is None or before is None or view.atr <= 0.0:
        return None
    slope_atr = (now - before) / view.atr
    trending = strength > params.adx_max or abs(slope_atr) > params.max_slope_atr
    return TrendStrength(adx=strength, slope_atr=slope_atr, trending=trending)


def _fades_btc(direction: Direction, bias: Bias) -> bool:
    """Bu kurulum BTC'nin saatlik yönüne KARŞI mı?

    Long = düşüşü fade etmek, yani BTC düşüyorsa akıntıya karşı. Short = yükselişi fade
    etmek, yani BTC yükseliyorsa akıntıya karşı. `flat` ve `unknown` kapıyı AÇMAZ:
    ölçülemeyen bir yönü "karşı" saymak, BTC verisinin bir gün geç gelmesini modelin
    susması hâline getirirdi (sembolün kendi ADX kapısı zaten ayrı ve zorunlu).
    """
    return (direction == "long" and bias == "down") or (
        direction == "short" and bias == "up"
    )


def _exhaustion(
    view: SymbolView, *, direction: Direction, params: GuardParams
) -> str | None:
    """Uçta tükenme izi: klimaks hacmi YA DA red mumu. Hangisi tetiklediyse adı döner.

    İkisinden biri yeterlidir (modül docstring'indeki gerekçe). Sıra sabittir — önce
    klimaks, sonra red mumu — ki aynı bar iki koşulu da sağladığında deftere yazılan
    etiket koşudan koşuya değişmesin.

    "Uç" iki bardır: sapmayı yapan bar (−2) ve dönüşü yapan bar (−1). Red mumu ikisinde
    de aranır çünkü fitil, satıcının/alıcının hangi barda bittiğine göre ikisinden
    birinde oluşur; klimaks ise tanımı gereği SAPMA barına aittir ve dönüş barında hacmin
    SÖNMÜŞ olması istenir — "tükenme" tam olarak budur, tek başına yüksek hacim değil.
    """
    frame = view.frame
    lookback = params.exhaustion_lookback
    if len(frame) < lookback + 2:
        return None
    volumes = frame["volume"].to_numpy(dtype="float64")
    extreme_volume = float(volumes[-2])
    current_volume = float(volumes[-1])
    baseline = float(volumes[-(lookback + 2):-2].mean())
    if (
        baseline > 0.0
        and extreme_volume >= params.climax_mult * baseline
        and current_volume < extreme_volume
    ):
        return "klimaks"
    for index, label in ((-2, "red_mumu_uc"), (-1, "red_mumu_donus")):
        if _rejection(frame.iloc[index], direction=direction, ratio=params.rejection_wick_ratio):
            return label
    return None


def _rejection(bar: pd.Series, *, direction: Direction, ratio: float) -> bool:
    """Red mumu: sapma yönündeki fitil, barın tüm aralığının en az `ratio` kadarı.

    Long kurulumda sapma AŞAĞIDIR, yani aranan alt fitildir (fiyat aşağı itildi ve geri
    alındı); short kurulumda üst fitil. Gövde yönüne bakılmaz: reddin kanıtı fitilin
    uzunluğudur, mumun yeşil ya da kırmızı kapanması değil.
    """
    high = float(bar["high"])
    low = float(bar["low"])
    span = high - low
    if not math.isfinite(span) or span <= 0.0:
        return False
    body_low = min(float(bar["open"]), float(bar["close"]))
    body_high = max(float(bar["open"]), float(bar["close"]))
    wick = (body_low - low) if direction == "long" else (high - body_high)
    return wick >= ratio * span


def stop_price(candidate: GuardedCandidate, *, atr_multiple: float) -> float:
    """Stop: girişin ATR'nin `atr_multiple` katı kadar ALEYHTE tarafında.

    Volatiliteye göre stop, kural 11'in boyutlandırmasıyla birlikte "sabit teminat"ın
    yerini alan şeydir: boyut `risk_per_trade × sermaye / |giriş − stop|` olduğu için
    oynak bir sembolde pozisyon kendiliğinden küçülür, sakin bir sembolde büyür ve
    RİSK her işlemde aynı kalır.
    """
    sign = 1.0 if candidate.direction == "long" else -1.0
    return candidate.entry_price - sign * candidate.atr * atr_multiple


def projected_target(candidate: GuardedCandidate, *, stop: float, reward_risk: float) -> float:
    """Hedef PROJEKSİYONU: stop mesafesinin `reward_risk` katı, girişin lehte tarafında."""
    sign = 1.0 if candidate.direction == "long" else -1.0
    return candidate.entry_price + sign * abs(candidate.entry_price - stop) * reward_risk


def nearest_target(candidate: GuardedCandidate, *, projected: float) -> float:
    """Projeksiyon ile yapısal engelin (seans VWAP'i) YAKIN olanı.

    VWAP girişin gerisinde kaldıysa yolda engel yok demektir ve hedef projeksiyondur;
    önünde duruyorsa hedef odur. Bilinen bir seviyenin ötesini hedeflemek, o seviyenin
    orada olmadığını varsaymak olurdu — ve 1.5R kapısını ölü koda çevirirdi.
    """
    if candidate.direction == "long":
        return projected if candidate.vwap <= candidate.entry_price else min(
            projected, candidate.vwap
        )
    return projected if candidate.vwap >= candidate.entry_price else max(
        projected, candidate.vwap
    )


def reward_risk_of(candidate: GuardedCandidate, *, stop: float, target: float) -> float:
    distance = abs(candidate.entry_price - stop)
    if distance <= 0.0:
        raise ValueError(f"{candidate.symbol}: stop giriş fiyatına eşit, R tanımsız")
    return abs(target - candidate.entry_price) / distance


def stop_distance_pct(candidate: GuardedCandidate, *, stop: float) -> float:
    return abs(candidate.entry_price - stop) / candidate.entry_price

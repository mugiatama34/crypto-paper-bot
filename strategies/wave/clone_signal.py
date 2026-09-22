"""MODEL 21'in sinyali: kaynak sistemin Elliott Wave Dalga-3 kuralları, OLDUĞU GİBİ.

Kaynak `wave_detector.py` (`atr_pct`, `zigzag_pivots`, `detect_wave3_setup`,
`build_signal_levels`) + `main.py::_find_wave_setup` ve `main.py::try_open_position`'ın
geometri kapısı — `klonnist/Hasanwavebot @ fa888b7`. Ön-kayıt: docs/backtest.md > 6h.

**Bu modül yalnızca SİNYAL kuralını taşır.** Boyutlandırma, kaldıraç, maliyet, funding ve
likidasyon EVİN kurallarıdır ve buraya hiç girmez (model 21 bir YARIŞMACIDIR, kopya
değil — §6h > 1). Modül boyut/komisyon/bakiye hesaplamaz (kural 1/2/3/7), deftere yazmaz
ve rastgelelik kullanmaz. Parametreleri (`WaveParams`) çağıran verir; kaynakta da öyledir —
`learner.select(symbol)` her sembol için `_find_wave_setup`ten ÖNCE çağrılır.

**`core/indicators.py::zigzag_pivots` NEDEN KULLANILMIYOR.** Aynı adı taşıyor ama BAŞKA bir
algoritmadır (crypto-scanner deposundan birebir taşınmıştır): trendi `close[0]`dan kurar,
başlangıç ankrajı EKLER, kısa bacakları çift hâlinde ELER ve eşiği kesir olarak alır.
Kaynak sistemin zigzag'ı ise ilk barın orta fiyatından (`(high+low)/2`) başlar, trend
kurulurken zıt taraftaki ekstremi ayrıca izler (`ref_high_idx > ref_low_idx` şartı),
bacak eleme YAPMAZ ve eşiği YÜZDE olarak alır. İkisi aynı barlarda farklı pivot listesi
üretir. Ev fonksiyonunu kullanmak, ölçtüğümüz şeyin kaynak sistem olduğu iddiasını
çürütürdü — bu paketin varlık sebebi tam olarak budur (aynı gerekçe
`strategies/vwap/clone_signal.py`).

**ATR tek tanımdan gelir.** `atr_pct` kendi ATR'sini YAZMAZ,
`core/indicators.average_true_range(..., smoothing="simple")`ı çağırır. Kaynağın ATR'si
(`true_range.rolling(14).mean().iloc[-1]`) 15 bardan uzun her çerçevede bu sayının
BİREBİR aynısıdır (test: `tests/test_wave_clone_signal.py`) — yani parite ikinci bir
uygulama olmadan sağlanır. Yumuşatma `simple`dır ve bu bir tercih değil SPEC uyumudur:
kaynak da düz ortalama kullanıyor, yani karar 46'nın `ema_trend` için gerektirdiği Wilder
tadilatı BURADA UYGULANMAZ. Periyot da projenin tek ATR periyodudur
(`trailing.atr_period` = 14 = kaynağın `period` varsayılanı), yani motorun stop tavanı
kontrolü (kural 14) modelin ölçtüğü ATR ile aynı sayıyı görür.

⚠ **Bilinen TEK aritmetik ayrışma, ısınma ucundadır** (§6h > 3(j)): çerçeve TAM `period`
bar taşıdığında kaynak kısmi bir pencereyle (ilk barın `high−low`u dâhil) bir ATR üretir,
`average_true_range` ise None döner ve burada `vol_pct = 0.0`a düşülür — kaynağın kendi
NaN dalının aynısı. `period`dan AZ barda iki taraf da 0.0 verir. Koşuda pencere 300
bardır, yani dal ulaşılamaz; yine de yazılı durur.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal, Mapping

import pandas as pd

from core.indicators import average_true_range, bars_until
from strategies.base import Direction

# Kolun ADI. Kol kırılımında (core/metrics.py::breakdown) bu etiketle durur ve hiçbir ev
# koluyla paylaşılmaz: tek bir ad, iki farklı kural kümesini aynı kolmuş gibi gösterirdi.
ARM_NAME = "wave3_src"

PivotKind = Literal["H", "L"]

# Bir sembolün o barda neden aday OLAMADIĞI. Sayım bir ölçüm DEĞİL, bir denetim izidir
# (kural 15; `strategies/vwap/clone_signal.py::Survey` ile aynı gerekçe ve aynı statü):
# modelin hiçbir ev kapısı yoktur (§6h > 3(h)), dolayısıyla `signals=0` olan bir tur
# "hiç kurulum yoktu" ile "sinyal modülü sessizce bozuldu"yu aynı görünüme çökertirdi.
# Sebepler kaynağın ELEME NOKTALARIDIR, bizim seçtiğimiz kategoriler değil.
NO_BAR = "bar_yok"                  # sembol o barı taşımıyor (o tur evrende değil)
FEW_PIVOTS = "pivot_az"             # onaylı pivot < 3: son üç pivot okunamıyor
NO_PATTERN = "desen_yok"            # son üç onaylı pivot L-H-L / H-L-H değil
BAD_WAVE1 = "wave1_yok"             # wave1_len <= 0
OVERLAP = "p2_p0_asti"              # p2, p0'ı aştı (BUY: p2<=p0, SELL: p2>=p0)
RETRACE_OUT = "retrace_disi"        # retrace, [retrace_min, retrace_max] dışında
BAD_GEOMETRY = "gecersiz_geometri"  # sl<entry<tp (BUY) / tp<entry<sl (SELL) sağlanmadı
SETUP = "kurulum"                   # aday

REASONS: tuple[str, ...] = (
    SETUP,
    RETRACE_OUT,
    OVERLAP,
    NO_PATTERN,
    BAD_WAVE1,
    BAD_GEOMETRY,
    FEW_PIVOTS,
    NO_BAR,
)


@dataclass(frozen=True, kw_only=True)
class WaveParams:
    """Kaynağın `WaveParams`ı: bandit'in bir kolunun sayıları.

    `deviation_pct` bir yüzde DEĞİL, ATR%'nin KATSAYISIDIR (kaynaktaki docstring'in kendi
    uyarısı): gerçek zigzag eşiği `max(deviation_pct × atr_pct, min_deviation_pct)`tir.
    Adı kaynakta böyle ve burada da böyle korunur — "düzeltmek" iki tarafın aynı sayıyı
    farklı adla taşıması demekti.

    `retrace_min/max` ve `sl_mult` ızgaranın EKSENİ DEĞİLDİR (kaynakta sabitler); burada da
    sabit gelirler ve `config.yaml > wave.clone` bloğundan okunurlar.
    """

    deviation_pct: float
    tp_mult: float
    sl_mult: float
    retrace_min: float
    retrace_max: float


@dataclass(frozen=True, kw_only=True)
class Pivot:
    """Kaynağın `Pivot`ı. `index` POZİSYONDUR (çerçeve içi tamsayı), `time` damgadır.

    İkisi birlikte taşınır çünkü kaynak zigzag'ı pozisyonlarla yürür (`ref_high_idx >
    ref_low_idx` şartı bir POZİSYON karşılaştırmasıdır) ama denetim izi damgayla okunur.
    """

    index: int
    price: float
    kind: PivotKind
    time: pd.Timestamp


@dataclass(frozen=True, kw_only=True)
class Wave3Setup:
    """Kaynağın `detect_wave3_setup` çıktısı: kurulumun YERİ (seviyeler ayrı adımdadır)."""

    direction: Direction
    p0: Pivot
    p1: Pivot
    p2: Pivot
    wave1_len: float
    retrace: float


@dataclass(frozen=True, kw_only=True)
class WaveCandidate:
    """Kurulum + seviyeler: motora verilebilir hâle gelmiş aday."""

    symbol: str
    direction: Direction
    entry_price: float  # `as_of` kapanışı — dolum bir SONRAKİ barın açılışındadır (kural 13)
    stop_price: float
    target_price: float
    setup: Wave3Setup
    atr_pct: float
    deviation_pct: float  # kurulumda fiilen kullanılan zigzag eşiği (yüzde)
    pivots: int           # onaylı pivot sayısı (denetim izi)

    def detail(self) -> str:
        setup = self.setup
        return (
            f"Dalga-3 (kaynak kuralları): p0={setup.p0.price:.6g} p1={setup.p1.price:.6g} "
            f"p2={setup.p2.price:.6g}, dalga1={setup.wave1_len:.6g}, "
            f"dalga2 retrace=%{setup.retrace * 100:.1f}; zigzag eşiği "
            f"%{self.deviation_pct:.3f} (atr%={self.atr_pct:.2f})"
        )


@dataclass(frozen=True, kw_only=True)
class Survey:
    """Bir BARDA kolun ne gördüğü: sebep -> sembol sayısı.

    Her incelenen sembol TAM OLARAK bir sebep alır, yani `examined == Σcounts`. Ayrı bir
    `examined` alanı tutulmaz: iki sayı bir gün ayrışırsa hangisinin doğru olduğu
    bilinemezdi.
    """

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
    """Sıfırlanmış sayaç. Sebep kümesi tek yerde durur ki sayım eksik başlamasın."""
    return {reason: 0 for reason in REASONS}


# --------------------------------------------------------------------------- #
# Kaynağın saf fonksiyonları (wave_detector.py)
# --------------------------------------------------------------------------- #
def atr_pct(frame: pd.DataFrame, period: int) -> float:
    """Kaynağın `atr_pct`i: ATR'nin son kapanışa oranı, YÜZDE olarak.

    Volatilite normalizasyonu kaynağın kendi gerekçesiyle burada: sabit bir yüzdelik
    zigzag hassasiyeti BTC ile bir memecoin arasında adaletsiz çalışır, ATR% ise her
    sembolü kendi son hareketine göre ölçekler.

    ATR, projenin TEK tanımından okunur (modül docstring'i). `None` dönerse — yeterli bar
    yok — kaynağın NaN dalıyla aynı sonuç verilir: **0.0**, yani eşik tabanına düşülür.
    Sıfır ya da negatif kapanışta da 0.0 (kaynakta `last_close <= 0` dalı).
    """
    last_close = float(frame["close"].iloc[-1])
    if last_close <= 0.0:
        return 0.0
    atr = average_true_range(frame, period, smoothing="simple")
    if atr is None or not math.isfinite(atr):
        return 0.0
    return float(atr / last_close * 100.0)


def zigzag_pivots(frame: pd.DataFrame, *, deviation_pct: float) -> list[Pivot]:
    """Kaynağın `zigzag_pivots`i, BİREBİR (eşik karşılaştırmaları ve canlı uç dâhil).

    Kaynağın algoritmasının ev fonksiyonundan ayrıldığı üç nokta korunur:

    1. **Çapa ilk barın ORTA fiyatıdır** (`(high[0] + low[0]) / 2`), kapanışı değil.
    2. **Trend kurulurken zıt taraf ayrıca izlenir.** `ref_high`/`ref_low` bağımsız
       ilerler ve trendin kurulması için ekstremin ZAMAN SIRASI şartı vardır
       (`ref_high_idx > ref_low_idx`): dip zirveden ÖNCE gelmiş olmalı. Bu şart olmadan
       aynı barda hem eşik aşılmış hem yön belirsiz olabilirdi.
    3. **Bacak eleme YOKTUR.** Kısa bacaklar birleştirilmez (ev fonksiyonunun
       `_merge_short_legs`i burada yoktur).

    **Son pivot ONAYSIZDIR** — kaynak onu her koşulda ekler (trend hiç kurulmadıysa bile:
    o zaman `index=0`, fiyat = çapa, tip = "L"). Onayı `detect_wave3_setup` verir ve tam
    olarak o yüzden `pivots[:-1]`e bakar. Look-ahead yok (kural 12): canlı uç bir sonraki
    barda yer değiştirebilir ama GELECEĞİ görmez.

    `deviation_pct` YÜZDEDİR (kaynakta bölen 100'dür); kesir olarak verilirse eşik 100 kat
    küçülür ve her bar pivot olurdu.
    """
    if deviation_pct <= 0.0:
        raise ValueError(f"deviation_pct pozitif olmalı: {deviation_pct}")
    if frame.empty:
        return []

    highs = frame["high"].to_numpy(dtype="float64")
    lows = frame["low"].to_numpy(dtype="float64")
    times = frame.index

    pivots: list[Pivot] = []
    trend: Literal["up", "down"] | None = None
    start_price = (float(highs[0]) + float(lows[0])) / 2.0
    ref_high, ref_low = float(highs[0]), float(lows[0])
    ref_high_index = ref_low_index = 0
    last_price = start_price
    last_index = 0
    last_kind: PivotKind = "L"

    for index in range(1, len(frame)):
        high, low = float(highs[index]), float(lows[index])

        if trend is None:
            if high > ref_high:
                ref_high, ref_high_index = high, index
            if low < ref_low:
                ref_low, ref_low_index = low, index

            if (
                ref_high >= start_price * (1 + deviation_pct / 100.0)
                and ref_high_index > ref_low_index
            ):
                trend = "up"
                pivots.append(
                    Pivot(index=ref_low_index, price=ref_low, kind="L", time=times[ref_low_index])
                )
                last_price, last_index, last_kind = ref_high, ref_high_index, "H"
            elif (
                ref_low <= start_price * (1 - deviation_pct / 100.0)
                and ref_low_index > ref_high_index
            ):
                trend = "down"
                pivots.append(
                    Pivot(
                        index=ref_high_index, price=ref_high, kind="H",
                        time=times[ref_high_index],
                    )
                )
                last_price, last_index, last_kind = ref_low, ref_low_index, "L"
            continue

        if trend == "up":
            if high > last_price:
                last_price, last_index = high, index
            elif low <= last_price * (1 - deviation_pct / 100.0):
                pivots.append(
                    Pivot(index=last_index, price=last_price, kind="H", time=times[last_index])
                )
                trend = "down"
                last_price, last_index, last_kind = low, index, "L"
        else:
            if low < last_price:
                last_price, last_index = low, index
            elif high >= last_price * (1 + deviation_pct / 100.0):
                pivots.append(
                    Pivot(index=last_index, price=last_price, kind="L", time=times[last_index])
                )
                trend = "up"
                last_price, last_index, last_kind = high, index, "H"

    pivots.append(
        Pivot(index=last_index, price=last_price, kind=last_kind, time=times[last_index])
    )
    return pivots


def detect_wave3_setup(
    pivots: list[Pivot], params: WaveParams
) -> tuple[Wave3Setup | None, str]:
    """Kaynağın `detect_wave3_setup`i + eleme SEBEBİ.

    Sebep ile birlikte döner çünkü `None` tek başına "neden olmadığını" söylemez ve tam da
    ayırt edilmek istenen şey odur (karar 34'ün dersi: `momentum_burst` aylarca
    tetikliyor sanıldı, oysa kapı aritmetiği onu imkânsız kılıyordu).

    **Son pivot ATILIR** (`pivots[:-1]`): onaysızdır, yani ters yönde `deviation` kadar
    hareket görmemiştir. Onaysız ucu kurulumun p2'si saymak, henüz teyit edilmemiş bir
    dönüşten işlem açmak olurdu ve kaynak da bunu yapmaz.
    """
    confirmed = pivots[:-1]
    if len(confirmed) < 3:
        return None, FEW_PIVOTS

    p0, p1, p2 = confirmed[-3], confirmed[-2], confirmed[-1]

    if p0.kind == "L" and p1.kind == "H" and p2.kind == "L":
        direction: Direction = "long"
        wave1_len = p1.price - p0.price
        if wave1_len <= 0.0:
            return None, BAD_WAVE1
        retrace = (p1.price - p2.price) / wave1_len
        # Dalga-2, dalga-1'in başlangıcını AŞMAMALIDIR: aşarsa sayım bir Elliott
        # dürtüsü değildir. Kaynaktaki sıra korunur (önce örtüşme, sonra retrace) —
        # sebep sayımı hangi kapının bağladığını bu sıraya göre raporlar.
        if p2.price <= p0.price:
            return None, OVERLAP
    elif p0.kind == "H" and p1.kind == "L" and p2.kind == "H":
        direction = "short"
        wave1_len = p0.price - p1.price
        if wave1_len <= 0.0:
            return None, BAD_WAVE1
        retrace = (p2.price - p1.price) / wave1_len
        if p2.price >= p0.price:
            return None, OVERLAP
    else:
        return None, NO_PATTERN

    if not params.retrace_min <= retrace <= params.retrace_max:
        return None, RETRACE_OUT

    return (
        Wave3Setup(
            direction=direction, p0=p0, p1=p1, p2=p2, wave1_len=wave1_len, retrace=retrace
        ),
        SETUP,
    )


def build_signal_levels(
    setup: Wave3Setup, params: WaveParams, last_close: float
) -> tuple[float, float, float]:
    """Kaynağın `build_signal_levels`i: `(entry, tp, sl)`.

    **Giriş sinyal barının KAPANIŞIDIR, bir pivot değil** — hedef ve stop ise `p2`den
    projekte edilir. Bu asimetri kaynağın kendi kuralıdır ve geometri kapısının
    (`_ordered`) varlık sebebidir: fiyat p2'den bu yana hedefi çoktan geçmiş olabilir.
    """
    p2 = setup.p2.price
    wave1 = setup.wave1_len
    sign = 1.0 if setup.direction == "long" else -1.0
    entry = float(last_close)
    target = p2 + sign * wave1 * params.tp_mult
    stop = p2 - sign * wave1 * params.sl_mult
    return entry, target, stop


# --------------------------------------------------------------------------- #
# Tarama (main.py::_find_wave_setup + try_open_position'ın geometri kapısı)
# --------------------------------------------------------------------------- #
def detect(
    frame: pd.DataFrame | None,
    *,
    symbol: str,
    as_of: pd.Timestamp,
    params: WaveParams,
    atr_period: int,
    window_bars: int,
    min_deviation_pct: float,
) -> tuple[WaveCandidate | None, str]:
    """Kaynağın bir sembol için yaptığı taramanın tamamı: aday VE eleme sebebi.

    **Pencere `window_bars` bardır ve bu bir fidelity şartıdır**, bir derinlik ayarı
    değil: kaynak `fetch_ohlcv(..., limit=300)` ile tam 300 bar alır ve zigzag'ın çıktısı
    pencere UZUNLUĞUNA bağlıdır (çapa pencerenin İLK barıdır — `zigzag_pivots`in 1.
    maddesi). Daha uzun bir pencere vermek, kaynağın hiç görmediği bir pivot listesi
    üretirdi; daha kısası çapayı ileri kaydırırdı. Bu yüzden değer `config.yaml`da durur
    ve harness'ın `--history-bars` derinlik bayrağıyla KARIŞMAZ.
    """
    if frame is None or frame.empty:
        return None, NO_BAR
    window = bars_until(frame, as_of)
    if window.empty or window.index[-1] != as_of:
        # Sembol `as_of` barını taşımıyor: o tur evrenin dışındadır (core/data.py aynı
        # kuralı uygular, kural 12). Kaynakta karşılığı "fetch boş döndü"dür.
        return None, NO_BAR
    window = window.tail(int(window_bars))

    volatility = atr_pct(window, atr_period)
    # Kaynağın tabanı: eşik hiçbir koşulda %0.05'in altına inmez. ATR ölçülemediğinde
    # (volatility == 0.0) eşik tam olarak bu tabandır — kaynakta da böyledir.
    effective_deviation = max(params.deviation_pct * volatility, float(min_deviation_pct))

    pivots = zigzag_pivots(window, deviation_pct=effective_deviation)
    setup, reason = detect_wave3_setup(pivots, params)
    if setup is None:
        return None, reason

    entry, target, stop = build_signal_levels(setup, params, float(window["close"].iloc[-1]))
    if not _ordered(setup.direction, stop=stop, entry=entry, target=target):
        return None, BAD_GEOMETRY

    return (
        WaveCandidate(
            symbol=symbol,
            direction=setup.direction,
            entry_price=entry,
            stop_price=stop,
            target_price=target,
            setup=setup,
            atr_pct=volatility,
            deviation_pct=effective_deviation,
            pivots=len(pivots) - 1,
        ),
        SETUP,
    )


def _ordered(direction: Direction, *, stop: float, entry: float, target: float) -> bool:
    """Kaynağın geometri kapısı: long'da `sl < entry < tp`, short'ta `tp < entry < sl`.

    Eşitlik GEÇMEZ (kaynakta da geçmez): hedefi girişe eşit bir kurulum fiyat hiç hareket
    etmeden "hedefe ulaştı" diye kapanır ve deftere kârmış gibi yazılırdı. Kaynağın kendi
    yorumu bu kapıyı böyle gerekçelendirir — seviyeler p2'den, giriş ise güncel kapanıştan
    geldiği için fiyat hedefi zaten geçmiş olabilir.

    Kapı ayrıca `stop != entry`i garanti eder, yani `core/validate.py`nin sıfıra bölme
    kontrolü bu modelde hiçbir zaman tetiklenmez.
    """
    if not all(math.isfinite(value) for value in (stop, entry, target)):
        return False
    if direction == "long":
        return stop < entry < target
    return target < entry < stop

"""VWAP sapma-dönüş sinyalinin TEK tanımı — modeller 13 ve 14 aynı kopyayı görür.

**Tez.** Gün-çapalı VWAP, günün o ana kadarki hacim ağırlıklı ortalama işlem maliyetidir.
Fiyat ondan kendi hacim ağırlıklı standart sapmasının `band_mult` katı kadar uzaklaştığında
"bugünkü katılımcıların çoğuna göre pahalı/ucuz" bölgeye girmiştir; oradan VWAP'e doğru
DÖNMEYE BAŞLADIĞINDA işlem VWAP yönünde açılır.

**Dönüş şartı sapmanın kendisinden ayrıdır ve zorunludur.** Yalnızca "banttan uzakta"
olmak bir sinyal değildir: güçlü bir trendde fiyat bant dışında saatlerce kalır ve her
barda aynı sinyali üretirdi. Bu yüzden iki bar karşılaştırılır — önceki bar bandın
DIŞINDA kapanmış, bu bar VWAP'e doğru bir adım atmış ve HÂLÂ aynı tarafta olmalıdır.
"Hâlâ aynı tarafta" şartı, VWAP'i çoktan geçmiş bir barı bir dönüş başlangıcı saymayı
engeller: o artık başka bir kurulumdur (VWAP kırılımı), bu kolun ölçtüğü şey değil.

**Ortak olan sinyal, hedef DEĞİL.** Bu modül kurulumun YERİNİ verir (sembol, yön, giriş,
ATR, VWAP, sapma); stop ve hedefi modeller kendi kurallarıyla kurar:

    model 13 (`vwap_clone`)    stop = öğrenilen ATR katı, hedef = öğrenilen R katı
                               (kaynak sistemin kuralı; ev kapıları uygulanmaz)
    model 14 (`vwap_managed`)  stop = sabit ATR katı, hedef = projeksiyon ile VWAP'in
                               YAKIN olanı; %1 stop tabanı ve 1.5R kapısı geçerli

İkisinin hedefi aynı olsaydı model 14'ün 1.5R kapısı ölü koda dönerdi (bkz.
`strategies/scalp/arms.py`'deki aynı gerekçe): sabit bir R katı kapıyı hiçbir zaman
tetiklemez. VWAP'i yapısal engel saymak kapıyı canlı tutar — VWAP 1.5R'den yakınsa
kurulum atlanır, çünkü tezin hedefi zaten VWAP'tir ve oraya kadar olan mesafe maliyeti
karşılamıyordur.

**z hesabı `as_of` VWAP'ine göredir, her bar için yeniden çapalanmış VWAP'e göre değil.**
Soru "önceki bar ŞU ANKİ ortalama maliyetin ne kadar uzağındaydı"dır; her bar için o barın
kendi VWAP'ini kullanmak iki farklı ölçeği karşılaştırmak olurdu. Ayrıca kural 12 ile de
tutarlıdır: kullanılan tüm barlar `as_of` ve öncesindedir.

**Aday BULUNAMAMASI da kaydedilir (`Survey`).** Kol, sinyal üretmediği barlarda hiçbir iz
bırakmasaydı "bugün kurulum yoktu" ile "sinyal modülü sessizce bozuldu" aynı görünürdü —
model 13 gibi hiç kapısı olmayan bir modelde defterdeki boşluk tek başına hangisinin doğru
olduğunu söylemez. Bu yüzden her tarama eleme SEBEPLERİYLE sayılır ve loglanır; kural
15'in "ret sebep koduyla kaydedilir" şartının bu koldaki karşılığıdır. Sayım yalnızca bir
denetim izidir: hangi adayın üretileceğini ve sıralarını hiçbir biçimde etkilemez.

Rollere dikkat: bu modül boyut/komisyon/bakiye hesaplamaz (kural 1/2/3/7), deftere yazmaz
ve kendi gösterge matematiğini yazmaz — VWAP ve ATR `core/indicators.py`'dedir, sembol
görünümleri `strategies/scalp/arms.py`'nin ortak yardımcısından gelir.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import Collection, Mapping, Sequence

from core.indicators import anchored_vwap
from strategies.base import Direction, MarketData
from strategies.scalp.arms import SymbolView, symbol_views

logger = logging.getLogger(__name__)

# Kolun ADI. İki model de aynı etiketi yazar: kol kırılımı (core/metrics.py::arm_of)
# "her işlem bir kola aittir" varsayımına dayanır ve etiketsiz satır TagError üretir.
ARM_NAME = "vwap_revert"


@dataclass(frozen=True, kw_only=True)
class VwapCandidate:
    """Bir sembolde görülen sapma-dönüş kurulumunun YERİ. Stop/hedef henüz YOKTUR."""

    symbol: str
    direction: Direction
    entry_price: float  # `as_of` kapanışı — dolum bir sonraki barın açılışındadır (kural 13)
    atr: float
    vwap: float
    deviation: float
    z_prev: float  # önceki barın VWAP'ten uzaklığı (sapma cinsinden)
    z_now: float   # bu barın uzaklığı; |z_now| < |z_prev| olduğu için dönüş "başlamış"tır
    vwap_bars: int

    @property
    def extension(self) -> float:
        """Kurulumun GÜCÜ: sinyalin doğduğu barın bant dışındaki mesafesi."""
        return abs(self.z_prev)

    def detail(self) -> str:
        return (
            f"VWAP sapma-dönüş: gün-çapalı VWAP={self.vwap:.6g} ({self.vwap_bars} bar), "
            f"sapma={self.deviation:.6g}; önceki bar {self.z_prev:+.2f}σ ile bandın "
            f"dışında kapandı, bu bar {self.z_now:+.2f}σ ile VWAP'e döndü "
            f"(yön {self.direction}, ATR={self.atr:.6g})"
        )


# Bir sembolün o barda neden aday OLAMADIĞI. Sayım bir ölçüm değil, bir DENETİM İZİDİR:
# "bu barda hiç sinyal yok" satırı, sinyal modülünün sessizce bozulduğu bir bardan ayırt
# edilemezse kural 15'in "atlama sessiz olamaz" şartı bu kolda karşılanmamış olur. Model
# 13'ün deftere hiç satır yazmadığı bir gün ile kolun hiç kurulum görmediği bir gün
# birbirinin aynısı görünür ve hangisinin doğru olduğu ancak veriyi elle çekerek anlaşılır.
NO_VWAP = "vwap_yok"             # çapa/sapma yokluğu: "kaç σ uzakta" sorusunun cevabı yok
INSIDE_BAND = "bant_ici"         # önceki bar bandın İÇİNDE kapandı (sapma yetersiz)
STILL_EXTENDING = "donus_yok"    # bant dışı ama dönüş başlamamış (uzaklaşma sürüyor)
CROSSED = "vwap_gecildi"         # bant dışı, ama bu bar VWAP'i çoktan geçmiş
SETUP = "kurulum"                # aday


@dataclass(frozen=True, kw_only=True)
class Survey:
    """Bir BARDA kolun ne gördüğü: sebep -> sembol sayısı, ve VWAP'ten en uzak sembol.

    `max_extension` bandın kendisiyle kıyaslanmak içindir: 0 aday üreten bir barda
    "en uzak sembol 1.9σ'daydı" ile "en uzak sembol 0.4σ'daydı" bambaşka iki durumdur —
    birincisi bandın dar kaldığını, ikincisi piyasanın gerçekten VWAP'e yapışık olduğunu
    söyler. `nan` ise hiçbir sembolde z hesaplanamadı demektir (bkz. NO_VWAP).
    """

    examined: int
    counts: Mapping[str, int]
    furthest_symbol: str | None
    max_extension: float   # görülen en büyük |z_prev|; nan = hiç ölçülemedi

    @property
    def candidates(self) -> int:
        return self.counts.get(SETUP, 0)

    def describe(self) -> str:
        reasons = " ".join(
            f"{reason}={self.counts[reason]}"
            for reason in (SETUP, INSIDE_BAND, STILL_EXTENDING, CROSSED, NO_VWAP)
            if self.counts.get(reason)
        )
        furthest = (
            "en uzak sapma: yok"
            if self.furthest_symbol is None or math.isnan(self.max_extension)
            else f"en uzak sapma: {self.furthest_symbol} {self.max_extension:.2f}σ"
        )
        return f"{self.examined} sembol; {reasons or 'sayım yok'}; {furthest}"


def propose(
    market: MarketData,
    *,
    atr_period: int,
    band_mult: float,
    min_vwap_bars: int,
    symbols: Collection[str] | None = None,
    model: str | None = None,
) -> list[VwapCandidate]:
    """O BARIN sapma-dönüş adayları; GÜÇ SIRASINA göre (eşitlikte sembol adına göre).

    Sıra sözlük sırasına bırakılmaz: iki model de aynı adaylardan tek bir kurulum seçer
    ve seçimin tekrarlanabilir olması sıranın belirli olmasına bağlıdır
    (`strategies/scalp/arms.py::symbol_views` ile aynı gerekçe).

    `symbols` modelin KENDİ evrenidir (None = katmanın tamamı). Kopya model kaynak
    sistemin evrenini taşır; katmanın evreni ondan geniştir ve fazladan bir sembolde
    işlem açmak kopyayı kopya olmaktan çıkarırdı.

    `model` yalnızca LOG ETİKETİDİR ve seçimi hiçbir biçimde etkilemez: aynı bar iki model
    için ayrı ayrı taranır (evrenleri farklı) ve iki sayım satırı birbirinden ayırt
    edilebilmelidir. Sonuç, `scan`in döndürdüğü listenin aynısıdır.
    """
    candidates, survey = scan(
        market,
        atr_period=atr_period,
        band_mult=band_mult,
        min_vwap_bars=min_vwap_bars,
        symbols=symbols,
    )
    logger.info(
        "%s%s %s bandı=%.2fσ -> %s",
        f"{model} " if model else "",
        ARM_NAME,
        market.as_of.isoformat(),
        band_mult,
        survey.describe(),
    )
    return candidates


def scan(
    market: MarketData,
    *,
    atr_period: int,
    band_mult: float,
    min_vwap_bars: int,
    symbols: Collection[str] | None = None,
) -> tuple[list[VwapCandidate], Survey]:
    """`propose`un sessiz hâli: adaylar VE eleme sayımı.

    Ayrı bir fonksiyon olmasının nedeni test edilebilirliktir: sayımın kendisi log
    metninden değil bir değerden okunabilmelidir, yoksa "denetim izi doğru mu" sorusu
    ancak log ayrıştırarak cevaplanabilirdi.
    """
    allowed = None if symbols is None else set(symbols)
    candidates: list[VwapCandidate] = []
    counts: dict[str, int] = {
        SETUP: 0, INSIDE_BAND: 0, STILL_EXTENDING: 0, CROSSED: 0, NO_VWAP: 0
    }
    examined = 0
    furthest_symbol: str | None = None
    max_extension = float("nan")

    for view in symbol_views(market, atr_period=atr_period):
        if allowed is not None and view.symbol not in allowed:
            continue
        examined += 1
        candidate, reason, extension = _evaluate(
            view, band_mult=band_mult, min_vwap_bars=min_vwap_bars
        )
        counts[reason] += 1
        if candidate is not None:
            candidates.append(candidate)
        if extension is not None and (
            math.isnan(max_extension) or extension > max_extension
        ):
            furthest_symbol, max_extension = view.symbol, extension

    candidates.sort(key=lambda item: (-item.extension, item.symbol))
    survey = Survey(
        examined=examined,
        counts=counts,
        furthest_symbol=furthest_symbol,
        max_extension=max_extension,
    )
    return candidates, survey


def _evaluate(
    view: SymbolView, *, band_mult: float, min_vwap_bars: int
) -> tuple[VwapCandidate | None, str, float | None]:
    """Tek sembolün değerlendirmesi: aday, ELEME SEBEBİ ve ölçülen |z_prev|.

    Sebep ile birlikte döner, çünkü `None` tek başına "neden olmadığını" söylemez ve tam
    da ayırt edilmek istenen şey odur (bkz. sebep sabitlerinin üstündeki gerekçe).
    """
    if len(view.frame) < 2:
        return None, NO_VWAP, None
    vwap = anchored_vwap(view.frame, anchor=view.as_of.normalize())
    if vwap is None or vwap.bars < min_vwap_bars or vwap.deviation <= 0.0:
        # Sapması sıfır olan (ya da gün başı henüz birkaç barlık) bir pencerede "kaç σ
        # uzakta" sorusunun cevabı yoktur; sıfıra bölmek yerine kurulum kurulmaz.
        return None, NO_VWAP, None

    previous = float(view.frame["close"].iloc[-2])
    z_prev = (previous - vwap.value) / vwap.deviation
    z_now = (view.close - vwap.value) / vwap.deviation
    extension = abs(z_prev)

    if -band_mult < z_prev < band_mult:
        return None, INSIDE_BAND, extension

    direction: Direction | None = None
    if z_prev <= -band_mult and z_prev < z_now < 0.0:
        direction = "long"
    elif z_prev >= band_mult and 0.0 < z_now < z_prev:
        direction = "short"
    if direction is None:
        # Bant dışında kalan bar ya hâlâ uzaklaşıyordur ya da VWAP'i geçmiştir; ikisi
        # farklı şeyler söyler (birincisi trend, ikincisi kaçırılmış dönüş) ve tek bir
        # "aday değil" sayısına indirgemek kolun neyi kaçırdığını gizlerdi.
        crossed = z_now >= 0.0 if z_prev < 0.0 else z_now <= 0.0
        return None, CROSSED if crossed else STILL_EXTENDING, extension

    return (
        VwapCandidate(
            symbol=view.symbol,
            direction=direction,
            entry_price=view.close,
            atr=view.atr,
            vwap=vwap.value,
            deviation=vwap.deviation,
            z_prev=z_prev,
            z_now=z_now,
            vwap_bars=vwap.bars,
        ),
        SETUP,
        extension,
    )


def stop_price(candidate: VwapCandidate, *, atr_multiple: float) -> float:
    """Stop: girişin ATR'nin `atr_multiple` katı kadar ALEYHTE tarafında.

    Mesafe her iki modelde de aynı formülle kurulur; farklı olan yalnızca çarpanın
    NEREDEN geldiğidir (model 13 öğrenir, model 14 config'ten okur). Formülü tek yerde
    tutmak, iki modelin stop mesafesini — yani maliyet ölçeğini, kural 14 — aynı tanıma
    bağlar.
    """
    sign = 1.0 if candidate.direction == "long" else -1.0
    return candidate.entry_price - sign * candidate.atr * atr_multiple


def projected_target(candidate: VwapCandidate, *, stop: float, reward_risk: float) -> float:
    """Hedef PROJEKSİYONU: stop mesafesinin `reward_risk` katı, girişin lehte tarafında."""
    sign = 1.0 if candidate.direction == "long" else -1.0
    return candidate.entry_price + sign * abs(candidate.entry_price - stop) * reward_risk


def nearest_target(candidate: VwapCandidate, *, projected: float) -> float:
    """Projeksiyon ile yapısal engelin (VWAP) YAKIN olanı.

    VWAP girişin gerisinde kaldıysa (fiyat dönüşü çoktan tamamlamış) yolda engel yok
    demektir ve hedef projeksiyondur. Önünde duruyorsa hedef odur: bilinen bir seviyenin
    ötesini hedeflemek, o seviyenin orada olmadığını varsaymak olurdu.
    """
    sign = 1.0 if candidate.direction == "long" else -1.0
    ahead = (candidate.vwap - candidate.entry_price) * sign > 0.0
    if not ahead:
        return projected
    return min(projected, candidate.vwap) if candidate.direction == "long" else max(
        projected, candidate.vwap
    )


def reward_risk_of(candidate: VwapCandidate, *, stop: float, target: float) -> float:
    distance = abs(candidate.entry_price - stop)
    if distance <= 0.0:
        raise ValueError(f"{candidate.symbol}: stop giriş fiyatına eşit, R tanımsız")
    return abs(target - candidate.entry_price) / distance


def stop_distance_pct(candidate: VwapCandidate, *, stop: float) -> float:
    return abs(candidate.entry_price - stop) / candidate.entry_price


def strongest(candidates: Sequence[VwapCandidate]) -> VwapCandidate | None:
    """Turda oynanacak tek aday: en uzağa sapmış olan (liste zaten o sırada)."""
    return candidates[0] if candidates else None

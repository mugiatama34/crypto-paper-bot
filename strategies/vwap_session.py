"""**F0 — kopyanın (model 13) BİRİMİ düzeltilmiş hâli. Tek değişken, başka filtre yok.**

Karar 45 iki şey öğretti ve bu model ikisinin de cevabıdır:

1. **Altı kapıyı aynı anda takmak ölçümü öldürür.** `vwap_guarded` (model 18) 54 günde
   8 pozisyon üretti; n=30 kapısına ~300 günde ulaşırdı. Bir kapı kümesi, tek tek
   ölçülmeden takılmaz.
2. **"2.5σ" bir sayı değil, bir BİRİMDİR.** Aynı sayı iki farklı σ tahmincisinde iki
   farklı eşiktir.

Bu yüzden F0 yalnızca BİRİMİ değiştirir ve başka hiçbir şeye dokunmaz:

| | model 13 (kopya) | F0 (`vwap_session`) |
|---|---|---|
| VWAP çapası | son 300 barın kümülatifi (her barda kayar) | **SEANS (UTC gün)** |
| σ | sapmanın `rolling(20)` örneklem sd'si | **seansın hacim ağırlıklı σ'su** |
| bant / hedef çarpanı | ÖĞRENİLİR (3×3 grid) | **SABİT: 2.0 / 0.75** (grid'in ortası) |
| geri kalan her şey | — | **AYNI** |

"Geri kalan her şey" şunları içerir ve hiçbiri tartışmaya açılmadı: dönüş şartı
(`close > prev_close`), stop (`band × sl_mult × σ`), hedef (VWAP mesafesinin kesri),
sabit teminat × 10x boyutlandırma, kendi limitleri (5 pozisyon / yönde 3 / portföy
riski %8), üç aşamalı çıkış yönetimi, bar başına kotaya kadar sinyal, evren sırasıyla
tarama ve **hiçbir ev kapısı yok** (stop tabanı, 1.5R, zaman stop'u UYGULANMAZ).

**Öğrenmenin kaldırılması bir SADELEŞTİRME değil, ölçümün şartıdır.** Öğrenen bir model
birim değişikliğinin etkisini kendi keşif payıyla karıştırır; iki koşu arasındaki fark
"birim mi, çekiliş mi" diye sorulamaz hâle gelir. Çarpanlar grid'in ORTASINDAN alınır
(2.0 ve 0.75) — süpürülmediler, çünkü orta değer sonucu görmeden seçilebilen tek
değerdir.

**Neden `is_replica = True`.** Bu model dış bir sistemin SADIK kopyası değildir (o hâlâ
yalnızca model 13'tür) ama bayrağın işlevsel anlamını taşır: **1R'si sabit teminattan
gelir, yarışmacılarınkiyle aynı birim değildir.** Bayrağın bütün sonuçları bu tek
olgudan çıkar — ortalama R sıralamasına girmemesi, maliyet ölçeği kolonlarında `nan`
alması, stop bandı medyanına katılmaması ve `ModelLimits` bildirebilmesi. Sabit teminatla
koşan bir satırı yarışmacılarla aynı sütuna koymak, farklı paydaya sahip iki oranı
kıyaslanabilirmiş gibi sunardı (kural 15b'nin kendi gerekçesi). Tabloda kopyanın yanında,
**REFERANS (dış sistem)** bölümünde durur ve 13 ↔ F0 farkı orada okunur.

Boyutlandırmanın da değiştiği sürüm F1'dir ve AYRI bir ön-kayıtla gelir
(`docs/backtest.md > 6f`): risk boyutlandırma, 5x, maker dolum, skor ve zaman stop'u
oraya aittir. F0'ın işi tek bir soruyu cevaplamaktır: *birim düzeltmesi tek başına ne
yapıyor?*

Rollere dikkat: bu modül boyut/komisyon/bakiye hesaplamaz (kural 1/2/3/7) ve deftere
yazmaz (kural 1). Gördüğü tek geçmiş yoktur — `observe_closed_trades` UYGULANMAZ, yani
uyarlanabilir değildir ve Kapı 0'da sinyalleri birebir eşleşmelidir.
"""

from __future__ import annotations

import logging
from typing import Any, Mapping

from core.config import get_setting, load_config
from core.tags import format_tags
from strategies.base import (
    Direction,
    MarketData,
    ModelLimits,
    Signal,
    Strategy,
    TakeProfit,
)
from strategies.exit_management import ExitManagement
from strategies.vwap import session_signal

logger = logging.getLogger(__name__)

CONFIG_PREFIX = "vwap.session"


class VwapSession(Strategy):
    name = "vwap_session"
    allowed_directions: list[Direction] = ["long", "short"]
    is_replica = True

    def __init__(self, *, config: Mapping[str, Any] | None = None) -> None:
        settings = dict(config) if config is not None else load_config()
        self._params = session_signal.SessionParams(
            band_mult=float(get_setting(settings, f"{CONFIG_PREFIX}.band_mult")),
            tp_mult=float(get_setting(settings, f"{CONFIG_PREFIX}.tp_mult")),
            sl_mult=float(get_setting(settings, f"{CONFIG_PREFIX}.sl_mult")),
        )
        self._min_bars = int(get_setting(settings, f"{CONFIG_PREFIX}.min_bars"))
        self._notional_fraction = float(
            get_setting(settings, f"{CONFIG_PREFIX}.notional_fraction")
        )
        self._exit = ExitManagement.from_config(settings)
        if self._params.band_mult <= 0.0 or self._params.sl_mult <= 0.0:
            raise ValueError(f"{CONFIG_PREFIX}: band_mult ve sl_mult pozitif olmalı")
        if not 0.0 < self._params.tp_mult <= 1.0:
            raise ValueError(
                f"{CONFIG_PREFIX}.tp_mult (0, 1] aralığında olmalı — hedef VWAP'i "
                f"aşamaz: {self._params.tp_mult}"
            )
        self.limits = ModelLimits(
            max_positions=int(get_setting(settings, f"{CONFIG_PREFIX}.max_positions")),
            max_per_direction=int(
                get_setting(settings, f"{CONFIG_PREFIX}.max_per_direction")
            ),
            max_portfolio_risk=float(
                get_setting(settings, f"{CONFIG_PREFIX}.max_portfolio_risk")
            ),
            leverage=float(get_setting(settings, f"{CONFIG_PREFIX}.leverage")),
        )
        self._survey: session_signal.Survey | None = None

    def generate_signals(
        self,
        market: MarketData,
        peer_signals: Mapping[str, tuple[Signal, ...]] | None = None,
    ) -> list[Signal]:
        """Evreni ADI SIRASIYLA tarar; kotaya kadar sinyal üretir (kaynağın davranışı).

        Evren KATMANIN evrenidir, kaynağın 12 sembolü değil: F0 bir kopya değildir ve
        kopyanın evrenini taşımak, birim ekseninin yanına ikinci bir değişken
        (hangi semboller) koyardı. Sıralama güce göre YAPILMAZ — kaynakta da yoktur ve
        burada da eklenmez; liste sırası sembol adıdır, yani tekrarlanabilirdir.

        Liste modelin kendi pozisyon kotasıyla kırpılır: kotanın ötesindeki her emir bir
        sonraki barda zaten `max_positions` koduyla reddedilir ve tur raporunu gerçek
        olmayan retlerle doldururdu.
        """
        counts = session_signal.empty_counts()
        signals: list[Signal] = []

        for symbol in sorted(market.ohlcv):
            candidate, reason = session_signal.detect(
                market.ohlcv.get(symbol),
                symbol=symbol,
                as_of=market.as_of,
                params=self._params,
                min_bars=self._min_bars,
            )
            counts[reason] += 1
            if candidate is not None:
                signals.append(self._signal(candidate))

        survey = session_signal.Survey(counts=counts)
        self._survey = survey
        logger.info(
            "%s %s %s bant=%.2fσ -> %s",
            self.name, session_signal.ARM_NAME, market.as_of.isoformat(),
            self._params.band_mult, survey.describe(),
        )

        limit = self.limits.max_positions if self.limits and self.limits.max_positions else len(signals)
        return signals[:limit]

    def take_survey(self) -> Mapping[str, int] | None:
        """Son taramanın eleme sayımı (kural 15). Sinyalleri hiçbir biçimde etkilemez."""
        return None if self._survey is None else dict(self._survey.counts)

    def _signal(self, candidate: session_signal.SessionCandidate) -> Signal:
        return Signal(
            symbol=candidate.symbol,
            direction=candidate.direction,
            # Kaynağın boyutlandırması, olduğu gibi: sabit teminat × sabit kaldıraç.
            sizing="notional_fraction",
            notional_fraction=self._notional_fraction,
            stop_price=candidate.stop_price,
            take_profits=(TakeProfit(price=candidate.target_price, fraction=1.0),),
            **self._exit.signal_fields(),
            reason=format_tags(
                f"{candidate.detail()}; stop {self._params.band_mult:g}×"
                f"{self._params.sl_mult:g}σ ({candidate.stop_price:.6g}), hedef VWAP "
                f"mesafesinin %{self._params.tp_mult * 100:g}'i "
                f"({candidate.target_price:.6g}); {self._exit.describe()}",
                arm=session_signal.ARM_NAME,
                z=candidate.z,
            ),
        )

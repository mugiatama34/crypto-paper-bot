"""`meanrev`in EŞLENMİŞ kontrolü: bilgisiz giriş + `meanrev`in çıkış geometrisi (docs/backtest.md > 6n).

Cevapladığı soru tek: *`meanrev`in RSI + Bollinger aşırılık girişi, AYNI stop ve AYNI hedef
kuralıyla rastgele açılmış işlemlerden ayırt edilebilir mi?* Stop `meanrev`inkinin aynısıdır
(2×ATR); hedef Bollinger(20, 2) orta bandından türer ve TEK dilimdir (`fraction = 1.0`).

**Hedefin MESAFESİ korunur, TARAFI yönden gelir** (`reflect` deseni, §6i > 2 — §6n > 3):
`meanrev` hedefi orta bandın kendisine koyar; rastgele bir girişte orta bant kabaca yarı
yarıya TERS tarafta kalır. Orada `meanrev`in davranışı (hedefsiz açılış) kontrole SANSÜRÜ
geri getirirdi; "hedefi yalnızca bant doğru taraftayken aç" ise yönü ortalamaya dönüşe
bağlar, yani kontrolü `meanrev`in tezine çevirirdi. Bu yüzden hedef
`kapanış ± |SMA20 − kapanış|` yönün tarafına konur. `|SMA20 − kapanış| = 0` ise hedef
kurulamaz ve sembol o barda UYGUN DEĞİLDİR (kurulabilirlik, görüş değil).

**KABUL EDİLEN SAPMALAR** (§6n > 9): hedef mesafesinin dağılımı `meanrev`inkiyle aynı
değildir (rastgele barlarda banda uzaklık küçüktür) ve yakın hedef kural 13'ün "aynı barda
stop+hedef → stop" varsayımına daha sık düşer — ikisi de ön-kayıtlıdır.

Sayılar `strategies/meanrev.py`nin KENDİ sabitlerinden okunur. `meanrev`in BTC rejim kapısı
TAŞINMAZ (kontrol bir strateji olurdu). Giriş kuralı `strategies/random_entry.py`dedir.
"""

from __future__ import annotations

import logging
from typing import Any

import pandas as pd

from core.indicators import bollinger
from strategies.base import Direction, MarketData, Signal, TakeProfit
from strategies.meanrev import BOLLINGER_PERIOD, BOLLINGER_STD, STOP_ATR_MULTIPLE
from strategies.random_entry import RandomEntryControl

logger = logging.getLogger(__name__)


class MeanrevRandom(RandomEntryControl):
    name = "meanrev_random"

    def _setup(self, symbol: str, frame: pd.DataFrame, close: float) -> float | None:
        """Orta banda UZAKLIK; bant hesaplanamıyorsa ya da uzaklık sıfırsa kurulamaz."""
        bands = bollinger(frame["close"], BOLLINGER_PERIOD, BOLLINGER_STD)
        if bands is None:
            return None
        distance = abs(bands.middle - close)
        if distance <= 0.0:
            logger.info(
                "%s %s: kapanış orta banda eşit, hedef mesafesi sıfır — kurulamaz",
                self.name, symbol,
            )
            return None
        return distance

    def _signal(
        self,
        market: MarketData,
        *,
        symbol: str,
        direction: Direction,
        close: float,
        atr: float,
        setup: Any,
        eligible_count: int,
    ) -> Signal:
        distance = atr * STOP_ATR_MULTIPLE
        target_distance = float(setup)
        if direction == "long":
            stop_price, target = close - distance, close + target_distance
        else:
            stop_price, target = close + distance, close - target_distance
        return Signal(
            symbol=symbol,
            direction=direction,
            stop_price=stop_price,
            take_profits=(TakeProfit(price=target, fraction=1.0),),
            reason=(
                self._draw_note(market, symbol=symbol, direction=direction,
                                eligible_count=eligible_count)
                + f"; `meanrev` geometrisi: stop {STOP_ATR_MULTIPLE:g}×ATR({self._atr_period})="
                f"{distance:.6g} uzakta ({stop_price:.6g}), hedef orta banda uzaklık "
                f"{target_distance:.6g} yön tarafında ({target:.6g})"
            ),
        )

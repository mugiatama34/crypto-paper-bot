"""Model 27 (`daily` katmanı): günlük 20 bar Donchian kırılımı, YALNIZCA long, 3×ATR Chandelier.

Kaynağı bu deponun dışındadır: `mugiatama34/trading-premium > arastirma/trend/` (PR #5). Orada
18 aday (N ∈ {20, 50, 100} × k ∈ {2, 3, 4} × {iki yön, yalnızca long}) yalnızca 2024-06
öncesi Binance verisinde tarandı ve bu kural seçildi; örneklem dışında (2024-06 → 2026-09)
o projenin devam eşiklerini geçen İLK model oldu. Bu katman onun ileriye dönük (paper)
ölçümüdür, bir backtest tekrarı değil.

Kural tek cümledir: günlük kapanış, son 20 günün (o gün HARİÇ, `core.indicators.donchian`)
tepesinin üstündeyse ertesi günün açılışında long. Rejim filtresi, hedef ve zaman stop'u
YOKTUR — kaynak kuralda yoklar ve eklemek ölçülen şeyi değiştirirdi.

**Kaynaktan iki bilinçli sapma** (ikisi de motorun ortak kuralıdır, model değiştiremez):
1. ATR periyodu 14'tür (`trailing.atr_period`), kaynakta 20.
2. Trailing, motorun Chandelier kuralıdır (`zirve − 3×ATR`); kaynakta `kapanış − 3×ATR`.
Bu varyant kaynakta AYRICA koşuldu (aynı veri, aynı maliyetler): örneklem dışı +0.22R/işlem,
PF 1.62 (açık pozisyonlar stoplarından kapanmış sayılırsa +0.17R). Rakamlar seçim için
değil beklenti için yazılıdır; bkz. docs/backtest.md > 6y.

Boyut, komisyon, funding ve trailing'in uygulaması `core/`dedir (kural 1/2/3/9).
"""

from __future__ import annotations

import logging
from typing import Any, Mapping

from core.config import get_setting, load_config
from core.indicators import average_true_range, bars_until, donchian
from strategies.base import Direction, MarketData, Signal, Strategy

logger = logging.getLogger(__name__)

DONCHIAN_PERIOD = 20
STOP_ATR_MULTIPLE = 3.0
TRAILING_ATR_MULTIPLE = 3.0


class DailyTrend(Strategy):
    name = "daily_trend"
    allowed_directions: list[Direction] = ["long"]

    def __init__(self, *, config: Mapping[str, Any] | None = None) -> None:
        settings = dict(config) if config is not None else load_config()
        self._atr_period = int(get_setting(settings, "trailing.atr_period"))

    def generate_signals(
        self,
        market: MarketData,
        peer_signals: Mapping[str, tuple[Signal, ...]] | None = None,
    ) -> list[Signal]:
        signals: list[Signal] = []
        # Sıralı gezinti: kota dolduğunda hangi sembolün girdiği veri katmanının sırasına
        # bağlı kalmasın (`trend`in aynı gerekçesi).
        for symbol in sorted(market.ohlcv):
            signal = self._evaluate(symbol, market)
            if signal is not None:
                signals.append(signal)
        return signals

    def _evaluate(self, symbol: str, market: MarketData) -> Signal | None:
        frame = bars_until(market.ohlcv[symbol], market.as_of)
        if frame.empty or frame.index[-1] != market.as_of:
            return None

        channel = donchian(frame, DONCHIAN_PERIOD)
        if channel is None:
            return None
        close = float(frame["close"].iloc[-1])
        if close <= channel.upper:
            return None

        atr = average_true_range(frame, self._atr_period)
        if atr is None or atr <= 0.0:
            logger.info(
                "%s %s: kırılım atlandı, ATR(%d) hesaplanamadı",
                self.name, symbol, self._atr_period,
            )
            return None

        distance = atr * STOP_ATR_MULTIPLE
        stop_price = close - distance
        return Signal(
            symbol=symbol,
            direction="long",
            stop_price=stop_price,
            trailing_atr=TRAILING_ATR_MULTIPLE,
            reason=(
                f"{DONCHIAN_PERIOD} günlük Donchian tepesi {channel.upper:.6g}, kapanış {close:.6g} "
                f"ile %{(close - channel.upper) / channel.upper * 100.0:.2f} üstünde; "
                f"stop {STOP_ATR_MULTIPLE:g}×ATR({self._atr_period})={distance:.6g} uzakta "
                f"({stop_price:.6g}), trailing {TRAILING_ATR_MULTIPLE:g}×ATR"
            ),
        )

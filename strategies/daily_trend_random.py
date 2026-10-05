"""Model 28 — `daily_trend`in EŞLENMİŞ kontrolü (`daily` katmanı, yarışmacı).

Cevapladığı soru tek: *20 günlük kırılım girişi, AYNI stop ve AYNI trailing ile rastgele
açılmış long işlemlerden ayırt edilebilir mi?* Kaynak backtest'te bu sorunun cevabı
sınırdaydı (p = 0.045): kazancın bir kısmı girişten değil "kaybı kes, kazancı koştur"
çıkışından geliyordu. Bu yüzden kontrol katmanın İÇİNDE koşar.

Giriş `strategies/random_entry.py`dir (barda tek çekiliş, uygunluk = kurulabilirlik).
Ayrışan iki nokta: çıkış geometrisi `daily_trend`in KENDİ sabitlerinden okunur ve yön
yalnızca long'dur — model yalnızca long açtığı için yarı yarıya short açan bir kontrol,
farkı girişin değil yönün ölçüsü yapardı. Tasarımı bozulamaz: kırılım kapısını taşımaz.
"""

from __future__ import annotations

from typing import Any

from strategies.base import Direction, MarketData, Signal
from strategies.daily_trend import STOP_ATR_MULTIPLE, TRAILING_ATR_MULTIPLE
from strategies.random_entry import RandomEntryControl


class DailyTrendRandom(RandomEntryControl):
    name = "daily_trend_random"
    allowed_directions: list[Direction] = ["long"]
    directions: tuple[Direction, ...] = ("long",)

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
        return Signal(
            symbol=symbol,
            direction=direction,
            stop_price=close - distance,
            trailing_atr=TRAILING_ATR_MULTIPLE,
            reason=(
                self._draw_note(market, symbol=symbol, direction=direction,
                                eligible_count=eligible_count)
                + f"; `daily_trend` geometrisi: stop {STOP_ATR_MULTIPLE:g}×ATR({self._atr_period})="
                f"{distance:.6g} uzakta ({close - distance:.6g}), trailing {TRAILING_ATR_MULTIPLE:g}×ATR"
            ),
        )

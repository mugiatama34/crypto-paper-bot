"""`trend`in EŞLENMİŞ kontrolü: bilgisiz giriş + `trend`in çıkış geometrisi (docs/backtest.md > 6n).

Cevapladığı soru tek: *`trend`in Donchian kırılımı + EMA rejim girişi, AYNI stop ve AYNI
trailing ile rastgele açılmış işlemlerden ayırt edilebilir mi?* `random_ctrl` bu soruyu
soramıyordu: yalnızca stop'la kapanıyordu ve kapanmış-işlem R'si sansürlüydü (karar 60).
Burada çıkış `trend`inkinin aynısıdır — 2×ATR ilk stop + 1×ATR trailing (motorun Chandelier
kuralı, kural 9) — ve trailing her pozisyonu sonlu sürede kapatır.

Sayılar `strategies/trend.py`nin KENDİ sabitlerinden okunur, kopyalanmaz: ikinci bir kopya
bir gün sessizce ayrışır ve fark "girişin ölçüsü" olmaktan çıkardı.

**Tasarımı bozulamaz** (`random_ctrl`in sözü): `trend`in rejim ya da kırılım kapısını
taşımaz — taşısaydı kontrol olmazdı. Giriş kuralı `strategies/random_entry.py`dedir.
Statü YARIŞMACI (kontrol grubu): boyut, maliyet ve limitler yarışmacılarla birebir.
"""

from __future__ import annotations

from typing import Any

from strategies.base import Direction, MarketData, Signal
from strategies.random_entry import RandomEntryControl
from strategies.trend import STOP_ATR_MULTIPLE, TRAILING_ATR_MULTIPLE


class TrendRandom(RandomEntryControl):
    name = "trend_random"

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
        stop_price = close - distance if direction == "long" else close + distance
        return Signal(
            symbol=symbol,
            direction=direction,
            stop_price=stop_price,
            trailing_atr=TRAILING_ATR_MULTIPLE,
            reason=(
                self._draw_note(market, symbol=symbol, direction=direction,
                                eligible_count=eligible_count)
                + f"; `trend` geometrisi: stop {STOP_ATR_MULTIPLE:g}×ATR({self._atr_period})="
                f"{distance:.6g} uzakta ({stop_price:.6g}), trailing {TRAILING_ATR_MULTIPLE:g}×ATR"
            ),
        )

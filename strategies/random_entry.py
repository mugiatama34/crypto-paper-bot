"""Bilgisiz GİRİŞİN tek tanımı: kontrol modellerinin (`random_ctrl`, `trend_random`, `meanrev_random`) ortak gövdesi.

Üç kontrol aynı soruyu sorar — *"bu modelin girişi, aynı çıkış geometrisiyle rastgele
açılmış işlemlerden ayırt edilebilir mi?"* — ve yalnızca ÇIKIŞ geometrisinde ayrışır
(docs/backtest.md > 6n). Giriş kuralı (uygunluk, barda tek çekiliş, sembol sonra yön) bu
yüzden TEK kopyadır: üç yerde yazılsaydı bir gün ayrışır ve "iki kontrolün farkı" giriş
kuralının değil iki uygulamanın farkı olurdu (`time_stop.py`, `exit_management.py`nin aynı
gerekçesi).

**Uygunluk bir GÖRÜŞ değil, işlemin kurulabilirliğidir:** `as_of` barı + hesaplanabilir ATR
ve alt sınıfın geometrisinin kurulabilmesi (`_setup`; ör. `meanrev_random` için Bollinger).
Buraya eklenecek her ek eleme — rejim, hacim, "kötü sembol" — kontrolü sessizce bir
stratejiye çevirir.

**Alt sınıfın değiştirebileceği iki nokta vardır ve ikisi de ölçülen eksendir (çıkış):**
`_setup` (geometrinin girdisi; kurulamıyorsa None) ve `_signal` (stop/hedef/trailing).
Çekiliş sırası (`rng.choice(uygunlar)` sonra `rng.choice(yönler)`) ve RNG kimliği gövdededir;
`random_ctrl` tarihli defterini bölmesin diye kendi eski tohum biçimini (`_rng_key`) korur.

**RNG ağ katmanıyla ve modül düzeyindeki `random` ile PAYLAŞILMAZ** (bkz.
`strategies/random_ctrl.py`): aynı `as_of` her koşuda aynı sinyali vermelidir.
"""

from __future__ import annotations

import logging
import random
from abc import abstractmethod
from typing import Any, Mapping

import pandas as pd

from core.config import get_setting, load_config
from core.indicators import average_true_range, bars_until
from strategies.base import Direction, MarketData, Signal, Strategy

DIRECTIONS: tuple[Direction, ...] = ("long", "short")
SIGNALS_PER_ROUND = 1


class RandomEntryControl(Strategy):
    allowed_directions: list[Direction] = ["long", "short"]

    def __init__(self, *, config: Mapping[str, Any] | None = None) -> None:
        settings = dict(config) if config is not None else load_config()
        self._atr_period = int(get_setting(settings, "trailing.atr_period"))
        self._seed = int(get_setting(settings, "random_seed"))

    def generate_signals(
        self,
        market: MarketData,
        peer_signals: Mapping[str, tuple[Signal, ...]] | None = None,
    ) -> list[Signal]:
        eligible = self._eligible(market)
        if not eligible:
            # Her kontrol KENDİ modülünün adıyla loglar: log filtresi model başına kalır.
            logging.getLogger(type(self).__module__).info(
                "%s: %s barında işlem kurulabilir sembol yok, çekiliş yapılmadı",
                self.name, market.as_of,
            )
            return []

        rng = random.Random(self._rng_key(market))
        signals: list[Signal] = []
        for _ in range(SIGNALS_PER_ROUND):
            symbol, close, atr, setup = rng.choice(eligible)
            direction = rng.choice(DIRECTIONS)
            signals.append(
                self._signal(
                    market,
                    symbol=symbol, direction=direction, close=close, atr=atr, setup=setup,
                    eligible_count=len(eligible),
                )
            )
        return signals

    def _rng_key(self, market: MarketData) -> str:
        """Tur başına bağımsız çekiliş: tohum sabit, `as_of` ve MODEL ADI ile karışır.

        Ad akışı ayırır: iki kontrol iki ayrı modelin sıfır noktasıdır ve paylaşacakları
        ölçülmeyen bir eksen yoktur (docs/backtest.md > 6n > 4).
        """
        return f"{self._seed}:{market.as_of.isoformat()}:{self.name}"

    def _eligible(self, market: MarketData) -> list[tuple[str, float, float, Any]]:
        """`as_of` barını taşıyan, ATR'si ve geometrisi kurulabilen semboller, ADI SIRALI.

        Sıra çekilişin tekrarlanabilirliğinin parçasıdır: sözlük sırasına bırakmak, aynı
        tohumla aynı turun veri katmanının sırasına göre farklı sembol seçmesi demekti.
        """
        eligible: list[tuple[str, float, float, Any]] = []
        for symbol in sorted(market.ohlcv):
            frame = bars_until(market.ohlcv[symbol], market.as_of)
            if frame.empty or frame.index[-1] != market.as_of:
                continue
            atr = average_true_range(frame, self._atr_period)
            if atr is None or atr <= 0.0:
                continue
            close = float(frame["close"].iloc[-1])
            setup = self._setup(symbol, frame, close)
            if setup is None:
                continue
            eligible.append((symbol, close, atr, setup))
        return eligible

    def _setup(self, symbol: str, frame: pd.DataFrame, close: float) -> Any:
        """Geometrinin ATR dışındaki girdisi; kurulamıyorsa None. Varsayılan: gerek yok."""
        return True

    @abstractmethod
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
        """Çekilen sembol ve yön için ÇIKIŞ geometrisi — kontrolün ölçtüğü tek eksen."""

    def _draw_note(self, market: MarketData, *, symbol: str, direction: Direction,
                   eligible_count: int) -> str:
        return (
            f"KONTROL GRUBU: bilgisiz çekiliş — {eligible_count} uygun sembol "
            f"arasından {symbol}, yön {direction}; tohum {self._seed} + "
            f"{market.as_of:%Y-%m-%d %H:%M} UTC"
        )

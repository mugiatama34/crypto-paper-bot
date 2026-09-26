"""Kontrol grubu: bilgisiz sinyal — edge'in ölçüldüğü sıfır noktası.

Bu model bir strateji DEĞİLDİR; diğer dokuz modelin ürettiği sayıların referansıdır.
Sorduğu soru tek: *"bir modelin ortalama R'si, aynı maliyetler ve aynı limitler altında
rastgele açılmış işlemlerden ayırt edilebilir mi?"* Buy&hold çıpası (kural 15) "piyasa ne
yaptı"yı ölçer; bu satır ise "sinyalin kendisi bir şey söylüyor mu"yu ölçer. İkisi farklı
sorulardır ve bu yüzden bu model bir REFERANS değil, yarışmacıdır: boyutlandırması,
komisyonu, kayması, stop'u ve limitleri diğerleriyle BİREBİR aynıdır — tek farkı sinyalin
bilgisiz olmasıdır.

**Tasarımı bozulamaz.** Buraya eklenen her filtre (rejim, hacim, likidite, "kötü sembolleri
ele") kontrolü sessizce bir stratejiye çevirir ve o andan itibaren diğer modellerin farkı
neye karşı ölçtüğü bilinmez hâle gelir. Tek meşru eleme, işlemin KURULAMADIĞI durumlardır:
`as_of` barı olmayan ya da ATR'si hesaplanamayan sembolde stop mesafesi üretilemez.

**Stop 2×ATR ve sabit.** Stop mesafesi aynı zamanda maliyet ölçeğidir (kural 14): kontrolün
R ölçeği yarışmacılarınkiyle aynı bantta (1×–2.5×ATR) olmazsa `cost_per_r` kolonu kontrolü
haksız biçimde iyi ya da kötü gösterir. 2×, `trend` modeliyle aynı — bandın ortası.

**Tohum turdan tura karıştırılır.** RNG `config.yaml`'ın `random_seed` değerinden beslenir
ama her tur `as_of` ile birlikte tohumlanır: `random.Random(f"{seed}:{as_of}")`. Sabit bir
tohumla süreç başına tek bir RNG kurmak, her koşu ayrı bir süreç olduğu için HER TURDA aynı
çekilişi yapardı — kontrol grubu bilgisiz değil, sabit olurdu. Tur bazlı tohumlama ise iki
şeyi birden verir: aynı `as_of` ile yeniden koşulan bir tur birebir aynı sinyali üretir
(tekrarlanabilirlik), farklı turlar ise bağımsız çekilişler görür.

**RNG ağ katmanıyla PAYLAŞILMAZ.** `core/data.py` retry jitter'ı için kendi
`random.Random(random_seed)` örneğini tutar. Aynı örneği paylaşmak, çekilişi o turda kaç
kez yeniden denendiğine — yani borsanın o günkü keyfine — bağlardı: aynı `as_of` iki koşuda
farklı sembol seçebilirdi ve "tekrarlanabilir koşu" iddiası çökerdi. Modül düzeyinde
`random` fonksiyonlarının (global RNG) kullanılmaması da aynı gerekçeye dayanır.

Rollere dikkat: bu modül boyut/komisyon/bakiye hesaplamaz (kural 1/2/3/7); ATR
`core/indicators.py`'dedir. Pozisyon sayısı, short limiti ve tekrar reddi
`core/portfolio.py`'nin işidir — kontrol de o kapılardan diğerleriyle aynı şekilde geçer.

**EMEKLİ (base katmanında, karar 65) ve ölçü çubuğu olarak BOZUK (karar 60, 63).** Yalnızca
stop'la kapanabildiği için kapanmış-işlem R'si SANSÜRLÜDÜR; `acceptance.broken_controls`
listesindedir. Kod ve defter DURUR (kural 1); giriş kuralı artık `strategies/random_entry.py`
gövdesidir ve bu modelin ürettiği sinyaller o taşımadan önceki hâliyle BİREBİR aynıdır
(tohum biçimi dâhil — test: `tests/test_random_entry.py`).
"""

from __future__ import annotations

from typing import Any, Mapping

from strategies.base import Direction, MarketData, Signal
from strategies.random_entry import DIRECTIONS, SIGNALS_PER_ROUND, RandomEntryControl

STOP_ATR_MULTIPLE = 2.0

__all__ = ["DIRECTIONS", "SIGNALS_PER_ROUND", "STOP_ATR_MULTIPLE", "RandomControl"]


class RandomControl(RandomEntryControl):
    name = "random_ctrl"

    def __init__(
        self,
        *,
        config: Mapping[str, Any] | None = None,
        stop_atr_multiple: float = STOP_ATR_MULTIPLE,
    ) -> None:
        super().__init__(config=config)
        self._stop_atr_multiple = stop_atr_multiple

    def _rng_key(self, market: MarketData) -> str:
        """Eski tohum biçimi (model adı YOK): defter tarihli bölünmesin diye korunur."""
        return f"{self._seed}:{market.as_of.isoformat()}"

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
        distance = atr * self._stop_atr_multiple
        stop_price = close - distance if direction == "long" else close + distance
        return Signal(
            symbol=symbol,
            direction=direction,
            stop_price=stop_price,
            reason=(
                self._draw_note(market, symbol=symbol, direction=direction,
                                eligible_count=eligible_count)
                + f"; stop {self._stop_atr_multiple:g}×ATR({self._atr_period})={distance:.6g} "
                f"uzakta ({stop_price:.6g})"
            ),
        )

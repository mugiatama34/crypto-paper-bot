"""MODEL 14 — model 13'ün sinyali, EV KURALLARIYLA. Tam yarışmacı.

Bu model iki soruyu aynı anda cevaplanabilir kılar:

    model 13 ↔ model 14   Ev kurallarının (risk boyutlandırma, %1 stop tabanı, 1.5R
                          kapısı, 5x kaldıraç tavanı) katkısı nedir? Sinyal birebir aynı
                          olduğu için fark yalnızca kuralların farkıdır.
    model 14 ↔ scalp_fixed  Sinyal + çıkış yönetimi birlikte, beş kollu eşit ağırlıklı
                          çekilişe karşı ne yapıyor?

İkinci kıyasın okunabilmesi için burada DEĞİŞEBİLECEK tek şey sinyal ve çıkış yönetimidir;
geri kalan her şey (boyutlandırma, maliyet, limitler, stop tabanı, hedef/stop kapısı,
katmanın 14 sembollük evreni) scalp_fixed ile birebir aynıdır.

**Parametre öğrenimi YOKTUR ve bilinçli olarak yoktur.** ATR ve hedef çarpanları
config'ten okunan SABİT değerlerdir. Bandit eklemek, model 14 ile scalp_fixed arasındaki
farka üçüncü bir değişken (adaptasyon) katardı — oysa adaptasyonun katkısı zaten
model 11 ↔ model 12 ekseninde ölçülüyor. Bir eksende bir değişken: projenin tamamının
tasarım ilkesi budur.

**Uygulanan ev kapıları** (`strategies/scalp/model.py` ile aynı değerler, aynı config
anahtarları — iki model aynı çıtayı görmezse aralarındaki fark çıtanın farkı olurdu):

- *Stop tabanı %1* (`scalp.min_stop_pct`): tur maliyeti ~%0.25'tir; daha dar stop'ta
  maliyet 0.25R'yi aşar. Stop GENİŞLETİLMEZ, kurulum ATLANIR ve atlama loglanır
  (kural 14'ün "atlama sessiz olamaz" şartı).
- *Hedef/stop ≥ 1.5* (`scalp.min_reward_risk`): sağlamayan kurulum atlanır. Kapının canlı
  kalması için hedef, projeksiyon ile VWAP'in YAKIN olanıdır (bkz.
  `strategies/vwap/signal.py`): yalnızca projeksiyon kullanmak kapıyı ölü koda çevirirdi.

**Turda tek sinyal.** En uzağa sapmış aday oynanır. Gerekçe scalp modellerininkiyle aynı
değildir (burada kol tahsisi yok) ama sonucu aynı olmalıdır: kıyas hedefi scalp_fixed tur
başına tek pozisyon açar ve model 14 beşini birden açsaydı aradaki ortalama R farkı
kısmen "kaç işlem açıldığı"nın farkı olurdu.

Rollere dikkat: bu modül boyut/komisyon/bakiye hesaplamaz (kural 1/2/3/7) ve deftere
yazmaz (kural 1). Sinyal `strategies/vwap/signal.py`'de, çıkış yönetimi
`strategies/exit_management.py`'de — ikisi de model 13 ile tek kopya.
"""

from __future__ import annotations

import logging
from typing import Any, Mapping

from core.config import get_setting, load_config
from core.tags import format_tags
from strategies.base import Direction, MarketData, Signal, Strategy, TakeProfit
from strategies.exit_management import ExitManagement
from strategies.vwap import signal as vwap_signal

logger = logging.getLogger(__name__)


class VwapManaged(Strategy):
    name = "vwap_managed"
    allowed_directions: list[Direction] = ["long", "short"]

    def __init__(self, *, config: Mapping[str, Any] | None = None) -> None:
        settings = dict(config) if config is not None else load_config()
        self._band_mult = float(get_setting(settings, "vwap.band_mult"))
        self._min_vwap_bars = int(get_setting(settings, "vwap.min_vwap_bars"))
        self._atr_period = int(get_setting(settings, "trailing.atr_period"))
        self._atr_multiple = float(get_setting(settings, "vwap.managed.atr_multiple"))
        self._target_reward_risk = float(
            get_setting(settings, "vwap.managed.target_reward_risk")
        )
        # Ev kapıları scalp modelleriyle AYNI anahtarlardan okunur: iki ayrı anahtar,
        # iki ayrı çıta demekti ve model 14 ↔ scalp_fixed kıyası bozulurdu.
        self._min_stop_pct = float(get_setting(settings, "scalp.min_stop_pct"))
        self._min_reward_risk = float(get_setting(settings, "scalp.min_reward_risk"))
        self._exit = ExitManagement.from_config(settings)

    def generate_signals(
        self,
        market: MarketData,
        peer_signals: Mapping[str, tuple[Signal, ...]] | None = None,
    ) -> list[Signal]:
        """Kapılardan geçen en güçlü tek aday. Evren katmanın kendi evrenidir (filtre yok)."""
        candidates = vwap_signal.propose(
            market,
            atr_period=self._atr_period,
            band_mult=self._band_mult,
            min_vwap_bars=self._min_vwap_bars,
            model=self.name,
        )
        for candidate in candidates:
            stop = vwap_signal.stop_price(candidate, atr_multiple=self._atr_multiple)
            projected = vwap_signal.projected_target(
                candidate, stop=stop, reward_risk=self._target_reward_risk
            )
            target = vwap_signal.nearest_target(candidate, projected=projected)
            if not self._passes_gates(candidate, stop=stop, target=target):
                continue
            return [self._signal(candidate, stop=stop, target=target)]
        return []

    def _passes_gates(
        self, candidate: vwap_signal.VwapCandidate, *, stop: float, target: float
    ) -> bool:
        """Stop tabanı ve hedef/stop kapısı. Her eleme GEREKÇESİYLE loglanır (kural 14)."""
        stop_pct = vwap_signal.stop_distance_pct(candidate, stop=stop)
        if stop_pct < self._min_stop_pct:
            logger.info(
                "%s %s: kurulum atlandı, stop mesafesi %%%.3f < taban %%%.3f "
                "(tur maliyeti bu mesafede 0.25R'yi aşar)",
                self.name, candidate.symbol, stop_pct * 100, self._min_stop_pct * 100,
            )
            return False
        reward_risk = vwap_signal.reward_risk_of(candidate, stop=stop, target=target)
        if reward_risk < self._min_reward_risk:
            logger.info(
                "%s %s: kurulum atlandı, hedef/stop %.2f < çıta %.2f (VWAP %.6g çok yakın)",
                self.name, candidate.symbol, reward_risk, self._min_reward_risk, candidate.vwap,
            )
            return False
        return True

    def _signal(
        self, candidate: vwap_signal.VwapCandidate, *, stop: float, target: float
    ) -> Signal:
        stop_pct = vwap_signal.stop_distance_pct(candidate, stop=stop)
        reward_risk = vwap_signal.reward_risk_of(candidate, stop=stop, target=target)
        return Signal(
            symbol=candidate.symbol,
            direction=candidate.direction,
            # Ev kuralı: boyut core/portfolio.py'de, risk_per_trade × sermaye / stop
            # mesafesi (kural 11) ve katmanın leverage_cap'i geçerli.
            stop_price=stop,
            take_profits=(TakeProfit(price=target, fraction=1.0),),
            **self._exit.signal_fields(),
            reason=format_tags(
                f"{candidate.detail()}; stop {self._atr_multiple:g}×ATR = "
                f"%{stop_pct * 100:.2f} ({stop:.6g}), hedef {target:.6g} "
                f"({reward_risk:.2f}R); {self._exit.describe()}",
                arm=vwap_signal.ARM_NAME,
                rr=reward_risk,
            ),
        )

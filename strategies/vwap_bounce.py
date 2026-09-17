"""**Mod B — trend gününde VWAP BOUNCE.** F2'nin ikinci kanadı (ön-kayıt: `docs/backtest.md > 6f`).

**Tez.** Klasik VWAP masası fade ile bitmez: denge gününde uçtan karşıya yatmak çalışır
(Mod A = `vwap_scored`), TREND gününde aynı kurulum ölür. Mod B o günlerde fade açmaz,
fiyat VWAP'e geri geldiğinde trendin YÖNÜNDE girer.

**Bu bir seçim daraltması DEĞİL, ölü bir kurulumun yerine başkasını koymaktır.** Mod A
zaten ADX tavanının üstünde işlem açmıyor — o barlar onun için boş geçiyordu. İki model
birbirinin işlemini ÇALMAZ; ayrı defterlerde, ayrı R'lerle ölçülürler ve **tek bir
birleşik PnL ile karar verilmez** (ön-kayıtta böyle yazıldı).

**Gövde Mod A ile ORTAKTIR ve bu ölçümün şartıdır.** Boyut kademesi, post-only maker
giriş, ilerleme koşullu zaman stop'u, likidite kuralı, korelasyon yarım boyu ve skor
iskeleti `VwapScored`tan MİRAS ALINIR. İki ayrı uygulama olsaydı, Mod A ↔ Mod B farkı
"kurulum farkı" olmaktan çıkar, "iki ayrı gövdenin farkı" olurdu.

**Ayrışan ÜÇ nokta ve her birinin karşılığı olan eksen:**

| Override | Ne değişir | Neden |
|---|---|---|
| `_load_signal_params` + `_detect` | KURULUM: sapma-dönüş yerine geri çekilme-bounce | ölçülen eksenin kendisi |
| `_regime_tier` | kademe TERSİNE döner: güçlü trend = tam boy | bounce tezinin geçerli olduğu yer güçlü trenddir; sakin rejimde VWAP'e dönüş bir "bounce" değil gürültüdür |
| `_extension` | sıralama ölçütü |z|/bant yerine GEOMETRİ (hedef/stop) | bounce'ta giriş VWAP'in DİBİNDEDİR, yani |z| tanım gereği küçüktür ve sıralamayı anlamsız kılardı |

**Kademeler 25–30 aralığında ÖRTÜŞÜR ve bu bilinçlidir.** Fade 22–30 arasında yarım boya
düşer, bounce 25'te yarım boyla başlar: o bant "ne tam denge ne tam trend" bölgesidir ve
iki tezin de zayıf konuştuğu yerdir. İkisinin aynı barda ters yönde işlem açması bir
çelişki değildir — ayrı hesaplar, ayrı defterler, ayrı R'ler; hangisinin haklı olduğu tam
olarak orada ölçülür. Tek bir birleşik PnL bunu gizlerdi ve ön-kayıt onu yasakladı.

Rollere dikkat: boyut/komisyon/bakiye hesaplanmaz (kural 1/2/3/7), deftere yazılmaz.
"""

from __future__ import annotations

import logging
from typing import Any, Mapping

import pandas as pd

from core.config import get_setting
from strategies.vwap import bounce_signal
from strategies.vwap_scored import VwapScored

logger = logging.getLogger(__name__)

CONFIG_PREFIX = "vwap.bounce"


class VwapBounce(VwapScored):
    name = "vwap_bounce"
    config_prefix = CONFIG_PREFIX
    arm_name = bounce_signal.ARM_NAME

    def __init__(self, *, config: Mapping[str, Any] | None = None) -> None:
        super().__init__(config=config)
        if self._adx_min > self._adx_max:
            raise ValueError(f"{CONFIG_PREFIX}.regime: adx_min, adx_max'ı aşamaz")

    # ------------------------------------------------------------------ #
    # 1. Kurulum: geri çekilme + reddediliş
    # ------------------------------------------------------------------ #
    def _load_signal_params(self, settings: Mapping[str, Any]) -> None:
        self._params = bounce_signal.BounceParams(
            min_slope_sigma=float(
                get_setting(settings, f"{self.config_prefix}.min_slope_sigma")
            ),
            slope_bars=int(get_setting(settings, f"{self.config_prefix}.slope_bars")),
            touch_sigma=float(get_setting(settings, f"{self.config_prefix}.touch_sigma")),
            stop_sigma=float(get_setting(settings, f"{self.config_prefix}.stop_sigma")),
            target_reward_risk=float(
                get_setting(settings, f"{self.config_prefix}.target_reward_risk")
            ),
            extreme_lookback=int(
                get_setting(settings, f"{self.config_prefix}.extreme_lookback")
            ),
            min_bars=int(get_setting(settings, f"{self.config_prefix}.min_bars")),
        )
        self._min_reward_risk = float(
            get_setting(settings, f"{self.config_prefix}.min_reward_risk")
        )
        self._adx_min = float(get_setting(settings, f"{self.config_prefix}.regime.adx_min"))

    def _detect(self, frame: pd.DataFrame, *, symbol: str, as_of: pd.Timestamp):
        candidate, reason = bounce_signal.detect(
            frame, symbol=symbol, as_of=as_of, params=self._params,
            atr_period=self._adx_period,
        )
        if candidate is not None and candidate.reward_risk < self._min_reward_risk:
            # Geometri kapısı BURADADIR, sinyal modülünde değil: modül kurulumun YERİNİ
            # verir, "oynanır mı" sorusu modelin kuralıdır (model 14 ile aynı ayrım).
            logger.info(
                "%s %s: kurulum atlandı, hedef/stop %.2f < çıta %.2f",
                self.name, symbol, candidate.reward_risk, self._min_reward_risk,
            )
            return None, bounce_signal.BAD_GEOMETRY
        return candidate, reason

    def _empty_counts(self) -> dict[str, int]:
        return bounce_signal.empty_counts()

    def _describe(self, counts: Mapping[str, int]) -> str:
        return bounce_signal.Survey(counts=counts).describe()

    # ------------------------------------------------------------------ #
    # 2. Kademe TERSİNE döner
    # ------------------------------------------------------------------ #
    def _regime_tier(self, strength: float) -> tuple[float, float] | None:
        """Güçlü trend = TAM boy; eşiğin altında kurulum YOK.

        Mod A'da sakin rejim tam boydu ve gerekçe fade tezinin orada çalışmasıydı.
        Burada tez terstir, kademe de ters: `adx_full`in üstü tam boy, `adx_min` ile
        `adx_full` arası yarım boy, altı hiç yok. Üst tavan YOKTUR — bounce için "çok
        güçlü trend" diye bir sakınca yoktur, tezin kendisi odur.
        """
        if strength < self._adx_min:
            return None
        return (
            (self._size_full, 1.0) if strength >= self._adx_full else (self._size_half, 0.5)
        )

    # ------------------------------------------------------------------ #
    # 3. Sıralama: sapma değil GEOMETRİ
    # ------------------------------------------------------------------ #
    def _extension(self, candidate: Any) -> float:
        """Bounce'ta giriş VWAP'in DİBİNDEDİR: |z| tanım gereği küçüktür.

        Sıralama ölçütü olarak |z| kullanmak, "VWAP'e en uzak" kurulumu en iyi ilan
        ederdi — oysa bu kolda VWAP'e YAKINLIK kurulumun şartıdır. Ölçüt hedef/stop
        oranıdır: aynı riske en çok yol vaat eden kurulum önce oynanır.
        """
        return float(candidate.reward_risk)

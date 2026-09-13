"""İki scalp modelinin ORTAK gövdesi: kapılar, zaman stop'u, sinyal kurulumu.

Model 11 (`scalp_bandit`) ve model 12 (`scalp_fixed`) bu sınıftan türer ve **yalnızca tek
bir metodu** farklı uygular: `choose_arm`. Geri kalan her şey — hangi kolların
çağrıldığı, stop tabanı, hedef/stop kapısı, zaman stop'u, sinyalin nasıl kurulduğu, ret
gerekçelerinin nasıl loglandığı — burada tek kopyadır. Fark tek satıra indirgenmezse iki
model arasındaki ortalama R farkı "adaptasyonun katkısı" olmaktan çıkar ve iki ayrı
uygulamanın farkı hâline gelir; oysa model 12 tam da model 11'in NULL HİPOTEZİDİR.

**Üç zorunlu kısıt (ikisi kapı, biri çıkış):**

1. **Stop tabanı %1.** Tur maliyeti ~%0.25'tir (giriş+çıkış komisyonu %0.2 + kayma %0.1
   civarı). Stop mesafesi girişin %1'inin altındaysa bu maliyet 0.25R'yi aşar ve model
   daha başlamadan geride başlar. Stop GENİŞLETİLMEZ, işlem ATLANIR: tabana çekmek, kolun
   "bu mesafede yanılmışım demektir" tezini sessizce başka bir teze çevirirdi (kural 14'ün
   aynı gerekçesi).
2. **Hedef/stop ≥ 1.5.** Bu oranla başabaş kazanma oranı %40'tır (1/(1+1.5)); maliyetle
   birlikte pratikte ~%50. Çıta budur: sağlamayan kurulum atlanır.
3. **Zaman stop'u: 16 bar (4 saat).** Scalp tezleri saatler ölçeğindedir; 16 bar sonra hâlâ
   ne stop'a ne hedefe değmiş bir pozisyon, tezin ölçtüğü hareketin gerçekleşmediğinin
   kanıtıdır ve sermayeyi (ve marjı) tutmaya devam etmesinin bir gerekçesi yoktur.
   Kapanış `manage_positions` üzerinden istenir (kural 10) ve dolum bir SONRAKİ barın
   açılışındadır (kural 13) — yani pozisyonun gerçek ömrü 17 bardır. Bu bir sapma değil,
   dolum kuralının kendisidir; "16 bar" kararın verildiği bardır.

**Neden trailing yok.** `Signal.trailing_atr` doldurulmaz: yürüyen stop hedefe varmadan
çıkışı öne çeker ve işlemin gerçekleşen R'si ile kurulumun vaat ettiği 1.5R+ arasındaki
bağı koparır. Kollar tam olarak o vaadin tutup tutmadığıyla ölçülüyor; trailing, bandit'in
öğrendiği sinyali bulanıklaştırırdı.

**Turda tek sinyal.** Seçilen koldan tek kurulum oynanır. Beşini birden oynamak, kol
tahsisini anlamsız kılardı (her kol her turda oynanıyorsa öğrenilecek bir tahsis yoktur);
üstelik `max_positions` kotası turun ilk sembollerine keyfî bir öncelik verirdi.

Rollere dikkat: bu modül boyut/komisyon/bakiye hesaplamaz (kural 1/2/3/7) ve deftere
yazmaz. Kol mantığı `strategies/scalp/arms.py`'de, gösterge matematiği
`core/indicators.py`'dedir.
"""

from __future__ import annotations

import logging
import random
from abc import abstractmethod
from typing import Any, Mapping, Sequence

import pandas as pd

from core.config import get_setting, load_config
from core.data import bar_duration
from core.tags import format_tags
from strategies.base import (
    Direction,
    ExitInstruction,
    MarketData,
    Position,
    Signal,
    Strategy,
    TakeProfit,
)
from strategies.scalp.arms import ARM_NAMES, ArmParams, ArmSetup, propose_all

logger = logging.getLogger(__name__)

SIGNALS_PER_ROUND = 1


class ScalpModel(Strategy):
    """İki scalp modelinin ortak gövdesi. `choose_arm` dışında her şey burada."""

    allowed_directions: list[Direction] = ["long", "short"]

    def __init__(self, *, config: Mapping[str, Any] | None = None) -> None:
        settings = dict(config) if config is not None else load_config()
        self._params = ArmParams(
            # ATR periyodu parametre DEĞİL: projenin tek ATR tanımı config'in
            # `trailing.atr_period` değeridir — aynı "5×ATR" her modelde aynı mesafe demeli.
            atr_period=int(get_setting(settings, "trailing.atr_period")),
            stop_atr_multiple=float(get_setting(settings, "scalp.stop_atr_multiple")),
            target_reward_risk=float(get_setting(settings, "scalp.target_reward_risk")),
        )
        self._min_stop_pct = float(get_setting(settings, "scalp.min_stop_pct"))
        self._min_reward_risk = float(get_setting(settings, "scalp.min_reward_risk"))
        self._time_stop_bars = int(get_setting(settings, "scalp.time_stop_bars"))
        self._bar_duration = bar_duration(str(get_setting(settings, "timeframe")))
        self._seed = int(get_setting(settings, "random_seed"))

    # ------------------------------------------------------------------ #
    # Açılış
    # ------------------------------------------------------------------ #
    def generate_signals(
        self,
        market: MarketData,
        peer_signals: Mapping[str, tuple[Signal, ...]] | None = None,
    ) -> list[Signal]:
        proposals = propose_all(market, self._params)
        available = {
            arm: kept
            for arm, setups in proposals.items()
            if (kept := self._gated(arm, setups))
        }
        if not available:
            return []

        rng = self._round_rng(market)
        arm, posterior = self.choose_arm(sorted(available), rng=rng)
        if arm not in available:
            raise ValueError(
                f"{self.name}: seçilen kol {arm!r} bu turda kurulum üretmemişti "
                f"(uygun kollar: {sorted(available)})"
            )

        setups = sorted(available[arm], key=lambda item: item.symbol)
        chosen = [rng.choice(setups) for _ in range(min(SIGNALS_PER_ROUND, len(setups)))]
        return [self._signal(setup, posterior=posterior) for setup in chosen]

    def _gated(self, arm: str, setups: Sequence[ArmSetup]) -> list[ArmSetup]:
        """Stop tabanı ve hedef/stop kapısı. Her eleme GEREKÇESİYLE loglanır.

        Sessiz eleme, modelin işlem sayısını denetlenemez biçimde düşürmesi demek olurdu
        (kural 14'ün "atlama sessiz olamaz" şartı): kolun hiç kurulum üretmemesi ile
        kurulumlarının kapıda elenmesi logda ayırt edilebilir olmalı.
        """
        kept: list[ArmSetup] = []
        for setup in setups:
            if setup.stop_distance_pct < self._min_stop_pct:
                logger.info(
                    "%s %s/%s: kurulum atlandı, stop mesafesi %%%.3f < taban %%%.3f "
                    "(tur maliyeti bu mesafede 0.25R'yi aşar)",
                    self.name, arm, setup.symbol,
                    setup.stop_distance_pct * 100, self._min_stop_pct * 100,
                )
                continue
            if setup.reward_risk < self._min_reward_risk:
                logger.info(
                    "%s %s/%s: kurulum atlandı, hedef/stop %.2f < çıta %.2f",
                    self.name, arm, setup.symbol, setup.reward_risk, self._min_reward_risk,
                )
                continue
            kept.append(setup)
        return kept

    def _signal(self, setup: ArmSetup, *, posterior: float) -> Signal:
        """Kurulumu sinyale çevirir; `reason` kuyruğuna kol ve posterior etiketlenir.

        Etiketler ayrıştırılabilir olmak zorundadır (bkz. core/tags.py): kol bazlı kırılım
        (`docs/data/metrics_scalp.json`) bu kuyruktan okunur. Etiketsiz bir satır kırılımı
        sessizce eksiltirdi, bu yüzden okuyan taraf etiketi bulamazsa hata fırlatır.
        """
        return Signal(
            symbol=setup.symbol,
            direction=setup.direction,
            stop_price=setup.stop_price,
            take_profits=(TakeProfit(price=setup.target_price, fraction=1.0),),
            reason=format_tags(
                f"{setup.detail}; stop {setup.stop_distance_pct * 100:.2f}% "
                f"({setup.stop_price:.6g}), hedef {setup.target_price:.6g} "
                f"({setup.reward_risk:.2f}R), zaman stop'u {self._time_stop_bars} bar",
                arm=setup.arm,
                post_r=posterior,
            ),
        )

    def _round_rng(self, market: MarketData) -> random.Random:
        """Tur ve model başına bağımsız RNG.

        Tohum sabittir ama `as_of` ve model adıyla karışır: aynı `as_of` ile yeniden
        koşulan tur birebir aynı seçimi üretir (tekrarlanabilirlik), iki model ise aynı
        turda bağımsız çekiliş görür — model 12'nin eşit ağırlıklı çekilişi model 11'in
        kararının kopyası olsaydı, aradaki fark adaptasyonun değil tesadüfün ölçüsü olurdu.
        """
        return random.Random(f"{self._seed}:{market.as_of.isoformat()}:{self.name}")

    # ------------------------------------------------------------------ #
    # Çıkış: zaman stop'u
    # ------------------------------------------------------------------ #
    def manage_positions(
        self, market: MarketData, positions: list[Position]
    ) -> list[ExitInstruction]:
        """16 bardan uzun süredir açık olan pozisyonları piyasa fiyatından kapatır."""
        deadline = self._bar_duration * self._time_stop_bars
        instructions: list[ExitInstruction] = []
        for position in positions:
            age = market.as_of - pd.Timestamp(position.opened_at)
            if age < deadline:
                continue
            bars = int(age / self._bar_duration)
            instructions.append(
                ExitInstruction(
                    symbol=position.symbol,
                    action="close",
                    reason=format_tags(
                        f"zaman stop'u: pozisyon {bars} bardır açık "
                        f"({self._time_stop_bars} bar sınırı), piyasa fiyatından kapatılıyor",
                        exit_rule="time_stop",
                    ),
                )
            )
        return instructions

    # ------------------------------------------------------------------ #
    # Alt sınıfın tek işi
    # ------------------------------------------------------------------ #
    @abstractmethod
    def choose_arm(
        self, available: Sequence[str], *, rng: random.Random
    ) -> tuple[str, float]:
        """O turda oynanacak kolu seçer ve (kol, posterior ortalama R) döndürür.

        `available` YALNIZCA bu turda kurulum üretmiş kolları içerir ve adı sıralıdır.
        Kurulum üretmemiş bir kolu seçmek turu boşa harcamak olurdu; posterior ise
        ölçülemediğinde `nan`dır (0.0 değil — "ölçülmedi" ile "ölçüldü, sıfır çıktı"
        aynı hücreye yazılamaz).
        """


def arm_universe() -> tuple[str, ...]:
    """Kol adları, sabit sırayla. Bandit'in durum sözlüğü bu sırayı kullanır."""
    return ARM_NAMES

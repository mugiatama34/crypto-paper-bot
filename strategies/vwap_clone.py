"""MODEL 13 — dış bir sistemin KOPYASI (`is_replica=True`), yarışmacı değil.

**Ölçtüğü soru başkalarınınkinden farklıdır.** On üç yarışmacı "bu sinyal fikri işe
yarıyor mu" diye sorar; bu model *"dışarıda koşan şu sistem, BİZİM maliyet, kayma,
funding ve likidasyon varsayımlarımız altında ne yapardı"* diye sorar. Cevap bir sıralama
değil, bir zemindir — tıpkı alım-tut çıpası gibi ama başka bir eksende: çıpa "piyasa ne
yaptı"yı, kopya "dış sistem ne yapardı"yı ölçer. Bu yüzden tabloda ayrı bir bölümdedir
(`REFERANS (dış sistem)`), ortalama R sıralamasına girmez ve maliyet ölçeği kolonlarında
`nan` alır: 1R'sini sabit teminattan türettiği için yarışmacılarınkiyle aynı birim değil.

**Kaynak sistemin kuralları, olduğu gibi:**

- *Sinyal:* VWAP sapma-dönüş (`strategies/vwap/signal.py`, model 14 ile tek kopya).
- *Boyutlandırma:* sabit teminat × sabit kaldıraç. `sizing="notional_fraction"` ve
  `ModelLimits.leverage` ile ifade edilir: notional = fraction × sermaye, marj =
  notional / kaldıraç. Başlangıç sermayesinde (10.000) fraction 0.5 ve 10x, tam olarak
  "500 USDT teminat, 5.000 notional" demektir; sermaye büyüdükçe teminat da orantılı
  büyür — sabit bir USDT tutarı yazmak, hesap iki katına çıktığında kopyayı gerçek
  sistemin yarısı kadar risk alan bir şeye çevirirdi.
- *Çıkış:* üç aşamalı yönetim (`strategies/exit_management.py`, modeller 14-15 ile tek
  kopya): breakeven, kısmi çıkış + stop kaydırma, geri verme takibi.
- *Limitler:* eşzamanlı en çok 5 pozisyon, aynı yönde en çok 3, açık toplam risk
  sermayenin %8'ini aşamaz (`ModelLimits`; uygulayan core/portfolio.py, kural 3).
- *Parametre öğrenimi:* epsilon-greedy bandit, 4 ATR çarpanı × 3 hedef çarpanı = 12
  kombinasyon; istatistik SEMBOL BAZINDA tutulur ve bir sembolde `min_symbol_samples`
  örnekten az veri varken tüm semboller genelindeki ortalamaya düşülür.
- *Evren:* kaynak sistemin 13 sembolü. Katmanın evreni 14'tür; aradaki fark bilinçlidir
  ve config'te yazılıdır — fazladan bir sembolde işlem açmak kopyayı kopya olmaktan
  çıkarırdı.

**Ev kapıları UYGULANMAZ.** Scalp katmanının %1 stop tabanı ve 1.5R hedef/stop kapısı bu
modele geçmez: kaynak sistemde yoktur. Kapıları eklemek kopyayı "ev kurallarıyla koşan
bir model"e çevirirdi — ki o zaten model 14'tür ve ikisinin farkı tam olarak bu kapıların
(artı boyutlandırmanın) katkısıdır.

**Likidasyon modellemesi KORUNUR** ve bu bir tercih değil, ölçümün koşuludur: 10x'te
likidasyon gerçek bir risktir ve onu kapatmak kopyayı haksız biçimde iyi gösterirdi
(gerekçe: docs/decisions.md > "Kopya modelde likidasyon kapatılmaz").

**Bandit durumu deftere yazılır, ayrı bir durum dosyası YOKTUR** (`scalp_bandit` ile aynı
desen ve aynı gerekçe): posterior `trades.csv`'nin saf bir fonksiyonudur ve her turda
sıfırdan kurulur. Denetim izi `reason` kuyruğundadır: `... | arm=vwap_revert |
combo=atr2_tp1 | ...`.

Rollere dikkat: bu modül boyut/komisyon/bakiye hesaplamaz (kural 1/2/3/7) ve deftere
yazmaz (kural 1); gördüğü tek geçmiş KENDİ kapanmış işlemleridir (kural 16).
"""

from __future__ import annotations

import logging
import random
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from core.config import get_setting, load_config
from core.tags import find_tag, format_tags
from strategies.base import (
    ClosedTrade,
    Direction,
    MarketData,
    ModelLimits,
    Signal,
    Strategy,
    TakeProfit,
)
from strategies.exit_management import ExitManagement
from strategies.vwap import signal as vwap_signal

logger = logging.getLogger(__name__)

NAN = float("nan")


@dataclass(frozen=True, kw_only=True)
class Combo:
    """Bandit'in bir kolu: bir stop çarpanı + bir hedef çarpanı.

    `key` deftere yazılan ve geri okunan etikettir. İNDEKSTEN türetilir, sayıdan değil:
    "2.5" gibi bir kayan noktayı etiket anahtarı yapmak, biçimlendirme (`%.4g`) ile geri
    ayrıştırmanın bir gün ayrışması demekti — ve posterior sessizce boşalırdı.
    """

    key: str
    atr_multiple: float
    target_reward_risk: float


@dataclass(frozen=True, kw_only=True)
class ComboStats:
    """Bir kombinasyonun gözlem özeti. `nan` = ölçülmedi (0.0 değil)."""

    trades: int
    mean_r: float

    @property
    def measured(self) -> bool:
        return self.trades > 0


class VwapClone(Strategy):
    name = "vwap_clone"
    allowed_directions: list[Direction] = ["long", "short"]
    is_replica = True

    def __init__(self, *, config: Mapping[str, Any] | None = None) -> None:
        settings = dict(config) if config is not None else load_config()
        self._band_mult = float(get_setting(settings, "vwap.band_mult"))
        self._min_vwap_bars = int(get_setting(settings, "vwap.min_vwap_bars"))
        self._atr_period = int(get_setting(settings, "trailing.atr_period"))
        self._universe = [str(s) for s in get_setting(settings, "vwap.clone.universe")]
        self._notional_fraction = float(get_setting(settings, "vwap.clone.notional_fraction"))
        self._epsilon = float(get_setting(settings, "vwap.clone.bandit.epsilon"))
        self._min_symbol_samples = int(
            get_setting(settings, "vwap.clone.bandit.min_symbol_samples")
        )
        self._exit = ExitManagement.from_config(settings)
        self._seed = int(get_setting(settings, "random_seed"))

        self._combos = _build_combos(
            [float(v) for v in get_setting(settings, "vwap.clone.atr_multiples")],
            [float(v) for v in get_setting(settings, "vwap.clone.target_reward_risks")],
        )
        if not 0.0 <= self._epsilon <= 1.0:
            raise ValueError(f"vwap.clone.bandit.epsilon 0 ile 1 arasında olmalı: {self._epsilon}")

        self.limits = ModelLimits(
            max_positions=int(get_setting(settings, "vwap.clone.max_positions")),
            max_per_direction=int(get_setting(settings, "vwap.clone.max_per_direction")),
            max_portfolio_risk=float(get_setting(settings, "vwap.clone.max_portfolio_risk")),
            leverage=float(get_setting(settings, "vwap.clone.leverage")),
        )

        # Sembol bazlı ve genel istatistik; ikisi de defterden kurulur (aşağıya bkz.).
        self._by_symbol: dict[str, dict[str, ComboStats]] = {}
        self._global: dict[str, ComboStats] = _empty_stats(self._combos)

    # ------------------------------------------------------------------ #
    # Öğrenme (kural 16)
    # ------------------------------------------------------------------ #
    def observe_closed_trades(self, trades: Sequence[ClosedTrade]) -> None:
        """Kendi KAPANMIŞ işlemlerinden kombinasyon istatistiklerini sıfırdan kurar.

        Artımlı güncelleme YOK (`scalp_bandit` ile aynı gerekçe): durum defterin saf bir
        fonksiyonu olmazsa, defterle modelin hafızası bir gün ayrışır ve hangisinin doğru
        olduğu bilinemez.

        Etiketi TANINMAYAN satır sessizce atlanmaz, loglanır: kombinasyon kümesi
        değişmişse (config'te çarpan listesi düzenlenmişse) eski satırlar yeni kümeye
        ait değildir ve onları yeni kolların ortalamasına katmak, öğrenmeyi defterde
        görünmeyen bir geçmişe bağlardı.

        `arm=` etiketi olmayan satırda `TagError` fırlatılmaz — burada aranan etiket
        `combo`dur ve yokluğu ayrıca sayılır; kol kırılımının bütünlüğünü
        `core/metrics.py::arm_of` zaten kendi başına denetler.
        """
        by_symbol: dict[str, dict[str, list[float]]] = {}
        overall: dict[str, list[float]] = {combo.key: [] for combo in self._combos}
        unknown: set[str] = set()
        untagged = 0

        for trade in sorted(trades, key=lambda item: item.closed_at):
            key = find_tag(trade.signal_reason, "combo")
            if key is None:
                untagged += 1
                continue
            if key not in overall:
                unknown.add(key)
                continue
            if trade.r_multiple is None:
                continue
            overall[key].append(float(trade.r_multiple))
            by_symbol.setdefault(trade.symbol, {k: [] for k in overall})[key].append(
                float(trade.r_multiple)
            )

        if untagged:
            logger.warning(
                "%s: %d kapanmış işlemde combo etiketi yok, öğrenmeye girmedi",
                self.name, untagged,
            )
        if unknown:
            logger.warning(
                "%s: defterde tanınmayan kombinasyon: %s — kombinasyon kümesi değişmiş "
                "olabilir, bu satırlar öğrenmeye girmedi",
                self.name, ", ".join(sorted(unknown)),
            )

        self._global = {key: _summarize(values) for key, values in overall.items()}
        self._by_symbol = {
            symbol: {key: _summarize(values) for key, values in per_combo.items()}
            for symbol, per_combo in by_symbol.items()
        }
        logger.info("%s öğrenme durumu: %s", self.name, self.state_summary())

    def state_summary(self) -> str:
        return " ".join(
            f"{key}(n={item.trades},R={item.mean_r:.3f})" if item.measured else f"{key}(n=0)"
            for key, item in self._global.items()
        )

    def stats_for(self, symbol: str) -> dict[str, ComboStats]:
        """Sembolün istatistikleri; testler ve denetim için salt okunur kopya."""
        return dict(self._by_symbol.get(symbol, {}))

    # ------------------------------------------------------------------ #
    # Sinyal
    # ------------------------------------------------------------------ #
    def generate_signals(
        self,
        market: MarketData,
        peer_signals: Mapping[str, tuple[Signal, ...]] | None = None,
    ) -> list[Signal]:
        """Kaynak sistemin evrenindeki tüm sapma-dönüş adayları, en güçlüden başlayarak.

        Turda tek sinyalle sınırlanmaz (scalp modellerinin kolları için geçerli olan kural
        burada yok): kaynak sistem aynı anda beş pozisyona kadar taşır ve tek sinyale
        indirmek onu başka bir sisteme çevirirdi. Liste yine de modelin kendi pozisyon
        kotasıyla (`ModelLimits.max_positions`) kırpılır — kotanın ötesindeki her emir
        bir sonraki barda zaten `max_positions` koduyla reddedilir ve tur raporunu
        gerçek olmayan retlerle doldururdu.
        """
        candidates = vwap_signal.propose(
            market,
            atr_period=self._atr_period,
            band_mult=self._band_mult,
            min_vwap_bars=self._min_vwap_bars,
            symbols=self._universe,
        )
        limit = self.limits.max_positions if self.limits.max_positions else len(candidates)
        rng = self._round_rng(market)
        return [self._signal(candidate, rng=rng) for candidate in candidates[:limit]]

    def _signal(self, candidate: vwap_signal.VwapCandidate, *, rng: random.Random) -> Signal:
        combo, stats, explored = self.choose_combo(candidate.symbol, rng=rng)
        stop = vwap_signal.stop_price(candidate, atr_multiple=combo.atr_multiple)
        target = vwap_signal.projected_target(
            candidate, stop=stop, reward_risk=combo.target_reward_risk
        )
        return Signal(
            symbol=candidate.symbol,
            direction=candidate.direction,
            # Kaynak sistemin boyutlandırması: sabit teminat × sabit kaldıraç.
            sizing="notional_fraction",
            notional_fraction=self._notional_fraction,
            stop_price=stop,
            take_profits=(TakeProfit(price=target, fraction=1.0),),
            **self._exit.signal_fields(),
            reason=format_tags(
                f"{candidate.detail()}; stop {combo.atr_multiple:g}×ATR ({stop:.6g}), "
                f"hedef {combo.target_reward_risk:g}R ({target:.6g}); "
                f"{self._exit.describe()}",
                arm=vwap_signal.ARM_NAME,
                combo=combo.key,
                combo_r=stats.mean_r,
                combo_n=stats.trades,
                pick="explore" if explored else "exploit",
            ),
        )

    # ------------------------------------------------------------------ #
    # Epsilon-greedy seçim
    # ------------------------------------------------------------------ #
    def choose_combo(
        self, symbol: str, *, rng: random.Random
    ) -> tuple[Combo, ComboStats, bool]:
        """`epsilon` olasılıkla rastgele, aksi hâlde en yüksek ortalama R'li kombinasyon.

        İstatistik önce SEMBOL bazında aranır: aynı kurulum BTC'de ve PENGU'da aynı stop
        çarpanıyla aynı sonucu vermez (oynaklık ve kitap derinliği farklı). Ama sembol
        başına veri geç birikir; `min_symbol_samples` örneğe ulaşmamış bir sembolde
        sembolün kendi gürültüsüne uymak, tüm semboller genelindeki ortalamadan DAHA KÖTÜ
        bir tahmindir — o yüzden genele düşülür.

        Keşif payı (`epsilon`) sabittir ve sıfıra inmez: sıfırlanan bir kombinasyon bir
        daha ÖLÇÜLEMEZ ve "kötüydü" iddiası sınanamaz hâle gelir (`scalp_bandit`'in taban
        tahsisiyle aynı gerekçe).
        """
        stats = self._stats_for_choice(symbol)
        if rng.random() < self._epsilon:
            combo = rng.choice(self._combos)
            logger.info(
                "%s %s: keşif çekilişi (epsilon=%.2f) -> %s",
                self.name, symbol, self._epsilon, combo.key,
            )
            return combo, stats[combo.key], True

        measured = [combo for combo in self._combos if stats[combo.key].measured]
        if not measured:
            # Hiç ölçüm yokken "en iyi"yi seçmek, sabit bir kombinasyonu kalıcı olarak
            # ayrıcalıklı kılardı; çekiliş ilk gözlemleri kombinasyonlara dağıtır.
            combo = rng.choice(self._combos)
            logger.info(
                "%s %s: henüz ölçüm yok, eşit çekiliş -> %s", self.name, symbol, combo.key
            )
            return combo, stats[combo.key], True

        combo = max(measured, key=lambda item: (stats[item.key].mean_r, item.key))
        logger.info(
            "%s %s: en iyi kombinasyon -> %s (R=%.3f, n=%d)",
            self.name, symbol, combo.key, stats[combo.key].mean_r, stats[combo.key].trades,
        )
        return combo, stats[combo.key], False

    def _stats_for_choice(self, symbol: str) -> dict[str, ComboStats]:
        """Kombinasyon -> istatistik; sembolde yeterli örnek yoksa o hücre GENELE düşer.

        Geri düşüş kombinasyon BAZINDADIR, sembolün tamamı için değil: bir sembolde bir
        kombinasyon 10 kez, diğeri 1 kez oynanmış olabilir ve ölçülmüş olanı genelin
        ortalamasına feda etmek, sembolün gerçekten taşıdığı bilgiyi atmak olurdu.
        """
        per_symbol = self._by_symbol.get(symbol, {})
        resolved: dict[str, ComboStats] = {}
        for combo in self._combos:
            local = per_symbol.get(combo.key)
            if local is not None and local.trades >= self._min_symbol_samples:
                resolved[combo.key] = local
            else:
                resolved[combo.key] = self._global[combo.key]
        return resolved

    def _round_rng(self, market: MarketData) -> random.Random:
        """Tur ve model başına bağımsız RNG; tohum sabit, seçim tekrarlanabilir."""
        return random.Random(f"{self._seed}:{market.as_of.isoformat()}:{self.name}")


# --------------------------------------------------------------------------- #
# Yardımcılar
# --------------------------------------------------------------------------- #
def _build_combos(
    atr_multiples: Sequence[float], target_reward_risks: Sequence[float]
) -> list[Combo]:
    """Kartezyen çarpım, SABİT sırayla. Sıra tekrarlanabilirliğin parçasıdır."""
    if not atr_multiples or not target_reward_risks:
        raise ValueError("vwap.clone çarpan listeleri boş olamaz")
    return [
        Combo(
            key=f"atr{atr_index}_tp{tp_index}",
            atr_multiple=float(atr_multiple),
            target_reward_risk=float(reward_risk),
        )
        for atr_index, atr_multiple in enumerate(atr_multiples)
        for tp_index, reward_risk in enumerate(target_reward_risks)
    ]


def _empty_stats(combos: Sequence[Combo]) -> dict[str, ComboStats]:
    return {combo.key: ComboStats(trades=0, mean_r=NAN) for combo in combos}


def _summarize(values: Sequence[float]) -> ComboStats:
    if not values:
        return ComboStats(trades=0, mean_r=NAN)
    return ComboStats(trades=len(values), mean_r=sum(values) / len(values))

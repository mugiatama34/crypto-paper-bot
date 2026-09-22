"""Scalp modellerinin ORTAK gövdesi: kapılar, zaman stop'u, sinyal kurulumu.

Model 11 (`scalp_bandit`), model 12 (`scalp_fixed`) ve model 15 (`scalp_managed`) bu
sınıftan türer. Her şey — hangi kolların çağrıldığı, stop tabanı, hedef/stop kapısı,
zaman stop'u, sinyalin nasıl kurulduğu, ret gerekçelerinin nasıl loglandığı — burada tek
kopyadır. Alt sınıfın değiştirebileceği noktalar SAYILIDIR ve **her biri ölçülen bir eksene
karşılık gelmek ZORUNDADIR** — kural sayı değil, bu karşılıklılıktır:

    choose_arm       — kol seçimi (model 11 ↔ model 12: adaptasyonun katkısı)
    exit_management  — üç aşamalı çıkış yönetimi (model 12 ↔ model 15: yönetimin katkısı)
    rng_identity     — çekilişin kimliği (aşağıya bkz.)
    time_stop_key    — zaman stop'unun SINIRI (model 12 ↔ model 16: sürenin katkısı)
    regime_filter    — ek rejim kapısı (model 16 ↔ model 17: volatilite rejiminin katkısı)
    direction_policy — kurulumun YÖNÜ (model 16 ↔ model 23: yön iddiasının bilgi değeri)

Liste zamanla uzadı ve uzayabilir; uzatmanın bedeli şudur: **karşılığı bir eksen olmayan
bir override noktası eklenemez.** Aksi hâlde iki model arasındaki fark birden çok yerden
gelir ve hangisinin ölçüldüğü bilinemez (bkz. CLAUDE.md > "13 ↔ 14 bir EKSEN DEĞİL").

Fark tek bir noktaya indirgenmezse modeller arası ortalama R farkı bir eksenin ölçüsü
olmaktan çıkar ve iki ayrı uygulamanın farkı hâline gelir; oysa model 12 tam da model
11'in NULL HİPOTEZİDİR ve model 15 de model 12'nin çıkış yönetimi eklenmiş İKİZİDİR.

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

**`rng_identity` neden var.** Çekiliş `random_seed`, `as_of` ve model KİMLİĞİ ile
tohumlanır. Kimlik varsayılan olarak modelin kendi adıdır: model 11 ile model 12 aynı
turda BAĞIMSIZ çekiliş görmelidir, yoksa aradaki fark adaptasyonun değil tesadüfün ölçüsü
olurdu. Model 15'te ise tam tersi gerekir — model 12 ile AYNI kimliği kullanır, böylece
ikisi her turda aynı kolu ve aynı sembolü seçer ve aralarındaki ortalama R farkı yalnızca
çıkış yönetiminden gelir (eşleştirilmiş deney). Hangi durumun geçerli olduğu ölçülen
eksene bağlıdır, bu yüzden kimlik bir alan olarak durur ve alt sınıf gerekçesiyle
değiştirir.

**Tarama sayımı (`take_survey`) bir override noktası DEĞİLDİR.** Gövde onu tek kopya
olarak uygular ve her scalp modeli aynı sayımı alır; alt sınıf `take_survey`i EZMEZ,
yalnızca `_note_survey` ile kendi teşhis anahtarlarını EKLEYEBİLİR (`scalp_vol`ün rejim
kapısı böyle sayar). Sayım `rejections`/`emitted` ile aynı statüde bir DENETİM İZİDİR
(kural 15): hangi kolun seçileceğini, hangi sembolün çekileceğini ve sıralarını
DEĞİŞTİRMEZ — bu yüzden yukarıdaki "her override noktası bir eksene karşılık gelmeli"
kuralının konusu değildir, o kural DAVRANIŞI değiştiren noktalar içindir.

Sayımın birimi **kol × eleme sebebi**dir ve sebepler AYRIKTIR: her kol için sebeplerin
toplamı o barda taranan sembol sayısına (`taranan`) eşittir. Sağlama olmadan bir kolun
sessizce düşmesi görünmezdi — `momentum_burst`ün ölü olduğu tam da bu yüzden iki backtest
sonra fark edildi (karar 34/48). Ön-kayıt: docs/backtest.md > 6i.

Rollere dikkat: bu modül boyut/komisyon/bakiye hesaplamaz (kural 1/2/3/7) ve deftere
yazmaz. Kol mantığı `strategies/scalp/arms.py`'de, gösterge matematiği
`core/indicators.py`'de, çıkış yönetiminin tanımı `strategies/exit_management.py`'dedir.
"""

from __future__ import annotations

import logging
import random
from abc import abstractmethod
from typing import Any, Mapping, Sequence

from core.config import get_setting, load_config
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
from strategies.exit_management import ExitManagement
from strategies.scalp.arms import ARM_NAMES, ArmParams, ArmSetup, scan_all
from strategies.time_stop import CONFIG_KEY as TIME_STOP_KEY, TimeStop

logger = logging.getLogger(__name__)

SIGNALS_PER_ROUND = 1

# --- Tarama sayımının sebep kodları (docs/backtest.md > 6i > 6). AYRIKTIRLAR: bir kol
# için `Σ sebep == SCANNED`. Anahtarlar tur raporuna `<kol>/<sebep>` olarak düşer;
# `SCANNED` ise kolsuzdur, çünkü paydadır ve her kol için aynıdır.
SCANNED = "taranan"
NO_SETUP = "kurulum_yok"       # kol o sembolde hiç kurulum üretmedi
MIN_STOP = "stop_tabani"       # stop mesafesi < scalp.min_stop_pct
MIN_REWARD = "hedef_stop"      # hedef/stop < scalp.min_reward_risk
REGIME_GATE = "rejim_kapisi"   # ek rejim kapısı eledi (yalnızca regime_filter'lı modelde)
QUOTA = "kota"                 # tüm kapıları geçti, barda tek sinyal oynandığı için kaldı
SELECTED = "secildi"           # oynanan kurulum
ARM_ERROR = "kol_hatasi"       # kol patladı; "tez tutmadı" ile aynı hücreye yazılamaz


def survey_key(arm: str, reason: str) -> str:
    """Tur raporundaki sayım anahtarı. Tek tanım: sağlamayı yazan test de bunu kullanır."""
    return f"{arm}/{reason}"


class ScalpModel(Strategy):
    """Scalp modellerinin ortak gövdesi; her override noktası bir ÖLÇÜLEN eksene karşılıktır."""

    allowed_directions: list[Direction] = ["long", "short"]
    # Turluk çekilişin kimliği. None = modelin kendi adı (bağımsız çekiliş). Bir alt
    # sınıf başka bir modelin adını yazarsa ikisi AYNI kurulumu seçer — eşleştirilmiş
    # kıyas isteyen model bunu bilinçli olarak yapar (bkz. modül docstring'i).
    rng_identity: str | None = None
    # Üç aşamalı çıkış yönetimi; None = kapalı (model 11 ve 12'nin sözleşmesi).
    exit_management: ExitManagement | None = None
    # Zaman stop'u DEĞERİNİN config anahtarı. Kural tek kopyadır
    # (`strategies/time_stop.py`); ayrışan yalnızca sınırın kaç bar olduğudur ve bu,
    # `scalp_fixed ↔ scalp_patient` ekseninin ölçtüğü tek değişkendir.
    time_stop_key: str = TIME_STOP_KEY

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
        # Zaman stop'u tek kopyadır (strategies/time_stop.py): model 14 bu gövdeden
        # türemiyor ama aynı kuralı okuyor — iki uygulama, sessiz bir dördüncü
        # değişken demekti (bkz. o modülün docstring'i).
        self._time_stop = TimeStop.from_config(settings, key=self.time_stop_key)
        self._seed = int(get_setting(settings, "random_seed"))
        # Son taramanın eleme sayımı (denetim izi, kural 15). Her `generate_signals`
        # çağrısının BAŞINDA sıfırlanır: patlayan bir çağrının yarım sayımı motor
        # tarafından hiç okunmaz (motor `take_survey`i yalnızca başarılı çağrıdan sonra
        # çağırır) ve bir sonraki taramaya SIZMAZ.
        self._survey: dict[str, int] = {}

    # ------------------------------------------------------------------ #
    # Açılış
    # ------------------------------------------------------------------ #
    def generate_signals(
        self,
        market: MarketData,
        peer_signals: Mapping[str, tuple[Signal, ...]] | None = None,
    ) -> list[Signal]:
        """O barın tek sinyali (varsa). Yan ürün: kol × eleme sebebi TARAMA SAYIMI.

        Sayım akışa hiç dokunmaz — kollar, kapılar, çekiliş ve seçim sayım eklenmeden
        önceki hâliyle aynıdır (test: `tests/test_scalp_survey.py`). Yalnızca her adımda
        kaç kurulumun düştüğü kaydedilir ki "sinyal neden hiç üretilmedi" sorusunun cevabı
        tur raporunda dursun (docs/backtest.md > 6i > 6).
        """
        self._survey = {}
        scan = scan_all(market, self._params)
        self._note_survey(SCANNED, scan.examined)

        available: dict[str, list[ArmSetup]] = {}
        for arm, setups in scan.setups.items():
            if arm in scan.failed:
                # Patlayan kol o barda HİÇBİR sembolü değerlendiremedi; sağlamanın
                # (Σ sebep == taranan) payını bu sebep taşır. `kurulum_yok`a yazmak,
                # "tez tutmadı" ile "kol patladı"yı aynı hücreye koymak olurdu.
                self._note_survey(survey_key(arm, ARM_ERROR), scan.examined)
                continue
            self._note_survey(survey_key(arm, NO_SETUP), scan.examined - len(setups))
            gated = self._gated(arm, setups)
            kept = self.regime_filter(gated, market)
            self._note_survey(survey_key(arm, REGIME_GATE), len(gated) - len(kept))
            if kept:
                available[arm] = kept

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

        # Kapılardan geçmiş ama oynanmayan her kurulum `kota`dır — "başka kol seçildi" ile
        # "bu kolda başka sembol çekildi" ayrı ayrı sayılmaz: ikisi de aynı şeyi söyler
        # (oynanabilirdi, barda tek sinyal oynandığı için oynanmadı) ve ayırmak sayımı
        # seçim mekaniğinin bir kopyasına çevirirdi. Seçimin kendisi `emitted`dadır.
        played = len({id(setup) for setup in chosen})
        for name, kept in available.items():
            selected = played if name == arm else 0
            self._note_survey(survey_key(name, SELECTED), selected)
            self._note_survey(survey_key(name, QUOTA), len(kept) - selected)

        # YÖN en sonda belirlenir: sayım ve çekiliş kolun kendi yön iddiası üzerinden
        # yapılmıştır, yani `direction_policy` hangi kurulumun oynanacağını DEĞİŞTİREMEZ
        # (model 16 ↔ 23 ekseninin şartı — bkz. docs/backtest.md > 6i > 2).
        return [
            self._signal(self.direction_policy(setup, market=market), posterior=posterior)
            for setup in chosen
        ]

    def _note_survey(self, key: str, count: int) -> None:
        """Tarama sayımına ekler. Sıfır YAZILMAZ: bilgi taşımaz ve raporu şişirirdi.

        `protected` olmasının sebebi alt sınıfın kendi teşhis anahtarını ekleyebilmesidir
        (`scalp_vol`ün rejim kapısı). Bu bir override noktası DEĞİLDİR: yazılan sayı
        hiçbir sinyali, sırayı ya da çekilişi etkilemez.
        """
        if count:
            self._survey[key] = self._survey.get(key, 0) + int(count)

    def take_survey(self) -> Mapping[str, int] | None:
        """Son taramanın eleme sayımı; okununca SIFIRLANIR (motor bar bazında toplar).

        Tek kopyadır ve alt sınıfta ezilmez: `scalp_fixed`, `scalp_patient` ve
        `scalp_coinflip` aynı sayımı alır. İki uygulama, aynı eksenin iki tarafında iki
        farklı "taranan sembol" tanımı demekti.
        """
        survey, self._survey = dict(self._survey), {}
        return survey or None

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
                self._note_survey(survey_key(arm, MIN_STOP), 1)
                continue
            if setup.reward_risk < self._min_reward_risk:
                logger.info(
                    "%s %s/%s: kurulum atlandı, hedef/stop %.2f < çıta %.2f",
                    self.name, arm, setup.symbol, setup.reward_risk, self._min_reward_risk,
                )
                self._note_survey(survey_key(arm, MIN_REWARD), 1)
                continue
            kept.append(setup)
        return kept

    def _signal(self, setup: ArmSetup, *, posterior: float) -> Signal:
        """Kurulumu sinyale çevirir; `reason` kuyruğuna kol ve posterior etiketlenir.

        Etiketler ayrıştırılabilir olmak zorundadır (bkz. core/tags.py): kol bazlı kırılım
        (`docs/data/metrics_scalp.json`) bu kuyruktan okunur. Etiketsiz bir satır kırılımı
        sessizce eksiltirdi, bu yüzden okuyan taraf etiketi bulamazsa hata fırlatır.

        Çıkış yönetimi bildirilmişse alanları buraya açılır ve gerekçe metnine de yazılır:
        aynı kolun aynı kurulumu iki modelde farklı yönetilecekse, hangi satırın hangi
        kuralla kapandığı defterden okunabilmelidir.
        """
        managed = self.exit_management
        return Signal(
            symbol=setup.symbol,
            direction=setup.direction,
            stop_price=setup.stop_price,
            take_profits=(TakeProfit(price=setup.target_price, fraction=1.0),),
            **({} if managed is None else managed.signal_fields()),
            reason=format_tags(
                f"{setup.detail}; stop {setup.stop_distance_pct * 100:.2f}% "
                f"({setup.stop_price:.6g}), hedef {setup.target_price:.6g} "
                f"({setup.reward_risk:.2f}R), {self._time_stop.describe()}"
                + ("" if managed is None else f"; {managed.describe()}"),
                arm=setup.arm,
                post_r=posterior,
            ),
        )

    def _round_rng(self, market: MarketData) -> random.Random:
        """Tur ve model başına bağımsız RNG.

        Tohum sabittir ama `as_of` ve model KİMLİĞİ ile karışır: aynı `as_of` ile yeniden
        koşulan tur birebir aynı seçimi üretir (tekrarlanabilirlik), iki model ise aynı
        turda bağımsız çekiliş görür — model 12'nin eşit ağırlıklı çekilişi model 11'in
        kararının kopyası olsaydı, aradaki fark adaptasyonun değil tesadüfün ölçüsü olurdu.

        Kimlik varsayılan olarak modelin adıdır; `rng_identity` ile başka bir modelin adına
        bağlanması EŞLEŞTİRİLMİŞ kıyas içindir (bkz. modül docstring'i ve model 15).
        """
        identity = self.rng_identity or self.name
        return random.Random(f"{self._seed}:{market.as_of.isoformat()}:{identity}")

    # ------------------------------------------------------------------ #
    # Çıkış: zaman stop'u
    # ------------------------------------------------------------------ #
    def manage_positions(
        self, market: MarketData, positions: list[Position]
    ) -> list[ExitInstruction]:
        """16 bardan uzun süredir açık olan pozisyonları piyasa fiyatından kapatır.

        Kuralın kendisi `strategies/time_stop.py`dedir: model 14 de aynı kuralı okur ve
        bu gövdeden türemez, yani kopyalanmış bir uygulama iki modelin arasına ölçülmeyen
        bir fark koyardı.
        """
        return self._time_stop.instructions(market, positions)

    # ------------------------------------------------------------------ #
    # Alt sınıfın tek işi
    # ------------------------------------------------------------------ #
    def regime_filter(
        self, setups: Sequence[ArmSetup], market: MarketData
    ) -> list[ArmSetup]:
        """Ev kapılarından GEÇMİŞ kurulumlara uygulanan ek REJİM kapısı.

        Varsayılan: hiçbir şey eleme. Bu, `choose_arm`/`exit_management`/`rng_identity`/
        `time_stop_key` ile aynı statüde bir override noktasıdır ve aynı kurala tabidir:
        **bir alt sınıf burayı yalnızca ÖLÇÜLEN bir eksene karşılık geliyorsa
        değiştirebilir.** Fark tek bir noktaya indirgenmezse modeller arası ortalama R
        farkı bir eksenin ölçüsü olmaktan çıkar.

        Kapıdan SONRA çağrılır, çünkü ölçülen şey "rejim kapısının ev kapılarının ÜSTÜNE
        ne kattığı"dır; önce çağrılsaydı iki kapının sırası sonucu etkiler ve eksen
        "rejim + sıralama"nın toplam farkı olurdu.

        `market` verilir çünkü rejim KESİTSEL olabilir (o bardaki tüm sembollere göre);
        tek bir kurulumun kendi alanlarından okunamaz.
        """
        return list(setups)

    def direction_policy(self, setup: ArmSetup, *, market: MarketData) -> ArmSetup:
        """Oynanacak kurulumun YÖNÜNÜ belirler. Varsayılan: kolun yön iddiası korunur.

        `choose_arm`/`exit_management`/`rng_identity`/`time_stop_key`/`regime_filter` ile
        aynı statüde bir override noktasıdır ve aynı kurala tabidir: **bir alt sınıf
        burayı yalnızca ÖLÇÜLEN bir eksene karşılık geliyorsa değiştirebilir.** Bugün
        karşılığı `scalp_patient` (16) ↔ `scalp_coinflip` (23) eksenidir ve cevapladığı
        soru şudur: *kolun YÖN iddiası bilgi taşıyor mu, yoksa taşıdığı şey yalnızca
        "oynanabilir bir kurulum" mu?* (ön-kayıt: docs/backtest.md > 6i).

        **SEÇİMDEN SONRA çağrılır ve seçimi değiştiremez.** Kol çekilişi, sembol çekilişi
        ve tarama sayımı kolun kendi yön iddiası üzerinden yapılmıştır; yön kararı o
        akışın önüne geçseydi kontrol, `scalp_patient` ile aynı kurulumları seçmez ve
        eşleştirilmiş deney bozulurdu — fark yönün değil tesadüfün ölçüsü olurdu.

        **Mesafeler KORUNMALIDIR.** Yönü çeviren bir uygulama stop/hedef mesafelerini
        değiştirirse model başka bir maliyet ölçeğinde koşar, ⚠B bandı yanar ve
        `cost_per_r` kıyaslanamaz olur (kural 14). Geometrisi hazırdır ve saf bir
        fonksiyondur: `strategies/scalp/arms.py::reflect`.

        `market` verilir çünkü karar bara bağlı olabilir (yazı-tura `as_of` ile tohumlanır);
        tek bir kurulumun kendi alanlarından okunamaz — `regime_filter`ın aynı gerekçesi.
        """
        return setup

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

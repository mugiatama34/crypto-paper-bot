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
from strategies.scalp.arms import ARM_NAMES, ArmParams, ArmScan, ArmSetup, propose_all
from strategies.time_stop import CONFIG_KEY as TIME_STOP_KEY, TimeStop

logger = logging.getLogger(__name__)

SIGNALS_PER_ROUND = 1

# Tarama sayımının sebep kodları. Her kol için bir sembol TAM OLARAK BİRİNE düşer; bu
# ayrıklık `Σ sebep == taranan sembol` değişmezinin kendisidir ve testle sabittir
# (tests/test_scalp_survey.py). Anahtar biçimi `<kol>:<sebep>` — kol olmadan sayım
# okunamaz, çünkü ölü kolun teşhisi tam olarak "HANGİ kolun kurulumları nerede öldü"dür.
SURVEY_NO_SETUP = "kurulum_yok"
SURVEY_ARM_ERROR = "kol_hatasi"
SURVEY_STOP_FLOOR = "stop_tabani_alti"
SURVEY_REWARD_RISK = "hedef_stop_alti"
SURVEY_REGIME = "rejim_kapisi"
SURVEY_QUOTA = "kota_disi"
SURVEY_CHOSEN = "secildi"


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
        # Tarama sayımı (kural 15'in denetim izi). Bar bazında birikir, `take_survey`
        # okuyup sıfırlar; motor her BARDAN sonra okur (core/engine.py).
        self._scan_counts: dict[str, int] = {}

    # ------------------------------------------------------------------ #
    # Açılış
    # ------------------------------------------------------------------ #
    def generate_signals(
        self,
        market: MarketData,
        peer_signals: Mapping[str, tuple[Signal, ...]] | None = None,
    ) -> list[Signal]:
        scan = ArmScan()
        proposals = propose_all(market, self._params, scan=scan)
        available: dict[str, list[ArmSetup]] = {}
        for arm, setups in proposals.items():
            # Patlayan kol da BOŞ liste döndürür: ikisini aynı sebebe yazmak bir arızayı
            # olağan bir eleme gibi gösterirdi (bkz. `propose_all`ın `scan` parametresi).
            if arm in scan.failed:
                self._tally(arm, SURVEY_ARM_ERROR, scan.examined)
            else:
                self._tally(arm, SURVEY_NO_SETUP, scan.examined - len(setups))
            passed = self._gated(arm, setups)
            kept = self.regime_filter(passed, market)
            self._tally(arm, SURVEY_REGIME, len(passed) - len(kept))
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
        # Kapıların hepsini geçip de oynanmayan kurulum bir ELEME DEĞİL, bir KOTA
        # sonucudur (barda tek sinyal) ve ayrı sayılır: ikisini birleştirmek "kurulum
        # yoktu" ile "kurulum vardı ama sıra ona gelmedi"yi aynı hücreye yazardı.
        # Kimlik `id()` ile taşınır — `ArmSetup` frozen'dır ama eşitliği alan bazlıdır ve
        # aynı barda iki kol aynı sembolde aynı geometriyi üretebilir.
        played = {id(setup) for setup in chosen}
        for candidate_arm, kept in available.items():
            for setup in kept:
                self._tally(
                    candidate_arm,
                    SURVEY_CHOSEN if id(setup) in played else SURVEY_QUOTA,
                )
        return [self._signal(setup, posterior=posterior) for setup in chosen]

    def _tally(self, arm: str, reason: str, count: int = 1) -> None:
        """Sayımı `<kol>:<sebep>` anahtarıyla biriktirir. Sıfır/negatif yazılmaz.

        Negatif bir sayı yalnızca `regime_filter` kendisine verilenden FAZLA kurulum
        döndürürse oluşur — sözleşme gereği olamaz, ama sessizce eksi bir sayı
        raporlamaktansa yazmamak doğrudur: ayrıklık testi (Σ = taranan sembol) o
        durumda zaten kırmızıya döner.
        """
        if count <= 0:
            return
        key = f"{arm}:{reason}"
        self._scan_counts[key] = self._scan_counts.get(key, 0) + count

    def _gated(self, arm: str, setups: Sequence[ArmSetup]) -> list[ArmSetup]:
        """Stop tabanı ve hedef/stop kapısı. Her eleme GEREKÇESİYLE loglanır.

        Sessiz eleme, modelin işlem sayısını denetlenemez biçimde düşürmesi demek olurdu
        (kural 14'ün "atlama sessiz olamaz" şartı): kolun hiç kurulum üretmemesi ile
        kurulumlarının kapıda elenmesi logda ayırt edilebilir olmalı.

        Eleme ayrıca SAYILIR (`take_survey`): log satırı bir turu açıklar, sayım ise
        aylara yayılan bir deseni. `momentum_burst`ün hiç tetiklenmediği tam olarak bu
        yüzden iki backtest sonra öğrenildi — logda her turda yazıyordu, hiçbir yerde
        toplanmıyordu (karar 34).
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
                self._tally(arm, SURVEY_STOP_FLOOR)
                continue
            if setup.reward_risk < self._min_reward_risk:
                logger.info(
                    "%s %s/%s: kurulum atlandı, hedef/stop %.2f < çıta %.2f",
                    self.name, arm, setup.symbol, setup.reward_risk, self._min_reward_risk,
                )
                self._tally(arm, SURVEY_REWARD_RISK)
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
                **self.extra_tags(setup),
            ),
        )

    def extra_tags(self, setup: ArmSetup) -> Mapping[str, object]:
        """Alt sınıfın `reason` kuyruğuna ekleyeceği DENETİM İZİ etiketleri.

        **Bir override NOKTASI değildir ve "her override bir eksene karşılık gelir"
        kuralına tabi değildir** (bkz. modül docstring'i): buradan dönen şey sinyalin
        hiçbir alanını — yön, stop, hedef, boyut, sıra — etkilemez, yalnızca deftere
        yazılan gerekçe metnine bir `| anahtar=değer` çifti ekler. `take_survey` ile aynı
        statüde bir denetim izidir (kural 15).

        Neden gerekli: `scalp_coinflip` kurulumun yönünü çevirdiğinde "çevrildi mi"
        bilgisi defterden OKUNAMAZ olurdu — çevrilmiş bir long ile kolun kendi short'u
        satırda birbirinin aynısı görünür. Alternatif `_signal`ı alt sınıfta baştan
        yazmaktı; o yol sinyal kurulumunu ikinci kez yazmak, yani tam olarak bu gövdenin
        engellemek için var olduğu şeydi.

        `arm` ve `post_r` anahtarları REZERVEDİR: kırılımlar onları okur (core/tags.py)
        ve üzerlerine yazmak kol tablosunu sessizce bozardı.
        """
        return {}

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
    # Denetim izi
    # ------------------------------------------------------------------ #
    def take_survey(self) -> Mapping[str, int] | None:
        """Son `generate_signals` çağrısının KOL × SEBEP sayımı; okununca sıfırlanır.

        **Denetim izidir (kural 15):** ölçüme girmez, sinyalleri, sıralarını, çekilişi ve
        dolumları DEĞİŞTİRMEZ. `rejections` "emir neden dolmadı"yı sayar; bu "sinyal neden
        hiç üretilmedi"yi — ikisi turun ayrı aşamalarıdır.

        **Ayrık sayım:** her kol için sebeplerin toplamı o barda taranan sembol sayısına
        eşittir (test: `tests/test_scalp_survey.py`). `vwap` modellerinin
        `Σ counts == examined` sözleşmesinin aynısı; kümülatif kova (`extensions`) YOKTUR,
        çünkü bu kolların ölçeklenecek tek bir sürekli değişkeni yok.

        ⚠ **`kurulum_yok` KOLUN İÇİNİ açmaz.** Kolun kendi koşullarından hangisinin
        tutmadığı (funding serisi yok mu, sıçrama tabanın altında mı, VWAP hesaplanamadı
        mı) bu sayımda GÖRÜNMEZ; hepsi tek bir kovaya düşer. Bu bilinçli bir kapsam
        sınırıdır: beş kolun iç koşullarını ayrı ayrı etiketlemek `arms.py`yi baştan
        yazmak demekti ve o ayrı bir karardır. Ön-kayıt (docs/backtest.md > 6h) V1'e tam
        da bu yüzden TAHMİN YAZMADI — `kurulum_yok` baskın çıkarsa cevap "daha derin
        sayım gerekiyor" olacaktır, "sebep yok" değil.
        """
        survey, self._scan_counts = dict(self._scan_counts), {}
        return survey or None

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

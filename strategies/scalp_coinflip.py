"""KONTROL — `scalp_patient`in İKİZİ, tek farkı YÖNÜN adil bir yazı-turayla belirlenmesi.

**Ölçtüğü eksen:** *kolun YÖN iddiası bilgi taşıyor mu?* Ön-kayıt: docs/backtest.md > 6h.

**Neden var (karar 54).** `layers.scalp.models` listesinde kontrol de çıpa da yoktu.
`core/metrics.py::_flags_for`de `edge` beş koşulun VE'sidir ve kümede kontrol/çıpa
olmadığında üçü birden DÜŞER (marj, güven aralığı, çıpa) — yani scalp katmanında `edge`
bayrağı fiilen `avg_r > 0` demekti. `scalp_patient` örneklem kapısını geçtiği anda
`passed: true` görünecekti ve bu, `docs/backtest.md > 4`ün çıtasının karşılığı
OLMAYACAKTI.

**Tasarımı bozulamaz** (`random_ctrl` ve `xsec_random`ın aynı sözü). Buraya eklenecek her
filtre — "şu sembolde oynama", "şu rejimde çevirme", "kötü kolu ele" — kontrolü sessizce
bir stratejiye çevirir ve o andan itibaren `scalp_patient`in farkının NEYE KARŞI ölçüldüğü
bilinmez hâle gelir. Adil yazı-tura burada bir parametre değil, tanımın kendisidir.

**Neden `random_ctrl` DEĞİL.** Base katmanının kontrolü scalp geometrisini okumaz: kendi
stop mantığını taşır, %1 tabanını ve 1.5R kapısını görmez. Katmana eklenseydi
`avg_stop_distance_pct` ayrışır, kural 14'ün bandı (⚠B) yanar ve `cost_per_r`
kıyaslanamaz hâle gelirdi — yani kontrol, kıyas ZEMİNİ olmaktan çıkıp ayrı bir maliyet
ölçeğinde koşan ikinci bir model olurdu. Yazı-turada bu risk YOKTUR ve bu bir tercih
değil bir ÖZDEŞLİKTİR: yön çevrilse de stop mesafesi aynı sayıdır.

**Yön çevrildiğinde geometri YANSITILIR, yeniden KURULMAZ.** Stop ve hedef *mesafeleri*
girişin öbür tarafına aynen taşınır, yani `stop_distance_pct` ve `reward_risk` çevirme
işleminden ETKİLENMEZ. Ön-kayıtlı S1 sağlaması tam olarak budur (§6h): iki modelin stop
mesafesi bağıl olarak %1'den fazla ayrışırsa kontrol bozulmuştur ve M1/M2 OKUNMAZ.

⚠ **KABUL EDİLEN SAPMA — yansıtılan hedef yapısal engele dayanmaz.** `arms.py::
_maybe_setup` hedefi "projeksiyon ile kolun yapısal engelinin YAKIN olanı" yapar;
çevrilmiş yönde o mesafenin yapısal bir karşılığı YOKTUR. Alternatif (engeli ters yön için
yeniden hesaplamak) `reward_risk`i değiştirir ve S1'i yapısal olarak imkânsız kılardı.
Sapma bilinçlidir ve doğru yöndedir: ölçülen şey "yön iddiası bilgi taşıyor mu" olduğuna
göre **kontrolün bir tezi olmamalıdır** — hedefi bir tezden değil bir simetriden gelir.

**Çekiliş PAYLAŞILIR** (`rng_identity` mirasla `scalp_fixed`). CLAUDE.md'nin kuralı:
*çekiliş, ölçülmeyen eksende PAYLAŞILIR, ölçülen eksende BAĞIMSIZDIR.* Burada ölçülen
eksen YÖN; kol ve sembol seçimi ölçülmüyor, bu yüzden kontrol ile `scalp_patient` her
barda AYNI kurulumu görür ve aradaki fark yalnızca yöndendir (eşleştirilmiş deney —
model 12 ↔ 15'in deseni). `xsec_random`da tersi geçerliydi: orada ölçülen eksen seçimin
kendisiydi.

**Yazı-tura AYRI bir RNG akışından çekilir** ve bu zorunludur: `_round_rng`in akışından
çekilseydi akış ilerler, `choose_arm` ve `rng.choice` başka değerler görür ve kontrol
`scalp_patient`ten BAŞKA bir kurulum seçerdi — eşleştirme, tam da onu kurmak için eklenen
şey tarafından bozulurdu.

Rollere dikkat: boyut/komisyon/bakiye hesaplanmaz (kural 1/2/3/7), deftere yazılmaz
(kural 1). Kollar, kapılar, geometri, zaman stop'u ve kol seçimi `ScalpPatient`ten MİRAS
ALINIR — bu dosyada yalnızca yön kuralı vardır.
"""

from __future__ import annotations

import logging
import random
from typing import Mapping, Sequence

from strategies.base import MarketData
from strategies.scalp.arms import ArmSetup
from strategies.scalp_patient import ScalpPatient

logger = logging.getLogger(__name__)


def flip(setup: ArmSetup) -> ArmSetup:
    """Yönü çevirir; stop ve hedef MESAFELERİNİ girişin öbür tarafına yansıtır.

    Mesafeler girişten ölçülür ve aynen korunur, yani `stop_distance_pct` ve `reward_risk`
    değişmez — kontrolün maliyet ölçeği tanım gereği kaynağınınkiyle aynı kalır (§6h > S1).

    `detail` metnine dokunulmaz: o, kurulumun HANGİ gözlemden doğduğunu anlatır ve gözlem
    çevrilmedi. Çevrildiği bilgisi ayrı bir etikette (`coin=flipped`) durur — gerekçe
    metnini yeniden yazmak, defterde kolun tezini yanlış anlatmak olurdu.
    """
    entry = setup.entry_price
    return ArmSetup(
        arm=setup.arm,
        symbol=setup.symbol,
        direction="short" if setup.direction == "long" else "long",
        entry_price=entry,
        stop_price=entry + (entry - setup.stop_price),
        target_price=entry + (entry - setup.target_price),
        detail=setup.detail,
    )


class ScalpCoinflip(ScalpPatient):
    name = "scalp_coinflip"

    def __init__(self, **kwargs: object) -> None:
        super().__init__(**kwargs)  # type: ignore[arg-type]
        # O BARIN yazı-tura sonuçları: sembol -> yön korundu mu. `extra_tags` etiketi
        # buradan okur, yeniden çekmez — etiket ile UYGULANAN kararın aynı olduğu böylece
        # bir tohum eşitliğine değil tek bir kayda dayanır.
        self._coin: dict[str, bool] = {}
        self._coin_bar: object = None

    def regime_filter(
        self, setups: Sequence[ArmSetup], market: MarketData
    ) -> list[ArmSetup]:
        """Eleme YAPMAZ; kapılardan geçmiş her kurulumun yönünü yazı-turayla belirler.

        **Neden `regime_filter` içinde.** Bu, `ScalpModel`in kapılardan SONRA çağırdığı tek
        kancadır — tam olarak ihtiyaç duyulan yer: yazı-tura ev kapılarının (%1 stop tabanı,
        1.5R) ÖNÜNE konsaydı, çevirme `reward_risk`i korusa bile kapıya giren küme
        `scalp_patient`inkiyle aynı olmazdı ve eşleştirme sinyal düzeyinde bozulurdu.

        Kanca bir ELEME noktası olarak tanımlıdır ve burada hiçbir şey elenmiyor: dönen
        liste girenle AYNI UZUNLUKTADIR. Ortak sayım (`take_survey`) bu yüzden bu modelde
        `rejim_kapisi` sebebine hiç satır yazmaz — doğru olan da budur, çünkü bu model bir
        rejim kapısı taşımıyor.

        Override noktası kuralına uyar (`ScalpModel` docstring'i): karşılığı ÖLÇÜLEN bir
        eksen vardır — *kolun yön iddiası bilgi taşıyor mu?*
        """
        if self._coin_bar != market.as_of:
            # Yeni bar: hafıza sıfırlanır. Taşınsaydı `extra_tags` bir önceki barın
            # kararını etiketleyebilirdi — denetim izi, izlediği şeyden ayrışırdı.
            self._coin = {}
            self._coin_bar = market.as_of
        flipped = 0
        result: list[ArmSetup] = []
        for setup in setups:
            heads = self._coin.get(setup.symbol)
            if heads is None:
                heads = self._heads(market, setup)
                self._coin[setup.symbol] = heads
            if heads:
                result.append(setup)
                continue
            result.append(flip(setup))
            flipped += 1
        if setups:
            logger.info(
                "%s: %d kurulumun %d tanesinin yönü çevrildi (adil yazı-tura)",
                self.name, len(setups), flipped,
            )
        return result

    def extra_tags(self, setup: ArmSetup) -> Mapping[str, object]:
        """`coin=same|flipped` — kurulumun yönü çevrildi mi (denetim izi, kural 15).

        Etiket olmadan bu soru defterden CEVAPLANAMAZ: çevrilmiş bir long ile kolun kendi
        short'u satırda birbirinin aynısı görünür. Ölçüt kurulumun ŞU ANKİ yönü değil,
        yazı-turanın kendisidir — çevirme `regime_filter`da olup bitti ve burada yalnızca
        o barın kaydı okunuyor.

        Kayıt bulunamazsa `TagError` DEĞİL, açık bir `KeyError` uygundur — `_signal`
        yalnızca `regime_filter`dan geçmiş kurulumlar için çağrılır, yani eksik bir kayıt
        bir veri durumu değil programlama hatasıdır (kural 8'in aynı ayrımı) ve modelin
        o turu boş geçer.
        """
        return {"coin": "same" if self._coin[setup.symbol] else "flipped"}

    def _heads(self, market: MarketData, setup: ArmSetup) -> bool:
        """Adil yazı-tura. True = kolun yönü korunur.

        **Tohum kol/sembol çekilişinin akışından BAĞIMSIZDIR** (bkz. modül docstring'i) ve
        her çağrıda sıfırdan kurulur: `random.Random(...).random()` tek bir çekiliştir,
        yani sıra bağımlılığı yoktur ve aynı bar yeniden koşulduğunda aynı sonucu verir
        (`xsec_random`ın bar bazlı tohumlamasıyla aynı gerekçe).

        **Sembol tohuma GİRER:** aynı barda birden çok kurulum fiyatlanır ve sembol
        olmasaydı hepsi aynı yazı-turayı görürdü — o bar için kontrol bilgisiz değil
        TEK YÖNLÜ olurdu.

        **Kol tohuma GİRMEZ:** bir sembolde o barda en fazla bir kolun kurulumu oynanır ve
        kol adını eklemek, aynı sembolün yazı-turasını hangi kolun tetiklediğine
        bağlardı — yani çevirme kararı kol seçimine sızardı.
        """
        stream = random.Random(
            f"{self._seed}:{market.as_of.isoformat()}:{self.name}:{setup.symbol}"
        )
        return stream.random() < 0.5

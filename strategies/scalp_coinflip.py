"""MODEL 23 — `scalp_patient`in KONTROLÜ: aynı kurulum, yönü yazı-turayla seçilmiş.

Ön-kayıt: **docs/backtest.md > 6i** (koşudan ve bu dosyadan ÖNCE commit edildi).

**Cevapladığı soru tek:** *Kolun YÖN iddiası bilgi taşıyor mu, yoksa taşıdığı şey yalnızca
"oynanabilir bir kurulum" mu?* `scalp_patient` ile aralarındaki ortalama R farkı, kabul
çıtasının C-2 koşulunun (kontrolü ≥ 0.15R marjla geçmek) tam olarak ölçüsüdür.

**Neden gerekiyordu.** Scalp katmanının `models` listesinde kontrol modeli YOKTU
(`random_ctrl` base katmanındadır), bu yüzden `core/metrics.py::acceptance_flags` marj,
bootstrap güven aralığı ve çıpa koşullarını düşürüyordu: katmanda `edge` fiilen
`avg_r > 0`'a inmişti ve `scalp_patient` örneklem kapısını geçtiği anda `passed: true`
görünecekti. Bu model o kapıyı ölçülebilir kılar. **C-3 hâlâ değerlendirilemez** ve bu
model onu AÇMAZ — scalp'e çıpa eklemek ayrı bir karardır (docs/backtest.md > 4).

**Yarışmacıdır** (`is_replica=False`, `is_benchmark=False`): `random_ctrl` ve
`xsec_random`ın statüsüyle birebir aynı gerekçe — kontrol, yarışmacılarla AYNI
boyutlandırma, AYNI maliyet ve AYNI limitlerle koşmazsa aralarındaki fark sinyalin değil
koşulların ölçüsü olur.

**Neden `random_ctrl` bu katmanın kontrolü olamazdı.** O model scalp geometrisini HİÇ
okumaz: kendi stop kuralı vardır, `scalp.*` bloğunu (5×ATR, %1 taban, 1.5R) görmez ve 15
dakikalık barın ölçeğinde kurulmamıştır. İki farklı stop ölçeğini aynı C-2 farkında
toplamak ⚠B bandını yakar ve `cost_per_r`yi kıyaslanamaz kılar — yani fark "yönün ölçüsü"
olmaktan çıkar, "iki maliyet ölçeğinin farkı" olur (docs/backtest.md > 6h > EK-1'in aynı
itirazı; orada `scalp_coinflip` tam da bu yüzden WAVE'in kontrolü olmaktan reddedilmişti
ve o red geçerlidir — `scripts/backtest_wave.py` kontrolünü açıkça geçirmeye devam eder).

**AYRIŞAN TEK ŞEY YÖNDÜR.** Beş kol, ev kapıları (%1 stop tabanı, 1.5R), 5×ATR stop
geometrisi, 100 barlık zaman stop'u, kol seçimi (`choose_arm`) ve çekiliş kimliği
(`rng_identity`) `ScalpPatient`ten **MİRAS ALINIR, kopyalanmaz** (`scalp_patient`in
`ScalpFixed`ten türemesiyle aynı desen): kopyalanan bir kural bir gün sessizce ayrışır ve
fark "yönün ölçüsü" olmaktan çıkar. İki model her barda AYNI kolu ve AYNI sembolü seçer —
eşleştirilmiş deneydir.

**Yansıtma mesafeyi KORUR.** "Ters" gelen kurulumda stop ve hedef, girişin öbür tarafına
aynı MESAFEYLE yansıtılır (`strategies/scalp/arms.py::reflect`), yani `|giriş − stop|` ve
`|hedef − giriş|` değişmez; `stop_distance_pct` ve `reward_risk` tanım gereği aynı kalır.
Bu bir tercih değil ölçümün şartıdır: mesafe değişseydi kontrol başka bir maliyet
ölçeğinde koşar, ⚠B yanar ve `cost_per_r` kıyaslanamaz olurdu.

Denetimi İKİ parçalıdır (§6i > 7 > DÜZELTME-1) ve ayrı şeyleri sınar: **S1a** aynı bar +
aynı kol + aynı sembol kurulumunda stop mesafesinin BİREBİR eşit olması (kod hatası
kapısı; dolum kümesinden bağımsızdır, çünkü KURULUM düzeyinde ölçülür — test:
`tests/test_scalp_coinflip.py`), **S1b** ise defterdeki `avg_stop_distance_pct` farkının
**< %10 bağıl** kalması (kıyas koşulu; aşılırsa C-2 okunmaz). İkincisi bir zamanlar %1
yazılıydı ve o sayı YANLIŞ KÜMEDE ölçülüyordu: `max_positions` (5) de `max_short_positions`
(3) de `scalp_patient`in defterinde ZATEN bağlıyor (azami 5 eşzamanlı pozisyon, azami 3
short — ölçüldü), yani iki modelin DOLUM kümeleri yönden bağımsız olarak da ayrışır ve
defter ortalaması aynı kurulumların değil farklı alt kümelerin ortalaması olur.

⚠ **KABUL EDİLEN SAPMA — yansıtılan hedef yapısal engele DAYANMAZ.** Kolların hedefi
projeksiyon (`target_reward_risk × stop`) ile kolun kendi yapısal engelinin (VWAP,
Bollinger orta bandı, aralığın ölçülü hareketi…) YAKIN olanıdır. Yansıtılan kurulumda
hedef aynı mesafededir ama orada kolun tezinden gelen bir engel yoktur — kontrolün hedefi
saf bir projeksiyondur. Sapma DÜZELTİLMEZ: ters yönde yeni bir engel hesaplamak kontrolü
"aynı kurulum, ters yön" olmaktan çıkarıp kendi hedef kuralı olan İKİNCİ bir modele
çevirirdi; hedefi olduğu yerde bırakmak ise mesafeyi bozar ve S1a'yı tanım gereği düşürürdü.
Sapmanın YÖNÜ ön-kayıtta yazılıdır (docs/backtest.md > 6i > 5): kontrolü olduğundan KÖTÜ
gösterme yönünde çalışır ve C-2 farkını şişirebilir — bu yüzden C-2 tek başına okunmaz,
M1 ile birlikte okunur.

**İKİ RNG AKIŞI AYRIDIR ve bu yapısaldır:**

    kol / sembol çekilişi   {seed}:{as_of}:{rng_identity}            ← PAYLAŞILIR
    yazı-tura               {seed}:{as_of}:{name}:{symbol}           ← KENDİNE AİT

Çekiliş akışı `scalp_patient` (ve `scalp_fixed`) ile PAYLAŞILIR (`rng_identity` miras
alınır), çünkü kol/sembol seçimi ölçülen eksen DEĞİLDİR: paylaşılmasa iki model aynı barda
farklı kurulumlar oynardı ve fark yönün değil tesadüfün ölçüsü olurdu (CLAUDE.md >
*"çekiliş, ölçülmeyen eksende PAYLAŞILIR, ölçülen eksende BAĞIMSIZDIR"*). Yazı-tura ise
kendi akışındadır ve çekiliş dizisine DOKUNMAZ — tek akış olsaydı her yazı-tura diziyi bir
adım kaydırır ve kontrol `scalp_patient`in seçtiği kurulumu hiç görmezdi.

Akış SEMBOL bazında çatallanır: aynı barda iki sembolün yazı-turası birbirinden bağımsız
olmalı, yoksa "adil yazı-tura" bar başına tek bir çekilişe inerdi.

**Tasarımı bozulamaz** (`random_ctrl` ve `xsec_random`ın aynı sözü): buraya eklenecek her
filtre — "kötü yönü ele", "trende karşı çevirme" — kontrolü sessizce bir stratejiye
çevirir ve C-2 farkının neye karşı ölçüldüğü bilinemez hâle gelir.

**Denetim izi:** `reason` kuyruğunda `coin=same|flipped`. Yazı-turanın gerçekten adil
olduğu (S2) yalnızca bu etiketten okunabilir; yönü geometriden geri hesaplamak aynı cevabı
verir ama ikinci bir doğruluk kaynağı yaratırdı. Kuyruğun geri kalanı (`arm`, `post_r`) ve
`setup.detail` DOKUNULMAZ: ikisi de kolun o barda gerçekten gördüğüdür, yani ters yönlü bir
satırda kolun long gerekçesini okumak bir tutarsızlık değil, kontrolün tanımıdır.

Rollere dikkat: bu modül boyut/komisyon/bakiye hesaplamaz (kural 1/2/3/7) ve deftere
yazmaz (kural 1). Geometri `strategies/scalp/arms.py::reflect`te, kapılar ve tarama sayımı
`strategies/scalp/model.py`dedir.
"""

from __future__ import annotations

import logging
import random
from dataclasses import replace
from typing import Any, Mapping

from core.tags import find_tag, format_tags
from strategies.base import MarketData, Signal
from strategies.scalp.arms import ArmSetup, reflect
from strategies.scalp_patient import ScalpPatient

logger = logging.getLogger(__name__)

SAME = "same"
FLIPPED = "flipped"


class ScalpCoinflip(ScalpPatient):
    name = "scalp_coinflip"
    # `rng_identity` bilinçli olarak YENİDEN TANIMLANMAZ: `ScalpPatient`ten miras alınan
    # "scalp_fixed" değeri paylaşımın ta kendisidir. Buraya `name`e eşit bir değer yazmak
    # akışı ayırır ve ölçülen eksene tesadüf katardı (modül docstring'i).

    def __init__(self, *, config: Mapping[str, Any] | None = None) -> None:
        super().__init__(config=config)
        # Son taramanın yazı-tura dökümü: sembol -> "same" | "flipped". S2 ölçümü deftere
        # yazılan etiketten okunur; bu alan yalnızca testler ve log içindir.
        self._flips: dict[str, str] = {}

    # ------------------------------------------------------------------ #
    # Ölçülen eksen: YÖN
    # ------------------------------------------------------------------ #
    def direction_policy(self, setup: ArmSetup, *, market: MarketData) -> ArmSetup:
        """Adil yazı-tura: "aynı" ise kolun yönü, "ters" ise yansıtılmış hâli.

        Çekiliş KENDİ akışındadır (modül docstring'i) ve sembol bazında çatallanır.
        Yansıtma `strategies/scalp/arms.py::reflect`tedir — geometri burada YENİDEN
        YAZILMAZ, yoksa mesafe koruması iki yerde tanımlı olur ve bir gün ayrışırdı.
        """
        rng = self._coin_rng(market, setup.symbol)
        flipped = rng.random() < 0.5
        self._flips[setup.symbol] = FLIPPED if flipped else SAME
        if not flipped:
            return setup
        reflected = reflect(setup)
        logger.info(
            "%s %s/%s: yazı-tura TERS -> %s (stop %.6g -> %.6g, hedef %.6g -> %.6g)",
            self.name, setup.arm, setup.symbol, reflected.direction,
            setup.stop_price, reflected.stop_price,
            setup.target_price, reflected.target_price,
        )
        return reflected

    def _coin_rng(self, market: MarketData, symbol: str) -> random.Random:
        """Yazı-turanın RNG'si: kol/sembol çekilişinden AYRI, sembol bazında çatallı."""
        return random.Random(
            f"{self._seed}:{market.as_of.isoformat()}:{self.name}:{symbol}"
        )

    def last_flips(self) -> dict[str, str]:
        """Son taramanın yazı-tura dökümü; testler ve denetim için salt okunur kopya."""
        return dict(self._flips)

    # ------------------------------------------------------------------ #
    # Denetim izi
    # ------------------------------------------------------------------ #
    def _signal(self, setup: ArmSetup, *, posterior: float) -> Signal:
        """`scalp_patient`in sinyalini kurar, kuyruğa `coin=` etiketini EKLER.

        Etiket burada eklenir çünkü yön kararı burada BİLİNİR: `direction_policy`
        çağrıldıktan sonra kurulumun yönü kolunkiyle aynı mı, çevrilmiş mi yalnızca
        çekilişin kendisinden okunabilir — geometriden geri hesaplamak (ör. "kolun
        yönüyle karşılaştır") aynı cevabı verir ama iki doğruluk kaynağı yaratırdı.
        """
        signal = super()._signal(setup, posterior=posterior)
        return replace(
            signal,
            reason=format_tags(signal.reason, coin=self._flips.get(setup.symbol, SAME)),
        )

    @staticmethod
    def coin_of(reason: str) -> str | None:
        """Defter satırından yazı-tura sonucu; S2 ölçümünün TEK okuma yolu."""
        return find_tag(str(reason), "coin")

"""MODEL 22 — `wave_scalp`in KONTROLÜ: aynı kurulum, yönü yazı-turayla seçilmiş.

Ön-kayıt: **docs/backtest.md > 6h > EK-1** (koşudan ve bu dosyadan ÖNCE commit edildi).

**Cevapladığı soru tek:** *Dalga-3 kurulumunun taşıdığı şey YÖN bilgisi mi, yoksa yalnızca
"oynanabilir bir kurulum" mu?* `wave_scalp` ile aralarındaki ortalama R farkı, kabul
çıtasının C-2 koşulunun (kontrolü ≥ 0.15R marjla geçmek) tam olarak ölçüsüdür — ve scalp
katmanında kontrol modeli olmadığı için o koşul bugüne kadar DEĞERLENDİRİLEMİYORDU
(§6h > 7).

**Yarışmacıdır** (`is_replica=False`, `is_benchmark=False`): `random_ctrl`ün statüsüyle
birebir aynı gerekçe — kontrol, yarışmacılarla AYNI boyutlandırma, AYNI maliyet ve AYNI
limitlerle koşmazsa aralarındaki fark sinyalin değil koşulların ölçüsü olur.

**AYRIŞAN TEK ŞEY YÖNDÜR.** Zigzag eşiği, dalga-3 kuralı, seviye geometrisi, 12 hücreli
bandit ızgarası, epsilon-greedy seçim sırası, üç aşamalı çıkış yönetimi, evren ve limitler
`WaveScalp`ten **MİRAS ALINIR, kopyalanmaz** (`scalp_patient`in `ScalpFixed`ten türemesiyle
aynı desen): kopyalanan bir kural bir gün sessizce ayrışır ve fark "yönün ölçüsü" olmaktan
çıkar.

**Yansıtma mesafeyi KORUR.** "Ters" gelen kurulumda stop ve hedef, girişin öbür tarafına
aynı MESAFEYLE yansıtılır (`strategies/wave/clone_signal.py::reflect`), yani
`|giriş − stop|` ve `|hedef − giriş|` değişmez. Bu bir tercih değil ölçümün şartıdır:
mesafe değişseydi kontrol başka bir maliyet ölçeğinde koşar, ⚠B bandı yanar ve
`cost_per_r` kıyaslanamaz olurdu — EK-1'in `scalp_coinflip`i reddetme gerekçesinin ta
kendisi. Denetimi **S1** ölçümüdür (`avg_stop_distance_pct` farkı < %10 bağıl).

**İKİ RNG AKIŞI AYRIDIR ve bu yapısaldır:**

    bandit (ε + denenmemiş)   {seed}:{as_of}:{rng_identity}          ← PAYLAŞILIR
    yazı-tura                 {seed}:{as_of}:{name}:{symbol}         ← KENDİNE AİT

Bandit akışı `wave_scalp` ile PAYLAŞILIR (`rng_identity` miras alınır), çünkü kombinasyon
çekilişi ölçülen eksen DEĞİLDİR: paylaşılmasa iki model aynı barda farklı kombinasyonlar
denerdi ve fark yönün değil tesadüfün ölçüsü olurdu (CLAUDE.md > *"çekiliş, ölçülmeyen
eksende PAYLAŞILIR, ölçülen eksende BAĞIMSIZDIR"*). Yazı-tura ise kendi akışındadır ve
bandit'in ε dizisine DOKUNMAZ — tek akış olsaydı her yazı-tura ε dizisini bir adım kaydırır
ve kontrolün kombinasyon seçimleri yön çekilişine bağlanırdı.

Akış SEMBOL bazında çatallanır: aynı barda iki sembolün yazı-turası birbirinden bağımsız
olmalı, yoksa bir bardaki tüm kurulumlar aynı yöne çevrilir ve "adil yazı-tura" bar başına
tek bir çekilişe inerdi (S2 bunu 0.5 ± 0.05 ile denetler).

⚠ **Bandit KENDİ defterinden öğrenir** (kural 16) ve bu kapatılmaz — modelin yapısının
parçasıdır. Bedeli ön-kayıtta yazılıdır: iki modelin posteriorları farklı defterlerden
beslendiği için kombinasyon SEÇİMLERİ zamanla AYRIŞIR. Paylaşılan akış çekilişi hizalar,
posterioru hizalamaz. Bu beklenen bir durumdur, raporlanır ve S1 toleransının %10 (scalp
raporundaki %1 değil) olmasının gerekçelerinden biridir. Alternatif — kontrolün
`wave_scalp`in posteriorunu okuması — kural 4'ü (izolasyon) delerdi.

**Denetim izi:** `reason` kuyruğunda `coin=same|flipped`. Yazı-turanın gerçekten adil
olduğu (S2) yalnızca bu etiketten okunabilir.

`REGISTRY`de durur, **hiçbir katmanın `models` listesinde YOKTUR**: kod ölçülmeden
yarışmaz ve bu model canlıya alınacak bir tez değil, bir ölçüm zeminidir.
"""

from __future__ import annotations

import logging
import random
from typing import Any, Mapping

from core.tags import find_tag
from strategies.base import MarketData
from strategies.wave import clone_signal
from strategies.wave_scalp import WaveScalp

logger = logging.getLogger(__name__)

SAME = "same"
FLIPPED = "flipped"


class WaveCoinflip(WaveScalp):
    name = "wave_coinflip"
    # Bandit çekilişi `wave_scalp` ile PAYLAŞILIR (modül docstring'i). Alan bilinçli olarak
    # YENİDEN TANIMLANMAZ — miras alınması paylaşımın ta kendisidir; burada `name`e eşit
    # bir değer yazmak akışı ayırır ve ölçülen eksene tesadüf katardı.

    def __init__(self, *, config: Mapping[str, Any] | None = None) -> None:
        super().__init__(config=config)
        # Son taramanın yazı-tura dökümü: sembol -> "same" | "flipped". S2 ölçümü deftere
        # yazılan etiketten okunur; bu alan yalnızca testler ve log içindir.
        self._flips: dict[str, str] = {}

    # ------------------------------------------------------------------ #
    # Ölçülen eksen: YÖN
    # ------------------------------------------------------------------ #
    def orient(
        self, candidate: clone_signal.WaveCandidate, *, market: MarketData
    ) -> clone_signal.WaveCandidate:
        """Adil yazı-tura: "aynı" ise kaynağın yönü, "ters" ise yansıtılmış hâli.

        Çekiliş KENDİ akışındadır (modül docstring'i) ve sembol bazında çatallanır.
        Yansıtma `clone_signal.reflect`tedir — geometri bu modülde YENİDEN YAZILMAZ.
        """
        rng = self._coin_rng(market, candidate.symbol)
        flipped = rng.random() < 0.5
        self._flips[candidate.symbol] = FLIPPED if flipped else SAME
        if not flipped:
            return candidate
        reflected = clone_signal.reflect(candidate)
        logger.info(
            "%s %s: yazı-tura TERS -> %s (stop %.6g -> %.6g, hedef %.6g -> %.6g)",
            self.name, candidate.symbol, reflected.direction,
            candidate.stop_price, reflected.stop_price,
            candidate.target_price, reflected.target_price,
        )
        return reflected

    def _coin_rng(self, market: MarketData, symbol: str) -> random.Random:
        """Yazı-turanın RNG'si: bandit akışından AYRI, sembol bazında çatallı."""
        return random.Random(
            f"{self._seed}:{market.as_of.isoformat()}:{self.name}:{symbol}"
        )

    def last_flips(self) -> dict[str, str]:
        """Son taramanın yazı-tura dökümü; testler ve denetim için salt okunur kopya."""
        return dict(self._flips)

    # ------------------------------------------------------------------ #
    # Denetim izi
    # ------------------------------------------------------------------ #
    def _signal(self, candidate, *, combo, stats, pick):  # type: ignore[override]
        """`wave_scalp`in sinyalini kurar, kuyruğa `coin=` etiketini EKLER.

        Etiket burada eklenir çünkü yön kararı burada BİLİNİR: `orient` çağrıldıktan sonra
        adayın yönü kaynağınkiyle aynı mı, çevrilmiş mi yalnızca çekilişin kendisinden
        okunabilir — adayın alanlarından geri hesaplamak (ör. "setup.direction ile
        candidate.direction farklı mı") aynı cevabı verir ama iki doğruluk kaynağı yaratırdı.
        """
        signal = super()._signal(candidate, combo=combo, stats=stats, pick=pick)
        coin = self._flips.get(candidate.symbol, SAME)
        return type(signal)(
            **{
                **{field: getattr(signal, field) for field in signal.__dataclass_fields__},
                "reason": f"{signal.reason} | coin={coin}",
            }
        )

    @staticmethod
    def coin_of(reason: str) -> str | None:
        """Defter satırından yazı-tura sonucu; S2 ölçümünün TEK okuma yolu."""
        return find_tag(str(reason), "coin")

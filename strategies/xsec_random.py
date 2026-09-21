"""Kesitsel momentumun KONTROL grubu: aynı kurulum, bilgisiz seçim (§6g).

Sorduğu soru tek: *"`xsec_mom`un ortalama R'si, aynı rebalance günlerinde aynı uygunluk
kuralı, aynı stop ve aynı çıkış kurallarıyla RASTGELE seçilmiş bir portföyden ayırt
edilebilir mi?"* Ölçülen eksen seçimin ta kendisidir, bu yüzden seçim dışındaki her şey
ortak kopyadan (`strategies/xsec/`) okunur.

**Tasarımı bozulamaz** (`random_ctrl`ün aynı sözü): buraya eklenecek her filtre kontrolü
sessizce bir stratejiye çevirir ve o andan itibaren `xsec_mom`un farkı neye karşı
ölçtüğü bilinmez hâle gelir. Tek meşru eleme, işlemin KURULAMADIĞI durumlardır ve o eleme
zaten ortak `eligible_candidates`tadır.

**Çekiliş `xsec_mom` ile PAYLAŞILMAZ ve bu bilinçlidir.** CLAUDE.md'nin kuralı: *çekiliş,
ölçülmeyen eksende paylaşılır, ölçülen eksende BAĞIMSIZDIR.* Burada ölçülen eksen seçimin
kendisi olduğu için paylaşılan bir çekiliş, farkı momentumun değil tesadüfün ölçüsü
yapardı (model 11 ↔ 12'nin aynı gerekçesi; model 12 ↔ 15'te ise tersi geçerliydi).

**Tohum bar bazında karıştırılır:** `random_seed` + `as_of`. Sabit bir tohumla süreç
başına tek RNG kurmak, her koşu ayrı bir süreç olduğu için HER REBALANCE'ta aynı çekilişi
yapardı — kontrol bilgisiz değil SABİT olurdu. Bar bazlı tohumlama iki şeyi birden verir:
aynı bar yeniden koşulduğunda birebir aynı seçim (tekrarlanabilirlik) ve farklı barlarda
bağımsız çekilişler. Aynı bar içinde `generate_signals` ile `manage_positions`ın aynı
kümeyi görmesi de buna dayanır — RNG her çağrıda sıfırdan tohumlanır.

**RNG ağ katmanıyla PAYLAŞILMAZ:** `core/data.py` retry jitter'ı için kendi örneğini
tutar. Paylaşmak, çekilişi o turda kaç kez yeniden denendiğine — yani borsanın o günkü
keyfine — bağlardı.
"""

from __future__ import annotations

import random
from typing import Any, Mapping, Sequence

from core.config import get_setting, load_config
from strategies.base import MarketData
from strategies.xsec.model import XsecModel
from strategies.xsec.ranking import Candidate


class XsecRandom(XsecModel):
    name = "xsec_random"

    def __init__(self, *, config: Mapping[str, Any] | None = None) -> None:
        settings = dict(config) if config is not None else load_config()
        super().__init__(config=settings)
        self._seed = int(get_setting(settings, "random_seed"))

    def choose(self, candidates: Sequence[Candidate], market: MarketData) -> list[Candidate]:
        rng = random.Random(f"{self._seed}:{self.name}:{market.as_of.isoformat()}")
        k = min(self._rules.top_k, len(candidates))
        # `sample` YERİNDE karıştırmaz ve aynı sembolü iki kez seçmez: kota k olduğu için
        # tekrarlı bir çekiliş, kontrolün portföyünü momentumunkinden dar yapardı.
        return rng.sample(list(candidates), k)

    def selection_note(self, chosen: Sequence[Candidate], pool: Sequence[Candidate]) -> str:
        return (
            f"KONTROL GRUBU: bilgisiz çekiliş — {len(pool)} uygun sembol arasından "
            f"{len(chosen)} tanesi; tohum {self._seed}"
        )

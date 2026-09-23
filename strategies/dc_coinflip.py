"""Model 23 — `dc_coinflip`: `dc_short`un KONTROLÜ. Aynı kurulum, yönü yazı-tura.

Ön-kayıt: docs/backtest.md > 6i > 4. Desen `wave_coinflip`in (§6h > EK-1) aynısıdır.
Kurulum tespiti, geometri, evren, sıra ve sayım `dc_short`tan MİRAS ALINIR, kopyalanmaz;
ayrışan TEK şey yöndür — ölçülen eksen odur.

- Yazı-tura "aynı" → sinyal `dc_short`unkinin birebir aynısı.
- Yazı-tura "ters" → yön long; stop ve hedef MESAFELERİ kurulum kapanışı etrafında
  aynalanır (`strategies/dc/signal.py::reflect`). Tavan kapısı iki modelde birebir aynı
  çalışır, çünkü `|close − stop|` korunur.

**RNG ayrı akıştır ve SEMBOL bazında çatallanır:** `random_seed:as_of:dc_coinflip:sembol`.
Bar başına tek çekiliş, o bardaki bütün kurulumları aynı yöne çevirirdi ve S2 (ters payı
0.5 ± 0.05) bunu denetler. **Tohum tek seferliktir** (§6i > 4): farklı tohumla yeniden
koşmak, E kapısının dayandığı FARKIN zeminini seçmek olurdu.

**Denetim izi:** `coin=same|flipped`. S2 yalnızca bu etiketten okunur.

**Tasarımı bozulamaz** (`random_ctrl`ün aynı sözü): buraya eklenecek her filtre kontrolü
sessizce bir stratejiye çevirir.
"""

from __future__ import annotations

import random
from typing import Any, Mapping

from core.config import get_setting, load_config
from core.tags import find_tag
from strategies.base import Direction, MarketData
from strategies.dc.signal import DcSetup, reflect
from strategies.dc_short import DcShort

SAME = "same"
FLIPPED = "flipped"


class DcCoinflip(DcShort):
    name = "dc_coinflip"
    allowed_directions: list[Direction] = ["long", "short"]

    def __init__(self, *, config: Mapping[str, Any] | None = None) -> None:
        settings = dict(config) if config is not None else load_config()
        super().__init__(config=settings)
        self._seed = int(get_setting(settings, "random_seed"))

    def orient(self, setup: DcSetup, market: MarketData) -> tuple[DcSetup, str | None]:
        rng = random.Random(f"{self._seed}:{market.as_of.isoformat()}:{self.name}:{setup.symbol}")
        if rng.random() < 0.5:
            return reflect(setup), FLIPPED
        return setup, SAME

    @staticmethod
    def coin_of(reason: str) -> str | None:
        """Defter satırından yazı-tura sonucu; S2 ölçümünün TEK okuma yolu."""
        return find_tag(str(reason), "coin")

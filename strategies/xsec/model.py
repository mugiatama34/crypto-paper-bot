"""Kesitsel momentum modellerinin ORTAK gövdesi: rebalance akışı, giriş, çıkış.

Alt sınıfın değiştirebileceği TEK nokta `choose`dur ve bu bir sayı tercihi değil bir
ilkedir (`strategies/scalp/model.py`nin aynı kuralı): **karşılığı ölçülen bir eksen
olmayan bir override noktası eklenemez.** Burada ölçülen eksen seçimin kendisidir
(momentum sıralaması ↔ bilgisiz çekiliş); uygunluk, sıralama ölçütü, stop geometrisi,
rebalance takvimi ve çıkış kuralı iki modelde de BİREBİR aynıdır. Ayrışsalardı ortalama R
farkı seçimin ölçüsü olmaktan çıkardı.

**Kural 4 korunur ve bedeli bilinçlidir.** `generate_signals` açık pozisyonları GÖREMEZ,
bu yüzden her rebalance'ta seçilen k sembolün TAMAMI için sinyal üretilir; elde zaten
olanlar `core/portfolio.py` tarafından `duplicate_position` sebep koduyla reddedilir ve
tur raporunda sayılır. Bu, `buyhold` çıpasının kullandığı desenin aynısıdır (kural 15:
"her turda `signals=2 / filled=0` görünümü") ve reddin bir sebep koduyla kaydedilmesi,
beklenen tekrarı gerçek bir boyutlandırma arızasından ayırt eder.

**Çıkış `manage_positions`tadır (kural 10)** ve orada açık pozisyonlar GÖRÜLÜR. İki taraf
aynı barda aynı seçimi hesaplar; seçim deterministik olduğu için (momentumda sıralama,
kontrolde `as_of` ile tohumlanan çekiliş) giriş kümesi ile tutulan küme tutarlıdır.
"""

from __future__ import annotations

import logging
from typing import Any, Mapping, Sequence

from core.config import load_config
from core.tags import format_tags
from strategies.base import (
    Direction,
    ExitInstruction,
    MarketData,
    Position,
    Signal,
    Strategy,
)
from strategies.xsec.ranking import (
    EXIT_RULE,
    Candidate,
    XsecRules,
    eligible_candidates,
    is_rebalance_bar,
)

logger = logging.getLogger(__name__)


class XsecModel(Strategy):
    """Haftalık rebalance eden, long-only, top-k kesitsel model."""

    allowed_directions: list[Direction] = ["long"]

    def __init__(self, *, config: Mapping[str, Any] | None = None) -> None:
        settings = dict(config) if config is not None else load_config()
        self._rules = XsecRules.from_config(settings)
        self._settings = settings

    # ------------------------------------------------------------------ #
    # Alt sınıfın TEK override noktası
    # ------------------------------------------------------------------ #
    def choose(self, candidates: Sequence[Candidate], market: MarketData) -> list[Candidate]:
        """Uygun adaylardan `top_k` tanesini seçer. ÖLÇÜLEN EKSEN BUDUR.

        Deterministik olmak ZORUNDADIR: aynı bar içinde `generate_signals` ve
        `manage_positions` ayrı ayrı çağırır ve iki çağrı farklı küme döndürürse model
        aynı barda hem alır hem satar — defterde okunamayan bir davranış.
        """
        raise NotImplementedError

    def selection_note(self, chosen: Sequence[Candidate], pool: Sequence[Candidate]) -> str:
        """Defter `reason` kuyruğuna yazılacak seçim gerekçesi."""
        raise NotImplementedError

    # ------------------------------------------------------------------ #
    # Giriş
    # ------------------------------------------------------------------ #
    def generate_signals(
        self,
        market: MarketData,
        peer_signals: Mapping[str, tuple[Signal, ...]] | None = None,
    ) -> list[Signal]:
        chosen = self._selection(market)
        if chosen is None:
            return []
        note = self.selection_note(chosen, self._pool)
        signals: list[Signal] = []
        for candidate in chosen:
            stop_price = candidate.stop_price(self._rules.stop_atr_multiple)
            distance = candidate.close - stop_price
            signals.append(
                Signal(
                    symbol=candidate.symbol,
                    direction="long",
                    stop_price=stop_price,
                    # TAKE-PROFIT YOK (§6g): momentumda kâr pozisyonda kalma süresinden
                    # gelir ve bir hedef tam olarak o süreyi keserdi. §6e'nin yol ölçümü
                    # hedef geometrisinin beklenen değeri değiştirmediğini göstermişti.
                    reason=(
                        f"{note}; {self._rules.lookback_bars} barlık getiri "
                        f"{candidate.lookback_return:+.2%}; kapanış {candidate.close:.6g}, "
                        f"stop {self._rules.stop_atr_multiple:g}×ATR"
                        f"({self._rules.atr_period})={distance:.6g} uzakta ({stop_price:.6g})"
                    ),
                )
            )
        return signals

    # ------------------------------------------------------------------ #
    # Çıkış — rebalance'ta seçim dışına düşen pozisyonlar
    # ------------------------------------------------------------------ #
    def manage_positions(
        self, market: MarketData, positions: list[Position]
    ) -> list[ExitInstruction]:
        chosen = self._selection(market)
        if chosen is None:
            # Rebalance barı değil: pozisyonlara DOKUNULMAZ. Çıkışın öteki yolu stop'tur
            # ve onu motor uygular (kural 13); model arada bir şey yapmaz.
            return []
        keep = {candidate.symbol for candidate in chosen}
        instructions: list[ExitInstruction] = []
        for position in positions:
            if position.symbol in keep:
                continue
            instructions.append(
                ExitInstruction(
                    symbol=position.symbol,
                    action="close",
                    reason=format_tags(
                        f"rebalance: {position.symbol} artık ilk {self._rules.top_k} "
                        f"içinde değil ({len(self._pool)} uygun sembol arasından)",
                        exit_rule=EXIT_RULE,
                    ),
                )
            )
        return instructions

    # ------------------------------------------------------------------ #
    # Ortak yol
    # ------------------------------------------------------------------ #
    def _selection(self, market: MarketData) -> list[Candidate] | None:
        """Rebalance barındaysa seçim, değilse None.

        `None` ile boş liste AYRI şeylerdir: ilki "bugün rebalance günü değil" (pozisyona
        dokunulmaz), ikincisi "rebalance günü ama uygun sembol yok" (tutulan her pozisyon
        kapanır). İkisini tek değere indirmek, veri boşluğu olan bir haftada portföyü
        sessizce donduracaktı.
        """
        self._pool: list[Candidate] = []
        if not is_rebalance_bar(market.as_of, duration=self._rules.bar_duration):
            return None
        self._pool = eligible_candidates(market, self._rules)
        if not self._pool:
            logger.info(
                "%s: %s rebalance barında uygun sembol yok", self.name, market.as_of,
            )
            return []
        return self.choose(self._pool, market)

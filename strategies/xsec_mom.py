"""Kesitsel momentum: geriye bakış getirisine göre top-k (ön-kayıt: docs/backtest.md > 6g).

MEKANİZMA: kripto'da sermaye akışı son dönemin kazananlarını GECİKMEYLE takip eder;
göreli güç kısa vadede kalıcıdır ve karşı tarafta geç gelen akış durur.

**`ema_trend`den (model 18) farkı bir parametre değil sorunun kendisidir:** o ZAMAN
SERİSİ momentumudur ("bu sembol yükseliyor mu"), bu KESİTSELdir ("bu sembol
diğerlerinden iyi mi"). Yatay bir piyasada zaman serisi sinyali susar, kesitsel sinyal
susmaz — göreli sıralama her zaman tanımlıdır.

Modelin kendine ait TEK şeyi seçimdir (`choose`); uygunluk, sıralama ölçütü, stop
geometrisi, rebalance takvimi ve çıkış kuralı `strategies/xsec/` altındaki ortak
kopyadadır ve `xsec_random` ile BİREBİR paylaşılır. Ölçülen eksen tam olarak aradaki o
tek farktır.

Parametreler `config.yaml > xsec` bloğunda ve ön-kayıtlıdır: geriye bakış 126 bar
(21 gün), top-3, stop 5×ATR(14, `simple`). Koşu sonucuna göre DEĞİŞTİRİLEMEZLER (§7.1)
ve süpürülmeyeceklerdir.
"""

from __future__ import annotations

from typing import Sequence

from strategies.base import MarketData
from strategies.xsec.model import XsecModel
from strategies.xsec.ranking import Candidate, by_momentum


class XsecMomentum(XsecModel):
    name = "xsec_mom"

    def choose(self, candidates: Sequence[Candidate], market: MarketData) -> list[Candidate]:
        return by_momentum(candidates, self._rules.top_k)

    def selection_note(self, chosen: Sequence[Candidate], pool: Sequence[Candidate]) -> str:
        rank = {candidate.symbol: index + 1 for index, candidate in enumerate(chosen)}
        return (
            f"KESİTSEL MOMENTUM: {len(pool)} uygun sembol arasından ilk "
            f"{self._rules.top_k} (sıra {rank})"
        )

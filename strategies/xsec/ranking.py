"""Kesitsel sıralamanın TEK tanımı: uygunluk, geriye bakış getirisi, rebalance barı.

`xsec_mom` ve `xsec_random` bu tek kopyayı okur. Ayrışan TEK şey seçimdir
(`XsecModel.choose`) ve ölçülen eksen zaten odur — uygunluk kuralı, sıralama ölçütü,
stop geometrisi ya da rebalance takvimi iki modelde ayrı yazılsaydı aradaki ortalama R
farkı "seçimin ölçüsü" olmaktan çıkar, "iki ayrı uygulamanın farkı" olurdu. Aynı gerekçe
`strategies/time_stop.py` ve `strategies/exit_management.py`nin tek kopya olmasındadır.

**Uygunluk bir GÖRÜŞ değil, işlemin kurulabilirliğidir** (`random_ctrl`ün aynı sözü):
sembolün `as_of` barı olmalı, tam geriye bakış geçmişi bulunmalı, ATR'si hesaplanmalı ve
stop fiyatı pozitif çıkmalı. Buraya eklenecek her ek eleme (hacim, rejim, "kötü
sembolleri çıkar") kontrolü sessizce bir stratejiye çevirir ve momentumun neye karşı
ölçüldüğünü bilinmez kılar.

**ATR yumuşatması projenin VARSAYILANIDIR (`simple`), `wilder` DEĞİL** — ön-kayıtta
düzeltildi (§6g > "ATR yumuşatması"). Gerekçe iki katmanlı: (a) `ema_trend` Wilder'ı bir
dış sistem paritesi için aldı, bu modelin öyle bir referansı yok; (b) motorun stop tavanı
kontrolü (`core/engine.py::_within_stop_band`) ATR'yi projenin varsayılanıyla ölçer ve
modelin bildirimini okumaz — model Wilder deseydi "5×ATR" ifadesi modelde ve tavanda iki
farklı sayı anlamına gelir, oran kayardı.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

import pandas as pd

from core.config import get_setting
from core.data import bar_duration
from core.indicators import average_true_range, bars_until
from strategies.base import MarketData

logger = logging.getLogger(__name__)

# Çıkışın ALT sebebi (kural 13c). `exit_reason` yalnızca "signal" der; rebalance çıkışı
# ile başka bir strateji çıkışı aynı satıra çökerse ön-kayıtlı P1 tahmini (rebalance payı
# ≥ %70) defterden OKUNAMAZ.
EXIT_RULE = "rebalance"


@dataclass(frozen=True, kw_only=True)
class Candidate:
    """Sıralamaya giren, işlemi KURULABİLİR bir sembol."""

    symbol: str
    close: float
    atr: float
    lookback_return: float

    def stop_price(self, multiple: float) -> float:
        return self.close - self.atr * multiple


@dataclass(frozen=True, kw_only=True)
class XsecRules:
    """Ön-kayıtta (§6g) sabitlenen sayılar; hepsi `config.yaml > xsec` altından okunur."""

    lookback_bars: int
    top_k: int
    stop_atr_multiple: float
    atr_period: int
    timeframe: str

    @classmethod
    def from_config(cls, settings: Mapping[str, Any]) -> "XsecRules":
        config = dict(settings)
        return cls(
            lookback_bars=int(get_setting(config, "xsec.lookback_bars")),
            top_k=int(get_setting(config, "xsec.top_k")),
            stop_atr_multiple=float(get_setting(config, "xsec.stop_atr_multiple")),
            # ATR periyodu parametre DEĞİL: projenin tek ATR tanımı `trailing.atr_period`.
            atr_period=int(get_setting(config, "trailing.atr_period")),
            timeframe=str(get_setting(config, "timeframe")),
        )

    @property
    def bar_duration(self) -> pd.Timedelta:
        return bar_duration(self.timeframe)


def is_rebalance_bar(as_of: pd.Timestamp, *, duration: pd.Timedelta) -> bool:
    """Bar PAZARTESİ 00:00 UTC'de KAPANIYOR mu? (§6g'nin birebir tanımı)

    Ölçüt barın KAPANIŞIDIR, indeksi değil: `core/data.py` bar indeksini AÇILIŞ zamanı
    olarak tutar (kapanış = `bar_ts + timeframe`), yani 4H barda rebalance barının
    indeksi Pazar 20:00'dir. Kuralı kapanış üzerinden yazmak ön-kaydın cümlesiyle birebir
    aynı şeyi söyler ve bar süresi değişirse indeks otomatik kayar — "Pazar 20:00" diye
    sabitlemek, kuralı 4H'ye gizlice çivilemek olurdu.
    """
    close_ts = as_of + duration
    return close_ts.weekday() == 0 and close_ts.hour == 0 and close_ts.minute == 0


def eligible_candidates(market: MarketData, rules: XsecRules) -> list[Candidate]:
    """Sıralamaya girebilecek semboller, ADI SIRALI.

    Sıra tekrarlanabilirliğin parçasıdır: sözlük sırasına bırakmak, aynı tohumla aynı
    barın veri katmanının sırasına göre farklı sembol seçmesi demekti (`random_ctrl`ün
    aynı gerekçesi). Eşit getirili iki sembolde de sıralamayı ad belirler.
    """
    candidates: list[Candidate] = []
    for symbol in sorted(market.ohlcv):
        frame = bars_until(market.ohlcv[symbol], market.as_of)
        if frame.empty or frame.index[-1] != market.as_of:
            # Sembol `as_of` barını taşımıyor. core/data.py bunları zaten dışlar; bir
            # sıralamayı bir bar geriden kurmak look-ahead kadar sessiz bir ölçüm hatası.
            continue
        closes = frame["close"]
        if len(closes) < rules.lookback_bars + 1:
            # Tam geriye bakış geçmişi yok (§6g'nin uygunluk kuralı). Kısmi pencereyle
            # hesaplanan bir getiri, sembolü diğerleriyle AYNI ölçütle sıralamaz.
            continue
        past = float(closes.iloc[-(rules.lookback_bars + 1)])
        close = float(closes.iloc[-1])
        if past <= 0.0 or close <= 0.0:
            continue
        atr = average_true_range(frame, rules.atr_period)
        if atr is None or atr <= 0.0:
            logger.info(
                "xsec %s: sıralama dışı, ATR(%d) hesaplanamadı", symbol, rules.atr_period,
            )
            continue
        candidate = Candidate(
            symbol=symbol, close=close, atr=float(atr),
            lookback_return=close / past - 1.0,
        )
        if candidate.stop_price(rules.stop_atr_multiple) <= 0.0:
            # ATR fiyatın kendisinden büyük: stop sıfırın altına düşer, geometri kurulamaz.
            # Bu bir piyasa durumudur, programlama hatası değil (kural 8 ile karışmaz).
            logger.info(
                "xsec %s: sıralama dışı, stop sıfırın altında (kapanış %.6g, %g×ATR %.6g)",
                symbol, candidate.close, rules.stop_atr_multiple,
                candidate.atr * rules.stop_atr_multiple,
            )
            continue
        candidates.append(candidate)
    return candidates


def by_momentum(candidates: Sequence[Candidate], k: int) -> list[Candidate]:
    """Geriye bakış getirisine göre EN İYİ k. Eşitlikte sembol adı belirler.

    Eşitlik kuralı yazılı olmalı: `sorted` kararlıdır ve girdi zaten ada göre sıralı
    geldiği için eşit getirili iki sembolden adı önce gelen seçilir. Bunu yazmamak,
    eşitliğin Python sürümüne bağlı bir ayrıntı olmasına izin vermek olurdu.
    """
    ordered = sorted(candidates, key=lambda c: -c.lookback_return)
    return list(ordered[:k])

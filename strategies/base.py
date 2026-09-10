"""Strateji arayüz sözleşmesi: Strategy, Signal, Position, ExitInstruction, MarketData.

Onaylanan tasarım (bkz. CLAUDE.md > "Strateji Arayüz Sözleşmesi"):

- generate_signals: yalnızca yeni pozisyon açılışı önerir.
- manage_positions: yalnızca mevcut pozisyonlarda kapanış/kısmi çıkış önerir.
  İkisi karıştırılmaz; engine'in "bu sinyal yeni mi, mevcut pozisyona müdahale mi"
  diye tahmin yürütmesi gerekmez.
- entry_type sözleşmede "limit" olarak da yazılabilir ama v1'de yalnızca "market"
  işlenir; core/validate.py "limit" gördüğünde NotImplementedError fırlatır.
- trailing_atr yalnızca stratejinin isteğini taşır; trailing'in uygulanması
  core/engine.py'nin işidir, strateji kendi trailing mantığını yazmaz.
- take_profits bir tuple'dır, liste değil: frozen dataclass içinde mutable liste
  taşımak dondurmayı yarım bırakır ve bir stratejinin (ya da meta modelin) kendi/
  başkasının sinyalini yerinde değiştirmesine izin verirdi.
- MarketData.funding tek oran değil zaman indeksli seridir ve as_of sözleşmede yer
  alır: "şimdi"nin tek ve açık tanımı, look-ahead yasağını (kural 12) test edilebilir
  kılar.
- Bu dosya mantık içermez, yalnızca sözleşme. Doğrulama core/validate.py'dedir.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Literal, Mapping

import pandas as pd

Direction = Literal["long", "short"]


@dataclass(frozen=True, kw_only=True)
class TakeProfit:
    price: float
    fraction: float  # 0 < fraction <= 1.0; bir Signal içindeki toplam <= 1.0


@dataclass(frozen=True, kw_only=True)
class Signal:
    symbol: str
    direction: Direction
    stop_price: float
    entry_type: Literal["market", "limit"] = "market"
    take_profits: tuple[TakeProfit, ...] = ()
    trailing_atr: float | None = None
    reason: str = ""  # deftere yazılacak serbest metin


@dataclass(frozen=True, kw_only=True)
class Position:
    """manage_positions'a verilen salt okunur pozisyon görünümü."""

    symbol: str
    direction: Direction
    entry_price: float
    stop_price: float
    take_profits: tuple[TakeProfit, ...] = ()
    trailing_atr: float | None = None
    opened_at: pd.Timestamp


@dataclass(frozen=True, kw_only=True)
class ExitInstruction:
    symbol: str
    action: Literal["close", "reduce"]
    fraction: float = 1.0  # yalnızca action="reduce" için anlamlı
    reason: str = ""


@dataclass(frozen=True, kw_only=True)
class MarketData:
    ohlcv: dict[str, pd.DataFrame]  # sembol -> timeframe OHLCV, yalnızca KAPANMIŞ barlar
    btc: pd.DataFrame  # BTC referans verisi (aynı kural)
    funding: dict[str, pd.Series] = field(default_factory=dict)  # sembol -> funding geçmişi
    as_of: pd.Timestamp  # değerlendirilen son KAPANMIŞ barın zamanı (tz-aware, UTC)


class Strategy(ABC):
    name: str
    allowed_directions: list[Direction]
    is_meta: bool = False  # True ise engine'in ikinci geçişinde çalışır (CLAUDE.md kural 4)

    @abstractmethod
    def generate_signals(
        self,
        market: MarketData,
        peer_signals: Mapping[str, tuple[Signal, ...]] | None = None,
    ) -> list[Signal]:
        """Yeni pozisyon açılışı önerir. Mevcut pozisyonlara dokunmaz.

        peer_signals yalnızca is_meta=True modellere doldurulur: strateji adı ->
        o turda üretilmiş sinyallerin salt okunur kopyası. Normal modellerde None.
        """

    def manage_positions(
        self, market: MarketData, positions: list[Position]
    ) -> list[ExitInstruction]:
        """Mevcut pozisyonlarda kapanış/kısmi çıkış önerir. Varsayılan: hiçbir şey yapma."""
        return []

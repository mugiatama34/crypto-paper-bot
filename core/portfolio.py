"""Pozisyon/bakiye yönetimi ve boyutlandırmanın tek yetkili kaynağı (CLAUDE.md kural 3/11).

Parayı hareket ettiren tek yer burasıdır: boyutlandırma, marj, komisyon, kayma,
likidasyon ve stop/TP tetikleme. Stratejiler yalnızca yön ve seviye önerir; buradaki
kurallar 10 modele de birebir aynı uygulanır (kural 2/6).

Mum içi kontrol sırası — CLAUDE.md kural 13 ve modül tablosu:

    1. likidasyon (bakım marjı, mum içi high/low ile)
    2. stop
    3. take-profit

Bir mumda hem stop hem TP aralığa giriyorsa mum içi sıralama bilinemez; bu yüzden KÖTÜ
olan (stop) gerçekleşmiş varsayılır — iyimser varsayım her modelin sonucunu, en çok da
geniş hedefli olanlarınkini, sistematik biçimde şişirirdi. Likidasyon her ikisinden de
önce gelir: gerçek borsada bakım marjı ihlali stop emrini beklemez, kontrolü sonraya almak
yüksek kaldıraçlı modellere gerçekte var olmayan bir kurtulma şansı verir.

Nakit muhasebesi (deftere birebir yansır):

    açılışta   : nakit -= marj + giriş komisyonu
    açıkken    : nakit += funding (işaretli; + alınan, - ödenen)
    kapanışta  : nakit += marj + brüt PnL - çıkış komisyonu
    likidasyon : nakit += 0  (marjın tamamı gider)

`Trade.pnl` işlemin nakde net etkisidir (marj gidiş-dönüşü hariç, likidasyonda marj
kaybı dâhil): kapanan işlemlerin `pnl` toplamı, bakiyedeki toplam değişime eşittir.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Iterable, Literal, Mapping, Sequence

import pandas as pd

from core.config import get_setting
from strategies.base import Direction, Position, TakeProfit

logger = logging.getLogger(__name__)

ExitReason = Literal["liquidation", "stop", "tp", "signal"]

# Kalan miktar başlangıcın bu oranının altına düşerse pozisyon kapanmış sayılır:
# kısmi çıkışların kayan nokta artığı "0.0000000001 adet" pozisyon bırakmasın.
_DUST_RATIO = 1e-9


@dataclass(frozen=True, kw_only=True)
class Bar:
    """Tek bir mumun motorun ihtiyaç duyduğu dar görünümü."""

    open: float
    high: float
    low: float
    close: float

    @classmethod
    def from_row(cls, row: Mapping[str, Any]) -> "Bar":
        return cls(
            open=float(row["open"]),
            high=float(row["high"]),
            low=float(row["low"]),
            close=float(row["close"]),
        )


@dataclass(frozen=True, kw_only=True)
class Sizing:
    qty: float
    notional: float
    margin: float
    leverage: float
    clipped: bool
    note: str


@dataclass(frozen=True, kw_only=True)
class Trade:
    """Kapanan işlem (kısmi çıkışta yalnızca kapanan dilim) — trades.csv'nin bir satırı."""

    strategy: str
    symbol: str
    direction: Direction
    opened_at: pd.Timestamp
    closed_at: pd.Timestamp
    entry_price: float
    exit_price: float
    qty: float
    notional: float
    leverage: float
    margin: float
    fee: float
    funding: float
    pnl: float
    exit_reason: ExitReason
    signal_reason: str
    notes: str

    def as_row(self) -> dict[str, Any]:
        return {
            "strategy": self.strategy,
            "symbol": self.symbol,
            "direction": self.direction,
            "opened_at": self.opened_at.isoformat(),
            "closed_at": self.closed_at.isoformat(),
            "entry_price": self.entry_price,
            "exit_price": self.exit_price,
            "qty": self.qty,
            "notional": self.notional,
            "leverage": self.leverage,
            "margin": self.margin,
            "fee": self.fee,
            "funding": self.funding,
            "pnl": self.pnl,
            "exit_reason": self.exit_reason,
            "signal_reason": self.signal_reason,
            "notes": self.notes,
        }


@dataclass
class OpenPosition:
    """Açık pozisyonun tam (yazılabilir) hâli. `strategies/base.Position` bunun kırpılmış görünümüdür.

    `margin`, `entry_fee` ve `funding` kısmi çıkışlarda miktarla orantılı azalır; bu sayede
    `margin / qty` (dolayısıyla likidasyon fiyatı) kısmi çıkıştan etkilenmez.
    """

    symbol: str
    direction: Direction
    qty: float
    initial_qty: float
    entry_price: float
    stop_price: float
    initial_stop_price: float
    opened_at: pd.Timestamp
    margin: float
    leverage: float
    entry_fee: float
    liq_price: float
    high_water: float
    low_water: float
    take_profits: tuple[TakeProfit, ...] = ()
    trailing_atr: float | None = None
    funding: float = 0.0
    reason: str = ""
    notes: str = ""

    def view(self) -> Position:
        """Stratejiye verilen salt okunur görünüm — miktar/marj bilinçli olarak yoktur (kural 3)."""
        return Position(
            symbol=self.symbol,
            direction=self.direction,
            entry_price=self.entry_price,
            stop_price=self.stop_price,
            take_profits=self.take_profits,
            trailing_atr=self.trailing_atr,
            opened_at=self.opened_at,
        )

    def as_state(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "direction": self.direction,
            "qty": self.qty,
            "initial_qty": self.initial_qty,
            "entry_price": self.entry_price,
            "stop_price": self.stop_price,
            "initial_stop_price": self.initial_stop_price,
            "opened_at": self.opened_at.isoformat(),
            "margin": self.margin,
            "leverage": self.leverage,
            "entry_fee": self.entry_fee,
            "liq_price": self.liq_price,
            "high_water": self.high_water,
            "low_water": self.low_water,
            "take_profits": [
                {"price": tp.price, "fraction": tp.fraction} for tp in self.take_profits
            ],
            "trailing_atr": self.trailing_atr,
            "funding": self.funding,
            "reason": self.reason,
            "notes": self.notes,
        }

    @classmethod
    def from_state(cls, payload: Mapping[str, Any]) -> "OpenPosition":
        return cls(
            symbol=str(payload["symbol"]),
            direction=str(payload["direction"]),  # type: ignore[arg-type]
            qty=float(payload["qty"]),
            initial_qty=float(payload["initial_qty"]),
            entry_price=float(payload["entry_price"]),
            stop_price=float(payload["stop_price"]),
            initial_stop_price=float(payload["initial_stop_price"]),
            opened_at=_to_utc(payload["opened_at"]),
            margin=float(payload["margin"]),
            leverage=float(payload["leverage"]),
            entry_fee=float(payload["entry_fee"]),
            liq_price=float(payload["liq_price"]),
            high_water=float(payload["high_water"]),
            low_water=float(payload["low_water"]),
            take_profits=tuple(
                TakeProfit(price=float(tp["price"]), fraction=float(tp["fraction"]))
                for tp in payload.get("take_profits", ())
            ),
            trailing_atr=(
                None if payload.get("trailing_atr") is None else float(payload["trailing_atr"])
            ),
            funding=float(payload.get("funding", 0.0)),
            reason=str(payload.get("reason", "")),
            notes=str(payload.get("notes", "")),
        )


@dataclass(frozen=True, kw_only=True)
class OpenResult:
    """Açılış denemesinin sonucu. `rejected` doluysa pozisyon açılmadı."""

    position: OpenPosition | None = None
    rejected: str = ""


@dataclass
class Account:
    """Bir stratejinin izole sanal hesabı (kural 4/6)."""

    model: str
    initial_capital: float
    cash: float
    positions: list[OpenPosition] = field(default_factory=list)

    def find(self, symbol: str, direction: Direction) -> OpenPosition | None:
        for position in self.positions:
            if position.symbol == symbol and position.direction == direction:
                return position
        return None


# --------------------------------------------------------------------------- #
# Saf kurallar: boyutlandırma ve likidasyon fiyatı
# --------------------------------------------------------------------------- #
def size_position(
    *,
    equity: float,
    free_cash: float,
    entry_price: float,
    stop_price: float,
    risk_per_trade: float,
    leverage_cap: float,
) -> Sizing:
    """CLAUDE.md kural 11: boyut = (risk_per_trade × sermaye) / |giriş − stop|.

    Sermaye tanımı: `equity` hesabın toplam değeridir (nakit + marj + gerçekleşmemiş PnL),
    yani riske atılan %1 her zaman hesabın %1'idir — açık pozisyon sayısına göre sessizce
    değişmez. Kaldıraç tavanı ise `free_cash` üzerinden uygulanır: pozisyonun marjı eldeki
    nakitten fazla olamaz, dolayısıyla hesabın toplam notional'ı da `leverage_cap × equity`
    sınırında kalır.

    Kaldıraç bir hedef değil sonuçtur: yalnızca gereken notional eldeki nakdi aşarsa
    devreye girer. `leverage_cap` aşılacaksa işlem ATLANMAZ, pozisyon tavana sığacak
    şekilde küçültülür (atlamak, geniş stop kullanan modellerin işlem sayısını sessizce
    düşürüp tam da ölçtüğümüz karşılaştırmayı bozardı).
    """
    distance = abs(entry_price - stop_price)
    if distance <= 0.0:
        raise ValueError("stop giriş fiyatına eşit: boyutlandırma sıfıra bölünürdü")
    if entry_price <= 0.0:
        raise ValueError(f"geçersiz giriş fiyatı: {entry_price}")
    if equity <= 0.0 or free_cash <= 0.0:
        return Sizing(qty=0.0, notional=0.0, margin=0.0, leverage=0.0, clipped=False,
                      note="sermaye/nakit kalmadı")

    qty = (risk_per_trade * equity) / distance
    notional = qty * entry_price
    leverage = 1.0 if notional <= free_cash else notional / free_cash

    if leverage <= leverage_cap:
        margin = min(notional / leverage, free_cash)
        return Sizing(qty=qty, notional=notional, margin=margin, leverage=leverage,
                      clipped=False, note="")

    capped_notional = leverage_cap * free_cash
    scale = capped_notional / notional
    note = (
        f"boyut leverage_cap={leverage_cap:g} nedeniyle {scale:.4f} oranında kırpıldı "
        f"(gereken kaldıraç {leverage:.2f})"
    )
    return Sizing(
        qty=qty * scale,
        notional=capped_notional,
        margin=free_cash,
        leverage=float(leverage_cap),
        clipped=True,
        note=note,
    )


def liquidation_price(
    *,
    direction: Direction,
    entry_price: float,
    qty: float,
    margin: float,
    maintenance_margin: float,
) -> float:
    """Pozisyon özsermayesinin (marj + gerçekleşmemiş PnL) bakım marjına indiği fiyat.

    Eşik giriş notional'ı üzerinden hesaplanır: mum içi mark fiyatına göre yeniden
    hesaplamak her barda farklı bir eşik üretir ve aynı senaryo iki koşuda farklı
    sonuç verebilir — tekrarlanabilirlik (random_seed ilkesi) bundan önce gelir.

    Kaldıraçsız (marj = notional) pozisyonda sonuç sıfırın yakınına düşer, yani pratikte
    likidasyon olmaz; kaldıraç arttıkça eşik giriş fiyatına yaklaşır.
    """
    if qty <= 0.0:
        raise ValueError("likidasyon fiyatı için miktar pozitif olmalı")
    buffer = margin / qty
    if direction == "long":
        return entry_price * (1.0 + maintenance_margin) - buffer
    return entry_price * (1.0 - maintenance_margin) + buffer


# --------------------------------------------------------------------------- #
# Portföy
# --------------------------------------------------------------------------- #
class Portfolio:
    """Her strateji için izole hesap durumu tutar ve tüm para kurallarını uygular."""

    def __init__(self, config: Mapping[str, Any]) -> None:
        config_dict = dict(config)
        self.initial_capital = float(get_setting(config_dict, "initial_capital"))
        self.risk_per_trade = float(get_setting(config_dict, "risk_per_trade"))
        self.leverage_cap = float(get_setting(config_dict, "leverage_cap"))
        self.max_positions = int(get_setting(config_dict, "max_positions"))
        self.max_short_positions = int(get_setting(config_dict, "max_short_positions"))
        self.fee_rate = float(get_setting(config_dict, "fee_rate"))
        self.slippage_long = float(get_setting(config_dict, "slippage_long"))
        self.slippage_short_stop = float(get_setting(config_dict, "slippage_short_stop"))
        self.maintenance_margin = float(get_setting(config_dict, "maintenance_margin"))
        self._accounts: dict[str, Account] = {}

    # ------------------------------------------------------------------ #
    # Hesap durumu
    # ------------------------------------------------------------------ #
    def account(self, model: str) -> Account:
        account = self._accounts.get(model)
        if account is None:
            account = Account(model=model, initial_capital=self.initial_capital,
                              cash=self.initial_capital)
            self._accounts[model] = account
        return account

    def cash(self, model: str) -> float:
        return self.account(model).cash

    def positions(self, model: str) -> tuple[OpenPosition, ...]:
        return tuple(self.account(model).positions)

    def position_views(self, model: str) -> list[Position]:
        """`Strategy.manage_positions`a verilecek salt okunur görünümler."""
        return [position.view() for position in self.account(model).positions]

    def margin_used(self, model: str) -> float:
        return sum(position.margin for position in self.account(model).positions)

    def unrealized_pnl(self, model: str, marks: Mapping[str, float]) -> float:
        return sum(
            _gross_pnl(position, _mark(position, marks), position.qty)
            for position in self.account(model).positions
        )

    def equity(self, model: str, marks: Mapping[str, float]) -> float:
        account = self.account(model)
        return account.cash + self.margin_used(model) + self.unrealized_pnl(model, marks)

    def load_state(self, model: str, state: Mapping[str, Any]) -> None:
        self._accounts[model] = Account(
            model=model,
            initial_capital=float(state.get("initial_capital", self.initial_capital)),
            cash=float(state["cash"]),
            positions=[OpenPosition.from_state(item) for item in state.get("positions", ())],
        )

    def to_state(self, model: str) -> dict[str, Any]:
        account = self.account(model)
        return {
            "initial_capital": account.initial_capital,
            "cash": account.cash,
            "positions": [position.as_state() for position in account.positions],
        }

    # ------------------------------------------------------------------ #
    # Dolum fiyatı: komisyon ve kayma
    # ------------------------------------------------------------------ #
    def fill_price(
        self,
        *,
        direction: Direction,
        reference_price: float,
        side: Literal["entry", "exit"],
        is_stop: bool = False,
    ) -> float:
        """Referans fiyata kaymayı DAİMA aleyhte uygular.

        config yalnızca iki kayma sabiti tanımlar: `slippage_long` her dolumun taban
        kayması, `slippage_short_stop` ise short stop dolumlarının (yukarı boşluklarda
        daha kötü dolan) özel hâlidir. Short girişlere/TP'lere kayma uygulamamak, projenin
        ana sorusunu (short'lar daha mı başarılı) shortlar lehine bozardı; bu yüzden taban
        kayma yönden bağımsız uygulanır, tek asimetri config'in açıkça istediğidir.
        """
        slippage = (
            self.slippage_short_stop
            if is_stop and direction == "short"
            else self.slippage_long
        )
        adverse = 1.0 if (direction == "long") == (side == "entry") else -1.0
        return reference_price * (1.0 + adverse * slippage)

    # ------------------------------------------------------------------ #
    # Açılış
    # ------------------------------------------------------------------ #
    def open_position(
        self,
        model: str,
        *,
        symbol: str,
        direction: Direction,
        stop_price: float,
        reference_price: float,
        ts: pd.Timestamp,
        marks: Mapping[str, float],
        take_profits: Sequence[TakeProfit] = (),
        trailing_atr: float | None = None,
        reason: str = "",
    ) -> OpenResult:
        """Bir sonraki barın açılışından pozisyon açar (kural 13); reddedilirse gerekçe döner."""
        account = self.account(model)

        if account.find(symbol, direction) is not None:
            return OpenResult(rejected=f"{symbol} üzerinde zaten açık {direction} pozisyon var")
        if len(account.positions) >= self.max_positions:
            return OpenResult(rejected=f"max_positions={self.max_positions} dolu")
        if direction == "short":
            open_shorts = sum(1 for p in account.positions if p.direction == "short")
            if open_shorts >= self.max_short_positions:
                return OpenResult(rejected=f"max_short_positions={self.max_short_positions} dolu")

        entry_price = self.fill_price(
            direction=direction, reference_price=reference_price, side="entry"
        )
        # Sinyal önceki barın kapanışına göre üretildi; bir sonraki bar stop'un ötesinde
        # açtıysa pozisyon doğduğu anda stoplanmış olurdu — böyle bir emir doldurulmaz.
        if direction == "long" and stop_price >= entry_price:
            return OpenResult(rejected=f"boşluklu açılış: stop {stop_price} >= dolum {entry_price}")
        if direction == "short" and stop_price <= entry_price:
            return OpenResult(rejected=f"boşluklu açılış: stop {stop_price} <= dolum {entry_price}")

        sizing = size_position(
            equity=self.equity(model, marks),
            free_cash=account.cash,
            entry_price=entry_price,
            stop_price=stop_price,
            risk_per_trade=self.risk_per_trade,
            leverage_cap=self.leverage_cap,
        )
        if sizing.qty <= 0.0:
            return OpenResult(rejected=f"boyut sıfır ({sizing.note or 'yetersiz sermaye'})")

        qty, notional, margin = sizing.qty, sizing.notional, sizing.margin
        fee = self.fee_rate * notional
        notes = sizing.note

        # Marj + komisyon nakdi aşamaz. Her büyüklük miktarla doğrusal olduğu için
        # tek bir ölçekle tam olarak nakde sığdırılır (yine kırpma, atlama değil).
        cost = margin + fee
        if cost > account.cash:
            scale = account.cash / cost
            qty *= scale
            notional *= scale
            margin *= scale
            fee *= scale
            notes = _join_notes(notes, f"boyut komisyon+marj nakde sığsın diye {scale:.4f} oranında kırpıldı")
        if qty <= 0.0:
            return OpenResult(rejected="nakit marj ve komisyonu karşılamıyor")

        position = OpenPosition(
            symbol=symbol,
            direction=direction,
            qty=qty,
            initial_qty=qty,
            entry_price=entry_price,
            stop_price=stop_price,
            initial_stop_price=stop_price,
            opened_at=ts,
            margin=margin,
            leverage=sizing.leverage,
            entry_fee=fee,
            liq_price=liquidation_price(
                direction=direction,
                entry_price=entry_price,
                qty=qty,
                margin=margin,
                maintenance_margin=self.maintenance_margin,
            ),
            high_water=entry_price,
            low_water=entry_price,
            take_profits=_ordered_take_profits(direction, take_profits),
            trailing_atr=trailing_atr,
            reason=reason,
            notes=notes,
        )
        account.cash -= margin + fee
        account.positions.append(position)
        if sizing.clipped:
            logger.info("%s %s %s: %s", model, symbol, direction, sizing.note)
        return OpenResult(position=position)

    # ------------------------------------------------------------------ #
    # Bar işleme: likidasyon -> stop -> TP
    # ------------------------------------------------------------------ #
    def process_bar(
        self, model: str, *, ts: pd.Timestamp, bars: Mapping[str, Bar]
    ) -> list[Trade]:
        """Açık pozisyonları tek bir mum boyunca ilerletir ve kapananların kaydını döndürür."""
        account = self.account(model)
        trades: list[Trade] = []

        for position in list(account.positions):
            bar = bars.get(position.symbol)
            if bar is None:
                # Sembol o tur evrende görünmüyorsa (gecikmiş/durdurulmuş) pozisyona
                # dokunulmaz: elimizde olmayan mumla stop tetiklemek uydurma olurdu.
                logger.warning(
                    "%s %s: %s barı yok, pozisyon bu barda kontrol edilmedi", model, position.symbol, ts
                )
                continue
            trades.extend(self._process_position(account, position, bar=bar, ts=ts))

        return trades

    def _process_position(
        self, account: Account, position: OpenPosition, *, bar: Bar, ts: pd.Timestamp
    ) -> list[Trade]:
        long = position.direction == "long"

        # 1) Likidasyon — stop'tan ÖNCE. Bakım marjı ihlali gerçek borsada stop emrini
        #    beklemez; likide olan pozisyon stop'a hiç ulaşmaz (CLAUDE.md kural 13).
        if (long and bar.low <= position.liq_price) or (not long and bar.high >= position.liq_price):
            return [self._close(account, position, exit_price=position.liq_price, ts=ts,
                                fraction_of_initial=1.0, exit_reason="liquidation")]

        # 2) Stop. Mum stop'un ötesinde AÇTIYSA dolum stop'ta değil açılışta gerçekleşir
        #    (boşluk); iki fiyattan aleyhte olanı seçilir, üstüne kayma uygulanır.
        if (long and bar.low <= position.stop_price) or (not long and bar.high >= position.stop_price):
            reference = min(position.stop_price, bar.open) if long else max(position.stop_price, bar.open)
            exit_price = self.fill_price(
                direction=position.direction, reference_price=reference, side="exit", is_stop=True
            )
            return [self._close(account, position, exit_price=exit_price, ts=ts,
                                fraction_of_initial=1.0, exit_reason="stop")]

        # 3) Take-profit. Buraya yalnızca stop AYNI mumda tetiklenmediyse gelinir: stop ve TP
        #    aynı mumun aralığındaysa mum içi sıralama bilinemeyeceği için kötü olan (stop)
        #    gerçekleşmiş varsayılır ve yukarıdaki dal döner.
        trades: list[Trade] = []
        for take_profit in list(position.take_profits):
            touched = bar.high >= take_profit.price if long else bar.low <= take_profit.price
            if not touched:
                continue
            # Limit emri kendi fiyatından dolar; mum TP'nin ötesinde açtıysa oluşan lehte
            # boşluk kâr yazılmaz (iyimser varsayımdan kaçınma).
            exit_price = self.fill_price(
                direction=position.direction, reference_price=take_profit.price, side="exit"
            )
            position.take_profits = tuple(tp for tp in position.take_profits if tp is not take_profit)
            trades.append(
                self._close(account, position, exit_price=exit_price, ts=ts,
                            fraction_of_initial=take_profit.fraction, exit_reason="tp")
            )
            if position.qty <= 0.0:
                break

        if position.qty > 0.0:
            position.high_water = max(position.high_water, bar.high)
            position.low_water = min(position.low_water, bar.low)
        return trades

    def close_position(
        self,
        model: str,
        *,
        symbol: str,
        direction: Direction,
        reference_price: float,
        ts: pd.Timestamp,
        fraction: float = 1.0,
        exit_reason: ExitReason = "signal",
    ) -> Trade | None:
        """Strateji talimatıyla (manage_positions) kapatma/kısmi çıkış."""
        account = self.account(model)
        position = account.find(symbol, direction)
        if position is None:
            return None
        exit_price = self.fill_price(
            direction=direction, reference_price=reference_price, side="exit"
        )
        return self._close(account, position, exit_price=exit_price, ts=ts,
                           fraction_of_initial=fraction, exit_reason=exit_reason)

    def _close(
        self,
        account: Account,
        position: OpenPosition,
        *,
        exit_price: float,
        ts: pd.Timestamp,
        fraction_of_initial: float,
        exit_reason: ExitReason,
    ) -> Trade:
        """Pozisyonun `fraction_of_initial` dilimini kapatır ve nakit etkisini işler.

        Oranlar BAŞLANGIÇ miktarı üzerindendir: `TakeProfit.fraction` toplamı bir sinyal
        içinde 1.0'ı aşamaz (sözleşme), yani hedefler baştaki boyutun dilimlerini ifade
        eder. `ExitInstruction.fraction` da aynı tabanı kullanır ki iki yol aynı anlama
        gelsin.
        """
        qty = min(position.initial_qty * fraction_of_initial, position.qty)
        if exit_reason == "liquidation":
            qty = position.qty
        share = qty / position.qty if position.qty > 0.0 else 0.0

        margin_part = position.margin * share
        entry_fee_part = position.entry_fee * share
        funding_part = position.funding * share
        gross = _gross_pnl(position, exit_price, qty)

        if exit_reason == "liquidation":
            # Marjın tamamı gider: borsaya geri dönen nakit yoktur, ayrıca çıkış komisyonu
            # da yazılmaz (pozisyonun değeri zaten sıfırlanmıştır).
            exit_fee = 0.0
            cash_back = 0.0
            pnl = -(margin_part + entry_fee_part) + funding_part
        else:
            exit_fee = self.fee_rate * qty * exit_price
            cash_back = margin_part + gross - exit_fee
            pnl = gross - entry_fee_part - exit_fee + funding_part

        account.cash += cash_back
        position.qty -= qty
        position.margin -= margin_part
        position.entry_fee -= entry_fee_part
        position.funding -= funding_part
        if position.qty <= position.initial_qty * _DUST_RATIO:
            account.positions.remove(position)

        return Trade(
            strategy=account.model,
            symbol=position.symbol,
            direction=position.direction,
            opened_at=position.opened_at,
            closed_at=ts,
            entry_price=position.entry_price,
            exit_price=exit_price,
            qty=qty,
            notional=qty * position.entry_price,
            leverage=position.leverage,
            margin=margin_part,
            fee=entry_fee_part + exit_fee,
            funding=funding_part,
            pnl=pnl,
            exit_reason=exit_reason,
            signal_reason=position.reason,
            notes=position.notes,
        )

    # ------------------------------------------------------------------ #
    # Funding ve trailing stop
    # ------------------------------------------------------------------ #
    def apply_funding(self, model: str, charges: Iterable[Any]) -> float:
        """core/funding.py'nin ürettiği tahakkukları nakde ve pozisyona işler.

        Tutarın işareti funding modülünde belirlenir (+ alınan, - ödenen); burada yalnızca
        nakit hareketi yapılır — maliyet kuralının iki yerde durması, iki yerde bozulması
        demektir (kural 2).
        """
        account = self.account(model)
        total = 0.0
        for charge in charges:
            position = account.find(charge.symbol, charge.direction)
            if position is None:
                continue
            position.funding += charge.amount
            account.cash += charge.amount
            total += charge.amount
        return total

    def set_stop_price(
        self, model: str, *, symbol: str, direction: Direction, stop_price: float
    ) -> bool:
        """Stop'u yalnızca SIKILAŞTIRIR (trailing mantığı core/engine.py'de).

        Gevşeme yönünde hareket, zarardaki bir pozisyonun stop'unu kaçırıp riski sessizce
        büyütmek olurdu; hesabın değişmezi olarak burada engellenir.
        """
        position = self.account(model).find(symbol, direction)
        if position is None:
            return False
        tightened = (
            max(position.stop_price, stop_price)
            if direction == "long"
            else min(position.stop_price, stop_price)
        )
        if tightened == position.stop_price:
            return False
        position.stop_price = tightened
        return True


# --------------------------------------------------------------------------- #
# Yardımcılar
# --------------------------------------------------------------------------- #
def _gross_pnl(position: OpenPosition, price: float, qty: float) -> float:
    sign = 1.0 if position.direction == "long" else -1.0
    return sign * qty * (price - position.entry_price)


def _mark(position: OpenPosition, marks: Mapping[str, float]) -> float:
    # Fiyatı olmayan sembol için giriş fiyatı kullanılır: bilgi yokken pozisyonu kâr ya da
    # zararda göstermek, boyutlandırmanın dayandığı sermayeyi uydurmak olurdu.
    return float(marks.get(position.symbol, position.entry_price))


def _ordered_take_profits(
    direction: Direction, take_profits: Sequence[TakeProfit]
) -> tuple[TakeProfit, ...]:
    """Hedefler girişe en yakından uzağa sıralanır: aynı mumda birden fazlası dolarsa sıra bellidir."""
    return tuple(sorted(take_profits, key=lambda tp: tp.price, reverse=direction == "short"))


def _join_notes(*notes: str) -> str:
    return "; ".join(note for note in notes if note)


def _to_utc(value: Any) -> pd.Timestamp:
    stamp = pd.Timestamp(value)
    return stamp.tz_localize("UTC") if stamp.tzinfo is None else stamp.tz_convert("UTC")

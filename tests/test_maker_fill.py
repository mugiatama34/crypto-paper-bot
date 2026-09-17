"""POST-ONLY limit dolum ve skorla boyut: F1'in iki çekirdek yeteneği.

**Neden çekirdekte.** İkisi de kural 13'ün (dolum bir sonraki barın açılışında) ve kural
3/11'in (boyutu yalnızca portföy belirler) sınırları içinde kalmak zorundadır; bir
stratejinin kendi dolum fiyatını ya da kendi risk oranını yazması, ölçümün ortak birimini
(1R) ortadan kaldırırdı. Bu yüzden strateji YALNIZCA ister (`entry_type`, `limit_price`,
`size_scale`), uygulayan taraf motor ve portföydür.

**Maker indirimi BEDAVA DEĞİLDİR ve bedeli burada ölçülür:** kitapta bekleyen emir, fiyat
oraya gelmezse dolmaz ve `limit_not_filled` ile sayılır. Bir backtest'in en kolay yalanı,
maker komisyonunu alıp dolmama riskini almamaktır.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from core.config import load_config
from core.engine import Engine, PendingOrder
from core.ledger import Ledger
from core.portfolio import Bar, Portfolio
from strategies.base import MarketData, Signal, Strategy

SYMBOL = "BTC-USDT-SWAP"
START = pd.Timestamp("2026-01-01 00:00:00", tz="UTC")

# bar0: sinyal, bar1: dolum penceresi (aşağı fitili 98'e iner), bar2: kapanış kontrolü
ROWS = [
    (100.0, 101.0, 99.0, 100.0),
    (100.5, 101.5, 98.0, 101.0),
    (101.0, 102.0, 100.0, 101.5),
]


def _market(bars: int) -> MarketData:
    index = pd.date_range(START, periods=len(ROWS), freq="4h", tz="UTC", name="ts")
    frame = pd.DataFrame(list(ROWS), index=index, columns=["open", "high", "low", "close"])
    frame["volume"] = 1.0
    frame = frame.head(bars)
    return MarketData(ohlcv={SYMBOL: frame}, btc=frame, funding={}, as_of=frame.index[-1])


def _config(**overrides: Any) -> dict[str, Any]:
    config = load_config()
    config.update(overrides)
    return config


class _Once(Strategy):
    """İlk barda tek bir sinyal veren sahte model."""

    allowed_directions = ["long", "short"]

    def __init__(self, name: str, signal: Signal) -> None:
        self.name = name
        self._signal = signal

    def generate_signals(self, market: MarketData, peer_signals=None) -> list[Signal]:
        return [self._signal] if market.as_of == START else []


def _run(signal: Signal, *, tmp_path: Path, bars: int = 2, **config: Any):
    strategy = _Once("m", signal)
    ledger = Ledger(tmp_path)
    portfolio = Portfolio(_config(**config))
    engine = Engine([strategy], config=_config(**config), ledger=ledger, portfolio=portfolio)
    engine.run_round(_market(1))
    report = engine.run_round(_market(bars))
    return report, portfolio


# --------------------------------------------------------------------------- #
# Dolum
# --------------------------------------------------------------------------- #
def test_a_touched_limit_fills_at_its_own_price_with_the_maker_fee(tmp_path: Path) -> None:
    """Pasif dolumda kayma YOKTUR: emir kitapta BELİRLİ bir fiyatta duruyordu."""
    signal = Signal(
        symbol=SYMBOL, direction="long", stop_price=90.0,
        entry_type="limit", limit_price=99.0, reason="test",
    )

    report, portfolio = _run(signal, tmp_path=tmp_path)

    position = portfolio.positions("m")[0]
    assert position.entry_price == pytest.approx(99.0)   # kayma yok
    assert position.entry_slippage == pytest.approx(0.0)
    maker = float(load_config()["maker_fee_rate"])
    assert position.entry_fee == pytest.approx(maker * position.qty * 99.0)
    assert report.models[0].filled == 1


def test_an_untouched_limit_is_cancelled_and_counted(tmp_path: Path) -> None:
    """Maker indiriminin BEDELİ: fiyat gelmezse emir dolmaz ve bu sayılır."""
    signal = Signal(
        symbol=SYMBOL, direction="long", stop_price=80.0,
        entry_type="limit", limit_price=95.0, reason="test",   # bar1 en düşük 98.0
    )

    report, portfolio = _run(signal, tmp_path=tmp_path)

    assert portfolio.positions("m") == ()
    assert report.models[0].filled == 0
    assert report.models[0].rejections["limit_not_filled"] == 1


def test_a_market_order_still_pays_taker_and_slippage(tmp_path: Path) -> None:
    """Varsayılan yol DEĞİŞMEDİ: piyasa emri barın AÇILIŞINDAN, kaymayla, taker ücretle."""
    signal = Signal(symbol=SYMBOL, direction="long", stop_price=90.0, reason="test")

    _, portfolio = _run(signal, tmp_path=tmp_path)

    position = portfolio.positions("m")[0]
    config = load_config()
    expected = 100.5 * (1.0 + float(config["slippage_base"]))  # bar1 açılışı + kayma
    assert position.entry_price == pytest.approx(expected)
    assert position.entry_fee == pytest.approx(
        float(config["fee_rate"]) * position.qty * position.entry_price
    )


def test_the_short_side_needs_the_high_to_reach_the_limit(tmp_path: Path) -> None:
    signal = Signal(
        symbol=SYMBOL, direction="short", stop_price=110.0,
        entry_type="limit", limit_price=101.4, reason="test",  # bar1 en yüksek 101.5
    )

    _, portfolio = _run(signal, tmp_path=tmp_path)

    assert portfolio.positions("m")[0].entry_price == pytest.approx(101.4)


def test_the_order_type_survives_a_round_boundary() -> None:
    """Emir koşular arası defterde taşınır (kural 13): tip de taşınmalıdır.

    Taşınmasaydı bir sonraki turda dolan limit emri sessizce piyasa emrine dönüşür ve
    maker varsayımı deftere yanlış yazılırdı.
    """
    order = PendingOrder(
        kind="open", symbol=SYMBOL, direction="long", created_at=START,
        stop_price=90.0, entry_type="limit", limit_price=99.0, size_scale=0.5,
    )

    restored = PendingOrder.from_state(order.as_state())

    assert restored.entry_type == "limit"
    assert restored.limit_price == pytest.approx(99.0)
    assert restored.size_scale == pytest.approx(0.5)


def test_old_ledgers_default_to_market_orders() -> None:
    """Alanı olmayan eski bir kayıt "piyasa emri, tam boy" demektir."""
    restored = PendingOrder.from_state(
        {"kind": "open", "symbol": SYMBOL, "direction": "long",
         "created_at": START.isoformat()}
    )

    assert restored.entry_type == "market"
    assert restored.limit_price is None
    assert restored.size_scale == pytest.approx(1.0)


# --------------------------------------------------------------------------- #
# Skorla boyut
# --------------------------------------------------------------------------- #
def test_size_scale_shrinks_the_position_proportionally(tmp_path: Path) -> None:
    """Ölçek yalnızca KÜÇÜLTÜR; 1R'nin tanımı korunur çünkü payda gerçekleşen risktir."""
    full = Signal(symbol=SYMBOL, direction="long", stop_price=90.0, reason="test")
    half = Signal(
        symbol=SYMBOL, direction="long", stop_price=90.0, size_scale=0.5, reason="test"
    )

    _, full_portfolio = _run(full, tmp_path=tmp_path / "full")
    _, half_portfolio = _run(half, tmp_path=tmp_path / "half")

    full_qty = full_portfolio.positions("m")[0].qty
    half_qty = half_portfolio.positions("m")[0].qty
    assert half_qty == pytest.approx(full_qty * 0.5)
    # Komisyon da yarıya iner: maliyet boyutla DOĞRUSALDIR ve öyle kalmalıdır.
    assert half_portfolio.positions("m")[0].entry_fee == pytest.approx(
        full_portfolio.positions("m")[0].entry_fee * 0.5
    )


def test_scaling_beyond_full_size_is_a_programming_error() -> None:
    """Büyütme, modelin kendi risk oranını seçmesi demekti (kural 3/11)."""
    portfolio = Portfolio(_config())

    with pytest.raises(ValueError, match="size_scale"):
        portfolio.open_position(
            "m", symbol=SYMBOL, direction="long", stop_price=95.0,
            reference_price=100.0, ts=START, marks={SYMBOL: 100.0}, size_scale=1.5,
        )


def test_the_scaled_position_records_why_it_shrank(tmp_path: Path) -> None:
    """Kırpma sessiz olamaz: gerekçe deftere yazılır (kural 14'ün aynı ilkesi)."""
    _, portfolio = _run(
        Signal(symbol=SYMBOL, direction="long", stop_price=90.0, size_scale=0.25,
               reason="test"),
        tmp_path=tmp_path,
    )

    assert "skorla" in portfolio.positions("m")[0].notes

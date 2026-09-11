"""core/engine.py: dolum sırası, iki geçişli tur, izolasyon ve defter kalıcılığı.

Buradaki testler zamanlama kurallarını çiviler: sinyal ÜRETİLDİĞİ barda dolmaz (kural 13),
meta modeller yalnızca normal modellerin sinyallerini görür (kural 4), bir modelin hatası
turu düşürmez (kural 8).
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Mapping, Sequence

import pandas as pd
import pytest

from core.config import load_config
from core.engine import Engine, average_true_range
from core.ledger import Ledger
from strategies.base import (
    ExitInstruction,
    MarketData,
    Position,
    Signal,
    Strategy,
    TakeProfit,
)

SYMBOL = "BTC-USDT-SWAP"
START = pd.Timestamp("2026-01-01 00:00:00", tz="UTC")


# --------------------------------------------------------------------------- #
# Test yardımcıları
# --------------------------------------------------------------------------- #
def _frame(rows: Sequence[tuple[float, float, float, float]]) -> pd.DataFrame:
    index = pd.date_range(START, periods=len(rows), freq="4h", tz="UTC", name="ts")
    frame = pd.DataFrame(list(rows), index=index, columns=["open", "high", "low", "close"])
    frame["volume"] = 1.0
    return frame


def _market(
    rows: Sequence[tuple[float, float, float, float]],
    *,
    bars: int | None = None,
    funding: Mapping[str, pd.Series] | None = None,
) -> MarketData:
    frame = _frame(rows)
    if bars is not None:
        frame = frame.head(bars)
    return MarketData(
        ohlcv={SYMBOL: frame},
        btc=frame,
        funding=dict(funding or {}),
        as_of=frame.index[-1],
    )


def _config(**overrides: Any) -> dict[str, Any]:
    config = load_config()
    config.update(fee_rate=0.0, slippage_base=0.0, slippage_short_stop=0.0)
    config.update(overrides)
    return config


class _Scripted(Strategy):
    """Barına göre önceden yazılmış sinyal/çıkış üreten sahte model."""

    is_meta = False

    def __init__(
        self,
        name: str,
        *,
        allowed_directions: Sequence[str] = ("long", "short"),
        signals: Mapping[pd.Timestamp, Sequence[Signal]] | None = None,
        exits: Mapping[pd.Timestamp, Sequence[ExitInstruction]] | None = None,
    ) -> None:
        self.name = name
        self.allowed_directions = list(allowed_directions)  # type: ignore[assignment]
        self._signals = dict(signals or {})
        self._exits = dict(exits or {})
        self.seen_positions: list[list[Position]] = []

    def generate_signals(
        self, market: MarketData, peer_signals: Mapping[str, tuple[Signal, ...]] | None = None
    ) -> list[Signal]:
        assert peer_signals is None  # normal modeller akran sinyali görmez (kural 4)
        return list(self._signals.get(market.as_of, ()))

    def manage_positions(
        self, market: MarketData, positions: list[Position]
    ) -> list[ExitInstruction]:
        self.seen_positions.append(list(positions))
        return list(self._exits.get(market.as_of, ()))


class _Meta(Strategy):
    is_meta = True

    def __init__(self, name: str) -> None:
        self.name = name
        self.allowed_directions = ["long", "short"]
        self.peers: Mapping[str, tuple[Signal, ...]] | None = None

    def generate_signals(
        self, market: MarketData, peer_signals: Mapping[str, tuple[Signal, ...]] | None = None
    ) -> list[Signal]:
        self.peers = peer_signals
        return []


class _Broken(Strategy):
    name = "broken"
    allowed_directions = ["long", "short"]

    def generate_signals(
        self, market: MarketData, peer_signals: Mapping[str, tuple[Signal, ...]] | None = None
    ) -> list[Signal]:
        raise RuntimeError("model çöktü")


def _long_signal(stop: float = 95.0, **overrides: Any) -> Signal:
    return Signal(symbol=SYMBOL, direction="long", stop_price=stop, reason="test", **overrides)


ROWS = [
    (100.0, 101.0, 99.0, 100.0),   # bar0 00:00 — sinyal burada üretilir
    (102.0, 103.0, 101.0, 102.0),  # bar1 04:00 — dolum burada
    (102.0, 104.0, 101.5, 103.5),  # bar2 08:00
]


# --------------------------------------------------------------------------- #
# Dolum kuralı (kural 13)
# --------------------------------------------------------------------------- #
def test_signal_does_not_fill_on_the_bar_it_was_generated(tmp_path: Path) -> None:
    strategy = _Scripted("m", signals={START: [_long_signal()]})
    engine = Engine([strategy], config=_config(), ledger=Ledger(tmp_path))

    report = engine.run_round(_market(ROWS, bars=1))

    state = Ledger(tmp_path).load_state("m")
    assert state is not None
    assert state["positions"] == []
    assert len(state["pending_orders"]) == 1
    assert state["last_processed_bar"] == START.isoformat()
    assert report.by_model("m").signals == 1  # type: ignore[union-attr]


def test_pending_order_fills_at_the_next_bar_open(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path)
    strategy = _Scripted("m", signals={START: [_long_signal()]})
    engine = Engine([strategy], config=_config(), ledger=ledger)

    engine.run_round(_market(ROWS, bars=1))
    report = engine.run_round(_market(ROWS, bars=2))

    state = ledger.load_state("m")
    assert state is not None
    (position,) = state["positions"]
    assert position["entry_price"] == pytest.approx(102.0)  # bar1'in AÇILIŞI
    assert position["opened_at"] == pd.Timestamp("2026-01-01 04:00", tz="UTC").isoformat()
    assert state["pending_orders"] == []
    assert report.by_model("m").filled == 1  # type: ignore[union-attr]


def test_fill_price_carries_fee_and_slippage(tmp_path: Path) -> None:
    config = load_config()  # gerçek maliyetler
    strategy = _Scripted("m", signals={START: [_long_signal()]})
    engine = Engine([strategy], config=config, ledger=Ledger(tmp_path))

    engine.run_round(_market(ROWS, bars=1))
    engine.run_round(_market(ROWS, bars=2))

    state = Ledger(tmp_path).load_state("m")
    assert state is not None
    (position,) = state["positions"]
    entry = 102.0 * (1 + config["slippage_base"])
    assert position["entry_price"] == pytest.approx(entry)
    notional = position["qty"] * entry
    assert state["cash"] == pytest.approx(
        config["initial_capital"] - position["margin"] - notional * config["fee_rate"]
    )


def test_state_survives_a_fresh_engine_instance(tmp_path: Path) -> None:
    """Cron'la çalışan sistemde tur bir süreç, defter tek hafızadır."""
    signals = {START: [_long_signal()]}
    Engine([_Scripted("m", signals=signals)], config=_config(), ledger=Ledger(tmp_path)).run_round(
        _market(ROWS, bars=1)
    )
    Engine([_Scripted("m", signals=signals)], config=_config(), ledger=Ledger(tmp_path)).run_round(
        _market(ROWS, bars=2)
    )

    state = Ledger(tmp_path).load_state("m")
    assert state is not None and len(state["positions"]) == 1


def test_bars_are_processed_once(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path)
    engine = Engine([_Scripted("m")], config=_config(), ledger=ledger)
    engine.run_round(_market(ROWS, bars=2))
    engine.run_round(_market(ROWS, bars=2))  # aynı as_of ile tekrar
    engine.run_round(_market(ROWS, bars=3))

    timestamps = [row["ts"] for row in ledger.read_equity("m")]
    assert timestamps == sorted(set(timestamps))
    assert len(timestamps) == 2  # ilk tur yalnızca as_of'u, ikinci tur yeni barı işler


# --------------------------------------------------------------------------- #
# İşlem yaşam döngüsü ve defter
# --------------------------------------------------------------------------- #
def test_closed_trade_is_written_to_the_ledger(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path)
    rows = [
        (100.0, 101.0, 99.0, 100.0),
        (102.0, 103.0, 101.0, 102.0),
        (102.0, 102.5, 94.0, 95.0),  # stop (95) bu barda vurulur
    ]
    strategy = _Scripted("m", signals={START: [_long_signal(stop=95.0)]})
    engine = Engine([strategy], config=_config(), ledger=ledger)

    engine.run_round(_market(rows, bars=1))
    engine.run_round(_market(rows, bars=3))

    (trade,) = ledger.read_trades("m")
    assert trade["symbol"] == SYMBOL
    assert trade["direction"] == "long"
    assert trade["exit_reason"] == "stop"
    assert trade["signal_reason"] == "test"
    assert float(trade["exit_price"]) == pytest.approx(95.0)
    assert float(trade["pnl"]) < 0.0
    assert ledger.load_state("m")["positions"] == []  # type: ignore[index]


def test_exit_instruction_closes_at_the_next_bar_open(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path)
    bar1 = pd.Timestamp("2026-01-01 04:00", tz="UTC")
    strategy = _Scripted(
        "m",
        signals={START: [_long_signal()]},
        exits={bar1: [ExitInstruction(symbol=SYMBOL, action="close", reason="kapat")]},
    )
    engine = Engine([strategy], config=_config(), ledger=ledger)

    engine.run_round(_market(ROWS, bars=1))
    engine.run_round(_market(ROWS, bars=2))  # dolum + çıkış talimatı
    engine.run_round(_market(ROWS, bars=3))  # çıkış bar2'nin açılışından dolar

    (trade,) = ledger.read_trades("m")
    assert trade["exit_reason"] == "signal"
    assert float(trade["exit_price"]) == pytest.approx(102.0)  # bar2 açılışı
    assert ledger.load_state("m")["positions"] == []  # type: ignore[index]


def test_manage_positions_sees_a_read_only_view_without_size(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path)
    strategy = _Scripted("m", signals={START: [_long_signal()]})
    engine = Engine([strategy], config=_config(), ledger=ledger)
    engine.run_round(_market(ROWS, bars=1))
    engine.run_round(_market(ROWS, bars=2))

    (view,) = strategy.seen_positions[-1]
    assert isinstance(view, Position)
    assert not hasattr(view, "qty")  # kural 3: strateji boyutu görmez
    with pytest.raises(Exception):
        view.stop_price = 1.0  # type: ignore[misc]


def test_take_profit_partial_exit_is_recorded(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path)
    rows = [
        (100.0, 101.0, 99.0, 100.0),
        (102.0, 103.0, 101.0, 102.0),
        (102.0, 111.0, 101.5, 110.0),  # TP 110'a değer
    ]
    signal = _long_signal(stop=95.0, take_profits=(TakeProfit(price=110.0, fraction=0.5),))
    engine = Engine([_Scripted("m", signals={START: [signal]})], config=_config(), ledger=ledger)

    engine.run_round(_market(rows, bars=1))
    engine.run_round(_market(rows, bars=3))

    (trade,) = ledger.read_trades("m")
    assert trade["exit_reason"] == "tp"
    assert float(trade["pnl"]) > 0.0
    (position,) = ledger.load_state("m")["positions"]  # type: ignore[index]
    assert position["qty"] == pytest.approx(position["initial_qty"] / 2)


def test_funding_is_charged_while_the_position_is_open(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path)
    funding_ts = pd.Timestamp("2026-01-01 08:00", tz="UTC")
    series = pd.Series(
        [0.0002], index=pd.DatetimeIndex([funding_ts], name="ts"), dtype="float64"
    )
    strategy = _Scripted("m", signals={START: [_long_signal()]})
    engine = Engine([strategy], config=_config(), ledger=ledger)

    engine.run_round(_market(ROWS, bars=1, funding={SYMBOL: series}))
    engine.run_round(_market(ROWS, bars=3, funding={SYMBOL: series}))

    (position,) = ledger.load_state("m")["positions"]  # type: ignore[index]
    # 04:00'te açıldı, 08:00 periyodunu ÖDEDİ (long, pozitif oran)
    assert position["funding"] == pytest.approx(-position["qty"] * 102.0 * 0.0002)


# --------------------------------------------------------------------------- #
# İki geçişli tur ve izolasyon (kural 4)
# --------------------------------------------------------------------------- #
def test_meta_models_see_only_normal_model_signals(tmp_path: Path) -> None:
    normal_a = _Scripted("a", signals={START: [_long_signal()]})
    normal_b = _Scripted("b", signals={START: []})
    meta_one, meta_two = _Meta("meta1"), _Meta("meta2")
    engine = Engine(
        [normal_a, meta_one, normal_b, meta_two], config=_config(), ledger=Ledger(tmp_path)
    )

    engine.run_round(_market(ROWS, bars=1))

    for meta in (meta_one, meta_two):
        assert meta.peers is not None
        assert set(meta.peers) == {"a", "b"}  # meta'lar birbirini görmez
        assert len(meta.peers["a"]) == 1


def test_each_meta_model_gets_its_own_copy_of_peer_signals(tmp_path: Path) -> None:
    normal = _Scripted("a", signals={START: [_long_signal()]})
    meta_one, meta_two = _Meta("meta1"), _Meta("meta2")
    engine = Engine([normal, meta_one, meta_two], config=_config(), ledger=Ledger(tmp_path))

    engine.run_round(_market(ROWS, bars=1))

    assert meta_one.peers is not None and meta_two.peers is not None
    assert meta_one.peers["a"][0] is not meta_two.peers["a"][0]
    assert meta_one.peers["a"][0] == meta_two.peers["a"][0]


def test_duplicate_strategy_names_are_refused() -> None:
    with pytest.raises(ValueError, match="benzersiz"):
        Engine([_Scripted("m"), _Scripted("m")], config=_config(), ledger=Ledger())


# --------------------------------------------------------------------------- #
# Hata izolasyonu (kural 8)
# --------------------------------------------------------------------------- #
def test_a_crashing_model_does_not_stop_the_round(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path)
    healthy = _Scripted("healthy", signals={START: [_long_signal()]})
    engine = Engine([_Broken(), healthy], config=_config(), ledger=ledger)

    report = engine.run_round(_market(ROWS, bars=1))

    assert "model çöktü" in report.by_model("broken").skipped  # type: ignore[union-attr]
    assert report.by_model("healthy").signals == 1  # type: ignore[union-attr]
    assert len(ledger.load_state("healthy")["pending_orders"]) == 1  # type: ignore[index]
    assert ledger.load_state("broken")["pending_orders"] == []  # type: ignore[index]


def test_signal_violating_allowed_directions_skips_only_that_model(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path)
    offender = _Scripted(
        "short_only",
        allowed_directions=("short",),
        signals={START: [_long_signal()]},
    )
    healthy = _Scripted("healthy", signals={START: [_long_signal()]})
    engine = Engine([offender, healthy], config=_config(), ledger=ledger)

    report = engine.run_round(_market(ROWS, bars=1))

    assert "izinli değil" in report.by_model("short_only").skipped  # type: ignore[union-attr]
    assert ledger.load_state("short_only")["pending_orders"] == []  # type: ignore[index]
    assert len(ledger.load_state("healthy")["pending_orders"]) == 1  # type: ignore[index]


# --------------------------------------------------------------------------- #
# Trailing stop (kural 9)
# --------------------------------------------------------------------------- #
def test_trailing_stop_tightens_as_price_advances(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path)
    rows = [(100.0 + i, 101.0 + i, 99.0 + i, 100.0 + i) for i in range(20)]
    signal = _long_signal(stop=95.0, trailing_atr=1.0)
    engine = Engine(
        [_Scripted("m", signals={START: [signal]})],
        config=_config(**{"trailing": {"atr_period": 3}}),
        ledger=ledger,
    )

    engine.run_round(_market(rows, bars=1))
    engine.run_round(_market(rows, bars=len(rows)))

    (position,) = ledger.load_state("m")["positions"]  # type: ignore[index]
    assert position["stop_price"] > position["initial_stop_price"]
    assert position["stop_price"] < rows[-1][3]  # stop hâlâ fiyatın altında


def test_trailing_needs_enough_history() -> None:
    frame = _frame(ROWS)
    assert average_true_range(frame, 10) is None
    # TR(bar1)=max(2, |103-100|, |101-100|)=3, TR(bar2)=max(2.5, 2, 0.5)=2.5
    assert average_true_range(frame, 2) == pytest.approx(2.75)


def test_atr_period_must_be_positive() -> None:
    with pytest.raises(ValueError, match="atr_period"):
        average_true_range(_frame(ROWS), 0)


# --------------------------------------------------------------------------- #
# Kenar durumlar
# --------------------------------------------------------------------------- #
def test_round_without_strategies_is_a_no_op(tmp_path: Path) -> None:
    report = Engine([], config=_config(), ledger=Ledger(tmp_path)).run_round(_market(ROWS, bars=1))
    assert report.models == ()


def test_equity_row_is_written_for_every_processed_bar(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path)
    engine = Engine([_Scripted("m")], config=_config(), ledger=ledger)
    engine.run_round(_market(ROWS, bars=1))
    engine.run_round(_market(ROWS, bars=3))

    rows = ledger.read_equity("m")
    assert len(rows) == 3
    assert all(float(row["equity"]) == pytest.approx(10_000.0) for row in rows)


def test_rerunning_the_same_bar_does_not_queue_the_signal_twice(tmp_path: Path) -> None:
    """Cron retry ya da elle tekrar, aynı bar için ikinci bir pozisyon açtırmamalı."""
    ledger = Ledger(tmp_path)
    signals = {START: [_long_signal()], pd.Timestamp("2026-01-01 04:00", tz="UTC"): []}
    engine = Engine([_Scripted("m", signals=signals)], config=_config(), ledger=ledger)

    engine.run_round(_market(ROWS, bars=1))
    engine.run_round(_market(ROWS, bars=1))  # aynı as_of, ikinci kez

    assert len(ledger.load_state("m")["pending_orders"]) == 1  # type: ignore[index]

    engine.run_round(_market(ROWS, bars=2))
    assert len(ledger.load_state("m")["positions"]) == 1  # type: ignore[index]


def test_order_is_cancelled_when_the_symbol_leaves_the_universe(tmp_path: Path) -> None:
    """Sembol bir sonraki turda görülemiyorsa emir iptal edilir; gecikmeli dolum yok."""
    ledger = Ledger(tmp_path)
    engine = Engine(
        [_Scripted("m", signals={START: [_long_signal()]})], config=_config(), ledger=ledger
    )
    engine.run_round(_market(ROWS, bars=1))

    frame = _frame(ROWS).head(2)
    blind = MarketData(ohlcv={}, btc=frame, funding={}, as_of=frame.index[-1])
    engine.run_round(blind)

    state = ledger.load_state("m")
    assert state is not None
    assert state["positions"] == [] and state["pending_orders"] == []


# --------------------------------------------------------------------------- #
# Stop mesafesi bandı (kural 14)
# --------------------------------------------------------------------------- #
# Her barın gerçek aralığı 2.0 ve boşluk yok -> ATR tam olarak 2.0; tavan 3×ATR = 6 birim.
FLAT = [(100.0, 101.0, 99.0, 100.0)] * 6


def _band_engine(strategy: Strategy, ledger: Ledger) -> Engine:
    return Engine(
        [strategy],
        config=_config(**{"trailing": {"atr_period": 3}, "max_stop_atr_multiple": 3.0}),
        ledger=ledger,
    )


def test_signal_inside_the_stop_band_is_kept(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path)
    as_of = _frame(FLAT).index[4]
    strategy = _Scripted("m", signals={as_of: [_long_signal(stop=95.0)]})  # 5 birim = 2.5×ATR
    engine = _band_engine(strategy, ledger)

    report = engine.run_round(_market(FLAT, bars=5))

    assert report.by_model("m").skipped_signals == 0  # type: ignore[union-attr]
    assert len(ledger.load_state("m")["pending_orders"]) == 1  # type: ignore[index]


def test_signal_wider_than_the_cap_is_skipped_and_logged(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Stop tavana ÇEKİLMEZ, işlem atlanır — çekmek modelin tezini başka bir modele çevirirdi."""
    ledger = Ledger(tmp_path)
    as_of = _frame(FLAT).index[4]
    strategy = _Scripted("m", signals={as_of: [_long_signal(stop=93.0)]})  # 7 birim = 3.5×ATR
    engine = _band_engine(strategy, ledger)

    with caplog.at_level(logging.INFO, logger="core.engine"):
        report = engine.run_round(_market(FLAT, bars=5))

    assert report.by_model("m").skipped_signals == 1  # type: ignore[union-attr]
    assert ledger.load_state("m")["pending_orders"] == []  # type: ignore[index]
    assert "stop mesafesi" in caplog.text and "3.50" in caplog.text  # atlama sessiz değil


def test_wide_stop_is_a_market_condition_not_a_programming_error(tmp_path: Path) -> None:
    """Kural 14 elemesi kural 8 ile karışmaz: model atlanmaz, yalnızca o sinyal düşer."""
    ledger = Ledger(tmp_path)
    as_of = _frame(FLAT).index[4]
    strategy = _Scripted(
        "m", signals={as_of: [_long_signal(stop=93.0), _long_signal(stop=96.0)]}
    )
    report = _band_engine(strategy, ledger).run_round(_market(FLAT, bars=5))

    assert report.by_model("m").skipped == ""  # type: ignore[union-attr]
    assert report.by_model("m").signals == 1  # type: ignore[union-attr]
    assert len(ledger.load_state("m")["pending_orders"]) == 1  # type: ignore[index]


def test_band_is_measured_against_the_short_side_too(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path)
    as_of = _frame(FLAT).index[4]
    wide = Signal(symbol=SYMBOL, direction="short", stop_price=107.0, reason="geniş")
    strategy = _Scripted("m", allowed_directions=("short",), signals={as_of: [wide]})
    report = _band_engine(strategy, ledger).run_round(_market(FLAT, bars=5))

    assert report.by_model("m").skipped_signals == 1  # type: ignore[union-attr]


def test_unverifiable_band_keeps_the_signal_and_warns(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """ATR yoksa tavan doğrulanamaz; sinyali elemek işlem sayısını ölçülemeyen nedenle düşürürdü."""
    ledger = Ledger(tmp_path)
    strategy = _Scripted("m", signals={START: [_long_signal(stop=95.0)]})
    engine = Engine(
        [strategy],
        config=_config(**{"trailing": {"atr_period": 50}, "max_stop_atr_multiple": 3.0}),
        ledger=ledger,
    )
    with caplog.at_level(logging.WARNING, logger="core.engine"):
        report = engine.run_round(_market(ROWS, bars=1))

    assert report.by_model("m").skipped_signals == 0  # type: ignore[union-attr]
    assert len(ledger.load_state("m")["pending_orders"]) == 1  # type: ignore[index]
    assert "ATR hesaplanamadı" in caplog.text

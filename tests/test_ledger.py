"""core/ledger.py: denetim izinin bozulmadan yazıldığını doğrular.

Defter sistemin tek kalıcı hafızası: yarım yazılmış bir dosya ya da üzerine yazılan bir
satır, sonraki koşuların yanlış bakiyeyle devam etmesi demektir.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from core.ledger import (
    EQUITY_COLUMNS,
    TRADE_COLUMNS,
    Ledger,
    LedgerError,
    new_state,
)


def _trade_row(**overrides: Any) -> dict[str, Any]:
    row: dict[str, Any] = {column: "" for column in TRADE_COLUMNS}
    row.update(
        strategy="m",
        symbol="BTC-USDT-SWAP",
        direction="long",
        opened_at="2026-01-01T00:00:00+00:00",
        closed_at="2026-01-01T04:00:00+00:00",
        entry_price=100.0,
        exit_price=105.0,
        qty=2.0,
        notional=200.0,
        leverage=1.0,
        margin=200.0,
        fee=0.4,
        funding=-0.02,
        pnl=9.58,
        exit_reason="tp",
        signal_reason="test",
        notes="",
    )
    row.update(overrides)
    return row


def _equity_row(ts: str) -> dict[str, Any]:
    return {
        "ts": ts,
        "cash": 10_000.0,
        "margin_used": 0.0,
        "unrealized_pnl": 0.0,
        "equity": 10_000.0,
        "open_positions": 0,
    }


def test_new_model_ledger_starts_empty(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path)
    state = ledger.initialize_model("alpha", initial_capital=10_000.0)

    assert state["cash"] == 10_000.0
    assert state["positions"] == [] and state["pending_orders"] == []
    assert state["last_processed_bar"] is None
    assert ledger.read_trades("alpha") == []
    assert ledger.read_equity("alpha") == []
    assert (tmp_path / "alpha" / "positions.json").is_file()
    assert (tmp_path / "alpha" / "trades.csv").read_text().strip() == ",".join(TRADE_COLUMNS)
    assert (tmp_path / "alpha" / "equity.csv").read_text().strip() == ",".join(EQUITY_COLUMNS)


def test_initialize_does_not_touch_an_existing_ledger(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path)
    ledger.initialize_model("alpha", initial_capital=10_000.0)
    ledger.append_trades("alpha", [_trade_row()])
    ledger.write_state("alpha", {**new_state("alpha", initial_capital=10_000.0), "cash": 9_500.0})

    state = ledger.initialize_model("alpha", initial_capital=10_000.0)
    assert state["cash"] == 9_500.0
    assert len(ledger.read_trades("alpha")) == 1


def test_reset_starts_the_model_over(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path)
    ledger.initialize_model("alpha", initial_capital=10_000.0)
    ledger.append_trades("alpha", [_trade_row()])

    state = ledger.reset_model("alpha", initial_capital=10_000.0)
    assert state["cash"] == 10_000.0
    assert ledger.read_trades("alpha") == []


def test_appends_never_rewrite_earlier_rows(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path)
    ledger.initialize_model("alpha", initial_capital=10_000.0)
    ledger.append_trades("alpha", [_trade_row(symbol="A-USDT-SWAP")])
    first_pass = (tmp_path / "alpha" / "trades.csv").read_text()

    ledger.append_trades("alpha", [_trade_row(symbol="B-USDT-SWAP"), _trade_row(symbol="C-USDT-SWAP")])
    text = (tmp_path / "alpha" / "trades.csv").read_text()

    assert text.startswith(first_pass)  # eski içerik bayt bayt korunur
    assert [row["symbol"] for row in ledger.read_trades("alpha")] == [
        "A-USDT-SWAP", "B-USDT-SWAP", "C-USDT-SWAP"
    ]


def test_equity_rows_append_in_order(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path)
    ledger.initialize_model("alpha", initial_capital=10_000.0)
    ledger.append_equity("alpha", [_equity_row("2026-01-01T00:00:00+00:00")])
    ledger.append_equity("alpha", [_equity_row("2026-01-01T04:00:00+00:00")])
    assert [row["ts"] for row in ledger.read_equity("alpha")] == [
        "2026-01-01T00:00:00+00:00", "2026-01-01T04:00:00+00:00"
    ]


def test_writes_leave_no_temporary_files_behind(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path)
    ledger.initialize_model("alpha", initial_capital=10_000.0)
    ledger.append_trades("alpha", [_trade_row()])
    ledger.write_state("alpha", new_state("alpha", initial_capital=10_000.0))
    assert [path.name for path in sorted((tmp_path / "alpha").iterdir())] == [
        "equity.csv", "positions.json", "trades.csv"
    ]


def test_state_round_trip_keeps_values(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path)
    state = ledger.initialize_model("alpha", initial_capital=10_000.0)
    state["cash"] = 8_123.45
    state["last_processed_bar"] = "2026-01-01T04:00:00+00:00"
    state["pending_orders"] = [{"kind": "open", "symbol": "BTC-USDT-SWAP"}]
    ledger.write_state("alpha", state)

    assert Ledger(tmp_path).load_state("alpha") == state


def test_models_keep_separate_ledgers(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path)
    ledger.initialize_model("alpha", initial_capital=10_000.0)
    ledger.initialize_model("beta", initial_capital=10_000.0)
    ledger.append_trades("alpha", [_trade_row(strategy="alpha")])

    assert len(ledger.read_trades("alpha")) == 1
    assert ledger.read_trades("beta") == []


def test_unknown_model_has_no_state(tmp_path: Path) -> None:
    assert Ledger(tmp_path).load_state("yok") is None


def test_unsafe_model_name_is_rejected(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path)
    for name in ("..", "a/b", ""):
        with pytest.raises(LedgerError, match="güvensiz model adı"):
            ledger.initialize_model(name, initial_capital=10_000.0)


def test_row_with_a_missing_column_is_rejected(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path)
    ledger.initialize_model("alpha", initial_capital=10_000.0)
    with pytest.raises(LedgerError, match="eksik kolon"):
        ledger.append_trades("alpha", [{"strategy": "alpha"}])


def test_schema_drift_is_refused_instead_of_silently_mixing(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path)
    ledger.initialize_model("alpha", initial_capital=10_000.0)
    (tmp_path / "alpha" / "trades.csv").write_text("eski,kolonlar\n", encoding="utf-8")
    with pytest.raises(LedgerError, match="başlığı beklenenden farklı"):
        ledger.append_trades("alpha", [_trade_row()])


def test_corrupt_state_stops_the_run_instead_of_guessing(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path)
    ledger.initialize_model("alpha", initial_capital=10_000.0)
    (tmp_path / "alpha" / "positions.json").write_text("{bozuk", encoding="utf-8")
    with pytest.raises(LedgerError, match="okunamadı"):
        ledger.load_state("alpha")


def test_appending_nothing_is_a_no_op(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path)
    ledger.initialize_model("alpha", initial_capital=10_000.0)
    before = (tmp_path / "alpha" / "trades.csv").read_text()
    ledger.append_trades("alpha", [])
    assert (tmp_path / "alpha" / "trades.csv").read_text() == before

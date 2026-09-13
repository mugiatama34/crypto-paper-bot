"""main.py'nin scalp katmanını uçtan uca koşması ve iki katmanın birbirine karışmaması.

Ölçülen şey veri çekme değil (borsa sahte), turun SIRASI: katman çözüldü mü, sabit evren
istendi mi, defter ve rapor katmanın kendi yoluna mı yazıldı, kırılımlar ve saklama
penceresi katmandan mı geldi.
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

import main as main_module
from core.ledger import Ledger

SYMBOLS = ("BTC-USDT-SWAP", "ETH-USDT-SWAP", "SOL-USDT-SWAP")
START = pd.Timestamp("2026-03-02 00:00:00", tz="UTC")


def _market(bars: int = 60) -> Any:
    from strategies.base import MarketData

    index = pd.date_range(START, periods=bars, freq="15min", tz="UTC", name="ts")
    frame = pd.DataFrame(
        {"open": 100.0, "high": 100.6, "low": 99.4, "close": 100.0, "volume": 100.0},
        index=index,
    )
    return MarketData(
        ohlcv={symbol: frame for symbol in SYMBOLS},
        btc=frame,
        funding={},
        as_of=index[-1],
    )


class Sandbox:
    def __init__(self, root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        self.root = root
        self.ledgers = root / "ledgers_scalp"
        self.metrics_path = root / "docs" / "data" / "metrics_scalp.json"
        self.requested: list[list[str] | None] = []
        real_resolve = main_module.resolve_layer

        def _resolve(config: Any, name: str = "base") -> Any:
            return replace(
                real_resolve(config, name),
                ledger_root=self.ledgers,
                metrics_path=self.metrics_path,
            )

        def _load(config: Any, symbols: Any = None) -> Any:
            self.requested.append(None if symbols is None else list(symbols))
            return _market()

        monkeypatch.setattr(main_module, "resolve_layer", _resolve)
        monkeypatch.setattr(main_module, "load_market_data", _load)

    def metrics(self) -> dict[str, Any]:
        return json.loads(self.metrics_path.read_text(encoding="utf-8"))


@pytest.fixture()
def sandbox(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Sandbox:
    return Sandbox(tmp_path, monkeypatch)


def test_scalp_layer_runs_both_models(sandbox: Sandbox) -> None:
    assert main_module.main(["--layer", "scalp"]) == 0

    payload = sandbox.metrics()
    assert payload["layer"] == "scalp"
    assert payload["settings"]["timeframe"] == "15m"
    assert [model["name"] for model in payload["models"]] == ["scalp_bandit", "scalp_fixed"]
    assert (sandbox.ledgers / "scalp_bandit" / "positions.json").is_file()


def test_scalp_layer_requests_the_fixed_universe(sandbox: Sandbox) -> None:
    """Sabit evren katmandan gelir: hacimden otomatik seçim scalp'te YOK."""
    main_module.main(["--layer", "scalp"])

    assert sandbox.requested and sandbox.requested[0] is not None
    assert len(sandbox.requested[0]) == 14
    assert "PENGU-USDT-SWAP" in sandbox.requested[0]


def test_base_layer_still_asks_for_the_computed_universe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """4 saatlik katman bu eklemeden etkilenmez: evreni yine hacimden hesaplanır."""
    box = Sandbox(tmp_path, monkeypatch)

    main_module.main([])

    assert box.requested == [None]


def test_scalp_payload_carries_the_layer_breakdowns(sandbox: Sandbox) -> None:
    """Kol ve sembol kırılımı katmanın rapor sözleşmesidir; sayfa onları buradan okur."""
    main_module.main(["--layer", "scalp"])

    breakdowns = sandbox.metrics()["breakdowns"]

    assert set(breakdowns) == {"arm", "symbol"}
    assert set(breakdowns["arm"]) == {"scalp_bandit", "scalp_fixed"}


def test_scalp_settings_report_the_layer_ceiling(sandbox: Sandbox) -> None:
    """Stop tavanı scalp'te 8×ATR'dir: yükün "hangi varsayımlarla ölçüldü" bölümü bunu söylemeli."""
    main_module.main(["--layer", "scalp"])

    assert sandbox.metrics()["settings"]["max_stop_atr_multiple"] == 8.0


def test_equity_compaction_runs_for_the_scalp_layer(sandbox: Sandbox) -> None:
    """30 günden eski özsermaye satırları günlük özete iner: depo geçmişi şişmesin."""
    ledger = Ledger(sandbox.ledgers)
    ledger.reset_model("scalp_bandit", initial_capital=10_000.0)
    old = [
        {
            "ts": (START - pd.Timedelta(days=60) + pd.Timedelta(minutes=15 * i)).isoformat(),
            "cash": 10_000,
            "margin_used": 0,
            "unrealized_pnl": 0,
            "equity": 10_000,
            "open_positions": 0,
        }
        for i in range(96)  # tek bir günün tüm barları
    ]
    ledger.append_equity("scalp_bandit", old)

    main_module.main(["--layer", "scalp"])

    stale = [
        row
        for row in ledger.read_equity("scalp_bandit")
        if row["ts"] < (START - pd.Timedelta(days=30)).isoformat()
    ]
    assert len(stale) == 1  # 96 bar -> 1 günlük özet


def test_base_layer_never_compacts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """4 saatlik katmanda sıkıştırma kapalıdır (`null`): eğri tam çözünürlükte kalır."""
    box = Sandbox(tmp_path, monkeypatch)
    ledger = Ledger(box.ledgers)
    ledger.reset_model("buyhold", initial_capital=10_000.0)
    rows = [
        {
            "ts": (START - pd.Timedelta(days=200) + pd.Timedelta(hours=4 * i)).isoformat(),
            "cash": 10_000,
            "margin_used": 0,
            "unrealized_pnl": 0,
            "equity": 10_000,
            "open_positions": 0,
        }
        for i in range(6)
    ]
    ledger.append_equity("buyhold", rows)

    main_module.main([])

    assert len([row for row in ledger.read_equity("buyhold") if row["ts"] < START.isoformat()]) == 6


def test_dry_run_writes_nothing_in_the_scalp_layer(sandbox: Sandbox) -> None:
    assert main_module.main(["--layer", "scalp", "--dry-run"]) == 0

    assert not sandbox.metrics_path.exists()
    assert not sandbox.ledgers.exists() or list(sandbox.ledgers.iterdir()) == []


def test_unknown_layer_fails_loudly(sandbox: Sandbox) -> None:
    """Sessizce base'e düşmek, scalp turunun 4 saatlik defteri ezmesi demekti."""
    from core.config import ConfigError

    with pytest.raises(ConfigError):
        main_module.main(["--layer", "yok"])

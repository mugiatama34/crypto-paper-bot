"""İleriye bakış (kural 12) — pencere taşmasından AYRI bir soru, ayrı bir test.

docs/decisions.md > 56'nın denetimi iki şeyi ayırır: (a) pencere ön-kayıttakinden UZUN
koştu mu (veri katmanı, `tests/test_data.py` ve `tests/test_backtest.py`), (b) model
işlediği bir barda o barın ÖTESİNİ gördü mü. (b) "motor çerçeveyi bara kadar kesiyor"
cümlesine dayanıyordu; bu dosya o cümleyi bir iddia olmaktan çıkarıp ölçer.

İki ölçü:

1. **Yoklama.** Gerçek modellerin `generate_signals`/`manage_positions` çağrıları sarılır ve
   her çağrıda modelin eline verilen HER serinin (mumlar, BTC çıpası, funding) son damgası
   kaydedilir. Hiçbiri o çağrının `as_of`'unu aşamaz — anlık görüntü `as_of`'un çok ötesini
   taşırken bile.
2. **Önek değişmezliği.** Aynı pencere iki kez koşulur: veri T'de bitiyor ↔ veri T'den
   300 bar sonrasına uzanıyor. T'ye kadar üretilen her sinyal, T'ye kadar kapanan her
   işlem ve T'ye kadarki her özsermaye satırı BİREBİR aynı olmalıdır. Yoklama modelin
   ne GÖRDÜĞÜNÜ, bu test görülenin sonuca SIZMADIĞINI ölçer (stop tavanının ATR'si,
   trailing, funding tahakkuku dâhil — hepsi motorun kendi yolundan geçer).

Modeller taşması ölçülen koşuların modelleridir: `dc_short`/`dc_coinflip` (dc),
`ema_trend` (ema), `xsec_mom`/`xsec_random` (xsec) ve `buyhold`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pytest

from core.config import load_config
from core.engine import Engine
from core.layers import resolve_layer
from core.ledger import Ledger
from core.portfolio import Portfolio
from strategies.base import MarketData, Strategy
from strategies.registry import build

BARS = 1400
INDEX = pd.date_range("2022-01-03", periods=BARS, freq="4h", tz="UTC", name="ts")
START = INDEX[500]   # tohumlanan bar (işlenmez)
T = INDEX[1100]      # kısa koşunun `as_of`'u
SYMBOLS = ["BTC-USDT-SWAP", "ETH-USDT-SWAP", "SOL-USDT-SWAP", "XRP-USDT-SWAP", "ADA-USDT-SWAP"]

LAYERS: dict[str, dict[str, Any]] = {
    # dc: ölçek küçültülür (tanım periyottan bağımsızdır — tests/test_dc_signal.py'nin
    # gerekçesi); canlı 50/200/3000 bu uzunlukta kesişim üretmez.
    "dc": {
        "models": ["dc_short", "dc_coinflip", "buyhold"],
        "overrides": {"dc": {"fast_period": 5, "slow_period": 20, "warmup_bars": 40, "lookback_bars": 400}},
    },
    "ema": {"models": ["ema_trend", "buyhold"], "overrides": {}},
    "xsec": {"models": ["xsec_mom", "xsec_random", "buyhold"], "overrides": {}},
}


def _frames() -> dict[str, pd.DataFrame]:
    frames = {}
    t = np.arange(BARS)
    for k, symbol in enumerate(SYMBOLS):
        rng = np.random.default_rng(100 + k)
        close = np.maximum(
            200 + 50 * np.sin(2 * np.pi * t / (260 + 40 * k) + k) + np.cumsum(rng.normal(0, 1.0, BARS)),
            20,
        )
        opn = np.r_[close[0], close[:-1]] + rng.normal(0, 0.4, BARS)
        high = np.maximum(opn, close) + np.abs(rng.normal(0, 1.5, BARS))
        low = np.minimum(opn, close) - np.abs(rng.normal(0, 1.5, BARS))
        frames[symbol] = pd.DataFrame(
            {"open": opn, "high": high, "low": low, "close": close, "volume": 1.0}, index=INDEX
        )
    return frames


def _funding() -> dict[str, pd.Series]:
    stamps = pd.date_range(INDEX[0], INDEX[-1] + pd.Timedelta("4h"), freq="8h", tz="UTC", name="ts")
    return {s: pd.Series(0.0001 * (1 + k), index=stamps) for k, s in enumerate(SYMBOLS)}


def _market(end: pd.Timestamp) -> MarketData:
    """`end`e kadar veri taşıyan anlık görüntü; `as_of` = `end`."""
    frames, funding = _frames(), _funding()
    return MarketData(
        ohlcv={s: f.loc[:end] for s, f in frames.items()},
        btc=frames["BTC-USDT-SWAP"].loc[:end],
        funding={s: series.loc[:end] for s, series in funding.items()},
        as_of=end,
    )


def _config(layer: str) -> dict[str, Any]:
    config = dict(resolve_layer(load_config(), layer).config)
    for key, value in LAYERS[layer]["overrides"].items():
        config[key] = {**config[key], **value}
    config["signals_per_bar"] = True  # harness'ın koşulu (docs/backtest.md > 5a)
    return config


class _Probe:
    """Modelin eline verilen her serinin son damgasını kaydeder."""

    def __init__(self) -> None:
        self.violations: list[str] = []
        self.calls = 0

    def check(self, model: str, hook: str, market: MarketData) -> None:
        self.calls += 1
        seen = {"btc": market.btc.index.max()}
        seen.update({f"ohlcv:{s}": f.index.max() for s, f in market.ohlcv.items()})
        seen.update({f"funding:{s}": f.index.max() for s, f in market.funding.items() if len(f)})
        for name, last in seen.items():
            if last > market.as_of:
                self.violations.append(f"{model}.{hook} as_of={market.as_of} {name} son={last}")


def _wrap(strategy: Strategy, probe: _Probe) -> Strategy:
    # Gölgeleme ÖRNEK düzeyindedir (scripts/backtest.py::_silence_signals_after'ın deseni):
    # modelin sınıfı ve davranışı değişmez, yalnızca girişi gözlenir.
    generate, manage = strategy.generate_signals, strategy.manage_positions

    def generate_signals(market: MarketData, peer_signals: Any = None) -> Any:
        probe.check(strategy.name, "generate_signals", market)
        return generate(market, peer_signals)

    def manage_positions(market: MarketData, positions: Any) -> Any:
        probe.check(strategy.name, "manage_positions", market)
        return manage(market, positions)

    strategy.generate_signals = generate_signals  # type: ignore[method-assign]
    strategy.manage_positions = manage_positions  # type: ignore[method-assign]
    return strategy


def _run(layer: str, end: pd.Timestamp, root: Path, probe: _Probe) -> tuple[Ledger, Any]:
    config = _config(layer)
    strategies = [_wrap(build(name, config=config), probe) for name in LAYERS[layer]["models"]]
    ledger = Ledger(root)
    for strategy in strategies:
        ledger.initialize_model(strategy.name, initial_capital=float(config["initial_capital"]))
        state = ledger.load_state(strategy.name)
        assert state is not None
        state["last_processed_bar"] = START.isoformat()
        ledger.write_state(strategy.name, state)
    report = Engine(strategies, config=config, ledger=ledger, portfolio=Portfolio(config)).run_round(
        _market(end)
    )
    return ledger, report


def _emitted_until(report: Any, cutoff: pd.Timestamp) -> dict[str, list[Any]]:
    return {m.model: [e for e in m.emitted if pd.Timestamp(e.bar) <= cutoff] for m in report.models}


@pytest.mark.parametrize("layer", sorted(LAYERS))
def test_models_never_see_past_the_bar_they_are_called_on(layer: str, tmp_path: Path) -> None:
    probe = _Probe()
    _run(layer, INDEX[-1], tmp_path / "long", probe)

    assert probe.calls > 0
    assert probe.violations == []


@pytest.mark.parametrize("layer", sorted(LAYERS))
def test_future_bars_in_the_snapshot_do_not_change_anything_up_to_t(
    layer: str, tmp_path: Path
) -> None:
    short_ledger, short = _run(layer, T, tmp_path / "short", _Probe())
    long_ledger, long = _run(layer, INDEX[-1], tmp_path / "long", _Probe())

    short_emitted, long_emitted = _emitted_until(short, T), _emitted_until(long, T)
    # Test boş geçmesin: her katmanda T'ye kadar en az bir yarışmacı sinyali üretilmiş olmalı.
    competitors = [m for m in LAYERS[layer]["models"] if m != "buyhold"]
    assert any(short_emitted[m] for m in competitors), f"{layer}: T'ye kadar sinyal yok"
    assert short_emitted == long_emitted

    for model in LAYERS[layer]["models"]:
        closed_by_t = lambda rows: [r for r in rows if pd.Timestamp(r["closed_at"]) <= T]  # noqa: E731
        assert closed_by_t(short_ledger.read_trades(model)) == closed_by_t(long_ledger.read_trades(model))
        until_t = lambda rows: [r for r in rows if pd.Timestamp(r["ts"]) <= T]  # noqa: E731
        short_equity = until_t(short_ledger.read_equity(model))
        assert short_equity and short_equity == until_t(long_ledger.read_equity(model))

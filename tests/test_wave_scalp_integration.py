"""Model 21, GERÇEK motorla uçtan uca: sinyal → dolum → yönetim → defter.

Birim testleri (`test_wave_scalp.py`) modelin ürettiği `Signal`i ölçer; bu dosya o
sinyalin motorun ve portföyün elinden geçerken ne olduğunu ölçer. Ağ YOKTUR: anlık
görüntü sentetiktir, defter `tmp_path` altındadır.

Neden ayrı bir dosya: ölçülen şey modelin mantığı değil **bağlanış noktalarıdır** ve
onların hepsi `core/` tarafındadır — kural 13'ün dolumu, kural 13b'nin üç aşamalı
yönetimi, kural 14'ün stop tavanı, kural 15b'nin boyutlandırma kapısı ve kural 16'nın
öğrenme kancası. Bir tanesi kopsa birim testleri yeşil kalırdı.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Sequence

import pandas as pd
import pytest

from core.config import load_config
from core.engine import Engine
from core.layers import resolve_layer
from core.ledger import Ledger
from core.portfolio import Portfolio
from core.tags import find_tag
from strategies.registry import build
from strategies.wave import clone_signal
from tests.test_wave_clone_signal import buy_path, wave_frame

SYMBOL = "BTC-USDT-SWAP"


def _config(**overrides: Any) -> dict[str, Any]:
    config = resolve_layer(load_config(), "scalp").config
    config.update(overrides)
    return config


def _market(frame: pd.DataFrame, *, symbols: Sequence[str] = (SYMBOL,)):
    from strategies.base import MarketData

    return MarketData(
        ohlcv={symbol: frame for symbol in symbols},
        btc=frame,
        funding={},
        as_of=frame.index[-1],
    )


def _run(frame: pd.DataFrame, tmp_path: Path, **overrides: Any):
    config = _config(**overrides)
    model = build("wave_scalp", config=config)
    ledger = Ledger(tmp_path / "ledger")
    engine = Engine([model], config=config, ledger=ledger, portfolio=Portfolio(config))
    report = engine.run_round(_market(frame))
    return model, ledger, report


def _extend(frame: pd.DataFrame, closes: Sequence[float], *, wick: float = 0.004):
    """Çerçeveyi verilen kapanışlarla BİR BAR uzatır (dolum bir sonraki barda, kural 13)."""
    values = list(frame["close"]) + [float(c) for c in closes]
    return wave_frame(values, wick=wick)


# --------------------------------------------------------------------------- #
# Dolum: kural 13
# --------------------------------------------------------------------------- #
def test_signal_becomes_a_pending_order_and_fills_on_the_next_bar(tmp_path: Path) -> None:
    """Sinyal barında pozisyon AÇILMAZ; emir bir sonraki barın açılışından dolar."""
    frame = wave_frame(buy_path())
    model, ledger, report = _run(frame, tmp_path)
    first = report.by_model("wave_scalp")
    assert first is not None
    assert first.signals == 1
    assert first.filled == 0, "kural 13: dolum sinyal barında OLMAZ"

    state = ledger.load_state("wave_scalp")
    assert len(state["pending_orders"]) == 1
    assert state["pending_orders"][0]["kind"] == "open"
    assert state["positions"] == []

    # İkinci tur: bir bar ileri — emir dolmalı.
    later = _extend(frame, [float(frame["close"].iloc[-1]) * 1.001])
    config = _config()
    engine = Engine(
        [build("wave_scalp", config=config)],
        config=config,
        ledger=ledger,
        portfolio=Portfolio(config),
    )
    second = engine.run_round(_market(later)).by_model("wave_scalp")
    assert second is not None and second.filled == 1

    state = ledger.load_state("wave_scalp")
    assert len(state["positions"]) == 1
    position = state["positions"][0]
    assert position["symbol"] == SYMBOL
    assert position["direction"] == "long"
    # Dolum fiyatı DOLUM barının açılışıdır, sinyal barının kapanışı değil.
    assert position["entry_price"] == pytest.approx(
        float(later["open"].iloc[-1]) * (1 + config["slippage_base"])
    )


def test_house_sizing_and_leverage_cap_are_applied_by_the_portfolio(tmp_path: Path) -> None:
    """Boyut `risk / |giriş − stop|`dir ve kaldıraç `leverage_cap`i AŞMAZ (kural 11).

    Kaynağın sabit teminatı (500 × 10x = 5.000 notional) kopyalanmadığı için pozisyonun
    büyüklüğü tamamen evin formülünden gelir (§6h > 3f).
    """
    frame = wave_frame(buy_path())
    _, ledger, _ = _run(frame, tmp_path)
    later = _extend(frame, [float(frame["close"].iloc[-1]) * 1.001])
    config = _config()
    Engine(
        [build("wave_scalp", config=config)],
        config=config, ledger=ledger, portfolio=Portfolio(config),
    ).run_round(_market(later))

    position = ledger.load_state("wave_scalp")["positions"][0]
    entry, stop = position["entry_price"], position["stop_price"]
    expected_qty = (config["risk_per_trade"] * config["initial_capital"]) / abs(entry - stop)
    assert position["qty"] == pytest.approx(expected_qty, rel=1e-6)
    assert position["leverage"] <= config["leverage_cap"] + 1e-9


def test_stop_band_ceiling_can_skip_a_signal_and_the_skip_is_counted(tmp_path: Path) -> None:
    """Kural 14: tavanı aşan sinyal ELENİR, stop tavana ÇEKİLMEZ ve eleme SAYILIR.

    §6h > 3(i) bu sayının raporlanmasını zorunlu kılıyor; burada sayacın gerçekten
    dolduğu çivilenir. Tavan bilerek 0.01'e indirilir — sinyalin kendisi değişmez, yani
    ölçülen şey tam olarak motorun kapısıdır.
    """
    frame = wave_frame(buy_path())
    _, _, loose = _run(frame, tmp_path / "loose")
    _, _, tight = _run(frame, tmp_path / "tight", max_stop_atr_multiple=0.01)

    assert loose.by_model("wave_scalp").signals == 1
    assert loose.by_model("wave_scalp").skipped_signals == 0
    assert tight.by_model("wave_scalp").signals == 0
    assert tight.by_model("wave_scalp").skipped_signals == 1


# --------------------------------------------------------------------------- #
# Defter ve denetim izi
# --------------------------------------------------------------------------- #
def test_closed_trade_carries_every_preregistered_tag(tmp_path: Path) -> None:
    """Defter satırı `arm`, `combo`, `retrace`, `wave1` ve `target` taşımalı.

    `target` olmadan fiili R:R (§6h > 10.5) defterden HİÇ okunamaz — `trades.csv`de bir
    TP kolonu yoktur ve yeni kolon açılamaz (kural 13c).
    """
    frame = wave_frame(buy_path())
    _, ledger, _ = _run(frame, tmp_path)
    config = _config()

    # Stop'a düşen bir bar: pozisyon açılır ve aynı turda kapanır.
    stop_price = None
    later = _extend(frame, [float(frame["close"].iloc[-1]) * 1.001])
    Engine(
        [build("wave_scalp", config=config)],
        config=config, ledger=ledger, portfolio=Portfolio(config),
    ).run_round(_market(later))
    stop_price = ledger.load_state("wave_scalp")["positions"][0]["stop_price"]

    crash = _extend(later, [stop_price * 0.9], wick=0.004)
    Engine(
        [build("wave_scalp", config=config)],
        config=config, ledger=ledger, portfolio=Portfolio(config),
    ).run_round(_market(crash))

    rows = ledger.read_trades("wave_scalp")
    assert rows, "pozisyon stop'a düştü ama deftere satır yazılmadı"
    reason = str(rows[-1]["signal_reason"])
    assert find_tag(reason, "arm") == clone_signal.ARM_NAME
    assert find_tag(reason, "combo", ) is not None
    assert find_tag(reason, "retrace") is not None
    assert find_tag(reason, "wave1") is not None
    assert find_tag(reason, "target") is not None
    assert float(rows[-1]["risk_amount"]) > 0.0, "risk_amount olmadan R hesaplanamaz"


def test_learning_hook_reads_the_models_own_closed_trades(tmp_path: Path) -> None:
    """Kural 16: motor kancayı besler ve posterior defterden kurulur."""
    frame = wave_frame(buy_path())
    _, ledger, _ = _run(frame, tmp_path)
    config = _config()
    later = _extend(frame, [float(frame["close"].iloc[-1]) * 1.001])
    Engine(
        [build("wave_scalp", config=config)],
        config=config, ledger=ledger, portfolio=Portfolio(config),
    ).run_round(_market(later))
    stop_price = ledger.load_state("wave_scalp")["positions"][0]["stop_price"]
    crash = _extend(later, [stop_price * 0.9])
    Engine(
        [build("wave_scalp", config=config)],
        config=config, ledger=ledger, portfolio=Portfolio(config),
    ).run_round(_market(crash))

    # Yeni bir tur: kanca artık dolu bir geçmiş görmeli.
    model = build("wave_scalp", config=config)
    engine = Engine([model], config=config, ledger=ledger, portfolio=Portfolio(config))
    engine.run_round(_market(_extend(crash, [float(crash["close"].iloc[-1])])))
    measured = [key for key, stats in model._global.items() if stats.measured]
    assert measured, "kapanmış işlem posteriora hiç girmedi (kural 16 kancası kopmuş)"


def test_exit_management_fields_reach_the_open_position(tmp_path: Path) -> None:
    """Üç aşamalı yönetim motorun yeteneğidir (kural 13b); pozisyon onu TAŞIMALI."""
    frame = wave_frame(buy_path())
    _, ledger, _ = _run(frame, tmp_path)
    config = _config()
    later = _extend(frame, [float(frame["close"].iloc[-1]) * 1.001])
    Engine(
        [build("wave_scalp", config=config)],
        config=config, ledger=ledger, portfolio=Portfolio(config),
    ).run_round(_market(later))

    position = ledger.load_state("wave_scalp")["positions"][0]
    assert position["breakeven_at_r"] == pytest.approx(1.0)
    assert position["partial_tp"]["r"] == pytest.approx(1.5)
    assert position["partial_tp"]["fraction"] == pytest.approx(0.5)
    assert position["trail_giveback_pct"] == pytest.approx(0.5)
    assert position.get("trailing_atr") is None


def test_no_time_stop_closes_the_position(tmp_path: Path) -> None:
    """Zaman stop'u YOKTUR (§6h > 3h): pozisyon 20+ bar sonra da açık kalmalı.

    Scalp katmanının 16 barlık zaman stop'u bu modele sızarsa burası kırmızıya düşer.
    """
    frame = wave_frame(buy_path())
    _, ledger, _ = _run(frame, tmp_path)
    config = _config()
    later = _extend(frame, [float(frame["close"].iloc[-1]) * 1.001])
    Engine(
        [build("wave_scalp", config=config)],
        config=config, ledger=ledger, portfolio=Portfolio(config),
    ).run_round(_market(later))
    entry = ledger.load_state("wave_scalp")["positions"][0]["entry_price"]

    # 25 bar boyunca ne stop'a ne hedefe değmeyen, dar bantlı bir seyir.
    drifting = _extend(later, [entry * (1.0 + 0.0001 * ((i % 3) - 1)) for i in range(25)],
                       wick=0.0001)
    Engine(
        [build("wave_scalp", config=config)],
        config=config, ledger=ledger, portfolio=Portfolio(config),
    ).run_round(_market(drifting))

    state = ledger.load_state("wave_scalp")
    exits = [r for r in ledger.read_trades("wave_scalp")
             if "time_stop" in str(r.get("notes", ""))]
    assert not exits, "zaman stop'u tetiklemiş — bu modelde OLMAMALI"
    assert len(state["positions"]) == 1, "pozisyon 25 bar sonra hâlâ açık olmalı"

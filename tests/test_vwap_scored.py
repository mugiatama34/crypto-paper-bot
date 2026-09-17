"""F1 (`vwap_scored`): skorla boyut, maker giriş, ilerleme koşullu zaman stop'u, likidite.

F1 bir DEMETTİR ve ön-kayıt bunu böyle yazdı: hangi kalemin işe yaradığı koşudan
okunamaz. Bu dosyanın işi de o yüzden performans değil, **her kalemin gerçekten
uygulandığını** çivilemektir — karar 45'in dersi tam olarak sessizce açık kalan (ya da
sessizce kapanan) kapılardı.
"""

from __future__ import annotations

import copy
from typing import Any

import pandas as pd
import pytest

from core.config import load_config
from core.layers import resolve_layer
from core.tags import find_tag
from strategies.base import Position, Strategy
from strategies.vwap_scored import (
    NO_LIMIT,
    RANK_CUT,
    THIN_BOOK,
    TIME_STOP,
    TRENDING,
    VwapScored,
)
from tests.helpers_market import frame, market

START = pd.Timestamp("2026-01-01 00:00:00", tz="UTC")
BARS = 380
SYMBOL = "SOL-USDT-SWAP"
LIQUID = 3000.0   # 100 × 3000 = 300k quote hacim > 250k eşiği


@pytest.fixture()
def config() -> dict[str, Any]:
    return resolve_layer(load_config(), "scalp").config


def _reverting(
    *, drop: float = 3.0, volume: float = LIQUID, last_volume: float | None = None,
    level: float = 100.0, slope: float = 0.0,
) -> pd.DataFrame:
    closes = [level + slope * index + (index % 4) * 0.05 for index in range(BARS)]
    closes[-2] = closes[-2] - drop
    closes[-1] = closes[-2] + 0.3
    volumes = [volume] * BARS
    volumes[-1] = volume if last_volume is None else last_volume
    return frame(closes, spread=0.2, volumes=volumes, freq="15min", start=START)


def _market(frames: dict[str, pd.DataFrame] | None = None):
    payload = dict(frames or {SYMBOL: _reverting()})
    btc = frame(
        [50000.0 + (index % 4) * 0.05 for index in range(BARS)],
        spread=5.0, volumes=[LIQUID] * BARS, freq="15min", start=START,
    )
    return market(payload, btc=btc)


def _position(symbol: str, direction: str, *, opened_at: pd.Timestamp,
              entry: float = 100.0, stop: float = 99.0) -> Position:
    return Position(
        symbol=symbol, direction=direction,  # type: ignore[arg-type]
        entry_price=entry, stop_price=stop, opened_at=opened_at,
    )


# --------------------------------------------------------------------------- #
# Giriş: post-only maker
# --------------------------------------------------------------------------- #
def test_the_order_is_a_post_only_limit_at_the_signal_bars_extreme(
    config: dict[str, Any]
) -> None:
    """Long emri o barın en DÜŞÜĞÜNE konur; piyasa emrine düşmek seçenek değildir."""
    bars = _reverting()
    signals = VwapScored(config=config).generate_signals(_market({SYMBOL: bars}))

    assert len(signals) == 1
    signal = signals[0]
    assert signal.entry_type == "limit"
    assert signal.limit_price == pytest.approx(float(bars["low"].iloc[-1]))
    assert signal.limit_price < bars["close"].iloc[-1]   # post-only: LEHTE tarafta


def test_an_unreachable_limit_drops_the_setup(config: dict[str, Any]) -> None:
    """Uç, kapanışın lehte tarafında değilse emir hiç kurulamaz ve bu SAYILIR."""
    model = VwapScored(config=config)
    bars = _reverting()
    # Son barın en düşüğünü kapanışa çek: artık post-only bir long emri kurulamaz.
    bars.loc[bars.index[-1], "low"] = bars["close"].iloc[-1]

    signals = model.generate_signals(_market({SYMBOL: bars}))

    assert signals == []
    assert model.take_survey()[NO_LIMIT] == 1


def test_the_model_is_a_full_competitor(config: dict[str, Any]) -> None:
    """Sabit teminat bırakıldı: boyut kural 11'in formülünden, kaldıraç katmanın tavanından."""
    model = VwapScored(config=config)

    signal = model.generate_signals(_market())[0]

    assert model.is_replica is False and model.limits is None
    assert signal.sizing == "risk"
    assert signal.notional_fraction is None


# --------------------------------------------------------------------------- #
# Skor ve boyut kademesi
# --------------------------------------------------------------------------- #
def test_a_calm_regime_gets_full_size(config: dict[str, Any]) -> None:
    signal = VwapScored(config=config).generate_signals(_market())[0]

    assert signal.size_scale == pytest.approx(float(config["vwap"]["scored"]["size"]["full"]))
    assert float(find_tag(signal.reason, "adx")) < 22.0


def test_a_strong_trend_is_not_faded(config: dict[str, Any]) -> None:
    """ADX tavanının üstünde fade YOK.

    Kapı EŞİK oynatılarak ölçülür, sentetik bir trend çerçevesiyle değil: 15 dakikalık
    bir seride "hem geçerli sapma hem güçlü trend" kurmak σ'yı da büyütür ve kurulumun
    kendisini yok eder — yani test kapıyı değil fixture'ı ölçerdi (karar 45'in aynı
    dersi).
    """
    strict = copy.deepcopy(config)
    strict["vwap"]["scored"]["regime"]["adx_full"] = 0.0
    strict["vwap"]["scored"]["regime"]["adx_max"] = 0.0
    model = VwapScored(config=strict)

    signals = model.generate_signals(_market())

    assert signals == []
    assert model.take_survey()[TRENDING] == 1


def test_only_the_best_scoring_setups_are_played(config: dict[str, Any]) -> None:
    """Evrenden en yüksek skorlu 1-2 kurulum; kalanlar sıralamada kalır ve sayılır."""
    model = VwapScored(config=config)
    frames = {
        f"{name}-USDT-SWAP": _reverting(drop=drop)
        for name, drop in (("AAA", 3.0), ("BBB", 4.0), ("CCC", 5.0), ("DDD", 6.0))
    }

    signals = model.generate_signals(_market(frames))
    survey = model.take_survey()

    assert len(signals) == int(config["vwap"]["scored"]["max_new_per_bar"]) == 2
    assert survey[RANK_CUT] == 2
    scores = [float(find_tag(signal.reason, "score")) for signal in signals]
    assert scores == sorted(scores, reverse=True)


def test_volume_above_the_baseline_lowers_the_score_but_does_not_ban(
    config: dict[str, Any]
) -> None:
    """Hacim tükenmesi bir ELEME değil, skorda geriye düşmedir."""
    model = VwapScored(config=config)
    frames = {
        "AAA-USDT-SWAP": _reverting(last_volume=LIQUID * 3),   # hacim patlaması
        "BBB-USDT-SWAP": _reverting(last_volume=LIQUID * 0.2),  # tükenme
    }

    signals = model.generate_signals(_market(frames))

    assert [signal.symbol for signal in signals][0] == "BBB-USDT-SWAP"
    assert len(signals) == 2  # patlayan da oynanır, ama SONRA


# --------------------------------------------------------------------------- #
# Likidite ve korelasyon
# --------------------------------------------------------------------------- #
def test_a_thin_book_symbol_is_never_scanned(config: dict[str, Any]) -> None:
    """Eşik P&L'den değil büyüklükten gelir; hangi sembolün eleneceği önceden bilinmez."""
    model = VwapScored(config=config)
    frames = {SYMBOL: _reverting(volume=1.0)}   # 100 × 1 = 100 quote << 250k

    assert model.generate_signals(_market(frames)) == []
    assert model.take_survey()[THIN_BOOK] == 1


def test_a_second_position_in_the_same_direction_is_halved(config: dict[str, Any]) -> None:
    """Korelasyon kotası bir YASAK değil, yarım boydur."""
    model = VwapScored(config=config)
    snapshot = _market()
    previous = snapshot.as_of - pd.Timedelta("15min")
    frames = {symbol: value.loc[:previous] for symbol, value in snapshot.ohlcv.items()}
    model.manage_positions(
        market(frames, btc=snapshot.btc.loc[:previous], as_of=previous),
        [_position("ETH-USDT-SWAP", "long", opened_at=previous)],
    )

    signal = model.generate_signals(snapshot)[0]

    full = float(config["vwap"]["scored"]["size"]["full"])
    assert signal.size_scale == pytest.approx(full * 0.5)


def test_a_stale_position_snapshot_is_not_carried_forward(config: dict[str, Any]) -> None:
    model = VwapScored(config=config)
    snapshot = _market()
    old = snapshot.as_of - pd.Timedelta("2h")
    frames = {symbol: value.loc[:old] for symbol, value in snapshot.ohlcv.items()}
    model.manage_positions(
        market(frames, btc=snapshot.btc.loc[:old], as_of=old),
        [_position("ETH-USDT-SWAP", "long", opened_at=old)],
    )

    signal = model.generate_signals(snapshot)[0]

    assert signal.size_scale == pytest.approx(float(config["vwap"]["scored"]["size"]["full"]))


# --------------------------------------------------------------------------- #
# İlerleme koşullu zaman stop'u
# --------------------------------------------------------------------------- #
def test_a_stalled_position_is_closed_after_the_deadline(config: dict[str, Any]) -> None:
    model = VwapScored(config=config)
    snapshot = _market()
    bars = int(config["vwap"]["scored"]["time_stop"]["bars"])
    opened = snapshot.as_of - pd.Timedelta(minutes=15 * bars)
    # Giriş, pencerede görülen EN YÜKSEK fiyata konur: lehte ilerleme tam sıfırdır,
    # yani kurulum hiç çalışmamıştır.
    best = float(snapshot.ohlcv[SYMBOL].loc[opened:, "high"].max())
    instructions = model.manage_positions(
        snapshot, [_position(SYMBOL, "long", opened_at=opened, entry=best, stop=best - 1.0)]
    )

    assert len(instructions) == 1
    assert find_tag(instructions[0].reason, "exit_rule") == "time_stop"
    assert model.take_survey()[TIME_STOP] == 1


def test_a_working_position_is_left_alone(config: dict[str, Any]) -> None:
    """Fade çalışmışsa süre dolsa da kapatılmaz: ölçüt SÜRE değil, İLERLEMEDİR."""
    model = VwapScored(config=config)
    snapshot = _market()
    bars = int(config["vwap"]["scored"]["time_stop"]["bars"])
    opened = snapshot.as_of - pd.Timedelta(minutes=15 * bars)
    # Aynı pencere, ama giriş daha aşağıda ve risk küçük: lehte ilerleme 0.3R'yi aşar.
    best = float(snapshot.ohlcv[SYMBOL].loc[opened:, "high"].max())
    instructions = model.manage_positions(
        snapshot,
        [_position(SYMBOL, "long", opened_at=opened, entry=best - 1.0, stop=best - 2.0)],
    )

    assert instructions == []


def test_a_young_position_is_not_measured(config: dict[str, Any]) -> None:
    model = VwapScored(config=config)
    snapshot = _market()
    bars = int(config["vwap"]["scored"]["time_stop"]["bars"]) - 1
    opened = snapshot.as_of - pd.Timedelta(minutes=15 * bars)

    assert model.manage_positions(snapshot, [_position(SYMBOL, "long", opened_at=opened)]) == []


def test_progress_is_read_from_the_extreme_not_the_close(config: dict[str, Any]) -> None:
    """Hedefe doğru 0.5R yol alıp dönen bir kurulum "hiç çalışmadı" sayılmaz."""
    model = VwapScored(config=config)
    snapshot = _market()
    bars = int(config["vwap"]["scored"]["time_stop"]["bars"])
    opened = snapshot.as_of - pd.Timedelta(minutes=15 * bars)
    window = snapshot.ohlcv[SYMBOL].loc[opened:]
    best = float(window["high"].max())
    entry = 100.0
    stop = entry - (best - entry) / 0.4   # ilerleme tam 0.4R -> eşiğin üstünde

    assert model.manage_positions(
        snapshot, [_position(SYMBOL, "long", opened_at=opened, entry=entry, stop=stop)]
    ) == []


def test_the_model_does_not_learn() -> None:
    """F1 bir demet ölçer; öğrenme o demetin parçası DEĞİLDİR."""
    assert VwapScored.observe_closed_trades is Strategy.observe_closed_trades

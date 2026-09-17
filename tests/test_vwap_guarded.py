"""MODEL 18 — kopyanın canlıya hazırlanmış uyarlaması: kapılar, kesiciler ve kota.

Bu dosyanın ölçtüğü şey modelin KÂRLILIĞI değil, **kurallarının gerçekten uygulandığıdır.**
Model 13'ün (kopya) ölçtüğü soru dışarıdan gelen bir sistemin davranışıydı; buradaki
model o davranışın canlı bir hesapta eksik kalan parçalarını (rejim kapısı, tükenme şartı,
risk boyutlandırma, zaman stop'u, risk kesicileri, korelasyon kotası) ekler ve her biri
sessizce açık kalabilecek bir kapıdır. Sessiz kalan kapıyı yakalayacak tek şey testtir.

Kopyanın kendisine DOKUNULMADIĞI da burada sabitlenir (kural 15b): `vwap_clone` ayrı bir
modeldir ve bu modelin varlığı onun kurallarını değiştirmez.
"""

from __future__ import annotations

import copy
from typing import Any, Sequence

import pandas as pd
import pytest

from core.config import load_config
from core.layers import resolve_layer
from core.tags import find_tag
from strategies.base import ClosedTrade, Position
from strategies.vwap import guarded_signal
from strategies.vwap_clone import VwapClone
from strategies.vwap_guarded import (
    CORRELATION_QUOTA,
    HALT_DAILY,
    HALT_DRAWDOWN,
    VwapGuarded,
)
from tests.helpers_market import frame, market

START = pd.Timestamp("2026-01-01 00:00:00", tz="UTC")
BARS = 380          # as_of günün 92. barına düşer: seans VWAP'i anlamlı bir pencere görür
SYMBOL = "SOL-USDT-SWAP"


@pytest.fixture()
def config() -> dict[str, Any]:
    return resolve_layer(load_config(), "scalp").config


def _closes(level: float = 100.0) -> list[float]:
    """Neredeyse yatay bir seri: ADX düşük, EMA eğimi sıfır — rejim kapısı AÇIK kalsın."""
    return [level + (i % 4) * 0.05 for i in range(BARS)]


def _deviating(
    *,
    direction: str,
    distance: float = 3.0,
    climax: bool = True,
    rejection: bool = True,
    turn: bool = True,
) -> pd.DataFrame:
    """Son iki barda sapma + dönüş kuran çerçeve.

    `direction="long"` aşağı sapmayı (ucuzlama) kurar; short yukarı. `climax`, `rejection`
    ve `turn` üç kapıyı ayrı ayrı açıp kapatır — her biri tek başına ölçülebilsin diye.

    `rejection=False` son iki barın fitilini sapma yönünün TERSİNE koyar: `frame`
    yardımcısı `open == close` yazdığı için gövde sıfırdır ve simetrik bir aralıkta her
    iki fitil de aralığın yarısı olur, yani red mumu kapısı istemeden hep yanardı.
    """
    closes = _closes()
    sign = -1.0 if direction == "long" else 1.0
    closes[-2] = 100.0 + sign * distance
    closes[-1] = closes[-2] + (0.3 * -sign if turn else 0.3 * sign)
    volumes = [1.0] * BARS
    if climax:
        volumes[-2] = 6.0
    highs = [close + 0.2 for close in closes]
    lows = [close - 0.2 for close in closes]
    if not rejection:
        for index in (-2, -1):
            if direction == "long":      # alt fitil kısa, üst fitil uzun
                highs[index] = closes[index] + 0.9
                lows[index] = closes[index] - 0.1
            else:
                highs[index] = closes[index] + 0.1
                lows[index] = closes[index] - 0.9
    return frame(
        closes, volumes=volumes, highs=highs, lows=lows, freq="15min", start=START
    )


def _market(frames: dict[str, pd.DataFrame] | None = None, *, btc_closes: list[float] | None = None):
    payload = dict(frames or {SYMBOL: _deviating(direction="long")})
    btc = frame(
        _closes(50000.0) if btc_closes is None else btc_closes,
        spread=5.0, freq="15min", start=START,
    )
    return market(payload, btc=btc)


def _trade(*, r: float, closed_at: pd.Timestamp) -> ClosedTrade:
    return ClosedTrade(
        symbol=SYMBOL,
        direction="long",
        opened_at=closed_at - pd.Timedelta("1h"),
        closed_at=closed_at,
        r_multiple=r,
        signal_reason="test | arm=vwap_revert_guard",
        exit_reason="stop",
    )


def _position(symbol: str, direction: str, *, opened_at: pd.Timestamp) -> Position:
    return Position(
        symbol=symbol,
        direction=direction,  # type: ignore[arg-type]
        entry_price=100.0,
        stop_price=98.0,
        opened_at=opened_at,
    )


# --------------------------------------------------------------------------- #
# Sinyal: kurulum ve kapılar
# --------------------------------------------------------------------------- #
def test_emits_a_risk_sized_signal_on_a_guarded_setup(config: dict[str, Any]) -> None:
    signals = VwapGuarded(config=config).generate_signals(_market())

    assert len(signals) == 1
    signal = signals[0]
    assert signal.direction == "long"
    # Sabit teminat YOK: boyut core/portfolio.py'de risk kuralıyla kurulur (kural 11).
    assert signal.sizing == "risk"
    assert signal.notional_fraction is None
    assert signal.stop_price is not None and signal.stop_price < 100.0
    # Üç aşamalı çıkış yönetimi tek kopyadan gelir (strategies/exit_management.py).
    assert signal.breakeven_at_r is not None and signal.partial_tp is not None
    assert find_tag(signal.reason, "arm") == guarded_signal.ARM_NAME
    assert find_tag(signal.reason, "exhaustion") == "klimaks"
    assert find_tag(signal.reason, "btc") == "flat"


def test_at_most_one_signal_per_bar(config: dict[str, Any]) -> None:
    """Kota ancak pozisyonlar zaman içinde birikirken bir şey ölçer."""
    frames = {
        SYMBOL: _deviating(direction="long"),
        "ETH-USDT-SWAP": _deviating(direction="long"),
        "ADA-USDT-SWAP": _deviating(direction="long"),
    }

    assert len(VwapGuarded(config=config).generate_signals(_market(frames))) == 1


def test_setup_without_exhaustion_is_rejected(config: dict[str, Any]) -> None:
    """Dönüş bir niyet beyanıdır; klimaks ya da red mumu onun kanıtıdır."""
    model = VwapGuarded(config=config)
    neither = _deviating(direction="long", climax=False, rejection=False)

    signals = model.generate_signals(_market({SYMBOL: neither}))

    assert signals == []
    assert model.take_survey()[guarded_signal.NO_EXHAUSTION] == 1


def test_setup_without_a_turn_is_rejected(config: dict[str, Any]) -> None:
    model = VwapGuarded(config=config)

    signals = model.generate_signals(_market({SYMBOL: _deviating(direction="long", turn=False)}))

    assert signals == []
    assert model.take_survey()[guarded_signal.STILL_EXTENDING] == 1


def test_trending_symbol_is_rejected(config: dict[str, Any]) -> None:
    """Ortalamaya dönüş tezi trendde geçersizdir: ADX/EMA eğimi kapısı yanar."""
    model = VwapGuarded(config=config)
    trending = frame(
        [100.0 + 0.5 * i for i in range(BARS)], spread=0.2, freq="15min", start=START
    )

    signals = model.generate_signals(_market({SYMBOL: trending}))

    assert signals == []
    survey = model.take_survey()
    assert survey.get(guarded_signal.TRENDING, 0) + survey.get(guarded_signal.INSIDE_BAND, 0) == 1


def test_long_needs_a_wider_band_than_short() -> None:
    """Aynı büyüklükteki sapma, LONG tarafında bandın içinde sayılır.

    Ölçüm modülün kendi parametresiyle yapılır: bandın asimetrisi bir yorum değil, bir
    sayıdır ve `band_for` onu tek yerde taşır.
    """
    params = guarded_signal.GuardParams(
        band_long=4.0, band_short=2.5, min_vwap_bars=8, adx_period=14, adx_max=22.0,
        ema_period=50, slope_bars=10, max_slope_atr=1.5, exhaustion_lookback=20,
        climax_mult=1.5, rejection_wick_ratio=0.5,
    )

    _, down = guarded_signal.scan(
        _market({SYMBOL: _deviating(direction="long")}),
        atr_period=14, params=params, bias="flat", symbols=[SYMBOL],
    )
    up_candidates, up = guarded_signal.scan(
        _market({SYMBOL: _deviating(direction="short")}),
        atr_period=14, params=params, bias="flat", symbols=[SYMBOL],
    )

    assert down.counts[guarded_signal.INSIDE_BAND] == 1
    assert up.counts[guarded_signal.SETUP] == 1
    assert up_candidates[0].direction == "short"


def test_config_keeps_the_long_band_wider(config: dict[str, Any]) -> None:
    band = config["vwap"]["guarded"]["band"]

    assert float(band["long"]) > float(band["short"]) >= 2.5


def test_btc_downtrend_blocks_a_long(config: dict[str, Any]) -> None:
    """Altcoin'i BTC trendine KARŞI fade etme kuralı."""
    model = VwapGuarded(config=config)
    falling_btc = [50000.0 - 20.0 * i for i in range(BARS)]

    signals = model.generate_signals(
        _market({SYMBOL: _deviating(direction="long")}, btc_closes=falling_btc)
    )

    assert signals == []
    assert model.take_survey()[guarded_signal.AGAINST_BTC] == 1


def test_btc_uptrend_does_not_block_a_long(config: dict[str, Any]) -> None:
    """Kapı YÖNLÜDÜR: akıntıya karşı olanı keser, lehte olanı değil."""
    rising_btc = [50000.0 + 20.0 * i for i in range(BARS)]

    signals = VwapGuarded(config=config).generate_signals(
        _market({SYMBOL: _deviating(direction="long")}, btc_closes=rising_btc)
    )

    assert len(signals) == 1
    assert find_tag(signals[0].reason, "btc") == "up"


def test_survey_counts_every_examined_symbol_exactly_once(config: dict[str, Any]) -> None:
    """Σsayım = incelenen sembol: bir kapının sessizce kaybettiği sembol olamaz."""
    model = VwapGuarded(config=config)
    frames = {
        SYMBOL: _deviating(direction="long"),
        "ETH-USDT-SWAP": frame(_closes(200.0), spread=0.2, freq="15min", start=START),
    }

    model.generate_signals(_market(frames))
    survey = model.take_survey()

    counted = sum(value for key, value in survey.items() if key in guarded_signal.REASONS)
    assert counted == len(frames)


def test_universe_excludes_the_thin_book_symbols(config: dict[str, Any]) -> None:
    """PENGU/ETHFI elemesi config'te AÇIKÇA durur — sessiz bir filtre değildir."""
    universe = config["vwap"]["guarded"]["universe"]

    assert "PENGU-USDT-SWAP" not in universe
    assert "ETHFI-USDT-SWAP" not in universe
    # Model evreni katmanın evreninin ÖZ ALT KÜMESİDİR: evren dışı bir sembolde işlem
    # açmak kural 6'yı (aynı izinli evren) delerdi.
    assert set(universe) < set(resolve_layer(load_config(), "scalp").symbols)


def test_symbols_outside_the_model_universe_are_never_scanned(config: dict[str, Any]) -> None:
    model = VwapGuarded(config=config)
    frames = {"PENGU-USDT-SWAP": _deviating(direction="long")}

    assert model.generate_signals(_market(frames)) == []
    # Tarama koştu ama hiçbir sembol incelenmedi: sayım SIFIRDIR, yok değil — "kapı
    # eledi" ile "evrende değildi" aynı hücreye yazılmaz.
    assert sum(model.take_survey().values()) == 0


# --------------------------------------------------------------------------- #
# Risk kesicileri (kural 16: yalnızca KENDİ kapanmış işlemleri)
# --------------------------------------------------------------------------- #
def test_daily_loss_limit_stops_the_scan(config: dict[str, Any]) -> None:
    model = VwapGuarded(config=config)
    snapshot = _market()
    today = snapshot.as_of.normalize()

    model.observe_closed_trades(
        [_trade(r=-1.4, closed_at=today + pd.Timedelta("1h")),
         _trade(r=-1.1, closed_at=today + pd.Timedelta("2h"))]
    )

    assert model.generate_signals(snapshot) == []
    assert model.take_survey() == {HALT_DAILY: 1}


def test_yesterdays_loss_does_not_stop_today(config: dict[str, Any]) -> None:
    """Günlük limit GÜNLÜKTÜR: sayaç UTC gününde sıfırlanır."""
    model = VwapGuarded(config=config)
    snapshot = _market()
    yesterday = snapshot.as_of.normalize() - pd.Timedelta("1D")

    model.observe_closed_trades([_trade(r=-3.0, closed_at=yesterday)])

    assert len(model.generate_signals(snapshot)) == 1


def test_drawdown_kill_switch_stops_the_scan(config: dict[str, Any]) -> None:
    """Zirveden %8 (= 8R) düşüş: yeni kurulum aranmaz."""
    model = VwapGuarded(config=config)
    snapshot = _market()
    day = snapshot.as_of.normalize() - pd.Timedelta("3D")

    model.observe_closed_trades(
        [_trade(r=2.0, closed_at=day), _trade(r=-8.5, closed_at=day + pd.Timedelta("1h"))]
    )

    assert model.generate_signals(snapshot) == []
    assert model.take_survey() == {HALT_DRAWDOWN: 1}


def test_drawdown_threshold_is_the_equity_percentage_over_risk_per_trade(
    config: dict[str, Any]
) -> None:
    """%8 / %1 = 8R. Yaklaşıklık değil, kural 11'in doğrudan sonucu."""
    model = VwapGuarded(config=config)
    expected = float(config["vwap"]["guarded"]["risk"]["max_drawdown_pct"]) / float(
        config["risk_per_trade"]
    )

    assert model._max_drawdown_r == pytest.approx(expected)


def test_shallow_drawdown_does_not_stop_the_scan(config: dict[str, Any]) -> None:
    model = VwapGuarded(config=config)
    snapshot = _market()
    day = snapshot.as_of.normalize() - pd.Timedelta("3D")

    model.observe_closed_trades(
        [_trade(r=2.0, closed_at=day), _trade(r=-5.0, closed_at=day + pd.Timedelta("1h"))]
    )

    assert len(model.generate_signals(snapshot)) == 1


def test_unmeasurable_trades_do_not_count_as_zero(config: dict[str, Any]) -> None:
    """`r_multiple is None` = ölçülemedi; 0.0 saymak onu "tam başabaş" göstermek olurdu."""
    model = VwapGuarded(config=config)
    snapshot = _market()
    today = snapshot.as_of.normalize()
    unmeasured = ClosedTrade(
        symbol=SYMBOL, direction="long", opened_at=today, closed_at=today,
        r_multiple=None, signal_reason="test", exit_reason="stop",
    )

    model.observe_closed_trades([_trade(r=-1.9, closed_at=today), unmeasured])

    assert len(model.generate_signals(snapshot)) == 1


# --------------------------------------------------------------------------- #
# Korelasyon kotası ve zaman stop'u
# --------------------------------------------------------------------------- #
def test_correlation_quota_blocks_a_third_position_in_the_same_direction(
    config: dict[str, Any]
) -> None:
    model = VwapGuarded(config=config)
    snapshot = _market()
    previous_bar = snapshot.as_of - pd.Timedelta("15min")
    model.manage_positions(
        _market_at(previous_bar, snapshot),
        [
            _position("ETH-USDT-SWAP", "long", opened_at=previous_bar),
            _position("ADA-USDT-SWAP", "long", opened_at=previous_bar),
        ],
    )

    assert model.generate_signals(snapshot) == []
    assert model.take_survey()[CORRELATION_QUOTA] == 1


def test_quota_counts_the_models_own_signal_from_the_previous_bar(
    config: dict[str, Any]
) -> None:
    """Görüntü bir bar bayattır; kendi sinyalini saymazsak kota tam bir pozisyon aşılırdı."""
    model = VwapGuarded(config=config)
    snapshot = _market()
    previous_bar = snapshot.as_of - pd.Timedelta("15min")
    earlier = _market_at(previous_bar, snapshot)
    model.manage_positions(earlier, [_position("ETH-USDT-SWAP", "long", opened_at=previous_bar)])
    # Bir önceki barın kendi sinyali: kotanın ikinci yarısı.
    model._emitted = (("ADA-USDT-SWAP", "long"),)
    model._emitted_at = previous_bar

    assert model.generate_signals(snapshot) == []
    assert model.take_survey()[CORRELATION_QUOTA] == 1


def test_stale_position_snapshot_is_not_carried_forward(config: dict[str, Any]) -> None:
    """Pozisyonu olmayan barda motor `manage_positions`ı çağırmaz: kota sıfırdan başlar."""
    model = VwapGuarded(config=config)
    snapshot = _market()
    old_bar = snapshot.as_of - pd.Timedelta("2h")
    model.manage_positions(
        _market_at(old_bar, snapshot),
        [
            _position("ETH-USDT-SWAP", "long", opened_at=old_bar),
            _position("ADA-USDT-SWAP", "long", opened_at=old_bar),
        ],
    )

    assert len(model.generate_signals(snapshot)) == 1


def test_opposite_direction_is_not_blocked(config: dict[str, Any]) -> None:
    model = VwapGuarded(config=config)
    snapshot = _market()
    previous_bar = snapshot.as_of - pd.Timedelta("15min")
    model.manage_positions(
        _market_at(previous_bar, snapshot),
        [
            _position("ETH-USDT-SWAP", "short", opened_at=previous_bar),
            _position("ADA-USDT-SWAP", "short", opened_at=previous_bar),
        ],
    )

    assert len(model.generate_signals(snapshot)) == 1


def test_time_stop_closes_a_position_at_the_configured_bar_count(
    config: dict[str, Any]
) -> None:
    model = VwapGuarded(config=config)
    snapshot = _market()
    bars = int(config["vwap"]["guarded"]["time_stop_bars"])
    assert 8 <= bars <= 12
    opened = snapshot.as_of - pd.Timedelta(minutes=15 * bars)

    instructions = model.manage_positions(snapshot, [_position(SYMBOL, "long", opened_at=opened)])

    assert len(instructions) == 1
    assert find_tag(instructions[0].reason, "exit_rule") == "time_stop"


def test_time_stop_leaves_a_younger_position_alone(config: dict[str, Any]) -> None:
    model = VwapGuarded(config=config)
    snapshot = _market()
    bars = int(config["vwap"]["guarded"]["time_stop_bars"]) - 1
    opened = snapshot.as_of - pd.Timedelta(minutes=15 * bars)

    assert model.manage_positions(snapshot, [_position(SYMBOL, "long", opened_at=opened)]) == []


# --------------------------------------------------------------------------- #
# Kopyaya dokunulmadı (kural 15b)
# --------------------------------------------------------------------------- #
def test_the_replica_is_untouched(config: dict[str, Any]) -> None:
    """Uyarlama AYRI bir modeldir: kopyanın bayrağı, limitleri ve evreni değişmedi."""
    clone = VwapClone(config=config)

    assert clone.is_replica is True
    assert VwapGuarded.is_replica is False
    assert clone.limits is not None and clone.limits.leverage == 10.0
    assert "PENGU-USDT-SWAP" in clone._universe


def test_the_two_models_write_different_arm_tags() -> None:
    """Tek bir kol adı, iki kural kümesini aynı kolmuş gibi gösterirdi."""
    from strategies.vwap import clone_signal, signal as managed_signal

    assert len({clone_signal.ARM_NAME, managed_signal.ARM_NAME, guarded_signal.ARM_NAME}) == 3


def _market_at(ts: pd.Timestamp, snapshot: Any):
    """Aynı çerçevelerin `ts` barında duran kopyası (kural 12: sonrası görünmez)."""
    frames = {
        symbol: value.loc[:ts] for symbol, value in snapshot.ohlcv.items()
    }
    return market(frames, btc=snapshot.btc.loc[:ts], as_of=ts)

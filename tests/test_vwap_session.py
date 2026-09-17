"""F0 (`vwap_session`): kopyanın BİRİMİ düzeltilmiş hâli — tek değişken, başka filtre yok.

Bu dosyanın ölçtüğü şey iki yönlüdür ve ikincisi en az birincisi kadar önemlidir:

1. **Ne DEĞİŞTİ:** VWAP çapası ve σ tahmincisi. İki modül aynı barda farklı karar
   verebilmelidir — veremezlerse ortada bir eksen yoktur.
2. **Ne DEĞİŞMEDİ:** dönüş şartı, geometri, boyutlandırma, limitler ve **hiçbir ev
   kapısının olmaması.** Karar 45'in dersi tam olarak buydu: kapılar sessizce sızarsa
   model ölçülemez hâle gelir ve bunu yakalayacak tek şey testtir.
"""

from __future__ import annotations

from typing import Any

import pandas as pd
import pytest

from core.config import load_config
from core.layers import resolve_layer
from core.tags import find_tag
from strategies.base import Strategy
from strategies.vwap import clone_signal, session_signal
from strategies.vwap_clone import VwapClone
from strategies.vwap_guarded import VwapGuarded
from strategies.vwap_session import VwapSession
from tests.helpers_market import frame, market

START = pd.Timestamp("2026-01-01 00:00:00", tz="UTC")
BARS = 380
SYMBOL = "SOL-USDT-SWAP"


@pytest.fixture()
def config() -> dict[str, Any]:
    return resolve_layer(load_config(), "scalp").config


def _reverting(*, drop: float = 3.0, turn: bool = True) -> pd.DataFrame:
    """Neredeyse yatay bir seans + son iki barda sapma ve dönüş."""
    closes = [100.0 + (index % 4) * 0.05 for index in range(BARS)]
    closes[-2] = 100.0 - drop
    closes[-1] = closes[-2] + (0.3 if turn else -0.3)
    return frame(closes, spread=0.2, freq="15min", start=START)


def _market(frames: dict[str, pd.DataFrame] | None = None):
    payload = dict(frames or {SYMBOL: _reverting()})
    btc = frame(
        [50000.0 + (index % 4) * 0.05 for index in range(BARS)],
        spread=5.0, freq="15min", start=START,
    )
    return market(payload, btc=btc)


# --------------------------------------------------------------------------- #
# Değişen TEK şey: çapa ve σ
# --------------------------------------------------------------------------- #
def test_the_two_modules_can_disagree_on_the_same_bar(config: dict[str, Any]) -> None:
    """Eksen ancak iki birim aynı barda farklı karar verebiliyorsa vardır."""
    bars = _reverting()
    as_of = bars.index[-1]
    session_params = session_signal.SessionParams(band_mult=2.0, tp_mult=0.75, sl_mult=0.5)
    clone_params = clone_signal.CloneParams(band_mult=2.0, tp_mult=0.75, sl_mult=0.5)

    session_candidate, _ = session_signal.detect(
        bars, symbol=SYMBOL, as_of=as_of, params=session_params, min_bars=8
    )
    clone_candidate, _ = clone_signal.detect(
        bars, symbol=SYMBOL, as_of=as_of, params=clone_params,
        vwap_window=300, std_window=20, min_bars=25,
    )

    assert session_candidate is not None and clone_candidate is not None
    # Aynı bar, aynı çarpanlar, FARKLI σ -> farklı stop mesafesi ve farklı z.
    assert session_candidate.std != pytest.approx(clone_candidate.std)
    assert session_candidate.stop_price != pytest.approx(clone_candidate.stop_price)


def test_the_session_anchor_resets_every_day() -> None:
    """Çapa gün başında sıfırlanır: seans VWAP'i dünün barlarını taşımaz."""
    bars = _reverting()
    as_of = bars.index[-1]
    candidate, _ = session_signal.detect(
        bars, symbol=SYMBOL, as_of=as_of,
        params=session_signal.SessionParams(band_mult=2.0, tp_mult=0.75, sl_mult=0.5),
        min_bars=8,
    )

    assert candidate is not None
    assert candidate.bars == int((bars.index >= as_of.normalize()).sum())
    assert candidate.bars < len(bars)


def test_a_young_session_has_no_band() -> None:
    """Günün ilk barlarında VWAP tek bir mumun etrafındadır; sayı üretilmez."""
    bars = _reverting()
    as_of = bars.index[-1]

    _, reason = session_signal.detect(
        bars, symbol=SYMBOL, as_of=as_of,
        params=session_signal.SessionParams(band_mult=2.0, tp_mult=0.75, sl_mult=0.5),
        min_bars=500,  # bu seansta asla dolmaz
    )

    assert reason == session_signal.NO_BANDS


# --------------------------------------------------------------------------- #
# Değişmeyen her şey
# --------------------------------------------------------------------------- #
def test_the_turn_condition_is_the_sources_close_comparison() -> None:
    """Dönüş şartının TAMAMI `close > prev_close`tur — "sapma daraldı" şartı YOK."""
    params = session_signal.SessionParams(band_mult=2.0, tp_mult=0.75, sl_mult=0.5)
    bars = _reverting(turn=False)

    _, reason = session_signal.detect(
        bars, symbol=SYMBOL, as_of=bars.index[-1], params=params, min_bars=8
    )

    assert reason == session_signal.NO_TURN


def test_no_house_gates_are_applied(config: dict[str, Any]) -> None:
    """%1 stop tabanı, 1.5R kapısı ve zaman stop'u UYGULANMAZ (kaynakta yok).

    Bu kurulumun stop mesafesi katmanın %1'lik tabanının ALTINDADIR: taban sızsaydı
    sinyal hiç üretilmezdi. Zaman stop'unun yokluğu da ayrıca ölçülür — `manage_positions`
    varsayılanı döner, yani modelin hiçbir çıkış TALİMATI yoktur.
    """
    model = VwapSession(config=config)

    signals = model.generate_signals(_market())

    assert len(signals) == 1
    signal = signals[0]
    entry = float(_reverting()["close"].iloc[-1])
    stop_pct = abs(entry - signal.stop_price) / entry
    assert stop_pct < float(config["scalp"]["min_stop_pct"])
    assert VwapSession.manage_positions is Strategy.manage_positions


def test_sizing_is_the_sources_fixed_margin(config: dict[str, Any]) -> None:
    model = VwapSession(config=config)

    signal = model.generate_signals(_market())[0]

    assert signal.sizing == "notional_fraction"
    assert signal.notional_fraction == pytest.approx(0.5)
    assert signal.stop_price is not None      # kopyada stop ZORUNLUDUR (kural 15b)
    assert model.limits is not None and model.limits.leverage == 10.0
    assert find_tag(signal.reason, "arm") == session_signal.ARM_NAME


def test_three_stage_exit_management_is_kept(config: dict[str, Any]) -> None:
    signal = VwapSession(config=config).generate_signals(_market())[0]

    assert signal.breakeven_at_r is not None
    assert signal.partial_tp is not None
    assert signal.trail_giveback_pct is not None


def test_the_model_does_not_learn(config: dict[str, Any]) -> None:
    """Öğrenme kaldırıldı: birim ekseninin yanında ikinci bir değişken olamaz."""
    assert VwapSession.observe_closed_trades is Strategy.observe_closed_trades


def test_multipliers_are_the_middle_of_the_source_grid(config: dict[str, Any]) -> None:
    """Süpürme yok: orta değer, sonucu görmeden seçilebilen tek değerdir."""
    session = config["vwap"]["session"]
    clone = config["vwap"]["clone"]

    assert float(session["band_mult"]) == pytest.approx(sorted(clone["band_mults"])[1])
    assert float(session["tp_mult"]) == pytest.approx(sorted(clone["tp_mults"])[1])
    assert float(session["sl_mult"]) == pytest.approx(float(clone["sl_mult"]))


def test_signals_are_capped_by_the_models_own_quota(config: dict[str, Any]) -> None:
    """Kotanın ötesindeki emir bir sonraki barda zaten reddedilirdi; kuyruk şişirilmez."""
    model = VwapSession(config=config)
    frames = {
        f"{name}-USDT-SWAP": _reverting()
        for name in ("SOL", "ETH", "ADA", "XRP", "LINK", "AVAX", "NEAR")
    }

    signals = model.generate_signals(_market(frames))

    assert len(signals) == model.limits.max_positions == 5


def test_every_examined_symbol_is_counted_exactly_once(config: dict[str, Any]) -> None:
    model = VwapSession(config=config)
    frames = {SYMBOL: _reverting(), "ETH-USDT-SWAP": frame(
        [200.0] * BARS, spread=0.2, freq="15min", start=START
    )}

    model.generate_signals(_market(frames))
    survey = model.take_survey()

    assert sum(survey.values()) == len(frames)


def test_the_scan_order_is_the_symbol_name(config: dict[str, Any]) -> None:
    """Güce göre sıralama YOK (kaynakta da yok); sıra tekrarlanabilir olmalı."""
    model = VwapSession(config=config)
    frames = {"ZZZ-USDT-SWAP": _reverting(), "AAA-USDT-SWAP": _reverting()}

    signals = model.generate_signals(_market(frames))

    assert [signal.symbol for signal in signals] == ["AAA-USDT-SWAP", "ZZZ-USDT-SWAP"]


# --------------------------------------------------------------------------- #
# Kopya ve reddedilen tasarım
# --------------------------------------------------------------------------- #
def test_the_replica_is_untouched(config: dict[str, Any]) -> None:
    """Model 13 kendi kurallarıyla koşmaya devam eder (kural 15b)."""
    clone = VwapClone(config=config)

    assert clone.is_replica is True
    assert clone._vwap_window == 300 and clone._std_window == 20
    assert VwapSession.is_replica is True  # 1R'si de sabit teminattan gelir


def test_the_rejected_design_no_longer_runs(config: dict[str, Any]) -> None:
    """`vwap_guarded` (model 18) kâğıt katmanından ÇIKARILDI (karar 46).

    Kodu ve kaydı durur — backtest onu `--models` ile hâlâ çağırabilir — ama 0.1
    işlem/gün kadansla kâğıtta koşmak kanıt biriktirmiyordu.
    """
    layer = resolve_layer(load_config(), "scalp")

    assert VwapGuarded.name not in layer.models
    assert VwapSession.name not in layer.models  # F0 önce ÖLÇÜLÜR, sonra koşar

"""Mod B (`vwap_bounce`): trend gününde VWAP'e dönüşte trend yönünde giriş.

Ölçtüğü iki şey var: (1) kurulumun gerçekten Mod A'nın TERSİ koşullarda doğduğu —
ikisi birbirinin işlemini çalmamalı; (2) ortak gövdenin (boyut, maker giriş, zaman
stop'u, likidite) MİRAS alındığı, kopyalanmadığı. İkincisi ölçümün şartıdır: iki ayrı
gövde, "kurulum farkı" iddiasını çürütürdü.
"""

from __future__ import annotations

import copy
from typing import Any

import pandas as pd
import pytest

from core.config import load_config
from core.layers import resolve_layer
from core.tags import find_tag
from strategies.vwap import bounce_signal
from strategies.vwap_bounce import VwapBounce
from strategies.vwap_scored import TRENDING, VwapScored
from tests.helpers_market import frame, market

START = pd.Timestamp("2026-01-01 00:00:00", tz="UTC")
BARS = 400
SYMBOL = "SOL-USDT-SWAP"
LIQUID = 3000.0


@pytest.fixture()
def config() -> dict[str, Any]:
    return resolve_layer(load_config(), "scalp").config


def _uptrend_pullback(
    *, pullback: float = 0.8, recover: float = 0.5, reject: bool = True
) -> pd.DataFrame:
    """Yükselen bir seans + son barda VWAP'e geri çekilme ve KISMİ toparlanma.

    Toparlanma kısmi olmak zorunda: bar trendin tepesine kadar geri gelseydi giriş,
    önceki ucun dibine oturur ve hedef/stop oranı kapının altında kalırdı — yani fixture
    kurulumun kendisini yok ederdi.
    """
    closes = [100.0 + 0.08 * index for index in range(BARS)]
    highs = [close + 0.15 for close in closes]
    lows = [close - 0.15 for close in closes]
    last = closes[-1]
    closes[-1] = last - recover
    lows[-1] = last - pullback
    highs[-1] = last
    if not reject:
        closes[-1] = last - pullback   # VWAP'in altında kapanış: KIRILMA
    bars = frame(
        closes, volumes=[LIQUID] * BARS, highs=highs, lows=lows,
        freq="15min", start=START,
    )
    # `helpers_market.frame` açılışı kapanışa eşitler; bounce şartı GÖVDENİN trend
    # yönünde olmasını da ister (`close > open`), bu yüzden son barın açılışı elle
    # aşağı çekilir — yani mum gerçekten geri çekilip toparlanmıştır.
    bars.loc[bars.index[-1], "open"] = float(bars["low"].iloc[-1])
    return bars


def _market(frames: dict[str, pd.DataFrame] | None = None):
    payload = dict(frames or {SYMBOL: _uptrend_pullback()})
    btc = frame(
        [50000.0 + (index % 4) * 0.05 for index in range(BARS)],
        spread=5.0, volumes=[LIQUID] * BARS, freq="15min", start=START,
    )
    return market(payload, btc=btc)


def test_the_body_is_inherited_not_copied() -> None:
    """Boyut, dolum, zaman stop'u ve likidite Mod A'nın gövdesinden gelir."""
    assert issubclass(VwapBounce, VwapScored)
    for shared in ("_liquid", "_limit_price", "_progress_r", "manage_positions", "_signal"):
        assert getattr(VwapBounce, shared) is getattr(VwapScored, shared)
    # Ayrışan noktalar ise GERÇEKTEN ayrışmış olmalı.
    for override in ("_detect", "_regime_tier", "_extension", "_load_signal_params"):
        assert getattr(VwapBounce, override) is not getattr(VwapScored, override)


def test_a_trend_pullback_becomes_a_long_bounce(config: dict[str, Any]) -> None:
    model = VwapBounce(config=config)

    signals = model.generate_signals(_market())

    assert len(signals) == 1
    signal = signals[0]
    assert signal.direction == "long"
    assert find_tag(signal.reason, "arm") == bounce_signal.ARM_NAME
    # Stop VWAP'in ÖTESİNDE, yani girişin epeyce altında; hedef yukarıda.
    assert signal.stop_price < signal.take_profits[0].price
    assert signal.entry_type == "limit"


def test_a_break_through_the_vwap_is_not_a_bounce(config: dict[str, Any]) -> None:
    """Değip geri alınmayan VWAP bir geri çekilme değil KIRILMADIR."""
    model = VwapBounce(config=config)

    signals = model.generate_signals(_market({SYMBOL: _uptrend_pullback(reject=False)}))

    assert signals == []
    assert model.take_survey().get(bounce_signal.NO_REJECTION) == 1


def test_a_flat_session_has_no_trend_to_ride(config: dict[str, Any]) -> None:
    model = VwapBounce(config=config)
    flat = frame(
        [100.0 + (index % 4) * 0.05 for index in range(BARS)],
        spread=0.2, volumes=[LIQUID] * BARS, freq="15min", start=START,
    )

    signals = model.generate_signals(_market({SYMBOL: flat}))

    assert signals == []
    survey = model.take_survey()
    assert survey.get(bounce_signal.NO_TREND, 0) + survey.get(TRENDING, 0) == 1


def test_the_size_tier_is_inverted(config: dict[str, Any]) -> None:
    """Mod A'da sakin rejim tam boydu; burada GÜÇLÜ trend tam boydur."""
    model = VwapBounce(config=config)

    assert model._regime_tier(40.0) == (model._size_full, 1.0)
    assert model._regime_tier(27.0) == (model._size_half, 0.5)
    assert model._regime_tier(10.0) is None      # eşiğin altında bounce YOK
    # Mod A'nın kademesi tam tersidir.
    scored = VwapScored(config=config)
    assert scored._regime_tier(10.0) == (scored._size_full, 1.0)
    assert scored._regime_tier(40.0) is None


def test_ranking_uses_geometry_not_the_distance_to_the_vwap(config: dict[str, Any]) -> None:
    """Bounce'ta giriş VWAP'in DİBİNDEDİR: |z| sıralaması ölçüyü tersine çevirirdi."""
    model = VwapBounce(config=config)
    candidate = bounce_signal.BounceCandidate(
        symbol=SYMBOL, direction="long", entry_price=101.0, stop_price=100.0,
        target_price=103.0, vwap=100.5, std=0.5, z=1.0, bars=40, slope_sigma=2.0,
    )

    assert model._extension(candidate) == pytest.approx(candidate.reward_risk) == 2.0


def test_the_geometry_gate_lives_in_the_model(config: dict[str, Any]) -> None:
    """Modül kurulumun YERİNİ verir; "oynanır mı" sorusu modelin kuralıdır."""
    strict = copy.deepcopy(config)
    strict["vwap"]["bounce"]["min_reward_risk"] = 99.0
    model = VwapBounce(config=strict)

    assert model.generate_signals(_market()) == []
    assert model.take_survey().get(bounce_signal.BAD_GEOMETRY) == 1


def test_the_modes_overlap_only_where_both_speak_softly(config: dict[str, Any]) -> None:
    """25–30 aralığında İKİ model de YARIM boyla konuşur; bu bir çakışma değil.

    Kullanıcının kademesi böyle yazıldı: fade 22–30 arasında yarım boya düşer, bounce
    25'te yarım boyla başlar. Aradaki bant "ne tam denge ne tam trend" bölgesidir ve iki
    tezin de zayıf konuştuğu yerdir. Ayrı defterler sayesinde hangisinin haklı olduğu
    ÖLÇÜLEBİLİR — tek bir birleşik PnL bunu gizlerdi.
    """
    scored = VwapScored(config=config)
    bounce = VwapBounce(config=config)

    # Sakin rejim: yalnızca fade konuşur.
    assert scored._regime_tier(15.0) == (scored._size_full, 1.0)
    assert bounce._regime_tier(15.0) is None
    # Çok güçlü trend: yalnızca bounce konuşur.
    assert scored._regime_tier(45.0) is None
    assert bounce._regime_tier(45.0) == (bounce._size_full, 1.0)
    # Ara bant: ikisi de YARIM boy.
    assert scored._regime_tier(27.0) == (scored._size_half, 0.5)
    assert bounce._regime_tier(27.0) == (bounce._size_half, 0.5)

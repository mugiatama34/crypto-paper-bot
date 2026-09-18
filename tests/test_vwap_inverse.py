"""MODEL 22 (`vwap_inverse`): kopyanın TERSİ — ayrışan TEK şey yönün işareti.

Bu dosyanın ölçtüğü şey iki yönlüdür ve ikincisi en az birincisi kadar önemlidir:

1. **Ne DEĞİŞTİ:** yön ve seviyelerin tarafı. Ters model kopyanın `long` dediği barda
   `short` açmalı, seviyeleri girişe göre aynalanmış olmalıdır.
2. **Ne DEĞİŞMEDİ:** kurulumun YERİ (aynı sembol, aynı bar, aynı giriş), stop MESAFESİ,
   hedef MESAFESİ (dolayısıyla R:R ve maliyet ölçeği), boyutlandırma, limitler ve çıkış
   yönetimi. Bunlardan biri sessizce ayrışırsa 13 ↔ 22 farkı artık "yönün katkısı"
   olmaktan çıkar — F0'ın P4 dersinin aynısı.
"""

from __future__ import annotations

from typing import Any

import pandas as pd
import pytest

from core.config import load_config
from core.layers import resolve_layer
from core.tags import find_tag
from strategies.vwap import clone_signal
from strategies.vwap_clone import VwapClone
from strategies.vwap_inverse import VwapInverse
from tests.helpers_market import frame, market

START = pd.Timestamp("2026-01-01 00:00:00", tz="UTC")
BARS = 380
SYMBOL = "SOL-USDT-SWAP"


@pytest.fixture()
def config() -> dict[str, Any]:
    return resolve_layer(load_config(), "scalp").config


def _reverting(*, drop: float = 3.0, turn: bool = True) -> pd.DataFrame:
    closes = [100.0 + (index % 4) * 0.05 for index in range(BARS)]
    closes[-2] = 100.0 - drop
    closes[-1] = closes[-2] + (0.3 if turn else -0.3)
    return frame(closes, spread=0.2, freq="15min", start=START)


def _rallying() -> pd.DataFrame:
    """Yukarı sapma + aşağı dönüş: kopya SHORT der, ters model LONG açmalı."""
    closes = [100.0 + (index % 4) * 0.05 for index in range(BARS)]
    closes[-2] = 100.0 + 3.0
    closes[-1] = closes[-2] - 0.3
    return frame(closes, spread=0.2, freq="15min", start=START)


def _market(bars: pd.DataFrame):
    btc = frame(
        [50000.0 + (index % 4) * 0.05 for index in range(BARS)],
        spread=5.0, freq="15min", start=START,
    )
    return market({SYMBOL: bars}, btc=btc)


def _only(model, snapshot):
    signals = model.generate_signals(snapshot)
    assert len(signals) == 1, f"{model.name}: tek sinyal bekleniyordu, {len(signals)} geldi"
    return signals[0]


# --------------------------------------------------------------------------- #
# Değişen TEK şey: yön
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("bars_factory", "clone_direction", "inverse_direction"),
    [(_reverting, "long", "short"), (_rallying, "short", "long")],
)
def test_the_inverse_takes_the_opposite_side_of_the_same_setup(
    config: dict[str, Any], bars_factory, clone_direction: str, inverse_direction: str
) -> None:
    snapshot = _market(bars_factory())
    clone_signal_ = _only(VwapClone(config=config), snapshot)
    inverse_signal = _only(VwapInverse(config=config), snapshot)

    assert clone_signal_.direction == clone_direction
    assert inverse_signal.direction == inverse_direction
    assert inverse_signal.symbol == clone_signal_.symbol


def test_levels_are_mirrored_around_the_same_entry(config: dict[str, Any]) -> None:
    """Stop ve hedef MESAFESİ korunur; yalnızca tarafı değişir.

    Mesafenin korunması ölçümün koşuludur: stop mesafesi notional'ı, notional maliyeti
    belirler (kural 14). Aynalamak yerine stop ile hedefi TAKAS etmek R:R'yi de ters
    çevirirdi ve fark artık yönün değil geometrinin ölçüsü olurdu.
    """
    bars = _reverting()
    snapshot = _market(bars)
    entry = float(bars["close"].iloc[-1])

    clone_sig = _only(VwapClone(config=config), snapshot)
    inverse_sig = _only(VwapInverse(config=config), snapshot)

    clone_stop_distance = abs(entry - clone_sig.stop_price)
    inverse_stop_distance = abs(entry - inverse_sig.stop_price)
    assert inverse_stop_distance == pytest.approx(clone_stop_distance)

    clone_target = clone_sig.take_profits[0].price
    inverse_target = inverse_sig.take_profits[0].price
    assert abs(entry - inverse_target) == pytest.approx(abs(entry - clone_target))

    # Taraf gerçekten döndü: kopyada stop girişin ALTINDA, terste ÜSTÜNDE.
    assert clone_sig.stop_price < entry < clone_target
    assert inverse_target < entry < inverse_sig.stop_price


def test_the_reward_to_risk_is_identical(config: dict[str, Any]) -> None:
    """R:R korunmazsa fark yönün değil, geometrinin ölçüsü olur."""
    bars = _reverting()
    snapshot = _market(bars)
    entry = float(bars["close"].iloc[-1])

    clone_sig = _only(VwapClone(config=config), snapshot)
    inverse_sig = _only(VwapInverse(config=config), snapshot)

    def rr(signal) -> float:
        return abs(signal.take_profits[0].price - entry) / abs(entry - signal.stop_price)

    assert rr(inverse_sig) == pytest.approx(rr(clone_sig))


# --------------------------------------------------------------------------- #
# Değişmeyen her şey
# --------------------------------------------------------------------------- #
def test_sizing_limits_and_exit_management_are_untouched(config: dict[str, Any]) -> None:
    clone = VwapClone(config=config)
    inverse = VwapInverse(config=config)
    snapshot = _market(_reverting())

    assert inverse.limits == clone.limits
    assert inverse.is_replica is True

    clone_sig = _only(clone, snapshot)
    inverse_sig = _only(inverse, snapshot)
    assert inverse_sig.sizing == clone_sig.sizing == "notional_fraction"
    assert inverse_sig.notional_fraction == clone_sig.notional_fraction
    assert inverse_sig.breakeven_at_r == clone_sig.breakeven_at_r
    assert inverse_sig.partial_tp == clone_sig.partial_tp
    assert inverse_sig.trail_giveback_pct == clone_sig.trail_giveback_pct
    # Ev kapıları burada da YOKTUR: zaman stop'u uygulanmaz (kaynakta yok).
    assert inverse.manage_positions(snapshot, []) == []


def test_the_arm_tag_is_separate_but_the_combo_tag_is_kept(config: dict[str, Any]) -> None:
    """Kol AYRI olmalı (iki ZIT kural kümesi tek satırda toplanamaz), combo KALMALI.

    Kol etiketi paylaşılsaydı kırılımda iki modelin ortalama R'si birbirini götürürdü —
    yani ölçülmek istenen farkın kendisi görünmez olurdu. `combo` ise kalmalıdır: ters
    modelin kendi öğrenmesi (kural 16) o etiketten beslenir.
    """
    snapshot = _market(_reverting())
    inverse_sig = _only(VwapInverse(config=config), snapshot)

    assert find_tag(inverse_sig.reason, "arm") == "vwap_revert_inv"
    assert find_tag(inverse_sig.reason, "arm") != clone_signal.ARM_NAME
    assert find_tag(inverse_sig.reason, "combo") is not None


def test_the_draw_identity_is_shared_with_the_clone(config: dict[str, Any]) -> None:
    """Ölçülmeyen eksende çekiliş PAYLAŞILIR (model 15'in `rng_identity` kuralı)."""
    assert VwapInverse.rng_identity == VwapClone.rng_identity
    assert VwapInverse.arm_name != VwapClone.arm_name

    snapshot = _market(_reverting())
    clone = VwapClone(config=config)
    inverse = VwapInverse(config=config)
    # Aynı defterle (ikisi de boş) aynı barda aynı kombinasyon çekilmeli.
    assert find_tag(_only(clone, snapshot).reason, "combo") == find_tag(
        _only(inverse, snapshot).reason, "combo"
    )


def test_no_setup_means_no_signal_on_either_side(config: dict[str, Any]) -> None:
    """Ters model KENDİ kurulumunu aramaz: kopya susuyorsa o da susar."""
    snapshot = _market(_reverting(turn=False))
    assert VwapClone(config=config).generate_signals(snapshot) == []
    assert VwapInverse(config=config).generate_signals(snapshot) == []

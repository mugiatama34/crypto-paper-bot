"""Model 22 (`wave_coinflip`): yansıtma, akış ayrımı ve statü.

Ön-kayıt: docs/backtest.md > 6h > EK-1. Sınanan üç şey, EK-1'in üç iddiasıdır:

1. **Yansıtma mesafeyi KORUR.** "Ters" kurulumda stop ve hedef mesafeleri orijinalle
   birebir aynı; yön ve taraflar doğru. Mesafe kaysaydı kontrol başka bir maliyet
   ölçeğinde koşar, ⚠B yanar ve C-2 okunamaz olurdu — yani `scalp_coinflip`i reddetme
   gerekçemize kendimiz düşerdik.
2. **Yazı-tura akışı bandit çekilişini DEĞİŞTİRMEZ.** Yazı-tura sabit "aynı" döndürürse
   model, `wave_scalp` ile BİREBİR aynı sinyalleri üretir.
3. **Kontrol `REGISTRY`dedir ama hiçbir canlı `models` listesinde değildir.**
"""

from __future__ import annotations

import random
from dataclasses import replace

import pytest

from core.config import load_config
from core.layers import resolve_layer
from core.tags import find_tag, parse_tag
from core.validate import validate_signal
from strategies.registry import REGISTRY, build
from strategies.wave import clone_signal
from strategies.wave_coinflip import FLIPPED, SAME, WaveCoinflip
from tests.helpers_market import market
from tests.test_wave_clone_signal import buy_path, wave_frame

SYMBOL = "BTC-USDT-SWAP"


@pytest.fixture()
def layer():
    return resolve_layer(load_config(), "scalp")


@pytest.fixture()
def control(layer) -> WaveCoinflip:
    return build("wave_coinflip", config=layer.config)


@pytest.fixture()
def faithful(layer):
    return build("wave_scalp", config=layer.config)


def long_candidate() -> clone_signal.WaveCandidate:
    frame = wave_frame(buy_path())
    candidate, reason = clone_signal.detect(
        frame,
        symbol=SYMBOL,
        as_of=frame.index[-1],
        params=clone_signal.WaveParams(
            deviation_pct=1.2, tp_mult=1.618, sl_mult=0.15,
            retrace_min=0.236, retrace_max=0.886,
        ),
        atr_period=14,
        window_bars=300,
        min_deviation_pct=0.05,
    )
    assert reason == clone_signal.SETUP and candidate is not None
    return candidate


# --------------------------------------------------------------------------- #
# (1) Yansıtma
# --------------------------------------------------------------------------- #
def test_reflection_preserves_both_distances_exactly() -> None:
    original = long_candidate()
    flipped = clone_signal.reflect(original)

    assert original.direction == "long" and flipped.direction == "short"
    assert flipped.entry_price == original.entry_price
    # MESAFELER birebir aynı — S1'in ön koşulu.
    assert abs(flipped.entry_price - flipped.stop_price) == pytest.approx(
        abs(original.entry_price - original.stop_price), rel=1e-12
    )
    assert abs(flipped.target_price - flipped.entry_price) == pytest.approx(
        abs(original.target_price - original.entry_price), rel=1e-12
    )


def test_reflection_puts_the_levels_on_the_correct_sides() -> None:
    original = long_candidate()
    flipped = clone_signal.reflect(original)
    # Long: sl < giriş < tp. Short: tp < giriş < sl.
    assert original.stop_price < original.entry_price < original.target_price
    assert flipped.target_price < flipped.entry_price < flipped.stop_price


def test_reflection_is_an_involution() -> None:
    """İki kez yansıtmak orijinali verir — mesafe korumasının aritmetik kanıtı."""
    original = long_candidate()
    twice = clone_signal.reflect(clone_signal.reflect(original))
    assert twice.direction == original.direction
    assert twice.stop_price == pytest.approx(original.stop_price, rel=1e-12)
    assert twice.target_price == pytest.approx(original.target_price, rel=1e-12)


def test_reflection_works_from_short_to_long() -> None:
    original = long_candidate()
    short = replace(
        original,
        direction="short",
        stop_price=original.entry_price + (original.entry_price - original.stop_price),
        target_price=original.entry_price - (original.target_price - original.entry_price),
    )
    flipped = clone_signal.reflect(short)
    assert flipped.direction == "long"
    assert flipped.stop_price < flipped.entry_price < flipped.target_price


def test_reflection_does_not_touch_the_setup() -> None:
    """`setup` kaynağın GÖZLEMİDİR; yansıtılan şey pozisyonun yönü, gözlem değil."""
    original = long_candidate()
    flipped = clone_signal.reflect(original)
    assert flipped.setup is original.setup
    assert flipped.setup.direction == "long"
    assert flipped.setup.retrace == original.setup.retrace


def test_flipped_signal_still_passes_the_validation_gate(control, layer) -> None:
    frame = wave_frame(buy_path())
    snapshot = market({SYMBOL: frame})
    # Yazı-tura ne çıkarsa çıksın sinyal kapıdan geçmeli.
    signals = control.generate_signals(snapshot)
    assert len(signals) == 1
    validate_signal(
        signals[0],
        entry_price=float(frame["close"].iloc[-1]),
        allowed_directions=control.allowed_directions,
        symbol_universe=list(layer.symbols or []),
        is_benchmark=control.is_benchmark,
        is_replica=control.is_replica,
    )


# --------------------------------------------------------------------------- #
# (2) Akış ayrımı
# --------------------------------------------------------------------------- #
def test_forced_same_reproduces_the_faithful_model_signal_for_signal(
    control, faithful, monkeypatch
) -> None:
    """Yazı-tura sabit "aynı" ise iki model BİREBİR aynı sinyalleri üretir.

    Bu, bandit akışının PAYLAŞILDIĞININ kanıtıdır: paylaşılmasa iki model aynı barda
    farklı kombinasyonlar denerdi ve `coin=same` bile sinyalleri hizalamazdı.
    """
    monkeypatch.setattr(
        WaveCoinflip, "orient",
        lambda self, candidate, *, market: (
            self._flips.__setitem__(candidate.symbol, SAME) or candidate
        ),
    )
    frames = {symbol: wave_frame(buy_path()) for symbol in faithful._universe[:5]}
    snapshot = market(frames)

    mine = control.generate_signals(snapshot)
    theirs = faithful.generate_signals(snapshot)

    assert len(mine) == len(theirs) > 0
    for a, b in zip(mine, theirs):
        assert (a.symbol, a.direction) == (b.symbol, b.direction)
        assert a.stop_price == pytest.approx(b.stop_price, rel=1e-12)
        assert a.take_profits[0].price == pytest.approx(b.take_profits[0].price, rel=1e-12)
        assert find_tag(a.reason, "combo") == find_tag(b.reason, "combo")
        assert find_tag(a.reason, "pick") == find_tag(b.reason, "pick")


def test_bandit_stream_is_shared_with_the_faithful_model(control, faithful) -> None:
    """`rng_identity` MİRAS ALINIR: ölçülmeyen eksende çekiliş paylaşılır."""
    assert control.rng_identity == faithful.rng_identity == "wave_scalp"
    snapshot = market({SYMBOL: wave_frame(buy_path())})
    assert control._round_rng(snapshot).random() == faithful._round_rng(snapshot).random()


def test_coin_stream_is_separate_from_the_bandit_stream(control) -> None:
    """Tek akış olsaydı her yazı-tura ε dizisini bir adım kaydırırdı."""
    snapshot = market({SYMBOL: wave_frame(buy_path())})
    bandit = control._round_rng(snapshot).random()
    coin = control._coin_rng(snapshot, SYMBOL).random()
    assert bandit != coin


def test_coin_stream_forks_per_symbol(control) -> None:
    """Aynı barda iki sembolün yazı-turası bağımsız olmalı; yoksa bar başına tek çekiliş."""
    snapshot = market({SYMBOL: wave_frame(buy_path())})
    first = control._coin_rng(snapshot, "BTC-USDT-SWAP").random()
    second = control._coin_rng(snapshot, "ETH-USDT-SWAP").random()
    assert first != second


def test_coin_is_deterministic_for_the_same_bar_and_symbol(control) -> None:
    snapshot = market({SYMBOL: wave_frame(buy_path())})
    assert (
        control._coin_rng(snapshot, SYMBOL).random()
        == control._coin_rng(snapshot, SYMBOL).random()
    )


def test_coin_is_fair_over_many_bars(control) -> None:
    """S2'nin kod tarafı: çekiliş 0.5 etrafında olmalı (ölçümü deftere bakar)."""
    flips = 0
    trials = 4000
    for index in range(trials):
        rng = random.Random(f"{control._seed}:bar{index}:{control.name}:{SYMBOL}")
        if rng.random() < 0.5:
            flips += 1
    assert 0.47 < flips / trials < 0.53, flips / trials


# --------------------------------------------------------------------------- #
# Denetim izi
# --------------------------------------------------------------------------- #
def test_reason_tail_records_the_coin_result(control) -> None:
    signals = control.generate_signals(market({SYMBOL: wave_frame(buy_path())}))
    assert len(signals) == 1
    coin = parse_tag(signals[0].reason, "coin")
    assert coin in (SAME, FLIPPED)
    assert WaveCoinflip.coin_of(signals[0].reason) == coin
    # EK-1'in öteki etiketleri KAYBOLMAZ.
    assert parse_tag(signals[0].reason, "arm") == clone_signal.ARM_NAME
    assert parse_tag(signals[0].reason, "combo").startswith("dev")


def test_coin_tag_matches_the_actual_direction(control) -> None:
    """Etiket ile geometri ayrışamaz: `flipped` ise yön kaynağınkinin TERSİ olmalı."""
    frame = wave_frame(buy_path())
    source = long_candidate()
    signals = control.generate_signals(market({SYMBOL: frame}))
    coin = parse_tag(signals[0].reason, "coin")
    if coin == FLIPPED:
        assert signals[0].direction != source.direction
    else:
        assert signals[0].direction == source.direction


def test_coin_of_returns_none_when_the_tag_is_missing() -> None:
    assert WaveCoinflip.coin_of("x | arm=wave3_src") is None


# --------------------------------------------------------------------------- #
# (3) Statü
# --------------------------------------------------------------------------- #
def test_control_is_registered_but_not_live_anywhere() -> None:
    assert "wave_coinflip" in REGISTRY
    config = load_config()
    for name in ("base", "scalp", "ema", "xsec"):
        assert "wave_coinflip" not in resolve_layer(config, name).config["models"]


def test_control_is_a_competitor_not_a_benchmark_or_replica(control) -> None:
    """`random_ctrl`ün statüsüyle birebir: kontrol aynı sütunda yarışmalı."""
    assert control.is_benchmark is False
    assert control.is_replica is False
    assert control.limits is None


def test_control_inherits_every_rule_except_direction(control, faithful) -> None:
    """Ayrışan TEK şey yön: her şey MİRAS ALINIR, kopyalanmaz."""
    assert control._universe == faithful._universe
    assert [c.key for c in control._combos] == [c.key for c in faithful._combos]
    assert control._window_bars == faithful._window_bars
    assert control._min_deviation_pct == faithful._min_deviation_pct
    assert control._epsilon == faithful._epsilon
    assert control._min_symbol_samples == faithful._min_symbol_samples
    assert control._atr_period == faithful._atr_period
    assert control._exit == faithful._exit
    # `orient` DIŞINDA hiçbir davranış override edilmemiş olmalı.
    overridden = {
        name for name in vars(WaveCoinflip)
        if callable(vars(WaveCoinflip)[name]) and not name.startswith("__")
    }
    assert overridden <= {"orient", "_coin_rng", "last_flips", "_signal", "coin_of"}


def test_control_learns_from_its_own_ledger_only(control, faithful) -> None:
    """Kural 4: kontrol `wave_scalp`in posteriorunu OKUMAZ; defteri kendi adından türer."""
    from strategies.wave_scalp import ComboStats

    key = control._combos[0].key
    control._global[key] = ComboStats(trades=9, mean_r=2.0)
    assert faithful._global[key].trades == 0

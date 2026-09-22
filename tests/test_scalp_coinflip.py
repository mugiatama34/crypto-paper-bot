"""Model 23 (`scalp_coinflip`): yansıtma, akış ayrımı, eşleştirme ve statü.

Ön-kayıt: docs/backtest.md > 6i. Sınanan dört şey o bölümün dört iddiasıdır:

1. **Yansıtma mesafeyi KORUR.** "Ters" kurulumda stop ve hedef mesafeleri orijinalle
   birebir aynı; yön ve taraflar doğru. Mesafe kaysaydı kontrol başka bir maliyet
   ölçeğinde koşar, ⚠B yanar ve C-2 okunamaz olurdu. Bu, **S1a**'nın kendisidir
   (§6i > 7 > DÜZELTME-1): kurulum düzeyinde BİREBİR eşitlik. Defter düzeyindeki **S1b**
   (`avg_stop_distance_pct` farkı < %10 bağıl) burada SINANAMAZ ve sınanmamalıdır —
   dolum kümeleri `max_positions`/`max_short_positions` yüzünden ayrışır, yani o bir kod
   iddiası değil bir kıyas koşuludur ve ancak defterden okunur.
2. **Yazı-tura AYRI akıştadır ve kol/sembol çekilişine dokunmaz.** Yazı-tura sabit "aynı"
   döndürürse model `scalp_patient` ile BİREBİR aynı sinyali üretir.
3. **Yazı-tura ADİLDİR** (S2'nin kod tarafı: 10.000 çekilişte 0.5 ± 0.02).
4. **Statü ve kadro:** yarışmacıdır, scalp katmanında KOŞAR ve katmanın
   `acceptance.control_model`üdür.
"""

from __future__ import annotations

import random
from typing import Any, Sequence

import pandas as pd
import pytest

from core.config import get_setting, load_config
from core.layers import resolve_layer
from core.tags import find_tag, parse_tag
from core.validate import validate_signal
from strategies.registry import REGISTRY, build
from strategies.scalp import arms as arms_module
from strategies.scalp.arms import ArmParams, ArmSetup, SymbolView, reflect
from strategies.scalp_coinflip import FLIPPED, SAME, ScalpCoinflip
from tests.helpers_market import frame, market

DAY = pd.Timestamp("2026-03-02 00:00:00", tz="UTC")
SYMBOLS = ("BTC-USDT-SWAP", "ETH-USDT-SWAP", "SOL-USDT-SWAP", "XRP-USDT-SWAP")


@pytest.fixture()
def layer():
    return resolve_layer(load_config(), "scalp")


@pytest.fixture()
def config(layer) -> dict[str, Any]:
    return layer.config


@pytest.fixture()
def control(config) -> ScalpCoinflip:
    return build("scalp_coinflip", config=config)


@pytest.fixture()
def twin(config):
    return build("scalp_patient", config=config)


@pytest.fixture()
def data():
    closes = [100.0 + (i % 5) * 0.4 for i in range(120)]
    return market({
        symbol: frame(closes, spread=0.3, start=DAY, freq="15min") for symbol in SYMBOLS
    })


def long_setup(symbol: str = "BTC-USDT-SWAP") -> ArmSetup:
    return ArmSetup(
        arm="rsi2_reversal",
        symbol=symbol,
        direction="long",
        entry_price=100.0,
        stop_price=95.0,
        target_price=110.0,
        detail="RSI(2) aşırılık dönüşü",
    )


def _install(monkeypatch: pytest.MonkeyPatch, *, symbols: int = 4) -> None:
    """Tek kollu sahte tarama: ölçülen şey kolun tetiklemesi değil, YÖN kararı."""
    def arm(views: Sequence[SymbolView], params: ArmParams) -> list[ArmSetup]:
        return [
            ArmSetup(
                arm="rsi2_reversal",
                symbol=view.symbol,
                direction="long",
                entry_price=view.close,
                stop_price=view.close * 0.95,
                target_price=view.close * 1.10,
                detail="sahte kurulum",
            )
            for view in views[:symbols]
        ]
    monkeypatch.setattr(arms_module, "ARMS", {"rsi2_reversal": arm})


# --------------------------------------------------------------------------- #
# (1) Yansıtma
# --------------------------------------------------------------------------- #
def test_reflection_preserves_both_distances_exactly() -> None:
    original = long_setup()
    flipped = reflect(original)

    assert original.direction == "long" and flipped.direction == "short"
    assert flipped.entry_price == original.entry_price
    assert abs(flipped.entry_price - flipped.stop_price) == pytest.approx(
        abs(original.entry_price - original.stop_price), rel=1e-12
    )
    assert abs(flipped.target_price - flipped.entry_price) == pytest.approx(
        abs(original.target_price - original.entry_price), rel=1e-12
    )


def test_reflection_preserves_the_derived_gate_inputs() -> None:
    """Kapılar bu iki orandan okunur: ikisi de değişmezse kontrol AYNI kapılardan geçer."""
    original = long_setup()
    flipped = reflect(original)
    assert flipped.stop_distance_pct == pytest.approx(original.stop_distance_pct, rel=1e-12)
    assert flipped.reward_risk == pytest.approx(original.reward_risk, rel=1e-12)


def test_reflection_puts_the_levels_on_the_correct_sides() -> None:
    original = long_setup()
    flipped = reflect(original)
    assert original.stop_price < original.entry_price < original.target_price
    assert flipped.target_price < flipped.entry_price < flipped.stop_price


def test_reflection_works_from_short_to_long() -> None:
    short = reflect(long_setup())
    back = reflect(short)
    assert short.direction == "short" and back.direction == "long"
    assert back.stop_price < back.entry_price < back.target_price


def test_reflection_is_an_involution() -> None:
    """İki kez yansıtmak orijinali verir — mesafe korumasının aritmetik kanıtı."""
    original = long_setup()
    twice = reflect(reflect(original))
    assert twice.direction == original.direction
    assert twice.stop_price == pytest.approx(original.stop_price, rel=1e-12)
    assert twice.target_price == pytest.approx(original.target_price, rel=1e-12)


def test_reflection_does_not_touch_the_arms_observation() -> None:
    """`arm` ve `detail` kolun o barda GERÇEKTEN gördüğüdür; yansıtılan şey pozisyonun yönü."""
    original = long_setup()
    flipped = reflect(original)
    assert flipped.arm == original.arm
    assert flipped.detail == original.detail
    assert flipped.symbol == original.symbol


# --------------------------------------------------------------------------- #
# (2) S1a: aynı kurulum, aynı stop mesafesi (kurulum düzeyi — dolumdan BAĞIMSIZ)
# --------------------------------------------------------------------------- #
def test_the_control_trades_the_same_setup_at_the_same_stop_distance(
    monkeypatch, control, twin, data
) -> None:
    """**S1a**: aynı barda aynı sembol, BİREBİR aynı stop mesafesi (§6i > 7 > DÜZELTME-1).

    Ölçüt KURULUM düzeyindedir ve bu bilinçlidir: yansıtmanın mesafeyi koruduğu bir
    aritmetik iddiadır, dolum kümesine bağlı değildir. Defterdeki `avg_stop_distance_pct`
    farkı (S1b) bunun sonucu DEĞİLDİR ve buradan türetilemez — `max_positions` (5) ve
    `max_short_positions` (3) `scalp_patient`in defterinde zaten bağlıyor, yani iki
    modelin dolum kümeleri yönden bağımsız olarak da ayrışır. S1b bu yüzden %1 değil
    **%10** toleransla ve yalnızca DEFTERDEN okunur.
    """
    _install(monkeypatch)
    mine = control.generate_signals(data)
    theirs = twin.generate_signals(data)

    assert len(mine) == len(theirs) == 1
    assert mine[0].symbol == theirs[0].symbol, "eşleştirme bozuldu: farklı sembol seçildi"

    entry = float(data.ohlcv[mine[0].symbol]["close"].iloc[-1])
    assert abs(entry - mine[0].stop_price) == pytest.approx(
        abs(entry - theirs[0].stop_price), rel=1e-12
    )
    assert abs(mine[0].take_profits[0].price - entry) == pytest.approx(
        abs(theirs[0].take_profits[0].price - entry), rel=1e-12
    )


def test_a_flipped_signal_still_passes_the_validation_gate(
    monkeypatch, control, layer, data
) -> None:
    """Yazı-tura ne çıkarsa çıksın sinyal `core/validate.py` kapısından geçmeli."""
    _install(monkeypatch)
    signals = control.generate_signals(data)
    assert len(signals) == 1
    validate_signal(
        signals[0],
        entry_price=float(data.ohlcv[signals[0].symbol]["close"].iloc[-1]),
        allowed_directions=control.allowed_directions,
        symbol_universe=list(layer.symbols or []),
        is_benchmark=control.is_benchmark,
        is_replica=control.is_replica,
    )


# --------------------------------------------------------------------------- #
# (3) Akış ayrımı
# --------------------------------------------------------------------------- #
def test_forced_same_reproduces_the_twin_signal_for_signal(
    monkeypatch, control, twin, data
) -> None:
    """Yazı-tura sabit "aynı" ise iki model BİREBİR aynı sinyali üretir.

    Bu, kol/sembol çekilişinin PAYLAŞILDIĞININ kanıtıdır: paylaşılmasa iki model aynı
    barda farklı kurulumlar oynardı ve `coin=same` bile sinyalleri hizalamazdı.
    """
    _install(monkeypatch)
    monkeypatch.setattr(
        ScalpCoinflip, "direction_policy",
        lambda self, setup, *, market: (
            self._flips.__setitem__(setup.symbol, SAME) or setup
        ),
    )
    mine = control.generate_signals(data)
    theirs = twin.generate_signals(data)

    assert len(mine) == len(theirs) == 1
    assert (mine[0].symbol, mine[0].direction) == (theirs[0].symbol, theirs[0].direction)
    assert mine[0].stop_price == pytest.approx(theirs[0].stop_price, rel=1e-12)
    assert mine[0].take_profits[0].price == pytest.approx(
        theirs[0].take_profits[0].price, rel=1e-12
    )
    # Kuyruğun geri kalanı da aynı: ayrışan tek şey `coin=` etiketidir.
    assert parse_tag(mine[0].reason, "arm") == parse_tag(theirs[0].reason, "arm")
    assert mine[0].reason.replace(f" | coin={SAME}", "") == theirs[0].reason


def test_the_draw_stream_is_shared_with_the_twin(control, twin, data) -> None:
    """`rng_identity` MİRAS ALINIR: ölçülmeyen eksende çekiliş paylaşılır."""
    assert control.rng_identity == twin.rng_identity == "scalp_fixed"
    assert control._round_rng(data).random() == twin._round_rng(data).random()


def test_the_coin_stream_is_separate_from_the_draw_stream(control, data) -> None:
    """Tek akış olsaydı her yazı-tura kol/sembol çekilişini bir adım kaydırırdı."""
    draw = control._round_rng(data).random()
    coin = control._coin_rng(data, SYMBOLS[0]).random()
    assert draw != coin


def test_the_coin_stream_forks_per_symbol(control, data) -> None:
    """Aynı barda iki sembolün yazı-turası bağımsız olmalı; yoksa bar başına tek çekiliş."""
    assert (
        control._coin_rng(data, "BTC-USDT-SWAP").random()
        != control._coin_rng(data, "ETH-USDT-SWAP").random()
    )


def test_the_coin_is_deterministic_for_the_same_bar_and_symbol(control, data) -> None:
    """Tekrarlanabilirlik: aynı `as_of` ile yeniden koşulan tur aynı yönü verir."""
    assert (
        control._coin_rng(data, SYMBOLS[0]).random()
        == control._coin_rng(data, SYMBOLS[0]).random()
    )


def test_the_coin_is_fair(control) -> None:
    """S2'nin kod tarafı: 10.000 çekilişte "ters" oranı 0.5 ± 0.02."""
    trials = 10_000
    flips = sum(
        random.Random(f"{control._seed}:bar{index}:{control.name}:{SYMBOLS[0]}").random() < 0.5
        for index in range(trials)
    )
    assert 0.48 < flips / trials < 0.52, flips / trials


def test_the_draw_is_unchanged_by_adding_the_coin(monkeypatch, control, twin, data) -> None:
    """Kol/sembol çekilişi yazı-tura EKLENMEDEN önceki hâliyle aynı.

    Ölçüt `scalp_patient`in seçimidir: o model yazı-tura taşımaz, yani kontrolün onunla
    aynı sembolü seçmesi çekilişin kaymadığının kanıtıdır. `direction_policy` seçimden
    SONRA çağrıldığı için seçime dokunamaz.
    """
    _install(monkeypatch)
    for _ in range(5):
        assert (
            control.generate_signals(data)[0].symbol
            == twin.generate_signals(data)[0].symbol
        )


# --------------------------------------------------------------------------- #
# Denetim izi
# --------------------------------------------------------------------------- #
def test_the_reason_tail_records_the_coin_result(monkeypatch, control, data) -> None:
    _install(monkeypatch)
    signals = control.generate_signals(data)
    coin = parse_tag(signals[0].reason, "coin")
    assert coin in (SAME, FLIPPED)
    assert ScalpCoinflip.coin_of(signals[0].reason) == coin
    # Kol kırılımının okuduğu etiket KAYBOLMAZ.
    assert parse_tag(signals[0].reason, "arm") == "rsi2_reversal"
    assert find_tag(signals[0].reason, "post_r") is not None


def test_the_coin_tag_matches_the_actual_direction(monkeypatch, control, data) -> None:
    """Etiket ile geometri ayrışamaz: `flipped` ise yön kolunkinin TERSİ olmalı."""
    _install(monkeypatch)
    signals = control.generate_signals(data)
    coin = parse_tag(signals[0].reason, "coin")
    # Sahte kol her kurulumu `long` üretir.
    assert signals[0].direction == ("short" if coin == FLIPPED else "long")


def test_coin_of_returns_none_when_the_tag_is_missing() -> None:
    assert ScalpCoinflip.coin_of("x | arm=rsi2_reversal") is None


def test_both_outcomes_occur_across_bars(monkeypatch, control) -> None:
    """Yazı-tura gerçekten çekiliyor: iki sonuç da barlar boyunca görülmeli."""
    _install(monkeypatch)
    closes = [100.0 + (i % 5) * 0.4 for i in range(120)]
    seen = set()
    for offset in range(24):
        start = DAY + pd.Timedelta(minutes=15 * offset)
        snapshot = market({
            symbol: frame(closes, spread=0.3, start=start, freq="15min")
            for symbol in SYMBOLS
        })
        signals = control.generate_signals(snapshot)
        seen.add(parse_tag(signals[0].reason, "coin"))
    assert seen == {SAME, FLIPPED}


# --------------------------------------------------------------------------- #
# (4) Statü ve kadro
# --------------------------------------------------------------------------- #
def test_the_control_is_a_competitor_not_a_benchmark_or_replica(control) -> None:
    """`random_ctrl`ün statüsüyle birebir: kontrol aynı sütunda yarışmalı."""
    assert control.is_benchmark is False
    assert control.is_replica is False
    assert control.limits is None
    assert control.is_meta is False


def test_the_control_runs_in_the_scalp_layer_and_is_its_control(layer) -> None:
    """C-2 bir FARKA dayanır: farkın öteki tarafı ancak canlı kâğıt defterinde birikir."""
    assert "scalp_coinflip" in REGISTRY
    assert "scalp_coinflip" in layer.models
    assert get_setting(layer.config, "acceptance.control_model") == "scalp_coinflip"


def test_the_control_is_not_live_in_any_other_layer() -> None:
    config = load_config()
    for name in ("base", "ema", "xsec"):
        assert "scalp_coinflip" not in resolve_layer(config, name).config["models"]


def test_the_wave_run_does_not_inherit_this_control() -> None:
    """§6h > EK-1: `scalp_coinflip` WAVE'in kontrolü DEĞİLDİR (farklı stop geometrisi).

    Katmanın varsayılanı artık `scalp_coinflip`tir; `scripts/backtest_wave.py` kontrolü
    açıkça geçirmeseydi wave koşusu sessizce yanlış zemine karşı ölçülürdü. Bu test tam
    olarak EK-1'in öngördüğü durumun gerçekleştiğini ve korumanın tuttuğunu söyler.
    """
    from scripts.backtest_wave import CONTROL

    assert CONTROL == "wave_coinflip"


def test_the_control_inherits_every_rule_except_direction(control, twin) -> None:
    """Ayrışan TEK şey yön: her şey MİRAS ALINIR, kopyalanmaz."""
    assert control.time_stop_key == twin.time_stop_key
    assert control._params == twin._params
    assert control._min_stop_pct == twin._min_stop_pct
    assert control._min_reward_risk == twin._min_reward_risk
    assert control._time_stop.bars == twin._time_stop.bars
    assert control.exit_management is twin.exit_management
    assert control.allowed_directions == twin.allowed_directions
    assert type(control).choose_arm is type(twin).choose_arm

    # `direction_policy` ve denetim izi DIŞINDA hiçbir davranış override edilmemiş olmalı.
    overridden = {
        name for name, value in vars(ScalpCoinflip).items()
        if callable(value) and not name.startswith("__")
    }
    assert overridden <= {"direction_policy", "_coin_rng", "last_flips", "_signal", "coin_of"}


def test_the_control_does_not_learn_from_any_ledger(control) -> None:
    """Kontrol geçmişe BAKMAZ: `observe_closed_trades` uygulanmaz (kural 16).

    `scalp_fixed`in aynı sözü — uyarlanabilir bir kontrol, C-2 farkını yönün değil
    öğrenmenin ölçüsü yapardı.
    """
    from strategies.base import Strategy

    assert type(control).observe_closed_trades is Strategy.observe_closed_trades


# --------------------------------------------------------------------------- #
# Ekin ASIL SONUCU: katmanda `edge` artık bedava geçilmiyor
# --------------------------------------------------------------------------- #
def _scalp_flags(metrics: list[Any], control: str) -> dict[str, Any]:
    """Katmanın kendi eşikleriyle kabul bayrakları — ikinci bir eşik tablosu YOK."""
    from core.metrics import acceptance_flags

    settings = resolve_layer(load_config(), "scalp").config
    return {
        item.model: item
        for item in acceptance_flags(
            metrics,
            min_trades=int(get_setting(settings, "acceptance.min_trades")),
            stop_band_ratio=float(get_setting(settings, "acceptance.stop_band_ratio")),
            control_model=control,
            edge_margin_r=float(get_setting(settings, "acceptance.edge_margin_r")),
            control_min_trades=int(get_setting(settings, "acceptance.control_min_trades")),
        )
    }


def _winner(model: str, *, n: int = 40):
    from tests.test_metrics import _competitor, _trade

    return _competitor(
        model,
        avg_r_trades=[
            _trade(pnl=50.0, risk=100.0, entry_price=100.0, stop_price=97.0,
                   closed_at=f"2026-01-{index + 1:02d}T00:00:00+00:00")
            for index in range(n)
        ],
        final=12_000.0,
    )


def _empty(model: str):
    from tests.test_metrics import _competitor

    return _competitor(model, avg_r_trades=[], final=10_000.0)


def test_without_a_control_the_edge_gate_was_effectively_avg_r_positive() -> None:
    """Ekin GEREKÇESİ: kontrolsüz katmanda `edge` kendiliğinden geçiyordu.

    Bu test düzeltilen davranışı KAYDEDER (kontrolü kümeden çıkararak eski hâli kurar);
    yeşil kalması bir gerileme değil, ekin neyi kapattığının kanıtıdır.
    """
    flags = _scalp_flags([_winner("scalp_patient")], control="scalp_coinflip")
    assert flags["scalp_patient"].sample
    assert flags["scalp_patient"].edge, (
        "kontrol kümede hiç yokken koşul düşüyordu — ekin kapattığı boşluk budur"
    )


def test_a_control_with_no_trades_yet_blocks_the_edge_gate() -> None:
    """Kontrol kadroda ama HENÜZ BİRİKMEMİŞ: `edge` değerlendirilemez, `passed` FALSE.

    Beklenen ve İSTENEN davranış budur (docs/backtest.md > 6i > 4): `scalp_patient`in
    örneklem kapısını geçtiği anda görünecek `passed: true` yanlış olurdu.
    """
    flags = _scalp_flags(
        [_winner("scalp_patient"), _empty("scalp_coinflip")], control="scalp_coinflip"
    )
    row = flags["scalp_patient"]
    assert row.sample, "model kendi örneklem kapısını geçmiş olmalı"
    assert not row.edge
    assert not row.passed
    assert row.control_trades == 0
    assert row.control_min_trades == 30


def test_once_the_control_is_measured_the_gate_reads_the_difference() -> None:
    """Kontrol kapısını geçince C-2 gerçekten ÖLÇÜLÜR: marj ve işaret okunur."""
    from tests.test_metrics import _competitor, _trade

    losing_control = _competitor(
        "scalp_coinflip",
        avg_r_trades=[
            _trade(pnl=-12.0, risk=100.0, entry_price=100.0, stop_price=97.0,
                   closed_at=f"2026-02-{index + 1:02d}T00:00:00+00:00")
            for index in range(30)
        ],
        final=9_700.0,
    )
    flags = _scalp_flags(
        [_winner("scalp_patient"), losing_control], control="scalp_coinflip"
    )
    row = flags["scalp_patient"]
    assert row.control_trades == 30
    assert row.avg_r - row.control_avg_r >= row.edge_margin_r
    # Kontrol kendini geçemez: kendisiyle farkı 0, gereken marj 0.15R.
    assert not flags["scalp_coinflip"].edge

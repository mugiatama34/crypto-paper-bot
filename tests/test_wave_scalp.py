"""Model 21 (`wave_scalp`): statü, bandit sırası, sinyal alanları ve denetim izi.

Sinyal KURALININ kendisi `tests/test_wave_clone_signal.py`dedir (parite kapısı dâhil);
burada ölçülen şey modelin o kuralı EVİN koşullarına nasıl bağladığıdır — ve ön-kayıtın
(docs/backtest.md > 6h) hangi cümlesinin hangi satırda karşılığı olduğudur.

**Statü testleri gevşetilemez.** "Yarışmacı, kopya değil" bir etiket değil bir ölçüm
koşuludur: kopya olsaydı `cost_per_r` ve `avg_stop_distance_pct` kolonları `nan` olur ve
Aşama 2'nin iyileştirme turu okunacağı kolonları kaybederdi (§6h > 1).
"""

from __future__ import annotations

import logging
import random

import pandas as pd
import pytest

from core.config import load_config
from core.layers import resolve_layer
from core.tags import find_tag, parse_tag
from core.validate import validate_signal
from strategies.base import ClosedTrade
from strategies.registry import build
from strategies.wave import clone_signal
from strategies.wave_scalp import ComboStats, WaveScalp, combo_key
from tests.test_wave_clone_signal import buy_path, wave_frame
from tests.helpers_market import market

SYMBOL = "BTC-USDT-SWAP"
OTHER = "ETH-USDT-SWAP"


@pytest.fixture()
def layer():
    return resolve_layer(load_config(), "scalp")


@pytest.fixture()
def layer_config(layer):
    return layer.config


@pytest.fixture()
def model(layer_config) -> WaveScalp:
    return build("wave_scalp", config=layer_config)


def closed(
    symbol: str, combo: str, r: float | None, *, day: int = 1, extra: str = ""
) -> ClosedTrade:
    stamp = pd.Timestamp("2026-01-01", tz="UTC") + pd.Timedelta(days=day)
    return ClosedTrade(
        symbol=symbol,
        direction="long",
        opened_at=stamp,
        closed_at=stamp + pd.Timedelta(hours=1),
        r_multiple=r,
        signal_reason=f"x | arm={clone_signal.ARM_NAME} | combo={combo}{extra}",
        exit_reason="tp",
    )


# --------------------------------------------------------------------------- #
# Statü ve kurulum (§6h > 1, 3(g), 4)
# --------------------------------------------------------------------------- #
def test_model_is_a_competitor_not_a_replica_or_benchmark(model: WaveScalp) -> None:
    assert model.is_replica is False, "kopya olsaydı maliyet ölçeği kolonları nan olurdu"
    assert model.is_benchmark is False
    assert model.limits is None, "ModelLimits yalnızca kopyalara açıktır (kural 15b)"


def test_model_is_registered_but_not_in_any_live_layer_models_list() -> None:
    """Kod ölçülmeden yarışmaz: `REGISTRY`de var, katmanın `models` listesinde yok."""
    from strategies.registry import REGISTRY

    config = load_config()
    assert "wave_scalp" in REGISTRY
    for name in ("base", "scalp", "ema", "xsec"):
        models = resolve_layer(config, name).config["models"]
        assert "wave_scalp" not in models, f"{name} katmanına sessizce eklenmiş"


def test_universe_is_the_sources_list_minus_ton(model: WaveScalp, layer) -> None:
    """Evren kaynağın `POPULAR_COINS`i (fa888b7) EKSİ TON'dur — 11 sembol (§6h > 4)."""
    assert model._universe == [
        "BTC-USDT-SWAP", "ETH-USDT-SWAP", "SOL-USDT-SWAP", "XRP-USDT-SWAP",
        "BNB-USDT-SWAP", "DOGE-USDT-SWAP", "ADA-USDT-SWAP", "AVAX-USDT-SWAP",
        "LINK-USDT-SWAP", "ETHFI-USDT-SWAP", "NEAR-USDT-SWAP",
    ]
    assert "TON-USDT-SWAP" not in model._universe
    # Katmanın evrenine DOKUNULMAZ: küme onun alt kümesi olmalı, yoksa core/validate.py
    # her sinyali "evren dışı" diye reddederdi.
    assert layer.symbols is not None
    assert set(model._universe) <= set(layer.symbols)


def test_universe_is_not_shared_with_the_vwap_clone_block() -> None:
    """İki kaynak profili bugün AYNI evreni taşımıyor; blok paylaşımı yanlış olurdu."""
    config = load_config()
    wave = set(config["wave"]["clone"]["universe"])
    vwap = set(config["vwap"]["clone"]["universe"])
    assert wave != vwap
    assert "PENGU-USDT-SWAP" in vwap and "PENGU-USDT-SWAP" not in wave


def test_atr_period_comes_from_the_projects_single_definition(model: WaveScalp) -> None:
    """Modele özel ATR periyodu YOKTUR: motorun stop tavanı ile aynı sayı okunur."""
    config = load_config()
    assert model._atr_period == config["trailing"]["atr_period"] == 14
    assert "atr_period" not in config["wave"]["clone"]
    assert "atr_smoothing" not in config["wave"]["clone"]


def test_setup_rejects_a_window_too_short_to_scale_the_threshold(layer_config) -> None:
    """ATR'yi hesaplayamayan pencere KURULUMDA patlar, her turu sessizce geçmez."""
    broken = dict(layer_config)
    broken["wave"] = {"clone": dict(layer_config["wave"]["clone"], window_bars=10)}
    with pytest.raises(ValueError, match="window_bars"):
        WaveScalp(config=broken)


# --------------------------------------------------------------------------- #
# Izgara (§6h > 2)
# --------------------------------------------------------------------------- #
def test_grid_is_twelve_cells_with_a_fixed_sl_mult(model: WaveScalp) -> None:
    assert len(model._combos) == 12
    assert {c.params.sl_mult for c in model._combos} == {0.15}
    assert sorted({c.deviation_pct for c in model._combos}) == [0.8, 1.2, 1.8, 2.5]
    assert sorted({c.tp_mult for c in model._combos}) == [1.272, 1.618, 2.0]
    assert {c.params.retrace_min for c in model._combos} == {0.236}
    assert {c.params.retrace_max for c in model._combos} == {0.886}


def test_combo_keys_are_stable_and_unique(model: WaveScalp) -> None:
    """Anahtar DEĞERDEN türer; biçimlendirme kararlı ve çakışmasız olmalı.

    Bu, `vwap_clone`un indeks tabanlı anahtarından ayrılmanın bedelidir ve burada
    ödenir: iki farklı çarpan aynı metne inseydi posterior iki kolu tek hücrede toplardı.
    """
    keys = [c.key for c in model._combos]
    assert len(set(keys)) == 12
    assert keys[0] == "dev0.8_tp1.272"
    assert combo_key(1.2, 1.618) == "dev1.2_tp1.618"
    assert combo_key(2.5, 2.0) == "dev2.5_tp2"
    # Etiket `core/tags.py`nin değer deseninden geçmeli (boşluk ve "|" yasak).
    for key in keys:
        assert " " not in key and "|" not in key


def test_colliding_multipliers_fail_at_setup(layer_config) -> None:
    broken = dict(layer_config)
    broken["wave"] = {
        "clone": dict(layer_config["wave"]["clone"], deviations=[1.0, 1.0000000001])
    }
    with pytest.raises(ValueError, match="çakışan kombinasyon anahtarı"):
        WaveScalp(config=broken)


@pytest.mark.parametrize(
    "override,message",
    [
        ({"deviations": []}, "boş olamaz"),
        ({"deviations": [0.0, 1.2]}, "deviations pozitif"),
        ({"tp_mults": [-1.0]}, "tp_mults pozitif"),
        ({"sl_mult": 0.0}, "sl_mult pozitif"),
        ({"retrace_min": 0.9, "retrace_max": 0.886}, "retrace aralığı"),
        ({"retrace_max": 1.5}, "retrace aralığı"),
        ({"min_deviation_pct": 0.0}, "min_deviation_pct pozitif"),
        ({"bandit": {"epsilon": 1.5, "min_symbol_samples": 3}}, "epsilon"),
    ],
)
def test_bad_config_fails_loudly_at_setup(layer_config, override, message) -> None:
    broken = dict(layer_config)
    broken["wave"] = {"clone": dict(layer_config["wave"]["clone"], **override)}
    with pytest.raises(ValueError, match=message):
        WaveScalp(config=broken)


# --------------------------------------------------------------------------- #
# Bandit sırası (kaynak: learner.select) — §6h > 2
# --------------------------------------------------------------------------- #
def test_unexplored_combination_wins_before_exploration_or_exploitation(
    model: WaveScalp,
) -> None:
    """1. adım: o SEMBOLDE denenmemiş kombinasyon varsa ondan biri çekilir."""
    tried = [c.key for c in model._combos[:11]]
    model.observe_closed_trades(
        [closed(SYMBOL, key, 5.0, day=i) for i, key in enumerate(tried)]
    )
    missing = model._combos[11].key

    for seed in range(20):
        combo, _, pick = model.choose_combo(SYMBOL, rng=random.Random(seed))
        assert (combo.key, pick) == (missing, "unexplored")


def test_exploration_share_is_the_sources_epsilon(model: WaveScalp) -> None:
    """2. adım: ısınma bittikten sonra çekilişlerin ~%25'i keşif olmalı."""
    model.observe_closed_trades(
        [closed(SYMBOL, c.key, 1.0 if c.key.endswith("tp2") else -1.0, day=i)
         for i, c in enumerate(model._combos)]
    )
    rng = random.Random(20240217)
    picks = [model.choose_combo(SYMBOL, rng=rng)[2] for _ in range(4000)]
    assert set(picks) == {"explore", "exploit"}
    share = picks.count("explore") / len(picks)
    assert 0.22 < share < 0.28, share


def test_exploitation_picks_the_highest_mean_r(model: WaveScalp) -> None:
    """3. adım: keşif dalı kapalıyken en yüksek ortalama R seçilir."""
    best = model._combos[7].key
    trades = []
    for index, combo in enumerate(model._combos):
        reward = 3.0 if combo.key == best else -0.5
        trades.append(closed(SYMBOL, combo.key, reward, day=index))
    model.observe_closed_trades(trades)

    class NoExplore(random.Random):
        def random(self) -> float:  # keşif eşiğini asla geçmeyen çekiliş
            return 1.0

    combo, stats, pick = model.choose_combo(SYMBOL, rng=NoExplore(0))
    assert (combo.key, pick) == (best, "exploit")
    assert stats.mean_r == pytest.approx(3.0)


def test_symbol_statistics_fall_back_to_global_below_min_samples(model: WaveScalp) -> None:
    """Geri düşüş KOMBİNASYON bazındadır: ölçülmüş hücre genele feda edilmez."""
    target = model._combos[3].key
    trades = [closed(OTHER, target, 2.0, day=i) for i in range(5)]           # genel: +2.0
    trades += [closed(SYMBOL, target, -1.0, day=10)]                          # sembol: n=1
    trades += [closed(SYMBOL, c.key, 0.0, day=20 + i)
               for i, c in enumerate(model._combos) if c.key != target]
    model.observe_closed_trades(trades)

    resolved = model._stats_for_choice(SYMBOL)
    # n=1 < min_symbol_samples(3) -> genele düşer
    assert resolved[target].mean_r == pytest.approx((2.0 * 5 + -1.0) / 6)

    model.observe_closed_trades(trades + [closed(SYMBOL, target, -1.0, day=30 + i)
                                          for i in range(2)])
    assert model._stats_for_choice(SYMBOL)[target].mean_r == pytest.approx(-1.0)


def test_unmeasured_combo_reports_nan_not_zero(model: WaveScalp) -> None:
    """`nan` = ölçülmedi. `0.0` "ölçtük, tam sıfır çıktı" demektir; ikisi karıştırılamaz."""
    stats = model._stats_for_choice(SYMBOL)
    assert all(s.trades == 0 and s.mean_r != s.mean_r for s in stats.values())
    assert ComboStats(trades=0, mean_r=float("nan")).measured is False


def test_unknown_and_untagged_ledger_rows_are_logged_not_silently_learned(
    model: WaveScalp, caplog
) -> None:
    good = model._combos[0].key
    rows = [
        closed(SYMBOL, good, 1.0, day=1),
        closed(SYMBOL, "dev9.9_tp9.9", 99.0, day=2),      # tanınmayan kombinasyon
        ClosedTrade(
            symbol=SYMBOL, direction="long",
            opened_at=pd.Timestamp("2026-01-05", tz="UTC"),
            closed_at=pd.Timestamp("2026-01-05 01:00", tz="UTC"),
            r_multiple=42.0, signal_reason="etiketsiz", exit_reason="tp",
        ),
    ]
    with caplog.at_level(logging.WARNING):
        model.observe_closed_trades(rows)
    assert "tanınmayan kombinasyon" in caplog.text
    assert "combo etiketi yok" in caplog.text
    assert model._global[good].trades == 1
    assert model._global[good].mean_r == pytest.approx(1.0)


def test_trades_without_an_r_multiple_do_not_enter_the_posterior(model: WaveScalp) -> None:
    key = model._combos[0].key
    model.observe_closed_trades([closed(SYMBOL, key, None, day=1), closed(SYMBOL, key, 2.0, day=2)])
    assert model._global[key].trades == 1


def test_draw_is_not_shared_with_the_vwap_clone(model: WaveScalp, layer_config) -> None:
    """Çekiliş kimliği model ADINI taşır: iki kopya aynı barda aynı diziyi çekmez."""
    clone = build("vwap_clone", config=layer_config)
    snapshot = market({SYMBOL: wave_frame(buy_path())})
    assert model._round_rng(snapshot).random() != clone._round_rng(snapshot).random()

    # Aynı bar iki kez koşulursa aynı dizi gelmeli (tekrarlanabilirlik).
    assert model._round_rng(snapshot).random() == model._round_rng(snapshot).random()


# --------------------------------------------------------------------------- #
# Sinyal alanları (§6h > 1, 3(f))
# --------------------------------------------------------------------------- #
def signals_for(model: WaveScalp, frames: dict) -> list:
    return model.generate_signals(market(frames))


def test_signal_uses_house_sizing_and_carries_a_stop(model: WaveScalp) -> None:
    signals = signals_for(model, {SYMBOL: wave_frame(buy_path())})
    assert len(signals) == 1
    signal = signals[0]
    assert signal.sizing == "risk", "kaynağın sabit teminatı KOPYALANMAZ (§6h > 3f)"
    assert signal.notional_fraction is None
    assert signal.stop_price is not None
    assert signal.stop_price != signal.entry_type  # sanity: stop gerçekten dolu
    assert len(signal.take_profits) == 1
    assert signal.take_profits[0].fraction == 1.0


def test_signal_requests_the_shared_three_stage_exit_management(model: WaveScalp) -> None:
    """Çıkış yönetimi YENİDEN YAZILMAZ: tek kopyadan gelen alanlar birebir bildirilir."""
    config = load_config()["exit_management"]
    signal = signals_for(model, {SYMBOL: wave_frame(buy_path())})[0]
    assert signal.breakeven_at_r == config["breakeven_at_r"] == 1.0
    assert signal.partial_tp is not None
    assert signal.partial_tp.r == config["partial_tp"]["r"] == 1.5
    assert signal.partial_tp.fraction == config["partial_tp"]["fraction"] == 0.5
    assert signal.trail_giveback_pct == config["trail_giveback_pct"] == 0.5
    assert signal.trailing_atr is None, "trailing_atr ile geri verme birlikte OLAMAZ"


def test_signal_passes_the_engine_validation_gate(model: WaveScalp, layer) -> None:
    frame = wave_frame(buy_path())
    signal = signals_for(model, {SYMBOL: frame})[0]
    validate_signal(
        signal,
        entry_price=float(frame["close"].iloc[-1]),
        allowed_directions=model.allowed_directions,
        symbol_universe=list(layer.symbols or []),
        is_benchmark=model.is_benchmark,
        is_replica=model.is_replica,
    )


def test_reason_tail_carries_the_preregistered_audit_tags(model: WaveScalp) -> None:
    """`arm=wave3_src | combo=… | retrace=… | wave1=…` — ön-kayıtın istediği denetim izi."""
    signal = signals_for(model, {SYMBOL: wave_frame(buy_path())})[0]
    assert parse_tag(signal.reason, "arm") == "wave3_src"
    assert parse_tag(signal.reason, "combo").startswith("dev")
    assert 0.236 <= float(parse_tag(signal.reason, "retrace")) <= 0.886
    assert float(parse_tag(signal.reason, "wave1")) > 0.0
    assert find_tag(signal.reason, "pick") in ("unexplored", "explore", "exploit")
    assert find_tag(signal.reason, "combo_n") == "0"


def test_arm_tag_is_not_shared_with_any_house_arm(model: WaveScalp) -> None:
    """Tek bir ad, iki farklı kural kümesini aynı kolmuş gibi gösterirdi."""
    from strategies.scalp import arms
    from strategies.vwap import clone_signal as vwap_clone_signal
    from strategies.vwap import signal as vwap_house

    others = {vwap_clone_signal.ARM_NAME, vwap_house.ARM_NAME}
    others |= {name for name in dir(arms) if isinstance(getattr(arms, name), str)}
    assert clone_signal.ARM_NAME not in others


# --------------------------------------------------------------------------- #
# Tarama davranışı (§6h > 9) ve denetim izi (kural 15)
# --------------------------------------------------------------------------- #
def test_scan_follows_the_universe_order_and_is_not_one_signal_per_bar(
    model: WaveScalp,
) -> None:
    """Kaynak evreni SIRAYLA tarar ve barda birden çok sinyal üretebilir."""
    frames = {symbol: wave_frame(buy_path()) for symbol in model._universe[:4]}
    signals = signals_for(model, frames)
    assert len(signals) == 4, "barda tek sinyal kuralı bu modele uygulanmaz"
    assert [s.symbol for s in signals] == model._universe[:4]


def test_emitted_list_is_capped_at_the_root_position_quota(model: WaveScalp) -> None:
    """Kırpma bir KOTA UYGULAMASI değil, üst sınırdır — gerçek kotayı portfolio uygular."""
    frames = {symbol: wave_frame(buy_path()) for symbol in model._universe}
    signals = signals_for(model, frames)
    assert len(signals) == model._max_positions == 5
    assert [s.symbol for s in signals] == model._universe[:5], "kırpma SONDAN yapılmalı"


def test_survey_counts_every_scanned_symbol_exactly_once(model: WaveScalp) -> None:
    """Σcounts = taranan sembol. Evrenin TAMAMI taranır; çerçevesi olmayan `bar_yok` alır.

    İki çerçeve AYNI uzunlukta kurulur: `as_of` referans çerçevenin son barıdır ve farklı
    uzunluktaki bir çerçeve o barı taşımadığı için `bar_yok` alırdı — testin ölçmek
    istediği ayrım (kurulum var / kurulum yok) o zaman kaybolurdu.
    """
    bars = len(buy_path())
    frames = {
        model._universe[0]: wave_frame(buy_path()),
        model._universe[1]: wave_frame([100.0 + i for i in range(bars)]),  # kurulum yok
    }
    signals = signals_for(model, frames)
    survey = model.take_survey()
    assert survey is not None
    assert sum(survey.values()) == len(model._universe)
    assert survey[clone_signal.SETUP] == len(signals) == 1
    assert survey[clone_signal.NO_BAR] == len(model._universe) - 2
    assert survey[clone_signal.FEW_PIVOTS] == 1


def test_survey_is_none_before_the_first_scan(model: WaveScalp) -> None:
    assert model.take_survey() is None


def test_model_has_no_time_stop(model: WaveScalp) -> None:
    """Zaman stop'u kaynakta YOKTUR: `manage_positions` hiçbir çıkış üretmez (§6h > 3h).

    Bunun bedeli ölçümdedir ve ön-kayıtta yazılıdır: embargo varsayılamaz, dönem A'dan
    ÖLÇÜLÜR.
    """
    frame = wave_frame(buy_path())
    assert model.manage_positions(market({SYMBOL: frame}), []) == []
    assert type(model).manage_positions is WaveScalp.__mro__[1].manage_positions


def test_scan_is_deterministic_for_the_same_bar(model: WaveScalp) -> None:
    frames = {symbol: wave_frame(buy_path()) for symbol in model._universe[:3]}
    first = [(s.symbol, s.stop_price, find_tag(s.reason, "combo")) for s in signals_for(model, frames)]
    second = [(s.symbol, s.stop_price, find_tag(s.reason, "combo")) for s in signals_for(model, frames)]
    assert first == second

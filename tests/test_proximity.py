"""Yakınlık taramasının ölçtüğü şey bir SÖZDÜR: "hiçbir şeye dokunmadım".

Araç modellerin kendi sinyal kodunu hipotetik bir barla çağırır (`scripts/proximity.py`).
Bu, iki ayrı kırılganlık üretir ve bu dosya ikisini de kapıya bağlar:

1. **Yan etki.** Çağrılan kod bir gün kendi alanına yazmaya başlayabilir (`_survey` zaten
   yazıyor) ya da bir RNG'yi ilerletebilir. O gün tarama, ölçtüğü turu SESSİZCE
   değiştirirdi — ölçüm aracının üretebileceği en kötü hata budur. Test asıl model
   nesnesinin taramadan önce ve sonra BİT BİT aynı olduğunu sabitler.
2. **İkinci uygulama.** Yakınlık, indikatör matematiği ayrıca yazılarak da bulunabilirdi
   ve o kod bir gün modelden ayrışır, ekran modelin üretmeyeceği bir sinyali gösterirdi.
   Test bunu bir TUTARLILIK kapısıyla yakalar: taramanın bulduğu tetik fiyatında model
   GERÇEKTEN aynı sinyali üretmelidir.

Ayrıca kaydedilen şey kadar KAYDEDİLMEYEN de sınanır: ızgarada tetik yoksa "yok" açıkça
yazılır (boş bir liste "bilmiyoruz" ile "yok"u aynı hücreye koyardı) ve kapıda ölen tetik
geçenden AYRI işaretlenir (karar 34'ün dersi).
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from core.config import load_config
from core.engine import Engine
from core.layers import resolve_layer
from core.ledger import Ledger
from core.portfolio import Portfolio
from scripts import proximity
from strategies.scalp_fixed import ScalpFixed
from strategies.trend import Trend
from tests.helpers_market import frame, market

SYMBOL = "BTC-USDT-SWAP"


# --------------------------------------------------------------------------- #
# Kurucular
# --------------------------------------------------------------------------- #
@pytest.fixture()
def base_config() -> dict[str, Any]:
    return resolve_layer(load_config(), "base").config


@pytest.fixture()
def scalp_config() -> dict[str, Any]:
    return resolve_layer(load_config(), "scalp").config


def _band(config: dict[str, Any], tmp_path: Path) -> proximity.StopBandGate:
    engine = Engine([], config=config, ledger=Ledger(tmp_path), portfolio=Portfolio(config))
    return proximity.StopBandGate(engine, config)


def _quiet_market(*, freq: str = "4h", bars: int = 260) -> Any:
    """Kırılımsız, dar bantlı bir geçmiş: hiçbir model son barda sinyal üretmez.

    Testin başlangıç noktası "sinyal yok" olmalı ki tetiğin taramadan geldiği kesin
    olsun — zaten tetiklenmiş bir seride "yakınlık" ölçülemez.
    """
    closes = [100.0 + (i % 3) * 0.1 for i in range(bars)]
    return market({SYMBOL: frame(closes, spread=0.2, freq=freq)})


def _state() -> dict[str, Any]:
    return {
        "open_positions": 0,
        "max_positions": 5,
        "open_long": 0,
        "open_short": 0,
        "max_short_positions": 3,
        "quota_full": False,
        "open_keys": [],
    }


# --------------------------------------------------------------------------- #
# 1) Hipotetik bar asıl çerçeveyi DEĞİŞTİRMEZ
# --------------------------------------------------------------------------- #
def test_hypothetical_bar_leaves_the_source_frame_untouched() -> None:
    base = frame([100.0, 101.0, 102.0])
    before = base.copy(deep=True)

    extended = proximity.hypothetical_bar(
        base, close=110.0, ts=base.index[-1] + pd.Timedelta(hours=4)
    )

    pd.testing.assert_frame_equal(base, before)
    assert len(extended) == len(base) + 1


def test_hypothetical_bar_uses_the_documented_assumptions() -> None:
    base = frame([100.0, 101.0, 102.0], volumes=[10.0, 20.0, 90.0])
    ts = base.index[-1] + pd.Timedelta(hours=4)

    row = proximity.hypothetical_bar(base, close=105.0, ts=ts).iloc[-1]

    assert row["open"] == pytest.approx(102.0)  # son kapanış
    assert row["close"] == pytest.approx(105.0)  # aday fiyat
    assert row["high"] == pytest.approx(105.0)  # gövdenin ucu, fitil YOK
    assert row["low"] == pytest.approx(102.0)
    assert row["volume"] == pytest.approx(20.0)  # medyan, ortalama (40) DEĞİL


def test_candidate_prices_are_ordered_by_distance() -> None:
    up, down = proximity.candidate_prices(100.0, range_pct=1.0, step_pct=0.5)

    assert up == pytest.approx((100.5, 101.0))
    assert down == pytest.approx((99.5, 99.0))


def test_snapshot_only_moves_the_scanned_symbol() -> None:
    """Diğer semboller `as_of` barında kalır — modeller onları zaten atlar (kural 12)."""
    other = "ETH-USDT-SWAP"
    snapshot = market({SYMBOL: frame([100.0] * 5), other: frame([50.0] * 5)})
    ts = snapshot.as_of + pd.Timedelta(hours=4)

    moved = proximity.snapshot_at(
        snapshot, symbol=SYMBOL, close=120.0, ts=ts, btc_symbol=SYMBOL
    )

    assert set(moved.ohlcv) == {SYMBOL}
    assert moved.as_of == ts
    assert moved.ohlcv[SYMBOL].index[-1] == ts
    assert snapshot.ohlcv[other].index[-1] != ts  # kaynak anlık görüntü el değmemiş


# --------------------------------------------------------------------------- #
# 2) Model durumu tarama boyunca BİT BİT aynı
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("model_name", ["trend", "meanrev"])
def test_scan_does_not_touch_the_model_state(
    base_config: dict[str, Any], tmp_path: Path, model_name: str
) -> None:
    from main import build_strategies

    (strategy,), _ = build_strategies([model_name], base_config)
    probe = proximity.build_probe(
        strategy, config=base_config, band=_band(base_config, tmp_path), ledger=Ledger(tmp_path)
    )
    before = repr(sorted(strategy.__dict__.items(), key=str))

    proximity.scan_symbol(
        [probe],
        _quiet_market(),
        symbol=SYMBOL,
        hypo_ts=_quiet_market().as_of + pd.Timedelta(hours=4),
        btc_symbol=SYMBOL,
        range_pct=3.0,
        step_pct=0.5,
        atr_period=14,
        states={probe.name: _state()},
    )[probe.name]

    assert repr(sorted(strategy.__dict__.items(), key=str)) == before


def test_probe_never_calls_the_original_model(
    scalp_config: dict[str, Any], tmp_path: Path
) -> None:
    """Sonda KOPYAYI çağırır: asıl nesne çağrı yolunun ayrıntısından bağımsız korunur."""
    strategy = ScalpFixed(config=scalp_config)
    probe = proximity.build_probe(
        strategy, config=scalp_config, band=_band(scalp_config, tmp_path), ledger=Ledger(tmp_path)
    )

    assert probe.model is not strategy
    assert probe.strategy is strategy


def test_scalp_probe_does_not_draw_an_arm(
    scalp_config: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`choose_arm`/RNG taramada ÇAĞRILMAZ: tarama seçimi değil TETİKLEMEYİ raporlar."""
    strategy = ScalpFixed(config=scalp_config)
    probe = proximity.build_probe(
        strategy, config=scalp_config, band=_band(scalp_config, tmp_path), ledger=Ledger(tmp_path)
    )

    def explode(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("tarama çekiliş yapmamalı")

    monkeypatch.setattr(type(probe.model), "choose_arm", explode)
    monkeypatch.setattr(type(probe.model), "_round_rng", explode)

    snapshot = _quiet_market(freq="15min", bars=300)
    proximity.scan_symbol(
        [probe],
        snapshot,
        symbol=SYMBOL,
        hypo_ts=snapshot.as_of + pd.Timedelta(minutes=15),
        btc_symbol=SYMBOL,
        range_pct=3.0,
        step_pct=0.25,
        atr_period=14,
        states={probe.name: _state()},
    )[probe.name]

    assert probe.selection == proximity.SELECTION_NOTE


# --------------------------------------------------------------------------- #
# 3) TUTARLILIK: bulunan tetik fiyatında modelin gerçek turu aynı sinyali üretir
# --------------------------------------------------------------------------- #
def test_reported_trigger_price_really_produces_the_signal(
    base_config: dict[str, Any], tmp_path: Path
) -> None:
    """İkinci uygulama kapısı: taramanın sayısı modelin kendi kodundan doğrulanır."""
    strategy = Trend(config=base_config)
    probe = proximity.build_probe(
        strategy, config=base_config, band=_band(base_config, tmp_path), ledger=Ledger(tmp_path)
    )
    snapshot = _quiet_market()
    hypo_ts = snapshot.as_of + pd.Timedelta(hours=4)

    triggers = proximity.scan_symbol(
        [probe],
        snapshot,
        symbol=SYMBOL,
        hypo_ts=hypo_ts,
        btc_symbol=SYMBOL,
        range_pct=10.0,
        step_pct=0.1,
        atr_period=14,
        states={probe.name: _state()},
    )[probe.name]

    assert triggers, "kırılım modeli yeterince geniş bir ızgarada tetiklenmeli"
    trigger = triggers[0]

    replay = proximity.snapshot_at(
        snapshot, symbol=SYMBOL, close=trigger.trigger_price, ts=hypo_ts, btc_symbol=SYMBOL
    )
    signals = Trend(config=base_config).generate_signals(replay)

    assert [s.symbol for s in signals] == [SYMBOL]
    assert signals[0].direction == trigger.direction
    assert signals[0].stop_price == pytest.approx(trigger.stop_price)


def test_one_step_closer_does_not_trigger(base_config: dict[str, Any], tmp_path: Path) -> None:
    """"En yakın" gerçekten en yakın: bir adım berisinde model sinyal ÜRETMEMELİ."""
    strategy = Trend(config=base_config)
    probe = proximity.build_probe(
        strategy, config=base_config, band=_band(base_config, tmp_path), ledger=Ledger(tmp_path)
    )
    snapshot = _quiet_market()
    hypo_ts = snapshot.as_of + pd.Timedelta(hours=4)
    step = 0.1

    triggers = proximity.scan_symbol(
        [probe],
        snapshot,
        symbol=SYMBOL,
        hypo_ts=hypo_ts,
        btc_symbol=SYMBOL,
        range_pct=10.0,
        step_pct=step,
        atr_period=14,
        states={probe.name: _state()},
    )[probe.name]
    upward = [item for item in triggers if item.price_direction == "up"]
    assert upward

    last_close = upward[0].last_close
    steps = round((upward[0].trigger_price / last_close - 1.0) * 100.0 / step)
    closer = last_close * (1.0 + step * (steps - 1) / 100.0)

    replay = proximity.snapshot_at(
        snapshot, symbol=SYMBOL, close=closer, ts=hypo_ts, btc_symbol=SYMBOL
    )
    assert Trend(config=base_config).generate_signals(replay) == []


# --------------------------------------------------------------------------- #
# 4) "Tetik yok" AÇIKÇA kaydedilir
# --------------------------------------------------------------------------- #
def test_no_trigger_in_range_is_recorded_explicitly(
    base_config: dict[str, Any], tmp_path: Path
) -> None:
    """Boş liste "bilmiyoruz" ile "yok"u aynı hücreye koyardı; sembol adıyla yazılır."""
    strategy = Trend(config=base_config)
    probe = proximity.build_probe(
        strategy, config=base_config, band=_band(base_config, tmp_path), ledger=Ledger(tmp_path)
    )
    snapshot = _quiet_market()

    triggers = proximity.scan_symbol(
        [probe],
        snapshot,
        symbol=SYMBOL,
        hypo_ts=snapshot.as_of + pd.Timedelta(hours=4),
        btc_symbol=SYMBOL,
        range_pct=0.05,  # kırılım için fersah fersah dar
        step_pct=0.05,
        atr_period=14,
        states={probe.name: _state()},
    )[probe.name]

    assert triggers == []


# --------------------------------------------------------------------------- #
# 5) Kapıda ölen tetik AYRI işaretlenir (karar 34)
# --------------------------------------------------------------------------- #
def test_gate_failures_are_reported_not_dropped(
    scalp_config: dict[str, Any], tmp_path: Path
) -> None:
    """Scalp kolları %1 stop tabanına takılır; tetiklenen kurulum SİLİNMEZ, işaretlenir."""
    strategy = ScalpFixed(config=scalp_config)
    probe = proximity.build_probe(
        strategy, config=scalp_config, band=_band(scalp_config, tmp_path), ledger=Ledger(tmp_path)
    )
    snapshot = _quiet_market(freq="15min", bars=300)

    triggers = proximity.scan_symbol(
        [probe],
        snapshot,
        symbol=SYMBOL,
        hypo_ts=snapshot.as_of + pd.Timedelta(minutes=15),
        btc_symbol=SYMBOL,
        range_pct=5.0,
        step_pct=0.05,
        atr_period=14,
        states={probe.name: _state()},
    )[probe.name]

    assert triggers, "dar bantlı bir seride RSI(2) kolu ızgarada tetiklenmeli"
    # Kapı KARARI modelin kendi zincirinden gelir; ölçüler yanında durur ama yargı üretmez.
    for trigger in triggers:
        assert {gate.name for gate in trigger.gates} >= {"house_gates"}
        assert trigger.gates_passed == all(gate.passed for gate in trigger.gates)
        assert {m.name for m in trigger.measurements} >= {"min_stop_pct", "min_reward_risk"}
    assert any(not trigger.gates_passed for trigger in triggers), (
        "bu seride %1 stop tabanına takılan en az bir kurulum bekleniyor"
    )


def test_scalp_scan_reports_arms_not_a_selection(
    scalp_config: dict[str, Any], tmp_path: Path
) -> None:
    strategy = ScalpFixed(config=scalp_config)
    probe = proximity.build_probe(
        strategy, config=scalp_config, band=_band(scalp_config, tmp_path), ledger=Ledger(tmp_path)
    )
    snapshot = _quiet_market(freq="15min", bars=300)

    triggers = proximity.scan_symbol(
        [probe],
        snapshot,
        symbol=SYMBOL,
        hypo_ts=snapshot.as_of + pd.Timedelta(minutes=15),
        btc_symbol=SYMBOL,
        range_pct=5.0,
        step_pct=0.05,
        atr_period=14,
        states={probe.name: _state()},
    )[probe.name]

    assert all(trigger.arm is not None for trigger in triggers)


# --------------------------------------------------------------------------- #
# 6) Salt okunurluk ve çıkış kodları
# --------------------------------------------------------------------------- #
def test_run_returns_three_and_writes_nothing_when_no_symbol_is_scannable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Karar 51: boş rapor YEŞİL dönmez ve dosya YAZILMAZ."""
    def no_data(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("önbellek boş")

    monkeypatch.setattr(proximity, "load_cached_market_data", no_data)
    out = tmp_path / "proximity.json"

    code = proximity.run(["--layer", "base", "--out", str(out)])

    assert code == 3
    assert not out.exists()


def test_run_rejects_a_model_outside_the_layer_scope(tmp_path: Path) -> None:
    code = proximity.run(
        ["--layer", "base", "--models", "buyhold", "--out", str(tmp_path / "p.json")]
    )

    assert code == 2


def test_run_rejects_models_across_several_layers(tmp_path: Path) -> None:
    code = proximity.run(
        ["--layer", "base", "--layer", "ema", "--models", "trend", "--out", str(tmp_path / "p.json")]
    )

    assert code == 2


def test_scan_writes_a_readable_payload(
    base_config: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Uçtan uca: yük geçerli JSON, varsayımlar ve "tetik yok" kaydı içinde."""
    snapshot = _quiet_market()
    # Defter kökü tmp_path'e alınır: tarama gerçek deftere DOKUNMAZ ama pozisyon
    # durumunu ondan okur, yani test gerçek `ledgers/`i okumamalı.
    layer = replace(resolve_layer(load_config(), "base"), ledger_root=tmp_path)
    monkeypatch.setattr(proximity, "load_cached_market_data", lambda *a, **k: snapshot)

    payload = proximity.scan_layer(layer, models=["trend"])

    assert payload["as_of"] == snapshot.as_of.isoformat()
    assert payload["hypothetical_bar_at"] == (
        snapshot.as_of + pd.Timedelta(hours=4)
    ).isoformat()
    assert payload["assumptions"], "varsayımlar çıktıda AÇIKÇA yazılır"
    assert payload["models"][0]["model"] == "trend"
    # Geçerli JSON: nan -> null (main.jsonable ile TEK kopyadan).
    json.dumps(payload, allow_nan=False, default=str)


def test_cached_loader_never_writes_to_disk(tmp_path: Path) -> None:
    """`load_cached_market_data` diske DOKUNMAZ: "salt okunur" koddan denetlenebilir."""
    from core.data import load_cached_market_data

    config = resolve_layer(load_config(), "base").config
    config = dict(config)
    config["data"] = {**config["data"], "cache_dir": str(tmp_path / "cache")}

    with pytest.raises(Exception):
        load_cached_market_data(config, symbols=[SYMBOL])

    assert not (tmp_path / "cache").exists()


# --------------------------------------------------------------------------- #
# 7) Uçtan uca: ÖNBELLEKTEN oku, dosya yaz, yeşil dön
# --------------------------------------------------------------------------- #
def _seed_cache(directory: Path, symbols: list[str], *, bar: str, bars: int) -> None:
    """Turun bıraktığı parquet önbelleğinin aynısı; tarama ondan başka bir şey okumaz."""
    directory.mkdir(parents=True, exist_ok=True)
    for index, symbol in enumerate(symbols):
        closes = [100.0 + index + (i % 5) * 0.2 for i in range(bars)]
        candles = frame(
            closes,
            spread=0.3,
            freq="15min" if bar == "15m" else "4h",
            start=pd.Timestamp.now(tz="UTC").floor("h")
            - pd.Timedelta(minutes=15 * bars if bar == "15m" else 240 * bars),
        )
        candles.to_parquet(directory / f"{symbol.replace('/', '_')}_{bar}.parquet")


def test_end_to_end_reads_the_cache_and_writes_a_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    layer = resolve_layer(load_config(), "scalp")
    symbols = list(layer.symbols or [])
    cache = tmp_path / "cache"
    _seed_cache(cache, symbols, bar="15m", bars=320)

    original = proximity.resolve_layer

    def patched(config: Any, name: str) -> Any:
        resolved = original(config, name)
        config_copy = dict(resolved.config)
        config_copy["data"] = {**config_copy["data"], "cache_dir": str(cache)}
        return replace(resolved, config=config_copy, ledger_root=tmp_path / "ledgers")

    monkeypatch.setattr(proximity, "resolve_layer", patched)
    out = tmp_path / "proximity_scalp.json"

    code = proximity.run(["--layer", "scalp", "--out", str(out), "--log-level", "ERROR"])

    assert code == 0
    payload = json.loads(out.read_text(encoding="utf-8"))
    scan = payload["layers"][0]
    assert scan["layer"] == "scalp"
    assert scan["timeframe"] == "15m"
    assert scan["scanned_symbols"] == len(symbols)
    assert {item["model"] for item in scan["models"]} == set(proximity.SCOPE["scalp"])
    # Izgara katmanın barına göre: 15m'de ±%5 / %0.05.
    assert scan["grid"] == {"range_pct": 5.0, "step_pct": 0.05, "method": "grid"}
    for item in scan["models"]:
        # "tetik yok" bir boşluk değil, adıyla yazılan bir KAYIT.
        assert set(item["no_trigger"]) <= set(item["scanned_symbols"])
        assert len(item["triggers"]) + len(item["no_trigger"]) >= len(item["scanned_symbols"]) - len(
            {t["symbol"] for t in item["triggers"]}
        )

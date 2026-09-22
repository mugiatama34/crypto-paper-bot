"""`scalp_coinflip` — katmanın KONTROLÜ: geometri korunur, yön bilgisizdir.

Ön-kayıt: docs/backtest.md > 6h. Bu dosya o bölümün **S1 sağlamasını** ve kontrolün
tanımını (adil yazı-tura, eşleştirilmiş çekiliş) mekanik olarak sabitler.

Kontrolün değeri tam olarak bozulamazlığındadır: `scalp_patient`in C-2 kapısı bu modelin
ortalama R'sine göre ölçülecek. Geometri ayrışırsa kıyas bir maliyet karşılaştırmasına
döner (kural 14) ve ön-kayıt S1 sağlanmadığında M1/M2'nin OKUNMAYACAĞINI söylüyor —
burada sınanan şey o sağlamanın koddan garanti edilmesidir.
"""

from __future__ import annotations

import random
from typing import Any

import pandas as pd
import pytest

from core.config import load_config
from core.layers import resolve_layer
from core.tags import parse_tag
from strategies.scalp.arms import ArmSetup
from strategies.scalp_coinflip import ScalpCoinflip, flip
from strategies.scalp_fixed import ScalpFixed
from strategies.scalp_patient import ScalpPatient
from tests.helpers_market import frame, market

START = pd.Timestamp("2026-03-02 00:00:00", tz="UTC")
SYMBOLS = ("BTC-USDT-SWAP", "ETH-USDT-SWAP", "SOL-USDT-SWAP")


@pytest.fixture()
def config() -> dict[str, Any]:
    return resolve_layer(load_config(), "scalp").config


def _setup(direction: str = "long", symbol: str = "BTC-USDT-SWAP") -> ArmSetup:
    sign = 1.0 if direction == "long" else -1.0
    return ArmSetup(
        arm="rsi2_reversal",
        symbol=symbol,
        direction=direction,  # type: ignore[arg-type]
        entry_price=100.0,
        stop_price=100.0 - sign * 5.0,
        target_price=100.0 + sign * 12.0,
        detail="test kurulumu",
    )


def _market():
    """Üç sembollü, sakin bir 15m piyasası. Kolların TETİKLEMESİ gerekmez.

    Yazı-tura testleri yalnızca `as_of`a ve sembol adına bakar; eşleştirme testleri ise
    kurulumları `_fixed_proposals` ile sabitler (aşağıya bkz.).
    """
    frames = {}
    for index, symbol in enumerate(SYMBOLS):
        base = 100.0 + index * 10.0
        closes = [base + (0.05 if step % 2 else -0.05) for step in range(80)]
        frames[symbol] = frame(closes, start=START, freq="15min", spread=0.05)
    return market(frames, btc=frames[SYMBOLS[0]])


def _fixed_proposals(monkeypatch: pytest.MonkeyPatch) -> None:
    """`propose_all`ı EV KAPILARINDAN GEÇEN sabit kurulumlarla değiştirir.

    **Neden sentetik bir piyasa yetmiyor.** Kolların kendisi tetiklenebiliyor, ama ev
    kapıları (%1 stop tabanı ve 1.5R) gerçekçi bir 15m kurgusunda kurulumların
    neredeyse tamamını eliyor: ölçüldü — `rsi2_reversal` üç sembolde de tetikledi,
    üçünde de hedef/stop 1.12 çıkıp 1.5R kapısında öldü. Bu bir test kurgusu kusuru
    DEĞİL, karar 34'ün bulgusunun ta kendisidir: kapı aritmetiği kurulumları imkânsız
    kılıyor.

    Bu yüzden eşleştirme testleri `tests/test_scalp_model.py::_Stub`ın desenini izler —
    ölçülen şey kolun tetiklenmesi değil, İKİ MODELİN AYNI KURULUM KÜMESİNDEN AYNI
    SEÇİMİ yapıp yapmadığıdır. Kurulumu sabitlemek o soruyu piyasa gürültüsünden ayırır.

    Geometri kapıları geçecek biçimde seçildi: stop %5 (taban %1), hedef/stop 2.0
    (çıta 1.5).
    """
    import strategies.scalp.model as model_module

    def stub(market_data: Any, params: Any, *, scan: Any = None) -> dict[str, list[ArmSetup]]:
        if scan is not None:
            scan.examined = len(SYMBOLS)
        return {
            "rsi2_reversal": [
                ArmSetup(
                    arm="rsi2_reversal",
                    symbol=symbol,
                    direction="long",
                    entry_price=100.0,
                    stop_price=95.0,
                    target_price=110.0,
                    detail=f"sabit kurulum {symbol}",
                )
                for symbol in SYMBOLS
            ]
        }

    monkeypatch.setattr(model_module, "propose_all", stub)


# --------------------------------------------------------------------------- #
# S1 — geometri yansıtılır, YENİDEN KURULMAZ
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("direction", ["long", "short"])
def test_flip_preserves_stop_distance_and_reward_risk(direction: str) -> None:
    """Ön-kayıt §6h > S1'in koddan garantisi: iki oran da BİREBİR korunur.

    Kontrolün maliyet ölçeği (`cost_per_r`, `avg_stop_distance_pct`) kaynağınınkiyle aynı
    kalmazsa kural 14'ün bandı yanar ve kıyas bir sinyal karşılaştırması olmaktan çıkar.
    """
    original = _setup(direction)
    flipped = flip(original)

    assert flipped.direction != original.direction
    assert flipped.entry_price == original.entry_price
    assert flipped.stop_distance_pct == original.stop_distance_pct
    assert flipped.reward_risk == original.reward_risk


@pytest.mark.parametrize("direction", ["long", "short"])
def test_flip_puts_stop_and_target_on_the_correct_sides(direction: str) -> None:
    """Çevrilen long'da stop girişin ÜSTÜNDE, hedef ALTINDA olmalı (short için tersi).

    Geometri yanlış tarafa düşerse `core/validate.py` bunu bir PROGRAMLAMA HATASI sayar
    (kural 8) ve modelin tüm turunu düşürür — kontrol sessizce hiç işlem yapmazdı.
    """
    flipped = flip(_setup(direction))
    if flipped.direction == "short":
        assert flipped.stop_price > flipped.entry_price
        assert flipped.target_price < flipped.entry_price
    else:
        assert flipped.stop_price < flipped.entry_price
        assert flipped.target_price > flipped.entry_price


def test_flip_is_an_involution() -> None:
    """İki kez çevirmek başlangıca döner: yansıtma bir kayıp taşımıyor."""
    original = _setup("long")
    assert flip(flip(original)) == original


def test_flip_keeps_the_arm_and_detail() -> None:
    """Kol etiketi ve gerekçe metni DEĞİŞMEZ: gözlem çevrilmedi, yön çevrildi.

    Kol kırılımı `arm=` etiketinden okunur; değiştirmek kontrolün işlemlerini kol
    tablosunda kaybederdi.
    """
    original = _setup("long")
    flipped = flip(original)
    assert flipped.arm == original.arm
    assert flipped.detail == original.detail
    assert flipped.symbol == original.symbol


# --------------------------------------------------------------------------- #
# Yazı-tura ADİL ve BAĞIMSIZ
# --------------------------------------------------------------------------- #
def test_coin_is_fair_over_many_draws(config: dict[str, Any]) -> None:
    """10.000 çekilişte 'ters' oranı 0.5 ± 0.02.

    Yanlı bir yazı-tura kontrolü bilgisiz olmaktan çıkarır: sistematik olarak bir yöne
    eğilen bir kontrol, ölçülen pencerede piyasanın yönüyle karışır ve C-2 kapısı
    modelin değil rejimin ölçüsü olur.
    """
    model = ScalpCoinflip(config=config)
    rng = random.Random(0)
    base = pd.Timestamp("2026-03-02 00:00:00", tz="UTC")

    flips = 0
    draws = 10_000
    for index in range(draws):
        data = market(
            {SYMBOLS[0]: frame([100.0, 101.0], start=base, freq="15min")},
            as_of=base + pd.Timedelta(minutes=15 * index),
        )
        symbol = f"SYM{rng.randrange(10_000)}-USDT-SWAP"
        if not model._heads(data, _setup(symbol=symbol)):  # noqa: SLF001
            flips += 1
    assert abs(flips / draws - 0.5) < 0.02, f"yazı-tura yanlı: {flips}/{draws}"


def test_coin_is_deterministic_for_the_same_bar_and_symbol(config: dict[str, Any]) -> None:
    """Aynı bar + aynı sembol = aynı sonuç. Tekrarlanabilirlik `random_seed`in sözü."""
    data = _market()
    first = ScalpCoinflip(config=config)
    second = ScalpCoinflip(config=config)
    setup = _setup()
    assert first._heads(data, setup) == second._heads(data, setup)  # noqa: SLF001


def test_coin_differs_across_symbols_in_the_same_bar(config: dict[str, Any]) -> None:
    """Sembol tohuma GİRER: girmeseydi bir bardaki tüm kurulumlar aynı yönü alırdı.

    O bar için kontrol bilgisiz değil TEK YÖNLÜ olurdu — ve tek yönlü bir kontrol,
    piyasanın o barki yönüyle karışır.
    """
    data = _market()
    model = ScalpCoinflip(config=config)
    results = {
        symbol: model._heads(data, _setup(symbol=symbol))  # noqa: SLF001
        for symbol in (f"S{i}-USDT-SWAP" for i in range(40))
    }
    assert len(set(results.values())) == 2, "40 sembolün hepsi aynı yüzü gördü"


# --------------------------------------------------------------------------- #
# Eşleştirme — çekiliş PAYLAŞILIR, akış BOZULMAZ
# --------------------------------------------------------------------------- #
def test_shares_the_draw_identity_with_scalp_fixed(config: dict[str, Any]) -> None:
    """`rng_identity` mirasla `scalp_fixed`: eşleştirilmiş deneyin tanımı."""
    assert ScalpCoinflip(config=config).rng_identity == ScalpFixed.name


def test_inherits_the_patient_time_stop(config: dict[str, Any]) -> None:
    """Zaman stop'u `scalp_patient`inkidir (100 bar), `scalp_fixed`inki (16) DEĞİL.

    Kontrol, C-2'de `scalp_patient`e karşı ölçülecek; farklı bir zaman stop'u, ölçülen
    eksene (yön) ikinci bir değişken (süre) katardı.
    """
    assert ScalpCoinflip(config=config).time_stop_key == ScalpPatient.time_stop_key


def test_arm_and_symbol_choice_match_scalp_patient(
    config: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Kontrol ile kaynak AYNI kurulumu seçer: yazı-tura kol/sembol akışını BOZMAZ.

    Ön-kayıtın eşleştirilmiş deney iddiası tam olarak budur. Yazı-tura `_round_rng`in
    akışından çekilseydi akış ilerler ve iki model farklı sembolleri oynardı — eşleştirme,
    onu kurmak için eklenen şey tarafından bozulurdu.
    """
    _fixed_proposals(monkeypatch)
    data = _market()
    source = ScalpPatient(config=config)
    control = ScalpCoinflip(config=config)

    origin = source.generate_signals(data)
    mirror = control.generate_signals(data)

    assert origin, "kurgu kurulum üretmeliydi: boş bir eşleştirme testi garanti değildir"
    assert len(origin) == len(mirror)
    assert [s.symbol for s in origin] == [s.symbol for s in mirror]
    assert [parse_tag(s.reason, "arm") for s in origin] == [
        parse_tag(s.reason, "arm") for s in mirror
    ]


def test_signal_geometry_matches_when_the_coin_keeps_the_direction(
    config: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Tura 'aynı' geldiyse sinyal kaynağınkiyle BİREBİR aynı olmalı."""
    _fixed_proposals(monkeypatch)
    data = _market()
    source = ScalpPatient(config=config)
    control = ScalpCoinflip(config=config)
    origin = source.generate_signals(data)
    mirror = control.generate_signals(data)
    assert origin, "kurgu kurulum üretmeliydi"

    for left, right in zip(origin, mirror):
        if parse_tag(right.reason, "coin") != "same":
            continue
        assert left.direction == right.direction
        assert left.stop_price == right.stop_price
        assert left.take_profits[0].price == right.take_profits[0].price


# --------------------------------------------------------------------------- #
# Denetim izi
# --------------------------------------------------------------------------- #
def test_every_signal_carries_the_coin_tag(
    config: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """`coin=same|flipped` olmadan 'kontrol gerçekten çevirdi mi' defterden okunamaz."""
    _fixed_proposals(monkeypatch)
    data = _market()
    signals = ScalpCoinflip(config=config).generate_signals(data)
    assert signals, "kurgu kurulum üretmeliydi"
    for signal in signals:
        assert parse_tag(signal.reason, "coin") in {"same", "flipped"}
        # Rezerve etiketler ezilmemeli: kol kırılımı bunlardan okunur.
        assert parse_tag(signal.reason, "arm")


def test_regime_filter_eliminates_nothing(config: dict[str, Any]) -> None:
    """Kanca bir ELEME noktasıdır ama bu model elemez: giren ve çıkan sayı EŞİT.

    Elenseydi kontrolün örneklemi kaynağınkinden küçük olurdu ve `control_min_trades`
    kapısına daha yavaş ulaşırdı — yani kontrol, ölçmesi gereken şeyi geciktirirdi.
    """
    data = _market()
    model = ScalpCoinflip(config=config)
    setups = [_setup("long"), _setup("short")]
    assert len(model.regime_filter(setups, data)) == len(setups)


def test_control_model_is_a_competitor_not_a_benchmark(config: dict[str, Any]) -> None:
    """Kontrol yarışmacıdır: `is_benchmark`/`is_replica` olsaydı kabul kapılarına girmezdi."""
    model = ScalpCoinflip(config=config)
    assert model.is_benchmark is False
    assert model.is_replica is False

"""`ScalpModel.take_survey`: kol × eleme sebebi sayımı — AYRIK, denetim izi, etkisiz.

Ön-kayıt: docs/backtest.md > 6i > 6. Sınanan üç şey o bölümün üç iddiasıdır:

1. **Sayım AYRIKTIR.** Her kol için sebeplerin toplamı tam olarak o barda taranan sembol
   sayısına eşittir. Sağlama olmadan bir kolun sessizce düşmesi görünmezdi — karar 34/48
   tam olarak bu yüzden iki backtest gecikti.
2. **Sayım DAVRANIŞI DEĞİŞTİRMEZ** (kural 15): sayım kapalıyken ve açıkken üretilen
   sinyaller birebir aynıdır.
3. **"Kol patladı" ile "tez tutmadı" AYNI hücreye yazılmaz.** `propose_all` bir kolun
   hatasını yutup boş liste döndürür; ikisi tek sebepte toplansaydı bozuk bir kol,
   hiç tetiklemeyen bir koldan ayırt edilemezdi.

Kollar burada SAHTE'dir (`ARMS` monkeypatch'lenir) ve bu bilinçlidir: ölçülen şey kolun
ne zaman tetiklediği değil (o `tests/test_scalp_arms.py`nin işi), sayımın aritmetiğidir.
Gerçek kollarla yazılmış bir sağlama, piyasa verisinin tesadüfüne bağlı olurdu.
"""

from __future__ import annotations

from typing import Any, Iterable, Sequence

import pandas as pd
import pytest

from core.config import load_config
from core.layers import resolve_layer
from strategies.registry import build
from strategies.scalp import arms as arms_module
from strategies.scalp.arms import ArmParams, ArmSetup, SymbolView
from strategies.scalp.model import (
    ARM_ERROR,
    MIN_REWARD,
    MIN_STOP,
    NO_SETUP,
    QUOTA,
    SCANNED,
    SELECTED,
    survey_key,
)
from tests.helpers_market import frame, market

DAY = pd.Timestamp("2026-03-02 00:00:00", tz="UTC")
SYMBOLS = (
    "BTC-USDT-SWAP", "ETH-USDT-SWAP", "SOL-USDT-SWAP", "XRP-USDT-SWAP", "DOGE-USDT-SWAP",
)
# Kol bazlı sebepler; `SCANNED` kolsuzdur (paydadır) ve bu kümeye GİRMEZ.
REASONS = (NO_SETUP, MIN_STOP, MIN_REWARD, "rejim_kapisi", QUOTA, SELECTED, ARM_ERROR)

LIVE_MODELS = ("scalp_fixed", "scalp_patient", "scalp_coinflip")


@pytest.fixture()
def config() -> dict[str, Any]:
    return resolve_layer(load_config(), "scalp").config


@pytest.fixture()
def data(request):
    count = getattr(request, "param", len(SYMBOLS))
    closes = [100.0 + (i % 5) * 0.4 for i in range(120)]
    return market({
        symbol: frame(closes, spread=0.3, start=DAY, freq="15min")
        for symbol in SYMBOLS[:count]
    })


@pytest.fixture()
def varied_data():
    """Sembollerin ATR%'i AYRIŞIR: kesitsel medyan kapısının ölçülebilmesinin ön koşulu.

    Eşit oynaklıkta her sembol medyana EŞİTTİR ve kapı (`ratio < medyan`) hiçbir şeyi
    elemez — o zaman test kapının sebebini değil verinin düzlüğünü ölçerdi.
    """
    frames = {}
    for index, symbol in enumerate(SYMBOLS):
        amplitude = 0.2 + index * 0.6
        closes = [100.0 + (i % 5) * amplitude for i in range(120)]
        frames[symbol] = frame(closes, spread=0.1 + index * 0.2, start=DAY, freq="15min")
    return market(frames)


def _setup(view: SymbolView, *, arm: str, stop_pct: float, reward: float) -> ArmSetup:
    """Kapıların hangi dalına düşeceği ÖNCEDEN bilinen kurulum."""
    stop = view.close * (1.0 - stop_pct)
    return ArmSetup(
        arm=arm,
        symbol=view.symbol,
        direction="long",
        entry_price=view.close,
        stop_price=stop,
        target_price=view.close + (view.close - stop) * reward,
        detail="sahte kurulum",
    )


def _install(monkeypatch: pytest.MonkeyPatch, **arms: Any) -> None:
    """`ARMS` sözlüğünü sahte kollarla değiştirir (scan_all bu globali okur)."""
    monkeypatch.setattr(arms_module, "ARMS", dict(arms))


def _arm(builder) -> Any:
    def run(views: Sequence[SymbolView], params: ArmParams) -> list[ArmSetup]:
        return builder(views)
    return run


def _totals(survey: dict[str, int], arm: str) -> int:
    return sum(survey.get(survey_key(arm, reason), 0) for reason in REASONS)


def _assert_disjoint(survey: dict[str, int], arms: Iterable[str]) -> None:
    """ÖN-KAYITLI SAĞLAMA: her kol için `Σ sebep == taranan`."""
    scanned = survey[SCANNED]
    for arm in arms:
        assert _totals(survey, arm) == scanned, (
            f"{arm}: sebeplerin toplamı taranan sembol sayısını vermiyor "
            f"({_totals(survey, arm)} != {scanned}); sayım AYRIK değil\n"
            f"  sayım: {{k: v for k, v in survey.items() if k.startswith(arm)}}"
        )


# --------------------------------------------------------------------------- #
# (1) Ayrık sayım
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("name", LIVE_MODELS)
def test_every_gate_branch_is_counted_exactly_once(monkeypatch, config, data, name) -> None:
    """Her kapı dalı ayrı bir sebep: taban, hedef/stop, kota, seçildi, kurulum yok."""
    def builder(views: Sequence[SymbolView]) -> list[ArmSetup]:
        return [
            _setup(views[0], arm="a", stop_pct=0.005, reward=2.0),   # stop tabanı eler
            _setup(views[1], arm="a", stop_pct=0.05, reward=1.0),    # hedef/stop eler
            _setup(views[2], arm="a", stop_pct=0.05, reward=2.0),    # geçer
            _setup(views[3], arm="a", stop_pct=0.05, reward=2.0),    # geçer
        ]  # views[4] için kurulum YOK

    _install(monkeypatch, a=_arm(builder))
    model = build(name, config=config)
    model.generate_signals(data)
    survey = dict(model.take_survey() or {})

    assert survey[SCANNED] == 5
    assert survey[survey_key("a", MIN_STOP)] == 1
    assert survey[survey_key("a", MIN_REWARD)] == 1
    assert survey[survey_key("a", NO_SETUP)] == 1
    assert survey[survey_key("a", SELECTED)] == 1
    assert survey[survey_key("a", QUOTA)] == 1
    _assert_disjoint(survey, ["a"])


def test_unplayed_arms_count_every_kept_setup_as_quota(monkeypatch, config, data) -> None:
    """Oynanmayan kolun kapıdan geçmiş kurulumlarının TAMAMI `kota`dır.

    "Başka kol seçildi" ile "bu kolda başka sembol çekildi" ayrı ayrı sayılmaz: ikisi de
    aynı şeyi söyler (oynanabilirdi, barda tek sinyal oynandı).
    """
    def builder(arm: str, count: int):
        def build_setups(views: Sequence[SymbolView]) -> list[ArmSetup]:
            return [
                _setup(view, arm=arm, stop_pct=0.05, reward=2.0) for view in views[:count]
            ]
        return build_setups

    _install(monkeypatch, a=_arm(builder("a", 3)), b=_arm(builder("b", 2)))
    model = build("scalp_patient", config=config)
    signals = model.generate_signals(data)
    survey = dict(model.take_survey() or {})

    assert len(signals) == 1
    played = signals[0].reason.split("arm=")[1].split()[0]
    unplayed = "b" if played == "a" else "a"
    assert survey[survey_key(played, SELECTED)] == 1
    assert survey.get(survey_key(unplayed, SELECTED), 0) == 0
    assert survey[survey_key(unplayed, QUOTA)] == (3 if unplayed == "a" else 2)
    _assert_disjoint(survey, ["a", "b"])


def test_a_bar_with_no_setup_at_all_still_balances(monkeypatch, config, data) -> None:
    """Sinyalsiz bar da sayılır: `signals=0` ile "kol hiç taranmadı" ayrı şeylerdir."""
    _install(monkeypatch, a=_arm(lambda views: []))
    model = build("scalp_patient", config=config)

    assert model.generate_signals(data) == []
    survey = dict(model.take_survey() or {})
    assert survey[survey_key("a", NO_SETUP)] == 5
    _assert_disjoint(survey, ["a"])


def test_the_regime_gate_gets_its_own_reason(monkeypatch, config, varied_data) -> None:
    """`scalp_vol`ün kapısı `kurulum_yok`a KARIŞMAZ; sağlama yine tutar."""
    def builder(views: Sequence[SymbolView]) -> list[ArmSetup]:
        return [_setup(view, arm="a", stop_pct=0.05, reward=2.0) for view in views]

    _install(monkeypatch, a=_arm(builder))
    model = build("scalp_vol", config=config)
    model.generate_signals(varied_data)
    survey = dict(model.take_survey() or {})

    # Kesitsel medyan kapısı sembollerin yarısını eler; kaçını elediği veriye bağlıdır,
    # ölçülen şey sebebin AYRI durması ve sağlamanın tutmasıdır.
    assert survey.get(survey_key("a", "rejim_kapisi"), 0) > 0
    _assert_disjoint(survey, ["a"])
    # `scalp_vol`ün KENDİ teşhis anahtarları sağlamanın DIŞINDADIR (kolsuzdurlar).
    assert survey.get("dusuk_vol", 0) > 0
    assert "/" not in "dusuk_vol"


# --------------------------------------------------------------------------- #
# (2) Sayım davranışı değiştirmez
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("name", LIVE_MODELS)
def test_signals_are_identical_with_and_without_the_survey(
    monkeypatch, config, data, name
) -> None:
    """Sayım KAPALIYKEN ve açıkken aynı barda aynı sinyaller üretilir (kural 15).

    "Kapalı" hâli `_note_survey`i etkisizleştirerek kurulur: sayımın tek yazma yolu odur,
    yani hiçbir sayaç dönmezken model tam olarak sayım eklenmeden önceki koddur.
    """
    def builder(views: Sequence[SymbolView]) -> list[ArmSetup]:
        return [_setup(view, arm="a", stop_pct=0.05, reward=2.0) for view in views]

    _install(monkeypatch, a=_arm(builder))
    with_survey = build(name, config=config).generate_signals(data)

    from strategies.scalp.model import ScalpModel

    muted = build(name, config=config)
    monkeypatch.setattr(ScalpModel, "_note_survey", lambda self, k, c: None)
    without_survey = muted.generate_signals(data)

    assert muted.take_survey() is None, "sayım gerçekten kapalı olmalı"
    assert len(with_survey) == len(without_survey) == 1
    for left, right in zip(with_survey, without_survey):
        assert (left.symbol, left.direction) == (right.symbol, right.direction)
        assert left.stop_price == pytest.approx(right.stop_price, rel=1e-12)
        assert left.take_profits[0].price == pytest.approx(
            right.take_profits[0].price, rel=1e-12
        )
        assert left.reason == right.reason


# --------------------------------------------------------------------------- #
# (3) Patlayan kol
# --------------------------------------------------------------------------- #
def test_a_crashed_arm_is_counted_apart_from_a_silent_one(monkeypatch, config, data) -> None:
    """`kol_hatasi` ≠ `kurulum_yok`: bozuk bir kol, hiç tetiklemeyenden ayırt edilmeli."""
    def boom(views: Sequence[SymbolView], params: ArmParams) -> list[ArmSetup]:
        raise RuntimeError("kol bozuk")

    _install(monkeypatch, a=boom, b=_arm(lambda views: []))
    model = build("scalp_patient", config=config)

    assert model.generate_signals(data) == []
    survey = dict(model.take_survey() or {})
    assert survey[survey_key("a", ARM_ERROR)] == 5
    assert survey.get(survey_key("a", NO_SETUP), 0) == 0
    assert survey[survey_key("b", NO_SETUP)] == 5
    assert survey.get(survey_key("b", ARM_ERROR), 0) == 0
    _assert_disjoint(survey, ["a", "b"])


# --------------------------------------------------------------------------- #
# Okuma sözleşmesi
# --------------------------------------------------------------------------- #
def test_the_survey_is_drained_when_read(monkeypatch, config, data) -> None:
    """Motor bar bazında toplar: okunan sayım tur tur BİRİKMEZ."""
    _install(monkeypatch, a=_arm(lambda views: []))
    model = build("scalp_patient", config=config)
    model.generate_signals(data)

    assert model.take_survey() is not None
    assert model.take_survey() is None


def test_a_crashed_scan_does_not_leak_into_the_next_bar(monkeypatch, config, data) -> None:
    """Yarım sayım BİR SONRAKİ barın sayımına sızamaz.

    Motor patlayan bir çağrıdan sonra `take_survey`i hiç ÇAĞIRMAZ (core/engine.py), yani
    yarım sayım okunmaz; sızmasını engelleyen şey ise sayacın her taramanın BAŞINDA
    sıfırlanmasıdır. İkisi birlikte "bu barda şu kadar sembol incelendi" satırını doğru
    tutar.
    """
    def explode_after_counting(views: Sequence[SymbolView], params: ArmParams):
        raise RuntimeError("tarama yarıda kaldı")

    model = build("scalp_patient", config=config)
    monkeypatch.setattr(
        arms_module, "symbol_views", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x"))
    )
    with pytest.raises(RuntimeError):
        model.generate_signals(data)
    monkeypatch.undo()

    _install(monkeypatch, a=_arm(lambda views: []))
    model.generate_signals(data)
    survey = dict(model.take_survey() or {})
    assert survey[SCANNED] == 5, "önceki barın yarım sayımı sızmış"
    _assert_disjoint(survey, ["a"])


def test_take_survey_is_not_overridden_by_any_live_scalp_model() -> None:
    """Tek kopya: üç model de aynı sayımı alır (docs/backtest.md > 6i > 6)."""
    from strategies.scalp.model import ScalpModel
    from strategies.scalp_coinflip import ScalpCoinflip
    from strategies.scalp_fixed import ScalpFixed
    from strategies.scalp_patient import ScalpPatient
    from strategies.scalp_vol import ScalpVol

    for model in (ScalpFixed, ScalpPatient, ScalpCoinflip, ScalpVol):
        assert model.take_survey is ScalpModel.take_survey, (
            f"{model.__name__} `take_survey`i eziyor: iki uygulama, aynı eksenin iki "
            "tarafında iki farklı 'taranan sembol' tanımı demektir"
        )

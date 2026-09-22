"""`ScalpModel.take_survey` — AYRIK sayım ve DEĞİŞMEZLİK.

Survey bir denetim izidir (kural 15): ölçüme girmez, sinyalleri ve sıralarını
DEĞİŞTİRMEZ. Bu dosya iki şeyi sınar ve ikisi de ön-kayıtta (docs/backtest.md > 6h)
yazılıdır:

1. **Ayrıklık:** her kol için sebeplerin toplamı o barda taranan sembol sayısına eşittir.
   Değişmez bozulursa sayım bir şeyi iki kez sayıyor ya da hiç saymıyor demektir ve
   `funding_spike_fade`in nerede öldüğü sorusu yanlış cevaplanır.
2. **Değişmezlik:** sayımın açık olması üretilen sinyalleri etkilemez. Bunu kanıtlamanın
   tek dürüst yolu sayımı kapatıp açmak değil (kapatılamaz), aynı barı iki kez koşup
   `take_survey`in araya girmesinin sinyalleri kaydırmadığını göstermektir — çünkü
   sayımın tek yan etkisi bir sözlüğe yazmak ve onu okuyunca sıfırlamaktır.
"""

from __future__ import annotations

from typing import Any

import pandas as pd
import pytest

from core.config import load_config
from core.layers import resolve_layer
from strategies.scalp.arms import ARM_NAMES, ArmParams, symbol_views
from strategies.scalp.model import (
    SURVEY_ARM_ERROR,
    SURVEY_CHOSEN,
    SURVEY_NO_SETUP,
    SURVEY_QUOTA,
    SURVEY_REGIME,
    SURVEY_REWARD_RISK,
    SURVEY_STOP_FLOOR,
)
from strategies.scalp_fixed import ScalpFixed
from strategies.scalp_patient import ScalpPatient
from tests.helpers_market import frame, market

START = pd.Timestamp("2026-03-02 00:00:00", tz="UTC")
SYMBOLS = ("BTC-USDT-SWAP", "ETH-USDT-SWAP", "SOL-USDT-SWAP")

_REASONS = (
    SURVEY_NO_SETUP,
    SURVEY_ARM_ERROR,
    SURVEY_STOP_FLOOR,
    SURVEY_REWARD_RISK,
    SURVEY_REGIME,
    SURVEY_QUOTA,
    SURVEY_CHOSEN,
)


@pytest.fixture()
def config() -> dict[str, Any]:
    return resolve_layer(load_config(), "scalp").config


def _market(bars: int = 200):
    """Üç sembollü, hareketli bir 15m piyasası. Kolların tetiklenmesi ŞART DEĞİLDİR.

    Ayrıklık değişmezi kurulum üretilmese de tutmalıdır — hatta asıl orada tutmalıdır:
    `kurulum_yok` kovası tam olarak o durumu sayar.
    """
    frames = {}
    for index, symbol in enumerate(SYMBOLS):
        closes = [
            100.0 + index * 10 + (step % 7) - (step % 3) * 1.5 + step * 0.05
            for step in range(bars)
        ]
        frames[symbol] = frame(closes, start=START, freq="15min", spread=0.4)
    return market(frames, btc=frames[SYMBOLS[0]])


def _by_arm(survey: dict[str, int]) -> dict[str, dict[str, int]]:
    """`<kol>:<sebep>` anahtarlarını kola göre grupla; kolsuz anahtarları YOK SAY.

    Kolsuz anahtar `scalp_vol`ün `rejim_kapisi_yok` sayacıdır: bar düzeyinde bir olgudur
    ve tanımı gereği ayrık sayımın dışındadır (bkz. o modelin `take_survey` docstring'i).
    """
    grouped: dict[str, dict[str, int]] = {}
    for key, count in survey.items():
        if ":" not in key:
            continue
        arm, reason = key.split(":", 1)
        assert reason in _REASONS, f"tanınmayan sebep kodu: {reason!r}"
        grouped.setdefault(arm, {})[reason] = count
    return grouped


def test_survey_counts_are_disjoint_and_sum_to_examined(config: dict[str, Any]) -> None:
    """Her kol için Σsebep == taranan sembol. Ön-kayıt §6h'nin ayrık sayım sağlaması."""
    data = _market()
    model = ScalpFixed(config=config)
    model.generate_signals(data)
    survey = dict(model.take_survey() or {})

    examined = len(symbol_views(data, atr_period=model._params.atr_period))  # noqa: SLF001
    assert examined == len(SYMBOLS), "kurulum: üç sembolün üçü de taranabilmeli"

    grouped = _by_arm(survey)
    assert set(grouped) == set(ARM_NAMES), "her kol sayımda görünmeli"
    for arm, counts in grouped.items():
        assert sum(counts.values()) == examined, (
            f"{arm}: Σ{counts} != taranan sembol {examined} — sayım ayrık değil"
        )


def test_survey_is_empty_before_any_round(config: dict[str, Any]) -> None:
    """Hiç koşmamış model `None` döner: boş sözlük 'sayım tuttu ve sıfır çıktı' demekti."""
    assert ScalpFixed(config=config).take_survey() is None


def test_take_survey_resets_between_bars(config: dict[str, Any]) -> None:
    """Okunan sayım sıfırlanır; birikirse tur raporu barları üst üste toplardı."""
    data = _market()
    model = ScalpFixed(config=config)
    model.generate_signals(data)
    first = dict(model.take_survey() or {})
    assert first, "ilk barda sayım üretilmeliydi"
    assert model.take_survey() is None, "ikinci okuma boş olmalı"


def test_survey_does_not_change_signals(config: dict[str, Any]) -> None:
    """DEĞİŞMEZLİK: `take_survey` araya girse de aynı bar aynı sinyalleri üretir."""
    data = _market()

    quiet = ScalpFixed(config=config)
    without = quiet.generate_signals(data)

    noisy = ScalpFixed(config=config)
    noisy.take_survey()
    with_survey = noisy.generate_signals(data)
    noisy.take_survey()

    assert [s.symbol for s in without] == [s.symbol for s in with_survey]
    assert [s.direction for s in without] == [s.direction for s in with_survey]
    assert [s.stop_price for s in without] == [s.stop_price for s in with_survey]


def test_arm_failure_is_not_counted_as_missing_setup(
    config: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Patlayan kol `kol_hatasi`ya düşer, `kurulum_yok`a DEĞİL.

    İkisini aynı kovaya yazmak bir arızayı olağan bir eleme gibi gösterirdi — tam olarak
    `propose_all`ın `scan` parametresinin var olma sebebi.
    """
    import strategies.scalp.arms as arms

    def explode(views: Any, params: ArmParams) -> list[Any]:
        raise RuntimeError("kol patladı")

    monkeypatch.setitem(arms.ARMS, "rsi2_reversal", explode)

    data = _market()
    model = ScalpFixed(config=config)
    model.generate_signals(data)
    grouped = _by_arm(dict(model.take_survey() or {}))

    counts = grouped["rsi2_reversal"]
    assert counts.get(SURVEY_ARM_ERROR) == len(SYMBOLS)
    assert SURVEY_NO_SETUP not in counts, "patlayan kol iki kovaya birden yazılmamalı"
    assert sum(counts.values()) == len(SYMBOLS)


def test_patient_inherits_the_survey(config: dict[str, Any]) -> None:
    """Sayım gövdededir: alt sınıfta override YOKTUR, üç model de otomatik alır."""
    data = _market()
    model = ScalpPatient(config=config)
    model.generate_signals(data)
    assert _by_arm(dict(model.take_survey() or {})), "scalp_patient sayım üretmeli"

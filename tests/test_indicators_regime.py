"""Rejim kapısının iki yeni yardımcısı: ADX ve üst zaman dilimine toplama.

İkisi de `core/indicators.py`dedir çünkü gösterge matematiğinin ikinci bir uygulaması,
iki modelin aynı barda farklı sayı görmesi demektir (kural 5). Buradaki testler o tek
tanımın SÖZLEŞMESİNİ sabitler: yetersiz barda sayı üretilmez, kapanmamış üst bar
atılır, ve aynı çerçeve her zaman aynı sayıyı verir (Wilder özyinelemesi yok).
"""

from __future__ import annotations

import pandas as pd
import pytest

from core.indicators import adx, resample_ohlcv
from tests.helpers_market import frame


def test_adx_needs_two_full_periods() -> None:
    """`2 × period + 1` bardan azında None — kısmi pencereyle "trend gücü" üretilmez."""
    assert adx(frame([100.0] * 28, freq="15min"), 14) is None
    assert adx(frame([100.0] * 29, freq="15min"), 14) is not None


def test_adx_is_high_in_a_trend_and_low_in_chop() -> None:
    trend = adx(frame([100.0 + i for i in range(60)], freq="15min"), 14)
    chop = adx(frame([100.0 + (i % 2) for i in range(60)], freq="15min"), 14)

    assert trend is not None and chop is not None
    assert trend > 80.0
    assert chop < 20.0


def test_adx_does_not_depend_on_how_much_history_is_prepended() -> None:
    """Wilder yumuşatması KULLANILMAZ: aynı bar, çerçeve ne kadar uzun olursa olsun aynı.

    Bu, ATR ve RSI'daki kararın aynısıdır ve rejim kapısının koşudan koşuya başka
    kurulumları elememesinin tek güvencesidir (önbellek farklı ısınabilir).
    """
    closes = [100.0 + (i % 5) * 0.4 for i in range(200)]
    short = adx(frame(closes[-40:], freq="15min"), 14)
    long = adx(frame(closes, freq="15min"), 14)

    assert short == pytest.approx(long)


def test_resample_drops_the_incomplete_bucket() -> None:
    """Son saat yalnızca iki 15m barı taşıyorsa o saat KAPANMAMIŞTIR ve atılır."""
    six = frame([100.0, 101.0, 102.0, 103.0, 104.0, 105.0], freq="15min")

    hourly = resample_ohlcv(six, "1h")

    assert len(hourly) == 1
    assert hourly.index[-1] == six.index[0]
    assert hourly["open"].iloc[0] == 100.0
    assert hourly["close"].iloc[0] == 103.0
    assert hourly["volume"].iloc[0] == 4.0


def test_resample_keeps_high_low_and_volume_of_the_window() -> None:
    quarter = frame([100.0, 104.0, 98.0, 101.0], spread=1.0, volumes=[1, 2, 3, 4], freq="15min")

    hourly = resample_ohlcv(quarter, "1h")

    assert hourly["high"].iloc[0] == pytest.approx(105.0)
    assert hourly["low"].iloc[0] == pytest.approx(97.0)
    assert hourly["volume"].iloc[0] == pytest.approx(10.0)


def test_resample_of_an_empty_frame_is_empty() -> None:
    empty = pd.DataFrame(
        {"open": [], "high": [], "low": [], "close": [], "volume": []},
        index=pd.DatetimeIndex([], tz="UTC", name="ts"),
    )

    assert resample_ohlcv(empty, "1h").empty

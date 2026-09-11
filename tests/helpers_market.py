"""Strateji testleri için sentetik MarketData kurucuları.

Neden ortak dosya: iki strateji testi de "200+ barlık geçmiş + kontrollü son bar" kurmak
zorunda (EMA200 ve Bollinger/RSI pencereleri bunu şart koşar). Kurucuyu kopyalamak, iki
testin farklı `as_of` ya da farklı bar aralığı kullanmasına ve "aynı veriyi gördüler"
varsayımının testlerde bile bozulmasına yol açardı.
"""

from __future__ import annotations

from typing import Mapping, Sequence

import pandas as pd

from strategies.base import MarketData

START = pd.Timestamp("2026-01-01 00:00:00", tz="UTC")


def frame(closes: Sequence[float], *, spread: float = 0.5) -> pd.DataFrame:
    """Kapanış dizisinden OHLCV çerçevesi; high/low kapanışın `spread` kadar uzağında."""
    index = pd.date_range(START, periods=len(closes), freq="4h", tz="UTC", name="ts")
    values = [float(close) for close in closes]
    return pd.DataFrame(
        {
            "open": values,
            "high": [close + spread for close in values],
            "low": [close - spread for close in values],
            "close": values,
            "volume": [1.0] * len(values),
        },
        index=index,
    )


def market(
    ohlcv: Mapping[str, pd.DataFrame],
    *,
    btc: pd.DataFrame | None = None,
    as_of: pd.Timestamp | None = None,
) -> MarketData:
    frames = dict(ohlcv)
    reference = btc if btc is not None else next(iter(frames.values()))
    return MarketData(
        ohlcv=frames,
        btc=reference,
        funding={},
        as_of=as_of if as_of is not None else reference.index[-1],
    )


def rising(bars: int = 260, *, start: float = 100.0, step: float = 1.0) -> list[float]:
    """EMA50 > EMA200 rejimi üreten monoton yükseliş."""
    return [start + step * i for i in range(bars)]


def falling(bars: int = 260, *, start: float = 400.0, step: float = 1.0) -> list[float]:
    """EMA50 < EMA200 rejimi üreten monoton düşüş."""
    return [start - step * i for i in range(bars)]


def flat(bars: int = 260, *, level: float = 100.0) -> list[float]:
    return [level] * bars

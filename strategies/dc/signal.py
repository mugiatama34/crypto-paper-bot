"""Ölüm kesişimi + geri çekilme kurulumunun TEK tanımı (modeller 22-23 aynı kopyayı okur).

Ön-kayıt: docs/backtest.md > 6i. Tanımlar ölçülebilirlik sayımıyla
(`scripts/measure_death_cross.py`) BİREBİR aynıdır; bu modül onları DEĞİŞTİRMEZ:

- **Ölüm kesişimi:** bar c'de `EMA50 < EMA200`, bar c−1'de `EMA50 ≥ EMA200`.
- **Rejim:** kesişimden sonra `EMA50 < EMA200` kaldığı sürece aktif; kesişimin kendi barı
  rejimin ilk barıdır.
- **Kurulum barı:** rejim aktif, `high ≥ EMA50`, `close < EMA50`, `close < open`.

Modelin eklediği tek şey GEOMETRİDİR ve o da ön-kayıtlıdır:

- **Stop:** kurulum barındaki EMA200.
- **Hedef:** `min(low[c .. t−1])` — kurulum barı HARİÇ (§6i > 3). Dâhil edilseydi hedef
  tanım gereği `≤ low[t] ≤ close[t]` olur ve "hedef zaten geçilmiş" kuralı fiilen boş
  kalırdı. `t = c` ise aralık boştur: `target_undefined`.
- `close ≤ hedef` ise sinyal yok (`target_passed`). Bu kural bir tercih değil, kural 8'in
  zorunluluğudur: `core/validate.py` short hedefini kurulum kapanışına göre doğrular ve
  kapanışın altında olmayan bir hedef `ValueError` ile modelin turunu boşaltırdı.

**Stop tavanı burada YOKTUR.** `6.0 × ATR` kapısı motorundur (`core/engine.py::
_within_stop_band`) — model kendisi süzseydi eleme iki yerde yazılı olurdu ve model ile
kontrol farklı yerlerde süzülebilirdi.

**Görüş penceresi bir MODEL kuralıdır** (`dc.lookback_bars`, §6i > TADİLAT-1): canlıda
modele `data.history_bars` bar verilir, backtest'te ise motor her bara yüklenen verinin
TAMAMINI verir (`core/engine.py::_snapshot`). Kesişimin görülebilirliği pencereye bağlı
olduğu için pencere burada, iki ortamda aynı olacak biçimde kesilir. Pencerenin ilk
`warmup_bars` barı EMA200'ün ısınmasıdır ve kesişim o sınırdan SONRA aranır.

**Tarama sırası ve sayım kodları sabittir** (§6i > 3): her sembol TAM OLARAK bir koda
düşer, yani `Σ counts == taranan sembol` değişmezi korunur. Sayım salt denetim izidir:
hangi kurulumun üretileceğini ve sırasını etkilemez.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any, Mapping

import numpy as np
import pandas as pd

from core.config import get_setting
from core.indicators import bars_until, ema_series
from strategies.base import Direction, MarketData

# Sayım kodları — sıra ön-kayıtlıdır (§6i > 3) ve `scan` onu bu sırayla uygular.
NO_DATA = "no_data"
NO_REGIME = "no_regime"
CANDLE_FAILS = "candle_fails"
CROSS_NOT_VISIBLE = "cross_not_visible"
TARGET_UNDEFINED = "target_undefined"
TARGET_PASSED = "target_passed"
SETUP = "setup"

SURVEY_CODES: tuple[str, ...] = (
    NO_DATA, NO_REGIME, CANDLE_FAILS, CROSS_NOT_VISIBLE, TARGET_UNDEFINED, TARGET_PASSED, SETUP,
)

# Kurulum barı olup (rejim + mum tuttu) kesişimi görülen ya da görülemeyen her şey. Payda
# `cross_not_visible`in %5 eşiği içindir (§6i > 6) ve kurulum barlarıdır, rejim barları değil.
SETUP_BAR_CODES: tuple[str, ...] = (CROSS_NOT_VISIBLE, TARGET_UNDEFINED, TARGET_PASSED, SETUP)

ARM = "dc_pullback"


@dataclass(frozen=True, kw_only=True)
class DcRules:
    """Ön-kayıtlı parametreler (`config.yaml > dc`). Süpürülmez (§6i > 12)."""

    fast_period: int
    slow_period: int
    warmup_bars: int
    lookback_bars: int

    @classmethod
    def from_config(cls, config: Mapping[str, Any]) -> "DcRules":
        rules = cls(
            fast_period=int(get_setting(config, "dc.fast_period")),
            slow_period=int(get_setting(config, "dc.slow_period")),
            warmup_bars=int(get_setting(config, "dc.warmup_bars")),
            lookback_bars=int(get_setting(config, "dc.lookback_bars")),
        )
        if not 0 < rules.fast_period < rules.slow_period:
            raise ValueError(
                f"dc: fast_period ({rules.fast_period}) slow_period'dan ({rules.slow_period}) küçük olmalı"
            )
        if rules.warmup_bars < rules.slow_period:
            raise ValueError(
                f"dc.warmup_bars ({rules.warmup_bars}) EMA{rules.slow_period}'ün tohumundan kısa olamaz"
            )
        if rules.lookback_bars <= rules.warmup_bars + 1:
            raise ValueError(
                f"dc.lookback_bars ({rules.lookback_bars}) ısınmayı ({rules.warmup_bars}) aşmalı"
            )
        return rules


@dataclass(frozen=True, kw_only=True)
class DcSetup:
    """Bir kurulum ve GEOMETRİSİ. Yön ve seviyeler `reflect` ile aynalanabilir."""

    symbol: str
    bar: pd.Timestamp
    cross_bar: pd.Timestamp
    direction: Direction
    close: float
    stop_price: float
    target_price: float

    @property
    def stop_distance(self) -> float:
        return abs(self.close - self.stop_price)

    @property
    def target_distance(self) -> float:
        return abs(self.target_price - self.close)

    @property
    def reward_risk(self) -> float:
        """Hedef mesafesi ÷ stop mesafesi — kurulum kapanışından ölçülür (§6i > 3)."""
        return self.target_distance / self.stop_distance


def reflect(setup: DcSetup) -> DcSetup:
    """Yönü çevirir, stop ve hedef MESAFELERİNİ kurulum kapanışı etrafında aynalar.

    `stop_ters = close + (close − stop)`, `hedef_ters = close + (close − hedef)` (§6i > 4).
    Kapanış etrafında aynalanır çünkü doğrulamanın ve tavan kapısının referansı odur
    (`core/engine.py::_reference_price`): `|close − stop|` korunduğu için tavan kapısı iki
    modelde birebir aynı çalışır. Geometri yalnızca BURADA yazılıdır; kontrol onu yeniden
    kurmaz.
    """
    flipped: Direction = "long" if setup.direction == "short" else "short"
    return replace(
        setup,
        direction=flipped,
        stop_price=setup.close + (setup.close - setup.stop_price),
        target_price=setup.close + (setup.close - setup.target_price),
    )


@dataclass(frozen=True, kw_only=True)
class ScanResult:
    """Bir BARIN taraması: kurulumlar (evren sırasıyla) + her sembolün sayım kodu."""

    setups: tuple[DcSetup, ...]
    counts: Mapping[str, int] = field(default_factory=dict)

    @property
    def examined(self) -> int:
        return sum(self.counts.values())


def scan(market: MarketData, rules: DcRules) -> ScanResult:
    """`market.as_of` barında her sembolü tarar; sıra `market.ohlcv`nin sırasıdır.

    `market.ohlcv`nin sırası katmanın evren listesinin sırasıdır (`core/data.py` onu
    evrenden kurar, `core/engine.py::_snapshot` sırayı korur) — kota bağladığında hangi
    sinyalin dolacağı bu sıraya bağlıdır ve model ile kontrol aynı sırayı görür.
    """
    counts = {code: 0 for code in SURVEY_CODES}
    setups: list[DcSetup] = []
    for symbol, frame in market.ohlcv.items():
        code, setup = evaluate(symbol, frame, as_of=market.as_of, rules=rules)
        counts[code] += 1
        if setup is not None:
            setups.append(setup)
    return ScanResult(setups=tuple(setups), counts={k: v for k, v in counts.items() if v})


def evaluate(
    symbol: str, frame: pd.DataFrame, *, as_of: pd.Timestamp, rules: DcRules
) -> tuple[str, DcSetup | None]:
    """Tek sembolün `as_of` barındaki kodu ve (varsa) kurulumu. Sıra ön-kayıtlıdır."""
    window = bars_until(frame, as_of).tail(rules.lookback_bars)
    if window.empty or window.index[-1] != as_of or len(window) < rules.warmup_bars + 2:
        return NO_DATA, None

    close = window["close"]
    fast = ema_series(close, rules.fast_period).to_numpy()
    slow = ema_series(close, rules.slow_period).to_numpy()
    t = len(window) - 1

    if not fast[t] < slow[t]:
        return NO_REGIME, None

    high_t = float(window["high"].iloc[t])
    close_t = float(close.iloc[t])
    open_t = float(window["open"].iloc[t])
    if not (high_t >= fast[t] and close_t < fast[t] and close_t < open_t):
        return CANDLE_FAILS, None

    cross = _cross_index(fast, slow, t=t, warmup=rules.warmup_bars)
    if cross is None:
        return CROSS_NOT_VISIBLE, None
    if cross == t:
        return TARGET_UNDEFINED, None

    target = float(window["low"].iloc[cross:t].min())
    if close_t <= target:
        return TARGET_PASSED, None

    return SETUP, DcSetup(
        symbol=symbol,
        bar=window.index[t],
        cross_bar=window.index[cross],
        direction="short",
        close=close_t,
        stop_price=float(slow[t]),
        target_price=target,
    )


def _cross_index(fast: np.ndarray, slow: np.ndarray, *, t: int, warmup: int) -> int | None:
    """Bar t'de aktif olan rejimin kesişim barı; ısınma sınırından ÖNCEYSE None.

    t'den geriye `EMA50 < EMA200` sürdüğü müddetçe yürünür. Durumun ilk bozulduğu bar
    j ise kesişim c = j + 1'dir ve **j de ısınma sınırının İÇİNDE olmalıdır** — kesişimin
    tanımı iki barın karşılaştırmasıdır ve ikisi de yakınsamış EMA ile ölçülmelidir.
    """
    below = fast[warmup : t + 1] < slow[warmup : t + 1]
    breaks = np.flatnonzero(~below)
    if breaks.size == 0:
        return None
    return warmup + int(breaks[-1]) + 1

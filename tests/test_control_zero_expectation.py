"""SIFIR-BEKLENTİ testi — eşlenmiş kontrollerin değişmezi (docs/backtest.md > 6n > 7, karar 65).

Sürüklenmesiz bir rastgele yürüyüşte bilgisiz bir girişin beklenen R'si maliyetsizken 0,
maliyetle −(maliyet/R)'dir — HER pozisyon sonlu sürede kapandığı sürece. Bu test motorun
KENDİSİNİ koşar (`core/engine.py`; ikinci bir simülatör yoktur) ve üç şeyi sınar:

- **(a)** maliyet sıfırken `|ort(R_düz)| ≤ 0.05`;
- **(b)** canlı maliyet config'iyle `|ort(R_düz) + ort(cost_per_r)| ≤ 0.05`;
- **(c)** SINIRLI tutuş: pencere sonunda açık hiçbir pozisyon 200 bardan yaşlı değil ve
  kapanmış pozisyonların azami tutuşu ≤ 200 bar;
- **geçerlilik:** ≥ 1000 kapanmış pozisyon ve `ort(R_düz)`in bootstrap SE'si ≤ 0.0125
  (tolerans ≥ 4 SE). Sağlanmazsa test TASARIM hatasıyla düşer, "geçti" sayılmaz.

`random_ctrl` aynı testten KALIR ((a) ve (c)) — karar 60'ın sansürünün kalıcı belgesi.

**Kural 13 kâhini (`R_düz`).** Aynı barda hem stop hem hedef aralıktaysa motor stop'u
varsayar; bu, sürüklenmesiz veride beklentiyi kontrolden bağımsız olarak sıfırın altına
iter. Sentetik veri her barın alt adım yolunu sakladığı için test hangi seviyeye ÖNCE
değildiğini okur ve yalnızca o işlemlerin R'sini düzeltir. Pozisyon iki durumda da o barda
kapandığı için sonraki yol değişmez. Motor ve kural 13 DEĞİŞMEZ.

**Sabitler ön-kayıtlıdır ve DEĞİŞTİRİLEMEZ** (§6n > 10) — tohum dâhil: test deterministiktir
ve "başka tohumla dene" düşen bir testi geçene çevirmenin yoludur.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pytest

from core.config import load_config
from core.engine import Engine
from core.indicators import bollinger
from core.layers import resolve_layer
from core.ledger import Ledger
from core.metrics import merge_fills, r_multiple
from core.portfolio import Portfolio
from strategies import meanrev
from strategies.base import MarketData
from strategies.meanrev_random import MeanrevRandom
from strategies.random_ctrl import RandomControl
from strategies.trend_random import TrendRandom

# --- §6n > 7: ön-kayıtlı sabitler (DEĞİŞTİRİLMEZ) ---------------------------------------
SYMBOLS = 8
BARS = 5000
START_PRICE = 1000.0
SIGMA_PER_BAR = 5.0
SUBSTEPS = 16
SEED = 20260926
TOLERANCE_R = 0.05
MAX_AGE_BARS = 200
MIN_POSITIONS = 1000
MAX_SE = 0.0125
BOOTSTRAP = 2000
# -------------------------------------------------------------------------------------------

WARMUP = 40
STEP = pd.Timedelta(hours=4)
T0 = pd.Timestamp("2020-01-01 00:00:00", tz="UTC")


@dataclass(frozen=True)
class Synthetic:
    frames: dict[str, pd.DataFrame]
    paths: dict[str, np.ndarray]      # sembol -> (BARS, SUBSTEPS + 1): open + alt adımlar


def _synthetic() -> Synthetic:
    rng = np.random.default_rng(SEED)
    index = pd.date_range(T0, periods=BARS, freq="4h", tz="UTC", name="ts")
    frames: dict[str, pd.DataFrame] = {}
    paths: dict[str, np.ndarray] = {}
    for i in range(SYMBOLS):
        steps = rng.normal(0.0, SIGMA_PER_BAR / math.sqrt(SUBSTEPS), size=(BARS, SUBSTEPS))
        closes_flat = START_PRICE + np.cumsum(steps.ravel())
        within = closes_flat.reshape(BARS, SUBSTEPS)
        opens = np.concatenate([[START_PRICE], within[:-1, -1]])   # boşluk yok
        path = np.column_stack([opens, within])
        paths_i = path
        frames[f"R{i}-USDT-SWAP"] = pd.DataFrame(
            {
                "open": opens,
                "high": path.max(axis=1),
                "low": path.min(axis=1),
                "close": within[:, -1],
                "volume": np.ones(BARS),
            },
            index=index,
        )
        paths[f"R{i}-USDT-SWAP"] = paths_i
    assert min(float(f["low"].min()) for f in frames.values()) > 0.0, "fiyat sıfıra indi"
    return Synthetic(frames=frames, paths=paths)


def _market(data: Synthetic, upto: int) -> MarketData:
    ohlcv = {symbol: frame.iloc[:upto] for symbol, frame in data.frames.items()}
    reference = next(iter(ohlcv.values()))
    return MarketData(ohlcv=ohlcv, btc=reference, funding={}, as_of=reference.index[-1])


def _config(*, costs: bool) -> dict[str, Any]:
    config = dict(resolve_layer(load_config(), "base").config)
    config["signals_per_bar"] = True
    config["funding"] = {**config["funding"], "enabled": False}
    if not costs:
        config.update(fee_rate=0.0, slippage_base=0.0, slippage_short_stop=0.0)
    return config


@dataclass(frozen=True)
class Outcome:
    r_engine: np.ndarray
    r_corrected: np.ndarray
    cost_per_r: np.ndarray
    corrected_exits: int
    max_closed_age: float
    max_open_age: float


def _run(tmp: Path, *, costs: bool) -> tuple[dict[str, Outcome], dict[str, Any]]:
    data = _synthetic()
    config = _config(costs=costs)
    models = [TrendRandom(config=config), MeanrevRandom(config=config), RandomControl(config=config)]
    ledger = Ledger(tmp)
    engine = Engine(models, config=config, ledger=ledger, portfolio=Portfolio(config))
    engine.run_round(_market(data, WARMUP))     # boş defterde yalnızca son bar işlenir
    engine.run_round(_market(data, BARS))
    end = data.frames[next(iter(data.frames))].index[-1]
    return {m.name: _outcome(ledger, m.name, data, config, end) for m in models}, config


def _outcome(ledger: Ledger, model: str, data: Synthetic, config: dict[str, Any],
             end: pd.Timestamp) -> Outcome:
    positions = merge_fills(ledger.read_trades(model))
    fee = float(config["fee_rate"])
    slip = float(config["slippage_base"])
    r_engine, r_corrected, costs = [], [], []
    corrected = 0
    ages = []
    for row in positions:
        r = r_multiple(row)
        if r is None:
            continue
        risk = float(row["risk_amount"])
        cost = float(row["fee"]) + float(row["slippage_cost"])
        pnl = float(row["pnl"])
        opened, closed = pd.Timestamp(row["opened_at"]), pd.Timestamp(row["closed_at"])
        ages.append((closed - opened) / STEP)
        if model == MeanrevRandom.name and row["exit_reason"] == "stop":
            fix = _oracle(row, data, fee=fee, slip=slip)
            if fix is not None:
                pnl += fix[0]
                cost += fix[1]
                corrected += 1
        r_engine.append(r)
        r_corrected.append(pnl / risk)
        costs.append(cost / risk)
    open_ages = [
        (end - pd.Timestamp(p["opened_at"])) / STEP
        for p in (ledger.load_state(model) or {}).get("positions", [])
    ]
    return Outcome(
        r_engine=np.array(r_engine), r_corrected=np.array(r_corrected),
        cost_per_r=np.array(costs), corrected_exits=corrected,
        max_closed_age=max(ages, default=0.0), max_open_age=max(open_ages, default=0.0),
    )


def _oracle(row: dict[str, Any], data: Synthetic, *, fee: float, slip: float
            ) -> tuple[float, float] | None:
    """Kural 13'ün bağladığı bir stop çıkışında hedef ÖNCE değdiyse (Δpnl, Δmaliyet); değilse None.

    Hedef, sinyal barının kapanışı ve orta bandından modelin KENDİ kuralıyla yeniden kurulur
    (defter hedefi yazmaz). Hedef dolumu motorun sözleşmesiyle fiyatlanır: seviye × (1 ∓ kayma),
    komisyon dolum notional'ı üzerinden.
    """
    symbol = row["symbol"]
    frame = data.frames[symbol]
    opened, closed = pd.Timestamp(row["opened_at"]), pd.Timestamp(row["closed_at"])
    signal_bar = frame.loc[:opened - STEP]
    close = float(signal_bar["close"].iloc[-1])
    bands = bollinger(signal_bar["close"], meanrev.BOLLINGER_PERIOD, meanrev.BOLLINGER_STD)
    side = 1.0 if row["direction"] == "long" else -1.0
    target = close + side * abs(bands.middle - close)
    stop = float(row["stop_price"])

    bar = frame.index.get_loc(closed)
    path = data.paths[symbol][bar]
    if not (float(path.min()) <= min(stop, target) and float(path.max()) >= max(stop, target)):
        return None                       # belirsiz değil: motorun cevabı zaten doğru
    for price in path:
        if side * (price - target) >= 0.0:
            break                         # hedef önce
        if side * (price - stop) <= 0.0:
            return None                   # stop önce: motor haklı
    qty = float(row["qty"])
    exit_stop = float(row["exit_price"])
    exit_tp = target * (1.0 - side * slip)
    delta_pnl = side * qty * (exit_tp - exit_stop) - fee * qty * (exit_tp - exit_stop)
    old_cost = fee * qty * exit_stop + abs(exit_stop - stop) * qty
    new_cost = fee * qty * exit_tp + abs(exit_tp - target) * qty
    return delta_pnl, new_cost - old_cost


def _bootstrap_se(values: np.ndarray) -> float:
    rng = np.random.default_rng(SEED)
    draws = rng.integers(0, len(values), size=(BOOTSTRAP, len(values)))
    return float(values[draws].mean(axis=1).std(ddof=1))


@pytest.fixture(scope="module")
def zero_cost(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Outcome]:
    return _run(tmp_path_factory.mktemp("zero"), costs=False)[0]


@pytest.fixture(scope="module")
def live_cost(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Outcome]:
    return _run(tmp_path_factory.mktemp("live"), costs=True)[0]


def _report(name: str, outcome: Outcome) -> str:
    return (
        f"{name}: n={len(outcome.r_corrected)} ort(R)={outcome.r_engine.mean():+.4f} "
        f"ort(R_düz)={outcome.r_corrected.mean():+.4f} kural13 bedeli="
        f"{outcome.r_engine.mean() - outcome.r_corrected.mean():+.4f} "
        f"düzeltilen={outcome.corrected_exits} ort(cost/R)={outcome.cost_per_r.mean():.4f} "
        f"azami yaş kapanmış/açık={outcome.max_closed_age:.0f}/{outcome.max_open_age:.0f}"
    )


def _assert_valid(name: str, outcome: Outcome) -> None:
    n = len(outcome.r_corrected)
    assert n >= MIN_POSITIONS, f"TASARIM HATASI — örneklem yetersiz: {_report(name, outcome)}"
    se = _bootstrap_se(outcome.r_corrected)
    assert se <= MAX_SE, f"TASARIM HATASI — SE {se:.4f} > {MAX_SE}: {_report(name, outcome)}"


@pytest.mark.parametrize("name", [TrendRandom.name, MeanrevRandom.name])
def test_a_zero_cost_mean_r_is_zero(zero_cost: dict[str, Outcome], name: str) -> None:
    outcome = zero_cost[name]
    _assert_valid(name, outcome)
    assert abs(outcome.r_corrected.mean()) <= TOLERANCE_R, _report(name, outcome)


@pytest.mark.parametrize("name", [TrendRandom.name, MeanrevRandom.name])
def test_b_live_cost_mean_r_is_minus_cost_per_r(live_cost: dict[str, Outcome], name: str) -> None:
    outcome = live_cost[name]
    _assert_valid(name, outcome)
    gap = outcome.r_corrected.mean() + outcome.cost_per_r.mean()
    assert abs(gap) <= TOLERANCE_R, f"fark {gap:+.4f} — {_report(name, outcome)}"


@pytest.mark.parametrize("name", [TrendRandom.name, MeanrevRandom.name])
def test_c_every_position_closes_in_bounded_time(zero_cost: dict[str, Outcome], name: str) -> None:
    outcome = zero_cost[name]
    assert outcome.max_open_age <= MAX_AGE_BARS, _report(name, outcome)
    assert outcome.max_closed_age <= MAX_AGE_BARS, _report(name, outcome)


def test_random_ctrl_fails_the_same_test(zero_cost: dict[str, Outcome]) -> None:
    """Karar 60'ın kalıcı belgesi: yalnızca stop'la kapanan kontrol sansürlüdür."""
    outcome = zero_cost[RandomControl.name]
    assert outcome.r_corrected.mean() <= -0.5, _report(RandomControl.name, outcome)
    assert outcome.max_open_age > MAX_AGE_BARS, _report(RandomControl.name, outcome)

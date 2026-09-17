"""Canlıya alma ÖN KOŞULLARI — maliyet modelinin gerçekten uygulandığı.

Bu dosya bir modeli değil, **defterin maliyet varsayımını** çiviler. Gerekçe: canlı
işleme geçmeden önce sorulan ilk soru "her işlem taker ücretini ve kaymayı ödüyor mu"
oldu ve cevabın "evet" olduğu yalnızca `config.yaml`ın yorum satırlarında yazılıydı.
Yorum bir garanti değildir; bu testler garantidir.

Ölçüt somut bir referans emirdir: **5.000 USDT notional.** Bu, kopyanın (model 13) sabit
teminat × kaldıraç boyutlandırmasının başlangıç sermayesindeki karşılığıdır (0.5 × 10.000
/ 10x = 500 USDT teminat) ve risk boyutlandıran modellerde %2 stop mesafesinin ürettiği
büyüklüktür — yani iki boyutlandırma kuralının ortak ölçeği.

**Dolum kuralı (bir sonraki barın açılışı) burada TEKRAR test edilmez:** motorun kendi
testlerinde sabittir (`tests/test_engine.py::test_pending_order_fills_at_the_next_bar_open`,
`tests/test_engine_per_bar.py::test_each_bar_order_fills_at_the_next_bar_open`). İkinci bir
uygulama değil, ikinci bir test de istemiyoruz: kuralın tek yeri motordur.
"""

from __future__ import annotations

from typing import Any

import pandas as pd
import pytest

from core.config import load_config
from core.portfolio import Bar, Portfolio

TS = pd.Timestamp("2026-01-01 00:00:00", tz="UTC")
SYMBOL = "BTC-USDT-SWAP"
REFERENCE_NOTIONAL = 5000.0


@pytest.fixture()
def config() -> dict[str, Any]:
    return load_config()


def test_round_trip_taker_fee_on_the_reference_order_is_at_least_five_usdt(
    config: dict[str, Any]
) -> None:
    """İki bacak da taker: modeller `entry_type="market"` ile girip çıkar."""
    fee_rate = float(config["fee_rate"])

    assert 2.0 * fee_rate * REFERENCE_NOTIONAL >= 5.0


def test_base_slippage_is_between_two_and_five_basis_points(config: dict[str, Any]) -> None:
    """Kayma yönden bağımsız olarak HER dolumda uygulanır; short stop'ta daha kötüdür."""
    assert 0.0002 <= float(config["slippage_base"]) <= 0.0005
    assert float(config["slippage_short_stop"]) > float(config["slippage_base"])


def test_every_fill_pays_both_fee_and_slippage(config: dict[str, Any]) -> None:
    """Gerçek config ile açılıp kapanan bir pozisyon: ücret ve kayma deftere YAZILIR.

    Boyut kural 11'den gelir: %1 risk (100 USDT) ve %2 stop mesafesi tam olarak 5.000
    notional eder — yani referans emir, uydurulmuş bir sayı değil boyutlandırma
    kuralının kendisinden çıkar.
    """
    portfolio = Portfolio(config)
    result = portfolio.open_position(
        "m", symbol=SYMBOL, direction="long", stop_price=98.0,
        reference_price=100.0, ts=TS, marks={SYMBOL: 100.0},
    )
    position = result.position
    assert position is not None
    # Tolerans kaymanın kendisidir: giriş referans fiyattan değil, KAYMIŞ fiyattan
    # dolar (100.05), yani stop mesafesi %2'den biraz geniştir ve boyut bir tık
    # küçüktür. Sapmanın yönü doğrudur — kayma boyutu büyütseydi maliyet modeli
    # işlemi olduğundan ucuz gösterirdi.
    assert position.qty * 100.0 == pytest.approx(REFERENCE_NOTIONAL, rel=0.03)
    assert position.qty * 100.0 < REFERENCE_NOTIONAL
    # Giriş, referans fiyatın ALEYHTE tarafında doldu: kayma bir varsayım değil, fiyatın
    # kendisidir.
    assert position.entry_price > 100.0

    trades = portfolio.process_bar(
        "m", ts=TS + pd.Timedelta("15min"), bars={SYMBOL: Bar(open=100.0, high=101.0, low=97.0, close=98.0)}
    )

    assert len(trades) == 1
    trade = trades[0]
    assert trade.fee >= 5.0
    assert trade.slippage_cost > 0.0
    # Ücret ve kayma AYRI kolonlardır: toplanıp tek sayıya indirilselerdi
    # `core/metrics.py::cost_per_r` hangi varsayımın ne kadar yediğini söyleyemezdi.
    assert trade.fee != trade.slippage_cost


def test_costs_are_layer_independent(config: dict[str, Any]) -> None:
    """İki katman da AYNI maliyeti görür (kural 6): katman bloğunda maliyet anahtarı yoktur."""
    from core.layers import resolve_layer

    base = resolve_layer(load_config(), "base").config
    scalp = resolve_layer(load_config(), "scalp").config

    for key in ("fee_rate", "slippage_base", "slippage_short_stop", "risk_per_trade",
                "leverage_cap", "initial_capital", "maintenance_margin"):
        assert base[key] == scalp[key] == config[key], key

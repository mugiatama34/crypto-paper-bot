"""Göstergelerin TEK uygulaması: ATR, RSI, Bollinger, EMA, SMA, Donchian.

Neden ortak modül: aynı göstergenin iki farklı uygulaması iki modelin aynı barda farklı
sayı görmesi demektir — bu da kural 5'i ("tüm stratejiler aynı anlık görüntüyü görür")
sayısal düzeyde deler. `generate_signals` içinde "hızlıca" yazılmış bir RSI, modellerin
sinyal farkını gösterge farkıyla karıştırır ve kıyası geçersizleştirir. Bu yüzden hiçbir
strateji kendi gösterge matematiğini yazmaz; hepsi buradan okur.

Bu modül SALT OKUNUR ve durumsuzdur: config okumaz, bakiye/pozisyon bilmez (kural 7),
yalnızca verilen çerçeveden sayı üretir. Periyot gibi parametreler çağıranın sorumluluğudur
— ATR periyodu projenin tek ATR tanımıdır ve `config.yaml`'ın `trailing.atr_period`
değerinden gelir (bkz. CLAUDE.md, "config.yaml Değerleri").

Tanım kararları (hepsi aynı gerekçeye dayanır: **denetlenebilirlik > gelenek**):

- **Yeterli bar yoksa `None` döner, kısmi pencereyle hesaplanmış bir sayı değil.** Yeni
  listelenmiş bir sembolde 30 barlık veriyle "200 EMA" üretmek, o sembolde rejim filtresini
  sessizce anlamsız kılardı; `None` çağıranı sinyal üretmemeye zorlar.
- **Pencere-yerel ortalamalar (ATR, RSI) Wilder yumuşatması kullanmaz.** Wilder özyinelemeli
  olduğu için sonuç çerçeveye kaç bar geçmiş verildiğine bağlıdır; önbellek farklı ısındığında
  ya da sembol geç listelendiğinde aynı bar için farklı sayı çıkar. Basit ortalama yalnızca
  son `period` bara bakar: aynı bar her zaman aynı sayıyı verir.
- **EMA'da bu kaçış yok** — EMA tanımı gereği özyinelemelidir. Bu yüzden başlangıç değeri
  açıkça ilk `period` barın SMA'sına sabitlenmiştir (pandas `ewm` varsayılanına bırakılmaz):
  aynı çerçeve her koşuda aynı EMA'yı verir ve tanım tek satırda denetlenebilir.
- **Bollinger sapması POPÜLASYON standart sapmasıdır (ddof=0).** Örneklem sapması (ddof=1)
  20 barlık pencerede bandı ~%2.6 genişletir; hangisi "doğru" olduğu değil, tek ve yazılı
  olması önemlidir.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass(frozen=True, kw_only=True)
class BollingerBands:
    upper: float
    middle: float
    lower: float


@dataclass(frozen=True, kw_only=True)
class DonchianChannel:
    upper: float
    lower: float


def bars_until(frame: pd.DataFrame, as_of: pd.Timestamp) -> pd.DataFrame:
    """`as_of` dâhil, sonrasını atan dilim — look-ahead yasağının (kural 12) tek satırı.

    Stratejilerin `frame.tail(...)` ya da `iloc[-1]` ile "şimdi"yi tahmin etmesi kuralın en
    sık delindiği yerdir: anlık görüntü doğruysa ikisi aynı sonucu verir, bir sembol bir bar
    ileri kaçtığında vermez. Kesme tek yerde durursa hata da tek yerde aranır.
    """
    return frame.loc[:as_of]


def sma(series: pd.Series, period: int) -> float | None:
    """Son `period` değerin basit ortalaması; yeterli veri yoksa None."""
    _require_positive(period, "period")
    if len(series) < period:
        return None
    return float(series.to_numpy(dtype="float64")[-period:].mean())


def ema(series: pd.Series, period: int) -> float | None:
    """Üstel hareketli ortalama; ilk `period` barın SMA'sı ile tohumlanır.

    Tohumun açıkça yazılmasının nedeni modül docstring'inde: EMA özyinelemelidir ve
    başlangıç değeri kütüphane varsayılanına bırakılırsa tanım kodda görünmez olur.
    """
    _require_positive(period, "period")
    values = series.to_numpy(dtype="float64")
    if len(values) < period:
        return None
    alpha = 2.0 / (period + 1.0)
    current = float(values[:period].mean())
    for value in values[period:]:
        current = alpha * float(value) + (1.0 - alpha) * current
    return current


def rsi(series: pd.Series, period: int) -> float | None:
    """Son `period` değişimin kazanç/kayıp ortalamasından RSI; yeterli veri yoksa None.

    Kayıp ortalaması sıfırsa (kesintisiz yükseliş) RSI 100.0; her iki ortalama da sıfırsa
    (fiyat hiç değişmemiş) 50.0 döner — 100 demek, hareketsiz bir seriyi "azami aşırı alım"
    ilan etmek olurdu.
    """
    _require_positive(period, "period")
    values = series.to_numpy(dtype="float64")
    if len(values) < period + 1:
        return None
    changes = np.diff(values[-(period + 1):])
    average_gain = float(np.clip(changes, 0.0, None).mean())
    average_loss = float(np.clip(-changes, 0.0, None).mean())
    if average_loss == 0.0:
        return 50.0 if average_gain == 0.0 else 100.0
    relative_strength = average_gain / average_loss
    return 100.0 - 100.0 / (1.0 + relative_strength)


def bollinger(series: pd.Series, period: int, num_std: float) -> BollingerBands | None:
    """Orta bant = SMA(period); bantlar = orta ± num_std × popülasyon sapması."""
    _require_positive(period, "period")
    if num_std <= 0.0:
        raise ValueError(f"num_std pozitif olmalı: {num_std}")
    values = series.to_numpy(dtype="float64")
    if len(values) < period:
        return None
    window = values[-period:]
    middle = float(window.mean())
    deviation = float(window.std(ddof=0)) * num_std
    return BollingerBands(upper=middle + deviation, middle=middle, lower=middle - deviation)


def donchian(frame: pd.DataFrame, period: int) -> DonchianChannel | None:
    """Son barı HARİÇ tutan `period` barlık en yüksek zirve / en düşük dip.

    Son barın dışlanması bir ayrıntı değil, tanımın kendisidir: kanal son barı da içerseydi
    `upper >= high >= close` olurdu ve "tepenin üstüne kapanış" koşulu yalnızca kapanışın
    tam olarak barın zirvesine eşit olduğu durumda sağlanabilirdi — yani kırılım pratikte
    hiç oluşmazdı. Kanal, kırılımı ölçülen barın GEÇMİŞİDİR.
    """
    _require_positive(period, "period")
    if len(frame) < period + 1:
        return None
    window = frame.iloc[-(period + 1):-1]
    return DonchianChannel(
        upper=float(window["high"].to_numpy(dtype="float64").max()),
        lower=float(window["low"].to_numpy(dtype="float64").min()),
    )


def average_true_range(frame: pd.DataFrame, period: int) -> float | None:
    """Son `period` kapanmış barın ortalama gerçek aralığı; yeterli bar yoksa None.

    Basit ortalama kullanılır (Wilder yumuşatması değil): trailing mesafesinin tek amacı
    tüm modeller için AYNI ve denetlenebilir olması; yumuşatma seçimi ölçümü etkilemez
    ama tanımın açık olması etkiler.
    """
    if period <= 0:
        raise ValueError(f"atr_period pozitif olmalı: {period}")
    if len(frame) < period + 1:
        return None
    window = frame.tail(period + 1)
    high = window["high"].to_numpy(dtype="float64")
    low = window["low"].to_numpy(dtype="float64")
    close = window["close"].to_numpy(dtype="float64")
    previous_close = close[:-1]
    true_range = np.maximum(
        high[1:] - low[1:],
        np.maximum(np.abs(high[1:] - previous_close), np.abs(low[1:] - previous_close)),
    )
    return float(true_range.mean())


def _require_positive(value: int, name: str) -> None:
    if value <= 0:
        raise ValueError(f"{name} pozitif olmalı: {value}")

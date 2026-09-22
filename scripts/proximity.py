"""YAKINLIK TARAMASI — "bir sonraki bar şu fiyatta kapanırsa bu model sinyal üretir".

**Bu bir TAHMİN DEĞİLDİR** ve ölçümün parçası değildir: salt okunur, deftere yazmaz,
`config.yaml`a dokunmaz, önbelleğe yazmaz, hiçbir modelin davranışını ve hiçbir sinyalin
sırasını değiştirmez. `survey`/`emitted`/`rejections` ile AYNI statüde bir DENETİM
İZİDİR (kural 15): "bugün sinyal yok" satırının arkasında ne olduğunu — kurulum fersah
fersah mı uzaktı, yoksa kıl payı mı kaçırdı — gösterir.

Cevapladığı soru: *son kapanışın hangi fiyat seviyesinde bir sonraki bar kapansaydı bu
model bir sinyal üretirdi?* Cevap bir OLASILIK değil, bir KOŞULDUR.

## Temel ilke: İKİNCİ UYGULAMA YOK

Yakınlık, modelin KENDİ sinyal kodu hipotetik bir sonraki barla çağrılarak bulunur.
İndikatör matematiğini (RSI'ı tersine çözmek, Donchian eşiğini elle hesaplamak, EMA
kesişimini analitik çözmek) burada ayrıca yazmak YASAKTIR: iki uygulama bir gün
ayrışır ve ekran, modelin gerçekte üretmeyeceği bir sinyali "yaklaşıyor" diye gösterir —
yani araç tam da engellemek için var olduğu şeyi üretir. Aynı gerekçe
`scripts/measure_vwap_signal.py`nin `--verify` kapısında ve `scripts/backtest.py`nin
"ikinci bir motor yazmaz" kuralında yazılıdır.

Sonda modelin KENDİSİNİ değil, kurulduğunda aldığı **DERİN KOPYASINI** çağırır (`Probe`).
Çoğu çağrı zaten yan etkisizdir (bkz. aşağıdaki tablo), ama ikisi kendi alanına yazar
(`_survey`) ve biri defterden öğrenir (`vwap_clone`). Kopya, "tarama turu değiştiremez"
sözünü çağrı yolunun ayrıntısına değil TEK BİR SATIRA bağlar: bir gün `generate_signals`
yeni bir alana yazmaya başlasa bile söz tutmaya devam eder (test:
`tests/test_proximity.py`).

| Model | Çağrılan yol | Neden güvenli |
|---|---|---|
| `trend`, `meanrev`, `ema_trend` | `generate_signals` | saf: config sabitlerini ve çerçeveyi okur, hiçbir alana yazmaz |
| `vwap_clone` | `observe_closed_trades` → `generate_signals` | RNG her çağrıda sıfırdan kurulur; öğrenici durumu OKUNUR, yazılmaz. Besleme motorun yaptığının aynısıdır — beslenmezse combo (dolayısıyla bant/stop/hedef) yanlış çıkar |
| `vwap_managed` | `vwap_signal.scan` → `stop_price`/`projected_target`/`nearest_target` → `_passes_gates` | `generate_signals` kapıda ölen adayı düşürür; "tetiklenir ama kapıda ölür" ancak modelin kendi kapı fonksiyonu AYRI çağrılarak görülebilir |
| `scalp_fixed`, `scalp_patient` | `arms.propose_all` → `_gated` → `regime_filter` | üçü de saf; `choose_arm`/RNG ÇAĞRILMAZ (bkz. "Seçim raporlanmaz") |

`scalp_bandit` kapsam dışıdır (emekli, karar 33). `vwap_clone`un öğrenicisi taramada
KARAR VERİR — kombinasyon seçimi onun işidir ve bant çarpanı stop/hedefi belirler — ama
DURUMU YALNIZCA OKUNUR: `observe_closed_trades` defteri okuyup kopyanın posteriorunu
kurar, tarama boyunca hiçbir şey ona yazmaz ve gerçek defter hiç değişmez.

## Seçim raporlanmaz, TETİKLEME raporlanır

`scalp_fixed`/`scalp_patient` bir turda kurulum üreten kollar arasından **çekilişle** bir
kol ve bir sembol seçer. Çekiliş `random_seed:as_of:rng_identity` ile deterministiktir ama
seçilen küme O BARDAKİ TÜM SEMBOLLERİN kurulumlarına bağlıdır — tarama sembolleri tek tek
hipotetik fiyatlandırdığı için o küme yeniden üretilemez. Bu yüzden çıktı **"hangi kollar
tetiklenir"i** söyler, "hangisi oynanır"ı DEĞİL (`selection` alanı). Aynı nedenle
`scalp_fixed` ile `scalp_patient` birebir aynı tetikleme listesini verir: ikisi
`rng_identity`yi paylaşır ve yalnızca ZAMAN STOP'unda ayrışır, o da çıkış tarafındadır.

## Kapı ≠ tetikleme (karar 34'ün dersi)

`momentum_burst` kolu katmanın tüm ömrü boyunca tek sinyal üretmedi ve sebebi ancak
ölçüldükten sonra anlaşıldı: kapı aritmetiği kolu imkânsız kılıyordu. Bu yüzden burada
**"tetiklenir ve kapıdan geçer" ile "tetiklenir ama kapıda ölür" AYRI** raporlanır. Kapı
KARARI her zaman modelin (ya da motorun) kendi kodundan gelir; çıktıdaki ölçülen değer ve
eşik yalnızca okuyucu için yazılır — ikinci bir yargı üretilmez.

Değerlendirilen kapılar: modelin ev kapıları (%1 stop tabanı, 1.5R) ve motorun stop
bandı tavanı (`core/engine.py::_within_stop_band`, kural 14). Motorun kapısı da
ÇAĞRILIR, kopyalanmaz.

## Varsayımlar (çıktıda AÇIKÇA yazılır)

Hipotetik bar: `open` = son kapanış, `close` = aday fiyat, `high`/`low` =
`max`/`min(open, close)`, `volume` = son 20 barın MEDYANI. Bar damgası `as_of + bar
süresi`dir.

- **Izgara, ikiye bölme DEĞİL:** koşullar fiyata göre monoton değildir (ör. RSI(2)
  kolunun aralık rejimi kapısı fiyat uzaklaştıkça KAPANIR). İkiye bölme bu koşullarda
  yanlış cevap verir. Aralık ve adım katmandan gelir (15m: ±%5 / %0.05, 4H: ±%15 / %0.1).
- **Her sembol TEK BAŞINA fiyatlandırılır:** anlık görüntüde yalnızca taranan sembol
  hipotetik barı taşır, diğerleri `as_of` barında kalır ve modeller onları zaten atlar
  (kural 12'nin aynı kesmesi). Yani çıktı "şu sembol şu fiyata giderse" der, "tüm piyasa
  şöyle kapanırsa" demez.
- **Fiyat DIŞI koşullar hipotetik bara taşınmaz:** BTC rejim kapısı (`meanrev`) son
  kapanıştan okunur, funding serisi olduğu gibi kalır. İkisi de çıktıda koşul olarak
  işaretlenir — varsayıldıkları yerde gizlenmezler.

## Veri: turun kullandığı AYNI önbellek

`core/data.py::load_cached_market_data` — parquet önbelleğini okur, borsaya İKİNCİ BİR
ÇAĞRI YAPMAZ ve diske yazmaz. Gerekçe ikili: ağ trafiği değil, `as_of` BİRLİĞİ. Tarama
kendi verisini çekseydi arada kapanan bir bar onu turun ölçtüğünden başka bir bara
oturturdu ve ekran ile defter aynı "şimdi"yi konuşmayı bırakırdı.

## Çıkış kodları (karar 51: boş rapor YEŞİL dönmez)

- `0` — en az bir katmanda en az bir sembol tarandı (tetik sayısı sıfır olabilir: "yok"
  da bir cevaptır ve açıkça kaydedilir).
- `2` — kullanım hatası (tanınmayan katman, kapsam dışı model).
- `3` — VERİ KAPISI: hiçbir katmanda tek bir sembol bile taranamadı. Rapor yazılmaz.
  `core/metrics.py`nin "veri yoksa `nan`, `0.0` değil" kuralının çıkış kodundaki
  karşılığı: yeşil bir koşu OKUNABİLİR bir rapor demektir, boş bir iskelet değil.
- `1` — beklenmeyen hata.

Tetikleyicisi `run.yml` ve `run-scalp.yml`de defter commit'inden SONRAKİ ayrı adımdır
(`continue-on-error: true`): yakınlık bir bildirimdir, ölçüm değil — burada ne olursa
olsun tur çoktan kaydedilmiştir.
"""

from __future__ import annotations

import argparse
import copy
import json
import logging
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

# Depo kökü `sys.path`e ALINIR (öteki betiklerin aynı kalıbı). Workflow betiği
# `python scripts/proximity.py` ile çağırır: o kipte `sys.path[0]` `scripts/`tir ve
# `core`/`strategies`/`main` görünmez. Testlerde görünüyor olması bunu maskeler —
# `pytest.ini` kökü zaten yola koyar — bu yüzden kapı da ayrıca testtedir
# (`tests/test_script_entrypoints.py`): betikler GERÇEKTEN çağrıldıkları gibi koşulur.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd  # noqa: E402

from core.config import ConfigError, get_setting, load_config  # noqa: E402
from core.data import bar_duration, load_cached_market_data  # noqa: E402
from core.engine import Engine  # noqa: E402
from core.indicators import average_true_range, sma  # noqa: E402
from core.layers import DEFAULT_LAYER, Layer, resolve_layer  # noqa: E402
from core.ledger import Ledger  # noqa: E402
from core.portfolio import Portfolio  # noqa: E402
from core.tags import find_tag  # noqa: E402
from main import build_strategies, jsonable  # noqa: E402
from strategies.base import MarketData, Signal, Strategy  # noqa: E402
from strategies.scalp import arms as scalp_arms  # noqa: E402
from strategies.scalp.model import ScalpModel  # noqa: E402
from strategies.vwap import signal as vwap_signal  # noqa: E402
from strategies.vwap_clone import VwapClone  # noqa: E402
from strategies.vwap_managed import VwapManaged  # noqa: E402

logger = logging.getLogger("proximity")

# KAPSAM (görev tanımı). `buyhold` sinyal üretmez, `random_ctrl` bilgisiz bir çekiliştir
# ve ikisinde de "yakınlık" tanımsızdır: çıpanın tetiği fiyattan bağımsızdır (her turda
# aynı iki sinyal), kontrolünki ise bir zar atışıdır — bir fiyat eşiği göstermek, orada
# olmayan bir kuralı varmış gibi çizmek olurdu.
#
# `scalp_coinflip` (23) aynı gerekçeyle kapsam DIŞIDIR ve bu, katmanın `models` listesinde
# olmasına rağmen böyledir: kurulumu `scalp_patient`inkiyle birebir aynıdır (zaten o
# satırda taranıyor), ayrıştığı tek şey ise bir YAZI-TURADIR. "Şu fiyatta long tetikler"
# demek, yönü fiyattan türüyormuş gibi göstermek olurdu — oysa fiyat yalnızca kurulumu
# belirler, yönü çekiliş belirler. Liste bir İZİN listesidir, yani kapsam dışı kalan model
# sessizce "bulunamadı"ya düşmez: `--models` ile istenirse açıkça hata verir.
SCOPE: Mapping[str, tuple[str, ...]] = {
    "base": ("trend", "meanrev"),
    "ema": ("ema_trend",),
    "scalp": ("scalp_fixed", "scalp_patient", "vwap_managed", "vwap_clone"),
}

# Izgara katmanın BARINA göre: 15 dakikalık bir barın makul aralığı 4 saatlikinkinden
# dardır ve aynı ızgarayı ikisine de uygulamak ya scalp'te gereksiz binlerce adım ya
# 4H'de görülemeyecek kadar dar bir pencere demekti.
_GRIDS: Mapping[str, tuple[float, float]] = {  # timeframe -> (menzil %, adım %)
    "15m": (5.0, 0.05),
}
_DEFAULT_GRID: tuple[float, float] = (15.0, 0.1)

# Hipotetik barın hacmi: son bu kadar barın MEDYANI. Ortalama değil — tek bir hacim
# patlaması ortalamayı yukarı çeker ve hacim teyitli kolları (ORB, momentum) olduğundan
# kolay tetiklenir gösterirdi.
VOLUME_LOOKBACK = 20

SELECTION_NOTE = (
    "Bu liste TETİKLENEN kolları gösterir, OYNANACAK kolu değil: model uygun kollar "
    "arasından çekilişle seçer ve çekiliş o bardaki TÜM sembollerin kurulumlarına "
    "bağlıdır (tarama sembolleri tek tek fiyatlandırır, o küme yeniden üretilemez)."
)


# --------------------------------------------------------------------------- #
# Çıktı kayıtları
# --------------------------------------------------------------------------- #
@dataclass(frozen=True, kw_only=True)
class Gate:
    """Bir kapının SONUCU — `passed` HER ZAMAN modelin ya da motorun kendi kodundan gelir.

    Bu araç hiçbir kapı kararını kendisi vermez. `house_gates` modelin kendi kapı
    fonksiyonunun (`ScalpModel._gated` + `regime_filter`, `VwapManaged._passes_gates`)
    dönüşüdür; `atr_ceiling` motorun `_within_stop_band`inin dönüşüdür. Eşiği burada
    yeniden karşılaştırmak, kapının bir gün modelde değişip ekranda değişmemesi demekti.
    """

    name: str
    passed: bool


@dataclass(frozen=True, kw_only=True)
class Measurement:
    """Bir kapının ÖLÇÜSÜ — yargı DEĞİL.

    Ayrı durmaları şart: `Gate.passed` modelin kararıdır, bu ise yalnızca "hangi sayı
    hangi eşiğe ne kadar uzaktı" sorusunu cevaplar. İkisini tek alanda toplamak,
    okuyucunun buradaki karşılaştırmayı kapının KENDİSİ sanmasına kapı bırakırdı —
    oysa kapı tek yerde tanımlıdır ve burada yalnızca GÖSTERİLİR.
    """

    name: str
    value: float | None
    threshold: float | None
    unit: str = ""


@dataclass(frozen=True, kw_only=True)
class Condition:
    """Fiyat DIŞI bir koşul: hipotetik bara taşınamayan, varsayılan bir şey.

    Gizlenmez, çünkü gizlendiği anda çıktı "bu fiyatta tetiklenir" derken aslında "bu
    fiyatta VE şu varsayım tutarsa tetiklenir" demiş olurdu.
    """

    name: str
    satisfied: bool | None
    detail: str


@dataclass(frozen=True, kw_only=True)
class Trigger:
    model: str
    symbol: str
    direction: str
    arm: str | None
    price_direction: str  # "up" | "down": aday fiyat son kapanışın üstünde mi altında mı
    trigger_price: float
    last_close: float
    distance_pct: float  # işaretli: + yukarı, − aşağı
    distance_atr: float | None
    atr: float | None
    stop_price: float | None
    target_price: float | None
    stop_distance_pct: float | None
    reward_risk: float | None
    gates_passed: bool
    gates: tuple[Gate, ...]
    measurements: tuple[Measurement, ...]
    conditions: tuple[Condition, ...]
    position_state: Mapping[str, Any]
    reason: str


@dataclass(frozen=True, kw_only=True)
class ModelScan:
    model: str
    selection: str | None  # çekilişi olan modellerde SELECTION_NOTE
    scanned_symbols: tuple[str, ...]
    triggers: tuple[Trigger, ...]
    no_trigger: tuple[str, ...]  # ızgarada hiçbir yönde tetik bulunamayan semboller
    position_state: Mapping[str, Any]


# --------------------------------------------------------------------------- #
# Hipotetik bar
# --------------------------------------------------------------------------- #
def assumed_volume(frame: pd.DataFrame, *, lookback: int = VOLUME_LOOKBACK) -> float:
    """Hipotetik barın hacmi: son `lookback` barın medyanı (yoksa son barın hacmi)."""
    volumes = frame["volume"].astype("float64")
    if volumes.empty:
        return 0.0
    return float(volumes.tail(lookback).median())


def hypothetical_bar(frame: pd.DataFrame, *, close: float, ts: pd.Timestamp) -> pd.DataFrame:
    """`frame`in KOPYASINA bir bar ekler. Asıl çerçeveye DOKUNULMAZ.

    `open` son kapanıştır (boşluksuz açılış varsayımı — dolum kuralı zaten bir sonraki
    barın açılışıdır, kural 13), `high`/`low` gövdenin uçlarıdır (fitilsiz bar). Fitil
    uydurmak, mum içi stop/TP tetiklemesini de uydurmak olurdu; bu araç yalnızca
    KAPANIŞ koşullarını sorar.
    """
    previous_close = float(frame["close"].iloc[-1])
    row = pd.DataFrame(
        {
            "open": [previous_close],
            "high": [max(previous_close, close)],
            "low": [min(previous_close, close)],
            "close": [close],
            "volume": [assumed_volume(frame)],
        },
        index=pd.DatetimeIndex([ts], name=frame.index.name or "ts"),
    )
    return pd.concat([frame, row])


def candidate_prices(
    last_close: float, *, range_pct: float, step_pct: float
) -> tuple[tuple[float, ...], tuple[float, ...]]:
    """(yukarı, aşağı) aday fiyatlar — her ikisi de UZAKLIĞA göre artan sırada.

    Sıra, "en yakın tetik" sorusunun cevabını ilk isabete indirger; ikiye bölme
    kullanılmaz çünkü koşullar fiyata göre monoton değildir (bkz. modül docstring'i).
    """
    if step_pct <= 0.0 or range_pct <= 0.0:
        raise ValueError(f"ızgara pozitif olmalı: menzil={range_pct}, adım={step_pct}")
    steps = int(round(range_pct / step_pct))
    up = tuple(last_close * (1.0 + step_pct * i / 100.0) for i in range(1, steps + 1))
    down = tuple(last_close * (1.0 - step_pct * i / 100.0) for i in range(1, steps + 1))
    return up, tuple(price for price in down if price > 0.0)


def snapshot_at(
    base: MarketData, *, symbol: str, close: float, ts: pd.Timestamp, btc_symbol: str
) -> MarketData:
    """Tek sembolü hipotetik barla ileri taşıyan anlık görüntü.

    Anlık görüntüde YALNIZCA taranan sembol vardır. Diğer semboller `as_of` barında
    kaldıkları için modeller onları zaten atlardı (`frame.index[-1] != as_of`); dışarıda
    bırakmak davranışı değiştirmez, yalnızca her adayda 13 sembolü yeniden kesmeyi
    önler. `market.btc` ise KALIR: `meanrev`in short rejim kapısı oradan okunur ve o
    kapı fiyat dışı bir koşuldur (çıktıda öyle işaretlenir).
    """
    frame = hypothetical_bar(base.ohlcv[symbol], close=close, ts=ts)
    btc = frame if symbol == btc_symbol else base.btc
    funding = {symbol: base.funding[symbol]} if symbol in base.funding else {}
    return MarketData(ohlcv={symbol: frame}, btc=btc, funding=funding, as_of=ts)


# --------------------------------------------------------------------------- #
# Kapılar
# --------------------------------------------------------------------------- #
class StopBandGate:
    """Motorun stop bandı tavanı (kural 14) — KOPYALANMAZ, `core/engine.py` ÇAĞRILIR.

    Kural tek kopyadır: `max_stop_atr_multiple` karşılaştırmasını burada yeniden yazmak,
    tavanın bir gün motorda değişip ekranda değişmemesi demekti. Motorun kendi
    fonksiyonu salt okunurdur; saydığı tek şey kendi geçici çalışma nesnesidir.
    """

    def __init__(self, engine: Engine, config: Mapping[str, Any]) -> None:
        self._engine = engine
        self._cap = float(get_setting(dict(config), "max_stop_atr_multiple"))
        self._atr_period = int(get_setting(dict(config), "trailing.atr_period"))

    @property
    def cap(self) -> float:
        return self._cap

    def check(
        self, strategy: Strategy, signal: Signal, market: MarketData
    ) -> tuple[Gate, Measurement]:
        run = _throwaway_run(strategy)
        kept = self._engine._within_stop_band(run, [signal], market)  # noqa: SLF001
        frame = market.ohlcv.get(signal.symbol)
        atr = (
            average_true_range(frame.loc[: market.as_of], self._atr_period)
            if frame is not None
            else None
        )
        measured = None
        if atr and atr > 0.0 and signal.stop_price is not None:
            reference = float(frame.loc[market.as_of, "close"])
            measured = abs(reference - signal.stop_price) / atr
        return (
            Gate(name="atr_ceiling", passed=bool(kept)),
            Measurement(
                name="stop_atr_multiple", value=measured, threshold=self._cap, unit="xATR"
            ),
        )


def _throwaway_run(strategy: Strategy) -> Any:
    """`_within_stop_band`in saymak için kullandığı geçici çalışma nesnesi.

    Motorun kendi tipinden üretilir (ikinci bir tanım yazılmaz) ve HİÇBİR YERE bağlı
    değildir: içine yazılan `skipped_signals` sayacı bu fonksiyonla birlikte ölür.
    """
    from core.engine import _ModelRun  # noqa: PLC0415 — yalnızca bu çağrı için

    return _ModelRun(strategy=strategy, state={}, pending=[], last_bar=None)


# --------------------------------------------------------------------------- #
# Model sondaları
# --------------------------------------------------------------------------- #
class Probe:
    """Bir modelin hipotetik bir barda ne ürettiğini soran salt okunur sonda.

    **Sonda modelin KENDİSİNİ değil, DERİN KOPYASINI çağırır.** `self.strategy` turun
    kurduğu nesnedir ve buradan hiçbir zaman çağrılmaz; `self.model` yalnızca bu sondaya
    ait bir kopyadır. Böylece "tarama öncesi/sonrası model durumu bit bit aynı" sözü
    hangi metodun hangi alana yazdığına değil, tek bir satıra bağlanır — bir gün
    `generate_signals` yeni bir alana yazmaya başlasa bile söz tutmaya devam eder.
    """

    def __init__(self, strategy: Strategy, *, config: Mapping[str, Any]) -> None:
        self.strategy = strategy
        self.model = copy.deepcopy(strategy)
        self.config = dict(config)

    @property
    def name(self) -> str:
        return self.strategy.name

    @property
    def selection(self) -> str | None:
        return None

    def symbols(self, available: Sequence[str]) -> list[str]:
        return list(available)

    def conditions(self, market: MarketData, symbol: str) -> tuple[Condition, ...]:
        return ()

    def probe(self, market: MarketData, symbol: str) -> list[dict[str, Any]]:
        """Bu anlık görüntüde üretilen kurulumlar; her kayıt için bkz. `_record`."""
        raise NotImplementedError


def _record(
    *,
    direction: str,
    arm: str | None,
    stop_price: float | None,
    target_price: float | None,
    entry_price: float,
    gates: Sequence[Gate],
    measurements: Sequence[Measurement],
    reason: str,
) -> dict[str, Any]:
    distance = None if stop_price is None else abs(entry_price - stop_price)
    return {
        "direction": direction,
        "arm": arm,
        "stop_price": stop_price,
        "target_price": target_price,
        "stop_distance_pct": None if not distance else distance / entry_price * 100.0,
        "reward_risk": (
            None
            if not distance or target_price is None
            else abs(target_price - entry_price) / distance
        ),
        "gates": tuple(gates),
        "measurements": tuple(measurements),
        "reason": reason,
    }


def _from_signal(
    model: Strategy,
    signal: Signal,
    market: MarketData,
    *,
    band: StopBandGate,
) -> dict[str, Any]:
    gate, measurement = band.check(model, signal, market)
    return _record(
        direction=signal.direction,
        arm=find_tag(signal.reason, "arm"),
        stop_price=signal.stop_price,
        target_price=signal.take_profits[0].price if signal.take_profits else None,
        entry_price=float(market.ohlcv[signal.symbol].loc[market.as_of, "close"]),
        gates=[gate],
        measurements=[measurement],
        reason=signal.reason,
    )


class SignalProbe(Probe):
    """`generate_signals`ı olduğu gibi çağırır (`trend`, `meanrev`, `ema_trend`, `vwap_clone`).

    Bu modellerde EV KAPISI yoktur: bir kurulum ya sinyal olur ya olmaz, arada kapıda
    ölen bir aday kalmaz. Tek dış kapı motorun stop bandı tavanıdır ve o ayrıca sorulur.
    """

    def __init__(
        self, strategy: Strategy, *, config: Mapping[str, Any], band: StopBandGate
    ) -> None:
        super().__init__(strategy, config=config)
        self._band = band

    def probe(self, market: MarketData, symbol: str) -> list[dict[str, Any]]:
        return [
            _from_signal(self.model, signal, market, band=self._band)
            for signal in self.model.generate_signals(market)
            if signal.symbol == symbol
        ]


class MeanRevProbe(SignalProbe):
    """`meanrev` + BTC rejim kapısının BİLDİRİMİ.

    Kapı fiyat dışıdır: BTC'nin bir sonraki barı bilinmiyor, bu yüzden model onu son
    kapanıştan okur. Söylenmezse çıktı "bu fiyatta short tetiklenir" derken aslında
    "BTC 200 EMA'sının ALTINDA KALIRSA tetiklenir" demiş olurdu.
    """

    def conditions(self, market: MarketData, symbol: str) -> tuple[Condition, ...]:
        allowed, note = self.model._btc_regime(market)  # noqa: SLF001 — modelin KENDİ kapısı
        return (
            Condition(
                name="btc_short_gate",
                satisfied=allowed,
                detail=f"short rejim kapısı {'AÇIK' if allowed else 'KAPALI'} — {note}",
            ),
        )


class VwapCloneProbe(SignalProbe):
    """`vwap_clone`: taramadan ÖNCE öğrenici, motorun yaptığı gibi beslenir.

    Beslenmezse `choose_combo` "hiç denenmemiş kombinasyon" dalına düşer ve taramada
    canlı turun kullanmayacağı bir bant çarpanı çekilir — stop ve hedef o çarpandan
    türediği için ekran modelin üretmeyeceği bir geometriyi gösterirdi.
    """

    def __init__(
        self,
        strategy: Strategy,
        *,
        config: Mapping[str, Any],
        band: StopBandGate,
        ledger: Ledger,
    ) -> None:
        super().__init__(strategy, config=config, band=band)
        _train(self.model, ledger)

    def symbols(self, available: Sequence[str]) -> list[str]:
        """Yalnızca kopyanın KENDİ evreni (kural 15b): dışındaki sembolde zaten üretmez."""
        universe = set(getattr(self.strategy, "_universe", ()))
        return [symbol for symbol in available if symbol in universe]


class VwapManagedProbe(Probe):
    """`vwap_managed`: aday ile KAPI ayrı sorulur.

    `generate_signals` kapıdan geçemeyen adayı düşürür ve geriye hiçbir iz kalmaz —
    oysa tam olarak ayırt edilmek istenen şey odur (karar 34). Bu yüzden modelin kendi
    çağırdığı üç modül fonksiyonu (`stop_price`, `projected_target`, `nearest_target`)
    ve kendi kapı fonksiyonu (`_passes_gates`) ayrı ayrı çağrılır. Hiçbiri burada
    yeniden yazılmaz; kapı KARARI yine `_passes_gates`ten gelir.
    """

    def __init__(
        self, strategy: VwapManaged, *, config: Mapping[str, Any], band: StopBandGate
    ) -> None:
        super().__init__(strategy, config=config)
        self._band = band
        self._min_stop_pct = float(get_setting(self.config, "scalp.min_stop_pct"))
        self._min_reward_risk = float(get_setting(self.config, "scalp.min_reward_risk"))

    def probe(self, market: MarketData, symbol: str) -> list[dict[str, Any]]:
        model = self.model
        candidates, _ = vwap_signal.scan(
            market,
            atr_period=model._atr_period,  # noqa: SLF001
            band_mult=model._band_mult,  # noqa: SLF001
            min_vwap_bars=model._min_vwap_bars,  # noqa: SLF001
        )
        records: list[dict[str, Any]] = []
        for candidate in candidates:
            if candidate.symbol != symbol:
                continue
            stop = vwap_signal.stop_price(candidate, atr_multiple=model._atr_multiple)  # noqa: SLF001
            projected = vwap_signal.projected_target(
                candidate, stop=stop, reward_risk=model._target_reward_risk  # noqa: SLF001
            )
            target = vwap_signal.nearest_target(candidate, projected=projected)
            house = model._passes_gates(candidate, stop=stop, target=target)  # noqa: SLF001
            gates = [Gate(name="house_gates", passed=house)]
            measurements = [
                Measurement(
                    name="min_stop_pct",
                    value=vwap_signal.stop_distance_pct(candidate, stop=stop) * 100.0,
                    threshold=self._min_stop_pct * 100.0,
                    unit="%",
                ),
                Measurement(
                    name="min_reward_risk",
                    value=vwap_signal.reward_risk_of(candidate, stop=stop, target=target),
                    threshold=self._min_reward_risk,
                    unit="R",
                ),
            ]
            if house:
                # Motorun tavanı yalnızca ev kapılarını GEÇEN bir kurulum için
                # anlamlıdır: geçemeyen kurulum motora hiç ulaşmaz ve ona tavan
                # uygulamak, hiç sorulmamış bir soruya cevap uydurmak olurdu.
                signal = model._signal(candidate, stop=stop, target=target)  # noqa: SLF001
                gate, measurement = self._band.check(model, signal, market)
                gates.append(gate)
                measurements.append(measurement)
            records.append(
                _record(
                    direction=candidate.direction,
                    arm=vwap_signal.ARM_NAME,
                    stop_price=stop,
                    target_price=target,
                    entry_price=candidate.entry_price,
                    gates=gates,
                    measurements=measurements,
                    reason=candidate.detail(),
                )
            )
        return records


class ScalpArmProbe(Probe):
    """`scalp_fixed`/`scalp_patient`: kolların TAMAMI, çekiliş YOK.

    `generate_signals` bir kol ve bir sembol çeker; tarama o çekilişi yeniden
    üretemeyeceği için (bkz. SELECTION_NOTE) modelin kapıdan ÖNCEKİ ve SONRAKİ
    kurulumları okunur: `propose_all` (kol mantığı), `_gated` (ev kapıları),
    `regime_filter` (alt sınıfın ek kapısı). Üçü de modelin kendi kodudur ve kapı
    KARARI o zincirin çıktısından okunur — burada hiçbir eşik yeniden karşılaştırılmaz.
    """

    def __init__(
        self, strategy: ScalpModel, *, config: Mapping[str, Any], band: StopBandGate
    ) -> None:
        super().__init__(strategy, config=config)
        self._band = band
        self._min_stop_pct = float(get_setting(self.config, "scalp.min_stop_pct"))
        self._min_reward_risk = float(get_setting(self.config, "scalp.min_reward_risk"))

    @property
    def selection(self) -> str | None:
        return SELECTION_NOTE

    def conditions(self, market: MarketData, symbol: str) -> tuple[Condition, ...]:
        """Hacim teyidi: varsayılan hacim, hacim teyitli kolların eşiğini geçiyor mu?

        Eşik burada bir KARAR üretmez — kolun kendisi zaten karar verdi. Sayı yalnızca
        "bu kol hiç tetiklenmediyse hacim yüzünden mi" sorusu cevaplanabilsin diye
        yazılır ve kolun KENDİ sabitlerinden (`scalp_arms`) + ortak `sma`dan gelir.
        """
        frame = market.ohlcv[symbol]
        volume = float(frame["volume"].iloc[-1])
        average = sma(frame["volume"], scalp_arms.VOLUME_LOOKBACK)
        if average is None or average <= 0.0:
            return (
                Condition(
                    name="volume_confirm",
                    satisfied=None,
                    detail="hacim ortalaması hesaplanamadı",
                ),
            )
        required = average * scalp_arms.VOLUME_CONFIRM_MULTIPLE
        return (
            Condition(
                name="volume_confirm",
                satisfied=volume >= required,
                detail=(
                    f"varsayılan hacim {volume:.6g}; hacim teyitli kolların "
                    f"(opening_range_breakout, momentum_burst) eşiği "
                    f"{scalp_arms.VOLUME_CONFIRM_MULTIPLE:g}×SMA"
                    f"{scalp_arms.VOLUME_LOOKBACK} = {required:.6g}"
                ),
            ),
        )

    def probe(self, market: MarketData, symbol: str) -> list[dict[str, Any]]:
        model = self.model
        proposals = scalp_arms.propose_all(market, model._params)  # noqa: SLF001
        records: list[dict[str, Any]] = []
        for arm, setups in proposals.items():
            # Kapı KARARI modelin kendi zincirinden gelir; kimlik `id()` ile taşınır
            # çünkü `_gated`/`regime_filter` yeni nesne ÜRETMEZ, geçenleri aynen döndürür.
            survivors = {
                id(setup)
                for setup in model.regime_filter(model._gated(arm, setups), market)  # noqa: SLF001
            }
            for setup in setups:
                if setup.symbol != symbol:
                    continue
                passed = id(setup) in survivors
                gates = [Gate(name="house_gates", passed=passed)]
                measurements = [
                    Measurement(
                        name="min_stop_pct",
                        value=setup.stop_distance_pct * 100.0,
                        threshold=self._min_stop_pct * 100.0,
                        unit="%",
                    ),
                    Measurement(
                        name="min_reward_risk",
                        value=setup.reward_risk,
                        threshold=self._min_reward_risk,
                        unit="R",
                    ),
                ]
                if passed:
                    signal = model._signal(setup, posterior=float("nan"))  # noqa: SLF001
                    gate, measurement = self._band.check(model, signal, market)
                    gates.append(gate)
                    measurements.append(measurement)
                records.append(
                    _record(
                        direction=setup.direction,
                        arm=arm,
                        stop_price=setup.stop_price,
                        target_price=setup.target_price,
                        entry_price=setup.entry_price,
                        gates=gates,
                        measurements=measurements,
                        reason=setup.detail,
                    )
                )
        return records


def _train(model: Strategy, ledger: Ledger) -> None:
    """Öğrenen modele KENDİ kapanmış işlemlerini verir — motorun yoluyla AYNI (kural 16).

    `core.engine._closed_trade` çağrılır, satır dönüşümü burada yeniden yazılmaz: ikinci
    bir dönüşüm, modelin taramada canlı turdakinden başka bir geçmiş görmesi demekti.
    Kancayı uygulamayan modelde defter HİÇ okunmaz (motorun aynı kapısı).
    """
    from core.engine import _closed_trade  # noqa: PLC0415

    if type(model).observe_closed_trades is Strategy.observe_closed_trades:
        return
    rows = ledger.read_trades(model.name)
    model.observe_closed_trades(tuple(_closed_trade(row) for row in rows))


def build_probe(
    strategy: Strategy, *, config: Mapping[str, Any], band: StopBandGate, ledger: Ledger
) -> Probe:
    if isinstance(strategy, VwapManaged):
        return VwapManagedProbe(strategy, config=config, band=band)
    if isinstance(strategy, VwapClone):
        return VwapCloneProbe(strategy, config=config, band=band, ledger=ledger)
    if isinstance(strategy, ScalpModel):
        return ScalpArmProbe(strategy, config=config, band=band)
    if strategy.name == "meanrev":
        return MeanRevProbe(strategy, config=config, band=band)
    return SignalProbe(strategy, config=config, band=band)


# --------------------------------------------------------------------------- #
# Pozisyon / kota durumu
# --------------------------------------------------------------------------- #
def position_state(ledger: Ledger, model: Strategy, config: Mapping[str, Any]) -> dict[str, Any]:
    """Modelin AÇIK pozisyonları ve kotası — defterden OKUNUR, hesaplanmaz (kural 7).

    Bir tetik fiyatına ulaşmak emri açtırmaya yetmez: aynı yönde açık pozisyon varsa emir
    `duplicate_position` ile düşer, kota doluysa `max_positions`/`max_short_positions`
    ile. Bunu söylememek, ulaşılamayacak bir tetiği "yaklaşıyor" diye göstermek olurdu.
    """
    state = ledger.load_state(model.name) or {}
    positions = [dict(item) for item in state.get("positions", [])]
    limits = getattr(model, "limits", None)
    max_positions = int(get_setting(dict(config), "max_positions"))
    max_shorts = int(get_setting(dict(config), "max_short_positions"))
    if limits is not None:
        # Kopyanın limitleri kök kotayı yalnızca DARALTIR (kural 15b).
        if limits.max_positions is not None:
            max_positions = min(max_positions, int(limits.max_positions))
        if limits.max_per_direction is not None:
            max_shorts = min(max_shorts, int(limits.max_per_direction))
    open_by_key = {(str(p.get("symbol")), str(p.get("direction"))) for p in positions}
    shorts = sum(1 for p in positions if str(p.get("direction")) == "short")
    longs = len(positions) - shorts
    return {
        "open_positions": len(positions),
        "max_positions": max_positions,
        "open_long": longs,
        "open_short": shorts,
        "max_short_positions": max_shorts,
        "quota_full": len(positions) >= max_positions,
        "open_keys": sorted(f"{symbol}|{direction}" for symbol, direction in open_by_key),
    }


def _trigger_position_note(
    state: Mapping[str, Any], *, symbol: str, direction: str
) -> dict[str, Any]:
    duplicate = f"{symbol}|{direction}" in state["open_keys"]
    opposite_direction = "short" if direction == "long" else "long"
    opposite = f"{symbol}|{opposite_direction}" in state["open_keys"]
    direction_full = (
        direction == "short" and state["open_short"] >= state["max_short_positions"]
    )
    return {
        "duplicate_position": duplicate,
        "opposite_position": opposite,
        "quota_full": bool(state["quota_full"]),
        "direction_quota_full": bool(direction_full),
        "blocked": bool(duplicate or state["quota_full"] or direction_full),
    }


# --------------------------------------------------------------------------- #
# Tarama
# --------------------------------------------------------------------------- #
def scan_layer(
    layer: Layer,
    *,
    models: Sequence[str] | None = None,
    market: MarketData | None = None,
) -> dict[str, Any]:
    """Bir katmanın yakınlık raporu. Hiçbir şey yazmaz."""
    config = layer.config
    scope = SCOPE.get(layer.name)
    if scope is None:
        raise ValueError(f"kapsam dışı katman: {layer.name} (tanımlı: {sorted(SCOPE)})")
    wanted = list(models) if models is not None else list(scope)
    unknown = [name for name in wanted if name not in scope]
    if unknown:
        raise ValueError(f"{layer.name} kapsamında olmayan model: {unknown} (kapsam: {list(scope)})")

    snapshot = market if market is not None else load_cached_market_data(
        config, symbols=layer.symbols
    )
    duration = bar_duration(str(get_setting(config, "timeframe")))
    hypo_ts = snapshot.as_of + duration
    btc_symbol = str(get_setting(config, "exchange.btc_reference"))
    range_pct, step_pct = _GRIDS.get(layer.timeframe, _DEFAULT_GRID)
    atr_period = int(get_setting(config, "trailing.atr_period"))

    strategies, failures = build_strategies(wanted, config)
    ledger = Ledger(layer.ledger_root)
    band = StopBandGate(
        Engine([], config=config, ledger=ledger, portfolio=Portfolio(config)), config
    )

    symbols = sorted(snapshot.ohlcv)
    probes = [build_probe(s, config=config, band=band, ledger=ledger) for s in strategies]
    states = {s.name: position_state(ledger, s, config) for s in strategies}
    targets = {probe.name: probe.symbols(symbols) for probe in probes}
    triggers: dict[str, list[Trigger]] = {probe.name: [] for probe in probes}
    empty: dict[str, list[str]] = {probe.name: [] for probe in probes}

    for symbol in symbols:
        active = [probe for probe in probes if symbol in targets[probe.name]]
        if not active:
            continue
        found = scan_symbol(
            active,
            snapshot,
            symbol=symbol,
            hypo_ts=hypo_ts,
            btc_symbol=btc_symbol,
            range_pct=range_pct,
            step_pct=step_pct,
            atr_period=atr_period,
            states=states,
        )
        for probe in active:
            if found[probe.name]:
                triggers[probe.name].extend(found[probe.name])
            else:
                # "Tetik yok" bir BOŞLUK değil bir KAYITTIR: boş bırakmak "ızgarada
                # yoktu" ile "bu sembol hiç taranmadı"yı aynı hücreye yazardı.
                empty[probe.name].append(symbol)

    scans: list[ModelScan] = []
    for probe in probes:
        rows = sorted(triggers[probe.name], key=lambda item: abs(item.distance_pct))
        scans.append(
            ModelScan(
                model=probe.name,
                selection=probe.selection,
                scanned_symbols=tuple(targets[probe.name]),
                triggers=tuple(rows),
                no_trigger=tuple(empty[probe.name]),
                position_state=states[probe.name],
            )
        )
        logger.info(
            "%s/%s: %d sembol tarandı, %d tetik (%d kapıdan geçiyor), %d sembolde tetik yok",
            layer.name, probe.name, len(targets[probe.name]), len(rows),
            sum(1 for item in rows if item.gates_passed), len(empty[probe.name]),
        )

    return {
        "layer": layer.name,
        "timeframe": layer.timeframe,
        "as_of": snapshot.as_of.isoformat(),
        "hypothetical_bar_at": hypo_ts.isoformat(),
        "scanned_symbols": len(symbols),
        "build_failures": failures,
        "grid": {"range_pct": range_pct, "step_pct": step_pct, "method": "grid"},
        "assumptions": _assumptions(range_pct, step_pct),
        "models": [_scan_payload(item) for item in scans],
    }


def scan_symbol(
    probes: Sequence[Probe],
    base: MarketData,
    *,
    symbol: str,
    hypo_ts: pd.Timestamp,
    btc_symbol: str,
    range_pct: float,
    step_pct: float,
    atr_period: int,
    states: Mapping[str, Mapping[str, Any]],
) -> dict[str, list[Trigger]]:
    """Bir sembolün ızgarası; her (model, kol, yön, fiyat yönü) için EN YAKIN tetik.

    Sondalar TEK DÖNGÜDE gezilir çünkü pahalı olan şey anlık görüntünün kendisidir
    (her aday fiyat için bir çerçeve birleştirmesi): model başına ayrı geçmek, aynı
    çerçeveyi katmanın model sayısı kadar yeniden kurmak demekti.

    Her anahtar için İKİ kayıt tutulur ve ikisi de raporlanır: en yakın tetik ve en
    yakın KAPIDAN GEÇEN tetik. İkisi aynı olabilir; farklıysa aradaki mesafe tam olarak
    "kapı ne kadar uzağa itiyor" sorusunun cevabıdır (karar 34).
    """
    frame = base.ohlcv[symbol]
    last_close = float(frame.loc[base.as_of, "close"])
    # ATR projenin TEK tanımıdır (`trailing.atr_period`): "kaç ATR uzakta" kolonunu
    # kendi periyoduyla hesaplamak, ekrandaki mesafeyi modelinkinden başka bir ölçeğe
    # oturturdu.
    atr = average_true_range(frame, atr_period)
    up, down = candidate_prices(last_close, range_pct=range_pct, step_pct=step_pct)

    nearest: dict[str, dict[tuple[Any, ...], Trigger]] = {probe.name: {} for probe in probes}
    for price_direction, prices in (("up", up), ("down", down)):
        for price in prices:
            market = snapshot_at(
                base, symbol=symbol, close=price, ts=hypo_ts, btc_symbol=btc_symbol
            )
            for probe in probes:
                try:
                    records = probe.probe(market, symbol)
                except Exception as exc:  # bir sondanın patlaması taramayı düşürmez
                    logger.error(
                        "%s %s @%.10g: sonda hata verdi: %s", probe.name, symbol, price, exc
                    )
                    continue
                if not records:
                    continue
                conditions = probe.conditions(market, symbol)
                for record in records:
                    passed = all(gate.passed for gate in record["gates"])
                    key = (record["arm"], record["direction"], price_direction, passed)
                    # Kapıdan geçen bir tetik "en yakın tetik" kaydını da doldurur: aksi
                    # hâlde kapının hiç ısırmadığı bir kolda aynı satır iki kez görünürdü.
                    loose = (record["arm"], record["direction"], price_direction, False)
                    found = nearest[probe.name]
                    if key in found or (passed and loose in found):
                        continue
                    found[key] = _to_trigger(
                        probe,
                        record,
                        symbol=symbol,
                        price=price,
                        last_close=last_close,
                        atr=atr,
                        price_direction=price_direction,
                        conditions=conditions,
                        state=states[probe.name],
                        passed=passed,
                    )
    return {
        name: sorted(found.values(), key=lambda item: abs(item.distance_pct))
        for name, found in nearest.items()
    }


def _to_trigger(
    probe: Probe,
    record: Mapping[str, Any],
    *,
    symbol: str,
    price: float,
    last_close: float,
    atr: float | None,
    price_direction: str,
    conditions: Sequence[Condition],
    state: Mapping[str, Any],
    passed: bool,
) -> Trigger:
    distance_pct = (price - last_close) / last_close * 100.0
    return Trigger(
        model=probe.name,
        symbol=symbol,
        direction=str(record["direction"]),
        arm=record["arm"],
        price_direction=price_direction,
        trigger_price=price,
        last_close=last_close,
        distance_pct=distance_pct,
        distance_atr=None if not atr else abs(price - last_close) / atr,
        atr=atr,
        stop_price=record["stop_price"],
        target_price=record["target_price"],
        stop_distance_pct=record["stop_distance_pct"],
        reward_risk=record["reward_risk"],
        gates_passed=passed,
        gates=tuple(record["gates"]),
        measurements=tuple(record["measurements"]),
        conditions=tuple(conditions),
        position_state=_trigger_position_note(
            state, symbol=symbol, direction=str(record["direction"])
        ),
        reason=str(record["reason"]),
    )


def _assumptions(range_pct: float, step_pct: float) -> list[str]:
    return [
        "TAHMİN DEĞİLDİR: bir sonraki bar bu fiyatta KAPANIRSA modelin kendi kodu bu "
        "sinyali üretir. Olasılık ölçülmez.",
        "Hipotetik bar: open = son kapanış, close = aday fiyat, high/low = gövdenin "
        f"uçları (fitil yok), volume = son {VOLUME_LOOKBACK} barın medyanı.",
        f"Izgara taraması (ikiye bölme DEĞİL): ±%{range_pct:g}, adım %{step_pct:g} — "
        "koşullar fiyata göre monoton değildir.",
        "Her sembol TEK BAŞINA fiyatlandırılır: diğer semboller son kapanmış barda "
        "kalır, yani çıktı 'tüm piyasa şöyle kapanırsa' demez.",
        "Fiyat dışı koşullar (BTC rejim kapısı, funding serisi, hacim) hipotetik bara "
        "taşınmaz; koşul olarak işaretlenir.",
        "Ölçüme ve sicile GİRMEZ; sinyal üretimini etkilemez (survey/emitted ile aynı "
        "statüde denetim izi).",
    ]


def _scan_payload(scan: ModelScan) -> dict[str, Any]:
    return {
        "model": scan.model,
        "selection": scan.selection,
        "scanned_symbols": list(scan.scanned_symbols),
        "no_trigger": list(scan.no_trigger),
        "position_state": dict(scan.position_state),
        "triggers": [_trigger_payload(trigger) for trigger in scan.triggers],
    }


def _trigger_payload(trigger: Trigger) -> dict[str, Any]:
    return {
        "symbol": trigger.symbol,
        "direction": trigger.direction,
        "arm": trigger.arm,
        "price_direction": trigger.price_direction,
        "trigger_price": trigger.trigger_price,
        "last_close": trigger.last_close,
        "distance_pct": trigger.distance_pct,
        "distance_atr": trigger.distance_atr,
        "atr": trigger.atr,
        "stop_price": trigger.stop_price,
        "target_price": trigger.target_price,
        "stop_distance_pct": trigger.stop_distance_pct,
        "reward_risk": trigger.reward_risk,
        "gates_passed": trigger.gates_passed,
        "gates": [{"name": gate.name, "passed": gate.passed} for gate in trigger.gates],
        "measurements": [
            {
                "name": item.name,
                "value": item.value,
                "threshold": item.threshold,
                "unit": item.unit,
            }
            for item in trigger.measurements
        ],
        "conditions": [
            {"name": item.name, "satisfied": item.satisfied, "detail": item.detail}
            for item in trigger.conditions
        ],
        "position_state": dict(trigger.position_state),
        "reason": trigger.reason,
    }


# --------------------------------------------------------------------------- #
# Giriş noktası
# --------------------------------------------------------------------------- #
def run(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, args.log_level.upper()),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )
    config = load_config(args.config)

    if args.models and len(args.layer) > 1:
        # Kadro katmana özgüdür (`SCOPE`); tek bir listeyi iki katmana uygulamak, bir
        # katmanda kapsam dışı kalan adı sessizce "bulunamadı"ya çevirirdi.
        logger.error("--models yalnızca TEK katmanla kullanılabilir: %s", args.layer)
        return 2

    layers: list[dict[str, Any]] = []
    for name in args.layer:
        try:
            layer = resolve_layer(config, name)
        except ConfigError as exc:
            logger.error("kullanım hatası: %s", exc)
            return 2
        try:
            layers.append(scan_layer(layer, models=args.models))
        except ValueError as exc:
            logger.error("kullanım hatası: %s", exc)
            return 2
        except Exception as exc:
            # Bir katmanın verisi yoksa (önbellek boş, çıpa bayat) öteki katman yine
            # taranır; sebep yüke YAZILIR — sessiz bir eksik, boş bir bölümü "hiçbir
            # kurulum yaklaşmıyor" diye okuturdu.
            logger.error("%s katmanı taranamadı: %s", name, exc)
            layers.append({"layer": name, "error": str(exc), "models": [], "scanned_symbols": 0})

    scanned = sum(int(item.get("scanned_symbols") or 0) for item in layers)
    if scanned == 0:
        # VERİ KAPISI (karar 51): boş bir rapor YEŞİL dönmez ve YAZILMAZ. Yazılsaydı
        # sayfa "hiçbir kurulum yaklaşmıyor" derdi, oysa doğru cevap "ölçemedik".
        logger.error(
            "hiçbir katmanda sembol taranamadı (önbellek boş ya da çıpa bayat): "
            "%s yazılmadı", args.out,
        )
        return 3

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "kind": "proximity",
        "layers": layers,
    }
    path = Path(args.out)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(jsonable(payload), indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    logger.info("yakınlık taraması yazıldı: %s", path)
    return 0


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Yakınlık taraması: bir sonraki bar hangi fiyatta kapanırsa hangi model "
            "sinyal üretir. Salt okunur; deftere, config'e ve önbelleğe yazmaz."
        )
    )
    parser.add_argument(
        "--layer",
        action="append",
        default=None,
        help=(
            f"taranacak katman (birden çok kez verilebilir); varsayılan {DEFAULT_LAYER}. "
            f"Kapsam: {', '.join(f'{k}={list(v)}' for k, v in SCOPE.items())}"
        ),
    )
    parser.add_argument(
        "--models",
        nargs="+",
        default=None,
        help="kadroyu daralt (yalnızca katmanın KAPSAMINDAKİ adlar); varsayılan hepsi",
    )
    parser.add_argument(
        "--out", default="docs/data/proximity.json", help="çıktı dosyası (varsayılan: %(default)s)"
    )
    parser.add_argument("--config", default=None, help="config.yaml yolu")
    parser.add_argument(
        "--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"]
    )
    args = parser.parse_args(argv)
    if not args.layer:
        args.layer = [DEFAULT_LAYER]
    return args


if __name__ == "__main__":
    sys.exit(run())

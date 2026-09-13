"""Model adı -> strateji kurucusu eşlemesi. `config.yaml`'ın `models` listesi buradan çözülür.

Neden ayrı bir kayıt defteri: `config.yaml` yarışmaya giren kümeyi tanımlar (kural 6), ama
bir YAML dosyası sınıf örnekleyemez. Alternatif, main.py'nin modül adını dinamik import
etmesiydi; o yol config'e yazılan herhangi bir dizenin kod çalıştırabilmesi demekti ve
"hangi 10 model yarışıyor" sorusunun cevabını grep'lenemez hâle getirirdi.

Yeni bir model eklemek iki satırdır: sınıfı import et, `REGISTRY`ye adıyla yaz. Ad,
sınıfın `name` alanıyla birebir aynı olmalıdır — defter klasörü o addan türer.
"""

from __future__ import annotations

import inspect
from typing import Any, Callable, Mapping

from strategies.avwap import Avwap
from strategies.base import Strategy
from strategies.buyhold import BuyHold
from strategies.confluence import Confluence
from strategies.downtrend_rally import DowntrendRally
from strategies.ensemble import Ensemble
from strategies.failed_breakout import FailedBreakout
from strategies.meanrev import MeanReversion
from strategies.momentum import Momentum
from strategies.random_ctrl import RandomControl
from strategies.scalp_bandit import ScalpBandit
from strategies.scalp_fixed import ScalpFixed
from strategies.squeeze import Squeeze
from strategies.trend import Trend

StrategyFactory = Callable[[], Strategy]

REGISTRY: Mapping[str, StrategyFactory] = {
    BuyHold.name: BuyHold,
    Trend.name: Trend,
    MeanReversion.name: MeanReversion,
    Momentum.name: Momentum,
    Squeeze.name: Squeeze,
    Confluence.name: Confluence,
    FailedBreakout.name: FailedBreakout,
    DowntrendRally.name: DowntrendRally,
    Avwap.name: Avwap,
    Ensemble.name: Ensemble,
    RandomControl.name: RandomControl,
    # 15 dakikalık scalp katmanı (config.yaml > layers.scalp). Kayıt defteri katmandan
    # bağımsızdır: hangi modelin hangi turda koşacağını katmanın `models` listesi söyler.
    ScalpBandit.name: ScalpBandit,
    ScalpFixed.name: ScalpFixed,
}


class UnknownModelError(KeyError):
    """config.yaml'da kayıtlı olmayan bir model adı geçiyor."""


def build(name: str, *, config: Mapping[str, Any] | None = None) -> Strategy:
    """Adı verilen modeli örnekler; ad tanınmıyorsa UnknownModelError.

    Sessizce atlamak, yazım hatası yüzünden aylarca eksik yarışan bir küme demekti —
    tam da kural 6'nın ("izin verilen küme tüm modeller için aynı") engellemek istediği şey.

    `config` KATMANIN çözülmüş ayarlarıdır (core/layers.py) ve ayarı okuyan her modele
    verilir. Verilmezse model `load_config()` ile KÖK ayarları okur — 15 dakikalık katmanda
    bu, modelin 4 saatlik bar süresini görmesi ve zaman stop'unu 4 saat yerine 64 saat
    sanması demekti. Kurucusunda `config` parametresi olmayan model, config'ten hiçbir şey
    okumayan modeldir (ör. alım-tut çıpasının sabit ağırlıkları); bir gün okumaya
    başlarsa parametreyi kurucusuna eklemek zorundadır.
    """
    factory = REGISTRY.get(name)
    if factory is None:
        raise UnknownModelError(
            f"{name!r} strategies/registry.py'de kayıtlı değil (kayıtlı: {sorted(REGISTRY)})"
        )
    strategy = factory(config=config) if config is not None and _accepts_config(factory) else factory()
    if strategy.name != name:
        raise ValueError(
            f"kayıt adı ({name!r}) ile strateji adı ({strategy.name!r}) uyuşmuyor: "
            "defter klasörü strateji adından türer, ikisi ayrışırsa defter kaybolur"
        )
    return strategy


def _accepts_config(factory: StrategyFactory) -> bool:
    """Kurucu `config` anahtar argümanını kabul ediyor mu?"""
    try:
        return "config" in inspect.signature(factory).parameters
    except (TypeError, ValueError):  # C düzeyinde kurucu: imza okunamaz
        return False

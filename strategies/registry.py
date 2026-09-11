"""Model adı -> strateji kurucusu eşlemesi. `config.yaml`'ın `models` listesi buradan çözülür.

Neden ayrı bir kayıt defteri: `config.yaml` yarışmaya giren kümeyi tanımlar (kural 6), ama
bir YAML dosyası sınıf örnekleyemez. Alternatif, main.py'nin modül adını dinamik import
etmesiydi; o yol config'e yazılan herhangi bir dizenin kod çalıştırabilmesi demekti ve
"hangi 10 model yarışıyor" sorusunun cevabını grep'lenemez hâle getirirdi.

Yeni bir model eklemek iki satırdır: sınıfı import et, `REGISTRY`ye adıyla yaz. Ad,
sınıfın `name` alanıyla birebir aynı olmalıdır — defter klasörü o addan türer.
"""

from __future__ import annotations

from typing import Callable, Mapping

from strategies.base import Strategy
from strategies.buyhold import BuyHold

StrategyFactory = Callable[[], Strategy]

REGISTRY: Mapping[str, StrategyFactory] = {
    BuyHold.name: BuyHold,
}


class UnknownModelError(KeyError):
    """config.yaml'da kayıtlı olmayan bir model adı geçiyor."""


def build(name: str) -> Strategy:
    """Adı verilen modeli örnekler; ad tanınmıyorsa UnknownModelError.

    Sessizce atlamak, yazım hatası yüzünden aylarca eksik yarışan bir küme demekti —
    tam da kural 6'nın ("izin verilen küme tüm modeller için aynı") engellemek istediği şey.
    """
    factory = REGISTRY.get(name)
    if factory is None:
        raise UnknownModelError(
            f"{name!r} strategies/registry.py'de kayıtlı değil (kayıtlı: {sorted(REGISTRY)})"
        )
    strategy = factory()
    if strategy.name != name:
        raise ValueError(
            f"kayıt adı ({name!r}) ile strateji adı ({strategy.name!r}) uyuşmuyor: "
            "defter klasörü strateji adından türer, ikisi ayrışırsa defter kaybolur"
        )
    return strategy

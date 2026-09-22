"""15 dakikalık scalp katmanının ORTAK mantığı: kollar ve model gövdesi.

İki model (`strategies/scalp_bandit.py`, `strategies/scalp_fixed.py`) bu paketten türer.
Kolların ve kapıların tek kopya olması bir kolaylık değil, ölçümün koşuludur: iki modelin
farkı yalnızca KOL SEÇİMİ olmalıdır ki aradaki ortalama R farkı adaptasyonun katkısı
olarak okunabilsin.
"""

from strategies.scalp.arms import (
    ARM_NAMES,
    ARMS,
    ArmParams,
    ArmScan,
    ArmSetup,
    SymbolView,
    propose_all,
    reflect,
    scan_all,
    symbol_views,
)
from strategies.scalp.model import ScalpModel, arm_universe

__all__ = [
    "ARMS",
    "ARM_NAMES",
    "ArmParams",
    "ArmScan",
    "ArmSetup",
    "ScalpModel",
    "SymbolView",
    "arm_universe",
    "propose_all",
    "reflect",
    "scan_all",
    "symbol_views",
]

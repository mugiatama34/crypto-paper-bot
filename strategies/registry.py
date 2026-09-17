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

from core.validate import validate_model
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
from strategies.scalp_managed import ScalpManaged
from strategies.scalp_patient import ScalpPatient
from strategies.scalp_vol import ScalpVol
from strategies.squeeze import Squeeze
from strategies.trend import Trend
from strategies.vwap_clone import VwapClone
from strategies.vwap_guarded import VwapGuarded
from strategies.vwap_managed import VwapManaged
from strategies.vwap_scored import VwapScored
from strategies.vwap_session import VwapSession

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
    # Scalp katmanının çıkış yönetimi kanadı (modeller 13-15):
    #   vwap_clone    — dış sistem KOPYASI (is_replica), sabit teminat × 10x
    #   vwap_managed  — aynı sinyal, EV kurallarıyla (risk boyutlandırma, %1 taban, 1.5R)
    #   scalp_managed — scalp_fixed'in ikizi, tek farkı üç aşamalı çıkış yönetimi
    ScalpManaged.name: ScalpManaged,
    # scalp_patient — scalp_fixed'in ikizi, tek farkı zaman stop'unun SINIRI (16 ↔ 100).
    # Katmanın `models` listesinde YOKTUR: canlıya alınmadan önce taze bir OOS penceresinde
    # doğrulanmalı (docs/backtest.md > 4, C-5). Backtest onu `--models` ile çağırır.
    ScalpPatient.name: ScalpPatient,
    # scalp_vol — scalp_patient'in ikizi, tek farkı KESİTSEL volatilite rejimi kapısı.
    # Katmanın `models` listesinde YOKTUR: önce taze bir OOS penceresinde ölçülür.
    ScalpVol.name: ScalpVol,
    VwapClone.name: VwapClone,
    VwapManaged.name: VwapManaged,
    # vwap_guarded — model 13'ün CANLIYA HAZIRLANMIŞ uyarlaması. Kopyanın üstüne
    # yazılmaz (kural 15b: değiştirilen kopya kopya olmaktan çıkar), ayrı bir model
    # olarak durur: seans çapalı VWAP, 2.5/3.0σ bant, rejim kapıları (ADX + EMA eğimi
    # + BTC 1s yönü), tükenme şartı, risk boyutlandırma, zaman stop'u ve risk
    # kesicileri (günlük zarar limiti, drawdown kill-switch, korelasyon kotası).
    VwapGuarded.name: VwapGuarded,
    # vwap_session (F0) — kopyanın BİRİMİ düzeltilmiş hâli: seans çapalı VWAP ve
    # seans σ'su, başka hiçbir fark yok (çarpanlar grid'in ortasında sabit).
    # `is_replica`: 1R'si sabit teminattan gelir, yarışmacılarınkiyle aynı birim
    # değildir — bayrağın bütün sonuçları bu tek olgudan çıkar (kural 15b).
    VwapSession.name: VwapSession,
    # vwap_scored (F1) — F0 + skorla boyut, post-only maker giriş, ilerleme koşullu
    # zaman stop'u, risk boyutlandırma (5x) ve likidite kuralı. Tam YARIŞMACIDIR:
    # `sizing="risk"`, yani 1R'si yarışmacılarınkiyle aynı birimdedir.
    VwapScored.name: VwapScored,
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
    # Bayrak/limit bildiriminin kapısı (core/validate.py): kaldıraç tavanı ve
    # "ModelLimits yalnızca kopya modellere açıktır" kuralı KURULUMDA denetlenir. Sinyal
    # kapısında denetlemek modeli aylarca "bu turda sinyal üretmedi" gibi gösterirdi;
    # burada patlayan model main.py tarafından atlanır ve koşu hata koduyla biter.
    validate_model(strategy)
    return strategy


def _accepts_config(factory: StrategyFactory) -> bool:
    """Kurucu `config` anahtar argümanını kabul ediyor mu?"""
    try:
        return "config" in inspect.signature(factory).parameters
    except (TypeError, ValueError):  # C düzeyinde kurucu: imza okunamaz
        return False

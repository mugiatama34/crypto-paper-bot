"""MODEL 22 — kopyanın TERSİ (`is_replica=True`), yarışmacı değil.

**Cevapladığı soru tek:** kopyanın (model 13) kaybı SİNYALDEN mi geliyor, FRİKSİYONDAN mı?

Model 13 ölçüldü ve kaybediyor (88 günde ort. −0.76R, hesap −%99.91). İki açıklama var
ve defterden ayırt edilemiyorlar:

1. **Sinyal yanlış tarafta.** VWAP'ten sapan fiyat dönmüyor, devam ediyor; yani kurulum
   sistematik olarak TERS yönü gösteriyor. Doğruysa ters işlem KAZANIR.
2. **Friksiyon yiyor.** Sabit teminat × 10x, dar σ stop'u ve 59 işlem/gün ile komisyon +
   kayma R'nin büyük bir kesrini alıyor. Doğruysa ters işlem de KAYBEDER — çünkü maliyet
   yön değiştirmez, her iki tarafta da aleyhine işler.

Bu model tam olarak o ayrımı yapar: **aynı kurulum, aynı seviyeler, TERS yön.**

### Ayrışan TEK şey: yönün işareti

Kurulum `strategies/vwap/clone_signal.py`den gelir — modelin KENDİ sinyal modülü yoktur
ve bilinçli olarak yoktur. Kopyanın adayı alınır ve girişe göre AYNALANIR:

    kopya  (long):   stop = E − d,  hedef = E + g
    ters   (short):  stop = E + d,  hedef = E − g

`d` ve `g` aynen korunur, yani R:R ve stop mesafesi (dolayısıyla notional ve maliyet
ölçeği) birebir aynıdır. Aynalamak yerine "stop ile hedefi takas etmek" BAŞKA bir model
olurdu: o zaman R:R de tersine döner ve fark artık yönün değil geometrinin ölçüsü olurdu.

### Neden `vwap.clone.*` bloğunu okur (kendi bloğu YOKTUR)

Projenin genel kuralı her modelin kendi config bloğunu taşımasıdır, ki ikisi sessizce
ayrışmasın. Burada gerekçe TERSİNE çalışır: ayrışmaları ölçümü bozar. Eksen "yalnızca
yönün işareti" olduğu için bant çarpanları, σ pencereleri, teminat oranı, kaldıraç,
limitler ve çıkış yönetimi aynı SAYILAR olmak zorundadır — ayrı bir blok, bir gün birinin
değişmesi ve 13 ↔ 22 farkının "yön + o parametre" hâline gelmesi demekti (F0'ın P4
dersinin aynısı).

### Öğrenme: aynı MEKANİZMA, kendi verisi (kaçınılmaz ayrışma)

Kombinasyon seçimi kopyanınkiyle aynı koddur (`choose_combo` miras alınır) ama model
yalnızca KENDİ kapanmış işlemlerini görebilir (kural 16). Yani posterior'ı farklıdır ve
zamanla farklı kombinasyonları sömürür — bu, tek değişikliğin ARDIL bir sonucudur, ikinci
bir değişken değil (model 12 ↔ 15'teki "ayrışan şey dolumlardır" ile aynı statü). Yine de
sessiz olmaz: 13 ↔ 22 bar bar bir AYNA DEĞİLDİR ve ön-kayıt bunu böyle yazar.

Çekiliş kimliği ise bilinçli olarak PAYLAŞILIR (`_round_rng` kopyanın adını kullanır):
ölçülmeyen eksende çekilişi paylaşmak, model 15'in `rng_identity` kuralının aynısıdır —
keşif çekilişlerinin ilk turlarda hizalanması farkı yönden başka bir şeye bağlamaz.

### Ne DEĞİLDİR

Yarışmacı değildir (`is_replica=True`): 1R'si sabit teminattan gelir, yarışmacılarınkiyle
aynı birim değil (kural 15b). Kabul kapılarına girmez, ortalama R sıralamasına katılmaz,
maliyet ölçeği kolonlarında `nan` alır. Kopyanın ÜSTÜNE de yazmaz — model 13 olduğu gibi
durur (kural 15b: değiştirilen kopya kopya olmaktan çıkar).

Ön-kayıt: `docs/backtest.md > 6g`.
"""

from __future__ import annotations

import logging
import math
from typing import Mapping

from core.tags import format_tags
from strategies.base import Direction, Signal, TakeProfit
from strategies.vwap import clone_signal
from strategies.vwap_clone import Combo, ComboStats, VwapClone

logger = logging.getLogger(__name__)

# Kolun ADI kopyanınkinden AYRIDIR (karar 23'ün aynı gerekçesi): aynı etiketi yazsalardı
# kol kırılımı iki ZIT kural kümesini tek satırda toplar ve ortalama R'leri birbirini
# götürürdü — yani tam olarak ölçülmek istenen fark görünmez olurdu.
ARM_NAME = "vwap_revert_inv"


class VwapInverse(VwapClone):
    """Kopyanın adayını AYNALAYIP ters yönde açar. Başka hiçbir şey değişmez."""

    name = "vwap_inverse"
    allowed_directions: list[Direction] = ["long", "short"]
    is_replica = True
    # Kol AYRI (iki zıt kural kümesi tek satırda toplanamaz), çekiliş ORTAK (ölçülmeyen
    # eksende paylaşılır — model 15'in `rng_identity` kuralı).
    arm_name = ARM_NAME
    rng_identity = VwapClone.rng_identity

    def _signal(
        self,
        candidate: clone_signal.CloneCandidate,
        *,
        combo: Combo,
        stats: ComboStats,
        pick: str,
    ) -> Signal:
        mirrored = _mirror(candidate)
        return Signal(
            symbol=mirrored.symbol,
            direction=mirrored.direction,
            sizing="notional_fraction",
            notional_fraction=self._notional_fraction,
            stop_price=mirrored.stop_price,
            take_profits=(TakeProfit(price=mirrored.target_price, fraction=1.0),),
            **self._exit.signal_fields(),
            reason=format_tags(
                f"TERS: kaynak kuralları {candidate.direction} dedi, aynalanıp "
                f"{mirrored.direction} açıldı. {candidate.detail()}; stop "
                f"{combo.band_mult:g}×{self._sl_mult:g}σ ({mirrored.stop_price:.6g}), "
                f"hedef VWAP mesafesinin %{combo.tp_mult * 100:g}'i aynalanmış "
                f"({mirrored.target_price:.6g}); {self._exit.describe()}",
                arm=ARM_NAME,
                combo=combo.key,
                combo_r=stats.mean_r,
                combo_n=stats.trades,
                pick=pick,
            ),
        )


def _mirror(candidate: clone_signal.CloneCandidate) -> clone_signal.CloneCandidate:
    """Girişe göre ayna: yön döner, stop ve hedef `2E − x` ile karşı tarafa geçer.

    Geometri kendiliğinden geçerli kalır — kaynakta `stop < E < hedef` sağlandığı için
    aynada `hedef < E < stop` sağlanır (kural: `core/validate.py` yine de denetler).
    """
    entry = candidate.entry_price
    flipped: Direction = "short" if candidate.direction == "long" else "long"
    stop = 2.0 * entry - candidate.stop_price
    target = 2.0 * entry - candidate.target_price
    if not all(math.isfinite(v) and v > 0.0 for v in (stop, target)):
        # Aynalanan seviye fiyatı sıfırın altına taşıyorsa kurulum atlanamaz — ama bu
        # ancak stop/hedef girişin iki katından uzaksa olur, yani pratikte imkânsızdır.
        # Sessiz geçmemesi için yine de yükseltilir (kural 8: bu bir programlama hatası).
        raise ValueError(
            f"{candidate.symbol}: aynalanan seviye geçersiz (stop={stop}, hedef={target})"
        )
    return clone_signal.CloneCandidate(
        symbol=candidate.symbol,
        direction=flipped,
        entry_price=entry,
        stop_price=stop,
        target_price=target,
        vwap=candidate.vwap,
        std=candidate.std,
        z=candidate.z,
        bars=candidate.bars,
    )

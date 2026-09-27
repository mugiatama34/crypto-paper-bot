"""Borsa fiyatlarının HAM METNİ üzerinden kesinlik kuralı — tek kopya.

OKX bazı haftalarda 4H geçmişini bir ondalık eksik kaydetmiş (docs/backtest.md > 6p >
TADİLAT-4, 6q > TADİLAT-3): o barlarda 1H alt barının değeri, 4H değerinin ondalık
sayısına KESİLMİŞ hâliyle birebir aynıdır. Bu bir fiyat uyuşmazlığı değil bir KESİNLİK
farkıdır ve parite/tutarlılık kapıları onu ayrı sayar.

Kural iki şartla dar tutulur ve burada tek yerde durur (iki ölçümde iki kopya bir gün
ayrışırdı):
- yalnızca KESME (sıfıra doğru, `ROUND_DOWN`) — yuvarlanmış eşitlik geçmez;
- ondalık sayısı HAM METİNDEN okunur, float'tan türetilmez: float gösterimi sondaki
  sıfırları siler (100.10 → 100.1) ve kural fark ettirmeden gevşerdi.
Ham metni taşıyan çekim yolu `core/data.py::fetch_ohlcv_text`tir.
"""

from __future__ import annotations

from decimal import ROUND_DOWN, Decimal


def decimals_of(text: str) -> int:
    """Ham metindeki ondalık hane sayısı — float'tan DEĞİL (sondaki sıfırlar korunur)."""
    text = text.strip()
    if "e" in text.lower():
        raise ValueError(f"üstel gösterim beklenmiyor: {text!r}")
    return len(text.split(".", 1)[1]) if "." in text else 0


def truncates_to(fine: float, coarse_text: str) -> bool:
    """İnce değer, kaba değerin ham metnindeki ondalık sayısına KESİLDİĞİNDE ona TAM eşit mi?

    `repr(float)` en kısa kayıpsız ondalıktır, yani `Decimal` ince değeri birebir taşır.
    """
    quantum = Decimal(1).scaleb(-decimals_of(coarse_text))
    return Decimal(repr(float(fine))).quantize(quantum, rounding=ROUND_DOWN) == Decimal(coarse_text.strip())

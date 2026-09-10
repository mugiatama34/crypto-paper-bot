"""Sinyal doğrulamasının tek geçidi. Her strateji sinyali motora girmeden buradan geçer.

Programlama hatası (izin dışı yön, tutarsız stop/TP geometrisi, bilinmeyen sembol)
sessizce filtrelenmez — ValueError fırlatılır. Actions workflow'u model bazında hatayı
yakalayıp yalnızca o modeli atlar, koşu devam eder (CLAUDE.md kural 8).
"""

from __future__ import annotations

from strategies.base import Direction, Signal


def validate_signal(
    signal: Signal,
    *,
    entry_price: float,
    allowed_directions: list[Direction],
    symbol_universe: list[str],
) -> None:
    if signal.entry_type != "market":
        raise NotImplementedError(f"entry_type={signal.entry_type!r} henüz desteklenmiyor")

    if signal.symbol not in symbol_universe:
        raise ValueError(f"{signal.symbol} sembol evreninde değil")

    if signal.direction not in allowed_directions:
        raise ValueError(
            f"{signal.direction!r} yönü bu strateji için izinli değil "
            f"(allowed_directions={allowed_directions})"
        )

    if signal.stop_price == entry_price:
        raise ValueError("stop_price giriş fiyatına eşit olamaz (boyutlamada sıfıra bölme)")

    if signal.direction == "long" and signal.stop_price >= entry_price:
        raise ValueError("long pozisyonda stop_price giriş fiyatının altında olmalı")
    if signal.direction == "short" and signal.stop_price <= entry_price:
        raise ValueError("short pozisyonda stop_price giriş fiyatının üzerinde olmalı")

    total_fraction = 0.0
    for tp in signal.take_profits:
        if signal.direction == "long" and tp.price <= entry_price:
            raise ValueError("long pozisyonda take profit giriş fiyatının üzerinde olmalı")
        if signal.direction == "short" and tp.price >= entry_price:
            raise ValueError("short pozisyonda take profit giriş fiyatının altında olmalı")
        total_fraction += tp.fraction
    if total_fraction > 1.0:
        raise ValueError(f"take_profits fraction toplamı 1.0'ı aşıyor: {total_fraction}")

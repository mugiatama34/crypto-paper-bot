"""Sinyal doğrulamasının tek geçidi. Her strateji sinyali motora girmeden buradan geçer.

Programlama hatası (izin dışı yön, tutarsız stop/TP geometrisi, bilinmeyen sembol)
sessizce filtrelenmez — ValueError fırlatılır. Actions workflow'u model bazında hatayı
yakalayıp yalnızca o modeli atlar, koşu devam eder (CLAUDE.md kural 8).

Boyutlandırma modu da burada denetlenir (kural 15): `sizing="notional_fraction"` YALNIZCA
`is_benchmark=True` modellere açıktır. Bu kapı olmadan kural 3/11 delinebilir — her model
kendi boyutunu "referans gibi" belirlemeye başlar ve ortak risk birimi (1R) ortadan kalkar,
yani modeller artık aynı ölçekte yarışmaz. İki alanın birbirini dışlaması da sessiz
düzeltmeye değil hataya bağlanmıştır: `sizing="risk"` gelen bir sinyalde dolu bir
`notional_fraction`'ı yok saymak, modelin hangi boyutlandırmayı istediğini tahmin etmek
olurdu.
"""

from __future__ import annotations

from strategies.base import Direction, Signal

VALID_SIZING_MODES: tuple[str, ...] = ("risk", "notional_fraction")


def validate_signal(
    signal: Signal,
    *,
    entry_price: float,
    allowed_directions: list[Direction],
    symbol_universe: list[str],
    is_benchmark: bool = False,
) -> None:
    """Sinyali reddeder ya da sessizce geçirir; `is_benchmark` varsayılanı kısıtlayıcı olandır."""
    if signal.entry_type != "market":
        raise NotImplementedError(f"entry_type={signal.entry_type!r} henüz desteklenmiyor")

    if signal.symbol not in symbol_universe:
        raise ValueError(f"{signal.symbol} sembol evreninde değil")

    if signal.direction not in allowed_directions:
        raise ValueError(
            f"{signal.direction!r} yönü bu strateji için izinli değil "
            f"(allowed_directions={allowed_directions})"
        )

    _validate_sizing(signal, is_benchmark=is_benchmark)

    if signal.sizing == "risk":
        _validate_stop_geometry(signal, entry_price=entry_price)

    _validate_take_profits(signal, entry_price=entry_price)


def _validate_sizing(signal: Signal, *, is_benchmark: bool) -> None:
    if signal.sizing not in VALID_SIZING_MODES:
        raise ValueError(
            f"bilinmeyen sizing modu: {signal.sizing!r} (geçerli: {list(VALID_SIZING_MODES)})"
        )

    if signal.sizing == "risk":
        if signal.stop_price is None:
            raise ValueError('sizing="risk" iken stop_price zorunludur')
        if signal.notional_fraction is not None:
            raise ValueError(
                'sizing="risk" iken notional_fraction dolu olamaz '
                f"(geldi: {signal.notional_fraction})"
            )
        return

    # sizing == "notional_fraction"
    if not is_benchmark:
        raise ValueError(
            'sizing="notional_fraction" yalnızca is_benchmark=True modellere açıktır '
            "(CLAUDE.md kural 15); yarışmacı modeller kendi boyutunu belirleyemez"
        )
    if signal.stop_price is not None:
        # Sessizce yok saymak, deftere yazılan "ilk stop"un hiç kullanılmayan bir sayı
        # olması demekti: risk_amount ve cost_per_r o sayıdan türüyor.
        raise ValueError(
            'sizing="notional_fraction" iken stop_price None olmalıdır '
            f"(geldi: {signal.stop_price})"
        )
    if signal.notional_fraction is None:
        raise ValueError('sizing="notional_fraction" iken notional_fraction zorunludur')
    if not 0.0 < signal.notional_fraction <= 1.0:
        raise ValueError(
            f"notional_fraction 0 ile 1.0 arasında olmalı: {signal.notional_fraction}"
        )


def _validate_stop_geometry(signal: Signal, *, entry_price: float) -> None:
    stop_price = signal.stop_price
    assert stop_price is not None  # _validate_sizing garanti eder

    if stop_price == entry_price:
        raise ValueError("stop_price giriş fiyatına eşit olamaz (boyutlamada sıfıra bölme)")

    if signal.direction == "long" and stop_price >= entry_price:
        raise ValueError("long pozisyonda stop_price giriş fiyatının altında olmalı")
    if signal.direction == "short" and stop_price <= entry_price:
        raise ValueError("short pozisyonda stop_price giriş fiyatının üzerinde olmalı")


def _validate_take_profits(signal: Signal, *, entry_price: float) -> None:
    """TP geometrisi boyutlandırma modundan bağımsızdır: hedef girişin doğru tarafında olmalı."""
    total_fraction = 0.0
    for tp in signal.take_profits:
        if signal.direction == "long" and tp.price <= entry_price:
            raise ValueError("long pozisyonda take profit giriş fiyatının üzerinde olmalı")
        if signal.direction == "short" and tp.price >= entry_price:
            raise ValueError("short pozisyonda take profit giriş fiyatının altında olmalı")
        total_fraction += tp.fraction
    if total_fraction > 1.0:
        raise ValueError(f"take_profits fraction toplamı 1.0'ı aşıyor: {total_fraction}")

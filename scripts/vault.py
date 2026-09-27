#!/usr/bin/env python3
"""KASA kesimi (docs/backtest.md > 7.8, karar 68). Ölçümün parçası DEĞİL.

`KASA_START`tan sonra kapanan hiçbir bar, fonlama kaydı ya da defter satırı bir tezin
GELİŞTİRİLMESİNDE kullanılmaz; bu tarihten sonra yazılan her ön-kaydın B dönemi en geç
burada biter. Kesim TEK sabittedir: iki yerde yazılı bir kesim bir gün ayrışır ve bir araç
kasaya sessizce bakar.

Kasayı okuyan her yeni ölçüm aracı veri çekiminin `now`unu `vault_now()`dan alır ve
istediği her uç noktayı `assert_before_vault` ile sınar. Kasa yalnızca dondurulmuş bir tezin
ön-kayıtlı TEK sınamasında açılır; o araç bu modülü kullanmaz, kendi ön-kaydını gösterir.
"""

from __future__ import annotations

import pandas as pd

# 2026-09-27, kullanıcı kararı. DEĞİŞTİRİLMEZ; yeni bir kasa yalnızca daha İLERİ bir kesimle
# ve kullanıcı kararıyla açılır (geriye çekmek görülmüş veriyi kasaya geri koymak olurdu).
KASA_START = pd.Timestamp("2026-09-27T00:00:00Z")


class VaultError(RuntimeError):
    """İstenen an kasanın içinde."""


def vault_now() -> pd.Timestamp:
    """Geliştirme araçlarının veri çekiminde kullanacağı `now`: kasa başlangıcı."""
    return KASA_START


def assert_before_vault(ts: pd.Timestamp | str, *, what: str = "uç nokta") -> pd.Timestamp:
    """`ts` kasa başlangıcını AŞIYORSA hata. Kasa başlangıcında KAPANAN bar kasa öncesidir."""
    stamp = pd.Timestamp(ts)
    stamp = stamp.tz_localize("UTC") if stamp.tzinfo is None else stamp.tz_convert("UTC")
    if stamp > KASA_START:
        raise VaultError(f"{what} {stamp} kasanın içinde (KASA_START {KASA_START}; docs/backtest.md > 7.8)")
    return stamp

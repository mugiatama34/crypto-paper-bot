"""`wave_scalp`in ÖN-KAYITLI koşusu (docs/backtest.md > 6h). Ölçümün parçası DEĞİL.

**AŞAMA 1: YALNIZCA DÖNEM A.** Dönem B (hold-out) bu aşamada KOŞULMAZ ve betik, B
tarihlerine dokunan bir çağrıyı `--confirm-holdout` bayrağı olmadan REDDEDER. Eylül 2026
ve sonrası GÖRÜLMÜŞ VERİDİR (kaynağın canlı defteri sohbette okundu) ve hiçbir bayrakla
açılamaz — orası bir hold-out değil, kirlenmiş bir penceredir.

**İkinci bir backtest DEĞİLDİR** — `scripts/backtest.py::run_backtest`i çağırır ve
**hiçbir metrik burada hesaplanmaz** (kural 7): ortalama R, kazanma oranı, `cost_per_r`,
kırılımlar, tutuş süresi ve kabul bayrakları `core/metrics.py`den gelir. Burada
hesaplanan tek şey ön-kayıtın istediği DAĞILIM ÖZETLERİDİR (yüzdelikler, kovalar, paylar)
ve onlar da `core/metrics.py::_percentile`ı çağırır — ikinci bir yüzdelik tanımı, aynı
defterin iki farklı p90'ı demekti (`scripts/diagnose_ema_exits.py`nin aynı gerekçesi).

**Koşu sınıfları AYRI durur ve toplanmaz** (§6h > 9):

- **portföy koşusu = BİRİNCİL satır.** Kota (`max_positions` 5, `max_short_positions` 3)
  BAĞLAR ve kabul çıtası ondan okunur — canlı onu yapacak.
- **coin başına koşu = BİLGİ.** Kota bağlamaz; K-1/K-3 analogları buradan yazılır ama
  **bağlayıcı DEĞİLDİR** (§6h > 7): ikisi de `ema_trend`in kapı setinden gelir ve o
  modelin dış referans koşusu için tanımlandı; burada öyle bir referans yoktur ve eşik
  uydurmak, kapıyı ölçüme bakarak tasarlamak olurdu.

**Veri kapsamı bir KAPIDIR** (§6h > 5): dönem A'nın başlangıcına ulaşılamayan bir sembol
varsa ya da B-2 (`missing_bars` / `unchecked_position_bars`) sıfırdan büyükse koşu DURUR
ve karar kullanıcıya gider. Pencere kaydırılmaz — §7.3'ün yasağı tam olarak budur.

Tetikleyicisi `.github/workflows/backtest-wave.yml` (yalnızca `workflow_dispatch`, cron
yok — §7).
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd  # noqa: E402

from core.config import load_config  # noqa: E402
from core.layers import resolve_layer  # noqa: E402
from core.ledger import Ledger  # noqa: E402
# `_median` / `_percentile` bilinçli olarak core'dan ithal edilir: yüzdelik tanımı bu
# projede TEK yerdedir (`core/metrics.py`) ve ikinci bir uygulama, aynı defterin iki
# farklı p90'ını üretebilirdi — `scripts/diagnose_ema_exits.py` ile aynı gerekçe.
from core.metrics import _median, _percentile, breakdown, merge_fills  # noqa: E402
from core.tags import find_tag  # noqa: E402
from scripts.backtest import exit_code_of, BacktestResult, run_backtest  # noqa: E402
from strategies.wave_coinflip import FLIPPED, SAME, WaveCoinflip  # noqa: E402

logger = logging.getLogger("backtest_wave")

LAYER = "scalp"
MODEL = "wave_scalp"
# Kontrol AÇIKÇA verilir; katmanın kök varsayılanı (`acceptance.control_model` =
# `random_ctrl`) bu koşuda KULLANILMAZ. Gerekçe §6h > EK-1: scalp katmanı bir gün
# `scalp_coinflip` alırsa varsayılan ona kayabilir ve o, wave için YANLIŞ kontroldür
# (farklı stop geometrisi -> ⚠B yanar, `cost_per_r` kıyaslanamaz). Seçim
# `manifest.json > deviations.control_model`a yazılır.
CONTROL = "wave_coinflip"
PREREGISTRATION = "docs/backtest.md > 6h"

# --------------------------------------------------------------------------- #
# Pencereler — ÖN-KAYITLI, koşu sonucuna göre kaydırılmaz (§7.3)
# --------------------------------------------------------------------------- #
PERIOD_A_START = "2025-03-01T00:00:00Z"
PERIOD_A_CUTOFF = "2025-12-31T00:00:00Z"   # dönem A'nın SİNYAL kesimi
PERIOD_B_START = PERIOD_A_CUTOFF           # B, A'nın kesiminden + embargo ile başlar
PERIOD_B_END = "2026-08-31T00:00:00Z"
# Kaynağın canlı defteri (09.09–21.09.2026) sohbette OKUNDU. Bu tarihten sonrası hiçbir
# döneme giremez ve hiçbir bayrakla açılamaz — hold-out değil, kirlenmiş pencere.
SEEN_DATA_START = "2026-09-01T00:00:00Z"

# Dönem A'nın KUYRUĞU: kesimden sonra YENİ sinyal üretilmez ama barlar pozisyon yönetimi
# için işlenir (kural 13'ün dönem atamasındaki karşılığı — §6d'nin deseniyle birebir).
#
# Kuyruk zaman olarak dönem B ile ÖRTÜŞÜR ve bu bir kirlenme DEĞİLDİR: B'yi açan şey
# barların işlenmesi değil, kesimden sonra YENİ SİNYAL üretilmesidir. Kuyrukta üretilen
# sinyal sayısı sıfırdır (`--signal-cutoff`), yani A'nın kuyruğu B'nin sonuçlarına
# dokunmaz. §6d'de de A'nın kuyruğu (2024-12-30) B'nin başlangıcının (2024-07-21)
# ötesine uzanıyordu.
#
# Uzunluk 2 AY ve koşudan önce sabittir: model zaman stop'u taşımıyor (§6h > 3h), yani
# pozisyon ömrünün tanım gereği bir üst sınırı yok ve kuyruk ölçülen embargonun kendisini
# kırpmamalı. 15 dakikalık barda 2 ay ≈ 5.760 bardır — kaynağın geometrisinde (stop
# p2'den, hedef p2'den, üstüne üç aşamalı yönetim) tipik ömrün kat kat üstü. Kuyrukta
# hâlâ açık kalan pozisyonların SAYISI raporlanır ve kapanmış işlem istatistiğine
# GİRMEZ; sayı sıfırdan büyükse ölçülen embargo bir ALT SINIRDIR ve öyle okunur.
PERIOD_A_TAIL_END = "2026-02-28T00:00:00Z"

# --------------------------------------------------------------------------- #
# ÖN-KAYITLI TAHMİNLER (§6h > 8) — eşikler burada SEÇİLMEZ, ALINTILANIR
# --------------------------------------------------------------------------- #
P1_MAX_NET_AVG_R = 0.0          # tahmin: net ort. R ≤ 0
P2_MIN_COST_PER_R_MEDIAN = 0.08  # tahmin: cost_per_r MEDYANI ≥ 0.08
P3_MAX_GROSS_AVG_R = 0.15       # tahmin: brüt ort. R < +0.15
P4_MIN_TRADES = 300             # hem tahmin hem BAĞLAYICI okuma kapısı (K-2 analogu)

# Stop mesafesi kovaları (§6h > 10.2). Kenarlar GEOMETRİDEN gelir, veriden türetilmez:
# %1 scalp katmanının stop tabanıdır (bu modele uygulanmaz ama ölçek referansıdır), %2 ve
# %4 onun katları. Veriden türetmek, kovayı sonuca bakarak seçmek olurdu (§7.2).
STOP_DISTANCE_BUCKET_EDGES = (1.0, 2.0, 4.0)


class HoldoutError(RuntimeError):
    """Dönem B'ye ya da görülmüş veriye onaysız dokunma girişimi."""


# --------------------------------------------------------------------------- #
# Koşu
# --------------------------------------------------------------------------- #
def run_period(
    *,
    name: str,
    start: pd.Timestamp,
    end: pd.Timestamp,
    out_root: Path,
    history_bars: int,
    funding_periods: int,
    signal_cutoff: pd.Timestamp | None = None,
    embargo_bars: int = 0,
    symbols: Sequence[str] | None = None,
    models: Sequence[str] | None = None,
    config_path: str | None = None,
) -> BacktestResult:
    """Bir koşu. Maliyet override'ı YOKTUR (§6h > 6): canlı config ile ölçülür.

    `control_model` HER koşuda açıkça geçirilir — kontrolün kendi koşusunda da, çünkü
    kabul bayrağı farkın İKİ tarafını da aynı zeminden okumalı ve katmanın varsayılanı
    hiçbir koşuda sessizce devreye girmemeli (§6h > EK-1).
    """
    logger.info(
        "[%s] koşu: %s → %s%s", name, start, end,
        f" (kesim {signal_cutoff})" if signal_cutoff is not None else "",
    )
    return run_backtest(
        layer_name=LAYER,
        start=start,
        end=end,
        out_dir=out_root / name,
        models=list(models) if models else [MODEL],
        history_bars=history_bars,
        funding_periods=funding_periods,
        signal_cutoff=signal_cutoff,
        embargo_bars=embargo_bars or None,
        symbols=list(symbols) if symbols else None,
        control_model=CONTROL,
        config_path=config_path,
    )


def model_trades(result: BacktestResult, model: str = MODEL) -> list[Mapping[str, Any]]:
    """Koşunun KENDİ defterinden modelin dolum satırları (salt okunur)."""
    return Ledger(result.out_dir / "ledger").read_trades(model)


# --------------------------------------------------------------------------- #
# Kapsam kapısı (§6h > 5) — ana koşudan ÖNCE okunur
# --------------------------------------------------------------------------- #
def coverage_gate(
    result: BacktestResult, *, universe: Sequence[str], period_start: pd.Timestamp
) -> dict[str, Any]:
    """Her sembolün ilk barı ve eksik bar sayaçları; yetersizse koşu DURUR.

    Ölçüt koşudan ÖNCE sabitlendi ve burada yalnızca UYGULANIR: (a) evrendeki her sembol
    dönem A'nın başlangıcına ulaşmalı, (b) B-2 sıfır olmalı (`missing_bars` ve
    `unchecked_position_bars`). İkisi de "o barda stop/TP/likidasyon hiç sorulmadı"
    demektir ve sıfırdan büyükse pencere eksik bir geçmişin üstüne yazılmıştır.

    **Yetersizlik pencereyi KAYDIRMAZ.** Kaydırmak, pencereyi veri kapsamına göre seçmek
    olurdu (§7.3) — bu yüzden betik hata koduyla biter ve karar kullanıcıya gider.
    """
    rows: list[dict[str, Any]] = []
    for symbol in universe:
        item = dict(result.coverage.get(symbol) or {})
        first = item.get("first_bar")
        stamp = pd.Timestamp(first) if first else None
        reaches = stamp is not None and stamp <= period_start + _tolerance(result)
        rows.append(
            {
                "symbol": symbol,
                "bars": item.get("bars", 0),
                "first_bar": first,
                "last_bar": item.get("last_bar"),
                "reaches_period_start": bool(reaches),
            }
        )

    # B-2 MODEL raporundan okunur: `RoundReport` bu sayaçları taşımaz, `ModelReport` taşır
    # (biri turun hiç işlenemeyen barları, öteki tek bir sembolün veri boşluğu).
    model_report = result.report.by_model(MODEL)
    integrity = {
        "missing_bars": int(getattr(model_report, "missing_bars", 0) or 0),
        "unchecked_position_bars": int(
            getattr(model_report, "unchecked_position_bars", 0) or 0
        ),
    }
    short = [row["symbol"] for row in rows if not row["reaches_period_start"]]
    passed = not short and not any(integrity.values())
    return {
        "period_start": str(period_start),
        "symbols": rows,
        "integrity_B2": integrity,
        "symbols_not_reaching_start": short,
        "passed": passed,
        "note": (
            "Ölçüt §6h > 5'te koşudan ÖNCE sabitlendi. Yetersizlikte pencere KAYDIRILMAZ; "
            "koşu durur ve karar kullanıcıya gider."
        ),
    }


def _tolerance(result: BacktestResult) -> pd.Timedelta:
    """Kapsam ölçümünün bar payı: `start` tohumlanan bardır ve pencereye GİRMEZ.

    Bir barlık tolerans olmadan, veri tam olarak `start`te başlayan bir sembol bile
    "başlangıca ulaşmıyor" görünürdü — `run_backtest` pencereyi `ts > start` ile süzer.
    """
    del result
    return pd.Timedelta(days=1)


# --------------------------------------------------------------------------- #
# Teşhisler (§6h > 10) — GÖZLEM, karar değil
# --------------------------------------------------------------------------- #
def distribution(values: Sequence[float]) -> dict[str, Any]:
    """Bir dağılımın özeti; yüzdelikler `core/metrics.py::_percentile` ile."""
    clean = sorted(v for v in values if v is not None and not math.isnan(v))
    if not clean:
        nan = float("nan")
        return {"n": 0, "p5": nan, "p25": nan, "median": nan, "p75": nan, "p95": nan,
                "min": nan, "max": nan, "mean": nan}
    return {
        "n": len(clean),
        "p5": _percentile(clean, 0.05),
        "p25": _percentile(clean, 0.25),
        "median": _median(clean),
        "p75": _percentile(clean, 0.75),
        "p95": _percentile(clean, 0.95),
        "min": clean[0],
        "max": clean[-1],
        "mean": sum(clean) / len(clean),
    }


def exit_mix(result: BacktestResult) -> dict[str, Any]:
    """Çıkış sebebi dağılımı ve her grubun ort. R'si — kırılım `core/metrics.py`den.

    ⚠ Birimi **DİLİMDİR**, pozisyon değil (CLAUDE.md > Kırılımlar): üç aşamalı çıkış
    yönetimi kısmi çıkış üretir ve kısmi çıkışlı bir pozisyon İKİ gruba birden düşer.
    Yani grupların `trades` toplamı model tablosundan BÜYÜK olur; bu bir tutarsızlık
    değil, kırılımın tanımıdır ve rapor başlığına yazılır.
    """
    groups = (result.breakdowns or {}).get("exit_rule") or {}
    rows = groups.get(MODEL) or {}
    counts = {key: int(row["trades"]) for key, row in rows.items() if row.get("trades")}
    total = sum(counts.values())
    return {
        "unit": "fill",
        "unit_note": (
            "Kısmi çıkışlı pozisyon İKİ gruba düşer; grupların toplamı model tablosundan "
            "büyüktür (CLAUDE.md > Kırılımlar)."
        ),
        "counts": counts,
        "share": {k: v / total for k, v in counts.items()} if total else {},
        "avg_r": {k: row.get("avg_r") for k, row in rows.items() if row.get("trades")},
        "total_fills": total,
    }


def stop_distance_profile(trades: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Stop mesafesi % dağılımı VE kovaya göre `cost_per_r` ile net R.

    Birim POZİSYONDUR (`merge_fills`): dilim başına okumak, kısmi çıkış yapan
    pozisyonları iki kez sayardı. Kova metrikleri `core/metrics.py::breakdown` ile
    hesaplanır — yani ortalama R ve `cost_per_r` burada YENİDEN tanımlanmaz.
    """
    positions = merge_fills(trades)
    values = [_stop_distance_pct(row) for row in positions]
    per_bucket = breakdown(trades, key=lambda row: _stop_bucket(_stop_distance_pct(row)))
    return {
        "unit": "position",
        "distribution_pct": distribution(values),
        "bucket_edges_pct": list(STOP_DISTANCE_BUCKET_EDGES),
        "buckets": {
            name: {
                "trades": stats.trades,
                "avg_r": stats.avg_r,
                "cost_per_r": stats.cost_per_r,
                "avg_stop_distance_pct": stats.avg_stop_distance_pct,
                "win_rate": stats.win_rate,
            }
            for name, stats in sorted(per_bucket.items())
        },
    }


def _stop_distance_pct(row: Mapping[str, Any]) -> float:
    entry, stop = _number(row.get("entry_price")), _number(row.get("stop_price"))
    if entry is None or stop is None or entry <= 0.0:
        return float("nan")
    return abs(entry - stop) / entry * 100.0


def _stop_bucket(value: float) -> str:
    """Kova ADI; kenarlar modül sabitinden gelir ve veriden türetilmez."""
    if math.isnan(value):
        return "ölçülemedi"
    bounds = [0.0, *STOP_DISTANCE_BUCKET_EDGES, math.inf]
    for low, high in zip(bounds[:-1], bounds[1:]):
        if low <= value < high:
            return f"[{low:g}%, {high:g}%)" if math.isfinite(high) else f"≥{low:g}%"
    return "ölçülemedi"


def combo_grid(trades: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Kombinasyon başına n ve net ort. R — 12 HÜCRELİ ızgara.

    ⚠ Uyarı raporun İÇİNDE durur, okuyucunun hafızasında değil: bu bir çoklu
    karşılaştırma yüzeyidir ve en iyi hücreye bakıp "şu kombinasyon çalışıyor" demek,
    sicilin (§6c) engellemek için var olduğu şeyin hücre düzeyindeki hâlidir. Bandit
    zaten tahsis ediyor; tablo tahsisin NE gördüğünü anlatır, bir seçim önerisi değildir.
    """
    rows = breakdown(trades, key=lambda row: find_tag(str(row.get("signal_reason", "")), "combo") or "etiketsiz")
    return {
        "cells": len(rows),
        "warning": (
            "12 hücreli bir ızgaradır ve BH düzeltmesi (§6c) yapılmadan OKUNAMAZ. En iyi "
            "hücreyi seçmek bir bulgu değil, çoklu karşılaştırmanın kendisidir."
        ),
        "grid": {
            name: {
                "trades": stats.trades,
                "avg_r": stats.avg_r,
                "avg_r_ci_low": stats.avg_r_ci_low,
                "avg_r_ci_high": stats.avg_r_ci_high,
                "win_rate": stats.win_rate,
                "cost_per_r": stats.cost_per_r,
            }
            for name, stats in sorted(rows.items())
        },
    }


def reward_risk_profile(trades: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Giriş anındaki FİİLİ R:R dağılımı: `|TP − giriş| / |giriş − stop|`.

    Kaynakta dayatılmış bir R:R kapısı yoktur (ev kapıları uygulanmıyor, §6h > 3h), yani
    oran kurulumdan DOĞAR. Dağılım, geometrinin gerçekte ne ürettiğini gösterir.
    """
    values = [_reward_risk(row) for row in merge_fills(trades)]
    return {"unit": "position", "distribution": distribution(values)}


def _reward_risk(row: Mapping[str, Any]) -> float:
    """`|hedef − dolum| / |dolum − ilk stop|`.

    Hedef `reason` kuyruğundaki `target=` etiketinden okunur: `trades.csv`de TP kolonu
    YOKTUR ve yeni kolon açılamaz (kural 13c). Payda İLK stop'tur (defterin `stop_price`
    kolonu), yani R'nin paydasıyla aynı — trailing ile çekilmiş bir stop kullanmak
    `cost_per_r`yi şişirmenin oran tarafındaki hâli olurdu (CLAUDE.md > Tanım kararları).

    Giriş DOLUM fiyatıdır, sinyal barının kapanışı değil: "fiili" R:R tam olarak budur
    (kural 13 — emir bir sonraki barın açılışından dolar ve oran o boşluk kadar kayar).
    """
    entry = _number(row.get("entry_price"))
    stop = _number(row.get("stop_price"))
    tag = find_tag(str(row.get("signal_reason", "")), "target")
    target = _number(tag) if tag is not None else None
    if entry is None or stop is None or target is None:
        return float("nan")
    risk = abs(entry - stop)
    if risk <= 0.0:
        return float("nan")
    return abs(target - entry) / risk


def direction_and_symbol(result: BacktestResult) -> dict[str, Any]:
    """Yön ve sembol kırılımı — ikisi de `core/metrics.py`den, hesaplanmaz."""
    row = next((m for m in result.metrics if m.model == MODEL), None)
    symbols = ((result.breakdowns or {}).get("symbol") or {}).get(MODEL) or {}
    return {
        "direction": None if row is None else {
            side: {
                "trades": stats.trades,
                "avg_r": stats.avg_r,
                "avg_r_ci_low": stats.avg_r_ci_low,
                "avg_r_ci_high": stats.avg_r_ci_high,
                "win_rate": stats.win_rate,
                "cost_per_r": stats.cost_per_r,
                "avg_stop_distance_pct": stats.avg_stop_distance_pct,
            }
            for side, stats in (("long", row.long), ("short", row.short), ("total", row.total))
        },
        "symbol": {
            name: {
                "trades": stats.trades,
                "avg_r": stats.avg_r,
                "cost_per_r": stats.cost_per_r,
                "buy_hold_pct": result.buy_hold.get(name),
            }
            for name, stats in sorted(symbols.items())
        },
    }


# --------------------------------------------------------------------------- #
# Ön-kayıtlı tahminler (§6h > 8)
# --------------------------------------------------------------------------- #
def evaluate_predictions(
    *, metrics_row: Mapping[str, Any] | None, cost_per_r_median: float, gross_avg_r: float
) -> dict[str, Any]:
    """P1–P4'ü MEKANİK okur. Hiçbir eşik burada seçilmez; modül sabitlerinden gelir."""
    trades = int((metrics_row or {}).get("trades") or 0)
    net_avg_r = _number((metrics_row or {}).get("avg_r"))

    return {
        "P1_net_avg_r": {
            "prediction": f"≤ {P1_MAX_NET_AVG_R:g}",
            "measured": net_avg_r,
            "holds": _holds(net_avg_r, lambda v: v <= P1_MAX_NET_AVG_R),
            "source": "kaynağın GÖRÜLMÜŞ defterinden türetildi (net −0.044); tutması "
                      "sürpriz DEĞİLDİR ve bir doğrulama olarak okunmaz",
        },
        "P2_cost_per_r_median": {
            "prediction": f"≥ {P2_MIN_COST_PER_R_MEDIAN:g}",
            "measured": cost_per_r_median,
            "holds": _holds(cost_per_r_median, lambda v: v >= P2_MIN_COST_PER_R_MEDIAN),
            "source": "kaynağın GÖRÜLMÜŞ defteri (medyan 0.081); birim MEDYANDIR — "
                      "dağılım sağa çarpık ve ortalama tipik friksiyonu anlatmaz",
        },
        "P3_gross_avg_r": {
            "prediction": f"< {P3_MAX_GROSS_AVG_R:g}",
            "measured": gross_avg_r,
            "holds": _holds(gross_avg_r, lambda v: v < P3_MAX_GROSS_AVG_R),
            "note": "P1'den BAĞIMSIZ eksen: P1 'para kazanıyor mu', P3 'sinyalde sapma "
                    "var mı' diye sorar (§6h > 8).",
        },
        "P4_sample": {
            "prediction": f"n ≥ {P4_MIN_TRADES}",
            "measured": trades,
            "holds": trades >= P4_MIN_TRADES,
            "binding": True,
            "note": "Hem tahmin hem BAĞLAYICI okuma kapısı (K-2 analogu, §6h > 7): "
                    "n < 300 ise dönem A satırı OKUNMAZ ve Aşama 2 varyant TÜRETMEZ. "
                    "Eşik sonuca göre İNDİRİLMEZ.",
        },
    }


# --------------------------------------------------------------------------- #
# EK-1 ölçümleri (§6h > EK-1) — hipotez DEĞİL, BH paydasına girmez
# --------------------------------------------------------------------------- #
S1_MAX_RELATIVE_GAP = 0.10       # avg_stop_distance_pct bağıl farkı; aşarsa C-2 OKUNMAZ
S2_FLIP_SHARE = 0.5              # adil yazı-tura
S2_TOLERANCE = 0.05
C2_MARGIN_R = 0.15               # §4'teki hâliyle; burada SEÇİLMEZ, alıntılanır


def evaluate_addendum(
    *,
    model_row: Mapping[str, Any] | None,
    control_row: Mapping[str, Any] | None,
    control_flips: Mapping[str, int],
    control_flag: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """S1, S2, M1 ve C-2'yi MEKANİK okur. Hiçbir eşik burada seçilmez.

    **S1 bir KAPIDIR ve C-2'nin ÖNÜNDE durur:** iki model aynı maliyet ölçeğinde değilse
    aralarındaki ortalama R farkı "yönün ölçüsü" olmaktan çıkar. Bu yüzden S1 düşerse
    C-2 `readable=False` ile döner ve sonucu YORUMLANMAZ.
    """
    model_stop = _number((model_row or {}).get("avg_stop_distance_pct"))
    control_stop = _number((control_row or {}).get("avg_stop_distance_pct"))
    gap = None
    if model_stop is not None and control_stop is not None:
        base = max(abs(model_stop), abs(control_stop))
        gap = abs(model_stop - control_stop) / base if base > 0.0 else 0.0

    s1_holds = _holds(gap, lambda v: v < S1_MAX_RELATIVE_GAP)

    flipped = int(control_flips.get(FLIPPED, 0))
    same = int(control_flips.get(SAME, 0))
    total_flips = flipped + same
    flip_share = flipped / total_flips if total_flips else float("nan")

    control_avg_r = _number((control_row or {}).get("avg_r"))
    control_cost = _number((control_row or {}).get("cost_per_r"))
    ci_low = _number((control_row or {}).get("avg_r_ci_low"))
    ci_high = _number((control_row or {}).get("avg_r_ci_high"))
    expected = None if control_cost is None else -control_cost
    m1_covers = None
    if expected is not None and ci_low is not None and ci_high is not None:
        m1_covers = bool(ci_low <= expected <= ci_high)

    model_avg_r = _number((model_row or {}).get("avg_r"))
    diff = None
    if model_avg_r is not None and control_avg_r is not None:
        diff = model_avg_r - control_avg_r
    # Farkın bootstrap CI alt sınırı kabul bayrağından okunur — ikinci bir bootstrap
    # hesaplamak, aynı defterin iki farklı kesinlik ölçüsü demekti (kural 7).
    diff_ci_low = _number((control_flag or {}).get("edge_diff_ci_low"))

    return {
        "S1_stop_scale": {
            "threshold_relative_gap": S1_MAX_RELATIVE_GAP,
            "model_avg_stop_distance_pct": model_stop,
            "control_avg_stop_distance_pct": control_stop,
            "relative_gap": gap,
            "holds": s1_holds,
            "note": (
                "Tolerans %10 ve koşudan ÖNCE sabit (§6h > EK-1): bandit posteriorları, "
                "dolumlar ve max_short_positions×yön etkileşimi iki modelin aynı "
                "kurulumları görmesini garanti etmez. Aşarsa C-2 OKUNMAZ."
            ),
        },
        "S2_flip_share": {
            "expected": S2_FLIP_SHARE,
            "tolerance": S2_TOLERANCE,
            "flipped": flipped,
            "same": same,
            "measured": flip_share,
            "holds": _holds(
                flip_share, lambda v: abs(v - S2_FLIP_SHARE) <= S2_TOLERANCE
            ),
        },
        "M1_control_avg_r": {
            "measured": control_avg_r,
            "ci": [ci_low, ci_high],
            "expected_point": expected,
            "covers_minus_cost_per_r": m1_covers,
            "note": (
                "KAPI DEĞİL, tutarlılık kontrolü: bilgisiz yönün beklenen değeri sıfır, "
                "gerçekleşen R friksiyon kadar altındadır. Kapsamıyorsa önce yansıtmanın "
                "mesafeyi bozup bozmadığı araştırılır."
            ),
        },
        "C2_edge_vs_control": {
            "margin_r": C2_MARGIN_R,
            "model_avg_r": model_avg_r,
            "control_avg_r": control_avg_r,
            "difference": diff,
            "diff_ci_low": diff_ci_low,
            # S1 düşerse okunmaz: farklı maliyet ölçeğinde bir fark yönün ölçüsü değildir.
            "readable": bool(s1_holds) if s1_holds is not None else False,
            "holds": (
                None
                if diff is None or not s1_holds
                else bool(diff >= C2_MARGIN_R and (diff_ci_low or float("-inf")) > 0.0)
            ),
        },
    }


def count_flips(trades: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    """Kontrolün yazı-tura dökümü; POZİSYON başına (dilim değil).

    Okuma yolu TEKTİR (`WaveCoinflip.coin_of`): etiketi ikinci bir yerde ayrıştırmak,
    S2'nin iki farklı cevabı olabilmesi demekti.
    """
    counts = {SAME: 0, FLIPPED: 0, "etiketsiz": 0}
    for row in merge_fills(trades):
        coin = WaveCoinflip.coin_of(row.get("signal_reason", ""))
        counts[coin if coin in (SAME, FLIPPED) else "etiketsiz"] += 1
    return counts


def _holds(value: float | None, test) -> bool | None:
    """`nan`/None bir dalı TETİKLEMEZ: ölçülemeyen bir tahmin tutmuş da düşmüş de sayılmaz."""
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return None
    return bool(test(value))


def gross_avg_r(trades: Sequence[Mapping[str, Any]]) -> float:
    """Friksiyon ÖNCESİ ortalama R: `Σ(pnl + komisyon + kayma) / Σrisk`.

    R'nin tanımı `core/metrics.py`nindir (`Σpnl / Σrisk`, pozisyon bazında) ve burada
    DEĞİŞTİRİLMEZ — yalnızca payın maliyet bileşeni geri eklenir, çünkü P3 tam olarak
    "sinyalin taşıdığı BRÜT sapma" hakkında bir tahmindir. Payda aynı kalır ki iki sayı
    aynı birimde okunsun.
    """
    total_pnl = 0.0
    total_cost = 0.0
    total_risk = 0.0
    for row in merge_fills(trades):
        risk = _number(row.get("risk_amount"))
        pnl = _number(row.get("pnl"))
        if risk is None or pnl is None or risk <= 0.0:
            continue
        total_pnl += pnl
        total_cost += (_number(row.get("fee")) or 0.0) + (_number(row.get("slippage_cost")) or 0.0)
        total_risk += risk
    return (total_pnl + total_cost) / total_risk if total_risk > 0.0 else float("nan")


def cost_per_r_median(trades: Sequence[Mapping[str, Any]]) -> float:
    """Pozisyon bazlı `cost_per_r` değerlerinin MEDYANI (§6h > 8, P2).

    `core/metrics.py` bu oranın model/yön bazında ORTALAMASINI raporlar; medyan ayrı bir
    metrik değil aynı sayıların başka bir özetidir ve ön-kayıt onu istiyor. Oranın
    TANIMI kopyalanmaz: pay ve payda defterin kendi kolonlarıdır (kural 7'nin sınırı,
    "aynı oranı ikinci kez TANIMLAMA" — ikinci kez ÖZETLEMEK serbesttir).
    """
    values: list[float] = []
    for row in merge_fills(trades):
        risk = _number(row.get("risk_amount"))
        if risk is None or risk <= 0.0:
            continue
        cost = (_number(row.get("fee")) or 0.0) + (_number(row.get("slippage_cost")) or 0.0)
        values.append(cost / risk)
    return _median(sorted(values)) if values else float("nan")


# --------------------------------------------------------------------------- #
# Hold-out koruması
# --------------------------------------------------------------------------- #
def guard_window(
    *, signal_cutoff: pd.Timestamp, tail_end: pd.Timestamp, confirm_holdout: bool
) -> None:
    """Dönem B'ye onaysız, görülmüş veriye HİÇ dokunulmaz.

    **Dönem B'yi açan şey BARLARIN İŞLENMESİ değil, YENİ SİNYAL üretilmesidir.** Ölçüt bu
    yüzden `signal_cutoff`tır, pencerenin ucu değil: A'nın kuyruğu tanımı gereği kesimden
    sonraki barları işler (pozisyon yönetimi, kural 13) ve zaman olarak B ile örtüşür, ama
    orada tek bir sinyal bile üretmez. Ucu ölçüt yapmak, A'nın kendi kuyruğunu "hold-out'a
    dokunma" sayıp varsayılan koşuyu reddetmek olurdu.

    İki kapı iki AYRI şey söyler ve biri diğerinin yerine geçmez:

    - `--confirm-holdout` dönem B'yi açar, yani kesimin A'nın ötesine taşınmasına izin
      verir (Aşama 2 onu kullanacaktır).
    - **Görülmüş veri hiçbir bayrakla açılmaz.** Orası bir hold-out değil, kirlenmiş bir
      penceredir: kaynağın canlı defteri (09.09–21.09.2026) okundu. Ölçüt burada
      pencerenin UCUDUR — o barların bir pozisyon yönetimi için bile işlenmesi, ölçümü
      görülmüş fiyatlara bağlardı.
    """
    if tail_end >= pd.Timestamp(SEEN_DATA_START):
        raise HoldoutError(
            f"pencere görülmüş veriye uzanıyor (≥ {SEEN_DATA_START}): kaynağın canlı "
            "defteri okundu, o pencere hiçbir döneme giremez ve bayrakla açılamaz"
        )
    if signal_cutoff > pd.Timestamp(PERIOD_A_CUTOFF) and not confirm_holdout:
        raise HoldoutError(
            f"sinyal kesimi dönem A'nın ötesinde ({signal_cutoff} > {PERIOD_A_CUTOFF}) "
            "ama --confirm-holdout verilmedi: Aşama 1 YALNIZCA dönem A'yı koşar (§6h > 5)"
        )


# --------------------------------------------------------------------------- #
# main
# --------------------------------------------------------------------------- #
def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    a_start = pd.Timestamp(args.a_start)
    a_cutoff = pd.Timestamp(args.a_cutoff)
    a_tail_end = pd.Timestamp(args.a_tail_end)
    try:
        guard_window(
            signal_cutoff=a_cutoff,
            tail_end=a_tail_end,
            confirm_holdout=args.confirm_holdout,
        )
    except HoldoutError as exc:
        logger.error("%s", exc)
        return 2

    config = load_config(args.config)
    layer = resolve_layer(config, LAYER)
    universe = [str(s) for s in config["wave"]["clone"]["universe"]]
    out_root = Path(args.out_dir)

    # --- Dönem A, PORTFÖY koşusu (birincil satır) --------------------------
    period_a = run_period(
        name="A", start=a_start, end=a_tail_end, out_root=out_root,
        history_bars=args.history_bars, funding_periods=args.funding_periods,
        signal_cutoff=a_cutoff, config_path=args.config,
    )

    # --- KAPSAM KAPISI: ana sayılar okunmadan ÖNCE -------------------------
    coverage = coverage_gate(period_a, universe=universe, period_start=a_start)
    print(json.dumps({"coverage_gate": coverage}, indent=2, ensure_ascii=False, default=str))
    if not coverage["passed"]:
        logger.error(
            "KAPSAM KAPISI DÜŞTÜ — pencere KAYDIRILMAZ, koşu duruyor. Başlangıca "
            "ulaşamayan semboller: %s; B-2: %s",
            coverage["symbols_not_reaching_start"] or "—", coverage["integrity_B2"],
        )
        return 3

    trades = model_trades(period_a)
    metrics_row = _metrics_row(period_a)

    # --- EK-1: KONTROL koşusu (§6h > EK-1) ---------------------------------
    # `wave_scalp` YENİDEN KOŞULMAZ; kontrol kendi koşusunda, aynı pencere/seed/config ile
    # koşar. Kabul bayrağı farkın iki tarafını da gördüğü için kontrol koşusunda İKİ model
    # birlikte verilir — ayrı defterlere yazarlar (model adı = defter klasörü) ama
    # `acceptance_flags` farkı tek bir kümeden okur.
    control_result = None
    control_row = None
    control_flips: dict[str, int] = {}
    addendum = None
    if not args.skip_control:
        control_result = run_period(
            name="A-control", start=a_start, end=a_tail_end, out_root=out_root,
            history_bars=args.history_bars, funding_periods=args.funding_periods,
            signal_cutoff=a_cutoff, models=[MODEL, CONTROL], config_path=args.config,
        )
        control_row = _metrics_row(control_result, CONTROL)
        control_flips = count_flips(model_trades(control_result, CONTROL))
        addendum = evaluate_addendum(
            model_row=_metrics_row(control_result, MODEL),
            control_row=control_row,
            control_flips=control_flips,
            control_flag=_flag(control_result, MODEL),
        )
    diagnostics = {
        "exit_mix": exit_mix(period_a),
        "stop_distance": stop_distance_profile(trades),
        "holding": dict(period_a.holding.get(MODEL) or {}),
        "combo_grid": combo_grid(trades),
        "reward_risk": reward_risk_profile(trades),
        "breakdowns": direction_and_symbol(period_a),
    }

    # --- Coin başına koşu = BİLGİ (kota bağlamaz, kapı üretmez) ------------
    per_symbol: dict[str, Any] = {}
    if not args.skip_per_symbol:
        for symbol in universe:
            single = run_period(
                name=f"A-{symbol}", start=a_start, end=a_tail_end, out_root=out_root,
                history_bars=args.history_bars, funding_periods=args.funding_periods,
                signal_cutoff=a_cutoff, symbols=[symbol], config_path=args.config,
            )
            per_symbol[symbol] = {
                "metrics": _metrics_row(single),
                "holding": dict(single.holding.get(MODEL) or {}),
            }

    payload: dict[str, Any] = {
        "phase": 1,
        "layer": LAYER,
        "model": MODEL,
        "preregistration": PREREGISTRATION,
        "holdout_status": "dönem B KAPALI — bu koşuda hiç çalıştırılmadı",
        "windows": {
            "A": {"start": str(a_start), "signal_cutoff": str(a_cutoff),
                  "tail_end": str(a_tail_end)},
            "B": {"start": PERIOD_B_START, "end": PERIOD_B_END, "status": "KOŞULMADI"},
            "excluded_seen_data": {"from": SEEN_DATA_START,
                                   "reason": "kaynağın canlı defteri okundu"},
        },
        "coverage_gate": coverage,
        "deviations": dict(period_a.deviations or {}),
        "portfolio": {
            "metrics": metrics_row,
            "acceptance": _flag(period_a),
            "skipped_signals_stop_band": int(_skipped_signals(period_a)),
            # Kuyruğun ucunda hâlâ AÇIK olan pozisyonlar: kapanmış işlem istatistiğine
            # GİRMEZLER (gerçekleşmemiş bir sonucu ölçüme sokmak olurdu) ama sayıları
            # raporlanır — sıfırdan büyükse ölçülen embargo bir ALT SINIRDIR.
            "open_at_tail_end": _open_positions(period_a),
        },
        "diagnostics": diagnostics,
        # EK-1: kontrol satırı ve ölçümleri. `control` alanı YOKSA kontrol koşulmadı
        # demektir ve C-2 yine değerlendirilemez — eksik bir çıta, geçilmiş çıta gibi
        # görünmemeli.
        "control": {
            "model": CONTROL,
            "preregistration": f"{PREREGISTRATION} > EK-1",
            "selected_explicitly": True,
            "layer_default_not_used": "random_ctrl",
            "metrics": control_row,
            "acceptance": None if control_result is None else _flag(control_result, CONTROL),
            "flips": control_flips,
            "paired_run_metrics_model": (
                None if control_result is None else _metrics_row(control_result, MODEL)
            ),
        },
        "addendum_measurements": addendum,
        "per_symbol_INFORMATIONAL": per_symbol,
        # Embargo dönem A'dan ÖLÇÜLÜR ve burada yalnızca KAYDEDİLİR; Aşama 2 onu
        # kullanacaktır. Model zaman stop'u taşımadığı için varsayılamaz (§6h > 5).
        "measured_embargo_bars": _max_holding_bars(period_a),
    }
    payload["predictions"] = evaluate_predictions(
        metrics_row=metrics_row,
        cost_per_r_median=cost_per_r_median(trades),
        gross_avg_r=gross_avg_r(trades),
    )

    out_root.mkdir(parents=True, exist_ok=True)
    (out_root / "results.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False, default=str), encoding="utf-8"
    )
    dest = Path(args.report)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False, default=str), encoding="utf-8"
    )
    logger.info("sonuç yazıldı: %s ve %s", out_root / "results.json", dest)

    print(json.dumps(
        {"predictions": payload["predictions"],
         "portfolio": payload["portfolio"],
         "addendum_measurements": payload["addendum_measurements"],
         "control": {k: payload["control"][k] for k in ("model", "metrics", "flips")},
         "measured_embargo_bars": payload["measured_embargo_bars"]},
        indent=2, ensure_ascii=False, default=str,
    ))
    return 0


def _metrics_row(result: BacktestResult, model: str = MODEL) -> dict[str, Any] | None:
    """Model satırının özeti — hiçbir sayı burada hesaplanmaz (kural 7)."""
    for item in result.metrics:
        if item.model != model:
            continue
        return {
            "trades": item.total.trades,
            "avg_r": item.total.avg_r,
            "avg_r_ci_low": item.total.avg_r_ci_low,
            "avg_r_ci_high": item.total.avg_r_ci_high,
            "median_r": item.total.median_r,
            "win_rate": item.total.win_rate,
            "avg_win_r": item.total.avg_win_r,
            "avg_loss_r": item.total.avg_loss_r,
            "payoff": item.total.payoff,
            "pnl": item.total.pnl,
            "profit_factor": item.total.profit_factor,
            "cost_per_r": item.total.cost_per_r,
            "cost_pct": item.total.cost_pct,
            "avg_stop_distance_pct": item.total.avg_stop_distance_pct,
            "max_drawdown_pct": _pct(item.account.max_drawdown),
            "total_return_pct": _pct(item.account.total_return),
        }
    return None


def _flag(result: BacktestResult, model: str = MODEL) -> dict[str, Any] | None:
    return next((dict(f.__dict__) for f in result.acceptance if f.model == model), None)


def _skipped_signals(result: BacktestResult) -> int:
    """Stop tavanının (kural 14) ELEDİĞİ sinyal sayısı — §6h > 3(i) raporlamayı ZORUNLU kılar."""
    model_report = result.report.by_model(MODEL)
    return int(getattr(model_report, "skipped_signals", 0) or 0)


def _open_positions(result: BacktestResult) -> int:
    """Kuyruğun ucunda AÇIK kalan pozisyon sayısı — koşunun kendi durum dosyasından.

    `Ledger.load_state` ile okunur, dosya elle ayrıştırılmaz: durumun şeması
    `core/ledger.py`nindir ve ikinci bir okuyucu, bir alan adı değiştiğinde sessizce
    sıfır döndürürdü.
    """
    state = Ledger(result.out_dir / "ledger").load_state(MODEL)
    rows = (state or {}).get("positions")
    return len(rows) if isinstance(rows, list) else 0


def _max_holding_bars(result: BacktestResult) -> float:
    return float((result.holding.get(MODEL) or {}).get("max_bars") or 0.0)


def _pct(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return None if number != number else number * 100.0


def _number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(number) else number


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out-dir", default="backtests/wave")
    parser.add_argument("--report", default="docs/data/backtest_wave_scalp_A.json")
    parser.add_argument("--config", default=None)
    # Sınıf-1 bayraklar (§5g): yalnızca DERİNLEŞTİRİR, sonucu kaydırmaz.
    #
    # ⚠ DEĞER KEYFİ DEĞİL, ARİTMETİKTİR. `core/data.py::_download_candles` barları ŞU ANDAN
    # geriye doğru sayfalar ve `data.history_bars` kadar bar toplayınca DURUR — pencerenin
    # başına ATLAMAZ. Yani derinlik, bugünden dönem A'nın başına kadarki TÜM mesafeyi
    # kapsamalı, yalnızca pencerenin kendisini değil:
    #
    #     2026-09 → 2025-03-01 ≈ 570 gün × 96 bar/gün ≈ 54.720 bar
    #     + zigzag penceresi (300) + ATR ısınması (14)
    #
    # 60.000 o mesafeye pay bırakır. Yetmezse kapsam KAPISI düşer ve teşhis yanıltıcı olur:
    # rapor "OKX veriyi vermiyor" derken aslında "biz o kadar geriye İSTEMEDİK" demiş
    # olurdu. Emsali `scripts/backtest_ema.py`nin 12.000'idir (4H'de ≈ 5,5 yıl).
    #
    # Bedeli istek sayısıdır: 60.000 / 100 ≈ 600 sayfa × 13 sembol ≈ 7.800 istek ve
    # `exchange.min_request_interval_sec` (0.15) ile en az ~20 dakika. Workflow'un
    # timeout'u (180 dk) buna göre seçildi.
    parser.add_argument("--history-bars", type=int, default=60000)
    parser.add_argument("--funding-periods", type=int, default=2000)
    parser.add_argument(
        "--skip-per-symbol", action="store_true",
        help="coin başına koşuyu atla (BİLGİ koşusudur, birincil satırı etkilemez)",
    )
    parser.add_argument(
        "--skip-control", action="store_true",
        help="EK-1 kontrol koşusunu atla; C-2 o zaman DEĞERLENDİRİLEMEZ kalır",
    )
    # Pencereler ön-kayıtlıdır; bayraklar yalnızca tekrarlanabilirlik için açıktır,
    # dönem B'ye bakmayı kolaylaştırmak için DEĞİL.
    parser.add_argument("--a-start", default=PERIOD_A_START)
    parser.add_argument("--a-cutoff", default=PERIOD_A_CUTOFF, help="dönem A sinyal kesimi")
    parser.add_argument("--a-tail-end", default=PERIOD_A_TAIL_END)
    parser.add_argument(
        "--confirm-holdout", action="store_true",
        help="dönem B'ye dokunan bir pencereyi AÇAR (Aşama 1'de kullanılmaz)",
    )
    return parser.parse_args(argv)


if __name__ == "__main__":
    raise SystemExit(exit_code_of(main))

"""`xsec_mom`un ÖN-KAYITLI koşusu (docs/backtest.md > 6g). Ölçümün parçası DEĞİL.

İki dönemi ön-kayıtta sabitlenen SIRAYLA çağırır (A → embargo ölçümü → B) ve tek bir
versiyonlanabilir yüke indirir. **İkinci bir backtest DEĞİLDİR** —
`scripts/backtest.py::run_backtest`i çağırır ve **hiçbir metrik burada hesaplanmaz**
(kural 7): bir ortalama, bir kâr faktörü ya da bir kabul bayrağı burada hesaplansaydı
aynı defterin iki cevabı olurdu. Kapılar `core/metrics.py::acceptance_flags`ten okunur —
canlı tablo ile backtest tablosunun aynı çıtayı göstermesi buna bağlıdır.

**Pencereler `scripts/backtest_ema.py`den İTHAL EDİLİR.** İki yerde yazılı bir pencere
bir gün ayrışır ve iki modelin sonucu farklı piyasa geçmişlerine dayanmaya başlar; oysa
ön-kayıt onları kasten aynı seçti (§6g > "Pencereler ve embargo").

**COIN BAŞINA KOŞU YOKTUR ve bu yapısaldır** (§6g > K-1): top-3 seçimi tanımı gereği tüm
evrene aynı anda bakar. Tek sembollü bir koşuda sıralama tek elemanlıdır, yani model
artık kesitsel momentum değil "her hafta bu sembolü al" olur — kapıyı zorla uygulamak,
ölçülmek isteneni ölçmemek demekti.

**Dönem B'ye, A koşulup embargo ÖLÇÜLMEDEN dokunulmaz.** Model zaman stop'u taşımadığı
için §6.1'in dayandığı üst sınır tanım gereği yoktur; embargo A'da gözlenen azami tutuş
süresidir ve `measured_embargo_bars` ile ölçülür (yine `backtest_ema`den ithal — yöntemin
tek kopyası).

Tetikleyicisi `.github/workflows/backtest-xsec.yml` (yalnızca `workflow_dispatch`, cron
yok — §7).
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd  # noqa: E402

from core.config import load_config  # noqa: E402
from core.layers import resolve_layer  # noqa: E402
from scripts.backtest import exit_code_of, BacktestResult, run_backtest  # noqa: E402
from scripts.backtest_ema import (  # noqa: E402
    PERIOD_A_CUTOFF,
    PERIOD_A_START,
    measured_embargo_bars,
)

logger = logging.getLogger("backtest_xsec")

LAYER = "xsec"
MODEL = "xsec_mom"
CONTROL = "xsec_random"

# §6g'nin model sahibi kapıları. K-1 UYGULANMAZ (portföy modeli, yukarısı).
K3_MAX_DRAWDOWN_PCT = 25.0
# K-2 BAĞLAYICI DEĞİL (§6g > TADİLAT-1): bağlayıcı kapılar CI bazlı ve örneklem
# yeterliliğini zaten içeriyor. Eşik burada yalnızca RAPORLAMA için duruyor ve
# `passed` alanı ÜRETİLMEZ — bir eşik değeri yazıp bağlayıcı saymamak, sonraki
# okuyucunun onu kapı sanmasına açık kapı bırakırdı.
K2_REFERENCE_TRADES = 300

# Ön-kayıtlı P1: çıkışların ≥ %70'i rebalance, ≤ %30'u stop.
P1_MIN_REBALANCE_SHARE = 0.70
REBALANCE_EXIT_KEY = "signal:rebalance"


def run_period(
    *,
    name: str,
    start: pd.Timestamp,
    end: pd.Timestamp,
    out_root: Path,
    models: Sequence[str],
    history_bars: int,
    funding_periods: int,
    signal_cutoff: pd.Timestamp | None = None,
    embargo_bars: int = 0,
    config_path: str | None = None,
) -> BacktestResult:
    """Bir dönemin PORTFÖY koşusu. Tek koşu sınıfı var; coin başına koşu yok."""
    logger.info("[%s] portföy koşusu: %d model", name, len(models))
    return run_backtest(
        layer_name=LAYER,
        start=start,
        end=end,
        out_dir=out_root / name,
        models=list(models),
        history_bars=history_bars,
        funding_periods=funding_periods,
        signal_cutoff=signal_cutoff,
        embargo_bars=embargo_bars or None,
        config_path=config_path,
    )


def _flag(result: BacktestResult, model: str) -> Mapping[str, Any] | None:
    return next((dict(f.__dict__) for f in result.acceptance if f.model == model), None)


def _metrics_row(result: BacktestResult, model: str) -> Mapping[str, Any] | None:
    """Model satırının ÖZETİ — hiçbir sayı burada hesaplanmaz, `core/metrics.py`den gelir.

    K-3 için hesap düzeyi drawdown okunur (§6g): `ema_trend`de coin başına kümülatif PnL
    eğrisinden hesaplanıyordu çünkü orada tek-sembollü koşular vardı; `xsec_mom` portföy
    modeli olduğu için doğrudan hesabın kendi `max_drawdown`u doğru ölçüdür.
    """
    for item in result.metrics:
        if item.model != model:
            continue
        return {
            "trades": item.total.trades,
            "avg_r": item.total.avg_r,
            "avg_r_ci_low": item.total.avg_r_ci_low,
            "avg_r_ci_high": item.total.avg_r_ci_high,
            "win_rate": item.total.win_rate,
            "pnl": item.total.pnl,
            "profit_factor": item.total.profit_factor,
            "max_drawdown_pct": _pct(item.account.max_drawdown),
            "total_return_pct": _pct(item.account.total_return),
            "avg_stop_distance_pct": item.total.avg_stop_distance_pct,
            "cost_per_r": item.total.cost_per_r,
        }
    return None


def _pct(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return None if number != number else number * 100.0


def exit_mix(result: BacktestResult, model: str) -> dict[str, Any]:
    """Çıkış sebebi dağılımı — P1 BURADAN okunur, hesaplanmaz.

    Kırılımı `core/metrics.py::breakdown` üretir (katmanın `breakdowns` ayarı);
    buradaki iş yalnızca modelin satırlarını toplayıp payı yazmaktır. Bir ikinci
    kırılım uygulaması, aynı defterin iki cevabı demekti (kural 7).
    """
    groups = (result.breakdowns or {}).get("exit_rule") or {}
    rows = groups.get(model) or {}
    counts = {key: int(row["trades"]) for key, row in rows.items() if row.get("trades")}
    total = sum(counts.values())
    share = {key: value / total for key, value in counts.items()} if total else {}
    return {
        "counts": counts,
        "share": share,
        "total_fills": total,
        # Birim DİLİMDİR, pozisyon değil (CLAUDE.md > Kırılımlar): kısmi çıkışlı bir
        # pozisyon iki gruba birden düşer. Bu modelde kısmi çıkış YOK, yani pratikte
        # eşitler — ama varsayım yazılı dursun ki bir gün kısmi eklenirse fark edilsin.
        "unit": "fill",
    }


def evaluate_gates(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Ön-kayıtlı kapıları MEKANİK uygular. Hiçbir eşik burada SEÇİLMEZ.

    Repo kapıları (C-1..C-5) ve E kapısı `acceptance_flags`ten okunur; K-3 metrik
    satırından. K-2 raporlanır ama bir `passed` üretmez (TADİLAT-1).
    """
    periods = payload["periods"]
    repo = {period: data.get("acceptance_model") for period, data in periods.items()}

    drawdowns = {
        period: (data.get("model") or {}).get("max_drawdown_pct")
        for period, data in periods.items()
    }
    breaches = [
        {"period": period, "drawdown_pct": value}
        for period, value in drawdowns.items()
        if isinstance(value, (int, float)) and value == value and abs(value) > K3_MAX_DRAWDOWN_PCT
    ]

    trades = {
        period: ((data.get("model") or {}).get("trades") or 0)
        for period, data in periods.items()
    }

    exits = payload["periods"]["A"].get("exit_mix") or {}
    rebalance_share = (exits.get("share") or {}).get(REBALANCE_EXIT_KEY)

    gates: dict[str, Any] = {
        "repo_acceptance": repo,
        "K3_max_drawdown": {
            "threshold_pct": K3_MAX_DRAWDOWN_PCT,
            "measured": drawdowns,
            "breaches": breaches,
            "passed": not breaches,
        },
        # BAĞLAYICI DEĞİL: `passed` alanı bilerek YOK (TADİLAT-1). Referans eşik
        # yalnızca okuyucunun sayıyı bir ölçekle görmesi için duruyor.
        "K2_total_trades_REPORTED_ONLY": {
            "binding": False,
            "reference_threshold": K2_REFERENCE_TRADES,
            "measured": {**trades, "A+B": sum(trades.values())},
            "note": (
                "Bağlayıcı değil (§6g > TADİLAT-1): bağlayıcı kapılar CI bazlı ve "
                "örneklem yeterliliğini içeriyor. Eşik gevşetilmedi, kapsamı değişti."
            ),
        },
        # Bir TAHMİN, kapı DEĞİL: tasarım niyetinin (baskın çıkış rebalance olsun)
        # sınaması. Tutmaması modeli kötü yapmaz, 5×ATR'nin yetersiz olduğunu gösterir.
        "P1_exit_mix_PREDICTION": {
            "binding": False,
            "threshold_share": P1_MIN_REBALANCE_SHARE,
            "measured_share": rebalance_share,
            "holds": (
                isinstance(rebalance_share, float)
                and rebalance_share >= P1_MIN_REBALANCE_SHARE
            ),
        },
    }
    gates["verdict"] = _verdict(gates)
    return gates


def _verdict(gates: Mapping[str, Any]) -> str:
    """Karar METNİ de yükün içindedir (kural 7'nin karar kuralına uygulanmış hâli)."""
    repo = gates["repo_acceptance"]
    if any(flag is None for flag in repo.values()):
        return "DEĞERLENDİRİLEMEZ — en az bir dönemde kabul bayrağı yok"
    if not gates["K3_max_drawdown"]["passed"]:
        return "BLOKE — K-3 (max drawdown) aşıldı"

    passed = {period: bool(flag.get("passed")) for period, flag in repo.items()}
    if all(passed.values()):
        return "GEÇTİ — tüm bağlayıcı kapılar yeşil"

    # Çıpa istisnası (§6g, `ema_trend`dekiyle birebir): YALNIZCA çıpadan kalıyorsa DUR.
    #
    # `AcceptanceFlags` alt koşulları ayrı ayrı YAYINLAMAZ (`edge` bileşik bir bayraktır),
    # bu yüzden "hangi koşuldan kaldı" sorusu bayrağın KULLANDIĞI SAYILARDAN okunur —
    # ikinci bir eşik seçilmez, aynı alanlar yeniden sorulur. Yanlış türetmenin bedeli
    # sınırlıdır ve bu bilinçlidir: istisna yalnızca DUR üretir, asla otomatik GEÇTİ —
    # yani en kötü ihtimalle karar bir insana gider.
    if all(_fails_only_on_anchor(flag) for flag in repo.values() if not flag.get("passed")):
        return "DUR — yalnızca çıpa koşulundan kalıyor; karar kullanıcıya gider (otomatik geçiş YOK)"
    return "BLOKE — çıpa dışında en az bir kapıdan kalıyor"


def _fails_only_on_anchor(flag: Mapping[str, Any]) -> bool:
    """Örneklem, marj ve CI koşulları sağlanıyor ama hesap getirisi çıpanın ALTINDA mı?"""
    if not flag.get("sample"):
        return False
    avg_r, control = flag.get("avg_r"), flag.get("control_avg_r")
    margin, ci_low = flag.get("edge_margin_r"), flag.get("edge_diff_ci_low")
    total, benchmark = flag.get("total_return"), flag.get("benchmark_return")
    if not all(isinstance(v, (int, float)) and v == v
               for v in (avg_r, control, margin, ci_low, total, benchmark)):
        return False
    return (
        avg_r > 0.0
        and (avg_r - control) >= margin
        and ci_low > 0.0
        and total <= benchmark
    )


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    layer = resolve_layer(load_config(args.config), LAYER)
    models = list(layer.config["models"])
    out_root = Path(args.out_dir)
    a_start = pd.Timestamp(args.a_start)
    a_cutoff = pd.Timestamp(args.a_cutoff)

    # --- Dönem A -----------------------------------------------------------
    period_a = run_period(
        name="A", start=a_start, end=pd.Timestamp(args.a_tail_end), out_root=out_root,
        models=models, history_bars=args.history_bars,
        funding_periods=args.funding_periods, signal_cutoff=a_cutoff,
        config_path=args.config,
    )

    # --- Embargo: VARSAYILMAZ, A'dan ÖLÇÜLÜR -------------------------------
    embargo = measured_embargo_bars(period_a, model=MODEL)
    logger.info("dönem A'dan ölçülen embargo: %d bar", embargo)

    # --- Dönem B (A bitmeden BAŞLATILMAZ) ----------------------------------
    b_end = pd.Timestamp(args.b_end) if args.b_end else pd.Timestamp.now(tz="UTC").floor("h")
    period_b = run_period(
        name="B", start=a_cutoff, end=b_end, out_root=out_root, models=models,
        history_bars=args.history_bars, funding_periods=args.funding_periods,
        embargo_bars=embargo, config_path=args.config,
    )

    payload: dict[str, Any] = {
        "layer": LAYER,
        "model": MODEL,
        "control": CONTROL,
        "preregistration": "docs/backtest.md > 6g",
        "embargo_bars": embargo,
        "periods": {
            "A": {
                "start": str(a_start), "end": str(a_cutoff),
                "model": _metrics_row(period_a, MODEL),
                "control": _metrics_row(period_a, CONTROL),
                "acceptance_model": _flag(period_a, MODEL),
                "exit_mix": exit_mix(period_a, MODEL),
                "holding": dict(period_a.holding.get(MODEL) or {}),
            },
            "B": {
                "start": str(a_cutoff), "end": str(b_end),
                "model": _metrics_row(period_b, MODEL),
                "control": _metrics_row(period_b, CONTROL),
                "acceptance_model": _flag(period_b, MODEL),
                "exit_mix": exit_mix(period_b, MODEL),
                "holding": dict(period_b.holding.get(MODEL) or {}),
            },
        },
    }
    payload["gates"] = evaluate_gates(payload)

    out_root.mkdir(parents=True, exist_ok=True)
    results = out_root / "results.json"
    results.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    logger.info("sonuç yazıldı: %s", results)

    print(json.dumps(payload["gates"], indent=2, ensure_ascii=False, default=str))
    return 0


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out-dir", default="backtests/xsec")
    parser.add_argument("--config", default=None)
    # 3000 dönem A'yı karşılamıyordu (#35578057311, docs/decisions.md > 59); 12000
    # ema/dc'nin derinliği. Yetmeyen derinliği artık pencere kapısı reddeder.
    parser.add_argument("--history-bars", type=int, default=12000)
    parser.add_argument("--funding-periods", type=int, default=2000)
    # Pencereler ön-kayıtlıdır ve backtest_ema'den İTHAL EDİLİR; bayraklar yalnızca
    # tekrarlanabilirlik için açıktır, dönem B'ye bakmayı kolaylaştırmak için değil.
    parser.add_argument("--a-start", default=PERIOD_A_START)
    parser.add_argument("--a-cutoff", default=PERIOD_A_CUTOFF, help="dönem A sinyal kesimi")
    parser.add_argument("--a-tail-end", default=PERIOD_A_CUTOFF)
    parser.add_argument("--b-end", default=None, help="varsayılan: koşu anı")
    return parser.parse_args(argv)


if __name__ == "__main__":
    raise SystemExit(exit_code_of(main))

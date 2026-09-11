"""Tek bir turu uçtan uca çalıştıran giriş noktası.

    veri çek -> as_of -> config'deki modelleri çalıştır -> metrikleri üret -> rapor

Akış bilinçli olarak İNCEDİR: iş mantığının tamamı `core/` içindedir, burada yalnızca
sıra ve hata izolasyonu vardır. Cron bu dosyayı çağırır (.github/workflows/run.yml),
defterler ve `docs/data/metrics.json` koşudan sonra commit edilir.

**Hata izolasyonu iki katmanlıdır ve kasten farklıdır:**

- *Model kurulumu* burada izole edilir: `config.yaml`'da tanınmayan ya da kurulurken
  patlayan bir model, diğer dokuz modelin turunu düşürmez — yalnızca kendisi atlanır ve
  çıkış kodu 1 olur (sessiz eksik yarışma olmasın, kural 6).
- *Sinyal/çıkış üretimi* `core/engine.py` içinde izole edilir (kural 8): modelin kodu
  patlarsa o modelin turu boş geçer, koşu sürer.
- *Defter/veri hatası izole EDİLMEZ:* bozuk bir defter ya da bayat bir anlık görüntü
  turu tümden düşürür. Bunlar model hatası değil ölçüm hatasıdır; "yarısı yazılmış"
  bir turla devam etmek denetim izini sessizce bozardı (bkz. core/ledger.py).

`--dry-run` deftere yazmaz: defterin bir KOPYASI geçici dizine alınır, tur orada koşar ve
rapor oradan üretilir. Gerçek defteri okuyup yazmayı atlamak yetmezdi — motor turu
ilerletirken durumu yazar, yazmayan bir motor da metrikleri üretecek satırları hiç
oluşturmazdı. `docs/data/metrics.json` de dry-run'da yazılmaz: o dosya defterin türevidir,
defterle birlikte commit edilir; kalıcı olmayan bir turdan üretilmiş hâli ikisini ayrıştırır.
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import shutil
import sys
import tempfile
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

import pandas as pd

from core.config import PROJECT_ROOT, get_setting, load_config, project_path
from core.data import load_market_data
from core.engine import Engine, RoundReport
from core.ledger import LEDGER_DIRNAME, Ledger
from core.metrics import ModelMetrics, compare, format_report
from core.portfolio import Portfolio
from strategies.base import MarketData, Strategy
from strategies.registry import build

logger = logging.getLogger("main")

METRICS_PATH = Path("docs/data/metrics.json")


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, args.log_level.upper()),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )

    config = load_config(args.config)
    names = [str(name) for name in get_setting(config, "models")]
    if not names:
        logger.error("config.yaml'da models listesi boş: çalıştırılacak model yok")
        return 1

    strategies, build_failures = _build_strategies(names)
    if not strategies:
        logger.error("hiçbir model kurulamadı, tur çalıştırılmadı")
        return 1

    market = load_market_data(config)
    logger.info(
        "anlık görüntü hazır: as_of=%s, %d sembol, %d model",
        market.as_of, len(market.ohlcv), len(strategies),
    )

    with _ledger_for(args.dry_run) as ledger:
        report = Engine(
            strategies, config=config, ledger=ledger, portfolio=Portfolio(config)
        ).run_round(market)
        _log_round(report)

        metrics = compare(
            [strategy.name for strategy in strategies],
            ledger=ledger,
            config=config,
            benchmarks=[s.name for s in strategies if s.is_benchmark],
        )
        print(format_report(metrics))

        payload = _payload(
            config=config,
            market=market,
            report=report,
            metrics=metrics,
            strategies=strategies,
            build_failures=build_failures,
            dry_run=args.dry_run,
        )

    if args.dry_run:
        logger.info("--dry-run: defter ve %s yazılmadı", METRICS_PATH)
    else:
        _write_metrics(payload)

    # Kurulamayan model = eksik yarışma. Tur başarılı olsa bile koşu kırmızı dönmeli.
    return 1 if build_failures else 0


# --------------------------------------------------------------------------- #
# Model kurulumu
# --------------------------------------------------------------------------- #
def _build_strategies(names: Sequence[str]) -> tuple[list[Strategy], dict[str, str]]:
    """Modelleri sırayla kurar; biri patlarsa yalnızca o atlanır (gerekçesiyle)."""
    strategies: list[Strategy] = []
    failures: dict[str, str] = {}
    for name in names:
        try:
            strategies.append(build(name))
        except Exception as exc:
            logger.error("%s modeli kurulamadı, atlanıyor: %s", name, exc)
            failures[name] = str(exc)
    return strategies, failures


# --------------------------------------------------------------------------- #
# Defter (dry-run kopyası)
# --------------------------------------------------------------------------- #
class _LedgerScope:
    """`--dry-run` için defterin geçici kopyasını, aksi hâlde gerçek defteri verir."""

    def __init__(self, dry_run: bool) -> None:
        self._dry_run = dry_run
        self._tmp: tempfile.TemporaryDirectory[str] | None = None

    def __enter__(self) -> Ledger:
        if not self._dry_run:
            return Ledger()
        self._tmp = tempfile.TemporaryDirectory(prefix="paper-bot-dryrun-")
        source = project_path(LEDGER_DIRNAME)
        target = Path(self._tmp.name) / LEDGER_DIRNAME
        if source.is_dir():
            shutil.copytree(source, target)
        else:
            target.mkdir(parents=True)
        logger.info("--dry-run: defter kopyası %s", target)
        return Ledger(target)

    def __exit__(self, *exc_info: object) -> None:
        if self._tmp is not None:
            self._tmp.cleanup()


def _ledger_for(dry_run: bool) -> _LedgerScope:
    return _LedgerScope(dry_run)


# --------------------------------------------------------------------------- #
# Çıktı
# --------------------------------------------------------------------------- #
def _payload(
    *,
    config: dict[str, Any],
    market: MarketData,
    report: RoundReport,
    metrics: Sequence[ModelMetrics],
    strategies: Sequence[Strategy],
    build_failures: dict[str, str],
    dry_run: bool,
) -> dict[str, Any]:
    """docs/data/metrics.json içeriği.

    `as_of` ve koşu koşulları (maliyet sabitleri, görülen sembol sayısı) da yazılır:
    tablo tek başına "hangi varsayımlarla ölçüldü" sorusuna cevap veremez, oysa sonucun
    yorumu tam da ona bağlıdır.
    """
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "as_of": market.as_of.isoformat(),
        "dry_run": dry_run,
        "universe_size": len(market.ohlcv),
        "settings": {
            key: get_setting(config, key)
            for key in (
                "initial_capital", "risk_per_trade", "leverage_cap", "max_positions",
                "max_short_positions", "fee_rate", "slippage_base", "slippage_short_stop",
                "maintenance_margin", "max_stop_atr_multiple", "timeframe",
            )
        },
        "round": {
            "as_of": report.as_of.isoformat(),
            "models": [asdict(item) for item in report.models],
        },
        "build_failures": build_failures,
        "models": [
            {**_jsonable(asdict(item)), "name": item.model}
            for item in metrics
        ],
        "benchmarks": [strategy.name for strategy in strategies if strategy.is_benchmark],
    }


def _write_metrics(payload: dict[str, Any]) -> None:
    path = PROJECT_ROOT / METRICS_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    logger.info("metrikler yazıldı: %s", path)


def _jsonable(value: Any) -> Any:
    """nan/inf -> null. `json.dumps` bunları `NaN` yazar ve ortaya GEÇERSİZ JSON çıkar.

    nan burada "ölçülemedi" demektir (bkz. core/metrics.py) ve JSON'da onun karşılığı
    null'dır; 0.0'a çevirmek "ölçüldü, sıfır çıktı" ile karıştırırdı.
    """
    if isinstance(value, dict):
        return {key: _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    if is_dataclass(value) and not isinstance(value, type):
        return _jsonable(asdict(value))
    return value


def _log_round(report: RoundReport) -> None:
    logger.info("tur tamamlandı: as_of=%s", report.as_of)
    for model in report.models:
        logger.info(
            "  %-16s bar=%d dolum=%d kapanan=%d sinyal=%d çıkış=%d band-atlanan=%d%s",
            model.model, model.bars_processed, model.filled, model.closed,
            model.signals, model.exits, model.skipped_signals,
            f" ATLANDI: {model.skipped}" if model.skipped else "",
        )


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Bir paper-trading turu çalıştırır ve metrikleri üretir."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="deftere ve docs/data/metrics.json'a yazmadan turu çalıştırıp raporlar",
    )
    parser.add_argument("--config", default=None, help="config.yaml yolu (varsayılan: proje kökü)")
    parser.add_argument(
        "--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"]
    )
    return parser.parse_args(argv)


if __name__ == "__main__":
    sys.exit(main())

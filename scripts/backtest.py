#!/usr/bin/env python3
"""Backtest harness: CANLI MOTORU geçmiş bir pencerede koşturur.

Kurallar `docs/backtest.md`'dedir ve sonuç üretilmeden ÖNCE yazılmıştır; bu dosya onların
uygulamasıdır, yeni bir kural koymaz.

**Burada ikinci bir motor YOKTUR.** `core/engine.py` atlanan turları telafi ederken zaten
tam olarak bir backtest yapar: son işlenmiş bardan `as_of`'a kadar her barı SIRAYLA işler,
emirler kendi barının ertesinden dolar (kural 13), stop/TP/likidasyon her barın kendi
`high`/`low`'uyla kontrol edilir. Sadakat iddia değil, testle sabittir —
`tests/test_engine_per_bar.py::test_catch_up_matches_running_each_bar_in_its_own_round`
bir turda telafi edilen N barın, N ayrı turda koşulan N bar ile BİREBİR aynı defteri
ürettiğini gösterir.

Harness'ın yaptığı üç şey vardır ve hiçbiri çekirdeğe dokunmaz:

1. **Ayrı defter kökü** (`backtests/<koşu-id>/`). Gerçek defter hiçbir koşulda açılmaz —
   okunmaz da: bir backtest denetim izine (kural 1) yazmaz.
2. **`last_processed_bar` tohumlama.** Boş defterde `core/engine.py::_timeline` yalnızca
   son barı işler (`run.last_bar is None -> index[-1:]`), çünkü canlıda yeni açılan bir
   modelin geçmişi geriye dönük işlemesi yalnızca boş özsermaye satırı üretirdi. Backtest
   tam olarak o geçmişi istediği için başlangıç barını kendisi yazar.
3. **`signals_per_bar: true`.** Kapalıyken sinyal yalnızca `as_of` barında üretilir, yani
   tüm pencere tek bir sinyal verirdi. Base katmanı canlıda kapalı koşar; bu, bilinçli
   kabul edilmiş bir sapmadır (docs/backtest.md > 5a) ve koşu çıktısında AÇIKÇA yazılır.

Model kurulumu `main.py::build_strategies`ten gelir, burada yeniden yazılmaz: kurulum iki
yerde ayrışırsa backtest, canlıda koşandan başka bir model kümesini ölçmeye başlar.

Maliyet, dolum, likidasyon, funding ve metrik tanımlarının hiçbiri burada YOKTUR; hepsi
katmanın çözülmüş config'inden ve `core/`den gelir. Backtest'e özel bir sabit eklemek,
backtest'in kendi uydurduğu bir dünyayı ölçmesi demek olurdu.

## Kapı 0 (`--verify-live`)

Hiçbir backtest sayısı, harness canlı veriye karşı doğrulanmadan okunmaz. Yer gerçeği
canlı turların `round.models[].emitted` kaydıdır: her barda her modelin tam olarak hangi
sinyali ürettiği. Harness aynı pencerede koşturulur ve sinyaller karşılaştırılır.

- **Uyarlanabilir OLMAYAN modeller birebir eşleşmeli.** Sinyalleri piyasa verisinin ve
  sabit tohumun saf fonksiyonudur: `ScalpModel._round_rng` her barı `random_seed`, `as_of`
  ve model kimliğiyle yeniden tohumlar, yani çekiliş durum TAŞIMAZ; `vwap_managed`de
  rastgelelik hiç yoktur.
- **Uyarlanabilir modellerden eşleşme BEKLENMEZ** (`scalp_bandit`, `vwap_clone`): ikisi de
  kendi kapanmış işlemlerinden öğrenir (kural 16) ve boş defterden başlayan bir koşu farklı
  bir geçmiş görür. Rapor edilir, kapı sayılmaz.

Karşılaştırma penceresi config'in DEĞİŞMEDİĞİ bir aralık olmalıdır: `fee_rate` (karar 25)
ve `vwap.managed.atr_multiple` (karar 26) 15 Eylül'de değişti, öncesi ile sonrası aynı
kurallarla koşmadı.
"""

from __future__ import annotations

import argparse
import json
import logging
import subprocess
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd  # noqa: E402

from core.config import load_config  # noqa: E402
from core.data import load_market_data  # noqa: E402
from core.engine import Engine, RoundReport  # noqa: E402
from core.layers import DEFAULT_LAYER, Layer, resolve_layer  # noqa: E402
from core.ledger import Ledger  # noqa: E402
from core.metrics import ModelMetrics, compare, format_report  # noqa: E402
from core.portfolio import Portfolio  # noqa: E402
from main import build_strategies  # noqa: E402
from strategies.base import Strategy  # noqa: E402

logger = logging.getLogger("backtest")

BACKTEST_ROOT = Path("backtests")
MANIFEST_FILENAME = "manifest.json"

# Config parmak izine giren anahtarlar: koşunun hangi kurallarla yapıldığını sonradan
# okuyabilmek için (docs/backtest.md > 9). Ölçümün anlamını belirleyen her sabit burada
# olmalı — eksik bir anahtar, iki backtest'in neden ayrıştığını açıklanamaz kılar.
_FINGERPRINT_KEYS: tuple[str, ...] = (
    "initial_capital", "risk_per_trade", "leverage_cap", "max_positions",
    "max_short_positions", "fee_rate", "slippage_base", "slippage_short_stop",
    "maintenance_margin", "max_stop_atr_multiple", "timeframe", "random_seed",
    "signals_per_bar",
)

# Kendi kapanmış işlemlerinden öğrenen modeller (kural 16). Kapı 0'da bunlardan birebir
# eşleşme BEKLENMEZ: boş defterden başlayan bir koşu farklı bir geçmiş görür. Liste
# `Strategy.observe_closed_trades`ın uygulanıp uygulanmadığından TÜRETİLİR, elle
# yazılmaz — elle yazılan bir liste, yeni bir uyarlanabilir model eklendiği gün sessizce
# yanlış olurdu ve Kapı 0 o modelden haksız yere eşleşme beklerdi.
def is_adaptive(strategy: Strategy) -> bool:
    return type(strategy).observe_closed_trades is not Strategy.observe_closed_trades


@dataclass(frozen=True, kw_only=True)
class BacktestResult:
    layer: str
    start: pd.Timestamp
    end: pd.Timestamp
    out_dir: Path
    report: RoundReport
    metrics: tuple[ModelMetrics, ...]
    build_failures: Mapping[str, str]


# --------------------------------------------------------------------------- #
# Koşu
# --------------------------------------------------------------------------- #
def run_backtest(
    *,
    layer_name: str,
    start: pd.Timestamp,
    end: pd.Timestamp,
    out_dir: Path,
    models: Sequence[str] | None = None,
    config_path: str | None = None,
) -> BacktestResult:
    """Katmanı `start`..`end` penceresinde koşturur ve ayrı bir deftere yazar.

    `start` TOHUMLANAN bardır: motor ondan SONRAKİ ilk bardan başlar (`_timeline`
    `ts > last_bar` süzer). Yani pencere yarı açıktır — `start` işlenmez, `end` işlenir.
    """
    if end <= start:
        raise ValueError(f"pencere boş: start={start} >= end={end}")

    layer = resolve_layer(load_config(config_path), layer_name)
    config = dict(layer.config)
    # Bilinçli sapma (docs/backtest.md > 5a): kapalıyken tüm pencere TEK sinyal üretirdi.
    signals_per_bar_was = bool(config.get("signals_per_bar"))
    config["signals_per_bar"] = True

    names = list(models) if models is not None else layer.models
    if not names:
        raise ValueError(f"{layer.name} katmanında model yok")

    strategies, build_failures = build_strategies(names, config)
    if not strategies:
        raise RuntimeError("hiçbir model kurulamadı")

    out_dir.mkdir(parents=True, exist_ok=True)
    ledger = Ledger(out_dir / "ledger")
    initial_capital = float(config["initial_capital"])
    for strategy in strategies:
        ledger.initialize_model(strategy.name, initial_capital=initial_capital)
        _seed_start_bar(ledger, strategy.name, start=start)

    # Anlık görüntü `end`e kadar kesilir: `now` verildiğinde core/data.py hem çıpayı hem
    # tüm serileri oraya kadar budar, yani model geleceği GÖREMEZ (kural 12). Backtest'in
    # look-ahead güvencesi burada başlar ve motorun bar bazlı dilimlemesiyle sürer.
    market = load_market_data(config, symbols=layer.symbols, now=end)
    logger.info(
        "katman=%s pencere=(%s, %s] as_of=%s sembol=%d model=%d",
        layer.name, start, end, market.as_of, len(market.ohlcv), len(strategies),
    )
    if market.as_of < end:
        logger.warning(
            "anlık görüntünün son barı (%s) istenen bitişten (%s) geride: pencere kısaldı",
            market.as_of, end,
        )

    report = Engine(
        strategies, config=config, ledger=ledger, portfolio=Portfolio(config)
    ).run_round(market)

    metrics = compare(
        [strategy.name for strategy in strategies],
        ledger=ledger,
        config=config,
        benchmarks=[s.name for s in strategies if s.is_benchmark],
        replicas=[s.name for s in strategies if s.is_replica],
    )

    _write_manifest(
        out_dir,
        layer=layer,
        config=config,
        start=start,
        end=market.as_of,
        report=report,
        strategies=strategies,
        build_failures=build_failures,
        signals_per_bar_was=signals_per_bar_was,
    )
    return BacktestResult(
        layer=layer.name, start=start, end=market.as_of, out_dir=out_dir,
        report=report, metrics=tuple(metrics), build_failures=build_failures,
    )


def _seed_start_bar(ledger: Ledger, model: str, *, start: pd.Timestamp) -> None:
    """`last_processed_bar`ı pencere başına yazar.

    Bu olmadan motor yalnızca son barı işler: boş defterli bir model için `_timeline`
    bilinçli olarak `index[-1:]` döner (canlıda geçmişi geriye dönük işlemek yalnızca boş
    özsermaye satırı üretirdi). Backtest tam olarak o geçmişi istediği için barı KENDİSİ
    yazar — motorun kuralını değiştirmeden, ona canlıdakiyle aynı girdiyi vererek.
    """
    state = ledger.load_state(model)
    if state is None:
        raise RuntimeError(f"{model}: defter durumu kurulamadı")
    state["last_processed_bar"] = start.isoformat()
    ledger.write_state(model, state)


# --------------------------------------------------------------------------- #
# Denetim izi: koşunun kendisi tekrar üretilebilir olmalı (docs/backtest.md > 9)
# --------------------------------------------------------------------------- #
def _write_manifest(
    out_dir: Path,
    *,
    layer: Layer,
    config: Mapping[str, Any],
    start: pd.Timestamp,
    end: pd.Timestamp,
    report: RoundReport,
    strategies: Sequence[Strategy],
    build_failures: Mapping[str, str],
    signals_per_bar_was: bool,
) -> None:
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "harness_sha": _git_sha(),
        "layer": layer.name,
        "window": {"start": start.isoformat(), "end": end.isoformat()},
        "models": [s.name for s in strategies],
        "adaptive_models": [s.name for s in strategies if is_adaptive(s)],
        "build_failures": dict(build_failures),
        "config_fingerprint": {key: config.get(key) for key in _FINGERPRINT_KEYS},
        # Sapma gizlenmez: base katmanı canlıda signals_per_bar=false koşar ve backtest
        # onu açar, yani canlıdan ÇOK işlem yapar (docs/backtest.md > 5a).
        "signals_per_bar_forced": not signals_per_bar_was,
        # Geçerlilik kapısı B-2: ikisi de "o barda stop/TP/likidasyon hiç sorulmadı" demek.
        "validity": {
            model.model: {
                "bars_processed": model.bars_processed,
                "missing_bars": model.missing_bars,
                "unchecked_position_bars": model.unchecked_position_bars,
                "signals": model.signals,
                "filled": model.filled,
            }
            for model in report.models
        },
    }
    (out_dir / MANIFEST_FILENAME).write_text(
        json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _git_sha() -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True, timeout=10
        )
    except Exception:  # noqa: BLE001 — SHA bir kolaylıktır, koşuyu düşüremez
        return "unknown"
    return result.stdout.strip() if result.returncode == 0 else "unknown"


def check_validity(report: RoundReport) -> dict[str, list[str]]:
    """Geçerlilik kapıları B-2 (docs/backtest.md > 3): model -> ihlal listesi.

    B-1 (n >= 30) burada DEĞİL: örneklem kapısı metriklerden okunur ve `acceptance_flags`
    zaten onu canlıyla aynı eşikle uygular. Burada yalnızca backtest'e özgü olan, yani
    pencerenin kendisinin sağlam olup olmadığı sorulur.
    """
    violations: dict[str, list[str]] = {}
    for model in report.models:
        reasons = []
        if model.missing_bars:
            reasons.append(f"missing_bars={model.missing_bars}")
        if model.unchecked_position_bars:
            reasons.append(f"unchecked_position_bars={model.unchecked_position_bars}")
        if reasons:
            violations[model.model] = reasons
    return violations


# --------------------------------------------------------------------------- #
# Kapı 0: harness canlı veriye karşı doğrulanır
# --------------------------------------------------------------------------- #
@dataclass(frozen=True, kw_only=True)
class SignalKey:
    """Karşılaştırmanın birimi: hangi model, hangi barda, hangi sembolde, hangi yönde.

    Fiyat alanları (stop/hedef) KASTEN dışarıda: kayan nokta eşitliği kırılgandır ve
    sorulan soru "aynı kurulumu buldu mu", "ondalık basamağına kadar aynı mı" değil.
    Fiyat farkı varsa zaten sembol/yön/bar üçlüsü tutmazdı.
    """
    model: str
    bar: str
    symbol: str
    direction: str


def emitted_keys(report: RoundReport) -> set[SignalKey]:
    return {
        SignalKey(
            model=model.model,
            bar=str(signal.bar),
            symbol=str(signal.symbol),
            direction=str(signal.direction),
        )
        for model in report.models
        for signal in model.emitted
    }


def live_emitted_keys(
    metrics_path: str, *, start: pd.Timestamp, end: pd.Timestamp
) -> set[SignalKey]:
    """Canlı turların `emitted` kayıtları, git geçmişinden toplanır.

    Neden git: `docs/data/metrics_*.json` her turda ÜZERİNE yazılır, yani dosyanın son hâli
    yalnızca son turu taşır. Canlının bar bar ne ürettiği ancak commit geçmişinde durur —
    ve orası zaten değiştirilemez bir kayıttır, tam da bir yer gerçeğinden istenen şey.
    """
    keys: set[SignalKey] = set()
    shas = _git_log_shas(metrics_path)
    for sha in shas:
        blob = _git_show(f"{sha}:{metrics_path}")
        if not blob:
            continue
        try:
            payload = json.loads(blob)
        except json.JSONDecodeError:
            continue
        for model in (payload.get("round") or {}).get("models") or ():
            for signal in model.get("emitted") or ():
                bar = _stamp(signal.get("bar"))
                if bar is None or not (start < bar <= end):
                    continue
                keys.add(
                    SignalKey(
                        model=str(signal.get("model") or model.get("model")),
                        bar=bar.isoformat(),
                        symbol=str(signal.get("symbol")),
                        direction=str(signal.get("direction")),
                    )
                )
    return keys


def compare_signals(
    backtest: set[SignalKey], live: set[SignalKey], *, adaptive: Iterable[str]
) -> dict[str, dict[str, Any]]:
    """Model bazında eşleşme. Uyarlanabilir modeller `gate=False` ile işaretlenir."""
    adaptive_names = set(adaptive)
    models = {key.model for key in backtest} | {key.model for key in live}
    result: dict[str, dict[str, Any]] = {}
    for name in sorted(models):
        mine = {key for key in backtest if key.model == name}
        theirs = {key for key in live if key.model == name}
        result[name] = {
            "gate": name not in adaptive_names,
            "backtest": len(mine),
            "live": len(theirs),
            "both": len(mine & theirs),
            "only_backtest": sorted(f"{k.bar} {k.symbol} {k.direction}" for k in mine - theirs),
            "only_live": sorted(f"{k.bar} {k.symbol} {k.direction}" for k in theirs - mine),
            "match": mine == theirs,
        }
    return result


def _git_log_shas(path: str) -> list[str]:
    result = subprocess.run(
        ["git", "log", "--format=%H", "--", path], capture_output=True, text=True
    )
    return result.stdout.split() if result.returncode == 0 else []


def _git_show(spec: str) -> str:
    result = subprocess.run(["git", "show", spec], capture_output=True, text=True)
    return result.stdout if result.returncode == 0 else ""


def _stamp(value: Any) -> pd.Timestamp | None:
    if not value:
        return None
    try:
        stamp = pd.Timestamp(str(value))
    except (ValueError, TypeError):
        return None
    if pd.isna(stamp):
        return None
    return stamp.tz_convert("UTC") if stamp.tzinfo is not None else stamp.tz_localize("UTC")


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, args.log_level.upper()),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )

    start, end = _stamp(args.start), _stamp(args.end)
    if start is None or end is None:
        logger.error("--start ve --end ISO zaman damgası olmalı")
        return 2

    run_id = args.run_id or f"{args.layer}-{start:%Y%m%dT%H%M}-{end:%Y%m%dT%H%M}"
    out_dir = Path(args.out) if args.out else BACKTEST_ROOT / run_id

    try:
        result = run_backtest(
            layer_name=args.layer, start=start, end=end, out_dir=out_dir,
            models=args.models.split(",") if args.models else None,
            config_path=args.config,
        )
    except Exception as exc:  # noqa: BLE001 — CLI sınırı; gerekçe kullanıcıya gider
        logger.error("backtest koşulamadı: %s", exc)
        return 1

    print(format_report(list(result.metrics)))

    violations = check_validity(result.report)
    if violations:
        # Kapı B-2 düştü: pencere eksik bir geçmişin üstüne yazılmış demektir. Sayılar
        # yine de basılır (gizlemek daha kötü olurdu) ama koşu KIRMIZI döner.
        logger.error("GEÇERLİLİK KAPISI DÜŞTÜ (docs/backtest.md > 3):")
        for model, reasons in sorted(violations.items()):
            logger.error("  %-16s %s", model, ", ".join(reasons))

    if args.verify_live:
        ok = _report_gate_zero(result, metrics_path=args.verify_live, start=start, end=end)
        if not ok:
            return 1

    logger.info("çıktı: %s", out_dir)
    return 1 if violations or result.build_failures else 0


def _report_gate_zero(
    result: BacktestResult, *, metrics_path: str, start: pd.Timestamp, end: pd.Timestamp
) -> bool:
    manifest = json.loads((result.out_dir / MANIFEST_FILENAME).read_text(encoding="utf-8"))
    adaptive = manifest["adaptive_models"]
    live = live_emitted_keys(metrics_path, start=start, end=end)
    if not live:
        logger.error("KAPI 0: %s geçmişinde bu pencereye ait canlı kayıt yok", metrics_path)
        return False

    table = compare_signals(emitted_keys(result.report), live, adaptive=adaptive)
    logger.info("KAPI 0 — harness canlı veriye karşı (docs/backtest.md > 1)")
    failed = []
    for name, row in table.items():
        mark = "KAPI" if row["gate"] else "bilgi"
        status = "EŞLEŞTİ" if row["match"] else "AYRIŞTI"
        logger.info(
            "  %-16s [%-5s] backtest=%d canlı=%d ortak=%d -> %s",
            name, mark, row["backtest"], row["live"], row["both"], status,
        )
        for line in row["only_backtest"][:5]:
            logger.info("      yalnız backtest: %s", line)
        for line in row["only_live"][:5]:
            logger.info("      yalnız canlı   : %s", line)
        if row["gate"] and not row["match"]:
            failed.append(name)

    if failed:
        logger.error(
            "KAPI 0 DÜŞTÜ: %s birebir eşleşmedi. docs/backtest.md > 1 gereği "
            "hiçbir backtest sonucu yorumlanmaz.", ", ".join(failed),
        )
        return False
    logger.info("KAPI 0 GEÇTİ: uyarlanabilir olmayan modellerin sinyalleri birebir eşleşti")
    return True


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Canlı motoru geçmiş bir pencerede koşturur (docs/backtest.md)."
    )
    parser.add_argument("--layer", default=DEFAULT_LAYER)
    parser.add_argument("--start", required=True, help="pencere başı (ISO); bu bar İŞLENMEZ")
    parser.add_argument("--end", required=True, help="pencere sonu (ISO); bu bar işlenir")
    parser.add_argument("--models", default=None, help="virgülle; boş = katmanın listesi")
    parser.add_argument("--out", default=None, help="çıktı dizini")
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--config", default=None)
    parser.add_argument(
        "--verify-live", default=None, metavar="METRICS_PATH",
        help="KAPI 0: sinyalleri bu rapor dosyasının git geçmişiyle karşılaştır",
    )
    parser.add_argument(
        "--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"]
    )
    return parser.parse_args(argv)


if __name__ == "__main__":
    sys.exit(main())

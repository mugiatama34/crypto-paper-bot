#!/usr/bin/env python3
"""SÜREKLİ KOŞU: turu cron yerine uzun ömürlü bir süreç tetikler.

**Neden var (karar 45, madde A5).** Ölçümün tetikleyicisi GitHub Actions cron'uydu ve
cron'un kendisi ölçüldü: 15 dakikalık tetiklemelerin ~%91'i düşüyordu ve 4 saatlik
katmanda barların %26'sı sinyalsiz geçiyordu — üstelik kaybolan bar hep aynı saatlerdeydi,
yani kayıp gürültü değil YANLILIKTI (karar 39). Telafi yolu (`signals_per_bar`) kaybı
ölçüm tarafında kapatır; bu süreç ise SEBEBİNİ kapatır: bar kapandıktan saniyeler sonra
turu kendisi başlatır ve düşecek bir tetikleyici kalmaz.

**İkinci bir motor DEĞİLDİR** (`scripts/backtest.py` ile aynı söz). Burada tek satır iş
mantığı yoktur: süreç yalnızca ZAMANLAR ve `main.py`yi çağırır. Turu içeriden koşmak
(import edip `main()` çağırmak) cazipti ama reddedildi — uzun ömürlü bir süreçte
`pandas`/`requests` durumu turlar arasında taşınır ve bir turun sızıntısı bir sonrakinin
ölçümüne karışabilirdi. Ayrı süreç, her turun GitHub runner'ındaki kadar temiz
başlamasını garanti eder.

**Neden websocket değil.** İstenen "sürekli süreç + websocket"ti; burada birincisi var,
ikincisi bilinçli olarak yok ve gerekçesi kural 12'dir: `MarketData` yalnızca KAPANMIŞ
barları içerir ve kapanmamış bardan gelen hiçbir tick ölçüme giremez. Websocket'in bu
sistemde yapabileceği tek iş "bar kapandı" saatini saniyeler önce duyurmaktır; onu bu
süreç zaten takvimden bilir (`bar_duration`) ve dolum bir SONRAKİ barın açılışındadır
(kural 13), yani birkaç saniyelik fark hiçbir dolum fiyatını değiştirmez. Karşılığında
websocket yeni bir bağımlılık, yeniden bağlanma durumu ve ikinci bir veri yolu (dolayısıyla
iki modelin aynı barda farklı veri görme riski, kural 5) getirirdi.

**Turlar asla üst üste binmez:** döngü tek iş parçacıklıdır ve bir tur bitmeden bir
sonraki başlamaz. Gecikmiş bir tur bir sonraki barı kaçırırsa motor onu zaten TELAFİ eder
(scalp katmanında `signals_per_bar: true`, yani telafi barları kendi sinyallerini de
üretir) — kayıp bar sessiz kalmaz, `missing_bars` olarak sayılır.

Kurulum ve systemd birimi: `docs/live_runner.md`.
"""

from __future__ import annotations

import argparse
import logging
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd  # noqa: E402

from core.config import load_config  # noqa: E402
from core.data import bar_duration  # noqa: E402
from core.layers import DEFAULT_LAYER, Layer, resolve_layer  # noqa: E402

logger = logging.getLogger("live_loop")

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Bar kapandıktan sonra beklenen süre. Borsa son mumu yayımlamadan tur koşarsa
# `core/data.py` o barı görmez ve tur "yeni bar yok" diye geçer; bir sonraki tura kadar
# da sinyal üretilmez. Bekleme, o boşluğu kapatan tek şeydir ve ölçüme dokunmaz —
# `as_of` her hâlükârda son KAPANMIŞ barın zamanıdır (kural 12).
DEFAULT_SETTLE_SECONDS = 20.0


@dataclass(frozen=True, kw_only=True)
class Step:
    """Turun bir adımı: ad, komut ve başarısızlığın turu düşürüp düşürmediği.

    `critical=False` olan adım (bildirim) hata verse de döngü devam eder — workflow'daki
    `continue-on-error`ın karşılığıdır ve aynı gerekçeye dayanır: bildirim katmanı
    ölçümü düşüremez.
    """

    name: str
    command: Sequence[str]
    critical: bool


class Stopped(Exception):
    """SIGINT/SIGTERM alındı: içinde bulunulan tur bitince çıkılır."""


def next_wakeup(
    now: datetime, *, duration: timedelta, settle: float
) -> datetime:
    """Bir SONRAKİ bar kapanışı + yerleşme payı.

    `now` tam olarak bir bar sınırındaysa bile BİR SONRAKİ sınır seçilir: o barın
    kapanışı henüz yayımlanmamıştır ve aynı anda koşmak "yeni bar yok" turu üretirdi.
    Sınır her zaman epoch'a göre hizalanır (00:00, 00:15, ...) çünkü borsa barları da öyle
    hizalıdır; sürecin başlatıldığı saniyeye göre hizalamak, her yeniden başlatmada
    turların kayması demekti.
    """
    if duration <= timedelta(0):
        raise ValueError(f"bar süresi pozitif olmalı: {duration}")
    epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)
    elapsed = now.astimezone(timezone.utc) - epoch
    boundary = epoch + duration * (int(elapsed / duration) + 1)
    return boundary + timedelta(seconds=settle)


def build_steps(layer: Layer, *, python: str, notify: bool, log_level: str) -> list[Step]:
    """Turun adımları: ölçüm önce, bildirim sonra.

    Sıra workflow'la BİREBİR aynıdır ve bu bir tercih değil bir kuraldır: bildirim, turun
    kaydedilmesinden sonra gelir ki ağ erişimi olan bir adım ölçümün önüne geçmesin.
    """
    steps = [
        Step(
            name="tur",
            command=[python, str(PROJECT_ROOT / "main.py"), "--layer", layer.name,
                     "--log-level", log_level],
            critical=True,
        )
    ]
    if not notify:
        return steps
    script = "telegram_signals.py" if layer.name == "scalp" else "telegram_report.py"
    steps.append(
        Step(
            name="bildirim",
            command=[python, str(PROJECT_ROOT / "scripts" / script), "--log-level", log_level],
            critical=False,
        )
    )
    return steps


def run_round(steps: Sequence[Step], *, timeout: float) -> bool:
    """Adımları sırayla koşar; kritik bir adım düşerse False döner.

    Çıktı bastırılmaz: sürecin logu systemd/journald'a düşer ve turun ne yaptığı orada
    okunur — bir ölçüm sisteminin denetim izi yalnızca defterde değil, koşu logunda da
    durmalıdır.
    """
    for step in steps:
        logger.info("adım başlıyor: %s", step.name)
        try:
            completed = subprocess.run(step.command, cwd=PROJECT_ROOT, timeout=timeout)
        except subprocess.TimeoutExpired:
            logger.error("adım zaman aşımına uğradı (%.0fs): %s", timeout, step.name)
            if step.critical:
                return False
            continue
        if completed.returncode != 0:
            logger.error("adım hata kodu döndürdü (%d): %s", completed.returncode, step.name)
            if step.critical:
                return False
        else:
            logger.info("adım bitti: %s", step.name)
    return True


def commit_round(layer: Layer, *, push: bool) -> None:
    """Turun çıktısını commit eder (ve istenirse push).

    Kapsam KATMANIN kendi dosyalarıdır: `git add` yalnızca o katmanın defterini, rapor
    dosyasını ve bildirim durumunu alır. Geniş bir `git add -A`, bir katmanın turunun
    diğerinin defterini commit'lemesi demekti — iki ölçümün birbirine karışmasının en
    sessiz yolu.

    Push çakışması beklenen bir durumdur (defter append-only, rebase güvenli) ve
    workflow'daki gibi birkaç kez denenir. Başarısızlık turu DÜŞÜRMEZ: tur çoktan diske
    yazılmıştır ve bir sonraki commit eksiği kapatır.
    """
    paths = [str(layer.ledger_root), str(layer.metrics_path), "state"]
    _git(["add", *[path for path in paths if (PROJECT_ROOT / path).exists()]])
    if _git(["diff", "--cached", "--quiet"], check=False).returncode == 0:
        logger.info("değişiklik yok, commit atlanıyor")
        return
    _git(["commit", "-m", f"{layer.name}: {_as_of(layer)}"])
    if not push:
        return
    for attempt in range(1, 5):
        if _git(["push"], check=False).returncode == 0:
            return
        logger.warning("push başarısız (deneme %d), rebase edilip tekrar denenecek", attempt)
        _git(["pull", "--rebase"], check=False)
        time.sleep(2**attempt)
    logger.error("push 4 denemede başarısız oldu; bir sonraki tur tekrar deneyecek")


def _as_of(layer: Layer) -> str:
    """Rapor dosyasındaki `as_of` — commit mesajı turun barını taşısın diye."""
    import json

    try:
        return str(json.loads(Path(layer.metrics_path).read_text(encoding="utf-8"))["as_of"])
    except (OSError, ValueError, KeyError):
        return pd.Timestamp.utcnow().isoformat()


def _git(args: Sequence[str], *, check: bool = True) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(["git", *args], cwd=PROJECT_ROOT, check=check)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, args.log_level.upper()),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )
    layer = resolve_layer(load_config(args.config), args.layer)
    duration = bar_duration(layer.timeframe).to_pytimedelta()
    steps = build_steps(
        layer, python=args.python, notify=not args.no_notify, log_level=args.log_level
    )
    logger.info(
        "sürekli koşu başladı: katman=%s bar=%s yerleşme=%.0fs adımlar=%s",
        layer.name, layer.timeframe, args.settle_seconds,
        ", ".join(step.name for step in steps),
    )

    stop = _install_signal_handlers()
    rounds = 0
    while True:
        if not args.now or rounds:
            wakeup = next_wakeup(
                datetime.now(timezone.utc), duration=duration, settle=args.settle_seconds
            )
            logger.info("bir sonraki tur %s (UTC)", wakeup.isoformat())
            if not _sleep_until(wakeup, stop):
                break
        ok = run_round(steps, timeout=args.timeout)
        if ok and not args.no_commit:
            commit_round(layer, push=not args.no_push)
        rounds += 1
        if args.max_rounds and rounds >= args.max_rounds:
            logger.info("--max-rounds (%d) doldu, çıkılıyor", args.max_rounds)
            break
        if stop["requested"]:
            logger.info("durdurma isteği alındı, tur bitti, çıkılıyor")
            break
    return 0


def _install_signal_handlers() -> dict[str, bool]:
    """SIGINT/SIGTERM: bayrağı kaldırır, turu YARIDA KESMEZ.

    Yarıda kesilen bir tur, defterin yarısı yazılmış hâlde kalabilirdi; `core/ledger.py`
    satırları durumdan önce yazar (bkz. `core/engine.py::_persist`) ama yine de sürecin
    kendi eliyle o pencereyi açması yanlış olurdu. Bekleme UYKUSU kesilir, tur kesilmez.
    """
    state = {"requested": False}

    def handler(signum: int, _frame: object) -> None:
        logger.info("sinyal alındı (%d): içinde bulunulan tur bitince çıkılacak", signum)
        state["requested"] = True

    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, handler)
    return state


def _sleep_until(target: datetime, stop: dict[str, bool]) -> bool:
    """Hedefe kadar uyur; durdurma isteği gelirse False döner.

    Tek bir uzun `sleep` yerine kısa dilimler: 15 dakikalık bir uykunun ortasında gelen
    SIGTERM'i beklemek, systemd'nin süreci öldürmesi demekti.
    """
    while True:
        remaining = (target - datetime.now(timezone.utc)).total_seconds()
        if stop["requested"]:
            return False
        if remaining <= 0:
            return True
        time.sleep(min(remaining, 1.0))


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Turu bar kapanışlarında tetikleyen uzun ömürlü süreç (cron yerine)."
    )
    parser.add_argument("--layer", default=DEFAULT_LAYER)
    parser.add_argument("--config", default=None)
    parser.add_argument(
        "--settle-seconds", type=float, default=DEFAULT_SETTLE_SECONDS,
        help="bar kapanışından sonra beklenecek süre (varsayılan %(default)s)",
    )
    parser.add_argument(
        "--timeout", type=float, default=900.0, help="tek bir adımın azami süresi (sn)"
    )
    parser.add_argument(
        "--now", action="store_true", help="ilk turu bar beklemeden hemen koş"
    )
    parser.add_argument("--max-rounds", type=int, default=0, help="0 = sınırsız")
    parser.add_argument("--no-commit", action="store_true", help="turu commit etme")
    parser.add_argument("--no-push", action="store_true", help="commit et ama push etme")
    parser.add_argument("--no-notify", action="store_true", help="bildirim adımını atla")
    parser.add_argument("--python", default=sys.executable)
    parser.add_argument(
        "--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"]
    )
    return parser.parse_args(argv)


if __name__ == "__main__":
    sys.exit(main())

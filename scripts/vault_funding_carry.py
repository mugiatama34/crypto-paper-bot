#!/usr/bin/env python3
"""Fonlama taşıması — KASA SINAMASI (docs/backtest.md > 6w). Dondurulmuş kuralın kasadaki İLK ÖLÇÜMÜ.

Kural `scripts/measure_funding_carry.py`dir (@ `fbfc2cd1`: ön-kayıt `bfd46b05`, TADİLAT-1
`b12c957e`, TADİLAT-2) ve burada İKİNCİ KEZ YAZILMAZ: bu betik yalnızca kasa penceresinin
TARİHLERİNİ (`Window`) kurar ve dondurulmuş fonksiyonları çağırır. Geliştirme penceresinde aynı
kod yolunun §6t sayılarını birebir ürettiği CI'da sınanır (`tests/test_vault_funding_carry.py`).

ÜÇ AŞAMA (§6w > 8):
- `universe` — yalnızca kasa ÖNCESİ veri: enstrüman listesi, 2026-08-28 → 09-27 perp cirosu,
  2026-09-20 → 09-27 1H mumlar. Birincil evren burada donar (`docs/data/pins/funding_carry_vault/`).
- `count` — zamanlanmış, haftalık, YALNIZCA SAYIM. 2026-12-28'den (13. hafta) 2027-09-27'ye
  (tavan, 52. hafta) kadar her Pazartesi 00:00Z kontrol noktasını sırayla değerlendirir ve yalnızca
  tam hafta, Σ pozisyon-gün, giriş sayısı, ölçülen pay ve kapı sonucunu yazar. Getiri, fonlama
  tutarı, baz, maliyet, sinyal değeri ya da sembol bazında hiçbir sayı YAZMAZ (test).
- `measure` — ilk geçen kontrol noktasında TEK sefer; pencere sonu o noktadır, başka bir girdi
  almaz. Ölçümün pozisyon-gün ve giriş sayısı `count`unkiyle birebir olmalıdır.

Kasa yalnızca `scripts/vault.py::OPENINGS`teki `funding_carry_vault` kaydıyla açılır.
Çıkış kodları (karar 51): 0 = yazıldı / bekleniyor; 3 = veri kapısı; 2 = kullanım ya da tutarlılık hatası.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import logging
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any, Mapping, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from core.config import get_setting, load_config  # noqa: E402
from scripts import measure_funding_carry as mfc  # noqa: E402
from scripts.vault import KASA_START  # noqa: E402

logger = logging.getLogger("vault_funding_carry")

# --- Ön-kayıtlı tarihler ve kapılar (§6w > 3–6). Hiçbiri CLI girdisi DEĞİLDİR. ------------------
OPENING = "funding_carry_vault"
VOLUME_START = pd.Timestamp("2026-08-28T00:00:00Z")      # §6w > 3: 30 UTC günü, kasa öncesi
VOLUME_END = KASA_START
U2_START = pd.Timestamp("2026-09-20T00:00:00Z")          # §6w > 3: kimlik kapısı, kasa öncesi
MEASURE_START = pd.Timestamp("2026-09-28T00:00:00Z")     # §6w > 3, V7
DATA_START = MEASURE_START - mfc.SIGNAL_DAYS * mfc.DAY  # sinyal ısınması (girdi)
FIRST_CHECKPOINT = pd.Timestamp("2026-12-28T00:00:00Z")  # 13 tam hafta
CAP = pd.Timestamp("2027-09-27T00:00:00Z")               # V1: 52 tam hafta
WEEK = pd.Timedelta(days=7)
MIN_WEEKS = 13
MIN_MEASURED = 17                                        # V3: ölçülen pay ≥ 17/20
ARCHIVE_STALENESS = mfc.DAY                              # veri kapısı: arşiv kontrol noktasına bu kadar yakın olmalı
PINS_DIR = Path("docs/data/pins/funding_carry_vault")
OUT_NAME = "funding_carry_vault"
ARCHIVE_LIST = "archive_symbols.json"
COUNT_KEYS = ("checkpoint", "weeks", "measured", "position_days", "entries", "status")

UNIVERSE_WINDOW = mfc.Window(data_start=DATA_START, measure_start=MEASURE_START, end=KASA_START,
                             snapshot_start=U2_START, volume_start=VOLUME_START, volume_end=VOLUME_END,
                             fetch_now=KASA_START)


def window_at(end: pd.Timestamp) -> mfc.Window:
    return mfc.Window(data_start=DATA_START, measure_start=MEASURE_START, end=end, snapshot_start=U2_START,
                      volume_start=VOLUME_START, volume_end=VOLUME_END, fetch_now=end, opening=OPENING)


def checkpoints_due(now: pd.Timestamp) -> list[pd.Timestamp]:
    """Kapanmış kontrol noktaları: 2026-12-28'den haftalık, `now`a ve tavana kadar."""
    last = min(mfc._utc(now), CAP)
    return list(pd.date_range(FIRST_CHECKPOINT, last, freq=WEEK)) if last >= FIRST_CHECKPOINT else []


def weeks_between(end: pd.Timestamp) -> int:
    return int((end - MEASURE_START) // WEEK)


def measured_at(primary: Sequence[str], funding: Mapping[str, mfc.FundingSeries], end: pd.Timestamp) -> list[str]:
    """§6w > 5.4: ısınma başlangıcından kontrol noktasına kadar arşivde fonlama kaydı olan birincil sembol."""
    lo, hi = DATA_START.value, end.value
    return [s for s in primary if s in funding and np.any((funding[s].times >= lo) & (funding[s].times < hi))]


def gate(row: Mapping[str, Any]) -> str:
    """Kontrol noktasının kapı sonucu (§6w > 5): okunabilir / değerlendirilemez / ölçülemez."""
    if row["measured"] < MIN_MEASURED:
        return "ölçülemez"
    if row["weeks"] >= MIN_WEEKS and row["position_days"] >= mfc.MIN_POSITION_DAYS and row["entries"] >= mfc.MIN_ENTRIES:
        return "okunabilir"
    return "değerlendirilemez"


def evaluate_checkpoints(checkpoints: Sequence[pd.Timestamp], primary: Sequence[str],
                         funding: Mapping[str, mfc.FundingSeries], perp: Mapping[str, pd.DataFrame],
                         spot: Mapping[str, pd.DataFrame], *, taker: mfc.Costs, mm: float) -> dict[str, Any]:
    """Kontrol noktalarını SIRAYLA sayar; ilk okunabilir noktada durur. Yalnızca SAYIM döner."""
    archive_last = max((int(funding[s].times[-1]) for s in primary if s in funding and len(funding[s].times)),
                       default=0)
    rows: list[dict[str, Any]] = []
    first_pass = None
    data_gap = None
    for c in checkpoints:
        if archive_last < (c - ARCHIVE_STALENESS).value:
            data_gap = c.isoformat()
            break
        measured = measured_at(primary, funding, c)
        row: dict[str, Any] = {"checkpoint": c.isoformat(), "weeks": weeks_between(c), "measured": len(measured),
                               "position_days": 0.0, "entries": 0}
        if len(measured) >= MIN_MEASURED:
            res = mfc.simulate(measured, funding, {s: perp[s] for s in measured}, {s: spot[s] for s in measured},
                               threshold=taker.entry_threshold, costs=taker, taker=taker, mm=mm,
                               start=MEASURE_START, end=c)
            row["position_days"] = float(sum(p.days for p in res["positions"]))
            row["entries"] = len(res["positions"])
        row["status"] = gate(row)
        rows.append({k: row[k] for k in COUNT_KEYS})
        if row["status"] == "okunabilir":
            first_pass = c.isoformat()
            break
    verdict = None
    if first_pass is None and data_gap is None and checkpoints and checkpoints[-1] == CAP:
        verdict = "KASADA ÖLÇÜLEMEZ" if rows[-1]["status"] == "ölçülemez" else "KASADA DEĞERLENDİRİLEMEZ"
    return {"rows": rows, "first_pass": first_pass, "data_gap": data_gap, "verdict": verdict}


# --------------------------------------------------------------------------- #
# Evren pinleri
# --------------------------------------------------------------------------- #
def load_universe(pins_dir: Path, config: Mapping[str, Any]) -> dict[str, Any]:
    """Pinleri SHA256 ile doğrular ve evren seçimini aynı kuralla yeniden kurup MANIFEST'le karşılaştırır."""
    pins = mfc.load_pins(pins_dir)          # SHA256SUMS'teki her dosyayı (arşiv listesi dâhil) doğrular
    listing = pins_dir / f"{ARCHIVE_LIST}.gz"
    if ARCHIVE_LIST not in (pins_dir / "SHA256SUMS").read_text(encoding="utf-8") or not listing.is_file():
        raise mfc.DataGateError(f"{ARCHIVE_LIST} pinlerde yok")
    archive = json.loads(gzip.decompress(listing.read_bytes()))
    missing: list[str] = []

    def getter(inst: str) -> pd.DataFrame:
        if inst not in pins.frames:
            missing.append(inst)
            return pd.DataFrame(columns=["open", "high", "low", "close"])
        return pins.frames[inst]

    selection = mfc.select_universes(mfc.rank_perps(pins.instruments["swap"], pins.volumes),
                                     set(pins.instruments["spot"]), getter, archive_symbols=archive,
                                     ema_symbols=mfc.ema_symbols(config), window=UNIVERSE_WINDOW)
    if missing:
        raise mfc.DataGateError("evren pinlerinde eksik mum: " + ", ".join(sorted(set(missing))))
    recorded = pins.manifest.get("selection", {})
    for key in ("primary", "today", "ema13"):
        if list(recorded.get(key, [])) != selection[key]:
            raise mfc.DataGateError(f"evren seçimi MANIFEST ile tutmuyor ({key})")
    return {"pins": pins, "archive": archive, "selection": selection}


def fetch_frames(config: Mapping[str, Any], perps: Sequence[str], end: pd.Timestamp) -> dict[str, pd.DataFrame]:
    window = window_at(end)
    frames: dict[str, pd.DataFrame] = {}
    with tempfile.TemporaryDirectory() as cache:
        for sym in perps:
            for inst in (sym, mfc.spot_of(sym)):
                frames[inst] = mfc.fetch_hourly(config, inst, cache_dir=cache, window=window)
                logger.info("%s: %d bar", inst, len(frames[inst]))
    return frames


def counts_for(config: Mapping[str, Any], args: argparse.Namespace, checkpoints: Sequence[pd.Timestamp],
               universe: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, mfc.FundingSeries], dict[str, pd.DataFrame]]:
    primary = universe["selection"]["primary"]
    last = checkpoints[-1]
    funding, _ = mfc.read_archive(Path(args.archive), with_rates=True, window=window_at(last))
    frames = fetch_frames(config, primary, last)
    perp = {s: frames[s] for s in primary}
    spot = {s: frames[mfc.spot_of(s)] for s in primary}
    taker = mfc.scenario_costs(config)["taker"]
    result = evaluate_checkpoints(checkpoints, primary, funding, perp, spot, taker=taker,
                                  mm=float(get_setting(config, "maintenance_margin")))
    return result, funding, frames


# --------------------------------------------------------------------------- #
# Aşamalar
# --------------------------------------------------------------------------- #
def run_universe(args: argparse.Namespace, config: Mapping[str, Any]) -> int:
    archive = mfc.archive_symbols(Path(args.archive))
    code = mfc.run_snapshot(args, config, window=UNIVERSE_WINDOW, out_name=OUT_NAME)
    out = Path(args.out_dir) / OUT_NAME
    raw = (json.dumps(archive, indent=1) + "\n").encode("utf-8")
    (out / f"{ARCHIVE_LIST}.gz").write_bytes(gzip.compress(raw, compresslevel=9, mtime=0))
    with (out / "SHA256SUMS").open("a", encoding="utf-8") as handle:
        handle.write(f"{hashlib.sha256(raw).hexdigest()}  {ARCHIVE_LIST}\n")
    manifest = json.loads((out / "MANIFEST.json").read_text(encoding="utf-8"))
    print(json.dumps({"primary": manifest["selection"]["primary"], "archive_symbols": len(archive)},
                     indent=2, ensure_ascii=False))
    return code


def _summary(lines: Sequence[str], path: str | None) -> None:
    if path:
        with open(path, "a", encoding="utf-8") as handle:
            handle.write("\n".join(lines) + "\n")


def run_count(args: argparse.Namespace, config: Mapping[str, Any]) -> int:
    now = mfc._utc(args.now) if args.now else pd.Timestamp.now(tz="UTC")
    checkpoints = checkpoints_due(now)
    if not checkpoints:
        msg = f"henüz kontrol noktası yok (ilki {FIRST_CHECKPOINT:%Y-%m-%d}; şimdi {now:%Y-%m-%d %H:%M}Z)"
        print(msg)
        _summary([f"§6w sayım: {msg}"], args.summary)
        return 0
    try:
        universe = load_universe(Path(args.pins), config)
        result, _, _ = counts_for(config, args, checkpoints, universe)
    except mfc.DataGateError as exc:
        logger.error("VERİ KAPISI: %s", exc)
        return 3
    payload = {"preregistration": "docs/backtest.md > 6w", "stage": "count", "now": now.isoformat(), **result}
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / f"{OUT_NAME}_counts.json").write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
                                                 encoding="utf-8")
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    lines = ["### §6w — fonlama taşıması kasa sayımı (yalnızca sayım)", "",
             "| kontrol noktası | hafta | ölçülen | pozisyon-gün | giriş | sonuç |", "|---|---|---|---|---|---|"]
    lines += [f"| {r['checkpoint'][:10]} | {r['weeks']} | {r['measured']}/{mfc.N_UNIVERSE} | {r['position_days']:.1f} "
              f"| {r['entries']} | {r['status']} |" for r in result["rows"]]
    if result["first_pass"]:
        lines.append(f"\n**İlk okunabilir kontrol noktası: {result['first_pass'][:10]}** — `measure` tek sefer koşulabilir.")
    if result["verdict"]:
        lines.append(f"\n**{result['verdict']}** (tavan {CAP:%Y-%m-%d}).")
    if result["data_gap"]:
        lines.append(f"\n**Veri kapısı:** arşiv {result['data_gap'][:10]} kontrol noktasına ulaşmıyor.")
    _summary(lines, args.summary)
    return 3 if result["data_gap"] else 0


def run_measure(args: argparse.Namespace, config: Mapping[str, Any]) -> int:
    now = mfc._utc(args.now) if args.now else pd.Timestamp.now(tz="UTC")
    checkpoints = checkpoints_due(now)
    if not checkpoints:
        logger.error("henüz kontrol noktası yok — measure koşulamaz")
        return 3
    try:
        universe = load_universe(Path(args.pins), config)
        result, _, _ = counts_for(config, args, checkpoints, universe)
    except mfc.DataGateError as exc:
        logger.error("VERİ KAPISI: %s", exc)
        return 3
    if not result["first_pass"]:
        logger.error("okunabilir kontrol noktası yok — measure koşulamaz: %s", result["rows"][-1:] or result)
        return 3
    end = mfc._utc(result["first_pass"])
    window = window_at(end)
    sel = universe["selection"]
    pins: mfc.Pins = universe["pins"]
    out = Path(args.out_dir)
    snapshot = out / "snapshot"
    frames = dict(pins.frames)
    frames.update(fetch_frames(config, sorted(set(sel["primary"]) | set(sel["today"]) | set(sel["ema13"])), end))
    volumes = {s: {d: repr(v) for d, v in days.items()} for s, days in pins.volumes.items()}
    mfc.write_pins(snapshot / OUT_NAME, instruments=pins.instruments, volumes=volumes, frames=frames,
                   selection=sel, run=args.run, window=window)
    with tempfile.TemporaryDirectory() as tmp:
        archive = Path(tmp)
        for sym in universe["archive"]:          # "bugünkü evren" evren anındaki arşiv listesiyle kurulur
            src = Path(args.archive) / f"{sym}.csv"
            if src.is_file():
                shutil.copy(src, archive / src.name)
        conflicts = Path(args.archive) / mfc.CONFLICTS_FILE
        if conflicts.is_file():
            shutil.copy(conflicts, archive / conflicts.name)
        ns = argparse.Namespace(pins=str(snapshot / OUT_NAME), archive=str(archive), out_dir=str(out))
        code = mfc.run_analysis(ns, config, measure=True, window=window, out_name=OUT_NAME)
    if code != 0:
        return code
    path = out / f"{OUT_NAME}.json"
    report = json.loads(path.read_text(encoding="utf-8"))
    row = result["rows"][-1]
    primary = report["primary_taker"]
    if primary["entries"] != row["entries"] or abs(primary["position_days"] - row["position_days"]) > 1e-9:
        logger.error("ölçüm sayımla tutmuyor: measure %s/%s ↔ count %s/%s", primary["entries"],
                     primary["position_days"], row["entries"], row["position_days"])
        return 2
    m2 = report["decision"]["m2"]
    label = {"GEÇTİ": "kasada ilk ölçümde GEÇTİ", "GEÇMEDİ": "kasada ilk ölçümde GEÇMEDİ"}.get(m2, m2)
    report["vault"] = {"preregistration": "docs/backtest.md > 6w", "checkpoint": result["first_pass"],
                       "counts": result["rows"], "label": label}
    path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(report["vault"], indent=2, ensure_ascii=False))
    return 0


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--stage", required=True, choices=("universe", "count", "measure"))
    parser.add_argument("--archive", default=str(mfc.ARCHIVE_DIR))
    parser.add_argument("--pins", default=str(PINS_DIR))
    parser.add_argument("--out-dir", default="backtests/funding_carry_vault")
    parser.add_argument("--run", default=None, help="koşu kimliği (manifest'e yazılır)")
    parser.add_argument("--now", default=None, help="yalnızca test: 'şimdi'")
    parser.add_argument("--summary", default=None, help="GitHub step summary dosyası")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    try:
        args = _parse_args(argv)
    except SystemExit as exc:
        return int(exc.code or 0) if exc.code in (0, None) else 2
    config = load_config()
    if args.stage == "universe":
        try:
            return run_universe(args, config)
        except mfc.DataGateError as exc:
            logger.error("VERİ KAPISI: %s", exc)
            return 3
    if args.stage == "count":
        return run_count(args, config)
    return run_measure(args, config)


if __name__ == "__main__":
    sys.exit(main())

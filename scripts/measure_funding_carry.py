#!/usr/bin/env python3
"""Fonlama taşıması (delta-nötr) ölçümü (docs/backtest.md > 6t). ÖLÇÜM, yeni model YOK.

Ön-kayıt (`bfd46b0`, TADİLAT-1 `b12c957`) bu betikten ÖNCE, hiçbir fonlama oranı görülmeden
commit edildi. Betik o metni MEKANİK uygular; hiçbir sayı burada SEÇİLMEZ ve hiçbir sabit CLI
girdisi değildir.

- **Tez:** fonlamanın son 7 günlük ortalaması maliyetten türetilmiş eşiği aşınca long spot +
  short perp; ortalama sıfırın altına inince çık (histerezis). Yön tahmini YOK.
- **M1 (bilgi):** fonlama kalıcı mı — `s_D` ↔ `s_{D+7g}` günlük kesitsel Spearman.
- **M2 (BİRİNCİL):** taker maliyetiyle net günlük getiri > 0; gün ∧ hafta küme bootstrap'ı,
  bağlayıcı = iki alt sınırın KÜÇÜĞÜ. < 70 pozisyon-gün ya da < 10 giriş → DEĞERLENDİRİLEMEZ
  (TADİLAT-1 > T2, §7.6'dan sapma).
- **M3 (betimsel):** üç bileşen (fonlama, baz, maliyet), devir, tutuş, en kötü baz olayları,
  maker satırları, betimsel evrenler.

**Evren (TADİLAT-1 > T1):** OKX'in bütün USDT perp'leri, 2026-05-23 → 06-22 perp cirosuna göre
sıralanır; spot karşılığı (U1) ve kimlik kapısını (U2) geçen İLK 20. Arşivde fonlaması olmayan
sembol evrende SAYILIR ama ölçülemez.

**İkinci bir uygulama YOK:** küme çekilişleri ve yüzdelik aralık `scripts/backtest_dc.py`den,
kasa kesimi `scripts/vault.py`den, mum çekimi `core/data.py::fetch_ohlcv`tan gelir.

**Salt okunur:** deftere, config'e, `data/cache/`e ve `data/funding_archive/`e YAZMAZ (snapshot
koşuya özel geçici önbellek kullanır). Kasaya ait tek bar ya da fonlama kaydı OKUNMAZ.

ÜÇ AŞAMA: `snapshot` enstrüman listelerini, 30 günlük perp cirosunu ve gereken 1H mumları
çekip gzip + SHA256SUMS + MANIFEST olarak yazar — evren seçimi dışında HİÇBİR ŞEY hesaplamaz.
`preflight` SHA256'ları, evren seçimini (yeniden kurup MANIFEST ile karşılaştırarak), arşiv
kapsamını, ödeme ızgarası boşluklarını ve ölçülen payı raporlar — hiçbir fonlama ORANI, sinyal
değeri, getiri ya da baz değeri OKUMAZ (oran kolonu hiç ayrıştırılmaz). `measure` tek seferliktir.

Çıkış kodları (karar 51): 0 = yazıldı; 3 = veri kapısı; 2 = kullanım hatası. Tetikleyicisi
`.github/workflows/measure-funding-carry.yml`.
"""

from __future__ import annotations

import argparse
import copy
import csv
import gzip
import hashlib
import io
import json
import logging
import math
import statistics
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from core.config import get_setting, load_config  # noqa: E402
from core.data import OKXClient, fetch_ohlcv  # noqa: E402
from scripts.backtest_dc import MIN_CLUSTERS, _percentiles, cluster_mean_draws  # noqa: E402
from scripts.vault import KASA_START, assert_before_vault, vault_now  # noqa: E402

logger = logging.getLogger("measure_funding_carry")

# --- Ön-kayıtlı sayılar (§6t + TADİLAT-1). Hiçbiri CLI girdisi DEĞİLDİR. -------------------
ARCHIVE_DIR = Path("data/funding_archive")
CONFLICTS_FILE = "_conflicts.csv"
PINS_DIR = Path("docs/data/pins/funding_carry")
HOUR = pd.Timedelta(hours=1)
DAY = pd.Timedelta(days=1)
DEV_START = pd.Timestamp("2026-06-22T00:00:00Z")        # arşivin ilk günü (§6t > 10)
DEV_END = KASA_START                                     # §7.8
SNAPSHOT_START = pd.Timestamp("2026-06-15T00:00:00Z")    # §6t > 2: kimlik kapısı tamponu
VOLUME_START = pd.Timestamp("2026-05-23T00:00:00Z")      # T1: 30 UTC günü
VOLUME_END = DEV_START
SIGNAL_DAYS = 7                                          # §6t > 4
HORIZON_DAYS = 14                                        # §6t > 4: eşiğin ufku
COST_MULTIPLE = 2.0                                      # §6t > 4: gidiş-dönüşün 2 katı
EXIT_THRESHOLD = 0.0                                     # §6t > 4
MEASURE_START = DEV_START + SIGNAL_DAYS * DAY            # T3: 2026-06-29
K_SLOTS = 5                                              # O3
N_UNIVERSE = 20                                          # T1
GAP_TOLERANCE = 1.5                                      # §6t > 4: medyan aralığın 1.5 katı
IDENTITY_BARS = 168                                      # T1 > U2
IDENTITY_MIN_BARS = 24                                   # T1 > U2
IDENTITY_MAX_BASIS = 0.02                                # T1 > U2
MIN_POSITION_DAYS = 70.0                                 # T2
MIN_ENTRIES = 10                                         # T2
MIN_RHO_SYMBOLS = 5                                      # T3
ITERATIONS = 10_000                                      # §6t > 8
CI_DEFINITIONS = ("day", "week")                         # §6t > 8
WORST_BASIS_EVENTS = 10                                  # §6t > 8
QUOTE = "USDT"
UNMEASURED_SENTENCE = (
    "Ölçülen semboller, kasa sonrası kurulan arşivle kesişimdir; evren seçim yanlılığının bu "
    "kısmı tamamen KAPANMIYOR — (b) onu görünür kılıyor."
)


@dataclass(frozen=True, kw_only=True)
class Costs:
    """Birim nominal başına dolum maliyeti (§6t > 6). OKX standart kademe (O8)."""
    name: str
    spot_fee: float
    perp_fee: float
    slippage: float          # bacak başına, her dolumda

    @property
    def round_trip(self) -> float:
        return 2.0 * (self.spot_fee + self.perp_fee + 2.0 * self.slippage)

    @property
    def entry_threshold(self) -> float:
        """Günlük oran: 14 gün boyunca toplanan fonlama gidiş-dönüşün 2 katını öder."""
        return COST_MULTIPLE * self.round_trip / HORIZON_DAYS


def scenario_costs(config: Mapping[str, Any]) -> dict[str, Costs]:
    slip = float(get_setting(config, "slippage_base"))
    return {
        "taker": Costs(name="taker", spot_fee=0.0010, perp_fee=0.0005, slippage=slip),
        "maker": Costs(name="maker", spot_fee=0.0008, perp_fee=0.0002, slippage=0.0),
    }


class DataGateError(RuntimeError):
    """Veri kapısı düştü: rapor yazılmaz, çıkış 3 (karar 51)."""


# --------------------------------------------------------------------------- #
# Yardımcılar
# --------------------------------------------------------------------------- #
def _utc(value: Any) -> pd.Timestamp:
    stamp = pd.Timestamp(value)
    return stamp.tz_localize("UTC") if stamp.tzinfo is None else stamp.tz_convert("UTC")


def _ms(stamp: pd.Timestamp) -> int:
    return int(_utc(stamp).value // 1_000_000)


def iso_week_key(stamp: pd.Timestamp) -> str:
    year, week, _ = _utc(stamp).isocalendar()
    return f"{year:04d}-W{week:02d}"


def spot_of(perp: str) -> str:
    """`PEPE-USDT-SWAP` → `PEPE-USDT`."""
    if not perp.endswith(f"-{QUOTE}-SWAP"):
        raise ValueError(f"USDT perp değil: {perp}")
    return perp.removesuffix("-SWAP")


def bootstrap_p(draws: Sequence[float]) -> float:
    b = len(draws)
    le = sum(1 for d in draws if d <= 0.0)
    ge = sum(1 for d in draws if d >= 0.0)
    return min(1.0, 2.0 * min(le + 1, ge + 1) / (b + 1))


def _spearman(x: Sequence[float], y: Sequence[float]) -> float | None:
    if len(x) < 2:
        return None
    rx = pd.Series(x, dtype="float64").rank(method="average").to_numpy()
    ry = pd.Series(y, dtype="float64").rank(method="average").to_numpy()
    if np.std(rx) == 0 or np.std(ry) == 0:
        return None
    return float(np.corrcoef(rx, ry)[0, 1])


# --------------------------------------------------------------------------- #
# Fonlama arşivi — yalnızca kasa ÖNCESİ satırlar
# --------------------------------------------------------------------------- #
@dataclass(kw_only=True)
class FundingSeries:
    symbol: str
    times: np.ndarray                  # int64 ns, artan
    rates: np.ndarray | None           # None = oran OKUNMADI (preflight)


def read_archive(archive_dir: Path, *, with_rates: bool) -> tuple[dict[str, FundingSeries], dict[str, Any]]:
    """`funding_time < KASA_START` satırları. `with_rates=False` iken oran kolonu AYRIŞTIRILMAZ.

    Çakışma dosyasındaki (sembol, damga) çiftleri seriden ÇIKARILIR (§6t > 2) — ızgarada boşluk
    olarak görünür ve sayılır.
    """
    cutoff_ms = _ms(KASA_START)
    conflicts: set[tuple[str, int]] = set()
    conflict_path = archive_dir / CONFLICTS_FILE
    if conflict_path.is_file():
        with conflict_path.open(newline="", encoding="utf-8") as handle:
            for row in csv.DictReader(handle):
                ms = int(row["funding_time_ms"])
                if ms < cutoff_ms:
                    conflicts.add((row["inst_id"], ms))
    series: dict[str, FundingSeries] = {}
    meta: dict[str, Any] = {"files": {}, "conflicts_in_window": sorted(f"{s}@{m}" for s, m in conflicts)}
    for path in sorted(archive_dir.glob(f"*-{QUOTE}-SWAP.csv")):
        symbol = path.name.removesuffix(".csv")
        digest = hashlib.sha256()
        stamps: list[int] = []
        rates: list[float] = []
        dropped = 0
        with path.open(newline="", encoding="utf-8") as handle:
            reader = csv.reader(handle)
            header = next(reader)
            i_inst, i_ms, i_rate = header.index("inst_id"), header.index("funding_time_ms"), header.index("realized_rate")
            for row in reader:
                if not row:
                    continue
                ms = int(row[i_ms])
                if ms >= cutoff_ms:          # kasa — okunmaz
                    continue
                if row[i_inst] != symbol:
                    raise DataGateError(f"{path.name}: yabancı satır {row[i_inst]}")
                digest.update((",".join(row) + "\n").encode("utf-8"))
                if (symbol, ms) in conflicts:
                    dropped += 1
                    continue
                stamps.append(ms)
                if with_rates:
                    rates.append(float(row[i_rate]))
        order = np.argsort(np.asarray(stamps, dtype="int64"), kind="stable")
        times = np.asarray(stamps, dtype="int64")[order] * 1_000_000
        if len(times) and np.any(np.diff(times) <= 0):
            raise DataGateError(f"{path.name}: yinelenen damga")
        series[symbol] = FundingSeries(
            symbol=symbol, times=times,
            rates=np.asarray(rates, dtype="float64")[order] if with_rates else None)
        meta["files"][symbol] = {
            "rows": int(len(times)), "conflicts_dropped": dropped,
            "first": pd.Timestamp(int(times[0]), tz="UTC").isoformat() if len(times) else None,
            "last": pd.Timestamp(int(times[-1]), tz="UTC").isoformat() if len(times) else None,
            "sha256_pre_vault_rows": digest.hexdigest(),
        }
    return series, meta


def grid_gaps(times: np.ndarray) -> int:
    """Seri boyunca medyan aralığın 1.5 katını aşan boşluk sayısı (yalnızca DAMGA)."""
    if len(times) < 3:
        return 0
    diffs = np.diff(times)
    return int(np.sum(diffs > GAP_TOLERANCE * float(np.median(diffs))))


def signal_at(series: FundingSeries, t: pd.Timestamp) -> float | None:
    """`s_T = Σ realized_rate(t ∈ (T − 7g, T]) / 7` (günlük oran); tanımsızsa None (§6t > 4).

    Tanımlılık: pencerede ≥ 2 kayıt; aralık = pencere İÇİ medyan; ilk arşiv damgası
    `≤ T − 7g + aralık`; pencere başı → ilk kayıt, ardışık kayıtlar ve son kayıt → T arası
    boşlukların hiçbiri aralığın 1.5 katını aşmaz.
    """
    if series.rates is None:
        raise RuntimeError("oranlar okunmadı (preflight sinyal hesaplayamaz)")
    t_ns = _utc(t).value
    lo = t_ns - SIGNAL_DAYS * DAY.value
    i0 = int(np.searchsorted(series.times, lo, side="right"))
    i1 = int(np.searchsorted(series.times, t_ns, side="right"))
    if i1 - i0 < 2:
        return None
    window = series.times[i0:i1]
    step = float(np.median(np.diff(window)))
    limit = GAP_TOLERANCE * step
    if series.times[0] > lo + step:
        return None
    if window[0] - lo > limit or t_ns - window[-1] > limit or np.any(np.diff(window) > limit):
        return None
    return float(np.sum(series.rates[i0:i1])) / SIGNAL_DAYS


# --------------------------------------------------------------------------- #
# Evren (TADİLAT-1 > T1)
# --------------------------------------------------------------------------- #
def rank_perps(swaps: Sequence[str], volumes: Mapping[str, Mapping[str, float]]) -> list[tuple[str, float]]:
    """30 günlük USDT cirosu toplamı, azalan; eşitlikte sembol adı. Mumu olmayan gün 0."""
    ranked = [(s, float(sum(volumes.get(s, {}).values()))) for s in swaps]
    return sorted(ranked, key=lambda kv: (-kv[1], kv[0]))


def identity_check(perp: pd.DataFrame, spot: pd.DataFrame) -> tuple[bool, int]:
    """U2: ortak 1H barların ilk 168'inde (en az 24) medyan |perp/spot − 1| ≤ %2."""
    if perp.empty or spot.empty:
        return False, 0
    common = perp.index.intersection(spot.index).sort_values()
    common = common[common >= SNAPSHOT_START][:IDENTITY_BARS]
    if len(common) < IDENTITY_MIN_BARS:
        return False, int(len(common))
    ratio = perp.loc[common, "close"].to_numpy(dtype="float64") / spot.loc[common, "close"].to_numpy(dtype="float64")
    return bool(float(np.median(np.abs(ratio - 1.0))) <= IDENTITY_MAX_BASIS), int(len(common))


FrameGetter = Callable[[str], pd.DataFrame]


def eligibility(perp: str, spot_ids: set[str], frames: FrameGetter) -> str:
    """"ok" | "no_spot" | "spot_no_bars" | "identity" — GEÇTİ/KALDI dışında DEĞER yazılmaz."""
    spot = spot_of(perp)
    if spot not in spot_ids:
        return "no_spot"
    spot_frame = frames(spot)
    if spot_frame.empty:
        return "spot_no_bars"
    passed, _ = identity_check(frames(perp), spot_frame)
    return "ok" if passed else "identity"


def select_universes(ranked: Sequence[tuple[str, float]], spot_ids: set[str], frames: FrameGetter, *,
                     archive_symbols: Sequence[str], ema_symbols: Sequence[str]) -> dict[str, Any]:
    status: dict[str, str] = {}

    def check(sym: str) -> str:
        if sym not in status:
            status[sym] = eligibility(sym, spot_ids, frames)
        return status[sym]

    primary: list[str] = []
    walked: list[dict[str, Any]] = []
    for rank, (sym, _) in enumerate(ranked, start=1):
        if len(primary) >= N_UNIVERSE:
            break
        verdict = check(sym)
        walked.append({"rank": rank, "symbol": sym, "status": verdict})
        if verdict == "ok":
            primary.append(sym)
    today = [s for s in archive_symbols if check(s) == "ok"]
    ema = [s for s in ema_symbols if check(s) == "ok"]
    return {
        "primary": primary, "walk": walked, "today": today, "ema13": ema,
        "excluded": {name: {s: status[s] for s in group if status.get(s) != "ok"}
                     for name, group in (("today", archive_symbols), ("ema13", ema_symbols))},
    }


# --------------------------------------------------------------------------- #
# Snapshot ve pins
# --------------------------------------------------------------------------- #
def list_instruments(client: Any, inst_type: str) -> list[str]:
    rows = client.get("/api/v5/public/instruments", {"instType": inst_type})
    if inst_type == "SWAP":
        ids = [r["instId"] for r in rows
               if r.get("settleCcy") == QUOTE and r.get("ctType", "linear") == "linear"
               and str(r.get("instId", "")).endswith(f"-{QUOTE}-SWAP")]
    else:
        ids = [r["instId"] for r in rows if r.get("quoteCcy") == QUOTE]
    return sorted(set(ids))


def daily_quote_volume(client: Any, inst_id: str) -> dict[str, str]:
    """`1Dutc` mumlarının `volCcyQuote` METNİ, [VOLUME_START, VOLUME_END). Tek istek (≤ 100 gün)."""
    assert_before_vault(VOLUME_END, what="hacim penceresi")
    rows = client.get("/api/v5/market/history-candles",
                      {"instId": inst_id, "bar": "1Dutc", "after": str(_ms(VOLUME_END)), "limit": "100"})
    out: dict[str, str] = {}
    for raw in rows:
        ts = pd.Timestamp(int(raw[0]), unit="ms", tz="UTC")
        if VOLUME_START <= ts < VOLUME_END:
            if len(raw) < 8:
                raise DataGateError(f"{inst_id}: volCcyQuote alanı yok")
            out[ts.strftime("%Y-%m-%d")] = str(raw[7]).strip()
    return out


def fetch_hourly(config: Mapping[str, Any], inst_id: str, *, cache_dir: str,
                 fetcher: Callable[..., pd.DataFrame] | None = None) -> pd.DataFrame:
    fetch = fetcher if fetcher is not None else fetch_ohlcv
    now = vault_now()
    local = copy.deepcopy(dict(config))
    local["timeframe"] = "1H"
    bars = int(math.ceil((now - SNAPSHOT_START) / HOUR)) + 2
    local["data"] = {**local["data"], "history_bars": bars, "cache_dir": cache_dir}
    frame = fetch(local, inst_id, now=now)
    frame = frame.loc[(frame.index >= SNAPSHOT_START) & (frame.index + HOUR <= now)]
    return frame[["open", "high", "low", "close"]]


def _frame_csv(frame: pd.DataFrame) -> bytes:
    lines = ["ts,open,high,low,close"]
    for ts, row in frame.iterrows():
        values = ",".join(repr(float(row[c])) for c in ("open", "high", "low", "close"))
        lines.append(f"{_utc(ts).isoformat()},{values}")
    return ("\n".join(lines) + "\n").encode("utf-8")


def write_pins(out: Path, *, instruments: Mapping[str, Sequence[str]], volumes: Mapping[str, Mapping[str, str]],
               frames: Mapping[str, pd.DataFrame], selection: Mapping[str, Any], run: str | None) -> dict[str, Any]:
    out.mkdir(parents=True, exist_ok=True)
    payloads: dict[str, bytes] = {
        "instruments.json": (json.dumps({k: list(v) for k, v in instruments.items()}, indent=1) + "\n").encode("utf-8"),
    }
    vol_lines = ["inst_id,day,vol_ccy_quote"]
    for sym in sorted(volumes):
        for day in sorted(volumes[sym]):
            vol_lines.append(f"{sym},{day},{volumes[sym][day]}")
    payloads["volume_1d.csv"] = ("\n".join(vol_lines) + "\n").encode("utf-8")
    for inst in sorted(frames):
        payloads[f"{inst}_1H.csv"] = _frame_csv(frames[inst])
    files = []
    for name, raw in payloads.items():
        (out / f"{name}.gz").write_bytes(gzip.compress(raw, compresslevel=9, mtime=0))
        entry: dict[str, Any] = {"path": name, "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}
        if name.endswith("_1H.csv"):
            frame = frames[name.removesuffix("_1H.csv")]
            entry.update(bars=len(frame),
                         first=_utc(frame.index[0]).isoformat() if len(frame) else None,
                         last=_utc(frame.index[-1]).isoformat() if len(frame) else None)
        files.append(entry)
    manifest = {
        "purpose": "OKX enstrüman listeleri, 30g perp cirosu, 1H mumlar — docs/backtest.md > 6t; "
                   "evren seçimi dışında hiçbir şey hesaplanmadı",
        "snapshot_start": SNAPSHOT_START.isoformat(), "now": vault_now().isoformat(),
        "volume_window": [VOLUME_START.isoformat(), VOLUME_END.isoformat()],
        "snapshot_run": run,
        "note": "ts = bar AÇILIŞI (UTC); sha256 SIKIŞTIRILMAMIŞ içeriğe aittir",
        "selection": {k: selection[k] for k in ("primary", "today", "ema13")},
        "files": files,
    }
    (out / "MANIFEST.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n")
    (out / "SHA256SUMS").write_text("".join(f"{f['sha256']}  {f['path']}\n" for f in files))
    return manifest


@dataclass(kw_only=True)
class Pins:
    instruments: dict[str, list[str]]
    volumes: dict[str, dict[str, float]]
    frames: dict[str, pd.DataFrame]
    manifest: dict[str, Any]


def load_pins(pins: Path) -> Pins:
    sums = pins / "SHA256SUMS"
    if not sums.is_file() or not (pins / "MANIFEST.json").is_file():
        raise DataGateError(f"{pins}: SHA256SUMS ya da MANIFEST yok")
    raws: dict[str, bytes] = {}
    for line in sums.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        digest, rel = line.split(None, 1)
        rel = rel.strip()
        gz = pins / f"{rel}.gz"
        if not gz.is_file():
            raise DataGateError(f"{rel}: dosya yok")
        raw = gzip.decompress(gz.read_bytes())
        if hashlib.sha256(raw).hexdigest() != digest:
            raise DataGateError(f"{rel}: SHA256 tutmuyor")
        raws[rel] = raw
    for need in ("instruments.json", "volume_1d.csv"):
        if need not in raws:
            raise DataGateError(f"pins içinde {need} yok")
    volumes: dict[str, dict[str, float]] = {}
    for row in csv.DictReader(io.StringIO(raws["volume_1d.csv"].decode("utf-8"))):
        volumes.setdefault(row["inst_id"], {})[row["day"]] = float(row["vol_ccy_quote"])
    frames: dict[str, pd.DataFrame] = {}
    for rel, raw in raws.items():
        if rel.endswith("_1H.csv"):
            frame = pd.read_csv(io.BytesIO(raw))
            frame["ts"] = pd.to_datetime(frame["ts"], utc=True)
            frames[rel.removesuffix("_1H.csv")] = frame.set_index("ts").sort_index()
    return Pins(instruments=json.loads(raws["instruments.json"]), volumes=volumes, frames=frames,
                manifest=json.loads((pins / "MANIFEST.json").read_text(encoding="utf-8")))


def archive_symbols(archive_dir: Path) -> list[str]:
    return sorted(p.name.removesuffix(".csv") for p in archive_dir.glob(f"*-{QUOTE}-SWAP.csv"))


def ema_symbols(config: Mapping[str, Any]) -> list[str]:
    return list(get_setting(config, "layers.ema.universe"))


# --------------------------------------------------------------------------- #
# Simülasyon (§6t > 3–7)
# --------------------------------------------------------------------------- #
@dataclass(kw_only=True)
class Position:
    symbol: str
    entry: pd.Timestamp
    s0: float
    p0: float
    q_spot: float
    q_perp: float
    entry_cost: float
    exit: pd.Timestamp | None = None
    s1: float | None = None
    p1: float | None = None
    exit_cost: float = 0.0
    exit_reason: str = ""
    funding: list[tuple[pd.Timestamp, float]] = field(default_factory=list)

    @property
    def basis(self) -> float:
        """`q_spot(S₁ − S₀) + q_perp(P₀ − P₁)` = `N(S₁/S₀ − P₁/P₀)` (§6t > 7)."""
        return self.q_spot * (self.s1 - self.s0) + self.q_perp * (self.p0 - self.p1)

    @property
    def funding_total(self) -> float:
        return float(sum(v for _, v in self.funding))

    @property
    def cost(self) -> float:
        return self.entry_cost + self.exit_cost

    @property
    def net(self) -> float:
        return self.funding_total + self.basis - self.cost

    @property
    def days(self) -> float:
        return (self.exit - self.entry) / DAY


class Prices:
    """1H açılış/kapanış erişimi. `open_at(t)` = t'de AÇILAN barın açılışı."""

    def __init__(self, frame: pd.DataFrame) -> None:
        self.frame = frame
        self._open = frame["open"].to_dict() if not frame.empty else {}
        self._index = frame.index

    def open_at(self, t: pd.Timestamp) -> float | None:
        value = self._open.get(t)
        return None if value is None else float(value)

    def mark(self, t: pd.Timestamp) -> float | None:
        """İşaretleme: t'nin açılışı; yoksa t'den önce kapanan son barın kapanışı."""
        value = self.open_at(t)
        if value is not None:
            return value
        i = self._index.searchsorted(t - HOUR, side="right") - 1
        return None if i < 0 else float(self.frame["close"].iloc[i])

    def bar(self, t: pd.Timestamp) -> pd.Series | None:
        return self.frame.loc[t] if t in self._open else None


def _fill_cost(costs: Costs, spot_notional: float, perp_notional: float) -> float:
    return (spot_notional * (costs.spot_fee + costs.slippage)
            + perp_notional * (costs.perp_fee + costs.slippage))


def simulate(symbols: Sequence[str], funding: Mapping[str, FundingSeries], perp: Mapping[str, pd.DataFrame],
             spot: Mapping[str, pd.DataFrame], *, threshold: float, costs: Costs, taker: Costs, mm: float,
             start: pd.Timestamp = MEASURE_START, end: pd.Timestamp = DEV_END,
             notional: float = 1.0) -> dict[str, Any]:
    """Saatlik karar döngüsü. Adım H: (a) [H−1h, H) barında likidasyon, (b) H'de planlanmış dolumlar,
    (c) H = end − 1h ise dönem sonu kapanışı, (d) H'de karar → dolum H + 1h (§6t > 3)."""
    P = {s: Prices(perp[s]) for s in symbols}
    S = {s: Prices(spot[s]) for s in symbols}
    signal_cache: dict[tuple[str, pd.Timestamp], float | None] = {}

    def sig(sym: str, t: pd.Timestamp) -> float | None:
        key = (sym, t)
        if key not in signal_cache:
            signal_cache[key] = signal_at(funding[sym], t)
        return signal_cache[key]

    open_pos: dict[str, Position] = {}
    closed: list[Position] = []
    pending_entries: list[str] = []
    pending_exits: set[str] = set()
    counters = {"entry_fill_missing": 0, "exit_postponed": 0, "liquidations": 0,
                "undefined_signal_hours_open": 0, "over_capacity_hours": 0}
    last_fill = end - 2 * HOUR
    final = end - HOUR

    def close(pos: Position, t: pd.Timestamp, s1: float, p1: float, cost: float, reason: str) -> None:
        pos.exit, pos.s1, pos.p1, pos.exit_cost, pos.exit_reason = t, s1, p1, cost, reason
        f = funding[pos.symbol]
        lo = int(np.searchsorted(f.times, pos.entry.value, side="right"))
        hi = int(np.searchsorted(f.times, t.value, side="left"))
        for k in range(lo, hi):                    # giriş < t < çıkış (§6t > 3)
            ft = pd.Timestamp(int(f.times[k]), tz="UTC")
            price = P[pos.symbol].mark(ft)
            pos.funding.append((ft, float(f.rates[k]) * pos.q_perp * price))
        closed.append(pos)
        del open_pos[pos.symbol]
        pending_exits.discard(pos.symbol)

    h = start
    while h <= final:
        # (a) likidasyon — [h − 1h, h) barı, o barda açık olan pozisyonlar
        prev = h - HOUR
        for sym in sorted(open_pos):
            pos = open_pos[sym]
            if pos.entry > prev:
                continue
            bar = P[sym].bar(prev)
            if bar is None:
                continue
            liq = pos.p0 * (2.0 - mm)
            if float(bar["high"]) >= liq:
                sbar = S[sym].bar(prev)
                s1 = float(sbar["close"]) if sbar is not None else S[sym].mark(h)
                remaining = mm * pos.q_perp * pos.p0          # teminatın kalan kısmı da kaybedilir
                cost = pos.q_spot * s1 * (taker.spot_fee + taker.slippage) + remaining
                close(pos, h, s1, liq, cost, "liquidation")
                counters["liquidations"] += 1
        # (b) dolumlar
        for sym in sorted(pending_exits & set(open_pos)):
            s1, p1 = S[sym].open_at(h), P[sym].open_at(h)
            if s1 is None or p1 is None:
                counters["exit_postponed"] += 1
                continue
            pos = open_pos[sym]
            close(pos, h, s1, p1, _fill_cost(costs, pos.q_spot * s1, pos.q_perp * p1), "signal")
        for sym in pending_entries:
            s0, p0 = S[sym].open_at(h), P[sym].open_at(h)
            if s0 is None or p0 is None or sym in open_pos:
                counters["entry_fill_missing"] += 1
                continue
            open_pos[sym] = Position(symbol=sym, entry=h, s0=s0, p0=p0, q_spot=notional / s0,
                                     q_perp=notional / p0, entry_cost=_fill_cost(costs, notional, notional))
        pending_entries = []
        if len(open_pos) > K_SLOTS:
            counters["over_capacity_hours"] += 1
        # (c) dönem sonu — son kasa öncesi barın açılışı, çıkış maliyeti ÖDENEREK (§6t > 4, O9)
        if h == final:
            for sym in sorted(open_pos):
                pos = open_pos[sym]
                s1, p1 = S[sym].mark(h), P[sym].mark(h)
                close(pos, h, s1, p1, _fill_cost(costs, pos.q_spot * s1, pos.q_perp * p1), "period_end")
            break
        # (d) karar
        if h + HOUR <= last_fill:
            for sym in sorted(open_pos):
                if sym in pending_exits:
                    continue
                s = sig(sym, h)
                if s is None:
                    counters["undefined_signal_hours_open"] += 1
                elif s < EXIT_THRESHOLD:
                    pending_exits.add(sym)
            free = K_SLOTS - (len(open_pos) - len(pending_exits & set(open_pos)))
            if free > 0:
                candidates = []
                for sym in symbols:
                    if sym in open_pos:
                        continue
                    s = sig(sym, h)
                    if s is not None and s > threshold:
                        candidates.append((-s, sym))
                pending_entries = [sym for _, sym in sorted(candidates)[:free]]
        h += HOUR
    return {"positions": closed, "counters": counters}


def daily_equity(positions: Sequence[Position], perp: Mapping[str, pd.DataFrame], spot: Mapping[str, pd.DataFrame],
                 days: Sequence[pd.Timestamp]) -> list[float]:
    """E_D − C: D'ye kadar gerçekleşen nakit + açık pozisyonların gerçekleşmemiş bazı (§6t > 7)."""
    P = {s: Prices(f) for s, f in perp.items()}
    S = {s: Prices(f) for s, f in spot.items()}
    out = []
    for d in days:
        total = 0.0
        for pos in positions:
            if d < pos.entry:
                continue
            total -= pos.entry_cost
            total += sum(v for t, v in pos.funding if t <= d)
            if d < pos.exit:
                total += pos.q_spot * (S[pos.symbol].mark(d) - pos.s0) + pos.q_perp * (pos.p0 - P[pos.symbol].mark(d))
            else:
                total += pos.basis - pos.exit_cost
        out.append(total)
    return out


def measurement_days() -> list[pd.Timestamp]:
    return list(pd.date_range(MEASURE_START, DEV_END, freq="D"))


def ci_block(returns: Sequence[float], days: Sequence[pd.Timestamp], *, seed_base: str, alpha: float) -> dict[str, Any]:
    out: dict[str, Any] = {"mean_daily": float(np.mean(returns)) if len(returns) else None, "n_days": len(returns)}
    lowers = []
    for definition in CI_DEFINITIONS:
        groups: dict[str, list[float]] = {}
        for d, r in zip(days, returns):
            key = d.strftime("%Y-%m-%d") if definition == "day" else iso_week_key(d)
            groups.setdefault(key, []).append(r)
        draws = cluster_mean_draws(groups, iterations=ITERATIONS, seed=f"{seed_base}:funding_carry:{definition}")
        lo, hi = _percentiles(draws, alpha)
        out[definition] = {"clusters": len(groups), "ci_low": lo, "ci_high": hi, "p": bootstrap_p(draws)}
        lowers.append(lo)
    out["binding_low"] = min(lowers)
    out["weeks"] = out["week"]["clusters"]
    return out


def summarize(result: Mapping[str, Any], perp: Mapping[str, pd.DataFrame], spot: Mapping[str, pd.DataFrame], *,
              capital: float, seed_base: str, alpha: float, with_ci: bool) -> dict[str, Any]:
    positions: list[Position] = result["positions"]
    days = measurement_days()
    equity = daily_equity(positions, perp, spot, days)
    returns = [(b - a) / capital for a, b in zip(equity[:-1], equity[1:])]
    ret_days = days[:-1]
    pos_days = float(sum(p.days for p in positions))
    out: dict[str, Any] = {
        "positions": len(positions),
        "entries": len(positions),
        "position_days": pos_days,
        "components": {
            "funding": float(sum(p.funding_total for p in positions)) / capital,
            "basis": float(sum(p.basis for p in positions)) / capital,
            "cost": float(sum(p.cost for p in positions)) / capital,
            "net": float(sum(p.net for p in positions)) / capital,
        },
        "total_return": (equity[-1] - equity[0]) / capital,
        "counters": result["counters"],
        "slot_occupancy": pos_days / (K_SLOTS * (DEV_END - MEASURE_START) / DAY),
        "days_with_position": float(np.mean([any(p.entry <= d < p.exit for p in positions) for d in ret_days])),
        "turnover_per_day": (len(positions) * 2.0 / capital) / ((DEV_END - MEASURE_START) / DAY),
        "holding_days": {"mean": float(np.mean([p.days for p in positions])) if positions else None,
                         "median": float(np.median([p.days for p in positions])) if positions else None},
        "exit_reasons": {r: sum(1 for p in positions if p.exit_reason == r)
                         for r in ("signal", "liquidation", "period_end")},
        "period_end_exit_cost": float(sum(p.exit_cost for p in positions if p.exit_reason == "period_end")) / capital,
    }
    out["total_return_no_period_end_cost"] = out["total_return"] + out["period_end_exit_cost"]
    if with_ci:
        out["m2"] = ci_block(returns, ret_days, seed_base=seed_base, alpha=alpha)
    else:
        out["mean_daily"] = float(np.mean(returns)) if returns else None
    out["daily"] = [{"day": d.strftime("%Y-%m-%d"), "return": r} for d, r in zip(ret_days, returns)]
    return out


def per_symbol(positions: Sequence[Position], capital: float) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for p in positions:
        row = out.setdefault(p.symbol, {"positions": 0, "days": 0.0, "funding": 0.0, "basis": 0.0, "cost": 0.0, "net": 0.0})
        row["positions"] += 1
        row["days"] += p.days
        for k, v in (("funding", p.funding_total), ("basis", p.basis), ("cost", p.cost), ("net", p.net)):
            row[k] += v / capital
    return dict(sorted(out.items()))


def position_rows(positions: Sequence[Position], capital: float) -> list[dict[str, Any]]:
    return [{"symbol": p.symbol, "entry": p.entry.isoformat(), "exit": p.exit.isoformat(),
             "days": p.days, "exit_reason": p.exit_reason,
             "funding": p.funding_total / capital, "basis": p.basis / capital,
             "cost": p.cost / capital, "net": p.net / capital} for p in positions]


# --------------------------------------------------------------------------- #
# M1 — mekanizma
# --------------------------------------------------------------------------- #
def mechanism(symbols: Sequence[str], funding: Mapping[str, FundingSeries], *, threshold: float,
              seed_base: str, alpha: float) -> dict[str, Any]:
    anchors = [d for d in pd.date_range(MEASURE_START, DEV_END, freq="D") if d + SIGNAL_DAYS * DAY <= DEV_END]
    daily: list[tuple[pd.Timestamp, float]] = []
    pooled_x: list[float] = []
    pooled_y: list[float] = []
    skipped = 0
    for d in anchors:
        xs, ys = [], []
        for sym in symbols:
            x = signal_at(funding[sym], d)
            y = signal_at(funding[sym], d + SIGNAL_DAYS * DAY)
            if x is None or y is None:
                continue
            xs.append(x)
            ys.append(y)
        pooled_x += xs
        pooled_y += ys
        rho = _spearman(xs, ys) if len(xs) >= MIN_RHO_SYMBOLS else None
        if rho is None:
            skipped += 1
            continue
        daily.append((d, rho))
    out: dict[str, Any] = {"anchors": len(anchors), "days_skipped": skipped, "days": len(daily),
                           "pooled_spearman": _spearman(pooled_x, pooled_y), "pairs": len(pooled_x)}
    above = [(x, y) for x, y in zip(pooled_x, pooled_y) if x > threshold]
    out["conditional"] = {"n": len(above),
                          "share_next_positive": float(np.mean([y > 0 for _, y in above])) if above else None,
                          "share_next_above_threshold": float(np.mean([y > threshold for _, y in above])) if above else None}
    if not daily:
        out.update(rho_mean=None, persistent=False, evaluable=False)
        return out
    groups: dict[str, list[float]] = {}
    for d, rho in daily:
        groups.setdefault(iso_week_key(d), []).append(rho)
    draws = cluster_mean_draws(groups, iterations=ITERATIONS, seed=f"{seed_base}:funding_carry:m1")
    lo, hi = _percentiles(draws, alpha)
    out.update(rho_mean=float(np.mean([r for _, r in daily])), weeks=len(groups), ci_low=lo, ci_high=hi,
               p=bootstrap_p(draws), evaluable=len(groups) >= MIN_CLUSTERS)
    out["persistent"] = bool(out["evaluable"] and out["rho_mean"] > 0 and lo > 0)
    return out


# --------------------------------------------------------------------------- #
# Karar (§6t > 12, TADİLAT-1 > T2)
# --------------------------------------------------------------------------- #
def decide(primary: Mapping[str, Any], m1: Mapping[str, Any]) -> dict[str, Any]:
    reasons = []
    if primary["position_days"] < MIN_POSITION_DAYS:
        reasons.append(f"pozisyon-gün {primary['position_days']:.2f} < {MIN_POSITION_DAYS:g}")
    if primary["entries"] < MIN_ENTRIES:
        reasons.append(f"giriş {primary['entries']} < {MIN_ENTRIES}")
    m2 = primary["m2"]
    if m2["weeks"] < MIN_CLUSTERS:
        reasons.append(f"hafta {m2['weeks']} < {MIN_CLUSTERS}")
    if reasons:
        return {"m2": "DEĞERLENDİRİLEMEZ", "reasons": reasons, "reading":
                "kural olduğu gibi DONDURULUR; kasada tek seferlik İLK ÖLÇÜM (≥ 13 hafta ∧ asgari eşik) — "
                "sonucu 'doğrulandı' değil 'kasada ilk ölçümde geçti/geçmedi' (TADİLAT-1 > T2)"}
    passed = bool(m2["binding_low"] > 0)
    persistent = bool(m1.get("persistent"))
    if passed and persistent:
        reading = "kasaya aday — dondur, kasa ön-kaydı"
    elif passed:
        reading = "mekanizmasız geçiş — kasaya aday DEĞİL"
    elif persistent:
        reading = "öncül tutuyor, maliyet yiyor — maker satırı betimsel"
    else:
        reading = "tez dayanaksız"
    return {"m2": "GEÇTİ" if passed else "GEÇMEDİ", "m1": "kalıcı" if persistent else "kalıcı değil",
            "reading": reading}


# --------------------------------------------------------------------------- #
# Aşamalar
# --------------------------------------------------------------------------- #
def run_snapshot(args: argparse.Namespace, config: Mapping[str, Any], *, client: Any | None = None,
                 fetcher: Callable[..., pd.DataFrame] | None = None) -> int:
    active = client if client is not None else OKXClient.from_config(dict(config))
    swaps = list_instruments(active, "SWAP")
    spots = list_instruments(active, "SPOT")
    volumes_text: dict[str, dict[str, str]] = {}
    for sym in swaps:
        volumes_text[sym] = daily_quote_volume(active, sym)
    volumes = {s: {d: float(v) for d, v in days.items()} for s, days in volumes_text.items()}
    ranked = rank_perps(swaps, volumes)
    frames: dict[str, pd.DataFrame] = {}
    with tempfile.TemporaryDirectory() as cache:
        def getter(inst: str) -> pd.DataFrame:
            if inst not in frames:
                frames[inst] = fetch_hourly(config, inst, cache_dir=cache, fetcher=fetcher)
                logger.info("%s: %d bar", inst, len(frames[inst]))
            return frames[inst]

        selection = select_universes(ranked, set(spots), getter, archive_symbols=archive_symbols(Path(args.archive)),
                                     ema_symbols=ema_symbols(config))
    manifest = write_pins(Path(args.out_dir) / "funding_carry", instruments={"swap": swaps, "spot": spots},
                          volumes=volumes_text, frames=frames, selection=selection, run=args.run)
    print(json.dumps({"selection": manifest["selection"], "files": len(manifest["files"])}, indent=2, ensure_ascii=False))
    if len(selection["primary"]) < N_UNIVERSE:
        logger.error("birincil evren %d < %d sembol", len(selection["primary"]), N_UNIVERSE)
        return 3
    return 0


def load_inputs(args: argparse.Namespace, config: Mapping[str, Any], *, with_rates: bool) -> dict[str, Any]:
    pins = load_pins(Path(args.pins))
    ranked = rank_perps(pins.instruments["swap"], pins.volumes)
    missing: list[str] = []

    def getter(inst: str) -> pd.DataFrame:
        if inst not in pins.frames:
            missing.append(inst)
            return pd.DataFrame(columns=["open", "high", "low", "close"])
        return pins.frames[inst]

    arch = archive_symbols(Path(args.archive))
    selection = select_universes(ranked, set(pins.instruments["spot"]), getter,
                                 archive_symbols=arch, ema_symbols=ema_symbols(config))
    if missing:
        raise DataGateError("pins içinde eksik mum: " + ", ".join(sorted(set(missing))))
    recorded = pins.manifest.get("selection", {})
    for key in ("primary", "today", "ema13"):
        if list(recorded.get(key, [])) != selection[key]:
            raise DataGateError(f"evren seçimi MANIFEST ile tutmuyor ({key})")
    if len(selection["primary"]) < N_UNIVERSE:
        raise DataGateError(f"birincil evren {len(selection['primary'])} < {N_UNIVERSE}")
    funding, archive_meta = read_archive(Path(args.archive), with_rates=with_rates)
    measured = [s for s in selection["primary"] if s in funding and len(funding[s].times)]
    if not measured:
        raise DataGateError("birincil evrende ölçülebilir sembol yok")
    return {"pins": pins, "ranked": ranked, "selection": selection, "funding": funding,
            "archive_meta": archive_meta, "measured": measured}


def coverage(frames: Mapping[str, pd.DataFrame], symbols: Sequence[str]) -> dict[str, Any]:
    expected = int((DEV_END - DEV_START) / HOUR)
    out = {}
    for sym in symbols:
        for inst in (sym, spot_of(sym)):
            frame = frames.get(inst, pd.DataFrame())
            inside = frame.loc[(frame.index >= DEV_START) & (frame.index < DEV_END)] if len(frame) else frame
            out[inst] = {"bars_dev_window": int(len(inside)), "expected": expected,
                         "first": _utc(frame.index[0]).isoformat() if len(frame) else None,
                         "last": _utc(frame.index[-1]).isoformat() if len(frame) else None}
    return out


def universe_report(inputs: Mapping[str, Any]) -> dict[str, Any]:
    sel = inputs["selection"]
    funding = inputs["funding"]
    measured = inputs["measured"]
    return {
        "primary": sel["primary"], "walk": sel["walk"],
        "measured": measured, "unmeasured": [s for s in sel["primary"] if s not in measured],
        "measured_share": len(measured) / N_UNIVERSE, "measured_note": UNMEASURED_SENTENCE,
        "delisted_note": "enstrüman listesi yalnızca bugün işlem gören sözleşmeleri döndürür; "
                         "2026-06-22'den sonra kaldırılmış perp'ler sıralamaya giremedi (TADİLAT-1 > T1)",
        "today": [s for s in sel["today"] if s in funding], "ema13": [s for s in sel["ema13"] if s in funding],
        "excluded": sel["excluded"],
    }


def run_analysis(args: argparse.Namespace, config: Mapping[str, Any], *, measure: bool) -> int:
    try:
        inputs = load_inputs(args, config, with_rates=measure)
    except DataGateError as exc:
        logger.error("VERİ KAPISI: %s", exc)
        return 3
    pins: Pins = inputs["pins"]
    funding: dict[str, FundingSeries] = inputs["funding"]
    universes = universe_report(inputs)
    report: dict[str, Any] = {
        "stage": "measure" if measure else "preflight",
        "preregistration": "docs/backtest.md > 6t (bfd46b0, TADİLAT-1 b12c957)",
        "window": {"dev_start": DEV_START.isoformat(), "measure_start": MEASURE_START.isoformat(),
                   "dev_end": DEV_END.isoformat(), "weeks": len({iso_week_key(d) for d in measurement_days()[:-1]})},
        "universe": universes,
        "archive": {**inputs["archive_meta"],
                    "grid_gaps": {s: grid_gaps(funding[s].times) for s in sorted(funding)}},
        "coverage": coverage(pins.frames, sorted(set(universes["measured"]) | set(universes["today"]) | set(universes["ema13"]))),
    }
    costs = scenario_costs(config)
    report["thresholds"] = {name: {"round_trip": c.round_trip, "entry_daily": c.entry_threshold, "exit_daily": EXIT_THRESHOLD}
                            for name, c in costs.items()}
    if measure:
        alpha = float(get_setting(config, "acceptance.edge_ci_alpha"))
        seed_base = str(get_setting(config, "random_seed"))
        mm = float(get_setting(config, "maintenance_margin"))
        capital = K_SLOTS * 2.0
        perp = pins.frames
        spot = {s: pins.frames[spot_of(s)] for s in funding if spot_of(s) in pins.frames}

        def run(symbols: Sequence[str], threshold: float, cost: Costs) -> dict[str, Any]:
            return simulate(symbols, funding, {s: perp[s] for s in symbols}, {s: spot[s] for s in symbols},
                            threshold=threshold, costs=cost, taker=costs["taker"], mm=mm)

        measured = universes["measured"]
        taker = run(measured, costs["taker"].entry_threshold, costs["taker"])
        primary = summarize(taker, perp, spot, capital=capital, seed_base=seed_base, alpha=alpha, with_ci=True)
        m1 = mechanism(measured, funding, threshold=costs["taker"].entry_threshold, seed_base=seed_base, alpha=alpha)
        maker_m1 = run(measured, costs["maker"].entry_threshold, costs["maker"])
        m2_recost = [copy.copy(p) for p in taker["positions"]]
        for p in m2_recost:
            p.entry_cost = _fill_cost(costs["maker"], p.q_spot * p.s0, p.q_perp * p.p0)
            if p.exit_reason != "liquidation":
                p.exit_cost = _fill_cost(costs["maker"], p.q_spot * p.s1, p.q_perp * p.p1)
        worst = sorted(taker["positions"], key=lambda p: p.basis)[:WORST_BASIS_EVENTS]
        report["m1"] = m1
        report["primary_taker"] = primary
        report["decision"] = decide(primary, m1)
        report["per_symbol"] = per_symbol(taker["positions"], capital)
        report["worst_basis"] = position_rows(worst, capital)
        basis = [p.basis / capital for p in taker["positions"]]
        report["basis_distribution"] = ({q: float(np.quantile(basis, q / 100)) for q in (1, 5, 25, 50, 75, 95, 99)}
                                        if basis else None)
        report["descriptive"] = {
            "maker_own_threshold": summarize(maker_m1, perp, spot, capital=capital, seed_base=seed_base,
                                             alpha=alpha, with_ci=False),
            "taker_trades_at_maker_cost": summarize({"positions": m2_recost, "counters": taker["counters"]},
                                                    perp, spot, capital=capital, seed_base=seed_base,
                                                    alpha=alpha, with_ci=False),
        }
        for name in ("today", "ema13"):
            syms = universes[name]
            res = run(syms, costs["taker"].entry_threshold, costs["taker"])
            report["descriptive"][f"universe_{name}"] = summarize(res, perp, spot, capital=capital,
                                                                  seed_base=seed_base, alpha=alpha, with_ci=False)
        positions_csv = position_rows(taker["positions"], capital)
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    name = "funding_carry.json" if measure else "funding_carry_preflight.json"
    (out / name).write_text(json.dumps(_clean(report), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    if measure:
        with (out / "funding_carry_positions.csv").open("w", newline="", encoding="utf-8") as handle:
            fields = ["symbol", "entry", "exit", "days", "exit_reason", "funding", "basis", "cost", "net"]
            writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
            writer.writeheader()
            writer.writerows(positions_csv)
        print(json.dumps(_clean({k: report[k] for k in ("decision", "m1")}), indent=2, ensure_ascii=False))
        print(json.dumps(_clean({k: v for k, v in report["primary_taker"].items() if k != "daily"}), indent=2, ensure_ascii=False))
    else:
        print(json.dumps(_clean({k: report[k] for k in ("window", "universe")}), indent=2, ensure_ascii=False))
    logger.info("yazıldı: %s", out / name)
    return 0


def _clean(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_clean(v) for v in value]
    if isinstance(value, (np.floating, float)):
        return None if not np.isfinite(value) else float(value)
    if isinstance(value, np.integer):
        return int(value)
    if isinstance(value, np.bool_):
        return bool(value)
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    return value


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--stage", required=True, choices=("snapshot", "preflight", "measure"))
    parser.add_argument("--archive", default=str(ARCHIVE_DIR))
    parser.add_argument("--pins", default=str(PINS_DIR))
    parser.add_argument("--out-dir", default="backtests/funding_carry")
    parser.add_argument("--run", default=None, help="snapshot koşusunun kimliği (manifest'e yazılır)")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    try:
        args = _parse_args(argv)
    except SystemExit as exc:
        return int(exc.code or 0) if exc.code in (0, None) else 2
    config = load_config()
    if args.stage == "snapshot":
        try:
            return run_snapshot(args, config)
        except DataGateError as exc:
            logger.error("VERİ KAPISI: %s", exc)
            return 3
    return run_analysis(args, config, measure=args.stage == "measure")


if __name__ == "__main__":
    sys.exit(main())

"""OKX fonlama geçmişinin KENDİ arşivimiz. ALTYAPI — ölçümün parçası DEĞİL.

Neden var: `/api/v5/public/funding-rate-history` ~3 aylık KAYAN bir pencere tutuyor
(karar 50; `scripts/probe_funding_depth.py` ölçtü). Bugün başlayan günlük bir arşiv ilk
koşuda o pencereyi (~3 ay) doldurur, sonra her gün birikir — pencereden düşen kayıt
bir daha hiçbir yoldan gelmez, bu yüzden biriktirmenin tek zamanı ŞİMDİDİR.

Ne DEĞİLDİR:
- `core/funding.py` bu arşivi OKUMAZ ve bu betik ona dokunmaz. Arşivi bir ölçümde
  kullanmak ayrı bir karardır (karar 50: damga bazlı tutarlılık kanıtı geçmeden hiçbir
  arşiv kullanılmaz). Buradaki iş yalnızca veriyi KAYBETMEMEKTİR.
- Rapor bir ORAN basmaz: yalnızca sayım ve damga (eklenen satır, en eski/en yeni). Oran
  dosyaya yazılır ama log'a düşmez — log'a bakmak eşik seçimini kirletmesin
  (docs/backtest.md > 7; `probe_funding_*` ile aynı disiplin).

Dosya sözleşmesi (`data/funding_archive/<SYMBOL>.csv`):
- **APPEND-ONLY.** Mevcut bir satır hiçbir gerekçeyle değişmez, silinmez, yeniden
  sıralanmaz: yeni satırlar dosyanın SONUNA eklenir (dosya sırası = varış sırası; okuyan
  taraf `funding_time_ms`e göre sıralar). Eski içerik yeni dosyanın ÖNEKİ olarak kalır
  (test: `tests/test_archive_funding.py`).
- Kimlik `(inst_id, funding_time_ms)`dir; aynı damga ikinci kez eklenmez.
- **Aynı damgada FARKLI oran gelirse üzerine YAZILMAZ.** Arşivdeki satır kalır, gözlem
  `_conflicts.csv`ye (o da append-only) düşer. Üzerine yazmak "borsa geçmişi sonradan
  değiştirdi" olgusunu silerdi — ve karar 50'nin C kapısı tam olarak o olguyu sorar.
  Aynı çakışma her gün yeniden raporlanmaz (`(inst, damga, alan, gözlenen)` tekildir).
- Oranlar borsanın gönderdiği METİN olarak saklanır (float'a çevrilmez): kayan noktalı
  bir gidiş-dönüş, "aynı oran" sorusunu temsil hatasına bağlardı. Karşılaştırma
  `Decimal` iledir, yani `0.0001` ile `0.00010` çakışma DEĞİLDİR.

Evren: `ema` katmanının SABİT 13 sembolü ∪ `base` evreninin güncel sembolleri ∪ arşivde
zaten dosyası olan semboller. Sonuncusu, base'in hacim sıralamasından düşen bir sembolün
arşivinin o gün sessizce durmasını engeller.

Çıkış kodları: 0 = koşu tamam (hiç yeni satır yoksa da 0, ama `::warning` ile İŞARETLİ —
boş bir koşu sessizce yeşil geçmez); 1 = bütünlük hatası (başlık uyuşmazlığı, yanlış
sembol, bozuk dosya sonu), evren çözülemedi ya da hiçbir sembol çekilemedi; 2 = kullanım.
Tek sembolün AĞ hatası `::warning`dır ve koşuyu düşürmez: öteki sembollerin kaydı
kaybolmasın.
"""

from __future__ import annotations

import argparse
import csv
import io
import logging
import os
import sys
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd  # noqa: E402

from core.config import PROJECT_ROOT, get_setting, load_config  # noqa: E402
from core.data import OKXClient, load_universe  # noqa: E402
from core.layers import resolve_layer  # noqa: E402

logger = logging.getLogger("archive_funding")

FUNDING_ENDPOINT = "/api/v5/public/funding-rate-history"
DEFAULT_ARCHIVE_DIR = PROJECT_ROOT / "data" / "funding_archive"
CONFLICTS_FILE = "_conflicts.csv"

COLUMNS = (
    "inst_id",
    "funding_time_ms",
    "funding_time_utc",
    "funding_rate",
    "realized_rate",
    "method",
    "archived_at",
)
CONFLICT_COLUMNS = (
    "inst_id",
    "funding_time_ms",
    "funding_time_utc",
    "field",
    "archived_value",
    "observed_value",
    "detected_at",
)
# Karşılaştırılan alanlar: ikisi de bir ORANDIR. `method` gibi etiketler çakışma sayılmaz.
RATE_FIELDS = ("funding_rate", "realized_rate")

# Güvenlik tavanı: pencere ~3 ay (~283 kayıt ≈ 3 sayfa). İmleç bir gün ilerlemez ya da
# uç `after`ı yok sayarsa döngü sonsuza gitmesin.
MAX_PAGES = 60


class ArchiveIntegrityError(RuntimeError):
    """Arşiv dosyası sözleşmeye uymuyor — ONARILMAZ, koşu kırmızı biter."""


# --------------------------------------------------------------------------- #
# Saf çekirdek: dosya okuma, tekilleştirme, çakışma
# --------------------------------------------------------------------------- #
def _stamp_iso(ms: int) -> str:
    return pd.Timestamp(ms, unit="ms", tz="UTC").strftime("%Y-%m-%dT%H:%M:%SZ")


def normalize_row(raw: Mapping[str, Any], *, symbol: str, archived_at: str) -> dict[str, str]:
    """OKX kaydını arşiv satırına çevirir. Oranlar METİN kalır."""
    inst = str(raw.get("instId", ""))
    if inst != symbol:
        raise ArchiveIntegrityError(f"{symbol} isteğine {inst!r} kaydı döndü")
    ms = int(raw["fundingTime"])
    return {
        "inst_id": inst,
        "funding_time_ms": str(ms),
        "funding_time_utc": _stamp_iso(ms),
        "funding_rate": str(raw.get("fundingRate", "")),
        "realized_rate": str(raw.get("realizedRate", "")),
        "method": str(raw.get("method", "")),
        "archived_at": archived_at,
    }


def same_rate(left: str, right: str) -> bool:
    """`Decimal` eşitliği; biri sayı değilse metin eşitliği (boş ↔ boş aynıdır)."""
    try:
        return Decimal(left) == Decimal(right)
    except (InvalidOperation, ValueError):
        return left.strip() == right.strip()


def _read_csv(path: Path, columns: Sequence[str]) -> list[dict[str, str]]:
    if not path.exists():
        return []
    text = path.read_text(encoding="utf-8")
    if not text:
        raise ArchiveIntegrityError(f"{path.name} boş (başlık bile yok)")
    if not text.endswith("\n"):
        # Yarım yazılmış son satır: onarmak mevcut içeriği değiştirmek olurdu.
        raise ArchiveIntegrityError(f"{path.name} satır sonuyla bitmiyor (yarım yazım?)")
    reader = csv.DictReader(io.StringIO(text))
    if tuple(reader.fieldnames or ()) != tuple(columns):
        raise ArchiveIntegrityError(
            f"{path.name} başlığı {reader.fieldnames} ≠ beklenen {list(columns)}"
        )
    return list(reader)


def read_archive(path: Path) -> list[dict[str, str]]:
    return _read_csv(path, COLUMNS)


@dataclass(frozen=True)
class MergeResult:
    new_rows: list[dict[str, str]]
    conflicts: list[dict[str, str]]
    duplicates: int  # arşivde zaten olan ve oranı AYNI gelen kayıtlar


def merge(
    existing: Sequence[Mapping[str, str]],
    fetched: Iterable[Mapping[str, str]],
    *,
    reported_conflicts: Iterable[Mapping[str, str]] = (),
    detected_at: str,
) -> MergeResult:
    """Çekilen kayıtları arşive göre ayırır: YENİ / AYNI / ÇAKIŞAN.

    Mevcut satırlara dokunmaz — yalnızca eklenecekleri döndürür. Aynı çekimin içinde
    aynı damga iki kez gelirse ilki "arşivdeki" sayılır, ikincisi ona karşı sınanır.
    """
    known: dict[tuple[str, str], Mapping[str, str]] = {
        (row["inst_id"], row["funding_time_ms"]): row for row in existing
    }
    seen_conflicts = {
        (c["inst_id"], c["funding_time_ms"], c["field"], c["observed_value"])
        for c in reported_conflicts
    }
    new_rows: list[dict[str, str]] = []
    conflicts: list[dict[str, str]] = []
    duplicates = 0

    for row in fetched:
        key = (row["inst_id"], row["funding_time_ms"])
        archived = known.get(key)
        if archived is None:
            fresh = dict(row)
            known[key] = fresh
            new_rows.append(fresh)
            continue
        clashed = False
        for name in RATE_FIELDS:
            if same_rate(archived[name], row[name]):
                continue
            clashed = True
            ident = (row["inst_id"], row["funding_time_ms"], name, row[name])
            if ident in seen_conflicts:
                continue
            seen_conflicts.add(ident)
            conflicts.append(
                {
                    "inst_id": row["inst_id"],
                    "funding_time_ms": row["funding_time_ms"],
                    "funding_time_utc": row["funding_time_utc"],
                    "field": name,
                    "archived_value": archived[name],
                    "observed_value": row[name],
                    "detected_at": detected_at,
                }
            )
        if not clashed:
            duplicates += 1

    new_rows.sort(key=lambda r: int(r["funding_time_ms"]))
    return MergeResult(new_rows=new_rows, conflicts=conflicts, duplicates=duplicates)


def append_rows(path: Path, rows: Sequence[Mapping[str, str]], columns: Sequence[str]) -> None:
    """Dosyanın SONUNA ekler; dosya yoksa önce başlık. Asla yeniden yazmaz."""
    if not rows:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    fresh = not path.exists()
    with path.open("a", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(columns), lineterminator="\n")
        if fresh:
            writer.writeheader()
        for row in rows:
            writer.writerow({name: row[name] for name in columns})


# --------------------------------------------------------------------------- #
# Ağ: pencerenin tamamını sayfala
# --------------------------------------------------------------------------- #
def fetch_window(client: Any, symbol: str, *, limit: int) -> list[dict[str, Any]]:
    """Uç noktanın tuttuğu pencerenin TAMAMI, ham kayıtlar.

    `scripts/measure_funding.py::fetch_history` kullanılmaz: o oranı float'a çevirir ve
    bir pencereye kırpar; arşiv borsanın metnini olduğu gibi ister. Pencerenin tamamı
    her gün yeniden çekilir (~3 sayfa): örtüşen günler çakışma denetiminin kendisidir.
    """
    rows: list[dict[str, Any]] = []
    cursor: int | None = None
    for _ in range(MAX_PAGES):
        params = {"instId": symbol, "limit": str(limit)}
        if cursor is not None:
            params["after"] = str(cursor)
        page = client.get(FUNDING_ENDPOINT, params)
        if not page:
            return rows
        rows.extend(page)
        oldest = min(int(item["fundingTime"]) for item in page)
        if cursor is not None and oldest >= cursor:
            return rows  # imleç ilerlemedi
        cursor = oldest
        if len(page) < limit:
            return rows
    logger.warning("%s: %d sayfa tavanına ulaşıldı", symbol, MAX_PAGES)
    return rows


# --------------------------------------------------------------------------- #
# Koşu
# --------------------------------------------------------------------------- #
@dataclass
class SymbolReport:
    symbol: str
    added: int = 0
    duplicates: int = 0
    conflicts: int = 0
    total: int = 0
    oldest: str = "—"
    newest: str = "—"
    error: str = ""
    integrity: bool = False


@dataclass
class RunReport:
    symbols: list[SymbolReport] = field(default_factory=list)
    universe_error: str = ""

    @property
    def added(self) -> int:
        return sum(item.added for item in self.symbols)

    @property
    def fetched_any(self) -> bool:
        return any(not item.error for item in self.symbols)

    def exit_code(self) -> int:
        if self.universe_error or any(item.integrity for item in self.symbols):
            return 1
        if not self.fetched_any:
            return 1
        return 0


def archive_symbol(
    client: Any,
    symbol: str,
    *,
    archive_dir: Path,
    detected_at: str,
    limit: int,
) -> SymbolReport:
    report = SymbolReport(symbol=symbol)
    path = archive_dir / f"{symbol}.csv"
    conflicts_path = archive_dir / CONFLICTS_FILE
    try:
        existing = read_archive(path)
        reported = _read_csv(conflicts_path, CONFLICT_COLUMNS)
    except ArchiveIntegrityError as exc:
        report.error, report.integrity = str(exc), True
        return report
    try:
        raw = fetch_window(client, symbol, limit=limit)
    except Exception as exc:  # tek sembolün ağ hatası öteki sembollerin kaydını düşürmez
        report.error = f"çekilemedi: {exc}"
        _fill_span(report, existing)
        return report
    try:
        fetched = [normalize_row(item, symbol=symbol, archived_at=detected_at) for item in raw]
    except (ArchiveIntegrityError, KeyError, ValueError) as exc:
        report.error, report.integrity = f"yanıt sözleşmeye uymuyor: {exc}", True
        _fill_span(report, existing)
        return report

    result = merge(existing, fetched, reported_conflicts=reported, detected_at=detected_at)
    append_rows(path, result.new_rows, COLUMNS)
    append_rows(conflicts_path, result.conflicts, CONFLICT_COLUMNS)

    report.added = len(result.new_rows)
    report.duplicates = result.duplicates
    report.conflicts = len(result.conflicts)
    _fill_span(report, [*existing, *result.new_rows])
    return report


def _fill_span(report: SymbolReport, rows: Sequence[Mapping[str, str]]) -> None:
    report.total = len(rows)
    if rows:
        stamps = [int(row["funding_time_ms"]) for row in rows]
        report.oldest = _stamp_iso(min(stamps))
        report.newest = _stamp_iso(max(stamps))


def archived_symbols(archive_dir: Path) -> list[str]:
    if not archive_dir.is_dir():
        return []
    return sorted(p.stem for p in archive_dir.glob("*.csv") if not p.name.startswith("_"))


def resolve_symbols(config: dict[str, Any], archive_dir: Path) -> tuple[list[str], str]:
    """ema'nın sabit evreni ∪ base'in güncel evreni ∪ arşivdekiler; sıralı, tekil."""
    symbols: set[str] = set(resolve_layer(config, "ema").symbols or [])
    error = ""
    base = resolve_layer(config, "base")
    try:
        symbols.update(base.symbols or load_universe(base.config))
    except Exception as exc:  # base çözülemese de ema sembolleri arşivlenir
        error = f"base evreni çözülemedi: {exc}"
    symbols.update(archived_symbols(archive_dir))
    return sorted(symbols), error


def _cell(text: str, width: int = 160) -> str:
    text = text.replace("|", "/").replace("\n", " ")
    return text if len(text) <= width else text[: width - 1] + "…"


def format_report(report: RunReport) -> list[str]:
    lines = [
        "| sembol | eklenen | aynı | çakışma | toplam | en eski | en yeni | not |",
        "|---|---:|---:|---:|---:|---|---|---|",
    ]
    for item in report.symbols:
        lines.append(
            f"| {item.symbol} | {item.added} | {item.duplicates} | {item.conflicts} "
            f"| {item.total} | {item.oldest} | {item.newest} | {_cell(item.error)} |"
        )
    lines.append(
        f"| **toplam** | **{report.added}** | | "
        f"**{sum(i.conflicts for i in report.symbols)}** | | | | |"
    )
    return lines


def annotations(report: RunReport) -> list[str]:
    """GitHub annotation satırları. Boş koşu YEŞİL ama İŞARETLİ geçer."""
    out: list[str] = []
    if report.universe_error:
        out.append(f"::error title=Fonlama arşivi::{report.universe_error}")
    for item in report.symbols:
        if item.integrity:
            out.append(f"::error title=Fonlama arşivi bütünlük::{item.symbol}: {item.error}")
        elif item.error:
            out.append(f"::warning title=Fonlama arşivi::{item.symbol}: {item.error}")
        if item.conflicts:
            out.append(
                f"::warning title=Fonlama çakışması::{item.symbol}: {item.conflicts} damgada "
                f"arşivdekinden FARKLI oran geldi — üzerine yazılmadı, {CONFLICTS_FILE}'ye düştü"
            )
    if report.fetched_any and report.added == 0:
        out.append(
            "::warning title=Fonlama arşivi boş geçti::hiçbir sembol için yeni satır "
            "gelmedi — uç nokta donmuş ya da koşu aynı gün tekrarlanmış olabilir"
        )
    return out


def run(
    client: Any,
    symbols: Sequence[str],
    *,
    archive_dir: Path,
    now: pd.Timestamp,
    limit: int,
) -> RunReport:
    detected_at = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    report = RunReport()
    for symbol in symbols:
        item = archive_symbol(
            client, symbol, archive_dir=archive_dir, detected_at=detected_at, limit=limit
        )
        logger.info(
            "%-22s +%-4d (toplam %d, %s → %s)%s",
            symbol,
            item.added,
            item.total,
            item.oldest,
            item.newest,
            f" [{item.error}]" if item.error else "",
        )
        report.symbols.append(item)
    return report


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    config = load_config(args.config)
    archive_dir = Path(args.archive_dir)
    if args.symbols:
        symbols, universe_error = sorted(set(args.symbols)), ""
    else:
        symbols, universe_error = resolve_symbols(config, archive_dir)
    if not symbols:
        logger.error("arşivlenecek sembol yok")
        return 1

    base_config = resolve_layer(config, "base").config
    client = OKXClient.from_config(base_config)
    limit = int(get_setting(base_config, "exchange.funding_limit"))
    report = run(
        client,
        symbols,
        archive_dir=archive_dir,
        now=pd.Timestamp.now(tz="UTC"),
        limit=limit,
    )
    report.universe_error = universe_error

    table = format_report(report)
    print("\n".join(table))
    for line in annotations(report):
        print(line)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as handle:
            handle.write("### Fonlama arşivi\n\n" + "\n".join(table) + "\n")
            if report.fetched_any and report.added == 0:
                handle.write("\n⚠️ Hiçbir sembol için yeni satır gelmedi.\n")
    return report.exit_code()


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--config", default=None, help="config.yaml yolu (varsayılan: depo kökü)")
    parser.add_argument("--archive-dir", default=str(DEFAULT_ARCHIVE_DIR))
    parser.add_argument(
        "--symbols", nargs="*", default=None, help="evreni ez (varsayılan: ema ∪ base ∪ arşiv)"
    )
    return parser.parse_args(argv)


if __name__ == "__main__":
    sys.exit(main())

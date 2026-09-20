"""Fonlama ARŞİVİNİN ŞEMA PROBE'u. SALT OKUNUR, ölçümün parçası DEĞİL.

Cevapladığı soru tek: *elimizdeki URL ŞEMASI tutuyor mu — ve tutmuyorsa GERÇEK nedir?*
Yani bu bir dağılım ölçümü değil, `scripts/probe_funding_depth.py`nin açtığı yolun bir
sonraki adımıdır: A-2 geçti (portal fonlama geçmişi sunuyor, docs/backtest.md > 6f) ve
şimdi o veri kümesinin ERİŞİM YOLU ile BİÇİMİ sabitlenecek.

**Şema bir GÖZLEMDİR, bir varsayım değil.** Betik onu SIFIRDAN ARAMAZ, DOĞRULAR: verilen
şablonu gerçek isteklerle sınar ve sonucu mekanik olarak sınıflandırır. Bu, kör aramadan
hem hızlı hem denetlenebilir — ama tek şartla:

⚠ **ŞEMAYA UYDURMA YOKTUR.** Gözlem ile şema çelişirse rapor GERÇEĞİ yazar: dönen HTTP
durumu, içerik tipi, yönlendirme hedefi. Betik "şema tutmalıydı" diye ikinci bir tahmin
üretmez ve bir eşleşme İMA ETMEZ; `--discover` ile yapılan şey de arama değil, BAŞARISIZ
şablonun komşularını raporlamaktır (sınırlı, sayılı, ve raporda ayrı bir başlık altında).

**Bu bir eşik seçmez, bir dağılım göstermez, bir tez sınamaz.** Getiri, R, PnL
hesaplanmaz ve `core/portfolio.py`, `core/metrics.py`, `core/ledger.py`, `strategies/*`
modülleri import EDİLMEZ (test: `tests/test_probe_funding_archive.py`).

**Rapor YALNIZCA meta veri taşır: HTTP durumu, içerik tipi, dosya boyu, arşiv üyeleri,
KOLON ADLARI, zaman damgası biçimi ve damga aralığı. Hiçbir yerde bir fonlama ORANI
yazılmaz.** Örnek satırlar da maskelenerek basılır ve maskeleme KARA liste değil BEYAZ
listedir (`METADATA_COLUMNS`): tanınmayan bir kolon gizlenir, gösterilmez. Ters kural
(oran gibi görünen kolonu gizle) bir gün beklenmedik adlı bir kolonu sızdırırdı ve
sızacak şey tam olarak eşiğin görmemesi gereken sayıdır (docs/backtest.md > 7).

Damga okumanın SERBEST olmasının gerekçesi `probe_funding_depth.py`nin aynısıdır: damga
saymak o pencereye BAKMAK değildir. Izgara doğrulaması (fonlama aralığı 8 saat mi)
tanımı gereği damga ister ve başka türlü yapılamaz.

**Salt okunur.** Deftere yazmaz, `config.yaml`a dokunmaz, `docs/`a bir şey koymaz,
`data/cache/`e yazmaz, indirdiği örneği diske BIRAKMAZ (bellekte açar), hiçbir modelin
davranışını değiştirmez (kural 1/2/3/7). Çıktısı yalnızca log'dur.

**Neden `probe_funding_depth.py`ye bir bayrak değil.** O betiğin sorusu "uç nokta nereye
kadar veriyor"dur ve cevabı ÖLÇÜLDÜ (karar 50: ~3 aylık kayan pencere). Bu betiğin sorusu
"portal veri kümesinin yolu ve biçimi nedir"dir — farklı bir kaynağa, farklı bir protokole
(REST JSON değil dosya indirme) sorulur. İkisini tek betiğe koymak, kapanmış bir soruyu
her koşuda yeniden koşturmak olurdu.

**DÖNEM SINIRLARI HAKKINDA BİR AYRIM (karıştırılırsa ölçüm bozulur):**
`scripts/backtest_ema.py::PERIOD_A_START` (2022-01-01) `ema_trend`in BACKTEST penceresidir
ve bu betik ona DOKUNMAZ. Burada sınanan şey ARŞİVİN KAPSAMIDIR — veri kümesinin kendi
başlangıcı (iddia: 2022-03) — ve ikisi ayrı şeylerdir: biri bir modelin ölçüldüğü pencere,
öteki bir veri kaynağının nereden başladığı. Arşiv kapsamı pencereyi DARALTIR ama
pencerenin TANIMINI değiştirmez.

Kullanım (depo kökünden):
    python scripts/probe_funding_archive.py --url-template "<şema>" --granularity monthly
    python scripts/probe_funding_archive.py --url-template "<şema>" --sample-date 2022-03
"""

from __future__ import annotations

import argparse
import csv
import io
import logging
import re
import sys
import zipfile
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd  # noqa: E402

from core.config import load_config  # noqa: E402
from core.layers import resolve_layer  # noqa: E402

logger = logging.getLogger("probe_funding_archive")

# --------------------------------------------------------------------------- #
# Sabitler — hepsi ŞEMA hakkında, hiçbiri VERİ hakkında değil
# --------------------------------------------------------------------------- #

# Portalın iddia ettiği kapsam başlangıcı (docs/backtest.md > 6f). Bu bir GÖZLEMİN
# kaydıdır, bir varsayım değil — ve probe tam olarak onu SINAR: başlangıçtan bir önceki
# aya yapılan istek de dosya döndürürse iddia yanlıştır ve rapor öyle yazar.
ARCHIVE_CLAIMED_START = "2022-03"

# Sembol adlandırması UYDURULMAZ, SINANIR: portal hangi yazımı kullanıyorsa o çıkar.
# İsimler mekanik üretilir ve hepsi aynı istekle denenir — "bence böyledir" yok.
SYMBOL_STYLES: dict[str, str] = {
    "instid": "BTC-USDT-SWAP biçimi (OKX REST'in kullandığı)",
    "compact": "BTCUSDT biçimi",
    "dash": "BTC-USDT biçimi",
    "base": "BTC biçimi",
}

# Örnek satırlarda GÖSTERİLEBİLEN kolonlar. BEYAZ liste olmasının gerekçesi modül
# başlığındadır: tanınmayan kolon GİZLENİR.
METADATA_COLUMNS: tuple[str, ...] = (
    "timestamp", "time", "fundingtime", "funding_time", "date", "datetime",
    "instrument_id", "instid", "inst_id", "symbol", "contract", "pair",
)
MASK = "‹gizlendi›"

# Bir dosyanın GERÇEKTEN dosya olup olmadığının mekanik ölçütü. HTML dönen bir 200
# yanıtı bir dosya DEĞİLDİR (giriş sayfası ya da SPA kabuğu olabilir) ve "indirdik"
# diye okunması bu probe'un kapatmak için var olduğu hatadır.
FILE_CONTENT_HINTS: tuple[str, ...] = (
    "application/zip", "application/x-zip", "application/octet-stream",
    "application/gzip", "text/csv", "application/csv",
)
PAGE_CONTENT_HINTS: tuple[str, ...] = ("text/html", "application/xhtml")
AUTH_STATUSES: frozenset[int] = frozenset({401, 403})

REQUEST_TIMEOUT_SEC = 30.0
SAMPLE_MAX_BYTES = 64 * 1024 * 1024   # bir aylık fonlama dosyası bunun çok altındadır
SAMPLE_ROWS = 5

_PLACEHOLDER = re.compile(r"\{([a-zA-Z0-9_\-]+)\}")


# --------------------------------------------------------------------------- #
# Şema işleme — SAF, ağsız, test edilebilir
# --------------------------------------------------------------------------- #
def symbol_variants(inst_id: str) -> dict[str, str]:
    """`BTC-USDT-SWAP` -> her adlandırma stilindeki yazımı. Tahmin YOK, dönüşüm VAR."""
    parts = inst_id.split("-")
    base = parts[0]
    quote = parts[1] if len(parts) > 1 else ""
    return {
        "instid": inst_id,
        "compact": f"{base}{quote}",
        "dash": f"{base}-{quote}" if quote else base,
        "base": base,
    }


def render_url(template: str, *, symbol: str, date: pd.Timestamp) -> str:
    """Şablonu tek bir (sembol, tarih) için açar.

    TANINMAYAN yer tutucu sessizce bırakılmaz, `ValueError` olur: yarı açılmış bir URL
    "404 geldi, demek ki şema tutmuyor" diye okunurdu — oysa hata bizdedir.
    """
    values = {
        "symbol": symbol,
        "yyyy": f"{date.year:04d}",
        "mm": f"{date.month:02d}",
        "dd": f"{date.day:02d}",
        "yyyymm": f"{date.year:04d}{date.month:02d}",
        "yyyymmdd": f"{date.year:04d}{date.month:02d}{date.day:02d}",
        "yyyy-mm": f"{date.year:04d}-{date.month:02d}",
        "yyyy-mm-dd": f"{date.year:04d}-{date.month:02d}-{date.day:02d}",
    }
    unknown = sorted({m for m in _PLACEHOLDER.findall(template) if m not in values})
    if unknown:
        raise ValueError(
            f"şablonda tanınmayan yer tutucu: {', '.join(unknown)} "
            f"(tanınanlar: {', '.join(sorted(values))})"
        )
    for key, value in values.items():
        template = template.replace("{" + key + "}", value)
    return template


def classify_response(
    *, status: int | None, content_type: str, size: int | None, error: str
) -> str:
    """Bir isteğin MEKANİK sınıfı. 'Dosya geldi' iddiası tek bir yerde kurulur."""
    if error:
        return "hata"
    if status in AUTH_STATUSES:
        return "giriş-gerekli"
    if status == 404:
        return "yok"
    if status != 200:
        return "hata"
    lowered = (content_type or "").lower()
    if any(hint in lowered for hint in PAGE_CONTENT_HINTS):
        # 200 + HTML: bir dosya DEĞİL. Portal SPA'sı ya da giriş duvarı olabilir.
        return "sayfa-döndü"
    if any(hint in lowered for hint in FILE_CONTENT_HINTS):
        return "dosya"
    if size is not None and size > 0 and not lowered:
        # İçerik tipi yoksa boyut tek ipucudur; "dosya" demiyoruz, belirsiz diyoruz.
        return "belirsiz-içerik"
    return "belirsiz-içerik"


def classify_schema_match(verdicts: Sequence[str]) -> str:
    """Şema ↔ gerçek eşleşmesinin MEKANİK yargısı.

    Ölçüt "dosya geldi mi"dir; "istek başarılı mı" DEĞİL. 200 dönen bir HTML sayfası
    şemayı doğrulamaz — bu ayrım betiğin varlık sebebidir.
    """
    if not verdicts:
        return "belirsiz"
    if any(v == "hata" for v in verdicts):
        return "belirsiz"
    files = sum(1 for v in verdicts if v == "dosya")
    if files == len(verdicts):
        return "uyuyor"
    if files > 0:
        return "kısmen"
    if any(v == "giriş-gerekli" for v in verdicts):
        return "uymuyor (giriş gerekiyor)"
    if any(v == "sayfa-döndü" for v in verdicts):
        return "uymuyor (dosya değil sayfa döndü)"
    return "uymuyor"


def detect_stamp_format(value: str) -> str:
    """Zaman damgası BİÇİMİ — değeri değil, biçimi raporlanır."""
    text = str(value).strip()
    if text.isdigit():
        digits = len(text)
        if digits >= 13:
            return "epoch_ms"
        if digits >= 10:
            return "epoch_s"
        return "belirsiz-sayı"
    try:
        pd.Timestamp(text)
    except Exception:
        return "bilinmiyor"
    return "iso"


def parse_stamp(value: str, fmt: str) -> pd.Timestamp | None:
    try:
        if fmt == "epoch_ms":
            return pd.Timestamp(int(value), unit="ms", tz="UTC")
        if fmt == "epoch_s":
            return pd.Timestamp(int(value), unit="s", tz="UTC")
        if fmt == "iso":
            stamp = pd.Timestamp(str(value))
            return stamp.tz_localize("UTC") if stamp.tzinfo is None else stamp.tz_convert("UTC")
    except Exception:
        return None
    return None


def modal_interval(stamps: Sequence[pd.Timestamp]) -> pd.Timedelta | None:
    """Ardışık damgalar arasındaki EN SIK fark. Izgara doğrulamasının tek ölçütü."""
    ordered = sorted(stamps)
    gaps = [b - a for a, b in zip(ordered, ordered[1:]) if b > a]
    if not gaps:
        return None
    return Counter(gaps).most_common(1)[0][0]


def mask_row(columns: Sequence[str], values: Sequence[str]) -> list[str]:
    """Beyaz listede OLMAYAN her hücre maskelenir (modül başlığı)."""
    out: list[str] = []
    for index, value in enumerate(values):
        name = columns[index].strip().lower() if index < len(columns) else ""
        out.append(str(value) if name in METADATA_COLUMNS else MASK)
    return out


def _stamp_column(columns: Sequence[str]) -> int | None:
    for index, name in enumerate(columns):
        if name.strip().lower() in METADATA_COLUMNS and "symbol" not in name.lower():
            lowered = name.strip().lower()
            if any(k in lowered for k in ("time", "date", "stamp")):
                return index
    return None


# --------------------------------------------------------------------------- #
# Ağ katmanı — yalnızca burada istek yapılır
# --------------------------------------------------------------------------- #
@dataclass(frozen=True, kw_only=True)
class UrlProbe:
    label: str
    url: str
    status: int | None
    content_type: str
    size: int | None
    final_url: str
    error: str

    @property
    def verdict(self) -> str:
        return classify_response(
            status=self.status, content_type=self.content_type, size=self.size, error=self.error
        )


def probe_url(url: str, *, label: str, timeout: float = REQUEST_TIMEOUT_SEC) -> UrlProbe:
    """Tek bir URL'in VARLIĞINI yoklar; içeriği İNDİRMEZ (HEAD, gerekirse kısa GET)."""
    import requests  # yerel import: saf mantık ağsız test edilebilir kalsın

    headers = {"User-Agent": "crypto-paper-bot/archive-probe (read-only)"}
    try:
        response = requests.head(url, timeout=timeout, allow_redirects=True, headers=headers)
        # Bazı CDN'ler HEAD'i desteklemez; 405/501'de tek seferlik akışlı GET'e düşülür
        # ve gövde OKUNMADAN kapatılır — "indirmeden yoklamak" sözü korunur.
        if response.status_code in (405, 501):
            with requests.get(
                url, timeout=timeout, allow_redirects=True, headers=headers, stream=True
            ) as streamed:
                response = streamed
                length = streamed.headers.get("content-length")
                return UrlProbe(
                    label=label, url=url, status=int(streamed.status_code),
                    content_type=str(streamed.headers.get("content-type", "")),
                    size=int(length) if length and length.isdigit() else None,
                    final_url=str(streamed.url), error="",
                )
    except Exception as exc:
        return UrlProbe(
            label=label, url=url, status=None, content_type="", size=None,
            final_url="", error=f"{type(exc).__name__}: {exc}",
        )

    length = response.headers.get("content-length")
    return UrlProbe(
        label=label,
        url=url,
        status=int(response.status_code),
        content_type=str(response.headers.get("content-type", "")),
        size=int(length) if length and length.isdigit() else None,
        final_url=str(response.url),
        error="",
    )


# --------------------------------------------------------------------------- #
# Örnek dosyanın YAPISI — kolon ADLARI, damga BİÇİMİ, ızgara. Oran YOK.
# --------------------------------------------------------------------------- #
@dataclass(frozen=True, kw_only=True)
class SampleReport:
    url: str
    status: int | None
    content_type: str
    byte_size: int
    container: str
    members: tuple[str, ...]
    columns: tuple[str, ...]
    rows: int
    stamp_column: str
    stamp_format: str
    stamp_first: pd.Timestamp | None
    stamp_last: pd.Timestamp | None
    interval: pd.Timedelta | None
    preview: tuple[tuple[str, ...], ...] = field(default=())
    error: str = ""


def _empty_sample(url: str) -> SampleReport:
    return SampleReport(
        url=url, status=None, content_type="", byte_size=0, container="", members=(),
        columns=(), rows=0, stamp_column="", stamp_format="", stamp_first=None,
        stamp_last=None, interval=None,
    )


def read_sample(url: str, *, timeout: float = REQUEST_TIMEOUT_SEC) -> SampleReport:
    """Tek bir dosyayı BELLEKTE açar ve YAPISINI raporlar. Diske hiçbir şey yazılmaz.

    Ağ katmanı burada biter: çözümlemenin tamamı `describe_payload`dadır ve o SAF'tır
    (test: `tests/test_probe_funding_archive.py`). Ayrım, maskeleme sözünün ağ olmadan
    sınanabilmesi içindir — söz koda yazılıp teste yazılmazsa bir gün sessizce düşer.
    """
    import requests

    try:
        response = requests.get(
            url, timeout=timeout, allow_redirects=True,
            headers={"User-Agent": "crypto-paper-bot/archive-probe (read-only)"},
        )
    except Exception as exc:
        return SampleReport(
            **{**_empty_sample(url).__dict__, "error": f"{type(exc).__name__}: {exc}"}
        )

    return describe_payload(
        response.content or b"",
        url=url,
        status=int(response.status_code),
        content_type=str(response.headers.get("content-type", "")),
    )


def describe_payload(
    payload: bytes, *, url: str, status: int | None, content_type: str
) -> SampleReport:
    """Ham gövdenin YAPISINI çıkarır: kap, üyeler, kolon ADLARI, damga biçimi, ızgara.

    SAF: ağ yok, disk yok. Oran değeri hiçbir dönüş alanına girmez; örnek satırlar
    `mask_row` ile beyaz listeden geçer.
    """
    base = {**_empty_sample(url).__dict__, "status": status, "content_type": content_type,
            "byte_size": len(payload)}

    verdict = classify_response(
        status=status, content_type=content_type, size=len(payload), error=""
    )
    if verdict != "dosya":
        return SampleReport(**{**base, "error": f"dosya gelmedi ({verdict})"})
    if len(payload) > SAMPLE_MAX_BYTES:
        return SampleReport(**{**base, "error": f"dosya {len(payload)} bayt, tavan aşıldı"})

    members: tuple[str, ...] = ()
    container = "csv"
    text_bytes = payload
    if payload[:2] == b"PK":
        container = "zip"
        try:
            with zipfile.ZipFile(io.BytesIO(payload)) as archive:
                members = tuple(archive.namelist())
                if not members:
                    return SampleReport(**{**base, "container": container,
                                           "error": "zip boş"})
                text_bytes = archive.read(members[0])
        except Exception as exc:
            return SampleReport(**{**base, "container": container,
                                   "error": f"zip açılamadı: {type(exc).__name__}: {exc}"})

    try:
        text = text_bytes.decode("utf-8-sig", errors="replace")
    except Exception as exc:
        return SampleReport(**{**base, "container": container, "members": members,
                               "error": f"metin çözülemedi: {exc}"})

    reader = csv.reader(io.StringIO(text))
    try:
        header = next(reader)
    except StopIteration:
        return SampleReport(**{**base, "container": container, "members": members,
                               "error": "dosya boş"})
    columns = tuple(h.strip() for h in header)

    index = _stamp_column(columns)
    rows = 0
    stamps: list[pd.Timestamp] = []
    preview: list[tuple[str, ...]] = []
    stamp_format = ""
    for values in reader:
        if not values:
            continue
        rows += 1
        if rows <= SAMPLE_ROWS:
            preview.append(tuple(mask_row(columns, values)))
        if index is not None and index < len(values):
            if not stamp_format:
                stamp_format = detect_stamp_format(values[index])
            stamp = parse_stamp(values[index], stamp_format)
            if stamp is not None:
                stamps.append(stamp)

    return SampleReport(
        url=url, status=status, content_type=content_type, byte_size=len(payload),
        container=container, members=members, columns=columns, rows=rows,
        stamp_column=columns[index] if index is not None else "(bulunamadı)",
        stamp_format=stamp_format or "(okunamadı)",
        stamp_first=min(stamps) if stamps else None,
        stamp_last=max(stamps) if stamps else None,
        interval=modal_interval(stamps),
        preview=tuple(preview),
        error="",
    )


# --------------------------------------------------------------------------- #
# Rapor
# --------------------------------------------------------------------------- #
def _probe_line(probe: UrlProbe) -> str:
    if probe.error:
        return f"  {probe.label:<28} ULAŞILAMADI — {probe.error}"
    size = f"{probe.size:>12,} bayt" if probe.size is not None else "boy bildirilmedi"
    line = (
        f"  {probe.label:<28} HTTP {probe.status} | {probe.verdict:<16} | {size} | "
        f"{probe.content_type or '—'}"
    )
    if probe.final_url and probe.final_url != probe.url:
        line += f"\n  {'':<28} yönlendirme → {probe.final_url}"
    return line


def format_symbol_style(probes: Sequence[UrlProbe]) -> list[str]:
    lines = [
        "",
        "=" * 78,
        "1) SEMBOL ADLANDIRMASI — hangi yazım tutuyor? (tahmin değil, istek)",
        "=" * 78,
    ]
    lines += [_probe_line(p) for p in probes]
    winners = [p.label for p in probes if p.verdict == "dosya"]
    lines += ["", f"  >>> dosya döndüren yazım: {', '.join(winners) if winners else 'HİÇBİRİ'}"]
    if not winners:
        lines.append("      Hiçbir yazım dosya döndürmedi; aşağıdaki şema yargısı da bunu yazar.")
    return lines


def format_schema(probes: Sequence[UrlProbe], *, template: str, granularity: str) -> list[str]:
    verdict = classify_schema_match([p.verdict for p in probes])
    lines = [
        "",
        "=" * 78,
        "2) ŞEMA DOĞRULAMASI — verilen şablon gerçekte tutuyor mu?",
        "=" * 78,
        f"  şablon      : {template}",
        f"  granülarite : {granularity} (BİLDİRİLEN; aşağıdaki istekler SINAR)",
        "",
    ]
    lines += [_probe_line(p) for p in probes]
    lines += ["", f"  >>> MEKANİK YARGI: şema {verdict}"]
    if verdict.startswith("uymuyor") or verdict == "kısmen":
        lines += [
            "      Rapor GERÇEĞİ yazar; şemaya uydurulmuş bir ikinci tahmin ÜRETİLMEZ.",
            "      Yukarıdaki durum/içerik tipi/yönlendirme satırları ham gözlemdir.",
        ]
    if verdict == "belirsiz":
        lines.append("      En az bir istek hata verdi; 'şema tutmuyor' diye OKUNAMAZ.")
    return lines


def format_coverage(probes: Sequence[UrlProbe], *, claimed_start: str) -> list[str]:
    lines = [
        "",
        "=" * 78,
        f"3) KAPSAM UCU — veri kümesi gerçekten {claimed_start}'te mi başlıyor?",
        "=" * 78,
    ]
    lines += [_probe_line(p) for p in probes]
    available = [p.label for p in probes if p.verdict == "dosya"]
    lines += ["", f"  >>> dosya dönen aylar: {', '.join(available) if available else 'HİÇBİRİ'}"]
    lines += [
        "",
        "  OKUMA NOTU: iddia edilen başlangıçtan ÖNCEKİ bir ay da dosya döndürüyorsa",
        "  kapsam iddiası YANLIŞTIR ve dönem A'nın fiilî başlangıcı (docs/backtest.md",
        "  > 6f) yeniden yazılır. Tersi de geçerli: iddia edilen ay dosya döndürmüyorsa",
        "  kapsam iddia edilenden DAR demektir. Bu satır, bir kaydın ÖLÇÜMLE",
        "  doğrulanmasıdır — ikincil kaynak bu belgede iki kez çürüdü (karar 50).",
    ]
    return lines


def format_sample(sample: SampleReport) -> list[str]:
    lines = ["", "=" * 78, "4) ÖRNEK DOSYA — YAPI (kolon adları, damga biçimi, ızgara)", "=" * 78]
    lines.append(f"  url         : {sample.url}")
    if sample.error:
        lines += [f"  HATA        : {sample.error}", "",
                  "  Yapı raporlanmadı. Bir biçim TAHMİN EDİLMEZ."]
        return lines
    lines += [
        f"  durum       : HTTP {sample.status} | {sample.content_type}",
        f"  boy         : {sample.byte_size:,} bayt | kap: {sample.container}",
    ]
    if sample.members:
        lines.append(f"  arşiv üyesi : {', '.join(sample.members)}")
    lines += [
        f"  satır       : {sample.rows:,}",
        f"  KOLONLAR    : {', '.join(sample.columns) if sample.columns else '—'}",
        f"  damga kolonu: {sample.stamp_column} | biçim: {sample.stamp_format}",
    ]
    if sample.stamp_first is not None and sample.stamp_last is not None:
        lines.append(
            f"  damga aralığı: {sample.stamp_first:%Y-%m-%d %H:%M} → "
            f"{sample.stamp_last:%Y-%m-%d %H:%M} (UTC)"
        )
    else:
        lines.append("  damga aralığı: OKUNAMADI (damga kolonu bulunamadı ya da çözülemedi)")
    if sample.interval is not None:
        grid = "8 saat" if sample.interval == pd.Timedelta(hours=8) else str(sample.interval)
        lines.append(f"  IZGARA      : en sık damga farkı = {grid}")
    else:
        lines.append("  IZGARA      : ölçülemedi (tek damga ya da damga yok)")

    lines += ["", "  örnek satırlar (BEYAZ liste dışındaki her hücre maskeli):"]
    if sample.columns:
        lines.append("    " + " | ".join(sample.columns))
    for row in sample.preview:
        lines.append("    " + " | ".join(row))
    lines += [
        "",
        f"  Maskeleme BEYAZ listedir: yalnızca {', '.join(METADATA_COLUMNS[:6])}… gibi",
        "  META kolonlar gösterilir, tanınmayan kolon GİZLENİR. Gerekçe: burada sızacak",
        "  şey eşiğin görmemesi gereken sayıdır (docs/backtest.md > 7).",
    ]
    return lines


def format_header(*, template: str, symbols: Sequence[str]) -> list[str]:
    return [
        "",
        "#" * 78,
        "# FONLAMA ARŞİVİ ŞEMA PROBE'u — salt okunur, ölçümün parçası DEĞİL",
        "# Dağılım göstermez, eşik önermez, getiri/R/PnL hesaplamaz.",
        "# Rapor meta veri taşır: durum, içerik tipi, boy, KOLON ADLARI, damga biçimi.",
        "# Hiçbir fonlama ORANI yazılmaz; örnek satırlar beyaz listeyle maskelenir.",
        "# ŞEMAYA UYDURMA YOKTUR: gözlem çelişirse GERÇEK raporlanır.",
        f"# Şablon: {template}",
        f"# Sembol: {', '.join(symbols)}",
        "# Ön-kayıt: docs/backtest.md > 6f",
        "#" * 78,
    ]


# --------------------------------------------------------------------------- #
# Giriş noktası
# --------------------------------------------------------------------------- #
def _month(text: str) -> pd.Timestamp:
    stamp = pd.Timestamp(text if len(text) > 7 else f"{text}-01")
    return stamp.tz_localize("UTC") if stamp.tzinfo is None else stamp.tz_convert("UTC")


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    layer = resolve_layer(load_config(args.config), args.layer)
    universe = list(layer.symbols or ())
    symbols = list(args.symbols) if args.symbols else universe
    unknown = [s for s in symbols if universe and s not in universe]
    if unknown:
        logger.error("evren dışı sembol: %s", ", ".join(unknown))
        return 2

    sample_symbol = args.sample_symbol or (symbols[0] if symbols else "BTC-USDT-SWAP")
    if universe and sample_symbol not in universe:
        logger.error("örnek sembol evren dışı: %s", sample_symbol)
        return 2

    try:
        sample_date = _month(args.sample_date)
        claimed = _month(ARCHIVE_CLAIMED_START)
    except Exception as exc:
        logger.error("tarih çözülemedi: %s", exc)
        return 2

    lines = format_header(template=args.url_template, symbols=symbols)

    # 1) Sembol adlandırması: aynı tarih, dört yazım. Hangisi DOSYA döndürüyor?
    variants = symbol_variants(sample_symbol)
    style_probes: list[UrlProbe] = []
    for style, description in SYMBOL_STYLES.items():
        try:
            url = render_url(args.url_template, symbol=variants[style], date=sample_date)
        except ValueError as exc:
            logger.error("şablon hatası: %s", exc)
            return 2
        style_probes.append(probe_url(url, label=f"{style} ({variants[style]})"))
    lines += format_symbol_style(style_probes)

    # Sonraki adımlar dosya döndüren yazımı kullanır; hiçbiri döndürmediyse BİLDİRİLEN
    # yazım kullanılır ve rapor bunu söyler — sessiz bir "en iyisini seç" yoktur.
    winning = next((s for s, p in zip(SYMBOL_STYLES, style_probes) if p.verdict == "dosya"),
                   args.symbol_style)
    rendered_symbol = variants[winning]

    # 2) Şema doğrulaması: birkaç sembol, aynı tarih.
    schema_probes = [
        probe_url(
            render_url(args.url_template, symbol=symbol_variants(s)[winning], date=sample_date),
            label=s,
        )
        for s in symbols[: args.schema_symbols]
    ]
    lines += format_schema(
        schema_probes, template=args.url_template, granularity=args.granularity
    )

    # 3) Kapsam ucu: iddia edilen başlangıç, ondan önceki iki ay, sonraki ay.
    coverage_months = [
        claimed - pd.DateOffset(months=2),
        claimed - pd.DateOffset(months=1),
        claimed,
        claimed + pd.DateOffset(months=1),
    ]
    coverage_probes = [
        probe_url(
            render_url(args.url_template, symbol=rendered_symbol, date=month),
            label=f"{month:%Y-%m}",
        )
        for month in coverage_months
    ]
    lines += format_coverage(coverage_probes, claimed_start=ARCHIVE_CLAIMED_START)

    # 4) Örnek dosya: TEK dosya, bellekte, yapı raporu.
    sample = SampleReport(
        url="", status=None, content_type="", byte_size=0, container="", members=(),
        columns=(), rows=0, stamp_column="", stamp_format="", stamp_first=None,
        stamp_last=None, interval=None, error="--no-sample ile atlandı",
    )
    if not args.no_sample:
        sample = read_sample(
            render_url(args.url_template, symbol=rendered_symbol, date=sample_date)
        )
    lines += format_sample(sample)

    # VERİ KAPISI (karar 51'in aynı gerekçesi): hiçbir istek dosya döndürmediyse rapor
    # okunabilir değildir ve koşu YEŞİL dönemez.
    all_probes = style_probes + schema_probes + coverage_probes
    any_file = any(p.verdict == "dosya" for p in all_probes)
    any_error = any(p.verdict == "hata" for p in all_probes)

    lines += ["", "=" * 78, "TOPLU SONUÇ", "=" * 78]
    if not any_file and any_error:
        lines.append("BELİRSİZ — istekler hata verdi; şema hakkında hiçbir şey söylenemez.")
        exit_code = 1
    elif not any_file:
        lines.append("VERİ KAPISI — hiçbir istek DOSYA döndürmedi. Şema bu hâliyle kullanılamaz.")
        lines.append("Sıradaki adım gerçeğe bakmaktır: yukarıdaki durum/içerik tipi satırları.")
        exit_code = 3
    else:
        lines.append(f"ŞEMA: {classify_schema_match([p.verdict for p in schema_probes])}")
        lines.append(f"SEMBOL YAZIMI: {winning} ({rendered_symbol})")
        exit_code = 0

    lines += [
        "",
        "=" * 78,
        "NOT: Bu rapor bir eşik ÖNERMEZ ve bir dağılım GÖSTERMEZ. Arşiv, docs/backtest.md",
        "> 6f'nin C kapısını (aynı damga → aynı oran) geçmeden hiçbir yerde kullanılamaz",
        "— ne bir dağılım raporunda, ne bir eşik seçiminde, ne bir backtest'te.",
        "=" * 78,
    ]
    print("\n".join(lines))
    return exit_code


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--url-template", required=True,
        help="beklenen URL şeması; yer tutucular: {symbol} {yyyy} {mm} {dd} {yyyymm} "
             "{yyyymmdd} {yyyy-mm} {yyyy-mm-dd}",
    )
    parser.add_argument(
        "--granularity", default="monthly", choices=("daily", "monthly"),
        help="BİLDİRİLEN granülarite; rapor onu sınar, varsaymaz",
    )
    parser.add_argument("--layer", default="ema", help="sembol evreninin alınacağı katman")
    parser.add_argument("--config", default=None)
    parser.add_argument("--symbols", nargs="*", default=None, help="varsayılan: katmanın evreni")
    parser.add_argument("--schema-symbols", type=int, default=3, help="şema kaç sembolde sınanır")
    parser.add_argument("--sample-symbol", default=None)
    parser.add_argument("--sample-date", default=ARCHIVE_CLAIMED_START, help="YYYY-MM ya da tarih")
    parser.add_argument(
        "--symbol-style", default="instid", choices=tuple(SYMBOL_STYLES),
        help="hiçbir yazım dosya döndürmezse kullanılacak varsayılan (rapor bunu söyler)",
    )
    parser.add_argument("--no-sample", action="store_true", help="örnek dosyayı indirme")
    return parser.parse_args(argv)


if __name__ == "__main__":
    raise SystemExit(main())

"""Fonlama ARŞİVİNİN ŞEMA PROBE'u. SALT OKUNUR, ölçümün parçası DEĞİL.

Cevapladığı soru tek: *elimizdeki URL ŞEMASI tutuyor mu — ve tutmuyorsa GERÇEK nedir?*
Yani bu bir dağılım ölçümü değil, `scripts/probe_funding_depth.py`nin açtığı yolun bir
sonraki adımıdır: A-2 geçti (portal fonlama geçmişi sunuyor, docs/backtest.md > 6f) ve
şimdi o veri kümesinin ERİŞİM YOLU ile BİÇİMİ sabitlenecek.

**Şema İKİ ADIMLIDIR ve tek bir şablon DEĞİLDİR:** (1) listeleme uç noktası o ayın
dosya ADLARINI verir, (2) indirme uç noktası o adı kullanır — üstelik ayrı bir host'tan.
Bu yapı modelin içine yazılıdır, bir seçenek olarak değil: tek şablonlu bir model dosya
adını TAHMİN etmek zorunda kalırdı ve o tahmin, sınanan şemanın yerine geçerdi. Ay
yazımı iki adımda ayrışabildiği için (`2022-03` ↔ `202203`) probe İKİSİNİ DE dener ve
hangisinin tuttuğunu raporlar — seçmez, gözlemler.

⚠ **Listelemenin başarısızlığı "şema uymuyor" DEĞİLDİR.** İlk adım düşerse indirme HİÇ
DENENMEZ ve rapor "listeleme başarısız" der. İkisini tek yargıya çökertmek, sınanmamış
bir şemayı "sınandı ve tutmadı" diye kaydetmek olurdu.

**Şema bir GÖZLEMDİR, bir varsayım değil.** Betik onu SIFIRDAN ARAMAZ, DOĞRULAR: verilen
şablonları gerçek isteklerle sınar ve sonucu mekanik olarak sınıflandırır. Bu, kör aramadan
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

**Sembol URL'de OLMAYABİLİR.** Aylık dosya tüm sembolleri taşıyor olabilir; o ihtimal
dört yazım denemesiyle değil, örnek dosyanın SEMBOL KOLONUYLA sınanır (sembol adları
beyaz listededir, yani meta veridir). Şablonda `{symbol}` yoksa yazım denemesi ATLANIR
ve rapor bunu söyler — şablonda yeri olmayan bir değişkeni sınamak, sınanmış gibi
görünen boş bir sonuç üretirdi.

Kullanım (depo kökünden):
    python scripts/probe_funding_archive.py \
        --listing-template "<listeleme url'i, {msg_type}/{month}>" \
        --download-template "<indirme url'i, {yyyymm}/{file}>"
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
LISTING_BODY_HEAD = 400   # başarısız listelemede ham gövdenin raporlanan başı
LISTED_NAMES_SHOWN = 8

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


def month_candidates(date: pd.Timestamp) -> tuple[str, ...]:
    """`{month}`in İKİ yazımı; hangisinin tuttuğu VARSAYILMAZ, ikisi de denenir.

    Gözlem, listeleme ile indirmenin ay biçiminde ayrıştığını söylüyor (biri tireli,
    öteki değil). Hangisinin nerede geçerli olduğunu seçmek bir tahmindir; probe onu
    seçmez, ikisini de sorar ve hangisinin dosya döndürdüğünü RAPORLAR.
    """
    return (f"{date.year:04d}-{date.month:02d}", f"{date.year:04d}{date.month:02d}")


def render_url(
    template: str,
    *,
    symbol: str = "",
    date: pd.Timestamp,
    msg_type: str = "",
    file: str = "",
    month_style: str = "dash",
    now: pd.Timestamp | None = None,
) -> str:
    """Şablonu tek bir (sembol, tarih, ay yazımı, dosya) için açar.

    TANINMAYAN yer tutucu sessizce bırakılmaz, `ValueError` olur: yarı açılmış bir URL
    "404 geldi, demek ki şema tutmuyor" diye okunurdu — oysa hata bizdedir.

    `{file}` DOLDURULMADAN bırakılamaz ve UYDURULMAZ: adı listeleme adımı verir
    (iki adımlı şemanın kendisi). Boş `file` ile `{file}` taşıyan bir şablon açmak
    `ValueError`dır — tahmin edilmiş bir dosya adı, sınanan şeyi sınanmamış bir
    varsayımla karıştırırdı.
    """
    stamp = now if now is not None else pd.Timestamp.now("UTC")
    dash, compact = month_candidates(date)
    values = {
        "symbol": symbol,
        "msg_type": msg_type,
        "file": file,
        "month": dash if month_style == "dash" else compact,
        "epoch_ms": str(int(stamp.value // 1_000_000)),
        "yyyy": f"{date.year:04d}",
        "mm": f"{date.month:02d}",
        "dd": f"{date.day:02d}",
        "yyyymm": compact,
        "yyyymmdd": f"{date.year:04d}{date.month:02d}{date.day:02d}",
        "yyyy-mm": dash,
        "yyyy-mm-dd": f"{date.year:04d}-{date.month:02d}-{date.day:02d}",
    }
    if "{file}" in template and not file:
        raise ValueError(
            "şablon {file} taşıyor ama dosya adı YOK — ad listelemeden gelir, uydurulmaz"
        )
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


DATA_FILE_SUFFIXES: tuple[str, ...] = (".zip", ".csv", ".gz", ".tar", ".tar.gz", ".json")


def extract_file_names(payload: Any) -> tuple[str, ...]:
    """JSON yanıtındaki DOSYA ADLARINI toplar — yapıyı VARSAYMADAN, ağacı gezerek.

    Uç noktanın gövde şeması bir gözlem değil; `data[0].fileList` gibi bir yol
    VARSAYMAK, yanıt başka bir biçimdeyse "dosya yok" demek olurdu ve o, veriyi
    değil bizim varsayımımızı raporlamaktır. Ölçüt adın kendisidir: veri dosyası
    uzantısı taşıyan her string bir adaydır.
    """
    found: list[str] = []

    def walk(node: Any) -> None:
        if isinstance(node, str):
            lowered = node.lower()
            if any(lowered.endswith(suffix) for suffix in DATA_FILE_SUFFIXES):
                found.append(node)
        elif isinstance(node, Mapping):
            for value in node.values():
                walk(value)
        elif isinstance(node, (list, tuple)):
            for value in node:
                walk(value)

    walk(payload)
    # Sıra korunur (listelemenin kendi sırası bir bilgidir), yinelenen ad atılır.
    return tuple(dict.fromkeys(found))


def classify_listing(
    *, status: int | None, content_type: str, error: str, parsed: bool, names: Sequence[str]
) -> str:
    """Listeleme adımının MEKANİK sınıfı.

    ⚠ **Listelemenin başarısızlığı "şema uymuyor" DEĞİLDİR.** İki adımlı bir şemada
    ilk adım düşerse ikinci adım hiç denenmemiştir, yani indirme şeması hakkında
    hiçbir gözlem yoktur. İkisini tek yargıya çökertmek, sınanmamış bir şemayı
    "sınandı ve tutmadı" diye kaydetmek olurdu.
    """
    if error:
        return "hata"
    if status in AUTH_STATUSES:
        return "giriş-gerekli"
    if status == 404:
        return "yok"
    if status != 200:
        return "hata"
    if any(hint in (content_type or "").lower() for hint in PAGE_CONTENT_HINTS):
        return "sayfa-döndü"
    if not parsed:
        return "json-değil"
    if not names:
        return "json-ama-dosya-yok"
    return "dosya-listesi"


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


def _symbol_column(columns: Sequence[str]) -> int | None:
    """Sembol kolonu — BEYAZ listeden, ad üzerinden.

    Gerekçe gözlemin ikinci ihtimalidir: sembol URL'de değil DOSYANIN İÇİNDE olabilir
    (aylık dosya tüm sembolleri taşıyor olabilir). O ihtimal ancak dosyanın sembol
    kolonu okunarak sınanır ve sembol adları zaten meta veridir (beyaz listededir).
    """
    for index, name in enumerate(columns):
        lowered = name.strip().lower()
        if lowered in METADATA_COLUMNS and any(
            k in lowered for k in ("symbol", "instrument", "inst_id", "instid", "contract", "pair")
        ):
            return index
    return None


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
    symbol_column: str = ""
    symbols: tuple[str, ...] = field(default=())
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
    sym_index = _symbol_column(columns)
    rows = 0
    stamps: list[pd.Timestamp] = []
    seen_symbols: dict[str, None] = {}
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
        if sym_index is not None and sym_index < len(values):
            seen_symbols.setdefault(values[sym_index].strip(), None)

    return SampleReport(
        url=url, status=status, content_type=content_type, byte_size=len(payload),
        container=container, members=members, columns=columns, rows=rows,
        stamp_column=columns[index] if index is not None else "(bulunamadı)",
        stamp_format=stamp_format or "(okunamadı)",
        stamp_first=min(stamps) if stamps else None,
        stamp_last=max(stamps) if stamps else None,
        interval=modal_interval(stamps),
        symbol_column=columns[sym_index] if sym_index is not None else "(bulunamadı)",
        symbols=tuple(sorted(seen_symbols)),
        preview=tuple(preview),
        error="",
    )


# --------------------------------------------------------------------------- #
# ADIM 1 — LİSTELEME (dosya adlarının TEK kaynağı)
# --------------------------------------------------------------------------- #
@dataclass(frozen=True, kw_only=True)
class ListingProbe:
    label: str
    url: str
    status: int | None
    content_type: str
    size: int
    parsed: bool
    names: tuple[str, ...]
    body_head: str
    error: str

    @property
    def verdict(self) -> str:
        return classify_listing(
            status=self.status, content_type=self.content_type, error=self.error,
            parsed=self.parsed, names=self.names,
        )


def probe_listing(url: str, *, label: str, timeout: float = REQUEST_TIMEOUT_SEC) -> ListingProbe:
    """Listeleme uç noktasını sorar ve DOSYA ADLARINI çıkarır.

    Gövde JSON değilse ya da dosya adı taşımıyorsa bu bir BAŞARISIZLIKTIR ve öyle
    raporlanır; indirme adımı DENENMEZ (iki adımlı şemanın kuralı). Gövdenin ilk
    satırları rapora düşer — "gerçeği raporla" sözü, ham gözlemi göstermeyi gerektirir.
    """
    import requests

    try:
        response = requests.get(
            url, timeout=timeout, allow_redirects=True,
            headers={
                "User-Agent": "crypto-paper-bot/archive-probe (read-only)",
                "Accept": "application/json, text/plain, */*",
            },
        )
    except Exception as exc:
        return ListingProbe(
            label=label, url=url, status=None, content_type="", size=0, parsed=False,
            names=(), body_head="", error=f"{type(exc).__name__}: {exc}",
        )

    text = getattr(response, "text", "") or ""
    parsed_ok = False
    names: tuple[str, ...] = ()
    try:
        body = response.json()
        parsed_ok = True
        names = extract_file_names(body)
    except Exception:
        parsed_ok = False

    return ListingProbe(
        label=label,
        url=url,
        status=int(response.status_code),
        content_type=str(response.headers.get("content-type", "")),
        size=len(text),
        parsed=parsed_ok,
        names=names,
        body_head=text[:LISTING_BODY_HEAD].replace("\n", " "),
        error="",
    )


# --------------------------------------------------------------------------- #
# Rapor
# --------------------------------------------------------------------------- #
def _probe_line(probe: UrlProbe) -> str:
    if probe.error:
        return f"  {probe.label:<30} ULAŞILAMADI — {probe.error}"
    size = f"{probe.size:>12,} bayt" if probe.size is not None else "boy bildirilmedi"
    line = (
        f"  {probe.label:<30} HTTP {probe.status} | {probe.verdict:<16} | {size} | "
        f"{probe.content_type or '—'}"
    )
    if probe.final_url and probe.final_url != probe.url:
        line += f"\n  {'':<30} yönlendirme → {probe.final_url}"
    return line


def _listing_line(probe: ListingProbe) -> list[str]:
    if probe.error:
        return [f"  {probe.label:<30} ULAŞILAMADI — {probe.error}"]
    lines = [
        f"  {probe.label:<30} HTTP {probe.status} | {probe.verdict:<20} | "
        f"{probe.size:>9,} bayt | {probe.content_type or '—'}"
    ]
    if probe.names:
        shown = ", ".join(probe.names[:LISTED_NAMES_SHOWN])
        more = f" … (+{len(probe.names) - LISTED_NAMES_SHOWN})" if len(probe.names) > LISTED_NAMES_SHOWN else ""
        lines.append(f"  {'':<30} {len(probe.names)} dosya: {shown}{more}")
    elif probe.verdict in ("json-ama-dosya-yok", "json-değil", "sayfa-döndü"):
        lines.append(f"  {'':<30} gövde başı: {probe.body_head or '(boş)'}")
    return lines


def format_listing(probes: Sequence[ListingProbe], *, template: str) -> list[str]:
    lines = [
        "",
        "=" * 78,
        "1) LİSTELEME — dosya adlarının TEK kaynağı (ay yazımı İKİ biçimde denenir)",
        "=" * 78,
        f"  şablon: {template}",
        "",
    ]
    for probe in probes:
        lines += _listing_line(probe)
    winners = [p.label for p in probes if p.verdict == "dosya-listesi"]
    lines += ["", f"  >>> dosya listesi dönen ay yazımı: {', '.join(winners) if winners else 'HİÇBİRİ'}"]
    if not winners:
        lines += [
            "",
            "  ⚠ LİSTELEME BAŞARISIZ — indirme adımı DENENMEDİ ve denenmemelidir.",
            "  Bu 'şema uymuyor' DEĞİLDİR: iki adımlı bir şemada ilk adım düşerse",
            "  ikinci adım hakkında hiçbir gözlem yoktur. Dosya adı listelemeden",
            "  gelir ve UYDURULMAZ; uydurulsaydı sınanan şey şema değil tahminimiz",
            "  olurdu. Yukarıdaki ham gözlem (durum, içerik tipi, gövde başı) bu",
            "  adımın raporudur.",
        ]
    return lines


def format_download(probe: UrlProbe | None, *, template: str, file: str) -> list[str]:
    lines = ["", "=" * 78, "2) İNDİRME — adı LİSTELEMEDEN gelen dosya", "=" * 78,
             f"  şablon: {template}"]
    if probe is None:
        lines += [
            "  ATLANDI — listeleme dosya adı vermedi.",
            "  Bir ad tahmin edip denemek, sınanmamış bir varsayımı sınanmış gibi",
            "  gösterirdi (modül başlığındaki söz).",
        ]
        return lines
    lines += [f"  dosya (listelemeden): {file}", "", _probe_line(probe)]
    lines += ["", f"  >>> MEKANİK YARGI: indirme {classify_schema_match([probe.verdict])}"]
    if probe.verdict != "dosya":
        lines.append("      Rapor GERÇEĞİ yazar; şemaya uydurulmuş bir tahmin ÜRETİLMEZ.")
    return lines


def format_symbol_style(probes: Sequence[UrlProbe], *, in_template: bool) -> list[str]:
    lines = ["", "=" * 78, "3) SEMBOL — URL'de mi, dosyanın İÇİNDE mi?", "=" * 78]
    if not in_template:
        lines += [
            "  Şablonda `{symbol}` YOK → sembol URL'de taşınmıyor.",
            "  Bu, aylık dosyanın TÜM sembolleri taşıdığı ihtimalidir ve dört yazımı",
            "  denemenin konusu değildir; cevap örnek dosyanın sembol kolonundadır",
            "  (aşağıda, 5. bölüm). Yazım denemesi ATLANDI — şablonda yeri olmayan",
            "  bir değişkeni sınamak, sınanmış gibi görünen boş bir sonuç üretirdi.",
        ]
        return lines
    lines += [_probe_line(p) for p in probes]
    winners = [p.label for p in probes if p.verdict == "dosya"]
    lines += ["", f"  >>> dosya döndüren yazım: {', '.join(winners) if winners else 'HİÇBİRİ'}"]
    if not winners:
        lines += [
            "      Hiçbir yazım tutmadı. İKİNCİ İHTİMAL: sembol URL'de değil dosyanın",
            "      İÇİNDE olabilir — cevabı örnek dosyanın sembol kolonu verir (5. bölüm).",
        ]
    return lines


def format_coverage(probes: Sequence[ListingProbe], *, claimed_start: str) -> list[str]:
    lines = [
        "",
        "=" * 78,
        f"4) KAPSAM UCU — veri kümesi gerçekten {claimed_start}'te mi başlıyor?",
        "=" * 78,
        "  Ölçüt LİSTELEMEDİR, indirme değil: listeleme o ayın dizinidir ve",
        "  'dosya var mı' sorusunun yetkili cevabı odur.",
        "",
    ]
    for probe in probes:
        lines += _listing_line(probe)
    available = [p.label for p in probes if p.verdict == "dosya-listesi"]
    lines += ["", f"  >>> dosya listeleyen aylar: {', '.join(available) if available else 'HİÇBİRİ'}"]
    lines += [
        "",
        "  OKUMA NOTU: iddia edilen başlangıçtan ÖNCEKİ bir ay da dosya listeliyorsa",
        "  kapsam iddiası YANLIŞTIR ve dönem A'nın fiilî başlangıcı (docs/backtest.md",
        "  > 6f) yeniden yazılır. Tersi de geçerli: iddia edilen ay listelemiyorsa",
        "  kapsam iddia edilenden DAR demektir. Bu satır bir kaydın ÖLÇÜMLE",
        "  doğrulanmasıdır — ikincil kaynak bu belgede iki kez çürüdü (karar 50).",
    ]
    return lines


def format_sample(sample: SampleReport, *, universe: Sequence[str]) -> list[str]:
    lines = ["", "=" * 78, "5) ÖRNEK DOSYA — YAPI (kolon adları, damga biçimi, ızgara, semboller)",
             "=" * 78]
    lines.append(f"  url         : {sample.url or '—'}")
    if sample.error:
        lines += [f"  HATA/DURUM  : {sample.error}", "",
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

    lines += ["", f"  sembol kolonu: {sample.symbol_column} | ayrık sembol: {len(sample.symbols)}"]
    if sample.symbols:
        shown = ", ".join(sample.symbols[:LISTED_NAMES_SHOWN])
        more = f" … (+{len(sample.symbols) - LISTED_NAMES_SHOWN})" if len(sample.symbols) > LISTED_NAMES_SHOWN else ""
        lines.append(f"  semboller    : {shown}{more}")
        if len(sample.symbols) > 1:
            lines.append("  >>> Dosya BİRDEN ÇOK sembol taşıyor: sembol URL'de değil İÇERİDE.")
        present = [s for s in universe if s in sample.symbols]
        missing = [s for s in universe if s not in sample.symbols]
        lines.append(f"  ema evreni   : {len(present)}/{len(universe)} bu dosyada mevcut")
        if missing:
            lines.append(f"  eksik        : {', '.join(missing)}")
            lines.append(
                "  (Eksiklik bir arşiv kusuru DEĞİL olabilir: listeleme tarihi. "
                "Kapsam tablosu ayrı bir sorudur.)"
            )
    else:
        lines.append("  semboller    : OKUNAMADI (sembol kolonu bulunamadı)")

    lines += ["", "  örnek satırlar (BEYAZ liste dışındaki her hücre maskeli):"]
    if sample.columns:
        lines.append("    " + " | ".join(sample.columns))
    for row in sample.preview:
        lines.append("    " + " | ".join(row))
    lines += [
        "",
        "  Maskeleme BEYAZ listedir: yalnızca damga/sembol gibi META kolonlar gösterilir,",
        "  tanınmayan kolon GİZLENİR. Gerekçe: burada sızacak şey eşiğin görmemesi",
        "  gereken sayıdır (docs/backtest.md > 7).",
    ]
    return lines


def format_header(*, listing_template: str, download_template: str, msg_type: str) -> list[str]:
    return [
        "",
        "#" * 78,
        "# FONLAMA ARŞİVİ ŞEMA PROBE'u — salt okunur, ölçümün parçası DEĞİL",
        "# Dağılım göstermez, eşik önermez, getiri/R/PnL hesaplamaz.",
        "# Rapor meta veri taşır: durum, içerik tipi, boy, dosya ADLARI, KOLON ADLARI,",
        "# damga biçimi, sembol adları. Hiçbir fonlama ORANI yazılmaz.",
        "# ŞEMAYA UYDURMA YOKTUR: gözlem çelişirse GERÇEK raporlanır.",
        "# Şema İKİ ADIMLI: listeleme dosya ADINI verir, indirme onu kullanır.",
        f"# msg_type   : {msg_type}",
        f"# listeleme  : {listing_template}",
        f"# indirme    : {download_template}",
        "# Ön-kayıt: docs/backtest.md > 6f",
        "#" * 78,
    ]


# --------------------------------------------------------------------------- #
# Giriş noktası
# --------------------------------------------------------------------------- #
def _month(text: str) -> pd.Timestamp:
    stamp = pd.Timestamp(text if len(text) > 7 else f"{text}-01")
    return stamp.tz_localize("UTC") if stamp.tzinfo is None else stamp.tz_convert("UTC")


def _listing_for(
    template: str, *, date: pd.Timestamp, msg_type: str, label: str
) -> tuple[ListingProbe, str]:
    """Ayın İKİ yazımını da dener; ilk DOSYA LİSTESİ döndüreni seçer.

    Seçim bir tercih değil bir GÖZLEMDİR: hangisinin tuttuğunu veri söyler. Hiçbiri
    tutmazsa son deneme raporlanır (ham gözlem yine de yazılsın diye).
    """
    last: ListingProbe | None = None
    for style in ("dash", "compact"):
        url = render_url(template, date=date, msg_type=msg_type, month_style=style)
        probe = probe_listing(url, label=f"{label} [{month_candidates(date)[0 if style == 'dash' else 1]}]")
        if probe.verdict == "dosya-listesi":
            return probe, style
        last = probe
    assert last is not None
    return last, ""


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
    try:
        sample_date = _month(args.sample_date)
        claimed = _month(ARCHIVE_CLAIMED_START)
    except Exception as exc:
        logger.error("tarih çözülemedi: %s", exc)
        return 2

    lines = format_header(
        listing_template=args.listing_template,
        download_template=args.download_template,
        msg_type=args.msg_type,
    )

    # --- 1) LİSTELEME: dosya adlarının tek kaynağı ---------------------------
    listing_probes: list[ListingProbe] = []
    chosen: ListingProbe | None = None
    for style in ("dash", "compact"):
        try:
            url = render_url(
                args.listing_template, date=sample_date, msg_type=args.msg_type,
                month_style=style, symbol=sample_symbol,
            )
        except ValueError as exc:
            logger.error("listeleme şablonu hatası: %s", exc)
            return 2
        label = f"ay={month_candidates(sample_date)[0 if style == 'dash' else 1]}"
        probe = probe_listing(url, label=label)
        listing_probes.append(probe)
        if chosen is None and probe.verdict == "dosya-listesi":
            chosen = probe
    lines += format_listing(listing_probes, template=args.listing_template)

    # --- 2) İNDİRME: adı listelemeden gelir, UYDURULMAZ ----------------------
    download_probe: UrlProbe | None = None
    chosen_file = ""
    if chosen is not None and chosen.names:
        chosen_file = chosen.names[0]
        try:
            url = render_url(
                args.download_template, date=sample_date, msg_type=args.msg_type,
                file=chosen_file, symbol=sample_symbol, month_style="compact",
            )
        except ValueError as exc:
            logger.error("indirme şablonu hatası: %s", exc)
            return 2
        download_probe = probe_url(url, label=chosen_file)
    lines += format_download(download_probe, template=args.download_template, file=chosen_file)

    # --- 3) SEMBOL: şablonda yeri varsa yazımlar, yoksa dosyanın içi ---------
    has_symbol = "{symbol}" in args.download_template or "{symbol}" in args.listing_template
    style_probes: list[UrlProbe] = []
    if has_symbol and chosen_file:
        variants = symbol_variants(sample_symbol)
        for style in SYMBOL_STYLES:
            url = render_url(
                args.download_template, date=sample_date, msg_type=args.msg_type,
                file=chosen_file, symbol=variants[style], month_style="compact",
            )
            style_probes.append(probe_url(url, label=f"{style} ({variants[style]})"))
    lines += format_symbol_style(style_probes, in_template=has_symbol and bool(chosen_file))

    # --- 4) KAPSAM UCU: ölçüt LİSTELEMEDİR ----------------------------------
    coverage_probes: list[ListingProbe] = []
    for offset in (-2, -1, 0, 1):
        month = claimed + pd.DateOffset(months=offset)
        probe, _ = _listing_for(
            args.listing_template, date=month, msg_type=args.msg_type,
            label=f"{month:%Y-%m}",
        )
        coverage_probes.append(probe)
    lines += format_coverage(coverage_probes, claimed_start=ARCHIVE_CLAIMED_START)

    # --- 5) ÖRNEK DOSYA -----------------------------------------------------
    sample = SampleReport(**{
        **_empty_sample("").__dict__,
        "error": "--no-sample ile atlandı" if args.no_sample else "indirme adımı dosya vermedi",
    })
    if not args.no_sample and download_probe is not None and download_probe.verdict == "dosya":
        sample = read_sample(download_probe.url)
    lines += format_sample(sample, universe=universe)

    # --- TOPLU SONUÇ + VERİ KAPISI ------------------------------------------
    listing_ok = any(p.verdict == "dosya-listesi" for p in listing_probes + coverage_probes)
    any_error = any(
        p.verdict == "hata" for p in listing_probes + coverage_probes
    ) or (download_probe is not None and download_probe.verdict == "hata")

    lines += ["", "=" * 78, "TOPLU SONUÇ", "=" * 78]
    if not listing_ok and any_error:
        lines.append("BELİRSİZ — istekler hata verdi; şema hakkında hiçbir şey söylenemez.")
        exit_code = 1
    elif not listing_ok:
        lines += [
            "LİSTELEME BAŞARISIZ — indirme şeması SINANMADI.",
            "Bu bir 'şema uymuyor' yargısı DEĞİLDİR; ilk adım düştüğü için ikinci adım",
            "hakkında gözlem yok. Sıradaki iş ham gözleme bakmaktır (1. bölüm).",
        ]
        exit_code = 3
    elif download_probe is None or download_probe.verdict != "dosya":
        lines += [
            "LİSTELEME GEÇTİ, İNDİRME GEÇMEDİ — ikisi ayrı ayrı raporlandı.",
            "Dosya adı listelemeden geldi, yani ad bir tahmin değil; başarısızlık",
            "indirme yolundadır.",
        ]
        exit_code = 3
    else:
        lines.append("LİSTELEME + İNDİRME GEÇTİ — yapı raporu 5. bölümde.")
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
        "--listing-template", required=True,
        help="ADIM 1: dosya adlarını veren listeleme URL'i; yer tutucular: "
             "{msg_type} {month} {epoch_ms} {yyyy} {mm} {yyyymm} {yyyy-mm} …",
    )
    parser.add_argument(
        "--download-template", required=True,
        help="ADIM 2: indirme URL'i; {file} ZORUNLU olarak listelemeden doldurulur",
    )
    parser.add_argument("--msg-type", default="swaprate", help="veri kümesi türü (fonlama: swaprate)")
    parser.add_argument("--layer", default="ema", help="sembol evreninin alınacağı katman")
    parser.add_argument("--config", default=None)
    parser.add_argument("--symbols", nargs="*", default=None, help="varsayılan: katmanın evreni")
    parser.add_argument("--sample-symbol", default=None)
    parser.add_argument("--sample-date", default=ARCHIVE_CLAIMED_START, help="YYYY-MM ya da tarih")
    parser.add_argument("--no-sample", action="store_true", help="örnek dosyayı indirme")
    return parser.parse_args(argv)


if __name__ == "__main__":
    raise SystemExit(main())

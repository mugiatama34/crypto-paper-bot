"""`scripts/probe_funding_archive.py`nin SAF mantığı: ağ erişimi yok, defter yok.

Sınanan şey probe'un beş sözleşmesidir:
(1) şema ↔ gerçek yargısı MEKANİKTİR ve ölçütü "DOSYA döndü mü"dür, "istek başarılı
    mı" değil — 200 dönen bir HTML sayfası şemayı doğrulamaz;
(2) belirsiz bir sonuç sessizce "şema uymuyor"a düşmez;
(3) örnek satır maskelemesi BEYAZ listedir — tanınmayan kolon GİZLENİR;
(4) şablondaki tanınmayan yer tutucu sessiz geçilmez;
(5) ölçümün parçası değildir — R/PnL üreten modülleri import etmez ve kaynağında
    bir oran alanı OKUNMAZ.
"""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pandas as pd
import pytest

from scripts.probe_funding_archive import (
    MASK,
    SampleReport,
    classify_response,
    classify_schema_match,
    describe_payload,
    detect_stamp_format,
    mask_row,
    modal_interval,
    render_url,
    symbol_variants,
)


def _zip(csv_text: str, *, name: str = "sample.csv") -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as archive:
        archive.writestr(name, csv_text)
    return buf.getvalue()


CSV = (
    "instrument_id,funding_time,realized_rate\n"
    "BTC-USDT-SWAP,1646092800000,0.00010000\n"
    "BTC-USDT-SWAP,1646121600000,-0.00025000\n"
    "BTC-USDT-SWAP,1646150400000,0.00005000\n"
)


# --------------------------------------------------------------------------- #
# Şema açma — tanınmayan yer tutucu SESSİZ GEÇİLMEZ
# --------------------------------------------------------------------------- #
def test_render_url_expands_every_known_placeholder():
    url = render_url(
        "https://h/{yyyymm}/{symbol}-swaprate-{yyyy-mm}.zip",
        symbol="BTCUSDT",
        date=pd.Timestamp("2022-03-01"),
    )
    assert url == "https://h/202203/BTCUSDT-swaprate-2022-03.zip"


def test_unknown_placeholder_raises_instead_of_producing_a_half_url():
    """Yarı açılmış URL 404 döner ve 'şema tutmuyor' diye okunurdu — oysa hata bizde."""
    with pytest.raises(ValueError, match="tanınmayan yer tutucu"):
        render_url("https://h/{quarter}/x.zip", symbol="B", date=pd.Timestamp("2022-03-01"))


def test_symbol_variants_are_derived_not_guessed():
    assert symbol_variants("BTC-USDT-SWAP") == {
        "instid": "BTC-USDT-SWAP",
        "compact": "BTCUSDT",
        "dash": "BTC-USDT",
        "base": "BTC",
    }


# --------------------------------------------------------------------------- #
# Yanıt sınıflandırması — ÖLÇÜT "dosya geldi mi", "200 mü" DEĞİL
# --------------------------------------------------------------------------- #
def test_html_two_hundred_is_not_a_file():
    """Giriş duvarı ya da SPA kabuğu 200 döner; onu 'indirdik' saymak asıl tuzaktır."""
    assert classify_response(
        status=200, content_type="text/html; charset=utf-8", size=59000, error=""
    ) == "sayfa-döndü"


def test_zip_two_hundred_is_a_file():
    assert classify_response(
        status=200, content_type="application/zip", size=4096, error=""
    ) == "dosya"


def test_auth_statuses_are_reported_as_login_required():
    for status in (401, 403):
        assert classify_response(
            status=status, content_type="", size=None, error=""
        ) == "giriş-gerekli"


def test_missing_file_is_distinct_from_error():
    assert classify_response(status=404, content_type="", size=None, error="") == "yok"
    assert classify_response(
        status=None, content_type="", size=None, error="Timeout"
    ) == "hata"


def test_unknown_content_type_is_not_optimistically_called_a_file():
    assert classify_response(
        status=200, content_type="", size=4096, error=""
    ) == "belirsiz-içerik"


# --------------------------------------------------------------------------- #
# Şema yargısı — belirsizlik "uymuyor"a DÜŞMEZ
# --------------------------------------------------------------------------- #
def test_all_files_means_the_schema_holds():
    assert classify_schema_match(["dosya", "dosya", "dosya"]) == "uyuyor"


def test_some_files_is_reported_as_partial_not_as_success():
    assert classify_schema_match(["dosya", "yok", "dosya"]) == "kısmen"


def test_a_request_error_yields_inconclusive_not_a_mismatch():
    assert classify_schema_match(["dosya", "hata"]) == "belirsiz"
    assert classify_schema_match([]) == "belirsiz"


def test_pages_everywhere_name_the_real_reason():
    assert classify_schema_match(["sayfa-döndü", "sayfa-döndü"]).startswith("uymuyor")
    assert "sayfa" in classify_schema_match(["sayfa-döndü"])
    assert "giriş" in classify_schema_match(["giriş-gerekli"])


# --------------------------------------------------------------------------- #
# Maskeleme BEYAZ listedir — tanınmayan kolon GİZLENİR
# --------------------------------------------------------------------------- #
def test_rate_column_is_masked():
    assert mask_row(
        ["timestamp", "fundingRate", "symbol"], ["1646092800000", "0.0001", "BTC"]
    ) == ["1646092800000", MASK, "BTC"]


def test_unrecognised_column_is_masked_not_shown():
    """Kara liste olsaydı beklenmedik adlı bir oran kolonu sızardı."""
    assert mask_row(["timestamp", "weird_new_field"], ["1", "0.0009"]) == ["1", MASK]


def test_extra_values_beyond_the_header_are_masked():
    assert mask_row(["timestamp"], ["1", "0.0009"]) == ["1", MASK]


# --------------------------------------------------------------------------- #
# Yapı çıkarımı — SAF, ağsız; oran hiçbir alana girmez
# --------------------------------------------------------------------------- #
def test_zip_structure_is_reported_without_any_rate():
    report = describe_payload(
        _zip(CSV, name="BTC-USDT-SWAP-swaprate-2022-03.csv"),
        url="https://h/a.zip", status=200, content_type="application/zip",
    )
    assert report.container == "zip"
    assert report.members == ("BTC-USDT-SWAP-swaprate-2022-03.csv",)
    assert report.columns == ("instrument_id", "funding_time", "realized_rate")
    assert report.rows == 3
    assert report.stamp_column == "funding_time"
    assert report.stamp_format == "epoch_ms"
    assert report.interval == pd.Timedelta(hours=8)
    # Kolon ADI meta veridir ve raporlanır; DEĞER hiçbir alanda görünmez.
    assert "0.00010000" not in str(report.preview)
    assert "0.00025000" not in str(report)


def test_plain_csv_payload_needs_no_container():
    report = describe_payload(
        CSV.encode("utf-8"), url="https://h/a.csv", status=200, content_type="text/csv"
    )
    assert report.container == "csv"
    assert report.rows == 3


def test_non_file_payload_reports_the_reason_and_no_structure():
    report = describe_payload(
        b"<html>login</html>", url="https://h/a.zip", status=200, content_type="text/html",
    )
    assert "sayfa-döndü" in report.error
    assert report.columns == ()


def test_empty_zip_is_reported_not_guessed():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w"):
        pass
    report = describe_payload(
        buf.getvalue(), url="https://h/a.zip", status=200, content_type="application/zip"
    )
    assert report.error == "zip boş"


# --------------------------------------------------------------------------- #
# Damga biçimi ve ızgara
# --------------------------------------------------------------------------- #
def test_stamp_format_detection():
    assert detect_stamp_format("1646092800000") == "epoch_ms"
    assert detect_stamp_format("1646092800") == "epoch_s"
    assert detect_stamp_format("2022-03-01T00:00:00Z") == "iso"
    assert detect_stamp_format("not-a-time") == "bilinmiyor"


def test_modal_interval_is_the_most_common_gap_not_the_mean():
    """Tek bir boşluk (listeleme kesintisi) ızgarayı kaydırmamalı."""
    stamps = [
        pd.Timestamp("2022-03-01T00:00Z"),
        pd.Timestamp("2022-03-01T08:00Z"),
        pd.Timestamp("2022-03-01T16:00Z"),
        pd.Timestamp("2022-03-05T00:00Z"),
    ]
    assert modal_interval(stamps) == pd.Timedelta(hours=8)


def test_modal_interval_of_a_single_stamp_is_none_not_zero():
    assert modal_interval([pd.Timestamp("2022-03-01T00:00Z")]) is None


# --------------------------------------------------------------------------- #
# Probe ölçümün parçası DEĞİL
# --------------------------------------------------------------------------- #
def _source() -> str:
    return Path("scripts/probe_funding_archive.py").read_text(encoding="utf-8")


def test_module_does_not_import_measurement_or_strategy_modules():
    text = _source()
    for banned in ("core.portfolio", "core.metrics", "core.ledger", "strategies."):
        assert f"import {banned}" not in text and f"from {banned}" not in text


def test_module_never_reads_a_rate_field():
    """Kolon ADI raporlanır ama hiçbir oran alanı OKUNMAZ; beyaz liste tek geçittir."""
    text = _source()
    assert "fundingRate" not in text
    assert "realized_rate" not in text


def test_sample_report_carries_no_value_field():
    """Yapı raporunun alanları meta veridir; bir 'değer' alanı olsaydı söz delinirdi."""
    fields = set(SampleReport.__dataclass_fields__)
    assert "values" not in fields and "rates" not in fields and "data" not in fields

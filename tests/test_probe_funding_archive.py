"""`scripts/probe_funding_archive.py`nin SAF mantığı: ağ erişimi yok, defter yok.

Sınanan şey probe'un YEDİ sözleşmesidir:
(1) şema ↔ gerçek yargısı MEKANİKTİR ve ölçütü "DOSYA döndü mü"dür, "istek başarılı
    mı" değil — 200 dönen bir HTML sayfası şemayı doğrulamaz;
(2) belirsiz bir sonuç sessizce "şema uymuyor"a düşmez;
(3) LİSTELEME başarısızlığı "şema uymuyor" DEĞİLDİR — ayrı bir sınıftır, çünkü ilk
    adım düşünce ikinci adım hiç denenmez;
(4) dosya adı UYDURULMAZ: `{file}` taşıyan bir şablon boş adla açılamaz;
(5) örnek satır maskelemesi BEYAZ listedir — tanınmayan kolon GİZLENİR;
(6) şablondaki tanınmayan yer tutucu sessiz geçilmez;
(7) ölçümün parçası değildir — R/PnL üreten modülleri import etmez ve kaynağında
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
    classify_listing,
    classify_response,
    classify_schema_match,
    describe_payload,
    detect_stamp_format,
    extract_file_names,
    mask_row,
    modal_interval,
    month_candidates,
    render_url,
    symbol_variants,
)

NOW = pd.Timestamp("2026-09-20T00:00:00")


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


def test_listing_placeholders_expand():
    url = render_url(
        "https://h/priapi?t={epoch_ms}&path=cdn/{msg_type}/monthly/{month}",
        date=pd.Timestamp("2022-03-01"), msg_type="swaprate", month_style="dash", now=NOW,
    )
    assert url == f"https://h/priapi?t={int(NOW.value // 1_000_000)}&path=cdn/swaprate/monthly/2022-03"


def test_month_placeholder_has_two_spellings_and_both_are_tried():
    """Ay yazımı iki adımda ayrışıyor; hangisinin tuttuğunu SEÇMEK bir tahmin olurdu."""
    assert month_candidates(pd.Timestamp("2022-03-01")) == ("2022-03", "202203")
    tpl = "https://h/{month}"
    assert render_url(tpl, date=pd.Timestamp("2022-03-01"), month_style="dash") == "https://h/2022-03"
    assert render_url(tpl, date=pd.Timestamp("2022-03-01"), month_style="compact") == "https://h/202203"


def test_file_name_is_never_invented():
    """Ad listelemeden gelir; boş adla açmak, tahmini şemanın yerine koymak olurdu."""
    with pytest.raises(ValueError, match="listelemeden gelir"):
        render_url("https://s/{yyyymm}/{file}", date=pd.Timestamp("2022-03-01"))


def test_file_name_from_listing_is_used_verbatim():
    url = render_url(
        "https://s/{msg_type}/monthly/{yyyymm}/{file}",
        date=pd.Timestamp("2022-03-01"), msg_type="swaprate",
        file="BTC-USDT-SWAP-swaprate-2022-03-01.zip",
    )
    assert url.endswith("/swaprate/monthly/202203/BTC-USDT-SWAP-swaprate-2022-03-01.zip")


# --------------------------------------------------------------------------- #
# Listeleme — dosya adlarının TEK kaynağı, yapı VARSAYILMADAN gezilir
# --------------------------------------------------------------------------- #
def test_file_names_are_found_without_assuming_the_json_shape():
    """`data[0].fileList` gibi bir yol varsaymak, başka biçimde 'dosya yok' derdi."""
    body = {"code": "0", "data": [{"fileList": ["a-2022-03-01.zip", "b-2022-03-02.zip"]}]}
    assert extract_file_names(body) == ("a-2022-03-01.zip", "b-2022-03-02.zip")


def test_file_names_are_found_at_any_depth_and_deduplicated():
    body = {"x": {"y": [{"z": "f.csv"}, {"z": "f.csv"}, "g.zip"]}}
    assert extract_file_names(body) == ("f.csv", "g.zip")


def test_non_file_strings_are_not_collected():
    assert extract_file_names({"msg": "ok", "path": "cdn/okex/traderecords"}) == ()


def test_listing_success_requires_file_names_not_just_two_hundred():
    assert classify_listing(
        status=200, content_type="application/json", error="", parsed=True, names=("a.zip",)
    ) == "dosya-listesi"
    assert classify_listing(
        status=200, content_type="application/json", error="", parsed=True, names=()
    ) == "json-ama-dosya-yok"


def test_listing_that_returns_html_is_its_own_class():
    assert classify_listing(
        status=200, content_type="text/html", error="", parsed=False, names=()
    ) == "sayfa-döndü"


def test_listing_that_is_not_json_is_its_own_class():
    assert classify_listing(
        status=200, content_type="text/plain", error="", parsed=False, names=()
    ) == "json-değil"


def test_listing_failure_classes_are_never_the_schema_verdict():
    """Kritik ayrım: listeleme düşerse indirme HİÇ denenmez, yani şema sınanmamıştır."""
    failures = {
        classify_listing(status=200, content_type="text/html", error="", parsed=False, names=()),
        classify_listing(status=403, content_type="", error="", parsed=False, names=()),
        classify_listing(status=404, content_type="", error="", parsed=False, names=()),
        classify_listing(status=None, content_type="", error="Timeout", parsed=False, names=()),
        classify_listing(
            status=200, content_type="application/json", error="", parsed=True, names=()
        ),
    }
    for verdict in failures:
        assert verdict != "dosya-listesi"
        assert not verdict.startswith("uymuyor")
        assert verdict != "uyuyor"


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


def test_symbol_column_is_read_so_the_in_file_hypothesis_can_be_tested():
    """Sembol URL'de olmayabilir; aylık dosya tüm sembolleri taşıyor olabilir."""
    multi = (
        "instrument_id,funding_time,realized_rate\n"
        "BTC-USDT-SWAP,1646092800000,0.0001\n"
        "ETH-USDT-SWAP,1646092800000,0.0002\n"
        "BTC-USDT-SWAP,1646121600000,0.0003\n"
    )
    report = describe_payload(
        multi.encode("utf-8"), url="https://h/a.csv", status=200, content_type="text/csv"
    )
    assert report.symbol_column == "instrument_id"
    assert report.symbols == ("BTC-USDT-SWAP", "ETH-USDT-SWAP")
    # Sembol ADI meta veridir ve raporlanır; ORAN yine hiçbir alanda yok.
    assert "0.0002" not in str(report.preview)


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

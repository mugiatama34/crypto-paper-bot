"""`scripts/archive_funding.py`: tekilleştirme, append-only garantisi, çakışma logu.

Ağsız: istemci sahte bir sayfalayıcıdır. Arşiv `tmp_path` altında kurulur.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from scripts import archive_funding as af

SYMBOL = "BTC-USDT-SWAP"
H8 = 8 * 3600 * 1000
T0 = 1_750_000_000_000 - (1_750_000_000_000 % H8)


def _raw(i: int, rate: str = "0.0001", symbol: str = SYMBOL, realized: str | None = None) -> dict:
    return {
        "instId": symbol,
        "fundingTime": str(T0 + i * H8),
        "fundingRate": rate,
        "realizedRate": realized if realized is not None else rate,
        "method": "current_period",
    }


class FakeClient:
    """OKX gibi: yeniden eskiye, `after` imleciyle sayfalar."""

    def __init__(self, records: list[dict]) -> None:
        self.records = sorted(records, key=lambda r: -int(r["fundingTime"]))
        self.calls: list[dict[str, str]] = []

    def get(self, path: str, params: dict[str, str]) -> list[dict[str, Any]]:
        assert path == af.FUNDING_ENDPOINT
        self.calls.append(dict(params))
        rows = self.records
        if "after" in params:
            rows = [r for r in rows if int(r["fundingTime"]) < int(params["after"])]
        return [dict(r) for r in rows[: int(params["limit"])]]


def _run(tmp_path: Path, records: list[dict], *, day: str = "2026-09-27", limit: int = 3):
    return af.run(
        FakeClient(records),
        [SYMBOL],
        archive_dir=tmp_path,
        now=pd.Timestamp(day, tz="UTC"),
        limit=limit,
    )


def _rows(tmp_path: Path) -> list[dict[str, str]]:
    return af.read_archive(tmp_path / f"{SYMBOL}.csv")


def test_first_run_archives_the_whole_window_across_pages(tmp_path: Path) -> None:
    report = _run(tmp_path, [_raw(i) for i in range(8)], limit=3)
    rows = _rows(tmp_path)
    assert [int(r["funding_time_ms"]) for r in rows] == [T0 + i * H8 for i in range(8)]
    assert report.symbols[0].added == 8
    assert report.exit_code() == 0


def test_rerun_with_same_data_adds_nothing_and_is_flagged(tmp_path: Path) -> None:
    _run(tmp_path, [_raw(i) for i in range(5)])
    before = (tmp_path / f"{SYMBOL}.csv").read_bytes()
    report = _run(tmp_path, [_raw(i) for i in range(5)], day="2026-09-28")
    assert (tmp_path / f"{SYMBOL}.csv").read_bytes() == before
    assert report.added == 0 and report.symbols[0].duplicates == 5
    assert report.exit_code() == 0  # yeşil …
    assert any("boş geçti" in line for line in af.annotations(report))  # … ama işaretli


def test_duplicates_within_one_fetch_are_collapsed(tmp_path: Path) -> None:
    result = af.merge(
        [],
        [af.normalize_row(_raw(1), symbol=SYMBOL, archived_at="x")] * 3,
        detected_at="x",
    )
    assert len(result.new_rows) == 1 and result.duplicates == 2


def test_append_only_existing_bytes_stay_a_prefix(tmp_path: Path) -> None:
    """Kayan pencere: eski damgalar düşer, yeniler gelir — dosya yalnızca UZAR."""
    _run(tmp_path, [_raw(i) for i in range(0, 5)])
    first = (tmp_path / f"{SYMBOL}.csv").read_bytes()
    _run(tmp_path, [_raw(i) for i in range(3, 9)], day="2026-09-28")
    second = (tmp_path / f"{SYMBOL}.csv").read_bytes()
    assert second.startswith(first) and len(second) > len(first)
    stamps = [int(r["funding_time_ms"]) for r in _rows(tmp_path)]
    assert stamps == [T0 + i * H8 for i in range(9)]  # pencereden düşenler KALDI


def test_existing_row_keeps_its_original_archived_at(tmp_path: Path) -> None:
    _run(tmp_path, [_raw(0)], day="2026-09-27")
    _run(tmp_path, [_raw(0), _raw(1)], day="2026-09-28")
    rows = _rows(tmp_path)
    assert rows[0]["archived_at"].startswith("2026-09-27")
    assert rows[1]["archived_at"].startswith("2026-09-28")


def test_changed_rate_is_not_overwritten_but_logged_once(tmp_path: Path) -> None:
    _run(tmp_path, [_raw(0, "0.0001"), _raw(1, "0.0002")])
    before = (tmp_path / f"{SYMBOL}.csv").read_bytes()

    report = _run(tmp_path, [_raw(0, "0.0001"), _raw(1, "0.0003")], day="2026-09-28")
    assert (tmp_path / f"{SYMBOL}.csv").read_bytes() == before  # üzerine YAZILMADI
    conflicts = af._read_csv(tmp_path / af.CONFLICTS_FILE, af.CONFLICT_COLUMNS)
    assert {(c["field"], c["archived_value"], c["observed_value"]) for c in conflicts} == {
        ("funding_rate", "0.0002", "0.0003"),
        ("realized_rate", "0.0002", "0.0003"),
    }
    assert report.symbols[0].conflicts == 2
    assert any("çakışma" in line.lower() for line in af.annotations(report))

    # Aynı çakışma ertesi gün yeniden yazılmaz; FARKLI bir gözlem yazılır.
    _run(tmp_path, [_raw(1, "0.0003")], day="2026-09-29")
    assert len(af._read_csv(tmp_path / af.CONFLICTS_FILE, af.CONFLICT_COLUMNS)) == 2
    _run(tmp_path, [_raw(1, "0.0004")], day="2026-09-30")
    assert len(af._read_csv(tmp_path / af.CONFLICTS_FILE, af.CONFLICT_COLUMNS)) == 4


def test_equal_decimals_in_different_spelling_are_not_a_conflict(tmp_path: Path) -> None:
    _run(tmp_path, [_raw(0, "0.0001")])
    report = _run(tmp_path, [_raw(0, "0.00010")], day="2026-09-28")
    assert report.symbols[0].conflicts == 0 and report.symbols[0].duplicates == 1
    assert not (tmp_path / af.CONFLICTS_FILE).exists()


def test_rates_are_stored_as_exchange_text(tmp_path: Path) -> None:
    _run(tmp_path, [_raw(0, "0.000012345678901234567")])
    assert _rows(tmp_path)[0]["funding_rate"] == "0.000012345678901234567"


def test_wrong_header_is_an_integrity_error_and_file_is_untouched(tmp_path: Path) -> None:
    path = tmp_path / f"{SYMBOL}.csv"
    path.write_text("a,b\n1,2\n", encoding="utf-8")
    report = _run(tmp_path, [_raw(0)])
    assert path.read_text(encoding="utf-8") == "a,b\n1,2\n"
    assert report.symbols[0].integrity and report.exit_code() == 1


def test_half_written_last_line_is_not_repaired(tmp_path: Path) -> None:
    _run(tmp_path, [_raw(0)])
    path = tmp_path / f"{SYMBOL}.csv"
    broken = path.read_text(encoding="utf-8") + "BTC-USDT-SWAP,17"
    path.write_text(broken, encoding="utf-8")
    report = _run(tmp_path, [_raw(0), _raw(1)], day="2026-09-28")
    assert path.read_text(encoding="utf-8") == broken
    assert report.exit_code() == 1


def test_foreign_symbol_in_response_is_rejected(tmp_path: Path) -> None:
    report = _run(tmp_path, [_raw(0, symbol="ETH-USDT-SWAP")])
    assert report.symbols[0].integrity
    assert not (tmp_path / f"{SYMBOL}.csv").exists()


def test_network_error_is_a_warning_not_a_failure(tmp_path: Path) -> None:
    class Boom:
        def get(self, *_: Any) -> list:
            raise RuntimeError("timeout")

    ok = af.archive_symbol(
        FakeClient([_raw(0, symbol="ETH-USDT-SWAP")]),
        "ETH-USDT-SWAP",
        archive_dir=tmp_path,
        detected_at="x",
        limit=3,
    )
    bad = af.archive_symbol(Boom(), SYMBOL, archive_dir=tmp_path, detected_at="x", limit=3)
    report = af.RunReport(symbols=[ok, bad])
    assert report.exit_code() == 0
    assert any(line.startswith("::warning") and SYMBOL in line for line in af.annotations(report))

    all_bad = af.RunReport(symbols=[bad])
    assert all_bad.exit_code() == 1


def test_pagination_stops_when_cursor_does_not_move(tmp_path: Path) -> None:
    class Stuck:
        calls = 0

        def get(self, _path: str, _params: dict) -> list:
            Stuck.calls += 1
            return [_raw(0), _raw(1), _raw(2)]  # `after` yok sayılıyor

    rows = af.fetch_window(Stuck(), SYMBOL, limit=3)
    assert Stuck.calls == 2 and len(rows) == 6


def test_archived_symbols_are_kept_even_if_they_leave_the_universe(tmp_path: Path) -> None:
    (tmp_path / "OLD-USDT-SWAP.csv").write_text(",".join(af.COLUMNS) + "\n", encoding="utf-8")
    (tmp_path / af.CONFLICTS_FILE).write_text("x\n", encoding="utf-8")
    assert af.archived_symbols(tmp_path) == ["OLD-USDT-SWAP"]


def test_report_prints_no_rates(tmp_path: Path) -> None:
    """Rapor meta veridir: oran dosyaya yazılır, log'a/özete düşmez."""
    report = _run(tmp_path, [_raw(0, "0.000777"), _raw(1, "0.000888")])
    text = "\n".join(af.format_report(report) + af.annotations(report))
    assert "0.000777" not in text and "0.000888" not in text


@pytest.mark.parametrize("left,right,same", [("0.1", "0.10", True), ("", "", True), ("", "0", False)])
def test_same_rate(left: str, right: str, same: bool) -> None:
    assert af.same_rate(left, right) is same

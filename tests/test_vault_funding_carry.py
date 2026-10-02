"""§6w kasa sınaması: dondurulmuş kuralın geliştirme penceresinde birebir üretimi, kasa açılışı ve sayım.

`test_dev_window_reproduces_6t` ZORUNLU REGRESYON TESTİDİR (§6w > 8, V4): parametreye çevrilmiş
`scripts/measure_funding_carry.py` geliştirme penceresinde, sabitlenmiş veride ve geliştirme anındaki 51
arşiv dosyasıyla §6t'nin sayılarını birebir üretmezse kasa koşusu başlatılamaz.
"""

from __future__ import annotations

import json
import re
import shutil
from argparse import Namespace
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from core.config import load_config
from scripts import measure_funding_carry as mfc
from scripts import vault_funding_carry as vfc
from scripts.vault import KASA_START, OPENINGS, VaultError

ROOT = Path(__file__).resolve().parent.parent
PUBLISHED = ROOT / "docs" / "data" / "funding_carry.json"
# TADİLAT-2'li maker (m1) neti — §6t > TADİLAT-2 > "Betimsel etki" (+%0.16).
MAKER_NET_TADILAT2 = 0.0016237931145948042


def _dev_archive(tmp_path: Path) -> Path:
    published = json.loads(PUBLISHED.read_text(encoding="utf-8"))
    archive = tmp_path / "archive"
    archive.mkdir()
    for symbol in published["archive"]["files"]:
        shutil.copy(ROOT / "data" / "funding_archive" / f"{symbol}.csv", archive / f"{symbol}.csv")
    return archive


def test_dev_window_reproduces_6t(tmp_path: Path) -> None:
    published = json.loads(PUBLISHED.read_text(encoding="utf-8"))
    args = Namespace(pins=str(ROOT / mfc.PINS_DIR), archive=str(_dev_archive(tmp_path)), out_dir=str(tmp_path / "out"))
    assert mfc.run_analysis(args, load_config(), measure=True, window=mfc.DEV_WINDOW) == 0
    got = json.loads((tmp_path / "out" / "funding_carry.json").read_text(encoding="utf-8"))

    assert got["archive"]["files"] == published["archive"]["files"]          # arşivin kasa öncesi satırları aynı
    assert got["primary_taker"]["positions"] == 0                             # taker 0 pozisyon
    today = got["descriptive"]["universe_today"]                              # (d1)
    assert today["positions"] == 2
    assert today["position_days"] == pytest.approx(46.541666666666664, abs=1e-12)
    assert today["components"]["net"] == pytest.approx(0.010128620924335714, abs=1e-12)
    maker = got["descriptive"]["maker_own_threshold"]                         # (m1), TADİLAT-2'li
    assert maker["positions"] == 6
    assert maker["components"]["net"] == pytest.approx(MAKER_NET_TADILAT2, abs=1e-12)
    assert round(100 * maker["components"]["net"], 2) == 0.16
    # TADİLAT-2'nin değiştirdiği tek satır maker (m1); geri kalan her alan yayımlanmış yükle birebir.
    for key in published:
        if key not in ("descriptive", "preregistration"):     # preregistration metnine TADİLAT-2 eklendi
            assert got[key] == published[key], key
    for key in published["descriptive"]:
        if key != "maker_own_threshold":
            assert got["descriptive"][key] == published["descriptive"][key], key


def test_dev_window_is_the_old_constants() -> None:
    w = mfc.DEV_WINDOW
    assert (w.data_start, w.measure_start, w.end) == (mfc.DEV_START, mfc.MEASURE_START, KASA_START)
    assert (w.snapshot_start, w.volume_start, w.volume_end, w.fetch_now) == (
        mfc.SNAPSHOT_START, mfc.VOLUME_START, mfc.VOLUME_END, KASA_START)
    assert w.opening is None


def test_window_into_vault_needs_registered_opening() -> None:
    later = KASA_START + pd.Timedelta(days=7)
    kwargs = dict(data_start=vfc.DATA_START, measure_start=vfc.MEASURE_START, end=later, snapshot_start=vfc.U2_START,
                  volume_start=vfc.VOLUME_START, volume_end=vfc.VOLUME_END, fetch_now=later)
    with pytest.raises(VaultError):
        mfc.Window(**kwargs)
    with pytest.raises(VaultError):
        mfc.Window(**kwargs, opening="başka")
    assert mfc.Window(**kwargs, opening=vfc.OPENING).end == later
    assert vfc.OPENING in OPENINGS


def test_only_the_vault_tool_names_the_opening() -> None:
    sources = list((ROOT / "scripts").glob("*.py")) + list((ROOT / "core").glob("*.py"))
    naming = {p.name for p in sources if '"funding_carry_vault"' in p.read_text(encoding="utf-8")}
    assert naming == {"vault.py", "vault_funding_carry.py"}
    opening_api = {p.name for p in sources if re.search(r"\bassert_vault_opening\b", p.read_text(encoding="utf-8"))}
    assert opening_api == {"vault.py", "measure_funding_carry.py"}


def test_checkpoint_schedule() -> None:
    assert vfc.checkpoints_due(pd.Timestamp("2026-12-27T23:00Z")) == []
    first = vfc.checkpoints_due(pd.Timestamp("2026-12-28T00:00Z"))
    assert first == [pd.Timestamp("2026-12-28T00:00Z")] and vfc.weeks_between(first[0]) == 13
    capped = vfc.checkpoints_due(pd.Timestamp("2028-01-01T00:00Z"))
    assert capped[-1] == vfc.CAP and vfc.weeks_between(vfc.CAP) == 52 and len(capped) == 40
    assert all(c.dayofweek == 0 and c.hour == 0 for c in capped)
    assert vfc.MEASURE_START.dayofweek == 0 and vfc.DATA_START == pd.Timestamp("2026-09-21T00:00Z")


def test_gate_order() -> None:
    row = {"weeks": 13, "measured": 17, "position_days": 70.0, "entries": 10}
    assert vfc.gate(row) == "okunabilir"
    assert vfc.gate({**row, "measured": 16}) == "ölçülemez"
    assert vfc.gate({**row, "entries": 9}) == "değerlendirilemez"
    assert vfc.gate({**row, "position_days": 69.9}) == "değerlendirilemez"
    assert vfc.gate({**row, "weeks": 12}) == "değerlendirilemez"


def _synthetic(n: int, *, until: pd.Timestamp) -> tuple[list[str], dict, dict, dict]:
    """Fonlaması 10 gün yüksek / 4 gün negatif çevrimli, fiyatı sabit n sembol (likidasyon yok)."""
    symbols = [f"S{i:02d}-USDT-SWAP" for i in range(n)]
    stamps = pd.date_range("2026-09-01T00:00Z", until, freq="8h")
    phase = ((stamps - stamps[0]) / pd.Timedelta(days=1)).astype(int) % 14
    rates = np.where(phase < 10, 0.002, -0.006)
    hours = pd.date_range("2026-09-20T00:00Z", until, freq="1h")
    frame = pd.DataFrame({"open": 100.0, "high": 100.0, "low": 100.0, "close": 100.0}, index=hours)
    funding = {s: mfc.FundingSeries(symbol=s, times=stamps.as_unit("ns").asi8.copy(), rates=rates.copy()) for s in symbols}
    return symbols, funding, {s: frame for s in symbols}, {s: frame for s in symbols}


def test_count_rows_carry_only_counts_and_stop_at_first_pass() -> None:
    end = pd.Timestamp("2027-02-01T00:00Z")
    symbols, funding, perp, spot = _synthetic(20, until=end)
    taker = mfc.scenario_costs(load_config())["taker"]
    checkpoints = vfc.checkpoints_due(end)
    result = vfc.evaluate_checkpoints(checkpoints, symbols, funding, perp, spot, taker=taker, mm=0.005)
    assert result["first_pass"] == "2026-12-28T00:00:00+00:00"
    assert len(result["rows"]) == 1 and result["data_gap"] is None and result["verdict"] is None
    row = result["rows"][0]
    assert set(row) == set(vfc.COUNT_KEYS)
    assert row["entries"] >= 10 and row["position_days"] >= 70 and row["measured"] == 20
    banned = re.compile(r"return|funding|basis|cost|net|pnl|rate|signal", re.I)
    assert not any(banned.search(k) for r in result["rows"] for k in r)


def test_measured_share_gate_and_stale_archive() -> None:
    end = pd.Timestamp("2027-01-11T00:00Z")
    symbols, funding, perp, spot = _synthetic(20, until=end)
    taker = mfc.scenario_costs(load_config())["taker"]
    for s in symbols[:4]:                                          # 16/20 ölçülebilir
        funding[s] = mfc.FundingSeries(symbol=s, times=np.asarray([], dtype="int64"), rates=np.asarray([]))
    rows = vfc.evaluate_checkpoints(vfc.checkpoints_due(end), symbols, funding, perp, spot, taker=taker, mm=0.005)["rows"]
    assert [r["status"] for r in rows] == ["ölçülemez", "ölçülemez", "ölçülemez"]
    assert all(r["entries"] == 0 for r in rows)                    # ölçülemeyen noktada simülasyon koşmaz
    stale_end = pd.Timestamp("2026-12-20T00:00Z")
    symbols, funding, perp, spot = _synthetic(20, until=stale_end)
    res = vfc.evaluate_checkpoints(vfc.checkpoints_due(end), symbols, funding, perp, spot, taker=taker, mm=0.005)
    assert res["data_gap"] == "2026-12-28T00:00:00+00:00" and res["rows"] == []


def test_cap_without_pass_is_final_verdict() -> None:
    taker = mfc.scenario_costs(load_config())["taker"]
    symbols, funding, perp, spot = _synthetic(20, until=vfc.CAP)
    for s in symbols:                                             # eşik hiç aşılmaz → giriş yok
        funding[s].rates[:] = 0.0001
    res = vfc.evaluate_checkpoints([vfc.CAP], symbols, funding, perp, spot, taker=taker, mm=0.005)
    assert res["verdict"] == "KASADA DEĞERLENDİRİLEMEZ" and res["rows"][0]["entries"] == 0


def test_count_before_first_checkpoint_does_not_fetch(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    summary = tmp_path / "summary.md"
    assert vfc.main(["--stage", "count", "--now", "2026-10-02T00:00Z", "--summary", str(summary),
                     "--pins", str(tmp_path / "yok")]) == 0
    assert "henüz kontrol noktası yok" in summary.read_text(encoding="utf-8")


def test_workflow_schedules_only_the_count() -> None:
    body = (ROOT / ".github" / "workflows" / "measure-funding-carry-vault.yml").read_text(encoding="utf-8")
    assert body.count("- cron:") == 1                              # evren kaydı (§6w > 9) ile birlikte
    assert 'if [ "$EVENT" = "schedule" ] || [ "$EVENT" = "workflow_dispatch" ]; then\n            echo "stage=count"' in body
    assert '--stage "measure=fcv-measure*.run"' in body           # measure YALNIZCA tetikleyici dosyayla
    count_job = body.split("\n  count:\n", 1)[1].split("\n  measure:\n", 1)[0]
    assert "contents: write" not in count_job                       # sayım işi yazamaz
    assert "--stage count" in count_job and "--stage measure" not in count_job

"""Yeniden koşu kaydı (karar 59): dönem B'nin sonu serbest bir girdi olamaz.

Sınanan iki söz: (1) bir backtest workflow'u B sonunu (ya da herhangi bir dönem sınırını)
serbest girdi olarak AÇMAZ — yalnızca `rerun_record` alır; (2) commit'lenmiş her kayıt
kaynağına atıf taşır ve bir bar sınırına düşer.
"""

from __future__ import annotations

import os
from pathlib import Path

import pandas as pd
import pytest
import yaml

from scripts.rerun_record import RecordError, TRIGGER_DIR, main, parse_record

ROOT = Path(__file__).resolve().parent.parent
BACKTESTS = ("backtest-ema.yml", "backtest-dc.yml", "backtest-xsec.yml")
VALID = """# yorum
workflow=backtest-xsec.yml
source_run=#35578057311
source_record=docs/decisions.md > 58
b_end=2026-09-21T08:00:00+00:00
"""


@pytest.fixture()
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    (tmp_path / TRIGGER_DIR).mkdir(parents=True)
    monkeypatch.chdir(tmp_path)
    return tmp_path


def _write(name: str, body: str) -> Path:
    path = TRIGGER_DIR / name
    path.write_text(body, encoding="utf-8")
    return path


def test_valid_record_yields_the_recorded_end(repo: Path) -> None:
    path = _write("rerun-x.run", VALID)
    assert parse_record(path, workflow="backtest-xsec.yml") == pd.Timestamp("2026-09-21T08:00Z")


@pytest.mark.parametrize(
    ("body", "match"),
    [
        (VALID.replace("workflow=backtest-xsec.yml", "workflow=backtest-dc.yml"), "için yazılmış"),
        (VALID.replace("source_run=#35578057311\n", ""), "eksik alan: source_run"),
        (VALID.replace("source_record=docs/decisions.md > 58\n", ""), "source_record"),
        (VALID.replace("#35578057311", "dün"), "koşu numarası"),
        (VALID.replace("+00:00", ""), "saat dilimi"),
        (VALID + "b_end=2026-10-01T00:00:00+00:00\n", "tekrarlanan"),
    ],
)
def test_malformed_record_is_refused(repo: Path, body: str, match: str) -> None:
    with pytest.raises(RecordError, match=match):
        parse_record(_write("rerun-x.run", body), workflow="backtest-xsec.yml")


def test_record_outside_the_trigger_directory_is_refused(repo: Path) -> None:
    path = repo / "rerun-x.run"
    path.write_text(VALID, encoding="utf-8")
    with pytest.raises(RecordError, match="rerun-"):
        parse_record(Path("rerun-x.run"), workflow="backtest-xsec.yml")


def test_no_record_means_a_new_run_ending_now(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--workflow", "backtest-xsec.yml", ""]) == 0
    assert capsys.readouterr().out.strip() == ""


def test_cli_refuses_with_exit_code_2(repo: Path) -> None:
    _write("rerun-x.run", VALID)
    assert main(["--workflow", "backtest-dc.yml", str(TRIGGER_DIR / "rerun-x.run")]) == 2


@pytest.mark.parametrize("workflow", BACKTESTS)
def test_backtest_workflows_do_not_expose_a_free_period_bound(workflow: str) -> None:
    doc = yaml.safe_load((ROOT / ".github" / "workflows" / workflow).read_text(encoding="utf-8"))
    triggers = doc.get("on", doc.get(True))
    inputs = set((triggers.get("workflow_dispatch") or {}).get("inputs") or {})
    assert "rerun_record" in inputs
    bounds = {name for name in inputs if any(k in name for k in ("start", "end", "cutoff"))}
    assert not bounds, f"{workflow} serbest dönem sınırı açıyor: {bounds}"


def test_every_committed_record_validates_and_ends_on_a_bar_boundary() -> None:
    records = sorted((ROOT / TRIGGER_DIR).glob("rerun-*.run"))
    assert records, "kayıt bulunamadı — tarama boşsa test her zaman yeşil görünür"
    cwd = os.getcwd()
    os.chdir(ROOT)
    try:
        for path in records:
            fields = dict(
                line.split("=", 1) for line in path.read_text(encoding="utf-8").splitlines()
                if line and not line.startswith("#")
            )
            stamp = parse_record(path.relative_to(ROOT), workflow=fields["workflow"])
            assert stamp == stamp.floor("4h"), f"{path.name}: {stamp} bir 4H bar sınırı değil"
    finally:
        os.chdir(cwd)

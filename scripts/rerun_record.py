"""Yeniden koşu KAYDI: dönem B'nin sonunu serbest bir girdiden değil, commit'lenmiş bir dosyadan okur.

Karar 59: bir ALET düzeltmesinden sonraki yeniden koşu, orijinal koşunun B sonunda bitmeli
— yoksa fark onarımın değil eklenen verinin ölçüsü olur. Ama workflow girdisi olarak AÇIK
bir dönem sınırı, pencere seçmenin kapısıdır: bir değer denenir, sonuca bakılır, bir başkası
denenir. Bu yüzden B sonu hiçbir workflow'da serbest yazılmaz; yalnızca bu dosyadan gelir:

    .github/triggers/rerun-<ad>.run
        workflow=backtest-xsec.yml
        source_run=#35578057311
        source_record=<değerin kayıttaki yeri: yük alanı, log satırı>
        b_end=2026-09-21T08:00:00+00:00

Dosya koşudan ÖNCE commit edilir ve bir daha DEĞİŞTİRİLMEZ; değeri, kaynağına atıfla, git
geçmişinde tarih damgalı durur. Yeni (yeniden olmayan) koşularda kayıt verilmez ve B sonu
koşu anıdır — bu betik hiçbir şey basmaz.

Çıktı stdout'a tek satır: B sonunun ISO damgası (kayıt yoksa boş). Hatalı kayıt → çıkış 2.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Sequence

# Öteki betiklerin kalıbı (tests/test_script_entrypoints.py): `core` ithal edilmese de tek kalıp.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd  # noqa: E402

TRIGGER_DIR = Path(".github/triggers")
NAME = re.compile(r"^rerun-[a-z0-9-]+\.run$")
REQUIRED = ("workflow", "source_run", "source_record", "b_end")


class RecordError(ValueError):
    """Kayıt kuralı ihlali: yeniden koşu başlatılmaz."""


def parse_record(path: Path, *, workflow: str) -> pd.Timestamp:
    """Kaydı doğrular ve B sonunu döndürür."""
    if path.parent != TRIGGER_DIR or not NAME.match(path.name):
        raise RecordError(f"kayıt {TRIGGER_DIR}/rerun-*.run olmalı: {path}")
    if not path.is_file():
        raise RecordError(f"kayıt dosyası yok (commit edilmemiş olabilir): {path}")
    fields: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, sep, value = line.partition("=")
        if not sep:
            raise RecordError(f"anahtar=değer olmayan satır: {line!r}")
        if key.strip() in fields:
            raise RecordError(f"tekrarlanan anahtar: {key.strip()}")
        fields[key.strip()] = value.strip()
    missing = [key for key in REQUIRED if not fields.get(key)]
    if missing:
        raise RecordError(f"eksik alan: {', '.join(missing)}")
    if fields["workflow"] != workflow:
        raise RecordError(f"kayıt {fields['workflow']} için yazılmış, çağıran {workflow}")
    if not re.fullmatch(r"#\d+", fields["source_run"]):
        raise RecordError(f"source_run bir koşu numarası olmalı (#123…): {fields['source_run']}")
    try:
        stamp = pd.Timestamp(fields["b_end"])
    except ValueError as exc:
        raise RecordError(f"b_end ISO damgası değil: {fields['b_end']}") from exc
    if stamp.tzinfo is None:
        raise RecordError(f"b_end saat dilimi taşımalı (UTC): {fields['b_end']}")
    return stamp.tz_convert("UTC")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--workflow", required=True, help="çağıran workflow dosyasının adı")
    parser.add_argument("record", nargs="?", default="", help="kayıt yolu; boş = yeni koşu")
    args = parser.parse_args(argv)
    if not args.record:
        print("")
        return 0
    try:
        stamp = parse_record(Path(args.record), workflow=args.workflow)
    except RecordError as exc:
        print(f"YENİDEN KOŞU KAYDI REDDEDİLDİ: {exc}", file=sys.stderr)
        return 2
    print(stamp.isoformat())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

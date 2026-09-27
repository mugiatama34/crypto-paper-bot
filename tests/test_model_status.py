"""`docs/data/model_status.json`: durum etiketinin TEK kaynağı (docs/backtest.md > 7.8, karar 68).

Sınanan: (1) dosya geçerli JSON ve durumlar izinli kümede; (2) her kayıt bir karar/kayıt referansı
taşır — bir model sessizce "doğrulandı"ya çıkamaz; (3) kayıttaki her model gerçekten var; (4) sayfaların
okuduğu durum kümesi (`docs/shared.js`) ile dosyanın kümesi aynı; (5) iki sayfa da durumu yükten okur.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from strategies.registry import REGISTRY

STATUS_FILE = Path("docs/data/model_status.json")
SHARED_JS = Path("docs/shared.js").read_text(encoding="utf-8")
ALLOWED = {"deneme", "dogrulandi"}   # "dogrulanmamis" varsayılandır ve YAZILMAZ


def _payload() -> dict:
    return json.loads(STATUS_FILE.read_text(encoding="utf-8"))


def test_file_is_valid_and_statuses_allowed():
    models = _payload()["models"]
    assert isinstance(models, dict)
    for name, entry in models.items():
        assert entry["status"] in ALLOWED, name


def test_every_entry_cites_a_record():
    for name, entry in _payload()["models"].items():
        assert str(entry.get("record", "")).strip(), f"{name}: kayıt referansı yok"


def test_entries_name_existing_models():
    for name in _payload()["models"]:
        assert name in REGISTRY, name


def test_shared_js_status_keys_match():
    block = SHARED_JS[SHARED_JS.index("const MODEL_STATUS_DEFS"):SHARED_JS.index("let MODEL_STATUS")]
    keys = set(re.findall(r"^\s+(\w+): \{ label:", block, re.M))
    assert keys == ALLOWED | {"dogrulanmamis"}
    assert 'return entry && MODEL_STATUS_DEFS[entry.status] ? entry.status : "dogrulanmamis";' in SHARED_JS


def test_pages_load_status_before_rendering():
    for page in ("docs/index.html", "docs/positions.html"):
        text = Path(page).read_text(encoding="utf-8")
        assert "Promise.all([loadModelStatus()]" in text, page
        assert "statusBadge(" in text, page

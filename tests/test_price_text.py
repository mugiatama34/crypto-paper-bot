"""`core/price_text.py`: kesinlik kuralının TEK kopyası (6p > TADİLAT-4, 6q > TADİLAT-3)."""

from __future__ import annotations

import pytest

from core import price_text
from scripts import measure_btc_veto


def test_truncation_only_not_rounding():
    assert price_text.truncates_to(95.096, "95.09")
    assert not price_text.truncates_to(95.096, "95.10")
    assert price_text.truncates_to(100.101, "100.1")


def test_decimals_come_from_text_not_float():
    assert price_text.decimals_of("100.10") == 2 and price_text.decimals_of("98") == 0
    assert price_text.truncates_to(100.105, "100.10")
    assert not price_text.truncates_to(100.115, "100.10")
    with pytest.raises(ValueError):
        price_text.decimals_of("1e-5")


def test_btc_veto_uses_the_shared_copy():
    assert measure_btc_veto.truncates_to is price_text.truncates_to
    assert measure_btc_veto.decimals_of is price_text.decimals_of

"""Sürekli koşu: zamanlama ve adım sırası — turun İÇİNE hiçbir şey karışmıyor.

`scripts/live_loop.py` bir tetikleyicidir, bir motor değil. Bu yüzden testler iki şeyi
çiviler: (1) uyanma anı takvime (epoch'a) hizalıdır, yani süreç ne zaman başlatılırsa
başlatılsın turlar aynı saniyelerde koşar; (2) adım sırası workflow'la aynıdır — ölçüm
önce, bildirim sonra — ve bildirimin hatası turu düşürmez.
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone

import pytest

from core.config import load_config
from core.layers import resolve_layer
from scripts.live_loop import Step, build_steps, next_wakeup, run_round

UTC = timezone.utc


def test_wakeup_is_aligned_to_the_bar_grid() -> None:
    now = datetime(2026, 9, 17, 12, 7, 31, tzinfo=UTC)

    wakeup = next_wakeup(now, duration=timedelta(minutes=15), settle=20.0)

    assert wakeup == datetime(2026, 9, 17, 12, 15, 20, tzinfo=UTC)


def test_wakeup_on_a_boundary_waits_for_the_next_bar() -> None:
    """Sınırın kendisinde koşmak "yeni bar yok" turu üretirdi: bar henüz yayımlanmadı."""
    now = datetime(2026, 9, 17, 12, 15, 0, tzinfo=UTC)

    wakeup = next_wakeup(now, duration=timedelta(minutes=15), settle=0.0)

    assert wakeup == datetime(2026, 9, 17, 12, 30, 0, tzinfo=UTC)


def test_wakeup_does_not_drift_with_the_start_time() -> None:
    """Süreç saniyenin ortasında başlasa da ızgara aynı kalır: yeniden başlatma kaymaz."""
    grid = {
        next_wakeup(
            datetime(2026, 9, 17, 12, 0, second, tzinfo=UTC),
            duration=timedelta(hours=4), settle=15.0,
        )
        for second in (0, 7, 31, 59)
    }

    assert grid == {datetime(2026, 9, 17, 16, 0, 15, tzinfo=UTC)}


def test_zero_duration_is_rejected() -> None:
    with pytest.raises(ValueError):
        next_wakeup(datetime.now(UTC), duration=timedelta(0), settle=1.0)


def test_scalp_round_notifies_with_the_signal_script() -> None:
    layer = resolve_layer(load_config(), "scalp")

    steps = build_steps(layer, python=sys.executable, notify=True, log_level="INFO")

    assert [step.name for step in steps] == ["tur", "bildirim"]
    assert steps[0].critical and not steps[1].critical
    assert steps[0].command[1].endswith("main.py")
    assert steps[1].command[1].endswith("telegram_signals.py")


def test_base_round_notifies_with_the_daily_report() -> None:
    layer = resolve_layer(load_config(), "base")

    steps = build_steps(layer, python=sys.executable, notify=True, log_level="INFO")

    assert steps[1].command[1].endswith("telegram_report.py")


def test_notifications_can_be_switched_off() -> None:
    layer = resolve_layer(load_config(), "scalp")

    steps = build_steps(layer, python=sys.executable, notify=False, log_level="INFO")

    assert [step.name for step in steps] == ["tur"]


def test_a_failing_notification_does_not_fail_the_round() -> None:
    steps = [
        Step(name="tur", command=[sys.executable, "-c", "pass"], critical=True),
        Step(name="bildirim", command=[sys.executable, "-c", "raise SystemExit(1)"], critical=False),
    ]

    assert run_round(steps, timeout=60.0) is True


def test_a_failing_round_stops_the_remaining_steps() -> None:
    steps = [
        Step(name="tur", command=[sys.executable, "-c", "raise SystemExit(2)"], critical=True),
        Step(name="bildirim", command=[sys.executable, "-c", "pass"], critical=False),
    ]

    assert run_round(steps, timeout=60.0) is False

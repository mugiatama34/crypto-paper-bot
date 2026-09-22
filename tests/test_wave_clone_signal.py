"""Model 21'in sinyali: kaynağın HER kuralı için üreten ve ÜRETMEYEN birer senaryo.

Bu dosyanın sözleşmesi iki cümledir:

1. **Kaynağın her kuralı, kurulum ÜRETEN ve ÜRETMEYEN birer senaryoyla çivilenir.**
   Yalnızca "üretiyor" tarafını test etmek, kuralın gerçekten bir kapı olduğunu
   göstermez — kaldırılsa da testler yeşil kalırdı.
2. **PARİTE bir altın dosyayla kanıtlanır** (`tests/data/wave_source_parity.json`):
   dosya kaynağın KENDİ kodu (`klonnist/Hasanwavebot @ fa888b7`, `wave_detector.py`)
   sabit OHLC dizileri üzerinde koşturularak üretildi ve 8 seri × 12 kombinasyon = 96
   hücre taşır (42 kurulum, 33 geçerli geometri, 9 geometri reddi, iki yön). Bu,
   docs/backtest.md > 6h'nin **Kapı 0**'ıdır: parite düşerse koşunun hiçbir sayısı
   okunmaz.

**Altın dosya neden kaynağı import etmiyor.** Kaynak deposu CI'da yoktur ve bir test
dış bir çalışma kopyasına bağlı olamaz. Dosya bağımsızdır ve provenance'ı (`_provenance`)
kendi başlığında taşır: hangi depo, hangi commit, hangi fonksiyonlar, hangi çağıran
kurallar. Yeniden üretmek isteyen o commit'i checkout eder ve aynı dizileri koşturur.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Sequence

import pandas as pd
import pytest

from core.indicators import average_true_range
from strategies.wave import clone_signal
from tests.helpers_market import market

SYMBOL = "BTC-USDT-SWAP"
START = pd.Timestamp("2026-01-01 00:00:00", tz="UTC")

# Kaynağın sabitleri (config.yaml > wave.clone). Testte AÇIKÇA yazılır ki config'te bir
# değer değişirse test "hangi kuralı ölçüyordum" sorusunu hâlâ cevaplayabilsin.
ATR_PERIOD = 14
SL_MULT = 0.15
MIN_DEV = 0.05
RETRACE_MIN = 0.236
RETRACE_MAX = 0.886
WINDOW_BARS = 300

PARITY = json.loads((Path(__file__).parent / "data" / "wave_source_parity.json").read_text())


def params(
    *,
    deviation_pct: float = 1.2,
    tp_mult: float = 1.618,
    sl_mult: float = SL_MULT,
    retrace_min: float = RETRACE_MIN,
    retrace_max: float = RETRACE_MAX,
) -> clone_signal.WaveParams:
    return clone_signal.WaveParams(
        deviation_pct=deviation_pct,
        tp_mult=tp_mult,
        sl_mult=sl_mult,
        retrace_min=retrace_min,
        retrace_max=retrace_max,
    )


def wave_frame(closes: Sequence[float], *, wick: float = 0.004) -> pd.DataFrame:
    """Altın dosyanın ürettiği çerçevenin BİREBİR aynısı, zaman indeksli hâliyle.

    `tests.helpers_market.frame` kullanılmaz çünkü orada high/low kapanışın SABİT bir
    mutlak mesafesindedir (`spread`); kaynağın zigzag'ı ORANSAL eşiklerle çalışır ve
    sabit mesafe, fiyat 100'den 260'a çıkan bir seride barın aralığını sessizce
    daraltırdı. Fitil burada oransaldır — üreteçle aynı kural.
    """
    values = [float(c) for c in closes]
    opens = [values[0]] + values[:-1]
    index = pd.date_range(START, periods=len(values), freq="15min", tz="UTC", name="ts")
    return pd.DataFrame(
        {
            "open": opens,
            "high": [max(o, c) * (1 + wick) for o, c in zip(opens, values)],
            "low": [min(o, c) * (1 - wick) for o, c in zip(opens, values)],
            "close": values,
            "volume": [1000.0 + i for i in range(len(values))],
        },
        index=index,
    )


def detect(frame: pd.DataFrame, *, wave_params: clone_signal.WaveParams | None = None):
    return clone_signal.detect(
        frame,
        symbol=SYMBOL,
        as_of=frame.index[-1],
        params=wave_params or params(),
        atr_period=ATR_PERIOD,
        window_bars=WINDOW_BARS,
        min_deviation_pct=MIN_DEV,
    )


# --------------------------------------------------------------------------- #
# KAPI 0 — kaynakla PARİTE
# --------------------------------------------------------------------------- #
def _parity_cells():
    for case in PARITY["cases"]:
        for cell in case["combos"]:
            yield pytest.param(case, cell, id=f"{case['name']}-dev{cell['deviation_pct']}-tp{cell['tp_mult']}")


def test_parity_fixture_covers_every_branch() -> None:
    """Altın dosya boş ya da tek yönlü çıkarsa parite kapısı sessizce geçerdi.

    Kendi verisini tarayan bir testte "hiç eşleşme yok" her zaman yeşil görünür
    (`tests/test_script_entrypoints.py`nin aynı gerekçesi), bu yüzden kapsam AYRICA
    sınanır: iki yön, kurulum üreten ve üretmeyen seriler, geometri reddi ve eşik
    tabanının (atr%=0) tetiklendiği en az bir hücre bulunmalı.
    """
    cells = [cell for case in PARITY["cases"] for cell in case["combos"]]
    assert len(cells) == 96, "altın dosya 8 seri × 12 kombinasyon taşımalı"
    setups = [cell["setup"] for cell in cells if cell["setup"] is not None]
    assert {s["direction"] for s in setups} == {"BUY", "SELL"}, "iki yön de sınanmalı"
    assert any(cell["setup"] is None for cell in cells), "kurulum ÜRETMEYEN hücre yok"
    assert any(
        cell["setup"] is not None and cell["geometry_valid"] is False for cell in cells
    ), "geometri kapısının BAĞLADIĞI hücre yok"
    assert any(
        cell["effective_deviation_pct"] == pytest.approx(MIN_DEV) for cell in cells
    ), "eşik TABANININ tetiklendiği hücre yok"
    assert PARITY["_provenance"]["source_commit"].startswith("fa888b7")


# Parite toleransı. **Bit düzeyinde eşitlik ARANMAZ ve aranmamalıdır:** kaynak ATR'yi
# pandas `rolling(14).mean()` ile, ev tanımı numpy `.mean()` ile toplar; ikisi matematiksel
# olarak aynı, kayan nokta TOPLAMA SIRASI olarak farklıdır ve ~1e-12 bağıl fark bırakır.
# 1e-9, en ince kitaplı sembolde bile bir tick'in milyonda birinden küçüktür — yani hiçbir
# pivot, eşik ya da geometri KARARINI değiştiremez. Kararların kendisi (pivot indeksleri,
# pivot tipleri, yön, kurulum var/yok, geometri geçti/geçmedi) TAM eşitlikle sınanır.
PARITY_REL = 1e-9


@pytest.mark.parametrize("case,cell", list(_parity_cells()))
def test_source_parity(case: dict, cell: dict) -> None:
    """Pivot listesi, yön, seviyeler ve geometri kararı kaynakla aynı olmalı."""
    frame = wave_frame(case["closes"], wick=case["wick"])
    wave_params = params(deviation_pct=cell["deviation_pct"], tp_mult=cell["tp_mult"])

    assert clone_signal.atr_pct(frame, ATR_PERIOD) == pytest.approx(
        case["atr_pct"], rel=PARITY_REL, abs=1e-12
    )
    effective = max(cell["deviation_pct"] * case["atr_pct"], MIN_DEV)
    assert effective == pytest.approx(cell["effective_deviation_pct"], rel=PARITY_REL)

    pivots = clone_signal.zigzag_pivots(frame, deviation_pct=effective)
    assert [p.index for p in pivots] == [row["index"] for row in cell["pivots"]]
    assert [p.kind for p in pivots] == [row["kind"] for row in cell["pivots"]]
    assert [p.price for p in pivots] == pytest.approx(
        [row["price"] for row in cell["pivots"]], rel=PARITY_REL
    )

    setup, reason = clone_signal.detect_wave3_setup(pivots, wave_params)
    if cell["setup"] is None:
        assert setup is None
        assert reason != clone_signal.SETUP
        return

    expected = cell["setup"]
    assert setup is not None, f"kaynak kurulum buldu, biz bulamadık ({reason})"
    assert setup.direction == ("long" if expected["direction"] == "BUY" else "short")
    for name, pivot in (("p0", setup.p0), ("p1", setup.p1), ("p2", setup.p2)):
        assert pivot.index == expected[name]["index"]
        assert pivot.price == pytest.approx(expected[name]["price"], rel=PARITY_REL)
        assert pivot.kind == expected[name]["kind"]
    assert setup.wave1_len == pytest.approx(expected["wave1_len"], rel=PARITY_REL)
    assert setup.retrace * 100 == pytest.approx(expected["retrace_pct"], rel=PARITY_REL)

    entry, target, stop = clone_signal.build_signal_levels(
        setup, wave_params, float(frame["close"].iloc[-1])
    )
    assert [entry, target, stop] == pytest.approx(cell["levels"], rel=PARITY_REL)

    # Geometri kararı: kaynak kurulumu açar mı? `detect` aynı kararı vermeli.
    candidate, detect_reason = detect(frame, wave_params=wave_params)
    if cell["geometry_valid"]:
        assert candidate is not None and detect_reason == clone_signal.SETUP
        assert candidate.entry_price == pytest.approx(entry, rel=PARITY_REL)
        assert candidate.target_price == pytest.approx(target, rel=PARITY_REL)
        assert candidate.stop_price == pytest.approx(stop, rel=PARITY_REL)
    else:
        assert candidate is None
        assert detect_reason == clone_signal.BAD_GEOMETRY


def test_atr_pct_matches_the_projects_single_atr_definition() -> None:
    """`atr_pct` ikinci bir ATR YAZMAZ: ev tanımıyla birebir aynı sayıyı verir.

    Kaynağın ATR'si `true_range.rolling(14).mean().iloc[-1]`dir ve 15 bardan uzun her
    çerçevede `average_true_range(..., "simple")`ın birebir aynısıdır — parite bu yüzden
    ikinci bir uygulama olmadan sağlanır (modül docstring'i).
    """
    frame = wave_frame(PARITY["cases"][0]["closes"])
    atr = average_true_range(frame, ATR_PERIOD, smoothing="simple")
    assert atr is not None
    expected = atr / float(frame["close"].iloc[-1]) * 100.0
    assert clone_signal.atr_pct(frame, ATR_PERIOD) == pytest.approx(expected, rel=1e-15)


def test_atr_pct_falls_back_to_zero_when_atr_is_undefined() -> None:
    """Yeterli bar yokken kaynağın NaN dalıyla aynı sonuç: 0.0 (eşik tabana düşer)."""
    frame = wave_frame([100.0, 101.0, 100.5])
    assert average_true_range(frame, ATR_PERIOD, smoothing="simple") is None
    assert clone_signal.atr_pct(frame, ATR_PERIOD) == 0.0


# --------------------------------------------------------------------------- #
# Kural kural: ÜRETEN ve ÜRETMEYEN senaryo
# --------------------------------------------------------------------------- #
def _leg(start: float, end: float, bars: int) -> list[float]:
    step = (end - start) / bars
    return [start + step * (k + 1) for k in range(bars)]


def buy_path(*, retrace_to: float = 107.0, tail: float = 112.0) -> list[float]:
    """L-H-L deseni: 100 -> 92 (p0) -> 118 (p1) -> `retrace_to` (p2) -> `tail` (canlı uç)."""
    closes = [100.0]
    closes += _leg(100.0, 92.0, 12)
    closes += _leg(92.0, 118.0, 20)
    closes += _leg(118.0, retrace_to, 12)
    closes += _leg(retrace_to, tail, 6)
    return closes


def test_unconfirmed_last_pivot_is_not_used() -> None:
    """Son pivot ONAYSIZDIR: kurulum `pivots[:-1]`den okunur.

    Kanıt doğrudan: aynı pivot listesinde son eleman p2 sayılsaydı ÜÇLÜ kayar ve
    `detect_wave3_setup` başka bir p0/p1/p2 döndürürdü.
    """
    frame = wave_frame(buy_path())
    volatility = clone_signal.atr_pct(frame, ATR_PERIOD)
    effective = max(1.2 * volatility, MIN_DEV)
    pivots = clone_signal.zigzag_pivots(frame, deviation_pct=effective)
    setup, reason = clone_signal.detect_wave3_setup(pivots, params())

    assert reason == clone_signal.SETUP and setup is not None
    confirmed = pivots[:-1]
    assert (setup.p0, setup.p1, setup.p2) == (confirmed[-3], confirmed[-2], confirmed[-1])
    assert setup.p2 is not pivots[-1], "onaysız uç p2 olarak kullanılmış"


def test_fewer_than_three_confirmed_pivots_yields_no_setup() -> None:
    """Onaylı pivot 3'ten azsa kurulum yok ve sebep `pivot_az`."""
    frame = wave_frame(_leg(100.0, 130.0, 40))  # tek yönlü: tek onaylı pivot bile yok
    candidate, reason = detect(frame)
    assert candidate is None
    assert reason == clone_signal.FEW_PIVOTS


def test_retrace_inside_the_window_produces_a_setup() -> None:
    frame = wave_frame(buy_path(retrace_to=107.0))
    candidate, reason = detect(frame)
    assert reason == clone_signal.SETUP and candidate is not None
    assert RETRACE_MIN <= candidate.setup.retrace <= RETRACE_MAX
    assert candidate.direction == "long"


@pytest.mark.parametrize(
    "retrace_to,tail",
    [
        (116.0, 117.0),  # ~%8: retrace_min altında
        (93.5, 96.0),    # ~%94: retrace_max üstünde
    ],
)
def test_retrace_outside_the_window_is_rejected(retrace_to: float, tail: float) -> None:
    """Sınırlar İKİ yandan da kapıdır: sığ retrace de derin retrace de elenir."""
    frame = wave_frame(buy_path(retrace_to=retrace_to, tail=tail))
    candidate, reason = detect(frame)
    assert candidate is None
    assert reason in (clone_signal.RETRACE_OUT, clone_signal.OVERLAP)


def test_retrace_bounds_are_inclusive() -> None:
    """Sınır DEĞERİ geçer (kaynakta `min <= r <= max`); bir kıl payı dışı elenir."""
    frame = wave_frame(buy_path())
    volatility = clone_signal.atr_pct(frame, ATR_PERIOD)
    effective = max(1.2 * volatility, MIN_DEV)
    pivots = clone_signal.zigzag_pivots(frame, deviation_pct=effective)
    setup, _ = clone_signal.detect_wave3_setup(pivots, params())
    assert setup is not None
    exact = setup.retrace

    inside, reason = clone_signal.detect_wave3_setup(
        pivots, params(retrace_min=exact, retrace_max=exact)
    )
    assert inside is not None and reason == clone_signal.SETUP

    outside, reason = clone_signal.detect_wave3_setup(
        pivots, params(retrace_min=exact + 1e-9, retrace_max=1.0)
    )
    assert outside is None and reason == clone_signal.RETRACE_OUT


def test_wave2_overlapping_wave1_start_is_rejected() -> None:
    """`p2` `p0`'ı aşarsa kurulum yok: sayım bir Elliott dürtüsü değildir.

    Retrace kapısı tek başına bunu yakalamaz — bu yüzden ÖRTÜŞME ayrı bir kapıdır ve
    testte retrace aralığı bilerek `[0, 2]`ye genişletilir ki eleyen kapının hangisi
    olduğu belirsiz kalmasın.
    """
    wide = params(retrace_min=1e-9, retrace_max=1.999)
    frame = wave_frame(buy_path(retrace_to=88.0, tail=90.0))  # p2 (88) < p0 (92)
    volatility = clone_signal.atr_pct(frame, ATR_PERIOD)
    pivots = clone_signal.zigzag_pivots(
        frame, deviation_pct=max(1.2 * volatility, MIN_DEV)
    )
    setup, reason = clone_signal.detect_wave3_setup(pivots, wide)
    assert setup is None
    assert reason == clone_signal.OVERLAP

    # Aynı geometri, p2 p0'ın ÜSTÜNDE: aynı kapı artık geçiyor.
    ok_frame = wave_frame(buy_path(retrace_to=96.0, tail=99.0))
    ok_volatility = clone_signal.atr_pct(ok_frame, ATR_PERIOD)
    ok_pivots = clone_signal.zigzag_pivots(
        ok_frame, deviation_pct=max(1.2 * ok_volatility, MIN_DEV)
    )
    ok_setup, ok_reason = clone_signal.detect_wave3_setup(ok_pivots, wide)
    assert ok_reason == clone_signal.SETUP and ok_setup is not None
    assert ok_setup.p2.price > ok_setup.p0.price


def test_geometry_gate_skips_setups_whose_entry_passed_the_target() -> None:
    """`sl < entry < tp` sağlanmazsa kurulum ATLANIR (kaynağın kendi kapısı)."""
    frame = wave_frame(buy_path(retrace_to=107.0, tail=107.0)[:-6] + _leg(107.0, 260.0, 10))
    candidate, reason = detect(frame)
    assert candidate is None
    assert reason == clone_signal.BAD_GEOMETRY


def test_levels_are_projected_from_p2_and_entry_is_the_close() -> None:
    """Seviyeler `p2 ± dalga1 × çarpan`, giriş ise sinyal barının KAPANIŞI."""
    frame = wave_frame(buy_path())
    wave_params = params(tp_mult=2.0)
    candidate, reason = detect(frame, wave_params=wave_params)
    assert reason == clone_signal.SETUP and candidate is not None

    p2 = candidate.setup.p2.price
    wave1 = candidate.setup.wave1_len
    assert candidate.entry_price == pytest.approx(float(frame["close"].iloc[-1]))
    assert candidate.target_price == pytest.approx(p2 + wave1 * 2.0)
    assert candidate.stop_price == pytest.approx(p2 - wave1 * SL_MULT)


def test_short_levels_mirror_the_long_geometry() -> None:
    closes = [100.0] + _leg(100.0, 110.0, 12) + _leg(110.0, 84.0, 20)
    closes += _leg(84.0, 95.0, 12) + _leg(95.0, 90.0, 6)
    candidate, reason = detect(wave_frame(closes))
    assert reason == clone_signal.SETUP and candidate is not None
    assert candidate.direction == "short"
    p2 = candidate.setup.p2.price
    wave1 = candidate.setup.wave1_len
    assert candidate.target_price == pytest.approx(p2 - wave1 * 1.618)
    assert candidate.stop_price == pytest.approx(p2 + wave1 * SL_MULT)
    assert candidate.target_price < candidate.entry_price < candidate.stop_price


# --------------------------------------------------------------------------- #
# ATR% ölçeklemesi ve eşik tabanı
# --------------------------------------------------------------------------- #
def test_threshold_scales_with_atr_percent() -> None:
    """Eşik `deviation × atr%`tir: katsayı iki katına çıkınca eşik de iki katına çıkar."""
    frame = wave_frame(buy_path())
    volatility = clone_signal.atr_pct(frame, ATR_PERIOD)
    assert volatility > MIN_DEV, "fikstür tabanın üstünde olmalı, yoksa ölçek test edilemez"

    low, _ = detect(frame, wave_params=params(deviation_pct=0.8))
    high, _ = detect(frame, wave_params=params(deviation_pct=2.5))
    assert low is not None and high is not None
    assert low.deviation_pct == pytest.approx(0.8 * volatility)
    assert high.deviation_pct == pytest.approx(2.5 * volatility)
    assert high.deviation_pct > low.deviation_pct


def test_threshold_never_falls_below_the_floor() -> None:
    """ATR ölçülemediğinde/sıfırken eşik TABANDIR (%0.05), sıfır değil.

    Sıfır eşik `zigzag_pivots`i her barda pivot üretmeye zorlar; kaynağın `max(..., 0.05)`
    dalı tam olarak bunu engeller.
    """
    flat = wave_frame([100.0] * 40, wick=0.0)
    assert clone_signal.atr_pct(flat, ATR_PERIOD) == 0.0
    pivots = clone_signal.zigzag_pivots(flat, deviation_pct=MIN_DEV)
    assert pivots, "eşik tabanıyla bile pivot listesi (canlı uç) dönmeli"
    candidate, reason = detect(flat, wave_params=params(deviation_pct=0.8))
    assert candidate is None and reason == clone_signal.FEW_PIVOTS


def test_zero_threshold_is_a_programming_error_not_a_market_state() -> None:
    with pytest.raises(ValueError, match="deviation_pct"):
        clone_signal.zigzag_pivots(wave_frame(buy_path()), deviation_pct=0.0)


# --------------------------------------------------------------------------- #
# Look-ahead ve pencere (kural 12)
# --------------------------------------------------------------------------- #
def test_bars_after_as_of_are_never_read() -> None:
    """`as_of`'tan sonrası kesilir; sonraki barları eklemek kurulumu DEĞİŞTİRMEZ."""
    closes = buy_path()
    early = wave_frame(closes)
    extended = wave_frame(closes + _leg(closes[-1], 300.0, 20))

    baseline, baseline_reason = detect(early)
    cut, cut_reason = clone_signal.detect(
        extended,
        symbol=SYMBOL,
        as_of=early.index[-1],
        params=params(),
        atr_period=ATR_PERIOD,
        window_bars=WINDOW_BARS,
        min_deviation_pct=MIN_DEV,
    )
    assert (baseline_reason, cut_reason) == (clone_signal.SETUP, clone_signal.SETUP)
    assert baseline is not None and cut is not None
    assert (cut.entry_price, cut.stop_price, cut.target_price) == pytest.approx(
        (baseline.entry_price, baseline.stop_price, baseline.target_price)
    )


def test_symbol_without_the_as_of_bar_is_out_of_the_universe_that_round() -> None:
    frame = wave_frame(buy_path())
    candidate, reason = clone_signal.detect(
        frame,
        symbol=SYMBOL,
        as_of=frame.index[-1] + pd.Timedelta(minutes=15),
        params=params(),
        atr_period=ATR_PERIOD,
        window_bars=WINDOW_BARS,
        min_deviation_pct=MIN_DEV,
    )
    assert candidate is None and reason == clone_signal.NO_BAR


def test_missing_frame_is_reported_as_no_bar() -> None:
    for empty in (None, pd.DataFrame(columns=["open", "high", "low", "close", "volume"])):
        candidate, reason = clone_signal.detect(
            empty,
            symbol=SYMBOL,
            as_of=START,
            params=params(),
            atr_period=ATR_PERIOD,
            window_bars=WINDOW_BARS,
            min_deviation_pct=MIN_DEV,
        )
        assert candidate is None and reason == clone_signal.NO_BAR


def test_window_bars_is_a_fidelity_constraint_not_a_depth_setting() -> None:
    """Pencere UZUNLUĞU çıktıyı değiştirir: çapa pencerenin İLK barıdır.

    Bu yüzden `window_bars` bir derinlik ayarı değildir ve harness'ın `--history-bars`
    bayrağıyla karıştırılamaz (config yorumunun kanıtı).
    """
    long_history = wave_frame(_leg(50.0, 100.0, 200) + buy_path())
    narrow, _ = clone_signal.detect(
        long_history, symbol=SYMBOL, as_of=long_history.index[-1], params=params(),
        atr_period=ATR_PERIOD, window_bars=51, min_deviation_pct=MIN_DEV,
    )
    wide, _ = clone_signal.detect(
        long_history, symbol=SYMBOL, as_of=long_history.index[-1], params=params(),
        atr_period=ATR_PERIOD, window_bars=251, min_deviation_pct=MIN_DEV,
    )
    assert narrow is not None or wide is not None, "fikstür en az birinde kurulum vermeli"
    if narrow is not None and wide is not None:
        assert narrow.pivots != wide.pivots or narrow.setup.p0.index != wide.setup.p0.index


# --------------------------------------------------------------------------- #
# Survey — denetim izi (kural 15)
# --------------------------------------------------------------------------- #
def test_every_examined_symbol_gets_exactly_one_reason() -> None:
    counts = clone_signal.empty_counts()
    assert set(counts) == set(clone_signal.REASONS)
    for frame in (wave_frame(buy_path()), wave_frame(_leg(100.0, 130.0, 40))):
        _, reason = detect(frame)
        counts[reason] += 1
    survey = clone_signal.Survey(counts=counts)
    assert survey.examined == 2 == sum(counts.values())
    assert survey.candidates == 1


def test_survey_describe_lists_only_nonzero_reasons() -> None:
    counts = clone_signal.empty_counts()
    counts[clone_signal.RETRACE_OUT] = 3
    text = clone_signal.Survey(counts=counts).describe()
    assert "retrace_disi=3" in text
    assert "kurulum" not in text


def test_house_zigzag_is_a_different_algorithm_and_is_not_reused() -> None:
    """Ev fonksiyonu aynı adı taşır ama BAŞKA pivotlar üretir — bu yüzden import edilmez.

    Test modül docstring'inin iddiasını çivileyerek durur: bir gün biri "aynı işi yapıyor"
    diye kaynağın portunu silip ev fonksiyonunu çağırırsa, kırmızıya düşen bu satır olur.
    """
    from core import indicators as house

    frame = wave_frame(buy_path())
    effective = max(1.2 * clone_signal.atr_pct(frame, ATR_PERIOD), MIN_DEV)
    ours = clone_signal.zigzag_pivots(frame, deviation_pct=effective)
    theirs = house.zigzag_pivots(frame, pct_threshold=effective / 100.0, min_leg_bars=0)
    assert [(p.price, p.kind) for p in ours] != [
        (p.price, "H" if p.kind == "high" else "L") for p in theirs
    ]


def test_pivot_kinds_alternate_within_the_confirmed_list() -> None:
    """Onaylı pivotlar dip/zirve olarak SIRAYLA gelir; desen kontrolü buna dayanır."""
    frame = wave_frame(PARITY["cases"][4]["closes"])  # random_walk: çok pivot
    effective = max(1.2 * clone_signal.atr_pct(frame, ATR_PERIOD), MIN_DEV)
    pivots = clone_signal.zigzag_pivots(frame, deviation_pct=effective)
    confirmed = pivots[:-1]
    assert len(confirmed) >= 3
    kinds = [p.kind for p in confirmed]
    assert all(a != b for a, b in zip(kinds, kinds[1:])), kinds
    assert all(math.isfinite(p.price) for p in pivots)

"""Önbellek + pencere hata sınıfı (docs/decisions.md > 59).

İki bilinen arıza burada BİREBİR yeniden üretilir ve onarımın ikisini de kapattığı sınanır:

1. `backtest-dc` #35839008498 — önbellek başka bir koşunun `now`ından (2026-09-18) kalma
   barları taşıyordu; `fetch_ohlcv` onları kesmedi, `_anchor_as_of` `as_of`u son bara koydu
   ve dönem A penceresi 2026-09'a taştı.
2. `measure-timesfm` #35861965835 — P1'in yazdığı sığ ve taze önbellek, dönem A'nın daha
   derin ve geçmişteki isteğini "zaten güncel" saydırdı; A `now`dan önce kapanmış TEK bar
   almadı.

Ayrıca `backtest-xsec` #35578057311'in sınıfı: derinlik pencereyi karşılamadı ve koşu
kısalmış bir pencereye sessizce karar verdi — pencere kapısı (`assert_window_covered`).

Önbelleğin DURUMU hiçbir testte sonucu belirlememeli: her senaryo "önce başka bir koşu bu
dizini doldurdu" ile başlar.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pandas as pd
import pytest
import yaml

from core.data import (
    OKXError,
    _anchor_as_of,
    fetch_funding,
    fetch_ohlcv,
    load_cached_market_data,
    load_market_data,
)
from core.engine import Engine
from core.ledger import Ledger
from core.portfolio import Portfolio
from strategies.base import MarketData
from tests.test_data import StubSession, _candle, _client, _config
from tests.test_engine_per_bar import _EveryBar
from tests.test_engine_per_bar import _config as engine_config

BAR = pd.Timedelta(hours=4)
SYMBOL = "BTC-USDT-SWAP"
FIRST = pd.Timestamp("2022-01-01 00:00", tz="UTC")
T_REAL = pd.Timestamp("2026-09-18 16:00", tz="UTC")  # önceki koşunun (ema B) "şimdi"si
T_PAST = pd.Timestamp("2024-12-31 00:00", tz="UTC")  # dc dönem A'nın sonu
EXCHANGE_BARS = pd.date_range(FIRST, T_REAL - BAR, freq="4h", tz="UTC")


def _ms(ts: pd.Timestamp) -> int:
    return int(ts.value // 1_000_000)


def _exchange(bars: pd.DatetimeIndex = EXCHANGE_BARS) -> StubSession:
    """`after` imlecini onurlandıran, en yeniden eskiye sayfalayan borsa."""

    def candles(params: dict[str, str]) -> list[list[str]]:
        after = int(params["after"]) if "after" in params else None
        rows = [b for b in bars if after is None or _ms(b) < after]
        return [_candle(_ms(b), 100.0) for b in rows[::-1][: int(params["limit"])]]

    def funding(params: dict[str, str]) -> list[dict[str, str]]:
        after = int(params["after"]) if "after" in params else None
        stamps = pd.date_range(FIRST, T_REAL, freq="8h", tz="UTC")
        rows = [s for s in stamps if after is None or _ms(s) < after][::-1][: int(params["limit"])]
        return [{"fundingTime": str(_ms(s)), "fundingRate": "0.0001"} for s in rows]

    return StubSession({
        "/market/candles": candles,
        "/market/history-candles": candles,
        "/public/funding-rate-history": funding,
    })


def _cfg(tmp_path: Path, *, history_bars: int, funding_periods: int = 50) -> dict[str, Any]:
    cfg = _config(tmp_path)
    cfg["data"]["history_bars"] = history_bars
    cfg["data"]["funding_history_periods"] = funding_periods
    cfg["data"]["max_staleness_bars"] = 3
    cfg["exchange"].update(candles_limit=300, history_candles_limit=100, funding_limit=100)
    return cfg


def _closed_by(frame: pd.DataFrame, now: pd.Timestamp) -> bool:
    return bool((frame.index + BAR <= now).all())


# --------------------------------------------------------------------------- #
# 1. dc: önbellekteki now-sonrası barlar pencereyi taşırmaz
# --------------------------------------------------------------------------- #
def test_dc_scenario_cache_from_a_later_run_does_not_move_now_forward(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path, history_bars=12000)
    session = _exchange()
    fetch_ohlcv(cfg, SYMBOL, client=_client(session, cfg), now=T_REAL)  # önceki koşu

    frame = fetch_ohlcv(cfg, SYMBOL, client=_client(session, cfg), now=T_PAST)

    assert _closed_by(frame, T_PAST)
    assert frame.index[-1] == T_PAST - BAR
    assert frame.index[0] == FIRST, "önbellekte zaten olan geçmiş korunur"
    stored = pd.read_parquet(tmp_path / "cache" / f"{SYMBOL}_4H.parquet")
    assert stored.index.max() >= T_REAL - BAR, "dosya budanmaz: başka bir now onu ister"


def test_dc_scenario_anchor_sits_at_the_window_end(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path, history_bars=12000)
    session = _exchange()
    fetch_ohlcv(cfg, SYMBOL, client=_client(session, cfg), now=T_REAL)

    market = load_market_data(cfg, symbols=[SYMBOL], client=_client(session, cfg), now=T_PAST)

    assert market.as_of == T_PAST - BAR
    assert _closed_by(market.btc, T_PAST)
    assert all(_closed_by(frame, T_PAST) for frame in market.ohlcv.values())
    assert all(series.index.max() <= T_PAST for series in market.funding.values())


def test_anchor_refuses_a_bar_that_closes_after_now() -> None:
    """İkinci savunma: kesilmemiş bir seri çıpaya ulaşırsa sessizce `as_of` olmaz."""
    frame = pd.DataFrame({"close": [1.0]}, index=pd.DatetimeIndex([T_REAL - BAR], tz="UTC"))
    with pytest.raises(OKXError, match="now'dan SONRA"):
        _anchor_as_of(frame, symbol=SYMBOL, now=T_PAST, duration=BAR, max_staleness_bars=3)


# --------------------------------------------------------------------------- #
# 2. TimesFM: sığ ve taze önbellek derin ve geçmişteki isteği yutmaz
# --------------------------------------------------------------------------- #
def test_timesfm_scenario_shallow_recent_cache_does_not_blind_a_deep_past_request(
    tmp_path: Path,
) -> None:
    p1 = _cfg(tmp_path, history_bars=300)
    session = _exchange()
    fetch_ohlcv(p1, SYMBOL, client=_client(session, p1), now=T_REAL)  # P1

    period_a = _cfg(tmp_path, history_bars=3000)
    frame = fetch_ohlcv(period_a, SYMBOL, client=_client(session, period_a), now=T_PAST)

    assert len(frame) == 3000
    assert _closed_by(frame, T_PAST)
    assert frame.index[-1] == T_PAST - BAR
    assert (frame.index.to_series().diff().dropna() == BAR).all(), "seri boşluksuz"


# --------------------------------------------------------------------------- #
# 3. Derinlik: sığ önbellek daha derin bir isteği kısaltmaz; taban bilinir
# --------------------------------------------------------------------------- #
def test_shallow_cache_is_backfilled_to_the_requested_depth(tmp_path: Path) -> None:
    shallow = _cfg(tmp_path, history_bars=600)
    session = _exchange()
    fetch_ohlcv(shallow, SYMBOL, client=_client(session, shallow), now=T_REAL)

    deep = _cfg(tmp_path, history_bars=3000)
    frame = fetch_ohlcv(deep, SYMBOL, client=_client(session, deep), now=T_REAL)

    assert len(frame) == 3000
    assert frame.index[-1] == T_REAL - BAR


def test_disjoint_cached_segments_do_not_leave_a_hole_inside_the_window(tmp_path: Path) -> None:
    """TimesFM'in üç isteği aynı dizinde: P1 (bugün, sığ) → A (2024, derin) → B (bugün, derin).

    Dosya iki AYRIK parça taşır; B'nin bar SAYISI tutar ama ortası ~19 ay delik kalırdı.
    """
    session = _exchange()
    recent = _cfg(tmp_path, history_bars=300)
    fetch_ohlcv(recent, SYMBOL, client=_client(session, recent), now=T_REAL)
    deep = _cfg(tmp_path, history_bars=2000)
    fetch_ohlcv(deep, SYMBOL, client=_client(session, deep), now=T_PAST)

    frame = fetch_ohlcv(deep, SYMBOL, client=_client(session, deep), now=T_REAL)

    assert len(frame) == 2000
    assert frame.index[-1] == T_REAL - BAR
    assert (frame.index.to_series().diff().dropna() == BAR).all()


def test_a_gap_the_exchange_also_has_is_asked_once_and_left_alone(tmp_path: Path) -> None:
    """Bakım boşluğu borsada da yoksa istek döngüye girmez ve seri olduğu gibi döner."""
    holed = EXCHANGE_BARS[(EXCHANGE_BARS < T_REAL - 60 * BAR) | (EXCHANGE_BARS >= T_REAL - 50 * BAR)]
    cfg = _cfg(tmp_path, history_bars=500)
    fetch_ohlcv(cfg, SYMBOL, client=_client(_exchange(holed), cfg), now=T_REAL)

    session = _exchange(holed)
    frame = fetch_ohlcv(cfg, SYMBOL, client=_client(session, cfg), now=T_REAL)

    assert len(frame) == 500
    assert len(session.calls) <= 3


def test_exchange_floor_is_remembered_so_a_young_symbol_is_not_repaged(tmp_path: Path) -> None:
    """Yeni listelenmiş sembol: borsada daha eski bar YOK — her turda sormak israftır."""
    young = pd.date_range(T_REAL - 50 * BAR, T_REAL - BAR, freq="4h", tz="UTC")
    cfg = _cfg(tmp_path, history_bars=600)
    fetch_ohlcv(cfg, SYMBOL, client=_client(_exchange(young), cfg), now=T_REAL - BAR)

    session = _exchange(young)
    frame = fetch_ohlcv(cfg, SYMBOL, client=_client(session, cfg), now=T_REAL)

    assert len(frame) == 50
    assert all("after" not in params for _, params in session.calls), (
        "taban ölçülmüşken geriye doğru istek atılmamalı"
    )


def test_depth_counts_from_now_not_from_the_cache_end(tmp_path: Path) -> None:
    """`history_bars` geçmişteki `now`dan geriye sayılır — önbelleğin ucundan değil."""
    cfg = _cfg(tmp_path, history_bars=1000)
    session = _exchange()
    fetch_ohlcv(cfg, SYMBOL, client=_client(session, cfg), now=T_REAL)

    frame = fetch_ohlcv(cfg, SYMBOL, client=_client(session, cfg), now=T_PAST)

    assert len(frame) == 1000
    assert frame.index[0] == T_PAST - 1000 * BAR


# --------------------------------------------------------------------------- #
# 4. Fonlama: aynı iki kural
# --------------------------------------------------------------------------- #
def test_funding_cache_from_a_later_run_is_cut_at_now(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path, history_bars=10, funding_periods=50)
    session = _exchange()
    fetch_funding(cfg, SYMBOL, client=_client(session, cfg), now=T_REAL)

    series = fetch_funding(cfg, SYMBOL, client=_client(session, cfg), now=T_PAST)

    assert len(series) == 50
    assert series.index.max() <= T_PAST


def test_disjoint_funding_segments_do_not_leave_a_hole(tmp_path: Path) -> None:
    session = _exchange()
    cfg = _cfg(tmp_path, history_bars=10, funding_periods=30)
    fetch_funding(cfg, SYMBOL, client=_client(session, cfg), now=T_REAL)
    fetch_funding(cfg, SYMBOL, client=_client(session, cfg), now=T_PAST)
    deep = _cfg(tmp_path, history_bars=10, funding_periods=200)

    series = fetch_funding(deep, SYMBOL, client=_client(session, deep), now=T_REAL)

    assert len(series) == 200
    assert (series.index.to_series().diff().dropna() == pd.Timedelta(hours=8)).all()


def test_shallow_funding_cache_is_backfilled(tmp_path: Path) -> None:
    session = _exchange()
    shallow = _cfg(tmp_path, history_bars=10, funding_periods=20)
    fetch_funding(shallow, SYMBOL, client=_client(session, shallow), now=T_REAL)

    deep = _cfg(tmp_path, history_bars=10, funding_periods=200)
    series = fetch_funding(deep, SYMBOL, client=_client(session, deep), now=T_REAL)

    assert len(series) == 200


# --------------------------------------------------------------------------- #
# 5. Salt okunur ikiz: aynı kesim
# --------------------------------------------------------------------------- #
def test_cached_snapshot_is_cut_at_now_too(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path, history_bars=12000)
    fetch_ohlcv(cfg, SYMBOL, client=_client(_exchange(), cfg), now=T_REAL)

    market = load_cached_market_data(cfg, symbols=[SYMBOL], now=T_PAST)

    assert market.as_of == T_PAST - BAR
    assert _closed_by(market.btc, T_PAST)


# --------------------------------------------------------------------------- #
# 6. İleriye bakış YOK: taşmış bir anlık görüntüde bile model bar t'de t'yi görür
# --------------------------------------------------------------------------- #
def test_signal_at_bar_t_never_sees_a_bar_after_t_even_in_an_overflowing_snapshot(
    tmp_path: Path,
) -> None:
    """Kapanmış kararların SİNYAL tarafının kirlenmediğinin kanıtı (karar 59).

    dc koşusunun anlık görüntüsü penceresinden taşıyordu; etkinin pencere kontaminasyonu
    olup ileriye bakış OLMADIĞI, motorun her barı kendi görüntüsüne kesmesine dayanır.
    Casus model her çağrıda gördüğü son barı kaydeder.
    """
    index = pd.date_range("2026-01-01", periods=24, freq="15min", tz="UTC", name="ts")
    frame = pd.DataFrame(
        {"open": 100.0, "high": 100.5, "low": 99.5, "close": 100.0, "volume": 1.0}, index=index
    )
    market = MarketData(ohlcv={SYMBOL: frame}, btc=frame, funding={}, as_of=index[-1])
    config = engine_config()
    ledger = Ledger(tmp_path / "ledger")
    spy = _EveryBar()
    ledger.initialize_model(spy.name, initial_capital=10000.0)
    state = ledger.load_state(spy.name)
    assert state is not None
    state["last_processed_bar"] = index[0].isoformat()
    ledger.write_state(spy.name, state)

    Engine([spy], config=config, ledger=ledger, portfolio=Portfolio(config)).run_round(market)

    assert len(spy.signal_bars) == len(index) - 1
    assert spy.seen_last_bar == spy.signal_bars


# --------------------------------------------------------------------------- #
# 7. Pencere kapısı (karar 51'in pencere karşılığı)
# --------------------------------------------------------------------------- #
def _snapshot(first: pd.Timestamp, last: pd.Timestamp) -> MarketData:
    index = pd.date_range(first, last, freq="4h", tz="UTC", name="ts")
    frame = pd.DataFrame({"close": 1.0}, index=index)
    return MarketData(ohlcv={SYMBOL: frame}, btc=frame, funding={}, as_of=index[-1])


def test_xsec_scenario_shallow_snapshot_is_refused() -> None:
    """#35578057311: 2022-01'den istenen A, 3000 barla ~2023-02'den başladı."""
    from scripts.backtest import WindowCoverageError, assert_window_covered

    start = pd.Timestamp("2022-01-01", tz="UTC")
    end = pd.Timestamp("2024-06-30", tz="UTC")
    market = _snapshot(end - 3000 * BAR, end - BAR)
    with pytest.raises(WindowCoverageError, match="pencere ölçülmedi"):
        assert_window_covered(market, start=start, end=end, warmup_bars=600, duration=BAR)


def test_window_gate_requires_the_live_warmup_before_start() -> None:
    from scripts.backtest import WindowCoverageError, assert_window_covered

    start = pd.Timestamp("2022-01-01", tz="UTC")
    end = pd.Timestamp("2024-06-30", tz="UTC")
    assert_window_covered(
        _snapshot(start - 600 * BAR, end - BAR), start=start, end=end, warmup_bars=600, duration=BAR
    )
    with pytest.raises(WindowCoverageError):
        assert_window_covered(
            _snapshot(start - 599 * BAR, end - BAR),
            start=start, end=end, warmup_bars=600, duration=BAR,
        )


def test_window_gate_refuses_an_overflowing_or_empty_window() -> None:
    from scripts.backtest import WindowCoverageError, assert_window_covered

    start = pd.Timestamp("2022-01-01", tz="UTC")
    end = pd.Timestamp("2024-12-31", tz="UTC")
    with pytest.raises(WindowCoverageError, match="TAŞTI"):
        assert_window_covered(
            _snapshot(FIRST - 700 * BAR, T_REAL - BAR),
            start=start, end=end, warmup_bars=600, duration=BAR,
        )
    with pytest.raises(WindowCoverageError, match="boş"):
        assert_window_covered(
            _snapshot(FIRST - 700 * BAR, start), start=start, end=end, warmup_bars=600, duration=BAR
        )


def test_run_backtest_refuses_a_snapshot_that_does_not_cover_the_window(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import scripts.backtest as bt

    start = pd.Timestamp("2022-01-01", tz="UTC")
    end = pd.Timestamp("2024-06-30", tz="UTC")
    monkeypatch.setattr(bt, "load_market_data", lambda *a, **k: _snapshot(end - 3000 * BAR, end - BAR))
    with pytest.raises(bt.WindowCoverageError):
        bt.run_backtest(layer_name="xsec", start=start, end=end, out_dir=tmp_path / "out")
    assert not (tmp_path / "out" / "manifest.json").exists(), "kapıdan dönen koşu sonuç yazmaz"


def test_window_gate_is_exit_code_3_and_is_never_swallowed_by_a_single_symbol_loop(
    tmp_path: Path,
) -> None:
    import scripts.backtest as bt
    import scripts.backtest_dc as dc

    def gate(*_a: Any, **_k: Any) -> Any:
        raise bt.WindowCoverageError("pencere ölçülmedi")

    with pytest.raises(bt.WindowCoverageError):
        dc.run_singles(gate, name="A", out_root=tmp_path, symbols=["BTC-USDT-SWAP"])
    assert bt.exit_code_of(lambda argv: gate()) == bt.EXIT_DATA_GATE


def test_ema_single_symbol_loop_does_not_swallow_the_gate(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import scripts.backtest as bt
    import scripts.backtest_ema as ema

    calls: list[Any] = []

    def fake(**kwargs: Any) -> Any:
        calls.append(kwargs)
        if kwargs.get("symbols") and len(kwargs["symbols"]) == 1:
            raise bt.WindowCoverageError("pencere ölçülmedi")
        return object()

    monkeypatch.setattr(ema, "run_backtest", fake)
    with pytest.raises(bt.WindowCoverageError):
        ema.run_period(
            name="A", start=pd.Timestamp("2022-01-01", tz="UTC"),
            end=pd.Timestamp("2024-12-31", tz="UTC"), out_root=tmp_path,
            symbols=["BTC-USDT-SWAP", "ETH-USDT-SWAP"], models=["ema_trend"],
            history_bars=12000, funding_periods=6000, singles=True,
        )


# --------------------------------------------------------------------------- #
# 8. Workflow önbellek anahtarları: bir önek başka bir workflow'un anahtarını yakalayamaz
# --------------------------------------------------------------------------- #
_EXPR = re.compile(r"\$\{\{.*?\}\}")
WORKFLOWS = Path(__file__).resolve().parent.parent / ".github" / "workflows"


def _static_prefix(template: str) -> str:
    return _EXPR.split(template.strip(), maxsplit=1)[0]


def _market_data_caches() -> list[tuple[str, str, list[str]]]:
    found: list[tuple[str, str, list[str]]] = []
    for path in sorted(WORKFLOWS.glob("*.yml")):
        doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        for job in (doc.get("jobs") or {}).values():
            for step in job.get("steps") or []:
                if not str(step.get("uses", "")).startswith("actions/cache"):
                    continue
                spec = step.get("with") or {}
                if "data/cache" not in str(spec.get("path", "")):
                    continue
                restore = [line for line in str(spec.get("restore-keys", "")).splitlines() if line.strip()]
                found.append((path.name, str(spec["key"]), restore))
    return found


def test_workflow_cache_listing_is_not_empty() -> None:
    """Kendi verisini tarayan bir test "hiç eşleşme yok"ta her zaman yeşil görünür."""
    names = {name for name, _, _ in _market_data_caches()}
    assert {"backtest.yml", "backtest-ema.yml", "backtest-dc.yml", "diagnose-ema-exits.yml"} <= names


def test_restore_keys_only_reach_their_own_workflow() -> None:
    caches = _market_data_caches()
    for name, key, restore in caches:
        own = _static_prefix(key)
        for prefix in map(_static_prefix, restore):
            assert own.startswith(prefix), f"{name}: {prefix!r} kendi anahtarını ({own!r}) kapsamıyor"
            for other, other_key, _ in caches:
                if other == name:
                    continue
                theirs = _static_prefix(other_key)
                assert not theirs.startswith(prefix) and not prefix.startswith(theirs), (
                    f"{name} restore-key {prefix!r}, {other} anahtarını ({theirs!r}) yakalayabilir"
                )


# --------------------------------------------------------------------------- #
# 9. Fonlama kapsamı raporu (karar 59 > sıra 1 şartı)
# --------------------------------------------------------------------------- #
def test_funding_coverage_counts_grid_stamps_with_a_record() -> None:
    """`rate_at`in birebir eşleme kuralıyla: kaydı olmayan damga fonlama ÜRETMEZ."""
    from scripts.backtest import funding_coverage

    start = pd.Timestamp("2026-06-15", tz="UTC")
    end = pd.Timestamp("2026-06-20", tz="UTC")
    index = pd.date_range(start, end, freq="4h", tz="UTC", name="ts")
    frame = pd.DataFrame({"close": 1.0}, index=index)
    records = pd.date_range(pd.Timestamp("2026-06-17 16:00", tz="UTC"), end, freq="8h")
    funding = {SYMBOL: pd.Series(0.0001, index=records)}
    market = MarketData(ohlcv={SYMBOL: frame}, btc=frame, funding=funding, as_of=index[-1])

    report = funding_coverage(market, start=start, end=end, interval_hours=8, enabled=True)

    assert report["stamps_per_symbol"] == 15  # [06-15 00:00, 06-20 00:00) 8 saatte bir
    assert report["with_record_total"] == 7   # 06-17 16:00 → 06-19 16:00
    assert report["first_record_earliest"] == "2026-06-17T16:00:00+00:00"
    assert funding_coverage(market, start=start, end=end, interval_hours=8, enabled=False) == {
        "enabled": False
    }

"""`xsec_mom` ve `xsec_random`ın SAF mantığı: ağ yok, defter yok.

Sınanan şey ön-kaydın (docs/backtest.md > 6g) koda GERÇEKTEN girip girmediğidir:

(1) rebalance barı PAZARTESİ 00:00'da KAPANAN bardır (indeks Pazar 20:00) — reddedilen
    alternatif (indeks Pazartesi 00:00) sınanır ve tutmamalıdır;
(2) rebalance dışı barda model pozisyonlara DOKUNMAZ;
(3) stop tam 5×ATR ve TP YOK;
(4) ATR yumuşatması projenin VARSAYILANI (`simple`), `wilder` değil;
(5) uygunluk kuralı iki modelde BİREBİR aynı (tek kopya) ve tam geriye bakış ister;
(6) seçim dışındaki her şey ortak — ayrışan TEK şey `choose`;
(7) kontrolün çekilişi deterministik (aynı bar → aynı küme) ama momentumdan bağımsız;
(8) çıkış `exit_rule=rebalance` etiketi taşır (P1 bu kırılımdan okunur).
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from core.config import load_config
from core.data import bar_duration
from core.indicators import average_true_range
from core.layers import resolve_layer
from core.tags import parse_tag
from strategies.base import MarketData, Position
from strategies.registry import build
from strategies.xsec.ranking import (
    Candidate,
    XsecRules,
    by_momentum,
    eligible_candidates,
    is_rebalance_bar,
)

REBALANCE_TS = pd.Timestamp("2024-01-07 20:00", tz="UTC")   # Pazartesi 00:00'da kapanır


@pytest.fixture(scope="module")
def layer():
    return resolve_layer(load_config(), "xsec")


@pytest.fixture(scope="module")
def rules(layer):
    return XsecRules.from_config(layer.config)


def _market(symbols, *, as_of=REBALANCE_TS, periods=200, drifts=None, duration=None):
    duration = duration or pd.Timedelta(hours=4)
    index = pd.date_range(end=as_of, periods=periods, freq=duration)
    frames = {}
    for i, symbol in enumerate(symbols):
        drift = (drifts or {}).get(symbol, (i - len(symbols) // 2) * 0.0015)
        close = 100.0 * np.exp(np.cumsum(np.full(len(index), drift)))
        frames[symbol] = pd.DataFrame(
            {"open": close, "high": close * 1.01, "low": close * 0.99,
             "close": close, "volume": np.full(len(index), 1e6)},
            index=index,
        )
    return MarketData(
        ohlcv=frames, btc=frames[symbols[0]], funding={}, as_of=as_of,
    )


# --------------------------------------------------------------------------- #
# (1) Rebalance barının TAM tanımı — ön-kaydın cümlesi
# --------------------------------------------------------------------------- #
def test_rebalance_bar_is_the_one_closing_monday_midnight():
    duration = pd.Timedelta(hours=4)
    assert is_rebalance_bar(REBALANCE_TS, duration=duration) is True


def test_the_rejected_alternative_is_not_a_rebalance_bar():
    """İndeks Pazartesi 00:00 (04:00'te kapanır) — §6g'de açıkça REDDEDİLEN okuma."""
    duration = pd.Timedelta(hours=4)
    monday_open = pd.Timestamp("2024-01-08 00:00", tz="UTC")
    assert is_rebalance_bar(monday_open, duration=duration) is False


@pytest.mark.parametrize("ts", ["2024-01-07 16:00", "2024-01-08 20:00", "2024-01-05 20:00"])
def test_other_bars_are_not_rebalance_bars(ts):
    assert is_rebalance_bar(pd.Timestamp(ts, tz="UTC"), duration=pd.Timedelta(hours=4)) is False


def test_rule_is_written_on_the_close_not_pinned_to_4h():
    """Kural kapanışa yazılı: bar süresi değişirse indeks kayar, kural aynı kalır."""
    hourly = pd.Timestamp("2024-01-07 23:00", tz="UTC")
    assert is_rebalance_bar(hourly, duration=pd.Timedelta(hours=1)) is True
    assert is_rebalance_bar(hourly, duration=pd.Timedelta(hours=4)) is False


# --------------------------------------------------------------------------- #
# (2) Rebalance dışı barda model SESSİZDİR
# --------------------------------------------------------------------------- #
def test_no_signals_outside_a_rebalance_bar(layer):
    market = _market(layer.symbols, as_of=pd.Timestamp("2024-01-08 00:00", tz="UTC"))
    for name in ("xsec_mom", "xsec_random"):
        assert build(name, config=layer.config).generate_signals(market) == []


def test_positions_are_untouched_outside_a_rebalance_bar(layer):
    """Rebalance dışı barda çıkışın tek yolu stop'tur ve onu MOTOR uygular."""
    market = _market(layer.symbols, as_of=pd.Timestamp("2024-01-08 00:00", tz="UTC"))
    position = Position(
        symbol=layer.symbols[0], direction="long", entry_price=100.0,
        stop_price=95.0, opened_at=REBALANCE_TS,
    )
    model = build("xsec_mom", config=layer.config)
    assert model.manage_positions(market, [position]) == []


# --------------------------------------------------------------------------- #
# (3) Stop 5×ATR, TP YOK
# --------------------------------------------------------------------------- #
def test_stop_is_exactly_the_preregistered_multiple(layer, rules):
    market = _market(layer.symbols)
    signals = build("xsec_mom", config=layer.config).generate_signals(market)
    assert signals
    for signal in signals:
        frame = market.ohlcv[signal.symbol]
        atr = average_true_range(frame, rules.atr_period)
        distance = float(frame["close"].iloc[-1]) - signal.stop_price
        assert distance == pytest.approx(atr * rules.stop_atr_multiple, rel=1e-9)


def test_no_take_profit_anywhere(layer):
    """TP YOK (§6g): momentumda kâr pozisyonda kalma süresinden gelir."""
    market = _market(layer.symbols)
    for name in ("xsec_mom", "xsec_random"):
        for signal in build(name, config=layer.config).generate_signals(market):
            assert signal.take_profits == ()


def test_long_only(layer):
    market = _market(layer.symbols)
    for name in ("xsec_mom", "xsec_random"):
        model = build(name, config=layer.config)
        assert model.allowed_directions == ["long"]
        assert all(s.direction == "long" for s in model.generate_signals(market))


def test_top_k_signals_at_most(layer, rules):
    market = _market(layer.symbols)
    for name in ("xsec_mom", "xsec_random"):
        assert len(build(name, config=layer.config).generate_signals(market)) == rules.top_k


# --------------------------------------------------------------------------- #
# (4) ATR yumuşatması: VARSAYILAN, `wilder` DEĞİL
# --------------------------------------------------------------------------- #
def test_module_never_declares_wilder_smoothing():
    """§6g'nin DÜZELTMESİ: motorun tavan ölçüsüyle aynı yumuşatma, oran kayması yok."""
    for path in ("strategies/xsec/ranking.py", "strategies/xsec/model.py",
                 "strategies/xsec_mom.py", "strategies/xsec_random.py"):
        source = Path(path).read_text(encoding="utf-8")
        assert 'smoothing="wilder"' not in source
        assert "atr_smoothing" not in source.replace("# ", "")


def test_config_block_declares_no_smoothing():
    """`xsec` bloğunda yumuşatma anahtarı YOKTUR — varsayılanı kullanmanın kendisi budur."""
    assert "atr_smoothing" not in load_config()["xsec"]


def test_stop_matches_the_engine_cap_measure(layer, rules):
    """Model ile motorun tavan kontrolü AYNI ATR'yi görür: 5× ölçülür, 5× çıkar."""
    market = _market(layer.symbols)
    cap = float(layer.config["max_stop_atr_multiple"])
    for signal in build("xsec_mom", config=layer.config).generate_signals(market):
        frame = market.ohlcv[signal.symbol]
        atr = average_true_range(frame, rules.atr_period)   # motorun çağrısıyla aynı
        multiple = (float(frame["close"].iloc[-1]) - signal.stop_price) / atr
        assert multiple == pytest.approx(rules.stop_atr_multiple, rel=1e-9)
        assert multiple <= cap, "sinyal motorun tavanında elenirdi"


# --------------------------------------------------------------------------- #
# (5) Uygunluk: tam geriye bakış ZORUNLU
# --------------------------------------------------------------------------- #
def test_symbol_without_full_lookback_is_not_ranked(layer, rules):
    market = _market(layer.symbols, periods=rules.lookback_bars)   # bir bar eksik
    assert eligible_candidates(market, rules) == []


def test_exactly_enough_history_is_eligible(layer, rules):
    market = _market(layer.symbols, periods=rules.lookback_bars + 1)
    assert len(eligible_candidates(market, rules)) == len(layer.symbols)


def test_symbol_missing_the_as_of_bar_is_dropped(layer, rules):
    market = _market(layer.symbols)
    stale = layer.symbols[0]
    frames = dict(market.ohlcv)
    frames[stale] = frames[stale].iloc[:-1]
    trimmed = MarketData(ohlcv=frames, btc=market.btc, funding={}, as_of=market.as_of)
    assert stale not in {c.symbol for c in eligible_candidates(trimmed, rules)}


def _referenced_names(path: str) -> set[str]:
    """Kaynaktaki GERÇEK kod referansları — docstring ve yorumlar hariç.

    Metinde arama yapmak yanlış olurdu: modüllerin docstring'leri ortak kopyadan
    ("`eligible_candidates`tadır") söz ediyor ve bir söz, bir çağrı değildir.
    """
    tree = ast.parse(Path(path).read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            names.update(alias.name for alias in node.names)
    return names


def test_both_models_share_one_eligibility_definition():
    """Uygunluk ve ATR tek kopyada: iki modelde ayrı yazılsaydı fark seçimin ölçüsü olmazdı."""
    for path in ("strategies/xsec_mom.py", "strategies/xsec_random.py"):
        names = _referenced_names(path)
        assert "eligible_candidates" not in names
        assert "average_true_range" not in names
        assert "is_rebalance_bar" not in names


def test_models_do_not_reimplement_the_stop_geometry():
    """Stop mesafesi ortak `Candidate.stop_price`tadır; modelde çarpan aritmetiği yok."""
    for path in ("strategies/xsec_mom.py", "strategies/xsec_random.py"):
        assert "stop_atr_multiple" not in _referenced_names(path)


# --------------------------------------------------------------------------- #
# (6) Ayrışan TEK şey seçim
# --------------------------------------------------------------------------- #
def test_only_choose_and_its_note_are_overridden():
    """Karşılığı ölçülen bir eksen olmayan override noktası eklenemez."""
    from strategies.xsec.model import XsecModel
    from strategies.xsec_mom import XsecMomentum
    from strategies.xsec_random import XsecRandom

    base = {n for n, _ in inspect.getmembers(XsecModel, inspect.isfunction)}
    for subclass in (XsecMomentum, XsecRandom):
        overridden = {
            name for name in base
            if getattr(subclass, name, None) is not getattr(XsecModel, name, None)
        }
        assert overridden <= {"choose", "selection_note", "__init__"}, overridden


def test_momentum_picks_the_strongest(layer, rules):
    drifts = {s: 0.0 for s in layer.symbols}
    drifts[layer.symbols[3]] = 0.004
    drifts[layer.symbols[7]] = 0.003
    drifts[layer.symbols[1]] = 0.002
    market = _market(layer.symbols, drifts=drifts)
    chosen = [s.symbol for s in build("xsec_mom", config=layer.config).generate_signals(market)]
    assert chosen == [layer.symbols[3], layer.symbols[7], layer.symbols[1]]


def test_momentum_ties_break_on_symbol_name(rules):
    pool = [
        Candidate(symbol=name, close=100.0, atr=1.0, lookback_return=0.05)
        for name in ("ZZZ", "AAA", "MMM")
    ]
    pool.sort(key=lambda c: c.symbol)          # eligible_candidates ADI SIRALI verir
    assert [c.symbol for c in by_momentum(pool, 2)] == ["AAA", "MMM"]


# --------------------------------------------------------------------------- #
# (7) Kontrolün çekilişi: deterministik ama BAĞIMSIZ
# --------------------------------------------------------------------------- #
def test_control_draw_is_reproducible_for_the_same_bar(layer):
    market = _market(layer.symbols)
    model = build("xsec_random", config=layer.config)
    first = [s.symbol for s in model.generate_signals(market)]
    second = [s.symbol for s in model.generate_signals(market)]
    assert first == second


def test_control_entry_and_exit_agree_within_a_bar(layer):
    """Aynı barda giriş kümesi ile tutulan küme aynı olmalı; yoksa model aynı barda
    hem alır hem satar."""
    market = _market(layer.symbols)
    model = build("xsec_random", config=layer.config)
    chosen = {s.symbol for s in model.generate_signals(market)}
    held = [
        Position(symbol=symbol, direction="long", entry_price=100.0,
                 stop_price=95.0, opened_at=REBALANCE_TS)
        for symbol in chosen
    ]
    assert model.manage_positions(market, held) == []


def test_control_draw_differs_across_bars(layer):
    model = build("xsec_random", config=layer.config)
    a = [s.symbol for s in model.generate_signals(_market(layer.symbols))]
    later = pd.Timestamp("2024-01-14 20:00", tz="UTC")
    b = [s.symbol for s in model.generate_signals(_market(layer.symbols, as_of=later))]
    assert a != b


def test_control_never_repeats_a_symbol(layer):
    model = build("xsec_random", config=layer.config)
    for week in range(8):
        as_of = REBALANCE_TS + pd.Timedelta(weeks=week)
        chosen = [s.symbol for s in model.generate_signals(_market(layer.symbols, as_of=as_of))]
        assert len(chosen) == len(set(chosen))


def test_control_does_not_share_the_momentum_draw(layer):
    """Ölçülen eksen seçim: çekiliş paylaşılsaydı fark tesadüfün ölçüsü olurdu."""
    drifts = {s: (i - 6) * 0.002 for i, s in enumerate(layer.symbols)}
    market = _market(layer.symbols, drifts=drifts)
    mom = [s.symbol for s in build("xsec_mom", config=layer.config).generate_signals(market)]
    ctrl = [s.symbol for s in build("xsec_random", config=layer.config).generate_signals(market)]
    assert mom != ctrl


# --------------------------------------------------------------------------- #
# (8) Çıkış: rebalance etiketli
# --------------------------------------------------------------------------- #
def test_dropped_symbol_is_closed_with_the_rebalance_tag(layer):
    drifts = {s: 0.0 for s in layer.symbols}
    for winner in layer.symbols[:3]:
        drifts[winner] = 0.004
    market = _market(layer.symbols, drifts=drifts)
    model = build("xsec_mom", config=layer.config)
    loser = layer.symbols[-1]
    position = Position(
        symbol=loser, direction="long", entry_price=100.0,
        stop_price=95.0, opened_at=REBALANCE_TS,
    )
    instructions = model.manage_positions(market, [position])
    assert [i.symbol for i in instructions] == [loser]
    assert instructions[0].action == "close"
    assert parse_tag(instructions[0].reason, "exit_rule") == "rebalance"


def test_symbol_still_in_top_k_is_kept(layer):
    market = _market(layer.symbols)
    model = build("xsec_mom", config=layer.config)
    keeper = model.generate_signals(market)[0].symbol
    position = Position(
        symbol=keeper, direction="long", entry_price=100.0,
        stop_price=95.0, opened_at=REBALANCE_TS,
    )
    assert model.manage_positions(market, [position]) == []


def test_empty_eligible_pool_closes_everything(layer, rules):
    """'Rebalance günü ama uygun sembol yok' ile 'rebalance günü değil' AYRI durumlar."""
    market = _market(layer.symbols, periods=rules.lookback_bars)   # hiçbiri uygun değil
    model = build("xsec_mom", config=layer.config)
    position = Position(
        symbol=layer.symbols[0], direction="long", entry_price=100.0,
        stop_price=95.0, opened_at=REBALANCE_TS,
    )
    assert [i.symbol for i in model.manage_positions(market, [position])] == [layer.symbols[0]]


# --------------------------------------------------------------------------- #
# Katman: ön-kayıtlı sayılar ve `ema`ya dokunulmamışlık
# --------------------------------------------------------------------------- #
def test_preregistered_numbers(rules):
    assert (rules.lookback_bars, rules.top_k, rules.stop_atr_multiple) == (126, 3, 5.0)
    assert rules.atr_period == 14


def test_layer_cap_admits_the_stop(layer, rules):
    assert float(layer.config["max_stop_atr_multiple"]) == 6.0 > rules.stop_atr_multiple


def test_layer_control_is_the_xsec_one(layer):
    assert layer.config["acceptance"]["control_model"] == "xsec_random"


def test_ema_layer_is_untouched():
    """xsec, tamamlanmış bir ön-kayıtlı koşunun katman koşullarına DOKUNMAZ."""
    ema = resolve_layer(load_config(), "ema")
    assert float(ema.config["max_stop_atr_multiple"]) == 3.0
    assert ema.config["acceptance"]["control_model"] == "random_ctrl"
    assert ema.config["models"] == ["buyhold", "trend", "random_ctrl", "ema_trend"]

"""Performans metrikleri. Salt okunur: defteri okur, asla değiştirmez.

**Birinci sınıf metrik: işlem başına ortalama R.**

R = işlemin net PnL'i / o işlemde AÇILIŞTA riske edilen tutar. Payda `trades.csv`'nin
`risk_amount` kolonudur (`adet × |giriş − ilk stop|`), dolayısıyla R bileşiklenmeden
bağımsızdır: hesap büyüdükçe boyut da büyüdüğü için toplam getiri kısmen "model ne kadar
hızlı bileşiklendi"yi ölçer, sinyal kalitesini değil. Aynı sinyal kalitesine sahip iki
modelden açık kârını erken büyüteni toplam getiride öne geçer — oysa ölçmek istediğimiz
şey bu değil. Ortalama R bu etkiyi dışarıda bırakır ve "bu model iyi mi" sorusuna daha
temiz cevap verir. Toplam getiri (USDT/%) ikinci sırada raporlanır, atılmaz.

**Long/short ayrıştırması opsiyonel değildir** (CLAUDE.md): projenin ana sorusu short
işlemlerin görece başarısı olduğu için her işlem-tabanlı metrik long, short ve toplam için
ayrı hesaplanır. Özsermaye eğrisinden gelen metrikler (hesap getirisi, hesap max drawdown,
hesap Sharpe) yön bazında ayrıştırılamaz — tek bir bakiye vardır — bu yüzden onlar
`AccountStats` altında açıkça "hesap düzeyi" olarak raporlanır. Yönlerin kendi risk profili
R serisinden ölçülür: `r_sharpe` ve `max_drawdown_r`, o yönün kümülatif R eğrisi üzerinden.

Tanımsız bir metrik (işlem yok, varyans sıfır) `nan` döner; 0.0 döndürmek "ölçüldü ve
sıfır çıktı" ile "ölçülemedi"yi aynı sayıya indirger ve karşılaştırmayı sessizce bozar.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence

import pandas as pd

from core.config import get_setting
from core.data import bar_duration
from core.ledger import Ledger
from strategies.base import Direction

DIRECTIONS: tuple[Direction, ...] = ("long", "short")
TOTAL = "total"

_NAN = float("nan")
_DAYS_PER_YEAR = 365.0


@dataclass(frozen=True, kw_only=True)
class DirectionStats:
    """Tek bir yönün (ya da toplamın) işlem-tabanlı metrikleri. Başta R gelir."""

    direction: str
    trades: int
    avg_r: float
    median_r: float
    total_r: float
    r_sharpe: float
    max_drawdown_r: float
    win_rate: float
    avg_win_r: float
    avg_loss_r: float
    profit_factor: float
    pnl: float
    fees: float
    funding: float
    liquidations: int
    unmeasured: int  # risk_amount'ı olmayan, R'ye giremeyen satır sayısı


@dataclass(frozen=True, kw_only=True)
class AccountStats:
    """Özsermaye eğrisinden gelen, yön bazında ayrıştırılamayan hesap düzeyi metrikler."""

    initial_capital: float
    final_equity: float
    total_return: float
    max_drawdown: float
    sharpe: float
    bars: int


@dataclass(frozen=True, kw_only=True)
class ModelMetrics:
    model: str
    long: DirectionStats
    short: DirectionStats
    total: DirectionStats
    account: AccountStats

    def by_direction(self, direction: str) -> DirectionStats:
        return {"long": self.long, "short": self.short, TOTAL: self.total}[direction]


# --------------------------------------------------------------------------- #
# İşlem-tabanlı metrikler
# --------------------------------------------------------------------------- #
def r_multiple(row: Mapping[str, Any]) -> float | None:
    """İşlemin R katsayısı; riske edilen tutar bilinmiyorsa None (0.0 değil).

    Likidasyonda R -1'in altına iner (marjın tamamı gider) — bu bir hata değil, tam da
    ölçmek istediğimiz risk farkının görünür hâlidir.
    """
    risk = _to_float(row.get("risk_amount"))
    if risk is None or risk <= 0.0:
        return None
    pnl = _to_float(row.get("pnl"))
    return None if pnl is None else pnl / risk


def direction_stats(
    trades: Iterable[Mapping[str, Any]], *, direction: str = TOTAL
) -> DirectionStats:
    """`direction` ("long" | "short" | "total") için işlem metrikleri."""
    rows = [
        row
        for row in trades
        if direction == TOTAL or str(row.get("direction", "")) == direction
    ]
    r_values = [r for r in (r_multiple(row) for row in rows) if r is not None]
    wins = [r for r in r_values if r > 0.0]
    losses = [r for r in r_values if r < 0.0]
    loss_total = abs(sum(losses))

    return DirectionStats(
        direction=direction,
        trades=len(rows),
        avg_r=_mean(r_values),
        median_r=_median(r_values),
        total_r=sum(r_values) if r_values else _NAN,
        r_sharpe=_ratio(_mean(r_values), _stdev(r_values)),
        max_drawdown_r=_max_drawdown_r(r_values),
        win_rate=len(wins) / len(r_values) if r_values else _NAN,
        avg_win_r=_mean(wins),
        avg_loss_r=_mean(losses),
        profit_factor=_ratio(sum(wins), loss_total) if r_values else _NAN,
        pnl=_sum_column(rows, "pnl"),
        fees=_sum_column(rows, "fee"),
        funding=_sum_column(rows, "funding"),
        liquidations=sum(1 for row in rows if row.get("exit_reason") == "liquidation"),
        unmeasured=len(rows) - len(r_values),
    )


def _max_drawdown_r(r_values: Sequence[float]) -> float:
    """Kümülatif R eğrisinin tepe-dip en büyük düşüşü (negatif ya da 0.0).

    Yön bazında risk profilini özsermaye eğrisine bakmadan ölçmenin yolu budur: hesapta
    tek bakiye vardır, longun drawdown'ı shortunkinden ayrıştırılamaz ama R eğrileri
    ayrıştırılabilir.
    """
    if not r_values:
        return _NAN
    cumulative = 0.0
    peak = 0.0
    worst = 0.0
    for value in r_values:
        cumulative += value
        peak = max(peak, cumulative)
        worst = min(worst, cumulative - peak)
    return worst


# --------------------------------------------------------------------------- #
# Hesap düzeyi metrikler (equity.csv)
# --------------------------------------------------------------------------- #
def account_stats(
    equity_rows: Sequence[Mapping[str, Any]],
    *,
    initial_capital: float,
    periods_per_year: float,
) -> AccountStats:
    """Özsermaye eğrisinden getiri, max drawdown ve Sharpe.

    Sharpe bar getirileri üzerinden hesaplanır ve risksiz getiri 0 alınır: modeller aynı
    anda, aynı para birimiyle ve aynı ufukta yarıştığı için ortak bir sabit çıkarmak
    sıralamayı değiştirmez, ama uydurulmuş bir oran karşılaştırmayı bozabilirdi.
    """
    equity = [value for value in (_to_float(row.get("equity")) for row in equity_rows) if value is not None]
    if not equity:
        return AccountStats(
            initial_capital=initial_capital,
            final_equity=initial_capital,
            total_return=_NAN,
            max_drawdown=_NAN,
            sharpe=_NAN,
            bars=0,
        )

    returns = [
        equity[index] / equity[index - 1] - 1.0
        for index in range(1, len(equity))
        if equity[index - 1] > 0.0
    ]
    return AccountStats(
        initial_capital=initial_capital,
        final_equity=equity[-1],
        total_return=(equity[-1] / initial_capital - 1.0) if initial_capital > 0.0 else _NAN,
        max_drawdown=_max_drawdown_pct(equity),
        sharpe=_ratio(_mean(returns), _stdev(returns)) * math.sqrt(periods_per_year),
        bars=len(equity),
    )


def _max_drawdown_pct(equity: Sequence[float]) -> float:
    peak = equity[0]
    worst = 0.0
    for value in equity:
        peak = max(peak, value)
        if peak > 0.0:
            worst = min(worst, value / peak - 1.0)
    return worst


def periods_per_year(config: Mapping[str, Any]) -> float:
    duration = bar_duration(str(get_setting(dict(config), "timeframe")))
    return pd.Timedelta(days=_DAYS_PER_YEAR) / duration


# --------------------------------------------------------------------------- #
# Model ve karşılaştırma
# --------------------------------------------------------------------------- #
def model_metrics(
    model: str,
    *,
    trades: Sequence[Mapping[str, Any]],
    equity_rows: Sequence[Mapping[str, Any]],
    initial_capital: float,
    periods_per_year: float,
) -> ModelMetrics:
    return ModelMetrics(
        model=model,
        long=direction_stats(trades, direction="long"),
        short=direction_stats(trades, direction="short"),
        total=direction_stats(trades, direction=TOTAL),
        account=account_stats(
            equity_rows, initial_capital=initial_capital, periods_per_year=periods_per_year
        ),
    )


def compare(
    models: Sequence[str], *, ledger: Ledger | None = None, config: Mapping[str, Any]
) -> list[ModelMetrics]:
    """Defterleri okuyup her model için metrikleri üretir. Defter değiştirilmez."""
    active_ledger = ledger if ledger is not None else Ledger()
    config_dict = dict(config)
    initial_capital = float(get_setting(config_dict, "initial_capital"))
    per_year = periods_per_year(config_dict)
    return [
        model_metrics(
            model,
            trades=active_ledger.read_trades(model),
            equity_rows=active_ledger.read_equity(model),
            initial_capital=initial_capital,
            periods_per_year=per_year,
        )
        for model in models
    ]


# --------------------------------------------------------------------------- #
# Rapor
# --------------------------------------------------------------------------- #
_HEADERS = ("model", "yön", "n", "ort.R", "medyan R", "topl.R", "R-Sharpe",
            "maxDD(R)", "kazanç%", "PF", "PnL(USDT)", "likid.")
_WIDTHS = (18, 6, 5, 8, 9, 8, 9, 9, 8, 7, 12, 7)


def format_report(metrics: Sequence[ModelMetrics]) -> str:
    """Karşılaştırma tablosu. Kolon sırası bilinçlidir: önce R, sonra USDT getirisi.

    Sıralama da ortalama R'ye göredir — tabloyu toplam getiriye göre sıralamak, tam da
    ayıklamaya çalıştığımız bileşiklenme etkisini geri sokardı.
    """
    lines = [
        "  ".join(header.rjust(width) if index else header.ljust(width)
                  for index, (header, width) in enumerate(zip(_HEADERS, _WIDTHS))),
        "-" * (sum(_WIDTHS) + 2 * (len(_WIDTHS) - 1)),
    ]
    ordered = sorted(
        metrics,
        key=lambda item: (-item.total.avg_r if not math.isnan(item.total.avg_r) else math.inf,
                          item.model),
    )
    for item in ordered:
        for index, direction in enumerate((*DIRECTIONS, TOTAL)):
            stats = item.by_direction(direction)
            label = item.model if index == 0 else ""
            cells = (
                label.ljust(_WIDTHS[0]),
                ("TOPLAM" if direction == TOTAL else direction).rjust(_WIDTHS[1]),
                str(stats.trades).rjust(_WIDTHS[2]),
                _fmt(stats.avg_r).rjust(_WIDTHS[3]),
                _fmt(stats.median_r).rjust(_WIDTHS[4]),
                _fmt(stats.total_r).rjust(_WIDTHS[5]),
                _fmt(stats.r_sharpe).rjust(_WIDTHS[6]),
                _fmt(stats.max_drawdown_r).rjust(_WIDTHS[7]),
                _fmt(_pct(stats.win_rate), digits=1).rjust(_WIDTHS[8]),
                _fmt(stats.profit_factor).rjust(_WIDTHS[9]),
                _fmt(stats.pnl, digits=2).rjust(_WIDTHS[10]),
                str(stats.liquidations).rjust(_WIDTHS[11]),
            )
            lines.append("  ".join(cells))
        account = item.account
        lines.append(
            f"{'':<{_WIDTHS[0]}}  hesap: son özsermaye {_fmt(account.final_equity, digits=2)} | "
            f"getiri {_fmt(_pct(account.total_return), digits=2)}% | "
            f"maxDD {_fmt(_pct(account.max_drawdown), digits=2)}% | "
            f"Sharpe {_fmt(account.sharpe)} | {account.bars} bar"
        )
        unmeasured = item.total.unmeasured
        if unmeasured:
            lines.append(
                f"{'':<{_WIDTHS[0]}}  UYARI: {unmeasured} işlemde risk_amount yok, R'ye girmedi"
            )
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


# --------------------------------------------------------------------------- #
# Yardımcılar
# --------------------------------------------------------------------------- #
def _to_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _sum_column(rows: Iterable[Mapping[str, Any]], column: str) -> float:
    return sum(value for value in (_to_float(row.get(column)) for row in rows) if value is not None)


def _mean(values: Sequence[float]) -> float:
    return sum(values) / len(values) if values else _NAN


def _median(values: Sequence[float]) -> float:
    if not values:
        return _NAN
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2.0


def _stdev(values: Sequence[float]) -> float:
    """Örneklem standart sapması; iki işlemden azında tanımsızdır."""
    if len(values) < 2:
        return _NAN
    mean = _mean(values)
    return math.sqrt(sum((value - mean) ** 2 for value in values) / (len(values) - 1))


def _ratio(numerator: float, denominator: float) -> float:
    if math.isnan(numerator) or math.isnan(denominator) or denominator == 0.0:
        return _NAN
    return numerator / denominator


def _pct(value: float) -> float:
    return value * 100.0 if not math.isnan(value) else _NAN


def _fmt(value: float, *, digits: int = 2) -> str:
    return "—" if value is None or math.isnan(value) else f"{value:.{digits}f}"

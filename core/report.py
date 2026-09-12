"""Dashboard yükü: `docs/data/metrics.json`'un tablo DIŞINDA kalan bölümleri.

`core/metrics.py` "model ne kadar iyi" sorusunu cevaplar ve sayı üretir. Burası o
sayıların yanına, tek bir sayfada okunabilmeleri için gereken BAĞLAMI koyar: hangi
pozisyonlar hâlâ açık, son işlemler hangi gerekçeyle kapandı, özsermaye eğrileri nereden
geçti, son 24 saatte ne oldu. İkisi bilinçli olarak ayrı modüldür — metrics salt
okunur bir ÖLÇÜM modülüdür ve bir sunum katmanının ihtiyaçları (kaç satır gösterilecek,
eğri kaç noktaya seyreltilecek) oraya sızarsa ölçümün tanımı sunum kararlarına bağlanır.

Bu modül de salt okunurdur: defteri ve anlık görüntüyü okur, hiçbir şey yazmaz ve hiçbir
şey hesaplamaz ki `core/portfolio.py` zaten hesaplamış olsun. Açık pozisyonun güncel
PnL'i tek istisnadır ve bilinçli olarak portfolio'nun kapanış formülüyle AYNI parçalardan
kurulur (brüt fiyat farkı − giriş komisyonu + funding); çıkış maliyeti dâhil değildir,
çünkü pozisyon henüz kapanmamıştır ve kapanış fiyatı bilinmez. Dashboard'da bu ayrım
etiketle görünür.

Yüke giren her şey `docs/data/metrics.json`'da durur: sayfa statiktir (GitHub Pages,
build adımı yok) ve defteri kendisi okuyamaz.
"""

from __future__ import annotations

import logging
import math
from dataclasses import asdict
from typing import Any, Mapping, Sequence

import pandas as pd

from core.config import get_setting
from core.ledger import Ledger
from core.metrics import (
    ModelMetrics,
    acceptance_flags,
    pooled_direction_stats,
    return_correlation,
)
from strategies.base import MarketData

logger = logging.getLogger(__name__)

# Son N işlem ve eğri seyreltmesi ÖLÇÜM sabiti değil sunum sabitidir, bu yüzden
# config.yaml'da değil burada durur: config.yaml "tüm modeller için birebir aynı
# ölçüm koşulu" sözleşmesidir (kural 6), kaç satır çizildiği o sözleşmenin parçası değil.
RECENT_TRADE_LIMIT = 20
EQUITY_MAX_POINTS = 500  # eğri başına; koşu aylarca sürdüğünde JSON sınırsız büyümesin
ACTIVITY_HOURS = 24


def build_dashboard(
    metrics: Sequence[ModelMetrics],
    *,
    ledger: Ledger,
    config: Mapping[str, Any],
    market: MarketData,
) -> dict[str, Any]:
    """`docs/data/metrics.json`'a eklenen dashboard bölümleri.

    Havuz (`pooled`) ve kabul bayrakları YALNIZCA yarışmacılardan hesaplanır: referans
    çıpasının R'si yoktur (kural 15), havuzun ortalama R'sine katılması "stop'suz bir
    işlemi 1R'lik bir işlemmiş gibi saymak" olurdu. Korelasyon matrisi ise çıpayı DA
    içerir — orada ölçülen R değil bar getirisidir ve "modeller piyasadan ne kadar
    ayrışıyor" sorusunun cevabı tam olarak çıpayla karşılaştırmayı gerektirir.
    """
    config_dict = dict(config)
    competitors = [item.model for item in metrics if not item.is_benchmark]
    models = [item.model for item in metrics]

    trades = {model: ledger.read_trades(model) for model in models}
    equity = {model: ledger.read_equity(model) for model in models}
    marks = marks_from_market(market)

    pooled = pooled_direction_stats({model: trades[model] for model in competitors})
    flags = acceptance_flags(
        metrics,
        min_trades=int(get_setting(config_dict, "acceptance.min_trades")),
        stop_band_ratio=float(get_setting(config_dict, "acceptance.stop_band_ratio")),
        control_model=str(get_setting(config_dict, "acceptance.control_model")),
        edge_margin_r=float(get_setting(config_dict, "acceptance.edge_margin_r")),
    )
    positions = open_positions(models, ledger=ledger, marks=marks)

    return {
        "pooled": {
            "models": competitors,
            "directions": {
                direction: asdict(stats) for direction, stats in pooled.items()
            },
        },
        "acceptance": {
            "control_model": str(get_setting(config_dict, "acceptance.control_model")),
            "min_trades": int(get_setting(config_dict, "acceptance.min_trades")),
            "edge_margin_r": float(get_setting(config_dict, "acceptance.edge_margin_r")),
            "stop_band_ratio": float(get_setting(config_dict, "acceptance.stop_band_ratio")),
            "models": [asdict(item) for item in flags],
        },
        "correlation": return_correlation(equity),
        "equity": {model: equity_series(rows) for model, rows in equity.items()},
        "open_positions": positions,
        "recent_trades": recent_trades(trades, limit=RECENT_TRADE_LIMIT),
        "activity": activity(
            trades, positions=positions, as_of=market.as_of, hours=ACTIVITY_HOURS
        ),
    }


# --------------------------------------------------------------------------- #
# Fiyat çıpası
# --------------------------------------------------------------------------- #
def marks_from_market(market: MarketData) -> dict[str, float]:
    """Sembol -> `as_of` barının KAPANIŞI.

    Kural 12: yalnızca kapanmış barlar. Son satırı körlemesine almak yerine `as_of`
    satırı aranır — bir sembolün serisi daha ileri giderse (önbellek yenilendi, tur
    gecikti) o barın fiyatı turun "şimdi"sinden ileride olur ve açık pozisyonlar
    stratejilerin görmediği bir fiyattan işaretlenirdi.
    """
    marks: dict[str, float] = {}
    for symbol, frame in market.ohlcv.items():
        if frame is None or frame.empty or "close" not in frame.columns:
            continue
        if market.as_of in frame.index:
            marks[symbol] = float(frame.loc[market.as_of, "close"])
        else:
            logger.debug("%s serisinde as_of barı yok, işaretleme dışı", symbol)
    return marks


# --------------------------------------------------------------------------- #
# Özsermaye eğrileri
# --------------------------------------------------------------------------- #
def equity_series(
    equity_rows: Sequence[Mapping[str, Any]], *, max_points: int = EQUITY_MAX_POINTS
) -> list[list[Any]]:
    """`[[ts, equity], ...]`. Uzun eğriler EŞİT ARALIKLA seyreltilir.

    Seyreltme tepe/dip değil aralık üzerinden yapılır: "en uç noktaları koru" biçiminde
    bir seyreltme eğriyi olduğundan oynak gösterir. İlk ve son nokta her zaman korunur,
    böylece başlangıç sermayesi ve son özsermaye grafikten okunabilir kalır. Max drawdown
    gibi uç değerler zaten tablodan (hesap düzeyi metrikler) okunur, grafikten değil.
    """
    points: list[list[Any]] = []
    for row in equity_rows:
        ts = str(row.get("ts", ""))
        value = _to_float(row.get("equity"))
        if ts and value is not None:
            points.append([ts, value])
    if len(points) <= max_points or max_points < 2:
        return points
    step = (len(points) - 1) / (max_points - 1)
    indexes = sorted({int(round(index * step)) for index in range(max_points)} | {len(points) - 1})
    return [points[index] for index in indexes]


# --------------------------------------------------------------------------- #
# Açık pozisyonlar
# --------------------------------------------------------------------------- #
def open_positions(
    models: Sequence[str], *, ledger: Ledger, marks: Mapping[str, float]
) -> list[dict[str, Any]]:
    """Her modelin `positions.json`'daki açık pozisyonları, güncel fiyatla işaretlenmiş.

    `pnl` ÇIKIŞ MALİYETİ HARİÇTİR: pozisyon kapanmadığı için çıkış fiyatı da komisyonu da
    bilinmez. Uydurmak (örn. mevcut fiyattan kapanmış saymak) deftere hiç girmeyecek bir
    sayıyı kapanmış işlemlerin yanına koyardı; sayfa bunu etiketiyle söyler.

    `r` payda olarak İLK stop'tan gelen risk tutarını kullanır — kapanan işlemlerdeki
    `risk_amount` ile birebir aynı tanım (bkz. core/portfolio.py::_close), ki açık ve
    kapalı işlemler aynı birimde okunabilsin. Stop'suz referans pozisyonlarda `nan`.
    """
    rows: list[dict[str, Any]] = []
    for model in models:
        state = ledger.load_state(model)
        if not state:
            continue
        for payload in state.get("positions", ()) or ():
            rows.append(_position_row(model, payload, marks))
    rows.sort(key=lambda row: (row["model"], row["symbol"]))
    return rows


def _position_row(
    model: str, payload: Mapping[str, Any], marks: Mapping[str, float]
) -> dict[str, Any]:
    symbol = str(payload.get("symbol", ""))
    direction = str(payload.get("direction", ""))
    qty = _to_float(payload.get("qty")) or 0.0
    entry = _to_float(payload.get("entry_price")) or 0.0
    initial_stop = _to_float(payload.get("initial_stop_price"))
    funding = _to_float(payload.get("funding")) or 0.0
    entry_fee = _to_float(payload.get("entry_fee")) or 0.0
    # Fiyatı olmayan sembolde giriş fiyatı kullanılır: bilgi yokken pozisyonu kâr ya da
    # zararda göstermek uydurmak olurdu (core/portfolio.py::_mark ile aynı kural).
    mark = float(marks.get(symbol, entry))
    sign = 1.0 if direction == "long" else -1.0
    gross = sign * qty * (mark - entry)
    pnl = gross - entry_fee + funding
    risk = None if initial_stop is None else qty * abs(entry - initial_stop)

    return {
        "model": model,
        "symbol": symbol,
        "direction": direction,
        "opened_at": str(payload.get("opened_at", "")),
        "qty": qty,
        "entry_price": entry,
        "mark_price": mark,
        "marked": symbol in marks,
        "stop_price": _to_float(payload.get("stop_price")),
        "initial_stop_price": initial_stop,
        "liq_price": _to_float(payload.get("liq_price")),
        "leverage": _to_float(payload.get("leverage")),
        "notional": qty * entry,
        "gross_pnl": gross,
        "entry_fee": entry_fee,
        "funding": funding,
        "pnl": pnl,
        # Yüzde NET PnL'den türetilir, brütten değil: iki sayı yan yana duruyor ve
        # birinin artı diğerinin eksi görünmesi (brüt kârda ama komisyon sonrası
        # zararda bir pozisyon) okuyucuya çelişki gibi gelirdi.
        "pnl_pct": (pnl / (qty * entry) * 100.0) if qty * entry > 0.0 else float("nan"),
        "r": (pnl / risk) if risk else float("nan"),
        "trailing_atr": _to_float(payload.get("trailing_atr")),
        "reason": str(payload.get("reason", "")),
    }


# --------------------------------------------------------------------------- #
# Son işlemler ve 24 saatlik hareket
# --------------------------------------------------------------------------- #
def recent_trades(
    trades_by_model: Mapping[str, Sequence[Mapping[str, Any]]],
    *,
    limit: int = RECENT_TRADE_LIMIT,
) -> list[dict[str, Any]]:
    """Tüm modellerin defterlerinden, kapanış zamanına göre en yeni `limit` işlem.

    `signal_reason` kırpılmadan taşınır: bir modelin neden o işlemi açtığı (ensemble'ın
    oy sayısı, confluence'ın güven kuyruğu) tablodaki sayıdan çok daha fazlasını söyler
    ve denetim izinin okunabilir yüzü tam olarak budur.
    """
    rows = [
        {**dict(row), "model": model}
        for model, trades in trades_by_model.items()
        for row in trades
    ]
    rows.sort(key=lambda row: str(row.get("closed_at", "")), reverse=True)
    return [_trade_row(row) for row in rows[: max(0, limit)]]


def _trade_row(row: Mapping[str, Any]) -> dict[str, Any]:
    risk = _to_float(row.get("risk_amount"))
    pnl = _to_float(row.get("pnl"))
    return {
        "model": str(row.get("model", row.get("strategy", ""))),
        "symbol": str(row.get("symbol", "")),
        "direction": str(row.get("direction", "")),
        "opened_at": str(row.get("opened_at", "")),
        "closed_at": str(row.get("closed_at", "")),
        "entry_price": _to_float(row.get("entry_price")),
        "exit_price": _to_float(row.get("exit_price")),
        "stop_price": _to_float(row.get("stop_price")),
        "pnl": pnl,
        "fee": _to_float(row.get("fee")),
        "slippage_cost": _to_float(row.get("slippage_cost")),
        "funding": _to_float(row.get("funding")),
        "r": (pnl / risk) if (risk and pnl is not None) else float("nan"),
        "exit_reason": str(row.get("exit_reason", "")),
        "reason": str(row.get("signal_reason", "")),
    }


def activity(
    trades_by_model: Mapping[str, Sequence[Mapping[str, Any]]],
    *,
    positions: Sequence[Mapping[str, Any]],
    as_of: pd.Timestamp,
    hours: int = ACTIVITY_HOURS,
) -> dict[str, Any]:
    """Son `hours` saatte açılan ve kapanan işlemler — Telegram özetinin "o gün" bölümü.

    Pencere duvar saatinden değil `as_of`'tan geriye sayılır: koşu geciktiğinde ya da
    elle tekrarlandığında duvar saati penceresi turun gerçekten işlediği barlarla
    örtüşmez ve özet, olmayan bir sessizliği rapor ederdi.

    Açılan işlem sayılırken HÂLÂ AÇIK pozisyonlar da sayılır: yalnızca kapananlara
    bakmak, pencerede açılıp açık kalan bir pozisyonu hiç olmamış gibi gösterirdi.
    """
    since = pd.Timestamp(as_of) - pd.Timedelta(hours=int(hours))
    since_text = since.isoformat()

    closed = [
        _trade_row({**dict(row), "model": model})
        for model, trades in trades_by_model.items()
        for row in trades
        if str(row.get("closed_at", "")) >= since_text
    ]
    closed.sort(key=lambda row: str(row["closed_at"]), reverse=True)

    opened_closed = sum(
        1
        for model, trades in trades_by_model.items()
        for row in trades
        if str(row.get("opened_at", "")) >= since_text
    )
    opened_open = [row for row in positions if str(row.get("opened_at", "")) >= since_text]

    r_values = [row["r"] for row in closed if not math.isnan(row["r"])]
    pnl_values = [row["pnl"] for row in closed if row["pnl"] is not None]

    return {
        "since": since_text,
        "hours": int(hours),
        "opened": opened_closed + len(opened_open),
        "still_open": len(opened_open),
        "closed": len(closed),
        "closed_pnl": sum(pnl_values) if pnl_values else 0.0,
        "closed_avg_r": (sum(r_values) / len(r_values)) if r_values else float("nan"),
        "closed_wins": sum(1 for value in r_values if value > 0.0),
        "trades": closed,
    }


def _to_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None

"""Strateji çalıştırma orkestrasyonu (CLAUDE.md kural 4/9/13).

Bir TUR şu akıştan ibarettir:

    A. Defterleri yükle (core/ledger.py) ve hesapları kur (core/portfolio.py).
    B. Son işlenmiş bardan `as_of`'a kadar geçen her barı SIRAYLA ilerlet:
         1. funding tahakkuku (bara taşınan pozisyonlara, barın açılış fiyatından)
         2. bekleyen emirlerin dolumu — barın AÇILIŞINDAN (kural 13)
         3. mum içi kontrol: likidasyon -> stop -> TP (core/portfolio.py)
         4. trailing stop güncellemesi (kontrolden SONRA)
         5. bar kapanışında özsermaye kaydı
    C. `as_of` barında sinyal üret: önce normal modeller, sonra meta modeller (kural 4),
       ardından stop mesafesi bandını aşan sinyalleri ele (kural 14).
    D. manage_positions ile çıkış talimatlarını topla.
    E. C ve D'nin ürettikleri bekleyen emir olarak kuyruğa girer: bir SONRAKİ barın
       açılışında dolarlar. Defter atomik olarak yazılır.

Neden bekleyen emir kuyruğu: kural 13 sinyalin üretildiği barda değil bir sonraki barın
açılışında dolmasını şart koşar. Tur `as_of` barında biter, yani dolum bir sonraki turun
işlediği ilk bardır — emirlerin koşular arasında defterde taşınması bu yüzden zorunludur.

Neden trailing stop kontrolden SONRA güncellenir: barın high/low'una bakıp aynı barın
stop'unu değiştirmek, o barın içinde geçmişe dönük karar vermek olurdu (kural 12). Yeni
stop ancak bir sonraki barda geçerlidir.

Bu modül iş mantığı taşımaz: fiyat/komisyon/marj kararları core/portfolio.py'de, funding
kuralı core/funding.py'de, doğrulama core/validate.py'dedir.
"""

from __future__ import annotations

import copy
import logging
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Iterable, Literal, Mapping, Sequence

import pandas as pd

from core import funding as funding_module
from core.config import get_setting, load_config
from core.data import bar_duration
# ATR tanımı core/indicators.py'de tektir: trailing mesafesi (kural 9), stop bandı
# (kural 14) ve stratejilerin stop'ları aynı sayıyı görmek zorundadır. Ad burada
# yeniden dışa verilir — motorun ATR'yi "kendi" hesaplaması bu garantiyi bozardı.
from core.indicators import average_true_range
from core.ledger import Ledger
# R'nin tek tanımı core/metrics.py'dedir (pnl / risk_amount). Motorun kendi bölmesi,
# modelin öğrendiği R ile tabloda raporlanan R'nin sessizce ayrışması demekti.
from core.metrics import r_multiple
from core.portfolio import SIZING_FAILURES, Bar, Portfolio, Trade
from core.validate import validate_signal
from strategies.base import (
    ClosedTrade,
    Direction,
    ExitInstruction,
    MarketData,
    Signal,
    SizingMode,
    Strategy,
    TakeProfit,
)

logger = logging.getLogger(__name__)

OrderKind = Literal["open", "exit"]


@dataclass(frozen=True, kw_only=True)
class PendingOrder:
    """Üretildiği barda değil, bir sonraki barın açılışında dolacak emir (kural 13)."""

    kind: OrderKind
    symbol: str
    direction: Direction
    created_at: pd.Timestamp
    # Stop'suz referans emirlerinde (kural 15) None kalır; 0.0 "stop sıfırda" demek olurdu.
    stop_price: float | None = None
    sizing: SizingMode = "risk"
    notional_fraction: float | None = None
    take_profits: tuple[TakeProfit, ...] = ()
    trailing_atr: float | None = None
    fraction: float = 1.0
    reason: str = ""

    def as_state(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "symbol": self.symbol,
            "direction": self.direction,
            "created_at": self.created_at.isoformat(),
            "stop_price": self.stop_price,
            "sizing": self.sizing,
            "notional_fraction": self.notional_fraction,
            "take_profits": [{"price": tp.price, "fraction": tp.fraction} for tp in self.take_profits],
            "trailing_atr": self.trailing_atr,
            "fraction": self.fraction,
            "reason": self.reason,
        }

    @classmethod
    def from_state(cls, payload: Mapping[str, Any]) -> "PendingOrder":
        return cls(
            kind=str(payload["kind"]),  # type: ignore[arg-type]
            symbol=str(payload["symbol"]),
            direction=str(payload["direction"]),  # type: ignore[arg-type]
            created_at=_to_utc(payload["created_at"]),
            # Eski defterlerde alan yoktu: varsayılan "risk", stop 0.0 yerine None'a düşer
            # ve exit emirlerinde zaten kullanılmaz.
            stop_price=_opt_float(payload.get("stop_price")),
            sizing=str(payload.get("sizing", "risk")),  # type: ignore[arg-type]
            notional_fraction=_opt_float(payload.get("notional_fraction")),
            take_profits=tuple(
                TakeProfit(price=float(tp["price"]), fraction=float(tp["fraction"]))
                for tp in payload.get("take_profits", ())
            ),
            trailing_atr=(
                None if payload.get("trailing_atr") is None else float(payload["trailing_atr"])
            ),
            fraction=float(payload.get("fraction", 1.0)),
            reason=str(payload.get("reason", "")),
        )


@dataclass(frozen=True, kw_only=True)
class ModelReport:
    model: str
    bars_processed: int = 0
    filled: int = 0
    closed: int = 0
    signals: int = 0
    exits: int = 0
    skipped_signals: int = 0  # stop bandı nedeniyle elenen sinyal sayısı (kural 14)
    # Anlık görüntünün `last_processed_bar`a ulaşamadığı, yani TELAFİ EDİLEMEYEN bar sayısı.
    # Olağan bir cron gecikmesinde 0'dır: atlanan turların barları bu turda sırayla işlenir.
    # Sıfırdan büyük olması, o barlarda stop/TP/likidasyon kontrolünün hiç yapılmadığı ve
    # funding'in hiç tahakkuk etmediği anlamına gelir — raporda durur ki atlama sessiz
    # kalmasın (bkz. Engine._record_missing_bars).
    missing_bars: int = 0
    # Doldurulamayan emirlerin SEBEP KODU -> adet dökümü (bkz. core/portfolio.RejectReason).
    # Bu alan olmadan "sinyal üretildi ama işlem açılmadı" tek bir görünüme çöker ve beklenen
    # bir tekrar (referansın zaten taşıdığı pozisyon) gerçek bir boyutlandırma arızasından
    # ayırt edilemez. Serbest metin gerekçe yalnızca logda; burada sayılabilir kod durur.
    rejections: Mapping[str, int] = field(default_factory=dict)
    skipped: str = ""


@dataclass(frozen=True, kw_only=True)
class RoundReport:
    as_of: pd.Timestamp
    models: tuple[ModelReport, ...] = ()

    def by_model(self, model: str) -> ModelReport | None:
        for report in self.models:
            if report.model == model:
                return report
        return None


@dataclass
class _ModelRun:
    """Tur boyunca tek bir modelin biriken durumu."""

    strategy: Strategy
    state: dict[str, Any]
    pending: list[PendingOrder]
    last_bar: pd.Timestamp | None
    trades: list[Trade] = field(default_factory=list)
    equity_rows: list[dict[str, Any]] = field(default_factory=list)
    reached_as_of: bool = False
    bars_processed: int = 0
    filled: int = 0
    signals: int = 0
    exits: int = 0
    skipped_signals: int = 0
    missing_bars: int = 0
    history_failed: bool = False
    rejections: dict[str, int] = field(default_factory=dict)
    skipped: str = ""

    def reject(self, code: str) -> None:
        self.rejections[code] = self.rejections.get(code, 0) + 1


class Engine:
    """Turu yürüten orkestratör. Strateji listesi dışarıdan verilir (test edilebilirlik)."""

    def __init__(
        self,
        strategies: Sequence[Strategy],
        *,
        config: Mapping[str, Any] | None = None,
        ledger: Ledger | None = None,
        portfolio: Portfolio | None = None,
    ) -> None:
        self._config: dict[str, Any] = dict(config) if config is not None else load_config()
        self._strategies = list(strategies)
        _assert_unique_names(self._strategies)
        self._ledger = ledger if ledger is not None else Ledger()
        self._portfolio = portfolio if portfolio is not None else Portfolio(self._config)
        self._initial_capital = float(get_setting(self._config, "initial_capital"))
        self._bar_duration = bar_duration(str(get_setting(self._config, "timeframe")))
        self._atr_period = int(get_setting(self._config, "trailing.atr_period"))
        self._max_stop_atr_multiple = float(get_setting(self._config, "max_stop_atr_multiple"))
        self._funding_enabled, self._funding_interval = funding_module.settings(self._config)

    # ------------------------------------------------------------------ #
    # Tur
    # ------------------------------------------------------------------ #
    def run_round(self, market: MarketData) -> RoundReport:
        universe = list(market.ohlcv)
        runs = [self._load_model(strategy) for strategy in self._strategies]

        for run in runs:
            self._advance(run, market)

        # Sinyal üretimi yalnızca `as_of` barına BU turda ulaşan modeller için çalışır.
        # Aynı `as_of` ile ikinci kez koşmak (elle tekrar, cron retry) aksi hâlde aynı
        # sinyali ikinci kez kuyruğa alır ve model tek bir bar için çift pozisyon açardı.
        fresh = [run for run in runs if run.reached_as_of]
        for run in fresh:
            self._observe_history(run)
        signals = self._collect_signals(fresh, market, universe=universe)
        for run in runs:
            if not run.reached_as_of:
                logger.info(
                    "%s: %s barı zaten işlenmiş, bu turda sinyal üretilmedi",
                    run.strategy.name,
                    market.as_of,
                )
                self._persist(run, market)
                continue
            model_signals = self._within_stop_band(run, signals.get(run.strategy.name, []), market)
            run.signals = len(model_signals)
            run.pending.extend(
                PendingOrder(
                    kind="open",
                    symbol=signal.symbol,
                    direction=signal.direction,
                    created_at=market.as_of,
                    stop_price=signal.stop_price,
                    sizing=signal.sizing,
                    notional_fraction=signal.notional_fraction,
                    take_profits=signal.take_profits,
                    trailing_atr=signal.trailing_atr,
                    reason=signal.reason,
                )
                for signal in model_signals
            )
            self._collect_exits(run, market)
            self._persist(run, market)

        return RoundReport(
            as_of=market.as_of,
            models=tuple(
                ModelReport(
                    model=run.strategy.name,
                    bars_processed=run.bars_processed,
                    filled=run.filled,
                    closed=len(run.trades),
                    signals=run.signals,
                    exits=run.exits,
                    skipped_signals=run.skipped_signals,
                    missing_bars=run.missing_bars,
                    rejections=dict(sorted(run.rejections.items())),
                    skipped=run.skipped,
                )
                for run in runs
            ),
        )

    # ------------------------------------------------------------------ #
    # A) Defter yükleme
    # ------------------------------------------------------------------ #
    def _load_model(self, strategy: Strategy) -> _ModelRun:
        state = self._ledger.initialize_model(
            strategy.name, initial_capital=self._initial_capital
        )
        self._portfolio.load_state(strategy.name, state)
        last_bar = state.get("last_processed_bar")
        return _ModelRun(
            strategy=strategy,
            state=dict(state),
            pending=[PendingOrder.from_state(item) for item in state.get("pending_orders", ())],
            last_bar=None if last_bar is None else _to_utc(last_bar),
        )

    # ------------------------------------------------------------------ #
    # B) Barları ilerlet
    # ------------------------------------------------------------------ #
    def _advance(self, run: _ModelRun, market: MarketData) -> None:
        model = run.strategy.name
        timeline = self._timeline(run, market)

        for ts in timeline:
            bars = _bars_at(market, ts)
            opens = {symbol: bar.open for symbol, bar in bars.items()}

            charges = funding_module.accrue(
                self._portfolio.positions(model),
                bar_open=ts,
                bar_close=ts + self._bar_duration,
                prices=opens,
                funding=market.funding,
                interval_hours=self._funding_interval,
                enabled=self._funding_enabled,
                model=model,
            )
            self._portfolio.apply_funding(model, charges)

            run.filled += self._fill_pending(run, ts=ts, bars=bars, marks=opens)
            run.trades.extend(self._portfolio.process_bar(model, ts=ts, bars=bars))
            self._update_trailing_stops(run, market, ts=ts)

            closes = {symbol: bar.close for symbol, bar in bars.items()}
            run.equity_rows.append(self._equity_row(model, ts=ts, marks=closes))
            run.bars_processed += 1
            run.last_bar = ts
            run.reached_as_of = run.reached_as_of or ts == market.as_of

    def _timeline(self, run: _ModelRun, market: MarketData) -> list[pd.Timestamp]:
        """İşlenecek barlar: BTC çıpasının zaman ızgarasında son işlenenden `as_of`'a kadar.

        Izgara BTC'nindir çünkü `as_of`'un tanımı da odur (core/data.py); her modelin kendi
        sembollerinin ızgarasını kullanmak, aynı turda modellerin farklı sayıda bar
        ilerlemesi demek olurdu.

        Tur ATLANDIĞINDA (cron gecikmesi/atlaması) aradaki barlar burada geri gelir ve
        `_advance` hepsini SIRAYLA işler: bekleyen emirler kendi barının açılışından dolar,
        stop/TP/likidasyon her barın kendi high/low'uyla kontrol edilir. Yalnızca son bara
        atlamak, atlanan barlardaki stop'ları hiç tetiklemeyip pozisyonu ölçümde hayatta
        tutardı.

        Defteri yeni açılan model için yalnızca `as_of` işlenir: ortada ne pozisyon ne
        bekleyen emir varken geçmişi geriye dönük işlemek yalnızca boş özsermaye satırları
        üretirdi.
        """
        index = [ts for ts in market.btc.index if ts <= market.as_of]
        if run.last_bar is None:
            return index[-1:]
        timeline = [ts for ts in index if ts > run.last_bar]
        self._record_missing_bars(run, market, last_bar=run.last_bar, timeline=timeline)
        return timeline

    def _record_missing_bars(
        self,
        run: _ModelRun,
        market: MarketData,
        *,
        last_bar: pd.Timestamp,
        timeline: Sequence[pd.Timestamp],
    ) -> None:
        """Anlık görüntünün `last_processed_bar`a ULAŞAMADIĞI barları sayar ve söyler.

        Telafi yalnızca bar elimizdeyse mümkündür. Çıpanın penceresi son işlenmiş bara
        kadar geri gitmiyorsa (kesinti `data.history_bars`ı aşmış, ya da seride delik var)
        aradaki barlar hiç işlenmez — ama `last_processed_bar` yine `as_of`a taşınır, yani
        defter o barları işlenmiş SAYAR. Bu, kural 14/15'in yasakladığı sessiz atlamanın
        ta kendisidir: o barlarda tetiklenmesi gereken stop/TP/likidasyon hiç sorulmamış,
        funding hiç tahakkuk etmemiş olur ve sonraki satırlar eksik bir geçmişin üstüne
        yazılır.

        Tur DÜŞÜRÜLMEZ: borsanın penceresinden düşmüş bar geri getirilemez, hata vermek
        katmanı kalıcı olarak kilitlerdi. Bunun yerine atlama denetlenebilir kayda
        dönüşür — sayı tur raporuna (ve metrics JSON'una) girer.

        Log seviyesi ayrımı taşır: modelin o boşluğa taşıdığı pozisyon ya da bekleyen
        emir varsa ölçüm gerçekten etkilenmiştir (WARNING); açık hesapla geçilen boşluk
        yalnızca eksik özsermaye satırı demektir (INFO).
        """
        grid = pd.date_range(last_bar + self._bar_duration, market.as_of, freq=self._bar_duration)
        missing = len(grid) - len(set(grid) & set(timeline))
        if missing <= 0:
            return

        run.missing_bars = missing
        model = run.strategy.name
        exposed = bool(self._portfolio.positions(model)) or bool(run.pending)
        logger.log(
            logging.WARNING if exposed else logging.INFO,
            "%s: %s ile %s arasında %d bar anlık görüntüde yok, telafi edilemedi%s",
            model,
            last_bar,
            market.as_of,
            missing,
            " (o boşlukta açık pozisyon/bekleyen emir vardı)" if exposed else "",
        )

    def _fill_pending(
        self,
        run: _ModelRun,
        *,
        ts: pd.Timestamp,
        bars: Mapping[str, Bar],
        marks: Mapping[str, float],
    ) -> int:
        """Bekleyen emirleri barın açılışından doldurur; çıkışlar açılışlardan önce gelir.

        Çıkışın önce gelmesi, aynı turda hem kapanıp hem yeniden açılan bir sembolde
        max_positions kotasının yapay olarak dolu görünmesini engeller.
        """
        model = run.strategy.name
        due = [order for order in run.pending if order.created_at < ts]
        run.pending = [order for order in run.pending if order.created_at >= ts]
        filled = 0

        for order in sorted(due, key=lambda item: 0 if item.kind == "exit" else 1):
            bar = bars.get(order.symbol)
            if bar is None:
                # Emir bir sonraki barda doldurulamadıysa iptal edilir: kural 13'ün
                # "bir sonraki barın açılışı" tanımı gecikmeli bir dolumu kabul etmez.
                logger.warning(
                    "%s %s %s emri iptal [missing_bar]: %s barında sembol verisi yok",
                    model, order.symbol, order.kind, ts,
                )
                run.reject("missing_bar")
                continue

            if order.kind == "exit":
                trade = self._portfolio.close_position(
                    model,
                    symbol=order.symbol,
                    direction=order.direction,
                    reference_price=bar.open,
                    ts=ts,
                    fraction=order.fraction,
                    exit_reason="signal",
                )
                if trade is None:
                    logger.info(
                        "%s %s %s: çıkış talimatı düştü [exit_already_closed], "
                        "pozisyon zaten kapanmış",
                        model, order.symbol, order.direction,
                    )
                    run.reject("exit_already_closed")
                    continue
                run.trades.append(trade)
                filled += 1
                continue

            result = self._portfolio.open_position(
                model,
                symbol=order.symbol,
                direction=order.direction,
                stop_price=order.stop_price,
                reference_price=bar.open,
                ts=ts,
                marks=marks,
                sizing_mode=order.sizing,
                notional_fraction=order.notional_fraction,
                take_profits=order.take_profits,
                trailing_atr=order.trailing_atr,
                reason=order.reason,
            )
            if result.position is None:
                code = result.reason_code or "unknown"
                run.reject(code)
                # Boyutlandırma arızası (sıfır boyut, yetersiz nakit) bakılması gereken tek
                # gruptur; beklenen bir tekrar değildir. Seviye farkı, logu okuyanın ikisini
                # gözle ayırmasını sağlar — sayılabilir hâli tur raporundaki `rejections`.
                level = logging.WARNING if code in SIZING_FAILURES else logging.INFO
                logger.log(
                    level, "%s %s %s açılmadı [%s]: %s",
                    model, order.symbol, order.direction, code, result.rejected,
                )
                continue
            filled += 1

        return filled

    def _update_trailing_stops(self, run: _ModelRun, market: MarketData, *, ts: pd.Timestamp) -> None:
        """Trailing stop uygulaması buradadır, stratejide değil (kural 9).

        Chandelier kuralı: long'da (giriş sonrası görülen en yüksek zirve − ATR × kat),
        short'ta (en düşük dip + ATR × kat). Stop yalnızca sıkışır; portfolio gevşemeyi
        zaten reddeder.
        """
        model = run.strategy.name
        for position in self._portfolio.positions(model):
            if position.trailing_atr is None:
                continue
            frame = market.ohlcv.get(position.symbol)
            if frame is None:
                continue
            atr = average_true_range(frame.loc[:ts], self._atr_period)
            if atr is None or atr <= 0.0:
                continue
            offset = atr * position.trailing_atr
            candidate = (
                position.high_water - offset
                if position.direction == "long"
                else position.low_water + offset
            )
            if self._portfolio.set_stop_price(
                model, symbol=position.symbol, direction=position.direction, stop_price=candidate
            ):
                logger.debug(
                    "%s %s trailing stop -> %.10g (ts=%s)", model, position.symbol, candidate, ts
                )

    def _equity_row(
        self, model: str, *, ts: pd.Timestamp, marks: Mapping[str, float]
    ) -> dict[str, Any]:
        return {
            "ts": ts.isoformat(),
            "cash": self._portfolio.cash(model),
            "margin_used": self._portfolio.margin_used(model),
            "unrealized_pnl": self._portfolio.unrealized_pnl(model, marks),
            "equity": self._portfolio.equity(model, marks),
            "open_positions": len(self._portfolio.positions(model)),
        }

    # ------------------------------------------------------------------ #
    # C) İki geçişli sinyal üretimi
    # ------------------------------------------------------------------ #
    def _observe_history(self, run: _ModelRun) -> None:
        """Modele KENDİ kapanmış işlemlerini verir (yalnızca kancayı uygulayan modellere).

        Defter okuması kancayı uygulamayan modeller için HİÇ yapılmaz: 4 saatlik katmanın
        on bir modeli her turda gereksiz yere `trades.csv` okumaz ve davranışları bu
        eklemeden etkilenmez.

        Besleme, defterdeki satırlara BU turda kapanan işlemleri de ekler. 15 dakikalık
        bir modelde bir pozisyon aynı turda açılıp kapanabilir; defteri beklemek, modelin
        en taze sonucu bir tur geç görmesi demekti. Satırlar henüz yazılmamış olsa da
        KAPANMIŞTIR — açık pozisyon hiçbir yoldan bu listeye giremez.

        Kanca patlarsa (ör. beklenen etiketi taşımayan bir satır) yalnızca bu modelin turu
        boş geçer: yarım öğrenilmiş bir posterior ile sinyal üretmek, modelin ne ölçtüğünü
        bilinmez kılardı. Koşu sürer (kural 8).
        """
        strategy = run.strategy
        if type(strategy).observe_closed_trades is Strategy.observe_closed_trades:
            return
        rows = [
            *self._ledger.read_trades(strategy.name),
            *(trade.as_row() for trade in run.trades),
        ]
        try:
            strategy.observe_closed_trades(tuple(_closed_trade(row) for row in rows))
        except Exception as exc:
            logger.error("%s modeli atlandı (observe_closed_trades): %s", strategy.name, exc)
            run.skipped = _join(run.skipped, f"observe_closed_trades: {exc}")
            run.history_failed = True

    def _collect_signals(
        self, runs: Sequence[_ModelRun], market: MarketData, *, universe: Sequence[str]
    ) -> dict[str, list[Signal]]:
        """Önce normal modeller, sonra meta modeller (kural 4).

        Meta geçişine giren küme YALNIZCA normal modellerin çıktısıdır ve her meta model
        için aynıdır: metaların birbirini okuması, kimin önce çalıştığına bağlı sonuç
        üretir ve adil karşılaştırmayı bozardı. Her meta model kümenin kendi DERİN
        kopyasını alır — sözleşme salt okunur olsa da paylaşılan nesne, bir modelin
        diğerinin sinyalini bozabileceği bir kapı bırakırdı.
        """
        results: dict[str, list[Signal]] = {}
        normal = [run for run in runs if not run.strategy.is_meta]
        meta = [run for run in runs if run.strategy.is_meta]

        for run in normal:
            results[run.strategy.name] = self._model_signals(run, market, None, universe=universe)

        peer_base = {name: tuple(signals) for name, signals in results.items()}
        for run in meta:
            peers = MappingProxyType(
                {name: copy.deepcopy(signals) for name, signals in peer_base.items()}
            )
            results[run.strategy.name] = self._model_signals(run, market, peers, universe=universe)

        return results

    def _model_signals(
        self,
        run: _ModelRun,
        market: MarketData,
        peer_signals: Mapping[str, tuple[Signal, ...]] | None,
        *,
        universe: Sequence[str],
    ) -> list[Signal]:
        """Bir modelin sinyalleri; doğrulamadan geçemezse YALNIZCA o model atlanır (kural 8)."""
        strategy = run.strategy
        if run.history_failed:
            return []
        try:
            signals = strategy.generate_signals(market, peer_signals)
            for signal in signals:
                validate_signal(
                    signal,
                    entry_price=_reference_price(market, signal.symbol),
                    allowed_directions=list(strategy.allowed_directions),
                    symbol_universe=list(universe),
                    is_benchmark=strategy.is_benchmark,
                )
        except Exception as exc:
            # Sessiz filtreleme yok: hata loglanır ve modelin o turu boş geçer, koşu sürer.
            logger.error("%s modeli atlandı (sinyal üretimi/doğrulama): %s", strategy.name, exc)
            run.skipped = _join(run.skipped, f"generate_signals: {exc}")
            return []
        return list(signals)

    def _within_stop_band(
        self, run: _ModelRun, signals: Sequence[Signal], market: MarketData
    ) -> list[Signal]:
        """Stop mesafesi `max_stop_atr_multiple`'ı aşan sinyalleri eler (kural 14).

        Stop mesafesi yalnızca bir risk tercihi değil, aynı zamanda maliyet ölçeğidir: boyut
        `risk / |giriş − stop|` olduğu için dar stop kuran model aynı 1R'yi daha büyük notional
        ile taşır ve R başına daha çok komisyon+kayma öder. Bandın dışındaki işlem, sinyal
        farkını maliyet farkının gölgelemesi demektir; kıyaslanamaz.

        Stop tavana ÇEKİLMEZ — bu, modelin "stop fitilin üstünde olmalı" tezini sessizce başka
        bir modele çevirirdi. `core/validate.py` de burada devreye girmez: geniş stop bir
        programlama hatası değil, karşılaştırılamayacak bir piyasa durumudur (kural 8 ile
        karışmaz). Atlama sessiz değildir: her eleme gerekçesiyle loglanır.
        """
        kept: list[Signal] = []
        for signal in signals:
            if signal.stop_price is None:
                # Stop'suz referans sinyali (kural 15). Band bir MALİYET ÖLÇEĞİ kuralıdır:
                # stop mesafesi 1R'yi, 1R de R başına maliyeti tanımlar. Referans modelin
                # R'si yoktur (metrics'te nan) ve yarışmacılarla aynı tabloda sıralanmaz,
                # dolayısıyla elenecek bir karşılaştırılamazlık da yoktur.
                kept.append(signal)
                continue
            frame = market.ohlcv.get(signal.symbol)
            reference = _reference_price(market, signal.symbol)
            atr = average_true_range(frame.loc[:market.as_of], self._atr_period) if frame is not None else None
            if atr is None or atr <= 0.0:
                # Tavan doğrulanamıyor. Sinyali elemek, ölçülemeyen bir nedenle işlem sayısını
                # sessizce düşürürdü (kural 11'in itirazı); bandın gerçekten tutup tutmadığı
                # zaten sonradan `avg_stop_distance_pct` kolonundan denetlenebilir.
                logger.warning(
                    "%s %s: ATR hesaplanamadı, stop bandı bu sinyalde doğrulanamadı",
                    run.strategy.name, signal.symbol,
                )
                kept.append(signal)
                continue
            multiple = abs(reference - signal.stop_price) / atr
            if multiple > self._max_stop_atr_multiple:
                logger.info(
                    "%s %s: sinyal atlandı, stop mesafesi %.2f×ATR tavanı (%.2f×) aşıyor "
                    "(stop=%.10g, referans=%.10g, ATR=%.10g)",
                    run.strategy.name, signal.symbol, multiple,
                    self._max_stop_atr_multiple, signal.stop_price, reference, atr,
                )
                run.skipped_signals += 1
                continue
            kept.append(signal)
        return kept

    # ------------------------------------------------------------------ #
    # D) Çıkış talimatları
    # ------------------------------------------------------------------ #
    def _collect_exits(self, run: _ModelRun, market: MarketData) -> None:
        model = run.strategy.name
        views = self._portfolio.position_views(model)
        if not views:
            return
        try:
            instructions = run.strategy.manage_positions(market, views)
        except Exception as exc:
            logger.error("%s modeli atlandı (manage_positions): %s", model, exc)
            run.skipped = _join(run.skipped, f"manage_positions: {exc}")
            return

        directions = {position.symbol: position.direction for position in views}
        for instruction in _validated_exits(instructions, directions=directions, model=model):
            run.pending.append(
                PendingOrder(
                    kind="exit",
                    symbol=instruction.symbol,
                    direction=directions[instruction.symbol],
                    created_at=market.as_of,
                    fraction=1.0 if instruction.action == "close" else instruction.fraction,
                    reason=instruction.reason,
                )
            )
            run.exits += 1

    # ------------------------------------------------------------------ #
    # E) Kalıcılaştırma
    # ------------------------------------------------------------------ #
    def _persist(self, run: _ModelRun, market: MarketData) -> None:
        model = run.strategy.name
        state = dict(run.state)
        state.update(self._portfolio.to_state(model))
        state["model"] = model
        state["pending_orders"] = [order.as_state() for order in run.pending]
        state["last_processed_bar"] = None if run.last_bar is None else run.last_bar.isoformat()

        # Önce satırlar, sonra durum: koşu araya düşerse defterde fazladan satır kalır
        # (tekrarı fark edilebilir), tersi olsaydı bakiye değişmiş ama işlemi kayıp bir
        # defter kalırdı — denetim izi bunu affetmez.
        if run.trades:
            self._ledger.append_trades(model, [trade.as_row() for trade in run.trades])
        if run.equity_rows:
            self._ledger.append_equity(model, run.equity_rows)
        self._ledger.write_state(model, state)


# --------------------------------------------------------------------------- #
# Yardımcılar
# --------------------------------------------------------------------------- #
def _bars_at(market: MarketData, ts: pd.Timestamp) -> dict[str, Bar]:
    bars: dict[str, Bar] = {}
    for symbol, frame in market.ohlcv.items():
        if ts in frame.index:
            bars[symbol] = Bar.from_row(frame.loc[ts])
    return bars


def _reference_price(market: MarketData, symbol: str) -> float:
    """Doğrulamada kullanılan referans giriş fiyatı: `as_of` barının kapanışı.

    Gerçek dolum bir sonraki barın açılışıdır (kural 13) ve o fiyat sinyal anında
    bilinemez; geometri bu yüzden modelin gördüğü son kapanışa göre doğrulanır. Dolum
    fiyatı stop'un ötesine düşerse emir core/portfolio.py'de reddedilir.
    """
    frame = market.ohlcv.get(symbol)
    if frame is None or market.as_of not in frame.index:
        raise ValueError(f"{symbol} bu turun anlık görüntüsünde yok")
    return float(frame.loc[market.as_of, "close"])


def _validated_exits(
    instructions: Iterable[ExitInstruction],
    *,
    directions: Mapping[str, Direction],
    model: str,
) -> list[ExitInstruction]:
    valid: list[ExitInstruction] = []
    for instruction in instructions:
        if instruction.symbol not in directions:
            logger.warning(
                "%s: %s için çıkış talimatı var ama açık pozisyon yok", model, instruction.symbol
            )
            continue
        if instruction.action == "reduce" and not 0.0 < instruction.fraction <= 1.0:
            logger.error(
                "%s: %s kısmi çıkış oranı geçersiz (%s), talimat atlandı",
                model, instruction.symbol, instruction.fraction,
            )
            continue
        valid.append(instruction)
    return valid


def _closed_trade(row: Mapping[str, Any]) -> ClosedTrade:
    """Defter satırını modelin göreceği salt okunur görünüme çevirir."""
    return ClosedTrade(
        symbol=str(row.get("symbol", "")),
        direction=str(row.get("direction", "")),  # type: ignore[arg-type]
        opened_at=_to_utc(row.get("opened_at")),
        closed_at=_to_utc(row.get("closed_at")),
        r_multiple=r_multiple(row),
        signal_reason=str(row.get("signal_reason", "")),
        exit_reason=str(row.get("exit_reason", "")),
    )


def _assert_unique_names(strategies: Sequence[Strategy]) -> None:
    names = [strategy.name for strategy in strategies]
    duplicates = {name for name in names if names.count(name) > 1}
    if duplicates:
        # İki model aynı defteri paylaşırsa ikisinin de sonucu anlamsızlaşır.
        raise ValueError(f"strateji adları benzersiz olmalı, tekrar edenler: {sorted(duplicates)}")


def _join(*notes: str) -> str:
    return "; ".join(note for note in notes if note)


def _opt_float(value: Any) -> float | None:
    return None if value is None or value == "" else float(value)


def _to_utc(value: Any) -> pd.Timestamp:
    stamp = pd.Timestamp(value)
    return stamp.tz_localize("UTC") if stamp.tzinfo is None else stamp.tz_convert("UTC")

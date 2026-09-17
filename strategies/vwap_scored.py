"""**F1 — F0 + maliyet ve çıkış tarafı.** Ön-kayıt: `docs/backtest.md > 6f`.

F0 tek bir soruyu sorar (*birim düzeltmesi ne yapıyor?*); F1 ikinci soruyu sorar:
**asıl kâr girişi sıkılaştırmakta değil, MALİYETİ ve ÇIKIŞI düzeltmekte olabilir mi?**

F0'ın SİNYALİ aynen kullanılır (`strategies/vwap/session_signal.py`, aynı çarpanlar) —
birim ekseni sabit kalsın diye. F1'in eklediği beş kalem şunlardır ve hepsi bir DEMET
olarak ölçülür (ön-kayıt bunu böyle yazdı; demetin hangi kaleminin işe yaradığı bu
koşudan OKUNAMAZ):

1. **Skorla SIRALAMA ve BOYUT — yasak değil, küçültme.** Sert bir `ADX > 25: atla`
   kuralı yerine skor:

       S = (|z| / bant) × (1 − min(V / V₂₀, 1)) × w_red × w_rejim

   Evrenden en yüksek skorlu 1–2 kurulum oynanır. Boyut kullanıcının kendi kademesidir:
   ADX < 22 tam boy, 22–30 yarım boy, > 30 fade YOK. "Tam boy" burada katmanın
   `risk_per_trade`inin (%1) bir KESRİDİR (%0.6) — `size_scale` yalnızca küçültebilir
   (kural 3/11, karar 47) ve bu, istenen "hesap riskinin %0.4–0.6'sı" bandına oturur.

2. **POST-ONLY limit giriş** (karar 47): emir sinyal barının UCUNA konur (long'da o barın
   en düşüğü, short'ta en yükseği). Dolarsa maker komisyonu ve kaymasız dolum; bir
   sonraki barda dolmazsa emir İPTAL edilir ve `limit_not_filled` ile sayılır. Bu bir
   "piyasa eleği"dir ve ADX'ten temizdir: dolmayan emirler, fiyatın geri gelmediği —
   yani dönüşün olmadığı — kurulumlardır.

3. **İlerleme koşullu zaman stop'u:** `time_stop.bars` bar içinde en iyi ilerleme
   `min_progress_r`ye ulaşmadıysa pozisyon piyasadan kapatılır. Kaynakta zaman stop'u
   YOKTUR ve `strategies/time_stop.py`deki ev kuralı SÜREYE bakar; buradaki kural
   İLERLEMEYE de bakar — fade ya çabuk çalışır ya yanlıştır. İlerleme, pozisyon
   açıldığından beri LEHTE görülen uç fiyattan hesaplanır (mum içi), kapanıştan değil.

4. **Risk boyutlandırma ve 5x:** sabit teminat × 10x bırakıldı. `sizing="risk"` ile boyut
   `core/portfolio.py`nindir (kural 11) ve kaldıraç katmanın `leverage_cap`i (5) ile
   sınırlıdır. Bu, modeli tam YARIŞMACI yapar: kabul kapılarına tabidir ve maliyet ölçeği
   kolonları (`avg_stop_distance_pct`, `cost_per_r`) onun için hesaplanır.

5. **Likidite kuralı — P&L'ye DEĞİL, hacme bakar.** Son `lookback` barın MEDYAN quote
   hacmi eşiğin altındaysa sembol o bar taranmaz. Eşik bir görüşten değil bir
   büyüklükten gelir: tipik emrimiz bir barın işlem hacminin %2'sini aşmamalı. Hangi
   sembolün eleneceği ÖNCEDEN bilinmez ve defterdeki kârına bakılmaz — `docs/backtest.md
   > 7.2`nin yasakladığı şey tam olarak ikincisiydi.

**Korelasyon kotası da bir YASAK DEĞİL, yarım boydur:** aynı yönde zaten açık bir pozisyon
varken gelen ikinci kurulum yarı boyla açılır (bu evrende altcoin'ler BTC ile yüksek
korelasyonludur; aynı yönde iki pozisyon, iki katı büyüklükte tek bir pozisyondur). Açık
pozisyon sayısı sözleşmenin verdiği yerden okunur (`manage_positions`, karar 45'in aynı
gerekçesi): okunan şey bir SONUÇ değil bir SAYIDIR, öğrenmeye hiç girmez.

Rollere dikkat: boyut/komisyon/bakiye hesaplanmaz (kural 1/2/3/7), deftere yazılmaz
(kural 1), gösterge matematiği `core/indicators.py`dedir.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

import pandas as pd

from core.config import get_setting, load_config
from core.data import bar_duration
from core.indicators import adx, average_true_range, bars_until
from core.tags import format_tags
from strategies.base import (
    Direction,
    ExitInstruction,
    MarketData,
    Position,
    Signal,
    Strategy,
    TakeProfit,
)
from strategies.vwap import session_signal

logger = logging.getLogger(__name__)

CONFIG_PREFIX = "vwap.scored"

# Sayım anahtarları. `session_signal.REASONS` ile ÇAKIŞMAZ: ikisi aynı sözlükte
# raporlanır ve aynı ada sahip iki sayaç birbirini sessizce ezerdi.
THIN_BOOK = "ince_kitap"
TRENDING = "trend_guclu"
NO_LIMIT = "limit_kurulamadi"
RANK_CUT = "skor_sirasinda_kaldi"
TIME_STOP = "zaman_stopu"


@dataclass(frozen=True, kw_only=True)
class ScoredSetup:
    """Skorlanmış kurulum: aday + sıralama skoru + boyut kademesi."""

    candidate: session_signal.SessionCandidate
    score: float
    adx: float
    size_scale: float
    limit_price: float


class VwapScored(Strategy):
    name = "vwap_scored"
    allowed_directions: list[Direction] = ["long", "short"]

    def __init__(self, *, config: Mapping[str, Any] | None = None) -> None:
        settings = dict(config) if config is not None else load_config()
        self._params = session_signal.SessionParams(
            band_mult=float(get_setting(settings, f"{CONFIG_PREFIX}.band_mult")),
            tp_mult=float(get_setting(settings, f"{CONFIG_PREFIX}.tp_mult")),
            sl_mult=float(get_setting(settings, f"{CONFIG_PREFIX}.sl_mult")),
        )
        self._min_bars = int(get_setting(settings, f"{CONFIG_PREFIX}.min_bars"))
        self._max_new = int(get_setting(settings, f"{CONFIG_PREFIX}.max_new_per_bar"))
        self._adx_period = int(get_setting(settings, f"{CONFIG_PREFIX}.regime.adx_period"))
        self._adx_full = float(get_setting(settings, f"{CONFIG_PREFIX}.regime.adx_full"))
        self._adx_max = float(get_setting(settings, f"{CONFIG_PREFIX}.regime.adx_max"))
        self._volume_lookback = int(
            get_setting(settings, f"{CONFIG_PREFIX}.score.volume_lookback")
        )
        self._rejection_ratio = float(
            get_setting(settings, f"{CONFIG_PREFIX}.score.rejection_ratio")
        )
        self._rejection_weight = float(
            get_setting(settings, f"{CONFIG_PREFIX}.score.rejection_weight")
        )
        self._size_full = float(get_setting(settings, f"{CONFIG_PREFIX}.size.full"))
        self._size_half = float(get_setting(settings, f"{CONFIG_PREFIX}.size.half"))
        self._use_limit = bool(get_setting(settings, f"{CONFIG_PREFIX}.entry.use_limit"))
        self._time_stop_bars = int(get_setting(settings, f"{CONFIG_PREFIX}.time_stop.bars"))
        self._min_progress_r = float(
            get_setting(settings, f"{CONFIG_PREFIX}.time_stop.min_progress_r")
        )
        self._min_quote_volume = float(
            get_setting(settings, f"{CONFIG_PREFIX}.liquidity.min_bar_quote_volume")
        )
        self._liquidity_lookback = int(
            get_setting(settings, f"{CONFIG_PREFIX}.liquidity.lookback")
        )
        self._bar = bar_duration(str(get_setting(settings, "timeframe")))
        for name, value in (("size.full", self._size_full), ("size.half", self._size_half)):
            if not 0.0 < value <= 1.0:
                raise ValueError(
                    f"{CONFIG_PREFIX}.{name} (0, 1] aralığında olmalı — boyut ölçeği "
                    f"yalnızca KÜÇÜLTÜR (kural 3/11): {value}"
                )
        if self._size_half > self._size_full:
            raise ValueError(
                f"{CONFIG_PREFIX}.size.half, size.full'dan büyük olamaz: "
                f"{self._size_half} > {self._size_full}"
            )
        if self._adx_full > self._adx_max:
            raise ValueError(f"{CONFIG_PREFIX}.regime: adx_full, adx_max'ı aşamaz")
        if self._max_new < 1:
            raise ValueError(f"{CONFIG_PREFIX}.max_new_per_bar en az 1 olmalı")

        self._survey: dict[str, int] = {}
        self._open: tuple[tuple[str, Direction], ...] = ()
        self._open_at: pd.Timestamp | None = None

    # ------------------------------------------------------------------ #
    # Sinyal
    # ------------------------------------------------------------------ #
    def generate_signals(
        self,
        market: MarketData,
        peer_signals: Mapping[str, tuple[Signal, ...]] | None = None,
    ) -> list[Signal]:
        """Skorla sıralanmış en iyi `max_new_per_bar` kurulum."""
        counts = session_signal.empty_counts()
        setups: list[ScoredSetup] = []

        for symbol in sorted(market.ohlcv):
            frame = market.ohlcv.get(symbol)
            if frame is None or not self._liquid(frame, as_of=market.as_of):
                self._count(THIN_BOOK)
                continue
            candidate, reason = session_signal.detect(
                frame, symbol=symbol, as_of=market.as_of,
                params=self._params, min_bars=self._min_bars,
            )
            counts[reason] += 1
            if candidate is None:
                continue
            setup = self._score(candidate, frame=bars_until(frame, market.as_of))
            if setup is not None:
                setups.append(setup)

        self._survey.update(counts)
        setups.sort(key=lambda item: (-item.score, item.candidate.symbol))
        exposure = self._exposure(market.as_of)
        signals: list[Signal] = []

        for setup in setups:
            if len(signals) >= self._max_new:
                self._count(RANK_CUT)
                continue
            scale = setup.size_scale
            if self._same_direction(setup.candidate.direction, exposure):
                # Korelasyon kotası: yasak değil, YARIM BOY. Aynı yönde iki pozisyon,
                # bu evrende iki katı büyüklükte tek bir pozisyondur.
                scale *= 0.5
            signals.append(self._signal(setup, scale=scale))

        logger.info(
            "%s %s %s -> %s; skorlanan=%d, açılan=%d",
            self.name, session_signal.ARM_NAME, market.as_of.isoformat(),
            session_signal.Survey(counts=counts).describe(), len(setups), len(signals),
        )
        return signals

    def manage_positions(
        self, market: MarketData, positions: list[Position]
    ) -> list[ExitInstruction]:
        """İlerleme koşullu zaman stop'u + korelasyon kotasının okuduğu görüntü.

        İlerleme LEHTE görülen uç fiyattan hesaplanır (mum içi), kapanıştan değil: bir
        kurulum hedefe doğru 0.5R yol alıp geri dönmüşse "hiç çalışmadı" demek yanlış
        olurdu. Ölçüt, kurulumun ÇALIŞIP çalışmadığıdır — ne kadar kâr kaldığı değil.
        """
        self._open = tuple((position.symbol, position.direction) for position in positions)
        self._open_at = market.as_of

        deadline = self._bar * self._time_stop_bars
        instructions: list[ExitInstruction] = []
        for position in positions:
            if market.as_of - pd.Timestamp(position.opened_at) < deadline:
                continue
            progress = self._progress_r(position, market=market)
            if progress is not None and progress >= self._min_progress_r:
                continue
            self._count(TIME_STOP)
            instructions.append(
                ExitInstruction(
                    symbol=position.symbol,
                    action="close",
                    reason=format_tags(
                        f"ilerleme koşullu zaman stop'u: {self._time_stop_bars} barda en "
                        f"iyi ilerleme {'ölçülemedi' if progress is None else f'{progress:.2f}R'}"
                        f" < {self._min_progress_r:g}R",
                        exit_rule="time_stop",
                    ),
                )
            )
        return instructions

    def take_survey(self) -> Mapping[str, int] | None:
        survey, self._survey = dict(self._survey), {}
        return survey or None

    # ------------------------------------------------------------------ #
    # Skor, boyut ve likidite
    # ------------------------------------------------------------------ #
    def _score(
        self, candidate: session_signal.SessionCandidate, *, frame: pd.DataFrame
    ) -> ScoredSetup | None:
        """Skor ve boyut kademesi; trend çok güçlüyse kurulum DÜŞER (fade yok)."""
        strength = adx(frame, self._adx_period)
        if strength is None:
            # Trend gücü ölçülemiyorsa kademe de seçilemez: cevapsız bir rejim sorusunda
            # tam boy açmak, kademeyi hiç koymamakla aynı şey olurdu.
            self._count(TRENDING)
            return None
        if strength > self._adx_max:
            self._count(TRENDING)
            return None

        volumes = frame["volume"].to_numpy(dtype="float64")
        baseline = (
            float(volumes[-(self._volume_lookback + 1):-1].mean())
            if len(volumes) > self._volume_lookback
            else 0.0
        )
        # Hacim TÜKENMESİ: son barın hacmi ortalamanın ne kadar ALTINDA. Ortalamanın
        # üstündeyse çarpan sıfıra iner ve kurulum skorda geriye düşer — elenmez.
        exhaustion = (
            0.0 if baseline <= 0.0 else max(0.0, 1.0 - float(volumes[-1]) / baseline)
        )
        rejection = _rejection(frame.iloc[-1], direction=candidate.direction,
                               ratio=self._rejection_ratio)
        regime_weight = 1.0 if strength < self._adx_full else 0.5
        extension = abs(candidate.z) / self._params.band_mult
        score = extension * exhaustion * (1.0 if rejection else self._rejection_weight) * regime_weight

        limit = self._limit_price(candidate, frame=frame)
        if limit is None:
            self._count(NO_LIMIT)
            return None
        return ScoredSetup(
            candidate=candidate,
            score=score,
            adx=strength,
            size_scale=self._size_full if strength < self._adx_full else self._size_half,
            limit_price=limit,
        )

    def _limit_price(
        self, candidate: session_signal.SessionCandidate, *, frame: pd.DataFrame
    ) -> float | None:
        """POST-ONLY emrin fiyatı: sinyal barının UCU (long'da en düşük, short'ta en yüksek).

        Piyasa emrine düşmek bir seçenek DEĞİLDİR: F1'in ölçtüğü şeylerden biri tam olarak
        maker dolumun katkısıdır ve sessizce taker'a düşen bir emir o ölçüyü kirletirdi.
        Uç, kapanışın lehte tarafında değilse (dokunulamaz bir emir) kurulum düşer ve
        sayılır.
        """
        if not self._use_limit:
            return None
        bar = frame.iloc[-1]
        price = float(bar["low"]) if candidate.direction == "long" else float(bar["high"])
        entry = candidate.entry_price
        if not math.isfinite(price) or price <= 0.0:
            return None
        if candidate.direction == "long" and price >= entry:
            return None
        if candidate.direction == "short" and price <= entry:
            return None
        return price

    def _liquid(self, frame: pd.DataFrame, *, as_of: pd.Timestamp) -> bool:
        """Son `lookback` barın MEDYAN quote hacmi eşiği geçiyor mu?

        Medyan, ortalama değil: tek bir haber mumu ince bir kitabı likit gösterirdi.
        Eşik P&L'den değil büyüklükten gelir (bkz. config yorumu) ve hangi sembolün
        eleneceği önceden bilinmez.
        """
        window = bars_until(frame, as_of).tail(self._liquidity_lookback)
        if len(window) < self._liquidity_lookback:
            return False
        quote = (window["close"] * window["volume"]).to_numpy(dtype="float64")
        return float(pd.Series(quote).median()) >= self._min_quote_volume

    def _progress_r(self, position: Position, *, market: MarketData) -> float | None:
        """Pozisyon açıldığından beri LEHTE görülen uç fiyatın R cinsinden karşılığı."""
        if position.stop_price is None:
            return None
        risk = abs(position.entry_price - position.stop_price)
        if risk <= 0.0:
            return None
        frame = market.ohlcv.get(position.symbol)
        if frame is None:
            return None
        window = bars_until(frame, market.as_of).loc[pd.Timestamp(position.opened_at):]
        if window.empty:
            return None
        if position.direction == "long":
            best = float(window["high"].max()) - position.entry_price
        else:
            best = position.entry_price - float(window["low"].min())
        return best / risk

    def _exposure(self, as_of: pd.Timestamp) -> tuple[tuple[str, Direction], ...]:
        """Bir önceki barın açık pozisyonları; damgası taze değilse pozisyon YOKTUR.

        Motor `manage_positions`ı yalnızca pozisyonu olan modelde çağırır, yani eski bir
        görüntü sessizce taşınabilirdi (karar 45'in aynı tuzağı). Damga kontrolü bunu
        keser.
        """
        if self._open_at is None or as_of - self._open_at != self._bar:
            return ()
        return self._open

    def _same_direction(
        self, direction: Direction, exposure: Sequence[tuple[str, Direction]]
    ) -> bool:
        return any(held == direction for _, held in exposure)

    def _count(self, key: str) -> None:
        self._survey[key] = self._survey.get(key, 0) + 1

    def _signal(self, setup: ScoredSetup, *, scale: float) -> Signal:
        candidate = setup.candidate
        return Signal(
            symbol=candidate.symbol,
            direction=candidate.direction,
            # Risk boyutlandırma (kural 11) + katmanın leverage_cap'i (5x). Sabit teminat
            # YOK; `size_scale` katmanın %1'ini modelin kademesine göre KÜÇÜLTÜR.
            stop_price=candidate.stop_price,
            size_scale=scale,
            entry_type="limit",
            limit_price=setup.limit_price,
            take_profits=(TakeProfit(price=candidate.target_price, fraction=1.0),),
            reason=format_tags(
                f"{candidate.detail()}; post-only limit {setup.limit_price:.6g}, stop "
                f"{candidate.stop_price:.6g}, hedef {candidate.target_price:.6g}; "
                f"ilerleme koşullu zaman stop'u {self._time_stop_bars} bar / "
                f"{self._min_progress_r:g}R",
                arm=session_signal.ARM_NAME,
                score=setup.score,
                adx=setup.adx,
                scale=scale,
            ),
        )


def _rejection(bar: pd.Series, *, direction: Direction, ratio: float) -> bool:
    """Red mumu: sapma yönündeki fitil, barın tüm aralığının en az `ratio` kadarı."""
    high = float(bar["high"])
    low = float(bar["low"])
    span = high - low
    if not math.isfinite(span) or span <= 0.0:
        return False
    body_low = min(float(bar["open"]), float(bar["close"]))
    body_high = max(float(bar["open"]), float(bar["close"]))
    wick = (body_low - low) if direction == "long" else (high - body_high)
    return wick >= ratio * span

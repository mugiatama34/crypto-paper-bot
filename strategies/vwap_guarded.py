"""MODEL 18 — kopyanın (model 13) CANLIYA HAZIRLANMIŞ uyarlaması.

**Bu model bir kopya DEĞİLDİR ve kopyanın yerine de geçmez.** Model 13 (`vwap_clone`,
`is_replica=True`) dış bir sistemin kurallarını birebir yeniden üretir ve kural 15b gereği
o kuralların hiçbiri değiştirilemez: değiştirilen bir kopya, kopya olmaktan çıkar ve
"dış sistem bizim varsayımlarımızla ne yapardı" sorusunun cevabı kaybolur. Bu yüzden
uyarlama, kopyanın üstüne yazılarak değil YENİ BİR MODEL olarak eklenmiştir — CLAUDE.md'nin
"tek değişkenli bir eksen isteniyorsa yolu yeni bir model açmaktır" kuralının kendisi.

**Ne ayrışıyor (model 13 -> model 18).** Sekiz kalem; hepsi canlı işlem hazırlığının
gerekçeleridir, hiçbiri "daha iyi görünen sayı" arayışı değildir:

| # | Kopya (13) | Bu model (18) | Neden |
|---|---|---|---|
| 1 | 300 barlık kayan kümülatif VWAP | **seans (UTC gün) çapalı VWAP** | çapası kayan bir ortalama hiçbir seansa ait değildir |
| 2 | bant öğrenilir (1.5/2.0/2.5σ) | **taban 2.5σ, LONG için 3.0σ** | 1.5σ bu geometride gürültü; defterde beklenti farkı long tarafındaydı |
| 3 | rejim kapısı yok | **ADX + EMA eğimi + BTC 1s yönü** | ortalamaya dönüş tezi trendde geçersizdir |
| 4 | tükenme şartı yok | **klimaks hacmi ya da red mumu** | dönüş bir niyet beyanıdır, tükenme kanıtıdır |
| 5 | sabit teminat × 10x | **risk boyutlandırma (kural 11), katmanın `leverage_cap`i** | sabit teminat, oynaklığı yüksek sembolde riski gizlice büyütür |
| 6 | zaman stop'u yok | **`vwap.guarded.time_stop_bars` (10 bar)** | dönmeyen kurulum marjı süresiz tutar |
| 7 | 12 sembollük kaynak evreni | **kendi evreni (PENGU/ETHFI yok)** | bkz. aşağıda "sembol elemesi" |
| 8 | risk kesici yok | **günlük zarar limiti + drawdown kill-switch + korelasyon kotası** | canlı bir hesabın ölçümde karşılığı olmayan tek eksiği buydu |

**Ev kapıları GEÇERLİDİR** (model 14 ile aynı anahtarlardan): %1 stop tabanı ve 1.5R
hedef/stop kapısı `scalp.*`tan okunur. İki ayrı anahtar açmak, iki ayrı çıta demekti.

**Sembol elemesi bir SAPMADIR ve öyle işaretlenir.** `docs/backtest.md > 7.2` sonuca bakıp
sembol elemeyi ("şu sembolü çıkarsak") açıkça yasaklar ve karar 40 bunu bir kez reddetti.
Buradaki eleme kullanıcının açık talimatıdır ve modelin bir ÖNSELİ olarak, koşudan ÖNCE
ön-kayda yazılmıştır (`docs/backtest.md > 6d`): dolayısıyla model 18'in sonucu sembol
seçimi açısından IN-SAMPLE'dır ve bu, sonucun yanında durur. Eleme sessiz değildir:
evren config'te tek tek yazılıdır ve her turda loglanır.

**Kill-switch nasıl kural 16'ya sığıyor.** Model bakiyesini göremez (kural 7) ve açık
pozisyonunun kâr/zararını okuyamaz (kural 16); gördüğü tek şey KENDİ KAPANMIŞ
işlemlerinin gerçekleşmiş R'sidir. Bu yüzden iki kesici de R cinsindendir:

- **Günlük zarar limiti:** o UTC gününde kapanmış işlemlerin R toplamı
  `−vwap.guarded.risk.daily_loss_r` (2R) veya altındaysa gün biter — tarama durur.
- **Drawdown kill-switch:** kümülatif R'nin zirvesinden düşüş `max_drawdown_pct /
  risk_per_trade` (yani %8 / %1 = 8R) veya fazlaysa tarama durur. Yüzde eşiğinin R'ye
  çevrilmesi bir yaklaşıklık DEĞİL, boyutlandırma kuralının doğrudan sonucudur: 1R tanımı
  gereği sermayenin `risk_per_trade` kadarıdır (kural 11).

Kesici tetiklendiğinde model **yalnızca yeni kurulum aramayı bırakır**; açık pozisyonların
stop/hedef/zaman yönetimi motorda sürer (kural 9/10/13) — bir kill-switch pozisyonları
piyasaya atmaz, yeni risk almayı durdurur. Drawdown kesicisi pratikte KALICIDIR: durmuş
bir model kendini toparlayamaz, çünkü yeni işlem açmaz. Bu bir kusur değil tanımdır;
yeniden açmak bir insan kararıdır ve bu depoda bir commit demektir.

**Korelasyon kotası açık pozisyonları SAYAR ve bunu sözleşmenin verdiği yerden okur.**
`Strategy.manage_positions` modele KENDİ açık pozisyonlarının salt okunur görünümünü
zaten verir (kural 10'un çıkış kararı için gereklidir); bu model o görünümün yalnızca
(sembol, yön) çiftini saklar ve bir sonraki barda kotayı ona göre uygular. Kural 16'nın
yasağı DELİNMEZ: yasak "açık pozisyonun henüz gerçekleşmemiş sonucunu ÖĞRENMEYE sokmak"
üzerinedir (gerekçesi kâğıt üstündeki kârın ölçüme girmesi ve kapanışta ikinci kez
sayılmasıdır); burada okunan şey bir sonuç değil bir SAYIDIR, öğrenmeye hiç girmez ve
başka bir modelin hiçbir verisine dokunmaz (kural 4).

Görüntü BİR BAR BAYATTIR (`generate_signals`, motorda `manage_positions`tan önce koşar)
ve bu yüzden aynı barda üretilmiş kendi sinyali de kotaya sayılır: sayılmasaydı kota tam
olarak bir pozisyon kadar aşılabilirdi. Pozisyonu hiç olmayan bir barda motor
`manage_positions`ı çağırmaz, yani görüntü tazelenmez — bu durum görüntünün DAMGASINDAN
anlaşılır (bir bar geride değilse pozisyon yoktur) ve kota sıfırdan başlar.

Rollere dikkat: bu modül boyut/komisyon/bakiye hesaplamaz (kural 1/2/3/7), deftere yazmaz
(kural 1) ve kendi gösterge matematiğini yazmaz (`core/indicators.py`).
"""

from __future__ import annotations

import logging
from collections import defaultdict
from typing import Any, Mapping, Sequence

import pandas as pd

from core.config import get_setting, load_config
from core.data import bar_duration
from core.tags import format_tags
from strategies.base import (
    ClosedTrade,
    Direction,
    ExitInstruction,
    MarketData,
    Position,
    Signal,
    Strategy,
    TakeProfit,
)
from strategies.exit_management import ExitManagement
from strategies.time_stop import TimeStop
from strategies.vwap import guarded_signal

logger = logging.getLogger(__name__)

CONFIG_PREFIX = "vwap.guarded"

# Kesicilerin ve kotanın sayım anahtarları. `guarded_signal.REASONS` ile ÇAKIŞMAZ:
# ikisi aynı sözlükte raporlanır (tur raporunda tek bir `survey` alanı vardır) ve aynı
# ada sahip iki sayaç, birinin diğerini sessizce ezmesi demekti.
HALT_DAILY = "kesici_gunluk_zarar"
HALT_DRAWDOWN = "kesici_drawdown"
CORRELATION_QUOTA = "korelasyon_kotasi"
GATE_STOP_FLOOR = "stop_tabani"
GATE_REWARD_RISK = "hedef_stop_kapisi"


class VwapGuarded(Strategy):
    name = "vwap_guarded"
    allowed_directions: list[Direction] = ["long", "short"]

    def __init__(self, *, config: Mapping[str, Any] | None = None) -> None:
        settings = dict(config) if config is not None else load_config()
        self._universe = [str(symbol) for symbol in get_setting(settings, f"{CONFIG_PREFIX}.universe")]
        if not self._universe:
            raise ValueError(f"{CONFIG_PREFIX}.universe boş olamaz")
        self._atr_period = int(get_setting(settings, "trailing.atr_period"))
        self._atr_multiple = float(get_setting(settings, f"{CONFIG_PREFIX}.atr_multiple"))
        self._target_reward_risk = float(
            get_setting(settings, f"{CONFIG_PREFIX}.target_reward_risk")
        )
        self._params = guarded_signal.GuardParams(
            band_long=float(get_setting(settings, f"{CONFIG_PREFIX}.band.long")),
            band_short=float(get_setting(settings, f"{CONFIG_PREFIX}.band.short")),
            min_vwap_bars=int(get_setting(settings, f"{CONFIG_PREFIX}.min_vwap_bars")),
            adx_period=int(get_setting(settings, f"{CONFIG_PREFIX}.regime.adx_period")),
            adx_max=float(get_setting(settings, f"{CONFIG_PREFIX}.regime.adx_max")),
            ema_period=int(get_setting(settings, f"{CONFIG_PREFIX}.regime.ema_period")),
            slope_bars=int(get_setting(settings, f"{CONFIG_PREFIX}.regime.slope_bars")),
            max_slope_atr=float(get_setting(settings, f"{CONFIG_PREFIX}.regime.max_slope_atr")),
            exhaustion_lookback=int(
                get_setting(settings, f"{CONFIG_PREFIX}.exhaustion.lookback")
            ),
            climax_mult=float(get_setting(settings, f"{CONFIG_PREFIX}.exhaustion.climax_mult")),
            rejection_wick_ratio=float(
                get_setting(settings, f"{CONFIG_PREFIX}.exhaustion.rejection_wick_ratio")
            ),
        )
        self._btc_timeframe = str(get_setting(settings, f"{CONFIG_PREFIX}.btc_bias.timeframe"))
        self._btc_ema_period = int(get_setting(settings, f"{CONFIG_PREFIX}.btc_bias.ema_period"))
        self._btc_slope_bars = int(get_setting(settings, f"{CONFIG_PREFIX}.btc_bias.slope_bars"))
        self._btc_min_slope_atr = float(
            get_setting(settings, f"{CONFIG_PREFIX}.btc_bias.min_slope_atr")
        )
        # Ev kapıları model 14 ile AYNI anahtarlardan: iki ayrı anahtar iki ayrı çıta
        # demekti ve `vwap_managed ↔ vwap_guarded` kıyasına ölçülmeyen bir değişken
        # girerdi.
        self._min_stop_pct = float(get_setting(settings, "scalp.min_stop_pct"))
        self._min_reward_risk = float(get_setting(settings, "scalp.min_reward_risk"))
        self._time_stop = TimeStop.from_config(
            settings, key=f"{CONFIG_PREFIX}.time_stop_bars"
        )
        self._exit = ExitManagement.from_config(settings)
        self._bar = bar_duration(str(get_setting(settings, "timeframe")))

        # --- Risk kesicileri (R cinsinden; gerekçe modül docstring'inde) ---
        self._daily_loss_r = float(get_setting(settings, f"{CONFIG_PREFIX}.risk.daily_loss_r"))
        max_drawdown_pct = float(
            get_setting(settings, f"{CONFIG_PREFIX}.risk.max_drawdown_pct")
        )
        risk_per_trade = float(get_setting(settings, "risk_per_trade"))
        if risk_per_trade <= 0.0:
            raise ValueError(f"risk_per_trade pozitif olmalı: {risk_per_trade}")
        if not 0.0 < max_drawdown_pct <= 1.0:
            raise ValueError(
                f"{CONFIG_PREFIX}.risk.max_drawdown_pct (0, 1] aralığında olmalı: "
                f"{max_drawdown_pct}"
            )
        if self._daily_loss_r <= 0.0:
            raise ValueError(
                f"{CONFIG_PREFIX}.risk.daily_loss_r pozitif olmalı (eşik NEGATİF R'de "
                f"uygulanır): {self._daily_loss_r}"
            )
        self._max_drawdown_pct = max_drawdown_pct
        self._max_drawdown_r = max_drawdown_pct / risk_per_trade
        self._max_correlated = int(
            get_setting(settings, f"{CONFIG_PREFIX}.risk.max_correlated_positions")
        )
        if self._max_correlated < 1:
            raise ValueError(
                f"{CONFIG_PREFIX}.risk.max_correlated_positions en az 1 olmalı "
                f"(0, modeli hiç işlem açamaz hâle getirirdi): {self._max_correlated}"
            )
        if self._params.band_short <= 0.0 or self._params.band_long <= 0.0:
            raise ValueError(f"{CONFIG_PREFIX}.band değerleri pozitif olmalı")

        # --- Defterden kurulan durum (kural 16: yalnızca KENDİ kapanmış işlemleri) ---
        self._daily_r: dict[pd.Timestamp, float] = {}
        self._drawdown_r = 0.0
        # --- Bar bazlı denetim izi ve kota defteri ---
        self._survey: dict[str, int] = {}
        self._scan_counts: Mapping[str, int] | None = None
        self._open: tuple[tuple[str, Direction], ...] = ()
        self._open_at: pd.Timestamp | None = None
        self._emitted: tuple[tuple[str, Direction], ...] = ()
        self._emitted_at: pd.Timestamp | None = None

    # ------------------------------------------------------------------ #
    # Öğrenme yüzeyi (kural 16) — burada ÖĞRENME yok, yalnızca RİSK sayacı
    # ------------------------------------------------------------------ #
    def observe_closed_trades(self, trades: Sequence[ClosedTrade]) -> None:
        """Kesicilerin sayaçlarını defterden SIFIRDAN kurar.

        Artımlı güncelleme YOK (`scalp_bandit` ve `vwap_clone` ile aynı gerekçe): durum
        defterin saf bir fonksiyonu olmazsa, defterle modelin hafızası bir gün ayrışır ve
        hangisinin doğru olduğu bilinemez. Ayrı bir durum dosyası da yoktur — ikinci bir
        doğruluk kaynağı olurdu.

        **Bu bir öğrenme değildir:** hiçbir parametre, hiçbir kol ağırlığı, hiçbir eşik
        geçmişe göre değişmez. Sinyal kuralları geçmişten BAĞIMSIZDIR; buradan gelen tek
        şey "durmalı mıyım" sorusunun cevabıdır.

        `r_multiple` None olan satır (risk bilinmiyor) sayaca girmez ve loglanır: sıfır
        saymak, ölçülemeyen bir işlemi "tam başabaş" gibi göstermek olurdu.
        """
        daily: dict[pd.Timestamp, float] = defaultdict(float)
        cumulative = 0.0
        peak = 0.0
        unmeasured = 0

        for trade in sorted(trades, key=lambda item: item.closed_at):
            if trade.r_multiple is None:
                unmeasured += 1
                continue
            value = float(trade.r_multiple)
            cumulative += value
            peak = max(peak, cumulative)
            daily[pd.Timestamp(trade.closed_at).normalize()] += value

        self._daily_r = dict(daily)
        self._drawdown_r = peak - cumulative
        if unmeasured:
            logger.warning(
                "%s: %d kapanmış işlemin R'si yok, risk sayacına girmedi",
                self.name, unmeasured,
            )
        logger.info(
            "%s risk durumu: kümülatif R=%.2f, zirveden düşüş=%.2fR (kesici %.2fR), "
            "gün sayısı=%d",
            self.name, cumulative, self._drawdown_r, self._max_drawdown_r, len(self._daily_r),
        )

    # ------------------------------------------------------------------ #
    # Sinyal
    # ------------------------------------------------------------------ #
    def generate_signals(
        self,
        market: MarketData,
        peer_signals: Mapping[str, tuple[Signal, ...]] | None = None,
    ) -> list[Signal]:
        """Kesiciler kapalıysa, kapılardan geçen EN GÜÇLÜ tek aday.

        Barda tek sinyal: kıyas hedefleri (`vwap_managed`, `scalp_fixed`) da bar başına
        tek pozisyon açar ve birden çok sinyal, korelasyon kotasını aynı barda anlamsız
        kılardı — kota ancak pozisyonlar zaman içinde birikirken bir şey ölçer.
        """
        self._scan_counts = None
        halt = self._halted(market.as_of)
        if halt is not None:
            self._count(halt)
            return []

        bias = guarded_signal.btc_bias(
            market,
            timeframe=self._btc_timeframe,
            ema_period=self._btc_ema_period,
            slope_bars=self._btc_slope_bars,
            min_slope_atr=self._btc_min_slope_atr,
            atr_period=self._atr_period,
        )
        candidates, survey = guarded_signal.scan(
            market,
            atr_period=self._atr_period,
            params=self._params,
            bias=bias,
            symbols=self._universe,
        )
        self._scan_counts = survey.report()
        logger.info(
            "%s %s %s bant=%.2f/%.2fσ -> %s",
            self.name, guarded_signal.ARM_NAME, market.as_of.isoformat(),
            self._params.band_long, self._params.band_short, survey.describe(),
        )

        exposure = self._exposure(market.as_of)
        for candidate in candidates:
            if self._correlated(candidate.direction, exposure):
                logger.info(
                    "%s %s: kurulum atlandı, aynı yönde (%s) zaten %d pozisyon var "
                    "(kota %d) — bu evrende altcoin'ler BTC ile yüksek korelasyonludur",
                    self.name, candidate.symbol, candidate.direction,
                    self._same_direction(candidate.direction, exposure), self._max_correlated,
                )
                self._count(CORRELATION_QUOTA)
                continue
            stop = guarded_signal.stop_price(candidate, atr_multiple=self._atr_multiple)
            projected = guarded_signal.projected_target(
                candidate, stop=stop, reward_risk=self._target_reward_risk
            )
            target = guarded_signal.nearest_target(candidate, projected=projected)
            if not self._passes_gates(candidate, stop=stop, target=target):
                continue
            signal = self._signal(candidate, stop=stop, target=target, bias=bias)
            self._emitted = ((candidate.symbol, candidate.direction),)
            self._emitted_at = market.as_of
            return [signal]
        return []

    def manage_positions(
        self, market: MarketData, positions: list[Position]
    ) -> list[ExitInstruction]:
        """Zaman stop'u + korelasyon kotasının okuduğu (sembol, yön) görüntüsü.

        Görüntü BURADA saklanır çünkü sözleşme onu yalnızca burada verir (kural 10).
        Saklanan şey bir sonuç değil bir sayıdır: kâr/zarar, süre ve stop mesafesi
        alınmaz — kural 16'nın yasakladığı "gerçekleşmemiş sonucu ölçüme sokmak"
        buradan geçemez.
        """
        self._open = tuple((position.symbol, position.direction) for position in positions)
        self._open_at = market.as_of
        return self._time_stop.instructions(market, positions)

    def take_survey(self) -> Mapping[str, int] | None:
        """Taramanın ve kesicilerin sayımı (kural 15). Sinyalleri hiçbir biçimde etkilemez.

        İki kaynak tek sözlükte birleşir: `guarded_signal.Survey` (sembol başına eleme
        sebebi) ve modelin kendi sayaçları (kesiciler, kota, ev kapıları). Ayrı ayrı
        raporlamak, tur raporunun sözleşmesini (`survey` tek bir eşleme) bozardı;
        anahtarların çakışmaması ise sabitlerin tek yerde durmasıyla güvence altındadır.
        """
        extra, self._survey = dict(self._survey), {}
        counts = dict(self._scan_counts or {})
        counts.update(extra)
        return counts or None

    # ------------------------------------------------------------------ #
    # Kesiciler, kota ve ev kapıları
    # ------------------------------------------------------------------ #
    def _halted(self, as_of: pd.Timestamp) -> str | None:
        """Tarama durmalı mı? Duruyorsa SEBEP KODU döner (sessiz durma yoktur)."""
        if self._drawdown_r >= self._max_drawdown_r:
            logger.warning(
                "%s: KILL-SWITCH — zirveden düşüş %.2fR ≥ %.2fR (≈ sermayenin "
                "%%%.1f'i); yeni kurulum aranmıyor, açık pozisyonlar yönetilmeye devam "
                "ediyor. Yeniden açmak bir insan kararıdır.",
                self.name, self._drawdown_r, self._max_drawdown_r,
                self._max_drawdown_pct * 100.0,
            )
            return HALT_DRAWDOWN
        today = pd.Timestamp(as_of).normalize()
        realized = self._daily_r.get(today, 0.0)
        if realized <= -self._daily_loss_r:
            logger.warning(
                "%s: günlük zarar limiti — %s gününde gerçekleşen R %.2f ≤ -%.2f; "
                "gün sonuna kadar yeni kurulum aranmıyor",
                self.name, today.date(), realized, self._daily_loss_r,
            )
            return HALT_DAILY
        return None

    def _exposure(self, as_of: pd.Timestamp) -> tuple[tuple[str, Direction], ...]:
        """Kotanın saydığı maruziyet: bir önceki barın pozisyonları + o barın kendi sinyali.

        Damga kontrolü ("tam bir bar geride mi") iki şeyi birden çözer: pozisyonu olmayan
        barda motor `manage_positions`ı hiç çağırmaz, yani eski bir görüntü sessizce
        taşınabilirdi; ve telafi edilen barlarda görüntü yalnızca bir önceki bara aittir.
        Eski damga taşınmaz — kota ancak TAZE bir sayıyla anlamlıdır.
        """
        positions: tuple[tuple[str, Direction], ...] = ()
        if self._open_at is not None and as_of - self._open_at == self._bar:
            positions = self._open
        pending: tuple[tuple[str, Direction], ...] = ()
        if self._emitted_at is not None and as_of - self._emitted_at == self._bar:
            held = {symbol for symbol, _ in positions}
            pending = tuple(item for item in self._emitted if item[0] not in held)
        return positions + pending

    def _same_direction(
        self, direction: Direction, exposure: Sequence[tuple[str, Direction]]
    ) -> int:
        return sum(1 for _, held in exposure if held == direction)

    def _correlated(
        self, direction: Direction, exposure: Sequence[tuple[str, Direction]]
    ) -> bool:
        return self._same_direction(direction, exposure) >= self._max_correlated

    def _passes_gates(
        self, candidate: guarded_signal.GuardedCandidate, *, stop: float, target: float
    ) -> bool:
        """%1 stop tabanı ve 1.5R kapısı. Her eleme GEREKÇESİYLE loglanır (kural 14)."""
        stop_pct = guarded_signal.stop_distance_pct(candidate, stop=stop)
        if stop_pct < self._min_stop_pct:
            logger.info(
                "%s %s: kurulum atlandı, stop mesafesi %%%.3f < taban %%%.3f "
                "(tur maliyeti bu mesafede 0.25R'yi aşar)",
                self.name, candidate.symbol, stop_pct * 100, self._min_stop_pct * 100,
            )
            self._count(GATE_STOP_FLOOR)
            return False
        reward_risk = guarded_signal.reward_risk_of(candidate, stop=stop, target=target)
        if reward_risk < self._min_reward_risk:
            logger.info(
                "%s %s: kurulum atlandı, hedef/stop %.2f < çıta %.2f (VWAP %.6g çok yakın)",
                self.name, candidate.symbol, reward_risk, self._min_reward_risk, candidate.vwap,
            )
            self._count(GATE_REWARD_RISK)
            return False
        return True

    def _count(self, key: str) -> None:
        self._survey[key] = self._survey.get(key, 0) + 1

    def _signal(
        self,
        candidate: guarded_signal.GuardedCandidate,
        *,
        stop: float,
        target: float,
        bias: guarded_signal.Bias,
    ) -> Signal:
        stop_pct = guarded_signal.stop_distance_pct(candidate, stop=stop)
        reward_risk = guarded_signal.reward_risk_of(candidate, stop=stop, target=target)
        return Signal(
            symbol=candidate.symbol,
            direction=candidate.direction,
            # Ev kuralı: boyut core/portfolio.py'de, risk_per_trade × sermaye / stop
            # mesafesi (kural 11) ve katmanın leverage_cap'i geçerli. Sabit teminat YOK.
            stop_price=stop,
            take_profits=(TakeProfit(price=target, fraction=1.0),),
            **self._exit.signal_fields(),
            reason=format_tags(
                f"{candidate.detail()}; stop {self._atr_multiple:g}×ATR = "
                f"%{stop_pct * 100:.2f} ({stop:.6g}), hedef {target:.6g} "
                f"({reward_risk:.2f}R), {self._time_stop.describe()}; "
                f"{self._exit.describe()}",
                arm=guarded_signal.ARM_NAME,
                rr=reward_risk,
                adx=candidate.adx,
                exhaustion=candidate.exhaustion,
                btc=bias,
            ),
        )

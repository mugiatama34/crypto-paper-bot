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
| 9 | öğrenme: 9 kombinasyon, ε=0.25, sembol eşiği 3, ödül = ham ort. R | **aynı 3×3 iskelet, ε=0.10, sembol eşiği 30, ödül drawdown ile cezalandırılmış** | bkz. "Öğrenme" |

**Öğrenme (epsilon-greedy, kaynağın iskeleti korunur).** Kopya bant ve hedef çarpanını
öğrenir; bu model de öğrenir ama üç yerde ayrışır:

1. **Ödül işlem MALİYETİNİ zaten içerir.** Öğrenmenin girdisi `ClosedTrade.r_multiple`dır
   ve o `pnl / risk_amount`tır — `pnl` komisyon, kayma ve funding DÜŞÜLMÜŞ nettir
   (`core/portfolio.py`). Yani "ödüle işlem maliyetini ekle" bu depoda bir değişiklik
   değil, zaten geçerli olan tanımın kendisidir; burada yazılı olmasının sebebi, bunun
   sessiz bir varsayım olarak kalmamasıdır.
2. **Ödül DRAWDOWN ile cezalandırılır:** `skor = (Σr − w × en_derin_düşüş_R) / n`. Ceza
   bir kez ve TAM olarak uygulanır (`w = 1.0`), örnekleme bölünerek: iki kombinasyon aynı
   ortalama R'yi verdiğinde, oraya daha derin bir çukurdan geçerek ulaşan kaybeder.
   Ortalama R tek başına sıralama ölçütü olsaydı, "önce 10R kaybedip sonra 12R kazanan"
   bir kol "hiç kaybetmeden 2R kazanan" ile aynı görünürdü — canlı bir hesapta ikisi aynı
   şey DEĞİLDİR.
3. **Sembol eşiği 30'dur** (kopyada 3). Bir kombinasyonun o semboldeki ortalaması ancak
   `acceptance.min_trades` kadar örnekle GÜVENİLİRDİR; altında kalan hücre tüm semboller
   genelindeki ortalamaya düşer. Eşiğin kabul çıtasıyla aynı sayı olması tesadüf değil:
   "bu ortalama bir ölçüm mü, gürültü mü" sorusunun cevabı öğrenme içinde de aynı olmalı.
4. **Keşif payı 0.25 değil 0.10'dur.** Sıfıra İNMEZ (kopyanın ve `scalp_bandit`in aynı
   gerekçesi: susturulan kombinasyon bir daha ÖLÇÜLEMEZ ve "kötüydü" iddiası sınanamaz
   hâle gelir), ama canlı bir hesapta keşfin bedelini gerçek işlemler öder — 0.10 o bedeli
   dörtte bire indirir.

**Isınma GLOBALDİR, sembol başına değil** (kopyada sembol başınadır). Gerekçe 3. maddenin
doğrudan sonucudur: sembol istatistiğine 30 örnekten önce güvenilmiyorsa, "bu sembolde
denenmemiş kombinasyon" da bir ısınma ölçütü olamaz — 9 kombinasyon × 11 sembol, bu
katmanın tüm örneklemini saf keşfe harcardı.

**Stop çarpanı grid'in EKSENİ DEĞİLDİR** (`atr_multiple` sabit 2.5). Stop mesafesi aynı
zamanda maliyet ölçeğidir (kural 14) ve onu öğrenmeye açmak, modelin kendi ⚠B bandını
koşu sırasında kaydırması demekti: iki koşunun `avg_stop_distance_pct` değeri ayrışır ve
kıyas geçersizleşir.

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
import random
from collections import defaultdict
from dataclasses import dataclass, replace
from typing import Any, Mapping, Sequence

import pandas as pd

from core.config import get_setting, load_config
from core.data import bar_duration
from core.tags import find_tag, format_tags
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

NAN = float("nan")


@dataclass(frozen=True, kw_only=True)
class Combo:
    """Bandit'in bir kolu: bir bant çarpanı + bir hedef oranı.

    `key` deftere yazılan ve geri okunan etikettir ve İNDEKSTEN türetilir, sayıdan değil
    (kopyadaki aynı gerekçe): "2.5" gibi bir kayan noktayı etiket anahtarı yapmak,
    biçimlendirme ile geri ayrıştırmanın bir gün ayrışması ve öğrenmenin sessizce
    boşalması demekti.
    """

    key: str
    band_short: float
    band_long: float
    target_reward_risk: float


@dataclass(frozen=True, kw_only=True)
class ComboStats:
    """Bir kombinasyonun gözlem özeti. `nan` = ölçülmedi (0.0 DEĞİL).

    `score` sıralama ölçütüdür ve ortalama R'den farklıdır: `(Σr − w × maxDD) / n`.
    İkisi ayrı alanlarda durur çünkü ikisi ayrı sorulara cevap verir — ortalama R
    "ne kazandırdı", skor "hangi yoldan kazandırdı".
    """

    trades: int
    mean_r: float
    max_drawdown_r: float
    score: float

    @property
    def measured(self) -> bool:
        return self.trades > 0


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
        # Bant ve hedef oranı ÖĞRENİLİR (aşağıdaki grid); `_template` ise öğrenilmeyen
        # kapıların tek kopyasıdır — her kombinasyon onun üstüne yalnızca kendi bandını
        # yazar. İki ayrı GuardParams kurmak, bir gün bir kapının yalnızca bir kolda
        # değişmesi ve farkın "kapının katkısı" olmaktan çıkması demekti.
        self._template = guarded_signal.GuardParams(
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
        self._combos = _build_combos(
            [float(v) for v in get_setting(settings, f"{CONFIG_PREFIX}.bandit.band_mults")],
            [
                float(v)
                for v in get_setting(settings, f"{CONFIG_PREFIX}.bandit.target_reward_risks")
            ],
            long_extra=float(get_setting(settings, f"{CONFIG_PREFIX}.bandit.long_extra")),
            floor=self._template.band_short,
        )
        self._epsilon = float(get_setting(settings, f"{CONFIG_PREFIX}.bandit.epsilon"))
        if not 0.0 < self._epsilon <= 1.0:
            raise ValueError(
                f"{CONFIG_PREFIX}.bandit.epsilon (0, 1] aralığında olmalı — sıfır, "
                f"susturulan kombinasyonun bir daha ölçülememesi demekti: {self._epsilon}"
            )
        self._min_symbol_samples = int(
            get_setting(settings, f"{CONFIG_PREFIX}.bandit.min_symbol_samples")
        )
        self._drawdown_weight = float(
            get_setting(settings, f"{CONFIG_PREFIX}.bandit.drawdown_weight")
        )
        if self._drawdown_weight < 0.0:
            raise ValueError(
                f"{CONFIG_PREFIX}.bandit.drawdown_weight negatif olamaz: "
                f"{self._drawdown_weight}"
            )
        self._seed = int(get_setting(settings, "random_seed"))
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
        if self._template.band_short <= 0.0 or self._template.band_long <= 0.0:
            raise ValueError(f"{CONFIG_PREFIX}.band değerleri pozitif olmalı")

        # --- Defterden kurulan durum (kural 16: yalnızca KENDİ kapanmış işlemleri) ---
        self._daily_r: dict[pd.Timestamp, float] = {}
        self._drawdown_r = 0.0
        self._global: dict[str, ComboStats] = _empty_stats(self._combos)
        self._by_symbol: dict[str, dict[str, ComboStats]] = {}
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
        overall: dict[str, list[float]] = {combo.key: [] for combo in self._combos}
        by_symbol: dict[str, dict[str, list[float]]] = {}
        untagged = 0
        unknown: set[str] = set()

        for trade in sorted(trades, key=lambda item: item.closed_at):
            if trade.r_multiple is None:
                unmeasured += 1
                continue
            value = float(trade.r_multiple)
            cumulative += value
            peak = max(peak, cumulative)
            daily[pd.Timestamp(trade.closed_at).normalize()] += value

            key = find_tag(trade.signal_reason, "combo")
            if key is None:
                untagged += 1
                continue
            if key not in overall:
                # Grid değişmişse (config'te çarpan listesi düzenlenmişse) eski satırlar
                # yeni kollara ait DEĞİLDİR; onları yeni kolların ortalamasına katmak,
                # öğrenmeyi defterde görünmeyen bir geçmişe bağlardı.
                unknown.add(key)
                continue
            overall[key].append(value)
            by_symbol.setdefault(trade.symbol, {k: [] for k in overall})[key].append(value)

        self._daily_r = dict(daily)
        self._drawdown_r = peak - cumulative
        self._global = {
            key: _summarize(values, weight=self._drawdown_weight)
            for key, values in overall.items()
        }
        self._by_symbol = {
            symbol: {
                key: _summarize(values, weight=self._drawdown_weight)
                for key, values in per_combo.items()
            }
            for symbol, per_combo in by_symbol.items()
        }
        if untagged:
            logger.warning(
                "%s: %d kapanmış işlemde combo etiketi yok, öğrenmeye girmedi",
                self.name, untagged,
            )
        if unknown:
            logger.warning(
                "%s: defterde tanınmayan kombinasyon: %s — grid değişmiş olabilir, bu "
                "satırlar öğrenmeye girmedi",
                self.name, ", ".join(sorted(unknown)),
            )
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
        logger.info("%s öğrenme durumu: %s", self.name, self.state_summary())

    def state_summary(self) -> str:
        """Kombinasyonların tek satırlık özeti — denetim ve testler için."""
        return " ".join(
            f"{key}(n={item.trades},R={item.mean_r:.3f},skor={item.score:.3f})"
            if item.measured
            else f"{key}(n=0)"
            for key, item in self._global.items()
        )

    def stats_for(self, symbol: str) -> dict[str, ComboStats]:
        """Sembolün istatistikleri; testler ve denetim için salt okunur kopya."""
        return dict(self._by_symbol.get(symbol, {}))

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
        rng = self._round_rng(market)
        picks: dict[str, tuple[Combo, ComboStats, str]] = {}

        def params_for(symbol: str) -> guarded_signal.GuardParams:
            """Sembolün kombinasyonunu çeker ve parametreye çevirir.

            Çekiliş kurulumdan ÖNCE gelir (kaynağın `learner.select(symbol)` deseni) ve
            sembol başına bir kezdir. `symbol_views` adı SIRALI döndürdüğü için çekiliş
            dizisi tekrarlanabilirdir — sıra sözlük sırasına bırakılsaydı aynı bar iki
            koşuda farklı kombinasyon dağılımı verirdi.
            """
            combo, stats, pick = self.choose_combo(symbol, rng=rng)
            picks[symbol] = (combo, stats, pick)
            return self._params_for(combo)

        candidates, survey = guarded_signal.scan(
            market,
            atr_period=self._atr_period,
            params_for=params_for,
            bias=bias,
            symbols=self._universe,
        )
        self._scan_counts = survey.report()
        logger.info(
            "%s %s %s -> %s",
            self.name, guarded_signal.ARM_NAME, market.as_of.isoformat(), survey.describe(),
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
            combo, stats, pick = picks[candidate.symbol]
            stop = guarded_signal.stop_price(candidate, atr_multiple=self._atr_multiple)
            projected = guarded_signal.projected_target(
                candidate, stop=stop, reward_risk=combo.target_reward_risk
            )
            target = guarded_signal.nearest_target(candidate, projected=projected)
            if not self._passes_gates(candidate, stop=stop, target=target):
                continue
            signal = self._signal(
                candidate, stop=stop, target=target, bias=bias,
                combo=combo, stats=stats, pick=pick,
            )
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

    # ------------------------------------------------------------------ #
    # Epsilon-greedy seçim (kaynağın iskeleti; ödül ve eşikler bu modelin)
    # ------------------------------------------------------------------ #
    def choose_combo(
        self, symbol: str, *, rng: random.Random
    ) -> tuple[Combo, ComboStats, str]:
        """Üç adım; dönen üçüncü değer deftere yazılan `pick` etiketidir.

        1. **Isınma — GLOBAL olarak denenmemiş kombinasyon.** Kopyada bu adım SEMBOL
           başınadır; burada değil, çünkü sembol istatistiğine `min_symbol_samples` (30)
           örnekten önce zaten güvenilmiyor. Sembol başına ısınma, 9 kombinasyon × 11
           sembol = 99 işlemi saf keşfe harcardı ve bu katmanın toplam örneklemi o kadar
           bile değil.
        2. **Keşif** — `epsilon` (0.10) olasılıkla eşit çekiliş. Sıfıra İNMEZ: susturulan
           kombinasyon bir daha ölçülemez ve "kötüydü" iddiası sınanamaz hâle gelir.
        3. **Sömürü** — en yüksek SKOR (ortalama R değil): `(Σr − w × maxDD) / n`.
           Beraberlikte anahtar adına göre, çünkü sıralamanın kendisi de tekrarlanabilir
           olmalıdır.
        """
        stats = self._stats_for_choice(symbol)

        unexplored = [combo for combo in self._combos if not self._global[combo.key].measured]
        if unexplored:
            combo = rng.choice(unexplored)
            logger.info(
                "%s %s: denenmemiş kombinasyon (%d/%d kaldı) -> %s",
                self.name, symbol, len(unexplored), len(self._combos), combo.key,
            )
            return combo, stats[combo.key], "unexplored"

        if rng.random() < self._epsilon:
            combo = rng.choice(self._combos)
            logger.info(
                "%s %s: keşif çekilişi (epsilon=%.2f) -> %s",
                self.name, symbol, self._epsilon, combo.key,
            )
            return combo, stats[combo.key], "explore"

        measured = [combo for combo in self._combos if stats[combo.key].measured]
        if not measured:
            # Buraya ancak defter etiketleri okunamadığında düşülür (bkz.
            # observe_closed_trades'in "tanınmayan kombinasyon" uyarısı). Sabit bir
            # kombinasyonu ayrıcalıklı kılmamak için çekiliş yapılır.
            combo = rng.choice(self._combos)
            logger.info("%s %s: ölçüm yok, eşit çekiliş -> %s", self.name, symbol, combo.key)
            return combo, stats[combo.key], "explore"

        combo = max(measured, key=lambda item: (stats[item.key].score, item.key))
        logger.info(
            "%s %s: en iyi kombinasyon -> %s (skor=%.3f, R=%.3f, n=%d)",
            self.name, symbol, combo.key, stats[combo.key].score,
            stats[combo.key].mean_r, stats[combo.key].trades,
        )
        return combo, stats[combo.key], "exploit"

    def _stats_for_choice(self, symbol: str) -> dict[str, ComboStats]:
        """Kombinasyon -> istatistik; sembolde yeterli örnek yoksa o hücre GENELE düşer.

        Geri düşüş kombinasyon BAZINDADIR, sembolün tamamı için değil: bir sembolde bir
        kombinasyon 40 kez, diğeri 2 kez oynanmış olabilir ve ölçülmüş olanı genelin
        ortalamasına feda etmek, sembolün gerçekten taşıdığı bilgiyi atmak olurdu.
        """
        per_symbol = self._by_symbol.get(symbol, {})
        resolved: dict[str, ComboStats] = {}
        for combo in self._combos:
            local = per_symbol.get(combo.key)
            if local is not None and local.trades >= self._min_symbol_samples:
                resolved[combo.key] = local
            else:
                resolved[combo.key] = self._global[combo.key]
        return resolved

    def _params_for(self, combo: Combo) -> guarded_signal.GuardParams:
        """Kombinasyonun bandı + öğrenilmeyen kapıların tek kopyası."""
        return replace(self._template, band_short=combo.band_short, band_long=combo.band_long)

    def _round_rng(self, market: MarketData) -> random.Random:
        """Bar ve model başına bağımsız RNG; tohum sabit, çekiliş tekrarlanabilir.

        Durum TAŞIMAZ: her bar `random_seed`, `as_of` ve model adıyla yeniden tohumlanır,
        yani aynı defter aynı barda her zaman aynı çekilişi verir (backtest Kapı 0'ın
        bunu uyarlanabilir modellerden beklememesinin sebebi öğrenilen GEÇMİŞTİR, çekiliş
        değil).
        """
        return random.Random(f"{self._seed}:{market.as_of.isoformat()}:{self.name}")

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
        combo: Combo,
        stats: ComboStats,
        pick: str,
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
                combo=combo.key,
                combo_score=stats.score,
                combo_n=stats.trades,
                pick=pick,
                rr=reward_risk,
                adx=candidate.adx,
                exhaustion=candidate.exhaustion,
                btc=bias,
            ),
        )


# --------------------------------------------------------------------------- #
# Yardımcılar
# --------------------------------------------------------------------------- #
def _build_combos(
    band_mults: Sequence[float],
    target_reward_risks: Sequence[float],
    *,
    long_extra: float,
    floor: float,
) -> list[Combo]:
    """Kartezyen çarpım, SABİT sırayla. Sıra tekrarlanabilirliğin parçasıdır.

    `long_extra` bandın YÖN ASİMETRİSİDİR ve grid'in ekseni DEĞİLDİR: long tarafı her
    kombinasyonda short'tan tam bu kadar geniştir. Ayrı bir eksen olsaydı grid 27 kola
    çıkar ve her kolun örneklemi üçe bölünürdü — bu katmanda hiçbiri ölçülebilir
    olmazdı.

    `floor` bandın tabanıdır (2.5σ): grid'in altına inmesi, "1.5σ gürültüdür" kararını
    bir config satırıyla geri almak olurdu.
    """
    if not band_mults or not target_reward_risks:
        raise ValueError(f"{CONFIG_PREFIX}.bandit listeleri boş olamaz")
    if any(value < floor for value in band_mults):
        raise ValueError(
            f"{CONFIG_PREFIX}.bandit.band_mults tabanın ({floor:g}σ) altına inemez: "
            f"{list(band_mults)}"
        )
    if any(value <= 0.0 for value in target_reward_risks):
        raise ValueError(
            f"{CONFIG_PREFIX}.bandit.target_reward_risks pozitif olmalı: "
            f"{list(target_reward_risks)}"
        )
    if long_extra < 0.0:
        raise ValueError(
            f"{CONFIG_PREFIX}.bandit.long_extra negatif olamaz — long tarafı short'tan "
            f"DAR olamaz: {long_extra}"
        )
    return [
        Combo(
            key=f"band{band_index}_rr{rr_index}",
            band_short=float(band),
            band_long=float(band) + long_extra,
            target_reward_risk=float(reward_risk),
        )
        for band_index, band in enumerate(band_mults)
        for rr_index, reward_risk in enumerate(target_reward_risks)
    ]


def _empty_stats(combos: Sequence[Combo]) -> dict[str, ComboStats]:
    return {
        combo.key: ComboStats(trades=0, mean_r=NAN, max_drawdown_r=NAN, score=NAN)
        for combo in combos
    }


def _summarize(values: Sequence[float], *, weight: float) -> ComboStats:
    """Kombinasyonun özeti: ortalama R, en derin düşüş ve cezalandırılmış skor.

    En derin düşüş, o kombinasyonun KENDİ kapanış sırasındaki kümülatif R eğrisinin
    zirveden en büyük gerilemesidir. Zirve 0'dan başlar: ilk işlemi kaybeden bir kol
    zaten çukurdadır ve "henüz zirve yapmadı" diye cezasız kalmamalıdır.
    """
    if not values:
        return ComboStats(trades=0, mean_r=NAN, max_drawdown_r=NAN, score=NAN)
    cumulative = 0.0
    peak = 0.0
    drawdown = 0.0
    for value in values:
        cumulative += value
        peak = max(peak, cumulative)
        drawdown = max(drawdown, peak - cumulative)
    count = len(values)
    return ComboStats(
        trades=count,
        mean_r=cumulative / count,
        max_drawdown_r=drawdown,
        score=(cumulative - weight * drawdown) / count,
    )

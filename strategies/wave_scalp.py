"""MODEL 21 — Elliott Wave Dalga-3 (15m), dış bir sistemin TEZİ ama EVİN kurallarıyla.

Ön-kayıt: **docs/backtest.md > 6h** (koşudan ve bu dosyadan ÖNCE commit edildi).

**YARIŞMACIDIR, kopya DEĞİL** (`is_replica = False`) — emsali `ema_trend`dir (karar 45 §1).
Dışarıdan gelen üç şeydir: sinyal kuralı (`strategies/wave/clone_signal.py`), bandit
ızgarası (12 kombinasyon) ve çıkış yönetimi. Boyut, kaldıraç tavanı, maliyet, kayma,
funding ve likidasyon EVİN kurallarıdır. Gerekçe ölçülecek şeyin kendisidir: Aşama 2'nin
iyileştirme turu `cost_per_r` ve `avg_stop_distance_pct` üzerinden okunacak ve kopya
statüsünde (kural 15b) bu iki kolon tanım gereği `nan` olurdu — yani model,
iyileştirmenin okunacağı kolonları taşımayan bir satır olurdu.

Bunun BEDELİ vardır ve ön-kayıtta 3(f)/3(g) olarak yazılıdır:

- Kaynağın sabit teminat × 10x boyutlandırması KOPYALANMAZ; `sizing="risk"` ile
  `risk_per_trade` (%1) ve `leverage_cap` (5) geçerlidir (kural 3/11).
- `ModelLimits` yalnızca kopyalara açıktır (kural 15b, kapı
  `core/validate.py::validate_model`), yani kaynağın "aynı YÖNDE en çok 3" ve "açık toplam
  risk ≤ %8" bildirimleri BU MODELDE YOKTUR. Kök kotalar geçerlidir: `max_positions` 5
  (kaynakla aynı) ve `max_short_positions` 3 (kaynağın yön kotasının yalnızca SHORT
  tarafı). Portföy riski tavanı yapısal olarak bağlamaz: %1 × en çok 5 pozisyon = %5 < %8.

**Ev kapıları UYGULANMAZ** (§6h > 3(h)): scalp katmanının %1 stop tabanı, 1.5R hedef/stop
kapısı ve 16 barlık zaman stop'u kaynakta yoktur. Bu yüzden model `ScalpModel` gövdesinden
TÜREMEZ ve `strategies/time_stop.py`'yi OKUMAZ — kapıları eklemek modeli ölçülmek
isteneni başka bir şeye çevirirdi (`vwap_clone`a verilen aynı gerekçe). Motorun stop
tavanı (kural 14, katmanda 8×ATR) ise UYGULANIR ve elenen sinyal sayısı
`ModelReport.skipped_signals`ta sayılır.

**Çıkış yönetimi YENİDEN YAZILMAZ:** `strategies/exit_management.py` tek kopyadır (modeller
13, 14, 15 ile ortak) ve kaynağın `--breakeven-r 1.0 / --partial-tp-r 1.5 /
--partial-tp-fraction 0.5 / --trail-giveback-pct 0.5` varsayılanlarıyla birebir aynıdır.
Uygulaması motorda ve portföydedir (kural 9/13b), burada yalnızca İSTEK bildirilir.

**Bandit durumu deftere yazılır, ayrı bir durum dosyası YOKTUR** (`vwap_clone` ve
`scalp_bandit` ile aynı desen ve aynı gerekçe): istatistik `trades.csv`'nin saf bir
fonksiyonudur ve her turda sıfırdan kurulur. Kaynak iki JSON tutar; burada tutmak,
defterle senkron kalması ayrıca test edilmesi gereken İKİNCİ bir doğruluk kaynağı
yaratırdı. Bu bir sapma değildir: kaynağın istatistiği de yalnızca KAPANAN işlemlerden
beslenir (`learner.update`, `try_close_position`ın içinden), yani iki yol aynı defterden
aynı sayıyı üretir.

Denetim izi `reason` kuyruğundadır:
`... | arm=wave3_src | combo=dev1.2_tp1.618 | retrace=0.412 | wave1=1234 | ...`

Rollere dikkat: bu modül boyut/komisyon/bakiye hesaplamaz (kural 1/2/3/7), deftere yazmaz
(kural 1) ve gördüğü tek geçmiş KENDİ kapanmış işlemleridir (kural 16).
"""

from __future__ import annotations

import logging
import random
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from core.config import get_setting, load_config
from core.tags import find_tag, format_tags
from strategies.base import (
    ClosedTrade,
    Direction,
    MarketData,
    Signal,
    Strategy,
    TakeProfit,
)
from strategies.exit_management import ExitManagement
from strategies.wave import clone_signal

logger = logging.getLogger(__name__)

NAN = float("nan")


@dataclass(frozen=True, kw_only=True)
class Combo:
    """Bandit'in bir kolu: bir ATR katsayısı + bir hedef çarpanı (`sl_mult` sabit).

    `key` deftere yazılan ve geri OKUNAN etikettir ve DEĞERDEN türer (`dev1.2_tp1.618`) —
    `vwap_clone`un indeks tabanlı anahtarından (`band0_tp1`) bilinçli olarak ayrılır.
    Orada itiraz "kayan noktayı biçimlendirip geri AYRIŞTIRMAK bir gün ayrışır"dı; burada
    ayrıştırma ADIMI YOKTUR: anahtar kümesi her kurulumda config'ten yeniden üretilir ve
    defterdeki etiket o kümeye karşı yalnızca KARŞILAŞTIRILIR (tanınmayan anahtar
    `observe_closed_trades`ta loglanır, sessizce öğrenmeye girmez). Biçimlendirmenin
    kararlılığı ve anahtarların tekilliği ayrıca sınanır
    (`tests/test_wave_scalp.py::test_combo_keys_are_stable_and_unique`).

    Okunabilirliğin bedeli olmadığı için tercih edilir: ön-kayıt (§6h > 10.4) 12 hücreli
    ızgara tablosunu raporlamayı zorunlu kılıyor ve `dev2.5_tp2` satırı `dev3_tp2`den
    hangi çarpanı anlattığını kendi başına söyler.
    """

    key: str
    params: clone_signal.WaveParams

    @property
    def deviation_pct(self) -> float:
        return self.params.deviation_pct

    @property
    def tp_mult(self) -> float:
        return self.params.tp_mult


@dataclass(frozen=True, kw_only=True)
class ComboStats:
    """Bir kombinasyonun gözlem özeti. `nan` = ölçülmedi (`0.0` DEĞİL)."""

    trades: int
    mean_r: float

    @property
    def measured(self) -> bool:
        return self.trades > 0


class WaveScalp(Strategy):
    name = "wave_scalp"
    allowed_directions: list[Direction] = ["long", "short"]

    def __init__(self, *, config: Mapping[str, Any] | None = None) -> None:
        settings = dict(config) if config is not None else load_config()
        self._universe = [str(s) for s in get_setting(settings, "wave.clone.universe")]
        self._window_bars = int(get_setting(settings, "wave.clone.window_bars"))
        self._min_deviation_pct = float(get_setting(settings, "wave.clone.min_deviation_pct"))
        self._epsilon = float(get_setting(settings, "wave.clone.bandit.epsilon"))
        self._min_symbol_samples = int(
            get_setting(settings, "wave.clone.bandit.min_symbol_samples")
        )
        # ATR PERİYODU modele özel DEĞİLDİR: projenin tek ATR periyodu okunur. Kaynağın
        # kendi varsayılanı da 14'tür, yani SPEC ile ev burada çakışmıyor — ve motorun
        # stop tavanı kontrolü aynı periyodu ölçtüğü için "×ATR" tek bir sayı kalır.
        self._atr_period = int(get_setting(settings, "trailing.atr_period"))
        # Emitted listesinin kırpıldığı üst sınır; KOTAYI UYGULAYAN yer burası DEĞİL
        # (kural 3/7), `core/portfolio.py`dir. Bkz. generate_signals.
        self._max_positions = int(get_setting(settings, "max_positions"))
        self._exit = ExitManagement.from_config(settings)
        self._seed = int(get_setting(settings, "random_seed"))

        self._combos = _build_combos(
            [float(v) for v in get_setting(settings, "wave.clone.deviations")],
            [float(v) for v in get_setting(settings, "wave.clone.tp_mults")],
            sl_mult=float(get_setting(settings, "wave.clone.sl_mult")),
            retrace_min=float(get_setting(settings, "wave.clone.retrace_min")),
            retrace_max=float(get_setting(settings, "wave.clone.retrace_max")),
        )
        if not 0.0 <= self._epsilon <= 1.0:
            raise ValueError(f"wave.clone.bandit.epsilon 0 ile 1 arasında olmalı: {self._epsilon}")
        if self._min_deviation_pct <= 0.0:
            raise ValueError(
                f"wave.clone.min_deviation_pct pozitif olmalı: {self._min_deviation_pct}"
            )
        # Pencere KURULUMDA denetlenir, sinyal anında değil: ATR'yi hesaplayamayan bir
        # pencere modeli her turu sessizce eşik tabanıyla (%0.05) koşturur ve "hiç sinyal
        # üretmeyen model" ile "kurulumu bozuk model" ayırt edilemez hâle gelirdi
        # (kural 15'in tam tersi; `vwap_clone`un std_window denetiminin aynısı).
        if self._window_bars < self._atr_period + 1:
            raise ValueError(
                f"wave.clone.window_bars ({self._window_bars}) ATR periyodu + 1 "
                f"({self._atr_period + 1}) değerinin altında olamaz: zigzag eşiği hiçbir "
                "zaman volatiliteye ölçeklenemezdi"
            )

        # Sembol bazlı ve genel istatistik; ikisi de defterden kurulur.
        self._by_symbol: dict[str, dict[str, ComboStats]] = {}
        self._global: dict[str, ComboStats] = _empty_stats(self._combos)
        # Son taramanın eleme sayımı (kural 15). Motor her BARDAN sonra okur ve tur
        # raporuna toplar; ölçüme girmez, sinyalleri ve sıralarını etkilemez.
        self._survey: clone_signal.Survey | None = None

    # ------------------------------------------------------------------ #
    # Öğrenme (kural 16)
    # ------------------------------------------------------------------ #
    def observe_closed_trades(self, trades: Sequence[ClosedTrade]) -> None:
        """Kendi KAPANMIŞ işlemlerinden kombinasyon istatistiklerini SIFIRDAN kurar.

        Artımlı güncelleme YOK (`vwap_clone` ve `scalp_bandit` ile aynı gerekçe): durum
        defterin saf bir fonksiyonu olmazsa, defterle modelin hafızası bir gün ayrışır ve
        hangisinin doğru olduğu bilinemez.

        Etiketi TANINMAYAN satır sessizce atlanmaz, loglanır: kombinasyon kümesi
        değişmişse (config'te çarpan listesi düzenlenmişse) eski satırlar yeni kümeye ait
        DEĞİLDİR ve onları yeni kolların ortalamasına katmak, öğrenmeyi defterde
        görünmeyen bir geçmişe bağlardı.

        `arm=` etiketi burada aranmaz (`combo` aranır) ve yokluğu ayrıca sayılır; kol
        kırılımının bütünlüğünü `core/metrics.py::arm_of` kendi başına denetler.
        """
        by_symbol: dict[str, dict[str, list[float]]] = {}
        overall: dict[str, list[float]] = {combo.key: [] for combo in self._combos}
        unknown: set[str] = set()
        untagged = 0

        for trade in sorted(trades, key=lambda item: item.closed_at):
            key = find_tag(trade.signal_reason, "combo")
            if key is None:
                untagged += 1
                continue
            if key not in overall:
                unknown.add(key)
                continue
            if trade.r_multiple is None:
                continue
            overall[key].append(float(trade.r_multiple))
            by_symbol.setdefault(trade.symbol, {k: [] for k in overall})[key].append(
                float(trade.r_multiple)
            )

        if untagged:
            logger.warning(
                "%s: %d kapanmış işlemde combo etiketi yok, öğrenmeye girmedi",
                self.name, untagged,
            )
        if unknown:
            logger.warning(
                "%s: defterde tanınmayan kombinasyon: %s — kombinasyon kümesi değişmiş "
                "olabilir, bu satırlar öğrenmeye girmedi",
                self.name, ", ".join(sorted(unknown)),
            )

        self._global = {key: _summarize(values) for key, values in overall.items()}
        self._by_symbol = {
            symbol: {key: _summarize(values) for key, values in per_combo.items()}
            for symbol, per_combo in by_symbol.items()
        }
        logger.info("%s öğrenme durumu: %s", self.name, self.state_summary())

    def state_summary(self) -> str:
        return " ".join(
            f"{key}(n={item.trades},R={item.mean_r:.3f})" if item.measured else f"{key}(n=0)"
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
        """Kaynağın taraması: evren listesini BAŞTAN SONA, sembol SIRASIYLA.

        Güce göre sıralama YOKTUR (model 14'ün "en güçlü tek aday"ı burada geçersizdir):
        kaynak `POPULAR_COINS`i sırayla gezer, her sembolde ÖNCE kombinasyonu çeker
        (`learner.select(symbol)`), SONRA kurulumu arar. Çekilişin kurulumdan önce gelmesi
        kaynağın davranışıdır ve burada da korunur — aksi hâlde aynı turda çekilen
        kombinasyon dizisi, dolayısıyla keşif payının dağılımı başkalaşırdı.

        **Barda tek sinyalle sınırlanmaz** (scalp kollarının kuralı burada yok): kaynak
        aynı anda beş pozisyona kadar taşır. Liste yine de kök `max_positions` ile
        KIRPILIR — kotanın ötesindeki her emir bir sonraki barda zaten `max_positions`
        koduyla reddedilir ve tur raporunu gerçek olmayan retlerle doldururdu. Kırpma
        listenin SONUNDAN yapılır, yani kaynağın "kota dolunca kalan sembollere hiç bakma"
        davranışıyla aynı sonucu verir.

        Kırpma bir KOTA UYGULAMASI DEĞİLDİR (kural 3/7): model kendi açık pozisyonlarını
        göremez (kural 4/16), yani bu yalnızca bir ÜST SINIRDIR. Gerçek kotayı tek yetkili
        yer uygular (`core/portfolio.py`) ve reddi sebep koduyla sayar (kural 15).
        """
        rng = self._round_rng(market)
        counts = clone_signal.empty_counts()
        signals: list[Signal] = []

        for symbol in self._universe:
            combo, stats, pick = self.choose_combo(symbol, rng=rng)
            candidate, reason = clone_signal.detect(
                market.ohlcv.get(symbol),
                symbol=symbol,
                as_of=market.as_of,
                params=combo.params,
                atr_period=self._atr_period,
                window_bars=self._window_bars,
                min_deviation_pct=self._min_deviation_pct,
            )
            counts[reason] += 1
            if candidate is not None:
                signals.append(self._signal(candidate, combo=combo, stats=stats, pick=pick))

        survey = clone_signal.Survey(counts=counts)
        self._survey = survey
        logger.info(
            "%s %s %s -> %s",
            self.name, clone_signal.ARM_NAME, market.as_of.isoformat(), survey.describe(),
        )

        return signals[: self._max_positions]

    def take_survey(self) -> Mapping[str, int] | None:
        """Son taramanın eleme sayımı (kural 15). Sinyalleri hiçbir biçimde etkilemez."""
        return None if self._survey is None else dict(self._survey.counts)

    def _signal(
        self,
        candidate: clone_signal.WaveCandidate,
        *,
        combo: Combo,
        stats: ComboStats,
        pick: str,
    ) -> Signal:
        return Signal(
            symbol=candidate.symbol,
            direction=candidate.direction,
            # EVİN boyutlandırması (madde 1): kaynağın sabit teminatı KOPYALANMAZ.
            sizing="risk",
            stop_price=candidate.stop_price,
            take_profits=(TakeProfit(price=candidate.target_price, fraction=1.0),),
            **self._exit.signal_fields(),
            reason=format_tags(
                f"{candidate.detail()}; stop p2 ∓ dalga1×{combo.params.sl_mult:g} "
                f"({candidate.stop_price:.6g}), hedef p2 ± dalga1×{combo.tp_mult:g} "
                f"({candidate.target_price:.6g}); {self._exit.describe()}",
                arm=clone_signal.ARM_NAME,
                combo=combo.key,
                retrace=candidate.setup.retrace,
                wave1=candidate.setup.wave1_len,
                # Hedef FİYATI etiket olarak yazılır çünkü `trades.csv`de TP kolonu
                # YOKTUR ve yeni kolon açılamaz (kural 13c: başlık değişirse eski
                # satırlar okunamaz hâle gelir). Ön-kayıt (§6h > 10.5) fiili R:R
                # dağılımını istiyor ve o oran, dolum fiyatı (`entry_price`) ile ilk
                # stop (`stop_price`) yanında hedefi de gerektirir.
                target=candidate.target_price,
                combo_r=stats.mean_r,
                combo_n=stats.trades,
                pick=pick,
            ),
        )

    # ------------------------------------------------------------------ #
    # Epsilon-greedy seçim (kaynak: learner.py::select)
    # ------------------------------------------------------------------ #
    def choose_combo(
        self, symbol: str, *, rng: random.Random
    ) -> tuple[Combo, ComboStats, str]:
        """Kaynağın üç adımlı seçimi; dönen üçüncü değer deftere yazılan `pick` etiketidir.

        1. **Denenmemiş öncelik.** O SEMBOLDE hiç örneği olmayan kombinasyon varsa önce
           onlardan biri çekilir (kaynağın `unexplored` dalı). Kontrol sembolün KENDİ
           sayımına bakar, genele düşmüş hücreye değil — genel ortalama dolu diye o
           sembolde hiç denenmemiş bir kolu "denenmiş" saymak, ısınmayı ilk sembolden
           sonra tümden atlamak olurdu.
        2. **Keşif.** `epsilon` olasılıkla ızgaradan rastgele. Pay sabittir ve sıfıra
           inmez: sıfırlanan bir kombinasyon bir daha ÖLÇÜLEMEZ ve "kötüydü" iddiası
           sınanamaz hâle gelir.
        3. **Sömürü.** En yüksek ortalama R. İstatistik önce SEMBOL bazında aranır; aynı
           kurulum BTC'de ve ETHFI'de aynı katsayıyla aynı sonucu vermez. Ama sembol
           başına veri geç birikir ve `min_symbol_samples` örneğe ulaşmamış bir sembolde
           sembolün kendi gürültüsüne uymak, tüm semboller genelindeki ortalamadan DAHA
           KÖTÜ bir tahmindir — o yüzden genele düşülür.

        Eşitlik bozma `key`e göredir (`vwap_clone` ile aynı): iki kombinasyonun ortalaması
        birebir eşitse seçim tohumdan değil SABİT bir sıradan gelir, yani tekrarlanabilir.
        """
        stats = self._stats_for_choice(symbol)

        unexplored = [combo for combo in self._combos if not self._seen(symbol, combo)]
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
            # Birinci adım her kombinasyonu en az bir kez oynattığı için buraya ancak
            # defter etiketleri okunamadığında düşülür (observe_closed_trades'in
            # "tanınmayan kombinasyon" uyarısı). Sabit bir kombinasyonu ayrıcalıklı
            # kılmamak için çekiliş yapılır.
            combo = rng.choice(self._combos)
            logger.info(
                "%s %s: henüz ölçüm yok, eşit çekiliş -> %s", self.name, symbol, combo.key
            )
            return combo, stats[combo.key], "explore"

        combo = max(measured, key=lambda item: (stats[item.key].mean_r, item.key))
        logger.info(
            "%s %s: en iyi kombinasyon -> %s (R=%.3f, n=%d)",
            self.name, symbol, combo.key, stats[combo.key].mean_r, stats[combo.key].trades,
        )
        return combo, stats[combo.key], "exploit"

    def _seen(self, symbol: str, combo: Combo) -> bool:
        """Bu SEMBOLDE bu kombinasyonun en az bir kapanmış işlemi var mı?"""
        local = self._by_symbol.get(symbol, {}).get(combo.key)
        return local is not None and local.measured

    def _stats_for_choice(self, symbol: str) -> dict[str, ComboStats]:
        """Kombinasyon -> istatistik; sembolde yeterli örnek yoksa o hücre GENELE düşer.

        Geri düşüş kombinasyon BAZINDADIR, sembolün tamamı için değil (kaynakta da böyle):
        bir sembolde bir kombinasyon 10 kez, diğeri 1 kez oynanmış olabilir ve ölçülmüş
        olanı genelin ortalamasına feda etmek, sembolün gerçekten taşıdığı bilgiyi atmak
        olurdu.
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

    def _round_rng(self, market: MarketData) -> random.Random:
        """Tur ve model başına bağımsız RNG; tohum sabit, seçim tekrarlanabilir.

        Kimlik model ADINI taşır, yani çekiliş `vwap_clone` ile PAYLAŞILMAZ: iki model
        aynı barda aynı sembolleri tarıyor ama ölçtükleri şey ayrı ve paylaşılan bir
        çekiliş aralarına ölçülmeyen bir bağ koyardı.
        """
        return random.Random(f"{self._seed}:{market.as_of.isoformat()}:{self.name}")


# --------------------------------------------------------------------------- #
# Yardımcılar
# --------------------------------------------------------------------------- #
def _build_combos(
    deviations: Sequence[float],
    tp_mults: Sequence[float],
    *,
    sl_mult: float,
    retrace_min: float,
    retrace_max: float,
) -> list[Combo]:
    """Kartezyen çarpım, SABİT sırayla. Sıra tekrarlanabilirliğin parçasıdır.

    `sl_mult` ızgaranın bir ekseni DEĞİLDİR (kaynakta `WAVE_SL_MULT` sabittir): üçüncü bir
    eksen ızgarayı 36 hücreye çıkarıp her birinin örneklemini üçe bölerdi. Kaynağın
    defterinde ızgara dışı bir `sl_mult=1.5` satırı vardır (n=2) ve o satır TAŞINMAZ
    (§6h > 3(e)).
    """
    if not deviations or not tp_mults:
        raise ValueError("wave.clone çarpan listeleri boş olamaz")
    if any(value <= 0.0 for value in deviations):
        raise ValueError(f"wave.clone.deviations pozitif olmalı: {list(deviations)}")
    if any(value <= 0.0 for value in tp_mults):
        raise ValueError(f"wave.clone.tp_mults pozitif olmalı: {list(tp_mults)}")
    if sl_mult <= 0.0:
        raise ValueError(f"wave.clone.sl_mult pozitif olmalı: {sl_mult}")
    if not 0.0 < retrace_min < retrace_max < 1.0:
        # Retrace bir ORANDIR ve dalga-2 dalga-1'in içinde kalmalıdır: `retrace_max >= 1`
        # p2'nin p0'ı aştığı kurulumları geçirirdi ve kaynağın örtüşme kapısıyla
        # çelişirdi.
        raise ValueError(
            f"wave.clone retrace aralığı 0 < min < max < 1 olmalı: "
            f"{retrace_min} .. {retrace_max}"
        )
    combos = [
        Combo(
            key=combo_key(deviation, tp_mult),
            params=clone_signal.WaveParams(
                deviation_pct=float(deviation),
                tp_mult=float(tp_mult),
                sl_mult=float(sl_mult),
                retrace_min=float(retrace_min),
                retrace_max=float(retrace_max),
            ),
        )
        for deviation in deviations
        for tp_mult in tp_mults
    ]
    keys = [combo.key for combo in combos]
    if len(set(keys)) != len(keys):
        # Değer tabanlı anahtarın TEK riski budur: `%g` biçimlendirmesi iki farklı çarpanı
        # aynı metne indirirse posterior iki kolu tek hücrede toplar. Kurulumda patlamak,
        # aylarca birleşmiş bir istatistikle koşmaktan iyidir.
        raise ValueError(
            f"wave.clone çarpanları çakışan kombinasyon anahtarı üretti: {keys}"
        )
    return combos


def combo_key(deviation_pct: float, tp_mult: float) -> str:
    """Kombinasyonun defter etiketi. TEK tanım — yazan ve okuyan aynı metni üretsin diye."""
    return f"dev{deviation_pct:g}_tp{tp_mult:g}"


def _empty_stats(combos: Sequence[Combo]) -> dict[str, ComboStats]:
    return {combo.key: ComboStats(trades=0, mean_r=NAN) for combo in combos}


def _summarize(values: Sequence[float]) -> ComboStats:
    if not values:
        return ComboStats(trades=0, mean_r=NAN)
    return ComboStats(trades=len(values), mean_r=sum(values) / len(values))

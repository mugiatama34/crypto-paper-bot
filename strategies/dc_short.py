"""Model 22 — `dc_short`: ölüm kesişimi rejiminde EMA50'ye geri çekilmenin reddi, short.

Ön-kayıt: docs/backtest.md > 6i (commit `03e9e2e`, TADİLAT-1 `5ad7653`). Kuralları dış
bir kaynaktan (bir eğitim görseli) gelir ama KOPYA DEĞİLDİR (kural 15b): dışarıdan gelen
yalnızca sinyaldir; boyut (risk %1), kaldıraç tavanı, maliyet, funding ve likidasyon evin
kuralıdır — yani tam bir yarışmacıdır (`ema_trend`in statüsü).

Kurulum, geometri ve sayım `strategies/dc/signal.py`dedir ve kontrol (`dc_coinflip`) aynı
kopyayı okur. Bu sınıfın kendine ait TEK şeyi `orient`tir — ölçülen eksen (yön) odur ve
kural `ScalpModel`/`XsecModel`inkiyle aynıdır: **karşılığı ölçülen bir eksen olmayan bir
override noktası eklenemez.**

Çıkış yalnızca stop, hedef ya da likidasyondur: `manage_positions` UYGULANMAZ — zaman
stop'u ve rejim-sonu çıkışı kaynakta yok. Zaman stop'unun yokluğunun bedeli ölçümdedir:
OOS embargosu varsayılamaz, dönem A'dan ölçülür (§6i > 6).

**Sembol başına tek pozisyon kuralı bu modülde DEĞİL `core/portfolio.py`dedir** (kural 4:
model kendi açık pozisyonunu göremez). Model her kurulum barında sinyal üretir; elde zaten
olan sembolün sinyali `duplicate_position` sebep koduyla reddedilir ve sayılır.
"""

from __future__ import annotations

from typing import Any, Mapping

from core.config import get_setting, load_config
from core.tags import format_tags
from strategies.base import Direction, MarketData, Signal, Strategy, TakeProfit
from strategies.dc.signal import ARM, DcRules, DcSetup, scan


class DcShort(Strategy):
    name = "dc_short"
    allowed_directions: list[Direction] = ["short"]

    def __init__(self, *, config: Mapping[str, Any] | None = None) -> None:
        settings = dict(config) if config is not None else load_config()
        self._rules = DcRules.from_config(settings)
        # Canlıda modele `data.history_bars` bar verilir; bu sayı görüş penceresinden kısa
        # olsaydı canlı model backtest'tekinden DAR bir pencere görürdü ve TADİLAT-1'in
        # (§6i) kurduğu canlı = backtest eşitliği sessizce bozulurdu.
        history_bars = int(get_setting(settings, "data.history_bars"))
        if history_bars < self._rules.lookback_bars:
            raise ValueError(
                f"{self.name}: data.history_bars ({history_bars}) dc.lookback_bars'tan "
                f"({self._rules.lookback_bars}) kısa olamaz (docs/backtest.md > 6i > TADİLAT-1)"
            )
        self._survey: dict[str, int] | None = None

    # ------------------------------------------------------------------ #
    # Alt sınıfın TEK override noktası
    # ------------------------------------------------------------------ #
    def orient(self, setup: DcSetup, market: MarketData) -> tuple[DcSetup, str | None]:
        """Kurulumun oynanacak yönü ve (varsa) denetim etiketi. ÖLÇÜLEN EKSEN BUDUR.

        Model kurulumu kaynağın yönüyle (short) oynar; kontrol burada yazı-tura atar.
        İkinci öğe `coin=` etiketinin değeridir — yalnızca kontrol doldurur.
        """
        return setup, None

    # ------------------------------------------------------------------ #
    # Giriş
    # ------------------------------------------------------------------ #
    def generate_signals(
        self,
        market: MarketData,
        peer_signals: Mapping[str, tuple[Signal, ...]] | None = None,
    ) -> list[Signal]:
        result = scan(market, self._rules)
        self._survey = dict(result.counts)
        signals: list[Signal] = []
        for found in result.setups:
            setup, coin = self.orient(found, market)
            signals.append(self._signal(setup, coin=coin))
        return signals

    def take_survey(self) -> Mapping[str, int] | None:
        """Son taramanın sayımı (§6i > 3); motor bar bazında toplar.

        Okununca SIFIRLANIR (`scalp_vol`un deseni): backtest'in sinyal kesiminden sonra
        `generate_signals` hiç çağrılmaz ama motor `take_survey`i yine okur — sıfırlanmayan
        bir sayım, kesimden sonraki her barda son taramayı yeniden sayar ve huniyi
        (§6i > 6) şişirirdi.
        """
        survey, self._survey = self._survey, None
        return survey

    def _signal(self, setup: DcSetup, *, coin: str | None) -> Signal:
        tags: dict[str, object] = {
            "arm": ARM,
            # Küme bootstrap'ının kimliği (§6i > 8): sembol + bu etiket = rejim kümesi.
            "regime": setup.cross_bar.isoformat(),
            # Hedef-R dağılımının kaynağı (§6i > 13). Kurulum kapanışından ölçülür.
            "rr": f"{setup.reward_risk:.4f}",
        }
        if coin is not None:
            tags["coin"] = coin
        text = (
            f"ölüm kesişimi {setup.cross_bar:%Y-%m-%d %H:%M} rejiminde EMA50 reddi; "
            f"kapanış {setup.close:.6g}, stop {setup.stop_price:.6g}, hedef {setup.target_price:.6g}"
        )
        return Signal(
            symbol=setup.symbol,
            direction=setup.direction,
            stop_price=setup.stop_price,
            # TEK dilim: kesirli hedef pozisyonu iki ölçüm satırına bölerdi (§6i > 3).
            take_profits=(TakeProfit(price=setup.target_price, fraction=1.0),),
            reason=format_tags(text, **tags),
        )

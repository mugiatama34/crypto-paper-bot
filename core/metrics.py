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

**Maliyet ölçeği kolonları zorunludur** (CLAUDE.md > Rapor Kolonları): `avg_stop_distance_pct`
ve `cost_per_r`. Bunlar projenin ana sorusunu doğrudan kirleten etkiyi ölçer — "short modeller
daha iyi" sonucu sinyalden mi geliyor, yoksa short modellerin daha geniş stop kullanıp R başına
daha az maliyet ödemesinden mi? Boyut `risk / |giriş − stop|` olduğu için dar stop kuran model
aynı 1R'yi daha büyük notional ile taşır ve R başına daha çok komisyon+kayma öder. İki modelin
`avg_stop_distance_pct` değerleri banda göre ayrışıyor ve `cost_per_r` farkı performans farkını
tek başına açıklayabiliyorsa, kıyas geçersiz sayılır.

**Referans (benchmark) modeller yarışmacı değildir** (CLAUDE.md kural 15). Stop'u olmayan
bir alım-tut çıpasının 1R'si yoktur; `avg_stop_distance_pct` ve `cost_per_r` onun için
tanımsızdır ve `nan` raporlanır. Tabloda ortalama R sıralamasına da girmez, ayrı bir
"REFERANS" bölümünde durur: aynı sütunda sıralamak, farklı boyutlandırma kuralıyla
(fraction × sermaye, 1x) çalışan bir satırı risk-birimi yarışının parçasıymış gibi
gösterirdi. Referansın işi sıralamada yer almak değil, yarışmacıların hesap düzeyi
getirisine bir zemin vermek: "model piyasayı yendi mi?"

Tanımsız bir metrik (işlem yok, varyans sıfır) `nan` döner; 0.0 döndürmek "ölçüldü ve
sıfır çıktı" ile "ölçülemedi"yi aynı sayıya indirger ve karşılaştırmayı sessizce bozar.
Hiç short açmamış bir modelin `cost_per_r`'si 0.0 olsaydı, "maliyetsiz short yapan model"
gibi görünür ve model ortalamalarını aşağı çekerdi.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import Any, Callable, Collection, Iterable, Mapping, Sequence

import pandas as pd

from core.config import get_setting
from core.data import bar_duration
from core.ledger import Ledger
from core.tags import parse_tag
from strategies.base import Direction

logger = logging.getLogger(__name__)

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
    avg_stop_distance_pct: float
    cost_per_r: float
    pnl: float
    fees: float
    slippage_cost: float
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
    is_benchmark: bool = False  # kural 15: yarışmacı değil, referans çıpası

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


def stop_distance_pct(row: Mapping[str, Any]) -> float | None:
    """`|giriş − ilk stop| / giriş` (yüzde). Modelin hangi R ölçeğinde işlem yaptığını gösterir."""
    entry = _to_float(row.get("entry_price"))
    stop = _to_float(row.get("stop_price"))
    if entry is None or stop is None or entry <= 0.0:
        return None
    return abs(entry - stop) / entry * 100.0


def cost_per_r(row: Mapping[str, Any]) -> float | None:
    """İşlemin tüm dolumlarında ödenen komisyon + kaymanın `risk_amount`'a oranı.

    Payda her zaman İLK stop'tan gelen `risk_amount`'tır, trailing ile güncellenen stop
    değil: R giriş anında üstlenilen risktir, trailing yalnızca kârı korur. Yürüyen stop'u
    kullanmak iyi giden işlemlerin paydasını sonradan küçültüp cost_per_r'yi şişirirdi —
    üstelik bu şişme trailing kullanan modellerde farklı olur, yani tam da kıyaslanmak
    istenen şeyi bozardı.
    """
    risk = _to_float(row.get("risk_amount"))
    if risk is None or risk <= 0.0:
        return None
    fee = _to_float(row.get("fee")) or 0.0
    slippage = _to_float(row.get("slippage_cost")) or 0.0
    return (fee + slippage) / risk


def direction_stats(
    trades: Iterable[Mapping[str, Any]],
    *,
    direction: str = TOTAL,
    is_benchmark: bool = False,
) -> DirectionStats:
    """`direction` ("long" | "short" | "total") için işlem metrikleri.

    `is_benchmark=True` iken maliyet ölçeği kolonları koşulsuz `nan` olur (kural 15).
    Stop'suz işlemde bu değerler zaten hesaplanamaz, ama garantiyi defterin içeriğine
    bırakmak kırılgan olurdu: referans modelin defterine bir gün stop'lu bir satır girerse
    (elle düzeltme, şema göçü) tablo sessizce onu yarışmacı bir maliyet ölçeği gibi
    gösterirdi.
    """
    # Kapanış sırası: yön bazlı R-Sharpe, o yöndeki işlemlerin kapanış sırasına göre dizilmiş
    # R dizisinden hesaplanır (CLAUDE.md > Rapor Kolonları). Defter zaten bu sırada yazılır;
    # sıralama, satırların başka bir yoldan gelmesi hâlinde de garantiyi gerçek kılar.
    rows = sorted(
        (
            row
            for row in trades
            if direction == TOTAL or str(row.get("direction", "")) == direction
        ),
        key=lambda row: str(row.get("closed_at", "")),
    )
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
        avg_stop_distance_pct=(
            _NAN if is_benchmark else _mean(_collect(rows, stop_distance_pct))
        ),
        cost_per_r=_NAN if is_benchmark else _mean(_collect(rows, cost_per_r)),
        pnl=_sum_column(rows, "pnl"),
        fees=_sum_column(rows, "fee"),
        slippage_cost=_sum_column(rows, "slippage_cost"),
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
    is_benchmark: bool = False,
) -> ModelMetrics:
    return ModelMetrics(
        model=model,
        long=direction_stats(trades, direction="long", is_benchmark=is_benchmark),
        short=direction_stats(trades, direction="short", is_benchmark=is_benchmark),
        total=direction_stats(trades, direction=TOTAL, is_benchmark=is_benchmark),
        account=account_stats(
            equity_rows, initial_capital=initial_capital, periods_per_year=periods_per_year
        ),
        is_benchmark=is_benchmark,
    )


def compare(
    models: Sequence[str],
    *,
    ledger: Ledger | None = None,
    config: Mapping[str, Any],
    benchmarks: Collection[str] = (),
) -> list[ModelMetrics]:
    """Defterleri okuyup her model için metrikleri üretir. Defter değiştirilmez.

    `benchmarks` referans modellerin adlarıdır (kural 15). Bilgi stratejinin
    `is_benchmark` alanından gelir ve buraya çağıran tarafından taşınır: metrics defteri
    okur, strateji sınıflarını değil — `strategies/` importu, salt okunur bir metrik
    modülünü tüm model koduna bağlardı.
    """
    active_ledger = ledger if ledger is not None else Ledger()
    config_dict = dict(config)
    initial_capital = float(get_setting(config_dict, "initial_capital"))
    per_year = periods_per_year(config_dict)
    benchmark_names = set(benchmarks)
    return [
        model_metrics(
            model,
            trades=active_ledger.read_trades(model),
            equity_rows=active_ledger.read_equity(model),
            initial_capital=initial_capital,
            periods_per_year=per_year,
            is_benchmark=model in benchmark_names,
        )
        for model in models
    ]


# --------------------------------------------------------------------------- #
# Kırılımlar (kol / sembol)
# --------------------------------------------------------------------------- #
def breakdown(
    trades: Iterable[Mapping[str, Any]], *, key: Callable[[Mapping[str, Any]], str]
) -> dict[str, DirectionStats]:
    """İşlemleri `key`e göre gruplayıp her grup için aynı metrikleri hesaplar.

    Grup ölçütü dışarıdan gelir: bu modül defteri okur, modelleri değil (bkz. `compare`).
    Kol kırılımı `arm_of`, sembol kırılımı `symbol_of` ile kurulur; ikisi de satırın
    kendi alanlarından türer.

    Her grup TOPLAM (yön ayrımsız) raporlanır. Grup × yön kırılımı JSON'u üç katına
    çıkarırdı ve yön sorusunun cevabı zaten model tablosunda ve havuz panelinde durur;
    kırılımın cevapladığı soru farklıdır: "bu kol/sembol ölçülebilir bir şey üretti mi".

    `key` bir satırda hata fırlatırsa hata YUTULMAZ (bkz. `arm_of`): eksik etiketi olan
    satırı gruptan düşürmek, kırılım toplamı ile model toplamını sessizce ayrıştırırdı.
    """
    grouped: dict[str, list[Mapping[str, Any]]] = {}
    for row in trades:
        grouped.setdefault(key(row), []).append(row)
    return {
        group: direction_stats(rows, direction=TOTAL)
        for group, rows in sorted(grouped.items())
    }


def arm_of(row: Mapping[str, Any]) -> str:
    """İşlemin kolu: `signal_reason` kuyruğundaki `arm=` etiketi.

    Etiket yoksa `TagError` (bkz. core/tags.py): kol kırılımının anlamı "her işlem bir
    kola aittir" varsayımına dayanır ve etiketsiz satırı sessizce atlamak o varsayımı
    denetlenemez kılardı.
    """
    return parse_tag(str(row.get("signal_reason", "")), "arm")


def symbol_of(row: Mapping[str, Any]) -> str:
    return str(row.get("symbol", ""))


# --------------------------------------------------------------------------- #
# Havuzlanmış yön karşılaştırması (projenin ana sorusu)
# --------------------------------------------------------------------------- #
def pooled_direction_stats(
    trades_by_model: Mapping[str, Sequence[Mapping[str, Any]]],
) -> dict[str, DirectionStats]:
    """Birden çok modelin işlemlerini TEK havuzda birleştirip yön bazında ölçer.

    Model tablosu "hangi model iyi" sorusunu cevaplar; bu havuz projenin asıl sorusunu:
    **short işlemler long işlemlerden daha mı başarılı?** Model başına ortalama R'lerin
    ortalamasını almak bu soruya yanlış cevap verirdi — 2 işlemlik bir model 200 işlemlik
    bir modelle eşit ağırlık alır ve sonuç, işlemlerin değil model sayısının ortalaması
    olurdu. Havuz her işleme bir oy verir.

    Havuza kimin gireceğine çağıran karar verir: referans çıpalarının (kural 15) R'si
    yoktur, havuzda işleri de yoktur. Burada filtre uygulanmaz ki modül defterin
    içeriğinden başka bir şey varsaymasın.
    """
    rows = [row for trades in trades_by_model.values() for row in trades]
    return {
        direction: direction_stats(rows, direction=direction)
        for direction in (*DIRECTIONS, TOTAL)
    }


# --------------------------------------------------------------------------- #
# Kabul çıtası (iki kapı + bir uyarı)
# --------------------------------------------------------------------------- #
@dataclass(frozen=True, kw_only=True)
class AcceptanceFlags:
    """Bir yarışmacının sonucu: İKİ KAPI ve BİR UYARI.

    **Kapılar** (`passed` = ikisi birden):

    - `sample` — **örneklem**: R'ye giren kapanmış işlem sayısı eşiğin altındaysa
      ortalama R bir ölçüm değil gürültüdür. Bu kapı olmadan iki işlemle +3R yapmış bir
      model tablonun başına oturur.
    - `edge` — **üstünlük**: ortalama R pozitif, bilgisiz kontrol grubunun ortalama
      R'sini **en az `edge_margin_r` kadar** aşıyor ve hesap getirisi referans çıpasını
      geçiyor. Üçü birlikte tek bir soruyu sorar: "bu sonuç sinyalden mi geliyor, yoksa
      piyasadan ve şanstan mı?" Marj olmadan kontrolü 0.01R ile geçen bir model de
      "geçti" sayılırdı; oysa çekilişin kendi gürültüsü o kadar farkı tek başına üretir.

    **Uyarı** (`band`, `passed`'ı ETKİLEMEZ):

    - `band` — **maliyet ölçeği** (kural 14): modelin `avg_stop_distance_pct` değeri
      yarışmacı medyanının etrafındaki bantta mı? Dışındaysa model aynı 1R'yi belirgin
      biçimde farklı notional ile taşımış, yani R başına farklı maliyet ödemiştir.
      Bu bir KUSUR DEĞİL, bir kıyas koşuludur: modelin kendi ölçümü geçerlidir, ama
      başka bir modelle yan yana konurken `cost_per_r` farkının sonucu tek başına
      açıklayıp açıklamadığı sorulmalıdır (CLAUDE.md > Rapor Kolonları). Kapı yapmak
      iki ayrı soruyu birbirine karıştırırdı: "bu model doğrulandı mı" ile "bu model
      şu modelle kıyaslanabilir mi". Bu yüzden raporda bir uyarı göstergesidir.

    Kapılar YALNIZCA yarışmacılara uygulanır; referans çıpası yarışmacı değildir
    (kural 15), ona bir çıta koymak ölçmediği bir yarışta not vermek olurdu.
    """

    model: str
    sample: bool
    edge: bool
    passed: bool
    band: bool  # UYARI göstergesi: False = bandın dışında. `passed`'a girmez.
    measured_trades: int
    min_trades: int
    avg_stop_distance_pct: float
    band_low: float
    band_high: float
    avg_r: float
    control_avg_r: float
    edge_margin_r: float
    total_return: float
    benchmark_return: float


def acceptance_flags(
    metrics: Sequence[ModelMetrics],
    *,
    min_trades: int,
    stop_band_ratio: float,
    control_model: str,
    edge_margin_r: float,
) -> list[AcceptanceFlags]:
    """Her yarışmacı için iki kabul kapısı ve bir band uyarısı. Çıpalar listeye girmez.

    Bandın çapası yarışmacıların `avg_stop_distance_pct` MEDYANIDIR, sabit bir yüzde
    değil: kural 14'ün bandı ATR katı cinsindendir, defterde ise ATR yoktur (işlem
    kapandıktan sonra "o anki ATR" geri hesaplanamaz, geriye dönük yeniden hesaplamak da
    look-ahead kapısı açardı). Medyan, aynı evrende aynı barlarda işlem yapan modellerin
    ortak volatilite ölçeğini taşır; band `medyan/√oran .. medyan×√oran` olarak kurulur,
    yani uçtan uca tam `stop_band_ratio` kadar geniştir.

    Kontrol ya da referans modeli kümede yoksa ilgili koşul değerlendirilemez ve `edge`
    geri kalan koşullara düşer — ama bu sessiz olmaz, `logger.warning` ile söylenir:
    eksik bir çıta, geçilmiş bir çıta gibi görünmemelidir.
    """
    competitors = [item for item in metrics if not item.is_benchmark]
    band_low, band_high = _stop_band(competitors, ratio=stop_band_ratio)
    control_avg_r = _control_avg_r(metrics, control_model)
    benchmark_return = _benchmark_return(metrics)

    return [
        _flags_for(
            item,
            min_trades=int(min_trades),
            band_low=band_low,
            band_high=band_high,
            control_avg_r=control_avg_r,
            edge_margin_r=float(edge_margin_r),
            benchmark_return=benchmark_return,
        )
        for item in competitors
    ]


def _flags_for(
    item: ModelMetrics,
    *,
    min_trades: int,
    band_low: float,
    band_high: float,
    control_avg_r: float,
    edge_margin_r: float,
    benchmark_return: float,
) -> AcceptanceFlags:
    measured = item.total.trades - item.total.unmeasured
    avg_r = item.total.avg_r
    stop_distance = item.total.avg_stop_distance_pct
    total_return = item.account.total_return

    sample = measured >= min_trades
    # Ölçülemeyen band (tek yarışmacı, hiç stop'lu işlem yok) uyarı üretmez: gösterge
    # ancak kıyaslanacak bir medyan varken anlamlıdır.
    band = (
        True
        if math.isnan(band_low) or math.isnan(stop_distance)
        else band_low <= stop_distance <= band_high
    )
    edge = (
        not math.isnan(avg_r)
        and avg_r > 0.0
        # Marj, kontrolü ANLAMLI biçimde geçmiş modeli kıl payı önde olandan ayırır:
        # bilgisiz çekilişin kendi gürültüsü 0.01R'lik bir farkı tek başına üretebilir.
        and (math.isnan(control_avg_r) or avg_r - control_avg_r >= edge_margin_r)
        and (math.isnan(benchmark_return) or (
            not math.isnan(total_return) and total_return > benchmark_return
        ))
    )
    return AcceptanceFlags(
        model=item.model,
        sample=sample,
        edge=edge,
        # Band bilinçli olarak DIŞARIDA: kıyas koşuludur, kalite kapısı değil.
        passed=sample and edge,
        band=band,
        measured_trades=measured,
        min_trades=min_trades,
        avg_stop_distance_pct=stop_distance,
        band_low=band_low,
        band_high=band_high,
        avg_r=avg_r,
        control_avg_r=control_avg_r,
        edge_margin_r=edge_margin_r,
        total_return=total_return,
        benchmark_return=benchmark_return,
    )


def _stop_band(competitors: Sequence[ModelMetrics], *, ratio: float) -> tuple[float, float]:
    values = [
        item.total.avg_stop_distance_pct
        for item in competitors
        if not math.isnan(item.total.avg_stop_distance_pct)
    ]
    if not values or ratio <= 0.0:
        return (_NAN, _NAN)
    center = _median(values)
    half = math.sqrt(ratio)
    return (center / half, center * half)


def _control_avg_r(metrics: Sequence[ModelMetrics], control_model: str) -> float:
    for item in metrics:
        if item.model == control_model:
            return item.total.avg_r
    logger.warning(
        "kontrol grubu %r kümede yok: edge bayrağı 'kontrolü geçti mi' koşulunu "
        "değerlendiremiyor", control_model,
    )
    return _NAN


def _benchmark_return(metrics: Sequence[ModelMetrics]) -> float:
    """Çıpanın hesap getirisi. Birden fazla çıpa varsa EN YÜKSEĞİ alınır.

    Zemin en yüksek çıpadır: "piyasayı yendi mi" sorusuna, geçilmesi en kolay çıpayı
    seçerek cevap vermek çıtayı sessizce indirirdi.
    """
    returns = [
        item.account.total_return
        for item in metrics
        if item.is_benchmark and not math.isnan(item.account.total_return)
    ]
    if not returns:
        logger.warning(
            "kümede referans çıpası yok: edge bayrağı 'piyasayı geçti mi' koşulunu "
            "değerlendiremiyor (kural 15)",
        )
        return _NAN
    return max(returns)


# --------------------------------------------------------------------------- #
# Modeller arası getiri korelasyonu
# --------------------------------------------------------------------------- #
def return_correlation(
    equity_by_model: Mapping[str, Sequence[Mapping[str, Any]]],
    *,
    min_overlap: int = 3,
) -> dict[str, Any]:
    """Modellerin bar getirilerinin Pearson korelasyon matrisi.

    Neden ölçülüyor: on model aynı evreni aynı barlarda görüyor. Yüksek korelasyonla
    yarışan iki model bağımsız iki ölçüm değil, tek ölçümün iki kopyasıdır — tablonun
    ilk iki sırasını doldurmaları bir teyit değil, tekrardır. Matris, sıralamanın ne
    kadarının gerçekten farklı fikirlerden geldiğini gösterir.

    Kesişim PAR BAZINDA alınır, global değil: yarışmaya sonradan eklenen bir modelin kısa
    geçmişi, global kesişim kullanılsaydı TÜM çiftleri onun uzunluğuna kırpardı. Örtüşme
    `min_overlap`'in altındaysa hücre `nan`'dır (0.0 "ilişkisiz" demek olurdu); örneklem
    okunabilsin diye örtüşme sayıları da ayrıca döner.
    """
    models = sorted(equity_by_model)
    series = {model: _bar_returns(equity_by_model[model]) for model in models}

    matrix: list[list[float]] = []
    overlap: list[list[int]] = []
    for row_model in models:
        correlations: list[float] = []
        counts: list[int] = []
        for column_model in models:
            left, right = _align(series[row_model], series[column_model])
            counts.append(len(left))
            correlations.append(
                _pearson(left, right) if len(left) >= min_overlap else _NAN
            )
        matrix.append(correlations)
        overlap.append(counts)

    return {
        "models": models,
        "matrix": matrix,
        "overlap": overlap,
        "min_overlap": int(min_overlap),
    }


def _bar_returns(equity_rows: Sequence[Mapping[str, Any]]) -> dict[str, float]:
    """Zaman damgası -> o bardaki getiri. Damga anahtar, çünkü modeller farklı barda başlar."""
    points = [
        (str(row.get("ts", "")), _to_float(row.get("equity")))
        for row in equity_rows
    ]
    points = [(ts, value) for ts, value in points if ts and value is not None]
    points.sort(key=lambda item: item[0])
    return {
        ts: points[index][1] / points[index - 1][1] - 1.0
        for index, (ts, _) in enumerate(points)
        if index > 0 and points[index - 1][1] > 0.0
    }


def _align(
    left: Mapping[str, float], right: Mapping[str, float]
) -> tuple[list[float], list[float]]:
    shared = sorted(set(left) & set(right))
    return ([left[ts] for ts in shared], [right[ts] for ts in shared])


def _pearson(left: Sequence[float], right: Sequence[float]) -> float:
    """Sabit bir seri (varyans 0) için `nan`: sabitle korelasyon tanımsızdır, 0 değil."""
    mean_left = _mean(left)
    mean_right = _mean(right)
    covariance = sum(
        (a - mean_left) * (b - mean_right) for a, b in zip(left, right)
    )
    spread_left = math.sqrt(sum((a - mean_left) ** 2 for a in left))
    spread_right = math.sqrt(sum((b - mean_right) ** 2 for b in right))
    if spread_left == 0.0 or spread_right == 0.0:
        return _NAN
    return covariance / (spread_left * spread_right)


# --------------------------------------------------------------------------- #
# Rapor
# --------------------------------------------------------------------------- #
_HEADERS = ("model", "yön", "n", "ort.R", "medyan R", "topl.R", "R-Sharpe",
            "maxDD(R)", "kazanç%", "PF", "stopMes.%", "maliyet/R", "PnL(USDT)", "likid.")
_WIDTHS = (18, 6, 5, 8, 9, 8, 9, 9, 8, 7, 10, 10, 12, 7)
_TABLE_WIDTH = sum(_WIDTHS) + 2 * (len(_WIDTHS) - 1)


def format_report(metrics: Sequence[ModelMetrics]) -> str:
    """Karşılaştırma tablosu. Kolon sırası bilinçlidir: önce R, sonra USDT getirisi.

    Sıralama ortalama R'ye göredir — tabloyu toplam getiriye göre sıralamak, tam da
    ayıklamaya çalıştığımız bileşiklenme etkisini geri sokardı.

    Referans modeller (kural 15) sıralamaya girmez, tablonun altında ayrı bir bölümde
    durur: farklı boyutlandırma kuralıyla çalışan bir satırı yarışmacılarla aynı sütunda
    sıralamak, okuyucuya olmayan bir kıyas sunardı.
    """
    competitors = [item for item in metrics if not item.is_benchmark]
    references = [item for item in metrics if item.is_benchmark]

    lines = [
        "  ".join(header.rjust(width) if index else header.ljust(width)
                  for index, (header, width) in enumerate(zip(_HEADERS, _WIDTHS))),
        "-" * _TABLE_WIDTH,
    ]
    for item in sorted(competitors, key=_rank_key):
        lines.extend(_model_block(item))

    if references:
        lines.append("REFERANS (yarışma dışı, kural 15) — kıyas zemini: model piyasayı yendi mi?")
        lines.append("-" * _TABLE_WIDTH)
        for item in sorted(references, key=lambda entry: entry.model):
            lines.extend(_model_block(item))

    return "\n".join(lines).rstrip() + "\n"


def _rank_key(item: ModelMetrics) -> tuple[float, str]:
    """Ortalama R'si olmayan model sıralamanın sonuna düşer, başına değil."""
    avg_r = item.total.avg_r
    return (-avg_r if not math.isnan(avg_r) else math.inf, item.model)


def _model_block(item: ModelMetrics) -> list[str]:
    lines: list[str] = []
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
            _fmt(stats.avg_stop_distance_pct).rjust(_WIDTHS[10]),
            _fmt(stats.cost_per_r, digits=3).rjust(_WIDTHS[11]),
            _fmt(stats.pnl, digits=2).rjust(_WIDTHS[12]),
            str(stats.liquidations).rjust(_WIDTHS[13]),
        )
        lines.append("  ".join(cells))

    account = item.account
    lines.append(
        f"{'':<{_WIDTHS[0]}}  hesap: son özsermaye {_fmt(account.final_equity, digits=2)} | "
        f"getiri {_fmt(_pct(account.total_return), digits=2)}% | "
        f"maxDD {_fmt(_pct(account.max_drawdown), digits=2)}% | "
        f"Sharpe {_fmt(account.sharpe)} | {account.bars} bar"
    )
    # Referansta R'siz satır beklenendir (stop yok), yarışmacıda ise denetlenmesi gereken
    # bir anomalidir: uyarıyı ikisine de yazmak, gerçek uyarıyı gürültüye boğardı.
    unmeasured = item.total.unmeasured
    if unmeasured and not item.is_benchmark:
        lines.append(
            f"{'':<{_WIDTHS[0]}}  UYARI: {unmeasured} işlemde risk_amount yok, R'ye girmedi"
        )
    lines.append("")
    return lines


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


def _collect(
    rows: Iterable[Mapping[str, Any]], extract: Any
) -> list[float]:
    """Hesaplanabilen değerleri toplar; hesaplanamayanlar ortalamaya 0.0 olarak girmez."""
    return [value for value in (extract(row) for row in rows) if value is not None]


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

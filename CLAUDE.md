# CLAUDE.md

## Amaç

Bu proje, 10 farklı strateji modelinin **aynı piyasa verisini** görüp kendi izole sanal
hesabıyla paper-trading yaptığı ve performanslarının objektif biçimde karşılaştırıldığı bir
**ölçüm projesidir.** Kâr üretmek hedef değildir — hedef, stratejileri adil, tekrarlanabilir
ve manipüle edilemez koşullarda kıyaslamaktır. Bu ilke tüm tasarım kararlarına önceliklidir:
bir değişiklik ölçümün adilliğini bozuyorsa, ne kadar "daha iyi trading" sağlarsa sağlasın
reddedilir.

Projenin cevaplamaya çalıştığı ana soru: **short işlemler long işlemlerden daha mı
başarılı?** Bu yüzden long/short ayrımı raporlamanın merkezindedir (bkz. `core/metrics.py`).

## Klasör Yapısı ve Modül Sorumlulukları

| Yol | Tek Sorumluluk |
|---|---|
| `config.yaml` | Evrensel ayarlar: sembol evreni, zaman dilimi, başlangıç bakiyesi, çalışma sıklığı, funding parametreleri ve tüm risk/maliyet sabitleri — `risk_per_trade`, `leverage_cap`, `max_positions`, `max_short_positions`, `fee_rate`, `slippage_long`, `slippage_short_stop`, `maintenance_margin`, `random_seed`. Tüm modeller için tek kaynak; hiçbir modül bu değerlerin kendi kopyasını taşımaz. Değerler için bkz. "config.yaml Değerleri". |
| `core/config.py` | `config.yaml`'ı okuyan tek kapı. Eksik anahtarda `ConfigError` fırlatır; hiçbir varsayılan değer taşımaz — sessiz varsayılan, modellerin farklı maliyet/risk varsayımlarıyla yarışması demektir. |
| `core/data.py` | Piyasa verisi çekme/önbellekleme. Borsadan OHLCV + funding geçmişini çeker, `MarketData` üretir. Kapanmamış barı atmak (kural 12) ve `as_of`'u BTC referansından belirlemek burasının işidir; `as_of` barına sahip olmayan semboller o tur dışlanır ve loglanır. Strateji mantığı barındırmaz. |
| `core/engine.py` | Orkestrasyon: her turu **iki geçişli** yürütür — önce normal modeller, sonra meta modeller (kural 4) — ürettikleri `Signal` listelerini `portfolio`'ya iletir. Trailing stop mantığı da burada. Zamanlama/akış kontrolü burada, iş mantığı değil. |
| `core/portfolio.py` | Pozisyon açma/kapama, boyutlandırma, **likidasyon kontrolü**, stop/TP tetikleme, bakiye güncelleme. Her strateji için izole hesap durumu tutar. Pozisyon boyutlandırmasının **tek yetkili kaynağı.** Her barda sıra: önce `maintenance_margin` ile likidasyon kontrolü (mum içi `high`/`low` kullanılarak), **sonra** stop/TP kontrolü. Likidasyon stop'tan önce gelir; likide olan pozisyon stop'a hiç ulaşmaz. |
| `core/funding.py` | Açık pozisyonlara funding/borrow maliyeti uygular. Borsa kurallarını simüle eder. |
| `core/metrics.py` | PnL, Sharpe, max drawdown, win-rate vb. performans metriklerini hesaplar. **Her metrik long ve short işlemler için AYRI hesaplanır ve ayrı raporlanır** (toplam değer de verilir, ama ayrışma yerine geçmez). Projenin ana sorusu "short işlemler daha mı başarılı" olduğu için bu opsiyonel değil. Salt okunur — ledger'ı değiştirmez. |
| `core/ledger.py` | Her işlemi ve bakiye değişimini kalıcı, append-only biçimde `ledgers/` altına yazar. Sistemin denetim izi (audit trail) burasıdır. |
| `core/validate.py` | Her `Signal`in motora girmeden geçtiği tek doğrulama kapısı: izinli yön, stop/TP geometrisi, sıfıra bölme, fraction toplamı, sembol evreni. Geçersiz sinyalde `ValueError`/`NotImplementedError` fırlatır, sessizce filtrelemez. |
| `strategies/base.py` | Tüm stratejilerin uyacağı soyut arayüz (`Strategy`, `Signal`, `Position`, `ExitInstruction`, `MarketData`). Mantık içermez, yalnızca sözleşme. |
| `strategies/*.py` (ileride) | `Strategy`'den türeyen, yalnızca `generate_signals` uygulayan bağımsız, birbirinden habersiz modüller. |
| `data/` | Çalışma zamanı veri deposu (depoya girmez): `data/universe.json` ve `data/cache/<sembol>_<bar>.parquet`. Mum/funding önbelleği burada tutulur, her koşuda yalnızca eksik barlar çekilir. |
| `ledgers/` | Her stratejinin işlem/bakiye kayıtlarının tutulduğu çıktı klasörü (strateji başına dosya/alt klasör). |
| `docs/` | Tasarım kararları, metrik tanımları, kabul kriterleri. |
| `tests/` | Her `core` modülü ve her strateji için bağımsız birim testleri. |
| `.github/workflows/run.yml` | Periyodik çalıştırma (cron) ve CI'da test doğrulaması. |

### config.yaml Değerleri

| Anahtar | Değer | Anlamı |
|---|---|---|
| `risk_per_trade` | `0.01` | İşlem başına riske atılan sermaye oranı (%1). Boyutlandırma formülünün payı. |
| `leverage_cap` | `5` | İzin verilen azami kaldıraç. Hiçbir koşulda aşılmaz. |
| `max_positions` | `5` | Bir stratejinin aynı anda taşıyabileceği toplam pozisyon sayısı. |
| `max_short_positions` | `3` | Bunların en fazla kaçının short olabileceği. |
| `fee_rate` | `0.001` | Tek yön komisyon oranı; giriş ve çıkışta ayrı ayrı uygulanır. |
| `slippage_long` | `0.0005` | Long girişlerde/çıkışlarda uygulanan kayma. |
| `slippage_short_stop` | `0.0015` | Short pozisyonların stop dolumunda uygulanan kayma (short stop'lar yukarı boşluklarda daha kötü dolar). |
| `maintenance_margin` | `0.005` | Likidasyon eşiği. Pozisyonun mum içi zararı bu seviyeyi geçerse likide edilir. |
| `random_seed` | (sabit tam sayı) | Rastgelelik kullanan her yol bu tohumdan beslenir; koşular tekrarlanabilir olmalıdır. |
| `initial_capital` | `10000` | Her stratejinin izole sanal hesabının başlangıç bakiyesi (USDT). |
| `timeframe` | `"4H"` | Tek zaman dilimi; OKX bar kodu ve bar süresi bundan türetilir. |
| `universe_size` | `50` | 24s hacme göre seçilen USDT perpetual sayısı. |
| `universe_refresh_days` | `30` | Evren bu süre dolmadan yeniden hesaplanmaz (kıyas kümesi sabit kalsın). |
| `funding.*` | `enabled`, `interval_hours` | Funding simülasyonunun açık/kapalı olması ve periyodu. |
| `exchange.*` | OKX erişimi | `rest_base`, `inst_type`, `quote_ccy`, `btc_reference`, istek limitleri, timeout, throttle ve retry/backoff sabitleri. |
| `data.*` | yerel depo | `cache_dir`, `universe_file`, `history_bars`, `funding_history_periods`, `max_staleness_bars` (BTC çıpasının azami bayatlığı). |

## Değişmez Kurallar

1. Hiçbir strateji kendi defterine (ledger) doğrudan yazamaz. Strateji yalnızca `Signal`
   üretir; yazma işlemi merkezi olarak `core/ledger.py` üzerinden yapılır.
2. Hiçbir strateji komisyon, funding veya borçlanma maliyeti hesaplamaz. Bunlar
   `core/funding.py` ve `core/portfolio.py` içinde, tüm modeller için birebir aynı kurallarla
   uygulanır.
3. Hiçbir strateji pozisyon boyutu (miktar/USD tutarı) belirlemez. Boyutlandırma
   `core/portfolio.py`'de tanımlı ortak bir kurala göre yapılır (bkz. kural 11); strateji
   yalnızca yön ve giriş/çıkış seviyelerini önerir.
4. Stratejiler birbirinin verisine, pozisyonuna veya iç durumuna erişemez; her biri tam
   izolasyonla çalışır. **Tek istisna — meta-stratejiler:** `is_meta = True` ile işaretlenen
   bir model, diğer modellerin **o turdaki sinyallerini** salt okunur bir kopya olarak alır.
   Bunun için `core/engine.py` her turu iki geçişte yürütür: önce normal modeller (kendi
   aralarında izole), sonra meta modeller. Meta modeller **birbirinin** sinyallerini okuyamaz
   — meta geçişine giren sinyal kümesi yalnızca normal modellerin çıktısıdır ve her meta
   model için aynıdır. Meta modeller yine de birbirinin pozisyonuna, bakiyesine veya iç
   durumuna erişemez; istisna yalnızca "o turda üretilmiş sinyaller" ile sınırlıdır.
5. Tüm stratejiler aynı `MarketData` anlık görüntüsünü görür; hiçbiri farklı veya gecikmeli
   veri kullanamaz.
6. Karşılaştırma adil olmalıdır: başlangıç bakiyesi, komisyon oranı ve izin verilen sembol
   evreni tüm modeller için birebir aynıdır.
7. `core/` dışındaki hiçbir modül bakiye, komisyon veya pozisyon durumunu tutmaz/hesaplamaz.
8. `allowed_directions` ihlali veya geçersiz sinyal geometrisi (stop yanlış tarafta,
   `stop_price == entry_price`, take-profit yönü tutarsız, fraction toplamı > 1.0, evren
   dışı sembol) bir piyasa durumu değil, programlama hatasıdır — `core/validate.py`
   `ValueError` ile reddeder; sessizce filtrelenmez. Bir modelin sinyali bu kapıdan
   geçemezse yalnızca o model atlanır, koşu durmaz.
9. Trailing stop mantığı stratejide değil `core/engine.py`'de uygulanır. Strateji yalnızca
   `Signal.trailing_atr` ile isteğini bildirir.
10. Mevcut pozisyonlarda kapanış/kısmi çıkış kararı yalnızca `Strategy.manage_positions`
    üzerinden verilir; `generate_signals` yalnızca yeni pozisyon açılışı önerir. İkisi
    karıştırılmaz.
11. **Boyutlandırma kuralı** (tek yetkili uygulayıcı `core/portfolio.py`):

    ```
    boyut = (risk_per_trade × sermaye) / |giriş fiyatı − stop fiyatı|
    ```

    Kaldıraç bir hedef değil, bir sonuçtur: yalnızca gereken notional eldeki nakdi aşarsa
    devreye girer ve **hiçbir koşulda `leverage_cap`'i aşmaz.** Gereken kaldıraç
    `leverage_cap`'i aşacaksa pozisyon `leverage_cap`'e sığacak şekilde **küçültülür** —
    işlem atlanmaz. (Atlamak, geniş stop kullanan modellerin işlem sayısını sessizce
    düşürür ve tam da ölçmeye çalıştığımız karşılaştırmayı bozar.)
12. **Look-ahead yasağı:** `MarketData` yalnızca **kapanmış** barları içerir. Borsa API'si
    kapanmamış (oluşmakta olan) bir bar dönerse `core/data.py` bu barı atar; `as_of`
    BTC referans sembolünün son kapanmış barının zamanıdır ve stratejiler "şimdi"yi buradan
    okur.
    Bu kuralın ihlali tek bir modeli değil, **tüm sonuçları geçersiz kılar.**
13. **Dolum kuralı:** Sinyaller üretildikleri barda değil, **bir sonraki barın açılışından**
    dolar. Bir barın aralığında hem stop hem take-profit varsa, mum içi sıralama
    bilinemeyeceği için **kötü olan (stop) gerçekleşmiş varsayılır.** Aynı barda likidasyon
    seviyesi de dokunulmuşsa likidasyon her ikisinden de önce gelir (bkz. `core/portfolio.py`).

## Kod Stili

- Python 3.11+. Tüm fonksiyon ve metod imzalarında type hint zorunludur.
- Harici bağımlılık minimumda tutulur: `requests`, `pandas`, `numpy`, `pyyaml` dışında yeni
  bağımlılık eklenmeden önce gerekçelendirilmelidir.
- Her modül, diğer `core` modülleri mock/stub'lanarak tek başına test edilebilir olmalıdır
  (gevşek bağlılık, açık ve dar arayüzler).
- Yorum yalnızca "neden" sorusuna cevap verdiğinde yazılır; kodun zaten söylediği "ne
  yaptığını" tekrar eden yorum yazılmaz.

## Strateji Arayüz Sözleşmesi (onaylandı — `strategies/base.py` içinde uygulandı)

```python
class Strategy(ABC):
    name: str
    allowed_directions: list[Direction]   # ["long"], ["short"] veya ["long", "short"]
    is_meta: bool = False                 # True ise engine'in ikinci geçişinde çalışır

    @abstractmethod
    def generate_signals(
        self,
        market: MarketData,
        peer_signals: Mapping[str, tuple[Signal, ...]] | None = None,
    ) -> list[Signal]:
        """Yeni pozisyon açılışı önerir. Mevcut pozisyonlara dokunmaz.

        peer_signals yalnızca is_meta=True modellere doldurulur: strateji adı ->
        o turda üretilmiş sinyallerin salt okunur kopyası. Normal modellerde None.
        """

    def manage_positions(
        self, market: MarketData, positions: list[Position]
    ) -> list[ExitInstruction]:
        """Mevcut pozisyonlarda kapanış/kısmi çıkış önerir. Varsayılan: hiçbir şey yapma."""
        return []


@dataclass(frozen=True, kw_only=True)
class TakeProfit:
    price: float
    fraction: float          # 0 < fraction <= 1.0; bir Signal içindeki toplam <= 1.0


@dataclass(frozen=True, kw_only=True)
class Signal:
    symbol: str
    direction: Direction               # "long" | "short"
    stop_price: float
    entry_type: Literal["market", "limit"] = "market"   # v1'de yalnızca "market" işlenir
    take_profits: tuple[TakeProfit, ...] = ()
    trailing_atr: float | None = None  # uygulaması core/engine.py'de, strateji yazmaz
    reason: str = ""                   # deftere yazılacak serbest metin


@dataclass(frozen=True, kw_only=True)
class Position:
    """manage_positions'a verilen salt okunur pozisyon görünümü."""
    symbol: str
    direction: Direction
    entry_price: float
    stop_price: float
    take_profits: tuple[TakeProfit, ...] = ()
    trailing_atr: float | None = None
    opened_at: pd.Timestamp


@dataclass(frozen=True, kw_only=True)
class ExitInstruction:
    symbol: str
    action: Literal["close", "reduce"]
    fraction: float = 1.0    # yalnızca action="reduce" için anlamlı
    reason: str = ""


@dataclass(frozen=True, kw_only=True)
class MarketData:
    ohlcv: dict[str, pd.DataFrame]     # sembol -> 4h OHLCV, yalnızca KAPANMIŞ barlar
    btc: pd.DataFrame                  # BTC referans verisi (aynı kural)
    funding: dict[str, pd.Series]      # sembol -> zaman indeksli funding geçmişi
    as_of: pd.Timestamp                # değerlendirilen son KAPANMIŞ barın zamanı
```

Tasarım kararları:

- **`entry_type`**: sözleşmede `"limit"` de yazılabilir, ama v1'de yalnızca `"market"`
  işlenir — `core/validate.py` `"limit"` gördüğünde `NotImplementedError` fırlatır. Limit
  emri desteklemek "bekleyen emirler", geçerlilik süresi ve mum-içi dokunma kontrolü
  gerektirir; bu da look-ahead hatası için yeni bir kapı açar. Alan sözleşmede duruyor,
  uygulaması ileri bir karar.
- **`funding` tek oran değil, seri**: bazı modeller son N periyodun funding trendine bakar
  (funding'in yönü ve hızlanması bir kalabalıklık göstergesidir). Tek bir `float` bu bilgiyi
  taşıyamadığı için alan zaman indeksli `pd.Series`'e çevrildi. Seri de kural 12'ye tabidir:
  yalnızca `as_of` ve öncesindeki funding değerlerini içerir.
- **`as_of` neden sözleşmede**: stratejinin "şimdi"yi `pd.Timestamp.now()` ile ya da
  DataFrame'in son satırından tahmin ederek okuması, look-ahead hatasının en sık kapısıdır.
  Tek ve açık bir "şimdi" tanımı, kural 12'yi test edilebilir kılar.
- **Kapanış sinyali `generate_signals` üzerinden değil**: `generate_signals` sadece açılış,
  `manage_positions` sadece kapanış/kısmi çıkış. Engine'in "bu sinyal yeni pozisyon mu,
  mevcut pozisyona müdahale mi" diye tahmin yürütmesi gerekmez.
- **Meta-stratejiler ve iki geçişli tur**: bir modelin diğerlerinin sinyallerini okuması
  (konsensüs sayma, aykırı gitme, sinyal filtreleme) ölçmeye değer bir strateji sınıfıdır,
  ama izolasyonu kırma riski taşır. Bu yüzden istisna dar tutuldu: meta model **yalnızca o
  turun sinyallerini**, **yalnızca kopya olarak** görür; pozisyon/bakiye/iç duruma erişimi
  yoktur ve **başka meta modelleri göremez.** Meta'ların birbirini okuması sıralamaya bağlı
  sonuç (kim önce çalıştıysa avantajlı) üretirdi — bu da adil karşılaştırmayı bozar.
  `peer_signals` derin kopya olarak verilir: `Signal` frozen olsa da `take_profits` listesi
  değiştirilebilir olduğundan, kopyalamadan paylaşmak bir modelin diğerinin sinyalini
  bozmasına izin verirdi.
- **`take_profits` tuple, liste değil**: `Signal` ve `Position` `frozen=True` ama mutable bir
  liste taşıdıklarında dondurma yarım kalır — bir strateji (ya da `peer_signals` okuyan bir meta
  model) kendi veya başkasının sinyalinin hedeflerini yerinde değiştirebilirdi. Tuple, sözleşmeyi
  gerçekten salt okunur yapar ve `peer_signals` kopyalamasının garantisini tamamlar.
- **`allowed_directions` ihlali → `ValueError`**: bu bir piyasa durumu değil, programlama
  hatasıdır. Sessiz filtreleme, short-only bir modelin aylarca yarı yarıya az işlem yapıp
  bunu kimsenin fark etmemesi demek — tam da ölçmeye çalıştığımız şeyi bozar. CI/çalıştırma
  workflow'u modeli bazında hatayı yakalayıp yalnızca o modeli atlar, koşu durmaz.
- **`core/validate.py`**: yukarıdaki doğrulamayı (ve stop==entry sıfıra bölme, stop yanlış
  taraf, TP yönü tutarsız, fraction toplamı > 1.0, evren dışı sembol kontrollerini) tek yere
  toplar. Her strateji sinyali motora girmeden bu kapıdan geçer.
- **Likidasyon stop'tan önce**: gerçek borsada bakım marjı ihlali stop emrini beklemez.
  Kontrolü stop'tan sonra yapmak, yüksek kaldıraçlı modellere gerçekte var olmayan bir
  kurtulma şansı verir ve tam da kıyaslamak istediğimiz risk farkını gizler. Kontrol mum içi
  `high`/`low` ile yapılır; kapanış fiyatıyla yapmak aynı hatanın daha yumuşak hâlidir.
- **`as_of` sabit bir çıpaya bağlıdır — BTC**: `core/data.py` anlık görüntüyü kurarken
  `as_of`'u `exchange.btc_reference` sembolünün son kapanmış barından okur. Sembollerin
  **ortak** (minimum) barını kullanmak iki sorun üretiyordu: (a) döngüsel bağımlılık — bayat
  sembolü dışlamak için `as_of`, `as_of` için sembol listesi gerekiyordu; (b) tek bir gecikmiş
  sembol turun "şimdi"sini bir bar geri çekiyordu, bu da zaten işlenmiş bir barı tekrar işlemek
  (çift işlem) ya da turun hiç ilerlememesi (donmuş sistem) demekti. BTC hem her modelin rejim
  filtresinde referans hem de en likit ve en az gecikecek sembol. `as_of` barına sahip olmayan
  semboller **o tur dışlanır ve loglanır** — hangi turda kaç sembolün görülebildiği sonradan
  denetlenebilsin diye. `data.max_staleness_bars` artık sembol başına tolerans değil, çıpanın
  kendi tazeliğinin sınırıdır: BTC verisi bundan daha geride kalmışsa anlık görüntü hiç
  üretilmez (bayat veriyle işlem açmaktansa tur düşer).
- **Long/short metriklerinin ayrılması**: projenin ana sorusu short işlemlerin görece
  başarısı olduğu için birleşik bir Sharpe ya da win-rate cevabı vermez. Ayrıştırma
  raporlamanın varsayılanıdır, ek bir seçenek değil.

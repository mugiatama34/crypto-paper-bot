# Tasarım Kararları

Bu dosya, projenin ilerleyişi sırasında alınan önemli tasarım kararlarını ve gerekçelerini
kayıt altına alır.

## 1. Proje sözleşmesi ve klasör iskeleti

`CLAUDE.md` ve boş klasör iskeleti oluşturuldu. Strateji arayüzü henüz onaylanmamıştı.

## 2. Strateji arayüzü onaylandı: açılış/kapanış ayrımı, doğrulama kapısı

- `entry_type` alanı `"market" | "limit"` olarak tanımlı ama v1'de yalnızca `"market"`
  işlenir; `"limit"` `core/validate.py`'de `NotImplementedError` fırlatır. Limit emri
  desteklemek bekleyen emir/geçerlilik süresi/mum-içi dokunma kavramları gerektirir ve
  look-ahead hatası riski taşır — kapsam dışı bırakıldı, alan sözleşmede korundu.
- Kapanış/kısmi çıkış kararı ayrı bir metodla verilir: `Strategy.manage_positions(market,
  positions) -> list[ExitInstruction]`, varsayılan implementasyon boş liste döner.
  `generate_signals` yalnızca açılış önerir. Bu ayrım, engine'in sinyal niyetini tahmin
  etmesini gereksiz kılar.
- `trailing_atr` alanı `Signal`'a eklendi ama uygulaması strateji değil `core/engine.py`
  sorumluluğunda — tekrarlanan trailing mantığının 10 modelde ayrı ayrı yazılmasının önüne
  geçer.
- Tek hedefli `take_profit: float | None` yerine `take_profits: list[TakeProfit]`
  (`price`, `fraction`) kullanılır — kısmi çıkış yapan stratejiler için özel durum
  gerektirmez.
- `allowed_directions` ihlali veya geçersiz sinyal geometrisi sessizce filtrelenmez,
  `ValueError`/`NotImplementedError` fırlatılır — bu doğrulama `core/validate.py`'de tek
  yerde toplanır (stop==entry sıfıra bölme, stop yanlış taraf, TP yönü tutarsızlığı,
  fraction toplamı > 1.0, evren dışı sembol). Sessiz filtreleme bir modelin performansını
  fark edilmeden bozabileceği için "ölçüm projesi" ilkesiyle çelişir.

`strategies/base.py` ve `core/validate.py` bu kararlarla uygulandı, testler
`tests/test_base.py` ve `tests/test_validate.py`'de.

## 3. config.yaml şeması netleşti, veri katmanı (OKX) uygulandı

- **`config.yaml` CLAUDE.md şemasına göre yeniden yazıldı.** Eski taslaktaki
  `commission_rate: 0.0004` ve `account`/`universe`/`schedule` grupları kaldırıldı; tek
  kaynak artık düz anahtarlardan oluşuyor. Komisyon `fee_rate: 0.001` (OKX **taker**):
  modeller `entry_type="market"` ile giriyor, maker oranı kullanmak hiç gerçekleşmeyen bir
  dolumu varsayıp tüm sonuçları optimist gösterirdi.
- **`core/config.py` eklendi** (CLAUDE.md modül tablosuna da yazıldı): config'in tek okuma
  kapısı, hiçbir varsayılan değer taşımaz, eksik anahtarda `ConfigError` fırlatır. Sessiz
  varsayılan, modellerin farklı maliyet/risk varsayımlarıyla yarışması demektir. Loader'ın
  ayrı modül olması, ayara ihtiyaç duyan her modülü `core/data.py`'ye bağlamamak içindir.
- **`Signal.take_profits` / `Position.take_profits` `tuple`'a çevrildi.** `frozen=True` bir
  dataclass içinde mutable liste taşımak dondurmayı yarım bırakıyordu: bir strateji ya da
  `peer_signals` okuyan bir meta model, sinyalin hedeflerini yerinde değiştirebilirdi.
- **`strategies/base.py` sözleşmenin tamamına getirildi**: `MarketData.funding` zaman
  indeksli seri, `MarketData.as_of`, `Strategy.is_meta` ve `generate_signals(..., peer_signals)`
  eklendi — `core/data.py`'nin ürettiği anlık görüntü ancak bu alanlarla tanımlı.
- **Veri kaynağı OKX public API v5** (auth gerekmez): `/public/instruments` + `/market/tickers`
  ile evren, `/market/candles` + `/market/history-candles` ile barlar,
  `/public/funding-rate-history` ile funding. Evren 24s **cirosuna** göre sıralanır
  (`volCcy24h × last`; SWAP'te `volCcy24h` base cinsindendir, sembolleri karşılaştırılabilir
  kılmak için fiyatla çarpılır), eşitlikte alfabetik — aynı veri aynı evreni vermeli.
- **Look-ahead (kural 12) iki katmanlı savunmayla uygulandı**: OKX mumları ters kronolojik
  döner ve ilk satır genelde kapanmamış bardır (`confirm="0"`). (1) `confirm != "1"` olan bar
  atılır, (2) kapanış zamanı `now`'ı aşan bar da atılır (confirm taşımayan yanıtlara karşı).
  `as_of` ayakta kalan son kapanmış barın zamanıdır.
- **`as_of` sembollerin ortak son barıydı (minimum);** bkz. karar 4 — bu yaklaşım BTC
  çıpasıyla değiştirildi.
- **Önbellek**: sembol başına parquet (`data/cache/`), her koşuda yalnızca eksik barlar çekilir
  (önbellekteki son bara ulaşıldığında sayfalama durur). Sayfalama `after` imleciyle yeniye
  doğru değil eskiye doğru ilerler; imleç ilerlemezse döngü kırılır. Rate limit (HTTP 429 /
  OKX `50011`) ve geçici 5xx hataları exponential backoff ile yeniden denenir, jitter
  `random_seed`'den beslenir. Kalıcı hata `OKXError` olur ve tek sembolün hatası turu düşürmez.
- **Yeni bağımlılık: `pyarrow`** — parquet önbelleği için gerekli (CLAUDE.md "harici bağımlılık
  minimumda" kuralı gereği gerekçe: mumların sütunlu ve tipli saklanması, CSV'ye göre hem
  tekrar okuma maliyetini hem tip kaybı riskini düşürür). CI kurulumuna eklendi.
- **Manuel doğrulama aracı**: `scripts/manual_data_check.py` 3 sembol için veriyi çeker, son
  barları ve funding'i yazdırır, son barın gerçekten kapanmış olduğunu ekrana basar.

## 4. `as_of` sembollerin ortağı değil, BTC çıpasıdır

Karar 3'teki "ortak (minimum) son kapanmış bar" tanımı iki sorun taşıyordu:

- **Döngüsel bağımlılık:** bayat sembolü dışlamak için önce `as_of`'a, `as_of`'u hesaplamak
  için önce sembol listesine ihtiyaç vardı. `max_staleness_bars` bu döngüyü "en yeni bara
  göre tolerans" diye kırıyordu, ama tanım kendi kendine dayanıyordu.
- **Tek gecikmiş sembol bütün turu geri çekiyordu:** tolerans içinde kalan tek bir sembol
  `as_of`'u bir bar (4 saat) geriye taşıyabiliyordu. O bar zaten işlenmişse sonuç ya çift
  işlem ya da hiç ilerlemeyen bir tur olurdu — ikisi de ölçümü geçersiz kılar.

**Yeni kural:** `as_of`, `exchange.btc_reference` (BTC-USDT-SWAP) sembolünün son kapanmış
barıdır. BTC zaten her modelin rejim filtresinde referans, hem de evrenin en likit ve en az
gecikecek sembolü; çıpa olarak sabit ve dışarıdan denetlenebilir bir tanım verir.

- `as_of` barına sahip olmayan semboller (geç kalan, durdurulan, serisinde boşluk olan) o tur
  **dışlanır** ve `logger.warning` ile gerekçesi yazılır; ayrıca her tur tek satırlık bir
  `logger.info` özeti (`as_of`, görülebilen sembol sayısı, dışlananların listesi) düşer —
  hangi turda modellerin kaç sembol görebildiği sonradan denetlenebilsin diye.
- BTC'den **ileride** olan semboller dışlanmaz, `as_of`'a kırpılır: çıpa geri gittiğinde tur
  yine tek bir "şimdi" görür (kural 5).
- `data.max_staleness_bars` anlamını değiştirdi: artık sembol başına tolerans değil, **çıpanın
  kendi tazelik sınırı.** BTC verisi beklenen bardan bu kadar barlık gecikmeyi aşarsa
  `OKXError` fırlatılır ve anlık görüntü hiç üretilmez — veri kesintisinde eski bir barı
  yeniymiş gibi işlemek, atlanan bir turdan daha pahalıdır.


## 5. Motor, portföy, defter ve funding uygulandı

`core/ledger.py`, `core/portfolio.py`, `core/funding.py` ve `core/engine.py` iskeletten
çıkarıldı. Alınan kararlar ve gerekçeleri:

### Defter (`core/ledger.py`)

- Model başına `ledgers/<model>/` altında üç dosya: `positions.json` (koşular arası taşınan
  durum — nakit, açık pozisyonlar, bekleyen emirler, işlenmiş son bar), `trades.csv`
  (kapanan her işlem/kısmi çıkış) ve `equity.csv` (bar başına bakiye görüntüsü).
- **Her yazma atomiktir**: geçici dosya + `fsync` + `os.replace`. Cron adımının ortasında
  düşen bir koşu yarım yazılmış defter bırakırsa, sonraki koşu yanlış bakiyeyle devam eder
  ve ölçüm sessizce bozulur. Append'ler de aynı yoldan gider; mevcut içerik bayt bayt
  korunduğu için "append-only" bir kural değil dosya sözleşmesidir.
- **Şema kayması reddedilir**: CSV başlığı beklenenden farklıysa `LedgerError`. Eski satırlar
  yeni kolonlarla karışırsa denetim izi sessizce anlamsızlaşır.
- `initialize_model` mevcut deftere dokunmaz, `reset_model` sıfırdan başlatır: yarışmaya yeni
  eklenen bir modelin defteri kendiliğinden boş açılır, mevcut bir modelinki elle sıfırlanır.

### Boyutlandırma ve marj (`core/portfolio.py`)

- **Sermaye tanımı**: kural 11'deki "sermaye" hesabın toplam özsermayesidir (nakit + marj +
  gerçekleşmemiş PnL), yani riske atılan %1 her zaman hesabın %1'idir. Kaldıraç tavanı ise
  eldeki **nakit** üzerinden uygulanır (marj nakitten fazla olamaz); bu, hesabın toplam
  notional'ını da `leverage_cap × özsermaye` sınırında tutar.
- **Kaldıraç bir sonuçtur**: yalnızca gereken notional nakdi aşarsa devreye girer. Tavana
  takılan işlem atlanmaz, tavana sığacak şekilde küçültülür ve gerekçe `trades.csv`'nin
  `notes` kolonuna yazılır (kural 11). Marj + komisyon nakdi aşarsa aynı doğrusal ölçekle
  ikinci bir kırpma yapılır — yine atlama değil.
- **Likidasyon fiyatı** giriş notional'ı üzerinden hesaplanır: `entry × (1 ± mm) ∓ marj/adet`.
  Mum içi mark fiyatına göre yeniden hesaplamak her barda farklı bir eşik üretir ve aynı
  senaryo iki koşuda farklı sonuç verebilirdi; tekrarlanabilirlik önce gelir. Kısmi çıkışta
  marj ve miktar orantılı azaldığı için `marj/adet` — dolayısıyla likidasyon fiyatı — sabit
  kalır.
- **Nakit muhasebesi**: açılışta `nakit -= marj + komisyon`, açıkken funding doğrudan nakde
  işler, kapanışta `nakit += marj + brüt PnL - komisyon`, likidasyonda hiçbir şey dönmez
  (marjın tamamı gider). `Trade.pnl` işlemin nakde net etkisidir; kapanan işlemlerin `pnl`
  toplamı bakiyedeki toplam değişime eşittir (test edilir).
- **Kayma yorumu**: config yalnızca iki kayma sabiti tanımlar. `slippage_long` her dolumun
  **taban** kayması olarak, `slippage_short_stop` ise short stop dolumlarının özel hâli
  olarak uygulanır. Short girişlere/TP'lere kayma uygulamamak, tam da ölçtüğümüz long/short
  farkını shortlar lehine bozardı; tek asimetri config'in açıkça istediğidir.
- **Stop boşluklu barda açılıştan dolar** (stop ile açılıştan aleyhte olanı seçilir); TP ise
  kendi fiyatından dolar ve lehte boşluk kâr yazılmaz — iyimser varsayımdan kaçınılır.
- Aynı sembolde aynı yönde ikinci pozisyon açılmaz; ters yön ayrı bir pozisyondur.
  `max_positions` ve `max_short_positions` açılış anında kontrol edilir.

### Funding (`core/funding.py`)

- Kural tek cümle: **pozitif funding'de long öder, short alır**; tutar = oran × notional.
  Modül maliyeti hesaplar, nakde `core/portfolio.py` işler (kural 2/7).
- Oranlar **uydurulmaz**: yalnızca tam zaman eşleşmesi kabul edilir, kayıt yoksa o periyot
  atlanır ve loglanır. `ffill` yapmak veri boşluğunu sentetik maliyete çevirirdi.
- Notional barın **açılış** fiyatından hesaplanır (mum içi bir fiyat seçmek look-ahead
  olurdu); funding anında henüz açılmamış pozisyon o periyodu ödemez.

### Tur akışı (`core/engine.py`)

- **Bekleyen emir kuyruğu**: kural 13 sinyalin bir sonraki barın açılışında dolmasını şart
  koşar; tur `as_of` barında bittiği için dolum bir sonraki turun ilk barıdır. Emirler bu
  yüzden `positions.json` içinde koşular arası taşınır. `manage_positions` çıkışları da aynı
  kuyruğa girer ve açılışlardan önce doldurulur (aynı turda kapanıp yeniden açılan sembolde
  kotanın yapay olarak dolu görünmemesi için).
- **Zaman ızgarası BTC'nindir**: işlenecek barlar `market.btc.index` üzerinden, son işlenmiş
  bardan `as_of`'a kadar alınır — `as_of`'un tanımı da odur (karar 4). Bir tur birden fazla
  bar ilerletebilir (cron kaçırılmışsa), her bar tam olarak bir kez işlenir.
- **Aynı `as_of` ile ikinci koşu sinyal üretmez**: aksi hâlde elle tekrar ya da cron retry,
  aynı bar için ikinci bir pozisyon kuyruğa alırdı (çift işlem — karar 4'ün uyardığı hata).
- **Bar içi sıra**: funding → bekleyen emir dolumu → likidasyon/stop/TP → trailing → özsermaye
  satırı. Trailing stop kontrolden **sonra** güncellenir: barın high/low'una bakıp aynı barın
  stop'unu değiştirmek, o barın içinde geçmişe dönük karar vermek olurdu (kural 12).
- **Trailing stop** chandelier kuralıyla uygulanır (long: en yüksek zirve − ATR × kat) ve
  yalnızca sıkışır. ATR periyodu config'e eklendi (`trailing.atr_period: 14`): her modelin
  kendi periyodunu seçmesi, aynı `trailing_atr` değerinin modelden modele farklı stop
  mesafesi anlamına gelmesi demekti.
- **Hata izolasyonu** (kural 8): `generate_signals`/doğrulama ya da `manage_positions` hata
  fırlatırsa yalnızca o model o turu boş geçer, gerekçe loglanır ve `RoundReport`a yazılır;
  koşu devam eder. Modelin mevcut pozisyonları yine core tarafından işlenir.
- **Doğrulama referans fiyatı** `as_of` barının kapanışıdır: gerçek dolum fiyatı (bir sonraki
  barın açılışı) sinyal anında bilinemez. Dolum stop'un ötesine düşerse emir açılışta
  reddedilir.
- Bir sonraki barda sembolü görülemeyen emir **iptal** edilir; gecikmeli dolum kural 13'ün
  tanımına uymaz. Barı olmayan sembolün açık pozisyonuna da dokunulmaz (elde olmayan mumla
  stop tetiklemek uydurma olurdu).

### Kapsam dışı bırakılanlar

- `core/metrics.py` hâlâ iskelet: `trades.csv` kolonları (yön, giriş/çıkış, komisyon,
  funding, PnL) long/short ayrıştırmasını besleyecek biçimde tasarlandı ama hesaplama ayrı
  bir adım.
- Turu başlatan bir çalıştırıcı (`main.py`/workflow adımı) henüz yok; `config.yaml > models`
  boş olduğu için çalıştıracak model de yok. İlk strateji eklendiğinde yazılacak.

## 6. Ortalama R birinci sınıf metrik; toplam getiri ikinci sırada

Karar 5'te sermaye tanımı "hesabın toplam özsermayesi (nakit + marj + gerçekleşmemiş PnL)"
olarak sabitlendi. Bu standart yaklaşım ama bir yan etkisi var: **açık kârı olan model daha
büyük risk alır.** Yani toplam getiri kısmen "model ne kadar hızlı bileşiklendi"yi ölçer,
sinyal kalitesini değil. Aynı sinyal kalitesine sahip iki modelden erken kâr yakalayanı
toplam getiride öne geçer — oysa ölçmek istediğimiz şey bu değil.

Bu yüzden `core/metrics.py`'de **işlem başına ortalama R** birinci sınıf metriktir:

```
R = işlemin net PnL'i / o işlemde AÇILIŞTA riske edilen tutar
```

- Payda `trades.csv`'nin **`risk_amount`** kolonudur (`adet × |giriş − ilk stop|`) ve deftere
  açılış anında yazılır. Türetilmiş olan R'nin kendisi deftere yazılmaz: defter olguları,
  `metrics.py` türetmeleri tutar — aynı hesabın iki yerde durması, iki yerde bozulması demek.
- **İlk stop** kullanılır, güncel stop değil: trailing stop sonradan kısaldığında R'nin
  tabanı değişirse aynı işlem sonradan daha başarılı görünürdü.
- Kaldıraç tavanına takılıp küçülen pozisyonda payda **gerçekten riske edilen** tutardır,
  modelin niyeti değil — model kırpma yüzünden ne ödüllendirilir ne cezalandırılır.
- Likidasyonda R −1'in altına iner (marjın tamamı gider). Bu bir hata değil, tam da ölçmek
  istediğimiz risk farkının görünür hâlidir; `liquidations` ayrıca sayılır.
- Karşılaştırma tablosu **ortalama R'ye göre sıralanır** ve kolon sırası bilinçlidir: önce R,
  sonra USDT getirisi. Toplam getiriye göre sıralamak, ayıklamaya çalıştığımız bileşiklenme
  etkisini geri sokardı. Toplam getiri atılmaz, ikinci sırada durur.

### Long/short ayrıştırmasının sınırı

İşlem-tabanlı her metrik (R, win-rate, profit factor, PnL) long ve short için ayrı hesaplanır.
Özsermaye eğrisinden gelenler (hesap getirisi, hesap max drawdown, hesap Sharpe) **yön bazında
ayrıştırılamaz** — hesapta tek bakiye vardır. Bunlar `AccountStats` altında açıkça hesap
düzeyi olarak raporlanır; yönlerin kendi risk profili bunun yerine o yönün **kümülatif R
eğrisinden** ölçülür (`r_sharpe`, `max_drawdown_r`).

### Tanımsız metrik `nan` döner

İşlem yoksa, varyans sıfırsa ya da kazanan işlem yoksa metrik `nan`'dır ve raporda `—` yazılır.
0.0 döndürmek "ölçüldü ve sıfır çıktı" ile "ölçülemedi"yi aynı sayıya indirger; 10 modelin
sıralandığı bir tabloda bu sessiz bir sıralama hatasıdır. `risk_amount`'ı olmayan satırlar da
R ortalamasına girmez, `unmeasured` olarak sayılır ve raporda uyarı satırı düşer.

### Gözlem: bir stop gerçekte 1R'den pahalıdır

İlk uçtan uca koşuda 1R'lik bir stop long'da **−1.04R**, short'ta **−1.16R** ile kapandı.
Fark maliyetlerden geliyor ve yapısal: komisyon notional ile ölçeklenir, risk ise stop
mesafesiyle. Dar stop kullanan bir model aynı 1R için daha büyük pozisyon taşır, dolayısıyla
R başına daha çok komisyon öder; short stop'ların kayması ayrıca 3 kat (`slippage_short_stop`).
Bu, **stop mesafesi tercihinin ölçüme sızdığı** anlamına gelir: modeller karşılaştırılırken
ortalama stop mesafesi de bakılması gereken bir değişkendir.

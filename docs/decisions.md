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

## 7. `slippage_base` adlandırması, R başına maliyet kolonları ve stop mesafesi bandı

Tetikleyen bulgu: **stop mesafesi aynı zamanda maliyet ölçeğidir.** Boyut
`risk / |giriş − stop|` olduğundan dar stop kuran model aynı 1R'yi daha büyük notional ile
taşır ve R başına daha çok komisyon+kayma öder. Bu, projenin ana sorusunu doğrudan kirletir:
"short modeller daha başarılı" sonucu sinyalden değil, yalnızca daha geniş stop kullanıp daha
az maliyet ödemelerinden geliyor olabilir. Ölçülmeden bırakılamaz.

- **`slippage_long` → `slippage_base` olarak yeniden adlandırıldı** (config.yaml, `core/config.py`,
  testler ve CLAUDE.md). Oran yöne bağlı değildi: short stop dolumu dışındaki **her** dolumda
  (long/short giriş, çıkış, kısmi TP) uygulanıyor. Eski ad, short girişlerin kaymasız dolduğu
  izlenimini veriyordu — yani tam da ölçtüğümüz long/short maliyet farkını yanlış anlatıyordu.
  `tests/test_config.py` eski adın geri sızmasını da test ediyor: iki anahtarın bir süre birlikte
  yaşaması, modellerin farklı maliyet varsayımlarıyla yarışması demek olurdu.
- **`core/metrics.py` iki maliyet kolonu raporlar** (model ve yön bazında):
  `avg_stop_distance_pct` ve `cost_per_r`. Böylece bir model öne çıktığında, farkın sinyalden mi
  maliyet avantajından mı geldiği ayrıştırılabilir. Tanımlar CLAUDE.md > "Rapor Kolonları"nda.
  - Payda **ilk stop**tur, trailing ile yürüyen stop değil: R giriş anında üstlenilen risktir.
    Yürüyen stop'u kullanmak iyi giden işlemlerin paydasını küçültür, `cost_per_r`'yi şişirir ve
    bu şişme yalnızca trailing kullanan modellerde olur — kıyası bozar.
  - `risk_amount` = `boyut × |giriş − ilk stop|`, yani **gerçekleşen** 1R. Boyutlandırma
    formülünün payı (`risk_per_trade × sermaye`) değil: kaldıraç tavanı boyutu küçülttüğünde
    (kural 11) gerçek risk de küçülür ve payı kullanmak o işlemlerin maliyetini olduğundan düşük
    gösterirdi.
  - **Veri yoksa `nan`, `0.0` değil.** `0.0` "işlem yaptı, tam sıfır çıktı" demektir; hiç short
    açmamış bir modeli "maliyetsiz short yapan model" gibi göstermek ortalamaları bozar.
  - **Yön bazlı Sharpe tek bakiyeyi bölerek üretilmez.** Hesap tektir (ortak margin, ortak
    funding, ortak nakit); bakiye eğrisini zorla ikiye ayırmak uydurma sayı verir. Yön Sharpe'ı o
    yöndeki işlemlerin kapanış sırasına dizilmiş **R cinsinden** getiri dizisinden hesaplanır;
    hesap Sharpe'ı ayrı bir satır olarak gerçek bakiye eğrisinden gelir ve iki yönün toplamı
    ya da ortalaması değildir.
- **Stop mesafesi bandı kural 14 olarak yazıldı:** modeller kabaca **1×–2.5×ATR** bandında kalır
  (2× ile 2.5× kıyaslanabilir; 0.5× ile 3× kıyaslanamaz). Prompt 3-5'teki parametreler zaten bu
  bandda. Tek risk, stop'unu veriye bağlı kuran "failed breakout" modeli (fitil tepesinin üstü
  **veya** 1×ATR, hangisi genişse): uzun bir fitil bandın dışına taşabilir. Bu yüzden
  `max_stop_atr_multiple: 3.0` config'e eklendi ve mesafe tavanı aşarsa **işlem atlanır.**
  - Stop tavana çekilmez: bu, modelin "stop fitilin üstünde olmalı" tezini sessizce başka bir
    modele çevirirdi — atlamak yalnızca ölçülemeyen işlemi eler.
  - `core/validate.py` burada hata fırlatmaz; geniş stop programlama hatası değil (kural 8),
    karşılaştırılamaz bir piyasa durumudur.
  - Atlama `logger.info` ile kaydedilir. Kural 11'in "atlamak işlem sayısını sessizce düşürür"
    itirazı böyle karşılanır: düşüş denetlenebilir, bandın tutup tutmadığı ise
    `avg_stop_distance_pct` kolonundan okunur.

Not: `core/metrics.py` hâlâ stub. Yukarıdaki kolon tanımları sözleşmeye yazıldı; uygulaması
ledger kayıt şemasıyla birlikte (portfolio/ledger promptu) gelecek.

## 8. Karar 7'nin `core/` tarafı uygulandı

Karar 7 sözleşmeyi (config, CLAUDE.md) değiştirdi ama `core/` tarafı motor dalında duruyordu;
merge sırasında uygulandı:

- **`slippage_long` → `slippage_base`** yalnızca ad değişikliği: davranış zaten yönden bağımsızdı
  (karar 5), ad onu gizliyordu.
- **`slippage_cost` deftere eklendi.** `cost_per_r` komisyon **ve** kayma ister; kayma dolum
  fiyatının içine gömülü olduğu için ayrıca yazılmazsa maliyetin yarısı ölçülemez kalırdı. Giriş
  kayması pozisyonda USDT olarak taşınır ve kısmi çıkışlarda komisyon gibi orantılı dağıtılır.
  Likidasyonda çıkış kayması yazılmaz: pozisyonun değeri zaten sıfırlanmıştır, üstüne kayma
  eklemek kaybı marjın ötesine taşırdı.
- **Kural 14 `core/engine.py`'de bir filtre olarak uygulandı**, `core/validate.py`'de değil —
  kuralın kendisi bunu şart koşuyor: geniş stop programlama hatası değil, karşılaştırılamayacak
  bir piyasa durumu. Elenen her sinyal `logger.info` ile gerekçelenir ve `RoundReport`'ta
  `skipped_signals` olarak sayılır; kural 11'in "atlamak işlem sayısını sessizce düşürür"
  itirazı böyle karşılanır. Kural 8 ile karışmaz: model atlanmaz, yalnızca o sinyal düşer.
- **ATR doğrulanamıyorsa sinyal elenmez, uyarı yazılır.** Tavanın aşılıp aşılmadığı
  bilinmiyorken işlemi atmak, ölçülemeyen bir nedenle işlem sayısını düşürürdü; bandın gerçekten
  tutup tutmadığı zaten sonradan `avg_stop_distance_pct` kolonundan denetlenir. Kural 14'ün ATR'si
  `trailing.atr_period`'dur — proje tek bir ATR tanımı taşır, yoksa "3×ATR" iki farklı mesafe
  demeye başlardı.
- **`cost_per_r` ve `avg_stop_distance_pct` `DirectionStats`e eklendi** ve tabloda getiri
  kolonlarından ÖNCE durur. Yön bazlı R dizisi kapanış zamanına göre sıralanır: defter zaten bu
  sırada yazılır, sıralama garantiyi satırlar başka yoldan gelse de gerçek kılar.

## 9. İlk iki yarışmacı model ve ortak gösterge modülü

`buyhold` çıpasının üstüne ilk iki YARIŞMACI eklendi: `trend` (Donchian kırılımı + EMA rejim
filtresi) ve `meanrev` (RSI + Bollinger ortalamaya dönüş). Boru hattı (veri → motor → portföy →
defter → metrikler) değiştirilmedi; modeller yalnızca `Signal` üretir.

### Göstergeler tek modülde: `core/indicators.py`

Her strateji kendi RSI/ATR/EMA'sını yazsaydı, iki model aynı barda **farklı sayı** görürdü — bu
kural 5'in ("tüm stratejiler aynı anlık görüntüyü görür") sayısal düzeyde delinmesidir ve sinyal
farkını gösterge farkıyla karıştırır. Bu yüzden gösterge matematiği stratejilerden çıkarıldı.

`average_true_range` **taşındı**, kopyalanmadı: `core/engine.py` artık onu indicators'tan import
edip yeniden dışa veriyor. İkinci bir ATR uygulaması, trailing mesafesi (kural 9), stop bandı
(kural 14) ve stratejinin stop'unun üç ayrı sayıya bölünmesi demekti. Dış davranış aynı; mevcut
testler değiştirilmeden geçiyor.

Tanım kararları ve gerekçeleri:

- **Pencere-yerel ortalamalar (ATR, RSI) Wilder yumuşatması kullanmaz.** Wilder özyinelemelidir,
  yani sonuç çerçeveye kaç bar geçmiş verildiğine bağlıdır: önbellek farklı ısındığında ya da
  sembol geç listelendiğinde aynı bar için farklı sayı çıkar. Basit ortalama yalnızca son
  `period` bara bakar — aynı bar her zaman aynı sayıyı verir.
- **EMA'da bu kaçış yok** (tanımı gereği özyinelemeli), o yüzden tohum açıkça ilk `period` barın
  SMA'sına sabitlendi; pandas `ewm` varsayılanına bırakılsaydı tanım kodda görünmez olurdu.
- **Donchian son barı HARİÇ tutar.** Kanal değerlendirilen barı da içerseydi `upper >= high >= close`
  olurdu ve "tepenin üstüne kapanış" yalnızca kapanış tam zirveye eşitken sağlanabilirdi — kırılım
  pratikte hiç oluşmazdı. Kanal, kırılımı ölçülen barın GEÇMİŞİDİR.
- **Bollinger sapması popülasyon sapmasıdır (ddof=0).** Hangisinin "doğru" olduğu değil, tek ve
  yazılı olması önemli: ddof=1, 20 barlık pencerede bandı ~%2.6 genişletir.
- **Yeterli bar yoksa `None` döner**, kısmi pencereyle hesaplanmış bir sayı değil. Yeni listelenmiş
  bir sembolde 30 barla "200 EMA" üretmek, rejim filtresini o sembolde sessizce anlamsız kılardı.

### `strategies/trend.py`

Kırılım (20 bar Donchian) **ve** rejim (50/200 EMA) kapılarının ikisi de aranır; stop 2×ATR(14),
trailing 1×ATR. Rejim filtresinin işlevi kârı artırmak değil, **ölçülen şeyi daraltmaktır**:
filtresiz bir kırılım modeli yatay piyasada iki yönde de tetiklenir ve sonuç "kırılım işe yarıyor
mu" sorusuna değil "hangi rejimde ne kadar testere yedik" sorusuna cevap verir.

Stop neden sabit 2×ATR: stop mesafesi aynı zamanda maliyet ölçeğidir (kural 14) ve 2×, 1×–2.5×ATR
bandının ortasıdır — `max_stop_atr_multiple` (3.0) tavanına takılmaz, yani bu model tavan yüzünden
sessizce işlem kaybetmez.

### `strategies/meanrev.py`

RSI(14) eşiği **ve** Bollinger(20,2) kırılımı birlikte aranır; stop 2×ATR, tek TP = orta bant
(20 SMA), fraction 1.0. Hedef bir ATR katı değil göstergenin kendi orta bandıdır: modelin tezi
"ne kadar kazanacağım" değil "fiyat ortalamasına döner"dir, TP'yi tezden bağımsız bir sayıya
bağlamak ölçülen şeyi ortalamaya dönüş olmaktan çıkarırdı. Kısmi çıkış (kâr koruma) ayrı bir
tezdir, bu modele karıştırılmadı — aynı nedenle trailing de istenmiyor.

**Short'un BTC rejim kapısı** modelin ölçüm açısından en kritik kuralı: short sinyali yalnızca BTC
4h kapanışı kendi 200 EMA'sının altındayken geçerli. Altcoin'lerin çoğu BTC ile yüksek
korelasyonlu olduğundan boğa rejiminde "aşırı alım" sürekli tetiklenir; filtresiz bir short kolu
sinyal kalitesini değil **rejim yönünü** ölçer. Projenin ana sorusu "short işlemler long'lardan
daha mı başarılı" olduğuna göre bu, cevabı baştan kirletirdi.

Kapı BTC'ye bağlı çünkü `as_of` çıpası da BTC (karar 4): her model aynı barı "şimdi" sayar,
dolayısıyla filtre tüm semboller için aynı anda açılıp kapanır. Sembol başına ayrı rejim tanımı,
aynı turda birbiriyle çelişen kapılar üretirdi.

BTC'nin 200 EMA'sı hesaplanamıyorsa (yeterli bar yok) kapı **kapalı** kalır. Açık varsaymak,
filtrenin var olmadığı bir dönemde short açmak — modeli sessizce başka bir modele çevirmek —
olurdu; kapalı varsaymanın bedeli yalnızca eksik işlemdir ve o `logger.info` ile kaydedilir.

### `reason` alanı bir denetim aracıdır, süs değil

İki model de gerekçeyi **ölçüm ve eşiği yan yana** yazar ("RSI(14) 24 (<30); alt Bollinger(20,2)
90.28 bandının %1.80 altında kapanış (88.66); stop 2×ATR(14)=6.71 uzakta (81.95); TP orta bant
99"). Yalnızca ölçümü yazmak, sonradan "eşik neydi" sorusunu commit geçmişinde aramaya zorlardı.
Alan `Position` üzerinden kapanan işlemin `signal_reason` kolonuna taşınır: "model neden bu işlemi
yaptı" denetimi defterden, koşuyu tekrar üretmeye gerek kalmadan yapılabilir.

### Atlamalar sessiz değil

ATR üretilemediği için düşen sinyal `logger.info` ile kaydedilir (kural 14'ün gerekçesi burada da
geçerli: ölçülemeyen bir nedenle düşen işlem sayısı denetlenebilir olmalı). `as_of` barını
taşımayan sembol sinyal üretmez — `core/data.py` bunları zaten dışlar, ama bir bar geriden sinyal
üretmek look-ahead kadar sessiz bir ölçüm hatasıdır.

### Yan etki: `tests/test_main.py` config'e bağlandı

`models` listesi büyüdüğü için `payload["models"]` artık literal `["buyhold"]` ile değil
`load_config()["models"]` ile karşılaştırılıyor. Testin ölçtüğü şey "hangi modeller var" değil,
"main config'in TAMAMINI koşturdu mu" — 10 model hedefiyle bu ayrım kalıcı.

## 10. İki yarışmacı daha: `momentum` ve `squeeze` (`confluence` beklemede)

Yarışmacı sayısı 2'den 4'e çıktı: `momentum` (kesitsel momentum, haftalık dengeleme) ve
`squeeze` (Bollinger sıkışması + hacim teyitli kırılım). Boru hattına (veri → motor → portföy →
defter → metrikler) dokunulmadı; iki model de yalnızca `Signal` üretir, göstergelerini
`core/indicators.py`'den okur ve kendi boyut/komisyon/bakiye hesabı yapmaz (kural 1/2/3/7).

### `strategies/momentum.py`

Evren her dengelemede 7 günlük getiriye göre sıralanır; ilk 5 long, son 5 short, stop 2.5×ATR(14).
Model mutlak bir yön iddiası taşımaz — aynı anda iki yönde de açar; ölçtüğü şey **sıralamanın
uçlarıdır.**

- **Dengeleme yalnızca Pazartesi 00:00 UTC barında.** Sıralama her turda (4 saatte bir) yeniden
  hesaplansaydı model haftada 42 kez dengelerdi; sıra değiştiren her küçük oynama bir işlem açar
  ve sonuç "momentum işe yarıyor mu" sorusuna değil "komisyon+kayma ne kadar yiyor" sorusuna
  cevap verirdi. Sabit tek bar, işlem sayısını tezin zaman ölçeğine (haftalık) bağlar ve hangi
  turda dengelendiği sonradan `as_of`'tan okunur. Diğer turlarda sinyal üretilmez; açık
  pozisyonların stop/trailing takibi motorun işidir (kural 9).
- **Geriye bakış GÜN cinsinden tanımlı, bar cinsinden değil.** `42` gibi bir sabit, `timeframe`
  değiştiğinde tezi sessizce başka bir teze çevirirdi; bar sayısı config'in `timeframe`'inden
  türetilir.
- **Uçlar örtüşürse tur atlanır.** Sıralanabilen sembol sayısı 10'un altına düşerse aynı sembol
  hem long hem short listesine girerdi. Listeyi küçültmek (ilk 2 / son 2) modeli sessizce başka
  bir modele çevirir; atlamak yalnızca ölçülemeyen turu ölçüyormuş gibi göstermez ve loglanır.
- **Eşitlikte sıra sembol adına göre.** Sözlük/veri katmanı sırasına bırakmak, koşuları
  tekrarlanamaz kılardı (`random_seed`'in aynı gerekçesi).
- Stop **2.5×ATR**, yani 1×–2.5×ATR bandının üst ucu: haftalık tutulan bir pozisyonun 4 saatlik
  gürültüye takılmaması için bilinçli olarak geniş, ama `max_stop_atr_multiple` (3.0) tavanının
  altında — model tavan yüzünden sessizce işlem kaybetmez.
- Kısmi pencereyle getiri hesaplanmaz: 43 barı olmayan sembol sıralamaya hiç girmez. Aksi hâlde
  3 günlük getiri 7 günlük sanılır ve iki farklı uzunluk aynı kolonda yarışırdı.

### `strategies/squeeze.py`

Bant genişliği son 50 barın en dar %20'sindeyken banda kapanış + hacim teyidi; yukarı kırılım
long, aşağı kırılım short. Stop, sıkışma aralığının karşı ucu.

- **Hacim teyidi (20 bar ortalamasının 1.5 katı) opsiyonel bir ek değil, modelin ana filtresidir.**
  Sıkışmadan çıkan kapanışların çoğu sahte kırılımdır; teyitsiz bir koşul modeli "dar banttan
  çıkışları say" ölçümüne indirger ve tezin kendisi (hacmin katılımı) hiç test edilmemiş olur.
  Teyit yoksa **işlem yok**, ve bastırılan her kırılım loglanır — filtrenin kaç işlemi elediği
  denetlenebilir olmalı.
- **Bantlar ve hacim ortalaması kırılım barını HARİÇ tutar.** Gerekçe Donchian'ınkiyle birebir
  aynı (karar 9): kırılım barının kendi hareketi aynı barın standart sapmasını, kendi hacmi de
  20 barlık ortalamayı şişirir — yani en güçlü kırılımlar kendi kendini elerdi. İki ölçü de
  kırılım barının GEÇMİŞİNDEN alınır; böylece "sıkışma" ile "kırılım" iki ayrı bara oturur ve
  tanım denetlenebilir olur.
- **Genişlik orta banda bölünür (yüzdesel).** Mutlak genişlik fiyat seviyesiyle ölçeklenir;
  aynı sembolün altı ay önceki bandıyla bugünküsü kıyaslanamaz hâle gelirdi.
- **Stop tezle aynı yerde: sıkışma aralığının karşı ucu.** Kırılım geçersizse fiyat aralığın
  içine döner ve diğer uca ulaşır. Bu mesafeyi veriye bağlar, dolayısıyla `max_stop_atr_multiple`
  burada bir **tavandır** (kural 14): mesafe tavanı aşarsa işlem **atlanır**, stop tavana
  çekilmez — çekmek modelin tezini sessizce başka bir modele çevirirdi. Motor aynı tavanı ayrıca
  uygular; modelin kendi kapısı, atlamanın gerekçesini (hangi sıkışma, hangi mesafe) modelin
  diliyle loglamak içindir.
- Sıkışma aralığı kapanışı içeriyorsa (stop girişin yanlış tarafında) sinyal üretilmez ve
  loglanır: bu bir programlama hatası değil veri durumudur, `core/validate.py`'ye taşınsaydı
  `ValueError` ile TÜM modeli düşürürdü (kural 8 ile karıştırılmamalı).

### `tests/helpers_market.py` genişletildi

`frame()` artık `volumes` ve `start` alıyor. Hacim teyidini ölçen bir model sabit 1.0 hacimle
test edilemez; dengeleme barına duyarlı bir model de takvim günü sabitlenmeden test edilemez.
Kurucuyu kopyalamak yerine ortak dosyaya eklendi — iki testin farklı bar aralığı kullanması,
"aynı veriyi gördüler" varsayımını testlerde bile bozardı.

### `confluence` bu turda yazılmadı

Model, mevcut crypto-scanner reposundaki zigzag pivot mantığına dayanıyor ve o kod bu depoda yok.
İkinci bir zigzag uygulaması yazmak, karar 9'un ("gösterge matematiği tek yerde") tam tersi
olurdu: aynı pivot iki farklı tanımla iki farklı sayı üretir ve modelin ölçtüğü şeyin kaynak
modelle aynı olduğu iddiası kanıtlanamaz hâle gelir. Kaynak dosyalar yapıştırılana kadar
`confluence` ne `strategies/registry.py`'ye ne de `config.yaml`'ın `models` listesine eklendi —
kayıtlı olmayan bir ad koşuyu hata koduyla düşürür (`main.py`), yarı yazılmış bir model ise
sessizce eksik yarışırdı.

## 11. `confluence`: kaynak tarayıcıdan taşıma, birebir pivot, bilinçli sapmalar

`confluence`, crypto-scanner deposundaki ana gate'in (`find_confluence_candidates` +
`evaluate_confluence_entry`) bu projeye taşınmış hâlidir. Tez: fiyatın kendi yapısından çıkan
iki bağımsız swing'in Fibonacci seviyeleri aynı fiyatta çakışıyorsa (büyük dalganın
0.618/0.786 retracement'i, küçük ABC bacağının 1.272/1.618 extension'ı) orası tek bir
seviyeden güçlü bir dönüş bölgesidir; yönü RSI belirler (<35 long, >65 short, arası işlem yok).

### Pivot matematiği kopyalanmadı, TAŞINDI

`find_zigzag_pivots`, `_merge_short_legs` ve `compute_fib_levels` `core/indicators.py`'ye
birebir taşındı (eşik karşılaştırmaları, canlı uç davranışı, çift silme mantığı dâhil).
Yeniden yazmak, karar 9'un ("aynı göstergenin iki uygulaması iki farklı sayı demektir")
tam tersi olurdu — üstelik burada ikinci uygulama, modelin ölçtüğü şeyin kaynak tarayıcıyla
aynı olduğu iddiasını da kanıtlanamaz kılardı.

Taşımanın doğruluğu **kaynağa karşı** doğrulandı: 600 rastgele çerçeve × 3 parametre setinde
pivot listeleri (zaman, fiyat, tip) birebir aynı; 300 çerçevede büyük dalga/küçük bacak seçimi,
seçilen oranlar, seviye fiyatları, mesafeler ve `within_tolerance` kararı birebir aynı.

Look-ahead (kural 12) açısından temiz: fonksiyonlar yalnızca verilen çerçeveyi okur, çağıran
taraf çerçeveyi `bars_until` ile `as_of`'ta keser. Kaynaktaki "henüz teyit edilmemiş canlı uç"
pivotu KORUNDU: bir sonraki barda yer değiştirebilir ama geleceği görmez; atmak swing'in
güncel ucunu tümden kaybettirirdi.

### Kaynaktan bilinçli sapmalar

- **Confidence kademeleri (low/medium/high) ve 0.5R/1.0R/1.5R çarpanı taşınmadı.** Kaynakta
  confidence'ın TEK işlevi pozisyon boyutunu çarpmaktı. Bu projede boyutlandırma stratejinin
  işi değildir (kural 3/11): çarpanı taşımak ortak risk birimini (1R) modele göre değiştirir
  ve tabloyu kıyaslanamaz kılardı. Çarpan olmadan confidence hiçbir ölçülen büyüklüğü
  etkilemez; yalnızca `reason` metnini süslemek için ~300 satırlık Double Bottom/Top + RSI
  diverjans + Wyckoff katmanını taşımak, ölçüm değeri olmayan bakım yükü olurdu.
  **Kademeler ölçülmek isteniyorsa doğru yol ayrı model satırlarıdır** (ör. `confluence` ve
  yalnızca "tam teyitli" kurulumları alan `confluence_confirmed`): o zaman kademe farkı
  tabloda iki satır olarak, aynı risk biriminde yarışır — ki bu projenin cevap verebildiği
  soru biçimi tam olarak budur.
- **RSI ve ATR `core/indicators.py`'den okunur.** Kaynak Wilder yumuşatması kullanıyor
  (`ewm(alpha=1/period)`); bu depo onu açıkça reddediyor (karar 9: özyineleme, sonucu çerçeveye
  kaç bar geçmiş verildiğine bağlı kılar). Eşikler (35/65, ATR periyodu 14) aynı; sayılar
  kaynakla birebir aynı çıkmaz. Bu, ölçümün tekrarlanabilirliği uğruna kabul edilmiş bir farktır.
- **Stop 2.5×ATR, kaynaktaki 3.0×ATR değil.** 3.0 bu projede `max_stop_atr_multiple`
  TAVANININ kendisidir (kural 14). Dolum bir sonraki barın açılışında olduğundan (kural 13)
  motorun ölçtüğü mesafe girişin kapanışına göre hesaplanandan farklıdır ve tavanın hemen
  üstüne çıkabilir: model, sinyal üretip sessizce elenen işlemlerle ölçülemez hâle gelirdi.
  2.5, bandın (1×–2.5×ATR) üst ucudur ve `momentum` ile aynı ölçektedir.
- **Telegram/state/cooldown/CSV ve likidite-stablecoin filtreleri taşınmadı**: ilki raporlama
  katmanı (burada defter ve `docs/data/metrics.json` var), ikincisi evren katmanının işi
  (`core/data.py` zaten hacme göre ilk 50 USDT perpetual'ı seçiyor).

### Kapının kaç kez yönsüz açıldığı loglanır

Confluence toleransı geçip RSI'ın nötr bölgede kaldığı durum `logger.info` ile kaydedilir.
Sessiz geçmek, eşiklerin (35/65) gerçekten mi yoksa geometrinin mi eleme yaptığını sonradan
ayırt edilemez kılardı.

## 12. Confluence confidence kademesi: hesaplanır, yalnızca deftere yazılır

Karar 11'de kademe hiç taşınmamıştı. Kullanıcı kararıyla üçüncü yol seçildi: **kademe
hesaplanır ama hiçbir kararı etkilemez**; `reason`ın sonuna ayrıştırılabilir biçimde
`| confidence=low|medium|high` olarak eklenir ve `trades.csv`in `signal_reason` kolonundan
gruplanarak sonradan incelenir. Giriş kararı, yön, stop mesafesi, pozisyon boyutu ve ölçüm
tablosu bundan **etkilenmez.**

Ayrı model açılmadı (`confluence_confirmed`): teyitli kurulumlar `confluence`ın ALT KÜMESİ
olduğu için iki satır büyük ölçüde aynı işlemleri taşır; korelasyon matrisinde bağımsız bir
model gibi görünüp 10 modelin birini gereksiz tüketirdi.

### Sınır tek bir dosyada duruyor

Hesap `strategies/confluence_confidence.py`'de; `strategies/confluence.py` oradan yalnızca
bir string alır. "Kademe hiçbir şeyi etkilemiyor" iddiası böylece tek yerden denetlenebilir.
İki koruma test altında:

- Kademe zorla değiştirildiğinde sinyalin `stop_price`, `direction`, `sizing`, `take_profits`
  alanları ve `reason`ın geri kalanı **birebir aynı** kalır.
- Kademe katmanı istisna fırlatırsa sinyal **değişmeden** üretilir, etiket `unknown` olur ve
  `logger.warning` düşer. Süs amaçlı bir katmanın ölçümü düşürmesi kabul edilemez.

Etiket serbest cümlenin içine gömülmez (` | confidence=high` olarak sonda durur): gömülü bir
etiket, defterden gruplama için metin ayrıştırmak zorunda bırakırdı.

### Taşınan kod ve kaynağa karşı doğrulama

Kademe tanımı kaynaktaki `evaluate_confluence_entry` ile aynıdır: `high` = yapı var ve tam
teyitli, `medium` = yalnızca yapı var, `low` = yapı yok. "Tam teyit" kaynaktaki üç kapıdır
(çift dip/tepe + kırılım/hacim, RSI diverjansı, fiyatın 0.618-0.786 bandında olması).
Kaynağın Wyckoff/Elliott/Motor-1 katmanları taşınmadı: onlar `is_valid`i değil yalnızca
kaynağın kendi confidence/bonus alanlarını etkiliyordu — bu kademede karşılıkları yok.

`signal_validation.py`'ye karşı 800 karşılaştırmada (400 rastgele çerçeve × 2 yön):

| Katman | Sonuç |
|---|---|
| Yapı var mı (`structure_present`) | 800/800 aynı |
| Kırılım + hacim teyidi | 800/800 aynı |
| Tam teyit (`is_valid`) ve kademe | 799/800 aynı |

Tek fark RSI tanımından geliyor ve beklenen bir farktır (karar 9/11): aynı çift dipte Wilder
RSI'ı 31.0 -> 34.1 (fark 3.1, "en az 5 puan" eşiğini geçmiyor), pencere-yerel RSI 23.0 -> 40.4
(fark 17.4, geçiyor). Bu fark **yalnızca etiketi** etkiler; hiçbir işlem, boyut veya metrik
ondan türemez.

### `core/indicators.py`'ye eklenenler

- **`rsi_series`**: RSI'ın bar bazlı hâli. Diverjans kontrolü "şimdiki" RSI'ı değil, iki
  PİVOT barının RSI'ını karşılaştırır. `rsi()` artık bu serinin son değeridir — iki ayrı
  hesap tutmak, aynı modelin pivotta okuduğu RSI ile eşik karşılaştırdığı RSI'ı farklı
  tanımlardan besleyecekti.
- **`local_lows` / `local_highs`**: kaynaktaki `find_local_lows`/`find_local_highs` (fraktal,
  `order=3`). Zigzag'dan **bilerek ayrı** bir pivot tanımıdır — kaynak da çift dip yapısını
  bununla arar; birini diğerinin yerine kullanmak kaynak modelin ölçtüğü yapıyı değiştirirdi.
  Son `order` bar hiçbir zaman pivot olamaz: bu bir gecikmedir, look-ahead değil.

---

## 13. Son dört model: `failed_breakout`, `downtrend_rally`, `avwap`, `random_ctrl`

Kayıtlı yarışmacı sayısı 9'a çıktı (buyhold hâlâ referans çıpası, kural 15). Dördünün de
göstergeleri `core/indicators.py`'den okunur; hiçbiri kendi RSI/ATR/EMA/pivot matematiğini
yazmaz (karar 9'un gerekçesi aynen geçerli).

### `strategies/failed_breakout.py` (short-only)

Tuzak kırılım: 20 bar zirvesini FİTİLLE süpüren, ardından 1-2 bar içinde aynı seviyenin
altına kapanan, hacmi kendi 20 bar ortalamasının altında kalan ve RSI ayı uyumsuzluğu taşıyan
kurulum.

- **Süpürme fitille, teyit kapanışla tanımlı.** Kırılımı kapanışla tanımlamak tuzağın
  kendisini eler ve model "başarılı kırılım" aramaya başlardı.
- **Teyit BİRİNCİ kapanışta aranır.** Aradaki barlardan biri zaten seviyenin altına
  kapanmışsa kurulum orada oluşmuştur; aksi hâlde aynı tuzak iki tur üst üste sinyal üretir,
  `core/portfolio.py` ikincisini `duplicate_position` ile reddeder ve modelin sinyal sayısı
  ölçülemeyen bir nedenle şişerdi.
- **Funding önceliği boyut değil SIRA.** Son 3 periyodun ortalaması pozitif ve yükseliyorsa
  sinyal listenin başına alınır. Boyutla ödüllendirmek kural 3/11'i delerdi; motor
  `max_positions` dolana kadar sinyalleri geldikleri sırada doldurduğu için sıra,
  boyutlandırmaya dokunmayan tek "öncelik" kanalıdır. Gerekçe `reason`a yazılır.
- **Stop fitil tepesi ya da 1×ATR — hangisi genişse; tavanı aşarsa işlem ATLANIR** (kural 14).
  ATR burada bir TABANDIR: dar fitilli bir süpürmede stop'u fitilin hemen üstüne koymak R'yi
  gürültü ölçeğine indirir ve R başına maliyeti diğer modellerle kıyaslanamaz yapardı.
- **Hedef önce son swing dip, yoksa 2R.** Sabit R hedefini dipten öne almak, modeli "tuzak"
  modelinden "sabit R hasat eden" bir modele çevirirdi.

### `strategies/downtrend_rally.py` (short-only)

Rejim (200 EMA altı + 50 EMA < 200 EMA) ve kesitsel zayıflık (7g getiri evrenin alt %20'si)
altında, son düşüş bacağının 0.382-0.618 bandına ya da 20 EMA'ya **aşağıdan ilk dokunuşta**,
RSI(14) < 45 VE hacim 20 bar ortalamasının üstündeyken short.

- **Zayıflık kesitseldir.** "Düşüyor" mutlak bir ifade değil, sıralamada bir yerdir; mutlak
  tanım modeli bir piyasa yönü bahsine indirgerdi. Alt %20'nin tek bir sembolü
  adlandırabilmesi için en az 5 sıralanabilir sembol aranır, yoksa tur sinyalsiz geçer ve
  loglanır.
- **Bacağın dibi pivottan değil, barların kendi en düşüğünden okunur.** Zigzag kısa bacakları
  ÇİFT hâlinde eler (`_merge_short_legs`): taze bir dip, onu teyit eden ralli 8 bardan kısaysa
  pivot listesinde hiç görünmez. Dibi pivotlardan okumak, modelin "son düşüş bacağı" derken
  hep bir önceki bacağı kastetmesi — yani düzeltme bandını fiyattan onlarca yüzde uzağa
  koyması ve kapının pratikte hiç açılmaması — demekti. Bacağın BAŞI ise teyitli bir zigzag
  zirvesi olmak zorundadır.
- **Funding kapısı (ortalama < %-0.01 ise girme) bir kâr filtresi değil ölçüm filtresidir:**
  kalabalık zaten short taraftayken açılan işlem sinyal kalitesini değil squeeze riskini
  ölçer. Veri yoksa kapı KAPALI kalır — `meanrev`in BTC kapısıyla aynı gerekçe.
- **Hedefin yarısı önceki dip, kalanı 1×ATR trailing.** Yarı yarıya bölünme bir tercihtir:
  tezin bittiği yerde kârın yarısını almakla trendin devamını ölçmeye devam etmek arasında,
  ikisini de ölçülebilir bırakan tek nokta odur.

### `strategies/avwap.py` (long + short)

Çapa **en son kesinleşmiş** zigzag pivotu; çapadan itibaren hacim ağırlıklı ortalama ve hacim
ağırlıklı σ. Long: çapa dip, kapanış −2σ altında ve fiyat hâlâ çapanın üstünde. Short: çapa
tepe, kapanış +2σ üstünde ve BTC 200 EMA altında. Hedefler ±1σ (yarısı) ve AVWAP çizgisi.

- **`core/indicators.py`'ye `anchored_vwap` + `typical_price` eklendi.** Hacim ağırlıklı
  ortalama ve sapması bir göstergedir; strateji içine yazılmış ikinci bir uygulama modül
  docstring'indeki itirazın tam hedefidir. Sapma popülasyon (ddof=0) tanımındadır — Bollinger
  kararıyla tutarlı olsun ve "2σ" bu depoda tek bir şey ifade etsin diye.
- **Teyit "listenin sonuncusunu at" ile değil, zigzag'ın KENDİ eşiğiyle sorulur.** Canlı uç
  çapa yapılamaz (her barda yer değiştirebilir, AVWAP'ın başlangıcı kayardı), ama kısa bacak
  elemesi canlı ucu bazen zaten silmiş olur; sonuncuyu koşulsuz atmak çapayı bir swing geriye
  — bazen serinin başındaki sentetik ankraja — kaydırırdı. Teyit ölçüsü: pivottan sonra fiyat
  `pct_threshold` kadar ters yöne dönmüş mü. Bu, pivot matematiğinin ikinci bir uygulaması
  değil, aynı eşikle yapılan bir süzmedir.
- **Eğim filtresi ATR cinsindendir.** "Sert ters" ölçüsünü yüzdeyle tanımlamak eşiği sembolün
  oynaklığına göre farklı bir şeye çevirirdi; 0.5×ATR/5 bar, projenin ortak birimini kullanır.
- **Stop çapanın ötesi ya da 1.5×ATR — hangisi genişse.** Uzak çapada mesafe tavanı aşar ve
  işlem atlanır (kural 14). Bu, modeli doğal olarak GÜNCEL çapalara yönlendirir; stop'u tavana
  çekmek ise "tez çapanın ötesinde ölür" iddiasını sessizce başka bir iddiaya çevirirdi.

### `strategies/random_ctrl.py` — kontrol grubu

Her turda evrenden bilgisiz bir çekiliş: rastgele sembol, rastgele yön, 2×ATR stop. Boyut,
maliyet, limit ve defter kuralları diğer modellerle birebir aynıdır.

- **`is_benchmark = False`.** Çıpa (buyhold) "piyasa ne yaptı"yı ölçer; kontrol "sinyalin
  kendisi bir şey söylüyor mu"yu. İkincisinin cevabı ancak yarışmacılarla AYNI sütunda,
  aynı ortalama R sıralamasında okunabilir — ayrı bir bölüme koymak onu kıyasın dışına atardı.
- **Tohum tur bazlı karıştırılır:** `random.Random(f"{random_seed}:{as_of}")`. Sabit tohumla
  süreç başına tek RNG kurmak, her koşu ayrı bir süreç olduğu için HER TURDA aynı çekilişi
  yapardı — kontrol bilgisiz değil SABİT olurdu. Tur bazlı tohum hem tekrarlanabilirliği
  (aynı `as_of` → aynı sinyal) hem turlar arası bağımsızlığı verir.
- **RNG `core/data.py`'nin jitter RNG'siyle ve global `random` ile paylaşılmaz.** Paylaşmak,
  çekilişi o turda kaç kez yeniden denendiğine — borsanın o günkü keyfine — bağlardı; aynı
  `as_of` iki koşuda farklı sembol seçebilirdi.
- **Elemede görüş yok:** yalnızca `as_of` barı olmayan ve ATR'si hesaplanamayan semboller
  (işlem kurulamaz) çekiliş dışıdır. Buraya eklenecek her filtre kontrolü sessizce bir
  stratejiye çevirir ve diğer modellerin farkı neye karşı ölçtüğü bilinmez hâle gelir.
- **Stop 2×ATR**, `trend` ile aynı: kontrolün R ölçeği bandın (1×–2.5×ATR) dışında kalsaydı
  `cost_per_r` kolonu onu haksız biçimde iyi ya da kötü gösterirdi.

### `tests/helpers_market.py` genişletildi

`market(...)` artık `funding` alıyor ve `funding_series(rates, end=as_of)` eklendi: funding
kapısı/önceliği olan iki model (failed_breakout, downtrend_rally) onsuz test edilemezdi. Seri
`as_of`ta biter — kural 12 funding tarafında da geçerlidir.

---

## 14. `ensemble`: ilk meta model ve `is_meta` yolunun canlı doğrulaması

Onuncu model kayıtlı: `strategies/ensemble.py`, projedeki tek `is_meta = True` model. Kendi
sinyal mantığı yoktur — tek girdisi `peer_signals`'tır (kural 4'ün dar istisnası). Tezi tek
cümledir: *bağımsız modellerin aynı sembolde aynı yönde üst üste binmesi, tek bir modelin
sinyalinden daha iyi bir giriş midir?*

### Kendi kapısını eklemez

Ensemble'a bir rejim ya da hacim filtresi eklemek ölçtüğü şeyi "üst üste binme + benim
filtrem" yapardı: iyi sonucun örtüşmeden mi filtreden mi geldiği ayrılamaz, tez test edilemez
hâle gelirdi. Bu yüzden model hiçbir gösterge hesaplamaz; fiyat verisine yalnızca "sembol bu
turun anlık görüntüsünde `as_of` barını taşıyor mu" kontrolü için bakar.

### Oy veren havuz: `random_ctrl` ve `buyhold` dışarıda

- **`random_ctrl` oy vermez.** Kontrol bilgisiz bir çekiliştir; oyu sayılsaydı ensemble kısmen
  rastgele olurdu. Bozulma tek taraflı da değildir: kontrolün "bilgisiz sinyal" tanımı
  ensemble üzerinden dolaylı olarak bir stratejiye bağlanır, yani iki modelin ölçümü birden
  kirlenir.
- **`buyhold` oy vermez.** Çıpa yönlü bir görüş bildirmez (kural 15): her turda aynı iki
  sinyali üretir ve oyu sayılsaydı BTC/ETH'de kalıcı bir "long" tabanı yaratırdı.
- Havuz `VOTER_POOL` sabitinde açıkça yazılıdır — "hepsi hariç şunlar" biçiminde bir dışlama
  listesi, ileride eklenecek bir modelin sessizce oy vermeye başlaması demekti.

### Eşik iki AYRI model; oylar eşit ağırlıklı

Aynı model bir sembolde aynı yönde iki sinyal üretse bile tek oy sayar: ölçülen şey modellerin
üst üste binmesi, sinyal sayısı değil. Short-only modeller (`failed_breakout`,
`downtrend_rally`) short tarafta diğerleriyle aynı ağırlığı taşır. **Ağırlıklandırma yapılmadı**
— "hangi model daha iyi oy veriyor" ayrı bir tezdir ve karıştırıldığında iyi sonucun ağırlıktan
mı örtüşmeden mi geldiği ayrılamaz.

### Zıt yön: çakışma varsa işlem yok, tek muhalif veto değil

Bir sembolde hem long hem short eşiği geçiyorsa işlem yoktur ve durum `logger.info` ile
(hangi modellerin hangi tarafta olduğu dâhil) yazılır. Oy farkına ya da bir öncelik sırasına
bakarak bir tarafı seçmek ensemble'ı gizli bir ağırlıklandırmaya çevirirdi.

Eşiği geçmeyen TEK bir karşı oy ise çakışma sayılmaz: iki long oyuna karşı bir short oyu,
konsensüsün tanımı gereği konsensüsü bozmaz. Aksi kural, modeli "hiç kimse itiraz etmedi"
tezini ölçen bambaşka bir modele çevirirdi — tek bir muhalif her işlemi veto ederdi.

### Seviyeler devralınır, yeniden hesaplanmaz

- **Stop: katılanların en GENİŞİ.** En muhafazakâr seçim budur; dar stop aynı 1R'yi daha büyük
  notional ile taşımak ve R başına daha çok maliyet ödemek demektir (kural 14). Ortalama almak
  ya da kendi ATR katını kurmak, ensemble'ı katılımcıların hiçbirinin savunmadığı ÜÇÜNCÜ bir
  modele çevirirdi. En geniş stop `max_stop_atr_multiple` tavanını aşarsa işlem
  `core/engine.py` tarafından atlanır ve loglanır — bilinçli sonuç: konsensüsün taşıdığı risk
  ölçülemiyorsa işlem de açılmaz.
- **Hedef: en YAKIN, tek TP, fraction 1.0.** Kısmi çıkışları birleştirmek (fraction toplamak,
  kademe sıraya dizmek) katılımcıların hiçbirinin planına benzemeyen bir çıkış üretirdi.
  Hiçbir katılımcının hedefi yoksa sinyal hedefsiz gider.
- **`trailing_atr` istenmez.** Trailing bir çıkış tezidir ve katılımcılar arasında farklıdır;
  üçüncü bir birleştirme kuralı, ölçülen şeye ensemble'ın kendi katkısını eklerdi.

### `reason` ayrıştırılabilir bir kuyrukla biter

`... | voters=squeeze,trend | stop_from=squeeze | tp_from=none`. Karar 12'deki `| confidence=`
ile aynı gerekçe: hangi model bileşiminin kazandığı defterden (`trades.csv`) gruplanarak
sorulabilsin, ölçüm tablosunun kolonları hiç kirlenmesin. Oy listesi ve `stop_from`/`tp_from`
alfabetik seçilir — eşitlikte etiketin akranların çalışma sırasına göre değişmemesi için.

### `is_meta` yolu canlı doğrulandı

Bu yol bugüne kadar yalnızca sahte modellerle test edilmişti. `tests/test_ensemble.py` gerçek
Ensemble'ı `core/engine.py` içinde koşturur ve şunları çiviler:

- normal modeller bittikten SONRA meta geçişi çalışır; iki oy tek konsensüs sinyaline dönüşür
  ve bir sonraki barda dolar (kural 13),
- normal modeller `peer_signals=None` alır,
- metalar birbirini görmez: ensemble'ın yanında koşan ikinci bir meta'nın gördüğü küme yalnızca
  normal modellerdir (sıralamadan bağımsız), ve bir meta'nın sinyali ensemble'a oy olarak
  girmez,
- akran kümesi derin kopyadır ve `MappingProxyType`'tır (yazılamaz).

Kod tarafında bir değişiklik gerekmedi: `_collect_signals` `peer_base`'i meta döngüsünden ÖNCE
dondurduğu için metaların çıktısı akran kümesine hiç girmiyor, her meta kendi derin kopyasını
alıyor.

### Bilinen ve kabul edilen kenar durum: soğuk başlangıç

`core/engine.py` sinyal üretimini yalnızca `as_of` barına BU turda ulaşan modellere açar
(`reached_as_of`, aynı turun iki kez koşmasına karşı). Ensemble defteri boş olarak listeye
eklendiğinde, diğer modeller o barı zaten işlemişse ensemble tek başına "taze" olur ve akran
kümesi BOŞ gelir. Bu tek turluk bir durumdur ve sessiz değildir: model "havuzdan hiç sinyal
gelmedi" diye loglar. Guard'ı gevşetmek (metaları tazelik kontrolünden muaf tutmak) çift sinyal
riskini geri getirirdi; bir turluk boş oy ondan ucuzdur.

## 15. Dashboard ve Telegram özeti: raporlama katmanı

Ölçüm tamamdı, okunması değil. Bu adım hiçbir ölçüm kuralına dokunmaz — tek işi defterde
ve `docs/data/metrics.json`'da zaten duran sayıları telefondan okunabilir hâle getirmek.
Ayrım bilinçlidir ve aşağıdaki kararların çoğu ondan türer: **raporlama katmanı ölçümü ne
değiştirebilir ne de düşürebilir.**

### Neden ayrı bir modül: `core/report.py`

Dashboard'un ihtiyaç duyduğu her şey `core/metrics.py`'ye eklenebilirdi. Eklenmedi, çünkü
metrics bir ÖLÇÜM modülüdür ve sunum kararları (kaç işlem gösterilir, eğri kaç noktaya
seyreltilir, açık pozisyon nasıl işaretlenir) oraya sızsaydı ölçümün tanımı sunuma
bağlanırdı. Bölüşüm şöyle:

- **`core/metrics.py`** — havuzlanmış yön istatistikleri, kabul bayrakları, getiri
  korelasyonu. Üçü de metriktir: girdisi defter satırı, çıktısı sayı.
- **`core/report.py`** — bu sayıların yanına bağlam koyar (açık pozisyonlar, son işlemler,
  özsermaye eğrileri, 24 saatlik hareket) ve JSON bölümlerini kurar. Sunum sabitleri
  (`RECENT_TRADE_LIMIT`, `EQUITY_MAX_POINTS`) burada durur, `config.yaml`'da değil:
  config.yaml "tüm modeller için birebir aynı ölçüm koşulu" sözleşmesidir (kural 6) ve kaç
  satır çizildiği o sözleşmenin parçası değildir.

`main.py` ince kaldı: tek bir `build_dashboard(...)` çağrısı, defter kapsamı hâlâ açıkken.
Böylece `--dry-run` sayfayı da turun geçici kopyasından üretir, gerçek defterden değil.

### Havuz modele değil İŞLEME oy verir

"Short işlemler daha mı başarılı" sorusunun cevabı model başına ortalama R'lerin ortalaması
olamaz: 2 işlemlik bir model 200 işlemlik bir modelle eşit ağırlık alır ve sonuç işlemlerin
değil model sayısının ortalaması olur. Bu yüzden `pooled_direction_stats` tüm yarışmacıların
satırlarını tek havuzda birleştirip `direction_stats`'ı ONUN üzerinde çalıştırır.

Havuza kimin gireceğine `core/report.py` karar verir, `core/metrics.py` değil: modül defterin
içeriğinden başka bir şey varsaymamalı. Referans çıpası havuz dışıdır (stop'suz işlemin R'si
yoktur, kural 15); `random_ctrl` içeridedir, çünkü boyutlandırması ve stop ölçeği
yarışmacılarla birebir aynıdır.

### Kabul çıtası: neden ÜÇ bayrak, neden tek bir puan değil

Tek bir "skor" üretmek üç farklı soruyu tek sayıya yığardı ve hangi kapıda takılındığı
görünmez olurdu. Üç kapı ayrı ayrı raporlanır (tanımlar: `CLAUDE.md > Kabul Çıtası`):

- **Ö (örneklem)** — iki işlemle +3R yapmış bir model, bu kapı olmadan tablonun başına
  oturur.
- **B (band)** — kural 14'ün maliyet ölçeği kuralının denetlenebilir hâli.
- **E (edge)** — ortalama R pozitif, kontrol grubu aşılmış ve çıpa geçilmiş.

`E`'nin üç koşulu ayrı bayrak yapılmadı: üçü aynı soruyu farklı yerlerden soruyor ("bu
sonuç sinyalden mi geliyor, piyasadan ve şanstan mı"), ve rozet sayısını altıya çıkarmak
mobil tabloda okunaksız bir sütun üretirdi. Hangi koşulun düştüğü rozetin tooltip'inde ve
Telegram özetinin "en yakını" satırında yazılı.

#### Bandın çapası neden medyan

Kural 14'ün bandı **ATR katı** cinsindendir; `trades.csv`'de ATR yoktur ve işlem kapandıktan
sonra "giriş anındaki ATR" geri hesaplanamaz — geriye dönük yeniden hesaplamak look-ahead
için yeni bir kapı açardı. Sabit bir yüzde eşiği de kullanılamazdı: %3 stop, düşük volatil
bir dönemde geniş, yüksek volatil bir dönemde dardır.

Medyan bu iki sorunu birden çözer: aynı evrende aynı barlarda işlem yapan modellerin ortak
volatilite ölçeğini taşır ve rejim değiştikçe kendisi de kayar. Band `medyan/√oran ..
medyan×√oran` olarak kurulur — uçtan uca tam `stop_band_ratio` kadar geniş, yani kural 14'ün
1×–2.5× bandıyla aynı genişlikte.

#### Çıpayı geçme koşulu ve "en yüksek çıpa" kuralı

Kural 15'in sorusu: "model piyasayı yendi mi, yoksa yalnızca yükselen bir piyasada mı
durdu?" Birden çok çıpa varsa **en yükseği** zemindir. Alternatif (ilk çıpayı ya da
ortalamayı almak) çıtayı, hangi çıpaların listede olduğuna göre sessizce indirirdi.

Kontrol ya da çıpa kümede yoksa koşul değerlendirilemez ve `edge` geri kalanlara düşer —
ama `logger.warning` ile. Eksik bir çıta, geçilmiş bir çıta gibi görünmemelidir.

### Korelasyon: örtüşme par bazında, yetersizse `nan`

Modellerin bar getirilerinin Pearson korelasyonu, "sıralamanın ne kadarı gerçekten farklı
fikirlerden geliyor" sorusunu sorar: yüksek korelasyonla yarışan iki model bağımsız iki
ölçüm değil, aynı ölçümün iki kopyasıdır.

Kesişim **par bazında** alınır. Global kesişim kullanılsaydı, yarışmaya sonradan eklenen bir
modelin kısa geçmişi TÜM çiftleri onun uzunluğuna kırpardı. Örtüşme `min_overlap`'in
altındaysa hücre `nan`'dır ve sayfada boş kalır: `0.0` "ilişkisiz" demektir, "ölçülemedi"
değil (aynı gerekçe `core/metrics.py`'nin tamamında geçerli).

Korelasyon matrisi referans çıpasını DA içerir — orada ölçülen R değil bar getirisidir ve
"modeller piyasadan ne kadar ayrışıyor" sorusunun cevabı tam olarak çıpayla karşılaştırmayı
gerektirir.

### `docs/index.html`: sıfır bağımlılık

Sayfa tek dosyadır: framework yok, CDN yok, build adımı yok. Gerekçe, sayfanın ölçümün YÜZÜ
olup parçası olmamasıdır: bir grafik kütüphanesinin sürüm değişikliği ya da CDN kesintisi
raporu okunamaz hâle getirebilmemeli. Ölçüm zaten defterde ve JSON'da duruyor.

- **Renk ataması kimliğe bağlıdır, sıraya değil.** Filtre ya da sıralama değiştiğinde hiçbir
  modelin rengi değişmez; "trend maviydi" diye öğrenen okuyucu yanılmamalı.
- **Nötr griler bir zemindir, bir fikir değil.** Referans çıpası ve kontrol grubu kategorik
  renk slotu almaz — grafikte de tabloda da hue'suz durur.
- **Dokuzuncu model üretilmiş bir renk almaz.** İlk slotun rengini KESİKLİ çizgiyle (tabloda:
  içi boş kare) tekrar kullanır; kimliği renk + desen birlikte taşır. Sekizden sonra üretilen
  renkler renk körlüğünde birbirinden ayrılamaz.
- **Yön kimliği ile sayının işareti farklı renk ailelerindedir.** Short'a "negatif kırmızı"yı
  vermek, kârdaki bir short'u da kırmızı gösterip "kötü" diye okuturdu. Yön kimliği kategorik
  (mavi/turuncu), işaret ise diverging çift (mavi/kırmızı).
- **Tanımsız metrik `—`'dir.** JSON'da `null` (bkz. `main.py::_jsonable`), sayfada tire.
  `0` yazmak "ölçüldü, sıfır çıktı" ile "ölçülemedi"yi aynı hücreye yığardı.
- **Açık pozisyonun PnL'i çıkış maliyeti hariçtir** ve sayfa bunu söyler: pozisyon kapanmadı,
  çıkış fiyatı da komisyonu da bilinmiyor. Mevcut fiyattan kapanmış saymak, deftere hiç
  girmeyecek bir sayıyı kapanmış işlemlerin yanına koyardı.
- **Fiyatı olmayan sembol kâr/zararda gösterilmez:** giriş fiyatından işaretlenir (aynı kural
  `core/portfolio.py::_mark`'ta) ve satır `*` ile işaretlenir.

`main.py::_write_metrics` artık `_jsonable`'ı yükün TAMAMINA uygular, yalnızca model
tablosuna değil. Dönüşümü tek bir dala uygulamak, yeni bir bölüm eklendiği gün sessizce
GEÇERSİZ JSON üretirdi ve sayfa veriyi hiç çizemeden ölürdü.

### Telegram: kapı `as_of`'ta, hata yutuluyor

"Günde bir kez, 20:00 UTC turunda" kararını script verir, cron değil. 4H barlarda gün içinde
altı tur koşar; "günde bir"in tekrarlanabilir tanımı **"as_of'u 20:00 olan tur"**dur. Duvar
saatine bakan bir kapı, cron geciktiğinde ya da tur elle tekrarlandığında özeti ya iki kez
ya hiç yollardı.

Saat kapısı tek başına yetmiyor: tur düşerse depodaki `metrics.json` bir önceki turdan kalır
ve DÜNKÜ 20:00 raporu bugün tekrar yollanabilirdi. Bu yüzden `generated_at` üzerinden bir
tazelik kapısı daha var (`MAX_REPORT_AGE_HOURS = 3`; bar 4H olduğu için taze bir rapor her
zaman bundan gençtir).

**Script her yolda 0 döner.** Eksik token, ağ hatası, Telegram 4xx'i, bozuk JSON — hepsi
loglanır ve geçilir. Workflow adımı ayrıca `continue-on-error: true` taşır ve defter
commit'inden SONRA gelir. Üç ayrı emniyet, tek bir gerekçeyle: özet bir bildirimdir, ölçümün
parçası değil. Telegram'ın kesintisi yüzünden turun kırmızı dönmesi, defterin commit'lenip
commit'lenmediğine dair gerçek sinyali gürültüye boğardı.

Mesaj `parse_mode: HTML` ile yollanır, Markdown ile değil: model adları alt çizgi içerir
(`failed_breakout`, `downtrend_rally`) ve Markdown'da alt çizgi italik açar — mesaj ya bozuk
biçimlenir ya Telegram 400 döner. HTML'de kaçırılması gereken üç karakter vardır ve
`html.escape` hepsini kapatır.

Sıralamaya yalnızca ortalama R'si ÖLÇÜLEBİLEN modeller girer; kaç modelin ölçülemediği ayrı
bir satırda söylenir. Hiç kapanmış işlemi olmayan bir modeli "ilk 3"e koymak, ölçülmemiş bir
modeli ölçülmüş bir modelin önüne geçirirdi. Kontrol grubu sıralamada `⚠` ile anılır:
bilgisiz çekilişin ilk üçte olması gizlenecek bir kusur değil, raporlanacak bir sonuçtur.

### Referans satırlarının görsel ayrışması — kısmi bir sapma

İstenen "referans satırları (`buyhold`, `random_ctrl`) görsel olarak ayrışsın, yarışmacı
değiller" idi. Görsel ayrışma ikisine de uygulandı (nötr gri, ayrı etiket), ama
**`random_ctrl` sıralamada bırakıldı.** Gerekçe `README.md`'de zaten yazılı: `random_ctrl`
`is_benchmark` DEĞİLDİR — boyutlandırması, stop ölçeği ve limitleri yarışmacılarla birebir
aynıdır, tek farkı sinyalin bilgisiz olmasıdır. Onu ayrı bir bölüme almak "edge'in referansı
ancak aynı sütunda okunabilir" ilkesini bozardı. Yalnızca `buyhold` ayrı REFERANS bölümünde
durur (kural 15). Ayrım tabloda KONTROL / REFERANS etiketleriyle görünür.

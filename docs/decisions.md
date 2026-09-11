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

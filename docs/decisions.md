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


## 5. `slippage_base` adlandırması, R başına maliyet kolonları ve stop mesafesi bandı

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

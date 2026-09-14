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

Ölçüm **iki KATMANDA** yürür ve ikisi de aynı çekirdeği kullanır (bkz. "Katmanlar"):
`base` 4 saatlik ana yarışma, `scalp` 15 dakikalık scalp katmanıdır. Katman ölçümün
koşullarını (bar, sembol evreni, model listesi, defter, rapor dosyası) değiştirir;
kurallarını — maliyet, risk, likidasyon, dolum, metrik tanımları — DEĞİŞTİREMEZ. İki
katmanın sonuçları aynı tabloda sıralanmaz: zaman dilimi farkı doğrudan kıyası yanıltıcı
yapar.

## Klasör Yapısı ve Modül Sorumlulukları

| Yol | Tek Sorumluluk |
|---|---|
| `main.py` | Bir turu uçtan uca çalıştıran giriş noktası: katmanı çöz -> veri çek -> `as_of` -> katmanın modellerini çalıştır -> metrikleri üret -> katmanın rapor dosyası. `--layer` hangi katmanın koşacağını seçer (varsayılan `base`); İKİ katman da bu tek giriş noktasını ve tek çekirdeği kullanır — ayrı bir giriş noktası orkestrasyonu (model kurulumu, hata izolasyonu, dry-run kopyası, yük yazımı) kopyalar ve iki katmanın sessizce ayrışmasına kapı açardı. İnce bir orkestrasyon katmanıdır, iş mantığı taşımaz. `--dry-run` deftere yazmadan raporlar. Model KURULUMU burada izole edilir (tanınmayan/patlayan model atlanır, koşu hata koduyla biter); defter ve veri hataları izole edilmez — onlar ölçüm hatasıdır, tur düşer. |
| `config.yaml` | Evrensel ayarlar: sembol evreni, zaman dilimi, başlangıç bakiyesi, çalışma sıklığı, funding parametreleri ve tüm risk/maliyet sabitleri — `risk_per_trade`, `leverage_cap`, `max_positions`, `max_short_positions`, `fee_rate`, `slippage_base`, `slippage_short_stop`, `maintenance_margin`, `max_stop_atr_multiple`, `random_seed`. Tüm modeller için tek kaynak; hiçbir modül bu değerlerin kendi kopyasını taşımaz. Değerler için bkz. "config.yaml Değerleri". |
| `core/layers.py` | Katman çözücü: `config.yaml > layers.<ad>` bloğunu kök ayarların üzerine derin birleştirmeyle bindirir ve katmanın kimliğini (defter kökü, rapor dosyası, sabit evren, saklama penceresi, kırılımlar) verir. Çözülmüş config'te `layers` anahtarı YOKTUR — çekirdek modüller tek bir "şimdi geçerli" değer görür. Eksik katman anahtarı `ConfigError`; tanınmayan katman adı sessizce `base`e düşmez. |
| `core/tags.py` | Defter `reason` alanındaki `\| anahtar=değer` etiketlerinin tek tanımı. Etiketi yazan taraf strateji, okuyan taraf `core/metrics.py`'dir; format tek yerde durur. `parse_tag` bulamadığı etikette `TagError` fırlatır — kol kırılımının anlamı "her işlem bir kola aittir" varsayımına dayanır ve etiketsiz satırı atlamak kırılımı sessizce eksiltirdi. |
| `core/config.py` | `config.yaml`'ı okuyan tek kapı. Eksik anahtarda `ConfigError` fırlatır; hiçbir varsayılan değer taşımaz — sessiz varsayılan, modellerin farklı maliyet/risk varsayımlarıyla yarışması demektir. |
| `core/data.py` | Piyasa verisi çekme/önbellekleme. Borsadan OHLCV + funding geçmişini çeker, `MarketData` üretir. Kapanmamış barı atmak (kural 12) ve `as_of`'u BTC referansından belirlemek burasının işidir; `as_of` barına sahip olmayan semboller o tur dışlanır ve loglanır. Strateji mantığı barındırmaz. |
| `core/engine.py` | Orkestrasyon: her BARI **iki geçişli** yürütür — önce normal modeller, sonra meta modeller (kural 4) — ürettikleri `Signal` listelerini `portfolio`'ya iletir. Trailing stop mantığı da burada. Zamanlama/akış kontrolü burada, iş mantığı değil. Doldurulamayan emirleri sebep koduna göre sayıp tur raporuna yazar (bkz. kural 15); kuyruğa GİREN sinyalleri de aynı raporda döker (`EmittedSignal`: bar, dolum barı, o barın kapanışı, stop/hedef/R:R) — `signals` bir sayıdır ve hangi sinyalin üretildiğini söylemez, oysa anlık bildirim (`scripts/telegram_signals.py`) tam olarak onu sorar ve defterde cevabı yoktur (dolum bir sonraki barda, kural 13). Kayıt salt denetim izidir: ölçüme girmez, sinyalleri ve sıralarını etkilemez. Atlanan turları telafi eder: son işlenmiş bardan `as_of`'a kadarki TÜM barları sırayla ilerletir (yalnızca son bara atlamak, atlanan barlardaki stop/TP/likidasyon kontrolünü hiç yapmamak olurdu) ve `signals_per_bar` açıkken her telafi barı KENDİ sinyalini de üretir (bkz. "Telafi edilen barlarda sinyal"). Anlık görüntü son işlenmiş bara kadar geri gitmiyorsa telafi mümkün değildir; o barlar `missing_bars` olarak sayılır ve loglanır — atlama sessiz olamaz. |
| `core/portfolio.py` | Pozisyon açma/kapama, boyutlandırma, **likidasyon kontrolü**, stop/TP tetikleme, bakiye güncelleme. Her strateji için izole hesap durumu tutar. Pozisyon boyutlandırmasının **tek yetkili kaynağı.** Her barda sıra: önce `maintenance_margin` ile likidasyon kontrolü (mum içi `high`/`low` kullanılarak), **sonra** stop/TP kontrolü. Likidasyon stop'tan önce gelir; likide olan pozisyon stop'a hiç ulaşmaz. Kapanan işlemin `notes` kuyruğuna çıkışın ALT sebebini `| exit_rule=<kural>` etiketiyle yazar (bkz. kural 13c). |
| `core/funding.py` | Açık pozisyonlara funding/borrow maliyeti uygular. Borsa kurallarını simüle eder. |
| `core/metrics.py` | Performans metrikleri. **Birinci sınıf metrik: işlem başına ortalama R** (`PnL / risk_amount`) — bileşiklenmeden bağımsız olduğu için "bu model iyi mi" sorusuna toplam getiriden daha temiz cevap verir; tablo da ona göre sıralanır. Toplam getiri, Sharpe, max drawdown ve win-rate ikinci sırada raporlanır, atılmaz. **Her işlem metriği long ve short için AYRI hesaplanır ve ayrı raporlanır** (toplam değer de verilir, ama ayrışma yerine geçmez); özsermaye eğrisinden gelenler tek bakiye olduğu için hesap düzeyinde kalır. Ayrıca **maliyet ölçeği kolonlarını** (`avg_stop_distance_pct`, `cost_per_r`) model ve yön bazında raporlar — bkz. "Rapor Kolonları". Projenin ana sorusu "short işlemler daha mı başarılı" olduğu için bunların hiçbiri opsiyonel değil. **Ölçümün birimi POZİSYONDUR, defter satırı değil:** `merge_fills` aynı pozisyonun dolumlarını (kısmi çıkış ve `fraction < 1.0` olan take-profit'ler) tek ölçüm satırına indirger ve R'yi `Σpnl / Σrisk` olarak kurar — bkz. "Dolum ve pozisyon". Salt okunur — ledger'ı değiştirmez. |
| `core/report.py` | Dashboard yükü: `docs/data/metrics.json`'un tablo dışında kalan bölümleri (özsermaye eğrileri, açık pozisyonlar, son işlemler, model başına son 100 kapanmış işlem, son 24 saatin hareketi, havuz ve kabul bayraklarının toplanması). Salt okunur; hiçbir şey hesaplamaz ki `core/portfolio.py` zaten hesaplamış olsun. Tek istisna açık pozisyonun güncel PnL'idir ve kapanış formülünün aynı parçalarından kurulur (brüt − giriş komisyonu + funding; çıkış maliyeti YOK). Açık pozisyon satırı ayrıca çıkış yönetiminin DURUMUNU taşır — `breakeven_at_r`/`breakeven_done`, `partial_tp`/`partial_done`, `trail_giveback_pct`/`trailing_active`, `stop_rule`/`stop_moved` — ve İSTEK ile OLAY ayrı alanlardadır: mekanizmayı bildiren ama henüz tetiklenmemiş bir pozisyonu "yönetildi" göstermek, modeller 13/14/15'in ölçtüğü katkıyı yanlış okuturdu. Sunum sabitleri (kaç işlem gösterilir, eğri kaç noktaya seyreltilir) burada durur, `config.yaml`'da değil. |
| `core/ledger.py` | Her işlemi ve bakiye değişimini kalıcı, append-only biçimde katmanın defter kökü altına yazar. Sistemin denetim izi (audit trail) burasıdır. **Tek istisna `compact_equity`:** katmanın saklama penceresinden eski `equity.csv` satırlarını günlük özete indirir (bkz. "Katmanlar > Saklama penceresi"). `trades.csv` için istisna YOKTUR — bir işlem satırı hiçbir gerekçeyle değişmez veya silinmez. |
| `core/validate.py` | Her `Signal`in motora girmeden geçtiği tek doğrulama kapısı: izinli yön, stop/TP geometrisi, sıfıra bölme, fraction toplamı, sembol evreni, çıkış yönetimi alanlarının tutarlılığı (`trailing_atr` ile `trail_giveback_pct` aynı anda kullanılamaz). Ayrıca `validate_model`: model bayrak/limit bildiriminin kapısı (`ModelLimits` yalnızca `is_replica`, kaldıraç tavanı `REPLICA_LEVERAGE_CAP`), `strategies/registry.py` kurulumda çağırır. Geçersizde `ValueError`/`NotImplementedError` fırlatır, sessizce filtrelemez. |
| `strategies/base.py` | Tüm stratejilerin uyacağı soyut arayüz (`Strategy`, `Signal`, `Position`, `ExitInstruction`, `MarketData`). Mantık içermez, yalnızca sözleşme. |
| `strategies/scalp/arms.py` | Scalp katmanının **beş ortak kolu** (VWAP geri çekilme, açılış aralığı kırılımı, RSI(2) dönüşü, momentum patlaması, funding sıçraması fade'i). İki model de bu tek kopyayı görür. Stop mesafesi her kolda aynıdır (`stop_atr_multiple × ATR`) — kollar stop ölçeğinde ayrışsaydı kol tablosu bir sinyal değil maliyet karşılaştırması olurdu. Hedef ise projeksiyon (`target_reward_risk × stop`) ile kolun yapısal engelinin YAKIN olanıdır. |
| `strategies/scalp/model.py` | Scalp modellerinin ortak gövdesi: stop tabanı (%1), hedef/stop kapısı (1.5R), zaman stop'u (16 bar), sinyal kurulumu, kol etiketi. Alt sınıfın değiştirebileceği YALNIZCA üç nokta vardır ve her biri ölçülen bir eksene karşılık gelir: `choose_arm` (kol seçimi), `exit_management` (çıkış yönetimi), `rng_identity` (çekiliş kimliği). Fark bu üç noktaya indirgenmezse modeller arası ortalama R farkı bir eksenin ölçüsü olmaktan çıkar. |
| `strategies/scalp_bandit.py` | **Model 11:** Thompson sampling ile kollar arası tahsis. Posterior yalnızca KAPANMIŞ işlemlerin gerçekleşmiş R'sinden beslenir ve her turda defterden sıfırdan kurulur (ayrı durum dosyası yoktur — ikinci bir doğruluk kaynağı olurdu). Isınma 20 işlem/kol, taban tahsis %5, kayan pencere 100 işlem. |
| `strategies/scalp_fixed.py` | **Model 12 (KONTROL):** aynı beş kol, eşit ağırlıklı çekiliş, öğrenme yok. Model 11'in null hipotezi; `observe_closed_trades`ı bilinçli olarak UYGULAMAZ, yani geçmişe erişimi hiç yoktur. |
| `strategies/exit_management.py` | Üç aşamalı çıkış yönetiminin TEK tanımı (modeller 13, 14, 15 aynı kopyayı okur): breakeven -> kısmi çıkış + stop kaydırma -> geri verme takibi. Yalnızca config'i okuyup `Signal` alanlarına çevirir; uygulama `core/engine.py` (stop hareketleri) ve `core/portfolio.py`dedir (kısmi dolum) — kural 9'un `trailing_atr` için koyduğu sınırın aynısı. Üç dosyaya kopyalansaydı model 15 ile `scalp_fixed` arasındaki fark "yönetimin katkısı" olmaktan çıkar, "iki ayrı yönetimin farkı" olurdu. |
| `strategies/vwap/signal.py` | Model 13 ve 14'ün ORTAK sinyali: gün-çapalı VWAP'ten `band_mult × sapma` kadar uzaklaşıp DÖNMEYE BAŞLAYAN bar. Dönüş şartı zorunludur — yalnızca "bant dışında" olmak, güçlü bir trendde her barda aynı sinyali üretirdi. Modül kurulumun YERİNİ verir; stop ve hedefi her model kendi kuralıyla kurar (13 öğrenilen çarpanlar, 14 sabit çarpan + VWAP kırpması). Aday BULAMADIĞI barı da kaydeder (`Survey`): her tarama eleme sebeplerine göre sayılır ve loglanır — kural 15'in "ret sebep koduyla kaydedilir" şartının bu koldaki karşılığı. Model 13'ün hiç kapısı olmadığı için `signals=0` demek "hiç aday yok" demektir ve sayım olmadan bu, sessizce bozulmuş bir sinyal modülünden ayırt edilemezdi. Sayım salt denetim izidir: hangi adayın üretileceğini ve sıralarını etkilemez. |
| `strategies/vwap_clone.py` | **Model 13 (KOPYA, `is_replica=True`):** dış bir sistemin kurallarını birebir yeniden üretir. Sabit teminat × 10x (`notional_fraction` + `ModelLimits.leverage`), üç aşamalı çıkış yönetimi, kendi limitleri (5 pozisyon, yönde 3, portföy riski %8), 13 sembollük kendi evreni, epsilon-greedy parametre öğrenimi (4×3 = 12 kombinasyon, sembol bazlı, 3 örnek altında genele düşer). Ev kapıları (%1 stop tabanı, 1.5R) UYGULANMAZ — kaynak sistemde yok. Yarışmacı değildir. |
| `strategies/vwap_managed.py` | **Model 14:** model 13'ün sinyali, EV kurallarıyla — `sizing="risk"`, katmanın `leverage_cap`i, %1 stop tabanı ve 1.5R kapısı geçerli, parametre öğrenimi YOK (sabit çarpanlar config'te). Barda tek sinyal. Tam yarışmacı; kıyas hedefleri model 13 (ev kurallarının katkısı) ve `scalp_fixed`. |
| `strategies/scalp_managed.py` | **Model 15:** `scalp_fixed`in BİREBİR ikizi (aynı beş kol, aynı eşit ağırlıklı çekiliş — `choose_arm` miras alınır, kopyalanmaz), tek farkı üç aşamalı çıkış yönetimi. Çekiliş kimliği (`rng_identity`) bilinçli olarak `scalp_fixed` ile PAYLAŞILIR: iki model her turda aynı kolu ve aynı sembolü seçer, aradaki ortalama R farkı yalnızca yönetimden gelir (eşleştirilmiş deney). |
| `strategies/buyhold.py` | **Referans çıpası** (kural 15), yarışmacı değil: BTC %50 / ETH %50, 1x, stop'suz, bir kez alınır ve hiç satılmaz. `is_benchmark = True`. |
| `strategies/registry.py` | Model adı -> strateji sınıfı eşlemesi. `config.yaml`'ın `models` listesi buradan çözülür; tanınmayan ad sessizce atlanmaz. |
| `strategies/*.py` (ileride) | `Strategy`'den türeyen, yalnızca `generate_signals` uygulayan bağımsız, birbirinden habersiz modüller. |
| `data/` | Çalışma zamanı veri deposu (depoya girmez): `data/universe.json` ve `data/cache/<sembol>_<bar>.parquet`. Mum/funding önbelleği burada tutulur, her koşuda yalnızca eksik barlar çekilir. |
| `ledgers/` | Her stratejinin işlem/bakiye kayıtlarının tutulduğu çıktı klasörü (strateji başına dosya/alt klasör). |
| `docs/` | Tasarım kararları, metrik tanımları, kabul kriterleri. |
| `docs/index.html` | GitHub Pages dashboard'u: harici framework/CDN yok, build adımı yok (ortak tasarım dili ve yardımcılar `docs/shared.css` + `docs/shared.js`'te). Tek veri kaynağı `docs/data/metrics.json`. **İki seviye:** (1) genel bakış — üç özet kartı, LONG vs SHORT paneli (ana soru), tez tipine göre gruplanmış model kartları (ort. R, getiri, işlem sayısı, mini eğri, kabul rozetleri, rütbe), özsermaye eğrileri (tıklayınca izole), modeller arası getiri korelasyonu; (2) model detayı — üst şerit metrikler + rozet gerekçeleri, modelin long/short kırılımı, açık pozisyonlar, sayfalı kapanmış işlem listesi (20'şer, yön filtresiyle), modelin kendi eğrisi. Detayın adresi `#model=<ad>` hash'idir: geri tuşu, yer imi ve paylaşılan link çalışır. Koyu tema, mobil öncelikli: yatay kaydırma yoktur, geniş tablolar dar ekranda kart düzenine döner, dokunma hedefleri en az 44px. Süs katmanıdır: ölçüm defterde ve JSON'dadır, sayfa yalnızca onu çizer. Satır satır defter görünümü bu sayfada DEĞİL, `docs/positions.html`'dedir. |
| `docs/positions.html` | Yoğun defter görünümü: **tüm katmanlar ve tüm modeller tek sayfada**, model/katman/yön/sonuç/tarih filtreleriyle. İki tablo — açık pozisyonlar (anlık K/Z, anlık R, TP/SL, çıkış yönetimi rozetleri, kaldıraç, margin, riske edilen tutar, birikmiş funding, tam `reason`) ve kapanmış işlemler (sayfalı, 50'şer; sonuç = KAZANÇ/KAYIP + çıkış sebebi). Dashboard "hangi model önde" der, burası "tam olarak ne açık, tam olarak ne kapandı" der. **Kısmi çıkış ve fraksiyonel hedef satırları ayrı satır olarak görünür ama istatistiğe GİRMEZ** ve "DİLİM" etiketiyle durur — tamamlanmış işlem değil, aynı pozisyonun dilimidirler. **Sayfa ortalama R'yi ve kazanma oranını HESAPLAMAZ** (kural 7): ikisini de yükten okur (`docs/shared.js::statsSlice` -> `core/metrics.py`). İkinci bir hesap yolu bugün hizalansa bile yarın ayrışır ve aynı model iki sayfada iki farklı kazanma oranı gösterirdi. Katman etiketi her satırda durur ve ölçüm şeridi **katman başına AYRI bir kart** verir — `layer=all` dâhil hiçbir görünümde iki katmanın R'si tek sayıda toplanmaz: katmanlar arası kıyas burada da yapılmaz. Yükün cevaplayamadığı filtreler (sonuç, tarih) yalnızca tabloyu daraltır ve bu SÖYLENİR. 480px altında tablolar kart düzenine döner. |
| `docs/shared.css` | İki sayfanın ORTAK tasarım dili: renk paleti, tipografi, kart/tablo düzeni, rozetler, filtre ve sayfalama kontrolleri, dokunma hedefi tabanı. Sayfaya özgü görseller (eğri grafiği, ısı haritası, model kartları) kendi dosyasında kalır. Tek kopya olmasının gerekçesi biçimlendirmenin ölçümün parçası olmasıdır: aynı sayının iki sayfada farklı görünmesi okuyucuya iki ayrı ölçüm gibi gelir. |
| `docs/shared.js` | İki sayfanın ORTAK yardımcıları: biçimleyiciler (`num`, `price`, `qty`, `usd`, `pct`, `fullTs` — tanımsız metrik `—` olur, `0` DEĞİL), çıkış sebebi/alt sebep etiketleri, renk-kimlik ataması (`assignStyles`), yük okuma (`fetchPayload`) ve **ölçüm okuma (`statsSlice`)** — iki sayfanın da ortalama R / kazanma oranı / işlem sayısını aldığı TEK yol; `model` verilmezse havuz (yalnızca yarışmacılar) okunur. Klasik script; build adımı, bundler ve CDN yok. |
| `scripts/telegram_report.py` | **Günlük PERFORMANS özeti** (base katmanı). `as_of` saati 20:00 (UTC) olan turda yollanır; token'lar ortamdan (`TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`) okunur. **Her yolda 0 döner** — eksik token, ağ hatası, Telegram 4xx'i, bozuk JSON: hepsi loglanıp geçilir. Özet bir bildirimdir, ölçümün parçası değil; Telegram kesintisi turu kırmızıya çeviremez. |
| `scripts/telegram_signals.py` | **Anlık SİNYAL bildirimi** (scalp katmanı). Günlük özetten AYRIDIR ve ona dokunmaz: tetikleyen tek olay yeni sinyaldir — pozisyon kapanışı, funding tahakkuku ve bar ilerlemesi mesaj üretmez. Kaynağı turun kendi raporudur (`round.models[].emitted`, bkz. `core/engine.py::EmittedSignal`); defteri okumak yetmezdi, çünkü sinyalin haber değeri olduğu an henüz DOLMAMIŞTIR (kural 13). **Üç filtre:** yalnızca `as_of` barının sinyalleri (telafi barlarınınki deftere yazılır ama bildirilmez — emri çoktan dolmuştur), aynı (model, sembol, yön) için 4 bar susturma (durum: `state/telegram_scalp.json`), 5'ten fazla sinyalde tek toplu mesaj. Mesaj ZORUNLU bir uyarı satırı taşır: emir bir sonraki barın açılışından dolacağı için okuyucunun girişi bottan farklı fiyattan olur. `telegram_report.py` ile aynı söz: **her yolda 0 döner** ve aynı secret'ları kullanır. |
| `tests/` | Her `core` modülü ve her strateji için bağımsız birim testleri. |
| `state/` | Bildirim bookkeeping'i, ölçüm DEĞİL: `state/telegram_scalp.json` yalnızca "hangi kurulum en son hangi barda bildirildi" bilgisini tutar (susturma penceresi). Defterden ayrı durur çünkü defter denetim izidir (kural 1) ve bu dosya silinse ölçüm hiç değişmez — en kötü ihtimalle bir mesaj tekrar eder. Koşular arası commit edilir (runner her koşuda sıfırdan kurulur), ama KENDİ adımında: defter commit'ine katmak, ağ erişimi olan bildirim adımını turun kaydedilmesinin önüne koymayı gerektirirdi. |
| `ledgers_scalp/` | Scalp katmanının defteri. İki katman asla aynı defteri paylaşmaz: paylaşsalardı 15 dakikalık turlar 4 saatlik modellerin `last_processed_bar` değerini ileri taşır ve iki ölçüm birbirinin bakiyesini bozardı. |
| `.github/workflows/run-scalp.yml` | Scalp katmanının periyodik turu (`main.py --layer scalp`, SAATLİK: her koşu dört 15m barını işler). Ayrı cron, ayrı concurrency grubu, ayrı commit kapsamı (`ledgers_scalp` + `docs/data/metrics_scalp.json`). `run.yml`e dokunmaz. Defter commit'inden SONRA anlık sinyal bildirimi (`scripts/telegram_signals.py`) ve onun durum dosyasının kendi commit'i gelir; ikisi de `continue-on-error` — bildirim katmanı ölçümü düşüremez. 15 dakikalık cron ölçüldü ve tetiklemelerin ~%91'i düşüyordu; saatlik kadans `signals_per_bar` sayesinde sinyal kaybı üretmez (bkz. "Telafi edilen barlarda sinyal"). |
| `.github/workflows/run.yml` | Periyodik çalıştırma (cron) ve CI'da test doğrulaması. Telegram adımı defter commit'inden **sonra** gelir ve `continue-on-error` ile korunur: bildirim katmanı ölçümü düşüremez. |

### config.yaml Değerleri

| Anahtar | Değer | Anlamı |
|---|---|---|
| `risk_per_trade` | `0.01` | İşlem başına riske atılan sermaye oranı (%1). Boyutlandırma formülünün payı. |
| `leverage_cap` | `5` | İzin verilen azami kaldıraç. Hiçbir koşulda aşılmaz. |
| `max_positions` | `5` | Bir stratejinin aynı anda taşıyabileceği toplam pozisyon sayısı. |
| `max_short_positions` | `3` | Bunların en fazla kaçının short olabileceği. |
| `fee_rate` | `0.001` | Tek yön komisyon oranı; giriş ve çıkışta ayrı ayrı uygulanır. |
| `slippage_base` | `0.0005` | Yönden bağımsız olarak **her** dolumda (long/short giriş, çıkış, TP) uygulanan temel kayma. |
| `slippage_short_stop` | `0.0015` | Short pozisyonların stop dolumunda `slippage_base` yerine geçen kayma (short stop'lar yukarı boşluklarda daha kötü dolar). |
| `max_stop_atr_multiple` | `3.0` | Stop mesafesi tavanı: stop mesafesi ATR'nin 3 katını aşan sinyal açılmaz, işlem atlanır (kural 14). |
| `maintenance_margin` | `0.005` | Likidasyon eşiği. Pozisyonun mum içi zararı bu seviyeyi geçerse likide edilir. |
| `random_seed` | (sabit tam sayı) | Rastgelelik kullanan her yol bu tohumdan beslenir; koşular tekrarlanabilir olmalıdır. |
| `initial_capital` | `10000` | Her stratejinin izole sanal hesabının başlangıç bakiyesi (USDT). |
| `signals_per_bar` | `false` (scalp: `true`) | Telafi edilen barlarda da sinyal üretilsin mi. Kapalıyken sinyal yalnızca `as_of` barında üretilir; açıkken her bar kendi sinyalini üretir ve tur, o barların her birinde ayrı ayrı koşulmuş gibi sonuçlanır. Bkz. "Telafi edilen barlarda sinyal". |
| `timeframe` | `"4H"` | Tek zaman dilimi; OKX bar kodu ve bar süresi bundan türetilir. |
| `universe_size` | `50` | 24s hacme göre seçilen USDT perpetual sayısı. |
| `universe_refresh_days` | `30` | Evren bu süre dolmadan yeniden hesaplanmaz (kıyas kümesi sabit kalsın). |
| `trailing.atr_period` | `14` | Projenin **tek ATR tanımı**: hem trailing stop (kural 9) hem stop mesafesi bandı (kural 14) bu periyodu kullanır. İkisi ayrışırsa "3×ATR" iki farklı mesafe demeye başlar. Uygulama `core/engine.py`'dedir; periyot ortak olmalı ki aynı `trailing_atr` değeri her modelde aynı stop mesafesi anlamına gelsin. |
| `acceptance.min_trades` | `30` | **Kapı** — örneklem: R'ye giren kapanmış işlem bu sayının altındaysa ortalama R bir ölçüm değil gürültüdür. |
| `acceptance.control_model` | `"random_ctrl"` | **Kapı** — edge'in kontrol referansı. `is_benchmark` değildir (aynı sütunda yarışır), bu yüzden adı `benchmarks` listesinden türetilemez. |
| `acceptance.edge_margin_r` | `0.15` | **Kapı** — edge marjı: kontrolü geçmek için ortalama R farkının en az bu kadar olması gerekir. Çekilişin kendi gürültüsü kıl payı bir farkı tek başına üretebilir. |
| `acceptance.stop_band_ratio` | `2.5` | **Uyarı** — band (kural 14). Defterde ATR olmadığı için band yarışmacıların `avg_stop_distance_pct` medyanına göre kurulur: `medyan/√oran .. medyan×√oran`, uçtan uca tam bu oran kadar geniş. Bandın dışında kalmak doğrulamayı ENGELLEMEZ; raporda uyarı ikonudur. |
| `funding.*` | `enabled`, `interval_hours` | Funding simülasyonunun açık/kapalı olması ve periyodu. |
| `exchange.*` | OKX erişimi | `rest_base`, `inst_type`, `quote_ccy`, `btc_reference`, istek limitleri, timeout, throttle ve retry/backoff sabitleri. |
| `data.*` | yerel depo | `cache_dir`, `universe_file`, `history_bars`, `funding_history_periods`, `max_staleness_bars` (BTC çıpasının azami bayatlığı). |
| `layers.*` | katmanlar | Her katmanın FARKI: `ledger_dir`, `metrics_file`, `universe` (sabit liste ya da `null`), `retention.equity_compaction_days`, `retention.model_trade_limit`, `breakdowns` ve kökü ezen ayarlar (`timeframe`, `models`, `signals_per_bar`, …). Bkz. "Katmanlar". |
| `scalp.*` | scalp kısıtları | Beş kollu modellerin (11, 12, 15) ve model 14'ün BİREBİR aynı okuduğu değerler: `min_stop_pct` (0.01), `min_reward_risk` (1.5), `time_stop_bars` (16), `stop_atr_multiple` (5.0), `target_reward_risk` (2.0) ve `bandit.*` (`warmup_trades` 20, `min_allocation` 0.05, `window_trades` 100, `prior_r_sigma` 1.0). |
| `exit_management.*` | üç aşamalı çıkış | Modeller 13, 14 ve 15'in TEK kaynağı: `breakeven_at_r` (1.0), `partial_tp.r` (1.5), `partial_tp.fraction` (0.5), `trail_giveback_pct` (0.5). Model başına ayrı bloklar, bir gün birinin sessizce ayrışması ve model 15 ↔ `scalp_fixed` farkının "iki ayrı yönetimin farkı"na dönüşmesi demekti. |
| `vwap.*` | modeller 13-14 | Ortak sinyal (`band_mult` 2.0, `min_vwap_bars` 8), model 14'ün sabit çarpanları (`managed.*`) ve kopyanın kendi kuralları (`clone.*`: sabit teminat oranı, kaldıraç, limitler, 12 kombinasyon, epsilon, 13 sembollük evren). |

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

13b. **Üç aşamalı çıkış yönetimi motorun yeteneğidir, stratejinin değil** (kural 9'un aynı
sınırı). `Signal.breakeven_at_r`, `Signal.partial_tp` ve `Signal.trail_giveback_pct`
OPSİYONELDİR, varsayılanları `None`dır ve doldurmayan model bu yetenekten hiçbir biçimde
etkilenmez. Strateji yalnızca isteğini bildirir; stop hareketlerini `core/engine.py`,
kısmi dolumu `core/portfolio.py` uygular.
- **Mum içi sıra likidasyon -> stop -> kısmi -> TP'dir.** Kısmi çıkışın stop'tan sonra
  gelmesi kural 13'ün aynı kuralıdır: aynı mumda ikisi de aralığa giriyorsa kötü olan
  gerçekleşmiş varsayılır.
- **Kısmi çıkışın çektiği stop o mumda BİR DAHA KONTROL EDİLMEZ.** O stop, kısmi dolum
  gerçekleştikten sonra verilmiş yeni bir emirdir ve mumun daha önceki hareketleri
  sırasında piyasada durduğu varsayılamaz. Tersini yapmak kısmi çıkışı her mumda anında
  tam çıkışa çevirir ve mekanizmayı ölçülemez kılardı.
- **Stop hareketleri bar KAPANDIKTAN sonra uygulanır** (kural 12; ATR trailing ile aynı
  adım) ve yalnızca SIKIŞTIRIR, bu yüzden aralarındaki sıra sonucu değiştirmez.
- **`trailing_atr` ile `trail_giveback_pct` aynı anda kullanılamaz** (`ValueError`): ikisi
  de stop'u sıkıştıran ayrı mekanizmalardır ve birlikte çalıştıklarında çıkışı hangi
  kuralın ürettiği defterden okunamaz — oysa bu modeller tam olarak çıkış kuralını ölçüyor.
- **Takip eden stop orijinal hedefi ASLA aşmaz:** aşsaydı hedef hiç dolmaz, her işlem
  stop'la kapanır ve `exit_reason` kolonunun anlamı kaybolurdu.

13c. **Çıkışın ALT sebebi deftere yazılır (`exit_rule`).** `exit_reason` beş kaba koddur ve
ikisi birleşiktir: `stop` hem İLK stop'u hem takip eden stop'u (ATR trailing, breakeven,
geri verme, kısmi çıkışın çektiği stop), `signal` hem zaman stop'unu hem başka bir strateji
çıkışını anlatır. Oysa modeller 13/14/15'in ölçtüğü şey tam olarak yönetimin katkısıdır:
iki çıkış aynı satıra çökerse o katkı defterden okunamaz. Ayrım **sonradan geri
hesaplanamaz** — deftere yalnızca İLK stop yazılır (`stop_price`, R'nin paydası) ve stop'un
sonradan nereye çekildiği yalnızca kapanış anında bilinir.
- Stop'u en son hangi kuralın taşıdığı, hareket ANINDA pozisyonda saklanır
  (`OpenPosition.stop_rule`; yazan `core/portfolio.py::_tighten_stop`, kuralı veren
  `core/engine.py::_update_stops`) ve `positions.json`'a düşer — hareket bir turda,
  çıkış başka bir turda olabilir.
- Kural yalnızca stop GERÇEKTEN hareket ettiğinde yazılır: reddedilen bir hareketin
  (gevşeme yönü) kuralını saklamak, işlemi hiç uygulanmamış bir yönetimle etiketlerdi.
- Strateji çıkışlarında kural TALİMATIN kendi etiketinden gelir
  (`ExitInstruction.reason` içindeki `exit_rule=time_stop`).
- Etiket `notes` kolonuna `core/tags.py` formatıyla düşer; **yeni kolon açılmaz** —
  `trades.csv` başlığı değişirse eski satırlar okunamaz hâle gelir
  (`core/ledger.py::_assert_header`).
- **"İlk stop aldı" için etiket YOKTUR:** etiketin yokluğu o bilgiyi taşır. Uydurma bir
  `exit_rule=initial`, hiç hareket etmemiş bir stop'u bir yönetim kararıymış gibi
  gösterirdi.

14. **Stop mesafesi bandı (maliyet karşılaştırılabilirliği):** Stop mesafesi yalnızca bir risk
    tercihi değil, aynı zamanda **maliyet ölçeğidir.** Boyut `risk / |giriş − stop|` olduğu için
    dar stop kuran model aynı 1R'yi daha büyük notional ile taşır ve R başına daha çok
    komisyon+kayma öder. Bu yüzden modellerin stop mesafeleri kabaca aynı bantta — **1×–2.5×
    ATR** — tutulur: 2×ATR ile 2.5×ATR kıyaslanabilir, 0.5×ATR ile 3×ATR kıyaslanamaz, çünkü
    maliyet farkı sinyal farkını gölgeler. Stop'unu veriye bağlı kuran modeller (ör. "fitil
    tepesinin üstü veya 1×ATR, hangisi genişse") bandın dışına taşabilir; onlar için
    `max_stop_atr_multiple` bir **tavandır**: mesafe tavanı aşıyorsa **işlem atlanır.**
    - Stop tavana **çekilmez** — bu, modelin "stop fitilin üstünde olmalı" tezini sessizce başka
      bir modele çevirirdi.
    - `core/validate.py` burada hata fırlatmaz: geniş stop bir programlama hatası değil,
      karşılaştırılamayacak bir piyasa durumudur (kural 8 ile karışmaz).
    - Atlama **sessiz olamaz:** model atladığı her sinyali gerekçesiyle `logger.info` ile yazar.
      Kural 11'in "atlamak işlem sayısını sessizce düşürür" itirazı burada denetlenebilir kayıtla
      karşılanır; bandın gerçekten tuttuğu ise `avg_stop_distance_pct` kolonundan okunur.

15. **Referans (benchmark) modeller ve boyutlandırma muafiyeti:** `is_benchmark = True` ile
    işaretlenen bir model **yarışmacı değil, zemindir.** Cevapladığı soru tek: *"model piyasayı
    yendi mi, yoksa yalnızca yükselen bir piyasada mı durdu?"* On modelin hepsi pozitif getiri
    üretse bile hiçbiri bu satırı geçemiyorsa ölçümün sonucu "stratejiler işe yarıyor" değildir.

    Böyle bir model `Signal.sizing = "notional_fraction"` kullanabilir ve boyutu şöyle belirlenir
    (uygulayıcı yine tek yetkili yer, `core/portfolio.py`):

    ```
    boyut = (notional_fraction × sermaye) / giriş fiyatı      # kaldıraç ZORLA 1x
    ```

    - **Neden muafiyet gerekiyor:** alım-tut'un tanımı stop'suz olmasıdır. Ona yapay bir stop
      takmak onu bir trend modeline çevirirdi; kural 11'in formülüne (`risk / |giriş − stop|`)
      sokmak ise imkânsızdır — paydası yoktur. Muafiyet olmadan çıpa ya var olamaz ya da
      ölçtüğü şey artık alım-tut olmaz.
    - **Muafiyet YALNIZCA boyutlandırmadadır.** Komisyon, kayma, funding, likidasyon, evren,
      `allowed_directions`, dolum kuralı (kural 13) ve defter kuralları (kural 1/2/7) referans
      modele de birebir aynı uygulanır. Kaldıraç 1x'e **sabitlenir**: `leverage_cap` bir tavandır,
      çıpa için ise kaldıraç hiç devreye girmez — çıpanın işi "piyasa ne yaptı"yı ölçmek, onu
      kaldıraçla büyütmek değil.
    - **Kapı `core/validate.py`'dedir:** `sizing = "notional_fraction"` gelen bir sinyal
      `is_benchmark` VE `is_replica` olmayan bir modelden geliyorsa `ValueError`. Bu kapı
      olmadan kural 3/11 delinir — her model kendi boyutunu "referans gibi" belirlemeye başlar,
      ortak risk birimi (1R) ortadan kalkar ve modeller artık aynı ölçekte yarışmaz.
    - **İki alan birbirini dışlar, sessiz düzeltme yoktur:** `sizing = "risk"` iken `stop_price`
      zorunludur ve `notional_fraction` dolu olamaz; `sizing = "notional_fraction"` iken
      `stop_price` **None olmalıdır** (dolu gelirse hata — yok saymak, deftere yazılan "ilk
      stop"u hiç kullanılmayan bir sayı yapardı, oysa `risk_amount` ve `cost_per_r` ondan türer).
    - **Raporlama:** referans satırı `avg_stop_distance_pct` ve `cost_per_r` kolonlarında `nan`
      alır — stop'u olmayanın 1R'si, dolayısıyla R başına maliyeti de yoktur. Tabloda ortalama R
      sıralamasına girmez; ayrı bir **REFERANS** bölümünde durur. Aynı sütunda sıralamak, farklı
      boyutlandırma kuralıyla çalışan bir satırı risk-birimi yarışının parçasıymış gibi
      gösterirdi. Referansın taşıdığı bilgi sıralamada değil, **hesap düzeyi getirisindedir.**
    - Kural 14'ün stop bandı referans modele uygulanmaz: band bir maliyet ölçeği kuralıdır ve
      stop mesafesi üzerinden tanımlıdır; stop'suz sinyalde uygulanacak bir şey yoktur.
    - **Çıpanın "bir kez al, sonra tut" davranışı sessiz olamaz.** Model defterini göremediği
      için (kural 4/7) her turda aynı sinyalleri üretir; tekrarı reddeden tek yetkili yer
      `core/portfolio.py`'dir. Bu, her turda `signals=2 / filled=0` görünümü demektir — ki
      **gerçek bir boyutlandırma arızası da tam olarak böyle görünür.** İkisi ayırt edilebilsin
      diye her ret bir **sebep koduyla** kaydedilir (`RejectReason`; `duplicate_position`,
      `zero_size`, `insufficient_cash`, ...) ve tur raporunun `rejections` alanında
      (`docs/data/metrics.json` dâhil) sayılarak durur. Log seviyesi de ayrımı taşır: beklenen
      tekrar `INFO`, boyutlandırma arızası `WARNING`. Kodsuz bir "hiç dolmadı" turu yalnızca
      kural 13'ün kuyruğudur (emir bir sonraki barda dolacak) ve ayrıca öyle loglanır.

15b. **Kopya (replica) modeller: `is_replica = True`.** Bir kopya, dış bir sistemin
kurallarını bizim maliyet, kayma, funding ve likidasyon varsayımlarımız altında yeniden
üretir. Çıpa gibi yarışmacı değildir ve aynı sonuçları doğurur — ortalama R sıralamasına
girmez, kabul kapılarına tabi değildir, stop bandı medyanına katılmaz ve maliyet ölçeği
kolonlarında `nan` alır — ama AYRI bir bayraktır, çünkü:
- **Ölçtüğü soru farklıdır.** Çıpa "piyasa ne yaptı", kopya "dış sistem bizim
  varsayımlarımızla ne yapardı" der. Kabul çıtasının zemini (kural 15) yalnızca
  `is_benchmark` satırlarından gelir; kopyayı zemin saymak, çıtayı bir stratejinin
  performansına bağlamak olurdu. Tabloda da ayrı bir **REFERANS (dış sistem)** bölümünde
  durur.
- **Stop kuralı TERSTİR.** Çıpanın `stop_price`ı None OLMALIDIR (stop'suzluk tanımının
  kendisidir); kopyanın `stop_price`ı ZORUNLUDUR, çünkü stop yönetimi kopyalanan
  sistemin parçasıdır ve tam olarak ölçülmek istenen şeydir.
- **`nan` kolonları burada "hesaplanamaz" değil "kıyaslanamaz" demektir.** Kopya 1R'yi
  sabit teminattan, yarışmacılar sermayenin %1'inden türetir; aynı sütuna koymak farklı
  paydaya sahip iki oranı karşılaştırılabilirmiş gibi sunardı. R'nin kendisi ölçülmeye
  devam eder (kopyanın kendi öğrenmesi ona dayanır).
- **`ModelLimits` yalnızca kopya modellere açıktır** (kapı: `core/validate.py::validate_model`,
  çağıran `strategies/registry.py`). Kaynak sistemin kaldıracı, eşzamanlı pozisyon sayısı,
  yön kotası ve portföy riski tavanı onun kendi kurallarıdır; model bunları kendi içinde
  uygulayamaz (kural 4/16: açık pozisyonlarını göremez; kural 3: boyut hesabı
  `core/portfolio.py`nindir), bu yüzden bir BİLDİRİMDİR ve uygulayan yine tek yetkili
  yerdir. Yarışmacılara kapalı olması kural 6'nın kendisidir. Limitler kök kotayı yalnızca
  DARALTIR; kaldıraç tavanı `REPLICA_LEVERAGE_CAP` (10x).
- **Muafiyet YALNIZCA boyutlandırmadadır** (kural 15 ile birebir aynı): komisyon, kayma,
  funding, **likidasyon**, evren, `allowed_directions` ve dolum kuralı kopyaya da aynen
  uygulanır. Likidasyonu kapatmak, 10x kullanmanın bedelini silip kopyayı haksız biçimde
  iyi göstermek olurdu (bkz. docs/decisions.md > "Kopya modelde likidasyon kapatılmaz").

16. **Model kendi KAPANMIŞ işlemlerini okuyabilir, açık pozisyonlarını okuyamaz.** Uyarlanabilir
    modeller (bkz. `strategies/scalp_bandit.py`) geçmiş sonuçlarından öğrenir; bunun tek meşru
    kaynağı defterin kapanmış satırlarıdır. Motor, `Strategy.observe_closed_trades` kancasını
    **uygulayan** modellere her turda kendi kapanmış işlemlerinin salt okunur bir görünümünü
    (`ClosedTrade`) verir; uygulamayan model için defter hiç okunmaz ve davranışı değişmez.
    - **Açık pozisyon bu listeye hiçbir yoldan giremez.** Girseydi model, henüz gerçekleşmemiş
      bir sonucu öğrenir ve kâğıt üstündeki kârı ölçüme sokardı — üstelik pozisyon kapandığında
      aynı sonucu ikinci kez sayardı.
    - **İzolasyon korunur (kural 4):** model yalnızca KENDİ işlemlerini görür; başka modelin
      işlemine, pozisyonuna, bakiyesine ya da iç durumuna erişimi yoktur.
    - **Kural 1/7 korunur:** bu bir OKUMA yüzeyidir. Model deftere yazmaz, bakiye/pozisyon
      durumu tutmaz; gördüğü `r_multiple` `core/metrics.py`'deki tek R tanımından gelir.
    - Besleme, defterdeki satırlara **bu turda kapanan** işlemleri de ekler: 15 dakikalık bir
      modelde pozisyon aynı turda açılıp kapanabilir ve defteri beklemek, modelin en taze
      sonucu bir tur geç görmesi demekti.
    - Kanca patlarsa yalnızca o modelin turu boş geçer (kural 8): yarım öğrenilmiş bir
      posterior ile sinyal üretmek, modelin ne ölçtüğünü bilinmez kılardı.

## Katmanlar (`core/layers.py`)

Ölçüm iki zaman diliminde yürür ve ikisi de **AYNI çekirdeği** koşar: `core/engine.py`,
`core/portfolio.py`, `core/ledger.py`, `core/funding.py`, `core/metrics.py`, `core/report.py`
ve `main.py` tek kopyadır. Katman, ölçümün **koşullarını** değiştirir:

| | `base` | `scalp` |
|---|---|---|
| Bar | 4H | 15m |
| Evren | hacme göre ilk 50 (30 günde bir yenilenir) | **SABİT 14 sembol**, otomatik seçim yok |
| Modeller | 10 yarışmacı + 1 referans çıpası | 4 yarışmacı (11, 12, 14, 15) + 1 dış sistem kopyası (13) |
| Defter | `ledgers/` | `ledgers_scalp/` |
| Rapor | `docs/data/metrics.json` | `docs/data/metrics_scalp.json` |
| Cron | `run.yml` (6 saatte bir tur, 4 saatlik bar) | `run-scalp.yml` (saatlik; tur başına 4 bar) |
| Telafi barında sinyal | yok (`signals_per_bar: false`) | var (`signals_per_bar: true`) |
| Stop tavanı (kural 14) | 3×ATR | 8×ATR |
| Kırılımlar | yok | kol + sembol |
| Yarışma dışı satır | `buyhold` (`is_benchmark`) | `vwap_clone` (`is_replica`) |

**Neden ayrı bir `scalp_config.yaml` değil.** `risk_per_trade`, `fee_rate`, `slippage_*`,
`leverage_cap`, `initial_capital`, `maintenance_margin` iki katmanda da BİREBİR aynıdır
(kural 6). İki dosyaya bölmek, bir gün birinin sessizce ayrışması demekti — ve iki katman
farklı maliyet varsayımlarıyla koştuğunda bunu hiçbir test yakalamazdı. Kök tek kaynaktır;
katman bloğu yalnızca **farkı** yazar (derin birleştirme: `data.history_bars` yazmak `data`nın
geri kalanını silmez).

**Neden ayrı bir giriş noktası değil.** Çekirdek zaten tamamen config sürümlüdür; ayrı bir
`main_scalp.py` yalnızca orkestrasyonu (model kurulumu, hata izolasyonu, dry-run kopyası, yük
yazımı) ikinci kez yazmak olurdu. Ayrışan şey ölçümün koşulları, akışı değil.

**Katmanlar arası kıyas YAPILMAZ.** Dashboard scalp modellerini ayrı bir bölümde, ayrı bir
tabloda gösterir. 15 dakikalık bir modelin ortalama R'si ile 4 saatlik bir modelinki aynı
sütuna konsaydı, aradaki fark strateji farkı gibi okunurdu — oysa işlem sıklığı, maliyetin R
içindeki payı ve tutma süresi bambaşkadır. Katman **içi** kıyas (model 11 ↔ model 12) ise
tam olarak tasarımın amacıdır.

**Telafi edilen barlarda sinyal (`signals_per_bar`).** Motor atlanan turları zaten telafi
ederdi — son işlenmiş bardan `as_of`'a kadarki her barı sırayla ilerletir — ama sinyal
YALNIZCA `as_of` barında üretilirdi: atlanan barların pozisyon yönetimi (stop/TP/likidasyon/
funding) yapılır, sinyal fırsatı ise kaybolurdu. 15 dakikalık katmanda bu kayıp ölçülebilir
bir sorundur: GitHub cron'unun 15 dakikalık tetiklemelerinin ~%91'i düşüyordu, yani tablo
modelin değil **cron'un kadansını** ölçüyordu. Üstelik kayıp turdan tura değiştiği için
modeller arası farkın kendisi de gürültüye karışırdı.

Ayar açıkken tur, o barların her birinde **ayrı ayrı koşulmuş gibi** sonuçlanır ve bunun
testi bir eşdeğerliktir (`tests/test_engine_per_bar.py`): bir turda telafi edilen N bar ile
N ayrı turda koşulan N bar **birebir aynı defteri** üretir. Bunu sağlayan dört kural:

- **Barlar sırayla işlenir, toplu değerlendirme YOKTUR:** her bar için sinyal üretilir,
  emir bir SONRAKİ barın açılışından dolar (kural 13), sonra o barın stop/TP/likidasyon
  kontrolü yapılır. Barları birleştirip tek bir değerlendirme yapmak, ara barlarda
  tetiklenecek çıkışları yok saymak olurdu.
- **Pozisyon limitleri her barda yeniden sorulur** (`core/portfolio.py`): kota barın kendi
  doluluğuna bakar, turun toplamına değil.
- **Model her barda yalnızca o bara kadarki veriyi görür** (kural 12): `core/engine.py`
  çerçeveleri o barda keser ve o barı taşımayan sembolü o barın evreninden düşürür —
  `core/data.py`'nin `as_of` için uyguladığı kuralın aynısı. Aksi, telafi barında geleceği
  görerek sinyal üretmek olurdu.
- **`as_of`'tan sonrası işlenmez:** kapanmamış bar ne sinyal ne özsermaye satırı üretir.

Ayar **kökte kapalıdır** (4 saatlik katman), scalp katmanında açıktır. Gerekçe kural 6'nın
kendisi değil, defterin tek bir kuralla yazılmasıdır: `run.yml` güvenilir tetikleniyor,
yani base katmanında telafi nadiren devreye girer — ama devreye girdiği turlarda defterin
kuralı sessizce değişir ve biriken geçmişin bir kısmı "tur başına tek sinyal", bir kısmı
"bar başına tek sinyal" ile üretilmiş olurdu. İki dönemin işlem sıklığı kıyaslanamazdı.
Katmanlar arası kıyas zaten yapılmadığı için ayarın katmana göre farklı olması bir
tutarsızlık değildir; katman **içi** kıyasta ise beş model de aynı ayarı görür.

**Saklama penceresi (`retention`).** 15 dakikalık katman günde 96 tur koşar ve her turu commit
eder: `equity.csv` yılda on binlerce satıra çıkar ve depo geçmişi ölçümle ilgisiz satırlarla
şişer. Bu yüzden scalp katmanında 30 günden eski özsermaye satırları **günlük özete** (o günün
son barı) indirilir ve JSON'a model başına son **50** işlem yazılır. İkisi de ölçümü
değiştirmez: `trades.csv` — denetim izi — hiçbir koşulda dokunulmaz, taze 30 gün bar bazında
kalır ve sıkıştırma her model için birebir aynı uygulanır.

### Scalp katmanının model kuralları

Katmanda **üç ölçüm ekseni** vardır ve her eksende yalnızca TEK bir değişken ayrışır:

| Eksen | Çift | Ayrışan tek şey |
|---|---|---|
| Adaptasyonun katkısı | `scalp_bandit` (11) ↔ `scalp_fixed` (12) | kol seçimi |
| Çıkış yönetiminin katkısı | `scalp_fixed` (12) ↔ `scalp_managed` (15) | üç aşamalı çıkış |
| Ev kurallarının katkısı | `vwap_clone` (13) ↔ `vwap_managed` (14) | boyutlandırma + kapılar |

**Çekiliş, ölçülmeyen eksende PAYLAŞILIR, ölçülen eksende BAĞIMSIZDIR.** `ScalpModel`in
`rng_identity` alanı bunu taşır. Model 11 ↔ 12'de ölçülen şey seçimin kendisidir; çekilişi
paylaşsalardı fark adaptasyonun değil tesadüfün ölçüsü olurdu. Model 12 ↔ 15'te ise ölçülen
şey seçim DEĞİL, aynı seçimin nasıl yönetildiğidir — bu yüzden model 15 `scalp_fixed`in
çekiliş kimliğini kullanır ve ikisi her turda aynı kolu, aynı sembolü seçer (eşleştirilmiş
deney). Bu, defterlerinin birebir aynı olacağı anlamına gelmez: yönetim bazı pozisyonları
erken kapatır ve `max_positions` doluluğu zamanla ayrışır — ayrışan şey DOLUMLARDIR,
sinyaller değil, ve bu ayrışmanın kendisi yönetimin bir sonucudur.

Beş kollu üç model (11, 12, 15) **beş ortak kolu** (`strategies/scalp/arms.py`) aynı
kapılardan geçirir; VWAP modelleri (13, 14) ise **ortak sinyali**
(`strategies/vwap/signal.py`) görür ve kol kırılımında `arm=vwap_revert` etiketiyle durur.

- **Stop tabanı %1** — tur maliyeti ~%0.25'tir; daha dar stop'ta maliyet 0.25R'yi aşar ve
  model daha başlamadan geride başlar. Stop **genişletilmez**, işlem **atlanır** (kural 14'ün
  aynı gerekçesi) ve her atlama `logger.info` ile yazılır.
- **Hedef/stop ≥ 1.5** — sağlamayan kurulum atlanır. Hedef, projeksiyon
  (`target_reward_risk × stop`) ile kolun yapısal engelinin YAKIN olanıdır: yalnızca
  projeksiyon kullanmak kapıyı ölü koda çevirirdi, yalnızca yapısal seviye kullanmak ise
  15 dakikalık barda hemen hiçbir kurulumun 1.5R'ye ulaşmaması demekti.
- **Zaman stop'u 16 bar (4 saat)** — `manage_positions` üzerinden istenir (kural 10), dolum
  bir sonraki barın açılışındadır (kural 13), yani gerçek ömür 17 bardır.
- **Trailing yok** — yürüyen stop, gerçekleşen R ile kurulumun vaat ettiği R arasındaki bağı
  koparır ve bandit'in öğrendiği sinyali bulanıklaştırırdı.
- **BARDA tek sinyal** — beş kolu birden oynamak kol tahsisini anlamsız kılardı. Birim
  `bar`dır, `tur` değil: `signals_per_bar` açık olduğu için bir tur kaç bar telafi ediyorsa
  o kadar sinyal üretilir (saatlik cron'da 4). Model 14 de barda tek sinyal oynar (kıyas
  hedefi `scalp_fixed` bar başına tek pozisyon açar); model 13 ise kaynak sistemin kuralı
  gereği kendi pozisyon kotasına (5) kadar sinyal üretir.
- **Ev kapıları (stop tabanı, 1.5R, zaman stop'u) model 13'e UYGULANMAZ** — kaynak sistemde
  yoktur; eklemek kopyayı model 14'e çevirirdi ve ikisinin farkı ölçülemez hâle gelirdi.
- **Üç aşamalı çıkış yönetimi** (`exit_management` bloğu) modeller 13, 14 ve 15'te aynıdır
  ve tek kopyadan (`strategies/exit_management.py`) okunur.

**Bandit durumu deftere yazılır ve tekrar üretilebilir.** Ayrı bir `bandit_state.json`
YOKTUR: posterior `ledgers_scalp/scalp_bandit/trades.csv`'nin saf bir fonksiyonudur (kol
etiketi + gerçekleşen R + kayan pencere) ve her turda sıfırdan kurulur. Ayrı bir durum
dosyası, defterle senkron kalması ayrıca test edilmesi gereken ikinci bir doğruluk kaynağı
yaratırdı. Denetim izi `reason` kuyruğundadır: `... | arm=vwap_pullback | post_r=0.31`.
Etiketi olmayan bir satır **sessizce atlanmaz**, `TagError` fırlatılır (kural 8 gereği
yalnızca o modelin turu boş geçer) — atlamak, posterior'ı defterde görünmeyen bir geçmişe
bağlardı.

## Dolum ve Pozisyon (ölçümün birimi)

`trades.csv` bir **DOLUM defteridir**, işlem defteri değil. `core/portfolio.py::_close`
her dilim için ayrı satır yazar ve bir pozisyon birden çok satır üretebilir:

- **kısmi çıkış** (`exit_reason="partial"`) — üç aşamalı çıkış yönetimi (modeller 13/14/15);
- **fraksiyonel hedef** (`exit_reason="tp"`, `TakeProfit.fraction < 1.0`) — `avwap` iki TP
  seviyesi, `downtrend_rally` yarım TP kullanır.

Ölçümün birimi ise **POZİSYONDUR.** `core/metrics.py::merge_fills` aynı pozisyonun
satırlarını tek ölçüm satırına indirger; kimlik `strategy + symbol + direction + opened_at`
(havuz birden çok modelin satırını birleştirdiği için `strategy` şart — kural 4 ölçümde de
geçerlidir). Nakit kolonları TOPLANIR, kapanış alanları pozisyonu KAPATAN son dolumdan gelir.

**Neden saymak yanlış:** aynı pozisyon iki kez ölçüme girer, `acceptance.min_trades` (30)
örneklem kapısı iki kat hızlı geçilir ve kazanma oranı yapay yükselir — kısmi çıkış tanımı
gereği kârda gerçekleşir, yani daima "kazanç" satırıdır.

**Neden kısmi satırı ATMAK da yanlış:** pozisyonun kilitlenmiş kârı ölçümden düşer ve
kalan dilimin R'si tüm pozisyonun R'si sanılır. Yönetimli model (15) yönetimsiz ikizine
(12) karşı bu kez haksızca KÖTÜ görünürdü — aynı hatanın ters yönü. Ayrıca `exit_reason`
filtresi fraksiyonel hedefleri hiç yakalamaz: `avwap` ve `downtrend_rally` "partial" kodu
taşımaz, "tp" taşır.

**Toplama ikisinden de kaçınır:** `R = Σpnl / Σrisk`, yani pozisyonun gerçek R'si.
`Σpnl = bakiye değişimi` değişmezi de korunur, çünkü nakit kolonları atılmaz toplanır
(test: `tests/test_metrics.py`).

`docs/positions.html` dolum satırlarını GÖRÜNMEYE devam ettirir ("DİLİM" etiketiyle) ama
istatistik hesaplamaz — ölçümü yükten okur. Bu yüzden **işlem sayısı tablodaki satır
sayısından az olabilir** ve sayfa bunu söyler.

## Rapor Kolonları

`core/metrics.py` her modeli **aynı tabloda**, her metriği **long / short / toplam** olarak
raporlar. Performans kolonlarının (`trades`, `win_rate`, `pnl`, `sharpe`, `max_drawdown`)
yanında iki **maliyet ölçeği** kolonu zorunludur:

| Kolon | Tanım |
|---|---|
| `avg_stop_distance_pct` | İşlem bazında `\|giriş − ilk stop\| / giriş` değerlerinin ortalaması (yüzde). Modelin hangi R ölçeğinde işlem yaptığını gösterir. |
| `cost_per_r` | İşlemin **tüm** dolumlarında ödenen komisyon + kayma toplamının (giriş, kısmi TP'ler, çıkış) `risk_amount`'a bölümü; model/yön bazında ortalaması. "Tüm dolumlar" şartını sağlayan şey `merge_fills`tir: tek bir dilimin maliyet/risk oranı pozisyonun maliyetini anlatmaz. |

`risk_amount` = `pozisyon boyutu × |giriş − ilk stop|` (yani **gerçekleşen** 1R). Formülün payı
(`risk_per_trade × sermaye`) değil: kaldıraç tavanı boyutu küçülttüyse (kural 11) gerçek risk de
küçülmüştür ve payı kullanmak o işlemlerin maliyetini olduğundan düşük gösterirdi.

Bu kolonlar opsiyonel değildir, çünkü projenin ana sorusunu doğrudan kirleten etkiyi ölçerler:
"short modeller daha iyi" sonucu **sinyalden** mi geliyor, yoksa short modellerin daha geniş stop
kullanıp R başına daha az maliyet ödemesinden mi? İki modelin `avg_stop_distance_pct` değerleri
bandın dışında ayrışıyorsa ve `cost_per_r` farkı performans farkını tek başına açıklayabiliyorsa,
kıyas **geçersiz** sayılır; sonuç yorumlanmaz, modelin stop parametresi düzeltilir.

**Kırılımlar (yalnızca katmanın istediği yerde).** `core/metrics.py::breakdown` işlemleri bir
gruplama ölçütüne göre böler ve her grup için aynı metrikleri hesaplar; hangi kırılımların
üretileceği katman ayarıdır (`layers.<ad>.breakdowns`). Scalp katmanı ikisini de ister:

- **kol** (`arm`) — hangi kolun kaç işlem yaptığı, ortalama R'si ve kazanma oranı. Grup ölçütü
  `signal_reason` kuyruğundaki `arm=` etiketidir; etiket yoksa `TagError` fırlatılır, satır
  sessizce atlanmaz (atlamak kırılım toplamı ile model toplamını ayrıştırırdı).
- **sembol** (`symbol`) — sembol başına işlem sayısı, ortalama R ve `cost_per_r`. Kayma
  varsayımı (`slippage_base`) evrendeki her sembol için tek bir sayıdır; ince kitapta işlem
  gören bir sembolde (PENGU, ETHFI) `cost_per_r` belirgin biçimde ayrışıyorsa varsayım orada
  tutmuyor demektir ve o satırın sonucu yorumlanmadan önce bu bilinmelidir.

Referans çıpaları (kural 15) ve dış sistem kopyaları (kural 15b) bu iki kolonu **`nan`** alır
ve tablonun AYRI İKİ bölümünde durur. Çıpada gerekçe hesaplanamazlıktır: stop'u olmayanın 1R'si
yoktur. Kopyada ise sayı hesaplanabilir ama **kıyaslanamaz**: 1R'si sabit teminattan türer,
yarışmacılarınki sermayenin %1'inden — aynı sütuna koymak farklı paydaya sahip iki oranı
karşılaştırılabilirmiş gibi sunardı. İkisi de aynı bölümde toplanmaz, çünkü ölçtükleri soru
farklıdır. Onların taşıdığı bilgi bu kolonlarda değil, hesap düzeyi getirisindedir.

Tanım kararları:

- **Payda her zaman ilk stop'tur, trailing ile güncellenen stop değil.** R, giriş anında üstlenilen
  risktir; trailing yalnızca kârı korur. Yürüyen stop'u kullanmak iyi giden işlemlerin paydasını
  sonradan küçültüp `cost_per_r`'yi şişirirdi — üstelik bu şişme trailing kullanan modellerde
  farklı olurdu, yani tam da kıyaslanmak istenen şeyi bozardı.
- **Veri yoksa `nan`, `0.0` değil.** Bir modelin o yönde hiç işlemi yoksa kolon `nan` olur. `0.0`
  "işlem yaptı ve tam sıfır çıktı" demektir; ikisini aynı hücreye yazmak, hiç short açmamış bir
  modeli "maliyetsiz short yapan model" gibi gösterir ve model ortalamalarını aşağı çeker.
- **Yön bazlı Sharpe tek bakiyeyi bölerek değil, kümülatif R eğrisinden türetilir.** Hesap tektir
  ve bölünemez (ortak margin, ortak funding, ortak nakit); bakiye eğrisini zorla long/short diye
  ikiye ayırmak uydurma sayılar üretir. Bunun yerine yön bazlı Sharpe, o yöndeki işlemlerin
  kapanış sırasına göre dizilmiş **R cinsinden getiri dizisinden** hesaplanır. Toplam (hesap)
  Sharpe'ı ise gerçek bakiye eğrisinden gelir ve iki yön Sharpe'ının toplamı ya da ortalaması
  **değildir** — tabloda ayrı bir satır olarak durur.

## Kabul Çıtası (iki kapı + bir uyarı)

Tablo "hangi model önde" der; çıta "bu satır okunabilir mi" der. Hesap
`core/metrics.py::acceptance_flags`, eşikler `config.yaml > acceptance`.

**İki KAPI** — bir model ancak **ikisi birden** yeşilken doğrulanmış (`passed`) sayılır:

| Kapı | Soru | Geçme koşulu |
|---|---|---|
| **Ö** — örneklem | Bu ortalama bir ölçüm mü, gürültü mü? | R'ye giren kapanmış POZİSYON ≥ `acceptance.min_trades` (30); kısmi çıkış ve fraksiyonel hedef dilimleri ayrı sayılmaz (bkz. "Dolum ve Pozisyon") |
| **E** — edge | Sonuç sinyalden mi geliyor, piyasadan ve şanstan mı? | ortalama R > 0 **ve** kontrol grubunun ortalama R'sini **en az `acceptance.edge_margin_r` (0.15R) marjla** aşıyor **ve** hesap getirisi referans çıpasını geçiyor |

**Bir UYARI** — `passed`'ı **etkilemez**, yalnızca sonucun nasıl okunacağını söyler:

| Uyarı | Soru | Tetiklenme |
|---|---|---|
| **⚠ B** — band | Bu satır başka bir satırla aynı maliyet ölçeğinde mi? (kural 14) | `avg_stop_distance_pct`, yarışmacı medyanının `medyan/√oran .. medyan×√oran` bandının **dışında** |

Kararlar:

- **Band neden kapı değil.** Bandın dışında kalmak bir kusur değil, bir **kıyas koşuludur.**
  Modelin kendi ölçümü geçerlidir; sorun ancak o satır bir başkasının yanına konduğunda
  doğar — model aynı 1R'yi farklı notional ile taşımış, yani R başına farklı komisyon+kayma
  ödemiştir. Kapı yapmak iki ayrı soruyu birbirine karıştırırdı: *"bu model doğrulandı mı"*
  ile *"bu model şu modelle kıyaslanabilir mi."* Birincisi modelin kendi verisiyle
  cevaplanır, ikincisi ancak bir çiftle. Bu yüzden band tabloda bir uyarı ikonudur:
  görüldüğünde önce `cost_per_r` kolonuna bakılır, çünkü fark sonucu tek başına açıklıyor
  olabilir (CLAUDE.md > Rapor Kolonları).
- **Edge'in marjı neden var.** Kontrolü 0.01R ile geçen bir model marj olmadan "geçti"
  sayılırdı; oysa bilgisiz çekilişin kendi gürültüsü o kadar farkı tek başına üretir.
  `edge_margin_r`, kontrolü **anlamlı biçimde** geçmiş modeli kıl payı önde olandan ayırır.
  Karşılaştırma `fark >= marj` şeklindedir ("en az bu kadar"); iki kayan noktalı ortalamanın
  farkı söz konusu olduğu için sınırın ULP düzeyinde tanımı anlamsızdır ve koda yapay bir
  tolerans eklenmez.
- **Kapılar yalnızca yarışmacılara uygulanır.** Referans çıpası (kural 15) yarışmacı
  değildir; ölçmediği bir yarışta not vermek, çıpanın ne olduğunu yanlış anlatırdı. Çıpa
  `acceptance` bölümüne hiç girmez, tabloda bayrak sütununda `—` görünür.
- **Kontrol grubu kapılara girer.** `random_ctrl` bir yarışmacıdır (`is_benchmark = False`)
  ve kendi edge kapısında kendini geçemez (kendisiyle farkı 0, gereken marj 0.15R). Bilgisiz
  çekilişin sıralamada nerede durduğu gizlenecek bir kusur değil, raporlanacak bir sonuçtur.
- **Band neden medyana bağlı:** kural 14'ün bandı ATR katı cinsindendir, defterde ise ATR
  yoktur — işlem kapandıktan sonra "o anki ATR" geri hesaplanamaz ve geriye dönük yeniden
  hesaplamak look-ahead kapısı açardı. Medyan, aynı evrende aynı barlarda işlem yapan
  modellerin ortak volatilite ölçeğini taşır.
- **Edge'in "çıpayı geç" koşulu** kural 15'in sorusudur: on model de pozitif getirse ama
  hiçbiri çıpayı geçemese sonuç "stratejiler işe yarıyor" değildir. Birden çok çıpa varsa
  **en yükseği** zemindir; geçilmesi en kolay olanı seçmek çıtayı sessizce indirirdi.
- **Kontrol ya da çıpa kümede yoksa** ilgili koşul değerlendirilemez ve `edge` geri kalan
  koşullara düşer — ama bu sessiz olmaz, `logger.warning` ile söylenir. Eksik bir çıta,
  geçilmiş bir çıta gibi görünmemelidir.

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
    is_benchmark: bool = False            # True ise yarışmacı değil referans çıpası (kural 15)
    is_replica: bool = False              # True ise yarışmacı değil dış sistem kopyası (kural 15b)
    limits: ModelLimits | None = None     # yalnızca is_replica (kapı: core/validate.py)

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

    def observe_closed_trades(self, trades: Sequence[ClosedTrade]) -> None:
        """Modelin KENDİ kapanmış işlemleri; generate_signals'tan ÖNCE, turda bir kez.

        Varsayılan: hiçbir şey yapma. Motor kancayı yalnızca uygulayan modeller için
        doldurur (kural 16); açık pozisyon bu listeye asla giremez.
        """
        return None


@dataclass(frozen=True, kw_only=True)
class TakeProfit:
    price: float
    fraction: float          # 0 < fraction <= 1.0; bir Signal içindeki toplam <= 1.0


@dataclass(frozen=True, kw_only=True)
class PartialTakeProfit:
    """Kısmi çıkış İSTEĞİ: fiyat değil R SEVİYESİ (kural 13b)."""
    r: float                 # giriş anındaki riskin katı; fiyata çeviren core/portfolio.py
    fraction: float          # 0 < fraction < 1.0; 1.0 kısmi değil TAM çıkıştır


@dataclass(frozen=True, kw_only=True)
class ModelLimits:
    """Kopya modelin KENDİ kuralları; kök kotaları yalnızca DARALTIR (kural 15b)."""
    max_positions: int | None = None
    max_per_direction: int | None = None
    max_portfolio_risk: float | None = None   # Σ açık risk / sermaye tavanı
    leverage: float | None = None             # yalnızca sizing="notional_fraction"; tavan 10x


SizingMode = Literal["risk", "notional_fraction"]


@dataclass(frozen=True, kw_only=True)
class Signal:
    symbol: str
    direction: Direction               # "long" | "short"
    stop_price: float | None = None    # sizing="risk" iken ZORUNLU, aksi hâlde None OLMALI
    sizing: SizingMode = "risk"        # "notional_fraction" yalnızca is_benchmark (kural 15)
    notional_fraction: float | None = None   # yalnızca sizing="notional_fraction" iken
    entry_type: Literal["market", "limit"] = "market"   # v1'de yalnızca "market" işlenir
    take_profits: tuple[TakeProfit, ...] = ()
    trailing_atr: float | None = None  # uygulaması core/engine.py'de, strateji yazmaz
    # Üç aşamalı çıkış yönetimi (kural 13b): hepsi OPSİYONEL, varsayılan KAPALI.
    breakeven_at_r: float | None = None        # bu R'a ulaşınca stop girişe çekilir
    partial_tp: PartialTakeProfit | None = None  # bu R'da kısmi çıkış + stop o seviyeye
    trail_giveback_pct: float | None = None    # kısmi SONRASI takip; trailing_atr ile birlikte OLMAZ
    reason: str = ""                   # deftere yazılacak serbest metin


@dataclass(frozen=True, kw_only=True)
class Position:
    """manage_positions'a verilen salt okunur pozisyon görünümü."""
    symbol: str
    direction: Direction
    entry_price: float
    stop_price: float | None           # referans çıpasında stop yoktur (kural 15)
    take_profits: tuple[TakeProfit, ...] = ()
    trailing_atr: float | None = None
    breakeven_at_r: float | None = None
    partial_tp: PartialTakeProfit | None = None
    trail_giveback_pct: float | None = None
    partial_done: bool = False         # kısmi çıkış doldu mu (bir İSTEK değil, bir OLAY)
    opened_at: pd.Timestamp


@dataclass(frozen=True, kw_only=True)
class ClosedTrade:
    """Modelin KENDİ kapanmış işleminin salt okunur görünümü (kural 16)."""
    symbol: str
    direction: Direction
    opened_at: pd.Timestamp
    closed_at: pd.Timestamp
    r_multiple: float | None           # pnl / risk_amount; risk bilinmiyorsa None (0.0 değil)
    signal_reason: str
    exit_reason: str


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
- **Boyutlandırma muafiyeti neden bir bayrağa bağlandı (kural 15)**: alternatif, alım-tut
  çıpasına yapay bir stop (örn. girişin %99 altında) takıp kural 11'i hiç değiştirmemekti. O yol
  daha az kod değiştiriyordu ama iki şeyi bozuyordu: (a) uydurma stop, `risk_amount` üzerinden
  uydurma bir R üretir ve çıpa yarışmacılarla aynı sütunda sıralanabilir hâle gelirdi — oysa
  ölçtüğü şey farklı; (b) %1 risk kuralı çıpayı sermayenin küçük bir dilimine hapseder, "piyasa
  ne yaptı" sorusunun cevabı ise sermayenin tamamının piyasada olmasını gerektirir. Bayrak,
  muafiyeti **tek bir yerde denetlenebilir** kılar: `core/validate.py`'de bir satır, tablonun
  ayrı bir bölümü. Muafiyetin gizli kalmadığı, `is_benchmark` alanının hem sözleşmede hem
  raporda görünmesiyle güvence altındadır.
- **Long/short metriklerinin ayrılması**: projenin ana sorusu short işlemlerin görece
  başarısı olduğu için birleşik bir Sharpe ya da win-rate cevabı vermez. Ayrıştırma
  raporlamanın varsayılanıdır, ek bir seçenek değil.

# Backtest: ön-kayıtlı değerlendirme kuralları

**Bu belge sonuç üretilmeden ÖNCE yazıldı ve commit edildi.** Tarih damgası git geçmişindedir.

Gerekçe bu oturumun kendi geçmişidir. İki kez, veriye bakıp sonradan kural yazmanın nasıl
yanlış sonuca götürdüğünü ölçtük:

- **Saat hipotezi** (karar 27): tek günün verisinde doğrulanır göründü, altı günde TERSİNE
  döndü, permütasyon testinde p=0.575 çıktı.
- **Kayıp serisi cooldown'u** (karar 28): kümelenme gerçekti (p<0.002) ama 12 parametre
  kombinasyonundan en iyisini seçmenin kazancı, kümelenmesiz karıştırılmış dizide de
  p=0.48 olasılıkla çıkıyordu.

İkisinde de kuralı **önce** yazmış olsaydık tuzağa hiç girmezdik. Backtest, bu tuzağın en
verimli üreme alanıdır: pencereyi, parametreyi ve metriği sonuca bakarak seçebildiğin
sürece her sistem kârlı görünür.

---

## 0. Mimari: ikinci bir uygulama YOKTUR

Backtest ayrı bir motor değildir. `core/engine.py` atlanan turları telafi ederken zaten
tam olarak bir backtest yapar: son işlenmiş bardan `as_of`'a kadar **her barı sırayla**
işler, emirler kendi barının ertesinden dolar (kural 13), stop/TP/likidasyon her barın
kendi `high`/`low`'uyla kontrol edilir (kural 12/13).

Sadakat iddia değil, **testle sabittir**:

```
tests/test_engine_per_bar.py::test_catch_up_matches_running_each_bar_in_its_own_round
  -> bir turda telafi edilen N bar ile N ayrı turda koşulan N bar
     BİREBİR AYNI defteri üretir
```

Harness'ın yaptığı üç şey vardır ve hiçbiri çekirdeğe dokunmaz:

1. Ayrı bir defter kökü (`backtests/<koşu-id>/`) — gerçek defter hiçbir koşulda yazılmaz
2. Her modelin `last_processed_bar`'ını pencere başına **tohumlar** (boş defterde
   `core/engine.py::_timeline` yalnızca son barı işler)
3. `signals_per_bar: true` (bkz. §5, base katmanının bilinçli sapması)

Backtest'e özel hiçbir maliyet, dolum, likidasyon ya da metrik kuralı YOKTUR. Hepsi
canlıyla aynı koddan gelir; ayrışsalardı backtest kendi uydurduğu bir dünyayı ölçerdi.

---

## 1. Kapı 0 — harness kanıtlanmadan hiçbir sayı okunmaz

**Hiçbir backtest sonucu, harness canlı veriye karşı doğrulanmadan yorumlanmaz.**

Elimizde yer gerçeği var: canlı scalp defteri ve her turun `round.models[].emitted`
kaydı — yani her barda her modelin tam olarak hangi sinyali ürettiği.

Test: config'in DEĞİŞMEDİĞİ bir canlı pencere seçilir, backtest aynı pencerede koşturulur
ve üretilen sinyaller canlı kayıtla karşılaştırılır.

| Model sınıfı | Beklenti |
|---|---|
| **Uyarlanabilir OLMAYAN** (`scalp_fixed`, `scalp_managed`, `vwap_managed`) | Sinyaller **birebir** eşleşmeli. Sinyal, piyasa verisinin ve sabit tohumun saf fonksiyonudur. |
| **Uyarlanabilir** (`scalp_bandit`, `vwap_clone`, `vwap_guarded`) | Eşleşme BEKLENMEZ ve aranmaz: ikisi de kendi kapanmış işlemlerinden öğrenir (kural 16), boş defterden başlayan bir koşu farklı bir geçmiş görür. Rapor edilir, kapı sayılmaz. |

**Birebir eşleşme sağlanmazsa hiçbir backtest çıktısı yorumlanmaz.** Önce fark açıklanır.

Config'in değiştiği pencereler bu teste uygun değildir: `fee_rate` (karar 25) ve
`vwap.managed.atr_multiple` (karar 26) 15 Eylül'de değişti, o tarihten öncesi ile sonrası
aynı kurallarla koşmadı.

**Kapı 0 HARNESS düzeyindedir, pencere düzeyinde DEĞİL.** Kapı "bu harness canlı motoru
yeniden üretiyor mu" diye sorar; cevap harness'ın bir özelliğidir ve her yeni pencerede
yeniden kazanılması gerekmez. Bu ayrım pratik bir zorunluluktur: canlı scalp katmanı
2026-09-13'te başladı, yani ondan ÖNCEYE uzanan her pencerede canlı kayıt yoktur ve
karşılaştırma zorunlu olarak "yalnız backtest" dolu bir liste üretir. O liste bir sadakat
hatası DEĞİLDİR — ölçtüğü şey "canlı o tarihte henüz koşmuyordu"dur, ve onu kapı saymak
kapıyı veri kapsamının ölçüsüne çevirirdi.

Kural şu: Kapı 0, canlının TAMAMEN kapsadığı bir pencerede koşulur ve geçmelidir; geçtikten
sonra aynı config ile koşulan daha uzun pencereler o doğrulamaya dayanır. Config değişirse
(yukarıdaki iki karar gibi) kapı yeniden koşulur — çünkü o zaman ölçülen harness değil,
harness'ın okuduğu kurallar değişmiştir.

---

## 2. Ölçülen şey

**Birincil metrik: pozisyon başına ortalama R** — canlıyla birebir aynı tanım
(`core/metrics.py`, `merge_fills` ile pozisyona indirgenmiş). Toplam getiri birincil
DEĞİLDİR: bileşiklenme hızını ölçer, sinyal kalitesini değil.

İkincil (raporlanır, karara tek başına girmez): `R/gün` (ort.R × işlem/gün),
`max_drawdown_r`, `cost_per_r`, `avg_stop_distance_pct`, işlem/gün, long/short kırılımı.

---

## 3. Geçerlilik kapıları — sağlanmazsa satır OKUNMAZ

| # | Kapı | Eşik |
|---|---|---|
| **B-0** | Kapı 0 (§1) geçilmiş | zorunlu |
| **B-1** | Örneklem | R'ye giren kapanmış **pozisyon ≥ 30** (`acceptance.min_trades`) |
| **B-2** | Veri bütünlüğü | `missing_bars = 0` **ve** `unchecked_position_bars = 0` |

B-2'nin gerekçesi: ikisi de "o barda stop/TP/likidasyon hiç sorulmadı" demektir. Sıfırdan
büyükse pencere eksik bir geçmişin üstüne yazılmıştır ve sonucu yorumlanamaz.

---

## 4. Canlıya alma eşiği — **hepsi** sağlanmalı

Bir model ancak aşağıdakilerin **tamamı** sağlanırsa canlıya alınır:

| # | Koşul |
|---|---|
| **C-1** | ortalama R **> 0** |
| **C-2** | `random_ctrl`'ün ortalama R'sini **≥ 0.15R** marjla geçer (`acceptance.edge_margin_r`) |
| **C-3** | hesap getirisi referans çıpasını (`buyhold`) geçer |
| **C-4** | `avg_stop_distance_pct` yarışmacı bandının İÇİNDE (⚠B yanmıyor) |
| **C-5** | **C-1, C-2 ve C-3 OOS penceresinde de sağlanıyor** (§6) |

**Scalp katmanında C-3 değerlendirilemez:** o katmanda referans çıpası yoktur
(`vwap_clone` bir kopyadır, çıpa değil — kural 15b). `core/metrics.py::acceptance_flags`
bu durumu zaten `logger.warning` ile söyler ve koşul düşer. Scalp'e bir çıpa eklemek AYRI
bir karardır; bu belge onu varsaymaz ve eksik çıtayı geçilmiş çıta gibi göstermez.

---

## 5. Bilerek kabul edilen sapmalar

Bunlar sonuç görülmeden yazıldı ve sonuç okunurken hatırlanacak.

### 5a. `base` katmanı: `signals_per_bar` sapması — KABUL EDİLDİ

Canlıda base katmanı `signals_per_bar: false` ile ve 6 saatte bir koşar (4 saatlik barda),
yani bazı barların sinyal fırsatını hiç kullanmaz. Backtest ise her barın sinyalini üretir.

**Sonuç: base backtest'i canlıdan DAHA ÇOK işlem yapar.** Kapalı koşmak alternatif değil —
o hâlde tüm pencere tek bir sinyal üretir ve ölçüm hiç olmaz.

Bu sapma base katmanının backtest'ini **işlem sıklığı açısından iyimser** kılar. Ortalama
R'yi doğrudan şişirmez (R işlem başınadır), ama `R/gün` ve toplam getiri satırları canlıda
elde edilemeyecek bir kadansı varsayar. Base için **yalnızca ortalama R** karara girer;
`R/gün` ve toplam getiri bilgi olarak raporlanır.

Scalp katmanında böyle bir sapma yoktur: canlıda da `signals_per_bar: true`.

### 5b. Evren look-ahead

`base` evreni "24s hacme göre ilk 50"dir ve bugünün hacmiyle seçilmiştir; geçmiş bir
pencereye uygulamak survivorship bias taşır (bugün likit olan sembol o gün olmayabilirdi,
ve o gün likit olup bugün listeden düşmüş semboller hiç görünmez). `scalp`'in sabit 13
sembolü de bugün bilinerek seçildi.

Bu, modeller ARASI kıyası bozmaz (hepsi aynı evreni görür, kural 6) ama mutlak getiriyi
iyimser gösterir. **Backtest'in mutlak getirisi bir tahmin değildir.**

### 5c. Maliyet modeli varsayımdır, gözlem değil

`fee_rate`, `slippage_base`, `slippage_short_stop` ve funding birer varsayımdır. Backtest
canlıyla **aynı** varsayımı kullanır, dolayısıyla göreli kıyas geçerlidir; ama gerçek
dolum, kitap derinliği ve borsa kesintisi modellenmez.

### 5e. Dolum belirsizliği tek yönlü bir varsayımdır

Kural 13 aynı mumda hem stop hem hedef aralığa girdiğinde **kötü olanın (stop)
gerçekleştiğini** varsayar. Varsayım muhafazakârdır ve doğru taraftadır — iyimser olanı,
elde olmayan bir bilgiyle kâr yazmak olurdu — ama tek yönlüdür, yani sonuçları
sistematik olarak AŞAĞI çeker.

Payı artık ölçülüyor: `ModelReport.stop_exits` / `ambiguous_stop_exits` ve backtest
çıktısındaki **AYNI-BAR BELİRSİZLİĞİ** tablosu, stop'la kapanan pozisyonların kaçında
aynı mumun hedefe (ya da kısmi çıkış seviyesine) de değdiğini söyler.

**Sayı bir düzeltme değil, bir tetikleyicidir.** Oran küçükse varsayım tartışmaya değmez.
Büyükse sıra varsayımını oynatan bir duyarlılık koşusu gerekir — ve o koşu **yalnızca
harness'ta** yapılır: canlı defterin kuralı tek olmalıdır (aynı gerekçe `signals_per_bar`in
base'de kapalı tutulmasıdır — biriken geçmişin bir kısmı bir kuralla, kalanı başka bir
kuralla üretilemez). Duyarlılık koşusu bir sonuç DEĞİL, sonucun etrafındaki banttır.

### 5d. Veri derinliği

`data.history_bars` pencereyi sınırlar (base 600 bar ≈ 100 gün; scalp 1500 bar ≈ 15,6 gün).
Pencere derinliği aşarsa çıpanın serisi `last_processed_bar`'a ulaşmaz ve **B-2 kapısı
düşer** — sessiz bir kısalma olmaz, sayı raporda görünür.

---

### 5b. Derinlik override'ı (`--history-bars`)

`data.history_bars` bir ÖLÇÜM kuralı DEĞİLDİR: maliyet, risk, dolum, likidasyon ve metrik
tanımlarına dokunmaz; yalnızca anlık görüntünün ne kadar geriye gittiğini söyler. Backtest
onu DERİNLEŞTİREBİLİR.

**Neden config'te global olarak yükseltilmiyor:** canlı bundan faydalanmaz. Modellerin
lookback'leri sınırlıdır (`tail(300)`, `rolling(20)`, EMA50, gün-çapalı VWAP), yani daha
derin geçmiş canlı sinyalini değiştirmez — ama `data/` depoya girmediği için her saatlik
tur veriyi baştan indirir ve global bir artış, faydasız yere her turda kat kat indirme
demekti.

**"Değiştirmez" bir varsayım değil, SINANAN bir iddiadır:** aynı override ile koşulan
Kapı 0, canlı kayıtla birebir eşleşmeye devam etmelidir. Eşleşmezse override bir ölçüm
sapması üretiyor demektir ve kullanılamaz.

**Yalnızca derinleştirir, sığlaştırmaz** (`ValueError`): `tail(300)` okuyan bir model 200
barlık görüntüde BAŞKA bir sinyal üretir ve backtest artık canlıyı değil, kendi uydurduğu
bir modeli ölçerdi.

Kullanılan değer her koşuda `manifest.json > history_bars` altında (`config` ve `used`)
yazılı durur.

### 5f. Uzun pencere bir DERİNLİK sorunudur, bir niyet değil

"2-3 yıllık araştırma backtest'i" ayrı bir motor ya da ayrı bir mimari gerektirmez —
harness zaten canlı motoru geçmiş bir pencerede koşturur (§0). Gerektiren şey **veridir**
ve sınır ağdadır, diskte değil:

- OKX `history-candles` istek başına **100 bar** verir. 3 yıllık 15m verisi sembol başına
  ~105.000 bar, 13 sembol için ~13.600 istek demektir — throttle ile saatler.
- `data/` depoya GİRMEZ (çalışma zamanı verisi, denetim izi değil) ve runner her koşuda
  sıfırdan kurulur; önbelleksiz her koşu bu maliyeti baştan öder.

Bu yüzden `backtest.yml` mum önbelleğini koşular arasında **taşır** (`actions/cache`,
rolling anahtar). Önbellek ölçümü değiştiremez: `core/data.py` yalnızca EKSİK barları
çeker ve kapanmamış barı zaten atar (kural 12). Bayat bir önbellek **eksik** bar üretir,
yanlış bar değil — ve eksik bar B-2 kapısında (`missing_bars`,
`unchecked_position_bars`) görünür, sessizce geçmez.

**Pencere uzadıkça §5b'nin evren look-ahead uyarısı AĞIRLAŞIR.** `scalp`in sabit 13
sembolü bugün bilinerek seçildi; 2023'e uzanan bir pencerede bu listenin bir kısmı ya
listelenmemişti ya da ince kitaplıydı, ve o dönemde likit olup bugün düşmüş semboller hiç
görünmez. Uzun pencerenin MUTLAK getirisi bu yüzden bir tahmin değildir; modeller ARASI
kıyas geçerli kalır (hepsi aynı evreni görür, kural 6).

---

## 6. Kontaminasyon ve OOS

Bir modelin parametresi hangi veride seçildiyse o veri onun için **in-sample**'dır ve
üzerinde ölçülen başarı kanıt değildir.

| Model | Durum |
|---|---|
| `trend`, `meanrev`, `momentum`, `squeeze`, `confluence`, `failed_breakout`, `downtrend_rally`, `avwap`, `random_ctrl`, `ensemble`, `buyhold` | Parametreleri son 30 günün verisinden seçilmedi -> **tüm pencere OOS** |
| `scalp_bandit`, `scalp_fixed`, `scalp_managed` | Aynı -> **tüm pencere OOS** |
| `vwap_clone` | Kaynak sistemin parametreleri; bizim veriyle seçilmedi -> **OOS** |
| `vwap_guarded` | Parametreleri koşudan önce sabitlendi (ön-kayıt §6d) -> **OOS**; tek istisna SEMBOL SEÇİMİDİR (PENGU/ETHFI elemesi geçmiş defter bilinerek yapıldı) -> o eksende **IN-SAMPLE** |
| **`vwap_managed`** | `atr_multiple=2.5`, **17 Ağu – 16 Eyl 2026** verisinde seçildi (karar 26) -> o pencere **IN-SAMPLE** |
| **`vwap_fast`** (henüz kurulmadı) | `band_mult` aynı pencerede ölçüldü -> o pencere **IN-SAMPLE** |

**Kural:** `vwap_managed` ve `vwap_fast` için C-1..C-3, **17 Ağu 2026'dan ÖNCEKİ** bir
pencerede de sağlanmalıdır (C-5). O pencerede sağlanmıyorsa kalibrasyon o 30 güne
uydurulmuş demektir.

### 6.1 Embargo — OOS penceresinin başındaki boşluk

Parametresi `T` tarihine kadarki veriyle seçilmiş bir model için, OOS penceresini `T`'den
hemen başlatmak **temiz değildir.** Sebep pozisyonların zaman içinde ÖRTÜŞMESİDİR: IS
penceresinin son kurulumları `T`'den sonraki barlarda çözülür, yani o barların fiyat
hareketi IS etiketlerinin sonucuna zaten katkı yapmıştır. Aynı barları OOS'un ilk
sonuçları olarak okumak, "bağımsız pencere" iddiasını sessizce çürütür.

**Doğru boşluk tam olarak azami tutuş süresidir** (`scalp.time_stop_bars`; `patient` için
100 bar), çünkü bu geometride hiçbir pozisyon ondan uzun yaşamaz — zaman stop'u kapatır.
Daha uzun bir embargo veri harcar, daha kısası örtüşmeyi bırakır.

**Neden tam bir purged walk-forward makinesi değil.** Purging/embargo literatürdeki ağır
biçimiyle, etiketleri belirsiz sürelerle örtüşen ve ÇOK sayıda parametresi süpürülen
kurulumlar içindir. Burada ikisi de yok: tutuş süresi sınırlı ve BİLİNEN, süpürülmüş
parametre ise pratikte tek (`vwap.managed.atr_multiple`, karar 26 — ve o zaten §6'da
IN-SAMPLE olarak işaretli). `scalp.patient.time_stop_bars = 100` veriden değil teoriden
gelir (`N = (hedef/ATR)²`), `scalp_vol`'un eşiği medyandır (serbest parametre değil).
Yani düzeltilecek olan seçim yanlılığı burada elle ve açıkça yönetiliyor; eklenen tek şey
örtüşme boşluğudur. Süpürülen parametre sayısı 1'i geçtiğinde bu karar yeniden açılır.

**Uygulama:** `scripts/backtest.py --embargo-bars N`. Pencere N bar ileri kaydırılır ve
`manifest.json > window` hem `requested_start`i hem uygulanan `start`i yazar — boşluk
sessiz olamaz.

---

## 6b. ÖN-KAYIT — `scalp_vol` (model 17)

**Bu bölüm koşudan ÖNCE yazıldı ve commit edildi; tarih damgası git'tedir.** Sonuç
görüldükten sonra hiçbir satırı değiştirilmeyecek (§7).

**Hipotez.** Friksiyon notional'ın sabit bir yüzdesidir; sinyalin sürüklenmesi volatiliteyle
ölçeklenir. `scalp_patient`in başabaş noktası evrenin MEDYAN volatilitesindedir (karar 35:
brüt %0.261 ↔ maliyet %0.284). O hâlde medyanın ÜSTÜNDEKİ kurulumlarla sınırlanmış aynı
model, daha yüksek brüt sürüklenme üretmelidir.

**Eksen.** `scalp_patient` ↔ `scalp_vol`; ayrışan TEK şey kesitsel ATR% medyan kapısı.
Kol seçimi, kapılar, geometri, 100 barlık zaman stop'u ve çekiliş kimliği miras.

**Pencere.** 2026-07-19 → 2026-09-04, `--history-bars 6000`. Bu pencere `scalp_vol` için
TAZEDİR (model o koşudan sonra yazıldı). Sonuca göre kaydırılmayacak (§7.3).

**ÖN-KAYITLI TAHMİNLER** (sonucu görmeden):

| # | Ölçüm | Tahmin | Çürütür |
|---|---|---|---|
| **P1** | brüt sürüklenme% | `scalp_vol` > `scalp_patient` (%0.261) | ≤ %0.261 |
| **P2** | maliyet/R | `scalp_vol` < `scalp_patient` (0.124) — stop ATR ile büyür | ≥ 0.124 |
| **P3** | örneklem | n ≥ 30 | n < 30 → satır okunmaz (B-1) |
| **P4** | stop bandı | ⚠B yanmaz (C-4) | yanarsa kıyas geçersiz |

**P1 birincildir.** P1 tutmazsa "edge σ ile ölçeklenir" varsayımı YANLIŞTIR — o zaman edge
mutlak, maliyet oranlıdır ve bu geometride hiçbir 15m tezi kurtarılamaz. Bu, **olumsuz
çıktığında da değerli** bir ölçümdür ve o durumda doğru hamle katmanı kapatmak ya da maliyet
çalışmasına geçmektir.

**P2 bir SAĞLAMADIR, bir başarı ölçütü değil.** Kapı ATR'yi seçiyorsa stop mesafesi
büyümek zorundadır. P2 tutmazsa kapı ATR'yi değil başka bir şeyi seçmiştir ve P1'in sonucu
yorumlanamaz.

**Canlıya alma eşiği (§4) AYRICA geçilmelidir.** P1'in tutması `scalp_vol`u canlıya almaz;
C-1 (ort. R > 0) ayrı bir çıtadır ve `scalp_patient` onu −0.01 ile geçememişti.

**Çoklu karşılaştırma (§7.5).** Araştırmadan 10 öneri çıktı; bu, test edilen **1.**sidir.
Sonuç raporlanırken bu sayı yazılacak.

---

## 6d. ÖN-KAYIT — `vwap_guarded` (model 18)

**Bu bölüm koşudan ÖNCE yazıldı ve commit edildi; tarih damgası git'tedir.** Sonuç
görüldükten sonra hiçbir satırı değiştirilmeyecek (§7).

**Model ne.** `vwap_clone`un (model 13) CANLIYA HAZIRLANMIŞ uyarlaması: seans (UTC gün)
çapalı VWAP, 2.5σ taban bant (LONG tarafında 3.0σ), rejim kapıları (ADX(14) > 22 **veya**
EMA50 eğimi > 1.5×ATR **veya** BTC'nin saatlik trendine karşı fade), uçta tükenme şartı
(klimaks hacmi ya da red mumu), risk boyutlandırma (kural 11; sabit teminat YOK), 10 barlık
zaman stop'u, günlük −2R zarar limiti, %8 (= 8R) drawdown kill-switch'i ve aynı yönde en
çok 2 pozisyonluk korelasyon kotası.

**Kopyaya DOKUNULMADI** (kural 15b): model 13 kendi kurallarıyla koşmaya devam eder.

**Bu bir EKSEN DEĞİL, bir toplam farktır** — 13 ↔ 14 satırının aynı durumu. Sekiz kalem
birden ayrışır (çapa, bant, rejim, tükenme, boyutlandırma, zaman stop'u, evren, risk
kesicileri), yani sonuç "kapıların katkısı" olarak OKUNAMAZ; okunabilen tek şey "iki
sistemin toplam farkı"dır. Tek değişkenli bir eksen isteniyorsa yolu yeni bir model
açmaktır.

**Pencereler.** İkisi de 17 Ağustos 2026'dan ÖNCEDİR, çünkü model `atr_multiple=2.5`
değerini karar 26'dan devralır ve o değer **17 Ağu – 16 Eyl 2026** verisinde seçilmiştir
(§6): o pencere model 18 için de IN-SAMPLE'dır.

| Koşu | Pencere | Derinlik |
|---|---|---|
| **A (birincil)** | 2026-06-25 → 2026-08-16 | `--history-bars 6000` |
| **B (doğrulama)** | 2026-05-01 → 2026-06-24 | `--history-bars 6000` |

İki pencere bitişiktir ve A'nın başı B'nin sonundan sonradır; B, A'nın sonucunu
doğrulamak için vardır (C-5'in "başka bir pencerede de sağlanıyor" koşulu). Sonuca göre
kaydırılmayacak (§7.3).

**Öğrenme.** Model kopyanın epsilon-greedy iskeletini KORUR (3 bant × 3 hedef oranı = 9
kombinasyon, sembol bazlı istatistik, keşif payı sıfıra inmez) ve üç yerde ayrışır: ödül
drawdown ile cezalandırılır (`skor = (Σr − w × maxDD) / n`, `w = 1.0`), sembol eşiği 3
değil **30**'dur (`acceptance.min_trades` ile aynı sayı) ve keşif payı 0.25 değil
**0.10**'dur. İşlem maliyeti ödülün İÇİNDEDİR ve bu bir ekleme değildir: ödülün girdisi
`r_multiple = pnl / risk_amount`tır ve `pnl` komisyon, kayma ve funding düşülmüş nettir
(test: `tests/test_live_readiness.py::test_realized_pnl_is_net_of_fee_and_slippage`).

Öğrenme modeli **uyarlanabilir** yapar (§1): Kapı 0'da ondan birebir eşleşme BEKLENMEZ ve
bu, `scripts/backtest.py::is_adaptive` tarafından `observe_closed_trades` kancasından
TÜRETİLİR — elle yazılmış bir liste bugün doğru, yarın sessizce yanlış olurdu.

Grid'in ekseni bant ve hedef oranıdır; **stop çarpanı (`atr_multiple`) SABİTTİR.** Stop
mesafesi aynı zamanda maliyet ölçeğidir (kural 14) ve onu öğrenmeye açmak, modelin kendi
⚠B bandını koşu sırasında kaydırması demekti — C-4 o zaman neyi ölçtüğünü söyleyemezdi.

**TADİLAT NOTU (aynı gün, SONUÇ GÖRÜLMEDEN).** Bu bölümün ilk hâli öğrenmesiz bir model
tarif ediyordu; kullanıcının listesindeki C maddeleri (ödül, epsilon, sembol eşiği) tam
olarak öğrenmeye dairdir ve modele eklendi. İlk hâliyle tetiklenen iki koşu **iptal
edildi ve hiçbir çıktısı okunmadı** — §7.1'in yasakladığı şey "sonucu görüp parametre
değiştirmek"tir; burada görülen bir sonuç yoktur, bu yüzden pencereler, tahminler ve
metrik AYNEN kalır ve koşu yeni modelle baştan yapılır.

**Kıyas kümesi:** `vwap_guarded`, `vwap_clone`, `vwap_managed`, `scalp_fixed`,
`random_ctrl`. Kontrol olmadan C-2 değerlendirilemez; `scalp_fixed` katmanın kontrol
geometrisidir.

**ÖN-KAYITLI TAHMİNLER** (sonucu görmeden):

| # | Ölçüm | Tahmin | Çürütür |
|---|---|---|---|
| **P1** | ortalama R (`vwap_guarded`) | **> 0** | ≤ 0 |
| **P2** | ortalama R farkı | `vwap_guarded` > `vwap_clone` | ≤ 0 |
| **P3** | örneklem | n ≥ 30 (B-1) | n < 30 → satır OKUNMAZ |
| **P4** | stop bandı | ⚠B yanmaz (C-4) | yanarsa kıyas geçersiz |
| **P5** | işlem sayısı | `vwap_guarded` < `vwap_clone` | tersi: kapılar elemiyor demektir |
| **P6** | öğrenmenin izi | `pick=exploit` işlemlerinin ortalama R'si `pick=explore`den YÜKSEK | düşükse öğrenme gürültü seçiyor demektir |

**P1 birincildir** ve canlıya alma eşiğinin C-1'i ile aynı sayıdır. P2 ikincildir ve tek
başına bir başarı ölçütü DEĞİLDİR: kopya kendi boyutlandırmasıyla koşar, yani iki satırın
R'si aynı paydadan gelmez (kural 15b). P5 bir SAĞLAMADIR: kapılar gerçekten eliyor mu?

**P3 için beklenen zorluk yazılıdır:** altı kapının üst üste binmesi kurulum sayısını
düşürür ve n=30 bu katmanda zaten zor geçiliyor. n < 30 çıkarsa sonuç "model kötü" değil,
**"bu kapı kümesi bu pencerede ölçülemez"** demektir ve doğru hamle kapıyı gevşetmek
DEĞİL (o, sonucu görüp parametre oynatmaktır — §7.1), daha uzun bir pencere ya da
katmanı kapatmaktır.

**SEMBOL ELEMESİ BİR SAPMADIR ve burada işaretlenir.** Model evreninden PENGU ve ETHFI
çıkarıldı. §7.2 bunu ("şu sembolü çıkarsak") açıkça yasaklar ve karar 40 bir kez
reddetti. Eleme kullanıcının açık talimatıdır, koşudan ÖNCE modelin bir ÖNSELİ olarak
sabitlendi ve sonuç **sembol seçimi açısından IN-SAMPLE'dır**: geçmiş defterde o iki
sembolün kötü olduğu BİLİNEREK çıkarıldılar. Dolayısıyla model 18'in ortalama R'si bu
iki sembolün dışlanmasından gelen bir üstünlük taşır ve bu, C-5 bile geçilse iddia
edilmeyecek bir paydır. Eleme etkisinin kendisi ancak aynı modeli TAM evrenle koşturan
ayrı bir hipotezle ölçülebilir; o hipotez bu ön-kayıtta YOKTUR.

**Canlıya alma.** Kullanıcı talimatı açıktır: model backtest'ten sonra kâğıt katmanında
(`layers.scalp.models`) KOŞACAKTIR. Bu, §4'ün canlıya alma eşiğinin geçildiği anlamına
GELMEZ ve o eşik burada ayrıca raporlanır — `scalp_patient`in (model 16) kâğıt katmanına
alınırken kurulan aynı ayrım: kâğıtta koşmak ileriye dönük kanıt biriktirmektir, gerçek
parayla işlem açma izni değildir.

**Çoklu karşılaştırma (§7.5).** Bu, sicildeki **2.** hipotezdir.

---

## 6e. ÖN-KAYIT — `vwap_guarded` (model 18), σ birimi düzeltildikten SONRA

**Bu bölüm koşudan ÖNCE yazıldı ve commit edildi; tarih damgası git'tedir.**

**Neden ikinci bir ön-kayıt.** §6d'nin koşusu (#13) iki şey gösterdi: (a) `random_ctrl`
scalp katmanında koşturulamaz (kol etiketi yazmaz, katmanın kırılımı `TagError` fırlatır);
(b) model 52 günlük pencerede **tek bir kurulum bile üretmedi**, çünkü "2.5σ" sayısı
kaynağın σ'suna aitken seansın hacim ağırlıklı gün içi σ'suyla uygulanmıştı — kat kat
seçici bir eşik (karar 45). Düzeltme σ TAHMİNCİSİNİ kaynağınkine döndürür (seans
VWAP'inden sapmanın son 20 barlık örneklem sapması); çapa SEANS olarak kalır.

Bu bir parametre değişikliğidir, dolayısıyla **§7.1 gereği yeni bir hipotezdir ve TAZE bir
pencere ister.** Pencere A (2026-06-25 → 08-16) bu karar için artık IN-SAMPLE'dır: orada
"sıfır kurulum" görüldü. Koşu B'nin penceresi §6d'de zaten yazılıydı ve o güne kadar
HİÇ KOŞULMADI.

**Pencere (tek).** 2026-05-01 → 2026-06-24, `--history-bars 6000`. 17 Ağustos 2026'dan
öncedir (§6: `atr_multiple` kalibrasyonunun IS penceresi dışında).

**Kıyas kümesi:** `vwap_guarded`, `vwap_clone`, `vwap_managed`, `scalp_fixed`.
`random_ctrl` ÇIKARILDI — katmanda koşamıyor. **Sonucu şudur: C-2 (edge, kontrolü marjla
geç) bu katmanda DEĞERLENDİRİLEMEZ**, tıpkı C-3 (çıpayı geç) gibi. Eksik çıta, geçilmiş
çıta sayılmaz; kıyasın kalan çıpası `scalp_fixed`tir ve o bir KONTROL DEĞİL, katmanın
geometrisidir.

**ÖN-KAYITLI TAHMİNLER** (sonucu görmeden; §6d'nin listesiyle aynı, P2 ve P5 kopyaya
karşı okunur):

| # | Ölçüm | Tahmin | Çürütür |
|---|---|---|---|
| **P1** | ortalama R (`vwap_guarded`) | **> 0** | ≤ 0 |
| **P2** | ortalama R farkı | `vwap_guarded` > `vwap_clone` | ≤ 0 |
| **P3** | örneklem | n ≥ 30 (B-1) | n < 30 → satır OKUNMAZ |
| **P4** | stop bandı | ⚠B yanmaz (C-4) | yanarsa kıyas geçersiz |
| **P5** | işlem sayısı | `vwap_guarded` < `vwap_clone` | tersi: kapılar elemiyor |
| **P6** | öğrenmenin izi | `pick=exploit` ort. R > `pick=explore` | düşükse öğrenme gürültü seçiyor |

**P1 birincildir.** P3 ayrı bir öneme sahiptir: §6d'de sıfır çıktı ve düzeltmenin
ölçtüğü ilk şey modelin YAŞAYIP yaşamadığıdır. n yine 30'un altında kalırsa sonuç "model
kötü" değil, **"bu kapı kümesi bu pencerede ölçülemez"**tir ve doğru hamle kapıyı
gevşetmek DEĞİL (o, sonucu görüp parametre oynatmaktır), daha uzun bir pencere ya da
tezden vazgeçmektir.

**Sembol elemesi sapması §6d'deki gibi geçerlidir** (PENGU/ETHFI; sonuç o eksende
IN-SAMPLE).

**Çoklu karşılaştırma (§7.5).** Bu, sicildeki **3.** hipotezdir.

---

## 6c. ÖN-KAYIT SİCİLİ — her hipotez, sonucu ne olursa olsun, buraya yazılır

**Bu tablo §7.5'in ("çoklu karşılaştırma açıkça raporlanır") tutulan hâlidir.** §7.5 bir
RAPORLAMA kuralıydı ve sayıyı tutan bir yer yoktu; karar 36 "10 önerinin 1.'si" diye
saymaya başlamıştı ama sayaç hiçbir yerde durmuyordu. Sicil olmadan çoklu karşılaştırma
düzeltmesi yapılamaz, çünkü düzeltme tam olarak "kaç test yapıldı" sayısına dayanır.

**Kayıt kuralı:** bir hipotez koşulmadan ÖNCE satırı açılır (ön-kayıt commit'i ile), koşu
bittiğinde sonucu yazılır. **Düşen hipotez silinmez.** Silmek, paydayı küçültüp kalan
sonuçları olduğundan anlamlı gösterirdi — yayın yanlılığının kendisi budur ve bu projede
onu üretecek olan tek şey bu tabloyu düzenlemektir.

| # | Hipotez | Ön-kayıt | Pencere | Birincil tahmin | Sonuç |
|---|---|---|---|---|---|
| 1 | `scalp_vol`: edge σ ile ölçeklenir | §6b, commit `a7c08ae` | 2026-07-19 → 09-04 | P1: brüt sürüklenme% `vol` > `patient` | **DÜŞTÜ** (0.253 < 0.263) — karar 36 |
| 2 | `vwap_guarded`: canlıya hazırlık kapıları kopyanın beklentisini pozitife çevirir | §6d | A: 2026-06-25 → 08-16 | P1: ortalama R > 0 | **ÖLÇÜLEMEDİ** — σ birimi hatası: 52 günde 0 kurulum; koşu ayrıca `random_ctrl` yüzünden düştü (karar 45) |
| 3 | `vwap_guarded` (σ birimi düzeltilmiş): aynı tahminler, taze pencere | §6e | B: 2026-05-01 → 06-24 | P1: ortalama R > 0 | **P3 DÜŞTÜ** (n=8 < 30) → P1 değerlendirilemez (+0.30R, aralık [−0.19, +0.69]); 0.1 işlem/gün — karar 45 |

**Araştırmadan çıkan öneri sayısı: 10.** Bunların 3'ü test edildi (yukarıdakiler), 4'ü
ölçüm katmanı olduğu için hipotez DEĞİLDİR ve sicile girmez (kabul kapısı, belge
senkronu, dolum belirsizliği sayımı, sicilin kendisi — hiçbiri bir modelin performansı
hakkında bir iddia taşımaz), 2'si reddedildi (işlem sıklığı tavanı, sembol eleme), 3'ü
kuyrukta (maliyet modeli, rejim filtresi, portföy tavanı).

### Çoklu karşılaştırma düzeltmesi: Benjamini-Hochberg

`m` ön-kayıtlı birincil tahminin `p` değerleri küçükten büyüğe sıralanır (`p₍₁₎ ≤ … ≤ p₍ₘ₎`)
ve `p₍ᵢ₎ ≤ (i/m) × q` koşulunu sağlayan en büyük `i`ye kadarki hipotezler kabul edilir;
`q = 0.10` (yanlış keşif oranı).

**Neden Bonferroni değil.** Bonferroni aile düzeyinde HATA OLASILIĞINI kontrol eder ve 10
test için eşiği 0.005'e indirir; bu geometride (n≈200, ort. R ≈ −0.01) gerçek bir edge
bile o eşiği geçemez, yani prosedür her şeyi eler ve ölçüm anlamını yitirir. BH ise
yanlış keşif ORANINI kontrol eder: bir tanesinin yanlışlıkla geçmesini tamamen engellemez
ama geçenlerin çoğunluğunun gerçek olmasını sağlar. Bir kâğıt katmanında doğru denge budur
— yanlış bir modeli kâğıtta koşturmanın bedeli, gerçek bir edge'i hiç görmemenin
bedelinden küçüktür.

**`q` sonucu görmeden sabitlendi ve §7.4 gereği sonuca göre değiştirilmeyecektir.**

**Düzeltme kabul çıtasının (`acceptance`) YERİNE geçmez, ONDAN SONRA gelir.** Çıta tek bir
modelin kendi verisiyle cevaplanabilen soruyu sorar ("bu satır okunabilir mi"); BH ise
"kaç hipotez denendi" sorusunu sorar ve cevabı tek bir modelin defterinde yoktur. Bir
model iki kapıyı geçip BH'de düşebilir; o zaman sonuç "edge yok" değil, **"bu kadar
denemeden sonra bu kadar farkı görmek beklenirdi"** demektir.

Bu liste bağlayıcıdır. İhlal edilirse backtest bir ölçüm olmaktan çıkar.

1. **Parametre değiştirip yeniden koşmak yok.** Değiştirirsek bu YENİ bir hipotezdir ve
   TAZE bir OOS penceresi gerektirir; eski pencerede "artık geçiyor" demek geçersizdir.
2. **Post-hoc filtre yok** — "şu sembolü çıkarsak", "şu saati elesek", "şu ayı atlasak".
3. **Pencereyi sonuca göre kaydırmak yok.** Pencere §6'nın kurallarıyla, sonuç görülmeden
   sabitlenir.
4. **Metrik değiştirmek yok.** Ortalama R kötü çıkıp "ama Sharpe iyi" demek yasaktır;
   birincil metrik §2'de sabittir.
5. **Çoklu karşılaştırma açıkça raporlanır VE sicile yazılır** (§6c). Kaç model test
   edildi ve kaçı geçti, sonucun yanında yazılır; hipotez sicile koşudan ÖNCE girer ve
   **sonucu ne olursa olsun orada kalır.** 6 modelden 1'inin kıl payı geçmesi gürültüdür,
   bulgu değil — ~%26 olasılıkla şansa bağlıdır. Düşen satırı sicilden silmek, paydayı
   küçültüp kalanları olduğundan anlamlı göstermek olurdu.

---

## 8. Backtest'in cevaplayamayacağı sorular

Bunlar eksiklik değil, kapsam dışıdır; sonuç okunurken iddia edilmeyecek şeylerdir.

- Gerçek dolum fiyatı ve kitap derinliği
- Borsa kesintisi, API arızası, likidite kuruması
- Rejim değişimi (geçmişte kârlı olan gelecekte kârlı değildir)
- Kayma varsayımının ince kitaplı sembollerde tutup tutmadığı
  (`breakdowns.symbol > cost_per_r` ipucu verir ama kanıt değildir)

---

## 9. Koşunun kendisi denetlenebilir olmalı

Her backtest koşusu çıktısına şunları yazar: katman, pencere (başlangıç/bitiş), model
listesi, **config parmak izi** (koşuda geçerli tüm ayarların özeti) ve harness sürümü
(git SHA). Aynı girdilerle tekrar koşulduğunda aynı sonucu vermelidir — `random_seed`
sabittir ve rastgelelik kullanan her yol ondan beslenir.

Sonuç dosyaları `backtests/` altına yazılır ve **depoya girmez**: bir backtest ölçümün
kendisi değil, ölçüm hakkında bir denemedir. Karara giren sayılar bu belgeye ve
`docs/decisions.md`'ye yazılır.

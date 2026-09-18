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
| **Uyarlanabilir** (`scalp_bandit`, `vwap_clone`) | Eşleşme BEKLENMEZ ve aranmaz: ikisi de kendi kapanmış işlemlerinden öğrenir (kural 16), boş defterden başlayan bir koşu farklı bir geçmiş görür. Rapor edilir, kapı sayılmaz. |

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

### 5g. Harness override'ları İKİ SINIFA ayrılır ve karıştırılmazlar

Bayrakların hepsi "koşuyu değiştiren ayar" değildir; ikisi arasındaki fark, sonucun nasıl
okunacağını belirler.

**Sınıf 1 — sonucu DEĞİŞTİRMEMESİ beklenen (ve sınanan) ayarlar.** `--history-bars`
(§5b) ve `--funding-periods`: ikisi de anlık görüntünün derinliğini artırır. İddia
"modelin gördüğü sinyal değişmez"dir ve bu bir varsayım değil, Kapı 0'da SINANAN bir
iddiadır. İkisi de yalnızca DERİNLEŞTİRİR; sığlaştırma `ValueError`dır, çünkü sığ veri
modeli canlıda olmadığı bir şeye çevirir. `--funding-periods`in ayrıca bir yönü vardır:
varsayılan 180 periyot ≈ 60 gündür ve yıllara uzanan bir pencerede kaydı olmayan anda
`core/funding.py::rate_at` None döner — funding HİÇ işlenmez (uydurma yok), yani eski
dönem sistematik olarak İYİMSER çıkar. Derinleştirmek bu yanlılığı kapatır.

**Sınıf 2 — sonucu DOĞRUDAN kaydıran ayarlar.** `--fee-rate`, `--slippage-base`,
`--symbols`, `--signal-cutoff`. Bunlar ölçümün koşullarını değiştirir ve sonucu canlı
defterle aynı dünyada bırakmaz:

| Bayrak | Ne için | Neden canlıya girmez |
|---|---|---|
| `--fee-rate`, `--slippage-base` | dış bir referansla (başka bir backtest aracı) parite | kural 6: maliyet tüm modeller için tek kaynaktır; `fee_rate` bir kez değiştiğinde defteri tarihli olarak böler (karar 25) |
| `--symbols` | tek sembollü parite koşusu | çok sembollü koşuda portföy kotası (`max_positions`) sinyal REDDEDER ve dışarıdaki tek sembollü bir koşuyla kıyas, modelin değil kotanın ölçüsü olur. Evreni yalnızca DARALTIR: genişletmek katmanın tanımını (kural 6) harness'a devretmek olurdu |
| `--signal-cutoff` | dönem ataması | bir işlem GİRİŞ tarihine göre döneme aittir; dönemin son kurulumları sınırı aşsa bile kapanışına kadar o döneme sayılır. Kesim YENİ sinyali durdurur, pozisyon yönetimini DURDURMAZ — dondurmak, o kurulumları kendi çıkış kurallarından mahrum bırakıp sonucu uydururdu |

Sınıf 2'nin her kullanımı `manifest.json > deviations` altında durur ve maliyet override'ı
ayrıca `logger.warning` ile bağırır: **bir sayının hangi dünyada ölçüldüğü, sayının kendisi
kadar ölçümün parçasıdır.**

---

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
| **`vwap_managed`** | `atr_multiple=2.5`, **17 Ağu – 16 Eyl 2026** verisinde seçildi (karar 26) -> o pencere **IN-SAMPLE** |
| **`vwap_fast`** (henüz kurulmadı) | `band_mult` aynı pencerede ölçüldü -> o pencere **IN-SAMPLE** |
| **`ema_trend`** | Parametreleri dış bir sistemde (TradingView), **2022-01 – 2024-06** BTC verisinde doğrulandı -> o pencere **IN-SAMPLE**; 2024-07 sonrası OOS (§6d) |

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
| 2 | `ema_trend`: EMA(21/55) kesişimi long-only bir edge taşır (dış sistemden) | §6d, bu commit | A: 2022-01-01 → 2024-06-30, B: embargo sonrası → koşu günü | P1: BTC tek-sembollü PF A 1.551±0.2 / B 1.299±0.2 | _koşulmadı_ |

**Sicildeki 2. satır bu paydaya AİT DEĞİLDİR:** `ema_trend` hipotezi dış bir sistemden geldi, ev içi arama uzayından seçilmedi (bkz. §6d > Çoklu karşılaştırma). İki payda ayrı tutulur.

**Araştırmadan çıkan öneri sayısı: 10.** Bunların 1'i test edildi (yukarıdaki), 4'ü
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

## 6d. ÖN-KAYIT — `ema_trend` (model 18) ve `ema` katmanı

**Bu bölüm koşudan ÖNCE yazıldı ve commit edildi; tarih damgası git'tedir.** Sonuç
görüldükten sonra hiçbir satırı değiştirilmeyecek (§7).

**Kökeni dışarıdır.** Hipotez bu projenin araştırma listesinden (§6c) çıkmadı: modelin
kuralları dış bir sistemde (TradingView / Pine Script) geliştirildi ve orada doğrulandı.
Bu, kopyalardan (kural 15b) farklıdır — kopya dış sistemin BOYUTLANDIRMASINI da taşır,
bu model ise evin kurallarıyla (risk %1, `leverage_cap`, ev maliyetleri) koşan tam bir
YARIŞMACIDIR. Dışarıdan gelmesi ön-kaydı gereksiz kılmaz, tersine zorunlu kılar: dış
sistemin doğruladığı pencere bizim için de IN-SAMPLE'dır (§6).

### Sabitlenen kurallar — koşudan sonra DEĞİŞMEZ

| | |
|---|---|
| Yön | **yalnızca long** (`allowed_directions=["long"]`); short kaynak sistemde denendi ve sistematik kaybettiriyordu |
| Giriş | EMA(21), EMA(55)'i YUKARI keser (önceki bar `fast <= slow`, bu bar `fast > slow`) |
| Stop | giriş barının kapanışı − `1.5 × ATR(14)` |
| Hedef | giriş + (giriş − stop) × `2.0`, tek dilim (`fraction=1.0`) |
| Trailing | **yok** |
| Zaman stop'u | **yok** (bkz. embargo notu) |
| Pozisyon | sembol başına tek; tekrar sinyal `core/portfolio.py`'de `duplicate_position` ile reddedilir (model kendi pozisyonunu göremez — kural 4/16) |
| Dolum | kural 13: sinyal bar kapanışında, emir bir SONRAKİ barın açılışında |

**Parametreler bu koşu için SABİTTİR.** Sonuç kötü çıkarsa ayar aranmaz (§7.1); zaman
stop'u eklemek dâhil her değişiklik YENİ bir hipotezdir ve taze bir OOS penceresi ister.

### Katman

Model `ema` katmanında koşar (4H, `config.yaml > layers.ema`): sabit 13 sembol (scalp
katmanının evreni), `ledgers_ema/`, `docs/data/metrics_ema.json`. Katmanın model listesi
`ema_trend` ile birlikte `trend` (kıyas hedefi), `random_ctrl` (kabul çıtasının kontrolü)
ve `buyhold` (çıpa, C-3) içerir.

**Neden base katmanına değil.** Backtest 13 sabit coinde koşuyor; base evreni hacme göre
seçilen 50 coindir ve zamanla kayar. Modeli base'e almak, backtest'in ölçtüğü evrenden
başka bir evrende koşturmak olurdu. Ayrı katman ikisini eşitler. Bedeli katmanlar arası
kıyasın yapılamamasıdır (CLAUDE.md > Katmanlar) — bu yüzden kıyas hedefi `trend` KATMANIN
İÇİNE alınır: aynı evren, aynı barlar, aynı maliyet, aynı defter kuralları.

### Pencereler ve dönem atama

| Dönem | Aralık | Durum |
|---|---|---|
| **A** | 2022-01-01 → 2024-06-30 (sinyal kesimi) | **IN-SAMPLE** — kaynak sistem parametrelerini bu veride doğruladı |
| **B** | 2024-06-30 + embargo → koşu günü | **OOS** |

**Dönem atama ölçütü GİRİŞ tarihidir.** Dönem A'da açılan bir işlem sınırı aşsa bile
kapanışına kadar A'ya sayılır. Uygulaması harness'ta `--signal-cutoff`tur: kesimden
sonraki barlar pozisyon yönetimi için işlenir (stop/TP/likidasyon/funding) ama YENİ sinyal
üretilmez. Kuyruk 2024-12-31'de biter; o tarihte hâlâ açık olan pozisyonların SAYISI
raporlanır ve kapanmış işlem istatistiğine girmez (girseydi gerçekleşmemiş bir sonuç
ölçüme girerdi).

Kesim olmadan alternatif, sınırda açık olan pozisyonları düşürmekti; bu, dönem A'nın en
uzun yaşayan kurulumlarını sistematik olarak eleyip ortalamayı kısa işlemlere doğru
çekerdi.

### Embargo

Modelin zaman stop'u YOKTUR, yani §6.1'in "doğru boşluk azami tutuş süresidir" kuralının
dayandığı üst sınır bu modelde tanım gereği yok. Bu yüzden sınır ÖLÇÜLÜR: dönem A'da
gözlenen **azami tutuş süresi** embargo olarak uygulanır ve uygulanan değer
(`manifest.json > window.embargo_bars`) raporlanır.

**Tutuş süresi dağılımı ayrıca raporlanır** (medyan, 90. yüzdelik, azami — bar ve gün
cinsinden). Bu bir yan çıktı değil, ön-kayıtlı bir ölçümdür: uzun kuyruk, pozisyonların
hedefe/stop'a varmadan beklediğini gösterir ve P2'nin bağımsız kontrolüdür.

### Maliyet — bu koşu canlı config ile AYNI DEĞİLDİR

| | backtest | canlı (`config.yaml`) |
|---|---|---|
| `fee_rate` | **0.00075** (tek yön) | 0.00055 |
| `slippage_base` | **0.0001** | 0.0005 |

Talep eden taraf modelin sahibidir (TradingView koşusunun varsayımı). `config.yaml`
DEĞİŞTİRİLMEZ: kural 6 maliyeti tüm modeller için tek kaynağa bağlar ve `fee_rate` bir kez
değiştiğinde defteri tarihli olarak böler (karar 25). Bu yüzden sapma harness'ta,
`--fee-rate` / `--slippage-base` bayraklarıyla ve `manifest.json`'a yazılarak uygulanır.

**"2 tick" oransal kayma modeline birebir çevrilmez:** tick boyu sembole göre değişir ve
sembole bağlı kayma AYRI bir karardır (CLAUDE.md > `scripts/measure_slippage.py`). 0.0001
(1bp) bunun düz bir temsilidir ve seçim sonuçtan önce yapılmıştır.

**Yön bilinmektedir:** backtest komisyonda canlıdan pahalı (+0.0002 × 2 bacak), kaymada
ucuzdur (−0.0004 × 2 bacak); net etki tipik bir kurulumda kaymanın lehinedir, yani bu
koşu canlı maliyetten **daha ucuz** bir dünyayı ölçer. Forward test bu yüzden backtest'in
biraz ALTINDA kalmalıdır; tersi bir sürpriz değil, bir uyarıdır.

### Kapılar — İKİSİ DE bağlayıcı

**Repo kapıları birincildir** (§3 B-0/B-1/B-2 ve §4 C-1..C-5). Gerekçe ölçümün kendisidir:
diğer modeller bu eşikten geçti; yeni bir modele daha gevşek kapı açmak, onu iyi olduğu
için değil kapısı kolay olduğu için önde gösterirdi (kural 6).

**Model sahibinin kapısı EK filtredir**, yerine geçmez:

| # | Koşul |
|---|---|
| **K-1** | En az **6 coinde** dönem B kâr faktörü **> 1.1** (tek-sembollü koşulardan) |
| **K-2** | Toplam işlem sayısı (tüm coinler, dönem A+B) **> 300** |
| **K-3** | Hiçbir coinde max drawdown **%25'i geçmiyor** |

**K-3'ün tanımı** (sonuçtan önce sabitlenir): hesap tektir ve coin başına bölünemez
(ortak nakit, ortak margin — CLAUDE.md > Rapor Kolonları'nın yön bazlı Sharpe için verdiği
aynı gerekçe). Bu yüzden coin bazlı drawdown, o coinin **kümülatif PnL eğrisinin** en
büyük tepe-dip düşüşünün BAŞLANGIÇ SERMAYESİNE oranıdır (`initial_capital`, 10.000).
Hesap düzeyi `max_drawdown` ayrıca ve ayrı olarak raporlanır.

### Tek istisna — ve o da sonuçtan ÖNCE yazılıyor

Model **YALNIZCA C-3'ten** (hesap getirisi `buyhold` çıpasını geçer) kalıyor ve diğer tüm
kapıları (B-0..B-2, C-1, C-2, C-4, C-5, K-1..K-3) geçiyorsa: koşu **DURUR**, karar model
sahibine gider, otomatik geçiş YOKTUR. Başka herhangi bir kapıdan kalırsa doğrudan
**BLOKE** — istisna tek bir kapı içindir ve genişletilemez.

Gerekçe: C-3 diğer kapılardan farklı bir soru sorar ("piyasayı yendi mi") ve cevabı
modelin kendi verisiyle değil, ölçüldüğü pencerenin yönüyle belirlenir — 2022-2026
penceresinde BTC yaklaşık %110 yükseldi. Bu, kapıyı yumuşatmak için gerekçe DEĞİLDİR;
istisna kapıyı kaldırmıyor, kararı otomatikten insana taşıyor.

### ÖN-KAYITLI TAHMİNLER (sonucu görmeden)

| # | Ölçüm | Tahmin | Çürütür |
|---|---|---|---|
| **P1** | BTC tek-sembollü PF | A: **1.551 ± 0.2**, B: **1.299 ± 0.2** (TradingView referansı) | dışına çıkması → harness ↔ TV ayrışması araştırılır, sonuç YORUMLANMAZ |
| **P2** | hedefe ulaşma oranı | `tp` ile kapanan pozisyon payı **< %35** | ≥ %35 |
| **P3** | ortalama R | **> 0** (C-1) | ≤ 0 |
| **P4** | çıpa | hesap getirisi `buyhold`un **ALTINDA** (C-3 DÜŞER) | çıpayı geçerse |

**P1 birincildir ve bir KAPIDIR, bir tahmin değil.** Tutmazsa hiçbir sayı yorumlanmaz ve
paper trading'e geçilmez — modelin iyi ya da kötü olduğu değil, harness'ın kaynak sistemi
yeniden üretmediği anlaşılır. Tek-sembollü koşu şart: portföy kotası (`max_positions: 5`)
13 coinlik koşuda BTC sinyallerinin bir kısmını reddeder ve tek sembollü bir TradingView
koşusuyla kıyas geçersiz olurdu.

**P4 modelin sahibinin açık beklentisidir** (TradingView'de BTC 2022-2026: strateji
+%4.89, al-tut +%110) ve tam da bu yüzden ön-kayda yazılıyor: sonucu görüp "zaten
biliyorduk" demek ile önceden yazmak arasındaki fark, ölçümün kendisidir.

### Bu koşuya özgü, KABUL EDİLEN sapmalar (§5'in üstüne)

Kaynak sistemle birebir eşleşme beklenmez; sebepler sonuçtan önce yazılıdır:

1. **ATR tanımı.** Repo `core/indicators.py` TR'lerin DÜZ ortalamasını kullanır;
   TradingView `ta.atr` Wilder (RMA) yumuşatmasını. Projenin tek ATR tanımı vardır
   (`trailing.atr_period`) ve modele özel bir ATR, aynı "1.5×ATR" ifadesinin modelden
   modele farklı mesafe anlamına gelmesi demekti. P1 düşerse ilk sınanacak ayrışma budur.
2. **Veri kaynağı.** Repo OKX perpetual (`BTC-USDT-SWAP`); kaynak sistemin grafiği başka
   bir borsa olabilir. Kesişim barları sınırda ayrışır.
3. **Funding.** Repo açık pozisyona funding işletir, kaynak sistem işletmez. Ayrıca
   `data.funding_history_periods` (180 ≈ 60 gün) 4 yıllık pencereyi kapsamaz: kaydı
   olmayan anda `core/funding.py::rate_at` None döner ve maliyet İŞLENMEZ (uydurma yok).
   Bu, dönem A'yı İYİMSER yapar. Harness `--funding-periods` ile derinliği artırır; OKX'in
   kendi sınırı ilk koşuda ölçülür ve raporlanır.
4. **Dolum belirsizliği** (§5e): aynı mumda hem stop hem hedef varsa repo STOP varsayar.
   Bu geometride (stop 1.5×ATR, hedef 3×ATR) payın küçük olması beklenir; AYNI-BAR
   BELİRSİZLİĞİ tablosu bunu sayar.
5. **Portföy kotası.** 13 coinlik koşuda `max_positions: 5` sinyal reddeder; tek-sembollü
   koşularda bağlamaz. İki koşu AYRI raporlanır ve karıştırılmaz.
6. **Stop'un çapası.** `Signal.stop_price` mutlak bir fiyattır ve sinyal barının
   kapanışından kurulur; dolum bir sonraki barın açılışındadır (kural 13). Kaynak sistem
   stop'u gerçekleşen giriş fiyatından kuruyorsa aradaki fark bir bar boşluğu kadardır.

### Çoklu karşılaştırma

Bu, sicilin (**§6c**) **2.** satırıdır ve §6c'deki "araştırmadan çıkan 10 öneri"
paydasına AİT DEĞİLDİR: hipotez dışarıdan geldi, o listeden seçilmedi. BH düzeltmesinde
iki payda ayrı tutulur; aksi hâlde dışarıdan gelen her hipotez, ev içi arama uzayının
cezasını ödemiş gibi görünürdü.

---

## 7. Sonucu gördükten sonra YAPILMAYACAKLAR

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

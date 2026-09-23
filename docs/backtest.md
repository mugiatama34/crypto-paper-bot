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

**Scalp katmanında C-2 de 2026-09-22'ye kadar DEĞERLENDİRİLEMİYORDU — ve bu yazılı
değildi.** Katmanın `models` listesinde kontrol modeli yoktu (`random_ctrl` base
katmanındadır), yani `acceptance_flags` marj ve bootstrap koşullarını düşürüyordu: scalp'te
`edge` fiilen `avg_r > 0`'a iniyordu. Statü C-3'ün eksikliğiyle BİREBİR AYNIYDI, tek farkı
belgeye geçmemiş olmasıydı — ve bir çıtanın sessizce düşmesi, geçilmiş bir çıta gibi
görünmesinin tam da yoludur. Boşluk §6i ile kapatıldı (`scalp_coinflip`, model 23:
`scalp_patient`in ikizi, yönü adil bir yazı-turayla seçilmiş). **Kontrol kendi örneklem
kapısını (`acceptance.control_min_trades` = 30) geçene kadar C-2 hâlâ değerlendirilemez ve
`passed` FALSE kalır** — bu bir gerileme değil, ölçülmemiş bir kapının ölçülmemiş
görünmesidir.

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
| **`wave_scalp`** | Kuralları dış bir sistemin kodundaki sabitlerdir ve bizim verimizle seçilmedi -> koşudan ÖNCE tüm pencere OOS. **Kaynağın canlı defteri (09.09–21.09.2026) GÖRÜLDÜ** -> Eylül 2026 hiçbir döneme girmez; dönem A (2025-03 → 2025-12) teşhisleri okunacağı için **Aşama 2 açısından IN-SAMPLE**dır, dönem B (2026-01 → 2026-08) hold-out'tur (§6h) |

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
| 2 | `ema_trend`: EMA(21/55) kesişimi long-only bir edge taşır (dış sistemden) | §6d, commit `e912efd` (TADİLAT-1: `93cd891`) | A: 2022-01-01 → 2024-12-30 (sinyal kesimi 06-30), B: 2024-07-21 → 2026-09-18 | P1: BTC tek-sembollü PF A 1.551±0.2 / B 1.299±0.2 | **P1 TUTTU** (1.549 / 1.206) ama **hipotez DÜŞTÜ**: ortalama R A −0.0015 / B −0.0165 (C-1), çıpa da geçilemedi (C-3) → **BLOKE**, §6d > SONUÇ |
| 3 | `ema_trend` çıkış varyantları: kenar giriş sinyalinde, çıkış geometrisi yiyor | §6e (güç ve kabul kuralları), commit `9ce4f34`; varyant tanımları HİÇ yazılmadı | A: 2022-01-01 → 2024-12-30 (teşhis; B'ye dokunulmadı) | ön-kayıtlı seçim kuralının bir dalının tetiklemesi | **DÜŞTÜ — teşhis aşamasında** (M2 0.153 < 0.25, M1 0.552 < 1.0, M4 0.667 < 2.0): tur kapandı, varyant kurulmadı — §6e > SONUÇ, karar 49 |
| 4 | `xsec_mom`: kesitsel momentum (21g geriye bakış, top-3, haftalık rebalance) long-only bir edge taşır | §6g, bu commit | A: 2022-01-01 → 2024-06-30, B: A+embargo → koşu günü | P2: A'da `xsec_mom` ort. R > `xsec_random` ort. R | **KOŞULMADI** — ön-kayıt açık, sonuç buraya yazılacak |
| 5 | `wave_scalp`: Elliott Wave Dalga-3 (15m, zigzag + retrace 0.236–0.886) bir edge taşır (dış sistemden) | §6h, bu commit | A: 2025-03-01 → 2025-12-31, B: A+embargo → 2026-08-31 (**Aşama 2'de**) | P1: dönem A net ort. R ≤ 0 | **KOŞULMADI** — ön-kayıt açık, sonuç buraya yazılacak |
| 6 | `dc_short`: ölüm kesişimi (EMA50 < EMA200) rejiminde EMA50'ye geri çekilmenin reddi, short bir edge taşır (dış kaynaktan: eğitim görseli) | §6j (ön-kayıt commit'lerinde §6i olarak yazıldı; birleştirmede yeniden numaralandı), commit `03e9e2e` (TADİLAT-1 `5ad7653`, TADİLAT-2 `cd8fb54`); uygulama `54dd3aa` | A: 2022-01-01 → 2024-06-30 (sinyal kesimi), B: A+embargo → koşu günü | P1: dönem A ort. R > 0 | **KOŞULMADI** — ön-kayıt açık, sonuç buraya yazılacak |
| 7 | TimesFM 2.5 (zero-shot, `klonnist/hemstir`) 48 saatlik yön tahmini üç basit kuraldan (hep yukarı, momentum, yazı-tura) daha isabetli — **model DEĞİL, salt okunur araştırma** | §6k, bu commit | A: 2022-01-01 → 2024-06-30, B: 2024-07-02 → koşu günü, **C: checkpoint yayını (2025-09-15) → koşu günü, bağlayıcı** | tahmin yazılmadı; kapı: üç kurala karşı ayrı ayrı Δ > 0 ve küme CI alt sınırı > 0, A ∧ B ∧ C | **KOŞULMADI** — ön-kayıt açık, sonuç buraya yazılacak |

**3. satır BH paydasına GİRMEZ ve bu bir muafiyet değil bir tanımdır:** hipotez bir
model koşusuna hiç dönüşmedi, yani ortada düzeltilecek bir `p` değeri yok. Satırın
sicilde durmasının sebebi paydanın kendisi değil, **kaç denemenin yapıldığının
görünmesidir** — düşen bir denemeyi silmek, sicilin engellemek için var olduğu yayın
yanlılığının ta kendisidir.

**Sicildeki 2. satır bu paydaya AİT DEĞİLDİR:** `ema_trend` hipotezi dış bir sistemden geldi, ev içi arama uzayından seçilmedi (bkz. §6d > Çoklu karşılaştırma). İki payda ayrı tutulur.

**Sicildeki 5. satır (`wave_scalp`) da bu paydaya AİT DEĞİLDİR** ve gerekçesi 2. satırın
aynısıdır: hipotez dış bir sistemden (`klonnist/Hasanwavebot`, `15m` profili) geldi, ev içi
arama uzayından seçilmedi. Böylece **dış kökenli payda bu satırla 2 oldu** (`ema_trend`,
`wave_scalp`; güncel değer aşağıda) ve ev içi paydadan ayrı tutulur. ⚠ `wave_scalp`in Aşama 2'de türetilecek
VARYANTI dış kökenli DEĞİLDİR — dönem A teşhislerinden çıkacaktır, yani ev içi paydaya
girer ve kendi satırını açar (§6h > 13).

**Sicildeki 6. satır (`dc_short`) da dış kökenli paydaya girer** — kaynak bir eğitim
görselidir, ev içi arama uzayından seçilmedi. **Dış kökenli payda bugün 3'tür**
(`ema_trend`, `wave_scalp`, `dc_short`).

**Sicildeki 7. satır (TimesFM yön isabeti) da dış kökenli paydaya girer** — kaynak
`klonnist/hemstir`dir. **Dış kökenli payda bugün 4'tür** (`ema_trend`, `wave_scalp`,
`dc_short`, `timesfm_direction`); ev içi BH paydası değişmez (§6k > 11).

**Sicildeki 4. satır (`xsec_mom`) BH paydasına GİRER.** Gerekçe 2. satırın tersidir: bu
hipotez dış bir sistemden gelmedi, ev içi bir tezdir — yani "kaç deneme yapıldı"
sayacının saydığı türdendir. Satır koşudan ÖNCE açıldı ve sonucu ne olursa olsun burada
kalacaktır.

**Araştırmadan çıkan öneri sayısı: 10.** Bunların 1'i test edildi (yukarıdaki), 4'ü
ölçüm katmanı olduğu için hipotez DEĞİLDİR ve sicile girmez (kabul kapısı, belge
senkronu, dolum belirsizliği sayımı, sicilin kendisi — hiçbiri bir modelin performansı
hakkında bir iddia taşımaz), 2'si reddedildi (işlem sıklığı tavanı, sembol eleme), 3'ü
kuyrukta (maliyet modeli, rejim filtresi, portföy tavanı).

**§6i'nin iki işi de sicile GİRMEZ** (`scalp_coinflip` kontrolü ve `ScalpModel.
take_survey`). Gerekçe yukarıdaki "4'ü ölçüm katmanı" cümlesinin aynısıdır ve §6h >
EK-1'in gerekçesiyle birebir örtüşür: ikisi de bir modelin performansı hakkında yeni bir
iddia taşımıyor — biri var olan bir kapıyı (C-2) scalp katmanında ölçülebilir kılıyor,
öteki hâlihazırda üretilen bir sayımı tur raporuna düşürüyor. Ortada düzeltilecek bir `p`
değeri yoktur, yani **BH paydası değişmez.** ⚠ Kontrolün EKLENMESİ bir hipotez değildir,
ama `scalp_patient`in onunla ölçülecek C-2'si sicildeki bir satırın parçası olduğunda o
satırın kendi tahmini olarak yazılır — kontrol zemin, hipotez değildir.

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

### TADİLAT-1 — ATR yumuşatması Wilder'a çevrildi (SPEC uyumu, sonuca bakılarak DEĞİL)

**Bu tadilat tam koşudan ÖNCE yapıldı ve gerekçesi burada, karar anında yazıldı.** §7 bir
ön-kaydın sonucu görüldükten sonra değiştirilmesini yasaklar; aşağıdaki kayıt o yasağın
denetlenebilir kalması içindir — okuyucu değişikliğin ne zaman, neye bakılarak yapıldığını
buradan görür.

**Sorun bir parametre farkı değil, SPEC farkıydı.** Kaynak sistem (TradingView / Pine
Script) `ta.atr` kullanır ve o Wilder yumuşatmasıdır (RMA); bu repo `simple` (TR'lerin düz
ortalaması) kullanıyordu. Stop mesafesi ve hedefin TAMAMI bu değerden türüyor, yani iki
sistem aynı kuralı koşmuyordu: "koşulan şey" ile "doğrulanan şey" ayrışmıştı.

**Değişiklik:** `ema_trend` artık `config.yaml > ema_trend.atr_smoothing: "wilder"` okur.
`core/indicators.py::average_true_range` iki yumuşatmayı da verir; **varsayılan `simple`
olarak KALDI** ve başka hiçbir modelin sayısı değişmedi.

**Küresel değişiklik REDDEDİLDİ.** Tanımı topyekûn Wilder'a çevirmek, bugün canlı koşan her
modelin (`trend`, `meanrev`, beş kollu scalp modelleri, `vwap_managed`) stop ölçeğini o
commit'ten itibaren kaydırırdı; biriken defterin bir kısmı bir ölçekle, kalanı başkasıyla
üretilmiş olur ve iki dönem kıyaslanamazdı — `fee_rate`in defteri tarihli olarak böldüğü
hatanın aynısı (docs/decisions.md > 25).

**Bedeli açıkça yazılıdır:** "1.5×ATR" ifadesi artık modeller arasında birebir
kıyaslanabilir DEĞİLDİR. Kural 14'ün kıyas ölçütü zaten ATR katı değil GERÇEKLEŞEN stop
mesafesidir (`avg_stop_distance_pct` ve ⚠B bandı), yani kıyas o kolondan okunmaya devam
eder. Motorun tavan kontrolü (`core/engine.py`) ORTAK tanımda kalır: her model kendi
yumuşatmasını seçerek kendi tavanını genişletebilseydi tavan bir kural olmaktan çıkardı.

**Bu tadilat bir kapıyı KURTARMIYOR.** Değişiklikten ÖNCE ölçülen BTC parite koşusu
(`backtest.yml` run 35373017427, dönem A, tek sembollü, ön-kayıtlı maliyetle) şunu verdi:

| | TradingView referansı | ölçülen (`simple` ATR) |
|---|---|---|
| kâr faktörü | 1.551 | **1.40** (sapma −0.151, tolerans ±0.2 → P1 GEÇTİ) |
| kazanma oranı | %46.67 | %44.2 |
| ödeme oranı | 1.79 | **1.76** |
| n | — | 43 |

Yani P1 kapısı `simple` ATR ile de GEÇİLMİŞTİ; tadilat düşen bir kapıyı geçirmek için
değil, spec uyumu için yapıldı.

**Tadilat SONRASI aynı pencere** (`backtest.yml` run 35382583335, `wilder` ATR):

| | TradingView referansı | `simple` (tadilat öncesi) | `wilder` (tadilat sonrası) |
|---|---|---|---|
| kâr faktörü | 1.551 | 1.40 (−0.151) | **1.55 (−0.001)** |
| kazanma oranı | %46.67 | %44.2 | **%47.0** |
| ödeme oranı | 1.79 | 1.76 | **1.77** |
| ortalama R | — | +0.24 | +0.32 |
| n | — | 43 | 45 |

**İki sayı da burada duruyor ve duracak** — ilk koşuyu silmek, sonucu görüp geçmişi
yazmak olurdu. Sonuç, tadilatın gerekçesini de doğruluyor: kalan sapmanın neredeyse
tamamı ATR yumuşatmasındanmış (kâr faktörü farkı −0.151 → −0.001). Veri kaynağı farkı
(OKX perp ↔ kaynak sistemin borsası) beklenenden küçük çıktı.

**Tutuş süresi (dönem A, BTC):** medyan 9 bar, azami **46 bar** (7.7 gün). Portföy
koşusundan ölçülecek embargo bu mertebede beklenir.

Aynı koşudan gelen, tadilat ÖNCESİ portföy referansı (13 sembol, dönem A, `backtest.yml`
run 35373248059): `ema_trend` n=359, ortalama R ≈ **−0.06**. Tek sembollü BTC koşusu
(+0.24R) ile portföy koşusunun ayrışması ölçümün kendisidir, tadilatın konusu değil.

### Veri kapsamı — KOŞUDAN ÖNCE ölçüldü

Bu bölüm bir sonuç değil, bir VERİ OLGUSUDUR ve ana koşudan önce ölçülmüştür (prob koşusu:
`backtest.yml`, run 35372230431, 2026-09-18; pencere 2022-01-01 → 2022-02-01). Sonuca göre
yazılmadığı için §7'nin kapsamına girmez; buraya yazılmasının sebebi tersidir — sonucu
görünce "zaten biliyorduk" denmesin.

- **OKX 4H verisi 2022-01'e uzanıyor**, yani dönem A'nın başlangıcı veri tarafından
  destekleniyor.
- **Dönem A'nın başında evren 13 değil 9 semboldür.** 2022-01'de OKX'te kapanmış barı
  olmayanlar: **BNB, SUI, PENGU, ETHFI**. (SUI/PENGU/ETHFI beklenen listelenme
  tarihleriyle uyumlu; BNB'nin de o tarihte OKX perpetual'i yok.) Semboller verileri
  başladığı anda evrene girer — `core/data.py` `as_of` barını taşımayan sembolü zaten o
  tur dışlar (kural 12), yani bu bir sessiz kayıp değil, loglanan bir kapsam sınırıdır.
- **Sonuç okunurken:** dönem A'nın erken kısmı daha DAR bir evrende ölçülür; coin başına
  tabloda o semboller `—` ile durur. Bu, K-1 kapısının (dönem B) birimini etkilemez —
  dört sembolün de dönem B'de verisi vardır.
- **Funding kayıtları 2022'de seyrek**: `core/funding.py::rate_at` kaydı olmayan anda None
  döner ve maliyet işlenmez (uydurma yok). Bu, dönem A'yı İYİMSER yapar ve §6d'nin
  "kabul edilen sapmalar" listesindeki 3. maddenin ölçülmüş hâlidir.

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

### SONUÇ — koşuldu, kapılar okundu: **BLOKE**

Koşu: `backtest-ema` #35391881083, commit `4c5bfac`, 2026-09-18. Ham çıktı
`docs/data/backtest_ema_trend.json` (site yükü) ve `docs/data/backtest_ema_trend.csv`
(coin tablosu). **Bu bölüm sonuçları KAYDEDER, kuralları değiştirmez** — yukarıdaki
hiçbir eşik, tahmin ya da istisna koşudan sonra dokunulmadı.

**Pencereler ve embargo (ölçüldü, varsayılmadı).** Dönem A `2022-01-01T00:00` →
`2024-12-30T20:00` (sinyal kesimi `2024-06-30`; A'da açılan pozisyonlar kesimden sonra
da yönetildi, kural: dönem ataması GİRİŞ tarihine göre). A'da `ema_trend`in azami tutuş
süresi **129 bar (21.5 gün)** ölçüldü, embargo bu sayıdır ve dönem B ondan sonra başlar:
`2024-07-21T12:00` → `2026-09-18T08:00`.

| Tutuş süresi (`ema_trend`) | medyan | p90 | azami |
|---|---|---|---|
| Dönem A | 7 bar (1.17 gün) | 21 bar (3.5 gün) | **129 bar (21.5 gün)** |
| Dönem B | 6 bar (1.0 gün) | 19.2 bar (3.2 gün) | 65 bar (10.8 gün) |

#### P1 — TradingView paritesi (KAPI): **GEÇTİ**

| Dönem | Referans (TV) | Ölçülen | Sapma | Tolerans ±0.2 |
|---|---|---|---|---|
| A | 1.551 | **1.549** | −0.002 | ✅ |
| B | 1.299 | **1.206** | −0.093 | ✅ |

Harness kaynak sistemi yeniden üretiyor; sayılar yorumlanabilir. Dönem A'daki sapmanın
binde iki olması, TADİLAT-1'in (Wilder ATR) doğru tadilat olduğunun kanıtıdır — koşudan
ÖNCE yapıldı ve gerekçesi o zaman yazıldı.

#### Model sahibinin kapıları: **ÜÇÜ DE GEÇTİ**

| # | Koşul | Ölçülen | Sonuç |
|---|---|---|---|
| K-1 | ≥ 6 coinde dönem B PF > 1.1 | **tam 6**: SOL 2.204, BNB 1.369, DOGE 1.222, BTC 1.206, XRP 1.149, LINK 1.101 | ✅ |
| K-2 | toplam işlem > 300 | **978** | ✅ |
| K-3 | hiçbir coinde DD > %25 | azami **%21.3** (XRP, dönem A) | ✅ |

⚠ **K-1 kıl payı geçti ve bu bir okuma notudur, kural değil:** eşik 6, ölçülen 6 ve
sınırdaki satır (LINK 1.101) eşiğin 0.001 üstünde. Tek bir işlemin yeri değişse kapı
düşerdi. Eşik SONUÇTAN SONRA değiştirilmedi — ne aşağı (kurtarmak için) ne yukarı.

#### Repo kabul kapıları (BİRİNCİL): **İKİ DÖNEMDE DE DÜŞTÜ**

| | Ö (örneklem) | ⚠ B (band) | E (edge) | `passed` |
|---|---|---|---|---|
| Dönem A | ✅ n=369 | ✅ 3.62% ∈ [3.42, 8.56] | ❌ | **false** |
| Dönem B | ✅ n=389 | ✅ 3.56% ∈ [3.36, 8.39] | ❌ | **false** |

Edge kapısının hangi koşulda düştüğü:

| Koşul | Dönem A | Dönem B |
|---|---|---|
| C-1 ortalama R > 0 | **−0.0015** ❌ | **−0.0165** ❌ |
| C-2 kontrolü ≥ 0.15R marjla geç | +1.031 ✅ (kontrol −1.033) | +1.046 ✅ (kontrol −1.063) |
| C-2 farkın bootstrap CI alt sınırı > 0 | +0.887 ✅ | +0.897 ✅ |
| C-3 hesap getirisi çıpayı geç | **−%8.6 ↔ +%43.4** ❌ | **−%20.4 ↔ −%7.3** ❌ |

#### Verdikt: **BLOKE — paper trading'e GEÇİLMEZ**

Ön-kayıttaki tek istisna (**yalnızca** C-3'ten kalma → koşu durur, karar model sahibine
gider) **UYGULANMAZ**: model C-3'ün yanında **C-1'den de** kalıyor (ortalama R iki
dönemde de negatif). İstisnanın kapsamı sonuçtan önce tek bir kapıyla sınırlandı ve
burada genişletilmiyor — "diğer her şeyi geçiyorsa" şartı sağlanmıyor.

`.github/workflows/run-ema.yml` AÇILMAZ; `ema` katmanı tetikleyicisiz kalır.

#### Ön-kayıtlı tahminlerin sonucu (hepsi, düşen dâhil)

| # | Tahmin | Sonuç |
|---|---|---|
| P1 | BTC PF A 1.551±0.2 / B 1.299±0.2 | **TUTTU** (1.549 / 1.206) |
| P2 | `tp` ile kapanan pozisyon payı < %35 | **A'da DÜŞTÜ (%35.23), B'de tuttu (%34.70)** — eşiğin iki tarafında, kıl payı |
| P3 | ortalama R > 0 (C-1) | **DÜŞTÜ** (A −0.0015, B −0.0165) |
| P4 | hesap getirisi çıpanın ALTINDA (C-3 düşer) | **TUTTU** (model sahibinin beklentisi doğrulandı) |

**P2 nasıl ölçüldü:** model tek dilimli hedef kullanır (`fraction=1.0`) ve ne trailing
ne zaman stop'u taşır, yani bir pozisyon ya hedefte ya stop'ta ya da likidasyonda kapanır.
Pay bu yüzden `(pozisyon − stop çıkışı − likidasyon) / pozisyon`tur: A 369'da 239 stop ve
0 likidasyon → **%35.23**; B 389'da 254 stop ve 0 likidasyon → **%34.70**. Tahmin A'da
yüzde binde 23 ile düştü. **Eşik yuvarlanarak kurtarılmıyor** (§7.4): "yaklaşık %35" demek,
sonucu gördükten sonra tahmini gevşetmek olurdu. P2 zaten bir KAPI değil bir tahmindi ve
verdikti değiştirmez — düşüşü buraya, geçtiği yer kadar açık yazılıyor.

**P3 ile P4 birlikte okunur:** model çıpayı yenemedi VE bilgisiz çekilişin üstünde bir
beklenen değer üretmedi. İkincisi C-2'yi geçmesiyle çelişmez — C-2 kontrolün (`random_ctrl`)
ortalama R'sine göre ölçülür ve kontrol −1.03R ile neredeyse her işlemi stop'ta
kapatıyor; onu geçmek sıfırın üstüne çıkmak demek değildir. Modelin kendi eşiği C-1'dir
ve sıfırın altındadır.

#### Ne öğrenildi (bir kural değil, bir kayıt)

Kesişim sinyali maliyet ÖNCESİ bir kenar taşıyor gibi görünüyor: ödeme oranı (payoff)
dönem B'de 1.61 ve kazanma oranı %34.7 — bu çift, 2R hedefli bir sistemde başabaşın tam
sınırıdır. Friksiyon (`cost_per_r` 0.057) işareti negatife çeviren şeydir. Bu bir
GÖZLEMDİR; parametre değiştirmenin, maliyeti yeniden varsaymanın ya da yeni bir eksen
açmanın gerekçesi DEĞİLDİR (§7.1/§7.2/§7.4). Yeni bir tez ancak yeni bir ön-kayıt
satırıyla, yeni bir model olarak gelir.

---

## 6e. ÖN-KAYIT — çıkış varyantı turunun İSTATİSTİKSEL GÜCÜ

**Bu bölüm varyant tanımlarından ÖNCE ve Adım 0b'nin (yol teşhisi) sonucu ortaya
çıkmadan yazıldı; tarih damgası git'tedir.** Sebebi usuldür: aşağıdaki eşikler sonuç
görüldükten sonra yazılsaydı, "sonuç kötü çıktı, eşik yeniden yorumlanıyor" ile ayırt
edilemezdi (§7.4). Varyantların kendisi (tanımlar, parametreler, P tahminleri, sicil
satırı) AYRI bir ön-kayıtla ve yine koşudan önce gelecek; bu bölüm yalnızca o turun
**okunabilirlik koşullarını** sabitler.

### Ölçümün çözünürlüğü — `ema_trend`in R dağılımı İKİ NOKTALIDIR

Model ne trailing ne zaman stop'u taşır ve hedefi tek dilimdir; yani her pozisyon ya
hedefte ya ilk stop'ta kapanır (likidasyon iki dönemde de 0). Dağılım bu yüzden iki
noktalıdır: `%35 → +1.93R`, `%65 → −1.05R`.

| | hesap | değer |
|---|---|---|
| standart sapma | `√(p(1−p)·(1.9296+1.0519)²)` | **1.424** |
| standart hata, A (n=369) | `1.424/√369` | **0.0742** |
| standart hata, B (n=389) | `1.424/√389` | **0.0722** |

Bağımsız sağlaması defterdedir: `core/metrics.py::bootstrap_mean_ci`in ürettiği aralık
(A: −0.147 … +0.143) analitik `±1.96·SE = ±0.145` ile çakışıyor.

**§6d'nin sonucu bu ışıkta şöyle okunur:** A `−0.0015 ± 0.074`, B `−0.0165 ± 0.072`.
İkisi de sıfırdan ayırt edilemez. **Model negatif KANITLANMADI; sıfır kanıtlandı.**
Bu, §6d > SONUÇ'un **BLOKE** verdiktini DEĞİŞTİRMEZ ve o bölümün hiçbir satırına
dokunulmaz: model C-3'ten de kalıyordu, ayrıca kanıtlanmamış bir kenara sermaye
ayrılmaz. Değiştirdiği şey verdikt değil, **bundan sonraki turun ne vaat edebileceğidir.**

### Asgari tespit edilebilir etki (MDE) — turdan ÖNCE yazılır

`n ≈ 400`, `sd ≈ 1.42`, `α = 0.05`, güç `0.80` için:

| test | gereken GERÇEK etki |
|---|---|
| iki yanlı | **+0.202R** |
| tek yanlı | +0.180R |

**Bundan küçük bir iyileşme bu kurulumda görülemez.** Ölçek şudur: brüt kenar A'da
+0.057R, B'de +0.041R; friksiyon 0.057R. Yani gerçekçi bir çıkış iyileştirmesi
(0.02–0.06R mertebesi) MDE'nin **üçte biri ile beşte biri** arasındadır. Bunun iki
dürüst sonucu var ve ikisi de şimdi yazılıyor:

1. **"Ortalama R > 0 çıktı" tek başına bir bulgu değildir.** Gerçekten değersiz bir
   varyantın dönem B'de pozitif çıkma ihtimali ≈ %50 — C-1 bu örneklemde neredeyse
   yazı-turadır.
2. **Turun olumlu çıktısı bir kabul değil, bir SIRALAMA ve bir ELEME olabilir.** Bu tur
   "şu varyant kazandırıyor"u kanıtlayamaz; kanıtlayabileceği şey "şu varyant,
   ölçülebilir bir farkla KÖTÜLEŞTİRMİYOR / kötüleştiriyor"dur.

**Her varyantın MDE'si KENDİ dağılımından yeniden hesaplanır ve sonucunun yanına
yazılır.** İki noktalı dağılım yalnızca `ema_trend`in geometrisine aittir: trailing ya
da hedefsiz bir varyantın sağ kuyruğu şişer, `sd` büyür ve MDE **kötüleşir**. Tek bir
MDE sayısını tüm varyantlara uygulamak, kuyruğu geniş bir varyantı olduğundan
ölçülebilir göstermek olurdu.

### Eksen istatistiği EŞLEŞTİRİLMİŞTİR: pozisyon başına ΔR

Varyantlar `ema_trend`in girişini ve stop'unu MİRAS ALIR (varyant tanımları AYRI bir
ön-kayıtla ve yine koşudan önce gelecek):
aynı sembolde, aynı barda, aynı stop mesafesiyle girilir ve ayrışan tek şey çıkıştır.
İki model bu yüzden **aynı fiyat yolunu** paylaşır ve farkın varyansı, iki bağımsız
ölçümün varyansından çok daha küçüktür.

Eksen istatistiği bu yüzden marjinal ortalama R değil, **eşleşen pozisyonlarda
`ΔR = R_varyant − R_taban`in ortalamasıdır.** Precedent katmanın içindedir: model 15 ↔
`scalp_fixed` çifti aynı gerekçeyle çekiliş kimliğini paylaşır (CLAUDE.md > "Çekiliş,
ölçülmeyen eksende PAYLAŞILIR").

`ρ` (iki modelin R'leri arasındaki korelasyon) **bilinmiyor ve ÖLÇÜLECEK**; ön-kayıtlı
beklenti ve ondan çıkan güç şudur:

| varsayılan ρ | `sd(ΔR) = 1.42·√(2(1−ρ))` | MDE (n=369, iki yanlı, güç 0.80) |
|---|---|---|
| 0.7 | 1.10 | +0.160R |
| **0.8 (ön-kayıtlı beklenti)** | **0.90** | **+0.131R** |
| 0.9 | 0.64 | +0.093R |

Yani eşleştirme MDE'yi +0.20R'den **~+0.13R**'ye indirir. Hâlâ beklenen etkinin
(0.02–0.06R) üstündedir — eşleştirme güç sorununu çözmez, **küçültür**; bu da sonucun
"ayırt edilemedi" ile bitme ihtimalini düşürür.

Üç şart, sonucu görmeden:

1. **`ρ` ve gerçekleşen `sd(ΔR)` sonucun yanına YAZILIR.** Yukarıdaki tablo bir beklenti;
   MDE gerçekleşen `sd`den yeniden hesaplanır. Beklentiyi tutmuş gibi raporlamak, gücü
   olduğundan iyi göstermek olurdu.
2. **`n` eşleşen pozisyonların sayısıdır, iki defterin toplamı değil.** Dolumlar
   ayrışır (çıkış kuralı kotayı ve nakdi farklı zamanlarda serbest bırakır — CLAUDE.md
   aynı ayrışmayı model 15 için yazar), yani eşleşme kesişimdir ve kesişimin dışında
   kalan pozisyon sayısı da raporlanır.
3. **C-1 BAĞLAYICI KALIR ve eşleştirme onun YERİNE GEÇMEZ.** Varyant tabandan
   ölçülebilir biçimde iyi olup hâlâ negatif olabilir; o durumda bulunan şey "daha az
   kötü"dür ve canlıya alınacak bir şey değildir. İkisi birlikte okunur: ΔR ekseni
   *"çıkış kuralı bir fark yaratıyor mu"*, C-1 *"bu model para kazanıyor mu"* sorusunu
   cevaplar.

`ΔR`nin aralığı **eşleştirilmiş bootstrap** ile kurulur (pozisyon çiftleri birlikte
yeniden örneklenir; bağımsız yeniden örnekleme eşleştirmenin kazancını geri verirdi) ve
hesap `core/metrics.py`ye girer — ikinci bir R tanımı, aynı defterin iki cevabı demekti
(kural 7).

### Kabul kuralı — C-1'in yanına bir KESİNLİK koşulu

C-1 (`ortalama R > 0`) bu örneklemde tek başına yetersizdir (yukarıdaki 1. madde). Bu
yüzden varyant turunda C-1, modelin KENDİ ortalama R'sinin güven aralığıyla birlikte
okunur: **aralığın ALT SINIRI > 0.** Böylece "pozitif çıktı" ile "pozitif olduğu
gösterildi" ayrışır — kabul çıtasının edge kapısında (E) kontrol farkı için zaten
uygulanan ayrımın aynısı (CLAUDE.md > Kabul Çıtası).

Aralık `core/metrics.py::bootstrap_mean_ci` ile kurulur ve tabloda zaten vardır
(`avg_r_ci_low` / `avg_r_ci_high`); alfa `acceptance.edge_ci_alpha`tan (0.05) gelir.
**İKİNCİ bir alfa anahtarı açılmaz** (CLAUDE.md > Rapor Kolonları): aynı tabloda iki
farklı kesinlik ölçüsü durması, hangi satırın hangi ölçüyle okunacağını belirsiz
bırakırdı — ve "hangi tabloya bakıyorduk" sorusunun cevabı bir gün belirsizleşirse
kesinlik ölçüsünün kendisi işe yaramaz hâle gelir. Hazır kolon kullanılmasının ikinci
kazancı yeni kod yazılmamasıdır: yeni kod yeni hata demektir.

⚠ **Bu seçim SONUÇ GÖRÜLMEDEN ve DAHA SIKI olduğu BİLİNEREK yapıldı.** `edge_ci_alpha`
iki yanlı %95'tir, yani alt sınırı tek yanlı α=0.025'e denk gelir; tartışılan alternatif
(iki yanlı %90, yani tek yanlı α=0.05) daha gevşek olurdu. Kayıt buraya, seçim anına
yazılıyor ki ileride bir varyant kıl payı kalırsa "keşke %90 deseydik" tartışması
açılmasın — o tartışma, eşiği sonuca göre yeniden yorumlamanın kendisidir (§7.4).

### Çoklu karşılaştırma — tek BİRİNCİL varyant

Birden fazla varyant sınanacaksa, birinin şansla geçme ihtimali tek varyanttan
yüksektir. Bu yüzden:

- **Koşudan önce TEK bir varyant BİRİNCİL ilan edilir**; kalanlar raporlanır ama kabul
  çıtasına aday değildir. "Beşinin arasından B'de en iyisini seçmek", B'yi OOS olmaktan
  çıkarırdı.
- Birincil varyantın tahmini §6c sicilinin **3.** satırıdır ve BH düzeltmesine
  (`q = 0.10`) girer. Satır, varyant tanımlarıyla birlikte ve koşudan ÖNCE açılır;
  sonucu ne olursa olsun orada kalır (§7.5).
- Birincil varyantın seçim KURALI da koşudan önce yazılır: Adım 0b'nin hangi çıktısının
  hangi varyanta işaret ettiği, teşhis sonucunu GÖRMEDEN sabitlenir. Aksi hâlde
  "baktım, en iyi görüneni seçtim" olurdu.

### BİRİNCİL VARYANTIN SEÇİM KURALI — teşhis çıktısı GÖRÜLMEDEN sabitlendi

Kuralın **çalıştırılabilir kopyası** `scripts/diagnose_ema_exits.py::select_primary_family`
fonksiyonudur ve eşikler orada sabittir; bu bölüm onu ALINTILAR. Gerekçe: kuralın tek işi
"sonucu görüp seçmedik"i kanıtlamaktır, iki yerde yazılı bir kural ise bir gün sessizce
ayrışır ve o kanıtı yok eder. Seçim bir metin değil bir fonksiyondur, teşhisin İÇİNDE
çalışır ve çıktısı log'a basılır.

Dallar **öncelik sırasıyla** denenir:

| sıra | dal | ölçü (dönem A teşhisi) | eşik | seçilen aile |
|---|---|---|---|---|
| 1 | **M2** — kuyruk | `tp` çıkışlarından 20 bar sonraki **İŞARETLİ** hareketin medyanı | ≥ **+0.25R** | kazananı koşturan (trailing / hedefin kaldırılması) |
| 2 | **M1** — geri dönüş | `stop` çıkışlarının medyan MFE'si (kapanış barı hariç) | ≥ **1.0R** | breakeven + kısmi çıkış |
| 3 | **M4** — oyalanma | medyan `stop` tutuşu / medyan `tp` tutuşu | ≥ **2.0×** | zaman stop'u |
| 4 | — | hiçbiri tetiklenmedi | — | **tur KAPANIR, varyant kurulmaz** |

**Eşiklerin gerekçesi (hiçbiri veriden türetilmedi):**

- **M1 = 1.0R** keyfi DEĞİLDİR: `config.yaml > exit_management.breakeven_at_r` değerinin
  ta kendisidir ve bir breakeven kuralının tetiklenebilmesi için gereken hareket odur.
- **M2 = +0.25R** ölçülen friksiyonun (0.057R) kabaca dört katıdır. **YUVARLAK bir
  sayıdır ve öyle seçildiği burada yazılıdır** — sonradan eşik tartışması açılmasın.
- **M2'nin ufku = 20 bar**, dönem A'nın p90 tutuş süresinin (21 bar) yuvarlanmış hâlidir.
  O sayı §6d'de koşudan önce yayımlandı; teşhis çıktısından gelmiyor.
- **M4 = 2.0×** yuvarlak bir katsayıdır ve açıkça yuvarlak seçilmiştir.

**M2 neden İŞARETLİ hareketi ölçer, azami yükselişi değil.** "Çıkıştan sonra ne kadar
yükseldi" (azami) tanım gereği ≥ 0'dır ve sürüklenmesiz bir yürüyüşte bile ufukla
birlikte `√H` hızında büyür — 20 barda ~2.4R. Onu eşiğe bağlamak, M2 dalını HER koşulda
tetiklemek olurdu. İşaretli kapanış farkının medyanı ise martingal altında sıfırdır,
yani sıfırdan sapması gerçek bir sürüklenmedir. Teşhis ikisini de basar; kural yalnızca
ikincisine bakar.

**Sıranın gerekçesi.** M2 ile M1 aynı pozisyonlar üzerinde TERS yönde çalışır (biri
kuyruğu uzatır, öteki keser); ikisi birden tetiklendiğinde hangisinin seçileceği
önceden yazılmazsa, "hangisi daha mantıklı" tartışması kuralın kapatmak için var olduğu
serbestliği geri açardı. M4 en sona konur çünkü ölçtüğü şey R değil SERMAYE HIZIDIR ve
C-1 bir R kapısıdır.

**4. dal bir boşluk değil, bir SONUÇTUR.** Hiçbir dal tetiklenmezse yolda çıkışın
sömürebileceği bir yapı yok demektir: varyant turu kurulmaz, sicile bu yazılır ve açık
kalan tek kaldıraç friksiyondur — o da çıkış ekseninde değil, aşağıdaki KAYIT'ta duran
stop mesafesi ekseninde.

**Kural BİR KEZ çalışır.** Teşhis geldiğinde uygulanır, birincil aile belirlenir ve
çıkan aile beklenen olmasa bile tartışılmaz. `nan` bir dalı tetiklemez: ölçülemeyen bir
koşul sağlanmış sayılamaz (eksik bir çıta, geçilmiş bir çıta gibi görünmemelidir — §4'ün
aynı ilkesi).

### KAYIT (bir varyant önerisi DEĞİL) — friksiyon stop mesafesine bağlıdır, hedefe değil

`cost_per_r ≈ 2c / stop%` özdeşliği, R başına friksiyonun **stop mesafesiyle** ters
orantılı olduğunu söyler: 1.5×ATR'den 3.0×ATR'ye geçmek onu kabaca YARIYA indirir
(ölçülen 0.057R → ~0.028R). Hedefin yeri bu sayıya girmez.

**Bu, çıkış varyantı turunun konusu DEĞİLDİR ve o turda denenmeyecektir:** stop
mesafesi ayrı bir geometri eksenidir ve çıkış ekseniyle birlikte oynatılırsa aradaki
fark iki değişkenin toplamı olur (CLAUDE.md > Scalp katmanının model kuralları'nın
"13 ↔ 14 bir eksen değil, bir toplam farktır" gerekçesi). Kaynak sistemde SL 2.5
denemesi çökmüştü, ama orada hedef de stop'la birlikte kaymıştı — **stop ile hedefin
AYRIŞTIRILMASI hiç test edilmedi.** Sırası gelirse kendi ön-kaydıyla gelir.

---

### SONUÇ — kural koşuldu, TUR KAPANDI (varyant kurulmadı)

Koşu: `diagnose-ema-exits` #35435506689, commit `d0332da`, 2026-09-19. Ham çıktı
artifact'te (`diagnose-ema-exits`, 14 gün); özet yük koşunun log'una basıldı.
**Bu bölüm sonucu KAYDEDER, kuralları değiştirmez** — yukarıdaki hiçbir eşik, öncelik
sırası ya da dal tanımı koşudan sonra dokunulmadı.

**Determinizm kapısı GEÇTİ.** Teşhis koşusu, karara giren koşunun (`backtest-ema`
#35391881083) dönem A sayılarını birebir üretti: 369 pozisyon, 130 tp, 239 stop,
ortalama R −0.0014889765, azami tutuş 129 bar. `missing_bars = 0`,
`unchecked_position_bars = 0`. Yani yol istatistiği okunabilir.

**Kuralın uygulanması (bir kez çalıştı):**

| dal | aile | ölçülen | eşik | sonuç |
|---|---|---|---|---|
| M2 | kuyruk (trailing / hedefsiz) | **+0.153R** | ≥ +0.25R | tetiklemedi |
| M1 | geri dönüş (breakeven + kısmi) | **0.552R** | ≥ 1.0R | tetiklemedi |
| M4 | oyalanma (zaman stop'u) | **0.667×** | ≥ 2.0× | tetiklemedi |

**SEÇİLEN: 4. dal — tur kapanır, varyant kurulmaz.**

#### Ölçülen yol (kayıt)

| çıkış | n | tutuş: medyan / p75 / p90 / azami (bar) | MFE\* medyan | MAE\* medyan |
|---|---|---|---|---|
| `tp` | 130 | 9 / 15.8 / 22 / 129 | +1.62R | −0.41R |
| `stop` | 239 | 6 / 12 / 20 / 43 | +0.55R | −0.77R |

\* kapanış barı HARİÇ (kural 13b): bir çıkış kuralının karar anında görebileceği hareket.

Stop'la kapananların MFE dağılımı (kapanış barı hariç, n=222 — kapanıştan önce barı
olan pozisyonlar): `%46.8` hiç +0.5R'ye ulaşamadı, `%24.3` 0.5–1.0R, `%16.7` 1.0–1.5R,
`%12.2` 1.5–2.0R. Hedefe varanların MAE'si: `%34.7` −0.25R'den derine hiç inmedi,
`%17.7` −0.75R'nin altına indi.

TP sonrası İŞARETLİ hareketin medyanı ufuklar arasında işaret değiştiriyor:
5 bar −0.10R, 10 bar −0.23R, **20 bar +0.15R**, 40 bar −0.37R. Kural yalnızca 20 barlık
ufka bakar (o ufuk koşudan önce sabitlenmişti) ve orada da eşiğin altında kalıyor;
diğer üç ufkun negatif olması, +0.15R'nin kararlı bir sürüklenme DEĞİL gürültü
olduğunu söylüyor — martingal ile uyumlu. Azami YÜKSELİŞ ölçüsü (20 barda medyan
+1.46R) bu tabloda bir bulgu değildir: sürüklenmesiz bir yürüyüşte de aynı mertebede
çıkar ve kural bu yüzden ona bakmıyor.

#### Ne öğrenildi (bir kural değil, bir kayıt)

- **Zaman stop'unun ön kabulü TERSİNE çıktı.** Kaybedenler kazananlardan DAHA HIZLI
  ölüyor (medyan 6 ↔ 9 bar, p90 20 ↔ 22 bar). Bir zaman stop'u bu dağılımda önce
  kazananları keserdi; "oyalanan kaybedenleri kes" tezinin dayanağı bu veride yok.
- **Kaybedenlerin yarısı hiç kâra geçmiyor:** medyan MFE +0.55R ve `%46.8`'i +0.5R'yi
  bile görmüyor. Breakeven'ın koruyacağı bir kâr çoğu kayıpta hiç oluşmamış.
- **Hedefte kesilen kuyruk ölçülebilir değil:** çıkış sonrası işaretli hareket sıfır
  etrafında salınıyor. "TP'ler erken kesiyor" iddiası bu pencerede desteklenmiyor.
- Üçü birlikte, §6e'nin 4. dalının tanımladığı durumdur: **yolda çıkışın sömürebileceği
  bir yapı yok.** Kenar sinyalin kendisinde ve küçük (brüt +0.057R ↔ friksiyon 0.057R);
  onu çıkış geometrisini oynatarak büyütmenin bu veride bir dayanağı yok.

Açık kalan tek kaldıraç friksiyondur ve o, çıkış ekseninde değil **stop mesafesi**
ekseninde durur (yukarıdaki KAYIT). O eksen kendi ön-kaydıyla gelir; bu tur onu
denemedi ve denemeyecek.

---

## 6f. ÖN-KAYIT — dönem A fonlama arşivi (karar 50'nin açık işi)

**Bu bir TEZİN ön-kaydı değil, bir VERİ YOLUNUN ön-kaydıdır.** Sabitlenen şey bir modelin
performansı hakkında bir tahmin değil, *"dönem A'nın fonlama dağılımı hangi veriyle
sorulacak"* sorusunun cevabıdır. Bu yüzden §6c'nin sicil tablosuna **girmez** ve BH
paydasına eklenmez: sicil bir modelin performansı hakkında iddia taşıyan satırları sayar
ve bu satırın öyle bir iddiası yoktur (§6c'nin "ölçüm katmanı hipotez DEĞİLDİR" ayrımı).
Arşivle beslenen bir TEZ geldiğinde o tez kendi satırını açar.

**Neden şimdi yazılıyor.** Karar 50 arşiv işinin sırasını bağlayıcı kıldı (önce arşiv,
sonra örtüşme kanıtı, ancak sonra kullanım) ve "engelin tam mekanizması … girmesi gereken
yer arşiv işinin ön-kaydıdır" dedi. Burası o yer. Kapılar veri GÖRÜLMEDEN sabitleniyor;
tersi §7.1'in yasakladığı şeyin ta kendisidir. Belge, `scripts/probe_funding_depth.py`
hiç koşmadan commit edilir — probe'un ürettiği hiçbir sayı bu sayfadaki hiçbir eşiği
seçmedi.

### Adımlar ve sıra (bağlayıcı)

| Adım | Soru | Nerede cevaplanır |
|---|---|---|
| **A** | OKX'in KENDİ yolu dönem A'ya ulaşıyor mu? | `scripts/probe_funding_depth.py` log'u |
| **B** | Ulaşmıyorsa hangi arşiv? | aday sırası aşağıda SABİT |
| **C** | Arşiv canlı seriyle tutarlı mı? | 60 günlük örtüşmede üç koşul |

✅ **A GEÇTİ (A-2, 2026-09-20) → B DÜŞTÜ, C literal biçime döndü.** Ayrıntı aşağıda
"SONUÇ — A-2 GEÇTİ" başlığında; bu paragraf o sonuçtan ÖNCE yazılmıştı ve aynen duruyor.

**A geçerse B ve C DÜŞER.** OKX-içi bir yol bir kaynak değişikliği değildir: kural 5'in
"tüm modeller aynı veriyi görür" şartı zaten sağlanır ve karar 50'nin örtüşme kanıtı
konusuz kalır. Bu yüzden probe'un sırası da A-2 (OKX'in tarihsel veri portalı) ile başlar:
portal erişilebilir ve fonlama veri kümesi taşıyorsa aşağıdaki (a)/(b) ayrımı bile
gereksizdir.

### Adım A — iki hipotez, tek ayrım

PR #37'nin izi `BTC-USDT-SWAP` için **4 sayfa / 312 kayıt / en eski 2026-06-11** verdi ve
"kısa sayfa" (12 < 100) ile durdu. Kısa sayfa İKİ ayrı şeyin imzasıdır ve iz ikisini
ayırt etmiyor:

- **(a) borsa tabanı** — uç nokta bu yoldan gerçekten ~3-4 ay veriyor;
- **(b) yürüyüş tabanı** — `fetch_history`nin `after`-yürüyüşü sunucu tarafı bir pencereyi
  tüketiyor ve doğrudan parametrelenmiş bir istek daha geriye ulaşıyor.

312 kayıt, bir ikincil kaynağın bildirdiği 400 kayıtlık tavanın **altında** durdu; bu (b)
lehine bir işarettir ama kanıt değildir — kanıt tek bir istektir.

**Ayrım MEKANİKTİR ve kodda yazılıdır** (`probe_funding_depth.py::classify_depth_floor`).
Gerekçe `diagnose_ema_exits.py::select_primary_family`in aynısıdır: sonucu bir insanın
okuyup "bana (b) gibi göründü" demesi, kuralın kapatmak için var olduğu serbestliği geri
açardı. Kural, probe koşmadan önce şudur:

1. Yürüyüşün ulaştığı en eski damgadan **doğrudan** bir `after` isteği daha eski kayıt
   döndürüyorsa → **(b) yürüyüş tabanı.**
2. Döndürmüyorsa, dönem A kesimine (2024-06-30) **doğrudan** atlayan bir `after` isteği
   kayıt döndürüyorsa → yine **(b).**
3. İkisi de boşsa → **(a) borsa tabanı.**
4. Herhangi bir istek hata verirse → **belirsiz** (probe hata koduyla biter; belirsiz bir
   sonuç "(a) çıktı" diye okunamaz).

### SONUÇ — Adım A KOŞULDU: (a) borsa tabanı

Koşu: `measure-funding` #35463452072, `main` @ `b542954`, 2026-09-19 19:09 UTC.
Semboller: `BTC-USDT-SWAP`, `SUI-USDT-SWAP` (biri en derin kitap, öteki dönem A'yı
KISMEN kapsayan bir listeleme — ikisi farklı şey sınıyor).

| Ölçüm | BTC-USDT-SWAP | SUI-USDT-SWAP |
|---|---|---|
| `limit=100` | 100 kayıt | 100 kayıt |
| `limit=200` | **200 kayıt** | **200 kayıt** |
| `limit=300` | 283 kayıt | 283 kayıt |
| `limit=400` | 283 kayıt | 284 kayıt |
| yürüyüş (`fetch_history`) | 283 kayıt, en eski 2026-06-17 16:00 | 283 kayıt, en eski 2026-06-17 16:00 |
| `after=<yürüyüş tabanı>` | **0 kayıt** | **0 kayıt** |
| `after=2024-06-30` | **0 kayıt** | **0 kayıt** |
| `before=2024-06-30` | 100 kayıt, 2026-06-17 → 2026-07-20 | aynı |

**Mekanik ayrım (`classify_depth_floor`): (a) BORSA TABANI, iki sembolde de.** Üç
bağımsız kanıt aynı yeri gösteriyor: (1) `limit=300` ile `limit=400` aynı 283 kaydı
veriyor, yani sayfa boyu bağlamıyor; (2) yürüyüş de tam olarak aynı 283'e ulaşıyor;
(3) tabanın altını isteyen iki `after` isteği de boş dönüyor. `before=2024-06-30` ise
pencerenin EN ESKİ ucundan başlıyor (2026-06-17) — 2024'e bakan bir istek bile tabanın
altına inemiyor. Uç nokta **~3 aylık KAYAN bir pencere** tutuyor ve dönem A'nın (2022-01
→ 2024-06) tamamı o pencerenin ~26 ay dışında.

**⚠ DÜZELTME — "400 kayıt tavanı" hipotezi YANLIŞTI.** Bu bölüm ilk yazıldığında
"312 kayıt, bir ikincil kaynağın bildirdiği 400 kayıtlık tavanın ALTINDA durdu; bu (b)
lehine bir işarettir" diyordu. Probe onu çürüttü: `limit=400` KABUL EDİLİYOR ama ortada
yalnızca 283 kayıt var — yani 400 bir kayıt tavanı değil, **kısıt ~3 aylık kayan bir
penceredir.** PR #37'nin 312 kaydı da bir tavanın altında durmuş değildi; o gün pencere
o kadardı. İki koşunun karşılaştırması bunu doğruluyor: #37'de en eski damga 2026-06-11,
bugün 2026-06-17 — pencere İLERİ kaydı. SUI'de taban tek koşunun içinde bile bir damga
oynadı (`limit=300` → 16:00, `limit=400` → 08:00), yani bu bir arşiv değil canlı bir
penceredir. **Yanlış hipotezin çürütülüşü sonucun kendisi kadar kayda değerdir:** yazılı
durmazsa ileride biri aynı izi yeniden sürer ve probe boşuna tekrar koşturulur.

**`fetch_history` KAYIP VERMİYOR.** Yürüyüş (`limit=100`, 3 sayfa) tek istekle
ulaşılabilen azami derinliğin (283) TAMAMINI alıyor. Kodda onarılacak bir şey yok —
veri orada değil. `limit>100`ün kabul edilmesi yalnızca istek SAYISINI düşürürdü (3 → 2)
ve derinliği değiştirmez; bu yüzden canlı yolun `--request-limit` varsayılanı
DEĞİŞTİRİLMEDİ (kural 5'in okuduğu seriye dokunmayan bir kazanç, dokunma riskini
karşılamıyor).

**Alternatif uç nokta adı YOK:** `/api/v5/public/history-funding-rate` HTTP 404.
İkincil kaynağın verdiği ad gerçek değil; `/api/v5/public/funding-rate-history` tek yol.

**A-2 — probe'un ölçtüğü (2026-09-19):** portal
(`https://www.okx.com/en-us/historical-data`) HTTP 200 döndü ve HTML'inde hem `funding`
hem `2022` geçiyor — ama bu **hiçbir şey kanıtlamıyor** ve probe da öyle raporladı:
59 KB'lık bir sayfa, veri kümesi listesi büyük olasılıkla JS ile yükleniyor, o iki kelime
menü/altbilgi metninden gelebilir. Bir YOKLUK KANITI da değildi. Probe A-2'yi **açık**
bıraktı ve elle kapatılmasını istedi.

⚠ **AŞAĞIDAKİ İKİ PARAGRAF ÇÜRÜDÜ (2026-09-20, aynı gün).** Silinmiyor; bir sonraki
başlık neyin ve hangi kanıtla çürüdüğünü yazıyor. Okumadan önce oraya bakın.

**A-2 KAPANDI (2026-09-20): OKX-İÇİ BİR YOL YOK.** Portalın sunduğu olarak anılan veri
kümeleri **tick bazlı işlem verisi ve OHLCV mum verisi**; fonlama oranı geçmişi
listelenmiyor ve fonlama için aynı kaynaklar **REST API'yi** işaret ediyor — yani
probe'un tabanına çarptığı uç noktanın ta kendisi. İki bağımsız yol aynı yere çıkıyor:
ölçüm uç noktanın ~3 ayda bittiğini gösterdi, kaynak taraması da portalın o boşluğu
dolduracak bir veri kümesi sunmadığını.

⚠ **Kanıt SINIFI yazılı olsun:** bu bir **ikincil kaynak taraması**, portalın indirme
listesinin doğrudan görüntüsü DEĞİL. Aynı sınıf kanıt bu belgede bir kez çürüdü (yukarıdaki
"400 kayıt tavanı"). Aradaki fark kaydedilmeye değer: orada tek bir ikincil iddia bir
ÖLÇÜMLE çelişiyordu, burada ikincil kaynaklar ile doğrudan ölçüm **aynı yöne** işaret
ediyor. Yine de portalın indirme listesine doğrudan bakan bir gözlem bunu tersine
çevirebilir; o gözlem gelirse **A-2 yeniden açılır** ve Adım B düşer.

### SONUÇ — A-2 GEÇTİ: portal fonlama geçmişi SUNUYOR *(2026-09-20)*

**Gözlem:** OKX'in tarihsel veri portalının (`okx.com/en-us/historical-data`) veri kümesi
listesinde fonlama oranı geçmişi VAR — *"Historical perpetual funding rates from March
2022 onwards."*

**Kanıt sınıfı: portalın indirme listesine DOĞRUDAN bakan bir tarayıcı gözlemi**
(2026-09-20). Yukarıdaki ⚠ paragrafı tam olarak bu gözlem sınıfını adıyla çağırıp
"gelirse A-2 yeniden açılır" demişti — şart, gözlem gelmeden ÖNCE yazılıydı. Çürüten
kanıt, çürüttüğü iddiadan daha güçlü bir sınıftan geliyor: orada ikincil kaynak taraması
vardı, burada listenin kendisi.

⚠ **İKİNCİ KEZ:** bu belgede bir ikincil kaynak taraması ikinci kez çürüyor (ilki "400
kayıt tavanı", karar 50). Ders tekrar yazılıyor çünkü tekrar eden bir hata artık bir
tesadüf değil bir DESENDİR: ikincil kaynak, doğrudan gözlemin yerine geçmez — bir YOKLUK
iddiası için hiç geçmez. "X listelenmiyor" cümlesi, listeye bakmayan bir kaynaktan
alınamaz.

**§6f'nin kendi kuralı devreye giriyor: "A geçerse B ve C DÜŞER"** — Adımlar tablosunun
altında, probe hiç koşmadan yazılmıştı. Dört sonuç:

1. **ADIM B DÜŞTÜ.** Dış arşiv kurulmayacak; Bybit aday sırası hiç kullanılmadı. Sıra
   SİLİNMİYOR (aşağıda, DÜŞTÜ damgasıyla duruyor): bir gün portal yolu da tıkanırsa
   sıranın sonuca bakılmadan seçilmiş olması yine gerekecek.
2. **CROSS-VENUE MUAFİYETİ KULLANILMADI.** §5'in kabul edilen sapmalar listesine satır
   EKLENMEZ. §8'in ön-kayıtlı "iddia edilmeyecekler" satırı yerinde DURUR ama
   TETİKLENMEDİ — kaldırılmaz, çünkü kaldırmak onu sonucu gördükten sonra silmek olurdu.
3. **ADIM C'nin üç koşullu biçimi (C-a/b/c) DÜŞTÜ.** Yerine karar 50'nin **LİTERAL**
   şartı geçer: aynı damga → aynı oran. Bu bir gevşetme değil SIKILAŞTIRMADIR; C-a/b/c
   zaten literal şartın cross-venue bir aday için zayıflatılmış hâliydi ve §6f o
   zayıflamayı açıkça yazmıştı.
4. **Karar 50 LİTERAL hâliyle geçerli kalıyor.** Kaydın 2. maddesi ("damga bazlı
   tutarlılık kanıtı, örtüşmede") hiç esnetilmedi; esnetmeyi gerektiren şey adayın başka
   bir borsa olmasıydı ve o aday düştü.

**Tez hâlâ ENGELLENMİŞ değil ama henüz ÖLÇÜLEBİLİR de değil:** portal verisi C kapısını
geçmeden hiçbir yerde kullanılmaz — ne bir dağılım raporunda, ne bir eşik seçiminde, ne
bir backtest'te (karar 50'nin sırası, aynen).

#### Dönem A fiilen 2022-03-01 → 2024-06-30 (28 ay)

Portal Mart 2022'de başlıyor. **Ocak–Şubat 2022 hiçbir yoldan kapsanmıyor** ve bu bir
seçim değil bir KAPSAM SINIRIDIR: kesim veri kümesinin kendi başlangıç tarihinden
geliyor, hiçbir sonuca bakılarak seçilmedi. §7.3'ün ("pencereyi sonuca göre kaydırmak
yok") yasakladığı şey bu değildir — ama ayrımın yazılı olması şart, çünkü sonradan
bakan biri için bir pencere kısaltması her zaman aynı görünür.

**Ön-kayıt: pencere bundan sonra 2022-03-01 → 2024-06-30'dur ve sonuç görüldükten sonra
ne uzatılır ne kısaltılır.** İki aylık eksik uç, dönem A'nın TEZİ hakkında bir eksiklik
olarak raporlanır; "aslında 2022-01'den başlıyordu" diye düzeltilemez (§7.1).

⚠ **BU KESİM `scripts/backtest_ema.py::PERIOD_A_START`i DEĞİŞTİRMEZ ve o sabit
2022-01-01 KALIR.** İki şey karıştırılırsa ölçüm bozulur:

| | nedir | kim okur |
|---|---|---|
| `PERIOD_A_START` = 2022-01-01 | **`ema_trend`in backtest PENCERESİ** — ön-kayıtlı (§6d), karara giren koşu (#35391881083) onunla koştu ve `diagnose_ema_exits.py`nin determinizm kapısı o sayılara bağlı | `backtest_ema.py`, `diagnose_ema_exits.py`, `measure_funding.py` |
| arşiv kapsamı = 2022-03 | **veri kaynağının nereden BAŞLADIĞI** — portalın veri kümesinin kendi ucu | fonlama tezi, bu bölümün n bütçesi |

Biri bir modelin ölçüldüğü pencere, öteki bir veri kaynağının kapsamı. Kapsam pencereyi
**DARALTIR** (fonlama tezi 28 ay görür) ama pencerenin **TANIMINI değiştirmez** — sabiti
oynatmak, zaten koşmuş ve karara girmiş bir backtest'in penceresini geriye dönük
kaydırmak olurdu (§7.3'ün tam olarak yasakladığı şey) ve determinizm kapısını kırardı.
Fonlama tezi kendi kapsamını kendi raporunda söyler; sabitin işi o değildir.

### A-3 — arşivin ERİŞİM YOLU: ilk şema adayı düştü *(2026-09-20)*

A-2 portalın fonlama veri kümesi SUNDUĞUNU gösterdi; A-3 onun **programatik yolunu**
sorar. Bu ayrı bir sorudur ve A-2'nin sonucunu DEĞİŞTİRMEZ: veri kümesinin var olduğu
doğrudan gözlemle görüldü, bulunamayan şey ona ulaşan istek.

**Sınanan şema** (ikincil bir kaynaktan, bir indirici deposundan; doğrudan gözlem DEĞİL —
ama bir VARLIK iddiası, yokluk iddiası değil, ve `scripts/probe_funding_archive.py`nin işi
tam olarak onu sınamaktı). İki adımlı: listeleme `…/priapi/v5/broker/public/orderRecord
?t=<ms>&path=cdn/okex/traderecords/<msg_type>/monthly/<ay>` dosya ADLARINI verecek,
indirme `static.okx.com/cdn/okex/traderecords/…/<YYYYMM>/<dosya>` onu kullanacaktı.

**SONUÇ (`measure-funding` #35518204981, `5667998`): listeleme ADIMI DÜŞTÜ.** Altı istek
(iki ay yazımı × `2022-03`, artı kapsam için 2022-01/-02/-03/-04) altı kez aynı cevabı
verdi: HTTP 404, `application/json`, 102 bayt,
`{"code":404,"data":{},"detailMsg":"","error_code":"404","error_message":"Not Found","msg":"Not Found"}`.

**404'ün KAPSAMI ölçüldü: ROTA düzeyinde** (`classify_not_found_scope`; kural test
koşmadan ÖNCE yazıldı — `classify_depth_floor` ve `select_primary_family` ile aynı
gerekçe). Ölçüt *"uç nokta `path` DEĞERİNİ değerlendiriyor mu"*dur ve üç istekle sorulur:

| İstek | Sonuç |
|---|---|
| gerçek `path` | HTTP 404, 102 bayt, jenerik gövde |
| saçma `path` (`BURASI-YOK-PROBE`) | **aynı** |
| `path` parametresi YOK | **aynı** |

Üç imza da aynı → uç nokta `path` değerini **hiç okumuyor** → bu adda bir rota YOK.
Şablonun parametreleri değil, **uç noktanın kendisi** tutmuyor. (İmzalardan biri ayrışsaydı
yargı "parametre düzeyinde" olurdu; bir istek hata verseydi "belirsiz" ve sessizce
"rota"ya DÜŞMEZDİ.)

⚠ **BURADAN SONRA TAHMİN TURU YOKTUR — kural, tercih değil.** Başka rota adı DENENMEZ:
ikincil kaynağın çürüdüğü noktadan sonra ad denemek körlemesine aramadır ve bu belgede
aynı sınıf kanıt iki kez çürüdü (karar 50). Sıradaki adım **doğrudan gözlemdir**:
tarayıcının ağ sekmesinden gerçek istek. Kural betiğin kendi raporunda da basılır, yani
bir sonraki okuyucu onu sohbet geçmişinde aramak zorunda kalmaz.

**'Rota yok' ile 'rota hiç olmadı' AYNI ŞEY DEĞİLDİR.** İkincil kaynak yanlış değil BAYAT
olabilir: priapi rotaları sürüm ve ad değiştirir, yani bir zamanlar çalışmış bir yol bugün
bulunmayabilir. Pratik sonucu şudur: **indirme host'u hâlâ geçerli olabilir** — listeleme
ile indirme ayrı sistemlerdir. Ama dosya ADI bilinmeden indirme denenemez, yani bu bir
çıkış yolu DEĞİL, yalnızca doğrudan gözlemde nereye bakılacağını daraltan bir nottur.

**Bu bulgu A-2'yi yeniden AÇMAZ ve Adım B'yi geri getirmez.** Engellenen şey veri kümesinin
varlığı değil, ona giden bir istek adayı. C kapısı (aynı damga → aynı oran) aynen yerinde:
arşiv, yolu bulunduktan sonra da o kapıyı geçmeden hiçbir yerde kullanılamaz.

### SONUÇ — fonlama tezi ASKIYA ALINDI: veri yolu engelli *(2026-09-21)*

⚠ **BU BİR TEZ SONUCU DEĞİLDİR.** Tez SINANMADI: ne bir dağılım görüldü, ne bir eşik
seçildi, ne bir koşu yapıldı. Askıya alınan şey ölçüm, düşen şey iddia DEĞİL — karar
50'nin ilk paragrafındaki ayrım aynen geçerli: *düşmüş bir tezi yeniden açmak yeni kanıt
ister, engellenmiş bir tezi açmaksa yalnızca yolun onarılmasını.*

**A-2 hâlâ GEÇERLİ:** portal fonlama veri kümesini SUNUYOR (doğrudan gözlem, 2026-09-20).
Askıya alınma sebebi verinin yokluğu değil, ona **güvenilir bir yoldan ulaşılamaması.**

**Portalın gözlenen kısıtları** (tarayıcıdan doğrudan gözlem, 2026-09-21):

| Gözlem | Sonucu |
|---|---|
| Aylık grupta **"All" seçeneği YOK** | 13 sembol tek talepte alınamaz |
| Sembol limiti **4** | dönem A için en az ⌈13/4⌉ = 4 tur talep |
| Aralık sınırı **6 ay** (günlükte 7 gün) | 28 aylık pencere için sembol grubu başına ≥5 talep → toplam ~20 talep |
| **Talep edilen teslim EDİLMİYOR** | aşağısı |

**Teslim güvenilmezliği ÖLÇÜLDÜ, tahmin değil:** 4 sembol × 6 ay talebi (BTC/ETH/SOL/BNB,
2022-03 → 2022-08 = 24 dosya beklenir) **TEK dosya** döndürdü — `BTC-USDT-SWAP` 2022-08.
Üstelik talebin kendi listesinde **BNB düşmüştü** ve boyut **0.00 MB** göründü. Yani
portal ne istendiğini eksiksiz kabul ediyor ne de kabul ettiğini teslim ediyor.

**Bu bir kapsam sorunundan daha kötüdür ve gerekçe budur:** eksik ama BİLİNEN bir kapsamla
ölçüm yapılabilir (kapsam raporlanır, n bütçesi ona göre kurulur — §6f'nin 10.6 sembol
hesabı tam olarak budur). Sessizce eksilen bir teslimatla yapılamaz: hangi damganın
gelmediği bilinmediği için "olay yok" ile "veri gelmedi" aynı hücreye düşer ve bu, karar
51'in çıkış kodu kapısının kapatmak için var olduğu hatanın ta kendisidir.

**İki yapısal gözlem — yol açılırsa GEREKECEK, o yüzden şimdi yazılıyor:**

1. **Portalın GÜNÜ 16:00 UTC'de başlıyor.** Günlük dosya sınırları bizim UTC gün
   tanımımızla hizalı DEĞİL; bir gün-çapası kullanan her hesap (ör. `core/metrics.py`nin
   seans kırılımı, VWAP'in gün çapası) bu farkı görmek zorunda.
2. **Aylık dosyanın damgaları tam 8 saatlik ızgarada; GÜNLÜK dosyada saniye düzeyinde
   oynama var.** C kapısının ızgara koşulu (`C-ızgara`) aylık dosyada doğrudan
   sınanabilir; günlük dosyada damga eşleşmesi tam eşitlikle DEĞİL bir tolerans kuralıyla
   kurulmak zorunda kalır — ve o tolerans, seçilmeden önce ön-kayda yazılması gereken yeni
   bir serbestliktir. Aylık dosyayı tercih etmenin gerekçesi budur.

**Yeniden açılma koşulu tek ve yazılı:** güvenilir bir veri yolu (talep edilenin eksiksiz
teslim edildiği, damga bazında doğrulanabilir bir kaynak). O gün geldiğinde C kapısı
(aynı damga → aynı oran) aynen yerinde durur; askı onu gevşetmez.

**Sicile (§6c) GİRMEZ.** §6f'nin kendi kuralı: bu bölüm bir tezin performansı hakkında
iddia taşımaz, bir VERİ YOLUNUN ön-kaydıdır. Sınanmamış bir tezi sicile yazmak, paydayı
sınanmış hipotezlerle sınanmamışları karıştırarak şişirirdi.

### Adım B — aday sırası, SONUÇTAN ÖNCE sabitlendi *(DÜŞTÜ: 2026-09-20, A-2 geçti)*

⚠ **Bu adım HİÇ KULLANILMADI.** Aşağıdaki sıra duruyor çünkü değeri sonucunda değil
biçimindedir: veriye bakılmadan sabitlenmiş bir aday sırası, portal yolu bir gün
tıkanırsa yine gerekir ve o gün yeniden yazılması "kapsamaya bakıp seçmek" olurdu.
Bugün geçerli olan yol OKX-içidir; Bybit'e hiç başvurulmadı.

1. **Bybit** — `/v5/market/funding/history`
2. **Binance** — `data.binance.vision` aylık `fundingRate` dökümleri (sembol eşleme gerekir:
   `BTCUSDT` ↔ `BTC-USDT-SWAP`)
3. **Ücretli kaynaklar** (Tardis.dev, CoinAPI, CryptoHFTData) — **kapsam dışı**

**Bybit'in önceliği kapsamadan DEĞİL, venue tercihinden gelir:** hesabın tutulduğu yer
zaten Bybit'tir — `fee_rate` Bybit'in taker oranıdır ve `scripts/measure_slippage.py`
kitabı Bybit'ten okur. Sıranın tek gerekçesi budur ve **bağlayıcıdır: Binance daha iyi
kapsama verse bile Bybit seçilir.** Kasıtlı; aksi hâlde arşiv kapsamaya bakılarak seçilir
ve kapsama sonuçla korelasyonlu olabilir (§7.2'nin "post-hoc filtre yok" kuralının kaynak
seçimine uygulanmış hâli).

Bybit C kapısında düşerse sıradaki aday Binance'tir — ama bu bir SEÇİM değil bir
DÜŞMEDİR: Bybit'in hangi koşulda düştüğü yazılır ve Binance kendi C kapısından baştan
geçmek zorundadır.

### Adım C — örtüşme kapısı (veri görülmeden sabitlendi)

⚠ **BİÇİM DEĞİŞTİ (2026-09-20): aşağıdaki C-a/b/c DÜŞTÜ, yerine karar 50'nin LİTERAL
şartı geçti.** Gerekçe bu bölümün kendi son paragrafında, veri görülmeden yazılıydı:
"O şart **aynı borsanın** arşivi için doğrudur ve adım A geçerse aynen uygulanır —
C-a/b/c'nin yerine birebir eşitlik aranır." A-2 geçti, yani aday OKX'in kendi portalıdır
ve şart birebir eşitliktir. C-a/b/c bu commit'te ölü metin olarak duruyor: silmek, bir
gün cross-venue bir adaya dönülürse eşiklerin yeniden — ve o zaman veri görüldükten
sonra — seçilmesi demekti.

#### C (YÜRÜRLÜKTEKİ BİÇİM) — aynı damga → aynı oran

**Pencere:** portal ile canlı REST serisinin kesiştiği aralık = **REST'in verebildiği
son ~94 gün** (probe #35463452072: 2026-06-17 → 2026-09-19, 283 kayıt). Bu sayı
`data.funding_history_periods` (180 periyot ≈ 60 gün) DEĞİLDİR ve karışmamalı: 180 canlı
turun okuduğu seriyi budayan bizim tavanımız, ~94 gün ise uç noktanın kendi kayan
penceresi (karar 50). Örtüşme, ikisinin GENİŞ olanıyla değil uç noktanın gerçekten
verdiğiyle tanımlanır — kapı ölçülebilen her damgaya sorulmalı.

**Evren:** `ema` katmanının 13 sembolü (listeleme notu bu pencereyi etkilemez).

| Kapı | Ölçü | Geçme koşulu |
|---|---|---|
| **C-lit** | damga → oran eşitliği | ortak damgaların **tamamında** oran birebir aynı |
| **C-ızgara** | fonlama aralığı | her sembolde aralık 8 saat (probe BTC/SUI'de doğruladı; 13/13 teyit EDİLMEDİ) |

**Yuvarlama farkı kabul edilir ama RAPORLANIR** (iki kaynağın ondalık gösterimi farklı
olabilir; bu bir veri farkı değil bir biçim farkıdır). **Sistematik fark varsa portal
verisi KULLANILMAZ** ve sebebi araştırılır — "yakın" bir seri, kural 5'in reddettiği
"iki kaynaktan beslenen seri"nin ta kendisidir.

**Eşleşmeyen DAMGA bir düşme sebebi değil, bir RAPOR kalemidir:** iki tarafın damga
kümeleri uçlarda (pencerenin kenarında) doğal olarak ayrışır. Düşüren şey ORTAK
damgalarda oranın ayrışmasıdır.

<details>
<summary>DÜŞMÜŞ BİÇİM — C-a/b/c (cross-venue aday için, kullanılmadı)</summary>

**Üç koşul, ÜÇÜ BİRDEN:**

| Kapı | Ölçü | Geçme koşulu |
|---|---|---|
| **C-a** | damga hizası | iki serinin damga kümeleri **≥ %95** örtüşür (Jaccard, damga bazında) |
| **C-b** | olay kümesi | p95 eşiğinin ürettiği olay kümelerinin **Jaccard ≥ 0.70** |
| **C-c** | işaret uyumu | ortak damgaların **≥ %95**'inde oranın İŞARETİ aynı |

Üç sayı da bu commit'te sabittir ve koşudan sonra §7.1/§7.4 gereği değiştirilmez.

**C-b'nin eşiği STATİKTİR, kayan değil:** ölçümün kendi kuralı 270 periyotluk kayan
pencere + `shift(1)`tir ve o pencere 60 güne SIĞMAZ. C-b'de p95, örtüşme penceresinin
kendi dağılımından hesaplanır. Bu bilinçli bir sapmadır, yalnızca C kapısında geçerlidir
ve dağılım raporunun kuralını DEĞİŞTİRMEZ.

**C-b'nin örneklemi küçüktür ve bu yazılı olsun:** 60 gün = sembol başına ~180 damga, p95
≈ 9 olay, 13 sembolde ≈ 117 olay. `Jaccard ≥ 0.70`, iki kümenin her birinin ~%82'sinin
ortak olması demektir — yani ≈117 olayda her iki tarafta ~21 olayın kayması tolere edilir.
Kapı bu çözünürlükte okunmalıdır: geçmesi "seriler aynı" demek değil, "olay kümeleri bu
örneklemde ayırt edilemiyor" demektir.

⚠ **C, karar 50'nin LİTERAL şartından daha zayıftır ve bu sessizce geçilmiyor.** Karar 50
"aynı zaman damgası, aynı oran" dedi. O şart **aynı borsanın** arşivi için doğrudur ve
adım A geçerse aynen uygulanır — C-a/b/c'nin yerine birebir eşitlik aranır. Ama B'nin
adayları BAŞKA borsalardır ve başka bir borsanın fonlama oranı tanım gereği başka bir
sayıdır: "aynı oran" orada sağlanamadığı için değil, **anlamsız olduğu için** geçersizdir.
Cross-venue bir arşivde ölçülebilen şey "aynı veri" değil **"aynı olgu"**dur ve yukarıdaki
üç koşul tam olarak onu ölçer.

**Bedeli de yazılı:** cross-venue bir arşivle seçilen bir eşik, OKX mumlarıyla koşacak bir
backtest'e uygulandığında bir **cross-venue varsayımı** taşır — §5c'nin "maliyet modeli
varsayımdır, gözlem değil" maddesiyle aynı statü. Arşiv kullanılırsa bu sapma §5'in kabul
edilen sapmalar listesine girer ve §8'in "iddia edilmeyecekler" listesine bir satır ekler:
*eşiğin OKX'te de aynı olayları seçtiği gösterilmedi, yalnızca ayırt edilemediği.*

</details>

### Örneklem bütçesi — n hesabı 13 DEĞİL ~10.6 sembol, 30 DEĞİL 28 ay üzerinden

**GÜNCELLENDİ (2026-09-20, A-2 geçti).** Pencere 2022-01-01 değil **2022-03-01**'de
başlıyor (portalın veri kümesi Mart 2022'den itibaren). İki düzeltme birbirinden
bağımsızdır ve ikisi de aşağıda:

Dönem A (**2022-03-01 → 2024-06-30, 853 gün ≈ 28 ay**) boyunca `ema` katmanının 13
sembolünün üçü pencereyi tam göremez. Bu bir arşiv kusuru değil bir **listeleme
tarihidir** ve `measure_funding.py::coverage` ayrımı zaten yapar (beklenen sayı sembolün
KENDİ ilk kaydından sayılır):

| Sembol | Listeleme (ikincil kaynak; portal kapsam tablosu DOĞRULAYACAK) | Dönem A payı |
|---|---|---|
| PENGU | perp 2024-12-18 | **0.00** — dönem A 2024-06-30'da biter |
| ETHFI | 2024-03-18 | ~0.12 (≈3.4 ay / 28) |
| SUI | 2023-05 | ~0.50 (≈14 ay / 28) |
| diğer 10 | 2022-03-01 öncesi (VARSAYIM, doğrulanacak) | 1.00 |

**Etkin sembol sayısı ≈ 10 + 0.50 + 0.12 + 0.00 = 10.62.** Beklenen damga sayısı
`853 × 3 × 10.62 ≈ 27.200` — 13 sembol varsayımının (`13 × 853 × 3 ≈ 33.300`) **%82'si.**

Pencere kısalması bütçeyi ayrıca **~%6** düşürüyor (eski hesap 912 gün × 10.58 ≈ 29.000).
İki etki aynı yöne çalışıyor ve toplamı, naif `13 × 912 × 3 ≈ 35.600` beklentisinin
**%76'sıdır** — yani naif hesap olay sayısını ~3'te 1 fazla sayar.

**ÖN-KAYIT: bu tezle ilgili her n hesabı, güç hesabı ve "kaç olay beklenir" beklentisi
10.6 sembol ve 853 gün üzerinden kurulur.** 13 sembol ya da 912 gün üzerinden kurulmuş
bir beklenti olay sayısını fazla sayar; fonlama ekstremleri zaten seyrekse bu fark tezi
ölçülebilir taraftan ölçülemez tarafa itebilir — ve `acceptance.min_trades` (30) kapısına
ulaşıp ulaşılamadığı tam olarak bu sayıya bağlıdır. Sayılar koşudan SONRA "aslında 13'tü"
ya da "aslında 2022-01'den başlıyordu" diye düzeltilemez (§7.1).

**Üç sembol satırı C kapısını ETKİLEMEZ** (örtüşme penceresi REST'in son ~94 günüdür,
orada üçü de vardır). Etkilediği şey dönem A'nın TEZİDİR. Kapsama tablosunda bu üç
sembolün düşük `completeness` değeri **arşiv kusuru olarak okunamaz**; hangi arşiv
gelirse gelsin dönem A'da 13/13 mümkün değildir.

**Ocak–Şubat 2022'nin eksikliği de bir arşiv kusuru DEĞİLDİR** ve öyle raporlanmaz:
portalın veri kümesi orada başlıyor. Eksik uç, sonucun yanında bir KAPSAM notu olarak
durur.

### Ölçüm adımının varsayılanı KAPALI — gerekçesi burada durur

`measure-funding.yml`in dağılım adımı (`run_measure`) **varsayılan olarak `false`**;
probe (`run_probe`) varsayılan olarak `true`. Gerekçe bu bölümün tamamıdır: bugünkü veri
yolu 60 gün veriyor ve dönem A 30 ay — ölçümü elle açmadan koşturmak, 30 aylık bir soruya
60 günlük bir pencereyle cevap arayıp neredeyse boş bir rapor üretmek demektir. Boş bir
rapor zararsız değildir: "ölçtük, olay yok" ile "ölçemedik" aynı log'a benzer ve karar
50'nin tam olarak ayırmak istediği şey budur (engellenmiş tez ↔ düşmüş tez).

**Bu bir kapı DEĞİL bir varsayılandır:** kimse engellenmiyor, tek kutu işaretlenerek
koşuluyor. Arşiv bu bölümün kapılarını geçtiğinde varsayılan `true`ya döner ve o
değişikliğin gerekçesi de buraya yazılır — "bu neden kapalıydı" sorusunun cevabı
workflow'un git geçmişinde değil, ön-kayıtta durmalı.

### Bu ön-kayıt neyi SEÇMİYOR

**Eşiği.** Hangi persentil, hangi yön (pozitif/negatif kuyruk), hangi kümeleme kuralı bir
olay sayar — hiçbiri burada yok, çünkü hiçbiri veri görülmeden seçilemez ve
`measure_funding.py`nin raporu da onları önermez (§7). Eşik, arşiv C kapısını geçtikten ve
dağılım GÖRÜLDÜKTEN sonra **ayrı bir ön-kayıtla** seçilir ve o ön-kayıt §6c'nin siciline
kendi satırını açar. Bu belge yalnızca **hangi veriyle** sorulacağını sabitler, **ne
sorulacağını** değil.

---

## 6g. ÖN-KAYIT — `xsec_mom` (kesitsel momentum) ve `xsec` katmanı

**Bu belge koşudan ÖNCE yazıldı ve commit edildi.** Hiçbir sayı görülmedi: ne bir
sıralama, ne bir işlem, ne bir ortalama. §7'nin tamamı bu bölüme uygulanır.

### Mekanizma — neden böyle bir edge olabilir

Kripto'da sermaye akışı son dönemin kazananlarını **gecikmeyle** takip eder; göreli güç
kısa vadede kalıcıdır. Karşı tarafta duran şey geç gelen akıştır. Tez, fiyatın kendi
geçmişine değil **diğer sembollere göre** konumuna dayanır.

**`ema_trend`den (model 18) farkı bir parametre değil, sorunun kendisidir:** o ZAMAN
SERİSİ momentumudur ("bu sembol yükseliyor mu"), bu KESİTSELdir ("bu sembol
diğerlerinden iyi mi"). Yatay bir piyasada zaman serisi sinyali susar, kesitsel sinyal
susmaz — göreli sıralama her zaman tanımlıdır. İki modelin aynı katmanda olmamasının
sebebi de budur (aşağısı): ölçtükleri şey farklı, koşulları da öyle.

### Model: `xsec_mom` (long-only, stop'lu, yarışmacı)

| Boyut | Tanım |
|---|---|
| Evren | `xsec` katmanının SABİT 13 sembolü (`ema` katmanının aynısı) |
| Uygunluk | sembol, o rebalance barında **tam geriye bakış geçmişine** (126 bar) sahipse uygundur; değilse o hafta sıralamaya GİRMEZ |
| Sıralama ölçütü | **geriye bakış getirisi = 126 × 4H bar (21 gün)**, `close[t] / close[t−126] − 1` |
| Rebalance | **haftalık**, aşağıdaki bar tanımıyla |
| Portföy | **top-3** |
| Giriş | top-3'e giren ve elde olmayan sembol; dolum bir SONRAKİ barın açılışında (kural 13, değişiklik yok) |
| Çıkış | (1) rebalance'ta top-3 DIŞINA düşerse, (2) stop |
| Take-profit | **YOK** |
| Stop | **5 × ATR(14)**, projenin varsayılan yumuşatmasıyla (`simple`) |
| Trailing / breakeven / kısmi çıkış / zaman stop'u | **YOK** |
| Boyutlandırma, maliyet, kayma, funding, likidasyon, dolum | **repo standardı, değişiklik yok** |

**Geriye bakış 21 gün, çünkü** kripto kesitsel momentum literatüründeki 1–4 haftalık
bandın ortasıdır. Tek değer seçildi ve **süpürülmeyecek** (§7.1).

**TP YOK, çünkü** `ema_trend`in yol ölçümü (§6e > SONUÇ, karar 49) hedef geometrisinin
beklenen değeri değiştirmediğini gösterdi: hedef sonrası işaretli hareket sıfır etrafında
salınıyordu. Momentumda kâr, pozisyonda **kalma süresinden** gelir; bir hedef tam olarak
o süreyi keserdi.

**Stop 5×ATR, çünkü** tipik tutuş ~1 hafta = 42 bardır ve stop, haftalık oynaklık
ölçeğinde olmalıdır ki **baskın çıkış rebalance olsun, gürültü stop'u değil.** Bu bir
tasarım ilkesidir ve sonuçtan türetilmedi — P1 tahmini (aşağıda) tam olarak bu niyetin
sınamasıdır.

⚠ **Yan etkisi ÖNCEDEN yazılıyor, sonradan keşfedilen bir avantaj olarak okunmasın:**
friksiyon/R = `2c / stop%` olduğu için R başına friksiyon `ema_trend`in 1.5×ATR'sine göre
**~3.3 kat düşer.** Bu, modeli otomatik olarak iyi yapmaz — aynı oranda R başına
**sürüklenme de** küçülür (karar 35'in özdeşliği). Kaydın sebebi şudur: iki model
`avg_stop_distance_pct` kolonunda ayrışacak ve stop bandı uyarısı (⚠ B) tetiklenebilir;
o uyarı görüldüğünde şaşırılmayacak, çünkü burada yazılı.

### ATR yumuşatması: `simple` — ve bu bir DÜZELTMEDİR

Bu modelin ilk taslağı `wilder` diyordu. **Düzeltildi ve gerekçesi kayda geçiyor:**
`ema_trend` Wilder'ı bir SPEC uyumu olarak aldı (kaynak sistem TradingView `ta.atr` ile
doğrulanmıştı, §6d > TADİLAT-1). `xsec_mom`un öyle bir dış referansı **yoktur** — Wilder
seçmek gerekçesiz bir ayrışma olurdu.

Üstelik somut bir bedeli vardı: motorun stop tavanı kontrolü (`core/engine.py::
_within_stop_band`) ATR'yi projenin **varsayılanıyla** hesaplar ve modelin bildirimini
okumaz (CLAUDE.md'nin kuralı: *her model kendi yumuşatmasıyla kendi tavanını
genişletebilseydi tavan bir kural olmaktan çıkardı*). Model Wilder, tavan `simple` ölçseydi
"5×ATR" ifadesi iki farklı sayı anlamına gelir ve oran kayardı. `simple` seçmek bu kaymayı
**sıfırlar** ve modele özel bir istisna gerektirmez.

Periyot ortak kalır: `trailing.atr_period` = 14 (projenin tek ATR periyodu).

### Kontrol: `xsec_random` (yarışmacı, referans DEĞİL)

Ölçülen şey **seçimin kendisidir**, o yüzden kontrol seçim dışındaki her şeyde birebir
aynıdır:

| Aynı | Farklı |
|---|---|
| rebalance günleri, uygunluk kuralı, top-3 kotası, stop (5×ATR simple), çıkış kuralları (rebalance + stop), TP yokluğu, boyutlandırma, maliyet | **seçim**: uygun semboller arasından **rastgele 3** |

**Çekiliş BAĞIMSIZDIR, paylaşılmaz.** Gerekçe scalp katmanının kuralıdır (CLAUDE.md >
"Çekiliş, ölçülmeyen eksende PAYLAŞILIR, ölçülen eksende BAĞIMSIZDIR"): burada ölçülen
eksen seçimin ta kendisi, yani çekiliş paylaşılsaydı fark momentumun değil tesadüfün
ölçüsü olurdu. Tohumlama `random_ctrl`ün desenini izler (`random_seed` + `as_of`): aynı
bar yeniden koşulduğunda aynı çekiliş, farklı barlarda bağımsız çekiliş.

**Kontrol bir REFERANS değil yarışmacıdır** (`is_benchmark = False`) ve katmanın
`acceptance.control_model`ü odur.

### Kontrolün TOHUMU — SABİT ve tek seferlik

**`random_seed = 20240217`** (`config.yaml`, kök). `xsec_random` çekilişini
`random.Random(f"{random_seed}:{self.name}:{as_of}")` ile kurar; yani kontrolün her
rebalance günündeki seçimi bu sayının ve barın saf fonksiyonudur.

**Koşu tek seferliktir, farklı tohumla yeniden koşulmaz.** Gerekçe bağlayıcı kapının
kendisidir: E kapısı (§6g > Kapılar) `xsec_mom` ile `xsec_random` arasındaki ortalama R
FARKINA dayanır. Tohum serbest bırakılsaydı aynı model, aynı pencere ve aynı defterle
"kontrol kötü çıkana kadar yeniden koş" mümkün olurdu — ve bu §7.1'in yasakladığı
parametre aramasının en sinsi hâli olurdu, çünkü aranan şey modelin bir parametresi değil
KARŞILAŞTIRMA ZEMİNİ olurdu. Kontrolün bilgisiz olması onu manipülasyona kapalı yapmaz:
bilgisiz bir çekiliş de yeniden çekilebilir.

Bu yüzden sayı koşudan ÖNCE, bu commit'te sabittir:

- Tohum değişirse koşu **yeni bir tez** sayılır ve sicile (§6c) ayrı bir satır olarak
  girer; mevcut satırın sonucu onunla değiştirilemez ve BH paydası (§6c) bir artar.
- Tohumun sabitliği bir belge cümlesine bırakılmaz, **mekanik olarak sınanır**
  (`tests/test_docs_sync.py`): `config.yaml > random_seed` ile bu bölümde yazan sayı
  aynı olmalıdır. İki yerde yazılı bir sayı bir gün sessizce ayrışır — aynı gerekçe
  `scripts/measure_funding.py`nin pencere sınırlarını `backtest_ema.py`den İTHAL
  etmesidir.

### Katman: yeni `xsec` — neden `ema`ya eklenmedi

**`ema` katmanının stop tavanı 3.0'dır ve tavanı motor merkezî olarak uygular**
(`_within_stop_band`, adım C): tavanı aşan sinyal **elenir**. 5×ATR stop o katmanda
istisnasız her sinyali düşürürdü — model sıfır işlem yapardı.

Üç yol vardı ve seçim **sonuç görülmeden** yapıldı:

| Yol | Karar |
|---|---|
| Stop'u ≤3×ATR'ye indirmek | **RED** — tasarım niyetini bozar; "baskın çıkış rebalance olsun" gerekçesi düşer ve P1'in dayanağı kalmaz |
| `ema` katmanının tavanını yükseltmek | **RED** — tamamlanmış, ön-kayıtlı bir koşunun (#35391881083) katman koşulunu geriye dönük değiştirir; §7.3'ün yasakladığı şeyin katman düzeyindeki hâli |
| **Yeni `xsec` katmanı** | **SEÇİLDİ** |

Gerekçe `ema` katmanının kendi varlık sebebiyle birebir aynıdır: *backtest'in ölçtüğü
koşullar forward test'inkiyle aynı olmalı.* Katman `ema`nın evrenini (aynı 13 sembol),
maliyetini, riskini ve defter kurallarını aynen taşır; ayrışan tek şey stop tavanı ve
model listesidir.

**`max_stop_atr_multiple = 6.0` ve bu sayı koşudan önce sabittir.** Gerekçe: stop 5×ATR
ve dolum kayması (giriş bir sonraki barın açılışından, kural 13) mesafeyi ölçüldüğü andan
sonra genişletebilir. 6.0, o paya yer bırakır ve **tavanı bir kural olmaktan çıkarmaz** —
5×ATR'yi sistematik olarak aşan bir kurulum yine elenir. Sayı sonuca göre
DEĞİŞTİRİLMEYECEKTİR (§7.1).

Katman `ledgers_xsec/` ve `docs/data/metrics_xsec.json` kullanır; **tetikleyicisi YOKTUR**
(`ema` ile aynı statü: tanım var, koşu yok — canlıya alma ayrı bir karardır).

### Rebalance barının TAM tanımı

Bar indeksi **açılış** zamanıdır (`core/data.py`: kapanış = `bar_ts + timeframe`). Bu
yüzden "Pazartesi 00:00 kapanışı" tek başına iki şeye işaret edebilirdi; seçilen ve
sabitlenen tanım:

> **Rebalance barı, indeksi PAZAR 20:00 UTC olan 4H bardır** (Pazartesi 00:00 UTC'de
> kapanır). Sıralama o barın kapanışıyla hesaplanır; giriş ve çıkış emirleri bir sonraki
> barın açılışından, yani **Pazartesi 00:00 UTC**'den dolar (kural 13).

Böylece haftanın ilk barının açılışı girişin ta kendisi olur. Alternatif (indeks Pazartesi
00:00, dolum 04:00) **reddedildi** ve bu satır onu kayda geçirir.

⚠ **Canlı not (bugün geçerli değil, canlıya alınırsa geçerli):** `signals_per_bar` bu
katmanda `false` olacak (base/ema ile aynı). Backtest harness'ı onu `true` yapar, yani her
bar sinyal üretilir ve modelin kendisi rebalance barını süzer — backtest'te sorun yok.
Canlıda ise bir Pazartesi turu düşerse **o haftanın rebalance'ı komple kaçar.** Bu bir
kadans riskidir (karar 39'un ölçtüğü şeyin aynısı) ve canlıya alma aşamasına gelinirse o
zaman çözülür; bugün yalnızca yazılı durur.

### Pencereler ve embargo

| Dönem | Aralık | Rol |
|---|---|---|
| **A** | 2022-01-01 → 2024-06-30 (sinyal kesimi) | IS |
| **B** | 2024-06-30 + embargo → koşu günü | **OOS** |

`ema_trend` ile **aynı pencereler** — kasıtlı: iki model aynı rejimleri görür ve
sonuçları aynı piyasa geçmişine göre okunur.

**Embargo VARSAYILMAZ, dönem A'dan ÖLÇÜLÜR** (`holding_stats`), yöntem §6d'nin aynısı:
modelin zaman stop'u yoktur, yani tutuş süresinin tanım gereği bir üst sınırı yoktur ve
§6.1'in dayandığı sınır kurulamaz. Dönem A'da gözlenen **azami tutuş süresi** embargo
olarak uygulanır ve uygulanan değer `manifest.json > window.embargo_bars`ta raporlanır.

⚠ **Dönem B'ye koşu öncesi DOKUNULMAZ.** A koşulup embargo ölçülmeden B başlatılmaz.

**Beklenti (tahmin değil, bir not):** bir sembol top-3'te haftalarca kalabileceği için
azami tutuş `ema_trend`in 129 barından (21.5 gün) UZUN çıkabilir. Embargo o ölçüme
uyar; uzun çıkması bir sorun değil, ölçümün kendisidir.

### Kapılar (hepsi BAĞLAYICI)

**1) Repo kapıları birincildir** — §3'ün B-0/B-1/B-2'si ve §4'ün C-1..C-5'i, canlı
koşuyla birebir aynı `acceptance_flags` hesabından okunur.

**2) E kapısı (edge) BAĞLAYICIDIR ve repo standardındadır:** ortalama R kontrolün
ortalama R'sini **en az 0.15R marjla** aşar **ve** farkın bootstrap güven aralığının alt
sınırı **> 0** (`acceptance.edge_ci_alpha` = 0.05) **ve** hesap getirisi çıpayı geçer.

⚠ **İlk taslaktaki daha zayıf "ek kapı" (yalnızca `momentum > random_ctrl`) DÜŞÜRÜLDÜ.**
Gerekçe `ema_trend`de verilen kararla aynıdır: bu modele diğerlerinden kolay bir kapı
açmak karşılaştırmayı bozar. Yön karşılaştırması bir KAPI değil, aşağıdaki **P2
tahminidir**.

**3) Model sahibinin kapıları:**

| Kapı | Koşul | Statü |
|---|---|---|
| **K-2** | Toplam işlem sayısı (dönem A+B) | **BAĞLAYICI DEĞİL — raporlanır** (bkz. TADİLAT-1) |
| **K-3** | Max drawdown **%25'i geçmiyor** | **BAĞLAYICI** |

#### TADİLAT-1 — K-2 bağlayıcı olmaktan çıktı *(koşudan önce, işlem sayısı GÖRÜLMEDEN)*

> **K-2 xsec için bağlayıcı değil. Gerekçe: bağlayıcı kapılar CI bazlı ve örneklem
> yeterliliğini içeriyor; sayı eşiği PF bazlı kapılar için tasarlanmıştı. Gerçekleşen
> işlem sayısı raporlanır. Karar koşudan önce, işlem sayısı görülmeden verildi; eşik
> gevşetilmedi, eşiğin kapsamı değişti.**

Ayrıntı: K-2 `ema_trend` için eklenmişti ve amacı tekti — **küçük örneklemde tesadüfi bir
kâr faktörünün modeli geçirmesini engellemek.** Orada bağlayıcı kapılar PF bazlıydı ve PF
örneklem büyüklüğünü kendi içinde TAŞIMAZ; bu yüzden ayrı bir sayı eşiği gerekiyordu.

`xsec`te bağlayıcı kapılar **güven aralığı bazlıdır** (`avg_r_ci_low > 0` ve kontrol
farkının bootstrap CI alt sınırı > 0). Bir CI kapısı örneklem yetersizliğini
**kendiliğinden cezalandırır:** 195 işlemle geçen bir model, 300 işlemle geçenden daha
BÜYÜK bir etki göstermek zorundadır. Yani K-2'nin işini burada başka bir kapı zaten
yapıyor; onu bağlayıcı tutmak modeli **edge'le ilgisi olmayan** bir sebeple düşürebilirdi.

⚠ **Eşik İNDİRİLMEDİ ve bu bilinçlidir.** 300'ü 150 ya da 200 yapmak, projeksiyona
(195–366 beklentisi) bakarak kapıyı tasarıma uydurmak olurdu — §7.1'in yasakladığı şeyin
kapı tarafındaki hâli. **Kapıyı kaldırmak ilkelidir, düşürmek değil:** kaldırma bir
GEREKÇEYE dayanır (kapsamın yanlış olması), düşürme ise beklenen sayıya.

**Raporlama zorunlu kalır:** gerçekleşen toplam işlem sayısı (A, B ve A+B) sonuçla
birlikte yazılır. Bağlayıcı olmaması, görünmez olması demek değildir.

**K-1 UYGULANMAZ ve gerekçesi yapısaldır:** K-1 tek-sembollü koşulardan okunur ("en az 6
coinde dönem B PF > 1.1"), oysa `xsec_mom` tanımı gereği bir PORTFÖY modelidir — top-3
seçimi ancak tüm sembollere aynı anda bakılarak kurulur ve tek-sembollü bir koşu modeli
başka bir modele çevirir. Kapıyı zorla uygulamak, ölçülmek istenen şeyi ölçmemek olurdu.

**K-3'ün tanımı** §6d'deki gibi sabittir: hesap tektir ve coin başına bölünemez; burada
portföy modeli olduğu için doğrudan **hesap düzeyi `max_drawdown`** okunur.

**K-2'nin neden bağlayıcı olamayacağını gösteren sayı da burada dursun** (TADİLAT-1'in
dayanağı, koşudan önce hesaplandı): `ema_trend`in 978 işlemi tek-sembollü koşuların
TOPLAMIYDI; burada öyle bir toplam yok, yalnızca portföy koşusu var. Beklenen aralık:
A+B'de **244 rebalance günü** × haftalık giriş sayısı ≈ **195 – 366 işlem** (haftalık
devir 0.8 ↔ 1.5) — eşik 300 bu aralığın **içinde**. Bu bir projeksiyondur, bir ölçüm
değil; eşiği indirmek için değil, eşiğin bu modelde neyi ölçtüğünü göstermek için
yazıldı.

**4) Çıpa istisnası — `ema_trend`dekiyle birebir aynı:** model YALNIZCA C-3'ten (hesap
getirisi `buyhold` çıpasını geçer) kalıyor ve diğer TÜM kapıları geçiyorsa koşu **DURUR**,
karar kullanıcıya gider, otomatik geçiş YOKTUR. Başka herhangi bir kapıdan kalırsa
doğrudan **BLOKE**. İstisna tek kapı içindir ve genişletilemez.

### İSTATİSTİKSEL GÜÇ — dürüst hâliyle

**Örneklem tahmini.** Dönem A'da **130 rebalance günü** (912 gün, Pazartesi sayımı).
Rebalance günlerinde ortalama uygun sembol **10.54** (10 → 12; SUI 2023-05, ETHFI
2024-03, her biri +21 gün ısınma; PENGU dönem A'da hiç yok). Haftalık devir top-3'te
0.8–1.5 giriş varsayımıyla:

| Haftalık giriş | Dönem A işlem |
|---|---|
| 0.8 | ~104 |
| 1.2 | ~156 |
| 1.5 | ~195 |

**Merkezî beklenti ~150–200 işlem.**

**R dağılımının sd'si.** TP yok, stop −1R'de kesiyor, rebalance çıkışı kuyruğu buduyor.
Bu yapı için **sd ≈ 1.2 – 1.6 R** varsayılıyor (nokta tahmin 1.4). Varsayım koşudan sonra
gerçekleşen sd ile karşılaştırılacak ve **fark raporlanacaktır** — güç hesabının kendisi
de denetlenebilir olmalıdır.

**MDE (güç 0.80), n = 175:**

| Kapı | MDE |
|---|---|
| C-1: ortalama R > 0 (tek örneklem, tek yönlü α=0.05) | **0.23 – 0.30 R** |
| `avg_r_ci_low` > 0 (≈ iki yönlü α=0.05) | **0.25 – 0.34 R** |
| **E kapısı: kontrol farkı (iki örneklem)** | **0.36 – 0.48 R** |

İki örneklem karşılaştırması eşleştirilmemiştir: iki model aynı GÜNLERDE işlem açar ama
aynı SEMBOLLERDE açmaz, yani eşleştirilecek çift yoktur (CLAUDE.md >
`bootstrap_diff_ci`'ın aynı gerekçesi).

> **Bağlayıcı kapıda MDE ≈ 0.42R. Bu kurulum ancak büyük bir etkiyi tespit edebilir;
> "ayırt edilemedi" beklenen sonuçlardan biridir ve tezin reddi olarak okunmaz.**

**Güç için tasarım OYNATILMADI ve bu bilinçlidir.** Rebalance sıklığını artırmak ya da
k'yı büyütmek örneklemi büyütürdü. Veri görülmediği için teknik olarak §7'yi ihlal
etmezdi — ama **güç hesabına bakarak tasarım seçmek ayrı bir kaygan zemindir** ve her
değişiklik friksiyonu artırır (karar 35'in özdeşliği). Tasarım olduğu gibi kalıyor;
bedeli yukarıdaki MDE'dir ve o bedel burada yazılı.

### ÖN-KAYITLI TAHMİNLER (sonucu görmeden)

| # | Ölçüm | Tahmin | Çürütür |
|---|---|---|---|
| **P1** | çıkış sebebi dağılımı | rebalance çıkışı **≥ %70**, stop **≤ %30** | dışına çıkması → stop çok dar demektir, tasarım niyeti tutmamıştır |
| **P2** | seçim | dönem A'da `xsec_mom` ort. R **>** `xsec_random` ort. R | ≤ olması |
| **P3** | ortalama R | dönem A'da **> 0** (C-1) | ≤ 0 |
| **P4** | çıpa | hesap getirisi `buyhold`un **ALTINDA** (C-3 DÜŞER) | çıpayı geçerse |

**P1 bir performans tahmini değil, TASARIM NİYETİNİN sınamasıdır:** stop'un rolü
felaket sigortasıdır, birincil çıkış değil. Tutmazsa model "kötü" olmaz — 5×ATR'nin
haftalık ölçek için yeterli olmadığı ölçülmüş olur.

**P4'ün pozitif çıkması (çıpanın geçilmesi) bir sürpriz olur ve öyle raporlanır:**
long-only bir top-k modelin boğa dönemlerinde al-tut'un gerisinde kalması beklenir.

### Koşu kuralları

- **Parametre araması YOK.** Tek tanım, tek koşu. Geriye bakış (126), k (3), rebalance
  (haftalık), stop (5×ATR) ve tavan (6.0) bu commit'te sabittir.
- **Kapıdan kalırsa ayar aranmaz.** Düşen bir tez sicilde kalır (§6c).
- **Tek workflow tetiklemesi, tek sonuç dosyası.**
- **Tek tohum.** Kontrolün çekilişi `random_seed = 20240217`e bağlıdır ve koşu tek
  seferliktir, farklı tohumla yeniden koşulmaz (§6g > Kontrolün TOHUMU).
- **Koşu sonrası ZORUNLU raporlama:** çıkış sebebi kırılımı (`exit_rule`) ve tutuş süresi
  dağılımı — `ema_trend`in yol ölçümündeki gibi. P1 bu ikisinden okunur.

### Bu ön-kayıt neyi SEÇMİYOR

Canlıya alınmayı. `xsec` katmanının tetikleyicisi yoktur ve kapılar geçilse bile
`run-xsec.yml` ayrı bir karardır — `ema` katmanının bugünkü statüsünün aynısı. Kod
ölçülmeden yarışmaz; ölçülüp geçse bile canlıya alınması otomatik değildir.

---

## 6h. ÖN-KAYIT — `wave_scalp` (model 21): Elliott Wave Dalga-3, 15m

**Bu bölüm koşudan ÖNCE yazıldı ve commit edildi; tarih damgası git'tedir.** Hiçbir sayı
görülmedi: ne bir pivot, ne bir işlem, ne bir ortalama. Kod da bu commit'te YOKTUR —
ön-kayıt modelden önce gelir. §7'nin tamamı bu bölüme uygulanır.

### NUMARALANDIRMA DÜZELTMESİ — talep "§6g / model 19" diyordu, ikisi de DOLU

Talep bu bölümü `§6g`, modeli `19` olarak adlandırıyordu. İkisi de alınmıştır: **§6g**
`xsec_mom`un ön-kaydıdır ve **19-20** numaraları `xsec_mom` / `xsec_random`a aittir
(`strategies/registry.py`, karar 53'ün öncesi). Çakışmaya izin vermek bir yazım detayı
olmazdı: iki ön-kayıt aynı başlığı taşısa sicilden (§6c) hangi hipotezin hangi pencerede
koşulduğu okunamaz ve düşen bir tez, geçen bir tezin başlığı altında kaybolurdu. Bu
yüzden bölüm **§6h**, model **21**'dir. Talebin başka hiçbir satırı değişmedi.

### Köken: dış bir sistem, ama SİNYAL ve ÖĞRENME düzeyinde

Model `klonnist/Hasanwavebot` deposunun **`15m` profilinden** gelir
(`.github/workflows/run-bot.yml`: `main.py --timeframe 15m --leverage 10 --trade-margin
500 --data-dir ./data/15m --once`; `--strategy` varsayılanı `wave`). Aynı kaynağın VWAP
profili bu repoda zaten `vwap_clone` (model 13) olarak koşuyor, yani altyapı — üç aşamalı
çıkış yönetimi, bandit deseni, defterden türeyen durum — **tek kopya olarak mevcuttur** ve
bu model onu yeniden yazmaz.

**SPEC referansı `fa888b7`'dir** (`klonnist/Hasanwavebot`, 2026-09-21). Aşağıdaki her kural
o commit'teki `wave_detector.py`, `learner.py` ve `main.py`'den okunmuştur; koşudan sonra
kaynak değişse bile bu ön-kayıt o commit'i anlatmaya devam eder.

⚠ **Kaynağın kendi defteri GÖRÜLMÜŞ VERİDİR.** `data/15m/account_state.json` (09.09 –
21.09.2026, 195 pozisyon) sohbette okundu: brüt ort. R +0.057, CI [−0.08, +0.20]; ev
komisyonuyla (0.00055 × 2) net −0.044. **Eylül 2026 bu yüzden hiçbir döneme girmez** ve
hiçbir OOS iddiasında kullanılamaz. P1 ve P2 tahminleri (aşağıda) tam olarak bu görülmüş
pencereden türetildi ve öyle etiketlendi.

### 1. Statü: YARIŞMACI, kopya DEĞİL (`is_replica = False`)

Emsal `ema_trend`dir (§6d, karar 45 §1): dışarıdan gelen yalnızca **sinyal ve öğrenme**
kuralıdır; boyut, kaldıraç tavanı, maliyet, funding ve likidasyon **evin** kurallarıdır.

| Kaynaktan gelen | Evden gelen |
|---|---|
| zigzag + Dalga-3 kurulum kuralı, seviye geometrisi (giriş/TP/SL) | boyutlandırma (`sizing="risk"`, `risk_per_trade` %1) |
| bandit ızgarası (12 kombinasyon) ve seçim sırası | kaldıraç tavanı (`leverage_cap` 5) |
| üç aşamalı çıkış yönetimi (breakeven / kısmi / geri verme) | `fee_rate`, `slippage_*`, funding, `maintenance_margin` |
| — | dolum kuralı (kural 13), likidasyon sırası, defter kuralları (kural 1/2/7) |

**Gerekçe ölçülecek şeyin kendisidir.** Aşama 2'nin iyileştirme turu `cost_per_r` ve
`avg_stop_distance_pct` üzerinden okunacak; kopya statüsünde (kural 15b) bu iki kolon
tanım gereği **`nan`** olur — 1R'si sabit teminattan türer, yarışmacılarınki sermayenin
%1'inden. Kopya olarak kurulsa model, iyileştirmenin okunacağı kolonları taşımayan bir
satır olurdu. Ayrıca yarışmacı olmak modeli kabul kapılarına (§3, §4) TABİ kılar; kopya
onlara tabi değildir ve bu tez bir kabul kararı hedefliyor.

**Bedeli vardır ve aşağıda 3(f)/3(g) olarak yazılıdır:** yarışmacı statüsü kaynağın
boyutlandırmasını ve limit bildirimini KOPYALAMAYI yasaklar.

### 2. Kaynaktan BİREBİR gelen kurallar (`fa888b7`)

**Zigzag eşiği (`wave_detector.atr_pct` + `main._find_wave_setup`):**

```
vol_pct        = ATR(14, BASİT ortalama) / son_kapanış × 100
effective_dev  = max(deviation_pct × vol_pct, 0.05)        # taban %0.05
pivots         = zigzag_pivots(df, deviation_pct=effective_dev)
```

ATR periyodu 14 ve yumuşatma **BASİT ortalamadır** (`true_range.rolling(14).mean()`).
Bu, projenin varsayılanıyla (`simple`) ÇAKIŞMAZ — yani karar 46'nın `ema_trend` için
verdiği Wilder tadilatı **burada UYGULANMAZ ve uygulanmasına gerek de yoktur**: SPEC
kaynağın kodudur ve kaynak zaten basit ortalama kullanıyor. Periyot da projenin tek ATR
periyoduna (`trailing.atr_period` = 14) eşittir, yani motorun stop tavanı kontrolü
(`_within_stop_band`) ile modelin ölçüsü aynı sayıyı görür (§6g'nin `xsec` için verdiği
gerekçenin kendiliğinden sağlanmış hâli).

**Dalga-3 kurulumu (`detect_wave3_setup`):**

- Aday pivotlar `pivots[:-1]`'dir — **son pivot ONAYSIZDIR ve kullanılmaz** (henüz ters
  yönde `deviation` kadar hareket görmemiştir).
- Onaylılar 3'ten azsa kurulum yoktur.
- Son üç onaylı pivot `p0, p1, p2`; desen **L-H-L → BUY**, **H-L-H → SELL**.
- `wave1_len` = `p1 − p0` (BUY) / `p0 − p1` (SELL); `≤ 0` ise kurulum yoktur.
- `retrace` = `(p1 − p2)/wave1_len` (BUY) / `(p2 − p1)/wave1_len` (SELL);
  **`0.236 ≤ retrace ≤ 0.886`** dışında kurulum yoktur.
- **`p2`, `p0`'ı AŞMAMALIDIR:** BUY'da `p2 > p0`, SELL'de `p2 < p0` şartı; ihlalde kurulum
  yoktur.

**Seviyeler (`build_signal_levels`):**

| | BUY | SELL |
|---|---|---|
| giriş | sinyal barının KAPANIŞI | sinyal barının KAPANIŞI |
| TP | `p2 + wave1_len × tp_mult` | `p2 − wave1_len × tp_mult` |
| SL | `p2 − wave1_len × 0.15` | `p2 + wave1_len × 0.15` |

**Geçerlilik kapısı (`main.try_open_position`):** `sl < giriş < tp` (BUY) /
`tp < giriş < sl` (SELL). Sağlanmazsa kurulum **atlanır**. Talep metni bu kapıyı yalnızca
BUY biçiminde yazıyordu; SELL aynası kaynakta mevcuttur ve burada ikisi de sabitlenir —
tek yönlü yazmak short kolunu sessizce kapısız bırakırdı.

**Izgara (`learner.build_wave_grid`):** `deviation_pct ∈ {0.8, 1.2, 1.8, 2.5}` ×
`tp_mult ∈ {1.272, 1.618, 2.0}` = **12 kombinasyon**. `sl_mult` **sabit 0.15**'tir
(`WAVE_SL_MULT`) ve ızgarada değildir.

**Bandit (`learner.select`):** `epsilon = 0.25`; sıra (a) o sembolde HİÇ DENENMEMİŞ
kombinasyon varsa onlardan biri, (b) `epsilon` olasılıkla ızgaradan rastgele,
(c) sömürü. İstatistik **sembol bazındadır** ve `min_symbol_samples = 3`'ten az örnekte
o kombinasyonun **tüm semboller genelindeki** ortalamasına düşülür. Ödül, gerçekleşen R
çarpanıdır.

**Durum ayrı bir dosyada TUTULMAZ.** Kaynak iki JSON tutar (`learner_state.json` +
`..._by_symbol.json`); burada posterior `trades.csv`'nin saf bir fonksiyonudur ve her
turda sıfırdan kurulur — `vwap_clone` ve `scalp_bandit` ile aynı desen ve aynı gerekçe
(ikinci bir doğruluk kaynağı, defterle senkron kalması ayrıca test edilmesi gereken bir
şey olurdu). Bu bir SAPMA DEĞİLDİR: kaynağın istatistiği de yalnızca kapanan işlemlerden
beslenir, yani iki yol aynı defterden aynı sayıyı üretir.

**Limitler (kaynağın CLI varsayılanları):** en çok 5 eşzamanlı pozisyon, aynı YÖNDE en
çok 3, açık toplam risk ≤ sermayenin %8'i. Bunların bu modelde ne kadarının geçerli
olduğu 3(g)'de yazılıdır.

**Çıkış yönetimi (kaynağın CLI varsayılanları):** breakeven `1.0R`, kısmi `1.5R`'da
`%50`, sonrasında kazancın en çok `%50`'sini geri veren takip. Repo'nun
`exit_management.*` bloğu bu dördünü **birebir** taşır; ayrı bir uygulama YAZILMAZ
(`strategies/exit_management.py`, modeller 13/14/15 ile tek kopya).

### 3. Motor gereği KOPYALANMAYAN sapmalar — listelenir, düzeltilmez

Bunlar sonuç görülmeden yazıldı ve sonuç okunurken hatırlanacak. Hiçbiri "onarılacak" bir
kusur değildir: her biri ya bir değişmez kuralın (12/13) ya da bir statü kararının
(yarışmacı olmak) zorunlu sonucudur.

**(a) Sinyal yalnızca KAPANMIŞ barla üretilir (kural 12).** Kaynak `fetch_ohlcv` ile
OLUŞMAKTA OLAN barı da alır ve `close.iloc[-1]` o barın anlık fiyatıdır; yani kaynağın
girişi bizim göremediğimiz bir fiyattır. Yön bilinmiyor ve bilinemez.

**(b) Emir sinyal barının ERTESİNDEN dolar (kural 13).** Kaynakta giriş, kurulumun
bulunduğu anın fiyatındadır. Bizde bir bar (15 dk) gecikme ve `slippage_base` vardır.
Stop/TP seviyeleri sinyal barından kurulur ve dolum fiyatına GÖRE yeniden kurulmaz — yani
gerçekleşen R:R bir bar boşluğu kadar kayabilir.

**(c) Çıkışlar 15m barın `high`/`low` değeriyle ve motorun KÖTÜMSER sırasıyla kontrol
edilir.** Kaynak her taramada son 20 adet **1 dakikalık** mumu sırayla gezer
(`try_close_position`) ve — pozisyon henüz açılmamış olsa bile — girişten ÖNCEKİ dakikalara
da bakabilir. Bizde mum içi sıra likidasyon → stop → kısmi → TP'dir ve aynı barda hem stop
hem hedef varsa **stop** gerçekleşmiş sayılır (§5e). Yön bilinmektedir: bu sapma bizi
kaynaktan **kötümser** tarafa koyar.

**(d) Kısmi kâr tam `1.5R` seviyesinden dolar.** Kaynak kısmiyi mumun UÇ fiyatından alır
(`_manage_position(pos, last_price)`, `last_price` = mumun `high`/`low`'u), yani 1.5R'nin
ötesinden. Bu sapma da bizi kötümser tarafa koyar.

**(e) Kaynaktaki ızgara dışı `sl_mult = 1.5` satırı TAŞINMAZ.** Kaynağın kendi
istatistiğinde n=2 ile duran, `WAVE_SL_MULT = 0.15`'ten önceki bir kalıntıdır; ızgarada
yoktur ve bandit onu hiç seçemez.

**(f) Boyutlandırma: sabit teminat × 10x → risk %1 + `leverage_cap` 5.** Statü
kararının (madde 1) doğrudan sonucu. Kaynağın her işlemi 5.000 USDT notional taşır ve R'si
sabit teminattan türer; bu model `boyut = (%1 × sermaye) / |giriş − stop|` ile
boyutlandırılır ve gereken notional nakdi aşarsa kaldıraç en çok 5'e kadar devreye girer,
aşarsa pozisyon KÜÇÜLTÜLÜR (kural 11, atlanmaz). Sonuç: aynı sinyal kümesinde bile
pozisyon büyüklükleri ve dolayısıyla hesap eğrisi kaynakla karşılaştırılamaz. **Ortalama R
karşılaştırılabilir kalır** — ölçümün birimi tam bu yüzden R'dir.

**(g) `ModelLimits` bu modele KAPALIDIR (kural 15b) — limitler kökten gelir.** Kapı
`core/validate.py::validate_model`tedir ve yalnızca kopyalara açıktır; yarışmacıya
açmak kural 6'yı delerdi. Sonuçlar:

| Kaynağın limiti | Bu modelde |
|---|---|
| en çok 5 pozisyon | **aynı** (kök `max_positions` = 5) |
| aynı YÖNDE en çok 3 | **short tarafta uygulanır** (kök `max_short_positions` = 3), **long tarafta UYGULANMAZ** — kök ayar yalnızca short'u kapsar, yani long kotası 5'tir |
| açık toplam risk ≤ %8 | **UYGULANMAZ** (kökte böyle bir ayar yok) — ama **YAPISAL OLARAK BAĞLAMAZ**: risk %1 × en çok 5 pozisyon = açık risk ≤ %5 < %8 |

Üçüncü satır kayda değer: sapma listelenir ama bedeli SIFIRDIR ve bu koşudan önce
hesaplanmıştır. İkinci satır ise gerçek bir sapmadır ve yönü bellidir — model long
tarafta kaynaktan daha yoğunlaşabilir. Gerçekleşen eşzamanlı pozisyon dağılımı sonuçla
birlikte raporlanacaktır.

**(h) Ev kapıları UYGULANMAZ ve bu bir sapma DEĞİL, bir tanımdır.** Scalp katmanının %1
stop tabanı, 1.5R hedef/stop kapısı ve 16 barlık zaman stop'u kaynakta yoktur; eklemek
modeli ölçülmek isteneni başka bir şeye çevirirdi (`vwap_clone`a verilen aynı gerekçe).
Model bu yüzden `ScalpModel` gövdesinden TÜREMEZ ve `strategies/time_stop.py`'yi OKUMAZ.

⚠ **(i) Stop tavanı (kural 14) UYGULANIR ve bunun bir bedeli olabilir — ÖNCEDEN yazılıyor.**
Scalp katmanının tavanı `max_stop_atr_multiple = 8.0`'dır ve motor onu merkezî olarak
uygular (`core/engine.py::_within_stop_band`): stop mesafesi 8×ATR(14)'ü aşan sinyal
**elenir**, stop tavana ÇEKİLMEZ. Dalga-3'ün stop mesafesi `|giriş − p2| + 0.15 ×
wave1_len`'dir ve `wave1_len` zigzag eşiğinin (0.8–2.5 × ATR%) bir katı olduğundan geniş
kurulumlarda tavanı aşabilir. Bu, modelin işlem sayısını düşürebilir ve **sessiz olamaz:**
elenen sinyal sayısı (`ModelReport.skipped_signals`) sonuçla birlikte raporlanır. Tavan
YÜKSELTİLMEZ — `xsec` için verilen karar (katmanın tavanını geriye dönük değiştirmek,
tamamlanmış ön-kayıtlı koşuların koşulunu değiştirmektir) burada da geçerlidir; scalp
katmanında dört model canlı koşuyor ve tavanı oynatmak onların defterini böler (karar
25'in `fee_rate` hatası).

### 4. Katman ve evren

**Katman `scalp`'tir** (15m bar, `config.yaml > layers.scalp`): `ledgers_scalp/`,
`docs/data/metrics_scalp.json`, `signals_per_bar: true`, stop tavanı 8×ATR. Model
katmanın **canlı `models` listesine EKLENMEZ** — `REGISTRY`de durur ve backtest onu
`--models` ile çağırır (`scalp_vol` ve `scalp_patient`in bugünkü statüsü; kod ölçülmeden
yarışmaz).

**Yeni bir katman AÇILMAZ ve gerekçesi `ema`/`xsec` kararlarının tersidir:** o iki model
katmanın stop tavanına ya da evrenine sığmıyordu; bu model `scalp`in barını (15m —
kaynağın profilinin ta kendisi), tavanını (8×ATR) ve evrenini zaten kullanabiliyor. Sığan
bir modele ayrı katman açmak, ölçümü bölmekten başka bir şey yapmazdı.

#### EVREN — talebin işaret ettiği blok kaynakla AYRIŞMIŞ durumda

Talep "Evren `vwap.clone.universe` (kaynağın sembolleri, TON hariç)" diyordu. **İki tanım
bugün aynı kümeyi vermiyor** ve fark ölçüme girdiği için burada sabitlenir:

| | Küme |
|---|---|
| Kaynağın `POPULAR_COINS` @ `fa888b7` (12) | BTC, ETH, SOL, XRP, BNB, DOGE, ADA, AVAX, LINK, **TON**, ETHFI, NEAR |
| `config.yaml > vwap.clone.universe` (12) | BTC, ETH, SOL, XRP, BNB, DOGE, ADA, AVAX, LINK, ETHFI, **PENGU**, NEAR |

`vwap_clone` yazıldığında kaynağın listesi PENGU taşıyordu; `fa888b7`'de taşımıyor.
`vwap.clone.universe`'ü yeniden kullanmak, kaynağın Dalga-3 profilinin **hiç taramadığı**
bir sembolde (PENGU) işlem açmak olurdu.

**SEÇİLEN ve SABİTLENEN tanım — talebin parantez içindeki DEFİNİSYONU, işaret ettiği blok
değil:** kaynağın `fa888b7` sembolleri **eksi TON** = **11 sembol**:

```
BTC, ETH, SOL, XRP, BNB, DOGE, ADA, AVAX, LINK, ETHFI, NEAR   (hepsi -USDT-SWAP)
```

TON dışlanır çünkü OKX'te kalıcı olarak yoktur (51001) ve `scalp` katmanının evreninde de
yoktur (karar 24): listede tutmak hiç taranmayan bir sembolü taranıyormuş gibi
göstermek olurdu. Küme, katmanın 13 sembollük evreninin **alt kümesidir** — yani
`core/validate.py`'nin evren kapısı sağlanır ve katmanın evreni DEĞİŞTİRİLMEZ.

Liste `config.yaml > wave.clone.universe` olarak **kendi bloğunda** durur,
`vwap.clone.universe` ile paylaşılmaz: iki blok iki farklı kaynak profilini anlatıyor ve
bugün ayrışmış oldukları ölçüldü. Paylaşılan bir liste, birinin kaynağı değiştiğinde
ötekini sessizce yanlış yapardı.

⚠ **SUI ve PENGU bu modelin evreninde YOKTUR.** Katmanda koşan öteki modeller onları
görür; bu model görmez. Bu, katman İÇİ kıyasta bir asimetridir ve **kıyas hedefi
seçilirken hatırlanacaktır** — `vwap_clone` da kendi 12'sini görüyor, yani asimetri yeni
değil ama bu modelde 11'e iniyor.

#### `config.yaml` neden değişiyor — ve neyin değişMEDİĞİ

Talebin yasak listesi "`config.yaml` değişmez" diyor. Uygulanan okuma: **ölçümün ORTAK
koşulları değişmez** — `fee_rate`, `slippage_*`, `risk_per_trade`, `leverage_cap`,
`initial_capital`, `maintenance_margin`, `random_seed`, katmanların `timeframe`/`universe`/
`models`/`max_stop_atr_multiple` değerleri: hiçbirine dokunulmaz. Eklenen tek şey modelin
**kendi** ön-kayıtlı parametre bloğudur (`wave.*`), emsali `ema_trend.*` (§6d) ve `xsec.*`
(§6g). Alternatif, sayıları koda gömmekti — CLAUDE.md'nin "hiçbir sabit hardcoded
edilmez" kuralının doğrudan ihlali ve ön-kayıtlı bir sayının grep'lenemez hâle gelmesi.
Blok hiçbir canlı modelin gördüğü hiçbir değeri değiştirmez (okunmayan anahtar zararsızdır).

Sonucu DOĞRUDAN kaydıran harness sapmaları (`--fee-rate`, `--slippage-base`, `--symbols`,
`--signal-cutoff`) yine yalnızca bayrakla uygulanır ve `manifest.json > deviations`a
yazılır (§5g). **Bu koşuda maliyet override'ı YOKTUR** (madde 6).

### 5. Pencereler — koşudan ÖNCE sabitlenir, sonuca göre KAYDIRILMAZ (§7.3)

| Dönem | Aralık | Rol |
|---|---|---|
| **A** (geliştirme) | **2025-03-01 → 2025-12-31** (sinyal kesimi) | teşhis + birincil satır; Aşama 2'nin TEK girdisi |
| **B** (hold-out) | **2026-01-01 + embargo → 2026-08-31** | **OOS — bu aşamada KOŞULMAZ** |
| — | 2026-09-01 → koşu günü | **hiçbir döneme girmez**: görülmüş veri (kaynağın defteri) |

**Dönem atama ölçütü GİRİŞ tarihidir** (§6d ile birebir): dönem A'da açılan bir pozisyon
sınırı aşsa bile kapanışına kadar A'ya sayılır. Uygulaması `--signal-cutoff`tur —
kesimden sonraki barlar pozisyon yönetimi için işlenir (stop/TP/kısmi/likidasyon/funding)
ama YENİ sinyal üretilmez. Kesimde hâlâ açık olan pozisyonların SAYISI raporlanır ve
kapanmış işlem istatistiğine GİRMEZ.

**Kuyruğun UZUNLUĞU: 2 ay (kesim → 2026-02-28), koşudan önce sabit.** Kuyruk zaman olarak
dönem B ile ÖRTÜŞÜR ve bu bir kirlenme DEĞİLDİR: B'yi açan şey barların işlenmesi değil,
kesimden sonra YENİ SİNYAL üretilmesidir ve kuyrukta üretilen sinyal sayısı sıfırdır.
§6d'de de A'nın kuyruğu (2024-12-30) B'nin başlangıcının (2024-07-21) ötesine uzanıyordu.
Uzunluğun gerekçesi ölçülen embargonun kırpılmamasıdır: model zaman stop'u taşımıyor
(3h), yani pozisyon ömrünün tanım gereği bir üst sınırı yok. 15 dakikalık barda 2 ay
≈ **5.760 bardır** — kaynağın geometrisinde tipik ömrün kat kat üstü. Kuyruğun ucunda
hâlâ açık kalan pozisyonların sayısı raporlanır; **sıfırdan büyükse ölçülen embargo bir
ALT SINIRDIR** ve öyle okunur.

⚠ **Görülmüş veri hiçbir bayrakla açılmaz ve ölçütü PENCERENİN UCUDUR** (kesim değil):
2026-09-01 ve sonrasındaki barların bir pozisyon yönetimi için bile işlenmesi, ölçümü
görülmüş fiyatlara bağlardı. Dönem B'yi açan `--confirm-holdout` bayrağı bu kapıyı
AÇMAZ ve `.github/workflows/backtest-wave.yml` o bayrağı girdi olarak hiç sunmaz.

**Ne A ne B kaynağın kendi öğrenme penceresidir.** Kaynağın banditi 2026 Eylül'ünde canlı
öğrendi; ızgara, `epsilon` ve `sl_mult` ise koddaki sabitlerdir ve bizim verimizle
seçilmedi. Yani A da B de bizim için kontaminasyon taşımaz — **A'nın "geliştirme" olması
bir veri etiketi değil, bir KULLANIM taahhüdüdür:** teşhislerine bakacağız, bu yüzden
Aşama 2'nin varyantı için A artık IN-SAMPLE sayılır (§6'ya satır eklendi).

#### Embargo — VARSAYILMAZ, dönem A'dan ÖLÇÜLÜR

Model **zaman stop'u taşımaz** (madde 3h), yani §6.1'in dayandığı üst sınır ("hiçbir
pozisyon `time_stop_bars`tan uzun yaşamaz") tanım gereği yoktur. Yöntem §6d/§6g'nin
aynısı: dönem A'da gözlenen **azami tutuş süresi** embargo olarak uygulanır ve uygulanan
değer `manifest.json > window.embargo_bars`ta raporlanır. **A koşulup embargo
ölçülmeden B başlatılamaz** — bu, Aşama 1'in Aşama 2'den ayrı olmasının teknik sebebidir.

#### VERİ KAPSAMI — koşudan ÖNCE ölçülür ve bir KAPIDIR (§6d emsali)

15m barda dönem A ≈ **29.400 bar/sembol**, dönem B ≈ **23.300 bar/sembol**. Bu, katmanın
canlı derinliğinin (`layers.scalp.data.history_bars` = 1500) yaklaşık 20 katıdır ve OKX'in
15m geçmişinin o kadar geriye gittiği **VARSAYILMAZ**. Ana koşudan önce ayrı bir kapsam
adımı koşar ve şunları tabloya yazar:

⚠ **BİZİM ÇEKME YOLUMUZUN derinliği ayrı bir sayıdır ve pencereden BÜYÜKTÜR.**
`core/data.py::_download_candles` barları ŞU ANDAN geriye doğru sayfalar ve
`data.history_bars` kadar bar toplayınca durur — pencerenin başına ATLAMAZ. Yani derinlik,
koşu gününden dönem A'nın başına kadarki TÜM mesafeyi kapsamalıdır:

> 2026-09 → 2025-03-01 ≈ **570 gün × 96 bar/gün ≈ 54.720 bar**, artı zigzag penceresi
> (300) ve ATR ısınması (14).

Harness'ın varsayılanı bu yüzden **60.000**'dir (emsali `backtest_ema`nın 4H'deki
12.000'i). Bu ayrım yazılı durmak zorunda, çünkü yetersiz bir derinlikte kapsam kapısı
düşer ve teşhis YANILTICI olur: rapor "OKX veriyi vermiyor" derken aslında *"biz o kadar
geriye istemedik"* demiş olurdu. Kapı düştüğünde bakılacak İLK şey bu sayıdır; ikinci şey
borsanın kendi derinliğidir (karar 50'nin fonlama uç noktasında ölçtüğü şeyin mum
tarafındaki karşılığı).

- her sembolün **ilk 15m barı** (OKX'te fiilen ulaşılan en eski kapanmış bar);
- dönem A ve B için **beklenen ↔ gerçekleşen bar sayısı** ve **eksik bar** sayısı;
- `missing_bars` ve `unchecked_position_bars` (B-2).

**Geç listelenen sembol kendi başlangıç tarihinden girer** — `core/data.py` `as_of` barını
taşımayan sembolü zaten o tur dışlar (kural 12), yani bu bir sessiz kayıp değil loglanan
bir kapsam sınırıdır.

⚠ **Kapsam dönem A için yetersizse pencere KAYDIRILMAZ.** Koşu durur ve karar kullanıcıya
gider. "Yetersiz" ölçütü koşudan önce sabitlenir: **herhangi bir sembolde dönem A'nın
başlangıcına ulaşılamıyorsa** ya da **B-2 sıfırdan büyükse**. Pencereyi veri kapsamına
göre kaydırmak §7.3'ün yasağının tam olarak kendisidir; durmak değildir.

### 6. Maliyet — canlı config, DEĞİŞİKLİK YOK

`fee_rate = 0.00055` (tek yön, iki bacak taker), `slippage_base = 0.0005`,
`slippage_short_stop = 0.0015`. **Harness maliyet override'ı KULLANILMAZ** —
`ema_trend`in koşusundan (§6d) ayrıştığı nokta budur ve bilinçlidir: orada model sahibi
TradingView paritesi için farklı bir maliyet talep ediyordu; burada böyle bir talep yok ve
kaynağın kendi defteri zaten komisyonsuz tutuluyor, yani parite kurulacak bir sayı yoktur.
Bu koşu **canlının ödediği maliyeti** ölçer.

Funding: `data.funding_history_periods = 180` (≈60 gün) dönem A'yı kapsamaz; kaydı olmayan
anda `core/funding.py::rate_at` **None** döner ve maliyet işlenmez (uydurma yok). Bu,
A ve B'yi **İYİMSER** yapar ve §6d'nin 3. sapmasının bu koşudaki hâlidir. Harness
`--funding-periods` ile derinliği artırır (sınıf-1 bayrak: yalnızca derinleştirir), ama
OKX'in kendi tavanı ~3 aylık KAYAN bir penceredir (karar 50, `probe_funding_depth.py`
ölçtü) — yani derinleştirme bu pencerede işe yaramayacaktır ve **fonlama maliyetinin
büyük kısmı işlenmeyecektir.** Gerçekleşen kapsam (funding kaydı bulunan pozisyon-bar
oranı) sonuçla birlikte raporlanır.

### 7. Kapılar

**Repo kapıları birincildir ve canlıyla birebir aynı hesaptan okunur**
(`core/metrics.py::acceptance_flags`): §3'ün **B-0/B-1/B-2**'si ve §4'ün **C-1..C-5**'i.

⚠ **C-3 scalp katmanında DEĞERLENDİRİLEMEZ ve geçilmiş SAYILMAZ** (§4'ün kendi notu):
katmanda referans çıpası yoktur — `vwap_clone` bir kopyadır, çıpa değil (kural 15b).
`acceptance_flags` bunu `logger.warning` ile söyler. Scalp'e çıpa eklemek AYRI bir
karardır ve bu ön-kayıt onu varsaymaz. **Sonuç: `ema_trend`/`xsec_mom`daki "yalnızca
C-3'ten kalırsa DUR" istisnası bu modelde TETİKLENEMEZ** — eksik bir çıta, geçilmiş çıta
gibi kullanılamaz, dolayısıyla istisnanın dayandığı "diğer tüm kapıları geçiyor" hâli
burada kurulamaz.

**Kontrol (C-2) `random_ctrl`dür ve katmanda YOKTUR.** `layers.scalp.models` bugün
`scalp_fixed, scalp_patient, vwap_clone, vwap_managed` içeriyor; `acceptance.control_model`
kökte `random_ctrl`dür. Kontrol kümede yoksa C-2 **değerlendirilemez** ve geçilmiş
sayılmaz (Kabul Çıtası'nın yazılı kuralı). **Bu ön-kayıt kontrolü katmana EKLEMEZ** ve
gerekçesi §7.1'dir: yeni bir model eklemek katmanın bugünkü koşullarını değiştirir ve
canlı koşan dört modelin kıyas zemini olur. Sonuç dürüstçe yazılır: **bu koşuda C-2 ve
C-3 değerlendirilemez, yani C-5 de tam anlamıyla kurulamaz.**

> **Bunun anlamı açıkça yazılıyor: Aşama 1'in kapı çıktısı en iyi hâlde "C-1 ve C-4
> sağlandı, C-2/C-3 değerlendirilemedi"dir. Bu, canlıya alma için YETERLİ DEĞİLDİR.**

**Aşama 1 canlıya alma kararı VERMEZ.** C-5 yalnızca dönem B'de okunabilir ve dönem B bu
aşamada kapalıdır.

**Model sahibinin kapısı (K-2 analogu) — P4:** dönem A'da R'ye giren kapanmış pozisyon
**≥ 300**. Bu, repo'nun B-1'inden (30) **ON KAT sıkıdır** ve talep onu bir tahmin
tablosunda vermişti; burada statüsü netleştirilir: **hem ön-kayıtlı bir tahmin hem
bağlayıcı bir okuma kapısıdır** (`ema_trend`in K-2'siyle aynı rol, §6d). `n < 300` ise
dönem A satırı **okunmaz** ve Aşama 2 teşhislerden varyant TÜRETMEZ. Eşik sonuca göre
İNDİRİLMEZ (§6g > TADİLAT-1'in "kapıyı kaldırmak ilkelidir, düşürmek değil" kuralı: bir
gerekçe bulunursa kaldırılabilir, beklenen sayıya bakarak düşürülemez).

**K-1 (coin başına PF) ve K-3 (max drawdown %25) bu koşuda BAĞLAYICI DEĞİLDİR ve
raporlanır.** Gerekçe: ikisi de `ema_trend`in kapı setinden gelir ve o modelin sahibi
tarafından o modelin tek-sembollü TradingView koşusu için tanımlandı; burada böyle bir dış
referans koşusu yoktur. Coin başına koşu yine yapılır (madde 9) ve PF ile drawdown
**bilgi olarak** yazılır — eşik uydurmak, kapıyı ölçüme bakarak tasarlamak olurdu.

### 8. ÖN-KAYITLI TAHMİNLER (sonucu görmeden)

| # | Ölçüm | Tahmin | Çürütür |
|---|---|---|---|
| **P1** | dönem A **net** ort. R | **≤ 0** | > 0 |
| **P2** | dönem A `cost_per_r` **medyanı** | **≥ 0.08** | < 0.08 |
| **P3** | dönem A **brüt** ort. R | **< +0.15** | ≥ +0.15 |
| **P4** | örneklem | **n ≥ 300** | n < 300 → dönem A satırı OKUNMAZ (madde 7) |

**P1 ve P2 kaynağın GÖRÜLMÜŞ defterinden türetildi** (09.09–21.09.2026: net −0.044,
`cost_per_r` medyanı 0.081) ve bu **açıkça etiketlenir**: tutmaları bir sürpriz değildir
ve bir doğrulama olarak okunmayacaktır. **Tutmamaları ise bilgidir** — o zaman kaynağın
iki haftalık penceresi ile 10 aylık bir pencere ayrışmış demektir ve ayrışmanın yönü
raporlanır.

⚠ **P2'nin birimi MEDYANDIR, ortalama değil, ve bu koşudan önce seçildi:** `cost_per_r`
dağılımı sağa çarpıktır (dar stop kuran tek bir kurulum ortalamayı tek başına taşır) ve
ortalama, ölçülmek istenen "tipik friksiyon"u anlatmaz. `core/metrics.py` model/yön bazında
ORTALAMA raporlar; medyan **pozisyon bazlı `cost_per_r` değerlerinden** okunacak ve
kolonun ortalaması da yanında yazılacaktır — iki sayı arasındaki fark çarpıklığın ölçüsüdür.

**P3, P1'den bağımsız bir eksendir:** P1 "para kazanıyor mu", P3 "sinyalin taşıdığı brüt
sapma ne kadar" diye sorar. İkisi birlikte Aşama 2'nin tek anlamlı sorusunu kurar —
*friksiyon mu yiyor, yoksa sinyalde sapma mı yok?* P1 düşüp P3 tutarsa (net ≤ 0, brüt
< 0.15) cevap "sapma yok"tur ve maliyet ekseninde iyileştirme aramak boşunadır; P1 düşüp
P3 çürürse (brüt ≥ 0.15) cevap "friksiyon yiyor"dur ve stop mesafesi ekseni açılır.

**Sicil:** bu hipotez §6c'nin **5. satırıdır** ve satır bu commit'te, koşudan ÖNCE
açılmıştır.

### 9. Koşu kuralları (Aşama 1)

- **Parametre araması YOK.** Izgara (12 kombinasyon), `sl_mult` (0.15), retrace aralığı
  (0.236–0.886), eşik tabanı (%0.05), `epsilon` (0.25), `min_symbol_samples` (3), çıkış
  yönetimi dörtlüsü ve evren (11 sembol) bu commit'te sabittir.
- **Portföy koşusu = BİRİNCİL satır.** Kota (`max_positions` 5, `max_short_positions` 3)
  bağlar ve kabul çıtası ondan okunur — canlı onu yapacak.
- **Coin başına koşu = BİLGİ.** Kota bağlamaz; K-1/K-3 analogları buradan yazılır ama
  bağlayıcı değildir (madde 7). İki koşu sınıfı AYRI durur ve toplanmaz.
- **Tek tohum.** `random_seed = 20240217`. Bandit'in keşif dalı ve "denenmemiş kombinasyon"
  seçimi bu tohumdan beslenir; koşu tek seferliktir ve **farklı tohumla yeniden
  koşulmaz.** Gerekçe §6g'nin `xsec_random` için yazdığıyla birebir aynıdır: tohum serbest
  bırakılsaydı "sonuç iyi çıkana kadar yeniden çek" mümkün olurdu.
- **Tek workflow tetiklemesi, tek sonuç dosyası.** Cron YOKTUR (§7).
- **Kapı 0 (§1) zorunludur ve İKİ parçalıdır:** (i) harness'ın canlı `emitted`
  kayıtlarıyla karşılaştırması — bu model canlıda koşmadığı için BOŞTUR ve kapı
  sayılmaz; (ii) yerine geçen **PARİTE TESTİ**: kaynağın `zigzag_pivots` +
  `detect_wave3_setup` + `build_signal_levels` çıktısı ile bu repo'daki modülün çıktısı,
  sabitlenmiş OHLC dizilerinde **birebir** aynı olmalıdır (pivot listesi, yön, seviyeler).
  **Parite düşerse hiçbir sayı okunmaz** ve koşu hata koduyla biter (§1'in aynı mantığı).
- **Kapıdan kalırsa ayar aranmaz.** Düşen tez sicilde kalır (§6c).
- **Koşu sonrası ZORUNLU raporlama** (madde 10'un teşhisleri + madde 3'ün sayaçları):
  `skipped_signals` (stop tavanı), `duplicate_position` ve boyutlandırma retleri,
  kesimde açık kalan pozisyon sayısı, funding kapsamı, eşzamanlı pozisyon dağılımı.

### 10. Teşhis çıktıları (dönem A) — GÖZLEM, karar değil

Hepsi `core/metrics.py` ve `core/report.py`den okunur; **yeni metrik yazılmaz** (kural 7).
Bu sayılar sicile GİRMEZ ve bir kapı değildir — Aşama 2'nin tek girdisidir.

1. **Çıkış sebebi dağılımı** (`exit_rule` kırılımı: `tp` / `stop` / `stop:breakeven` /
   `partial` / `stop:giveback` / `liquidation`) ve her grubun ort. R'si. ⚠ Birimi
   **DİLİMDİR**, pozisyon değil (CLAUDE.md): kısmi çıkışlı pozisyon iki gruba düşer ve
   grupların `trades` toplamı model tablosundan büyük olur. Bu, rapor başlığına yazılır.
2. **Stop mesafesi % dağılımı** (p5/p25/p50/p75/p95) ve stop mesafesi KOVASINA göre
   `cost_per_r` ile net R. Karar 35'in özdeşliği (`net R = (brüt sürüklenme% −
   maliyet%) / stop%`) tam olarak burada okunur.
3. **Tutuş süresi dağılımı** (medyan / p90 / **azami**). Azami değer **embargodur**
   (madde 5).
4. **Kombinasyon başına n ve net ort. R.** ⚠ Bunun **12 hücreli bir ızgara** olduğu ve
   **BH düzeltmesi yapılmadan okunamayacağı** raporun BAŞINA yazılır: 12 hücrenin en
   iyisine bakıp "şu kombinasyon çalışıyor" demek, sicilin (§6c) engellemek için var
   olduğu şeyin hücre düzeyindeki hâlidir. Bandit zaten tahsis ediyor; ızgara tablosu
   tahsisin NE gördüğünü anlatır, bir seçim önerisi DEĞİLDİR.
5. **Giriş anında fiili R:R** (`|TP − giriş| / |giriş − stop|`) dağılımı. Kaynakta
   dayatılmış bir R:R kapısı yoktur (ev kapıları uygulanmıyor, madde 3h); bu dağılım
   geometrinin gerçekte ne ürettiğini gösterir.
   **Hedef fiyatı `reason` kuyruğuna `target=` ETİKETİYLE yazılır** ve oran oradan
   okunur: `trades.csv`de bir TP kolonu YOKTUR ve yeni kolon açılamaz (kural 13c —
   başlık değişirse eski satırlar okunamaz hâle gelir). Payda defterin `stop_price`
   kolonudur, yani İLK stop ve R'nin paydasıyla aynı; giriş ise DOLUM fiyatıdır, sinyal
   barının kapanışı değil — "fiili" tam olarak budur (kural 13: emir bir sonraki barın
   açılışından dolar ve oran o boşluk kadar kayar).
6. **Yön (long/short) ve sembol kırılımı.** Projenin ana sorusu long/short ayrımıdır;
   sembol kırılımı ise kayma varsayımının ince kitapta tutup tutmadığına dair İPUÇTUR,
   kanıt değil (§8).

### 11. İYİLEŞTİRME TURUNUN KURALLARI — Aşama 2, teşhis GÖRÜLMEDEN sabitlendi

Emsal §6e'dir: kural, çıktıya bakılarak yazılamaz. Aşama 2 aşağıdakilere bağlı kalır.

- **Değiştirilmiş sürüm YALNIZCA dönem A teşhislerinden türetilir.** Dönem B'nin hiçbir
  sayısı — tek bir işlem, tek bir ortalama — görülmeden tasarlanır.
- **TEK BİRİNCİL VARYANT seçilir.** Değerlendirilen ama seçilmeyen her aday sicile
  "değerlendirildi, seçilmedi" olarak, gerekçesiyle yazılır. İki varyant koşmak BH
  paydasını büyütür ve §6e'nin ölçtüğü güç sorununu iki katına çıkarır.
- **Dönem B'de sadık sürüm ve varyant AYNI koşuda, BİR KEZ çalışır.** Kıyas pozisyon
  başına EŞLEŞTİRİLMEZ — iki sürüm farklı işlemler üretir (§6e'nin eşleştirdiği durum
  burada YOKTUR: orada çıkış varyantları aynı girişleri paylaşıyordu) — ve iki BAĞIMSIZ
  satır olarak okunur; fark `bootstrap_diff_ci` ile kurulur.
- **B'de düşen varyant için İKİNCİ DENEME YOKTUR.** Yeni tez yeni pencere ister (§7.1).
- **Sadık sürüm dönem B'de de koşar ve satırı yayınlanır** — varyantın tek başına
  okunması, iyileştirmenin neye göre iyileştirme olduğunu belirsiz bırakırdı.

### 12. Bu ön-kayıt neyi SEÇMİYOR

Canlıya alınmayı. Model katmanın `models` listesinde YOKTUR ve kapılar geçilse bile
eklenmesi AYRI bir karardır (`scalp_patient`in bugünkü statüsü). Dönem B'yi de seçmiyor:
B, Aşama 2'nin ön-kaydıyla birlikte tek seferde açılacaktır.

Ayrıca **`scalp` katmanına kontrol (`random_ctrl`) ya da çıpa (`buyhold`) eklenmesini
seçmiyor.** İkisinin yokluğu C-2 ve C-3'ü değerlendirilemez bırakıyor (madde 7) ve bu,
bu modelin değil KATMANIN bir eksiğidir; onarımı katmanda koşan dört modelin koşullarını
değiştirir, yani kendi kararını ve kendi ön-kaydını ister.

### 13. Çoklu karşılaştırma

Bu, sicilin (**§6c**) **5.** satırıdır ve §6c'deki "ev içi araştırmadan çıkan 10 öneri"
paydasına **AİT DEĞİLDİR** — `ema_trend` satırıyla (2. satır) aynı gerekçe: hipotez dış
bir sistemden geldi, ev içi arama uzayından seçilmedi. İki payda ayrı tutulur; aksi hâlde
dışarıdan gelen her hipotez ev içi aramanın cezasını ödemiş gibi görünürdü. Bu satır
**dış kökenli paydanın 2. üyesidir** (1. üye `ema_trend`).

Aşama 2'nin varyantı ise **ev içi bir tez olacaktır** (teşhisten türetilir) ve o zaman ev
içi paydaya girer. Bu ayrım şimdi, varyant tanımlanmadan yazılıyor.

### EK-1 — kontrol modeli `wave_coinflip` *(2026-09-22)*

**Bu ek §6h'yi DEĞİŞTİRMEZ.** Yukarıdaki hiçbir satır, hiçbir eşik ve hiçbir tahmin
dokunulmadı; ek yalnızca eksik bir kapıyı (C-2) ölçülebilir hâle getirir. Talep bu eki
"§6g'nin altına" diyordu; §6g `xsec_mom`un ön-kaydıdır ve wave ön-kaydı §6h'dir (bkz.
§6h > NUMARALANDIRMA DÜZELTMESİ), bu yüzden ek buraya konuldu.

#### ZAMANLAMA — talebin cümlesi DÜZELTİLDİ, çünkü doğru değildi

Talep şu cümlenin yazılmasını istiyordu: *"Bu ek, dönem A'nın `wave_scalp` sonuçları
görüldükten SONRA yazıldı."* **O cümle yazılmadı, çünkü yanlış olurdu.** Gerçek durum:

> **Dönem A KOŞULMADI.** Bu ek, dönem A'nın `wave_scalp` sonuçları görülmeden yazıldı ve
> commit edildi. Görülen tek şey, kapsam kapısını sınamak için koşulan **BİR HAFTALIK bir
> probe**dur (aşağıda tek tek yazılı). Kontrolün ayarlanabilir parametresi yoktur ve C-2
> marjı §4'te önceden sabittir. Bu ekteki tek serbest seçim **S1 toleransıdır** ve kontrol
> koşusundan önce sabitlenmiştir.

Ön-kayıt belgesine, doğru olmayan bir zamanlama beyanı yazmak §7'nin tamamını anlamsız
kılar: belgenin kanıt değeri tarih damgasının DOĞRU olmasına dayanır. Ek, istenen cümleden
daha GÜÇLÜ bir konumda duruyor — kontrol, sonuç görülmeden tanımlandı.

#### GÖRÜLEN VERİ — bir haftalık probe, tek tek yazılı

Kapsam kapısı (§6h > 5) ana koşudan önce cevaplanmak zorundaydı ve cevabı ancak veriye
erişen bir koşu verebiliyordu. Koşu: **`backtest` #35701959605**, commit `494e3ea`,
2026-09-22, pencere **2025-03-01 → 2025-03-08** (dönem A'nın ilk haftası), `wave_scalp`
tek başına, canlı maliyet.

**Cevap: OKX 15m geçmişi dönem A'nın başına ULAŞIYOR** — log 2025-03-07T21:15 ve 21:30
barlarını işledi. Kapsam kapısının veri tarafı GEÇTİ.

Aynı koşu kaçınılmaz olarak birkaç sayı da gösterdi ve **gizlenmeleri söz konusu değil**
(karar 42'nin çerçevesi: görülen veri görülmüş sayılır):

| Ölçüm | Bir haftalık probe |
|---|---|
| kapanmış pozisyon | 96 |
| ort. R | −0.37 |
| çıkış dağılımı (dilim) | tp 41, stop 40, partial 13, stop:breakeven 8, stop:partial 6, **likidasyon 1** |
| tutuş süresi (bar) | medyan 9, p90 57, azami 140 |
| en kötü sembol | ADA: n=6, ort. R −3.07, ort. kayıp −10.19R |

⚠ **Bu sayılar dönem A'nın sonucu DEĞİLDİR ve hiçbir karara girmezler:** bir hafta,
ön-kayıtlı pencerenin ~%2'sidir ve P4'ün (n ≥ 300) örneklem kapısının çok altındadır.
Buraya yazılmalarının sebebi tersidir — dönem A koşulduğunda, "hiçbir şey görülmemişti"
denemesin. §7.3'ün yasakladığı şey pencereyi sonuca göre KAYDIRMAKTIR; pencere
kaydırılmadı ve kaydırılmayacak.

⚠ **Likidasyon satırı (1 dilim, −20.30R) bir UYARIDIR, bir sonuç değil.** Kaynağın
geometrisi stop'u `p2 ∓ dalga1 × 0.15`e koyuyor ve bu mesafe bazen çok dar çıkıyor; ev
boyutlandırması (`risk / |giriş − stop|`) o durumda notional'ı büyütür, `leverage_cap` (5)
onu kırpar ama likidasyon yine mümkün kalır (kural 15b'nin "likidasyon kapatılmaz"
gerekçesiyle aynı yer). **Bu gözlem tasarımı DEĞİŞTİRMEZ** (§7.1) ve kontrolü de
etkilemez: `wave_coinflip` aynı stop geometrisini taşır, yani etki iki tarafta da vardır
ve C-2 farkından DÜŞER.

#### Neden bu ek gerekiyor

Scalp katmanında **kontrol modeli yoktur** (`config.yaml > layers.scalp.models`:
`scalp_fixed`, `scalp_patient`, `vwap_clone`, `vwap_managed`). `core/metrics.py::
acceptance_flags` kontrolü kümede bulamayınca ilgili koşulu düşürür, yani `edge` bayrağı
fiilen `avg_r > 0`'a iner. §6h > 7 bunu zaten dürüstçe yazıyordu ("C-2 ve C-3
değerlendirilemez"); bu ek, C-2'yi **ölçülebilir** hâle getirir. C-3 hâlâ
değerlendirilemez ve bu ek onu AÇMAZ (çıpa eklemek ayrı bir karardır).

**`scalp_coinflip` bu modelin kontrolü OLAMAZ** ve gerekçesi kural 14'ün kendisidir: o
model scalp kollarının stop geometrisini (`stop_atr_multiple` 5.0 × ATR, %1 taban)
taşıyacak, `wave_scalp` ise `p2 ∓ dalga1 × 0.15` taşıyor. İki farklı stop ölçeği demek,
⚠B bandının yanması ve `cost_per_r`nin kıyaslanamaz olması demektir — yani fark
"seçimin ölçüsü" olmaktan çıkar, "iki maliyet ölçeğinin farkı" olur (CLAUDE.md > Rapor
Kolonları'nın tam olarak reddettiği kıyas). Bugün `scalp_coinflip` depoda YOKTUR; bu satır
o model geldiğinde yanlış kontrolün seçilmesini engellemek için şimdi yazılıyor.

#### Model: `wave_coinflip` (`strategies/wave_coinflip.py`)

`wave_scalp`ten TÜRER. Zigzag eşiği, dalga-3 kuralı, seviye geometrisi, 12 hücreli bandit
ızgarası, epsilon-greedy seçim sırası, üç aşamalı çıkış yönetimi, evren ve limitler
**birebir aynıdır** — miras alınır, kopyalanmaz (`scalp_patient`in `ScalpFixed`ten
türemesiyle aynı desen ve aynı gerekçe: kopyalanan bir kural bir gün sessizce ayrışır ve
fark "seçimin ölçüsü" olmaktan çıkar).

**Ayrışan TEK şey yöndür:** kurulumun yönü **adil bir yazı-tura** ile belirlenir.

- "Aynı" gelirse sinyal `wave_scalp`in ürettiğinin birebir aynısıdır.
- "Ters" gelirse yön çevrilir ve **stop ile hedef MESAFELERİ girişin öbür tarafına aynen
  yansıtılır:**

  ```
  stop_ters   = giriş + (giriş − stop_özgün)
  hedef_ters  = giriş + (giriş − hedef_özgün)
  ```

  Böylece `|giriş − stop|` ve `|hedef − giriş|` KORUNUR, `sl < giriş < tp` yapısı yönün
  gerektirdiği tarafa geçer. Mesafeleri korumak S1'in ön koşuludur: yansıtma mesafeyi
  değiştirseydi kontrol başka bir maliyet ölçeğinde koşar ve ⚠B yanardı — yani tam olarak
  `scalp_coinflip`i reddetme gerekçemize kendimiz düşerdik.

**Yazı-tura AYRI bir RNG akışındandır:** `random.Random(f"{random_seed}:{as_of}:
wave_coinflip:{symbol}")`. Bandit'in ε çekilişi kendi akışında kalır
(`{random_seed}:{as_of}:{model_adı}`) ve yazı-tura ona DOKUNMAZ. Paylaşılan tek bir akış,
yazı-turanın ε dizisini kaydırması ve kontrolün kombinasyon seçimlerinin yön çekilişine
bağlanması demekti — o zaman ölçülen şey "yönün katkısı" olmaktan çıkardı.

**Bandit kontrolün KENDİ kapanmış işlemlerinden öğrenir** (kural 16) ve bu, modelin
yapısının parçasıdır — kapatılmaz. Bedeli önceden yazılıyor: iki modelin kombinasyon
seçimleri zamanla AYRIŞIR, çünkü posteriorları farklı defterlerden beslenir. Bu
beklenen bir durumdur, raporlanır ve S1 toleransının (aşağıda) gerekçesinin bir
parçasıdır. Alternatif — kontrolün `wave_scalp`in posteriorunu okuması — kural 4'ü
(izolasyon) delerdi.

**Denetim izi:** `reason` kuyruğuna `coin=same|flipped` eklenir. Yazı-turanın gerçekten
adil olduğu (S2) yalnızca bu etiketten okunabilir.

**Statü:** yarışmacı (`is_replica=False`, `is_benchmark=False`) — `wave_scalp` ile aynı
sütunda, aynı boyutlandırmayla ve aynı maliyetle koşar; kabul çıtasının kontrolü olmasının
şartı budur (`random_ctrl`ün `is_benchmark` olmamasıyla aynı gerekçe). `REGISTRY`de durur,
**hiçbir katmanın `models` listesinde YOKTUR.**

#### Kontrol bağlantısı — AÇIKÇA verilir, katmanın varsayılanı kullanılmaz

`scripts/backtest_wave.py` kontrolü `control_model="wave_coinflip"` olarak açıkça geçirir
ve değer `manifest.json > deviations.control_model`a yazılır. Katmanın kök varsayılanı
(`acceptance.control_model` = `random_ctrl`) bu koşuda KULLANILMAZ ve bu bir sessiz
tercih değil, manifest'te görünen bir karardır.

Gerekçe ileriye dönüktür: scalp katmanı bir gün `scalp_coinflip` alırsa katmanın
varsayılanı ona kayabilir ve o, wave için YANLIŞ kontroldür (yukarısı). Varsayılana
güvenmek, doğru kontrolün bir başka modelin eklenmesiyle sessizce değişebilmesi demekti.

#### Ölçümler — hipotez DEĞİL, BH paydasına (§6c) GİRMEZ

Sicile yeni satır açılmaz: bu ek bir modelin performansı hakkında yeni bir iddia
taşımıyor, mevcut bir kapının (C-2) ölçülebilmesini sağlıyor. §6c'nin 5. satırı
(`wave_scalp`) olduğu gibi kalır.

| # | Ölçüm | Tahmin / kural |
|---|---|---|
| **S1** | `avg_stop_distance_pct` farkı, `wave_scalp` ↔ `wave_coinflip` | **< %10 bağıl.** Aşarsa **C-2 OKUNMAZ** ve bu sonuca yazılır |
| **S2** | kontrolün "ters" oranı (`coin=flipped` payı) | **0.5 ± 0.05** |
| **M1** | kontrolün dönem A ort. R'si | %95 CI, **−`cost_per_r`'yi KAPSAR** (bilgisiz yön ≈ −maliyet) |
| **C-2** | `wave_scalp.avg_r − wave_coinflip.avg_r` | §4'teki hâliyle: **≥ 0.15R** (artı bootstrap CI alt sınırı > 0) |

**S1 toleransı %10'dur ve bu sayı koşudan ÖNCE sabittir.** Scalp raporundaki %1 DEĞİL,
çünkü wave'de iki model aynı kurulumları GARANTİ ETMEZ: (a) bandit posteriorları ayrı
defterlerden beslenir ve kombinasyon seçimleri ayrışır, (b) dolumlar ayrışır ve
`max_positions` doluluğu zamanla farklılaşır, (c) `max_short_positions` (3) YÖNLE
etkileşir — yazı-tura short üretince kota bağlar ve kontrol o kurulumu hiç açmaz. Üçü de
mesafe dağılımını kaydırabilir. **Tolerans sonuca göre GEVŞETİLMEYECEKTİR** (§7.1); S1
aşılırsa C-2 okunmaz ve sebebi yazılır.

> ⚠ **DİPNOT (2026-09-22):** yukarıdaki *"Scalp raporundaki %1 DEĞİL"* cümlesi ARTIK
> GEÇERSİZDİR — scalp'in uygulanan toleransı da %10'dur (§6i > 7 > DÜZELTME-1). EK-1'in
> kendi sayısı, gerekçesi ve kapsamı DEĞİŞMEDİ; değişen, kıyaslandığı öteki sayıdır.
> Cümle silinmiyor: düzeltilen şey bir kaydın kendisi değil, o kaydın başka bir belgeye
> yaptığı atıftır. ⚠ Ayrıca bir kayıt: yukarıdaki **(b) maddesi** (`max_positions`
> doluluğunun zamanla farklılaşması) burada koşudan önce yazılmıştı ve §6i onu
> DEVRALMADI — DÜZELTME-1'in kapattığı boşluk tam olarak budur.

**M1 bir KAPI değil, bir tutarlılık kontrolüdür.** Bilgisiz bir yön seçiminin beklenen
değeri sıfırdır ve gerçekleşen R, friksiyon kadar altındadır; CI bunu kapsamıyorsa ölçülen
şey yönün bilgisizliği değil başka bir şeydir (ör. yansıtmanın mesafeyi bozması) ve önce o
araştırılır.

#### Koşu kuralları

- **Dönem A'da `wave_coinflip` koşulur:** aynı pencere, aynı `random_seed`, aynı config,
  portföy koşusu. `wave_scalp` **YENİDEN KOŞULMAZ** — mevcut sonucu kullanılır (determinizm
  `random_seed` ile garanti; aynı girdiler aynı defteri verir).
- **Dönem B'de (Aşama 3) kontrol, sadık sürüm ve varyantla AYNI koşuda çalışır.** Bu,
  §6h > 9'a eklenen bir kuraldır: üç satırın aynı barları, aynı kotayı ve aynı
  dolumları görmesi, aralarındaki farkın koşu koşullarından gelmemesinin şartıdır.
- **Kontrolün tohumu tek seferliktir** ve farklı tohumla yeniden koşulmaz (§6g >
  Kontrolün TOHUMU ile birebir aynı gerekçe: C-2 bir FARKA dayanır ve tohum serbest
  bırakılsaydı "kontrol kötü çıkana kadar yeniden çek" mümkün olurdu).

---

## 6i. ÖN-KAYIT — scalp katmanının KONTROLÜ (`scalp_coinflip`, model 23) ve `ScalpModel` taraması *(2026-09-22)*

**Bu bölüm bir HİPOTEZ ön-kaydı DEĞİLDİR ve §6c'nin siciline satır AÇMAZ.** İki iş de
ÖLÇÜMDÜR: biri hâlihazırda var olan bir kapıyı (C-2) scalp katmanında **ölçülebilir**
kılar, öteki hâlihazırda üretilen ama hiçbir yere yazılmayan bir sayımı tur raporuna
düşürür. Hiçbiri bir modelin performansı hakkında yeni bir iddia taşımıyor, yani ortada
düzeltilecek bir `p` değeri yoktur — **BH paydasına girmezler** (§6h > EK-1'in "Ölçümler —
hipotez DEĞİL" başlığıyla birebir aynı gerekçe).

Ön-kayıt yine de **koşudan ÖNCE** yazılıyor ve ayrı bir commit'tedir. Gerekçe §7.4'ün
kendisidir: ölçümlerin tahminleri (M1, M2, S1) sonucu gördükten sonra yazılsaydı,
"zaten beklediğimiz buydu" cümlesi denetlenemez olurdu — bir ölçümün tahmini de bir
tahmindir.

### 1. Sorun — scalp katmanında `edge` fiilen `avg_r > 0`'a iniyor

`config.yaml > layers.scalp.models` bugün şudur:

```yaml
models: [scalp_fixed, scalp_patient, vwap_clone, vwap_managed]
```

Bu listede **kontrol modeli yoktur** (`random_ctrl` base katmanındadır) ve **çıpa da
yoktur** (`vwap_clone` bir KOPYADIR, çıpa değil — kural 15b). `core/metrics.py::
acceptance_flags` kontrolü kümede bulamayınca marj, bootstrap güven aralığı ve çıpa
koşullarını DÜŞÜRÜR (`_control_avg_r` / `_benchmark_return`; ikisi de `logger.warning`
yazar). Geriye kalan tek koşul `avg_r > 0`'dır.

Bunun pratikteki sonucu şudur: **`scalp_patient` örneklem kapısını (n = 30) geçtiği anda
`passed: true` görünecektir** — oysa §4'ün çıtası C-1'den ibaret değildir. Rozet
"doğrulandı" derken ölçülmüş olan yalnızca "ortalamanın işareti pozitif" olurdu.

§6h > 7 bu boşluğu `wave_scalp` için zaten dürüstçe yazıyordu ("C-2 ve C-3
değerlendirilemez"); bu bölüm C-2'yi katmanın **kendi kadrosu** için ölçülebilir kılar.
**C-3 hâlâ değerlendirilemez ve bu bölüm onu AÇMAZ** — scalp'e bir çıpa eklemek ayrı bir
karardır ve burada varsayılmaz.

### 2. Model: `scalp_coinflip` (model 23, `strategies/scalp_coinflip.py`)

**Cevapladığı soru tek:** *Kolun YÖN iddiası bilgi taşıyor mu, yoksa taşıdığı şey yalnızca
"oynanabilir bir kurulum" mu?*

`ScalpPatient`ten TÜRER. Beş kol, ev kapıları (%1 stop tabanı, 1.5R), `stop_atr_multiple`
(5.0 × ATR), 100 barlık zaman stop'u, kol seçimi (`choose_arm`) ve **çekiliş kimliği
(`rng_identity`)** birebir aynıdır — **miras alınır, kopyalanmaz** (`scalp_patient`in
`ScalpFixed`ten türemesiyle aynı desen ve aynı gerekçe: kopyalanan bir kural bir gün
sessizce ayrışır ve fark "yönün ölçüsü" olmaktan çıkar). Yani iki model her barda **aynı
kolu ve aynı sembolü** seçer; eşleştirilmiş deneydir.

**Ayrışan TEK şey yöndür.** Kapılardan geçmiş ve SEÇİLMİŞ kurulumun yönü **adil bir
yazı-tura** ile belirlenir:

- "Aynı" gelirse sinyal `scalp_patient`in ürettiğinin birebir aynısıdır.
- "Ters" gelirse yön çevrilir ve **stop ile hedef MESAFELERİ girişin öbür tarafına aynen
  yansıtılır:**

  ```
  stop_ters   = giriş + (giriş − stop_özgün)
  hedef_ters  = giriş + (giriş − hedef_özgün)
  ```

  Böylece `|giriş − stop|` ve `|hedef − giriş|` KORUNUR ve `stop_distance_pct` ile
  `reward_risk` tanım gereği değişmez.

**Mesafeyi korumak bir tercih değil, ölçümün ŞARTIDIR.** Yansıtma mesafeyi değiştirseydi
kontrol başka bir maliyet ölçeğinde koşar, ⚠B bandı yanar ve `cost_per_r` kıyaslanamaz
olurdu — yani §6h > EK-1'in `scalp_coinflip`i *wave'in kontrolü olmaktan* reddetme
gerekçesine kendimiz düşerdik. Denetimi **S1**'dir.

⚠ **EK-1'in reddi bu modeli reddetmez, bir EŞLEŞTİRMEYİ reddeder.** EK-1 şunu yazmıştı:
`scalp_coinflip` scalp kollarının stop geometrisini (5 × ATR) taşır, `wave_scalp` ise
`p2 ∓ dalga1 × 0.15` taşır; iki farklı stop ölçeğini aynı C-2 farkında toplamak "seçimin
ölçüsü"nü "iki maliyet ölçeğinin farkı"na çevirirdi. O cümle `scalp_coinflip`i **wave'in
kontrolü** olarak reddediyordu ve o red BUGÜN DE GEÇERLİDİR — `scripts/backtest_wave.py`
kontrolü (`wave_coinflip`) açıkça geçirir ve katmanın varsayılanını KULLANMAZ, tam olarak
bu satır yazılırken öngörülen durum gerçekleştiği için. Burada eşleştirilen çift ise
`scalp_patient ↔ scalp_coinflip`tir ve ikisi AYNI stop geometrisini taşır; EK-1'in itirazı
bu çifte uygulanmaz.

**Yazı-tura AYRI bir RNG akışındandır:**

```
kol / sembol çekilişi   {random_seed}:{as_of}:{rng_identity}            ← PAYLAŞILIR
yazı-tura               {random_seed}:{as_of}:scalp_coinflip:{sembol}   ← KENDİNE AİT
```

Tek akış olsaydı her yazı-tura kol/sembol çekilişini bir adım kaydırır, model
`scalp_patient` ile aynı kurulumları seçmez ve eşleştirme bozulurdu — o zaman ölçülen şey
"yönün katkısı" olmaktan çıkar, tesadüfün ölçüsü olurdu (CLAUDE.md > *"çekiliş,
ölçülmeyen eksende PAYLAŞILIR, ölçülen eksende BAĞIMSIZDIR"*). Akış SEMBOL bazında
çatallanır: aynı barda iki sembolün yazı-turası bağımsız olmalıdır, yoksa "adil yazı-tura"
bar başına tek bir çekilişe inerdi.

**Denetim izi:** `reason` kuyruğuna `coin=same|flipped` eklenir. Yazı-turanın gerçekten
adil olduğu (S2) yalnızca bu etiketten okunabilir; yönü geometriden geri hesaplamak aynı
cevabı verir ama ikinci bir doğruluk kaynağı yaratırdı.

**Statü: YARIŞMACI** (`is_benchmark = False`, `is_replica = False`) — `random_ctrl` ve
`xsec_random` ile birebir aynı gerekçe: kontrol, yarışmacılarla AYNI boyutlandırma, AYNI
maliyet ve AYNI limitlerle koşmazsa aralarındaki fark sinyalin değil koşulların ölçüsü
olur.

**Tasarımı bozulamaz** (`random_ctrl` ve `xsec_random`ın aynı sözü): buraya eklenecek her
filtre — "kötü yönü ele", "trende karşı çevirme" — kontrolü sessizce bir stratejiye
çevirir ve C-2 farkının neye karşı ölçüldüğü bilinemez hâle gelir.

### 3. Neden `random_ctrl` bu katmanın kontrolü OLAMAZ

`random_ctrl` base katmanının kontrolüdür ve scalp geometrisini HİÇ okumaz: kendi stop
kuralı vardır, `scalp.*` bloğunu (5 × ATR, %1 taban, 1.5R) görmez ve 15 dakikalık barın
ölçeğinde kurulmamıştır. Onu scalp'e eklemek tam olarak EK-1'in reddettiği kıyası
üretirdi — iki farklı stop ölçeği, yanan bir ⚠B ve kıyaslanamaz bir `cost_per_r`. Kontrol
ile ölçtüğü modelin **aynı maliyet ölçeğinde** olması C-2'nin ön koşuludur; bu yüzden
kontrol katmanın kendi gövdesinden türetildi.

### 4. Katman bağlantısı — ve kontrolün birikeceği süre

- `scalp_coinflip` katmanın `models` listesine EKLENİR. Kâğıt katmanında koşar: C-2 bir
  FARKA dayanır ve farkın öteki tarafı ancak canlı defterde birikir.
- `layers.scalp.acceptance.control_model: "scalp_coinflip"` tanımlanır. Emsal `xsec`
  katmanıdır (`control_model: "xsec_random"`); kök varsayılan (`random_ctrl`) yalnızca
  base ve ema katmanlarında kalır.
- **Kontrol kendi örneklem kapısını (`acceptance.control_min_trades` = 30) geçene kadar
  katmanda `edge` DEĞERLENDİRİLEMEZ ve `passed` FALSE kalır.** Bu bir gerileme değil,
  **beklenen ve istenen** davranıştır: `scalp_patient`in yakında göreceği `passed: true`
  yanlış olurdu. `core/metrics.py::acceptance_flags` bunu `logger.warning` ile söyler ve
  skorboard rozetin ipucunda "⚠ KONTROL HENÜZ ÖLÇÜLMEDİ: n / 30" olarak gösterir (bu
  görünüm bugün de vardır, kod değişikliği gerektirmez).
- **Bildirim kadrosu:** `run-scalp.yml`de `scalp_coinflip` SUSTURULUR (`--mute`).
  Gerekçesi `run.yml`in `random_ctrl` için yazdığının aynısıdır — bilgisiz bir çekilişin
  yönünü telefona düşürmek, susturma penceresinin engellemek için var olduğu gürültünün
  kendisi olurdu. Susturma bir BİLDİRİM ayarıdır, ölçüm değil: model koşmaya, deftere
  yazmaya ve tabloda görünmeye devam eder.

### 5. KABUL EDİLEN SAPMA — yansıtılan hedef yapısal engele DAYANMAZ

`strategies/scalp/arms.py`de hedef iki parçanın YAKIN olanıdır: projeksiyon
(`target_reward_risk × stop`) ile kolun kendi **yapısal engeli** (VWAP, Bollinger orta
bandı, aralığın ölçülü hareketi…). Yansıtılan bir kurulumda hedef, girişin öbür tarafında
aynı MESAFEDEDİR ama orada kolun tezinden gelen bir engel YOKTUR — yani kontrolün hedefi
saf bir projeksiyondur.

**Sapma düzeltilmez ve gizlenmez.** Düzeltmenin iki yolu vardı, ikisi de daha kötüdür:

1. *Ters yönde yeni bir yapısal engel hesaplamak* — o zaman kontrol "aynı kurulum, ters
   yön" olmaktan çıkar, kendi hedef kuralı olan İKİNCİ bir model olurdu ve fark artık
   yönün değil iki hedef kuralının ölçüsü olurdu.
2. *Hedefi olduğu yerde bırakmak* — `|hedef − giriş|` değişirdi, yani S1 tanım gereği
   düşer, ⚠B yanar ve C-2 okunamaz hâle gelirdi.

Sapmanın YÖNÜ de önceden yazılıyor: engelsiz bir hedef, engelli olana göre **daha az sık**
ulaşılır değildir — engel çoğu zaman hedefi YAKINLAŞTIRIR (yakın olanı alınır), yani
yansıtılmış hedef ortalamada **projeksiyona eşit ya da ondan uzaktır**. Bu, kontrolü
olduğundan **kötü** gösterme yönünde çalışır ve C-2 farkını (model − kontrol) **şişirebilir.**
Bu yüzden C-2 tek başına okunmaz: M1 aynı anda kontrolün ortalamasının ≈ −`cost_per_r`
olmasını bekler ve o beklenti tutmazsa önce bu sapma araştırılır.

### 6. `ScalpModel.take_survey` — karar 34'ün açık işi

Bugün `ScalpModel` `take_survey` uygulamıyor, yani `metrics_scalp.json > round.models[].
survey` alanı beş kollu her model için `{}`. Sonucu şudur: **`funding_spike_fade`in
katmanın tüm ömrü boyunca neden tek sinyal üretmediği hiçbir yere yazılmıyor** (karar 48
bunu açık iş olarak bırakmıştı) ve `momentum_burst`ün sebebi (karar 34: kapı aritmetiği)
yalnızca bir TÜRETMEDİR, ölçüm değil.

Sayım `rejections` / `emitted` / `proximity` ile **aynı statüde bir DENETİM İZİDİR**
(kural 15): ölçüme girmez, hangi kolun seçileceğini, hangi sembolün çekileceğini ve
sıralarını DEĞİŞTİRMEZ.

**Birim: kol × eleme sebebi.** Sebepler AYRIKTIR ve her kol için toplamları o barda
taranan sembol sayısına eşittir:

| sebep | anlamı |
|---|---|
| `kurulum_yok` | kol o sembolde hiç kurulum üretmedi (tez tutmadı ya da veri yetmedi) |
| `stop_tabani` | kurulum var, stop mesafesi %1'in altında (`scalp.min_stop_pct`) |
| `hedef_stop` | kurulum var, hedef/stop < 1.5 (`scalp.min_reward_risk`) |
| `rejim_kapisi` | ek rejim kapısı eledi (yalnızca `regime_filter` uygulayan modelde) |
| `kota` | tüm kapıları geçti ama o barda oynanmadı (barda tek sinyal) |
| `secildi` | oynanan kurulum |

**Neden `kota` tek bir sebep.** "Başka bir kol seçildi" ile "bu kolda başka bir sembol
çekildi" ayrı ayrı sayılabilirdi; ikisi de aynı şeyi söyler — kurulum oynanabilirdi ve
oynanmadı, çünkü barda tek sinyal oynanır. Ayırmak sayımı seçim mekaniğinin bir kopyası
hâline getirirdi, oysa taramanın cevapladığı soru "sinyal neden hiç ÜRETİLMEDİ"dir;
seçimin kendisi zaten `emitted` ve kol kırılımından okunur.

**Ayrık sayım bir SAĞLAMADIR, bir tercih değil:** toplam taranan sembol sayısını vermeyen
bir sayım, bir kolun sessizce düştüğünü gizleyebilirdi (`propose_all` bir kolu hata
verdiğinde boş liste döndürür). Bu yüzden kolun patladığı bar ayrı bir sebeple
(`kol_hatasi`) sayılır ve `kurulum_yok`a KARIŞTIRILMAZ: "tez tutmadı" ile "kol patladı"
aynı hücreye yazılamaz.

Sayım bar bazında TOPLANIR (`core/engine.py::ModelReport.survey`; telafi edilen bar da
kendi taramasını yapar) ve alt sınıfta override EDİLMEZ — `scalp_fixed`, `scalp_patient`
ve `scalp_coinflip` aynı kopyayı alır.

### 7. ÖN-KAYITLI ÖLÇÜMLER (sonucu görmeden)

| # | Ölçüm | Tahmin / kural |
|---|---|---|
| **S1** | `avg_stop_distance_pct` farkı, `scalp_patient` ↔ `scalp_coinflip` | **< %1 bağıl.** Aşarsa **M1 ve C-2 OKUNMAZ** ve sebebi sonuca yazılır — ⚠ **DÜZELTME-1 ile S1a + S1b olarak İKİYE BÖLÜNDÜ** (aşağısı); satır kayıt için duruyor, uygulanan hâli S1a/S1b'dir |
| **S2** | kontrolün "ters" oranı (`coin=flipped` payı) | **0.5 ± 0.02** (10.000 çekilişlik kod sağlaması; defterdeki pay örneklem küçükken daha geniş salınır) |
| **M1** | kontrolün ortalama R'si, n ≥ 30'da | %95 bootstrap CI'si **0'ı ve −`cost_per_r`'yi KAPSAR** (bilgisiz yön ≈ −maliyet) |
| **M2** | `scalp_patient.avg_r − scalp_coinflip.avg_r` | §4'teki hâliyle **C-2**: ≥ 0.15R **ve** farkın bootstrap CI alt sınırı > 0 |

**S1 toleransı %1'dir ve bu sayı koşudan ÖNCE sabittir.** §6h > EK-1'in wave için seçtiği
%10 DEĞİL, çünkü buradaki eşleştirme daha sıkıdır: yansıtma mesafeyi aritmetik olarak
korur (test: birebir eşitlik) ve iki model `rng_identity`yi paylaştığı için aynı barda
aynı kurulumu seçer. **Toleransı aşabilecek TEK bilinen mekanizma önceden yazılıyor:**
`max_short_positions` (3) YÖNLE etkileşir — yazı-tura short üretince kota bağlayabilir ve
kontrol o kurulumu hiç açmaz, yani iki defterin DOLUM kümeleri ayrışır ve mesafe dağılımı
kayar. S1 aşılırsa önce bu bakılır, **tolerans GEVŞETİLMEZ** (§7.1).

#### DÜZELTME-1 — S1 İKİYE BÖLÜNDÜ: %1 yanlış kümede ölçülüyordu *(2026-09-22, merge'ten ÖNCE)*

⚠ **Yukarıdaki S1 satırı SİLİNMEDİ ve silinmeyecek.** Yanlış yazılmış bir ön-kayıt satırını
silmek, sicilin engellemek için var olduğu şeyin ta kendisidir (§6c'nin "düşen hipotez
silinmez" kuralının aynısı). Satır olduğu gibi duruyor; aşağıdaki düzeltme onu **kapsam
bakımından** ikiye ayırır ve ikinci parçanın toleransını değiştirir.

**Bu düzeltme `scalp_coinflip`in HİÇBİR İŞLEMİ GÖRÜLMEDEN yazıldı.** Model bu satırlar
yazılırken henüz tek tur koşmadı: defteri yok, tek bir `coin=` etiketi üretmedi ve
`avg_stop_distance_pct` değeri hiçbir yerde hesaplanmadı. Düzeltmenin dayanağı kontrolün
sonucu değil, **`scalp_patient`in ZATEN VAR OLAN defteridir** — yani §7.1'in yasakladığı
"sonuca bakıp eşiği ayarlama" burada yapılamaz, çünkü bakılacak bir sonuç yok.

**Ölçülen gerekçe (`ledgers_scalp/scalp_patient/`, 533 özsermaye barı / 22 kapanmış
pozisyon):**

| Gözlem | Değer | Anlamı |
|---|---|---|
| azami eşzamanlı pozisyon | **5** (11 barda, %2.1; şu an açık olan küme de tam 5) | **`max_positions` BAĞLIYOR** |
| ≥ 4 pozisyon taşınan bar | 190 / 533 = **%35.6** | tavan sınırda gezen bir doluluk, istisna değil |
| azami eşzamanlı short | **3** (şu an açık kümede de 3) | **`max_short_positions` BAĞLIYOR** |
| yön dağılımı (kapanmış) | long %68.2 ↔ short %31.8 | kontrolün ~%50 short'u kotayı DAHA SIK bağlayacak |

**Neden %1 yanlış kümede ölçülüyordu.** Ön-kayıt, dolum kümelerini ayrıştırabilecek TEK
mekanizma olarak `max_short_positions`ı yazmıştı. Ölçüm bunu **eksik** buldu: toplam tavan
(5) da bağlıyor ve bağladığı anda ayrışma yönden BAĞIMSIZ hâle gelir — kotası dolu bir
barda iki modelin hangi kurulumu açabildiği, o ana kadar taşıdıkları pozisyonların
ömürlerine bağlıdır ve o ömürler yön çevrildiği anda zaten ayrışmıştır. Yani
`avg_stop_distance_pct`, iki modelin AYNI kurulumlarının değil **farklı ALT
KÜMELERİNİN** ortalaması olur. Bir alt küme farkı üzerinde %1 bağıl tolerans aramak,
aritmetik olarak korunan bir şeyi (mesafe) korunmayan bir şeyle (hangi kurulumun
doldurulabildiği) sınamaktır.

⚠ **Bu mekanizma ZATEN YAZILIYDI ve §6i onu devralmadı.** §6h > EK-1 wave için %10'u
seçerken gerekçesini üç maddede saymıştı ve **(b) maddesi** birebir şuydu: *"dolumlar
ayrışır ve `max_positions` doluluğu zamanla farklılaşır."* §6i aynı aileden bir kontrol
tanımlarken bu maddeyi taşımadı, yalnızca (c)'yi (`max_short_positions`) yazdı. Yani
DÜZELTME-1 yeni bir keşif değil, **bir kopyalama eksiğinin onarımıdır** — ve bu, eşiğin
sonuca bakılarak seçilmediğinin ikinci kanıtıdır: devralınan sayı, zaten commit edilmiş
bir ön-kayıtta duruyordu.

**S1 ikiye bölünür ve ikisi AYRI şeyleri sınar:**

| # | Ölçüm | Kural |
|---|---|---|
| **S1a** | aynı bar + aynı kol + aynı sembol kurulumunda stop mesafesi | **BİREBİR EŞİT** (`rel=1e-12`). Yansıtmanın mesafeyi koruduğunun kanıtı; dolum kümesinden BAĞIMSIZDIR, çünkü kurulum düzeyinde ölçülür. Mevcut test: `tests/test_scalp_coinflip.py::test_the_control_trades_the_same_setup_at_the_same_stop_distance` |
| **S1b** | `avg_stop_distance_pct` farkı, `scalp_patient` ↔ `scalp_coinflip` (DEFTER) | **< %10 bağıl** (§6h > EK-1'in wave için seçtiği sayı). Aşarsa **M1 ve C-2 OKUNMAZ** ve sebebi sonuca yazılır |

**S1a KAPI GİBİ davranır, S1b okuma koşuludur.** S1a düşerse yansıtma bozuktur ve ölçüm
zaten geçersizdir — o bir kod hatasıdır, bir veri durumu değil. S1b düşerse kod doğru ama
iki defter kıyaslanabilir ölçekte değildir; o zaman C-2 okunmaz (§4'ün C-4'ünün aynı
mantığı: ⚠B bir kusur değil, bir kıyas koşuludur).

**%10 neden EK-1'in sayısı ve neden gevşetme DEĞİL.** EK-1 wave için %10'u tam olarak bu
üç mekanizmaya dayandırmıştı: (a) posterior ayrışması, (b) dolumların ayrışması, (c)
`max_short_positions`ın yönle etkileşmesi. Burada (a) YOKTUR (kontrol öğrenmez) ama (b) ve
(c) aynen vardır ve ölçüm (b)'nin `max_positions` üzerinden de işlediğini gösterdi. Yani
aynı gerekçeye aynı sayı veriliyor — **`scalp_coinflip`in bir sonucuna bakılarak değil,
kardeş bir ön-kaydın zaten sabitlenmiş sayısı devralınarak.** Yeni bir eşik uydurmak,
tam da §7.1'in yasakladığı serbestliği açardı.

⚠ **S1b bir kez daha aşılırsa tolerans YİNE GEVŞETİLMEYECEKTİR** (§7.1). O durumda
yapılacak şey eşiği büyütmek değil, C-2'yi OKUMAMAK ve kotanın ayrıştırdığını
raporlamaktır.

**BİLGİ SATIRI — kapı değil, ölçüm** (`passed` üretmez, hiçbir eşiği yoktur):

| # | Bilgi | Kaynak |
|---|---|---|
| **I1** | kontrolün LONG oranı (kapanmış pozisyonlarda) | defter; beklenen ~%50, `scalp_patient`in %68.2'si ile YAN YANA okunur — fark yazı-turanın kurulumun yön yanlılığını gerçekten sildiğini gösterir |
| **I2** | kontrolün `max_short_positions` ret sayısı | `round.models[].rejections` (`core/engine.py`); kod ZATEN üretiyor, yeni bir alan açılmadı |
| **I3** | kontrolün `max_positions` ret sayısı | aynı yer — DÜZELTME-1'in gerekçesi tam olarak bu kodun da bağladığıdır, yani ayrı sayılmalıdır |

I1–I3 **S1b'yi AÇIKLAR, onun yerine geçmez:** S1b düşerse bu üç sayı sebebin kotada mı
başka yerde mi olduğunu söyler. Hiçbiri bir eşiğe bağlanmaz — bağlansaydı sonucu gördükten
sonra "hangi sayı kapı" diye seçme serbestliği doğardı.

**M1 bir KAPI değil, bir tutarlılık kontrolüdür.** Bilgisiz bir yön seçiminin beklenen
değeri sıfırdır ve gerçekleşen R friksiyon kadar altındadır; CI bunu kapsamıyorsa ölçülen
şey yönün bilgisizliği değil başka bir şeydir (ör. yansıtmanın mesafeyi ya da hedef
yapısını bozması, §5) ve önce o araştırılır.

**`take_survey` için ön-kayıtlı ölçümler (30 tur sonra okunur):**

| # | Ölçüm | Tahmin / kural |
|---|---|---|
| **V1** | `funding_spike_fade`in kurulumlarının hangi sebepte elendiği | **TAHMİN YOK** — bugün bilinmiyor ve bir tahmin uydurmak, sayımın var olma sebebini (karar 48'in açık işi) sonradan "zaten biliyorduk"a çevirirdi. Sebep adıyla raporlanır |
| **V2** | `momentum_burst`ün eleme sebebi | kurulumlarının **neredeyse tamamı `hedef_stop`ta** elenmeli, `kurulum_yok`ta değil — karar 34'ün aritmetiğinin türetimi budur. Doğrulamazsa **karar 34 eksiktir** ve karara bir DÜZELTME alt başlığı yazılır |

V2 bir modelin performansı hakkında bir iddia değil, **var olan bir kararın türetiminin
sağlamasıdır**; bu yüzden o da sicile girmez.

### 8. Koşu kuralları

- **Hiçbir mevcut defter sıfırlanmaz ve hiçbir modelin kuralı değişmez** (§7.1).
  `scalp_fixed`, `scalp_patient`, `vwap_clone` ve `vwap_managed`in parametreleri,
  geometrileri ve çekiliş kimlikleri aynen kalır; `scalp_coinflip` katmana yeni bir satır
  olarak EKLENİR.
- **Kontrolün tohumu tek seferliktir** ve farklı tohumla yeniden çekilmez (§6g >
  "Kontrolün TOHUMU" ile birebir aynı gerekçe: C-2 bir FARKA dayanır ve tohum serbest
  bırakılsaydı "kontrol kötü çıkana kadar yeniden çek" mümkün olurdu). Tohum
  `random_seed`dir ve zaten sabittir.
- **Kontrolün birikmesi beklenirken C-2 okunmaz.** `n < 30` iken yazılacak tek cümle
  "kontrol birikiyor"dur; ara bir okuma, çıtayı örneklem büyüdükçe defalarca sorgulamak
  (ve en uygun anda durmak) demekti.
- `scalp_coinflip` **wave koşularının kontrolü DEĞİLDİR** ve olmayacaktır (§6h > EK-1);
  `scripts/backtest_wave.py` kontrolü açıkça geçirmeye devam eder.

### 9. Bu ön-kayıt neyi SEÇMİYOR

- Scalp katmanına **çıpa eklemiyor**: C-3 değerlendirilemez kalır (§4).
- `scalp_patient`i canlıya ALMIYOR: C-1 bugün sağlanmıyor (−0.01) ve bu bölüm o sayıya
  dokunmuyor.
- Kolların tezine, eşiklerine ya da geometrisine dokunmuyor: `take_survey` yalnızca
  SAYAR.
- Ölü kolları (`momentum_burst`, `funding_spike_fade`) silmiyor ya da onarmıyor —
  V1/V2 tam olarak onarımın hangi yerde yapılacağını ölçmek içindir.

---

## 6j. ÖN-KAYIT — `dc_short` (model 22): ölüm kesişimi + geri çekilme short, ve `dc` katmanı

> **NUMARALANDIRMA NOTU (birleştirmede, içerik değişmeden).** Bu ön-kayıt `03e9e2e`, `5ad7653` ve `cd8fb54` commit'lerinde **§6i**, kararı **54**, kontrolü **model 23** olarak yazıldı. Aynı günlerde `main` kendi ön-kaydını da §6i, kararını 54 ve `scalp_coinflip`i model 23 olarak işledi; birleştirmede `main`'in numaraları korundu ve bu bölüm **§6j**, kararı **55**, kontrolü **model 24** oldu. Metnin hiçbir kuralı, sayısı ya da tarihi değişmedi — yalnızca numaralar. O commit mesajlarındaki "§6i" bu bölümü kasteder.

**Bu belge koşudan ÖNCE yazıldı ve AYRI bir commit olarak işlendi** — uygulama (strateji,
config, harness, testler) SONRAKİ commit'tedir. Koşudan önce görülen TEK veri
ölçülebilirlik sayımıdır (§6j > 2): olay sayısı ve stop MESAFESİ. Getiri, R, PnL, kazanma
oranı görülmedi. §7'nin tamamı bu bölüme uygulanır.

### 1. Köken, mekanizma, statü

**Köken dıştır:** bir eğitim görseli. Dışarıdan gelen YALNIZCA sinyal kuralıdır; boyut
(risk %1), kaldıraç tavanı, maliyet, funding ve likidasyon evin kuralıdır. Statü bu
yüzden `ema_trend`inkiyle aynıdır: **tam yarışmacı, kopya DEĞİL** (`is_replica = False`,
kural 15b) — kopya dış sistemin boyutlandırmasını da taşır ve yarışmaz.

**Mekanizma.** Ölüm kesişimi (EMA50 < EMA200) orta vadeli bir düşüş rejimini işaretler.
Rejim içinde 50 EMA'ya geri çekilme, geç gelen alıcıların tükendiği yerdir; reddedilme
trendin devamına işaret eder. Karşı taraf: düşüşte "dip avlayan" erken alıcılar.

**Önsel kanıt — tezin ALEYHİNE, olduğu gibi yazılır.** Aynı aileden EMA kesişimi short
işlemleri model sahibinin BTC iskelet testinde **her iki dönemde de kaybetti: dönem A
−127 USDT, dönem B −178 USDT.** Parametreler ve giriş kuralı farklı, aile aynı. Kaynağı
model sahibidir; sayı bu deponun defterinden okunmadı ve burada doğrulanamaz — ama yönü
nettir ve P1'i (§6j > 11) **riskli bir tahmin** yapar. Önsel kanıtı yazmamak, olası bir
olumlu sonucu olduğundan şaşırtıcı olmayan gösterirdi.

### 2. Ölçülebilirlik sayımı — kapı GEÇİLDİ (koşudan önce görülen TEK veri)

`scripts/measure_death_cross.py`, commit `f5df1bb`, workflow koşusu `#35718873885`.
Tanımlar §6j > 3'tekilerin aynısı; ChopZone yok (sayı bir ÜST SINIRDIR). Kapı sayı
görülmeden sabitlenmişti: **dönem A birincil sayım ≥ 150.**

| | Dönem A |
|---|---|
| Ölüm kesişimi | 139 |
| Kurulum — ham | 1773 |
| **Kurulum — BİRİNCİL** (önceki birincilden ≥ 6 bar sonra) | **1090 → kapı GEÇTİ** |
| Kurulum — rejim başına ilk | 121 |
| Stop mesafesi `(EMA200 − close) / ATR(14, simple)` — medyan / p75 / p90 / azami | 2.88 / 4.53 / 6.62 / 19.45 |
| Birincillerin `> 3.0` / `> 6.0` payı | %48 / %15 |

Kapsam boşlukları (sayı bunlara rağmen kapıyı geçti, ama okuyucu bilmeli): PENGU dönem A'da
HİÇ yok; ETHFI yalnızca 21 barla sayıldı; SUI 2023-08-13'ten, BNB 2023-04-02'den itibaren
sayıldı (OKX'in verdiği ilk bar + 600 barlık ısınma).

⚠ **Kapı bir ÜST SINIRA uygulandı.** Sayımın 6 barlık kümeleme kuralı modelde YOKTUR;
modelin fiili kümelemesi "sembolde açık pozisyon varken yeni pozisyon açılmaz" kuralıdır
(`duplicate_position`, §6j > 3) ve o, tutuş süresi boyunca sürdüğü için daha uzundur. Yani
gerçekleşen pozisyon sayısı 1090'ın **altında** olacaktır. Fark koşudan sonra bir huni
olarak raporlanır (§6j > 6).

**Stop tavanı (6.0) bu geometriden seçildi, sonuçtan DEĞİL:** stop EMA200'de kalırsa
kurulumların %48'i `ema` katmanının 3.0 tavanını aşıyor, 6.0'da %15'i elenir. Sayı bu
commit'te sabittir.

### 3. Model: `dc_short` (short-only, stop'lu, yarışmacı)

| Boyut | Tanım |
|---|---|
| EMA'lar | EMA50, EMA200 — kapanış üzerinden, 4H, `core/indicators.py`nin TEK EMA tanımı (tohum ilk `period` barın SMA'sı) |
| Ölüm kesişimi | bar c'de `EMA50 < EMA200` ve bar c−1'de `EMA50 ≥ EMA200` |
| Rejim | kesişimden sonra `EMA50 < EMA200` kaldığı sürece aktif. Kesişimin KENDİ barı rejimin ilk barıdır |
| Kurulum barı | rejim aktif, `high ≥ EMA50`, `close < EMA50`, `close < open` |
| Sinyal | kurulum barında üretilir; dolum bir SONRAKİ barın açılışından (kural 13) |
| Yön | yalnızca short (`allowed_directions = ["short"]`) |
| Stop | kurulum barındaki EMA200 değeri |
| Stop tavanı | `6.0 × ATR(14, simple)`, ölçüt `\|kurulum kapanışı − stop\|`. **Model SÜZMEZ** — eleme motorun kapısıdır (`core/engine.py::_within_stop_band`), `skipped_signals` olarak sayılır |
| Hedef | `min(low[c .. t−1])`: kesişim barından kurulum barının BİR ÖNCEKİNE kadarki en düşük low — kurulum barı HARİÇ (bkz. aşağısı) |
| Hedef geçilmişse | `close ≤ hedef` → sinyal ÜRETİLMEZ (`target_passed`) |
| Hedef dilimi | TEK dilim, `fraction = 1.0` — kesirli hedef pozisyonu iki ölçüm satırına bölerdi (`ema_trend`in kuralı) |
| Çıkış | yalnızca stop, hedef ya da likidasyon. `manage_positions` UYGULANMAZ: zaman stop'u yok, rejim-sonu çıkışı yok — kaynakta ikisi de yok |
| Sembol başına | tek pozisyon. Model kendi açık pozisyonunu göremediği için (kural 4) kuralı `core/portfolio.py` uygular; tekrar `duplicate_position` sebep koduyla reddedilir ve sayılır (`buyhold`/`xsec`in deseni) |
| Sinyal sırası | katmanın evren listesinin sırası — kota bağladığında hangi sinyalin dolacağı deterministik olmalı ve model ile kontrol aynı sırayı görmeli |
| ATR | modelin kendisi ATR KULLANMAZ; ATR yalnızca motorun tavan kapısındadır ve projenin varsayılanı (`simple`) ile ölçülür — `xsec`in §6g'deki aynı gerekçesi |
| ChopZone | DÂHİL DEĞİL: kaynağın renk anlamı tanımlı bir göstergeyle net örtüşmüyor, eklemek serbest parametre olurdu; `ema_trend`de aynı şeyi ölçen üst üste filtreler kenar eklemedi |

**Hedef tanımının gerekçesi — kurulum barı HARİÇ (commit'ten önce, sayı görülmeden
seçildi).** Kaynak ifade ("kesişim barından kurulum barına kadarki en düşük low") iki türlü
okunabiliyordu. Kurulum barı DÂHİL edilseydi hedef tanım gereği `≤ low[t] ≤ close[t]`
olurdu ve "hedef zaten geçilmiş" kuralı yalnızca kapanışın barın dibinde olduğu anlarda
tetiklenirdi — kural fiilen boş kalırdı. Kuralın gerekçesi hedefin kurulumdan ÖNCEKİ bir
dip olduğunu varsayıyor; bu yüzden aralık `[c, t−1]`dir.

**`target_undefined` raporlanır.** Kurulum kesişim barının kendisindeyse (`t = c`) aralık
boştur, hedef tanımsızdır ve sinyal üretilmez. Sayımda 1090 birincilin 4'ü bu durumdaydı —
az, ama sayı raporlanır ki elenen kurulumlar görünür olsun (huni, §6j > 6).

**Bu seçim hedef-R dağılımını DOĞRUDAN etkiler ve o yüzden dağılım ayrıca önemlidir.**
Hariç tutmak bile erken rejim kurulumlarında hedefi yakın bırakır: kesişimden birkaç bar
sonraki bir kurulumda "önceki dip" çoğu zaman girişin hemen altındadır ve R:R 1'in altına
düşer. **Filtre EKLENMEZ.** Koşudan sonra bakılacak şey: kaç pozisyon `rr < 1.0` hedefle
girdi (§6j > 13). Büyük bir kısmıysa modelin geometrisi baştan zayıf demektir — ama bu bir
SONUÇ olarak öğrenilir, şimdi bir filtreyle önlenmez.

**Kesişimin GÖRÜLEBİLİRLİĞİ — veri derinliği bu modelde sinyali DEĞİŞTİREBİLİR.** Hedef
kesişim barına bağlıdır; model ise her barda yalnızca son **`dc.lookback_bars` = 3000**
barı görür — bu bir MODEL kuralıdır ve canlıda da backtest'te de aynıdır (bkz. §6j >
TADİLAT-1). Pencerenin ilk **600 barı** EMA200'ün ısınmasıdır (tohumun ağırlığı `(1 − 2/201)^400 ≈
%1.4`; sayımın `WARMUP_BARS`ının AYNISI) ve kesişim yalnızca o sınırdan SONRA aranır.
Kural: `t` barından geriye `EMA50 < EMA200` sürdüğü müddetçe yürünür; ısınma sınırına
kesişim bulunmadan varılırsa kurulum `cross_not_visible` olarak sayılır ve sinyal
üretilmez. 3000 barlık pencerede bu, **~400 günden eski** bir kesişim demektir.

**Tarama sırası ve sayım kodları (`take_survey`, karar 34'ün dersi) — sıra sabittir:**

1. `no_data` — `as_of` barı yok ya da pencere `600 + 2` bardan kısa
2. `no_regime` — `EMA50 ≥ EMA200`
3. `candle_fails` — `EMA50 < EMA200` ama mum koşulu tutmuyor
4. `cross_not_visible` — mum tutuyor, kesişim ısınma sınırından sonra bulunamadı
5. `target_undefined` — kurulum kesişim barının kendisinde
6. `target_passed` — `close ≤ hedef`
7. `setup` — sinyal üretildi

Mum koşulu kesişim aramasından ÖNCE gelir, çünkü `cross_not_visible`in anlamı "kurulum
olurdu ama kesişimi göremedik"tir — paydası kurulum barlarıdır, rejim barları değil.

**Defter etiketleri** (`core/tags.py`): `arm=dc_pullback | regime=<kesişim barı ISO> |
rr=<hedef mesafesi ÷ stop mesafesi>`. `regime` küme bootstrap'ının kimliğidir (§6j > 8),
`rr` koşu sonrası hedef-R dağılımının kaynağıdır. Mesafeler kurulum kapanışından ölçülür.

### 4. Kontrol: `dc_coinflip` (model 24, yarışmacı)

`wave_coinflip`in (§6h > EK-1) deseni. `dc_short`tan TÜRER; kurulum tespiti, geometri,
evren, sıra ve sayım **miras alınır, kopyalanmaz**. Ayrışan TEK şey yöndür — ölçülen eksen
odur.

- Yazı-tura "aynı" → sinyal `dc_short`unkinin birebir aynısı.
- Yazı-tura "ters" → yön long, mesafeler **kurulum kapanışı** etrafında aynalanır:

  ```
  stop_ters  = close + (close − stop)
  hedef_ters = close + (close − hedef)
  ```

  Aynalama kapanış etrafındadır, çünkü doğrulamanın ve tavan kapısının referansı odur
  (`core/engine.py::_reference_price`). `|close − stop|` korunduğu için **tavan kapısı iki
  modelde birebir aynı çalışır** ve ⚠B yanmaz.

**RNG ayrı akıştır:** `random.Random(f"{random_seed}:{as_of}:dc_coinflip:{symbol}")`.
**Tohum `random_seed = 20240217`** (`config.yaml`, kök) ve **koşu tek seferliktir** —
farklı tohumla yeniden koşulmaz (§6g > Kontrolün TOHUMU ile birebir aynı gerekçe: E kapısı
bir FARKA dayanır). Tohum değişirse koşu yeni bir tezdir ve sicile ayrı satır olarak girer.
Denetim izi `coin=same|flipped` etiketidir.

**Bilgi yapısı — koşudan önce yazılır:** "aynı" gelen kurulumlarda iki model aynı sinyali
üretir ve ikisi de dolarsa R'leri özdeştir; yani E kapısının farkı esasen **"ters" gelen
yarıdan** beslenir. Farkın kesinliği bu yüzden `n` değil kabaca `n/2` üzerinden okunmalıdır.

### 5. Katman: yeni `dc`

| Ayar | Değer | Gerekçe |
|---|---|---|
| `models` | `buyhold`, `dc_short`, `dc_coinflip` | çıpa (C-3), model, kontrol — kıyas katman İÇİNDE |
| `universe` | `ema` katmanının 13 sembolü, SABİT | sayımla aynı evren |
| Defter / rapor | `ledgers_dc/`, `docs/data/metrics_dc.json` | katmanlar defter paylaşmaz |
| `max_stop_atr_multiple` | **6.0** | §6j > 2 |
| `max_short_positions` | **5** (= `max_positions`) | aşağısı |
| `acceptance.control_model` | `dc_coinflip` | kök değer (`random_ctrl`) bu katmanda YOK; bırakılsaydı kapı kümede olmayan bir modele bakıp sessizce düşerdi (`xsec`in aynı gerekçesi) |
| `data.history_bars` | **3000** — canlı çekim derinliği; `dc.lookback_bars`dan küçük olamaz | §6j > 6, TADİLAT-1 |
| `breakdowns` | `symbol`, `exit_rule`, `session`, `loss_streak` | `ema`/`xsec` ile aynı statüde ölçüm |
| Tetikleyici | **YOK** | tanım var, koşu yok (`ema`/`xsec` statüsü) |

**Neden `ema`ya eklenmedi.** `ema`nın tavanı 3.0'dır ve motor merkezî uygular:
kurulumların %48'i elenirdi. Tavanı yükseltmek, TAMAMLANMIŞ ve ön-kayıtlı bir koşunun
(`#35391881083`) katman koşulunu geriye dönük değiştirirdi — `xsec`in §6g'deki aynı
gerekçesi.

**Neden `max_short_positions: 5`.** Kök değerle (`3`) short-only `dc_short` en fazla 3
pozisyon taşır, karma yön üreten `dc_coinflip` ise 5 — ölçülen eksenle (yön seçimi)
ilgisi olmayan bir **kapasite asimetrisi**, ve E kapısının farkını kısmen kapasite
farkından üretirdi. Kotayı `max_positions`e eşitlemek iki modelin taşıma kapasitesini
eşitler. ⚠ Bu, depo tarafından KORUNAN bir değişmezin bilerek daraltılmasıdır — bkz.
TADİLAT-2 (ilk metin burada "katman bloğunun taşımadığı bir ayar sınıfı" diyordu ve eksik
anlatıyordu).

### TADİLAT-2 — kota override'ı KORUNAN bir değişmezi daraltıyor *(koşudan önce, uygulama testleri koşarken, hiçbir sonuç görülmeden)*

> **Önceki iddia YANLIŞTI.** Plan aşamasında ve ön-kayıt metninde (`03e9e2e`)
> `max_short_positions`ı katman bloğunda ezmenin **"yapısal olarak izinli"** olduğu yazıldı.
> Bu, `max_stop_atr_multiple`ın katman başına ayrışmasından yapılmış yanlış bir
> genellemeydi. Kota sabitleri katmanlar arası PAYLAŞILIR ve depo bunu bir testle korur:
> `tests/test_layers.py::test_cost_and_risk_constants_are_identical_in_every_layer`,
> `max_positions` ve `max_short_positions`ı `fee_rate`, `risk_per_trade`, `leverage_cap`
> ile AYNI listede — kural 6'nın paylaşılan sabitleri olarak — sayar. Uygulama o test
> kırmızıya döndüğünde bulundu. Kullanıcı kararı öncülün yanlış olduğu bilinerek YENİDEN
> verildi.

**Karar: 5 kalır, değişmez DAR bir istisnayla daraltılır.** Gerekçe projenin birinci
ilkesidir (CLAUDE.md > Amaç: ölçümün adilliğini bozan her şey reddedilir): kök değerle
short-only model 3, karma yönlü kontrolü 5 pozisyon taşır ve bu kapasite asimetrisi E
kapısının farkına sızar — yani ölçülen eksen (yön) ile ölçülmeyen bir değişken (kapasite)
karışır.

**İstisnanın sınırları — mekanik olarak sabitlenir:**

- Test anahtarı paylaşılan listeden **ÇIKARMAZ.** İzinli katman başına kota istisnaları
  AÇIKÇA sayılır (`{dc: {max_short_positions}}`); başka her katmanın ayrışması ve bu
  katmanın başka her sabitte ayrışması yine kırmızıdır.
- İstisna yalnızca KOTA anahtarlarına açıktır (`max_positions`, `max_short_positions`).
  Maliyet ve risk-oranı sabitleri (`fee_rate`, `slippage_*`, `risk_per_trade`,
  `leverage_cap`, `initial_capital`, `maintenance_margin`, `random_seed`) istisna
  listesine GİREMEZ — test bunu da sınar.
- İstisna ölü kalamaz: listedeki anahtar katmanda gerçekten ayrışmıyorsa test kırmızıdır
  (bayat bir istisna, bir gün sessizce kullanılabilecek bir delik olurdu).

**Neden kotayı paylaşılan bir sabit saymak hâlâ doğru — ve neden bu istisna onu
çürütmüyor.** Kota bir risk varsayımıdır: katmanlar arası ayrışsaydı "aynı kurallar" iddiası
bir katmanda sessizce gevşeyebilirdi. Buradaki ayrışma sessiz değildir (ön-kayıtta, karar
54'te, testte ve config'te yazılı), bir katmanla sınırlıdır ve YÖNÜ bellidir: short
kotasını toplam kotaya EŞİTLER, toplam kotayı (`max_positions` 5) aşmaz. Katmanlar arası
kıyas zaten yapılmadığı için (CLAUDE.md > Katmanlar) bir katmanın kotası başka bir katmanın
okumasını değiştirmez; katman İÇİNDE ise iki model aynı kotayı görür (kural 6).

### 6. Pencereler, veri derinliği, embargo

| Dönem | Aralık | Rol |
|---|---|---|
| **A** | 2022-01-01 → 2024-06-30 sinyal kesimi; kesimde açık pozisyonlar 2024-12-31'e kadar yönetilir | IS |
| **B** | A kesimi + ÖLÇÜLEN embargo → koşu günü | **OOS** |

Sınırlar `scripts/backtest_ema.py`den İTHAL EDİLİR (`PERIOD_A_START`, `PERIOD_A_CUTOFF`,
`PERIOD_A_TAIL_END`) — sayımla, `ema_trend`le ve `xsec_mom`la aynı pencere. **Embargo
VARSAYILMAZ, dönem A'dan ÖLÇÜLÜR** (`measured_embargo_bars`): modelin zaman stop'u yok,
§6.1'in dayandığı üst sınır tanım gereği yok. **Dönem B'ye koşu öncesi DOKUNULMAZ.**

**Modelin görüş penceresi bir serbest parametre DEĞİLDİR: canlıda ve backtest'te AYNI
3000 bar (≈ 500 gün), `dc.lookback_bars` ile modelin KENDİSİNDE sabitlenir** (bkz.
TADİLAT-1 — ilk metin bunu `data.history_bars`a bağlıyordu ve motorun mekaniğiyle
çelişiyordu). Kesişim bu pencerenin dışında kalırsa hedef tanımsızdır ve sinyal üretilmez
(§6j > 3). Pencere modelde sabitlendiği için backtest'in `--history-bars` değeri (12000)
§5b'nin 1. sınıfına geri döner: yalnızca verinin nereden başladığını belirler, modelin
gördüğünü DEĞİŞTİRMEZ.

**`cross_not_visible` eşiği — koşudan ÖNCE yazılır:** dönem A'da `cross_not_visible`,
kurulum barlarının (`setup + target_undefined + target_passed + cross_not_visible`)
**%5'ini aşarsa** bu bir BULGUDUR: 3000 bar bu model için yetersizdir. **O durumda bile
bu koşuda derinlik DEĞİŞTİRİLMEZ** — sonuç kayda geçer ve gelecekteki bir tur için not
olur. Sayıyı görüp derinliği yükseltmek §7.3 ihlalidir. (Payda bar düzeyindedir, çünkü
modelde 6 barlık kümeleme YOKTUR; sayımın "birincil" birimi modelde tanımlı değildir.)

**Sayım ↔ backtest farkı — koşudan sonra HUNİ olarak raporlanır.** Sayım ısınmayı dönem
A'nın öncesinden aldı ve her sembolün TAM geçmişini gördü; backtest 3000 barlık kayan bir
pencereyle sınırlı. Kurulum sayısının 1773 (ham) / 1090 (birincil) altında kalması
beklenen bir sapmadır, ama büyüklüğü bilinmeli — yoksa "sayım 1090 demişti, backtest neden
daha az" sorusu cevapsız kalır. Huni (dönem A, `dc_short`):

| Aşama | Kaynak |
|---|---|
| sayım: ham 1773 / birincil 1090 | §6j > 2 |
| modelin kurulum barları (`setup + target_undefined + target_passed + cross_not_visible`) | `survey` |
| fark: `cross_not_visible` + kalıntı (pencere tohumu kaynaklı EMA farkı) | `survey` |
| − `target_undefined`, − `target_passed` → üretilen sinyal (`setup`) | `survey` |
| − tavan elemesi → kuyruğa giren | `skipped_signals`, `emitted` |
| − `duplicate_position`, kota, `gap_past_stop` → açılan pozisyon | `rejections` |

1090'ın modelde karşılığı yoktur (6 barlık kural modelde yok); modelin kümelemesi
`duplicate_position` satırıdır ve huni onu AYRI gösterir.

### TADİLAT-1 — görüş penceresi MODELE taşındı *(koşudan önce, uygulama yazılırken, hiçbir sonuç görülmeden)*

> **İlk metin (`03e9e2e`) şunu diyordu:** "Veri derinliği bir serbest parametre DEĞİLDİR:
> katman ve backtest AYNI sayıyı kullanır (3000 bar ≈ 500 gün) … `--history-bars` bu
> koşuda bir 'derinleştirme' değil katmanın kendi değeridir." **Bu cümle motorun
> mekaniğiyle çelişiyordu ve uygulanamazdı.**

**Ne bulundu.** `data.history_bars` backtest'te ve canlıda İKİ AYRI şey belirliyor:

- **Canlıda** modele verilen çerçeve tam olarak `history_bars` bardır
  (`core/data.py::fetch_ohlcv` → `tail(history_bars)`).
- **Backtest'te** aynı sayı yüklenen verinin BAŞLANGICINI belirler ve motor her bara, o
  başlangıçtan o bara kadar olan HER ŞEYİ verir (`core/engine.py::_snapshot` →
  `bars_until`). Görüş penceresi bar ilerledikçe BÜYÜR.

İki sonuç: (a) backtest'e `--history-bars 3000` vermek dönem A'yı son ~3000 barına
keserdi (A + kuyruk ≈ 6570 bar) — koşu kullanılamazdı; (b) doğru derinlikle bile modelin
backtest'te gördüğü pencere canlıdakinden BÜYÜK olurdu ve `cross_not_visible` canlıda
olacağından az görünürdü — tam olarak §5b'nin engellemek için var olduğu canlı ↔ backtest
ayrışması.

**Düzeltme.** 3000 barlık görüş bir MODEL kuralı olur: `dc.lookback_bars: 3000` ve model
her barda çerçevenin son `lookback_bars` barını kullanır (diğer modellerin `tail(...)`
deseni). Katmanın `data.history_bars`ı 3000 kalır (canlı çekim; uygulama `history_bars ≥
lookback_bars` şartını KURULUMDA sınar). Backtest `--history-bars 12000` ile koşar
(`ema_trend`in koşusuyla aynı derinlik) ve bu artık §5b'nin 1. sınıfıdır: veri daha
eskiden başlar ama model yine son 3000 barı görür. Gösterge ATR'si (tavan kapısı) son 15
barı okuduğu için derinlikten etkilenmez.

**Kararın ÖZÜ DEĞİŞMEDİ, yalnızca doğru yere konuldu.** Kullanıcının kararı (§6j > 6):
"3000 bar; serbest parametre değil; canlıda ve backtest'te aynı; `cross_not_visible`
raporlanır, %5'i aşarsa bulgudur ama koşuda değiştirilmez." Bu kararın dört parçası da
aynen yürürlükte — tek fark 3000'in `history_bars` yerine `lookback_bars`ta durmasıdır ve
o, kararı mekanik olarak DOĞRU kılan tek yerdir. Sayım ↔ backtest farkının gerekçesi
("backtest 3000 barlık kayan bir pencereyle sınırlı") ilk metinde bir varsayımdı; bu
tadilatla gerçek oldu.

**Yan sonuç — dönem A'nın ilk barlarında görüş.** A 2022-01-01'de başlar; tam 3000 barlık
görüş için sembolün ~2020-08'den beri verisi olmalıdır. Daha geç listelenen sembollerde
(SOL, NEAR ve sayımdaki BNB/SUI/ETHFI gibi) görüş mevcut veriyle sınırlıdır — canlıda da
aynısı olurdu. Isınma kuralı (ilk 600 bar) pencere kısa da olsa aynen uygulanır; etkisi
`cross_not_visible` ve `no_data` sayımlarında görünür, gizlenmez.

### 7. Maliyet — canlı config, değişiklik YOK

`fee_rate`, `slippage_base`, `slippage_short_stop`, funding: kökten, canlıyla aynı. İki
bilinen asimetri koşudan önce yazılır ve DÜZELTİLMEZ (düzeltmek maliyet modelini
değiştirmek olurdu, kural 2/6):

- **`slippage_short_stop` (0.0015) yalnızca short stop'larına uygulanır.** `dc_short`un
  bütün stop'ları bu kaymayı öder, `dc_coinflip`in yalnızca "aynı" yarısınınkiler. Etki
  mertebesi: stop mesafesi ~%5–10 iken ek 0.001 kayma ≈ **0.01–0.02R** / stop çıkışı —
  0.15R marjın yanında küçük ama sıfır değil ve modelin ALEYHİNE.
- **Funding derinliği (karar 50):** REST uç noktası ~3 aylık kayan bir pencere tutuyor;
  pencerenin çoğunda kayıt yok ve `core/funding.py::rate_at` None döner, maliyet
  işlenmez. `ema_trend`de bu "eski dönem İYİMSER" demekti (long funding öder). **Short'ta
  yön terstir:** pozitif funding short'a GELİRDİR, yani funding işlenmeyen dönemler pozitif
  funding rejiminde modelin ALEYHİNE, negatif funding rejiminde LEHİNE sapar. Hangisinin
  baskın olduğu bu koşuda ölçülemez; sapma raporda yazılı durur.

### 8. İstatistik — KÜME BOOTSTRAP (bağlayıcı)

**Neden i.i.d. bootstrap burada yetmez.** Kurulumlar bağımsız değildir: sayımda 1090
birincil kurulum 121 rejimde kümelendi ve semboller arası korelasyon yüksektir (2022 ayı
piyasası aynı haftalarda bütün evreni aynı yöne itti). i.i.d. yeniden örnekleme bu
bağımlılığı yok sayar ve güven aralığını **sahte biçimde daraltır** — CI bazlı bir kapıyı
gevşetmenin görünmez yolu budur.

**`core/metrics.py`nin canlı yolu DEĞİŞMEZ.** Küme bootstrap harness'ta
(`scripts/backtest_dc.py`) ayrı fonksiyonlardır. Pozisyon ve R tanımı yine
`core/metrics.py::merge_fills` + `r_multiple`'dan okunur — ikinci bir R tanımı yoktur.

**İki küme tanımı:**

| Tanım | Küme kimliği | Yakaladığı bağımlılık |
|---|---|---|
| `regime` | sembol + `regime=` etiketi (kesişim barı) | aynı rejimin ardışık kurulumları |
| `month` | `opened_at`in takvim ayı (UTC), semboller arası ORTAK | semboller arası eşzamanlılık |

Ay kesimi `opened_at`tır, `closed_at` değil (seans kırılımının aynı kuralı: soru "kurulum
ne zaman alındı").

**Ortalama R aralığı.** C kümeden C tanesi YERİNE KOYARAK çekilir, çekilen kümelerin
bütün pozisyonları birleştirilir, ortalama alınır. `acceptance.bootstrap_samples` (2000)
tekrar, yüzdelik aralık, α = `acceptance.edge_ci_alpha` (0.05) — ikinci bir anahtar
açılmaz.

**Fark aralığı (model − kontrol) EŞLEŞTİRİLMİŞTİR** ve bu `core/metrics.py::
bootstrap_diff_ci`'dan BİLİNÇLİ bir ayrılıştır: oradaki gerekçe ("model ile kontrol aynı
barlarda aynı sembollerde işlem açmaz, eşleştirilecek çift yoktur") burada **geçerli
değildir** — iki model tanım gereği aynı kurulum barlarında işlem açar ve kümeler
ORTAKTIR. Her iterasyonda küme etiketleri bir kez, iki modelin kümelerinin BİRLEŞİMİNDEN
çekilir; iki modelin o kümelerdeki pozisyonları alınır ve `ort(model) − ort(kontrol)`
hesaplanır. Bir tarafı boş kalan çekiliş atılır ve **atılan çekiliş sayısı raporlanır.**

**BAĞLAYICI KURAL: iki küme tanımının alt sınırlarından MİNİMUM olanı.**

> **Düzeltme kaydı (commit'ten ÖNCE, sayı görülmeden).** İlk taslak "iki aralıktan GENİŞ
> olanı bağlayıcı" diyordu. Niyet genişlik değil MUHAFAZAKÂRLIKTI; kaymış geniş bir
> aralığın alt sınırı dar olanınkinden yüksek çıkabileceği için o ifade kuralı
> GEVŞETEBİLİRDİ. Bağlayıcı kural: iki küme tanımının (rejim, takvim ayı) alt
> sınırlarından minimum olanı. Her iki aralığın genişliği ve alt/üst sınırları ayrıca
> raporlanır; i.i.d. aralığı da karşılaştırma için raporlanır, bağlayıcı değildir.

Kural hem ortalama R'nin aralığına hem farkın aralığına ayrı ayrı uygulanır.

**Asgari küme sayısı.** Bir dönemde bir tanım **10'dan az küme** üretirse o tanımın aralığı
DEĞERLENDİRİLEMEZ ve kapı GEÇİLMİŞ SAYILMAZ (`core/metrics.py`nin "eksik çıta geçilmiş çıta
gibi görünmemeli" kuralı). Birkaç kümeden kurulan bir bootstrap bir aralık değil bir
anekdottur. Kural yalnızca DÜŞÜRÜR, hiçbir kapıyı geçirmez.

**Bilinen sınır:** küme sayısı azken (ay tanımında dönem A'da ~30) küme bootstrap'ı da
aralığı hafifçe DARALTMA eğilimindedir. Minimum kuralı buna karşı kısmi bir sigortadır;
sınır burada yazılı durur.

**Determinizm:** tohum `f"{random_seed}:{model}:{tanım}:{dönem}"`; aynı defter her zaman
aynı aralığı verir.

### 9. Güç — MDE ETKİN küme sayısı üzerinden (1090 değil)

Formüller şimdi sabitlenir, sayılar koşudan sonra yazılır:

```
SE_küme = sqrt( Σ_g ( Σ_{i∈g} (r_i − r̄) )² ) / n
SE_iid  = sd(r) / sqrt(n)
DEFF    = (SE_küme / SE_iid)²          n_etkin = n / DEFF
MDE     = (z₀.₉₇₅ + z₀.₈₀) × SE_küme = 2.802 × SE_küme
```

Fark kapısı için aynı formül, küme etkisi
`d_g = Σ_{i∈g,model}(r_i − r̄_m)/n_m − Σ_{i∈g,kontrol}(r_i − r̄_c)/n_c` ve
`SE_fark = sqrt(Σ_g d_g²)` üzerinden. Her ikisi de iki tanım için ayrı raporlanır; MDE'nin
bağlayıcı okuması BÜYÜK olanıdır (minimum kuralının güç tarafındaki karşılığı).

⚠ **Bu bir KESİNLİK beyanıdır, gözlenen etki için post-hoc güç DEĞİLDİR.** Soru "bu
koşu hangi büyüklükteki bir etkiyi görebilirdi"dir; "gözlenen fark anlamlı mıydı" sorusu
aralıkların işidir.

**Koşu öncesi projeksiyon (bir tahmin, ölçüm değil):**

| Varsayım | Değer |
|---|---|
| Dönem A pozisyon sayısı (`dc_short`) | **200 – 500** (1773 ham → hedef/tavan elemesi → `duplicate_position` → kota 5) |
| R'nin sd'si | **1.0 – 1.8 R** (stop −1R'de keser, hedef mesafesi değişken — R:R dağılımı koşudan sonra raporlanır) |
| DEFF (ay tanımı) | **3 – 6** (2022'nin ortak yönü) |
| n_etkin | **~50 – 150** |
| **MDE, C-1 (tek örneklem)** | **~0.3 – 0.6 R** |
| **MDE, E kapısı (fark)** | **~0.4 – 0.8 R** — farkın bilgisi esasen "ters" yarıdan gelir (§6j > 4) |

> **Bu kurulum ancak BÜYÜK bir etkiyi tespit edebilir. "Ayırt edilemedi" beklenen
> sonuçlardan biridir ve tezin reddi olarak okunmaz.** Gerçekleşen sd, DEFF ve n_etkin bu
> projeksiyonla yan yana raporlanır — güç hesabının kendisi de denetlenebilir olmalıdır.

**Güç için tasarım OYNATILMADI** (§6g'nin aynı gerekçesi): ChopZone'suz, kümelemesiz,
tek tanım.

### 10. Kapılar

**Bağlayıcı — hepsi:**

| # | Kapı | Koşul |
|---|---|---|
| B-0/B-1/B-2 | geçerlilik (§3) | harness sadakati; ≥ 30 pozisyon; `missing_bars = 0` ve `unchecked_position_bars = 0` |
| **C-1** | ortalama R | **> 0** |
| **CI** | ortalama R'nin KÜME aralığı | bağlayıcı alt sınır (§6j > 8) **> 0** |
| **E** | kontrol farkı | `avg_r − dc_coinflip.avg_r ≥ 0.15R` **ve** farkın KÜME aralığının bağlayıcı alt sınırı **> 0** |
| **C-3** | çıpa | hesap getirisi `buyhold`u geçer |
| **C-5** | OOS | C-1, CI, E ve C-3 **dönem B'de de** sağlanır |
| **K-1** | coin başına | dönem B'de **≥ 6 coinde PF > 1.1** (tek-sembollü koşulardan — model sembol bazında bağımsız olduğu için tanımlı; `xsec`ten farkı budur) |
| **K-3** | drawdown | portföy koşusunun hesap düzeyi `max_drawdown` **≤ %25**, iki dönemde de |

Kapıların i.i.d. dışındaki kısımları (`avg_r`, kontrolün `avg_r`i, marj, çıpa, örneklemler)
`core/metrics.py::acceptance_flags`in YAYINLADIĞI sayılardan okunur; harness yalnızca iki
CI koşulunu küme aralığıyla DEĞİŞTİRİR. `acceptance_flags`in kendi i.i.d. `edge` bayrağı
raporlanır ama bağlayıcı değildir.

**Raporlanan, bağlayıcı DEĞİL:** K-2 toplam işlem (§6g > TADİLAT-1'in gerekçesi: bağlayıcı
kapılar CI bazlı ve örneklemi içeriyor) · i.i.d. aralıklar · DEFF, n_etkin, MDE ·
huni (§6j > 6) · `cross_not_visible` payı.

**Tutarlılık kontrolleri (hipotez değil, `wave_coinflip`in S1/S2/M1'i):**

| # | Ölçüm | Kural |
|---|---|---|
| **S1** | `avg_stop_distance_pct` bağıl farkı, `dc_short` ↔ `dc_coinflip` | **< %10.** Aşarsa **E kapısı OKUNMAZ** (geçilmiş sayılmaz) ve sebebi yazılır |
| **S2** | kontrolün açılan pozisyonlarında `coin=flipped` payı | **0.5 ± 0.05** |
| **M1** | kontrolün ort. R'sinin KÜME aralığı | **−`cost_per_r`'yi kapsar** (bilgisiz yön ≈ −maliyet); kapsamıyorsa önce yansıtma araştırılır |

⚠ **C-4 (⚠B bandı) bu katmanda YAPISAL olarak boştur:** yarışmacılar yalnızca `dc_short`
ve `dc_coinflip`tir ve stop mesafeleri aynalama gereği eşittir — band medyanı kendileridir.
Bilgi taşıyan hâli S1'dir; rapor C-4'ü "geçti" diye OKUTMAZ.

**Çıpa istisnası — `ema_trend`/`xsec`dekiyle birebir:** model YALNIZCA C-3'ten kalıyor ve
diğer TÜM bağlayıcı kapıları geçiyorsa koşu **DURUR**, karar kullanıcıya gider, otomatik
geçiş YOKTUR. Başka herhangi bir kapıdan kalırsa **BLOKE**. İstisna tek kapı içindir.

**Karar sırası (mekanik, `evaluate_gates`):** (1) geçerlilik ve küme tabanı — sağlanmazsa
DEĞERLENDİRİLEMEZ; (2) S1 — aşılırsa E okunmaz; (3) bağlayıcı kapılar; (4) çıpa istisnası.

### 11. ÖN-KAYITLI TAHMİNLER (sonucu görmeden)

| # | Ölçüm | Tahmin | Çürütür |
|---|---|---|---|
| **P1** *(birincil)* | dönem A ortalama R | **> 0** (2022 ayı yılı short'ları destekler) | ≤ 0 |
| **P2** | rejim bağımlılığı | dönem B ort. R **<** dönem A ort. R | ≥ |
| **P3** | bağımlılığın büyüklüğü | dönem A, `dc_short` ort. R: `max(genişlik_regime, genişlik_month) / genişlik_iid` **> 1.5** | ≤ 1.5 |
| **P4** | çıpa | dönem A'da hesap getirisi `buyhold`un **altında**, yani C-3 düşer | çıpayı geçerse |

**P1 riskli bir tahmindir** ve önsel kanıt ona karşıdır (§6j > 1). **P3 i.i.d. aralığın
ne kadar yanılttığının ölçüsüdür:** tutmazsa bağımlılık beklenenden zayıftır — bu da bir
bulgudur ve küme kuralını gevşetmez. **P4 bir paradoks değildir:** short-only bir model
dönem A'da ortalama R'de kazanırken, sermayenin tamamını piyasada tutan çıpaya getiride
yetişemeyebilir (dönem A'nın sonu 2023–24 boğasıdır).

### 12. Koşu kuralları

- **Parametre araması YOK.** Tek tanım, tek koşu. EMA periyotları (50/200), ısınma (600),
  tavan (6.0), kota (5), görüş penceresi (`dc.lookback_bars` 3000 — TADİLAT-1), tohum ve
  küme tanımları bu commit'te sabittir. Backtest derinliği (`--history-bars 12000`) bir
  §5b 1. sınıf ayarıdır ve modelin gördüğünü değiştirmez.
- **Kapıdan kalırsa ayar aranmaz; ChopZone ekleyerek kurtarılmaz.** Düşen tez sicilde
  kalır (§6c).
- **Dönem B'ye koşu öncesi DOKUNULMAZ.**
- **Tek workflow tetiklemesi, tek sonuç dosyası.** Sıra betiğin içindedir: A (portföy +
  13 tek-sembollü) → embargo A'dan ÖLÇÜLÜR → B (portföy + 13 tek-sembollü).
- **Tek tohum** (§6j > 4).

### 13. Koşu sonrası ZORUNLU rapor

Çıkış sebebi dağılımı (stop / hedef / likidasyon — `exit_rule`) · tutuş süresi dağılımı ·
**hedef R çarpanı dağılımı** (`rr=` etiketinden: medyan, p25, p75, p90) **ve `rr < 1.0` ile
açılan pozisyonların SAYISI ve PAYI** (§6j > 3; filtre değil, geometrinin ölçüsü) · sembol
ve YIL kırılımı · huni (§6j > 6) · `target_undefined` sayısı · `cross_not_visible` payı ·
gerçekleşen pozisyon sayısı ↔
projeksiyon · gerçekleşen sd, DEFF, n_etkin ↔ projeksiyon · iki küme tanımının küme
sayıları, aralıkları ve genişlikleri · atılan bootstrap çekilişi sayısı · S1/S2/M1.

### 14. Çoklu karşılaştırma

Sicilde (§6c) **6. satır.** Hipotez dış kökenlidir (eğitim görseli), ev içi arama
uzayından seçilmedi — §6c'nin kuralına göre **dış kökenli paydaya** girer ve o payda
**2 → 3** olur (`ema_trend`, `wave_scalp`, `dc_short`). Ev içi BH paydası değişmez.

### 15. Bu ön-kayıt neyi SEÇMİYOR

Canlıya alınmayı. `dc` katmanının tetikleyicisi yoktur; kapılar geçilse bile `run-dc.yml`
ayrı bir karardır — `ema` ve `xsec` katmanlarının bugünkü statüsü.

## 6k. ÖN-KAYIT — TimesFM 2.5 (zero-shot) YÖN İSABETİ: harici bir tahmin modelinin salt okunur araştırması *(2026-09-23)*

**Bu belge koşudan ÖNCE yazıldı ve AYRI bir commit olarak işlendi** — ölçüm betiği,
workflow ve testler SONRAKİ commit'lerdedir. §7'nin tamamı bu bölüme uygulanır.

### 1. Köken, statü, kapsam

**Köken dıştır:** `klonnist/hemstir` — Google TimesFM 2.5 (200M, zero-shot, yeniden
eğitim yok) ile OKX `<COIN>-USDT-SWAP` 1H kapanışlarından 48 saatlik tahmin üreten ve
`docs/forecasts.json`'ı 6 saatte bir `main`'e commit'leyen bir sistem. Yön kuralı:
48. saat tahmini ≥ son kapanış → BUY, değilse SELL.

**Statü: MODEL DEĞİL, salt okunur bir araştırma.** Bu ön-kayıt hiçbir modeli `REGISTRY`e,
hiçbir katmanın `models` listesine, hiçbir deftere sokmaz; `config.yaml`, `strategies/`,
`core/` ve defterler DEĞİŞMEZ. Cevaplanan soru tek: *TimesFM'in 48 saatlik yön tahmini,
aynı noktalarda üç basit kuraldan daha isabetli mi?* Geçse bile bir model önerisi ayrı bir
ön-kayıtla gelir (§6k > 12).

### 2. Körlük beyanı (sonuç görülmeden yazıldı)

- **Ön-kaydı yazan taraf hemstir'in sonuç dosyalarını görmedi; incelenen README sürümü
  sonuç içermiyordu.** (Kullanıcının beyanı: README'ye bir kez bakıldı — dosya listesi,
  sinyal kuralı, coinler, lisans, Actions açıklaması; hiçbir isabet rakamı yoktu.
  `evaluation.json`, `backtest.json`, `research_locked.json` açılmadı. Kodu yazan ajan da
  bu dosyaları ve README'nin sonuç bölümlerini açmadı; yalnızca `generate_forecasts.py`,
  `okx_client.py`, `requirements.txt` ve `docs/forecasts.json`'ın ŞEMASI — fiyat alanları
  maskelenerek — okundu.)
- **Ferhat'ın beyanı:** hemstir sonuç dosyalarını okuyup okumadığını ayrıca kendisi
  söyleyecek; beyan gelene kadar bu satır **AÇIK**tır ve sonuç raporunda olduğu gibi
  yazılır.
- **hemstir'in sonuç dosyaları** (`docs/evaluation.json`, `docs/backtest.json`,
  `docs/research_dev.json`, `docs/research_locked.json`, `docs/locked_test_run.json`,
  `docs/diagnostics.json`, README'nin sonuç bölümleri) **bizim sonucumuz bu belgeye
  kayda geçene kadar AÇILMAZ.** Sonra açılırlarsa bağımsız bir çapraz kontroldür; önce
  açılırlarsa ön-kaydı kirletir. Ölçüm betiği bu dosyaları okumaz (test sınar).

### 3. İŞ 1 — git geçmişi sayımı ve kapı (koşudan önce görülen TEK veri)

Kapı sayım görülmeden sabitlendi: **çakışmasız 48 saatlik pencere sayısı < 200 ise git
geçmişi tek başına değerlendirilmez** (%60 isabeti %50'den ayırmak ~200, %55 için ~800
bağımsız gözlem ister). Sayım (2026-09-23, hemstir HEAD `f6ae0f4`) — gerçekleşen
fiyatlarla hiçbir karşılaştırma yapılmadı:

| | |
|---|---|
| `docs/forecasts.json` commit'i | 54 (2026-09-12 20:23Z → 2026-09-23 06:09Z, ~10.4 gün) |
| coin × tahmin serisi | 713 (4×10 + 47×14 + 3×5) |
| Coin listesi | 10 → 14 (09-13 07:34; ETHFI, CRV, NEAR, BNB) → 5 (09-22 18:09; BTC ETH XRP SOL AVAX) |
| Enstrüman | 09-13 07:39'a kadar karışık spot/SWAP, sonra tamamı `-USDT-SWAP` |
| Şema | `generated_at, model, checkpoint, bar, context_hours=300, horizon_hours=48`; coin başına `inst_id`, `history[300]{ts,close}`, `forecast[48]{ts,value,lower,upper}`, `signal{side,strength,entry_price,atr,expected_move(_pct),take_profit,stop_loss,risk_reward}` (ilk commit'te `signal` yok) |
| Çakışmasız 48s çapa | 6 (5'i olgun) → 66 coin-pencere, ema evreniyle kesişimde 52; **etkin küme ≤ 5** |

**KAPI GEÇİLMEDİ.** Git geçmişi yalnızca PARİTE KONTROLÜ olur (§6k > 5); değerlendirme
geçmiş veride TimesFM'i bizim koşmamızla yapılır.

### 4. Değerlendirme tanımı

**Evren:** `ema` katmanının 13 sembolü ∩ hemstir'in tüm-zaman listesi = **11 sembol:**
BTC ETH SOL XRP DOGE BNB AVAX LINK ADA NEAR ETHFI (`-USDT-SWAP`). Dışarıda kalanlar:
hemstir'de DOT/LTC/CRV, `ema`da SUI/PENGU. Sembol, çapa anında gerekli barlara sahip
değilse (listelenmemiş: ETHFI dönem A'nın büyük kısmında) o çapada düşer ve SAYILIR.

**Veri:** OKX 1H, yalnızca KAPANMIŞ barlar (kural 12). Ölçüm betiği `data/cache/`e
yazmaz; OKX bu oturumda erişilebilir değilse veri CI workflow'unda çekilir.

**Çapa ızgarası (sabit, sonuçtan bağımsız):** OKX açılış damgası `T_k = 2022-01-01T00:00Z
+ k × 48s`. Çapa barı açılış damgası `T` olan 1H bardır; **son kapanış** `c₀ = close(T)`,
**gerçekleşen** `c₄₈ = close(T + 48s)` (tam 48 bar ileride; hemstir'in `forecast[47]`
damgası `last_ts + 48s` ile aynı indeks). **Girdi:** açılış damgası `T−299s … T` olan
300 kapanış. Girdide ya da hedefte eksik bar varsa (sembol, çapa) düşer ve sayılır —
doldurma/ara değer yok.

**Dört yön tahmini (aynı çapalar, aynı semboller):**
- **TimesFM:** `ŷ₄₈ ≥ c₀` → yukarı, değilse aşağı (hemstir'in `>=` kuralı, parite gereği).
- **(a) hep yukarı.**
- **(b) momentum:** `c₀ ≥ close(T − 48s)` → yukarı, değilse aşağı (eşitlik kuralı TimesFM'le aynı).
- **(c) yazı-tura:** tohum `"{random_seed}:{T}:{sembol}:timesfm_coin"` — (sembol, çapa)
  başına bağımsız, koşudan koşuya aynı.

**İsabet:** `sign(tahmin) == sign(c₄₈ − c₀)`. `c₄₈ == c₀` olan gözlem DÖRT kuraldan da
aynı anda düşer ve sayılır.

**TimesFM çağrısı hemstir'le BİREBİR:** `timesfm[torch]==2.0.2`,
`google/timesfm-2.5-200m-pytorch`, `TimesFM_2p5_200M_torch.from_pretrained(...,
torch_compile=False)`, `ForecastConfig(max_context=300, max_horizon=48,
normalize_inputs=True, use_continuous_quantile_head=True, force_flip_invariance=True,
infer_is_positive=True, fix_quantile_crossing=True)`. Checkpoint REVİZYONU (HF commit
sha'sı) ve `torch` sürümü koşu manifestine yazılır. Bağımlılık YALNIZCA workflow'da
kurulur; `requirements.txt` değişmez.

### 5. Parite kapıları (değerlendirmeden ÖNCE; tutmazsa hiçbir sayı okunmaz)

- **P0 — model paritesi.** hemstir'in commit'lediği her serinin KENDİ `history[300]`
  girdisi bizim çıkarımımıza verilir; `forecast[0..47].value`, `lower`, `upper` yeniden
  üretilmelidir. Tolerans: her noktada `|bizim − commit| / |commit| ≤ 1e-4` ve `side`
  %100 aynı. Tek istisna: `|expected_move| / entry_price < 1e-4` olan serilerde `side`
  sayısal gürültüyle dönebilir — bunlar ayrıca SAYILIR ve istisnaya yalnızca onlar girer.
  Kapsam: `forecast` taşıyan 54 commit'in tamamı (girdi dosyadan geldiği için enstrüman
  türü ve kapanmamış bar P0'ı etkilemez).
- **P1 — veri paritesi.** Yalnızca `inst_id` `-USDT-SWAP` olan ve evrendeki serilerde,
  bizim OKX 1H kapanışlarımız commit'lenmiş `history`nin ilk **299** barıyla BİREBİR
  eşit olmalıdır. **Son bar (indeks 299) HARİÇ** — bkz. §6k > 6, S1. P1 dönem B/C'ye
  düşen damgalarda fiyat okur ama kapanışı kapanışla karşılaştırır; tahminle gerçekleşen
  karşılaştırılmaz.

### 6. Kabul edilen sapmalar

- **S1 — kapanmamış bar.** Canlı hemstir OKX `/market/candles`'ın `confirm` alanını
  filtrelemez: tahmin saatin ~9. dakikasında üretilir ve girdinin son satırı OLUŞMAKTA
  olan barın o anki fiyatıdır. Bizim sürüm kural 12 gereği yalnızca kapanmış barı
  kullanır. **Backtest, canlı hemstir'den hafifçe farklı ve daha temiz bir modeli ölçer.**
  P0 hemstir'in kendi girdisini kullandığı için bu sapmadan etkilenmez; P1'de son bar
  hariç tutulur.
- **S2 — çapa fazı.** Izgara 00:00Z'ye sabittir (her 48 saatte bir, hep aynı saat);
  hemstir 00/06/12/18Z'de koşar. Sabit faz seçim serbestliğini kapatır; bedeli gün içi
  mevsimselliğin tek fazdan okunmasıdır ve bu yazılır.

### 7. Dönemler ve Katman C (sızıntı kapısı)

| Dönem | Çapa aralığı (`T`) | Not |
|---|---|---|
| **A** | 2022-01-01 → 2024-06-30 (`backtest_ema.PERIOD_A_START/CUTOFF`'tan İTHAL) | hedef 2024-07-02'ye kadar uzanır |
| **B** | 2024-07-02 → son olgun çapa (`T + 48s` ≤ son kapanmış bar) | embargo = ufuk = 48s; ufuk SABİT olduğu için ölçülmez |
| **C** | `C_start` → son olgun çapa | **B'nin alt kümesidir**; ayrı ve bağlayıcı |

**Katman C'nin gerekçesi — bu ön-kaydın en önemli kapısı.** Sıfırdan eğitilmemiş bir temel
modelde asıl tehdit sızıntıdır: 2022–2024 kripto serileri ön-eğitim verisindeyse model o
dönemi tahmin etmiyor, HATIRLIYOR olabilir. A'nın tamamı ve B'nin büyük kısmı bu riski
taşır; modelin görmüş olamayacağı tek veri checkpoint yayınından sonrasıdır.

**`C_start` — kaynağıyla, koşudan önce:** resmi `google-research/timesfm` README'si,
*"Update - Sept. 15, 2025 — TimesFM 2.5 is out!"* (depo HEAD `e31dadd`, 2026-09-15;
2.5 kodunun ilk commit'i `7d8f3d9`, 2025-09-12). **`C_start = 2025-09-15T00:00Z`**,
ızgaranın o tarihe eşit ya da sonraki ilk çapası. ⚠ HF model kartı bu oturumda
erişilemedi (proxy reddi); bu yüzden kural MEKANİKTİR: workflow, koşunun kullandığı
checkpoint revizyonunun HF commit tarihini okur ve manifest'e yazar; **o tarih
2025-09-15'ten SONRAysa `C_start` o tarihe kayar** (ağırlıklar yayından sonra
değişmişse, sızıntı sınırı yeni ağırlıkların tarihidir). Geriye kaymaz.

### 8. Geçme koşulu (ön-kayıtlı, bağlayıcı)

Her dönem d ∈ {A, B, C} ve her basit kural r ∈ {(a), (b), (c)} için:

`Δ_{d,r} = isabet(TimesFM) − isabet(r)` > 0 **ve** Δ'nın küme bootstrap aralığının ALT
SINIRI > 0.

**Hipotez yalnızca dokuz koşulun TAMAMI sağlanırsa geçer.** Üç kurala AYRI AYRI karşı
test edilir ("o dönemin en iyi kuralı" seçilmez — seçim yanlılığını geri getirirdi);
kesişim-birleşim testi olduğu için çoklu karşılaştırma düzeltmesi gerekmez.

**Küme bootstrap:** küme = çapa damgası `T` (aynı anda 11 coin korelasyonludur). Çapalar
yerine koymalı yeniden örneklenir; her örneklemde Δ = Σ(isabet farkı) / Σ(gözlem) —
EŞLEŞTİRİLMİŞ (TimesFM ve kural aynı çapa-sembol çiftlerinde; `backtest_dc`'nin
gerekçesi). Yüzdelik aralık, `α = acceptance.edge_ci_alpha` (0.05, iki yanlı),
`acceptance.bootstrap_samples` (2000) örnek, tohum `random_seed`den türer. Dönemde
**< 10 küme** → değerlendirilemez ve geçilmiş SAYILMAZ.

**Sıra:** P0 → P1 → A → (A geçerse) B ve C. A'daki dokuz koşuldan biri düşerse hipotez
düşmüştür ve B/C koşulmaz — B'yi gereksiz yere görmek sonraki bir ön-kaydın OOS
penceresini harcar.

### 9. Güç — sonuç görülmeden, dürüst

Etkin gözlem `n_eff = K × m / (1 + (m−1)ρ)` (K çapa, m sembol, ρ çapa-içi korelasyon).
Saptanabilir en küçük fark (%80 güç, α 0.05 iki yanlı): isabet için `2.802 × 0.5 / √n_eff`;
**asıl test olan eşleştirilmiş fark için `2.802 × √(δ / n_eff)`**, δ = iki kuralın
ayrıştığı gözlem payı.

| Dönem | K | m | ρ = 1 (n_eff = K) | ρ = 0.8 | ρ = 0.5 |
|---|---|---|---|---|---|
| A | 456 | ~10 | %50'ye karşı 6.6 pp; fark δ=0.3 / 0.5: 7.2 / 9.3 pp | 5.9; 6.5 / 8.4 | 4.9; 5.3 / 6.9 |
| B | 406 | 11 | 7.0; 7.6 / 9.8 | 6.3; 6.9 / 8.9 | 5.1; 5.6 / 7.3 |
| **C** | **186** | 11 | **10.3; 11.3 / 14.5** | **9.3; 10.2 / 13.1** | **7.6; 8.3 / 10.7** |

(K'ler 2026-09-21'e kadar olan olgun çapalardır; koşu günü B ve C'yi birkaç çapa uzatır.)

**Okuma:** **%55 isabetli bir model bu tasarımla hiçbir dönemde ayırt edilemez.** C'de
ayırt edilebilir fark ~8–15 puandır; yani C, gerçek ama mütevazı bir kenarı
"ayırt edilemedi" diye engelleyebilir. **Bu bedel kabul edildi:** görmediği veride
çalıştığı gösterilemeyen bir modele güvenilemez. Kripto 48 saatlik yönlerinde ρ'nun
yüksek (0.5 ve üstü) olması beklenir, yani gerçekçi satırlar sağ sütunlar değil orta ve
sol sütunlardır. Koşudan sonra her Δ'nın yanında GERÇEKLEŞEN küme SE'si ve ondan
türeyen MDE (`2.802 × SE_küme`) raporlanır — okuma yardımıdır, kapı değildir.

### 10. Rapor (koşudan sonra, hepsi)

Dönem başına: çapa sayısı, (sembol, çapa) gözlemi, düşenler (sebep koduyla: listelenmemiş,
eksik bar, sıfır hareket), dört kuralın isabeti, üç Δ ve aralıkları, gerçekleşen SE/MDE,
TimesFM'in yukarı-tahmin payı ve **gerçekleşen yukarı hareket payı** (boğa/ayı zemini —
"hep yukarı"nın neden iyi ya da kötü olduğunu gösterir). P0/P1 sayıları, checkpoint
revizyonu ve HF tarihi, `C_start`, `torch` sürümü.

### 11. Sicil

§6c'de **7. satır.** Hipotez dış kökenlidir (`klonnist/hemstir`), ev içi arama uzayından
seçilmedi — **dış kökenli paydaya** girer: **3 → 4** (`ema_trend`, `wave_scalp`,
`dc_short`, `timesfm_direction`). Ev içi BH paydası değişmez. Bir modelin DEFTER
performansı değil bir tahmincinin yön isabeti hakkında bir iddiadır; sicile yine de girer,
çünkü sicilin saydığı şey denenen hipotezlerdir.

### 12. Bu ön-kayıt neyi SEÇMİYOR

Bir modeli. Geçerse bile TimesFM'e dayanan bir strateji kendi ön-kaydıyla gelir (boyut,
stop, maliyet, kontrol, ⚠B — isabet bir R değildir ve maliyet öncesidir). Düşerse satır
sicilde kalır ve hemstir'in sonuç dosyaları çapraz kontrol olarak ancak o zaman açılır.
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
- **§6f'nin C kapısının GEÇMESİ, iki serinin AYNI olduğu anlamına gelmez.** Geçmesi
  "olay kümeleri bu örneklemde AYIRT EDİLEMİYOR" demektir — 60 günde sembol başına ~180
  damga, p95 ≈ 9 olay, 13 sembolde ≈ 117 olay, ve `Jaccard ≥ 0.70` her iki tarafta ~21
  olayın kaymasına izin verir. Bu satır buraya, ileride birinin kapıyı "arşiv
  doğrulandı" diye okuyacağı için yazıldı: kapı bir doğrulama değil, bir AYIRT
  EDİLEMEZLİK ölçüsüdür ve örneklemi küçüktür.
- **Cross-venue bir arşivle seçilen bir eşiğin OKX'te de aynı olayları seçtiği**
  (§6f > Adım C). Gösterilen şey yalnızca ayırt edilemediğidir; "aynı oran" şartı başka
  bir borsada anlamsızdır ve bu sapma arşiv kullanılırsa §5'e yazılır. Satır arşiv
  gelmeden önce buraya kondu — sonradan eklenseydi, sonucu gördükten sonra yazılmış bir
  uyarı olurdu.
  ⚠ **TETİKLENMEDİ (2026-09-20):** A-2 geçti, arşiv OKX-içidir ve cross-venue muafiyeti
  KULLANILMADI — §5'e sapma satırı eklenmedi. Bu satır yine de KALDIRILMAZ: ön-kayıtlı
  bir uyarıyı sonucu gördükten sonra silmek, §7.5'in düşen hipotezi sicilden silme
  yasağının aynısıdır. Portal yolu bir gün tıkanır ve Adım B'ye dönülürse satır zaten
  yerindedir.

---

## 9. Koşunun kendisi denetlenebilir olmalı

Her backtest koşusu çıktısına şunları yazar: katman, pencere (başlangıç/bitiş), model
listesi, **config parmak izi** (koşuda geçerli tüm ayarların özeti) ve harness sürümü
(git SHA). Aynı girdilerle tekrar koşulduğunda aynı sonucu vermelidir — `random_seed`
sabittir ve rastgelelik kullanan her yol ondan beslenir.

Sonuç dosyaları `backtests/` altına yazılır ve **depoya girmez**: bir backtest ölçümün
kendisi değil, ölçüm hakkında bir denemedir. Karara giren sayılar bu belgeye ve
`docs/decisions.md`'ye yazılır.

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

**3. satır BH paydasına GİRMEZ ve bu bir muafiyet değil bir tanımdır:** hipotez bir
model koşusuna hiç dönüşmedi, yani ortada düzeltilecek bir `p` değeri yok. Satırın
sicilde durmasının sebebi paydanın kendisi değil, **kaç denemenin yapıldığının
görünmesidir** — düşen bir denemeyi silmek, sicilin engellemek için var olduğu yayın
yanlılığının ta kendisidir.

**Sicildeki 2. satır bu paydaya AİT DEĞİLDİR:** `ema_trend` hipotezi dış bir sistemden geldi, ev içi arama uzayından seçilmedi (bkz. §6d > Çoklu karşılaştırma). İki payda ayrı tutulur.

**Sicildeki 5. satır (`wave_scalp`) da bu paydaya AİT DEĞİLDİR** ve gerekçesi 2. satırın
aynısıdır: hipotez dış bir sistemden (`klonnist/Hasanwavebot`, `15m` profili) geldi, ev içi
arama uzayından seçilmedi. Böylece **dış kökenli payda bugün 2'dir** (`ema_trend`,
`wave_scalp`) ve ev içi paydadan ayrı tutulur. ⚠ `wave_scalp`in Aşama 2'de türetilecek
VARYANTI dış kökenli DEĞİLDİR — dönem A teşhislerinden çıkacaktır, yani ev içi paydaya
girer ve kendi satırını açar (§6h > 13).

**Sicildeki 4. satır (`xsec_mom`) BH paydasına GİRER.** Gerekçe 2. satırın tersidir: bu
hipotez dış bir sistemden gelmedi, ev içi bir tezdir — yani "kaç deneme yapıldı"
sayacının saydığı türdendir. Satır koşudan ÖNCE açıldı ve sonucu ne olursa olsun burada
kalacaktır.

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

⚠ **BİZİM ÇEKME YOLUMUZUN derinliği ayrı bir sayıdır.** Harness'ın varsayılanı
**60.000 bardır** (emsali `backtest_ema`nın 4H'deki 12.000'i) ve bu ayrım yazılı durmak
zorunda, çünkü yetersiz bir derinlikte kapsam kapısı düşer ve teşhis YANILTICI olur: rapor
"OKX veriyi vermiyor" derken aslında *"biz o kadar geriye istemedik"* demiş olurdu. Kapı
düştüğünde bakılacak İLK şey bu sayıdır; ikinci şey borsanın kendi derinliğidir (karar
50'nin fonlama uç noktasında ölçtüğü şeyin mum tarafındaki karşılığı).

**DÜZELTME — `history_bars`ın ne saydığı ÖLÇÜLDÜ ve ilk okuma ÇÜRÜDÜ** *(2026-09-22)*

⚠ Bu paragraf bir zamanlar şunu diyordu: *"`core/data.py::_download_candles` barları ŞU
ANDAN geriye sayfalar ve `data.history_bars` kadar bar toplayınca durur — pencerenin başına
ATLAMAZ. Yani derinlik, koşu gününden dönem A'nın başına kadarki TÜM mesafeyi kapsamalıdır:
570 gün × 96 ≈ 54.720 bar."* **O okuma yanlıştı ve ölçümle çürüdü.**

Gerçek mekanizma: `scripts/backtest.py` anlık görüntüyü `load_market_data(..., now=min(end,
now))` ile kurar, yani `now` PENCERENİN SONUDUR. `_parse_candle` `now`dan yeni barları atar
ve `_download_candles`ın `max_bars` sayacı yalnızca `now`dan ESKİ barları sayar. Yani
`history_bars` **pencerenin sonunda biten bar sayısıdır**, bugünden geriye olan mesafe
DEĞİL. Bugüne kadarki mesafe yine de sayfa sayfa gezilir (ve atılır), ama bu bir DERİNLİK
ayarı değil sabit bir ÜCRETTİR.

**Kanıt** (koşu #35701959605, 2026-09-22): `history_bars=2000` ve `end=2025-03-08` ile koşan
probe 2025-03-07T21:15 ve 21:30 barlarını İŞLEDİ. İlk okuma doğru olsaydı 2000 bar yalnızca
2026-09'a yeterdi ve pencerede tek bar bulunmazdı.

**Doğru gereksinim** pencere uzunluğu + ısınmadır:

> 2025-03-01 → 2026-02-28 = 364 gün × 96 ≈ **34.944 bar**, artı zigzag penceresi (300) ve
> ATR ısınması (14) ≈ **35.300**.

**Sayı DEĞİŞMİYOR: 60.000 hâlâ yeterli ve fazlasıyla paylı.** Değişen yalnızca gerekçedir ve
düzeltilmesi zorunluydu: bir ön-kayıtta yanlış bir gerekçe, doğru bir sayıdan daha
tehlikelidir — sonraki okuyucu o gerekçeye dayanarak başka bir pencere için yanlış bir
derinlik seçer. Düzeltme HİÇBİR sonuç görülmeden yapıldı (dönem A koşulmamıştı) ve §7'nin
kapsamına girmez; §6f'nin "~300-400 kayıtlık sayfalama tavanı" okumasının çürütülmesiyle
aynı statüde bir kayıttır.

⚠ **Sabit ücretin bedeli ZAMANDIR ve ölçüldü:** bir HAFTALIK pencere için bile koşu 24
dakika sürdü, çünkü 13 sembolün her biri için bugünden 2025-03'e kadar ~550 sayfa gezilip
atıldı. `backtest.yml`in 45 dakikalık timeout'u bu yüzden 300'e çıkarıldı.

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

#### NOT — koşu MEKANİĞİ değişti, ölçüm değişmedi *(2026-09-22)*

İlk koşu denemesi (#35705966047) **runner kaybıyla** düştü (`The runner has received a
shutdown signal`), 48. dakikada ve pencerenin ilk çeyreğinde. Bu bir kod hatası değil,
altyapı kaybıdır. O koşudan ÖĞRENİLEN İKİ ŞEY kayda geçiyor:

**1. Model ve kontrol artık TEK koşuda, birlikte çalışır.** Önce ayrı iki koşu yapılıyordu
(`A` ve `A-control`). Gerekçe üç katlı ve hiçbiri sonuca bakmıyor:

- **Sonuç AYNIDIR.** Modeller izoledir (kural 4), portföy her model için ayrı hesap durumu
  tutar (kural 7) ve kotalar model başınadır; `random_seed` sabittir. Yani
  `wave_coinflip`in eklenmesi `wave_scalp`in tek bir satırını dahi değiştirmez.
- **EK-1'in kendi ilkesi.** "Dönem B'de kontrol, sadık sürüm ve varyant AYNI koşuda
  çalışır" kuralı yukarıda yazılıydı; dönem A'da da öyle yapmak onu erken uygular ve
  *"`wave_scalp` YENİDEN KOŞULMAZ"* şartını kendiliğinden sağlar.
- **Bütçe ÖLÇÜLDÜ.** Tek eşleştirilmiş koşu ≈ **112 dakika** (veri çekimi ~25 dk + 34.944
  bar × 2 model motor zamanı). Ayrı koşular bunu ikiye katlıyordu ve workflow'un 180
  dakikalık tavanına sığmıyordu; tavan 300'e çıkarıldı.

**2. `zero_size` retleri GÖRÜLDÜ ve raporlanacak.** Düşen koşunun logunda, pencerenin
3. ayında (2025-06-05) `wave_scalp` için şu satırlar var:

> `WARNING core.engine: wave_scalp ETH-USDT-SWAP long açılmadı [zero_size]: boyut sıfır
> (sermaye/nakit kalmadı)` — aynı barda ETH, AVAX ve ETHFI için.

`zero_size` `core/portfolio.py::SIZING_FAILURES` kümesindedir ve CLAUDE.md'nin
*"bakılması gereken tek grup"*udur. Burada bir harness arızası DEĞİL, bir **ölçüm
sonucudur**: hesap tükenmiş. Bu yüzden rapora iki alan eklendi (`rejections` ve
`sizing_failures`) — bir modelin sermayesini yakması, ortalama R'nin yanında görünmek
zorundadır.

⚠ **Aynı logda kombinasyon posteriorları da görüldü** (her iki model için, pencerenin
1/4'ünde) ve bunlar bir SONUÇ DEĞİLDİR: tur ortası ara değerlerdir, kapanmış örneklem
değil. Görüldükleri yine de kayda geçiyor (yukarıdaki bir haftalık probe ile aynı
gerekçe) ve hiçbir tahminin okunmasında kullanılmayacaktır. Özellikle: kontrolün bazı
hücrelerinin o anda modelden yüksek görünmesi C-2 hakkında bir şey SÖYLEMEZ ve öyle
okunmayacaktır — C-2 yalnızca tamamlanmış koşunun kabul bayrağından okunur.

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

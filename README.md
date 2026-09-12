# crypto-paper-bot

10 farklı strateji modelinin aynı kripto piyasa verisini görüp kendi izole sanal hesabıyla
paper-trading yaptığı bir **ölçüm projesi.** Amaç kâr etmek değil, stratejileri adil ve
tekrarlanabilir koşullarda kıyaslamaktır. Kurallar ve mimari için bkz. [`CLAUDE.md`](./CLAUDE.md).

## Nasıl çalışır (özet)

- `core/engine.py` her turda tüm stratejileri aynı `MarketData` anlık görüntüsüyle çağırır.
- Her strateji yalnızca `Signal` (yön + giriş/çıkış seviyeleri) üretir; defter yazımı,
  komisyon/funding hesabı ve pozisyon boyutlandırma her zaman `core/` içinde, tüm modeller
  için aynı kurallarla yapılır.
- Sinyal üretildiği barda dolmaz: **bir sonraki barın açılışından** dolar (kural 13). Bu
  yüzden emirler koşular arasında defterde bekler.
- Her barda açık pozisyonlar şu sırayla kontrol edilir: **likidasyon → stop → take-profit.**
  Stop ve TP aynı mumun aralığındaysa kötü olan (stop) gerçekleşmiş varsayılır.
- Her stratejinin işlemleri kendi defterine (`ledgers/`) yazılır ve `core/metrics.py`
  tarafından karşılaştırılır.

## Çalıştırma

```bash
python main.py              # bir tur: veri çek -> modelleri koştur -> defteri ve metrikleri yaz
python main.py --dry-run    # deftere ve docs/data/metrics.json'a YAZMADAN aynı turu raporla
```

`main.py` bir turu uçtan uca yürütür: veriyi çeker, `as_of`'u belirler, `config.yaml`'daki
modelleri sırayla koşturur, metrikleri üretir ve `docs/data/metrics.json`'a yazar. `--dry-run`
defterin bir kopyası üzerinde çalışır — motor turu ilerletirken durumu yazdığı için "yazmayan
motor" metrikleri üretecek satırları hiç oluşturmazdı.

Hata izolasyonu kasten iki katmanlıdır: **model kurulumu** (tanınmayan ad, kurucu hatası) ve
**model kodu** (sinyal/çıkış üretimi) izole edilir — biri düşse de diğer modeller koşar. Tanınmayan
bir model koşuyu ayrıca hata koduyla bitirir; sessizce eksik yarışan bir küme kural 6'yı bozardı.
**Defter ve veri hataları izole EDİLMEZ:** bozuk defter ya da bayat anlık görüntü turu tümden
düşürür — bunlar model hatası değil ölçüm hatasıdır.

`.github/workflows/run.yml` bunu `5 0,4,8,12,16,20 * * *` cron'uyla çalıştırır: 4H barlar
00/04/08/12/16/20 UTC'de kapanır, 5 dakikalık pay hem kapanmamış barı beklemeye hem GitHub
cron gecikmesine yeter. Koşudan sonra `ledgers/` ve `docs/data/` commit edilir (değişiklik
yoksa boş commit atılmaz). **Defter bu yüzden depoya girer:** runner her koşuda sıfırdan
kurulur, commit edilmezse her tur boş bakiyeyle başlar ve ölçüm hiç birikmez.

Her tur `docs/data/metrics.json`'a bir **tur raporu** da yazar: model başına işlenen bar,
dolum, kapanan işlem, sinyal sayısı ve **doldurulamayan emirlerin sebep kodu dökümü**
(`rejections`). Bu döküm opsiyonel değil: "sinyal üretildi ama işlem açılmadı" iki bambaşka
şeyin aynı görünümüdür — beklenen bir tekrar (`duplicate_position`, örn. alım-tut çıpasının
zaten taşıdığı pozisyon) ile gerçek bir boyutlandırma arızası (`zero_size`,
`insufficient_cash`). Kod olmadan ikisi aylar sonra ayırt edilemez. Log seviyesi de aynı
ayrımı taşır: beklenen tekrar `INFO`, arıza `WARNING`.

## Dashboard (GitHub Pages)

`docs/index.html` statik bir tek sayfadır: harici framework yok, CDN yok, build adımı yok.
Tek veri kaynağı kardeş dosya `docs/data/metrics.json`'dır — her turda `main.py` üretir,
koşu workflow'u defterlerle birlikte commit eder. Sayfayı yayına almak için depo
ayarlarında **Settings → Pages → Source: Deploy from a branch → `main` / `/docs`** seçmek
yeterlidir; ayrı bir deploy workflow'u gerekmez.

| Bölüm | Ne gösterir |
|---|---|
| LONG vs SHORT | Projenin ana sorusu, en üstte: tüm yarışmacıların long işlemleri havuzu vs short işlemleri havuzu — ortalama R, kazanma oranı, işlem sayısı, net funding katkısı ayrı ayrı. Havuz **işleme** oy verir, modele değil: model ortalamalarının ortalaması 2 işlemlik bir modeli 200 işlemlik bir modelle eşitlerdi. |
| Leaderboard | Ortalama R'ye göre sıralı (başlığa tıklayınca değişir): getiri, MDD, işlem sayısı, kazanma oranı, profit factor, `avg_stop_distance_pct`, `cost_per_r` ve kabul sütunu — **iki kapı** (Ö, E) ve **bir uyarı** (⚠ B); bkz. Kabul çıtası. `random_ctrl` KONTROL etiketiyle sıralamada kalır; `buyhold` REFERANS olarak ayrı bölümdedir (kural 15). |
| Özsermaye eğrileri | Tüm modeller tek grafikte; bir modele tıklayınca yalnız o kalır. Kesikli gri çizgi başlangıç sermayesi. |
| Açık pozisyonlar | Model, sembol, yön, giriş, stop, güncel PnL ve stratejinin gerekçesi. PnL **çıkış maliyeti hariçtir** (pozisyon kapanmadı, çıkış fiyatı bilinmiyor) ve sayfa bunu söyler. |
| Son 20 işlem | Kapanış sırasına göre, `signal_reason` kırpılmadan — ensemble'ın oy sayısı, confluence'ın güven kuyruğu dâhil. |
| Getiri korelasyonu | Modellerin bar getirilerinin Pearson korelasyonu (çift bazında örtüşme). Yüksek korelasyonla yarışan iki model bağımsız iki ölçüm değil, aynı ölçümün iki kopyasıdır. |

Sayfa bir **süs katmanıdır**: ölçüm defterde ve JSON'dadır, sayfa yalnızca onu çizer.
Yerelde açmak için `python -m http.server` gerekir (`file://` ile `fetch` engellenir).

## Telegram özeti

`scripts/telegram_report.py` günde bir kez, **`as_of` saati 20:00 UTC olan turda** tek bir
mesaj yollar: long vs short güncel durum, ortalama R'ye göre ilk 3 ve son 3 model, son 24
saatte açılan/kapanan işlemler ve kabul çıtasını geçen model olup olmadığı (yoksa en yakını
hangi kapıda takıldığı). Band uyarısı geçen modelin yanında `⚠` olarak anılır — kapı
olmadığı için "geçemedi" diye raporlanmaz.

```bash
python scripts/telegram_report.py --dry-run --force   # yollamadan mesajı gör
```

Token'lar GitHub Secrets'tan ortama geçer: **`TELEGRAM_BOT_TOKEN`** ve **`TELEGRAM_CHAT_ID`**
(Settings → Secrets and variables → Actions). Tanımlı değilse script bunu bilgi olarak
loglar ve sessizce geçer.

Kapı duvar saatine değil `as_of` barına bakar: 4H barlarda gün içinde altı tur koşar ve
"günde bir"in tekrarlanabilir tanımı "as_of'u 20:00 olan tur"dur. Raporun tazeliği de ayrıca
kontrol edilir, yoksa tur düştüğünde depoda kalan dünkü 20:00 raporu yeniden yollanırdı.

**Telegram hatası koşuyu düşürmez.** Script her yolda 0 döner (eksik token, ağ hatası,
Telegram 4xx'i, bozuk JSON — hepsi loglanıp geçilir), workflow adımı ayrıca
`continue-on-error: true` taşır ve defter commit'inden **sonra** gelir. Özet bir bildirimdir;
onun kesintisi yüzünden turun kırmızı dönmesi, defterin commit'lenip commit'lenmediğine dair
gerçek sinyali gürültüye boğardı.

## Defter formatı (`ledgers/<model>/`)

| Dosya | İçerik |
|---|---|
| `positions.json` | Koşular arası taşınan durum: nakit, açık pozisyonlar, bekleyen emirler, işlenmiş son bar. |
| `trades.csv` | Kapanan her işlem (kısmi çıkışlar dâhil): giriş/çıkış zamanı ve fiyatı, yön, miktar, notional, ilk stop, **riske edilen tutar (`risk_amount`)**, kaldıraç, marj, komisyon, funding, **ödenen kayma (`slippage_cost`)**, PnL, çıkış sebebi (`stop`/`tp`/`liquidation`/`signal`) ve stratejinin gerekçesi. |
| `equity.csv` | Bar başına nakit, kullanılan marj, gerçekleşmemiş PnL, özsermaye ve açık pozisyon sayısı. |

Yazmalar atomiktir (geçici dosya + `rename`); yazılmış bir satır asla değiştirilmez.
Kapanan işlemlerin `pnl` toplamı bakiyedeki değişime eşittir — defter bu yüzden
denetlenebilir. `risk_amount` (`adet × |giriş − ilk stop|`) deftere yazılır çünkü
karşılaştırmanın birinci sınıf metriği olan **R** (`pnl / risk_amount`) onun üzerinden
hesaplanır; R'nin kendisi türetilmiş bir değer olduğu için deftere değil `core/metrics.py`'ye
aittir.

## Karşılaştırma

```python
from core.config import load_config
from core.metrics import compare, format_report

print(format_report(compare(
    ["buyhold", "model_a"], config=load_config(), benchmarks=["buyhold"]
)))
```

Kayma dolum fiyatının içine gömülü olduğu için `slippage_cost` deftere ayrıca yazılır:
`cost_per_r` komisyon **ve** kayma ister, yazılmazsa maliyetin yarısı görünmez kalırdı.

Referans modeller (kural 15) bu sıralamaya girmez: tablonun altında ayrı bir **REFERANS**
bölümünde durur ve `avg_stop_distance_pct` / `cost_per_r` kolonlarında `—` gösterir. Stop'u
olmayanın 1R'si yoktur; çıpanın taşıdığı bilgi sıralamada değil hesap düzeyi getirisindedir
("model piyasayı yendi mi?").

Tablo **ortalama R'ye göre** sıralanır ve her model için long / short / TOPLAM satırlarını
ayrı gösterir. Toplam getiri (USDT ve %) ikinci sırada, hesap düzeyinde raporlanır: hesapta
tek bakiye olduğu için getiri, max drawdown ve Sharpe yön bazında ayrıştırılamaz — yönlerin
risk profili bunun yerine o yönün kümülatif R eğrisinden ölçülür (`r_sharpe`,
`max_drawdown_r`). Ölçülemeyen bir metrik `—` yazılır; 0.0 gösterilmez.

## Kurulum ve manuel veri kontrolü

```bash
pip install requests pandas numpy pyyaml pyarrow pytest
pytest -q

# 3 sembol için veri çekip son 5 barı yazdırır (evrenden ilk 3 sembol için argümansız çalışır)
python scripts/manual_data_check.py BTC-USDT-SWAP ETH-USDT-SWAP SOL-USDT-SWAP
```

Veri kaynağı **OKX public API v5**'tir (API anahtarı gerekmez). Semboller OKX `instId`
biçimindedir (`BTC-USDT-SWAP`). Evren `data/universe.json`'a, mumlar ve funding geçmişi
`data/cache/` altına parquet olarak yazılır; `data/` klasörü depoya girmez ve her koşuda
yalnızca eksik barlar çekilir. Son bar **kapanmamışsa atılır** — `MarketData.as_of` her
zaman son kapanmış barın zamanıdır (bkz. CLAUDE.md kural 12). `as_of` sabit bir çıpadan
okunur: **BTC-USDT-SWAP'ın son kapanmış barı.** Bu bara sahip olmayan semboller o tur
dışlanır ve loglanır; böylece tek bir gecikmiş sembol turun "şimdi"sini geri çekemez.

## Model listesi

Aktif küme `config.yaml`'ın `models` listesidir; adlar `strategies/registry.py`'de çözülür.
Yarışmanın tamamlanması için 10 model gerekir.

| # | Strateji | Yön | Durum |
|---|---|---|---|
| — | `buyhold` | long | **referans çıpası** (kural 15), yarışmacı değil |
| 1 | `trend` | long + short | Donchian kırılımı + EMA rejim filtresi |
| 2 | `meanrev` | long + short | RSI + Bollinger ortalamaya dönüş (short'ta BTC rejim kapısı) |
| 3 | `momentum` | long + short | kesitsel 7g getiri sıralaması, haftalık dengeleme |
| 4 | `squeeze` | long + short | Bollinger sıkışması + hacim teyitli kırılım |
| 5 | `confluence` | long + short | Fibonacci çakışması (retracement × extension), yön RSI'dan |
| 6 | `failed_breakout` | short | 20 bar zirvesini süpürüp altına kapanan tuzak |
| 7 | `downtrend_rally` | short | düşüş trendinde 0.382-0.618 / 20 EMA rallisinin satışı |
| 8 | `avwap` | long + short | kesinleşmiş pivota çapalı VWAP'tan ±2σ sapma |
| 9 | `random_ctrl` | long + short | **kontrol grubu**: bilgisiz çekiliş, edge'in referansı |
| 10 | `ensemble` | long + short | **meta** (kural 4): akranların o turdaki sinyallerinden konsensüs |

`buyhold` sayıya dâhil değildir: BTC %50 / ETH %50, 1x, stop'suz, bir kez alınıp hiç satılmaz.
Tek işi yarışmacılara bir zemin vermektir.

`ensemble` projedeki tek META modeldir (`is_meta = True`): kendi sinyal mantığı yoktur, o
turda normal modellerin ürettiği sinyalleri salt okunur okur ve aynı sembolde aynı yönde en az
2 modelin birleştiği yerde işlem açar. Oy veren havuz `random_ctrl` (kontrol grubu) ile
`buyhold` (referans çıpası) DIŞINDADIR; oylar eşit ağırlıklıdır. Stop katılımcıların en
genişi, hedef en yakını (tek TP, tamamı). Ölçtüğü tek şey: **üst üste binme işe yarıyor mu.**

`random_ctrl` ise sayıya dâhildir ve `is_benchmark` DEĞİLDİR: boyutlandırması, stop ölçeği ve
limitleri yarışmacılarla birebir aynıdır, tek farkı sinyalin bilgisiz olmasıdır. Çıpa "piyasa
ne yaptı"yı ölçer, kontrol ise "sinyalin kendisi bir şey söylüyor mu"yu — ikincisinin cevabı
ancak aynı sütunda, aynı ortalama R sıralamasında okunabilir.

## Kabul çıtası

İki ayrı çıta vardır ve karıştırılmamalıdır: **kod çıtası** (bir strateji "tamamlandı" mı)
ve **sonuç çıtası** (bir modelin ölçülmüş sonucu okunabilir mi).

### Sonuç çıtası — iki kapı + bir uyarı (dashboard rozetleri)

Eşikler `config.yaml > acceptance` altındadır; hesap `core/metrics.py::acceptance_flags`.
Bir model ancak **iki kapı da** yeşilken doğrulanmış sayılır.

| Kapı | Soru | Geçme koşulu |
|---|---|---|
| **Ö** örneklem | Bu ortalama bir ölçüm mü, gürültü mü? | R'ye giren kapanmış işlem ≥ `acceptance.min_trades` (**30**) |
| **E** edge | Sonuç sinyalden mi geliyor? | ort. R > 0 **ve** `random_ctrl`'ü **en az `edge_margin_r` = 0.15R marjla** aşıyor **ve** hesap getirisi `buyhold` çıpasını geçiyor |

Marj olmadan kontrolü 0.01R ile geçen bir model de "geçti" sayılırdı; oysa bilgisiz
çekilişin kendi gürültüsü o kadar farkı tek başına üretir.

| Uyarı | Soru | Tetiklenme |
|---|---|---|
| **⚠ B** band | Bu satır başka bir satırla aynı maliyet ölçeğinde mi? (kural 14) | `avg_stop_distance_pct`, yarışmacı medyanının `medyan/√2.5 .. medyan×√2.5` bandının **dışında** |

**Band bir kapı değildir ve doğrulamayı engellemez.** Bandın dışında kalmak bir kusur değil
bir kıyas koşuludur: modelin kendi ölçümü geçerlidir, ama o satırı bir başkasının yanına
koyarken **maliyet farkı (`cost_per_r`) dikkate alınmalıdır** — model aynı 1R'yi farklı
notional ile taşımış, yani R başına farklı komisyon+kayma ödemiştir. Tabloda uyarı ikonu
olarak durur; tooltip ne yapılacağını söyler.

Referans çıpası bu kapılara hiç girmez (kural 15): yarışmacı olmadığı için ölçmediği bir
yarışta not almaz. `random_ctrl` girer — bilgisiz çekilişin sıralamada nerede durduğu
gizlenecek bir kusur değil, raporlanacak bir sonuçtur. Gerekçeler için bkz.
[`CLAUDE.md` > Kabul Çıtası](./CLAUDE.md#kabul-çıtası-iki-kapı--bir-uyarı).

### Kod çıtası (taslak)

Bir stratejinin "tamamlandı" sayılması için:

- [ ] `strategies/base.py::Strategy` arayüzünü eksiksiz uygular, `core/`'a doğrudan erişmez.
- [ ] Kendi birim testi vardır ve mock `MarketData` ile bağımsız çalıştırılabilir.
- [ ] En az bir tam değerlendirme döngüsünde hatasız `Signal` üretir.
- [ ] Ürettiği `Signal.reason` alanı boş değildir (deftere yazılan gerekçe).
- [ ] `allowed_directions` dışında yön içeren sinyal üretmez.
- [ ] Stop mesafesi 1×–2.5×ATR bandındadır; veriye bağlı stop kuruyorsa
      `max_stop_atr_multiple` tavanını aşan işlemi atlar ve atlamayı loglar (kural 14).

Projenin "tamamlandı" sayılması için:

- [x] 10 strateji de arayüze uygun şekilde çalışıyor (9 yarışmacı + 1 meta; `buyhold` çıpa).
- [x] `core/portfolio.py`, `core/funding.py`, `core/ledger.py` tüm modeller için aynı kuralları
      uyguladığını kanıtlayan testlere sahip (`tests/test_portfolio.py`, `tests/test_funding.py`,
      `tests/test_ledger.py`, `tests/test_engine.py`).
- [x] `core/metrics.py` tüm stratejileri aynı tabloda karşılaştırabiliyor; her metrik
      long/short ayrı ve `avg_stop_distance_pct` + `cost_per_r` kolonları raporlanıyor
      (`tests/test_metrics.py`).
- [x] `.github/workflows/run.yml` periyodik çalıştırmayı yapıyor; testleri `ci.yml` doğruluyor.
- [x] `main.py` boru hattını uçtan uca çalıştırıyor ve `docs/data/metrics.json` üretiyor
      (`tests/test_main.py`).
- [x] Ölçüme bir referans çıpası (`buyhold`) eklendi (kural 15, `tests/test_buyhold.py`).
- [x] `docs/index.html` sonuçları tek sayfada gösteriyor; LONG vs SHORT paneli en üstte,
      iki kabul kapısı ve band uyarısı leaderboard'da (`tests/test_report.py`).
- [x] `scripts/telegram_report.py` günlük özeti yolluyor ve hatası turu düşürmüyor
      (`tests/test_telegram_report.py`).

Bu çıta taslaktır, onay/düzeltme bekliyor.

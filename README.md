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
| 1 | — | — | planlanıyor |
| ... | | | |
| 10 | — | — | planlanıyor |

`buyhold` sayıya dâhil değildir: BTC %50 / ETH %50, 1x, stop'suz, bir kez alınıp hiç satılmaz.
Tek işi yarışmacılara bir zemin vermektir.

## Kabul çıtası (taslak)

Bir stratejinin "tamamlandı" sayılması için:

- [ ] `strategies/base.py::Strategy` arayüzünü eksiksiz uygular, `core/`'a doğrudan erişmez.
- [ ] Kendi birim testi vardır ve mock `MarketData` ile bağımsız çalıştırılabilir.
- [ ] En az bir tam değerlendirme döngüsünde hatasız `Signal` üretir.
- [ ] Ürettiği `Signal.reason` alanı boş değildir (deftere yazılan gerekçe).
- [ ] `allowed_directions` dışında yön içeren sinyal üretmez.
- [ ] Stop mesafesi 1×–2.5×ATR bandındadır; veriye bağlı stop kuruyorsa
      `max_stop_atr_multiple` tavanını aşan işlemi atlar ve atlamayı loglar (kural 14).

Projenin "tamamlandı" sayılması için:

- [ ] 10 strateji de arayüze uygun şekilde çalışıyor.
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

Bu çıta taslaktır, onay/düzeltme bekliyor.

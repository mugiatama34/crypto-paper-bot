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

print(format_report(compare(["model_a", "model_b"], config=load_config())))
```

Kayma dolum fiyatının içine gömülü olduğu için `slippage_cost` deftere ayrıca yazılır:
`cost_per_r` komisyon **ve** kayma ister, yazılmazsa maliyetin yarısı görünmez kalırdı.

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

> Henüz tanımlanmadı — arayüz sözleşmesi onaylandıktan sonra `strategies/` altına eklenecek.
> Yarışmanın tamamlanması için 10 model gerekir; ilerledikçe bu tablo güncellenecek.

| # | Strateji | Yön | Durum |
|---|---|---|---|
| 1 | — | — | planlanıyor |
| ... | | | |
| 10 | — | — | planlanıyor |

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
- [ ] `.github/workflows/run.yml` periyodik çalıştırmayı ve testleri otomatik doğruluyor.

Bu çıta taslaktır, onay/düzeltme bekliyor.

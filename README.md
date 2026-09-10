# crypto-paper-bot

10 farklı strateji modelinin aynı kripto piyasa verisini görüp kendi izole sanal hesabıyla
paper-trading yaptığı bir **ölçüm projesi.** Amaç kâr etmek değil, stratejileri adil ve
tekrarlanabilir koşullarda kıyaslamaktır. Kurallar ve mimari için bkz. [`CLAUDE.md`](./CLAUDE.md).

## Nasıl çalışır (özet)

- `core/engine.py` her turda tüm stratejileri aynı `MarketData` anlık görüntüsüyle çağırır.
- Her strateji yalnızca `Signal` (yön + giriş/çıkış seviyeleri) üretir; defter yazımı,
  komisyon/funding hesabı ve pozisyon boyutlandırma her zaman `core/` içinde, tüm modeller
  için aynı kurallarla yapılır.
- Her stratejinin işlemleri kendi defterine (`ledgers/`) yazılır ve `core/metrics.py`
  tarafından karşılaştırılır.

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

Projenin "tamamlandı" sayılması için:

- [ ] 10 strateji de arayüze uygun şekilde çalışıyor.
- [ ] `core/portfolio.py`, `core/funding.py`, `core/ledger.py` tüm modeller için aynı kuralları
      uyguladığını kanıtlayan testlere sahip.
- [ ] `core/metrics.py` tüm stratejileri aynı tabloda karşılaştırabiliyor.
- [ ] `.github/workflows/run.yml` periyodik çalıştırmayı ve testleri otomatik doğruluyor.

Bu çıta taslaktır, onay/düzeltme bekliyor.

# Bu kopyadaki değişiklikler

Kaynak: [klonnist/ew-setup-scanner](https://github.com/klonnist/ew-setup-scanner), commit
`3e922f369349ea5adc80b76e0da0d6c45681d7d6` (2026-10-18), MIT lisansı (`LICENSE`).
`index.html`, `backtest.html`, `src/` ve `tests/` o commit'ten kopyalandı. Kural motoru
(`src/ew/`), dalga ve üçgen tarayıcısı ile arayüzün geri kalanı **değişmedi**.

Kopya, aracın backtest'ini gerçeğe yaklaştırmak için tek bir öneriyi uygular: **veri ve dolum
gerçekçiliği.** Dört parçası:

## 1. Veri spot değil USDT perp
- `src/data/okx.js`: coin listesi, mumlar ve enstrüman filtresi `SWAP` (`COIN-USDT-SWAP`).
  Araç short kurulumu da önerir; spot'ta short açılamaz.
- Perp'te `volCcy24h` baz coin cinsinden olduğu için USDT hacmi `volCcy24h × son fiyat`.
- TradingView bağlantıları perp grafiğine (`…USDT.P`) gider.

## 2. Funding
- `src/backtest/history.js::getFundingHistory`: OKX `funding-rate-history`.
- `src/backtest/engine.js`: dolumdan kapanışa kadar her funding anında pozisyonun yönüne göre
  `oran × fiyat` R'den düşülür (long pozitif oranda öder, short alır). İşlem satırı `fundingR`
  ve `fundingCovered` taşır.
- **Sınır:** OKX bu uç noktada yalnızca son ~3 ayı tutar. Daha eski işlemlerde funding
  uygulanamaz. Bu sıfır sayılmaz: raporda "Funding kapsamı" ve ayrı bir kırılım gösterilir.

## 3. Kayma ve limit dolumu
- Yeni parametre **Kayma (%)** (varsayılan %0,05): piyasa ve stop dolumları (B kırılımı
  girişi, tetiklenmiş giriş, üçgen girişi, stop, süre çıkışı) aleyhe kayar. Limit emirler
  (bölge girişi, hedef) kaymaz.
- Bölge limiti artık fiyat seviyeye **dokununca değil, seviyeyi geçince** dolar.

## 4. Döneme göre evren
- Yeni parametre **Evren** (varsayılan "dönem başındaki hacim"): bugünkü ilk `3 × N`
  (en çok 100) perp aday alınır, her birinin başlangıçtan önceki 30 günlük günlük hacmi okunur
  ve ilk N seçilir (`src/backtest/universe.js`). O dönemin büyük kısmında işlem görmeyen coin
  elenir.
- **Sınır:** aday kümesi bugün listelenen perp'lerdir; o tarihten sonra listeden kalkmış
  coinler hâlâ dahil edilemez.

## Diğer
- Kayıtlı çalışmaların ve parametrelerin tarayıcı anahtarları `v2` oldu: eski (spot, kaymasız)
  çalışmalar yeni varsayımlarla aynı listeye karışmaz.
- Grafik kütüphanesi (`lightweight-charts@5.2.1`, Apache-2.0) CDN yerine `lib/` altında.
- Testler: `tests/realism.test.js` eklendi. `backtest.test.js` kaymayı açıkça `0` verir
  (kaymasız geometriyi sınıyor). `okx.test.js` perp enstrüman adlarıyla güncellendi.
  Çalıştırma: `node --test tests/*.test.js`.

Bu sayfa crypto-paper-bot'un ölçümünün parçası değildir; defterlere ve modellere bağı yoktur.
**Yatırım tavsiyesi değildir.**

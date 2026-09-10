# Tasarım Kararları

Bu dosya, projenin ilerleyişi sırasında alınan önemli tasarım kararlarını ve gerekçelerini
kayıt altına alır.

## 1. Proje sözleşmesi ve klasör iskeleti

`CLAUDE.md` ve boş klasör iskeleti oluşturuldu. Strateji arayüzü henüz onaylanmamıştı.

## 2. Strateji arayüzü onaylandı: açılış/kapanış ayrımı, doğrulama kapısı

- `entry_type` alanı `"market" | "limit"` olarak tanımlı ama v1'de yalnızca `"market"`
  işlenir; `"limit"` `core/validate.py`'de `NotImplementedError` fırlatır. Limit emri
  desteklemek bekleyen emir/geçerlilik süresi/mum-içi dokunma kavramları gerektirir ve
  look-ahead hatası riski taşır — kapsam dışı bırakıldı, alan sözleşmede korundu.
- Kapanış/kısmi çıkış kararı ayrı bir metodla verilir: `Strategy.manage_positions(market,
  positions) -> list[ExitInstruction]`, varsayılan implementasyon boş liste döner.
  `generate_signals` yalnızca açılış önerir. Bu ayrım, engine'in sinyal niyetini tahmin
  etmesini gereksiz kılar.
- `trailing_atr` alanı `Signal`'a eklendi ama uygulaması strateji değil `core/engine.py`
  sorumluluğunda — tekrarlanan trailing mantığının 10 modelde ayrı ayrı yazılmasının önüne
  geçer.
- Tek hedefli `take_profit: float | None` yerine `take_profits: list[TakeProfit]`
  (`price`, `fraction`) kullanılır — kısmi çıkış yapan stratejiler için özel durum
  gerektirmez.
- `allowed_directions` ihlali veya geçersiz sinyal geometrisi sessizce filtrelenmez,
  `ValueError`/`NotImplementedError` fırlatılır — bu doğrulama `core/validate.py`'de tek
  yerde toplanır (stop==entry sıfıra bölme, stop yanlış taraf, TP yönü tutarsızlığı,
  fraction toplamı > 1.0, evren dışı sembol). Sessiz filtreleme bir modelin performansını
  fark edilmeden bozabileceği için "ölçüm projesi" ilkesiyle çelişir.

`strategies/base.py` ve `core/validate.py` bu kararlarla uygulandı, testler
`tests/test_base.py` ve `tests/test_validate.py`'de.

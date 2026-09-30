# crypto-paper-bot — Tasarım Sistemi (MASTER)

> **Durum:** ÖNERİ. Bu dosyaya göre henüz hiçbir kod değişmedi.
> **Kapsam:** `docs/index.html` (Skorboard), `docs/positions.html` (Operasyon), `docs/backtest.html`.
> **Kaynak:** `ui-ux-pro-max --design-system "fintech trading dashboard dark data-dense"`
> (variance 3 · motion 2 · density 8) + `chart` / `ux` / `typography` aramaları, **mevcut
> `docs/shared.css` ile karşılaştırıldı.** Skill çıktısının bu projeye UYMAYAN kısımları
> aşağıda "Reddedilenler" bölümünde gerekçesiyle duruyor.
> **Öncelik:** Sayfaya özel bir `pages/<sayfa>.md` varsa o sayfada bu dosyayı ezer.

---

## 0. İlke: bu bir ölçüm arayüzüdür

CLAUDE.md'nin ilkesi arayüz için de geçerli: **Biçim, ölçümün parçasıdır.** Aynı sayı
iki sayfada farklı görünürse okuyucu iki ayrı ölçüm görüyor sanar. Bu yüzden:

1. **Sayfa hesap yapmaz** (kural 7). Tasarım yalnızca yükte olan veriyi çizer.
2. **Renk bir anlam taşır, süs değildir.** Bir rengin iki anlamı olamaz (bkz. §2.3).
3. **`—` "ölçülemedi", `0` "ölçüldü ve sıfır" demektir.** Hiçbir bileşen bu ikisini aynı gösteremez.
4. **Sıfır bağımlılık:** framework yok, CDN yok, web fontu yok, build adımı yok.

---

## 1. Stil yönü

| | Karar |
|---|---|
| Stil | **Minimalizm / İsviçre stili, koyu**: ızgara tabanlı, yüksek kontrast, süs yok |
| Yoğunluk | **Yüksek (8/10)**: panel, pazarlama sayfası değil |
| Hareket | **Çok az (2/10)**: yalnızca durum geri bildirimi, sayfa kaydırılınca beliren animasyon YOK |
| Çeşitlilik | **Düşük (3/10)**: bento ızgarası ya da asimetrik düzen yok; tek sütun akış + kart ızgarası |
| Kaçınılacaklar | Varsayılan açık tema · cam efekti (glassmorphism) · gradyan · gölge yığını · yavaş render |

---

## 2. Renk

### 2.1 Yüzeyler ve metin (MEVCUT, korunuyor)

Bunlar `shared.css`'teki mevcut değerler. Ölçülmüş kontrastlarla birlikte duruyorlar ve
skill'in önerdiği slate paletinden (`#020617`/`#0F172A`) daha iyi durumdalar. Nötr sıcak
gri, kategorik seri renkleriyle çatışmıyor; mavi tonlu arka plan ise `--s1` mavisini söndürürdü.

| Token | Değer | Kullanım | Kontrast (plane / surface / surface-2) |
|---|---|---|---|
| `--plane` | `#0d0d0d` | sayfa zemini | — |
| `--surface` | `#1a1a19` | kart | — |
| `--surface-2` | `#222221` | iç içe yüzey, tablo başlığı | — |
| `--ink` | `#ffffff` | birincil metin, kilit sayı | 19.4 / 17.4 / 15.9 |
| `--ink-2` | `#c3c2b7` | ikincil metin | 10.9 / 9.7 / 8.9 |
| `--ink-dim` | `#a5a39a` | **küçük ve yoğun metin** (th, eksen, dipnot) | 7.7 / 6.9 / 6.3 |
| `--ink-muted` | `#898781` | **yalnızca metin dışı öğeler** (▸, eksen çizgisi, pasif durum) | 5.4 / 4.9 / **4.4 ✗ küçük metin** |
| `--grid` | `#2c2c2a` | grafik ızgarası | — |
| `--axis` | `#383835` | eksen, hover kenarlığı | — |
| `--hair` | `rgba(255,255,255,.10)` | ince ayraç | — |

**Kural:** 14px altındaki metin `--ink-muted` KULLANAMAZ, en az `--ink-dim` kullanır.

### 2.2 Anlamsal renkler: İŞARET ve METİN ayrı token

Her anlamsal rengin iki hâli var: **işaret** (çizgi, renk örneği, kenarlık; 3:1 yeterli) ve
**metin** (aynı ton, daha açık; 4.5:1 gerekir). Bir grafik çizgisi ile lejanttaki örnek kutusu
**her zaman aynı işaret token'ını** kullanır.

| Anlam | İşaret | Metin | Metin kontrastı (surface-2) |
|---|---|---|---|
| Pozitif sayı (işaret) | `--pos #3987e5` | `--pos-ink #5ea3f0` | 6.05 |
| Negatif sayı (işaret) | `--neg #e66767` | `--neg-ink #ef7d7d` | 5.99 |
| Long (kimlik) | `--long #3987e5` | `--long-ink #5ea3f0` | 6.05 |
| Short (kimlik) | `--short #d95926` | `--short-ink #f0813f` | 6.00 |
| Kapı geçti | `--good #0ca30c` | *(öneri)* `--good-ink #3ecf6e` | 4.75 → **7.85** |
| Uyarı (⚠B, Ö) | `--warning #fab219` | aynı | 8.68 |
| Arıza (sizing failure) | `--critical #d03b3b` | *(öneri)* `--critical-ink #ef7d7d` | — |

> **Öneri 1 — `--good-ink`:** `.badge.on` şu an `--good`'u metin rengi olarak kullanıyor.
> 4.75 sınırı ancak geçiyor ve rozet 11–12px. Aynı tonun açık hâli (`#3ecf6e`, 7.85)
> diğer `*-ink` token'larıyla aynı deseni tamamlar.

### 2.3 Renk anlamı ÇAKIŞMAZ (en önemli kural)

- **Kâr/zarar = mavi/kırmızı, yeşil/kırmızı DEĞİL.** Skill "yeşil pozitif" önerdi, ama
  yeşil zaten **kapı geçti** (`--good`) anlamını taşıyor. Yeşil kâr, "kârda" ile
  "doğrulandı"yı aynı renkte gösterip okuyucuya kapıyı geçmemiş bir modeli doğrulanmış
  gibi okuturdu. Mavi/kırmızı aynı zamanda renk körlüğüne güvenli bir diverging çift.
- **Long/short, işaret renginden AYRI bir eksen.** Short'a kırmızı verilirse kârdaki bir
  short "kötü" diye okunur. Short = turuncu.
- **Renk tek başına anlam taşımaz:** her işaretli sayının önünde `+`/`−` durur, yön
  hücresinde `LONG`/`SHORT` yazısı durur, rozetin metni vardır.

### 2.4 Model kimliği (kategorik seri)

8 slot: `--s1 … --s8` (mavi, turuncu, teal, amber, pembe, yeşil, mor, mercan). Atama
`shared.js::assignStyles`te **tek kopya**; bir model her sayfada, her grafikte aynı slotu alır.

| Rol | Görsel |
|---|---|
| Yarışmacı | kategorik slot, düz çizgi, 1.75px |
| Kontrol (`controlModel`, liste olabilir) | **nötr ton** (`--ink-dim`), kesikli `4 3` |
| Referans çıpası (`buyhold`) | `--ink-2`, noktalı `1 3` |
| Dış sistem kopyası (`vwap_clone`) | kategorik slot, kesikli `6 3` |
| Başlangıç sermayesi çizgisi | `--ink-muted`, `3 4`, %70 opaklık |

> 8 slottan fazla model varsa (iki katman birlikte ~11 satır) slotlar **çizgi stiliyle
> birlikte** tekrar edilir (renk × {düz, kesikli}). Aynı renk + aynı stil iki modele verilemez.

---

## 3. Tipografi

| Karar | Değer | Gerekçe |
|---|---|---|
| Font ailesi | `system-ui, -apple-system, "Segoe UI", sans-serif` (**mevcut**) | Web fontu = CDN = kural ihlali; ayrıca ilk çizimi geciktirir |
| Sayı fontu | *(öneri)* `--font-num: ui-monospace, "SF Mono", "Cascadia Mono", Menlo, monospace`, **yalnızca fiyat/miktar kolonlarında** | Ondalık hizası; `tabular-nums` oransal fontta nokta hizasını garanti etmez |
| Sayı rakamları | `font-variant-numeric: tabular-nums` **her sayıda** (mevcut, korunuyor) | Satırlar arası sütun titremesi olmaz |
| Temel boyut | 14px / 1.5 (mevcut) | Yoğun panel; mobilde metin 16px'e yükselmez, ama input'lar 16px (iOS zoom) |

### Tip ölçeği (öneri: bugün 26 farklı `10–11.x px` değeri var)

| Token | px | Ağırlık | Kullanım |
|---|---|---|---|
| `--t-kpi` | 28 | 650 | durum kartındaki kilit sayı |
| `--t-h1` | 20 | 650 | sayfa başlığı |
| `--t-h2` | 16 | 600 | bölüm başlığı |
| `--t-body` | 14 | 400 | gövde, tablo hücresi |
| `--t-small` | 12 | 400/500 | th, rozet, chip, eksen etiketi |
| `--t-micro` | 11 | 500 | yalnızca `row-val-sub`, grafik tick'i. **Alt sınır, bundan küçüğü yok** |

> **Öneri 2:** `11.5px`, `10.5px` gibi ara değerler bu altı token'a yuvarlanır. Tek
> ölçek, iki sayfada aynı sayının aynı büyüklükte görünmesini garanti eder.

---

## 4. Boşluk, düzen, şekil

Yoğunluk 8 → sıkı ölçek (4px taban):

| Token | px | Kullanım |
|---|---|---|
| `--sp-1` | 4 | rozet içi, ikon–metin |
| `--sp-2` | 8 | chip aralığı, hücre dikey |
| `--sp-3` | 12 | kart içi grup |
| `--sp-4` | 14 | **`--gap` (mevcut)**: kartlar arası |
| `--sp-5` | 24 | bölümler arası |
| `--sp-6` | 32 | sayfa üst/alt |

| Token | Değer |
|---|---|
| `--radius` | 10px kart · *(öneri)* `--radius-sm` 6px rozet/chip/input |
| `--touch` | 44px: **her** tıklanabilir öğenin alt sınırı (mevcut) |
| Konteyner | `max-width: 1200px`, yan boşluk 16px (≤480px), 24px (üstü) |
| Kırılma noktaları | 480 (kart düzeni) · 768 · 1024 · 1440 |
| Gölge | yok. Derinlik yüzey tonuyla (`plane → surface → surface-2`) verilir |

**Kural:** sayfa düzeyinde yatay kaydırma yok. Geniş tablo ≤480px'te kart düzenine döner,
arada kalan genişliklerde **kendi konteynerinde** kayar ve ilk kolon (model adı) `sticky` kalır.

---

## 5. Bileşenler

### 5.1 KPI / durum kartı
Etiket (`--t-small`, `--ink-dim`, büyük harf yok) → değer (`--t-kpi`, `--ink`) → alt satır
(bağlam: "12 kapanmış · 3 açık"). Değer işaretliyse işaret rengi **metin token'ı** ile.

### 5.2 Sıralama tablosu (`table.grid`)
- Sayılar **sağa**, metin sola hizalı; th ile hücre aynı hizada.
- Satır yüksekliği 36px (masaüstü), satırın tamamı ≥44px dokunma alanı (mobil kart).
- Sıralanabilir başlık = `<button>`, `aria-sort`, aktif kolonda ▲/▼. Varsayılan: **getiri ↓**.
- **Bloklar birleşmez:** yarışmacı / Ö kapısını geçmeyen / referans çıpası / dış sistem kopyası;
  her blok `--surface-2` başlık satırıyla ayrılır ve sıralama blok İÇİNDE uygulanır.
- Kapıyı geçmeyen satır: %60 opaklık + `Ö` rozeti. **Gizlenmez.**
- Hover: `--surface-2` zemin; tıklama → model detayı (`#model=<ad>&layer=<katman>`).
- Katman rozeti (`.lyr`) her satırda: `4s` nötr kenarlık, `Scalp` amber kenarlık.

### 5.3 Rozetler (`.badge`)
| Varyant | Anlam |
|---|---|
| `on` | kapı geçti (`--good-ink`) |
| `off` | kapı geçmedi (`--ink-dim`) |
| `na` | değerlendirilemez (kenarlıksız, `—`) |
| `warn` | ⚠B band / uyarı |
| `info` | nötr bilgi |
| *(öneri)* `fault` | beklenmedik ret / sıfır boyut (`--critical`). Beklenen ret (`duplicate_position`) ASLA bu renkte değil |

### 5.4 Chip / filtre
Tümü / 4s / Scalp gibi segmentler `role="radiogroup"`; aktif segment `--surface-2` + `--ink`.
"Filtrelendi" chip'i her zaman bir **sıfırla** eylemi taşır.

### 5.5 Açık pozisyon satırı
Yön etiketi (`LONG`/`SHORT` metni + kimlik rengi), anlık K/Z (işaret rengi), R, TP/SL,
çıkış yönetimi rozetleri. **İSTEK ile OLAY ayrı görsel:** istenmiş ama tetiklenmemiş = çerçeve
(`off`), tetiklenmiş = dolu (`on`). ⇅ KARŞI POZİSYON rozeti eşine atlar, adresi değiştirmez.

### 5.6 Katlanır panel (`.panel`, `.howto`)
`<details>`/`<summary>`, varsayılan kapalı, summary ≥44px, ▸ işareti `--ink-muted`, 90° döner.

---

## 6. Grafikler

### 6.1 Özsermaye eğrileri (çok serili çizgi)
Skill kuralı: **>6 seri görsel gürültüdür.** Panelde ~11 model var, bu yüzden:

1. **Varsayılan görünüm yarışmacılardır**, kontrol/çıpa soluk (%50) çizilir.
2. **Vurgu:** tabloda ya da lejantta bir satırın üzerine gelmek (veya odaklamak) o seriyi
   2.5px + tam opak yapar, diğerlerini %25'e indirir. Tıklamak izole eder (mevcut davranış).
3. **Doğrudan etiket:** her serinin sonunda model adı (çakışmada dikey kaydırma); lejant yedektir.
4. **Y ekseni:** `$` değil **başlangıca göre %** (iki katman aynı 10.000 $ ile başlasa da
   okuyucunun sorusu getiri). Başlangıç çizgisi yalnızca görünen seriler TEK bir sermaye
   paylaşıyorsa çizilir (mevcut kural).
5. **Nokta sayısı:** 1000 noktanın altında SVG (mevcut), üstünde seyreltme `core/report.py`de kalır.
6. **Erişilebilirlik:** `role="img"` + `aria-label` özeti ("Lider: X, +4.2%"), altında
   katlanır **veri tablosu** yedeği; seriler yalnızca renkle değil çizgi stiliyle de ayrılır.
7. **İpucu (tooltip):** dikey çizgi + o zamandaki TÜM görünen serilerin değeri, değere göre sıralı.
   Klavyeyle ←/→ ile gezilir.

### 6.2 Long vs Short kartı
Yan yana iki yatay çubuk (ort. R) + **bootstrap aralığı hata çubuğu olarak**. Aralıklar
ayrışmıyorsa kart bunu metinle söyler. Katman başına ayrı kart, asla toplanmaz.

### 6.3 Korelasyon ısı haritası
Diverging ölçek `--neg` ↔ nötr ↔ `--pos`; hücrede sayı yazılı (renk tek başına değil).
Matris katman başına.

### 6.4 Kırılımlar (kol / sembol / çıkış kuralı / seans / kayıp serisi)
Yatay çubuk (ort. R) + n etiketi; `n < min_trades` satırı soluk + `Ö`. Sıfır çizgisi `--axis`.

---

## 7. Etkileşim ve hareket

| Kural | Değer |
|---|---|
| Hover/focus geçişi | 150ms `ease-out`, yalnızca `color`, `background-color`, `border-color`, `opacity` |
| Panel açılışı | 200ms; kapanış daha hızlı (120ms) |
| Kaydırınca beliren animasyon | **YOK** (skill GSAP scroll-reveal önerdi, reddedildi: veri paneli, CDN yok) |
| Sayı değişimi | animasyonsuz, anında. Sayı sayma efekti ölçümü süsler |
| `prefers-reduced-motion` | tüm geçişler 0ms. *(bugün yalnızca index.html'de var; positions/backtest'te yok, bkz. Öneri 4)* |
| Yükleme | iskelet satırlar (sabit yükseklik, CLS < 0.1); yük hatası = banner, sayfanın geri kalanı çalışır |

### Odak (focus)
*(Öneri 3)* Tek bir odak token'ı: `--focus: #5ea3f0` (surface-2'de 6.05; 3:1 fazlasıyla
geçiyor), `outline: 2px solid var(--focus); outline-offset: 2px`. Bugün odak halkası
`--ink-muted` (4.4) ve bazı yerlerde `-2px` iç ofset kullanıyor; çalışıyor ama zayıf.

---

## 8. Biçimlendirme (shared.js, tek kopya)

| Tür | Biçim | Örnek |
|---|---|---|
| Para | `usd`: binlik ayraç, 2 ondalık | `10,482.17 $` |
| Yüzde | `pct`: işaretli, 2 ondalık | `+4.82%` / `−1.30%` (Unicode eksi `−`, tire değil) |
| R | 2 ondalık, işaretli, `R` son eki | `+0.31R` |
| Fiyat | `price`: sembolün kendi hassasiyeti | `0.08214` |
| Zaman | `fullTs`: UTC, `YYYY-MM-DD HH:mm` + `UTC` etiketi | seans sınırları UTC olduğu için |
| Tanımsız | `—` | `nan` asla `0` yazılmaz |
| Açık pozisyon = 0 | `0` | ölçüldü ve sıfır |

---

## 9. Erişilebilirlik kontrol listesi (teslimattan önce)

- [ ] Tüm metin ≥4.5:1, metin dışı öğeler ≥3:1 (§2 tabloları)
- [ ] 14px altı metinde `--ink-muted` yok
- [ ] Her etkileşimli öğe ≥44×44px, klavyeyle erişilebilir, görünür odak halkalı
- [ ] Kâr/zarar, yön ve kapı durumu renk DIŞINDA da okunuyor (işaret, metin, rozet yazısı)
- [ ] Grafiklerin veri tablosu yedeği ve `aria-label` özeti var
- [ ] `prefers-reduced-motion` üç sayfada da uygulanıyor
- [ ] 375 / 768 / 1024 / 1440 px'te sayfa düzeyinde yatay kaydırma yok
- [ ] Emoji ikon yok (⇅, ⚠, ▸ tipografik işaret olarak kabul; ikon gerekiyorsa inline SVG)
- [ ] Yük eksikse banner var, `—` ile `0` karışmıyor

---

## 10. Reddedilenler (skill önerdi, projeye uymuyor)

| Skill önerisi | Neden reddedildi |
|---|---|
| Desen: "Enterprise Gateway" (hero, müşteri logoları, satış CTA'sı) | Pazarlama sayfası kalıbı; skorboard bir ölçüm panelidir. Arama eşleşmesi konu dışı |
| Palet: slate `#020617` + yeşil vurgu `#22C55E` | Yeşil zaten "kapı geçti" anlamında (§2.3); mavi tonlu zemin kategorik maviyi söndürür |
| Font: Fira Code / Fira Sans (Google Fonts) | CDN/harici bağımlılık yasağı; sistem fontu + `tabular-nums` + monospace sayı yığını aynı işi görüyor |
| GSAP ScrollTrigger scroll-reveal | Harici kütüphane; veri panelinde kaydırma animasyonu okumayı geciktirir |
| "Cursor-pointer + hover" odaklı kontrol listesi | Mobil öncelikli: hover'a bağlı hiçbir bilgi olamaz, dokunma/odak eşdeğeri zorunlu |

---

## 11. Önerilen değişikliklerin özeti (uygulanmadı)

| # | Değişiklik | Dosya | Risk |
|---|---|---|---|
| 1 | `--good-ink` token'ı, `.badge.on` metni | `shared.css` | çok düşük |
| 2 | Tip ölçeği token'ları, 10–11.x px değerlerinin birleştirilmesi | `shared.css`, sayfalar | düşük (görsel) |
| 3 | Tek `--focus` token'ı, dış ofsetli odak halkası | `shared.css` | çok düşük |
| 4 | `prefers-reduced-motion` bloğunun `shared.css`'e taşınması (üç sayfa) | `shared.css` | çok düşük |
| 5 | Eğri grafiğinde hover vurgusu + seri sonu etiketleri + veri tablosu yedeği | `index.html` | orta |
| 6 | Kontrol / çıpa / kopya için sabit çizgi stili sözleşmesi | `shared.js::assignStyles` | düşük |
| 7 | `.badge.fault` (beklenmedik ret) | `shared.css`, `positions.html` | düşük |
| 8 | Fiyat/miktar kolonlarında `--font-num` | `shared.css` | düşük |

Hepsi **sunum** değişikliği. Hiçbiri ölçümü, sıralamayı, kapıları ya da yükü değiştirmiyor.
